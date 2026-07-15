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

from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import Text, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Integration, IntegrationPlatform, Logistica
from app.security.cipher import decrypt_json, encrypt_json
from app.services import logistica_rules
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


def _ship_previsao(sh: dict) -> str | None:
    """Data prevista de entrega (dd/mm) a partir do `lead_time` do shipment;
    prefere o limite final, cai no limite/estimado."""
    lt = sh.get("lead_time") or {}
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

    Retorna `{"meli_status": {...}, "rastreio": str|None}`. Best-effort: falha
    em shipment/claim/returns deixa aqueles campos de fora, mas mantém os que já
    resolveram. Levanta só se nem order nem pack existirem."""
    out: dict[str, str] = {}
    rastreio: str | None = None
    destino: str | None = None
    previsao: str | None = None
    order = await _fetch_order(client, str(order_id))

    st = (order.get("status") or "").strip()
    if st:
        out["order_status"] = st

    cancel_detail = order.get("cancel_detail") or {}
    grp = (cancel_detail.get("group") or "").strip()
    if grp:
        out["cancel_group"] = grp

    shipping = order.get("shipping") or {}
    ship_id = shipping.get("id")
    if ship_id:
        try:
            sh = await client.get_shipment(str(ship_id))
        except Exception as e:  # noqa: BLE001
            logger.warning("logistica_meli_shipment_failed", order_id=order_id, err=str(e)[:200])
            sh = {}
        ship_status = (sh.get("status") or "").strip()
        if ship_status:
            out["ship_status"] = ship_status
        sub = (sh.get("substatus") or "").strip()
        if sub:
            out["ship_substatus"] = sub
        tn = (sh.get("tracking_number") or "").strip()
        if tn:
            rastreio = tn
        destino = _ship_destino(sh)
        previsao = _ship_previsao(sh)
    else:
        ship_status = (shipping.get("status") or "").strip()
        if ship_status:
            out["ship_status"] = ship_status

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
        stage = (claim.get("stage") or "").strip()
        if stage:
            out["claim_stage"] = stage
        cstatus = (claim.get("status") or "").strip()
        if cstatus:
            out["claim_status"] = cstatus
        benefited = (claim.get("resolution") or {}).get("benefited")
        if isinstance(benefited, list):
            benefited = benefited[0] if benefited else None
        if benefited:
            out["benefited"] = str(benefited).strip()
        try:
            rets = await client.get_claim_returns(claim_id)
        except Exception as e:  # noqa: BLE001
            logger.info("logistica_meli_returns_none", order_id=order_id, err=str(e)[:120])
            rets = None
        rstatus = _extract_return_status(rets)
        if rstatus:
            out["return_status"] = rstatus

    # Mantém só campos conhecidos (defesa contra tokens estranhos entrando).
    meli = {f: out[f] for f in logistica_rules.FIELD_ORDER if out.get(f)}
    # Localização = proxy do "último local" (substatus/status do envio em PT) +
    # destino (cidade/UF) + previsão de entrega; o ML não dá o local físico da
    # rede própria.
    status_pt = logistica_rules.localizacao_pt(meli)
    localizacao = (
        logistica_rules.localizacao_completa(status_pt, destino=destino, previsao=previsao) or None
    )
    return {"meli_status": meli, "rastreio": rastreio, "localizacao": localizacao}


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


def _build_ml_client(session: AsyncSession, integration: Integration) -> MercadoLivreClient:
    creds = decrypt_json(integration.credentials)

    async def _persist(new_creds: dict) -> None:
        integration.credentials = encrypt_json(new_creds)
        exp = new_creds.get("expires_at")
        if exp:
            integration.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
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
    row.meli_status = enr["meli_status"]
    if enr.get("rastreio"):
        row.rastreio = enr["rastreio"]
    if enr.get("localizacao"):
        row.localizacao = enr["localizacao"]
    return True


async def enrich_recent(
    session: AsyncSession,
    *,
    limit: int = 100,
    only_empty: bool = True,
) -> dict[str, int]:
    """Enriquece um lote de linhas ML (mais recentes primeiro), reusando o
    client por conta. `only_empty` pula linhas que já têm meli_status."""
    stmt = select(Logistica).where(
        func.lower(func.trim(Logistica.plataforma)).in_(tuple(_ML_PLATAFORMAS))
    )
    if only_empty:
        stmt = stmt.where(cast(Logistica.meli_status, Text) == "{}")
    stmt = stmt.order_by(Logistica.data.desc().nulls_last(), Logistica.created_at.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()

    cache: dict[str, MercadoLivreClient] = {}
    updated = 0
    skipped = 0
    failed = 0
    for row in rows:
        try:
            if await enrich_row(session, row, client_cache=cache):
                updated += 1
        except MeliEnrichError:
            skipped += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            logger.warning(
                "logistica_meli_row_failed",
                id=str(row.id), pedido=row.pedido_marketplace, err=str(e)[:200],
            )
    await session.commit()
    summary = {"seen": len(rows), "updated": updated, "skipped": skipped, "failed": failed}
    logger.info("logistica_meli_enrich_batch", **summary)
    return summary
