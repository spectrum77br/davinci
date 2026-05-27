"""One-off: backfill `fornecedor.precoCusto` no Bling pros produtos
criados pela importação entre o fix #1 (precoCusto top-level — Bling
ignorava) e o fix #2 (fornecedor.precoCusto — funcional).

Estratégia:
  1. Lista import_products com bling_product_id setado, custo_bling > 0,
     bling_sync_status = 'sent'
  2. Pra cada um: GET /produtos/{id} no Bling
     - Se fornecedor.precoCusto já > 0: skip (já backfilled ou criado novo).
     - Senão: PUT com o body completo + fornecedor.precoCusto setado.
  3. Logs por SKU mostram o que aconteceu.

NÃO RODE sem aprovação do operador. Tem `--dry-run` por padrão — mostra
o que faria sem chamar PUT. Pra rodar pra valer: passar `--apply`.

Uso:
  docker compose exec -T api python -m scripts.backfill_bling_cost_27052026
  docker compose exec -T api python -m scripts.backfill_bling_cost_27052026 --apply
"""
from __future__ import annotations

import asyncio
import sys
from typing import Any

from sqlalchemy import and_, select

from app.db import session_scope
from app.models import ImportProduct, Integration, IntegrationPlatform
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketplaces.bling import BlingClient


async def _bling_client_for_first_integration(session):
    integ = (await session.execute(
        select(Integration).where(
            Integration.platform == IntegrationPlatform.BLING
        ).limit(1)
    )).scalar_one_or_none()
    if integ is None:
        return None
    creds = decrypt_json(integ.credentials)

    async def _persist(new_creds: dict) -> None:
        integ.credentials = encrypt_json(new_creds)
        await session.flush()

    return BlingClient(creds, on_token_refresh=_persist, integration_id=integ.id)


def _get_current_cost(data: dict[str, Any]) -> float:
    """Lê fornecedor.precoCusto ou precoCusto top-level — espelha o
    parser de leitura do BlingClient."""
    cost = data.get("precoCusto") or 0
    if float(cost or 0) == 0:
        fornecedor = data.get("fornecedor") or {}
        if isinstance(fornecedor, dict):
            cost = fornecedor.get("precoCusto") or 0
    try:
        return float(cost or 0)
    except (TypeError, ValueError):
        return 0.0


async def main(apply: bool) -> None:
    async with session_scope() as session:
        client = await _bling_client_for_first_integration(session)
        if client is None:
            print("ERR: nenhuma Integration de Bling")
            return

        rows = (await session.execute(
            select(ImportProduct).where(
                and_(
                    ImportProduct.bling_product_id.isnot(None),
                    ImportProduct.custo_bling > 0,
                    ImportProduct.bling_sync_status == "sent",
                )
            )
        )).scalars().all()
        print(f"📦 {len(rows)} produtos com bling_product_id + custo_bling > 0")
        if not apply:
            print("⚠ DRY-RUN — passe --apply pra atualizar de fato.\n")

        stats = {"skipped_ok": 0, "would_patch": 0, "patched": 0, "errors": 0}
        for r in rows:
            # GET atual
            try:
                g = await client._request("GET", f"/produtos/{r.bling_product_id}")
                g.raise_for_status()
                data = g.json().get("data") or {}
            except Exception as e:  # noqa: BLE001
                print(f"  {r.sku}: ERR GET {str(e)[:120]}")
                stats["errors"] += 1
                continue

            current = _get_current_cost(data)
            if current > 0:
                print(f"  {r.sku}: já tem custo {current} no Bling, skip")
                stats["skipped_ok"] += 1
                continue

            target = float(r.custo_bling)
            if not apply:
                print(f"  {r.sku} (bling {r.bling_product_id}): SET fornecedor.precoCusto={target}")
                stats["would_patch"] += 1
                continue

            # PUT com merge: usa o body atual do Bling + adiciona/sobrepoe
            # fornecedor.precoCusto. Bling V3 /produtos/{id} é PUT (replace),
            # então enviar só `{"fornecedor": ...}` poderia apagar outros
            # campos. Re-enviamos o body completo recebido do GET.
            body = dict(data)
            fornecedor = body.get("fornecedor") or {}
            if not isinstance(fornecedor, dict):
                fornecedor = {}
            fornecedor["precoCusto"] = target
            body["fornecedor"] = fornecedor
            # Remove campos que o PUT pode rejeitar (id é redundante com URL).
            body.pop("id", None)

            try:
                p = await client._request(
                    "PUT", f"/produtos/{r.bling_product_id}", json=body,
                )
                if p.status_code in (200, 204):
                    print(f"  {r.sku}: backfilled custo={target}")
                    stats["patched"] += 1
                else:
                    print(f"  {r.sku}: ERR status={p.status_code} body={p.text[:200]}")
                    stats["errors"] += 1
            except Exception as e:  # noqa: BLE001
                print(f"  {r.sku}: ERR PUT {str(e)[:120]}")
                stats["errors"] += 1

        print("\n✅ Concluído")
        for k, v in stats.items():
            print(f"   {k:<14}: {v}")


if __name__ == "__main__":
    apply_flag = "--apply" in sys.argv
    asyncio.run(main(apply_flag))
