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
primeira rodada pede desde `conciliacao_lojas.inicio`. Janela pedida cujo arquivo
não vem em `PRAZO_RELATORIO` é marcada com erro e pedida de novo (16/10).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import structlog
from sqlalchemy import and_, case, func, literal_column, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

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
from app.services.conciliacao_dias import reabrir_dias
from app.services.conciliacao_mp_csv import (
    dinheiro,
    id_externo_da_linha,
    ler_csv_mp,
    numerar_repetidas,
)

logger = structlog.get_logger()

MP_API_BASE = "https://api.mercadopago.com"
BRT = ZoneInfo("America/Sao_Paulo")
# 16/10 (revisão): janela "pedido" sem prazo deixava a loja parada pra sempre quando o
# Mercado Pago não gerava o arquivo. Passado isto desde o pedido, a janela é pedida de novo.
PRAZO_RELATORIO = timedelta(hours=6)
# Campos de criação que a lista de relatórios do Mercado Pago pode trazer (o primeiro
# que existir e for data vale).
CAMPOS_CRIACAO = ("date_created", "created_at", "created", "generation_date", "last_modified")

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
    # saldo negativo depois da cobrança da fatura: o MP segura o que entra até cobrir
    "reserve_for_debt_payment": "cobranca_divida",
}
# "payment" com essa referência é o Mercado Livre cobrando a FATURA do saldo do MP
# (16/10, mega: débito 377,27 em 15/10 = `pago.total_payment` da fatura 17/09→09/10,
# que vence 15/10). Não é venda.
PREFIXO_COBRANCA_FATURA = "MELIPAYMENTS-COLLECTIONATTEMPT"
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


async def mp_put(cliente: Any, path: str, body: dict) -> httpx.Response:
    headers = await _bearer(cliente)
    async with httpx.AsyncClient(timeout=60.0) as c:
        return await c.put(f"{MP_API_BASE}{path}", headers=headers, json=body)


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


CONFIG_OK = (
    "existia",
    "existia_minima",
    "criada",
    "criada_minima",
    "atualizada",
    "atualizada_minima",
)
CONFIG_RELER = ("atualizada", "atualizada_minima")


def _colunas_da_config(body: Any) -> set[str]:
    if not isinstance(body, dict):
        return set()
    colunas = body.get("columns") or []
    return {str(c.get("key")) for c in colunas if isinstance(c, dict) and c.get("key")}


async def garantir_config(cliente: Any) -> dict[str, Any]:
    """Cria a configuração do relatório na conta se não existir (404) e COMPLETA as
    colunas quando a que existe tem menos que as nossas (16/10: a aguiar tinha uma
    config antiga de 11 colunas, sem ORDER_ID — 14 pagamentos "sem dono"). Quem
    chama relê os relatórios quando o status é um de `CONFIG_RELER`."""
    r = await mp_get(cliente, "/v1/account/release_report/config")
    if r.status_code == 200:
        tem = _colunas_da_config(_json(r))
        faltam = [c for c in COLUNAS if c not in tem]
        if not faltam:
            return {"status": "existia", "http": 200}
        faltam_minimo = [c for c in COLUNAS_MINIMO if c not in tem]
        u = await mp_put(cliente, "/v1/account/release_report/config", config_relatorio(COLUNAS))
        if u.status_code in (200, 201):
            return {"status": "atualizada", "http": u.status_code, "faltavam": faltam}
        if not faltam_minimo:
            # a conta recusa as colunas extras, mas o mínimo que lemos ela já tem
            return {"status": "existia_minima", "http": 200, "recusou": _json(u)}
        m = await mp_put(
            cliente, "/v1/account/release_report/config", config_relatorio(COLUNAS_MINIMO)
        )
        if m.status_code in (200, 201):
            return {
                "status": "atualizada_minima",
                "http": m.status_code,
                "faltavam": faltam_minimo,
                "recusou": _json(u),
            }
        return {"status": "falhou", "http": m.status_code, "body": _json(m), "recusou": _json(u)}
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


def liberacao_sem_nome(
    tipo: str,
    record_type: str | None,
    credito: Decimal | None,
    bruto: Decimal | None,
    pedido_marketplace: str | None,
) -> bool:
    """A liberação de uma venda com uma DESCRIPTION que o painel não conhece (19/10,
    marquezini 301744: o ML liberou 563,41 como `fee_for_divergence_in_package_dimensions`,
    com bruto 830,00, tarifa e frete preenchidos). Caía em "outro" e o pedido ficava
    "recebido a MAIS" (nada liberou e entrou 563,41) quando foi "a MENOS 36,15". Linha
    `release`, com crédito, bruto e nº da venda é liberação — a descrição fica guardada
    e aparece no motivo da diferença."""
    return (
        tipo == "outro"
        and (record_type or "").strip().lower() == "release"
        and bool(credito) and credito > 0
        and bool(bruto) and bruto > 0
        and bool(pedido_marketplace)
    )


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
    cobranca = _cobranca_da_fatura(descricao, row.get("EXTERNAL_REFERENCE"))
    tipo = "fatura_cobrada" if cobranca else tipo_do_movimento(descricao, record_type)
    if liberacao_sem_nome(
        tipo, record_type, credito, dinheiro(row.get("GROSS_AMOUNT")), _texto(row.get("ORDER_ID"))
    ):
        tipo = "venda_liberada"
        logger.warning(
            "conciliacao_extrato_liberacao_sem_nome",
            descricao=descricao,
            pedido=_texto(row.get("ORDER_ID")),
            credito=str(credito),
        )
    if descricao in ("payout", "reserve_for_payout"):
        classificacao = "saque"
    elif cobranca or tipo == "cobranca_divida":
        classificacao = "fatura_ml"
    else:
        classificacao = None
    return {
        "origem": ORIGEM_EXTRATO,
        "id_externo": id_externo_da_linha(row),
        "data": data,
        "tipo": tipo,
        "descricao": descricao or None,
        "sub_tipo": _texto(record_type, 120),
        "pagamento_id": _texto(row.get("SOURCE_ID"), 120),
        "pedido_marketplace": None
        if cobranca
        else (_texto(row.get("ORDER_ID"), 120) or _texto(row.get("EXTERNAL_REFERENCE"), 120)),
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
        "classificacao": classificacao,
        "raw": {k: v for k, v in row.items() if v},
    }


def _cobranca_da_fatura(descricao: str, referencia: Any) -> bool:
    return (descricao or "").strip().lower() == "payment" and str(
        referencia or ""
    ).upper().startswith(PREFIXO_COBRANCA_FATURA)


async def classificar_cobrancas_da_fatura(session: AsyncSession, loja: ConciliacaoLoja) -> int:
    """Linhas já gravadas antes da regra (16/10): cobrança da fatura (referência
    MELIPAYMENTS) vira `fatura_cobrada` sem nº de venda; `reserve_for_debt_payment`
    vira `cobranca_divida`. As duas ganham a classificação "Fatura do ML" quando
    ninguém mexeu nelas."""
    mov = ConciliacaoMovimento
    rows = (
        (
            await session.execute(
                select(mov).where(
                    mov.integration_id == loja.integration_id,
                    mov.origem == ORIGEM_EXTRATO,
                    or_(
                        mov.pedido_marketplace.like(f"{PREFIXO_COBRANCA_FATURA}%"),
                        and_(
                            mov.tipo == "reserva_outra", mov.descricao == "reserve_for_debt_payment"
                        ),
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    n = 0
    for m in rows:
        if (m.pedido_marketplace or "").upper().startswith(PREFIXO_COBRANCA_FATURA):
            m.pedido_marketplace = None
            m.tipo = "fatura_cobrada"
        else:
            m.tipo = "cobranca_divida"
        if not m.pedido_bling and not m.vinculado_por and not m.classificacao:
            m.classificacao = "fatura_ml"
            n += 1
    return n


TIPOS_VENDA_DESFEITA = {
    "venda_liberada",
    "mediacao",
    "mediacao_cancelada",
    "estorno",
    "chargeback",
    "disputa",
}
TIPOS_QUE_DESFAZEM = {"mediacao", "estorno", "chargeback", "disputa"}


async def classificar_vendas_desfeitas(session: AsyncSession, loja: ConciliacaoLoja) -> int:
    """Venda do ML que não existe no Bling e cujo dinheiro entrou e saiu inteiro no
    mesmo pagamento (liberação + mediação/estorno somam zero): nada ficou com a loja,
    nada a conferir. Ganha a classificação "venda_desfeita" (16/10, aguiar 02/10:
    +320,69 −396,17 +75,48). Se o pedido aparecer no Bling, a ligação desfaz."""
    mov = ConciliacaoMovimento
    rows = (
        (
            await session.execute(
                select(mov)
                .where(
                    mov.integration_id == loja.integration_id,
                    mov.origem == ORIGEM_EXTRATO,
                    mov.pedido_bling.is_(None),
                    mov.pagamento_id.isnot(None),
                    mov.vinculado_por.is_(None),
                    mov.classificacao.is_(None),
                    mov.tipo.in_(tuple(TIPOS_VENDA_DESFEITA)),
                )
                .limit(5000)
            )
        )
        .scalars()
        .all()
    )
    grupos: dict[str, list[ConciliacaoMovimento]] = {}
    for m in rows:
        grupos.setdefault(str(m.pagamento_id), []).append(m)
    n = 0
    for linhas in grupos.values():
        tipos = {m.tipo for m in linhas}
        if "venda_liberada" not in tipos or not (tipos & TIPOS_QUE_DESFAZEM):
            continue
        entrou = sum((m.credito or Decimal("0")) for m in linhas)
        saiu = sum((m.debito or Decimal("0")) for m in linhas)
        if entrou == 0 or entrou != saiu:
            continue
        for m in linhas:
            m.classificacao = "venda_desfeita"
            n += 1
    return n


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


def _campos_para_completar(stmt: Any) -> dict[str, Any]:
    """Linha repetida (mesma identidade) COMPLETA a gravada em vez de ser ignorada:
    o relatório novo pode trazer coluna que o antigo não tinha (16/10). O que uma
    pessoa fez (classificação, vínculo à mão) fica como está."""
    ex = stmt.excluded
    mov = ConciliacaoMovimento
    coalesce = {
        c: func.coalesce(getattr(ex, c), getattr(mov, c))
        for c in (
            "descricao",
            "sub_tipo",
            "pagamento_id",
            "pedido_marketplace",
            "pack_id",
            "shipping_id",
            "sku",
            "item_id",
            "data_aprovacao",
            "saldo_apos",
        )
    }
    # valor novo manda quando não é zero; zero no novo não apaga o que já tinha
    valores = {
        c: func.coalesce(func.nullif(getattr(ex, c), 0), getattr(mov, c), getattr(ex, c))
        for c in ("bruto", "tarifa", "parcelamento", "frete", "cupom")
    }
    pedido = {
        c: case(
            (mov.vinculado_por.is_(None), func.coalesce(getattr(ex, c), getattr(mov, c))),
            else_=getattr(mov, c),
        )
        for c in ("pedido_bling", "bling_id")
    }
    return {
        **coalesce,
        **valores,
        **pedido,
        "raw": mov.raw.concat(ex.raw),
        "updated_at": func.now(),
    }


def _chave_natural(
    data: datetime, pagamento_id: str | None, descricao: str | None, credito: Any, debito: Any
) -> tuple:
    """Hora, pagamento, descrição e valores. Sem o RECORD_TYPE: o relatório pobre da
    aguiar não tinha essa coluna (sub_tipo vazio) e a gêmea completa não casava."""
    q = Decimal("0.01")
    return (
        data.astimezone(UTC),
        pagamento_id or "",
        descricao or "",
        str((Decimal(str(credito or 0))).quantize(q)),
        str((Decimal(str(debito or 0))).quantize(q)),
    )


async def _reidentificar_linhas_pobres(
    session: AsyncSession, loja: ConciliacaoLoja, movimentos: list[dict[str, Any]]
) -> tuple[int, int]:
    """A identidade da linha (hash) inclui ORDER_ID, RECORD_TYPE, GROSS e BALANCE. Linha
    gravada por um relatório SEM alguma dessas colunas (config antiga, 16/10 aguiar) tem
    outra identidade; quando o relatório completo chega, a linha antiga que o relatório
    novo não traz com a mesma identidade é casada pela chave natural (hora, pagamento,
    descrição, valores) e troca de identidade — aí o upsert completa os campos em vez
    de duplicar. Devolve (trocadas, colisões): colisão é quando a identidade nova JÁ
    está gravada (a gêmea completa entrou antes) — trocar daria UniqueViolation e
    derrubava a leitura inteira (16/10, revisão); a pobre fica e
    `apagar_duplicatas_pobres` a tira."""
    novos = movimentos
    if not novos:
        return 0, 0
    ids_novos = {m["id_externo"] for m in movimentos}
    datas = [m["data"] for m in novos]
    antigas = (
        (
            await session.execute(
                select(ConciliacaoMovimento).where(
                    ConciliacaoMovimento.integration_id == loja.integration_id,
                    ConciliacaoMovimento.origem == ORIGEM_EXTRATO,
                    ConciliacaoMovimento.data >= min(datas),
                    ConciliacaoMovimento.data <= max(datas),
                    ConciliacaoMovimento.id_externo.notin_(ids_novos),
                )
            )
        )
        .scalars()
        .all()
    )
    if not antigas:
        return 0, 0
    ja_gravadas = set(
        (
            await session.execute(
                select(ConciliacaoMovimento.id_externo).where(
                    ConciliacaoMovimento.integration_id == loja.integration_id,
                    ConciliacaoMovimento.origem == ORIGEM_EXTRATO,
                    ConciliacaoMovimento.id_externo.in_(ids_novos),
                )
            )
        )
        .scalars()
        .all()
    )
    # lista por chave: duas linhas idênticas do arquivo pobre (#2) casam cada uma com a sua
    por_chave: dict[tuple, list[ConciliacaoMovimento]] = {}
    for a in antigas:
        chave = _chave_natural(a.data, a.pagamento_id, a.descricao, a.credito, a.debito)
        por_chave.setdefault(chave, []).append(a)
    trocadas = colisoes = 0
    for m in novos:
        chave = _chave_natural(
            m["data"], m["pagamento_id"], m["descricao"], m["credito"], m["debito"]
        )
        fila = por_chave.get(chave)
        if not fila:
            continue
        if m["id_externo"] in ja_gravadas:
            colisoes += 1
            continue
        antiga = fila.pop(0)
        antiga.id_externo = m["id_externo"]
        ja_gravadas.add(m["id_externo"])
        trocadas += 1
    if trocadas:
        await session.flush()
    return trocadas, colisoes


async def apagar_duplicatas_pobres(session: AsyncSession, loja: ConciliacaoLoja) -> int:
    """Linha do relatório pobre que tem uma gêmea mais completa — mesma hora, pagamento,
    descrição e valores, e a gêmea tem o nº da venda ou o RECORD_TYPE que a pobre não
    tem — é a MESMA linha do Mercado Pago gravada duas vezes (16/10, aguiar: a gêmea
    não casou na releitura e a regra da irmã do pagamento ligou as duas ao pedido →
    recebido dobrado; os saques também dobraram). Apaga a pobre. A pobre que uma
    pessoa vinculou à mão também some (16/10, revisão: ficar com as duas era recebido
    em dobro) — o vínculo (pedido, quem, quando, classificação) passa pra gêmea
    completa quando ela não tem."""
    pobre = aliased(ConciliacaoMovimento)
    cheia = aliased(ConciliacaoMovimento)
    pares = (
        await session.execute(
            select(pobre, cheia)
            .join(
                cheia,
                and_(
                    cheia.id != pobre.id,
                    cheia.integration_id == pobre.integration_id,
                    cheia.origem == ORIGEM_EXTRATO,
                    cheia.pagamento_id == pobre.pagamento_id,
                    cheia.data == pobre.data,
                    func.coalesce(cheia.descricao, "") == func.coalesce(pobre.descricao, ""),
                    cheia.credito == pobre.credito,
                    cheia.debito == pobre.debito,
                    or_(
                        and_(
                            cheia.pedido_marketplace.isnot(None),
                            pobre.pedido_marketplace.is_(None),
                        ),
                        and_(cheia.sub_tipo.isnot(None), pobre.sub_tipo.is_(None)),
                    ),
                ),
            )
            .where(
                pobre.integration_id == loja.integration_id,
                pobre.origem == ORIGEM_EXTRATO,
                pobre.pagamento_id.isnot(None),
                or_(pobre.pedido_marketplace.is_(None), pobre.sub_tipo.is_(None)),
            )
            .order_by(pobre.created_at, pobre.id, cheia.created_at, cheia.id)
        )
    ).all()
    apagadas: set[Any] = set()
    dias_mexidos: set[date] = set()
    for p, c in pares:
        # a gêmea pode ter sido apagada como pobre de outro par: não some com as duas
        if p.id in apagadas or c.id in apagadas:
            continue
        if p.data is not None:
            dias_mexidos.add(p.data.astimezone(BRT).date())
        if p.vinculado_por and not c.vinculado_por:
            if p.pedido_bling is None and p.classificacao is None:
                # a pessoa DESVINCULOU a pobre: a gêmea fica sem dono também (2ª rodada)
                c.pedido_bling, c.bling_id = None, None
                c.vinculado_por, c.vinculado_em = p.vinculado_por, p.vinculado_em
            else:
                if c.pedido_bling is None:
                    c.pedido_bling, c.bling_id = p.pedido_bling, p.bling_id
                if c.pedido_bling == p.pedido_bling:
                    c.vinculado_por, c.vinculado_em = p.vinculado_por, p.vinculado_em
                # classificação só quando a gêmea não tem pedido (não pode ter os dois)
                if c.classificacao is None and p.classificacao and c.pedido_bling is None:
                    c.classificacao = p.classificacao
        await session.delete(p)
        apagadas.add(p.id)
    if apagadas:
        await session.flush()
        # 16/10 (revisão, aprovado): o saldo do dia mudou — dia fechado reabre
        await reabrir_dias(
            session, loja, dias_mexidos, motivo="linha duplicada do relatório apagada"
        )
    return len(apagadas)


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
    repetidas = numerar_repetidas(movimentos)
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
    reidentificados, colisoes = await _reidentificar_linhas_pobres(session, loja, movimentos)
    inseridos = atualizados = 0
    datas_novas: set[date] = set()
    for k in range(0, len(movimentos), 200):
        lote = movimentos[k : k + 200]
        stmt = pg_insert(ConciliacaoMovimento).values(lote)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_conciliacao_mov_externo", set_=_campos_para_completar(stmt)
        ).returning(
            ConciliacaoMovimento.id, literal_column("(xmax = 0)"), ConciliacaoMovimento.data
        )
        for _id, novo, data in (await session.execute(stmt)).all():
            if novo:
                inseridos += 1
                if data is not None:
                    datas_novas.add(data.astimezone(BRT).date())
            else:
                atualizados += 1
    await session.flush()
    if datas_novas:
        # 16/10 (revisão, aprovado): linha nova (folga de 1 dia, relatório atrasado) em dia
        # fechado reabre o dia pra conferência
        await reabrir_dias(
            session, loja, datas_novas, motivo="linha nova no relatório do Mercado Pago"
        )
    duplicatas = (
        await apagar_duplicatas_pobres(session, loja) if (reidentificados or colisoes) else 0
    )
    return {
        "linhas": len(rows),
        "movimentos": len(movimentos),
        "repetidas_no_arquivo": repetidas,
        "inseridos": inseridos,
        "ja_existiam": len(movimentos) - inseridos,
        "atualizados": atualizados,
        "reidentificados": reidentificados,
        "colisoes_de_identidade": colisoes,
        "duplicatas_apagadas": duplicatas,
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


def _criado_em(item: dict[str, Any]) -> datetime | None:
    """Quando o Mercado Pago criou o relatório: o primeiro campo de `CAMPOS_CRIACAO`
    que existir e for uma data."""
    for campo in CAMPOS_CRIACAO:
        v = item.get(campo)
        d = _dt(v) if isinstance(v, str) else None
        if d is not None:
            return d
    return None


def _acha_relatorio(
    lista: list[dict[str, Any]], janela: dict[str, str], *, desde: datetime | None = None
) -> dict[str, Any] | None:
    """O relatório da janela, o mais novo. Com `desde` (o `pedido_em` da janela) só vale
    o criado DEPOIS do pedido, com 5 min de folga pro relógio: na releitura da mesma
    janela a lista traz também o relatório antigo (o pobre, sem ORDER_ID) e era ele
    que podia ser pego (16/10, revisão). Item sem data de criação conhecida passa."""
    candidatos = [
        x
        for x in lista
        if x.get("file_name")
        and str(x.get("begin_date", "")).startswith(janela["begin_date"][:19])
        and str(x.get("end_date", "")).startswith(janela["end_date"][:19])
    ]
    if desde is not None:
        limite = desde - timedelta(minutes=5)
        candidatos = [x for x in candidatos if (c := _criado_em(x)) is None or c >= limite]
    if not candidatos:
        return None
    nunca = datetime.min.replace(tzinfo=UTC)
    candidatos.sort(key=lambda x: (_criado_em(x) or nunca, str(x.get("id") or "")), reverse=True)
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
    resumo: dict[str, Any] = {
        "loja": loja.conta,
        "lidos": [],
        "pedido": None,
        "repedidos": [],
        "erros": [],
    }

    async def _vencida(r: ConciliacaoSync, motivo: str) -> None:
        """Janela "pedido" cujo relatório não veio em `PRAZO_RELATORIO` (16/10, revisão:
        sem prazo a loja ficava parada pra sempre): marca o erro na janela e na loja e
        pede a MESMA janela de novo (novo `pedido_em`; o relatório velho, se aparecer,
        não vale mais)."""
        horas = int(PRAZO_RELATORIO.total_seconds() // 3600)
        de, ate = _janela_da_chave(r.chave)
        res = await pedir_relatorio(cliente, de, ate)
        pedidos = int((r.detalhe or {}).get("pedidos") or 1) + 1
        erro = f"{motivo} em {horas} h"
        if res["ok"]:
            r.pedido_em = agora
            r.detalhe = {
                **(r.detalhe or {}),
                "janela": res["janela"],
                "resposta": res["body"],
                "pedidos": pedidos,
                "repedido_em": agora.isoformat(),
            }
            erro += f"; pedido de novo ({pedidos}º pedido)"
        else:
            erro += f"; pedir de novo falhou: HTTP {res['status']}"
        r.erro = erro
        loja.ultimo_erro = f"extrato {r.chave}: {erro}"
        resumo["erros"].append({"janela": r.chave, "erro": erro})
        resumo["repedidos"].append({"janela": r.chave, "http": res["status"], "pedidos": pedidos})
        logger.warning("conciliacao_extrato_janela_vencida", loja=loja.conta, janela=r.chave)

    pendentes = [r for r in rows if r.status == "pedido"]
    if pendentes:
        lista = await listar_relatorios(cliente)
        for r in pendentes:
            janela = (r.detalhe or {}).get("janela") or janela_utc(*_janela_da_chave(r.chave))
            vencida = r.pedido_em is not None and agora - r.pedido_em > PRAZO_RELATORIO
            item = _acha_relatorio(lista, janela, desde=r.pedido_em)
            if not item:
                if vencida:
                    await _vencida(r, "relatório não veio")
                continue
            texto = await baixar_relatorio(cliente, str(item["file_name"]))
            if texto is None:
                if vencida:
                    await _vencida(r, "relatório não ficou pronto")
                continue
            try:
                # savepoint (16/10, revisão): um erro de banco na ingestão deixava a
                # transação quebrada — nem o erro gravava e a loja travava sem rastro
                async with session.begin_nested():
                    stats = await ingerir_csv(session, loja, texto)
            except Exception as e:  # noqa: BLE001
                r.erro = f"{type(e).__name__}: {str(e)[:300]}"
                resumo["erros"].append({"janela": r.chave, "erro": r.erro})
                loja.ultimo_erro = f"extrato {r.chave}: {r.erro[:200]}"
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
        # Confere a config do relatório 1×/dia (16/10: a aguiar tinha uma config antiga
        # de 11 colunas, sem ORDER_ID): se faltava coluna e foi completada, os
        # relatórios já lidos vieram capengas — esquece as janelas e relê desde o início.
        confere = (
            loja.extrato_config_em is None or loja.extrato_config_em.astimezone(BRT).date() < hoje
        )
        if confere:
            cfg = await garantir_config(cliente)
            resumo["config"] = cfg
            if cfg["status"] in CONFIG_OK:
                loja.extrato_config_em = agora
            if cfg["status"] in CONFIG_RELER:
                for r in rows:
                    await session.delete(r)
                rows = []
                await session.flush()
                resumo["reler_desde"] = loja.inicio.isoformat()
        fins = [_janela_da_chave(r.chave)[1] for r in rows if r.status in ("pedido", "lido")]
        # Folga de um dia (Vinicius, 14/10: "o ML pode mudar algo de ontem?"): a
        # janela nova começa no ÚLTIMO dia já lido, não no seguinte. Linha repetida é
        # ignorada (identidade = hash), linha atrasada do Mercado Pago entra.
        proximo = max(fins) if fins else loja.inicio
        if fins and proximo >= ontem and max(fins) >= ontem:
            proximo = ontem + timedelta(days=1)  # ontem já lido: nada a pedir
        if proximo <= ontem:
            ate = min(ontem, proximo + timedelta(days=max_dias - 1))
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
