"""O "Ver anúncio" do cabeçalho da conversa (03/11/2026, `anuncio_publico`).

O que estes testes seguram:

- o link é a página PÚBLICA de cada plataforma (Shopee com o shop_id da
  conta, ML pelo permalink ou MLB-<n>, TikTok, Amazon pelo ASIN), só com id
  limpo; o link que a loja deu só vale se for https do domínio dela;
- a ordem: o pedido (o retrato, o retrato do chat do mesmo pedido, o vínculo
  sem dúvida do SKU do Bling) → o último cartão do COMPRADOR → o anúncio da
  conversa (a pergunta do ML);
- sem anúncio (ou sem como montar o link), nenhum item e o porquê;
- o bloco vem no GET /painel.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import (
    AtendimentoCanal,
    AtendimentoMensagem,
    Integration,
    IntegrationPlatform,
    Listing,
    Product,
    ProductLink,
    UserRole,
)
from app.security.cipher import encrypt_json
from app.services.atendimento import anuncio_publico as ap
from app.services.atendimento import gravar

URL = "/api/atendimento"
AGORA = datetime.now(UTC)
SHOP_ID = 1143525481


@pytest.fixture
async def admin(make_user, auth_as):
    u = await make_user(role=UserRole.ADMIN, email=f"adm-{uuid4().hex[:6]}@davinci-test.com")
    auth_as(u)
    return u


async def _loja(db, dono, plataforma=IntegrationPlatform.SHOPEE, canal="chat", creds=None):
    integ = Integration(
        user_id=dono.id,
        platform=plataforma,
        name=f"loja-{uuid4().hex[:4]}",
        credentials=encrypt_json(creds if creds is not None else {"access_token": "t"}),
    )
    db.add(integ)
    await db.flush()
    c = AtendimentoCanal(
        integration_id=integ.id,
        plataforma=plataforma.value,
        canal=canal,
        modo="observar",
        status="ok",
    )
    db.add(c)
    await db.commit()
    return integ, c


async def _conversa(db, integ, canal, *, pedido=None, externo=None, dados=None, anuncio=None):
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=integ,
        plataforma=canal.plataforma,
        canal_nome=canal.canal,
        externo_id=externo or f"conv-{uuid4().hex[:6]}",
        pedido_marketplace=pedido,
        comprador_nome="Comprador",
        dados=dados,
        anuncio_id=anuncio,
    )
    await db.commit()
    return conversa


async def _cartao(db, conversa, item_id, *, autor="cliente", link=None, titulo=None, minutos=0):
    db.add(
        AtendimentoMensagem(
            conversa_id=conversa.id,
            externo_id=f"m-{uuid4().hex[:8]}",
            autor=autor,
            origem="cliente" if autor == "cliente" else "externo",
            tipo="produto",
            anexos=[{"tipo": "produto", "item_id": item_id, "titulo": titulo, "link": link}],
            enviada_em=AGORA - timedelta(hours=1) + timedelta(minutes=minutos),
        )
    )
    await db.commit()


async def _vinculo(db, dono, integ, sku, external_id, *, variation_id=None, titulo=None):
    pid = await db.scalar(select(Product.id).where(Product.user_id == dono.id, Product.sku == sku))
    if pid is None:
        prod = Product(user_id=dono.id, sku=sku, name=f"Nome {sku}")
        db.add(prod)
        await db.flush()
        pid = prod.id
    db.add(
        ProductLink(
            user_id=dono.id,
            product_id=pid,
            integration_id=integ.id,
            platform=integ.platform,
            external_id=external_id,
            variation_id=variation_id,
            listing_title=titulo,
        )
    )
    await db.commit()


# ─────────────── o link ───────────────


def test_link_montado_por_plataforma():
    assert (
        ap.link_montado("shopee", "22693481538", shop_id=SHOP_ID)
        == f"https://shopee.com.br/product/{SHOP_ID}/22693481538"
    )
    assert ap.link_montado("shopee", "22693481538") is None  # sem a loja, não monta
    assert ap.link_montado("shopee", "2269/../x", shop_id=SHOP_ID) is None
    assert (
        ap.link_montado("ml", "MLB7297088634")
        == "https://produto.mercadolivre.com.br/MLB-7297088634"
    )
    assert ap.link_montado("ml", "mlb-7297088634").endswith("/MLB-7297088634")
    assert ap.link_montado("ml", "MLA7297088634") is None
    assert (
        ap.link_montado("tiktok", "1737088026925434582")
        == "https://shop.tiktok.com/view/product/1737088026925434582?region=BR&locale=pt-BR"
    )
    assert ap.link_montado("amazon", "b0gkhfk8f1") == "https://www.amazon.com.br/dp/B0GKHFK8F1"
    assert ap.link_montado("amazon", "a006") is None  # SKU do vendedor não é ASIN
    assert ap.link_montado("magalu", "a006-2") is None
    assert ap.link_montado("temu", "123456789") is None


def test_link_da_loja_so_https_do_dominio_dela():
    ml = "https://produto.mercadolivre.com.br/MLB-7721297252-fossibot-_JM"
    assert ap.link_da_loja("ml", ml) == ml
    assert ap.link_da_loja("shopee", ml) is None  # outra plataforma
    assert ap.link_da_loja("ml", "http://produto.mercadolivre.com.br/MLB-1") is None
    assert ap.link_da_loja("ml", "javascript:alert(1)") is None
    assert ap.link_da_loja("ml", "https://mercadolivre.com.br.golpe.com/MLB-1") is None
    assert ap.link_da_loja("ml", "https://x@produto.mercadolivre.com.br/MLB-1") is None
    assert ap.link_da_loja("magalu", "https://www.magazineluiza.com.br/p/abc/") is not None
    assert ap.link_da_loja("site", "https://loja.com.br/p") is None


# ─────────────── a ordem das fontes ───────────────


async def test_pedido_com_varios_itens_vira_o_menu_pela_rota(db, client, admin, make_user):
    dono = await make_user()
    integ, canal = await _loja(db, dono, creds={"access_token": "t", "shop_id": SHOP_ID})
    conversa = await _conversa(
        db,
        integ,
        canal,
        pedido="251103AAA",
        dados={
            "pedido_mkt": {
                "itens": [
                    {"item_id": "22693481538", "titulo": "Uranyx A17", "variacao": "Preto"},
                    {"item_id": "58252691738", "titulo": "Uranyx A17 Premium"},
                    {"item_id": "22693481538", "titulo": "Uranyx A17", "variacao": "Azul"},
                    {"titulo": "sem anúncio"},
                ]
            }
        },
    )
    # O cartão do comprador perde para o pedido.
    await _cartao(db, conversa, "11111111111")
    r = await client.get(f"{URL}/conversas/{conversa.id}/painel")
    assert r.status_code == 200, r.text
    a = r.json()["anuncios"]
    assert a["motivo"] is None
    assert [(i["item_id"], i["variacao"], i["origem"]) for i in a["itens"]] == [
        ("22693481538", "Preto", "pedido"),
        ("58252691738", None, "pedido"),
    ]
    assert a["itens"][0]["link"] == f"https://shopee.com.br/product/{SHOP_ID}/22693481538"
    assert a["itens"][0]["titulo"] == "Uranyx A17"


async def test_shopee_sem_shop_id_nao_monta(db, make_user):
    dono = await make_user()
    integ, canal = await _loja(db, dono)
    conversa = await _conversa(
        db,
        integ,
        canal,
        pedido="251103BBB",
        dados={"pedido_mkt": {"itens": [{"item_id": "22693481538"}]}},
    )
    r = await ap.anuncios_da_conversa(db, conversa)
    assert r["itens"] == [] and r["codigo"] == ap.SEM_LINK
    assert "id da loja" in r["motivo"]


async def test_conversa_de_email_usa_o_retrato_do_chat_do_pedido(db, make_user):
    dono = await make_user()
    integ, chat = await _loja(db, dono, creds={"shop_id": SHOP_ID})
    email = AtendimentoCanal(
        integration_id=integ.id, plataforma="shopee", canal="email", modo="observar", status="ok"
    )
    db.add(email)
    await db.commit()
    await _conversa(
        db,
        integ,
        chat,
        pedido="251103CCC",
        dados={"pedido_mkt": {"itens": [{"item_id": "22693481538"}]}},
    )
    conv_email = await _conversa(db, integ, email, pedido="251103CCC")
    r = await ap.anuncios_da_conversa(db, conv_email)
    assert [i["link"] for i in r["itens"]] == [
        f"https://shopee.com.br/product/{SHOP_ID}/22693481538"
    ]


async def test_ml_usa_o_permalink_do_catalogo_e_senao_monta(db, make_user):
    dono = await make_user()
    integ, canal = await _loja(db, dono, IntegrationPlatform.ML, canal="pos_venda")
    permalink = "https://produto.mercadolivre.com.br/MLB-7297088634-cappa-uranyx-_JM"
    db.add(
        Listing(
            user_id=dono.id,
            integration_id=integ.id,
            platform=IntegrationPlatform.ML,
            external_id="MLB7297088634",
            title="Cappa",
            raw_data={"permalink": permalink},
        )
    )
    await db.commit()
    conversa = await _conversa(
        db,
        integ,
        canal,
        pedido="2000012345",
        dados={
            "pedido_mkt": {"itens": [{"item_id": "MLB7297088634"}, {"item_id": "MLB4625288033"}]}
        },
    )
    r = await ap.anuncios_da_conversa(db, conversa)
    assert [i["link"] for i in r["itens"]] == [
        permalink,
        "https://produto.mercadolivre.com.br/MLB-4625288033",
    ]


async def test_pergunta_do_ml_abre_o_anuncio_da_pergunta(db, make_user):
    dono = await make_user()
    integ, canal = await _loja(db, dono, IntegrationPlatform.ML, canal="pergunta")
    link = "https://produto.mercadolivre.com.br/MLB-7721297252-fossibot-f105-_JM"
    conversa = await _conversa(
        db,
        integ,
        canal,
        anuncio="MLB7721297252",
        dados={
            "produto": {
                "tipo": "produto",
                "item_id": "MLB7721297252",
                "titulo": "Fossibot F105",
                "link": link,
            }
        },
    )
    r = await ap.anuncios_da_conversa(db, conversa)
    assert r["itens"] == [
        {
            "item_id": "MLB7721297252",
            "titulo": "Fossibot F105",
            "variacao": None,
            "link": link,
            "origem": "conversa",
        }
    ]


async def test_ultimo_cartao_do_comprador_e_link_estranho_refeito(db, make_user):
    dono = await make_user()
    integ, canal = await _loja(db, dono, creds={"shop_id": SHOP_ID})
    conversa = await _conversa(db, integ, canal, anuncio="33333333333")
    await _cartao(db, conversa, "22222222222", minutos=1)
    # O mais novo do comprador, com um link de fora: vale o montado.
    await _cartao(db, conversa, "44444444444", link="https://golpe.com/x", minutos=5)
    # O da loja (mais novo ainda) não conta.
    await _cartao(db, conversa, "55555555555", autor="loja", minutos=9)
    r = await ap.anuncios_da_conversa(db, conversa)
    assert [(i["item_id"], i["origem"], i["link"]) for i in r["itens"]] == [
        ("44444444444", "chat", f"https://shopee.com.br/product/{SHOP_ID}/44444444444")
    ]


async def test_tiktok_pelo_vinculo_sem_duvida_do_sku(db, make_user):
    dono = await make_user()
    integ, canal = await _loja(db, dono, IntegrationPlatform.TIKTOK)
    conversa = await _conversa(db, integ, canal, pedido="577000111")
    await _vinculo(db, dono, integ, "b053", "1734101310989173791", titulo="Mala B053")
    # Dois anúncios vivos do mesmo produto na loja: não se sabe qual — fica de fora.
    await _vinculo(db, dono, integ, "b075", "1737420500780746116")
    await _vinculo(db, dono, integ, "b075", "1737420500780746999")
    r = await ap.anuncios_da_conversa(db, conversa, skus_bling=["B053", "b075"])
    assert r["itens"] == [
        {
            "item_id": "1734101310989173791",
            "titulo": "Mala B053",
            "variacao": None,
            "link": "https://shop.tiktok.com/view/product/1734101310989173791?region=BR&locale=pt-BR",
            "origem": "pedido",
        }
    ]
    # Só o ambíguo: cai no cartão do comprador.
    await _cartao(db, conversa, "1737088026925434582")
    r = await ap.anuncios_da_conversa(db, conversa, skus_bling=["b075"])
    assert [(i["item_id"], i["origem"]) for i in r["itens"]] == [("1737088026925434582", "chat")]


async def test_amazon_pelo_asin_do_vinculo(db, make_user):
    dono = await make_user()
    integ, canal = await _loja(db, dono, IntegrationPlatform.AMAZON, canal="email")
    conversa = await _conversa(db, integ, canal, pedido="702-8099058-8598622")
    await _vinculo(db, dono, integ, "b052.12.24", "b052.12.24", variation_id="B0GZ4MTZD5")
    r = await ap.anuncios_da_conversa(db, conversa, skus_bling=["b052.12.24"])
    assert [i["link"] for i in r["itens"]] == ["https://www.amazon.com.br/dp/B0GZ4MTZD5"]


async def test_sem_anuncio_diz_o_porque(db, make_user):
    dono = await make_user()
    integ, canal = await _loja(db, dono)
    conversa = await _conversa(db, integ, canal, pedido="251103DDD")
    r = await ap.anuncios_da_conversa(db, conversa)
    assert r["itens"] == [] and r["codigo"] == ap.SEM_ANUNCIO and r["motivo"]

    integ_m, canal_m = await _loja(db, dono, IntegrationPlatform.MAGALU, canal="sac")
    conversa_m = await _conversa(db, integ_m, canal_m, anuncio="b049-8-12-20-24")
    r = await ap.anuncios_da_conversa(db, conversa_m)
    assert r["itens"] == [] and r["codigo"] == ap.SEM_LINK
    assert "Magalu" in r["motivo"]
