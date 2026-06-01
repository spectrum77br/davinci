"""One-off: re-grava `estrutura` (componentes) em kits Bling que foram
criados com a aba Estrutura vazia.

Bug: o `bling_kit_create.py` antigo passava `estrutura` no body do
POST /produtos, mas Bling V3 descarta silenciosamente os componentes
(mesmo padrão do `fornecedor`). Fix em prod já corrige novos kits via
PUT /produtos/estruturas/{id} numa 2ª chamada. Este script aplica a
mesma correção aos kits que já estavam criados sem componentes.

Pega marks com `bling_product_id NOT NULL`, faz GET na estrutura no
Bling, e se `componentes==[]` reconstrói via mark+variation e chama
`update_product_estrutura`. Marca `bling_sync_status="sent"` no sucesso.

Uso:
  docker compose ... exec -T api python -m scripts.backfill_kit_estruturas
  docker compose ... exec -T api python -m scripts.backfill_kit_estruturas --apply
  docker compose ... exec -T api python -m scripts.backfill_kit_estruturas --mark-id <uuid>
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from app.db import session_scope
from app.models import (
    ImportKitBase,
    ImportKitMark,
    ImportKitVariation,
    Integration,
    IntegrationPlatform,
)
from app.security.cipher import decrypt_json, encrypt_json
from app.services.bling_kit_create import (
    _ESTRUTURA_LANCAMENTO,
    _ESTRUTURA_TIPO_ESTOQUE,
    _resolve_component_bling_ids,
    _resolve_component_skus,
)
from app.services.marketplaces.bling import BlingClient


async def _bling_client(session):
    integ = (await session.execute(
        select(Integration)
        .where(Integration.platform == IntegrationPlatform.BLING)
        .limit(1)
    )).scalar_one_or_none()
    if integ is None:
        return None
    creds = decrypt_json(integ.credentials)

    async def _persist(new_creds: dict) -> None:
        integ.credentials = encrypt_json(new_creds)
        exp = new_creds.get("expires_at")
        if exp:
            integ.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
        await session.flush()

    return BlingClient(creds, on_token_refresh=_persist, integration_id=integ.id)


async def _fetch_estrutura(client: BlingClient, product_id: int) -> dict | None:
    """GET /produtos/estruturas/{id} via endpoint dedicado. Retorna o
    dict de estrutura ou None se 404/empty."""
    try:
        r = await client._request(
            "GET", f"/produtos/estruturas/{int(product_id)}",
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json().get("data") or {}
    except Exception as e:  # noqa: BLE001
        print(f"  ! GET estrutura {product_id} falhou: {e}")
        return None


async def main(apply_flag: bool, mark_id_filter: str | None) -> None:
    label = "APPLY" if apply_flag else "DRY-RUN"
    print(f"{label} backfill kit estruturas")

    async with session_scope() as session:
        q = select(ImportKitMark).where(ImportKitMark.bling_product_id.isnot(None))
        if mark_id_filter:
            q = q.where(ImportKitMark.id == UUID(mark_id_filter))
        marks = (await session.execute(q)).scalars().all()
        print(f"  marks com bling_product_id setado: {len(marks)}")

        client = await _bling_client(session)
        if client is None:
            print("  ! sem Bling integration — abortando")
            return

        stats = {
            "checked": 0, "ja_ok": 0, "vazias_corrigidas": 0,
            "vazias_falha": 0, "missing_comp": 0,
        }

        for mark in marks:
            stats["checked"] += 1
            # Pega base + variation pra reconstruir a estrutura.
            base = (await session.execute(
                select(ImportKitBase).where(ImportKitBase.id == mark.base_id)
            )).scalar_one_or_none()
            variation = (await session.execute(
                select(ImportKitVariation).where(ImportKitVariation.id == mark.variation_id)
            )).scalar_one_or_none()
            if base is None or variation is None:
                print(f"  ! mark {mark.id} sem base/variation — pulando")
                continue

            sku_base = base.sku_base
            var_code = variation.code
            bling_id = int(mark.bling_product_id)

            est = await _fetch_estrutura(client, bling_id)
            componentes_atuais = (est or {}).get("componentes") or []
            if componentes_atuais:
                stats["ja_ok"] += 1
                continue

            comp_skus = _resolve_component_skus(sku_base, var_code)
            resolved, missing = await _resolve_component_bling_ids(session, comp_skus)
            if missing:
                stats["missing_comp"] += 1
                print(
                    f"  ! mark {mark.id} ({sku_base}/{var_code}) bling_id={bling_id} "
                    f"falta bling_id pros componentes: {missing}"
                )
                continue

            comp_strs = ", ".join(f"{sku}={bid}" for sku, bid, _ in resolved)
            print(
                f"  kit {sku_base}/{var_code} bling_id={bling_id} vazio → "
                f"{len(resolved)} comp: {comp_strs}"
            )

            if not apply_flag:
                continue

            estrutura = {
                "tipoEstoque": _ESTRUTURA_TIPO_ESTOQUE,
                "lancamentoEstoque": _ESTRUTURA_LANCAMENTO,
                "componentes": [
                    {"produto": {"id": bid}, "quantidade": 1}
                    for _, bid, _ in resolved
                ],
            }
            try:
                await client.update_product_estrutura(
                    product_id=bling_id, estrutura=estrutura,
                )
                stats["vazias_corrigidas"] += 1
                mark.bling_sync_status = "sent"
                mark.bling_sync_error = None
                mark.bling_sync_done_at = datetime.now(UTC)
                await session.flush()
            except Exception as e:  # noqa: BLE001
                stats["vazias_falha"] += 1
                print(f"  ! PUT estrutura {bling_id} falhou: {e}")

        if apply_flag:
            await session.commit()

        print("\nResumo:")
        for k, v in stats.items():
            print(f"  {k}: {v}")

    if not apply_flag:
        print("\nDRY-RUN — passe --apply pra gravar.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--mark-id", help="Filtrar pra UMA mark específica (uuid)")
    args = ap.parse_args()
    asyncio.run(main(apply_flag=args.apply, mark_id_filter=args.mark_id))
