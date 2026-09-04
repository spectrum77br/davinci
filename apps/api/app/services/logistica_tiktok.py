"""Preenche a assinatura de status do TikTok (`Logistica.meli_status`) + rastreio
+ localização física puxando das APIs 202309 do TikTok Shop.

Espelha o `logistica_shopee.py`. O TikTok tem vocabulário PRÓPRIO e enxuto: o
sinal de pós-venda que importa pro fluxo já vem no `status` do pedido (Order API
202309). Então a assinatura do TikTok é um único campo:

    {"order_status": "IN_TRANSIT" | "DELIVERED" | "CANCELLED" | ...}

renderizado em PT por `logistica_rules.assinatura_tiktok`. O rastreio vem do
`tracking_number` do pedido e a localização física dos eventos de tracking
(Fulfillment API — a `description` em inglês do último evento).

Só se aplica a pedidos TikTok. Best-effort: pedido que o TikTok não devolve fica
sem status (não derruba o lote).
"""

from __future__ import annotations

import asyncio
from collections.abc import Collection, Iterable
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Integration, IntegrationPlatform, Logistica
from app.security.cipher import decrypt_json, encrypt_json
from app.services import logistica_datas, logistica_enrich, logistica_rules, logistica_track
from app.services.devolucao_returns import ReturnInfo, epoch_to_dt
from app.services.marketplaces.tiktok import TikTokClient

logger = structlog.get_logger()

_TIKTOK_PLATAFORMAS = logistica_rules._TIKTOK_PLATAFORMAS


def _tiktok_localizacao(track: dict) -> str | None:
    """Descrição (inglês) do evento de tracking de maior `update_time_millis` —
    o local/estágio físico mais recente que o TikTok expõe."""
    eventos = track.get("tracking") or []
    if not eventos:
        return None
    top = max(eventos, key=lambda e: (e or {}).get("update_time_millis") or 0)
    desc = ((top or {}).get("description") or "").strip()
    return desc or None


def _tiktok_destino(order: dict) -> str | None:
    """Destino do pedido a partir de `recipient_address.district_info` (níveis
    do endereço). Fallback de localização quando ainda não há eventos de
    tracking. Junta os nomes não-vazios (mais específico por último)."""
    addr = order.get("recipient_address") or {}
    districts = addr.get("district_info") or []
    nomes = [((d or {}).get("address_name") or "").strip() for d in districts]
    nomes = [n for n in nomes if n]
    if not nomes:
        return None
    return " - ".join(nomes)


async def build_enrichment(client: TikTokClient, order_id: str) -> dict:
    """Monta a assinatura do TikTok + rastreio + localização física.

    Retorna `{"meli_status": {"order_status": ...} | {}, "rastreio": str | None,
    "localizacao": str | None, "datas": {...}}`. Campos que o TikTok não
    devolver ficam de fora / None.

    `datas` = quando o status mudou: o `update_time` do pedido (epoch), que a
    TikTok mexe a cada mudança de estado."""
    order_id = str(order_id)
    order = await client.get_order_detail(order_id)
    st = (order.get("status") or "").strip().upper()
    meli: dict[str, str] = {}
    datas: dict[str, dict[str, str]] = {}
    if st:
        meli["order_status"] = st
        logistica_datas.propor(
            datas, "order_status", order.get("update_time"), logistica_datas.FONTE_PLATAFORMA
        )

    rastreio = (order.get("tracking_number") or "").strip() or None

    track = await client.get_tracking(order_id)
    localizacao = _tiktok_localizacao(track)
    if not localizacao:
        localizacao = _tiktok_destino(order)

    return {
        "meli_status": meli,
        "rastreio": rastreio,
        "localizacao": localizacao,
        "datas": {f: datas[f] for f in meli if f in datas},
    }


async def _tiktok_integration_for_conta(
    session: AsyncSession, conta: str | None
) -> Integration | None:
    """Integração TikTok cuja `name` casa (trim+lower) com a `conta` da linha."""
    key = (conta or "").strip().lower()
    if not key:
        return None
    rows = (
        await session.execute(
            select(Integration).where(Integration.platform == IntegrationPlatform.TIKTOK)
        )
    ).scalars().all()
    for it in rows:
        if (it.name or "").strip().lower() == key:
            return it
    return None


def _build_tiktok_client(
    session: AsyncSession,
    integration: Integration,
    *,
    lock: asyncio.Lock | None = None,
) -> TikTokClient:
    creds = decrypt_json(integration.credentials)

    async def _persist(new_creds: dict) -> None:
        integration.credentials = encrypt_json(new_creds)
        exp = new_creds.get("token_expires_at") or new_creds.get("expires_at")
        if exp:
            integration.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
        # Único acesso ao banco durante a rajada concorrente do enrich_recent —
        # serializado pelo lock (sessão async não aceita flush simultâneo).
        async with (lock or logistica_enrich.NOLOCK):
            await session.flush()

    return TikTokClient(creds, on_token_refresh=_persist)


class TikTokEnrichError(Exception):
    """Falha de negócio ao enriquecer uma linha TikTok (código pro endpoint)."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


async def enrich_row(
    session: AsyncSession,
    row: Logistica,
    *,
    client_cache: dict[str, TikTokClient] | None = None,
) -> bool:
    """Preenche `row.meli_status`/rastreio/localizacao puxando do TikTok. Retorna
    True se atualizou.

    Levanta `TikTokEnrichError` com código quando não dá pra prosseguir (linha
    não-TikTok, sem pedido de marketplace, conta sem integração TikTok)."""
    if (row.plataforma or "").strip().lower() not in _TIKTOK_PLATAFORMAS:
        raise TikTokEnrichError("logistica_nao_tiktok")
    order_id = (row.pedido_marketplace or "").strip()
    if not order_id:
        raise TikTokEnrichError("logistica_sem_pedido")

    conta = row.conta
    client: TikTokClient | None = None
    if client_cache is not None and conta in client_cache:
        client = client_cache[conta]
    else:
        integ = await _tiktok_integration_for_conta(session, conta)
        if integ is None:
            raise TikTokEnrichError("logistica_sem_integracao")
        client = _build_tiktok_client(session, integ)
        if client_cache is not None:
            client_cache[conta] = client

    enr = await build_enrichment(client, order_id)
    meli = enr["meli_status"]
    # `return_status` vem do sweep de pós-venda (returns API), não do
    # build_enrichment — preserva no re-enrich, senão a devolução detectada
    # sumiria da assinatura no tick seguinte e a regra de status regrediria.
    ret = (row.meli_status or {}).get("return_status")
    if ret:
        meli = {**meli, "return_status": ret}
    # Antes de trocar o status: o carimbo compara o valor velho com o novo.
    row.status_datas = logistica_datas.aplicar(row, meli, enr.get("datas"))
    row.meli_status = meli
    if enr.get("rastreio"):
        row.rastreio = enr["rastreio"]
    # Envio por Correios com evento real do 17track (`localizacao_at`) não é
    # sobrescrito pelo proxy da plataforma — o físico é melhor que a estimativa.
    if enr.get("localizacao") and not (
        logistica_track.is_correios(row.rastreio) and row.localizacao_at
    ):
        row.localizacao = enr["localizacao"]
    # Divergência TikTok: order_status comercial × último evento físico.
    row.divergencia = logistica_rules.detectar_divergencia_tiktok(
        row.meli_status, row.localizacao
    )
    return True


# Janela do sweep de pós-venda: linhas TikTok com `data` (data do pedido) até 45
# dias atrás — cobre com folga o prazo de devolução. ~750 linhas ≈ 16 chamadas
# em lote por varredura (medido 28/08). Mesma mecânica do sweep da Shopee.
_SWEEP_JANELA_DIAS = 45
# Janela das devoluções (paridade com a Shopee, que limita em 15 dias).
_RETURNS_JANELA_DIAS = 15


async def sweep_pos_venda(session: AsyncSession) -> dict:
    """Re-olha TODAS as linhas TikTok da janela — inclusive as resolvidas que o
    painel esconde — e devolve os ids cuja situação de pós-venda MUDOU.

    Espelho do `logistica_shopee.sweep_pos_venda` (mesmo ponto cego: linha
    escondida nunca mais era re-consultada — 16 pedidos ficaram "Em trânsito"
    no painel dias depois de entregues, e devolução pós-entrega era invisível;
    caso real 28/08, pedidos 585411441781475242/585612645547804469/
    585673041600415018 + 15 pra "Entregue").

    Duas fontes, ambas em lote e por conta:
    - `get_order_status_map` (50 pedidos/chamada): status vivo mudou
      (DELIVERED, COMPLETED, ...) → atualiza meli_status + carimbo de data;
    - `get_return_list` (returns/search): devolução que o order_status do
      TikTok NEM TEM como mostrar → grava `meli_status["return_status"]`;
      `assinatura_tiktok` então rende "Devolução solicitada". Havendo mais de
      um caso pro mesmo pedido, vale o VIVO mais recente.

    Retorna {"ids": [UUID...], **contadores}. O recarregar passa os ids como
    `extras` do `_ids_pendentes` — extras furam o escondimento — e o fluxo
    normal re-enriquece a linha e aplica a regra de status no Bling."""
    corte = datetime.now(UTC).date() - timedelta(days=_SWEEP_JANELA_DIAS)
    rows = (
        await session.execute(
            select(Logistica).where(
                func.lower(func.trim(Logistica.plataforma)).in_(
                    tuple(_TIKTOK_PLATAFORMAS)
                ),
                func.coalesce(Logistica.pedido_marketplace, "") != "",
                or_(Logistica.data.is_(None), Logistica.data >= corte),
            )
        )
    ).scalars().all()

    por_conta: dict[str, list[Logistica]] = {}
    for r in rows:
        por_conta.setdefault((r.conta or "").strip(), []).append(r)

    mudados: set[UUID] = set()
    n_status = n_returns = contas_ok = 0
    agora = int(datetime.now(UTC).timestamp())
    ret_from = agora - _RETURNS_JANELA_DIAS * 24 * 3600 + 300
    for conta, linhas in por_conta.items():
        integ = await _tiktok_integration_for_conta(session, conta)
        if integ is None:
            continue
        client = _build_tiktok_client(session, integ)
        contas_ok += 1

        # 1) status vivo do pedido, em lotes de 50.
        oids = [(r.pedido_marketplace or "").strip() for r in linhas]
        smap = await client.get_order_status_map(oids)
        for r in linhas:
            info = smap.get((r.pedido_marketplace or "").strip()) or {}
            st = (info.get("status") or "").strip().upper()
            atual = ((r.meli_status or {}).get("order_status") or "").strip().upper()
            if st and st != atual:
                meli = dict(r.meli_status or {})
                meli["order_status"] = st
                datas: dict[str, dict[str, str]] = {}
                logistica_datas.propor(
                    datas, "order_status", info.get("update_time"),
                    logistica_datas.FONTE_PLATAFORMA,
                )
                r.status_datas = logistica_datas.aplicar(r, meli, datas)
                r.meli_status = meli
                mudados.add(r.id)
                n_status += 1

        # 2) devoluções da loja nos últimos 15 dias (por update_time).
        try:
            devolucoes = await client.get_return_list(
                update_time_from=ret_from, update_time_to=agora
            )
        except Exception as e:  # noqa: BLE001 — best-effort por conta
            logger.warning(
                "logistica_tiktok_sweep_returns_falhou",
                conta=conta, err=str(e)[:200],
            )
            devolucoes = []
        melhor: dict[str, tuple[bool, int, str]] = {}
        for d in devolucoes:
            oid = str(d.get("order_id") or "").strip()
            st = str(d.get("return_status") or "").strip().upper()
            if not oid or not st:
                continue
            vivo = st not in logistica_rules._TIKTOK_RETURN_ENCERRADO
            cand = (vivo, int(d.get("update_time") or 0), st)
            if oid not in melhor or cand[:2] > melhor[oid][:2]:
                melhor[oid] = cand
        for r in linhas:
            got = melhor.get((r.pedido_marketplace or "").strip())
            if got is None:
                continue
            st = got[2]
            atual = ((r.meli_status or {}).get("return_status") or "").strip().upper()
            if st != atual:
                meli = dict(r.meli_status or {})
                meli["return_status"] = st
                r.meli_status = meli
                mudados.add(r.id)
                n_returns += 1

    await session.commit()
    summary = {
        "seen": len(rows), "contas": contas_ok,
        "order_status": n_status, "returns": n_returns,
    }
    logger.info("logistica_tiktok_sweep_pos_venda", **summary)
    return {"ids": list(mudados), **summary}


# ---- devolução: o pacote que VOLTA (aba Acompanhamento de Devoluções) --------


def _epoch_int(v: object) -> int:
    """Epoch cru do payload (int/str) → int; 0 se ilegível/ausente."""
    try:
        return int(float(v))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _tiktok_return_info(d: dict) -> ReturnInfo:
    """Caso do returns/search → `ReturnInfo`. Devolução só-reembolso (return_type
    REFUND) não tem pacote: entra mesmo assim, com tracking None."""
    return ReturnInfo(
        fonte="tiktok",
        status=str(d.get("return_status") or "").strip().upper() or None,
        tracking=str(d.get("return_tracking_number") or "").strip() or None,
        carrier=str(d.get("return_provider_name") or "").strip() or None,
        created_at=epoch_to_dt(d.get("create_time")),
        updated_at=epoch_to_dt(d.get("update_time")),
        return_id=str(d.get("return_id") or "").strip() or None,
    )


def _melhor_devolucao_por_pedido(devolucoes: Iterable[dict]) -> dict[str, dict]:
    """{order_id: caso} — havendo mais de um caso pro mesmo pedido vale o VIVO
    (fora de `_TIKTOK_RETURN_ENCERRADO`) mais recente; sem vivo, o mais recente.
    "Recente" = `update_time` (fallback `create_time`). Mesma regra do
    `sweep_pos_venda`, mas guardando o caso inteiro (rastreio, transportadora)."""
    melhor: dict[str, tuple[tuple[bool, int], dict]] = {}
    for d in devolucoes:
        if not isinstance(d, dict):
            continue
        oid = str(d.get("order_id") or "").strip()
        if not oid:
            continue
        st = str(d.get("return_status") or "").strip().upper()
        vivo = bool(st) and st not in logistica_rules._TIKTOK_RETURN_ENCERRADO
        quando = _epoch_int(d.get("update_time")) or _epoch_int(d.get("create_time"))
        chave = (vivo, quando)
        if oid not in melhor or chave > melhor[oid][0]:
            melhor[oid] = (chave, d)
    return {oid: d for oid, (_, d) in melhor.items()}


async def returns_por_pedido(
    session: AsyncSession, linhas: list[Logistica]
) -> dict[str, ReturnInfo]:
    """Devolução conhecida no TikTok pra cada linha da Logística recebida:
    `{pedido_bling: ReturnInfo}` — pedido sem caso de devolução fica FORA do
    dict (ausente = desconhecido). Contrato em `services/devolucao_returns`.

    Eduardo (03/09): a aba Acompanhamento mostrava o rastreio da ENTREGA; o que
    interessa é o pacote que VOLTA (`return_tracking_number` +
    `return_provider_name` do returns/search).

    Busca por `order_ids` (lotes de 50 por conta, sem filtro de tempo) — o
    sweep só olha `update_time` dos últimos 15 dias e perderia devolução aberta
    meses atrás e nunca mais mexida. Só linhas TikTok com pedido de marketplace
    e pedido Bling; mais de um caso → o VIVO mais recente
    (`_melhor_devolucao_por_pedido`). Best-effort por conta: sem integração ou
    API caída → loga e pula, nunca levanta."""
    # conta → order_id → [pedido_bling] (a mesma venda pode ter 2 linhas).
    por_conta: dict[str, dict[str, list[str]]] = {}
    for r in linhas:
        if (r.plataforma or "").strip().lower() not in _TIKTOK_PLATAFORMAS:
            continue
        oid = (r.pedido_marketplace or "").strip()
        pb = (r.pedido_bling or "").strip()
        if not oid or not pb:
            continue
        por_conta.setdefault((r.conta or "").strip(), {}).setdefault(oid, []).append(pb)

    out: dict[str, ReturnInfo] = {}
    for conta, pedidos in por_conta.items():
        try:
            integ = await _tiktok_integration_for_conta(session, conta)
            if integ is None:
                logger.warning(
                    "logistica_tiktok_returns_sem_integracao",
                    conta=conta, pedidos=len(pedidos),
                )
                continue
            client = _build_tiktok_client(session, integ)
            devolucoes = await client.get_return_list(order_ids=list(pedidos))
        except Exception as e:  # noqa: BLE001 — best-effort por conta
            logger.warning(
                "logistica_tiktok_returns_por_pedido_falhou",
                conta=conta, pedidos=len(pedidos), err=str(e)[:200],
            )
            continue
        for oid, d in _melhor_devolucao_por_pedido(devolucoes).items():
            info = _tiktok_return_info(d)
            for pb in pedidos.get(oid) or ():
                out[pb] = info
    return out


async def enrich_recent(
    session: AsyncSession,
    *,
    limit: int = 100,
    only_empty: bool = True,
    ids: Collection[UUID] | None = None,
) -> dict[str, int]:
    """Enriquece um lote de linhas TikTok (mais recentes primeiro), reusando o
    client por conta. `only_empty` pula linhas que já têm meli_status.
    `ids` restringe às linhas dadas (o recarregar passa as pendentes do
    painel) — aí o `limit` não se aplica."""
    stmt = select(Logistica).where(
        func.lower(func.trim(Logistica.plataforma)).in_(tuple(_TIKTOK_PLATAFORMAS))
    )
    if only_empty:
        stmt = stmt.where(cast(Logistica.meli_status, Text) == "{}")
    stmt = stmt.order_by(
        Logistica.data.desc().nulls_last(), Logistica.created_at.desc()
    )
    if ids is not None:
        stmt = stmt.where(Logistica.id.in_(list(ids)))
    else:
        stmt = stmt.limit(limit)
    rows = (await session.execute(stmt)).scalars().all()

    cache: dict[str, TikTokClient] = {}
    lock = asyncio.Lock()
    await logistica_enrich.prewarm_clients(
        session,
        rows,
        cache,
        resolve=_tiktok_integration_for_conta,
        build=lambda s, i: _build_tiktok_client(s, i, lock=lock),
    )
    # Conta sem integração fica fora do cache: descarta aqui pra o enrich_row
    # não ir ao banco no meio da rajada concorrente.
    alvo = [r for r in rows if r.conta in cache]
    updated = 0
    skipped = len(rows) - len(alvo)
    failed = 0
    for lote in logistica_enrich.chunked(alvo):
        res = await asyncio.gather(
            *(enrich_row(session, r, client_cache=cache) for r in lote),
            return_exceptions=True,
        )
        for row, r in zip(lote, res, strict=True):
            if isinstance(r, TikTokEnrichError):
                skipped += 1
            elif isinstance(r, BaseException):
                failed += 1
                logger.warning(
                    "logistica_tiktok_row_failed",
                    id=str(row.id), pedido=row.pedido_marketplace, err=str(r)[:200],
                )
            elif r:
                updated += 1
    await session.commit()
    summary = {"seen": len(rows), "updated": updated, "skipped": skipped, "failed": failed}
    logger.info("logistica_tiktok_enrich_batch", **summary)
    return summary
