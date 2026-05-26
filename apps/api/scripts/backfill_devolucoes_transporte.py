"""Backfill nome_destinatario / cep_destino for devolucoes orders.

Fetches transporte data from Bling API for all orders in vw_devolucoes
that still have NULL nome_destinatario or cep_destino.

Run inside the API container:
  python scripts/backfill_devolucoes_transporte.py
"""

# ruff: noqa: T201

import asyncio
import sys

from sqlalchemy import select, text, update

from app.db import session_scope
from app.models import Integration, IntegrationPlatform
from app.models.bling_order import BlingOrder
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketplaces.bling import BlingClient


async def main() -> None:
    async with session_scope() as session:
        # -- 1. Load Bling integration credentials --------------------------
        integ = (
            await session.execute(
                select(Integration).where(
                    Integration.platform == IntegrationPlatform.BLING
                ).limit(1)
            )
        ).scalar_one_or_none()
        if integ is None:
            print("ERROR: no Bling integration found", file=sys.stderr)
            return

        creds = decrypt_json(integ.credentials)

        async def _persist(new_creds: dict) -> None:
            integ.credentials = encrypt_json(new_creds)
            await session.commit()

        client = BlingClient(creds, on_token_refresh=_persist, integration_id=integ.id)

        # -- 2. Find bling_ids that need backfill ---------------------------
        rows = (
            await session.execute(
                text(
                    """
                    SELECT DISTINCT bo.bling_id
                    FROM davinci.bling_orders bo
                    JOIN davinci.vw_devolucoes v ON v.bling_order_item_id = bo.id
                    WHERE bo.bling_id IS NOT NULL
                      AND (bo.nome_destinatario IS NULL OR bo.cep_destino IS NULL)
                    ORDER BY bo.bling_id
                    """
                )
            )
        ).fetchall()
        bling_ids = [r[0] for r in rows]
        print(f"Orders to backfill: {len(bling_ids)}")
        if not bling_ids:
            print("Nothing to do.")
            return

        # -- 3. Fetch from Bling and update ---------------------------------
        ok = 0
        skip = 0
        errors = 0
        for bling_id in bling_ids:
            try:
                raw = await client.get_order(int(bling_id))
                tp = raw.get("transporte") or {}
                ct = tp.get("contato") or {}
                en = tp.get("enderecoEntrega") or {}
                nome = ct.get("nome") or None
                cep = en.get("cep") or None
                if not nome and not cep:
                    print(f"  {bling_id}: no transporte data in API")
                    skip += 1
                    continue
                values: dict = {}
                if nome:
                    values["nome_destinatario"] = nome
                if cep:
                    values["cep_destino"] = cep
                await session.execute(
                    update(BlingOrder)
                    .where(BlingOrder.bling_id == bling_id)
                    .values(**values)
                )
                print(f"  {bling_id}: nome={nome!r} cep={cep!r}")
                ok += 1
            except Exception as exc:
                print(f"  {bling_id}: ERROR {exc}", file=sys.stderr)
                errors += 1

        await session.commit()
        print(f"\nDone — updated={ok} skip={skip} errors={errors}")


if __name__ == "__main__":
    asyncio.run(main())
