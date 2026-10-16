"""Conciliação — o recálculo: uma linha por pedido do Bling com as quatro
versões do dinheiro, o resultado, a situação e a etapa.

Regras (decididas com o Vinicius em 14/10/2026, `docs/conciliacao-avaliacao.md`):

* Entram os pedidos da loja vendidos a partir de `conciliacao_lojas.inicio`.
  Movimento de pedido anterior ganha a classificação `anterior_ao_inicio`.
* Recebido = créditos − débitos dos movimentos de dinheiro do extrato (as
  reservas são dinheiro travado e ficam de fora). Líquido previsto = o da
  Margem (`marketplace_order_financials.net_amount`).
* Situação: bateu / recebido a menos / a mais / aguardando / vencido /
  estornado depois / cancelado / Bling ≠ venda / sem venda. Tolerância R$ 0,05.
* Etapa: aguardando → conciliado ou pendência → finalizado (sozinho, 7 dias
  depois de bater sem novidade; à mão quando teve pendência) → reaberto
  (chegou movimento depois de finalizado) → finalizado de novo.
* Sobrou = recebido (ou previsto, enquanto não liberou) − custo − reembolso;
  margem = sobrou ÷ custo, a mesma conta da Margem.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingOrder,
    ConciliacaoLoja,
    ConciliacaoMovimento,
    ConciliacaoPedido,
    Integration,
    Logistica,
    MarketplaceOrderFinancial,
    Refund,
    SituacaoBling,
)
from app.models.conciliacao import ETAPA_PELO_ROBO, ORIGEM_EXTRATO, ORIGEM_FATURA, ORIGENS_EXTRATO
from app.services.conciliacao_dias import reabrir_dias
from app.services.conciliacao_extrato import (
    TIPOS_RESERVA,
    apagar_duplicatas_pobres,
    bling_store_ids_da_loja,
    classificar_cobrancas_da_fatura,
    classificar_vendas_desfeitas,
    resolver_pedidos_bling,
)
from app.services.conciliacao_fatura import tipo_do_detalhe

logger = structlog.get_logger()
BRT = ZoneInfo("America/Sao_Paulo")

TOLERANCIA = Decimal("0.05")
DIAS_FINALIZAR = 7
DIAS_VENCIDO_SEM_PREVISAO = 45
FOLGA_PREVISAO = timedelta(days=2)
# Shopee e TikTok não dizem quando liberam (16/10, revisão): 2.230 pedidos "aguardando"
# sem data e o "vencido" nunca disparava. Previsão = entrega confirmada (Logística) +
# N dias; sem entrega lida, data do pedido + M dias. (plataforma: (N, M) — ajustar aqui.)
PRAZO_LIBERACAO: dict[str, tuple[int, int]] = {"shopee": (9, 22), "tiktok": (9, 22)}
# Situação do Bling que quer dizer "não vai receber": cancelado ou excluído (16/10,
# revisão: 13 pedidos excluídos no Bling ficavam "aguardando" pra sempre)
BLING_DESFEITO = ("cancelado", "excluido", "excluído")
MARGEM_MAXIMA = Decimal("999999")  # Numeric(10,4) da coluna margem
ZERO = Decimal("0.00")


def _d(v: Any) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v)).quantize(Decimal("0.01"))
    except Exception:  # noqa: BLE001
        return None


def _soma(valores: list[Decimal | None]) -> Decimal:
    return sum((v for v in valores if v is not None), ZERO)


def _previsao_do_billing(raw: Any) -> datetime | None:
    """`raw.billing.results[].payment_info[].money_release_date` da Margem."""
    if not isinstance(raw, dict):
        return None
    billing = raw.get("billing")
    if not isinstance(billing, dict):
        return None
    for res in billing.get("results") or []:
        if not isinstance(res, dict):
            continue
        for p in res.get("payment_info") or []:
            if isinstance(p, dict) and p.get("money_release_date"):
                try:
                    d = datetime.fromisoformat(str(p["money_release_date"]))
                except ValueError:
                    continue
                return d if d.tzinfo else d.replace(tzinfo=BRT)
    return None


def _venda_cancelada(fin: MarketplaceOrderFinancial | None) -> bool:
    if fin is None:
        return False
    liquido = _d(fin.net_amount)
    estorno = _d(fin.refund_amount)
    if liquido is not None and liquido == ZERO and estorno and estorno > ZERO:
        return True
    raw = fin.raw if isinstance(fin.raw, dict) else {}
    order = raw.get("order") if isinstance(raw.get("order"), dict) else {}
    return str(order.get("status") or "").lower() == "cancelled"


def _escrow_income(venda: MarketplaceOrderFinancial | None) -> dict[str, Any] | None:
    raw = venda.raw if (venda is not None and isinstance(venda.raw, dict)) else None
    escrow = raw.get("escrow") if raw else None
    income = escrow.get("order_income") if isinstance(escrow, dict) else None
    return income if isinstance(income, dict) and income.get("escrow_amount") is not None else None


def conta_shopee(venda: MarketplaceOrderFinancial | None) -> dict[str, Decimal] | None:
    """A conta da venda do jeito que a tela da Shopee mostra em "Detalhes de Renda"
    (15/10, pedido 301438): preço de venda − subsídio da Shopee no produto = subtotal
    dos produtos; ± frete da loja; − cupons da loja; − taxas e encargos (líquidas do
    subsídio) = renda do pedido. A Margem guarda as taxas BRUTAS (722,36) e o desconto
    de tabela (5.078); aqui é o líquido (354,85) e só o que sai do bolso da loja."""
    inc = _escrow_income(venda)
    if inc is None:
        return None

    def d(k: str) -> Decimal:
        v = inc.get(k)
        try:
            return Decimal(str(v)) if v not in (None, "") else ZERO
        except Exception:  # noqa: BLE001
            return ZERO

    rebate = inc.get("seller_product_rebate")
    subsidio = Decimal(str(rebate.get("amount") or 0)) if isinstance(rebate, dict) else ZERO
    preco = d("order_selling_price") or d("cost_of_goods_sold")
    # 15/10, Vinicius: afiliados não é "a porcentagem sempre igual" — sai da tarifa
    # e vai pra Extras; a tarifa fica com comissão, serviço, transação e encargos
    afiliados = d("order_ams_commission_fee")
    taxas = (
        d("net_commission_fee")
        + d("net_service_fee")
        + d("shipping_seller_protection_fee_amount")
        + d("seller_transaction_fee")
        + d("campaign_fee")
    )
    frete_loja = (
        d("actual_shipping_fee") - d("buyer_paid_shipping_fee") - d("shopee_shipping_rebate")
    )
    cupons = d("voucher_from_seller") + d("seller_coin_cash_back")
    return {
        "preco": preco,
        "subsidio": subsidio,
        "subtotal_produtos": preco - subsidio,
        "frete_loja": frete_loja,
        "cupons": cupons,
        "taxas": taxas,
        "afiliados": afiliados,
        "renda": d("escrow_amount"),
    }


_TAXAS_TIKTOK_CONHECIDAS = (
    "platform_commission_amount",
    "transaction_fee_amount",
    "referral_fee_amount",
    "affiliate_commission_amount",
    "affiliate_ads_commission_amount",
    "affiliate_partner_commission_amount",
)


def conta_tiktok_de_transacoes(txs: list[Any]) -> dict[str, Decimal] | None:
    """A conta de um pedido como a TikTok fecha no extrato diário (15/10, Mini):
    soma as transações do pedido (estorno liquidado depois vem como outra transação).
    Os campos vêm COM sinal (taxa, frete e reembolso negativos; receita positiva), e
    a identidade que a Margem conferiu em pedidos reais é
    liquidação = receita + taxas + frete (+ ajustes). O que sobrar vai pra "outros",
    pra tabela sempre fechar."""
    txs = [t for t in txs if isinstance(t, dict)]
    if not txs:
        return None

    def soma(*chaves: str) -> Decimal:
        total = ZERO
        for t in txs:
            for k in chaves:
                v = t.get(k)
                if v in (None, ""):
                    continue
                try:
                    total += Decimal(str(v))
                except (InvalidOperation, ValueError):
                    continue
        return total.quantize(Decimal("0.01"))

    apos = soma("after_seller_discounts_subtotal_amount")
    receita = soma("revenue_amount")
    comissao = soma("platform_commission_amount")
    taxa_transacao = soma("transaction_fee_amount", "referral_fee_amount")
    afiliados = soma(
        "affiliate_commission_amount",
        "affiliate_ads_commission_amount",
        "affiliate_partner_commission_amount",
    )
    taxas = soma("fee_amount")
    frete = soma("shipping_cost_amount")
    ajustes = soma("adjustment_amount")
    liquido = soma("settlement_amount")
    return {
        "vendas_brutas": soma("gross_sales_amount"),
        "desconto_loja": soma("seller_discount_amount"),  # negativo
        "apos_desconto": apos,
        # subsídio da TikTok no produto, reembolsos ao cliente e o mais que entra na receita
        "outros_receita": receita - apos,
        "receita": receita,
        "comissao": comissao,
        "taxa_transacao": taxa_transacao,
        "afiliados": afiliados,
        "outras_taxas": taxas - (comissao + taxa_transacao + afiliados),
        "taxas": taxas,  # negativo
        "frete": frete,  # negativo = a loja pagou; positivo = sobrou do que o cliente pagou
        "frete_real": soma("actual_shipping_fee_amount"),
        "frete_cliente": soma("customer_paid_shipping_fee_amount"),
        "ajustes": ajustes,
        "outros": liquido - (receita + taxas + frete + ajustes),
        "liquido": liquido,
    }


def conta_tiktok(venda: MarketplaceOrderFinancial | None) -> dict[str, Decimal] | None:
    """A conta do pedido a partir do que a Margem guardou (`raw.settlements` = a
    resposta de `orders/{id}/statement_transactions`). Pedido só estimado (unsettled)
    não tem transação ainda → None."""
    raw = venda.raw if (venda is not None and isinstance(venda.raw, dict)) else None
    body = raw.get("settlements") if raw else None
    data = body.get("data") if isinstance(body, dict) else None
    txs = data.get("statement_transactions") if isinstance(data, dict) else None
    return conta_tiktok_de_transacoes(txs if isinstance(txs, list) else [])


def conta_amazon_de_transacoes(txs: list[Any]) -> dict[str, Decimal] | None:
    """A conta de um pedido na Amazon somando as quebras de todas as transações dele
    (Shipment + ServiceFee + Refund…), como a tela Transações (15/10, seção 24)."""
    from collections import defaultdict

    from app.services.conciliacao_amazon import conta_amazon_de_quebras, somar_quebras

    # só a venda e as tarifas dela (Shipment + ServiceFee): estorno é "depois", não
    # faz parte do que a venda prometeu (15/10, Amazon 302142)
    txs = [
        t
        for t in txs
        if isinstance(t, dict)
        and str(t.get("transactionType") or "Shipment") in ("Shipment", "ServiceFee")
    ]
    if not txs:
        return None
    acc: dict[str, Decimal] = defaultdict(Decimal)
    total = ZERO
    for t in txs:
        somar_quebras(t.get("breakdowns"), acc)
        for it in t.get("items") or []:
            if isinstance(it, dict):
                somar_quebras(it.get("breakdowns"), acc)
        amt = (t.get("totalAmount") or {}).get("currencyAmount")
        try:
            total += Decimal(str(amt)) if amt not in (None, "") else ZERO
        except InvalidOperation:
            pass
    conta = conta_amazon_de_quebras(dict(acc))
    conta["liquido"] = total.quantize(Decimal("0.01"))
    conta["outros"] = conta["liquido"] - (
        conta["produto"] + conta["frete_cliente"] + conta["promocao"] + conta["tarifas"]
    )
    return conta


def _am_proprio(venda: MarketplaceOrderFinancial | None) -> bool:
    """Envio próprio da Amazon pela conta do pedido: frete cobrado do cliente e nenhuma
    etiqueta Easy Ship (quando a Logística ainda não classificou o canal)."""
    c = conta_amazon(venda)
    return bool(c and c["frete_cliente"] > ZERO and not c["etiqueta"])


def motivo_amazon(movs: list[ConciliacaoMovimento]) -> str | None:
    """Em que momento o dinheiro do pedido está na Amazon (15/10, Vinicius): "preso na
    reserva" enquanto algum lançamento é DEFERRED; senão "liberado em dd/mm"."""
    lancamentos = [m for m in movs if m.tipo == "venda_liberada"]
    if not lancamentos:
        return None
    if any(m.sub_tipo == "DEFERRED" for m in lancamentos):
        return "preso na reserva"
    datas = []
    for m in lancamentos:
        raw = m.raw if isinstance(m.raw, dict) else {}
        quando = raw.get("_liberado_em")
        try:
            datas.append(datetime.fromisoformat(str(quando)) if quando else m.data)
        except ValueError:
            datas.append(m.data)
    ultima = max(datas)
    return f"liberado em {ultima.astimezone(BRT):%d/%m}"


def conta_amazon(venda: MarketplaceOrderFinancial | None) -> dict[str, Decimal] | None:
    """A partir do que a Margem guardou (`raw.transactions` = resposta da Finances API
    por pedido). Pedido ainda não lançado (pending) → None."""
    raw = venda.raw if (venda is not None and isinstance(venda.raw, dict)) else None
    body = raw.get("transactions") if raw else None
    payload = (
        body.get("payload")
        if isinstance(body, dict) and isinstance(body.get("payload"), dict)
        else body
    )
    txs = payload.get("transactions") if isinstance(payload, dict) else None
    return conta_amazon_de_transacoes(txs if isinstance(txs, list) else [])


def _previsao_do_unsettled(raw: Any) -> datetime | None:
    """TikTok: a varredura do unsettled da Margem guarda `raw.unsettled_estimate`
    (est_settlement_amount + estimated_settlement). Quando vier uma data/epoch,
    vira a previsão de liquidação; forma desconhecida → sem previsão."""
    est = raw.get("unsettled_estimate") if isinstance(raw, dict) else None
    if not isinstance(est, dict):
        return None
    v: Any = est.get("estimated_settlement") or est.get("estimated_settlement_time")
    if isinstance(v, dict):
        v = next((x for k, x in v.items() if "time" in str(k).lower()), None)
    if v in (None, ""):
        return None
    try:
        n = int(str(v))
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(str(v))
        except ValueError:
            return None
        return d if d.tzinfo else d.replace(tzinfo=BRT)
    if n > 10**12:
        n //= 1000
    return datetime.fromtimestamp(n, tz=UTC) if n > 10**9 else None


def _entrou_em(m: Any) -> datetime:
    """Quando a linha chegou ao painel (16/10, revisão): o relatório do dia é lido de
    madrugada, então um movimento de ontem às 10h só existe aqui hoje — comparando pela
    data da plataforma, o pedido finalizado ontem às 15h não reabria."""
    criado = getattr(m, "created_at", None)
    return max(m.data, criado) if criado else m.data


def situacao_do_pedido(
    *,
    agora: datetime,
    data_pedido: datetime | None,
    bling_total: Decimal | None,
    venda: MarketplaceOrderFinancial | None,
    previsao: datetime | None,
    movs_extrato: list[ConciliacaoMovimento],
    fatura: dict[str, Decimal],
    bling_cancelado: bool = False,
    liquido_previsto: Decimal | None = None,
    plataforma: str | None = None,
    entregue_em: datetime | None = None,
    bling_situacao: str | None = None,
    sem_margem: bool = False,
) -> dict[str, Any]:
    """A parte pura do recálculo: devolve os números do extrato, a situação e o motivo.
    `liquido_previsto` substitui o líquido da venda da Margem (Amazon por arquivo, 16/10:
    a venda vem das próprias transações do CSV; pack do ML com vários pedidos idem)."""
    previsao_estimada = False
    if previsao is None and plataforma in PRAZO_LIBERACAO:
        previsao = _previsao_por_prazo(plataforma, data_pedido, entregue_em)
        previsao_estimada = previsao is not None
    # Amazon: lançamento ainda DEFERRED é dinheiro PRESO na reserva — não liberou
    # (16/10, Vinicius: "bateu" só quando a Amazon liberar, igual ML/Shopee/TikTok)
    presas = [m for m in movs_extrato if _presa_na_reserva(m)]
    dinheiro = [m for m in movs_extrato if m.tipo not in TIPOS_RESERVA and not _presa_na_reserva(m)]
    liberacoes = [m for m in dinheiro if m.tipo == "venda_liberada"]
    liberado_valor = _soma([m.credito for m in liberacoes]) if liberacoes else None
    liberado_em = max((_quando_liberou(m) for m in liberacoes), default=None)
    recebido = (
        (_soma([m.credito for m in dinheiro]) - _soma([m.debito for m in dinheiro]))
        if dinheiro
        else None
    )
    if presas and not liberacoes:
        # Amazon preso na reserva: a etiqueta Easy Ship já debitou, mas a venda não
        # liberou — nada "entrou" ainda (16/10, revisão: 93 pedidos viravam "estornado")
        recebido = None
    # o que mexeu sem nenhuma liberação, fora o frete (a etiqueta faz parte da
    # liquidação, não é dinheiro que saiu)
    mexeu_sem_liberar = (
        _soma([m.credito for m in dinheiro if not m.tipo.startswith("frete")])
        - _soma([m.debito for m in dinheiro if not m.tipo.startswith("frete")])
        if dinheiro
        else ZERO
    )
    # frete cobrado depois do lançamento (etiqueta Easy Ship da Amazon, Mercado Envios à
    # parte) é custo da venda, não "saiu depois"
    saidas_depois = (
        _soma(
            [
                m.debito
                for m in dinheiro
                if liberado_em and m.data > liberado_em and m.tipo != "frete"
            ]
        )
        if liberado_em
        else None
    )
    ultimo_mov = max((_entrou_em(m) for m in movs_extrato), default=None)
    cancelada = _venda_cancelada(venda)
    venda_valor = _d(venda.gross_amount) if venda else None
    venda_liquido = _d(venda.net_amount) if venda else None
    if liquido_previsto is not None:
        venda_liquido = liquido_previsto
    dif_bling = (
        (bling_total - venda_valor)
        if (bling_total is not None and venda_valor is not None)
        else None
    )
    motivo: str | None = None
    dif: Decimal | None = None

    venda_vazia = (
        venda is not None
        and not cancelada
        and (venda_liquido is None or venda_liquido == ZERO)
        and not (_d(venda.fee_amount) or ZERO)
        and (venda_valor or ZERO) > ZERO
    )
    if bling_cancelado and not dinheiro and not liberacoes:
        # Shopee (15/10): venda cancelada não ganha escrow, então a plataforma nunca
        # "lê" a venda — o sinal é o Bling, e nada entrou. Zero a zero. 16/10 (revisão):
        # vale pra toda plataforma e pra "excluído" — kia/poofy (Amazon) ficavam
        # "aguardando" porque a venda da Margem não estava "vazia".
        situacao = "cancelado"
        motivo = f"{bling_situacao or 'cancelado'} no Bling, nada entrou"
    elif (venda is None or venda_vazia) and not dinheiro and not presas and not sem_margem:
        situacao = "sem_venda"
        motivo = (
            "a plataforma ainda não calculou a venda (pedido novo ou cancelado)"
            if venda_vazia
            else "venda ainda não lida pela Margem"
        )
    elif not liberacoes:
        if presas:
            situacao = "aguardando"
            preso = _soma([m.credito for m in presas]) - _soma([m.debito for m in presas])
            motivo = f"preso na reserva da Amazon: {preso:.2f}"
        elif dinheiro and abs(mexeu_sem_liberar) > TOLERANCIA:
            # 16/10 (revisão, aprovado): nada liberou e mesmo assim saiu (ou entrou)
            # dinheiro — devolvido antes de liquidar na TikTok, frete de devolução… Não é
            # "aguardando" (ia pro A liberar negativo) nem "zero a zero".
            situacao = "estornado_depois" if mexeu_sem_liberar < ZERO else "recebido_a_mais"
            verbo = "saiu" if mexeu_sem_liberar < ZERO else "entrou"
            motivo = f"nada liberou e {verbo} {abs(mexeu_sem_liberar):.2f}" + (
                " (venda desfeita)" if cancelada or bling_cancelado else ""
            )
        elif cancelada:
            situacao = "cancelado"
            motivo = "venda desfeita no ML"
        elif previsao and agora > previsao + FOLGA_PREVISAO:
            situacao = "vencido"
            estimada = " (estimada)" if previsao_estimada else ""
            motivo = f"previsão de liberação era {previsao.astimezone(BRT):%d/%m}{estimada}"
        elif data_pedido and agora - data_pedido > timedelta(days=DIAS_VENCIDO_SEM_PREVISAO):
            situacao = "vencido"
            motivo = f"vendido há mais de {DIAS_VENCIDO_SEM_PREVISAO} dias sem liberação"
        else:
            situacao = "aguardando"
            estimada = " (estimativa)" if previsao_estimada else ""
            motivo = f"libera em {previsao.astimezone(BRT):%d/%m}{estimada}" if previsao else None
            if sem_margem and venda is None and not dinheiro and not presas:
                motivo = "sem lançamento no arquivo da Amazon ainda"
    else:
        assert recebido is not None and liberado_valor is not None
        if cancelada and abs(recebido) <= TOLERANCIA:
            situacao = "cancelado"
            motivo = "venda desfeita: liberou e devolveu"
            # Tudo voltou: não existe "saiu depois" (a mediação pode cair segundos
            # depois da última liberação e parecer saída tardia — 15/10, 301418).
            saidas_depois = None
        elif bling_cancelado and recebido <= TOLERANCIA:
            # Amazon 302142 (15/10): pedido cancelado no Bling, a Amazon devolveu tudo ao
            # cliente e ficou com 10,00 de tarifa — é cancelado, não "estornado depois"
            situacao = "cancelado"
            ficou = -recebido
            motivo = "cancelado no Bling: devolveu tudo" + (
                f", a plataforma ficou com {ficou:.2f}" if ficou > TOLERANCIA else ""
            )
            saidas_depois = None
        elif (
            saidas_depois and saidas_depois > TOLERANCIA and recebido < liberado_valor - TOLERANCIA
        ):
            situacao = "estornado_depois"
            ultimo_debito = max(
                (m for m in dinheiro if m.debito and m.debito > ZERO), key=lambda m: m.data
            )
            quando = ultimo_debito.data.astimezone(BRT)
            motivo = f"{ultimo_debito.tipo} de {ultimo_debito.debito} em {quando:%d/%m}"
        else:
            if venda_liquido is None:
                # 16/10 (Vinicius: "como pode estar bateu se nem valor tem?"): entrou
                # dinheiro mas ninguém leu a venda — não dá pra dizer que bateu
                situacao = "sem_venda"
                motivo = "entrou dinheiro, mas a venda ainda não foi lida pela Margem"
            else:
                dif = recebido - venda_liquido
                if abs(dif) <= TOLERANCIA:
                    situacao = "bateu"
                elif dif < ZERO:
                    situacao = "recebido_a_menos"
                else:
                    situacao = "recebido_a_mais"
                if situacao != "bateu":
                    motivo = _motivo_da_diferenca(venda, fatura, liberacoes)
            # 15/10: o Bling "nunca bate" (Vinicius) — a diferença Bling × venda continua
            # gravada (dif_bling_venda), mas não vira situação nem aparece na tela.
    return {
        "liberado_valor": liberado_valor,
        "liberado_em": liberado_em,
        "recebido": recebido,
        "saidas_depois": saidas_depois,
        "ultimo_movimento_em": ultimo_mov,
        "dif_bling_venda": dif_bling,
        "dif_previsto_recebido": dif,
        "situacao": situacao,
        "motivo": motivo,
        # cancelado de verdade (nada ficou na conta): só esse finaliza sozinho
        "zero_a_zero": situacao == "cancelado"
        and (recebido is None or abs(recebido) <= TOLERANCIA),
        # nada liberou: o produto não saiu (ou voltou) — custo zero no Sobrou
        "nada_liberou": not liberacoes,
        # previsão usada (a da plataforma ou a estimada) — vai pra previsao_liberacao
        "previsao": previsao,
    }


def _presa_na_reserva(m: ConciliacaoMovimento) -> bool:
    return m.tipo == "venda_liberada" and (m.sub_tipo or "") == "DEFERRED"


def _quando_liberou(m: ConciliacaoMovimento) -> datetime:
    """Data da liberação de verdade: a Amazon mantém a linha na data do lançamento e
    guarda a liberação em raw._liberado_em; o resto libera na data da linha."""
    raw = m.raw if isinstance(m.raw, dict) else {}
    quando = raw.get("_liberado_em")
    if quando:
        try:
            return datetime.fromisoformat(str(quando))
        except ValueError:
            pass
    return m.data


def _previsao_por_prazo(
    plataforma: str, data_pedido: datetime | None, entregue_em: datetime | None
) -> datetime | None:
    apos_entrega, apos_pedido = PRAZO_LIBERACAO[plataforma]
    if entregue_em is not None:
        return entregue_em + timedelta(days=apos_entrega)
    if data_pedido is not None:
        return data_pedido + timedelta(days=apos_pedido)
    return None


def frete_do_comprador(
    venda: MarketplaceOrderFinancial | None, liberacoes: list[ConciliacaoMovimento]
) -> Decimal:
    """Quanto o comprador pagou de frete = bruto do extrato − valor da venda (ML põe o
    frete do comprador dentro do GROSS_AMOUNT do pagamento). Zero quando não dá pra saber."""
    if venda is None or venda.gross_amount is None or not liberacoes:
        return ZERO
    bruto = _soma([m.bruto for m in liberacoes if m.bruto is not None])
    dif = bruto - _d(venda.gross_amount)
    return dif if dif > TOLERANCIA else ZERO


def _motivo_da_diferenca(
    venda: MarketplaceOrderFinancial | None,
    fatura: dict[str, Decimal],
    liberacoes: list[ConciliacaoMovimento],
) -> str | None:
    partes: list[str] = []
    # A carteira da Shopee só traz o líquido (sem tarifa/frete na linha): não dá pra
    # dizer "cobrado 0,00". Fica só a diferença, e a abertura está no detalhe da venda.
    if liberacoes and all(m.tarifa is None and m.frete is None for m in liberacoes):
        return "a carteira só traz o líquido: abertura no detalhe da venda"
    frete_previsto = _d(venda.freight_amount) if venda else None
    frete_cobrado = _soma([m.frete for m in liberacoes]).copy_abs() if liberacoes else None
    # O extrato cobra o frete INTEIRO (SHIPPING_FEE) e o bruto traz o que o comprador
    # pagou de frete; a venda guarda só a parte da loja. Compara líquido com líquido.
    frete_comprador = frete_do_comprador(venda, liberacoes)
    if (
        frete_previsto is not None
        and frete_cobrado is not None
        and abs((frete_cobrado - frete_comprador) - frete_previsto) > TOLERANCIA
    ):
        detalhe = f" ({frete_comprador} pago pelo comprador)" if frete_comprador else ""
        partes.append(f"frete cobrado {frete_cobrado}{detalhe} × previsto {frete_previsto}")
    parcelamento = _soma([m.parcelamento for m in liberacoes]).copy_abs() if liberacoes else ZERO
    if parcelamento > TOLERANCIA:
        partes.append(f"taxa de parcelamento {parcelamento}")
    tarifa_prevista = _d(venda.fee_amount) if venda else None
    tarifa_cobrada = _soma([m.tarifa for m in liberacoes]).copy_abs() if liberacoes else None
    if (
        tarifa_prevista is not None
        and tarifa_cobrada is not None
        and abs(tarifa_cobrada - tarifa_prevista) > TOLERANCIA
    ):
        partes.append(f"tarifa cobrada {tarifa_cobrada} × prevista {tarifa_prevista}")
    return " · ".join(partes) or None


def etapa_do_pedido(
    *,
    agora: datetime,
    atual: ConciliacaoPedido | None,
    situacao: str,
    ultimo_movimento_em: datetime | None,
    zero_a_zero: bool = True,
    com_dinheiro: bool = False,
) -> dict[str, Any]:
    """Transições da etapa. Respeita o que a pessoa marcou (finalizado à mão, reaberto
    à mão). `zero_a_zero`: cancelado em que nada ficou na conta (16/10, revisão,
    aprovado: cancelado com dinheiro ≠ 0 é pendência, não "venda desfeita")."""
    etapa_anterior = atual.etapa if atual else None
    bateu_em = atual.bateu_em if atual else None
    out: dict[str, Any] = {"bateu_em": bateu_em}
    fechou = situacao == "bateu" or (situacao == "cancelado" and zero_a_zero)
    if fechou and not bateu_em:
        out["bateu_em"] = agora
    if not fechou and bateu_em:
        # 16/10 (revisão): deixou de bater (reserva, estorno) — a marca sai, senão a
        # finalização sozinha contava os 7 dias de um "bateu" que não existe mais
        out["bateu_em"] = None
    if etapa_anterior == "finalizado":
        # o robô finalizou como "venda desfeita" e o pedido deixou de ser cancelado
        # (mudou o status no Bling — 302142, 15/10): volta pra fila como reaberto
        if (
            atual.etapa_por == ETAPA_PELO_ROBO
            and "venda desfeita" in (atual.etapa_motivo or "")
            and situacao != "cancelado"
        ):
            out.update(
                etapa="reaberto",
                etapa_em=agora,
                etapa_por=ETAPA_PELO_ROBO,
                etapa_motivo=f"deixou de ser cancelado ({situacao})",
            )
            return out
        # 16/10 (revisão): movimento novo só reabre se o pedido DEIXOU de fechar — 47
        # cancelados "zero a zero" reabriam por linha atrasada que não mudava nada
        if (
            ultimo_movimento_em
            and atual.etapa_em
            and ultimo_movimento_em > atual.etapa_em
            and not fechou
        ):
            quando = ultimo_movimento_em.astimezone(BRT)
            out.update(
                etapa="reaberto",
                etapa_em=agora,
                etapa_por=ETAPA_PELO_ROBO,
                etapa_motivo=f"movimento novo em {quando:%d/%m %H:%M} ({situacao})",
            )
        else:
            out["etapa"] = "finalizado"
        return out
    if etapa_anterior == "reaberto" and atual is not None and atual.etapa_por != ETAPA_PELO_ROBO:
        # 16/10 (revisão, aprovado): o que a pessoa reabriu só a pessoa fecha — o robô
        # não refinaliza nem apaga o motivo dela
        out["etapa"] = "reaberto"
        return out
    if situacao == "aguardando" or (situacao == "sem_venda" and not com_dinheiro):
        nova = "aguardando"
    elif situacao == "sem_venda":
        nova = "pendencia"  # entrou dinheiro sem venda lida: alguém olha (2ª rodada)
    elif fechou:
        nova = "conciliado"
    else:
        nova = "pendencia"
    if etapa_anterior == "reaberto" and nova != "conciliado":
        nova = "reaberto"
    if nova == "conciliado" and situacao == "cancelado":
        # Venda desfeita é zero a zero: finaliza de cara (15/10, Vinicius). Se cair
        # algo depois, o "movimento novo reabre" cuida.
        out.update(
            etapa="finalizado",
            etapa_em=agora,
            etapa_por=ETAPA_PELO_ROBO,
            etapa_motivo="venda desfeita, zero a zero",
        )
        return out
    if nova == "conciliado":
        base = out["bateu_em"] or agora
        sem_novidade = ultimo_movimento_em is None or ultimo_movimento_em <= base + timedelta(
            hours=1
        )
        if agora - base >= timedelta(days=DIAS_FINALIZAR) and sem_novidade:
            out.update(
                etapa="finalizado",
                etapa_em=agora,
                etapa_por=ETAPA_PELO_ROBO,
                etapa_motivo=f"bateu, {DIAS_FINALIZAR} dias sem novidade",
            )
            return out
    if nova != etapa_anterior:
        out.update(etapa=nova, etapa_em=agora, etapa_por=ETAPA_PELO_ROBO, etapa_motivo=None)
    else:
        out["etapa"] = nova
    return out


async def _reservas_vai_e_volta(session: AsyncSession, loja: ConciliacaoLoja) -> tuple[int, int]:
    """Reserva sem pedido no Bling: o par −X/+X do mesmo pagamento (soma zero) vira
    "reserva"; reserva sozinha (dinheiro retido) fica sem dono — e a que o robô tinha
    classificado antes volta a sem dono, reabrindo o dia."""
    linhas = (
        (
            await session.execute(
                select(ConciliacaoMovimento)
                .where(
                    ConciliacaoMovimento.integration_id == loja.integration_id,
                    ConciliacaoMovimento.origem == ORIGEM_EXTRATO,
                    ConciliacaoMovimento.tipo.in_(tuple(TIPOS_RESERVA)),
                    ConciliacaoMovimento.pedido_bling.is_(None),
                    ConciliacaoMovimento.vinculado_por.is_(None),
                    or_(
                        ConciliacaoMovimento.classificacao.is_(None),
                        ConciliacaoMovimento.classificacao == "reserva",
                    ),
                )
                .limit(5000)
            )
        )
        .scalars()
        .all()
    )
    grupos: dict[str, list[ConciliacaoMovimento]] = defaultdict(list)
    for m in linhas:
        grupos[m.pagamento_id or f"sem-pagamento:{m.id}"].append(m)
    classificadas = retidas = 0
    dias: set[date] = set()
    for grupo in grupos.values():
        saldo = _soma([m.credito for m in grupo]) - _soma([m.debito for m in grupo])
        fechou = len(grupo) >= 2 and abs(saldo) <= TOLERANCIA
        for m in grupo:
            if fechou and m.classificacao is None:
                m.classificacao = "reserva"
                classificadas += 1
            elif not fechou and m.classificacao == "reserva":
                m.classificacao = None
                retidas += 1
                dias.add(m.data.astimezone(BRT).date())
    if dias:
        await reabrir_dias(session, loja, dias, motivo="reserva retida sem dono")
    return classificadas, retidas


_TIPOS_COBRANCA_PLATAFORMA = ("ajuste_shopee", "ajuste_amazon", "ajuste_tiktok")


async def _soltar_cobrancas_da_plataforma(session: AsyncSession, loja: ConciliacaoLoja) -> int:
    """Linha de cobrança/ajuste da plataforma sem pedido que o robô classificou sozinho
    ("ajuste"/"outro") e tem dinheiro: volta a sem dono e o dia reabre."""
    linhas = (
        (
            await session.execute(
                select(ConciliacaoMovimento).where(
                    ConciliacaoMovimento.integration_id == loja.integration_id,
                    ConciliacaoMovimento.tipo.in_(_TIPOS_COBRANCA_PLATAFORMA),
                    ConciliacaoMovimento.pedido_bling.is_(None),
                    ConciliacaoMovimento.vinculado_por.is_(None),
                    ConciliacaoMovimento.classificacao.in_(("ajuste", "outro")),
                    or_(ConciliacaoMovimento.credito != 0, ConciliacaoMovimento.debito != 0),
                )
            )
        )
        .scalars()
        .all()
    )
    for m in linhas:
        m.classificacao = None
    if linhas:
        await reabrir_dias(
            session,
            loja,
            {m.data.astimezone(BRT).date() for m in linhas},
            motivo="cobrança da plataforma sem dono",
        )
    return len(linhas)


async def recalcular_loja(
    session: AsyncSession, loja: ConciliacaoLoja, *, agora: datetime | None = None
) -> dict[str, Any]:
    agora = agora or datetime.now(UTC)
    integration = await session.get(Integration, loja.integration_id)
    lojas_bling = await bling_store_ids_da_loja(session, integration) if integration else set()
    inicio_dt = datetime.combine(loja.inicio, datetime.min.time(), tzinfo=BRT)
    plataforma_loja = getattr(loja.platform, "value", str(loja.platform))
    if not lojas_bling:
        return {"loja": loja.conta, "erro": "sem loja do Bling ligada a esta conta", "pedidos": 0}
    # 0. Linha do relatório pobre que tem gêmea completa (16/10, aguiar): some antes de
    # qualquer conta, senão o pedido recebe em dobro.
    duplicatas_pobres = await apagar_duplicatas_pobres(session, loja)

    # 1. Pedidos do Bling da loja desde o início (uma linha por item → agrupa por número).
    rows = (
        (
            await session.execute(
                select(BlingOrder).where(
                    BlingOrder.loja.in_(lojas_bling), BlingOrder.data >= inicio_dt
                )
            )
        )
        .scalars()
        .all()
    )
    por_numero: dict[str, list[BlingOrder]] = defaultdict(list)
    for r in rows:
        if r.numero:
            por_numero[str(r.numero)].append(r)
    numeros = set(por_numero)
    numerolojas = {str(r.numeroloja) for r in rows if r.numeroloja}

    # 2. Venda (Margem), movimentos e reembolsos.
    fins = (
        (
            await session.execute(
                select(MarketplaceOrderFinancial).where(
                    MarketplaceOrderFinancial.integration_id == loja.integration_id,
                    MarketplaceOrderFinancial.external_order_id.in_(numerolojas or {"-"}),
                )
            )
        )
        .scalars()
        .all()
    )
    fin_por_numeroloja = {f.external_order_id: f for f in fins}
    movs = (
        (
            await session.execute(
                select(ConciliacaoMovimento).where(
                    ConciliacaoMovimento.integration_id == loja.integration_id,
                    or_(
                        ConciliacaoMovimento.pedido_bling.in_(numeros or {"-"}),
                        ConciliacaoMovimento.pedido_marketplace.in_(numerolojas or {"-"}),
                        ConciliacaoMovimento.pack_id.in_(numerolojas or {"-"}),
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    numero_por_numeroloja = {
        str(r.numeroloja): str(r.numero) for r in rows if r.numeroloja and r.numero
    }
    # Entrega confirmada pela Logística (17track/EasyShip): base da previsão de liberação
    # da Shopee e da TikTok (16/10, revisão)
    entregue_por_numero: dict[str, datetime] = {}
    if plataforma_loja in PRAZO_LIBERACAO and numeros:
        entregue_por_numero = {
            str(n): e
            for n, e in (
                await session.execute(
                    select(Logistica.pedido_bling, func.max(Logistica.entregue_em))
                    .where(
                        Logistica.pedido_bling.in_(numeros),
                        Logistica.entregue_em.is_not(None),
                    )
                    .group_by(Logistica.pedido_bling)
                )
            ).all()
            if n and e
        }
    movs_por_numero: dict[str, list[ConciliacaoMovimento]] = defaultdict(list)
    for m in movs:
        if not m.pedido_bling and (m.vinculado_por or m.classificacao):
            # Uma pessoa desvinculou ou classificou sem pedido: não é deste pedido,
            # mesmo que o nº da venda aponte pra ele (15/10).
            continue
        numero = (
            m.pedido_bling
            or numero_por_numeroloja.get(m.pedido_marketplace or "")
            or numero_por_numeroloja.get(m.pack_id or "")
        )
        if numero:
            movs_por_numero[numero].append(m)
            if not m.pedido_bling:
                m.pedido_bling = numero
    reembolsos = (
        await session.execute(
            select(Refund.pedido_bling, Refund.reembolso).where(
                Refund.pedido_bling.in_(numeros or {"-"})
            )
        )
    ).all()
    # O painel Reembolso grava com o sinal da Margem (ao cliente = negativo; "Saldo
    # Final = Saldo Efetivo + reembolso"). Aqui "reembolso" é o que SAIU: inverte o
    # sinal pra que Sobrou = recebido − custo − reembolso desconte de verdade
    # (16/10, revisão: Inova 300489 mostrava −7,38 em vez de −43,60).
    reembolso_por_numero: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for numero, valor in reembolsos:
        if numero and valor:
            reembolso_por_numero[str(numero)] -= _d(valor) or ZERO
    nomes_situacao = {
        str(i): n
        for i, n in (await session.execute(select(SituacaoBling.id, SituacaoBling.nome))).all()
        if n
    }
    existentes = {
        p.pedido_bling: p
        for p in (
            await session.execute(
                select(ConciliacaoPedido).where(
                    ConciliacaoPedido.integration_id == loja.integration_id
                )
            )
        ).scalars()
    }
    # Amazon (15/10): frete pago fora (Melhor Envio) e o canal de envio de cada pedido
    fretes_pagos: dict[str, Decimal] = {}
    canal_por_numero: dict[str, str] = {}
    if plataforma_loja == "amazon":
        from app.services.conciliacao_melhor_envio import fretes_por_pedido

        fretes_pagos = await fretes_por_pedido(session, loja)
        if numeros:
            for num, canal in (
                await session.execute(
                    select(Logistica.pedido_bling, Logistica.amazon_canal).where(
                        Logistica.pedido_bling.in_(numeros)
                    )
                )
            ).all():
                if canal:
                    canal_por_numero[str(num)] = str(canal)

    # 3. Uma linha por pedido.
    contagem: dict[str, int] = defaultdict(int)
    if duplicatas_pobres:
        contagem["duplicata_pobre_apagada"] = duplicatas_pobres
    for numero, itens in por_numero.items():
        cab = itens[0]
        numeroloja = str(cab.numeroloja) if cab.numeroloja else None
        venda = fin_por_numeroloja.get(numeroloja) if numeroloja else None
        meus = movs_por_numero.get(numero, [])
        extrato = [m for m in meus if m.origem in ORIGENS_EXTRATO]
        fatura_movs = [m for m in meus if m.origem == ORIGEM_FATURA]
        fatura = {
            "tarifa": _soma(
                [
                    m.debito
                    for m in fatura_movs
                    if m.tipo in ("fatura_tarifa_venda", "fatura_tarifa_cobranca")
                ]
            ),
            "parcelamento": _soma(
                [m.debito for m in fatura_movs if m.tipo == "fatura_parcelamento"]
            ),
            "frete": _soma([m.debito for m in fatura_movs if m.tipo == "fatura_frete"]),
            "bonificacao": _soma(
                [m.credito for m in fatura_movs if m.tipo == "fatura_bonificacao"]
            ),
            "outros": _soma(
                [m.debito for m in fatura_movs if m.tipo in ("fatura_outro", "fatura_publicidade")]
            ),
        }
        bling_total = _d(cab.total)
        # O total do Bling traz o frete que o cliente pagou; a venda do ML (total_amount)
        # é só produto. Compara produto com produto (14/10: "Bling +9,99" em tudo que
        # tinha frete de R$ 9,99 pago pelo comprador).
        bling_produtos = _d(cab.totalprodutos) if cab.totalprodutos is not None else bling_total
        custo = _soma(
            [(_d(i.preco_custo) or ZERO) * Decimal(int(i.item_quantidade or 1)) for i in itens]
        )
        previsao = (
            (_previsao_do_billing(venda.raw) or _previsao_do_unsettled(venda.raw))
            if venda
            else None
        )
        nome_bling = str(nomes_situacao.get(str(cab.situacao or ""), cab.situacao) or "")
        # Amazon sem venda da Margem (KFA por arquivo, 16/10): a venda é a soma das
        # próprias transações do pedido (lançamento + tarifas), como a tela da Amazon
        # 16/10 à noite: só quando a Margem não tem a venda (KFA antes do arquivo entrar);
        # com venda da Margem, o esperado é dela — não as próprias linhas da conta
        amazon_linhas = (
            conta_amazon_de_transacoes([m.raw for m in extrato if isinstance(m.raw, dict)])
            if plataforma_loja == "amazon" and venda is None
            else None
        )
        # 16/10 à noite (Vinicius): o valor esperado do pedido é o da PLATAFORMA lido pela
        # MARGEM — pack do ML com vários pedidos compara com a venda da Margem; se ela só
        # leu um pedido do carrinho, aparece "recebido a mais" (sinal pra consertar a
        # leitura, não pra esconder)
        liquido_previsto = amazon_linhas["liquido"] if amazon_linhas else None
        estado_bling = nome_bling.strip().lower()
        calc = situacao_do_pedido(
            agora=agora,
            data_pedido=cab.data,
            bling_total=bling_produtos,
            venda=venda,
            previsao=previsao,
            bling_cancelado=estado_bling in BLING_DESFEITO,
            bling_situacao=estado_bling or None,
            movs_extrato=extrato,
            fatura=fatura,
            liquido_previsto=liquido_previsto,
            plataforma=plataforma_loja,
            entregue_em=entregue_por_numero.get(numero),
            # loja por arquivo (KFA): a Margem não lê a venda — sem linha no CSV é
            # "aguardando" (e vence pelo prazo), não "venda não lida" pra sempre
            sem_margem=loja.modo_extrato == "upload",
        )
        if plataforma_loja == "amazon" and calc["situacao"] == "bateu":
            calc["motivo"] = motivo_amazon(extrato)
            # envio próprio sem o frete do Melhor Envio: "falta frete" (etapa = pendência)
            if fretes_pagos.get(numero) is None:
                proprio_linhas = bool(
                    amazon_linhas
                    and amazon_linhas["frete_cliente"] > ZERO
                    and not amazon_linhas["etiqueta"]
                )
                canal_am = canal_por_numero.get(numero) or (
                    "proprio" if (_am_proprio(venda) or proprio_linhas) else None
                )
                if canal_am == "proprio":
                    calc["situacao"] = "falta_frete"
                    calc["motivo"] = "sem o frete do Melhor Envio"
        atual = existentes.get(numero)
        etapa = etapa_do_pedido(
            agora=agora,
            atual=atual,
            situacao=calc["situacao"],
            ultimo_movimento_em=calc["ultimo_movimento_em"],
            zero_a_zero=calc["zero_a_zero"],
            com_dinheiro=calc["recebido"] is not None,
        )
        reembolso = reembolso_por_numero.get(numero, ZERO)
        # Venda sem valor ainda (Shopee antes de calcular o escrow): não grava tarifa/líquido 0,00
        venda_ok = venda if calc["situacao"] != "sem_venda" else None
        # Shopee: a conta como a tela deles (preço de venda, subsídio, taxas líquidas)
        shopee = conta_shopee(venda_ok) if plataforma_loja == "shopee" else None
        # TikTok: a conta do extrato por pedido (valor = após desconto da loja,
        # tarifa = todas as taxas, frete = parte da loja)
        tiktok = conta_tiktok(venda_ok) if plataforma_loja == "tiktok" else None
        # Amazon: tarifa = comissão + Amazon for All + retenção sobre o frete (porcentagens);
        # frete = etiqueta Easy Ship (DBA) menos o frete cobrado do cliente (próprio);
        # extras = promoção da loja + outras tarifas
        amazon = conta_amazon(venda_ok) if plataforma_loja == "amazon" else None
        if amazon is None and amazon_linhas and calc["situacao"] != "sem_venda":
            amazon = amazon_linhas
        if amazon is not None:
            # 15/10, Vinicius: Valor = o que o cliente pagou à loja (produto + frete
            # cobrado no envio próprio); Frete = só o que a Amazon cobra (etiqueta DBA)
            shopee = {
                "preco": amazon["produto"] + amazon["frete_cliente"],
                "taxas": -(
                    amazon["comissao"] + amazon["amazon_for_all"] + amazon["retencao_frete"]
                ),
                "afiliados": ZERO,
                "frete_loja": -amazon["etiqueta"],
                "subsidio": ZERO,
                "cupons": -(amazon["promocao"] + amazon["outras_tarifas"] + amazon["outros"]),
            }
        if tiktok is not None:
            # valor = produtos após o desconto da loja; tarifa = todas as taxas; frete =
            # parte da loja; extras = o que a TikTok acrescentou (subsídio) entra negativo
            shopee = {
                "preco": tiktok["apos_desconto"] or tiktok["vendas_brutas"],
                # tarifa = comissão + taxa de transação; afiliados vai pra Extras (15/10)
                "taxas": -(tiktok["taxas"] - tiktok["afiliados"]),
                "afiliados": -tiktok["afiliados"],
                "frete_loja": -tiktok["frete"],
                "subsidio": ZERO,
                "cupons": -tiktok["outros_receita"],
            }
        # Resultado de VERDADE: só com o dinheiro que entrou. Enquanto não libera, sobrou
        # e margem ficam vazios (15/10, Vinicius: "não precisa aparecer previsto nem margem").
        base_resultado = calc["recebido"]
        # Amazon: frete pago fora da plataforma (Melhor Envio) — entra no Sobrou, não
        # no Líquido; envio próprio sem ele não pode ser "bateu" (15/10, Vinicius)
        frete_pago = fretes_pagos.get(numero) if plataforma_loja == "amazon" else None
        if calc["situacao"] == "cancelado":
            # Venda desfeita: o produto não saiu (ou voltou) — custo zero (15/10, Vinicius).
            # 16/10 à noite (essência do painel): "nada liberou e saiu X" NÃO zera o custo —
            # é diferença pra alguém olhar, não suposição de que o produto voltou.
            custo = ZERO
            sobrou = (base_resultado or ZERO) - reembolso - (frete_pago or ZERO)
            margem = None
        else:
            sobrou = (
                (base_resultado - custo - reembolso - (frete_pago or ZERO))
                if base_resultado is not None
                else None
            )
            margem = (
                (sobrou / custo).quantize(Decimal("0.0001"))
                if (sobrou is not None and custo > ZERO)
                else None
            )
            if margem is not None and abs(margem) >= MARGEM_MAXIMA:
                # custo de 0,01 dava margem de 6 dígitos e estourava Numeric(10,4),
                # derrubando o recálculo da loja inteira (16/10, revisão)
                margem = None
        valores = {
            "platform": loja.platform,
            "bling_id": cab.bling_id,
            "numeroloja": numeroloja,
            "pack_id": next((m.pack_id for m in extrato if m.pack_id), None),
            "conta": loja.conta,
            "data_pedido": cab.data,
            "situacao_bling": nomes_situacao.get(str(cab.situacao or ""), cab.situacao),
            "bling_total": bling_total,
            "bling_produtos": _d(cab.totalprodutos),
            "bling_valorbase": _d(cab.valorbase),
            "bling_custofrete": _d(cab.custofrete),
            "bling_taxacomissao": _d(cab.taxacomissao),
            "venda_valor": (
                shopee["preco"] if shopee else (_d(venda.gross_amount) if venda else None)
            ),
            "venda_tarifa": (
                shopee["taxas"] if shopee else (_d(venda.fee_amount) if venda_ok else None)
            ),
            "venda_frete": (
                shopee["frete_loja"] if shopee else (_d(venda.freight_amount) if venda_ok else None)
            ),
            # Shopee: "extras" = subsídio no produto + cupons da loja (a tela deles)
            "venda_cupom": (
                shopee["subsidio"] + shopee["cupons"]
                if shopee
                else (_d(venda.discount_amount) if venda_ok else None)
            ),
            "venda_afiliados": shopee.get("afiliados") if shopee else None,
            "venda_frete_pago": frete_pago,
            "venda_estorno": _d(venda.refund_amount) if venda_ok else None,
            # Amazon: o líquido previsto é o da venda (Shipment + tarifas), sem estornos
            "venda_liquido": (
                amazon["liquido"] if amazon else (_d(venda.net_amount) if venda_ok else None)
            ),
            "venda_status": venda.status if venda_ok else None,
            "previsao_liberacao": calc["previsao"],
            "fatura_tarifa": fatura["tarifa"] if fatura_movs else None,
            "fatura_parcelamento": fatura["parcelamento"] if fatura_movs else None,
            "fatura_frete": fatura["frete"] if fatura_movs else None,
            "fatura_bonificacao": fatura["bonificacao"] if fatura_movs else None,
            "fatura_outros": fatura["outros"] if fatura_movs else None,
            "liberado_em": calc["liberado_em"],
            "liberado_valor": calc["liberado_valor"],
            "saidas_depois": calc["saidas_depois"],
            "recebido": calc["recebido"],
            "ultimo_movimento_em": calc["ultimo_movimento_em"],
            "custo": custo,
            "reembolso": reembolso,
            "sobrou": sobrou,
            "margem": margem,
            "dif_bling_venda": calc["dif_bling_venda"],
            "dif_previsto_recebido": calc["dif_previsto_recebido"],
            "situacao": calc["situacao"],
            "motivo": calc["motivo"],
            "recalculado_em": agora,
            **etapa,
        }
        if atual is None:
            atual = ConciliacaoPedido(
                integration_id=loja.integration_id, pedido_bling=numero, **valores
            )
            session.add(atual)
        else:
            for k, v in valores.items():
                setattr(atual, k, v)
        contagem[calc["situacao"]] += 1
        contagem[f"etapa:{atual.etapa}"] += 1

    # 3b. Pedido que saiu do escopo da loja (loja do Bling desligada desta conta,
    # pedido apagado no Bling) — 15/10: os pedidos da Shopee "Mini" que tinham entrado
    # na TikTok Mini pelo nome igual. 16/10 (revisão, aprovado): não apaga mais (a
    # observação e a finalização sumiam e as linhas ficavam com dono fantasma): marca
    # "fora da loja", solta as linhas que o robô tinha casado e alguém decide.
    fora = [
        n for n in existentes if n not in por_numero and existentes[n].situacao != "fora_da_loja"
    ]
    if por_numero and fora:
        for n in fora:
            p = existentes[n]
            p.situacao = "fora_da_loja"
            p.motivo = "pedido saiu desta loja no Bling (apagado ou mudou de loja)"
            p.recalculado_em = agora
            if p.etapa != "finalizado":
                p.etapa = "pendencia"
                p.etapa_em = agora
                p.etapa_por = ETAPA_PELO_ROBO
                p.etapa_motivo = "saiu da loja"
        soltas = (
            await session.execute(
                select(ConciliacaoMovimento.data).where(
                    ConciliacaoMovimento.integration_id == loja.integration_id,
                    ConciliacaoMovimento.pedido_bling.in_(fora),
                    ConciliacaoMovimento.vinculado_por.is_(None),
                )
            )
        ).scalars()
        dias_soltas = {d.astimezone(BRT).date() for d in soltas if d}
        await session.execute(
            update(ConciliacaoMovimento)
            .where(
                ConciliacaoMovimento.integration_id == loja.integration_id,
                ConciliacaoMovimento.pedido_bling.in_(fora),
                ConciliacaoMovimento.vinculado_por.is_(None),
            )
            .values(pedido_bling=None, bling_id=None)
        )
        # linha que perdeu o dono num dia fechado: o dia reabre (2ª rodada)
        await reabrir_dias(session, loja, dias_soltas, motivo="pedido saiu da loja: linha sem dono")
        contagem["fora_da_loja"] = len(fora)

    # 4. Movimento de pedido anterior ao início: classifica, não cobra.
    antigos = (
        (
            await session.execute(
                select(ConciliacaoMovimento).where(
                    ConciliacaoMovimento.integration_id == loja.integration_id,
                    ConciliacaoMovimento.pedido_bling.isnot(None),
                    ConciliacaoMovimento.classificacao.is_(None),
                    ConciliacaoMovimento.pedido_bling.notin_(numeros or {"-"}),
                )
            )
        )
        .scalars()
        .all()
    )
    if antigos:
        datas = (
            await session.execute(
                select(BlingOrder.numero, BlingOrder.data).where(
                    BlingOrder.numero.in_({m.pedido_bling for m in antigos}),
                    BlingOrder.loja.in_(lojas_bling),
                )
            )
        ).all()
        data_por_numero = {str(n): d for n, d in datas if n}
        for m in antigos:
            d = data_por_numero.get(m.pedido_bling or "")
            if d is not None and d < inicio_dt:
                m.classificacao = "anterior_ao_inicio"
                contagem["anterior_ao_inicio"] += 1
    # Pedido do ML ainda sem pedido do Bling (extrato OU fatura, de qualquer data):
    # tenta resolver de novo — o Bling pode ter importado depois.
    soltos = (
        (
            await session.execute(
                select(ConciliacaoMovimento)
                .where(
                    ConciliacaoMovimento.integration_id == loja.integration_id,
                    ConciliacaoMovimento.pedido_bling.is_(None),
                    ConciliacaoMovimento.pedido_marketplace.isnot(None),
                    # linha que uma PESSOA desvinculou fica como ela deixou (15/10:
                    # o "Desvincular" era desfeito pelo recálculo no segundo seguinte)
                    ConciliacaoMovimento.vinculado_por.is_(None),
                )
                .limit(5000)
            )
        )
        .scalars()
        .all()
    )
    if soltos:
        ids = {m.pedido_marketplace for m in soltos if m.pedido_marketplace} | {
            m.pack_id for m in soltos if m.pack_id
        }
        mapa = await resolver_pedidos_bling(session, ids, lojas_bling)
        for m in soltos:
            achado = mapa.get(m.pedido_marketplace or "") or mapa.get(m.pack_id or "")
            if achado:
                m.pedido_bling, m.bling_id = achado
                contagem["resolvido_agora"] += 1
                if m.classificacao in ("reserva", "venda_desfeita"):
                    # classificação dada pelo robô (abaixo) enquanto não tinha dono
                    m.classificacao = None
    # Cobrança da fatura do ML no saldo do MP e dinheiro segurado pra pagar a dívida
    # (16/10, mega 377,27 e aguiar 12/10): não é venda, é "Fatura do ML".
    cobradas = await classificar_cobrancas_da_fatura(session, loja)
    if cobradas:
        contagem["cobranca_fatura"] = cobradas
    # Linha sem nº de venda mas do MESMO pagamento de uma linha já ligada a um pedido
    # (16/10: relatório antigo da aguiar sem ORDER_ID; reservas/estornos sem o nº):
    # herda o pedido da irmã.
    orfas = (
        (
            await session.execute(
                select(ConciliacaoMovimento)
                .where(
                    ConciliacaoMovimento.integration_id == loja.integration_id,
                    ConciliacaoMovimento.origem == ORIGEM_EXTRATO,
                    ConciliacaoMovimento.pedido_bling.is_(None),
                    ConciliacaoMovimento.pedido_marketplace.is_(None),
                    ConciliacaoMovimento.pagamento_id.isnot(None),
                    ConciliacaoMovimento.vinculado_por.is_(None),
                    ConciliacaoMovimento.classificacao.is_(None),
                )
                .limit(5000)
            )
        )
        .scalars()
        .all()
    )
    if orfas:
        irmas = (
            await session.execute(
                select(
                    ConciliacaoMovimento.pagamento_id,
                    ConciliacaoMovimento.pedido_bling,
                    ConciliacaoMovimento.bling_id,
                )
                .where(
                    ConciliacaoMovimento.integration_id == loja.integration_id,
                    ConciliacaoMovimento.origem == ORIGEM_EXTRATO,
                    ConciliacaoMovimento.pagamento_id.in_({m.pagamento_id for m in orfas}),
                    ConciliacaoMovimento.pedido_bling.isnot(None),
                )
                .distinct()
            )
        ).all()
        por_pagamento: dict[str, tuple[str, int | None]] = {}
        for pid, pb, bid in irmas:
            if pid and pb and pid not in por_pagamento:
                por_pagamento[str(pid)] = (str(pb), bid)
        for m in orfas:
            achado = por_pagamento.get(m.pagamento_id or "")
            if achado:
                m.pedido_bling, m.bling_id = achado
                contagem["irma_do_pagamento"] += 1
    # Reserva (vai e volta) de venda que não existe no Bling: só vira "reserva" quando o
    # PAR existe e soma zero (−X e +X do mesmo pagamento) — 16/10 à noite (Vinicius):
    # reserva que só saiu é dinheiro retido, fica sem dono até alguém ver.
    reservas, retidas = await _reservas_vai_e_volta(session, loja)
    contagem["reserva_sem_pedido"] += reservas
    if retidas:
        contagem["reserva_retida"] = retidas
    # Cobrança da plataforma sem pedido (penalidade da Shopee, ajuste da Amazon/TikTok):
    # fica SEM DONO até uma pessoa classificar — 16/10 à noite (Vinicius): "é dinheiro
    # que saiu, alguém tem que ver"; o robô não classifica mais sozinho.
    soltas = await _soltar_cobrancas_da_plataforma(session, loja)
    if soltas:
        contagem["cobrancas_soltas"] = soltas
    # Venda sem pedido no Bling cujo dinheiro entrou e saiu inteiro: venda desfeita.
    desfeitas = await classificar_vendas_desfeitas(session, loja)
    if desfeitas:
        contagem["venda_desfeita"] = desfeitas
    # Cupom do extrato: só o que sai do bolso da loja (EFFECTIVE_COUPON_AMOUNT); as linhas
    # gravadas antes de 15/10 tinham o COUPON_AMOUNT (que soma o cupom bancado pelo ML).
    from app.services.conciliacao_extrato import cupom_da_linha

    extrato_gravado = (
        (
            await session.execute(
                select(ConciliacaoMovimento).where(
                    ConciliacaoMovimento.integration_id == loja.integration_id,
                    ConciliacaoMovimento.origem == ORIGEM_EXTRATO,
                    ConciliacaoMovimento.tipo == "venda_liberada",
                    ConciliacaoMovimento.cupom.isnot(None),
                    ConciliacaoMovimento.cupom != ZERO,
                )
            )
        )
        .scalars()
        .all()
    )
    for m in extrato_gravado:
        raw = m.raw if isinstance(m.raw, dict) else {}
        if "EFFECTIVE_COUPON_AMOUNT" not in raw:
            continue
        certo = cupom_da_linha(raw)
        if certo != m.cupom:
            m.cupom = certo
            contagem["cupom_corrigido"] += 1
    # Nome do tipo da cobrança: se o mapa de sub-tipos mudou, renomeia o que já está gravado.
    fatura_gravada = (
        (
            await session.execute(
                select(ConciliacaoMovimento)
                .where(
                    ConciliacaoMovimento.integration_id == loja.integration_id,
                    ConciliacaoMovimento.origem == ORIGEM_FATURA,
                )
                .limit(20000)
            )
        )
        .scalars()
        .all()
    )
    for m in fatura_gravada:
        esperado = tipo_do_detalhe(
            "BONUS" if m.credito and m.credito > ZERO else "CHARGE", m.sub_tipo
        )
        if esperado != m.tipo:
            m.tipo = esperado
            if (
                esperado in ("fatura_parcelamento", "fatura_parcelamento_comprador")
                and not m.parcelamento
            ):
                m.parcelamento = m.debito or m.credito
            contagem["tipo_renomeado"] += 1

    loja.ultimo_recalculo_em = agora
    await session.flush()
    return {"loja": loja.conta, "pedidos": len(por_numero), "movimentos": len(movs), **contagem}
