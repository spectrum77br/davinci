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

import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConciliacaoLoja, ConciliacaoMovimento, ConciliacaoSync, Integration
from app.models.conciliacao import ORIGEM_TIKTOK
from app.services.conciliacao_dias import reabrir_dias
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


def dia_do_extrato(statement: dict[str, Any]) -> date | None:
    """O dia do extrato no painel = a data UTC do `statement_time` (a TikTok fecha o
    extrato à meia-noite UTC = 21:00 BRT da véspera e paga ~00:20 BRT). Assim o
    "extrato de ontem" é o repasse que caiu no banco ontem de madrugada — e não
    fica vazio até a TikTok publicar o próximo (15/10, Vinicius)."""
    quando = _epoch(statement.get("statement_time"))
    return quando.date() if quando else None


def _ancora(statement: dict[str, Any]) -> datetime | None:
    dia = dia_do_extrato(statement)
    return datetime.combine(dia, datetime.min.time(), tzinfo=BRT) if dia else None


# Extrato gerado mas ainda não pago (16/10: 7 das 8 lojas; a TikTok paga essas em
# lotes, dias depois). As linhas entram no dia do extrato marcadas "aguardando o
# repasse"; a linha de repasse só entra quando o status vira PAID. O extrato é fixo
# (pedidos e valores); só a transferência pode falhar e ser refeita.
#
# 16/10, revisão: "SEM_REPASSE" é marca nossa, não da TikTok — extrato PAID lido numa
# rodada em que a API de repasses falhou: as linhas entram, o repasse fica pra próxima
# rodada (antes o erro nos repasses derrubava a leitura dos extratos inteira).
AGUARDANDO = ("PROCESSING", "FAILED", "SEM_REPASSE")
SUFIXOS = {
    "PROCESSING": " · aguardando o repasse",
    "FAILED": " · repasse falhou, a TikTok tenta de novo",
    "SEM_REPASSE": " · repasse ainda não lido (a API de repasses falhou)",
}
STATUS_SYNC = {"PROCESSING": "processando", "FAILED": "falhou", "SEM_REPASSE": "sem_repasse"}


def aguardando_repasse(m: ConciliacaoMovimento) -> bool:
    raw = m.raw if isinstance(m.raw, dict) else {}
    return m.origem == ORIGEM_TIKTOK and str(raw.get("_payment_status") or "").upper() in AGUARDANDO


def _sem_sufixo(descricao: str | None) -> str:
    d = descricao or ""
    for suf in SUFIXOS.values():
        d = d.replace(suf, "")
    return d


async def _linhas_do_extrato(
    session: AsyncSession, loja: ConciliacaoLoja, sid: str
) -> list[ConciliacaoMovimento]:
    mov = ConciliacaoMovimento
    return list(
        (
            await session.execute(
                select(mov).where(
                    mov.integration_id == loja.integration_id,
                    mov.origem == ORIGEM_TIKTOK,
                    mov.raw["_statement_id"].astext == sid,
                )
            )
        )
        .scalars()
        .all()
    )


async def _marcar_status_do_extrato(
    session: AsyncSession, loja: ConciliacaoLoja, sid: str, status: str
) -> int:
    """Troca a marca das linhas já gravadas de um extrato (PROCESSING ↔ FAILED → PAID)."""
    linhas = await _linhas_do_extrato(session, loja, sid)
    for m in linhas:
        m.descricao = _sem_sufixo(m.descricao) + SUFIXOS.get(status, "")
        m.raw = {**(m.raw if isinstance(m.raw, dict) else {}), "_payment_status": status}
    return len(linhas)


def transacao_para_movimento(
    tx: dict[str, Any], statement: dict[str, Any], *, ordem: int, saldo_apos: Decimal
) -> dict[str, Any] | None:
    """Uma transação do extrato → colunas de `conciliacao_movimentos` (sem a loja)."""
    tid = tx.get("id")
    quando = _ancora(statement)
    if tid is None or quando is None:
        return None
    tipo_tt = str(tx.get("type") or "ORDER").upper()
    liquido = _d(tx.get("settlement_amount"))
    order_id = str(tx.get("order_id") or tx.get("adjustment_order_id") or "").strip() or None
    if tipo_tt == "ORDER":
        tipo = "venda_liberada" if liquido >= ZERO else "estorno"
    else:
        tipo = "ajuste_tiktok"
    if tipo == "venda_liberada":
        descricao = f"Liquidação do pedido {order_id}" if order_id else "Liquidação sem pedido"
    elif tipo == "estorno":
        descricao = f"Estorno do pedido {order_id}" if order_id else "Estorno sem pedido"
    else:
        descricao = f"Ajuste da TikTok ({tipo_tt.lower()})" + (
            f" · pedido {order_id}" if order_id else ""
        )
    return {
        "origem": ORIGEM_TIKTOK,
        "id_externo": str(tid),
        # todas as linhas no começo do dia do extrato, em ordem, pro saldo encadear
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
            "_payment_status": str(statement.get("payment_status") or "").upper(),
        },
    }


def _zerada_sem_pedido(m: dict[str, Any]) -> bool:
    return (
        m["tipo"] == "venda_liberada"
        and not m["pedido_bling"]
        and not m["classificacao"]
        and not (m["credito"] or ZERO)
        and not (m["debito"] or ZERO)
    )


def repasse_para_movimento(
    statement: dict[str, Any], pagamento: dict[str, Any] | None, *, ordem: int
) -> dict[str, Any] | None:
    quando = _ancora(statement)
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
        # identidade pelo EXTRATO (16/10, revisão): um pagamento pode cobrir vários
        # extratos (fim de semana pago junto com segunda) — pelo nº do pagamento só o
        # primeiro ganhava a linha e 33 extratos (R$ 95 mil) ficaram sem repasse
        "id_externo": f"repasse:{sid or pid}",
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


async def _reparar_repasses(
    session: AsyncSession,
    loja: ConciliacaoLoja,
    extratos: list[dict[str, Any]],
    repasses: dict[str, dict[str, Any]] | None,
    rows_sync: dict[str, ConciliacaoSync],
    agora: datetime,
) -> dict[str, Any]:
    """Repasse gravado com a identidade antiga (nº do pagamento) ganha a do extrato que
    ele representa; extrato pago e já lido sem a sua linha de repasse ganha uma — e o
    dia, se estava fechado, reabre pra conferência (fecha de novo sozinho se bater)."""
    vazio = {"renomeados": 0, "recuperados": [], "dias_reabertos": []}
    if repasses is None:
        return vazio  # a API de repasses falhou nesta rodada: nada a reparar sem ela
    gravados = {
        m.id_externo: m
        for m in (
            await session.execute(
                select(ConciliacaoMovimento).where(
                    ConciliacaoMovimento.integration_id == loja.integration_id,
                    ConciliacaoMovimento.origem == ORIGEM_TIKTOK,
                    ConciliacaoMovimento.id_externo.like("repasse:%"),
                )
            )
        ).scalars()
    }
    renomeados = 0
    for m in list(gravados.values()):
        sid = str(((m.raw or {}).get("statement") or {}).get("id") or "")
        novo = f"repasse:{sid}"
        if sid and m.id_externo != novo and novo not in gravados:
            gravados.pop(m.id_externo)
            m.id_externo = novo
            gravados[novo] = m
            renomeados += 1
    if renomeados:
        await session.flush()
    recuperados: list[str] = []
    desfeitos: list[str] = []
    dias: set[date] = set()
    for st in extratos:
        sid = str(st.get("id") or "")
        status = str(st.get("payment_status") or "").upper()
        row = rows_sync.get(sid)
        if not sid or status != "PAID" or row is None or row.status != "lido":
            continue
        if _reemitido(row):
            # extrato reemitido (transações já estavam gravadas por outro): não tem
            # repasse próprio; se o reparo de 16/10 criou um, sai e o dia reabre
            linha = gravados.get(f"repasse:{sid}")
            if (
                linha is not None
                and (row.detalhe or {}).get("repasse_recuperado_em")
                and not linha.vinculado_por
            ):
                await session.delete(linha)
                gravados.pop(f"repasse:{sid}", None)
                row.linhas = max(0, int(row.linhas or 0) - 1)
                row.detalhe = {
                    k: v for k, v in (row.detalhe or {}).items() if k != "repasse_recuperado_em"
                }
                desfeitos.append(sid)
                dias.add(dia_do_extrato(st))
            continue
        if f"repasse:{sid}" in gravados:
            continue
        pid = str(st.get("payment_id") or "")
        rep = repasse_para_movimento(st, repasses.get(pid), ordem=int(row.linhas or 0))
        if rep is None:
            continue
        if await inserir_movimentos(session, loja, [rep], reabrir=False):
            row.linhas = int(row.linhas or 0) + 1
            row.detalhe = {**(row.detalhe or {}), "repasse_recuperado_em": agora.isoformat()}
            recuperados.append(sid)
            dias.add(dia_do_extrato(st))
    if desfeitos:
        await session.flush()
    reabertos = await reabrir_dias(session, loja, dias, motivo="repasse da TikTok recuperado")
    if renomeados or recuperados or desfeitos:
        logger.info(
            "conciliacao_tiktok_repasses_reparados",
            loja=loja.conta,
            renomeados=renomeados,
            recuperados=recuperados,
            desfeitos=desfeitos,
            dias_reabertos=reabertos,
        )
    return {
        "renomeados": renomeados,
        "recuperados": recuperados,
        "desfeitos": desfeitos,
        "dias_reabertos": reabertos,
    }


def _reemitido(row: ConciliacaoSync) -> bool:
    """Extrato lido cujas transações já estavam todas gravadas (por outro extrato):
    reemissão da TikTok — não tem repasse próprio."""
    d = row.detalhe or {}
    if d.get("reemitido"):
        return True
    linhas = int(row.linhas or 0)
    return linhas > 0 and int(d.get("inseridos") or 0) == 0


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
    com as transações de cada um e o repasse; extrato já lido não é relido.

    16/10, revisão: extrato cujas transações não somam o total vira "erro" (relido na
    próxima rodada; nada entra); erro só na API de repasses não derruba os extratos
    (marca SEM_REPASSE); `loja.ultimo_extrato_em` só avança em rodada sem erro."""
    agora = agora or datetime.now(UTC)
    hoje = hoje or agora.astimezone(BRT).date()
    rows_sync = {
        r.chave: r
        for r in (
            await session.execute(
                select(ConciliacaoSync).where(
                    ConciliacaoSync.integration_id == loja.integration_id,
                    ConciliacaoSync.origem == ORIGEM_TIKTOK,
                )
            )
        ).scalars()
    }
    de = max(loja.inicio, hoje - timedelta(days=max_dias))
    # extrato cujo dia (data UTC do statement_time) é >= início
    t_de = int(datetime.combine(de, datetime.min.time(), tzinfo=UTC).timestamp())
    # a API filtra por DIA e o `lt` exclui o dia inteiro: com lt = agora, o extrato de
    # hoje (pago de madrugada) só aparecia amanhã (15/10, repasse 3.819,28) → lt = amanhã
    t_ate = int((agora + timedelta(days=1)).timestamp())
    resumo: dict[str, Any] = {
        "extratos": [],
        "inseridos": 0,
        "aguardando_repasse": 0,
        "repasse_falhou": 0,
        "sem_repasse": 0,
        "pagos_agora": 0,
        "erros": [],
    }
    integration = await session.get(Integration, loja.integration_id)
    lojas_bling = await bling_store_ids_da_loja(session, integration) if integration else set()
    try:
        extratos = await ler_extratos(cliente, t_de, t_ate)
    except Exception as e:  # noqa: BLE001
        resumo["erros"].append({"erro": f"{type(e).__name__}: {str(e)[:300]}"})
        logger.warning("conciliacao_tiktok_falhou", loja=loja.conta, erro=str(e)[:300])
        return resumo
    repasses: dict[str, dict[str, Any]] | None
    try:
        repasses = await ler_repasses(cliente, t_de - 7 * 86400, t_ate)
    except Exception as e:  # noqa: BLE001
        repasses = None  # extratos entram; repasse fica pra próxima rodada
        resumo["erros"].append({"erro": f"repasses: {type(e).__name__}: {str(e)[:300]}"})
        logger.warning("conciliacao_tiktok_repasses_falhou", loja=loja.conta, erro=str(e)[:300])
    contadores = {
        "PROCESSING": "aguardando_repasse",
        "FAILED": "repasse_falhou",
        "SEM_REPASSE": "sem_repasse",
    }
    resumo["repasses"] = await _reparar_repasses(
        session, loja, extratos, repasses, rows_sync, agora
    )
    for st in extratos:
        sid = str(st.get("id") or "")
        if not sid:
            continue
        status = str(st.get("payment_status") or "").upper()
        pid = str(st.get("payment_id") or "")
        existente = rows_sync.get(sid)
        if existente is not None and existente.status == "lido":
            continue
        if existente is not None and existente.status in STATUS_SYNC.values():
            # linhas já gravadas; falta só o repasse (ou mudou PROCESSING ↔ FAILED)
            if status == "PAID":
                if repasses is None:
                    resumo["sem_repasse"] += 1  # a API de repasses falhou: próxima rodada
                    continue
                rep = (
                    None
                    if _reemitido(existente)
                    else repasse_para_movimento(
                        st, repasses.get(pid), ordem=int(existente.linhas or 0)
                    )
                )
                await _marcar_status_do_extrato(session, loja, sid, "PAID")
                novos = await inserir_movimentos(session, loja, [rep]) if rep else 0
                existente.status = "lido"
                existente.lido_em = agora
                existente.erro = None
                existente.linhas = int(existente.linhas or 0) + novos
                existente.detalhe = {**(existente.detalhe or {}), "pago": True, "payment_id": pid}
                resumo["pagos_agora"] += 1
                resumo["inseridos"] += novos
                resumo["extratos"].append({"extrato": sid, "linhas": 1, "inseridos": novos})
            elif status in AGUARDANDO:
                if existente.status != STATUS_SYNC[status]:
                    await _marcar_status_do_extrato(session, loja, sid, status)
                    existente.status = STATUS_SYNC[status]
                resumo[contadores[status]] += 1
            continue
        if status != "PAID" and status not in AGUARDANDO:
            continue
        # a marca das linhas: a da TikTok, ou SEM_REPASSE se o repasse não pôde ser lido
        marca = "SEM_REPASSE" if (status == "PAID" and repasses is None) else status
        row = await _sync_row(session, loja, sid)
        row.pedido_em = agora
        try:
            txs = await ler_transacoes(cliente, sid)
            # 16/10, revisão: página perdida/transação faltando era gravada como "lido" e
            # nunca relida. Prova dos nove antes de gravar: soma das transações = total do
            # extrato; se não bate, nada entra e o extrato fica "erro" (relido na próxima)
            soma = sum((_d(tx.get("settlement_amount")) for tx in txs), ZERO)
            total = _d(st.get("settlement_amount"))
            if st.get("settlement_amount") not in (None, "") and soma != total:
                raise RuntimeError(
                    f"transações incompletas: {len(txs)} linha(s) somam {soma}, "
                    f"o extrato diz {total}"
                )
            txs.sort(key=lambda x: (int(x.get("order_create_time") or 0), str(x.get("id"))))
            movs: list[dict[str, Any]] = []
            saldo = ZERO
            for i, tx in enumerate(txs):
                liq = _d(tx.get("settlement_amount"))
                saldo += liq
                m = transacao_para_movimento(tx, st, ordem=i, saldo_apos=saldo)
                if m:
                    movs.append(m)
            rep = (
                repasse_para_movimento(st, repasses.get(pid), ordem=len(txs))
                if marca == "PAID" and repasses is not None
                else None
            )
            ids = {m["pedido_marketplace"] for m in movs if m["pedido_marketplace"]}
            mapa = await resolver_pedidos_bling(session, ids, lojas_bling)
            for m in movs:
                achado = mapa.get(m["pedido_marketplace"] or "")
                if achado:
                    m["pedido_bling"], m["bling_id"] = achado
                elif _zerada_sem_pedido(m):
                    # liquidação 0 de pedido que o Bling não tem (desfeito antes de
                    # faturar): não é dinheiro, não pode travar o dia (08/10, Mini)
                    m["classificacao"] = "outro"
                    m["descricao"] = f"{m['descricao']} (zerada: nada entrou, nada saiu)"
            if marca != "PAID":
                for m in movs:
                    m["descricao"] = f"{m['descricao']}{SUFIXOS[marca]}"
                    m["raw"] = {**m["raw"], "_payment_status": marca}
            inseridos = await inserir_movimentos(session, loja, movs)
            # 16/10 (revisão, Barbosa 08/10): a TikTok reemite um extrato com as MESMAS
            # transações de outro (estorno levado pra outro pagamento). As transações
            # não entram de novo (identidade) — e o repasse também não pode entrar,
            # senão o dia ganha um crédito a mais. Extrato reemitido = sem transação nova.
            reemitido = bool(txs) and inseridos == 0
            if rep and not reemitido:
                inseridos += await inserir_movimentos(session, loja, [rep])
            elif rep:
                rep = None
        except Exception as e:  # noqa: BLE001
            row.status = "erro"
            row.erro = f"{type(e).__name__}: {str(e)[:300]}"
            resumo["erros"].append({"extrato": sid, "erro": row.erro})
            logger.warning(
                "conciliacao_tiktok_extrato_falhou", loja=loja.conta, extrato=sid, erro=row.erro
            )
            continue
        row.status = "lido" if marca == "PAID" else STATUS_SYNC[marca]
        row.lido_em = agora
        row.erro = None
        row.linhas = len(txs) + (1 if rep else 0)
        row.detalhe = {
            "statement_time": st.get("statement_time"),
            "settlement_amount": st.get("settlement_amount"),
            "payment_id": st.get("payment_id"),
            "payment_status": status,
            "inseridos": inseridos,
            "reemitido": reemitido,
        }
        resumo["inseridos"] += inseridos
        if marca != "PAID":
            resumo[contadores[marca]] += 1
        resumo["extratos"].append({"extrato": sid, "linhas": row.linhas, "inseridos": inseridos})
    # 16/10 (2ª rodada): a lista de extratos foi lida — "última leitura" avança; erro de
    # um extrato (soma que não bate, repasses fora) fica em `erros` → ultimo_erro, e não
    # congela a loja em "extrato não lido" nem a reenfileira a cada 10 min
    loja.ultimo_extrato_em = agora
    await _classificar_zeradas_antigas(session, loja)
    await session.flush()
    return resumo


async def _classificar_zeradas_antigas(session: AsyncSession, loja: ConciliacaoLoja) -> int:
    """Liquidação zerada sem pedido no Bling gravada antes da regra: classifica igual."""
    mov = ConciliacaoMovimento
    rows = (
        (
            await session.execute(
                select(mov).where(
                    mov.integration_id == loja.integration_id,
                    mov.origem == ORIGEM_TIKTOK,
                    mov.tipo == "venda_liberada",
                    mov.pedido_bling.is_(None),
                    mov.classificacao.is_(None),
                    mov.vinculado_por.is_(None),
                    mov.credito == 0,
                    mov.debito == 0,
                )
            )
        )
        .scalars()
        .all()
    )
    for m in rows:
        m.classificacao = "outro"
        if "(zerada" not in (m.descricao or ""):
            m.descricao = f"{m.descricao or ''} (zerada: nada entrou, nada saiu)".strip()
    # 1ª versão escrevia "Order X" nos estornos
    velhos = (
        (
            await session.execute(
                select(mov).where(
                    mov.integration_id == loja.integration_id,
                    mov.origem == ORIGEM_TIKTOK,
                    mov.tipo == "estorno",
                    mov.descricao.like("Order %"),
                )
            )
        )
        .scalars()
        .all()
    )
    for m in velhos:
        m.descricao = m.descricao.replace("Order ", "Estorno do pedido ", 1)
    return len(rows) + len(velhos)


async def reler_financeiro_liquidados(
    session: AsyncSession,
    loja: ConciliacaoLoja,
    *,
    maximo: int = 12,
    orcamento_s: float = 300.0,
) -> dict[str, Any]:
    """Pedido que liquidou no extrato mas cuja venda na Margem ainda é a estimativa do
    unsettled (ou foi lida antes da liquidação): pede à Margem pra reler agora — uma
    chamada por pedido, só os que mudaram. Sem isso a estimativa (centavos diferente)
    viraria "recebido a menos/a mais" falso até a esteira lenta da Margem passar.

    Medido 15/10: a API financeira da TikTok leva ~40 s por pedido quando limita
    chamadas; por isso poucos por rodada e um teto de tempo — o resto fica pra
    próxima rodada (ou pra esteira da própria Margem).

    16/10, revisão: a fila anda do mais antigo pro mais novo, estimados na frente —
    ordenava do mais novo, e com o teto os antigos nunca chegavam a ser relidos."""
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
            # estimado (status ≠ posted) primeiro, depois do mais antigo pro mais novo
            # 16/10 (2ª rodada): o que foi relido há mais tempo vai primeiro — senão um
            # pedido sempre em error/estimated ocupava a frente da fila pra sempre
            .order_by(
                (fin.status == "posted").asc(), fin.fetched_at.asc().nullsfirst(), mov.data.asc()
            )
            .limit(maximo)
        )
    ).all()
    vistos: set[int] = set()
    relidos = 0
    erros: list[dict[str, Any]] = []
    t0 = time.monotonic()
    for bling_id, order_id, _data, _status, _lido in rows:
        if bling_id in vistos:
            continue
        if time.monotonic() - t0 > orcamento_s:
            logger.info("conciliacao_tiktok_releitura_teto", loja=loja.conta, relidos=relidos)
            break
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
