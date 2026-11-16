"""devolutions: a rotina do prazo volta a consultar as devoluções TikTok — só dado

16/11/2026 (commit 8a452781): o ramo TikTok de `devolucao_prazo._prazo_da_plataforma`
chamava `_return_tiktok` com o client em vez dos casos; o TypeError era engolido e a
rodada carimbava `prazo_contestacao_at` mesmo assim. A rotina só volta a perguntar
quem está SEM MOTIVO (de 6 em 6 h), então devolução TikTok que já tem motivo ficou
marcada como "consultada" sem prazo e nunca mais seria lida — a coluna
"Prazo contest." ficaria vazia pra sempre nela.

Zera `prazo_contestacao_at` das devoluções TikTok da janela da rotina (45 dias) que
estão sem prazo: a próxima rodada (:20/:50, 60 por vez, mais novas primeiro) pergunta
de novo. TikTok = o mesmo caminho da rotina: espelho do pedido
(bling_orders.loja → store_info.platform) ou, de reserva, o nome da conta. Quem a
TikTok responder sem ação pendente só ganha a marca de consultada de novo.

O downgrade não faz nada (a marca é só "quando consultei por último").

Revision ID: 0490_devolucao_prazo_tiktok_reconsulta
Revises: 0489_atendimento_nao_precisa_colunas
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0490_devolucao_prazo_tiktok_reconsulta"
down_revision: str | None = "0489_atendimento_nao_precisa_colunas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"
JANELA_DIAS = 45  # = devolucao_prazo.JANELA_DIAS
# = logistica_rules._TIKTOK_PLATAFORMAS
TIKTOK = ("tiktok", "tik tok", "tiktok shop")


def _sql(schema: str) -> str:
    return (
        f'UPDATE "{schema}".devolutions d '  # noqa: S608 — nome fixo daqui
        "SET prazo_contestacao_at = NULL "
        "WHERE d.prazo_contestacao IS NULL "
        "AND d.prazo_contestacao_at IS NOT NULL "
        "AND d.created_at >= now() - make_interval(days => :dias) "
        "AND ("
        "  EXISTS ("
        f'    SELECT 1 FROM "{schema}".bling_orders b '
        f'    JOIN "{schema}".store_info s ON s.bling_store_id = b.loja '
        "    WHERE btrim(coalesce(d.pedido_bling, '')) <> '' "
        "      AND (b.numero = btrim(d.pedido_bling) OR b.numeroloja = btrim(d.pedido_bling)) "
        "      AND lower(btrim(s.platform)) IN :tiktok"
        "  ) OR EXISTS ("
        f'    SELECT 1 FROM "{schema}".store_info s '
        "    WHERE btrim(coalesce(d.conta, '')) <> '' "
        "      AND lower(btrim(s.account_name)) = lower(btrim(d.conta)) "
        "      AND lower(btrim(s.platform)) IN :tiktok"
        "  )"
        ")"
    )


def reconsultar(conn: sa.engine.Connection, schema: str) -> int:
    stmt = sa.text(_sql(schema)).bindparams(sa.bindparam("tiktok", expanding=True))
    return conn.execute(stmt, {"dias": JANELA_DIAS, "tiktok": list(TIKTOK)}).rowcount


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    n = reconsultar(op.get_bind(), SCHEMA)
    print(f"[0490] devoluções TikTok liberadas pra rotina do prazo consultar de novo: {n}")


def downgrade() -> None:
    pass
