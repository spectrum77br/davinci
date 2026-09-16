"""Qual SKU vinculado ao anúncio pertence a qual célula da Tabela de Preços.

Eduardo (16/09/2026): clicou em Enviar na coluna "Amazon kit 1" da mala, conta
kfa, e levou "no product_links" — com a integração saudável e 4.144 anúncios
vinculados, incluindo o SKU exato da célula.

A causa: a regra comparava só o LADO ESQUERDO do SKU do anúncio
("b109.20") contra o conjunto de SKUs da célula, que na mala guarda o kit
inteiro ("b109.20+a075+bp003+a076"). Nunca casava. Toda coluna de kit da mala
estava quebrada, em todas as contas — não só na Amazon.

A regra vivia embutida no laço do push, sem teste nenhum. Foi por isso que
passou despercebida.
"""

from __future__ import annotations

from app.services.pricing.push import sku_casa_no_departamento as casa

# A célula real do print do Eduardo.
CELULA_KIT = {
    "b109.20+a075+bp003+a076",
    "b110.20+a075+bp003+a076",
    "b111.20+a075+bp003+a076",
}
BASES_KIT = {s.split(".", 1)[0] for s in CELULA_KIT}


def test_mala_celula_de_kit_casa_com_o_anuncio_do_mesmo_kit():
    """O caso que estava quebrado."""
    assert casa(
        "b109.20+a075+bp003+a076", dept="mala",
        sku_full_set=CELULA_KIT, sku_base_set=BASES_KIT,
    )


def test_mala_celula_de_kit_nao_casa_com_kit_diferente_do_mesmo_produto():
    """A trava que impede empurrar preço pro anúncio errado: dois kits do
    mesmo produto têm o mesmo lado esquerdo."""
    assert not casa(
        "b109.20+a999", dept="mala",
        sku_full_set=CELULA_KIT, sku_base_set=BASES_KIT,
    )


def test_mala_celula_simples_continua_casando_com_o_kit_do_anuncio():
    """Comportamento que já existia e não podia quebrar."""
    celula = {"b109.20"}
    assert casa(
        "b109.20+a075+bp003+a076", dept="mala",
        sku_full_set=celula, sku_base_set={"b109"},
    )
    assert casa("b109.20", dept="mala", sku_full_set=celula, sku_base_set={"b109"})


def test_mala_nao_casa_produto_de_outra_linha():
    assert not casa(
        "b777.20+a075", dept="mala",
        sku_full_set=CELULA_KIT, sku_base_set=BASES_KIT,
    )


def test_catalogo_so_aceita_sku_simples_e_exato():
    celula = {"a075"}
    assert casa("a075", dept="catalogo", sku_full_set=celula, sku_base_set={"a075"})
    # kit nunca entra no catálogo
    assert not casa("a075+a076", dept="catalogo", sku_full_set=celula, sku_base_set={"a075"})
    assert not casa("a076", dept="catalogo", sku_full_set=celula, sku_base_set={"a075"})


def test_celular_casa_pela_base_antes_do_ponto():
    celula_base = {"dg053"}
    assert casa("dg053.ci", dept="celular", sku_full_set={"dg053.ci"}, sku_base_set=celula_base)
    assert casa("dg053.sp+a001", dept="celular", sku_full_set=set(), sku_base_set=celula_base)
    assert not casa("dg099.ci", dept="celular", sku_full_set=set(), sku_base_set=celula_base)


def test_sku_vazio_nunca_casa():
    for dept in ("mala", "celular", "catalogo"):
        assert not casa("", dept=dept, sku_full_set=CELULA_KIT, sku_base_set=BASES_KIT)
