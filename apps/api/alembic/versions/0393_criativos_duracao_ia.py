"""criativos: duração/resolução e a IA de cada vídeo entregue; a agência que escreveu a ideia

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
  • `marketing_creative_files`: `ia_modelo` VARCHAR(64) e `ia_creditos`
    NUMERIC(10,2) — o gerador e o crédito gasto, que a agência informa no
    envio se quiser (opcional de propósito: "não precisa tornar obrigatório").
    No ARQUIVO, e não na linha do criativo: o reenvio pela aba Entregas anexa
    vídeo novo à linha que já tem vídeo, e com a IA na linha o vídeo antigo
    passava a contar com a IA (e os créditos) do novo no relatório.
  • `marketing_roteiros.criado_pela_equipe` VARCHAR(64) — a agência por cuja
    porta a ideia NOVA entrou (`POST /api/portal/roteiros`, escrita pela
    pessoa no portal ou pelo Claude conectado a ele). NULL = escrita pela casa
    no DaVinci. Sem isto, a ideia de terceiro entrava na aba Roteiros idêntica
    ao briefing da casa.

Só colunas novas e anuláveis, sem padrão: o ADD COLUMN não reescreve a tabela
nem segura trava por mais que um instante. `lock_timeout` de 10 s como as
outras: se o robô de postagem estiver com a linha presa, a migration falha
inteira e é só rodar de novo. O downgrade derruba as seis colunas (e o que
foi medido nelas — o backfill refaz).

Era a 0392 até 13/10/2026: a `0392_marketing_conta_conferencia` entrou no main
(e em produção) antes, com a mesma base, e esta passou a vir depois dela para
o alembic continuar com uma ponta só.

Revision ID: 0393_criativos_duracao_ia
Revises: 0392_marketing_conta_conferencia
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0393_criativos_duracao_ia"
down_revision: str | None = "0392_marketing_conta_conferencia"
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
        "marketing_creative_files",
        sa.Column("ia_modelo", sa.String(64), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "marketing_creative_files",
        sa.Column("ia_creditos", sa.Numeric(10, 2), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "marketing_roteiros",
        sa.Column("criado_pela_equipe", sa.String(64), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.drop_column("marketing_roteiros", "criado_pela_equipe", schema=SCHEMA)
    op.drop_column("marketing_creative_files", "ia_creditos", schema=SCHEMA)
    op.drop_column("marketing_creative_files", "ia_modelo", schema=SCHEMA)
    op.drop_column("marketing_creative_files", "altura", schema=SCHEMA)
    op.drop_column("marketing_creative_files", "largura", schema=SCHEMA)
    op.drop_column("marketing_creative_files", "duracao_s", schema=SCHEMA)
