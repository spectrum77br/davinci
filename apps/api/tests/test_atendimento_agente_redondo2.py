# ruff: noqa: F811, E501 — fixtures importadas do laço e do e2e; as tabelas de casos reais
"""Rodada "redondo" (23/10/2026), parte 2: os itens 5 a 7.

5. O agente passa ao Sonnet 5.5 (decisão do dono; o eval cego de 23/10: o Sonnet melhor em
   9 de 20, o Haiku em 5): o padrão `claude-sonnet-5-5`, effort medium, o prazo cooperativo
   de 60 s e os tetos de US$ 0,50 por conversa e US$ 10 por dia; o pedido sem `thinking`
   nem amostragem e com o `fallbacks: "default"`; a recusa vai para pessoa SEM texto (nunca
   um envio vazio); o preço do Sonnet 5 que o fallback do servidor devolve; o verificador
   continua no Haiku.
6. A busca no catálogo da própria loja (`buscar_anuncios_da_loja`, `agente.catalogo`): só a
   mesma integração e plataforma, vínculo vivo, sem preço, quantidade, SKU nem link; a
   guarda do acessório (só o anúncio cujo TÍTULO é dele — nem o "Case …" que é celular, nem
   a air fryer "com tigela de vidro"); a conferência G1 (`checagens.
   conferir_outro_produto_da_loja`); o `ver_anuncio` de outro anúncio que não empresta
   característica ao produto da conversa; o verificador v10 (a BUSCA NO CATÁLOGO DA LOJA e
   o grupo `catalogo` da calibração). Casos reais: o vidro de 4 L da air fryer (Barbosa,
   22/10 14:28–16:27), a capa do A18 (Kia, 21–22/10), "tem o A18?" do anúncio do A17, o
   Doogee Fire 7 Ultra (a loja só tem o Fire 6 Power).
7. O COMO PENSAR do bloco 2 (no lugar do "só o que foi perguntado", que ele contém).

Nenhuma chamada a LLM, à loja ou à produção. Os títulos dos anúncios são os de produção
(catálogo da loja, sem dado de cliente).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from anthropic.lib.tools import ToolError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models import Integration, IntegrationPlatform, Product, ProductLink
from app.models.enums import LinkSyncStatus
from app.services.atendimento import agente, constantes, ia
from app.services.atendimento.agente import (
    catalogo,
    checagens,
    custo,
    ferramentas,
    prompt,
)
from app.services.atendimento.agente import contexto as agente_ctx
from app.services.atendimento.agente import verificador as v
from scripts.agente_eval import calibrar_verificador as calibrar
from tests.test_atendimento_agente_e2e import (  # noqa: F401 — fixtures
    NAO_DO_VERIFICADOR,
    _aviso_no_verificador,
    _decisao,
    _do_agente,
    _do_verificador,
    _usa,
    agente_ligado,
    api,
    envios,
    ia_ligada,
)
from tests.test_atendimento_agente_e2e import _final as _final_e2e
from tests.test_atendimento_agente_ferramentas import _base as _base_db
from tests.test_atendimento_agente_ferramentas import _chamar, _ctx
from tests.test_atendimento_agente_laco import (  # noqa: F401 — fixtures
    _categorias_do_manual,
    _ferramenta,
    _final,
    _resposta,
    _rodar,
    _texto,
    ajustes,
    pecas,
    redis_falso,
)
from tests.test_atendimento_agente_redondo import _auto, _cli, _menu, _proposta
from tests.test_atendimento_ia import _conversa, _integ

SONNET = "claude-sonnet-5-5"
HAIKU = "claude-haiku-5-5"


# ═══════════════ 5. o Sonnet 5.5 ═══════════════


def _padrao(nome: str) -> Any:
    return Settings.model_fields[nome].default


def test_5_o_padrao_e_o_sonnet_e_o_verificador_continua_no_haiku():
    assert _padrao("atendimento_agente_modelo") == SONNET
    assert _padrao("atendimento_agente_effort") == "medium"
    assert _padrao("atendimento_agente_prazo_s") == 60
    assert _padrao("atendimento_agente_teto_usd_conversa") == 0.50
    assert _padrao("atendimento_agente_teto_usd_dia") == 10.0
    assert v.MODELO == HAIKU and v.argumentos("x")["model"] == HAIKU
    # O Sonnet 5.5 tem o fallback do servidor; o Haiku não.
    assert agente._com_fallback(SONNET) and not agente._com_fallback(HAIKU)


def test_5_o_preco_do_sonnet_e_o_do_sonnet_5_do_fallback():
    sonnet = custo.preco_do_modelo(SONNET)
    assert sonnet == custo.preco_do_modelo("claude-sonnet-5") == custo._SONNET
    assert custo.preco_do_modelo("claude-sonnet-5-20261001") == custo._SONNET
    assert custo.preco_do_modelo("claude-sonnet-5-5-20261020") == custo._SONNET
    # O fallback refeito no Sonnet 5: cada iteração pelo preço do SEU modelo.
    uso = {
        "input_tokens": 0,
        "output_tokens": 500,
        "iterations": [
            {"type": "message", "model": SONNET, "input_tokens": 2_000, "output_tokens": 0},
            {
                "type": "fallback_message",
                "model": "claude-sonnet-5",
                "input_tokens": 2_000,
                "output_tokens": 500,
            },
        ],
    }
    esperado = (2_000 * 2.0 + 2_000 * 2.0 + 500 * 10.0) / 1e6
    assert custo.custo_do_uso(uso, SONNET) == pytest.approx(esperado)


async def test_5_o_teto_novo_da_conversa_cabe_o_sonnet_frio(redis_falso, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(
        s, "atendimento_agente_teto_usd_dia", _padrao("atendimento_agente_teto_usd_dia")
    )
    monkeypatch.setattr(
        s, "atendimento_agente_teto_usd_conversa", _padrao("atendimento_agente_teto_usd_conversa")
    )
    # A 1ª requisição fria do eval S1 (~US$ 0,09–0,11): o prefixo de 1 h gravado e a saída.
    frio = {
        "input_tokens": 1_500,
        "cache_creation_input_tokens": 20_000,
        "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 20_000},
        "output_tokens": 1_200,
    }
    uso = custo.Uso()
    gasto = await custo.registrar(frio, SONNET, conversa_id="c-sonnet", uso=uso)
    assert 0.09 <= gasto <= 0.11
    # Com o teto de antes (US$ 0,05) a mesma requisição parava a conversa.
    monkeypatch.setattr(s, "atendimento_agente_teto_usd_conversa", 0.05)
    with pytest.raises(custo.TetoEstourado):
        await custo.registrar(frio, SONNET, conversa_id="c-haiku", uso=custo.Uso())


async def test_5_o_pedido_do_sonnet_pelo_padrao(pecas, monkeypatch):
    monkeypatch.setattr(
        get_settings(), "atendimento_agente_modelo", _padrao("atendimento_agente_modelo")
    )
    r, api, _ = await _rodar(pecas, [_final()])
    corpo = api.pedidos[0]
    assert corpo["model"] == SONNET
    # Sonnet 5.5: nada de `thinking` (o {"type": "disabled"} dá 400 nele) nem amostragem.
    for proibido in ("thinking", "temperature", "top_p", "top_k"):
        assert proibido not in corpo
    assert corpo["output_config"]["effort"] == "medium"
    assert corpo["output_config"]["format"]["type"] == "json_schema"
    assert corpo["max_tokens"] == agente.MAX_TOKENS
    assert corpo["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in api.cabecalhos[0]["anthropic-beta"]
    assert r.parada == agente.PARADA_FIM and r.fatos_agente["modelo"] is not None


async def test_5_a_recusa_do_sonnet_depois_de_uma_ferramenta_vai_para_pessoa_sem_texto(
    pecas, monkeypatch
):
    monkeypatch.setattr(get_settings(), "atendimento_agente_modelo", SONNET)
    recusa = _resposta(
        [],
        "refusal",
        model=SONNET,
        stop_details={"type": "refusal", "category": "general_harms", "explanation": None},
    )
    r, api, _ = await _rodar(pecas, [_resposta([_ferramenta()], "tool_use", model=SONNET), recusa])
    assert r.parada == agente.PARADA_RECUSA and r.gravar
    assert r.texto is None and r.status == constantes.RASCUNHO_BLOQUEADO
    assert r.precisa_humano and r.motivo == "recusa do modelo (general_harms)"
    # Nada do texto da recusa vai à conferência (nem ao envio).
    assert pecas.conferir == [] and len(api.pedidos) == 2


async def test_5_o_raciocinio_antes_do_json_nao_atrapalha_a_proposta(pecas, monkeypatch):
    monkeypatch.setattr(get_settings(), "atendimento_agente_modelo", SONNET)
    final = _resposta(
        [
            {"type": "thinking", "thinking": "", "signature": "sig"},
            _texto(json.dumps({**json.loads(_final()["content"][0]["text"])}, ensure_ascii=False)),
        ],
        "end_turn",
        model=SONNET,
    )
    r, _api, _ = await _rodar(pecas, [final])
    assert r.parada == agente.PARADA_FIM and r.texto


# ═══════════════ 6. a busca no catálogo da loja ═══════════════

ITEM_CESTO = "22995184839"
ITEM_AIRFRYER = "58212957869"
ITEM_A18 = "58265493964"
ITEM_A17 = "22394778711"
ITEM_FIRE6 = "23598009683"
ITEM_SPUNK = "23998040257"
ITEM_CARREGADOR = "22499538778"
ITEM_CASE = "99999999901"  # o "Case …" que é celular (a guarda)


def _vinculos(*linhas: tuple[str, str, str, int | None, bool]) -> dict[str, list]:
    saida: dict[str, list] = {}
    for item, variacao, titulo, estoque, ok in linhas:
        saida.setdefault(item, []).append(catalogo.Vinculo(item, variacao, titulo, estoque, ok))
    return saida


# Os títulos de produção (Barbosa e Kia Shopee, 23/10/2026).
LOJA = _vinculos(
    (ITEM_CESTO, "1", "Uranyx Cesto De Vidro Para Air Fryers - Cesto 2,5 Litros", 0, True),
    (ITEM_CESTO, "2", "Uranyx Cesto De Vidro Para Air Fryers - Cesto 4 Litros", 0, True),
    (ITEM_CESTO, "3", "Uranyx Cesto De Vidro Para Air Fryers - Cesto 6 Litros", 0, True),
    (
        ITEM_AIRFRYER,
        "1",
        "AirFryer Fritadeira Digital Uranyx 4L 1500w de Vidro Tela Touch 110-220v - 110v",
        0,
        True,
    ),
    (
        ITEM_AIRFRYER,
        "2",
        "AirFryer Fritadeira Digital 4 Litros Tigela de Vidro Tela Touch 110-220v - 220v",
        0,
        True,
    ),
    (ITEM_A18, "1", "Uranyx A18 Pro Max NFC 16.128 Gb Memoria S5 Premium - A18 - Branco NFC", 175, True),
    (ITEM_A18, "2", "Uranyx A18 Pro Max NFC 16.128 Gb Memoria S5 Premium - A18 - Laranja NFC", 166, True),
    (ITEM_A17, "1", "Uranyx A17 Pro Max 12.128 ou 12.64 GB Memoria - Laranja,12 GB / 128 GB", 300, True),
    (
        ITEM_A17,
        "2",
        "Uranyx Hotwav A17 Pro Max 12.128 ou 12.64 GB Memoria - Prata,12 GB / 64 GB",
        200,
        True,
    ),
    (ITEM_FIRE6, "1", "Doogee Fire 6 Power 20.256 GB - - Resistente a água e Queda - NFC - Preto,20 GB / 256 GB", 26, True),
    (ITEM_SPUNK, "1", "Doogee Spunk Pro 20.512 GB - Resistente a água e Queda - NFC - Laranja,20 GB / 512 GB", 20, True),
    (
        ITEM_CARREGADOR,
        "1",
        "Carregador Rápido 50W Compatível Motorola Apple iPhone 20W Fonte USB-C - Lightning x USB-C",
        0,
        True,
    ),
    (ITEM_CASE, "1", "Case Uranyx A17 Pro Max 12.128 GB Celular - Preto", 50, True),
)  # fmt: skip


def _busca(termo: str) -> list[catalogo.Achado]:
    return catalogo.buscar_nos_vinculos(catalogo.ler_termo(termo), LOJA)


def test_6_o_termo_e_a_guarda_do_acessorio():
    t = catalogo.ler_termo("capa pro Uranyx A 18 Pró Max")
    # ("pro" de "capa pro Uranyx" é também a palavra do modelo: todas casam no A18 Pro Max.)
    assert set(t.obrigatorias) == {"capa", "a18", "pro", "max"} and t.opcionais == ("uranyx",)
    assert t.acessorios == {"capa"} and t.modelo
    assert catalogo.ler_termo("recipiente de vidro").acessorios == {"cesto", "vidro"}
    assert catalogo.ler_termo("cesto 4L").obrigatorias == ("cesto", "4", "litro")
    assert catalogo.ler_termo("o celular").vazio is False
    assert catalogo.ler_termo("de da do").vazio
    # O TÍTULO é do acessório (entre as 3 primeiras palavras, e não é de celular).
    assert catalogo.titulo_do_acessorio("Uranyx Cesto De Vidro Para Air Fryers", {"vidro"})
    assert catalogo.titulo_do_acessorio("Carregador Rápido 50W Compatível", {"carregador"})
    assert not catalogo.titulo_do_acessorio(
        "AirFryer Fritadeira Digital 4 Litros Tigela de Vidro Tela Touch", {"vidro", "cesto"}
    )
    assert not catalogo.titulo_do_acessorio("Case Uranyx A17 Pro Max 12.128 GB Celular", {"capa"})
    assert not catalogo.titulo_do_acessorio("Cappa ** Uranyx Hotwav A17 Pro Max 64 Gb", {"capa"})
    assert not catalogo.titulo_do_acessorio(
        "Uranyx Relógio Smartwatch USW10 + 7 Pulseiras", {"pulseira"}
    )


def test_6_nome_curto_sem_especificacao_garantia_nem_condicao():
    assert catalogo.nome_curto("Uranyx A18 Pro Max NFC 16.128 Gb Memoria S5 Premium") == (
        "Uranyx A18 Pro Max"
    )
    assert catalogo.nome_curto("Doogee Fire 6 Power 20.256 GB") == "Doogee Fire 6 Power"
    assert catalogo.nome_curto("Apple Iphone 15 128 GB - Original - 1 Ano de Garantia") == (
        "Apple Iphone 15"
    )
    assert catalogo.nome_curto("AirFryer Fritadeira Digital 4 Litros Tigela de Vidro") == (
        "AirFryer Fritadeira Digital"
    )
    assert catalogo.nome_curto("Uranyx Cesto De Vidro Para Air Fryers") == (
        "Uranyx Cesto De Vidro Para Air Fryers"
    )


def test_6_disponivel_so_nas_variacoes_casadas():
    ok = catalogo.Vinculo("1", "a", "T - A", 5, True)
    zero = catalogo.Vinculo("1", "b", "T - B", 0, True)
    sem_sync = catalogo.Vinculo("1", "c", "T - C", 9, False)
    nulo = catalogo.Vinculo("1", "d", "T - D", None, True)
    assert catalogo.disponivel([ok, zero]) is True
    assert catalogo.disponivel([zero, catalogo.Vinculo("1", "e", "T - E", 0, False)]) is False
    assert catalogo.disponivel([sem_sync]) is None and catalogo.disponivel([nulo, zero]) is None
    assert catalogo.disponivel([]) is None


def test_6_a_busca_nos_vinculos_reais():
    # O vidro de 4 L da air fryer (Barbosa, 22/10 14:28): só o anúncio do CESTO, com a
    # variação casada e esgotado; a air fryer "com tigela de vidro" não é o acessório.
    for termo in ("recipiente de vidro", "vidro 4 litros", "cesto de vidro 4L"):
        achados = _busca(termo)
        assert [a.item_id for a in achados] == [ITEM_CESTO], termo
    (cesto,) = _busca("vidro 4 litros")
    assert cesto.nome_curto == "Uranyx Cesto De Vidro Para Air Fryers"
    assert cesto.variacoes_casadas == ("Cesto 4 Litros",) and cesto.disponivel is False
    # A capa do A18 (Kia): a loja não vende capa — e o "Case … Celular" não é capa.
    assert _busca("capa A18") == [] and _busca("capa A17") == [] and _busca("capinha") == []
    # "tem o A18?" do anúncio do A17: o anúncio do A18, disponível.
    (a18,) = _busca("A18")
    assert (a18.item_id, a18.nome_curto, a18.disponivel) == (ITEM_A18, "Uranyx A18 Pro Max", True)
    # O A17 com os títulos dos vínculos divergentes: o nome do título mais comum.
    assert _busca("A17 Pro Max")[0].nome_curto.startswith("Uranyx")
    # O Doogee Fire 7 Ultra: a loja só tem o Fire 6 Power — nada casa (todo o termo).
    assert _busca("Doogee Fire 7 Ultra") == []
    assert [a.item_id for a in _busca("Doogee Fire 6")] == [ITEM_FIRE6]
    assert {a.item_id for a in _busca("Doogee")} == {ITEM_FIRE6, ITEM_SPUNK}
    (carregador,) = _busca("carregador iphone")
    assert carregador.item_id == ITEM_CARREGADOR and carregador.disponivel is False
    # O que vai ao modelo: nunca preço, quantidade, SKU, link nem o título inteiro.
    assert set(a18.para_o_modelo()) == {
        "item_id",
        "nome_curto",
        "variacoes_casadas",
        "disponivel",
        "da_conversa",
    }


async def _loja_com_catalogo(db: AsyncSession, make_user) -> tuple[Any, Integration]:
    """A conversa no anúncio do A17 e o catálogo da loja (e o de OUTRA loja, o do Bling e o
    anúncio morto, que a busca nunca vê)."""
    conversa = await _conversa(db, make_user, pedido=None, comprador_nome="Maria")
    conversa.anuncio_id = ITEM_A17
    conversa.anuncio_titulo = None
    integ = await db.get(Integration, conversa.integration_id)
    outra = await _integ(db, await make_user(), nome="outra")

    async def _link(
        integracao: Integration,
        item: str,
        variacao: str,
        titulo: str,
        estoque: int,
        *,
        plataforma: IntegrationPlatform = IntegrationPlatform.SHOPEE,
        morto: bool = False,
    ) -> None:
        produto = Product(
            user_id=integracao.user_id, sku=f"sku-{item}-{variacao}", name=titulo, stock=1
        )
        db.add(produto)
        await db.flush()
        db.add(
            ProductLink(
                user_id=integracao.user_id,
                product_id=produto.id,
                integration_id=integracao.id,
                platform=plataforma,
                external_id=item,
                variation_id=variacao,
                external_sku=f"sku-{item}-{variacao}",
                listing_title=titulo,
                stock=estoque,
                price=999,
                last_sync_status=LinkSyncStatus.OK,
                morto_desde=datetime.now(UTC) - timedelta(days=1) if morto else None,
            )
        )

    for item, variacao, titulo, estoque, _ok in (
        (ITEM_A17, "1", "Uranyx A17 Pro Max 12.128 ou 12.64 GB Memoria - Laranja", 300, True),
        (ITEM_A18, "1", "Uranyx A18 Pro Max NFC 16.128 Gb Memoria S5 Premium - A18 - Branco NFC", 175, True),
        (ITEM_A18, "2", "Uranyx A18 Pro Max NFC 16.128 Gb Memoria S5 Premium - A18 - Laranja NFC", 0, True),
        (ITEM_CESTO, "1", "Uranyx Cesto De Vidro Para Air Fryers - Cesto 4 Litros", 0, True),
    ):  # fmt: skip
        await _link(integ, item, variacao, titulo, estoque)
    await _link(integ, "77700000001", "1", "Uranyx A18 Pro Max Antigo - Preto", 9, morto=True)
    await _link(
        integ, "77700000002", "1", "Uranyx A18 Pro Max Bling - Preto", 9,
        plataforma=IntegrationPlatform.BLING,
    )  # fmt: skip
    await _link(outra, "77700000003", "1", "Uranyx A18 Pro Max Outra Loja - Preto", 9)
    await db.commit()
    return conversa, integ


async def test_6_a_ferramenta_so_le_a_loja_da_conversa(db: AsyncSession, make_user):
    conversa, _integ_da_conversa = await _loja_com_catalogo(db, make_user)
    base = await _base_db(db, conversa)
    ctx = _ctx(conversa, base)
    dados = await _chamar(ctx, base, conversa, "buscar_anuncios_da_loja", termo="A18")
    # Só o anúncio vivo da MESMA integração e plataforma (nem o morto, nem o do Bling, nem
    # o de outra loja), sem preço, quantidade, SKU nem link.
    assert [a["item_id"] for a in dados["anuncios"]] == [ITEM_A18]
    (a18,) = dados["anuncios"]
    assert a18 == {
        "item_id": ITEM_A18,
        "nome_curto": "Uranyx A18 Pro Max",
        "variacoes_casadas": [],
        "disponivel": True,
        "da_conversa": False,
    }
    bruto = json.dumps(dados, ensure_ascii=False)
    for proibido in ("999", "sku-", "http", "175", "Antigo", "Outra Loja", "Bling"):
        assert proibido not in bruto, proibido
    assert dados["evidencias"] == [f"anuncio:{ITEM_A18}"] and dados["como_achar"]
    # Prova o id; o JSON só libera número (nunca vira fonte de característica).
    assert ctx.registro.permite(f"anuncio:{ITEM_A18}")
    assert not any("A18 Pro Max" in t for t in ctx.registro.textos)
    assert any("A18 Pro Max" in t for t in ctx.numeros_vistos)
    assert ctx.anuncios_buscados[ITEM_A18]["disponivel"] is True
    # Revisão do fechamento: cada busca guarda os SEUS achados e o "sem_no_titulo".
    (busca_a18,) = ctx.buscas_catalogo
    assert {k: busca_a18[k] for k in ("termo", "acessorios", "modelo", "achados")} == {
        "termo": "A18", "acessorios": [], "modelo": True, "achados": [ITEM_A18]
    }  # fmt: skip
    assert [a["disponivel"] for a in busca_a18["anuncios"]] == [True]
    assert busca_a18["sem_no_titulo"] == []
    assert ctx.para_fatos()["buscas_catalogo"] == [{"termo": "A18", "achados": [ITEM_A18]}]
    # O anúncio da conversa vem marcado; o acessório sem anúncio: a lista vazia e o aviso.
    proprio = await _chamar(ctx, base, conversa, "buscar_anuncios_da_loja", termo="A17 Pro Max")
    assert proprio["anuncios"][0]["da_conversa"] is True
    capa = await _chamar(ctx, base, conversa, "buscar_anuncios_da_loja", termo="capa A18")
    # A loja não tem "capa" em título nenhum: a busca vazia prova (revisão do fechamento).
    assert capa["anuncios"] == [] and "nenhum anúncio desta loja tem 'capa'" in capa["nenhum"]
    assert ctx.buscas_catalogo[-1]["sem_no_titulo"] == ["capa"]
    # O termo inteiro que não casa, com o acessório num título: não prova nada.
    cesto = await _chamar(
        ctx, base, conversa, "buscar_anuncios_da_loja", termo="cesto de vidro 8 litros"
    )
    assert cesto["anuncios"] == [] and "NÃO prova" in cesto["nenhum"]
    assert ctx.buscas_catalogo[-1]["sem_no_titulo"] == []
    with pytest.raises(ToolError) as exc:
        await ferramentas.ferramentas(ctx, base, conversa)[-1].call({"termo": "de da"})
    assert str(exc.value.content) == ferramentas.ERRO_FORA_DO_ESCOPO


def test_6_a_descricao_da_ferramenta_traz_as_regras_g1():
    d = ferramentas.DESCRICOES[ferramentas.BUSCAR_ANUNCIOS_DA_LOJA]
    for trecho in (
        "cite anuncio:<item_id> com disponivel=true",
        "nunca link nem preço",
        "'Não temos' só com o fato aprovado (capinha, película) ou quando a busca diz que "
        "nenhum título da loja tem o acessório",
        "a busca vazia do termo inteiro não prova",
        "modelo ou versão que a busca não achou: pergunte qual é ou encaminhe",
        "o de outro anúncio nunca vale para o produto da conversa",
    ):
        assert trecho in d, trecho
    assert ferramentas.NOMES[-1] == ferramentas.BUSCAR_ANUNCIOS_DA_LOJA


# ─────────────── G1 no caminho AUTO (a configuração real dos canais Shopee) ───────────────

FICHA_AIRFRYER = {
    "de_onde": "anúncio ligado à conversa",
    "item_id": ITEM_AIRFRYER,
    "titulo": "AirFryer Fritadeira Digital 4 Litros Tigela de Vidro Tela Touch 110-220v",
    "variacoes": [
        {"nome": "110v", "disponivel": False, "confiavel": True, "fonte": "loja"},
        {"nome": "220v", "disponivel": False, "confiavel": True, "fonte": "loja"},
    ],
    "variacoes_completas": True,
}
FICHA_A17 = {
    "de_onde": "anúncio ligado à conversa",
    "item_id": ITEM_A17,
    "titulo": "Uranyx A17 Pro Max 12.128 ou 12.64 GB Memoria",
    "variacoes": [
        {"nome": "Laranja,12 GB / 128 GB", "disponivel": True, "confiavel": True, "fonte": "loja"},
        {"nome": "Prata,12 GB / 64 GB", "disponivel": True, "confiavel": True, "fonte": "loja"},
    ],
    "variacoes_completas": True,
}
FICHA_A18 = {
    "de_onde": "anúncio ligado à conversa",
    "item_id": ITEM_A18,
    "titulo": "Uranyx A18 Pro Max NFC 16.128 Gb Memoria S5 Premium",
    "variacoes": [
        {"nome": "A18 - Branco NFC", "disponivel": True, "confiavel": True, "fonte": "loja"},
        {"nome": "A18 - Laranja NFC", "disponivel": True, "confiavel": True, "fonte": "loja"},
    ],
    "variacoes_completas": True,
}
FATO_26 = "No momento não trabalhamos com capinha nem película avulsas nesta loja."
VIDRO = (
    "Então mais no anúncio está o valor da airfryer completa, eu queria saber o valor somente "
    "recipiente de vidro, entendeu?"
)
CAPA = (
    "Boa tarde tudo bem ? Vcs tem a capa pro Uranyx A 18 Pró Max na Cor Laranja? De preferência "
    "com símbolo da Maçã ,comprei com vcs o celular e esqueci de comprar a capa que eu queria"
)
TEXTO_VIDRO = (
    "Entendi! O recipiente de vidro é vendido separadamente na nossa loja, no anúncio Uranyx "
    "Cesto De Vidro Para Air Fryers, e o valor aparece nele; no momento, o de 4 litros está "
    "esgotado."
)
TEXTO_A18 = (
    "Temos sim! O Uranyx A18 Pro Max está à venda em outro anúncio da nossa loja; ele aparece "
    "na página da loja no app, buscando pelo nome."
)


def _achado(item: str, nome: str, disponivel: bool | None, variacoes=(), titulo=None) -> dict:
    return {
        "item_id": item,
        "nome_curto": nome,
        "variacoes_casadas": list(variacoes),
        "disponivel": disponivel,
        "da_conversa": False,
        "titulo_plano": catalogo.plano(" ".join([titulo or nome, *variacoes])),
    }


def _b(termo: str, *achados: dict) -> dict:
    lido = catalogo.ler_termo(termo)
    return {
        "termo": termo,
        "acessorios": sorted(lido.acessorios),
        "modelo": lido.modelo,
        "anuncios": list(achados),
    }


CESTO_4L = _achado(ITEM_CESTO, "Uranyx Cesto De Vidro Para Air Fryers", False, ["Cesto 4 Litros"])
A18_DISPONIVEL = _achado(
    ITEM_A18, "Uranyx A18 Pro Max", True, titulo="Uranyx A18 Pro Max NFC 16.128 Gb Memoria S5"
)
A17_ACHADO = _achado(ITEM_A17, "Uranyx A17 Pro Max", True)
G1 = {checagens.OUTRO_PRODUTO_SEM_BUSCA, checagens.NAO_TEMOS_SEM_FONTE, checagens.NAO_TEMOS_MODELO}


def _g1(r) -> set[str]:
    return set(r.codigos) & G1


async def test_6_casos_reais_com_a_busca_vao_ao_verificador():
    # 22/10 14:28–16:27 (Barbosa): antes, três vezes para pessoa com dado_indisponivel.
    r = await _auto(
        _proposta(TEXTO_VIDRO, "acessorios_avulsos", [f"anuncio:{ITEM_CESTO}"], 0.9),
        [_menu(), *_cli(VIDRO)],
        fichas=[FICHA_AIRFRYER],
        buscas=[_b("recipiente de vidro 4 litros", CESTO_4L)],
    )
    assert r.decisao == "verificador", (r.codigos, r.motivos)
    # 21/10 14:42 (Kia, fac02bf4): a capa do A18 pelo fato aprovado 26.
    r = await _auto(
        _proposta("Olá! " + FATO_26, "acessorios_avulsos", ["fato:26"], 0.9),
        [_menu(), *_cli(CAPA)],
        fichas=[FICHA_A18],
        fatos_lidos={"fato:26": FATO_26},
    )
    assert r.decisao == "verificador", (r.codigos, r.motivos)
    # "tem o A18?" do anúncio do A17: o anúncio do A18 achado, disponível.
    r = await _auto(
        _proposta(TEXTO_A18, "estoque_variacao", [f"anuncio:{ITEM_A18}"], 0.9),
        [_menu(), *_cli("Vocês têm o A18?")],
        fichas=[FICHA_A17],
        buscas=[_b("A18", A18_DISPONIVEL)],
    )
    assert r.decisao == "verificador", (r.codigos, r.motivos)


@pytest.mark.parametrize(
    ("caso", "texto", "evidencias", "buscas", "fatos", "esperado"),
    [
        # O A18 sem citar o anúncio achado, citando o anúncio ERRADO (o A17) e com ele
        # esgotado dito disponível.
        ("a18-sem-citar", TEXTO_A18, [], [_b("A18", A18_DISPONIVEL)], {}, checagens.OUTRO_PRODUTO_SEM_BUSCA),
        ("a18-sem-busca", TEXTO_A18, [], [], {}, checagens.OUTRO_PRODUTO_SEM_BUSCA),
        ("a18-cita-o-a17", TEXTO_A18, [f"anuncio:{ITEM_A17}"], [_b("A17", A17_ACHADO)], {}, checagens.OUTRO_PRODUTO_SEM_BUSCA),
        (
            "a18-esgotado-dito-disponivel",
            "O Uranyx A18 Pro Max está disponível em outro anúncio da nossa loja.",
            [f"anuncio:{ITEM_A18}"],
            [_b("A18", {**A18_DISPONIVEL, "disponivel": False})],
            {},
            checagens.OUTRO_PRODUTO_SEM_BUSCA,
        ),
        # O cesto esgotado dito disponível (mesmo com "esgotado" em outra frase).
        (
            "cesto-disponivel",
            "O recipiente de vidro de 4 litros está disponível na nossa loja. O de 6 litros está esgotado.",
            [f"anuncio:{ITEM_CESTO}"],
            [_b("vidro 4 litros", CESTO_4L)],
            {},
            checagens.OUTRO_PRODUTO_SEM_BUSCA,
        ),
        # "Não temos" de MODELO: nunca (nem com a busca vazia).
        ("doogee", "Não temos o Doogee Fire 7 Ultra.", [], [_b("Doogee Fire 7 Ultra")], {}, checagens.NAO_TEMOS_MODELO),
        # "Não vendemos" o acessório sem o fato nem a busca vazia.
        ("vidro-sem-fonte", "Não vendemos o recipiente de vidro separadamente.", [], [], {}, checagens.NAO_TEMOS_SEM_FONTE),
        ("capinha-sem-fato", "No momento não trabalhamos com capinha.", [], [], {}, checagens.NAO_TEMOS_SEM_FONTE),
        # O rascunho real 0ca5b37b (Kia, 22/10 04:42): a evasiva do "anúncio próprio".
        (
            "0ca5b37b",
            "Olá! Este anúncio é do aparelho e não inclui capa. Acessórios avulsos só aparecem à "
            "venda quando a loja tem anúncio próprio deles.",
            [],
            [],
            {},
            checagens.OUTRO_PRODUTO_SEM_BUSCA,
        ),
    ],
)  # fmt: skip
async def test_6_g1_segura(caso, texto, evidencias, buscas, fatos, esperado):
    r = await _auto(
        _proposta(texto, "acessorios_avulsos", evidencias, 0.9),
        [_menu(), *_cli("Vocês têm?")],
        fichas=[FICHA_A17],
        buscas=buscas,
        fatos_lidos=fatos,
    )
    assert esperado in r.codigos, (caso, r.codigos)
    assert r.decisao != "verificador", caso


@pytest.mark.parametrize(
    ("caso", "texto", "evidencias", "buscas", "fatos"),
    [
        ("capinha-com-fato", "No momento não trabalhamos com capinha nem película avulsas nesta loja.", ["fato:26"], [], {"fato:26": FATO_26}),
        # Revisão do fechamento: a busca vazia só prova com o acessório fora de todo título.
        ("capinha-sem-no-titulo", "No momento não vendemos capinha para este aparelho.", [], [{**_b("capa A17"), "sem_no_titulo": ["capa"]}], {}),
        ("a-cor-do-anuncio", "Temos sim a cor laranja disponível neste anúncio.", ["variacoes:" + ITEM_A17], [], {}),
        ("a-memoria-do-anuncio", "Temos o A17 Pro Max de 128 GB neste anúncio.", [], [], {}),
        ("loja-fisica", "Pode vir buscar na nossa loja física.", [], [], {}),
        ("sem-o-dado", "Não temos a informação do prazo de reposição.", [], [], {}),
        ("a18-citado", TEXTO_A18, [f"anuncio:{ITEM_A18}"], [_b("A18", A18_DISPONIVEL)], {}),
    ],
)  # fmt: skip
async def test_6_g1_nao_segura(caso, texto, evidencias, buscas, fatos):
    r = await _auto(
        _proposta(texto, "acessorios_avulsos", evidencias, 0.9),
        [_menu(), *_cli("Vocês têm?")],
        fichas=[FICHA_A17],
        buscas=buscas,
        fatos_lidos=fatos,
        ids=[f"variacoes:{ITEM_A17}"],
    )
    assert not _g1(r), (caso, r.codigos)


async def test_6_g1_a_variacao_do_acessorio_da_conversa_nao_e_outro_produto():
    ficha_cesto = {
        "de_onde": "anúncio ligado à conversa",
        "item_id": ITEM_CESTO,
        "titulo": "Uranyx Cesto De Vidro Para Air Fryers",
        "variacoes": [
            {"nome": f"Cesto {x} Litros", "disponivel": False, "confiavel": True, "fonte": "loja"}
            for x in ("2,5", "4", "6")
        ],
        "variacoes_completas": True,
    }
    r = await _auto(
        _proposta("Não temos o cesto de 8 litros.", "estoque_variacao", [], 0.9),
        [_menu(), *_cli("Tem o cesto de 8 litros?")],
        fichas=[ficha_cesto],
    )
    assert not _g1(r), r.codigos
    # Mas a venda avulsa ("não vendemos o cesto separadamente") continua pedindo a fonte.
    r = await _auto(
        _proposta("Não vendemos o cesto separadamente.", "acessorios_avulsos", [], 0.9),
        [_menu(), *_cli("Vende o cesto separado?")],
        fichas=[ficha_cesto],
    )
    assert checagens.NAO_TEMOS_SEM_FONTE in r.codigos, r.codigos


def test_6_g1_so_no_responder_e_no_perguntar():
    ctx = agente_ctx.ContextoAgente.da_conversa(
        type("C", (), {"id": 1, "plataforma": "shopee", "canal": "chat"})(),
        None,
        modo=constantes.MODO_AUTO,
    )
    for acao, esperado in (("pessoa", []), ("responder", [checagens.NAO_TEMOS_MODELO])):
        r = checagens.conferir_outro_produto_da_loja(
            "Não temos o Doogee Fire 7 Ultra.", acao=acao, evidencias=[], ctx=ctx
        )
        assert [p.codigo for p in r] == esperado
        assert all(p.suave for p in r)


# ─────────────── o ver_anuncio de OUTRO anúncio não empresta característica ───────────────

FICHA_C2 = {
    "de_onde": "anúncio ligado à conversa",
    "item_id": "58203678406",
    "titulo": "Uranyx C2 128 Gb Memoria 16 Gb",
}
RUGGED = {
    "item_id": "58269759596",
    "titulo": "Smartphones Rugged Uranyx à Prova d'Água IP68 Resistente a Queda",
    "variacoes": [],
    "variacoes_completas": False,
    "condicao": None,
    "da_conversa": False,
}


async def test_6_o_outro_anuncio_lido_nao_empresta_caracteristica():
    texto = "Sim, o Uranyx C2 é à prova d'água."
    # Com o produto da conversa (o C2), o título do Rugged lido pelo ver_anuncio não é fonte.
    r = await _auto(
        _proposta(texto, "resistencia_agua", ["ficha:58203678406"], 0.9),
        [_menu(), *_cli("O C2 é à prova d'água?")],
        fichas=[FICHA_C2],
        fichas_lidas=[RUGGED],
    )
    assert checagens.PRODUTO_SEM_FONTE in r.codigos, r.codigos
    # Sem produto na conversa, o anúncio lido é o assunto: vale como antes.
    ctx = agente_ctx.ContextoAgente.da_conversa(
        type("C", (), {"id": 1, "plataforma": "shopee", "canal": "chat"})(),
        None,
        modo=constantes.MODO_AUTO,
    )
    ctx.fichas_lidas.append(dict(RUGGED))
    assert checagens.anuncios_de_fora([RUGGED], ctx) == frozenset()
    assert checagens.anuncios_de_fora([FICHA_C2, RUGGED], ctx) == {RUGGED["item_id"]}
    assert checagens.anuncios_de_fora([RUGGED], ctx, ["Uranyx C2 - item do pedido"]) == {
        RUGGED["item_id"]
    }
    sem_produto = checagens.conferir_fonte_produto(
        "O Rugged é à prova d'água.", [RUGGED], ctx, sem_os_anuncios=frozenset()
    )
    assert not [p for p in sem_produto if p.codigo == checagens.PRODUTO_SEM_FONTE]


# ─────────────── o verificador v10 ───────────────


def test_6_o_verificador_v10_le_a_busca_so_quando_houve():
    assert v.VERSAO in ("v10", "v11", "v12", "v13", "v14") and v.MODELO == HAIKU
    s = " ".join(v.SISTEMA.split())
    for trecho in (
        "BUSCA NO CATÁLOGO DA LOJA (quando vier)",
        'O anúncio desta loja que a BUSCA NO CATÁLOGO DA LOJA traz não é "outro anúncio nosso"',
        "dizer que a loja tem ou vende o produto de uma linha com disponivel=true",
        # Revisão do fechamento (23/10/2026): só a linha "nenhum anúncio desta loja tem
        # <acessório> no título" sustenta o "não vende"; a busca vazia do termo, nunca.
        'uma linha "nenhum anúncio desta loja tem <acessório> no título" — "nenhum anúncio '
        'casou com o termo" nunca sustenta "não temos", e de um modelo ou versão nada sustenta',
    ):
        assert trecho in s, trecho
    busca = (
        {
            "termo": "A18",
            "anuncios": [{k: x for k, x in A18_DISPONIVEL.items() if k != "titulo_plano"}],
        },
        {"termo": "capa A18", "anuncios": [], "sem_no_titulo": ["capa"]},
        {"termo": "fone de ouvido sem fio", "anuncios": []},
    )
    linhas = v.linhas_da_busca(busca, ())
    assert linhas[0] == v.CABECALHO_BUSCA
    assert linhas[1] == f'- termo "A18": anuncio:{ITEM_A18} «Uranyx A18 Pro Max»; disponivel=true'
    assert linhas[2] == (
        '- termo "capa A18": nenhum anúncio desta loja tem "capa" no título — a loja NÃO vende '
        "esse acessório: dizer que não vende ou não trabalha com ele tem lastro"
    )
    assert linhas[3] == (
        '- termo "fone de ouvido sem fio": nenhum anúncio casou com o termo (isso não prova '
        "que a loja não vende)"
    )
    assert v.linhas_da_busca((), ()) == []
    # O contexto: as buscas com os achados, sem o título plano (que é só da G1).
    ctx = agente_ctx.ContextoAgente.da_conversa(
        type("C", (), {"id": 1, "plataforma": "shopee", "canal": "chat"})(),
        None,
        modo=constantes.MODO_AUTO,
    )
    ctx.registrar_busca(_b("A18", A18_DISPONIVEL))
    (b,) = v.busca_do_contexto(ctx)
    assert b["termo"] == "A18" and "titulo_plano" not in b["anuncios"][0]


def test_6_a_calibracao_tem_o_grupo_catalogo_e_as_linhas_de_antes_nao_mudam_de_entrada():
    casos = {c["id"]: c for c in calibrar.ler_casos(calibrar.CASOS_PADRAO)}
    catalogo_ = {i: c for i, c in casos.items() if c.get("grupo") == "catalogo"}
    bons = {i for i, c in catalogo_.items() if c["esperado"]["ok_para_envio_automatico"]}
    # Revisão do fechamento (23/10/2026): o bom do acessório fora de todo título e os ruins
    # da busca vazia do termo inteiro e do C17 esgotado dito disponível.
    assert bons == {
        "boa-catalogo-a18",
        "boa-catalogo-cesto",
        "boa-catalogo-capa-fato",
        "boa-catalogo-pelicula-sem-no-titulo",
    }
    assert set(catalogo_) - bons == {
        "viz-catalogo-a18-sem-busca",
        "viz-catalogo-a18-esgotado",
        "viz-catalogo-cesto-disponivel",
        "viz-catalogo-doogee-nao-temos",
        "viz-catalogo-capa-sem-fonte",
        "viz-catalogo-fone-busca-vazia",
        "viz-catalogo-c17-esgotado-disponivel",
    }
    for i, c in casos.items():
        entrada = calibrar.entrada_do_caso(c)
        assert ("BUSCA NO CATÁLOGO DA LOJA" in entrada) is bool(c.get("busca")), i
    # A linha do bom e a do vizinho só diferem na busca.
    assert casos["boa-catalogo-a18"]["texto"] == casos["viz-catalogo-a18-sem-busca"]["texto"]
    with pytest.raises(calibrar.CasoInvalido):
        calibrar.conferir_caso({**casos["boa-catalogo-a18"], "busca": [{"anuncios": []}]})


# ═══════════════ 7. o COMO PENSAR do bloco 2 ═══════════════


def test_7_o_como_pensar_vai_no_bloco_2_de_todas_as_plataformas():
    for plataforma in ("shopee", "ml", "tiktok", "amazon"):
        texto = prompt.bloco_da_plataforma(plataforma, _categorias_do_manual())
        cabeca, _assuntos = texto.split("# ASSUNTOS")
        assert prompt.COMO_PENSAR in cabeca
        assert "# SÓ O QUE FOI PERGUNTADO" not in cabeca  # está dentro do COMO PENSAR
        assert len(texto) / 3.5 <= 5_000, plataforma  # D1–D3 do dono (23/10/2026)
    c = prompt.COMO_PENSAR
    for trecho in (
        "o raciocínio é seu e nunca vai no texto",
        "buscar_anuncios_da_loja",
        "buscar_respostas_equipe",
        "Responda só o que o cliente perguntou neste turno, em 1 a 3 frases, sem inventar",
        "nunca emende orientação de outro assunto que ele não pediu nem a conversa pede",
        "Falta de dado não é motivo de pessoa: diga o que os dados mostram e onde o cliente vê o "
        "resto no app",
        "Pessoa só com um código da régua (como arquivo da NF, evidência de defeito, aprovar "
        "devolução, Pix ou dados bancários, desconto, chamado, ameaça jurídica) ou com decisão "
        "da loja",
        "Saudação sem pergunta: pergunte como pode ajudar, sem dizer que a equipe responde.",
    ):
        assert trecho in c, trecho
    # Nada que peça o raciocínio por escrito (o Sonnet 5.5 recusa: "reasoning_extraction").
    plano = " ".join(c.lower().split())
    for proibido in (
        "escreva o raciocínio",
        "explique o raciocínio",
        "liste no texto",
        "passo a passo no texto",
    ):
        assert proibido not in plano


# ═══════════════ de ponta a ponta: o `ia._gerar` com o banco e a API falsa ═══════════════


async def _a18_do_a17(db: AsyncSession, make_user) -> Any:
    """A conversa no anúncio do A17 ("Vocês têm o A18?"), o catálogo da loja e o canal com a
    configuração real dos canais Shopee."""
    from tests.test_atendimento_agente_redondo import _canal_real
    from tests.test_atendimento_ia import _importar_manual_base, _msg

    await _importar_manual_base(db)
    conversa, _integ_da_conversa = await _loja_com_catalogo(db, make_user)
    await _canal_real(db, conversa)
    await _msg(db, conversa, "Vocês têm o A18?", ha=timedelta(minutes=6))
    return conversa


async def test_e2e_6_a_busca_no_catalogo_de_ponta_a_ponta(
    db, make_user, agente_ligado, api, envios
):
    conversa = await _a18_do_a17(db, make_user)
    api.roteiro = [
        _usa("buscar_anuncios_da_loja", termo="A18"),
        _final_e2e(
            texto=TEXTO_A18,
            assunto="estoque_variacao",
            confianca=0.9,
            evidencias=[f"anuncio:{ITEM_A18}"],
        ),
    ]

    r = await ia.gerar_rascunho(db, conversa)

    # A ferramenta leu o catálogo do banco: o A18 desta loja, sem preço, SKU nem estoque.
    resultado = api.resultado(1)
    assert f'"item_id":"{ITEM_A18}"' in resultado and "Uranyx A18 Pro Max" in resultado
    for proibido in ("999", "sku-", "175", "Outra Loja", "Antigo"):
        assert proibido not in resultado, proibido
    agente_ = _do_agente(r)
    assert agente_["buscas_catalogo"] == [{"termo": "A18", "achados": [ITEM_A18]}]
    assert agente_["sem_auto"] == [], agente_["sem_auto"]
    # O verificador recebeu a BUSCA NO CATÁLOGO DA LOJA (v10) e liberou.
    entrada = api.verificador[0]["messages"][0]["content"]
    assert v.CABECALHO_BUSCA in entrada
    assert f'- termo "A18": anuncio:{ITEM_A18} «Uranyx A18 Pro Max»; disponivel=true' in entrada
    assert [e["texto"] for e in envios] == [r.texto] and _decisao(r) == "enviado", r.motivo


async def test_e2e_6_sem_citar_o_anuncio_achado_nao_sai(db, make_user, agente_ligado, api, envios):
    conversa = await _a18_do_a17(db, make_user)
    api.roteiro = [
        _usa("buscar_anuncios_da_loja", termo="A18"),
        _final_e2e(texto=TEXTO_A18, assunto="estoque_variacao", confianca=0.9, evidencias=[]),
    ]
    # O portão do dono (27/10/2026): a G1 é AVISO — o verificador decide (aqui, "não").
    api.vereditos = [_do_verificador(**NAO_DO_VERIFICADOR)]

    r = await ia.gerar_rascunho(db, conversa)

    assert any("busca no catálogo" in m for m in _do_agente(r)["avisos"]), r.motivo
    assert envios == [] and _decisao(r) != "enviado"
    _aviso_no_verificador(api, "busca no catálogo")


# ═══════════════ 8. o que o eval S1 mede (o relatório) ═══════════════


def test_8_o_relatorio_conta_o_dado_indisponivel_o_assunto_nao_pedido_e_as_ferramentas():
    from scripts.agente_eval import relatorio

    validos = {
        "a": {
            "proposta": {"acao_pessoa": "dado_indisponivel"},
            "decisao": {"pessoa": True, "acoes_modelo": ["dado_indisponivel"]},
            "rastro": {"checagens": [], "passos": [{"ferramenta": "ver_anuncio"}]},
            "ms": 10_000,
            "turnos": 2,
        },
        "b": {
            "proposta": {"acao_pessoa": None},
            "decisao": {"pessoa": False, "apto_auto": True, "enviaria": True},
            "rastro": {
                "checagens": [checagens.ASSUNTO_NAO_PEDIDO, checagens.OUTRO_PRODUTO_SEM_BUSCA],
                "passos": [
                    {"ferramenta": "buscar_anuncios_da_loja"},
                    {"ferramenta": "buscar_anuncios_da_loja"},
                ],
            },
            "ms": 30_000,
            "turnos": 3,
        },
        "legado": {"parada": relatorio.PARADA_LEGADO, "decisao": {"pessoa": True}},
        "pulado": {"pulado": "atalho"},
    }
    m = relatorio.metricas_da_rodada(validos)
    assert m["n"] == 2
    assert m["dado_indisponivel_do_modelo"] == (1, 2)
    assert m["assunto_nao_pedido"] == (1, 2) and m["g1"] == (1, 2)
    assert m["pessoa"] == (1, 2) and m["apto_auto"] == (1, 2) and m["enviaria"] == (1, 2)
    assert m["ferramentas"]["buscar_anuncios_da_loja"] == (1, 2)
    assert m["ferramentas"]["ver_anuncio"] == (1, 2)
    assert m["ferramentas"]["buscar_respostas_equipe"] == (0, 2)
    assert m["turnos_media"] == 2.5
    assert (m["latencia_p50_ms"], m["latencia_p90_ms"]) == (10_000, 30_000)
    # As constantes do relatório são as das checagens (o relatório não importa o app).
    assert relatorio.CHECAGEM_ASSUNTO_NAO_PEDIDO == checagens.ASSUNTO_NAO_PEDIDO
    assert relatorio.CHECAGENS_G1 == {
        checagens.OUTRO_PRODUTO_SEM_BUSCA,
        checagens.NAO_TEMOS_SEM_FONTE,
        checagens.NAO_TEMOS_MODELO,
    }
    assert relatorio.ACAO_DADO_INDISPONIVEL == constantes.ACAO_DADO_INDISPONIVEL
    assert set(relatorio.FERRAMENTAS_DA_RODADA) <= set(ferramentas.NOMES)
