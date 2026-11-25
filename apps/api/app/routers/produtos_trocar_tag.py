"""Produtos 2 › Configuração › "Tag" (M-46, Harry 24/11/2026) — só admin.

Troca a tag (= o fim do código) dos produtos marcados, no Bling, por `PATCH /produtos/{id}`
`{"codigo": novo}`. Regras, motivos de pular e o passo a passo em
`services/produto_troca_tag.py`.

  POST /api/produtos/trocar-tag/previa   o que será alterado e o que será pulado (não grava)
  POST /api/produtos/trocar-tag          recalcula no servidor e troca (Idempotency-Key)
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_admin
from app.models import User
from app.services import produto_troca_tag as svc

logger = structlog.get_logger()

router = APIRouter(prefix="/api/produtos/trocar-tag", tags=["products"])

Admin = Annotated[User, Depends(require_admin)]
Sessao = Annotated[AsyncSession, Depends(get_session)]


class TrocarTagIn(BaseModel):
    # O limite de 200 é conferido no serviço (erro no formato da tela); aqui só um teto.
    skus: list[Annotated[str, Field(max_length=80)]] = Field(..., max_length=1000)
    tag: str = Field(..., max_length=60)


class ItemPreviaOut(BaseModel):
    sku: str
    novo: str | None
    acao: str
    motivo: str | None


class PreviaOut(BaseModel):
    itens: list[ItemPreviaOut]
    alterar: int
    pular: int


class ItemOut(BaseModel):
    sku: str
    novo: str | None
    # ok | pulado | falhou (o contrato da tela: "sem_confirmacao" conta como
    # falhou, com o motivo dizendo que o DaVinci confere de novo).
    estado: str
    motivo: str | None
    # O estado gravado, sem juntar: ok | pulado | falhou | sem_confirmacao |
    # pendente | enviando (campo a mais; a tela atual não usa).
    situacao: str


class TrocaOut(BaseModel):
    itens: list[ItemOut]
    ok: int
    pulado: int
    falhou: int
    # Quantos dos "falhou" ainda estão sem confirmação (o worker reconfere).
    sem_confirmacao: int = 0


async def _client(session: AsyncSession):
    from app.services.devolution_stock_return import _get_bling_client

    client = await _get_bling_client(session)
    if client is None:
        raise HTTPException(
            503, detail={"code": "bling_not_connected", "message": "Bling não conectado."}
        )
    return client


def _erro(exc: svc.TrocaInvalidaError) -> HTTPException:
    return HTTPException(exc.status, detail={"code": exc.code, "message": exc.message})


@router.post("/previa", response_model=PreviaOut)
async def previa(body: TrocarTagIn, session: Sessao, _u: Admin) -> PreviaOut:
    try:
        await svc.validar_pedido(session, body.skus, body.tag)
    except svc.TrocaInvalidaError as exc:
        raise _erro(exc) from exc
    client = await _client(session)
    itens = await svc.previa(session, client, body.skus, body.tag)
    out = [ItemPreviaOut(sku=i.sku, novo=i.novo, acao=i.acao, motivo=i.motivo) for i in itens]
    alterar = sum(1 for i in itens if i.acao == svc.ALTERAR)
    return PreviaOut(itens=out, alterar=alterar, pular=len(itens) - alterar)


@router.post("", response_model=TrocaOut)
async def trocar(
    body: TrocarTagIn,
    session: Sessao,
    user: Admin,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TrocaOut:
    chave = (idempotency_key or "").strip()
    if not (8 <= len(chave) <= 100):
        raise HTTPException(
            422, detail={"code": "idempotency_key", "message": "Chave da tela ausente."}
        )
    try:
        await svc.validar_pedido(session, body.skus, body.tag)
    except svc.TrocaInvalidaError as exc:
        raise _erro(exc) from exc
    # A mesma chave já trocou: devolve o resultado sem precisar do Bling.
    existente = await svc.por_idem(session, chave)
    client = None if existente is not None else await _client(session)
    try:
        troca = await svc.executar(
            session,
            client,
            idem_key=chave,
            user_id=user.id,
            user_nome=user.name or user.email,
            skus=body.skus,
            tag=body.tag,
        )
    except svc.TrocaInvalidaError as exc:
        await session.rollback()
        raise _erro(exc) from exc
    itens = await svc.itens_da_troca(session, troca.id)
    out = [
        ItemOut(
            sku=i.sku_antigo,
            novo=i.sku_novo,
            estado=svc.estado_na_tela(i.estado),
            motivo=i.motivo,
            situacao=i.estado,
        )
        for i in itens
    ]
    return TrocaOut(
        itens=out,
        ok=sum(1 for i in out if i.estado == svc.OK),
        pulado=sum(1 for i in out if i.estado == svc.PULADO),
        falhou=sum(1 for i in out if i.estado == svc.FALHOU),
        sem_confirmacao=sum(1 for i in out if i.situacao == svc.SEM_CONFIRMACAO),
    )
