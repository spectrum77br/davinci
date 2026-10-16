"""Conciliação — extrato do Mercado Pago (Relatório de liberações) por loja.

Como funciona (sondagem de 14/10/2026, `docs/conciliacao-avaliacao.md` §13):
a chave do ML da loja abre `api.mercadopago.com`; o relatório é pedido por
janela de datas (POST), fica pronto em minutos e é baixado como CSV. Cada
linha vira um `conciliacao_movimentos` com o tipo normalizado, o nº do
pagamento, o nº da venda/pack e, quando o Bling já importou, o pedido do
Bling. Saques (`payout`) e as reservas antes do saque são os únicos
movimentos sem pedido — e nem deveriam ter.

Rotina (`run_extrato_sync`): uma janela por vez. Se há janela pedida e ainda
não lida, procura o arquivo na lista e lê; senão pede a próxima janela
(do dia seguinte ao último lido até ontem, no máximo `max_dias` dias). A
primeira rodada pede desde `conciliacao_lojas.inicio`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import structlog
from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingOrder,
    ConciliacaoLoja,
    ConciliacaoMovimento,
    ConciliacaoSync,
    Integration,
    Store,
    StoreInfo,
)
from app.models.conciliacao import ORIGEM_EXTRATO
from app.services.conciliacao_mp_csv import dinheiro, id_externo_da_linha, ler_csv_mp

logger = structlog.get_logger()

MP_API_BASE = "https://api.mercadopago.com"
BRT = ZoneInfo("America/Sao_Paulo")

COLUNAS = [
    "DATE",
    "SOURCE_ID",
    "EXTERNAL_REFERENCE",
    "RECORD_TYPE",
    "DESCRIPTION",
    "NET_CREDIT_AMOUNT",
    "NET_DEBIT_AMOUNT",
    "GROSS_AMOUNT",
    "MP_FEE_AMOUNT",
    "FINANCING_FEE_AMOUNT",
    "SHIPPING_FEE_AMOUNT",
    "TAXES_AMOUNT",
    "COUPON_AMOUNT",
    "EFFECTIVE_COUPON_AMOUNT",
    "INSTALLMENTS",
    "PAYMENT_METHOD",
    "PAYMENT_METHOD_TYPE",
    "TRANSACTION_APPROVAL_DATE",
    "TRANSACTION_DATE",
    "ORDER_ID",
    "SHIPPING_ID",
    "SHIPMENT_MODE",
    "PACK_ID",
    "ITEM_ID",
    "PRODUCT_SKU",
    "SALE_DETAIL",
    "BALANCE_AMOUNT",
    "METADATA",
    "OPERATION_TAGS",
    "SELLER_AMOUNT",
    "TAX_DETAIL",
    "PAYOUT_BANK_ACCOUNT_NUMBER",
    "CURRENCY",
    "PURCHASE_ID",
    "ORDER_MP",
    "SHIPPING_ORDER_ID",
]
COLUNAS_MINIMO = COLUNAS[:13] + [
    "INSTALLMENTS",
    "PAYMENT_METHOD",
    "ORDER_ID",
    "SHIPPING_ID",
    "PACK_ID",
    "BALANCE_AMOUNT",
    "METADATA",
]

# DESCRIPTION do relatório → tipo normalizado (o que a tela mostra).
TIPOS = {
    "payment": "venda_liberada",
    "mediation": "mediacao",
    "mediation_cancel": "mediacao_cancelada",
    "refund": "estorno",
    "chargeback": "chargeback",
    "dispute": "disputa",
    "reserve_for_dispute": "reserva_disputa",
    "reserve_for_refund": "reserva_estorno",
    "reserve_for_bpp_shipping_return": "reserva_frete_devolucao",
    "reserve_for_payout": "reserva_saque",
    "reserve_for_payment": "reserva_pagamento",
    "payout": "saque",
    "shipping": "frete",
    "shipping_cancel": "frete_cancelado",
    "shipping_return": "frete_devolucao",
    "shipping_refund": "frete_devolucao",
    "fee_release_in_advance": "antecipacao",
    "credit_payment": "credito",
}
TIPOS_RESERVA = {
    "reserva_disputa",
    "reserva_estorno",
    "reserva_frete_devolucao",
    "reserva_saque",
    "reserva_pagamento",
}
REGISTROS_IGNORADOS = {"initial_available_balance", "total", "available_balance"}


def config_relatorio(colunas: list[str]) -> dict[str, Any]:
    return {
        "file_name_prefix": "davinci-liberacoes",
        "include_withdrawal_at_end": False,
        "execute_after_withdrawal": False,
        "display_timezone": "GMT-03",
        "frequency": {"hour": 0, "type": "monthly", "value": 1},
        "columns": [{"key": c} for c in colunas],
    }


async def _bearer(cliente: Any) -> dict[str, str]:
    if cliente._expired():
        await cliente.refresh()
    return {"Authorization": f"Bearer {cliente.access_token}", "Accept": "application/json"}


async def mp_get(cliente: Any, path: str, params: dict | None = None) -> httpx.Response:
    headers = await _bearer(cliente)
    async with httpx.AsyncClient(timeout=60.0) as c:
        return await c.get(f"{MP_API_BASE}{path}", headers=headers, params=params)


async def mp_post(cliente: Any, path: str, body: dict) -> httpx.Response:
    headers = await _bearer(cliente)
    async with httpx.AsyncClient(timeout=60.0) as c:
        return await c.post(f"{MP_API_BASE}{path}", headers=headers, json=body)


def janela_utc(de: date, ate: date) -> dict[str, str]:
    """00:00 do primeiro dia e 23:59:59 do último, em BRT, escritos em UTC."""
    inicio = datetime(de.year, de.month, de.day, 3, 0, 0, tzinfo=UTC)
    fim = datetime(ate.year, ate.month, ate.day, 2, 59, 59, tzinfo=UTC) + timedelta(days=1)
    return {
        "begin_date": inicio.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end_date": fim.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _json(r: httpx.Response) -> Any:
    try:
        return r.json()
    except ValueError:
        return (r.text or "")[:500]


async def garantir_config(cliente: Any) -> dict[str, Any]:
    """Cria a configuração do relatório na conta se não existir (404)."""
    r = await mp_get(cliente, "/v1/account/release_report/config")
    if r.status_code == 200:
        return {"status": "existia", "http": 200}
    if r.status_code != 404:
        return {"status": "falhou", "http": r.status_code, "body": _json(r)}
    c = await mp_post(cliente, "/v1/account/release_report/config", config_relatorio(COLUNAS))
    if c.status_code in (200, 201):
        return {"status": "criada", "http": c.status_code}
    m = await mp_post(
        cliente, "/v1/account/release_report/config", config_relatorio(COLUNAS_MINIMO)
    )
    if m.status_code in (200, 201):
        return {"status": "criada_minima", "http": m.status_code, "recusou": _json(c)}
    return {"status": "falhou", "http": m.status_code, "body": _json(m), "recusou": _json(c)}


async def pedir_relatorio(cliente: Any, de: date, ate: date) -> dict[str, Any]:
    janela = janela_utc(de, ate)
    r = await mp_post(cliente, "/v1/account/release_report", janela)
    return {
        "status": r.status_code,
        "ok": r.status_code in (200, 201, 202),
        "body": _json(r),
        "janela": janela,
    }


async def listar_relatorios(cliente: Any) -> list[dict[str, Any]]:
    r = await mp_get(cliente, "/v1/account/release_report/list")
    if r.status_code != 200:
        return []
    body = _json(r)
    return [x for x in body if isinstance(x, dict)] if isinstance(body, list) else []


async def baixar_relatorio(cliente: Any, file_name: str) -> str | None:
    r = await mp_get(cliente, f"/v1/account/release_report/{file_name}")
    if r.status_code != 200 or "json" in r.headers.get("content-type", ""):
        return None
    return r.text


def tipo_do_movimento(descricao: str, record_type: str) -> str:
    d = (descricao or "").strip().lower()
    if d in TIPOS:
        return TIPOS[d]
    if d.startswith("reserve_for"):
        return "reserva_outra"
    if d.startswith("tax_"):
        return "imposto"
    if d.startswith("shipping"):
        return "frete"
    if not d and record_type in REGISTROS_IGNORADOS:
        return record_type
    return "outro"


def _dt(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(v.strip())
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=BRT)


def _texto(v: str | None, maximo: int | None = None) -> str | None:
    """Texto limpo; `maximo` corta no tamanho da coluna (16/10: EXTERNAL_REFERENCE e
    PRODUCT_SKU de algumas contas passavam de 40/120 e a ingestão inteira falhava —
    o valor completo continua no `raw`)."""
    t = (v or "").strip()
    if maximo and len(t) > maximo:
        t = t[:maximo]
    return t or None


def cupom_da_linha(row: dict[str, Any]) -> Decimal:
    """O cupom que sai do bolso da LOJA: EFFECTIVE_COUPON_AMOUNT. O COUPON_AMOUNT
    soma também o cupom bancado pelo ML, que não mexe no líquido da loja
    (300415, 15/10: COUPON 174,46 × EFFECTIVE −16,00; o líquido só desconta os 16)."""
    efetivo = _texto(row.get("EFFECTIVE_COUPON_AMOUNT"))
    v = dinheiro(efetivo) if efetivo else dinheiro(row.get("COUPON_AMOUNT"))
    return v.copy_abs()


def linha_para_movimento(row: dict[str, str]) -> dict[str, Any] | None:
    """Uma linha do CSV → colunas de `conciliacao_movimentos` (sem a loja).
    Devolve None pras linhas de saldo inicial/total (não são movimento)."""
    record_type = (row.get("RECORD_TYPE") or "").strip()
    descricao = (row.get("DESCRIPTION") or "").strip()
    if record_type in REGISTROS_IGNORADOS or (not descricao and record_type != "release"):
        return None
    data = _dt(row.get("DATE"))
    if data is None:
        return None
    credito = dinheiro(row.get("NET_CREDIT_AMOUNT"))
    debito = dinheiro(row.get("NET_DEBIT_AMOUNT"))
    return {
        "origem": ORIGEM_EXTRATO,
        "id_externo": id_externo_da_linha(row),
        "data": data,
        "tipo": tipo_do_movimento(descricao, record_type),
        "descricao": descricao or None,
        "sub_tipo": _texto(record_type, 120),
        "pagamento_id": _texto(row.get("SOURCE_ID"), 120),
        "pedido_marketplace": _texto(row.get("ORDER_ID"), 120)
        or _texto(row.get("EXTERNAL_REFERENCE"), 120),
        "pack_id": _texto(row.get("PACK_ID"), 120),
        "shipping_id": _texto(row.get("SHIPPING_ID"), 120),
        "pedido_bling": None,
        "bling_id": None,
        "bruto": dinheiro(row.get("GROSS_AMOUNT")),
        "tarifa": dinheiro(row.get("MP_FEE_AMOUNT")),
        "parcelamento": dinheiro(row.get("FINANCING_FEE_AMOUNT")),
        "frete": dinheiro(row.get("SHIPPING_FEE_AMOUNT")),
        "cupom": cupom_da_linha(row),
        "credito": credito,
        "debito": debito,
        "saldo_apos": dinheiro(row.get("BALANCE_AMOUNT"))
        if _texto(row.get("BALANCE_AMOUNT"))
        else None,
        "sku": _texto(row.get("PRODUCT_SKU"), 500),
        "item_id": _texto(row.get("ITEM_ID"), 120),
        "data_aprovacao": _dt(row.get("TRANSACTION_APPROVAL_DATE")),
        "classificacao": "saque" if descricao in ("payout", "reserve_for_payout") else None,
        "raw": {k: v for k, v in row.items() if v},
    }


async def bling_store_ids_da_loja(session: AsyncSession, integration: Integration) -> set[str]:
    """Ids de loja do Bling (`bling_orders.loja`) que pertencem a esta conta:
    pela tela Lojas (`store_info.account_name`) e pela `stores.integration_id`."""
    ids: set[str] = set()
    nome = (integration.name or "").strip().lower()
    # Pelo nome só na MESMA plataforma: a Shopee também tem uma loja "Mini" e os
    # pedidos dela entravam no painel da TikTok Mini (15/10). Apelidos do ML como na
    # tela Lojas (`routers/stores._plataformas`).
    plataforma = getattr(integration.platform, "value", str(integration.platform or "")).lower()
    apelidos = ("ml", "mercadolivre", "mercado livre") if plataforma == "ml" else (plataforma,)
    if nome:
        rows = (
            await session.execute(
                select(StoreInfo.bling_store_id).where(
                    StoreInfo.bling_store_id.isnot(None),
                    StoreInfo.archived_at.is_(None),
                    or_(
                        StoreInfo.integration_id == integration.id,
                        and_(
                            StoreInfo.account_name.ilike(nome),
                            func.lower(func.trim(StoreInfo.platform)).in_(apelidos),
                        ),
                    ),
                )
            )
        ).scalars()
        ids.update(str(x) for x in rows if x)
    rows2 = (
        await session.execute(
            select(Store.bling_store_id).where(Store.integration_id == integration.id)
        )
    ).scalars()
    ids.update(str(x) for x in rows2 if x)
    return ids


async def resolver_pedidos_bling(
    session: AsyncSession, ids: set[str], lojas: set[str]
) -> dict[str, tuple[str, int | None]]:
    """nº da venda/pack do ML → (pedido do Bling, bling_id). Prefere a loja certa."""
    ids = {i for i in ids if i}
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(
                BlingOrder.numeroloja, BlingOrder.numero, BlingOrder.bling_id, BlingOrder.loja
            ).where(BlingOrder.numeroloja.in_(ids))
        )
    ).all()
    out: dict[str, tuple[str, int | None]] = {}
    for numeroloja, numero, bling_id, loja in rows:
        if not numeroloja or not numero:
            continue
        chave = str(numeroloja)
        certa = str(loja) in lojas if lojas else True
        if chave not in out or certa:
            out[chave] = (str(numero), bling_id)
    return out


async def ingerir_csv(session: AsyncSession, loja: ConciliacaoLoja, texto: str) -> dict[str, Any]:
    cab, rows, _sep = ler_csv_mp(texto)
    integration = await session.get(Integration, loja.integration_id)
    lojas = await bling_store_ids_da_loja(session, integration) if integration else set()
    movimentos: list[dict[str, Any]] = []
    saldo_inicial = saldo_final = None
    for row in rows:
        rt = (row.get("RECORD_TYPE") or "").strip()
        if rt == "initial_available_balance":
            saldo_inicial = str(dinheiro(row.get("NET_CREDIT_AMOUNT") or row.get("BALANCE_AMOUNT")))
            continue
        if rt == "total":
            saldo_final = str(dinheiro(row.get("NET_CREDIT_AMOUNT")))
            continue
        m = linha_para_movimento(row)
        if m:
            movimentos.append(m)
    ids = {m["pedido_marketplace"] for m in movimentos if m["pedido_marketplace"]} | {
        m["pack_id"] for m in movimentos if m["pack_id"]
    }
    mapa = await resolver_pedidos_bling(session, ids, lojas)
    com_pedido = sem_pedido = 0
    for m in movimentos:
        achado = mapa.get(m["pedido_marketplace"] or "") or mapa.get(m["pack_id"] or "")
        if achado:
            m["pedido_bling"], m["bling_id"] = achado
        if m["pedido_marketplace"] or m["pack_id"]:
            com_pedido += 1
        else:
            sem_pedido += 1
        m["integration_id"] = loja.integration_id
        m["platform"] = loja.platform
    inseridos = 0
    for k in range(0, len(movimentos), 200):
        lote = movimentos[k : k + 200]
        stmt = (
            pg_insert(ConciliacaoMovimento)
            .values(lote)
            .on_conflict_do_nothing(constraint="uq_conciliacao_mov_externo")
            .returning(ConciliacaoMovimento.id)
        )
        inseridos += len((await session.execute(stmt)).all())
    await session.flush()
    return {
        "linhas": len(rows),
        "movimentos": len(movimentos),
        "inseridos": inseridos,
        "ja_existiam": len(movimentos) - inseridos,
        "com_pedido": com_pedido,
        "sem_pedido": sem_pedido,
        "pedido_bling_resolvido": sum(1 for m in movimentos if m.get("pedido_bling")),
        "saldo_inicial": saldo_inicial,
        "saldo_final": saldo_final,
        "colunas": len(cab),
    }


def _chave_janela(de: date, ate: date) -> str:
    return f"{de.isoformat()}..{ate.isoformat()}"


def _janela_da_chave(chave: str) -> tuple[date, date]:
    a, b = chave.split("..")
    return date.fromisoformat(a), date.fromisoformat(b)


def _acha_relatorio(lista: list[dict[str, Any]], janela: dict[str, str]) -> dict[str, Any] | None:
    candidatos = [
        x
        for x in lista
        if x.get("file_name")
        and str(x.get("begin_date", "")).startswith(janela["begin_date"][:19])
        and str(x.get("end_date", "")).startswith(janela["end_date"][:19])
    ]
    if not candidatos:
        return None
    candidatos.sort(key=lambda x: str(x.get("created_at") or x.get("id") or ""), reverse=True)
    return candidatos[0]


async def run_extrato_sync(
    session: AsyncSession,
    loja: ConciliacaoLoja,
    cliente: Any,
    *,
    hoje: date | None = None,
    max_dias: int = 14,
) -> dict[str, Any]:
    agora = datetime.now(UTC)
    hoje = hoje or agora.astimezone(BRT).date()
    ontem = hoje - timedelta(days=1)
    rows = (
        (
            await session.execute(
                select(ConciliacaoSync).where(
                    ConciliacaoSync.integration_id == loja.integration_id,
                    ConciliacaoSync.origem == ORIGEM_EXTRATO,
                )
            )
        )
        .scalars()
        .all()
    )
    resumo: dict[str, Any] = {"loja": loja.conta, "lidos": [], "pedido": None, "erros": []}

    pendentes = [r for r in rows if r.status == "pedido"]
    if pendentes:
        lista = await listar_relatorios(cliente)
        for r in pendentes:
            janela = (r.detalhe or {}).get("janela") or janela_utc(*_janela_da_chave(r.chave))
            item = _acha_relatorio(lista, janela)
            if not item:
                continue
            texto = await baixar_relatorio(cliente, str(item["file_name"]))
            if texto is None:
                continue
            try:
                stats = await ingerir_csv(session, loja, texto)
            except Exception as e:  # noqa: BLE001
                r.erro = f"{type(e).__name__}: {str(e)[:300]}"
                resumo["erros"].append({"janela": r.chave, "erro": r.erro})
                logger.exception(
                    "conciliacao_extrato_ingestao_falhou", loja=loja.conta, janela=r.chave
                )
                continue
            r.status = "lido"
            r.arquivo = str(item["file_name"])
            r.lido_em = agora
            r.linhas = stats["linhas"]
            r.erro = None
            r.detalhe = {**(r.detalhe or {}), "leitura": stats}
            loja.ultimo_extrato_em = agora
            resumo["lidos"].append({"janela": r.chave, **stats})

    ainda_pendente = any(r.status == "pedido" for r in rows)
    if not ainda_pendente:
        fins = [_janela_da_chave(r.chave)[1] for r in rows if r.status in ("pedido", "lido")]
        # Folga de um dia (Vinicius, 14/10: "o ML pode mudar algo de ontem?"): a
        # janela nova começa no ÚLTIMO dia já lido, não no seguinte. Linha repetida é
        # ignorada (identidade = hash), linha atrasada do Mercado Pago entra.
        proximo = max(fins) if fins else loja.inicio
        if fins and proximo >= ontem and max(fins) >= ontem:
            proximo = ontem + timedelta(days=1)  # ontem já lido: nada a pedir
        if proximo <= ontem:
            ate = min(ontem, proximo + timedelta(days=max_dias - 1))
            if not loja.extrato_config_em:
                cfg = await garantir_config(cliente)
                resumo["config"] = cfg
                if cfg["status"] in ("existia", "criada", "criada_minima"):
                    loja.extrato_config_em = agora
            res = await pedir_relatorio(cliente, proximo, ate)
            chave = _chave_janela(proximo, ate)
            if res["ok"]:
                session.add(
                    ConciliacaoSync(
                        integration_id=loja.integration_id,
                        origem=ORIGEM_EXTRATO,
                        chave=chave,
                        status="pedido",
                        pedido_em=agora,
                        detalhe={"janela": res["janela"], "resposta": res["body"]},
                    )
                )
                resumo["pedido"] = {"janela": chave, "http": res["status"]}
            else:
                resumo["erros"].append(
                    {"janela": chave, "http": res["status"], "body": res["body"]}
                )
                loja.ultimo_erro = f"extrato {chave}: HTTP {res['status']}"
    await session.flush()
    return resumo
