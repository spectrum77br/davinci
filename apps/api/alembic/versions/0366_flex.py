"""Flex: tipo de envio na Logística, pedidos Flex e estado do Flex por anúncio

Procedimento Flex (Downloads/procedimento-flex.md) com as decisões de 02/10/2026
e a análise relatorios/Flex_analise_02-10-2026.md. UMA migration para o projeto
inteiro: as etapas seguintes editam esta mesma enquanto ela não for publicada.

O que muda (só aditivo):
  • `logistica.envio_tipo` (TEXT) e `logistica.envio_flex` (BOOLEAN), os dois
    NULL e SEM default — ADD COLUMN assim não reescreve a tabela. Índice
    PARCIAL `ix_logistica_envio_flex` WHERE envio_flex IS TRUE (a aba Flex lê
    só essas; quase nenhuma linha é Flex). O valor nasce no enriquecimento da
    Logística (ML: o envio que já é lido; Shopee: o pedido em lote) — a
    migration não chama API nem preenche nada para trás.
  • `flex_pedido`: pedido cujo envio é Flex, por `bling_id` (PK). Escrito pelo
    `marketplace_shipment_check` com o envio que ele já lê de minuto em minuto.
  • `flex_anuncio_estado`: estado do Flex por anúncio (integration_id,
    external_id) — desejado × observado, aprovação, fila de escrita.
  • `flex_log`: trilha só de inserção (antes/depois, modo, resultado). Também
    guarda o pedido Flex levado ao .sp pelo robô de prioridade (`bling_id`,
    ações `pedido_sp` / `pedido_sem_sp` — etapa 2). Etapa 3 (motor por
    anúncio): ação `decidir`, `motivo` (o porquê em texto) e `por` (a pessoa
    que aprovou/apertou a emergência; NULL = robô); e `flex_anuncio_estado.
    recusa` (a plataforma recusou ligar — não tenta de novo sozinho).
    Etapa 5 (correções): `flex_pedido.sp_em` (o robô levou o pedido ao .sp —
    o motor desconta até o produto .sp ser atualizado depois disso),
    `acertado_em`/`acertado_por` (pedido Flex que saiu sem passar pelo .sp:
    desconta até uma pessoa dizer que acertou o estoque no Bling) e a ação
    `acertar_estoque` na trilha.
    Revisão com os fatos das contas (02/10/2026): `flex_anuncio_estado` ganha a
    fila de leitura justa (`leitura_em`, `leitura_falhas`, `proxima_leitura`)
    e o status do anúncio na plataforma (`status_anuncio`, `status_em`);
    `flex_conta` (a conta pode ter Flex? assinatura do ML / canal da loja
    Shopee, e a descoberta dos anúncios da conta) e `flex_emergencia` (o
    "Desligar tudo" vira job do worker com andamento).

Valores fechados em TEXT com CHECK (nada de enum do Postgres). As cinco
tabelas ficam FORA do Histórico (historico/sql.EXCLUIDAS): são da máquina.

`lock_timeout` de 3 s como a 0346/0353/0358: o ALTER pega AccessExclusiveLock
na `logistica` (o motor de 5 em 5 min e a api gravam nela) e as FKs novas pegam
ShareRowExclusiveLock em `integrations` e `users`. `tests/test_flex_migration.py`
roda esta migration num schema descartável e compara o catálogo com o model.

O downgrade apaga as cinco tabelas (o estado e o log do Flex se perdem) e as
duas colunas da Logística (a aba Flex fica vazia até reclassificar).

Revision ID: 0366_flex
Revises: 0365_marca_emails
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0366_flex"
down_revision: str | None = "0365_marca_emails"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

# Mesmos valores de app/models/flex.py (a migration não importa o app: o
# model pode mudar depois, a migration publicada não).
_PLATAFORMAS = ("ml", "shopee")
_DESEJADO = ("ligado", "desligado", "inelegivel")
_OBSERVADO = ("ligado", "desligado")
_MODOS = ("desligado", "observar", "piloto", "ativo")
_ACOES = (
    "decidir",
    "ligar",
    "desligar",
    "pedir_aprovacao",
    "aprovar",
    "emergencia",
    "ler",
    "pedido_sp",
    "pedido_sem_sp",
    "acertar_estoque",
)
_RESULTADOS = ("ok", "erro", "simulado", "pendente", "ignorado")
_STATUS_ANUNCIO = ("active", "paused", "under_review", "inactive", "closed")
_EMERGENCIA_STATUS = ("na_fila", "rodando", "concluida", "falhou")


def _in(coluna: str, valores: tuple[str, ...]) -> str:
    lista = ", ".join(f"'{v}'" for v in valores)
    return f"{coluna} IN ({lista})"


def _agora() -> sa.TextClause:
    return sa.text("now()")


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    # Nomes de constraint com op.f(): já são os finais da convenção do model
    # (ck_<tabela>_<nome>); sem isso o alembic passaria de novo por ela
    # (ck_flex_log_ck_flex_log_acao).

    # ---- Logística: o tipo de envio da plataforma -------------------------
    op.add_column("logistica", sa.Column("envio_tipo", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column("logistica", sa.Column("envio_flex", sa.Boolean(), nullable=True), schema=SCHEMA)
    op.create_index(
        "ix_logistica_envio_flex",
        "logistica",
        ["envio_flex"],
        postgresql_where=sa.text("envio_flex IS TRUE"),
        schema=SCHEMA,
    )

    # ---- flex_pedido --------------------------------------------------------
    op.create_table(
        "flex_pedido",
        sa.Column("bling_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("plataforma", sa.Text(), nullable=False),
        sa.Column("integration_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("numeroloja", sa.Text(), nullable=True),
        sa.Column("envio_tipo", sa.Text(), nullable=True),
        sa.Column("prazo", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "detectado_em", sa.DateTime(timezone=True), server_default=_agora(), nullable=False
        ),
        sa.Column("no_sp", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("alerta", sa.Text(), nullable=True),
        sa.Column("sp_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acertado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acertado_por", pg.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "atualizado_em", sa.DateTime(timezone=True), server_default=_agora(), nullable=False
        ),
        sa.CheckConstraint(_in("plataforma", _PLATAFORMAS), name=op.f("ck_flex_pedido_plataforma")),
        sa.ForeignKeyConstraint(
            ["integration_id"],
            [f"{SCHEMA}.integrations.id"],
            name=op.f("fk_flex_pedido_integration_id_integrations"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["acertado_por"],
            [f"{SCHEMA}.users.id"],
            name=op.f("fk_flex_pedido_acertado_por_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("bling_id", name=op.f("pk_flex_pedido")),
        schema=SCHEMA,
    )

    # ---- flex_anuncio_estado ------------------------------------------------
    op.create_table(
        "flex_anuncio_estado",
        sa.Column("integration_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("plataforma", sa.Text(), nullable=False),
        sa.Column("desejado", sa.Text(), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column("observado", sa.Text(), nullable=True),
        sa.Column("observado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "aguardando_aprovacao", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("aprovado_por", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("aprovado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("aplicado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tentativas", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("proxima_tentativa", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ultimo_erro", sa.Text(), nullable=True),
        sa.Column("recusa", sa.Text(), nullable=True),
        sa.Column("saldo_sp", sa.Integer(), nullable=True),
        sa.Column("familias", sa.Text(), nullable=True),
        sa.Column("leitura_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("leitura_falhas", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("proxima_leitura", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_anuncio", sa.Text(), nullable=True),
        sa.Column("status_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "atualizado_em", sa.DateTime(timezone=True), server_default=_agora(), nullable=False
        ),
        sa.CheckConstraint(
            _in("plataforma", _PLATAFORMAS), name=op.f("ck_flex_anuncio_estado_plataforma")
        ),
        sa.CheckConstraint(
            _in("desejado", _DESEJADO), name=op.f("ck_flex_anuncio_estado_desejado")
        ),
        sa.CheckConstraint(
            _in("observado", _OBSERVADO), name=op.f("ck_flex_anuncio_estado_observado")
        ),
        sa.CheckConstraint(
            _in("status_anuncio", _STATUS_ANUNCIO),
            name=op.f("ck_flex_anuncio_estado_status_anuncio"),
        ),
        sa.ForeignKeyConstraint(
            ["integration_id"],
            [f"{SCHEMA}.integrations.id"],
            name=op.f("fk_flex_anuncio_estado_integration_id_integrations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["aprovado_por"],
            [f"{SCHEMA}.users.id"],
            name=op.f("fk_flex_anuncio_estado_aprovado_por_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "integration_id", "external_id", name=op.f("pk_flex_anuncio_estado")
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_flex_anuncio_estado_aguardando",
        "flex_anuncio_estado",
        ["integration_id"],
        postgresql_where=sa.text("aguardando_aprovacao"),
        schema=SCHEMA,
    )

    # ---- flex_log -----------------------------------------------------------
    op.create_table(
        "flex_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=_agora(), nullable=False),
        sa.Column("integration_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("plataforma", sa.Text(), nullable=True),
        sa.Column("bling_id", sa.BigInteger(), nullable=True),
        sa.Column("sku", sa.Text(), nullable=True),
        sa.Column("saldo_sp", sa.Integer(), nullable=True),
        sa.Column("acao", sa.Text(), nullable=False),
        sa.Column("estado_antes", sa.Text(), nullable=True),
        sa.Column("estado_depois", sa.Text(), nullable=True),
        sa.Column("modo", sa.Text(), nullable=False),
        sa.Column("resultado", sa.Text(), nullable=False),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column("por", pg.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(_in("plataforma", _PLATAFORMAS), name=op.f("ck_flex_log_plataforma")),
        sa.CheckConstraint(_in("acao", _ACOES), name=op.f("ck_flex_log_acao")),
        sa.CheckConstraint(_in("estado_antes", _DESEJADO), name=op.f("ck_flex_log_estado_antes")),
        sa.CheckConstraint(_in("estado_depois", _DESEJADO), name=op.f("ck_flex_log_estado_depois")),
        sa.CheckConstraint(_in("modo", _MODOS), name=op.f("ck_flex_log_modo")),
        sa.CheckConstraint(_in("resultado", _RESULTADOS), name=op.f("ck_flex_log_resultado")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_flex_log")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_flex_log_anuncio",
        "flex_log",
        ["integration_id", "external_id", "criado_em"],
        schema=SCHEMA,
    )
    op.create_index("ix_flex_log_criado_em", "flex_log", ["criado_em"], schema=SCHEMA)

    # ---- flex_conta ---------------------------------------------------------
    op.create_table(
        "flex_conta",
        sa.Column("integration_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("plataforma", sa.Text(), nullable=False),
        sa.Column("flex_ativo", sa.Boolean(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("detalhe", sa.Text(), nullable=True),
        sa.Column("lido_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column("descoberta_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("descoberta_ok", sa.Boolean(), nullable=True),
        sa.Column("descoberta_total", sa.Integer(), nullable=True),
        sa.Column("descoberta_novos", sa.Integer(), nullable=True),
        sa.Column("descoberta_erro", sa.Text(), nullable=True),
        sa.Column(
            "atualizado_em", sa.DateTime(timezone=True), server_default=_agora(), nullable=False
        ),
        sa.CheckConstraint(_in("plataforma", _PLATAFORMAS), name=op.f("ck_flex_conta_plataforma")),
        sa.ForeignKeyConstraint(
            ["integration_id"],
            [f"{SCHEMA}.integrations.id"],
            name=op.f("fk_flex_conta_integration_id_integrations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("integration_id", name=op.f("pk_flex_conta")),
        schema=SCHEMA,
    )

    # ---- flex_emergencia ----------------------------------------------------
    op.create_table(
        "flex_emergencia",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("pedido_em", sa.DateTime(timezone=True), server_default=_agora(), nullable=False),
        sa.Column("por", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("escopo", pg.JSONB(), nullable=True),
        sa.Column("modo", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "aprovados", pg.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column("resumo", pg.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("iniciado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column(
            "atualizado_em", sa.DateTime(timezone=True), server_default=_agora(), nullable=False
        ),
        sa.CheckConstraint(
            _in("status", _EMERGENCIA_STATUS), name=op.f("ck_flex_emergencia_status")
        ),
        sa.CheckConstraint(_in("modo", _MODOS), name=op.f("ck_flex_emergencia_modo")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_flex_emergencia")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_flex_emergencia_pedido_em", "flex_emergencia", ["pedido_em"], schema=SCHEMA
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.drop_index("ix_flex_emergencia_pedido_em", table_name="flex_emergencia", schema=SCHEMA)
    op.drop_table("flex_emergencia", schema=SCHEMA)
    op.drop_table("flex_conta", schema=SCHEMA)
    op.drop_index("ix_flex_log_criado_em", table_name="flex_log", schema=SCHEMA)
    op.drop_index("ix_flex_log_anuncio", table_name="flex_log", schema=SCHEMA)
    op.drop_table("flex_log", schema=SCHEMA)
    op.drop_index(
        "ix_flex_anuncio_estado_aguardando", table_name="flex_anuncio_estado", schema=SCHEMA
    )
    op.drop_table("flex_anuncio_estado", schema=SCHEMA)
    op.drop_table("flex_pedido", schema=SCHEMA)
    op.drop_index("ix_logistica_envio_flex", table_name="logistica", schema=SCHEMA)
    op.drop_column("logistica", "envio_flex", schema=SCHEMA)
    op.drop_column("logistica", "envio_tipo", schema=SCHEMA)
