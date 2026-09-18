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
from decimal import Decimal, InvalidOperation
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


def _return_obj(rets: Any) -> dict | None:
    """O objeto da devolução: o payload de returns vem como objeto único OU
    lista (formato solto do ML) — normaliza."""
    if not rets:
        return None
    obj = rets[0] if isinstance(rets, list) and rets else rets
    return obj if isinstance(obj, dict) else None


# O ML devolve `shipments[]` em ORDEM ALEATÓRIA entre chamadas (visto 15/09 no
# claim 5570239837: ora [return, return_from_triage], ora o inverso). Pegar
# `[0]` fazia return_status e Localização oscilarem entre "galpão do ML" e
# "loja" a cada passada. A perna da triagem (galpão → loja) só existe depois da
# perna comprador → galpão, então é sempre a mais ATUAL; entre pernas do mesmo
# tipo, o shipment_id maior (mais novo).
_RETURN_LEG_PRIORIDADE = {"return_from_triage": 0, "return": 1}


def _return_leg_rank(leg: dict) -> tuple[int, int]:
    try:
        sid = int(leg.get("shipment_id") or 0)
    except (TypeError, ValueError):
        sid = 0
    return _RETURN_LEG_PRIORIDADE.get(str(leg.get("type") or "").strip(), 9), -sid


def _return_leg(rets: Any) -> dict | None:
    """A perna ATUAL do envio de devolução (item de `shipments[]`, formato
    v2), escolhida de forma DETERMINÍSTICA; None quando o payload não traz
    pernas (formato antigo: `shipping`/`status` no topo)."""
    obj = _return_obj(rets)
    if obj is None:
        return None
    shipments = obj.get("shipments")
    legs = [s for s in shipments if isinstance(s, dict)] if isinstance(shipments, list) else []
    if not legs:
        return None
    return min(legs, key=_return_leg_rank)


def _extract_return_status(rets: Any) -> str:
    """Status do envio da devolução (`return_status`) do payload de returns do
    claim (v2). Aceita objeto único OU lista; prioriza a perna atual de
    `shipments[]` (`_return_leg`) e cai em `shipping.status` / `status`
    (resiliência)."""
    obj = _return_obj(rets)
    if obj is None:
        return ""
    leg = _return_leg(rets)
    if leg is not None:
        s = (leg.get("status") or "").strip()
        if s:
            return s
    shp = obj.get("shipping") or {}
    s = (shp.get("status") or "").strip()
    if s:
        return s
    return (obj.get("status") or "").strip()


def _return_em(rets: Any) -> Any:
    """Última mexida na devolução (estimativa da data do `return_status`).
    Mesma perna do `_extract_return_status`; sem data na perna, a do objeto."""
    obj = _return_obj(rets)
    if obj is None:
        return None
    leg = _return_leg(rets)
    if leg is not None:
        em = leg.get("last_updated") or leg.get("date_created")
        if em:
            return em
    return obj.get("last_updated") or obj.get("date_created")


def _endereco_cidade_uf(addr: dict | None) -> str | None:
    """Cidade/UF de um endereço do ML. `city`/`state` podem vir como string ou
    objeto `{id, name}`; `state.id` costuma ser `BR-SP` → extrai o `SP`."""
    addr = addr or {}
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


def _ship_destino(sh: dict) -> str | None:
    """Cidade/UF de destino do shipment (`receiver_address`)."""
    return _endereco_cidade_uf(sh.get("receiver_address"))


def _return_destino(rets: Any) -> tuple[str | None, str | None]:
    """(tipo, cidade/UF) do destino do envio de DEVOLUÇÃO — a MESMA perna de
    onde sai o `return_status` (`_return_leg`), pra localização e status
    contarem a mesma história. `destination.name` = `warehouse` (galpão do ML,
    1ª perna) ou `seller_address` (loja, depois da triagem)."""
    leg = _return_leg(rets)
    if leg is None:
        return None, None
    dest = leg.get("destination") or {}
    if not isinstance(dest, dict):
        return None, None
    tipo = str(dest.get("name") or "").strip() or None
    return tipo, _endereco_cidade_uf(dest.get("shipping_address"))


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


def _claim_momento(claim: dict) -> str:
    """Instante que ordena um claim: a data da RESOLUÇÃO (quando fechou) vale
    mais que a última mexida, que vale mais que a abertura. Strings ISO do ML
    comparam bem como texto; vazio fica no fim."""
    res = claim.get("resolution") or {}
    momento = res.get("date_created") or claim.get("last_updated") or claim.get("date_created")
    return str(momento or "")


async def _claim_mais_recente(
    client: MercadoLivreClient, order: dict, order_id: str
) -> tuple[Any, dict]:
    """Entre as mediações do pedido, a mais RECENTE (pela resolução/última
    mexida). Devolve (claim_id, claim); (None, {}) se não há mediação; e
    (id, {}) se a única não pôde ser lida. Uma falha numa das leituras não
    derruba as outras — fica sem aquela."""
    ids = _mediation_ids(order)
    if not ids:
        return None, {}
    lidos: list[tuple[Any, dict]] = []
    for cid in ids:
        try:
            lidos.append((cid, await client.get_claim(cid)))
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "logistica_meli_claim_failed", order_id=order_id, claim_id=cid, err=str(e)[:200]
            )
    if not lidos:
        return ids[0], {}
    cid, claim = max(lidos, key=lambda par: _claim_momento(par[1]))
    if len(lidos) > 1:
        logger.info(
            "logistica_meli_varias_mediacoes",
            order_id=order_id,
            total=len(ids),
            escolhida=cid,
            benefited=(claim.get("resolution") or {}).get("benefited"),
        )
    return cid, claim


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
    loc_devolucao: str | None = None
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

    # Reclamação/mediação — os ids vêm em order.mediations[].id (ML só lista
    # quando abriu caso de pós-venda). Um pedido pode ter MAIS DE UMA: o
    # 2000018106772396 (Eduardo, 07/09) teve a 1ª fechada a favor do vendedor
    # em 03/09 e a 2ª a favor do comprador em 06/09, com reembolso — e o
    # código pegava "o primeiro", mostrando Beneficiado = Vendedor quando o
    # ML já tinha devolvido R$ 2.152 ao cliente. Vale a mais RECENTE: é o
    # desfecho que a tela do ML mostra e o que o dinheiro seguiu.
    claim_id, claim = await _claim_mais_recente(client, order, order_id)
    if claim_id:
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
            # Com devolução, a Localização conta a VOLTA do produto (pra onde e
            # em que pé), não a ida — que já terminou.
            dev_tipo, dev_destino = _return_destino(rets)
            loc_devolucao = (
                logistica_rules.localizacao_devolucao(
                    rstatus, destino_tipo=dev_tipo, destino=dev_destino
                )
                or None
            )

    # Mantém só campos conhecidos (defesa contra tokens estranhos entrando).
    meli = {f: out[f] for f in logistica_rules.FIELD_ORDER if out.get(f)}
    datas = {f: datas[f] for f in meli if f in datas}
    # Localização = proxy do "último local" (substatus/status do envio em PT) +
    # destino (cidade/UF) + previsão de entrega; o ML não dá o local físico da
    # rede própria. Devolução em curso/finalizada sobrepõe tudo isso.
    if loc_devolucao:
        localizacao: str | None = loc_devolucao
    else:
        status_pt = logistica_rules.localizacao_pt(meli)
        localizacao = (
            logistica_rules.localizacao_completa(status_pt, destino=destino, previsao=previsao)
            or None
        )
    return {
        "meli_status": meli,
        "rastreio": rastreio,
        "localizacao": localizacao,
        # True = a localização descreve a devolução (não o envio de ida).
        "localizacao_devolucao": bool(loc_devolucao),
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
    row.status_lido_em = datetime.now(UTC)
    if enr.get("rastreio"):
        row.rastreio = enr["rastreio"]
    # Localização: pra Correios (...BR) o físico do 17track manda — não deixa o
    # proxy do ML sobrescrever uma localização física já existente. A prova de
    # que é física é `localizacao_at` (carimbo do push/pull do 17track). Antes
    # a guarda olhava só "tem alguma localização?", e como o proxy do ML também
    # preenche essa coluna ele acabava se BLOQUEANDO: a primeira frase que ele
    # escrevia congelava para sempre. Foi o que travou o 291809 em "Aguardando
    # NF → Rio de Janeiro/RJ · previsão 25/08" (Eduardo, 04/09).
    new_loc = enr.get("localizacao")
    if new_loc and enr.get("localizacao_devolucao"):
        # Devolução em curso/finalizada: a coluna descreve a volta do produto.
        # A leitura física dos Correios era do envio de ida, que já acabou —
        # sai o carimbo (senão a tela mostra "Correios · lido há X" ao lado de
        # "Devolvido → loja") e não há físico pra cruzar com o ML.
        row.localizacao = new_loc
        row.localizacao_at = None
        row.divergencia = None
        return True
    if new_loc and not (logistica_track.is_correios(row.rastreio) and row.localizacao_at):
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
    # Mais de uma mediação: fala na mais recente (a 1ª pode já estar fechada).
    claim_id, claim = await _claim_mais_recente(client, order, order_id)
    if not claim_id:
        raise MeliEnrichError("logistica_sem_reclamacao")
    if not claim:
        claim = await client.get_claim(claim_id)  # sobe o erro cru do ML
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


# Pedido que a regra manda abrir chamado mas o ML recusou (sem reclamação do
# comprador ainda, claim encerrado…) só é tentado de novo depois deste prazo —
# o motor roda a cada 5 min e cada tentativa custa 2 chamadas na API do ML.
_CHAMADO_AUTO_RETRY = timedelta(hours=6)
# Recusas do ML que significam "não dá pela venda" (sem reclamação do comprador,
# claim encerrado ou sem ação pro vendedor) → o chamado vai pelo formulário.
_CODES_SEM_MEDIACAO = frozenset(
    {"logistica_sem_reclamacao", "logistica_reclamacao_encerrada", "logistica_reclamacao_sem_acao"}
)


async def abrir_chamados_em_lote(
    session: AsyncSession,
    ids: Collection[UUID] | None = None,
    *,
    agora: datetime | None = None,
) -> dict[str, int]:
    """Executor AUTOMÁTICO do "Abrir chamado" da aba Status (Eduardo 07/09:
    "se tá no status e o status da plataforma está batendo... não está abrindo
    chamado automático"). Pra cada linha ML que casa uma regra APLICÁVEL AO
    ESTADO ATUAL com `abrir_chamado` e ainda não tem `chamado`, faz o mesmo que
    o botão: `enviar_chamado_for_row` com a `mensagem_chamado` da regra
    (escala a mediação e manda o texto ao mediador). Roda no motor do
    recarregar ANTES da troca de situação no Bling — assim a regra que muda o
    status ainda está ativa quando o chamado abre.

    - abertos: chamado aberto agora (`chamado` preenchido).
    - falhas: ML recusou/erro → carimba `chamado_auto_at` + `chamado_auto_erro`
      e só tenta de novo depois de `_CHAMADO_AUTO_RETRY`.
    - adiados: dentro do prazo de retentativa (não bateu na API).
    - pulados: regra pede chamado mas está sem "Mensagem do chamado" — nada a
      mandar; fica carimbado `logistica_sem_mensagem_chamado` pra aparecer.
    Commit por linha (o job pode estourar o timeout no meio)."""
    from sqlalchemy.orm import selectinload

    from app.models import LogisticaStatus
    from app.services import chamados as chamados_svc
    from app.services import logistica_match

    agora = agora or datetime.now(UTC)
    status_rows = list(
        (
            await session.execute(
                select(LogisticaStatus).options(selectinload(LogisticaStatus.anexos))
            )
        )
        .scalars()
        .all()
    )
    stmt = select(Logistica)
    if ids is not None:
        stmt = stmt.where(Logistica.id.in_(list(ids)))
    rows = [
        r
        for r in (await session.execute(stmt)).scalars().all()
        if (r.plataforma or "").strip().lower() in _ML_PLATAFORMAS
    ]
    abertos = falhas = adiados = pulados = robo = 0
    for row in rows:
        if (row.chamado or "").strip():
            continue  # já tem chamado (motor ou operador)
        assinatura = logistica_rules.assinatura_para(row.plataforma, row.meli_status or {})
        cands = logistica_match.find_matching_rules(
            status_rows, assinatura=assinatura, plataforma=row.plataforma
        )
        aplicaveis = logistica_match.regras_aplicaveis(cands, row.status_bling)
        rule = next((r for r in aplicaveis if r.abrir_chamado), None)
        if rule is None:
            continue
        mensagem = (rule.mensagem_chamado or "").strip()
        if not mensagem:
            if row.chamado_auto_erro != "logistica_sem_mensagem_chamado":
                row.chamado_auto_at = agora
                row.chamado_auto_erro = "logistica_sem_mensagem_chamado"
            pulados += 1
            continue
        # Já tem chamado na aba pra esta linha? Com protocolo (o robô do
        # formulário devolveu) → sincroniza a linha; com abertura pendente
        # (robô ainda vai abrir) → não enfileira de novo.
        existente = await chamados_svc.chamado_da_logistica(session, row)
        if existente is not None:
            if (existente.chamado or "").strip():
                row.chamado = existente.chamado
                row.chamado_auto_at = agora
                row.chamado_auto_erro = None
                await session.commit()
                continue
            abertura = await chamados_svc.abertura_do_chamado(session, existente)
            if abertura is not None and abertura.status in ("pendente", "enviando"):
                adiados += 1
                continue
        if row.chamado_auto_at is not None and agora - row.chamado_auto_at < _CHAMADO_AUTO_RETRY:
            adiados += 1
            continue
        try:
            claim_id = await enviar_chamado_for_row(session, row, mensagem)
        except MeliEnrichError as e:
            row.chamado_auto_at = agora
            if e.code in _CODES_SEM_MEDIACAO:
                # Sem reclamação do comprador (ou já encerrada): não dá pela
                # venda → vai pelo formulário de ajuda, via robô (canal robô na
                # aba Chamados). Eduardo 07/09: "se tiver disponível pela venda,
                # faz pela venda; se não, pelo formulário".
                await chamados_svc.abrir_chamado_logistica(
                    session,
                    row,
                    mensagem=mensagem,
                    regra=rule.status_plataforma,
                    anexos=list(rule.anexos or []),
                )
                row.chamado_auto_erro = "encaminhado_ao_robo"
                robo += 1
                logger.info(
                    "logistica_chamado_auto_via_robo",
                    id=str(row.id),
                    pedido=row.pedido_marketplace,
                    motivo=e.code,
                )
            else:
                row.chamado_auto_erro = e.code
                falhas += 1
                logger.info(
                    "logistica_chamado_auto_recusado",
                    id=str(row.id),
                    pedido=row.pedido_marketplace,
                    code=e.code,
                )
        except Exception as e:  # noqa: BLE001 — best-effort, não derruba o lote
            row.chamado_auto_at = agora
            row.chamado_auto_erro = str(e)[:200]
            falhas += 1
            logger.warning(
                "logistica_chamado_auto_falhou",
                id=str(row.id),
                pedido=row.pedido_marketplace,
                err=str(e)[:200],
            )
        else:
            row.chamado_auto_at = agora
            row.chamado_auto_erro = None
            abertos += 1
            # Vai pra aba Chamados também (Eduardo 07/09: "lembra que vai para a
            # aba chamados").
            await chamados_svc.abrir_chamado_logistica(
                session,
                row,
                claim_id=claim_id,
                mensagem=mensagem,
                regra=rule.status_plataforma,
            )
            logger.info(
                "logistica_chamado_auto_aberto",
                id=str(row.id),
                pedido=row.pedido_marketplace,
                chamado=claim_id,
            )
        await session.commit()
    await session.commit()
    return {
        "abertos": abertos,
        "robo": robo,
        "falhas": falhas,
        "adiados": adiados,
        "pulados": pulados,
    }


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
      - tag delivered   e ship_status local != delivered  (entrega tardia);
      - tag not_delivered e ship_status local == delivered (reversão: estava
        entregue e voltou a não-entregue);
      - status do pedido  != order_status local.

    CUIDADO com a tag `not_delivered` (Eduardo, 15/09): o ML a coloca em todo
    pedido que AINDA não foi entregue — inclusive o que está simplesmente a
    caminho. Comparar com `ship_status != "not_delivered"` marcava como
    "mudou" todo pedido em trânsito, a cada rodada, para sempre: 343 de 2.037
    linhas por passada (na conta marquezini, 96 falsos contra 1 verdadeiro),
    ~8 mil chamadas/dia ao ML sem nenhuma mudança real — e era o que fazia a
    varredura demorar dezenas de minutos quando ainda rodava dentro do motor.

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
                    # `not_delivered` só vale como sinal quando CONTRADIZ o que
                    # temos: local "entregue" e o ML dizendo que não foi. Em
                    # trânsito a tag está sempre lá — ver docstring.
                    or ("not_delivered" in tags and ship_local == "delivered")
                    or (st and st != order_local)
                ):
                    mudados.add(r.id)
                    n_hits += 1
                    break

        # Salva por conta: o token renovado no meio da varredura não se perde
        # se o deploy matar o worker antes do fim (mesma razão do sweep Shopee).
        await session.commit()

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
    # Payload cru do return: é dele que sai a perna do vendedor (a que prova
    # que o pacote chegou em NÓS, e não no galpão do ML).
    raw: dict = {}


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


def _destino_do_shipment(sh: dict) -> str:
    return str(((sh or {}).get("destination") or {}).get("name") or "").strip().lower()


def _return_shipment(ret: dict) -> dict:
    """Envio de VOLTA de um return, priorizando a perna que vai PRO VENDEDOR.

    O ML parte a devolução em duas: o comprador posta pro galpão dele
    (`destination.name = warehouse`, em Cajamar) e, depois da triagem, o ML
    reenvia pro vendedor (`destination.name = seller_address`). Pegar
    `shipments[0]` misturava as duas — e a ordem da lista NÃO é estável (três
    GETs seguidos no mesmo claim vieram em ordens diferentes), então o
    rastreio e o status da tela variavam a cada rodada. Pior: "entregue" na
    perna do galpão significa que chegou no MERCADO LIVRE, não no vendedor.
    Quando não há perna do vendedor ainda, vale a primeira (o pacote está no
    caminho do galpão). Formato v1 continua caindo em `shipping`."""
    shipments = ret.get("shipments")
    if isinstance(shipments, list):
        validos = [sh for sh in shipments if isinstance(sh, dict)]
        for sh in validos:
            if _destino_do_shipment(sh) == "seller_address":
                return sh
        if validos:
            return validos[0]
    shp = ret.get("shipping")
    return shp if isinstance(shp, dict) else {}


def _entregue_ao_vendedor(ret: dict, detalhe: dict | None = None) -> datetime | None:
    """Quando o pacote de volta chegou AO VENDEDOR (não ao galpão do ML).

    Só conta a perna `seller_address` com `status=delivered`; a data exata sai
    do `status_history.date_delivered` do shipment, que o fluxo já busca."""
    sh = _return_shipment(ret)
    if _destino_do_shipment(sh) != "seller_address":
        return None
    if str(sh.get("status") or "").strip().lower() != "delivered":
        return None
    hist = ((detalhe or {}).get("status_history") or {})
    return (
        iso_to_dt(hist.get("date_delivered"))
        or iso_to_dt((detalhe or {}).get("date_delivered"))
        or iso_to_dt(ret.get("last_updated"))
    )


# Janela de REVISÃO da devolução no ML: depois que o pacote chega ao vendedor,
# ele tem estes dias pra abrir e reportar produto errado/danificado
# (`return_review_fail`). A API não manda a data; a doc de returns diz que o
# reembolso sai "3 dias depois de o vendedor receber" (`refund_at:
# delivered`) — é a mesma janela. Vinicius 16/09: "acho que é 2 ou 3 dias
# após a devolução chegar". Calculado = entrega + N (marcado na tela).
ML_DIAS_REVISAO = 3
_ML_ACAO_REVISAO = "return_review_fail"


def _jogadores_vendedor(claim: dict | None) -> list[dict]:
    """Ações liberadas pro VENDEDOR no claim (player `type=seller`; nas
    mediações vem como `role=respondent`) — lista crua de `available_actions`."""
    out: list[dict] = []
    for p in (claim or {}).get("players") or []:
        if not isinstance(p, dict):
            continue
        if (p.get("type") or "").lower() == "seller" or (p.get("role") or "").lower() == "respondent":
            out.extend(a for a in (p.get("available_actions") or []) if isinstance(a, dict))
    return out


def _acao_pendente_ml(
    claim: dict | None, entregue_em: datetime | None
) -> tuple[str | None, datetime | None]:
    """O que o ML espera da LOJA e até quando — a ação de prazo mais curto:

      - qualquer `available_actions[]` do vendedor com `due_date` (o ML dá
        data quando exige resposta: mensagem ao comprador/mediador, decidir
        reembolso...) → "ML_<ACTION>";
      - `return_review_fail` liberado + pacote já ENTREGUE ao vendedor →
        "ML_REVISAR_DEVOLUCAO" com prazo CALCULADO = entrega + ML_DIAS_REVISAO
        (medido 16/09: só aparece depois da entrega; some quando a janela
        fecha).
    Nunca levanta; sem nada → (None, None)."""
    cands: list[tuple[str, datetime]] = []
    acoes = _jogadores_vendedor(claim)
    for a in acoes:
        nome = str(a.get("action") or "").strip().lower()
        due = iso_to_dt(a.get("due_date"))
        if nome and due is not None:
            cands.append((f"ML_{nome.upper()}", due))
    if entregue_em is not None and any(
        str(a.get("action") or "").strip().lower() == _ML_ACAO_REVISAO for a in acoes
    ):
        cands.append(("ML_REVISAR_DEVOLUCAO", entregue_em + timedelta(days=ML_DIAS_REVISAO)))
    if not cands:
        return (None, None)
    return min(cands, key=lambda x: x[1])


def _return_candidate(claim_id: str, ret: dict) -> _ReturnCand:
    sh = _return_shipment(ret)
    sid = sh.get("shipment_id") or sh.get("id")
    sh_status = str(sh.get("status") or "").strip()
    ret_status = str(ret.get("status") or "").strip()
    live = sh_status.lower() not in _RETURN_DEAD and ret_status.lower() not in _RETURN_DEAD
    return _ReturnCand(
        raw=ret,
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


class _ReembolsoML(NamedTuple):
    pago: bool  # saiu dinheiro do NOSSO
    valor: Decimal | None  # devolvido ao cliente (mesmo quando o ML cobriu)
    em: datetime | None
    detalhe: str | None


def _reembolso_ml(orders: list[dict]) -> _ReembolsoML:
    """Reembolso ao cliente a partir dos PAGAMENTOS do pedido (já vêm no
    `/orders/{id}`, zero chamada a mais): soma de `transaction_amount_refunded`
    e a data mais recente entre os estornados.

    Estorno no pagamento = saiu do NOSSO: o pagamento é o dinheiro da venda, e
    devolvê-lo (inteiro ou em parte) é tirá-lo da loja. O `status_detail`
    "bpp_refunded"/"partially_bpp_refunded" e o `resolution.applied_coverage`
    do claim só dizem que o ML executou o estorno pela Proteção ao Comprador —
    medido 17/09 nos 12 pedidos ML em devolução: TODOS vieram assim, inclusive
    devolução normal (`item_returned`), então não servem pra dizer que o ML
    pagou do próprio bolso. Quando o ML cobre de verdade, o pagamento não é
    estornado (o comprador recebe por fora) e aqui fica "não saiu nada"."""
    total = Decimal("0")
    quando: datetime | None = None
    for o in orders:
        for pg in (o or {}).get("payments") or []:
            if not isinstance(pg, dict):
                continue
            try:
                v = Decimal(str(pg.get("transaction_amount_refunded") or 0))
            except (InvalidOperation, ValueError):
                v = Decimal("0")
            if v <= 0:
                continue
            total += v
            em = iso_to_dt(pg.get("date_last_modified"))
            if em is not None and (quando is None or em > quando):
                quando = em
    if total <= 0:
        return _ReembolsoML(False, None, None, None)
    return _ReembolsoML(True, total, quando, "Pagamento estornado ao cliente no Mercado Livre")


def _so_reembolso(reemb: _ReembolsoML, claim_id: str | None) -> ReturnInfo:
    """Pedido com caso no ML mas SEM devolução (mediação/reclamação sem pacote de
    volta, ou cancelamento com estorno): só a parte do reembolso interessa.
    `return_type` "REFUND" (vocabulário do TikTok) = não vem pacote — é o que
    manda o pedido pra aba Fraude. Quem chama (`returns_por_pedido`) desfaz
    esse "REFUND" quando o envio de ida está voltando/voltou: ver
    `_com_pacote_voltando`."""
    return ReturnInfo(
        fonte="ml",
        status=None,
        tracking=None,
        carrier=None,
        created_at=None,
        updated_at=None,
        return_id=claim_id,
        return_type="REFUND",
        reembolso=reemb.pago,
        reembolso_valor=reemb.valor,
        reembolso_em=reemb.em,
        reembolso_detalhe=reemb.detalhe,
    )


def _com_pacote_voltando(info: ReturnInfo, linhas: list[Logistica]) -> ReturnInfo:
    """Desfaz o "REFUND" (só dinheiro) quando a Logística mostra o envio de
    ida voltando/voltado pro vendedor. O ML não abre claim/return no
    cancelamento por não entrega: o pacote volta pelo próprio envio
    (`not_delivered` → `returning_to_sender` → `returned`), e só o estorno
    aparece pela API — o pedido caía na aba Fraude sem "Chegou em" (287876,
    18/09: estorno 02/08, pacote de volta 17/09). Tipo None = a plataforma não
    separa; a chegada então vem do `returned` (data_retorno_concluido)."""
    if (info.return_type or "").strip().upper() != "REFUND":
        return info
    if any(logistica_rules.pacote_volta_pelo_envio(r.plataforma, r.meli_status) for r in linhas):
        return info._replace(return_type=None)
    return info


async def _return_info_for_pedido(client: MercadoLivreClient, pedido: str) -> ReturnInfo | None:
    """`ReturnInfo` do pacote que VOLTA de um pedido ML; None sem devolução.

    order(s) → `mediations[].id` → returns de cada claim (v2) → escolhe o caso
    (vivo mais recente, senão o mais recente) → `GET /shipments/{id}` do envio
    de volta (tracking_number/status/tracking_method). Claim sem return (404)
    e shipment que falhe são tolerados; levanta só se o pedido não existir."""
    orders = await _orders_do_pedido(client, pedido)
    reemb = _reembolso_ml(orders)
    claim_ids: list[str] = []
    for o in orders:
        for cid in _mediation_ids(o):
            if str(cid) not in claim_ids:
                claim_ids.append(str(cid))
    if not claim_ids:
        # Sem caso — mas com estorno (cancelamento com reembolso) a coluna
        # "Reembolso" ainda precisa saber. Sem os dois: desconhecido.
        return _so_reembolso(reemb, None) if reemb.valor is not None else None

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
        # Caso sem devolução (mediação/reclamação só de dinheiro, como o
        # 296695): nada de pacote, mas o reembolso conta.
        return _so_reembolso(reemb, claim_ids[-1])

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

    claim: dict | None = None
    claim_falhou = False

    async def _claim() -> dict:
        nonlocal claim, claim_falhou
        if claim is None:
            try:
                claim = await client.get_claim(esc.claim_id) or {}
            except Exception as e:  # noqa: BLE001
                logger.info(
                    "logistica_meli_claim_failed", pedido=pedido, claim_id=esc.claim_id,
                    err=str(e)[:120],
                )
                claim, claim_falhou = {}, True
        return claim

    created_at = esc.created_at
    if created_at is None:
        # Return sem data → quando o claim abriu (uma chamada a mais, só aqui).
        c = await _claim()
        created_at = iso_to_dt(c.get("date_created")) or iso_to_dt(sh.get("date_created"))

    updated_at = max(
        (d for d in (iso_to_dt(sh.get("last_updated")), esc.updated_at) if d is not None),
        default=None,
    )
    status = str(sh.get("status") or "").strip() or esc.shipment_status or None
    tracking = str(sh.get("tracking_number") or "").strip() or None
    carrier = str(sh.get("tracking_method") or "").strip() or None
    entregue_em = _entregue_ao_vendedor(esc.raw, sh)
    # Prazo de resposta da loja (Vinicius 16/09): as ações que o ML liberou
    # pro vendedor moram no claim — uma chamada a mais SÓ enquanto a janela de
    # revisão pode estar aberta (pacote chegou há poucos dias). Em trânsito
    # não gasta chamada: nessa fase o ML não dá prazo à loja (medido 16/09,
    # 13 pedidos, nenhum due_date).
    acao = prazo = None
    prazo_desconhecido = False
    if entregue_em is not None and entregue_em >= datetime.now(UTC) - timedelta(
        days=ML_DIAS_REVISAO + 2
    ):
        c = await _claim()
        if claim_falhou:
            prazo_desconhecido = True
        else:
            acao, prazo = _acao_pendente_ml(c, entregue_em)
    return ReturnInfo(
        fonte="ml",
        status=status,
        tracking=tracking,
        carrier=carrier,
        created_at=created_at,
        updated_at=updated_at,
        return_id=esc.claim_id,
        entregue_em=entregue_em,
        acao_pendente=acao,
        prazo_acao=prazo,
        prazo_desconhecido=prazo_desconhecido,
        reembolso=reemb.pago,
        reembolso_valor=reemb.valor,
        reembolso_em=reemb.em,
        reembolso_detalhe=reemb.detalhe,
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
    linhas_por_pedido: dict[tuple[str, str], list[Logistica]] = {}
    skipped = 0
    for r in alvo:
        if r.conta not in cache:
            skipped += 1
            continue
        chave = (str(r.conta), str(r.pedido_marketplace).strip())
        por_pedido.setdefault(chave, []).append(str(r.pedido_bling).strip())
        linhas_por_pedido.setdefault(chave, []).append(r)
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
            info = _com_pacote_voltando(info, linhas_por_pedido[(conta, pedido)])
            for pedido_bling in por_pedido[(conta, pedido)]:
                out[pedido_bling] = info
    logger.info(
        "logistica_meli_returns_por_pedido",
        seen=len(linhas), alvo=len(alvo), pedidos=len(chaves),
        skipped=skipped, found=len(out), failed=failed,
    )
    return out
