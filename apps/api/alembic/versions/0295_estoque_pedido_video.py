"""estoque_pedido_video — vídeo da embalagem por pedido (Controle de Estoque)

Vinicius, 18/09/2026: "todo pedido que a quantidade for mais de 1 pede vídeo
(ex.: 2 Apple Watch no mesmo pedido)". Botão antes de Obs na aba Pedidos
salva o link (sempre Google Drive); a aba Envios mostra por dia Feito /
Parcial / Não feito contando só os pedidos com mais de 1 unidade.

Chaveada por `pedido_bling` (bling_orders.numero — grão de pedido, como a
previsao_impressa). Sem FK pra bling_orders (uma linha por item, sem UNIQUE
em numero).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0295_estoque_pedido_video"
down_revision: str | None = "0294_users_video_trava_tags"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.create_table(
        "estoque_pedido_video",
        sa.Column("pedido_bling", sa.Text(), primary_key=True),
        sa.Column("link", sa.Text(), nullable=False),
        sa.Column(
            "salvo_por",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                f"{SCHEMA}.users.id",
                ondelete="SET NULL",
                name="fk_estoque_pedido_video_salvo_por_users",
            ),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("estoque_pedido_video", schema=SCHEMA)
