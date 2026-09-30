"""atendimento_canais: lojas lidas pelo robô do Mac mini (Temu, AliExpress)

Eduardo, 30/09/2026: "robô que deixa as 5 abertas no AdsPower e fica lendo se
tem mensagem". Temu e AliExpress não têm API de chat; um robô no Mac mini
mantém um perfil do AdsPower por loja com a lista de conversas do Seller
Center aberta, só ESCUTA o que a página já recebe e repassa ao DaVinci
(POST /api/atendimento/robo/*). A resposta é dada pela pessoa no Seller
Center.

Por que migration (e não uma integração por loja do robô):
  • `atendimento_canais.integration_id` era NOT NULL com FK para
    `integrations`. Criar uma integração para cada loja do robô poria a loja
    na mira de TODO worker que percorre `integrations` (~80 consultas: auto
    link, renovação de token, push de preço, vigia, a tela Integrações com o
    "testar conexão" → factory → `TemuClient` com credencial vazia) — e
    `aliexpress` nem existe no enum `integration_platform` (seria migration do
    mesmo jeito, e mais larga).
  • Sem canal, a loja do robô não teria modo (observar/copiloto), saúde da
    leitura nem entraria no cron da IA (que junta a conversa com o canal para
    saber o modo).

O que muda (só aditivo, nada existente é tocado):
  • `integration_id` passa a aceitar NULL — só no canal do robô;
  • `robo_perfil_id` (o perfil do AdsPower), UNIQUE: um canal por perfil;
  • CHECK: todo canal tem integração OU perfil do robô;
  • índice único PARCIAL `uq_atendimento_conversas_robo` (canal_id,
    externo_id) nas conversas SEM integração e COM canal — a idempotência da
    conversa do robô no banco (o UNIQUE com integration_id não vale com NULL).
    A Amazon sem conta identificada (integração e canal NULL) fica de fora.

O downgrade APAGA os canais do robô (não há como voltar ao NOT NULL com
eles); as conversas ficam, com `canal_id` NULL (FK SET NULL), como a de uma
loja desconectada.

Revision ID: 0347_atendimento_robo
Revises: 0346_atendimento
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0347_atendimento_robo"
down_revision: str | None = "0346_atendimento"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    # ALTER TABLE pega AccessExclusiveLock nas duas tabelas com a api
    # rodando: sem teto, uma transação longa (a rodada do sync) faria a
    # migration esperar e enfileirar todo mundo atrás dela.
    op.execute("SET lock_timeout = '3s'")

    op.alter_column("atendimento_canais", "integration_id", nullable=True, schema=SCHEMA)
    op.add_column(
        "atendimento_canais",
        sa.Column("robo_perfil_id", sa.String(64), nullable=True),
        schema=SCHEMA,
    )
    op.create_unique_constraint(
        "uq_atendimento_canais_robo_perfil_id",
        "atendimento_canais",
        ["robo_perfil_id"],
        schema=SCHEMA,
    )
    op.create_check_constraint(
        # Nome final (convenção ck_<tabela>_<nome>), sem passar de novo por ela.
        op.f("ck_atendimento_canais_integracao_ou_robo"),
        "atendimento_canais",
        "integration_id IS NOT NULL OR robo_perfil_id IS NOT NULL",
        schema=SCHEMA,
    )
    op.create_index(
        "uq_atendimento_conversas_robo",
        "atendimento_conversas",
        ["canal_id", "externo_id"],
        unique=True,
        postgresql_where=sa.text("integration_id IS NULL AND canal_id IS NOT NULL"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    op.drop_index(
        "uq_atendimento_conversas_robo", table_name="atendimento_conversas", schema=SCHEMA
    )
    # Os canais do robô não têm integração: sem eles não dá para voltar ao
    # NOT NULL. As conversas ficam (canal_id vira NULL pela FK SET NULL).
    op.execute(f'DELETE FROM "{SCHEMA}".atendimento_canais WHERE integration_id IS NULL')  # noqa: S608 — SCHEMA é constante daqui
    op.drop_constraint(
        op.f("ck_atendimento_canais_integracao_ou_robo"),
        "atendimento_canais",
        type_="check",
        schema=SCHEMA,
    )
    op.drop_constraint(
        "uq_atendimento_canais_robo_perfil_id",
        "atendimento_canais",
        type_="unique",
        schema=SCHEMA,
    )
    op.drop_column("atendimento_canais", "robo_perfil_id", schema=SCHEMA)
    op.alter_column("atendimento_canais", "integration_id", nullable=False, schema=SCHEMA)
