"""Botão Servidor do histórico do chamado (09/11/2026).

GET /api/chamados/{id}/adspower diz QUAL perfil do AdsPower abre a loja do
chamado (campo Servidor da loja = `store_info.server`). Quem abre é o navegador
de quem clicou, no AdsPower do computador dele. A loja sai pela régua da cerca
da equipe: com pedido no espelho, a loja do Bling do pedido manda; sem pedido
no espelho, a conta da linha (plataforma + nome).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, Chamado, ChamadoPedido, StoreInfo, User, UserRole, UserStatus

pytestmark = pytest.mark.asyncio

PERM = {"chamados": {"view": True, "edit": True, "delete": True}}


async def _user(db: AsyncSession, nome: str, *, role=UserRole.USER, equipes=None) -> User:
    email = f"{nome}-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(
        open_id=f"email:{email}",
        email=email,
        name=nome,
        role=role,
        status=UserStatus.ACTIVE,
        permissions=PERM,
        sales_teams=equipes,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _pedido(numero: str, loja: str) -> BlingOrder:
    return BlingOrder(
        bling_id=int(numero),
        numero=numero,
        numeroloja=f"MK{numero}",
        loja=loja,
        situacao="83960",
        item_index=0,
        item_codigo=f"sku{numero}",
        item_descricao=f"produto {numero}",
        item_quantidade=1,
        data=datetime.now(UTC) - timedelta(days=2),
    )


def _chamado(pedido: str | None, plataforma: str | None, conta: str | None) -> Chamado:
    return Chamado(
        data=datetime.now(UTC).date(),
        pedido_bling=pedido,
        pedido_marketplace=f"MK{pedido}" if pedido else "MKSEMPEDIDO",
        plataforma=plataforma,
        conta=conta,
        origem="devolucao",
        canal="manual",
    )


@pytest_asyncio.fixture
async def cenario(db: AsyncSession) -> dict:
    admin = await _user(db, "admin", role=UserRole.ADMIN)
    # (plataforma, conta, loja do Bling, equipe, Servidor)
    for plataforma, conta, loja, equipe, servidor in [
        ("ml", "aguiar", "8821", 101, "136"),
        ("ml", "inova", "8823", 101, "72"),
        ("shopee", "inova", "8822", 102, "88"),
        ("shopee", "vortan", "8824", 102, " 50 "),
        ("tiktok", "mini", "8825", 101, None),
    ]:
        db.add(
            StoreInfo(
                user_id=admin.id,
                platform=plataforma,
                account_name=conta,
                bling_store_id=loja,
                sales_team=equipe,
                server=servidor,
            )
        )
    db.add_all(
        [
            _pedido("877001", "8821"),
            _pedido("877002", "8822"),
            _pedido("877003", "8825"),
            _pedido("877009", "8829"),  # loja do Bling sem cadastro
        ]
    )
    linhas = {
        # pedido da ML aguiar; a conta digitada (Shopee vortan) não manda
        "pedido": _chamado("877001", "shopee", "vortan"),
        "sem_servidor": _chamado("877003", "tiktok", "mini"),
        "loja_sem_cadastro": _chamado("877009", "amazon", "zeta"),
        # sem pedido no espelho: a conta com a plataforma na frente
        "conta": _chamado(None, None, "Shopee Vortan"),
        # sem plataforma e nome repetido em duas lojas: ninguém adivinha
        "nome_repetido": _chamado("877090", None, "inova"),
        # lote com pedidos de duas lojas, Servidores diferentes
        "lote": _chamado("877091", "ml", "aguiar"),
    }
    db.add_all(linhas.values())
    await db.flush()
    db.add_all(
        [
            ChamadoPedido(chamado_id=linhas["lote"].id, pedido_bling="877001"),
            ChamadoPedido(chamado_id=linhas["lote"].id, pedido_bling="877002"),
        ]
    )
    await db.commit()
    return {
        "ids": {k: v.id for k, v in linhas.items()},
        "admin": admin,
        "israel": await _user(db, "israel", equipes=[102]),
    }


async def _perfil(client: AsyncClient, chamado_id) -> dict:
    r = await client.get(f"/api/chamados/{chamado_id}/adspower")
    assert r.status_code == 200, r.text
    return r.json()


async def test_perfil_pela_loja_do_pedido(
    client: AsyncClient, auth_as: Callable[[User | None], None], cenario: dict
):
    auth_as(cenario["admin"])
    p = await _perfil(client, cenario["ids"]["pedido"])
    assert p["perfil"] == "136" and p["fonte"] == "pedido" and p["codigo"] is None
    assert p["store_info_id"] and p["motivo"] is None and p["robo"] is False
    # Sem link guardado nem nº da devolução: a página do pedido buscando o nº.
    assert p["destino_tipo"] == "pedido" and p["destino"].endswith("order?search=MK877001")


async def test_perfil_pela_conta_sem_pedido_no_espelho(
    client: AsyncClient, auth_as: Callable[[User | None], None], cenario: dict
):
    auth_as(cenario["admin"])
    p = await _perfil(client, cenario["ids"]["conta"])
    assert p["perfil"] == "50" and p["fonte"] == "conta"


async def test_sem_perfil_diz_o_porque(
    client: AsyncClient, auth_as: Callable[[User | None], None], cenario: dict
):
    auth_as(cenario["admin"])
    esperado = {
        "sem_servidor": "sem_perfil",
        "loja_sem_cadastro": "sem_cadastro",
        "nome_repetido": "sem_cadastro",
        "lote": "ambiguo",
    }
    for chave, codigo in esperado.items():
        p = await _perfil(client, cenario["ids"][chave])
        assert p["perfil"] is None, chave
        assert p["codigo"] == codigo, (chave, p)
        assert p["motivo"], chave


async def test_chamado_de_outra_equipe_da_403(
    client: AsyncClient, auth_as: Callable[[User | None], None], cenario: dict
):
    auth_as(cenario["israel"])
    r = await client.get(f"/api/chamados/{cenario['ids']['pedido']}/adspower")
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "pedido_de_outra_equipe"
    # A conta "Shopee Vortan" (sem pedido) é de Israel: abre.
    assert (await _perfil(client, cenario["ids"]["conta"]))["perfil"] == "50"


# ─── A página que o botão abre (09/11/2026, 301908) ──────────────────────


def _ch(**kw) -> Chamado:
    base = {"origem": "devolucao", "canal": "manual", "chamado_de_tela": False}
    base.update(kw)
    return Chamado(**base)


def test_destino_usa_o_link_guardado_e_monta_quando_falta():
    from app.services.chamados_adspower import destino_do_chamado

    # O link guardado manda.
    url = "https://www.mercadolivre.com.br/cases/detail/483241217"
    assert destino_do_chamado(_ch(plataforma="ml", chamado_url=url)) == (url, "chamado")
    # 301908: Shopee com o nº da devolução → a lista de devoluções buscando ele.
    d, t = destino_do_chamado(
        _ch(plataforma="shopee", chamado="2610260N6GMARW6", pedido_marketplace="26100719629GUA")
    )
    assert t == "devolução"
    assert d == (
        "https://seller.shopee.com.br/portal/sale/returnrefundcancel"
        "?keyword=2610260N6GMARW6&keywordType=return_sn"
    )
    # Shopee sem o nº da devolução → a lista de pedidos buscando o pedido.
    d, t = destino_do_chamado(_ch(plataforma="Shopee", origem="logistica", pedido_marketplace="2610204DDTYU1Y"))
    assert (d, t) == ("https://seller.shopee.com.br/portal/sale/order?search=2610204DDTYU1Y", "pedido")
    # TikTok e ML → a página do pedido; Amazon → o pedido no Seller Central.
    assert destino_do_chamado(
        _ch(plataforma="tiktok", chamado="404283897103", pedido_marketplace="586792101607277770")
    ) == ("https://seller-br.tiktok.com/order/detail?order_no=586792101607277770", "pedido")
    assert destino_do_chamado(
        _ch(plataforma="Mercado Livre", chamado="5601924125", pedido_marketplace="2000015300605585")
    ) == ("https://www.mercadolivre.com.br/vendas/2000015300605585/detalhe", "pedido")
    assert destino_do_chamado(
        _ch(plataforma="amazon", pedido_marketplace="702-4461180-4591462")
    ) == ("https://sellercentral.amazon.com.br/orders-v3/order/702-4461180-4591462", "pedido")
    # Caso do suporte da Amazon (nº de 11 dígitos) → a página do caso.
    assert destino_do_chamado(_ch(plataforma="amazon", chamado="23057891781"))[1] == "caso"
    # Nada pra montar (ou nº com caractere estranho): None.
    assert destino_do_chamado(_ch(plataforma="shopee")) == (None, None)
    assert destino_do_chamado(_ch(plataforma="tiktok", pedido_marketplace="58 67/../x")) == (None, None)
