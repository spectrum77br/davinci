"""refunds — auto-popula tipo=Logistica para pedidos com diferença de frete

Backfill: INSERT WHERE NOT EXISTS varrendo vw_conciliacao_margens_marketplace
com o mesmo filtro de attention frete da página margens (frete_projetado_item
< frete_plataforma) agregado por (pedido_bling, conta).

Política de coexistência: o INSERT só roda se não houver NENHUM refund
com tipo='Logistica' para o mesmo (pedido_bling, conta). Manual e auto
coexistem pela ausência de overwrite — uma vez gravado, o auto-processo
nunca toca em refunds tipo='Logistica' (verificado pelo serviço também).

A regra de frete espelha _ATTENTION_FRETE_SQL em app/routers/margens.py
no momento da escrita; é intencionalmente congelada aqui (migrations
são snapshots, não código vivo).

Revision ID: 0070_refunds_frete_auto
Revises: 0071_integration_last_ads_sync_at
Create Date: 2026-05-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0072_refunds_frete_auto"
down_revision: str | None = "0071_integration_last_ads_sync_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

# Snapshot de _FRETE_PLATAFORMA_SQL em app/routers/margens.py.
_FRETE_PLATAFORMA_SQL = (
    "CASE "
    "WHEN COALESCE(v.plataforma_bling, v.plataforma_financeiro) = 'shopee' "
    "THEN CASE WHEN v.evento_freight IS NULL THEN NULL "
    "          ELSE GREATEST(v.evento_freight * v.item_proportion, 0::numeric) END "
    "ELSE v.marketplace_frete_real_cobrado_item "
    "END"
)


def upgrade() -> None:
    # Backfill: insere uma linha Logistica por (pedido_bling, conta) que
    # ainda não tenha refund Logistica registrado. Nunca atualiza, nunca
    # cria duplicata pro mesmo par.
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.refunds (
            data, pedido_bling, pedido_marketplace, plataforma, conta,
            tipo, prejuizo, reembolso, conferido
        )
        SELECT
            s.data, s.pedido_bling, s.pedido_marketplace, s.plataforma,
            s.conta, 'Logistica', s.prejuizo, NULL::double precision, false
        FROM (
            SELECT
                MAX(v.data) AS data,
                v.pedido_bling::text AS pedido_bling,
                MAX(v.pedido_marketplace)::text AS pedido_marketplace,
                COALESCE(v.plataforma_bling, v.plataforma_financeiro)::text AS plataforma,
                btrim(v.loja_nome) AS conta,
                SUM(({_FRETE_PLATAFORMA_SQL}) - v.frete_projetado_item) AS prejuizo
            FROM {SCHEMA}.vw_conciliacao_margens_marketplace v
            WHERE v.frete_projetado_item IS NOT NULL
              AND ({_FRETE_PLATAFORMA_SQL}) IS NOT NULL
              AND (v.frete_projetado_item - ({_FRETE_PLATAFORMA_SQL})) < 0
              AND v.pedido_bling IS NOT NULL
              AND v.loja_nome IS NOT NULL
              AND btrim(v.loja_nome) <> ''
            GROUP BY
                v.pedido_bling,
                COALESCE(v.plataforma_bling, v.plataforma_financeiro),
                btrim(v.loja_nome)
        ) s
        WHERE NOT EXISTS (
            SELECT 1 FROM {SCHEMA}.refunds r
            WHERE r.pedido_bling = s.pedido_bling
              AND r.conta = s.conta
              AND r.tipo = 'Logistica'
        )
        """  # noqa: S608
    )


def downgrade() -> None:
    # Não há marcador pra distinguir refunds Logistica auto-gerados dos
    # manuais. Downgrade é deliberadamente no-op pra não deletar refunds
    # manuais por engano. Se precisar reverter, identifique os pedidos
    # afetados por timestamp do backfill e delete manualmente.
    pass
