"""Marketing › Lives (03/11/2026): cadastro pela tela e a porta do gravador do Mac."""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.models import Integration, MarketingLive
from app.models.enums import IntegrationPlatform
from app.models.user import UserRole
from app.routers.marketing_lives import plataforma_do_link

API = "/api/marketing/lives"
TOKEN = "token-do-gravador-de-teste"  # noqa: S105


@pytest.fixture(scope="module", autouse=True)
def _monta_router():
    """O main.py só inclui os routers de Marketing com `enable_marketing`."""
    from app.main import app
    from app.routers import marketing_lives as router_mod

    if not any(getattr(r, "path", "").startswith(API) for r in app.routes):
        app.include_router(router_mod.router)


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(get_settings(), "marketing_agent_token", TOKEN)
    return {"X-Agent-Token": TOKEN}


def test_plataforma_do_link():
    assert plataforma_do_link("https://live.shopee.com.br/share?from=live&session=123") == "shopee"
    assert plataforma_do_link("https://s.shopee.com.br/abc") == "shopee"
    assert plataforma_do_link("https://shp.ee/xyz") == "shopee"
    assert plataforma_do_link("https://www.tiktok.com/@loja/live") == "tiktok"
    assert plataforma_do_link("https://vm.tiktok.com/ZMabc/") == "tiktok"
    assert plataforma_do_link("https://shopee.com.br.golpe.com/x") is None
    assert plataforma_do_link("ftp://live.shopee.com.br/x") is None
    assert plataforma_do_link("live.shopee.com.br/share") is None


async def test_cadastro_e_robo(db, make_user, auth_as, client, token):
    admin = await make_user(role=UserRole.ADMIN)
    loja = Integration(
        user_id=admin.id,
        platform=IntegrationPlatform.SHOPEE,
        name="Barbosa Shopee",
        credentials=b"x",
    )
    db.add(loja)
    await db.commit()
    auth_as(admin)

    r = await client.post(API, json={"link": "https://exemplo.com/live"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "link_invalido"

    r = await client.post(
        API,
        json={
            "link": " https://live.shopee.com.br/share?from=live&session=42 ",
            "apresentador": "Josi",
            "integration_id": str(loja.id),
            "produtos": "Mala ABS 28",
        },
    )
    assert r.status_code == 201, r.text
    live = r.json()
    assert live["plataforma"] == "shopee"
    assert live["status"] == "aguardando"
    assert live["loja"] == "Barbosa Shopee"
    assert live["link"].startswith("https://live")

    # Loja de outra plataforma não casa com link do TikTok.
    r = await client.post(
        API, json={"link": "https://www.tiktok.com/@x/live", "integration_id": str(loja.id)}
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "loja_de_outra_plataforma"

    # Robô sem token: fechado.
    r = await client.get(f"{API}/robo/pendentes")
    assert r.status_code == 401
    r = await client.get(f"{API}/robo/pendentes", headers=token)
    assert [x["id"] for x in r.json()] == [live["id"]]

    url = f"{API}/robo/{live['id']}/estado"
    r = await client.post(url, headers=token, json={"status": "aguardando"})
    assert r.status_code == 200
    r = await client.post(
        url,
        headers=token,
        json={
            "status": "gravando",
            "titulo": "Live das malas",
            "inicio_em": "2026-11-03T20:00:00Z",
            "arquivo": "/x/parte1.ts",
        },
    )
    assert r.status_code == 200

    # Gravando: não apaga nem troca o link.
    assert (await client.delete(f"{API}/{live['id']}")).status_code == 409
    r = await client.patch(
        f"{API}/{live['id']}", json={"link": "https://live.shopee.com.br/share?session=9"}
    )
    assert r.status_code == 409
    r = await client.patch(f"{API}/{live['id']}", json={"apresentador": "Neide"})
    assert r.status_code == 200 and r.json()["apresentador"] == "Neide"
    assert r.json()["loja"] == "Barbosa Shopee"

    r = await client.post(
        url,
        headers=token,
        json={
            "status": "gravada",
            "fim_em": "2026-11-03T21:30:00Z",
            "duracao_s": 5400,
            "tamanho_bytes": 123,
            "metricas": {"espectadores_pico": 80},
        },
    )
    assert r.status_code == 200
    row = await db.get(MarketingLive, live["id"])
    await db.refresh(row)
    assert row.status == "gravada" and row.titulo == "Live das malas"
    assert row.arquivo == "/x/parte1.ts" and row.metricas == {"espectadores_pico": 80}
    assert row.robo_visto_em is not None

    # Gravada é final.
    r = await client.post(url, headers=token, json={"status": "gravando"})
    assert r.status_code == 409
    assert (await client.get(f"{API}/robo/pendentes", headers=token)).json() == []


async def test_erro_regravar_e_permissao(db, make_user, auth_as, client, token):
    leitor = await make_user(permissions={"marketing_criativos": {"view": True}})
    editor = await make_user(permissions={"marketing_criativos": {"view": True, "edit": True}})
    auth_as(leitor)
    r = await client.post(API, json={"link": "https://www.tiktok.com/@x/live"})
    assert r.status_code == 403
    auth_as(editor)
    r = await client.post(API, json={"link": "https://www.tiktok.com/@x/live"})
    live = r.json()
    assert live["plataforma"] == "tiktok"

    url = f"{API}/robo/{live['id']}/estado"
    r = await client.post(url, headers=token, json={"status": "erro", "erro": "a live não começou"})
    assert r.status_code == 200
    auth_as(leitor)
    lista = (await client.get(API)).json()
    assert lista[0]["status"] == "erro" and lista[0]["erro"] == "a live não começou"
    auth_as(editor)
    r = await client.post(f"{API}/{live['id']}/regravar")
    assert r.status_code == 200 and r.json()["status"] == "aguardando" and r.json()["erro"] is None
    assert (await client.delete(f"{API}/{live['id']}")).status_code == 204


async def test_lojas_so_shopee_e_tiktok_ativas(db, make_user, auth_as, client):
    admin = await make_user(role=UserRole.ADMIN)
    db.add_all(
        [
            Integration(
                user_id=admin.id,
                platform=IntegrationPlatform.SHOPEE,
                name="B Shopee",
                credentials=b"x",
            ),
            Integration(
                user_id=admin.id,
                platform=IntegrationPlatform.TIKTOK,
                name="A TikTok",
                credentials=b"x",
            ),
            Integration(
                user_id=admin.id, platform=IntegrationPlatform.ML, name="C ML", credentials=b"x"
            ),
        ]
    )
    await db.commit()
    auth_as(admin)
    r = await client.get(f"{API}/lojas")
    assert [(x["nome"], x["plataforma"]) for x in r.json()] == [
        ("A TikTok", "tiktok"),
        ("B Shopee", "shopee"),
    ]
