"""Usuários › "Lojas no atendimento": as lojas de cada pessoa (30/10/2026).

O Eduardo separou as lojas do /atendimento por pessoa (a planilha "Separação de
contas": Vendedor 1 = londres, 2 = israel, 3 = marrocos) — as conversas, os
números, as caixas de e-mail das lojas e a Garantia. Só ADMIN lê e muda.

  GET /api/users/{user_id}/lojas-atendimento — as lojas para marcar
      (`escopo.lojas_para_vincular`), as chaves da pessoa e, por chave, quem
      mais tem a mesma loja (para não sobrar loja sem ninguém ou com dois).
  PUT /api/users/{user_id}/lojas-atendimento — troca as lojas da pessoa
      pelas `chaves` (cada uma de uma loja da lista ou já gravada nela).

Admin vê tudo sem linha nenhuma; quem não é admin e não tem loja não vê
nenhuma (`services/atendimento/escopo.py`). Só ids, nomes e plataformas.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_admin
from app.models import User, UserRole
from app.services.atendimento import escopo

logger = structlog.get_logger(__name__)

# Em /api/users (a tela de Usuários), fora de /api/atendimento: lá toda rota passa
# pela trava da caixa e quem só lê lê tudo; aqui é só admin.
router = APIRouter(prefix="/api/users", tags=["users"])

Session = Annotated[AsyncSession, Depends(get_session)]
Admin = Annotated[User, Depends(require_admin)]


class LojasIn(BaseModel):
    chaves: list[str] = Field(default_factory=list, max_length=1000)


async def _pessoa_ou_404(session: AsyncSession, user_id: UUID) -> User:
    pessoa = await session.get(User, user_id)
    if pessoa is None:
        raise HTTPException(404, detail={"code": "usuario_nao_encontrado"})
    return pessoa


def _nome(u: User) -> str:
    return (u.name or u.email or "").strip() or str(u.id)


async def _resposta(session: AsyncSession, pessoa: User) -> dict[str, Any]:
    lojas = await escopo.lojas_para_vincular(session)
    todas = await escopo.vinculos(session)
    outros_ids = {uid for uid in todas if uid != pessoa.id}
    nomes = (
        {
            u.id: _nome(u)
            for u in (await session.execute(select(User).where(User.id.in_(outros_ids)))).scalars()
        }
        if outros_ids
        else {}
    )
    outros: dict[str, list[str]] = {}
    for uid, chaves in todas.items():
        if uid == pessoa.id:
            continue
        for c in chaves:
            outros.setdefault(c, []).append(nomes.get(uid, str(uid)))
    return {
        "user_id": str(pessoa.id),
        "admin": pessoa.role == UserRole.ADMIN,
        "chaves": todas.get(pessoa.id, []),
        "lojas": lojas,
        "outros": {c: sorted(n) for c, n in outros.items()},
    }


@router.get("/{user_id}/lojas-atendimento")
async def lojas_da_pessoa(user_id: UUID, session: Session, user: Admin) -> dict[str, Any]:
    return await _resposta(session, await _pessoa_ou_404(session, user_id))


@router.put("/{user_id}/lojas-atendimento")
async def salvar_lojas(
    user_id: UUID, body: LojasIn, session: Session, user: Admin
) -> dict[str, Any]:
    pessoa = await _pessoa_ou_404(session, user_id)
    chaves = {c.strip().lower() for c in body.chaves}
    invalidas = sorted(c for c in chaves if not escopo.chave_valida(c))
    if invalidas:
        raise HTTPException(422, detail={"code": "chave_invalida", "chaves": invalidas[:20]})
    conhecidas = {c for loja in await escopo.lojas_para_vincular(session) for c in loja["chaves"]}
    conhecidas |= await escopo.chaves_da_pessoa(session, pessoa.id)
    desconhecidas = sorted(chaves - conhecidas)
    if desconhecidas:
        raise HTTPException(422, detail={"code": "loja_desconhecida", "chaves": desconhecidas[:20]})
    antes = await escopo.chaves_da_pessoa(session, pessoa.id)
    await escopo.salvar(session, pessoa.id, chaves, user)
    await session.commit()
    logger.info(
        "atendimento_lojas_da_pessoa",
        pessoa=str(pessoa.id),
        autor=str(user.id),
        entraram=len(chaves - antes),
        sairam=len(antes - chaves),
        total=len(chaves),
    )
    return await _resposta(session, pessoa)
