"""Conector do Claude (MCP) — o chat do Claude do chefe criando tarefa no
DaVinci. Eduardo, 08/09: "é só para ele mandar o áudio e a gente gravar na
aba Tarefas". Aqui o "Claude" é simulado com as mensagens JSON-RPC que o app
manda (initialize → initialized → tools/list → tools/call)."""

from __future__ import annotations

import logging

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Tarefa, UserRole
from app.routers import claude_conector as roteador
from app.services import claude_tarefas
from app.services.rate_limit import RateLimitError


@pytest.fixture
def sem_alerta(monkeypatch):
    """Captura os avisos (alerta + Telegram) em vez de mandar."""
    avisos: list[dict] = []

    async def _emit(session, **kw):
        avisos.append(kw)
        return None

    monkeypatch.setattr(claude_tarefas, "emit_alert", _emit)
    monkeypatch.setattr(roteador, "emit_alert", _emit)
    return avisos


@pytest.fixture(autouse=True)
def sem_rate_limit(monkeypatch):
    async def _ok(**kw):
        return 1

    monkeypatch.setattr(roteador, "sliding_window_check", _ok)


async def _conector(
    client: AsyncClient, db: AsyncSession, make_user, auth_as, *, role=UserRole.ADMIN
):
    admin = await make_user(role=UserRole.ADMIN, email="admin@davinci-test.com")
    dono = await make_user(role=role, email="chefe@davinci-test.com")
    dono.name = "Chefe"
    await db.commit()
    auth_as(admin)
    r = await client.post(
        "/api/claude-conector", json={"user_id": str(dono.id), "nome": "Celular do chefe"}
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["url"].endswith("/mcp") and "/api/claude-mcp/" in body["url"]
    token = body["url"].split("/api/claude-mcp/")[1].split("/")[0]
    auth_as(None)
    return admin, dono, token, body


def _rpc(id_, method, params=None):
    m = {"jsonrpc": "2.0", "method": method}
    if id_ is not None:
        m["id"] = id_
    if params is not None:
        m["params"] = params
    return m


def _call(id_, **args):
    return _rpc(id_, "tools/call", {"name": "criar_tarefa", "arguments": args})


async def _usuario(db, make_user, email, nome, **kw):
    u = await make_user(email=email, **kw)
    u.name = nome
    await db.commit()
    return u


@pytest.mark.asyncio
async def test_handshake_e_lista_de_ferramentas(
    client: AsyncClient, db: AsyncSession, make_user, auth_as, sem_alerta
):
    _admin, _dono, token, _ = await _conector(client, db, make_user, auth_as)
    url = f"/api/claude-mcp/{token}/mcp"

    r = await client.post(
        url,
        json=_rpc(
            1,
            "initialize",
            {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "claude"}},
        ),
    )
    assert r.status_code == 200
    res = r.json()["result"]
    assert res["protocolVersion"] == "2025-06-18"
    assert res["serverInfo"]["name"] == "DaVinci" and "tools" in res["capabilities"]

    r = await client.post(url, json=_rpc(None, "notifications/initialized"))
    assert r.status_code == 202
    # Resposta do cliente (sem method) também não tem corpo de volta.
    r = await client.post(url, json={"jsonrpc": "2.0", "id": 9, "result": {}})
    assert r.status_code == 202

    r = await client.post(url, json=_rpc(2, "tools/list"))
    tools = r.json()["result"]["tools"]
    assert [t["name"] for t in tools] == ["criar_tarefa"]
    assert tools[0]["inputSchema"]["required"] == ["descricao"]
    assert tools[0]["annotations"]["destructiveHint"] is False

    r = await client.post(url, json=_rpc(3, "ping"))
    assert r.json()["result"] == {}
    r = await client.post(url, json=_rpc(4, "resources/list"))
    assert r.json()["result"] == {"resources": []}
    r = await client.post(
        url, json=_rpc(5, "tools/call", {"name": "criar_tarefa", "arguments": "x"})
    )
    assert r.json()["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_cria_tarefa_para_si_mesmo(
    client: AsyncClient, db: AsyncSession, make_user, auth_as, sem_alerta
):
    _admin, dono, token, _ = await _conector(client, db, make_user, auth_as)
    sem_alerta.clear()  # o aviso da criação do conector não conta aqui
    r = await client.post(
        f"/api/claude-mcp/{token}/mcp",
        json=_call(5, descricao="Ligar para o fornecedor da mala P7", prazo="2026-09-12"),
    )
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    assert res["isError"] is False
    texto = res["content"][0]["text"]
    assert "Tarefa criada no DaVinci: Ligar para o fornecedor da mala P7" in texto
    assert "Responsável: Chefe" in texto and "Prazo: 12/09/2026" in texto

    t = (await db.execute(select(Tarefa))).scalar_one()
    assert t.responsavel_id == dono.id and t.created_by == dono.id
    assert t.tarefa == "Ligar para o fornecedor da mala P7"
    assert t.observacao.startswith("Prazo: 12/09/2026") and "Claude" in t.observacao
    assert t.data_conclusao is None
    # Pra si mesmo TAMBÉM avisa (confirmação de que o áudio virou tarefa).
    assert len(sem_alerta) == 1 and sem_alerta[0]["user_id"] == dono.id
    assert "Você foi avisado(a) no DaVinci" in texto


@pytest.mark.asyncio
async def test_atribui_a_outra_pessoa_pelo_nome_e_avisa(
    client: AsyncClient, db: AsyncSession, make_user, auth_as, sem_alerta
):
    _admin, dono, token, _ = await _conector(client, db, make_user, auth_as)
    sem_alerta.clear()
    eduardo = await _usuario(db, make_user, "eduardo@davinci-test.com", "Eduardo Lima")

    r = await client.post(
        f"/api/claude-mcp/{token}/mcp",
        json=_call(6, descricao="Conferir estoque", responsavel="eduardo"),
    )
    res = r.json()["result"]
    assert res["isError"] is False and "Responsável: Eduardo Lima" in res["content"][0]["text"]
    assert "foi avisado" in res["content"][0]["text"]
    t = (await db.execute(select(Tarefa))).scalar_one()
    assert t.responsavel_id == eduardo.id and t.created_by == dono.id
    assert len(sem_alerta) == 1 and sem_alerta[0]["user_id"] == eduardo.id


@pytest.mark.asyncio
async def test_nome_parcial_e_por_palavra_e_ignora_acento(
    client: AsyncClient, db: AsyncSession, make_user, auth_as, sem_alerta
):
    """'Ana' NÃO pode cair em 'Juliana' (a revisão pegou); 'João' acha
    'Joao Silva'; 'ana' com duas Anas pergunta."""
    _admin, _dono, token, _ = await _conector(client, db, make_user, auth_as)
    await _usuario(db, make_user, "ju@davinci-test.com", "Juliana Souza")
    joao = await _usuario(db, make_user, "joao@davinci-test.com", "Joao Silva")
    url = f"/api/claude-mcp/{token}/mcp"

    r = await client.post(url, json=_call(1, descricao="x", responsavel="Ana"))
    res = r.json()["result"]
    assert res["isError"] is True and "Não existe usuário 'Ana'" in res["content"][0]["text"]
    assert "Juliana Souza" in res["content"][0]["text"]  # lista só nomes
    assert "@" not in res["content"][0]["text"]  # nunca e-mail

    r = await client.post(url, json=_call(2, descricao="Pagar boleto", responsavel="João"))
    assert r.json()["result"]["isError"] is False
    t = (await db.execute(select(Tarefa))).scalar_one()
    assert t.responsavel_id == joao.id

    await _usuario(db, make_user, "ana1@davinci-test.com", "Ana Paula")
    await _usuario(db, make_user, "ana2@davinci-test.com", "Ana Clara")
    r = await client.post(url, json=_call(3, descricao="x", responsavel="ana"))
    res = r.json()["result"]
    assert res["isError"] is True and "Mais de uma pessoa" in res["content"][0]["text"]
    texto = res["content"][0]["text"]
    assert "Ana Clara" in texto and "Juliana" not in texto
    r = await client.post(url, json=_call(4, descricao="x", responsavel="ana paula"))
    assert r.json()["result"]["isError"] is False


@pytest.mark.asyncio
async def test_usuario_comum_so_cria_para_si_e_nao_ve_lista(
    client: AsyncClient, db: AsyncSession, make_user, auth_as, sem_alerta
):
    _admin, dono, token, _ = await _conector(client, db, make_user, auth_as, role=UserRole.USER)
    await _usuario(db, make_user, "outro@davinci-test.com", "Outro Fulano")
    url = f"/api/claude-mcp/{token}/mcp"
    r = await client.post(url, json=_call(9, descricao="x", responsavel="Outro"))
    texto = r.json()["result"]["content"][0]["text"]
    assert r.json()["result"]["isError"] is True
    assert "só pode criar tarefas para si mesmo" in texto and "Fulano" not in texto
    # Citar a si mesmo pelo nome funciona.
    r = await client.post(url, json=_call(10, descricao="x", responsavel="chefe"))
    assert r.json()["result"]["isError"] is False
    assert (await db.execute(select(Tarefa))).scalar_one().responsavel_id == dono.id


@pytest.mark.asyncio
async def test_usuario_sistema_nao_e_responsavel(
    client: AsyncClient, db: AsyncSession, make_user, auth_as, sem_alerta
):
    _admin, _dono, token, _ = await _conector(client, db, make_user, auth_as)
    sistema = await make_user(email="aprovacao-threema@davinci-test.com")
    sistema.name = "Aprovação via Threema"
    sistema.open_id = "system:aprovacao-threema"
    await db.commit()
    r = await client.post(
        f"/api/claude-mcp/{token}/mcp", json=_call(1, descricao="x", responsavel="Aprovação")
    )
    res = r.json()["result"]
    assert res["isError"] is True and "Threema" not in res["content"][0]["text"]


@pytest.mark.asyncio
async def test_aviso_falhando_nao_duplica_tarefa(
    client: AsyncClient, db: AsyncSession, make_user, auth_as, monkeypatch, sem_alerta
):
    """Se o aviso (alerta/Telegram) estourar, a tarefa fica gravada e o texto
    diz que não avisou — sem 'tente de novo' (duplicaria)."""
    _admin, _dono, token, _ = await _conector(client, db, make_user, auth_as)
    await _usuario(db, make_user, "ed@davinci-test.com", "Eduardo")

    async def _boom(session, **kw):
        raise RuntimeError("telegram caiu")

    monkeypatch.setattr(claude_tarefas, "emit_alert", _boom)
    r = await client.post(
        f"/api/claude-mcp/{token}/mcp", json=_call(1, descricao="x", responsavel="Eduardo")
    )
    res = r.json()["result"]
    assert res["isError"] is False and "não consegui avisar" in res["content"][0]["text"]
    assert len((await db.execute(select(Tarefa))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_limite_de_chamadas(
    client: AsyncClient, db: AsyncSession, make_user, auth_as, monkeypatch, sem_alerta
):
    _admin, _dono, token, _ = await _conector(client, db, make_user, auth_as)

    async def _estourou(**kw):
        raise RateLimitError(retry_after=42)

    monkeypatch.setattr(roteador, "sliding_window_check", _estourou)
    r = await client.post(f"/api/claude-mcp/{token}/mcp", json=_call(1, descricao="x"))
    res = r.json()["result"]
    assert res["isError"] is True and "42 segundos" in res["content"][0]["text"]
    assert (await db.execute(select(Tarefa))).scalars().all() == []


@pytest.mark.asyncio
async def test_freios_do_transporte(
    client: AsyncClient, db: AsyncSession, make_user, auth_as, sem_alerta
):
    _admin, _dono, token, _ = await _conector(client, db, make_user, auth_as)
    url = f"/api/claude-mcp/{token}/mcp"
    # lote grande demais
    r = await client.post(url, json=[_rpc(i, "ping") for i in range(6)])
    assert r.status_code == 400 and r.json()["error"]["code"] == -32600
    # corpo grande demais
    r = await client.post(
        url, content=b"x" * (64 * 1024 + 1), headers={"content-type": "application/json"}
    )
    assert r.status_code == 413
    # Origin estranho => 403; o nosso ou o do Claude passa
    r = await client.post(url, json=_rpc(1, "ping"), headers={"origin": "https://mal.example"})
    assert r.status_code == 403
    r = await client.post(url, json=_rpc(1, "ping"), headers={"origin": "https://claude.ai"})
    assert r.status_code == 200
    # versão de protocolo desconhecida no cabeçalho => 400
    r = await client.post(url, json=_rpc(1, "ping"), headers={"mcp-protocol-version": "1999"})
    assert r.status_code == 400
    # lote pequeno volta lista
    r = await client.post(url, json=[_rpc(1, "ping"), _rpc(2, "tools/list")])
    assert isinstance(r.json(), list) and len(r.json()) == 2


@pytest.mark.asyncio
async def test_token_errado_revogado_e_listagem_sem_url(
    client: AsyncClient, db: AsyncSession, make_user, auth_as, sem_alerta
):
    admin, _dono, token, body = await _conector(client, db, make_user, auth_as)
    r = await client.post("/api/claude-mcp/nao-existe/mcp", json=_rpc(1, "ping"))
    assert r.status_code == 404
    assert (await client.get(f"/api/claude-mcp/{token}/mcp")).status_code == 405
    assert (await client.delete(f"/api/claude-mcp/{token}/mcp")).status_code == 200

    auth_as(admin)
    r = await client.get("/api/claude-conector")
    lista = r.json()
    assert [c["nome"] for c in lista] == ["Celular do chefe"]
    assert lista[0]["url"] is None  # só na criação; o banco guarda o hash
    assert lista[0]["criado_por"] == "admin@davinci-test.com"
    # Conector pra outra pessoa: ela foi avisada.
    assert len(sem_alerta) == 1 and sem_alerta[0]["title"].startswith("🔗")
    assert (await client.delete(f"/api/claude-conector/{body['id']}")).status_code == 204
    auth_as(None)
    r = await client.post(f"/api/claude-mcp/{token}/mcp", json=_rpc(1, "ping"))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_so_admin_gera_conector(client: AsyncClient, make_user, auth_as):
    comum = await make_user(role=UserRole.USER)
    auth_as(comum)
    r = await client.post("/api/claude-conector", json={"user_id": str(comum.id), "nome": "x"})
    assert r.status_code == 403
    assert (await client.get("/api/claude-conector")).status_code == 403


def test_mascara_token_no_access_log():
    roteador.mascarar_token_no_access_log()
    rec = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:1", "POST", "/api/claude-mcp/" + "a" * 48 + "/mcp", "1.1", 200),
        None,
    )
    for f in logging.getLogger("uvicorn.access").filters:
        f.filter(rec)
    assert rec.args[2] == "/api/claude-mcp/***/mcp"
