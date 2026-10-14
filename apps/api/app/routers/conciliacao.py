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

import httpx
import structlog
from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission_any
from app.models import User
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
