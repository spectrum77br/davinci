"""users.video_trava_tags — tags cujas solicitações de vídeo TRANCAM a aba Pedidos do usuário

Vinicius, 17/09/2026 (depois de testar a trava de vídeo como cairo.sa, que tem
as 11 tags e ficou travado "devido a todos os produtos"): cada login trava só
pelas tags da própria equipe, escolhidas na tela de Usuários. Vazio = nunca
tranca, só vê a lista como aviso (londres, israel, marrocos, churchill).

Backfill combinado com ele: azeroth→ra, coreia→sp, espanha→ci, paris→pi,
thatcher→us, factor→mala+eletro+cd, cairo sa→só sa. Chaveado por e-mail; um
e-mail que não exista simplesmente não recebe nada.
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0294_users_video_trava_tags"
down_revision: str | None = "0293_devolucao_video"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INICIAL: dict[str, list[str]] = {
    "ra_geral@tutamail.com": ["ra"],  # azeroth
    "spgeral@tutamail.com": ["sp"],  # coreia
    "ci_geral@tutamail.com": ["ci"],  # espanha
    "geral.pi@tutamail.com": ["pi"],  # paris
    "sthevem7@tuta.com": ["us"],  # thatcher
    "malas.geral@tutamail.com": ["mala", "eletro", "cd"],  # factor
    "sa.geral@tutamail.com": ["sa"],  # cairo sa
}


def upgrade() -> None:
    op.add_column("users", sa.Column("video_trava_tags", postgresql.JSONB(), nullable=True))
    for email, tags in _INICIAL.items():
        op.execute(
            sa.text(
                "UPDATE users SET video_trava_tags = CAST(:tags AS jsonb) "
                "WHERE lower(email) = :email"
            ).bindparams(tags=json.dumps(tags), email=email)
        )


def downgrade() -> None:
    op.drop_column("users", "video_trava_tags")
