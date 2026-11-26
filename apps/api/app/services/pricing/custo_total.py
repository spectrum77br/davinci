"""Custo total da conta (M-56, 25/11/2026, migration 0511).

Pedido do dono: na aba Contas da Tabela de Preços, Afiliado % + Desconto % +
Ads % da conta não pode passar do Custo total (%) da conta (desde a M-94,
Desconto + o MAIOR entre Afiliado e Ads; ver abaixo). Vazio conta como
zero; Custo total vazio = sem teto. É só controle e anotação: nada disso entra
no cálculo do preço.

O Ads da soma é o MAIOR % que pode valer para algum produto da conta:
- o % Geral, se a caixa "Geral" está ligada e o % não é nulo;
- cada % próprio de produto, se a caixa "Produto" está ligada e o % não é
  nulo.
Sem nenhum, 0.

M-78 (26/11/2026): "Geral" e "Produto" são UM OU OUTRO (`caixas_ads`). Sem
linha em `pricing_ads_conta`, ou com as duas ligadas (o que vinha de antes da
M-78), vale "Geral"; os % próprios ficam gravados, mas não valem. Nada é
reescrito no banco: a regra é aplicada na leitura.

Só contam os produtos que a janela de Ads mostra (os do departamento da conta,
`produtos_do_departamento`, a mesma condição do GET /api/pricing/ads): linha de
produto que mudou de departamento fica órfã em `pricing_ads_produto`, a janela
não mostra nem deixa apagar, e não pode travar a conta (revisão da M-56).

A trava fica no servidor, em toda gravação que mexe na soma (PATCH da conta,
PUT do Ads da conta e PUT do Ads de produto), sempre com o estado que FICARIA
gravado; se estoura, nada é gravado (422 `custo_total_estourado`).

M-86 (26/11/2026): com a Margem mínima da conta preenchida (M-85,
`margem_minima_pct`, só o administrador vê), o Custo total deixa de ser
digitado e é CALCULADO pelo caso mais apertado:

    Custo total (%) = (m − r) × (1 − k) ÷ (1 + m) × 100, arredondado PARA BAIXO
    (inteiro), nunca menor que 0;

m = a menor margem preenchida entre as categorias de venda da conta (as colunas
"marg" da aba Contas: as categorias ATIVAS do centro de custo, a N-ésima sobre
margin{N}); r = a Margem mínima (25 → 0,25); k = a comissão (no par do ML, a
maior das duas). Frete não entra. Sem nenhuma margem (ou sem comissão)
preenchida, não calcula: o Custo total fica vazio (sem teto). O valor é gravado
no `custo_total_pct` de sempre, e a trava acima continua igual com ele. A conta
é recalculada quando muda a Margem mínima, uma margem, a comissão, o centro de
custo da conta ou as categorias ativas do centro. Margem mínima apagada: o
Custo total fica com o último valor e volta a ser digitado.

Mudar a Margem mínima, uma margem ou a comissão NÃO é recusado se o Custo total
calculado ficar abaixo do que já está gravado (M-94: Desconto + o maior entre
Afiliado e Ads; a tela mostra o aviso até alguém corrigir); a trava só segura
quem mexe em Afiliado, Desconto ou Ads.

Revisão da M-86: com a conta já acima do teto, a trava aceita a gravação que
NÃO aumenta a soma e não baixa o Custo total (`nao_piora`), mesmo que a soma
ainda não caiba. A tela grava um campo por vez: sem isso, quando a sobra é
maior que cada campo sozinho (ex.: Custo total calculado 0 com Afiliado e
Desconto preenchidos), toda correção era recusada e ninguém saía do aviso. A
gravação que aumenta a soma acima do teto, ou que baixa o teto abaixo dela,
continua recusada como antes.

M-94 (26/11/2026): a venda vem por Ads OU por afiliado, nunca pelos dois. A
trava passa a ser Desconto + o MAIOR entre Afiliado e Ads ≤ Custo total, ou
seja, as duas contas têm de caber: Afiliado + Desconto e Ads + Desconto (o Ads
é o de sempre, o maior que pode valer). A "soma" da trava, do `nao_piora` e do
aviso da tela é esse Desconto + o maior; a mensagem de recusa mostra só a dupla
que estourou (a maior). Nada que cabia antes passa a estourar.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models import PricingAccount, PricingAdsConta, PricingAdsProduto, PricingProduct, Segment
from app.models.pricing import CANAL_KIT
from app.services.pricing.posicoes import CAMPOS_MARGEM

ZERO = Decimal("0")
UM = Decimal("1")
CEM = Decimal("100")

# Os pares margem/frete da conta (margin1..10 desde a M-93, migration 0520); a
# categoria de venda na posição N (a N-ésima ativa do centro de custo) usa o
# margin{N}.
_MARGENS = CAMPOS_MARGEM
# M-86: o que muda o Custo total calculado, na própria conta.
CAMPOS_DA_MARGEM_MINIMA = frozenset({"margem_minima_pct", "commission", "segment_id", *_MARGENS})


def caixas_ads(geral: PricingAdsConta | None) -> tuple[bool, bool]:
    """(Geral, Produto) que valem para a conta (M-78: um ou outro). Sem linha
    ou com as duas ligadas = só "Geral"."""
    if geral is None or geral.ads_geral_ligada:
        return True, False
    return False, geral.ads_por_produto_ligada


@dataclass(frozen=True)
class EstadoAds:
    """O Ads de uma conta, como está gravado (ou como ficaria)."""

    geral_ligada: bool = True
    geral_pct: Decimal | None = None
    por_produto_ligada: bool = False
    # {pricing_product_id: % próprio (None = sem %)}
    produtos: dict[UUID, Decimal | None] = field(default_factory=dict)

    def maximo(self) -> Decimal:
        """Um ou outro (M-78): com "Geral", o % Geral; com "Produto", o maior
        % próprio (produto sem % não tem Ads); com nenhuma, 0."""
        if self.geral_ligada:
            return self.geral_pct if self.geral_pct is not None else ZERO
        if self.por_produto_ligada:
            return max((p for p in self.produtos.values() if p is not None), default=ZERO)
        return ZERO

    def com_geral(self, mudancas: dict) -> EstadoAds:
        """O estado depois do PUT da Geral (campo ausente não muda). Ligar uma
        caixa desliga a outra; desligar não mexe na outra."""
        novo = self
        if mudancas.get("ads_geral_ligada") is not None:
            ligada = mudancas["ads_geral_ligada"]
            novo = replace(
                novo,
                geral_ligada=ligada,
                por_produto_ligada=False if ligada else novo.por_produto_ligada,
            )
        if "pode_gastar_pct" in mudancas:
            novo = replace(novo, geral_pct=mudancas["pode_gastar_pct"])
        if mudancas.get("ads_por_produto_ligada") is not None:
            ligada = mudancas["ads_por_produto_ligada"]
            novo = replace(
                novo,
                por_produto_ligada=ligada,
                geral_ligada=False if ligada else novo.geral_ligada,
            )
        return novo

    def com_produto(self, produto_id: UUID, pct: Decimal | None) -> EstadoAds:
        produtos = dict(self.produtos)
        produtos[produto_id] = pct
        return replace(self, produtos=produtos)


def produtos_do_departamento(stmt: Select, root_id: UUID | ColumnElement) -> Select:
    """Restringe `stmt` (um SELECT sobre `pricing_products`) aos produtos do
    departamento `root_id` — os que a janela de Ads lista (GET /api/pricing/ads)
    e os únicos em que o PUT do Ads de produto grava. Um lugar só, para a soma
    do Custo total não divergir do que a janela mostra."""
    return stmt.join(Segment, Segment.id == PricingProduct.segment_id).where(
        Segment.parent_id == root_id
    )


async def estado_ads(session: AsyncSession, account_id: UUID) -> EstadoAds:
    """O Ads gravado da conta (lido do banco, sem cache do ORM: o PUT da Geral
    grava por INSERT … ON CONFLICT, que a sessão não enxerga). Produto fora do
    departamento da conta não conta (ver o topo)."""
    raiz_da_conta = (
        select(PricingAccount.segment_id).where(PricingAccount.id == account_id).scalar_subquery()
    )
    do_departamento = produtos_do_departamento(select(PricingProduct.id), raiz_da_conta)
    geral = (
        await session.execute(
            select(PricingAdsConta)
            .where(PricingAdsConta.pricing_account_id == account_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    produtos = {
        p.pricing_product_id: p.pode_gastar_pct
        for p in (
            await session.execute(
                select(PricingAdsProduto)
                .where(
                    PricingAdsProduto.pricing_account_id == account_id,
                    PricingAdsProduto.pricing_product_id.in_(do_departamento),
                )
                .execution_options(populate_existing=True)
            )
        ).scalars()
    }
    geral_ligada, por_produto_ligada = caixas_ads(geral)
    return EstadoAds(
        geral_ligada=geral_ligada,
        geral_pct=geral.pode_gastar_pct if geral is not None else None,
        por_produto_ligada=por_produto_ligada,
        produtos=produtos,
    )


def pct_br(valor: Decimal) -> str:
    """4.50 → "4,5"; 10.00 → "10"; 0 → "0" (pt-BR, sem zeros à direita)."""
    texto = format(valor, "f")
    if "." in texto:
        texto = texto.rstrip("0").rstrip(".")
    return texto.replace(".", ",") or "0"


def soma_do_custo_total(
    afiliado: Decimal | None, desconto: Decimal | None, ads: Decimal
) -> Decimal:
    """Desconto + o MAIOR entre Afiliado e Ads (M-94; vazio conta como zero)."""
    return (desconto if desconto is not None else ZERO) + max(
        afiliado if afiliado is not None else ZERO, ads
    )


def nao_piora(
    soma_gravada: Decimal,
    teto_gravado: Decimal | None,
    soma_nova: Decimal,
    teto_novo: Decimal | None,
) -> bool:
    """Revisão da M-86 (ver o topo): True se a gravação não deixa a conta pior
    do que já está — a soma nova não passa da gravada e o Custo total não baixa
    (vazio = sem teto, o mais alto). Só é usado com a soma nova acima do teto
    novo: aí True quer dizer que a conta JÁ estava acima e a mudança corrige
    (ou não mexe), então grava."""
    if soma_nova > soma_gravada:
        return False
    if teto_novo is None:
        return True
    return teto_gravado is not None and teto_novo >= teto_gravado


def estouro(
    afiliado: Decimal | None,
    desconto: Decimal | None,
    ads: Decimal,
    custo_total: Decimal | None,
) -> str | None:
    """A mensagem do aviso se Desconto + o maior entre Afiliado e Ads passa do
    Custo total; None se cabe (ou se a conta não tem teto). Igual ao teto é
    aceito. A mensagem mostra só a dupla que estourou; se as duas estouram, a
    maior (empate: a do Afiliado)."""
    if custo_total is None:
        return None
    afiliado = afiliado if afiliado is not None else ZERO
    desconto = desconto if desconto is not None else ZERO
    soma = soma_do_custo_total(afiliado, desconto, ads)
    if soma <= custo_total:
        return None
    if ads > afiliado:
        dupla = f"Desconto {pct_br(desconto)}% + Ads {pct_br(ads)}%"
    else:
        dupla = f"Afiliado {pct_br(afiliado)}% + Desconto {pct_br(desconto)}%"
    return (
        f"{dupla} = {pct_br(soma)}%."
        f" Passa do Custo total ({pct_br(custo_total)}%). Não gravado."
    )


# --------------------------------------------------------------- M-86


def calcular_custo_total(
    margens: Iterable[Decimal | None],
    margem_minima_pct: Decimal,
    comissoes: Iterable[Decimal | None],
) -> Decimal | None:
    """O Custo total (%) pela Margem mínima (ver o topo): inteiro, para baixo.

    `margens` e `comissoes` em fração (0.356 = 35,6%, como `marginN` e
    `commission`); `margem_minima_pct` em % (25 = 25%). None = não calcula
    (nenhuma margem ou nenhuma comissão preenchida).

    Tudo em Decimal e a divisão inteira `//` do Decimal, que é exata: um valor
    que dá 6 certinho fica 6, e 5,9999999 fica 5 (em float, (0,30 − 0,20) ×
    (1 − 0,22) ÷ 1,30 × 100 dá 5,999…98, e o "para baixo" virava 5 em vez de 6)."""
    preenchidas = [m for m in margens if m is not None]
    com_valor = [k for k in comissoes if k is not None]
    if not preenchidas or not com_valor:
        return None
    m = min(preenchidas)
    r = margem_minima_pct / CEM
    k = max(com_valor)
    numerador = (m - r) * (UM - k) * CEM
    # m ≤ r (ou comissão de 100% ou mais): sem folga, 0. Assim 1 + m > 1 > 0.
    if numerador <= 0:
        return ZERO
    # (1 − k) ≤ 1 e (m − r) < (1 + m): nunca passa de 100 (o CHECK do banco).
    return min(numerador // (UM + m), CEM)


def valores_finais(conta: PricingAccount, novos: Mapping[str, object] | None = None):
    """Os campos do cálculo como FICARIAM gravados na conta (`novos` vence)."""
    novos = novos or {}
    return SimpleNamespace(
        **{
            campo: novos[campo] if campo in novos else getattr(conta, campo)
            for campo in CAMPOS_DA_MARGEM_MINIMA
        }
    )


async def categorias_ativas(session: AsyncSession, raiz_id: UUID) -> int:
    """Quantas colunas "marg" a aba Contas mostra para o centro de custo: as
    categorias de venda ATIVAS dele (pages/pricing/[tab].vue, TYPE_HEADERS; a
    N-ésima ativa fica sobre margin{N}), no máximo 10 (M-93). Margem gravada numa
    posição sem categoria ativa (ex.: a da "Queima de estoque", que saiu da
    Mala) não aparece na tela e não entra no cálculo."""
    n = (
        await session.execute(
            select(func.count())
            .select_from(Segment)
            .where(Segment.parent_id == raiz_id, Segment.active.is_(True))
        )
    ).scalar_one()
    return min(n, len(_MARGENS))


async def custo_total_pela_margem_minima(
    session: AsyncSession, linhas: Sequence[object]
) -> Decimal | None:
    """O Custo total calculado de UMA conta: `linhas` = a linha (e, no par do
    ML, a irmã), com os valores finais (a conta em si ou `valores_finais`). A
    primeira tem a Margem mínima preenchida (quem chama confere). A margem é a
    menor das linhas e a comissão a maior (o caso mais apertado)."""
    primeira = linhas[0]
    n = await categorias_ativas(session, primeira.segment_id)
    margens = [getattr(linha, campo) for linha in linhas for campo in _MARGENS[:n]]
    return calcular_custo_total(
        margens, primeira.margem_minima_pct, [linha.commission for linha in linhas]
    )


async def recalcular(session: AsyncSession, linhas: Sequence[PricingAccount]) -> bool:
    """Recalcula e grava (sem commit) o Custo total de UMA conta (a linha e,
    no par, a irmã) se ela tem a Margem mínima preenchida. False = vazia: nada
    muda (o Custo total fica como está e é digitado)."""
    com_margem = [linha for linha in linhas if linha.margem_minima_pct is not None]
    if not com_margem:
        return False
    ordem = [com_margem[0], *(linha for linha in linhas if linha is not com_margem[0])]
    valor = await custo_total_pela_margem_minima(session, ordem)
    for linha in linhas:
        linha.custo_total_pct = valor
    return True


async def recalcular_contas_da_raiz(session: AsyncSession, raiz_id: UUID) -> int:
    """As categorias de venda do centro de custo mudaram (criada, excluída,
    ativada ou desativada): as colunas "marg" da aba Contas mudam, e o Custo
    total das contas do centro com Margem mínima é recalculado (sem commit).
    Devolve quantas contas (o par conta uma vez)."""
    contas = (
        await session.execute(
            select(PricingAccount).where(
                PricingAccount.segment_id == raiz_id, PricingAccount.canal == CANAL_KIT
            )
        )
    ).scalars().all()
    grupos: dict[object, list[PricingAccount]] = {}
    for conta in contas:
        grupos.setdefault(conta.par_id or conta.id, []).append(conta)
    feitas = 0
    for linhas in grupos.values():
        if await recalcular(session, linhas):
            feitas += 1
    return feitas
