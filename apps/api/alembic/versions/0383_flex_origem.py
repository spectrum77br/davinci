"""Flex: de onde o motoboy sai (origem da assinatura) e o local de saída editável

Decisões do Eduardo (08/10/2026): "o motoboy vai sair de São Bernardo"
(procedimento-flex.md, seção 2) e "não vai ser pra sempre fixo em São
Bernardo, eu quero poder alterar". A leitura de 08/10 mostrou as 17 contas do
ML com a assinatura "in" saindo de Piracicaba (origin.zip_code 134xx).

- flex_conta.origem_cep / origem_cidade / origem_lida_em: a origem da
  assinatura do Flex
  (`GET /flex/sites/MLB/users/{id}/subscriptions/v1` → origin.zip_code e
  origin.city.name; mais de uma: separadas por vírgula). O motor só mexe na
  conta cuja saída é na cidade do local.
- flex_pedido.lote: o lote do local de saída de cada pedido Flex (os de hoje:
  .sp). O pedido que já saiu fica com o seu ao trocar o local.
- flex_local (uma linha, id = 1): a cidade de saída do Flex e o lote do
  estoque de lá, trocados na aba Flex. Nasce com São Bernardo do Campo / .sp
  — o mesmo que o código fazia fixo até aqui.

Só ADD COLUMN nulo (não reescreve) e uma tabela nova. flex_conta tem uma linha
por conta (38) e só o motor do Flex escreve nela; lock_timeout de 3 s como a
0367. As linhas de hoje ficam com a origem NULL: na primeira rodada depois do
deploy o motor pergunta de novo a assinatura das contas "in" sem origem (não
espera a validade de 1 h) — até lá a conta fica bloqueada ("não deu para ler
de onde sai"). O gatilho do Histórico entra na flex_local quando o worker
sobe (historico_manutencao, run_at_startup).

Revision ID: 0383_flex_origem
Revises: 0382_conferencia_plataformas
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0383_flex_origem"
down_revision: str | None = "0382_conferencia_plataformas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
LOTES = ("ci", "pi", "ra", "sa", "sp")


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.add_column("flex_conta", sa.Column("origem_cep", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column("flex_conta", sa.Column("origem_cidade", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column(
        "flex_conta",
        sa.Column("origem_lida_em", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    # O lote de cada pedido Flex (os de hoje são do .sp — o que era fixo).
    # Default constante: não reescreve a tabela.
    op.add_column(
        "flex_pedido",
        sa.Column("lote", sa.Text(), server_default=sa.text("'sp'"), nullable=False),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_flex_pedido_lote"),
        "flex_pedido",
        "lote IN (" + ", ".join(f"'{v}'" for v in LOTES) + ")",
        schema=SCHEMA,
    )
    op.create_table(
        "flex_local",
        sa.Column(
            "id",
            sa.SmallInteger(),
            server_default=sa.text("1"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("cidade", sa.Text(), nullable=False),
        sa.Column("lote", sa.Text(), nullable=False),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("atualizado_por", pg.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint("id = 1", name=op.f("ck_flex_local_uma_linha")),
        sa.CheckConstraint(
            "lote IN (" + ", ".join(f"'{v}'" for v in LOTES) + ")", name=op.f("ck_flex_local_lote")
        ),
        sa.CheckConstraint("length(btrim(cidade)) > 0", name=op.f("ck_flex_local_cidade")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_flex_local")),
        schema=SCHEMA,
    )
    op.execute(
        f"INSERT INTO {SCHEMA}.flex_local (id, cidade, lote) "  # noqa: S608 — SCHEMA é constante
        "VALUES (1, 'São Bernardo do Campo', 'sp')"
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.drop_table("flex_local", schema=SCHEMA)
    op.drop_constraint(op.f("ck_flex_pedido_lote"), "flex_pedido", schema=SCHEMA)
    op.drop_column("flex_pedido", "lote", schema=SCHEMA)
    op.drop_column("flex_conta", "origem_lida_em", schema=SCHEMA)
    op.drop_column("flex_conta", "origem_cidade", schema=SCHEMA)
    op.drop_column("flex_conta", "origem_cep", schema=SCHEMA)
