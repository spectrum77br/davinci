"""denuncia_relatorios: manda de novo o relatório de 05/10 no formato curto — só dado

Vinicius, 06/10/2026, depois do 1º envio (08:04): "chegou no Threema porém muito grande. coloca só
o resumo pequeno e manda o Excel". A mensagem virou o resumo geral + o link que baixa o Excel
(services/denuncia_relatorio_threema). Tira o carimbo do dia 05/10 pra o worker (que roda no
restart do deploy) mandar de novo no formato novo — vale só se o deploy for ainda em 06/10 (o
worker só manda o de ontem).

Revision ID: 0374_denuncia_relatorio_reenvia_05_10
Revises: 0373_denuncia_relatorio_threema
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0374_denuncia_relatorio_reenvia_05_10"
down_revision: str | None = "0373_denuncia_relatorio_threema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE davinci.denuncia_relatorios SET threema_enviado_em = NULL WHERE dia = '2026-10-05'"
    )


def downgrade() -> None:
    pass
