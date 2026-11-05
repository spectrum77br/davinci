"""Atendimento › o brinde do pedido não pago (o dono, 05/11/2026).

  GET  /api/atendimento/brindes/pendentes
      "Brindes a adicionar": os pedidos pagos com oferta enviada e sem decisão, e
      os alertas dos que expiraram nas últimas 48 h. Sem Bling. O comprador que
      paga não escreve no chat: sem esta lista, ninguém abriria a conversa.
  POST /api/atendimento/brindes/{integration_id}/{pedido}/adicionar
      o botão (`services/atendimento/brinde.py`): 200 com `ok` (False = o Bling
      não respondeu: `incerto`, o clique seguinte confere); 409 `{code, detail}`
      numa trava (nada mudou); 404 sem oferta ou fora das lojas da pessoa.
  POST /api/atendimento/brindes/{integration_id}/{pedido}/dispensar
      "Não adicionar": solta a NF.

ACESSO: a MESMA trava da caixa (`_so_admin`). Ler = `_view`. Escrever = quem
responde (`_responde`; as duas rotas estão em `ROTAS_DE_QUEM_RESPONDE`), como a
troca de produto, só nas lojas da pessoa (`escopo_do_atendimento`). `{pedido}` é o
nº na plataforma; fora do escopo é 404, como o que não existe.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import User
from app.routers.atendimento import _responde, _so_admin, _view
from app.services.atendimento import brinde
from app.services.atendimento.escopo import escopo_do_atendimento

router = APIRouter(
    prefix="/api/atendimento", tags=["atendimento"], dependencies=[Depends(_so_admin)]
)


class BrindeAcaoIn(BaseModel):
    # Vazio = o lote do item vendido, conferido ao vivo no Bling.
    sku: str | None = Field(default=None, max_length=64)


class BrindeResultadoOut(BaseModel):
    ok: bool
    estado: str | None = None
    sku: str | None = None
    numero_bling: str | None = None
    mensagem: str
    ja_estava: bool = False


class BrindePendenteOut(BaseModel):
    integration_id: UUID
    pedido: str
    numero_bling: str | None = None
    plataforma: str | None = None
    loja: str | None = None
    tipo: str | None = None
    rotulo: str | None = None
    sku_sugerido: str | None = None
    horas: float | None = None
    horas_restantes: float | None = None
    conversa_id: UUID | None = None
    estado: str | None = None
    erro: str | None = None
    pode_adicionar: bool = False
    motivo: str | None = None


class BrindeAlertaOut(BaseModel):
    integration_id: UUID
    pedido: str
    plataforma: str | None = None
    loja: str | None = None
    rotulo: str | None = None
    horas: float | None = None
    conversa_id: UUID | None = None
    mensagem: str


class BrindesPendentesOut(BaseModel):
    total: int = 0
    pendentes: list[BrindePendenteOut] = Field(default_factory=list)
    alertas: list[BrindeAlertaOut] = Field(default_factory=list)


def _recusa(e: brinde.BrindeRecusado) -> HTTPException:
    return HTTPException(e.status, detail={"code": e.code, "detail": e.detail})


async def _no_escopo(session: AsyncSession, user: User, integration_id: UUID) -> None:
    """A loja é da pessoa? Fora = 404, como o que não existe."""
    scope = await escopo_do_atendimento(session, user)
    if not scope.unrestricted and integration_id not in scope.integration_ids:
        raise HTTPException(
            404, detail={"code": "sem_oferta", "detail": brinde.TEXTOS["sem_oferta"]}
        )


@router.get("/brindes/pendentes", response_model=BrindesPendentesOut)
async def brindes_pendentes(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_view)],
) -> dict[str, Any]:
    """Os brindes a adicionar nas lojas da pessoa. Só leitura, sem Bling."""
    scope = await escopo_do_atendimento(session, user)
    return await brinde.pendentes(session, scope, user)


@router.post("/brindes/{integration_id}/{pedido}/adicionar", response_model=BrindeResultadoOut)
async def adicionar_brinde(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_responde)],
    integration_id: UUID,
    pedido: Annotated[str, Path(min_length=1, max_length=64)],
    body: Annotated[BrindeAcaoIn | None, Body()] = None,
) -> dict[str, Any]:
    """O botão: GET ao vivo, um PUT, e o estado de verdade."""
    await _no_escopo(session, user, integration_id)
    try:
        return await brinde.adicionar(
            session, integration_id, pedido, user, sku=body.sku if body else None
        )
    except brinde.BrindeRecusado as e:
        raise _recusa(e) from e


@router.post("/brindes/{integration_id}/{pedido}/dispensar", response_model=BrindeResultadoOut)
async def dispensar_brinde(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_responde)],
    integration_id: UUID,
    pedido: Annotated[str, Path(min_length=1, max_length=64)],
) -> dict[str, Any]:
    """ "Não adicionar": solta a NF, sem falar com o Bling."""
    await _no_escopo(session, user, integration_id)
    try:
        return await brinde.dispensar(session, integration_id, pedido, user)
    except brinde.BrindeRecusado as e:
        raise _recusa(e) from e
