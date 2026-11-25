"""Produtos 2: "Incluir" entrada no Bling (M-43) e "Trocar a tag" em massa (M-46)

Pedidos do Harry (prévia aprovada pelo administrador em 24/11/2026), só tabelas novas:

- `estoque_entradas` (M-43): uma entrada avulsa lançada no Bling pelo botão "Incluir" do estoque
  do produto — quem, quando, produto, quantidade, observação, custo mandado, status do
  lançamento (pendente → enviando → ok | falhou | conferindo → sem_confirmacao), o movimento do
  extrato que confirmou (`mov_ref`) e o id do lançamento no Bling. `idem_key` única (a mesma
  chave da tela nunca lança duas vezes), `numero` curto por sequência (a observação do extrato
  diz "Entrada DaVinci #N"). CHECKs: quantidade 1..100000 e o status da lista.
- `produto_trocas_tag` / `produto_trocas_tag_itens` (M-46): cada "Aplicar" do menu Configuração
  (quem, quando, a tag, a chave da tela) e cada SKU dele (código antigo, código novo, id no Bling,
  estado ok/pulado/falhou… e o motivo). CHECK do estado.

As três ficam COM o gatilho do Histórico (o diário do worker instala, como na 0453): quem lançou
estoque e quem trocou o código de um produto é o que o Histórico serve para mostrar.

Sem ALTER em tabela existente. `SET LOCAL lock_timeout` como na 0505/0507/0508: as FKs novas
apontam para `users`; se algo segurar o catálogo, desiste em 5 s e é só rodar de novo.

DOWNGRADE: apaga as três tabelas (nada de fora aponta para elas) e a sequência.

Revision ID: 0510_estoque_entradas_troca_tag
Revises: 0509_mudanca_auto_aprovacao
"""

# ruff: noqa: S608 — SQL montado só com nomes fixos desta migration (schema), sem entrada de fora
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0510_estoque_entradas_troca_tag"
down_revision: str | None = "0509_mudanca_auto_aprovacao"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Constante de módulo: o teste troca pelo schema de teste.
SCHEMA = "davinci"

ENTRADAS = "estoque_entradas"
SEQ = "estoque_entradas_numero_seq"
TROCAS = "produto_trocas_tag"
ITENS = "produto_trocas_tag_itens"
ESTADOS_ENTRADA = "'pendente','enviando','ok','falhou','conferindo','sem_confirmacao'"
ESTADOS_ITEM = "'pendente','enviando','ok','pulado','falhou','sem_confirmacao'"


def _datas() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def _criado_por(tabela: str) -> sa.Column:
    return sa.Column(
        "criado_por",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey(
            f"{SCHEMA}.users.id",
            ondelete="SET NULL",
            name=op.f(f"fk_{tabela}_criado_por_users"),
        ),
        nullable=True,
    )


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(sa.schema.CreateSequence(sa.Sequence(SEQ, schema=SCHEMA)))
    op.create_table(
        ENTRADAS,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "numero",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text(f"nextval('{SCHEMA}.{SEQ}'::regclass)"),
        ),
        sa.Column("idem_key", sa.Text(), nullable=False),
        sa.Column("sku", sa.Text(), nullable=False),
        sa.Column("bling_product_id", sa.BigInteger(), nullable=False),
        sa.Column("quantidade", sa.Integer(), nullable=False),
        sa.Column("custo", sa.Numeric(14, 4), nullable=True),
        sa.Column("observacao", sa.Text(), nullable=True),
        _criado_por(ENTRADAS),
        sa.Column("criado_por_nome", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pendente'")),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column("tentativa_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lancada_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mov_ref", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("bling_lancamento_id", sa.BigInteger(), nullable=True),
        *_datas(),
        # Nomes finais (`op.f`), os mesmos que a convenção do modelo dá.
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{ENTRADAS}")),
        sa.UniqueConstraint("numero", name=op.f(f"uq_{ENTRADAS}_numero")),
        sa.UniqueConstraint("idem_key", name=op.f(f"uq_{ENTRADAS}_idem_key")),
        sa.CheckConstraint(
            "quantidade BETWEEN 1 AND 100000", name=op.f(f"ck_{ENTRADAS}_quantidade")
        ),
        sa.CheckConstraint(f"status IN ({ESTADOS_ENTRADA})", name=op.f(f"ck_{ENTRADAS}_status")),
        schema=SCHEMA,
    )
    op.execute(f"ALTER SEQUENCE {SCHEMA}.{SEQ} OWNED BY {SCHEMA}.{ENTRADAS}.numero")
    op.create_index("ix_estoque_entradas_sku", ENTRADAS, [sa.text("lower(sku)")], schema=SCHEMA)
    # O worker só olha as entradas em andamento: índice parcial pequeno.
    op.create_index(
        "ix_estoque_entradas_andamento",
        ENTRADAS,
        ["status"],
        schema=SCHEMA,
        postgresql_where=sa.text("status IN ('pendente','enviando','conferindo')"),
    )
    op.create_index(
        "ix_estoque_entradas_bling_product_id", ENTRADAS, ["bling_product_id"], schema=SCHEMA
    )

    op.create_table(
        TROCAS,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idem_key", sa.Text(), nullable=False),
        sa.Column("tag", sa.Text(), nullable=False),
        sa.Column("pedido", sa.Text(), nullable=False),
        _criado_por(TROCAS),
        sa.Column("criado_por_nome", sa.Text(), nullable=True),
        sa.Column("concluido_em", sa.DateTime(timezone=True), nullable=True),
        *_datas(),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TROCAS}")),
        sa.UniqueConstraint("idem_key", name=op.f(f"uq_{TROCAS}_idem_key")),
        schema=SCHEMA,
    )
    op.create_table(
        ITENS,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "troca_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.{TROCAS}.id",
                ondelete="CASCADE",
                name=op.f(f"fk_{ITENS}_troca_id_{TROCAS}"),
            ),
            nullable=False,
        ),
        sa.Column("ordem", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sku_antigo", sa.Text(), nullable=False),
        sa.Column("sku_novo", sa.Text(), nullable=True),
        sa.Column("bling_product_id", sa.BigInteger(), nullable=True),
        sa.Column("estado", sa.Text(), nullable=False, server_default=sa.text("'pendente'")),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column("tentativa_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("concluido_at", sa.DateTime(timezone=True), nullable=True),
        *_datas(),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{ITENS}")),
        sa.CheckConstraint(f"estado IN ({ESTADOS_ITEM})", name=op.f(f"ck_{ITENS}_estado")),
        schema=SCHEMA,
    )
    op.create_index(op.f(f"ix_{ITENS}_troca_id"), ITENS, ["troca_id"], schema=SCHEMA)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")  # ver o upgrade
    op.drop_table(ITENS, schema=SCHEMA)
    op.drop_table(TROCAS, schema=SCHEMA)
    op.drop_table(ENTRADAS, schema=SCHEMA)  # a sequência sai junto (OWNED BY)
    op.execute(f"DROP SEQUENCE IF EXISTS {SCHEMA}.{SEQ}")
