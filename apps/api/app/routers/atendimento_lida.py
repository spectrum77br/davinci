"""Pós-venda › Atendimento: marcar a conversa como LIDA / VISTA ao abrir (14 e 15/10/2026).

  POST /api/atendimento/conversas/{id}/lida
  POST /api/atendimento/conversas/{id}/visto
  POST /api/atendimento/zerar-nao-lidas

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

O /lida grava também o "visto no DaVinci" (`visto_em`), ANTES de tentar a
plataforma — a pessoa abriu, com ou sem a plataforma aceitar (e no 409 de
corrida também).

O /visto (15/10/2026, decisão do dono: "os números da Caixa têm que bater com
o Duoke", e lá abrir a conversa zera o número dela) é a rota LEVE que a tela
chama quando o detalhe NÃO diz `marcar_lida` — a loja em `observar`, o envio
desligado, a plataforma sem "lida", nada não lido: só grava `visto_em`, em
qualquer modo de loja, e NUNCA vai à plataforma. Uma rota à parte, e não um
modo do /lida, para o /lida continuar recusando (409) o que não marca na
plataforma. Respostas:
  - 403 `atendimento_so_leitura` — quem só lê (a trava e o serviço);
  - 409 `nao_se_aplica` — o Direct do Instagram (não é conversa da Caixa);
  - 404 — fora do escopo da equipe ou não existe;
  - 200 `{visto_em, conversa}` — idempotente (abrir de novo só anda o
    carimbo para a frente). O número da Caixa (`pendente.nao_lidas_duoke`)
    só conta a mensagem do comprador que CHEGOU depois dele.
O corpo, nos dois (opcional, 15/10/2026): `{"ate": <visto_ate do detalhe>}`
— até onde a tela mostrava a conversa (a chegada da mensagem mais nova); o
`visto_em` fica nele, nunca depois do relógio do banco. Sem corpo, o relógio
do banco (`lida.gravar_visto`). Com a conversa aberta, a tela chama o /visto
de novo quando a releitura traz mensagem nova (o /lida, uma vez por
abertura).

O /zerar-nao-lidas (15/10/2026) é o "Limpar não lidas" do Duoke: o "visto no
DaVinci" de uma vez em todas as conversas do número (o chat e a reclamação do
ML, `pendente.do_numero`) da loja (`integration_id`), das plataformas
(`plataformas`) ou de tudo — dentro do escopo da equipe.
Responde `{conversas}` (quantas foram marcadas); 403 para quem só lê.

A MESMA TRAVA de acesso do router do atendimento (`_so_admin`), a permissão de
escrita (`_edit`) e o escopo por equipe (`_conversa_ou_404`). Router à parte;
o `main.py` o inclui.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.team_scope import resolve_team_scope
from app.models import AtendimentoConversa, User
from app.routers.atendimento import (
    SO_LEITURA,
    _clausula_escopo,
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


class VistoIn(BaseModel):
    """O corpo (opcional) do /lida e do /visto: até onde a tela mostrava.

    `ate` = o `visto_ate` do detalhe aberto (a chegada da mensagem mais nova,
    `lida_svc.visto_ate`). Sem corpo (a tela antiga), o relógio do banco.
    """

    ate: datetime | None = None


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
    body: VistoIn | None = None,
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
        # A pessoa abriu: o "visto no DaVinci" vale com ou sem a plataforma.
        await lida_svc.gravar_visto(session, c, user, body.ate if body else None)
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


class VistoOut(BaseModel):
    """O "visto no DaVinci" gravado (a tela só atualiza a linha e relê os números)."""

    visto_em: datetime | None = None
    conversa: ConversaOut | None = None


@router.post("/conversas/{conversa_id}/visto", response_model=VistoOut)
async def marcar_visto(
    conversa_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
    body: VistoIn | None = None,
) -> VistoOut:
    """Grava o "visto no DaVinci" (`visto_em`) — em qualquer modo, sem ir à plataforma.

    Ao abrir e, com a conversa aberta, a cada mensagem nova que a tela mostra.
    """
    if instagram.e_instagram(conversa_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": lida_svc.RECUSA_NAO_SE_APLICA, "detail": lida_svc.MOTIVO_NAO_SE_APLICA},
        )
    scope = await resolve_team_scope(session, user)
    c = await _conversa_ou_404(session, conversa_id, scope)
    try:
        visto = await lida_svc.gravar_visto(session, c, user, body.ate if body else None)
    except lida_svc.LidaRecusada as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=SO_LEITURA) from e
    return VistoOut(visto_em=visto, conversa=await _conversa_out(session, c))


class ZerarIn(BaseModel):
    """O que zerar: a loja, as plataformas ou (os dois vazios) a Caixa inteira."""

    plataformas: list[str] = []
    integration_id: UUID | None = None


class ZerarOut(BaseModel):
    conversas: int = 0


@router.post("/zerar-nao-lidas", response_model=ZerarOut)
async def zerar_nao_lidas(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
    body: ZerarIn | None = None,
) -> ZerarOut:
    """O "Zerar não lidas" do Duoke: marca como vistas no DaVinci, sem ir à plataforma."""
    body = body or ZerarIn()
    scope = await resolve_team_scope(session, user)
    try:
        total = await lida_svc.zerar_nao_lidas(
            session,
            user,
            plataformas=[p.strip() for p in body.plataformas],
            integration_id=body.integration_id,
            escopo=_clausula_escopo(scope, AtendimentoConversa.integration_id),
        )
    except lida_svc.LidaRecusada as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=SO_LEITURA) from e
    return ZerarOut(conversas=total)
