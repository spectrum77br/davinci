"""Cadastros › Redes Sociais — a conta de Shopee Vídeo (08/10/2026).

A conta nasce ligada a UMA loja (integração Shopee), recebe o app de vídeo
(partner_id + partner_key, digitados na tela) e é autorizada pelo login da
loja na Shopee. O que este arquivo prova:

  • o partner e os tokens ficam CIFRADOS e nunca voltam — nem em resposta,
    nem em claro no banco (varredura do `row::text`, inclusive pelo hex);
  • o link de autorização é o NOVO (auth_type=seller) e volta pro DaVinci
    com o state no PATH;
  • o retorno só aceita a loja da integração (`loja_errada` não grava nada),
    o state vale uma vez e não se mistura com o OAuth das integrações;
  • a loja é identidade: trocar derruba a autorização; uma conta por loja.

Nada de rede: a troca do code (`ClienteShopeeVideo.trocar_code`) é fakada.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Integration,
    IntegrationPlatform,
    Marca,
    OAuthState,
    RedeSocial,
    RedeSocialToken,
    UserRole,
)
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketing import shopee_video as sv

pytestmark = pytest.mark.asyncio

API = "/api/redes-sociais"
SHOP_ID = 1725800210
CHAVE = "chave-falsa-do-app-de-video-NUNCA-vaza-0123"
ACCESS = "access-falso-nunca-vaza"
REFRESH = "refresh-falso-nunca-vaza"


@pytest.fixture(autouse=True)
def _base(monkeypatch):
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "shopee_video_redirect_base", "https://app.hadken.com")
    monkeypatch.setattr(s, "app_url", "https://app.hadken.com")


@pytest.fixture
def troca(monkeypatch):
    chamadas: list[dict] = []
    resposta = {
        "error": "",
        "shop_id_list": [SHOP_ID],
        "user_id_list": [987654321],
        "access_token": ACCESS,
        "refresh_token": REFRESH,
        "expire_in": 14400,
    }

    async def trocar_code(self, code, *, shop_id=None, main_account_id=None):
        chamadas.append(
            {
                "code": code,
                "shop_id": shop_id,
                "partner_id": self.partner_id,
                "main": main_account_id,
            }
        )
        return dict(resposta)

    monkeypatch.setattr(sv.ClienteShopeeVideo, "trocar_code", trocar_code)
    return {"chamadas": chamadas, "resposta": resposta}


async def _admin(make_user, auth_as):
    u = await make_user(role=UserRole.ADMIN)
    auth_as(u)
    return u


async def _loja(
    db: AsyncSession,
    user,
    *,
    nome="Barbosa",
    plataforma=IntegrationPlatform.SHOPEE,
    shop_id: int | None = SHOP_ID,
) -> Integration:
    creds = {"partner_id": 2032110, "partner_key": "da-integracao", "access_token": "loja"}
    if shop_id:
        creds["shop_id"] = shop_id
    i = Integration(
        user_id=user.id, platform=plataforma, name=nome, credentials=encrypt_json(creds)
    )
    db.add(i)
    await db.commit()
    await db.refresh(i)
    return i


async def _marca(db: AsyncSession) -> Marca:
    m = Marca(nome="Uranyx", slug="uranyx")
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return m


async def _cria(client, marca, integ, conta="barbosa"):
    return await client.post(
        API,
        json={
            "marca_id": str(marca.id),
            "plataforma": "shopee",
            "conta": conta,
            "integration_id": str(integ.id) if integ else None,
        },
    )


def _sem_segredo(texto: str) -> None:
    for s in (CHAVE, ACCESS, REFRESH):
        assert s not in texto, s


async def _linha_em_claro(db: AsyncSession, rede_id) -> str:
    return (
        await db.execute(
            text("SELECT t::text FROM redes_sociais_tokens t WHERE rede_social_id = :r"),
            {"r": rede_id},
        )
    ).scalar_one()


async def test_conta_shopee_nasce_ligada_a_loja_shopee(client, db, make_user, auth_as):
    u = await _admin(make_user, auth_as)
    marca = await _marca(db)
    barbosa = await _loja(db, u)
    bling = await _loja(db, u, nome="Bling", plataforma=IntegrationPlatform.BLING)

    r = await _cria(client, marca, bling)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "loja_nao_e_shopee"
    r = await client.post(
        API,
        json={
            "marca_id": str(marca.id),
            "plataforma": "instagram",
            "conta": "x",
            "integration_id": str(barbosa.id),
        },
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "loja_so_para_shopee"

    r = await _cria(client, marca, barbosa)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["integration_id"] == str(barbosa.id)
    assert body["integration_nome"] == "Barbosa"
    assert body["has_token"] is False and body["shopee_app_configurado"] is False

    # Uma conta de Shopee Vídeo por loja.
    r2 = await _cria(client, marca, barbosa, conta="outra")
    assert r2.status_code == 409 and r2.json()["detail"]["code"] == "loja_ja_tem_conta_shopee"

    lojas = (await client.get(f"{API}/lojas-shopee")).json()
    assert [x["nome"] for x in lojas] == ["Barbosa"]


async def test_autorizacao_ponta_a_ponta_sem_vazar_nada(client, db, make_user, auth_as, troca):
    u = await _admin(make_user, auth_as)
    marca = await _marca(db)
    barbosa = await _loja(db, u)
    rede_id = (await _cria(client, marca, barbosa)).json()["id"]

    r = await client.post(
        f"{API}/{rede_id}/shopee/iniciar", json={"partner_id": 2047721, "partner_key": CHAVE}
    )
    assert r.status_code == 200, r.text
    _sem_segredo(r.text)
    url = r.json()["url"]
    q = parse_qs(urlsplit(url).query)
    assert url.startswith("https://open.shopee.com.br/auth?")
    assert q["partner_id"] == ["2047721"] and q["auth_type"] == ["seller"]
    volta = q["redirect_uri"][0]
    assert volta.startswith("https://app.hadken.com/api/redes-sociais/shopee/callback/")
    state = volta.rsplit("/", 1)[1]
    assert q["state"] == [state]

    # Antes de autorizar: app salvo (cifrado), ainda não "conectado".
    out = (await client.get(f"{API}/{rede_id}")).json()
    assert out["shopee_app_configurado"] is True and out["has_token"] is False
    assert out["token_status"] == "pendente"
    linha = await _linha_em_claro(db, rede_id)
    assert CHAVE not in linha and CHAVE.encode().hex() not in linha

    # Volta da Shopee (sem login: é o navegador do vendedor).
    auth_as(None)
    cb = await client.get(
        f"{API}/shopee/callback/{state}",
        params={"code": "CODE-1", "shop_id": SHOP_ID},
        follow_redirects=False,
    )
    assert cb.status_code == 302
    assert cb.headers["location"] == "https://app.hadken.com/redes-sociais?shopee=ok"
    assert troca["chamadas"] == [
        {"code": "CODE-1", "shop_id": SHOP_ID, "partner_id": 2047721, "main": None}
    ]

    tok = (
        await db.execute(
            select(RedeSocialToken)
            .where(RedeSocialToken.rede_social_id == rede_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert tok.external_user_id == "987654321" and tok.status == "ok" and tok.provedor == "shopee"
    assert tok.token_expires_at > datetime.now(UTC) + timedelta(days=29)
    blob = decrypt_json(tok.token_enc)
    assert blob["refresh_token"] == REFRESH and blob["partner_key"] == CHAVE
    assert blob["shop_id"] == SHOP_ID and blob["user_id"] == 987654321
    linha = await _linha_em_claro(db, rede_id)
    for s in (CHAVE, ACCESS, REFRESH):
        assert s not in linha and s.encode().hex() not in linha

    await _admin(make_user, auth_as)
    out = await client.get(f"{API}/{rede_id}")
    _sem_segredo(out.text)
    assert out.json()["has_token"] is True
    assert out.json()["token_conta_externa"] == "Barbosa"
    grid = await client.get(f"{API}/grid")
    _sem_segredo(grid.text)

    # O state vale UMA vez.
    auth_as(None)
    de_novo = await client.get(
        f"{API}/shopee/callback/{state}",
        params={"code": "CODE-1", "shop_id": SHOP_ID},
        follow_redirects=False,
    )
    assert de_novo.headers["location"].endswith("shopee=erro&code=state_consumed")


async def test_loja_errada_nao_grava_nada(client, db, make_user, auth_as, troca):
    u = await _admin(make_user, auth_as)
    marca = await _marca(db)
    barbosa = await _loja(db, u)
    rede_id = (await _cria(client, marca, barbosa)).json()["id"]
    url = (
        await client.post(
            f"{API}/{rede_id}/shopee/iniciar", json={"partner_id": 2047721, "partner_key": CHAVE}
        )
    ).json()["url"]
    state = parse_qs(urlsplit(url).query)["state"][0]
    auth_as(None)
    cb = await client.get(
        f"{API}/shopee/callback/{state}",
        params={"code": "C", "shop_id": 42},
        follow_redirects=False,
    )
    assert cb.headers["location"].endswith("shopee=erro&code=loja_errada")
    assert troca["chamadas"] == []  # nem chegou a trocar o code
    tok = (
        await db.execute(select(RedeSocialToken).where(RedeSocialToken.rede_social_id == rede_id))
    ).scalar_one()
    assert tok.external_user_id is None and tok.status == "pendente"


async def test_resposta_da_troca_com_outra_loja_e_recusada(client, db, make_user, auth_as, troca):
    u = await _admin(make_user, auth_as)
    marca = await _marca(db)
    barbosa = await _loja(db, u)
    rede_id = (await _cria(client, marca, barbosa)).json()["id"]
    url = (
        await client.post(
            f"{API}/{rede_id}/shopee/iniciar", json={"partner_id": 2047721, "partner_key": CHAVE}
        )
    ).json()["url"]
    state = parse_qs(urlsplit(url).query)["state"][0]
    troca["resposta"].update(shop_id_list=[555], user_id_list=[1])
    auth_as(None)
    cb = await client.get(
        f"{API}/shopee/callback/{state}",
        params={"code": "C", "main_account_id": 9},
        follow_redirects=False,
    )
    assert cb.headers["location"].endswith("shopee=erro&code=loja_errada")
    tok = (
        await db.execute(
            select(RedeSocialToken)
            .where(RedeSocialToken.rede_social_id == rede_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert tok.external_user_id is None


async def test_state_das_integracoes_nao_serve_aqui(client, db, make_user, auth_as, troca):
    """Mesma tabela, mesma plataforma — mas o state do OAuth da INTEGRAÇÃO
    (code_verifier = id da integração) não pode autorizar vídeo, e nem é
    consumido por este callback."""
    u = await _admin(make_user, auth_as)
    barbosa = await _loja(db, u)
    db.add(
        OAuthState(
            state="state-da-integracao",
            platform=IntegrationPlatform.SHOPEE,
            user_id=u.id,
            code_verifier=str(barbosa.id),
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    )
    await db.commit()
    auth_as(None)
    cb = await client.get(
        f"{API}/shopee/callback/state-da-integracao",
        params={"code": "C", "shop_id": SHOP_ID},
        follow_redirects=False,
    )
    assert cb.headers["location"].endswith("shopee=erro&code=state_not_found")
    row = (
        await db.execute(
            select(OAuthState)
            .where(OAuthState.state == "state-da-integracao")
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert row.consumed_at is None


async def test_iniciar_exige_app_e_loja_com_shop_id(client, db, make_user, auth_as):
    u = await _admin(make_user, auth_as)
    marca = await _marca(db)
    sem_shop = await _loja(db, u, nome="Sem shop", shop_id=None)
    rede_id = (await _cria(client, marca, sem_shop)).json()["id"]
    r = await client.post(
        f"{API}/{rede_id}/shopee/iniciar", json={"partner_id": 2047721, "partner_key": CHAVE}
    )
    assert r.json()["detail"]["code"] == "loja_sem_shop_id"
    barbosa = await _loja(db, u)
    rede2 = (await _cria(client, marca, barbosa, conta="b2")).json()["id"]
    r = await client.post(f"{API}/{rede2}/shopee/iniciar", json={})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "conta_sem_app_shopee"
    r = await client.post(f"{API}/{rede2}/shopee/iniciar", json={"partner_id": 2047721})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "partner_incompleto"


async def test_token_colado_da_meta_nao_entra_na_shopee(client, db, make_user, auth_as):
    u = await _admin(make_user, auth_as)
    marca = await _marca(db)
    barbosa = await _loja(db, u)
    rede_id = (await _cria(client, marca, barbosa)).json()["id"]
    r = await client.post(f"{API}/{rede_id}/conectar", json={"access_token": "x" * 30})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "shopee_usa_autorizacao"


async def test_trocar_a_loja_derruba_a_autorizacao(client, db, make_user, auth_as, troca):
    u = await _admin(make_user, auth_as)
    marca = await _marca(db)
    barbosa = await _loja(db, u)
    mega = await _loja(db, u, nome="Mega", shop_id=99)
    rede_id = (await _cria(client, marca, barbosa)).json()["id"]
    await client.post(
        f"{API}/{rede_id}/shopee/iniciar", json={"partner_id": 2047721, "partner_key": CHAVE}
    )
    r = await client.patch(f"{API}/{rede_id}", json={"integration_id": str(mega.id)})
    assert r.status_code == 200, r.text
    assert r.json()["integration_nome"] == "Mega"
    assert (
        await db.execute(select(RedeSocialToken).where(RedeSocialToken.rede_social_id == rede_id))
    ).scalar_one_or_none() is None
    rede = await db.get(RedeSocial, rede_id)
    assert rede is not None
