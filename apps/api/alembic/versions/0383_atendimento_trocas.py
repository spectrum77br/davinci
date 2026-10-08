"""atendimento: troca de produto no pedido em "Aguardando Cancelamento" (item 4, fase 4c)

Eduardo, 07/10/2026 (decisões sobre docs/item4/desenho.md §3): o pedido que
caiu em 83955 por falta de estoque pode trocar o item por um parecido com
estoque e voltar para Em aberto. Nível 0 (o mesmo produto em outro lote) o
robô troca sozinho, sem aceite; níveis 1 e 2 (outra cor, outro modelo) só por
clique de pessoa, com a caixinha "o cliente aceitou" — a prova do aceite é
opcional (mensagem da conversa, resposta colada do Duoke ou "declarado").

O que muda (só aditivo):
  • `atendimento_trocas`: uma linha por troca — o antes e o depois, o aceite,
    o que havia antes (pino da Margem, marca da NF), o `estado` (a troca NÃO é
    atômica: cada passo no Bling faz commit) e os `passos`. CHECK do estado e
    da fonte do aceite; UNIQUE da `idem_key`; índice único PARCIAL de uma
    troca aberta por pedido (`uq_atendimento_trocas_aberta`).

Fica FORA do Histórico (`historico/sql.EXCLUIDAS`, no mesmo commit): guarda
texto de comprador (`aceite_texto`) e já é a própria trilha da troca.

`lock_timeout` de 3 s, como as outras do atendimento (as FKs novas pegam
ShareRowExclusiveLock em users, conversas e mensagens).
`tests/test_atendimento_migration.py` roda esta num schema descartável e
compara com o model.

O código aguenta a tabela ainda não existir (um push no main pode ser
publicado antes do alembic): o painel e a lista leem a troca aberta num
SAVEPOINT, e as rotas de escrita nascem desligadas (`ATENDIMENTO_TROCA_ATIVA`).

O downgrade apaga a tabela (as trocas registradas se perdem; o que foi feito
no Bling fica, com a linha "TROCA ..." nas Observações do pedido).

NO REBASE (08/10/2026): o origin/main já tem 0379–0382 em cima da 0378 — esta
vira `0383_atendimento_trocas` com `down_revision` = a ponta do main na hora
(`alembic heads`; hoje `0382_conferencia_plataformas`), e o
`test_atendimento_migration.py` junto. Duas pontas = `alembic upgrade head`
para no deploy (o `test_alembic_tem_uma_ponta_so` acusa antes).

Revision ID: 0383_atendimento_trocas
Revises: 0382_conferencia_plataformas
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0383_atendimento_trocas"
down_revision: str | None = "0382_conferencia_plataformas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
TROCAS = "atendimento_trocas"


def _uuid(nome: str, nullable: bool = True) -> sa.Column:
    return sa.Column(nome, pg.UUID(as_uuid=True), nullable=nullable)


def _quando(nome: str, nullable: bool = True, agora: bool = False) -> sa.Column:
    return sa.Column(
        nome,
        sa.DateTime(timezone=True),
        server_default=sa.text("now()") if agora else None,
        nullable=nullable,
    )


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")

    op.create_table(
        TROCAS,
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("pedido_bling", sa.Text(), nullable=False),
        sa.Column("bling_id", sa.BigInteger(), nullable=False),
        sa.Column("numeroloja", sa.Text(), nullable=True),
        sa.Column("plataforma", sa.String(16), nullable=True),
        _uuid("conversa_id"),
        sa.Column("motivo_codigo", sa.String(24), nullable=False),
        sa.Column("sku_antigo", sa.Text(), nullable=False),
        sa.Column("sku_novo", sa.Text(), nullable=False),
        sa.Column("produto_novo_id", sa.BigInteger(), nullable=False),
        sa.Column("descricao_nova", sa.Text(), nullable=True),
        sa.Column("quantidade", sa.Integer(), nullable=False),
        sa.Column("valor_unitario", sa.Numeric(14, 2), nullable=True),
        sa.Column("nivel", sa.SmallInteger(), nullable=False),
        sa.Column("automatica", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("custo_antigo", sa.Numeric(14, 4), nullable=True),
        sa.Column("custo_novo", sa.Numeric(14, 4), nullable=True),
        sa.Column("saldo_ao_vivo", sa.Numeric(14, 2), nullable=True),
        sa.Column("aceite_fonte", sa.String(16), nullable=True),
        _uuid("mensagem_aceite_id"),
        sa.Column("aceite_texto", sa.Text(), nullable=True),
        _quando("aceite_em"),
        _uuid("oferta_mensagem_id"),
        sa.Column("pino_anterior", sa.Text(), nullable=True),
        sa.Column("nf_status_anterior", sa.Text(), nullable=True),
        sa.Column("nf_erro_anterior", sa.Text(), nullable=True),
        sa.Column("estado", sa.String(16), nullable=False),
        sa.Column("codigo_erro", sa.String(48), nullable=True),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column("passos", pg.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        _uuid("idem_key", nullable=False),
        _quando("em_execucao_ate"),
        _uuid("criado_por"),
        sa.Column("criado_por_nome", sa.Text(), nullable=False),
        _quando("concluida_em"),
        _quando("created_at", nullable=False, agora=True),
        _quando("updated_at", nullable=False, agora=True),
        sa.CheckConstraint(
            "estado IN ('iniciada', 'item_trocado', 'em_atendido', 'em_aberto',"
            " 'nf_liberada', 'concluida', 'abortada', 'incerta')",
            # Nome final (`op.f`): sem passar de novo pela convenção ck_<tabela>_<nome>.
            name=op.f("ck_atendimento_trocas_estado"),
        ),
        sa.CheckConstraint(
            "aceite_fonte IS NULL OR aceite_fonte IN ('davinci', 'duoke', 'declarado')",
            name=op.f("ck_atendimento_trocas_aceite_fonte"),
        ),
        sa.ForeignKeyConstraint(
            ["conversa_id"],
            [f"{SCHEMA}.atendimento_conversas.id"],
            name="fk_atendimento_trocas_conversa_id_atendimento_conversas",
            ondelete="SET NULL",
        ),
        # Nome à mão: pela convenção ficaria a 1 caractere do teto do Postgres.
        sa.ForeignKeyConstraint(
            ["mensagem_aceite_id"],
            [f"{SCHEMA}.atendimento_mensagens.id"],
            name="fk_atendimento_trocas_mensagem_aceite",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["criado_por"],
            [f"{SCHEMA}.users.id"],
            name="fk_atendimento_trocas_criado_por_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_atendimento_trocas"),
        sa.UniqueConstraint("idem_key", name="uq_atendimento_trocas_idem_key"),
        schema=SCHEMA,
    )
    op.create_index("ix_atendimento_trocas_pedido_bling", TROCAS, ["pedido_bling"], schema=SCHEMA)
    # UMA troca aberta por pedido: dois cliques (ou a pessoa e o robô), um INSERT só.
    op.create_index(
        "uq_atendimento_trocas_aberta",
        TROCAS,
        ["pedido_bling"],
        unique=True,
        postgresql_where=sa.text("estado NOT IN ('concluida', 'abortada')"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    # DROP TABLE leva junto os índices e as FKs.
    op.drop_table(TROCAS, schema=SCHEMA)
