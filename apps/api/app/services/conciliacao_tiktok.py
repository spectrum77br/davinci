"""Conciliação — TikTok: o extrato diário (statement) e o repasse (passo 4, 15/10/2026).

Sondagem de 15/10 na loja Mini (docs/conciliacao-avaliacao.md, seção 22): a TikTok
não tem carteira com saldo. Todo dia ela fecha UM extrato (`/finance/202309/statements`)
com as vendas liquidadas naquele dia — uma transação por pedido, com a conta aberta —
e manda o total pro banco como UM repasse (`/finance/202309/payments`, mesmo
`payment_id`). Prova dos nove: soma das transações = total do extrato = repasse.

Aqui cada transação vira um movimento (crédito = liquidação do pedido; estorno
liquidado negativo vira débito) e o repasse vira um "saque". Pra fechar a conta com
o mesmo rodapé das outras plataformas, o saldo é sintético: começa em 0 no extrato,
sobe com cada pedido e o repasse devolve a 0. Só extrato `PAID` entra (o `PROCESSING`
entra quando for pago — a TikTok paga minutos depois de fechar).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConciliacaoLoja, ConciliacaoMovimento, ConciliacaoSync, Integration
from app.models.conciliacao import ORIGEM_TIKTOK
from app.services.conciliacao_extrato import bling_store_ids_da_loja, resolver_pedidos_bling
from app.services.conciliacao_shopee import inserir_movimentos

logger = structlog.get_logger()
BRT = ZoneInfo("America/Sao_Paulo")
ROTA_EXTRATOS = "/finance/202309/statements"
ROTA_REPASSES = "/finance/202309/payments"
ZERO = Decimal("0")


def _d(v: Any) -> Decimal:
    try:
        return Decimal(str(v)).quantize(Decimal("0.01")) if v not in (None, "") else ZERO
    except Exception:  # noqa: BLE001
        return ZERO


def _epoch(v: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(v), tz=UTC) if v else None
    except (TypeError, ValueError, OSError):
        return None


def transacao_para_movimento(
    tx: dict[str, Any], statement: dict[str, Any], *, ordem: int, saldo_apos: Decimal
) -> dict[str, Any] | None:
    """Uma transação do extrato → colunas de `conciliacao_movimentos` (sem a loja)."""
    tid = tx.get("id")
    quando = _epoch(statement.get("statement_time"))
    if tid is None or quando is None:
        return None
    tipo_tt = str(tx.get("type") or "ORDER").upper()
    liquido = _d(tx.get("settlement_amount"))
    order_id = str(tx.get("order_id") or tx.get("adjustment_order_id") or "").strip() or None
    if tipo_tt == "ORDER":
        tipo = "venda_liberada" if liquido >= ZERO else "estorno"
    else:
        tipo = "ajuste_tiktok"
    descricao = (
        f"Liquidação do pedido {order_id}"
        if tipo == "venda_liberada" and order_id
        else f"{tipo_tt.title()} {order_id or ''}".strip()
    )
    return {
        "origem": ORIGEM_TIKTOK,
        "id_externo": str(tid),
        # todas as linhas do extrato no dia do extrato, em ordem, pro saldo encadear
        "data": quando + timedelta(seconds=ordem),
        "tipo": tipo,
        "descricao": descricao,
        "sub_tipo": tipo_tt,
        "pagamento_id": str(statement.get("payment_id") or "") or None,
        "pedido_marketplace": order_id,
        "pack_id": None,
        "shipping_id": None,
        "pedido_bling": None,
        "bling_id": None,
        "bruto": _d(tx.get("gross_sales_amount")).copy_abs() or None,
        "tarifa": _d(tx.get("fee_amount")).copy_abs() or None,
        "parcelamento": None,
        "frete": _d(tx.get("shipping_cost_amount")).copy_abs() or None,
        "cupom": _d(tx.get("seller_discount_amount")).copy_abs() or None,
        "credito": liquido if liquido > ZERO else ZERO,
        "debito": -liquido if liquido < ZERO else ZERO,
        "saldo_apos": saldo_apos,
        "descontado_da_venda": None,
        "sku": None,
        "item_id": None,
        "data_aprovacao": _epoch(tx.get("order_create_time")),
        "classificacao": None if order_id else "ajuste",
        "vinculado_por": None,
        "vinculado_em": None,
        "raw": {
            **tx,
            "_statement_id": str(statement.get("id") or ""),
            "_statement_time": statement.get("statement_time"),
        },
    }


def repasse_para_movimento(
    statement: dict[str, Any], pagamento: dict[str, Any] | None, *, ordem: int
) -> dict[str, Any] | None:
    quando = _epoch(statement.get("statement_time"))
    if quando is None:
        return None
    sid = str(statement.get("id") or "")
    pid = str(statement.get("payment_id") or (pagamento or {}).get("id") or "") or None
    valor = _d(statement.get("settlement_amount"))
    conta = (pagamento or {}).get("bank_account")
    pago_em = _epoch((pagamento or {}).get("paid_time") or statement.get("payment_time"))
    descricao = "Repasse pro banco"
    if conta:
        descricao += f" {conta}"
    if pago_em:
        descricao += f" · pago {pago_em.astimezone(BRT):%d/%m %H:%M}"
    return {
        "origem": ORIGEM_TIKTOK,
        "id_externo": f"repasse:{pid or sid}",
        "data": quando + timedelta(seconds=ordem),
        "tipo": "saque",
        "descricao": descricao,
        "sub_tipo": "PAYMENT",
        "pagamento_id": pid,
        "pedido_marketplace": None,
        "pack_id": None,
        "shipping_id": None,
        "pedido_bling": None,
        "bling_id": None,
        "bruto": None,
        "tarifa": None,
        "parcelamento": None,
        "frete": None,
        "cupom": None,
        "credito": ZERO if valor >= ZERO else -valor,
        "debito": valor if valor >= ZERO else ZERO,
        "saldo_apos": ZERO,  # o repasse zera o extrato
        "descontado_da_venda": None,
        "sku": None,
        "item_id": None,
        "data_aprovacao": None,
        "classificacao": "saque",
        "vinculado_por": None,
        "vinculado_em": None,
        "raw": {"statement": statement, "payment": pagamento or {}},
    }


async def _paginado(
    cliente: Any, path: str, params: dict[str, str], chave: str, *, max_paginas: int = 50
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    token: str | None = None
    for _ in range(max_paginas):
        p = dict(params)
        if token:
            p["page_token"] = token
        body = await cliente._get(path, p)
        if not isinstance(body, dict) or body.get("code") != 0:
            raise RuntimeError(
                f"tiktok {path}: code {body.get('code') if isinstance(body, dict) else '?'} "
                f"{str((body or {}).get('message') if isinstance(body, dict) else body)[:200]}"
            )
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        out.extend(x for x in (data.get(chave) or []) if isinstance(x, dict))
        token = data.get("next_page_token") or None
        if not token:
            break
    return out


async def ler_extratos(cliente: Any, t_de: int, t_ate: int) -> list[dict[str, Any]]:
    return await _paginado(
        cliente,
        ROTA_EXTRATOS,
        {
            "statement_time_ge": str(t_de),
            "statement_time_lt": str(t_ate),
            "page_size": "50",
            "sort_field": "statement_time",
            "sort_order": "ASC",
        },
        "statements",
    )


async def ler_transacoes(cliente: Any, statement_id: str) -> list[dict[str, Any]]:
    return await _paginado(
        cliente,
        f"{ROTA_EXTRATOS}/{statement_id}/statement_transactions",
        {"page_size": "100", "sort_field": "order_create_time", "sort_order": "DESC"},
        "statement_transactions",
    )


async def ler_repasses(cliente: Any, t_de: int, t_ate: int) -> dict[str, dict[str, Any]]:
    pagos = await _paginado(
        cliente,
        ROTA_REPASSES,
        {
            "create_time_ge": str(t_de),
            "create_time_lt": str(t_ate),
            "page_size": "50",
            "sort_field": "create_time",
            "sort_order": "ASC",
        },
        "payments",
    )
    return {str(p.get("id")): p for p in pagos if p.get("id") is not None}


async def _sync_row(session: AsyncSession, loja: ConciliacaoLoja, chave: str) -> ConciliacaoSync:
    row = (
        await session.execute(
            select(ConciliacaoSync).where(
                ConciliacaoSync.integration_id == loja.integration_id,
                ConciliacaoSync.origem == ORIGEM_TIKTOK,
                ConciliacaoSync.chave == chave,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = ConciliacaoSync(
            integration_id=loja.integration_id,
            origem=ORIGEM_TIKTOK,
            chave=chave,
            status="pedido",
            detalhe={},
        )
        session.add(row)
        await session.flush()
    return row


async def run_tiktok_sync(
    session: AsyncSession,
    loja: ConciliacaoLoja,
    cliente: Any,
    *,
    hoje: date | None = None,
    agora: datetime | None = None,
    max_dias: int = 45,
) -> dict[str, Any]:
    """Lê os extratos pagos desde o início da loja (ou os últimos `max_dias`) até agora,
    com as transações de cada um e o repasse; extrato já lido não é relido."""
    agora = agora or datetime.now(UTC)
    hoje = hoje or agora.astimezone(BRT).date()
    lidos = {
        r.chave
        for r in (
            await session.execute(
                select(ConciliacaoSync).where(
                    ConciliacaoSync.integration_id == loja.integration_id,
                    ConciliacaoSync.origem == ORIGEM_TIKTOK,
                    ConciliacaoSync.status == "lido",
                )
            )
        ).scalars()
    }
    de = max(loja.inicio, hoje - timedelta(days=max_dias))
    t_de = int(datetime.combine(de, datetime.min.time(), tzinfo=BRT).timestamp())
    t_ate = int(agora.timestamp()) + 1
    resumo: dict[str, Any] = {"extratos": [], "inseridos": 0, "pulados_processando": 0, "erros": []}
    integration = await session.get(Integration, loja.integration_id)
    lojas_bling = await bling_store_ids_da_loja(session, integration) if integration else set()
    try:
        extratos = await ler_extratos(cliente, t_de, t_ate)
        repasses = await ler_repasses(cliente, t_de - 7 * 86400, t_ate)
    except Exception as e:  # noqa: BLE001
        resumo["erros"].append({"erro": f"{type(e).__name__}: {str(e)[:300]}"})
        logger.warning("conciliacao_tiktok_falhou", loja=loja.conta, erro=str(e)[:300])
        return resumo
    for st in extratos:
        sid = str(st.get("id") or "")
        if not sid or sid in lidos:
            continue
        if str(st.get("payment_status") or "").upper() != "PAID":
            resumo["pulados_processando"] += 1
            continue
        row = await _sync_row(session, loja, sid)
        row.pedido_em = agora
        try:
            txs = await ler_transacoes(cliente, sid)
            txs.sort(key=lambda x: (int(x.get("order_create_time") or 0), str(x.get("id"))))
            movs: list[dict[str, Any]] = []
            saldo = ZERO
            for i, tx in enumerate(txs):
                liq = _d(tx.get("settlement_amount"))
                saldo += liq
                m = transacao_para_movimento(tx, st, ordem=i, saldo_apos=saldo)
                if m:
                    movs.append(m)
            rep = repasse_para_movimento(
                st, repasses.get(str(st.get("payment_id") or "")), ordem=len(txs)
            )
            if rep:
                movs.append(rep)
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
            resumo["erros"].append({"extrato": sid, "erro": row.erro})
            logger.warning(
                "conciliacao_tiktok_extrato_falhou", loja=loja.conta, extrato=sid, erro=row.erro
            )
            continue
        row.status = "lido"
        row.lido_em = agora
        row.erro = None
        row.linhas = len(txs) + (1 if rep else 0)
        row.detalhe = {
            "statement_time": st.get("statement_time"),
            "settlement_amount": st.get("settlement_amount"),
            "payment_id": st.get("payment_id"),
            "inseridos": inseridos,
        }
        loja.ultimo_extrato_em = agora
        resumo["inseridos"] += inseridos
        resumo["extratos"].append({"extrato": sid, "linhas": row.linhas, "inseridos": inseridos})
    if extratos:
        loja.ultimo_extrato_em = agora
    await session.flush()
    return resumo


async def reler_financeiro_liquidados(
    session: AsyncSession, loja: ConciliacaoLoja, *, maximo: int = 60
) -> dict[str, Any]:
    """Pedido que liquidou no extrato mas cuja venda na Margem ainda é a estimativa do
    unsettled (ou foi lida antes da liquidação): pede à Margem pra reler agora — uma
    chamada por pedido, só os que mudaram. Sem isso a estimativa (centavos diferente)
    viraria "recebido a menos/a mais" falso até a esteira lenta da Margem passar."""
    from app.models import MarketplaceOrderFinancial
    from app.services.marketplace_financials import run_sync_marketplace_financials_for_bling_order

    mov = ConciliacaoMovimento
    fin = MarketplaceOrderFinancial
    rows = (
        await session.execute(
            select(mov.bling_id, mov.pedido_marketplace, mov.data, fin.status, fin.fetched_at)
            .select_from(mov)
            .join(
                fin,
                (fin.integration_id == mov.integration_id)
                & (fin.external_order_id == mov.pedido_marketplace),
            )
            .where(
                mov.integration_id == loja.integration_id,
                mov.origem == ORIGEM_TIKTOK,
                mov.tipo == "venda_liberada",
                mov.bling_id.isnot(None),
                or_(fin.status != "posted", fin.fetched_at < mov.data),
            )
            .order_by(mov.data.desc())
            .limit(maximo)
        )
    ).all()
    vistos: set[int] = set()
    relidos = 0
    erros: list[dict[str, Any]] = []
    for bling_id, order_id, _data, _status, _lido in rows:
        if bling_id in vistos:
            continue
        vistos.add(bling_id)
        try:
            await run_sync_marketplace_financials_for_bling_order(
                session, bling_order_id=int(bling_id), trigger="conciliacao_liquidacao"
            )
            relidos += 1
        except Exception as e:  # noqa: BLE001
            erros.append({"pedido": order_id, "erro": f"{type(e).__name__}: {str(e)[:200]}"})
            logger.warning("conciliacao_tiktok_releitura_falhou", loja=loja.conta, pedido=order_id)
    await session.flush()
    return {"candidatos": len(rows), "relidos": relidos, "erros": erros}
