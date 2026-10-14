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
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Annotated, Any

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
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

    # 1. A chave funciona? (quem é a loja)
    eu = await ml("ml_quem_sou", "/users/me")

    # 2. Fatura do ML por período (documentado; nunca usado no DaVinci)
    await ml(
        "ml_periodos",
        "/billing/integration/monthly/periods",
        {"group": "ML", "document_type": "BILL", "limit": 3},
    )
    await ml(
        "ml_resumo_mes_atual",
        f"/billing/integration/periods/key/{k_atual}/summary/details",
        {"group": "ML"},
    )
    det_atual = await ml(
        "ml_detalhes_mes_atual",
        f"/billing/integration/periods/key/{k_atual}/group/ML/details",
        {
            "document_type": "BILL",
            "limit": _AMOSTRA,
            "from_id": 0,
            "sort_by": "ID",
            "order_by": "ASC",
        },
    )
    det_ant = await ml(
        "ml_detalhes_mes_anterior",
        f"/billing/integration/periods/key/{k_ant}/group/ML/details",
        {
            "document_type": "BILL",
            "limit": _AMOSTRA,
            "from_id": 0,
            "sort_by": "ID",
            "order_by": "ASC",
        },
    )
    await ml(
        "ml_bonificacoes_mes_anterior",
        f"/billing/integration/periods/key/{k_ant}/group/ML/details",
        {"document_type": "BILL", "limit": _AMOSTRA, "from_id": 0, "detail_type": "bonus"},
    )
    await ml(
        "mp_grupo_detalhes_mes_atual",
        f"/billing/integration/periods/key/{k_atual}/group/MP/details",
        {"document_type": "BILL", "limit": _AMOSTRA, "from_id": 0},
    )
    await ml(
        "ml_pagamentos_da_fatura_mes_anterior",
        f"/billing/integration/periods/key/{k_ant}/group/ML/payment/details",
        {"limit": 3},
    )

    # 3. Porta do extrato no Mercado Pago (a dúvida que define o desenho)
    cfg = await mp("mp_extrato_config", "/v1/account/release_report/config")
    lista = await mp("mp_extrato_lista", "/v1/account/release_report/list")
    arquivo: str | None = None
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
