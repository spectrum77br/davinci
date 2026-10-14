"""Pós-venda › Atendimento: marcar a conversa como LIDA ao abrir (14/10/2026).

  POST /api/atendimento/conversas/{id}/lida

Decisão do dono (Eduardo, 14/10/2026): "ao passar também tem que marcar como
visto, igual lá no Duoke quando clicar na conversa". A tela chama esta rota
quando a PESSOA abre a conversa (depois de 1,5 s com ela aberta e a aba
visível, uma vez por abertura — `apps/web/lib/atendimentoLida.ts`) e só se o
detalhe disse `marcar_lida = true`. A regra inteira (quem, quais lojas, quais
plataformas, por que a IA e os crons nunca) mora em
`services/atendimento/lida.py`.

Respostas:
  - 403 `atendimento_so_leitura` — quem só lê (a trava da caixa, `_so_admin`,
    e de novo no serviço);
  - 409 com o `code` — não é para marcar esta conversa, nada foi à plataforma:
    `canal_em_observacao` (a loja em observar: o Duoke responde),
    `envio_desligado`, `nao_se_aplica` (plataforma sem "lida": Amazon, e-mail,
    robô, Magalu, site, redes, Direct, pergunta do ML), `sem_integracao`;
  - 200 `{marcada, motivo, aviso, conversa}` — `marcada = true`: a plataforma
    aceitou e `nao_lidas` zerou (ou `motivo = chegou_mensagem`: marcou, mas
    chegou mensagem no meio e o número fica com o sync). `marcada = false`:
    `ja_lida` (nada a fazer — idempotente), `em_andamento` (outra aba já está
    marcando) ou `plataforma_falhou`/`sem_mensagem` com o `aviso` — a conversa
    continua não lida lá, no Duoke e aqui. Falha da plataforma NÃO é erro HTTP:
    a tela segue.

A MESMA TRAVA de acesso do router do atendimento (`_so_admin`), a permissão de
escrita (`_edit`) e o escopo por equipe (`_conversa_ou_404`). Router à parte;
o `main.py` o inclui.
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.team_scope import resolve_team_scope
from app.models import User
from app.routers.atendimento import (
    SO_LEITURA,
    _conversa_ou_404,
    _conversa_out,
    _edit,
    _so_admin,
)
from app.schemas.atendimento import ConversaOut
from app.services.atendimento import instagram
from app.services.atendimento import lida as lida_svc

logger = structlog.get_logger()

router = APIRouter(
    prefix="/api/atendimento", tags=["atendimento"], dependencies=[Depends(_so_admin)]
)


class LidaOut(BaseModel):
    """O que aconteceu ao marcar como lida (a tela só atualiza o número)."""

    marcada: bool
    # ja_lida | em_andamento | plataforma_falhou | sem_mensagem | chegou_mensagem
    motivo: str | None = None
    # A frase para a pessoa quando a plataforma não marcou (a tela não mostra
    # toast por isso: fica no title/log — a conversa segue não lida).
    aviso: str | None = None
    conversa: ConversaOut | None = None


@router.post("/conversas/{conversa_id}/lida", response_model=LidaOut)
async def marcar_lida(
    conversa_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> LidaOut:
    """Marca a conversa como lida NA PLATAFORMA (e zera `nao_lidas` se ela aceitou)."""
    if instagram.e_instagram(conversa_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": lida_svc.RECUSA_NAO_SE_APLICA, "detail": lida_svc.MOTIVO_NAO_SE_APLICA},
        )
    scope = await resolve_team_scope(session, user)
    c = await _conversa_ou_404(session, conversa_id, scope)
    try:
        r = await lida_svc.marcar_como_lida(session, c, user=user)
    except lida_svc.LidaRecusada as e:
        logger.info(
            "atendimento_lida_recusada", conversa_id=str(c.id), code=e.code, user_id=str(user.id)
        )
        if e.code == lida_svc.RECUSA_SO_LEITURA:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=SO_LEITURA) from e
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail={"code": e.code, "detail": e.detail}
        ) from e
    return LidaOut(
        marcada=r.marcada,
        motivo=r.motivo,
        aviso=r.aviso,
        conversa=await _conversa_out(session, c),
    )
