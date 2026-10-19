"""A mesma política em lojas distintas; nenhum fato de uma loja vaza para outra."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.atendimento import politicas_atendimento as politica


@pytest.fixture
def regras():
    return [dict(r, id=f"regra-{i}", ativa=True) for i, r in enumerate(politica.REGRAS)]


def _conversa(plataforma="shopee", canal="chat", **campos):
    return SimpleNamespace(
        **(
            {
                "integration_id": "loja-qualquer",
                "plataforma": plataforma,
                "canal": canal,
                "anuncio_id": "anuncio-desta-loja",
                "pedido_marketplace": None,
                "dados": {},
            }
            | campos
        )
    )


def _ficha(plataforma="shopee", **campos):
    return {
        "fonte": f"loja ({politica._PLATAFORMAS[plataforma]}), agora",
        "de_onde": "anúncio ligado à conversa",
        "titulo": "Relógio deste anúncio - Novo",
    } | campos


def _envio(plataforma="shopee", canal="chat"):
    conversa = _conversa(plataforma, canal, pedido_marketplace="PEDIDO-DESTA-LOJA")
    conversa.dados = {
        "pedido_mkt": {
            "fonte": plataforma,
            "pedido": conversa.pedido_marketplace,
            "atualizado_em": datetime.now(UTC).isoformat(),
            "pago_em": "2026-10-18T23:50:20Z",
            "status": "paid" if plataforma == "ml" else "READY_TO_SHIP",
            "logistica": {"status": "handling"},
        }
    }
    fatos = {
        "pedido_informado_na_conversa": True,
        "pedido": {"situacao_no_sistema": "Em digitação"},
        "entrega": {"status": "Pedido em preparação", "entregue": False},
    }
    return conversa, fatos


@pytest.mark.parametrize("loja", ["loja-a", "loja-b"])
@pytest.mark.parametrize(
    "plataforma,canal",
    [("shopee", "chat"), ("ml", "pergunta"), ("ml", "pos_venda"), ("tiktok", "chat")],
)
def test_politicas_iguais_para_lojas_e_plataformas_distintas(loja, plataforma, canal, regras):
    conversa = _conversa(plataforma, canal, integration_id=loja)
    fatos = {"ficha_do_anuncio": [_ficha(plataforma, atributos=["Tamanho da pulseira: padrão"])]}
    for pergunta, categoria, trecho in [
        ("A pulseira é P/M ou M/G?", "espec_outros", "padrão"),
        ("Pode mandar foto do modelo?", "pedido_link_midia", "fotos do próprio anúncio"),
        ("Este produto é novo ou recondicionado?", "originalidade_condicao", "produto é novo"),
    ]:
        resposta = politica.resposta_aprovada(conversa, [pergunta], fatos, regras)
        assert resposta is not None and resposta.categoria == categoria
        assert trecho in resposta.texto and resposta.fato["integration_id"] == loja


@pytest.mark.parametrize(
    "plataforma,canal,nome",
    [
        ("shopee", "chat", "pela Shopee"),
        ("ml", "pos_venda", "pelo Mercado Livre"),
        ("tiktok", "chat", "pelo TikTok Shop"),
    ],
)
def test_prazo_da_propria_plataforma_sem_copiar_origem(plataforma, canal, nome, regras):
    conversa, fatos = _envio(plataforma, canal)
    resposta = politica.resposta_aprovada(
        conversa,
        ["Já realizei o pedido, qual a previsão de entrega/envio?", "De onde estão enviando?"],
        fatos,
        regras,
    )
    assert resposta is not None and resposta.categoria == "prazo_envio"
    assert nome in resposta.texto
    assert "São Paulo" not in resposta.texto and "não está informada" in resposta.texto
    assert "número" not in resposta.texto and "vou" not in resposta.texto


def test_pack_ml_reconhece_order_id_proprio_e_rejeita_pedido_alheio(regras):
    conversa, fatos = _envio("ml", "pos_venda")
    conversa.dados["order_id"] = "ORDER-DO-PACK"
    conversa.dados["pedido_mkt"]["pedido"] = "ORDER-DO-PACK"
    assert politica.resposta_aprovada(conversa, ["Qual o prazo de entrega?"], fatos, regras)
    conversa.dados["pedido_mkt"]["pedido"] = "OUTRO-PEDIDO"
    assert politica.resposta_aprovada(conversa, ["Qual o prazo de entrega?"], fatos, regras) is None


@pytest.mark.parametrize(
    "campo,valor",
    [
        ("status", "SHIPPED"),
        ("pago_em", None),
        ("enviado_em", "2026-10-19"),
        ("concluido_em", "2026-10-19"),
        ("fonte", "outra plataforma"),
        ("atualizado_em", (datetime.now(UTC) - timedelta(days=2)).isoformat()),
    ],
)
def test_prazo_nao_ignora_pagamento_postagem_fonte_ou_idade(campo, valor, regras):
    conversa, fatos = _envio()
    conversa.dados["pedido_mkt"][campo] = valor
    assert politica.resposta_aprovada(conversa, ["Qual o prazo de entrega?"], fatos, regras) is None


@pytest.mark.parametrize(
    "pergunta",
    [
        "Pode mandar foto real do produto?",
        "Pode mandar outra foto do modelo?",
        "Pode mandar foto do modelo? É original?",
        "A pulseira é P/M ou M/G e serve no meu braço?",
        "Este produto é novo ou recondicionado? Quero cancelar.",
        "Qual o prazo de entrega? É urgente.",
        "Qual o prazo de entrega? Preciso receber até amanhã.",
    ],
)
def test_detalhe_extra_ou_demanda_mista_continua_no_fluxo_normal(pergunta, regras):
    conversa, fatos = _envio()
    fatos["ficha_do_anuncio"] = [_ficha(atributos=["Tamanho da pulseira: padrão"])]
    assert politica.resposta_aprovada(conversa, [pergunta], fatos, regras) is None


@pytest.mark.parametrize(
    "mudanca",
    [
        {"fonte": "outro anúncio do mesmo produto (Shopee)"},
        {"de_onde": "outro anúncio"},
        {"fonte": "loja (Shopee), há até três dias"},
        {"fonte": "loja (Mercado Livre), agora"},
    ],
)
def test_sem_fonte_propria_fresca_so_coleta_modelo(mudanca, regras):
    fatos = {"ficha_do_anuncio": [_ficha(**mudanca)]}
    resposta = politica.resposta_aprovada(_conversa(), ["É novo ou recondicionado?"], fatos, regras)
    assert resposta is not None and "confirmar qual é o modelo" in resposta.texto
    assert "produto é novo" not in resposta.texto


def test_sem_anuncio_pede_identificacao_respeitando_pergunta_publica(regras):
    for plataforma, canal in [("shopee", "chat"), ("ml", "pergunta"), ("tiktok", "chat")]:
        resposta = politica.resposta_aprovada(
            _conversa(plataforma, canal, anuncio_id=None),
            ["Pode mandar foto do modelo?"],
            {},
            regras,
        )
        assert resposta is not None and "confirmar qual é o modelo" in resposta.texto
        assert ("card" in resposta.texto) is (plataforma != "ml")


@pytest.mark.parametrize(
    "titulo,trecho",
    [
        ("Modelo deste anúncio - Novo", "O produto é novo"),
        ("Modelo deste anúncio - Recondicionado", "O produto é recondicionado"),
        ("Modelo deste anúncio", "confirmar qual é o modelo"),
        ("Modelo deste anúncio - Novo ou Recondicionado", "confirmar qual é o modelo"),
    ],
)
def test_condicao_so_literal_do_titulo_proprio(titulo, trecho, regras):
    resposta = politica.resposta_aprovada(
        _conversa(),
        ["É novo ou recondicionado?"],
        {"ficha_do_anuncio": [_ficha(titulo=titulo)]},
        regras,
    )
    assert resposta is not None and trecho in resposta.texto


def test_pulseira_padrao_so_com_produtos_aprovados_vinculados(regras):
    ficha = _ficha(
        titulo="Apple Watch Series 11",
        produtos_vinculados=[
            {"product_id": produto, "sku": "SKU-DA-LOJA", "variation_id": str(i)}
            for i, produto in enumerate(politica.IDS_PRODUTOS_PULSEIRA_PADRAO)
        ],
    )
    resposta = politica.resposta_aprovada(
        _conversa(), ["A pulseira é P/M ou M/G?"], {"ficha_do_anuncio": [ficha]}, regras
    )
    assert (
        resposta is not None
        and resposta.texto == "Olá! A pulseira deste modelo tem tamanho padrão."
    )
    ficha["produtos_vinculados"].append({"product_id": "produto-nao-aprovado"})
    resposta = politica.resposta_aprovada(
        _conversa(), ["A pulseira é P/M ou M/G?"], {"ficha_do_anuncio": [ficha]}, regras
    )
    assert resposta is not None and "confirmar qual é o modelo" in resposta.texto


@pytest.mark.parametrize("descricao", ["Pulseira P/M", "A pulseira não tem tamanho padrão."])
def test_catalogo_nao_sobrepoe_tamanho_ou_negacao_da_ficha(descricao, regras):
    ficha = _ficha(
        descricao=descricao,
        produtos_vinculados=[{"product_id": next(iter(politica.IDS_PRODUTOS_PULSEIRA_PADRAO))}],
    )
    resposta = politica.resposta_aprovada(
        _conversa(), ["A pulseira é P/M ou M/G?"], {"ficha_do_anuncio": [ficha]}, regras
    )
    assert resposta is not None and "confirmar qual é o modelo" in resposta.texto


def test_aprovacao_original_kia_preservada_com_ficha_propria_sem_metadata(regras):
    conversa = _conversa(
        integration_id=politica._APROVACAO_WATCH[1], anuncio_id=politica._APROVACAO_WATCH[2]
    )
    resposta = politica.resposta_aprovada(
        conversa,
        ["A pulseira é P/M ou M/G?"],
        {"ficha_do_anuncio": [_ficha(titulo="Apple Watch Series 11")]},
        regras,
    )
    assert resposta is not None and "tamanho padrão" in resposta.texto


def test_anuncio_proprio_identificado_basta_para_orientar_galeria(regras):
    resposta = politica.resposta_aprovada(_conversa(), ["Pode mandar foto do modelo?"], {}, regras)
    assert resposta is not None and "fotos do próprio anúncio" in resposta.texto
    assert "modelo que você receberá" not in resposta.texto


def test_variacao_com_tamanho_explicito_nao_recebe_padrao_por_catalogo(regras):
    conversa = _conversa(dados={"pedido_mkt": {"itens": [{"variacao": "Cinza, M/G"}]}})
    ficha = _ficha(
        produtos_vinculados=[{"product_id": next(iter(politica.IDS_PRODUTOS_PULSEIRA_PADRAO))}]
    )
    assert (
        politica.resposta_aprovada(
            conversa, ["A pulseira é P/M ou M/G?"], {"ficha_do_anuncio": [ficha]}, regras
        )
        is None
    )


@pytest.mark.parametrize(
    "mudanca",
    [
        {"ativa": False},
        {"faca": "Somente pessoa."},
        {"prioridade": 1},
        {"plataforma": "shopee"},
        {"canal": "chat"},
        {"quando": "Outro escopo"},
    ],
)
def test_manual_editado_revoga_o_atalho(mudanca, regras):
    regra = next(r for r in regras if r["categoria"] == "originalidade_condicao")
    regra.update(mudanca)
    assert (
        politica.resposta_aprovada(_conversa(), ["É novo ou recondicionado?"], {}, regras) is None
    )


def test_manual_duplicado_revoga_atalho(regras):
    assert (
        politica.resposta_aprovada(
            _conversa(), ["É novo ou recondicionado?"], {}, regras + deepcopy(regras)
        )
        is None
    )


@pytest.mark.parametrize("campo", ["chamados_abertos", "devolucoes", "reclamacoes_abertas"])
def test_pendencia_sensivel_preserva_fluxo_normal(campo, regras):
    assert (
        politica.resposta_aprovada(_conversa(), ["É novo ou recondicionado?"], {campo: 1}, regras)
        is None
    )


@pytest.mark.parametrize(
    "plataforma,canal",
    [("shopee", "avaliacao"), ("ml", "reclamacao"), ("tiktok", "email"), ("temu", "chat")],
)
def test_canal_sem_essa_politica_nao_e_liberado(plataforma, canal, regras):
    assert (
        politica.resposta_aprovada(
            _conversa(plataforma, canal), ["É novo ou recondicionado?"], {}, regras
        )
        is None
    )
