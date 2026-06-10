"""One-off: atualiza ImportProduct.modelo_bling com o `nome` do produto
no Bling (categoria mala).

Operador renomeou produtos no Bling (ex.: "Mala Lisa M2 tamanho 12 -
Branca (DT - DTLG014 - DT16)") e quer essa descrição no nosso campo
modelo_bling — antes era um nome curto que ficou defasado.

Dry-run por padrão. --apply pra atualizar de fato. Idempotente.

Uso:
  docker compose exec -T api python -m scripts.backfill_modelo_bling_mala
  docker compose exec -T api python -m scripts.backfill_modelo_bling_mala --apply
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select, update

from app.db import session_scope
from app.models import Integration
from app.models.enums import IntegrationPlatform
from app.models.importacao import ImportProduct
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketplaces.bling import BlingClient, parse_bling_product


async def _build_bling_client(session, integration: Integration) -> BlingClient:
    creds = decrypt_json(integration.credentials)

    async def _persist(new_creds: dict) -> None:
        integration.credentials = encrypt_json(new_creds)
        await session.flush()

    return BlingClient(creds, on_token_refresh=_persist, integration_id=integration.id)


async def main(apply_flag: bool) -> None:
    async with session_scope() as session:
        integration = (await session.execute(
            select(Integration).where(
                Integration.platform == IntegrationPlatform.BLING,
                Integration.status == "active",
            ).limit(1)
        )).scalar_one_or_none()
        if integration is None:
            print("❌ Integration Bling ativa não encontrada")
            return
        client = await _build_bling_client(session, integration)

        rows = (await session.execute(
            select(ImportProduct).where(
                ImportProduct.categoria == "mala",
                ImportProduct.bling_product_id.isnot(None),
            )
        )).scalars().all()
        print(f"📦 {len(rows)} produtos mala com bling_product_id")
        if not apply_flag:
            print("⚠ DRY-RUN — passe --apply pra atualizar de fato.\n")

        stats = {"updated": 0, "skipped_same": 0, "no_name": 0, "fetch_err": 0}

        for r in rows:
            try:
                raw = await client.get_product(int(r.bling_product_id))
            except Exception as e:  # noqa: BLE001
                print(f"  {r.sku} (bling_id={r.bling_product_id}) FETCH_ERR {str(e)[:80]}")
                stats["fetch_err"] += 1
                continue
            if not raw:
                stats["no_name"] += 1
                continue
            parsed = parse_bling_product(raw)
            new_name = (parsed.get("name") or "").strip()
            if not new_name:
                stats["no_name"] += 1
                continue
            current = (r.modelo_bling or "").strip()
            if new_name == current:
                stats["skipped_same"] += 1
                continue
            print(f"  {r.sku}: '{current}' → '{new_name}'")
            if apply_flag:
                await session.execute(
                    update(ImportProduct)
                    .where(ImportProduct.id == r.id)
                    .values(modelo_bling=new_name)
                )
            stats["updated"] += 1

        if apply_flag:
            await session.commit()

        print("\n✅ Concluído")
        for k, v in stats.items():
            print(f"   {k:<15}: {v}")


if __name__ == "__main__":
    asyncio.run(main("--apply" in sys.argv))
