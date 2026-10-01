"""denuncia_provas.arquivo_pedido_em — prova aberta sob demanda

Vinicius, 01/10/2026: "quando clica nessa parte das provas não está abrindo
nada, eu precisava ver as imagens, vídeos". Os arquivos ficam no Mac mini e
no MEGA (3,9 GB e crescendo; o servidor tem 11 GB livres), então o DaVinci
não guarda todos: quem clica pede, o mini vê o pedido em segundos e manda só
aquele arquivo. O DaVinci guarda os abertos recentemente até um teto e apaga
os mais antigos.

`arquivo_pedido_em` marca o pedido; o envio do arquivo limpa.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0349_denuncia_prova_pedida"
down_revision: str | None = "0348_denuncia_robo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "denuncia_provas",
        sa.Column("arquivo_pedido_em", sa.DateTime(timezone=True)),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("denuncia_provas", "arquivo_pedido_em", schema=SCHEMA)
