"""Cadastros › Imobilizado — cadastro dos bens da empresa (06/10/2026).

Projeto "Cadastro de Imobilizado no DaVinci" v0.1. Permissão `imobilizado`:
view = vê todos os itens (Gestor), edit = cadastra, edita e transfere,
delete = dá baixa (Administrador de patrimônio). Quem não tem view vê só os
itens de que é responsável, sem mexer (Colaborador). Admin vê e faz tudo.

Item não é excluído: sai por baixa (RN05) e baixado não muda mais (RN06).
Cada mudança de descrição, valor, responsável ou status grava uma linha em
imobilizado_historico (RN07).
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import BigInteger, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_active_user, require_permission
from app.models import Imobilizado, ImobilizadoHistorico, User, UserRole, UserStatus
from app.schemas.imobilizado import (
    ImobilizadoBaixa,
    ImobilizadoCreate,
    ImobilizadoHistoricoOut,
    ImobilizadoListaOut,
    ImobilizadoOut,
    ImobilizadoTransferir,
    ImobilizadoTransferirOut,
    ImobilizadoUpdate,
    PessoaRef,
)
from app.services import imobilizado as svc

router = APIRouter(prefix="/api/imobilizado", tags=["imobilizado"])
_view = require_permission("imobilizado", "view")
_edit = require_permission("imobilizado", "edit")
_baixa = require_permission("imobilizado", "delete")

SAO_PAULO = ZoneInfo("America/Sao_Paulo")
PREFIXO = "IMB-"


def _ve_tudo(u: User) -> bool:
    return u.role == UserRole.ADMIN or bool(
        (u.permissions or {}).get("imobilizado", {}).get("view")
    )


def _erro(codigo: int, code: str, campo: str | None = None, **extra) -> HTTPException:
    """`campo` diz à tela embaixo de qual campo mostrar a mensagem."""
    detail = {"code": code, **extra}
    if campo:
        detail["campo"] = campo
    return HTTPException(codigo, detail=detail)


async def _pessoas(session: AsyncSession, ids: set[UUID | None]) -> dict[UUID, User]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {u.id: u for u in (await session.scalars(select(User).where(User.id.in_(ids)))).all()}


def _ref(pessoas: dict[UUID, User], uid: UUID | None) -> PessoaRef | None:
    if uid is None:
        return None
    return PessoaRef(id=uid, nome=svc.nome_pessoa(pessoas.get(uid)))


def _out(item: Imobilizado, pessoas: dict[UUID, User]) -> ImobilizadoOut:
    return ImobilizadoOut(
        id=item.id,
        numero=item.numero,
        descricao=item.descricao,
        valor=item.valor,
        responsavel=_ref(pessoas, item.responsavel_id),
        status=item.status,
        baixa_data=item.baixa_data,
        baixa_motivo=item.baixa_motivo,
        criado_em=item.criado_em,
        criado_por=_ref(pessoas, item.criado_por),
        atualizado_em=item.atualizado_em,
        atualizado_por=_ref(pessoas, item.atualizado_por),
    )


async def _out_um(session: AsyncSession, item: Imobilizado) -> ImobilizadoOut:
    await session.refresh(item)
    pessoas = await _pessoas(session, {item.responsavel_id, item.criado_por, item.atualizado_por})
    return _out(item, pessoas)


async def _item_visivel(session: AsyncSession, item_id: int, user: User) -> Imobilizado:
    item = await session.get(Imobilizado, item_id)
    # Colaborador: item de outra pessoa responde igual a item que não existe.
    if item is None or (not _ve_tudo(user) and item.responsavel_id != user.id):
        raise _erro(404, "item_nao_encontrado")
    return item


async def _item_editavel(session: AsyncSession, item_id: int) -> Imobilizado:
    item = await session.get(Imobilizado, item_id, with_for_update=True)
    if item is None:
        raise _erro(404, "item_nao_encontrado")
    if item.status == "baixado":
        raise _erro(409, "item_baixado")  # RN06
    return item


async def _responsavel_ativo(session: AsyncSession, uid: UUID) -> User:
    u = await session.get(User, uid)
    if not svc.pessoa_ativa(u):
        raise _erro(422, "responsavel_inativo", "responsavel_id")  # RN04
    return u


@router.get("", response_model=ImobilizadoListaOut)
async def listar(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_active_user)],
    busca: str | None = None,
    responsavel_id: UUID | None = None,
    status_: Annotated[Literal["ativo", "baixado", "todos"], Query(alias="status")] = "ativo",
) -> ImobilizadoListaOut:
    filtros = []
    if not _ve_tudo(user):
        filtros.append(Imobilizado.responsavel_id == user.id)
    elif responsavel_id:
        filtros.append(Imobilizado.responsavel_id == responsavel_id)
    if status_ != "todos":
        filtros.append(Imobilizado.status == status_)
    termo = (busca or "").strip()
    if termo:
        like = f"%{termo}%"
        filtros.append(or_(Imobilizado.numero.ilike(like), Imobilizado.descricao.ilike(like)))
    itens = (
        await session.scalars(select(Imobilizado).where(*filtros).order_by(Imobilizado.numero))
    ).all()
    pessoas = await _pessoas(
        session,
        {p for i in itens for p in (i.responsavel_id, i.criado_por, i.atualizado_por)},
    )
    return ImobilizadoListaOut(
        itens=[_out(i, pessoas) for i in itens],
        quantidade=len(itens),
        soma=sum((i.valor for i in itens), Decimal("0.00")),
    )


@router.get("/proximo-numero")
async def proximo_numero(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_edit)],
) -> dict[str, str]:
    """Sugestão para o campo Número: o maior IMB-NNNNNN + 1. A pessoa pode
    trocar pelo número da plaqueta que já está no bem."""
    maior = await session.scalar(
        select(func.max(func.substring(Imobilizado.numero, r"^IMB-(\d{1,15})$").cast(BigInteger)))
    )
    return {"numero": f"{PREFIXO}{(maior or 0) + 1:06d}"}


@router.get("/responsaveis", response_model=list[PessoaRef])
async def responsaveis(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_view)],
) -> list[PessoaRef]:
    """Usuários ativos (os únicos que o formulário oferece, RN04) e quem não
    está mais ativo mas ainda tem item — esses só servem para o filtro."""
    com_item = select(Imobilizado.responsavel_id).distinct()
    pessoas = (
        await session.scalars(
            select(User).where(
                or_(
                    (User.status == UserStatus.ACTIVE) & User.disabled_at.is_(None),
                    User.id.in_(com_item),
                )
            )
        )
    ).all()
    out = [PessoaRef(id=u.id, nome=svc.nome_pessoa(u), ativo=svc.pessoa_ativa(u)) for u in pessoas]
    return sorted(out, key=lambda p: p.nome.casefold())


@router.post("/transferir", response_model=ImobilizadoTransferirOut)
async def transferir(
    body: ImobilizadoTransferir,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> ImobilizadoTransferirOut:
    """Todos os itens ativos de uma pessoa passam para outra — o que a tela de
    Usuários oferece antes de desativar alguém responsável por bens (RN08)."""
    if body.de_id == body.para_id:
        raise _erro(422, "mesma_pessoa", "para_id")
    de = await session.get(User, body.de_id)
    if de is None:
        raise _erro(404, "usuario_nao_encontrado", "de_id")
    para = await _responsavel_ativo(session, body.para_id)
    itens = await svc.itens_ativos_de(session, de.id)
    for item in itens:
        svc.trocar_responsavel(session, item, de, para, user)
    await session.commit()
    return ImobilizadoTransferirOut(transferidos=len(itens))


@router.get("/{item_id}", response_model=ImobilizadoOut)
async def detalhe(
    item_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_active_user)],
) -> ImobilizadoOut:
    return await _out_um(session, await _item_visivel(session, item_id, user))


@router.post("", response_model=ImobilizadoOut, status_code=status.HTTP_201_CREATED)
async def cadastrar(
    body: ImobilizadoCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> ImobilizadoOut:
    if await session.scalar(select(Imobilizado.id).where(Imobilizado.numero == body.numero)):
        raise _erro(409, "numero_duplicado", "numero", numero=body.numero)  # RN01
    await _responsavel_ativo(session, body.responsavel_id)
    item = Imobilizado(
        numero=body.numero,
        descricao=body.descricao,
        valor=body.valor,
        responsavel_id=body.responsavel_id,
        status="ativo",
        criado_por=user.id,
    )
    session.add(item)
    try:
        await session.commit()
    except IntegrityError as e:
        # Duas pessoas salvando o mesmo número ao mesmo tempo.
        await session.rollback()
        raise _erro(409, "numero_duplicado", "numero", numero=body.numero) from e
    return await _out_um(session, item)


@router.put("/{item_id}", response_model=ImobilizadoOut)
async def editar(
    item_id: int,
    body: ImobilizadoUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> ImobilizadoOut:
    item = await _item_editavel(session, item_id)
    mudou = False
    if body.descricao is not None and body.descricao != item.descricao:
        svc.registrar(session, item, "descricao", item.descricao, body.descricao, user)
        item.descricao = body.descricao
        mudou = True
    if body.valor is not None and body.valor != item.valor:
        svc.registrar(
            session, item, "valor", svc.texto_valor(item.valor), svc.texto_valor(body.valor), user
        )
        item.valor = body.valor
        mudou = True
    if body.responsavel_id is not None and body.responsavel_id != item.responsavel_id:
        para = await _responsavel_ativo(session, body.responsavel_id)
        de = await session.get(User, item.responsavel_id)
        svc.trocar_responsavel(session, item, de, para, user)
        mudou = True
    if mudou:
        item.atualizado_em = func.now()
        item.atualizado_por = user.id
        await session.commit()
    return await _out_um(session, item)


@router.post("/{item_id}/baixa", response_model=ImobilizadoOut)
async def dar_baixa(
    item_id: int,
    body: ImobilizadoBaixa,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_baixa)],
) -> ImobilizadoOut:
    if body.baixa_data > datetime.now(SAO_PAULO).date():
        raise _erro(422, "baixa_data_futura", "baixa_data")
    item = await _item_editavel(session, item_id)
    item.status = "baixado"
    item.baixa_data = body.baixa_data
    item.baixa_motivo = body.baixa_motivo
    item.atualizado_em = func.now()
    item.atualizado_por = user.id
    svc.registrar(session, item, "status", "ativo", "baixado", user)
    await session.commit()
    return await _out_um(session, item)


@router.get("/{item_id}/historico", response_model=list[ImobilizadoHistoricoOut])
async def historico(
    item_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_active_user)],
) -> list[ImobilizadoHistoricoOut]:
    item = await _item_visivel(session, item_id, user)
    linhas = (
        await session.scalars(
            select(ImobilizadoHistorico)
            .where(ImobilizadoHistorico.imobilizado_id == item.id)
            .order_by(ImobilizadoHistorico.alterado_em.desc(), ImobilizadoHistorico.id.desc())
        )
    ).all()
    pessoas = await _pessoas(session, {h.alterado_por for h in linhas})
    return [
        ImobilizadoHistoricoOut(
            id=h.id,
            campo=h.campo,
            valor_anterior=h.valor_anterior,
            valor_novo=h.valor_novo,
            alterado_em=h.alterado_em,
            alterado_por=_ref(pessoas, h.alterado_por),
        )
        for h in linhas
    ]
