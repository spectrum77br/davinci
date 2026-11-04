"""Pós-venda › Atendimento: o cartão da reclamação (RF2, 01/10/2026).

  GET /api/atendimento/conversas/{id}/reclamacoes

As reclamações, mediações e devoluções da PLATAFORMA ligadas à conversa —
pela `conversa_id` que o leitor gravou OU pelo pedido da conversa (a conversa
do pack e a conversa `reclamacao` do mesmo pedido mostram o mesmo cartão).
Abertas primeiro, prazo mais curto antes; depois as encerradas. Quem lê e
grava é `services/atendimento/reclamacoes.py` (ML) e
`reclamacoes_devolucoes.py` (Shopee/TikTok, pela Logística).

SÓ LEITURA: nenhuma ação na plataforma sai daqui (aceitar devolução, oferecer
solução e pedir mediação ficam para depois, com confirmação e auditoria). O
"Abrir na plataforma" é um link.

A MESMA TRAVA de acesso do router do atendimento (`_so_admin`: na
observação, toda a equipe lê e só ATENDIMENTO_USUARIOS mexe) e a mesma
permissão de leitura (`_view`) e escopo por equipe (`_conversa_ou_404`).
Router à parte para não mexer no do atendimento: o `main.py` o inclui.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import User
from app.routers.atendimento import _conversa_ou_404, _so_admin, _view
from app.services.atendimento import foco_devolucao, instagram, reclamacoes
from app.services.atendimento.constantes import CANAL_RECLAMACAO
from app.services.atendimento.escopo import escopo_do_atendimento

router = APIRouter(
    prefix="/api/atendimento", tags=["atendimento"], dependencies=[Depends(_so_admin)]
)


class ReclamacaoOut(BaseModel):
    id: UUID
    plataforma: str
    plataforma_nome: str
    # Nº na plataforma (claim_id do ML, return_sn da Shopee, return_id da
    # TikTok); None quando a Logística ainda não tem o id do caso.
    numero: str | None
    # reclamacao | mediacao | devolucao
    tipo: str
    tipo_rotulo: str
    # O status cru da plataforma e o mesmo em português.
    status: str | None
    status_rotulo: str | None
    aberta: bool
    # O motivo da PLATAFORMA (nunca texto do comprador).
    motivo: str | None
    # O que o comprador pede (Shopee/TikTok: "Devolução + reembolso", "Só reembolso").
    solucao: str | None = None
    pedido_marketplace: str | None
    # Até quando a loja tem de agir (só das abertas).
    prazo_em: datetime | None
    aberta_em: datetime | None
    encerrada_em: datetime | None
    # O que a plataforma espera da loja, em português.
    acao_pendente: str | None
    # O ML mediando (o "Com Meli").
    mediacao: bool
    # A reclamação conta na reputação (ML)? None = não se sabe.
    reputacao_afetada: bool | None
    # Status da devolução do ML ligada à reclamação (cru).
    devolucao_status: str | None
    conversa_id: UUID | None
    url_plataforma: str | None
    # O bloco "Devolução" no painel do pedido (04/11/2026): caso ABERTO da Shopee
    # do pedido da conversa pedida. True = a tela põe o bloco e encolhe o cartão.
    do_pedido: bool = False
    # `refund_amount` e `currency` do detalhe; o rastreio e a situação da volta,
    # de `devolucao_rastreio`. Sem o dado, None: a tela não desenha a linha.
    valor_reembolso: float | None = None
    moeda: str | None = None
    rastreio_volta: str | None = None
    situacao_volta: str | None = None


class ReclamacoesOut(BaseModel):
    itens: list[ReclamacaoOut]
    abertas: int
    # O prazo mais curto entre as abertas (o cartão conta o tempo até ele).
    prazo_mais_curto: datetime | None


@router.get("/conversas/{conversa_id}/reclamacoes", response_model=ReclamacoesOut)
async def reclamacoes_da_conversa(
    conversa_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_view)],
) -> ReclamacoesOut:
    """As reclamações e devoluções da plataforma ligadas à conversa (cartão, só leitura)."""
    if instagram.e_instagram(conversa_id):
        # DM do Instagram não tem pedido nem reclamação.
        return ReclamacoesOut(itens=[], abertas=0, prazo_mais_curto=None)
    scope = await escopo_do_atendimento(session, user)
    conversa = await _conversa_ou_404(session, conversa_id, scope)
    linhas = await reclamacoes.reclamacoes_da_conversa(session, conversa)
    # O "Abrir na Shopee" com o id interno (devolução/pedido): uma consulta.
    ids_shopee = await reclamacoes.ids_shopee_das(session, linhas)
    # O bloco no painel só no chat e na conversa da devolução: e-mail, avaliação e
    # chat de afiliado ficam com o cartão de sempre.
    pedido_do_bloco = (
        conversa.pedido_marketplace
        if foco_devolucao.vale_para(conversa) or conversa.canal == CANAL_RECLAMACAO
        else None
    )
    itens = [
        ReclamacaoOut(**reclamacoes.para_tela(r, ids_shopee, pedido_da_conversa=pedido_do_bloco))
        for r in linhas
    ]
    prazos = [i.prazo_em for i in itens if i.aberta and i.prazo_em is not None]
    return ReclamacoesOut(
        itens=itens,
        abertas=sum(1 for i in itens if i.aberta),
        prazo_mais_curto=min(prazos) if prazos else None,
    )
