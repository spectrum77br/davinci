"""Pagamento da venda no Mercado Livre (aprovação, liberação, estorno e envio) pro
agente de chamados.

15/09 (Eduardo, consulta 482061374 / pedido 285250): três atendentes do ML
perguntaram se os R$ 940 de 1º de julho entraram na conta; o robô prometia
comprovante sem ter o dado, gastou as 3 réplicas e caiu pra humano. A API do ML
tinha a resposta: venda paga 01/07, liberada 29/07 e, em 24/08, o estorno ao
comprador saiu do programa de proteção do ML (refund `source.type = bpp`,
`status_detail = bpp_covered`) — não da conta da loja. Sem prejuízo: não há
compensação a pedir, o chamado pode ser encerrado.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import logistica_meli as lm

_SQL_VENDA = text(
    """
    select bo.numeroloja, si.account_name
    from davinci.bling_orders bo
    join davinci.store_info si on si.bling_store_id::text = bo.loja::text
    where bo.numero::text = :n
      and lower(coalesce(si.platform, '')) in ('ml', 'mercado livre', 'mercadolivre', 'meli')
    limit 1
    """
)


def _dt(v: object) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _data(d: datetime | None) -> str:
    return d.strftime("%d/%m/%Y") if d else "?"


def _moeda(v: float | None) -> str:
    if v is None:
        return "?"
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _num(v: object) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


async def pagamento_da_venda(session: AsyncSession, pedido_bling: str, clientes: dict) -> dict:
    """Fatos do pagamento da venda do pedido. `clientes` = cache de cliente ML por conta."""
    out: dict = {"pedido_bling": pedido_bling, "ok": False, "sem_prejuizo": False, "resumo": ""}
    row = (await session.execute(_SQL_VENDA, {"n": str(pedido_bling)})).first()
    if not row or not row[0]:
        out["erro"] = "pedido_sem_venda_ml"
        return out
    venda, conta = str(row[0]), row[1]
    out.update(venda=venda, conta=conta)
    chave = (conta or "").strip().lower()
    if chave not in clientes:
        integ = await lm._ml_integration_for_conta(session, conta)
        clientes[chave] = lm._build_ml_client(session, integ) if integ else None
    cliente = clientes[chave]
    if cliente is None:
        out["erro"] = "sem_integracao_ml"
        return out

    order = await lm._fetch_order(cliente, venda)
    out["pedido_status"] = order.get("status")
    pagamentos = [p for p in (order.get("payments") or []) if isinstance(p, dict) and p.get("id")]
    # o pagamento que vale é o maior aprovado/estornado (compra com 2 cartões tem 2)
    pagamentos.sort(key=lambda p: (_num(p.get("transaction_amount")) or 0), reverse=True)
    col: dict = pagamentos[0] if pagamentos else {}
    if col.get("id"):
        r = await cliente._request("GET", f"/collections/{col['id']}")
        if r.status_code == 200:
            j = r.json() or {}
            col = {**col, **(j.get("collection") or j)}

    pago_em = _dt(col.get("date_approved"))
    liberado_em = _dt(col.get("money_release_date"))
    valor_pago = _num(col.get("total_paid_amount")) or _num(col.get("transaction_amount"))
    estornos = [e for e in (col.get("refunds") or []) if isinstance(e, dict)]
    estorno_valor = sum(_num(e.get("amount")) or 0 for e in estornos) or _num(col.get("transaction_amount_refunded"))
    estorno_em = min((d for d in (_dt(e.get("date_created")) for e in estornos) if d), default=None)
    fontes = {((e.get("source") or {}).get("type") or "").lower() for e in estornos}
    pelo_ml = col.get("status_detail") == "bpp_covered" or (bool(estornos) and fontes == {"bpp"})

    envio_status = envio_sub = None
    sid = (order.get("shipping") or {}).get("id")
    if sid:
        r = await cliente._request("GET", f"/shipments/{sid}")
        if r.status_code == 200:
            j = r.json() or {}
            envio_status, envio_sub = j.get("status"), j.get("substatus")

    agora = datetime.now(UTC)
    liberado = bool(liberado_em and liberado_em <= agora)
    sem_prejuizo = bool(pago_em and col.get("status") == "refunded" and pelo_ml and liberado)

    partes = [f"Pagamento da venda {venda} no Mercado Livre (dados da API do ML):"]
    if pago_em:
        partes.append(f"pago em {_data(pago_em)}, {_moeda(valor_pago)}")
        if liberado_em:
            partes.append(f"valor liberado para a loja em {_data(liberado_em)}" if liberado
                          else f"liberação prevista para {_data(liberado_em)}")
    else:
        partes.append(f"pagamento não aprovado (status {col.get('status') or '?'})")
    if estornos or col.get("status") in ("refunded", "partially_refunded"):
        quem = ("pago pelo programa de proteção do Mercado Livre — NÃO saiu da conta da loja" if pelo_ml
                else "debitado da loja")
        partes.append(f"estorno ao comprador de {_moeda(estorno_valor)} em {_data(estorno_em)}, {quem}")
    else:
        partes.append("sem estorno registrado")
    if envio_status:
        partes.append(f"envio: {envio_status}{('/' + envio_sub) if envio_sub else ''}")
    if sem_prejuizo:
        partes.append("conclusão: a loja ficou com o valor da venda e o ML cobriu o comprador — sem prejuízo, nada a compensar")

    out.update(
        ok=True,
        pagamento_status=col.get("status"),
        pagamento_detalhe=col.get("status_detail"),
        valor_pago=valor_pago,
        pago_em=pago_em,
        liberado_em=liberado_em,
        estorno_valor=estorno_valor,
        estorno_em=estorno_em,
        estorno_fonte=",".join(sorted(f for f in fontes if f)) or None,
        envio_status=envio_status,
        envio_substatus=envio_sub,
        sem_prejuizo=sem_prejuizo,
        resumo=f"{partes[0]} {'; '.join(partes[1:])}.",
    )
    return out
