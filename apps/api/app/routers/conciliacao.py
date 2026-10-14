"""Conciliação — passo 1 (14/10/2026): sondagem só-leitura das fontes do
Mercado Livre / Mercado Pago pra uma loja.

`GET /api/conciliacao/sonda?conta=jlas2` responde, numa chamada só e sem
gravar nada, se a chave de acesso que o DaVinci já tem da loja:

* abre a **porta do extrato** no Mercado Pago (`/v1/account/release_report`
  — o "Relatório de liberações"), e o que já existe lá (configuração,
  arquivos gerados, uma amostra das linhas do último arquivo pronto);
* lê a **fatura do ML por período** (`/billing/integration/periods/key/…`:
  períodos, resumo, detalhes do mês atual e do anterior, pagamentos da
  fatura, movimentações do grupo MP).

Cada chamada volta com status, tempo e uma amostra recortada do corpo, pra
decidir por onde o extrato entra no painel (API ou importação do CSV) antes
de desenhar tabela. Avaliação completa em `docs/conciliacao-avaliacao.md`.

Só GET. O token nunca aparece na resposta. A renovação do token, se o ML
pedir, passa pelo caminho blindado do Atendimento (trava + commit próprio),
porque o refresh token do ML é de uso único.
"""

from __future__ import annotations

import csv
import io
import time
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Annotated, Any

import httpx
import structlog
from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission_any
from app.models import User
from app.services import logistica_meli as lm
from app.services.atendimento.clientes import cliente_da_integracao

logger = structlog.get_logger()
router = APIRouter(prefix="/api/conciliacao", tags=["conciliacao"])

MP_API_BASE = "https://api.mercadopago.com"
_PERM = require_permission_any(("margem", "logistica"), "view")
_PERM_EDIT = require_permission_any(("margem", "logistica"), "edit")
_AMOSTRA = 5
_LINHAS_CSV = 12
_MAX_TEXTO = 1500


def _chave(d: date) -> str:
    """Chave do período de faturamento do ML: sempre o primeiro dia do mês."""
    return d.strftime("%Y-%m-01")


def _mes_anterior(d: date) -> date:
    primeiro = d.replace(day=1)
    if primeiro.month == 1:
        return primeiro.replace(year=primeiro.year - 1, month=12)
    return primeiro.replace(month=primeiro.month - 1)


def _recorta(obj: Any) -> Any:
    """Amostra do corpo: listas ficam com os primeiros itens, textos encurtam."""
    if isinstance(obj, list):
        return {"total_itens": len(obj), "primeiros": [_recorta(x) for x in obj[:_AMOSTRA]]}
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if isinstance(v, list) and len(v) > _AMOSTRA:
                out[k] = {"total_itens": len(v), "primeiros": [_recorta(x) for x in v[:_AMOSTRA]]}
            else:
                out[k] = _recorta(v)
        return out
    if isinstance(obj, str) and len(obj) > _MAX_TEXTO:
        return obj[:_MAX_TEXTO] + "…"
    return obj


def _amostra_csv(texto: str) -> dict[str, Any]:
    """Cabeçalho + primeiras linhas de um CSV do Mercado Pago (separador ; ou ,)."""
    linhas = texto.splitlines()
    sep = ";" if linhas and linhas[0].count(";") >= linhas[0].count(",") else ","
    leitor = csv.reader(io.StringIO("\n".join(linhas[: _LINHAS_CSV + 1])), delimiter=sep)
    rows = list(leitor)
    return {
        "total_linhas": max(len(linhas) - 1, 0),
        "separador": sep,
        "colunas": rows[0] if rows else [],
        "primeiras": [dict(zip(rows[0], r, strict=False)) for r in rows[1:]] if rows else [],
    }


async def _chamada(
    nome: str, url: str, fn: Callable[[], Awaitable[httpx.Response]], *, csv_texto: bool = False
) -> dict[str, Any]:
    t0 = time.monotonic()
    item: dict[str, Any] = {"nome": nome, "url": url}
    try:
        r = await fn()
    except Exception as e:  # noqa: BLE001 — a sondagem registra e segue
        item.update(
            ok=False,
            erro=f"{type(e).__name__}: {str(e)[:300]}",
            ms=int((time.monotonic() - t0) * 1000),
        )
        return item
    item.update(
        status=r.status_code, ok=r.status_code == 200, ms=int((time.monotonic() - t0) * 1000)
    )
    ctype = r.headers.get("content-type", "")
    if csv_texto and r.status_code == 200 and "json" not in ctype:
        item["amostra"] = _amostra_csv(r.text)
        return item
    try:
        item["amostra"] = _recorta(r.json())
    except ValueError:
        item["amostra"] = (r.text or "")[:_MAX_TEXTO]
    return item


async def _mp_get(cliente: Any, path: str, params: dict | None = None) -> httpx.Response:
    """GET em api.mercadopago.com com a mesma chave da loja (host diferente do
    cliente do ML, por isso não passa pelo `_request` dele). Renova antes se
    estiver vencida — pelo `refresh` blindado que `cliente_da_integracao` instalou."""
    if cliente._expired():
        await cliente.refresh()
    headers = {"Authorization": f"Bearer {cliente.access_token}", "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=60.0) as c:
        return await c.get(f"{MP_API_BASE}{path}", headers=headers, params=params)


@router.get("/sonda")
async def sonda(
    conta: Annotated[
        str, Query(min_length=1, description="nome da conta ML na tela Lojas, ex. jlas2")
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(_PERM)],
    mes: Annotated[
        str | None, Query(description="AAAA-MM do período a ler; padrão = mês atual")
    ] = None,
    parte: Annotated[
        int | None,
        Query(
            ge=1,
            le=3,
            description="1 = chave + fatura do mês atual; 2 = mês anterior, bonificações, "
            "grupo MP, pagamentos da fatura; 3 = extrato do Mercado Pago. Sem valor = tudo. "
            "A API de faturamento do ML aceita 5 chamadas por minuto (14/10), daí as partes.",
        ),
    ] = None,
) -> dict[str, Any]:
    integ = await lm._ml_integration_for_conta(session, conta)
    if integ is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"code": "conta_ml_nao_encontrada", "conta": conta}
        )
    cliente = await cliente_da_integracao(integ)

    hoje = datetime.now(UTC).date()
    atual = date.fromisoformat(f"{mes}-01") if mes else hoje.replace(day=1)
    anterior = _mes_anterior(atual)
    k_atual, k_ant = _chave(atual), _chave(anterior)

    chamadas: list[dict[str, Any]] = []

    async def ml(nome: str, path: str, params: dict | None = None) -> dict[str, Any]:
        item = await _chamada(nome, path, lambda: cliente._request("GET", path, params=params))
        chamadas.append(item)
        return item

    async def mp(
        nome: str, path: str, params: dict | None = None, *, csv_texto: bool = False
    ) -> dict[str, Any]:
        item = await _chamada(
            nome,
            f"{MP_API_BASE}{path}",
            lambda: _mp_get(cliente, path, params),
            csv_texto=csv_texto,
        )
        chamadas.append(item)
        return item

    vazio: dict[str, Any] = {}
    eu = det_atual = det_ant = cfg = lista = vazio
    arquivo: str | None = None
    pagina = {
        "document_type": "BILL",
        "limit": _AMOSTRA,
        "from_id": 0,
        "sort_by": "ID",
        "order_by": "ASC",
    }

    if parte in (None, 1):
        # 1. A chave funciona? (quem é a loja) + fatura do ML do mês atual
        eu = await ml("ml_quem_sou", "/users/me")
        await ml(
            "ml_periodos",
            "/billing/integration/monthly/periods",
            {"group": "ML", "document_type": "BILL", "limit": 3},
        )
        await ml(
            "ml_resumo_mes_atual",
            f"/billing/integration/periods/key/{k_atual}/summary/details",
            {"group": "ML", "document_type": "BILL"},
        )
        det_atual = await ml(
            "ml_detalhes_mes_atual",
            f"/billing/integration/periods/key/{k_atual}/group/ML/details",
            pagina,
        )

    if parte in (None, 2):
        # 2. Mês anterior (fechado), bonificações, grupo MP e pagamentos da fatura
        det_ant = await ml(
            "ml_detalhes_mes_anterior",
            f"/billing/integration/periods/key/{k_ant}/group/ML/details",
            pagina,
        )
        await ml(
            "ml_bonificacoes_mes_anterior",
            f"/billing/integration/periods/key/{k_ant}/group/ML/details",
            {**pagina, "detail_type": "BONUS"},
        )
        await ml(
            "mp_grupo_detalhes_mes_atual",
            f"/billing/integration/periods/key/{k_atual}/group/MP/details",
            pagina,
        )
        await ml(
            "ml_pagamentos_da_fatura_mes_anterior",
            f"/billing/integration/periods/key/{k_ant}/group/ML/payment/details",
            {"limit": 3},
        )

    if parte in (None, 3):
        # 3. Porta do extrato no Mercado Pago (a dúvida que define o desenho)
        cfg = await mp("mp_extrato_config", "/v1/account/release_report/config")
        lista = await mp("mp_extrato_lista", "/v1/account/release_report/list")
        amostra = lista.get("amostra")
        itens = (
            amostra.get("primeiros")
            if isinstance(amostra, dict)
            else (amostra if isinstance(amostra, list) else None)
        )
        if lista.get("ok") and isinstance(itens, list):
            prontos = [x for x in itens if isinstance(x, dict) and x.get("file_name")]
            if prontos:
                arquivo = str(prontos[0]["file_name"])
        if arquivo:
            await mp(
                "mp_extrato_arquivo_mais_recente",
                f"/v1/account/release_report/{arquivo}",
                csv_texto=True,
            )
        await mp("mp_todas_transacoes_lista", "/v1/account/settlement_report/list")

    def _tem_resultados(item: dict[str, Any]) -> bool:
        a = item.get("amostra")
        if not isinstance(a, dict):
            return False
        res = a.get("results")
        if isinstance(res, dict):
            return bool(res.get("total_itens"))
        return bool(res)

    conclusao = {
        "chave_da_loja_funciona": bool(eu.get("ok")),
        "extrato_mp_abre_com_a_chave_do_ml": bool(lista.get("ok")),
        "extrato_mp_tem_config": bool(cfg.get("ok")),
        "extrato_mp_tem_arquivo_pronto": bool(arquivo),
        "fatura_mes_atual_tem_detalhes": _tem_resultados(det_atual),
        "fatura_mes_anterior_tem_detalhes": _tem_resultados(det_ant),
    }
    # O log da API é o único canal que dá pra ler em produção sem login: além
    # das conclusões, deixa o status de cada chamada e as colunas do extrato.
    por_nome = {c["nome"]: c.get("status") or c.get("erro") for c in chamadas}
    arquivo_amostra = next(
        (c.get("amostra") for c in chamadas if c["nome"] == "mp_extrato_arquivo_mais_recente"),
        None,
    )
    logger.info(
        "conciliacao_sonda",
        conta=conta,
        integration_id=str(integ.id),
        chamadas=por_nome,
        colunas_extrato=(arquivo_amostra or {}).get("colunas")
        if isinstance(arquivo_amostra, dict)
        else None,
        **conclusao,
    )
    return {
        "conta": conta,
        "integration_id": str(integ.id),
        "periodos": {"atual": k_atual, "anterior": k_ant},
        "quando": datetime.now(UTC).isoformat(),
        "conclusao": conclusao,
        "chamadas": chamadas,
    }


# --- Extrato do Mercado Pago (Relatório de liberações) ----------------------
# 14/10/2026, com OK do Vinicius: a conta da jlas2 nunca gerou o relatório, nem
# pelo painel nem por API. Pra ver as linhas de verdade são duas escritas de
# configuração na conta da loja (nunca em dinheiro ou pedido): criar a
# configuração (colunas, fuso) e pedir um relatório de um período. Depois o
# arquivo é lido e resumido aqui, sem gravar nada no DaVinci.

_COLUNAS_EXTRATO = [
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
# Conjunto mínimo (todos documentados) pra quando a conta recusar alguma coluna.
_COLUNAS_EXTRATO_MINIMO = _COLUNAS_EXTRATO[:13] + [
    "INSTALLMENTS",
    "PAYMENT_METHOD",
    "ORDER_ID",
    "SHIPPING_ID",
    "PACK_ID",
    "BALANCE_AMOUNT",
    "METADATA",
]


def _config_extrato(colunas: list[str]) -> dict[str, Any]:
    return {
        "file_name_prefix": "davinci-liberacoes",
        "include_withdrawal_at_end": False,
        "execute_after_withdrawal": False,
        "display_timezone": "GMT-03",
        "frequency": {"hour": 0, "type": "monthly", "value": 1},
        "columns": [{"key": c} for c in colunas],
    }


async def _mp_post(cliente: Any, path: str, body: dict) -> httpx.Response:
    if cliente._expired():
        await cliente.refresh()
    headers = {"Authorization": f"Bearer {cliente.access_token}", "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=60.0) as c:
        return await c.post(f"{MP_API_BASE}{path}", headers=headers, json=body)


def _num(v: Any) -> float:
    if v is None:
        return 0.0
    t = str(v).strip()
    if not t:
        return 0.0
    if "," in t and "." not in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return 0.0


def _coluna(cabecalho: list[str], *candidatas: str) -> str | None:
    baixo = {c.strip().lower(): c for c in cabecalho}
    for cand in candidatas:
        if cand.lower() in baixo:
            return baixo[cand.lower()]
    return None


def resumir_extrato(texto: str) -> dict[str, Any]:
    """Resumo de um CSV do Relatório de liberações: tipos de movimento, somas,
    quantas linhas têm número de pedido, e uma amostra por tipo. Não grava."""
    linhas = texto.splitlines()
    if not linhas:
        return {"total_linhas": 0}
    sep = ";" if linhas[0].count(";") >= linhas[0].count(",") else ","
    rows = list(csv.DictReader(io.StringIO("\n".join(linhas)), delimiter=sep))
    cab = list(rows[0].keys()) if rows else linhas[0].split(sep)
    c_tipo = _coluna(cab, "DESCRIPTION", "Descrição", "DESCRICAO")
    c_reg = _coluna(cab, "RECORD_TYPE", "Tipo de registro")
    c_cred = _coluna(cab, "NET_CREDIT_AMOUNT")
    c_deb = _coluna(cab, "NET_DEBIT_AMOUNT")
    c_pedido = _coluna(cab, "ORDER_ID")
    c_pack = _coluna(cab, "PACK_ID")
    c_ref = _coluna(cab, "EXTERNAL_REFERENCE")
    c_saldo = _coluna(cab, "BALANCE_AMOUNT")
    por_tipo: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"linhas": 0, "credito": 0.0, "debito": 0.0, "com_pedido": 0, "sem_pedido": 0}
    )
    registros: Counter[str] = Counter()
    amostra: dict[str, list[dict[str, Any]]] = defaultdict(list)
    com_pedido = sem_pedido = 0
    campos_amostra = [
        c
        for c in (
            _coluna(cab, "DATE"),
            _coluna(cab, "SOURCE_ID"),
            c_ref,
            c_reg,
            c_tipo,
            _coluna(cab, "GROSS_AMOUNT"),
            _coluna(cab, "MP_FEE_AMOUNT"),
            _coluna(cab, "FINANCING_FEE_AMOUNT"),
            _coluna(cab, "SHIPPING_FEE_AMOUNT"),
            _coluna(cab, "COUPON_AMOUNT"),
            c_cred,
            c_deb,
            c_saldo,
            c_pedido,
            c_pack,
            _coluna(cab, "SHIPPING_ID"),
            _coluna(cab, "INSTALLMENTS"),
            _coluna(cab, "PAYMENT_METHOD"),
            _coluna(cab, "METADATA"),
        )
        if c
    ]
    for r in rows:
        tipo = (r.get(c_tipo) or "").strip() if c_tipo else "?"
        reg = (r.get(c_reg) or "").strip() if c_reg else "?"
        registros[reg] += 1
        t = por_tipo[tipo]
        t["linhas"] += 1
        t["credito"] = round(t["credito"] + _num(r.get(c_cred)), 2) if c_cred else 0.0
        t["debito"] = round(t["debito"] + _num(r.get(c_deb)), 2) if c_deb else 0.0
        tem_pedido = bool((r.get(c_pedido) or "").strip() if c_pedido else "") or bool(
            (r.get(c_pack) or "").strip() if c_pack else ""
        )
        if tem_pedido:
            t["com_pedido"] += 1
            com_pedido += 1
        else:
            t["sem_pedido"] += 1
            sem_pedido += 1
        if len(amostra[tipo]) < 3:
            amostra[tipo].append({c: (r.get(c) or "")[:120] for c in campos_amostra})
    return {
        "total_linhas": len(rows),
        "separador": sep,
        "colunas": cab,
        "registros": dict(registros),
        "por_tipo": dict(por_tipo),
        "com_pedido": com_pedido,
        "sem_pedido": sem_pedido,
        "credito_total": round(sum(t["credito"] for t in por_tipo.values()), 2),
        "debito_total": round(sum(t["debito"] for t in por_tipo.values()), 2),
        "saldo_primeira_linha": (rows[0].get(c_saldo) if rows and c_saldo else None),
        "saldo_ultima_linha": (rows[-1].get(c_saldo) if rows and c_saldo else None),
        "amostra_por_tipo": dict(amostra),
    }


@router.post("/sonda/extrato/pedir")
async def sonda_extrato_pedir(
    conta: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(_PERM_EDIT)],
    de: Annotated[date, Query(description="primeiro dia (BRT)")],
    ate: Annotated[date, Query(description="último dia (BRT), inclusive")],
    _body: Annotated[dict | None, Body()] = None,
) -> dict[str, Any]:
    """Duas escritas de configuração na conta Mercado Pago da loja: cria a
    configuração do Relatório de liberações se não existir e pede o relatório
    do período. Nada de dinheiro, nada de pedido, nada gravado no DaVinci."""
    integ = await lm._ml_integration_for_conta(session, conta)
    if integ is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"code": "conta_ml_nao_encontrada", "conta": conta}
        )
    cliente = await cliente_da_integracao(integ)
    chamadas: list[dict[str, Any]] = []

    cfg = await _chamada(
        "mp_extrato_config_antes",
        f"{MP_API_BASE}/v1/account/release_report/config",
        lambda: _mp_get(cliente, "/v1/account/release_report/config"),
    )
    chamadas.append(cfg)
    if cfg.get("status") == 404:
        criada = await _chamada(
            "mp_extrato_config_criar",
            f"{MP_API_BASE}/v1/account/release_report/config",
            lambda: _mp_post(
                cliente, "/v1/account/release_report/config", _config_extrato(_COLUNAS_EXTRATO)
            ),
        )
        chamadas.append(criada)
        if criada.get("status") not in (200, 201):
            minima = await _chamada(
                "mp_extrato_config_criar_minima",
                f"{MP_API_BASE}/v1/account/release_report/config",
                lambda: _mp_post(
                    cliente,
                    "/v1/account/release_report/config",
                    _config_extrato(_COLUNAS_EXTRATO_MINIMO),
                ),
            )
            chamadas.append(minima)

    inicio = datetime(de.year, de.month, de.day, 3, 0, 0, tzinfo=UTC)  # 00:00 BRT
    fim = datetime(ate.year, ate.month, ate.day, 3, 0, 0, tzinfo=UTC).replace(
        hour=2, minute=59, second=59
    )
    fim = fim + (
        datetime(1, 1, 2) - datetime(1, 1, 1)
    )  # 23:59:59 BRT do último dia = 02:59:59Z do dia seguinte
    corpo = {
        "begin_date": inicio.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end_date": fim.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    pedido = await _chamada(
        "mp_extrato_pedir",
        f"{MP_API_BASE}/v1/account/release_report",
        lambda: _mp_post(cliente, "/v1/account/release_report", corpo),
    )
    pedido["corpo_enviado"] = corpo
    chamadas.append(pedido)
    logger.info(
        "conciliacao_sonda_extrato_pedido",
        conta=conta,
        integration_id=str(integ.id),
        chamadas={c["nome"]: c.get("status") or c.get("erro") for c in chamadas},
    )
    return {"conta": conta, "periodo": corpo, "chamadas": chamadas}


@router.get("/sonda/extrato")
async def sonda_extrato_ler(
    conta: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(_PERM)],
    bruto: Annotated[
        int,
        Query(ge=0, le=20, description="devolve também as N primeiras linhas cruas do CSV"),
    ] = 0,
) -> dict[str, Any]:
    """Lista os relatórios da conta e, se o mais novo estiver pronto, baixa o
    CSV e devolve um resumo (tipos de movimento, somas, com/sem pedido, amostra).
    Só leitura."""
    integ = await lm._ml_integration_for_conta(session, conta)
    if integ is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"code": "conta_ml_nao_encontrada", "conta": conta}
        )
    cliente = await cliente_da_integracao(integ)
    chamadas: list[dict[str, Any]] = []
    lista = await _chamada(
        "mp_extrato_lista",
        f"{MP_API_BASE}/v1/account/release_report/list",
        lambda: _mp_get(cliente, "/v1/account/release_report/list"),
    )
    chamadas.append(lista)
    amostra = lista.get("amostra")
    itens = (
        amostra.get("primeiros")
        if isinstance(amostra, dict)
        else (amostra if isinstance(amostra, list) else [])
    )
    relatorios = [x for x in (itens or []) if isinstance(x, dict)]
    pronto = next((x for x in relatorios if x.get("file_name")), None)
    resumo: dict[str, Any] | None = None
    if pronto:
        nome = str(pronto["file_name"])
        r_arq = await _chamada(
            "mp_extrato_arquivo",
            f"{MP_API_BASE}/v1/account/release_report/{nome}",
            lambda: _mp_get(cliente, f"/v1/account/release_report/{nome}"),
            csv_texto=True,
        )
        chamadas.append(r_arq)
        if r_arq.get("ok"):
            # relê o arquivo inteiro pro resumo (a amostra acima é só o começo)
            r2 = await _mp_get(cliente, f"/v1/account/release_report/{nome}")
            if r2.status_code == 200:
                resumo = resumir_extrato(r2.text)
                if bruto:
                    linhas = r2.text.splitlines()
                    # cabeçalho + primeiras linhas + a primeira linha com METADATA "cheio"
                    # (tem vírgula dentro: é ela que quebra o parser se não vier entre aspas)
                    cheia = next(
                        (x for x in linhas[1:] if '"payments_group_uuid' in x or '{"' in x), None
                    )
                    resumo["linhas_cruas"] = linhas[: bruto + 1] + ([cheia] if cheia else [])
    logger.info(
        "conciliacao_sonda_extrato_lido",
        conta=conta,
        integration_id=str(integ.id),
        relatorios=len(relatorios),
        pronto=bool(pronto),
        total_linhas=(resumo or {}).get("total_linhas"),
        tipos=list(((resumo or {}).get("por_tipo") or {}).keys()),
    )
    return {
        "conta": conta,
        "relatorios": relatorios,
        "pronto": pronto,
        "resumo": resumo,
        "chamadas": chamadas,
    }
