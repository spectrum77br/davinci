"""As métricas por EQUIPE e a análise do dia (13/11/2026).

A aba Métricas do atendimento (`services/atendimento/metricas_equipe.py`): as % por equipe
(a divisão da planilha), as lojas todas no select, e a análise que o agente escreve uma vez
por dia. Só admin (o dono vê as equipes).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_admin
from app.models import User
from app.routers.atendimento import _nomes_das_lojas, _so_admin, _turnos_das_lojas
from app.services.atendimento import metricas_equipe
from app.services.atendimento.escopo import escopo_do_atendimento

# A mesma trava da caixa das outras rotas do atendimento e, por rota, só admin.
router = APIRouter(
    prefix="/api/atendimento/metricas", tags=["atendimento"], dependencies=[Depends(_so_admin)]
)


@router.get("/equipes")
async def por_equipe(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_admin)],
    dias: Annotated[int, Query(ge=1, le=90)] = 7,
) -> dict:
    """As equipes com o que saiu sozinho, o tempo médio de resposta, o no prazo e a aprovação
    da IA; "sem equipe"; as lojas (16/11/2026: a conta de turnos do GET /metricas, do admin)."""
    agora = datetime.now(UTC)
    scope = await escopo_do_atendimento(session, user)
    turnos = await _turnos_das_lojas(session, scope, agora - timedelta(days=dias), agora)
    nomes = await _nomes_das_lojas(session, {integ for integ, _, _ in turnos})
    return await metricas_equipe.resumo(
        session, dias=dias, agora=agora, turnos=turnos, nomes_das_lojas=nomes
    )


@router.get("/analise")
async def analise(_user: Annotated[User, Depends(require_admin)]) -> dict:
    """A análise mais recente do agente (`{"analise": null}` antes da primeira)."""
    return {"analise": await metricas_equipe.ultima_analise()}
