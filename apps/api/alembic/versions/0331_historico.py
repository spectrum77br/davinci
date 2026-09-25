"""historico: registro de tudo que as PESSOAS mudam no DaVinci

Eduardo, 25/09/2026: "uma lista de histórico do que está sendo mudado no
davinci, por exemplo alterou a tabela de preços, tipo logs para irmos vendo as
movimentações". Só mudanças de pessoas (robôs fora) e só ele vê.

- historico_evento: um pedido de uma pessoa (quem, quando, tela, ação).
- historico_alteracao: cada linha mudada, com antes e depois, gravada pelo
  gatilho `historico_captura` (app/historico/sql.py).
- historico_acesso: quem pode ver. Nasce só com o heisenberg (Eduardo), que
  também é o único que pode liberar outras pessoas.

O gatilho vai em todas as tabelas de negócio, mas só grava quando o pedido
marcou a transação (`davinci.ator`). Robô não marca: nada é gravado e o custo
é uma checagem por linha. Esta migration cria só as tabelas e as funções; o
gatilho é posto tabela por tabela, cada uma na sua transação curta, por
`python -m app.historico.instalar` e pelo `historico_manutencao` do worker
(testado numa cópia da estrutura de produção: o autocommit_block do alembic
não funciona com o env assíncrono daqui).

Revision ID: 0331_historico
Revises: 0330_pedido_outra_pessoa
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op
from app.historico import sql as hsql

revision: str = "0331_historico"
down_revision: str | None = "0330_pedido_outra_pessoa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "historico_evento",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("req_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("ator_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("ator_nome", sa.Text(), nullable=True),
        sa.Column("via", sa.Text(), server_default="tela", nullable=False),
        sa.Column("metodo", sa.Text(), nullable=False),
        sa.Column("rota", sa.Text(), nullable=False),
        sa.Column("caminho", sa.Text(), nullable=False),
        sa.Column("tela", sa.Text(), nullable=True),
        sa.Column("pagina", sa.Text(), nullable=True),
        sa.Column("acao", sa.Text(), nullable=True),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("ip", sa.Text(), nullable=True),
        sa.Column("corpo", pg.JSONB(), nullable=True),
        sa.Column("n_alteracoes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("itens_texto", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["ator_id"], [f"{SCHEMA}.users.id"],
            name="fk_historico_evento_ator_id_users", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_historico_evento"),
        sa.UniqueConstraint("req_id", name="uq_historico_evento_req_id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_historico_evento_criado_em", "historico_evento", [sa.text("criado_em DESC")],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_historico_evento_ator", "historico_evento", ["ator_id", sa.text("criado_em DESC")],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_historico_evento_tela", "historico_evento", ["tela", sa.text("criado_em DESC")],
        schema=SCHEMA,
    )

    op.create_table(
        "historico_alteracao",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("req_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("ator_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("tabela", sa.Text(), nullable=False),
        sa.Column("operacao", sa.Text(), nullable=False),
        sa.Column("registro_id", sa.Text(), nullable=True),
        sa.Column("rotulo", sa.Text(), nullable=True),
        sa.Column("item", sa.Text(), nullable=True),
        sa.Column("antes", pg.JSONB(), nullable=True),
        sa.Column("depois", pg.JSONB(), nullable=True),
        sa.Column("ident", pg.JSONB(), nullable=True),
        sa.Column("app", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_historico_alteracao"),
        schema=SCHEMA,
    )
    op.create_index("ix_historico_alteracao_req", "historico_alteracao", ["req_id"], schema=SCHEMA)
    op.create_index(
        "ix_historico_alteracao_registro", "historico_alteracao", ["tabela", "registro_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_historico_alteracao_criado_em", "historico_alteracao", ["criado_em"], schema=SCHEMA
    )

    op.create_table(
        "historico_acesso",
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("pode_gerenciar", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("liberado_por", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("liberado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], [f"{SCHEMA}.users.id"],
            name="fk_historico_acesso_user_id_users", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["liberado_por"], [f"{SCHEMA}.users.id"],
            name="fk_historico_acesso_liberado_por_users", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_historico_acesso"),
        schema=SCHEMA,
    )
    # Só o Eduardo (heisenberg), e só ele libera outras pessoas.
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.historico_acesso (user_id, pode_gerenciar)
        SELECT id, true FROM {SCHEMA}.users
         WHERE lower(name) = 'heisenberg' AND role = 'admin'
        ON CONFLICT (user_id) DO NOTHING
        """
    )

    for comando in hsql.funcoes(SCHEMA):
        op.execute(comando)
    # O gatilho NÃO é posto aqui: numa transação só, o lock de cada tabela
    # ficaria preso até o fim da migration, travando os robôs. Quem põe é
    # `python -m app.historico.instalar` (no deploy) e o `historico_manutencao`
    # do worker (ao subir e todo dia), uma tabela por vez, em transação curta.


def downgrade() -> None:
    conn = op.get_bind()
    tabelas = [
        r[0]
        for r in conn.execute(
            sa.text(
                """
                SELECT c.relname FROM pg_trigger t
                  JOIN pg_class c ON c.oid = t.tgrelid
                  JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE n.nspname = :schema AND t.tgname = :gatilho AND NOT t.tgisinternal
                   AND t.tgparentid = 0
                """
            ),
            {"schema": SCHEMA, "gatilho": hsql.NOME_GATILHO},
        )
    ]
    for tabela in tabelas:
        op.execute(f'DROP TRIGGER IF EXISTS {hsql.NOME_GATILHO} ON {SCHEMA}."{tabela}"')
    for f in (
        "historico_captura()",
        "historico_valor(text, jsonb, boolean)",
        "historico_limpa_json(jsonb)",
        "historico_limpa_texto(text)",
        "historico_e_segredo(text)",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.{f}")
    op.drop_table("historico_acesso", schema=SCHEMA)
    op.drop_table("historico_alteracao", schema=SCHEMA)
    op.drop_table("historico_evento", schema=SCHEMA)
