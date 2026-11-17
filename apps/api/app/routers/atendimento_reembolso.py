"""Atendimento › o reembolso ao cliente pela conversa (Novos procedimentos 3/6, 17/11/2026).

  GET  /api/atendimento/conversas/{conversa_id}/reembolsos
      o bloco "Reembolso ao cliente" da aba Pedido: o pedido da conversa (nº do
      Bling e da plataforma, a conta como na tela Reembolso, o valor pago), se
      a plataforma já reembolsou, o limite do ADM, se a pessoa lança (e o
      porquê de não), se aprova, e os reembolsos que o pedido já tem (qualquer
      tipo, com a situação). Os dados do Pix só para ADM. Só leitura.
  POST /api/atendimento/conversas/{conversa_id}/reembolsos
      "Lançar reembolso ao cliente" (`LancarReembolsoIn`): 201 `{item}`; 403
      `sem_permissao` / `so_adm_plataforma_reembolsou`; 409 `sem_pedido`; 422
      `detalhe_obrigatorio` / `pix_sem_dados` / `valor_acima_do_pago`.
  POST /api/atendimento/reembolsos/{refund_id}/aprovar
      o ADM aprova o que aguarda: 200 `{item}`; 403 para os outros; 409
      `ja_aprovado`; 404 sem o reembolso.

As regras estão em `services/atendimento/reembolso_conversa.py` (a conversa) e
`services/reembolso_cliente.py` (o limite, a situação e o Pix, que a tela
Reembolso também usa).

ACESSO: a MESMA trava da caixa (`_so_admin`). Ler = `_view`, no escopo da
pessoa (`_conversa_ou_404`: conversa de loja fora das dela é 404). As duas
escritas estão em `ROTAS_DE_QUEM_RESPONDE` (passam pela trava para quem
responde, como a troca e o brinde) e a rota confere o resto: lançar = ADM ou
`reembolso.edit` (decisão 4 do dono); aprovar = só ADM — qualquer admin, também
quem paga e não está na lista de quem mexe na caixa. O corpo do POST não vai
para o Histórico (`historico/nomes.SEM_CORPO` cobre `/atendimento/conversas/`).
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import User
from app.routers.atendimento import _conversa_ou_404, _responde, _so_admin, _view
from app.schemas.atendimento_reembolso import (
    LancarReembolsoIn,
    ReembolsoItemRespostaOut,
    ReembolsosDaConversaOut,
)
from app.services.atendimento import instagram, reembolso_conversa
from app.services.atendimento.escopo import escopo_do_atendimento

router = APIRouter(
    prefix="/api/atendimento", tags=["atendimento"], dependencies=[Depends(_so_admin)]
)


def _recusa(e: reembolso_conversa.ReembolsoRecusado) -> HTTPException:
    return HTTPException(e.status, detail={"code": e.code, "detail": e.detail})


@router.get("/conversas/{conversa_id}/reembolsos", response_model=ReembolsosDaConversaOut)
async def reembolsos_da_conversa(
    conversa_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_view)],
) -> dict[str, Any]:
    """O bloco "Reembolso ao cliente" da aba Pedido. Só leitura."""
    if instagram.e_instagram(conversa_id):
        # O Direct do Instagram não tem pedido (nem loja de marketplace).
        return reembolso_conversa.sem_pedido(user)
    scope = await escopo_do_atendimento(session, user)
    conversa = await _conversa_ou_404(session, conversa_id, scope)
    bloco = await reembolso_conversa.bloco_da_conversa(session, conversa, user)
    await session.rollback()  # só leitura: nada a gravar
    return bloco


@router.post(
    "/conversas/{conversa_id}/reembolsos",
    response_model=ReembolsoItemRespostaOut,
    status_code=status.HTTP_201_CREATED,
)
async def lancar_reembolso(
    conversa_id: str,
    body: LancarReembolsoIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_responde)],
) -> dict[str, Any]:
    """ "Lançar reembolso ao cliente": o pedido, a conta e a data saem da conversa."""
    scope = await escopo_do_atendimento(session, user)
    conversa = await _conversa_ou_404(session, conversa_id, scope)
    try:
        item = await reembolso_conversa.lancar(
            session,
            conversa,
            user,
            valor=body.valor,
            motivo=body.motivo,
            motivo_detalhe=body.motivo_detalhe,
            forma=body.forma,
            dados_pagamento=body.dados_pagamento,
        )
    except reembolso_conversa.ReembolsoRecusado as e:
        raise _recusa(e) from e
    return {"item": item}


@router.post("/reembolsos/{refund_id}/aprovar", response_model=ReembolsoItemRespostaOut)
async def aprovar_reembolso(
    refund_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_responde)],
) -> dict[str, Any]:
    """O ADM aprova o reembolso que aguarda (acima do limite)."""
    try:
        item = await reembolso_conversa.aprovar(session, refund_id, user)
    except reembolso_conversa.ReembolsoRecusado as e:
        raise _recusa(e) from e
    return {"item": item}
