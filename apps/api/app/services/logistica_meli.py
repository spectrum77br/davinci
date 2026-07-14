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
    claim. Aceita objeto único OU lista; procura `shipping.status` e cai em
    `status`."""
    if not rets:
        return ""
    obj = rets[0] if isinstance(rets, list) and rets else rets
    if not isinstance(obj, dict):
        return ""
    shp = obj.get("shipping") or {}
    s = (shp.get("status") or "").strip()
    if s:
        return s
    return (obj.get("status") or "").strip()


async def build_meli_status(client: MercadoLivreClient, order_id: str) -> dict[str, str]:
    """Monta o dict dos 8 campos (só os não-vazios) pra um pedido do ML.

    Best-effort: falha em shipment/claim/returns deixa aqueles campos de fora,
    mas mantém os que já resolveram. Levanta só se o GET /orders/{id} falhar
    (aí o caller decide o que fazer com o pedido inteiro)."""
    out: dict[str, str] = {}
    order = await client.get_order(str(order_id))

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
    return {f: out[f] for f in logistica_rules.FIELD_ORDER if out.get(f)}


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

    meli = await build_meli_status(client, order_id)
    row.meli_status = meli
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
