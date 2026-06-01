"""Ingest Bling pedidos de venda from API into `bling_orders`.

Triggered by Bling webhook (`pedido.alteracao`, `pedido.alteracao.situacao`,
`pedido.exclusao`). Webhook payload only carries an order id; full order is
fetched via `GET /pedidos/vendas/{id}` and split into one row per item.

`pedido.exclusao` sets `situacao='excluido'` (soft delete — audit trail).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    BlingOrder,
    Integration,
    IntegrationPlatform,
    LinkSyncStatus,
    Product,
    ProductCategory,
    ProductLink,
    Store,
)
from app.security.cipher import decrypt_json, encrypt_json

# Cutoff operacional (< 10h BRT = dia anterior). Webhooks/sync do Bling
# que chegam de madrugada são quase sempre re-emissão do despacho da
# véspera — sem o cutoff, esses eventos caem no dia errado da planilha.
from app.services.marketplace_shipment_check import _operational_ship_date
from app.services.marketplaces.bling import BlingClient
from app.services.verificar_margem import refresh_silent as _verificar_margem_refresh_silent
from app.worker_pool import get_arq_pool

# em_andamento_data is the operator-facing ship-date column on the
# planilha. Stamp it in Brasília-local date — without this conversion,
# orders shipped late evening BRT would land on the next calendar day.
_BRT = ZoneInfo("America/Sao_Paulo")

# Situações que significam "despachado": 83965 (Enviado Etiqueta = etiqueta
# gerada) ou 15 (Atendido = agência confirmou). em_andamento_data é carimbada
# já no 83965 — é quando o operador despacha. Antes só carimbávamos no 15, então
# pedidos parados em 83965 ficavam com data NULL e "flutuavam" pra HOJE todo dia
# no filtro da aba (effective_date = COALESCE(em_andamento_data, hoje)).
_SHIP_STAMP_SITUACOES = ("15", "83965")

logger = structlog.get_logger()


def _to_dt(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    s = str(raw).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _num(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _int(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


async def _resolve_store_id(
    session: AsyncSession, bling_loja_id: int | None
) -> UUID | None:
    if bling_loja_id is None:
        return None
    row = (
        await session.execute(
            select(Store.id).where(Store.bling_store_id == bling_loja_id).limit(1)
        )
    ).scalar_one_or_none()
    return row


async def _bling_client_for_user(
    session: AsyncSession, user_id: UUID
) -> BlingClient | None:
    integ = (
        await session.execute(
            select(Integration).where(
                Integration.platform == IntegrationPlatform.BLING
            ).limit(1)
        )
    ).scalar_one_or_none()
    if integ is None:
        return None
    creds = decrypt_json(integ.credentials)

    async def _persist(new_creds: dict) -> None:
        integ.credentials = encrypt_json(new_creds)
        exp = new_creds.get("expires_at")
        if exp:
            integ.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
        await session.commit()

    return BlingClient(creds, on_token_refresh=_persist, integration_id=integ.id)


def _row_from_item(
    raw_order: dict[str, Any],
    item: dict[str, Any],
    *,
    item_index: int,
    store_id: UUID | None,
    category_names_by_id: dict[int, str] | None = None,
    product_categories_by_id: dict[int, tuple[int | None, str]] | None = None,
    product_categories_by_sku: dict[str, tuple[int | None, str]] | None = None,
    em_andamento_data_final: date | None = None,
) -> dict[str, Any]:
    """Flatten one (order, item) pair into a `bling_orders` row dict.

    `em_andamento_data_final`: data já decidida pelo caller via
    `_next_em_andamento_data` (que conhece a situação ANTERIOR e aplica a
    regra de transição). Este helper só repassa — toda a lógica de
    transição 83965/15 mora no caller.
    """
    loja = raw_order.get("loja") or {}
    if not isinstance(loja, dict):
        loja = {}
    categoria = (item.get("produto") or {}).get("categoria") or {}
    if not isinstance(categoria, dict):
        categoria = {}
    comissao = item.get("comissao") or {}
    if not isinstance(comissao, dict):
        comissao = {}
    produto = item.get("produto") or {}
    if not isinstance(produto, dict):
        produto = {}
    item_codigo = item.get("codigo") or produto.get("codigo")
    item_produto_id = _int(produto.get("id"))
    categoria_id = _int(categoria.get("id"))
    categoria_nome = categoria.get("descricao") or categoria.get("nome")
    if not categoria_nome and categoria_id is not None and category_names_by_id:
        categoria_nome = category_names_by_id.get(categoria_id)
    if not categoria_nome:
        product_category = None
        if item_produto_id is not None and product_categories_by_id:
            product_category = product_categories_by_id.get(item_produto_id)
        if product_category is None and item_codigo and product_categories_by_sku:
            product_category = product_categories_by_sku.get(str(item_codigo).strip().lower())
        if product_category is not None:
            fallback_category_id, categoria_nome = product_category
            if categoria_id is None:
                categoria_id = fallback_category_id

    # Marketplace fees live under `taxas` (valorBase / custoFrete / taxaComissao),
    # not at the top level. Bling populates these AFTER the marketplace settles,
    # so they may be NULL on the first webhook and arrive on a later
    # `pedido.alteracao.situacao` event. Fallback to `originaldata.taxas` (raw
    # marketplace payload preserved by Bling) if the normalized field is empty.
    taxas = raw_order.get("taxas") if isinstance(raw_order.get("taxas"), dict) else {}
    orig_taxas = (
        (raw_order.get("originaldata") or {}).get("taxas")
        if isinstance(raw_order.get("originaldata"), dict)
        else {}
    )
    if not isinstance(orig_taxas, dict):
        orig_taxas = {}

    situacao_id = (
        str(((raw_order.get("situacao") or {}) or {}).get("id"))
        if isinstance(raw_order.get("situacao"), dict)
        else (str(raw_order.get("situacao")) if raw_order.get("situacao") is not None else None)
    )
    # em_andamento_data é decidida pelo caller (upsert_order via
    # _next_em_andamento_data) e passada pronta — a regra de transição
    # 83965->15 mora no caller, que conhece a situação ANTERIOR.
    em_andamento_data = em_andamento_data_final

    transporte = raw_order.get("transporte") or {}
    if not isinstance(transporte, dict):
        transporte = {}
    _t_contato = transporte.get("contato") or {}
    if not isinstance(_t_contato, dict):
        _t_contato = {}
    _t_endereco = transporte.get("enderecoEntrega") or {}
    if not isinstance(_t_endereco, dict):
        _t_endereco = {}

    # Fallback to contato.nome / contato.endereco when transporte has no data.
    # Amazon orders typically have no transporte.enderecoEntrega but do have the
    # buyer's delivery address under contato.endereco.
    _buyer = raw_order.get("contato") or {}
    if not isinstance(_buyer, dict):
        _buyer = {}
    _buyer_endereco = _buyer.get("endereco") or {}
    if not isinstance(_buyer_endereco, dict):
        _buyer_endereco = {}

    def _addr(tp_field: str, buyer_field: str | None = None) -> str | None:
        return (
            _t_endereco.get(tp_field)
            or _buyer_endereco.get(buyer_field or tp_field)
            or None
        )

    return {
        "bling_id": _int(raw_order.get("id")),
        "numero": str(raw_order["numero"]) if raw_order.get("numero") is not None else None,
        "numeroloja": (str(raw_order["numeroLoja"]) if raw_order.get("numeroLoja") else None),
        "numero_documento": (
            str(raw_order["numeroPedidoCompra"])
            if raw_order.get("numeroPedidoCompra") else None
        ),
        "data": _to_dt(raw_order.get("data")),
        "totalprodutos": _num(raw_order.get("totalProdutos") or raw_order.get("totalprodutos")),
        "total": _num(raw_order.get("total")),
        "situacao": situacao_id,
        "em_andamento_data": em_andamento_data,
        "loja": str(loja.get("id")) if loja.get("id") is not None else None,
        "store_id": store_id,
        "itens": raw_order.get("itens"),
        "valorbase": _num(
            taxas.get("valorBase") if taxas.get("valorBase") is not None
            else orig_taxas.get("valorBase")
        ),
        "custofrete": _num(
            taxas.get("custoFrete") if taxas.get("custoFrete") is not None
            else orig_taxas.get("custoFrete")
        ),
        "taxacomissao": _num(
            taxas.get("taxaComissao") if taxas.get("taxaComissao") is not None
            else orig_taxas.get("taxaComissao")
        ),
        "item_id": _int(item.get("id")),
        "item_index": item_index,
        "itemvalor": _num(item.get("valor")),
        "item_codigo": item_codigo,
        "item_produto_id": item_produto_id,
        "item_descricao": item.get("descricao") or produto.get("nome"),
        "item_quantidade": _int(item.get("quantidade")),
        "item_desconto": _num(item.get("desconto")),
        "item_comissao_base": _num(comissao.get("base")),
        "item_comissao_valor": _num(comissao.get("valor")),
        "categoria_id": categoria_id,
        "categoria_nome": categoria_nome,
        "nome_destinatario": _t_contato.get("nome") or _buyer.get("nome") or None,
        "cep_destino": _addr("cep"),
        "endereco_destino": _addr("endereco"),
        "numero_destino": _addr("numero"),
        "complemento_destino": _addr("complemento"),
        "bairro_destino": _addr("bairro"),
        "cidade_destino": _addr("municipio"),
        "uf_destino": _addr("uf"),
    }


def _situacao_id(raw_order: dict[str, Any]) -> str | None:
    s = raw_order.get("situacao")
    if isinstance(s, dict):
        sid = s.get("id")
        return str(sid) if sid is not None else None
    return str(s) if s is not None else None


def _next_em_andamento_data(
    *,
    nova_situacao: str | None,
    situacao_antiga: str | None,
    data_existente: date | None,
    agora: datetime,
) -> date | None:
    """Decide a em_andamento_data (data operacional na planilha de Pedidos).

    - Transição **83965 → 15** (etiqueta confirmada pela agência AGORA):
      carimba o DIA DA CONFIRMAÇÃO, sobrescrevendo o provisório do 83965.
      Único caso de overwrite. Operação: etiqueta dia 30 + confirmação dia
      31 => pedido fica no dia 31.
    - Qualquer outra transição pra 15 (15↔15 re-disparo de taxa/endereço,
      oscilação 83953→15 do Bling, etc.): PRESERVA a data existente — não
      empurra pra hoje. A condição estreita `== "83965"` evita o bug em
      que `!= "15"` (commit 1fe10b6) pegava todas as voltas pra 15 e
      re-carimbava o dia do sync.
    - 83965 (Enviado Etiqueta) sem data ainda: provisório = dia da etiqueta,
      só pra não 'flutuar' pra HOJE no filtro. Badge segue vermelho.
    - Demais (6 Em aberto etc.): mantém o que houver (normalmente NULL).
    """
    if nova_situacao == "15" and situacao_antiga == "83965":
        return _operational_ship_date(agora)
    if data_existente is not None:
        return data_existente
    if nova_situacao in _SHIP_STAMP_SITUACOES:
        return _operational_ship_date(agora)
    return None


async def _cost_price_by_sku(
    session: AsyncSession, skus: set[str]
) -> dict[str, float]:
    """Custo unitário p/ snapshot do pedido = `products.bling_cost_price`
    (custo sincronizado diariamente do Bling), não `cost_price`."""
    if not skus:
        return {}
    rows = (
        await session.execute(
            select(Product.sku, Product.bling_cost_price).where(
                Product.sku.in_(skus)
            )
        )
    ).all()
    return {sku: float(cost) for sku, cost in rows if cost is not None}


async def _category_names_by_bling_id(
    session: AsyncSession, category_ids: set[int]
) -> dict[int, str]:
    if not category_ids:
        return {}
    rows = (
        await session.execute(
            select(
                ProductCategory.bling_category_id,
                ProductCategory.name,
            ).where(ProductCategory.bling_category_id.in_(category_ids))
        )
    ).all()
    return {int(category_id): name for category_id, name in rows}


async def _product_categories_by_item(
    session: AsyncSession, itens: list[Any]
) -> tuple[dict[int, tuple[int | None, str]], dict[str, tuple[int | None, str]]]:
    product_ids: set[int] = set()
    sku_keys: set[str] = set()
    for item in itens:
        if not isinstance(item, dict):
            continue
        produto = item.get("produto") or {}
        if not isinstance(produto, dict):
            produto = {}
        product_id = _int(produto.get("id"))
        if product_id is not None:
            product_ids.add(product_id)
        sku = item.get("codigo") or produto.get("codigo")
        if sku is not None:
            sku_key = str(sku).strip().lower()
            if sku_key:
                sku_keys.add(sku_key)

    if not product_ids and not sku_keys:
        return {}, {}

    category_rows = (
        await session.execute(
            select(ProductCategory.bling_category_id, ProductCategory.name)
        )
    ).all()
    category_by_id = {
        str(category_id): (int(category_id), name)
        for category_id, name in category_rows
    }
    category_by_name = {
        name.strip().lower(): (int(category_id), name)
        for category_id, name in category_rows
    }

    filters = []
    if product_ids:
        filters.append(Product.bling_product_id.in_(product_ids))
    if sku_keys:
        filters.append(func.lower(func.btrim(Product.sku)).in_(sku_keys))

    product_rows = (
        await session.execute(
            select(
                Product.bling_product_id,
                Product.sku,
                Product.category,
            ).where(or_(*filters))
        )
    ).all()
    by_product_id: dict[int, tuple[int | None, str]] = {}
    by_sku: dict[str, tuple[int | None, str]] = {}
    for product_id, sku, raw_category in product_rows:
        if raw_category is None:
            continue
        category = str(raw_category).strip()
        if not category:
            continue
        resolved = category_by_id.get(category) or category_by_name.get(category.lower())
        if resolved is None and not category.isdigit():
            resolved = (None, category)
        if resolved is None:
            continue
        if product_id is not None:
            by_product_id[int(product_id)] = resolved
        if sku is not None:
            sku_key = str(sku).strip().lower()
            if sku_key:
                by_sku[sku_key] = resolved
    return by_product_id, by_sku


def _normalize_new_itens(itens: list[Any]) -> list[tuple[str, int, float]]:
    out: list[tuple[str, int, float]] = []
    for it in itens:
        if not isinstance(it, dict):
            continue
        produto = it.get("produto") if isinstance(it.get("produto"), dict) else {}
        codigo = it.get("codigo") or produto.get("codigo") or ""
        qty = _int(it.get("quantidade")) or 0
        valor = _num(it.get("valor")) or 0.0
        out.append((str(codigo).strip(), qty, float(valor)))
    return sorted(out)


async def _existing_itens_signature(
    session: AsyncSession, bling_id: int
) -> list[tuple[str, int, float]]:
    rows = (
        await session.execute(
            select(
                BlingOrder.item_codigo,
                BlingOrder.item_quantidade,
                BlingOrder.itemvalor,
            ).where(BlingOrder.bling_id == bling_id)
        )
    ).all()
    return sorted(
        (
            ((codigo or "").strip(), int(qty or 0), float(valor or 0))
            for codigo, qty, valor in rows
        )
    )


async def upsert_order(
    session: AsyncSession,
    raw_order: dict[str, Any],
) -> int:
    """Persist a Bling order. Strategy:
    - situacao=6 ("Em andamento"): full replace + snapshot `preco_custo` from
      `products.bling_cost_price`.
    - other situations: if itens unchanged vs. DB, only UPDATE `situacao`
      (preserves preco_custo, taxas_checked_at, etc. populated by the daily
      sync); if itens differ or no row exists yet, full replace.
    """
    bling_id = _int(raw_order.get("id"))
    if bling_id is None:
        return 0

    situacao = _situacao_id(raw_order)

    itens = raw_order.get("itens") or []
    if not isinstance(itens, list):
        itens = []

    # Estado anterior do pedido (pra decidir em_andamento_data). situacao_antiga
    # distingue 83965→15 (etiqueta confirmada agora => carimba dia da confirmação;
    # único caso de overwrite) de qualquer outra volta pra 15 (oscilação Bling
    # 15↔83953 etc. => preserva). data_existente = ship-date já gravado.
    prev_rows = (
        await session.execute(
            select(BlingOrder.situacao, BlingOrder.em_andamento_data)
            .where(BlingOrder.bling_id == bling_id)
        )
    ).all()
    existing_count = len(prev_rows)
    situacao_antiga = prev_rows[0][0] if prev_rows else None
    data_existente = next((r[1] for r in prev_rows if r[1] is not None), None)
    nova_data = _next_em_andamento_data(
        nova_situacao=situacao,
        situacao_antiga=situacao_antiga,
        data_existente=data_existente,
        agora=datetime.now(UTC),
    )

    # Non-6 + existing rows + itens unchanged → narrow UPDATE only.
    if situacao != "6" and existing_count > 0:
        new_sig = _normalize_new_itens(itens)
        old_sig = await _existing_itens_signature(session, bling_id)
        if new_sig == old_sig:
            values: dict[str, Any] = {"situacao": situacao}
            # em_andamento_data decidida por _next_em_andamento_data: só a
            # transição 83965→15 sobrescreve (dia da confirmação); demais
            # voltas pra 15 (oscilação Bling) preservam; 83965 sem data ganha
            # provisório (dia da etiqueta) pra não flutuar pra hoje no filtro.
            if nova_data is not None:
                values["em_andamento_data"] = nova_data
            # Backfill address data for orders synced before migrations 0088/0094.
            # Prefer transporte.enderecoEntrega; fall back to contato.endereco
            # (Amazon and some other marketplaces omit transporte address).
            _tp = raw_order.get("transporte") or {}
            _tp = _tp if isinstance(_tp, dict) else {}
            _ct = _tp.get("contato") or {}
            _en = _tp.get("enderecoEntrega") or {}
            _ct = _ct if isinstance(_ct, dict) else {}
            _en = _en if isinstance(_en, dict) else {}
            _buyer = raw_order.get("contato") or {}
            _buyer = _buyer if isinstance(_buyer, dict) else {}
            _ben = _buyer.get("endereco") or {}
            _ben = _ben if isinstance(_ben, dict) else {}

            def _v(tp_f: str, buyer_f: str | None = None) -> str | None:
                return _en.get(tp_f) or _ben.get(buyer_f or tp_f) or None

            nome = _ct.get("nome") or _buyer.get("nome") or None
            if nome:
                values["nome_destinatario"] = nome
            for col, tp_f, buyer_f in [
                ("cep_destino", "cep", None),
                ("endereco_destino", "endereco", None),
                ("numero_destino", "numero", None),
                ("complemento_destino", "complemento", None),
                ("bairro_destino", "bairro", None),
                ("cidade_destino", "municipio", None),
                ("uf_destino", "uf", None),
            ]:
                val = _v(tp_f, buyer_f)
                if val:
                    values[col] = val
            await session.execute(
                update(BlingOrder)
                .where(BlingOrder.bling_id == bling_id)
                .values(**values)
            )
            return existing_count

    loja = raw_order.get("loja") or {}
    bling_loja_id = _int(loja.get("id")) if isinstance(loja, dict) else None
    store_id = await _resolve_store_id(session, bling_loja_id)

    # situacao=6 → snapshot unit cost from products.bling_cost_price.
    cost_by_sku: dict[str, float] = {}
    category_ids: set[int] = set()
    if situacao == "6" and itens:
        skus: set[str] = set()
        for it in itens:
            if not isinstance(it, dict):
                continue
            raw_sku = it.get("codigo") or (it.get("produto") or {}).get("codigo")
            if raw_sku is None:
                continue
            s = str(raw_sku).strip()
            if s:
                skus.add(s)
        cost_by_sku = await _cost_price_by_sku(session, skus)
    for it in itens:
        if not isinstance(it, dict):
            continue
        produto = it.get("produto") or {}
        if not isinstance(produto, dict):
            continue
        categoria = produto.get("categoria") or {}
        if not isinstance(categoria, dict):
            continue
        categoria_id = _int(categoria.get("id"))
        if categoria_id is not None:
            category_ids.add(categoria_id)
    category_names_by_id = await _category_names_by_bling_id(session, category_ids)
    (
        product_categories_by_id,
        product_categories_by_sku,
    ) = await _product_categories_by_item(session, itens)

    rows: list[dict[str, Any]] = []
    if not itens:
        # Order with no items — keep one summary row at item_index=0.
        row = _row_from_item(
            raw_order,
            {},
            item_index=0,
            store_id=store_id,
            category_names_by_id=category_names_by_id,
            product_categories_by_id=product_categories_by_id,
            product_categories_by_sku=product_categories_by_sku,
            em_andamento_data_final=nova_data,
        )
        row["preco_custo"] = None
        rows.append(row)
    else:
        for idx, item in enumerate(itens):
            if not isinstance(item, dict):
                continue
            row = _row_from_item(
                raw_order,
                item,
                item_index=idx,
                store_id=store_id,
                category_names_by_id=category_names_by_id,
                product_categories_by_id=product_categories_by_id,
                product_categories_by_sku=product_categories_by_sku,
                em_andamento_data_final=nova_data,
            )
            sku = (row.get("item_codigo") or "").strip() if row.get("item_codigo") else None
            row["preco_custo"] = cost_by_sku.get(sku) if sku else None
            rows.append(row)

    if not rows:
        return 0

    # Upsert por (bling_id, item_index) em vez de DELETE+INSERT: preserva
    # bling_orders.id entre re-ingests. O uuid4 trocava a cada re-ingest e
    # defasava o PK guardado no snapshot verificar_margem — causando
    # bling_order_not_found na página de margens. A UNIQUE (bling_id,
    # item_index) (migration 0111) também serializa webhooks concorrentes
    # que antes geravam linhas duplicadas. id é gerado aqui (não via default
    # do ORM) porque pg_insert não dispara defaults Python; na colisão o id
    # NÃO entra no SET, então o id existente é preservado. Colunas fora de
    # _row_from_item (status, observacao, aprovado_por, entregue_at, …) também
    # ficam de fora do SET e são preservadas — antes o DELETE+INSERT as perdia.
    for row in rows:
        row["id"] = uuid4()
    stmt = pg_insert(BlingOrder).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["bling_id", "item_index"],
        set_={
            col: getattr(stmt.excluded, col)
            for col in rows[0]
            if col not in ("id", "bling_id", "item_index")
        },
    )
    await session.execute(stmt)

    # Remove itens que saíram do pedido (existiam antes, não vieram agora),
    # sem tocar nos ids dos que permanecem.
    kept_item_index = [row["item_index"] for row in rows]
    await session.execute(
        delete(BlingOrder).where(
            BlingOrder.bling_id == bling_id,
            BlingOrder.item_index.notin_(kept_item_index),
        )
    )
    return len(rows)


async def mark_order_excluido(
    session: AsyncSession,
    bling_order_id: int,
) -> int:
    """Soft-delete: stamp `situacao='excluido'` across all rows of the order."""
    rows = (
        await session.execute(
            select(BlingOrder).where(BlingOrder.bling_id == bling_order_id)
        )
    ).scalars().all()
    for r in rows:
        r.situacao = "excluido"
    return len(rows)


async def _resolve_local_product(
    session: AsyncSession,
    *,
    sku: str | None,
    bling_product_id: int | None,
) -> Product | None:
    if sku:
        p = (
            await session.execute(
                select(Product).where(Product.sku == sku).limit(1)
            )
        ).scalar_one_or_none()
        if p is not None:
            return p
    if bling_product_id is not None:
        p = (
            await session.execute(
                select(Product).where(
                    Product.bling_product_id == bling_product_id
                ).limit(1)
            )
        ).scalar_one_or_none()
        if p is not None:
            return p
        link = (
            await session.execute(
                select(ProductLink).where(
                    ProductLink.platform == IntegrationPlatform.BLING,
                    ProductLink.external_id == str(bling_product_id),
                ).limit(1)
            )
        ).scalar_one_or_none()
        if link is not None:
            return await session.get(Product, link.product_id)
    return None


async def _enqueue_stock_refresh_for_order(
    session: AsyncSession,
    *,
    raw_order: dict[str, Any],
    user_id: UUID,
) -> list[tuple[UUID, UUID, list[str]]]:
    """For each item in the order, resolve local Product and create a
    SYNC_PRODUCT BackgroundJob so the stock refresh shows up in the jobs UI."""
    itens = raw_order.get("itens") or []
    if not isinstance(itens, list):
        return []
    seen_products: set[UUID] = set()
    jobs: list[tuple[UUID, UUID, list[str]]] = []
    for item in itens:
        if not isinstance(item, dict):
            continue
        produto = item.get("produto") or {}
        if not isinstance(produto, dict):
            produto = {}
        sku_raw = item.get("codigo") or produto.get("codigo")
        sku = (str(sku_raw).strip() or None) if sku_raw is not None else None
        bling_pid = _int(produto.get("id"))
        product = await _resolve_local_product(
            session, sku=sku, bling_product_id=bling_pid
        )
        if product is None or product.id in seen_products:
            continue
        seen_products.add(product.id)
        links = (
            await session.execute(
                select(ProductLink).where(
                    ProductLink.product_id == product.id,
                    ProductLink.last_sync_status.in_(
                        [
                            LinkSyncStatus.OK,
                            LinkSyncStatus.PENDING,
                            LinkSyncStatus.REQUIRES_REVIEW,
                        ]
                    ),
                )
            )
        ).scalars().all()
        link_ids = [str(link.id) for link in links]
        job = BackgroundJob(
            type=BackgroundJobType.SYNC_PRODUCT,
            status=BackgroundJobStatus.PENDING,
            created_by=user_id,
            total=len(link_ids),
            payload={
                "trigger": "bling_pedido",
                "bling_order_id": _int(raw_order.get("id")),
                "product_id": str(product.id),
                "link_ids": link_ids,
                "sku": sku,
            },
        )
        session.add(job)
        await session.flush()
        jobs.append((job.id, product.id, link_ids))
    return jobs


async def run_ingest_bling_order(
    session: AsyncSession,
    *,
    bling_order_id: int,
    user_id: UUID,
    event: str | None,
) -> dict[str, Any]:
    """Worker entrypoint. Fetches order from Bling and upserts (or marks excluded).
    For non-exclusion events, also enqueues a SYNC_PRODUCT job per matched SKU
    so the sale-driven stock refresh is visible in the jobs UI."""
    if event in ("pedido.exclusao", "order.deleted"):
        n = await mark_order_excluido(session, bling_order_id)
        await session.commit()
        await _verificar_margem_refresh_silent(session, bling_id=bling_order_id)
        logger.info(
            "bling_order_excluido", bling_order_id=bling_order_id, rows=n
        )
        return {"ok": True, "rows": n, "deleted": True}

    client = await _bling_client_for_user(session, user_id)
    if client is None:
        logger.warning(
            "bling_order_ingest_no_integration",
            bling_order_id=bling_order_id,
            user_id=str(user_id),
        )
        return {"ok": False, "error": "no_bling_integration"}

    raw = await client.get_order(bling_order_id)
    if not raw:
        logger.warning(
            "bling_order_ingest_empty", bling_order_id=bling_order_id
        )
        return {"ok": False, "error": "empty_order"}

    n = await upsert_order(session, raw)
    jobs = await _enqueue_stock_refresh_for_order(
        session, raw_order=raw, user_id=user_id
    )
    await session.commit()
    await _verificar_margem_refresh_silent(session, bling_id=bling_order_id)

    pool = None
    try:
        pool = await get_arq_pool()
        arq = await pool.enqueue_job(
            "sync_marketplace_financials_for_order_run",
            int(bling_order_id),
            "webhook",
        )
        logger.info(
            "bling_order_financial_sync_enqueued",
            bling_order_id=bling_order_id,
            arq_job_id=arq.job_id if arq is not None else None,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "bling_order_financial_sync_enqueue_failed",
            bling_order_id=bling_order_id,
            err=str(e)[:500],
        )

    if jobs:
        if pool is None:
            pool = await get_arq_pool()
        for job_id, product_id, link_ids in jobs:
            arq = await pool.enqueue_job(
                "sync_product_run",
                str(job_id),
                str(user_id),
                str(product_id),
                link_ids or None,
            )
            if arq is not None:
                job = await session.get(BackgroundJob, job_id)
                if job is not None:
                    job.arq_job_id = arq.job_id
        await session.commit()

    logger.info(
        "bling_order_ingested",
        bling_order_id=bling_order_id,
        bling_event=event,
        rows=n,
        refresh_jobs=len(jobs),
    )
    return {
        "ok": True,
        "rows": n,
        "deleted": False,
        "refresh_jobs": len(jobs),
    }
