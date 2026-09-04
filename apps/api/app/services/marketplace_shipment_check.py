"""Polls marketplace APIs (Shopee/ML/Amazon) for shipped-status changes
on Bling orders that haven't yet transitioned to situacao=15 ("Em
andamento") in Bling.

Why this exists:
  Bling does NOT auto-update situacao when a carrier scans a package on
  the marketplace side. The marketplace knows (status becomes SHIPPED /
  shipped / Shipped), but Bling stays on the "etiqueta enviada" state:
  native 21 ("Em digitação", canonical since 2026-09-03) or the legacy
  custom 83965 ("Enviado Etiqueta", historical orders). The
  /controle-estoque page filters by `em_andamento_data` so these
  shipped-but-not-bumped orders never appear.

Strategy:
  1. Find candidates: situacao IN (21, 83965-legacy, 6) within the
     window (see _load_candidates). DISTINCT by (bling_id, numeroloja,
     loja) — bling_orders has one row per item.
  2. Group by Bling store id (the `loja` column).
  3. Per store: resolve Store → Integration → marketplace client.
  4. Query the marketplace for each candidate's shipment status. Shopee
     supports up to 50 order_sns per call; ML/Amazon are one-at-a-time.
  5. For each shipped order: bump Bling situacao to 15 via PATCH, then
     stamp em_andamento_data on the local row(s).

Ordering of the two writes matters: Bling first, then local. If Bling
fails we don't touch the local DB — next 5-min tick will retry. If
local fails after Bling succeeds, the Bling webhook will eventually
re-fire `pedido.alteracao.situacao` and `upsert_order` will fill
em_andamento_data via the `_row_from_item` patch. Either way the row
ends up correct.
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import structlog
from sqlalchemy import and_, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import BlingOrder, Integration, IntegrationPlatform
from app.models.company import Store
from app.security.cipher import decrypt_json, encrypt_json
from app.services.bling_situacoes import SITUACOES_ENVIADO_ETIQUETA_STR
from app.services.margem_audit import record_margem_audit
from app.services.marketplaces.amazon import AmazonClient
from app.services.marketplaces.bling import BlingClient
from app.services.marketplaces.ml import MercadoLivreClient
from app.services.marketplaces.shopee import ShopeeClient
from app.services.marketplaces.tiktok import TikTokClient
from app.services.advisory_lock import SYNC_NAMESPACE

logger = structlog.get_logger()

# Chave do advisory lock que serializa o sweep (namespace SYNC compartilhado).
_SWEEP_LOCK_KEY = 0x73686970  # ascii "ship"

# Pre-shipment states the sweep considers: 21 (Bling native "Em digitação"
# — the etiqueta-enviada state since 2026-09-03), 83965 (legacy custom
# "Enviado Etiqueta", historical orders still carry it) and 6 (Bling stock
# "Em aberto" — orders imported directly from Bling never touch the
# etiqueta state and were silently skipped before 6 joined this list).
# Other 8xxx custom statuses exist but don't represent a still-shippable
# state for this account. All three mean "not yet flagged shipped"; the
# marketplace verification logic downstream is what actually decides to
# bump to 15 (Em andamento) — it never marks an order shipped without
# the marketplace confirming.
_OPEN_SITUACOES: tuple[str, ...] = (*SITUACOES_ENVIADO_ETIQUETA_STR, "6")
_SHIPPED_SITUACAO = 15  # Bling system situacao "Em andamento".
# 30 dias (era 7). Pedido RETIDO no marketplace é postado depois do 7º dia
# e saía do radar exatamente antes de despachar — 3 casos reais presos em
# ago/2026 (290463 criado 14/08 e postado 24/08; 290728 criado 16/08 e
# postado 25/08). Custo medido em prod (25/08): 140 → 141 candidatos, pois
# pedido normal vira 15 em 1-2 dias e sai do pool sozinho — quem fica além
# de 7 dias são só os retidos. Lixo antigo (2025, mai/2026) continua fora.
_CANDIDATE_WINDOW = timedelta(days=30)

# Marketplace status strings that mean "package on the way". Compared
# case-insensitively (we upper() the response before lookup).
_SHOPEE_SHIPPED = {"SHIPPED", "TO_RETURN", "COMPLETED"}
_ML_SHIPPED = {"shipped", "delivered"}
_AMAZON_SHIPPED = {"Shipped"}
# EasyShipShipmentStatus que CONFIRMAM que o pacote saiu da mão do vendedor.
# Lista de INCLUSÃO, igual à do ML e pelo mesmo motivo: o `order_status` da
# Amazon vira "Shipped" quando a NF é emitida, muito antes de o pacote sair, e
# quem sabe a verdade é o EasyShip. A checagem antiga só barrava
# "PendingPickUp" e deixava passar "PendingDropOff" — que é o caso em que NÓS
# é que temos de levar o pacote ao ponto de entrega, ou seja, ele está parado
# aqui dentro. Eduardo, 04/09: "ele ta jogando todos os pedidos para em
# andamento antes de ser enviado realmente". Estado novo/desconhecido conta
# como NÃO enviado — melhor o pedido esperar do que constar enviado sem ter saído.
_AMAZON_EASYSHIP_SAIU = {
    "PickedUp",
    "DroppedOff",
    "AtDestinationFC",
    "OutForDelivery",
    "Delivered",
    "RejectedByBuyer",
    "Undeliverable",
    "ReturningToSeller",
    "ReturnedToSeller",
    "Damaged",
    "Lost",
}
# TikTok Shop (Order API 202309) — vocabulário próprio, mesmo mapa usado em
# logistica_rules.TIKTOK_STATUS_LABELS_PT. Só entram os estados em que o
# pacote JÁ saiu da mão do vendedor, mesmo critério do Shopee/Amazon:
# AWAITING_COLLECTION (etiqueta pronta, transportadora não coletou) fica de
# fora igual "PendingPickUp" da Amazon; PARTIALLY_SHIPPING é ambíguo (parte
# do pedido) e também fica fora.
_TIKTOK_SHIPPED = {"IN_TRANSIT", "DELIVERED", "COMPLETED"}

# ML shipment substatuses where the seller has NOT yet handed the package.
# Any OTHER substatus under status=ready_to_ship means the package already
# left the seller's hands (dropped_off, in_packing_list, in_hub, first_mile,
# …). On the ML seller UI those all show up as "A caminho", so we treat
# them as shipped on our side too.
# ML shipment substatuses that CONFIRM the package left the seller's
# hands. Inclusion list, not exclusion — any unknown/new substatus
# (e.g. `invoice_pending` which ML emits while the NF isn't generated
# yet) is treated as NOT shipped. Previous "exclusion" approach
# (everything outside {pending, printed, ready_to_print} = shipped)
# caused 335 false-positive bumps on 2026-05-26 because invoice_pending
# wasn't in the exclusion set.
_ML_CONFIRMED_SHIPPED_SUBSTATUS = {
    "dropped_off",       # delivered to agency / collection point
    "in_hub",            # at ML distribution center
    "first_mile",        # in first-mile transit (already picked up)
    "in_packing_list",   # on the carrier's pickup list (handed over)
    "picked_up",         # picked up by carrier
}

# Brasília is UTC-3 (no DST since 2019). em_andamento_data is the
# operator-facing ship-date column on the planilha; storing it in BRT
# avoids the "23h BRT order shows up as next day" off-by-one and lets
# late-detected orders (sweep skipped a weekend) carry the real ship
# date instead of the day the sweep finally noticed.
_BRT = ZoneInfo("America/Sao_Paulo")

# Quantas consultas por pedido rodam em paralelo dentro de UMA loja. ML e
# Amazon não têm endpoint em lote (Shopee/TikTok têm), então antes o sweep
# fazia N requests em fila indiana — com o cron a cada 1 min isso não cabe
# na janela. 6 é conservador pro rate limit dos dois (ML ~ centenas/min,
# Amazon getOrder 0,5 req/s sustentado com burst 30).
_PER_ORDER_CONCURRENCY = 6

# Pedidos que o marketplace responde 404 ("não é meu") ficam nos candidatos
# até saírem da janela de 7 dias e eram re-consultados a cada tick — 2.768
# chamadas ML inúteis em 12h para 21 pedidos. Guardamos o instante do último
# 404 em memória do worker e só re-tentamos depois de _NOT_FOUND_BACKOFF.
# Em memória de propósito: zera no deploy/restart (auto-cura se o 404 era
# transitório) e não precisa de coluna nova.
_NOT_FOUND_BACKOFF = timedelta(minutes=30)
_not_found_until: dict[str, float] = {}


def _skip_not_found(key: str) -> bool:
    """True se esse pedido levou 404 recentemente (ainda em backoff)."""
    until = _not_found_until.get(key)
    if until is None:
        return False
    if time.monotonic() >= until:
        del _not_found_until[key]
        return False
    return True


def _mark_not_found(key: str) -> None:
    _not_found_until[key] = time.monotonic() + _NOT_FOUND_BACKOFF.total_seconds()


# Compra de carrinho no ML: o Bling grava o PACK id em numeroloja e
# `/orders/{pack}` responde 404 — em 06/08 eram 12 dos 18 pedidos ML
# "não enviados" do dia, presos pra sempre. `/packs/{id}` devolve os
# pedidos reais (todos compartilham o MESMO envio, checar o primeiro
# basta). O mapa pack→pedido real é imutável, então fica em cache no
# processo — 1 resolução por pedido por vida do worker.
_ml_pack_real_order: dict[str, str] = {}


def _operational_ship_date(ship_dt: datetime) -> date:
    """Retorna a data BRT em que o evento aconteceu. Sem cutoff —
    operador quer ver o dia exato em que a etiqueta foi gerada,
    independente do horário.

    Cutoff anterior de 10h BRT (eventos antes caíam no dia anterior)
    foi removido em 2026-06-03 por decisão operacional: confundia
    operador ao mostrar pedidos carimbados no dia errado quando
    gerava etiqueta cedo (ex: 6h-9h BRT). Reprodução em 03/06: 91
    pedidos com etiqueta gerada entre 6h-9h BRT do dia 03 cairam
    em 02/06 e foram corrigidos manualmente.

    Domingo nunca é dia de expedição → a data operacional rola para a
    segunda-feira (ex.: etiqueta no domingo 21/06 aparece em 22/06).
    Sábado é caso-a-caso (feriado/plantão) e NÃO é rolado automaticamente
    — decisão do dono, tratado manualmente quando aparece.
    """
    d = ship_dt.astimezone(_BRT).date()
    if d.weekday() == 6:  # domingo
        d += timedelta(days=1)
    return d


def _epoch_to_brt_date(update_time: Any) -> date | None:
    """`update_time` em unix epoch UTC (Shopee e TikTok usam esse formato)
    → data operacional BRT."""
    if not update_time:
        return None
    try:
        dt = datetime.fromtimestamp(int(update_time), tz=UTC)
        return _operational_ship_date(dt)
    except (TypeError, ValueError):
        return None


def _iso_to_brt_date(iso_str: Any) -> date | None:
    """ISO-8601 (ML last_updated, Amazon LastUpdateDate, …) → data BRT
    via _operational_ship_date (sem cutoff)."""
    if not iso_str:
        return None
    try:
        s = str(iso_str)
        # Python's fromisoformat accepts "Z" since 3.11 but normalising
        # to "+00:00" keeps us defensive against older runtimes.
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return _operational_ship_date(dt)
    except (TypeError, ValueError):
        return None


def _ml_corte_agencia(dl: datetime) -> datetime:
    """Corte EFETIVO de pedido ML postado em agência (Itu/Sumaré).

    Agência é reconhecida pela hora: o SLA do ML vem 23:59 BRT ("conta o
    dia"), enquanto coleta/cross-docking traz hora real (confirmado no
    banco em 17/08: 181×23:59:59 vs 12:00/13:00/16:15). A operação leva
    os pacotes às agências na MANHÃ SEGUINTE ao dia em que o pedido cai
    — postar no outro dia não é atraso. Logo, SLA 23:59 de hoje (ou
    passado) ganha +1 dia; data futura fica como veio (o ML já embutiu
    a folga do próprio corte da agência)."""
    dl_brt = dl.astimezone(_BRT)
    if (dl_brt.hour, dl_brt.minute) != (23, 59):
        return dl  # coleta/cross-docking: hora real de coleta vale como veio
    if dl_brt.date() > datetime.now(tz=_BRT).date():
        return dl
    return dl + timedelta(days=1)


def _epoch_to_utc_dt(v: Any) -> datetime | None:
    """Epoch (segundos, UTC) → datetime tz-aware. Usado pros prazos de
    despacho (Shopee `ship_by_date`, TikTok `rts_sla_time`)."""
    if not v:
        return None
    try:
        return datetime.fromtimestamp(int(v), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def _iso_to_utc_dt(v: Any) -> datetime | None:
    """ISO-8601 (Amazon `LatestShipDate`, ML `sla.expected_date`) →
    datetime tz-aware normalizado pra UTC."""
    if not v:
        return None
    try:
        s = str(v)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except (TypeError, ValueError):
        return None


async def run_check_marketplace_shipped_orders() -> dict[str, int]:
    """One sweep. Returns counters for logging/observability."""
    summary = {
        "candidates": 0, "stores_checked": 0, "shipped_found": 0,
        "bling_updated": 0, "local_updated": 0, "deadlines_updated": 0,
        "errors": 0,
    }
    async with session_scope() as session:
        # Lock transacional: com o cron a cada 1 min (era 5) um tick lento
        # ainda pode estar rodando quando o próximo dispara. Sem o lock os
        # dois consultariam os mesmos candidatos e fariam PATCH duplicado no
        # Bling. Quem não pega o lock sai na hora — o tick seguinte cobre.
        got_lock = bool(
            (
                await session.execute(
                    text("SELECT pg_try_advisory_xact_lock(:ns, :k)"),
                    {"ns": SYNC_NAMESPACE, "k": _SWEEP_LOCK_KEY},
                )
            ).scalar()
        )
        if not got_lock:
            logger.info("shipment_check_already_running")
            summary["skipped_locked"] = 1
            return summary

        # One sweep = one DB transaction. The Bling PATCH calls happen
        # inside the transaction; failures roll back the local write,
        # keeping local and Bling in sync (worst case: Bling stamped,
        # local not — webhook will reconcile).
        candidates = await _load_candidates(session)
        summary["candidates"] = len(candidates)
        if not candidates:
            return summary

        # Group by `loja` (Bling store id) so we can resolve one
        # integration + open one client per group.
        by_loja: dict[str, list[BlingOrder]] = defaultdict(list)
        for o in candidates:
            if o.loja:
                by_loja[str(o.loja)].append(o)
        # bling_id (int) → pedido canônico, p/ a trilha de auditoria de situação.
        cand_by_id: dict[int, BlingOrder] = {
            int(o.bling_id): o for o in candidates if o.bling_id is not None
        }

        bling_integration = await _get_bling_integration(session)
        if bling_integration is None:
            logger.warning("shipment_check_no_bling_integration")
            return summary

        for loja, orders in by_loja.items():
            try:
                store, integration = await _resolve_store_and_integration(session, loja)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "shipment_check_resolve_failed",
                    loja=loja, err=str(e)[:200],
                )
                summary["errors"] += 1
                continue
            if integration is None or store is None:
                logger.info(
                    "shipment_check_no_integration",
                    loja=loja, candidates=len(orders),
                )
                continue
            summary["stores_checked"] += 1

            deadlines: dict[int, datetime] = {}
            try:
                shipped_bling_ids = await _check_marketplace_shipped(
                    session, integration, orders, deadlines,
                )
            except Exception as e:  # noqa: BLE001
                logger.exception(
                    "shipment_check_marketplace_query_failed",
                    loja=loja, platform=integration.platform.value, err=str(e)[:200],
                )
                summary["errors"] += 1
                continue
            summary["shipped_found"] += len(shipped_bling_ids)

            # Prazo de despacho ("despachar até") capturado nas MESMAS
            # consultas acima — carimba mesmo quando nada foi enviado (é
            # justamente o pedido parado que precisa do corte na tela).
            # Cobre todas as linhas do bling_id (pedido multi-item);
            # IS DISTINCT FROM evita reescrever valor idêntico a cada tick.
            for _bid, _dl in deadlines.items():
                res = await session.execute(
                    update(BlingOrder)
                    .where(BlingOrder.bling_id == _bid)
                    .where(
                        BlingOrder.marketplace_ship_deadline.is_distinct_from(_dl)
                    )
                    .values(marketplace_ship_deadline=_dl)
                )
                if res.rowcount:
                    summary["deadlines_updated"] += 1

            if not shipped_bling_ids:
                continue

            bling_client = await _build_bling_client(session, bling_integration)
            for bling_id, real_ship_date in shipped_bling_ids.items():
                try:
                    await bling_client.update_order_situacao(
                        int(bling_id), _SHIPPED_SITUACAO,
                    )
                    summary["bling_updated"] += 1
                    _cand = cand_by_id.get(int(bling_id))
                    if _cand is not None:
                        await record_margem_audit(
                            session,
                            acao="situacao",
                            pedido_bling=str(_cand.numero),
                            bling_id=bling_id,
                            sku=_cand.item_codigo,
                            valor_antigo=_cand.situacao,
                            valor_novo=_SHIPPED_SITUACAO,
                            origem="job_envio",
                            mudado_por=None,
                        )
                except httpx.HTTPStatusError as e:
                    # Bling returns 4xx if the order is already in the
                    # target situacao — treat as success and still stamp
                    # local. Other 4xx/5xx are real errors.
                    if e.response.status_code in (409, 422, 400):
                        logger.info(
                            "shipment_check_bling_already_state",
                            bling_id=bling_id, status=e.response.status_code,
                        )
                        # Alguém (integração nativa do Bling / painel) já pôs
                        # o pedido em 15 no Bling ANTES do nosso PATCH. Sem
                        # este registro a trilha fica cega: em 17/08 foram 78
                        # carimbos locais "fantasmas" num dia (corridas
                        # perdidas pro push do canal contra nosso poll de
                        # 1 min) e a auditoria não explicava quem/quando.
                        # Origem própria pra distinguir do flip que foi nosso.
                        _cand = cand_by_id.get(int(bling_id))
                        if _cand is not None:
                            await record_margem_audit(
                                session,
                                acao="situacao",
                                pedido_bling=str(_cand.numero),
                                bling_id=bling_id,
                                sku=_cand.item_codigo,
                                valor_antigo=_cand.situacao,
                                valor_novo=_SHIPPED_SITUACAO,
                                origem="job_envio_espelho",
                                mudado_por=None,
                            )
                    else:
                        logger.warning(
                            "shipment_check_bling_update_failed",
                            bling_id=bling_id, status=e.response.status_code,
                            body=e.response.text[:200],
                        )
                        summary["errors"] += 1
                        continue
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "shipment_check_bling_update_error",
                        bling_id=bling_id, err=str(e)[:200],
                    )
                    summary["errors"] += 1
                    continue

                # Local stamp — covers every row of this order (multi-item).
                # Use the marketplace's reported ship-date when present;
                # fall back to operational-today (BRT, com cutoff
                # de _operational_ship_date) quando o marketplace não
                # devolveu timestamp. Antes de 10h BRT a fallback vai
                # pra ontem — quase certo que é despacho da véspera
                # detectado na madrugada/cedo da manhã.
                ship_date = real_ship_date or _operational_ship_date(datetime.now(UTC))
                # Só carimba em_andamento_data quando ainda está NULL — nunca
                # sobrescreve data correta de pedido já marcado como enviado
                # (ex.: marketplace reporta D+1/D+2 por delay de processamento
                # e re-carimbava dia errado). Situação sempre atualiza — esse
                # serviço existe justamente pra promover pedidos a situacao=15.
                result = await session.execute(
                    update(BlingOrder)
                    .where(BlingOrder.bling_id == int(bling_id))
                    .values(
                        em_andamento_data=func.coalesce(
                            BlingOrder.em_andamento_data, ship_date,
                        ),
                        situacao=str(_SHIPPED_SITUACAO),
                    )
                )
                summary["local_updated"] += result.rowcount or 0

    # `situacoes` no log denuncia worker com imagem velha. Em 04/09 o
    # container do cron rodou 20h com o código anterior ao 912a6a6, cujo
    # _OPEN_SITUACOES era ("83965", "6") — sem o 21, que virou a situação de
    # etiqueta enviada em 03/09. Nenhum pedido novo entrava na varredura e o
    # sintoma era só `bling_updated=0`, indistinguível de "nada a enviar".
    # Com esta linha, um worker defasado aparece como situacoes=83965,6.
    logger.info("shipment_check_done", situacoes=",".join(_OPEN_SITUACOES), **summary)
    return summary


# ─── candidate loading ─────────────────────────────────────────────


async def _load_candidates(session: AsyncSession) -> list[BlingOrder]:
    """One row per (bling_id, numeroloja, loja) — we use item_index=0
    as the canonical row to avoid hitting the marketplace N times for
    one multi-item order.

    Critério: situação em _OPEN_SITUACOES (`21` etiqueta enviada — Em
    digitação; `83965` legado; `6` em aberto) + dentro da janela temporal.
    Antes filtrava `em_andamento_data IS NULL` também, mas o fix e081e0d
    carimba data já na etiqueta (21/83965, provisório = dia da etiqueta),
    então 100% dos pedidos novos com etiqueta têm data — o IS NULL zerava
    os candidatos.
    O COALESCE no UPDATE (commit 4d3e088) garante que a data atual
    não é sobrescrita quando o marketplace responde divergente.
    """
    cutoff = datetime.now(UTC) - _CANDIDATE_WINDOW
    rows = (
        await session.execute(
            select(BlingOrder)
            .where(
                and_(
                    BlingOrder.situacao.in_(_OPEN_SITUACOES),
                    BlingOrder.created_at >= cutoff,
                    BlingOrder.item_index == 0,  # one row per order
                    BlingOrder.numeroloja.isnot(None),
                    BlingOrder.loja.isnot(None),
                )
            )
            .limit(2000)  # safety: prevent runaway if backlog ever explodes
        )
    ).scalars().all()
    return list(rows)


# ─── store/integration resolution ──────────────────────────────────


async def _resolve_store_and_integration(
    session: AsyncSession, bling_loja_id: str,
) -> tuple[Store | None, Integration | None]:
    """Resolves a Bling store id (`bling_orders.loja`, stored as text)
    to a Store row and its marketplace Integration. Mirrors the
    pattern in marketplace_financials._resolve_store_and_integration."""
    try:
        loja_int = int(bling_loja_id)
    except (TypeError, ValueError):
        return None, None
    store = (
        await session.execute(
            select(Store).where(Store.bling_store_id == loja_int).limit(1)
        )
    ).scalar_one_or_none()
    if store is None:
        return None, None
    integration: Integration | None = None
    if store.integration_id is not None:
        integration = await session.get(Integration, store.integration_id)
    if integration is None:
        # Fallback: find by store + platform. The marketplace_financials
        # helper does the same. Use the store's marketplace enum directly.
        platform = _platform_for_marketplace(store.marketplace.value)
        if platform is not None:
            integration = (
                await session.execute(
                    select(Integration)
                    .where(Integration.store_id == store.id)
                    .where(Integration.platform == platform)
                    .limit(1)
                )
            ).scalar_one_or_none()
    return store, integration


def _platform_for_marketplace(marketplace_value: str) -> IntegrationPlatform | None:
    try:
        return IntegrationPlatform(marketplace_value)
    except ValueError:
        return None


async def _get_bling_integration(session: AsyncSession) -> Integration | None:
    return (
        await session.execute(
            select(Integration)
            .where(Integration.platform == IntegrationPlatform.BLING)
            .limit(1)
        )
    ).scalar_one_or_none()


async def _build_bling_client(
    session: AsyncSession, integration: Integration,
) -> BlingClient:
    creds = decrypt_json(integration.credentials)

    async def _persist(new_creds: dict) -> None:
        integration.credentials = encrypt_json(new_creds)
        exp = new_creds.get("expires_at")
        if exp:
            integration.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
        await session.flush()

    return BlingClient(creds, on_token_refresh=_persist, integration_id=integration.id)


# ─── per-marketplace shipment checks ───────────────────────────────


async def _check_marketplace_shipped(
    session: AsyncSession,
    integration: Integration,
    orders: list[BlingOrder],
    deadlines: dict[int, datetime] | None = None,
) -> dict[int, date | None]:
    """Returns `{bling_id: real_ship_date_BRT}` for orders the marketplace
    reports as shipped. Value is None when the marketplace surfaced the
    shipped state without a usable timestamp — callers fall back to
    today-in-BRT in that case. Soft-fails per order — one bad fetch
    doesn't drop the rest.

    `deadlines` (opcional, mutado in-place): recebe `{bling_id: "despachar
    até" (UTC)}` capturado de carona nas mesmas respostas — Shopee
    `ship_by_date`, TikTok `rts_sla_time`, Amazon `LatestShipDate`, ML
    `/shipments/{id}/sla` (este só quando ainda NULL no banco: custa 1
    request extra por pedido)."""
    if deadlines is None:
        deadlines = {}
    creds = decrypt_json(integration.credentials)
    # ML/Amazon consultam vários pedidos em paralelo (_PER_ORDER_CONCURRENCY)
    # e qualquer uma dessas tasks pode disparar um refresh de token, que grava
    # na MESMA AsyncSession. AsyncSession/asyncpg não aceitam duas operações
    # simultâneas na mesma conexão ("another operation is in progress"), então
    # o flush do refresh fica serializado por este lock.
    persist_lock = asyncio.Lock()

    async def _persist(new_creds: dict) -> None:
        async with persist_lock:
            integration.credentials = encrypt_json(new_creds)
            exp = new_creds.get("expires_at")
            if exp:
                integration.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
            await session.flush()

    platform = integration.platform
    shipped: dict[int, date | None] = {}

    if platform == IntegrationPlatform.SHOPEE:
        client = ShopeeClient(creds, on_token_refresh=_persist)
        order_sns = [str(o.numeroloja) for o in orders if o.numeroloja]
        status_map = await client.get_order_status_map(order_sns)
        for o in orders:
            if not o.numeroloja or not o.bling_id:
                continue
            info = status_map.get(str(o.numeroloja))
            if not info:
                continue
            dl = _epoch_to_utc_dt(info.get("ship_by_date"))
            if dl is not None:
                deadlines[int(o.bling_id)] = dl
            if info.get("status") in _SHOPEE_SHIPPED:
                shipped[int(o.bling_id)] = _epoch_to_brt_date(info.get("update_time"))
        logger.info(
            "shipment_check_shopee",
            orders=len(orders), found_in_response=len(status_map),
            shipped=len(shipped),
        )

    elif platform == IntegrationPlatform.TIKTOK:
        client = TikTokClient(creds, on_token_refresh=_persist)
        order_ids = [str(o.numeroloja) for o in orders if o.numeroloja]
        status_map = await client.get_order_status_map(order_ids)
        for o in orders:
            if not o.numeroloja or not o.bling_id:
                continue
            info = status_map.get(str(o.numeroloja))
            if not info:
                continue
            dl = _epoch_to_utc_dt(info.get("rts_sla_time"))
            if dl is not None:
                deadlines[int(o.bling_id)] = dl
            if info.get("status") in _TIKTOK_SHIPPED:
                shipped[int(o.bling_id)] = _epoch_to_brt_date(info.get("update_time"))
        logger.info(
            "shipment_check_tiktok",
            orders=len(orders), found_in_response=len(status_map),
            shipped=len(shipped),
        )

    elif platform == IntegrationPlatform.ML:
        client = MercadoLivreClient(creds, on_token_refresh=_persist)
        sem = asyncio.Semaphore(_PER_ORDER_CONCURRENCY)

        async def _one_ml(o: BlingOrder) -> tuple[int, date | None] | None:
            """Estado de envio de UM pedido ML. None = não enviado/erro."""
            async with sem:
                return await _ml_shipped_for(client, o, deadlines)

        targets = [
            o for o in orders
            if o.numeroloja and o.bling_id and not _skip_not_found(f"ml:{o.numeroloja}")
        ]
        results = await asyncio.gather(
            *(_one_ml(o) for o in targets), return_exceptions=True,
        )
        for res in results:
            if isinstance(res, BaseException) or res is None:
                continue
            shipped[res[0]] = res[1]
        logger.info(
            "shipment_check_ml",
            orders=len(orders), queried=len(targets), shipped=len(shipped),
        )

    elif platform == IntegrationPlatform.AMAZON:
        client = AmazonClient(creds, on_token_refresh=_persist)
        sem = asyncio.Semaphore(_PER_ORDER_CONCURRENCY)

        async def _one_amazon(o: BlingOrder) -> tuple[int, date | None] | None:
            async with sem:
                return await _amazon_shipped_for(client, o, deadlines)

        targets = [o for o in orders if o.numeroloja and o.bling_id]
        results = await asyncio.gather(
            *(_one_amazon(o) for o in targets), return_exceptions=True,
        )
        for res in results:
            if isinstance(res, BaseException) or res is None:
                continue
            shipped[res[0]] = res[1]
        logger.info("shipment_check_amazon", orders=len(orders), shipped=len(shipped))

    else:
        logger.info(
            "shipment_check_platform_unsupported",
            platform=platform.value, orders=len(orders),
        )

    return shipped


async def _ml_shipped_for(
    client: MercadoLivreClient,
    o: BlingOrder,
    deadlines: dict[int, datetime] | None = None,
) -> tuple[int, date | None] | None:
    """`(bling_id, data_envio)` se o ML confirma que o pacote saiu; None se
    não saiu (ou se a consulta falhou). Extraído do laço pra rodar N pedidos
    em paralelo — a lógica de decisão é idêntica à de antes."""
    numeroloja = str(o.numeroloja)
    order_id = _ml_pack_real_order.get(numeroloja, numeroloja)
    via_pack = order_id != numeroloja
    try:
        data = await client.get_order(order_id)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404 and not via_pack:
            # Pode ser um PACK id (compra de carrinho) — resolve e re-tenta
            # com o pedido real. Só se nem o pack existir é que o pedido de
            # fato não pertence a essa conta (aí entra em backoff).
            real_id = await _ml_resolve_pack(client, numeroloja)
            if real_id is None:
                return None
            try:
                data = await client.get_order(real_id)
            except Exception as e2:  # noqa: BLE001
                logger.warning(
                    "shipment_check_ml_order_failed",
                    numeroloja=numeroloja, order_id=real_id, err=str(e2)[:200],
                )
                return None
        elif e.response.status_code == 404:
            # Pedido real (cacheado do pack) sumiu — não deveria acontecer;
            # trata como "não é meu" e entra em backoff.
            _mark_not_found(f"ml:{numeroloja}")
            logger.info("shipment_check_ml_order_404", numeroloja=numeroloja)
            return None
        else:
            logger.warning(
                "shipment_check_ml_order_failed",
                numeroloja=numeroloja, status=e.response.status_code,
            )
            return None
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "shipment_check_ml_order_failed",
            numeroloja=numeroloja, err=str(e)[:200],
        )
        return None

    # Skip cancelled orders outright — top-level status=cancelled means the
    # buyer/seller dropped the order; no shipment is ever going to happen,
    # no need to ask ML for a shipment row.
    top_status = data.get("status")
    if top_status == "cancelled":
        return None

    # Pass 1 — order/shipping level says shipped/delivered outright.
    ship_status = ((data.get("shipping") or {}) or {}).get("status")
    if (ship_status and str(ship_status).lower() in _ML_SHIPPED) or (
        top_status and str(top_status).lower() in _ML_SHIPPED
    ):
        return int(o.bling_id), _iso_to_brt_date(
            data.get("last_updated") or data.get("date_closed")
        )

    # Pass 2 — order.status still "paid" but the shipment may already be
    # moving. ML's order endpoint lags the shipment state: once the seller
    # hands the package off and it gets scanned anywhere downstream
    # (drop-off, hub, packing list, first mile), `/shipments/{id}` flips to
    # status=ready_to_ship with a substatus that means "no longer with the
    # seller", while the order resource still says status=paid.
    #
    # INCLUSION list — only confirmed-handoff substatuses count. The previous
    # "exclusion" version (everything outside {pending, printed,
    # ready_to_print} = shipped) falsely flipped 335 orders on 2026-05-26
    # because `invoice_pending` (NF not yet emitted) wasn't in the exclusion
    # set. Inclusion is safer: any new substatus ML emits goes to NOT shipped
    # until we explicitly whitelist it.
    shipment_id = (data.get("shipping") or {}).get("id")
    if not shipment_id:
        return None
    try:
        ship_data = await client.get_shipment(str(shipment_id))
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "shipment_check_ml_shipment_failed",
            numeroloja=o.numeroloja, shipment_id=shipment_id, err=str(e)[:200],
        )
        return None
    substatus = str(ship_data.get("substatus") or "").lower()
    ship_status2 = str(ship_data.get("status") or "").lower()
    if ship_status2 in _ML_SHIPPED or (
        ship_status2 == "ready_to_ship"
        and substatus in _ML_CONFIRMED_SHIPPED_SUBSTATUS
    ):
        return int(o.bling_id), _iso_to_brt_date(
            ship_data.get("last_updated") or ship_data.get("date_created")
        )

    # NÃO enviado — aproveita que já temos o shipment em mãos pra capturar
    # o "despachar até" (/sla.expected_date = horário de corte: 13:00 em
    # coleta, 23:59 em agência — este ganha +1 dia quando cai no próprio
    # dia, ver _ml_corte_agencia). Só quando ainda NULL no banco: custa 1
    # request extra por pedido, e o ML raramente muda o prazo depois.
    if deadlines is not None and o.marketplace_ship_deadline is None:
        try:
            sla = await client.get_shipment_sla(str(shipment_id))
            dl = _iso_to_utc_dt(sla.get("expected_date"))
            if dl is not None:
                deadlines[int(o.bling_id)] = _ml_corte_agencia(dl)
        except Exception as e:  # noqa: BLE001
            logger.info(
                "shipment_check_ml_sla_failed",
                numeroloja=numeroloja, err=str(e)[:120],
            )
    return None


async def _ml_resolve_pack(
    client: MercadoLivreClient, numeroloja: str,
) -> str | None:
    """Resolve um numeroloja que deu 404 em `/orders` como PACK id.

    Devolve o id do primeiro pedido real do pacote (todos compartilham o
    mesmo envio) e guarda no cache do processo. None = também não é pack →
    o pedido realmente não pertence a essa conta; marca backoff."""
    try:
        pack = await client.get_pack(numeroloja)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            _mark_not_found(f"ml:{numeroloja}")
            logger.info("shipment_check_ml_order_404", numeroloja=numeroloja)
        else:
            logger.warning(
                "shipment_check_ml_pack_failed",
                numeroloja=numeroloja, status=e.response.status_code,
            )
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "shipment_check_ml_pack_failed",
            numeroloja=numeroloja, err=str(e)[:200],
        )
        return None
    ids = [str((po or {}).get("id") or "").strip() for po in pack.get("orders") or []]
    ids = [i for i in ids if i]
    if not ids:
        _mark_not_found(f"ml:{numeroloja}")
        logger.info("shipment_check_ml_pack_empty", numeroloja=numeroloja)
        return None
    _ml_pack_real_order[numeroloja] = ids[0]
    logger.info(
        "shipment_check_ml_pack_resolved",
        numeroloja=numeroloja, order_id=ids[0], orders_in_pack=len(ids),
    )
    return ids[0]


async def _amazon_shipped_for(
    client: AmazonClient,
    o: BlingOrder,
    deadlines: dict[int, datetime] | None = None,
) -> tuple[int, date | None] | None:
    """`(bling_id, data_envio)` quando a Amazon confirma que o pacote saiu.

    Amazon vira OrderStatus pra "Shipped" no momento em que a NF é emitida,
    ANTES de o pacote sair. Quem sabe a verdade é o EasyShipShipmentStatus:
    só os estados de `_AMAZON_EASYSHIP_SAIU` confirmam que o pacote deixou o
    galpão. "PendingPickUp" (esperando a transportadora buscar) e
    "PendingDropOff" (esperando NÓS levarmos ao ponto) contam como não
    enviado, e estado desconhecido também. Pedido fora do EasyShip não tem
    esse campo — aí vale o `order_status` sozinho.
    """
    try:
        result = await client.get_order_status(str(o.numeroloja))
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "shipment_check_amazon_order_failed",
            numeroloja=o.numeroloja, err=str(e)[:200],
        )
        return None
    if not result:
        return None
    # "Despachar até" vem no mesmo payload — captura antes dos filtros de
    # status (o prazo interessa justamente enquanto NÃO está enviado).
    if deadlines is not None and o.bling_id:
        dl = _iso_to_utc_dt(result.get("latest_ship_date"))
        if dl is not None:
            deadlines[int(o.bling_id)] = dl
    if result.get("order_status") not in _AMAZON_SHIPPED:
        return None
    easy = (result.get("easyship_status") or "").strip()
    if easy and easy not in _AMAZON_EASYSHIP_SAIU:
        return None
    return int(o.bling_id), _iso_to_brt_date(result.get("last_update_date"))
