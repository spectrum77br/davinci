"""Categorias de venda (M-75, Harry 25/11/2026): os grupos de margem/frete da aba Contas.

NÃO é um cadastro novo: são os segmentos FILHOS das raízes de Cadastros › Segmentos (tabela
`segments`), mostrados e editados também em Cadastros › Produtos. A raiz é o "centro de custo"
(Celular, Eletro, Mala, Catálogo) e o filho é a categoria de venda.

O que liga o produto à margem/frete da conta é a POSIÇÃO do filho: sort_order 0..4 →
margin1..5 / shipping1..5 (routers/pricing._segment_index, services/pricing/push,
services/nf_frete_auto). E a aba Contas desenha os cabeçalhos pela ordem dos filhos ATIVOS
(pages/pricing/[tab].vue, TYPE_HEADERS): o N-ésimo ativo fica sobre margin{N}.

Por isso as regras abaixo, que seguram as posições (valem aqui e em Cadastros › Segmentos):
- a nova entra no FIM do centro de custo (maior sort_order + 1 — antes gravava 0, a mesma
  posição da primeira); com 5 (contando as inativas) é recusada: só existem 5 pares
  margem/frete. Também é recusada se a última do centro estiver inativa (a nova ficaria ativa
  depois de uma inativa e os cabeçalhos da aba Contas sairiam do lugar);
- excluir: só sem produto (Tabela de Preços e cadastro) e sem conta usando, e só a ÚLTIMA do
  centro de custo — nenhuma posição é renumerada, então nenhuma outra muda de coluna;
- desativar: só a última ATIVA; reativar: só se as de antes estiverem ativas. Assim as ativas
  ficam sempre nas primeiras posições e nenhum cabeçalho escorrega de coluna;
- "Apple" e "Queima de estoque" são procuradas pelo NOME em outras partes do sistema
  (portal_criativos, sites_custos, sites_midia, a view vw_bling_pedidos): não trocam de nome, e
  nenhuma outra passa a se chamar assim.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PricingAccount, PricingProduct, Product, Segment

# O tipo "Categoria de venda" da tela Cadastros › Produtos não tem linha em
# produto_cadastro_tipos (as linhas são os segmentos): id fixo só para a tela.
TIPO_ID = UUID("7d3c6a10-75a0-4c1e-9b0e-ca7e90c1a75e")
TIPO_NOME = "Categoria de venda"
CHAVE = "categoria_venda"

# Só existem 5 pares margem/frete por conta (margin1..5 / shipping1..5).
LIMITE = 5

# Procuradas pelo nome (lower(name) = 'apple' / 'queima de estoque') fora daqui.
NOMES_PROCURADOS = ("Apple", "Queima de estoque")


def _chave(nome: str | None) -> str:
    return " ".join((nome or "").split()).casefold()


def nome_procurado(nome: str | None) -> str | None:
    """O nome fixo ("Apple" / "Queima de estoque") se `nome` for um deles (sem olhar maiúsculas)."""
    k = _chave(nome)
    return next((n for n in NOMES_PROCURADOS if n.casefold() == k), None)


def _recusa(code: str, mensagem: str, **extra: object) -> HTTPException:
    return HTTPException(
        status.HTTP_409_CONFLICT, detail={"code": code, "mensagem": mensagem, **extra}
    )


def _qtd(n: int, um: str, varios: str) -> str:
    return f"1 {um}" if n == 1 else f"{n:,} {varios}".replace(",", ".")


# ---------------------------------------------------------------- leitura


async def raiz_de(session: AsyncSession, seg: Segment) -> Segment | None:
    """O centro de custo (a raiz) se `seg` é filho direto de uma raiz; senão None."""
    if seg.parent_id is None:
        return None
    pai = await session.get(Segment, seg.parent_id)
    if pai is None or pai.parent_id is not None:
        return None
    return pai


async def filhas(session: AsyncSession, raiz_id: UUID) -> list[Segment]:
    """As categorias de venda do centro de custo, na ordem das colunas (inativas também)."""
    return list(
        (
            await session.execute(
                select(Segment)
                .where(Segment.parent_id == raiz_id)
                .order_by(Segment.sort_order, Segment.name)
            )
        ).scalars()
    )


async def listar(session: AsyncSession) -> tuple[list[Segment], list[tuple[Segment, Segment]]]:
    """(centros de custo, [(categoria de venda, centro de custo)]) — as categorias na ordem das
    colunas dentro de cada centro."""
    q = select(Segment).order_by(Segment.sort_order, Segment.name)
    linhas = list((await session.execute(q)).scalars())
    raizes = {s.id: s for s in linhas if s.parent_id is None}
    vendas = [(s, raizes[s.parent_id]) for s in linhas if s.parent_id in raizes]
    return list(raizes.values()), vendas


# ---------------------------------------------------------------- incluir


async def sem_repetir_no_centro(
    session: AsyncSession, raiz: Segment, nome: str, menos: UUID | None = None
) -> None:
    """O mesmo nome pode repetir em centros diferentes (Acessórios no Celular e na Mala), não
    no mesmo."""
    for s in await filhas(session, raiz.id):
        if s.id != menos and _chave(s.name) == _chave(nome):
            raise _recusa("nome_repetido", f"Já existe “{s.name}” em {raiz.name}.")


async def conferir_nova(session: AsyncSession, raiz: Segment, nome: str) -> int:
    """Confere a nova categoria de venda e devolve a posição dela (sort_order): o fim do centro."""
    fixo = nome_procurado(nome)
    if fixo:
        raise _recusa(
            "categoria_venda_nome_reservado",
            f"“{fixo}” é um nome que outras partes do sistema procuram; escolha outro nome.",
        )
    await sem_repetir_no_centro(session, raiz, nome)
    atuais = await filhas(session, raiz.id)
    if len(atuais) >= LIMITE:
        raise _recusa(
            "categoria_venda_limite",
            f"Este centro de custo já tem {LIMITE} categorias de venda.",
        )
    posicao = max((s.sort_order for s in atuais), default=-1) + 1
    if posicao >= LIMITE:
        raise _recusa(
            "categoria_venda_limite",
            f"As {LIMITE} colunas de margem/frete de {raiz.name} já estão ocupadas.",
        )
    if atuais and not atuais[-1].active:
        raise _recusa(
            "categoria_venda_depois_de_inativa",
            f"A última categoria de venda de {raiz.name} (“{atuais[-1].name}”) está inativa: a "
            "nova ficaria depois dela e as colunas da aba Contas sairiam do lugar. Reative ou "
            "exclua a inativa antes.",
        )
    return posicao


# ---------------------------------------------------------------- renomear e ativar


def conferir_troca_de_nome(antigo: str, novo: str) -> None:
    """"Apple" e "Queima de estoque" não trocam de nome, e nenhuma outra passa a se chamar assim.
    Só maiúsculas/espaços ("apple" → "Apple") passa: a busca não olha maiúsculas."""
    if _chave(antigo) == _chave(novo):
        return
    fixo = nome_procurado(antigo)
    if fixo:
        raise _recusa(
            "categoria_venda_nome_fixo",
            f"“{antigo}” não pode trocar de nome: outras partes do sistema procuram esta "
            f"categoria pelo nome “{fixo}”.",
        )
    fixo = nome_procurado(novo)
    if fixo:
        raise _recusa(
            "categoria_venda_nome_reservado",
            f"“{fixo}” é um nome que outras partes do sistema procuram; escolha outro nome.",
        )


async def conferir_ativacao(
    session: AsyncSession, seg: Segment, raiz: Segment, ativa: bool
) -> None:
    """Desativar só a última ativa; reativar só com as de antes ativas (as ativas ficam sempre
    nas primeiras posições e os cabeçalhos da aba Contas não escorregam)."""
    irmas = [s for s in await filhas(session, raiz.id) if s.id != seg.id]
    if not ativa:
        depois = [s for s in irmas if s.sort_order > seg.sort_order and s.active]
        if depois:
            raise _recusa(
                "categoria_venda_desativar_do_meio",
                f"Só dá para desativar a última categoria de venda ativa de {raiz.name} "
                f"(“{depois[-1].name}”): desativar “{seg.name}” faria as seguintes trocarem de "
                "coluna de margem/frete na aba Contas.",
            )
    else:
        antes = [s for s in irmas if s.sort_order < seg.sort_order and not s.active]
        if antes:
            raise _recusa(
                "categoria_venda_reativar_depois_de_inativa",
                f"Reative antes “{antes[0].name}”: “{seg.name}” vem depois dela em {raiz.name} "
                "e ficaria na coluna de margem/frete errada na aba Contas.",
            )


# ---------------------------------------------------------------- excluir


@dataclass
class Uso:
    produtos_tabela: int
    produtos_cadastro: int
    contas: int
    subsegmentos: int

    @property
    def produtos(self) -> int:
        return self.produtos_tabela + self.produtos_cadastro


async def contar_uso(session: AsyncSession, seg_id: UUID) -> Uso:
    async def conta(q) -> int:
        return int((await session.execute(q)).scalar_one() or 0)

    return Uso(
        produtos_tabela=await conta(
            select(func.count(PricingProduct.id)).where(PricingProduct.segment_id == seg_id)
        ),
        produtos_cadastro=await conta(
            select(func.count(Product.id)).where(Product.segment_id == seg_id)
        ),
        contas=await conta(
            select(func.count(PricingAccount.id)).where(
                or_(
                    PricingAccount.slot1_segment_id == seg_id,
                    PricingAccount.slot2_segment_id == seg_id,
                    PricingAccount.slot3_segment_id == seg_id,
                    PricingAccount.slot4_segment_id == seg_id,
                    PricingAccount.slot5_segment_id == seg_id,
                )
            )
        ),
        subsegmentos=await conta(
            select(func.count(Segment.id)).where(Segment.parent_id == seg_id)
        ),
    )


@dataclass
class Bloqueio:
    code: str
    mensagem: str
    produtos: int
    contas: int


async def bloqueio_exclusao(
    session: AsyncSession, seg: Segment, raiz: Segment
) -> tuple[Uso, Bloqueio | None]:
    """(uso, o porquê de não poder excluir — None = pode)."""
    uso = await contar_uso(session, seg.id)
    if uso.produtos or uso.contas:
        partes = []
        if uso.produtos_tabela:
            partes.append(
                _qtd(
                    uso.produtos_tabela,
                    "produto da Tabela de Preços",
                    "produtos da Tabela de Preços",
                )
            )
        if uso.produtos_cadastro:
            partes.append(
                _qtd(uso.produtos_cadastro, "produto do cadastro", "produtos do cadastro")
            )
        if uso.contas:
            partes.append(_qtd(uso.contas, "conta", "contas"))
        texto = partes[0] if len(partes) == 1 else ", ".join(partes[:-1]) + " e " + partes[-1]
        return uso, Bloqueio(
            "categoria_venda_em_uso",
            f"“{seg.name}” ({raiz.name}) está em uso: {texto}. Troque a categoria de venda desses "
            "produtos e a coluna dessas contas antes de excluir.",
            uso.produtos,
            uso.contas,
        )
    if uso.subsegmentos:
        return uso, Bloqueio(
            "categoria_venda_com_subsegmentos",
            f"“{seg.name}” ({raiz.name}) tem subsegmentos em Cadastros › Segmentos; tire-os antes.",
            0,
            0,
        )
    depois = [s for s in await filhas(session, raiz.id) if s.sort_order > seg.sort_order]
    if depois:
        return uso, Bloqueio(
            "categoria_venda_do_meio",
            f"Só dá para excluir a última categoria de venda de {raiz.name} "
            f"(“{depois[-1].name}”): excluir “{seg.name}” deixaria um buraco nas colunas de "
            "margem/frete da aba Contas.",
            0,
            0,
        )
    return uso, None


def recusa(b: Bloqueio) -> HTTPException:
    return _recusa(b.code, b.mensagem, produtos=b.produtos, contas=b.contas)
