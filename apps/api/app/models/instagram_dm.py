"""Resposta automática das DMs do Instagram (Eduardo, 16/09/2026).

"queria adicionar a ideia de responder automaticamente também mensagens
enviadas nos nossos 4 que temos rede social" — quatro marcas, um app, um
webhook, quatro tokens de Página.

O espelho invertido do `marketing_postagem.py`: lá NÓS chamamos a Meta quando
queremos; aqui a META nos chama, e do outro lado da conversa tem um terceiro.
Daí vem tudo o que muda — webhook obrigatório, token de PÁGINA (não o do
usuário do sistema direto), permissões de mensageria e app publicado.

Duas tabelas, no vocabulário de `chamados`/`chamado_mensagem`, que já é
"thread + outbox na mesma linha":

  `dm_conversas`  — uma por (conta da marca, IGSID do cliente)
  `dm_mensagens`  — entrada imutável E fila de saída na mesma linha

O `mid` UNIQUE é a idempotência do webhook NO BANCO, não só no Redis: a Meta
reentrega por horas enquanto não vir 200, e um deploy reiniciando a api já
devolve não-200. Redis sozinho perde num flush; UNIQUE sozinho gasta viagem.
Os dois, como a postagem usa índice em voo + guarda na aplicação.

ATENÇÃO ao que NÃO existe aqui: não há `container_id`. Na postagem dá pra
perguntar à Meta "será que saiu?" antes de retentar; em mensagem não existe
esse passo do meio. Por isso a regra endurece — envio ambíguo NUNCA retenta,
vai direto pra humano. Post se apaga; DM não se desvê.

`participante_id` é o IGSID (Instagram-scoped ID): identifica a pessoa PARA
ESTA CONTA e não é o @ nem aponta pra cliente nenhum do ERP. Não existe vínculo
provado entre quem manda DM e quem comprou — por isso consulta de pedido não é
respondida por aqui, é escalada.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from app.models.base import Base, TimestampMixin

# ── Estados da conversa ───────────────────────────────────────────────────
# `aberta` = chegou mensagem e ninguém tratou; `respondida` = o robô falou;
# `humano` = escalou (a pessoa pediu ATENDENTE, ou o robô não teve confiança);
# `silenciada` = ninguém responde, nem robô nem gente.
CONVERSA_ABERTA = "aberta"
CONVERSA_RESPONDIDA = "respondida"
CONVERSA_HUMANO = "humano"
CONVERSA_SILENCIADA = "silenciada"

# ── Direção da mensagem ───────────────────────────────────────────────────
# `eco` é a mensagem que a PRÓPRIA conta enviou (pela caixa de entrada, por
# outro app ou por nós) voltando pelo mesmo webhook. Guardar é útil por dois
# motivos: dá o material real de atendimento humano, e sustenta a regra "se
# alguém já respondeu, cale-se". Sem esse filtro o robô responde a si mesmo.
DIRECAO_RECEBIDA = "recebida"
DIRECAO_ENVIADA = "enviada"
DIRECAO_ECO = "eco"

# ── Estados da mensagem ───────────────────────────────────────────────────
# Entrada: `recebida` e pronto (linha imutável).
# Saída: pendente → enviando → enviada | falhou | revisar.
# `seco` é a resposta que o robô produziu com `dm_resposta_commit=False`:
# existe, tem texto, e não foi enviada — é o material da semana de avaliação.
# `revisar` é o envio AMBÍGUO (timeout, erro sem código): pode ter saído, e
# ninguém retenta em cima disso.
MSG_RECEBIDA = "recebida"
MSG_PENDENTE = "pendente"
MSG_ENVIANDO = "enviando"
MSG_ENVIADA = "enviada"
MSG_FALHOU = "falhou"
MSG_SECO = "seco"
MSG_REVISAR = "revisar"
MSG_DESCARTADA = "descartada"

# Enquanto a resposta está em um destes, ela é "em voo": não pode existir
# outra na mesma conversa. É este índice que impede responder duas vezes.
MSG_EM_VOO = (MSG_PENDENTE, MSG_ENVIANDO)


class DmConversa(Base, TimestampMixin):
    __tablename__ = "dm_conversas"
    __table_args__ = (
        UniqueConstraint(
            "rede_social_id", "participante_id", name="uq_dm_conversas_conta_participante"
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    # SET NULL (não CASCADE): apagar a conta não pode apagar o histórico da
    # conversa — por isso plataforma/conta ficam em SNAPSHOT logo abaixo,
    # mesma decisão de MarketingPostagem.
    rede_social_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("redes_sociais.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    plataforma: Mapped[str] = mapped_column(
        String(32), nullable=False, default="instagram", server_default=text("'instagram'")
    )
    conta: Mapped[str | None] = mapped_column(Text, nullable=True)
    # IGSID — escopo da conta, não é o @ público e não se liga a pedido.
    participante_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    participante_nome: Mapped[str | None] = mapped_column(Text, nullable=True)
    # O RELÓGIO DA JANELA DE 24H. A Meta só deixa responder até 24h depois da
    # última mensagem do cliente; passou disso, vai pra humano — nunca se
    # contorna com uma tag que não fomos aprovados a usar.
    ultima_recebida_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    ultima_enviada_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=CONVERSA_ABERTA, server_default=text("'aberta'")
    )
    # Desliga o robô NESTA conversa sem silenciar a pessoa: é o que a palavra
    # de escape (ATENDENTE) vira, e é exigência de política da Meta — toda
    # experiência automatizada precisa de caminho para humano.
    auto: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    # Já avisamos que é atendimento automático? Uma vez por conversa, na
    # primeira resposta — repetir em toda mensagem vira ruído.
    avisada_automacao: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    assumido_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    assumido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DmConta(Base, TimestampMixin):
    """A credencial de MENSAGEM de uma conta — separada de `redes_sociais_tokens`.

    Por que tabela própria, e não uma coluna de finalidade na tabela que já
    existe: seis lugares que publicam Reels em produção leem
    `redes_sociais_tokens` esperando UMA linha por conta (worker, roteador de
    postagens, tela de redes sociais). Acrescentar uma dimensão lá obrigaria a
    filtrar em todos eles — risco alto num caminho que já fatura, por uma
    feature cuja viabilidade ainda não está provada.

    E os dois tokens são objetos diferentes de verdade:

      publicação → token de PÁGINA, trilha Login do Facebook, não expira
      mensagem   → token de USUÁRIO do Instagram, trilha Login do Instagram,
                   ~60 dias e precisa ser renovado

    Se um dia o caminho de DM se provar, juntar as duas é fácil. O contrário
    — desfazer uma mudança que quebrou a postagem — não é.
    """

    __tablename__ = "dm_contas"
    __table_args__ = (
        UniqueConstraint("rede_social_id", name="uq_dm_contas_rede_social_id"),
        # UNIQUE também no lado do Instagram: é por `ig_user_id` que o
        # webhook descobre de quem é a mensagem. Se a mesma conta entrasse
        # em duas marcas (um clique errado entre quatro), a busca devolveria
        # a primeira linha SEM ERRO e a DM cairia na conversa da marca
        # errada — pra depois ser respondida pela conta errada.
        UniqueConstraint("ig_user_id", name="uq_dm_contas_ig_user_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    rede_social_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("redes_sociais.id", ondelete="CASCADE"),
        nullable=False,
    )
    # O id da conta profissional, formato 17841... É ele que o webhook manda e
    # que vai no caminho de /{ig_user_id}/messages — NÃO é o id com escopo do
    # app nem o @.
    ig_user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    # `cipher.encrypt_json({access_token, expires_at, scopes})`, mesmo formato
    # de RedeSocialToken. Não sai por endpoint nenhum.
    token_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # EM CLARO só pra o cron achar o que vence sem decifrar nada. Token desta
    # trilha dura ~60 dias: sem renovação, o robô emudece sem avisar.
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ok", server_default=text("'ok'")
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DmMensagem(Base, TimestampMixin):
    __tablename__ = "dm_mensagens"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    conversa_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("dm_conversas.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # UNIQUE e nullable: entrada sempre tem `mid` (é a chave de reentrega da
    # Meta); saída só ganha um depois de enviada, e o Postgres deixa vários
    # NULL conviverem no índice único.
    mid: Mapped[str | None] = mapped_column(String(191), nullable=True, unique=True)
    direcao: Mapped[str] = mapped_column(String(16), nullable=False)
    # texto | story_reply | reacao | anexo | postback | apagada
    tipo: Mapped[str] = mapped_column(
        String(16), nullable=False, default="texto", server_default=text("'texto'")
    )
    texto: Mapped[str | None] = mapped_column(Text, nullable=True)
    # URL da CDN da Meta, que EXPIRA. Guardamos o endereço e o tipo, nunca o
    # arquivo: baixar mídia de DM é escopo perdido em v1 — e nunca se busca
    # link que veio na mensagem (é texto de estranho).
    anexo_tipo: Mapped[str | None] = mapped_column(String(32), nullable=True)
    anexo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Evento cru. É o material da semana de modo seco: sem ele não dá pra
    # avaliar o que o robô teria respondido nem por quê.
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # Timestamp da META (UTC), não o nosso: é ele que conta a janela de 24h.
    ocorrido_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # O cliente apagou a mensagem. A Meta exige que a gravação de tela do App
    # Review mostre o tratamento de "unsent" — e o histórico tem que refletir,
    # não fingir que a mensagem continua lá.
    apagada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=MSG_RECEBIDA, server_default=text("'recebida'")
    )
    # Qual resposta da biblioteca produziu este texto (saída FECHADA: o modelo
    # escolhe um id, nunca escreve). NULL numa linha enviada = texto humano.
    resposta_modelo_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    # Por que escalou, por que não respondeu, qual erro deu. Texto de operação,
    # nunca conteúdo da DM — dado pessoal de terceiro não entra em log.
    motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    tentativas_max: Mapped[int] = mapped_column(
        Integer, nullable=False, default=2, server_default=text("2")
    )
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enviada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# UMA resposta em voo por conversa — declarado no model (create_all dos testes)
# E na migration 0288, como manda o precedente de models/marca.py.
# É este índice, sozinho, que impede o robô de responder duas vezes à mesma
# pessoa: dois workers podem tentar, só um grava.
Index(
    "uq_dm_resposta_em_voo",
    DmMensagem.conversa_id,
    unique=True,
    postgresql_where=text("status IN ('pendente', 'enviando')"),
)
