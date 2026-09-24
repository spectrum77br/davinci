"""A porta do executor local — a que publica TikTok pelo navegador.

O app do TikTok foi recusado nas duas auditorias (23/09/2026), então quem
publica é o AdsPower no Mac do Eduardo. Estes testes travam o contrato dessa
porta, e principalmente as três coisas que, se quebrarem, quebram calado:

- o servidor NUNCA leva uma postagem de TikTok (ela iria pro branch da Meta);
- dois executores nunca levam a MESMA linha (o vídeo sairia duas vezes);
- conta sem perfil do AdsPower não é entregue — vai pra revisão com o motivo.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

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


@pytest.fixture(autouse=True)
def _uploads(tmp_path, monkeypatch):
    """O lease agora REVALIDA cada postagem antes de entregar (24/09/2026), e
    a revalidação confere o vídeo no disco. Sem apontar o diretório pra cá,
    toda postagem seria recusada por `arquivo_sumiu`."""
    monkeypatch.setattr(get_settings(), "uploads_dir", str(tmp_path))


async def _cenario(db: AsyncSession, *, perfil: str | None = "k1dohvrh", plataforma="tiktok"):
    marca = Marca(nome="Poofy", slug="poofy")
    db.add(marca)
    await db.flush()
    c = MarketingCreative(modelo="video 30s", marca="poofy", marca_id=marca.id, aprovado=True)
    db.add(c)
    await db.flush()
    rel = f"creatives/{c.id}/v.mp4"
    caminho = Path(get_settings().uploads_dir) / rel
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_bytes(b"\x00\x00\x00\x18ftypmp42-fake")
    f = MarketingCreativeFile(
        creative_id=c.id, file_name="v.mp4", file_mime="video/mp4", file_rel=rel,
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


async def test_mac_que_acordou_nao_publica_os_posts_da_noite_colados(
    client: AsyncClient, db: AsyncSession
):
    """O Mac dormiu das 17h às 9h. Os dois posts do TikTok da noite (18:00 e
    19:30) ficaram pendentes. Antes, o executor acordava e levava OS DOIS de
    uma vez, publicando um atrás do outro — ignorando o intervalo de 90
    minutos e sem reconferir nada.

    O publicador do servidor sempre teve essa proteção ("é isto que segura a
    rajada de catch-up depois de o worker ficar parado"); o lease do executor
    não tinha. Agora: UMA por conta por rodada, e a segunda esbarra no
    intervalo e volta pra fila sem gastar tentativa.
    """
    p1 = await _cenario(db)
    # Um SEGUNDO vídeo: o banco proíbe dois pendentes do mesmo arquivo na
    # mesma conta (uq_marketing_postagem_em_voo), então a noite real é isto —
    # o post das 18h de um vídeo e o das 19h30 de outro.
    c1 = await db.get(MarketingCreative, p1.creative_id)
    c2 = MarketingCreative(modelo="video 30s", marca="poofy", marca_id=c1.marca_id, aprovado=True)
    db.add(c2)
    await db.flush()
    rel = f"creatives/{c2.id}/v.mp4"
    caminho = Path(get_settings().uploads_dir) / rel
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_bytes(b"\x00\x00\x00\x18ftypmp42-fake")
    f2 = MarketingCreativeFile(creative_id=c2.id, file_name="v2.mp4", file_mime="video/mp4", file_rel=rel)
    db.add(f2)
    await db.flush()
    p2 = MarketingPostagem(
        creative_id=c2.id, file_id=f2.id, rede_social_id=p1.rede_social_id,
        plataforma="tiktok", conta="poofy_brasil", status="pendente", legenda="Outra ✈️",
    )
    db.add(p2)
    await db.flush()
    # A noite real: o robô criou um às 18:02 e outro às 19:32 de ONTEM, e o
    # Mac dormiu antes de publicar qualquer um. (Criados no mesmo instante, os
    # dois se bloqueariam — mas isso o próprio agendar() impede na criação.)
    agora = datetime.now(UTC)
    p1.created_at = agora - timedelta(hours=15)
    p2.created_at = agora - timedelta(hours=13, minutes=30)
    await db.commit()

    # O Mac acorda. Primeira rodada: sai UM.
    r = await client.post(LEASE, json={"limit": 5}, headers={"X-Agent-Token": TOKEN})
    assert r.status_code == 200, r.text
    assert len(r.json()) == 1, "uma por conta por rodada — nunca as duas coladas"
    assert r.json()[0]["id"] == str(p1.id), "o mais antigo primeiro"

    # 30 segundos depois, a rodada seguinte: o segundo ESPERA o intervalo,
    # porque o primeiro acabou de sair.
    r2 = await client.post(LEASE, json={"limit": 5}, headers={"X-Agent-Token": TOKEN})
    assert r2.json() == [], "o segundo espera os 90 minutos — não sai colado"
    await db.refresh(p2)
    assert p2.status == "pendente", "adiado, não perdido: volta pra fila sem gastar tentativa"


async def test_robo_desligado_de_noite_nao_publica_de_manha(
    client: AsyncClient, db: AsyncSession
):
    """Agendar não é autorizar pra sempre. Se o Eduardo desligou a publicação
    automática enquanto o Mac dormia, o post que o robô tinha agendado NÃO
    pode sair quando o Mac acorda."""
    p = await _cenario(db)
    p.origem = "robo"
    rede = await db.get(RedeSocial, p.rede_social_id)
    rede.postagem_auto = False  # desligou de noite
    await db.commit()

    r = await client.post(LEASE, json={}, headers={"X-Agent-Token": TOKEN})
    assert r.json() == [], "o interruptor tem que valer na hora de publicar"
    await db.refresh(p)
    assert p.status == "falhou"
    assert "postagem_auto" in (p.result or "")
