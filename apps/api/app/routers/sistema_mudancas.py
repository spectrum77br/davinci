"""Sistema › Mudanças: a tela de aprovação e as rotas do robô (regras em
`services/sistema_mudancas`).

  • /api/sistema-mudancas — a tela. Invisível para quem não está em
    `sistema_mudanca_aprovador` (Eduardo, 17/11/2026: "somente eu e o
    thorfinn"): para os outros, inclusive admin, toda rota daqui responde o
    404 de rota que não existe, com qualquer método — igual ao Histórico.
  • /api/sistema-mudancas-robo — o robô do Mac (apps/mudancas-robo), com
    cabeçalho X-Mudancas-Token = `mudancas_agent_token` (próprio: não é o
    token compartilhado do executor). Vazio no servidor = fechado (401).
"""

from __future__ import annotations

import json
import secrets
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.auth import get_current_user
from app.models import SistemaMudanca, User
from app.services import sistema_mudancas as svc

router = APIRouter(prefix="/api/sistema-mudancas", include_in_schema=False)
robo_router = APIRouter(prefix="/api/sistema-mudancas-robo", include_in_schema=False)

Sessao = Annotated[AsyncSession, Depends(get_session)]

# O diff inteiro vem no resultado: até ~400 mil letras + resumo e testes.
MAX_CORPO_ROBO = 2 * 1024 * 1024


def _nao_existe() -> HTTPException:
    return HTTPException(404, detail="Not Found")


async def _aprovador(
    user: Annotated[User | None, Depends(get_current_user)], session: Sessao
) -> User:
    if not await svc.pode_aprovar(session, user):
        raise _nao_existe()
    assert user is not None
    return user


Aprovador = Annotated[User, Depends(_aprovador)]


def _erro(exc: svc.MudancaError) -> HTTPException:
    codigo_http = (
        status.HTTP_422_UNPROCESSABLE_ENTITY
        if exc.code in ("motivo_invalido", "status_invalido", "campo_vazio", "campo_longo")
        else status.HTTP_409_CONFLICT
    )
    return HTTPException(codigo_http, detail={"code": exc.code, "detalhe": exc.detalhe})


async def _mudanca(
    session: AsyncSession, mudanca_id: str, *, travar: bool = False
) -> SistemaMudanca:
    """O pedido; `travar` = SELECT … FOR UPDATE (Aprovar e Recusar ao mesmo
    tempo: o segundo espera o primeiro e vê a situação nova)."""
    try:
        uid = UUID(mudanca_id)
    except ValueError:
        raise _nao_existe() from None
    q = select(SistemaMudanca).where(SistemaMudanca.id == uid)
    if travar:
        q = q.with_for_update()
    m = (await session.execute(q)).scalar_one_or_none()
    if m is None:
        raise _nao_existe()
    return m


async def _detalhe(session: AsyncSession, m: SistemaMudanca) -> dict[str, Any]:
    nomes = await svc.nomes(session, [m.pedido_por, m.aprovado_por, m.recusado_por])
    return svc.detalhe_dict(m, nomes, pode_aprovar_=True)


# ── a tela ────────────────────────────────────────────────────────────────


@router.get("")
async def listar(
    _user: Aprovador,
    session: Sessao,
    status_: Annotated[str | None, Query(alias="status")] = None,
    limite: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    try:
        itens = await svc.listar(session, status=status_ or None, limite=limite)
    except svc.MudancaError as exc:
        raise _erro(exc) from exc
    nomes = await svc.nomes(session, [m.pedido_por for m in itens])
    visto = await svc.robo_visto_em(session)
    return {
        "itens": [svc.resumo_dict(m, nomes) for m in itens],
        "robo_visto_em": visto.isoformat() if visto else None,
        "robo_parado": await svc.robo_parado(session),
    }


@router.get("/{mudanca_id}")
async def detalhe(mudanca_id: str, _user: Aprovador, session: Sessao) -> dict[str, Any]:
    return await _detalhe(session, await _mudanca(session, mudanca_id))


async def _corpo_da_tela(request: Request) -> dict[str, Any]:
    """O corpo lido À MÃO, depois da trava (um corpo errado de quem não é
    aprovador daria 422 e entregaria que a rota existe)."""
    try:
        dados = json.loads(await request.body() or b"{}")
    except ValueError:
        dados = {}
    return dados if isinstance(dados, dict) else {}


@router.post("/{mudanca_id}/aprovar")
async def aprovar(
    mudanca_id: str, request: Request, user: Aprovador, session: Sessao
) -> dict[str, Any]:
    dados = await _corpo_da_tela(request)
    m = await _mudanca(session, mudanca_id, travar=True)
    try:
        await svc.aprovar(session, m=m, user=user, diff_sha=dados.get("diff_sha"))
    except svc.MudancaError as exc:
        raise _erro(exc) from exc
    await session.commit()
    return await _detalhe(session, m)


@router.post("/{mudanca_id}/recusar")
async def recusar(
    mudanca_id: str, request: Request, user: Aprovador, session: Sessao
) -> dict[str, Any]:
    dados = await _corpo_da_tela(request)
    m = await _mudanca(session, mudanca_id, travar=True)
    try:
        await svc.recusar(session, m=m, user=user, motivo=dados.get("motivo"))
    except svc.MudancaError as exc:
        raise _erro(exc) from exc
    await session.commit()
    return await _detalhe(session, m)


@router.post("/{mudanca_id}/cancelar")
async def cancelar(
    mudanca_id: str, request: Request, user: Aprovador, session: Sessao
) -> dict[str, Any]:
    """Desfaz a aprovação de uma mudança cuja publicação ficou parada."""
    dados = await _corpo_da_tela(request)
    m = await _mudanca(session, mudanca_id, travar=True)
    try:
        await svc.cancelar(session, m=m, user=user, motivo=dados.get("motivo"))
    except svc.MudancaError as exc:
        raise _erro(exc) from exc
    await session.commit()
    return await _detalhe(session, m)


_TODOS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


@router.api_route("", methods=["POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
@router.api_route("/{resto:path}", methods=_TODOS)
async def _qualquer_outro(_user: Aprovador, resto: str = "") -> None:
    # Rota/método desconhecido: 404 (mesmo para o aprovador — sem 405/422 que
    # entreguem a existência da tela a quem não é).
    raise _nao_existe()


# ── o robô ────────────────────────────────────────────────────────────────


async def _token_do_robo(
    x_mudancas_token: Annotated[str | None, Header(alias="X-Mudancas-Token")] = None,
) -> None:
    esperado = get_settings().mudancas_agent_token
    if (
        not esperado
        or not x_mudancas_token
        or not secrets.compare_digest(x_mudancas_token.encode(), esperado.encode())
    ):
        # 404 igual a rota que não existe: quem não tem o token nem fica
        # sabendo que há rotas de robô aqui.
        raise _nao_existe()


Robo = Annotated[None, Depends(_token_do_robo)]


async def _corpo(request: Request) -> dict[str, Any]:
    tamanho = request.headers.get("content-length")
    if tamanho and tamanho.isdigit() and int(tamanho) > MAX_CORPO_ROBO:
        raise HTTPException(413, detail={"code": "corpo_grande"})
    partes: list[bytes] = []
    lido = 0
    async for parte in request.stream():
        lido += len(parte)
        if lido > MAX_CORPO_ROBO:
            raise HTTPException(413, detail={"code": "corpo_grande"})
        partes.append(parte)
    try:
        dados = json.loads(b"".join(partes) or b"{}")
    except ValueError:
        raise HTTPException(422, detail={"code": "json_invalido"}) from None
    if not isinstance(dados, dict):
        raise HTTPException(422, detail={"code": "json_invalido"})
    return dados


def _nome_robo(dados: dict[str, Any]) -> str:
    robo = str(dados.get("robo") or "").strip()
    if not robo:
        raise HTTPException(422, detail={"code": "sem_robo"})
    return robo[:120]


@robo_router.post("/pegar")
async def pegar(_r: Robo, request: Request, session: Sessao) -> dict[str, Any]:
    dados = await _corpo(request)
    m = await svc.pegar_trabalho(session, robo=_nome_robo(dados))
    if m is None:
        await session.commit()
        return {"trabalho": None}
    nomes = await svc.nomes(session, [m.pedido_por])
    trabalho = svc.trabalho_dict(m, nomes)
    await session.commit()
    return {"trabalho": trabalho}


@robo_router.post("/{mudanca_id}/vivo")
async def vivo(mudanca_id: str, _r: Robo, request: Request, session: Sessao) -> dict[str, Any]:
    dados = await _corpo(request)
    m = await _mudanca(session, mudanca_id)
    try:
        await svc.renovar(session, m=m, robo=_nome_robo(dados))
    except svc.MudancaError as exc:
        raise _erro(exc) from exc
    await session.commit()
    return {"ok": True, "status": m.status}


@robo_router.post("/{mudanca_id}/resultado")
async def resultado(
    mudanca_id: str, _r: Robo, request: Request, session: Sessao
) -> dict[str, Any]:
    dados = await _corpo(request)
    m = await _mudanca(session, mudanca_id)
    try:
        await svc.registrar_resultado(session, m=m, robo=_nome_robo(dados), dados=dados)
    except svc.MudancaError as exc:
        raise _erro(exc) from exc
    await session.commit()
    return {"ok": True, "codigo": svc.codigo(m), "status": m.status}


@robo_router.api_route("", methods=_TODOS)
@robo_router.api_route("/{resto:path}", methods=_TODOS)
async def _robo_outro(_r: Robo, resto: str = "") -> None:
    raise _nao_existe()
