"""Sistema › Mudanças (17/11/2026): o chefe pede pelo Claude, o robô do Mac faz e
testa, só Eduardo/Thorfinn aprovam, o robô publica.

Trava:
  • pedir_mudanca/listar_mudancas no conector (só admin);
  • a tela é invisível (404, qualquer método/corpo) para quem não aprova —
    inclusive admin; o /api/auth/me só traz `mudancas` para quem aprova;
  • o robô precisa do token próprio; um trabalho de cada vez; publicar
    antes de fazer; prazo vencido devolve (fazendo) ou falha (publicando);
  • a aprovação vale para O diff visto (diff_sha); recusa exige motivo;
  • avisos: pronta → aprovadores; aprovada/recusada/publicada → quem pediu.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import SistemaMudanca, SistemaMudancaAprovador, SistemaMudancaRobo, UserRole
from app.services import claude_tarefas
from app.services import sistema_mudancas as svc
from app.services.claude_tarefas import TarefaInvalidaError

pytestmark = pytest.mark.asyncio

TOKEN = "robo-teste-123"  # noqa: S105 — token falso de teste
ROBO = "mac-eduardo"
DIFF = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n"
C1 = "a" * 40
C2 = "b" * 40


@pytest.fixture
def avisos(monkeypatch):
    lista: list[dict] = []

    async def _emit(session, **kw):
        lista.append(kw)
        return None

    monkeypatch.setattr(svc, "emit_alert", _emit)
    return lista


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(get_settings(), "mudancas_agent_token", TOKEN)
    return {"X-Mudancas-Token": TOKEN}


async def _gente(db: AsyncSession, make_user):
    chefe = await make_user(role=UserRole.ADMIN, email="chefe@davinci-test.com")
    chefe.name = "harry potter"
    eduardo = await make_user(role=UserRole.ADMIN, email="heisenberg@davinci-test.com")
    eduardo.name = "heisenberg"
    outro_admin = await make_user(role=UserRole.ADMIN, email="joana@davinci-test.com")
    comum = await make_user(email="comum@davinci-test.com")
    db.add(SistemaMudancaAprovador(user_id=eduardo.id))
    await db.commit()
    return chefe, eduardo, outro_admin, comum


async def _pedido(db: AsyncSession, chefe, titulo="Trocar texto do botão") -> SistemaMudanca:
    m = await svc.criar_pedido(
        db, dono=chefe, titulo=titulo, descricao="O botão diz X; deve dizer Y.", onde="Logística"
    )
    await db.commit()
    return m


async def _recarrega(db: AsyncSession, m: SistemaMudanca) -> SistemaMudanca:
    await db.refresh(m)  # o que a API gravou (outra sessão)
    return m


# ── conector ──────────────────────────────────────────────────────────────


async def test_pedir_e_listar_pelo_conector(db, make_user, avisos):
    chefe, _eduardo, _outro, comum = await _gente(db, make_user)
    texto = await claude_tarefas.pedir_mudanca(
        db,
        dono=chefe,
        args={"titulo": "Mudar texto", "descricao": "Hoje diz A, deve dizer B", "onde": "NF"},
    )
    assert "M-" in texto and "Eduardo ou o Thorfinn aprovarem" in texto
    m = (await db.execute(select(SistemaMudanca))).scalar_one()
    assert (m.status, m.titulo, m.onde, m.pedido_por) == ("pedida", "Mudar texto", "NF", chefe.id)

    lista = await claude_tarefas.listar_mudancas(db, dono=chefe, args={})
    assert f"M-{m.numero} — Mudar texto — Na fila do robô" in lista
    so = await claude_tarefas.listar_mudancas(db, dono=chefe, args={"codigo": f"m{m.numero}"})
    assert so.startswith(f"M-{m.numero}")

    with pytest.raises(TarefaInvalidaError):
        await claude_tarefas.pedir_mudanca(db, dono=comum, args={"titulo": "x", "descricao": "y"})
    with pytest.raises(TarefaInvalidaError):
        await claude_tarefas.listar_mudancas(db, dono=comum, args={})
    with pytest.raises(TarefaInvalidaError):  # descrição vazia
        await claude_tarefas.pedir_mudanca(db, dono=chefe, args={"titulo": "x", "descricao": " "})


# ── a tela: só aprovador ──────────────────────────────────────────────────


async def test_tela_invisivel_para_quem_nao_aprova(client: AsyncClient, db, make_user, auth_as):
    chefe, eduardo, outro_admin, comum = await _gente(db, make_user)
    m = await _pedido(db, chefe)
    for quem in (chefe, outro_admin, comum):
        auth_as(quem)
        assert (await client.get("/api/sistema-mudancas")).status_code == 404
        assert (await client.get(f"/api/sistema-mudancas/{m.id}")).status_code == 404
        r = await client.post(f"/api/sistema-mudancas/{m.id}/aprovar", content=b"lixo")
        assert r.status_code == 404
        r = await client.post(f"/api/sistema-mudancas/{m.id}/recusar", json={})
        assert r.status_code == 404
        assert (await client.delete(f"/api/sistema-mudancas/{m.id}")).status_code == 404
        for metodo in ("POST", "PUT", "OPTIONS", "HEAD"):
            r = await client.request(metodo, "/api/sistema-mudancas")
            assert r.status_code == 404, metodo
        me = (await client.get("/api/auth/me")).json()
        assert "mudancas" not in me
    auth_as(eduardo)
    r = await client.get("/api/sistema-mudancas", params={"status": "abertas"})
    assert r.status_code == 200
    [item] = r.json()["itens"]
    assert item["codigo"] == f"M-{m.numero}" and item["status_texto"] == "Na fila do robô"
    assert item["pedido_por_nome"] == "harry potter"
    assert (await client.get("/api/auth/me")).json()["mudancas"] is True
    d = (await client.get(f"/api/sistema-mudancas/{m.id}")).json()
    assert d["pode_aprovar"] is False  # ainda não está pronta
    assert (await client.get("/api/sistema-mudancas/nao-e-uuid")).status_code == 404


# ── o robô ────────────────────────────────────────────────────────────────


async def test_robo_sem_token_e_404(client: AsyncClient, db, make_user, monkeypatch):
    """Sem o token (ou com o errado) as rotas do robô nem existem (404)."""
    await _gente(db, make_user)
    monkeypatch.setattr(get_settings(), "mudancas_agent_token", "")
    r = await client.post(
        "/api/sistema-mudancas-robo/pegar", json={"robo": ROBO}, headers={"X-Mudancas-Token": "x"}
    )
    assert r.status_code == 404
    monkeypatch.setattr(get_settings(), "mudancas_agent_token", TOKEN)
    r = await client.post("/api/sistema-mudancas-robo/pegar", json={"robo": ROBO})
    assert r.status_code == 404
    r = await client.post(
        "/api/sistema-mudancas-robo/pegar", json={"robo": ROBO}, headers={"X-Mudancas-Token": "y"}
    )
    assert r.status_code == 404
    for metodo in ("GET", "PUT", "OPTIONS", "HEAD"):
        r = await client.request(metodo, "/api/sistema-mudancas-robo/pegar")
        assert r.status_code == 404, metodo
    r = await client.get("/api/sistema-mudancas-robo/qualquer", headers={"X-Mudancas-Token": TOKEN})
    assert r.status_code == 404


async def _pegar(client, token) -> dict | None:
    r = await client.post("/api/sistema-mudancas-robo/pegar", json={"robo": ROBO}, headers=token)
    assert r.status_code == 200, r.text
    return r.json()["trabalho"]


async def _resultado(client, token, mid, **dados):
    return await client.post(
        f"/api/sistema-mudancas-robo/{mid}/resultado",
        json={"robo": ROBO, **dados},
        headers=token,
    )


async def test_caminho_completo_pedido_ate_o_ar(
    client: AsyncClient, db, make_user, auth_as, avisos, token
):
    chefe, eduardo, _outro, _comum = await _gente(db, make_user)
    m = await _pedido(db, chefe)

    t = await _pegar(client, token)
    assert t["acao"] == "fazer" and t["codigo"] == f"M-{m.numero}" and t["tentativa"] == 1
    assert (await _pegar(client, token))["id"] == t["id"]  # retoma o próprio
    outro = await client.post(
        "/api/sistema-mudancas-robo/pegar", json={"robo": "outro-mac"}, headers=token
    )
    assert outro.json()["trabalho"] is None  # um de cada vez
    r = await client.post(
        f"/api/sistema-mudancas-robo/{m.id}/vivo", json={"robo": ROBO}, headers=token
    )
    assert r.json()["status"] == "fazendo"

    r = await _resultado(
        client,
        token,
        m.id,
        status="pronta",
        resumo="Troquei o texto do botão.",
        testes="3 passed",
        diff=DIFF,
        branch=f"mudanca/M-{m.numero}",
        commit=C1,
        arquivos=[{"caminho": "x.py", "mais": 1, "menos": 1}, "lixo"],
    )
    assert r.status_code == 200, r.text
    m = await _recarrega(db, m)
    assert m.status == "pronta" and m.diff_sha == svc.sha_do_diff(DIFF) and m.robo is None
    assert m.arquivos == [{"caminho": "x.py", "mais": 1, "menos": 1}]
    assert [a["user_id"] for a in avisos] == [eduardo.id]
    assert "pronta para aprovar" in avisos[0]["title"]

    auth_as(eduardo)
    d = (await client.get(f"/api/sistema-mudancas/{m.id}")).json()
    assert d["pode_aprovar"] is True and d["diff"] == DIFF and d["resumo"]
    r = await client.post(f"/api/sistema-mudancas/{m.id}/aprovar", json={"diff_sha": "outro"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "diff_mudou"
    r = await client.post(f"/api/sistema-mudancas/{m.id}/aprovar", json={"diff_sha": d["diff_sha"]})
    assert r.status_code == 200 and r.json()["status"] == "aprovada"
    assert r.json()["aprovado_por_nome"] == "heisenberg"
    assert avisos[-1]["user_id"] == chefe.id and "aprovada" in avisos[-1]["title"]
    r = await client.post(f"/api/sistema-mudancas/{m.id}/aprovar", json={"diff_sha": d["diff_sha"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "nao_esta_pronta"

    outro = await _pedido(db, chefe, "Outro pedido")
    t = await _pegar(client, token)
    assert t["acao"] == "publicar" and t["commit"] == C1  # publicar antes de fazer
    r = await _resultado(client, token, m.id, status="publicada", commit=C2)
    assert r.status_code == 200
    m = await _recarrega(db, m)
    assert m.status == "publicada" and m.publicado_em and m.commit == C2
    assert {a["user_id"] for a in avisos[-2:]} == {chefe.id, eduardo.id}
    t = await _pegar(client, token)
    assert t["codigo"] == f"M-{outro.numero}" and t["acao"] == "fazer"


async def test_recusar_exige_motivo_e_avisa_quem_pediu(
    client: AsyncClient, db, make_user, auth_as, avisos, token
):
    chefe, eduardo, _o, _c = await _gente(db, make_user)
    m = await _pedido(db, chefe)
    await _pegar(client, token)
    await _resultado(
        client, token, m.id, status="pronta", diff=DIFF, branch=f"mudanca/M-{m.numero}", commit=C1
    )
    auth_as(eduardo)
    r = await client.post(f"/api/sistema-mudancas/{m.id}/recusar", json={"motivo": "x"})
    assert r.status_code == 422
    r = await client.post(
        f"/api/sistema-mudancas/{m.id}/recusar", json={"motivo": "Mexe na NF, não é rápido"}
    )
    assert r.status_code == 200 and r.json()["status"] == "recusada"
    assert r.json()["motivo_recusa"] == "Mexe na NF, não é rápido"
    assert avisos[-1]["user_id"] == chefe.id and "não é rápido" in avisos[-1]["message"]
    await db.refresh(m)
    texto = await claude_tarefas.listar_mudancas(db, dono=chefe, args={"filtro": "todas"})
    assert "recusada por heisenberg: Mexe na NF" in texto


async def test_robo_valida_o_que_manda(client: AsyncClient, db, make_user, avisos, token):
    chefe, _e, _o, _c = await _gente(db, make_user)
    m = await _pedido(db, chefe)
    await _pegar(client, token)
    r = await client.post(
        f"/api/sistema-mudancas-robo/{m.id}/resultado",
        json={"robo": "outro-mac", "status": "pronta"},
        headers=token,
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "nao_e_seu"
    r = await _resultado(client, token, m.id, status="publicada")
    assert r.status_code == 422  # de "fazendo" não vai para "publicada"
    r = await _resultado(
        client, token, m.id, status="pronta", diff="", branch=f"mudanca/M-{m.numero}", commit=C1
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "sem_diff"
    # commit com cara de comando ou ramo de outro pedido: recusado
    for commit, ramo in (("HEAD;curl x|sh;#", f"mudanca/M-{m.numero}"), (C1, "origin/main")):
        r = await _resultado(
            client, token, m.id, status="pronta", diff=DIFF, branch=ramo, commit=commit
        )
        assert r.status_code == 409 and r.json()["detail"]["code"] == "sem_commit"
    r = await _resultado(
        client, token, m.id, status="precisa_equipe", resumo="Precisa de migration"
    )
    assert r.status_code == 200
    m = await _recarrega(db, m)
    assert m.status == "precisa_equipe" and m.erro == "Precisa de migration"
    assert any("precisa da equipe" in a["title"] for a in avisos)


async def test_robo_que_para_no_meio(client: AsyncClient, db, make_user, avisos, token):
    chefe, _e, _o, _c = await _gente(db, make_user)
    m = await _pedido(db, chefe)
    passado = datetime.now(UTC) - timedelta(minutes=1)

    async def _vence():
        await db.execute(
            update(SistemaMudanca).where(SistemaMudanca.id == m.id).values(prazo_robo=passado)
        )
        await db.commit()

    assert (await _pegar(client, token))["tentativa"] == 1
    await _vence()
    t = await _pegar(client, token)  # volta para a fila e é pego de novo
    assert t["tentativa"] == 2
    await _vence()
    assert (await _pegar(client, token))["tentativa"] == 3
    await _vence()
    assert await _pegar(client, token) is None
    m = await _recarrega(db, m)
    assert m.status == "falhou" and "parou no meio" in (m.erro or "")

    # publicando vencido nunca repete sozinho: falha para uma pessoa conferir
    p = await _pedido(db, chefe, "Outra")
    await db.execute(
        update(SistemaMudanca)
        .where(SistemaMudanca.id == p.id)
        .values(status="publicando", robo="outro-robo", prazo_robo=passado, commit=C1)
    )
    await db.commit()
    await _pegar(client, token)
    p = await _recarrega(db, p)
    assert p.status == "falhou" and "publicação" in (p.erro or "")


async def test_publicar_ocupado_volta_para_a_fila(
    client: AsyncClient, db, make_user, avisos, token
):
    chefe, _e, _o, _c = await _gente(db, make_user)
    m = await _pedido(db, chefe)
    await db.execute(
        update(SistemaMudanca)
        .where(SistemaMudanca.id == m.id)
        .values(status="aprovada", aprovado_em=datetime.now(UTC), commit=C1, diff=DIFF)
    )
    await db.commit()
    assert (await _pegar(client, token))["acao"] == "publicar"
    r = await _resultado(client, token, m.id, status="aprovada", erro="outro deploy rodando")
    assert r.status_code == 200
    m = await _recarrega(db, m)
    assert m.status == "aprovada" and m.erro == "outro deploy rodando"
    assert await _pegar(client, token) is None  # adiada: espera 10 min
    await db.execute(
        update(SistemaMudanca)
        .where(SistemaMudanca.id == m.id)
        .values(atualizado_em=datetime.now(UTC) - timedelta(minutes=11))
    )
    await db.commit()
    assert (await _pegar(client, token))["acao"] == "publicar"


async def test_robo_retoma_o_proprio_trabalho_e_a_tela_ve_quando_ele_passou(
    client: AsyncClient, db, make_user, auth_as, avisos, token
):
    chefe, eduardo, _o, _c = await _gente(db, make_user)
    m = await _pedido(db, chefe)
    t = await _pegar(client, token)
    # o Mac reiniciou no meio: o mesmo robô pergunta de novo e recebe o mesmo
    t2 = await _pegar(client, token)
    assert t2 and t2["id"] == t["id"]
    # outro robô não pega nada enquanto este trabalha
    r = await client.post(
        "/api/sistema-mudancas-robo/pegar", json={"robo": "outro"}, headers=token
    )
    assert r.json()["trabalho"] is None
    auth_as(eduardo)
    lista = (await client.get("/api/sistema-mudancas")).json()
    assert lista["robo_visto_em"] and lista["robo_parado"] is False
    m = await _recarrega(db, m)
    assert m.tentativas == 2  # retomar conta tentativa (robô que cai sempre não fica em laço)
    # erro do ambiente: volta para a fila (até MAX_TENTATIVAS) e espera 10 min
    r = await _resultado(client, token, m.id, status="pedida", erro="GitHub fora")
    assert r.status_code == 200
    m = await _recarrega(db, m)
    assert m.status == "pedida" and m.erro == "GitHub fora"
    assert await _pegar(client, token) is None
    await db.execute(
        update(SistemaMudanca)
        .where(SistemaMudanca.id == m.id)
        .values(atualizado_em=datetime.now(UTC) - timedelta(minutes=11))
    )
    await db.commit()
    assert (await _pegar(client, token))["id"] == str(m.id)
    await _resultado(client, token, m.id, status="pedida", erro="GitHub fora de novo")
    m = await _recarrega(db, m)
    # erro do ambiente não gasta tentativa: uma queda do GitHub não derruba a fila
    assert m.status == "pedida" and m.tentativas == 1
    # ...mas o pedido não fica na fila para sempre: passou de 12 h, falha
    await db.execute(
        update(SistemaMudanca)
        .where(SistemaMudanca.id == m.id)
        .values(
            criado_em=datetime.now(UTC) - timedelta(hours=13),
            atualizado_em=datetime.now(UTC) - timedelta(minutes=11),
        )
    )
    await db.commit()
    assert (await _pegar(client, token))["id"] == str(m.id)
    await _resultado(client, token, m.id, status="pedida", erro="GitHub fora ainda")
    m = await _recarrega(db, m)
    assert m.status == "falhou" and "12 h" in (m.erro or "")
    # robô sumido com pedido esperando = "robô parado" na tela
    await _pedido(db, chefe, titulo="Outro pedido")
    await db.execute(
        update(SistemaMudancaRobo).values(visto_em=datetime.now(UTC) - timedelta(minutes=30))
    )
    await db.commit()
    lista = (await client.get("/api/sistema-mudancas")).json()
    assert lista["robo_parado"] is True


async def test_robo_que_recomeca_sempre_falha_no_teto(
    client: AsyncClient, db, make_user, avisos, token
):
    chefe, *_ = await _gente(db, make_user)
    m = await _pedido(db, chefe)
    for _ in range(3):  # pegar + 2 retomadas = 3 tentativas
        assert (await _pegar(client, token))["id"] == str(m.id)
    assert await _pegar(client, token) is None  # a 4ª passa do teto
    m = await _recarrega(db, m)
    assert m.status == "falhou" and "vezes demais" in (m.erro or "")


async def test_publicacao_adiada_demais_avisa(client: AsyncClient, db, make_user, avisos, token):
    chefe, eduardo, _o, _c = await _gente(db, make_user)
    m = await _pedido(db, chefe)
    for vez in range(svc.AVISO_ADIAMENTOS):
        await db.execute(
            update(SistemaMudanca)
            .where(SistemaMudanca.id == m.id)
            .values(
                status="aprovada",
                aprovado_em=datetime.now(UTC),
                commit=C1,
                diff=DIFF,
                erro=None,
                atualizado_em=datetime.now(UTC) - timedelta(minutes=11),
            )
        )
        await db.commit()
        assert (await _pegar(client, token))["acao"] == "publicar", vez
        await _resultado(client, token, m.id, status="aprovada", erro="disco cheio")
    m = await _recarrega(db, m)
    assert m.adiamentos == svc.AVISO_ADIAMENTOS
    parados = [a for a in avisos if "publicação está parada" in a["title"]]
    assert {a["user_id"] for a in parados} == {eduardo.id, chefe.id}
    eduardo_aviso = next(a for a in parados if a["user_id"] == eduardo.id)
    chefe_aviso = next(a for a in parados if a["user_id"] == chefe.id)
    assert "link" in eduardo_aviso["payload"] and "link" not in chefe_aviso["payload"]
    texto = await claude_tarefas.listar_mudancas(db, dono=chefe, args={"codigo": f"M-{m.numero}"})
    assert "publicação adiada: disco cheio" in texto
    # o aviso se repete a cada AVISO_ADIAMENTOS (ninguém esquece uma aprovada parada)
    antes = len(parados)
    for _vez in range(svc.AVISO_ADIAMENTOS):
        await db.execute(
            update(SistemaMudanca)
            .where(SistemaMudanca.id == m.id)
            .values(status="aprovada", atualizado_em=datetime.now(UTC) - timedelta(minutes=11))
        )
        await db.commit()
        await _pegar(client, token)
        await _resultado(client, token, m.id, status="aprovada", erro="disco cheio")
    assert len([a for a in avisos if "publicação está parada" in a["title"]]) == antes * 2


async def test_cancelar_aprovacao_parada(
    client: AsyncClient, db, make_user, auth_as, avisos, token
):
    chefe, eduardo, outro_admin, _c = await _gente(db, make_user)
    m = await _pedido(db, chefe)
    await db.execute(
        update(SistemaMudanca)
        .where(SistemaMudanca.id == m.id)
        .values(status="aprovada", aprovado_em=datetime.now(UTC), commit=C1, diff=DIFF)
    )
    await db.commit()
    # quem não aprova nem sabe que a rota existe
    auth_as(outro_admin)
    r = await client.post(f"/api/sistema-mudancas/{m.id}/cancelar", json={"motivo": "não quero"})
    assert r.status_code == 404
    auth_as(eduardo)
    detalhe = (await client.get(f"/api/sistema-mudancas/{m.id}")).json()
    assert detalhe["pode_cancelar"] is True and detalhe["pode_aprovar"] is False
    r = await client.post(f"/api/sistema-mudancas/{m.id}/cancelar", json={"motivo": "x"})
    assert r.status_code == 422  # motivo curto
    r = await client.post(
        f"/api/sistema-mudancas/{m.id}/cancelar", json={"motivo": "o pedido não vale mais"}
    )
    assert r.status_code == 200, r.text
    m = await _recarrega(db, m)
    assert m.status == "recusada" and m.motivo_recusa.startswith("Aprovação cancelada")
    assert any("aprovação cancelada" in a["title"] and a["user_id"] == chefe.id for a in avisos)
    assert await _pegar(client, token) is None  # o robô não publica mais
    # publicando (robô já no meio) não se cancela
    p = await _pedido(db, chefe, "Outra")
    await db.execute(
        update(SistemaMudanca)
        .where(SistemaMudanca.id == p.id)
        .values(status="publicando", robo="mac-eduardo", commit=C1, diff=DIFF)
    )
    await db.commit()
    r = await client.post(
        f"/api/sistema-mudancas/{p.id}/cancelar", json={"motivo": "tarde demais"}
    )
    assert r.status_code == 409


def test_invisiveis_acha_trojan_source():
    limpo = "+++ b/x.py\n+x = 'Olá, mundo'\t# ok\n-velho"
    assert svc.invisiveis(limpo) == []
    sujo = "+++ b/x.py\n+if ok: # \u202e } \u2066 return True\n+a\u200bb = 1\n"
    assert svc.invisiveis(sujo) == ["U+200B", "U+202E", "U+2066"]
    # só as linhas acrescentadas contam (tirar um caractere invisível é bom)
    assert svc.invisiveis("-x = '\u202e'\n") == []


async def test_robo_nao_entrega_diff_com_caractere_invisivel(
    client: AsyncClient, db, make_user, avisos, token
):
    chefe, *_ = await _gente(db, make_user)
    m = await _pedido(db, chefe)
    await _pegar(client, token)
    sujo = DIFF.replace("+", "+\u202e", 1)
    r = await _resultado(
        client, token, m.id, status="pronta", diff=sujo, commit=C1, branch=f"mudanca/M-{m.numero}"
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "diff_invisivel"


async def test_pergunta_vai_direto_para_quem_perguntou(
    client: AsyncClient, db, make_user, auth_as, avisos, token
):
    """P-N: o robô só lê o código e a resposta vai direto para quem perguntou,
    sem aprovação (Eduardo, 17/11)."""
    chefe, eduardo, _o, comum = await _gente(db, make_user)
    texto = await claude_tarefas.perguntar_sobre_o_sistema(
        db, dono=chefe, args={"pergunta": "Como a Margem decide segurar um pedido?"}
    )
    assert "Pergunta P-" in texto and "alguns minutos" in texto
    m = (await db.execute(select(SistemaMudanca))).scalar_one()
    assert m.tipo == "pergunta" and svc.codigo(m) == f"P-{m.numero}"
    # quem não é admin não pergunta
    with pytest.raises(TarefaInvalidaError):
        await claude_tarefas.perguntar_sobre_o_sistema(db, dono=comum, args={"pergunta": "x"})
    # ainda sem resposta
    ainda = await claude_tarefas.ver_resposta_do_sistema(
        db, dono=chefe, args={"codigo": f"P-{m.numero}"}
    )
    assert "ainda não foi respondida" in ainda
    # o robô pega como "responder"
    t = await _pegar(client, token)
    assert t["acao"] == "responder" and t["tipo"] == "pergunta" and t["codigo"] == f"P-{m.numero}"
    # pergunta não vira "pronta" (nada de aprovação)
    r = await _resultado(
        client, token, m.id, status="pronta", diff=DIFF, commit=C1, branch=f"mudanca/P-{m.numero}"
    )
    assert r.status_code == 422
    r = await _resultado(client, token, m.id, status="respondida", resposta="")
    assert r.status_code == 409
    resposta = "A Margem segura o pedido quando...\n\n```python\nif margem < 0: ...\n```"
    r = await _resultado(client, token, m.id, status="respondida", resposta=resposta)
    assert r.status_code == 200, r.text
    m = await _recarrega(db, m)
    assert m.status == "respondida" and m.resposta == resposta and m.aprovado_por is None
    # aviso só para quem perguntou (nenhum aprovador)
    resp_avisos = [a for a in avisos if "respondida" in a["title"]]
    assert {a["user_id"] for a in resp_avisos} == {chefe.id}
    # o Claude do chefe lê a resposta inteira
    texto = await claude_tarefas.ver_resposta_do_sistema(
        db, dono=chefe, args={"codigo": f"P-{m.numero}"}
    )
    assert resposta in texto
    lista = await claude_tarefas.listar_mudancas(db, dono=chefe, args={"filtro": "todas"})
    assert "(pergunta)" in lista and "Respondida" in lista and "ver_resposta_do_sistema" in lista
    # aprovadores veem na tela (histórico), sem botão de aprovar
    auth_as(eduardo)
    d = (await client.get(f"/api/sistema-mudancas/{m.id}")).json()
    assert d["tipo"] == "pergunta" and d["resposta"] == resposta and d["pode_aprovar"] is False
    # resposta longa demais é cortada, não recusada
    p2 = await svc.criar_pergunta(db, dono=chefe, pergunta="Outra pergunta")
    await db.commit()
    await _pegar(client, token)
    r = await _resultado(
        client, token, p2.id, status="respondida", resposta="x" * (svc.MAX_RESPOSTA + 500)
    )
    assert r.status_code == 200
    p2 = await _recarrega(db, p2)
    assert len(p2.resposta) <= svc.MAX_RESPOSTA and p2.resposta.endswith("longa demais]")


async def test_pergunta_que_falha_avisa_so_quem_perguntou(
    client: AsyncClient, db, make_user, avisos, token
):
    chefe, eduardo, _o, _c = await _gente(db, make_user)
    m = await svc.criar_pergunta(db, dono=chefe, pergunta="Como funciona o Flex?")
    await db.commit()
    await _pegar(client, token)
    r = await _resultado(client, token, m.id, status="falhou", erro="o Claude do robô deu erro")
    assert r.status_code == 200
    m = await _recarrega(db, m)
    assert m.status == "falhou"
    falhas = [a for a in avisos if "não conseguiu responder" in a["title"]]
    assert {a["user_id"] for a in falhas} == {chefe.id}
    texto = await claude_tarefas.ver_resposta_do_sistema(
        db, dono=chefe, args={"codigo": f"P-{m.numero}"}
    )
    assert "não conseguiu responder" in texto


async def test_pergunta_passa_na_frente_dos_pedidos_de_mudanca(
    client: AsyncClient, db, make_user, avisos, token
):
    chefe, *_ = await _gente(db, make_user)
    m = await _pedido(db, chefe)
    p = await svc.criar_pergunta(db, dono=chefe, pergunta="Como funciona o Flex?")
    await db.commit()
    assert (await _pegar(client, token))["id"] == str(p.id)  # a pergunta, criada depois, sai antes
    await _resultado(client, token, p.id, status="respondida", resposta="Assim.")
    assert (await _pegar(client, token))["id"] == str(m.id)
