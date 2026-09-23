"""redes_sociais.adspower_user_id — o perfil que o executor local abre

Eduardo, 23/09/2026. O app do TikTok foi recusado DUAS vezes: na auditoria do
Content Posting API e na Business API. A rota de rascunho (inbox), que parecia
saída, exige o mesmo formulário — conferido no portal.

Sem API, a publicação no TikTok passa pelo navegador logado no AdsPower, que
roda no Mac do Eduardo e não no servidor. Para o executor saber QUAL perfil
abrir, a conta precisa carregar o id dele.

Uma conta por perfil, sempre. Duas contas de marcas diferentes no mesmo
navegador desfaz o isolamento pelo qual o AdsPower é pago — e é assim que as
duas caem juntas.

NULL nas contas publicadas por API (Instagram, Facebook, YouTube): lá quem
autentica é o token, e perfil de navegador não entra na conversa.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0309_rede_social_adspower"
down_revision: str | None = "0308_chamado_leitura_robo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    op.add_column(
        "redes_sociais",
        sa.Column("adspower_user_id", sa.String(64), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("redes_sociais", "adspower_user_id", schema=SCHEMA)
