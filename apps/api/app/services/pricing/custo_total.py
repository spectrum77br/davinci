"""Custo total da conta (M-56, 25/11/2026, migration 0511).

Pedido do dono: na aba Contas da Tabela de Preços, Afiliado % + Desconto % +
Ads % da conta não pode passar do Custo total (%) da conta. Vazio conta como
zero; Custo total vazio = sem teto. É só controle e anotação: nada disso entra
no cálculo do preço.

O Ads da soma é o MAIOR % que pode valer para algum produto da conta:
- o % Geral, se a caixa "Geral" está ligada e o % não é nulo;
- cada % próprio de produto, se a caixa "Por produto" está ligada e o % não é
  nulo.
Sem nenhum, 0. (Sem linha em `pricing_ads_conta` = as duas caixas ligadas e
sem % Geral, como no resto do Ads.)

A trava fica no servidor, em toda gravação que mexe na soma (PATCH da conta,
PUT do Ads da conta e PUT do Ads de produto), sempre com o estado que FICARIA
gravado; se estoura, nada é gravado (422 `custo_total_estourado`).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PricingAdsConta, PricingAdsProduto

ZERO = Decimal("0")


@dataclass(frozen=True)
class EstadoAds:
    """O Ads de uma conta, como está gravado (ou como ficaria)."""

    geral_ligada: bool = True
    geral_pct: Decimal | None = None
    por_produto_ligada: bool = True
    # {pricing_product_id: % próprio (None = sem %)}
    produtos: dict[UUID, Decimal | None] = field(default_factory=dict)

    def maximo(self) -> Decimal:
        candidatos: list[Decimal] = []
        if self.geral_ligada and self.geral_pct is not None:
            candidatos.append(self.geral_pct)
        if self.por_produto_ligada:
            candidatos.extend(p for p in self.produtos.values() if p is not None)
        return max(candidatos, default=ZERO)

    def com_geral(self, mudancas: dict) -> EstadoAds:
        """O estado depois do PUT da Geral (campo ausente não muda)."""
        novo = self
        if mudancas.get("ads_geral_ligada") is not None:
            novo = replace(novo, geral_ligada=mudancas["ads_geral_ligada"])
        if "pode_gastar_pct" in mudancas:
            novo = replace(novo, geral_pct=mudancas["pode_gastar_pct"])
        if mudancas.get("ads_por_produto_ligada") is not None:
            novo = replace(novo, por_produto_ligada=mudancas["ads_por_produto_ligada"])
        return novo

    def com_produto(self, produto_id: UUID, pct: Decimal | None) -> EstadoAds:
        produtos = dict(self.produtos)
        produtos[produto_id] = pct
        return replace(self, produtos=produtos)


async def estado_ads(session: AsyncSession, account_id: UUID) -> EstadoAds:
    """O Ads gravado da conta (lido do banco, sem cache do ORM: o PUT da Geral
    grava por INSERT … ON CONFLICT, que a sessão não enxerga)."""
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
                .where(PricingAdsProduto.pricing_account_id == account_id)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    }
    if geral is None:
        return EstadoAds(produtos=produtos)
    return EstadoAds(
        geral_ligada=geral.ads_geral_ligada,
        geral_pct=geral.pode_gastar_pct,
        por_produto_ligada=geral.ads_por_produto_ligada,
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
