"""Preenche a assinatura de status da Amazon (`Logistica.meli_status`) +
localização puxando do Orders API (SP-API).

Espelha o `logistica_shopee.py`. A Amazon NÃO expõe número de rastreio pelo
Orders API (o `/shipment` dá 403 sem escopo), então a assinatura combina os DOIS
sinais que a API dá:

    {"order_status": "Shipped", "easyship_status": "Delivered"}

renderizados em PT por `logistica_rules.assinatura_amazon`. Como não há rastreio,
a localização é um PROXY: o `easyship_status` traduzido + destino (cidade/UF)
quando a Amazon devolve o endereço.

Só se aplica a pedidos Amazon. Best-effort: pedido que a Amazon não devolve fica
sem status (não derruba o lote).
"""

from __future__ import annotations

import asyncio
from collections.abc import Collection
from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy import Text, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Integration, IntegrationPlatform, Logistica
from app.security.cipher import decrypt_json, encrypt_json
from app.services import logistica_datas, logistica_enrich, logistica_rules
from app.services.marketplaces.amazon import AmazonClient

logger = structlog.get_logger()

_AMAZON_PLATAFORMAS = logistica_rules._AMAZON_PLATAFORMAS

# A SP-API é a mais restrita das quatro (Orders ~0,5 req/s por conta) — a rajada
# aqui é menor que a das outras. São poucas centenas de linhas, não é gargalo.
_CONCURRENCY = 2


def _amazon_destino(status: dict) -> str | None:
    """Destino `Cidade/UF` a partir do endereço do pedido (best-effort; a Amazon
    costuma redigir sem RDT → fica None)."""
    city = (status.get("ship_city") or "").strip()
    uf = (status.get("ship_state") or "").strip()
    if city and uf:
        return f"{city}/{uf}"
    return city or uf or None


async def build_enrichment(client: AmazonClient, order_id: str) -> dict:
    """Monta a assinatura da Amazon + rastreio (EasyShip, best-effort) +
    localização proxy.

    Retorna `{"meli_status": {"order_status": ..., "easyship_status": ...} | {},
    "rastreio": str | None, "localizacao": str | None, "datas": {...}}`.

    Rastreio vem da Easy Ship API — hoje 403 sem o papel de shipping aprovado
    (fica None), popula sozinho quando o papel for liberado.

    `datas` = quando o status mudou. A Amazon só data o PEDIDO inteiro
    (`LastUpdateDate`), não cada campo — então os dois entram como estimativa e
    o DaVinci refina quando vê o valor mudar (ver logistica_datas)."""
    order_id = str(order_id)
    st = await client.get_order_status(order_id) or {}
    meli: dict[str, str] = {}
    datas: dict[str, dict[str, str]] = {}
    atualizado_em = st.get("last_update_date")
    if st.get("order_status"):
        meli["order_status"] = str(st["order_status"])
        logistica_datas.propor(
            datas, "order_status", atualizado_em, logistica_datas.FONTE_APROX
        )
    if st.get("easyship_status"):
        meli["easyship_status"] = str(st["easyship_status"])
        logistica_datas.propor(
            datas, "easyship_status", atualizado_em, logistica_datas.FONTE_APROX
        )

    # Rastreio EasyShip (best-effort; 403 sem o papel de shipping → None).
    rastreio = await client.get_easyship_tracking(order_id)

    # Localização proxy: status físico (easyship) em PT + destino, quando houver.
    easy_pt = logistica_rules._amz_label(
        logistica_rules.AMAZON_EASYSHIP_LABELS_PT, meli.get("easyship_status")
    )
    destino = _amazon_destino(st)
    localizacao = logistica_rules.localizacao_completa(easy_pt, destino=destino) or None

    return {
        "meli_status": meli,
        "rastreio": rastreio,
        "localizacao": localizacao,
        "datas": {f: datas[f] for f in meli if f in datas},
    }


async def _amazon_integration_for_conta(
    session: AsyncSession, conta: str | None
) -> Integration | None:
    """Integração Amazon cuja `name` casa (trim+lower) com a `conta` da linha."""
    key = (conta or "").strip().lower()
    if not key:
        return None
    rows = (
        await session.execute(
            select(Integration).where(Integration.platform == IntegrationPlatform.AMAZON)
        )
    ).scalars().all()
    for it in rows:
        if (it.name or "").strip().lower() == key:
            return it
    return None


def _build_amazon_client(
    session: AsyncSession,
    integration: Integration,
    *,
    lock: asyncio.Lock | None = None,
) -> AmazonClient:
    creds = decrypt_json(integration.credentials)

    async def _persist(new_creds: dict) -> None:
        integration.credentials = encrypt_json(new_creds)
        exp = new_creds.get("expires_at") or new_creds.get("token_expires_at")
        if exp:
            integration.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
        # Único acesso ao banco durante a rajada concorrente do enrich_recent —
        # serializado pelo lock (sessão async não aceita flush simultâneo).
        async with (lock or logistica_enrich.NOLOCK):
            await session.flush()

    return AmazonClient(creds, on_token_refresh=_persist)


class AmazonEnrichError(Exception):
    """Falha de negócio ao enriquecer uma linha Amazon (código pro endpoint)."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


async def enrich_row(
    session: AsyncSession,
    row: Logistica,
    *,
    client_cache: dict[str, AmazonClient] | None = None,
) -> bool:
    """Preenche `row.meli_status`/localizacao puxando o status da Amazon. Retorna
    True se atualizou.

    Levanta `AmazonEnrichError` com código quando não dá pra prosseguir (linha
    não-Amazon, sem pedido de marketplace, conta sem integração Amazon)."""
    if (row.plataforma or "").strip().lower() not in _AMAZON_PLATAFORMAS:
        raise AmazonEnrichError("logistica_nao_amazon")
    order_id = (row.pedido_marketplace or "").strip()
    if not order_id:
        raise AmazonEnrichError("logistica_sem_pedido")

    conta = row.conta
    client: AmazonClient | None = None
    if client_cache is not None and conta in client_cache:
        client = client_cache[conta]
    else:
        integ = await _amazon_integration_for_conta(session, conta)
        if integ is None:
            raise AmazonEnrichError("logistica_sem_integracao")
        client = _build_amazon_client(session, integ)
        if client_cache is not None:
            client_cache[conta] = client

    enr = await build_enrichment(client, order_id)
    # Antes de trocar o status: o carimbo compara o valor velho com o novo.
    row.status_datas = logistica_datas.aplicar(row, enr["meli_status"], enr.get("datas"))
    row.meli_status = enr["meli_status"]
    if enr.get("rastreio"):
        row.rastreio = enr["rastreio"]
    if enr.get("localizacao"):
        row.localizacao = enr["localizacao"]
    # Divergência Amazon: order_status comercial × easyship físico.
    row.divergencia = logistica_rules.detectar_divergencia_amazon(
        row.meli_status, row.localizacao
    )
    return True


async def enrich_recent(
    session: AsyncSession,
    *,
    limit: int = 100,
    only_empty: bool = True,
    ids: Collection[UUID] | None = None,
) -> dict[str, int]:
    """Enriquece um lote de linhas Amazon (mais recentes primeiro), reusando o
    client por conta. `only_empty` pula linhas que já têm meli_status.
    `ids` restringe às linhas dadas (o recarregar passa as pendentes do
    painel) — aí o `limit` não se aplica."""
    stmt = select(Logistica).where(
        func.lower(func.trim(Logistica.plataforma)).in_(tuple(_AMAZON_PLATAFORMAS))
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

    cache: dict[str, AmazonClient] = {}
    lock = asyncio.Lock()
    await logistica_enrich.prewarm_clients(
        session,
        rows,
        cache,
        resolve=_amazon_integration_for_conta,
        build=lambda s, i: _build_amazon_client(s, i, lock=lock),
    )
    # Conta sem integração fica fora do cache: descarta aqui pra o enrich_row
    # não ir ao banco no meio da rajada concorrente.
    alvo = [r for r in rows if r.conta in cache]
    updated = 0
    skipped = len(rows) - len(alvo)
    failed = 0
    for lote in logistica_enrich.chunked(alvo, _CONCURRENCY):
        res = await asyncio.gather(
            *(enrich_row(session, r, client_cache=cache) for r in lote),
            return_exceptions=True,
        )
        for row, r in zip(lote, res, strict=True):
            if isinstance(r, AmazonEnrichError):
                skipped += 1
            elif isinstance(r, BaseException):
                failed += 1
                logger.warning(
                    "logistica_amazon_row_failed",
                    id=str(row.id), pedido=row.pedido_marketplace, err=str(r)[:200],
                )
            elif r:
                updated += 1
    await session.commit()
    summary = {"seen": len(rows), "updated": updated, "skipped": skipped, "failed": failed}
    logger.info("logistica_amazon_enrich_batch", **summary)
    return summary
