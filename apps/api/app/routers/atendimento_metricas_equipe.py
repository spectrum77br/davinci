"""As métricas por EQUIPE e a análise do dia (13/11/2026).

A aba Métricas do atendimento (`services/atendimento/metricas_equipe.py`): as % por equipe
(a divisão da planilha), as lojas todas no select, e a análise que o agente escreve uma vez
por dia. Só admin (o dono vê as equipes).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_admin
from app.models import User
from app.services.atendimento import metricas_equipe

router = APIRouter(prefix="/api/atendimento/metricas", tags=["atendimento"])


@router.get("/equipes")
async def por_equipe(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_admin)],
    dias: Annotated[int, Query(ge=1, le=90)] = 7,
) -> dict:
    """As equipes com a aprovação da IA, o que saiu sozinho e o que a equipe mandou; as lojas."""
    return await metricas_equipe.resumo(session, dias=dias)


@router.get("/analise")
async def analise(_user: Annotated[User, Depends(require_admin)]) -> dict:
    """A análise mais recente do agente (`{"analise": null}` antes da primeira)."""
    return {"analise": await metricas_equipe.ultima_analise()}
