"""One-off: recupera `categoria` dos produtos/pedidos do Valuation.

Motivo: a partir de junho o Bling parou de mandar `item.produto.categoria`
no payload do PEDIDO (GET /pedidos → categoria: null), então
`bling_orders.categoria_nome` ficou vazio e a Margem operacional por categoria
caiu tudo em "Sem categoria". O GET /produtos/{id} AINDA traz `categoria: {id}`
(só o id, sem nome) — este script busca esse id por produto e:
  1. grava `products.category = str(id)` (auto-cura o produto p/ o pipeline);
  2. carimba `bling_orders.categoria_id`/`categoria_nome` (nome resolvido via
     `product_categories.bling_category_id`) nas linhas sem categoria.

Escopo: só os `item_produto_id` distintos dos pedidos SEM categoria na janela
de 3 meses do Valuation (mês-2 / mês-1 / atual). Rate-limit ~0.3s entre chamadas.

Uso:
  uv run python backfill_categoria.py           # dry-run (só relata)
  uv run python backfill_categoria.py --apply    # grava em prod
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from app.db import session_scope
from app.services.bling_orders import _bling_client_for_user

_APPLY = "--apply" in sys.argv

_WINDOW_SQL = text("""
    SELECT DISTINCT item_produto_id
    FROM bling_orders
    WHERE item_produto_id IS NOT NULL
      AND (categoria_nome IS NULL OR categoria_nome = '')
      AND data >= date_trunc('month', now() AT TIME ZONE 'America/Sao_Paulo')
                  - interval '2 months'
""")

_CATS_SQL = text("SELECT bling_category_id, name FROM product_categories")

_UPD_PRODUCT_SQL = text("""
    UPDATE products SET category = :cat
    WHERE bling_product_id = :pid AND (category IS NULL OR category = '')
""")

_UPD_ORDERS_SQL = text("""
    UPDATE bling_orders SET categoria_id = :cid, categoria_nome = :name
    WHERE item_produto_id = :pid
      AND (categoria_nome IS NULL OR categoria_nome = '')
""")


async def main() -> None:
    async with session_scope() as session:
        product_ids = [
            int(r[0])
            for r in (await session.execute(_WINDOW_SQL)).all()
        ]
        cat_name_by_id = {
            int(cid): name
            for cid, name in (await session.execute(_CATS_SQL)).all()
        }
        print(f"produtos sem categoria na janela: {len(product_ids)}")
        print(f"categorias conhecidas: {len(cat_name_by_id)}")
        print(f"modo: {'APLICAR (grava)' if _APPLY else 'DRY-RUN (só relata)'}")

        # Um só client (ignora user_id — pega a 1ª Integration BLING).
        client = await _bling_client_for_user(
            session, user_id=None  # type: ignore[arg-type]
        )
        if client is None:
            print("ERRO: nenhuma Integration BLING encontrada.")
            return

        resolved = 0
        no_categoria = 0
        no_name = 0
        errors = 0
        prod_updated = 0
        orders_updated = 0
        by_cat: dict[str, int] = {}

        for i, pid in enumerate(product_ids, 1):
            try:
                data = await client.get_product(pid)
            except Exception as exc:  # noqa: BLE001
                errors += 1
                print(f"[{i}/{len(product_ids)}] {pid}: ERRO {exc!r}")
                await asyncio.sleep(0.3)
                continue

            categoria = data.get("categoria") or {}
            cat_id = categoria.get("id") if isinstance(categoria, dict) else None
            if cat_id is None:
                no_categoria += 1
                await asyncio.sleep(0.3)
                continue
            cat_id = int(cat_id)
            name = cat_name_by_id.get(cat_id)
            if not name:
                no_name += 1
                print(f"[{i}/{len(product_ids)}] {pid}: categoria {cat_id} "
                      "sem nome em product_categories")
                await asyncio.sleep(0.3)
                continue

            resolved += 1
            by_cat[name] = by_cat.get(name, 0) + 1

            if _APPLY:
                await session.execute(
                    _UPD_PRODUCT_SQL, {"cat": str(cat_id), "pid": pid}
                )
                res = await session.execute(
                    _UPD_ORDERS_SQL,
                    {"cid": cat_id, "name": name, "pid": pid},
                )
                prod_updated += 1
                orders_updated += res.rowcount or 0
                if i % 50 == 0:
                    await session.commit()

            await asyncio.sleep(0.3)

        if _APPLY:
            await session.commit()

        print("\n=== RESUMO ===")
        print(f"resolvidos (categoria + nome): {resolved}")
        print(f"sem categoria no Bling:        {no_categoria}")
        print(f"categoria sem nome no catálogo:{no_name}")
        print(f"erros de fetch:                {errors}")
        if _APPLY:
            print(f"produtos atualizados:          {prod_updated}")
            print(f"linhas de pedido carimbadas:   {orders_updated}")
        print("\npor categoria (produtos):")
        for name, n in sorted(by_cat.items(), key=lambda x: -x[1]):
            print(f"  {name}: {n}")


if __name__ == "__main__":
    asyncio.run(main())
