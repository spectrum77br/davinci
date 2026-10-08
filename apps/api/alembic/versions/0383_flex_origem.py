"""Flex: de onde o motoboy sai (origem da assinatura do Flex de cada conta)

Decisão do Eduardo (08/10/2026, procedimento-flex.md seção 2): "o motoboy vai
sair de São Bernardo". A leitura de 08/10 mostrou as 17 contas do ML com a
assinatura "in" saindo de Piracicaba (origin.zip_code 134xx). O motor passa a
guardar a origem da assinatura (`GET /flex/sites/MLB/users/{id}/subscriptions/v1`
→ `origin.zip_code` / `origin.city.name`) e só mexe na conta cuja saída está
nas faixas de `flex_origem_ceps` (padrão 09600-09899, São Bernardo do Campo).

- flex_conta.origem_cep: CEP só com dígitos (mais de uma origem: vírgula).
- flex_conta.origem_cidade: nome da cidade da origem.

Só ADD COLUMN nulo, sem default (não reescreve). flex_conta tem uma linha por
conta (38) e só o motor do Flex escreve nela; lock_timeout de 3 s como a 0367.
As linhas de hoje ficam com NULL: na primeira rodada depois do deploy o motor
pergunta de novo a assinatura das contas "in" sem origem (não espera a
validade de 1 h) — até lá a conta fica bloqueada ("não deu para ler a saída").

Revision ID: 0383_flex_origem
Revises: 0382_conferencia_plataformas
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0383_flex_origem"
down_revision: str | None = "0382_conferencia_plataformas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.add_column("flex_conta", sa.Column("origem_cep", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column("flex_conta", sa.Column("origem_cidade", sa.Text(), nullable=True), schema=SCHEMA)


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.drop_column("flex_conta", "origem_cidade", schema=SCHEMA)
    op.drop_column("flex_conta", "origem_cep", schema=SCHEMA)
