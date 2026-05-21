"""devolutions table — controle manual de devolucoes por pedido

Espelha a estrutura de refunds mas com campos especificos de devolucao
(condicao do produto, motivo, custo de manutencao, tecnico, link de
abertura, flag devolver estoque). O reembolso aqui e boolean (refunds
usa double precision pra valor de reembolso).

Revision ID: 0078_devolutions
Revises: 0077_alert_type_tarefa_atribuida
Create Date: 2026-05-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0078_devolutions"
down_revision: str | None = "0077_alert_type_tarefa_atribuida"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.devolutions (
            id                  uuid NOT NULL DEFAULT gen_random_uuid(),
            data                timestamptz NULL,
            pedido_bling        text NULL,
            pedido_marketplace  text NULL,
            conta               text NOT NULL,
            sku                 text NULL,
            produtos            text NULL,
            custo_produto       double precision NULL,
            condicao_produto    text NULL,
            link_abertura       text NULL,
            reembolso           boolean NOT NULL DEFAULT false,
            motivo_devolucao    text NULL,
            custo_manutencao    double precision NULL,
            tecnico             text NULL,
            devolver_estoque    text NULL,
            observacao          text NULL,
            created_at          timestamptz NOT NULL DEFAULT now(),
            updated_at          timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_devolutions PRIMARY KEY (id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_devolutions_conta ON {SCHEMA}.devolutions (conta)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_devolutions_pedido_bling ON {SCHEMA}.devolutions (pedido_bling)"
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.devolutions")
