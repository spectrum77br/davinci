"""Backfill transporte address fields for devolucoes orders.

Fetches transporte data from Bling API for all orders in vw_devolucoes
that still have NULL nome_destinatario, cep_destino, or endereco_destino.

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
                      AND (bo.nome_destinatario IS NULL OR bo.cep_destino IS NULL OR bo.endereco_destino IS NULL)
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
                tp = tp if isinstance(tp, dict) else {}
                ct = tp.get("contato") or {}
                ct = ct if isinstance(ct, dict) else {}
                en = tp.get("enderecoEntrega") or {}
                en = en if isinstance(en, dict) else {}
                buyer = raw.get("contato") or {}
                buyer = buyer if isinstance(buyer, dict) else {}
                ben = buyer.get("endereco") or {}
                ben = ben if isinstance(ben, dict) else {}

                def _v(tp_f: str) -> str | None:
                    return en.get(tp_f) or ben.get(tp_f) or None

                nome = ct.get("nome") or buyer.get("nome") or None
                cep = _v("cep")
                endereco = _v("endereco")
                numero = _v("numero")
                complemento = _v("complemento")
                bairro = _v("bairro")
                cidade = _v("municipio")
                uf = _v("uf")
                if not any([cep, endereco, bairro, cidade, uf]):
                    print(f"  {bling_id}: no address data in API (nome={nome!r})")
                    skip += 1
                    continue
                values: dict = {}
                if nome:
                    values["nome_destinatario"] = nome
                if cep:
                    values["cep_destino"] = cep
                if endereco:
                    values["endereco_destino"] = endereco
                if numero:
                    values["numero_destino"] = numero
                if complemento:
                    values["complemento_destino"] = complemento
                if bairro:
                    values["bairro_destino"] = bairro
                if cidade:
                    values["cidade_destino"] = cidade
                if uf:
                    values["uf_destino"] = uf
                await session.execute(
                    update(BlingOrder)
                    .where(BlingOrder.bling_id == bling_id)
                    .values(**values)
                )
                print(f"  {bling_id}: nome={nome!r} cep={cep!r} {uf}/{cidade}")
                ok += 1
            except Exception as exc:
                print(f"  {bling_id}: ERROR {exc}", file=sys.stderr)
                errors += 1

        await session.commit()
        print(f"\nDone — updated={ok} skip={skip} errors={errors}")


if __name__ == "__main__":
    asyncio.run(main())
