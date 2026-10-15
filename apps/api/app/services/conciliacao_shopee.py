"""Conciliação — Shopee: a carteira é o extrato (passo 3, 15/10/2026).

Sondagem de 15/10 na loja Jlas (docs/conciliacao-avaliacao.md, seção 18): a chave
que o DaVinci já tem abre `POST /api/v2/payment/get_wallet_transaction_list`, que
devolve cada lançamento da carteira com `current_balance` (saldo após a linha →
prova dos nove igual ao Mercado Pago), `order_sn` na renda do pedido, saques,
ajustes, penalidades e Ads na mesma lista. Limites: ~15 dias por chamada e 100
por página (`more`). Não existe "fatura" na Shopee: as tarifas já vêm descontadas
na renda do pedido (o detalhe é o `escrow_detail`, que a Margem já lê).

Leitura: um dia por vez (fuso de Brasília), do último dia lido (sobreposição de
um dia; linha repetida é barrada pelo unique `transaction_id`) até ontem.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConciliacaoLoja, ConciliacaoMovimento, ConciliacaoSync, Integration
from app.models.conciliacao import ORIGEM_CARTEIRA
from app.services.conciliacao_extrato import bling_store_ids_da_loja, resolver_pedidos_bling

logger = structlog.get_logger()
BRT = ZoneInfo("America/Sao_Paulo")
ROTA_CARTEIRA = "/api/v2/payment/get_wallet_transaction_list"

# transaction_type da Shopee → tipo nosso (os mesmos nomes do extrato do MP onde cabe)
TIPOS = {
    "ESCROW_VERIFIED_ADD": "venda_liberada",
    "ESCROW_VERIFIED_DEDUCT": "estorno",
    "WITHDRAWAL_CREATED": "saque",
    "WITHDRAWAL_COMPLETED": "saque_confirmado",
    "WITHDRAWAL_CANCELLED": "saque_cancelado",
    "ADJUSTMENT_CENTER_ADD": "ajuste_shopee",
    "ADJUSTMENT_CENTER_DEDUCT": "ajuste_shopee",
    "SPM_DEDUCT": "publicidade",
    "SPM_ADD": "publicidade",
}
# Linha sem pedido que o robô já sabe o que é: ganha a classificação sozinha.
CLASSIFICACAO_AUTOMATICA = {
    "saque": "saque",
    "saque_confirmado": "saque",
    "saque_cancelado": "saque",
    "publicidade": "publicidade",
    "ajuste_shopee": "ajuste",
}


def _dinheiro(v: Any) -> Decimal:
    try:
        return Decimal(str(v if v is not None else 0)).quantize(Decimal("0.01"))
    except Exception:  # noqa: BLE001
        return Decimal("0")


def transacao_para_movimento(t: dict[str, Any]) -> dict[str, Any] | None:
    """Um lançamento da carteira → colunas de `conciliacao_movimentos` (sem a loja)."""
    tid = t.get("transaction_id")
    ct = t.get("create_time")
    if tid is None or not isinstance(ct, int | float):
        return None
    tipo_shopee = str(t.get("transaction_type") or "")
    tipo = TIPOS.get(tipo_shopee, "outro")
    valor = _dinheiro(t.get("amount"))
    order_sn = str(t.get("order_sn") or "").strip() or None
    descricao = (
        str(t.get("description") or "").strip()
        or str(t.get("reason") or "").strip()
        or str(t.get("txn_title") or "").strip()
        or tipo_shopee
    )
    saldo = t.get("current_balance")
    return {
        "origem": ORIGEM_CARTEIRA,
        "id_externo": str(tid),
        "data": datetime.fromtimestamp(int(ct), tz=UTC),
        "tipo": tipo,
        "descricao": descricao,
        "sub_tipo": tipo_shopee or None,
        "pagamento_id": str(t.get("refund_sn") or "").strip() or None,
        "pedido_marketplace": order_sn,
        "pack_id": None,
        "shipping_id": None,
        "pedido_bling": None,
        "bling_id": None,
        # a carteira só traz o líquido; a abertura (tarifa, frete) está no escrow_detail
        "bruto": None,
        "tarifa": None,
        "parcelamento": None,
        "frete": None,
        "cupom": None,
        "credito": valor if valor > 0 else Decimal("0"),
        "debito": -valor if valor < 0 else Decimal("0"),
        "saldo_apos": _dinheiro(saldo) if isinstance(saldo, int | float) else None,
        "descontado_da_venda": None,
        "sku": None,
        "item_id": None,
        "data_aprovacao": None,
        "classificacao": None if order_sn else CLASSIFICACAO_AUTOMATICA.get(tipo),
        "vinculado_por": None,
        "vinculado_em": None,
        "raw": t,
    }


async def ler_carteira(
    cliente: Any, t_de: int, t_ate: int, *, page_size: int = 100, max_paginas: int = 50
) -> list[dict[str, Any]]:
    """Todos os lançamentos entre dois instantes (epoch), paginando até `more` = false."""
    out: list[dict[str, Any]] = []
    for page_no in range(max_paginas):
        r = await cliente._request(
            "POST",
            ROTA_CARTEIRA,
            json={
                "page_no": page_no,
                "page_size": page_size,
                "create_time_from": t_de,
                "create_time_to": t_ate,
            },
        )
        if r.status_code != 200:
            raise RuntimeError(f"carteira: HTTP {r.status_code} {r.text[:200]}")
        body = r.json() or {}
        if body.get("error"):
            raise RuntimeError(f"carteira: {body.get('error')}: {body.get('message')}")
        resp = body.get("response") if isinstance(body.get("response"), dict) else {}
        lista = [x for x in (resp.get("transaction_list") or []) if isinstance(x, dict)]
        out.extend(lista)
        if not resp.get("more") or not lista:
            break
    return out


async def _sync_row(session: AsyncSession, loja: ConciliacaoLoja, chave: str) -> ConciliacaoSync:
    row = (
        await session.execute(
            select(ConciliacaoSync).where(
                ConciliacaoSync.integration_id == loja.integration_id,
                ConciliacaoSync.origem == ORIGEM_CARTEIRA,
                ConciliacaoSync.chave == chave,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = ConciliacaoSync(
            integration_id=loja.integration_id,
            origem=ORIGEM_CARTEIRA,
            chave=chave,
            status="pedido",
            detalhe={},
        )
        session.add(row)
        await session.flush()
    return row


async def inserir_movimentos(
    session: AsyncSession, loja: ConciliacaoLoja, movimentos: list[dict[str, Any]]
) -> int:
    for m in movimentos:
        m["integration_id"] = loja.integration_id
        m["platform"] = loja.platform
    inseridos = 0
    for k in range(0, len(movimentos), 200):
        stmt = (
            pg_insert(ConciliacaoMovimento)
            .values(movimentos[k : k + 200])
            .on_conflict_do_nothing(constraint="uq_conciliacao_mov_externo")
            .returning(ConciliacaoMovimento.id)
        )
        inseridos += len((await session.execute(stmt)).all())
    await session.flush()
    return inseridos


async def run_carteira_sync(
    session: AsyncSession,
    loja: ConciliacaoLoja,
    cliente: Any,
    *,
    hoje: date | None = None,
    max_dias: int = 20,
) -> dict[str, Any]:
    """Lê a carteira dia a dia, do último dia lido (de novo, por segurança) até ontem."""
    agora = datetime.now(UTC)
    hoje = hoje or datetime.now(BRT).date()
    ontem = hoje - timedelta(days=1)
    lidos = (
        await session.execute(
            select(ConciliacaoSync.chave).where(
                ConciliacaoSync.integration_id == loja.integration_id,
                ConciliacaoSync.origem == ORIGEM_CARTEIRA,
                ConciliacaoSync.status == "lido",
            )
        )
    ).scalars()
    ultimo = max((date.fromisoformat(c) for c in lidos), default=None)
    inicio = ultimo if ultimo else loja.inicio
    dias: list[date] = []
    d = inicio
    while d <= ontem and len(dias) < max_dias:
        dias.append(d)
        d += timedelta(days=1)
    resumo: dict[str, Any] = {"dias": [], "inseridos": 0, "chamadas": 0, "erros": []}
    if not dias:
        return resumo
    integration = await session.get(Integration, loja.integration_id)
    lojas_bling = await bling_store_ids_da_loja(session, integration) if integration else set()
    for dia in dias:
        t_de = int(datetime.combine(dia, datetime.min.time(), tzinfo=BRT).timestamp())
        t_ate = t_de + 86400 - 1
        row = await _sync_row(session, loja, dia.isoformat())
        row.pedido_em = agora
        try:
            lista = await ler_carteira(cliente, t_de, t_ate)
            resumo["chamadas"] += 1 + len(lista) // 100
            movs = [m for m in (transacao_para_movimento(t) for t in lista) if m]
            ids = {m["pedido_marketplace"] for m in movs if m["pedido_marketplace"]}
            mapa = await resolver_pedidos_bling(session, ids, lojas_bling)
            for m in movs:
                achado = mapa.get(m["pedido_marketplace"] or "")
                if achado:
                    m["pedido_bling"], m["bling_id"] = achado
            inseridos = await inserir_movimentos(session, loja, movs)
        except Exception as e:  # noqa: BLE001
            row.status = "erro"
            row.erro = f"{type(e).__name__}: {str(e)[:300]}"
            resumo["erros"].append({"dia": dia.isoformat(), "erro": row.erro})
            logger.warning(
                "conciliacao_carteira_falhou", loja=loja.conta, dia=dia.isoformat(), erro=row.erro
            )
            break  # mantém a ordem dos dias; tenta de novo na próxima rodada
        row.status = "lido"
        row.lido_em = agora
        row.erro = None
        row.linhas = len(lista)
        row.detalhe = {**(row.detalhe or {}), "inseridos": inseridos}
        loja.ultimo_extrato_em = agora
        resumo["inseridos"] += inseridos
        resumo["dias"].append(
            {"dia": dia.isoformat(), "linhas": len(lista), "inseridos": inseridos}
        )
    await session.flush()
    return resumo


async def reler_escrow_liberados(
    session: AsyncSession, loja: ConciliacaoLoja, *, maximo: int = 60
) -> dict[str, Any]:
    """Venda liberada cujo escrow a Margem leu ANTES da liberação: relê agora.

    15/10, os 5 "recebido a menos" da Jlas: a Shopee só põe a comissão de
    Afiliados (`order_ams_commission_fee`) e a taxa de serviço final no escrow na
    hora de liberar; a Margem tinha lido dias antes e guardava 16–73 a mais. A
    carteira estava certa. Então: pra cada renda de pedido cuja leitura da Margem
    é mais velha que a liberação, pede à Margem pra reler (uma chamada por pedido).
    """
    from app.models import MarketplaceOrderFinancial
    from app.services.marketplace_financials import run_sync_marketplace_financials_for_bling_order

    mov = ConciliacaoMovimento
    fin = MarketplaceOrderFinancial
    rows = (
        await session.execute(
            select(mov.bling_id, mov.pedido_marketplace, mov.data, fin.fetched_at)
            .select_from(mov)
            .join(
                fin,
                (fin.integration_id == mov.integration_id)
                & (fin.external_order_id == mov.pedido_marketplace),
            )
            .where(
                mov.integration_id == loja.integration_id,
                mov.origem == ORIGEM_CARTEIRA,
                mov.tipo == "venda_liberada",
                mov.bling_id.isnot(None),
                fin.fetched_at < mov.data,
            )
            .order_by(mov.data.desc())
            .limit(maximo)
        )
    ).all()
    vistos: set[int] = set()
    relidos = 0
    erros: list[dict[str, Any]] = []
    for bling_id, order_sn, _data, _lido in rows:
        if bling_id in vistos:
            continue
        vistos.add(bling_id)
        try:
            await run_sync_marketplace_financials_for_bling_order(
                session, bling_order_id=int(bling_id), trigger="conciliacao_liberacao"
            )
            relidos += 1
        except Exception as e:  # noqa: BLE001
            erros.append({"pedido": order_sn, "erro": f"{type(e).__name__}: {str(e)[:200]}"})
            logger.warning("conciliacao_escrow_releitura_falhou", loja=loja.conta, pedido=order_sn)
    await session.flush()
    return {"candidatos": len(rows), "relidos": relidos, "erros": erros}
