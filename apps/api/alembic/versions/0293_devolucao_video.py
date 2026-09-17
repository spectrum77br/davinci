"""devolucao_rastreio.video_* — solicitação do vídeo da expedição (Devoluções ↔ Controle de Estoque)

Vinicius, 17/09/2026: nas abas Acompanhamento e Fraude, entre Observação e
Lançada, "um campo Vídeo e um botão solicitar. Quando alguém apertar, gera
uma solicitação de vídeo no painel Controle de Estoque": a equipe do SKU
(mesma regra de tag da aba Pedidos) só acessa a aba Pedidos depois de colar
o link do vídeo do pedido. O link volta pra coluna Vídeo; apagar o link (com
motivo) devolve a solicitação pra equipe refazer; "não tenho o vídeo" (com
motivo) responde sem link.

  • `video_solicitado_em/_por`: última solicitação (pendente enquanto
    `video_enviado_em` for NULL).
  • `video_enviado_em/_por`: resposta da equipe (link OU sem vídeo).
  • `video_link`: link enviado pela equipe.
  • `video_sem_motivo`: motivo de "não tenho o vídeo".
  • `video_refazer_motivo`: motivo dado por quem apagou o link — aparece
    pra equipe como "refazer: ...".
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0293_devolucao_video"
down_revision: str | None = "0292_companies_responsavel_nome"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLS = (
    "video_link",
    "video_solicitado_em",
    "video_solicitado_por",
    "video_enviado_em",
    "video_enviado_por",
    "video_sem_motivo",
    "video_refazer_motivo",
)


def upgrade() -> None:
    op.add_column("devolucao_rastreio", sa.Column("video_link", sa.Text(), nullable=True))
    op.add_column(
        "devolucao_rastreio",
        sa.Column("video_solicitado_em", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "devolucao_rastreio",
        sa.Column(
            "video_solicitado_por",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "devolucao_rastreio",
        sa.Column("video_enviado_em", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "devolucao_rastreio",
        sa.Column(
            "video_enviado_por",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("devolucao_rastreio", sa.Column("video_sem_motivo", sa.Text(), nullable=True))
    op.add_column(
        "devolucao_rastreio", sa.Column("video_refazer_motivo", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    for col in reversed(_COLS):
        op.drop_column("devolucao_rastreio", col)
