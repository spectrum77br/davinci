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

`returns_por_pedido` (aba Acompanhamento de Devoluções) segue o mesmo caminho
order → mediations → returns do claim, mas devolve o pacote que VOLTA
(shipment do return: tracking_number/status) no contrato compartilhado
`devolucao_returns.ReturnInfo`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Collection
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple
from uuid import UUID

import structlog
from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Integration, IntegrationPlatform, Logistica
from app.security.cipher import decrypt_json, encrypt_json
from app.services import logistica_datas, logistica_enrich, logistica_rules, logistica_track
from app.services.devolucao_returns import ReturnInfo, iso_to_dt
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


def _mediation_ids(order: dict) -> list[Any]:
    """Ids das reclamações/mediações do pedido (`order.mediations[].id`, na
    ordem em que o ML lista; aceita item cru = id). Vazio = nunca abriu caso
    de pós-venda — o ML só preenche `mediations` quando abriu."""
    out: list[Any] = []
    for m in order.get("mediations") or []:
        cid = m.get("id") if isinstance(m, dict) else m
        if cid and cid not in out:
            out.append(cid)
    return out


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
    # quando abriu um caso de pós-venda). Aqui vale o primeiro.
    claim_id = next(iter(_mediation_ids(order)), None)
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


_SWEEP_JANELA_DIAS = 45
# Janela do /orders/search por date_last_updated (1 chamada cobre 50 pedidos).
_SWEEP_UPDATED_DIAS = 15
_SWEEP_MAX_PAGINAS = 50


async def sweep_pos_venda(session: AsyncSession) -> dict:
    """Re-olha TODAS as linhas ML da janela — inclusive as resolvidas que o
    painel esconde — e devolve os ids cuja situação de pós-venda MUDOU.

    Mesmo ponto cego dos sweeps Shopee/TikTok: o recarregar só re-enriquece as
    pendentes/visíveis; linha escondida (ex. "Pago | Enviado" sem ação) nunca
    mais era consultada, então entrega tardia ficava invisível pra sempre e a
    regra "Pago | Entregue → Entregue" não disparava (caso real: pedido
    2000018057490026 / Bling 291816, 28/08).

    Fonte barata: `/orders/search` do seller por `order.date_last_updated`
    (janela de 15d, 50 pedidos/página) — devolve `status` e `tags` (incl.
    "delivered"/"not_delivered") de quem MUDOU. Sinais contra o local:
      - tag delivered   e ship_status local != delivered;
      - tag not_delivered e ship_status local != not_delivered;
      - status do pedido  != order_status local.

    Diferente dos sweeps Shopee/TikTok, NÃO grava meli_status aqui: o search
    não traz a assinatura completa (substatus, claim, return). Só coleta ids;
    o recarregar os passa como `extras` — furando o escondimento — e o enrich
    completo atualiza a linha e aplica a regra no Bling.

    Limite honesto: claim/mediação que não muda status nem tags do pedido não
    gera sinal (o search não expõe claims); pra isso segue valendo o ⟳ da
    linha. `pedido_marketplace` pode ser order_id OU pack_id — casa contra os
    dois índices."""
    corte = datetime.now(UTC).date() - timedelta(days=_SWEEP_JANELA_DIAS)
    rows = (
        await session.execute(
            select(Logistica).where(
                func.lower(func.trim(Logistica.plataforma)).in_(tuple(_ML_PLATAFORMAS)),
                func.coalesce(Logistica.pedido_marketplace, "") != "",
                or_(Logistica.data.is_(None), Logistica.data >= corte),
            )
        )
    ).scalars().all()

    por_conta: dict[str, list[Logistica]] = {}
    for r in rows:
        por_conta.setdefault((r.conta or "").strip(), []).append(r)

    agora = datetime.now(UTC)
    fmt = "%Y-%m-%dT%H:%M:%S.000-00:00"
    date_to = agora.strftime(fmt)
    date_from = (agora - timedelta(days=_SWEEP_UPDATED_DIAS)).strftime(fmt)

    mudados: set[UUID] = set()
    contas_ok = n_hits = 0
    for conta, linhas in por_conta.items():
        integ = await _ml_integration_for_conta(session, conta)
        if integ is None:
            continue
        seller_id = decrypt_json(integ.credentials).get("user_id")
        if not seller_id:
            logger.warning("logistica_ml_sweep_sem_user_id", conta=conta)
            continue
        client = _build_ml_client(session, integ)
        contas_ok += 1

        por_id: dict[str, list[dict]] = {}
        por_pack: dict[str, list[dict]] = {}
        offset = paginas = 0
        try:
            while True:
                body = await client.search_orders_updated(
                    seller_id=seller_id, date_from=date_from, date_to=date_to,
                    limit=50, offset=offset,
                )
                results = body.get("results") or []
                for o in results:
                    oid = str(o.get("id") or "").strip()
                    if oid:
                        por_id.setdefault(oid, []).append(o)
                    pid = str(o.get("pack_id") or "").strip()
                    if pid and pid.lower() not in ("none", "null"):
                        por_pack.setdefault(pid, []).append(o)
                paging = body.get("paging") or {}
                offset += len(results)
                paginas += 1
                if (
                    not results
                    or offset >= int(paging.get("total") or 0)
                    or paginas >= _SWEEP_MAX_PAGINAS
                ):
                    break
        except Exception as e:  # noqa: BLE001 — best-effort por conta
            logger.warning(
                "logistica_ml_sweep_search_falhou", conta=conta, err=str(e)[:200]
            )
            continue

        for r in linhas:
            meli = r.meli_status or {}
            if not meli:
                continue  # nunca enriquecida — o backfill normal cuida dela
            pedido = (r.pedido_marketplace or "").strip()
            cands = [*(por_id.get(pedido) or []), *(por_pack.get(pedido) or [])]
            if not cands:
                continue
            ship_local = (meli.get("ship_status") or "").strip().lower()
            order_local = (meli.get("order_status") or "").strip().lower()
            for o in cands:
                tags = {str(t).strip().lower() for t in (o.get("tags") or [])}
                st = str(o.get("status") or "").strip().lower()
                if (
                    ("delivered" in tags and ship_local != "delivered")
                    or ("not_delivered" in tags and ship_local != "not_delivered")
                    or (st and st != order_local)
                ):
                    mudados.add(r.id)
                    n_hits += 1
                    break

    await session.commit()  # persiste tokens que refrescarem durante o sweep
    summary = {"seen": len(rows), "contas": contas_ok, "hits": n_hits}
    logger.info("logistica_ml_sweep_pos_venda", **summary)
    return {"ids": list(mudados), **summary}


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


# ---- devolução: o pacote que VOLTA (aba Acompanhamento de Devoluções) -------
#
# Eduardo (03/09): a aba mostrava o rastreio da ENTREGA original. No ML o
# pacote de volta é o shipment do RETURN do claim (id em
# `returns.shipments[0].shipment_id`), com tracking_number/status próprios —
# `GET /shipments/{id}` dele é o que alimenta `devolucao_rastreio.*_auto`.

# Pedidos consultados em paralelo. Cada um custa até 4 chamadas (order →
# returns do claim → shipment do return [→ claim, só pra datar]); 4 de cada vez
# fica longe do rate limit do ML e do 6 do enrich (que já roda junto).
_RETURNS_CONCURRENCY = 4
# Status (do return em si e do envio de volta) que dizem "esse caso morreu".
# Só entra na resposta quando o pedido não tem outro caso vivo.
_RETURN_DEAD = {"cancelled", "canceled", "closed", "rejected", "expired"}
_DT_MIN = datetime.min.replace(tzinfo=UTC)


class _ReturnCand(NamedTuple):
    """Um return de um claim, já com o que decide a escolha entre vários."""

    claim_id: str
    shipment_id: str | None
    shipment_status: str
    created_at: datetime | None
    updated_at: datetime | None
    live: bool


def _returns_as_list(rets: Any) -> list[dict]:
    """Payload de returns do claim (objeto único v2, lista, ou envelope
    `{results: [...]}`) → lista de returns (só dicts)."""
    if isinstance(rets, list):
        return [r for r in rets if isinstance(r, dict)]
    if not isinstance(rets, dict) or not rets:
        return []
    inner = rets.get("results")
    if isinstance(inner, list) and "shipments" not in rets:
        return [r for r in inner if isinstance(r, dict)]
    return [rets]


def _return_shipment(ret: dict) -> dict:
    """Envio de VOLTA de um return: `shipments[0]` (v2; id em `shipment_id`,
    defesa `id`) ou `shipping` (formato v1). `{}` quando ainda não há envio
    (devolução aberta sem postagem / só reembolso)."""
    shipments = ret.get("shipments")
    if isinstance(shipments, list):
        for sh in shipments:
            if isinstance(sh, dict):
                return sh
    shp = ret.get("shipping")
    return shp if isinstance(shp, dict) else {}


def _return_candidate(claim_id: str, ret: dict) -> _ReturnCand:
    sh = _return_shipment(ret)
    sid = sh.get("shipment_id") or sh.get("id")
    sh_status = str(sh.get("status") or "").strip()
    ret_status = str(ret.get("status") or "").strip()
    live = sh_status.lower() not in _RETURN_DEAD and ret_status.lower() not in _RETURN_DEAD
    return _ReturnCand(
        claim_id=claim_id,
        shipment_id=str(sid) if sid else None,
        # Sem envio ainda, o status do return em si (ex. opened) é o que há.
        shipment_status=sh_status or ret_status,
        created_at=iso_to_dt(ret.get("date_created")) or iso_to_dt(sh.get("date_created")),
        updated_at=iso_to_dt(ret.get("last_updated")) or iso_to_dt(sh.get("last_updated")),
        live=live,
    )


def _cand_key(c: _ReturnCand) -> tuple[datetime, int]:
    """Recência do caso: data de abertura (cai na última mexida); empate
    (payload sem datas) desempata pelo id do claim — no ML ele só cresce."""
    try:
        n = int(c.claim_id)
    except ValueError:
        n = 0
    return (c.created_at or c.updated_at or _DT_MIN, n)


async def _orders_do_pedido(client: MercadoLivreClient, pedido: str) -> list[dict]:
    """Mesma resolução do `_fetch_order` (o número guardado pode ser order id
    OU pack id), mas devolvendo TODOS os pedidos do pack — a devolução pode
    estar em qualquer um deles. Levanta só se nem order nem pack existirem."""
    try:
        return [await client.get_order(pedido)]
    except Exception as e:  # noqa: BLE001 — pode ser pack id
        logger.debug("logistica_meli_order_try_pack", pedido=pedido, err=str(e)[:120])
    pack = await client.get_pack(pedido)  # levanta se nem pack existir
    ids = [
        o.get("id") for o in (pack.get("orders") or []) if isinstance(o, dict) and o.get("id")
    ]
    if not ids:
        return [await client.get_order(pedido)]  # re-levanta o erro original limpo
    orders: list[dict] = []
    for oid in ids:
        try:
            orders.append(await client.get_order(str(oid)))
        except Exception as e:  # noqa: BLE001 — best-effort por pedido do pack
            logger.info(
                "logistica_meli_pack_order_failed", pedido=pedido, order_id=oid, err=str(e)[:120]
            )
    return orders


async def _return_info_for_pedido(client: MercadoLivreClient, pedido: str) -> ReturnInfo | None:
    """`ReturnInfo` do pacote que VOLTA de um pedido ML; None sem devolução.

    order(s) → `mediations[].id` → returns de cada claim (v2) → escolhe o caso
    (vivo mais recente, senão o mais recente) → `GET /shipments/{id}` do envio
    de volta (tracking_number/status/tracking_method). Claim sem return (404)
    e shipment que falhe são tolerados; levanta só se o pedido não existir."""
    orders = await _orders_do_pedido(client, pedido)
    claim_ids: list[str] = []
    for o in orders:
        for cid in _mediation_ids(o):
            if str(cid) not in claim_ids:
                claim_ids.append(str(cid))
    if not claim_ids:
        return None

    cands: list[_ReturnCand] = []
    for cid in claim_ids:
        try:
            rets = await client.get_claim_returns(cid)
        except Exception as e:  # noqa: BLE001 — claim sem devolução → 404
            logger.info(
                "logistica_meli_returns_none", pedido=pedido, claim_id=cid, err=str(e)[:120]
            )
            continue
        cands.extend(_return_candidate(cid, ret) for ret in _returns_as_list(rets))
    if not cands:
        return None

    vivos = [c for c in cands if c.live]
    esc = max(vivos or cands, key=_cand_key)

    sh: dict = {}
    if esc.shipment_id:
        try:
            sh = await client.get_shipment(esc.shipment_id) or {}
        except Exception as e:  # noqa: BLE001 — fica o status do payload de returns
            logger.warning(
                "logistica_meli_return_shipment_failed",
                pedido=pedido, claim_id=esc.claim_id, shipment_id=esc.shipment_id,
                err=str(e)[:200],
            )
            sh = {}

    created_at = esc.created_at
    if created_at is None:
        # Return sem data → quando o claim abriu (uma chamada a mais, só aqui).
        try:
            claim = await client.get_claim(esc.claim_id) or {}
        except Exception as e:  # noqa: BLE001
            logger.info(
                "logistica_meli_claim_failed", pedido=pedido, claim_id=esc.claim_id,
                err=str(e)[:120],
            )
            claim = {}
        created_at = iso_to_dt(claim.get("date_created")) or iso_to_dt(sh.get("date_created"))

    updated_at = max(
        (d for d in (iso_to_dt(sh.get("last_updated")), esc.updated_at) if d is not None),
        default=None,
    )
    status = str(sh.get("status") or "").strip() or esc.shipment_status or None
    tracking = str(sh.get("tracking_number") or "").strip() or None
    carrier = str(sh.get("tracking_method") or "").strip() or None
    return ReturnInfo(
        fonte="ml",
        status=status,
        tracking=tracking,
        carrier=carrier,
        created_at=created_at,
        updated_at=updated_at,
        return_id=esc.claim_id,
    )


async def returns_por_pedido(
    session: AsyncSession, linhas: list[Logistica]
) -> dict[str, ReturnInfo]:
    """`{pedido_bling: ReturnInfo}` do pacote que VOLTA, pras linhas ML dadas.

    Contrato em `devolucao_returns`: só entra pedido com devolução conhecida
    (ausente = desconhecido); com vários casos vale o vivo mais recente, senão
    o mais recente. Best-effort por conta/pedido: conta sem integração ML e
    pedido cuja API falhe são registrados e pulados — nunca levanta.

    Sem janela de data: cada pedido é resolvido direto (`/orders/{id}` →
    `mediations` → `/claims/{id}/returns` → `/shipments/{id}`), então pedido
    antigo funciona igual ao recente. Linhas de outra plataforma, sem
    `pedido_marketplace` ou sem `pedido_bling` são ignoradas.

    Não faz commit: só o refresh de token toca o banco (flush, serializado
    pelo lock) — quem chama persiste junto com o que gravar."""
    alvo: list[Logistica] = []
    for r in linhas:
        if (r.plataforma or "").strip().lower() not in _ML_PLATAFORMAS:
            continue
        if not (r.pedido_bling or "").strip() or not (r.pedido_marketplace or "").strip():
            continue
        alvo.append(r)
    if not alvo:
        return {}

    cache: dict[str, MercadoLivreClient] = {}
    lock = asyncio.Lock()
    await logistica_enrich.prewarm_clients(
        session,
        alvo,
        cache,
        resolve=_ml_integration_for_conta,
        build=lambda s, i: _build_ml_client(s, i, lock=lock),
    )

    # O mesmo pedido do marketplace pode estar em mais de uma linha: consulta
    # uma vez e espelha em todos os pedido_bling.
    por_pedido: dict[tuple[str, str], list[str]] = {}
    skipped = 0
    for r in alvo:
        if r.conta not in cache:
            skipped += 1
            continue
        chave = (str(r.conta), str(r.pedido_marketplace).strip())
        por_pedido.setdefault(chave, []).append(str(r.pedido_bling).strip())
    if skipped:
        logger.info("logistica_meli_returns_sem_integracao", linhas=skipped)

    out: dict[str, ReturnInfo] = {}
    failed = 0
    chaves = list(por_pedido)
    for lote in logistica_enrich.chunked(chaves, _RETURNS_CONCURRENCY):
        res = await asyncio.gather(
            *(_return_info_for_pedido(cache[conta], pedido) for conta, pedido in lote),
            return_exceptions=True,
        )
        for (conta, pedido), info in zip(lote, res, strict=True):
            if isinstance(info, BaseException):
                failed += 1
                logger.warning(
                    "logistica_meli_returns_pedido_failed",
                    conta=conta, pedido=pedido, err=str(info)[:200],
                )
                continue
            if info is None:
                continue
            for pedido_bling in por_pedido[(conta, pedido)]:
                out[pedido_bling] = info
    logger.info(
        "logistica_meli_returns_por_pedido",
        seen=len(linhas), alvo=len(alvo), pedidos=len(chaves),
        skipped=skipped, found=len(out), failed=failed,
    )
    return out
