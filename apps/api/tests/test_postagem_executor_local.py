"""A porta do executor local — a que publica TikTok pelo navegador.

O app do TikTok foi recusado nas duas auditorias (23/09/2026), então quem
publica é o AdsPower no Mac do Eduardo. Estes testes travam o contrato dessa
porta, e principalmente as três coisas que, se quebrarem, quebram calado:

- o servidor NUNCA leva uma postagem de TikTok (ela iria pro branch da Meta);
- dois executores nunca levam a MESMA linha (o vídeo sairia duas vezes);
- conta sem perfil do AdsPower não é entregue — vai pra revisão com o motivo.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Marca, MarketingCreative, MarketingCreativeFile, RedeSocial
from app.models.marketing_postagem import MarketingPostagem

LEASE = "/api/marketing/postagens/executor/lease"
TOKEN = "token-do-executor-0123456789"  # noqa: S105 — token de teste, não senha


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setattr(get_settings(), "marketing_agent_token", TOKEN)


async def _cenario(db: AsyncSession, *, perfil: str | None = "k1dohvrh", plataforma="tiktok"):
    marca = Marca(nome="Poofy", slug="poofy")
    db.add(marca)
    await db.flush()
    c = MarketingCreative(modelo="video 30s", marca="poofy", aprovado=True)
    db.add(c)
    await db.flush()
    f = MarketingCreativeFile(
        creative_id=c.id, file_name="v.mp4", file_mime="video/mp4",
        file_rel=f"creatives/{c.id}/v.mp4",
    )
    rede = RedeSocial(
        marca_id=marca.id, plataforma=plataforma, conta="poofy_brasil",
        ativo=True, adspower_user_id=perfil,
    )
    db.add_all([f, rede])
    await db.flush()
    p = MarketingPostagem(
        creative_id=c.id, file_id=f.id, rede_social_id=rede.id,
        plataforma=plataforma, conta="poofy_brasil", status="pendente",
        legenda="Colecione destinos ✈️",
    )
    db.add(p)
    await db.commit()
    return p


async def test_sem_token_a_porta_fica_fechada(client: AsyncClient, db: AsyncSession):
    await _cenario(db)
    assert (await client.post(LEASE, json={})).status_code == 401


async def test_token_errado_401(client: AsyncClient, db: AsyncSession):
    await _cenario(db)
    r = await client.post(LEASE, json={}, headers={"X-Agent-Token": "chute"})
    assert r.status_code == 401


async def test_entrega_a_postagem_com_link_assinado_e_perfil(
    client: AsyncClient, db: AsyncSession
):
    p = await _cenario(db)
    r = await client.post(LEASE, json={}, headers={"X-Agent-Token": TOKEN})
    assert r.status_code == 200, r.text
    [item] = r.json()
    assert item["id"] == str(p.id)
    assert item["adspower_user_id"] == "k1dohvrh"
    assert item["legenda"] == "Colecione destinos ✈️"
    # O link é ASSINADO e temporário — nunca o caminho no disco.
    assert "/api/marketing/creatives/video/" in item["video_url"]
    assert "uploads" not in item["video_url"]
    # Token de conta não existe nesta plataforma, e não pode aparecer.
    assert "token" not in r.text.lower()


async def test_a_mesma_linha_nao_sai_duas_vezes(client: AsyncClient, db: AsyncSession):
    """A segunda chamada não pega nada: o vídeo sairia duas vezes no perfil."""
    await _cenario(db)
    h = {"X-Agent-Token": TOKEN}
    assert len((await client.post(LEASE, json={}, headers=h)).json()) == 1
    assert (await client.post(LEASE, json={}, headers=h)).json() == []


async def test_conta_sem_perfil_vai_pra_revisao(client: AsyncClient, db: AsyncSession):
    """Sem o perfil o executor não sabe qual janela abrir — e abrir a errada
    publica na conta de outra marca."""
    p = await _cenario(db, perfil=None)
    r = await client.post(LEASE, json={}, headers={"X-Agent-Token": TOKEN})
    assert r.json() == []
    await db.refresh(p)
    assert p.status == "revisar"
    assert "AdsPower" in (p.result or "")


async def test_nao_entrega_plataforma_de_api(client: AsyncClient, db: AsyncSession):
    """Instagram é publicado pelo SERVIDOR. Se vazasse pra cá, sairia duas
    vezes — uma pelo worker e outra pelo navegador."""
    await _cenario(db, plataforma="instagram")
    r = await client.post(LEASE, json={}, headers={"X-Agent-Token": TOKEN})
    assert r.json() == []


async def test_resultado_publicado_carimba_a_url(client: AsyncClient, db: AsyncSession):
    p = await _cenario(db)
    h = {"X-Agent-Token": TOKEN}
    await client.post(LEASE, json={}, headers=h)
    r = await client.post(
        f"/api/marketing/postagens/executor/{p.id}/resultado",
        json={"status": "publicado", "post_url": "https://tiktok.com/@poofy_brasil/video/123"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    await db.refresh(p)
    assert p.status == "publicado"
    assert p.post_url.endswith("/123")


async def test_resultado_repetido_da_409(client: AsyncClient, db: AsyncSession):
    """Relatório duplicado (ou cancelamento no meio) não sobrescreve o que já
    foi decidido."""
    p = await _cenario(db)
    h = {"X-Agent-Token": TOKEN}
    await client.post(LEASE, json={}, headers=h)
    url = f"/api/marketing/postagens/executor/{p.id}/resultado"
    assert (await client.post(url, json={"status": "publicado"}, headers=h)).status_code == 200
    assert (await client.post(url, json={"status": "publicado"}, headers=h)).status_code == 409
