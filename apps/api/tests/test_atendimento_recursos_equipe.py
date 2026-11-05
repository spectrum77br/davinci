# ruff: noqa: F811 — fixtures importadas dos testes de ponta a ponta
"""O que a equipe diz vale (o dono, 05/11/2026): "confie no q a eqpe diz sempre".

NFC, 5G e zíper expansivo. Os títulos dos testes são os de `product_links` em produção (lidos em
05/11/2026, só leitura).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.models import Product, ProductLink
from app.services.atendimento import especificacoes as e
from app.services.atendimento import ia as ia_svc
from app.services.atendimento import recursos_equipe as r
from app.services.atendimento.agente import checagens
from tests.test_atendimento_agente_e2e import (  # noqa: F401 — fixtures
    _decisao,
    _do_agente,
    _ligar,
    _usa,
    agente_ligado,
    api,
    envios,
    ia_ligada,
)
from tests.test_atendimento_agente_recurso import (
    F105,
    G5,
    ITEM_A18,
    ITEM_S7,
    ITEM_VARIOS,
    NFC,
    S7_NFC,
    ZIPER,
    _achado,
    _achados,
    _alvo,
    _dado,
    _ficha,
    _link,
    _modelo,
    _nfc,
    _p,
    _variacoes,
)
from tests.test_atendimento_especificacoes import _juntar

C68_128 = (
    "Oukitel C68 Plus-ev Celular 4gb+128gb, Tela 7,2 Hd+ 120hz, Câmera 64mp, Bateria 6000mah, Nfc"
)
C68_512 = "Uranyx Oukitel C68 Plus 512 Gb Memoria 24 Gb Ram Dourado - 24.512"
C68_256 = "Uranyx Oukitel C68 Plus 256 Gb Memoria 24 Gb Ram Rosa - 24.256"
C68_TODAS = "Uranyx Oukitel C68 Plus 16.128 Gb ou 24.256 Gb ou 512 Gb"
F110_PRO = "Uranyx Fossibot F110 Pro 5g 128 Gb Memoria 20 Gb Ram Preto"
F110L = "Uranyx Fossibot F110l 128 Gb Memoria 8 Gb Ram Preto"
F110_OS_DOIS = "Uranyx Fossibot F110 Pro 5G 20.128 ou F110L 8.128 Gb"
A17_64 = "Uranyx Hotwav A17 Pro Max 64 Gb Memoria 12 Gb Ram Branco - 12.64"
A17_128 = "Uranyx Hotwav A17 Pro Max 128 Gb Memoria 12 Gb Ram Laranja - 128 Gb / 12 Gb"
A17_DUAS = "Uranyx Hotwav A17 Pro Max 64/128 Gb Memoria 12Gb"
A17_PREMIUM = "Uranyx Hotwav A17 Pro Max 128 Gb Memoria 16Gb (Branco)"
A17_TODAS = "Uranyx A17 Pro Max 12.64 / 12.128 GB / 16.128 Premium Edition NFC"
A18 = "Uranyx A18 Pro Max NFC 128 Gb Memoria 16 Gb Ram S5 Edition"
S5 = "Uranyx S5 128 Gb Memoria 16 Gb Ram Nfc Prata"
F112 = "Uranyx Fossibot F112 Pro 5g 256 Gb Memoria 24 Gb Ram Preto"
F109 = "Uranyx Fossibot F109 5g 256 Gb Memoria 24 Gb Ram Preto"
F109S = "Uranyx Fossibot F109s 256 Gb Memoria 24 Gb Ram Preto"
P5 = "CHARLOT'S PARK Kit Mala Mochila e Frasqueira com Acessorios Polipropileno - Seta P5 Champanhe"
P6 = "Mala Viagem Com Rodinhas 10kg 20 Polegadas De Bordo Polipropileno P6 - Dourado Minecraft"
P7 = "Kit Mala Bordo 20 Pol 10kg + Frasqueira Tsa Pp Rosa P7 (expansível/roda Removível)"
P8 = "Kit 3 Malas Pp 24 Pol 23kg E 20 Pol 10kg + Frasqueira Tsa Azul P8 (expansível/roda Removível)"
M4 = "Kit Conjunto Mala De Bordo 18 Polegadas + Frasqueira 12 Viagem M4 - Branco 2"


# ═══════════════ a tabela, linha por linha ═══════════════


@pytest.mark.parametrize(
    ("recurso", "titulo", "esperado"),
    [
        # NFC, o modelo inteiro.
        (r.NFC, A18, True),
        (r.NFC, "Uranyx A18 Pro Max 128 Gb Memoria 16 Gb Ram", True),
        (r.NFC, S5, True),
        (r.NFC, "Uranyx Oukitel Wp60 5g 24/256 Gb Memoria Ou 48/512 Gb Branco - 48.512", True),
        (r.NFC, F112, True),
        (r.NFC, F110L, None),
        (r.NFC, "Uranyx Oscal Marine 1 128 Gb Memoria 12 Gb Ram Preto", True),
        (r.NFC, "Uranyx P1 Pro 256 Gb Memoria 16 Gb Ram Preto", True),
        (r.NFC, "Uranyx P1 256 Gb Memoria 16 Gb Ram Preto", None),
        (r.NFC, F109, True),
        (r.NFC, "Caap ** Uranyx Wp53 Pro 256 Gb Memoria 24 Gb Ram Preto", None),
        (r.NFC, S7_NFC, False),
        (r.NFC, F105, False),
        (r.NFC, F109S, None),
        (r.NFC, "Uranyx C2 256 Gb Memoria 16 Gb Ram Violeta", False),
        (r.NFC, "Uranyx C65 Pro 256 Gb Memoria 24 Gb Ram Azul", False),
        (r.NFC, "Uranyx Séries C com 64,128,256 e 512 GB Memoria com NFC - C57s - Azul,64", False),
        (r.NFC, "Uranyx Oscal Flat 3c 128 Gb Memoria 12 Gb Ram Preto", False),
        # NFC, por versão.
        (r.NFC, F110_PRO, True),
        (r.NFC, "Fossibot F110 Pro 128db Speaker Dual Sim Preto", True),
        (r.NFC, "Uranyx Fossibot F110 128 Gb Preto", None),
        (r.NFC, C68_512, True),
        (r.NFC, "Uranyx Oukitel C68 Plus 128 Gb Memoria 16 Gb Ram Cinza - 16.128", False),
        (r.NFC, C68_128, False),
        (r.NFC, C68_256, None),
        (r.NFC, C68_TODAS, None),
        (r.NFC, "Oukitel C68 Plus Dual Sim Dourado", None),
        (r.NFC, A17_64, False),
        (r.NFC, A17_128, False),
        (r.NFC, A17_DUAS, False),
        (r.NFC, "Uranyx A17 Pro Max 12.128 GB / 12.64 GB Memoria", False),
        (r.NFC, A17_PREMIUM, True),
        (r.NFC, A17_TODAS, None),
        (r.NFC, "Uranyx A17 Pro Max", None),
        # 5G.
        (r.G5, F109, True),
        (r.G5, F112, True),
        (r.G5, "Fossibot F112 Pro Celular Robusto 4g 24 Gb+256 Gb, Tela 6,88 Hd+ 120hz", None),
        (r.G5, "Fossibot F112 Pro Celular Robusto 5g 24 Gb+256 Gb, Tela 6,88 Hd+ 120hz", True),
        (r.G5, F110_PRO, True),
        (r.G5, "Uranyx Fossibot F110 128 Gb Preto", None),
        (r.G5, A18, False),
        (r.G5, C68_512, False),
        (r.G5, C68_TODAS, False),
        (r.G5, F109S, False),
        (r.G5, A17_TODAS, False),
        (r.G5, F110L, False),
        (r.G5, F105, False),
        (r.G5, S7_NFC, False),
        (r.G5, S5, False),
        # Zíper expansivo.
        (r.ZIPER, P7, True),
        (
            r.ZIPER,
            "Mala Viagem Com Rodinhas 10kg 20 Polegadas De Bordo Polipropileno P7 - Rosa",
            True,
        ),
        (r.ZIPER, P8, True),
        (r.ZIPER, M4, False),
        # Sem veredito: a fala é uma só, há conflito ou nunca se falou.
        (r.G5, "Uranyx Oukitel Hyper 7s 128 Gb Preto", None),
        (r.G5, "Celular Fossibot F117 Dual Sim 256gb Rom 24gb Ram Bateria 10000mah", None),
        (r.NFC, "Celular Fossibot F117 Dual Sim 256gb Rom 24gb Ram Bateria 10000mah", None),
        (r.NFC, "Uranyx Hotwav T8 12 GB Memoria Ram 64 GB Memoria Interna NFC", None),
        (r.ZIPER, P5, None),
        (r.ZIPER, P6, None),
        (r.ZIPER, "Mala De Bordo 20 Polegadas 10kg + Mochila Executiva P2 - Cinza Escuro", None),
    ],
)
def test_a_tabela(recurso, titulo, esperado):
    assert r.o_que_a_equipe_diz(recurso, titulo) is esperado, sorted(r.codigos_do(titulo))


@pytest.mark.parametrize(
    "titulo",
    [
        None,
        "",
        "Smartphone Uranyx 128 Gb Memoria",
        F110_OS_DOIS,
        f"{F110_OS_DOIS} - F110 Pro 5G - 20.128 GB",
        "Uranyx F110 Pro 5G 20.128 ou F110 L 8.128 Gb",
        "Uranyx Fossibot F109 5G 24.256 ou F109S 24.256 Resistente",
        "Uranyx Oukitel WP53 Robusto NFC 128GB 24GB RAM - WP53S - 16.128 + Relogio",
        "Uranyx WP53 24.128 GB - Resistente NFC - WP53 S - 16.128 + Relogio",
        "Uranyx A18 Pro Max NFC 128 Gb Memoria 16 Gb Ram S5 Edition - C17 - Laranja,16 GB / 128 GB",
        "Uranyx A17 Pro Max 12.128 ou 12.64 GB Memoria / Uranyx S5 16.128 De Memoria NFC",
        "Uranyx Séries C 256 GB Memoria C2 C51 C58 Pro C61 Pro C65 Pro P1 Celular Dual Chip",
    ],
)
def test_sem_um_codigo_so_nao_ha_veredito(titulo):
    assert r.o_que_a_equipe_diz(r.NFC, titulo) is None
    assert r.o_que_a_equipe_diz(r.G5, titulo) is None
    assert not r.pode_oferecer(r.NFC, titulo)


@pytest.mark.parametrize(
    ("titulo", "esperado"),
    [
        ("Uranyx C2 16.128 - Roxo + Fone U9", False),
        ("Uranyx S7 32.256 - Cinza + Fone UFB10 + Relogio", False),
        ("Uranyx Wp60 + Fone Com Fio S10+ Preto", True),
        ("Uranyx Wp60 + Fone Sem Fio Visor U9 + Smart Watch Preto", True),
        ("Uranyx F112 Pro 5g Helio G99 256 Gb", True),
        ("Uranyx F112 Pro 5g G99 256 Gb", True),
        ("Uranyx C2 Unisoc T606 256 Gb", False),
        ("Uranyx A18 Pro Max NFC 16.128 Gb Memoria S5 Premium - A18 - Branco NFC", True),
        ("Capa...uranyx A18 Pro Max Nfc 16.128 Gb / S5 Edition Laranja", True),
        ("Smartphone Uranyx A18 Pro Max - S5 Edition 128 GB 16 GB RAM 4G Android 16", True),
    ],
)
def test_o_codigo_que_nao_e_modelo_nao_derruba_o_veredito(titulo, esperado):
    assert r.o_que_a_equipe_diz(r.NFC, titulo) is esperado


def test_o_s5_sozinho_continua_o_s5():
    assert r.codigos_do("Uranyx S5 Premium 16.128 NFC") == {"s5"}
    assert r.codigos_do("Uranyx S5 Edition 128 Gb") == {"s5"}


def test_a_faixa_da_linha_nao_e_modelo_e_a_variacao_diz_qual():
    titulo = "Mala De Viagem 20 Polegadas 10kg + Mochila Grande Polipropileno P1-P8"
    assert r.o_que_a_equipe_diz(r.ZIPER, titulo) is None
    assert r.o_que_a_equipe_diz(r.ZIPER, f"{titulo} - Rosa P5") is None
    assert r.o_que_a_equipe_diz(r.ZIPER, titulo, "Rosa P7") is True


@pytest.mark.parametrize(
    ("recurso", "titulo"),
    [
        # O P1 da mala não é o celular P1, nem o M4 do MacBook é a mala M4.
        (r.NFC, "Conjunto Mala De Despacho 24 Polegadas 23kg Com Frasqueira P1 Polipropileno"),
        (r.G5, "Conjunto Mala De Despacho 24 Polegadas 23kg Com Frasqueira P1 Polipropileno"),
        (r.ZIPER, "Apple Macbook Air M4 16.256 GB - Original - 1 Ano de Garantia"),
        # O Poco C65 não é o C65 Pro de que a equipe falou.
        (r.NFC, "Poco c65 6.128 - Preto"),
    ],
)
def test_o_mesmo_codigo_em_outro_produto_nao_tem_veredito(recurso, titulo):
    assert r.o_que_a_equipe_diz(recurso, titulo) is None


def test_a_variacao_diz_a_versao_que_o_titulo_nao_separa():
    assert r.o_que_a_equipe_diz(r.NFC, C68_TODAS, "Dourado,24 GB/512 GB") is True
    assert r.o_que_a_equipe_diz(r.NFC, C68_TODAS, "Cinza + Acessorios,16 GB / 128 GB") is False
    assert r.o_que_a_equipe_diz(r.NFC, C68_TODAS, "Rosa - 256 Gb / 24 Gb") is None
    # A variação que não diz a versão não resolve o título.
    assert r.o_que_a_equipe_diz(r.NFC, C68_TODAS, "Dourado") is None
    assert r.o_que_a_equipe_diz(r.NFC, A17_TODAS, "Prata,16 GB / 128 GB Premium NFC") is True
    assert r.o_que_a_equipe_diz(r.NFC, A17_TODAS, "Preto,12 GB / 64") is False
    assert r.o_que_a_equipe_diz(r.NFC, A17_TODAS, "Preto,12 GB / 128 GB") is False
    # A variação conta como código também: dois modelos, sem veredito.
    assert r.o_que_a_equipe_diz(r.NFC, F109, "F109S") is None


def test_o_a17_premium_tem_mas_nunca_e_oferecido():
    assert r.o_que_a_equipe_diz(r.NFC, A17_PREMIUM) is True
    assert not r.pode_oferecer(r.NFC, A17_PREMIUM)
    assert not r.pode_oferecer(r.NFC, A17_TODAS, "Prata,16 GB / 128 GB Premium NFC")
    assert r.pode_oferecer(r.NFC, A18)


def test_a_oferta_so_com_o_true_da_equipe():
    assert r.pode_oferecer(r.NFC, C68_512) and r.pode_oferecer(r.ZIPER, P7)
    for recurso, titulo in ((r.NFC, F105), (r.NFC, C68_256), (r.G5, A18), (r.ZIPER, P5)):
        assert not r.pode_oferecer(recurso, titulo)
    assert not r.pode_oferecer(r.ZIPER, "Mala De Viagem Bordo 20 Polegadas Expansível - Preto")
    assert not r.pode_oferecer("outro", A18)


def test_a_leitura_dos_outros_nao_mudou():
    assert checagens._modelos_do is r._modelos_do
    assert checagens._modelos_do(A18) == {"a18", "s5"}
    assert checagens._modelos_do("Uranyx Oscal Marine 1 128 Gb") == set()
    assert checagens._modelos_do("Mala P1-P8") == {"p1", "p8"}
    assert checagens._modelos_do("Uranyx C2 + Fone U9") == {"c2", "u9"}
    assert checagens._modelos_do("IP68 Wi-Fi 6 USB-C A17") == {"a17"}


# ═══════════════ as especificações ═══════════════


def _linha(resultado: dict, campo: str) -> dict:
    return next(c for c in resultado["campos"] if c["campo"] == campo)


def test_a_equipe_vence_o_titulo_e_a_nota_diz():
    # "Fossibot F105 … Nfc": o título diz sim, a equipe diz não.
    r_ = e.aplicar_a_equipe(_juntar(None, F105), F105)
    assert _linha(r_, "nfc") == {
        "campo": "nfc",
        "valor": e.NAO,
        "fonte": e.FONTE_EQUIPE,
        "nota": e.NOTA_EQUIPE,
    }
    assert _linha(r_, "rede_5g")["valor"] == e.NAO and _linha(r_, "rede_5g")["fonte"] == "equipe"


def test_o_anuncio_que_concorda_nao_ganha_nota():
    linha = _linha(e.aplicar_a_equipe(_juntar(None, S5), S5), "nfc")
    assert (linha["valor"], linha["fonte"]) == (e.SIM, e.FONTE_EQUIPE) and "nota" not in linha


def test_a_linha_entra_mesmo_sem_o_anuncio_dizer_e_na_ordem():
    titulo = "Uranyx Oscal Marine 1 128 Gb Memoria 12 Gb Ram Preto"
    r_ = e.aplicar_a_equipe(_juntar(None, titulo), titulo)
    assert (_linha(r_, "nfc")["valor"], _linha(r_, "nfc")["fonte"]) == (e.SIM, e.FONTE_EQUIPE)
    assert "nota" not in _linha(r_, "nfc")
    ordem = [c["campo"] for c in r_["campos"]]
    assert ordem == sorted(ordem, key=e.CAMPOS.index)
    # O Marine 1 não tem 5G, e a linha entra mesmo sem o anúncio dizer.
    assert _linha(r_, "rede_5g")["valor"] == e.NAO


def test_o_conflito_no_anuncio_e_entre_fontes_saem_so_daquele_campo():
    ficha_ = {"titulo": F105, "atributos": ["NFC: Não", "Android: 14"], "descricao": "Android 15"}
    antes = _juntar(ficha_, F105)
    assert antes["conflito_no_anuncio"] is True and _linha(antes, "nfc")["conflito_no_anuncio"]
    depois = e.aplicar_a_equipe(antes, F105)
    assert "conflito_no_anuncio" not in _linha(depois, "nfc")
    assert _linha(depois, "nfc")["nota"] == e.NOTA_EQUIPE
    # Outro campo que se contradiz continua contradizendo.
    assert depois["conflito_no_anuncio"] is bool(
        [c for c in antes["campos"] if c["campo"] != "nfc" and c.get("conflito_no_anuncio")]
    )

    resultado = {
        "campos": [{"campo": "nfc", "valor": e.NAO, "fonte": e.FONTE_ANUNCIO}],
        "conflitos": [
            {"campo": "nfc", "valor": e.NAO, "fonte": "anuncio", "outro_valor": "sim"},
            {"campo": "bateria", "valor": "1", "fonte": "anuncio", "outro_valor": "2"},
        ],
        "conflito_no_anuncio": False,
    }
    limpo = e.aplicar_a_equipe(resultado, S5)
    assert [c["campo"] for c in limpo["conflitos"]] == ["bateria"]
    assert _linha(limpo, "nfc")["nota"] == e.NOTA_EQUIPE


def test_o_conflito_de_outro_campo_fica():
    resultado = {
        "campos": [
            {"campo": "nfc", "valor": "sim / não", "fonte": "anuncio", "conflito_no_anuncio": True},
            {"campo": "tela", "valor": "6 / 7", "fonte": "anuncio", "conflito_no_anuncio": True},
        ],
        "conflitos": [],
        "conflito_no_anuncio": True,
    }
    limpo = e.aplicar_a_equipe(resultado, F105)
    assert limpo["conflito_no_anuncio"] is True and _linha(limpo, "tela")["conflito_no_anuncio"]
    assert "conflito_no_anuncio" not in _linha(limpo, "nfc")


@pytest.mark.parametrize("titulo", [F110_OS_DOIS, "Uranyx Oukitel Hyper 7s 128 Gb Nfc", None])
def test_sem_veredito_nada_muda(titulo):
    antes = _juntar(None, titulo)
    assert e.aplicar_a_equipe(antes, titulo) == antes


async def _produtos(db, make_user, *nomes: str) -> list[Any]:
    user = await make_user()
    produtos = [
        Product(user_id=user.id, sku=f"sku-{i}", name=n, stock=1) for i, n in enumerate(nomes)
    ]
    db.add_all(produtos)
    await db.flush()
    return [p.id for p in produtos]


async def test_no_banco_o_modelo_so_tem_a_palavra_da_equipe(db, make_user):
    ids = await _produtos(db, make_user, "Uranyx F105 12.64 - Preto")
    r_ = await e.especificacoes_do_produto(db, None, ids, F105)
    assert _linha(r_, "nfc")["valor"] == e.NAO and _linha(r_, "nfc")["fonte"] == e.FONTE_EQUIPE
    assert r_["conflito_no_anuncio"] is False


async def test_no_banco_o_anuncio_de_2_modelos_fica_como_estava(db, make_user):
    # O título tem um código só, mas o anúncio liga dois modelos: o de sempre (varia_por_modelo).
    ids = await _produtos(db, make_user, "Uranyx F105 12.64 - Preto", "Uranyx S5 16.128 - Prata")
    r_ = await e.especificacoes_do_produto(db, None, ids, F105)
    assert _linha(r_, "nfc")["fonte"] == e.FONTE_ANUNCIO and _linha(r_, "nfc")["varia_por_modelo"]


async def test_no_banco_a_variacao_selecionada_diz_a_versao(db, make_user):
    ids = await _produtos(db, make_user, "Uranyx C68 Plus 24.512 - Dourado")
    variacoes = [
        {"nome": "Cinza,16 GB / 128 GB", "disponivel": True},
        {"nome": "Dourado,24 GB/512 GB", "disponivel": True, "selecionada": True},
    ]
    r_ = await e.especificacoes_do_produto(db, {"variacoes": variacoes}, ids, C68_TODAS)
    assert _linha(r_, "nfc")["valor"] == e.SIM and _linha(r_, "nfc")["fonte"] == e.FONTE_EQUIPE
    r_ = await e.especificacoes_do_produto(db, {"variacoes": variacoes[:1]}, ids, C68_TODAS)
    assert not [c for c in r_["campos"] if c["fonte"] == e.FONTE_EQUIPE and c["campo"] == "nfc"]


# ═══════════════ o que barra e o que passa ═══════════════


def _ficha_com_espec(titulo: str, variacao: str | None = None) -> dict:
    espec = e.aplicar_a_equipe(_juntar(None, titulo), titulo, variacao)
    return {"item_id": "1", "titulo": titulo, "especificacoes": espec}


@pytest.mark.parametrize(
    ("titulo", "texto", "barra"),
    [
        (F105, "Sim, este aparelho tem NFC.", True),
        (F105, "Este aparelho não tem NFC.", False),
        (S5, "Este aparelho não tem NFC.", True),
        (S5, "Tem NFC sim.", False),
        (C68_128, "Este aparelho tem NFC.", True),
        (C68_512, "Este aparelho não tem NFC.", True),
        (F110_PRO, "Não pega 5G.", True),
        (F110L, "Este tem 5G.", True),
        (A18, "Este tem 5G.", True),
        (A18, "Este não tem 5G.", False),
        # A negativa que o campo não lê, e a negativa com o recurso citado em outra oração.
        (S5, "No anúncio não consta NFC.", True),
        (S5, "Não tem NFC. O NFC serve para pagar por aproximação.", True),
    ],
)
def test_a_ficha_barra_contra_a_equipe(titulo, texto, barra):
    pendencias = checagens.conferir_fonte_produto(texto, [_ficha_com_espec(titulo)], None)
    assert bool([p for p in pendencias if p.codigo == checagens.CONTRA_A_EQUIPE]) is barra, (
        pendencias
    )
    # O portão do automático deixa passar o que não é duro: esta é dura.
    assert any(checagens.pendencia_dura(p) for p in pendencias) is barra


def test_o_texto_que_diz_o_que_a_equipe_diz_e_cita_o_recurso_nao_a_contradiz():
    # A frase da oferta e a menção em outra oração: fica o aviso de sempre, não a trava dura.
    for texto in (
        "O F105 não tem NFC. Quanto ao NFC, temos o Uranyx A18 Pro Max, que tem esse recurso.",
        "Não tem NFC. A descrição do anúncio fala de NFC só no título.",
    ):
        pendencias = checagens.conferir_fonte_produto(texto, [_ficha_com_espec(F105)], None)
        assert not [p for p in pendencias if checagens.pendencia_dura(p)], pendencias


def test_o_anuncio_de_2_modelos_nao_tem_a_trava_da_negativa():
    dado = {
        "item_id": "1",
        "titulo": S5,
        "especificacoes": {
            "campos": [
                {"campo": "nfc", "valor": "sim", "fonte": "anuncio", "varia_por_modelo": True}
            ],
            "conflitos": [],
        },
    }
    pendencias = checagens.conferir_fonte_produto("Este não tem NFC.", [dado], None)
    assert not [p for p in pendencias if p.codigo == checagens.CONTRA_A_EQUIPE]


def test_o_motivo_diz_que_e_a_equipe_e_nao_o_anuncio():
    (p,) = checagens.conferir_fonte_produto(
        "Sim, este aparelho tem NFC.", [_ficha_com_espec(F105)], None
    )
    assert "a equipe da loja diz" in p.motivo and "anúncio" not in p.motivo


def test_o_ziper_da_mala_que_a_equipe_confirma_e_barrado():
    dado = {"item_id": "1", "titulo": P7}
    pendencias = checagens.conferir_fonte_produto(
        "Esta mala não tem zíper expansivo.", [dado], None
    )
    assert [p.codigo for p in pendencias] == [checagens.CONTRA_A_EQUIPE]
    # O da P5 a equipe não confirma; o de outra mala na conversa pode ser dela.
    for dados in ([{"item_id": "1", "titulo": P5}], [dado, {"item_id": "2", "titulo": P5}]):
        assert not checagens.conferir_fonte_produto(
            "Esta mala não tem zíper expansivo.", dados, None
        )


def test_a_liberacao_pelo_anuncio_ve_a_palavra_da_equipe():
    saida = checagens._so_do_anuncio(_ficha_com_espec(F105))
    linha = next(c for c in saida["especificacoes"]["campos"] if c["campo"] == "nfc")
    assert linha["fonte"] == e.FONTE_EQUIPE and linha["valor"] == e.NAO


# ═══════════════ quem aciona a oferta ═══════════════


@pytest.mark.parametrize(
    ("titulo", "texto", "fala", "recurso"),
    [
        (S5, "Este aparelho não tem NFC.", "esse tem nfc?", None),
        (A18, "Este aparelho não tem NFC.", "esse tem nfc?", None),
        (F112, "Este aparelho não tem 5G.", "esse tem 5g?", None),
        (P7, "Esta mala não tem zíper expansivo.", "tem ziper expansivo?", None),
        (F105, "Este aparelho não tem NFC.", "esse tem nfc?", NFC),
        (C68_128, "Este aparelho não tem NFC.", "esse tem nfc?", NFC),
        (A18, "Este aparelho não tem 5G.", "esse tem 5g?", G5),
        (M4, "Esta mala não tem zíper expansivo.", "tem ziper expansivo?", ZIPER),
        # Sem veredito: a de hoje, o "não" do modelo ainda aciona.
        (P5, "Esta mala não tem zíper expansivo.", "tem ziper expansivo?", ZIPER),
    ],
)
def test_o_modelo_que_a_equipe_diz_que_tem_nao_gera_oferta(titulo, texto, fala, recurso):
    assunto = "espec_mala" if recurso is ZIPER or titulo == P7 else "espec_celular"
    alvo = _alvo(_p(texto, assunto=assunto), falas=(fala,), titulos=(titulo,))
    assert (alvo[0] if alvo else None) is recurso


# ═══════════════ quem é oferecido ═══════════════


@pytest.mark.parametrize(
    ("recurso", "titulo", "tipo", "entra"),
    [
        (NFC, A18, "celular", True),
        (NFC, S5, "celular", True),
        (NFC, "Uranyx F112 Pro 5g NFC 256 Gb Memoria 24gb Resistente", "celular", True),
        (NFC, F105, "celular", False),
        (NFC, S7_NFC, "celular", False),
        (NFC, C68_128, "celular", False),
        (NFC, "Uranyx Oukitel C68 Plus 512 Gb Memoria NFC 24 Gb Ram Dourado", "celular", True),
        (NFC, "Uranyx Oukitel C68 Plus 256 Gb Memoria NFC 24 Gb Ram Rosa", "celular", False),
        (NFC, A17_TODAS, "celular", False),
        (NFC, "Uranyx Hotwav A17 Pro Max 128 Gb Memoria 16Gb NFC (Branco)", "celular", False),
        (NFC, "Celular Fossibot F117 NFC 256gb Rom 24gb Ram", "celular", False),
        (G5, F112, "celular", True),
        (G5, F109, "celular", True),
        (G5, F110_PRO, "celular", True),
        (G5, "Uranyx C6 5g 256 Gb Memoria 24 Gb Ram Preto", "celular", False),
        (G5, "Uranyx Hyper 7s 5G 128 Gb Memoria", "celular", False),
        (ZIPER, "Mala De Viagem Bordo 20 Polegadas Expansível P7 - Preto", "mala", True),
        (
            ZIPER,
            "Conjunto Mala Polipropileno Fechadura Tsa Expansível P8 - Champanhe",
            "mala",
            True,
        ),
        (ZIPER, "Mala De Viagem Bordo 20 Polegadas Expansível P5 - Preto", "mala", False),
        (ZIPER, "Mala De Viagem Bordo 20 Polegadas Expansível P6 - Preto", "mala", False),
        (ZIPER, "Mala De Viagem Bordo 20 Polegadas Expansível M4 - Preto", "mala", False),
        (ZIPER, "Mala De Viagem Bordo 20 Polegadas Expansível - Preto", "mala", False),
    ],
)
def test_quem_e_oferecido(recurso, titulo, tipo, entra):
    assert bool(_achados(_achado("9", titulo), recurso=recurso, tipo=tipo)) is entra


def test_a_variacao_casada_escolhe_a_versao_que_tem():
    titulo = "Uranyx C68 Plus 16.128 / 24.256 / 24.512 GB Memoria"
    achado = _achado("9", titulo, variacoes=("Cinza,24 GB / 512 GB NFC",))
    assert _achados(achado)
    assert checagens.ficha_confirma_o_recurso(NFC, _dado(titulo), achado) is True
    sem_ela = _achado("9", titulo, variacoes=("Cinza,16 GB / 128 GB NFC",))
    assert not _achados(sem_ela)
    assert checagens.ficha_confirma_o_recurso(NFC, _dado(titulo), sem_ela) is None


def test_o_titulo_real_do_a18_com_o_s5_de_venda_confirma_o_anuncio_inteiro():
    assert checagens.ficha_confirma_o_recurso(NFC, _dado(A18), _achado("9", A18)) is False
    # E o A18 com o C17 nas variações continua de fora.
    dado = _dado(A18, variacoes=_variacoes(("A18 - Branco NFC", True), ("C17 - Azul", True)))
    assert checagens.ficha_confirma_o_recurso(NFC, dado, _achado("9", A18)) is None


def test_a_ficha_que_nega_continua_tirando_a_oferta_mesmo_com_a_equipe():
    assert (
        checagens.ficha_confirma_o_recurso(NFC, _dado(S5, descricao="Sem NFC."), _achado()) is None
    )


# ═══════════════ de ponta a ponta ═══════════════


async def _conversa_do(db, make_user, monkeypatch, titulo: str, descricao: str, **kw: Any) -> Any:
    conversa, integ, r_mem = await _nfc(db, make_user, monkeypatch, **kw)
    conversa.anuncio_titulo = titulo
    conversa.anuncio_id = ITEM_S7
    await _link(db, integ, ITEM_S7, "1", titulo)
    _ficha(r_mem, integ, ITEM_S7, titulo, descricao)
    await db.commit()
    return conversa, integ, r_mem


async def test_e2e_f105_nao_tem_e_recebe_a_oferta_do_a18(
    db, make_user, agente_ligado, api, envios, monkeypatch
):
    conversa, _integ, _r = await _conversa_do(
        db, make_user, monkeypatch, F105, "Celular com 2 chips."
    )
    _modelo(api, texto="Este aparelho não tem NFC.", item=ITEM_S7)

    rascunho = await ia_svc.gerar_rascunho(db, conversa)

    assert rascunho.texto.startswith(
        "Este aparelho não tem NFC. Temos o Uranyx A18 Pro Max, que tem esse recurso: "
    ), (rascunho.motivo, rascunho.validador_erros, rascunho.texto)
    assert _do_agente(rascunho)["recurso_oferecido"] == {"recurso": "nfc", "anuncios": [ITEM_A18]}
    assert [m["texto"] for m in envios] == [rascunho.texto]
    assert _decisao(rascunho) == "enviado", rascunho.motivo
    assert _do_agente(rascunho)["produto_pelo_anuncio"] is True
    # A ficha do pacote já leva a palavra da equipe, e o verificador a vê.
    assert e.NOTA_EQUIPE in str(api.pedidos[0])
    assert e.NOTA_EQUIPE in api.verificador[0]["messages"][0]["content"]


async def test_e2e_f105_tem_nfc_e_barrado(db, make_user, agente_ligado, api, envios, monkeypatch):
    conversa, _integ, _r = await _conversa_do(
        db, make_user, monkeypatch, F105, "Celular com 2 chips."
    )
    _modelo(api, texto="Sim, este aparelho tem NFC.", item=ITEM_S7)

    rascunho = await ia_svc.gerar_rascunho(db, conversa)

    assert envios == [], rascunho.motivo
    assert "recurso_oferecido" not in _do_agente(rascunho)


async def test_e2e_s5_nao_tem_e_barrado_e_nao_ganha_oferta(
    db, make_user, agente_ligado, api, envios, monkeypatch
):
    conversa, _integ, _r = await _conversa_do(
        db, make_user, monkeypatch, S5, "Celular com 2 chips."
    )
    _modelo(api, texto="Este aparelho não tem NFC.", item=ITEM_S7)

    rascunho = await ia_svc.gerar_rascunho(db, conversa)

    assert envios == [], rascunho.motivo
    agente_ = _do_agente(rascunho)
    assert "recurso_oferecido" not in agente_ and "recurso_sem_oferta" not in agente_
    assert "esse recurso" not in (rascunho.texto or "")


async def test_e2e_s5_tem_nfc_passa_mesmo_com_o_anuncio_calado(
    db, make_user, agente_ligado, api, envios, monkeypatch
):
    titulo = "Uranyx S5 128 Gb Memoria 16 Gb Ram Prata"
    conversa, _integ, _r = await _conversa_do(
        db, make_user, monkeypatch, titulo, "Celular com 2 chips."
    )
    _modelo(api, texto="Sim, este aparelho tem NFC.", item=ITEM_S7)

    rascunho = await ia_svc.gerar_rascunho(db, conversa)

    assert [m["texto"] for m in envios] == [rascunho.texto], rascunho.motivo
    assert _decisao(rascunho) == "enviado", rascunho.motivo


async def _mala(db, make_user, monkeypatch, *candidatas: str) -> Any:
    conversa, integ, r_mem = await _nfc(
        db, make_user, monkeypatch, pergunta="Essa mala tem zíper expansivo?"
    )
    conversa.anuncio_id = "55500000001"
    conversa.anuncio_titulo = "Mala De Bordo 20 Polegadas 10kg ABS - Preto"
    await _link(db, integ, "55500000001", "1", conversa.anuncio_titulo)
    _ficha(r_mem, integ, "55500000001", conversa.anuncio_titulo, "Mala de bordo rígida.")
    for i, titulo in enumerate(candidatas):
        item = f"5550000001{i}"
        await _link(db, integ, item, "1", titulo)
        _ficha(r_mem, integ, item, titulo, "Mala com zíper expansível.")
    await db.commit()
    return conversa


async def test_e2e_p5_nunca_e_oferecida(db, make_user, agente_ligado, api, envios, monkeypatch):
    conversa = await _mala(
        db,
        make_user,
        monkeypatch,
        "Mala De Viagem Bordo 20 Polegadas Expansível P5 - Preto",
        "Mala De Viagem Bordo 20 Polegadas Expansível - Azul",
    )
    _modelo(
        api, texto="Esta mala não tem zíper expansivo.", item="55500000001", assunto="espec_mala"
    )

    rascunho = await ia_svc.gerar_rascunho(db, conversa)

    assert rascunho.texto == "Esta mala não tem zíper expansivo.", rascunho.motivo
    assert _do_agente(rascunho)["recurso_sem_oferta"]["porque"] == "sem_anuncio"


async def test_e2e_a_conversa_da_p7_nao_tem_e_barrado_e_nao_ganha_oferta(
    db, make_user, agente_ligado, api, envios, monkeypatch
):
    conversa, integ, r_mem = await _nfc(
        db, make_user, monkeypatch, pergunta="Essa mala tem zíper expansivo?"
    )
    titulo = "Mala Viagem Com Rodinhas 10kg 20 Polegadas De Bordo Polipropileno P7 - Rosa Onda"
    conversa.anuncio_id = "55500000001"
    conversa.anuncio_titulo = titulo
    await _link(db, integ, "55500000001", "1", titulo)
    _ficha(r_mem, integ, "55500000001", titulo, "Mala de bordo.")
    outra = "Mala De Viagem Bordo 20 Polegadas Expansível P8 - Preto"
    await _link(db, integ, "55500000002", "1", outra)
    _ficha(r_mem, integ, "55500000002", outra, "Mala com zíper expansível.")
    await db.commit()
    _modelo(
        api, texto="Esta mala não tem zíper expansivo.", item="55500000001", assunto="espec_mala"
    )

    rascunho = await ia_svc.gerar_rascunho(db, conversa)

    assert envios == [], rascunho.motivo
    assert "recurso_oferecido" not in _do_agente(rascunho)


async def test_e2e_o_c68_de_128_recebe_a_oferta_do_outro_anuncio_e_nao_a_do_mesmo_modelo(
    db, make_user, agente_ligado, api, envios, monkeypatch
):
    conversa, integ, r_mem = await _conversa_do(
        db, make_user, monkeypatch, "Uranyx Oukitel C68 Plus 128 Gb Memoria 16 Gb Ram", "Celular."
    )
    await db.execute(ProductLink.__table__.delete().where(ProductLink.external_id == ITEM_A18))
    titulo = "Uranyx Oukitel C68 Plus 512 Gb Memoria NFC 24 Gb Ram"
    await _link(db, integ, ITEM_VARIOS, "1", titulo)
    _ficha(r_mem, integ, ITEM_VARIOS, titulo, "Celular com NFC.")
    outro = "Uranyx S5 128 Gb Memoria 16 Gb Ram Nfc Prata"
    await _link(db, integ, "44400000600", "1", outro)
    _ficha(r_mem, integ, "44400000600", outro, "Celular com NFC.")
    await db.commit()
    _modelo(api, texto="Este aparelho não tem NFC.", item=ITEM_S7)

    rascunho = await ia_svc.gerar_rascunho(db, conversa)

    assert _do_agente(rascunho)["recurso_oferecido"]["anuncios"] == ["44400000600"], (
        rascunho.motivo,
        rascunho.texto,
    )


# ── A resposta contra a equipe, oração por oração (a revisão de 05/11/2026) ───

F105_NFC = "Uranyx Fossibot F105 128 Gb Memoria 8 Gb Ram Nfc Preto"
S7_NFC = "Uranyx Fossibot S7 256 Gb Memoria 12 Gb Ram Nfc Preto"
F112_5G = "Fossibot F112 Pro Celular Robusto 5g 24 Gb+256 Gb Preto"
HYPER = "Uranyx Hyper 7s 256 Gb Memoria 16 Gb Ram 5g"
M4 = "Mala De Bordo M4 Preta 20 Pol"
NFC_Q = ["Esse tem NFC?"]
PAGA_Q = ["Paga por aproximação?"]
G5_Q = ["Tem 5G?"]
ZIPER_Q = ["Essa mala tem zíper expansivo?"]


def _barra(titulo: str, texto: str, falas: Any = ()) -> bool:
    pendencias = checagens.conferir_fonte_produto(
        texto, [_ficha_com_espec(titulo)], None, textos_cliente=falas
    )
    return any(p.codigo == checagens.CONTRA_A_EQUIPE for p in pendencias)


@pytest.mark.parametrize(
    ("titulo", "texto", "falas", "barra"),
    [
        # A resposta curta: o recurso vem da pergunta.
        (F105_NFC, "Sim, tem.", NFC_Q, True),
        (F105_NFC, "Sim, ele tem sim!", NFC_Q, True),
        (F105_NFC, "Sim, este aparelho tem esse recurso.", NFC_Q, True),
        (S5, "Não tem.", NFC_Q, True),
        (S5, "Não, este modelo não possui esse recurso.", NFC_Q, True),
        (S5, "Infelizmente não.", NFC_Q, True),
        (S5, "Sim, tem.", NFC_Q, False),
        (S5, "Tem sim.", NFC_Q, False),
        (F105_NFC, "Não tem.", NFC_Q, False),
        (F105_NFC, "Não, este modelo não possui esse recurso.", NFC_Q, False),
        (A18, "Sim, tem.", ["Tem NFC e 5G?"], True),
        (A18, "Tem NFC sim.", ["Tem NFC e 5G?"], False),
        (P7, "Não tem.", ZIPER_Q, True),
        (P7, "Sim, tem.", ZIPER_Q, False),
        (M4, "Sim, tem.", ZIPER_Q, True),
        # O que não é a resposta curta sobre o recurso.
        (F105_NFC, "Não tem problema, posso ajudar.", NFC_Q, False),
        (F105_NFC, "O prazo é de 3 dias.", NFC_Q, False),
        (F105_NFC, "Sim, chega amanhã.", ["Chega amanhã?"], False),
        (S5, "Sim", ["Esse não tem NFC?"], False),
        (F105_NFC, "Sim, não tem.", ["Esse não tem NFC?"], False),
        # Sem veredito não muda nada.
        (HYPER, "Sim, tem.", G5_Q, False),
        (HYPER, "Não tem.", G5_Q, False),
        (HYPER, "Este aparelho é 4G.", (), False),
        # De quem a oração fala: o modelo citado, depois a pergunta, depois a conversa.
        (S7_NFC, "O S7 não tem NFC, mas o Fossibot F105 tem NFC.", NFC_Q, True),
        (A18, "Tem NFC sim. O Fossibot F105 também tem NFC.", NFC_Q, True),
        (A18, "Sim, o F105 tem NFC.", ["E o F105, tem NFC?"], True),
        (A18, "Sim, tem.", ["E o F105, tem NFC?"], True),
        (A18, "O A18 tem NFC e o Fossibot S7 também tem NFC.", NFC_Q, True),
        (S7_NFC, "O S5 não tem NFC.", ["E o S5 tem NFC?"], True),
        (S7_NFC, "Não tem NFC.", ["E o S5 tem NFC?"], True),
        (S7_NFC, "O S7 não tem NFC, mas o Uranyx A18 tem NFC.", NFC_Q, False),
        (F105_NFC, "Temos o Fossibot F105, que tem NFC.", NFC_Q, True),
        (P7, "A M4 tem zíper expansivo.", (), True),
        (P7, "A P8 não tem zíper expansivo.", (), True),
        # A afirmação que outra oração não salva.
        (F105_NFC, "Este tem NFC sim, só o modelo antigo que não tem NFC.", NFC_Q, True),
        (S7_NFC, "O S7 tem NFC. Já o F105 não tem NFC.", NFC_Q, True),
        # A negativa de 5G dita como 4G.
        (F112_5G, "Este aparelho é 4G.", G5_Q, True),
        (F112_5G, "Ele funciona só em rede 4G.", G5_Q, True),
        (F109, "Só funciona em 4G.", (), True),
        (F109, "A rede é 4G.", (), True),
        (F109, "É só 4G.", (), True),
        (F109, "Este aparelho é 4G e 5G.", (), False),
        (F109, "Não é só 4G, tem 5G também.", (), False),
        (F105_NFC, "Este aparelho é 4G.", (), False),
        # Pagamento por aproximação é NFC, e explicar o NFC não é afirmar.
        (F105_NFC, "Sim, este aparelho faz pagamento por aproximação.", PAGA_Q, True),
        (F105_NFC, "Sim, tem.", PAGA_Q, True),
        (S5, "Este aparelho não faz pagamento por aproximação.", PAGA_Q, True),
        (S5, "Sim, faz pagamento por aproximação.", PAGA_Q, False),
        (F105_NFC, "Não faz pagamento por aproximação.", PAGA_Q, False),
        (F105_NFC, "Não tem NFC, que é o pagamento por aproximação.", NFC_Q, False),
        # A oferta e o que aponta outro modelo.
        (
            F105_NFC,
            "Este aparelho não tem NFC. Temos o Uranyx A18 Pro Max, que tem esse recurso: x",
            NFC_Q,
            False,
        ),
        (F105_NFC, "Este aparelho não tem NFC, mas temos outro modelo que tem NFC.", NFC_Q, False),
    ],
)
def test_a_resposta_contra_a_equipe(titulo, texto, falas, barra):
    assert _barra(titulo, texto, falas) is barra


def test_sem_modelo_na_oracao_fala_do_modelo_da_conversa():
    s5, s7 = _ficha_com_espec(S5), _ficha_com_espec(S7_NFC)
    assert checagens._contra_a_equipe("Não tem NFC.", [s5], (), (), [])
    # Com 2 modelos de vereditos diferentes na conversa, não se sabe de quem fala: fica como hoje.
    assert not checagens._contra_a_equipe("Não tem NFC.", [s5, s7], (), (), [])
    # O item do pedido é da conversa; o que não tem veredito não muda nada.
    assert not checagens._contra_a_equipe("Não tem NFC.", [s5], [S7_NFC], (), [])
    assert checagens._contra_a_equipe("Não tem NFC.", [s5], ["Película de vidro"], (), [])


@pytest.mark.parametrize(
    "titulo",
    [
        "Uranyx Celulares Rugged Resistente Água e Quedas 64GB 128GB 256GB 4G e 5G Smartphones"
        " - F109 S,256 GB",
        "Uranyx Fossibot F109 S 256 Gb",
        "Uranyx F109-S 256 Gb",
    ],
)
def test_o_f109_s_e_o_f109s(titulo):
    assert r.codigos_do(titulo) == {"f109s"}
    # NFC do F109S: a equipe se contradiz, sem veredito. O importante é não ler como F109.
    assert r.o_que_a_equipe_diz(r.NFC, titulo) is None
    assert r.o_que_a_equipe_diz(r.G5, titulo) is False
    assert r.pode_oferecer(r.G5, titulo) is False


def test_o_prompt_diz_que_a_equipe_vale_mais_e_nao_e_citada():
    from app.services.atendimento.agente import prompt

    assert 'fonte "equipe"' in prompt._PRODUTO
    assert "Fora a equipe, em conflito vale o anúncio" in prompt._PRODUTO
    assert "Nunca cite o site nem a equipe ao cliente" in prompt._PRODUTO


@pytest.mark.parametrize(
    ("titulo", "pergunta", "texto"),
    [
        (F105_NFC, "Esse tem NFC?", "Sim, tem."),
        (F105_NFC, "Esse tem NFC?", "Sim, este aparelho tem esse recurso."),
        (S5, "Esse tem NFC?", "Não tem."),
        (S5, "Esse tem NFC?", "Não, este modelo não possui esse recurso."),
        (F112_5G, "Esse tem 5G?", "Este aparelho é 4G."),
        (F105_NFC, "Paga por aproximação?", "Sim, este aparelho faz pagamento por aproximação."),
        (S5, "Paga por aproximação?", "Este aparelho não faz pagamento por aproximação."),
        (F105_NFC, "Esse tem NFC?", "Este tem NFC sim, só o modelo antigo que não tem NFC."),
        (A18, "E o F105, tem NFC?", "Sim, tem."),
        (S7_NFC, "E o S5 tem NFC?", "O S5 não tem NFC."),
    ],
)
async def test_e2e_a_resposta_contra_a_equipe_nao_sai(
    db, make_user, agente_ligado, api, envios, monkeypatch, titulo, pergunta, texto
):
    conversa, _integ, _r = await _conversa_do(
        db, make_user, monkeypatch, titulo, "Celular com 2 chips.", pergunta=pergunta
    )
    _modelo(api, texto=texto, item=ITEM_S7)

    rascunho = await ia_svc.gerar_rascunho(db, conversa)

    assert envios == [], (rascunho.motivo, rascunho.validador_erros)
    assert _decisao(rascunho) != "enviado"
    assert "recurso_oferecido" not in _do_agente(rascunho)


# ── A checagem final da tabela (05/11/2026) ─────────────────────────────────


@pytest.mark.parametrize(
    "recurso,titulo",
    [
        (r.NFC, "Fossibot F109s Celular Robusto 4g 24 Gb+256 Gb, Bateria 10600mah, Nfc"),
        (r.NFC, "Uranyx Fossibot F110L 8.128 Gb"),
        (r.NFC, "Caap ** Uranyx Wp53 Pro 256 Gb Memoria 24 Gb Ram Preto"),
        (r.NFC, "Uranyx Series C com NFC - P1"),
        (r.G5, "Fossibot F112 Pro Celular Robusto 4g 24 Gb+256 Gb"),
        (r.G5, "Uranyx F109 4G 24.256"),
    ],
)
def test_sem_veredito_depois_da_checagem(recurso: str, titulo: str) -> None:
    assert r.o_que_a_equipe_diz(recurso, titulo) is None
    assert r.pode_oferecer(recurso, titulo) is False


def test_os_que_ficaram_e_o_marine_sem_5g() -> None:
    assert r.o_que_a_equipe_diz(r.G5, "Oukitel Marine 1 128 Gb") is False
    assert r.o_que_a_equipe_diz(r.NFC, "Oukitel Marine 1 128 Gb") is True
    assert r.o_que_a_equipe_diz(r.NFC, "Uranyx P1 Pro 256 Gb") is True
    assert r.o_que_a_equipe_diz(r.G5, "Fossibot F112 Pro 5g 24 Gb+256 Gb") is True
    assert r.o_que_a_equipe_diz(r.G5, "Uranyx F109 5G 24.256") is True


def test_a_mala_do_ml_vai_com_o_codigo_no_nome() -> None:
    from app.services.atendimento.agente import catalogo

    titulo = "Kit Mala Bordo 20 Pol 10kg + Frasqueira Tsa Pp Rosa P7 (expansível/roda Removível)"
    achado = {
        "item_id": "MLB1",
        "nome_curto": "Kit Mala Bordo 20 Pol",
        "titulo_plano": catalogo.plano(titulo),
        "disponivel": True,
    }
    recurso = next(x for x in checagens.RECURSOS_DA_LOJA if x.chave == r.ZIPER)
    assert checagens.modelos_do_nome(achado) == ["p7"]
    assert checagens.nome_para_a_oferta(achado) == "Kit Mala Bordo 20 Pol P7"
    premium = {
        "disponivel": True,
        "titulo_plano": catalogo.plano("Kit Mala Bordo 20 Pol Tsa Preto P7 (premium)"),
    }
    assert checagens.nome_acha_so_quem_tem(recurso, achado, [{"anuncios": [achado, premium]}])
    outra = {
        "disponivel": True,
        "titulo_plano": catalogo.plano("Kit Mala Bordo 20 Pol e Mochila P7 P5"),
    }
    assert not checagens.nome_acha_so_quem_tem(recurso, achado, [{"anuncios": [achado, outra]}])


def test_nenhum_nome_de_checagens_sobrescreve_outro():
    """Em 05/11/2026 um `_SO_CORTESIA` novo apagou o antigo e mudou a leitura de cortesia."""
    import ast
    import collections
    import pathlib

    arvore = ast.parse(pathlib.Path(checagens.__file__).read_text())
    vezes: collections.Counter[str] = collections.Counter()
    for no in arvore.body:
        if isinstance(no, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            vezes[no.name] += 1
        elif isinstance(no, ast.Assign):
            vezes.update(a.id for a in no.targets if isinstance(a, ast.Name))
    # Os dois que já se repetiam: `DURAS` cresce por módulo e `_FALA_DE_CANCELAR` é antigo.
    assert {n for n, q in vezes.items() if q > 1} == {"DURAS", "_FALA_DE_CANCELAR"}
