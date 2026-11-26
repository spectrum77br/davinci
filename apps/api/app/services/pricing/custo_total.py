"""Custo total da conta (M-56, 25/11/2026, migration 0511).

Pedido do dono: na aba Contas da Tabela de Preços, Afiliado % + Desconto % +
Ads % da conta não pode passar do Custo total (%) da conta. Vazio conta como
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
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models import PricingAccount, PricingAdsConta, PricingAdsProduto, PricingProduct, Segment

ZERO = Decimal("0")


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


def estouro(
    afiliado: Decimal | None,
    desconto: Decimal | None,
    ads: Decimal,
    custo_total: Decimal | None,
) -> str | None:
    """A mensagem do aviso se a soma passa do Custo total; None se cabe (ou se
    a conta não tem teto). Igual ao teto é aceito."""
    if custo_total is None:
        return None
    afiliado = afiliado if afiliado is not None else ZERO
    desconto = desconto if desconto is not None else ZERO
    soma = afiliado + desconto + ads
    if soma <= custo_total:
        return None
    return (
        f"Afiliado {pct_br(afiliado)}% + Desconto {pct_br(desconto)}% + Ads {pct_br(ads)}%"
        f" = {pct_br(soma)}%. Passa do Custo total ({pct_br(custo_total)}%). Não gravado."
    )
