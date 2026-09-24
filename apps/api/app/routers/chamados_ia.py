"""Aba Chamados › IA de Chamado (Vinicius, 24/09/2026).

"Essa aba que vai criar é onde eu vou ensinar ele, e onde vai criando um manual —
quando acontecer isso e isso você faz isso". A IA de Chamado é o cérebro dos
chamados (hoje o Hermes no Mac Santiago, `chamados_cerebros`); aqui a pessoa:

- liga/desliga (desligada, ela não recebe caso nenhum e não decide nada — é o
  próprio servidor que trava, ver `routers/chamados.agent_analisar/analise`);
- escreve o manual (`chamados_ia_regras`), que a IA lê a cada passada;
- vê o que ela decidiu (as análises que ela gravou nos chamados) e marca ✓ acertou
  / ✗ errou. O ✗ leva a correção: vira instrução no chamado (a IA refaz na
  próxima passada) e aprendizado (vai pra IA a cada passada, ver
  `routers/chamados.agent_cerebro`).

Sem modo teste (decisão dele): ligada = decide de verdade.
"""

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import (
    Chamado,
    ChamadoCerebro,
    ChamadoIaAvaliacao,
    ChamadoIaRegra,
    ChamadoMensagem,
    User,
)
from app.models.chamado import CANAIS
from app.routers.chamados import _trabalho_do_cerebro
from app.schemas.chamados import (
    IaAvaliacaoIn,
    IaAvaliacaoOut,
    IaDecisaoOut,
    IaEstadoIn,
    IaEstadoOut,
    IaRegraIn,
    IaRegraOut,
    IaRegraPatch,
)
from app.services import chamados as svc

logger = structlog.get_logger()
# incluído ANTES do router da aba (senão "/ia" cairia em "/{chamado_id}")
router = APIRouter(prefix="/api/chamados/ia", tags=["chamados"])

_DECISOES = 40


def _nome(user: User | None) -> str | None:
    if user is None:
        return None
    return (user.name or user.email or "").strip() or None


def _plataforma(valor: str | None) -> str | None:
    v = (valor or "").strip().lower()
    return None if v in ("", "todas") else v


async def _ia(session: AsyncSession) -> ChamadoCerebro:
    ia = (
        await session.execute(
            select(ChamadoCerebro)
            .where(ChamadoCerebro.revoked_at.is_(None))
            .order_by(ChamadoCerebro.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    if ia is None:
        raise HTTPException(404, detail={"code": "ia_sem_cadastro"})
    return ia


async def _nomes(session: AsyncSession, ids: set[UUID | None]) -> dict[UUID, str | None]:
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    return {
        u.id: _nome(u)
        for u in (await session.execute(select(User).where(User.id.in_(ids)))).scalars()
    }


async def _decisoes_out(
    session: AsyncSession, ia: ChamadoCerebro, mensagem_id: UUID | None = None
) -> list[IaDecisaoOut]:
    q = (
        select(ChamadoMensagem, Chamado, ChamadoIaAvaliacao)
        .join(Chamado, Chamado.id == ChamadoMensagem.chamado_id)
        .outerjoin(ChamadoIaAvaliacao, ChamadoIaAvaliacao.mensagem_id == ChamadoMensagem.id)
        .where(ChamadoMensagem.tipo == "analise", ChamadoMensagem.autor_nome == ia.nome)
    )
    if mensagem_id is not None:
        q = q.where(ChamadoMensagem.id == mensagem_id)
    rows = (
        await session.execute(q.order_by(ChamadoMensagem.created_at.desc()).limit(_DECISOES))
    ).all()
    nomes = await _nomes(session, {av.updated_by for _m, _c, av in rows if av is not None})
    return [
        IaDecisaoOut(
            mensagem_id=m.id,
            chamado_id=ch.id,
            pedido_bling=ch.pedido_bling,
            plataforma=ch.plataforma,
            conta=ch.conta,
            quando=m.created_at,
            texto=m.texto,
            avaliacao=(
                IaAvaliacaoOut(
                    certo=av.certo,
                    correcao=av.correcao,
                    autor=nomes.get(av.updated_by),
                    quando=av.updated_at,
                )
                if av is not None
                else None
            ),
        )
        for m, ch, av in rows
    ]


async def _regras_out(session: AsyncSession) -> list[IaRegraOut]:
    regras = (
        (await session.execute(select(ChamadoIaRegra).order_by(ChamadoIaRegra.created_at)))
        .scalars()
        .all()
    )
    nomes = await _nomes(session, {r.updated_by or r.created_by for r in regras})
    return [
        IaRegraOut(
            id=r.id,
            quando=r.quando,
            faca=r.faca,
            plataforma=r.plataforma,
            ativa=r.ativa,
            autor=nomes.get(r.updated_by or r.created_by),
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in regras
    ]


@router.get("", response_model=IaEstadoOut)
async def estado(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("chamados", "view"))],
) -> IaEstadoOut:
    ia = await _ia(session)
    esperando = (
        await session.execute(
            select(func.count()).select_from(
                _trabalho_do_cerebro(None, list(CANAIS)).order_by(None).subquery()
            )
        )
    ).scalar_one()
    return IaEstadoOut(
        nome=ia.nome,
        ligada=ia.ligada,
        exclusivo=ia.exclusivo,
        ultima_passada=ia.last_used_at,
        esperando=esperando,
        regras=await _regras_out(session),
        decisoes=await _decisoes_out(session, ia),
    )


@router.patch("", response_model=IaEstadoOut)
async def ligar(
    body: IaEstadoIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("chamados", "edit"))],
) -> IaEstadoOut:
    ia = await _ia(session)
    if ia.ligada != body.ligada:
        ia.ligada = body.ligada
        await session.commit()
        logger.info("chamados_ia_ligada", ligada=ia.ligada, por=_nome(user))
    return await estado(session, user)


@router.post("/regras", response_model=IaRegraOut, status_code=status.HTTP_201_CREATED)
async def criar_regra(
    body: IaRegraIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("chamados", "edit"))],
) -> IaRegraOut:
    r = ChamadoIaRegra(
        quando=body.quando,
        faca=body.faca,
        plataforma=_plataforma(body.plataforma),
        created_by=user.id,
        updated_by=user.id,
    )
    session.add(r)
    await session.commit()
    await session.refresh(r)
    logger.info("chamados_ia_regra_criada", regra=str(r.id), por=_nome(user))
    return next(x for x in await _regras_out(session) if x.id == r.id)


@router.patch("/regras/{regra_id}", response_model=IaRegraOut)
async def editar_regra(
    regra_id: UUID,
    body: IaRegraPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("chamados", "edit"))],
) -> IaRegraOut:
    r = await session.get(ChamadoIaRegra, regra_id)
    if r is None:
        raise HTTPException(404, detail={"code": "regra_nao_encontrada"})
    campos = body.model_fields_set
    if "quando" in campos:
        if not body.quando:
            raise HTTPException(422, detail={"code": "quando_vazio"})
        r.quando = body.quando
    if "faca" in campos:
        if not body.faca:
            raise HTTPException(422, detail={"code": "faca_vazio"})
        r.faca = body.faca
    if "plataforma" in campos:
        r.plataforma = _plataforma(body.plataforma)
    if "ativa" in campos and body.ativa is not None:
        r.ativa = body.ativa
    r.updated_by = user.id
    await session.commit()
    await session.refresh(r)
    return next(x for x in await _regras_out(session) if x.id == r.id)


@router.delete("/regras/{regra_id}", status_code=status.HTTP_204_NO_CONTENT)
async def apagar_regra(
    regra_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("chamados", "edit"))],
) -> Response:
    r = await session.get(ChamadoIaRegra, regra_id)
    if r is None:
        raise HTTPException(404, detail={"code": "regra_nao_encontrada"})
    await session.delete(r)
    await session.commit()
    logger.info("chamados_ia_regra_apagada", regra=str(regra_id), por=_nome(user))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _decisao_da_ia(
    session: AsyncSession, mensagem_id: UUID
) -> tuple[ChamadoCerebro, ChamadoMensagem, Chamado]:
    ia = await _ia(session)
    m = await session.get(ChamadoMensagem, mensagem_id)
    if m is None or m.tipo != "analise" or m.autor_nome != ia.nome:
        raise HTTPException(404, detail={"code": "decisao_nao_encontrada"})
    ch = await session.get(Chamado, m.chamado_id)
    assert ch is not None
    return ia, m, ch


@router.put("/decisoes/{mensagem_id}/avaliacao", response_model=IaDecisaoOut)
async def avaliar(
    mensagem_id: UUID,
    body: IaAvaliacaoIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("chamados", "edit"))],
) -> IaDecisaoOut:
    """✓/✗ numa decisão. No ✗, a correção vira instrução no chamado — a IA refaz
    na próxima passada (salvo chamado Concluído: aí fica só o aprendizado)."""
    ia, m, ch = await _decisao_da_ia(session, mensagem_id)
    av = (
        await session.execute(
            select(ChamadoIaAvaliacao).where(ChamadoIaAvaliacao.mensagem_id == m.id)
        )
    ).scalar_one_or_none()
    if av is None:
        av = ChamadoIaAvaliacao(mensagem_id=m.id, chamado_id=ch.id, created_by=user.id)
        session.add(av)
    mudou_correcao = not body.certo and (av.certo is not False or av.correcao != body.correcao)
    av.certo = body.certo
    av.correcao = None if body.certo else body.correcao
    av.updated_by = user.id
    if mudou_correcao and not ch.resolvido:
        session.add(
            svc.nova_mensagem(
                ch,
                texto=(
                    f"Correção de {_nome(user) or 'uma pessoa'} sobre a decisão da {ia.nome} "
                    f"de {m.created_at.astimezone(svc.SAO_PAULO):%d/%m %H:%M}: {body.correcao}"
                ),
                tipo="instrucao",
                direcao="sistema",
                autor_nome=_nome(user) or "usuário",
                autor_id=user.id,
                status="registrada",
            )
        )
    await session.commit()
    logger.info("chamados_ia_avaliacao", mensagem=str(m.id), certo=body.certo, por=_nome(user))
    return (await _decisoes_out(session, ia, mensagem_id=m.id))[0]


@router.delete("/decisoes/{mensagem_id}/avaliacao", response_model=IaDecisaoOut)
async def desfazer_avaliacao(
    mensagem_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("chamados", "edit"))],
) -> IaDecisaoOut:
    """Tira o ✓/✗ (a instrução que o ✗ mandou continua no histórico)."""
    ia, m, _ch = await _decisao_da_ia(session, mensagem_id)
    av = (
        await session.execute(
            select(ChamadoIaAvaliacao).where(ChamadoIaAvaliacao.mensagem_id == m.id)
        )
    ).scalar_one_or_none()
    if av is not None:
        await session.delete(av)
        await session.commit()
    return (await _decisoes_out(session, ia, mensagem_id=m.id))[0]
