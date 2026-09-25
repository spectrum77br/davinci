"""Senha extra da tela Empresas (a mesma do Valuation).

Eduardo (25/09/2026): "em empresas preciso que coloque senha, a mesma que tem
em valuation, senha segura porque tem informações que muita gente não pode
ver". O que estes testes seguram:
- sem a chave, o SERVIDOR recusa: tabela, página da empresa, certificados e
  qualquer alteração — esconder a tela não bastaria;
- a lista aberta, usada pelos menus de outras telas, só entrega id, apelido e
  razão social;
- a chave do Valuation não abre Empresas, e vice-versa;
- 5 senhas erradas travam a pessoa por 15 minutos;
- sem senha configurada, ninguém entra.
"""

# ruff: noqa: S105, S106  (senhas e chaves de teste, nada real)

import time
from types import SimpleNamespace

import pytest

from app.main import app
from app.models import CompanyCertificate, UserRole
from app.security import senha_extra
from app.security.cipher import encrypt_bytes
from app.security.senha_extra import require_empresas_unlock

SENHA = "senha-de-teste-123"


class RedisFalso:
    def __init__(self):
        self.d = {}

    async def get(self, k):
        return self.d.get(k)

    async def incr(self, k):
        self.d[k] = int(self.d.get(k) or 0) + 1
        return self.d[k]

    async def expire(self, k, s):
        return True

    async def delete(self, k):
        self.d.pop(k, None)


@pytest.fixture
def trava_de_verdade(monkeypatch):
    """Tira o desvio do conftest e fixa senha, segredo e Redis de teste."""
    config = SimpleNamespace(
        valuation_password=SENHA, jwt_secret="segredo-de-teste", valuation_unlock_ttl_seconds=900
    )
    monkeypatch.setattr(senha_extra, "get_settings", lambda: config)
    redis = RedisFalso()

    async def _redis():
        return redis

    monkeypatch.setattr(senha_extra, "_redis", _redis)
    monkeypatch.setattr(senha_extra.asyncio, "sleep", _sem_espera)

    def _ativar():
        app.dependency_overrides.pop(require_empresas_unlock, None)

    return SimpleNamespace(ativar=_ativar, config=config, redis=redis)


async def _sem_espera(_s):
    return None


async def _admin(make_user, auth_as, trava):
    u = await make_user(role=UserRole.ADMIN)
    auth_as(u)
    trava.ativar()
    return u


async def _chave(client) -> dict:
    r = await client.post("/api/companies/unlock", json={"password": SENHA})
    assert r.status_code == 200, r.text
    return {"X-Empresas-Token": r.json()["token"]}


async def _empresa(client, chave, apelido="kfa", **extra) -> dict:
    r = await client.post(
        "/api/companies",
        json={"razao_social": apelido.upper(), "apelido": apelido, **extra},
        headers=chave,
    )
    assert r.status_code == 201, r.text
    return r.json()


# --- o servidor recusa sem a chave -------------------------------------------


@pytest.mark.asyncio
async def test_sem_chave_nada_sai_nem_entra(client, db, make_user, auth_as, trava_de_verdade):
    await _admin(make_user, auth_as, trava_de_verdade)
    e = await _empresa(client, await _chave(client))
    db.add(
        CompanyCertificate(company_id=e["id"], filename="x.pfx", size_bytes=1, blob=encrypt_bytes(b"x"))
    )
    await db.commit()

    for metodo, caminho, corpo in [
        ("GET", "/api/companies/grid", None),
        ("GET", f"/api/companies/{e['id']}", None),
        ("PATCH", f"/api/companies/{e['id']}", {"obs": "x"}),
        ("POST", "/api/companies", {"razao_social": "X", "apelido": "x"}),
        ("DELETE", f"/api/companies/{e['id']}", None),
        ("GET", f"/api/companies/{e['id']}/certificates", None),
    ]:
        r = await client.request(metodo, caminho, json=corpo)
        assert r.status_code == 401, (metodo, caminho, r.status_code)
        assert r.json()["detail"]["code"] == "empresas_locked"


@pytest.mark.asyncio
async def test_com_a_chave_a_tela_funciona(client, make_user, auth_as, trava_de_verdade):
    await _admin(make_user, auth_as, trava_de_verdade)
    chave = await _chave(client)
    e = await _empresa(client, chave, cnpj="11444777000161")
    r = await client.get("/api/companies/grid", headers=chave)
    assert r.status_code == 200
    assert r.json()["rows"][0]["company"]["cnpj"] == "11444777000161"
    assert (await client.get(f"/api/companies/{e['id']}", headers=chave)).status_code == 200


@pytest.mark.asyncio
async def test_lista_aberta_so_tem_o_nome(client, make_user, auth_as, trava_de_verdade):
    """Integrações e Cadastros usam a lista para os menus: continuam funcionando,
    mas sem CNPJ, IE, IP nem nada sensível."""
    await _admin(make_user, auth_as, trava_de_verdade)
    await _empresa(client, await _chave(client), cnpj="11444777000161", ip="72.60.156.216")
    r = await client.get("/api/companies")  # sem chave nenhuma
    assert r.status_code == 200
    [c] = r.json()
    assert set(c) == {"id", "apelido", "razao_social"}
    assert "11444777000161" not in r.text and "72.60.156.216" not in r.text


# --- a senha -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_senha_errada_nao_abre(client, make_user, auth_as, trava_de_verdade):
    await _admin(make_user, auth_as, trava_de_verdade)
    r = await client.post("/api/companies/unlock", json={"password": "errada"})
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "wrong_password"


@pytest.mark.asyncio
async def test_cinco_erros_travam_por_quinze_minutos(client, make_user, auth_as, trava_de_verdade):
    """Senha de 6 números com 0,3 s por erro e sem limite dava para descobrir
    testando todas. Agora o sexto pedido nem chega a conferir."""
    await _admin(make_user, auth_as, trava_de_verdade)
    for _ in range(5):
        r = await client.post("/api/companies/unlock", json={"password": "errada"})
        assert r.status_code == 401
    r = await client.post("/api/companies/unlock", json={"password": SENHA})  # até a certa
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "muitas_tentativas"


@pytest.mark.asyncio
async def test_acertar_zera_a_contagem(client, make_user, auth_as, trava_de_verdade):
    await _admin(make_user, auth_as, trava_de_verdade)
    for _ in range(4):
        await client.post("/api/companies/unlock", json={"password": "errada"})
    assert (await client.post("/api/companies/unlock", json={"password": SENHA})).status_code == 200
    for _ in range(4):
        await client.post("/api/companies/unlock", json={"password": "errada"})
    assert (await client.post("/api/companies/unlock", json={"password": SENHA})).status_code == 200


@pytest.mark.asyncio
async def test_sem_senha_configurada_ninguem_entra(client, make_user, auth_as, trava_de_verdade):
    await _admin(make_user, auth_as, trava_de_verdade)
    trava_de_verdade.config.valuation_password = ""
    r = await client.post("/api/companies/unlock", json={"password": ""})
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "senha_nao_configurada"


@pytest.mark.asyncio
async def test_quem_nao_tem_permissao_nem_tenta(client, make_user, auth_as, trava_de_verdade):
    auth_as(await make_user(role=UserRole.USER, permissions={}))
    trava_de_verdade.ativar()
    r = await client.post("/api/companies/unlock", json={"password": SENHA})
    assert r.status_code == 403


# --- a chave -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_chave_do_valuation_nao_abre_empresas(client, make_user, auth_as, trava_de_verdade):
    await _admin(make_user, auth_as, trava_de_verdade)
    token, _ = senha_extra.fazer_token("valuation")
    r = await client.get("/api/companies/grid", headers={"X-Empresas-Token": token})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_chave_vencida_nao_abre(client, make_user, auth_as, trava_de_verdade, monkeypatch):
    await _admin(make_user, auth_as, trava_de_verdade)
    chave = await _chave(client)
    agora = time.time()
    monkeypatch.setattr(senha_extra.time, "time", lambda: agora + 16 * 60)
    r = await client.get("/api/companies/grid", headers=chave)
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_chave_adulterada_nao_abre(client, make_user, auth_as, trava_de_verdade):
    await _admin(make_user, auth_as, trava_de_verdade)
    ts = int(time.time())
    r = await client.get("/api/companies/grid", headers={"X-Empresas-Token": f"{ts}.{'0' * 64}"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_quem_nao_e_admin_com_permissao_desbloqueia(client, make_user, auth_as, trava_de_verdade):
    """A senha vale para quem tem acesso à tela, não só para admin."""
    auth_as(await make_user(role=UserRole.USER, permissions={"empresa": {"view": True}}))
    trava_de_verdade.ativar()
    chave = await _chave(client)
    assert (await client.get("/api/companies/grid", headers=chave)).status_code == 200
