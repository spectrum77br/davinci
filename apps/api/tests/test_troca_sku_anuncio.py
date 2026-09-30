"""Troca do SKU .sp dos anúncios (lógica pura: app/services/troca_sku_anuncio.py)."""

from __future__ import annotations

import pytest

from app.services import troca_sku_anuncio as ts


def _p(id_: str, sku: str, sit: str | None = "A") -> ts.ProdutoInfo:
    return ts.ProdutoInfo(id_, sku, sit)


def _link(
    link_id="L1",
    plataforma="ml",
    iid="I1",
    conta="kfa",
    ext="MLB1",
    var=None,
    ext_sku="dg010.sp",
    pid="P-sp",
    psku="dg010.sp",
) -> ts.LinkInfo:
    return ts.LinkInfo(link_id, plataforma, iid, conta, ext, var, ext_sku, pid, psku)


# ------------------------------------------------------------------ SKU / lote


@pytest.mark.parametrize(
    "sku,lote,esperado",
    [
        ("dg010.sp", "ci", "dg010.ci"),
        ("dg010.sp+a001.sp", "pi", "dg010.pi+a001.pi"),
        ("dg010.sp+a003.sp+a004.sp", "ra", "dg010.ra+a003.ra+a004.ra"),
        ("dg010.SP+a001.sp", "ci", "dg010.ci+a001.ci"),
        (" dg010.sp ", "ci", "dg010.ci"),
        ("dg010.spx", "ci", "dg010.spx"),  # não é lote .sp
    ],
)
def test_trocar_lote_troca_todas_as_pecas(sku, lote, esperado):
    assert ts.trocar_lote(sku, lote) == esperado


def test_tem_lote_sp_e_base():
    assert ts.tem_lote_sp("dg010.sp+a001.sp")
    assert ts.tem_lote_sp("x.ci+a001.sp")
    assert not ts.tem_lote_sp("dg010.spa")
    assert not ts.tem_lote_sp(None)
    assert ts.base_sem_lote("dg090.ci+a001.ci") == ts.base_sem_lote("dg090.sp+a001.sp")


def test_escolher_alvo_segue_ci_ra_pi():
    idx = ts.IndiceProdutos([_p("1", "dg053.ci"), _p("2", "dg053.pi"), _p("3", "dg019.ra")])
    a = ts.escolher_alvo("dg053.sp", idx)
    assert (a.produto.sku, a.lote, a.motivo) == ("dg053.ci", "ci", "ok")
    a = ts.escolher_alvo("dg019.sp", idx)
    assert (a.produto.sku, a.lote) == ("dg019.ra", "ra")


def test_escolher_alvo_pula_inativo_e_para_no_ambiguo():
    idx = ts.IndiceProdutos([
        _p("1", "dg010.ci", "E"),
        _p("2", "dg010.pi"),
        _p("3", "dg054.ci"),
        _p("4", "DG054.CI"),  # duplicado (mesmo SKU normalizado)
        _p("5", "dg054.pi"),
    ])
    a = ts.escolher_alvo("dg010.sp", idx)
    assert (a.produto.sku, a.lote) == ("dg010.pi", "pi")
    a = ts.escolher_alvo("dg054.sp", idx)
    assert a.produto is None and a.motivo.startswith("alvo_ambiguo")


def test_escolher_alvo_kit_exige_o_kit_inteiro():
    idx = ts.IndiceProdutos([_p("1", "dg010.ci"), _p("2", "a001.ci"), _p("3", "dg010.pi+a001.pi")])
    a = ts.escolher_alvo("dg010.sp+a001.sp", idx)
    assert a.produto.sku == "dg010.pi+a001.pi"
    a = ts.escolher_alvo("x010.sp", idx)
    assert a.produto is None and a.motivo == "sem_alvo:ci=nao_existe,ra=nao_existe,pi=nao_existe"


def test_mapa_csv_e_validacao_do_alvo():
    texto = "sku_sp,alvo,lote_alvo\ndg010.sp,dg010.pi,pi\nx010.sp,,\ndg055.sp,dg055.ci,ci\n"
    mapa = ts.ler_mapa_csv(texto)
    assert mapa == {"dg010.sp": "dg010.pi", "dg055.sp": "dg055.ci"}
    idx = ts.IndiceProdutos([_p("1", "dg010.pi"), _p("2", "dg055.ci", "I")])
    ok = ts.alvo_pelo_mapa("DG010.SP", mapa, idx)
    assert (ok.produto.id, ok.lote) == ("1", "pi")
    assert ts.alvo_pelo_mapa("dg055.sp", mapa, idx).motivo == "alvo_inativo:dg055.ci"
    assert ts.alvo_pelo_mapa("x010.sp", mapa, idx).motivo == "sem_alvo:fora_do_mapa"


# ------------------------------------------------------------------ plano


def _alvo_fixo(mapa: dict[str, ts.ProdutoInfo]):
    def f(sp: str) -> ts.Alvo:
        p = mapa.get(sp)
        return ts.Alvo(p, "ci", "ok") if p else ts.Alvo(None, None, "sem_alvo:teste")

    return f


def test_montar_plano_regras():
    alvo = _alvo_fixo({"dg010.sp": _p("P-ci", "dg010.ci")})
    links = [
        _link("L1"),  # ML sem variação
        _link("L2", plataforma="shopee", ext="58", var="149", ext_sku="dg010.ci", psku="dg010.sp"),
        _link("L3", plataforma="amazon", ext="dg010.sp", var="B0X"),
        _link("L4", ext="MLB2", ext_sku="x010.sp", psku="x010.sp"),
        _link("L5", ext="MLB3", ext_sku="dg010.ci", pid="P-ci", psku="dg010.ci"),  # sem .sp
        _link("L6", ext="MLB1", var=None),  # duplicado do L1
    ]
    plano, fora = ts.montar_plano(links, alvo)
    assert [(x.external_id, x.sku_esperado, x.sku_novo) for x in plano] == [
        ("MLB1", "dg010.sp", "dg010.ci"),
        ("58", "dg010.sp", "dg010.ci"),  # anúncio .ci, produto .sp → o .sp do produto
    ]
    assert plano[0].links == [("L1", "P-sp", "P-ci"), ("L6", "P-sp", "P-ci")]
    motivos = {x.external_id: x.motivo_fora for x in fora}
    assert motivos == {"dg010.sp": "amazon_nao_renomeia", "MLB2": "sem_alvo:teste"}
    assert fora[0].links[0][2] is None  # fora do plano não tem produto destino


def test_montar_plano_duplicado_conflitante_sai():
    alvo = _alvo_fixo({"dg010.sp": _p("P1", "dg010.ci"), "dg053.sp": _p("P2", "dg053.ci")})
    links = [_link("L1", var="7"), _link("L2", var="7", ext_sku="dg053.sp", psku="dg053.sp")]
    plano, fora = ts.montar_plano(links, alvo)
    assert plano == [] and fora[0].motivo_fora == "vinculos_duplicados_conflitantes"


def test_agrupar_e_filtrar():
    alvo = _alvo_fixo({"dg010.sp": _p("P-ci", "dg010.ci")})
    links = [
        _link("L1", ext="MLB1", var="1"),
        _link("L2", ext="MLB1", var="2"),
        _link("L3", plataforma="tiktok", conta="Jlas", ext="17", var="9"),
    ]
    plano, _ = ts.montar_plano(links, alvo)
    grupos = ts.agrupar_por_anuncio(plano)
    assert [len(v) for v in grupos.values()] == [2, 1]
    assert len(ts.filtrar_plano(plano, conta="jlas")) == 1
    assert len(ts.filtrar_plano(plano, plataforma="ml", item="MLB1")) == 2
    assert ts.filtrar_plano(plano, item="MLB9") == []


# ------------------------------------------------------------------ decisão


def _nao_existe(_sku: str) -> bool:
    return False


@pytest.mark.parametrize(
    "live,irmao,esperado",
    [
        ("dg010.sp", False, "trocar"),
        (" DG010.SP ", False, "trocar"),
        ("dg010.ci", False, ts.JA_TROCADO),
        ("dg010.pi", False, ts.SKU_INESPERADO),
        ("dg010.pi", True, "trocar"),  # lote irmão só com a opção
        ("dg099.pi", True, ts.SKU_INESPERADO),
        (None, False, ts.SKU_INESPERADO),
        ("", True, ts.SKU_INESPERADO),
    ],
)
def test_classificar_sku(live, irmao, esperado):
    assert ts.classificar_sku(
        live, "dg010.sp", "dg010.ci", aceitar_irmao=irmao, existe_ativo=_nao_existe
    ) == esperado


# ------------------------------------------------------------------ ML


def _ml_var(vid, sku=None, scf=None, extra_attrs=()):
    attrs = [{"id": a, "value_name": "x"} for a in extra_attrs]
    if sku is not None:
        attrs.append({"id": "SELLER_SKU", "value_name": sku})
    return {"id": vid, "attributes": attrs, "seller_custom_field": scf, "picture_ids": ["p"]}


def test_ml_payload_sem_variacao():
    item = {"id": "MLB1", "attributes": [{"id": "SELLER_SKU", "value_name": "dg010.sp"}]}
    assert ts.ml_payload(item, {"": "dg010.ci"}) == {
        "attributes": [{"id": "SELLER_SKU", "value_name": "dg010.ci"}]
    }
    with pytest.raises(ValueError):
        ts.ml_payload(item, {"123": "x"})


def test_ml_payload_com_variacoes_manda_todas_pelo_id():
    item = {
        "id": "MLB1",
        "variations": [
            _ml_var(11, "dg072.pi"),
            _ml_var(12, "dg010.sp+a001.sp"),
            _ml_var(13, "dg090.sp"),
        ],
    }
    p = ts.ml_payload(item, {"12": "dg010.pi+a001.pi", "13": "dg090.pi"})
    assert p == {
        "variations": [
            {"id": 11},
            {"id": 12, "attributes": [{"id": "SELLER_SKU", "value_name": "dg010.pi+a001.pi"}]},
            {"id": 13, "attributes": [{"id": "SELLER_SKU", "value_name": "dg090.pi"}]},
        ]
    }
    # nada de preço, estoque, fotos ou seller_custom_field
    assert all(set(v) <= {"id", "attributes"} for v in p["variations"])
    with pytest.raises(ValueError):
        ts.ml_payload(item, {"99": "x"})


def test_ml_sku_de_e_localizar_variacao():
    assert ts.ml_sku_de(_ml_var(1, "a.sp", scf="b")) == ("a.sp", "SELLER_SKU")
    assert ts.ml_sku_de(_ml_var(1, None, scf="b.sp")) == ("b.sp", "seller_custom_field")
    assert ts.ml_sku_de({}) == (None, None)
    item = {"variations": [_ml_var(1, "a.sp"), _ml_var(2, "dg010.sp")]}
    assert ts.ml_localizar_variacao(item, "2", "x")[1] == "id"
    v, como = ts.ml_localizar_variacao(item, "999", "DG010.SP")
    assert (v["id"], como) == (2, "sku")
    assert ts.ml_localizar_variacao(item, None, "nada")[1] == "nao_encontrada"
    sem = {"attributes": []}
    assert ts.ml_localizar_variacao(sem, None, "x")[1] == "item"
    assert ts.ml_localizar_variacao(sem, "5", "x")[1] == "nao_encontrada"


@pytest.mark.parametrize(
    "status,sub,flag,esperado",
    [
        ("active", [], False, None),
        ("paused", ["out_of_stock"], False, None),
        ("under_review", ["waiting_for_patch"], False, "ml_under_review[waiting_for_patch]"),
        ("under_review", ["waiting_for_patch"], True, None),
        ("under_review", ["forbidden"], True, "ml_under_review[forbidden]"),
        ("under_review", ["forbidden", "waiting_for_patch"], True,
         "ml_under_review[forbidden,waiting_for_patch]"),
        ("inactive", ["deleted", "forbidden"], True, "ml_inactive[deleted,forbidden]"),
        ("closed", [], False, "ml_closed"),
    ],
)
def test_ml_motivo_status(status, sub, flag, esperado):
    item = {"status": status, "sub_status": sub}
    assert ts.ml_motivo_status(item, incluir_waiting_for_patch=flag) == esperado


def test_ml_conferir_confirma_e_pega_efeito_colateral():
    antes = {"variations": [_ml_var(1, "a.pi", extra_attrs=("GTIN",)), _ml_var(2, "b.sp",
                            extra_attrs=("GTIN",))]}
    ok = {"variations": [_ml_var(1, "a.pi", extra_attrs=("GTIN",)), _ml_var(2, "b.ci",
                         extra_attrs=("GTIN",))]}
    c = ts.ml_conferir(antes, ok, {"2": "b.ci"})
    assert c.confirmadas == {"2"} and not c.pendentes and not c.problemas

    pend = ts.ml_conferir(antes, antes, {"2": "b.ci"})
    assert pend.pendentes == {"2"} and not pend.problemas

    sumiu_attr = {"variations": [_ml_var(1, "a.pi", extra_attrs=("GTIN",)), _ml_var(2, "b.ci")]}
    assert ts.ml_conferir(antes, sumiu_attr, {"2": "b.ci"}).problemas

    outra_mudou = {"variations": [_ml_var(1, "zzz", extra_attrs=("GTIN",)),
                                  _ml_var(2, "b.ci", extra_attrs=("GTIN",))]}
    assert "sku_mudou_fora_do_plano[1]" in ts.ml_conferir(antes, outra_mudou,
                                                         {"2": "b.ci"}).problemas[0]

    sumiu_var = {"variations": [_ml_var(2, "b.ci", extra_attrs=("GTIN",))]}
    assert ts.ml_conferir(antes, sumiu_var, {"2": "b.ci"}).problemas[0].startswith(
        "variacoes_mudaram")


def test_ml_conferir_pega_estoque_zerado_e_preco():
    antes = {"variations": [{**_ml_var(1, "a.sp"), "available_quantity": 5, "price": 10}]}
    zerou = {"variations": [{**_ml_var(1, "a.ci"), "available_quantity": 0, "price": 10}]}
    assert ts.ml_conferir(antes, zerou, {"1": "a.ci"}).problemas == ["estoque_zerou[1]"]
    preco = {"variations": [{**_ml_var(1, "a.ci"), "available_quantity": 5, "price": 9}]}
    assert ts.ml_conferir(antes, preco, {"1": "a.ci"}).problemas[0].startswith("preco_mudou[1]")
    vendeu = {"variations": [{**_ml_var(1, "a.ci"), "available_quantity": 4, "price": 10}]}
    assert not ts.ml_conferir(antes, vendeu, {"1": "a.ci"}).problemas


def test_ml_conferir_item_sem_variacao():
    antes = {"attributes": [{"id": "SELLER_SKU", "value_name": "dg010.sp"}, {"id": "BRAND"}]}
    depois = {"attributes": [{"id": "SELLER_SKU", "value_name": "dg010.ci"}, {"id": "BRAND"}]}
    c = ts.ml_conferir(antes, depois, {"": "dg010.ci"})
    assert c.confirmadas == {""} and not c.problemas


def test_ml_classificar_erro():
    corpo = '{"cause":[{"code":"item.pictures.max","message":"max 12"}],"status":400}'
    assert ts.ml_classificar_erro(400, corpo) == ts.PULAR_FOTOS
    assert ts.ml_classificar_erro(401, "") == ts.CONTA_SEM_ACESSO
    assert ts.ml_classificar_erro(400, "field_not_updatable") == ts.ERRO_ESCRITA


# ------------------------------------------------------------------ Shopee


def test_shopee_payload_so_modelos_trocados():
    assert ts.shopee_payload("58206634324", {"149791640976": "dg010.pi"}) == {
        "item_id": 58206634324,
        "model": [{"model_id": 149791640976, "model_sku": "dg010.pi"}],
    }
    with pytest.raises(ValueError):
        ts.shopee_payload(1, {})


def test_shopee_conferir():
    antes = [{"model_id": 1, "model_sku": "a.sp"}, {"model_id": 2, "model_sku": "b.ci"}]
    depois = [{"model_id": 1, "model_sku": "a.ci"}, {"model_id": 2, "model_sku": "b.ci"}]
    c = ts.shopee_conferir(antes, depois, {"1": "a.ci"})
    assert c.confirmadas == {"1"} and not c.problemas
    ruim = [{"model_id": 1, "model_sku": "a.ci"}, {"model_id": 2, "model_sku": ""}]
    assert ts.shopee_conferir(antes, ruim, {"1": "a.ci"}).problemas
    assert ts.shopee_conferir(antes, depois[:1], {"1": "a.ci"}).problemas


def test_shopee_conferir_estoque_zerado():
    def m(sku, qtd):
        return {"model_id": 1, "model_sku": sku,
                "stock_info_v2": {"summary_info": {"total_available_stock": qtd}}}

    assert ts.shopee_conferir([m("a.sp", 3)], [m("a.ci", 0)], {"1": "a.ci"}).problemas == [
        "estoque_zerou[1]"
    ]
    assert not ts.shopee_conferir([m("a.sp", 3)], [m("a.ci", 3)], {"1": "a.ci"}).problemas


def test_shopee_motivo_status():
    assert ts.shopee_motivo_status("NORMAL") is None
    assert ts.shopee_motivo_status("UNLIST") is None
    assert ts.shopee_motivo_status("BANNED") == "shopee_banned"


# ------------------------------------------------------------------ TikTok


def _tk_sku(sid, sku, preco="100", cor="Preto"):
    s = {"id": sid, "price": {"amount": preco}, "sales_attributes": [{"id": "100000",
         "value_id": cor}], "inventory": [{"quantity": 3}]}
    if sku is not None:
        s["seller_sku"] = sku
    return s


def test_tiktok_payload_manda_todos_os_skus():
    prod = {
        "skus": [
            _tk_sku("1", "dg073.pi"),
            _tk_sku("2", "dg090.sp"),
            _tk_sku("3", "dg072.pi+003.pi+a004.pia"),  # malformado: vai como está
            _tk_sku("4", None),
            _tk_sku("5", "dg091.sp+a001.sp"),
        ]
    }
    p = ts.tiktok_payload(prod, {"2": "dg090.pi", "5": "dg091.pi+a001.pi"})
    attrs = [{"id": "100000", "value_id": "Preto"}]  # os de _tk_sku, como vieram
    assert p == {
        "skus": [
            {"id": "1", "sales_attributes": attrs, "seller_sku": "dg073.pi"},
            {"id": "2", "sales_attributes": attrs, "seller_sku": "dg090.pi"},
            {"id": "3", "sales_attributes": attrs, "seller_sku": "dg072.pi+003.pi+a004.pia"},
            {"id": "4", "sales_attributes": attrs},
            {"id": "5", "sales_attributes": attrs, "seller_sku": "dg091.pi+a001.pi"},
        ]
    }
    with pytest.raises(ValueError):
        ts.tiktok_payload(prod, {"9": "x"})


@pytest.mark.parametrize(
    "status,audit,flag,esperado",
    [
        ("ACTIVATE", "APPROVED", False, None),
        ("ACTIVATE", None, False, None),
        ("ACTIVATE", "REJECTED", False, "tiktok_activate[audit=rejected]"),
        ("ACTIVATE", "REJECTED", True, None),
        ("FAILED", None, False, "tiktok_failed"),
        ("FAILED", "REJECTED", True, None),
        ("PENDING", None, True, "tiktok_pending"),
        ("FREEZE", None, True, "tiktok_freeze"),
        ("DELETED", None, True, "tiktok_deleted"),
    ],
)
def test_tiktok_motivo_status(status, audit, flag, esperado):
    prod = {"status": status, "audit": {"status": audit} if audit else None}
    assert ts.tiktok_motivo_status(prod, incluir_reprovados=flag) == esperado


def test_tiktok_conferir():
    antes = {"skus": [_tk_sku("1", "a.pi"), _tk_sku("2", "b.sp", cor="Azul")]}
    ok = {"skus": [_tk_sku("1", "a.pi"), _tk_sku("2", "b.pi", cor="Azul")]}
    c = ts.tiktok_conferir(antes, ok, {"2": "b.pi"})
    assert c.confirmadas == {"2"} and not c.problemas
    # auditoria: a versão no ar ainda é a antiga → pendente, sem problema
    c = ts.tiktok_conferir(antes, antes, {"2": "b.pi"})
    assert c.pendentes == {"2"} and not c.problemas
    preco = {"skus": [_tk_sku("1", "a.pi", preco="90"), _tk_sku("2", "b.pi", cor="Azul")]}
    assert any(p.startswith("preco_mudou[1]") for p in ts.tiktok_conferir(
        antes, preco, {"2": "b.pi"}).problemas)
    sumiu = {"skus": [_tk_sku("2", "b.pi", cor="Azul")]}
    assert ts.tiktok_conferir(antes, sumiu, {"2": "b.pi"}).problemas[0].startswith("skus_mudaram")
    zerou = {"skus": [_tk_sku("1", "a.pi"), {**_tk_sku("2", "b.pi", cor="Azul"),
                                             "inventory": [{"quantity": 0}]}]}
    assert "estoque_zerou[2]" in ts.tiktok_conferir(antes, zerou, {"2": "b.pi"}).problemas
    variacao = {"skus": [_tk_sku("1", "a.pi"), _tk_sku("2", "b.pi", cor="Verde")]}
    assert "variacao_mudou[2]" in ts.tiktok_conferir(antes, variacao, {"2": "b.pi"}).problemas


def test_tiktok_sku_de():
    prod = {"skus": [_tk_sku("1", "a"), _tk_sku("2", "b")]}
    assert ts.tiktok_sku_de(prod, "2")[0]["seller_sku"] == "b"
    assert ts.tiktok_sku_de(prod, "3") == (None, "nao_encontrada")
    assert ts.tiktok_sku_de({"skus": [_tk_sku("1", "a")]}, None)[1] == "unico"


# ------------------------------------------------------------------ limite / log / desfazer


def test_eh_limite():
    assert ts.eh_limite("ml", 429, "")
    assert ts.eh_limite("shopee", 200, '{"error":"error_busy"}')
    assert ts.eh_limite("tiktok", 200, '{"code":36009003,"message":"Too many requests"}')
    assert not ts.eh_limite("ml", 400, "item.pictures.max")


def _reg(modo, resultado, var, antes="dg010.sp", novo="dg010.ci", links=None):
    linha = ts.PlanoLinha("ml", "I1", "kfa", "MLB1", var, "dg010.sp", novo,
                         links=links or [("L1", "P-sp", "P-ci")])
    return ts.registro(linha, resultado, modo=modo, sku_antes=antes, sku_depois=novo)


def test_desfazer_so_trocados_do_executar_e_inverte_vinculo():
    regs = [
        _reg("dry-run", ts.TROCARIA, "1"),
        _reg("executar", ts.TROCADO, "2"),
        _reg("executar", ts.SKU_INESPERADO, "3"),
        _reg("executar", ts.PENDENTE_AUDITORIA, "4"),
        _reg("executar", ts.ERRO_ESCRITA, "5"),
        _reg("executar", ts.TROCADO, "6", antes="dg010.ci"),  # antes == novo: nada a desfazer
    ]
    plano = ts.plano_desfazer(regs)
    assert [x.variation_id for x in plano] == ["2", "4"]
    d = plano[0]
    assert (d.sku_esperado, d.sku_novo) == ("dg010.ci", "dg010.sp")
    assert d.links == [("L1", "P-ci", "P-sp")]
    # desfazer do desfazer: o anúncio (já com o .sp de volta) é "já trocado"
    assert ts.classificar_sku("dg010.sp", d.sku_esperado, d.sku_novo) == ts.JA_TROCADO


def test_desfazer_prefere_a_linha_que_teve_escrita():
    # Correção 6: uma linha depois da troca (outra tentativa que deu erro, ou
    # uma rodada seguinte que viu 'ja_trocado') não apaga a troca de antes.
    regs = [_reg("executar", ts.TROCADO, "2"), _reg("executar", ts.ERRO_ESCRITA, "2")]
    assert [x.variation_id for x in ts.plano_desfazer(regs)] == ["2"]
    regs = [_reg("executar", ts.TROCADO, "2"),
            _reg("executar", ts.JA_TROCADO, "2", antes="dg010.ci")]
    assert [x.variation_id for x in ts.plano_desfazer(regs)] == ["2"]


def test_registro_e_resumo():
    regs = [
        _reg("dry-run", ts.TROCARIA, "1"),
        _reg("dry-run", ts.TROCARIA, "2"),
        _reg("dry-run", ts.SKU_INESPERADO, "3"),
    ]
    r = regs[0]
    assert {"ts", "plataforma", "conta", "external_id", "variation_id", "sku_antes",
            "sku_depois", "resultado", "erro", "links"} <= set(r)
    res = ts.resumir(regs)
    assert res["por_plataforma"] == {"ml": {ts.SKU_INESPERADO: 1, ts.TROCARIA: 2}}
    assert res["motivos"][0]["resultado"] == ts.SKU_INESPERADO


# ------------------------------------------------------------------ revisão 30/09
# Um bloco por defeito da revisão adversarial (numeração da revisão).


# 1) TikTok: versão em auditoria, religar só aprovado, desfazer pela versão recente


def test_tiktok_param_da_versao_em_auditoria():
    # nome do parâmetro na doc do Get Product 202309
    assert ts.TIKTOK_PARAM_VERSAO_EM_AUDITORIA == {"return_under_review_version": "true"}


@pytest.mark.parametrize(
    "status,audit,reprov,em_aud,esperado",
    [
        # --tiktok-incluir-reprovados nunca pega edição em andamento
        ("ACTIVATE", "AUDITING", True, False, "tiktok_activate[audit=auditing]"),
        ("FAILED", "AUDITING", True, False, "tiktok_failed[audit=auditing]"),
        # só o --desfazer aceita a nossa edição em auditoria
        ("ACTIVATE", "AUDITING", False, True, None),
        ("PENDING", "AUDITING", False, True, "tiktok_pending[audit=auditing]"),
        ("ACTIVATE", "REJECTED", False, True, "tiktok_activate[audit=rejected]"),
    ],
)
def test_tiktok_motivo_status_auditoria(status, audit, reprov, em_aud, esperado):
    prod = {"status": status, "audit": {"status": audit}}
    assert ts.tiktok_motivo_status(
        prod, incluir_reprovados=reprov, aceitar_em_auditoria=em_aud
    ) == esperado


def test_tiktok_so_religa_aprovado_e_no_ar():
    def prod(status, audit, sku):
        return {"status": status, "audit": {"status": audit} if audit else None,
                "skus": [_tk_sku("2", sku)]}

    no_ar_ok = prod("ACTIVATE", "APPROVED", "b.pi")
    assert ts.tiktok_motivo_nao_religar(no_ar_ok, no_ar_ok, "2", "b.pi") is None
    assert ts.tiktok_motivo_nao_religar(prod("ACTIVATE", None, "b.pi"),
                                        prod("ACTIVATE", None, "b.pi"), "2", "b.pi") is None
    # edição em auditoria: versão recente com o novo, no ar ainda o antigo
    assert ts.tiktok_motivo_nao_religar(
        prod("ACTIVATE", "AUDITING", "b.sp"), prod("ACTIVATE", "AUDITING", "b.pi"), "2", "b.pi"
    ) == "auditoria_no_ar=auditing"
    assert ts.tiktok_motivo_nao_religar(
        prod("ACTIVATE", "APPROVED", "b.sp"), prod("PENDING", "AUDITING", "b.pi"), "2", "b.pi"
    ) == "auditoria_versao_recente=auditing"
    assert ts.tiktok_motivo_nao_religar(
        prod("FAILED", "APPROVED", "b.pi"), no_ar_ok, "2", "b.pi"
    ) == "status=failed"
    assert ts.tiktok_motivo_nao_religar(
        prod("ACTIVATE", "APPROVED", "b.sp"), no_ar_ok, "2", "b.pi"
    ) == "no_ar_ainda_sem_o_sku_novo"


def test_tiktok_conferir_status():
    antes = {"status": "ACTIVATE", "skus": [_tk_sku("1", "a.sp")]}
    depois = {"status": "PENDING", "skus": [_tk_sku("1", "a.ci")]}
    # no ar: qualquer mudança de status para tudo
    assert ts.tiktok_conferir(antes, depois, {"1": "a.ci"}).problemas[0].startswith(
        "status_mudou")
    # versão recente: PENDING (em auditoria) é o esperado depois de editar
    c = ts.tiktok_conferir(antes, depois, {"1": "a.ci"}, versao="recente")
    assert c.confirmadas == {"1"} and not c.problemas
    falhou = {"status": "FAILED", "skus": [_tk_sku("1", "a.ci")]}
    assert ts.tiktok_conferir(antes, falhou, {"1": "a.ci"}, versao="recente").problemas


def test_tiktok_desfazer_de_pendente_classifica_pela_versao_recente():
    regs = [_reg("executar", ts.ESCREVENDO, "2"), _reg("executar", ts.PENDENTE_AUDITORIA, "2")]
    (d,) = ts.plano_desfazer(regs)
    # a versão em auditoria (recente) tem o SKU novo → o desfazer escreve
    assert ts.classificar_sku("dg010.ci", d.sku_esperado, d.sku_novo) == "trocar"
    # a versão no ar ainda com o antigo daria ja_trocado (o bug da revisão)
    assert ts.classificar_sku("dg010.sp", d.sku_esperado, d.sku_novo) == ts.JA_TROCADO


# 2 e 3) desfazer inclui toda escrita enviada; linha 'escrevendo' antes da escrita


def test_desfazer_inclui_nao_confirmado_divergente_erro_interno_e_escrevendo():
    regs = [
        _reg("executar", ts.ESCREVENDO, "1"), _reg("executar", ts.NAO_CONFIRMADO, "1"),
        _reg("executar", ts.ESCREVENDO, "2"), _reg("executar", ts.DIVERGENTE, "2"),
        # erro_interno sem sku_antes: vem da 'escrevendo'
        _reg("executar", ts.ESCREVENDO, "3"),
        _reg("executar", ts.ERRO_INTERNO, "3", antes=None),
        # o processo morreu depois de enviar: só a 'escrevendo'
        _reg("executar", ts.ESCREVENDO, "4"),
        # o marketplace recusou a escrita: não entra
        _reg("executar", ts.ESCREVENDO, "5"), _reg("executar", ts.ERRO_ESCRITA, "5"),
        _reg("executar", ts.ESCREVENDO, "6"), _reg("executar", ts.PULAR_FOTOS, "6"),
        # sem escrita nenhuma: não entra
        _reg("executar", ts.SKU_INESPERADO, "7"),
    ]
    plano = ts.plano_desfazer(regs)
    assert [x.variation_id for x in plano] == ["1", "2", "3", "4"]
    assert all((x.sku_esperado, x.sku_novo) == ("dg010.ci", "dg010.sp") for x in plano)


def test_desfazer_usa_a_chave_exata_da_escrevendo():
    esc = _reg("executar", ts.ESCREVENDO, "999")  # variation_id do vínculo, velho
    esc["chave"] = "197494095128"  # a variação achada pelo SKU e escrita
    fim = _reg("executar", ts.TROCADO, "999")
    (d,) = ts.plano_desfazer([esc, fim])
    assert d.variation_id == "197494095128"


# 5) conferência pós-escrita


def test_ml_conferir_fotos_combinacoes_status_e_valores():
    def var(**kw):
        cor = {"id": "COLOR", "value_id": "52049", "value_name": "Preto"}
        v = {**_ml_var(1, "a.sp", extra_attrs=("GTIN",)),
             "attribute_combinations": [cor], "picture_ids": ["p1", "p2"]}
        v.update(kw)
        return v

    antes = {"status": "active", "pictures": [{"id": "p1"}], "variations": [var()]}

    def depois(item_kw=None, **var_kw):
        v = var(**var_kw)
        v["attributes"] = [a if a["id"] != "SELLER_SKU" else {**a, "value_name": "a.ci"}
                           for a in v["attributes"]]
        return {**antes, **(item_kw or {}), "variations": [v]}

    trocas = {"1": "a.ci"}
    ok = ts.ml_conferir(antes, depois(), trocas)
    assert ok.confirmadas == {"1"} and not ok.problemas
    assert "fotos_mudaram[1]" in ts.ml_conferir(antes, depois(picture_ids=["p1"]), trocas).problemas
    assert "combinacoes_mudaram[1]" in ts.ml_conferir(
        antes, depois(attribute_combinations=[]), trocas).problemas
    assert ts.ml_conferir(antes, depois({"status": "under_review"}), trocas).problemas == [
        "status_mudou: active → under_review"]
    assert "fotos_mudaram[item]" in ts.ml_conferir(
        antes, depois({"pictures": []}), trocas).problemas
    gtin = depois()
    gtin["variations"][0]["attributes"] = [
        {**a, "value_name": "outro"} if a["id"] == "GTIN" else a
        for a in gtin["variations"][0]["attributes"]
    ]
    assert ts.ml_conferir(antes, gtin, trocas).problemas[0].startswith("atributo_mudou[1]: GTIN")


def test_shopee_conferir_preco_e_status():
    def m(sku, preco):
        return {"model_id": 1, "model_sku": sku,
                "price_info": [{"current_price": preco, "original_price": 20}]}

    trocas = {"1": "a.ci"}
    assert not ts.shopee_conferir([m("a.sp", 10)], [m("a.ci", 10)], trocas,
                                  status_antes="NORMAL", status_depois="NORMAL").problemas
    assert ts.shopee_conferir([m("a.sp", 10)], [m("a.ci", 9)], trocas).problemas[0].startswith(
        "preco_mudou[1]")
    assert ts.shopee_conferir([m("a.sp", 10)], [m("a.ci", 10)], trocas, status_antes="NORMAL",
                              status_depois="UNLIST").problemas == [
        "status_mudou: NORMAL → UNLIST"]


# 7) lote irmão só quando o SKU lido não existe ativo


def test_lote_irmao_so_quando_o_lido_nao_existe_ativo():
    idx = ts.IndiceProdutos([_p("1", "dg010.pi"), _p("2", "dg090.pi")])

    def existe(s):
        return idx.resolver_ativo(s)[1] in ("ok", "ambiguo")

    # dg010.pi existe e está ativo: nunca sobrescreve
    assert ts.classificar_sku("dg010.pi", "dg010.sp", "dg010.ci", aceitar_irmao=True,
                              existe_ativo=existe) == ts.SKU_INESPERADO
    # dg090.ci não existe (caso Barbosa): aceita
    assert ts.classificar_sku("dg090.ci", "dg090.sp", "dg090.pi", aceitar_irmao=True,
                              existe_ativo=existe) == "trocar"
    # sem o índice não aceita
    assert ts.classificar_sku("dg090.ci", "dg090.sp", "dg090.pi",
                              aceitar_irmao=True) == ts.SKU_INESPERADO


# 8) férias


def test_plano_carrega_ferias_da_conta():
    alvo = _alvo_fixo({"dg010.sp": _p("P-ci", "dg010.ci")})
    lk = ts.LinkInfo("L1", "ml", "I1", "counhago", "MLB1", None, "dg010.sp", "P", "dg010.sp",
                     ferias=True)
    plano, _ = ts.montar_plano([lk], alvo)
    assert plano[0].ferias is True


# 10) alvo sobre o SKU normalizado e validado (sem .sp, mesmo kit)


def test_trocar_lote_normaliza_espacos():
    assert ts.trocar_lote("dg010.sp + a001.sp", "ci") == "dg010.ci+a001.ci"


def test_alvo_nunca_tem_peca_sp_nem_outro_kit():
    assert ts.motivo_alvo_invalido("dg010.sp+a001.sp", "dg010.pi+a001.pi") is None
    assert ts.motivo_alvo_invalido("dg010.sp+a001.sp", "dg010.sp+a001.ci").startswith(
        "alvo_com_peca_sp")
    assert ts.motivo_alvo_invalido("dg010.sp", "x1.pi").startswith("alvo_de_outro_kit")
    # o caso da revisão: com espaço, o recalculado não cai num kit que ainda tem .sp
    idx = ts.IndiceProdutos([_p("1", "dg010.sp+a001.ci"), _p("2", "dg010.ci+a001.ci")])
    a = ts.escolher_alvo("dg010.sp + a001.sp", idx)
    assert a.produto.id == "2"
    # --mapa: alvo ativo de OUTRO kit ou com .sp é recusado
    idx = ts.IndiceProdutos([_p("1", "x1.pi"), _p("2", "dg010.sp+a001.ci")])
    mapa = {"dg010.sp": "x1.pi", "dg010.sp+a001.sp": "dg010.sp+a001.ci"}
    assert ts.alvo_pelo_mapa("dg010.sp", mapa, idx).motivo == "alvo_de_outro_kit:x1.pi"
    assert ts.alvo_pelo_mapa("dg010.sp+a001.sp", mapa, idx).motivo.startswith(
        "alvo_com_peca_sp")


# 11) seller_custom_field .sp no resumo


def test_resumo_conta_seller_custom_field_sp():
    r1 = _reg("dry-run", ts.TROCARIA, "1")
    r1.update(seller_custom_field_sp=True, seller_custom_field="dg010.sp")
    r2 = _reg("dry-run", ts.PULAR_STATUS, "1")  # mesma variação: conta 1 vez
    r2.update(seller_custom_field_sp=True, seller_custom_field="dg010.sp")
    r3 = _reg("dry-run", ts.TROCARIA, "2")
    r3.update(seller_custom_field_sp=False)
    res = ts.resumir([r1, r2, r3])
    assert res["ml_seller_custom_field_sp"]["n"] == 1
    assert res["ml_seller_custom_field_sp"]["lista"][0]["seller_custom_field"] == "dg010.sp"


def test_alvo_sem_lote_nao_serve():
    """Revisão de 30/09 (N4): "dg010" (sem lote) tem a mesma base do
    "dg010.sp" e passava como alvo pelo --mapa."""
    from app.services.troca_sku_anuncio import motivo_alvo_invalido

    assert motivo_alvo_invalido("dg010.sp", "dg010").startswith("alvo_sem_lote")
    assert motivo_alvo_invalido("dg010.sp+a001.sp", "dg010.pi+a001").startswith("alvo_sem_lote")
    assert motivo_alvo_invalido("dg010.sp+a001.sp", "dg010.pi+a001.pi") is None


def test_tiktok_payload_leva_os_atributos_de_venda():
    """Piloto de 30/09: a TikTok recusou o partial_edit sem sales_attributes
    (12052241). Cada SKU vai com os atributos lidos e a imagem pelo uri."""
    from app.services import troca_sku_anuncio as ts

    cor = {"id": "100000", "name": "Cor", "value_id": "76", "value_name": "Preto"}
    mem = {"id": "100027", "name": "Memória", "value_id": "74", "value_name": "12/128"}
    img = {"uri": "tos/abc", "urls": ["https://x"], "height": 1, "width": 1}
    prod = {"skus": [{
        "id": "1", "seller_sku": "dg052.sp+a001.sp",
        "sales_attributes": [{**cor, "sku_img": img}, mem],
    }]}
    p = ts.tiktok_payload(prod, {"1": "dg052.ci+a001.ci"})
    assert p == {"skus": [{
        "id": "1",
        "sales_attributes": [{**cor, "sku_img": {"uri": "tos/abc"}}, mem],
        "seller_sku": "dg052.ci+a001.ci",
    }]}


def test_tiktok_payload_mantem_ean_medidas_e_nao_manda_preco_estoque():
    """2º piloto de 30/09 (12052593): o EAN já enviado não pode mudar, e no edit
    da TikTok o que não vai é apagado — menos preço e estoque, que por isso
    ficam de fora."""
    from app.services import troca_sku_anuncio as ts

    sku = {
        "id": "1", "seller_sku": "dg052.sp+a001.sp",
        "sales_attributes": [{"id": "100000", "value_id": "76"}],
        "identifier_code": {"code": "7908855902292", "type": "EAN"},
        "sku_dimensions": {"height": "15", "length": "10", "unit": "CENTIMETER", "width": "5"},
        "sku_weight": {"unit": "KILOGRAM", "value": "0.6"},
        "price": {"currency": "BRL", "sale_price": "1226.7"},
        "inventory": [{"quantity": 175, "warehouse_id": "w1"}],
        "status_info": {"status": "NORMAL"},
    }
    [e] = ts.tiktok_payload({"skus": [sku]}, {"1": "dg052.ci+a001.ci"})["skus"]
    assert e["seller_sku"] == "dg052.ci+a001.ci"
    assert e["identifier_code"] == sku["identifier_code"]
    assert e["sku_dimensions"] == sku["sku_dimensions"] and e["sku_weight"] == sku["sku_weight"]
    assert "price" not in e and "inventory" not in e and "status_info" not in e

