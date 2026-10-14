# ruff: noqa: S105, F811 — chaves de teste de um provedor falso; fixtures importadas dos testes da IA
"""A FICHA DO ANÚNCIO nos FATOS da IA (14/10/2026, `ficha.py`).

O caso real: na Shopee da Kia o comprador mandou o cartão do "Uranyx A17
Pro Max" e perguntou "contém a função NFC?"; a IA só tinha o título e foi
para pessoa. A descrição do catálogo (importado em maio) diz "NFC - Não
Tem", mas o anúncio de hoje se chama "… S5 Premium NFC": a ficha de maio
responderia errado — por isso a ficha fresca (a que a loja devolveu ao
cartão) vem primeiro, e o catálogo velho ou com outro título não vale.

- a ficha por plataforma: Shopee (`attribute_list` + descrição, e o formato
  antigo dentro de `raw_data.item`), ML (`attributes`, sem descrição),
  TikTok (`product_attributes`), Amazon (só título = sem ficha);
- a descrição LIMPA: sem HTML, emoji, contato fora da loja, garantia,
  preço, prazo, devolução, condição/originalidade — e no teto;
- os atributos da pergunta primeiro, no teto; os que a IA não pode usar
  (garantia, condição, SKU, embalagem, homologação) fora;
- quais anúncios: o cartão do cliente (o mais novo), o da conversa, os
  itens do pedido — com o título atual — e o primeiro sem ficha encerra a
  lista;
- de onde: a ficha FRESCA (Redis, gravada pelo enriquecimento do cartão);
  o catálogo da própria plataforma só importado há até 30 dias e com o
  mesmo título; o mesmo produto em outro anúncio pelo `product_links`
  (TikTok/Magalu), só com vínculo vivo e com os produtos contidos, marcado
  com `fonte` e o título dos dois anúncios;
- resistência, condição e contato partido em frases não passam;
- nos FATOS da resposta (não na classificação, não no prompt de sistema),
  com teto próprio: as correções da equipe continuam todas;
- de ponta a ponta (modelo falso): a pergunta de NFC chega ao modelo com a
  ficha fresca e o título atual ("… S5 Premium NFC"), e não com o "NFC -
  Não Tem" de maio; no automático, a resposta pela ficha vai para pessoa.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoRegra,
    Integration,
    IntegrationPlatform,
    Listing,
    Product,
    ProductLink,
)
from app.security.cipher import encrypt_json
from app.services.atendimento import ficha, gravar, ia, validador
from app.services.atendimento.constantes import (
    ORIGEM_IA,
    RASCUNHO_BLOQUEADO,
    RASCUNHO_PENDENTE,
)
from tests.test_atendimento_ia import (  # noqa: F401 — fixtures
    ModeloSequencia,
    _conversa,
    _importar_manual_base,
    _manual_base_puro,
    _msg,
    _rascunhos,
    _saida,
    envios,
    ia_ligada,
    modelo,
)

ITEM_KIA = "22394778711"
# O título do anúncio HOJE (o cartão que os compradores mandam em outubro);
# o catálogo de maio ainda tem o "Uranyx Hotwav A17 Pro Max 12.128 ou 12.64".
TITULO_ATUAL = "Uranyx A17 Pro Max 12.64 / 12.128 GB / 16.128 S5 Premium NFC"
TITULO_DE_MAIO = "Uranyx Hotwav A17 Pro Max 12.128 ou 12.64 GB Memoria"

DESCRICAO_KIA = (
    "<p>Caixa Slim, o aparelho acompanha apenas o cabo de carregamento!</p>"
    "<p>Hotwav A17 Pro Max + Fone UFB10</p>"
    "Memoria Interna 128 GB OU 64 GB<br>"
    "Processador 8 Core 1.7 GHz<br/>"
    "Tela 6.75 120Hz\n"
    "✅ NFC - Não Tem\n"
    "Rede Movel 4G &amp; Wifi 5G\n"
    "Cancelamento de ruído ativo no fone\n"
    "ATENÇÃO: NOSSOS PRODUTOS SÃO FILMADOS E PESADOS ANTES DO ENVIO PARA EVITAR FRAUDE!\n"
    "OBS: Em caso de Devolução deverá ser enviado o produto e todos os acessórios.\n"
    "Garantia de 90 dias direto com a loja.\n"
    "90 dias contra defeitos de fabricação.\n"
    "Produto original e lacrado. Bateria de longa duração.\n"
    "Dúvidas? Chama no WhatsApp (11) 98765-4321\n"
    "Visite www.lojakia.com.br ou siga @lojakia\n"
    "De R$ 899,90 por apenas 699,90 no Pix!\n"
    "Envio em até 24 horas úteis.\n"
)

ATRIBUTOS_SHOPEE = [
    {
        "attribute_id": 101197,
        "original_attribute_name": "Registration ID",
        "attribute_value_list": [{"value_id": 0, "original_value_name": "082162518234"}],
    },
    {
        "attribute_id": 101237,
        "original_attribute_name": "Manufacturer",
        "attribute_value_list": [{"original_value_name": "Uranyx Industries Limited"}],
    },
    {
        "attribute_id": 100272,
        "original_attribute_name": "Battery Capacity",
        "attribute_value_list": [{"original_value_name": "5000", "value_unit": "mAh"}],
    },
    {
        "attribute_id": 100465,
        "original_attribute_name": "Storage Capacity",
        "attribute_value_list": [{"original_value_name": "128", "value_unit": "GB"}],
    },
    {
        "attribute_id": 100121,
        "original_attribute_name": "Warranty Duration",
        "attribute_value_list": [{"original_value_name": "3 Months"}],
    },
    {
        "attribute_id": 100370,
        "original_attribute_name": "Warranty Type",
        "attribute_value_list": [{"original_value_name": "Manufacturer Warranty"}],
    },
    {
        "attribute_id": 999,
        "original_attribute_name": "Vazio",
        "attribute_value_list": [],
    },
]

RAW_SHOPEE = {
    "item_id": int(ITEM_KIA),
    "item_name": "Uranyx Hotwav A17 Pro Max 12.128 ou 12.64 GB Memoria",
    "brand": {"brand_id": 1, "original_brand_name": "Uranyx"},
    "condition": "NEW",
    "description": DESCRICAO_KIA,
    "attribute_list": ATRIBUTOS_SHOPEE,
}


def _ml_attr(ident: str, nome: str, valor: str | None, valores: list[str] | None = None) -> dict:
    return {
        "id": ident,
        "name": nome,
        "value_name": valor,
        "values": [{"id": None, "name": v} for v in (valores or ([valor] if valor else []))],
    }


ATRIBUTOS_ML = [
    _ml_attr("ITEM_CONDITION", "Condição do item", "Novo"),
    _ml_attr("WITH_TV_TUNER", "Com sintonizador de TV", "Não"),
    _ml_attr("BRAND", "Marca", "Xiaomi"),
    _ml_attr("SELLER_SKU", "SKU", "x046.ra+a003.ra"),
    _ml_attr("GTIN", "Código universal de produto", "169218302112"),
    _ml_attr("WARRANTY_TIME", "Tempo de garantia", "90 dias"),
    _ml_attr("SELLER_PACKAGE_HEIGHT", "Altura da embalagem do vendor", "12 cm"),
    _ml_attr("CELLPHONES_ANATEL_HOMOLOGATION_NUMBER", "Número de homologação da Anatel", "342"),
    _ml_attr("IS_WATERPROOF", "É à prova d'água", "Não"),
    _ml_attr("IP_RATING", "Classificação IP", "IP53"),
    _ml_attr("ORIGIN", "Origem", "Argentina"),
    _ml_attr("INTERNAL_MEMORY", "Memória interna", "128 GB"),
    _ml_attr("RAM", "Memória RAM", "8 GB"),
    _ml_attr("WITH_NFC", "Com NFC", "Sim"),
    _ml_attr("COLOR", "Cor", None, ["Preto", "Azul"]),
    _ml_attr("ESIMS_NUMBER", "Quantidade de eSIMs", None),
]


# O item como a Shopee o devolve HOJE ao cartão (`get_item_base_info`).
ITEM_KIA_HOJE = {
    "item_id": int(ITEM_KIA),
    "item_name": TITULO_ATUAL,
    "brand": {"brand_id": 1, "original_brand_name": "Uranyx"},
    "description": (
        "Caixa Slim, o aparelho acompanha apenas o cabo de carregamento!\n"
        "Versões 12 GB: NFC - Não Tem\n"
        "Versão S5 Premium 16 GB: NFC - Tem\n"
        "Tela 6.75 120Hz\n"
        "Garantia de 90 dias direto com a loja.\n"
    ),
    "attribute_list": ATRIBUTOS_SHOPEE,
    "image": {"image_url_list": ["https://cf.shopee.com.br/file/x"]},
    "price_info": [{"current_price": 899.9}],
}


class RedisFicha:
    """O pedaço do Redis que a ficha fresca lê (`ficha._ficha_fresca`), em memória."""

    def __init__(self) -> None:
        self.dados: dict[str, str] = {}

    async def get(self, chave: str) -> str | None:
        return self.dados.get(chave)

    def guardar(self, plataforma: str, integration_id, item_id: str, bruta: dict | None) -> None:
        assert bruta is not None
        self.dados[ficha.chave_ficha(plataforma, integration_id, item_id)] = json.dumps(
            bruta, ensure_ascii=False
        )


@pytest.fixture(autouse=True)
def redis_ficha(monkeypatch) -> RedisFicha:
    r = RedisFicha()
    monkeypatch.setattr(ficha, "redis", r)
    return r


# ─────────────── a ficha de um anúncio (pura) ───────────────


def test_shopee_attribute_list_e_descricao_limpa():
    f = ficha.montar_ficha("Uranyx Hotwav A17 Pro Max", DESCRICAO_KIA, RAW_SHOPEE)

    assert f["titulo"] == "Uranyx Hotwav A17 Pro Max"
    # Marca (do `brand`) e os atributos preenchidos, com a unidade; fora:
    # garantia, homologação (Registration ID), fabricante e o vazio.
    assert f["atributos"] == [
        "Marca: Uranyx",
        "Battery Capacity: 5000 mAh",
        "Storage Capacity: 128 GB",
    ]
    d = f["descricao"]
    assert "NFC - Não Tem" in d and "Memoria Interna 128 GB OU 64 GB" in d
    assert "Caixa Slim, o aparelho acompanha apenas o cabo de carregamento!" in d
    assert "Rede Movel 4G & Wifi 5G" in d  # entidade HTML desfeita
    assert "Cancelamento de ruído ativo no fone" in d  # ANC é dado do produto
    assert "Bateria de longa duração." in d  # a outra frase da linha "original" fica
    # Sem HTML, emoji, contato, garantia, preço, prazo, devolução, condição.
    for proibido in (
        "<",
        "✅",
        "WhatsApp",
        "98765",
        "www",
        "@lojakia",
        "Garantia",
        "R$",
        "699",
        "24 horas",
        "Devolução",
        "defeitos",
        "FILMADOS",
        "original",
        "lacrado",
    ):
        assert proibido not in d, proibido
    assert d == "\n".join(x.strip() for x in d.split("\n"))  # espaços normalizados


def test_shopee_formato_antigo_dentro_de_item():
    raw = {"item": {**RAW_SHOPEE, "description": "Tela 6.5 polegadas"}, "model": {}}

    f = ficha.montar_ficha("Título", None, raw)

    assert f["descricao"] == "Tela 6.5 polegadas"
    assert "Battery Capacity: 5000 mAh" in f["atributos"]


def test_ml_attributes_sem_descricao():
    raw = {"title": "Xiaomi Redmi 15c 128 Gb", "attributes": ATRIBUTOS_ML, "descriptions": []}

    f = ficha.montar_ficha("Xiaomi Redmi 15c 128 Gb", None, raw)

    assert "descricao" not in f
    assert set(f["atributos"]) == {
        "Marca: Xiaomi",
        "Memória interna: 128 GB",
        "Memória RAM: 8 GB",
        "Com NFC: Sim",
        "Cor: Preto, Azul",
        "Com sintonizador de TV: Não",
    }
    # O "Não" de recurso que ninguém perguntou vai por último.
    assert f["atributos"][-1] == "Com sintonizador de TV: Não"


def test_tiktok_product_attributes():
    raw = {
        "description": "<p>Mala de bordo ABS</p>",
        "product_attributes": [
            {"id": "1", "name": "Material", "values": [{"id": "2", "name": "ABS"}]},
            {"id": "3", "name": "Warranty Period", "values": [{"name": "1 year"}]},
        ],
    }

    f = ficha.montar_ficha("Mala", None, raw)

    assert f == {"titulo": "Mala", "atributos": ["Material: ABS"], "descricao": "Mala de bordo ABS"}


def test_amazon_so_titulo_nao_e_ficha():
    raw = {"title": "Air Fryer 4L", "sku": "AF-4", "asin": "B0X", "stock": 3, "status": "ok"}

    assert ficha.montar_ficha("Air Fryer 4L", None, raw) is None
    assert ficha.montar_ficha("x", "", {}) is None
    # Descrição que só tinha o que a IA não pode usar = sem ficha.
    assert ficha.montar_ficha("x", "Garantia de 1 ano. Chama no zap 11987654321", {}) is None


def test_atributos_da_pergunta_primeiro_e_no_teto():
    muitos = [_ml_attr(f"A{i}", f"Recurso {i}", "Sim") for i in range(120)]
    raw = {"attributes": [*muitos, *ATRIBUTOS_ML]}
    palavras = ficha.palavras_da_pergunta(["Boa tarde, contém a função NFC?"])

    f = ficha.montar_ficha("Celular", None, raw, palavras=palavras)

    assert palavras == {"nfc"}  # "boa", "tarde", "contém", "função" não contam
    assert f["atributos"][0] == "Com NFC: Sim"
    assert len(f["atributos"]) == ficha.MAX_ATRIBUTOS_FICHA
    # Sem pergunta que bata: os que costumam ser perguntados vêm antes.
    sem = ficha.montar_ficha("Celular", None, raw)
    assert sem["atributos"][:3] == ["Marca: Xiaomi", "Memória interna: 128 GB", "Memória RAM: 8 GB"]


def test_atributo_longo_cortado_e_com_contato_fora():
    raw = {
        "attributes": [
            _ml_attr("X", "Observação", "a" * 300),
            _ml_attr("Y", "Contato", "chama no whatsapp 11 98765-4321"),
        ]
    }

    f = ficha.montar_ficha("t", None, raw)

    assert f["atributos"] == [f["atributos"][0]]
    assert len(f["atributos"][0]) <= ficha.MAX_CHARS_ATRIBUTO
    assert f["atributos"][0].endswith("…")


def test_descricao_no_teto_em_linhas_inteiras():
    linhas = [f"Linha {i} do anúncio com algum texto técnico" for i in range(200)]

    d = ficha.limpar_descricao("\n".join(linhas))

    assert len(d) <= ficha.MAX_CHARS_DESCRICAO_FICHA and d.endswith("…")
    assert all(x in linhas for x in d.rstrip("…").split("\n"))
    # Um parágrafo só, maior que o teto: corta na palavra.
    longo = ficha.limpar_descricao(" ".join(["palavra"] * 500))
    assert len(longo) <= ficha.MAX_CHARS_DESCRICAO_FICHA and longo.endswith("…")


def test_descricao_com_telefone_link_e_email_sai_a_linha():
    """A LINHA com contato sai inteira — o contato partido em frases
    ("lojakia. com. br") só aparece na linha toda."""
    texto = (
        "Fone sem fio com case. Fale conosco: contato@loja.com. Bluetooth 5.3.\n"
        "Acesse bit.ly/xyz para ver mais\n"
        "Ligue 0800 123 4567\n"
        "Nosso site: lojakia. com. br\n"
        "Mais modelos na nossa loja: shopee. com. br/lojakia\n"
        "Instagram. lojakia\n"
        "Compatível com Android e iOS\n"
        "Driver de 10 mm. Bluetooth 5.3."
    )

    d = ficha.limpar_descricao(texto)

    assert d == "Compatível com Android e iOS\nDriver de 10 mm. Bluetooth 5.3."
    assert not validador.contato_fora_da_loja(d)
    # Contato partido entre LINHAS (nenhuma sozinha é contato): sem descrição.
    partido = "Ligue 11 9\n8765 4321"
    assert not any(validador.contato_fora_da_loja(x) for x in partido.split("\n"))
    assert ficha.limpar_descricao(partido) is None


def test_resistencia_e_condicao_saem_da_descricao_e_dos_atributos():
    """Regra de segurança 8: resistência, novo/lacrado/original só pelo
    título. A medida pura ("Profundidade: 8 mm") fica."""
    d = ficha.limpar_descricao(
        "IP68 / IP69K resistente d'água / resistente poeira / resistente a choques\n"
        "Aparelhos Resistente em até 1,5 metros de profundidade!\n"
        "Resistente à água (50m).\n"
        "Fotos submersas e mergulho até 30 m\n"
        "À prova de quedas de 1,2 m\n"
        "Profundidade: 8 mm\n"
        "Tela 6.5 polegadas"
    )
    assert d == "Profundidade: 8 mm\nTela 6.5 polegadas"

    a = ficha.atributos(
        {
            "attribute_list": [
                {
                    "original_attribute_name": "Features",
                    "attribute_value_list": [{"original_value_name": "À Prova de Poeira"}],
                },
                {
                    "original_attribute_name": "Estado do produto",
                    "attribute_value_list": [{"original_value_name": "Novo e lacrado"}],
                },
                {
                    "original_attribute_name": "Tipo",
                    "attribute_value_list": [{"original_value_name": "Original"}],
                },
                {
                    "original_attribute_name": "Item",
                    "attribute_value_list": [{"original_value_name": "Novo"}],
                },
                {
                    "original_attribute_name": "Proteção",
                    "attribute_value_list": [{"original_value_name": "IP68"}],
                },
                {
                    "original_attribute_name": "NFC",
                    "attribute_value_list": [{"original_value_name": "Sim"}],
                },
                {
                    "original_attribute_name": "Fone",
                    "attribute_value_list": [
                        {"original_value_name": "Cancelamento ativo de ruído"}
                    ],
                },
            ]
        }
    )
    assert a == ["NFC: Sim", "Fone: Cancelamento ativo de ruído"]


def test_caber_corta_do_ultimo_anuncio():
    grande = {
        "titulo": "T",
        "atributos": [f"Atributo {i}: valor {i}" for i in range(25)],
        "descricao": "\n".join(f"linha {i} " + "x" * 40 for i in range(30)),
    }
    fichas = [{"de_onde": "a", **grande}, {"de_onde": "b", **grande}]

    saida = ficha.caber(fichas)

    assert len(json.dumps(saida, ensure_ascii=False)) <= ficha.MAX_CHARS_FICHAS
    assert saida[0] == fichas[0]  # o primeiro (o mais recente) inteiro
    assert fichas[1]["descricao"] == grande["descricao"]  # a entrada não muda
    if len(saida) == 2:
        assert len(saida[1].get("descricao", "")) < len(grande["descricao"])
        # O segundo anúncio tem o teto menor dele (custo de tokens).
        assert len(json.dumps(saida[1], ensure_ascii=False)) <= ficha.MAX_CHARS_SEGUNDA_FICHA
    # Teto apertado: o segundo sai, e o primeiro encolhe sem sumir.
    apertado = ficha.caber(fichas, teto=800)
    assert len(apertado) == 1 and len(json.dumps(apertado, ensure_ascii=False)) <= 800
    assert apertado[0]["de_onde"] == "a"


# ─────────────── quais anúncios ───────────────


def _m(autor: str, anexos: list | None = None, minutos: int = 0) -> AtendimentoMensagem:
    return AtendimentoMensagem(
        autor=autor,
        anexos=anexos or [],
        enviada_em=datetime(2026, 10, 14, 10, 0, tzinfo=UTC) + timedelta(minutes=minutos),
    )


def _cartao(item: str) -> list[dict]:
    return [{"tipo": "produto", "item_id": item, "titulo": None}]


def test_anuncios_da_conversa_na_ordem_sem_repetir():
    conversa = AtendimentoConversa(
        plataforma="shopee",
        canal="chat",
        anuncio_id="111",
        dados={"pedido_mkt": {"itens": [{"item_id": "333"}, {"item_id": "111"}, {"titulo": "x"}]}},
    )
    mensagens = [
        _m("cliente", _cartao("222"), 0),
        _m("loja", _cartao("999"), 1),  # cartão da LOJA não conta
        _m("cliente", _cartao("444"), 2),
    ]

    anuncios = ficha.anuncios_da_conversa(conversa, mensagens)

    assert anuncios == [
        ficha.Anuncio("444", ficha.DE_ONDE_CARTAO),
        ficha.Anuncio("222", ficha.DE_ONDE_CARTAO),
        ficha.Anuncio("111", ficha.DE_ONDE_ANUNCIO),
        ficha.Anuncio("333", ficha.DE_ONDE_PEDIDO),
    ]


def test_anuncios_levam_o_titulo_atual():
    """O título do cartão (o enriquecimento o preenche com o da loja), o da
    conversa e o do item do pedido; o cartão ainda sem título ganha o da
    conversa para o mesmo anúncio."""
    conversa = AtendimentoConversa(
        plataforma="shopee",
        canal="chat",
        anuncio_id="111",
        anuncio_titulo="Anúncio da conversa",
        dados={"pedido_mkt": {"itens": [{"item_id": "333", "titulo": "Item do pedido"}]}},
    )
    mensagens = [
        _m("cliente", _cartao("111"), 0),
        _m("cliente", [{"tipo": "produto", "item_id": "222", "titulo": TITULO_ATUAL}], 1),
    ]

    assert ficha.anuncios_da_conversa(conversa, mensagens) == [
        ficha.Anuncio("222", ficha.DE_ONDE_CARTAO, TITULO_ATUAL),
        ficha.Anuncio("111", ficha.DE_ONDE_CARTAO, "Anúncio da conversa"),
        ficha.Anuncio("333", ficha.DE_ONDE_PEDIDO, "Item do pedido"),
    ]


def test_mesmo_titulo():
    assert not ficha.mesmo_titulo(TITULO_DE_MAIO, TITULO_ATUAL)
    assert ficha.mesmo_titulo(TITULO_ATUAL, TITULO_ATUAL.upper() + " ")
    # A linha da variação no catálogo da Shopee vale para o título do anúncio.
    assert ficha.mesmo_titulo(f"{TITULO_ATUAL} - Prata,16 GB / 128 GB Premium", TITULO_ATUAL)
    assert not ficha.mesmo_titulo(TITULO_ATUAL, f"{TITULO_ATUAL} Pro")
    assert not ficha.mesmo_titulo(None, TITULO_ATUAL) and not ficha.mesmo_titulo("x", "")


def test_ficha_bruta_da_shopee_e_do_ml_enxuta():
    """O que o enriquecimento guarda da resposta da loja: só o que a ficha lê."""
    b = ficha.ficha_bruta_shopee({**ITEM_KIA_HOJE, "item_sku": "x", "weight": "1"})
    assert b["titulo"] == TITULO_ATUAL and "NFC - Tem" in b["descricao"]
    assert set(b["raw"]) == {"attribute_list", "brand"}
    assert {"original_attribute_name", "attribute_value_list"} >= set(b["raw"]["attribute_list"][0])
    f = ficha.montar_ficha(b["titulo"], b["descricao"], b["raw"])
    assert "Battery Capacity: 5000 mAh" in f["atributos"] and "Garantia" not in f["descricao"]
    # A descrição "estendida" da Shopee (sem `description`).
    estendida = ficha.ficha_bruta_shopee(
        {
            "item_name": "x",
            "description_info": {
                "extended_description": {
                    "field_list": [{"field_type": "text", "text": "Tela 6.5 polegadas"}]
                }
            },
        }
    )
    assert estendida == {"titulo": "x", "descricao": "Tela 6.5 polegadas", "raw": {}}
    assert ficha.ficha_bruta_shopee({"item_name": "só título"}) is None

    ml = ficha.ficha_bruta_ml({"id": "MLB1", "title": "Redmi", "attributes": ATRIBUTOS_ML})
    assert ml["titulo"] == "Redmi" and ml["descricao"] is None
    nfc = {"id": "WITH_NFC", "name": "Com NFC", "value_name": "Sim", "values": [{"name": "Sim"}]}
    assert nfc in ml["raw"]["attributes"]
    assert all(a["name"] != "Quantidade de eSIMs" for a in ml["raw"]["attributes"])  # vazio
    assert ficha.ficha_bruta_ml({"id": "MLB1", "title": "x"}) is None


def test_anuncio_da_pergunta_do_ml_e_plataforma_sem_catalogo():
    pergunta = AtendimentoConversa(
        plataforma="ml", canal="pergunta", anuncio_id="MLB1", dados={"item_id": "MLB1"}
    )
    assert ficha.anuncios_da_conversa(pergunta, []) == [
        ficha.Anuncio("MLB1", ficha.DE_ONDE_PERGUNTA)
    ]
    # No Instagram o `anuncio_id` é a mídia do post; no site, nada.
    for plataforma in ("instagram", "site", "aliexpress"):
        c = AtendimentoConversa(plataforma=plataforma, canal="chat", anuncio_id="18115199")
        assert ficha.anuncios_da_conversa(c, []) == []


# ─────────────── banco: o catálogo ───────────────


async def _integ(db: AsyncSession, user, platform: IntegrationPlatform, nome: str) -> Integration:
    integ = Integration(
        user_id=user.id, platform=platform, name=nome, credentials=encrypt_json({"t": "x"})
    )
    db.add(integ)
    await db.commit()
    return integ


async def _listing(
    db: AsyncSession,
    integ: Integration,
    external_id: str,
    *,
    title: str = "Uranyx Hotwav A17 Pro Max",
    description: str | None = DESCRICAO_KIA,
    raw: dict | None = None,
    importado_ha: timedelta = timedelta(days=1),
) -> Listing:
    linha = Listing(
        user_id=integ.user_id,
        integration_id=integ.id,
        platform=integ.platform,
        external_id=external_id,
        title=title,
        description=description,
        raw_data=RAW_SHOPEE if raw is None else raw,
        imported_at=datetime.now(UTC) - importado_ha,
    )
    db.add(linha)
    await db.commit()
    return linha


async def _vinculo(
    db: AsyncSession,
    integ: Integration,
    produto: Product,
    external_id: str,
    *,
    variacao: str | None = None,
    morto: bool = False,
):
    db.add(
        ProductLink(
            user_id=integ.user_id,
            product_id=produto.id,
            integration_id=integ.id,
            platform=integ.platform,
            external_id=external_id,
            variation_id=variacao,
            morto_desde=datetime.now(UTC) if morto else None,
        )
    )
    await db.commit()


async def test_a_ficha_fresca_vem_antes_do_catalogo(db: AsyncSession, make_user, redis_ficha):
    """O enriquecimento guardou o que a Shopee devolveu HOJE ao cartão: é
    essa a ficha, com o título atual — não a do catálogo."""
    user = await make_user()
    kia = await _integ(db, user, IntegrationPlatform.SHOPEE, "Kia")
    await _listing(db, kia, ITEM_KIA, title=TITULO_DE_MAIO)
    redis_ficha.guardar("shopee", kia.id, ITEM_KIA, ficha.ficha_bruta_shopee(ITEM_KIA_HOJE))
    conversa = AtendimentoConversa(plataforma="shopee", canal="chat", integration_id=kia.id)
    cartao = [{"tipo": "produto", "item_id": ITEM_KIA, "titulo": TITULO_ATUAL}]

    (f,) = await ficha.fichas_da_conversa(db, conversa, [_m("cliente", cartao)], ["tem NFC?"])

    assert f["titulo"] == TITULO_ATUAL and "NFC - Tem" in f["descricao"]
    assert "Memoria Interna" not in f["descricao"]  # a descrição de maio não entra
    # A ficha fresca é da LOJA da conversa: a outra loja não a vê.
    outra = await _integ(db, user, IntegrationPlatform.SHOPEE, "Inova")
    conversa_outra = AtendimentoConversa(plataforma="shopee", canal="chat", integration_id=outra.id)
    assert await ficha.fichas_da_conversa(db, conversa_outra, [_m("cliente", cartao)]) == []


async def test_catalogo_com_outro_titulo_ou_velho_nao_vale(db: AsyncSession, make_user):
    """O caso da Kia: o catálogo (maio) diz "NFC - Não Tem" com o título de
    maio; o cartão de hoje é "… S5 Premium NFC". Sem a ficha fresca, a de
    maio NÃO entra — nem com o mesmo título, se a importação é velha."""
    user = await make_user()
    kia = await _integ(db, user, IntegrationPlatform.SHOPEE, "Kia")
    await _listing(db, kia, ITEM_KIA, title=TITULO_DE_MAIO)
    conversa = AtendimentoConversa(plataforma="shopee", canal="chat", integration_id=kia.id)
    hoje = [{"tipo": "produto", "item_id": ITEM_KIA, "titulo": TITULO_ATUAL}]
    de_maio = [{"tipo": "produto", "item_id": ITEM_KIA, "titulo": TITULO_DE_MAIO}]

    assert await ficha.fichas_da_conversa(db, conversa, [_m("cliente", hoje)]) == []
    # O mesmo título (a linha da variação também vale): a ficha vem, com ele.
    (f,) = await ficha.fichas_da_conversa(db, conversa, [_m("cliente", de_maio)])
    assert f["titulo"] == TITULO_DE_MAIO and "NFC - Não Tem" in f["descricao"]

    # Importado há mais de `MAX_DIAS_CATALOGO_FICHA` dias: não vale, nem com o
    # mesmo título, nem sem título nenhum.
    velha = await _integ(db, user, IntegrationPlatform.SHOPEE, "Mega")
    await _listing(
        db,
        velha,
        "777",
        title=TITULO_DE_MAIO,
        importado_ha=timedelta(days=ficha.MAX_DIAS_CATALOGO_FICHA + 1),
    )
    conversa_velha = AtendimentoConversa(
        plataforma="shopee", canal="chat", integration_id=velha.id, anuncio_id="777"
    )
    assert await ficha.fichas_da_conversa(db, conversa_velha, []) == []
    cartao_velho = [{"tipo": "produto", "item_id": "777", "titulo": TITULO_DE_MAIO}]
    assert await ficha.fichas_da_conversa(db, conversa_velha, [_m("cliente", cartao_velho)]) == []


async def test_ficha_do_catalogo_pelo_cartao(db: AsyncSession, make_user):
    user = await make_user()
    kia = await _integ(db, user, IntegrationPlatform.SHOPEE, "Kia")
    await _listing(db, kia, ITEM_KIA)
    conversa = AtendimentoConversa(
        plataforma="shopee", canal="chat", integration_id=kia.id, dados={}
    )

    fichas = await ficha.fichas_da_conversa(
        db, conversa, [_m("cliente", _cartao(ITEM_KIA))], ["contém a função NFC?"]
    )

    assert len(fichas) == 1
    assert fichas[0]["de_onde"] == ficha.DE_ONDE_CARTAO and "fonte" not in fichas[0]
    assert "NFC - Não Tem" in fichas[0]["descricao"]


async def test_sem_catalogo_sem_ficha(db: AsyncSession, make_user):
    user = await make_user()
    kia = await _integ(db, user, IntegrationPlatform.SHOPEE, "Kia")
    # Só título (a Amazon do catálogo): não é ficha.
    await _listing(db, kia, "555", description=None, raw={"title": "x"})
    conversa = AtendimentoConversa(plataforma="shopee", canal="chat", integration_id=kia.id)

    assert await ficha.fichas_da_conversa(db, conversa, [_m("cliente", _cartao("404"))]) == []
    assert await ficha.fichas_da_conversa(db, conversa, [_m("cliente", _cartao("555"))]) == []
    assert await ficha.fichas_da_conversa(db, conversa, []) == []


async def test_o_primeiro_sem_ficha_encerra_a_lista(db: AsyncSession, make_user):
    """O cartão de AGORA fora do catálogo: a ficha do anúncio da conversa
    (outro produto) responderia "tem NFC?" sobre o produto errado."""
    user = await make_user()
    kia = await _integ(db, user, IntegrationPlatform.SHOPEE, "Kia")
    await _listing(db, kia, ITEM_KIA)
    conversa = AtendimentoConversa(
        plataforma="shopee", canal="chat", integration_id=kia.id, anuncio_id=ITEM_KIA
    )

    assert await ficha.fichas_da_conversa(db, conversa, [_m("cliente", _cartao("404"))]) == []
    # Com o cartão do mesmo anúncio, ou sem cartão: a ficha vem.
    so_anuncio = await ficha.fichas_da_conversa(db, conversa, [])
    assert [f["de_onde"] for f in so_anuncio] == [ficha.DE_ONDE_ANUNCIO]


async def test_dois_anuncios_e_itens_do_pedido(db: AsyncSession, make_user):
    user = await make_user()
    kia = await _integ(db, user, IntegrationPlatform.SHOPEE, "Kia")
    await _listing(db, kia, "1001", title="Celular")
    await _listing(db, kia, "1002", title="Capa", description="Capa de silicone", raw={})
    await _listing(db, kia, "1003", title="Película", description="Película 3D", raw={})
    conversa = AtendimentoConversa(
        plataforma="shopee",
        canal="chat",
        integration_id=kia.id,
        dados={
            "pedido_mkt": {"itens": [{"item_id": "1001"}, {"item_id": "1002"}, {"item_id": "1003"}]}
        },
    )

    fichas = await ficha.fichas_da_conversa(db, conversa, [])

    assert [f["titulo"] for f in fichas] == ["Celular", "Capa"]  # no máximo 2
    assert {f["de_onde"] for f in fichas} == {ficha.DE_ONDE_PEDIDO}
    assert len(json.dumps(fichas, ensure_ascii=False)) <= ficha.MAX_CHARS_FICHAS


async def test_ml_pergunta_com_atributos(db: AsyncSession, make_user):
    user = await make_user()
    ml = await _integ(db, user, IntegrationPlatform.ML, "Kia ML")
    await _listing(
        db,
        ml,
        "MLB4612514329",
        title="Xiaomi Redmi 15c",
        description=None,
        raw={"attributes": ATRIBUTOS_ML, "descriptions": []},
    )
    conversa = AtendimentoConversa(
        plataforma="ml",
        canal="pergunta",
        integration_id=ml.id,
        anuncio_id="MLB4612514329",
        dados={"item_id": "MLB4612514329"},
    )

    (f,) = await ficha.fichas_da_conversa(db, conversa, [], ["tem nfc?"])

    assert f["de_onde"] == ficha.DE_ONDE_PERGUNTA
    assert f["atributos"][0] == "Com NFC: Sim" and "descricao" not in f


async def test_tiktok_pelo_mesmo_produto_em_outro_anuncio(db: AsyncSession, make_user):
    """O TikTok não tem catálogo: anúncio → produto (product_links) → o
    anúncio da Shopee do MESMO produto — marcado com a fonte."""
    user = await make_user()
    tiktok = await _integ(db, user, IntegrationPlatform.TIKTOK, "Kia TikTok")
    kia = await _integ(db, user, IntegrationPlatform.SHOPEE, "Kia")
    outra = await _integ(db, user, IntegrationPlatform.SHOPEE, "Inova")
    a17 = Product(user_id=user.id, sku="dg054.ci", name="Hotwav A17 Pro Max")
    capa = Product(user_id=user.id, sku="a001.ci", name="Capa A17")
    db.add_all([a17, capa])
    await db.commit()
    await _vinculo(db, tiktok, a17, "1735458254156498182")
    await _vinculo(db, kia, a17, ITEM_KIA)
    await _vinculo(db, outra, capa, "777")  # outro produto: não serve
    await _listing(db, kia, ITEM_KIA)
    await _listing(db, outra, "777", title="Capa", description="Capa", raw={})
    conversa = AtendimentoConversa(
        plataforma="tiktok",
        canal="chat",
        integration_id=tiktok.id,
        anuncio_id="1735458254156498182",
    )

    (f,) = await ficha.fichas_da_conversa(db, conversa, [])

    assert f["fonte"] == f"{ficha.FONTE_OUTRO_ANUNCIO} (Shopee)"
    # O título dos DOIS anúncios: o deste (quando a conversa o tem) e o do outro.
    assert "titulo" not in f and f["titulo_do_outro_anuncio"] == "Uranyx Hotwav A17 Pro Max"
    assert "NFC - Não Tem" in f["descricao"]
    cartao = [{"tipo": "produto", "item_id": "1735458254156498182", "titulo": "A17 TikTok"}]
    (f,) = await ficha.fichas_da_conversa(db, conversa, [_m("cliente", cartao)])
    assert f["titulo"] == "A17 TikTok"
    assert f["titulo_do_outro_anuncio"] == "Uranyx Hotwav A17 Pro Max"
    # Vínculo morto não conta.
    await db.execute(
        ProductLink.__table__.update()
        .where(ProductLink.external_id == ITEM_KIA)
        .values(morto_desde=datetime.now(UTC))
    )
    await db.commit()
    assert await ficha.fichas_da_conversa(db, conversa, []) == []


async def test_vinculo_morto_da_origem_nao_da_ficha(db: AsyncSession, make_user):
    """O TikTok "A18 Pro Max" com todos os vínculos mortos ("produto
    congelado ou excluído no TikTok"): sem produto vivo, sem ficha."""
    user = await make_user()
    tiktok = await _integ(db, user, IntegrationPlatform.TIKTOK, "Kia TikTok")
    kia = await _integ(db, user, IntegrationPlatform.SHOPEE, "Kia")
    a18 = Product(user_id=user.id, sku="a18", name="Uranyx A18 Pro Max - S5 Edition")
    db.add(a18)
    await db.commit()
    await _vinculo(db, tiktok, a18, "1737008025640994518", morto=True)
    await _vinculo(db, kia, a18, "22693481538")
    await _listing(db, kia, "22693481538")
    conversa = AtendimentoConversa(
        plataforma="tiktok",
        canal="chat",
        integration_id=tiktok.id,
        anuncio_id="1737008025640994518",
    )

    assert await ficha.fichas_da_conversa(db, conversa, []) == []


async def test_anuncio_com_outro_modelo_como_variacao_nao_da_ficha(db: AsyncSession, make_user):
    """TikTok "A18 Pro Max" → produto A18 → que é UMA variação do anúncio
    "A17 Pro Max" da Shopee (RAM 12 GB, sem NFC): o anúncio da Shopee vende
    produtos que o do TikTok não vende — não é ficha dele. Um anúncio com só
    o A18 (mesmo em duas variações) é."""
    user = await make_user()
    tiktok = await _integ(db, user, IntegrationPlatform.TIKTOK, "Kia TikTok")
    kia = await _integ(db, user, IntegrationPlatform.SHOPEE, "Kia")
    a18 = Product(user_id=user.id, sku="a18", name="Uranyx A18 Pro Max - S5 Edition")
    a17s = [Product(user_id=user.id, sku=f"a17-{i}", name=f"A17 {i}") for i in range(3)]
    db.add_all([a18, *a17s])
    await db.commit()
    await _vinculo(db, tiktok, a18, "1737193846133392646")
    for p in [a18, *a17s]:
        await _vinculo(db, kia, p, "22693481538", variacao=str(p.sku))
    await _listing(db, kia, "22693481538", title="Uranyx Hotwav A17 Pro Max 128 Gb Memoria 12Gb")
    conversa = AtendimentoConversa(
        plataforma="tiktok",
        canal="chat",
        integration_id=tiktok.id,
        anuncio_id="1737193846133392646",
    )

    assert await ficha.fichas_da_conversa(db, conversa, []) == []

    # Vínculo MORTO de outro produto no anúncio da Shopee não o impede.
    for p in a17s:
        await db.execute(
            ProductLink.__table__.update()
            .where(ProductLink.product_id == p.id)
            .values(morto_desde=datetime.now(UTC))
        )
    await db.commit()
    (f,) = await ficha.fichas_da_conversa(db, conversa, [])
    assert f["titulo_do_outro_anuncio"].startswith("Uranyx Hotwav A17")

    # O catálogo do outro anúncio também passa pelo corte de data.
    await db.execute(
        Listing.__table__.update()
        .where(Listing.external_id == "22693481538")
        .values(imported_at=datetime.now(UTC) - timedelta(days=150))
    )
    await db.commit()
    assert await ficha.fichas_da_conversa(db, conversa, []) == []


# ─────────────── nos FATOS da IA ───────────────


async def _cartao_do_cliente(
    db: AsyncSession, conversa, item: str, *, ha: timedelta, titulo: str | None = None
):
    await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id=f"cartao-{item}",
        autor="cliente",
        texto=None,
        enviada_em=datetime.now(UTC) - ha,
        tipo="produto",
        anexos=[{"tipo": "produto", "item_id": item, "titulo": titulo}],
    )
    await db.commit()


async def _conversa_kia(
    db: AsyncSession, make_user, redis_ficha: RedisFicha, *, fresca: bool = True, **kw
) -> AtendimentoConversa:
    """A conversa da Kia como em produção: o catálogo de MAIO (título velho,
    "NFC - Não Tem"), o cartão de HOJE ("… S5 Premium NFC") e — quando o
    enriquecimento já leu o anúncio — a ficha fresca no Redis."""
    conversa = await _conversa(db, make_user, pedido=None, comprador_nome="Maria", **kw)
    conversa.anuncio_titulo = None  # como nas conversas da Shopee em produção
    await db.commit()
    integ = await db.get(Integration, conversa.integration_id)
    await _listing(db, integ, ITEM_KIA, title=TITULO_DE_MAIO, importado_ha=timedelta(days=150))
    if fresca:
        redis_ficha.guardar("shopee", integ.id, ITEM_KIA, ficha.ficha_bruta_shopee(ITEM_KIA_HOJE))
    await _cartao_do_cliente(db, conversa, ITEM_KIA, ha=timedelta(minutes=6), titulo=TITULO_ATUAL)
    await _msg(db, conversa, "contém a função NFC?")
    return conversa


def _fatos_do_modelo(usuario: str) -> dict:
    return json.loads(
        usuario.split("FATOS DO SISTEMA (verdadeiros; é tudo o que se sabe do pedido):\n", 1)[
            1
        ].split("\n\nCONVERSA", 1)[0]
    )


async def test_nfc_de_ponta_a_ponta_com_modelo_falso(
    db: AsyncSession, make_user, ia_ligada, modelo, envios, redis_ficha
):
    """A pergunta de NFC chega ao modelo com a ficha FRESCA (o que a Shopee
    devolveu ao cartão hoje) e o título atual — não com a de maio."""
    conversa = await _conversa_kia(db, make_user, redis_ficha)
    modelo.saida = _saida(
        "Olá! A versão S5 Premium de 16 GB tem NFC; as versões de 12 GB não têm.",
        categoria="duvida_produto",
        motivo="ficha",
    )

    r = await ia.gerar_rascunho(db, conversa)

    usuario = modelo.usuario
    (f,) = _fatos_do_modelo(usuario)["ficha_do_anuncio"]
    assert f["de_onde"] == ficha.DE_ONDE_CARTAO and f["titulo"] == TITULO_ATUAL
    assert "NFC - Tem" in f["descricao"] and "Premium NFC" in usuario
    assert "Memoria Interna" not in usuario  # a descrição de maio não chega
    assert "ficha_do_anuncio" not in modelo.sistema  # FATOS vão na mensagem
    assert "Garantia" not in f["descricao"]
    assert r.status == RASCUNHO_PENDENTE and r.validador_ok, r.motivo
    assert r.fatos["ficha_do_anuncio"] == [f]


async def test_kia_so_com_o_catalogo_de_maio_vai_sem_ficha(
    db: AsyncSession, make_user, ia_ligada, modelo, envios, redis_ficha
):
    """Sem a ficha fresca, o catálogo de maio (outro título, importação
    velha) NÃO chega ao modelo: "NFC - Não Tem" ali seria resposta errada."""
    conversa = await _conversa_kia(db, make_user, redis_ficha, fresca=False)
    modelo.saida = _saida(
        "Olá! A equipe confirma esse dado para você.",
        categoria="duvida_produto",
        precisa_humano=True,
    )

    r = await ia.gerar_rascunho(db, conversa)

    assert "ficha_do_anuncio" not in modelo.usuario and "NFC - Não Tem" not in modelo.usuario
    assert "ficha_do_anuncio" not in r.fatos


async def test_numero_da_ficha_nao_e_inventado(
    db: AsyncSession, make_user, ia_ligada, modelo, envios, redis_ficha
):
    """Com a ficha nos FATOS, "128 GB" e "6,75 polegadas" não são número
    inventado nem "valor em R$"; o que não está na ficha continua sendo."""
    conversa = await _conversa_kia(db, make_user, redis_ficha)
    modelo.saida = _saida(
        "Olá! Ele tem 128 GB de memória interna e tela de 6,75 polegadas.",
        categoria="duvida_produto",
    )
    r = await ia.gerar_rascunho(db, conversa)
    assert r.status == RASCUNHO_PENDENTE and r.validador_ok, r.motivo

    await _msg(db, conversa, "e a câmera?", ha=timedelta(minutes=1))
    modelo.saida = _saida("Olá! A câmera tem 108 MP.", categoria="duvida_produto")
    r = await ia.gerar_rascunho(db, conversa)
    assert ia.MOTIVO_NUMERO_INVENTADO in r.motivo


async def test_classificacao_nao_recebe_a_ficha(
    db: AsyncSession, make_user, ia_ligada, monkeypatch, envios, redis_ficha
):
    db.add(AtendimentoRegra(categoria="duvida_produto", quando="FICHA pergunta", faca="responda"))
    await db.commit()
    conversa = await _conversa_kia(db, make_user, redis_ficha)
    falso = ModeloSequencia(
        [
            {"categoria": "duvida_produto", "confianca": 0.9},
            _saida("Olá! Não tem NFC.", categoria="duvida_produto"),
        ]
    )
    monkeypatch.setattr(ia, "_chamar_modelo", falso)

    await ia.gerar_rascunho(db, conversa)

    (_, usuario_c), (_, usuario_r) = falso.chamadas
    assert "ficha_do_anuncio" not in usuario_c
    assert "ficha_do_anuncio" in usuario_r and "NFC - Tem" in usuario_r


async def test_sem_anuncio_ligado_sem_ficha_nos_fatos(
    db: AsyncSession, make_user, ia_ligada, modelo, envios
):
    conversa = await _conversa(db, make_user, pedido=None)
    await _listing(db, await db.get(Integration, conversa.integration_id), ITEM_KIA)
    await _msg(db, conversa, "tem NFC?")

    r = await ia.gerar_rascunho(db, conversa)

    assert "ficha_do_anuncio" not in modelo.usuario
    assert "ficha_do_anuncio" not in r.fatos


async def test_ficha_pelo_item_do_pedido(db: AsyncSession, make_user, ia_ligada, modelo, envios):
    conversa = await _conversa(db, make_user, pedido=None)
    await _listing(db, await db.get(Integration, conversa.integration_id), ITEM_KIA)
    conversa.dados = {"pedido_mkt": {"pedido": "X1", "itens": [{"item_id": ITEM_KIA}]}}
    await db.commit()
    await _msg(db, conversa, "o celular que comprei tem NFC?")
    modelo.saida = _saida("Olá! Não, este modelo não tem NFC.", categoria="duvida_produto")

    r = await ia.gerar_rascunho(db, conversa)

    assert r.fatos["ficha_do_anuncio"][0]["de_onde"] == ficha.DE_ONDE_PEDIDO


async def test_no_automatico_a_resposta_pela_ficha_vai_para_pessoa(
    db: AsyncSession, make_user, ia_ligada, modelo, envios, redis_ficha
):
    """A Kia em `auto` com `conteudo_da_caixa` liberado: a resposta tirada da
    ficha não sai sozinha — fica pendente para pessoa. No copiloto, a mesma
    resposta não ganha o motivo (quem envia já é pessoa)."""
    ia_ligada.atendimento_auto_ativo = True
    await _importar_manual_base(db)
    conversa = await _conversa_kia(
        db, make_user, redis_ficha, modo="auto", auto_categorias=["conteudo_da_caixa"]
    )
    await _msg(db, conversa, "o que vem na caixa?", ha=timedelta(minutes=1))
    modelo.saida = _saida(
        "Olá! Acompanha apenas o cabo de carregamento.",
        categoria="conteudo_da_caixa",
        confianca=0.95,
    )

    r = await ia.gerar_rascunho(db, conversa)

    assert "ficha_do_anuncio" in r.fatos
    assert r.status == RASCUNHO_PENDENTE and r.precisa_humano
    assert ia.MOTIVO_FICHA_NO_AUTOMATICO in r.motivo
    assert envios == []

    copiloto = await _conversa_kia(db, make_user, redis_ficha, externo_id="conv-2")
    await _msg(db, copiloto, "o que vem na caixa?", ha=timedelta(minutes=1))
    r2 = await ia.gerar_rascunho(db, copiloto)
    assert "ficha_do_anuncio" in r2.fatos
    assert ia.MOTIVO_FICHA_NO_AUTOMATICO not in (r2.motivo or "")


async def test_profundidade_da_descricao_nao_vira_numero_permitido(
    db: AsyncSession, make_user, ia_ligada, modelo, envios
):
    """A alegação de resistência sai da ficha: "1,5 metros de profundidade"
    continua sendo número que não veio do sistema (bloqueado), como antes da
    ficha."""
    conversa = await _conversa(db, make_user, pedido=None)
    conversa.anuncio_id = "23799035697"
    conversa.anuncio_titulo = "Uranyx Oukitel WP52 5g - Resistente NFC"
    await db.commit()
    await _listing(
        db,
        await db.get(Integration, conversa.integration_id),
        "23799035697",
        title="Uranyx Oukitel WP52 5g - Resistente NFC",
        description="Rede Móvel 5G\nAparelhos Resistente em até 1,5 metros de profundidade!",
        raw={},
    )
    await _msg(db, conversa, "posso usar na piscina?")
    modelo.saida = _saida(
        "Olá! Sim, ele é resistente até 1,5 metros de profundidade.", categoria="duvida_produto"
    )

    r = await ia.gerar_rascunho(db, conversa)

    assert "1,5" not in json.dumps(r.fatos, ensure_ascii=False)
    assert r.fatos["ficha_do_anuncio"][0]["descricao"] == "Rede Móvel 5G"
    assert r.status == RASCUNHO_BLOQUEADO and ia.MOTIVO_NUMERO_INVENTADO in r.motivo


# ─────────────── orçamento do prompt ───────────────


def test_ficha_grande_tem_teto_proprio_e_nao_tira_correcoes():
    """A ficha vai na MENSAGEM (FATOS), não no prompt de sistema: o pior caso
    do orçamento (sem classificação, manual base inteiro, 3 correções) fica
    igual, com as três correções; e a ficha enorme cabe no teto dela."""
    manual, categorias = _manual_base_puro("shopee", "chat")
    conversa = AtendimentoConversa(plataforma="shopee", canal="chat", conta="KFA Malas")
    correcoes = [
        {"categoria": "rastreio", "correcao": f"CORRECAO-{i} " + "x" * 190} for i in range(3)
    ]
    enorme = ficha.montar_ficha(
        "Celular " * 40,
        "\n".join(f"Especificação técnica número {i} do aparelho" for i in range(500)),
        {"attributes": [_ml_attr(f"A{i}", f"Recurso {i}", "Sim") for i in range(200)]},
    )
    fichas = ficha.caber([{"de_onde": "a", **enorme}, {"de_onde": "b", **enorme}])

    p = ia.montar_resposta(conversa, manual, [], categorias=categorias, correcoes=correcoes)
    usuario = ia.montar_usuario({"anuncio": "x", "ficha_do_anuncio": fichas}, [], ())

    assert len(json.dumps(fichas, ensure_ascii=False)) <= ficha.MAX_CHARS_FICHAS
    assert len(enorme["descricao"]) <= ficha.MAX_CHARS_DESCRICAO_FICHA
    assert len(enorme["atributos"]) == ficha.MAX_ATRIBUTOS_FICHA
    assert p.correcoes == correcoes and "correcoes" not in p.cortes
    assert p.tamanho <= ia.MAX_CHARS_RESPOSTA
    assert "ficha_do_anuncio" in usuario and "ficha_do_anuncio" not in p.sistema


# ─────────────── validador ───────────────


def test_validador_deixa_a_resposta_tirada_da_ficha():
    for texto in (
        "Sim, possui NFC.",
        "Não, este modelo não tem NFC.",
        "A tela tem 6,75 polegadas e 120 Hz.",
        'A tela tem 6.75" e o processador 1.26 GHz.',
        "A tela de 6,56'' tem 120 Hz.",
        'São 6,75" de tela.',
        "A bateria é de 3,85 volts.",
        "Ele tem 128 GB de memória interna e bateria de 5.160 mAh.",
        "A mala tem 2,40 kg e o fone tem bluetooth 5.3.",
    ):
        assert (
            validador.validar(texto, plataforma="shopee", canal="chat", origem=ORIGEM_IA) == []
        ), texto


def test_validador_continua_barrando_valor_prazo_e_contato():
    casos = {
        "Custa 59,90 no anúncio.": "valor em R$ só pode vir do sistema",
        "Sai por R$ 199,90.": "valor em R$ só pode vir do sistema",
        "Fica 49.90 hoje.": "valor em R$ só pode vir do sistema",
        "Enviamos em 2 dias úteis.": "prazo em números só pode vir do sistema",
        "Tem garantia de 3 meses.": "prazo de garantia só pode vir do sistema",
        "Chama no zap 11 98765-4321": "contato fora da loja (WhatsApp)",
        # Aspas só valem como polegada com a tela junto; V/W soltos não são
        # unidade (só "volts"/"watts").
        'O valor é "59,90" na variação preta.': "valor em R$ só pode vir do sistema",
        "O valor é \u201c59,90\u201d na variação.": "valor em R$ só pode vir do sistema",
        'A tela sai por "59,90" hoje.': "valor em R$ só pode vir do sistema",
        "Sai por 49,90 w": "valor em R$ só pode vir do sistema",
        "Fica 129,90 V à vista.": "valor em R$ só pode vir do sistema",
        "Custa 59,90 MB": "valor em R$ só pode vir do sistema",
    }
    for texto, motivo in casos.items():
        for plataforma, canal in (("shopee", "chat"), ("ml", "pergunta")):
            final = validador.normalizar(texto, plataforma=plataforma, canal=canal)
            motivos = validador.validar(final, plataforma=plataforma, canal=canal, origem=ORIGEM_IA)
            assert motivo in motivos, (texto, plataforma, motivos)


def test_contato_fora_da_loja():
    for texto in (
        "WhatsApp 11 98765-4321",
        "contato@loja.com",
        "www.loja.com.br",
        "siga @lojakia",
        "chave pix 123",
        "compra direto comigo",
    ):
        assert validador.contato_fora_da_loja(texto), texto
    for texto in ("Bluetooth 5.3", "Tela 6.75 120Hz", "NFC - Não Tem", "Pedido 2509250001", ""):
        assert not validador.contato_fora_da_loja(texto), texto
