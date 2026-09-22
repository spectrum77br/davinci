"""Imagem pública por link (sem login).

Vinicius, 22/09/2026: "sobe essa imagem no servidor e me passa o link dela… no
banco de dados". O painel inteiro exige login, então um link autenticado não
serviria pro uso que ela tem (colar num aviso, numa mensagem ao cliente, num
cartão que o robô manda). Aqui o link é aberto, como o dossiê do jurídico
(routers/chamados.py:785) — a proteção é o id ser UUID e só entrar aqui o que
pode ser público.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import ImagemPublica

router = APIRouter(prefix="/api/imagens", tags=["imagens"])


@router.get("/{imagem_id}")
async def servir_imagem(
    imagem_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """PÚBLICO: devolve a imagem guardada no banco. Cache longo — o conteúdo de
    um id nunca muda (imagem nova é linha nova)."""
    row = (
        await session.execute(select(ImagemPublica).where(ImagemPublica.id == imagem_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "imagem_not_found"})
    return Response(
        content=row.blob,
        media_type=row.content_type or "image/png",
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "Content-Disposition": f'inline; filename="{row.nome}"',
        },
    )
