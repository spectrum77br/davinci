"""O que as telas do Flex pedem à API (projeto Flex, etapa 4).

  • Logística › Flex › Anúncios Flex: `GET /api/flex/anuncios` com busca (id,
    família ou título), filtro do que a plataforma mostrou (`observado`), o
    motivo em português claro e o resumo do topo (sem os filtros).
  • Controle de Estoque › Pedidos: `flex` em cada linha — pedido em
    `flex_pedido` OU com `envio_flex` na Logística (ML/Shopee), as mesmas
    duas fontes do robô de prioridade.

Nada sai da máquina: só banco.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    BlingOrder,
    FlexAnuncioEstado,
    FlexConta,
    FlexPedido,
    Integration,
    IntegrationPlatform,
    Listing,
    ListingStatus,
    Logistica,
    Product,
    ProductLink,
    UserRole,
)
from app.security.cipher import encrypt_json
from app.services import flex_config


@pytest.fixture(autouse=True)
def _flex_visivel(monkeypatch):
    # Estes testes são do Flex em si; quem vê (`flex_usuarios`) tem teste
    # próprio em test_flex_visibilidade.py.
    monkeypatch.setattr(flex_config, "pode_ver", lambda user: True)


@pytest_asyncio.fixture
async def cena(db: AsyncSession, make_user, monkeypatch):
    cfg = get_settings()
    monkeypatch.setattr(cfg, "flex_modo", "observar")
    admin = await make_user(role=UserRole.ADMIN)
    conta = Integration(
        user_id=admin.id,
        platform=IntegrationPlatform.ML,
        name="vita",
        credentials=encrypt_json({"access_token": "t"}),
    )
    db.add(conta)
    await db.flush()
    monkeypatch.setattr(cfg, "flex_contas", str(conta.id))
    prod = Product(
        user_id=admin.id, sku="dg053.ci", name="Mala", stock=10, situacao="A", bling_product_id=123
    )
    db.add(prod)
    await db.flush()
    db.add(
        ProductLink(
            user_id=admin.id,
            product_id=prod.id,
            integration_id=conta.id,
            platform=IntegrationPlatform.ML,
            external_id="MLB1",
            stock=3,
            listing_title="Mala de bordo 10kg 100%",
        )
    )
    agora = datetime.now(UTC)
    # A conta tem o Flex saindo de São Bernardo do Campo (o local de fábrica):
    # o robô mexe nela, então os "ligados que deveriam desligar" contam.
    db.add(
        FlexConta(
            integration_id=conta.id,
            plataforma="ml",
            flex_ativo=True,
            status="in",
            lido_em=agora,
            origem_cep="09750000",
            origem_cidade="São Bernardo do Campo",
            origem_lida_em=agora,
        )
    )
    base = {"integration_id": conta.id, "plataforma": "ml", "tentativas": 0}
    db.add_all(
        [
            FlexAnuncioEstado(
                external_id="MLB1",
                desejado="ligado",
                observado="desligado",
                motivo="saldo Flex 5 em dg053.sp (liga com 3)",
                observado_em=agora,
                aguardando_aprovacao=True,
                saldo_sp=5,
                familias="dg053",
                **base,
            ),
            FlexAnuncioEstado(
                external_id="MLB2",
                desejado="desligado",
                observado="ligado",
                motivo="saldo Flex 0 em a017.sp abaixo de 1",
                observado_em=agora,
                aguardando_aprovacao=False,
                saldo_sp=0,
                familias="a017",
                **base,
            ),
            FlexAnuncioEstado(
                external_id="MLB3",
                desejado="inelegivel",
                observado=None,
                motivo="algo que o motor ainda não traduz",
                aguardando_aprovacao=False,
                **base,
            ),
        ]
    )
    await db.commit()
    return {"admin": admin, "conta_id": conta.id}


@pytest.mark.asyncio
async def test_anuncios_motivo_claro_e_resumo(client: AsyncClient, cena, auth_as: Callable):
    auth_as(cena["admin"])
    r = await client.get("/api/flex/anuncios")
    assert r.status_code == 200, r.text
    corpo = r.json()
    por_id = {i["external_id"]: i for i in corpo["itens"]}
    assert por_id["MLB1"]["motivo"] == "saldo Flex 5 em dg053.sp (liga com 3)"
    assert por_id["MLB1"]["motivo_claro"] == (
        "5 peças livres em São Bernardo do Campo (dg053.sp) — dá para ter Flex (o mínimo é 3)."
    )
    assert por_id["MLB2"]["motivo_claro"].startswith("Sem peça livre em São Bernardo")
    # Motivo sem tradução aparece como veio.
    assert por_id["MLB3"]["motivo_claro"] == "algo que o motor ainda não traduz"
    assert corpo["resumo"] == {
        "avaliados": 3,
        "ligados": 1,
        "aguardando": 1,
        "desligar": 1,
        "nao_lidos": 1,
        "sem_controle": 0,
    }
    # O resumo é de tudo, mesmo com a lista filtrada.
    r = await client.get("/api/flex/anuncios?aguardando=true")
    assert r.json()["total"] == 1
    assert r.json()["resumo"]["avaliados"] == 3


@pytest.mark.asyncio
async def test_anuncios_filtro_observado(client: AsyncClient, cena, auth_as: Callable):
    auth_as(cena["admin"])

    async def ids(q: str) -> list[str]:
        r = await client.get(f"/api/flex/anuncios?{q}")
        assert r.status_code == 200, r.text
        return sorted(i["external_id"] for i in r.json()["itens"])

    assert await ids("observado=ligado") == ["MLB2"]
    assert await ids("observado=desligado") == ["MLB1"]
    assert await ids("observado=nao_lido") == ["MLB3"]
    # "Ligados que deveriam desligar" (o número do resumo).
    assert await ids("desligar=true") == ["MLB2"]
    assert (await client.get("/api/flex/anuncios?observado=talvez")).status_code == 422


@pytest.mark.asyncio
async def test_anuncios_busca(client: AsyncClient, cena, auth_as: Callable):
    auth_as(cena["admin"])

    async def ids(busca: str) -> list[str]:
        r = await client.get("/api/flex/anuncios", params={"busca": busca})
        assert r.status_code == 200, r.text
        return sorted(i["external_id"] for i in r.json()["itens"])

    assert await ids("mlb2") == ["MLB2"]  # id do anúncio, sem maiúscula
    assert await ids("A017") == ["MLB2"]  # família
    assert await ids("bordo") == ["MLB1"]  # título do vínculo
    assert await ids("  ") == ["MLB1", "MLB2", "MLB3"]  # vazio = sem filtro
    # `%` e `_` digitados são letras, não curinga.
    assert await ids("100%") == ["MLB1"]
    assert await ids("%") == ["MLB1"]
    assert await ids("_") == []


@pytest.mark.asyncio
async def test_anuncio_so_importado_mostra_o_titulo_e_e_achado_pela_busca(
    client: AsyncClient, cena, db: AsyncSession, auth_as: Callable
):
    """Achado da revisão: o anúncio só importado (sem vínculo — o motor o
    desliga) vinha sem título e a busca pelo nome não o achava. Na Shopee a
    importação grava `item_model`: o título vale para o anúncio (a 1ª parte)."""
    conta = cena["conta_id"]
    admin = cena["admin"]
    loja = Integration(
        user_id=admin.id,
        platform=IntegrationPlatform.SHOPEE,
        name="loja",
        credentials=encrypt_json({"access_token": "t"}),
    )
    db.add(loja)
    await db.flush()
    loja_id = loja.id
    base = {
        "tentativas": 0,
        "aguardando_aprovacao": False,
        "desejado": "inelegivel",
        "motivo": "anúncio sem vínculo vivo com produto do DaVinci",
    }
    db.add_all(
        [
            Listing(
                user_id=admin.id,
                integration_id=conta,
                platform=IntegrationPlatform.ML,
                external_id="MLB4",
                title="Mochila Executiva Importada",
                status=ListingStatus.PAUSED,
            ),
            Listing(
                user_id=admin.id,
                integration_id=loja_id,
                platform=IntegrationPlatform.SHOPEE,
                external_id="777_55",
                title="Bolsa da Shopee",
            ),
            FlexAnuncioEstado(
                integration_id=conta,
                external_id="MLB4",
                plataforma="ml",
                status_anuncio="paused",
                **base,
            ),
            FlexAnuncioEstado(
                integration_id=loja_id, external_id="777", plataforma="shopee", **base
            ),
        ]
    )
    await db.commit()
    auth_as(admin)
    itens = {i["external_id"]: i for i in (await client.get("/api/flex/anuncios")).json()["itens"]}
    assert itens["MLB4"]["titulo"] == "Mochila Executiva Importada"
    assert itens["MLB4"]["status_anuncio"] == "paused"
    assert itens["777"]["titulo"] == "Bolsa da Shopee"
    assert itens["MLB1"]["titulo"] == "Mala de bordo 10kg 100%"  # o do vínculo continua

    async def ids(busca: str) -> list[str]:
        r = await client.get("/api/flex/anuncios", params={"busca": busca})
        return sorted(i["external_id"] for i in r.json()["itens"])

    assert await ids("mochila") == ["MLB4"]
    assert await ids("bolsa da") == ["777"]


# ---- Controle de Estoque › Pedidos: o selo "Flex" ---------------------------------


@pytest.mark.asyncio
async def test_estoque_pedidos_marca_flex(
    client: AsyncClient, db: AsyncSession, make_user, auth_as: Callable
):
    admin = await make_user(role=UserRole.ADMIN)
    d = date(2026, 5, 28)

    def pedido(bid: int, item: int = 0) -> BlingOrder:
        return BlingOrder(
            bling_id=bid,
            numero=str(bid),
            item_codigo=f"sku-{bid}-{item}",
            item_index=item,
            situacao="15",
            em_andamento_data=d,
        )

    db.add_all(
        [
            # Flex pelo shipment check (flex_pedido) — as DUAS linhas do pedido.
            pedido(930001),
            pedido(930001, 1),
            # Flex pela Logística (enriquecimento do ML).
            pedido(930002),
            # Comum: Logística lida e não é Flex.
            pedido(930003),
            # Marca Flex numa plataforma sem Flex: não vale.
            pedido(930004),
            # Sem nada.
            pedido(930005),
            FlexPedido(bling_id=930001, plataforma="shopee", envio_tipo="90022"),
            Logistica(
                pedido_bling="930002",
                plataforma="Mercado Livre",
                meli_status={},
                envio_flex=True,
                envio_tipo="self_service",
            ),
            Logistica(
                pedido_bling="930003",
                plataforma="Mercado Livre",
                meli_status={},
                envio_flex=False,
                envio_tipo="cross_docking",
            ),
            Logistica(pedido_bling="930004", plataforma="Amazon", meli_status={}, envio_flex=True),
        ]
    )
    await db.commit()
    auth_as(admin)
    r = await client.get("/api/estoque/pedidos?data_inicio=2026-05-28&data_fim=2026-05-28")
    assert r.status_code == 200, r.text
    flex: dict[str, set[bool]] = {}
    for linha in r.json()["data"]:
        flex.setdefault(linha["pedido_bling"], set()).add(linha["flex"])
    assert flex == {
        "930001": {True},
        "930002": {True},
        "930003": {False},
        "930004": {False},
        "930005": {False},
    }


@pytest.mark.asyncio
async def test_pedidos_sem_sp_so_abertos(
    client: AsyncClient, db: AsyncSession, make_user, auth_as: Callable
):
    """A lista "Pedidos Flex sem peça em SP" da tela pede `abertos=true`: o
    pedido que já saiu (15 = em andamento), foi atendido ou cancelado não
    espera mais ninguém; o que o espelho ainda não tem continua."""
    admin = await make_user(role=UserRole.ADMIN)
    agora = datetime.now(UTC)
    db.add_all(
        [
            BlingOrder(bling_id=940001, numero="940001", item_index=0, situacao="6", data=agora),
            BlingOrder(bling_id=940002, numero="940002", item_index=0, situacao="15", data=agora),
            BlingOrder(bling_id=940003, numero="940003", item_index=0, situacao="12", data=agora),
        ]
        + [
            FlexPedido(bling_id=b, plataforma="ml", alerta="o .sp não cobre")
            for b in (940001, 940002, 940003, 940004)
        ]
    )
    await db.commit()
    auth_as(admin)

    async def ids(q: str) -> list[int]:
        r = await client.get(f"/api/flex/pedidos?{q}")
        assert r.status_code == 200, r.text
        return sorted(p["bling_id"] for p in r.json())

    assert await ids("so_alerta=true") == [940001, 940002, 940003, 940004]
    assert await ids("so_alerta=true&abertos=true") == [940001, 940004]
