"""logistica.localizacao das linhas TikTok — legado em inglês vira português

Vinicius, 21/09/2026: a coluna Localização do painel Logística mostrava o
texto cru do último evento de tracking do TikTok (Fulfillment API 202309),
que só existe em inglês — testado com locale e Accept-Language, a API não
tem versão em PT. Levantamento em produção no mesmo dia: 2000 linhas TikTok,
30 textos distintos, 95% "Package has been delivered!". Daqui em diante o
enrich grava traduzido (`logistica_rules.tiktok_localizacao_pt`); esta
migração reescreve o que já está gravado.

Só linhas TikTok SEM `localizacao_at`:
com carimbo é evento real do 17track (envio por Correios), que já vem em PT.
Texto que a tabela não reconhece fica como está (o warning do serviço aponta
o que falta). Sem `action_code` gravado, a tradução é só pelo texto.

Downgrade é no-op: o inglês original não é recuperável a partir do PT (a
tradução perde a frase exata) e o próximo enrich regrava a linha de qualquer
jeito.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0298_tiktok_localizacao_pt"
down_revision: str | None = "0297_chamados_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
# = logistica_rules._TIKTOK_PLATAFORMAS na data; copiado pra migração não
# depender de um nome privado do serviço.
_PLATAFORMAS_TIKTOK = ["tik tok", "tiktok", "tiktok shop"]


def _plats() -> sa.BindParameter:
    return sa.bindparam("plats", value=_PLATAFORMAS_TIKTOK, type_=sa.ARRAY(sa.Text()))


def upgrade() -> None:
    # Import tardio: o alembic carrega TODAS as versões em qualquer comando
    # (`heads`, `current`, `upgrade`); um rename futuro no serviço não pode
    # travar o grafo inteiro de migrações.
    from app.services.logistica_rules import tiktok_localizacao_pt

    bind = op.get_bind()
    textos = bind.execute(
        sa.text(
            f"SELECT DISTINCT localizacao FROM {SCHEMA}.logistica "  # noqa: S608
            "WHERE lower(trim(plataforma)) = ANY(:plats) "
            "AND localizacao_at IS NULL AND localizacao IS NOT NULL"
        ).bindparams(_plats())
    ).scalars().all()
    for en in textos:
        pt = tiktok_localizacao_pt(en)
        if not pt or pt == en:
            continue
        bind.execute(
            sa.text(
                f"UPDATE {SCHEMA}.logistica SET localizacao = :pt "  # noqa: S608
                "WHERE lower(trim(plataforma)) = ANY(:plats) "
                "AND localizacao_at IS NULL AND localizacao = :en"
            ).bindparams(_plats(), pt=pt, en=en)
        )


def downgrade() -> None:
    # Sem volta: o texto em inglês não se reconstrói a partir do PT.
    pass
