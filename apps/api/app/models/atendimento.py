"""Atendimento unificado: a caixa de conversas de todas as lojas (25/09/2026).

Eduardo quer "o Duoke nosso": Shopee, Mercado Livre, TikTok e Amazon numa
fila só, com a IA deixando a resposta pronta e a pessoa conferindo. Plano em
`docs/atendimento-unificado.md`.

Por que tabelas PRÓPRIAS e não `dm_conversas`/`dm_mensagens`: o robô do
Instagram varre aquelas tabelas e responde sozinho. Conversa de marketplace
lá dentro seria respondida por um robô que não conhece pedido, prazo nem as
regras de cada loja. O Instagram aparece na tela por um adaptador SÓ LEITURA
sobre as tabelas dele (`services/atendimento/instagram.py`).

Dez tabelas:

  `atendimento_canais`     — uma por (integração × canal). Guarda o MODO da
                             loja (observar/humano/copiloto/auto), a saúde da
                             leitura e o cursor da consulta periódica.
  `atendimento_conversas`  — uma por conversa na plataforma. Os campos
                             `ultima_*`, `aguardando_resposta` e
                             `prazo_resposta_em` são DERIVADOS das mensagens
                             (`services/atendimento/gravar.recalcular`) para a
                             lista ordenar e filtrar sem varrer mensagem.
  `atendimento_mensagens`  — entrada imutável E saída na mesma linha, no
                             vocabulário de `dm_mensagens`/`chamado_mensagem`.
  `atendimento_rascunhos`  — o que a IA sugeriu, com o porquê e o custo.
  `atendimento_avaliacoes` — o que a pessoa FEZ com a sugestão (enviou igual,
                             editou, descartou, escreveu do zero). É o
                             material do aprendizado: resposta aprovada por
                             pessoa vira exemplo.
  `atendimento_regras`     — o manual "QUANDO → FAÇA" da IA. Só muda por
                             pessoa; a IA nunca escreve as próprias regras.
  `atendimento_modelos`    — respostas prontas (macros) da tela.

Parte 2 (28/09/2026):

  `atendimento_categorias`       — a lista oficial de ASSUNTOS (o manual base
                                   importado), com a descrição que a IA usa
                                   para classificar. Vazia = vale a lista de
                                   `constantes.CATEGORIAS`.
  `atendimento_pedidos_comprador` — índice (loja × pedido → comprador) para o
                                   cartão "Cliente": a Shopee não filtra
                                   pedido por comprador, então o DaVinci
                                   anota cada pedido que já vê passar.
  `atendimento_avaliacoes_loja`  — as avaliações que o comprador deixou na
                                   loja (Shopee `get_comment`), casadas com ele
                                   pelo pedido e pelo usuário. Desde a 0358
                                   (02/10/2026) também a opinião do produto
                                   do ML, a mídia, a resposta e a PENDÊNCIA
                                   (avaliações no atendimento, RF8).

Duas travas vivem NO BANCO, não na aplicação, pelo mesmo motivo do
`uq_dm_resposta_em_voo`: dois workers (ou duas abas) podem tentar, só um grava.

  `uq_atendimento_envio_em_voo`      — uma resposta `enviando` por conversa.
  `uq_atendimento_rascunho_pendente` — um rascunho `pendente` por conversa.

`autor` × `origem`: autor é QUEM escreveu na plataforma (cliente, loja,
sistema, mediador; `equipe` na nota interna); origem é POR ONDE saiu
(pelo DaVinci por pessoa, pela IA, ou fora do DaVinci — Duoke, Seller
Center, celular). É a origem `externo` que diz
"alguém já respondeu por fora, cale-se", e é ela que aposenta o rascunho.

Etiqueta = status atual (01/10/2026, migration 0353): a conversa ganha
`etiqueta` (+ desde, manual, secundárias, automática) e duas tabelas —

  `atendimento_etiquetas_historico` — uma linha por mudança de etiqueta (a
                                      linha do tempo), sistema ou pessoa.
  `atendimento_reclamacoes`         — reclamação/mediação/devolução da
                                      PLATAFORMA (ML, Shopee, TikTok), só
                                      leitura, ligada à conversa do pedido.

Carrinho e redes (02/10/2026, migration 0362): o canal ganha a origem
EXTERNA (`externo_ref`, `rede_social_id`) e entram três tabelas —

  `atendimento_carrinhos`   — o carrinho abandonado do lojista nos sites
                              (Charlots, Uranyx), por episódio, até o desfecho
                              (recuperado / não recuperado / resolvido).
  `atendimento_publicacoes` — a mídia do Instagram/Facebook (própria ou a que
                              marcou a marca) por trás das conversas de
                              comentário: o cartão "Detalhes da publicação".
  `atendimento_comentarios` — cada comentário lido, ligado à conversa da
                              pessoa naquela publicação.

Os valores válidos das colunas de estado estão em
`services/atendimento/constantes.py` — um lugar só, lido pelo model, pelo
sync, pela IA e pela tela.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from app.models.base import Base, TimestampMixin


class AtendimentoCanal(Base, TimestampMixin):
    """Uma caixa de entrada: (integração × canal).

    O ML tem DOIS canais por conta (pergunta pré-venda e mensagem pós-venda),
    com prazo, limite de caracteres e API diferentes — por isso o canal é
    linha própria e o modo é por canal, não por loja.

    Temu e AliExpress (30/09/2026) não têm API de chat: a loja é lida pelo
    robô do Mac mini (um perfil do AdsPower por loja) e o canal é
    (`robo_perfil_id` × canal), SEM integração. O cron do sync nunca o vê
    (junta com `integrations`); quem o escreve é `services/atendimento/robo.py`.

    Sites e redes (02/10/2026, migration 0362) também não têm integração nem
    robô: o canal EXTERNO é identificado por `externo_ref` ("site:charlots",
    "rede:instagram:<ig_user_id>", "rede:facebook:<page_id>") — ver
    `services/atendimento/canais_externos.py`. O cron do sync também nunca o
    vê; quem o escreve é o leitor de cada um (`carrinhos.py`, `redes.py`).
    """

    __tablename__ = "atendimento_canais"
    __table_args__ = (
        UniqueConstraint("integration_id", "canal"),
        # Um canal por perfil do AdsPower (loja lida pelo robô do Mac mini).
        UniqueConstraint("robo_perfil_id"),
        # Um canal por (origem externa, caixa): o site pode ganhar outra caixa
        # (o SAC) com a mesma referência.
        UniqueConstraint("externo_ref", "canal"),
        # Todo canal tem por onde ler: a integração (API da loja), o robô OU
        # a origem externa (site, rede social).
        CheckConstraint(
            "integration_id IS NOT NULL OR robo_perfil_id IS NOT NULL"
            " OR externo_ref IS NOT NULL",
            name="tem_origem",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    # CASCADE: canal sem integração não tem como ler nem enviar. A conversa,
    # essa sim, sobrevive (SET NULL lá embaixo) — histórico não se apaga.
    # NULL só nas lojas do ROBÔ (Temu/AliExpress, migration 0347): elas não
    # têm integração, e criar uma poria a loja na mira de todo worker que
    # percorre `integrations` (factory → TemuClient sem credencial).
    integration_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("integrations.id", ondelete="CASCADE"),
        nullable=True,
    )
    # O perfil do AdsPower (`user_id` da API local dele) que o robô mantém
    # aberto no chat da loja. É a identidade da loja do robô: o nome da loja e
    # o último sinal ficam em `cursor["robo"]` (services/atendimento/robo.py).
    robo_perfil_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # A origem EXTERNA (0362): "site:<site>" | "rede:<plataforma>:<id da conta
    # na rede>". O nome que a tela mostra fica em `cursor["externo"]["nome"]`.
    externo_ref: Mapped[str | None] = mapped_column(String(191), nullable=True)
    # A conta do cadastro Redes Sociais por trás do canal de rede (0362): é
    # por ela que a barra de lojas junta o Direct (`dm_conversas.
    # rede_social_id`) e os comentários da MESMA conta numa linha só. SET
    # NULL: apagar a conta do cadastro não apaga a caixa nem o histórico.
    rede_social_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("redes_sociais.id", ondelete="SET NULL"),
        nullable=True,
    )
    plataforma: Mapped[str] = mapped_column(String(16), nullable=False)
    canal: Mapped[str] = mapped_column(String(16), nullable=False)
    # Toda loja nasce em `observar` (só lê). Enquanto o Duoke estiver ligado,
    # é ele quem responde; o DaVinci assume loja por loja, por decisão de
    # pessoa na tela — nunca por deploy.
    modo: Mapped[str] = mapped_column(
        String(16), nullable=False, default="observar", server_default=text("'observar'")
    )
    # novo | ok | sem_escopo | erro | desligado. `sem_escopo` é o 401/403 de
    # permissão (TikTok sem `seller.customer_service`, ML Poofy): não adianta
    # insistir a cada 2 minutos.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="novo", server_default=text("'novo'")
    )
    # Onde a consulta parou (formato de cada plataforma). Só o adaptador lê.
    cursor: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # O número que a PLATAFORMA diz ter de não lidas — o termômetro de que a
    # leitura está completa, comparado com o que temos aqui.
    nao_lidas_plataforma: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ultimo_ok_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ultimo_erro_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Mensagem de operação (código/HTTP), nunca texto de comprador.
    ultimo_erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Categorias liberadas para envio automático NESTE canal (modo `auto`).
    # Vazia = nada sai sozinho, mesmo com o modo ligado.
    auto_categorias: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )


class AtendimentoConversa(Base, TimestampMixin):
    __tablename__ = "atendimento_conversas"
    __table_args__ = (UniqueConstraint("integration_id", "canal", "externo_id"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    # SET NULL (não CASCADE) nos dois: desligar uma loja não apaga o que o
    # cliente escreveu — por isso plataforma/canal/conta ficam em SNAPSHOT.
    canal_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("atendimento_canais.id", ondelete="SET NULL"),
        nullable=True,
    )
    integration_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("integrations.id", ondelete="SET NULL"),
        nullable=True,
    )
    plataforma: Mapped[str] = mapped_column(String(16), nullable=False)
    canal: Mapped[str] = mapped_column(String(16), nullable=False)
    # Nome da integração no momento em que a conversa entrou.
    conta: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Id da conversa NA PLATAFORMA: conversation_id da Shopee, pack/pergunta
    # do ML, thread do e-mail da Amazon. Mesmo tamanho do `mid` de
    # `dm_mensagens`: cabe o Message-ID de e-mail, o maior dos quatro.
    externo_id: Mapped[str] = mapped_column(String(191), nullable=False)
    comprador_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    comprador_nome: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Foto do comprador: SÓ a URL que a própria API de chat entrega (Shopee
    # `to_avatar`, participante da TikTok). ML e Amazon não têm — a tela
    # mostra as iniciais. Endereço da imagem, nunca o arquivo (28/09/2026,
    # "a pessoa que enviou a mensagem" igual ao Duoke).
    comprador_avatar: Mapped[str | None] = mapped_column(Text, nullable=True)
    # O elo com o resto do DaVinci (Bling, logística, chamados, devoluções).
    pedido_marketplace: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    anuncio_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    anuncio_titulo: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ── Derivados das mensagens (gravar.recalcular) ─────────────────────
    ultima_mensagem_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # Uma linha, até 160 caracteres: é o que a lista mostra.
    ultima_mensagem_resumo: Mapped[str | None] = mapped_column(Text, nullable=True)
    ultima_autor: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ultima_do_cliente_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ultima_da_loja_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # O cliente falou por último e ninguém respondeu (nem por fora).
    aguardando_resposta: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"), index=True
    )
    # Relógio do SLA de cada plataforma (ML pergunta 1 h, Amazon 24 h...).
    # É a coluna do filtro "vencendo" e do alerta no Telegram.
    prazo_resposta_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # Não lidas segundo a PLATAFORMA (o DaVinci nunca marca como lido).
    nao_lidas: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    # aberta | respondida | fechada | bloqueada
    situacao: Mapped[str] = mapped_column(
        String(16), nullable=False, default="aberta", server_default=text("'aberta'")
    )
    # Por que a plataforma não deixa responder (ML pós-venda bloqueada,
    # mediação aberta...). Texto de operação, sem dado pessoal.
    bloqueio_motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Janela de envio da plataforma, quando ela existe.
    pode_enviar_ate: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # "Não é necessária resposta" (o "obrigado" da Amazon): tira da fila sem
    # fingir que alguém respondeu.
    sem_resposta_necessaria: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    atribuido_a: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Desliga a IA NESTA conversa (a pessoa assumiu, ou o assunto é delicado).
    ia_pausada: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # Sobras de cada plataforma que a tela ou o adaptador querem guardar.
    dados: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # ── Etiqueta = status atual (RF1, migration 0353) ────────────────────
    # Quem grava é `services/atendimento/etiqueta` (recalcular_etiqueta e a
    # troca à mão) — NUNCA direto: a função aplica a prioridade e escreve o
    # histórico. Valores em `constantes.ETIQUETAS`. NULL = ainda não calculada.
    etiqueta: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    etiqueta_desde: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Um atendente trocou à mão. Vale até o PRÓXIMO acontecimento automático:
    # quando a etiqueta que o motor calcula (`etiqueta_automatica`) mudar.
    etiqueta_manual: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # As outras etiquetas abertas ao mesmo tempo, da mais urgente para a
    # menos (o indicador pequeno da lista). Nunca traz a de base
    # (pré/pós-venda) nem a própria `etiqueta`.
    etiquetas_secundarias: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # O que o MOTOR calculou da última vez, valendo ou não. Com a troca à
    # mão ligada, é por ela que se sabe se "aconteceu algo" desde a troca.
    etiqueta_automatica: Mapped[str | None] = mapped_column(String(24), nullable=True)


class AtendimentoEtiquetaHistorico(Base):
    """Uma mudança de etiqueta da conversa — o que a linha do tempo mostra.

    "Etiqueta mudou de Pós-venda para Reclamação". Escrita só por
    `services/atendimento/etiqueta`. `por_user_id` NULL = sistema (o motor);
    preenchido = troca à mão. A primeira classificação da conversa (de NULL
    para alguma) não vira linha: não é mudança.
    """

    __tablename__ = "atendimento_etiquetas_historico"
    __table_args__ = (
        # A linha do tempo de UMA conversa, em ordem.
        Index("ix_atendimento_etiquetas_historico_conversa_id_em", "conversa_id", "em"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    # Nome à mão: pela convenção passaria dos 63 caracteres do Postgres.
    conversa_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "atendimento_conversas.id",
            ondelete="CASCADE",
            name="fk_atendimento_etiquetas_historico_conversa",
        ),
        nullable=False,
    )
    de: Mapped[str | None] = mapped_column(String(24), nullable=True)
    para: Mapped[str] = mapped_column(String(24), nullable=False)
    # O acontecimento ("Reclamação 5582543195 aberta no ML", "Trocada à mão").
    # Texto de operação, sem dado pessoal.
    motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    por_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AtendimentoReclamacao(Base, TimestampMixin):
    """Reclamação, mediação ou devolução da PLATAFORMA (ML, Shopee, TikTok).

    O espelho do que a plataforma diz (RF2): lido por
    `services/atendimento/reclamacoes.py` (ML, claims) e
    `reclamacoes_devolucoes.py` (devoluções/disputas de Shopee e TikTok que a
    Logística já lê). SÓ LEITURA na plataforma: nenhuma ação sai daqui.

    ABERTA = `encerrada_em IS NULL`. O `status` é o da plataforma, cru — cada
    uma tem o seu vocabulário —, e é o que a tela mostra. A etiqueta da
    conversa (Reclamação/Devolução) sai daqui por `etiqueta_fatos`, casando
    por `conversa_id` OU por (plataforma, `pedido_marketplace`).
    """

    __tablename__ = "atendimento_reclamacoes"
    __table_args__ = (
        # Idempotência da leitura: a mesma reclamação volta em toda rodada.
        UniqueConstraint("plataforma", "externo_id"),
        # "Abertas, por prazo" — a fila da reclamação vencendo.
        Index("ix_atendimento_reclamacoes_status_prazo_em", "status", "prazo_em"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    # A conta da loja. SET NULL: desligar a loja não apaga a reclamação.
    integration_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("integrations.id", ondelete="SET NULL"),
        nullable=True,
    )
    # A conversa a que ela foi ligada (a do pedido, ou a conversa
    # `canal = 'reclamacao'` criada para ela). SET NULL: a conversa some, a
    # reclamação fica.
    conversa_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("atendimento_conversas.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    plataforma: Mapped[str] = mapped_column(String(16), nullable=False)
    # Id NA PLATAFORMA (claim_id do ML, return_sn da Shopee, return_id do TikTok).
    externo_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # reclamacao | mediacao | devolucao (constantes.TIPOS_RECLAMACAO)
    tipo: Mapped[str] = mapped_column(String(16), nullable=False)
    # Status da plataforma, cru (ML `opened`/`closed`, Shopee `REQUESTED`,
    # TikTok `RETURN_OR_REFUND_REQUEST_PENDING`...).
    status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Motivo da plataforma (código/descrição do motivo), nunca texto do comprador.
    motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    # O elo com a conversa e o resto do DaVinci (mesmo tamanho da conversa).
    pedido_marketplace: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    # Até quando a LOJA tem de agir (o prazo que a plataforma dá). Relógio da
    # plataforma.
    prazo_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    aberta_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    encerrada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # O resto do que a plataforma devolve (etapa, resolução, devolução física,
    # quem precisa agir) — material da tela e de depuração.
    dados: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class AtendimentoMensagem(Base, TimestampMixin):
    __tablename__ = "atendimento_mensagens"
    __table_args__ = (
        # Idempotência do sync NO BANCO: a mesma mensagem volta em toda
        # rodada de consulta. Nullable de propósito — a nossa resposta só
        # ganha id da plataforma depois de enviada, e o Postgres deixa vários
        # NULL conviverem no índice único.
        UniqueConstraint("conversa_id", "externo_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    conversa_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("atendimento_conversas.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    externo_id: Mapped[str | None] = mapped_column(String(191), nullable=True)
    # cliente | loja | sistema | mediador | equipe — QUEM escreveu.
    # `mediador` (0353): a plataforma falando na reclamação (o "Com Meli").
    # `equipe` (0353): a NOTA INTERNA, escrita no DaVinci (constantes.AUTOR_EQUIPE).
    autor: Mapped[str] = mapped_column(String(16), nullable=False)
    # cliente | davinci_humano | davinci_ia | externo | sistema | davinci_nota
    # — POR ONDE saiu. `davinci_nota` (0353) = NOTA INTERNA: nunca sai.
    origem: Mapped[str] = mapped_column(String(24), nullable=False)
    # Quem da equipe apertou "Enviar" (origem davinci_humano).
    autor_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # texto | imagem | video | produto | pedido | arquivo | outro | nota
    # (`nota` = nota interna, com a origem `davinci_nota`; constantes.TIPO_NOTA)
    tipo: Mapped[str] = mapped_column(
        String(16), nullable=False, default="texto", server_default=text("'texto'")
    )
    texto: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Endereço e tipo do anexo, nunca o arquivo: URL de CDN de marketplace
    # expira e baixar mídia de comprador é escopo perdido em v1.
    anexos: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # Relógio da PLATAFORMA, não o nosso: é ele que conta o SLA.
    enviada_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # recebida | enviando | enviada | falhou | revisar. `revisar` é o envio
    # AMBÍGUO (timeout, erro sem código): pode ter saído, e ninguém retenta
    # em cima disso — mensagem não se desenvia.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="recebida", server_default=text("'recebida'")
    )
    # Erro de operação (código da plataforma), nunca o texto do comprador.
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    # De qual rascunho saiu esta resposta. SEM FK de propósito: o rascunho já
    # aponta para a mensagem (gatilho), e FK nos dois sentidos vira ciclo no
    # DELETE e no create_all.
    rascunho_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    # Item cru da plataforma — material de depuração do adaptador.
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class AtendimentoRascunho(Base, TimestampMixin):
    """A sugestão da IA para uma conversa.

    Fica salva mesmo quando precisa de humano ou quando o validador barrou:
    é o que a pessoa vê como ponto de partida, e é o que se mede depois.
    """

    __tablename__ = "atendimento_rascunhos"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    conversa_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("atendimento_conversas.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # A mensagem do cliente que motivou a sugestão. Nome curto e explícito:
    # o da convenção passaria dos 63 caracteres do Postgres.
    mensagem_gatilho_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "atendimento_mensagens.id",
            ondelete="SET NULL",
            name="fk_atendimento_rascunhos_gatilho_atendimento_mensagens",
        ),
        nullable=True,
    )
    texto: Mapped[str | None] = mapped_column(Text, nullable=True)
    categoria: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confianca: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Nasce True: na dúvida, pessoa confere.
    precisa_humano: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    validador_ok: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    validador_erros: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # Os fatos que o CÓDIGO deu ao modelo (pedido, rastreio, prazo) — sem
    # dado pessoal. Sem eles não dá para auditar de onde saiu uma frase.
    fatos: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    modelo: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_versao: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Hash do manual (regras ativas) usado: resposta ruim se rastreia até a
    # versão do manual que a produziu.
    manual_hash: Mapped[str | None] = mapped_column(String(16), nullable=True)
    tokens_entrada: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_saida: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # pendente | enviado | editado | descartado | substituido | bloqueado
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pendente", server_default=text("'pendente'")
    )


class AtendimentoAvaliacao(Base, TimestampMixin):
    """O que a pessoa fez com o rascunho — UMA por rascunho.

    `enviou_igual`/`editou` com `texto_final` viram exemplo para a IA; o
    `motivo` do descarte e a `correcao` são o material da revisão semanal do
    manual (que só muda por pessoa).
    """

    __tablename__ = "atendimento_avaliacoes"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    rascunho_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("atendimento_rascunhos.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    # enviou_igual | editou | descartou | escreveu_do_zero
    acao: Mapped[str] = mapped_column(String(24), nullable=False)
    texto_final: Mapped[str | None] = mapped_column(Text, nullable=True)
    # difflib entre o rascunho e o que saiu (0–1).
    similaridade: Mapped[float | None] = mapped_column(Float, nullable=True)
    motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 'ok' | 'erro' — a nota que a pessoa dá depois.
    nota: Mapped[str | None] = mapped_column(String(8), nullable=True)
    correcao: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


class AtendimentoRegra(Base, TimestampMixin):
    """Uma linha do manual da IA: QUANDO acontecer isto → FAÇA aquilo.

    Parte 2 (P7, "manual sem regra batendo com regra"): cada regra tem um
    TIPO, que diz onde ela entra no prompt —

      `seguranca` — vale para TODA mensagem e vem primeiro (o que nunca se
                    diz, o que sempre vai para pessoa);
      `categoria` — só entra quando a mensagem foi classificada no assunto da
                    regra (`categoria` NULL = geral, entra sempre);
      `estilo`    — tom e assinatura, por último.

    Duas regras ATIVAS do tipo `categoria` para o mesmo (assunto, plataforma,
    canal) se contradizem na certa — é a IA escolhendo qual obedecer. A trava
    fica na aplicação (`services/atendimento/manual.conflitos_da_regra`), não
    num índice: o manual que já existia antes dela precisa continuar
    legível (a tela pinta o conflito de vermelho) em vez de quebrar a
    migration.
    """

    __tablename__ = "atendimento_regras"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    quando: Mapped[str] = mapped_column(Text, nullable=False)
    faca: Mapped[str] = mapped_column(Text, nullable=False)
    # NULL = vale para todas as plataformas / todos os canais.
    plataforma: Mapped[str | None] = mapped_column(String(16), nullable=True)
    canal: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Id de `atendimento_categorias` (ou de `constantes.CATEGORIAS`). Sem FK:
    # a lista pode estar só nas constantes (tabela vazia), e trocar o manual
    # base não pode apagar regra em cascata.
    categoria: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # seguranca | categoria | estilo (constantes.TIPOS_REGRA). O que já
    # existia vira `categoria` sem categoria = geral: o mesmo comportamento
    # de antes (entra sempre).
    tipo: Mapped[str] = mapped_column(
        String(16), nullable=False, default="categoria", server_default=text("'categoria'")
    )
    # Menor = mais importante: vem antes, dentro do mesmo tipo.
    prioridade: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, server_default=text("100")
    )
    ativa: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    criado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    atualizado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


class AtendimentoModelo(Base, TimestampMixin):
    """Resposta pronta (macro) que a pessoa escolhe na tela."""

    __tablename__ = "atendimento_modelos"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    titulo: Mapped[str] = mapped_column(Text, nullable=False)
    texto: Mapped[str] = mapped_column(Text, nullable=False)
    plataforma: Mapped[str | None] = mapped_column(String(16), nullable=True)
    canal: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # O assunto da resposta pronta (manual base, P7): a tela pode sugerir a
    # resposta pronta da categoria da conversa. NULL = qualquer assunto.
    categoria: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ativo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    ordem: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    criado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


class AtendimentoCategoria(Base, TimestampMixin):
    """Um ASSUNTO de atendimento — a lista oficial que a IA usa para classificar.

    Vem do manual base (`scripts/atendimento_manual.py importar`). A IA lê
    daqui as ativas; tabela vazia = `constantes.CATEGORIAS` com as descrições
    de `constantes.CATEGORIAS_INFO` (é o que vale até o manual base entrar).
    O id é o que as regras, os rascunhos e o `auto_categorias` do canal
    guardam — por isso é texto curto e estável, não UUID.
    """

    __tablename__ = "atendimento_categorias"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    # A classificação da IA é pela DESCRIÇÃO, não pelo id: "prazo_envio"
    # sozinho não diz se "já foi postado?" é prazo ou rastreio.
    descricao: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=text("''")
    )
    # Frases típicas do cliente neste assunto (ajudam a classificar).
    exemplos: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # Assunto que só pessoa responde. O banco ACRESCENTA a
    # `constantes.CATEGORIAS_SO_HUMANO`, nunca tira (manual.categorias_ativas).
    so_humano: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # As lacunas ({rastreio}, {nf_numero}...) que a resposta deste assunto usa.
    lacunas: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    ativa: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    ordem: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, server_default=text("100")
    )


class AtendimentoPedidoComprador(Base):
    """Pedido × comprador, por loja — o histórico de compra do cartão "Cliente".

    A Shopee não filtra pedido por comprador (o ML filtra: `/orders/search?
    buyer=`). Então cada retrato de pedido que o DaVinci já busca (o
    enriquecimento da conversa, o job `atendimento_indexar_pedidos`) anota a
    linha aqui. `comprador_id` é o id da plataforma (`buyer_user_id`), não
    nome nem contato: o índice não guarda dado pessoal.
    """

    __tablename__ = "atendimento_pedidos_comprador"
    __table_args__ = (
        # O mesmo pedido volta em toda leitura: a linha é atualizada, não repetida.
        UniqueConstraint("integration_id", "pedido"),
        # "Todos os pedidos deste comprador nesta loja" — a pergunta do cartão.
        Index(
            "ix_atendimento_pedidos_comprador_integration_id_comprador_id",
            "integration_id",
            "comprador_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    # CASCADE: índice de uma loja desligada não tem mais para quem servir.
    integration_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("integrations.id", ondelete="CASCADE"),
        nullable=False,
    )
    plataforma: Mapped[str] = mapped_column(String(16), nullable=False)
    comprador_id: Mapped[str] = mapped_column(String(128), nullable=False)
    pedido: Mapped[str] = mapped_column(String(64), nullable=False)
    # Relógio da PLATAFORMA (hora da compra), não o nosso.
    criado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # "2× Mala de bordo ABS; 1× Cadeado" — o que a linha do tempo mostra.
    itens_resumo: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Quando o DaVinci anotou/atualizou a linha. O upsert (INSERT ... ON
    # CONFLICT) não dispara o `onupdate` do ORM: quem grava põe `now()`.
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AtendimentoAvaliacaoLoja(Base):
    """Uma avaliação que o comprador deixou na venda (Shopee e Mercado Livre).

    O cartão "Cliente" mostra as estrelas e o sinal `avaliou_mal`; a IA manda
    para pessoa quem avaliou mal. Casa com o comprador pelo pedido
    (`order_sn`) e pelo usuário da loja (`buyer_username` = `to_name` da
    conversa). O texto vai cortado em 500 caracteres: é o que a tela mostra.

    Avaliações no atendimento (RF8, 02/10/2026, migration 0358 — o nome
    `atendimento_avaliacoes` já é a nota da pessoa sobre a sugestão da IA):
    a mesma tabela passa a guardar também a OPINIÃO do produto do Mercado
    Livre (`/reviews/item`, com o `order_id`), a mídia, a hora e a
    visibilidade da resposta da loja, e a PENDÊNCIA. Quem lê e decide é
    `services/atendimento/avaliacoes.py`:

      pendente (`pendente_desde` preenchido) = SEM resposta da loja, depois
      da carência (Shopee: nota 1–3 há mais de 1 h, 4–5 há mais de 24 h — o
      robô de fora responde em 1 a 3 h); no ML, que não deixa responder pela
      API, nota 1–3 ainda não tratada. Resolve com a resposta (de fora ou do
      DaVinci) ou com o "marcar como tratada" (`tratada_em`/`tratada_por`).

    Só a pendente ganha conversa própria (`canal = 'avaliacao'`, em
    `conversa_id`) e a etiqueta AVALIAÇÃO; a já respondida aparece na aba ★
    das conversas do mesmo pedido/comprador, lida daqui.
    """

    __tablename__ = "atendimento_avaliacoes_loja"
    __table_args__ = (
        # Idempotência do job: o cursor do get_comment pode trazer de novo.
        UniqueConstraint("integration_id", "comentario_id"),
        Index("ix_atendimento_avaliacoes_loja_integration_id_pedido", "integration_id", "pedido"),
        # 0358: o elo com a conversa de QUALQUER loja da plataforma (a
        # etiqueta e a aba ★ casam por plataforma + pedido) e o cartão
        # "Cliente" do ML (pelo id do comprador).
        Index("ix_atendimento_avaliacoes_loja_plataforma_pedido", "plataforma", "pedido"),
        Index(
            "ix_atendimento_avaliacoes_loja_integration_id_comprador_id",
            "integration_id",
            "comprador_id",
        ),
        # As pendentes (poucas): o fato da etiqueta lê só elas, a cada recálculo.
        Index(
            "ix_atendimento_avaliacoes_loja_pendentes",
            "plataforma",
            "pedido",
            postgresql_where=text("pendente_desde IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    integration_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("integrations.id", ondelete="CASCADE"),
        nullable=False,
    )
    plataforma: Mapped[str] = mapped_column(String(16), nullable=False)
    comentario_id: Mapped[str] = mapped_column(String(64), nullable=False)
    pedido: Mapped[str | None] = mapped_column(String(64), nullable=True)
    comprador_nome_loja: Mapped[str | None] = mapped_column(Text, nullable=True)
    item_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 1 a 5.
    estrelas: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    texto: Mapped[str | None] = mapped_column(Text, nullable=True)
    resposta_loja: Mapped[str | None] = mapped_column(Text, nullable=True)
    criado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── 0358: avaliações no atendimento (RF8) ─────────────────────────────
    # O título da opinião (ML); a Shopee não tem.
    titulo: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Fotos e vídeos: [{"tipo": "imagem"|"video", "url", "miniatura"?}] — só
    # o endereço https da plataforma, nunca o arquivo.
    midia: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # Quando a loja respondeu (relógio da plataforma) e se a resposta está
    # oculta (Shopee `comment_reply.hidden`).
    resposta_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resposta_oculta: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # A edição pelo COMPRADOR, crua (Shopee `editable`: EDITABLE |
    # HAVE_EDITED_ONCE | EXPIRED — ele pode mudar a nota uma vez).
    editavel: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # A variação avaliada (Shopee `model_id`, ML `variation_id`).
    modelo_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # A plataforma deixa responder pela API? (Shopee sim; ML não.) Sem, o
    # porquê que a tela mostra no lugar da caixa de resposta.
    pode_responder: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    motivo_sem_resposta: Mapped[str | None] = mapped_column(Text, nullable=True)
    # PENDENTE desde quando (NULL = não pendente). Ver a regra no topo da classe.
    pendente_desde: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # "Marcar como tratada" (o ML, que não deixa responder): quem e quando.
    tratada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tratada_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # A conversa `canal = 'avaliacao'` criada quando ela ficou pendente.
    # Nome à mão: pela convenção passaria dos 63 caracteres do Postgres.
    conversa_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "atendimento_conversas.id",
            ondelete="SET NULL",
            name="fk_atendimento_avaliacoes_loja_conversa",
        ),
        nullable=True,
        index=True,
    )
    # O id do comprador NA PLATAFORMA (Shopee `buyer_user_id` pelo índice de
    # pedidos; ML `buyer.id` do pedido) — id, não dado pessoal.
    comprador_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # O resto: o pack do ML (`pack_id`), o anúncio, quando foi conferida,
    # a fonte. Material da tela e de depuração.
    dados: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # Quando o DaVinci gravou/mexeu na linha. O upsert (INSERT ... ON
    # CONFLICT) não dispara o `onupdate` do ORM: quem grava põe `now()`.
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AtendimentoCarrinho(Base, TimestampMixin):
    """Um carrinho ABANDONADO de lojista num site (RF9, 02/10/2026, migration 0362).

    Charlots e Uranyx (PHP próprio) são atacado: o carrinho é do lojista
    logado (`adm_carrinho` do site) e termina "pelo WhatsApp". O DaVinci lê,
    servidor a servidor, os carrinhos parados há mais de
    `atendimento_carrinho_horas` e os EVENTOS de finalização
    (`adm_carrinho_eventos` do site) — quem lê e decide é
    `services/atendimento/carrinhos.py`. Uma linha por EPISÓDIO (o carrinho
    que parou, até o desfecho): o mesmo lojista pode ter vários ao longo do
    tempo, mas só UM aberto por site (índice único parcial).

      aberto          — parado, sem desfecho: etiqueta CARRINHO na conversa;
      recuperado      — o lojista finalizou pelo WhatsApp depois de parar
                        ("virou pedido": a etiqueta volta para PÓS-VENDA);
      nao_recuperado  — 7 dias sem finalizar, ou o carrinho foi esvaziado;
      resolvido       — alguém marcou como resolvido na tela.

    `lojista` e `itens` são o RETRATO da última leitura (o site é a verdade);
    o estoque mostrado na tela é o ATUAL do DaVinci, lido na hora pelo SKU
    (`painel.saldo_do_item`), nunca guardado aqui.
    """

    __tablename__ = "atendimento_carrinhos"
    __table_args__ = (
        # Só um carrinho ABERTO por lojista por site: a leitura de 30 em 30
        # minutos atualiza o mesmo, nunca abre outro em cima.
        Index(
            "uq_atendimento_carrinhos_aberto",
            "site",
            "lojista_id",
            unique=True,
            postgresql_where=text("situacao = 'aberto'"),
        ),
        # "Os carrinhos deste lojista" (o histórico no painel).
        Index("ix_atendimento_carrinhos_site_lojista_id", "site", "lojista_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    # `constantes.SITES` (o nome do token em `sites_estoque_tokens`).
    site: Mapped[str] = mapped_column(String(32), nullable=False)
    # O canal `carrinho` do site. SET NULL: o carrinho fica sem a caixa.
    canal_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("atendimento_canais.id", ondelete="SET NULL"),
        nullable=True,
    )
    # A conversa do lojista no canal (`externo_id = "lojista:<id>"`).
    conversa_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("atendimento_conversas.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # `adm_clientes.id` do site, em texto.
    lojista_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # Retrato do lojista: {nome, empresa, email, telefone, cidade, estado,
    # status, cnpj?}. Dado pessoal — esta tabela fica FORA do Histórico.
    lojista: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # Retrato dos itens (o formato do contrato com o site: produto_id,
    # titulo, cor, cor_rotulo, quantidade, skus, url, imagem, preco).
    itens: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    quantidade_total: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    # O último mexido no carrinho, relógio do SITE (MAX de
    # `adm_carrinho.atualizado_em`, em UTC).
    parado_desde: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Quando o DaVinci viu o carrinho parado pela primeira vez (o prazo de 7
    # dias para "não recuperado" conta daqui).
    detectado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # A última leitura em que o carrinho ainda estava no site.
    visto_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # aberto | recuperado | nao_recuperado | resolvido (constantes.SITUACOES_CARRINHO)
    situacao: Mapped[str] = mapped_column(
        String(16), nullable=False, default="aberto", server_default=text("'aberto'")
    )
    encerrado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # finalizado_whatsapp | esvaziado | prazo | marcado_resolvido
    motivo_fim: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # A hora da finalização pelo WhatsApp (relógio do site).
    recuperado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # "Marcar como resolvido": quem e quando.
    tratado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    tratado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # O resto: o evento do site que fechou (`evento_id`, `itens_enviados`),
    # o motivo escrito por quem resolveu. Material da tela e de depuração.
    dados: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class AtendimentoPublicacao(Base, TimestampMixin):
    """Uma publicação de rede social lida pelo atendimento (RF7, 02/10/2026, migration 0362).

    A mídia da PRÓPRIA conta (Instagram/Página do Facebook), cujos
    comentários viram conversa, ou a de outra pessoa que MARCOU a marca (IG
    `/tags`: a menção). É o cartão "Detalhes da publicação" da tela:
    miniatura, legenda, link, curtidas e comentários. Quem lê e escreve é
    `services/atendimento/redes.py`.

    A URL da miniatura da Graph API EXPIRA (CDN assinada): fica guardada com
    a hora em que foi lida (`miniatura_lida_em`) e o leitor a renova; a tela
    não confia numa URL velha.
    """

    __tablename__ = "atendimento_publicacoes"
    __table_args__ = (
        # Idempotência da leitura. A conta entra na chave: a mesma mídia de
        # terceiro pode marcar duas marcas nossas.
        UniqueConstraint("plataforma", "conta_id", "externo_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    # O canal `comentario` da conta. SET NULL: a publicação fica.
    canal_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("atendimento_canais.id", ondelete="SET NULL"),
        nullable=True,
    )
    # instagram | facebook
    plataforma: Mapped[str] = mapped_column(String(16), nullable=False)
    # A conta da MARCA que foi lida (`ig_user_id` / `page_id`).
    conta_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # O id da mídia na rede (IG media id; FB `<page>_<post>`).
    externo_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # propria | mencao (constantes.TIPOS_PUBLICACAO)
    tipo: Mapped[str] = mapped_column(
        String(16), nullable=False, default="propria", server_default=text("'propria'")
    )
    # O formato cru da rede (IMAGE, VIDEO, CAROUSEL_ALBUM, REELS, STORY…).
    formato: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # Quem publicou (na menção, a pessoa que marcou; na própria, o @ da marca).
    autor_username: Mapped[str | None] = mapped_column(Text, nullable=True)
    legenda: Mapped[str | None] = mapped_column(Text, nullable=True)
    # O permalink ("Abrir na rede").
    link: Mapped[str | None] = mapped_column(Text, nullable=True)
    miniatura_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    miniatura_lida_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Relógio da rede.
    publicada_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    curtidas: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comentarios: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dados: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class AtendimentoComentario(Base, TimestampMixin):
    """Um comentário (ou resposta) numa publicação lida (RF7, 02/10/2026, migration 0362).

    Cada comentário de uma PESSOA entra na conversa `comentario` dela naquela
    publicação (`conversa_id`) e vira mensagem lá (`externo_id` = o id do
    comentário). O comentário da PRÓPRIA marca (`da_marca`) é a resposta —
    entra como mensagem da loja na conversa de quem foi respondido, nunca
    como conversa nova. Esta tabela é o que o cartão da publicação lista
    ("os outros comentários, com o desta conversa em destaque") e guarda o
    que a mensagem não tem: o pai, o oculto, a pergunta.
    """

    __tablename__ = "atendimento_comentarios"
    __table_args__ = (
        UniqueConstraint("plataforma", "externo_id"),
        # Os comentários de UMA publicação, em ordem (o cartão).
        Index("ix_atendimento_comentarios_publicacao_id_criado_em", "publicacao_id", "criado_em"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    # CASCADE: comentário sem a publicação não tem contexto. Nome à mão: pela
    # convenção passaria dos 63 caracteres do Postgres.
    publicacao_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "atendimento_publicacoes.id",
            ondelete="CASCADE",
            name="fk_atendimento_comentarios_publicacao",
        ),
        nullable=False,
    )
    # A conversa (pessoa × publicação). SET NULL: a conversa some, o
    # comentário fica no cartão.
    conversa_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("atendimento_conversas.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    plataforma: Mapped[str] = mapped_column(String(16), nullable=False)
    # O id do comentário na rede.
    externo_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # Resposta a outro comentário: o id do pai (o fio).
    pai_externo_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # A pessoa na rede (IG `from.id`/`username`; FB `from.id`/`name`).
    autor_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    autor_username: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Comentário da própria conta da marca = RESPOSTA, não conversa nova.
    da_marca: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    texto: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Relógio da rede.
    criado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    curtidas: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Oculto na rede (pela marca, ou pelo filtro da própria rede).
    oculto: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # `constantes.e_pergunta`: só ordena a fila (RF7).
    eh_pergunta: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    dados: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


# UMA resposta em voo por conversa — declarado no model (create_all dos testes)
# E na migration 0346, como `uq_dm_resposta_em_voo`. Duas abas apertando
# "Enviar", ou a pessoa e o envio automático ao mesmo tempo: só um INSERT
# passa; o outro vira `envio_em_andamento` e o cliente recebe uma resposta só.
Index(
    "uq_atendimento_envio_em_voo",
    AtendimentoMensagem.conversa_id,
    unique=True,
    postgresql_where=text("status = 'enviando'"),
)

# UM rascunho pendente por conversa: a caixa de resposta mostra UMA sugestão,
# e dois workers de IA gerando para a mesma conversa gastariam token à toa.
Index(
    "uq_atendimento_rascunho_pendente",
    AtendimentoRascunho.conversa_id,
    unique=True,
    postgresql_where=text("status = 'pendente'"),
)

# A idempotência da conversa das lojas do ROBÔ (migration 0347). Elas não têm
# integração, e o UNIQUE (integration_id, canal, externo_id) não vale com
# NULL: a chave é o CANAL (a loja) — duas lojas Temu podem repetir o id de
# conversa. A Amazon sem conta identificada (integração e canal NULL) fica de
# fora do predicado, com a regra de sempre (`gravar._buscar_conversa`).
Index(
    "uq_atendimento_conversas_robo",
    AtendimentoConversa.canal_id,
    AtendimentoConversa.externo_id,
    unique=True,
    postgresql_where=text("integration_id IS NULL AND canal_id IS NOT NULL"),
)
