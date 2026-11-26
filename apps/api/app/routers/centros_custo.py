"""Cadastros › Centro de custo (M-76, Harry 25/11/2026; migration 0514).

Aba nova logo depois de Cadastros › Produtos, com a mesma tela (Nome | Ações). Celular, Eletro e
Mala são as raízes de Cadastros › Segmentos (não uma lista paralela); a raiz "Catálogo" não é
centro de custo. Quem pode:
- ver (GET): quem vê Cadastros › Produtos (permissão `cadastro_produtos`, view) — é a fonte do
  menu "Centro de custo" das Categorias de venda e, depois da M-73, do Produtos 2;
- incluir, renomear e excluir: só administrador (`require_admin`), como em Cadastros › Produtos.

Regras (services/centros_custo.py):
- nome até 60 letras, sem repetir sem olhar maiúsculas (409 `nome_repetido`);
- Celular, Eletro e Mala não mudam de nome (400 `centro_fixo_nome`) e não saem (409
  `centro_fixo`, com quantos usam). Centro novo com raiz troca o nome da raiz junto (o slug não);
- excluir: só sem uso — categorias de venda, contas, lojas e produtos (`uso_do_centro`); com uso,
  409 `centro_em_uso` dizendo quantos de cada um;
- centro novo nasce SEM raiz: não vira botão em tela nenhuma e ainda não recebe Categoria de
  venda (services/categorias_venda.centro_para_nova_categoria explica o porquê).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session, is_unique_violation
from app.deps.auth import require_admin, require_permission
from app.models import CentroCusto, Segment, User
from app.schemas.centros_custo import CentroCustoIn, CentroCustoOut, UsoCentroOut
from app.services import centros_custo as svc

router = APIRouter(prefix="/api/centros-custo", tags=["centros_custo"])

_view = require_permission("cadastro_produtos", "view")


def _out(c: CentroCusto) -> CentroCustoOut:
    return CentroCustoOut(id=c.id, nome=c.nome, fixo=c.fixo, segment_id=c.segment_id)


def _repetido(e: IntegrityError) -> HTTPException:
    """A corrida que passa pela pré-checagem: o índice único em lower(nome) responde."""
    if is_unique_violation(e):
        return HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "nome_repetido",
                "mensagem": "Já existe um centro de custo com esse nome.",
            },
        )
    raise e


@router.get("", response_model=list[CentroCustoOut])
async def listar(
    _user: Annotated[User, Depends(_view)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[CentroCustoOut]:
    """Celular, Eletro, Mala e depois os novos, em ordem alfabética."""
    return [_out(c) for c, _raiz in await svc.listar(session)]


@router.get("/{centro_id}/uso", response_model=UsoCentroOut)
async def uso(
    centro_id: UUID,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UsoCentroOut:
    """Para a lixeira avisar antes: quantos usam e se a exclusão é barrada."""
    centro = await svc.obter(session, centro_id)
    u, code, mensagem = await svc.bloqueio_exclusao(session, centro, admin)
    return UsoCentroOut(
        categorias=u.categorias,
        contas=u.contas,
        lojas=u.lojas,
        produtos=u.produtos,
        produtos_tabela=u.produtos_tabela,
        produtos_cadastro=u.produtos_cadastro,
        bloqueia=code is not None,
        mensagem=mensagem,
    )


@router.post("", response_model=CentroCustoOut, status_code=status.HTTP_201_CREATED)
async def criar(
    body: CentroCustoIn,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CentroCustoOut:
    """Centro novo: só o nome. Nasce sem raiz em Segmentos (não vira botão em lugar nenhum)."""
    nome = svc.conferir_nome(body.nome)
    await svc.sem_repetir(session, nome)
    centro = CentroCusto(nome=nome, fixo=False, segment_id=None, criado_por=admin.id)
    session.add(centro)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        raise _repetido(e) from e
    await session.refresh(centro)
    return _out(centro)


@router.patch("/{centro_id}", response_model=CentroCustoOut)
async def renomear(
    centro_id: UUID,
    body: CentroCustoIn,
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CentroCustoOut:
    centro = await svc.obter(session, centro_id)
    nome = svc.conferir_nome(body.nome)
    if nome == centro.nome:
        return _out(centro)
    if centro.fixo:
        raise svc.recusa_fixo_nome()
    await svc.sem_repetir(session, nome, menos=centro.id)
    centro.nome = nome
    if centro.segment_id is not None:
        # A raiz (se um dia houver) acompanha o nome; o slug não muda.
        raiz = await session.get(Segment, centro.segment_id)
        if raiz is not None:
            raiz.name = nome
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        raise _repetido(e) from e
    return _out(centro)


@router.delete("/{centro_id}", status_code=status.HTTP_204_NO_CONTENT)
async def excluir(
    centro_id: UUID,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    centro = await svc.obter(session, centro_id)
    u, code, mensagem = await svc.bloqueio_exclusao(session, centro, admin)
    if code is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": code,
                "mensagem": mensagem,
                "categorias": u.categorias,
                "contas": u.contas,
                "lojas": u.lojas,
                "produtos": u.produtos,
            },
        )
    # Só a linha do centro sai; nenhum segmento é apagado.
    await session.delete(centro)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
