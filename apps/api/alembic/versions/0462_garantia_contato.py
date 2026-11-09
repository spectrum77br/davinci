"""garantia_contato — o e-mail e o WhatsApp do comprador na aba Dados

Pedido do Eduardo (09/11/2026): "quero que as informações apareçam aqui
somente" — o e-mail e o número da pessoa, já no formato do WhatsApp, nos
Detalhes da garantia (aba Dados).

Medido em 09/11/2026 (só lendo, 12 pedidos por plataforma): o cadastro do
contato do pedido no Bling (GET /contatos/{id}) traz o telefone da entrega
nos pedidos Amazon (10 de 12) e o e-mail repassador da Amazon; Mercado Livre,
Shopee e TikTok vêm vazios. O robô (`garantia_contato.preencher`) lê o Bling
uma vez por garantia e guarda aqui:

* `garantias.contato_telefone` TEXT NULL — só dígitos, com o 55 na frente
  (o formato do wa.me);
* `garantias.contato_email` TEXT NULL;
* `garantias.contato_lido_em` TIMESTAMPTZ NULL — quando o robô leu o Bling
  (NULL = ainda não leu; lido e vazio = o Bling não tem).

Não preenche nada: o robô do worker preenche aos poucos (o Bling tem limite
de chamadas).

DOWNGRADE: tira as 3 colunas (o contato lido se perde; o robô lê de novo).

Revision ID: 0462_garantia_contato
Revises: 0461_certificacao_garantia
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0462_garantia_contato"
down_revision: str | None = "0461_certificacao_garantia"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
# Constante de módulo: o teste troca pelo schema de teste.
SCHEMA = "davinci"


def upgrade() -> None:
    # O robô da garantia pode estar no meio de uma volta nas garantias: desiste
    # em 5 s em vez de enfileirar a API atrás da migration.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column(
        "garantias", sa.Column("contato_telefone", sa.Text(), nullable=True), schema=SCHEMA
    )
    op.add_column("garantias", sa.Column("contato_email", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column(
        "garantias",
        sa.Column("contato_lido_em", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    for coluna in ("contato_lido_em", "contato_email", "contato_telefone"):
        op.drop_column("garantias", coluna, schema=SCHEMA)
