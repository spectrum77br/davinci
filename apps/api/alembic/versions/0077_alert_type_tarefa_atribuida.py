"""alert_type: add 'tarefa_atribuida'

Revision ID: 0077_alert_type_tarefa_atribuida
Revises: 0076_vw_conciliacao_margens_saldo_final_reembolso
Create Date: 2026-05-21

Adds a new variant to the davinci.alert_type Postgres enum so the
Tarefas router can emit `emit_alert(type=AlertType.TAREFA_ATRIBUIDA, ...)`
when an admin creates a task for a user or reassigns one to a new
responsible. Without the new enum value, the INSERT into davinci.alerts
fails with `invalid input value for enum`.

Idempotent via IF NOT EXISTS (PG12+ allows ALTER TYPE ADD VALUE inside
a transaction). Downgrade is a no-op — Postgres has no DROP VALUE for
enums; removing it would require recreating the type and rewriting
every row that references it, which isn't worth the complexity.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0077_alert_type_tarefa_atribuida"
down_revision: str | None = "0076_vw_conciliacao_margens_saldo_final_reembolso"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(
        f"ALTER TYPE \"{SCHEMA}\".alert_type ADD VALUE IF NOT EXISTS 'tarefa_atribuida'"
    )


def downgrade() -> None:
    # PG has no DROP VALUE; leave the enum value in place.
    pass
