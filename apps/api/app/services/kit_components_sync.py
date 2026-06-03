"""Sync semanal de `bling_kit_components` a partir da estrutura dos kits Bling.

Para cada produto composto ativo (`formato='E'`) com `bling_product_id`, lê
`estrutura.componentes` via `GET /produtos/{id}` (a listagem `/produtos` NÃO
traz a estrutura) e regrava os pares (kit → componente, quantidade) no cache
local. O order-lookup de devoluções usa esse cache pra explodir kits nos
componentes individuais.

A estrutura muda raramente (só quando se edita o kit no Bling), por isso o
sync é semanal. São ~1100 kits ativos → ~1100 GETs de detalhe; o BlingClient
trata o rate-limit. Best-effort por kit: um erro num kit não derruba o resto.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingKitComponent, Integration, IntegrationPlatform, Product
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketplaces.bling import BlingClient

logger = structlog.get_logger()


def _qty(v: object) -> Decimal:
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(1)
    return d if d > 0 else Decimal(1)


async def run_sync_kit_components(session: AsyncSession) -> dict:
    """Regrava `bling_kit_components` lendo a estrutura de cada kit ativo no
    Bling. Retorna um resumo."""
    integ = (
        await session.execute(
            select(Integration)
            .where(Integration.platform == IntegrationPlatform.BLING)
            .limit(1)
        )
    ).scalar_one_or_none()
    if integ is None:
        logger.warning("kit_components_sync_no_integration")
        return {"status": "no_integration"}

    creds = decrypt_json(integ.credentials)

    async def _persist(new_creds: dict) -> None:
        integ.credentials = encrypt_json(new_creds)
        exp = new_creds.get("expires_at")
        if exp:
            integ.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
        await session.commit()

    client = BlingClient(creds, on_token_refresh=_persist, integration_id=integ.id)

    kit_ids = (
        await session.execute(
            select(Product.bling_product_id).where(
                Product.formato == "E",
                Product.situacao == "A",
                Product.bling_product_id.is_not(None),
            )
        )
    ).scalars().all()

    summary = {
        "total": len(kit_ids),
        "fetched": 0,
        "with_components": 0,
        "components_written": 0,
        "empty": 0,
        "errors": 0,
    }

    for kit_pid in kit_ids:
        kit_pid = int(kit_pid)
        # get_product PRIMEIRO (pode disparar refresh+commit); só depois
        # delete+insert, pra um commit de refresh nunca pegar o kit no meio.
        try:
            raw = await client.get_product(kit_pid)
        except Exception as exc:  # noqa: BLE001
            summary["errors"] += 1
            logger.warning("kit_components_sync_get_failed", kit_pid=kit_pid, error=str(exc))
            continue
        summary["fetched"] += 1

        est = (raw or {}).get("estrutura") or {}
        comps = est.get("componentes") or [] if isinstance(est, dict) else []
        # Agrega por componente: o Bling às vezes lista o mesmo componente em
        # várias entradas (em vez de quantidade=2) — somamos pra não violar a
        # unique (kit, componente).
        desired: dict[int, Decimal] = {}
        for comp in comps:
            prod = (comp or {}).get("produto") or {}
            cid = prod.get("id")
            if cid:
                desired[int(cid)] = desired.get(int(cid), Decimal(0)) + _qty(comp.get("quantidade"))

        try:
            await session.execute(
                delete(BlingKitComponent).where(BlingKitComponent.kit_bling_product_id == kit_pid)
            )
            for comp_pid, qty in desired.items():
                session.add(
                    BlingKitComponent(
                        kit_bling_product_id=kit_pid,
                        component_bling_product_id=comp_pid,
                        quantidade=qty,
                    )
                )
            await session.commit()  # commit por kit: um kit ruim não derruba o run
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            summary["errors"] += 1
            logger.warning("kit_components_sync_write_failed", kit_pid=kit_pid, error=str(exc))
            continue

        if not desired:
            summary["empty"] += 1
        else:
            summary["with_components"] += 1
            summary["components_written"] += len(desired)

    logger.info("kit_components_sync_done", **summary)
    return summary
