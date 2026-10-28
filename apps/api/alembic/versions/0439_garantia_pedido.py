"""garantia_pedido — o Painel de Garantia mostra só quem PEDIU a garantia

Eduardo, 28/10/2026: "a garantia é só para o pessoal que solicita a garantia
do aparelho" — a 0434 pôs no Painel toda venda com NF de produto (5.994), e o
Resumo dava a divisão das vendas (Celular 77,8%), não a dos pedidos.

* `garantias`: `solicitada_em` / `solicitacao_origem` / `solicitacao_ref` (o
  pedido: mensagem do cliente, SAC do site, devolução por defeito, atendimento
  registrado, cadastro à mão ou a pessoa) e `nao_solicitada_em` /
  `nao_solicitada_por` (a pessoa disse "não é pedido de garantia": sai do
  Painel e o robô não marca de novo). A venda sem pedido NÃO se apaga: é o
  "vendidas" da porcentagem e a garantia fica pronta quando o cliente pedir.
* `garantia_log`: as ações `pedido_automatico`, `marcou_pedido`, `tirou_pedido`.
* Já marca aqui o que não precisa ler mensagem: a cadastrada à mão (desde o
  cadastro) e a que tem atendimento registrado (desde o 1º). O resto (mensagem,
  SAC, devolução) o robô marca (`garantia_pedido.marcar_pedidos`) — no deploy,
  rodar a carga antes de subir a tela.

Revision ID: 0439_garantia_pedido
Revises: 0438_mail_composer
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0439_garantia_pedido"
down_revision: str | None = "0438_mail_composer"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
SCHEMA = "davinci"

# Listas FIXAS (não importam a constante viva do modelo).
ACOES_ANTES = (
    "cadastrou",
    "alterou",
    "consultou",
    "registrou_atendimento",
    "recalculou",
    "buscou_cpf",
    "criou_automatico",
    "descartou",
    "reativou",
)
ACOES_DEPOIS = (*ACOES_ANTES, "pedido_automatico", "marcou_pedido", "tirou_pedido")
ORIGENS = ("mensagem", "sac", "devolucao", "atendimento", "manual", "pessoa")


def _lista(valores: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valores)


def upgrade() -> None:
    op.add_column(
        "garantias",
        sa.Column("solicitada_em", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "garantias", sa.Column("solicitacao_origem", sa.String(16), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "garantias", sa.Column("solicitacao_ref", sa.String(64), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "garantias",
        sa.Column("nao_solicitada_em", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "garantias",
        sa.Column(
            "nao_solicitada_por",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "solicitacao_origem_valida",
        "garantias",
        f"solicitacao_origem IS NULL OR solicitacao_origem IN ({_lista(ORIGENS)})",
        schema=SCHEMA,
    )

    op.drop_constraint("ck_garantia_log_acao_valida", "garantia_log", schema=SCHEMA)
    op.create_check_constraint(
        "acao_valida", "garantia_log", f"acao IN ({_lista(ACOES_DEPOIS)})", schema=SCHEMA
    )

    # O que já é pedido sem ler mensagem: o atendimento registrado (o 1º) e a
    # garantia cadastrada à mão (quem cadastrou atendia um pedido).
    op.execute(
        f"""
        UPDATE {SCHEMA}.garantias g
           SET solicitada_em = a.em, solicitacao_origem = 'atendimento',
               solicitacao_ref = a.id::text
          FROM (SELECT garantia_id, min(data_atendimento) AS em, min(id) AS id
                  FROM {SCHEMA}.garantia_atendimentos GROUP BY garantia_id) a
         WHERE a.garantia_id = g.id AND g.solicitada_em IS NULL
        """  # noqa: S608
    )
    op.execute(
        f"""
        UPDATE {SCHEMA}.garantias
           SET solicitada_em = criado_em, solicitacao_origem = 'manual'
         WHERE origem = 'manual' AND solicitada_em IS NULL
        """  # noqa: S608
    )


def downgrade() -> None:
    # O log só recebe linha nova (gatilho garantia_so_insercao); a volta tira
    # as ações novas com a marca de expurgo, senão a regra antiga não volta.
    op.execute("SET LOCAL davinci.garantia_expurgo = 'sim'")
    op.execute(
        f"DELETE FROM {SCHEMA}.garantia_log"  # noqa: S608
        f" WHERE acao IN ('pedido_automatico', 'marcou_pedido', 'tirou_pedido')"
    )
    op.drop_constraint("ck_garantia_log_acao_valida", "garantia_log", schema=SCHEMA)
    op.create_check_constraint(
        "acao_valida", "garantia_log", f"acao IN ({_lista(ACOES_ANTES)})", schema=SCHEMA
    )
    op.drop_constraint("ck_garantias_solicitacao_origem_valida", "garantias", schema=SCHEMA)
    for coluna in (
        "nao_solicitada_por",
        "nao_solicitada_em",
        "solicitacao_ref",
        "solicitacao_origem",
        "solicitada_em",
    ):
        op.drop_column("garantias", coluna, schema=SCHEMA)
