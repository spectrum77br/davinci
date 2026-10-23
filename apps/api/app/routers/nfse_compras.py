"""Cadastros › Contabilidade › Compra (23/10/2026).

Eduardo: "o nome emissao de serviço vai ser trocado por contabilidade, dentro vai
ter o nome emissao de servico e do lado [...] a nova aba compra". Mora na mesma
página (`/emissao-servico`) e debaixo da mesma senha extra e da mesma permissão
(`emissao_servico`): ver = view; cadastrar produto, registrar compra, marcar a
NF e baixar a planilha = edit; excluir = delete. Regras em `services/compra.py`.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import User
from app.models.compra import CompraPedido, CompraPedidoItem, CompraProduto
from app.schemas.compra import (
    CompradorOut,
    CompraIn,
    CompraOut,
    FaturamentoOut,
    ItemOut,
    NfIn,
    OpcoesOut,
    PlanilhaIn,
    ProdutoIn,
    ProdutoOut,
)
from app.security.senha_extra import require_nfse_unlock
from app.services import compra as svc
from app.services.nfse.erros import NfseError

logger = structlog.get_logger()
router = APIRouter(
    prefix="/api/nfse/compras", tags=["nfse-compras"], dependencies=[Depends(require_nfse_unlock)]
)


_view = require_permission("emissao_servico", "view")
_edit = require_permission("emissao_servico", "edit")
_delete = require_permission("emissao_servico", "delete")
Sess = Annotated[AsyncSession, Depends(get_session)]


def _http(e: NfseError) -> HTTPException:
    return HTTPException(e.status, detail=e.detail)


def _erro(status: int, code: str, mensagem: str) -> HTTPException:
    return HTTPException(status, detail={"code": code, "mensagem": mensagem})


async def _nomes(session: AsyncSession, ids: set[UUID | None]) -> dict[UUID, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    linhas = (
        await session.execute(select(User.id, User.name, User.email).where(User.id.in_(ids)))
    ).all()
    return {r.id: (r.name or r.email) for r in linhas}


def _saida(p: CompraPedido, itens: list[CompraPedidoItem], nomes: dict[UUID, str]) -> CompraOut:
    return CompraOut(
        id=p.id,
        numero=p.numero,
        company_id=p.company_id,
        comprador=p.comprador,
        vendedor_cnpj=p.vendedor_cnpj,
        vendedor_nome=p.vendedor_nome,
        vendedor_uf=p.vendedor_uf,
        competencia=p.competencia,
        data_pedido=p.data_pedido,
        faturamento=p.faturamento,
        modo=p.modo,
        percentual=p.percentual,
        cfop=p.cfop,
        total=p.total,
        status=p.status,
        nf_numero=p.nf_numero,
        nf_chave=p.nf_chave,
        emissao_pedida_em=p.emissao_pedida_em,
        emissao_iniciada_em=p.emissao_iniciada_em,
        emissao_tentativas=p.emissao_tentativas or 0,
        emissao_erro=p.emissao_erro,
        nf_emitida_em=p.nf_emitida_em,
        observacoes=p.observacoes,
        itens=[ItemOut.model_validate(i) for i in itens],
        created_at=p.created_at,
        updated_at=p.updated_at,
        criado_por=nomes.get(p.created_by) if p.created_by else None,
    )


async def _uma(session: AsyncSession, pedido_id: UUID, *, travar: bool = False) -> CompraPedido:
    q = select(CompraPedido).where(CompraPedido.id == pedido_id)
    if travar:
        q = q.with_for_update()
    p = (await session.execute(q)).scalar_one_or_none()
    if p is None:
        raise _erro(404, "compra_nao_encontrada", "Compra não encontrada.")
    return p


async def _devolver(session: AsyncSession, p: CompraPedido) -> CompraOut:
    itens = (await svc.itens_de(session, [p.id]))[p.id]
    return _saida(p, itens, await _nomes(session, {p.created_by}))


# --- o que a tela precisa para montar a compra ----------------------------------------


@router.get("/opcoes", response_model=OpcoesOut)
async def opcoes(session: Sess, _u: Annotated[User, Depends(_view)]) -> OpcoesOut:
    """As contas Bling de NF (quem vende) e os produtos ativos."""
    return OpcoesOut(
        vendedoras=await svc.vendedoras(session),
        produtos=await svc.produtos(session, so_ativos=True),
    )


@router.get("/comprador/{company_id}", response_model=CompradorOut)
async def comprador(
    company_id: UUID, session: Sess, _u: Annotated[User, Depends(_view)]
) -> CompradorOut:
    try:
        return CompradorOut(**await svc.comprador(session, company_id))
    except NfseError as e:
        raise _http(e) from e


@router.get("/faturamento", response_model=FaturamentoOut)
async def faturamento(
    session: Sess,
    _u: Annotated[User, Depends(_view)],
    company_id: UUID = Query(...),
    competencia: date = Query(...),
) -> FaturamentoOut:
    return FaturamentoOut(**await svc.faturamento(session, company_id, competencia))


# --- produtos ---------------------------------------------------------------------------


@router.get("/produtos", response_model=list[ProdutoOut])
async def listar_produtos(session: Sess, _u: Annotated[User, Depends(_view)]) -> list[ProdutoOut]:
    return [ProdutoOut(**p) for p in await svc.produtos(session)]


@router.post("/produtos", response_model=ProdutoOut, status_code=201)
async def criar_produto(
    body: ProdutoIn, session: Sess, u: Annotated[User, Depends(_edit)]
) -> ProdutoOut:
    if await svc.nome_em_uso(session, body.nome):
        raise _erro(409, "produto_repetido", f'Já existe um produto chamado "{body.nome}".')
    p = CompraProduto(**body.model_dump(), created_by=u.id)
    session.add(p)
    await session.commit()
    await session.refresh(p)
    logger.info("compra_produto_criado", produto=str(p.id), nome=p.nome, user=str(u.id))
    return ProdutoOut.model_validate(p)


@router.put("/produtos/{produto_id}", response_model=ProdutoOut)
async def editar_produto(
    produto_id: UUID, body: ProdutoIn, session: Sess, u: Annotated[User, Depends(_edit)]
) -> ProdutoOut:
    p = await session.get(CompraProduto, produto_id)
    if p is None:
        raise _erro(404, "produto_nao_encontrado", "Produto não encontrado.")
    if await svc.nome_em_uso(session, body.nome, exceto=produto_id):
        raise _erro(409, "produto_repetido", f'Já existe um produto chamado "{body.nome}".')
    for k, v in body.model_dump().items():
        setattr(p, k, v)
    await session.commit()
    await session.refresh(p)
    usado = next((x["usado"] for x in await svc.produtos(session) if x["id"] == p.id), 0)
    logger.info("compra_produto_editado", produto=str(p.id), user=str(u.id))
    return ProdutoOut(**{**ProdutoOut.model_validate(p).model_dump(), "usado": usado})


@router.delete("/produtos/{produto_id}", status_code=204)
async def excluir_produto(
    produto_id: UUID, session: Sess, u: Annotated[User, Depends(_delete)]
) -> Response:
    p = await session.get(CompraProduto, produto_id)
    if p is None:
        raise _erro(404, "produto_nao_encontrado", "Produto não encontrado.")
    usado = next((x["usado"] for x in await svc.produtos(session) if x["id"] == p.id), 0)
    if usado:
        raise _erro(
            409,
            "produto_em_uso",
            f"Esse produto está em {usado} compra(s): desative em vez de excluir "
            "(as compras ficam como estão).",
        )
    await session.delete(p)
    await session.commit()
    logger.info("compra_produto_excluido", produto=str(produto_id), user=str(u.id))
    return Response(status_code=204)


# --- compras ------------------------------------------------------------------------------


@router.get("", response_model=list[CompraOut])
async def listar(
    session: Sess,
    _u: Annotated[User, Depends(_view)],
    competencia: date | None = Query(None),
    company_id: UUID | None = Query(None),
    status: str | None = Query(None),
) -> list[CompraOut]:
    q = select(CompraPedido).order_by(CompraPedido.numero.desc())
    if competencia is not None:
        q = q.where(CompraPedido.competencia == competencia.replace(day=1))
    if company_id is not None:
        q = q.where(CompraPedido.company_id == company_id)
    if status:
        q = q.where(CompraPedido.status == status)
    pedidos = list((await session.execute(q.limit(500))).scalars())
    itens = await svc.itens_de(session, [p.id for p in pedidos])
    nomes = await _nomes(session, {p.created_by for p in pedidos})
    return [_saida(p, itens[p.id], nomes) for p in pedidos]


@router.get("/{pedido_id}", response_model=CompraOut)
async def uma(pedido_id: UUID, session: Sess, _u: Annotated[User, Depends(_view)]) -> CompraOut:
    return await _devolver(session, await _uma(session, pedido_id))


@router.post("", response_model=CompraOut, status_code=201)
async def registrar(body: CompraIn, session: Sess, u: Annotated[User, Depends(_edit)]) -> CompraOut:
    try:
        m = await svc.montar(session, body)
    except NfseError as e:
        raise _http(e) from e
    p = CompraPedido(**m["pedido"], status="registrada", created_by=u.id, updated_by=u.id)
    session.add(p)
    await session.flush()
    for it in m["itens"]:
        session.add(CompraPedidoItem(pedido_id=p.id, **it))
    await session.commit()
    await session.refresh(p)
    logger.info(
        "compra_registrada", compra=str(p.id), numero=p.numero, total=str(p.total), user=str(u.id)
    )
    return await _devolver(session, p)


@router.put("/{pedido_id}", response_model=CompraOut)
async def editar(
    pedido_id: UUID, body: CompraIn, session: Sess, u: Annotated[User, Depends(_edit)]
) -> CompraOut:
    p = await _uma(session, pedido_id, travar=True)
    if p.status not in ("registrada", "erro"):
        raise _erro(
            409,
            "compra_travada",
            "Só dá para editar compra sem NF e fora da fila (registrada ou com erro). "
            "Tire da fila, desfaça a NF ou reabra a compra antes.",
        )
    atual = {"cnpj": p.vendedor_cnpj, "nome": p.vendedor_nome, "uf": p.vendedor_uf}
    foto = p.comprador if body.company_id == p.company_id else None
    try:
        m = await svc.montar(session, body, vendedora_atual=atual, comprador_atual=foto)
    except NfseError as e:
        raise _http(e) from e
    for k, v in m["pedido"].items():
        setattr(p, k, v)
    p.updated_by = u.id
    for it in (await svc.itens_de(session, [p.id]))[p.id]:
        await session.delete(it)
    await session.flush()
    for it in m["itens"]:
        session.add(CompraPedidoItem(pedido_id=p.id, **it))
    await session.commit()
    await session.refresh(p)
    logger.info("compra_editada", compra=str(p.id), numero=p.numero, user=str(u.id))
    return await _devolver(session, p)


@router.post("/{pedido_id}/nf", response_model=CompraOut)
async def marcar_nf(
    pedido_id: UUID, body: NfIn, session: Sess, u: Annotated[User, Depends(_edit)]
) -> CompraOut:
    """Anota o nº da NF que saiu no Bling da vendedora (ou corrige o número)."""
    p = await _uma(session, pedido_id, travar=True)
    if p.status == "cancelada":
        raise _erro(409, "compra_cancelada", "Compra cancelada: reabra antes de anotar a NF.")
    if p.status in ("na_fila", "emitindo"):
        raise _erro(
            409,
            "na_fila",
            "A compra está na fila do robô: tire da fila antes de anotar a NF à mão.",
        )
    p.status, p.nf_numero, p.updated_by = "nf_emitida", body.nf_numero, u.id
    p.emissao_erro = None
    await session.commit()
    await session.refresh(p)
    logger.info("compra_nf", compra=str(p.id), numero=p.numero, nf=p.nf_numero, user=str(u.id))
    return await _devolver(session, p)


@router.post("/{pedido_id}/desfazer-nf", response_model=CompraOut)
async def desfazer_nf(
    pedido_id: UUID, session: Sess, u: Annotated[User, Depends(_edit)]
) -> CompraOut:
    p = await _uma(session, pedido_id, travar=True)
    if p.status != "nf_emitida":
        raise _erro(409, "sem_nf", "Essa compra não tem NF anotada.")
    p.status, p.nf_numero, p.nf_chave, p.nf_emitida_em, p.updated_by = (
        "registrada",
        None,
        None,
        None,
        u.id,
    )
    await session.commit()
    await session.refresh(p)
    return await _devolver(session, p)


@router.post("/{pedido_id}/cancelar", response_model=CompraOut)
async def cancelar(pedido_id: UUID, session: Sess, u: Annotated[User, Depends(_edit)]) -> CompraOut:
    p = await _uma(session, pedido_id, travar=True)
    if p.status == "nf_emitida":
        raise _erro(
            409,
            "tem_nf",
            f"A NF {p.nf_numero} já saiu: cancele a NF no Bling da vendedora "
            "e depois use 'Desfazer NF'.",
        )
    if p.status == "emitindo":
        raise _erro(409, "emitindo", "O robô está emitindo essa NF agora: espere terminar.")
    if p.status == "cancelada":
        return await _devolver(session, p)
    p.status, p.updated_by = "cancelada", u.id
    await session.commit()
    await session.refresh(p)
    logger.info("compra_cancelada", compra=str(p.id), numero=p.numero, user=str(u.id))
    return await _devolver(session, p)


@router.post("/{pedido_id}/reabrir", response_model=CompraOut)
async def reabrir(pedido_id: UUID, session: Sess, u: Annotated[User, Depends(_edit)]) -> CompraOut:
    p = await _uma(session, pedido_id, travar=True)
    if p.status != "cancelada":
        raise _erro(409, "nao_cancelada", "Só compra cancelada pode ser reaberta.")
    p.status, p.updated_by = "registrada", u.id
    await session.commit()
    await session.refresh(p)
    return await _devolver(session, p)


@router.delete("/{pedido_id}", status_code=204)
async def excluir(pedido_id: UUID, session: Sess, u: Annotated[User, Depends(_delete)]) -> Response:
    p = await _uma(session, pedido_id, travar=True)
    if p.status == "nf_emitida":
        raise _erro(
            409,
            "tem_nf",
            "Compra com NF não se exclui: desfaça a NF antes (e cancele a NF no Bling).",
        )
    if p.status in ("na_fila", "emitindo"):
        raise _erro(409, "na_fila", "A compra está na fila do robô: tire da fila antes de excluir.")
    await session.delete(p)
    await session.commit()
    logger.info("compra_excluida", compra=str(pedido_id), numero=p.numero, user=str(u.id))
    return Response(status_code=204)


# --- planilha do Bling ---------------------------------------------------------------------


def _csv(conteudo: bytes, nome: str) -> Response:
    return Response(
        content=conteudo,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


@router.get("/{pedido_id}/planilha")
async def planilha_uma(
    pedido_id: UUID, session: Sess, _u: Annotated[User, Depends(_edit)]
) -> Response:
    p = await _uma(session, pedido_id)
    if p.status == "cancelada":
        raise _erro(409, "compra_cancelada", "Compra cancelada não gera planilha.")
    itens = await svc.itens_de(session, [p.id])
    return _csv(svc.planilha([p], itens), svc.nome_da_planilha([p]))


@router.post("/planilha")
async def planilha_varias(
    body: PlanilhaIn, session: Sess, _u: Annotated[User, Depends(_edit)]
) -> Response:
    """Várias compras num arquivo só — todas da MESMA vendedora (cada planilha
    é importada no Bling de uma empresa)."""
    ids = list(dict.fromkeys(body.ids))
    pedidos = list(
        (
            await session.execute(
                select(CompraPedido).where(CompraPedido.id.in_(ids)).order_by(CompraPedido.numero)
            )
        ).scalars()
    )
    if len(pedidos) != len(ids):
        raise _erro(404, "compra_nao_encontrada", "Alguma das compras escolhidas não existe mais.")
    if any(p.status == "cancelada" for p in pedidos):
        raise _erro(409, "compra_cancelada", "Tire as compras canceladas da seleção.")
    com_nf = [p for p in pedidos if p.status in ("nf_emitida", "na_fila", "emitindo")]
    if com_nf:
        # o pedido já está no Bling: número repetido = o Bling recusa o arquivo INTEIRO
        raise _erro(
            409,
            "compra_com_nf",
            "Tire da seleção as compras que já têm NF (o pedido já está no Bling e o número "
            "repetido faz o Bling recusar o arquivo todo): nº "
            + ", ".join(str(p.numero) for p in com_nf)
            + ".",
        )
    if len({p.vendedor_cnpj for p in pedidos}) > 1:
        raise _erro(
            422,
            "vendedoras_diferentes",
            "Escolha compras de uma empresa que vende só: cada planilha é importada "
            "no Bling de uma empresa.",
        )
    itens = await svc.itens_de(session, [p.id for p in pedidos])
    return _csv(svc.planilha(pedidos, itens), svc.nome_da_planilha(pedidos))
