"""Refresh-stock-only job — manual quick path.

Iterates every Bling integration the user owns, paginates `/produtos`
100-at-a-time, and writes only `stock` (and `min_stock` when present) to
local `products` + `product_links`. No marketplace push, no full
orchestrator pipeline.

Use case: user wants fresh Bling stock without paying the full sync_all
cost (which also touches every marketplace link per product).

Reconciliação de excluídos: como o `/produtos` NÃO devolve produtos
apagados no Bling, o sweep nunca mais os revisita e o `products.situacao`
fica congelado em 'A' (eles seguem aparecendo no Controle de Estoque). No
fim do sweep marcamos `situacao='E'` nos produtos locais 'A'/NULL cujo
bling_product_id sumiu — confirmando 1 a 1 por id (404 = apagado) antes de
rebaixar. Produto só inativo responde 200 e NÃO é mexido; erro transitório
também não rebaixa. Ver `_reconcile_excluidos`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    Integration,
    IntegrationPlatform,
    LinkSyncStatus,
    Product,
    ProductLink,
)
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketplaces.bling import (
    BLING_PRODUCTS_PAGE_SIZE,
    BlingClient,
    parse_bling_product,
)

logger = structlog.get_logger()

DETAILS_MAX = 500

# Máximo de produtos verificados 1-a-1 por execução na reconciliação de
# excluídos. Como os rebaixados saem do conjunto de candidatos, em poucas
# execuções o backlog zera. Throttle pra respeitar o teto de ~3 req/s do
# Bling (o /produtos não dá pra bater em lote por id, só 1 por chamada).
RECONCILE_MAX = 1000
_RECONCILE_SLEEP = 0.34


def _now() -> datetime:
    return datetime.now(UTC)


async def _append_detail(session: AsyncSession, job: BackgroundJob, entry: dict[str, Any]) -> None:
    entry = {"at": _now().isoformat(), **entry}
    current = list(job.details or [])
    current.append(entry)
    if len(current) > DETAILS_MAX:
        current = current[-DETAILS_MAX:]
    job.details = current
    job.last_heartbeat_at = _now()
    await session.commit()


async def _build_client(session: AsyncSession, integ: Integration) -> BlingClient:
    creds = decrypt_json(integ.credentials)

    async def _persist(new_creds: dict, _it=integ, _s=session) -> None:
        _it.credentials = encrypt_json(new_creds)
        exp = new_creds.get("expires_at")
        if exp:
            _it.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
        await _s.commit()

    return BlingClient(creds, on_token_refresh=_persist, integration_id=integ.id)


async def _reconcile_excluidos(
    session: AsyncSession,
    clients: list[BlingClient],
    product_by_bpid: dict[int, Product],
    seen_bpids: set[int],
    job: BackgroundJob,
    summary: dict[str, Any],
) -> None:
    """Marca situacao='E' nos produtos locais que foram APAGADOS no Bling.

    Candidato = produto local 'A' (ou NULL) cujo bling_product_id NÃO voltou
    no sweep do /produtos (Bling não lista apagados). Cada candidato é
    confirmado 1 a 1 antes de qualquer escrita:

      * get_product(id) → 404 e nenhum produto ATIVO com o mesmo SKU
            → apagado de fato → situacao='E'.
      * get_product(id) → 404 mas existe ativo com o mesmo SKU
            → foi recriado sob id novo → atualiza bling_product_id, mantém 'A'.
      * get_product(id) → 200 com situacao 'E' (excluído mas ainda buscável)
            → situacao='E'.
      * get_product(id) → 200 ativo/inativo, OU erro transitório (timeout,
        429, 5xx) → NÃO mexe. Produto só inativo responde 200, então nunca
        é rebaixado por engano; e falha de rede nunca vira exclusão.

    Trava de segurança: se o sweep não devolveu NENHUM id (Bling fora/erro de
    token), aborta sem tocar em nada — senão TODO produto local viraria
    candidato. Cap de RECONCILE_MAX por execução; como os rebaixados saem do
    conjunto, o backlog converge em poucas rodadas.
    """
    if not clients or not seen_bpids:
        summary["reconcile_skipped"] = "no_sweep_data"
        logger.warning("reconcile_excluidos_skipped", reason="no_sweep_data")
        return

    candidates = [
        p
        for bpid, p in product_by_bpid.items()
        if bpid not in seen_bpids and (p.situacao in ("A", None))
    ]
    summary["reconcile_candidates"] = len(candidates)
    if not candidates:
        return

    demoted_skus: list[str] = []
    checked = 0
    for p in candidates:
        if checked >= RECONCILE_MAX:
            summary["reconcile_truncated"] = True
            break
        if p.bling_product_id is None:
            continue
        bpid = int(p.bling_product_id)
        checked += 1

        resolved = False  # achou por id em alguma integração → não apagado
        bling_excluido = False
        deleted_404 = False
        for client in clients:
            try:
                raw = await client.get_product(bpid)
                if (raw.get("situacao") or "").upper() in ("E", "EXCLUIDO"):
                    bling_excluido = True
                resolved = True
                break
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code if exc.response is not None else None
                if code == 404:
                    deleted_404 = True
                    continue  # tenta a próxima integração
                resolved = True  # transitório/outro → não rebaixa
                break
            except Exception:  # noqa: BLE001
                resolved = True  # rede/etc → não rebaixa
                break

        if resolved:
            if bling_excluido:
                p.situacao = "E"
                summary["reconciled_excluido"] += 1
                if len(demoted_skus) < 200:
                    demoted_skus.append(p.sku)
        elif deleted_404:
            # Sumiu por id em todas as integrações. Pode ter sido recriado
            # sob id novo com o mesmo SKU — nesse caso é cura, não exclusão.
            active = None
            for client in clients:
                try:
                    active = await client.find_active_product_by_sku(p.sku)
                except Exception:  # noqa: BLE001
                    active = None
                if active:
                    break
            if active and active.get("id"):
                p.bling_product_id = int(active["id"])
                summary["reconciled_healed"] += 1
            else:
                p.situacao = "E"
                summary["reconciled_excluido"] += 1
                if len(demoted_skus) < 200:
                    demoted_skus.append(p.sku)

        if checked % 50 == 0:
            job.last_heartbeat_at = _now()
            await session.commit()
        await asyncio.sleep(_RECONCILE_SLEEP)

    summary["excluido_skus"] = demoted_skus
    await session.commit()
    logger.info(
        "reconcile_excluidos_done",
        candidates=len(candidates),
        checked=checked,
        excluido=summary.get("reconciled_excluido", 0),
        healed=summary.get("reconciled_healed", 0),
        truncated=summary.get("reconcile_truncated", False),
    )


async def run_refresh_bling_stock(
    session: AsyncSession,
    *,
    job_id: UUID,
) -> None:
    job = await session.get(BackgroundJob, job_id)
    if job is None:
        logger.error("refresh_bling_stock_job_missing", job_id=str(job_id))
        return

    job.status = BackgroundJobStatus.RUNNING
    job.started_at = _now()
    job.last_heartbeat_at = _now()
    await session.commit()

    integrations = (
        await session.execute(
            select(Integration).where(Integration.platform == IntegrationPlatform.BLING)
        )
    ).scalars().all()

    if not integrations:
        job.status = BackgroundJobStatus.SUCCEEDED
        job.result = {"updated": 0, "missing_local": 0, "pages": 0, "integrations": 0}
        job.finished_at = _now()
        await session.commit()
        return

    summary = {
        "updated": 0,
        "missing_local": 0,
        "pages": 0,
        "integrations": len(integrations),
        "reconcile_candidates": 0,
        "reconciled_excluido": 0,
        "reconciled_healed": 0,
    }

    # Match Bling products to local products by Product.bling_product_id
    # (canonical), not by ProductLink — many tenants don't maintain a Bling
    # self-link and rely on the column instead.
    products = (
        await session.execute(
            select(Product).where(Product.bling_product_id.is_not(None))
        )
    ).scalars().all()
    product_by_bpid = {
        int(p.bling_product_id): p
        for p in products
        if p.bling_product_id is not None
    }

    # bling_product_ids que o sweep do /produtos devolveu (todas integrações).
    # Produto local com bpid que NÃO aparece aqui é candidato a "excluído no
    # Bling" — confirmado depois em _reconcile_excluidos. `clients` é reusado
    # lá pra a verificação 1-a-1 (evita reconstruir credenciais/refresh).
    seen_bpids: set[int] = set()
    clients: list[BlingClient] = []

    try:
        for integ in integrations:
            await _append_detail(
                session,
                job,
                {
                    "integration_id": str(integ.id),
                    "phase": "start",
                    "platform": "bling",
                },
            )
            client = await _build_client(session, integ)
            clients.append(client)

            bling_links = (
                await session.execute(
                    select(ProductLink).where(
                        ProductLink.integration_id == integ.id,
                        ProductLink.platform == IntegrationPlatform.BLING,
                    )
                )
            ).scalars().all()
            bling_link_by_external = {
                str(link.external_id): link for link in bling_links
            }

            page = 1
            while True:
                items = await client.list_products_page(
                    pagina=page, limite=BLING_PRODUCTS_PAGE_SIZE
                )
                if not items:
                    break

                page_updated = 0
                page_missing = 0
                for raw in items:
                    parsed = parse_bling_product(raw)
                    bpid = parsed.get("bling_product_id")
                    new_stock = parsed.get("stock")
                    # Registra o id como "existe no Bling" antes de qualquer
                    # filtro — mesmo sem stock, o produto está vivo no catálogo
                    # e não pode ser candidato a excluído.
                    if bpid is not None:
                        seen_bpids.add(int(bpid))
                    if bpid is None or new_stock is None:
                        continue
                    product = product_by_bpid.get(int(bpid))
                    if product is None:
                        page_missing += 1
                        continue
                    product.stock = int(new_stock)
                    if parsed.get("min_stock") is not None:
                        product.min_stock = int(parsed["min_stock"])
                    # Backfill situacao/formato em produtos pré-existentes
                    # que ainda têm NULL (criados antes do fix do parse).
                    # Não sobrescrevemos valor já populado pra preservar
                    # qualquer correção manual feita no DB.
                    if product.situacao is None and parsed.get("situacao"):
                        product.situacao = parsed["situacao"]
                    if product.formato is None and parsed.get("formato"):
                        product.formato = parsed["formato"]
                    bling_link = bling_link_by_external.get(str(bpid))
                    if bling_link is not None:
                        bling_link.stock = int(new_stock)
                        bling_link.last_sync_status = LinkSyncStatus.OK
                        bling_link.last_sync_at = _now()
                        bling_link.last_error = None
                    page_updated += 1

                summary["updated"] += page_updated
                summary["missing_local"] += page_missing
                summary["pages"] += 1
                job.processed = (job.processed or 0) + len(items)
                if job.total < job.processed:
                    job.total = job.processed
                await _append_detail(
                    session,
                    job,
                    {
                        "integration_id": str(integ.id),
                        "page": page,
                        "fetched": len(items),
                        "updated": page_updated,
                        "missing_local": page_missing,
                    },
                )

                if len(items) < BLING_PRODUCTS_PAGE_SIZE:
                    break
                page += 1

        # Sweep ok → reconcilia os produtos locais que sumiram do Bling.
        # Roda só aqui dentro do try (se o sweep falhou, vai pro except e
        # NÃO rebaixa nada — partial sweep nunca dispara exclusão em massa).
        await _reconcile_excluidos(
            session, clients, product_by_bpid, seen_bpids, job, summary,
        )

        job.status = BackgroundJobStatus.SUCCEEDED
        job.result = summary
    except Exception as e:  # noqa: BLE001
        logger.exception("refresh_bling_stock_failed", job_id=str(job_id))
        job.status = BackgroundJobStatus.FAILED
        job.error = f"{type(e).__name__}: {e}"[:1000]
        job.result = summary
    finally:
        job.finished_at = _now()
        await session.commit()
