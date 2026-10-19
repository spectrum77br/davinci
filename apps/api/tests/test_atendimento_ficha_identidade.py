# ruff: noqa: F401, F811 — fixtures importadas, nenhuma consulta a marketplace
"""A ficha própria leva identidade verificável, sem misturar lojas ou variações."""

import pytest

from app.models import AtendimentoConversa, Product, ProductLink
from app.models.enums import IntegrationPlatform
from app.services.atendimento import ficha
from tests.test_atendimento_ficha import (
    _integ,
    _listing,
    _m,
    _vinculo,
    redis_ficha,
)


async def _produto(db, user, sku):
    p = Product(user_id=user.id, sku=sku, name=f"Modelo {sku}")
    db.add(p)
    await db.commit()
    return p


def _fresca(redis_ficha, loja, item="111"):
    redis_ficha.guardar(
        loja.platform.value,
        loja.id,
        item,
        {"titulo": "Relógio", "descricao": "Caixa em alumínio", "raw": {}},
    )


async def test_ficha_so_identifica_vinculos_vivos_da_propria_loja_plataforma_e_anuncio(
    db, make_user, redis_ficha
):
    user = await make_user()
    loja = await _integ(db, user, IntegrationPlatform.SHOPEE, "ATV")
    outra = await _integ(db, user, IntegrationPlatform.SHOPEE, "Kia")
    produtos = [await _produto(db, user, f"i{i}.sa") for i in range(6)]
    await _vinculo(db, loja, produtos[0], "111", variacao="v1")
    await _vinculo(db, loja, produtos[1], "111", variacao="v2")
    await _vinculo(db, outra, produtos[2], "111", variacao="v3")
    await _vinculo(db, loja, produtos[3], "222", variacao="v4")
    await _vinculo(db, loja, produtos[4], "111", variacao="v5", morto=True)
    # Mesmo integration_id, mas plataforma inconsistente: não é identidade
    # do anúncio Shopee. Não basta filtrar apenas external_id/integration.
    db.add(
        ProductLink(
            user_id=user.id,
            integration_id=loja.id,
            product_id=produtos[5].id,
            platform=IntegrationPlatform.ML,
            external_id="111",
            variation_id="v6",
        )
    )
    await db.commit()
    _fresca(redis_ficha, loja)
    conversa = AtendimentoConversa(
        plataforma="shopee", canal="chat", integration_id=loja.id, anuncio_id="111"
    )
    # SKU e produto que o comprador insere no anexo não são fonte da identidade.
    cartao = [{"tipo": "produto", "item_id": "111", "sku": produtos[2].sku}]
    (f,) = await ficha.fichas_da_conversa(db, conversa, [_m("cliente", cartao)])

    assert {r["product_id"] for r in f["produtos_vinculados"]} == {
        str(produtos[0].id),
        str(produtos[1].id),
    }
    assert {r["sku"] for r in f["produtos_vinculados"]} == {"i0.sa", "i1.sa"}
    assert {r["variation_id"] for r in f["produtos_vinculados"]} == {"v1", "v2"}
    assert f["fonte"].startswith("loja (Shopee)")


@pytest.mark.parametrize("variacao", ["v1", "ausente", "morta", "ambigua"])
async def test_variacao_explicita_resolve_so_identidade_unica_sem_fallback(
    db, make_user, redis_ficha, variacao
):
    user = await make_user()
    loja = await _integ(db, user, IntegrationPlatform.SHOPEE, "Jlas")
    p1, p2 = await _produto(db, user, "i238.sa"), await _produto(db, user, "i242.sa")
    await _vinculo(db, loja, p1, "111", variacao="v1")
    await _vinculo(db, loja, p2, "111", variacao="morta", morto=True)
    await _vinculo(db, loja, p1, "111", variacao="ambigua")
    await _vinculo(db, loja, p2, "111", variacao="ambigua")
    _fresca(redis_ficha, loja)
    conversa = AtendimentoConversa(plataforma="shopee", canal="chat", integration_id=loja.id)
    cartao = [{"tipo": "produto", "item_id": "111", "model_id": variacao}]

    (f,) = await ficha.fichas_da_conversa(db, conversa, [_m("cliente", cartao)])

    if variacao == "v1":
        assert f["produtos_vinculados"] == [
            {"product_id": str(p1.id), "sku": p1.sku, "variation_id": "v1"}
        ]
    else:
        assert "produtos_vinculados" not in f


@pytest.mark.parametrize("propria", [True, False])
async def test_catalogo_de_outra_loja_nao_recebe_identidade_como_se_fosse_ficha_propria(
    db, make_user, propria
):
    user = await make_user()
    loja = await _integ(db, user, IntegrationPlatform.SHOPEE, "Vita")
    outra = await _integ(db, user, IntegrationPlatform.SHOPEE, "Kia")
    p = await _produto(db, user, "a020.pi")
    await _vinculo(db, loja, p, "111")
    await _listing(db, loja if propria else outra, "111")

    f = await ficha.ficha_do_anuncio(db, IntegrationPlatform.SHOPEE, "111", loja.id)

    assert f is not None
    assert bool(f.get("produtos_vinculados")) is propria


async def test_ficha_de_outro_anuncio_nao_recebe_identidade_propria(db, make_user, redis_ficha):
    user = await make_user()
    loja = await _integ(db, user, IntegrationPlatform.TIKTOK, "ATV")
    outra = await _integ(db, user, IntegrationPlatform.SHOPEE, "Kia")
    p = await _produto(db, user, "a020.pi")
    await _vinculo(db, loja, p, "111")
    await _vinculo(db, outra, p, "222")
    await _listing(db, outra, "222")

    f = await ficha.ficha_do_anuncio(db, IntegrationPlatform.TIKTOK, "111", loja.id)

    assert f is not None and f["fonte"].startswith(ficha.FONTE_OUTRO_ANUNCIO)
    assert "produtos_vinculados" not in f


async def test_conjunto_grande_nao_omite_variacoes_para_parecer_todo_aprovado(
    db, make_user, redis_ficha, monkeypatch
):
    user = await make_user()
    loja = await _integ(db, user, IntegrationPlatform.SHOPEE, "Vortan")
    for i in range(3):
        p = await _produto(db, user, f"sku-{i}")
        await _vinculo(db, loja, p, "111", variacao=f"v{i}")
    monkeypatch.setattr(ficha, "MAX_PRODUTOS_VINCULADOS", 2)
    _fresca(redis_ficha, loja)

    f = await ficha.ficha_do_anuncio(db, IntegrationPlatform.SHOPEE, "111", loja.id)

    assert f["descricao"] == "Caixa em alumínio"
    assert "produtos_vinculados" not in f


def test_cartao_atual_nao_herda_variacao_de_outro_card_ou_pedido_mais_antigo():
    conversa = AtendimentoConversa(
        plataforma="shopee",
        canal="chat",
        anuncio_id="111",
        dados={"pedido_mkt": {"itens": [{"item_id": "111", "model_id": "velha"}]}},
    )
    mensagens = [
        _m("cliente", [{"tipo": "produto", "item_id": "111", "model_id": "velha"}], 0),
        _m("cliente", [{"tipo": "produto", "item_id": "111"}], 1),
    ]

    assert ficha.anuncios_da_conversa(conversa, mensagens)[0].variation_id is None
    assert (
        ficha.anuncios_da_conversa(
            AtendimentoConversa(
                plataforma="tiktok",
                canal="chat",
                dados={"pedido_mkt": {"itens": [{"item_id": "222", "sku_id": "sku-atual"}]}},
            ),
            [],
        )[0].variation_id
        == "sku-atual"
    )
