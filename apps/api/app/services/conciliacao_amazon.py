"""Conciliação — Amazon: as transações dia a dia como extrato (passo 5, 15/10/2026).

Sondagem de 15/10 (docs/conciliacao-avaliacao.md, seção 24) e a tela Pagamentos ›
Transações do Seller Central: cada pedido entra na conta 3–4 dias depois do envio
(`Shipment`, já líquido de comissão, etiqueta e promoção), fica **Diferido** na
"reserva por data de entrega" até ~7 dias depois da entrega, vira liberado e a cada
14 dias a Amazon fecha o período e manda o total pro banco (`Transfer`). Estorno =
`Refund`; tarifas avulsas = `ServiceFee`; Ads = `ProductAdsPayment`; saldo negativo
cobrado no cartão = `DebtRecovery`.

O saldo é sintético e contínuo (como a Amazon mostra "Saldo total" = reserva +
disponível): recalculado do zero a cada rodada, em ordem de data. "Preso" = linhas
de venda ainda DEFERRED; o repasse pro banco é um Saque.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConciliacaoLoja, ConciliacaoMovimento, ConciliacaoSync, Integration
from app.models.conciliacao import ORIGEM_AMAZON
from app.services.conciliacao_extrato import bling_store_ids_da_loja, resolver_pedidos_bling
from app.services.conciliacao_shopee import inserir_movimentos

logger = structlog.get_logger()
BRT = ZoneInfo("America/Sao_Paulo")
ROTA_TRANSACOES = "/finances/2024-06-19/transactions"
ZERO = Decimal("0")
TIPOS = {
    "Shipment": "venda_liberada",
    "Refund": "estorno",
    "Transfer": "saque",
    "DebtRecovery": "ajuste_amazon",
    "ProductAdsPayment": "publicidade",
    "ServiceFee": "ajuste_amazon",
    "Adjustment": "ajuste_amazon",
    "Chargeback": "chargeback",
}
CLASSIFICACAO_AUTOMATICA = {
    "saque": "saque",
    "publicidade": "publicidade",
    "ajuste_amazon": "ajuste",
    "outro": "outro",
}


def _d(v: Any) -> Decimal:
    if isinstance(v, dict):
        v = v.get("currencyAmount")
    if v in (None, ""):
        return ZERO
    try:
        return Decimal(str(v)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return ZERO


def _iso(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def somar_quebras(no: Any, acc: dict[str, Decimal], prefixo: str = "") -> None:
    """Soma `breakdownAmount` por `breakdownType` (recursivo): "AmazonFees", "AmazonFees ›
    Commission", "ProductCharges › OurPricePrincipal"…"""
    if isinstance(no, dict):
        tipo = no.get("breakdownType")
        amt = (no.get("breakdownAmount") or {}).get("currencyAmount")
        if tipo and amt is not None:
            acc[f"{prefixo}{tipo}"] += _d(amt)
        for b in no.get("breakdowns") or []:
            somar_quebras(b, acc, f"{prefixo}{tipo} › " if tipo else prefixo)
    elif isinstance(no, list):
        for x in no:
            somar_quebras(x, acc, prefixo)


def quebras_da_transacao(tx: dict[str, Any]) -> dict[str, Decimal]:
    from collections import defaultdict

    acc: dict[str, Decimal] = defaultdict(Decimal)
    somar_quebras(tx.get("breakdowns"), acc)
    for it in tx.get("items") or []:
        if isinstance(it, dict):
            somar_quebras(it.get("breakdowns"), acc)
    return dict(acc)


def conta_amazon_de_quebras(q: dict[str, Decimal]) -> dict[str, Decimal]:
    """A conta de um pedido como a tela Transações mostra (15/10, Kia 301635 e 301943):
    produto (+), frete cobrado do cliente (+, envio próprio), tarifas da Amazon (−:
    comissão, Amazon for All 1,5%, retenção sobre o frete, etiqueta Easy Ship no DBA),
    promoção da loja (−). Tarifa = porcentagens fixas (regra do Vinicius); etiqueta vai
    pro frete; o resto das tarifas e a promoção vão pra Extras."""

    def g(chave: str) -> Decimal:
        return q.get(chave, ZERO)

    produto = g("ProductCharges")
    frete_cliente = g("Shipping")
    promocao = g("PromoRebates")  # negativo
    comissao = g("AmazonFees › Commission")
    amazon_for_all = g("AmazonFees › AmazonForAllFee")
    retencao_frete = g("AmazonFees › ShippingHB")
    etiqueta = g("AmazonFees › MFNPostageFee")
    tarifas = g("AmazonFees")
    outras_tarifas = tarifas - (comissao + amazon_for_all + retencao_frete + etiqueta)
    conhecidos = produto + frete_cliente + promocao + tarifas
    total = sum((v for k, v in q.items() if " › " not in k), ZERO)
    return {
        "produto": produto,
        "frete_cliente": frete_cliente,
        "valor": produto + frete_cliente,  # o que o cliente pagou à loja
        "promocao": promocao,
        "comissao": comissao,
        "amazon_for_all": amazon_for_all,
        "retencao_frete": retencao_frete,
        "etiqueta": etiqueta,
        "outras_tarifas": outras_tarifas,
        "tarifas": tarifas,
        "outros": total - conhecidos,
        "total": total,
    }


def transacao_para_movimento(tx: dict[str, Any]) -> dict[str, Any] | None:
    """Uma transação da Finances API → colunas de `conciliacao_movimentos` (sem a loja).
    O saldo é recalculado depois (`recalcular_saldo`)."""
    tid = tx.get("transactionId")
    quando = _iso(tx.get("postedDate"))
    if not tid or quando is None:
        return None
    tipo_amz = str(tx.get("transactionType") or "")
    status_amz = str(tx.get("transactionStatus") or "")
    valor = _d(tx.get("totalAmount"))
    tipo = TIPOS.get(tipo_amz, "outro")
    pedido = None
    for ri in tx.get("relatedIdentifiers") or []:
        if (
            isinstance(ri, dict)
            and str(ri.get("relatedIdentifierName") or "").upper() == "ORDER_ID"
        ):
            pedido = str(ri.get("relatedIdentifierValue") or "").strip() or None
            break
    if pedido is None:
        for it in tx.get("items") or []:
            for ri in (it.get("relatedIdentifiers") or []) if isinstance(it, dict) else []:
                if (
                    isinstance(ri, dict)
                    and str(ri.get("relatedIdentifierName") or "").upper() == "ORDER_ID"
                ):
                    pedido = str(ri.get("relatedIdentifierValue") or "").strip() or None
                    break
            if pedido:
                break
    q = quebras_da_transacao(tx)
    conta = conta_amazon_de_quebras(q)
    descricao = str(tx.get("description") or "").strip()
    # DBA: a etiqueta Easy Ship chega como ServiceFee separado, depois do lançamento —
    # é frete do pedido, não estorno (15/10, 32 pedidos da Kia marcados errado)
    if tipo_amz == "ServiceFee" and pedido and conta["etiqueta"]:
        tipo = "frete"
    if tipo == "venda_liberada":
        descricao = f"Lançamento do pedido {pedido}" if pedido else (descricao or "Lançamento")
        if status_amz == "DEFERRED":
            descricao += " (preso na reserva)"
    elif tipo == "estorno":
        descricao = f"Estorno do pedido {pedido}" if pedido else (descricao or "Estorno")
    elif tipo == "saque":
        descricao = "Repasse pro banco" + (
            f" · {descricao}" if descricao and descricao != "Disbursement" else ""
        )
    elif tipo_amz == "DebtRecovery":
        descricao = "Cobrança do saldo negativo (cartão/conta)"
    elif tipo_amz == "ProductAdsPayment":
        descricao = "Publicidade (Ads)"
    elif tipo == "frete":
        descricao = f"Etiqueta Easy Ship do pedido {pedido}"
    elif tipo_amz == "ServiceFee":
        descricao = f"Tarifa da Amazon{(' · ' + descricao) if descricao else ''}"
        if pedido:
            descricao += f" · pedido {pedido}"
    # Transfer vem positivo na API mas é dinheiro saindo; DebtRecovery é dinheiro entrando
    if tipo == "saque":
        credito, debito = ZERO, valor.copy_abs()
    else:
        credito, debito = (valor, ZERO) if valor >= ZERO else (ZERO, -valor)
    return {
        "origem": ORIGEM_AMAZON,
        "id_externo": str(tid),
        "data": quando,
        "tipo": tipo,
        "descricao": descricao,
        "sub_tipo": status_amz or None,  # DEFERRED | RELEASED | DEFERRED_RELEASED
        "pagamento_id": None,
        "pedido_marketplace": pedido,
        "pack_id": None,
        "shipping_id": None,
        "pedido_bling": None,
        "bling_id": None,
        "bruto": conta["produto"] or None,
        "tarifa": -conta["tarifas"] if conta["tarifas"] else None,
        "parcelamento": None,
        "frete": (-conta["etiqueta"] - conta["frete_cliente"])
        if (conta["etiqueta"] or conta["frete_cliente"])
        else None,
        "cupom": -conta["promocao"] if conta["promocao"] else None,
        "credito": credito,
        "debito": debito,
        "saldo_apos": None,
        "descontado_da_venda": None,
        "sku": None,
        "item_id": None,
        "data_aprovacao": None,
        "classificacao": None if pedido else CLASSIFICACAO_AUTOMATICA.get(tipo),
        "vinculado_por": None,
        "vinculado_em": None,
        "raw": {**tx, "_tipo_amazon": tipo_amz},
    }


def _tirar_duplicatas_de_liberacao(
    movs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], set[str]]:
    """A Amazon representa a saída da reserva com um registro NOVO: o lançamento
    original vira DEFERRED_RELEASED e aparece outro, RELEASED, do mesmo pedido e valor,
    na data em que liberou (15/10, Kia 301678: 446,20 em 06/10 e de novo em 14/10 —
    contava duas vezes). Fica o original (na data do lançamento, com "liberado em") e
    a repetição sai. Lançamento que nunca ficou preso vem só como RELEASED e fica."""
    from collections import Counter, defaultdict

    chave = lambda m: (m["pedido_marketplace"], m["tipo"], m["credito"], m["debito"])  # noqa: E731
    originais: Counter = Counter(
        chave(m)
        for m in movs
        if m["sub_tipo"] == "DEFERRED_RELEASED"
        and m["pedido_marketplace"]
        and m["tipo"] in ("venda_liberada", "estorno")
    )
    liberado_em: dict[tuple, list[tuple[datetime, str]]] = defaultdict(list)
    duplicadas: set[str] = set()
    for m in sorted(movs, key=lambda x: x["data"]):
        if (
            m["sub_tipo"] != "RELEASED"
            or not m["pedido_marketplace"]
            or m["tipo"] not in ("venda_liberada", "estorno")
        ):
            continue
        k = chave(m)
        if originais[k] > 0:
            originais[k] -= 1
            duplicadas.add(m["id_externo"])
            liberado_em[k].append((m["data"], m["id_externo"]))
    for m in movs:
        if m["sub_tipo"] == "DEFERRED_RELEASED" and liberado_em.get(chave(m)):
            quando, rep = liberado_em[chave(m)].pop(0)
            m["descricao"] = f"{m['descricao']} · liberado em {quando.astimezone(BRT):%d/%m}"
            m["raw"] = {**m["raw"], "_liberado_em": quando.isoformat(), "_repeticao": rep}
    return [m for m in movs if m["id_externo"] not in duplicadas], duplicadas


async def _casar_repeticoes_no_banco(
    session: AsyncSession,
    loja: ConciliacaoLoja,
    movs: list[dict[str, Any]],
    existentes: dict[str, ConciliacaoMovimento],
) -> set[str]:
    """RELEASED novo de pedido cujo lançamento original (DEFERRED/DEFERRED_RELEASED) já
    está gravado e ainda não "consumiu" uma repetição: marca o original como liberado
    e devolve os ids das repetições que não devem entrar."""
    candidatos = [
        m
        for m in movs
        if m["sub_tipo"] == "RELEASED"
        and m["pedido_marketplace"]
        and m["tipo"] in ("venda_liberada", "estorno")
        and m["id_externo"] not in existentes
    ]
    if not candidatos:
        return set()
    originais = (
        (
            await session.execute(
                select(ConciliacaoMovimento).where(
                    ConciliacaoMovimento.integration_id == loja.integration_id,
                    ConciliacaoMovimento.origem == ORIGEM_AMAZON,
                    ConciliacaoMovimento.pedido_marketplace.in_(
                        sorted({m["pedido_marketplace"] for m in candidatos})
                    ),
                    ConciliacaoMovimento.sub_tipo.in_(["DEFERRED", "DEFERRED_RELEASED"]),
                )
            )
        )
        .scalars()
        .all()
    )
    por_chave: dict[tuple, list[ConciliacaoMovimento]] = defaultdict(list)
    for o in originais:
        por_chave[(o.pedido_marketplace, o.tipo, o.credito, o.debito)].append(o)
    casadas: set[str] = set()
    for m in sorted(candidatos, key=lambda x: x["data"]):
        k = (m["pedido_marketplace"], m["tipo"], m["credito"], m["debito"])
        for o in por_chave.get(k, []):
            rep = (o.raw or {}).get("_repeticao")
            if rep and rep != m["id_externo"]:
                continue  # esse original já tem a sua repetição; procura outro
            if rep is None:
                quando = m["data"]
                o.sub_tipo = "DEFERRED_RELEASED"
                base = (o.descricao or "").split(" · liberado em ")[0]
                o.descricao = f"{base} · liberado em {quando.astimezone(BRT):%d/%m}"
                o.raw = {
                    **(o.raw or {}),
                    "_liberado_em": quando.isoformat(),
                    "_repeticao": m["id_externo"],
                }
            casadas.add(m["id_externo"])
            break
    if casadas:
        await session.flush()
    return casadas


MAX_PAGINAS_TRANSACOES = 30


async def ler_transacoes(
    cliente: Any,
    de: datetime,
    *,
    ate: datetime | None = None,
    max_paginas: int = MAX_PAGINAS_TRANSACOES,
) -> tuple[list[dict[str, Any]], bool]:
    """Todas as transações lançadas desde `de` (Finances 2024-06-19), paginando.
    Devolve (transações, cortado): `cortado` = bateu em `max_paginas` com `nextToken`
    ainda na mão (16/10, revisão: antes cortava em silêncio)."""
    out: list[dict[str, Any]] = []
    token: str | None = None
    for _ in range(max_paginas):
        params: dict[str, Any] = {"postedAfter": de.astimezone(UTC).isoformat()}
        if ate is not None:
            params["postedBefore"] = ate.astimezone(UTC).isoformat()
        if token:
            params = {"nextToken": token, "postedAfter": params["postedAfter"]}
        r = await cliente._request("GET", ROTA_TRANSACOES, params=params)
        if r.status_code != 200:
            raise RuntimeError(f"amazon transacoes: HTTP {r.status_code} {r.text[:200]}")
        body = r.json() or {}
        payload = body.get("payload") if isinstance(body.get("payload"), dict) else body
        lista = [t for t in (payload.get("transactions") or []) if isinstance(t, dict)]
        out.extend(lista)
        token = payload.get("nextToken") or None
        if not token or not lista:
            return out, False
    return out, token is not None


async def recalcular_saldo(session: AsyncSession, loja: ConciliacaoLoja) -> Decimal:
    """Saldo sintético contínuo: 0 no início, cada linha soma (crédito − débito), em
    ordem de data. Devolve o saldo final (= reserva + disponível na Amazon)."""
    movs = (
        (
            await session.execute(
                select(ConciliacaoMovimento)
                .where(
                    ConciliacaoMovimento.integration_id == loja.integration_id,
                    ConciliacaoMovimento.origem == ORIGEM_AMAZON,
                )
                .order_by(ConciliacaoMovimento.data, ConciliacaoMovimento.id_externo)
            )
        )
        .scalars()
        .all()
    )
    saldo = ZERO
    for m in movs:
        saldo += (m.credito or ZERO) - (m.debito or ZERO)
        if m.saldo_apos != saldo:
            m.saldo_apos = saldo
    return saldo


async def _sync_row(session: AsyncSession, loja: ConciliacaoLoja, chave: str) -> ConciliacaoSync:
    row = (
        await session.execute(
            select(ConciliacaoSync).where(
                ConciliacaoSync.integration_id == loja.integration_id,
                ConciliacaoSync.origem == ORIGEM_AMAZON,
                ConciliacaoSync.chave == chave,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = ConciliacaoSync(
            integration_id=loja.integration_id,
            origem=ORIGEM_AMAZON,
            chave=chave,
            status="pedido",
            detalhe={},
        )
        session.add(row)
        await session.flush()
    return row


async def run_amazon_sync(
    session: AsyncSession,
    loja: ConciliacaoLoja,
    cliente: Any,
    *,
    agora: datetime | None = None,
    sobreposicao_dias: int = 21,
    max_dias: int = 60,
    max_paginas: int = MAX_PAGINAS_TRANSACOES,
) -> dict[str, Any]:
    """Relê as transações desde o início da loja (ou os últimos `max_dias`), com
    sobreposição: linha nova entra, linha que mudou de situação (DEFERRED → RELEASED)
    é atualizada, e o saldo é recalculado do zero. Leitura cortada em `max_paginas`
    avisa (`cortado`/`aviso` no resumo, `loja.ultimo_erro`) em vez de calar (16/10)."""
    agora = agora or datetime.now(UTC)
    hoje = agora.astimezone(BRT).date()
    ultimo = (
        await session.execute(
            select(ConciliacaoSync.chave)
            .where(
                ConciliacaoSync.integration_id == loja.integration_id,
                ConciliacaoSync.origem == ORIGEM_AMAZON,
                ConciliacaoSync.status == "lido",
            )
            .order_by(ConciliacaoSync.chave.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    de_dia = max(loja.inicio, hoje - timedelta(days=max_dias))
    if ultimo:
        try:
            de_dia = max(de_dia, date.fromisoformat(ultimo) - timedelta(days=sobreposicao_dias))
        except ValueError:
            pass
    de = datetime.combine(de_dia, datetime.min.time(), tzinfo=BRT)
    row = await _sync_row(session, loja, hoje.isoformat())
    row.pedido_em = agora
    resumo: dict[str, Any] = {
        "de": de_dia.isoformat(),
        "lidas": 0,
        "inseridos": 0,
        "atualizados": 0,
        "erros": [],
    }
    integration = await session.get(Integration, loja.integration_id)
    lojas_bling = await bling_store_ids_da_loja(session, integration) if integration else set()
    try:
        txs, cortado = await ler_transacoes(cliente, de, max_paginas=max_paginas)
    except Exception as e:  # noqa: BLE001
        row.status = "erro"
        row.erro = f"{type(e).__name__}: {str(e)[:300]}"
        resumo["erros"].append({"erro": row.erro})
        logger.warning("conciliacao_amazon_falhou", loja=loja.conta, erro=row.erro)
        return resumo
    aviso: str | None = None
    if cortado:
        aviso = (
            f"amazon: leitura cortada em {max_paginas} páginas de transações "
            f"(desde {de_dia.isoformat()}; o que veio depois não entrou)"
        )
        resumo["cortado"] = True
        resumo["aviso"] = aviso
        loja.ultimo_erro = aviso
        logger.warning("conciliacao_amazon_cortado", loja=loja.conta, paginas=max_paginas)
    movs = [m for m in (transacao_para_movimento(t) for t in txs) if m]
    movs, duplicadas = _tirar_duplicatas_de_liberacao(movs)
    resumo["lidas"] = len(movs)
    resumo["duplicatas"] = len(duplicadas)
    ids = {m["pedido_marketplace"] for m in movs if m["pedido_marketplace"]}
    mapa = await resolver_pedidos_bling(session, ids, lojas_bling)
    for m in movs:
        achado = mapa.get(m["pedido_marketplace"] or "")
        if achado:
            m["pedido_bling"], m["bling_id"] = achado
    # linhas já gravadas: atualiza a situação (preso → liberado), a descrição e o raw
    existentes = {
        m.id_externo: m
        for m in (
            await session.execute(
                select(ConciliacaoMovimento).where(
                    ConciliacaoMovimento.integration_id == loja.integration_id,
                    ConciliacaoMovimento.origem == ORIGEM_AMAZON,
                    ConciliacaoMovimento.id_externo.in_([m["id_externo"] for m in movs] or ["-"]),
                )
            )
        ).scalars()
    }
    # repetição cujo original já saiu da janela de leitura (16/10, revisão: a partir de
    # ~22 dias a liberação voltava a entrar em dobro): casa com o que está no banco
    casadas = await _casar_repeticoes_no_banco(session, loja, movs, existentes)
    if casadas:
        movs = [m for m in movs if m["id_externo"] not in casadas]
        resumo["duplicatas_no_banco"] = len(casadas)
        resumo["lidas"] = len(movs)
    # duplicata que já tinha entrado (gravada pelo robô, sem mexida humana): sai
    if duplicadas:
        velhas = (
            (
                await session.execute(
                    select(ConciliacaoMovimento).where(
                        ConciliacaoMovimento.integration_id == loja.integration_id,
                        ConciliacaoMovimento.origem == ORIGEM_AMAZON,
                        ConciliacaoMovimento.id_externo.in_(sorted(duplicadas)),
                        ConciliacaoMovimento.vinculado_por.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for v in velhas:
            await session.delete(v)
        resumo["removidas"] = len(velhas)
        await session.flush()
    novos: list[dict[str, Any]] = []
    for m in movs:
        atual = existentes.get(m["id_externo"])
        if atual is None:
            novos.append(m)
            continue
        mudou = (
            atual.sub_tipo != m["sub_tipo"]
            or atual.tipo != m["tipo"]
            or atual.descricao != m["descricao"]
        )
        if mudou:
            atual.sub_tipo = m["sub_tipo"]
            atual.tipo = m["tipo"]
            atual.descricao = m["descricao"]
            atual.raw = m["raw"]
            resumo["atualizados"] += 1
        if not atual.pedido_bling and m["pedido_bling"] and not atual.vinculado_por:
            atual.pedido_bling, atual.bling_id = m["pedido_bling"], m["bling_id"]
    if novos:
        resumo["inseridos"] = await inserir_movimentos(session, loja, novos)
    await session.flush()
    saldo = await recalcular_saldo(session, loja)
    row.status = "lido"
    row.lido_em = agora
    row.erro = aviso  # None quando a leitura veio inteira
    row.linhas = len(movs)
    row.detalhe = {**resumo, "saldo": str(saldo)}
    loja.ultimo_extrato_em = agora
    await session.flush()
    resumo["saldo"] = float(saldo)
    return resumo
