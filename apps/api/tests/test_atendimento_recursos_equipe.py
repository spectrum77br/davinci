# ruff: noqa: F811 — fixtures importadas dos testes de ponta a ponta
"""O que a equipe diz vale (o dono, 05/11/2026): "confie no q a eqpe diz sempre".

NFC, 5G e zíper expansivo; e o que vem na caixa e os fatos das malas (06/11/2026). Os títulos dos
testes são os de `product_links` em produção (lidos em 05/11 e 06/11/2026, só leitura).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.models import Product, ProductLink
from app.models.enums import IntegrationPlatform
from app.services.atendimento import especificacoes as e
from app.services.atendimento import ia as ia_svc
from app.services.atendimento import recursos_equipe as r
from app.services.atendimento.agente import checagens, verificador
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
from tests.test_atendimento_agente_e2e import _final as _final_e2e
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
M2_18 = "Mala De Bordo 18 Polegadas 10kg M2 (Preto)"
M6_24 = "Mala De Viagem Despacho 24 Polegadas 23kg Abs Rodas 360 Tsa Verde-escuro M6"


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
        (r.ZIPER, "Mala De Bordo 20 Polegadas 10kg + Mochila Executiva P2 - Cinza Escuro", False),
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
        (
            "Mala De Bordo 20 Pol 10kg Polipropileno Tsa Rodas 360 Preto P2",
            "Esta mala não tem zíper expansivo.",
            "tem ziper expansivo?",
            ZIPER,
        ),
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


# ═══════════════ a caixa e a mala (06/11/2026): a tabela ═══════════════

# Os dois exemplos reais da medição de 05/11 no ML: a equipe respondeu sim.
A17_ML = "Cappa ** Uranyx Hotwav A17 Pro Max 128 Gb Memoria 12 Gb Ram - Laranja - 12.128"
S3_PRO_ML = "Uranyx Fossibot S3 Pro Tela Curva 20 Gb Ram 128 Gb Preto"
A17_SHOPEE = (
    "Uranyx A17 Pro Max 16.128 / 12.128 GB / 12.64 Memoria NFC - Prata,16 GB / 128 GB Premium NFC"
)
A18_ML = "Capa...uranyx A18 Pro Max Nfc 16.128 Gb / S5 Edition Prata"
S5_ML = "Caap ** Uranyx S5 128 Gb 16 Gb Ram Nfc + Tag E Fone Prata"
S7_ML = "Cappa **uranyx Fossibot S7 256 Gb Memoria 12 Gb Ram Preto - 256 Gb / 12 Gb"
WP60_ML = "Uranyx Oukitel Wp60 5g 256 Gb Memoria 24 Gb Ram Branco"
F110L_ML = "Caap **uranyx Fossibot F110l 128 Gb Memoria 8 Gb Ram Preto"
F110_PRO_ML = "Cappa **uranyx Fossibot F110 Pro 5g 128 Gb Memoria 20 Gb Ram Preto"
C3_ML = "Caap ** Uranyx C3 128 Gb Memoria 16 Gb Ram Dourado"
C2_ML = "Caap ** Uranyx C2 128 Gb Memoria 16 Gb Ram + Airtag E Fone Azul"
C68_ML = "Caap ** Uranyx Oukitel C68 Plus 512 Gb Memoria 24 Gb Ram Dourado - 24.512"
MARINE_ML = "Uranyx Oscal Marine 1 128 Gb Memoria 12 Gb Ram Preto"
T8_ML = "Uranyx T8 12 Gb Ram 64 Gb + Sd Card 64 Gb Preto"
WP35S_ML = "Uranyx Wp35s 256 Gb Memoria 24 Gb Ram Preto"
F105_ML = "Cappa **uranyx Fossibot F105 64 Gb Memoria 12 Gb Ram - Preto"
F117_ML = "Uranyx F117 256 Gb Memoria 24 Gb Ram Preto"
SPARK_ML = "Tecno Spark Go 1s Preto"
REDMI_ML = "Xiaomi Redmi 15c 128 Gb Memoria 8 Gb Ram - Azul - 128 GB / 8 GB"
C53_BLING = "Uranyx C53 4+64 Roxo + Relógio"
C57_PRO = "Uranyx C57 PRO 4+128 Preto AVULSO"


@pytest.mark.parametrize(
    ("titulo", "item", "esperado"),
    [
        # O carregador.
        (A17_ML, r.CARREGADOR, True),
        (A18_ML, r.CARREGADOR, True),
        # A equipe se divide no S7 ("acompanha apenas cabo" em 11/2026): sem veredito.
        (S7_ML, r.CARREGADOR, None),
        (WP60_ML, r.CARREGADOR, True),
        (F110L_ML, r.CARREGADOR, True),
        (C3_ML, r.CARREGADOR, True),
        (MARINE_ML, r.CARREGADOR, True),
        (T8_ML, r.CARREGADOR, True),
        (C53_BLING, r.CARREGADOR, True),
        (C57_PRO, r.CARREGADOR, True),
        (WP35S_ML, r.CARREGADOR, True),
        # De propósito sem veredito.
        (C68_ML, r.CARREGADOR, None),
        (F105_ML, r.CARREGADOR, None),
        (F110_PRO_ML, r.CARREGADOR, None),
        (F117_ML, r.CARREGADOR, None),
        (C2_ML, r.CARREGADOR, None),
        # A capinha.
        (A17_ML, r.CAPINHA, True),
        (A18_ML, r.CAPINHA, True),
        (S5_ML, r.CAPINHA, True),
        (S7_ML, r.CAPINHA, True),
        (C68_ML, r.CAPINHA, True),
        (REDMI_ML, r.CAPINHA, True),
        (S3_PRO_ML, r.CAPINHA, True),
        (WP60_ML, r.CAPINHA, False),
        (F110_PRO_ML, r.CAPINHA, False),
        (F110L_ML, r.CAPINHA, None),
        # O cabo.
        (A17_ML, r.CABO, True),
        (S7_ML, r.CABO, True),
        (C68_ML, r.CABO, True),
        (WP60_ML, r.CABO, True),
        (C3_ML, r.CABO, True),
        (F105_ML, r.CABO, True),
        (SPARK_ML, r.CABO, True),
        (F117_ML, r.CABO, True),
        (MARINE_ML, r.CABO, True),
        (S5_ML, r.CABO, True),
        (A18_ML, r.CABO, None),
    ],
)
def test_a_caixa_item_por_item(titulo, item, esperado):
    assert r.fatos_da_equipe(titulo, None, "ml").get(item) is esperado


@pytest.mark.parametrize(
    ("titulo", "plataforma", "pelicula", "chave"),
    [
        (A17_ML, "ml", True, True),
        (C68_ML, "ml", True, True),
        (WP60_ML, "ml", True, True),
        (S7_ML, "ml", True, True),
        (SPARK_ML, "ml", True, True),
        (MARINE_ML, "ml", True, True),
        (S5_ML, "ml", True, True),
        (A18_ML, "ml", None, None),
        (A17_SHOPEE, "shopee", False, None),
        (A17_SHOPEE, "tiktok", False, None),
        ("Uranyx Oukitel C68 Plus 16.128 GB, 24.256 Gb ou 24.512", "tiktok", False, None),
        (WP60_ML, "shopee", False, None),
        (A18_ML, "shopee", False, None),
        (S7_ML, "tiktok", False, None),
        (MARINE_ML, "shopee", None, None),
        (A17_ML, "amazon", None, None),
        (A17_ML, "magalu", None, None),
        (A17_ML, None, None, None),
    ],
)
def test_a_pelicula_e_a_chave_dependem_da_plataforma(titulo, plataforma, pelicula, chave):
    fatos = r.fatos_da_equipe(titulo, None, plataforma)
    assert fatos.get(r.PELICULA) is pelicula and fatos.get(r.CHAVE_CHIP) is chave


def test_a_plataforma_vem_do_catalogo():
    fatos = r.fatos_da_equipe(A17_ML, None, IntegrationPlatform.ML)
    assert fatos[r.PELICULA] is True and fatos[r.CHAVE_CHIP] is True


@pytest.mark.parametrize(
    "titulo",
    [
        # Usado, recondicionado, seminovo, devolvido. Os três do A17 são a forma: não há anúncio
        # vivo assim de modelo da tabela.
        "Aparelhos Celulares Smartphone Usados Uranyx Oukitel Fossibot Doogee Itel - C53 4.64 Azul",
        "Uranyx Oukitel C68 Plus 16.128 Gb ou 24.256 Gb ou 512 Gb - Dourado (USADO),16 GB / 128 GB",
        "Oukitel C68 Plus 24.256 - Dourado - USADO - AVULSO",
        "Uranyx A17 Pro Max 128 Gb Recondicionado Preto",
        "Uranyx A17 Pro Max 128 Gb Seminovo Preto",
        "Uranyx A17 Pro Max 128 Gb Semi Novo Preto",
        "Uranyx A17 Pro Max 128 Gb Devolvido Preto",
        "Mala Brilho Listrada P1 tamanho 20 - Cinza Escuro - Avariada",
        # Mais de 1 código.
        "Uranyx A17 Pro Max 12.64 ou 12.128 GB Memoria ou Uranyx S5 16.128gb NFC - A17 - Laranja,"
        "12 GB / 64 GB",
        F110_OS_DOIS,
        'Mala De Viagem 24" Média De Despacho Material Polipropileno P2 - Bege P4',
        # A linha toda.
        "Smartphones Uranyx Séries C com 64,128,256 e 512 GB Memoria com NFC - C57 Pro - Azul,"
        "128 GB",
        "Uranyx Séries C com 64,128,256 e 512 GB Memoria com NFC - C53 - Preto,64 GB",
        "Celulares Uranyx Séries com 64,128 ou 256 GB Memoria Varios Modelos - C3 - Dourado,128 GB",
        "Mala De Bordo 20 Polegadas Pequena - Diversos Modelos M3 Coral",
        "Conjunto De Mala Despacho Com 2 Frasqueiras Diversos Modelos M2 (Azul Indigo)",
        # Outra marca: o código é igual, o aparelho não. O Galaxy é a forma.
        "Samsung Galaxy S7 128 Gb Preto",
        "Poco C65 8.128 - Azul",
        "Redmi A5 6.64 - Dourado + Fone U9 + Relógio",
        "Apple MacMini M2 8.256 GB - Original Lacrado - 1 Ano de Garantia NF - M2 - 8 / 256 GB",
        # O acessório à venda, não o aparelho nem a mala. A forma: não há anúncio vivo assim com
        # 1 código de modelo (06/11/2026, 16,2 mil títulos).
        "Capinha Para Uranyx A17 Pro Max Transparente",
        "Pelicula Compativel Uranyx S7",
        "Par De Rodinhas 360 Graus Para Malas De Viagem Charlots P7",
        # Fora da tabela: nada de regra geral.
        "Uranyx Oukitel G1s 128 Gb Memoria 8 Gb Ram Preto",
        "Tecno Spark Go (2024) Dual Sim 64 Gb Memoria 6 Gb Ram Preto",
        "Redmi 14c 8.128 - Preto",
        None,
        "",
    ],
)
def test_nenhum_veredito(titulo):
    assert r.fatos_da_equipe(titulo, None, "ml") == {}


@pytest.mark.parametrize(
    ("titulo", "item"),
    [
        # A entrada por versão só vale quando o texto diz a versão. Só o S3 é real: o resto é a
        # forma, os anúncios vivos desses modelos dizem a versão.
        ("Uranyx Oukitel C68 128 Gb Memoria 16 Gb Ram", r.CAPINHA),
        ('URANYX S3 Celular 4G 128GB 20GB (6+14) RAM 6,67" Tela Dupla - Verde', r.CAPINHA),
        ("Uranyx F110 128 Gb Memoria 20 Gb Ram", r.CAPINHA),
        ("Uranyx C57 128 Gb Memoria 8 Gb Ram", r.CARREGADOR),
        ("Uranyx C57s 64 Gb Memoria 4 Gb Ram", r.CARREGADOR),
    ],
)
def test_a_versao_que_o_texto_nao_diz(titulo, item):
    assert item not in r.fatos_da_equipe(titulo, None, "ml")


def test_fone_e_relogio_nunca_tem_veredito():
    for titulo in (A17_ML, C3_ML, "Uranyx C3 16.128 - Dourado + Fone U9 + Relógio"):
        fatos = r.fatos_da_equipe(titulo, None, "ml")
        assert {"fone", "relogio"}.isdisjoint(fatos)


def test_o_avulso_com_1_codigo_de_celular_reconhecido():
    # Quem está em alguma tabela de celular, mesmo sem veredito da caixa (F109).
    for titulo in (
        A17_ML,
        REDMI_ML,
        SPARK_ML,
        "Uranyx F109 5g 256 Gb 24 Gb Ram + Airtag E Fone Preto",
    ):
        assert r.fatos_da_equipe(titulo, None, "ml")[r.VENDE_AVULSO] is False
    # A mala não tem esse campo, e o avulso nunca é True.
    assert r.VENDE_AVULSO not in r.fatos_da_equipe(P7, None, "ml")


@pytest.mark.parametrize(
    ("titulo", "rodas"),
    [
        ("Mala De Viagem 24 Pol Média De Despacho Polipropileno - Preto P7", True),
        ('Mala De Viagem 20" Pequena De Bordo Material Polipropileno - Azul P8', True),
        (P7, True),
        (P8, True),
        ("Mala De Bordo 20 Pol 10kg Polipropileno Tsa Rodas 360 Preto P2", False),
        ("Kit Mala Bordo 20 Pol 10kg + Frasqueira Tsa Pp Bege P4", False),
        # O M2 pelo tamanho da mala: a frasqueira do kit não conta.
        ("Mala De Bordo 18 Polegadas 10kg M2 (Preto)", True),
        ("Mala E Frasqueira De Viagem 12 E 18 Polegadas De Bordo Branca M2", True),
        ("Mala Pequena De Viagem 20 Polegadas De Bordo M2 - Roxo Escuro", False),
        ("Kit Conjunto Mala De Bordo 20'' + Frasqueira Viagem M2 Cinza-escuro", False),
        ("Malas Pequena E Media De Viagem 18 E 24 Polegadas M2 - Branca", None),
        ("Kit 4 Malas 24/20/12/8 + Mochila Encosto Chaveiro Abs Verde-escuro M2", None),
        ("Kit 6 Malas / Frasqueiras/Necessaire De Viagem Bordo E Despacho ABS M2 - Coral", None),
        # P5 e P6: conflito entre as lojas. M4: nada na tabela.
        ("Mala De Viagem 20  Pequena De Bordo P5 Azul-claro", None),
        ("Mala De Viagem 20  Pequena De Bordo P6 Dourado", None),
        ("Mala De Bordo 18 Polegadas 10kg M4 (Preto)", None),
        # A frasqueira não tem rodinha.
        ("Frasqueira Mala De Mão 14 E 16 Polegadas Polipropileno Azul Claro P7", None),
        (
            "Necessaire Maquiagem Bolsa Organizador Mala Viagem 14 Polegadas Polipropileno P2 - "
            "Azul Escuro Arco",
            None,
        ),
    ],
)
def test_a_rodinha_removivel(titulo, rodas):
    assert r.fatos_da_equipe(titulo, None, "ml").get(r.RODAS) is rodas


@pytest.mark.parametrize(
    ("titulo", "anac"),
    [
        ("Mala De Bordo 18 Polegadas 10kg M2 (Preto)", r.BORDO),
        ("Mala De Viagem 20 Polegadas  De Bordo P3 Polipropileno Azul-escuro", r.BORDO),
        ("Mala De Viagem 20'' Pequena De Bordo M5 Champanhe", r.BORDO),
        ('Mala De Viagem 20" Pequena De Bordo Material Polipropileno - Azul P8', r.BORDO),
        ("Mala De Viagem Despacho 24 Polegadas 23kg Abs Rodas 360 Tsa Verde-escuro M6", r.DESPACHO),
        ("Mala De Viagem 24 Pol Média De Despacho Polipropileno - Preto P7", r.DESPACHO),
        # O "P8" não é tamanho. Sem o código, vale o tamanho.
        ("Mala De Bordo 20 Polegadas 10kg Polipropileno Tsa Rodas 360 Rosa P8 (premium)", r.BORDO),
        ("Mala De Bordo 20 Polegadas 10Kg (Azul Escuro)", r.BORDO),
        # Kit ou mais de um tamanho: sem veredito.
        (
            "Kit Mala Bordo 20 Pol 10kg + Frasqueira Tsa Pp Rosa P7 (expansível/roda Removível)",
            None,
        ),
        ("Conjunto Mala De Despacho 24 Polegadas 23kg + Frasqueira P1 Preto", None),
        ("Mala Bordo 20 Pol 10kg Abs + Mochila Encosto E Chaveiro Coral M2", None),
        ("Mala E Frasqueira De Viagem 12 E 18 Polegadas De Bordo Branca M2", None),
        ("Malas Pequena E Media De Viagem 18 E 24 Polegadas M2 - Branca", None),
        # Sem a unidade não é "em polegadas"; o 14 não está na tabela.
        ("Mala De Viagem 20 Pequena De Bordo P2 - Cinza-escuro", None),
        ("Frasqueira Mala De Mão Viagem 14 Polegada P1 Polipropileno Bege", None),
    ],
)
def test_o_padrao_anac_pelo_tamanho(titulo, anac):
    assert r.fatos_da_equipe(titulo, None, "ml").get(r.PADRAO_ANAC) == anac


def test_o_tamanho_da_variacao_e_o_mais_especifico():
    # A forma: hoje nenhum anúncio do M2 diz o tamanho só na variação.
    titulo = "Mala De Viagem 18, 20 ou 24 Polegadas 10kg M2"
    assert r.PADRAO_ANAC not in r.fatos_da_equipe(titulo, None, "ml")
    assert r.fatos_da_equipe(titulo, "Preto,20 Polegadas", "ml")[r.PADRAO_ANAC] == r.BORDO
    assert r.fatos_da_equipe(titulo, "Preto,20 Polegadas", "ml")[r.RODAS] is False
    assert r.fatos_da_equipe(titulo, "Preto,18 Polegadas", "ml")[r.RODAS] is True


@pytest.mark.parametrize(
    ("titulo", "material"),
    [
        ("Mala De Viagem 20 Pol Pequena De Bordo Polipropileno - P1 - Bege", r.POLIPROPILENO),
        ("Mala De Bordo 20 Pol 10kg Polipropileno Tsa Rodas 360 Preto P2", r.POLIPROPILENO),
        ("Mala De Viagem 24 Polegadas De Despacho P3 Polipropileno Azul-escuro", r.POLIPROPILENO),
        ("Mala De Viagem 24 Média De Despacho P4 Polipropileno Verde-escuro", r.POLIPROPILENO),
        ("Mala De Viagem 24  Média De Despacho P5 Azul-claro", r.POLIPROPILENO),
        ("Mala De Viagem 20  Pequena De Bordo P6 Dourado", r.POLIPROPILENO),
        ("Frasqueira Mala De Mão 14 E 16 Polegadas Polipropileno Azul Claro P7", r.POLIPROPILENO),
        ("Mala De Viagem 24 Pol Média De Despacho Polipropileno - Azul P8", None),
        ("Mala De Bordo 18 Polegadas 10kg M2 (Preto)", None),
    ],
)
def test_o_material_da_mala(titulo, material):
    assert r.fatos_da_equipe(titulo, None, "ml").get(r.MATERIAL) == material


def test_o_ziper_da_p2_e_nao():
    titulo = "Mala De Bordo 20 Pol 10kg Polipropileno Tsa Rodas 360 Preto P2"
    assert r.o_que_a_equipe_diz(r.ZIPER, titulo) is False
    pendencias = checagens.conferir_fonte_produto(
        "Sim, esta mala é expansível.", [{"item_id": "1", "titulo": titulo}], None
    )
    assert [p.codigo for p in pendencias if checagens.pendencia_dura(p)] == [
        checagens.CONTRA_A_EQUIPE
    ]


# ═══════════════ a caixa e a mala: as especificações ═══════════════


def _especificacoes_de(titulo: str, plataforma: Any = "ml", ficha_: dict | None = None) -> dict:
    return e.aplicar_a_equipe(_juntar(ficha_, titulo), titulo, None, plataforma)


def test_as_linhas_da_caixa_tem_a_fonte_equipe():
    r_ = _especificacoes_de(A17_ML)
    for campo in ("carregador", "capinha", "cabo", "pelicula", "chave_chip"):
        linha = _linha(r_, campo)
        assert linha == {"campo": campo, "valor": e.VEM_NA_CAIXA, "fonte": e.FONTE_EQUIPE}
    assert _linha(r_, "vende_acessorio_avulso") == {
        "campo": "vende_acessorio_avulso",
        "valor": e.NAO,
        "fonte": e.FONTE_EQUIPE,
        "nota": e.NOTA_AVULSO,
    }
    ordem = [c["campo"] for c in r_["campos"]]
    assert ordem == sorted(ordem, key=e.CAMPOS.index)


def test_o_que_nao_vem_diz_que_nao_vem():
    r_ = _especificacoes_de(A17_SHOPEE, "shopee")
    assert _linha(r_, "pelicula")["valor"] == e.NAO_VEM_NA_CAIXA
    assert "chave_chip" not in [c["campo"] for c in r_["campos"]]


@pytest.mark.parametrize(
    "titulo", [C2_ML, "Uranyx Oukitel G1s 128 Gb Memoria 8 Gb Ram Preto", None]
)
def test_sem_veredito_nenhuma_linha_nova(titulo):
    antes = _juntar(None, titulo)
    campos = {
        "carregador",
        "capinha",
        "cabo",
        "pelicula",
        "chave_chip",
        "rodas_removiveis",
        "padrao_anac",
    }
    assert not [
        c for c in e.aplicar_a_equipe(antes, titulo, None, "ml")["campos"] if c["campo"] in campos
    ]


def test_a_mala_rodinha_nota_material_e_anac():
    titulo = (
        "Mala De Bordo 20 Pol 10kg Polipropileno Tsa Rodas 360 Cáqui P7 (expansível/roda Removível)"
    )
    ficha_ = {
        "titulo": titulo,
        "atributos": ["Material: Polipropileno", "Dimensões: 53 x 34 x 22 cm"],
    }
    r_ = _especificacoes_de(titulo, "ml", ficha_)
    assert _linha(r_, "rodas_removiveis") == {
        "campo": "rodas_removiveis",
        "valor": e.SIM,
        "fonte": e.FONTE_EQUIPE,
        "nota": e.NOTA_MEDIDAS,
    }
    assert _linha(r_, "padrao_anac")["valor"] == "padrão ANAC de bordo"
    # O material já existe: passa a ser da equipe, sem a nota de contradição.
    assert _linha(r_, "material") == {
        "campo": "material",
        "valor": r.POLIPROPILENO,
        "fonte": e.FONTE_EQUIPE,
    }
    # A medida continua a do anúncio, com a nota; nenhum centímetro vem da equipe.
    assert _linha(r_, "dimensoes")["fonte"] == e.FONTE_ANUNCIO
    assert _linha(r_, "dimensoes")["nota"] == e.NOTA_MEDIDAS
    assert "cm" not in e.NOTA_MEDIDAS and not any(ch.isdigit() for ch in e.NOTA_MEDIDAS)


def test_a_nota_das_medidas_so_com_veredito_da_rodinha():
    titulo = "Mala De Viagem 20  Pequena De Bordo P5 Azul-claro"
    r_ = _especificacoes_de(
        titulo, "ml", {"titulo": titulo, "atributos": ["Dimensões: 53 x 34 x 22 cm"]}
    )
    assert "nota" not in _linha(r_, "dimensoes")
    assert "rodas_removiveis" not in [c["campo"] for c in r_["campos"]]


def test_o_material_contra_o_anuncio_ganha_a_nota():
    titulo = "Mala De Viagem 24 Polegadas De Despacho P3 Polipropileno Azul-escuro"
    r_ = _especificacoes_de(titulo, "ml", {"titulo": titulo, "atributos": ["Material: ABS"]})
    assert _linha(r_, "material")["valor"] == r.POLIPROPILENO
    assert _linha(r_, "material")["nota"] == e.NOTA_EQUIPE


def test_a_ficha_que_declara_usado_fica_sem_a_caixa():
    assert e._anuncio_nao_e_novo({"condicao": {"novo": {"valor": False, "fontes": ["atributo"]}}})
    assert e._anuncio_nao_e_novo(
        {"condicao": {"novo": {"valor": "conflito", "fontes": ["titulo"]}}}
    )
    assert not e._anuncio_nao_e_novo({"condicao": {"novo": {"valor": True, "fontes": ["titulo"]}}})
    assert not e._anuncio_nao_e_novo({}) and not e._anuncio_nao_e_novo(None)
    r_ = e.aplicar_a_equipe(_juntar(None, S5), S5, None, "ml", sem_a_caixa=True)
    assert [c["campo"] for c in r_["campos"] if c["fonte"] == e.FONTE_EQUIPE] == ["rede_5g", "nfc"]


async def test_no_banco_a_plataforma_chega_a_caixa(db, make_user):
    ids = await _produtos(db, make_user, "Uranyx A17 Pro Max 12.128 - Laranja + Fone U9")
    r_ = await e.especificacoes_do_produto(db, None, ids, A17_ML, IntegrationPlatform.ML)
    assert _linha(r_, "pelicula")["valor"] == e.VEM_NA_CAIXA
    assert _linha(r_, "chave_chip")["valor"] == e.VEM_NA_CAIXA
    r_ = await e.especificacoes_do_produto(db, None, ids, A17_ML, IntegrationPlatform.SHOPEE)
    assert _linha(r_, "pelicula")["valor"] == e.NAO_VEM_NA_CAIXA
    r_ = await e.especificacoes_do_produto(db, None, ids, A17_ML)
    assert "pelicula" not in [c["campo"] for c in r_["campos"]]
    assert _linha(r_, "carregador")["fonte"] == e.FONTE_EQUIPE


async def test_no_banco_o_anuncio_de_2_modelos_nao_tem_a_caixa(db, make_user):
    ids = await _produtos(
        db, make_user, "Uranyx A17 Pro Max 12.128 - Preto", "Uranyx C17 8.128 - Prata"
    )
    r_ = await e.especificacoes_do_produto(db, None, ids, A17_ML, IntegrationPlatform.ML)
    assert not [c for c in r_["campos"] if c["fonte"] == e.FONTE_EQUIPE]


async def test_no_banco_a_ficha_de_usado_nao_tem_a_caixa(db, make_user):
    ids = await _produtos(db, make_user, "Uranyx A17 Pro Max 12.128 - Preto")
    ficha_ = {"condicao": {"novo": {"valor": False, "fontes": ["atributo"]}}}
    r_ = await e.especificacoes_do_produto(db, ficha_, ids, A17_ML, IntegrationPlatform.ML)
    assert "carregador" not in [c["campo"] for c in r_["campos"]]


# ═══════════════ a caixa e a mala: o que barra e o que passa ═══════════════


def _com_a_caixa(titulo: str, plataforma: Any = "ml", **extra: Any) -> dict:
    return {
        "item_id": "1",
        "titulo": titulo,
        "de_onde": "anuncio ligado a conversa",
        "especificacoes": e.aplicar_a_equipe(_juntar(None, titulo), titulo, None, plataforma),
        **extra,
    }


def _contra(texto: str, ficha_: dict, fala: str = "") -> list[str]:
    pendencias = checagens.conferir_fonte_produto(
        texto, [ficha_], None, textos_cliente=[fala] if fala else []
    )
    assert all(
        checagens.pendencia_dura(p) for p in pendencias if p.codigo == checagens.CONTRA_A_EQUIPE
    )
    return [p.motivo for p in pendencias if p.codigo == checagens.CONTRA_A_EQUIPE]


@pytest.mark.parametrize(
    ("fala", "texto", "titulo", "plataforma", "rotulo"),
    [
        # Os dois exemplos reais de 05/11, com o texto do rascunho.
        (
            "Acompanha carregador?",
            "Olá! Pela descrição do anúncio, o kit traz o aparelho Uranyx A17 Pro Max com o Fone "
            "Com Fio S10+. O carregador não aparece entre os itens listados. Se você já comprou, "
            "fale com a loja pelas mensagens da compra.",
            A17_ML,
            "ml",
            "o carregador",
        ),
        (
            "vem capinha?",
            "Olá! O anúncio informa que acompanha o celular e o fone com fio; capinha não consta "
            "entre os itens anunciados. Se você já comprou, fale com a loja pelas mensagens da "
            "compra.",
            S3_PRO_ML,
            "ml",
            "a capinha",
        ),
        # A forma de cada negativa.
        ("Acompanha carregador?", "Não acompanha carregador.", A17_ML, "ml", "o carregador"),
        ("Acompanha carregador?", "Vem sem carregador.", A17_ML, "ml", "o carregador"),
        (
            "Acompanha carregador?",
            "O carregador não faz parte do kit.",
            A17_ML,
            "ml",
            "o carregador",
        ),
        ("Acompanha carregador?", "O carregador fica de fora.", A17_ML, "ml", "o carregador"),
        ("Acompanha carregador?", "O carregador não.", A17_ML, "ml", "o carregador"),
        ("vem cabo?", "O anúncio não informa se vem cabo.", S7_ML, "ml", "o cabo"),
        ("vem cabo?", "O cabo não consta no anúncio.", S7_ML, "ml", "o cabo"),
        ("vem cabo?", "Não consigo confirmar se vem cabo.", S7_ML, "ml", "o cabo"),
        ("vem pelicula?", "A película não está inclusa.", S5_ML, "ml", "a película"),
        ("vem a chave do chip?", "Não vem com a chave do chip.", WP60_ML, "ml", "a chave do chip"),
        ("vem chave chip?", "A chave chip não consta.", S5_ML, "ml", "a chave do chip"),
        # Na enumeração, e o item citado antes da negativa.
        (
            "vem capinha e cabo?",
            "O A17 Pro Max vai com capinha transparente. Sobre o cabo USB e a película, o anúncio "
            "não informa que acompanham o aparelho.",
            A17_SHOPEE,
            "shopee",
            "o cabo",
        ),
        ("vem carregador?", "A capinha e o carregador não constam.", A17_ML, "ml", "o carregador"),
        ("vem capinha?", "A capinha e o carregador não constam.", A17_ML, "ml", "a capinha"),
        # A resposta curta e a omissão.
        ("Acompanha carregador?", "Não.", A17_ML, "ml", "o carregador"),
        ("Acompanha carregador?", "Olá! Não acompanha.", A17_ML, "ml", "o carregador"),
        ("Acompanha carregador?", "O kit traz o aparelho e o fone.", A17_ML, "ml", "o carregador"),
        # A lista exclusiva.
        ("o que vem?", "Acompanha apenas o cabo de carregamento.", A17_ML, "ml", "o carregador"),
        ("o que vem?", "Vem só com o fone.", A17_ML, "ml", "o cabo"),
        # O que a equipe nega, afirmado.
        ("vem capinha?", "Sim, acompanha capinha.", WP60_ML, "ml", "a capinha"),
        ("vem capinha?", "Vem com capinha, carregador e cabo.", WP60_ML, "ml", "a capinha"),
        ("vem capinha?", "A capinha vai de brinde.", F110_PRO_ML, "ml", "a capinha"),
        ("vem pelicula?", "Sim, vem com película.", A17_SHOPEE, "shopee", "a película"),
        ("vem pelicula?", "Sim, vem.", A17_SHOPEE, "tiktok", "a película"),
        ("vem pelicula?", "Acompanha capinha e película.", A17_SHOPEE, "shopee", "a película"),
    ],
)
def test_a_caixa_contra_a_equipe_e_dura(fala, texto, titulo, plataforma, rotulo):
    motivos = _contra(texto, _com_a_caixa(titulo, plataforma), fala)
    assert [m for m in motivos if f"diz que {rotulo} " in m], motivos


@pytest.mark.parametrize(
    ("fala", "texto", "titulo", "plataforma"),
    [
        ("Acompanha carregador?", "Sim, acompanha carregador.", A17_ML, "ml"),
        ("Acompanha carregador?", "Sim, acompanha.", A17_ML, "ml"),
        ("Acompanha carregador?", "Sim.", A17_ML, "ml"),
        ("Acompanha carregador?", "O carregador vem na caixa.", A17_ML, "ml"),
        ("vem capinha?", "Sim, vem capinha.", S3_PRO_ML, "ml"),
        ("vem pelicula?", "Sim, vem com película.", A17_ML, "ml"),
        ("vem a chave do chip?", "Sim, vem com a chave do chip.", A17_ML, "ml"),
        # O real do dia 05/11: acompanha tudo, em lista (o fone não tem veredito: outro aviso).
        (
            "o que vem?",
            "Conforme o anúncio, ele acompanha capinha, carregador, película, cabo de carregamento "
            "e chave para chip.",
            A17_ML,
            "ml",
        ),
        ("vem pelicula?", "Não acompanha película.", A17_SHOPEE, "shopee"),
        ("vem pelicula?", "A película não vem.", A17_SHOPEE, "shopee"),
        ("vem pelicula?", "Não.", A17_SHOPEE, "shopee"),
        # O "não informa" do que a equipe nega é evasivo (outro aviso), não a contraria.
        ("vem pelicula?", "O anúncio não informa película.", A17_SHOPEE, "shopee"),
        ("vem capinha?", "Não acompanha capinha.", WP60_ML, "ml"),
        # A negativa de um, a afirmação do outro: "só" separa.
        ("vem capinha?", "Não vem capinha, mas vem carregador e cabo.", WP60_ML, "ml"),
        # Não é o que vem na caixa: servir, comprar à parte, o defeito, o brinde.
        (
            "capinha do iphone serve?",
            "A capinha do iPhone não serve neste aparelho, porque capas são feitas para as medidas "
            "de cada modelo.",
            A17_SHOPEE,
            "shopee",
        ),
        ("a capa do 17 cabe?", "Não, a capa não cabe neste aparelho.", A17_SHOPEE, "shopee"),
        (
            "Vocês vendem capinha?",
            "No momento não trabalhamos com capinha nem película avulsas nesta loja.",
            A17_SHOPEE,
            "shopee",
        ),
        (
            "tem capinha colorida?",
            "Não temos capinhas à venda na nossa loja. O aparelho vai com a capinha transparente, "
            "e capinha de outro modelo não serve nele, porque as medidas mudam.",
            A17_SHOPEE,
            "shopee",
        ),
        (
            "tem capinha colorida?",
            "Não temos capinhas à venda na nossa loja, nem coloridas. Capinha só serve se for "
            "feita para o modelo do aparelho, porque as medidas mudam de um modelo para outro.",
            A17_ML,
            "ml",
        ),
        ("meu carregador", "Sinto muito pelo transtorno com o carregador.", A17_ML, "ml"),
        (
            "cade o carregador?",
            "O aparelho vem sem carregador de fábrica, e o que enviamos foi um brinde.",
            A17_ML,
            "ml",
        ),
        # Outra versão ou modelo na oração, a pergunta sem resposta, o pedido de foto.
        ("vem capinha?", "A capinha vem só na versão Premium.", A17_ML, "ml"),
        ("vem carregador?", "O C2 não vem com carregador.", A17_ML, "ml"),
        ("vem carregador?", "Você quer saber se vem carregador?", A17_ML, "ml"),
        (
            "veio sem carregador",
            "Pode enviar uma foto do que não veio, sem o carregador?",
            A17_ML,
            "ml",
        ),
        # O cliente fala de outro modelo.
        ("E o F105, vem carregador?", "Não vem carregador.", A17_ML, "ml"),
        # Sem veredito: como hoje.
        ("Acompanha carregador?", "O carregador não aparece entre os itens.", C2_ML, "ml"),
        ("vem pelicula?", "A película não vem.", A17_ML, "amazon"),
    ],
)
def test_a_caixa_que_nao_contraria_a_equipe(fala, texto, titulo, plataforma):
    assert _contra(texto, _com_a_caixa(titulo, plataforma), fala) == []


def test_sem_a_fala_a_negativa_vale_do_mesmo_jeito():
    assert _contra("O carregador não aparece.", _com_a_caixa(A17_ML))


def test_o_fone_continua_sem_veredito_e_sem_fonte():
    # Fone não tem veredito: o "acompanha fone" sem fonte fica como hoje, mesmo com o avulso.
    pendencias = checagens.conferir_fonte_produto("Acompanha fone.", [_com_a_caixa(A17_ML)], None)
    assert [p.codigo for p in pendencias] == [checagens.PRODUTO_SEM_FONTE]


def test_acompanhar_com_o_veredito_tem_fonte():
    # (a) sem a linha da equipe, o mesmo texto é "sem fonte".
    texto = "Acompanha carregador."
    assert not checagens.conferir_fonte_produto(texto, [_com_a_caixa(A17_ML)], None)
    (p,) = checagens.conferir_fonte_produto(texto, [_com_a_caixa(C2_ML)], None)
    assert p.codigo == checagens.PRODUTO_SEM_FONTE


def test_a_palavra_da_equipe_vale_contra_a_ficha():
    # A descrição diz que não vem; a equipe diz que vem: passa, e a negativa é a barrada.
    ficha_ = _com_a_caixa(A17_ML, descricao="Não acompanha carregador.")
    assert not checagens.conferir_fonte_produto("Acompanha carregador.", [ficha_], None)
    assert _contra("Não acompanha carregador.", ficha_)
    # O contrário: a descrição cita, a equipe nega.
    ficha_ = _com_a_caixa(A17_SHOPEE, "shopee", descricao="Acompanha película.")
    assert not checagens.conferir_fonte_produto("Não acompanha película.", [ficha_], None)
    assert _contra("Acompanha película.", ficha_)


def test_com_2_anuncios_so_vale_o_que_os_dois_dizem():
    a, b = _com_a_caixa(A17_ML), {**_com_a_caixa(C2_ML), "item_id": "2"}
    pendencias = checagens.conferir_fonte_produto("O carregador não aparece.", [a, b], None)
    assert checagens.CONTRA_A_EQUIPE not in [p.codigo for p in pendencias]
    # O carregador os dois não dizem igual; o avulso, sim.
    da_equipe = checagens._da_equipe_na_conversa([a, b])
    assert "carregador" not in da_equipe and da_equipe["vende_acessorio_avulso"] == e.NAO


def test_o_pedido_de_outro_modelo_tira_a_palavra_da_equipe():
    ficha_ = _com_a_caixa(A17_ML)
    assert checagens._da_equipe_na_conversa([ficha_], [A17_ML])
    assert checagens._da_equipe_na_conversa([ficha_], ["Uranyx A18 Pro Max 16.128 - Preto"]) == {}


def test_o_ver_anuncio_e_a_lista_do_pacote_tambem_valem():
    ficha_ = _com_a_caixa(A17_ML)
    assert checagens._da_equipe_na_conversa([[ficha_]])["carregador"] == e.VEM_NA_CAIXA
    # O pedido, a busca e o manual não são anúncio; a ficha lida sem linha tira o veredito.
    assert checagens._da_equipe_na_conversa([{"itens": [{"item_id": "9"}]}, ficha_])
    assert checagens._da_equipe_na_conversa([ficha_, {"item_id": "2", "titulo": C2_ML}]) == {}
    assert checagens._da_equipe_na_conversa([ficha_, {"item_id": "1", "titulo": A17_ML}])


@pytest.mark.parametrize(
    ("texto", "titulo", "plataforma", "segura"),
    [
        # (b) a negativa do que a equipe nega não é mais "sem o dado".
        ("A película não vem.", A17_SHOPEE, "shopee", False),
        ("Não acompanha película.", A17_SHOPEE, "shopee", False),
        ("Não acompanha capinha.", WP60_ML, "ml", False),
        ("A película não consta.", A17_SHOPEE, "tiktok", False),
        # Junto com a negativa de quem não tem veredito: fica.
        ("A película não vem, e o fone não consta.", A17_SHOPEE, "shopee", True),
        ("Não vem película nem acessórios.", A17_SHOPEE, "shopee", True),
        # O que a equipe confirma, negado: fica, além da trava da equipe.
        ("O carregador não vem.", A17_ML, "ml", True),
        # Sem veredito: como hoje.
        ("O carregador não vem.", C2_ML, "ml", True),
        ("A película não vem.", A17_ML, "amazon", True),
    ],
)
def test_o_conteudo_sem_o_dado_com_a_equipe(texto, titulo, plataforma, segura):
    ficha_ = _com_a_caixa(titulo, plataforma)
    fala = "vem película, carregador e capinha?"
    pendencias = checagens.conferir_conteudo_sem_o_dado(
        texto,
        [fala],
        acao="responder",
        fichas=[ficha_],
        da_equipe=checagens._da_equipe_na_conversa([ficha_]),
    )
    assert bool(pendencias) is segura, pendencias


@pytest.mark.parametrize(
    ("fala", "texto", "titulo", "barra"),
    [
        # O caso real de 05/11, na mala de rodinha fixa: o "não informa" não contraria.
        (
            "A rodinha é removivel? Ela é padrao anac? Qual material?",
            "Olá! O material é polipropileno, e a mala é de bordo de 20 polegadas. O anúncio não "
            "informa se a rodinha é removível.",
            "Mala De Bordo 20 Pol 10kg Polipropileno Tsa Rodas 360 Preto P2",
            False,
        ),
        # A que a equipe confirma: o "não informa" é a negativa.
        ("as rodas são removiveis?", "O anúncio não informa se as rodas são removíveis.", P7, True),
        ("as rodas são removiveis?", "As rodinhas não são removíveis.", P7, True),
        ("as rodas são removiveis?", "As rodas são fixas.", P7, True),
        ("as rodas são removiveis?", "Não.", P7, True),
        ("as rodas são removiveis?", "Sim, as rodinhas são removíveis.", P7, False),
        ("as rodas são removiveis?", "Sim.", P7, False),
        # A que a equipe nega.
        (
            "as rodas são removiveis?",
            "Sim, as rodinhas são removíveis.",
            "Mala De Bordo 20 Pol 10kg Polipropileno Tsa Rodas 360 Preto P2",
            True,
        ),
        (
            "as rodas são removiveis?",
            "Não, as rodinhas não são removíveis.",
            "Mala De Bordo 20 Pol 10kg Polipropileno Tsa Rodas 360 Preto P2",
            False,
        ),
        (
            "as rodas são removiveis?",
            "As rodas 360 são removíveis nos modelos Premium.",
            "Mala De Bordo 20 Pol 10kg Polipropileno Tsa Rodas 360 Preto P2",
            False,
        ),
        # P5: conflito, sem veredito.
        (
            "as rodas são removiveis?",
            "Sim, as rodinhas são removíveis.",
            "Mala De Viagem 20  Pequena De Bordo P5 Azul-claro",
            False,
        ),
        # O padrão ANAC.
        ("é padrão anac?", "Sim, é padrão ANAC de bordo.", M2_18, False),
        ("é padrão anac?", "O anúncio não informa se é padrão ANAC.", M2_18, True),
        ("é padrão anac?", "Não é padrão ANAC.", M2_18, True),
        ("é padrão anac?", "Não.", M2_18, True),
        ("vai a bordo?", "Precisa ser despachada.", M2_18, True),
        ("vai a bordo?", "Sim, vai a bordo.", M6_24, True),
        ("vai a bordo?", "Sim.", M6_24, True),
        ("vai a bordo?", "Não, é padrão de despacho, não vai a bordo.", M6_24, False),
        ("é de despacho?", "Não é de despacho.", M6_24, True),
        # "De bordo" é o nome, e a negação é de outra coisa.
        ("vem cadeado?", "A mala de bordo não vem com cadeado.", M2_18, False),
        ("é de bordo?", "A mala não é de bordo.", M2_18, True),
        ("é de bordo?", "Confira se é de bordo.", M2_18, True),
        ("precisa despachar?", "Não precisa despachar.", M2_18, False),
        # O material.
        ("resistente?", "O material é resistente e não amassa.", P7, False),
        ("qual o material?", "O material é polipropileno.", P7, False),
        ("qual o material?", "O material é ABS.", P7, True),
        ("qual o material?", "A mala é de ABS.", P7, True),
        ("qual o material?", "O anúncio não informa o material.", P7, True),
        ("é abs?", "O material não é ABS.", P7, False),
    ],
)
def test_a_mala_contra_a_equipe_e_dura(fala, texto, titulo, barra):
    assert bool(_contra(texto, _com_a_caixa(titulo), fala)) is barra


@pytest.mark.parametrize(
    ("fala", "texto", "titulo", "plataforma", "assunto"),
    [
        ("Acompanha carregador?", "Sim, acompanha carregador.", A17_ML, "ml", "conteudo_da_caixa"),
        ("Acompanha carregador?", "Sim, acompanha.", A17_ML, "ml", "conteudo_da_caixa"),
        ("vem capinha?", "Sim, a capinha vem na caixa.", S3_PRO_ML, "ml", "conteudo_da_caixa"),
        ("vem cabo?", "Sim, acompanha cabo de carregamento.", A17_ML, "ml", "conteudo_da_caixa"),
        ("vem chave?", "Sim, vem com a chave do chip.", A17_ML, "ml", "conteudo_da_caixa"),
        ("vem pelicula?", "Não acompanha película.", A17_SHOPEE, "shopee", "conteudo_da_caixa"),
        ("vem capinha?", "Não, a capinha não vem na caixa.", WP60_ML, "ml", "conteudo_da_caixa"),
        ("as rodas saem?", "Sim, as rodinhas são removíveis.", P7, "ml", "espec_mala"),
        (
            "as rodas sao removiveis?",
            "Não, as rodinhas não são removíveis.",
            "Mala De Bordo 20 Pol 10kg Polipropileno Tsa Rodas 360 Preto P2",
            "ml",
            "espec_mala",
        ),
        ("é padrao anac?", "Sim, é padrão ANAC de bordo.", M2_18, "ml", "espec_mala"),
        ("vai a bordo?", "Não, é padrão de despacho, não vai a bordo.", M6_24, "ml", "espec_mala"),
        ("qual o material?", "O material é polipropileno.", P7, "ml", "espec_mala"),
    ],
)
def test_a_resposta_certa_sai_pelo_anuncio(fala, texto, titulo, plataforma, assunto):
    # A liberação L1: a resposta certa com a palavra da equipe não fica presa à ficha no automático.
    ficha_ = _com_a_caixa(
        titulo,
        plataforma,
        descricao="Celular resistente. Kit com aparelho.",
        atributos=["Marca: X"],
    )
    assert checagens.liberacao_pelo_anuncio(
        texto, assunto, evidencias=["espec:1"], fichas=[ficha_], textos_cliente=[fala]
    )


def test_sem_a_equipe_a_mesma_resposta_nao_sai_pelo_anuncio():
    ficha_ = _com_a_caixa(C2_ML, descricao="Celular resistente.", atributos=["Marca: X"])
    assert not checagens.liberacao_pelo_anuncio(
        "Sim, acompanha carregador.",
        "conteudo_da_caixa",
        evidencias=["espec:1"],
        fichas=[ficha_],
        textos_cliente=["Acompanha carregador?"],
    )


def test_o_avulso_nao_vira_dado_de_que_o_fone_vem():
    # A nota do avulso cita o fone: "acompanha fone" continua fora do dado.
    ficha_ = _com_a_caixa(A17_ML, descricao="Celular resistente.", atributos=["Marca: X"])
    assert not checagens.liberacao_pelo_anuncio(
        "Sim, acompanha fone.",
        "conteudo_da_caixa",
        evidencias=["espec:1"],
        fichas=[ficha_],
        textos_cliente=["Acompanha fone?"],
    )


def test_a_negativa_com_a_palavra_da_equipe_contra_a_descricao_sai_pelo_anuncio():
    # A descrição cita a película; a equipe, na Shopee, nega: vale a equipe.
    ficha_ = _com_a_caixa(A17_SHOPEE, "shopee", descricao="Acompanha película.", atributos=[])
    assert checagens.liberacao_pelo_anuncio(
        "Não acompanha película.",
        "conteudo_da_caixa",
        evidencias=["espec:1"],
        fichas=[ficha_],
        textos_cliente=["vem película?"],
    )


@pytest.mark.parametrize(
    ("texto", "barra"),
    [
        ("Temos capinha para esse modelo em outro anúncio.", True),
        ("Vendemos película para este aparelho.", True),
        ("A capinha é vendida separadamente.", True),
        # O Fone Bluetooth UFB10 está à venda: o fone saiu do avulso (06/11/2026).
        ("Temos fone à venda.", False),
        ("Não temos capinha à venda.", False),
        ("No momento não trabalhamos com capinha nem película avulsas nesta loja.", False),
        ("Temos a opção com fone.", False),
    ],
)
def test_o_avulso_contra_a_equipe(texto, barra):
    motivos = _contra(texto, _com_a_caixa(A17_ML))
    assert bool([m for m in motivos if "avulso" in m]) is barra, motivos


def test_o_nao_temos_tem_a_palavra_da_equipe_como_fonte():
    ctx = SimpleNamespace(
        registro=SimpleNamespace(textos=[]), buscas_catalogo=[], fichas_lidas=[], achados_citados=[]
    )
    com = checagens.conferir_outro_produto_da_loja(
        "Não temos capinha à venda.",
        acao="responder",
        evidencias=[],
        ctx=ctx,
        fichas=[_com_a_caixa(A17_ML)],
        fontes_extra=[A17_ML],
    )
    assert checagens.NAO_TEMOS_SEM_FONTE not in [p.codigo for p in com]
    # A mala não tem o campo: o "não temos capinha" continua pedindo a fonte de sempre.
    sem = checagens.conferir_outro_produto_da_loja(
        "Não temos capinha à venda.",
        acao="responder",
        evidencias=[],
        ctx=ctx,
        fichas=[_com_a_caixa(P7)],
        fontes_extra=[P7],
    )
    assert checagens.NAO_TEMOS_SEM_FONTE in [p.codigo for p in sem]


# ═══════════════ o verificador ═══════════════


def test_o_corte_do_verificador_nao_come_a_palavra_da_equipe():
    # A descrição longa vem antes das especificações (chaves em ordem alfabética).
    ficha_ = _com_a_caixa(A17_ML, descricao="Celular resistente. " * 400, atributos=["Marca: X"])
    bruto = json.dumps([ficha_], ensure_ascii=False, sort_keys=True)
    assert len(bruto) > verificador.MAX_CHARS_EVIDENCIA
    (cortada, *resto) = verificador._evidencias([bruto], [])
    assert '"campo": "carregador"' not in cortada
    assert any(
        '"campo": "carregador"' in x and '"fonte": "equipe"' in x and e.VEM_NA_CAIXA in x
        for x in resto
    ), resto
    assert not any('"campo": "ram"' in x for x in resto)


def test_a_evidencia_que_cabe_nao_repete_a_palavra_da_equipe():
    bruto = json.dumps([_com_a_caixa(A17_ML)], ensure_ascii=False, sort_keys=True)
    assert verificador._evidencias([bruto], []) == [" ".join(bruto.split())]


def test_o_ver_anuncio_longo_tambem_leva_a_palavra_da_equipe():
    dado = {**_com_a_caixa(P7), "descricao": "Mala rígida. " * 500}
    bruto = json.dumps(dado, ensure_ascii=False, sort_keys=True)
    (_cortada, *resto) = verificador._evidencias([bruto], [])
    assert any('"campo": "rodas_removiveis"' in x for x in resto), resto


# ═══════════════ o modelo vê, de ponta a ponta ═══════════════

S7_SHOPEE = (
    "Uranyx S7 16.128 GB ou 32.256 GB Tela Traseira Android 16 - Preto + Acessorios,16/128 GB"
)


async def test_e2e_o_dado_chega_ao_modelo_sem_ferramenta_e_a_resposta_certa_sai(
    db, make_user, agente_ligado, api, envios, monkeypatch
):
    conversa, _integ, _r = await _conversa_do(
        db, make_user, monkeypatch, S7_SHOPEE, "Celular com 2 chips.", pergunta="Vem capinha?"
    )
    api.roteiro = [
        _final_e2e(
            texto="Sim, a capinha vem na caixa.",
            assunto="conteudo_da_caixa",
            confianca=0.9,
            evidencias=[f"espec:{ITEM_S7}"],
        )
    ]

    rascunho = await ia_svc.gerar_rascunho(db, conversa)

    # Na 1ª mensagem do modelo, sem chamar ferramenta; na Shopee a película não vem.
    pacote = api.pedidos[0]["messages"][0]["content"]
    assert '{"campo":"capinha","fonte":"equipe","valor":"vem na caixa"}' in pacote
    assert '{"campo":"pelicula","fonte":"equipe","valor":"não vem na caixa"}' in pacote
    assert "chave_chip" not in pacote
    # E o verificador recebe a mesma linha como evidência.
    assert '"campo":"capinha"' in api.verificador[0]["messages"][0]["content"]
    assert [m["texto"] for m in envios] == [rascunho.texto], rascunho.motivo
    assert _decisao(rascunho) == "enviado", rascunho.motivo


@pytest.mark.parametrize(
    ("pergunta", "texto", "titulo"),
    [
        (
            "vem capinha?",
            "Olá! O anúncio informa que acompanha o celular; capinha não consta entre os itens.",
            S7_SHOPEE,
        ),
        (
            "Vem cabo?",
            "O kit traz o aparelho e o fone. O cabo não aparece entre os itens listados.",
            S7_SHOPEE,
        ),
    ],
)
async def test_e2e_a_negativa_do_que_a_equipe_confirma_vai_para_pessoa(
    db, make_user, agente_ligado, api, envios, monkeypatch, pergunta, texto, titulo
):
    conversa, _integ, _r = await _conversa_do(
        db, make_user, monkeypatch, titulo, "Celular com 2 chips.", pergunta=pergunta
    )
    api.roteiro = [
        _final_e2e(
            texto=texto,
            assunto="conteudo_da_caixa",
            confianca=0.9,
            evidencias=[f"ficha:{ITEM_S7}"],
        )
    ]

    rascunho = await ia_svc.gerar_rascunho(db, conversa)

    assert envios == [], rascunho.motivo
    assert rascunho.precisa_humano is True
    assert "mas a equipe diz que vem neste modelo" in (rascunho.motivo or "")


async def test_e2e_a_negativa_do_que_a_equipe_nega_sai(
    db, make_user, agente_ligado, api, envios, monkeypatch
):
    conversa, _integ, _r = await _conversa_do(
        db, make_user, monkeypatch, S7_SHOPEE, "Celular com 2 chips.", pergunta="Vem película?"
    )
    api.roteiro = [
        _final_e2e(
            texto="Não, a película não vem na caixa.",
            assunto="conteudo_da_caixa",
            confianca=0.9,
            evidencias=[f"espec:{ITEM_S7}"],
        )
    ]

    rascunho = await ia_svc.gerar_rascunho(db, conversa)

    assert [m["texto"] for m in envios] == [rascunho.texto], rascunho.motivo
    assert _decisao(rascunho) == "enviado", rascunho.motivo


async def test_e2e_o_ver_anuncio_tambem_devolve_a_linha(
    db, make_user, agente_ligado, api, envios, monkeypatch
):
    conversa, _integ, _r = await _conversa_do(
        db, make_user, monkeypatch, S7_SHOPEE, "Celular com 2 chips.", pergunta="Vem capinha?"
    )
    _modelo(
        api,
        texto="Sim, a capinha vem na caixa.",
        item=ITEM_S7,
        assunto="conteudo_da_caixa",
    )

    await ia_svc.gerar_rascunho(db, conversa)

    resposta_da_ferramenta = json.dumps(api.pedidos[1]["messages"], ensure_ascii=False)
    assert "capinha" in resposta_da_ferramenta and "vem na caixa" in resposta_da_ferramenta


# ═══════════════ a revisão da caixa e da mala (06/11/2026) ═══════════════

P2_LIMPA = "Mala De Bordo 20 Pol 10kg Polipropileno Tsa Rodas 360 Preto P2"
P1_LIMPA = "Mala De Viagem 20 Pol Pequena De Bordo Polipropileno - P1 - Bege"
P7_24 = "Mala De Viagem 24 Pol Média De Despacho Polipropileno - Preto P7"


def _l1(
    fala: str, texto: str, titulo: str, assunto: str, plataforma: Any = "ml", **kw: Any
) -> bool:
    return checagens.liberacao_pelo_anuncio(
        texto,
        assunto,
        evidencias=["espec:1"],
        fichas=[_com_a_caixa(titulo, plataforma)],
        textos_cliente=[fala],
        **kw,
    )


@pytest.mark.parametrize(
    ("fala", "texto", "titulo", "plataforma", "contra"),
    [
        # O "não tem" é negativa: a L1 abria e nada segurava, nos dois sentidos.
        ("vem capinha?", "Não tem capinha.", A17_ML, "ml", True),
        ("vem capinha?", "Não possui capinha.", A17_ML, "ml", True),
        ("vem capinha?", "Não conta com capinha.", A17_ML, "ml", True),
        ("vem cabo?", "Não tem cabo.", A17_ML, "ml", True),
        ("vem capinha?", "Não tem capinha.", WP60_ML, "ml", False),
        ("vem capinha?", "Não tem capinha.", F110_PRO_ML, "ml", False),
        ("vem pelicula?", "Não tem película.", A17_SHOPEE, "shopee", False),
        ("vem capinha?", "Tem capinha no kit.", WP60_ML, "ml", True),
        ("vem capinha?", "Há capinha na caixa.", WP60_ML, "ml", True),
        # O "tem" depois do item não diz que ele vem.
        ("a capinha tem cor?", "A capinha tem cor preta.", WP60_ML, "ml", False),
        ("vem pelicula?", "Vem sem capinha nem película.", A17_ML, "ml", True),
        ("vem pelicula?", "Acompanha o carregador, mas não a película.", A17_ML, "ml", True),
        # A evasiva sem o nome do item, na pergunta sobre ele.
        ("Acompanha carregador?", "Não consta.", A17_ML, "ml", True),
        ("Acompanha carregador?", "Não temos essa informação.", A17_ML, "ml", True),
        ("Acompanha carregador?", "O anúncio não informa.", A17_ML, "ml", True),
        ("Acompanha carregador?", "Não consta no anúncio.", A17_ML, "ml", True),
        ("Acompanha carregador?", "Não consigo confirmar.", A17_ML, "ml", True),
        # Ela não contraria o que a equipe nega.
        ("vem capinha?", "Não consta.", WP60_ML, "ml", False),
        # A pergunta com o "tem" e sem verbo.
        ("tem carregador?", "Não tem.", A17_ML, "ml", True),
        ("tem carregador?", "Não vem.", A17_ML, "ml", True),
        ("tem carregador?", "Não consta.", A17_ML, "ml", True),
        ("Com carregador?", "Não.", A17_ML, "ml", True),
        ("e o carregador?", "Não vem.", A17_ML, "ml", True),
        ("Tem capinha?", "O Hotwav A17 não acompanha.", A17_ML, "ml", True),
        ("tem capinha?", "Sim.", WP60_ML, "ml", True),
        ("tem capinha?", "Tem sim.", WP60_ML, "ml", True),
        ("tem carregador?", "Sim, tem.", A17_ML, "ml", False),
        # A fala que cita o item e não pergunta se ele vem.
        ("o carregador é bivolt?", "Não.", A17_ML, "ml", False),
        ("tem capinha pra vender?", "Não.", A17_ML, "ml", False),
        ("tem capinha colorida?", "Não.", A17_ML, "ml", False),
        ("qual o prazo?", "Chega em 3 dias.", A17_ML, "ml", False),
        # O "verifique" e o "caso" não são "não vem": fala da tomada, do defeito.
        (
            "não está carregando",
            "Verifique se o carregador está conectado na tomada.",
            A17_ML,
            "ml",
            False,
        ),
        (
            "não está carregando",
            "Confira se o cabo e o carregador estão bem conectados.",
            A17_ML,
            "ml",
            False,
        ),
        (
            "não carrega",
            "Caso o carregador tenha vindo com defeito, abra a devolução pelo app.",
            A17_ML,
            "ml",
            False,
        ),
        (
            "não veio a película",
            "Confira se a película já está aplicada na tela.",
            A17_ML,
            "ml",
            False,
        ),
        # Outra coisa do item.
        (
            "o carregador é de quantos w?",
            "O anúncio não informa a potência do carregador.",
            A17_ML,
            "ml",
            False,
        ),
        (
            "o carregador é bivolt?",
            "Não consta se o carregador é bivolt.",
            A17_ML,
            "ml",
            False,
        ),
        ("a pelicula é de vidro?", "Não consta o material da película.", A17_ML, "ml", False),
        # O "se vem" continua evasiva.
        ("vem cabo?", "Não consta se o cabo vem.", S7_ML, "ml", True),
        ("vem carregador?", "Confira no anúncio se ele tem carregador.", A17_ML, "ml", True),
        # A omissão.
        ("Acompanha carregador?", "Vem o aparelho e o fone.", A17_ML, "ml", True),
        ("Acompanha carregador?", "O kit contém o aparelho e o fone.", A17_ML, "ml", True),
        ("Acompanha carregador?", "Vem só o aparelho.", A17_ML, "ml", True),
        ("Acompanha carregador?", "Acompanha apenas o aparelho celular.", A17_ML, "ml", True),
        (
            "Acompanha carregador?",
            "O carregador não está listado entre os itens.",
            A17_ML,
            "ml",
            True,
        ),
        ("Acompanha carregador?", "Não há menção a carregador no anúncio.", A17_ML, "ml", True),
        ("Acompanha carregador?", "Não encontrei menção de carregador.", A17_ML, "ml", True),
        # O real de 05/11.
        (
            "vem com o carregador USB-C incluso na caixa?",
            "O anúncio informa que tem conector de carregamento USB-C.",
            WP60_ML,
            "ml",
            True,
        ),
        ("vem carregador?", "Pode conferir na página do anúncio.", A17_ML, "ml", True),
        # A omissão do que a equipe nega é a resposta certa, e a pergunta de volta não afirma nada.
        ("vem capinha?", "Vem o aparelho e o carregador.", WP60_ML, "ml", False),
        ("vem carregador?", "Você quer saber se vem carregador?", A17_ML, "ml", False),
        # Comprar à parte é negar a caixa, a não ser para quem quer comprar.
        ("Acompanha carregador?", "O carregador é vendido separadamente.", A17_ML, "ml", True),
        ("Acompanha carregador?", "Você precisa comprar o carregador.", A17_ML, "ml", True),
        (
            "Acompanha carregador?",
            "O carregador precisa ser comprado separado.",
            A17_ML,
            "ml",
            True,
        ),
        ("vem carregador?", "O carregador é à parte.", A17_ML, "ml", True),
        (
            "vem carregador?",
            "Não precisa comprar o carregador, ele vem na caixa.",
            A17_ML,
            "ml",
            False,
        ),
        ("vocês vendem carregador?", "O carregador é vendido separadamente.", A17_ML, "ml", False),
        (
            "quero comprar outro carregador",
            "O carregador é vendido separadamente.",
            A17_ML,
            "ml",
            False,
        ),
        # O que a equipe nega, afirmado sem verbo ou com o verbo do envio.
        ("vem capinha?", "Com capinha sim!", WP60_ML, "ml", True),
        ("vem capinha?", "Com capinha.", WP60_ML, "ml", True),
        ("vem capinha?", "Na caixa: aparelho, capinha e cabo.", WP60_ML, "ml", True),
        ("vem capinha?", "Mandamos capinha junto.", WP60_ML, "ml", True),
        ("vem capinha?", "Ganha uma capinha.", WP60_ML, "ml", True),
        ("vem capinha?", "Você recebe capinha.", WP60_ML, "ml", True),
        ("vem capinha?", "Enviamos sim.", WP60_ML, "ml", True),
        ("e a capinha?", "Vai junto.", WP60_ML, "ml", True),
        ("vem capinha?", "Vem sim, inclusive com capinha.", WP60_ML, "ml", True),
        ("vem pelicula?", "Sai de fábrica com película.", A17_SHOPEE, "shopee", True),
        # O "sem carregador de fábrica" continua sendo o do brinde.
        (
            "cade o carregador?",
            "O aparelho vem sem carregador de fábrica, e o que enviamos foi um brinde.",
            A17_ML,
            "ml",
            False,
        ),
        # O estojo do fone não é a capinha.
        ("vem fone?", "O fone vem com case de carregamento.", WP60_ML, "ml", False),
        (
            "vem fone?",
            "Acompanha o fone UFB10, com case de carregamento e cabo tipo-C.",
            WP60_ML,
            "ml",
            False,
        ),
        ("vem capinha?", "Sim, vem com case.", WP60_ML, "ml", True),
        # "Temos de brinde" é o que vem, não o que se vende.
        ("vem capinha?", "Temos capinha de brinde neste modelo.", A17_ML, "ml", False),
        ("tem capinha?", "Temos capinha sim, vem de brinde.", A17_ML, "ml", False),
        ("vem fone?", "Temos fone no kit.", A17_ML, "ml", False),
        # As respostas e as falas reais de 150 dias: nada disso fala do que vem na caixa.
        ("vem pelicula?", "Enviamos amanhã.", A17_SHOPEE, "shopee", False),
        ("vem pelicula?", "A sua mensagem foi recebida.", A17_SHOPEE, "shopee", False),
        ("O carregador veio errado", "Abra a devolução pelo app.", A17_ML, "ml", False),
        ("Vc tem a capinha", "Não temos.", A17_ML, "ml", False),
        (
            "Esse valor é apenas da capa ou o aparelho está incluso?",
            "Esse valor se refere ao celular e todos os acessórios.",
            A17_ML,
            "ml",
            False,
        ),
        # A omissão também vale com a pergunta longa, e o "veio ...?" pergunta.
        ("veio carregador?", "Não.", A17_ML, "ml", True),
        (
            "Caixa uraxyn sem carregador??",
            "Acompanha película, cabo de carregamento, fone e chave chip.",
            MARINE_ML,
            "ml",
            True,
        ),
        ("vem carregador?", "Enviamos amanhã.", A17_ML, "ml", True),
        # O próprio nome e "esta versão" são o modelo da conversa.
        ("tem capinha?", "O Redmi 15C não vem com capinha.", REDMI_ML, "ml", True),
        ("tem capinha?", "O Xiaomi Redmi 15C não acompanha capinha.", REDMI_ML, "ml", True),
        ("tem capinha?", "O Redmi 15C vem com capinha.", REDMI_ML, "ml", False),
        ("vem capinha?", "Esta versão vem com capinha.", WP60_ML, "ml", True),
        ("vem capinha?", "Esta variação acompanha capinha.", WP60_ML, "ml", True),
        ("tem carregador?", "Esta versão não acompanha carregador.", A17_ML, "ml", True),
    ],
)
def test_a_revisao_o_que_a_resposta_diz_da_caixa(fala, texto, titulo, plataforma, contra):
    motivos = _contra(texto, _com_a_caixa(titulo, plataforma), fala)
    assert bool(motivos) is contra, motivos


@pytest.mark.parametrize(
    ("fala", "texto", "titulo", "barra"),
    [
        # A evasiva sem o nome: a rodinha, o padrão ANAC e o material que a equipe confirma.
        ("a rodinha é removível?", "O anúncio não informa.", P7_24, True),
        ("a rodinha é removível?", "Não consta no anúncio.", P7_24, True),
        ("a rodinha é removível?", "Não temos essa informação.", P7_24, True),
        ("é padrão anac?", "O anúncio não informa.", M2_18, True),
        ("é padrão anac?", "Não temos essa informação.", M2_18, True),
        ("qual material?", "O anúncio não informa.", P1_LIMPA, True),
        ("qual material?", "Não consta no anúncio.", P1_LIMPA, True),
        # O que a equipe nega: evasiva não a contraria.
        ("a rodinha é removível?", "O anúncio não informa.", P2_LIMPA, False),
        # As formas que a leitura não via.
        ("é abs?", "Sim, é ABS.", P1_LIMPA, True),
        ("é abs?", "Sim.", P1_LIMPA, True),
        ("é abs?", "Não.", P1_LIMPA, False),
        ("é abs?", "Não, é polipropileno.", P1_LIMPA, False),
        ("qual material?", "ABS.", P1_LIMPA, True),
        ("é polipropileno?", "Não.", P1_LIMPA, True),
        ("é polipropileno?", "Sim.", P1_LIMPA, False),
        ("as rodinhas saem?", "As rodinhas não saem.", P7_24, True),
        ("as rodinhas saem?", "Não saem.", P7_24, True),
        ("dá pra tirar a roda?", "Não dá.", P7_24, True),
        ("dá pra tirar a roda?", "Dá sim.", P7_24, False),
        ("a rodinha é removível?", "É sim.", P2_LIMPA, True),
        ("a rodinha é removível?", "São sim.", P2_LIMPA, True),
        ("dá pra tirar as rodas?", "Dá sim.", P2_LIMPA, True),
        ("a rodinha é removível?", "Não é.", P2_LIMPA, False),
        ("é de despacho?", "Sim.", M2_18, True),
        ("é de despacho?", "Não.", M2_18, False),
        ("é de despacho?", "Não.", M6_24, True),
        # A regra de todas as malas e o outro tamanho não dizem nada desta (a equipe, 150 dias).
        (
            "são liberadas pela anac?",
            "Sim, para mala de despacho você pode usar até 30 polegadas. Para mala de bordo até "
            "20 polegadas.",
            M6_24,
            False,
        ),
        (
            "As rodinhas são removíveis?",
            "Não, somente o tamanho 18 polegadas tem rodas removíveis.",
            "Mala Pequena De Viagem 20 Polegadas De Bordo M2 - Roxo Escuro",
            False,
        ),
        # O link não é modelo citado.
        (
            "Qual material?",
            "É polipropileno, veja outra cor https://x.com/mala-de-viagem-p3-abs-verde",
            P1_LIMPA,
            False,
        ),
        # O "confira" que não fala da mala.
        ("e a anac?", "Confira as regras da ANAC na sua companhia aérea.", M2_18, False),
        ("despacho?", "Se for despachar, proteja com plástico.", P7_24, False),
    ],
)
def test_a_revisao_o_que_a_resposta_diz_da_mala(fala, texto, titulo, barra):
    motivos = _contra(texto, _com_a_caixa(titulo), fala)
    assert bool(motivos) is barra, motivos


@pytest.mark.parametrize(
    ("fala", "texto", "titulo"),
    [
        ("é abs?", "Sim, é ABS.", P1_LIMPA),
        ("é abs?", "Sim.", P1_LIMPA),
        ("qual material?", "ABS.", P1_LIMPA),
        ("as rodinhas saem?", "As rodinhas não saem.", P7_24),
        # O que a leitura não lê fica como era antes da linha.
        (
            "qual a roda?",
            "As rodinhas têm freio.",
            "Mala De Bordo 20 Pol 10kg Polipropileno Preto P2",
        ),
    ],
)
def test_a_revisao_so_o_que_a_leitura_confirmou_abre_a_l1(fala, texto, titulo):
    assert not _l1(fala, texto, titulo, "espec_mala")


@pytest.mark.parametrize(
    ("fala", "texto", "titulo", "plataforma"),
    [
        ("vem capinha?", "Não.", WP60_ML, "ml"),
        ("Acompanha carregador?", "Sim.", A17_ML, "ml"),
        ("tem capinha?", "Tem sim.", A17_ML, "ml"),
        ("vem pelicula?", "Não tem película.", A17_SHOPEE, "shopee"),
    ],
)
def test_a_revisao_a_resposta_certa_curta_sai_pelo_anuncio(fala, texto, titulo, plataforma):
    assert _l1(fala, texto, titulo, "conteudo_da_caixa", plataforma)


@pytest.mark.parametrize(
    ("titulo", "texto", "sai"),
    [
        (A17_ML, "Sim, acompanha capinha, carregador, película e cabo.", True),
        # Fone e relógio não têm veredito: a lista com eles fica como antes da linha.
        (A17_ML, "Sim, acompanha capinha, carregador, película, cabo e fone.", False),
        (A17_ML, "Acompanha capinha, carregador e relógio.", False),
        (A17_ML, "Acompanha capinha, carregador e AirTag.", False),
        # O título diz que o fone vem: tem fonte.
        (S5_ML, "Sim, acompanha capinha, cabo e fone.", True),
    ],
)
def test_a_lista_com_o_que_a_equipe_nao_diz_nao_sai_pelo_anuncio(titulo, texto, sai):
    ficha_ = _com_a_caixa(titulo, descricao="Celular resistente.", atributos=["Marca: X"])
    assert (
        checagens.liberacao_pelo_anuncio(
            texto,
            "conteudo_da_caixa",
            evidencias=["espec:1"],
            fichas=[ficha_],
            textos_cliente=["o que vem?"],
        )
        is sai
    )


def test_a_nota_das_medidas_nao_e_fonte_da_rodinha():
    titulo = "Mala De Bordo 20 Pol 10kg Polipropileno Preto P2"
    ficha_ = _com_a_caixa(titulo)
    ficha_["especificacoes"]["campos"].append(
        {"campo": "dimensoes", "valor": "53 x 34", "fonte": "anuncio", "nota": e.NOTA_MEDIDAS}
    )
    assert not checagens.liberacao_pelo_anuncio(
        "As rodinhas têm freio.",
        "espec_mala",
        evidencias=["espec:1"],
        fichas=[ficha_],
        textos_cliente=["qual a roda?"],
    )


@pytest.mark.parametrize(
    ("fala", "texto", "contra"),
    [
        # A equipe nega o WP60 e o F110 Pro; a linha é do A17 e não empresta nada.
        ("o WP60 vem capinha?", "Sim, o WP60 vem com capinha.", True),
        ("o WP60 vem capinha?", "Sim, vem com capinha.", True),
        ("o F110 Pro vem capinha?", "Sim, o F110 Pro vem com capinha.", True),
        # A resposta certa sobre ele.
        ("o WP60 vem capinha?", "Não, o WP60 não vem com capinha.", False),
        # Sem o cliente citar, a oração fala do modelo que ela cita.
        ("vem carregador?", "O A18 não vem com carregador.", True),
        # O C2 não tem veredito do carregador: como antes da linha.
        ("o C2 vem com carregador?", "Sim, o C2 acompanha carregador.", False),
    ],
)
def test_a_oracao_de_outro_modelo_vale_o_veredito_dele(fala, texto, contra):
    motivos = _contra(texto, _com_a_caixa(A17_ML), fala)
    assert bool(motivos) is contra, motivos
    assert not _l1(fala, texto, A17_ML, "conteudo_da_caixa")


def test_a_linha_da_conversa_nao_e_fonte_do_outro_modelo():
    (p,) = checagens.conferir_fonte_produto(
        "Sim, o C2 acompanha carregador.",
        [_com_a_caixa(A17_ML)],
        None,
        textos_cliente=["o C2 vem com carregador?"],
    )
    assert p.codigo == checagens.PRODUTO_SEM_FONTE
    assert checagens._da_equipe_do_turno([_com_a_caixa(A17_ML)], [], "o c2 tem capinha", []) == {}


def test_a_outra_marca_tambem_nao_empresta_a_linha():
    for fala, texto in (
        ("o iphone vem com carregador?", "Sim, vem com carregador."),
        ("o Redmi 14C vem com capinha?", "Sim, o Redmi 14C vem com capinha."),
    ):
        assert not _l1(fala, texto, A17_ML, "conteudo_da_caixa")
    # O Redmi 14C não tem código, e o título da conversa também é Redmi.
    assert not _l1(
        "o Redmi 14C vem com capinha?",
        "Sim, o Redmi 14C vem com capinha.",
        REDMI_ML,
        "conteudo_da_caixa",
    )


def test_a_mala_de_outro_modelo_vale_o_veredito_dela():
    fala = "a P7 tem roda removível?"
    assert _contra("Não, a P7 não tem roda removível.", _com_a_caixa(P2_LIMPA), fala)
    assert not _contra("Sim, a P7 tem roda removível.", _com_a_caixa(P2_LIMPA), fala)


@pytest.mark.parametrize(
    ("nome", "codigos"),
    [
        ("Mala Onda P7 tamanho 20 - Rosa (OM - PPB810 - Pink - Premium)", {"p7"}),
        ("Mala Risca P2 tamanho 20 - Rosa (OM - PPB014 - Pink)", {"p2"}),
        ("Mala Losangulo M4 tamanho 18 - Cinza Escuro (DT - XS5426 - DT14)", {"m4"}),
        ("Mala Executivo ME2 tamanho 20 - Preto (OM - ALOB2058 - Black)", {"me2"}),
        ("Mala Brilho Listrada P1 tamanho 24 - Rose Gold (DT - PPDT802 - DT02)", {"p1"}),
    ],
)
def test_o_codigo_do_fornecedor_nao_e_modelo(nome, codigos):
    assert r.codigos_do(nome) == codigos


@pytest.mark.parametrize(
    ("titulo", "pedido", "vale"),
    [
        (P2_LIMPA, "Mala Risca P2 tamanho 20 - Rosa (OM - PPB014 - Pink)", True),
        (P7_24, "Mala Onda P7 tamanho 24 - Caqui (OM - PPB810 - Khaki)", True),
        # O S5 do título e o "A18 Pro Max - S5 Edition" do Bling são o mesmo aparelho.
        (S5_ML, "Uranyx A18 Pro Max - S5 Edition 16.128 - Prata + Relógio", True),
        (A17_ML, "Uranyx A18 Pro Max - S5 Edition 16.128 - Prata + Relógio", False),
        (P2_LIMPA, "Mala Onda P7 tamanho 20 - Rosa (OM - PPB810 - Pink)", False),
        # O título sem código não diz que o pedido é outro: vale o padrão ANAC, que é pelo tamanho.
        ("Mala De Bordo 20 Polegadas 10Kg (Azul Escuro)", "Mala Chanfrada M3 tamanho 20", True),
    ],
)
def test_o_pedido_da_conversa(titulo, pedido, vale):
    assert bool(checagens._da_equipe_na_conversa([_com_a_caixa(titulo)], [pedido])) is vale


def test_o_pedido_com_o_codigo_do_fornecedor_nao_desliga_a_trava():
    pendencias = checagens.conferir_fonte_produto(
        "Sim, as rodinhas são removíveis.",
        [_com_a_caixa(P2_LIMPA)],
        None,
        fontes_extra=["Mala Risca P2 tamanho 20 - Rosa (OM - PPB014 - Pink)"],
        textos_cliente=["as rodinhas são removíveis?"],
    )
    assert checagens.CONTRA_A_EQUIPE in [p.codigo for p in pendencias]


# ═══════════════ o título, o cadastro e a condição (a simulação de 06/11/2026) ═══════════════


@pytest.mark.parametrize(
    ("titulo", "nome", "nao_casa"),
    [
        # O modelo trocado: não se sabe qual dos dois está errado.
        ("Mala De Bordo 20 Polegadas 10Kg M2 (Roxo Escuro)", "Mala Chanfrada M3 tamanho 20", True),
        (
            "Conjunto Mala De Bordo 18'' + Necessaire 10\" M2 (Champanhe)",
            "Kit Malas Chanfrada M3 tamanho 10.18",
            True,
        ),
        (
            "Uranyx F109 5g 256 Gb 24 Gb Ram + Airtag E Fone Preto",
            "Uranyx F109S 24.256 - Preto + Fone + AirTag",
            True,
        ),
        (
            "Mala De Viagem 24 ''Polegadas Média De Despacho M4 - Branco",
            "Mala M1 tamanho 24 - Branco + Acessórios",
            True,
        ),
        # O S5 do título é o "S5 Edition" do cadastro: o mesmo aparelho.
        (
            "Uranyx S5 16 Ram 128 Gb Memoria Laranja",
            "Uranyx A18 Pro Max - S5 Edition 16.128 - Laranja + Relogio",
            False,
        ),
        # O mesmo modelo, com o código do fornecedor.
        (P7_24, "Mala Onda P7 tamanho 24 - Caqui (OM - PPB810 - Khaki)", False),
        # Sem código em um dos lados não há o que comparar.
        ("Mala De Bordo 20 Polegadas 10Kg (Azul Escuro)", "Mala Chanfrada M3 tamanho 20", False),
        (P7_24, "Mala de viagem 24", False),
        # A condição que o título não diz.
        ("Xiaomi Redmi 15c Verde", "Redmi 15C 8.128 - Verde AVULSO SALVADO", True),
        (A17_ML, "Uranyx A17 Pro Max 12.128 - Preto - USADO", True),
        (P7_24, "Mala Onda P7 tamanho 24 - Caqui - Avariada", True),
        # O "AVULSO" sozinho é a venda sem o kit.
        (C57_PRO, "Uranyx C57 PRO 4+128 Preto AVULSO", False),
    ],
)
def test_o_titulo_que_nao_casa_com_o_cadastro(titulo, nome, nao_casa):
    assert r.nao_casa_com_o_cadastro(titulo, [nome]) is nao_casa


async def test_no_banco_o_modelo_trocado_fica_sem_a_caixa(db, make_user):
    ids = await _produtos(db, make_user, "Mala Chanfrada M3 tamanho 20 - Roxo Escuro")
    titulo = "Mala De Bordo 20 Polegadas 10Kg M2 (Roxo Escuro)"
    r_ = await e.especificacoes_do_produto(db, None, ids, titulo, IntegrationPlatform.AMAZON)
    assert not [c for c in r_["campos"] if c["fonte"] == e.FONTE_EQUIPE]


async def test_no_banco_o_salvado_fica_sem_a_caixa(db, make_user):
    ids = await _produtos(db, make_user, "Redmi 15C 8.128 - Verde AVULSO SALVADO")
    r_ = await e.especificacoes_do_produto(
        db, None, ids, "Xiaomi Redmi 15c Verde", IntegrationPlatform.ML
    )
    assert "capinha" not in [c["campo"] for c in r_["campos"]]


async def test_no_banco_o_s5_edition_continua_com_a_caixa(db, make_user):
    ids = await _produtos(
        db, make_user, "Uranyx A18 Pro Max - S5 Edition 16.128 - Laranja + Relogio"
    )
    r_ = await e.especificacoes_do_produto(
        db, None, ids, "Uranyx S5 16 Ram 128 Gb Memoria Laranja", IntegrationPlatform.ML
    )
    assert _linha(r_, "capinha")["fonte"] == e.FONTE_EQUIPE


@pytest.mark.parametrize(
    "titulo",
    [
        "Malas De Bordo ou Despacho Polegadas Pequenas Médias Avarias Completas - Diversas Cores - "
        "Prata M2 - 24 Polegadas",
        "Xiaomi Redmi 15c Verde Salvado",
    ],
)
def test_as_avarias_e_o_salvado_nao_sao_novos(titulo):
    assert r.fatos_da_equipe(titulo, None, "ml") == {}


def test_o_kit4_e_kit():
    titulo = (
        'Mala Pequena De Viagem 18" 10 kg De Bordo ABS Resistente com Cadeado M3 - KIT4 UNIDADES'
    )
    assert r.PADRAO_ANAC not in r.fatos_da_equipe(titulo, None, "shopee")


@pytest.mark.parametrize(
    ("descricao", "so_cabo"),
    [
        ("Caixa Slim, o aparelho acompanha apenas o cabo de carregamento.", True),
        ("Acompanha somente o cabo.", True),
        ("Vem só um cabo USB.", True),
        ("Caixa Slim.", True),
        ("Acompanha carregador, cabo e película.", False),
        ("O cabo é reforçado.", False),
        (None, False),
    ],
)
def test_o_anuncio_que_diz_so_o_cabo(descricao, so_cabo):
    assert e._anuncio_so_com_o_cabo({"descricao": descricao}) is so_cabo


def test_o_anuncio_so_com_o_cabo_tira_so_o_carregador_da_equipe():
    """06/11/2026: 120 anúncios da Shopee dizem "apenas o cabo", e o comprador lê a descrição."""
    com = e.aplicar_a_equipe({"campos": [], "conflitos": []}, A17_ML, None, "ml")
    sem = e.aplicar_a_equipe(
        {"campos": [], "conflitos": []}, A17_ML, None, "ml", sem_carregador=True
    )
    assert _linha(com, "carregador")["valor"] == e.VEM_NA_CAIXA
    assert not [c for c in sem["campos"] if c["campo"] == "carregador"]
    assert _linha(sem, "capinha")["valor"] == e.VEM_NA_CAIXA


def test_a_nota_do_avulso_nao_cita_o_fone():
    assert "fone" not in e.NOTA_AVULSO
