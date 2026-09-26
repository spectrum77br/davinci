# ruff: noqa: S105, S106, S608  (senhas e tabela de teste, nada real)
"""Proxy de cada empresa (26/09/2026).

Eduardo: "colocarmos usuário e senha, um toogle bem organizado, aí quando
mudarmos o ip corrige corretamente e salva e já deixa no ar". O que estes
testes seguram:
- só admin grava/lê o proxy; a senha vai cifrada e só sai pela rota de ver
  senha (admin) e pela ponte do agente com token;
- salvar qualquer parte do proxy volta a empresa como pendente e sobe o rev;
- o serviço ANTIGO (sem ?v=2) não recebe empresa com proxy cadastrado;
- o serviço novo recebe o proxy inteiro e os perfis extras;
- resultado de uma versão velha do proxy não marca a nova como aplicada;
- porta/usuário/senha: tudo ou nada, e com eles o IP é obrigatório;
- painel aberto numa versão velha não desfaz a mudança de outra pessoa;
- perfil extra que já é de outra empresa é recusado;
- empresa com proxy não troca o IP pela tabela (o robô mandaria a senha para
  qualquer IP digitado); o Histórico não guarda o corpo do painel.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from app.models import Company, StoreInfo, UserRole
from app.security.cipher import decrypt_bytes
from sqlalchemy import select, text

from app.routers import adspower_agent

# IPs inventados (não são proxies nossos); públicos porque a tela recusa IP
# de rede interna e de documentação.
TOKEN = "token-de-teste"
H = {"X-Agent-Token": TOKEN}
URL = "/api/companies/{}/proxy"


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setattr(
        adspower_agent, "get_settings", lambda: SimpleNamespace(adspower_agent_token=TOKEN)
    )


@pytest.fixture
async def espelho(db):
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


async def _barbosa(db, user, espelho):
    """Barbosa como em produção: 4 perfis pelas lojas + 61 e 109 soltos."""
    for uid, no, nome in (
        ("u34", "34", "Barbosa - ML"),
        ("u113", "113", "Barbosa - Shopee"),
        ("u61", "61", "Barbosa - ML shop.34"),
        ("u109", "109", "Barbosa - ml sh tk te"),
    ):
        await espelho(uid, no, nome)
    for plat, no in (("ml", "34"), ("shopee", "113")):
        db.add(StoreInfo(user_id=user.id, platform=plat, account_name="barbosa", server=no))
    await db.commit()
    return await _empresa(db, "Barbosa", ip="45.60.2.7", ip_adspower="45.60.2.7")


def _corpo(**k):
    base = {
        "ip": "45.60.1.10",
        "tipo": "socks5",
        "porta": 7128,
        "usuario": "pxteste01",
        "senha": "SenhaNova123",
        "perfis_extras": ["61", " n109 ", "61"],
    }
    base.update(k)
    return base


@pytest.mark.asyncio
async def test_so_admin(client, make_user, auth_as, db):
    user = await make_user(
        role=UserRole.USER, permissions={"empresa": {"view": True, "edit": True}}
    )
    c = await _empresa(db, "Barbosa")
    auth_as(user)
    assert (await client.get(URL.format(c.id))).status_code == 403
    assert (await client.put(URL.format(c.id), json=_corpo())).status_code == 403
    assert (await client.get(URL.format(c.id) + "/senha")).status_code == 403


@pytest.mark.asyncio
async def test_grava_cifrado_volta_pendente_e_mostra_perfis(
    client, make_user, auth_as, db, espelho
):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    c = await _barbosa(db, admin, espelho)
    r = await client.put(URL.format(c.id), json=_corpo())
    assert r.status_code == 200, r.text
    d = r.json()
    assert "SenhaNova123" not in r.text
    assert d["tem_senha"] is True and d["usuario"] == "pxteste01" and d["porta"] == 7128
    assert d["perfis_extras"] == ["61", "109"]  # normalizado, sem repetir
    assert [(p["profile_no"], p["origem"]) for p in d["perfis"]] == [
        ("34", "loja"),
        ("61", "extra"),
        ("109", "extra"),
        ("113", "loja"),
    ]
    await db.refresh(c)
    assert decrypt_bytes(c.proxy_senha_enc).decode() == "SenhaNova123"
    assert c.proxy_rev == 1
    assert c.ip == "45.60.1.10" and c.ip_adspower is None  # pendente de novo
    # A tabela só recebe o sinal, nunca a senha.
    grid = await client.get("/api/companies/grid")
    linha = next(x for x in grid.json()["rows"] if x["company"]["id"] == str(c.id))
    assert linha["company"]["proxy_configurado"] is True
    assert "SenhaNova123" not in grid.text and "pxteste01" not in grid.text
    # Ver a senha: só pela rota própria.
    assert (await client.get(URL.format(c.id) + "/senha")).json() == {"senha": "SenhaNova123"}


@pytest.mark.asyncio
async def test_senha_mantem_troca_e_apaga(client, make_user, auth_as, db):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    c = await _empresa(db, "Barbosa")
    await client.put(URL.format(c.id), json=_corpo())
    # None = mantém, e sem mudança nenhuma o rev não sobe.
    r = await client.put(URL.format(c.id), json=_corpo(senha=None, perfis_extras=["61", "109"]))
    await db.refresh(c)
    assert r.json()["tem_senha"] is True and c.proxy_rev == 1
    # Só espaços também mantém (não apaga sem querer).
    await client.put(URL.format(c.id), json=_corpo(senha="   ", perfis_extras=["61", "109"]))
    await db.refresh(c)
    assert decrypt_bytes(c.proxy_senha_enc).decode() == "SenhaNova123" and c.proxy_rev == 1
    # Troca: rev sobe.
    await client.put(URL.format(c.id), json=_corpo(senha="Outra456", perfis_extras=["61", "109"]))
    await db.refresh(c)
    assert decrypt_bytes(c.proxy_senha_enc).decode() == "Outra456" and c.proxy_rev == 2
    # "" apaga — mas só junto com usuário e porta (meio proxy não salva).
    r = await client.put(URL.format(c.id), json=_corpo(senha="", perfis_extras=["61", "109"]))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "proxy_incompleto"
    r = await client.put(
        URL.format(c.id),
        json=_corpo(senha="", usuario=None, porta=None, perfis_extras=["61", "109"]),
    )
    await db.refresh(c)
    assert r.json()["tem_senha"] is False and c.proxy_senha_enc is None and c.proxy_rev == 3


@pytest.mark.asyncio
async def test_validacoes(client, make_user, auth_as, db):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    c = await _empresa(db, "Barbosa")
    await _empresa(db, "Inova", ip="45.60.1.20")
    r = await client.put(URL.format(c.id), json=_corpo(perfis_extras=["61", "abc"]))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "perfil_invalido"
    r = await client.put(URL.format(c.id), json=_corpo(ip="45.60.1.20"))
    assert r.status_code == 409 and r.json()["detail"]["empresa"] == "Inova"
    r = await client.put(URL.format(c.id), json=_corpo(ip="45.60.1.10:7128:px:SenhaVazada"))
    assert r.status_code == 200  # cola a linha do proxy: fica só o IP
    assert "SenhaVazada" not in r.text
    r = await client.put(URL.format(c.id), json=_corpo(porta=70000))
    assert r.status_code == 422
    # Tudo ou nada; com proxy, o IP é obrigatório.
    # (sem extras: 61 e 109 já são da Barbosa aqui)
    nova = await _empresa(db, "Nova")
    r = await client.put(
        URL.format(nova.id), json=_corpo(ip="45.60.1.30", senha=None, perfis_extras=[])
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "proxy_incompleto"
    r = await client.put(
        URL.format(nova.id), json=_corpo(ip="45.60.1.30", porta=None, perfis_extras=[])
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "proxy_incompleto"
    r = await client.put(URL.format(nova.id), json=_corpo(ip=None, perfis_extras=[]))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "ip_obrigatorio"
    # Só o IP (sem proxy) continua valendo.
    r = await client.put(URL.format(nova.id), json={"ip": "45.60.1.30"})
    assert r.status_code == 200 and r.json()["ip"] == "45.60.1.30"


@pytest.mark.asyncio
async def test_agente_antigo_nao_recebe_empresa_com_proxy(client, make_user, auth_as, db, espelho):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    c = await _barbosa(db, admin, espelho)
    await _empresa(db, "Inova", ip="45.60.1.20")  # sem proxy cadastrado
    await client.put(URL.format(c.id), json=_corpo())
    v1 = (await client.get("/api/agent/adspower/ip-pendentes", headers=H)).json()
    assert [p["apelido"] for p in v1] == ["Inova"]
    v2 = (await client.get("/api/agent/adspower/ip-pendentes?v=2", headers=H)).json()
    barbosa = next(p for p in v2 if p["apelido"] == "Barbosa")
    assert barbosa["proxy"] == {
        "tipo": "socks5",
        "porta": 7128,
        "usuario": "pxteste01",
        "senha": "SenhaNova123",
    }
    assert barbosa["rev"] == 1
    assert sorted(p["profile_no"] for p in barbosa["perfis"]) == ["109", "113", "34", "61"]
    inova = next(p for p in v2 if p["apelido"] == "Inova")
    assert inova["proxy"] is None


@pytest.mark.asyncio
async def test_extra_que_ja_e_de_outra_empresa_e_recusado(client, make_user, auth_as, db, espelho):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    c = await _barbosa(db, admin, espelho)
    outra = await _empresa(db, "Inova", ip="45.60.1.20")
    r = await client.put(
        URL.format(outra.id), json=_corpo(ip="45.60.1.20", usuario="px2", perfis_extras=["61"])
    )
    assert r.status_code == 200, r.text
    r = await client.put(URL.format(c.id), json=_corpo())
    assert r.status_code == 409
    assert r.json()["detail"] == {
        "code": "perfil_de_outra_empresa",
        "perfil": "61",
        "empresa": "Inova",
    }
    # Perfil de LOJA de outra empresa também.
    r = await client.put(
        URL.format(outra.id), json=_corpo(ip="45.60.1.20", usuario="px2", perfis_extras=["34"])
    )
    assert r.status_code == 409 and r.json()["detail"]["empresa"] == "Barbosa"


@pytest.mark.asyncio
async def test_extra_de_duas_empresas_nao_e_trocado(client, make_user, auth_as, db, espelho):
    """Dado antigo (gravado antes da trava) com o mesmo extra em duas empresas:
    o robô não recebe o perfil e o painel mostra ⚠."""
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    c = await _barbosa(db, admin, espelho)
    await _empresa(db, "Inova", ip="45.60.1.20", adspower_perfis_extras=["61"])
    await client.put(URL.format(c.id), json=_corpo(perfis_extras=["109"]))
    c.adspower_perfis_extras = ["61", "109"]
    await db.commit()
    v2 = (await client.get("/api/agent/adspower/ip-pendentes?v=2", headers=H)).json()
    barbosa = next(p for p in v2 if p["apelido"] == "Barbosa")
    assert "61" not in [p["profile_no"] for p in barbosa["perfis"]]
    assert "61" in [p["profile_no"] for p in barbosa["compartilhados"]]
    painel = (await client.get(URL.format(c.id))).json()
    assert next(p for p in painel["perfis"] if p["profile_no"] == "61")["compartilhado"] is True


@pytest.mark.asyncio
async def test_extra_que_nao_existe_no_adspower_e_avisado(client, make_user, auth_as, db, espelho):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    c = await _barbosa(db, admin, espelho)
    r = await client.put(URL.format(c.id), json=_corpo(perfis_extras=["999"]))
    assert any("999" in x for x in r.json()["sem_perfil"])


@pytest.mark.asyncio
async def test_resultado_de_versao_velha_nao_marca_aplicado(
    client, make_user, auth_as, db, espelho
):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    c = await _barbosa(db, admin, espelho)
    await client.put(URL.format(c.id), json=_corpo())  # rev 1
    await client.put(URL.format(c.id), json=_corpo(senha="Corrigida9"))  # rev 2
    ip = "45.60.1.10"
    r = await client.post(
        "/api/agent/adspower/ip-resultado",
        headers=H,
        json={"company_id": str(c.id), "ip": ip, "ok": True, "rev": 1},
    )
    assert r.json() == {"registrado": False, "motivo": "proxy_mudou"}
    # O serviço antigo (sem rev) também não vale para empresa com proxy cadastrado.
    r = await client.post(
        "/api/agent/adspower/ip-resultado",
        headers=H,
        json={"company_id": str(c.id), "ip": ip, "ok": True},
    )
    assert r.json()["registrado"] is False
    r = await client.post(
        "/api/agent/adspower/ip-resultado",
        headers=H,
        json={"company_id": str(c.id), "ip": ip, "ok": True, "rev": 2},
    )
    assert r.json() == {"registrado": True}
    c2 = (await db.execute(select(Company).where(Company.id == c.id))).scalar_one()
    await db.refresh(c2)
    assert c2.ip_adspower == ip


@pytest.mark.asyncio
async def test_troca_de_ip_na_tabela_sobe_o_rev(client, make_user, auth_as, db):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    c = await _empresa(db, "Barbosa", ip="45.60.2.7")
    await client.patch(f"/api/companies/{c.id}", json={"ip": "45.60.1.10"})
    await db.refresh(c)
    assert c.proxy_rev == 1 and c.ip_adspower is None


@pytest.mark.asyncio
async def test_painel_de_versao_velha_nao_desfaz_outra_mudanca(client, make_user, auth_as, db):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    c = await _empresa(db, "Barbosa")
    assert (await client.put(URL.format(c.id), json=_corpo(rev=0))).status_code == 200  # rev 1
    r = await client.put(URL.format(c.id), json=_corpo(senha="Outra456", rev=0))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "proxy_mudou_enquanto_editava"
    await db.refresh(c)
    assert decrypt_bytes(c.proxy_senha_enc).decode() == "SenhaNova123" and c.proxy_rev == 1


@pytest.mark.asyncio
async def test_salvar_sem_mudar_tenta_de_novo_na_hora(client, make_user, auth_as, db):
    """✗ no AdsPower: "Salvar e aplicar" sem mudar nada = tentar agora, sem
    esperar a nova tentativa de 1 hora (e sem subir a versão)."""
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    c = await _empresa(db, "Barbosa")
    await client.put(URL.format(c.id), json=_corpo(perfis_extras=[]))
    c.ip_adspower_erro = "o proxy cadastrado no DaVinci não funcionou"
    c.ip_adspower_em = datetime.now(UTC)
    await db.commit()
    v2 = (await client.get("/api/agent/adspower/ip-pendentes?v=2", headers=H)).json()
    assert v2 == []  # esperando a hora
    await client.put(URL.format(c.id), json=_corpo(senha=None, perfis_extras=[]))
    await db.refresh(c)
    assert c.ip_adspower_erro is None and c.proxy_rev == 1
    v2 = (await client.get("/api/agent/adspower/ip-pendentes?v=2", headers=H)).json()
    assert [p["apelido"] for p in v2] == ["Barbosa"]


@pytest.mark.asyncio
async def test_ip_de_empresa_com_proxy_nao_muda_pela_tabela(client, make_user, auth_as, db):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    c = await _empresa(db, "Barbosa")
    await client.put(URL.format(c.id), json=_corpo())
    r = await client.patch(f"/api/companies/{c.id}", json={"ip": "45.60.1.99"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "ip_pelo_painel_proxy"
    # O mesmo IP (outro campo na mesma edição) continua passando.
    r = await client.patch(f"/api/companies/{c.id}", json={"ip": "45.60.1.10", "uf": "SP"})
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_so_extras_tambem_e_do_servico_novo(client, make_user, auth_as, db, espelho):
    """Empresa só com perfis extras (sem senha): o serviço antigo não conhece
    extras e marcaria ✓ sem ter trocado esses perfis."""
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    c = await _barbosa(db, admin, espelho)
    await client.put(URL.format(c.id), json={"ip": "45.60.1.10", "perfis_extras": ["61"]})
    v1 = (await client.get("/api/agent/adspower/ip-pendentes", headers=H)).json()
    assert v1 == []
    r = await client.post(
        "/api/agent/adspower/ip-resultado",
        headers=H,
        json={"company_id": str(c.id), "ip": "45.60.1.10", "ok": True},
    )
    assert r.json() == {"registrado": False, "motivo": "proxy_mudou"}


@pytest.mark.asyncio
async def test_v2_manda_os_ips_das_outras_empresas(client, make_user, auth_as, db, espelho):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    c = await _barbosa(db, admin, espelho)
    await _empresa(db, "Inova", ip="45.60.1.20", ip_adspower="45.60.2.20")
    await client.put(URL.format(c.id), json=_corpo())
    v2 = (await client.get("/api/agent/adspower/ip-pendentes?v=2", headers=H)).json()
    barbosa = next(p for p in v2 if p["apelido"] == "Barbosa")
    assert barbosa["ips_de_outras"] == ["45.60.1.20", "45.60.2.20"]
    assert "45.60.1.10" not in barbosa["ips_de_outras"]
    v1 = (await client.get("/api/agent/adspower/ip-pendentes", headers=H)).json()
    assert all(p["ips_de_outras"] == [] for p in v1)


@pytest.mark.asyncio
async def test_senha_ilegivel_vira_erro_na_tela(client, make_user, auth_as, db):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    c = await _empresa(db, "Barbosa")
    await client.put(URL.format(c.id), json=_corpo(perfis_extras=[]))
    c.proxy_senha_enc = b"lixo-que-nao-decifra"
    await db.commit()
    v2 = (await client.get("/api/agent/adspower/ip-pendentes?v=2", headers=H)).json()
    assert v2 == []
    await db.refresh(c)
    assert "cadastre de novo" in c.ip_adspower_erro
    # Cadastrar a senha de novo resolve (não dá 500 por causa da ilegível).
    r = await client.put(URL.format(c.id), json=_corpo(senha="Refeita1", perfis_extras=[]))
    assert r.status_code == 200, r.text
    await db.refresh(c)
    assert c.ip_adspower_erro is None and decrypt_bytes(c.proxy_senha_enc).decode() == "Refeita1"


def test_historico_nao_guarda_o_corpo_do_painel():
    from app.historico import nomes

    assert nomes.SEM_CORPO.search("/api/companies/7f0c/proxy")
    assert not nomes.SEM_CORPO.search("/api/companies/7f0c")
    assert nomes.REVELACOES[("GET", "/api/companies/{company_id}/proxy/senha")]
