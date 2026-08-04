"""Motor de EMISSÃO da NF (Fase 3a) — núcleo puro da transformação.

Dado um pedido (os itens que já estão no davinci, vindos da `bling_orders` do
Bling PRINCIPAL) + a regra do FATURADOR da loja (`nf_faturador`), produz as
LINHAS que vão na planilha de importação avulsa do destino (outro Bling /
Upseller). É a "planilha do Bling principal" transformada — a NF sai como venda
AVULSA no destino, desacoplada do intermediador do marketplace.

Regras da spec (aba NF, áudios 25/07):
- `sku_fonte`: vazio ou 'principal' usa o SKU do produto do principal; qualquer
  outro valor é o SKU LITERAL que vai na NF ('a001' no bling avulso celular,
  'e3'/'m200' nos upseller — o Upseller casa o produto pelo SKU, e lá só existem
  os SKUs de embalagem/mala).
- `nome_fonte`: 'embalagem' usa o nome fixo "embalagem"; senão o nome do produto.
- `ncm`: o NCM da regra sobrepõe o do item (4202.12.10 na maioria; 3923.21.10
  nos upseller de mala). Vazio na regra = mantém o NCM do item.
- valor da linha:
  - `nf_cheia=True`  → valor integral do item (avulso).
  - `nf_cheia=False` → `percentual` % do valor do item (exclusivo 0,1%,
    upseller 2%/1%/70%/100%).

Só transforma; NÃO gera Excel nem loga em site — essas camadas ficam por cima.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

_CENT = Decimal("0.01")
_SKU_FONTE_PRINCIPAL = "principal"
_NOME_FIXO_EMBALAGEM = "embalagem"


class _RegraFaturador(Protocol):
    """O que o motor lê de um `NfFaturador` (duck-typing: aceita o model ORM ou
    qualquer objeto com esses atributos — facilita testar sem DB)."""

    nf_cheia: bool
    percentual: Decimal | None
    sku_fonte: str | None
    nome_fonte: str | None
    ncm: str | None


@dataclass(frozen=True)
class ItemPedido:
    """Um item do pedido do Bling principal (uma linha da `bling_orders`)."""

    sku: str | None
    nome: str | None
    quantidade: int
    valor_unitario: Decimal
    ncm: str | None = None


@dataclass(frozen=True)
class NfLinha:
    """Uma linha já transformada, pronta pra planilha de importação avulsa."""

    sku: str
    nome: str
    ncm: str | None
    quantidade: int
    valor_unitario: Decimal
    valor_total: Decimal


def _dec(v: object) -> Decimal:
    if isinstance(v, Decimal):
        return v
    if v is None:
        return Decimal(0)
    return Decimal(str(v))


def _q(v: Decimal) -> Decimal:
    """Arredonda a 2 casas (centavos), meio-pra-cima."""
    return v.quantize(_CENT, rounding=ROUND_HALF_UP)


def transformar_item(regra: _RegraFaturador, item: ItemPedido) -> NfLinha:
    """Aplica a regra do faturador a UM item → linha da NF.

    O valor é calculado sobre o total da linha (unitário × quantidade) e depois
    redistribuído no unitário, pra o percentual bater com o valor do pedido
    (ex.: exclusivo 0,1% de R$1000 = R$1) sem erro de arredondamento por item.
    """
    fonte_sku = (regra.sku_fonte or "").strip()
    sku = (
        (item.sku or "").strip()
        if not fonte_sku or fonte_sku.lower() == _SKU_FONTE_PRINCIPAL
        else fonte_sku
    )
    nome = (
        _NOME_FIXO_EMBALAGEM
        if (regra.nome_fonte or "").strip().lower() == _NOME_FIXO_EMBALAGEM
        else (item.nome or "").strip()
    )
    ncm = (regra.ncm or "").strip() or (item.ncm or "").strip() or None

    qtd = int(item.quantidade or 0)
    base_total = _dec(item.valor_unitario) * Decimal(qtd)
    if regra.nf_cheia:
        total = base_total
    else:
        pct = _dec(regra.percentual)
        total = base_total * pct / Decimal(100)
    total = _q(total)
    unit = _q(total / Decimal(qtd)) if qtd else Decimal("0.00")
    return NfLinha(
        sku=sku,
        nome=nome,
        ncm=ncm,
        quantidade=qtd,
        valor_unitario=unit,
        valor_total=total,
    )


def transformar_pedido(regra: _RegraFaturador, itens: list[ItemPedido]) -> list[NfLinha]:
    """Transforma todos os itens de um pedido pela regra do faturador da loja."""
    return [transformar_item(regra, it) for it in itens]
