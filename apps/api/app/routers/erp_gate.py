"""Porta de entrada do DaVinci ERP: o Caddy pergunta aqui quem é a pessoa.

Por enquanto só thorfinn e heisenberg (services/erp_acesso), em modo consulta enquanto o Bling
manda. O que cada um pode fazer lá dentro é decidido pelo próprio ERP, nos perfis de acesso.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.deps.auth import require_active_user
from app.models import User
from app.services import erp_acesso

router = APIRouter(prefix="/api/erp-gate", tags=["erp"])


@router.get("/authorize", status_code=204)
async def authorize(user: Annotated[User, Depends(require_active_user)]) -> Response:
    if not erp_acesso.liberado(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "erp_sem_acesso"})
    return Response(
        status_code=204, headers={"X-ERP-Actor": user.email or str(user.id), "X-ERP-Role": "admin"}
    )
