"""Contabilidade › Compra: emissão automática da NF de compra pelo robô do Bling 44

Eduardo (04/11/2026): o botão "Emitir" da compra põe na fila e o robô do Mac
(~/nf-executor/compra_emitir.py) importa, gera a NF, manda à SEFAZ uma vez só e
devolve o XML. Espelha `app/models/compra.py`:

- `emissao_etapa`: até onde o robô chegou (só ele escreve). Da `import_tentado`
  em diante o pedido 800000x pode já existir no Bling da vendedora — o número
  fica "queimado" (nunca mais editar, reabrir, excluir nem baixar a planilha).
- `planilha_baixada_em`: alguém baixou a planilha (importação manual possível);
  o "Emitir" pede confirmação.
- índice único parcial em `nf_chave`: a mesma NF não fica anotada em duas compras.

Revision ID: 0450_compra_emissao_robo
Revises: 0449_certificacoes_edicao_manual
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0450_compra_emissao_robo"
down_revision: str | None = "0449_certificacoes_edicao_manual"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
# Mesma lista (e mesma ordem) de `app/models/compra.py:ETAPAS`.
ETAPAS = (
    "lease",
    "import_tentado",
    "importado",
    "nf_gerada",
    "pronta_sefaz",
    "sefaz_tentado",
    "autorizada",
    "xml_ok",
    "devolvido",
)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.add_column(
        "compra_pedido",
        sa.Column("emissao_etapa", sa.String(16), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "compra_pedido",
        sa.Column("planilha_baixada_em", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_compra_pedido_emissao_etapa_valida"),
        "compra_pedido",
        f"emissao_etapa IS NULL OR emissao_etapa IN {ETAPAS}",
        schema=SCHEMA,
    )
    op.create_index(
        "uq_compra_pedido_nf_chave",
        "compra_pedido",
        ["nf_chave"],
        unique=True,
        postgresql_where=sa.text("nf_chave IS NOT NULL"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("uq_compra_pedido_nf_chave", table_name="compra_pedido", schema=SCHEMA)
    op.drop_constraint(
        op.f("ck_compra_pedido_emissao_etapa_valida"),
        "compra_pedido",
        type_="check",
        schema=SCHEMA,
    )
    op.drop_column("compra_pedido", "planilha_baixada_em", schema=SCHEMA)
    op.drop_column("compra_pedido", "emissao_etapa", schema=SCHEMA)
