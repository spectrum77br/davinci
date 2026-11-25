"""Produtos 2 › estoque do produto › "Incluir" (M-43, Harry 24/11/2026) — só admin.

Entrada avulsa de estoque lançada direto no Bling, pela API do DaVinci (fora de /api/erp-local:
a trava do modo Consulta do ERP não vale aqui, de propósito). Regras e o passo a passo em
`services/estoque_entrada.py`.

  POST /api/estoque/entradas         grava e lança (Idempotency-Key obrigatório; a mesma chave
                                     só repete a entrada até 15 min depois — então 409
                                     `idempotency_velha`; custo ilegível no Bling → 503
                                     `custo_indisponivel`, nada gravado)
  GET  /api/estoque/entradas?sku=    últimas entradas (do produto), mais recente primeiro
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_admin
from app.models import EstoqueEntrada, User
from app.services import estoque_entrada as svc

logger = structlog.get_logger()

router = APIRouter(prefix="/api/estoque/entradas", tags=["estoque"])

Admin = Annotated[User, Depends(require_admin)]
Sessao = Annotated[AsyncSession, Depends(get_session)]


class EntradaIn(BaseModel):
    sku: str = Field(..., max_length=80)
    quantidade: int
    observacao: str | None = Field(None, max_length=500)


class EntradaOut(BaseModel):
    id: UUID
    sku: str
    quantidade: int
    estado: str
    texto: str


class EntradaListaOut(EntradaOut):
    observacao: str | None
    criado_em: datetime
    criado_por: str | None


def _out(e: EstoqueEntrada) -> EntradaOut:
    estado, texto = svc.situacao(e)
    return EntradaOut(id=e.id, sku=e.sku, quantidade=e.quantidade, estado=estado, texto=texto)


def _erro(exc: svc.EntradaInvalidaError) -> HTTPException:
    return HTTPException(exc.status, detail={"code": exc.code, "message": exc.message})


async def _client(session: AsyncSession):
    from app.services.devolution_stock_return import _get_bling_client

    client = await _get_bling_client(session)
    if client is None:
        raise HTTPException(
            503, detail={"code": "bling_not_connected", "message": "Bling não conectado."}
        )
    return client


async def _recarregar(session: AsyncSession, entrada_id: UUID) -> EstoqueEntrada:
    return (
        await session.execute(
            select(EstoqueEntrada)
            .where(EstoqueEntrada.id == entrada_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


@router.post("", response_model=EntradaOut)
async def incluir(
    body: EntradaIn,
    session: Sessao,
    user: Admin,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> EntradaOut:
    chave = (idempotency_key or "").strip()
    if not (8 <= len(chave) <= 100):
        raise HTTPException(
            422, detail={"code": "idempotency_key", "message": "Chave da tela ausente."}
        )
    if not (1 <= body.quantidade <= svc.MAX_QUANTIDADE):
        raise HTTPException(
            422,
            detail={
                "code": "quantidade",
                "message": f"Quantidade precisa ser um número inteiro de 1 a {svc.MAX_QUANTIDADE}.",
            },
        )
    # A tela repetiu o POST (clique duplo, rede): a mesma entrada, sem lançar de novo — nem
    # precisa do Bling para responder. Chave de entrada criada há mais de 15 min, ou com outro
    # pedido: 409, nada lançado (`svc.conferir_repeticao`).
    existente = await svc.por_idem(session, chave)
    if existente is not None:
        try:
            svc.conferir_repeticao(existente, body.sku, body.quantidade)
        except svc.EntradaInvalidaError as exc:
            raise _erro(exc) from exc
        return _out(existente)
    client = await _client(session)
    try:
        e, nova = await svc.criar(
            session,
            client,
            idem_key=chave,
            user_id=user.id,
            user_nome=user.name or user.email,
            sku=body.sku,
            quantidade=body.quantidade,
            observacao=body.observacao,
        )
    except svc.EntradaInvalidaError as exc:
        await session.rollback()
        raise _erro(exc) from exc
    if nova:
        logger.info(
            "estoque_entrada_criada",
            numero=e.numero,
            user=str(user.id),
            sku=e.sku,
            qtd=e.quantidade,
        )
        await svc.executar(client, e.id)
    return _out(await _recarregar(session, e.id))


@router.get("", response_model=list[EntradaListaOut])
async def listar(
    session: Sessao,
    _u: Admin,
    sku: Annotated[str | None, Query(max_length=80)] = None,
    limite: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[EntradaListaOut]:
    q = select(EstoqueEntrada)
    if sku and sku.strip():
        q = q.where(func.lower(EstoqueEntrada.sku) == svc.norm(sku))
    rows = (
        (await session.execute(q.order_by(EstoqueEntrada.created_at.desc()).limit(limite)))
        .scalars()
        .all()
    )
    out = []
    for e in rows:
        estado, texto = svc.situacao(e)
        out.append(
            EntradaListaOut(
                id=e.id,
                sku=e.sku,
                quantidade=e.quantidade,
                estado=estado,
                texto=texto,
                observacao=e.observacao,
                criado_em=e.created_at,
                criado_por=e.criado_por_nome,
            )
        )
    return out
