# ruff: noqa: E501
"""Devolução → chamado automático no Mercado Livre com fotos — Eduardo, 2026-09-04.

"Todos esses motivos aí (Bloqueado, Golpe, Item faltando, Não recebido,
Danificado), se for adicionado lá, vai abrir o chamado automático … vai ter
foto sim e vídeo". O chamado de devolução no ML é a "revisão da devolução com
problema" (POST /post-purchase/v1/returns/{return_id}/return-review, motivo
SRF2–SRF7); Danificado e Produto diferente EXIGEM foto.

- `devolutions.motivo_ml`: sub-motivo escolhido pelo operador quando o motivo
  é "Golpe" ("depende"): SRF4 produto diferente | SRF5 pacote vazio | SRF6 outro.
- `devolucao_anexo`: fotos/vídeos anexados à linha da devolução. Foto vai pro
  ML (`ml_file_name` = nome devolvido pelo upload, evita reenviar); vídeo só
  fica guardado — a API do ML aceita JPG/PNG/PDF/TXT até 5 MB.

Revision ID: 0243_devolucao_anexo_chamado_ml
Revises: 0242_devolucao_entrada_manual
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0243_devolucao_anexo_chamado_ml"
down_revision: str | None = "0242_devolucao_entrada_manual"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.add_column("devolutions", sa.Column("motivo_ml", sa.Text(), nullable=True))
    op.create_table(
        "devolucao_anexo",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "devolution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.devolutions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("blob", sa.LargeBinary(), nullable=False),
        sa.Column("ml_file_name", sa.Text(), nullable=True),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_devolucao_anexo_devolution_id", "devolucao_anexo", ["devolution_id"], schema=SCHEMA
    )


def downgrade() -> None:
    op.execute(f'SET search_path TO "{SCHEMA}"')
    op.drop_index("ix_devolucao_anexo_devolution_id", table_name="devolucao_anexo", schema=SCHEMA)
    op.drop_table("devolucao_anexo", schema=SCHEMA)
    op.drop_column("devolutions", "motivo_ml")
