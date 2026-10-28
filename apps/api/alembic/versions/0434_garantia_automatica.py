"""garantia automática — o robô cria pela NF de produto e liga as conversas

Pedido do Eduardo (28/10/2026): a garantia do pedido Uranyx "cai sozinha" no
Painel de Garantia, ligada pelo número da NF, com o histórico das conversas
(fotos e vídeos) no estilo do Atendimento — sem o "Vincular à garantia" →
"Nova garantia" → "Cadastrar" de hoje.

* `garantias`: `criado_por` passa a aceitar NULL (o robô), com `origem`
  ('manual' | 'robo') e a regra "manual tem autor"; `descartada_em` /
  `descartada_motivo` para a garantia do robô cujo pedido foi cancelado antes
  de qualquer atendimento (sai da lista, não se apaga — o log aponta para ela).
* `garantia_log`: as ações `criou_automatico`, `descartou` e `reativou`.
* `garantia_conversas` (nova): ponteiro garantia × conversa. Sem cópia de
  texto — o painel lê `atendimento_mensagens` na hora.
* `garantia_pendencias` (nova): o pedido que o robô viu e não criou (motivo).
* Índice `atendimento_pedidos_comprador(pedido)`: o robô acha a conversa do
  comprador (Shopee/TikTok) pelo nº do pedido — o índice que existe começa
  pela integração.

As duas tabelas novas ficam fora do gatilho do Histórico (`historico/sql.py`,
EXCLUIDAS): são escritas pela máquina. As garantias que já existem ficam
`origem='manual'` (o default) — nada existente muda de valor.

Revision ID: 0434_garantia_automatica
Revises: 0433_atendimento_zap_saude
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0434_garantia_automatica"
down_revision: str | None = "0433_atendimento_zap_saude"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
SCHEMA = "davinci"

# Lista FIXA (não importa a constante viva do modelo): a migration tem de
# continuar igual quando a lista do código crescer.
ACOES_ANTES = (
    "cadastrou",
    "alterou",
    "consultou",
    "registrou_atendimento",
    "recalculou",
    "buscou_cpf",
)
ACOES_DEPOIS = (*ACOES_ANTES, "criou_automatico", "descartou", "reativou")


def _lista(valores: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valores)


def upgrade() -> None:
    op.add_column(
        "garantias",
        sa.Column("origem", sa.String(16), nullable=False, server_default="manual"),
        schema=SCHEMA,
    )
    op.add_column(
        "garantias",
        sa.Column("descartada_em", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "garantias", sa.Column("descartada_motivo", sa.Text(), nullable=True), schema=SCHEMA
    )
    op.alter_column("garantias", "criado_por", nullable=True, schema=SCHEMA)
    op.create_check_constraint(
        "origem_valida", "garantias", "origem IN ('manual', 'robo')", schema=SCHEMA
    )
    op.create_check_constraint(
        "manual_tem_autor",
        "garantias",
        "origem <> 'manual' OR criado_por IS NOT NULL",
        schema=SCHEMA,
    )

    op.drop_constraint("ck_garantia_log_acao_valida", "garantia_log", schema=SCHEMA)
    op.create_check_constraint(
        "acao_valida", "garantia_log", f"acao IN ({_lista(ACOES_DEPOIS)})", schema=SCHEMA
    )

    op.create_table(
        "garantia_conversas",
        sa.Column(
            "garantia_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.garantias.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "conversa_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.atendimento_conversas.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("como", sa.String(16), nullable=False),
        sa.Column(
            "ligada_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("como IN ('pedido', 'comprador')", name="como_valido"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_garantia_conversas_conversa", "garantia_conversas", ["conversa_id"], schema=SCHEMA
    )

    op.create_table(
        "garantia_pendencias",
        sa.Column("pedido_bling", sa.String(20), primary_key=True),
        sa.Column("motivo", sa.String(40), nullable=False),
        sa.Column("detalhe", sa.Text(), nullable=True),
        sa.Column(
            "visto_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("tentativas", sa.Integer(), nullable=False, server_default=sa.text("1")),
        schema=SCHEMA,
    )

    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_atendimento_pedidos_comprador_pedido"
        f" ON {SCHEMA}.atendimento_pedidos_comprador (pedido)"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_atendimento_pedidos_comprador_pedido")
    op.drop_table("garantia_pendencias", schema=SCHEMA)
    op.drop_index("ix_garantia_conversas_conversa", "garantia_conversas", schema=SCHEMA)
    op.drop_table("garantia_conversas", schema=SCHEMA)
    op.drop_constraint("ck_garantia_log_acao_valida", "garantia_log", schema=SCHEMA)
    op.create_check_constraint(
        "acao_valida", "garantia_log", f"acao IN ({_lista(ACOES_ANTES)})", schema=SCHEMA
    )
    op.drop_constraint("ck_garantias_manual_tem_autor", "garantias", schema=SCHEMA)
    op.drop_constraint("ck_garantias_origem_valida", "garantias", schema=SCHEMA)
    # Volta o NOT NULL: só dá se o robô ainda não criou nenhuma (senão o
    # downgrade falha de propósito — apagar garantia não é decisão de migration).
    op.alter_column("garantias", "criado_por", nullable=False, schema=SCHEMA)
    op.drop_column("garantias", "descartada_motivo", schema=SCHEMA)
    op.drop_column("garantias", "descartada_em", schema=SCHEMA)
    op.drop_column("garantias", "origem", schema=SCHEMA)
