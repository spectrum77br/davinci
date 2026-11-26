"""Cadastros › Centro de custo (M-76, Harry 25/11/2026): regras e contagem de uso.

Os três fixos (Celular, Eletro e Mala) são as raízes de Cadastros › Segmentos (`segment_id`).
Centro novo nasce sem raiz. Regras (o router chama daqui; a aba Segmentos também):
- nome com até 60 letras, sem repetir (sem olhar maiúsculas); nem o nome de uma raiz de
  Segmentos que não é centro de custo (o "Catálogo"), para não haver dois "Catálogo";
- os fixos não mudam de nome (aqui nem pela aba Segmentos): as outras telas têm Celular, Eletro e
  Mala escritos fixos (lib/categorias.ts, Lojas, Importação, Produtos 2…);
- excluir: fixo nunca; os outros só sem uso (`uso_do_centro`).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CentroCusto,
    PricingAccount,
    PricingProduct,
    Product,
    ProdutoRegra,
    Segment,
    StoreInfo,
    User,
)
from app.models.centro_custo import NOME_MAX
from app.services.categorias_ordem import posicao_categoria
from app.services.produto_cadastros import igual, normalizar, ordem
from app.services.store_departments import resolve_store_departments

MSG_FIXO_NOME = (
    "Celular, Eletro e Mala não mudam de nome: o sistema procura esses nomes em outras telas."
)


def _recusa(st: int, code: str, mensagem: str, **extra: object) -> HTTPException:
    return HTTPException(st, detail={"code": code, "mensagem": mensagem, **extra})


def _qtd(n: int, um: str, varios: str) -> str:
    return f"1 {um}" if n == 1 else f"{n:,} {varios}".replace(",", ".")


def _lista(partes: list[str]) -> str:
    return partes[0] if len(partes) == 1 else ", ".join(partes[:-1]) + " e " + partes[-1]


# ---------------------------------------------------------------- leitura


async def listar(session: AsyncSession) -> list[tuple[CentroCusto, Segment | None]]:
    """[(centro, raiz)]: os fixos em Celular, Eletro, Mala (M-23) e os novos depois, por nome."""
    linhas = (
        await session.execute(
            select(CentroCusto, Segment).outerjoin(Segment, Segment.id == CentroCusto.segment_id)
        )
    ).all()
    pares = [(c, r) for c, r in linhas]
    pares.sort(
        key=lambda p: (posicao_categoria(p[1].slug if p[1] is not None else None), ordem(p[0].nome))
    )
    return pares


async def obter(session: AsyncSession, centro_id: UUID) -> CentroCusto:
    centro = await session.get(CentroCusto, centro_id)
    if centro is None:
        raise _recusa(
            status.HTTP_404_NOT_FOUND,
            "centro_nao_encontrado",
            "Centro de custo não encontrado.",
        )
    return centro


async def nomes_por_raiz(session: AsyncSession) -> dict[UUID, str]:
    """{id da raiz: nome do centro} — a coluna "Centro de custo" das Categorias de venda."""
    linhas = (
        await session.execute(
            select(CentroCusto.segment_id, CentroCusto.nome).where(
                CentroCusto.segment_id.is_not(None)
            )
        )
    ).all()
    return dict(linhas)


# ---------------------------------------------------------------- nome


def conferir_nome(bruto: str) -> str:
    nome = normalizar(bruto)
    if not nome:
        raise _recusa(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "nome_vazio",
            "Informe o nome do centro de custo.",
        )
    if len(nome) > NOME_MAX:
        raise _recusa(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "nome_longo",
            f"O nome do centro de custo vai até {NOME_MAX} letras.",
        )
    return nome


async def sem_repetir(session: AsyncSession, nome: str, menos: UUID | None = None) -> None:
    q = select(CentroCusto.nome).where(func.lower(CentroCusto.nome) == nome.lower())
    if menos is not None:
        q = q.where(CentroCusto.id != menos)
    existente = (await session.execute(q)).scalar_one_or_none()
    if existente is not None:
        raise _recusa(
            status.HTTP_409_CONFLICT,
            "nome_repetido",
            f"Já existe o centro de custo “{existente}”.",
        )
    # Uma raiz de Segmentos que NÃO é centro de custo (o "Catálogo"): o nome fica com ela, senão
    # a lista de Categorias de venda teria dois "Catálogo" (um com categorias e um sem).
    ligadas = select(CentroCusto.segment_id).where(CentroCusto.segment_id.is_not(None))
    raizes = (
        await session.execute(
            select(Segment.name).where(Segment.parent_id.is_(None), Segment.id.not_in(ligadas))
        )
    ).scalars()
    for r in raizes:
        if igual(r, nome):
            raise _recusa(
                status.HTTP_409_CONFLICT,
                "nome_de_segmento",
                f"“{r}” é o nome de um segmento antigo de Cadastros › Segmentos, que não é "
                "centro de custo. Escolha outro nome.",
            )


def recusa_fixo_nome() -> HTTPException:
    return _recusa(status.HTTP_400_BAD_REQUEST, "centro_fixo_nome", MSG_FIXO_NOME)


async def centro_da_raiz(session: AsyncSession, raiz_id: UUID) -> CentroCusto | None:
    return (
        await session.execute(select(CentroCusto).where(CentroCusto.segment_id == raiz_id))
    ).scalar_one_or_none()


# ---------------------------------------------------------------- uso


@dataclass
class UsoCentro:
    categorias: int  # Categorias de venda (filhos da raiz, inativas também)
    contas: int  # contas da Tabela de Preços na raiz ou num filho
    lojas: int  # lojas com o departamento (services/store_departments)
    produtos_tabela: int  # linhas da Tabela de Preços num segmento do centro
    produtos_cadastro: int  # produtos (cadastro) num segmento do centro
    # M-73: produtos do Produtos 2 com este centro ESCOLHIDO (produto_regras.centro_custo_id). Os
    # em branco usam o departamento, calculado, e não entram aqui.
    produtos_centro: int = 0

    @property
    def produtos(self) -> int:
        return self.produtos_tabela + self.produtos_cadastro + self.produtos_centro

    @property
    def total(self) -> int:
        return self.categorias + self.contas + self.lojas + self.produtos

    def texto(self) -> str:
        partes = []
        if self.categorias:
            partes.append(_qtd(self.categorias, "categoria de venda", "categorias de venda"))
        if self.contas:
            partes.append(_qtd(self.contas, "conta", "contas"))
        if self.lojas:
            partes.append(_qtd(self.lojas, "loja", "lojas"))
        if self.produtos_tabela:
            partes.append(
                _qtd(
                    self.produtos_tabela,
                    "produto da Tabela de Preços",
                    "produtos da Tabela de Preços",
                )
            )
        if self.produtos_cadastro:
            partes.append(
                _qtd(self.produtos_cadastro, "produto do cadastro", "produtos do cadastro")
            )
        if self.produtos_centro:
            partes.append(
                _qtd(
                    self.produtos_centro,
                    "produto com este centro no Produtos 2",
                    "produtos com este centro no Produtos 2",
                )
            )
        return _lista(partes) if partes else "nada"


async def _arvore(session: AsyncSession, raiz_id: UUID) -> tuple[set[UUID], set[UUID]]:
    """(filhos diretos da raiz, a raiz e todos os descendentes). Tabela pequena: lida inteira."""
    linhas = (await session.execute(select(Segment.id, Segment.parent_id))).all()
    filhos: dict[UUID, list[UUID]] = {}
    for sid, pai in linhas:
        if pai is not None:
            filhos.setdefault(pai, []).append(sid)
    todos: set[UUID] = set()
    fila = [raiz_id]
    while fila:
        sid = fila.pop()
        if sid in todos:
            continue
        todos.add(sid)
        fila.extend(filhos.get(sid, ()))
    return set(filhos.get(raiz_id, ())), todos


async def uso_do_centro(session: AsyncSession, centro: CentroCusto, quem: User) -> UsoCentro:
    """Quem USA o centro de custo — o ÚNICO lugar que conta. Desde a M-73 conta também os produtos
    do Produtos 2 que ESCOLHERAM o centro (`produto_regras.centro_custo_id`), o que vale até para
    um centro sem raiz ("Brinquedos"). O resto depende da raiz: centro sem raiz não tem categoria,
    conta, loja nem linha da Tabela de Preços.

    `quem` só passa ao services/store_departments (o mesmo das telas Lojas e Faturamento); o
    router manda o administrador, que vê todas as lojas."""

    async def conta(q) -> int:
        return int((await session.execute(q)).scalar_one() or 0)

    produtos_centro = await conta(
        select(func.count())
        .select_from(ProdutoRegra)
        .where(ProdutoRegra.centro_custo_id == centro.id)
    )
    if centro.segment_id is None:
        return UsoCentro(0, 0, 0, 0, 0, produtos_centro)
    raiz = await session.get(Segment, centro.segment_id)
    if raiz is None:
        return UsoCentro(0, 0, 0, 0, 0, produtos_centro)
    diretos, arvore = await _arvore(session, raiz.id)

    lojas = list((await session.execute(select(StoreInfo))).scalars())
    departamentos = await resolve_store_departments(session, quem, lojas)
    return UsoCentro(
        categorias=len(diretos),
        contas=await conta(
            select(func.count(PricingAccount.id)).where(PricingAccount.segment_id.in_(arvore))
        ),
        lojas=sum(1 for d in departamentos.values() if raiz.slug in d.departments),
        produtos_tabela=await conta(
            select(func.count(PricingProduct.id)).where(PricingProduct.segment_id.in_(arvore))
        ),
        produtos_cadastro=await conta(
            select(func.count(Product.id)).where(Product.segment_id.in_(arvore))
        ),
        produtos_centro=produtos_centro,
    )


async def bloqueio_exclusao(
    session: AsyncSession, centro: CentroCusto, quem: User
) -> tuple[UsoCentro, str | None, str | None]:
    """(uso, code, mensagem) — code None = pode excluir."""
    uso = await uso_do_centro(session, centro, quem)
    if centro.fixo:
        return (
            uso,
            "centro_fixo",
            f"“{centro.nome}” não pode ser excluído: Celular, Eletro e Mala são fixos do "
            f"sistema. Hoje usam: {uso.texto()}.",
        )
    if uso.total:
        return (
            uso,
            "centro_em_uso",
            f"“{centro.nome}” está em uso: {uso.texto()}. Só dá para excluir um centro de "
            "custo que ninguém usa.",
        )
    return uso, None, None
