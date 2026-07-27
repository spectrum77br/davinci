"""Resolver do catálogo de mala (nf_catalogo) — casa o SKU do pedido com o valor
CHEIO por (sku_base, tamanho), aceitando faixas e preferindo o tamanho exato."""

from __future__ import annotations

from decimal import Decimal

from app.models import NfCatalogoMala
from app.services import nf_catalogo


def _lin(sku_base, tamanho, valor):
    return NfCatalogoMala(
        modelo="x", sku_base=sku_base, tamanho=tamanho, valor=Decimal(str(valor))
    )


def test_parse_sku_mala():
    assert nf_catalogo.parse_sku_mala("b001.20") == ("b001", 20)
    assert nf_catalogo.parse_sku_mala("B001.24") == ("b001", 24)
    # Kit / kit-6 / avulso / vazio não resolvem.
    assert nf_catalogo.parse_sku_mala("b001.12.18") is None
    assert nf_catalogo.parse_sku_mala("b001") is None
    assert nf_catalogo.parse_sku_mala("b001.18us") is None
    assert nf_catalogo.parse_sku_mala(None) is None


def test_valor_para_exato_e_sem_match():
    linhas = [_lin("b001", "20", "161.00"), _lin("b001", "24", "176.40")]
    assert nf_catalogo.valor_para(linhas, "b001.20") == Decimal("161.00")
    assert nf_catalogo.valor_para(linhas, "b001.24") == Decimal("176.40")
    # Tamanho não catalogado / base diferente → None (cai no valor de venda).
    assert nf_catalogo.valor_para(linhas, "b001.18") is None
    assert nf_catalogo.valor_para(linhas, "b999.20") is None


def test_valor_para_faixa():
    # Faixa "08.10" cobre 8 e 10.
    linhas = [_lin("b002", "08.10", "26.46")]
    assert nf_catalogo.valor_para(linhas, "b002.8") == Decimal("26.46")
    assert nf_catalogo.valor_para(linhas, "b002.10") == Decimal("26.46")
    assert nf_catalogo.valor_para(linhas, "b002.12") is None


def test_valor_para_exato_vence_faixa():
    # Mesma base: linha exata (13) deve vencer a faixa (13.14).
    linhas = [_lin("b003", "13.14", "57.40"), _lin("b003", "13", "60.00")]
    assert nf_catalogo.valor_para(linhas, "b003.13") == Decimal("60.00")
    # Só a faixa cobre o 14.
    assert nf_catalogo.valor_para(linhas, "b003.14") == Decimal("57.40")
