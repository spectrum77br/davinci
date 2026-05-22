"""Controle de Estoque router.

3 tabs on the front-end, each backed by one GET here:
  * /api/estoque/produtos    — Estoque tab: per-SKU summary of the day's
                               movements + current stock + reserve.
  * /api/estoque/pedidos     — Pedidos tab: bling_orders flagged as
                               "enviado etiqueta" (situacao=15), filtered
                               by the operator's stock_tag.
  * /api/estoque/envios      — Envios tab: per-day shipment count for
                               orders whose `em_andamento_data` falls
                               inside the window.
  * POST /api/estoque/check  — upserts the conferido checkbox for any
                               of the three sections.
  * POST /api/estoque/movement/{id}/obs — operator inline-edit of the
                               movement observação (webhook doesn't
                               carry it; this is the manual hook).

Scoping:
  * Admin (UserRole.ADMIN) sees everything; can pass `?tag=` to
    narrow to a specific operator's view.
  * Operator (stock_tag set, role != admin) sees ONLY products /
    orders whose SKU ends with `.{stock_tag}` and `situacao = 'A'`,
    `formato = 'S'` (simples — kits aren't operated by warehouse).
  * Anyone without role=admin AND without stock_tag → 403.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import Date, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import BlingOrder, Product, User, UserRole
from app.models.company import Store
from app.models.integration import Integration
from app.models.stock_check import StockCheck
from app.models.stock_movement import StockMovement

logger = structlog.get_logger()
router = APIRouter(prefix="/api/estoque", tags=["estoque"])

_VALID_TAGS = frozenset({"ci", "pi", "ra", "sa", "sp"})
# Bling situação ID for "enviado etiqueta" — confirmed against prod
# distinct values: id=15 has 735/928 rows with em_andamento_data set,
# the highest correspondence rate of any situação. id=12 is cancelado;
# 83953/83957 are custom statuses for this shop.
_SITUACAO_ENVIADO_ETIQUETA = "15"


def _resolve_tag(user: User, override: str | None) -> str | None:
    """Returns the tag to filter by — None means "no filter" (admin viewing all)."""
    if user.role == UserRole.ADMIN:
        if override:
            ov = override.strip().lower()
            if ov not in _VALID_TAGS:
                raise HTTPException(400, detail={"code": "invalid_tag"})
            return ov
        return None  # admin sees everything
    if not user.stock_tag:
        raise HTTPException(403, detail={"code": "no_stock_tag"})
    return user.stock_tag.lower()


def _resolve_dates(
    data_inicio: date | None, data_fim: date | None
) -> tuple[date, date]:
    """Both default to today. Caller already received `date` objects."""
    today = datetime.now(UTC).date()
    return (data_inicio or today, data_fim or today)


# ─── SEÇÃO 1: ESTOQUE ────────────────────────────────────────────────────


@router.get("/produtos")
async def list_estoque_produtos(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("controle_estoque", "view"))],
    data_inicio: date | None = Query(None),
    data_fim: date | None = Query(None),
    tag: str | None = Query(None),  # admin-only override
) -> dict[str, Any]:
    tag_filter = _resolve_tag(user, tag)
    data_inicio, data_fim = _resolve_dates(data_inicio, data_fim)
    window_start = datetime.combine(data_inicio, time.min, tzinfo=UTC)
    window_end = datetime.combine(data_fim, time.max, tzinfo=UTC)

    # `formato='S'` should already exclude kits, but prod data has some
    # compound SKUs ("x009.ci+a001.ci") sneaking through with the
    # simples flag set incorrectly. Belt-and-suspenders: drop anything
    # whose SKU contains a '+' character regardless of `formato`.
    where: list = [
        Product.situacao == "A",
        Product.formato == "S",
        Product.sku.notlike("%+%"),
    ]
    if tag_filter is not None:
        where.append(Product.sku.ilike(f"%.{tag_filter}"))

    products = (
        await session.execute(
            select(
                Product.id, Product.sku, Product.name,
                Product.stock, Product.reserved_stock,
            )
            .where(and_(*where))
            .order_by(Product.sku)
        )
    ).all()
    if not products:
        return {
            "data": [],
            "periodo": {"inicio": str(data_inicio), "fim": str(data_fim)},
        }

    skus = [p.sku for p in products if p.sku]
    movements = (
        await session.execute(
            select(
                StockMovement.id,
                StockMovement.sku,
                StockMovement.tipo,
                StockMovement.quantidade,
                StockMovement.observacao,
                StockMovement.origem,
                StockMovement.date,
            )
            .where(
                and_(
                    StockMovement.sku.in_(skus),
                    StockMovement.date >= window_start,
                    StockMovement.date <= window_end,
                )
            )
            .order_by(StockMovement.date.asc())
        )
    ).all()

    # Per-SKU buckets of individual entradas and saídas. We expose the
    # full lists so the front-end can render one "{qty} - {obs}" row per
    # entrada (matches what the operator sees in the Bling planilha).
    # `saida_qty_total` is a convenience sum — operators still want the
    # day's total for the saída column. `saida_origens` is the
    # comma-list of pedido numbers.
    by_sku: dict[str, dict[str, Any]] = {}
    for m in movements:
        slot = by_sku.setdefault(
            m.sku,
            {"entradas": [], "saidas": [], "saida_qty_total": 0, "saida_origens": []},
        )
        if m.tipo == "E":
            slot["entradas"].append({
                "movement_id": str(m.id),
                "qty": int(m.quantidade or 0),
                "obs": m.observacao or "",
            })
        elif m.tipo == "S":
            slot["saidas"].append({
                "movement_id": str(m.id),
                "qty": int(m.quantidade or 0),
                "origem": m.origem or "",
            })
            slot["saida_qty_total"] += int(m.quantidade or 0)
            if m.origem:
                slot["saida_origens"].append(m.origem)

    checks_rows = (
        await session.execute(
            select(StockCheck.reference_id, StockCheck.conferido)
            .where(
                StockCheck.user_id == user.id,
                StockCheck.section == "estoque",
                StockCheck.reference_date >= data_inicio,
                StockCheck.reference_date <= data_fim,
            )
        )
    ).all()
    checks = {r.reference_id: bool(r.conferido) for r in checks_rows}

    result: list[dict[str, Any]] = []
    for p in products:
        slot = by_sku.get(p.sku, {})
        virtual = int(p.stock or 0)
        reserved = int(p.reserved_stock or 0)
        # `Product.stock` is the VIRTUAL balance (Bling saldoVirtualTotal).
        # The operator's "saldo atual" column wants the FÍSICO total —
        # virtual + reserved reconstructs that. No new column needed.
        saldo_fisico = virtual + reserved
        result.append({
            "sku": p.sku,
            "nome": p.name,
            "entradas": slot.get("entradas") or [],
            "saidas": slot.get("saidas") or [],
            "saida_qty_total": int(slot.get("saida_qty_total", 0)),
            "saida_origens": ", ".join(slot.get("saida_origens") or []),
            "saldo_fisico": saldo_fisico,
            "saldo_virtual": virtual,
            "reserva": reserved,
            "conferido": checks.get(p.sku, False),
        })

    return {
        "data": result,
        "periodo": {"inicio": str(data_inicio), "fim": str(data_fim)},
    }


# ─── SEÇÃO 2: PEDIDOS ────────────────────────────────────────────────────


@router.get("/pedidos")
async def list_estoque_pedidos(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("controle_estoque", "view"))],
    data_inicio: date | None = Query(None),
    data_fim: date | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),  # enviado | nao_enviado
    tag: str | None = Query(None),
) -> dict[str, Any]:
    """Lista pedidos enviado-etiqueta. Filtra por `em_andamento_data`
    (data efetiva do envio) — pedidos que ainda não foram bipados
    aparecem apenas quando `status_filter='nao_enviado'` é pedido
    explicitamente; nesse modo cai pra `BlingOrder.data` como filtro
    (sem em_andamento_data nada por que filtrar)."""
    tag_filter = _resolve_tag(user, tag)
    data_inicio, data_fim = _resolve_dates(data_inicio, data_fim)

    where: list = [BlingOrder.situacao == _SITUACAO_ENVIADO_ETIQUETA]
    if tag_filter is not None:
        where.append(BlingOrder.item_codigo.ilike(f"%.{tag_filter}"))

    if status_filter == "nao_enviado":
        where.append(BlingOrder.em_andamento_data.is_(None))
        where.append(cast(BlingOrder.data, Date) >= data_inicio)
        where.append(cast(BlingOrder.data, Date) <= data_fim)
        order_by = BlingOrder.data.desc()
    else:
        # Default + "enviado" filter: scope by the SHIP date.
        where.append(BlingOrder.em_andamento_data.isnot(None))
        where.append(BlingOrder.em_andamento_data >= data_inicio)
        where.append(BlingOrder.em_andamento_data <= data_fim)
        order_by = BlingOrder.em_andamento_data.desc()

    orders = (
        await session.execute(
            select(BlingOrder).where(and_(*where)).order_by(order_by)
        )
    ).scalars().all()

    # Build a bling_store_id → "Plataforma loja" map for every store
    # referenced by the result set. One query, no N+1. Falls back to the
    # raw ID string when the lookup misses (rare — manual data, etc).
    store_ids: set[int] = set()
    for o in orders:
        try:
            store_ids.add(int(o.loja))
        except (TypeError, ValueError):
            continue
    store_name_by_id: dict[int, str] = {}
    if store_ids:
        rows = (
            await session.execute(
                select(Store.bling_store_id, Integration.name, Integration.platform)
                .join(Integration, Integration.id == Store.integration_id, isouter=True)
                .where(Store.bling_store_id.in_(store_ids))
            )
        ).all()
        for r in rows:
            try:
                bsid = int(r.bling_store_id)
            except (TypeError, ValueError):
                continue
            plat = (r.platform.value if hasattr(r.platform, "value") else str(r.platform or "")).strip()
            label = (r.name or "").strip()
            if plat and label:
                store_name_by_id[bsid] = f"{plat.upper()} {label}"
            elif label:
                store_name_by_id[bsid] = label

    order_ids = [str(o.id) for o in orders]
    checks_map: dict[str, dict[str, Any]] = {}
    if order_ids:
        checks_rows = (
            await session.execute(
                select(StockCheck.reference_id, StockCheck.conferido, StockCheck.observacao)
                .where(
                    StockCheck.user_id == user.id,
                    StockCheck.section == "pedido",
                    StockCheck.reference_id.in_(order_ids),
                )
            )
        ).all()
        for r in checks_rows:
            checks_map[r.reference_id] = {
                "conferido": bool(r.conferido),
                "observacao": r.observacao,
            }

    result: list[dict[str, Any]] = []
    for o in orders:
        check = checks_map.get(str(o.id), {"conferido": False, "observacao": None})
        bling_store_id: int | None = None
        try:
            bling_store_id = int(o.loja) if o.loja else None
        except (TypeError, ValueError):
            bling_store_id = None
        loja_name = (
            store_name_by_id.get(bling_store_id) if bling_store_id is not None else None
        ) or (o.loja or "")
        # "data" on the front-end shows the SHIP date; fall back to the
        # order create date if em_andamento_data isn't set (only happens
        # for the nao_enviado filter).
        ship_or_create = o.em_andamento_data or (o.data.date() if o.data else None)
        result.append({
            "id": str(o.id),
            "data": ship_or_create.isoformat() if ship_or_create else None,
            "data_pedido": o.data.isoformat() if o.data else None,
            "data_envio": o.em_andamento_data.isoformat() if o.em_andamento_data else None,
            "pedido_bling": o.numero,
            "pedido_marketplace": o.numeroloja,
            "loja": loja_name,
            "sku": o.item_codigo,
            "produto": o.item_descricao,
            "quantidade": o.item_quantidade or 1,
            "status": "enviado" if o.em_andamento_data else "nao_enviado",
            "conferido": check["conferido"],
            "observacao": check["observacao"],
            "bling_id": o.bling_id,
        })

    return {
        "data": result,
        "periodo": {"inicio": str(data_inicio), "fim": str(data_fim)},
    }


# ─── SEÇÃO 3: ENVIOS ─────────────────────────────────────────────────────


@router.get("/envios")
async def list_estoque_envios(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("controle_estoque", "view"))],
    data_inicio: date | None = Query(None),
    data_fim: date | None = Query(None),
    tag: str | None = Query(None),
    conferido_filter: str | None = Query(
        None,
        alias="conferido",
        pattern="^(all|conferidos|nao_conferidos)$",
    ),
) -> dict[str, Any]:
    tag_filter = _resolve_tag(user, tag)
    # Envios tab defaults to last 7 days when no window is set, matching
    # the page's date-picker default.
    today = datetime.now(UTC).date()
    if data_inicio is None and data_fim is None:
        data_inicio = today - timedelta(days=6)
        data_fim = today
    else:
        data_inicio, data_fim = _resolve_dates(data_inicio, data_fim)

    where: list = [
        BlingOrder.em_andamento_data.isnot(None),
        BlingOrder.em_andamento_data >= data_inicio,
        BlingOrder.em_andamento_data <= data_fim,
    ]
    if tag_filter is not None:
        where.append(BlingOrder.item_codigo.ilike(f"%.{tag_filter}"))

    rows = (
        await session.execute(
            select(
                BlingOrder.em_andamento_data.label("dia"),
                func.count(func.distinct(BlingOrder.bling_id)).label("envios"),
            )
            .where(and_(*where))
            .group_by(BlingOrder.em_andamento_data)
            .order_by(BlingOrder.em_andamento_data.desc())
        )
    ).all()

    checks_rows = (
        await session.execute(
            select(StockCheck.reference_id, StockCheck.conferido)
            .where(
                StockCheck.user_id == user.id,
                StockCheck.section == "envio",
                StockCheck.reference_date >= data_inicio,
                StockCheck.reference_date <= data_fim,
            )
        )
    ).all()
    checks = {r.reference_id: bool(r.conferido) for r in checks_rows}

    items: list[dict[str, Any]] = []
    total_envios = 0
    total_conferido = 0
    for r in rows:
        dia_str = r.dia.isoformat() if r.dia else ""
        envios_n = int(r.envios or 0)
        conf = checks.get(dia_str, False)
        # `conferido_filter` is applied client-of-the-loop so totals
        # reflect the visible set only. `all` (or None) shows everything;
        # the other two narrow to one bucket.
        if conferido_filter == "conferidos" and not conf:
            continue
        if conferido_filter == "nao_conferidos" and conf:
            continue
        items.append({"data": dia_str, "envios": envios_n, "conferido": conf})
        total_envios += envios_n
        if conf:
            total_conferido += envios_n

    return {
        "data": items,
        # Spec: rodapé "Total" conta SÓ os conferidos. `total_envios`
        # mantido pra "Total geral" se a UI quiser exibir.
        "total": total_conferido,
        "total_envios": total_envios,
        "total_conferido": total_conferido,
        "periodo": {"inicio": str(data_inicio), "fim": str(data_fim)},
    }


# ─── TOGGLE CONFERIDO ────────────────────────────────────────────────────


@router.post("/check")
async def toggle_estoque_check(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("controle_estoque", "edit"))],
    section: str = Query(..., pattern="^(estoque|pedido|envio)$"),
    reference_id: str = Query(...),
    reference_date: date = Query(...),
    conferido: bool = Query(...),
    observacao: str | None = Query(None),
) -> dict[str, Any]:
    # Envios tab is an admin-only triage view — operators see read-only
    # ✓/✗ but can't toggle. The other two sections are operator-editable.
    if section == "envio" and user.role != UserRole.ADMIN:
        raise HTTPException(403, detail={"code": "admin_only"})
    existing = (
        await session.execute(
            select(StockCheck).where(
                StockCheck.user_id == user.id,
                StockCheck.section == section,
                StockCheck.reference_id == reference_id,
                StockCheck.reference_date == reference_date,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        session.add(
            StockCheck(
                user_id=user.id,
                section=section,
                reference_id=reference_id,
                reference_date=reference_date,
                conferido=conferido,
                observacao=observacao,
            )
        )
    else:
        existing.conferido = conferido
        if observacao is not None:
            existing.observacao = observacao or None

    await session.commit()
    return {"ok": True}


# ─── MOVEMENT OBS PATCH (operator inline-edit) ───────────────────────────


@router.patch("/movement/{movement_id}/obs")
async def patch_movement_obs(
    movement_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("controle_estoque", "edit"))],
    observacao: str | None = Query(None),
) -> dict[str, Any]:
    """Bling's estoque webhook doesn't carry the 'observação' field that
    the operator's planilha shows in the 'Responsável' column. This
    endpoint lets the operator write that value in. Soft validation —
    we only require the movement to exist and the user to have edit
    permission; tag-scoping is enforced by /api/estoque/produtos which
    is the only way the operator discovers movement IDs."""
    m = await session.get(StockMovement, movement_id)
    if m is None:
        raise HTTPException(404, detail={"code": "movement_not_found"})
    m.observacao = (observacao or "").strip() or None
    await session.commit()
    return {"ok": True, "movement_id": str(m.id), "observacao": m.observacao}
