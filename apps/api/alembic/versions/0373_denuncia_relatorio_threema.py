"""denuncia_relatorios.threema_enviado_em + quem recebe o relatório (Cairo, harry potter, Roma)

Vinicius, 06/10/2026: "esse relatório consegue enviar pelo Threema para Cairo, Hary e Roma? todo
dia pode enviar". O worker manda o relatório de ontem às 7h (services/denuncia_relatorio_threema) e
carimba a linha do dia. A lista fica no cadastro `denuncia_relatorio` do Informar (botão "Quem
recebe o relatório" em Robô › Ocorrências) — já nasce com os três, pelos IDs do diretório do
Threema: cairo M5TT27JA, harry potter 9BH6R7HJ (usuários) e Roma VBS64V3S (contato avulso).
Se o cadastro já existir (alguém salvou pela tela antes), não mexe.

Revision ID: 0373_denuncia_relatorio_threema
Revises: 0372_londres_logistica_ver
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0373_denuncia_relatorio_threema"
down_revision: str | None = "0372_londres_logistica_ver"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
QUEM = "M5TT27JA,9BH6R7HJ,VBS64V3S"


def upgrade() -> None:
    op.add_column(
        "denuncia_relatorios",
        sa.Column("threema_enviado_em", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.execute(
        f'INSERT INTO "{SCHEMA}".threema_informar_config (id, contexto, recipients) '  # noqa: S608
        f"SELECT gen_random_uuid(), 'denuncia_relatorio', '{QUEM}' "
        f'WHERE NOT EXISTS (SELECT 1 FROM "{SCHEMA}".threema_informar_config '
        "WHERE contexto = 'denuncia_relatorio')"
    )


def downgrade() -> None:
    op.execute(
        f"DELETE FROM \"{SCHEMA}\".threema_informar_config WHERE contexto = 'denuncia_relatorio' "  # noqa: S608
        f"AND recipients = '{QUEM}'"
    )
    op.drop_column("denuncia_relatorios", "threema_enviado_em", schema=SCHEMA)
