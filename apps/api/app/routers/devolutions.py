import re
from datetime import UTC, date, datetime, time, timedelta
from io import BytesIO
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from sqlalchemy import desc, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_permission
from app.models import BlingOrder, Devolution, Product, Refund, User
from app.schemas.devolutions import (
    BlingStockResultOut,
    DevolutionCreate,
    DevolutionLookupOut,
    DevolutionOut,
    DevolutionPage,
    DevolutionPatch,
    DevolutionProductOut,
    SkuSuffixesOut,
    SkuSuffixVariant,
    StockCorrectionIn,
)
from app.services.devolution_stock_return import (
    _STOCK_TRIGGER_CONDICOES,
    _SUFFIX_TAGS,
    _sku_base,
    _sku_tag,
    apply_order_situacao,
    record_stock_movement,
    return_product_to_bling_stock,
    reverse_stock_movement,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/api/devolutions", tags=["devolutions"])

_REFUND_CONDICOES = {"Extraviado", "Manutenção"}
SAO_PAULO = ZoneInfo("America/Sao_Paulo")


def _maybe_create_refund(session: AsyncSession, row: Devolution, condicao: str) -> None:
    prejuizo = (
        row.custo_produto or 0
        if condicao == "Extraviado"
        else row.custo_manutencao or 0
    )
    refund = Refund(
        data=row.data,
        pedido_bling=row.pedido_bling,
        pedido_marketplace=row.pedido_marketplace,
        conta=row.conta,
        tipo=condicao,
        prejuizo=prejuizo,
        reembolso=0,
    )
    session.add(refund)
    logger.info("refund_auto_created", pedido_bling=row.pedido_bling, tipo=condicao)
settings = get_settings()
SCHEMA = settings.database_schema


def _search_clause(search: str):
    q = f"%{search}%"
    return or_(
        Devolution.pedido_bling.ilike(q),
        Devolution.pedido_marketplace.ilike(q),
        Devolution.conta.ilike(q),
        Devolution.sku.ilike(q),
        Devolution.tag.ilike(q),
        Devolution.produtos.ilike(q),
        Devolution.condicao_produto.ilike(q),
        Devolution.motivo_devolucao.ilike(q),
        Devolution.tecnico.ilike(q),
        Devolution.observacao.ilike(q),
    )


def _build_where(
    search: str | None,
    reembolso: bool | None,
    tag: str | None,
    data_devolucao: date | None,
    condicao: str | None,
) -> list:
    where = []
    if search and search.strip():
        where.append(_search_clause(search.strip()))
    if reembolso is not None:
        where.append(Devolution.reembolso.is_(reembolso))
    if tag and tag.strip() and tag.strip().lower() != "all":
        where.append(Devolution.tag == tag.strip().lower().lstrip("."))
    if condicao and condicao.strip() and condicao.strip().lower() != "all":
        where.append(Devolution.condicao_produto == condicao.strip())
    # "Data de devolução" = quando o registro entrou no sistema (created_at).
    if data_devolucao is not None:
        start = datetime.combine(data_devolucao, time.min, tzinfo=SAO_PAULO)
        end = start + timedelta(days=1)
        where.append(Devolution.created_at >= start.astimezone(UTC))
        where.append(Devolution.created_at < end.astimezone(UTC))
    return where


@router.get("", response_model=DevolutionPage)
async def list_devolutions(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("devolucoes", "view"))],
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    search: str | None = Query(None),
    reembolso: bool | None = Query(None),
    tag: str | None = Query(None),
    data_devolucao: date | None = Query(None),
    condicao: str | None = Query(None),
) -> DevolutionPage:
    where = _build_where(search, reembolso, tag, data_devolucao, condicao)

    stmt = (
        select(Devolution)
        .where(*where)
        .order_by(desc(Devolution.data).nulls_last(), desc(Devolution.created_at))
        .limit(limit)
        .offset(offset)
    )
    count_stmt = select(func.count()).select_from(Devolution).where(*where)

    items = (await session.execute(stmt)).scalars().all()
    total = (await session.execute(count_stmt)).scalar_one()

    return DevolutionPage(
        items=[DevolutionOut.model_validate(item) for item in items],
        total=int(total or 0),
        limit=limit,
        offset=offset,
    )


def _fmt_dt_sp(dt: datetime | None) -> str:
    """Datetime → 'dd/mm/aaaa HH:MM' no fuso de São Paulo (vazio se None)."""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(SAO_PAULO).strftime("%d/%m/%Y %H:%M")


_EXPORT_COLUMNS: list[tuple[str, str]] = [
    ("Data", "data"),
    ("Data Devolução", "created_at"),
    ("Pedido Bling", "pedido_bling"),
    ("Pedido Marketplace", "pedido_marketplace"),
    ("Conta", "conta"),
    ("SKU", "sku"),
    ("Tag", "tag"),
    ("Produtos", "produtos"),
    ("Custo produto", "custo_produto"),
    ("Condição", "condicao_produto"),
    ("Link abertura", "link_abertura"),
    ("Reembolso", "reembolso"),
    ("Motivo", "motivo_devolucao"),
    ("Custo manutenção", "custo_manutencao"),
    ("Técnico", "tecnico"),
    ("Qtd", "quantidade"),
    ("Devolver estoque", "devolver_estoque"),
    ("Data devolvido estoque", "data_devolvido_estoque"),
    ("Observação", "observacao"),
]


@router.get("/export.xlsx")
async def export_devolutions(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("devolucoes", "view"))],
    search: str | None = Query(None),
    reembolso: bool | None = Query(None),
    tag: str | None = Query(None),
    data_devolucao: date | None = Query(None),
    condicao: str | None = Query(None),
) -> StreamingResponse:
    """Exporta as devoluções (com os mesmos filtros da lista) em XLSX."""
    where = _build_where(search, reembolso, tag, data_devolucao, condicao)
    rows = (
        await session.execute(
            select(Devolution)
            .where(*where)
            .order_by(desc(Devolution.data).nulls_last(), desc(Devolution.created_at))
        )
    ).scalars().all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Devoluções"
    ws.append([label for label, _ in _EXPORT_COLUMNS])

    for r in rows:
        line = []
        for _, field in _EXPORT_COLUMNS:
            value = getattr(r, field, None)
            if field in ("data", "created_at", "data_devolvido_estoque"):
                line.append(_fmt_dt_sp(value))
            elif field in ("reembolso", "devolver_estoque"):
                line.append("Sim" if value else "Não")
            else:
                line.append(value if value is not None else "")
        ws.append(line)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    stamp = datetime.now(SAO_PAULO).strftime("%Y%m%d_%H%M")
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="devolucoes_{stamp}.xlsx"'},
    )


@router.get("/order-lookup", response_model=list[DevolutionLookupOut])
async def lookup_devolution_order(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("devolucoes", "view"))],
    pedido: str = Query(..., min_length=1),
) -> list[DevolutionLookupOut]:
    pedido = pedido.strip()
    if not pedido:
        raise HTTPException(422, detail={"code": "pedido_required"})

    q_like = f"%{pedido}%"
    rows = (
        await session.execute(
            text(
                f"""
                SELECT
                    v.data,
                    v.pedido_bling::text AS pedido_bling,
                    v.pedido_marketplace::text AS pedido_marketplace,
                    COALESCE(NULLIF(btrim(v.loja_nome), ''), 'Loja ' || v.bling_loja_id) AS conta,
                    v.sku,
                    v.produto AS produtos,
                    1 AS quantidade,
                    COALESCE(bo.preco_custo::numeric, 0)::double precision AS custo_produto,
                    v.nome_destinatario,
                    v.cep_destino,
                    v.endereco_destino,
                    v.numero_destino,
                    v.complemento_destino,
                    v.bairro_destino,
                    v.cidade_destino,
                    v.uf_destino
                FROM "{SCHEMA}".vw_devolucoes v
                LEFT JOIN "{SCHEMA}".bling_orders bo ON bo.id = v.bling_order_item_id
                CROSS JOIN generate_series(1, GREATEST(1, COALESCE(v.quantidade::int, 1))) gs(unit_num)
                WHERE (
                    v.pedido_bling::text = :pedido
                    OR v.pedido_marketplace::text = :pedido
                    OR v.nome_destinatario ILIKE :q_like
                    OR v.cep_destino ILIKE :q_like
                )
                ORDER BY v.data DESC NULLS LAST, v.pedido_bling, v.sku, gs.unit_num
                LIMIT 50
                """  # noqa: S608
            ),
            {"pedido": pedido, "q_like": q_like},
        )
    ).mappings().all()

    expanded = _split_mala_sizes(_split_compound_skus([dict(r) for r in rows]))

    part_skus = sorted({
        s for r in expanded
        if (s := (r["sku"] or "").strip().lower())
    })
    products: dict[str, dict] = {}
    if part_skus:
        prod_rows = (
            await session.execute(
                text(
                    f"""
                    SELECT DISTINCT ON (lower(btrim(sku)))
                        lower(btrim(sku)) AS k,
                        name,
                        cost_price::double precision AS cost_price
                    FROM "{SCHEMA}".products
                    WHERE lower(btrim(sku)) = ANY(:keys)
                    ORDER BY lower(btrim(sku)), situacao = 'A' DESC NULLS LAST
                    """  # noqa: S608
                ),
                {"keys": part_skus},
            )
        ).mappings().all()
        products = {p["k"]: dict(p) for p in prod_rows}

    for r in expanded:
        if not r.get("_compound"):
            continue
        prod = products.get((r["sku"] or "").strip().lower())
        if prod:
            r["produtos"] = prod["name"]
            if prod["cost_price"] is not None:
                r["custo_produto"] = prod["cost_price"]

    return [DevolutionLookupOut.model_validate({k: v for k, v in r.items() if not k.startswith("_")}) for r in expanded]


def _split_compound_skus(rows: list[dict]) -> list[dict]:
    """Expand rows whose SKU is a '+'-joined kit into one row per component SKU.

    Component name/cost are resolved later from the products table; the split
    cost here is only a fallback for components missing from the catalog.
    """
    out: list[dict] = []
    for row in rows:
        sku = (row.get("sku") or "").strip()
        if "+" in sku:
            parts = [p.strip() for p in sku.split("+") if p.strip()]
            if len(parts) > 1:
                base_cost = row.get("custo_produto") or 0.0
                split_cost = base_cost / len(parts) if base_cost else base_cost
                for part in parts:
                    out.append({**row, "sku": part, "custo_produto": split_cost, "_compound": True})
                continue
        out.append(row)
    return out


_MALA_BASE_RE = re.compile(r"^b[0-9]", re.IGNORECASE)
_MALA_SIZE_RE = re.compile(r"^[0-9]+$")


def _split_mala_sizes(rows: list[dict]) -> list[dict]:
    """Mala com vários tamanhos no SKU é, na verdade, várias malas distintas.

    `b004.12.18` = base `b004` + tamanhos `12` e `18` → duas malas separadas
    `b004.12` e `b004.18`, uma linha por tamanho. Só dispara quando a base é
    de mala (`b` + dígito) e TODOS os segmentos após a base são numéricos
    (tamanho) — assim sufixos regionais (`.sp`, `.us`…) nunca são divididos.
    Nome/custo de cada tamanho são resolvidos depois na tabela products; o
    custo dividido aqui é só fallback.
    """
    out: list[dict] = []
    for row in rows:
        sku = (row.get("sku") or "").strip()
        parts = sku.split(".")
        if (
            len(parts) >= 3
            and _MALA_BASE_RE.match(parts[0])
            and all(_MALA_SIZE_RE.match(p) for p in parts[1:])
        ):
            base = parts[0]
            sizes = parts[1:]
            base_cost = row.get("custo_produto") or 0.0
            split_cost = base_cost / len(sizes) if base_cost else base_cost
            for size in sizes:
                out.append({**row, "sku": f"{base}.{size}", "custo_produto": split_cost, "_compound": True})
            continue
        out.append(row)
    return out


@router.get("/product-search", response_model=list[DevolutionProductOut])
async def search_products(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("devolucoes", "view"))],
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=50),
) -> list[DevolutionProductOut]:
    """Busca produtos por SKU/nome para o modal de troca (Modal 1).
    Ativos primeiro; ordenado por SKU."""
    q = q.strip()
    if not q:
        return []
    like = f"%{q}%"
    rows = (
        await session.execute(
            select(Product.sku, Product.name, Product.cost_price)
            .where(or_(Product.sku.ilike(like), Product.name.ilike(like)))
            .order_by((Product.situacao == "A").desc().nullslast(), Product.sku)
            .limit(limit)
        )
    ).all()
    return [
        DevolutionProductOut(
            sku=r.sku,
            name=r.name,
            cost_price=float(r.cost_price) if r.cost_price is not None else None,
        )
        for r in rows
    ]


@router.get("/sku-suffixes", response_model=SkuSuffixesOut)
async def sku_suffixes(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("devolucoes", "view"))],
    sku: str = Query(..., min_length=1),
) -> SkuSuffixesOut:
    """Para o modal `.sp` (Modal 2): dado um SKU, devolve a base, os sufixos
    permitidos (já existentes no sistema — não cria novos) e quais variantes
    `base.<suffix>` já existem na tabela products."""
    base = _sku_base(sku.strip())
    candidates = {f"{base}.{s}": s for s in _SUFFIX_TAGS}
    rows = (
        await session.execute(
            select(Product.sku, Product.name)
            .where(func.lower(Product.sku).in_([k.lower() for k in candidates]))
        )
    ).all()
    found = {r.sku.lower(): r.name for r in rows}
    variants = [
        SkuSuffixVariant(
            suffix=suffix,
            sku=full_sku,
            name=found.get(full_sku.lower()),
            exists=full_sku.lower() in found,
        )
        for full_sku, suffix in candidates.items()
    ]
    return SkuSuffixesOut(base=base, allowed_suffixes=list(_SUFFIX_TAGS), variants=variants)


@router.post("/stock-correction", response_model=BlingStockResultOut)
async def stock_correction(
    body: StockCorrectionIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("devolucoes", "edit"))],
) -> BlingStockResultOut:
    """Correção manual de estoque: adiciona unidades de um SKU ao estoque Bling
    reaproveitando a MESMA lógica de devolução (Novo/Usado → bin existente ou
    criação de z000N.<tag>), porém SEM gravar registro de devolução nem alterar
    a situação de nenhum pedido."""
    condicao = body.condicao_produto
    if condicao not in _STOCK_TRIGGER_CONDICOES:
        raise HTTPException(
            422,
            detail={
                "code": "condicao_invalida",
                "message": f"Condição {condicao!r} não dispara estoque",
            },
        )
    # Linha transiente (NÃO adicionada à sessão): só carrega os campos que
    # return_product_to_bling_stock lê. Nada é persistido.
    row = Devolution(
        conta="Correção de Estoque",
        sku=body.sku,
        produtos=body.produtos,
        custo_produto=body.custo_produto,
        condicao_produto=condicao,
        quantidade=body.quantidade or 1,
        troca_sku=body.troca_sku,
        troca_condicao=body.troca_condicao,
        estoque_destino_sku=body.estoque_destino_sku,
        estoque_nova_tag=body.estoque_nova_tag,
        manutencao_destino=body.manutencao_destino,
    )
    sr = await return_product_to_bling_stock(session, row)
    if sr is None:
        raise HTTPException(
            422,
            detail={"code": "no_stock_action", "message": "Nenhuma ação de estoque para essa condição"},
        )
    logger.info(
        "stock_correction_done",
        sku=body.sku, condicao=condicao, qty=body.quantidade,
        action=sr.get("action"), ok=sr.get("ok"),
    )
    return BlingStockResultOut(**sr)


@router.post("", response_model=DevolutionOut, status_code=status.HTTP_201_CREATED)
async def create_devolution(
    body: DevolutionCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("devolucoes", "edit"))],
) -> DevolutionOut:
    # Manutenção: NÃO devolve estoque no add — só depois, pelo toggle na linha.
    # Aqui só registramos e patchamos a situação (84677, "em manutenção").
    devolver_no_add = bool(body.devolver_estoque) and body.condicao_produto != "Manutenção"
    row = Devolution(
        data=body.data,
        pedido_bling=body.pedido_bling,
        pedido_marketplace=body.pedido_marketplace,
        conta=body.conta,
        sku=body.sku,
        produtos=body.produtos,
        custo_produto=body.custo_produto,
        condicao_produto=body.condicao_produto,
        link_abertura=body.link_abertura,
        reembolso=body.reembolso,
        motivo_devolucao=body.motivo_devolucao,
        custo_manutencao=body.custo_manutencao,
        tecnico=body.tecnico,
        devolver_estoque=devolver_no_add,
        observacao=body.observacao,
        troca_sku=body.troca_sku,
        troca_condicao=body.troca_condicao,
        estoque_suffix=body.estoque_suffix,
        quantidade=body.quantidade or 1,
        estoque_destino_sku=body.estoque_destino_sku,
        estoque_nova_tag=body.estoque_nova_tag,
        manutencao_destino=body.manutencao_destino,
        tag=_sku_tag(body.sku),
        data_devolvido_estoque=datetime.now(UTC) if devolver_no_add else None,
    )
    session.add(row)
    if body.condicao_produto in _REFUND_CONDICOES:
        _maybe_create_refund(session, row, body.condicao_produto)
    await session.commit()
    await session.refresh(row)
    logger.info("devolution_created", id=str(row.id), pedido_bling=row.pedido_bling)
    out = DevolutionOut.model_validate(row)

    # Gatilho no ADD: Novo/Usado/Trocado processam estoque sempre. Manutenção e
    # Extraviado não mexem no estoque no add — só patcham a situação do pedido.
    condicao = body.condicao_produto
    should_stock = condicao in ("Novo", "Usado", "Trocado")
    if should_stock and not row.devolver_estoque:
        # Processou o estoque → marca o toggle e carimba a data.
        row.devolver_estoque = True
        row.data_devolvido_estoque = datetime.now(UTC)
        await session.commit()
        await session.refresh(row)
        out = DevolutionOut.model_validate(row)
    if should_stock:
        sr = await return_product_to_bling_stock(session, row)
        if sr is not None:
            out.bling_stock_result = BlingStockResultOut(**sr)
            record_stock_movement(row, sr)  # persiste p/ permitir estorno depois
            await session.commit()
            await session.refresh(row)
            out = DevolutionOut.model_validate(row)
            out.bling_stock_result = BlingStockResultOut(**sr)
    # Situação do pedido: Extraviado e Manutenção patcham já no add; Novo/Usado/
    # Trocado quando processam o estoque.
    if (condicao in ("Extraviado", "Manutenção") or should_stock) and row.pedido_bling:
        await apply_order_situacao(session, row.pedido_bling, actor_id=user.id)
        await session.commit()  # persiste a linha de auditoria de situação
    return out


@router.patch("/{devolution_id}", response_model=DevolutionOut)
async def patch_devolution(
    devolution_id: UUID,
    body: DevolutionPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("devolucoes", "edit"))],
) -> DevolutionOut:
    row = (
        await session.execute(select(Devolution).where(Devolution.id == devolution_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "devolution_not_found"})

    data = body.model_dump(exclude_unset=True)
    if data.get("conta") is None and "conta" in data:
        raise HTTPException(422, detail={"code": "conta_required"})

    prev_condicao = row.condicao_produto
    prev_devolver_estoque = row.devolver_estoque
    for key, value in data.items():
        setattr(row, key, value)
    if "sku" in data:
        row.tag = _sku_tag(row.sku)

    new_condicao = row.condicao_produto
    if new_condicao in _REFUND_CONDICOES and new_condicao != prev_condicao:
        _maybe_create_refund(session, row, new_condicao)

    # Carimba a data quando o toggle "devolver estoque" passa a TRUE.
    if row.devolver_estoque and (not prev_devolver_estoque or row.data_devolvido_estoque is None):
        row.data_devolvido_estoque = datetime.now(UTC)

    await session.commit()
    await session.refresh(row)

    condicao_changed = new_condicao != prev_condicao
    toggle_turned_on = row.devolver_estoque and not prev_devolver_estoque
    toggle_turned_off = prev_devolver_estoque and not row.devolver_estoque
    should_stock = (
        new_condicao in _STOCK_TRIGGER_CONDICOES
        and row.devolver_estoque
        and (condicao_changed or toggle_turned_on)
    )

    final_sr: dict | None = None
    has_pending_mov = (
        row.estoque_mov_bling_id is not None and row.estoque_mov_revertido_at is None
    )
    # Estorna a entrada anterior quando o operador desliga "devolver estoque" OU
    # quando vamos relançar por mudança de condição/destino — evita estoque
    # duplicado e tira de venda o item que, p.ex., entrou Usado e virou Sucata.
    if has_pending_mov and (toggle_turned_off or should_stock):
        rev = await reverse_stock_movement(session, row)
        await session.commit()
        await session.refresh(row)
        if toggle_turned_off:
            final_sr = rev

    if should_stock:
        sr = await return_product_to_bling_stock(session, row)
        if sr is not None:
            recorded = bool(sr.get("ok") and sr.get("bling_product_id"))
            record_stock_movement(row, sr)
            await session.commit()
            await session.refresh(row)
            if recorded or final_sr is None:
                final_sr = sr

    # Extraviado patcha a situação ao virar Extraviado (sem depender do toggle).
    extraviado_now = new_condicao == "Extraviado" and condicao_changed
    if (extraviado_now or should_stock) and row.pedido_bling:
        await apply_order_situacao(session, row.pedido_bling, actor_id=user.id)
        await session.commit()  # persiste a linha de auditoria de situação

    out = DevolutionOut.model_validate(row)
    if final_sr is not None:
        out.bling_stock_result = BlingStockResultOut(**final_sr)
    return out


@router.post("/backfill-addresses", status_code=status.HTTP_200_OK)
async def backfill_addresses(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("devolucoes", "edit"))],
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    """Re-fetch delivery addresses from Bling for devolution orders that have
    NULL nome_destinatario. Calls GET /pedidos/vendas/{bling_id} individually.
    Returns counts of processed / updated / failed records.
    """
    from app.services.devolution_stock_return import _get_bling_client

    _SITUACOES = ("83957", "83960", "83961", "83966", "84677")

    # Find orders that need backfill
    rows = (
        await session.execute(
            select(BlingOrder.id, BlingOrder.bling_id, BlingOrder.numero)
            .where(
                BlingOrder.situacao.in_(_SITUACOES),
                BlingOrder.nome_destinatario.is_(None),
                BlingOrder.bling_id.is_not(None),
            )
            .order_by(desc(BlingOrder.data))
            .limit(limit)
        )
    ).all()

    if not rows:
        return {"processed": 0, "updated": 0, "failed": 0, "message": "Nenhum pedido sem endereço encontrado"}

    client = await _get_bling_client(session)
    if client is None:
        raise HTTPException(503, detail={"code": "no_bling_integration"})

    processed = updated = failed = 0

    for row_id, bling_id, numero in rows:
        processed += 1
        try:
            raw = await client.get_order(bling_id)
            if not raw:
                failed += 1
                continue

            # Extract address using same logic as bling_orders.py
            transporte = raw.get("transporte") or {}
            transporte = transporte if isinstance(transporte, dict) else {}
            t_contato = transporte.get("contato") or {}
            t_contato = t_contato if isinstance(t_contato, dict) else {}
            t_endereco = transporte.get("enderecoEntrega") or {}
            t_endereco = t_endereco if isinstance(t_endereco, dict) else {}
            buyer = raw.get("contato") or {}
            buyer = buyer if isinstance(buyer, dict) else {}
            buyer_end = buyer.get("endereco") or {}
            buyer_end = buyer_end if isinstance(buyer_end, dict) else {}

            def _v(tp_f: str, buyer_f: str | None = None) -> str | None:
                return t_endereco.get(tp_f) or buyer_end.get(buyer_f or tp_f) or None

            values: dict = {}
            nome = t_contato.get("nome") or buyer.get("nome") or None
            if nome:
                values["nome_destinatario"] = nome
            for col, tp_f in [
                ("cep_destino", "cep"),
                ("endereco_destino", "endereco"),
                ("numero_destino", "numero"),
                ("complemento_destino", "complemento"),
                ("bairro_destino", "bairro"),
                ("cidade_destino", "municipio"),
                ("uf_destino", "uf"),
            ]:
                val = _v(tp_f)
                if val:
                    values[col] = val

            if values:
                await session.execute(
                    update(BlingOrder).where(BlingOrder.id == row_id).values(**values)
                )
                updated += 1
                logger.info("devolution_address_backfill_ok", numero=numero, fields=list(values))
            else:
                logger.info("devolution_address_backfill_no_data", numero=numero)

        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger.warning("devolution_address_backfill_error", numero=numero, error=str(exc))

    await session.commit()
    logger.info("devolution_address_backfill_done", processed=processed, updated=updated, failed=failed)
    return {
        "processed": processed,
        "updated": updated,
        "failed": failed,
        "message": f"{updated} de {processed} pedidos atualizados com endereço",
    }


@router.delete("/{devolution_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_devolution(
    devolution_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("devolucoes", "delete"))],
) -> None:
    row = (
        await session.execute(select(Devolution).where(Devolution.id == devolution_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "devolution_not_found"})
    await session.delete(row)
    await session.commit()
    logger.info("devolution_deleted", id=str(devolution_id))
    return None
