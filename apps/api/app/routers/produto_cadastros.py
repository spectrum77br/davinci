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

M-89 (26/11/2026): coluna "Tabela" — a tabela do sistema que cada cadastro alimenta:
- Categoria e Tag de envio (e os tipos criados na tela, que ficam na mesma lista) → "Produtos 2";
- Categoria de venda → "Contas Celular" / "Contas Eletro" / "Contas Mala" (as três tabelas da aba
  Contas, pelo centro de custo). As do Catálogo (raiz que não é centro de custo) saem desta lista:
  o catálogo é uma opção dentro das três tabelas. Nada é apagado — seguem em Cadastros ›
  Segmentos e em todo cálculo que as lê;
- tipo novo "Tipo de conta" (tabela "Lojas"): os centros de custo com raiz (Celular, Eletro,
  Mala), no fim da lista. Só para ver: não tem rota de criar, editar nem excluir.

M-90 (26/11/2026): a lixeira da Categoria de venda confere o uso só na tabela dela (Tabela de
Preços e contas). Em uso, a janela pede o destino (outra categoria de venda ativa do mesmo
centro) e o DELETE vai com `destino_id`: transfere e exclui num commit só. Os produtos da tela
Produtos (products.segment_id) não travam: vão junto ou ficam sem segmento. Categoria e Tag de
envio (Produtos 2) seguem como antes: a ligação produto → categoria/tag mora na base do ERP, que
esta API só lê — transferir exigiria gravar lá (e a tag faz parte do SKU).

M-91 (26/11/2026): a lixeira da Categoria de venda aceita a do meio. As seguintes sobem uma
posição e, em todas as contas do centro, a margem/frete de cada uma anda junto (o GET /uso diz
quais andam em `sobem` e em quantas contas a margem/frete da excluída somem em `contas_centro`).
Pela aba Segmentos continua só a última (services/categorias_venda.py).

M-107 (27/11/2026, migration 0521): o tipo "Cor" (chave 'cor', Tabela "Produtos 2"). Os nomes
entram pelo "Novo cadastro" e seguem as regras da Categoria: só administrador mexe; nome repetido
no tipo é recusado (sem olhar maiúsculas); cor em uso (produtos com `produto_regras.cor_id`
nela, contados no próprio DaVinci) NÃO sai nem muda de tipo (409 `cor_em_uso`, com quantos usam).
Diferente da Categoria, RENOMEAR a cor em uso pode: o produto guarda o id, então todos passam a
mostrar o nome novo (item 13 do pedido). GET /cores: as cores (id e nome, em ordem alfabética),
para o menu do campo Cor do cadastro do Produtos 2 — qualquer pessoa logada e ativa, como /listas.
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
from app.models import (
    CentroCusto,
    ProdutoCadastro,
    ProdutoCadastroTipo,
    ProdutoRegra,
    Segment,
    User,
)
from app.models.produto_cadastro import CHAVE_CATEGORIA, CHAVE_COR, CHAVE_TAG_ENVIO, NOME_MAX
from app.services import categorias_venda as cv
from app.services import centros_custo
from app.services import produto_cadastros as svc
from app.services.pricing.custo_total import recalcular_contas_da_raiz

router = APIRouter(prefix="/api/produto-cadastros", tags=["produto_cadastros"])

_view = require_permission("cadastro_produtos", "view")

# M-89: os nomes da coluna "Tabela".
TABELA_PRODUTOS2 = "Produtos 2"
TABELA_CONTAS = "Contas"  # o tipo Categoria de venda; cada linha diz qual: "Contas Celular"…
TABELA_LOJAS = "Lojas"


def tabela_contas(centro_nome: str) -> str:
    """A tabela da aba Contas de um centro de custo: "Contas Celular", "Contas Eletro"…"""
    return f"{TABELA_CONTAS} {centro_nome}"


# ---------------------------------------------------------------- formatos


class TipoOut(BaseModel):
    id: UUID
    nome: str
    chave: str | None
    # M-89: a tabela do tipo ("Produtos 2", "Contas" ou "Lojas").
    tabela: str | None = TABELA_PRODUTOS2


class CadastroOut(BaseModel):
    id: UUID
    tipo_id: UUID
    tipo_nome: str
    tipo_chave: str | None
    nome: str
    # M-75: só nas linhas de Categoria de venda (nas outras: None / True).
    centro_custo: str | None = None
    ativa: bool = True
    # M-89: a coluna "Tabela".
    tabela: str | None = TABELA_PRODUTOS2


class CentroCustoOut(BaseModel):
    id: UUID
    nome: str
    # M-76: só Celular, Eletro e Mala (os centros com raiz em Segmentos) recebem categoria de
    # venda por enquanto; a tela avisa ao escolher um centro novo.
    aceita_categoria: bool = True
    # M-89: a tabela do "Novo cadastro" ("Contas Celular"…); None no centro sem raiz, que não tem
    # tabela na aba Contas.
    tabela: str | None = None


class ListaOut(BaseModel):
    tipos: list[TipoOut]
    cadastros: list[CadastroOut]
    # O menu do "Novo cadastro" de Categoria de venda: M-75 eram as raízes de Segmentos; desde a
    # M-76, a lista de Cadastros › Centro de custo (id = centros_custo.id).
    centros_custo: list[CentroCustoOut] = []


class ListasFiltroOut(BaseModel):
    categorias: list[str]
    tags_envio: list[str]


class CorOut(BaseModel):
    id: UUID
    nome: str


class CoresOut(BaseModel):
    """M-107: as cores do tipo "Cor", em ordem alfabética (lista vazia sem nenhuma)."""

    cores: list[CorOut]


class UsoOut(BaseModel):
    regra: str | None
    produtos: int | None
    bloqueia: bool
    mensagem: str | None


class DestinoOut(BaseModel):
    id: UUID
    nome: str


class UsoVendaOut(UsoOut):
    """M-90: a lixeira da Categoria de venda. `produtos` = os da Tabela de Preços (a tabela dela);
    em uso (`precisa_destino`), `mensagem` é o quadro vermelho e a tela pede o destino entre
    `destinos` (as outras ativas do mesmo centro). `produtos_tela_produtos` (products.segment_id)
    não trava: vão junto para o destino ou ficam sem segmento. `avisos`: frases da confirmação
    simples (nome reservado; produtos da tela Produtos que ficam sem segmento).
    M-91: `sobem` = as categorias que andam uma posição na aba Contas (vazia se for a última) e
    `contas_centro` = em quantas contas do centro a margem/frete dela são apagados."""

    contas: int = 0
    produtos_tela_produtos: int = 0
    precisa_destino: bool = False
    destinos: list[DestinoOut] = []
    centro_custo: str | None = None
    avisos: list[str] = []
    sobem: list[str] = []
    contas_centro: int = 0


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
    Catálogo), o nome da raiz. M-89: a Tabela é a do centro na aba Contas ("Contas Celular");
    a raiz que não é centro (o Catálogo) não tem tabela (None)."""
    return CadastroOut(
        id=seg.id,
        tipo_id=cv.TIPO_ID,
        tipo_nome=cv.TIPO_NOME,
        tipo_chave=cv.CHAVE,
        nome=seg.name,
        centro_custo=centro_nome or raiz.name,
        ativa=seg.active,
        tabela=tabela_contas(centro_nome) if centro_nome else None,
    )


def _tipo_conta_out(centro: CentroCusto) -> CadastroOut:
    """Tipo de conta (M-89): a linha é o próprio centro de custo (id = centros_custo.id)."""
    return CadastroOut(
        id=centro.id,
        tipo_id=centros_custo.TIPO_CONTA_ID,
        tipo_nome=centros_custo.TIPO_CONTA_NOME,
        tipo_chave=centros_custo.CHAVE_TIPO_CONTA,
        nome=centro.nome,
        tabela=TABELA_LOJAS,
    )


async def _nome_do_centro(session: AsyncSession, raiz: Segment) -> str | None:
    """O nome do centro de custo da raiz; None na raiz que não é centro (o Catálogo)."""
    return (await centros_custo.nomes_por_raiz(session)).get(raiz.id)


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
    # Os tipos de id fixo (sem linha em produto_cadastro_tipos) também são nomes ocupados.
    for fixo in (cv.TIPO_NOME, centros_custo.TIPO_CONTA_NOME):
        if svc.igual(nome, fixo):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={"code": "tipo_repetido", "mensagem": f"Já existe o tipo “{fixo}”."},
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


async def produtos_com_cor(session: AsyncSession, cor_id: UUID) -> int:
    """M-107: quantos produtos (códigos sem a tag de envio) têm esta cor."""
    return (
        await session.execute(
            select(func.count()).select_from(ProdutoRegra).where(ProdutoRegra.cor_id == cor_id)
        )
    ).scalar_one()


async def _uso(
    tipo: ProdutoCadastroTipo,
    nome: str,
    session: AsyncSession | None = None,
    cadastro_id: UUID | None = None,
) -> UsoOut:
    """Quantos produtos usam este nome, para os tipos que têm regra."""
    if tipo.chave == CHAVE_COR and session is not None and cadastro_id is not None:
        # M-107: a cor do produto mora no DaVinci, pelo id (não precisa perguntar ao ERP).
        n = await produtos_com_cor(session, cadastro_id)
        return UsoOut(
            regra=CHAVE_COR,
            produtos=n,
            bloqueia=n > 0,
            mensagem=(
                f"{_plural(n)} {'usa' if n == 1 else 'usam'} a cor “{nome}”. Troque a cor "
                "desses produtos antes: nenhum produto pode perder a cor."
            )
            if n
            else None,
        )
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


async def _pode_tirar(
    tipo: ProdutoCadastroTipo,
    nome: str,
    confirmar: bool,
    session: AsyncSession | None = None,
    cadastro_id: UUID | None = None,
) -> None:
    """O nome deixa de existir no tipo (excluir, renomear, trocar de tipo): confere o uso."""
    uso = await _uso(tipo, nome, session, cadastro_id)
    if uso.regra == CHAVE_COR and uso.bloqueia:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "cor_em_uso", "produtos": uso.produtos, "mensagem": uso.mensagem},
        )
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
    # Mala) e na ordem das colunas de margem/frete (cv.listar); o sort estável só encaixa o tipo
    # entre os outros. M-89: só as das três tabelas de Contas (raiz ligada a um centro de
    # custo); as do Catálogo não aparecem aqui (continuam em Cadastros › Segmentos).
    nomes = await centros_custo.nomes_por_raiz(session)
    cadastros += [
        _venda_out(s, raiz, nomes[raiz.id])
        for s, raiz in await cv.listar(session)
        if raiz.id in nomes
    ]
    cadastros.sort(key=lambda c: svc.ordem(c.tipo_nome))
    # M-89: Tipo de conta (tabela Lojas) = os centros de custo com raiz, no fim da lista.
    centros = await cv.centros_do_menu(session)
    cadastros += [_tipo_conta_out(c) for c, raiz in centros if raiz is not None]
    tipos_out = [TipoOut(id=t.id, nome=t.nome, chave=t.chave) for t in tipos]
    tipos_out.append(
        TipoOut(id=cv.TIPO_ID, nome=cv.TIPO_NOME, chave=cv.CHAVE, tabela=TABELA_CONTAS)
    )
    tipos_out.sort(key=lambda t: svc.ordem(t.nome))
    tipos_out.append(
        TipoOut(
            id=centros_custo.TIPO_CONTA_ID,
            nome=centros_custo.TIPO_CONTA_NOME,
            chave=centros_custo.CHAVE_TIPO_CONTA,
            tabela=TABELA_LOJAS,
        )
    )
    return ListaOut(
        tipos=tipos_out,
        cadastros=cadastros,
        centros_custo=[
            CentroCustoOut(
                id=c.id,
                nome=c.nome,
                aceita_categoria=raiz is not None,
                tabela=tabela_contas(c.nome) if raiz is not None else None,
            )
            for c, raiz in centros
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


@router.get("/cores", response_model=CoresOut)
async def cores(
    _user: Annotated[User, Depends(require_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CoresOut:
    """M-107: o menu do campo Cor do cadastro do Produtos 2 — só as cores cadastradas aqui (tipo
    de chave 'cor'), em ordem alfabética, lidas agora (cor nova já aparece)."""
    linhas = (
        await session.execute(
            select(ProdutoCadastro.id, ProdutoCadastro.nome)
            .join(ProdutoCadastroTipo, ProdutoCadastroTipo.id == ProdutoCadastro.tipo_id)
            .where(ProdutoCadastroTipo.chave == CHAVE_COR)
        )
    ).all()
    return CoresOut(
        cores=[CorOut(id=i, nome=n) for i, n in sorted(linhas, key=lambda r: svc.ordem(r[1]))]
    )


@router.get("/{cadastro_id}/uso", response_model=UsoOut)
async def uso(
    cadastro_id: UUID,
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UsoOut:
    """Para a tela avisar antes de excluir: quantos produtos usam (e se a exclusão é barrada)."""
    cad = await _cadastro(session, cadastro_id)
    return await _uso(await _tipo(session, cad.tipo_id), cad.nome, session, cad.id)


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
    mudou_tipo = depois.id != antes.id
    # Trocar só maiúsculas ("celular" → "Celular") no mesmo tipo não tira o nome de ninguém.
    # M-107: renomear a cor também não (o produto guarda o id e mostra o nome novo); só sair do
    # tipo Cor confere o uso.
    if antes.chave == CHAVE_COR:
        if mudou_tipo:
            await _pode_tirar(antes, cad.nome, body.confirmar, session, cad.id)
    elif mudou_tipo or not svc.igual(nome, cad.nome):
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
    await _pode_tirar(await _tipo(session, cad.tipo_id), cad.nome, confirmar, session, cad.id)
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
        # M-86: uma coluna "marg" a mais na aba Contas → o Custo total das contas do centro
        # com Margem mínima é recalculado.
        await recalcular_contas_da_raiz(session, raiz.id)
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


@router.get("/categorias-venda/{venda_id}/uso", response_model=UsoVendaOut)
async def uso_categoria_venda(
    venda_id: UUID,
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UsoVendaOut:
    """Para a lixeira avisar antes: o uso na tabela dela, se a exclusão é barrada e, em uso
    (M-90), para quais categorias de venda dá para transferir."""
    seg, raiz = await _venda(session, venda_id)
    uso, bloqueio = await cv.bloqueio_exclusao(session, seg, raiz, pela_lixeira=True)
    precisa_destino = bloqueio is None and uso.em_uso
    opcoes = await cv.destinos(session, seg, raiz) if precisa_destino else []
    if bloqueio is not None:
        mensagem: str | None = bloqueio.mensagem
    elif precisa_destino:
        mensagem = cv.mensagem_em_uso(seg, raiz, uso)
    else:
        mensagem = None
    # M-91: a do meio sai e as seguintes andam uma posição (a janela lista quais).
    sobem, contas_centro = (
        await cv.previa_colunas(session, seg, raiz) if bloqueio is None else ([], 0)
    )
    return UsoVendaOut(
        regra=cv.CHAVE,
        produtos=uso.produtos,
        bloqueia=bloqueio is not None,
        mensagem=mensagem,
        contas=uso.contas,
        produtos_tela_produtos=uso.produtos_cadastro,
        precisa_destino=precisa_destino,
        destinos=[DestinoOut(id=s.id, nome=s.name) for s in opcoes],
        centro_custo=raiz.name,
        avisos=cv.avisos_exclusao(seg, uso) if bloqueio is None else [],
        sobem=sobem,
        contas_centro=contas_centro,
    )


@router.delete("/categorias-venda/{venda_id}", status_code=status.HTTP_204_NO_CONTENT)
async def excluir_categoria_venda(
    venda_id: UUID,
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    destino_id: Annotated[UUID | None, Query()] = None,
) -> Response:
    """M-90: em uso na tabela dela, vai com `destino_id` (outra categoria de venda ativa do
    mesmo centro): produtos e contas passam para ela e a categoria sai — tudo num commit só.
    M-91: pode ser a do meio — as seguintes sobem uma posição e a margem/frete de cada conta do
    centro andam junto, no mesmo commit."""
    seg, raiz = await _venda(session, venda_id)
    await cv.excluir(session, seg, raiz, destino_id, pela_lixeira=True)
    # M-86: a coluna "marg" dela sai da aba Contas (ver o criar); o Custo total é recalculado
    # com as margens que ficaram (já nas posições novas).
    await recalcular_contas_da_raiz(session, raiz.id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
