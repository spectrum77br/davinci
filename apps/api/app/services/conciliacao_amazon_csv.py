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
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConciliacaoLoja, ConciliacaoMovimento, ConciliacaoSync, Integration
from app.models.conciliacao import ORIGEM_AMAZON
from app.services.conciliacao_amazon import recalcular_saldo, transacao_para_movimento
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
    chave = "|".join(
        [
            row.get(COLUNAS["data"], ""),
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
    )
    tid = "upload:" + hashlib.sha1(chave.encode("utf-8"), usedforsecurity=False).hexdigest()[:24]
    return {
        "transactionId": tid,
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


def transacoes_do_csv(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    """O arquivo vem da mais nova pra mais velha; dentro do dia a ordem vira crescente
    (segundos a partir de 00:00). Linha repetida ganha #2, #3…"""
    por_dia: dict[str, int] = {}
    vistos: dict[str, int] = {}
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
        txs.append(tx)
    return txs


async def ingerir_csv_amazon(
    session: AsyncSession, loja: ConciliacaoLoja, texto: str, *, agora: datetime | None = None
) -> dict[str, Any]:
    agora = agora or datetime.now(UTC)
    _cab, rows = ler_csv_amazon(texto)
    txs = transacoes_do_csv(rows)
    movs = [m for m in (transacao_para_movimento(t) for t in txs) if m]
    integration = await session.get(Integration, loja.integration_id)
    lojas_bling = await bling_store_ids_da_loja(session, integration) if integration else set()
    ids = {m["pedido_marketplace"] for m in movs if m["pedido_marketplace"]}
    mapa = await resolver_pedidos_bling(session, ids, lojas_bling)
    for m in movs:
        achado = mapa.get(m["pedido_marketplace"] or "")
        if achado:
            m["pedido_bling"], m["bling_id"] = achado
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
    novos: list[dict[str, Any]] = []
    atualizados = 0
    por_tipo: dict[str, int] = {}
    for m in movs:
        por_tipo[m["tipo"]] = por_tipo.get(m["tipo"], 0) + 1
        ex = existentes.get(m["id_externo"])
        if ex is None:
            novos.append(m)
            continue
        mudou = False
        if ex.sub_tipo != m["sub_tipo"]:
            # preso na reserva → liberado: o arquivo não diz quando; fica a data do envio
            raw = dict(m["raw"])
            if m["sub_tipo"] == "RELEASED" and ex.sub_tipo == "DEFERRED":
                raw["_liberado_em"] = agora.isoformat()
            ex.sub_tipo = m["sub_tipo"]
            ex.descricao = m["descricao"]
            ex.raw = raw
            mudou = True
        if not ex.pedido_bling and m["pedido_bling"] and not ex.vinculado_por:
            ex.pedido_bling, ex.bling_id = m["pedido_bling"], m["bling_id"]
            mudou = True
        if mudou:
            atualizados += 1
    inseridos = await inserir_movimentos(session, loja, novos) if novos else 0
    saldo = await recalcular_saldo(session, loja)
    datas = [t["postedDate"][:10] for t in txs]
    stats = {
        "linhas": len(rows),
        "movimentos": len(movs),
        "inseridos": inseridos,
        "atualizados": atualizados,
        "ja_existiam": len(movs) - inseridos - atualizados,
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
