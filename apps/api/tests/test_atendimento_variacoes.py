# ruff: noqa: S105, F811 — tokens de lojas falsas; fixtures importadas dos testes da ficha
"""AS VARIAÇÕES DO ANÚNCIO para o AGENTE (20/10/2026, `variacoes.py`).

O caso real: na Shopee da Kia, o anúncio "Uranyx A18 Pro Max NFC 16.128 Gb
Memoria S5 Premium" (58265493964) tem as variações dg089 e dg088, que o
anúncio chama "A18 - Branco NFC" e "A18 - Laranja NFC" e o Bling chama
"… Prata + Fone UFB10" e "… Laranja + Fone UFB10". O comprador perguntou
"Vc tem o bordo" e "E fone vem junto"; a descrição diz "Uranyx S5 + Fone
UFB10" (o fone VEM). A equipe respondeu "no momento não temos na cor bordo!".

- o legado NÃO muda (decisão 1 do dono): a ficha sem o opt-in é a de
  origin — sem variações e sem ida à loja por elas;
- o que a loja devolve vira variação (Shopee `tier_variation`, TikTok
  `skus`, ML `variations`); anúncio fora do ar na loja entra esgotado;
- o nome, de UMA fonte: a loja; o final do título do vínculo só com todos
  os títulos iguais ao atual e finais distintos; o produto do Bling sem
  código; nome repetido, sem lista;
- disponível pela LOJA (cache fresco), senão pelo vínculo; confiável pela
  régua da spec 4.2 (vínculo `ok`, anúncio no ar, vínculo e produto
  coerentes), nunca com o nome do cadastro;
- o agente nunca vê quantidade, sku nem variation_id (`para_o_agente`);
- a ida à loja só pelas variações tem cota e espera próprias (a ficha do
  legado não perde vez), cache de 30 min e cache negativo próprio; lista
  do cache mais velha que um vínculo não vale;
- o enriquecimento do cartão grava as variações da MESMA resposta;
- a conferência (`conferir_variacoes`, para as checagens do agente), com a
  calibração de erros plantados (inclusive os 19 da revisão de 20/10/2026);
- sem anúncio na conversa (o caso robertasoares831): nenhuma ficha — o
  agente pergunta qual é o produto.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Integration, IntegrationPlatform, Product, ProductLink
from app.models.enums import LinkSyncStatus
from app.services.atendimento import enriquecer, ficha, ficha_loja, politicas_basicas
from app.services.atendimento import variacoes as variacoes_svc
from app.services.atendimento.variacoes import Vinculo
from tests.test_atendimento_enriquecer import (  # noqa: F401 — o Redis falso é fixture
    ShopeeFalso,
    item_shopee,
    redis_falso,
)
from tests.test_atendimento_enriquecer import _integ as _integ_enriquecer
from tests.test_atendimento_ficha_loja import (  # noqa: F401 — Redis, leitura: fixtures
    ML,
    SHOPEE,
    TIKTOK,
    Lojas,
    MLFalso,
    RedisMem,
    ShopeeFalsa,
    TikTokFalso,
    _erro_http,
    _loja,
    leitura,
    redis_mem,
    sem_busca_pendurada,
)
from tests.test_atendimento_ia import _conversa

ITEM_A18 = "58265493964"
TITULO_A18 = "Uranyx A18 Pro Max NFC 16.128 Gb Memoria S5 Premium"
BRANCO, LARANJA = "199625078450", "199625078451"
DESCRICAO_A18 = (
    "Uranyx S5 + Fone UFB10\n"
    "Memória interna de 128 GB\n"
    "Tela 6.9 HD+ 120 Hz\n"
    "NFC - Sim\n"
    "Fone UFB10\n"
    "ANC: sim\n"
    "Garantia de 90 dias direto com a loja.\n"
)
ITEM_A18_HOJE = {
    "item_id": int(ITEM_A18),
    "item_name": TITULO_A18,
    "description": DESCRICAO_A18,
    "has_model": True,
    "item_status": "NORMAL",
    "attribute_list": [
        {
            "original_attribute_name": "Model Name",
            "attribute_value_list": [{"original_value_name": "USM003"}],
        }
    ],
    "brand": {"original_brand_name": "Uranyx"},
}
# O `get_model_list` do anúncio (formato real: tier_variation + model).
MODELOS_A18 = {
    "tier_variation": [
        {"name": "Modelo", "option_list": [{"option": "A18"}]},
        {"name": "Cor", "option_list": [{"option": "Branco NFC"}, {"option": "Laranja NFC"}]},
    ],
    "model": [
        {
            "model_id": int(BRANCO),
            "tier_index": [0, 0],
            "model_sku": "dg089.ci+a003.ci",
            "model_status": "MODEL_NORMAL",
            "stock_info_v2": {"summary_info": {"total_available_stock": 175}},
            "price_info": [{"current_price": 899.9, "original_price": 1299.9}],
        },
        {
            "model_id": int(LARANJA),
            "tier_index": [0, 1],
            "model_sku": "dg088.ci+a003.ci",
            "model_status": "MODEL_NORMAL",
            "stock_info_v2": {"summary_info": {"total_available_stock": 178}},
            "price_info": [{"current_price": 899.9, "original_price": 1299.9}],
        },
    ],
}
PERGUNTA_BORDO = "Vc tem o bordo"
PERGUNTA_FONE = "E fone vem junto"
# A resposta que a equipe deu (Duoke, 20/10/2026), no formato da IA.
RESPOSTA_CERTA = (
    "Olá! No momento não temos na cor bordô: as opções deste anúncio são Branco NFC e "
    "Laranja NFC, as duas disponíveis."
)


# ─────────────── lojas falsas com o `get_model_list` ───────────────


class ShopeeComModelos(ShopeeFalsa):
    async def get_model_list(self, item_id) -> dict:
        await self._ida("get_model_list", item_id)
        return self.lojas.modelos.get(str(item_id), {"tier_variation": [], "model": []})


class LojasComModelos(Lojas):
    def __init__(self) -> None:
        super().__init__()
        self.modelos: dict[str, dict] = {}

    def fabrica(self, platform, creds, on_token_refresh=None, integration_id=None):
        self.criados.append((platform, dict(creds), integration_id))
        classe = {SHOPEE: ShopeeComModelos, ML: MLFalso, TIKTOK: TikTokFalso}[platform]
        return classe(self, creds, on_token_refresh)


@pytest.fixture(autouse=True)
def lojas(monkeypatch) -> LojasComModelos:
    from app.services.atendimento import clientes

    falsas = LojasComModelos()
    monkeypatch.setattr(clientes.factory, "client_for", falsas.fabrica)
    return falsas


def _a18_na_loja(lojas: LojasComModelos) -> None:
    lojas.shopee[ITEM_A18] = ITEM_A18_HOJE
    lojas.modelos[ITEM_A18] = MODELOS_A18


# ─────────────── o que a loja devolve (puras) ───────────────


def test_shopee_nome_pelo_tier_variation_e_estoque_do_modelo():
    assert variacoes_svc.da_shopee(MODELOS_A18) == [
        {
            "variation_id": BRANCO,
            "nome": "A18, Branco NFC",
            "sku": "dg089.ci+a003.ci",
            "estoque": 175,
        },
        {
            "variation_id": LARANJA,
            "nome": "A18, Laranja NFC",
            "sku": "dg088.ci+a003.ci",
            "estoque": 178,
        },
    ]
    # Sem tier_variation: o `model_name` (a Shopee separa por vírgula); o
    # modelo fora do ar entra esgotado; o estoque antigo (`stock_info`) vale.
    sem_tier = {
        "model": [
            {
                "model_id": 1,
                "model_name": "Rosa + Acessorios,16 GB / 128 GB",
                "stock_info": {"total_available_stock": 3},
            },
            {
                "model_id": 2,
                "model_name": "Preto",
                "model_status": "MODEL_UNAVAILABLE",
                "stock_info_v2": {"summary_info": {"total_available_stock": 9}},
            },
            {"model_name": "sem id"},
        ]
    }
    assert [(v["nome"], v["estoque"]) for v in variacoes_svc.da_shopee(sem_tier)] == [
        ("Rosa + Acessorios, 16 GB / 128 GB", 3),
        ("Preto", 0),
    ]
    assert variacoes_svc.da_shopee(None) == [] and variacoes_svc.da_shopee({"model": "x"}) == []
    # Revisão de 20/10/2026: o anúncio fora do ar (o `item_status` do
    # `get_item_base_info`) entra todo esgotado; NORMAL ou sem status, não.
    for status in ("UNLIST", "BANNED", "REVIEWING", "SELLER_DELETE"):
        assert [v["estoque"] for v in variacoes_svc.da_shopee(MODELOS_A18, status)] == [0, 0]
    assert [v["estoque"] for v in variacoes_svc.da_shopee(MODELOS_A18, "NORMAL")] == [175, 178]


def test_tiktok_skus_e_ml_variations():
    data = {
        "skus": [
            {
                "id": "1729",
                "seller_sku": "dg054.ci",
                "sales_attributes": [
                    {"name": "Cor", "value_name": "Preto"},
                    {"name": "Tamanho", "value_name": "42mm"},
                ],
                "inventory": [{"warehouse_id": "w1", "quantity": 2}, {"quantity": 1}],
            },
            {"id": "1730", "sales_attributes": [{"value_name": "Cinza"}]},
        ]
    }
    assert variacoes_svc.da_tiktok(data) == [
        {"variation_id": "1729", "nome": "Preto, 42mm", "sku": "dg054.ci", "estoque": 3},
        {"variation_id": "1730", "nome": "Cinza", "sku": None, "estoque": None},
    ]
    assert [v["estoque"] for v in variacoes_svc.da_tiktok({**data, "status": "ACTIVATE"})] == [
        3,
        None,
    ]
    assert [
        v["estoque"] for v in variacoes_svc.da_tiktok({**data, "status": "SELLER_DEACTIVATED"})
    ] == [0, 0]
    body = {
        "variations": [
            {
                "id": 180001,
                "attribute_combinations": [{"name": "Cor", "value_name": "Azul"}],
                "available_quantity": 0,
                "attributes": [{"id": "SELLER_SKU", "value_name": "MOC-AZ"}],
            },
            {
                "id": 180002,
                "attribute_combinations": [{"value_name": "Verde"}],
                "available_quantity": 4,
                "seller_custom_field": "MOC-VD",
            },
        ]
    }
    assert variacoes_svc.da_ml(body) == [
        {"variation_id": "180001", "nome": "Azul", "sku": "MOC-AZ", "estoque": 0},
        {"variation_id": "180002", "nome": "Verde", "sku": "MOC-VD", "estoque": 4},
    ]
    assert variacoes_svc.da_ml({"id": "MLB1"}) == []  # anúncio sem variação
    # Pausado ou em revisão no ML: tudo esgotado (o comprador não compra).
    assert [v["estoque"] for v in variacoes_svc.da_ml({**body, "status": "active"})] == [0, 4]
    for status in ("paused", "under_review", "closed"):
        assert [v["estoque"] for v in variacoes_svc.da_ml({**body, "status": status})] == [0, 0]


# ─────────────── a montagem (pura) ───────────────


def _vinculo(
    vid: str | None,
    *,
    titulo: str | None = None,
    estoque: int | None = 10,
    status: str = "ok",
    produto: str | None = None,
    estoque_produto: int | None = 10,
    sku: str | None = None,
    anuncio_status: str | None = None,
    criado_em: datetime | None = None,
) -> Vinculo:
    return Vinculo(
        vid, sku, titulo, estoque, status, produto, estoque_produto, anuncio_status, criado_em
    )


VINCULOS_KIA = [
    _vinculo(
        BRANCO,
        titulo=f"{TITULO_A18} - A18 - Branco NFC",
        estoque=175,
        produto="Uranyx A18 Pro Max - S5 Edition 16.128 - Prata + Fone UFB10",
        estoque_produto=174,
        sku="dg089.ci+a003.ci",
    ),
    _vinculo(
        LARANJA,
        titulo=f"{TITULO_A18} - A18 - Laranja NFC",
        estoque=178,
        produto="Uranyx A18 Pro Max - S5 Edition 16.128 - Laranja + Fone UFB10",
        estoque_produto=174,
        sku="dg088.ci+a003.ci",
    ),
]


def test_kia_nome_da_loja_e_sem_ela_o_final_do_titulo_atual():
    """Produção, 20/10/2026: o Bling diz "Prata", o anúncio diz "Branco NFC".
    Com a lista da loja: o nome dela. Sem ela, o final do título do vínculo
    (todos com o título ATUAL): confiável pela régua da spec 4.2. Sem o título
    atual: o nome do Bling — nunca confiável."""
    loja = variacoes_svc.da_shopee(MODELOS_A18)
    completo = variacoes_svc.montar_variacoes(
        loja, VINCULOS_KIA, titulo=TITULO_A18, variation_id=LARANJA
    )
    assert completo == {
        "variacoes": [
            {
                "nome": "A18, Branco NFC",
                "fonte": "loja",
                "disponivel": True,
                "confiavel": True,
                "sku": "dg089.ci+a003.ci",
                "variation_id": BRANCO,
            },
            {
                "nome": "A18, Laranja NFC",
                "fonte": "loja",
                "disponivel": True,
                "confiavel": True,
                "sku": "dg088.ci+a003.ci",
                "variation_id": LARANJA,
                "selecionada": True,
            },
        ],
        "variacoes_completas": True,
    }
    # Nunca a quantidade.
    assert "175" not in json.dumps(completo) and "estoque" not in json.dumps(completo)
    so_vinculos = variacoes_svc.montar_variacoes(None, VINCULOS_KIA, titulo=TITULO_A18)
    assert [(v["nome"], v["fonte"], v["confiavel"]) for v in so_vinculos["variacoes"]] == [
        ("A18 - Branco NFC", "titulo_do_vinculo", True),
        ("A18 - Laranja NFC", "titulo_do_vinculo", True),
    ]
    assert so_vinculos["variacoes_completas"] is False
    assert "Prata" not in json.dumps(so_vinculos)
    sem_titulo = variacoes_svc.montar_variacoes(None, VINCULOS_KIA)
    assert [(v["nome"], v["fonte"], v["confiavel"]) for v in sem_titulo["variacoes"]] == [
        ("Prata + Fone UFB10", "cadastro", False),
        ("Laranja + Fone UFB10", "cadastro", False),
    ]


def test_kia_hoje_cinco_vinculos_a18_e_c17():
    """Produção, 20/10/2026 (só leitura): o anúncio ganhou três variações do
    C17 (criadas hoje, sem estoque). Com o título atual: os finais do título;
    o C17 esgotado e confiável (vínculo 0, produto 0)."""
    c17 = [
        _vinculo(
            vid,
            titulo=f"{TITULO_A18} - C17 - {cor}",
            estoque=0,
            estoque_produto=0,
            produto=f"Uranyx C17 16.128 - {cor} + Fone U9",
            sku=sku,
        )
        for vid, cor, sku in (
            ("209195979019", "Laranja", "dg107.sp+a003.sp"),
            ("209195979020", "Azul", "dg106.sp+a003.sp"),
            ("209195979021", "Prata", "dg108.sp+a003.sp"),
        )
    ]
    agente = variacoes_svc.para_o_agente(
        variacoes_svc.montar_variacoes(None, [*VINCULOS_KIA, *c17], titulo=TITULO_A18)
    )
    assert agente == {
        "variacoes": [
            {
                "nome": "A18 - Branco NFC",
                "disponivel": True,
                "confiavel": True,
                "fonte": "titulo_do_vinculo",
            },
            {
                "nome": "A18 - Laranja NFC",
                "disponivel": True,
                "confiavel": True,
                "fonte": "titulo_do_vinculo",
            },
            {
                "nome": "C17 - Laranja",
                "disponivel": False,
                "confiavel": True,
                "fonte": "titulo_do_vinculo",
            },
            {
                "nome": "C17 - Azul",
                "disponivel": False,
                "confiavel": True,
                "fonte": "titulo_do_vinculo",
            },
            {
                "nome": "C17 - Prata",
                "disponivel": False,
                "confiavel": True,
                "fonte": "titulo_do_vinculo",
            },
        ],
        "completas": False,
        "omitidas": 0,
    }


def test_titulo_velho_misturado_ou_repetido_nao_da_nome():
    """Revisão de 20/10/2026 — o S7 da Kia (21599835776): 5 dos 6 vínculos com
    o título de antes da renomeação. Título que não bate com o atual, final
    repetido ou faltando: nada de final do título — o nome do Bling, de UMA
    fonte para a lista inteira; nome do Bling repetido: sem lista."""
    atual = "Uranyx S7 Pro Max 8.128"
    velho = "Uranyx S7 Pro Max 16.128 GB ou 32.256 GB Tela Traseira"
    vinculos = [
        _vinculo("1", titulo=f"{velho} - Dourado,16.128 GB", produto="S7 8.128 - Dourado"),
        _vinculo("2", titulo=f"{atual} - Cinza,12.256 GB", produto="S7 8.128 - Cinza"),
    ]
    bloco = variacoes_svc.montar_variacoes(None, vinculos, titulo=atual)
    assert [(v["nome"], v["fonte"]) for v in bloco["variacoes"]] == [
        ("Dourado", "cadastro"),
        ("Cinza", "cadastro"),
    ]
    assert "16.128" not in json.dumps(bloco)
    # Finais repetidos: idem.
    repetidos = [
        _vinculo("1", titulo=f"{atual} - Preto", produto="X - Preto 128"),
        _vinculo("2", titulo=f"{atual} - Preto", produto="X - Preto 256"),
    ]
    assert [
        v["nome"]
        for v in variacoes_svc.montar_variacoes(None, repetidos, titulo=atual)["variacoes"]
    ] == ["Preto 128", "Preto 256"]
    # Nome do Bling repetido (produção: 57156707765 com 9 opções "C53"): sem lista.
    c53 = [_vinculo(str(i), produto="Celular C53") for i in range(9)]
    assert variacoes_svc.montar_variacoes(None, c53, titulo=atual) is None
    # A loja com dois nomes iguais: sem lista também.
    assert (
        variacoes_svc.montar_variacoes(
            [
                {"variation_id": "1", "nome": "C53", "estoque": 1},
                {"variation_id": "2", "nome": "c53", "estoque": 1},
            ],
            [],
        )
        is None
    )


def test_nome_pelo_produto_do_bling_sem_codigo_e_sem_comeco_comum():
    vinculos = [
        _vinculo("1", produto="Mala Lisa M2 tamanho 18 - Cinza Escuro (DT - DTLG014 - DT14)"),
        _vinculo("2", produto="Mala Lisa M2 tamanho 18 - Mostarda (DT - DTLG014 - DT08)"),
    ]
    bloco = variacoes_svc.montar_variacoes(None, vinculos)
    assert [v["nome"] for v in bloco["variacoes"]] == ["Cinza Escuro", "Mostarda"]
    # Nomes sem um " - " em comum: inteiros (sem o código), cortados no teto.
    longos = [_vinculo("1", produto="A" * 80), _vinculo("2", produto="Kit Preto (X1)")]
    nomes = [v["nome"] for v in variacoes_svc.montar_variacoes(None, longos)["variacoes"]]
    assert nomes[1] == "Kit Preto" and len(nomes[0]) == variacoes_svc.MAX_CHARS_NOME_VARIACAO


def test_regua_do_confiavel():
    """Spec 4.2: disponível pela loja (quando há a lista), senão pelo vínculo;
    confiável só com vínculo `ok`, anúncio no ar, vínculo e produto coerentes
    (e, com a lista da loja, a loja coerente com o vínculo). Sem vínculo, ou
    com o nome do cadastro, nunca."""

    def um(vinculo: Vinculo, loja: dict | None = None) -> dict:
        outro = _vinculo("9")
        lista = [
            loja or {"variation_id": "1", "nome": "Azul", "estoque": 10},
            {"variation_id": "9", "nome": "Outra", "estoque": 10},
        ]
        bloco = variacoes_svc.montar_variacoes(lista, [vinculo, outro], titulo="T")
        return bloco["variacoes"][0]

    assert um(_vinculo("1")) == {
        "nome": "Azul",
        "fonte": "loja",
        "disponivel": True,
        "confiavel": True,
        "variation_id": "1",
    }
    # Sincronização que não deu certo (o ML Full/catálogo fica `skipped`).
    for status in ("skipped", "retryable", "fatal", "pending"):
        assert um(_vinculo("1", status=status))["confiavel"] is False
    # Anúncio pausado ou em revisão (o `anuncio_status` do ML): não confiável.
    for parado in ("paused", "under_review", "inactive"):
        assert um(_vinculo("1", anuncio_status=parado))["confiavel"] is False
    assert um(_vinculo("1", anuncio_status="active"))["confiavel"] is True
    # O vínculo diz que tem, o produto do Bling diz que não (e o contrário).
    assert um(_vinculo("1", estoque_produto=0))["confiavel"] is False
    esgotado_incoerente = um(
        _vinculo("1", estoque=0, estoque_produto=5), {"variation_id": "1", "nome": "Azul"}
    )
    assert (esgotado_incoerente["disponivel"], esgotado_incoerente["confiavel"]) == (False, False)
    # Esgotado e coerente: confiável ("esgotado" pode ser dito).
    esgotado = um(
        _vinculo("1", estoque=0, estoque_produto=0),
        {"variation_id": "1", "nome": "Azul", "estoque": 0},
    )
    assert (esgotado["disponivel"], esgotado["confiavel"]) == (False, True)
    # A LOJA (agora) diz esgotado, o vínculo diz que tem: vale a loja, não confiável.
    loja = {"variation_id": "1", "nome": "Azul", "estoque": 0}
    assert (um(_vinculo("1"), loja)["disponivel"], um(_vinculo("1"), loja)["confiavel"]) == (
        False,
        False,
    )
    # Sem estoque no vínculo: o da loja diz, mas não é confiável.
    sem = um(_vinculo("1", estoque=None), {**loja, "estoque": 4})
    assert sem["disponivel"] is True and sem["confiavel"] is False
    # Só a loja (anúncio sem vínculo): disponível pela loja, nunca confiável.
    so_loja = variacoes_svc.montar_variacoes(
        [
            {"variation_id": "1", "nome": "Azul", "estoque": 2},
            {"variation_id": "2", "nome": "Verde", "estoque": 0},
        ],
        [],
    )
    assert [(v["disponivel"], v["confiavel"]) for v in so_loja["variacoes"]] == [
        (True, False),
        (False, False),
    ]
    # Só os vínculos, com o nome do título atual: a régua do vínculo.
    so_vinculos = variacoes_svc.montar_variacoes(
        None,
        [
            _vinculo("1", titulo="T - Azul"),
            _vinculo("2", titulo="T - Verde", estoque=0, estoque_produto=0),
            _vinculo("3", titulo="T - Rosa", status="fatal"),
        ],
        titulo="T",
    )
    assert [(v["disponivel"], v["confiavel"]) for v in so_vinculos["variacoes"]] == [
        (True, True),
        (False, True),
        (True, False),
    ]
    # O mesmo, com o nome do cadastro (sem o título atual): nunca confiável.
    cadastro = variacoes_svc.montar_variacoes(
        None, [_vinculo("1", produto="X - Azul"), _vinculo("2", produto="X - Verde")]
    )
    assert [v["confiavel"] for v in cadastro["variacoes"]] == [False, False]
    # Vínculo pausado SEM a lista da loja: esgotado e não confiável.
    pausado = variacoes_svc.montar_variacoes(
        None,
        [
            _vinculo("1", titulo="T - Azul", anuncio_status="paused"),
            _vinculo("2", titulo="T - Verde"),
        ],
        titulo="T",
    )
    assert (pausado["variacoes"][0]["disponivel"], pausado["variacoes"][0]["confiavel"]) == (
        False,
        False,
    )
    # Kit (dois vínculos na mesma variação): disponível só com todos.
    kit = variacoes_svc.montar_variacoes(
        [
            {"variation_id": "1", "nome": "Kit", "estoque": 0},
            {"variation_id": "2", "nome": "Avulso", "estoque": 5},
        ],
        [
            _vinculo("1"),
            _vinculo("1", estoque=0, estoque_produto=0),
            _vinculo("2"),
        ],
        titulo="T",
    )
    assert (kit["variacoes"][0]["disponivel"], kit["variacoes"][0]["confiavel"]) == (False, True)


def test_loja_manda_variacao_apagada_sai_e_sem_escolha_sem_bloco():
    loja = [
        {"variation_id": BRANCO, "nome": "Branco", "estoque": 1},
        {"variation_id": "77", "nome": "Preto", "estoque": 1},
    ]
    bloco = variacoes_svc.montar_variacoes(loja, VINCULOS_KIA, titulo=TITULO_A18)
    # O Laranja tem vínculo vivo, mas a loja não o tem mais.
    assert [v["nome"] for v in bloco["variacoes"]] == ["Branco", "Preto"]
    # Anúncio da Shopee sem variação (o vínculo "0") e o do ML sem variação.
    assert variacoes_svc.montar_variacoes([], [_vinculo("0")]) is None
    assert variacoes_svc.montar_variacoes(None, [_vinculo("0")]) is None
    assert variacoes_svc.montar_variacoes(None, [_vinculo(None, produto="X")]) is None
    assert variacoes_svc.montar_variacoes(None, []) is None
    # Duas variações sem nome nenhum: nada a dizer.
    assert variacoes_svc.montar_variacoes(None, [_vinculo("1"), _vinculo("2")]) is None


def test_lista_da_loja_incompleta_variacao_sem_nome_e_vinculo_mais_novo():
    """Revisão de 20/10/2026: a variação da loja sem nome sai da lista e conta
    como omitida; o vínculo criado depois da ida à loja (variação nova no
    anúncio) também tira o "lista completa"."""
    loja = [
        {"variation_id": "1", "nome": "Azul", "estoque": 1},
        {"variation_id": "2", "nome": "Verde", "estoque": 1},
        {"variation_id": "3", "nome": None, "estoque": 1},
    ]
    bloco = variacoes_svc.montar_variacoes(loja, [])
    assert [v["nome"] for v in bloco["variacoes"]] == ["Azul", "Verde"]
    assert bloco["variacoes_completas"] is False and bloco["variacoes_omitidas"] == 1
    buscada = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    antes = _vinculo("1", criado_em=buscada - timedelta(days=3))
    depois = _vinculo("2", criado_em=buscada + timedelta(minutes=5))
    so_dois = loja[:2]
    assert variacoes_svc.montar_variacoes(so_dois, [antes], buscada_em=buscada)[
        "variacoes_completas"
    ]
    novo = variacoes_svc.montar_variacoes(so_dois, [antes, depois], buscada_em=buscada)
    assert novo["variacoes_completas"] is False


def test_teto_guarda_a_selecionada_a_pergunta_e_as_disponiveis():
    cores = [
        "Azul",
        "Verde",
        "Rosa",
        "Lilás",
        "Cinza",
        "Bege",
        "Marrom",
        "Nude",
        "Coral",
        "Vinho",
        "Prata",
        "Dourado",
        "Grafite",
        "Amarelo",
        "Laranja",
        "Preto",
        "Branco",
        "Turquesa",
        "Lavanda",
        "Mostarda",
    ]
    loja = [
        {"variation_id": str(i), "nome": c, "estoque": 0 if i < 15 else 5}
        for i, c in enumerate(cores)
    ]
    vinculos = [
        _vinculo(str(i), estoque=0 if i < 15 else 5, estoque_produto=0 if i < 15 else 5)
        for i in range(len(cores))
    ]
    bloco = variacoes_svc.montar_variacoes(
        loja,
        vinculos,
        variation_id="3",
        palavras=ficha.palavras_da_pergunta(["tem na cor vinho?"]),
        teto=4,
    )
    nomes = [v["nome"] for v in bloco["variacoes"]]
    # Na ordem do anúncio: a selecionada (Lilás), a perguntada (Vinho) e as
    # disponíveis confiáveis (Preto, Branco…) até o teto.
    assert nomes == ["Lilás", "Vinho", "Preto", "Branco"]
    assert bloco["variacoes_omitidas"] == 16 and bloco["variacoes_completas"] is False


def test_o_agente_nunca_ve_quantidade_sku_nem_id():
    """Spec 4.2: o agente vê nome, disponível, confiável e fonte (e a
    selecionada) — o sku e o variation_id ficam no registro."""
    registro = variacoes_svc.montar_variacoes(
        variacoes_svc.da_shopee(MODELOS_A18), VINCULOS_KIA, titulo=TITULO_A18, variation_id=BRANCO
    )
    agente = variacoes_svc.para_o_agente(registro)
    assert agente == {
        "variacoes": [
            {
                "nome": "A18, Branco NFC",
                "disponivel": True,
                "confiavel": True,
                "fonte": "loja",
                "selecionada": True,
            },
            {"nome": "A18, Laranja NFC", "disponivel": True, "confiavel": True, "fonte": "loja"},
        ],
        "completas": True,
        "omitidas": 0,
    }
    texto = json.dumps(agente)
    assert "dg089" not in texto and BRANCO not in texto and "175" not in texto
    # O registro fica inteiro (a conferência lê o sku dele).
    assert registro["variacoes"][0]["sku"] == "dg089.ci+a003.ci"
    # Na ficha do agente: as chaves da ficha; sem bloco, nada.
    assert variacoes_svc.na_ficha(agente) == {
        "variacoes": agente["variacoes"],
        "variacoes_completas": True,
    }
    assert variacoes_svc.na_ficha({**agente, "omitidas": 3})["variacoes_omitidas"] == 3
    assert variacoes_svc.na_ficha(None) == {} and variacoes_svc.para_o_agente(None) is None
    # `disponivel` desconhecido vai como null (o agente não afirma).
    sem_estoque = variacoes_svc.para_o_agente(
        {"variacoes": [{"nome": "A", "fonte": "cadastro", "confiavel": False}]}
    )
    assert sem_estoque["variacoes"][0]["disponivel"] is None


# ─────────────── a conferência da resposta (pura) ───────────────


def _fatos(variacoes: list[dict], completas: bool = True, titulo: str = TITULO_A18) -> dict:
    return {
        "ficha_do_anuncio": [
            {
                "de_onde": ficha.DE_ONDE_ANUNCIO,
                "titulo": titulo,
                "variacoes": variacoes,
                "variacoes_completas": completas,
            }
        ]
    }


KIA = [
    {"nome": "A18 - Branco NFC", "disponivel": True, "confiavel": True},
    {"nome": "A18 - Laranja NFC", "disponivel": True, "confiavel": True},
]
KIA_LARANJA_ESGOTADO = [KIA[0], {**KIA[1], "disponivel": False}]
MEMORIAS = [
    {"nome": "Preto, 128 GB", "disponivel": True, "confiavel": True},
    {"nome": "Preto, 256 GB", "disponivel": False, "confiavel": True},
    {"nome": "Branco, 128 GB", "disponivel": True, "confiavel": False},
]
# O S7 da Kia: a memória escrita sem "GB" ("o dourado 8.128").
S7 = [
    {"nome": "Dourado, 8.128 GB", "disponivel": False, "confiavel": True},
    {"nome": "Dourado, 12.256 GB", "disponivel": True, "confiavel": True},
]
# Dois anúncios na conversa: a conferência é só contra o 1º (o mais recente).
DUAS_FICHAS_1A_INCOMPLETA = {
    "ficha_do_anuncio": [
        {"titulo": TITULO_A18, "variacoes": KIA, "variacoes_completas": False},
        {"titulo": "Outro", "variacoes": KIA, "variacoes_completas": True},
    ]
}
DUAS_FICHAS_BRANCO = {
    "ficha_do_anuncio": [
        {
            "titulo": "Capa X",
            "variacoes": [
                {"nome": "Branco", "disponivel": False, "confiavel": True},
                {"nome": "Preto", "disponivel": True, "confiavel": True},
            ],
            "variacoes_completas": True,
        },
        {
            "titulo": "Capa Y",
            "variacoes": [
                {"nome": "Branco", "disponivel": True, "confiavel": True},
                {"nome": "Azul", "disponivel": True, "confiavel": True},
            ],
            "variacoes_completas": True,
        },
    ]
}


def _conferir(
    resposta: str, fatos: dict, cliente: list[str], *, aprovado: bool = True
) -> list[tuple[str, bool]]:
    return [
        (p.motivo, p.bloqueia)
        for p in variacoes_svc.conferir_variacoes(
            resposta, fatos, cliente, estoque_aprovado=aprovado
        )
    ]


def test_a_resposta_da_equipe_passa():
    cliente = [PERGUNTA_BORDO, PERGUNTA_FONE]
    assert _conferir(RESPOSTA_CERTA + " E sim, o fone UFB10 acompanha.", _fatos(KIA), cliente) == []
    assert _conferir("Olá! No momento não temos na cor bordo!", _fatos(KIA), cliente) == []
    # A IA de 20/10 (sem dado nenhum) disse o mesmo — com a lista, passa.
    ia_de_ontem = (
        "Olá! O modelo anunciado não está disponível na cor bordo, mas inclui o fone UFB10."
    )
    assert _conferir(ia_de_ontem, _fatos(KIA), cliente) == []
    # Sem a lista, "não tem bordô" não tem como ser conferido: pessoa.
    assert _conferir(ia_de_ontem, {}, cliente) == [
        ("disponibilidade de variação sem dado de estoque na ficha", False)
    ]


# Calibração com ERROS PLANTADOS (decisão 4 do dono, 20/10/2026): cada
# resposta errada aqui tem de ser pega; a certa ao lado, não.
ERROS_PLANTADOS = [
    # (resposta, ficha, cliente, bloqueia?)
    ("Olá! Temos sim o bordô!", _fatos(KIA), [PERGUNTA_BORDO], True),
    ("Olá! Temos sim!", _fatos(KIA), [PERGUNTA_BORDO], True),
    ("O bordô está disponível, pode comprar.", _fatos(KIA), [PERGUNTA_BORDO], True),
    ("O branco está esgotado no momento.", _fatos(KIA), ["tem branco?"], True),
    ("Infelizmente não temos o laranja.", _fatos(KIA), ["tem laranja?"], True),
    ("Temos o preto de 256 GB!", _fatos(MEMORIAS), ["tem o preto 256?"], True),
    ("Olá! Não temos o bordô.", _fatos(KIA, completas=False), [PERGUNTA_BORDO], False),
    ("Temos o branco!", _fatos(MEMORIAS), ["tem branco?"], False),
    ("Temos sim na cor preta.", {}, ["tem preto?"], False),
    ("São as últimas unidades do branco!", _fatos(KIA), ["tem branco?"], False),
    ("Vamos repor o laranja em breve.", _fatos(KIA), ["tem laranja?"], False),
    # Produção (rascunho real): promessa de acompanhar, que o Manual proíbe.
    ("Vamos verificar a reposição do laranja e retornaremos.", _fatos(KIA), ["volta?"], False),
    # ── Revisão de 20/10/2026 (passavam) ──
    # O "Temos sim!" quando outra frase cita uma cor sem dizer se tem.
    ("Temos sim! A cor bordô é linda.", _fatos(KIA), [PERGUNTA_BORDO], True),
    (
        "Temos sim! O laranja é o mais vendido.",
        _fatos(KIA_LARANJA_ESGOTADO),
        ["tem laranja?"],
        True,
    ),
    (
        "Temos sim! O laranja é o mais vendido.",
        _fatos(KIA_LARANJA_ESGOTADO),
        ["que cor tem?"],
        True,
    ),
    # Estoque dito depois da cor, ou o verbo negado depois dela.
    ("Sim, o bordô está com estoque.", _fatos(KIA), [PERGUNTA_BORDO], True),
    ("O bordô a gente tem sim!", _fatos(KIA), [PERGUNTA_BORDO], True),
    ("O laranja não temos mais.", _fatos(KIA), ["tem laranja?"], True),
    ("O branco tá disponível e o laranja esgotado.", _fatos(KIA), ["tem branco?"], True),
    ("Temos o branco, o laranja não temos.", _fatos(KIA), ["tem branco?"], True),
    ("O laranja infelizmente esgotou.", _fatos(KIA), ["tem laranja?"], True),
    ("Estamos sem o laranja.", _fatos(KIA), ["tem laranja?"], True),
    ("A cor laranja não aparece entre as opções.", _fatos(KIA), ["tem laranja?"], True),
    # Sem citar a opção: "há unidades", "todas as cores", "vem em".
    ("Sim, ainda há unidades disponíveis!", _fatos(KIA), [PERGUNTA_BORDO], True),
    ("Temos todas as cores!", _fatos(KIA), [PERGUNTA_BORDO], True),
    ("Esse modelo vem em bordô também.", _fatos(KIA), [PERGUNTA_BORDO], True),
    # Memória sem "GB": o dourado 8.128 está esgotado (o 12.256, não).
    ("Temos o dourado 8.128 disponível!", _fatos(S7, titulo="Uranyx S7"), ["tem dourado?"], True),
    # Negar o que é fixo no anúncio: o A18 é 16.128 (está no título).
    (
        "No momento não temos a opção de 128 GB. As disponíveis são Branco e Laranja.",
        _fatos(KIA),
        [PERGUNTA_BORDO],
        True,
    ),
    # Opção que as variações não cobrem nem o título tem: pessoa.
    ("Temos o de 256 GB!", _fatos(KIA), ["tem de 256 gb?"], False),
    ("No momento não temos 220V.", _fatos(KIA), ["tem 220v?"], False),
    # "Sim!" sem dizer a cor: deduzido — pessoa (não bloqueia).
    ("Sim! E o fone vem junto também.", _fatos(KIA), [PERGUNTA_BORDO, PERGUNTA_FONE], False),
    # Dois anúncios: a lista completa do 2º não vale para o 1º…
    ("No momento não temos o bordô.", DUAS_FICHAS_1A_INCOMPLETA, [PERGUNTA_BORDO], False),
    # …nem o branco disponível do 2º (o do 1º está esgotado).
    ("Temos o branco disponível!", DUAS_FICHAS_BRANCO, ["tem branco?"], True),
]
CERTAS = [
    (RESPOSTA_CERTA, _fatos(KIA), [PERGUNTA_BORDO]),
    # Produção (4 de 5 rascunhos que citavam reposição): a negação não promete.
    (
        "Ainda não temos uma data prevista para a reposição do laranja.",
        _fatos(KIA),
        ["quando volta o laranja?"],
    ),
    (
        "No momento não temos a quantidade exata em estoque nem a data de reposição.",
        _fatos(KIA),
        ["quantos laranja ainda tem em estoque?"],
    ),
    ("Temos o branco disponível!", _fatos(KIA), ["tem branco?"]),
    ("Temos apenas Branco e Laranja; o bordô não temos.", _fatos(KIA), [PERGUNTA_BORDO]),
    (
        "O preto de 256 GB está esgotado, mas o de 128 GB está disponível.",
        _fatos(MEMORIAS),
        ["tem o preto 256?"],
    ),
    ("Temos o preto!", _fatos(MEMORIAS), ["tem preto?"]),
    ("Confira no anúncio se o branco está disponível.", _fatos(KIA), ["tem branco?"]),
    ("Você prefere o branco ou o laranja?", _fatos(KIA), ["tem branco?"]),
    ("Não temos como confirmar a cor bordô por aqui.", _fatos(KIA), [PERGUNTA_BORDO]),
    (politicas_basicas.RESPOSTA_ESTOQUE, _fatos(KIA), [PERGUNTA_BORDO]),
    ("Sim, o celular tem NFC.", _fatos(KIA), ["tem nfc?"]),
    ("Qualquer dúvida, estamos à disposição! Estamos disponíveis.", _fatos(KIA), [PERGUNTA_BORDO]),
    ("O pedido acabou de ser enviado.", _fatos(KIA), ["ainda tem estoque?"]),
    ("A mala de bordo está disponível sim!", {"anuncio": "Mala de bordo ABS"}, ["tem a de bordo?"]),
    (
        "Olá! O pedido {numero_pedido} está em preparação e ainda não foi postado. A origem "
        "do envio não consta nos dados disponíveis do pedido.",
        {},
        ["Quantos cinza ainda tem em estoque?"],
    ),
    # ── Revisão de 20/10/2026 ──
    (
        "O branco está disponível e o laranja está esgotado.",
        _fatos(KIA_LARANJA_ESGOTADO),
        ["tem branco ou laranja?"],
    ),
    (
        "Não temos o bordô, mas o branco e o laranja estão disponíveis.",
        _fatos(KIA),
        [PERGUNTA_BORDO],
    ),
    ("Infelizmente não temos. Mas o branco é uma ótima opção!", _fatos(KIA), [PERGUNTA_BORDO]),
    (
        "O laranja está esgotado no momento, mas o branco temos sim.",
        _fatos(KIA_LARANJA_ESGOTADO),
        ["tem laranja?"],
    ),
    # O 128 GB é fixo do A18 (título): dizer que tem fala do anúncio inteiro.
    ("Temos o A18 de 128 GB nas cores Branco e Laranja.", _fatos(KIA), ["quais cores?"]),
    (
        "Temos o dourado 12.256 disponível; o 8.128 está esgotado.",
        _fatos(S7, titulo="Uranyx S7"),
        ["tem dourado?"],
    ),
    (
        "Olá! No momento não temos na cor bordô. Sim, o fone UFB10 acompanha o aparelho.",
        _fatos(KIA),
        [PERGUNTA_BORDO, PERGUNTA_FONE],
    ),
    ("Sim, temos o branco!", _fatos(KIA), ["tem branco?"]),
    ("Seu pedido foi enviado há 2 dias, na cor branca.", _fatos(KIA), ["e meu pedido?"]),
]


@pytest.mark.parametrize(("resposta", "fatos", "cliente", "bloqueia"), ERROS_PLANTADOS)
def test_calibracao_erro_plantado_e_pego(resposta, fatos, cliente, bloqueia):
    pendencias = _conferir(resposta, fatos, cliente)
    assert pendencias, resposta
    assert any(b for _m, b in pendencias) is bloqueia, pendencias


@pytest.mark.parametrize(("resposta", "fatos", "cliente"), CERTAS)
def test_calibracao_resposta_certa_passa(resposta, fatos, cliente):
    assert _conferir(resposta, fatos, cliente) == []


def test_sem_a_regra_no_manual_toda_afirmacao_de_estoque_vai_para_pessoa():
    """O Manual de hoje (`REGRA_ESTOQUE`): "sem afirmar que há ou que falta
    estoque". Afirmar — certo ou errado — vai para pessoa; contradizer o
    dado continua bloqueando; orientar e nomear as opções passa."""
    sem_regra = (variacoes_svc.MOTIVO_ESTOQUE_SEM_REGRA, False)
    assert _conferir(RESPOSTA_CERTA, _fatos(KIA), [PERGUNTA_BORDO], aprovado=False) == [sem_regra]
    assert _conferir(
        "Temos o branco disponível!", _fatos(KIA), ["tem branco?"], aprovado=False
    ) == [sem_regra]
    errada = _conferir("Olá! Temos sim o bordô!", _fatos(KIA), [PERGUNTA_BORDO], aprovado=False)
    assert sem_regra in errada and any(b for _m, b in errada)
    for neutra in (
        politicas_basicas.RESPOSTA_ESTOQUE,
        "Olá! As opções deste anúncio são A18 Branco NFC e A18 Laranja NFC; confira no próprio "
        "anúncio a disponibilidade.",
    ):
        assert _conferir(neutra, _fatos(KIA), [PERGUNTA_BORDO], aprovado=False) == []
    # Dois anúncios com variações: afirmar estoque vai para pessoa.
    assert (variacoes_svc.MOTIVO_DUAS_FICHAS, False) in _conferir(
        "Temos o branco disponível!",
        {"ficha_do_anuncio": [_fatos(KIA)["ficha_do_anuncio"][0]] * 2},
        ["tem branco?"],
    )


def test_sku_e_id_da_variacao_nunca_vao_para_o_cliente():
    com_codigo = _fatos(
        [
            {**KIA[0], "sku": "dg089.ci+a003.ci", "variation_id": BRANCO},
            {**KIA[1], "sku": "dg088.ci+a003.ci", "variation_id": LARANJA},
        ]
    )
    pego = ("cita o sku ou o id interno da variação", False)
    assert _conferir("Temos o branco (dg089.ci+a003.ci).", com_codigo, ["tem branco?"]) == [pego]
    assert _conferir(f"A variação {LARANJA} está disponível.", com_codigo, ["e o laranja?"]) == [
        pego
    ]
    assert _conferir("Temos o branco disponível!", com_codigo, ["tem branco?"]) == []


def test_motivo_diz_a_variacao():
    (motivo,) = _conferir("O branco está esgotado no momento.", _fatos(KIA), ["tem branco?"])
    assert motivo == ("diz que não tem 'A18 - Branco NFC', que está disponível", True)


def test_quando_a_pergunta_e_de_variacao():
    assert variacoes_svc.pede_variacao([PERGUNTA_BORDO])
    assert variacoes_svc.pede_variacao(["tem de 256 gb?"]) and variacoes_svc.pede_variacao(["cor?"])
    assert not variacoes_svc.pede_variacao(["qual o prazo de entrega?"])
    assert not variacoes_svc.pede_variacao(["a mala de bordo cabe no avião?"])
    assert not variacoes_svc.pede_variacao([PERGUNTA_FONE])


def test_caber_deixa_as_variacoes_fora_do_teto_do_texto():
    enorme = {"titulo": "X", "descricao": "linha de descrição\n" * 400, "atributos": ["a: b"] * 25}
    bloco = variacoes_svc.na_ficha(
        variacoes_svc.para_o_agente(
            variacoes_svc.montar_variacoes(None, VINCULOS_KIA, titulo=TITULO_A18)
        )
    )
    (f,) = ficha.caber([{"de_onde": "a", **enorme, **bloco}])
    assert f["variacoes"] == bloco["variacoes"] and f["variacoes_completas"] is False
    sem = {k: v for k, v in f.items() if k not in variacoes_svc.CHAVES_DA_FICHA}
    assert len(json.dumps([sem], ensure_ascii=False)) <= ficha.MAX_CHARS_FICHAS
    # A segunda ficha que sai inteira no corte leva as variações junto.
    segunda = {"de_onde": "b", "titulo": "Y", "descricao": "z" * 3000, **bloco}
    saida = ficha.caber([{"de_onde": "a", **enorme, **bloco}, segunda])
    assert len(saida) == 1 and saida[0]["de_onde"] == "a"


def test_caber_do_legado_e_o_de_origin():
    """Sem variações (o legado), o corte é o de antes, campo a campo."""
    fichas = [
        {"de_onde": "a", "titulo": "X", "descricao": "linha\n" * 600, "atributos": ["a: b"] * 25},
        {"de_onde": "b", "titulo": "Y", "descricao": "w\n" * 900, "atributos": ["c: d"] * 25},
    ]
    copia = json.loads(json.dumps(fichas))

    def origem(entrada: list[dict], teto: int = ficha.MAX_CHARS_FICHAS) -> list[dict]:
        copias = [
            {**f, **({"atributos": list(f["atributos"])} if f.get("atributos") else {})}
            for f in entrada
        ]
        saida = copias[:1] + [
            g for f in copias[1:] for g in ficha._encolher([f], ficha.MAX_CHARS_SEGUNDA_FICHA)
        ]
        return ficha._encolher(saida, teto)

    assert ficha.caber(fichas) == origem(copia)
    assert fichas == copia  # não muda as de entrada


# ─────────────── banco, Redis e loja ───────────────


async def _kia_com_vinculos(db: AsyncSession, integ: Integration) -> None:
    for vid, sku, cor, estoque in (
        (BRANCO, "dg089.ci+a003.ci", "Prata", 175),
        (LARANJA, "dg088.ci+a003.ci", "Laranja", 178),
    ):
        produto = Product(
            user_id=integ.user_id,
            sku=sku,
            name=f"Uranyx A18 Pro Max - S5 Edition 16.128 - {cor} + Fone UFB10",
            stock=174,
        )
        db.add(produto)
        await db.flush()
        cor_anuncio = "Branco NFC" if vid == BRANCO else "Laranja NFC"
        db.add(
            ProductLink(
                user_id=integ.user_id,
                product_id=produto.id,
                integration_id=integ.id,
                platform=IntegrationPlatform.SHOPEE,
                external_id=ITEM_A18,
                variation_id=vid,
                external_sku=sku,
                listing_title=f"{TITULO_A18} - A18 - {cor_anuncio}",
                stock=estoque,
                last_sync_status=LinkSyncStatus.OK,
            )
        )
    await db.commit()


async def _conversa_kia_a18(
    db: AsyncSession, make_user, redis_mem: RedisMem, *, fresca: bool = True, **kw
):
    conversa = await _conversa(db, make_user, pedido=None, comprador_nome="Maria", **kw)
    conversa.anuncio_id = ITEM_A18
    conversa.anuncio_titulo = None  # como nas conversas da Shopee em produção
    await db.commit()
    integ = await db.get(Integration, conversa.integration_id)
    await _kia_com_vinculos(db, integ)
    if fresca:
        bruta = ficha.ficha_bruta_shopee(ITEM_A18_HOJE)
        redis_mem.dados[ficha.chave_ficha("shopee", integ.id, ITEM_A18)] = json.dumps(
            {**bruta, "buscada_em": datetime.now(UTC).isoformat()}, ensure_ascii=False
        )
    return conversa, integ


async def test_legado_nao_ve_variacoes_nem_vai_a_loja_por_elas(
    db: AsyncSession, make_user, lojas: LojasComModelos, redis_mem: RedisMem
):
    """Decisão 1 do dono (20/10/2026): sem o opt-in, a ficha é a de origin —
    nem com pergunta de cor ela ganha variações ou vai à loja por elas."""
    conversa, _ = await _conversa_kia_a18(db, make_user, redis_mem)
    _a18_na_loja(lojas)

    (f,) = await ficha.fichas_da_conversa(db, conversa, [], [PERGUNTA_BORDO, PERGUNTA_FONE])

    assert not set(f) & set(variacoes_svc.CHAVES_DA_FICHA)
    assert set(f) == {"de_onde", "fonte", "titulo", "atributos", "descricao", "produtos_vinculados"}
    assert lojas.chamadas == []
    assert not [k for k in redis_mem.dados if k.startswith("atd:variacoes")]


async def test_agente_kia_sem_pergunta_de_cor_variacoes_pelos_vinculos_sem_ir_a_loja(
    db: AsyncSession, make_user, lojas: LojasComModelos, redis_mem: RedisMem
):
    conversa, _ = await _conversa_kia_a18(db, make_user, redis_mem)
    _a18_na_loja(lojas)

    (f,) = await ficha.fichas_da_conversa(db, conversa, [], ["tem NFC?"], com_variacoes=True)

    assert f["titulo"] == TITULO_A18 and "Uranyx S5 + Fone UFB10" in f["descricao"]
    # O título atual é o da ficha fresca (a loja): os finais dos títulos dos
    # vínculos; a régua do vínculo (spec 4.2) dá o confiável.
    assert f["variacoes"] == [
        {
            "nome": "A18 - Branco NFC",
            "disponivel": True,
            "confiavel": True,
            "fonte": "titulo_do_vinculo",
        },
        {
            "nome": "A18 - Laranja NFC",
            "disponivel": True,
            "confiavel": True,
            "fonte": "titulo_do_vinculo",
        },
    ]
    assert f["variacoes_completas"] is False
    assert lojas.chamadas == []  # "tem NFC?" não é pergunta de cor: sem ida à loja


async def test_agente_kia_vc_tem_o_bordo_vai_a_loja_so_pelas_variacoes_e_guarda_30_min(
    db: AsyncSession, make_user, lojas: LojasComModelos, redis_mem: RedisMem
):
    conversa, integ = await _conversa_kia_a18(db, make_user, redis_mem)
    _a18_na_loja(lojas)

    (f,) = await ficha.fichas_da_conversa(
        db, conversa, [], [PERGUNTA_BORDO, PERGUNTA_FONE], com_variacoes=True
    )

    # A ficha estava no cache: só as variações — o `item_status` e os modelos.
    assert lojas.metodos() == ["get_item_base_info", "get_model_list"]
    assert [v["nome"] for v in f["variacoes"]] == ["A18, Branco NFC", "A18, Laranja NFC"]
    assert f["variacoes_completas"] is True  # o bordô não existe no anúncio
    assert [(v["disponivel"], v["confiavel"]) for v in f["variacoes"]] == [(True, True)] * 2
    assert "Fone UFB10" in f["descricao"]  # o fone vem: está na descrição
    texto = json.dumps(f["variacoes"])
    assert "dg089" not in texto and BRANCO not in texto and "175" not in texto
    chave = variacoes_svc.chave_variacoes("shopee", integ.id, ITEM_A18)
    assert redis_mem.ttls[chave] == variacoes_svc.CACHE_VARIACOES_S
    guardada = json.loads(redis_mem.dados[chave])
    assert guardada["buscada_em"] and guardada["variacoes"][0]["estoque"] == 175
    assert not [k for k in redis_mem.dados if k.startswith("atd:variacoes_busca:")]
    # A ida pelas variações conta no minuto PRÓPRIO, não no da ficha.
    assert redis_mem.dados[f"atd:variacoes_limite:{integ.id}"] == "1"
    assert f"atd:ficha_limite:{integ.id}" not in redis_mem.dados

    # De novo (outra sugestão): do cache, sem ida à loja.
    (f2,) = await ficha.fichas_da_conversa(db, conversa, [], [PERGUNTA_BORDO], com_variacoes=True)
    assert f2["variacoes"] == f["variacoes"] and len(lojas.chamadas) == 2


async def test_ferramenta_ver_anuncio_vai_a_loja_pelas_variacoes(
    db: AsyncSession, make_user, lojas: LojasComModelos, redis_mem: RedisMem
):
    """A `ver_anuncio` do agente: `variacoes_do_anuncio` com a `Busca` vai à
    loja sem precisar de pergunta de cor; o registro tem os códigos."""
    conversa, integ = await _conversa_kia_a18(db, make_user, redis_mem)
    _a18_na_loja(lojas)
    busca = ficha_loja.Busca(integ.id, conversa.canal_id)

    bloco = await variacoes_svc.variacoes_do_anuncio(
        db, IntegrationPlatform.SHOPEE, integ.id, ITEM_A18, loja=busca
    )

    assert bloco == {
        "variacoes": [
            {"nome": "A18, Branco NFC", "disponivel": True, "confiavel": True, "fonte": "loja"},
            {"nome": "A18, Laranja NFC", "disponivel": True, "confiavel": True, "fonte": "loja"},
        ],
        "completas": True,
        "omitidas": 0,
    }
    assert busca.restam_variacoes == ficha_loja.MAX_BUSCAS_VARIACOES_POR_SUGESTAO - 1
    assert busca.restam == ficha_loja.MAX_BUSCAS_POR_SUGESTAO
    registro = await variacoes_svc.registro_do_anuncio(
        db, IntegrationPlatform.SHOPEE, integ.id, ITEM_A18
    )
    assert [v["sku"] for v in registro["variacoes"]] == ["dg089.ci+a003.ci", "dg088.ci+a003.ci"]
    # Outra loja / anúncio que não existe nos vínculos nem no cache: nada.
    assert (
        await variacoes_svc.variacoes_do_anuncio(db, IntegrationPlatform.SHOPEE, None, ITEM_A18)
        is None
    )


async def test_anuncio_fora_do_ar_na_shopee_sai_esgotado_e_nao_confiavel(
    db: AsyncSession, make_user, lojas: LojasComModelos, redis_mem: RedisMem
):
    """Revisão de 20/10/2026: o `get_model_list` não diz se o anúncio está no
    ar; o `item_status` do `get_item_base_info` diz. Deslistado: a loja diz
    esgotado (vale a loja), o vínculo diz que tem → não confiável."""
    conversa, _ = await _conversa_kia_a18(db, make_user, redis_mem)
    _a18_na_loja(lojas)
    lojas.shopee[ITEM_A18] = {**ITEM_A18_HOJE, "item_status": "UNLIST"}

    (f,) = await ficha.fichas_da_conversa(db, conversa, [], [PERGUNTA_BORDO], com_variacoes=True)

    assert [(v["disponivel"], v["confiavel"]) for v in f["variacoes"]] == [(False, False)] * 2


async def test_variacoes_da_loja_erro_cache_negativo_e_cota_da_sugestao(
    db: AsyncSession, make_user, lojas: LojasComModelos, redis_mem: RedisMem
):
    conversa, integ = await _conversa_kia_a18(db, make_user, redis_mem)
    lojas.erro = _erro_http(503)

    (f,) = await ficha.fichas_da_conversa(db, conversa, [], [PERGUNTA_BORDO], com_variacoes=True)

    # A loja falhou: fica com os vínculos (a ficha não cai).
    assert [v["nome"] for v in f["variacoes"]] == ["A18 - Branco NFC", "A18 - Laranja NFC"]
    negativa = ficha_loja.chave_sem_variacoes("shopee", integ.id, ITEM_A18)
    assert redis_mem.dados[negativa] == ficha_loja.RESULTADO_ERRO
    assert redis_mem.ttls[negativa] == ficha_loja.CACHE_FALHA_S
    # Com o negativo, não insiste.
    lojas.erro = None
    await ficha.fichas_da_conversa(db, conversa, [], [PERGUNTA_BORDO], com_variacoes=True)
    assert lojas.metodos() == ["get_item_base_info"]
    # A cota da sugestão é própria (a da ficha continua inteira).
    busca = ficha_loja.Busca(integ.id, None, restam_variacoes=0)
    assert await ficha_loja.variacoes_da_loja(db, busca, SHOPEE, ITEM_A18) is None
    assert busca.restam == ficha_loja.MAX_BUSCAS_POR_SUGESTAO


async def test_cota_e_espera_da_rodada_das_variacoes_sao_proprias(
    db: AsyncSession, make_user, lojas: LojasComModelos, redis_mem: RedisMem, monkeypatch
):
    """Revisão de 20/10/2026 ("a cota é a mesma da ficha"): a ida pelas
    variações tem conta própria na rodada; a da ficha (o legado) não perde vez."""
    conversa, integ = await _conversa_kia_a18(db, make_user, redis_mem, fresca=False)
    _a18_na_loja(lojas)
    monkeypatch.setattr(ficha_loja, "MAX_BUSCAS_VARIACOES_POR_RODADA", 1)
    monkeypatch.setattr(ficha_loja, "MAX_BUSCAS_POR_RODADA", 1)

    with ficha_loja.rodada():
        primeira = await ficha_loja.variacoes_da_loja(
            db, ficha_loja.Busca(integ.id, None), SHOPEE, ITEM_A18
        )
        segunda = await ficha_loja.variacoes_da_loja(
            db, ficha_loja.Busca(integ.id, None), SHOPEE, "123"
        )
        da_ficha = await ficha_loja.ficha_da_loja(
            db, ficha_loja.Busca(integ.id, None), SHOPEE, ITEM_A18
        )
        rodada = ficha_loja._RODADA.get()
        assert rodada.por_loja_variacoes == {str(integ.id): 1}
        assert rodada.por_loja == {str(integ.id): 1}

    assert [v["nome"] for v in primeira] == ["A18, Branco NFC", "A18, Laranja NFC"]
    assert segunda is None  # a cota das variações acabou…
    assert da_ficha is not None and da_ficha["titulo"] == TITULO_A18  # …a da ficha, não


async def test_busca_da_ficha_da_tiktok_guarda_as_variacoes_da_mesma_resposta(
    db: AsyncSession, make_user, lojas: LojasComModelos, redis_mem: RedisMem
):
    user = await make_user()
    loja = await _loja(db, user, TIKTOK, "tiktok", shop_cipher="c1")
    item = "1729000000000000001"
    lojas.tiktok[item] = {
        "code": 0,
        "data": {
            "id": item,
            "title": "Relógio X",
            "description": "<p>Tela AMOLED</p>",
            "skus": [
                {
                    "id": "11",
                    "sales_attributes": [{"value_name": "Preto"}],
                    "inventory": [{"quantity": 3}],
                },
                {
                    "id": "12",
                    "sales_attributes": [{"value_name": "Prata"}],
                    "inventory": [{"quantity": 0}],
                },
            ],
        },
    }
    busca = ficha_loja.Busca(loja.id, None)

    bruta = await ficha_loja.ficha_da_loja(db, busca, TIKTOK, item)

    assert bruta["titulo"] == "Relógio X" and "_variacoes" not in bruta
    guardada = json.loads(redis_mem.dados[variacoes_svc.chave_variacoes("tiktok", loja.id, item)])
    assert [(v["nome"], v["estoque"]) for v in guardada["variacoes"]] == [
        ("Preto", 3),
        ("Prata", 0),
    ]
    assert "_variacoes" not in json.loads(
        redis_mem.dados[ficha.chave_ficha("tiktok", loja.id, item)]
    )


async def test_enriquecimento_guarda_as_variacoes_do_get_model_list(
    db: AsyncSession, make_user, redis_falso
):
    integ = await _integ_enriquecer(db, await make_user(), IntegrationPlatform.SHOPEE, "Kia")
    f = ShopeeFalso()
    f.produtos[ITEM_A18] = {**item_shopee(int(ITEM_A18), com_variacao=True), **ITEM_A18_HOJE}
    f.modelos[ITEM_A18] = MODELOS_A18
    f.produtos["77"] = item_shopee(77, com_variacao=False)
    f.produtos["78"] = {
        **item_shopee(78, com_variacao=True),
        **{k: v for k, v in ITEM_A18_HOJE.items() if k != "item_id"},
        "item_status": "BANNED",
    }
    f.modelos["78"] = MODELOS_A18

    c = await enriquecer.cartao_produto(db, integ, f, "shopee", ITEM_A18)
    await enriquecer.cartao_produto(db, integ, f, "shopee", "77")
    await enriquecer.cartao_produto(db, integ, f, "shopee", "78")

    assert c["preco"] == 899.9 and f.contagem("variacoes") == 2  # a MESMA ida do preço
    chave = variacoes_svc.chave_variacoes("shopee", integ.id, ITEM_A18)
    assert redis_falso.ttl[chave] == variacoes_svc.CACHE_VARIACOES_S
    lista = json.loads(redis_falso.dados[chave])["variacoes"]
    assert [v["nome"] for v in lista] == ["A18, Branco NFC", "A18, Laranja NFC"]
    # Sem variação: a lista vazia (a sugestão não vai à loja por ela).
    sem = json.loads(redis_falso.dados[variacoes_svc.chave_variacoes("shopee", integ.id, "77")])
    assert sem["variacoes"] == []
    # Anúncio banido: tudo esgotado.
    banido = json.loads(redis_falso.dados[variacoes_svc.chave_variacoes("shopee", integ.id, "78")])
    assert [v["estoque"] for v in banido["variacoes"]] == [0, 0]


async def test_vinculo_pausado_no_ml_e_lista_mais_velha_que_o_vinculo(
    db: AsyncSession, make_user, redis_mem: RedisMem
):
    """O `anuncio_status` do vínculo (o ML grava) e o `created_at` chegam à
    montagem: pausado → não confiável; vínculo mais novo que a lista da loja
    → a lista do cache NÃO vale (pode faltar a variação nova): os vínculos."""
    user = await make_user()
    integ = await _loja(db, user, ML, "ml")
    item = "MLB123"
    for vid, cor in (("1", "Azul"), ("2", "Verde")):
        produto = Product(user_id=user.id, sku=f"MOC-{cor}", name=f"Moc - {cor}", stock=5)
        db.add(produto)
        await db.flush()
        db.add(
            ProductLink(
                user_id=user.id,
                product_id=produto.id,
                integration_id=integ.id,
                platform=IntegrationPlatform.ML,
                external_id=item,
                variation_id=vid,
                stock=5,
                last_sync_status=LinkSyncStatus.OK,
                anuncio_status="paused" if vid == "1" else "active",
            )
        )
    await db.commit()
    lista = [
        {"variation_id": "1", "nome": "Azul", "estoque": 5},
        {"variation_id": "2", "nome": "Verde", "estoque": 5},
    ]
    chave = variacoes_svc.chave_variacoes("ml", integ.id, item)
    redis_mem.dados[chave] = variacoes_svc.para_o_cache(lista, datetime.now(UTC))

    bloco = await variacoes_svc.variacoes_do_anuncio(db, IntegrationPlatform.ML, integ.id, item)

    assert [(v["nome"], v["confiavel"], v["fonte"]) for v in bloco["variacoes"]] == [
        ("Azul", False, "loja"),
        ("Verde", True, "loja"),
    ]
    assert bloco["completas"] is True
    # A lista de ontem, com vínculos criados hoje: não vale (o nome do cadastro).
    redis_mem.dados[chave] = variacoes_svc.para_o_cache(
        lista, datetime.now(UTC) - timedelta(days=1)
    )
    velha = await variacoes_svc.variacoes_do_anuncio(db, IntegrationPlatform.ML, integ.id, item)
    assert [(v["nome"], v["fonte"], v["confiavel"]) for v in velha["variacoes"]] == [
        ("Azul", "cadastro", False),
        ("Verde", "cadastro", False),
    ]
    assert velha["completas"] is False
    # O pausado, sem a loja, sai esgotado.
    assert velha["variacoes"][0]["disponivel"] is False


async def test_sem_anuncio_na_conversa_nenhuma_ficha(
    db: AsyncSession, make_user, lojas: LojasComModelos, redis_mem: RedisMem
):
    """O caso robertasoares831: o comprador perguntou de um produto sem cartão
    nem pedido. Sem anúncio, nenhuma ficha (nem variação): o agente pergunta
    qual é o produto, em vez de chutar."""
    conversa = await _conversa(db, make_user, pedido=None, comprador_nome="Roberta")
    assert conversa.anuncio_id is None

    assert await ficha.fichas_da_conversa(db, conversa, [], ["tem na cor rosa?"]) == []
    assert (
        await ficha.fichas_da_conversa(db, conversa, [], ["tem na cor rosa?"], com_variacoes=True)
        == []
    )
    assert lojas.chamadas == []
