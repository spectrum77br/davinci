"""Pós Vendas: espelho local das NF-e das contas de emissão.

A página Notas Fiscais vira "Pós Vendas": pedidos enviados × as duas notas
de cada envio (embalagem e produto). Pra casar pedido ↔ nota sem consultar o
Bling ao vivo, um cron espelha as notas de cada conta `bling_notas` em
`bling_notas_emitidas` (o valor da nota só existe no detalhe da API — o sync
completa aos poucos).

- `bling_notas.cnpj` / `.emitente`: CNPJ e razão social do emitente da conta
  (fixos por conta; vêm do primeiro XML autorizado). Classificação:
  CNPJ da conta == store_info.cnpj da loja do pedido → NF embalagem;
  outra conta → NF produto (avulsa).
- `bling_notas_emitidas`: uma linha por NF-e listada; chaves de casamento
  `complemento` (numeroloja gravado pelo fluxo de emissão) e `cpf_dest`.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0224_pos_vendas_notas"
down_revision: str | None = "0223_store_info_etiqueta_sabado"
branch_labels = None
depends_on = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "bling_notas", sa.Column("cnpj", sa.Text(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "bling_notas", sa.Column("emitente", sa.Text(), nullable=True), schema=SCHEMA
    )
    op.create_table(
        "bling_notas_emitidas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("conta_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bling_id", sa.BigInteger(), nullable=False),
        sa.Column("numero", sa.Text(), nullable=True),
        sa.Column("situacao", sa.Integer(), nullable=True),
        sa.Column("data_emissao", sa.DateTime(), nullable=True),
        sa.Column("chave_acesso", sa.Text(), nullable=True),
        sa.Column("cpf_dest", sa.Text(), nullable=True),
        sa.Column("nome_dest", sa.Text(), nullable=True),
        sa.Column("complemento", sa.Text(), nullable=True),
        sa.Column("valor", sa.Numeric(), nullable=True),
        sa.Column(
            "detalhe_ok",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conta_id"],
            [f"{SCHEMA}.bling_notas.id"],
            name="fk_bling_notas_emitidas_conta_id_bling_notas",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "conta_id",
            "bling_id",
            name="uq_bling_notas_emitidas_conta_id_bling_id",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_bling_notas_emitidas_cpf_dest",
        "bling_notas_emitidas",
        ["cpf_dest"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_bling_notas_emitidas_complemento",
        "bling_notas_emitidas",
        ["complemento"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_bling_notas_emitidas_data_emissao",
        "bling_notas_emitidas",
        ["data_emissao"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("bling_notas_emitidas", schema=SCHEMA)
    op.drop_column("bling_notas", "emitente", schema=SCHEMA)
    op.drop_column("bling_notas", "cnpj", schema=SCHEMA)
