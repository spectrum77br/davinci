"""Postagem automática dos criativos nas redes sociais (Eduardo, 15/09/2026).

"um robô que fará a postagem desses vídeos do criativo automaticamente; também
teremos a opção de agendar uma data para a postagem automática" — liga
Marketing › Criativos (o vídeo aprovado) a Cadastros › Redes Sociais (a conta
da marca em cada plataforma).

Quem publica é o SERVIDOR, pela API oficial da Meta — não o executor do Mac.
O `apps/executor` existe porque a API de Ads da Shopee é bloqueada e só dá pra
operar por navegador; aqui não: a Graph API é aberta, o vídeo já está no disco
do servidor (`settings.uploads_dir`) e um Mac desligado às 19h perderia o post
agendado. Automatizar navegador pra postar ainda violaria os termos das
plataformas, com a conta da marca como aposta.

`marketing_postagens` é agenda E outbox na mesma linha (mesmo ciclo do
LogisticaRoboComando): agendado → pendente → containering/publicando →
publicado | falhou | cancelado | revisar. Publicar NÃO é idempotente, então:
índice único parcial enquanto a postagem está "em voo", `container_id`
gravado ANTES de publicar e reconciliação pelo id externo antes de qualquer
retry.

`redes_sociais_tokens` guarda o token da conta cifrado — o models/marca.py já
tinha decidido: "tokens/ids externos entram em colunas próprias desta tabela
(ou tabela filha por rede_social_id), cifrados com app.security.cipher —
NUNCA em `integrations`". O token não sai por API nenhuma.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
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

# Estados da postagem. `agendado` espera a hora; `pendente` é a fila do
# publicador; `containering` é o passo do meio do Instagram (a Meta processa o
# vídeo de forma assíncrona); `revisar` é o agendamento que venceu demais
# (worker parado) e NÃO sai sozinho — alguém decide.
STATUS_AGENDADO = "agendado"
STATUS_PENDENTE = "pendente"
STATUS_CONTAINERING = "containering"
STATUS_PUBLICANDO = "publicando"
STATUS_PUBLICADO = "publicado"
STATUS_FALHOU = "falhou"
STATUS_CANCELADO = "cancelado"
STATUS_REVISAR = "revisar"

# Enquanto a postagem está em um destes, ela é "em voo": não pode existir
# outra pro mesmo (arquivo, conta) — é o que impede post duplicado.
STATUS_EM_VOO = (STATUS_AGENDADO, STATUS_PENDENTE, STATUS_CONTAINERING, STATUS_PUBLICANDO)


class MarketingPostagem(Base, TimestampMixin):
    __tablename__ = "marketing_postagens"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    creative_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketing_creatives.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # A postagem aponta pra UM arquivo (a linha do criativo aceita vários).
    file_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketing_creative_files.id", ondelete="CASCADE"),
        nullable=False,
    )
    # SET NULL (não CASCADE): apagar a conta/marca não pode apagar o histórico
    # do que já foi publicado — por isso plataforma/conta também ficam em
    # SNAPSHOT aqui embaixo (mesma ideia de MarketingCreative.pushed_dest).
    rede_social_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("redes_sociais.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    plataforma: Mapped[str] = mapped_column(String(32), nullable=False)
    conta: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Legenda do post — campo PRÓPRIO. O `roteiro` do criativo é briefing de
    # produção ("cena, fala, texto na tela"): entra como rascunho no modal,
    # mas publicar o roteiro cru poria instrução de gravação no Instagram.
    legenda: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Qual variação da biblioteca produziu a `legenda` acima (migration 0282).
    # É ESTADO DO RODÍZIO, não fonte do texto: a escolha é "a variação que faz
    # mais tempo que não sai nesta conta", e ela se lê daqui, sem tabela de
    # contador. A legenda que vai pro Instagram é a de cima, snapshot —
    # reescrever o modelo depois não muda post nenhum. SET NULL porque apagar
    # a variação não pode apagar o histórico do que já foi publicado.
    legenda_modelo_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        # Nome curto na mão: a convenção do repo geraria 66 caracteres e o
        # Postgres trunca em 63 calado — aí o nome do model e o da migration
        # deixariam de bater (mesmo motivo de fk_product_categories_parent_bling_cat_id).
        ForeignKey(
            "marketing_legenda_modelos.id",
            ondelete="SET NULL",
            name="fk_marketing_postagens_legenda_modelo_id_legenda_modelos",
        ),
        nullable=True,
    )
    # share_to_feed, thumb_offset, título do Short… (espelha MarketingCommand.payload)
    opcoes: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # UTC no banco, sempre; a borda converte pra BRT (como scheduling.py).
    # NULL = publicar no próximo tick ("publicar agora").
    agendado_para: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=STATUS_AGENDADO, server_default=text("'agendado'")
    )
    # "manual" (clique) | "agenda" (cron promoveu) — espelha MarketingCommand.source.
    origem: Mapped[str] = mapped_column(
        String(16), nullable=False, default="manual", server_default=text("'manual'")
    )
    # Canal de execução: 'api' (Graph API, no servidor). Fica aqui pra não
    # fechar a porta do 'browser' (executor local) se algum dia precisar.
    executor: Mapped[str] = mapped_column(
        String(16), nullable=False, default="api", server_default=text("'api'")
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    tentativas_max: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default=text("3")
    )
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Gravado ANTES de publicar: é o que permite reconciliar ("será que saiu?")
    # em vez de retentar cego — publicar duas vezes não tem desfazer.
    container_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    post_external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    post_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    publicado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Fora do Desempenho (migration 0317, Eduardo, 24/09/2026: "tirar aqueles
    # 2 tiktoks da outra conta da uranyx"). Preenchido = o post não entra em
    # soma, média nem comparação, e deixa de ser lido. É uma MARCA, não um
    # DELETE: o histórico fica, e "Voltar a contar" limpa as três colunas.
    #
    # A migration marcou os posts antigos pelo @ do LINK do TikTok, nunca pelo
    # `conta` daqui: `conta` é snapshot do nome da conta na hora de publicar, e
    # a conta do YouTube da Uranyx foi renomeada — o post dela ainda diz
    # `uranyx_brasil` e é da marca. O link do TikTok carrega o dono de verdade.
    fora_do_desempenho_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fora_do_desempenho_motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    fora_do_desempenho_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


class RedeSocialToken(Base, TimestampMixin):
    """Credencial de publicação de UMA conta de rede social.

    `token_enc` é `cipher.encrypt_json({access_token, refresh_token,
    expires_at, scopes})` em BYTEA — mesmo formato de integrations.credentials
    (vários campos, o refresh reescreve o conjunto). `token_expires_at` fica
    EM CLARO só pra o cron achar o que vence sem decifrar nada, como os
    *_token_refresh já fazem. O token não é devolvido por endpoint nenhum.
    """

    __tablename__ = "redes_sociais_tokens"
    __table_args__ = (
        UniqueConstraint("rede_social_id", name="uq_redes_sociais_tokens_rede_social_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    rede_social_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("redes_sociais.id", ondelete="CASCADE"),
        nullable=False,
    )
    # ig_user_id (Instagram), page_id (Facebook), open_id (TikTok), channel_id (YouTube).
    external_user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # DE ONDE veio o token — e, por consequência, PARA ONDE se publica:
    #   "facebook"  = trilha "Instagram API with Facebook Login" (a conta tem
    #                 Página vinculada): tudo em graph.facebook.com;
    #   "instagram" = trilha "Instagram API with Instagram Login" (conta sem
    #                 Página): tudo em graph.instagram.com.
    # É o TOKEN que decide o host, nunca a plataforma da linha: um token da
    # trilha Instagram Login mandado pro graph.facebook.com volta "sem
    # permissão" — erro que parece conta errada e não é.
    provedor: Mapped[str] = mapped_column(
        String(16), nullable=False, default="facebook", server_default=text("'facebook'")
    )
    external_username: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # ok | expirado | revogado
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ok", server_default=text("'ok'")
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    connected_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


# Uma postagem EM VOO por (arquivo, conta) — declarado no model (create_all dos
# testes) E na migration 0279, como manda o precedente de models/marca.py.
Index(
    "uq_marketing_postagem_em_voo",
    MarketingPostagem.file_id,
    MarketingPostagem.rede_social_id,
    unique=True,
    postgresql_where=text(
        "status IN ('agendado', 'pendente', 'containering', 'publicando')"
    ),
)


class MarketingPostagemMetrica(Base, TimestampMixin):
    """Um RETRATO por dia dos números de UMA publicação (migration 0315).

    Pedido do Eduardo (23/09/2026): ver as views e interações por marca, com o
    total somado e o detalhe de cada rede. A linha é por VÍDEO publicado pelo
    DaVinci, nunca o número geral do canal — é o que ele pediu explicitamente,
    e é o que a tabela-mãe permite: `marketing_postagens` só contém o que saiu
    por aqui.

    RETRATO DATADO, não linha sobrescrita. Todas as três plataformas devolvem
    número ACUMULADO ("este vídeo tem 400 views"), nunca o do dia. Guardando um
    UPDATE por cima, "quanto rendeu esta semana" fica impossível de responder —
    e essa é metade do valor da tela. Com um retrato por dia, a diferença entre
    dois dias é uma subtração.

    `plataforma` e `conta` repetem o que está na postagem de propósito, como a
    própria postagem já repete da rede social: a conta pode ser apagada do
    cadastro, e o histórico do que rendeu não pode sumir junto.

    O que cada rede entrega HOJE (conferido em 23/09/2026):
      youtube   — views, curtidas, comentários (Data API v3, escopo que os
                  canais já têm; NÃO precisa reautorizar);
      instagram — curtidas e comentários saem com o token atual; views, alcance,
                  compartilhamentos e salvamentos exigem `instagram_manage_
                  insights`, que o token ainda não carrega (provado por
                  sondagem: erro 10 "Application does not have permission");
      tiktok    — tudo pela página pública, que traz os números num JSON no
                  HTML. Sem API, sem login, e do IP do servidor.

    Por isso todo campo é NULÁVEL: nulo quer dizer "esta rede não me deu este
    número", que é diferente de zero. Somar tratando nulo como zero é como a
    tela mente sem ninguém perceber.
    """

    __tablename__ = "marketing_postagem_metricas"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    postagem_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketing_postagens.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Dia da coleta em BRT (a tela é do Eduardo, o fuso é o dele). Um retrato
    # por dia por postagem — o índice único embaixo garante.
    dia: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Snapshot: sobrevive à conta sair do cadastro.
    plataforma: Mapped[str] = mapped_column(String(32), nullable=False)
    conta: Mapped[str | None] = mapped_column(Text, nullable=True)
    marca_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marcas.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Os números. NULO = a rede não deu; ZERO = deu e é zero.
    views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    curtidas: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comentarios: Mapped[int | None] = mapped_column(Integer, nullable=True)
    compartilhamentos: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salvamentos: Mapped[int | None] = mapped_column(Integer, nullable=True)
    alcance: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # O que a rede respondeu, cru, pra quando um número parecer errado depois.
    bruto: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Preenchido quando a coleta falhou. Coleta falha BAIXO — a tela mostraria
    # número velho sem ninguém notar —, então o erro fica na linha e a tela
    # mostra "coletado em" por plataforma.
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Quando saiu a leitura BOA que deu os números desta linha (migration
    # 0317). NULO = a linha só tem erro. Existe porque `updated_at` não servia:
    # o upsert (`on_conflict_do_update`) não aplica o `onupdate` do ORM, e ele
    # ficava congelado no primeiro insert do dia. Agora `updated_at` é gravado
    # à mão e quer dizer "última TENTATIVA"; `lido_em`, "última leitura que
    # deu certo" — uma falha à tarde não apaga a leitura boa da manhã.
    lido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# Um retrato por dia por postagem. Recoletar no mesmo dia ATUALIZA a linha (o
# número acumulado mudou); no dia seguinte nasce outra, e a diferença entre as
# duas é o que rendeu no dia.
UniqueConstraint(
    MarketingPostagemMetrica.postagem_id,
    MarketingPostagemMetrica.dia,
    name="uq_metrica_postagem_dia",
)
Index(
    "ix_metrica_marca_dia",
    MarketingPostagemMetrica.marca_id,
    MarketingPostagemMetrica.dia,
)
