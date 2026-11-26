"""Cadastros › Produtos (M-29, 24/11/2026): a lista oficial de categorias e tags de envio.

Tabela Tipo | Nome (models/produto_cadastro.py, migration 0508). Quem pode:
- ver a aba: permissão `cadastro_produtos` (view), como cada aba do grupo Cadastros tem a sua; a
  0508 deu essa view a quem já via alguma aba de Cadastros (item 11 do pedido);
- criar, editar e excluir: só administrador, no servidor (`require_admin`);
- GET /listas (só os nomes, para os filtros Tags e Categoria do Produtos 2): qualquer pessoa
  logada e ativa — é a lista dos filtros, não a tela de cadastro.

Regras do pedido:
- nome repetido dentro do mesmo tipo (sem olhar maiúsculas): 409 `nome_repetido`;
- Categoria com produto (contado na base do ERP, pelo nome): excluir, renomear ou trocar de tipo
  é RECUSADO (409 `categoria_em_uso`, com quantos usam). Sem resposta do ERP também é recusado
  (503 `erp_indisponivel`): sem conferir, não dá para garantir que nenhum produto perde a
  categoria. A ligação produto → categoria continua no ERP e não é mexida aqui;
- Tag de envio com produto (SKU que termina com ela): excluir/renomear/trocar de tipo pede
  confirmação (409 `tag_em_uso` com a contagem); com `confirmar=true`, vai;
- renomear TIPO é permitido (a regra segue a `chave` do tipo, não o nome).

M-75 (25/11/2026): o tipo "Categoria de venda" são os segmentos filhos das raízes de
Cadastros › Segmentos (tabela `segments`, NÃO um cadastro novo), com a raiz como "Centro de
custo". Aparecem na mesma lista (GET "") e são incluídos, renomeados e excluídos pelas rotas
/categorias-venda (só administrador). As regras que seguram as colunas de margem/frete estão em
services/categorias_venda.py (as mesmas da aba Segmentos).

M-76 (25/11/2026): o menu "Centro de custo" do Novo cadastro de Categoria de venda vem de
Cadastros › Centro de custo (routers/centros_custo.py); centro novo (sem raiz) aparece no menu,
mas ainda não recebe categoria de venda ("Categoria de venda só em Celular, Eletro e Mala por
enquanto.").
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session, is_unique_violation
from app.deps.auth import require_active_user, require_admin, require_permission
from app.models import ProdutoCadastro, ProdutoCadastroTipo, Segment, User
from app.models.produto_cadastro import CHAVE_CATEGORIA, CHAVE_TAG_ENVIO, NOME_MAX
from app.services import categorias_venda as cv
from app.services import centros_custo
from app.services import produto_cadastros as svc

router = APIRouter(prefix="/api/produto-cadastros", tags=["produto_cadastros"])

_view = require_permission("cadastro_produtos", "view")


# ---------------------------------------------------------------- formatos


class TipoOut(BaseModel):
    id: UUID
    nome: str
    chave: str | None


class CadastroOut(BaseModel):
    id: UUID
    tipo_id: UUID
    tipo_nome: str
    tipo_chave: str | None
    nome: str
    # M-75: só nas linhas de Categoria de venda (nas outras: None / True).
    centro_custo: str | None = None
    ativa: bool = True


class CentroCustoOut(BaseModel):
    id: UUID
    nome: str
    # M-76: só Celular, Eletro e Mala (os centros com raiz em Segmentos) recebem categoria de
    # venda por enquanto; a tela avisa ao escolher um centro novo.
    aceita_categoria: bool = True


class ListaOut(BaseModel):
    tipos: list[TipoOut]
    cadastros: list[CadastroOut]
    # O menu do "Novo cadastro" de Categoria de venda: M-75 eram as raízes de Segmentos; desde a
    # M-76, a lista de Cadastros › Centro de custo (id = centros_custo.id).
    centros_custo: list[CentroCustoOut] = []


class ListasFiltroOut(BaseModel):
    categorias: list[str]
    tags_envio: list[str]


class UsoOut(BaseModel):
    regra: str | None
    produtos: int | None
    bloqueia: bool
    mensagem: str | None


class TipoIn(BaseModel):
    nome: str = Field(min_length=1, max_length=200)


class CadastroIn(BaseModel):
    tipo_id: UUID
    nome: str = Field(min_length=1, max_length=200)


class CadastroPatch(BaseModel):
    tipo_id: UUID | None = None
    nome: str | None = Field(default=None, min_length=1, max_length=200)
    confirmar: bool = False


class VendaIn(BaseModel):
    centro_custo_id: UUID
    nome: str = Field(min_length=1, max_length=200)


class VendaPatch(BaseModel):
    nome: str = Field(min_length=1, max_length=200)


# ---------------------------------------------------------------- ajudantes


def _nome(bruto: str, o_que: str) -> str:
    nome = svc.normalizar(bruto)
    if not nome:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "nome_vazio", "mensagem": f"Informe o nome do {o_que}."},
        )
    if len(nome) > NOME_MAX:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "nome_longo",
                "mensagem": f"O nome do {o_que} vai até {NOME_MAX} letras.",
            },
        )
    return nome


def _cadastro_out(c: ProdutoCadastro, t: ProdutoCadastroTipo) -> CadastroOut:
    return CadastroOut(id=c.id, tipo_id=t.id, tipo_nome=t.nome, tipo_chave=t.chave, nome=c.nome)


def _venda_out(seg: Segment, raiz: Segment, centro_nome: str | None = None) -> CadastroOut:
    """Categoria de venda (M-75): a linha é o próprio segmento filho (id = segments.id). O Centro
    de custo é o nome do cadastro de Centro de custo (M-76) ou, na raiz que não é centro (o
    Catálogo), o nome da raiz."""
    return CadastroOut(
        id=seg.id,
        tipo_id=cv.TIPO_ID,
        tipo_nome=cv.TIPO_NOME,
        tipo_chave=cv.CHAVE,
        nome=seg.name,
        centro_custo=centro_nome or raiz.name,
        ativa=seg.active,
    )


async def _nome_do_centro(session: AsyncSession, raiz: Segment) -> str:
    return (await centros_custo.nomes_por_raiz(session)).get(raiz.id, raiz.name)


async def _venda(session: AsyncSession, venda_id: UUID) -> tuple[Segment, Segment]:
    seg = await session.get(Segment, venda_id)
    raiz = await cv.raiz_de(session, seg) if seg is not None else None
    if seg is None or raiz is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={
                "code": "categoria_venda_nao_encontrada",
                "mensagem": "Categoria de venda não encontrada.",
            },
        )
    return seg, raiz


async def _tipo(session: AsyncSession, tipo_id: UUID) -> ProdutoCadastroTipo:
    tipo = await session.get(ProdutoCadastroTipo, tipo_id)
    if tipo is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "tipo_nao_encontrado", "mensagem": "Tipo não encontrado."},
        )
    return tipo


async def _cadastro(session: AsyncSession, cadastro_id: UUID) -> ProdutoCadastro:
    cad = await session.get(ProdutoCadastro, cadastro_id)
    if cad is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "cadastro_nao_encontrado", "mensagem": "Cadastro não encontrado."},
        )
    return cad


async def _sem_repetir_tipo(session: AsyncSession, nome: str, menos: UUID | None = None) -> None:
    if svc.igual(nome, cv.TIPO_NOME):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "tipo_repetido", "mensagem": f"Já existe o tipo “{cv.TIPO_NOME}”."},
        )
    q = select(ProdutoCadastroTipo.id).where(func.lower(ProdutoCadastroTipo.nome) == nome.lower())
    if menos is not None:
        q = q.where(ProdutoCadastroTipo.id != menos)
    if (await session.execute(q)).first():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "tipo_repetido", "mensagem": f"Já existe o tipo “{nome}”."},
        )


async def _sem_repetir_nome(
    session: AsyncSession, tipo: ProdutoCadastroTipo, nome: str, menos: UUID | None = None
) -> None:
    q = select(ProdutoCadastro.id).where(
        ProdutoCadastro.tipo_id == tipo.id, func.lower(ProdutoCadastro.nome) == nome.lower()
    )
    if menos is not None:
        q = q.where(ProdutoCadastro.id != menos)
    if (await session.execute(q)).first():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "nome_repetido",
                "mensagem": f"Já existe “{nome}” em {tipo.nome}.",
            },
        )


def _repetido(e: IntegrityError) -> HTTPException:
    """A corrida que passa pela pré-checagem: o índice único responde."""
    if is_unique_violation(e):
        return HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "nome_repetido", "mensagem": "Esse nome já existe neste tipo."},
        )
    raise e


def _plural(n: int) -> str:
    return "1 produto" if n == 1 else f"{n:,} produtos".replace(",", ".")


async def _uso(tipo: ProdutoCadastroTipo, nome: str) -> UsoOut:
    """Quantos produtos usam este nome, para os tipos que têm regra."""
    if tipo.chave == CHAVE_CATEGORIA:
        uso = await svc.uso_no_erp()
        if uso is None:
            return UsoOut(
                regra=CHAVE_CATEGORIA,
                produtos=None,
                bloqueia=True,
                mensagem=(
                    "Não consegui conferir na base do Produtos 2 quantos produtos usam a "
                    f"categoria “{nome}”. Nada foi mudado; tente de novo em instantes."
                ),
            )
        n = svc.produtos_na_categoria(uso, nome)
        return UsoOut(
            regra=CHAVE_CATEGORIA,
            produtos=n,
            bloqueia=n > 0,
            mensagem=(
                f"{_plural(n)} {'usa' if n == 1 else 'usam'} a categoria “{nome}”. Troque a "
                "categoria desses produtos antes: nenhum produto pode perder a categoria."
            )
            if n
            else None,
        )
    if tipo.chave == CHAVE_TAG_ENVIO:
        uso = await svc.uso_no_erp([nome])
        n = None if uso is None else svc.produtos_com_tag(uso, nome)
        if n is None:
            msg = f"Não consegui contar quantos produtos terminam com “{nome}”."
        elif n:
            msg = f"{_plural(n)} {'termina' if n == 1 else 'terminam'} com “{nome}”."
        else:
            msg = None
        return UsoOut(regra=CHAVE_TAG_ENVIO, produtos=n, bloqueia=False, mensagem=msg)
    return UsoOut(regra=None, produtos=None, bloqueia=False, mensagem=None)


async def _pode_tirar(tipo: ProdutoCadastroTipo, nome: str, confirmar: bool) -> None:
    """O nome deixa de existir no tipo (excluir, renomear, trocar de tipo): confere o uso."""
    uso = await _uso(tipo, nome)
    if uso.regra == CHAVE_CATEGORIA and uso.bloqueia:
        if uso.produtos is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "erp_indisponivel", "mensagem": uso.mensagem},
            )
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "categoria_em_uso",
                "produtos": uso.produtos,
                "mensagem": uso.mensagem,
            },
        )
    if uso.regra == CHAVE_TAG_ENVIO and (uso.produtos is None or uso.produtos > 0):
        if not confirmar:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "code": "tag_em_uso",
                    "produtos": uso.produtos,
                    "precisa_confirmar": True,
                    "mensagem": uso.mensagem,
                },
            )


# ---------------------------------------------------------------- leitura


@router.get("", response_model=ListaOut)
async def listar(
    _user: Annotated[User, Depends(_view)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ListaOut:
    tipos = list((await session.execute(select(ProdutoCadastroTipo))).scalars())
    por_id = {t.id: t for t in tipos}
    cadastros = [
        _cadastro_out(c, por_id[c.tipo_id])
        for c in (await session.execute(select(ProdutoCadastro))).scalars()
    ]
    cadastros.sort(key=lambda c: (svc.ordem(c.tipo_nome), svc.ordem(c.nome)))
    # M-75: Categoria de venda = os segmentos filhos, por centro de custo (Celular, Eletro,
    # Mala e depois o Catálogo) e na ordem das colunas de margem/frete (cv.listar); o sort
    # estável só encaixa o tipo entre os outros.
    nomes = await centros_custo.nomes_por_raiz(session)
    cadastros += [_venda_out(s, raiz, nomes.get(raiz.id)) for s, raiz in await cv.listar(session)]
    cadastros.sort(key=lambda c: svc.ordem(c.tipo_nome))
    tipos_out = [TipoOut(id=t.id, nome=t.nome, chave=t.chave) for t in tipos]
    tipos_out.append(TipoOut(id=cv.TIPO_ID, nome=cv.TIPO_NOME, chave=cv.CHAVE))
    tipos_out.sort(key=lambda t: svc.ordem(t.nome))
    return ListaOut(
        tipos=tipos_out,
        cadastros=cadastros,
        centros_custo=[
            CentroCustoOut(id=c.id, nome=c.nome, aceita_categoria=raiz is not None)
            for c, raiz in await cv.centros_do_menu(session)
        ],
    )


@router.get("/listas", response_model=ListasFiltroOut)
async def listas_dos_filtros(
    _user: Annotated[User, Depends(require_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ListasFiltroOut:
    """Os nomes dos filtros Categoria e Tags do Produtos 2 (M-30), em ordem alfabética."""
    linhas = (
        await session.execute(
            select(ProdutoCadastroTipo.chave, ProdutoCadastro.nome)
            .join(ProdutoCadastroTipo, ProdutoCadastroTipo.id == ProdutoCadastro.tipo_id)
            .where(ProdutoCadastroTipo.chave.in_((CHAVE_CATEGORIA, CHAVE_TAG_ENVIO)))
        )
    ).all()

    def de(chave: str) -> list[str]:
        return sorted((n for c, n in linhas if c == chave), key=svc.ordem)

    return ListasFiltroOut(categorias=de(CHAVE_CATEGORIA), tags_envio=de(CHAVE_TAG_ENVIO))


@router.get("/{cadastro_id}/uso", response_model=UsoOut)
async def uso(
    cadastro_id: UUID,
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UsoOut:
    """Para a tela avisar antes de excluir: quantos produtos usam (e se a exclusão é barrada)."""
    cad = await _cadastro(session, cadastro_id)
    return await _uso(await _tipo(session, cad.tipo_id), cad.nome)


# ---------------------------------------------------------------- tipos (só admin)


@router.post("/tipos", response_model=TipoOut, status_code=status.HTTP_201_CREATED)
async def criar_tipo(
    body: TipoIn,
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TipoOut:
    nome = _nome(body.nome, "tipo")
    await _sem_repetir_tipo(session, nome)
    tipo = ProdutoCadastroTipo(nome=nome)
    session.add(tipo)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        raise _repetido(e) from e
    return TipoOut(id=tipo.id, nome=tipo.nome, chave=tipo.chave)


@router.patch("/tipos/{tipo_id}", response_model=TipoOut)
async def renomear_tipo(
    tipo_id: UUID,
    body: TipoIn,
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TipoOut:
    tipo = await _tipo(session, tipo_id)
    nome = _nome(body.nome, "tipo")
    await _sem_repetir_tipo(session, nome, menos=tipo.id)
    tipo.nome = nome
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        raise _repetido(e) from e
    return TipoOut(id=tipo.id, nome=tipo.nome, chave=tipo.chave)


# ---------------------------------------------------------------- cadastros (só admin)


@router.post("", response_model=CadastroOut, status_code=status.HTTP_201_CREATED)
async def criar(
    body: CadastroIn,
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CadastroOut:
    tipo = await _tipo(session, body.tipo_id)
    nome = _nome(body.nome, "cadastro")
    await _sem_repetir_nome(session, tipo, nome)
    cad = ProdutoCadastro(tipo_id=tipo.id, nome=nome)
    session.add(cad)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        raise _repetido(e) from e
    return _cadastro_out(cad, tipo)


@router.patch("/{cadastro_id}", response_model=CadastroOut)
async def editar(
    cadastro_id: UUID,
    body: CadastroPatch,
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CadastroOut:
    cad = await _cadastro(session, cadastro_id)
    antes = await _tipo(session, cad.tipo_id)
    depois = await _tipo(session, body.tipo_id) if body.tipo_id else antes
    nome = _nome(body.nome, "cadastro") if body.nome is not None else cad.nome
    if depois.id == antes.id and nome == cad.nome:
        return _cadastro_out(cad, antes)
    await _sem_repetir_nome(session, depois, nome, menos=cad.id)
    # Trocar só maiúsculas ("celular" → "Celular") no mesmo tipo não tira o nome de ninguém.
    if depois.id != antes.id or not svc.igual(nome, cad.nome):
        await _pode_tirar(antes, cad.nome, body.confirmar)
    cad.tipo_id = depois.id
    cad.nome = nome
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        raise _repetido(e) from e
    return _cadastro_out(cad, depois)


@router.delete("/{cadastro_id}", status_code=status.HTTP_204_NO_CONTENT)
async def excluir(
    cadastro_id: UUID,
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    confirmar: Annotated[bool, Query()] = False,
) -> Response:
    cad = await _cadastro(session, cadastro_id)
    await _pode_tirar(await _tipo(session, cad.tipo_id), cad.nome, confirmar)
    await session.delete(cad)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- categorias de venda (M-75)
# O mesmo dado da aba Segmentos (segments): o que muda aqui aparece lá, na aba Contas e na
# coluna Tabela da Tabela de Preços › Produtos. Margem Mín, Condição Especial e DUIMP/mensagem
# continuam só na aba Segmentos.


@router.post("/categorias-venda", response_model=CadastroOut, status_code=status.HTTP_201_CREATED)
async def criar_categoria_venda(
    body: VendaIn,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CadastroOut:
    centro, raiz = await cv.centro_para_nova_categoria(session, body.centro_custo_id)
    nome = _nome(body.nome, "cadastro")
    posicao = await cv.conferir_nova(session, raiz, nome)
    seg = Segment(
        user_id=admin.id,
        parent_id=raiz.id,
        name=nome,
        slug=await cv.slug_livre(session, raiz, nome),
        sort_order=posicao,
        active=True,
    )
    nome_centro = centro.nome  # o rollback abaixo expira os objetos da sessão
    session.add(seg)
    try:
        # Na mesma transação: as contas do centro com o slot{N} vazio passam a apontar
        # para ela (frete "Projetado" da página Margem).
        await cv.preencher_slots(session, raiz, seg)
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        if is_unique_violation(e):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "code": "nome_repetido",
                    "mensagem": (
                        f"Já existe uma categoria de venda com nome parecido em {nome_centro}."
                    ),
                },
            ) from e
        raise
    return _venda_out(seg, raiz, nome_centro)


@router.patch("/categorias-venda/{venda_id}", response_model=CadastroOut)
async def renomear_categoria_venda(
    venda_id: UUID,
    body: VendaPatch,
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CadastroOut:
    """Só o nome (o centro de custo e a posição não mudam por aqui)."""
    seg, raiz = await _venda(session, venda_id)
    nome = _nome(body.nome, "cadastro")
    centro_nome = await _nome_do_centro(session, raiz)
    if nome == seg.name:
        return _venda_out(seg, raiz, centro_nome)
    cv.conferir_troca_de_nome(seg.name, nome)
    await cv.sem_repetir_no_centro(session, raiz, nome, menos=seg.id)
    seg.name = nome
    await session.commit()
    return _venda_out(seg, raiz, centro_nome)


@router.get("/categorias-venda/{venda_id}/uso", response_model=UsoOut)
async def uso_categoria_venda(
    venda_id: UUID,
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UsoOut:
    """Para a lixeira avisar antes: quantos produtos usam e se a exclusão é barrada."""
    seg, raiz = await _venda(session, venda_id)
    uso, bloqueio = await cv.bloqueio_exclusao(session, seg, raiz)
    return UsoOut(
        regra=cv.CHAVE,
        produtos=uso.produtos,
        bloqueia=bloqueio is not None,
        mensagem=bloqueio.mensagem if bloqueio else None,
    )


@router.delete("/categorias-venda/{venda_id}", status_code=status.HTTP_204_NO_CONTENT)
async def excluir_categoria_venda(
    venda_id: UUID,
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    seg, raiz = await _venda(session, venda_id)
    await cv.excluir(session, seg, raiz)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
