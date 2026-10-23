"""logistica.localizacao TikTok — "is being returned to the sender" em português

A 0427 (23/10/2026) imprimiu no log do deploy o único texto TikTok que
ainda ficou em inglês: "Package was unable to be delivered and is being
returned to the sender." (1 linha). A tabela de
`logistica_rules.tiktok_localizacao_pt` ganhou a frase ("Falha na entrega —
retornando ao remetente"); esta migração reescreve o gravado, com o mesmo
filtro da 0298/0427, e imprime o que ainda sobrar em inglês.

Downgrade é no-op, como na 0298.
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0428_tiktok_localizacao_retornando_remetente"
down_revision: str | None = "0427_tiktok_localizacao_devolvido_remetente"
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
                print(f"0428 ainda em inglês ({n}): {en!r}")
            continue
        bind.execute(
            sa.text(
                f"UPDATE {SCHEMA}.logistica SET localizacao = :pt "  # noqa: S608
                "WHERE lower(trim(plataforma)) = ANY(:plats) "
                "AND localizacao_at IS NULL AND localizacao = :en"
            ).bindparams(_plats(), pt=pt, en=en)
        )
        print(f"0428 traduziu ({n}): {en!r} -> {pt!r}")


def downgrade() -> None:
    pass
