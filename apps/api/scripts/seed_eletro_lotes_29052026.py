"""Seed inicial dos 4 lotes Eletro (EL01–EL04) + items, a partir do
i.eletro.xlsx (dados já extraídos, hardcoded). Roda uma vez após o seed
dos produtos eletro. Idempotente: pula lote/item que já existe.

`previsto` NÃO é setado — é computado pela API como SUM(quantidade ×
custo_bling) dos items. Os valores da planilha batem com esse cálculo
(ex.: EL01 = (1475+795) × 240 = 544.800).

Uso:
  docker compose -f docker-compose.yml -f docker-compose.prod.yml \
    exec -T api python -m scripts.seed_eletro_lotes_29052026
"""
from __future__ import annotations

import asyncio
from datetime import date

from sqlalchemy import select

from app.db import session_scope
from app.models import ImportLote, ImportLoteItem, ImportProduct

# (nome, abertura) — previsto é computado, não armazenado.
LOTES_DATA = [
    {"nome": "EL01", "abertura": date(2026, 4, 22)},
    {"nome": "EL02", "abertura": date(2026, 4, 22)},
    {"nome": "EL03", "abertura": date(2026, 4, 22)},
    {"nome": "EL04", "abertura": date(2026, 5, 20)},
]

ITEMS_DATA = [
    ("uaf001m1.110", "EL01", 1475),
    ("uaf001m1.220", "EL01", 795),
    ("uaf002m1.110", "EL02", 500),
    ("uaf002m1.220", "EL02", 300),
    ("usl001m1.110", "EL03", 680),
    ("usl001m1.220", "EL03", 200),
    ("uco001m1.110", "EL04", 320),
]


async def main() -> None:
    async with session_scope() as session:
        lotes_by_name: dict[str, ImportLote] = {}
        for entry in LOTES_DATA:
            existing = (await session.execute(
                select(ImportLote).where(
                    ImportLote.nome == entry["nome"],
                    ImportLote.categoria == "eletro",
                )
            )).scalar_one_or_none()
            if existing:
                print(f"  skip lote {entry['nome']} (já existe)")
                lotes_by_name[entry["nome"]] = existing
                continue
            lote = ImportLote(
                nome=entry["nome"],
                categoria="eletro",
                abertura=entry["abertura"],
                fechamento=None,
            )
            session.add(lote)
            await session.flush()
            lotes_by_name[entry["nome"]] = lote
            print(f"  criado lote {entry['nome']} id={lote.id}")

        rows = (await session.execute(
            select(ImportProduct.id, ImportProduct.sku)
            .where(ImportProduct.categoria == "eletro")
        )).all()
        prod_by_sku = {r.sku.lower(): r.id for r in rows}

        for sku, lote_nome, quant in ITEMS_DATA:
            product_id = prod_by_sku.get(sku.lower())
            if not product_id:
                print(f"  ERR: produto {sku} não achado (categoria=eletro)")
                continue
            lote = lotes_by_name[lote_nome]
            existing = (await session.execute(
                select(ImportLoteItem).where(
                    ImportLoteItem.lote_id == lote.id,
                    ImportLoteItem.product_id == product_id,
                )
            )).scalar_one_or_none()
            if existing:
                print(f"  skip item {sku} × {lote_nome} (já existe)")
                continue
            session.add(ImportLoteItem(
                lote_id=lote.id,
                product_id=product_id,
                quantidade=quant,
            ))
            print(f"  criado item {sku} × {lote_nome} qty={quant}")

        await session.commit()
    print("\n✅ Seed eletro lotes concluído")


if __name__ == "__main__":
    asyncio.run(main())
