"""Conciliação — Amazon por ARQUIVO (16/10): o CSV de "Pagamentos › Visualizar
transações › Fazer download" do Seller Central, pra loja que a API nega (KFA, caso
21731531021).

Cada linha do CSV vira uma transação no formato da Finances API (`breakdowns`,
`relatedIdentifiers`, `transactionStatus`) e passa pelo MESMO `transacao_para_movimento`
da leitura por API — a conta do pedido (Valor, Tarifa, Frete, Extras), o "preso na
reserva" e a etiqueta Easy Ship saem iguais aos da Kia e da Poofy.

O que o arquivo NÃO traz: os repasses pro banco (Transfer) e o detalhe das tarifas
(comissão × Amazon for All × retenção): vem tudo em "Tarifas da Amazon". Linha repetida
(mesmo dia, pedido, tipo e valores) ganha "#2" na identidade. Diferido que vira
Liberado num arquivo seguinte atualiza a linha gravada.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import re
import secrets
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ConciliacaoLoja,
    ConciliacaoMovimento,
    ConciliacaoSync,
    Integration,
    LogisticaRoboComando,
    MarketplaceOrderFinancial,
)
from app.models.conciliacao import ORIGEM_AMAZON
from app.services.conciliacao_amazon import recalcular_saldo, transacao_para_movimento
from app.services.conciliacao_dias import reabrir_dias
from app.services.conciliacao_extrato import bling_store_ids_da_loja, resolver_pedidos_bling
from app.services.conciliacao_mp_csv import dinheiro
from app.services.conciliacao_shopee import inserir_movimentos

logger = structlog.get_logger()
BRT = ZoneInfo("America/Sao_Paulo")
ZERO = Decimal("0")

COLUNAS = {
    "data": "Data",
    "status": "Status da transação",
    "grupo": "Grupo de Pagamento",
    "tipo": "Tipo de transação",
    "pedido": "ID do pedido",
    "detalhes": "Detalhes do produto",
    "produto": "Custo total do produto",
    "promocao": "Total de descontos promocionais",
    "tarifas": "Tarifas da Amazon",
    "outros": "Outros",
    "total": "(total) (BRL)",
}
# "Tipo de transação" do CSV → transactionType da Finances API
TIPOS_CSV = {
    "pagamento do pedido": "Shipment",
    "reembolso": "Refund",
    "reembolso de estorno": "Refund",
    "reembolso sob a garantia de a a z": "Refund",
    "cobranças do delivery by amazon": "ServiceFee",  # etiqueta Easy Ship, por pedido
    "tarifas de serviço": "ServiceFee",
    "serviços de envio comprados pela amazon": "ServiceFee",
    "amazon shipping": "ServiceFee",
    "liquidações": "Transfer",
    "pago à amazon": "DebtRecovery",
    "pago à amazon | repagamento do vendedor": "DebtRecovery",
    "ajustes de liquidações": "Adjustment",
    "cobrança de repasse": "Adjustment",
    "pagamentos para finalidades específicas": "Adjustment",
    "receivables adjustments": "Adjustment",
    "automated loan repayment": "Adjustment",
    "inventory reimbursement": "Adjustment",
    "acúmulo": "Adjustment",
    "outros": "Adjustment",
}
STATUS_CSV = {"liberado": "RELEASED", "diferido": "DEFERRED"}
_PEDIDO = re.compile(r"^\d{3}-\d{7}-\d{7}$")


def ler_csv_amazon(texto: str) -> tuple[list[str], list[dict[str, str]]]:
    """Cabeçalho e linhas. Confere as 11 colunas do download do Seller Central."""
    texto = texto.lstrip("\ufeff")
    leitor = csv.DictReader(io.StringIO(texto))
    cab = [c.strip() for c in (leitor.fieldnames or [])]
    faltam = [c for c in COLUNAS.values() if c not in cab]
    if faltam:
        raise ValueError(
            "não parece o CSV de 'Visualizar transações' da Amazon; faltam as colunas: "
            + ", ".join(faltam)
        )
    rows = [{(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in leitor]
    return cab, rows


def _data(v: str) -> datetime | None:
    try:
        return datetime.strptime(v.strip(), "%d/%m/%Y").replace(tzinfo=BRT)
    except ValueError:
        return None


def _quebra(tipo: str, valor: Decimal, filhos: list[dict[str, Any]] | None = None) -> dict:
    q: dict[str, Any] = {
        "breakdownType": tipo,
        "breakdownAmount": {"currencyAmount": str(valor), "currencyCode": "BRL"},
    }
    if filhos:
        q["breakdowns"] = filhos
    return q


def linha_para_transacao(row: dict[str, str], *, ordem: int) -> dict[str, Any] | None:
    """Uma linha do CSV → transação no formato da Finances API."""
    quando = _data(row.get(COLUNAS["data"], ""))
    if quando is None:
        return None
    tipo_csv = row.get(COLUNAS["tipo"], "").strip()
    status_csv = row.get(COLUNAS["status"], "").strip()
    detalhes = row.get(COLUNAS["detalhes"], "").strip()
    pedido = row.get(COLUNAS["pedido"], "").strip()
    pedido = pedido if _PEDIDO.match(pedido) else None
    tipo_amz = TIPOS_CSV.get(tipo_csv.lower(), "Adjustment")
    if tipo_amz == "ServiceFee" and "publicidade" in detalhes.lower():
        tipo_amz = "ProductAdsPayment"
    produto = dinheiro(row.get(COLUNAS["produto"]))
    promocao = dinheiro(row.get(COLUNAS["promocao"]))
    tarifas = dinheiro(row.get(COLUNAS["tarifas"]))
    outros = dinheiro(row.get(COLUNAS["outros"]))
    total = dinheiro(row.get(COLUNAS["total"]))
    quebras: list[dict[str, Any]] = []
    if produto:
        quebras.append(_quebra("ProductCharges", produto))
    if promocao:
        quebras.append(_quebra("PromoRebates", promocao))
    if tarifas:
        # o CSV não abre a tarifa; com pedido numa linha de serviço é a etiqueta Easy Ship
        sub = "MFNPostageFee" if (tipo_amz == "ServiceFee" and pedido) else "Commission"
        quebras.append(_quebra("AmazonFees", tarifas, [_quebra(sub, tarifas)]))
    if outros:
        quebras.append(_quebra("Other", outros))
    # Identidade SEM a data (16/10, revisão): quando a Amazon libera a venda presa, a
    # mesma linha volta no arquivo seguinte com a data da liberação ("02/10 Diferido"
    # vira "16/10 Liberado") — com a data na chave ela entrava de novo (300772 em dobro).
    partes = [
        row.get(COLUNAS["grupo"], ""),
        tipo_csv,
        pedido or "",
        detalhes,
        str(produto),
        str(promocao),
        str(tarifas),
        str(outros),
        str(total),
    ]
    # linha sem pedido (taxa de assinatura, publicidade…) repete todo mês com os mesmos
    # valores: a data fica na identidade dela (2ª rodada: a numeração #2/#3 escorregava
    # quando a mais velha saía da janela de 35 dias)
    data_csv = row.get(COLUNAS["data"], "")
    if pedido:
        tid = _hash_upload("|".join(partes))
        legado = _hash_upload("|".join([data_csv, *partes]))  # identidade original (com data)
    else:
        tid = _hash_upload("|".join([data_csv, *partes]))
        legado = _hash_upload("|".join(partes))  # identidade do 215f0494 (sem data), 16/10 à noite
    return {
        "transactionId": tid,
        "_idLegado": legado,
        "transactionType": tipo_amz,
        "transactionStatus": STATUS_CSV.get(status_csv.lower(), status_csv.upper() or "RELEASED"),
        "postedDate": (quando + timedelta(seconds=ordem)).isoformat(),
        "totalAmount": {"currencyAmount": str(total), "currencyCode": "BRL"},
        "description": detalhes,
        "relatedIdentifiers": (
            [{"relatedIdentifierName": "ORDER_ID", "relatedIdentifierValue": pedido}]
            if pedido
            else []
        ),
        "breakdowns": quebras,
        "_fonte": "upload",
        "_grupo_pagamento": row.get(COLUNAS["grupo"], ""),
        "_tipo_csv": tipo_csv,
        "_status_csv": status_csv,
    }


def _hash_upload(chave: str) -> str:
    return "upload:" + hashlib.sha1(chave.encode("utf-8"), usedforsecurity=False).hexdigest()[:24]


def transacoes_do_csv(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    """O arquivo vem da mais nova pra mais velha; dentro do dia a ordem vira crescente
    (segundos a partir de 00:00). Linha repetida ganha #2, #3…"""
    por_dia: dict[str, int] = {}
    vistos: dict[str, int] = {}
    vistos_legado: dict[str, int] = {}  # numeração própria (id e legado podem coincidir)
    txs: list[dict[str, Any]] = []
    for row in reversed(rows):
        dia = row.get(COLUNAS["data"], "")
        ordem = por_dia.get(dia, 0)
        por_dia[dia] = ordem + 1
        tx = linha_para_transacao(row, ordem=ordem)
        if not tx:
            continue
        n = vistos.get(tx["transactionId"], 0) + 1
        vistos[tx["transactionId"]] = n
        if n > 1:
            tx["transactionId"] = f"{tx['transactionId']}#{n}"
        nl = vistos_legado.get(tx["_idLegado"], 0) + 1
        vistos_legado[tx["_idLegado"]] = nl
        if nl > 1:
            tx["_idLegado"] = f"{tx['_idLegado']}#{nl}"
        txs.append(tx)
    return txs


def _chave_gemea(m: Any) -> tuple:
    """Pedido + tipo + valor: a linha presa e a liberada do mesmo lançamento."""
    pedido = m["pedido_marketplace"] if isinstance(m, dict) else m.pedido_marketplace
    tipo = m["tipo"] if isinstance(m, dict) else m.tipo
    credito = m["credito"] if isinstance(m, dict) else m.credito
    debito = m["debito"] if isinstance(m, dict) else m.debito
    return (pedido, tipo, Decimal(str(credito or 0)), Decimal(str(debito or 0)))


def _marcar_liberada(ex: ConciliacaoMovimento, m: dict[str, Any]) -> None:
    """Linha presa na reserva que o arquivo agora traz liberada (com a data da
    liberação): fica na data do lançamento, marcada "liberado em" — como na API."""
    quando = m["data"]
    ex.sub_tipo = "RELEASED"
    ex.descricao = f"{m['descricao']} · liberado em {quando.astimezone(BRT):%d/%m}"
    ex.raw = {**m["raw"], "_liberado_em": quando.isoformat()}
    if not ex.pedido_bling and m["pedido_bling"] and not ex.vinculado_por:
        ex.pedido_bling, ex.bling_id = m["pedido_bling"], m["bling_id"]


async def _gravar_vendas_da_margem(
    session: AsyncSession, loja: ConciliacaoLoja, movs: list[dict[str, Any]], agora: datetime
) -> dict[str, int]:
    """16/10 à noite (Vinicius: "o valor esperado é o da plataforma, lido pela Margem"):
    a Margem não lê a KFA pela API (negada), então a própria leitura do arquivo grava a
    venda na Margem (MarketplaceOrderFinancial) com o que a Amazon diz do pedido —
    lançamento + tarifas — enquanto a venda está presa; quando libera, a venda congela e
    a Conciliação compara o que entrou com ela."""
    from app.services.conciliacao_calculo import conta_amazon_de_transacoes

    por_pedido: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for m in movs:
        if m["pedido_marketplace"]:
            por_pedido[m["pedido_marketplace"]].append(m)
    criadas = atualizadas = 0
    for pedido, linhas in por_pedido.items():
        vendas = [m for m in linhas if m["tipo"] == "venda_liberada"]
        if not vendas:
            continue
        liberada = any(m["sub_tipo"] != "DEFERRED" for m in vendas)
        mof = (
            await session.execute(
                select(MarketplaceOrderFinancial).where(
                    MarketplaceOrderFinancial.integration_id == loja.integration_id,
                    MarketplaceOrderFinancial.external_order_id == pedido,
                )
            )
        ).scalar_one_or_none()
        if mof is not None and (mof.raw or {}).get("fonte") != "csv_kfa":
            continue  # venda lida pela Margem de verdade: não mexe
        if mof is not None and (mof.raw or {}).get("congelada"):
            continue
        txs = [m["raw"] for m in linhas]
        conta = conta_amazon_de_transacoes(txs) or {}
        raw = {"transactions": {"transactions": txs}, "fonte": "csv_kfa"}
        if liberada:
            raw["congelada"] = agora.isoformat()
        campos = {
            "status": "posted",
            "gross_amount": conta.get("produto"),
            "fee_amount": abs(conta.get("tarifas") or ZERO),
            "freight_amount": abs(conta.get("etiqueta") or ZERO),
            "discount_amount": abs(conta.get("promocao") or ZERO),
            "net_amount": conta.get("liquido"),
            "raw": raw,
            "fetched_at": agora,
            "bling_id": vendas[0].get("bling_id"),
            "pedido_bling": vendas[0].get("pedido_bling"),
        }
        if mof is None:
            session.add(
                MarketplaceOrderFinancial(
                    integration_id=loja.integration_id,
                    platform=loja.platform,
                    external_order_id=pedido,
                    **campos,
                )
            )
            criadas += 1
        else:
            for k, v in campos.items():
                setattr(mof, k, v)
            atualizadas += 1
    if criadas or atualizadas:
        await session.flush()
    return {"criadas": criadas, "atualizadas": atualizadas}


async def ingerir_csv_amazon(
    session: AsyncSession, loja: ConciliacaoLoja, texto: str, *, agora: datetime | None = None
) -> dict[str, Any]:
    agora = agora or datetime.now(UTC)
    _cab, rows = ler_csv_amazon(texto)
    if not rows:
        # 16/10 (revisão): arquivo vazio contava como "lido" e a loja parecia em dia
        raise ValueError("arquivo sem transações: nada gravado (confira o período do download)")
    txs = transacoes_do_csv(rows)
    legados = {t["transactionId"]: t.pop("_idLegado", None) for t in txs}
    movs = [m for m in (transacao_para_movimento(t) for t in txs) if m]
    integration = await session.get(Integration, loja.integration_id)
    lojas_bling = await bling_store_ids_da_loja(session, integration) if integration else set()
    ids = {m["pedido_marketplace"] for m in movs if m["pedido_marketplace"]}
    mapa = await resolver_pedidos_bling(session, ids, lojas_bling)
    for m in movs:
        achado = mapa.get(m["pedido_marketplace"] or "")
        if achado:
            m["pedido_bling"], m["bling_id"] = achado
    vendas_margem = await _gravar_vendas_da_margem(session, loja, movs, agora)
    ids_arquivo = [m["id_externo"] for m in movs] + [v for v in legados.values() if v]
    existentes = {
        m.id_externo: m
        for m in (
            await session.execute(
                select(ConciliacaoMovimento).where(
                    ConciliacaoMovimento.integration_id == loja.integration_id,
                    ConciliacaoMovimento.origem == ORIGEM_AMAZON,
                    ConciliacaoMovimento.id_externo.in_(ids_arquivo or ["-"]),
                )
            )
        ).scalars()
    }
    # linhas presas na reserva já gravadas (por qualquer identidade): a candidata a
    # "gêmea" de uma linha liberada do mesmo pedido e valor
    presas: dict[tuple, list[ConciliacaoMovimento]] = defaultdict(list)
    for p in (
        await session.execute(
            select(ConciliacaoMovimento).where(
                ConciliacaoMovimento.integration_id == loja.integration_id,
                ConciliacaoMovimento.origem == ORIGEM_AMAZON,
                ConciliacaoMovimento.id_externo.like("upload:%"),
                ConciliacaoMovimento.sub_tipo == "DEFERRED",
            )
        )
    ).scalars():
        presas[_chave_gemea(p)].append(p)
    novos: list[dict[str, Any]] = []
    atualizados = migrados = liberadas = duplicatas_apagadas = 0
    dias_mexidos: set[date] = set()
    por_tipo: dict[str, int] = {}
    usados: set = set()
    for m in movs:
        por_tipo[m["tipo"]] = por_tipo.get(m["tipo"], 0) + 1
        ex = existentes.get(m["id_externo"])
        leg = legados.get(m["id_externo"])
        if ex is not None and leg and leg != m["id_externo"]:
            velha = existentes.get(leg)
            if velha is not None and velha is not ex and not velha.vinculado_por:
                # a mesma linha gravada pelas duas identidades (16/10 à noite: publicidade,
                # cobrança do saldo e assinatura em dobro): a antiga sai
                existentes.pop(leg)
                dias_mexidos.add(velha.data.astimezone(BRT).date())
                await session.delete(velha)
                duplicatas_apagadas += 1
        if ex is None:
            leg = legados.get(m["id_externo"])
            ex = existentes.get(leg) if leg else None
            if ex is not None:
                # gravada antes da identidade nova: só troca o id
                existentes.pop(leg)
                ex.id_externo = m["id_externo"]
                existentes[m["id_externo"]] = ex
                migrados += 1
        if ex is None and m["sub_tipo"] == "RELEASED" and m["pedido_marketplace"]:
            fila = [p for p in presas.get(_chave_gemea(m), []) if p.id not in usados]
            if fila:
                # a venda presa liberou: é a MESMA linha, com a data da liberação no
                # arquivo — fica na data do lançamento, marcada "liberado em"
                ex = fila[0]
                usados.add(ex.id)
                existentes[m["id_externo"]] = ex
                ex.id_externo = m["id_externo"]
                _marcar_liberada(ex, m)
                liberadas += 1
                continue
        if ex is None:
            novos.append(m)
            continue
        usados.add(ex.id)
        if m["sub_tipo"] == "RELEASED" and ex.sub_tipo == "DEFERRED":
            # a presa liberou (a linha volta com a data da liberação): fica na data do
            # lançamento, marcada "liberado em"
            _marcar_liberada(ex, m)
            liberadas += 1
            continue
        mudou = False
        if ex.sub_tipo != m["sub_tipo"]:
            ex.sub_tipo = m["sub_tipo"]
            ex.descricao = m["descricao"]
            ex.raw = dict(m["raw"])
            mudou = True
        if not ex.pedido_bling and m["pedido_bling"] and not ex.vinculado_por:
            ex.pedido_bling, ex.bling_id = m["pedido_bling"], m["bling_id"]
            mudou = True
        if mudou:
            atualizados += 1
    # linha presa que o arquivo não traz mais e cuja liberada já está gravada (entrou em
    # dobro pela identidade antiga — 300772): a presa sai; vínculo manual passa pra liberada
    no_arquivo = {m["id_externo"] for m in movs}
    liberadas_gravadas: dict[tuple, list[ConciliacaoMovimento]] = defaultdict(list)
    for ex in existentes.values():
        if ex.sub_tipo == "RELEASED" and ex.pedido_marketplace:
            liberadas_gravadas[_chave_gemea(ex)].append(ex)
    for chave, fila in presas.items():
        for p in fila:
            if p.id in usados or p.id_externo in no_arquivo:
                continue
            gemeas = [g for g in liberadas_gravadas.get(chave, []) if g.id != p.id]
            if not gemeas:
                continue
            g = gemeas[0]
            if p.vinculado_por and not g.vinculado_por:
                g.vinculado_por, g.vinculado_em = p.vinculado_por, p.vinculado_em
                g.pedido_bling, g.bling_id = p.pedido_bling, p.bling_id
            if not g.raw.get("_liberado_em"):
                g.raw = {**g.raw, "_liberado_em": g.data.isoformat()}
            g.data = min(g.data, p.data)  # fica na data do lançamento, como a liberada normal
            if "liberado em" not in (g.descricao or ""):
                lib = g.raw["_liberado_em"]
                g.descricao = f"{g.descricao} · liberado em {lib[8:10]}/{lib[5:7]}"
            dias_mexidos.add(p.data.astimezone(BRT).date())
            dias_mexidos.add(g.data.astimezone(BRT).date())
            await session.delete(p)
            duplicatas_apagadas += 1
    if duplicatas_apagadas:
        await session.flush()
        # a presa saiu e a liberada mudou de dia: dias fechados reabrem (2ª rodada)
        await reabrir_dias(
            session, loja, dias_mexidos, motivo="lançamento preso da Amazon em dobro removido"
        )
    inseridos = await inserir_movimentos(session, loja, novos) if novos else 0
    saldo = await recalcular_saldo(session, loja)
    datas = [t["postedDate"][:10] for t in txs]
    stats = {
        "linhas": len(rows),
        "movimentos": len(movs),
        "inseridos": inseridos,
        "atualizados": atualizados,
        "migrados": migrados,
        "liberadas": liberadas,
        "duplicatas_apagadas": duplicatas_apagadas,
        "vendas_margem": vendas_margem,
        "ja_existiam": len(movs) - inseridos - atualizados - liberadas,
        "de": min(datas) if datas else None,
        "ate": max(datas) if datas else None,
        "por_tipo": por_tipo,
        "saldo_final": str(saldo),
    }
    session.add(
        ConciliacaoSync(
            integration_id=loja.integration_id,
            origem=ORIGEM_AMAZON,
            # com microssegundos: dois envios no mesmo segundo não colidem
            chave=f"upload:{agora:%Y-%m-%dT%H:%M:%S.%f}",
            status="lido",
            pedido_em=agora,
            lido_em=agora,
            linhas=len(rows),
            detalhe=stats,
        )
    )
    loja.ultimo_extrato_em = agora
    loja.ultimo_erro = None
    await session.flush()
    logger.info(
        "conciliacao_amazon_upload",
        loja=loja.conta,
        **{k: v for k, v in stats.items() if k != "por_tipo"},
    )
    return stats


# ---------- chave do script (16/10) ----------
# O script no Mac ("KFA Conciliação.command") baixa o CSV pelo AdsPower e envia sem
# ninguém logado: identifica-se por uma chave gerada em Conciliação › Amazon › Enviar
# transações › "Gerar chave pro script". Guardamos só o hash; a chave aparece uma vez.
CHAVE_CONFIG_UPLOAD = "conciliacao_upload_chave"


async def gerar_chave_upload(session: AsyncSession, *, por: str | None) -> str:
    from app.models import ConfiguracaoSegura
    from app.security.cipher import encrypt_json

    chave = "kfa_" + secrets.token_urlsafe(30)
    row = await session.get(ConfiguracaoSegura, CHAVE_CONFIG_UPLOAD)
    if row is None:
        row = ConfiguracaoSegura(chave=CHAVE_CONFIG_UPLOAD, valor=b"", detalhe={})
        session.add(row)
    row.valor = encrypt_json({"hash": hashlib.sha256(chave.encode()).hexdigest()})
    row.detalhe = {"gerada_em": datetime.now(UTC).isoformat(), "por": por}
    row.atualizado_por = por
    await session.flush()
    return chave


async def situacao_chave_upload(session: AsyncSession) -> dict[str, Any]:
    from app.models import ConfiguracaoSegura

    row = await session.get(ConfiguracaoSegura, CHAVE_CONFIG_UPLOAD)
    if row is None:
        return {"tem_chave": False}
    return {"tem_chave": True, **(row.detalhe or {})}


async def chave_upload_valida(session: AsyncSession, chave: str | None) -> bool:
    from app.models import ConfiguracaoSegura
    from app.security.cipher import decrypt_json

    if not chave:
        return False
    row = await session.get(ConfiguracaoSegura, CHAVE_CONFIG_UPLOAD)
    if row is None:
        return False
    try:
        guardado = str(decrypt_json(row.valor).get("hash") or "")
    except Exception:  # noqa: BLE001
        return False
    return hmac.compare_digest(guardado, hashlib.sha256(chave.strip().encode()).hexdigest())


# ---------- robô do Mac mini (16/10) ----------
# Botão "buscar transações" na loja por arquivo e o cron das 06:00 BRT criam um
# comando `amazon_transacoes_csv` na fila do robô da Logística; o executor do mini
# (fila `conciliacao`) abre o perfil no AdsPower, baixa o CSV e envia pelo endpoint
# do script (X-Agent-Token). O desfecho volta por /agent/comandos/{id}/resultado.
DIAS_PADRAO_ROBO = 35


async def enfileirar_busca_csv(
    session: AsyncSession,
    loja: ConciliacaoLoja,
    *,
    user_id: Any = None,
    dias: int = DIAS_PADRAO_ROBO,
) -> tuple[LogisticaRoboComando, bool]:
    """Cria o comando pro robô; se já há um pendente/em execução desta loja, devolve ele
    (novo=False). Só pra loja com `modo_extrato = upload`."""
    from app.services.logistica_robo import ACAO_AMAZON_CSV

    aberto = (
        await session.execute(
            select(LogisticaRoboComando)
            .where(
                LogisticaRoboComando.acao == ACAO_AMAZON_CSV,
                LogisticaRoboComando.status.in_(("pending", "claimed")),
                LogisticaRoboComando.payload["integration_id"].astext == str(loja.integration_id),
            )
            .order_by(LogisticaRoboComando.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if aberto is not None:
        return aberto, False
    cmd = LogisticaRoboComando(
        logistica_id=None,
        acao=ACAO_AMAZON_CSV,
        payload={
            "loja": loja.conta,
            "integration_id": str(loja.integration_id),
            "plataforma": getattr(loja.platform, "value", str(loja.platform)),
            "dias": int(dias),
        },
        created_by=user_id,
    )
    session.add(cmd)
    await session.flush()
    logger.info("conciliacao_amazon_csv_enfileirado", loja=loja.conta, comando=str(cmd.id))
    return cmd, True


async def enfileirar_busca_csv_todas(session: AsyncSession) -> list[str]:
    """Cron das 06:00 BRT: uma tarefa por loja por arquivo."""
    from app.services.conciliacao_jobs import lojas_ativas

    lojas = [x for x in await lojas_ativas(session) if x.modo_extrato == "upload"]
    novas: list[str] = []
    for loja in lojas:
        _cmd, novo = await enfileirar_busca_csv(session, loja)
        if novo:
            novas.append(loja.conta)
    await session.commit()
    return novas


async def registrar_resultado_robo(
    session: AsyncSession, cmd: LogisticaRoboComando, *, ok: bool
) -> None:
    """Desfecho do robô na loja: falhou → `ultimo_erro` (aparece no Resumo); deu certo →
    limpa (o envio em si já marcou `ultimo_extrato_em`)."""
    iid = str((cmd.payload or {}).get("integration_id") or "")
    if not iid:
        return
    loja = (
        await session.execute(select(ConciliacaoLoja).where(ConciliacaoLoja.integration_id == iid))
    ).scalar_one_or_none()
    if loja is None:
        return
    if ok:
        if (loja.ultimo_erro or "").startswith("robô:"):
            loja.ultimo_erro = None
    else:
        loja.ultimo_erro = f"robô: {(cmd.result or 'falhou')[:200]}"
    await session.flush()
