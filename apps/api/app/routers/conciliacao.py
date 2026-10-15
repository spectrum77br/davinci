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

import time
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

import httpx
import structlog
from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission_any
from app.models import Integration, User
from app.services import logistica_meli as lm
from app.services.atendimento.clientes import cliente_da_integracao
from app.services.conciliacao_extrato import (
    MP_API_BASE,
    baixar_relatorio,
    garantir_config,
    listar_relatorios,
    mp_get,
    pedir_relatorio,
)
from app.services.conciliacao_jobs import loja_por_conta, lojas_ativas
from app.services.conciliacao_mp_csv import ler_csv_mp
from app.worker_pool import get_arq_pool

logger = structlog.get_logger()
router = APIRouter(prefix="/api/conciliacao", tags=["conciliacao"])

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
    cab, rows, sep = ler_csv_mp(texto)
    return {
        "total_linhas": len(rows),
        "separador": sep,
        "colunas": cab,
        "primeiras": rows[:_LINHAS_CSV],
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
            lambda: mp_get(cliente, path, params),
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
# 14/10/2026, com OK do Vinicius: duas escritas de configuração na conta da loja
# (criar a configuração do relatório e pedir um período) e a leitura do arquivo.
# A rotina diária de verdade está em `services/conciliacao_extrato.py`; estes
# endpoints são a sondagem manual e o resumo de um arquivo pronto.


def resumir_extrato(texto: str) -> dict[str, Any]:
    """Resumo de um CSV do Relatório de liberações: tipos de movimento, somas,
    quantas linhas têm número de pedido, e uma amostra por tipo. Não grava."""
    from app.services.conciliacao_mp_csv import dinheiro

    cab, rows, sep = ler_csv_mp(texto)
    if not cab:
        return {"total_linhas": 0}
    por_tipo: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "linhas": 0,
            "credito": Decimal("0"),
            "debito": Decimal("0"),
            "com_pedido": 0,
            "sem_pedido": 0,
        }
    )
    registros: Counter[str] = Counter()
    amostra: dict[str, list[dict[str, Any]]] = defaultdict(list)
    com_pedido = sem_pedido = 0
    campos = [
        c
        for c in (
            "DATE",
            "SOURCE_ID",
            "EXTERNAL_REFERENCE",
            "RECORD_TYPE",
            "DESCRIPTION",
            "GROSS_AMOUNT",
            "MP_FEE_AMOUNT",
            "FINANCING_FEE_AMOUNT",
            "SHIPPING_FEE_AMOUNT",
            "COUPON_AMOUNT",
            "NET_CREDIT_AMOUNT",
            "NET_DEBIT_AMOUNT",
            "BALANCE_AMOUNT",
            "ORDER_ID",
            "PACK_ID",
            "SHIPPING_ID",
            "INSTALLMENTS",
            "PAYMENT_METHOD",
            "PRODUCT_SKU",
            "METADATA",
        )
        if c in cab
    ]
    for r in rows:
        tipo = (r.get("DESCRIPTION") or "").strip()
        registros[(r.get("RECORD_TYPE") or "").strip()] += 1
        t = por_tipo[tipo]
        t["linhas"] += 1
        t["credito"] += dinheiro(r.get("NET_CREDIT_AMOUNT"))
        t["debito"] += dinheiro(r.get("NET_DEBIT_AMOUNT"))
        tem_pedido = bool((r.get("ORDER_ID") or "").strip() or (r.get("PACK_ID") or "").strip())
        t["com_pedido" if tem_pedido else "sem_pedido"] += 1
        if tem_pedido:
            com_pedido += 1
        else:
            sem_pedido += 1
        if len(amostra[tipo]) < 3:
            amostra[tipo].append({c: (r.get(c) or "")[:120] for c in campos})
    return {
        "total_linhas": len(rows),
        "separador": sep,
        "colunas": cab,
        "registros": dict(registros),
        "por_tipo": {
            k: {**v, "credito": str(v["credito"]), "debito": str(v["debito"])}
            for k, v in por_tipo.items()
        },
        "com_pedido": com_pedido,
        "sem_pedido": sem_pedido,
        "credito_total": str(sum((v["credito"] for v in por_tipo.values()), Decimal("0"))),
        "debito_total": str(sum((v["debito"] for v in por_tipo.values()), Decimal("0"))),
        "saldo_primeira_linha": rows[0].get("BALANCE_AMOUNT") if rows else None,
        "saldo_ultima_linha": rows[-1].get("BALANCE_AMOUNT") if rows else None,
        "amostra_por_tipo": dict(amostra),
    }


async def _cliente_da_conta(session: AsyncSession, conta: str) -> Any:
    integ = await lm._ml_integration_for_conta(session, conta)
    if integ is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"code": "conta_ml_nao_encontrada", "conta": conta}
        )
    return await cliente_da_integracao(integ)


@router.post("/sonda/extrato/pedir")
async def sonda_extrato_pedir(
    conta: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(_PERM_EDIT)],
    de: Annotated[date, Query(description="primeiro dia (BRT)")],
    ate: Annotated[date, Query(description="último dia (BRT), inclusive")],
    _body: Annotated[dict | None, Body()] = None,
) -> dict[str, Any]:
    """Cria a configuração do Relatório de liberações se não existir e pede o
    relatório do período. Nada de dinheiro, nada de pedido, nada gravado no DaVinci."""
    cliente = await _cliente_da_conta(session, conta)
    config = await garantir_config(cliente)
    pedido = await pedir_relatorio(cliente, de, ate)
    logger.info(
        "conciliacao_sonda_extrato_pedido",
        conta=conta,
        config=config.get("status"),
        http=pedido["status"],
    )
    return {"conta": conta, "config": config, "periodo": pedido["janela"], "pedido": pedido}


@router.get("/sonda/extrato")
async def sonda_extrato_ler(
    conta: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(_PERM)],
    bruto: Annotated[
        int, Query(ge=0, le=20, description="devolve também as N primeiras linhas cruas do CSV")
    ] = 0,
) -> dict[str, Any]:
    """Lista os relatórios da conta e, se o mais novo estiver pronto, baixa o
    CSV e devolve um resumo. Só leitura."""
    cliente = await _cliente_da_conta(session, conta)
    relatorios = await listar_relatorios(cliente)
    pronto = next((x for x in relatorios if x.get("file_name")), None)
    resumo: dict[str, Any] | None = None
    if pronto:
        texto = await baixar_relatorio(cliente, str(pronto["file_name"]))
        if texto is not None:
            resumo = resumir_extrato(texto)
            if bruto:
                linhas = texto.splitlines()
                cheia = next((x for x in linhas[1:] if '"{' in x or '"[' in x), None)
                resumo["linhas_cruas"] = linhas[: bruto + 1] + ([cheia] if cheia else [])
    logger.info(
        "conciliacao_sonda_extrato_lido",
        conta=conta,
        relatorios=len(relatorios),
        pronto=bool(pronto),
        total_linhas=(resumo or {}).get("total_linhas"),
    )
    return {"conta": conta, "relatorios": relatorios, "pronto": pronto, "resumo": resumo}


# --- Painel: status das lojas ligadas e "sincronizar agora" -------------------


@router.get("/status")
async def status_lojas(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(_PERM)],
) -> dict[str, Any]:
    """Por loja ligada: últimas leituras, movimentos por origem/tipo e pedidos por
    situação e etapa. É o painel "cru" da fase 2a — a tela vem na 2b."""
    from sqlalchemy import func

    from app.models import ConciliacaoMovimento, ConciliacaoPedido, ConciliacaoSync

    saida: list[dict[str, Any]] = []
    for loja in await lojas_ativas(session):
        por_tipo = (
            await session.execute(
                select(
                    ConciliacaoMovimento.origem,
                    ConciliacaoMovimento.tipo,
                    func.count(),
                    func.sum(ConciliacaoMovimento.credito),
                    func.sum(ConciliacaoMovimento.debito),
                )
                .where(ConciliacaoMovimento.integration_id == loja.integration_id)
                .group_by(ConciliacaoMovimento.origem, ConciliacaoMovimento.tipo)
                .order_by(ConciliacaoMovimento.origem, ConciliacaoMovimento.tipo)
            )
        ).all()
        por_situacao = (
            await session.execute(
                select(ConciliacaoPedido.situacao, func.count())
                .where(ConciliacaoPedido.integration_id == loja.integration_id)
                .group_by(ConciliacaoPedido.situacao)
            )
        ).all()
        por_etapa = (
            await session.execute(
                select(ConciliacaoPedido.etapa, func.count())
                .where(ConciliacaoPedido.integration_id == loja.integration_id)
                .group_by(ConciliacaoPedido.etapa)
            )
        ).all()
        syncs = (
            (
                await session.execute(
                    select(ConciliacaoSync)
                    .where(ConciliacaoSync.integration_id == loja.integration_id)
                    .order_by(ConciliacaoSync.origem, ConciliacaoSync.chave)
                )
            )
            .scalars()
            .all()
        )
        sub_tipos = (
            await session.execute(
                select(
                    ConciliacaoMovimento.sub_tipo,
                    ConciliacaoMovimento.tipo,
                    func.count(),
                    func.sum(ConciliacaoMovimento.debito),
                    func.sum(ConciliacaoMovimento.credito),
                )
                .where(
                    ConciliacaoMovimento.integration_id == loja.integration_id,
                    ConciliacaoMovimento.origem == "ml_fatura",
                )
                .group_by(ConciliacaoMovimento.sub_tipo, ConciliacaoMovimento.tipo)
                .order_by(func.count().desc())
            )
        ).all()
        sem_dono_tipos = (
            await session.execute(
                select(ConciliacaoMovimento.origem, ConciliacaoMovimento.tipo, func.count())
                .where(
                    ConciliacaoMovimento.integration_id == loja.integration_id,
                    ConciliacaoMovimento.origem == "mp_extrato",
                    ConciliacaoMovimento.pedido_bling.is_(None),
                    ConciliacaoMovimento.classificacao.is_(None),
                )
                .group_by(ConciliacaoMovimento.origem, ConciliacaoMovimento.tipo)
            )
        ).all()
        sem_pedido = (
            await session.execute(
                select(func.count()).where(
                    ConciliacaoMovimento.integration_id == loja.integration_id,
                    ConciliacaoMovimento.origem == "mp_extrato",
                    ConciliacaoMovimento.pedido_bling.is_(None),
                    ConciliacaoMovimento.classificacao.is_(None),
                )
            )
        ).scalar()
        saida.append(
            {
                "conta": loja.conta,
                "integration_id": str(loja.integration_id),
                "inicio": loja.inicio.isoformat(),
                "ultimo_extrato_em": loja.ultimo_extrato_em,
                "ultimo_fatura_em": loja.ultimo_fatura_em,
                "ultimo_recalculo_em": loja.ultimo_recalculo_em,
                "ultimo_erro": loja.ultimo_erro,
                "movimentos": [
                    {
                        "origem": o,
                        "tipo": t,
                        "linhas": n,
                        "credito": str(c or 0),
                        "debito": str(d or 0),
                    }
                    for o, t, n, c, d in por_tipo
                ],
                "movimentos_sem_dono": sem_pedido,
                "sem_dono_por_tipo": [
                    {"origem": o, "tipo": t, "linhas": n} for o, t, n in sem_dono_tipos
                ],
                "fatura_por_sub_tipo": [
                    {
                        "sub_tipo": st,
                        "tipo": t,
                        "linhas": n,
                        "debito": str(d or 0),
                        "credito": str(c or 0),
                    }
                    for st, t, n, d, c in sub_tipos
                ],
                "pedidos_por_situacao": dict(por_situacao),
                "pedidos_por_etapa": dict(por_etapa),
                "leituras": [
                    {
                        "origem": x.origem,
                        "chave": x.chave,
                        "status": x.status,
                        "linhas": x.linhas,
                        "lido_em": x.lido_em,
                        "erro": x.erro,
                        "last_id": (x.detalhe or {}).get("last_id"),
                    }
                    for x in syncs
                ],
            }
        )
    return {"lojas": saida}


@router.post("/lojas/{conta}/sincronizar")
async def sincronizar_agora(
    conta: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(_PERM_EDIT)],
) -> dict[str, Any]:
    """Enfileira uma rodada completa da loja (fatura + extrato + recálculo) no worker."""
    loja = await loja_por_conta(session, conta)
    if loja is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"code": "loja_nao_ligada", "conta": conta}
        )
    pool = await get_arq_pool()
    # O arq recusa um job com o MESMO id enquanto o resultado do anterior ainda
    # está guardado (1 h): o id leva o minuto, e a trava por loja em
    # `sincronizar_loja` é quem evita duas rodadas ao mesmo tempo.
    job = await pool.enqueue_job(
        "conciliacao_sincronizar_loja",
        str(loja.integration_id),
        _job_id=f"conciliacao_sincronizar_loja:{loja.integration_id}:{int(time.time() // 60)}",
    )
    return {
        "conta": loja.conta,
        "job_id": job.job_id if job else None,
        "enfileirado": job is not None,
    }


# --- Painel: lojas, pedidos (lista + totais), Raio-X, marcações e Excel ----------
# Passo 2b (14/10/2026, "Ok" do Vinicius): a tela. Lê `conciliacao_pedidos`
# (reconstruída pelo recálculo) e `conciliacao_movimentos`; grava só as marcas
# da pessoa (observação, conferido, finalizar à mão com motivo).

from datetime import timedelta  # noqa: E402
from io import BytesIO  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from fastapi.responses import StreamingResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402
from sqlalchemy import case as sa_case  # noqa: E402
from sqlalchemy import func, or_  # noqa: E402

from app.models import ConciliacaoLoja, ConciliacaoMovimento, ConciliacaoPedido  # noqa: E402
from app.models.conciliacao import ORIGEM_EXTRATO  # noqa: E402

_BRT = ZoneInfo("America/Sao_Paulo")
_PERM_PAINEL = require_permission_any(("conciliacao", "margem"), "view")
_PERM_PAINEL_EDIT = require_permission_any(("conciliacao", "margem"), "edit")

SITUACAO_LABELS = {
    "bateu": "Bateu",
    "aguardando": "Aguardando liberação",
    "vencido": "Não liberado (vencido)",
    "recebido_a_menos": "Recebido a menos",
    "recebido_a_mais": "Recebido a mais",
    "bling_diferente": "Bling ≠ venda",
    "cancelado": "Cancelado",
    "estornado_depois": "Estornado depois",
    "sem_venda": "Sem venda do ML",
}
ETAPA_LABELS = {
    "aguardando": "aguardando",
    "conciliado": "conciliado",
    "pendencia": "pendência",
    "finalizado": "finalizado",
    "reaberto": "reaberto",
}
_CAMPOS_PEDIDO = (
    "pedido_bling",
    "bling_id",
    "numeroloja",
    "pack_id",
    "conta",
    "situacao_bling",
    "bling_total",
    "bling_produtos",
    "bling_valorbase",
    "bling_custofrete",
    "bling_taxacomissao",
    "venda_valor",
    "venda_tarifa",
    "venda_frete",
    "venda_cupom",
    "venda_estorno",
    "venda_liquido",
    "venda_status",
    "fatura_tarifa",
    "fatura_parcelamento",
    "fatura_frete",
    "fatura_bonificacao",
    "fatura_outros",
    "liberado_valor",
    "saidas_depois",
    "recebido",
    "custo",
    "reembolso",
    "sobrou",
    "margem",
    "dif_bling_venda",
    "dif_previsto_recebido",
    "situacao",
    "motivo",
    "etapa",
    "etapa_por",
    "etapa_motivo",
    "conferido_por",
    "observacao",
)
_DATAS_PEDIDO = (
    "data_pedido",
    "previsao_liberacao",
    "liberado_em",
    "ultimo_movimento_em",
    "etapa_em",
    "bateu_em",
    "conferido_em",
    "recalculado_em",
)


def _num(v: Any) -> float | None:
    return None if v is None else float(v)


def _pedido_out(p: ConciliacaoPedido) -> dict[str, Any]:
    out: dict[str, Any] = {"id": str(p.id), "integration_id": str(p.integration_id)}
    for c in _CAMPOS_PEDIDO:
        v = getattr(p, c)
        out[c] = _num(v) if isinstance(v, Decimal) else v
    for c in _DATAS_PEDIDO:
        v = getattr(p, c)
        out[c] = v.isoformat() if v else None
    out["situacao_label"] = SITUACAO_LABELS.get(p.situacao, p.situacao)
    out["etapa_label"] = ETAPA_LABELS.get(p.etapa, p.etapa)
    out["conferido"] = bool(p.conferido_em)
    return out


def _movimento_out(m: ConciliacaoMovimento) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "origem": m.origem,
        "data": m.data.isoformat() if m.data else None,
        "tipo": m.tipo,
        "descricao": m.descricao,
        "sub_tipo": m.sub_tipo,
        "pagamento_id": m.pagamento_id,
        "pedido_marketplace": m.pedido_marketplace,
        "pack_id": m.pack_id,
        "pedido_bling": m.pedido_bling,
        "bruto": _num(m.bruto),
        "tarifa": _num(m.tarifa),
        "parcelamento": _num(m.parcelamento),
        "frete": _num(m.frete),
        "cupom": _num(m.cupom),
        "credito": _num(m.credito),
        "debito": _num(m.debito),
        "saldo_apos": _num(m.saldo_apos),
        "descontado_da_venda": m.descontado_da_venda,
        "sku": m.sku,
        "classificacao": m.classificacao,
        "data_aprovacao": m.data_aprovacao.isoformat() if m.data_aprovacao else None,
    }


async def _loja_ou_404(session: AsyncSession, conta: str | None) -> ConciliacaoLoja:
    lojas = await lojas_ativas(session)
    if not lojas:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "nenhuma_loja_ligada"})
    if not conta:
        return lojas[0]
    loja = next((x for x in lojas if x.conta.strip().lower() == conta.strip().lower()), None)
    if loja is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"code": "loja_nao_ligada", "conta": conta}
        )
    return loja


def _filtros(
    loja: ConciliacaoLoja,
    *,
    de: date | None,
    ate: date | None,
    data: str,
    situacao: str | None,
    etapa: str | None,
    q: str | None,
) -> list[Any]:
    conds: list[Any] = [ConciliacaoPedido.integration_id == loja.integration_id]
    col = ConciliacaoPedido.liberado_em if data == "liberacao" else ConciliacaoPedido.data_pedido
    if de:
        conds.append(col >= datetime.combine(de, datetime.min.time(), tzinfo=_BRT))
    if ate:
        conds.append(
            col < datetime.combine(ate, datetime.min.time(), tzinfo=_BRT) + timedelta(days=1)
        )
    if situacao:
        conds.append(
            ConciliacaoPedido.situacao.in_([x.strip() for x in situacao.split(",") if x.strip()])
        )
    if etapa:
        conds.append(
            ConciliacaoPedido.etapa.in_([x.strip() for x in etapa.split(",") if x.strip()])
        )
    if q:
        termo = f"%{q.strip()}%"
        conds.append(
            or_(
                ConciliacaoPedido.pedido_bling.ilike(termo),
                ConciliacaoPedido.numeroloja.ilike(termo),
                ConciliacaoPedido.pack_id.ilike(termo),
            )
        )
    return conds


@router.get("/lojas")
async def listar_lojas(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(_PERM_PAINEL)],
) -> dict[str, Any]:
    return {
        "lojas": [
            {
                "conta": loja.conta,
                "integration_id": str(loja.integration_id),
                "platform": getattr(loja.platform, "value", loja.platform),
                "inicio": loja.inicio.isoformat(),
                "ultimo_extrato_em": loja.ultimo_extrato_em.isoformat()
                if loja.ultimo_extrato_em
                else None,
                "ultimo_fatura_em": loja.ultimo_fatura_em.isoformat()
                if loja.ultimo_fatura_em
                else None,
                "ultimo_recalculo_em": loja.ultimo_recalculo_em.isoformat()
                if loja.ultimo_recalculo_em
                else None,
                "ultimo_erro": loja.ultimo_erro,
            }
            for loja in await lojas_ativas(session)
        ]
    }


@router.get("/pedidos")
async def listar_pedidos(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(_PERM_PAINEL)],
    loja: str | None = None,
    de: date | None = None,
    ate: date | None = None,
    data: Annotated[str, Query(pattern="^(pedido|liberacao)$")] = "pedido",
    situacao: str | None = None,
    etapa: str | None = None,
    q: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    """Uma linha por pedido do Bling + os totais do filtro (os cartões e o rodapé)."""
    alvo = await _loja_ou_404(session, loja)
    conds = _filtros(alvo, de=de, ate=ate, data=data, situacao=situacao, etapa=etapa, q=q)
    base = select(ConciliacaoPedido).where(*conds)
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
    rows = (
        (
            await session.execute(
                base.order_by(
                    ConciliacaoPedido.data_pedido.desc().nullslast(),
                    ConciliacaoPedido.pedido_bling.desc(),
                )
                .offset((page - 1) * size)
                .limit(size)
            )
        )
        .scalars()
        .all()
    )
    ped = ConciliacaoPedido
    somas = (
        await session.execute(
            select(
                func.count(),
                func.sum(ped.venda_valor),
                func.sum(ped.venda_tarifa),
                func.sum(ped.venda_frete),
                func.sum(ped.venda_liquido),
                func.sum(ped.recebido),
                func.sum(ped.dif_previsto_recebido),
                func.sum(ped.custo),
                func.sum(ped.reembolso),
                func.sum(ped.sobrou),
                func.count(ped.liberado_em),
            ).where(*conds)
        )
    ).one()
    por_situacao = dict(
        (
            await session.execute(
                select(ped.situacao, func.count()).where(*conds).group_by(ped.situacao)
            )
        ).all()
    )
    por_etapa = dict(
        (
            await session.execute(select(ped.etapa, func.count()).where(*conds).group_by(ped.etapa))
        ).all()
    )
    custo = somas[7] or Decimal("0")
    sobrou = somas[9] or Decimal("0")
    return {
        "loja": alvo.conta,
        "total": total,
        "page": page,
        "size": size,
        "itens": [_pedido_out(p) for p in rows],
        "resumo": {
            "pedidos": somas[0],
            "vendido": _num(somas[1] or 0),
            "tarifas_frete": _num((somas[2] or 0) + (somas[3] or 0)),
            "previsto": _num(somas[4] or 0),
            "recebido": _num(somas[5] or 0),
            "diferenca": _num(somas[6] or 0),
            "custo": _num(custo),
            "reembolso": _num(somas[8] or 0),
            "sobrou": _num(sobrou),
            "margem": _num((sobrou / custo).quantize(Decimal("0.0001"))) if custo > 0 else None,
            "liberados": somas[10],
            "por_situacao": por_situacao,
            "por_etapa": por_etapa,
        },
    }


async def _pedido_ou_404(
    session: AsyncSession, loja: ConciliacaoLoja, pedido: str
) -> ConciliacaoPedido:
    p = (
        await session.execute(
            select(ConciliacaoPedido).where(
                ConciliacaoPedido.integration_id == loja.integration_id,
                ConciliacaoPedido.pedido_bling == pedido,
            )
        )
    ).scalar_one_or_none()
    if p is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"code": "pedido_nao_conciliado", "pedido": pedido}
        )
    return p


@router.get("/pedidos/{pedido}")
async def raio_x(
    pedido: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(_PERM_PAINEL)],
    loja: str | None = None,
) -> dict[str, Any]:
    """As quatro versões do dinheiro lado a lado + cada movimento do pedido."""
    alvo = await _loja_ou_404(session, loja)
    p = await _pedido_ou_404(session, alvo, pedido)
    chaves = {x for x in (p.numeroloja, p.pack_id) if x}
    movs = (
        (
            await session.execute(
                select(ConciliacaoMovimento)
                .where(
                    ConciliacaoMovimento.integration_id == alvo.integration_id,
                    or_(
                        ConciliacaoMovimento.pedido_bling == p.pedido_bling,
                        ConciliacaoMovimento.pedido_marketplace.in_(chaves or {"-"}),
                        ConciliacaoMovimento.pack_id.in_(chaves or {"-"}),
                    ),
                )
                .order_by(ConciliacaoMovimento.data, ConciliacaoMovimento.origem)
            )
        )
        .scalars()
        .all()
    )
    liberacoes = [m for m in movs if m.origem == ORIGEM_EXTRATO and m.tipo == "venda_liberada"]

    def _soma(attr: str, lista: list[ConciliacaoMovimento]) -> float | None:
        vals = [getattr(m, attr) for m in lista if getattr(m, attr) is not None]
        return float(abs(sum(vals, Decimal("0")))) if vals else None

    versoes = {
        "bling": {
            "valor": _num(p.bling_total),
            "tarifa": _num(p.bling_taxacomissao),
            "frete": _num(p.bling_custofrete),
            "liquido": _num(
                (p.bling_valorbase or Decimal("0"))
                - (p.bling_custofrete or Decimal("0"))
                - (p.bling_taxacomissao or Decimal("0"))
            )
            if p.bling_valorbase is not None
            else None,
        },
        "venda": {
            "valor": _num(p.venda_valor),
            "tarifa": _num(p.venda_tarifa),
            "frete": _num(p.venda_frete),
            "cupom": _num(p.venda_cupom),
            "estorno": _num(p.venda_estorno),
            "liquido": _num(p.venda_liquido),
            "status": p.venda_status,
        },
        "fatura": {
            "tarifa": _num(p.fatura_tarifa),
            "parcelamento": _num(p.fatura_parcelamento),
            "frete": _num(p.fatura_frete),
            "bonificacao": _num(p.fatura_bonificacao),
            "outros": _num(p.fatura_outros),
        },
        "extrato": {
            "valor": _soma("bruto", liberacoes),
            "tarifa": _soma("tarifa", liberacoes),
            "parcelamento": _soma("parcelamento", liberacoes),
            "frete": _soma("frete", liberacoes),
            "cupom": _soma("cupom", liberacoes),
            "liquido": _num(p.liberado_valor),
            "saidas_depois": _num(p.saidas_depois),
            "recebido": _num(p.recebido),
        },
    }
    return {
        "pedido": _pedido_out(p),
        "versoes": versoes,
        "movimentos": [_movimento_out(m) for m in movs],
    }


class PedidoPatch(BaseModel):
    loja: str | None = None
    observacao: str | None = Field(default=None, max_length=2000)
    conferido: bool | None = None
    # "finalizado" (à mão, com motivo quando teve pendência) ou "reaberto".
    etapa: str | None = Field(default=None, pattern="^(finalizado|reaberto)$")
    etapa_motivo: str | None = Field(default=None, max_length=500)


@router.patch("/pedidos/{pedido}")
async def marcar_pedido(
    pedido: str,
    body: PedidoPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_PERM_PAINEL_EDIT)],
) -> dict[str, Any]:
    alvo = await _loja_ou_404(session, body.loja)
    p = await _pedido_ou_404(session, alvo, pedido)
    agora = datetime.now(UTC)
    quem = (getattr(user, "name", None) or user.email or "?")[:120]
    if body.observacao is not None:
        p.observacao = body.observacao.strip() or None
    if body.conferido is not None:
        p.conferido_em = agora if body.conferido else None
        p.conferido_por = quem if body.conferido else None
    if body.etapa == "finalizado":
        if p.situacao not in ("bateu", "cancelado") and not (body.etapa_motivo or "").strip():
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "motivo_obrigatorio",
                    "mensagem": "pra finalizar com pendência, diga o motivo",
                },
            )
        p.etapa = "finalizado"
        p.etapa_em = agora
        p.etapa_por = quem
        p.etapa_motivo = (body.etapa_motivo or "").strip() or None
    elif body.etapa == "reaberto":
        p.etapa = "reaberto"
        p.etapa_em = agora
        p.etapa_por = quem
        p.etapa_motivo = (body.etapa_motivo or "").strip() or "reaberto à mão"
    await session.commit()
    await session.refresh(p)
    return {"pedido": _pedido_out(p)}


_COLUNAS_XLSX = [
    ("Data", "data_pedido"),
    ("Pedido Bling", "pedido_bling"),
    ("Venda ML", "numeroloja"),
    ("Pack", "pack_id"),
    ("Loja", "conta"),
    ("Bling total", "bling_total"),
    ("Venda valor", "venda_valor"),
    ("Tarifa", "venda_tarifa"),
    ("Frete", "venda_frete"),
    ("Cupom", "venda_cupom"),
    ("Estorno", "venda_estorno"),
    ("Líquido previsto", "venda_liquido"),
    ("Fatura tarifa", "fatura_tarifa"),
    ("Fatura parcelamento", "fatura_parcelamento"),
    ("Fatura frete", "fatura_frete"),
    ("Fatura bonificação", "fatura_bonificacao"),
    ("Liberado em", "liberado_em"),
    ("Liberado", "liberado_valor"),
    ("Saídas depois", "saidas_depois"),
    ("Recebido", "recebido"),
    ("Dif. previsto × recebido", "dif_previsto_recebido"),
    ("Dif. Bling × venda", "dif_bling_venda"),
    ("Custo", "custo"),
    ("Reembolso", "reembolso"),
    ("Sobrou", "sobrou"),
    ("Margem", "margem"),
    ("Situação", "situacao_label"),
    ("Motivo", "motivo"),
    ("Etapa", "etapa_label"),
    ("Etapa em", "etapa_em"),
    ("Etapa por", "etapa_por"),
    ("Conferido por", "conferido_por"),
    ("Observação", "observacao"),
]


@router.post("/export.xlsx")
async def exportar_xlsx(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(_PERM_PAINEL)],
    loja: str | None = None,
    de: date | None = None,
    ate: date | None = None,
    data: Annotated[str, Query(pattern="^(pedido|liberacao)$")] = "pedido",
    situacao: str | None = None,
    etapa: str | None = None,
    q: str | None = None,
) -> StreamingResponse:
    from openpyxl import Workbook

    alvo = await _loja_ou_404(session, loja)
    conds = _filtros(alvo, de=de, ate=ate, data=data, situacao=situacao, etapa=etapa, q=q)
    rows = (
        (
            await session.execute(
                select(ConciliacaoPedido)
                .where(*conds)
                .order_by(ConciliacaoPedido.data_pedido.desc().nullslast())
                .limit(20000)
            )
        )
        .scalars()
        .all()
    )
    wb = Workbook()
    ws = wb.active
    ws.title = "Conciliação"
    ws.append([c for c, _ in _COLUNAS_XLSX])
    for p in rows:
        out = _pedido_out(p)
        linha = []
        for _, campo in _COLUNAS_XLSX:
            v = out.get(campo)
            if isinstance(v, str) and len(v) >= 19 and v[4] == "-" and "T" in v:
                try:
                    v = datetime.fromisoformat(v).astimezone(_BRT).strftime("%d/%m/%Y %H:%M")
                except ValueError:
                    pass
            linha.append(v)
        ws.append(linha)
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    nome = f"conciliacao_{alvo.conta}_{datetime.now(_BRT):%Y-%m-%d}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


# --- Extrato do dia: movimentos, vincular/classificar, sugestões, fechar o dia -----
# Passo 2c (14/10/2026, "Ok" do Vinicius): a tela de trabalho da manhã. Cada
# linha do extrato de ontem precisa de um destino — um pedido ou uma
# classificação — e o dia só fecha com 100%. Prova dos nove no rodapé: saldo
# inicial + créditos − débitos = saldo final = saldo informado pelo Mercado Pago.

from app.models import BlingOrder, ConciliacaoDia, ConciliacaoSync  # noqa: E402
from app.services.conciliacao_calculo import recalcular_loja  # noqa: E402
from app.services.conciliacao_extrato import TIPOS_RESERVA as TIPOS_RESERVA_SET  # noqa: E402
from app.services.conciliacao_extrato import bling_store_ids_da_loja  # noqa: E402

CLASSIFICACOES = ("saque", "fatura_ml", "publicidade", "anterior_ao_inicio", "ajuste", "outro")
CLASSIFICACAO_LABELS = {
    "saque": "Saque",
    "fatura_ml": "Fatura do ML",
    "publicidade": "Publicidade",
    "anterior_ao_inicio": "Pedido anterior ao início",
    "ajuste": "Ajuste",
    "outro": "Outro",
}
TIPO_LABELS = {
    "venda_liberada": "Venda liberada",
    "mediacao": "Mediação (reclamação perdida)",
    "mediacao_cancelada": "Mediação cancelada",
    "estorno": "Estorno",
    "chargeback": "Chargeback",
    "disputa": "Disputa",
    "reserva_disputa": "Reserva por reclamação",
    "reserva_estorno": "Reserva pra estorno",
    "reserva_frete_devolucao": "Reserva do frete de devolução",
    "reserva_saque": "Reserva pro saque",
    "reserva_pagamento": "Reserva de pagamento",
    "saque": "Saque",
    "frete": "Frete",
    "frete_cancelado": "Frete cancelado",
    "frete_devolucao": "Frete de devolução",
    "antecipacao": "Antecipação",
    "credito": "Crédito",
    "imposto": "Imposto",
    "outro": "Outro",
}
_SITUACOES_DIVERGENTES = {
    "recebido_a_menos",
    "recebido_a_mais",
    "bling_diferente",
    "estornado_depois",
    "vencido",
}


def _status_movimento(m: ConciliacaoMovimento, pedido: ConciliacaoPedido | None) -> str:
    if m.classificacao == "anterior_ao_inicio":
        return "anterior"
    if m.classificacao:
        return "classificado"
    if not m.pedido_bling:
        return "pendente"
    if m.vinculado_por:
        return "manual"
    if (
        pedido is not None
        and m.tipo == "venda_liberada"
        and pedido.situacao in _SITUACOES_DIVERGENTES
    ):
        return "divergente"
    return "automatico"


def _ordena_dia(movs: list[ConciliacaoMovimento]) -> list[ConciliacaoMovimento]:
    """Ordem do dia. O relatório não traz nº de linha; em empate de horário, a
    ordem certa é a que encadeia o saldo (saldo da linha = saldo da anterior +
    crédito − débito)."""
    movs = sorted(movs, key=lambda m: (m.data, m.id))
    out: list[ConciliacaoMovimento] = []
    i = 0
    while i < len(movs):
        grupo = [movs[i]]
        j = i + 1
        while j < len(movs) and movs[j].data == movs[i].data:
            grupo.append(movs[j])
            j += 1
        if len(grupo) > 1 and all(m.saldo_apos is not None for m in grupo):
            resto = list(grupo)
            ordenado: list[ConciliacaoMovimento] = []
            anterior = out[-1].saldo_apos if out and out[-1].saldo_apos is not None else None
            while resto:
                prox = None
                if anterior is not None:
                    prox = next(
                        (
                            m
                            for m in resto
                            if abs((anterior + m.credito - m.debito) - m.saldo_apos)
                            <= Decimal("0.01")
                        ),
                        None,
                    )
                if prox is None:
                    # sem âncora: o primeiro é o que não é "seguinte" de nenhum outro
                    prox = next(
                        (
                            m
                            for m in resto
                            if not any(
                                abs((o.saldo_apos + m.credito - m.debito) - m.saldo_apos)
                                <= Decimal("0.01")
                                for o in resto
                                if o is not m
                            )
                        ),
                        resto[0],
                    )
                ordenado.append(prox)
                resto.remove(prox)
                anterior = prox.saldo_apos
            grupo = ordenado
        out.extend(grupo)
        i = j
    return out


def _totais_do_dia(movs: list[ConciliacaoMovimento]) -> dict[str, Any]:
    creditos = sum((m.credito or Decimal("0") for m in movs), Decimal("0"))
    debitos = sum((m.debito or Decimal("0") for m in movs), Decimal("0"))
    primeiro = next((m for m in movs if m.saldo_apos is not None), None)
    ultimo = next((m for m in reversed(movs) if m.saldo_apos is not None), None)
    saldo_inicial = (primeiro.saldo_apos - primeiro.credito + primeiro.debito) if primeiro else None
    saldo_final = (saldo_inicial + creditos - debitos) if saldo_inicial is not None else None
    saldo_informado = ultimo.saldo_apos if ultimo else None
    return {
        "linhas": len(movs),
        "saldo_inicial": _num(saldo_inicial),
        "creditos": _num(creditos),
        "debitos": _num(debitos),
        "saldo_final": _num(saldo_final),
        "saldo_informado": _num(saldo_informado),
        "diferenca": _num(saldo_informado - saldo_final)
        if (saldo_informado is not None and saldo_final is not None)
        else None,
    }


async def _movimentos_do_dia(
    session: AsyncSession, loja: ConciliacaoLoja, dia: date
) -> list[ConciliacaoMovimento]:
    ini = datetime.combine(dia, datetime.min.time(), tzinfo=_BRT)
    movs = (
        (
            await session.execute(
                select(ConciliacaoMovimento).where(
                    ConciliacaoMovimento.integration_id == loja.integration_id,
                    ConciliacaoMovimento.origem == ORIGEM_EXTRATO,
                    ConciliacaoMovimento.data >= ini,
                    ConciliacaoMovimento.data < ini + timedelta(days=1),
                )
            )
        )
        .scalars()
        .all()
    )
    return _ordena_dia(list(movs))


def _ontem() -> date:
    return (datetime.now(_BRT) - timedelta(days=1)).date()


@router.get("/extrato")
async def extrato_do_dia(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(_PERM_PAINEL)],
    loja: str | None = None,
    dia: date | None = None,
    mostrar: Annotated[str, Query(pattern="^(todos|pendentes|divergentes|sem_pedido)$")] = "todos",
    q: str | None = None,
) -> dict[str, Any]:
    alvo = await _loja_ou_404(session, loja)
    dia = dia or _ontem()
    movs = await _movimentos_do_dia(session, alvo, dia)
    pedidos = {
        p.pedido_bling: p
        for p in (
            await session.execute(
                select(ConciliacaoPedido).where(
                    ConciliacaoPedido.integration_id == alvo.integration_id,
                    ConciliacaoPedido.pedido_bling.in_(
                        {m.pedido_bling for m in movs if m.pedido_bling} or {"-"}
                    ),
                )
            )
        ).scalars()
    }
    linhas: list[dict[str, Any]] = []
    contagem: dict[str, int] = {
        "automatico": 0,
        "manual": 0,
        "classificado": 0,
        "anterior": 0,
        "pendente": 0,
        "divergente": 0,
    }
    for m in movs:
        st = _status_movimento(m, pedidos.get(m.pedido_bling or ""))
        contagem[st] += 1
        if mostrar == "pendentes" and st != "pendente":
            continue
        if mostrar == "divergentes" and st != "divergente":
            continue
        if mostrar == "sem_pedido" and m.pedido_bling:
            continue
        if q:
            termo = q.strip().lower()
            if (
                termo
                not in " ".join(
                    str(x or "")
                    for x in (
                        m.pedido_bling,
                        m.pedido_marketplace,
                        m.pack_id,
                        m.pagamento_id,
                        m.descricao,
                        m.sku,
                    )
                ).lower()
            ):
                continue
        ped = pedidos.get(m.pedido_bling or "")
        linhas.append(
            {
                **_movimento_out(m),
                "tipo_label": TIPO_LABELS.get(m.tipo, m.tipo),
                "status": st,
                "vinculado_por": m.vinculado_por,
                "vinculado_em": m.vinculado_em.isoformat() if m.vinculado_em else None,
                "classificacao_label": CLASSIFICACAO_LABELS.get(
                    m.classificacao or "", m.classificacao
                ),
                "pedido_situacao": ped.situacao if ped else None,
                "pedido_situacao_label": SITUACAO_LABELS.get(ped.situacao, ped.situacao)
                if ped
                else None,
                "pedido_etapa": ped.etapa if ped else None,
            }
        )
    fechado = (
        await session.execute(
            select(ConciliacaoDia).where(
                ConciliacaoDia.integration_id == alvo.integration_id, ConciliacaoDia.dia == dia
            )
        )
    ).scalar_one_or_none()
    return {
        "loja": alvo.conta,
        "dia": dia.isoformat(),
        "movimentos": linhas,
        "totais": _totais_do_dia(movs),
        "contagem": contagem,
        "resolvidos": len(movs) - contagem["pendente"],
        "fechado": {"em": fechado.fechado_em.isoformat(), "por": fechado.fechado_por}
        if fechado and fechado.fechado_em
        else None,
        "classificacoes": [{"key": k, "label": v} for k, v in CLASSIFICACAO_LABELS.items()],
    }


@router.get("/dias")
async def listar_dias(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(_PERM_PAINEL)],
    loja: str | None = None,
    de: date | None = None,
    ate: date | None = None,
) -> dict[str, Any]:
    """Um resumo por dia (linhas, pendentes, fechado?) pra navegar e pro Resumo."""
    alvo = await _loja_ou_404(session, loja)
    ate = ate or _ontem()
    de = de or max(alvo.inicio, ate - timedelta(days=30))
    ini = datetime.combine(de, datetime.min.time(), tzinfo=_BRT)
    fim = datetime.combine(ate, datetime.min.time(), tzinfo=_BRT) + timedelta(days=1)
    dia_col = func.date(func.timezone("America/Sao_Paulo", ConciliacaoMovimento.data))
    pend = func.sum(
        sa_case(
            (
                ConciliacaoMovimento.pedido_bling.is_(None)
                & ConciliacaoMovimento.classificacao.is_(None),
                1,
            ),
            else_=0,
        )
    )
    rows = (
        await session.execute(
            select(
                dia_col,
                func.count(),
                pend,
                func.sum(ConciliacaoMovimento.credito),
                func.sum(ConciliacaoMovimento.debito),
            )
            .where(
                ConciliacaoMovimento.integration_id == alvo.integration_id,
                ConciliacaoMovimento.origem == ORIGEM_EXTRATO,
                ConciliacaoMovimento.data >= ini,
                ConciliacaoMovimento.data < fim,
            )
            .group_by(dia_col)
            .order_by(dia_col)
        )
    ).all()
    fechados = {
        d.dia: d
        for d in (
            await session.execute(
                select(ConciliacaoDia).where(
                    ConciliacaoDia.integration_id == alvo.integration_id,
                    ConciliacaoDia.dia >= de,
                    ConciliacaoDia.dia <= ate,
                )
            )
        ).scalars()
    }
    saida = []
    for d, n, p, c, deb in rows:
        f = fechados.get(d)
        saida.append(
            {
                "dia": d.isoformat(),
                "linhas": n,
                "pendentes": int(p or 0),
                "creditos": _num(c or 0),
                "debitos": _num(deb or 0),
                "fechado_em": f.fechado_em.isoformat() if f and f.fechado_em else None,
                "fechado_por": f.fechado_por if f else None,
            }
        )
    return {"loja": alvo.conta, "de": de.isoformat(), "ate": ate.isoformat(), "dias": saida}


class MovimentoPatch(BaseModel):
    loja: str | None = None
    # Vincular a um pedido do Bling (número), classificar sem pedido, ou desvincular.
    pedido_bling: str | None = Field(default=None, max_length=20)
    classificacao: str | None = Field(default=None, max_length=40)
    desvincular: bool = False


@router.get("/movimentos/{mov_id}/sugestoes")
async def sugestoes_de_vinculo(
    mov_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(_PERM_PAINEL)],
) -> dict[str, Any]:
    """Pedidos candidatos pra uma linha sem dono: mesmo pagamento, mesmo nº de
    venda/pack, ou mesmo valor por perto da data."""
    m = await session.get(ConciliacaoMovimento, mov_id)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "movimento_nao_encontrado"})
    loja = (
        await session.execute(
            select(ConciliacaoLoja).where(ConciliacaoLoja.integration_id == m.integration_id)
        )
    ).scalar_one_or_none()
    lojas_bling = (
        await bling_store_ids_da_loja(session, await session.get(Integration, m.integration_id))
        if loja
        else set()
    )
    sugestoes: list[dict[str, Any]] = []
    vistos: set[str] = set()

    def _add(
        numero: str, numeroloja: str | None, data_pedido: Any, total: Any, motivo: str
    ) -> None:
        if numero in vistos:
            return
        vistos.add(numero)
        sugestoes.append(
            {
                "pedido_bling": numero,
                "numeroloja": numeroloja,
                "data_pedido": data_pedido.isoformat() if data_pedido else None,
                "total": _num(total),
                "motivo": motivo,
            }
        )

    if m.pagamento_id:
        irmaos = (
            await session.execute(
                select(ConciliacaoMovimento.pedido_bling, ConciliacaoMovimento.pedido_marketplace)
                .where(
                    ConciliacaoMovimento.integration_id == m.integration_id,
                    ConciliacaoMovimento.pagamento_id == m.pagamento_id,
                    ConciliacaoMovimento.pedido_bling.isnot(None),
                )
                .distinct()
            )
        ).all()
        for numero, numeroloja in irmaos:
            _add(str(numero), numeroloja, None, None, "mesmo pagamento")
    chaves = {x for x in (m.pedido_marketplace, m.pack_id) if x}
    if chaves:
        rows = (
            await session.execute(
                select(BlingOrder.numero, BlingOrder.numeroloja, BlingOrder.data, BlingOrder.total)
                .where(BlingOrder.numeroloja.in_(chaves), BlingOrder.loja.in_(lojas_bling or {"-"}))
                .distinct()
            )
        ).all()
        for numero, numeroloja, data_pedido, total in rows:
            _add(str(numero), numeroloja, data_pedido, total, "mesmo nº de venda")
    valor = (
        m.bruto.copy_abs()
        if m.bruto
        else ((m.credito or Decimal("0")) - (m.debito or Decimal("0"))).copy_abs()
    )
    if valor and valor > 0 and lojas_bling:
        rows = (
            await session.execute(
                select(BlingOrder.numero, BlingOrder.numeroloja, BlingOrder.data, BlingOrder.total)
                .where(
                    BlingOrder.loja.in_(lojas_bling),
                    BlingOrder.total.between(valor - Decimal("0.05"), valor + Decimal("0.05")),
                    BlingOrder.data >= m.data - timedelta(days=45),
                    BlingOrder.data <= m.data + timedelta(days=2),
                )
                .distinct()
                .limit(8)
            )
        ).all()
        for numero, numeroloja, data_pedido, total in rows:
            _add(str(numero), numeroloja, data_pedido, total, "mesmo valor")
    return {"movimento": _movimento_out(m), "sugestoes": sugestoes[:10]}


@router.patch("/movimentos/{mov_id}")
async def marcar_movimento(
    mov_id: UUID,
    body: MovimentoPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_PERM_PAINEL_EDIT)],
) -> dict[str, Any]:
    m = await session.get(ConciliacaoMovimento, mov_id)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "movimento_nao_encontrado"})
    loja = (
        await session.execute(
            select(ConciliacaoLoja).where(ConciliacaoLoja.integration_id == m.integration_id)
        )
    ).scalar_one_or_none()
    if loja is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "loja_nao_ligada"})
    agora = datetime.now(UTC)
    quem = (getattr(user, "name", None) or user.email or "?")[:120]
    if body.desvincular:
        m.pedido_bling = None
        m.bling_id = None
        m.classificacao = None
        m.vinculado_por = quem
        m.vinculado_em = agora
    elif body.pedido_bling:
        lojas_bling = await bling_store_ids_da_loja(
            session, await session.get(Integration, m.integration_id)
        )
        alvo = (
            await session.execute(
                select(BlingOrder.numero, BlingOrder.bling_id)
                .where(
                    BlingOrder.numero == body.pedido_bling.strip(),
                    BlingOrder.loja.in_(lojas_bling or {"-"}),
                )
                .limit(1)
            )
        ).first()
        if alvo is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "pedido_bling_nao_encontrado",
                    "pedido": body.pedido_bling,
                    "loja": loja.conta,
                },
            )
        m.pedido_bling, m.bling_id = str(alvo[0]), alvo[1]
        m.classificacao = None
        m.vinculado_por = quem
        m.vinculado_em = agora
    elif body.classificacao:
        if body.classificacao not in CLASSIFICACOES:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": "classificacao_invalida"}
            )
        m.classificacao = body.classificacao
        m.pedido_bling = None
        m.bling_id = None
        m.vinculado_por = quem
        m.vinculado_em = agora
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": "nada_a_fazer"})
    await session.flush()
    await recalcular_loja(session, loja)
    await session.commit()
    await session.refresh(m)
    return {"movimento": _movimento_out(m), "status": _status_movimento(m, None)}


@router.post("/dias/{dia}/fechar")
async def fechar_dia(
    dia: date,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_PERM_PAINEL_EDIT)],
    loja: str | None = None,
) -> dict[str, Any]:
    alvo = await _loja_ou_404(session, loja)
    movs = await _movimentos_do_dia(session, alvo, dia)
    pendentes = [m for m in movs if not m.pedido_bling and not m.classificacao]
    if pendentes:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "dia_com_pendencias",
                "pendentes": len(pendentes),
                "mensagem": "toda linha precisa de um destino",
            },
        )
    totais = _totais_do_dia(movs)
    agora = datetime.now(UTC)
    quem = (getattr(user, "name", None) or user.email or "?")[:120]
    row = (
        await session.execute(
            select(ConciliacaoDia).where(
                ConciliacaoDia.integration_id == alvo.integration_id, ConciliacaoDia.dia == dia
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = ConciliacaoDia(integration_id=alvo.integration_id, dia=dia)
        session.add(row)
    for k in ("saldo_inicial", "creditos", "debitos", "saldo_final", "saldo_informado"):
        setattr(row, k, Decimal(str(totais[k])) if totais[k] is not None else None)
    row.linhas = totais["linhas"]
    row.pendentes = 0
    row.fechado_em = agora
    row.fechado_por = quem
    await session.commit()
    return {
        "dia": dia.isoformat(),
        "fechado": {"em": agora.isoformat(), "por": quem},
        "totais": totais,
    }


@router.post("/dias/{dia}/reabrir")
async def reabrir_dia(
    dia: date,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(_PERM_PAINEL_EDIT)],
    loja: str | None = None,
) -> dict[str, Any]:
    alvo = await _loja_ou_404(session, loja)
    row = (
        await session.execute(
            select(ConciliacaoDia).where(
                ConciliacaoDia.integration_id == alvo.integration_id, ConciliacaoDia.dia == dia
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        row.fechado_em = None
        row.fechado_por = None
        await session.commit()
    return {"dia": dia.isoformat(), "fechado": None}


# --- Resumo (tela de entrada) e Fechamento do mês ----------------------------------
# Passo 2d (14/10/2026, "Ok" do Vinicius). Resumo: uma linha por loja ligada com o
# dia de ontem (fechou? pendentes? saldo bate?), as pendências dos pedidos e o mês
# até ontem; "Onde agir hoje" é a mesma lista que o vigia vai mandar no Threema.
# Fechamento: o mês por loja — vendas (pela data do pedido), caixa (pela data do
# movimento, com "ajustes de meses anteriores"), a fatura do ML (resumo × detalhes)
# e os dias fechados.

_ETAPAS_PENDENTES = ("pendencia", "reaberto")
_ATRASO_EXTRATO = timedelta(hours=36)


def _mes_range(mes: str | None) -> tuple[date, date]:
    """'AAAA-MM' → (primeiro dia, primeiro dia do mês seguinte)."""
    hoje = datetime.now(_BRT).date()
    try:
        ini = date.fromisoformat(f"{mes}-01") if mes else hoje.replace(day=1)
    except ValueError as exc:
        raise HTTPException(422, "mês inválido") from exc
    fim = (
        ini.replace(year=ini.year + 1, month=1)
        if ini.month == 12
        else ini.replace(month=ini.month + 1)
    )
    return ini, fim


async def _somas_pedidos(
    session: AsyncSession, loja: ConciliacaoLoja, ini: date, fim: date
) -> dict[str, Any]:
    """Totais dos pedidos da loja vendidos em [ini, fim) — os mesmos cartões da aba Pedidos."""
    ped = ConciliacaoPedido
    conds = [
        ped.integration_id == loja.integration_id,
        ped.data_pedido >= datetime.combine(ini, datetime.min.time(), tzinfo=_BRT),
        ped.data_pedido < datetime.combine(fim, datetime.min.time(), tzinfo=_BRT),
    ]
    s = (
        await session.execute(
            select(
                func.count(),
                func.sum(ped.venda_valor),
                func.sum(ped.venda_tarifa),
                func.sum(ped.venda_frete),
                func.sum(ped.fatura_parcelamento),
                func.sum(ped.venda_cupom),
                func.sum(ped.venda_liquido),
                func.sum(ped.recebido),
                func.sum(ped.saidas_depois),
                func.sum(ped.custo),
                func.sum(ped.reembolso),
                func.sum(ped.sobrou),
                func.count(ped.liberado_em),
            ).where(*conds)
        )
    ).one()
    por_situacao = dict(
        (
            await session.execute(
                select(ped.situacao, func.count()).where(*conds).group_by(ped.situacao)
            )
        ).all()
    )
    por_etapa = dict(
        (
            await session.execute(select(ped.etapa, func.count()).where(*conds).group_by(ped.etapa))
        ).all()
    )
    custo = s[9] or Decimal("0")
    sobrou = s[11] or Decimal("0")
    return {
        "pedidos": s[0],
        "vendido": _num(s[1] or 0),
        "tarifa": _num(s[2] or 0),
        "frete": _num(s[3] or 0),
        "parcelamento": _num(s[4] or 0),
        "cupom": _num(s[5] or 0),
        "previsto": _num(s[6] or 0),
        "recebido": _num(s[7] or 0),
        "estornos": _num(s[8] or 0),
        "custo": _num(custo),
        "reembolso": _num(s[10] or 0),
        "sobrou": _num(sobrou),
        "margem": _num((sobrou / custo).quantize(Decimal("0.0001"))) if custo > 0 else None,
        "liberados": s[12],
        "finalizados": por_etapa.get("finalizado", 0),
        "por_situacao": por_situacao,
        "por_etapa": por_etapa,
    }


async def _ajustes_anteriores(
    session: AsyncSession, loja: ConciliacaoLoja, ini: date, fim: date
) -> tuple[float, int]:
    """Dinheiro do extrato em [ini, fim) que é de pedido de ANTES de `ini` (ou
    "anterior ao início"): o que o mês carrega de meses passados. (valor, linhas)"""
    mov = ConciliacaoMovimento
    ped = ConciliacaoPedido
    ini_dt = datetime.combine(ini, datetime.min.time(), tzinfo=_BRT)
    fim_dt = datetime.combine(fim, datetime.min.time(), tzinfo=_BRT)
    valor, n = (
        await session.execute(
            select(func.sum(mov.credito - mov.debito), func.count())
            .select_from(mov)
            .outerjoin(
                ped,
                (ped.integration_id == mov.integration_id) & (ped.pedido_bling == mov.pedido_bling),
            )
            .where(
                mov.integration_id == loja.integration_id,
                mov.origem == ORIGEM_EXTRATO,
                mov.data >= ini_dt,
                mov.data < fim_dt,
                mov.tipo != "saque",
                mov.tipo.not_in(tuple(TIPOS_RESERVA_SET)),
                or_(mov.classificacao == "anterior_ao_inicio", ped.data_pedido < ini_dt),
            )
        )
    ).one()
    return float(valor or 0), int(n or 0)


def _soma_dict(destino: dict[str, Any], origem: dict[str, Any]) -> None:
    for k, v in origem.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            destino[k] = (destino.get(k) or 0) + v


@router.get("/resumo")
async def resumo_geral(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(_PERM_PAINEL)],
    dia: date | None = None,
) -> dict[str, Any]:
    dia = dia or _ontem()
    agora = datetime.now(UTC)
    mes_ini = dia.replace(day=1)
    lojas_out: list[dict[str, Any]] = []
    agir: list[dict[str, Any]] = []
    por_plataforma: dict[str, dict[str, Any]] = {}
    empresa: dict[str, Any] = {"lojas": 0}
    for loja in await lojas_ativas(session):
        plataforma = getattr(loja.platform, "value", str(loja.platform))
        movs = await _movimentos_do_dia(session, loja, dia)
        pedidos_do_dia = {
            p.pedido_bling: p
            for p in (
                await session.execute(
                    select(ConciliacaoPedido).where(
                        ConciliacaoPedido.integration_id == loja.integration_id,
                        ConciliacaoPedido.pedido_bling.in_(
                            {m.pedido_bling for m in movs if m.pedido_bling} or {"-"}
                        ),
                    )
                )
            ).scalars()
        }
        contagem: dict[str, int] = {
            "automatico": 0,
            "manual": 0,
            "classificado": 0,
            "anterior": 0,
            "pendente": 0,
            "divergente": 0,
        }
        for m in movs:
            contagem[_status_movimento(m, pedidos_do_dia.get(m.pedido_bling or ""))] += 1
        totais = _totais_do_dia(movs)
        fechado = (
            await session.execute(
                select(ConciliacaoDia).where(
                    ConciliacaoDia.integration_id == loja.integration_id, ConciliacaoDia.dia == dia
                )
            )
        ).scalar_one_or_none()
        ped = ConciliacaoPedido
        base = [ped.integration_id == loja.integration_id]
        por_etapa = dict(
            (
                await session.execute(
                    select(ped.etapa, func.count()).where(*base).group_by(ped.etapa)
                )
            ).all()
        )
        por_situacao = dict(
            (
                await session.execute(
                    select(ped.situacao, func.count()).where(*base).group_by(ped.situacao)
                )
            ).all()
        )
        mes = await _somas_pedidos(session, loja, mes_ini, dia + timedelta(days=1))
        mes["ajustes_meses_anteriores"], mes["ajustes_qtd"] = await _ajustes_anteriores(
            session, loja, mes_ini, dia + timedelta(days=1)
        )
        extrato_atrasado = dia >= loja.inicio and (
            loja.ultimo_extrato_em is None or agora - loja.ultimo_extrato_em > _ATRASO_EXTRATO
        )
        saldo_bate = totais["diferenca"] is not None and abs(totais["diferenca"]) <= 0.05
        sem_movimento = totais["linhas"] == 0
        if fechado and fechado.fechado_em:
            dia_status, dia_texto = "fechado", f"fechado por {fechado.fechado_por}"
        elif sem_movimento and extrato_atrasado:
            dia_status, dia_texto = "extrato_nao_lido", "extrato não lido"
        elif sem_movimento:
            dia_status, dia_texto = "sem_movimento", "sem movimento"
        elif contagem["pendente"] == 0 and saldo_bate:
            dia_status, dia_texto = "pronto", "100% casado, saldo bate — pode fechar"
        else:
            dia_status, dia_texto = "aberto", "não fechou"
        reabertos = por_etapa.get("reaberto", 0)
        pendencias = por_etapa.get("pendencia", 0)
        vencidos = por_situacao.get("vencido", 0)
        a_menos = por_situacao.get("recebido_a_menos", 0)
        estornados = por_situacao.get("estornado_depois", 0)
        problemas: list[str] = []
        if contagem["pendente"]:
            problemas.append(f"{contagem['pendente']} linha(s) sem dono")
        if totais["diferenca"] is not None and not saldo_bate:
            problemas.append(f"saldo não bate ({totais['diferenca']:+.2f})")
        if extrato_atrasado:
            problemas.append("extrato não lido")
        if reabertos:
            problemas.append(f"{reabertos} reaberto(s)")
        if vencidos:
            problemas.append(f"{vencidos} sem liberação vencido(s)")
        if a_menos:
            problemas.append(f"{a_menos} recebido(s) a menos")
        if estornados:
            problemas.append(f"{estornados} estornado(s) depois")
        if loja.ultimo_erro:
            problemas.append(f"erro: {loja.ultimo_erro[:80]}")
        linha = {
            "conta": loja.conta,
            "platform": plataforma,
            "inicio": loja.inicio.isoformat(),
            "dia": {
                "status": dia_status,
                "texto": dia_texto,
                "linhas": totais["linhas"],
                "pendentes": contagem["pendente"],
                "divergentes": contagem["divergente"],
                "saldo_bate": saldo_bate if totais["linhas"] else None,
                "diferenca": totais["diferenca"],
                "fechado_em": fechado.fechado_em.isoformat()
                if fechado and fechado.fechado_em
                else None,
                "fechado_por": fechado.fechado_por if fechado else None,
            },
            "pedidos": {
                "pendencia": pendencias,
                "reabertos": reabertos,
                "aguardando": por_etapa.get("aguardando", 0),
                "finalizados": por_etapa.get("finalizado", 0),
                "conciliados": por_etapa.get("conciliado", 0),
                "vencidos": vencidos,
                "recebido_a_menos": a_menos,
                "estornados": estornados,
            },
            "leituras": {
                "extrato_em": loja.ultimo_extrato_em.isoformat()
                if loja.ultimo_extrato_em
                else None,
                "fatura_em": loja.ultimo_fatura_em.isoformat() if loja.ultimo_fatura_em else None,
                "recalculo_em": loja.ultimo_recalculo_em.isoformat()
                if loja.ultimo_recalculo_em
                else None,
                "extrato_atrasado": extrato_atrasado,
                "erro": loja.ultimo_erro,
            },
            "mes": mes,
            "situacao": " · ".join(problemas)
            if problemas
            else ("dia fechado" if dia_status == "fechado" else "tudo certo"),
            "gravidade": "bad"
            if (contagem["pendente"] or extrato_atrasado or (totais["linhas"] and not saldo_bate))
            else ("warn" if problemas else "ok"),
        }
        lojas_out.append(linha)
        # Onde agir hoje
        if contagem["pendente"] or (totais["linhas"] and not saldo_bate):
            agir.append(
                {
                    "gravidade": "bad",
                    "loja": loja.conta,
                    "titulo": f"{loja.conta} · {contagem['pendente']} linha(s) sem dono"
                    + (
                        ""
                        if saldo_bate or totais["diferenca"] is None
                        else f" · saldo não bate ({totais['diferenca']:+.2f})"
                    ),
                    "detalhe": (
                        f"extrato de {dia:%d/%m}: {totais['linhas']} movimentos, "
                        f"{contagem['pendente']} pendentes"
                    ),
                    "acao": "extrato",
                    "dia": dia.isoformat(),
                }
            )
        if extrato_atrasado:
            agir.append(
                {
                    "gravidade": "bad",
                    "loja": loja.conta,
                    "titulo": f"{loja.conta} · extrato não lido",
                    "detalhe": (
                        f"última leitura {loja.ultimo_extrato_em.astimezone(_BRT):%d/%m %H:%M}"
                        if loja.ultimo_extrato_em
                        else "nunca lido"
                    )
                    + (f" · {loja.ultimo_erro[:80]}" if loja.ultimo_erro else ""),
                    "acao": "sincronizar",
                    "dia": dia.isoformat(),
                }
            )
        if reabertos:
            agir.append(
                {
                    "gravidade": "warn",
                    "loja": loja.conta,
                    "titulo": f"{loja.conta} · {reabertos} pedido(s) reaberto(s)",
                    "detalhe": "chegou movimento novo depois de finalizado",
                    "acao": "pedidos",
                    "filtro": {"etapa": "reaberto"},
                }
            )
        if vencidos:
            agir.append(
                {
                    "gravidade": "warn",
                    "loja": loja.conta,
                    "titulo": f"{loja.conta} · {vencidos} venda(s) sem liberação, prazo vencido",
                    "detalhe": "abrir chamado de repasse",
                    "acao": "pedidos",
                    "filtro": {"situacao": "vencido"},
                }
            )
        if a_menos:
            agir.append(
                {
                    "gravidade": "warn",
                    "loja": loja.conta,
                    "titulo": f"{loja.conta} · {a_menos} recebido(s) a menos",
                    "detalhe": "conferir o motivo e finalizar com motivo, ou abrir chamado",
                    "acao": "pedidos",
                    "filtro": {"situacao": "recebido_a_menos"},
                }
            )
        if estornados:
            agir.append(
                {
                    "gravidade": "warn",
                    "loja": loja.conta,
                    "titulo": f"{loja.conta} · {estornados} estornado(s) depois de liberar",
                    "detalhe": "mediação, estorno ou chargeback depois do dinheiro entrar",
                    "acao": "pedidos",
                    "filtro": {"situacao": "estornado_depois"},
                }
            )
        if dia_status == "pronto":
            agir.append(
                {
                    "gravidade": "ok",
                    "loja": loja.conta,
                    "titulo": f"{loja.conta} · dia {dia:%d/%m} pronto pra fechar",
                    "detalhe": "100% das linhas com destino e saldo batendo",
                    "acao": "extrato",
                    "dia": dia.isoformat(),
                }
            )
        plat = por_plataforma.setdefault(plataforma, {"lojas": 0})
        plat["lojas"] += 1
        _soma_dict(
            plat, {k: v for k, v in mes.items() if k not in ("margem", "por_situacao", "por_etapa")}
        )
        empresa["lojas"] += 1
        _soma_dict(
            empresa,
            {k: v for k, v in mes.items() if k not in ("margem", "por_situacao", "por_etapa")},
        )
    for bloco in (*por_plataforma.values(), empresa):
        custo = bloco.get("custo") or 0
        bloco["margem"] = round((bloco.get("sobrou") or 0) / custo, 4) if custo else None
    ordem = {"bad": 0, "warn": 1, "ok": 2}
    agir.sort(key=lambda x: (ordem.get(x["gravidade"], 9), x["loja"]))
    return {
        "dia": dia.isoformat(),
        "mes": mes_ini.isoformat()[:7],
        "lojas": lojas_out,
        "agir": agir,
        "por_plataforma": por_plataforma,
        "empresa": empresa,
        "cartoes": {
            "lojas": len(lojas_out),
            "dias_fechados": sum(1 for x in lojas_out if x["dia"]["status"] == "fechado"),
            "dias_prontos": sum(1 for x in lojas_out if x["dia"]["status"] == "pronto"),
            "pendentes": sum(x["dia"]["pendentes"] for x in lojas_out),
            "reabertos": sum(x["pedidos"]["reabertos"] for x in lojas_out),
            "vencidos": sum(x["pedidos"]["vencidos"] for x in lojas_out),
            "extrato_atrasado": sum(1 for x in lojas_out if x["leituras"]["extrato_atrasado"]),
            "saldo_bate": sum(1 for x in lojas_out if x["dia"]["saldo_bate"]),
            "com_movimento": sum(1 for x in lojas_out if x["dia"]["linhas"]),
        },
    }


_TIPOS_CAIXA = {
    "venda_liberada": "liberado",
    "mediacao": "mediacoes",
    "mediacao_cancelada": "mediacoes_canceladas",
    "estorno": "estornos",
    "chargeback": "chargebacks",
    "frete": "frete",
    "frete_cancelado": "frete",
    "frete_devolucao": "frete",
    "saque": "saques",
}


@router.get("/fechamento")
async def fechamento_do_mes(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(_PERM_PAINEL)],
    loja: str | None = None,
    mes: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None,
) -> dict[str, Any]:
    alvo = await _loja_ou_404(session, loja)
    ini, fim = _mes_range(mes)
    ini_dt = datetime.combine(ini, datetime.min.time(), tzinfo=_BRT)
    fim_dt = datetime.combine(fim, datetime.min.time(), tzinfo=_BRT)

    vendas = await _somas_pedidos(session, alvo, ini, fim)

    # Caixa: pela data do movimento, reservas de fora, com "ajustes de meses anteriores".
    mov = ConciliacaoMovimento
    pedd = ConciliacaoPedido
    rows = (
        await session.execute(
            select(
                mov.tipo,
                mov.classificacao,
                pedd.data_pedido,
                func.count(),
                func.sum(mov.credito),
                func.sum(mov.debito),
            )
            .select_from(mov)
            .outerjoin(
                pedd,
                (pedd.integration_id == mov.integration_id)
                & (pedd.pedido_bling == mov.pedido_bling),
            )
            .where(
                mov.integration_id == alvo.integration_id,
                mov.origem == ORIGEM_EXTRATO,
                mov.data >= ini_dt,
                mov.data < fim_dt,
            )
            .group_by(mov.tipo, mov.classificacao, pedd.data_pedido)
        )
    ).all()
    caixa: dict[str, Any] = {
        "liberado": 0.0,
        "mediacoes": 0.0,
        "mediacoes_canceladas": 0.0,
        "estornos": 0.0,
        "chargebacks": 0.0,
        "frete": 0.0,
        "saques": 0.0,
        "outros": 0.0,
        "reservas_liquido": 0.0,
        "ajustes_meses_anteriores": 0.0,
        "ajustes_qtd": 0,
        "linhas": 0,
    }
    for tipo, classificacao, data_pedido, n, c, d in rows:
        c = float(c or 0)
        d = float(d or 0)
        caixa["linhas"] += n
        if tipo in TIPOS_RESERVA_SET:
            caixa["reservas_liquido"] += c - d
            continue
        anterior = classificacao == "anterior_ao_inicio" or (
            data_pedido is not None and data_pedido < ini_dt
        )
        if anterior and tipo != "saque":
            caixa["ajustes_meses_anteriores"] += c - d
            caixa["ajustes_qtd"] += n
            continue
        chave = _TIPOS_CAIXA.get(tipo, "outros")
        caixa[chave] += c - d
    caixa = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in caixa.items()}
    # O que entrou de verdade no mês (antes de tirar): vendas liberadas, menos o que
    # saiu por mediação/estorno/chargeback/frete, mais ajustes de meses anteriores.
    caixa["resultado_caixa"] = round(
        caixa["liberado"]
        + caixa["mediacoes"]
        + caixa["mediacoes_canceladas"]
        + caixa["estornos"]
        + caixa["chargebacks"]
        + caixa["frete"]
        + caixa["outros"]
        + caixa["ajustes_meses_anteriores"],
        2,
    )
    # Tudo somado (com reservas e saques) tem que ser saldo final − saldo inicial.
    caixa["movimento_liquido"] = round(
        caixa["resultado_caixa"] + caixa["reservas_liquido"] + caixa["saques"], 2
    )

    # Dias do mês: linhas, pendentes, fechado — e o saldo do primeiro e do último dia.
    dias_resp = await listar_dias(
        session, _user, loja=alvo.conta, de=ini, ate=min(fim - timedelta(days=1), _ontem())
    )
    dias = dias_resp["dias"]
    saldo_inicial = saldo_final = None
    if dias:
        primeiro = await _movimentos_do_dia(session, alvo, date.fromisoformat(dias[0]["dia"]))
        ultimo = await _movimentos_do_dia(session, alvo, date.fromisoformat(dias[-1]["dia"]))
        saldo_inicial = _totais_do_dia(primeiro)["saldo_inicial"]
        saldo_final = _totais_do_dia(ultimo)["saldo_informado"]

    # Fatura do ML: resumo do período (chave AAAA-MM-01) × soma dos detalhes lidos.
    chave = ini.strftime("%Y-%m-01")
    sync = (
        await session.execute(
            select(ConciliacaoSync).where(
                ConciliacaoSync.integration_id == alvo.integration_id,
                ConciliacaoSync.origem == "ml_fatura",
                ConciliacaoSync.chave == f"{chave}|resumo",
            )
        )
    ).scalar_one_or_none()
    resumo_ml = ((sync.detalhe or {}).get("resumo") if sync else None) or {}
    periodo = resumo_ml.get("period") or {}
    bill = resumo_ml.get("bill_includes") or {}
    fatura: dict[str, Any] = {
        "periodo": {
            "chave": chave,
            "de": periodo.get("date_from"),
            "ate": periodo.get("date_to"),
            "expira": periodo.get("expiration_date"),
        },
        "resumo_cobrancas": [
            {"tipo": c.get("type"), "label": c.get("label"), "valor": _num(c.get("amount"))}
            for c in (bill.get("charges") or [])
            if isinstance(c, dict)
        ],
        "resumo_bonificacoes": [
            {"tipo": c.get("type"), "label": c.get("label"), "valor": _num(c.get("amount"))}
            for c in (bill.get("bonuses") or [])
            if isinstance(c, dict)
        ],
        "resumo_total": _num(bill.get("total_amount")),
        "pago": resumo_ml.get("payment_collected") or {},
        "detalhes_por_sub_tipo": [],
        "detalhes_cobrancas": 0.0,
        "detalhes_bonificacoes": 0.0,
        "lido_em": sync.lido_em.isoformat() if sync and sync.lido_em else None,
    }
    if periodo.get("date_from") and periodo.get("date_to"):
        p_ini = datetime.combine(
            date.fromisoformat(periodo["date_from"]), datetime.min.time(), tzinfo=_BRT
        )
        p_fim = datetime.combine(
            date.fromisoformat(periodo["date_to"]), datetime.min.time(), tzinfo=_BRT
        ) + timedelta(days=1)
        det = (
            await session.execute(
                select(
                    mov.sub_tipo,
                    mov.tipo,
                    func.count(),
                    func.sum(mov.debito),
                    func.sum(mov.credito),
                )
                .where(
                    mov.integration_id == alvo.integration_id,
                    mov.origem == "ml_fatura",
                    mov.data >= p_ini,
                    mov.data < p_fim,
                )
                .group_by(mov.sub_tipo, mov.tipo)
                .order_by(func.sum(mov.debito).desc())
            )
        ).all()
        fatura["detalhes_por_sub_tipo"] = [
            {
                "sub_tipo": st,
                "tipo": t,
                "linhas": n,
                "cobrado": _num(d or 0),
                "bonificado": _num(c or 0),
            }
            for st, t, n, d, c in det
        ]
        fatura["detalhes_cobrancas"] = round(sum(float(d or 0) for _, _, _, d, _ in det), 2)
        fatura["detalhes_bonificacoes"] = round(sum(float(c or 0) for _, _, _, _, c in det), 2)
    soma_resumo = round(sum(x["valor"] or 0 for x in fatura["resumo_cobrancas"]), 2)
    fatura["bate"] = (
        (abs(soma_resumo - fatura["detalhes_cobrancas"]) <= 1.0)
        if fatura["resumo_cobrancas"]
        else None
    )

    bate = (
        abs(saldo_inicial + caixa["movimento_liquido"] - saldo_final) <= 0.05
        if (saldo_inicial is not None and saldo_final is not None)
        else None
    )
    return {
        "loja": alvo.conta,
        "mes": ini.isoformat()[:7],
        "de": ini.isoformat(),
        "ate": (fim - timedelta(days=1)).isoformat(),
        "vendas": vendas,
        "caixa": {
            **caixa,
            "saldo_inicial": saldo_inicial,
            "saldo_final": saldo_final,
            "bate": bate,
        },
        "dias": dias,
        "dias_fechados": sum(1 for d in dias if d["fechado_em"]),
        "fatura": fatura,
    }
