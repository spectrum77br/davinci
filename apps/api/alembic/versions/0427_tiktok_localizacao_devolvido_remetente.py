"""logistica.localizacao TikTok — "returned to the sender" em português

Vinicius, 23/10/2026: a Localização do 299162 apareceu em inglês ("Package
was returned to the sender by the shipping carrier at Piracicaba.", action
80101) — frase que a tabela de `logistica_rules.tiktok_localizacao_pt` ainda
não tinha, e o fallback deixa o texto original. A tabela ganhou a frase;
esta migração reescreve o que já está gravado, igual à 0298 (só linhas
TikTok sem `localizacao_at`: com carimbo é evento do 17track, já em PT).

Imprime o que reescreveu e os textos que AINDA ficaram em inglês, pra ler
no /var/log/davinci-deploy.log.

Downgrade é no-op, como na 0298.
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0427_tiktok_localizacao_devolvido_remetente"
down_revision: str | None = "0426_conciliacao_zera_custo_devolvidos_cairo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
_PLATAFORMAS_TIKTOK = ["tik tok", "tiktok", "tiktok shop"]
_INGLES = re.compile(
    r"\b(package|order|arrived|departed|facility|delivered|returned|carrier|sender)\b",
    re.IGNORECASE,
)


def _plats() -> sa.BindParameter:
    return sa.bindparam("plats", value=_PLATAFORMAS_TIKTOK, type_=sa.ARRAY(sa.Text()))


def upgrade() -> None:
    from app.services.logistica_rules import tiktok_localizacao_pt

    bind = op.get_bind()
    textos = bind.execute(
        sa.text(
            f"SELECT localizacao, count(*) FROM {SCHEMA}.logistica "  # noqa: S608
            "WHERE lower(trim(plataforma)) = ANY(:plats) "
            "AND localizacao_at IS NULL AND localizacao IS NOT NULL "
            "GROUP BY localizacao"
        ).bindparams(_plats())
    ).all()
    for en, n in textos:
        pt = tiktok_localizacao_pt(en)
        if not pt or pt == en:
            if _INGLES.search(en):
                print(f"0427 ainda em inglês ({n}): {en!r}")
            continue
        bind.execute(
            sa.text(
                f"UPDATE {SCHEMA}.logistica SET localizacao = :pt "  # noqa: S608
                "WHERE lower(trim(plataforma)) = ANY(:plats) "
                "AND localizacao_at IS NULL AND localizacao = :en"
            ).bindparams(_plats(), pt=pt, en=en)
        )
        print(f"0427 traduziu ({n}): {en!r} -> {pt!r}")


def downgrade() -> None:
    pass
