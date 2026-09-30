"""Senha extra da Emissão de Serviço (a mesma de Empresas e do Valuation).

Eduardo (30/09/2026): "em emissão de serviço preciso que coloque o mesmo
esquema de senha do empresas". O que estes testes seguram:
- sem a chave, o SERVIDOR recusa TODAS as rotas de /api/nfse — inclusive PDF,
  XML, emitir e cancelar; rota nova no router já nasce trancada;
- a chave de Empresas não abre a Emissão de Serviço, e vice-versa;
- a senha é a mesma, e os erros somam com as outras telas (5 → 15 min);
- PDF/XML abrem por um link de 60 s que só vale para aquele arquivo;
- o webhook da NFE.io (/api/webhooks/nfeio) não depende da senha.
"""

# ruff: noqa: S105, S106  (senhas e chaves de teste, nada real)

import time
import uuid
from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute

from app.main import app
from app.models import UserRole
from app.routers import nfse as nfse_router
from app.security import senha_extra
from app.security.senha_extra import require_empresas_unlock, require_nfse_unlock

SENHA = "senha-de-teste-123"


class RedisFalso:
    def __init__(self):
        self.d = {}

    async def incr(self, k):
        self.d[k] = int(self.d.get(k) or 0) + 1
        return self.d[k]

    async def expire(self, k, s, nx=False):
        return True

    async def delete(self, k):
        self.d.pop(k, None)


@pytest.fixture
def trava_de_verdade(monkeypatch):
    """Tira os desvios do conftest e fixa senha, segredo e Redis de teste."""
    config = SimpleNamespace(
        valuation_password=SENHA, jwt_secret="segredo-de-teste", valuation_unlock_ttl_seconds=900
    )
    monkeypatch.setattr(senha_extra, "get_settings", lambda: config)
    redis = RedisFalso()

    async def _redis():
        return redis

    async def _sem_espera(_s):
        return None

    monkeypatch.setattr(senha_extra, "_redis", _redis)
    monkeypatch.setattr(senha_extra.asyncio, "sleep", _sem_espera)

    def _ativar():
        for dep in (
            require_nfse_unlock,
            require_empresas_unlock,
            nfse_router.trava_pdf,
            nfse_router.trava_xml,
        ):
            app.dependency_overrides.pop(dep, None)

    return SimpleNamespace(ativar=_ativar, config=config, redis=redis)


async def _admin(make_user, auth_as, trava):
    u = await make_user(role=UserRole.ADMIN)
    auth_as(u)
    trava.ativar()
    return u


async def _chave(client) -> dict:
    r = await client.post("/api/nfse/unlock", json={"password": SENHA})
    assert r.status_code == 200, r.text
    assert r.json()["expires_in"] == 900
    return {"X-Nfse-Token": r.json()["token"]}


def _rotas_nfse() -> list[tuple[str, str]]:
    """Todas as rotas de /api/nfse que existem no app, com id falso no caminho."""
    falso = str(uuid.uuid4())
    out = []
    for r in app.routes:
        if not isinstance(r, APIRoute) or not r.path.startswith("/api/nfse/"):
            continue
        if r.path == "/api/nfse/unlock":
            continue
        caminho = r.path
        for nome in r.param_convertors:
            caminho = caminho.replace("{" + nome + "}", falso)
        for metodo in r.methods:
            out.append((metodo, caminho))
    return out


# --- o servidor recusa sem a chave -------------------------------------------


def test_todas_as_rotas_da_emissao_exigem_a_chave():
    """Rota nova no router já nasce trancada: a trava é do router inteiro."""
    rotas = [
        r
        for r in app.routes
        if isinstance(r, APIRoute)
        and r.path.startswith("/api/nfse/")
        and r.path != "/api/nfse/unlock"
    ]
    assert len(rotas) >= 25, len(rotas)
    for r in rotas:
        deps = [d.call for d in r.dependant.dependencies]
        if r.path.endswith(("/pdf", "/xml")):
            # PDF/XML: a chave OU o link de 60 s daquele arquivo.
            assert any(getattr(d, "__qualname__", "").startswith("_trava_arquivo") for d in deps), (
                r.path
            )
        else:
            assert require_nfse_unlock in deps, r.path


@pytest.mark.asyncio
async def test_sem_chave_nada_sai_nem_entra(client, make_user, auth_as, trava_de_verdade):
    await _admin(make_user, auth_as, trava_de_verdade)
    rotas = _rotas_nfse()
    # As que mais importam estão mesmo na lista (PDF/XML e as que mexem na NFE.io).
    caminhos = {c.rsplit("/", 1)[-1] for _, c in rotas}
    assert {"pdf", "xml", "emitir", "cancelar", "status", "faturamento", "tomadores"} <= caminhos
    for metodo, caminho in rotas:
        r = await client.request(metodo, caminho, json={})
        assert r.status_code == 401, (metodo, caminho, r.status_code, r.text)
        assert r.json()["detail"]["code"] == "nfse_locked", (metodo, caminho)


@pytest.mark.asyncio
async def test_com_a_chave_a_tela_funciona(client, make_user, auth_as, trava_de_verdade):
    await _admin(make_user, auth_as, trava_de_verdade)
    chave = await _chave(client)
    r = await client.get("/api/nfse/status", headers=chave)
    assert r.status_code == 200, r.text
    assert r.json()["provedor"] == "nfeio"
    assert (await client.get("/api/nfse/tomadores", headers=chave)).status_code == 200


@pytest.mark.asyncio
async def test_webhook_da_nfeio_nao_depende_da_senha(client, trava_de_verdade):
    """A NFE.io avisa o DaVinci sem pessoa nenhuma na tela: a trava não vale ali."""
    trava_de_verdade.ativar()
    r = await client.post("/api/webhooks/nfeio", content=b"{}")
    assert not (r.status_code == 401 and "nfse_locked" in r.text), r.text


# --- a senha -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_senha_errada_nao_abre(client, make_user, auth_as, trava_de_verdade):
    await _admin(make_user, auth_as, trava_de_verdade)
    r = await client.post("/api/nfse/unlock", json={"password": "errada"})
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "wrong_password"


@pytest.mark.asyncio
async def test_erros_somam_com_a_tela_empresas(client, make_user, auth_as, trava_de_verdade):
    """A senha é a mesma: errar em Empresas conta aqui (senão seriam 5 chances
    por tela para adivinhar a mesma senha)."""
    await _admin(make_user, auth_as, trava_de_verdade)
    for _ in range(5):
        r = await client.post("/api/companies/unlock", json={"password": "errada"})
        assert r.status_code == 401
    r = await client.post("/api/nfse/unlock", json={"password": SENHA})
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "muitas_tentativas"


@pytest.mark.asyncio
async def test_sem_senha_configurada_ninguem_entra(client, make_user, auth_as, trava_de_verdade):
    await _admin(make_user, auth_as, trava_de_verdade)
    trava_de_verdade.config.valuation_password = ""
    r = await client.post("/api/nfse/unlock", json={"password": ""})
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "senha_nao_configurada"


@pytest.mark.asyncio
async def test_quem_nao_tem_permissao_nem_tenta(client, make_user, auth_as, trava_de_verdade):
    auth_as(await make_user(role=UserRole.USER, permissions={"empresa": {"view": True}}))
    trava_de_verdade.ativar()
    r = await client.post("/api/nfse/unlock", json={"password": SENHA})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_quem_nao_e_admin_com_permissao_desbloqueia(
    client, make_user, auth_as, trava_de_verdade
):
    auth_as(await make_user(role=UserRole.USER, permissions={"emissao_servico": {"view": True}}))
    trava_de_verdade.ativar()
    chave = await _chave(client)
    assert (await client.get("/api/nfse/status", headers=chave)).status_code == 200


# --- a chave -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_chave_de_empresas_nao_abre_a_emissao_e_vice_versa(
    client, make_user, auth_as, trava_de_verdade
):
    await _admin(make_user, auth_as, trava_de_verdade)
    r = await client.post("/api/companies/unlock", json={"password": SENHA})
    chave_empresas = r.json()["token"]
    r = await client.get("/api/nfse/status", headers={"X-Nfse-Token": chave_empresas})
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "nfse_locked"

    chave_nfse = (await _chave(client))["X-Nfse-Token"]
    r = await client.get("/api/companies/grid", headers={"X-Empresas-Token": chave_nfse})
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "empresas_locked"

    token_valuation, _ = senha_extra.fazer_token("valuation")
    r = await client.get("/api/nfse/status", headers={"X-Nfse-Token": token_valuation})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_chave_vencida_nao_abre(client, make_user, auth_as, trava_de_verdade, monkeypatch):
    await _admin(make_user, auth_as, trava_de_verdade)
    chave = await _chave(client)
    agora = time.time()
    monkeypatch.setattr(senha_extra.time, "time", lambda: agora + 16 * 60)
    r = await client.get("/api/nfse/status", headers=chave)
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "nfse_locked"


@pytest.mark.asyncio
async def test_chave_adulterada_ou_com_acento_e_recusada(
    client, make_user, auth_as, trava_de_verdade
):
    await _admin(make_user, auth_as, trava_de_verdade)
    ts = int(time.time())
    gigante = f"1{'0' * 400}.x".encode()  # revisão 30/09: estourava o relógio (erro 500)
    for chave in (f"{ts}.{'0' * 64}".encode(), f"{ts}.ção".encode("latin-1"), b"lixo", gigante):
        r = await client.get("/api/nfse/status", headers={"X-Nfse-Token": chave})
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "nfse_locked"


# --- link de 60 s do PDF/XML ---------------------------------------------------


@pytest.mark.asyncio
async def test_link_do_pdf_abre_so_aquele_arquivo(client, make_user, auth_as, trava_de_verdade):
    """A aba do PDF abre por link (sem cabeçalho). O link vale 60 s, só para
    aquela nota e aquele tipo; a chave da página não serve de link e vice-versa."""
    await _admin(make_user, auth_as, trava_de_verdade)
    chave_pagina = (await _chave(client))["X-Nfse-Token"]
    a, b = uuid.uuid4(), uuid.uuid4()
    link_pdf_a = senha_extra.fazer_link_arquivo("nfse", f"{a}:pdf")

    # Passou da trava: a nota não existe, então quem responde é a própria rota (404).
    r = await client.get(f"/api/nfse/emissoes/{a}/pdf", params={"chave": link_pdf_a})
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["code"] == "emissao_nao_encontrada"

    recusados = [
        (f"/api/nfse/emissoes/{a}/xml", {"chave": link_pdf_a}, {}),  # outro tipo
        (f"/api/nfse/emissoes/{b}/pdf", {"chave": link_pdf_a}, {}),  # outra nota
        (f"/api/nfse/emissoes/{a}/pdf", {"chave": chave_pagina}, {}),  # chave da página não é link
        (f"/api/nfse/emissoes/{a}/pdf", {}, {"X-Nfse-Token": link_pdf_a}),  # link não é chave
        (f"/api/nfse/emissoes/{a}/pdf", {"chave": "lixo"}, {}),
    ]
    for caminho, params, headers in recusados:
        r = await client.get(caminho, params=params, headers=headers)
        assert r.status_code == 401, (caminho, params, headers, r.status_code)
        assert r.json()["detail"]["code"] == "nfse_locked"

    # Com a chave no cabeçalho, PDF e XML continuam abrindo (sem link).
    r = await client.get(f"/api/nfse/emissoes/{a}/xml", headers={"X-Nfse-Token": chave_pagina})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_link_do_pdf_vence_em_60_segundos(
    client, make_user, auth_as, trava_de_verdade, monkeypatch
):
    await _admin(make_user, auth_as, trava_de_verdade)
    a = uuid.uuid4()
    link = senha_extra.fazer_link_arquivo("nfse", f"{a}:pdf")
    agora = time.time()
    monkeypatch.setattr(senha_extra.time, "time", lambda: agora + 61)
    r = await client.get(f"/api/nfse/emissoes/{a}/pdf", params={"chave": link})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_link_exige_a_chave_e_a_nota(client, make_user, auth_as, trava_de_verdade):
    await _admin(make_user, auth_as, trava_de_verdade)
    a = uuid.uuid4()
    r = await client.post(f"/api/nfse/emissoes/{a}/link", params={"tipo": "pdf"})
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "nfse_locked"
    chave = await _chave(client)
    r = await client.post(f"/api/nfse/emissoes/{a}/link", params={"tipo": "pdf"}, headers=chave)
    assert r.status_code == 404
    r = await client.post(f"/api/nfse/emissoes/{a}/link", params={"tipo": "exe"}, headers=chave)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_link_sem_sessao_nao_abre(client, trava_de_verdade):
    """O link sozinho não basta: precisa também da sessão e da permissão."""
    trava_de_verdade.ativar()
    a = uuid.uuid4()
    link = senha_extra.fazer_link_arquivo("nfse", f"{a}:pdf")
    r = await client.get(f"/api/nfse/emissoes/{a}/pdf", params={"chave": link})
    assert r.status_code in (401, 403), r.status_code
    assert "emissao_nao_encontrada" not in r.text


@pytest.mark.asyncio
async def test_link_de_verdade_abre_o_pdf_com_o_nome_da_nota(
    client, make_user, auth_as, trava_de_verdade, monkeypatch
):
    """O caminho que a página usa: POST /link com a chave → GET do endereço
    devolvido, sem cabeçalho → o PDF com o nome da nota."""
    await _admin(make_user, auth_as, trava_de_verdade)
    chave = await _chave(client)
    a = uuid.uuid4()
    nota = SimpleNamespace(id=a, n_nfse="1234", nfeio_id=None)

    async def _emissao(_session, emissao_id):
        assert emissao_id == a
        return nota

    async def _baixar(_session, _e, tipo):
        return (b"%PDF-1.4 teste", "application/pdf")

    monkeypatch.setattr(nfse_router, "_emissao", _emissao)
    monkeypatch.setattr(nfse_router.svc, "baixar", _baixar)

    r = await client.post(f"/api/nfse/emissoes/{a}/link", params={"tipo": "pdf"}, headers=chave)
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    assert url.startswith(f"/api/nfse/emissoes/{a}/pdf?chave=")
    r = await client.get(url)  # sem cabeçalho nenhum, como a aba do navegador
    assert r.status_code == 200, r.text
    assert r.content == b"%PDF-1.4 teste"
    assert r.headers["content-disposition"] == 'inline; filename="NFSe_1234.pdf"'
