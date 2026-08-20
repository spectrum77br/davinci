"""Preenche a assinatura de status do Meli (`Logistica.meli_status`) puxando da
API do Mercado Livre.

O backfill de 30 dias veio puro do Bling, que NÃO carrega a assinatura de
pós-venda do Meli — ele traduz o pedido numa `situacao` única e descarta o
resto. Aqui montamos os 8 campos (`order_status`, `ship_status`,
`ship_substatus`, `cancel_group`, `return_status`, `claim_stage`,
`claim_status`, `benefited`) direto do ML:

  - order_status / cancel_group -> GET /orders/{id}
  - ship_status / ship_substatus -> GET /shipments/{id}
  - claim_stage / claim_status / benefited -> GET /post-purchase/v1/claims/{id}
    (id vem de order.mediations[].id)
  - return_status -> GET /post-purchase/v1/claims/{id}/returns (shipping.status)

Além da assinatura, `build_enrichment` também devolve o `rastreio`
(`shipment.tracking_number`) e a `localizacao` (proxy do "último local" = o
substatus/status do envio traduzido pra PT — o ML NÃO expõe o local físico, que
só existiria no rastreamento direto do Correios/Amazon). Ambos gravados na linha.

Só se aplica a pedidos de Mercado Livre — as outras plataformas têm status
próprios e a planilha de referência é do Meli. Tudo best-effort: uma chamada de
claim que falhe (pedido sem reclamação → 404) só deixa aqueles campos vazios,
nunca derruba os campos de pedido/envio.
"""

from __future__ import annotations

import asyncio
from collections.abc import Collection
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import Text, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Integration, IntegrationPlatform, Logistica
from app.security.cipher import decrypt_json, encrypt_json
from app.services import logistica_datas, logistica_enrich, logistica_rules, logistica_track
from app.services.marketplaces.ml import MercadoLivreClient

logger = structlog.get_logger()

# Rótulos de plataforma (na Logística) que representam Mercado Livre.
_ML_PLATAFORMAS = {"mercado livre", "mercadolivre", "ml"}


def _extract_return_status(rets: Any) -> str:
    """Status do envio da devolução (`return_status`) do payload de returns do
    claim (v2). Aceita objeto único OU lista; prioriza `shipments[0].status`
    (formato v2) e cai em `shipping.status` / `status` (resiliência)."""
    if not rets:
        return ""
    obj = rets[0] if isinstance(rets, list) and rets else rets
    if not isinstance(obj, dict):
        return ""
    shipments = obj.get("shipments")
    if isinstance(shipments, list) and shipments and isinstance(shipments[0], dict):
        s = (shipments[0].get("status") or "").strip()
        if s:
            return s
    shp = obj.get("shipping") or {}
    s = (shp.get("status") or "").strip()
    if s:
        return s
    return (obj.get("status") or "").strip()


def _return_em(rets: Any) -> Any:
    """Última mexida na devolução (estimativa da data do `return_status`).
    Mesmo formato solto do `_extract_return_status`: objeto ou lista."""
    if not rets:
        return None
    obj = rets[0] if isinstance(rets, list) and rets else rets
    if not isinstance(obj, dict):
        return None
    shipments = obj.get("shipments")
    if isinstance(shipments, list) and shipments and isinstance(shipments[0], dict):
        em = shipments[0].get("last_updated") or shipments[0].get("date_created")
        if em:
            return em
    return obj.get("last_updated") or obj.get("date_created")


def _ship_destino(sh: dict) -> str | None:
    """Cidade/UF de destino do shipment (`receiver_address`). `city`/`state`
    podem vir como string ou objeto `{id, name}`; `state.id` costuma ser
    `BR-SP` → extrai o `SP`."""
    addr = sh.get("receiver_address") or {}
    city = addr.get("city")
    if isinstance(city, dict):
        city = city.get("name")
    city = str(city or "").strip()
    state = addr.get("state")
    if isinstance(state, dict):
        state = state.get("id") or state.get("name")
    uf = str(state or "").strip()
    if "-" in uf:
        uf = uf.rsplit("-", 1)[-1]
    where = "/".join(p for p in (city, uf) if p)
    return where or None


# Status de envio já finalizados — não têm previsão de entrega futura.
_SHIP_TERMINAL = {"delivered", "cancelled", "not_delivered"}


def _previsao_from_lead_time(lt: dict) -> str | None:
    """Data prevista de entrega (dd/mm) de um payload de `lead_time`; prefere o
    limite final, cai no limite/estimado."""
    lt = lt or {}
    for k in ("estimated_delivery_final", "estimated_delivery_limit", "estimated_delivery_time"):
        node = lt.get(k)
        date = node.get("date") if isinstance(node, dict) else None
        if not date:
            continue
        try:
            d = datetime.fromisoformat(str(date).replace("Z", "+00:00"))
        except ValueError:
            continue
        return d.strftime("%d/%m")
    return None


async def _ship_previsao(client: MercadoLivreClient, sh: dict, ship_id: str) -> str | None:
    """Previsão de entrega do shipment. O `get_shipment` normalmente NÃO embute
    `lead_time`, então busca o endpoint dedicado — mas só pra envios em curso
    (finalizados não têm ETA e não valem a chamada extra)."""
    prev = _previsao_from_lead_time(sh.get("lead_time") or {})
    if prev:
        return prev
    if (sh.get("status") or "").strip() in _SHIP_TERMINAL:
        return None
    try:
        r = await client._request("GET", f"/shipments/{ship_id}/lead_time")
        if r.status_code != 200:
            return None
        lt = r.json() or {}
    except Exception as e:  # noqa: BLE001
        logger.info("logistica_meli_lead_time_none", ship_id=ship_id, err=str(e)[:120])
        return None
    return _previsao_from_lead_time(lt)


async def _fetch_order(client: MercadoLivreClient, order_id: str) -> dict:
    """GET /orders/{id}; se falhar (o número guardado costuma ser um PACK id,
    não um order id → `/orders/{pack}` dá 404 "Order do not exists"), resolve
    via GET /packs/{id} e busca o primeiro order real do pack
    (`pack.orders[0].id`). Sem pack válido, deixa o erro original subir."""
    try:
        return await client.get_order(order_id)
    except Exception:  # noqa: BLE001
        pass
    pack = await client.get_pack(order_id)  # levanta se nem pack existir
    real_ids = [
        o.get("id") for o in (pack.get("orders") or []) if isinstance(o, dict) and o.get("id")
    ]
    if not real_ids:
        return await client.get_order(order_id)  # re-levanta o erro original limpo
    return await client.get_order(str(real_ids[0]))


async def build_enrichment(client: MercadoLivreClient, order_id: str) -> dict[str, Any]:
    """Puxa do ML tudo que a Logística consome de um pedido: a assinatura de 8
    campos (`meli_status`) + o número de rastreio (`rastreio`, vem do shipment).

    Retorna `{"meli_status": {...}, "rastreio": str|None, "datas": {...}}`.
    Best-effort: falha em shipment/claim/returns deixa aqueles campos de fora,
    mas mantém os que já resolveram. Levanta só se nem order nem pack existirem.

    `datas` = quando cada campo mudou, pelo que o ML conta (ver
    logistica_datas). O ML data o pedido (`date_closed`/`cancel_detail.date`) e
    cada status do envio (`status_history.date_*`), mas NÃO data o substatus —
    esse fica com a última mexida no envio (`aprox`)."""
    out: dict[str, str] = {}
    datas: dict[str, dict[str, str]] = {}
    rastreio: str | None = None
    destino: str | None = None
    previsao: str | None = None
    order = await _fetch_order(client, str(order_id))

    cancel_detail = order.get("cancel_detail") or {}
    st = (order.get("status") or "").strip()
    if st:
        out["order_status"] = st
        # Cancelado tem data própria; pago fecha o pedido em date_closed. Se
        # nenhum dos dois vier, a última mexida no pedido é a estimativa.
        if st == "cancelled":
            logistica_datas.propor(
                datas, "order_status", cancel_detail.get("date"),
                logistica_datas.FONTE_PLATAFORMA,
            )
        else:
            logistica_datas.propor(
                datas, "order_status", order.get("date_closed"),
                logistica_datas.FONTE_PLATAFORMA,
            )
        logistica_datas.propor(
            datas, "order_status", order.get("last_updated"), logistica_datas.FONTE_APROX
        )

    grp = (cancel_detail.get("group") or "").strip()
    if grp:
        out["cancel_group"] = grp
        logistica_datas.propor(
            datas, "cancel_group", cancel_detail.get("date"), logistica_datas.FONTE_PLATAFORMA
        )
        logistica_datas.propor(
            datas, "cancel_group", order.get("last_updated"), logistica_datas.FONTE_APROX
        )

    shipping = order.get("shipping") or {}
    ship_id = shipping.get("id")
    if ship_id:
        try:
            sh = await client.get_shipment(str(ship_id))
        except Exception as e:  # noqa: BLE001
            logger.warning("logistica_meli_shipment_failed", order_id=order_id, err=str(e)[:200])
            sh = {}
        # O ML guarda a data de CADA status do envio aqui
        # ({"date_shipped": ..., "date_delivered": ...}) — data oficial, sem
        # nenhuma chamada extra.
        historico = sh.get("status_history") or {}
        historico = historico if isinstance(historico, dict) else {}
        ship_status = (sh.get("status") or "").strip()
        if ship_status:
            out["ship_status"] = ship_status
            logistica_datas.propor(
                datas, "ship_status", historico.get(f"date_{ship_status}"),
                logistica_datas.FONTE_PLATAFORMA,
            )
            logistica_datas.propor(
                datas, "ship_status", sh.get("last_updated"), logistica_datas.FONTE_APROX
            )
        sub = (sh.get("substatus") or "").strip()
        if sub:
            out["ship_substatus"] = sub
            # O ML não data substatus (nem /shipments/{id}/history traz linha do
            # tempo por substatus): a melhor estimativa é a última mexida no
            # envio. A partir daí, mudou o substatus → o DaVinci carimba.
            logistica_datas.propor(
                datas, "ship_substatus", sh.get("last_updated"), logistica_datas.FONTE_APROX
            )
        tn = (sh.get("tracking_number") or "").strip()
        if tn:
            rastreio = tn
        destino = _ship_destino(sh)
        previsao = await _ship_previsao(client, sh, str(ship_id))
    else:
        ship_status = (shipping.get("status") or "").strip()
        if ship_status:
            out["ship_status"] = ship_status
            logistica_datas.propor(
                datas, "ship_status", order.get("last_updated"), logistica_datas.FONTE_APROX
            )

    # Reclamação/mediação — o id vem em order.mediations[].id (ML só lista
    # quando abriu um caso de pós-venda).
    claim_id = None
    for m in order.get("mediations") or []:
        cid = m.get("id") if isinstance(m, dict) else m
        if cid:
            claim_id = cid
            break
    if claim_id:
        try:
            claim = await client.get_claim(claim_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("logistica_meli_claim_failed", order_id=order_id, err=str(e)[:200])
            claim = {}
        # A reclamação só datou ela inteira (last_updated) — vale pros 3 campos
        # que saem dela como estimativa.
        claim_em = claim.get("last_updated") or claim.get("date_created")
        stage = (claim.get("stage") or "").strip()
        if stage:
            out["claim_stage"] = stage
            logistica_datas.propor(datas, "claim_stage", claim_em, logistica_datas.FONTE_APROX)
        cstatus = (claim.get("status") or "").strip()
        if cstatus:
            out["claim_status"] = cstatus
            logistica_datas.propor(datas, "claim_status", claim_em, logistica_datas.FONTE_APROX)
        benefited = (claim.get("resolution") or {}).get("benefited")
        if isinstance(benefited, list):
            benefited = benefited[0] if benefited else None
        if benefited:
            out["benefited"] = str(benefited).strip()
            resolucao_em = (claim.get("resolution") or {}).get("date_created") or claim_em
            logistica_datas.propor(datas, "benefited", resolucao_em, logistica_datas.FONTE_APROX)
        try:
            rets = await client.get_claim_returns(claim_id)
        except Exception as e:  # noqa: BLE001
            logger.info("logistica_meli_returns_none", order_id=order_id, err=str(e)[:120])
            rets = None
        rstatus = _extract_return_status(rets)
        if rstatus:
            out["return_status"] = rstatus
            logistica_datas.propor(
                datas, "return_status", _return_em(rets) or claim_em, logistica_datas.FONTE_APROX
            )

    # Mantém só campos conhecidos (defesa contra tokens estranhos entrando).
    meli = {f: out[f] for f in logistica_rules.FIELD_ORDER if out.get(f)}
    datas = {f: datas[f] for f in meli if f in datas}
    # Localização = proxy do "último local" (substatus/status do envio em PT) +
    # destino (cidade/UF) + previsão de entrega; o ML não dá o local físico da
    # rede própria.
    status_pt = logistica_rules.localizacao_pt(meli)
    localizacao = (
        logistica_rules.localizacao_completa(status_pt, destino=destino, previsao=previsao) or None
    )
    return {
        "meli_status": meli,
        "rastreio": rastreio,
        "localizacao": localizacao,
        "datas": datas,
    }


async def build_meli_status(client: MercadoLivreClient, order_id: str) -> dict[str, str]:
    """Só a assinatura de 8 campos (compat). Ver `build_enrichment`."""
    enr = await build_enrichment(client, order_id)
    return enr["meli_status"]


async def _ml_integration_for_conta(session: AsyncSession, conta: str | None) -> Integration | None:
    """Integração ML cuja `name` casa (trim+lower) com a `conta` da linha."""
    key = (conta or "").strip().lower()
    if not key:
        return None
    rows = (
        await session.execute(
            select(Integration).where(Integration.platform == IntegrationPlatform.ML)
        )
    ).scalars().all()
    for it in rows:
        if (it.name or "").strip().lower() == key:
            return it
    return None


def _build_ml_client(
    session: AsyncSession,
    integration: Integration,
    *,
    lock: asyncio.Lock | None = None,
) -> MercadoLivreClient:
    creds = decrypt_json(integration.credentials)

    async def _persist(new_creds: dict) -> None:
        integration.credentials = encrypt_json(new_creds)
        exp = new_creds.get("expires_at")
        if exp:
            integration.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
        # Único acesso ao banco durante a rajada concorrente do enrich_recent —
        # serializado pelo lock (sessão async não aceita flush simultâneo).
        async with (lock or logistica_enrich.NOLOCK):
            await session.flush()

    return MercadoLivreClient(creds, on_token_refresh=_persist)


class MeliEnrichError(Exception):
    """Falha de negócio ao enriquecer uma linha (código legível pro endpoint)."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


async def enrich_row(
    session: AsyncSession,
    row: Logistica,
    *,
    client_cache: dict[str, MercadoLivreClient] | None = None,
) -> bool:
    """Preenche `row.meli_status` puxando do ML. Retorna True se atualizou.

    Levanta `MeliEnrichError` com código quando não dá pra prosseguir
    (linha não-ML, sem pedido de marketplace, conta sem integração ML)."""
    if (row.plataforma or "").strip().lower() not in _ML_PLATAFORMAS:
        raise MeliEnrichError("logistica_nao_ml")
    order_id = (row.pedido_marketplace or "").strip()
    if not order_id:
        raise MeliEnrichError("logistica_sem_pedido")

    conta = row.conta
    client: MercadoLivreClient | None = None
    if client_cache is not None and conta in client_cache:
        client = client_cache[conta]
    else:
        integ = await _ml_integration_for_conta(session, conta)
        if integ is None:
            raise MeliEnrichError("logistica_sem_integracao")
        client = _build_ml_client(session, integ)
        if client_cache is not None:
            client_cache[conta] = client

    enr = await build_enrichment(client, order_id)
    # Antes de trocar o status: o carimbo compara o valor velho com o novo.
    row.status_datas = logistica_datas.aplicar(row, enr["meli_status"], enr.get("datas"))
    row.meli_status = enr["meli_status"]
    if enr.get("rastreio"):
        row.rastreio = enr["rastreio"]
    # Localização: pra Correios (...BR) o físico do 17track manda — não deixa o
    # proxy do ML sobrescrever uma localização física já existente.
    new_loc = enr.get("localizacao")
    if new_loc and not (logistica_track.is_correios(row.rastreio) and row.localizacao):
        row.localizacao = new_loc
    # Divergência ML × físico: só faz sentido pros Correios (onde o 17track dá o
    # local físico real na `localizacao`); nas outras não há como comparar.
    if logistica_track.is_correios(row.rastreio):
        row.divergencia = logistica_rules.detectar_divergencia(
            row.meli_status, row.localizacao
        )
    return True


def _respondent_actions(claim: dict) -> set[str]:
    """Ações liberadas pro VENDEDOR (player role=respondent) num claim."""
    for p in claim.get("players") or []:
        if (p or {}).get("role") == "respondent":
            return {
                a.get("action")
                for a in (p.get("available_actions") or [])
                if isinstance(a, dict) and a.get("action")
            }
    return set()


async def enviar_chamado_for_row(session: AsyncSession, row: Logistica, message: str) -> str:
    """Abre o chamado no ML e manda a `message` PRO MERCADO LIVRE (mediador),
    sobre a reclamação já existente do pedido. Grava o claim_id em `row.chamado`
    e o retorna.

    Fluxo (a API do ML NÃO deixa o vendedor abrir reclamação do zero — só o
    comprador; ver doc post-purchase): pega o claim_id de `order.mediations[]`;
    se a mediação ainda não está aberta e o vendedor tem a ação `open_dispute`,
    escala (`open-dispute`) e o ML entra como mediador; então manda a mensagem
    com `receiver_role=mediator`.

    Levanta `MeliEnrichError` com código: linha não-ML / sem pedido / sem
    integração (como o enrich) + `logistica_sem_reclamacao` (pedido sem claim do
    comprador) / `logistica_reclamacao_encerrada` (claim fechado, sem ações) /
    `logistica_reclamacao_sem_acao` (vendedor não pode falar com o mediador nem
    escalar). Erros crus da API do ML sobem como exceção (o endpoint devolve o
    corpo do ML)."""
    if (row.plataforma or "").strip().lower() not in _ML_PLATAFORMAS:
        raise MeliEnrichError("logistica_nao_ml")
    order_id = (row.pedido_marketplace or "").strip()
    if not order_id:
        raise MeliEnrichError("logistica_sem_pedido")
    integ = await _ml_integration_for_conta(session, row.conta)
    if integ is None:
        raise MeliEnrichError("logistica_sem_integracao")
    client = _build_ml_client(session, integ)

    order = await _fetch_order(client, order_id)
    claim_id = next(
        (
            m["id"]
            for m in (order.get("mediations") or [])
            if isinstance(m, dict) and m.get("id")
        ),
        None,
    )
    if not claim_id:
        raise MeliEnrichError("logistica_sem_reclamacao")

    claim = await client.get_claim(claim_id)
    actions = _respondent_actions(claim)
    if (claim.get("status") or "").lower() == "closed" or not actions:
        raise MeliEnrichError("logistica_reclamacao_encerrada")

    if "send_message_to_mediator" not in actions:
        if "open_dispute" not in actions:
            raise MeliEnrichError("logistica_reclamacao_sem_acao")
        await client.open_claim_dispute(claim_id)  # abre a mediação (entra o ML)

    await client.send_claim_message(claim_id, message, receiver_role="mediator")
    row.chamado = str(claim_id)
    return str(claim_id)


async def enrich_recent(
    session: AsyncSession,
    *,
    limit: int = 100,
    only_empty: bool = True,
    ids: Collection[UUID] | None = None,
) -> dict[str, int]:
    """Enriquece um lote de linhas ML (mais recentes primeiro), reusando o
    client por conta. `only_empty` pula linhas que já têm meli_status.
    `ids` restringe às linhas dadas (o recarregar passa as pendentes do
    painel) — aí o `limit` não se aplica."""
    stmt = select(Logistica).where(
        func.lower(func.trim(Logistica.plataforma)).in_(tuple(_ML_PLATAFORMAS))
    )
    if only_empty:
        stmt = stmt.where(cast(Logistica.meli_status, Text) == "{}")
    stmt = stmt.order_by(Logistica.data.desc().nulls_last(), Logistica.created_at.desc())
    if ids is not None:
        stmt = stmt.where(Logistica.id.in_(list(ids)))
    else:
        stmt = stmt.limit(limit)
    rows = (await session.execute(stmt)).scalars().all()

    cache: dict[str, MercadoLivreClient] = {}
    lock = asyncio.Lock()
    await logistica_enrich.prewarm_clients(
        session,
        rows,
        cache,
        resolve=_ml_integration_for_conta,
        build=lambda s, i: _build_ml_client(s, i, lock=lock),
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
            if isinstance(r, MeliEnrichError):
                skipped += 1
            elif isinstance(r, BaseException):
                failed += 1
                logger.warning(
                    "logistica_meli_row_failed",
                    id=str(row.id), pedido=row.pedido_marketplace, err=str(r)[:200],
                )
            elif r:
                updated += 1
    await session.commit()
    summary = {"seen": len(rows), "updated": updated, "skipped": skipped, "failed": failed}
    logger.info("logistica_meli_enrich_batch", **summary)
    return summary
