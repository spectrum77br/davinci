"""denuncia_casos_extra — status do caso trocado à mão + data do envio ao jurídico

Vinicius, 06/10/2026 (aba Casos): "poderia clicar no status e conseguir trocar… esse caso 2 já foi
enviado ao jurídico". O status sai dos fatos (compra, entrega, envio ao advogado); quando o fato
não chegou ao sistema (compra do TikTok, que o robô não lê; Shopee marcou entregue e não chegou;
envio ao advogado sem registro), a pessoa escolhe o status e ele vale por cima do automático.
`juridico_data` = quando o caso foi ao advogado, se o sistema do mini não tem a data.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0375_denuncia_caso_status_manual"
down_revision: str | None = "0374_logistica_status_localizacao_contem"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column("denuncia_casos_extra", sa.Column("status_manual", sa.Text()), schema=SCHEMA)
    op.add_column("denuncia_casos_extra", sa.Column("status_manual_por", sa.Text()), schema=SCHEMA)
    op.add_column(
        "denuncia_casos_extra",
        sa.Column("status_manual_em", sa.DateTime(timezone=True)),
        schema=SCHEMA,
    )
    op.add_column("denuncia_casos_extra", sa.Column("juridico_data", sa.Date()), schema=SCHEMA)


def downgrade() -> None:
    for col in ("juridico_data", "status_manual_em", "status_manual_por", "status_manual"):
        op.drop_column("denuncia_casos_extra", col, schema=SCHEMA)
