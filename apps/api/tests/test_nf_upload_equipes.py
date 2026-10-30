"""Controle de Estoque › Upload NF × equipe de vendas (30/10/2026).

Eduardo, 30/10: três equipes — 101 Londres, 102 Israel, 103 Marrocos — e quem
é de uma equipe só enxerga as lojas dela. Na aba Upload NF (XML → Mercado
Livre) a cerca é a integração ML das lojas da equipe: GET /api/nf/stores só
lista as dela, e POST /api/nf/upload e /upload-multiple recusam (403
`loja_de_outra_equipe`) loja de fora ANTES de chamar o ML — o XML nunca sai
pelo token de loja de outra equipe. Admin e quem não tem equipe (o galpão) não
mudam. Equipe sem loja cadastrada não vê loja nenhuma (nunca "todas").

O ML é de mentira (`ml_falso`): cada token usado fica gravado, e nenhum teste
chega à api.mercadolibre.com.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Integration,
    IntegrationPlatform,
    StoreInfo,
    User,
    UserRole,
    UserStatus,
)
from app.routers import nf_upload
from app.security.cipher import encrypt_json

pytestmark = pytest.mark.asyncio

PERM = {"controle_estoque": {"view": True, "edit": True, "delete": False}}
PEDIDO_ML = "2000001234567890"
XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe"><NFe><infNFe><infAdic>'
    f"<infCpl>Venda pela internet. Fonte IBPT. {PEDIDO_ML}</infCpl>"
    "</infAdic></infNFe></NFe></nfeProc>"
).encode()


async def _user(
    db: AsyncSession,
    nome: str,
    *,
    role: UserRole = UserRole.USER,
    equipes: list[int] | None = None,
) -> User:
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


async def _integracao(
    db: AsyncSession,
    dono: User,
    nome: str,
    token: str,
    *,
    platform: IntegrationPlatform = IntegrationPlatform.ML,
    status: str = "active",
) -> Integration:
    integ = Integration(
        user_id=dono.id,
        platform=platform,
        name=nome,
        credentials=encrypt_json({"access_token": token}),
        status=status,
    )
    db.add(integ)
    await db.commit()
    await db.refresh(integ)
    return integ


@pytest_asyncio.fixture
async def cenario(db: AsyncSession) -> dict[str, User]:
    """Lojas ML: aurora (Londres), bravo (Israel), cosmo (sem cadastro em
    Lojas: não é de equipe nenhuma), delta (Londres, integração inativa) e
    DUAS "inova" — uma da Londres e outra da Israel, cada cadastro com a sua
    integração pelo FK (o nome sozinho não diz a equipe). E a Shopee bravo,
    da Londres: o mesmo nome da ML bravo da Israel em outra plataforma (como
    inova/barbosa/mega/eron na produção) — não abre a ML bravo pra Londres."""
    admin = await _user(db, "admin", role=UserRole.ADMIN)
    await _integracao(db, admin, "aurora", "tok-aurora")
    await _integracao(db, admin, "bravo", "tok-bravo")
    await _integracao(db, admin, "cosmo", "tok-cosmo")
    delta = await _integracao(db, admin, "delta", "tok-delta", status="inactive")
    inova_londres = await _integracao(db, admin, "inova", "tok-inova-londres")
    inova_israel = await _integracao(db, admin, "inova", "tok-inova-israel")
    await _integracao(db, admin, "bravo", "tok-shopee-bravo", platform=IntegrationPlatform.SHOPEE)
    lojas = (
        # (plataforma, conta, equipe, integração pelo FK — None = pelo par
        # plataforma + nome, como a maioria das lojas na produção)
        ("ml", "aurora", 101, None),
        ("mercadolivre", "bravo", 102, None),
        ("shopee", "bravo", 101, None),
        ("ml", "delta", 101, delta),
        ("ml", "inova", 101, inova_londres),
        ("ml", "inova", 102, inova_israel),
    )
    for plataforma, conta, equipe, integ in lojas:
        db.add(
            StoreInfo(
                user_id=admin.id,
                platform=plataforma,
                account_name=conta,
                sales_team=equipe,
                integration_id=integ.id if integ else None,
            )
        )
    await db.commit()
    return {
        "admin": admin,
        "londres": await _user(db, "londres", equipes=[101]),
        "israel": await _user(db, "israel", equipes=[102]),
        # Galpão: sem equipe, vê todas as lojas como sempre.
        "galpao": await _user(db, "azeroth"),
        # Equipe sem nenhuma loja cadastrada: não vê loja nenhuma.
        "sem_loja": await _user(db, "sem-loja", equipes=[104]),
    }


class _SemRede:
    """No lugar do `httpx` do router: chamar o ML de verdade quebra o teste."""

    class AsyncClient:
        def __init__(self, *a: Any, **k: Any) -> None:
            raise AssertionError("o teste tentou chamar o Mercado Livre de verdade")


@pytest.fixture
def ml_falso(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """O ML de mentira: grava o token de cada consulta e de cada envio. Só o
    token `dono` tem o pedido (os outros respondem "não é desta loja")."""
    estado: dict[str, Any] = {"dono": None, "consultas": [], "envios": []}

    async def _resolve(access_token: str, order_id: str) -> tuple[str | None, str]:
        assert order_id == PEDIDO_ML
        estado["consultas"].append(access_token)
        if access_token == estado["dono"]:
            return "SHIP-1", ""
        return None, nf_upload._ERR_OUTRA_LOJA

    async def _post(access_token: str, shipping_id: str, xml_bytes: bytes) -> tuple[bool, str]:
        assert shipping_id == "SHIP-1" and xml_bytes == XML
        estado["envios"].append(access_token)
        return True, ""

    monkeypatch.setattr(nf_upload, "httpx", _SemRede)
    monkeypatch.setattr(nf_upload, "_resolve_shipping_id", _resolve)
    monkeypatch.setattr(nf_upload, "_post_invoice", _post)
    return estado


async def _lojas(client: AsyncClient) -> dict[str, Any]:
    r = await client.get("/api/nf/stores")
    assert r.status_code == 200, r.text
    return r.json()


async def _subir(client: AsyncClient, lojas: list[str]):
    return await client.post(
        "/api/nf/upload",
        files={"file": ("nota.xml", XML, "application/xml")},
        data={"selected_stores": lojas},
    )


async def _subir_varios(client: AsyncClient, lojas: list[str]):
    return await client.post(
        "/api/nf/upload-multiple",
        files=[
            ("files", ("nota-1.xml", XML, "application/xml")),
            ("files", ("nota-2.xml", XML, "application/xml")),
        ],
        data={"selected_stores": lojas},
    )


# ─── Lista de lojas alvo ─────────────────────────────────────────────────


async def test_lista_so_traz_as_lojas_ml_da_equipe(
    client: AsyncClient, auth_as: Callable[[User | None], None], cenario: dict[str, User]
):
    auth_as(cenario["londres"])
    body = await _lojas(client)
    # A inova da Israel tem o mesmo nome, mas a cerca é pela integração: a
    # Londres vê UMA inova (a dela). Sem a delta (inativa), a cosmo (sem
    # equipe) e a bravo: a ML é da Israel, a Shopee da Londres não é do ML.
    assert body["stores"] == ["aurora", "inova"]
    assert body["equipe"] == {"numeros": [101], "nomes": ["Londres"]}

    auth_as(cenario["israel"])
    body = await _lojas(client)
    assert body["stores"] == ["bravo", "inova"]
    assert body["equipe"] == {"numeros": [102], "nomes": ["Israel"]}


async def test_admin_e_sem_equipe_seguem_vendo_todas_as_lojas(
    client: AsyncClient, auth_as: Callable[[User | None], None], cenario: dict[str, User]
):
    for quem in ("admin", "galpao"):
        auth_as(cenario[quem])
        body = await _lojas(client)
        assert sorted(body["stores"]) == ["aurora", "bravo", "cosmo", "inova", "inova"], quem
        assert body["equipe"] is None, quem


async def test_equipe_sem_loja_nao_ve_loja_nenhuma(
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
    cenario: dict[str, User],
    ml_falso: dict[str, Any],
):
    auth_as(cenario["sem_loja"])
    body = await _lojas(client)
    assert body["stores"] == []
    assert body["equipe"] == {"numeros": [104], "nomes": ["Equipe 104"]}

    # E nem mandando o nome na mão: vazio nunca vira "todas".
    r = await _subir(client, ["aurora"])
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == {"code": "loja_de_outra_equipe", "lojas": ["aurora"]}
    assert ml_falso["consultas"] == [] and ml_falso["envios"] == []


# ─── Envio ───────────────────────────────────────────────────────────────


async def test_envio_recusa_loja_de_outra_equipe_antes_de_chamar_o_ml(
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
    cenario: dict[str, User],
    ml_falso: dict[str, Any],
):
    ml_falso["dono"] = "tok-bravo"
    auth_as(cenario["londres"])

    # Uma loja de fora na seleção recusa o envio inteiro — nem a loja da
    # equipe é tentada, e a resposta diz qual loja barrou. A bravo da Shopee
    # é da Londres, mas a ML bravo não: o nome em outra plataforma não abre.
    r = await _subir(client, ["aurora", "bravo"])
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == {"code": "loja_de_outra_equipe", "lojas": ["bravo"]}

    # Loja sem cadastro em Lojas e nome que nem existe: não dá pra provar
    # que é da equipe, barra igual (e repetido aparece uma vez só).
    r = await _subir(client, ["cosmo", "nao-existe", "cosmo"])
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == {
        "code": "loja_de_outra_equipe",
        "lojas": ["cosmo", "nao-existe"],
    }

    # O lote também, antes de ler qualquer arquivo.
    r = await _subir_varios(client, ["bravo"])
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == {"code": "loja_de_outra_equipe", "lojas": ["bravo"]}

    # A Israel não usa a loja da Londres.
    auth_as(cenario["israel"])
    r = await _subir(client, ["aurora"])
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["lojas"] == ["aurora"]

    assert ml_falso["consultas"] == [] and ml_falso["envios"] == []


async def test_envio_da_equipe_so_usa_o_token_da_loja_dela(
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
    cenario: dict[str, User],
    ml_falso: dict[str, Any],
):
    auth_as(cenario["londres"])

    ml_falso["dono"] = "tok-aurora"
    r = await _subir(client, ["aurora", "inova"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True and body["store_name"] == "aurora"
    assert body["order_id"] == PEDIDO_ML
    assert ml_falso["envios"] == ["tok-aurora"]

    # "inova" com o mesmo nome nas duas equipes: só a integração da Londres é
    # tentada. O pedido da inova da Israel não sai pelo upload da Londres.
    ml_falso.update(dono="tok-inova-israel", consultas=[], envios=[])
    r = await _subir(client, ["inova"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is False
    assert [a["store"] for a in body["attempts_details"]] == ["inova"]
    assert ml_falso["consultas"] == ["tok-inova-londres"]
    assert ml_falso["envios"] == []

    ml_falso.update(dono="tok-inova-londres", consultas=[], envios=[])
    r = await _subir_varios(client, ["aurora", "inova"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["total_files"], body["successful_files"]) == (2, 2)
    assert {x["store_name"] for x in body["results"]} == {"inova"}
    assert set(ml_falso["consultas"]) == {"tok-aurora", "tok-inova-londres"}
    assert ml_falso["envios"] == ["tok-inova-londres", "tok-inova-londres"]


async def test_loja_da_equipe_inativa_nao_e_de_outra_equipe(
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
    cenario: dict[str, User],
    ml_falso: dict[str, Any],
):
    """A delta é da Londres com a integração inativa: não leva o 403 (ela é
    da equipe) e fica fora das tentativas, como sempre."""
    auth_as(cenario["londres"])
    r = await _subir(client, ["delta"])
    assert r.status_code == 200, r.text
    assert r.json()["success"] is False
    assert r.json()["error"] == "nenhuma loja correspondente ativa"
    assert ml_falso["consultas"] == []


async def test_admin_e_sem_equipe_enviam_para_qualquer_loja(
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
    cenario: dict[str, User],
    ml_falso: dict[str, Any],
):
    for quem in ("admin", "galpao"):
        ml_falso.update(dono="tok-cosmo", consultas=[], envios=[])
        auth_as(cenario[quem])
        r = await _subir(client, ["bravo", "cosmo"])
        assert r.status_code == 200, (quem, r.text)
        body = r.json()
        assert body["success"] is True and body["store_name"] == "cosmo", quem
        assert ml_falso["consultas"] == ["tok-bravo", "tok-cosmo"], quem

        # As duas "inova" são tentadas, como sempre (o nome casa as duas).
        ml_falso.update(dono="tok-inova-israel", consultas=[], envios=[])
        r = await _subir_varios(client, ["inova"])
        assert r.status_code == 200, (quem, r.text)
        assert r.json()["successful_files"] == 2, quem
        assert "tok-inova-israel" in ml_falso["consultas"], quem

        # Nome que não existe segue o caminho de sempre (sem 403).
        r = await _subir(client, ["nao-existe"])
        assert r.status_code == 200, (quem, r.text)
        assert r.json()["error"] == "nenhuma loja correspondente ativa", quem
