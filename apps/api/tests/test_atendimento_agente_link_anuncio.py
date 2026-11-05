# ruff: noqa: F811 — fixtures importadas dos testes de ponta a ponta
"""As decisões do dono de 04/11/2026 no agente (a parte do verificador e do link).

- D3: "nao da pra ele mesmo abrir e ter os da loja o agente" — na Shopee, a lacuna do link
  por anúncio vivo da loja; o código põe a URL e o validador só aceita ela, exata. No ML,
  nada muda;
- 87747398: a opção pedida que a lista COMPLETA não tem não tem previsão de reposição;
- c7d6db8b: a confirmação curta do que o atendimento já disse, com o texto aprovado no Manual;
- a revisão da rodada de 04/11: o "temos outro modelo" e o "temos o <nome>" só com o anúncio
  citado, disponivel=true.

O "6" que cruzou com a resposta (D4) está em `test_atendimento_agente_decisoes_dono.py`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Integration, IntegrationPlatform, Product, ProductLink
from app.models.atendimento import AtendimentoRascunho
from app.models.enums import LinkSyncStatus
from app.security.cipher import encrypt_json
from app.services.atendimento import anuncio_publico, constantes, enviar, ia, validador
from app.services.atendimento import ficha as ficha_svc
from app.services.atendimento import variacoes as variacoes_svc
from app.services.atendimento.agente import catalogo, checagens, contexto, ferramentas
from app.services.atendimento.agente import verificador as v
from tests.test_atendimento_agente_e2e import (  # noqa: F401 — fixtures
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
from tests.test_atendimento_agente_redondo import _auto, _canal_real, _cli, _menu, _proposta
from tests.test_atendimento_agente_redondo2 import ITEM_A17, ITEM_A18, _loja_com_catalogo
from tests.test_atendimento_enviar import _cenario, ligado, plataforma  # noqa: F401 — fixtures
from tests.test_atendimento_ficha_loja import RedisMem
from tests.test_atendimento_ia import _conversa, _importar_manual_base, _msg

SHOP_ID = 987654321
URL = f"https://shopee.com.br/product/{SHOP_ID}/{ITEM_A18}"


def _valida(texto: str, *, links=(), plat="shopee", canal="chat", origem="davinci_ia") -> list[str]:
    return validador.validar(
        texto, plataforma=plat, canal=canal, origem=origem, links_permitidos=links
    )


# ═══════════════ D3: o validador aceita só a URL exata ═══════════════


def test_d3_o_validador_aceita_so_a_url_que_o_codigo_pos():
    # Ninguém disse que o link é este: bloqueia.
    assert validador._MOTIVO_LINK in _valida(f"Segue o link do anúncio: {URL}")
    # A URL exata, com ou sem o ponto final da frase.
    assert _valida(f"Segue o link do anúncio: {URL}", links=[URL]) == []
    assert _valida(f"Segue o link do anúncio: {URL}.", links=[URL]) == []


@pytest.mark.parametrize(
    "texto",
    [
        f"{URL}1",  # outro item que começa igual
        f"{URL}?utm=1",
        f"{URL}/outra",
        f"{URL}.golpe.com",
        f"xhttps://shopee.com.br/product/{SHOP_ID}/{ITEM_A18}",
        f"Segue {URL} e também www.exemplo.com",
        "Segue https://shopee.com.br/product/1/2",
        "Segue shope.ee/abc",
        "minhaloja.com.br",
    ],
)
def test_d3_qualquer_outra_url_continua_barrada(texto):
    assert validador._MOTIVO_LINK in _valida(texto, links=[URL])


@pytest.mark.parametrize(("plat", "canal"), [("ml", "pergunta"), ("tiktok", "chat")])
def test_d3_fora_da_shopee_nenhuma_url_passa(plat, canal):
    assert validador._MOTIVO_LINK in _valida(f"Segue: {URL}", links=[URL], plat=plat, canal=canal)


def test_d3_a_lacuna_nunca_sai_escrita():
    motivos = _valida("Segue o link: {link_anuncio:b}")
    assert any(m.startswith(validador._MOTIVO_LACUNA) for m in motivos)
    # Nem pela tela: a pessoa vê o buraco.
    motivos = _valida("Segue o link: {link_anuncio:b}", origem="davinci_humano")
    assert any(m.startswith(validador._MOTIVO_LACUNA) for m in motivos)
    assert _valida(validador.sem_lacuna_de_link("Segue o link do anúncio: {link_anuncio:b}")) == []


# ═══════════════ D3: a lacuna e o preenchimento ═══════════════


def test_d3_o_codigo_da_lacuna_so_tem_letras():
    codigos = [catalogo.codigo_do_link(i) for i in range(800)]
    assert codigos[:3] == ["a", "b", "c"] and codigos[26] == "aa"
    assert len(set(codigos)) == 800 and all(c.isalpha() for c in codigos)
    assert catalogo.lacuna_do_link("b") == "{link_anuncio:b}"
    assert catalogo.codigo_da_lacuna("{link_anuncio:B}") == "b"
    assert catalogo.codigo_da_lacuna("{{link_anuncio:b}}") == "b"


@pytest.mark.parametrize(
    "lacuna", ["{link}", "{link_anuncio_b}", "{url}", "[link do anúncio]", "<link>"]
)
def test_d3_o_que_tem_cara_de_lacuna_de_link_e_visto(lacuna):
    assert catalogo.lacunas_de_link(f"Segue: {lacuna}") == [lacuna]
    assert catalogo.codigo_da_lacuna(lacuna) is None


def test_d3_a_frase_com_a_lacuna_desconhecida_sai_do_texto():
    texto, usados = catalogo.preencher_links(
        "Olá. Segue: {link_anuncio:a}. Veja também {link_anuncio:z}. E {link}.", {"a": URL}
    )
    assert texto == f"Olá. Segue: {URL}."
    assert usados == [URL]


def _ctx_com_link(item: str = ITEM_A18, titulo: str = "uranyx a18 pro max nfc") -> Any:
    ctx = SimpleNamespace(links_anuncio={}, links_usados=[])
    ctx.links_anuncio[item] = {"codigo": "a", "url": URL, "titulo_plano": titulo}
    ctx.links_por_codigo = lambda: {v["codigo"]: v["url"] for v in ctx.links_anuncio.values()}
    return ctx


CITA_O_A18 = [f"anuncio:{ITEM_A18}"]


@pytest.mark.parametrize(
    "texto",
    [
        "Segue o link do anúncio: {link_anuncio:z}",
        "Segue o link: {link}",
        "Segue o link: {link_anuncio_a}",
        "Segue o link: [link do anúncio]",
        # Sem as chaves também: sairia escrita para o cliente, sem link.
        "Segue o link do anúncio: link_anuncio:a",
        "Segue o link do anúncio: (link_anuncio:a)",
        "Segue o link do anúncio: `link_anuncio:a`",
        "Segue o link do anúncio: link_anuncio_a",
        "Segue o link do anúncio: link_anuncio:z",
        "Segue o link do anúncio: {link_anuncio:a",
    ],
)
def test_d3_lacuna_desconhecida_nao_sai(texto):
    (p,) = checagens.conferir_link_do_anuncio(texto, ctx=_ctx_com_link(), evidencias=CITA_O_A18)
    assert p.bloqueia and checagens.pendencia_dura(p)


def test_d3_lacuna_sem_nada_oferecido_nao_sai_como_no_ml():
    ctx = SimpleNamespace(links_anuncio={})
    (p,) = checagens.conferir_link_do_anuncio("Segue: {link_anuncio:a}", ctx=ctx, evidencias=[])
    assert p.bloqueia


@pytest.mark.parametrize(
    ("texto", "segura"),
    [
        ("Segue o link do anúncio: {link_anuncio:a}", False),
        ("Temos o A18 Pro Max, que tem NFC: {link_anuncio:a}", False),
        ("Este não tem NFC, mas temos o Uranyx A18, que tem: {link_anuncio:a}", False),
        # A frase nomeia um produto, e o link é de outro.
        ("Segue o link do C17: {link_anuncio:a}", True),
        ("Segue o link do Doogee: {link_anuncio:a}", True),
    ],
)
def test_d3_o_link_e_do_anuncio_de_que_a_frase_fala(texto, segura):
    pendencias = checagens.conferir_link_do_anuncio(
        texto, ctx=_ctx_com_link(), evidencias=CITA_O_A18
    )
    assert bool(pendencias) is segura, pendencias
    assert all(checagens.pendencia_dura(p) for p in pendencias)


def _ctx_dois_links() -> Any:
    """O `ver_anuncio` do A17 (lacuna a) e a busca do A18 (lacuna b), como na revisão."""
    ctx = SimpleNamespace(links_usados=[])
    ctx.links_anuncio = {
        ITEM_A17: {"codigo": "a", "url": "https://a", "titulo_plano": "uranyx a17 pro max"},
        ITEM_A18: {"codigo": "b", "url": URL, "titulo_plano": "uranyx a18 pro max nfc"},
    }
    return ctx


@pytest.mark.parametrize(
    "texto",
    [
        # O nome em outra frase ou parte, e a lacuna do A17.
        "Temos o A18 Pro Max. Segue o link do anúncio: {link_anuncio:a}",
        "Temos o A18 Pro Max.\nSegue o link do anúncio: {link_anuncio:a}",
        "O Doogee Fire 7 Ultra tem NFC. Segue o link do anúncio: {link_anuncio:a}",
        "Temos o Doogee, mas segue o link: {link_anuncio:a}",
        # O nome sem dígito nem marca conhecida, e o acessório na parte da lacuna.
        "Temos a Air Fryer Sluchie: {link_anuncio:a}",
        "Temos a Air Fryer Sluchie. Segue o link do anúncio: {link_anuncio:a}",
        "Segue o link da capinha: {link_anuncio:a}",
        # A lacuna do A18, e o nome do A17.
        "Temos o A17 Pro Max. Segue o link do anúncio: {link_anuncio:b}",
        # O efeito colateral aceito: falha para o lado seguro.
        "O A17 não tem NFC; o link do que tem é {link_anuncio:b}",
        # O nome só depois da lacuna.
        "Segue o link do anúncio: {link_anuncio:a}. É o A18 Pro Max, que tem NFC.",
    ],
)
def test_d3_o_link_de_outro_anuncio_nao_sai_com_o_nome_em_outra_frase(texto):
    # Citando os dois anúncios: só a conferência do nome segura.
    pendencias = checagens.conferir_link_do_anuncio(
        texto, ctx=_ctx_dois_links(), evidencias=[f"anuncio:{ITEM_A17}", f"anuncio:{ITEM_A18}"]
    )
    assert pendencias, texto
    assert all(checagens.pendencia_dura(p) and not p.bloqueia for p in pendencias)


@pytest.mark.parametrize(
    "texto",
    [
        "Temos o A18 Pro Max. Segue o link do anúncio: {link_anuncio:b}",
        "Temos o A18 Pro Max, que tem NFC. Segue o link: {link_anuncio:b}",
        "Este não tem NFC, mas temos o Uranyx A18 Pro Max, que tem. Segue o link: {link_anuncio:b}",
        "O A18 Pro Max vem com carregador. Segue o link do anúncio: {link_anuncio:b}",
        "Temos as cores informadas no anúncio. Segue o link: {link_anuncio:b}",
        "Temos a Rosa e a Prata. Segue o link do anúncio: {link_anuncio:b}",
        "Segue o link do anúncio: {link_anuncio:b}. É o A18 Pro Max, que tem NFC.",
        # O "outro modelo" solta o nome de antes: as outras conferências cuidam dele.
        "O A17 Pro Max não tem NFC, mas temos outro modelo que tem. Segue: {link_anuncio:b}",
    ],
)
def test_d3_o_link_do_anuncio_certo_segue(texto):
    assert (
        checagens.conferir_link_do_anuncio(
            texto, ctx=_ctx_dois_links(), evidencias=[f"anuncio:{ITEM_A18}"]
        )
        == []
    )


@pytest.mark.parametrize(
    ("evidencias", "segura"),
    [
        (CITA_O_A18, True),  # só o A18 citado, e o link é do A17
        ([], True),
        ([f"anuncio:{ITEM_A17}"], False),
        ([f"ficha:{ITEM_A17}"], False),  # o anúncio da própria conversa, pelo `ver_anuncio`
        ([f"variacoes:{ITEM_A17}"], False),
        ([f"  anuncio:{ITEM_A17} "], False),
    ],
)
def test_d3_o_link_exige_o_anuncio_nas_evidencias(evidencias, segura):
    pendencias = checagens.conferir_link_do_anuncio(
        "Segue o link do anúncio: {link_anuncio:a}", ctx=_ctx_dois_links(), evidencias=evidencias
    )
    assert bool(pendencias) is segura, pendencias
    assert all(checagens.pendencia_dura(p) and not p.bloqueia for p in pendencias)


def _final_com(texto: str, **kw: Any) -> ia.SaidaConferida:
    return ia.SaidaConferida(
        texto=texto,
        categoria="pedido_link_midia",
        precisa_humano=False,
        motivos=[],
        erros=[],
        status=constantes.RASCUNHO_PENDENTE,
        **kw,
    )


def test_d3_o_codigo_poe_a_url_e_guarda_o_que_usou():
    ctx = _ctx_com_link()
    final = checagens.com_os_links_do_anuncio(
        _final_com("Segue o link: {link_anuncio:a}"), ctx, plataforma="shopee", canal="chat"
    )
    assert final.texto == f"Segue o link: {URL}"
    assert ctx.links_usados == [URL]
    assert _valida(final.texto, links=ctx.links_usados) == []
    # Sem lacuna: a mesma resposta, e nenhuma URL para o envio aceitar.
    outra = _final_com("Sem link.")
    assert checagens.com_os_links_do_anuncio(outra, ctx, plataforma="shopee", canal="chat") is outra
    assert ctx.links_usados == []


def test_d3_passou_do_limite_com_a_url_nao_sai():
    ctx = _ctx_com_link()
    limite = constantes.limite_caracteres("shopee", "chat")
    final = checagens.com_os_links_do_anuncio(
        _final_com("a" * (limite - 5) + " {link_anuncio:a}"), ctx, plataforma="shopee", canal="chat"
    )
    assert final.status == constantes.RASCUNHO_BLOQUEADO and final.precisa_humano


def test_d3_o_verificador_le_a_lacuna_nao_a_url():
    entrada = v.texto_para_verificar(f"Segue o link: {URL}", {"link_anuncio:a": URL}, ())
    assert entrada == "Segue o link: {link_anuncio:a}"
    assert "lacuna do link" in v.SISTEMA
    linhas = v.linhas_da_busca(
        [
            {
                "termo": "A18",
                "anuncios": [
                    {
                        "item_id": ITEM_A18,
                        "nome_curto": "Uranyx A18",
                        "disponivel": True,
                        "link": "{link_anuncio:a}",
                    }
                ],
            }
        ],
        (),
    )
    assert any("link: {link_anuncio:a}" in linha for linha in linhas)


# ═══════════════ D3: as ferramentas ═══════════════


async def _loja_com_shop_id(db: AsyncSession, make_user, shop_id: int | None = SHOP_ID) -> Any:
    conversa, integ = await _loja_com_catalogo(db, make_user)
    if shop_id is not None:
        integ.credentials = encrypt_json({"access_token": "x", "shop_id": shop_id})
        await db.commit()
    return conversa, integ


async def test_d3_a_busca_da_a_lacuna_e_nunca_a_url(db: AsyncSession, make_user):
    conversa, _integ = await _loja_com_shop_id(db, make_user)
    base = await _base_db(db, conversa)
    ctx = _ctx(conversa, base)
    dados = await _chamar(ctx, base, conversa, "buscar_anuncios_da_loja", termo="A18")
    (a18,) = dados["anuncios"]
    assert a18["link"] == "{link_anuncio:a}"
    assert ctx.links_anuncio[ITEM_A18]["url"] == URL
    assert "http" not in str(dados) and str(SHOP_ID) not in str(dados)
    # A mesma lacuna na mesma execução, em outra ferramenta, e um código novo para o outro anúncio.
    ficha = await _chamar(ctx, base, conversa, "ver_anuncio", item_id=ITEM_A18)
    assert ficha["link"] == "{link_anuncio:a}"
    outro = await _chamar(ctx, base, conversa, "ver_anuncio", item_id=ITEM_A17)
    assert outro["link"] == "{link_anuncio:b}"
    assert ctx.links_por_codigo()["b"] == f"https://shopee.com.br/product/{SHOP_ID}/{ITEM_A17}"
    # O que a G1 e o verificador leem da busca.
    assert ctx.buscas_catalogo[0]["anuncios"][0]["link"] == "{link_anuncio:a}"


async def test_d3_sem_o_id_da_loja_nao_ha_lacuna(db: AsyncSession, make_user):
    conversa, _integ = await _loja_com_shop_id(db, make_user, shop_id=None)
    base = await _base_db(db, conversa)
    ctx = _ctx(conversa, base)
    dados = await _chamar(ctx, base, conversa, "buscar_anuncios_da_loja", termo="A18")
    assert "link" not in dados["anuncios"][0] and not ctx.links_anuncio
    assert "link" not in await _chamar(ctx, base, conversa, "ver_anuncio", item_id=ITEM_A18)


async def test_d3_o_esgotado_nao_tem_lacuna(db: AsyncSession, make_user):
    conversa, _integ = await _loja_com_shop_id(db, make_user)
    base = await _base_db(db, conversa)
    ctx = _ctx(conversa, base)
    # O cesto da loja de teste está com estoque 0.
    dados = await _chamar(ctx, base, conversa, "buscar_anuncios_da_loja", termo="cesto")
    (cesto,) = dados["anuncios"]
    assert cesto["disponivel"] is False and "link" not in cesto


async def _vinculos_do_a18(db: AsyncSession, integration_id: Any) -> list[ProductLink]:
    return list(
        (
            await db.execute(
                select(ProductLink).where(
                    ProductLink.integration_id == integration_id,
                    ProductLink.external_id == ITEM_A18,
                )
            )
        )
        .scalars()
        .all()
    )


@pytest.mark.parametrize("status", ["paused", "under_review"])
async def test_d3_o_anuncio_sem_saber_se_esta_a_venda_nao_tem_lacuna(
    db: AsyncSession, make_user, status
):
    """Estoque com o status parado dá disponivel=None: o link é só do disponivel=true."""
    conversa, integ = await _loja_com_shop_id(db, make_user)
    for vinculo in await _vinculos_do_a18(db, integ.id):
        vinculo.anuncio_status = status
    await db.commit()
    base = await _base_db(db, conversa)
    ctx = _ctx(conversa, base)
    dados = await _chamar(ctx, base, conversa, "buscar_anuncios_da_loja", termo="A18")
    assert dados["anuncios"][0]["disponivel"] is None and "link" not in dados["anuncios"][0]
    assert "link" not in await _chamar(ctx, base, conversa, "ver_anuncio", item_id=ITEM_A18)
    assert not ctx.links_anuncio


def _loja_diz_unlist(vinculos: list[ProductLink], integ_id: Any, r: RedisMem) -> None:
    """A lista da LOJA em cache com o anúncio parado (`variacoes._parado`): o status do vínculo
    fica NULL, como nos 654 anúncios Shopee em produção."""
    bruto = {
        "tier_variation": [{"option_list": [{"option": f"Op{i}"} for i in range(len(vinculos))]}],
        "model": [
            {
                "model_id": v.variation_id,
                "tier_index": [i],
                "model_sku": v.external_sku,
                "stock_info_v2": {"summary_info": {"total_available_stock": 9}},
            }
            for i, v in enumerate(vinculos)
        ],
    }
    lista = variacoes_svc.da_shopee(bruto, "UNLIST")
    assert all(v["estoque"] == 0 for v in lista)
    r.dados[variacoes_svc.chave_variacoes("shopee", integ_id, ITEM_A18)] = (
        variacoes_svc.para_o_cache(lista, datetime.now(UTC) + timedelta(minutes=1))
    )
    r.dados[ficha_svc.chave_ficha("shopee", integ_id, ITEM_A18)] = json.dumps(
        {
            "titulo": "Uranyx A18 Pro Max 128GB",
            "descricao": "Celular Uranyx A18 Pro Max com tela grande e NFC.",
            "raw": {"item_id": ITEM_A18},
        }
    )


async def test_d3_a_ficha_sem_nada_a_venda_tira_o_link(db: AsyncSession, make_user, monkeypatch):
    """A busca só vê o vínculo; a ficha ao vivo diz que nada está à venda: a lacuna que a busca já
    deu deixa de valer, e o código dela não passa para outro anúncio."""
    r = RedisMem()
    monkeypatch.setattr(ficha_svc, "redis", r)
    conversa, integ = await _loja_com_shop_id(db, make_user)
    vinculos = await _vinculos_do_a18(db, integ.id)
    assert all(v.anuncio_status is None for v in vinculos)
    _loja_diz_unlist(vinculos, integ.id, r)
    base = await _base_db(db, conversa)
    ctx = _ctx(conversa, base)
    busca = await _chamar(ctx, base, conversa, "buscar_anuncios_da_loja", termo="A18")
    assert busca["anuncios"][0]["link"] == "{link_anuncio:a}"
    ficha = await _chamar(ctx, base, conversa, "ver_anuncio", item_id=ITEM_A18)
    assert ficha["variacoes"]["completas"] is True
    assert not any(v["disponivel"] for v in ficha["variacoes"]["variacoes"])
    assert "link" not in ficha and ctx.links_por_codigo() == {}
    (p,) = checagens.conferir_link_do_anuncio(
        "Segue o link do anúncio: {link_anuncio:a}", ctx=ctx, evidencias=CITA_O_A18
    )
    assert p.bloqueia
    # Não volta a ser oferecida; o próximo anúncio não herda o código.
    assert ctx.oferecer_link(ITEM_A18, URL) is None
    outro = await _chamar(ctx, base, conversa, "ver_anuncio", item_id=ITEM_A17)
    assert outro["link"] == "{link_anuncio:b}"


@pytest.mark.parametrize(
    ("variacoes", "completas", "nada"),
    [
        ([{"nome": "A", "disponivel": False}], True, True),
        ([{"nome": "A", "disponivel": None}], True, True),
        ([{"nome": "A", "disponivel": False}, {"nome": "B", "disponivel": True}], True, False),
        ([{"nome": "A", "disponivel": False}], False, False),
        ([], True, False),  # o anúncio sem variação não dá o sinal
    ],
)
def test_d3_o_que_a_ficha_diz_do_que_esta_a_venda(variacoes, completas, nada):
    f = {"variacoes": variacoes, "variacoes_completas": completas}
    assert ferramentas._nada_a_venda_na_ficha(f) is nada
    assert ferramentas._nada_a_venda_na_ficha(None) is False


async def test_d3_o_cartao_de_outro_vendedor_nao_tem_lacuna(db: AsyncSession, make_user):
    conversa, _integ = await _loja_com_shop_id(db, make_user)
    conversa.anuncio_id = "88800000001"  # o anúncio citado, sem vínculo nesta loja
    await db.commit()
    base = await _base_db(db, conversa)
    ctx = _ctx(conversa, base)
    dados = await _chamar(ctx, base, conversa, "ver_anuncio", item_id="88800000001")
    assert "link" not in dados and not ctx.links_anuncio


async def test_d3_no_ml_a_lacuna_nao_e_oferecida(db: AsyncSession, make_user):
    conversa = await _conversa(
        db,
        make_user,
        plataforma="ml",
        canal="pergunta",
        platform=IntegrationPlatform.ML,
        pedido=None,
    )
    integ = await db.get(Integration, conversa.integration_id)
    integ.credentials = encrypt_json({"access_token": "x", "shop_id": SHOP_ID})
    produto = Product(user_id=integ.user_id, sku="sku-ml", name="Uranyx A18", stock=1)
    db.add(produto)
    await db.flush()
    db.add(
        ProductLink(
            user_id=integ.user_id,
            product_id=produto.id,
            integration_id=integ.id,
            platform=IntegrationPlatform.ML,
            external_id="MLB1234567890",
            variation_id="1",
            external_sku="sku-ml",
            listing_title="Uranyx A18 Pro Max NFC",
            stock=5,
            price=999,
            anuncio_status="active",
            last_sync_status=LinkSyncStatus.OK,
        )
    )
    await db.commit()
    base = await _base_db(db, conversa)
    ctx = _ctx(conversa, base)
    dados = await _chamar(ctx, base, conversa, "buscar_anuncios_da_loja", termo="A18")
    assert dados["anuncios"][0]["item_id"] == "MLB1234567890"
    assert "link" not in dados["anuncios"][0] and not ctx.links_anuncio
    assert "link" not in await _chamar(ctx, base, conversa, "ver_anuncio", item_id="MLB1234567890")


def test_d3_a_descricao_diz_quando_usar_e_o_ml_fica_como_estava():
    d = ferramentas.DESCRICOES[ferramentas.BUSCAR_ANUNCIOS_DA_LOJA]
    assert "{link_anuncio:x}" in d and "Nunca escreva URL" in d
    # A saída de quem não recebe o campo (o ML): orientar pelo nome e pessoa, sem negar o link.
    assert "Sem o campo link: oriente buscar pelo nome" in d and "'pessoa' (dado_indisponivel)" in d
    assert "sem dizer que não envia link" in d


def test_d3_a_url_e_a_do_montador_da_loja():
    # O mesmo montador do botão da tela: https, o domínio da Shopee, só id limpo.
    assert anuncio_publico.link_montado("shopee", ITEM_A18, shop_id=SHOP_ID) == URL
    assert anuncio_publico.link_da_loja("shopee", URL) == URL
    assert anuncio_publico.link_montado("shopee", "12/../3", shop_id=SHOP_ID) is None


# ═══════════════ D3: de ponta a ponta (o `ia._gerar`, com o banco e a API falsa) ═══════════════


async def _vortan(db: AsyncSession, make_user, pergunta: str = "Me manda o link do A18?") -> Any:
    await _importar_manual_base(db)
    conversa, _integ = await _loja_com_shop_id(db, make_user)
    await _canal_real(db, conversa)
    await _msg(db, conversa, pergunta, ha=timedelta(minutes=6))
    return conversa


async def test_e2e_d3_o_link_sai_com_a_url_e_o_verificador_leu_a_lacuna(
    db, make_user, agente_ligado, api, envios
):
    conversa = await _vortan(db, make_user)
    api.roteiro = [
        _usa("buscar_anuncios_da_loja", termo="A18"),
        _final_e2e(
            texto="Temos o A18 Pro Max. Segue o link do anúncio: {link_anuncio:a}",
            assunto="pedido_link_midia",
            confianca=0.9,
            evidencias=[f"anuncio:{ITEM_A18}"],
        ),
    ]

    r = await ia.gerar_rascunho(db, conversa)

    assert "{link_anuncio:a}" in api.resultado(1) and "shopee.com.br" not in api.resultado(1)
    assert r.texto == f"Temos o A18 Pro Max. Segue o link do anúncio: {URL}"
    assert _do_agente(r)["links_anuncio"] == [URL]
    entrada = api.verificador[0]["messages"][0]["content"]
    assert "RASCUNHO" in entrada and "shopee.com.br/product" not in entrada
    assert "«Uranyx A18 Pro Max»; disponivel=true; link: {link_anuncio:a}" in entrada
    assert "Segue o link do anúncio: {link_anuncio:a}" in entrada
    assert [e["texto"] for e in envios] == [r.texto] and _decisao(r) == "enviado", r.motivo


async def test_e2e_d3_a_url_escrita_pelo_modelo_nao_sai(db, make_user, agente_ligado, api, envios):
    conversa = await _vortan(db, make_user)
    api.roteiro = [
        _usa("buscar_anuncios_da_loja", termo="A18"),
        _final_e2e(
            texto=f"Segue o link do anúncio: {URL}",
            assunto="pedido_link_midia",
            confianca=0.9,
            evidencias=[f"anuncio:{ITEM_A18}"],
        ),
    ]

    r = await ia.gerar_rascunho(db, conversa)

    assert envios == [] and _decisao(r) != "enviado"
    assert validador._MOTIVO_LINK in (r.validador_erros or []), r.validador_erros
    assert "links_anuncio" not in _do_agente(r)


@pytest.mark.parametrize(
    ("texto", "evidencias"),
    [
        # Só o A18 citado, e o link é do A17.
        ("Temos o A18 Pro Max. Segue o link do anúncio: {link_anuncio:a}", [f"anuncio:{ITEM_A18}"]),
        # A troca de letra citando os dois.
        (
            "Temos o A18 Pro Max. Segue o link do anúncio: {link_anuncio:a}",
            [f"ficha:{ITEM_A17}", f"anuncio:{ITEM_A18}"],
        ),
    ],
)
async def test_e2e_d3_o_link_de_outro_anuncio_nao_sai(
    db, make_user, agente_ligado, api, envios, texto, evidencias
):
    conversa = await _vortan(db, make_user)
    api.roteiro = [
        _usa("ver_anuncio", item_id=ITEM_A17),
        _usa("buscar_anuncios_da_loja", termo="A18"),
        _final_e2e(texto=texto, assunto="pedido_link_midia", confianca=0.9, evidencias=evidencias),
    ]

    r = await ia.gerar_rascunho(db, conversa)

    assert envios == [] and _decisao(r) != "enviado", r.motivo


async def test_e2e_d3_o_link_com_a_ficha_do_proprio_anuncio_sai(
    db, make_user, agente_ligado, api, envios
):
    conversa = await _vortan(db, make_user)
    api.roteiro = [
        _usa("ver_anuncio", item_id=ITEM_A18),
        _final_e2e(
            texto="Segue o link do anúncio: {link_anuncio:a}",
            assunto="pedido_link_midia",
            confianca=0.9,
            evidencias=[f"ficha:{ITEM_A18}"],
        ),
    ]

    r = await ia.gerar_rascunho(db, conversa)

    assert r.texto == f"Segue o link do anúncio: {URL}"
    assert [e["texto"] for e in envios] == [r.texto] and _decisao(r) == "enviado", r.motivo


async def test_e2e_d3_a_lacuna_da_busca_com_a_ficha_sem_nada_a_venda_nao_sai(
    db, make_user, agente_ligado, api, envios, monkeypatch
):
    r_mem = RedisMem()
    monkeypatch.setattr(ficha_svc, "redis", r_mem)
    conversa = await _vortan(db, make_user)
    _loja_diz_unlist(
        await _vinculos_do_a18(db, conversa.integration_id), conversa.integration_id, r_mem
    )
    api.roteiro = [
        _usa("buscar_anuncios_da_loja", termo="A18"),
        _usa("ver_anuncio", item_id=ITEM_A18),
        _final_e2e(
            texto="Segue o link do anúncio: {link_anuncio:a}",
            assunto="pedido_link_midia",
            confianca=0.9,
            evidencias=[f"anuncio:{ITEM_A18}"],
        ),
    ]

    r = await ia.gerar_rascunho(db, conversa)

    assert envios == [] and _decisao(r) != "enviado", r.motivo
    assert "shopee.com.br" not in (r.texto or "") and "links_anuncio" not in _do_agente(r)


@pytest.mark.parametrize(
    "lacuna",
    ["{link_anuncio:z}", "{link_anuncio_a}", "{link}", "link_anuncio:a", "(link_anuncio:a)"],
)
async def test_e2e_d3_a_lacuna_de_fora_nao_sai_nem_fica_no_rascunho(
    db, make_user, agente_ligado, api, envios, lacuna
):
    conversa = await _vortan(db, make_user)
    api.roteiro = [
        _usa("buscar_anuncios_da_loja", termo="A18"),
        _final_e2e(
            texto=f"Temos o A18 Pro Max. Segue o link: {lacuna}",
            assunto="pedido_link_midia",
            confianca=0.9,
            evidencias=[f"anuncio:{ITEM_A18}"],
        ),
    ]

    r = await ia.gerar_rascunho(db, conversa)

    assert envios == [] and _decisao(r) != "enviado"
    assert r.status == constantes.RASCUNHO_BLOQUEADO, r.motivo
    assert lacuna not in (r.texto or "") and "link_anuncio" not in (r.texto or "")


# ═══════════════ D3: o envio confere de novo ═══════════════


async def _rascunho(db: AsyncSession, conversa: Any, texto: str, links: list[str]) -> Any:
    r = AtendimentoRascunho(
        conversa_id=conversa.id,
        texto=texto,
        categoria="pedido_link_midia",
        status="pendente",
        precisa_humano=False,
        validador_ok=True,
        prompt_versao="ag1",
        fatos={"agente": {"links_anuncio": links}},
    )
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return r


async def _isencao(db: AsyncSession, conversa: Any, texto: str, rascunho: Any) -> str:
    recusa = enviar.EnvioRecusado(enviar.RECUSA_TEXTO_INVALIDO, ["link"])
    return await enviar._texto_com_isencao_do_agente(
        db,
        conversa,
        texto,
        origem=constantes.ORIGEM_IA,
        rascunho_id=rascunho.id,
        recusa=recusa,
    )


async def test_d3_o_envio_aceita_so_a_url_do_rascunho(db: AsyncSession, make_user, ligado):
    conversa, _user = await _cenario(db, make_user)
    texto = f"Segue o link do anúncio: {URL}"
    r = await _rascunho(db, conversa, texto, [URL])
    assert await _isencao(db, conversa, texto, r) == texto
    # Outro link junto, ou o rascunho sem a URL: a mesma recusa.
    with pytest.raises(enviar.EnvioRecusado):
        await _isencao(db, conversa, f"{texto} e www.exemplo.com", r)
    r.status = "descartado"
    await db.commit()
    sem = await _rascunho(db, conversa, texto, [])
    with pytest.raises(enviar.EnvioRecusado):
        await _isencao(db, conversa, texto, sem)


# ═══════════════ a oferta só com o anúncio citado, disponivel=true ═══════════════


def _busca(disponivel: bool | None, nome: str = "Smartphone Titan") -> dict:
    return {
        "termo": "NFC",
        "achados": ["1"],
        "anuncios": [
            {
                "item_id": "1",
                "nome_curto": nome,
                "disponivel": disponivel,
                "titulo_plano": catalogo.plano(nome),
            }
        ],
    }


def _g1(
    texto: str, *, buscas: list[dict] | None = None, citar: bool = True, titulo: str = "Uranyx A17"
) -> list[Any]:
    ctx = SimpleNamespace(buscas_catalogo=buscas or [], anuncios_buscados={}, fichas_lidas=[])
    return checagens.conferir_outro_produto_da_loja(
        texto,
        acao="responder",
        evidencias=["anuncio:1"] if citar else [],
        ctx=ctx,
        fichas=[],
        fontes_extra=[titulo],
    )


@pytest.mark.parametrize(
    "texto",
    [
        "Este modelo não tem NFC, mas temos outro modelo que tem.",
        "Temos outra versão que tem NFC.",
        "Temos sim, outro modelo com NFC.",
        "Temos outro modelo com NFC, gostaria de ver?",
        "Temos uma versão com NFC.",
        "Temos outro na loja com NFC.",
        "Este não tem NFC, mas temos o Smartphone Titan, que tem.",
        "Temos sim o Smartphone Titan.",
        # As outras formas do mesmo "temos".
        "Temos sim, modelos com NFC.",
        "Temos modelos com NFC.",
        "Temos versões com NFC.",
        "Temos celulares com NFC.",
        "Temos, sim, um outro modelo com NFC.",
        "A loja tem outro modelo com NFC.",
        "A nossa loja vende outro modelo com NFC.",
        "Vendemos outro modelo com NFC.",
        "Este não tem NFC, mas vendemos outro modelo que tem.",
        "Vendemos o Smartphone Titan.",
        "Temos, sim, o Smartphone Titan.",
    ],
)
@pytest.mark.parametrize(
    ("buscas", "citar"),
    [
        ([], False),  # sem busca
        ([_busca(True)], False),  # sem citar
        ([_busca(None)], True),  # a busca só trouxe o pausado
        ([_busca(False)], True),  # esgotado
    ],
)
def test_a_oferta_sem_o_anuncio_a_venda_citado_nao_sai(texto, buscas, citar):
    (p,) = _g1(texto, buscas=buscas, citar=citar)
    assert p.codigo == checagens.OUTRO_PRODUTO_SEM_BUSCA
    assert checagens.pendencia_dura(p), p.motivo


@pytest.mark.parametrize(
    "texto",
    [
        "Este modelo não tem NFC, mas temos outro modelo que tem.",
        "Este não tem NFC, mas temos o Smartphone Titan, que tem.",
    ],
)
def test_a_oferta_com_o_anuncio_a_venda_citado_segue(texto):
    assert _g1(texto, buscas=[_busca(True)]) == []


def test_o_nome_da_oferta_tem_de_ser_o_do_anuncio_citado():
    (p,) = _g1("Temos o Smartphone Titan, que tem.", buscas=[_busca(True, nome="Mala Viagem")])
    assert checagens.pendencia_dura(p)


def _duas_linhas(*linhas: tuple[str, bool | None]) -> list[dict]:
    return [
        {
            "termo": "NFC",
            "achados": [str(i) for i, _ in enumerate(linhas)],
            "anuncios": [
                {
                    "item_id": str(i),
                    "nome_curto": nome,
                    "disponivel": disponivel,
                    "titulo_plano": catalogo.plano(nome),
                }
                for i, (nome, disponivel) in enumerate(linhas)
            ],
        }
    ]


def _g1_com(texto: str, buscas: list[dict], evidencias: list[str]) -> list[Any]:
    ctx = SimpleNamespace(buscas_catalogo=buscas, anuncios_buscados={}, fichas_lidas=[])
    return checagens.conferir_outro_produto_da_loja(
        texto, acao="responder", evidencias=evidencias, ctx=ctx, fontes_extra=["Uranyx A17"]
    )


DOOGEES = (("Doogee Spunk Pro", True), ("Doogee Fire 6 Power", True))
OFERTA_DE_DOIS = "Temos o Doogee Spunk Pro e o Doogee Fire 6 Power."


@pytest.mark.parametrize(
    ("texto", "linhas"),
    [
        (OFERTA_DE_DOIS, DOOGEES),
        (
            "Este não tem NFC, mas temos o Uranyx A18 Pro Max e o Uranyx T8, que têm.",
            (("Uranyx A18 Pro Max", True), ("Uranyx T8", True)),
        ),
        (
            "Temos modelos com NFC: A18 Pro Max e T8.",
            (("Uranyx A18 Pro Max", True), ("Uranyx T8", True)),
        ),
    ],
)
def test_a_oferta_de_dois_anuncios_citados_segue(texto, linhas):
    assert _g1_com(texto, _duas_linhas(*linhas), ["anuncio:0", "anuncio:1"]) == []


@pytest.mark.parametrize(
    ("linhas", "evidencias"),
    [
        (DOOGEES, ["anuncio:0"]),  # só um citado
        ((DOOGEES[0], ("Doogee Fire 6 Power", None)), ["anuncio:0", "anuncio:1"]),  # um pausado
        ((DOOGEES[0], ("Doogee Fire 6 Power", False)), ["anuncio:0", "anuncio:1"]),  # um esgotado
    ],
)
def test_a_oferta_de_dois_com_um_so_a_venda_citado_nao_sai(linhas, evidencias):
    (p,) = _g1_com(OFERTA_DE_DOIS, _duas_linhas(*linhas), evidencias)
    assert checagens.pendencia_dura(p), p.motivo


def test_o_acessorio_tem_de_estar_na_mesma_linha_do_modelo():
    linhas = (("Capa Uranyx C17", True), ("Uranyx A18 Pro Max", True))
    (p,) = _g1_com("Temos a capa do A18.", _duas_linhas(*linhas), ["anuncio:0", "anuncio:1"])
    assert checagens.pendencia_dura(p), p.motivo


def test_o_milhar_e_a_contagem_nao_sao_nome_de_modelo():
    assert checagens._modelos_e_marcas("o t8 tem bateria de 10.000 mah") == ({"t8"}, set())
    assert checagens._modelos_e_marcas("temos 3 opcoes e 2 cores e 4 versoes") == (set(), set())
    assert checagens._modelos_e_marcas("o a18 de 12.128 gb") == ({"a18"}, set())
    # O número do modelo continua sendo modelo.
    assert checagens._modelos_e_marcas("o fire 7 ultra") == ({"7"}, set())


@pytest.mark.parametrize(
    "texto",
    [
        "Temos o Uranyx T8, de 10.000 mAh.",
        "Temos o Uranyx T8, com 3 opções de cor.",
    ],
)
def test_o_numero_da_especificacao_nao_entra_no_nome_da_oferta(texto):
    assert _g1_com(texto, _duas_linhas(("Uranyx T8", True)), ["anuncio:0"]) == []


@pytest.mark.parametrize(
    "texto",
    [
        # 18 vezes da equipe em 60 dias.
        "temos as cores informado no anuncio",
        "Temos as cores informadas no anúncio.",
        "Temos o modelo informado no anúncio.",
        "Temos o modelo conforme o anúncio.",
        "Temos os modelos informados no anúncio.",
        "Temos as versões informadas no anúncio.",
        "Temos modelos informados no anúncio.",
        "Temos a pronta entrega.",
        "Temos a Rosa neste anúncio.",
        "Temos a Rosa apenas.",
        "Temos a Rosa agora.",
        "Temos 3 opções de cor: Rosa, Prata e Azul.",
        "O A17 tem outro carregador.",
    ],
)
def test_a_frase_da_equipe_nao_e_oferta(texto):
    assert not any(checagens.pendencia_dura(p) for p in _g1(texto, citar=False)), texto


@pytest.mark.parametrize(
    "texto",
    [
        "Temos as cores informadas no anúncio.",
        "Temos o modelo conforme o anúncio.",
        "Temos a Rosa neste anúncio.",
        "Temos a Rosa apenas.",
    ],
)
async def test_a_frase_da_equipe_nao_vira_pessoa_pela_oferta(texto):
    ficha = {
        **_ficha(("Rosa, 42mm", True), ("Prata, 46mm", True), ("Azul, 46mm", True)),
        "titulo": "Uranyx F105",
    }
    r = await _auto(
        _proposta(texto, "estoque_variacao", ["ficha:41180596258", "variacoes:41180596258"], 0.9),
        [_menu(), *_cli("Tem na cor rosa?")],
        fichas=[ficha],
    )
    assert not any("oferece" in m for m in r.motivos), r.motivos


def test_o_anuncio_da_conversa_nao_e_o_outro_modelo():
    busca = {"termo": "NFC", "achados": ["1"], "anuncios": [{**_busca(True)["anuncios"][0]}]}
    busca["anuncios"][0]["da_conversa"] = True
    (p,) = _g1("Temos outro modelo com NFC.", buscas=[busca])
    assert checagens.pendencia_dura(p), p.motivo
    # A linha de outro anúncio segue para o verificador (o recurso, ele confere na ficha).
    assert _g1("Temos outro modelo com NFC.", buscas=[_busca(True, "Mala de Viagem 20")]) == []
    s = " ".join(v.SISTEMA.split())
    assert 'se não é "o anúncio desta conversa", «temos outro modelo»' in s
    assert "o recurso que a frase cita continua precisando da ficha ou do título" in s


@pytest.mark.parametrize(
    "texto",
    [
        "Temos sim a cor azul.",
        "Você quer saber se temos outro modelo?",
        "Temos outra opção de cor no anúncio.",
        "Temos a opção de 128 GB.",
        "Temos a versão 128 GB.",
        "Temos o tamanho M.",
        "Temos a Prata e a Rosa.",
        "Temos sim.",
        "Temos outro modelo neste anúncio.",
        "Temos outro modelo no anúncio.",
        "Temos o A17 em estoque.",  # o produto da conversa
        "Não temos previsão de reposição.",
        "Não temos como confirmar a cidade.",
    ],
)
def test_o_temos_que_nao_oferece_outro_produto_nao_muda(texto):
    assert _g1(texto) == []


def test_o_resto_da_g1_continua_so_aviso():
    (p,) = _g1("O Uranyx A18 Pro Max está à venda em outro anúncio da nossa loja.")
    assert p.codigo == checagens.OUTRO_PRODUTO_SEM_BUSCA and not checagens.pendencia_dura(p)
    assert "disponivel=null não sustenta tem nem vende" in v.SISTEMA


# ═══════════════ 87747398: o "sem previsão" fora da lista completa ═══════════════


def _ficha(*opcoes: tuple[str, bool], completas: bool = True) -> dict:
    return {
        "item_id": "41180596258",
        "titulo": "Apple Watch Series 11",
        "variacoes": [
            {"nome": n, "disponivel": d, "confiavel": True, "fonte": "loja"} for n, d in opcoes
        ],
        "variacoes_completas": completas,
    }


ROSA_E_PRATA = (("Rosa, 42mm", True), ("Prata, 46mm", True))
AS_DEMAIS = (
    "Olá! Por enquanto, as opções disponíveis neste anúncio são Rosa 42mm e Prata 46mm. "
    "Não temos previsão de reposição das demais."
)


def _sem_previsao(texto: str, ficha: dict, cliente: str) -> list[Any]:
    return checagens.conferir_sem_previsao(texto, [ficha], [cliente])


def test_87747398_a_opcao_fora_da_lista_completa_nao_avisa():
    assert _sem_previsao(AS_DEMAIS, _ficha(*ROSA_E_PRATA), "Tem na cor cinza?") == []
    sem_a_cinza = "Não temos previsão de reposição da cinza."
    assert _sem_previsao(sem_a_cinza, _ficha(*ROSA_E_PRATA), "E a cinza?") == []


def test_87747398_dizer_esgotada_o_que_a_lista_nao_tem_continua_avisando():
    texto = "A cinza está esgotada e sem previsão de reposição."
    assert _sem_previsao(texto, _ficha(*ROSA_E_PRATA), "E a cinza?")


def test_87747398_a_lista_incompleta_continua_avisando():
    ficha = _ficha(*ROSA_E_PRATA, completas=False)
    assert _sem_previsao(AS_DEMAIS, ficha, "Tem na cor cinza?")
    assert _sem_previsao("Não temos previsão de reposição da cinza.", ficha, "E a cinza?")


def test_87747398_a_opcao_disponivel_continua_contradizendo():
    pendencias = _sem_previsao(
        "Não temos previsão de reposição da Rosa.", _ficha(*ROSA_E_PRATA), "Quando chega a rosa?"
    )
    assert any(p.codigo == checagens.VARIACAO_CONTRADIZ for p in pendencias)
    # O cliente pede a Rosa, que está à venda: o "das demais" não a tira da conta.
    assert _sem_previsao(
        "Não temos previsão de reposição.", _ficha(*ROSA_E_PRATA), "Quando chega a rosa?"
    )


def test_87747398_o_verificador_v18_diz_o_que_vale():
    padrao = v.FRASE_PADRAO_REPOSICAO
    assert '"completas": true não tem' in padrao and "das demais" in padrao
    assert 'que uma lista de "completas": false não tem' in padrao
    assert "disponível" in padrao and "promessa de volta" in padrao
    assert "se essa lista traz cores" in padrao
    assert "TODAS as opções disponíveis dessa lista" in padrao


SO_MEMORIA = (("12 GB / 128 GB", True), ("16 GB / 128 GB", True))
TRES_A_VENDA = (("Rosa, 42mm", True), ("Prata, 46mm", True), ("Azul, 46mm", True))


@pytest.mark.parametrize(
    ("texto", "titulo", "opcoes", "cliente"),
    [
        # A lista completa não diz nada de cor: o texto insinua que o azul existiu e acabou.
        ("Não temos previsão de reposição da cor azul.", "Uranyx A17", SO_MEMORIA, "Tem azul?"),
        # Nega a cor do próprio título.
        ("A preta não tem previsão de reposição.", "Uranyx A17 Preto", SO_MEMORIA, "Tem preto?"),
        ("A preta não tem previsão de reposição.", "Apple Watch Preto", ROSA_E_PRATA, "Tem preto?"),
    ],
)
@pytest.mark.parametrize("plataforma_", ["shopee", "ml"])
async def test_87747398_a_cor_que_a_lista_nao_tem_como_dizer_continua_avisando(
    texto, titulo, opcoes, cliente, plataforma_
):
    ficha = {**_ficha(*opcoes), "titulo": titulo}
    assert _sem_previsao(texto, ficha, cliente)
    r = await _auto(
        _proposta(texto, "estoque_variacao", ["ficha:41180596258", "variacoes:41180596258"], 0.9),
        [_menu(), *_cli(cliente)],
        fichas=[ficha],
        plataforma=plataforma_,
    )
    assert checagens.MOTIVO_SEM_PREVISAO_SEM_DADO in [*r.avisos, *r.motivos]


@pytest.mark.parametrize(
    ("texto", "cliente"),
    [
        # Prata e Azul estão à venda, e o cliente entende que acabaram.
        ("Temos a Rosa 42mm. Não temos previsão de reposição das demais.", "Tem na cor cinza?"),
        ("Temos a Rosa e a Prata. Não temos previsão de reposição das demais.", "Tem cinza?"),
        # O cliente nem citou opção fora da lista.
        ("Não temos previsão de reposição das demais cores.", "Quais cores tem?"),
        ("Não há previsão de reposição das outras opções.", "Tem outras?"),
    ],
)
def test_87747398_as_demais_sem_citar_as_opcoes_a_venda_continua_avisando(texto, cliente):
    (p,) = _sem_previsao(texto, _ficha(*TRES_A_VENDA), cliente)
    assert p.motivo == checagens.MOTIVO_SEM_PREVISAO_SEM_DADO


@pytest.mark.parametrize(
    ("texto", "opcoes"),
    [
        ("As opções são Rosa, Prata e Azul. Não temos previsão de reposição das demais.", None),
        # A esgotada fora da citação: não é opção à venda.
        (
            "As opções são Rosa e Prata. Não temos previsão de reposição das demais.",
            (("Rosa, 42mm", True), ("Prata, 46mm", True), ("Azul, 46mm", False)),
        ),
    ],
)
def test_87747398_as_demais_depois_de_todas_as_opcoes_a_venda_nao_avisa(texto, opcoes):
    assert _sem_previsao(texto, _ficha(*(opcoes or TRES_A_VENDA)), "Tem na cor cinza?") == []


# ═══════════════ c7d6db8b: a confirmação curta e a NF antes da compra ═══════════════


async def test_c7d6db8b_a_nf_antes_da_compra_nao_cobra_pedido():
    r = await _auto(
        _proposta(
            "Todos os pedidos acompanham nota fiscal, que vai junto com o produto.",
            "nota_fiscal",
            ["regra:nota_fiscal"],
            0.9,
        ),
        [_menu(), *_cli("Vem com nota fiscal?")],
    )
    assert "conversa sem pedido identificado" not in r.avisos
    assert "conversa sem pedido identificado" not in r.motivos


ESTADO_DA_NF = "A nota fiscal do seu pedido já foi emitida e vai junto com o produto."
REGRA_DA_NF = "Todos os pedidos acompanham nota fiscal, que vai junto com o produto."


def _nf(texto: str) -> Any:
    return _proposta(texto, "nota_fiscal", ["regra:nota_fiscal"], 0.9)


@pytest.mark.parametrize(
    "cliente",
    [
        "Comprei ontem, vai vir com nota fiscal?",
        "Já paguei, a nota fiscal vem junto?",
        "Quero a nota fiscal da minha compra",
    ],
)
async def test_c7d6db8b_o_estado_da_nf_sem_pedido_continua_cobrando(cliente):
    r = await _auto(_nf(ESTADO_DA_NF), [_menu(), *_cli(cliente)])
    assert "conversa sem pedido identificado" in [*r.avisos, *r.motivos]


async def test_c7d6db8b_a_regra_da_nf_nao_cobra_pedido_nem_no_pos_venda():
    r = await _auto(_nf(REGRA_DA_NF), [_menu(), *_cli("Comprei ontem, vai vir com nota fiscal?")])
    assert "conversa sem pedido identificado" not in [*r.avisos, *r.motivos]


async def test_c7d6db8b_a_confirmacao_curta_nao_cobra_pedido():
    r = await _auto(_nf("Sim, é verdade."), [_menu(), *_cli("Vem com nota fiscal? É verdade?")])
    assert "conversa sem pedido identificado" not in [*r.avisos, *r.motivos]


async def test_c7d6db8b_com_a_politica_aprovada_o_estado_da_nf_avisa_pela_regua():
    """Em produção, "Meu pedido vem com nota fiscal?" tem `nf_politica_aprovada`: o motivo do
    pedido nem nasce, e quem avisa é a `_DIZ_QUE_EMITIU`, com "do seu pedido" no meio."""
    r = await _auto(
        _nf(ESTADO_DA_NF),
        [_menu(), *_cli("Meu pedido vem com nota fiscal?")],
        nf_politica_aprovada=True,
    )
    assert "afirma a nota fiscal emitida sem o dado" in [*r.avisos, *r.motivos]


@pytest.mark.parametrize(
    ("texto", "so_a_regra"),
    [
        (REGRA_DA_NF, True),
        (f"Olá! {REGRA_DA_NF}", True),
        ("Sim, é verdade: o Apple Watch acompanha nota fiscal, que vai junto com o produto.", True),
        ("Todo pedido tem nota fiscal.", True),
        ("Sim, é verdade.", True),
        ("Isso mesmo.", True),
        (ESTADO_DA_NF, False),
        ("A sua nota fiscal vem com o produto.", False),
        ("Todos os pedidos acompanham nota fiscal, e o seu pedido já foi enviado.", False),
        ("A nota fiscal já foi emitida.", False),
        ("O pedido chega amanhã e vem com nota fiscal.", False),
        ("Sim, chega amanhã.", False),
        ("A garantia é de 90 dias.", False),
    ],
)
def test_c7d6db8b_so_a_regra_da_nf(texto, so_a_regra):
    assert ia.so_a_regra_da_nf(texto) is so_a_regra


async def test_c7d6db8b_rastreio_sem_pedido_continua_cobrando():
    r = await _auto(
        _proposta("O seu pedido já foi enviado.", "rastreio", ["regra:rastreio"], 0.9),
        [_menu(), *_cli("Cadê o meu pedido?")],
    )
    assert "conversa sem pedido identificado" in [*r.avisos, *r.motivos]


async def test_c7d6db8b_o_texto_aprovado_entra_no_ja_enviado(db: AsyncSession, make_user):
    conversa = await _conversa(db, make_user, pedido=None)
    texto = "Olá! Todos os pedidos acompanham nota fiscal, que vai junto com o produto."

    async def _r(versao: str, fatos: dict) -> Any:
        r = AtendimentoRascunho(
            conversa_id=conversa.id,
            texto=texto,
            categoria="nota_fiscal",
            status="enviado",
            precisa_humano=False,
            validador_ok=True,
            prompt_versao=versao,
            fatos=fatos,
        )
        db.add(r)
        await db.commit()
        await db.refresh(r)
        return r

    aprovado = await _r("v10", {"resposta_basica_aprovada": True})
    legado = await _r("v10", {})
    quando = datetime(2026, 11, 4, 13, 3, tzinfo=UTC)

    def _fala(rascunho: Any, fala: str = texto) -> SimpleNamespace:
        return SimpleNamespace(
            id=uuid4(),
            autor=constantes.AUTOR_LOJA,
            origem=constantes.ORIGEM_IA,
            texto=fala,
            tipo="texto",
            payload={},
            rascunho_id=rascunho.id,
            enviada_em=quando,
            created_at=quando,
        )

    recentes = [_fala(aprovado), _fala(legado)]
    conferidas = await v.respostas_conferidas(db, recentes, None)
    assert set(conferidas) == {str(aprovado.id)}
    (linha,) = v.ja_enviado_da_conversa(recentes, None, (), conferidas)
    assert "acompanham nota fiscal" in linha
    # O texto mudou entre o rascunho e o envio: não é o aprovado.
    assert v.ja_enviado_da_conversa([_fala(aprovado, "Outra coisa.")], None, (), conferidas) == ()


def test_c7d6db8b_o_verificador_v18_diz_o_que_vale():
    s = " ".join(v.SISTEMA.split())
    assert "(v18) a regra da loja que essa seção diz" in s and "«Sim, é verdade»" in s
    assert "nunca sustenta estado, data, prazo ou valor de pedido, promessa nem aprovação" in s
    assert "«é verdade? se for eu compro»" in s


# ═══════════════ 76417da4: negar o link não tem lastro ═══════════════


def test_76417da4_negar_o_link_nao_tem_lastro():
    s = " ".join(v.SISTEMA.split())
    assert "dizer que a loja não envia ou não pode enviar link" in s
    assert validador._MOTIVO_LINK in _valida("Não enviamos links por aqui.")


# ═══════════════ o contexto volta com as ferramentas ═══════════════


def test_a_2a_passada_nao_herda_os_links_da_1a():
    conversa = SimpleNamespace(
        id=uuid4(),
        plataforma="shopee",
        canal="chat",
        integration_id=None,
        canal_id=None,
        comprador_id=None,
        anuncio_id=None,
        pedido_marketplace=None,
        dados={},
    )
    ctx = contexto.ContextoAgente.da_conversa(conversa, None, modo=constantes.MODO_AUTO)
    antes = ctx.estado_das_ferramentas()
    assert ctx.oferecer_link(ITEM_A18, URL) == "{link_anuncio:a}"
    assert ctx.oferecer_link(ITEM_A18, URL) == "{link_anuncio:a}"
    assert ctx.oferecer_link(ITEM_A17, "https://shopee.com.br/product/1/2") == "{link_anuncio:b}"
    ctx.voltar_ferramentas(antes)
    assert ctx.links_anuncio == {} and ctx.links_por_codigo() == {}
    # Só com a URL no texto o registro leva a chave (o registro de antes não muda).
    assert "links_anuncio" not in ctx.para_fatos()
    ctx.links_usados = [URL]
    assert ctx.para_fatos()["links_anuncio"] == [URL]


# ═══════════════ a calibração: os espelhos da revisão da rodada 2 ═══════════════


def test_a_calibracao_tem_os_espelhos_da_revisao():
    linhas = (
        Path(__file__).resolve().parent.parent
        / "scripts"
        / "agente_eval"
        / "calibracao_verificador.jsonl"
    ).read_text()
    casos = {c["id"]: c for c in map(json.loads, linhas.splitlines()) if c["grupo"] == "an0411"}
    assert len(casos) == 22
    boas = [c for c in casos.values() if c["esperado"]["ok_para_envio_automatico"]]
    assert len(boas) == 6
    curtas = {k: c for k, c in casos.items() if "confirmacao-curta" in k}
    assert {c["texto"] for c in curtas.values()} == {"Sim, é verdade.", "Isso mesmo."}
    assert all(c["ja_enviado"] for c in curtas.values())
    assert sorted(c["esperado"]["ok_para_envio_automatico"] for c in curtas.values()) == [
        False,
        False,
        False,
        True,
    ]
    troca = casos["an0411-ruim-link-letra-trocada"]
    assert len(troca["busca"][0]["anuncios"]) == 2
    assert (
        troca["texto"].startswith("Temos o A18 Pro Max.") and "{link_anuncio:a}" in troca["texto"]
    )
    assert casos["an0411-boa-link-duas-linhas-nome-na-frase-anterior"]["texto"].endswith(
        "{link_anuncio:b}"
    )


@pytest.mark.parametrize(
    "texto",
    ["Segue: link_anúncio:a", "Segue: link-anuncio:a", "Segue: link anuncio a", "link_do_anuncio:a"],  # noqa: E501
)
def test_a_lacuna_mal_escrita_e_reconhecida(texto):
    assert catalogo.lacunas_de_link(texto)


@pytest.mark.parametrize(
    "texto", ["Segue o link do anúncio.", "O link anuncio foi enviado", "Pode ver o link"]
)
def test_a_frase_normal_sobre_o_link_nao_e_lacuna(texto):
    assert not catalogo.lacunas_de_link(texto)


def test_a_ficha_antes_da_busca_tambem_retira_o_link():
    ctx = SimpleNamespace(links_anuncio={})
    contexto.ContextoAgente.retirar_link(ctx, "123")
    assert ctx.links_anuncio["123"]["url"] is None


@pytest.mark.parametrize(
    "texto",
    [
        "Temos versões de 128 GB e 256 GB.",
        "Temos uma versão de 256 GB.",
        "Temos outras opções: Rosa, Prata e Azul.",
    ],
)
def test_as_variacoes_daqui_nao_sao_oferta_de_outro_produto(texto):
    assert not checagens._TEMOS_SEM_NOME.search(checagens._plano(texto))


@pytest.mark.parametrize("texto", ["Temos outro modelo com NFC.", "Vendemos outro modelo com NFC."])
def test_a_oferta_de_outro_modelo_continua_reconhecida(texto):
    assert checagens._TEMOS_SEM_NOME.search(checagens._plano(texto))
