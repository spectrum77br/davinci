"""Resolver do catálogo de mala (nf_catalogo) — casa o SKU do pedido com o valor
CHEIO por (modelo, tamanho), aceitando faixas e preferindo o tamanho exato. O
modelo (família) vem do nome do produto (M1..M6 → abs, P1..P6 → pp, ME1/ME2)."""

from __future__ import annotations

from decimal import Decimal

from app.models import NfCatalogoMala
from app.services import nf_catalogo


def _lin(modelo, tamanho, valor):
    return NfCatalogoMala(modelo=modelo, tamanho=tamanho, valor=Decimal(str(valor)))


def test_parse_sku_mala():
    assert nf_catalogo.parse_sku_mala("b001.20") == ("b001", 20)
    assert nf_catalogo.parse_sku_mala("B001.24") == ("b001", 24)
    # Kit / kit-6 / avulso / vazio não resolvem.
    assert nf_catalogo.parse_sku_mala("b001.12.18") is None
    assert nf_catalogo.parse_sku_mala("b001") is None
    assert nf_catalogo.parse_sku_mala("b001.18us") is None
    assert nf_catalogo.parse_sku_mala(None) is None


def test_modelo_do_nome():
    # Famílias M1..M6 → abs.
    assert nf_catalogo.modelo_do_nome("Mala Lisa M2 tamanho 20 - Roxa") == "abs"
    assert nf_catalogo.modelo_do_nome("Mala Listrada M1 tamanho 12 - Preto") == "abs"
    assert nf_catalogo.modelo_do_nome("Mala Sorriso M6 tamanho 12") == "abs"
    # P1..P6 → pp.
    assert nf_catalogo.modelo_do_nome("Mala Brilho Listrada P1 tamanho 14") == "pp"
    assert nf_catalogo.modelo_do_nome("Mala Minecraft P6 tamanho 14") == "pp"
    # ME1 / ME2 — não podem colar em "M".
    assert nf_catalogo.modelo_do_nome("Mala Executivo ME1 tamanho 20") == "me1"
    assert nf_catalogo.modelo_do_nome("Mala Executivo ME2 tamanho 20") == "me2"
    # P7/P8 e nomes sem família → None.
    assert nf_catalogo.modelo_do_nome("Mala Onda P7 tamanho 16") is None
    assert nf_catalogo.modelo_do_nome("Chaveiro chariots") is None
    assert nf_catalogo.modelo_do_nome(None) is None


def test_valor_para_exato_e_sem_match():
    linhas = [_lin("abs", "20", "161.00"), _lin("abs", "24", "176.40")]
    assert nf_catalogo.valor_para(linhas, "b001.20", "abs") == Decimal("161.00")
    assert nf_catalogo.valor_para(linhas, "b001.24", "abs") == Decimal("176.40")
    # Tamanho não catalogado / modelo diferente / sem modelo → None (venda).
    assert nf_catalogo.valor_para(linhas, "b001.18", "abs") is None
    assert nf_catalogo.valor_para(linhas, "b001.20", "pp") is None
    assert nf_catalogo.valor_para(linhas, "b001.20", None) is None


def test_valor_para_faixa():
    # Faixa "08.10" cobre 8 e 10.
    linhas = [_lin("abs", "08.10", "26.46")]
    assert nf_catalogo.valor_para(linhas, "b001.8", "abs") == Decimal("26.46")
    assert nf_catalogo.valor_para(linhas, "b001.10", "abs") == Decimal("26.46")
    assert nf_catalogo.valor_para(linhas, "b001.12", "abs") is None


def test_valor_para_exato_vence_faixa():
    # Mesmo modelo: linha exata (13) deve vencer a faixa (13.14).
    linhas = [_lin("abs", "13.14", "57.40"), _lin("abs", "13", "60.00")]
    assert nf_catalogo.valor_para(linhas, "b001.13", "abs") == Decimal("60.00")
    # Só a faixa cobre o 14.
    assert nf_catalogo.valor_para(linhas, "b001.14", "abs") == Decimal("57.40")
