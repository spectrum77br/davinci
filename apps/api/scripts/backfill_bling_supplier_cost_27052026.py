"""One-off: vincular fornecedor padrão + setar precoCusto nos produtos
da Importação Mala que ficaram sem custo no Bling.

Diferente do backfill anterior (v1) que enviava precoCusto sem
fornecedor.id — Bling V3 descartava silenciosamente. Esta versão usa
o fornecedor padrão (Settings.bling_default_supplier_name) como
anchor obrigatório.

Estratégia:
  1. Resolve contato.id pelo nome do fornecedor padrão (1x no início)
  2. Lista import_products com bling_product_id + custo_bling > 0
  3. Pra cada um: GET no Bling. Se precoCusto já > 0, skip.
     Senão: PUT com fornecedor.id + precoCusto + campos mínimos
     (nome/codigo/tipo/situacao/formato — Bling V3 PUT é replace).
  4. Re-GET pra confirmar persistência.

NÃO RODE sem aprovação. Tem `--dry-run` por padrão; --apply pra valer.

Uso:
  docker compose exec -T api python -m scripts.backfill_bling_supplier_cost_27052026
  docker compose exec -T api python -m scripts.backfill_bling_supplier_cost_27052026 --apply
"""
from __future__ import annotations

import asyncio
import sys
from typing import Any

from sqlalchemy import and_, select

from app.config import get_settings
from app.db import session_scope
from app.models import ImportProduct, Integration, IntegrationPlatform
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketplaces.bling import BlingClient


async def _bling_client(session) -> BlingClient | None:
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


def _current_cost(data: dict[str, Any]) -> float:
    f = data.get("fornecedor") or {}
    if isinstance(f, dict):
        v = f.get("precoCusto") or 0
    else:
        v = 0
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


async def main(apply_flag: bool) -> None:
    settings = get_settings()
    supplier_name = settings.bling_default_supplier_name
    if not supplier_name:
        print("ERR: BLING_DEFAULT_SUPPLIER_NAME vazio nas settings")
        return

    async with session_scope() as session:
        client = await _bling_client(session)
        if client is None:
            print("ERR: nenhuma Integration de Bling")
            return

        sid = await client.find_contato_id_by_name(supplier_name)
        if sid is None:
            print(f"ERR: fornecedor {supplier_name!r} não encontrado no Bling")
            return
        print(f"📍 fornecedor padrão '{supplier_name}' id={sid}")

        rows = (await session.execute(
            select(ImportProduct).where(
                and_(
                    ImportProduct.bling_product_id.isnot(None),
                    ImportProduct.custo_bling > 0,
                    ImportProduct.bling_sync_status == "sent",
                )
            )
        )).scalars().all()
        print(f"📦 {len(rows)} produtos a verificar")
        if not apply_flag:
            print("⚠ DRY-RUN — passe --apply pra atualizar de fato.\n")

        stats = {"skipped_ok": 0, "would_patch": 0, "patched": 0, "errors": 0}
        for r in rows:
            try:
                g = await client._request("GET", f"/produtos/{r.bling_product_id}")
                g.raise_for_status()
                data = g.json().get("data") or {}
            except Exception as e:  # noqa: BLE001
                print(f"  {r.sku}: ERR GET {str(e)[:120]}")
                stats["errors"] += 1
                continue

            current = _current_cost(data)
            if current > 0:
                print(f"  {r.sku}: já tem custo {current} no Bling, skip")
                stats["skipped_ok"] += 1
                continue

            target = float(r.custo_bling)
            if not apply_flag:
                print(f"  {r.sku} (bling {r.bling_product_id}): SET supplier={sid} precoCusto={target}")
                stats["would_patch"] += 1
                continue

            # PUT com campos mínimos + fornecedor (Bling V3 PUT é replace).
            body = {
                "nome": data.get("nome"),
                "codigo": data.get("codigo"),
                "tipo": data.get("tipo") or "P",
                "situacao": data.get("situacao") or "A",
                "formato": data.get("formato") or "S",
                "fornecedor": {"id": sid, "precoCusto": target},
            }
            try:
                p = await client._request(
                    "PUT", f"/produtos/{r.bling_product_id}", json=body,
                )
                if p.status_code not in (200, 204):
                    print(f"  {r.sku}: PUT status={p.status_code} {p.text[:200]}")
                    stats["errors"] += 1
                    continue
                # Re-GET pra confirmar persistência (Bling pode aceitar PUT
                # mas descartar precoCusto se algo está faltando).
                g2 = await client._request("GET", f"/produtos/{r.bling_product_id}")
                new_cost = _current_cost(g2.json().get("data") or {})
                if new_cost > 0:
                    print(f"  ✓ {r.sku}: custo={new_cost}")
                    stats["patched"] += 1
                else:
                    print(f"  ✗ {r.sku}: PUT 200 mas precoCusto continua 0")
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
