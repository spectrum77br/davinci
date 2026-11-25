"""Eletro: o preço de uma linha não pode ir para a variação de outra linha.

Eduardo (25/11/2026): "quando jogo o preço do pote de vidro 2,5 L ele joga para
todos os outros da variação". Na Shopee e no TikTok de cada conta, o anúncio do
pote tem 3 variações (uaf001m1.2l / .4l / .6l), cada uma com o seu SKU; e a
linha 'airfryer vidro' (uaf001m1.110 / .220) tem o mesmo código base. A regra
de celular/eletro casava só pelo código base (uaf001m1), então as 4 linhas
mandavam preço umas para as variações das outras.

Regra nova (`sku_match.casa_pelo_codigo`): o pedaço mais específico do SKU do
anúncio que alguma linha do departamento tem por inteiro decide o dono; sem
nenhum, vale o código base — a não ser que outra linha divida esse código base.

Conferido nos dados reais (25/11): das 6.804 células linha × conta, só 252
mudaram, todas dessas 4 linhas, só tirando anúncios de outra linha; celular
(4.794) e mala (1.092) sem nenhuma diferença.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from app.services.pricing.anuncios import (
    CANAL_CATALOGO,
    CANAL_KIT,
    LinhasDoDepartamento,
    resolver_anuncios,
)
from app.services.pricing.sku_match import (
    casa_pelo_codigo,
    prefixos_de_ponto,
    sku_casa_no_departamento,
)

# As 4 linhas reais do Eletro que dividem o código base, e uma vizinha.
LINHAS = {
    "vidro": "uaf001m1.110, uaf001m1.220",
    "pote25": "uaf001m1.2l",
    "pote4": "uaf001m1.4l",
    "pote6": "uaf001m1.6l",
    "barbecue": "uaf002m1.110, uaf002m1.220",
}
LD = LinhasDoDepartamento.de((k, "eletro", sku) for k, sku in LINHAS.items())


def _link(sku: str, *, item: str, variacao: str, catalogo: bool | None = None,
          listing_type: str | None = None):
    pid = uuid4()
    lk = SimpleNamespace(
        id=uuid4(), product_id=pid, external_id=item, variation_id=variacao,
        catalog_listing=catalogo, listing_type=listing_type, morto_desde=None,
        anuncio_status="active", catalogo_relacionado=None,
    )
    return lk, pid, sku


def _resolver(links_skus, linha: str, *, plataforma: str = "shopee",
              canal: str = CANAL_KIT, listing_type_conta: str | None = None,
              outras: bool = True):
    links = [lk for lk, _pid, _sku in links_skus]
    skus = {pid: sku for _lk, pid, sku in links_skus}
    res = resolver_anuncios(
        links, skus,
        pricing_sku=LINHAS[linha], dept="eletro", plataforma=plataforma, canal=canal,
        listing_type_conta=listing_type_conta, pula_catalogo=False,
        skus_outras_linhas=LD.outras("eletro", linha) if outras else (),
    )
    por_id = {lk.id: sku for lk, _pid, sku in links_skus}
    return sorted(por_id[lk.id] for lk in res.links)


# O anúncio Shopee real da Barbosa (22995184839) e o da airfryer vidro.
SHOPEE = [
    _link("uaf001m1.2l", item="22995184839", variacao="129658702196"),
    _link("uaf001m1.4l", item="22995184839", variacao="129658702197"),
    _link("uaf001m1.6l", item="22995184839", variacao="129658702198"),
    _link("uaf001m1.110", item="58212957869", variacao="179728796876"),
    _link("uaf001m1.220", item="58212957869", variacao="179728796877"),
]


def test_o_defeito_regra_antiga_mandava_o_pote_para_todas_as_variacoes():
    """Sem as outras linhas (a regra de antes), o pote 2,5L pega os 5."""
    assert _resolver(SHOPEE, "pote25", outras=False) == [
        "uaf001m1.110", "uaf001m1.220", "uaf001m1.2l", "uaf001m1.4l", "uaf001m1.6l",
    ]


def test_cada_pote_so_manda_para_a_propria_variacao():
    assert _resolver(SHOPEE, "pote25") == ["uaf001m1.2l"]
    assert _resolver(SHOPEE, "pote4") == ["uaf001m1.4l"]
    assert _resolver(SHOPEE, "pote6") == ["uaf001m1.6l"]


def test_airfryer_vidro_fica_com_110_e_220_e_perde_os_potes():
    assert _resolver(SHOPEE, "vidro") == ["uaf001m1.110", "uaf001m1.220"]


def test_tiktok_e_ml_seguem_a_mesma_regra():
    tiktok = [
        _link("uaf001m1.2l", item="1737402357448", variacao="1737402357448475910"),
        _link("uaf001m1.4l", item="1737402357448", variacao="1737402357448541446"),
    ]
    assert _resolver(tiktok, "pote4", plataforma="tiktok") == ["uaf001m1.4l"]
    ml = [
        _link("uaf001m1.220", item="MLB1", variacao=None),
        _link("uaf001m1.6l", item="MLB2", variacao=None),
    ]
    assert _resolver(ml, "pote6", plataforma="ml") == ["uaf001m1.6l"]
    # Zorvex: a conta só tem o anúncio da 220V — o pote fica sem anúncio
    # (antes recebia o preço do pote).
    assert _resolver(ml[:1], "pote25", plataforma="ml") == []


def test_kit_com_acessorio_segue_o_lado_esquerdo():
    kits = [
        _link("uaf001m1.2l+a001", item="X", variacao="1"),
        _link("uaf001m1.110+a001", item="Y", variacao="2"),
    ]
    assert _resolver(kits, "pote25") == ["uaf001m1.2l+a001"]
    assert _resolver(kits, "vidro") == ["uaf001m1.110+a001"]


def test_variante_que_nenhuma_linha_tem_e_codigo_base_disputado_nao_vai_para_ninguem():
    """uaf001m1.xyz: as 4 linhas dividem o código base — mandar seria chutar."""
    estranho = [_link("uaf001m1.xyz", item="Z", variacao="9")]
    for linha in ("vidro", "pote25", "pote4", "pote6"):
        assert _resolver(estranho, linha) == []


def test_codigo_base_sem_disputa_continua_valendo():
    """Linha que não divide o código base com ninguém: tudo como antes
    (variante nova do barbecue continua recebendo)."""
    links = [_link("uaf002m1.127", item="B", variacao="1")]
    assert _resolver(links, "barbecue") == ["uaf002m1.127"]


def test_catalogo_ml_tambem_usa_a_regra():
    classico = {"variacao": None, "catalogo": True, "listing_type": "gold_special"}
    cat = [
        _link("uaf001m1.2l", item="MLB9", **classico),
        _link("uaf001m1.110", item="MLB8", **classico),
    ]
    assert _resolver(cat, "pote25", plataforma="ml", canal=CANAL_CATALOGO,
                     listing_type_conta="ml classico") == ["uaf001m1.2l"]
    assert _resolver(cat, "vidro", plataforma="ml", canal=CANAL_CATALOGO,
                     listing_type_conta="ml classico") == ["uaf001m1.110"]


def test_celular_sem_disputa_nao_muda():
    """Celular: linha com o código base e anúncios com sufixos (.sp/.ci/.ra)
    continuam todos dela; nenhuma linha divide código base no celular."""
    full, base = {"dg053"}, {"dg053"}
    outras = {"dg019", "a001.sp"}
    for sku in ("dg053.sp", "dg053.ci", "dg053.ra+a075", "dg053"):
        assert sku_casa_no_departamento(
            sku, dept="celular", sku_full_set=full, sku_base_set=base,
            skus_outras_linhas=outras,
        )
    assert not sku_casa_no_departamento(
        "dg019.sp", dept="celular", sku_full_set=full, sku_base_set=base,
        skus_outras_linhas=outras,
    )


def test_mala_e_catalogo_nao_usam_a_regra_nova():
    assert sku_casa_no_departamento(
        "b109.20", dept="mala", sku_full_set={"b109.20"}, sku_base_set={"b109"},
        skus_outras_linhas={"b109.20"},
    )


def test_prefixos_de_ponto():
    assert prefixos_de_ponto("uaf001m1.2l") == ["uaf001m1.2l", "uaf001m1"]
    assert prefixos_de_ponto("t031.sa.x") == ["t031.sa.x", "t031.sa", "t031"]
    assert prefixos_de_ponto("t031") == ["t031"]


def test_mais_especifico_vence_com_linha_de_codigo_base():
    """Uma linha com o código base puro (t031) e outra com a variante
    (t031.sa): a variante vai só para a linha específica."""
    assert casa_pelo_codigo(
        "t031.sa", sku_full_set={"t031.sa"}, sku_base_set={"t031"},
        skus_outras_linhas={"t031"},
    )
    assert not casa_pelo_codigo(
        "t031.sa", sku_full_set={"t031"}, sku_base_set={"t031"},
        skus_outras_linhas={"t031.sa"},
    )
    assert casa_pelo_codigo(
        "t031.sb", sku_full_set={"t031"}, sku_base_set={"t031"},
        skus_outras_linhas={"t031.sa"},
    )


def test_linhas_do_departamento_exclui_a_propria_e_outros_departamentos():
    ld = LinhasDoDepartamento.de([
        ("a", "eletro", "UAF001M1.2L"),
        ("b", "eletro", "uaf001m1.4l, uaf001m1.6l"),
        ("c", "celular", "uaf001m1.2l"),
        ("d", None, "x"),
    ])
    assert ld.outras("eletro", "a") == {"uaf001m1.4l", "uaf001m1.6l"}
    assert ld.outras("eletro", "b") == {"uaf001m1.2l"}
    assert ld.outras("celular", "c") == frozenset()
    assert ld.outras(None, "d") == frozenset()
