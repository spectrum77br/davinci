"""Pós-venda › Atendimento: o cartão da PUBLICAÇÃO e as ações nos comentários (RF7, 02/10/2026).

  GET  /api/atendimento/conversas/{id}/publicacao
       A publicação da conversa `comentario` (miniatura — renovada quando a
       URL da rede já venceu —, formato, legenda, link, curtidas, nº de
       comentários, própria ou menção) e os comentários dela EM FIO (os desta
       conversa marcados, as respostas da marca aninhadas), com o que dá para
       fazer em cada um e o porquê de não dar.
  POST /api/atendimento/comentarios/{id}/responder          {texto, confirmar}
  POST /api/atendimento/comentarios/{id}/responder-direct   {texto, confirmar}
  POST /api/atendimento/comentarios/{id}/ocultar            {confirmar, ocultar}
       O caminho existe (e é testado com HTTP falso), mas com
       `ATENDIMENTO_ENVIO_ATIVO=false` RECUSA ANTES de qualquer coisa (409
       `envio_desligado`, como as avaliações): nada é lido, criado ou enviado.
       Responder em público pede `confirmar=true` ("Responder em PÚBLICO?");
       o Direct, 1 mensagem até 7 dias depois do comentário; tudo só com a
       conta em modo `humano` na aba Lojas. Quem decide e fala com a rede é
       `services/atendimento/redes.py`.

A MESMA TRAVA do router do atendimento (`_so_admin`), a mesma permissão
(`_view` para ler, `_edit` para agir) e o escopo por equipe: o canal das
redes não tem loja de marketplace, então só quem vê todas as lojas
(`_no_escopo` com integração nula), como o Direct.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.team_scope import resolve_team_scope
from app.models import (
    AtendimentoCanal,
    AtendimentoComentario,
    AtendimentoConversa,
    AtendimentoPublicacao,
    User,
)
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
from app.services.atendimento import instagram, redes
from app.services.atendimento.constantes import CANAL_COMENTARIO
from app.services.atendimento.enviar import EnvioRecusado

logger = structlog.get_logger()

router = APIRouter(
    prefix="/api/atendimento", tags=["atendimento"], dependencies=[Depends(_so_admin)]
)


class PublicacaoOut(BaseModel):
    id: UUID
    plataforma: str
    # A conta da MARCA na rede (ig_user_id / page_id) e o nome dela.
    conta_id: str
    conta_nome: str | None
    externo_id: str
    # propria | mencao
    tipo: str
    formato: str | None
    # "Reels 28/09", "menção · Foto 30/09"
    origem: str
    autor_username: str | None
    legenda: str | None
    link: str | None
    miniatura_url: str | None
    miniatura_lida_em: datetime | None
    # A URL da miniatura é de CDN assinada: velha demais, pode não abrir.
    miniatura_vencida: bool
    publicada_em: datetime | None
    curtidas: int | None
    comentarios: int | None


class ComentarioOut(BaseModel):
    id: UUID
    externo_id: str
    pai_externo_id: str | None
    autor_username: str | None
    da_marca: bool
    texto: str | None
    criado_em: datetime | None
    curtidas: int | None
    oculto: bool
    eh_pergunta: bool
    mencao: bool
    # É desta conversa (o destaque do cartão).
    desta_conversa: bool
    # A marca já respondeu (em público, no fio, ou no Direct).
    respondido: bool
    resposta_privada_em: datetime | None
    # O que dá para fazer AGORA — e, quando não dá, o porquê (o texto da tela).
    pode_responder: bool
    motivo_responder: str | None
    pode_direct: bool
    motivo_direct: str | None
    pode_ocultar: bool
    motivo_ocultar: str | None
    respostas: list[ComentarioOut] = Field(default_factory=list)


ComentarioOut.model_rebuild()


class EnvioRedeOut(BaseModel):
    # ATENDIMENTO_ENVIO_ATIVO
    ativo: bool
    # O modo da conta na aba Lojas (observar | humano).
    modo_canal: str | None
    # Por que a caixa está desabilitada para todos (envio desligado, observar).
    motivo: str | None
    aviso_publico: str
    aviso_direct: str
    limite_publico: int
    limite_direct: int
    resposta_privada_dias: int


class PublicacaoPainelOut(BaseModel):
    publicacao: PublicacaoOut | None
    comentarios: list[ComentarioOut]
    total_comentarios: int
    truncado: bool
    # Outras conversas da mesma pessoa nesta rede (RF7: "já interagiu antes").
    interacoes_anteriores: int
    # O status da leitura da conta (o `sem_escopo` diz o que falta no token).
    canal_status: str | None
    canal_erro: str | None
    envio: EnvioRedeOut


class ResponderComentarioIn(BaseModel):
    # Sem `min_length`: texto vazio é recusa do serviço (`texto_invalido`).
    texto: str = Field(max_length=10_000)
    confirmar: bool = False


class OcultarIn(BaseModel):
    confirmar: bool = False
    # false = mostrar de novo.
    ocultar: bool = True


class ComentarioAcaoOut(BaseModel):
    mensagem: MensagemOut | None = None
    comentario: ComentarioOut


def _recusa(e: EnvioRecusado) -> HTTPException:
    return _recusa_http(e)


async def _comentario_ou_404(
    session: AsyncSession, comentario_id: str, user: User
) -> AtendimentoComentario:
    uid = _uuid_ou_404(comentario_id, "comentario_nao_encontrado")
    c = await session.get(AtendimentoComentario, uid)
    scope = await resolve_team_scope(session, user)
    # Canal de rede não tem loja de marketplace: só quem vê todas (como o Direct).
    if c is None or not _no_escopo(scope, None):
        raise HTTPException(404, detail={"code": "comentario_nao_encontrado"})
    return c


async def _comentario_saida(session: AsyncSession, c: AtendimentoComentario) -> ComentarioOut:
    pub = await session.get(AtendimentoPublicacao, c.publicacao_id)
    canal = (
        await session.get(AtendimentoCanal, pub.canal_id)
        if pub is not None and pub.canal_id
        else None
    )
    return ComentarioOut(
        **redes.comentario_para_tela(
            c,
            conversa_id=c.conversa_id,
            canal=canal,
            envio_ativo=get_settings().atendimento_envio_ativo,
            agora=redes._agora(),
        )
    )


@router.get("/conversas/{conversa_id}/publicacao", response_model=PublicacaoPainelOut)
async def publicacao_da_conversa(
    conversa_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_view)],
) -> PublicacaoPainelOut:
    """O painel da direita da conversa de comentário/menção."""
    if instagram.e_instagram(conversa_id):
        # O Direct não tem publicação.
        raise HTTPException(404, detail={"code": "sem_publicacao"})
    scope = await resolve_team_scope(session, user)
    conversa: AtendimentoConversa = await _conversa_ou_404(session, conversa_id, scope)
    if conversa.canal != CANAL_COMENTARIO:
        raise HTTPException(404, detail={"code": "sem_publicacao"})
    pub = await redes.publicacao_da_conversa(session, conversa)
    if pub is not None and await redes.renovar_miniatura(session, pub):
        await session.commit()
    dados = await redes.painel(
        session, conversa, envio_ativo=get_settings().atendimento_envio_ativo
    )
    return PublicacaoPainelOut(**dados)


async def _acao_bloqueada() -> None:
    """Envio desligado (ou simulador em produção) → 409 ANTES de tudo."""
    recusa = redes.bloqueio_do_envio()
    if recusa is not None:
        raise _recusa(recusa)


@router.post("/comentarios/{comentario_id}/responder", response_model=ComentarioAcaoOut)
async def responder_comentario(
    comentario_id: str,
    body: ResponderComentarioIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> ComentarioAcaoOut:
    """Responde em PÚBLICO (aparece na publicação). Erro da REDE não é erro HTTP:
    200 com a mensagem em `falhou`/`revisar`."""
    await _acao_bloqueada()
    return await _responder(session, comentario_id, body, user=user, privado=False)


@router.post("/comentarios/{comentario_id}/responder-direct", response_model=ComentarioAcaoOut)
async def responder_comentario_no_direct(
    comentario_id: str,
    body: ResponderComentarioIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> ComentarioAcaoOut:
    """Resposta PRIVADA ao comentário (uma, até 7 dias)."""
    await _acao_bloqueada()
    return await _responder(session, comentario_id, body, user=user, privado=True)


async def _responder(
    session: AsyncSession,
    comentario_id: str,
    body: ResponderComentarioIn,
    *,
    user: User,
    privado: bool,
) -> ComentarioAcaoOut:
    c = await _comentario_ou_404(session, comentario_id, user)
    cid, uid = str(c.id), str(user.id)  # o rollback expira os objetos
    try:
        m = await redes.responder(
            session, c, body.texto, user=user, confirmar=body.confirmar, privado=privado
        )
    except EnvioRecusado as e:
        await session.rollback()
        logger.info(
            "atendimento_redes_resposta_recusada",
            comentario_id=cid,
            privado=privado,
            code=e.code,
            user_id=uid,
        )
        raise _recusa(e) from e
    conversa = await session.get(AtendimentoConversa, m.conversa_id)
    await session.refresh(c)
    return ComentarioAcaoOut(
        mensagem=_mensagem_out(m, conversa=conversa, nomes={user.id: _nome(user)}),
        comentario=await _comentario_saida(session, c),
    )


@router.post("/comentarios/{comentario_id}/ocultar", response_model=ComentarioAcaoOut)
async def ocultar_comentario(
    comentario_id: str,
    body: OcultarIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> ComentarioAcaoOut:
    """Oculta o comentário na rede (ou mostra de novo, `ocultar=false`); registra quem fez."""
    await _acao_bloqueada()
    c = await _comentario_ou_404(session, comentario_id, user)
    cid, uid = str(c.id), str(user.id)  # o rollback expira os objetos
    try:
        await redes.ocultar(session, c, user=user, confirmar=body.confirmar, esconder=body.ocultar)
    except EnvioRecusado as e:
        await session.rollback()
        logger.info(
            "atendimento_redes_ocultar_recusado",
            comentario_id=cid,
            code=e.code,
            user_id=uid,
        )
        raise _recusa(e) from e
    await session.refresh(c)
    return ComentarioAcaoOut(comentario=await _comentario_saida(session, c))
