"""criativos: duração/resolução do vídeo entregue e a IA que o gerou

Marco, 13/10/2026, para o Studio DaVinci (o portal das agências): um relatório
dos vídeos que cada agência entregou — quantos, quantos minutos, a média e a
mediana, as faixas de duração e qual IA mais sai. Nada disso estava gravado:
o arquivo do criativo guardava nome, tipo, tamanho e sha256, e a única forma
de saber a duração era abrir vídeo por vídeo.

  • `marketing_creative_files`: `duracao_s` NUMERIC(8,2), `largura` e
    `altura` INT. Carimbadas no envio (portal e aba Criativos) pelo ffprobe
    que já mede o vídeo da Shopee. NULL = não medido; a medição nunca barra o
    envio. Os arquivos de antes desta migration ficam NULL até o
    `scripts/creatives_duracao_backfill.py` (de uma vez, idempotente).
  • `marketing_creatives`: `ia_modelo` VARCHAR(64) e `ia_creditos`
    NUMERIC(10,2) — o gerador e o crédito gasto, que a agência informa no
    envio se quiser (opcional de propósito: "não precisa tornar obrigatório").

Só colunas novas e anuláveis, sem padrão: o ADD COLUMN não reescreve a tabela
nem segura trava por mais que um instante. `lock_timeout` de 10 s como as
outras: se o robô de postagem estiver com a linha presa, a migration falha
inteira e é só rodar de novo. O downgrade derruba as cinco colunas (e o que
foi medido nelas — o backfill refaz).

Revision ID: 0392_criativos_duracao_ia
Revises: 0391_email_fora_do_chat
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0392_criativos_duracao_ia"
down_revision: str | None = "0391_email_fora_do_chat"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.add_column(
        "marketing_creative_files",
        sa.Column("duracao_s", sa.Numeric(8, 2), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "marketing_creative_files", sa.Column("largura", sa.Integer(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "marketing_creative_files", sa.Column("altura", sa.Integer(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "marketing_creatives", sa.Column("ia_modelo", sa.String(64), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "marketing_creatives",
        sa.Column("ia_creditos", sa.Numeric(10, 2), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.drop_column("marketing_creatives", "ia_creditos", schema=SCHEMA)
    op.drop_column("marketing_creatives", "ia_modelo", schema=SCHEMA)
    op.drop_column("marketing_creative_files", "altura", schema=SCHEMA)
    op.drop_column("marketing_creative_files", "largura", schema=SCHEMA)
    op.drop_column("marketing_creative_files", "duracao_s", schema=SCHEMA)
