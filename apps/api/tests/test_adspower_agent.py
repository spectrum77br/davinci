# ruff: noqa: S105, S608  (token de teste e tabela de teste, nada real)
"""Ponte DaVinci → AdsPower para o IP de cada empresa.

Eduardo (25/09/2026): "quando eu colocar o ip novo já funcione" — e "não quero
que atualize nada [do que já existe], para não dar pau". O que estes testes
seguram:
- sem o token certo, nada sai daqui (o serviço do Mac é o único cliente);
- só aparece como pendente a empresa cujo IP ainda não foi confirmado;
- perfil que atende DUAS empresas nunca é entregue para ser alterado;
- resultado de um IP que já foi trocado não marca o novo como aplicado;
- trocar o IP na tela faz a empresa voltar na hora, mesmo depois de um erro.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.models import Company, StoreInfo, UserRole
from app.routers import adspower_agent

TOKEN = "token-de-teste"
H = {"X-Agent-Token": TOKEN}


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setattr(
        adspower_agent, "get_settings", lambda: SimpleNamespace(adspower_agent_token=TOKEN)
    )


@pytest.fixture
async def espelho(db):
    """O espelho do AdsPower não tem modelo ORM (vem do adspower_sync)."""
    await db.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {adspower_agent._ESPELHO} "
            "(id text PRIMARY KEY, profile_no text, name text)"
        )
    )
    await db.execute(text(f"TRUNCATE {adspower_agent._ESPELHO}"))
    await db.commit()

    async def _perfil(uid: str, no: str, nome: str):
        await db.execute(
            text(f"INSERT INTO {adspower_agent._ESPELHO} VALUES (:i, :n, :m)"),
            {"i": uid, "n": no, "m": nome},
        )
        await db.commit()

    return _perfil


async def _empresa(db, apelido: str, ip: str | None = None, **extra) -> Company:
    c = Company(razao_social=apelido.upper(), apelido=apelido, ip=ip, **extra)
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return c


async def _loja(db, user, plataforma: str, conta: str, servidor: str):
    db.add(StoreInfo(user_id=user.id, platform=plataforma, account_name=conta, server=servidor))
    await db.commit()


# --- token -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sem_token_nada_sai(client):
    r = await client.get("/api/agent/adspower/ip-pendentes")
    assert r.status_code == 401
    r = await client.get("/api/agent/adspower/ip-pendentes", headers={"X-Agent-Token": "errado"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_token_vazio_no_servidor_fecha_tudo(client, monkeypatch):
    """Sem ADSPOWER_AGENT_TOKEN configurado, a ponte fica fechada — nem um
    cabeçalho vazio abre."""
    monkeypatch.setattr(
        adspower_agent, "get_settings", lambda: SimpleNamespace(adspower_agent_token="")
    )
    r = await client.get("/api/agent/adspower/ip-pendentes", headers={"X-Agent-Token": ""})
    assert r.status_code == 401


# --- o que fica pendente -----------------------------------------------------


@pytest.mark.asyncio
async def test_ip_novo_aparece_com_os_perfis_da_empresa(client, db, make_user, espelho):
    u = await make_user()
    await espelho("k1kfaml", "84", "KFA - Mercado Livre")
    await espelho("k1kfash", "119", "KFA - Shopee")
    await _loja(db, u, "ml", "kfa", "84")
    await _loja(db, u, "shopee", "KFA ", "119")  # nome com espaço/maiúscula bate igual
    await _empresa(db, "KFA", ip="72.60.156.216")

    r = await client.get("/api/agent/adspower/ip-pendentes", headers=H)
    assert r.status_code == 200
    [p] = r.json()
    assert p["apelido"] == "KFA" and p["ip"] == "72.60.156.216"
    assert [x["user_id"] for x in p["perfis"]] == ["k1kfaml", "k1kfash"]
    assert p["compartilhados"] == [] and p["sem_perfil"] == []


@pytest.mark.asyncio
async def test_ip_ja_aplicado_nao_volta(client, db, espelho):
    """É o caso das 25 empresas no dia da publicação: nada a fazer com elas."""
    await _empresa(db, "KFA", ip="72.60.156.216", ip_adspower="72.60.156.216")
    r = await client.get("/api/agent/adspower/ip-pendentes", headers=H)
    assert r.json() == []


@pytest.mark.asyncio
async def test_empresa_sem_ip_nao_aparece(client, db, espelho):
    await _empresa(db, "sem-ip")
    r = await client.get("/api/agent/adspower/ip-pendentes", headers=H)
    assert r.json() == []


@pytest.mark.asyncio
async def test_perfil_de_duas_empresas_nunca_e_entregue(client, db, make_user, espelho):
    """Minas e Nexus já dividiram um perfil. Trocar o proxy dele mudaria o IP
    das duas de uma vez."""
    u = await make_user()
    await espelho("k1comp", "83", "Nexus - am ml / Minas - sh")
    await espelho("k1minas", "153", "Minas - Shopee")
    await _loja(db, u, "ml", "nexus", "83")
    await _loja(db, u, "shopee", "minas", "83")
    await _loja(db, u, "tiktok", "minas", "153")
    await _empresa(db, "Minas", ip="187.77.1.2")

    [p] = (await client.get("/api/agent/adspower/ip-pendentes", headers=H)).json()
    assert [x["user_id"] for x in p["perfis"]] == ["k1minas"]
    assert [x["user_id"] for x in p["compartilhados"]] == ["k1comp"]


@pytest.mark.asyncio
async def test_loja_com_servidor_fora_do_espelho_e_avisada(client, db, make_user, espelho):
    u = await make_user()
    await _loja(db, u, "ml", "makisa", "50")
    await _empresa(db, "makisa", ip="187.77.1.3")
    [p] = (await client.get("/api/agent/adspower/ip-pendentes", headers=H)).json()
    assert p["perfis"] == []
    assert p["sem_perfil"] == ["ml/makisa no servidor 50"]


# --- resultado ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_resultado_ok_marca_como_aplicado_e_sai_da_fila(client, db, espelho):
    c = await _empresa(db, "KFA", ip="72.60.156.216", ip_adspower_erro="erro antigo")
    r = await client.post(
        "/api/agent/adspower/ip-resultado",
        headers=H,
        json={"company_id": str(c.id), "ip": "72.60.156.216", "ok": True},
    )
    assert r.json() == {"registrado": True}
    await db.refresh(c)
    assert c.ip_adspower == "72.60.156.216" and c.ip_adspower_erro is None
    assert (await client.get("/api/agent/adspower/ip-pendentes", headers=H)).json() == []


@pytest.mark.asyncio
async def test_resultado_com_erro_guarda_o_motivo_e_espera_uma_hora(client, db, espelho):
    c = await _empresa(db, "KFA", ip="72.60.156.216")
    await client.post(
        "/api/agent/adspower/ip-resultado",
        headers=H,
        json={
            "company_id": str(c.id),
            "ip": "72.60.156.216",
            "ok": False,
            "erro": "proxy não respondeu",
        },
    )
    await db.refresh(c)
    assert c.ip_adspower is None and c.ip_adspower_erro == "proxy não respondeu"
    # não volta a cada minuto...
    assert (await client.get("/api/agent/adspower/ip-pendentes", headers=H)).json() == []
    # ...mas volta depois de uma hora
    c.ip_adspower_em = datetime.now(UTC) - timedelta(hours=2)
    await db.commit()
    assert len((await client.get("/api/agent/adspower/ip-pendentes", headers=H)).json()) == 1


@pytest.mark.asyncio
async def test_resultado_de_ip_antigo_nao_marca_o_novo(client, db, espelho):
    """Alguém trocou o IP enquanto o Mac aplicava o anterior."""
    c = await _empresa(db, "KFA", ip="72.60.156.216")
    r = await client.post(
        "/api/agent/adspower/ip-resultado",
        headers=H,
        json={"company_id": str(c.id), "ip": "72.60.155.3", "ok": True},
    )
    assert r.json() == {"registrado": False, "motivo": "ip_mudou"}
    await db.refresh(c)
    assert c.ip_adspower is None


@pytest.mark.asyncio
async def test_erro_gigante_e_recusado(client, db, espelho):
    c = await _empresa(db, "KFA", ip="72.60.156.216")
    r = await client.post(
        "/api/agent/adspower/ip-resultado",
        headers=H,
        json={"company_id": str(c.id), "ip": "72.60.156.216", "ok": False, "erro": "x" * 501},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_trocar_o_ip_na_tela_faz_voltar_na_hora(client, db, make_user, auth_as, espelho):
    """Depois de um erro a empresa espera uma hora — a não ser que alguém
    digite outro IP: aí o erro era do IP velho e ela volta já."""
    auth_as(await make_user(role=UserRole.ADMIN))
    c = await _empresa(
        db, "KFA", ip="72.60.156.216", ip_adspower_erro="falhou", ip_adspower_em=datetime.now(UTC)
    )
    assert (await client.get("/api/agent/adspower/ip-pendentes", headers=H)).json() == []
    r = await client.patch(f"/api/companies/{c.id}", json={"ip": "76.13.226.18"})
    assert r.status_code == 200
    [p] = (await client.get("/api/agent/adspower/ip-pendentes", headers=H)).json()
    assert p["ip"] == "76.13.226.18"
