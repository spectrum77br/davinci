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
    assert [t["name"] for t in tools] == [
        "criar_tarefa",
        "listar_tarefas",
        "concluir_tarefa",
        "consultar_pedido",
        "listar_dms",
        "responder_dm",
    ]
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


# ---- listar / concluir ----------------------------------------------------------


def _tool(id_, nome, **args):
    return _rpc(id_, "tools/call", {"name": nome, "arguments": args})


@pytest.mark.asyncio
async def test_lista_pendentes_e_conclui_pelo_codigo(
    client: AsyncClient, db: AsyncSession, make_user, auth_as, sem_alerta
):
    _admin, _dono, token, _ = await _conector(client, db, make_user, auth_as)
    url = f"/api/claude-mcp/{token}/mcp"
    for d in ("Ligar para o fornecedor", "Conferir estoque das malas"):
        assert (await client.post(url, json=_call(1, descricao=d))).json()["result"][
            "isError"
        ] is False

    r = await client.post(url, json=_rpc(2, "tools/list"))
    assert [t["name"] for t in r.json()["result"]["tools"]] == [
        "criar_tarefa",
        "listar_tarefas",
        "concluir_tarefa",
        "consultar_pedido",
        "listar_dms",
        "responder_dm",
    ]

    r = await client.post(url, json=_tool(3, "listar_tarefas"))
    texto = r.json()["result"]["content"][0]["text"]
    assert texto.startswith("Tarefas pendentes (2)") and "[" in texto and "resp.: Chefe" in texto
    codigo = texto.split("[")[1].split("]")[0]
    assert len(codigo) == 8

    r = await client.post(url, json=_tool(4, "concluir_tarefa", codigo=codigo))
    res = r.json()["result"]
    assert res["isError"] is False and "Tarefa concluída no DaVinci" in res["content"][0]["text"]
    pend = (await db.execute(select(Tarefa).where(Tarefa.data_conclusao.is_(None)))).scalars().all()
    assert len(pend) == 1

    r = await client.post(url, json=_tool(5, "listar_tarefas", filtro="concluidas"))
    assert "Tarefas concluídas (1)" in r.json()["result"]["content"][0]["text"]
    r = await client.post(url, json=_tool(6, "listar_tarefas", so_minhas=True))
    assert "Tarefas pendentes (1)" in r.json()["result"]["content"][0]["text"]


@pytest.mark.asyncio
async def test_concluir_por_trecho_ambiguo_pergunta(
    client: AsyncClient, db: AsyncSession, make_user, auth_as, sem_alerta
):
    _admin, _dono, token, _ = await _conector(client, db, make_user, auth_as)
    url = f"/api/claude-mcp/{token}/mcp"
    for d in ("Pagar boleto da luz", "Pagar boleto da água"):
        await client.post(url, json=_call(1, descricao=d))
    r = await client.post(url, json=_tool(2, "concluir_tarefa", descricao="pagar boleto"))
    res = r.json()["result"]
    assert res["isError"] is True and "Mais de uma tarefa" in res["content"][0]["text"]
    r = await client.post(url, json=_tool(3, "concluir_tarefa", descricao="boleto da agua"))
    assert r.json()["result"]["isError"] is False
    r = await client.post(url, json=_tool(4, "concluir_tarefa", descricao="nada a ver"))
    assert r.json()["result"]["isError"] is True


@pytest.mark.asyncio
async def test_usuario_comum_lista_e_conclui_so_as_suas(
    client: AsyncClient, db: AsyncSession, make_user, auth_as, sem_alerta
):
    admin, dono, token, _ = await _conector(client, db, make_user, auth_as, role=UserRole.USER)
    url = f"/api/claude-mcp/{token}/mcp"
    # Tarefa de OUTRA pessoa, criada pelo admin.
    outro = await _usuario(db, make_user, "outro@davinci-test.com", "Outro")
    from datetime import date

    db.add(
        Tarefa(
            responsavel_id=outro.id,
            data_inicio=date.today(),
            tarefa="Segredo do outro",
            created_by=admin.id,
        )
    )
    await db.commit()
    await client.post(url, json=_call(1, descricao="Minha tarefa"))

    r = await client.post(url, json=_tool(2, "listar_tarefas", filtro="todas"))
    texto = r.json()["result"]["content"][0]["text"]
    assert "Minha tarefa" in texto and "Segredo" not in texto
    r = await client.post(url, json=_tool(3, "concluir_tarefa", descricao="Segredo"))
    assert r.json()["result"]["isError"] is True
    assert (
        await db.execute(select(Tarefa).where(Tarefa.tarefa == "Segredo do outro"))
    ).scalar_one().data_conclusao is None


# ---- briefing matinal (Eduardo, 14/09): linha rica + consultar_pedido ----------


def test_linha_tarefa_mostra_quem_passou_dias_e_prazo():
    from datetime import date
    from uuid import uuid4

    from app.models import Tarefa

    chefe, ana = uuid4(), uuid4()
    nomes = {chefe: "Chefe", ana: "Ana"}
    hoje = date(2026, 9, 14)
    t = Tarefa(
        id=uuid4(), responsavel_id=ana, created_by=chefe, data_inicio=date(2026, 9, 11),
        tarefa="Ligar para o fornecedor",
        observacao="Prazo: 12/09/2026 · Ver preço · Criada pelo Claude (áudio/chat)",
    )
    linha = claude_tarefas._linha_tarefa(t, nomes, hoje=hoje)
    assert "resp.: Ana" in linha and "de: Chefe" in linha
    assert "início 11/09 (há 3 dias)" in linha
    assert "PRAZO 12/09 — ATRASADA 2 dias" in linha
    # A etiqueta "Criada pelo Claude" e o "Prazo: …" saem; o resto da observação fica.
    assert "Ver preço" in linha and "Criada pelo Claude" not in linha
    assert "Prazo: 12/09/2026" not in linha and linha.count("12/09") == 1

    t.observacao = "Prazo: 14/09/2026"
    assert "vence HOJE" in claude_tarefas._linha_tarefa(t, nomes, hoje=hoje)
    t.observacao = "Prazo: 20/09/2026"
    t.data_inicio = hoje
    linha = claude_tarefas._linha_tarefa(t, nomes, hoje=hoje)
    assert "prazo 20/09 (em 6 dias)" in linha and "(hoje)" in linha
    # Tarefa que a própria pessoa criou: sem "de:".
    t.created_by = ana
    assert "de:" not in claude_tarefas._linha_tarefa(t, nomes, hoje=hoje)
    # Concluída: sem "há N dias" nem prazo.
    t.data_conclusao = hoje
    linha = claude_tarefas._linha_tarefa(t, nomes, hoje=hoje)
    assert "concluída 14/09" in linha and "prazo" not in linha.lower()


async def _pedido_completo(db: AsyncSession) -> None:
    """Pedido 295070 com tudo ligado: logística, chamado + mensagem, devolução,
    margem — o que `consultar_pedido` junta numa resposta só."""
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from sqlalchemy import text as sql

    from app.models import (
        BlingOrder,
        Chamado,
        ChamadoMensagem,
        DevolucaoRastreio,
        Devolution,
        Logistica,
        SituacaoBling,
    )

    agora = datetime.now(UTC)
    ja_tem = (
        await db.execute(select(SituacaoBling).where(SituacaoBling.id == 15))
    ).scalar_one_or_none()
    if ja_tem is None:
        db.add(SituacaoBling(id=15, nome="Em andamento"))
    db.add(BlingOrder(
        bling_id=1, numero="295070", numeroloja="2000012345", situacao="15", loja="5001",
        data=agora, item_index=0, item_codigo="SKU1", item_descricao="Celular X",
        item_quantidade=1, total=1800, nome_destinatario="Maria", cidade_destino="Curitiba",
        uf_destino="PR", em_andamento_data=agora.date(),
    ))
    db.add(Logistica(
        pedido_bling="295070", plataforma="Mercado Livre", rastreio="AD890179823BR",
        localizacao="Objeto apreendido pela Secretaria da Fazenda", localizacao_at=agora,
        meli_status={"order_status": "paid", "ship_status": "shipped"}, status_bling="Retido",
    ))
    ch = Chamado(
        pedido_bling="295070", origem="manual", canal="manual", data=agora.date(),
        resolvido=False, chamado="12345",
    )
    db.add(ch)
    await db.flush()
    db.add(ChamadoMensagem(
        chamado_id=ch.id, direcao="enviada", tipo="texto", texto="Olá, pedido retido",
        canal="manual", status="enviada",
    ))
    db.add(Devolution(
        pedido_bling="295070", conta="kia", data=agora, motivo_devolucao="Defeito",
        reembolso=True, prazo_contestacao=agora + timedelta(days=1),
    ))
    db.add(DevolucaoRastreio(
        pedido_bling="295070", rastreio="QB1BR", localizacao="Em trânsito",
        pacote_entregue_em=agora,
    ))
    await db.execute(sql(
        "INSERT INTO verificar_margem (bling_order_item_id, pedido_bling, sku, "
        "bling_status_margem, bling_margem_calculado, bling_lucro_calculado, financeiro_status) "
        "VALUES (:id, '295070', 'SKU1', 'Aprovado', 0.125, 200, 'pending')"
    ).bindparams(id=uuid4()))
    await db.commit()


@pytest.mark.asyncio
async def test_consultar_pedido_junta_tudo(
    client: AsyncClient, db: AsyncSession, make_user, auth_as, sem_alerta
):
    _admin, _dono, token, _ = await _conector(client, db, make_user, auth_as)
    url = f"/api/claude-mcp/{token}/mcp"
    await _pedido_completo(db)

    r = await client.post(url, json=_tool(1, "consultar_pedido", pedido="295070"))
    res = r.json()["result"]
    assert res["isError"] is False, res
    texto = res["content"][0]["text"]
    assert texto.startswith("Pedido Bling 295070 · marketplace 2000012345")
    assert "situação Bling: Em andamento" in texto
    assert "Celular X" in texto and "SKU SKU1" in texto
    assert "cliente Maria (Curitiba, PR)" in texto and "enviado (Em andamento) em" in texto
    assert "rastreio AD890179823BR" in texto and "Objeto apreendido" in texto
    assert "order_status=paid, ship_status=shipped" in texto and "classificação: Retido" in texto
    assert "Chamado" in texto and "ABERTO" in texto and "nº 12345" in texto
    assert "última mensagem (enviada" in texto and "Olá, pedido retido" in texto
    assert "Devolução" in texto and "motivo: Defeito" in texto and "reembolso: sim" in texto
    assert "prazo p/ contestar" in texto
    assert "Rastreio da devolução: rastreio QB1BR" in texto
    assert "pacote entregue ao vendedor" in texto
    assert (
        "Margem SKU SKU1 · status Aprovado (decisão manual) · Bling 12.5% (lucro R$ 200,00)"
        in texto
    )
    assert "saldo final" not in texto
    assert "financeiro: pending" in texto

    # Pelo código do marketplace acha o mesmo pedido.
    r = await client.post(url, json=_tool(2, "consultar_pedido", pedido="2000012345"))
    assert r.json()["result"]["content"][0]["text"].startswith("Pedido Bling 295070")


@pytest.mark.asyncio
async def test_consultar_pedido_nao_encontrado_e_so_admin(
    client: AsyncClient, db: AsyncSession, make_user, auth_as, sem_alerta
):
    _admin, _dono, token, _ = await _conector(client, db, make_user, auth_as)
    url = f"/api/claude-mcp/{token}/mcp"
    r = await client.post(url, json=_tool(1, "consultar_pedido", pedido="999999"))
    res = r.json()["result"]
    assert res["isError"] is False and "não encontrado" in res["content"][0]["text"]
    r = await client.post(url, json=_tool(2, "consultar_pedido", pedido=""))
    assert r.json()["result"]["isError"] is True

    # Usuário comum: a ferramenta aparece, mas recusa (dados de pedido cruzam equipes).
    comum = await make_user(role=UserRole.USER, email="comum@davinci-test.com")
    auth_as(_admin)
    r = await client.post(
        "/api/claude-conector", json={"user_id": str(comum.id), "nome": "Celular do comum"}
    )
    assert r.status_code == 201, r.text
    token2 = r.json()["url"].split("/api/claude-mcp/")[1].split("/")[0]
    auth_as(None)
    r = await client.post(
        f"/api/claude-mcp/{token2}/mcp", json=_tool(3, "consultar_pedido", pedido="295070")
    )
    res = r.json()["result"]
    assert res["isError"] is True and "administradores" in res["content"][0]["text"]


@pytest.mark.asyncio
async def test_consultar_pedido_ramos_vazios_vencido_cancelado_e_rastreio_sem_lancamento(
    client: AsyncClient, db: AsyncSession, make_user, auth_as, sem_alerta
):
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from sqlalchemy import text as sql

    from app.models import BlingOrder, Chamado, DevolucaoRastreio, SituacaoBling

    _admin, _dono, token, _ = await _conector(client, db, make_user, auth_as)
    url = f"/api/claude-mcp/{token}/mcp"
    agora = datetime.now(UTC)
    db.add(SituacaoBling(id=21, nome="Em digitação"))
    db.add(SituacaoBling(id=12, nome="Cancelado"))
    # 295071: só o pedido, etiqueta emitida e prazo de envio vencido há 2h.
    db.add(BlingOrder(
        bling_id=2, numero="295071", situacao="21", loja="5001", data=agora, item_index=0,
        item_codigo="SKU2", item_descricao="Mala Y",
        marketplace_ship_deadline=agora - timedelta(hours=2),
    ))
    # 295072: cancelado com o mesmo prazo vencido → NÃO pode sair como "VENCIDO".
    db.add(BlingOrder(
        bling_id=3, numero="295072", situacao="12", loja="5001", data=agora, item_index=0,
        item_codigo="SKU3", item_descricao="Mala Z",
        marketplace_ship_deadline=agora - timedelta(hours=2),
    ))
    # 295073: em Aguardando Devolução, rastreio reverso já andando, devolução ainda não lançada;
    # chamado resolvido; margem sem decisão manual.
    db.add(BlingOrder(
        bling_id=4, numero="295073", situacao="21", loja="5001", data=agora, item_index=0,
        item_codigo="SKU4", item_descricao="Celular W", aguardando_devolucao_data=agora.date(),
    ))
    db.add(DevolucaoRastreio(
        pedido_bling="295073", rastreio_auto="QB2BR", localizacao_auto="Objeto em trânsito",
        localizacao_auto_data=agora, devolucao_status_auto="A caminho do vendedor",
    ))
    db.add(Chamado(pedido_bling="295073", origem="manual", canal="manual", data=agora.date(),
                   resolvido=True))
    await db.execute(sql(
        "INSERT INTO verificar_margem (bling_order_item_id, pedido_bling, sku, "
        "marketplace_margem, marketplace_lucro) VALUES (:id, '295073', 'SKU4', 0.0825, 90)"
    ).bindparams(id=uuid4()))
    await db.commit()

    r = await client.post(url, json=_tool(1, "consultar_pedido", pedido="295071"))
    texto = r.json()["result"]["content"][0]["text"]
    assert "Pedido Bling 295071" in texto and "situação Bling: Em digitação" in texto
    assert "despachar até" in texto and "PRAZO DE ENVIO VENCIDO, sem despacho registrado" in texto
    assert "Logística: pedido não está no painel de Logística." in texto
    assert "Chamados: nenhum." in texto and "Devolução: nenhuma." in texto
    assert "Margem: pedido não está na tela de Margem." in texto

    r = await client.post(url, json=_tool(2, "consultar_pedido", pedido="295072"))
    texto = r.json()["result"]["content"][0]["text"]
    assert "situação Bling: Cancelado" in texto and "despachar até" in texto
    assert "VENCIDO" not in texto

    r = await client.post(url, json=_tool(3, "consultar_pedido", pedido="295073"))
    texto = r.json()["result"]["content"][0]["text"]
    assert "em Aguardando Devolução desde" in texto
    assert "Devolução: ainda não lançada na aba Devoluções" in texto
    assert "Devolução: nenhuma." not in texto
    assert "Rastreio da devolução: rastreio QB2BR · última localização: Objeto em trânsito" in texto
    assert "status da devolução: A caminho do vendedor" in texto
    assert "RESOLVIDO" in texto and "ABERTO" not in texto
    assert (
        "Margem SKU SKU4 · sem decisão manual (ver status na aba Margem) · "
        "plataforma 8.2% (lucro R$ 90,00)"
    ) in texto
