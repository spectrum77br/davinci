from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import Segment, SegmentSpecialDate, User
from app.schemas.segments import (
    SegmentCreate,
    SegmentOut,
    SegmentPatch,
    SegmentSpecialDateCreate,
    SegmentSpecialDateOut,
    SegmentTreeNode,
    _slugify,
)
from app.services import categorias_venda, centros_custo
from app.services.pricing.custo_total import recalcular_contas_da_raiz

logger = structlog.get_logger()
router = APIRouter(prefix="/api/segments", tags=["segments"])


async def _get_or_404(session: AsyncSession, sid: UUID) -> Segment:
    s = (await session.execute(select(Segment).where(Segment.id == sid))).scalar_one_or_none()
    if s is None:
        raise HTTPException(404, detail={"code": "segment_not_found"})
    return s


async def _parent_of(session: AsyncSession, sid: UUID) -> UUID | None:
    res = await session.execute(select(Segment.parent_id).where(Segment.id == sid))
    return res.scalar_one_or_none()


async def _ancestors(session: AsyncSession, sid: UUID) -> set[UUID]:
    """Returns the set of ancestor IDs (excluding `sid` itself). Used to
    block cycles when re-parenting."""
    seen: set[UUID] = set()
    cur = await _parent_of(session, sid)
    while cur is not None and cur not in seen:
        seen.add(cur)
        cur = await _parent_of(session, cur)
    return seen


@router.get("", response_model=list[SegmentOut])
async def list_segments(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("segmentos", "view"))],
) -> list[SegmentOut]:
    rows = (
        await session.execute(
            select(Segment).order_by(Segment.sort_order, Segment.name)
        )
    ).scalars().all()
    return [SegmentOut.model_validate(r) for r in rows]


@router.get("/tree", response_model=list[SegmentTreeNode])
async def segments_tree(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("segmentos", "view"))],
) -> list[SegmentTreeNode]:
    rows = (
        await session.execute(
            select(Segment).order_by(Segment.sort_order, Segment.name)
        )
    ).scalars().all()
    by_id: dict[UUID, SegmentTreeNode] = {
        r.id: SegmentTreeNode.model_validate(r) for r in rows
    }
    roots: list[SegmentTreeNode] = []
    for r in rows:
        node = by_id[r.id]
        if r.parent_id and r.parent_id in by_id:
            by_id[r.parent_id].children.append(node)
        else:
            roots.append(node)
    return roots


@router.post("", response_model=SegmentOut, status_code=status.HTTP_201_CREATED)
async def create_segment(
    body: SegmentCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("segmentos", "edit"))],
) -> SegmentOut:
    sort_order = body.sort_order
    raiz: Segment | None = None  # o centro de custo, quando o novo é categoria de venda
    if body.parent_id is not None:
        pai = await _get_or_404(session, body.parent_id)
        # M-75: filho de raiz = categoria de venda (coluna de margem/frete da
        # aba Contas). Entra SEMPRE no FIM do centro de custo (antes gravava 0,
        # a mesma da primeira; um sort_order no pedido é ignorado), até 5
        # (services/categorias_venda).
        if pai.parent_id is None:
            raiz = pai
            sort_order = await categorias_venda.conferir_nova(session, pai, body.name)
    if body.slug:
        slug = body.slug
    elif raiz is not None:
        # Slug livre no centro (uma renomeada guarda o slug antigo).
        slug = await categorias_venda.slug_livre(session, raiz, body.name)
    else:
        slug = _slugify(body.name)
    if not slug:
        raise HTTPException(400, detail={"code": "slug_invalid"})
    seg = Segment(
        user_id=user.id,
        parent_id=body.parent_id,
        name=body.name,
        slug=slug,
        sort_order=sort_order,
        active=body.active,
        duimp_padrao=body.duimp_padrao,
    )
    session.add(seg)
    try:
        if raiz is not None:
            # M-75: as contas do centro com o slot{N} vazio passam a apontar
            # para ela, na mesma transação (frete "Projetado" da Margem).
            await categorias_venda.preencher_slots(session, raiz, seg)
            # M-86: uma coluna "marg" a mais na aba Contas → o Custo total das
            # contas do centro com Margem mínima é recalculado.
            await recalcular_contas_da_raiz(session, raiz.id)
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        raise HTTPException(409, detail={"code": "segment_slug_conflict"}) from e
    await session.refresh(seg)
    return SegmentOut.model_validate(seg)


@router.patch("/{segment_id}", response_model=SegmentOut)
async def patch_segment(
    segment_id: UUID,
    body: SegmentPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("segmentos", "edit"))],
) -> SegmentOut:
    seg = await _get_or_404(session, segment_id)
    data = body.model_dump(exclude_unset=True)

    if "parent_id" in data:
        new_parent = data["parent_id"]
        if new_parent == segment_id:
            raise HTTPException(400, detail={"code": "segment_parent_self"})
        if new_parent is not None:
            await _get_or_404(session, new_parent)
            ancestors_of_new = await _ancestors(session, new_parent)
            ancestors_of_new.add(new_parent)
            # walk descendants of `seg` and ensure new_parent is not among them
            descendants = await _descendants(session, segment_id)
            if new_parent in descendants:
                raise HTTPException(400, detail={"code": "segment_parent_cycle"})

    # M-76: a raiz de um centro de custo (Cadastros › Centro de custo) não vira filha de outro
    # segmento; Celular, Eletro e Mala não mudam de nome (as outras telas têm os três escritos
    # fixos); a raiz de um centro novo troca o nome junto com o do centro.
    centro = await centros_custo.centro_da_raiz(session, seg.id)
    if centro is not None:
        if data.get("parent_id") is not None:
            raise HTTPException(
                400,
                detail={
                    "code": "centro_custo_raiz",
                    "mensagem": (
                        f"“{seg.name}” é um centro de custo (Cadastros › Centro de custo) e "
                        "não vira filho de outro segmento."
                    ),
                },
            )
        novo_nome = data.get("name")
        if novo_nome is not None and novo_nome != seg.name:
            if centro.fixo:
                raise centros_custo.recusa_fixo_nome()
            nome = centros_custo.conferir_nome(novo_nome)
            await centros_custo.sem_repetir(session, nome, menos=centro.id)
            data["name"] = nome
            centro.nome = nome

    # M-75: "Apple" e "Queima de estoque" são procuradas pelo nome; e desativar
    # ou reativar uma categoria de venda não pode tirar as outras da coluna de
    # margem/frete da aba Contas (services/categorias_venda). A posição e o
    # centro de custo de uma categoria de venda não mudam (nem um segmento vira
    # categoria por troca de pai): as colunas sairiam do lugar e o limite de 5
    # seria furado. A tela não manda esses campos; isto segura a API.
    raiz = await categorias_venda.raiz_de(session, seg)
    if raiz is not None and (
        data.get("parent_id", seg.parent_id) != seg.parent_id
        or data.get("sort_order", seg.sort_order) != seg.sort_order
    ):
        raise categorias_venda.recusar_mudanca_de_lugar()
    if raiz is None and data.get("parent_id") is not None:
        novo_pai = await _get_or_404(session, data["parent_id"])
        if novo_pai.parent_id is None:
            raise categorias_venda.recusar_mudanca_de_lugar()
    if data.get("name") is not None:
        categorias_venda.conferir_troca_de_nome(seg.name, data["name"])
        if raiz is not None:
            await categorias_venda.sem_repetir_no_centro(session, raiz, data["name"], menos=seg.id)
    if data.get("active") is not None and data["active"] != seg.active and raiz is not None:
        await categorias_venda.conferir_ativacao(session, seg, raiz, data["active"])

    muda_ativa = data.get("active") is not None and data["active"] != seg.active
    for k, v in data.items():
        setattr(seg, k, v)

    try:
        if muda_ativa and raiz is not None:
            # M-86: a coluna "marg" da categoria entra ou sai da aba Contas →
            # recalcula o Custo total das contas do centro com Margem mínima.
            await recalcular_contas_da_raiz(session, raiz.id)
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        raise HTTPException(409, detail={"code": "segment_slug_conflict"}) from e
    await session.refresh(seg)
    return SegmentOut.model_validate(seg)


async def _descendants(session: AsyncSession, sid: UUID) -> set[UUID]:
    out: set[UUID] = set()
    frontier = {sid}
    while frontier:
        children = (
            await session.execute(
                select(Segment.id).where(Segment.parent_id.in_(frontier))
            )
        ).scalars().all()
        new = set(children) - out
        if not new:
            break
        out.update(new)
        frontier = new
    return out


@router.delete("/{segment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_segment(
    segment_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("segmentos", "delete"))],
) -> None:
    seg = await _get_or_404(session, segment_id)
    # M-75: categoria de venda só sai sem produto e sem conta apontando direto
    # para ela, e só a última do centro de custo (as outras não mudam de
    # coluna). Antes vinha um erro genérico do banco quando havia produto.
    # M-91: a do meio sai só pela lixeira de Cadastros › Produtos (só ADM), que
    # anda as colunas de margem/frete das contas; aqui continua só a última.
    # Os vínculos slot1..5 das contas não contam como uso e são limpos junto.
    # M-90: produto da tela Produtos (products.segment_id) não conta como uso e
    # fica sem segmento; transferir para outra categoria só pela lixeira de
    # Cadastros › Produtos (esta rota não recebe destino: em uso, recusa).
    # M-76: a raiz de um centro de custo não sai por aqui (Celular, Eletro e Mala nunca saem;
    # a de um centro novo, só depois que o centro sair de Cadastros › Centro de custo). Sem
    # isto o banco recusaria (RESTRICT) com um erro genérico.
    centro = await centros_custo.centro_da_raiz(session, seg.id)
    if centro is not None:
        raise HTTPException(
            409,
            detail={
                "code": "centro_custo_raiz",
                "mensagem": (
                    f"“{seg.name}” é um centro de custo (Cadastros › Centro de custo) e não "
                    "sai pela aba Segmentos."
                ),
            },
        )
    raiz = await categorias_venda.raiz_de(session, seg)
    if raiz is not None:
        await categorias_venda.excluir(session, seg, raiz)
        # M-86: a coluna "marg" dela sai da aba Contas (ver o create).
        await recalcular_contas_da_raiz(session, raiz.id)
    else:
        await session.delete(seg)
    await session.commit()
    return None


# ==================================== Condição Especial (ex-"Datas Especiais")
# 10/09/2026: além do período, condição por nome do produto e/ou SKU (ver
# schemas.SegmentSpecialDateCreate). Endpoint e tabela mantêm o nome antigo.
# Eduardo (01/09/2026): "vamos colocar um novo campo chamado datas especiais,
# que é a regra que vamos aprovar, para exceção, por exemplo está com margem
# negativa, aprova". Janela em que a margem baixa não trava pedidos do
# segmento (nem dos descendentes). A regra em si é aplicada na triagem da
# Margem (_MARGEM_DATA_ESPECIAL_SQL em routers/margens.py); aqui é só o CRUD.
# Sem PATCH de propósito: editar = remover + adicionar (janelas são curtas).


@router.post(
    "/{segment_id}/special-dates",
    response_model=SegmentSpecialDateOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_special_date(
    segment_id: UUID,
    body: SegmentSpecialDateCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("segmentos", "edit"))],
) -> SegmentSpecialDateOut:
    await _get_or_404(session, segment_id)
    sd = SegmentSpecialDate(
        segment_id=segment_id,
        date_start=body.date_start,
        date_end=body.date_end,
        nome_contem=body.nome_contem,
        sku_contem=body.sku_contem,
        min_margin=body.min_margin,
    )
    session.add(sd)
    await session.commit()
    await session.refresh(sd)
    logger.info(
        "segment_special_date_created",
        segment_id=str(segment_id),
        date_start=str(body.date_start),
        date_end=str(body.date_end),
        nome_contem=body.nome_contem,
        sku_contem=body.sku_contem,
        min_margin=None if body.min_margin is None else float(body.min_margin),
    )
    return SegmentSpecialDateOut.model_validate(sd)


@router.delete(
    "/{segment_id}/special-dates/{special_date_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_special_date(
    segment_id: UUID,
    special_date_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("segmentos", "edit"))],
) -> None:
    sd = (
        await session.execute(
            select(SegmentSpecialDate).where(
                SegmentSpecialDate.id == special_date_id,
                SegmentSpecialDate.segment_id == segment_id,
            )
        )
    ).scalar_one_or_none()
    if sd is None:
        raise HTTPException(404, detail={"code": "special_date_not_found"})
    await session.delete(sd)
    await session.commit()
    logger.info(
        "segment_special_date_deleted",
        segment_id=str(segment_id),
        special_date_id=str(special_date_id),
    )
    return None
