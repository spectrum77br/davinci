"""Pós-venda › Atendimento: a aba ★ Avaliação (RF8, 02/10/2026).

  GET  /api/atendimento/conversas/{id}/avaliacoes
  POST /api/atendimento/avaliacoes/{id}/responder
  POST /api/atendimento/avaliacoes/{id}/tratada

As avaliações de venda ligadas à conversa — as do PEDIDO dela (pela
`conversa_id` ou pelo pedido/pack, a mesma ligação da etiqueta) e as
anteriores do MESMO comprador na mesma loja —, pendentes primeiro. Quem lê
e decide a pendência é `services/atendimento/avaliacoes.py` (Shopee e ML).

RESPONDER é PÚBLICO (aparece no anúncio): sai pelo caminho único do envio
(`enviar.enviar_resposta`, na conversa `avaliacao` — criada aqui se a
avaliação ainda não tinha), com as mesmas travas. Com o envio desligado
(`ATENDIMENTO_ENVIO_ATIVO=false`, o caso de hoje) a rota RECUSA antes de
qualquer coisa: 409 `envio_desligado`, com o aviso "resposta pública" — nada
é criado, nada sai. No ML (sem resposta pela API) o caminho é "marcar como
tratada", que tira a avaliação da pendência e devolve a etiqueta ao status
anterior.

A MESMA TRAVA do router do atendimento (`_so_admin`), a mesma permissão
(`_view` para ler, `_edit` para responder/tratar) e o escopo por equipe
(`_conversa_ou_404`; a avaliação, pela loja dela). Router à parte para não
mexer no do atendimento: o `main.py` o inclui (o integrador liga).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.team_scope import resolve_team_scope
from app.models import AtendimentoAvaliacaoLoja, User
from app.routers.atendimento import (
    _conversa_ou_404,
    _edit,
    _mensagem_out,
    _no_escopo,
    _nome,
    _recusa_http,
    _so_admin,
    _uuid_ou_404,
    _view,
)
from app.schemas.atendimento import MensagemOut
from app.services.atendimento import avaliacoes, enviar, instagram
from app.services.atendimento.constantes import ORIGEM_HUMANO
from app.services.atendimento.enviar import EnvioRecusado

logger = structlog.get_logger()

router = APIRouter(
    prefix="/api/atendimento", tags=["atendimento"], dependencies=[Depends(_so_admin)]
)


class MidiaAvaliacaoOut(BaseModel):
    # imagem | video — só o endereço https da plataforma.
    tipo: str
    url: str
    miniatura: str | None = None


class AvaliacaoLojaOut(BaseModel):
    id: UUID
    plataforma: str
    plataforma_nome: str
    # Id da avaliação NA PLATAFORMA (Shopee `comment_id`, ML id da opinião).
    comentario_id: str
    pedido: str | None
    item_id: str | None
    anuncio_titulo: str | None = None
    estrelas: int
    # Nota 1–3: o destaque (RF8).
    nota_baixa: bool
    titulo: str | None
    texto: str | None
    midia: list[MidiaAvaliacaoOut]
    criado_em: datetime | None
    # O comprador mudou a nota (Shopee `editable = HAVE_EDITED_ONCE`).
    editada: bool
    respondida: bool
    resposta_loja: str | None
    resposta_em: datetime | None
    resposta_oculta: bool | None
    # Dá para responder AGORA pelo DaVinci (plataforma com API, envio
    # ligado, ainda sem resposta). Senão, o porquê vai em `motivo_sem_resposta`
    # (o texto que a tela mostra no lugar da caixa).
    pode_responder: bool
    motivo_sem_resposta: str | None
    pendente: bool
    pendente_desde: datetime | None
    tratada_em: datetime | None
    tratada_por_nome: str | None
    conversa_id: UUID | None
    # True = do pedido desta conversa; False = anterior do mesmo comprador.
    do_pedido: bool
    url_plataforma: str | None


class AvaliacoesOut(BaseModel):
    itens: list[AvaliacaoLojaOut]
    pendentes: int
    # A pior nota entre as pendentes (o selo); None = nenhuma pendente.
    pior_pendente: int | None
    envio_ativo: bool
    # O aviso da caixa de resposta: a resposta é pública.
    aviso: str


class ResponderAvaliacaoIn(BaseModel):
    # Sem `min_length`: texto vazio é recusa do validador (`texto_invalido`).
    texto: str = Field(max_length=10_000)
    ultima_vista_id: UUID | None = None
    confirmar: bool = False


class ResponderAvaliacaoOut(BaseModel):
    mensagem: MensagemOut
    avaliacao: AvaliacaoLojaOut


class TratadaIn(BaseModel):
    # O porquê (opcional, texto da equipe): fica em `dados.tratada_motivo` —
    # não vai para a plataforma.
    motivo: str | None = Field(default=None, max_length=300)


class TratadaOut(BaseModel):
    avaliacao: AvaliacaoLojaOut


async def _nomes(session: AsyncSession, ids: set[UUID | None]) -> dict[UUID, str | None]:
    validos = [i for i in ids if i is not None]
    if not validos:
        return {}
    usuarios = (await session.execute(select(User).where(User.id.in_(validos)))).scalars().all()
    return {u.id: _nome(u) for u in usuarios}


async def _saida(
    session: AsyncSession, a: AtendimentoAvaliacaoLoja, *, do_pedido: bool
) -> AvaliacaoLojaOut:
    nomes = await _nomes(session, {a.tratada_por})
    return AvaliacaoLojaOut(
        **avaliacoes.para_tela(
            a,
            do_pedido=do_pedido,
            envio_ativo=get_settings().atendimento_envio_ativo,
            nomes=nomes,
        )
    )


async def _avaliacao_ou_404(
    session: AsyncSession, avaliacao_id: str, user: User
) -> AtendimentoAvaliacaoLoja:
    uid = _uuid_ou_404(avaliacao_id, "avaliacao_nao_encontrada")
    a = await session.get(AtendimentoAvaliacaoLoja, uid)
    scope = await resolve_team_scope(session, user)
    if a is None or not _no_escopo(scope, a.integration_id):
        raise HTTPException(404, detail={"code": "avaliacao_nao_encontrada"})
    return a


@router.get("/conversas/{conversa_id}/avaliacoes", response_model=AvaliacoesOut)
async def avaliacoes_da_conversa(
    conversa_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_view)],
) -> AvaliacoesOut:
    """A aba ★ Avaliação: as do pedido e as anteriores do comprador (pendentes primeiro)."""
    envio_ativo = get_settings().atendimento_envio_ativo
    if instagram.e_instagram(conversa_id):
        # DM do Instagram não tem pedido nem avaliação.
        return AvaliacoesOut(
            itens=[],
            pendentes=0,
            pior_pendente=None,
            envio_ativo=envio_ativo,
            aviso=avaliacoes.AVISO_RESPOSTA_PUBLICA,
        )
    scope = await resolve_team_scope(session, user)
    conversa = await _conversa_ou_404(session, conversa_id, scope)
    linhas = await avaliacoes.avaliacoes_da_conversa(session, conversa)
    nomes = await _nomes(session, {a.tratada_por for a, _ in linhas})
    itens = [
        AvaliacaoLojaOut(
            **avaliacoes.para_tela(a, do_pedido=do_pedido, envio_ativo=envio_ativo, nomes=nomes)
        )
        for a, do_pedido in linhas
    ]
    pendentes = [i.estrelas for i in itens if i.pendente]
    return AvaliacoesOut(
        itens=itens,
        pendentes=len(pendentes),
        pior_pendente=min(pendentes) if pendentes else None,
        envio_ativo=envio_ativo,
        aviso=avaliacoes.AVISO_RESPOSTA_PUBLICA,
    )


def _recusa(code: str, detail: Any) -> HTTPException:
    return HTTPException(409, detail={"code": code, "detail": detail})


@router.post("/avaliacoes/{avaliacao_id}/responder", response_model=ResponderAvaliacaoOut)
async def responder_avaliacao(
    avaliacao_id: str,
    body: ResponderAvaliacaoIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> ResponderAvaliacaoOut:
    """Responde a avaliação — resposta PÚBLICA, pelo caminho único do envio.

    Envio desligado → 409 `envio_desligado` ANTES de tudo (nada é criado).
    Plataforma sem resposta pela API (ML) → 409 `somente_leitura` com o
    motivo; já respondida → 409 `ja_respondida`. As travas do envio (loja
    em observar, uma em voo, texto reprovado → 422) vêm do `enviar`. Erro da
    PLATAFORMA não é erro HTTP: 200 com a mensagem em `falhou`/`revisar`.
    """
    a = await _avaliacao_ou_404(session, avaliacao_id, user)
    if not get_settings().atendimento_envio_ativo:
        raise _recusa(enviar.RECUSA_ENVIO_DESLIGADO, avaliacoes.MOTIVO_ENVIO_DESLIGADO)
    if not a.pode_responder:
        raise _recusa(
            enviar.RECUSA_SOMENTE_LEITURA,
            avaliacoes.motivo_sem_resposta(a, envio_ativo=True)
            or "Esta plataforma não deixa responder a avaliação pela API.",
        )
    if a.resposta_loja:
        raise _recusa(enviar.RECUSA_AVALIACAO_JA_RESPONDIDA, "Esta avaliação já foi respondida.")
    conversa = await avaliacoes.conversa_para_responder(session, a)
    await session.commit()
    try:
        m = await enviar.enviar_resposta(
            session,
            conversa,
            body.texto,
            user=user,
            origem=ORIGEM_HUMANO,
            ultima_vista_id=body.ultima_vista_id,
            confirmar=body.confirmar,
        )
    except EnvioRecusado as e:
        logger.info(
            "atendimento_avaliacao_resposta_recusada",
            avaliacao_id=str(a.id),
            conversa_id=str(conversa.id),
            code=e.code,
            user_id=str(user.id),
        )
        raise _recusa_http(e) from e
    await session.refresh(a)
    return ResponderAvaliacaoOut(
        mensagem=_mensagem_out(m, conversa=conversa, nomes={user.id: _nome(user)}),
        avaliacao=await _saida(session, a, do_pedido=True),
    )


@router.post("/avaliacoes/{avaliacao_id}/tratada", response_model=TratadaOut)
async def marcar_tratada(
    avaliacao_id: str,
    body: TratadaIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> TratadaOut:
    """Marcar como tratada: sai da pendência e a etiqueta volta ao status anterior.

    É o caminho do ML (sem resposta pela API), e vale para qualquer
    avaliação ainda sem resposta. Idempotente. A já respondida → 409
    `ja_respondida`. Nada vai para a plataforma.
    """
    a = await _avaliacao_ou_404(session, avaliacao_id, user)
    try:
        await avaliacoes.marcar_tratada(session, a, user=user, motivo=body.motivo)
    except avaliacoes.AcaoRecusada as e:
        raise _recusa(e.code, e.detail) from e
    await session.commit()
    await session.refresh(a)
    return TratadaOut(avaliacao=await _saida(session, a, do_pedido=True))
