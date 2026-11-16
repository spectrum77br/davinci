"""Código de barras que a Shopee pede para mudar o envio (services/flex_gtin,
16/11/2026). Só as partes PURAS: validação, celular do SKU e a escolha do
código de cada variação vazia. O caminho inteiro (recusa → preenche → tenta
de novo) está em test_flex_motor.py."""

from __future__ import annotations

from collections import Counter

from app.services import flex_gtin


def test_valido_confere_o_digito_verificador():
    assert flex_gtin.valido("7908855913533")
    assert flex_gtin.valido("7908855913687")
    assert not flex_gtin.valido("7908855913534")  # dígito errado
    assert flex_gtin.valido("036000291452")  # UPC-A de 12
    assert not flex_gtin.valido("00")  # "sem GTIN" não é código
    assert not flex_gtin.valido("0000000000000")
    assert not flex_gtin.valido("79088559135ab")
    assert not flex_gtin.valido(None)


def test_recusa_por_gtin():
    assert flex_gtin.recusa_por_gtin(
        "200 product.error_busi: The GTIN code is required for this category. Please fill in it."
    )
    assert flex_gtin.recusa_por_gtin("The GTIN code is mandatory, please check and upload again.")
    assert not flex_gtin.recusa_por_gtin("This is not a valid GTIN. Please, inform a valid number.")
    assert not flex_gtin.recusa_por_gtin(
        "Cannot update logistic info because the product has promotion"
    )


def test_celular_do_sku():
    assert flex_gtin.celular_do_sku("dg019.ra+a003.ra") == "dg019"
    assert flex_gtin.celular_do_sku("DG111.pi+a001.pi") == "dg111"
    assert flex_gtin.celular_do_sku("z0102") == "z0102"
    assert flex_gtin.celular_do_sku("") is None
    assert flex_gtin.celular_do_sku(None) is None


def test_melhor_prefere_gs1_brasil_depois_o_mais_usado():
    # dg092 em 16/11/2026: dois de 12 dígitos e dois 7908855….
    assert flex_gtin.melhor(Counter({"088125263319": 2, "7908855913519": 2})) == "7908855913519"
    assert flex_gtin.melhor(Counter({"7908855900847": 1, "7908855900816": 3})) == "7908855900816"
    # Inválido não entra, nem sendo o mais usado.
    assert flex_gtin.melhor(Counter({"7908855913534": 9, "036000291452": 1})) == "036000291452"
    assert flex_gtin.melhor(Counter()) is None


def _m(mid, sku, gtin="", nome="", opcao=None):
    m = {"model_id": mid, "model_sku": sku, "gtin_code": gtin, "model_name": nome}
    if opcao is not None:
        m["tier_index"] = [opcao, 0]
    return m


def test_escolher_na_ordem_combinada():
    modelos = [
        # Mesmo celular em outra loja (dg019) — mesmo com o anúncio tendo outro.
        _m(1, "dg019.pi+a003.pi", nome="Preto,12.64", opcao=0),
        # Mesmo celular em outra variação do anúncio (dg111).
        _m(2, "dg111.pi+a001.pi", nome="Branco,12.64", opcao=1),
        _m(3, "dg111.ra+a003.ra", "7908855913533", nome="Branco,12.64 Fone", opcao=1),
        # "xx1" não é produto: não identifica celular → nome igual.
        _m(4, "xx1", nome="Azul,64", opcao=2),
        _m(5, "dg1003.pi+a001.pi", "7908855918996", nome="Azul,64", opcao=2),
        # Sem SKU: mesma primeira opção.
        _m(6, None, nome="Azul,128", opcao=2),
        # Sem SKU e opção sem código: o mais usado do anúncio.
        _m(7, None, nome="Verde,512", opcao=3),
        # Já tem código: não mexe.
        _m(8, "dg019.pi", "7908855901592", nome="x", opcao=0),
    ]
    de_fora = {"dg019": Counter({"7908855913533": 3, "016766499118": 1})}
    validos = {"dg019", "dg111", "dg1003"}
    e = flex_gtin.escolher(modelos, de_fora, validos)
    got = {m["model_id"]: (c, f) for m, c, f in e.preencher}
    assert got == {
        # dg019: o 7908855913533 (3 lojas) ganha do 7908855901592 (1, no anúncio).
        1: ("7908855913533", flex_gtin.FONTE_MESMO_CELULAR),
        2: ("7908855913533", flex_gtin.FONTE_MESMO_CELULAR),
        4: ("7908855918996", flex_gtin.FONTE_MESMO_NOME),
        6: ("7908855918996", flex_gtin.FONTE_MESMA_OPCAO),
        7: (got[7][0], flex_gtin.FONTE_DO_ANUNCIO),
    }
    assert flex_gtin.valido(got[7][0])
    assert e.sem_codigo == []


def test_escolher_sem_nenhum_codigo_fica_para_a_pessoa():
    e = flex_gtin.escolher([_m(1, "xx1", nome="Azul"), _m(2, None, nome="Preto")], {}, set())
    assert e.preencher == []
    assert [m["model_id"] for m in e.sem_codigo] == [1, 2]


def test_payload_modelo_leva_sku_peso_e_medidas_iguais():
    m = {
        "model_id": 189629499987,
        "model_sku": "dg111.pi+a003.pi",
        "gtin_code": "",
        "weight": "0.4",
        "dimension": {"package_width": 10, "package_height": 18, "package_length": 12},
    }
    assert flex_gtin.payload_modelo(m, "7908855913533") == {
        "model_id": 189629499987,
        "model_sku": "dg111.pi+a003.pi",
        "gtin_code": "7908855913533",
        "weight": 0.4,
        "dimension": {"package_height": 18, "package_length": 12, "package_width": 10},
    }
    # Sem SKU, peso nem medidas: só o obrigatório.
    assert flex_gtin.payload_modelo({"model_id": "5", "model_sku": None}, "7908855913533") == {
        "model_id": 5,
        "model_sku": "",
        "gtin_code": "7908855913533",
    }


def test_textos_da_nota_e_do_que_faltou():
    p = flex_gtin.Preenchimento(
        gravados=2,
        sem_codigo=["Azul,64"],
        codigos=[("a", "7908855913533", "x"), ("b", "7908855913533", "y")],
    )
    assert p.nota() == "código de barras preenchido pelo sistema em 2 variação(ões): 7908855913533"
    assert p.faltou() == "sem código de barras conhecido para 1 variação(ões): Azul,64"
    assert flex_gtin.Preenchimento().nota() == ""
