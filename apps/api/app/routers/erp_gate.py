"""Porta de entrada do ERP próprio: o Caddy pergunta aqui quem é a pessoa.

Por enquanto só administrador entra (v1 em modo consulta, com o Bling mandando). O que cada um
pode fazer lá dentro é decidido pelo próprio ERP, nos perfis de acesso.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.deps.auth import require_admin
from app.models import User

router = APIRouter(prefix="/api/erp-gate", tags=["erp"])


@router.get("/authorize", status_code=204)
async def authorize(user: Annotated[User, Depends(require_admin)]) -> Response:
    return Response(
        status_code=204, headers={"X-ERP-Actor": user.email or str(user.id), "X-ERP-Role": "admin"}
    )
