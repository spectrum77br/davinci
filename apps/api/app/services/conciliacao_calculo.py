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
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingOrder,
    ConciliacaoLoja,
    ConciliacaoMovimento,
    ConciliacaoPedido,
    Integration,
    MarketplaceOrderFinancial,
    Refund,
    SituacaoBling,
)
from app.models.conciliacao import ETAPA_PELO_ROBO, ORIGEM_EXTRATO, ORIGEM_FATURA, ORIGENS_EXTRATO
from app.services.conciliacao_extrato import (
    TIPOS_RESERVA,
    bling_store_ids_da_loja,
    resolver_pedidos_bling,
)
from app.services.conciliacao_fatura import tipo_do_detalhe

logger = structlog.get_logger()
BRT = ZoneInfo("America/Sao_Paulo")

TOLERANCIA = Decimal("0.05")
DIAS_FINALIZAR = 7
DIAS_VENCIDO_SEM_PREVISAO = 45
FOLGA_PREVISAO = timedelta(days=2)
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
) -> dict[str, Any]:
    """A parte pura do recálculo: devolve os números do extrato, a situação e o motivo."""
    dinheiro = [m for m in movs_extrato if m.tipo not in TIPOS_RESERVA]
    liberacoes = [m for m in dinheiro if m.tipo == "venda_liberada"]
    liberado_valor = _soma([m.credito for m in liberacoes]) if liberacoes else None
    liberado_em = max((m.data for m in liberacoes), default=None)
    recebido = (
        (_soma([m.credito for m in dinheiro]) - _soma([m.debito for m in dinheiro]))
        if dinheiro
        else None
    )
    saidas_depois = (
        _soma([m.debito for m in dinheiro if liberado_em and m.data > liberado_em])
        if liberado_em
        else None
    )
    ultimo_mov = max((m.data for m in movs_extrato), default=None)
    cancelada = _venda_cancelada(venda)
    venda_valor = _d(venda.gross_amount) if venda else None
    venda_liquido = _d(venda.net_amount) if venda else None
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
    if bling_cancelado and not dinheiro and not liberacoes and (venda is None or venda_vazia):
        # Shopee (15/10): venda cancelada não ganha escrow, então a plataforma nunca
        # "lê" a venda — o sinal é o Bling, e nada entrou. Zero a zero.
        situacao = "cancelado"
        motivo = "cancelado no Bling, nada entrou"
    elif (venda is None or venda_vazia) and not dinheiro:
        situacao = "sem_venda"
        motivo = (
            "a plataforma ainda não calculou a venda (pedido novo ou cancelado)"
            if venda_vazia
            else "venda ainda não lida pela Margem"
        )
    elif not liberacoes:
        if cancelada:
            situacao = "cancelado"
            motivo = "venda desfeita no ML"
        elif previsao and agora > previsao + FOLGA_PREVISAO:
            situacao = "vencido"
            motivo = f"previsão de liberação era {previsao.astimezone(BRT):%d/%m}"
        elif data_pedido and agora - data_pedido > timedelta(days=DIAS_VENCIDO_SEM_PREVISAO):
            situacao = "vencido"
            motivo = f"vendido há mais de {DIAS_VENCIDO_SEM_PREVISAO} dias sem liberação"
        else:
            situacao = "aguardando"
            motivo = f"libera em {previsao.astimezone(BRT):%d/%m}" if previsao else None
    else:
        assert recebido is not None and liberado_valor is not None
        if cancelada and abs(recebido) <= TOLERANCIA:
            situacao = "cancelado"
            motivo = "venda desfeita: liberou e devolveu"
            # Tudo voltou: não existe "saiu depois" (a mediação pode cair segundos
            # depois da última liberação e parecer saída tardia — 15/10, 301418).
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
                situacao = "bateu"
                motivo = "sem líquido previsto pra comparar"
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
    }


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
) -> dict[str, Any]:
    """Transições da etapa. Respeita o que a pessoa marcou (finalizado à mão)."""
    etapa_anterior = atual.etapa if atual else None
    bateu_em = atual.bateu_em if atual else None
    out: dict[str, Any] = {"bateu_em": bateu_em}
    fechou = situacao in ("bateu", "cancelado")
    if fechou and not bateu_em:
        out["bateu_em"] = agora
    if etapa_anterior == "finalizado":
        if ultimo_movimento_em and atual.etapa_em and ultimo_movimento_em > atual.etapa_em:
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
    if situacao in ("aguardando", "sem_venda"):
        nova = "aguardando"
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


async def recalcular_loja(
    session: AsyncSession, loja: ConciliacaoLoja, *, agora: datetime | None = None
) -> dict[str, Any]:
    agora = agora or datetime.now(UTC)
    integration = await session.get(Integration, loja.integration_id)
    lojas_bling = await bling_store_ids_da_loja(session, integration) if integration else set()
    inicio_dt = datetime.combine(loja.inicio, datetime.min.time(), tzinfo=BRT)
    if not lojas_bling:
        return {"loja": loja.conta, "erro": "sem loja do Bling ligada a esta conta", "pedidos": 0}

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
    reembolso_por_numero: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for numero, valor in reembolsos:
        if numero and valor:
            reembolso_por_numero[str(numero)] += _d(valor) or ZERO
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

    # 3. Uma linha por pedido.
    contagem: dict[str, int] = defaultdict(int)
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
        previsao = _previsao_do_billing(venda.raw) if venda else None
        nome_bling = str(nomes_situacao.get(str(cab.situacao or ""), cab.situacao) or "")
        calc = situacao_do_pedido(
            agora=agora,
            data_pedido=cab.data,
            bling_total=bling_produtos,
            venda=venda,
            previsao=previsao,
            bling_cancelado=nome_bling.strip().lower() == "cancelado",
            movs_extrato=extrato,
            fatura=fatura,
        )
        atual = existentes.get(numero)
        etapa = etapa_do_pedido(
            agora=agora,
            atual=atual,
            situacao=calc["situacao"],
            ultimo_movimento_em=calc["ultimo_movimento_em"],
        )
        reembolso = reembolso_por_numero.get(numero, ZERO)
        # Venda sem valor ainda (Shopee antes de calcular o escrow): não grava tarifa/líquido 0,00
        venda_ok = venda if calc["situacao"] != "sem_venda" else None
        # Resultado de VERDADE: só com o dinheiro que entrou. Enquanto não libera, sobrou
        # e margem ficam vazios (15/10, Vinicius: "não precisa aparecer previsto nem margem").
        base_resultado = calc["recebido"]
        if calc["situacao"] == "cancelado":
            # Venda desfeita: o produto não saiu (ou voltou) — custo zero (15/10, Vinicius).
            custo = ZERO
            sobrou = (base_resultado or ZERO) - reembolso
            margem = None
        else:
            sobrou = (base_resultado - custo - reembolso) if base_resultado is not None else None
            margem = (
                (sobrou / custo).quantize(Decimal("0.0001"))
                if (sobrou is not None and custo > ZERO)
                else None
            )
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
            "venda_valor": _d(venda.gross_amount) if venda else None,
            "venda_tarifa": _d(venda.fee_amount) if venda_ok else None,
            "venda_frete": _d(venda.freight_amount) if venda_ok else None,
            "venda_cupom": _d(venda.discount_amount) if venda_ok else None,
            "venda_estorno": _d(venda.refund_amount) if venda_ok else None,
            "venda_liquido": _d(venda.net_amount) if venda_ok else None,
            "venda_status": venda.status if venda_ok else None,
            "previsao_liberacao": previsao,
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
