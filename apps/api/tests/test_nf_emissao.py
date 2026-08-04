"""Motor de emissão da NF (Fase 3a) — núcleo puro da transformação.

Cada teste é uma das regras de faturador da spec (áudios 25/07): avulso (NF
cheia com SKU/nome do principal), avulso celular (a001/embalagem), exclusivo
(0,1%), upseller 2%/1%/70%/100% (percentual, com ou sem troca de SKU/nome/NCM).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.services import nf_emissao
from app.services.nf_emissao import ItemPedido


@dataclass
class _Regra:
    nf_cheia: bool = False
    percentual: Decimal | None = None
    sku_fonte: str | None = None
    nome_fonte: str | None = None
    ncm: str | None = None


def _item(sku="dg053.ci", nome="Capa Celular", qtd=1, unit="1000", ncm=None) -> ItemPedido:
    return ItemPedido(
        sku=sku, nome=nome, quantidade=qtd, valor_unitario=Decimal(unit), ncm=ncm
    )


def test_avulso_nf_cheia_usa_sku_nome_principal():
    # bling avulso: SKU + nome do produto do principal, NCM da regra, valor integral.
    r = _Regra(nf_cheia=True, sku_fonte="principal", nome_fonte="produto", ncm="4202.12.10")
    linha = nf_emissao.transformar_item(r, _item(qtd=2, unit="500"))
    assert linha.sku == "dg053.ci"
    assert linha.nome == "Capa Celular"
    assert linha.ncm == "4202.12.10"
    assert linha.quantidade == 2
    assert linha.valor_total == Decimal("1000.00")  # cheia = 500 × 2
    assert linha.valor_unitario == Decimal("500.00")


def test_avulso_celular_troca_para_a001_embalagem():
    # bling avulso celular: SKU a001, nome "embalagem", resto igual ao avulso.
    r = _Regra(nf_cheia=True, sku_fonte="a001", nome_fonte="embalagem", ncm="4202.12.10")
    linha = nf_emissao.transformar_item(r, _item(unit="1000"))
    assert linha.sku == "a001"
    assert linha.nome == "embalagem"
    assert linha.ncm == "4202.12.10"
    assert linha.valor_total == Decimal("1000.00")


def test_exclusivo_percentual_0_1():
    # bling exclusivo: a001/embalagem, 0,1% do valor (R$1000 → R$1).
    r = _Regra(nf_cheia=False, percentual=Decimal("0.1"), sku_fonte="a001", nome_fonte="embalagem")
    linha = nf_emissao.transformar_item(r, _item(unit="1000"))
    assert linha.sku == "a001"
    assert linha.nome == "embalagem"
    assert linha.valor_total == Decimal("1.00")


def test_sku_fonte_literal_vira_o_sku_da_nf():
    # O Upseller casa o produto pelo SKU e lá só existem os de embalagem/mala;
    # qualquer sku_fonte que não seja 'principal'/vazio é o SKU literal da NF.
    r = _Regra(nf_cheia=False, percentual=Decimal("2"), sku_fonte="e3")
    linha = nf_emissao.transformar_item(r, _item(sku="dg057.ci+a001.ci", unit="1000"))
    assert linha.sku == "e3"


def test_upseller_2pct_mantem_sku_nome_item():
    # upseller 2%: sem troca de SKU/nome/NCM na regra → mantém os do item.
    r = _Regra(nf_cheia=False, percentual=Decimal("2"))
    linha = nf_emissao.transformar_item(r, _item(sku="x1", nome="Produto X", unit="1000", ncm="9999.99.99"))
    assert linha.sku == "x1"
    assert linha.nome == "Produto X"
    assert linha.ncm == "9999.99.99"  # NCM vazio na regra → mantém o do item
    assert linha.valor_total == Decimal("20.00")


def test_upseller_1pct():
    r = _Regra(nf_cheia=False, percentual=Decimal("1"))
    linha = nf_emissao.transformar_item(r, _item(unit="1000"))
    assert linha.valor_total == Decimal("10.00")


def test_upseller_mala_70pct_sku_nome_principal_ncm_3923():
    # upseller 70%: SKU+nome do principal, NCM 3923.21.10 (mala).
    r = _Regra(nf_cheia=False, percentual=Decimal("70"), sku_fonte="principal",
               nome_fonte="produto", ncm="3923.21.10")
    linha = nf_emissao.transformar_item(r, _item(sku="b011.20", nome="Mala ABS 20", unit="1000"))
    assert linha.sku == "b011.20"
    assert linha.nome == "Mala ABS 20"
    assert linha.ncm == "3923.21.10"
    assert linha.valor_total == Decimal("700.00")


def test_percentual_redistribui_no_unitario_sem_erro_de_arredondamento():
    # 0,1% de (3 × 333,33) = 0,1% de 999,99 = 1,00 (arredondado); unitário derivado.
    r = _Regra(nf_cheia=False, percentual=Decimal("0.1"))
    linha = nf_emissao.transformar_item(r, _item(qtd=3, unit="333.33"))
    assert linha.valor_total == Decimal("1.00")
    assert linha.quantidade == 3
    # unitário = total / qtd arredondado a centavo
    assert linha.valor_unitario == Decimal("0.33")


def test_transformar_pedido_varias_linhas():
    r = _Regra(nf_cheia=True, sku_fonte="principal", nome_fonte="produto", ncm="4202.12.10")
    itens = [_item(sku="a", unit="100"), _item(sku="b", unit="200", qtd=2)]
    linhas = nf_emissao.transformar_pedido(r, itens)
    assert [l.sku for l in linhas] == ["a", "b"]
    assert [l.valor_total for l in linhas] == [Decimal("100.00"), Decimal("400.00")]
