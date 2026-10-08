"""Conferência Shopee — as rotas (routers/marketing_conferencia).

Executor: X-Agent-Token (vazio nas settings = fechado; não-ASCII = 401, não
500), lease e resultado pelo HTTP, corpo acima de 3 MB = 413 (pelo
Content-Length e contando o que chega), 404/409/422, e a última loja fechando
a rodada na mesma chamada. Tela: Marketing ver × editar (anônimo 401),
"Gerar agora" (409 com outra coletando), lista com placar, detalhe, recalcular,
cancelar, os 5 arquivos, o link do Excel sem login (válido, vencido, mexido,
de outra execução), a lista de lojas e o token fora do access log.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.config import get_settings
from app.models import ConferenciaShopeeConta, ConferenciaShopeeExecucao, UserRole
from app.routers import marketing_conferencia as mc
from app.services.conferencia_shopee import fila, threema_aviso
from tests.test_conferencia_shopee_fila import AGORA, coletas_de, dados_loja, semear_contas

pytestmark = pytest.mark.asyncio

API = "/api/marketing/conferencia-shopee"
TOKEN = "tok-conferencia-teste"  # noqa: S105 (só do teste)
AGENTE = {"X-Agent-Token": TOKEN}


@pytest.fixture(scope="module", autouse=True)
def _monta_router():
    """`app/main.py` só inclui os routers de Marketing com `enable_marketing`
    ligado (o ambiente de teste não liga): sem isto tudo daqui seria 404."""
    from app.main import app

    if not any(getattr(r, "path", "").startswith(API) for r in app.routes):
        app.include_router(mc.router)


@pytest.fixture(autouse=True)
def _relogio_e_token(monkeypatch):
    monkeypatch.setattr(mc, "_agora", lambda: AGORA)
    monkeypatch.setattr(get_settings(), "marketing_agent_token", TOKEN)
    monkeypatch.setattr(get_settings(), "conferencia_shopee_threema", False)


@pytest.fixture
async def admin(make_user, auth_as):
    u = await make_user(role=UserRole.ADMIN)
    auth_as(u)
    return u


UMA = (("k-ana", "Ana", "mala", True, 0),)
DUAS = (*UMA, ("k-bia", "Bia", "celular", True, 1))


async def _execucao(db, contas=None) -> ConferenciaShopeeExecucao:
    if contas:
        await semear_contas(db, contas)
    else:
        await semear_contas(db)
    ex = await fila.criar_execucao(db, "semanal", "agenda", None, AGORA)
    await db.commit()
    return ex


async def _lease(client: AsyncClient) -> dict | None:
    r = await client.post(f"{API}/agent/lease", json={"agente": "mac-teste"}, headers=AGENTE)
    assert r.status_code == 200, r.text
    return r.json()["job"]


async def _pronta(db, client) -> ConferenciaShopeeExecucao:
    """Uma rodada com uma loja só (as outras da lista ficam inativas), já
    fechada pelo HTTP."""
    await db.execute(update(ConferenciaShopeeConta).values(ativo=False))
    db.add(ConferenciaShopeeConta(adspower_user_id="k-unica", nome="Única", grupo="mala",
                                  conta_key="unica"))
    await db.commit()
    ex = await fila.criar_execucao(db, "semanal", "agenda", None, AGORA)
    await db.commit()
    job = await _lease(client)
    r = await client.post(
        f"{API}/agent/coletas/{job['coleta_id']}/resultado",
        json={"status": "ok", "erro": None, "dados": dados_loja(ex.semanas)},
        headers=AGENTE,
    )
    assert r.json()["execucao_pronta"] is True
    await db.refresh(ex)
    return ex


# ───────────────────────────────────────────────────────────── executor


@pytest.mark.parametrize(
    "headers",
    [{}, {"X-Agent-Token": "outro"}, {"X-Agent-Token": "çãõ".encode("latin-1")}],
    ids=["sem", "errado", "nao_ascii"],
)
async def test_agente_sem_token_valido_401(client, db, headers):
    ex = await _execucao(db)
    coleta_id = (await coletas_de(db, ex.id))[0].id
    r = await client.post(f"{API}/agent/lease", json={"agente": "x"}, headers=headers)
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "agent_unauthorized"
    r = await client.post(
        f"{API}/agent/coletas/{coleta_id}/resultado", json={"status": "ok"}, headers=headers
    )
    assert r.status_code == 401


async def test_token_vazio_nas_settings_fecha(client, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "marketing_agent_token", "")
    r = await client.post(f"{API}/agent/lease", json={}, headers={"X-Agent-Token": ""})
    assert r.status_code == 401


async def test_lease_e_resultado_pelo_http_fecham_a_rodada(client, db, admin):
    ex = await _execucao(db, DUAS)
    ana = await _lease(client)
    assert ana["conta"] == "Ana" and ana["login_auto"] is True and ana["tentativa"] == 1
    assert ana["corte"] == "2026-10-06T20:30:00Z"
    bia = await _lease(client)
    assert await _lease(client) is None

    r = await client.post(
        f"{API}/agent/coletas/{ana['coleta_id']}/resultado",
        json={"status": "ok", "erro": None, "dados": dados_loja(ex.semanas)},
        headers=AGENTE,
    )
    assert r.status_code == 200, r.text
    assert r.json() == {
        "ok": True, "status": "ok", "reagendada": False, "disponivel_apos": None,
        "execucao_pronta": False,
    }
    # Enquanto coleta, o detalhe mostra o andamento e nada de relatório.
    d = (await client.get(f"{API}/execucoes/{ex.id}")).json()
    assert d["relatorio"] is None and d["execucao"]["status"] == "coletando"
    assert [(c["nome"], c["status"]) for c in d["coletas"]] == [
        ("Ana", "ok"), ("Bia", "coletando")
    ]

    r = await client.post(
        f"{API}/agent/coletas/{bia['coleta_id']}/resultado",
        json={"status": "sem_automacao", "erro": "perfil Firefox", "dados": None},
        headers=AGENTE,
    )
    assert r.json()["execucao_pronta"] is True
    d = (await client.get(f"{API}/execucoes/{ex.id}")).json()
    assert d["execucao"]["status"] == "pronto"
    assert d["execucao"]["finalizado_em"] is not None
    assert d["relatorio"]["execucao_id"] == str(ex.id)
    assert d["relatorio"]["contas_sem_dados"] == [
        {"conta": "Bia", "status": "sem_automacao", "erro": "perfil Firefox"}
    ]
    assert d["coletas"][1] == {
        "id": bia["coleta_id"], "conta_id": d["coletas"][1]["conta_id"], "nome": "Bia",
        "grupo": "celular", "status": "sem_automacao", "erro": "perfil Firefox",
        "tentativas": 1, "adiamentos": 0, "adiamentos_perfil": 0, "disponivel_apos": None,
        "concluido_em": d["coletas"][1]["concluido_em"],
    }


async def test_login_auto_vem_das_settings(client, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "conferencia_shopee_login_auto", False)
    await _execucao(db)
    assert (await _lease(client))["login_auto"] is False


async def test_resultado_perfil_em_uso_reagenda(client, db, admin):
    ex = await _execucao(db)
    job = await _lease(client)
    r = await client.post(
        f"{API}/agent/coletas/{job['coleta_id']}/resultado",
        json={"status": "perfil_em_uso", "erro": "perfil aberto"},
        headers=AGENTE,
    )
    corpo = r.json()
    assert (corpo["status"], corpo["reagendada"], corpo["execucao_pronta"]) == (
        "pendente", True, False
    )
    assert datetime.fromisoformat(corpo["disponivel_apos"]) == AGORA + timedelta(minutes=10)
    det = (await client.get(f"{API}/execucoes/{ex.id}")).json()
    c = next(c for c in det["coletas"] if c["id"] == job["coleta_id"])
    # `adiamentos_perfil` (só os de perfil em uso) é a cota de 3.
    assert (c["status"], c["adiamentos"], c["adiamentos_perfil"]) == ("pendente", 1, 1)


async def test_resultado_acima_de_3mb_413(client, db):
    ex = await _execucao(db)
    coleta_id = (await coletas_de(db, ex.id))[0].id
    url = f"{API}/agent/coletas/{coleta_id}/resultado"
    grande = json.dumps(
        {"status": "ok", "dados": {"lixo": "x" * (3 * 1024 * 1024)}}
    ).encode()
    json_ = {"content-type": "application/json"}
    r = await client.post(url, content=grande, headers={**AGENTE, **json_})
    assert r.status_code == 413
    assert r.json()["detail"]["code"] == "conferencia_payload_grande"

    # Sem Content-Length (chunked): conta o que chega.
    async def _pedacos():
        for i in range(0, len(grande), 512 * 1024):
            yield grande[i : i + 512 * 1024]

    r = await client.post(url, content=_pedacos(), headers={**AGENTE, **json_})
    assert r.status_code == 413
    # Sem token, o 401 vem antes de ler o corpo.
    r = await client.post(url, content=grande, headers=json_)
    assert r.status_code == 401
    # Nada foi gravado.
    assert (await coletas_de(db, ex.id))[0].status == "pendente"


async def test_resultado_404_409_422(client, db):
    await _execucao(db)
    r = await client.post(
        f"{API}/agent/coletas/{uuid.uuid4()}/resultado", json={"status": "ok"}, headers=AGENTE
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "coleta_nao_encontrada"

    job = await _lease(client)
    url = f"{API}/agent/coletas/{job['coleta_id']}/resultado"
    r = await client.post(url, json={"status": "expirada"}, headers=AGENTE)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "conferencia_payload_invalido"
    r = await client.post(
        url, content=b"{nao e json", headers={**AGENTE, "content-type": "application/json"}
    )
    assert r.status_code == 422
    async def _pedaco():  # corpo sem Content-Length também é lido
        yield json.dumps({"status": "erro", "erro": "x"}).encode()

    r = await client.post(
        url, content=_pedaco(), headers={**AGENTE, "content-type": "application/json"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "erro"
    r = await client.post(url, json={"status": "ok"}, headers=AGENTE)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "coleta_ja_concluida"


async def test_resultado_com_dados_tortos_422_e_nada_gravado(client, db):
    """Forma do ColetaDados v1 conferida na porta: um item torto guardado
    derrubaria o cálculo da rodada inteira."""
    ex = await _execucao(db, UMA)
    job = await _lease(client)
    url = f"{API}/agent/coletas/{job['coleta_id']}/resultado"

    def torto(mexe) -> dict:
        d = dados_loja(ex.semanas)
        mexe(d)
        return {"status": "ok", "dados": d}

    casos = {
        "item que não é objeto": (
            torto(lambda d: d["semanas"][0].update(afiliados_itens=["item-que-nao-e-objeto"])),
            ["dados", "semanas", 0, "afiliados_itens", 0],
        ),
        "semana que não é objeto": (
            torto(lambda d: d["semanas"].append("x")), ["dados", "semanas", 4],
        ),
        "número que não é número": (
            torto(lambda d: d["semanas"][1]["vendas"].update(valor="muito")),
            ["dados", "semanas", 1, "vendas", "valor"],
        ),
        "lista que não é lista": (
            torto(lambda d: d["semanas"][2].update(ads_itens={"a": 1})),
            ["dados", "semanas", 2, "ads_itens"],
        ),
        "nome que não é texto": (
            torto(lambda d: d["semanas"][0]["ads_itens"][0].update(nome=123)),
            ["dados", "semanas", 0, "ads_itens", 0, "nome"],
        ),
        "versão desconhecida": (torto(lambda d: d.update(versao=2)), ["dados", "versao"]),
        "sem semanas": (torto(lambda d: d.pop("semanas")), ["dados", "semanas"]),
        "avisos que não é lista": (torto(lambda d: d.update(avisos=3)), ["dados", "avisos"]),
        # Os cliques de afiliados (07/10/2026) são opcionais, mas número.
        "cliques de afiliados que não é número": (
            torto(lambda d: d["semanas"][0]["afiliados"].update(cliques="muitos")),
            ["dados", "semanas", 0, "afiliados", "cliques"],
        ),
        "cliques do item de afiliados que não é número": (
            torto(lambda d: d["semanas"][3]["afiliados_itens"][0].update(cliques=[1])),
            ["dados", "semanas", 3, "afiliados_itens", 0, "cliques"],
        ),
    }
    for nome, (corpo, onde) in casos.items():
        r = await client.post(url, json=corpo, headers=AGENTE)
        assert r.status_code == 422, (nome, r.text)
        det = r.json()["detail"]
        assert det["code"] == "conferencia_payload_invalido", nome
        assert any(e["loc"][: len(onde)] == onde for e in det["erros"]), (nome, det["erros"])
    (c,) = await coletas_de(db, ex.id)
    assert (c.status, c.dados) == ("coletando", None)  # nada gravado

    # Campo a mais não é defeito (executor mais novo): passa e guarda o dict cru.
    # Os cliques de afiliados (07/10/2026) entram no relatório.
    certo = dados_loja(ex.semanas) | {"novidade": {"x": 1}}
    certo["semanas"][0]["extra"] = [1, 2]
    for sem in certo["semanas"]:
        sem["afiliados"]["cliques"] = 400
        sem["afiliados_itens"][0]["cliques"] = 400
    r = await client.post(url, json={"status": "ok", "dados": certo}, headers=AGENTE)
    assert r.status_code == 200, r.text
    (c,) = await coletas_de(db, ex.id)
    assert c.status == "ok" and c.dados["novidade"] == {"x": 1}
    assert c.dados["semanas"][0]["afiliados"]["cliques"] == 400
    rel = (await db.get(ConferenciaShopeeExecucao, ex.id, populate_existing=True)).relatorio
    s1 = rel["geral"]["semanas"][0]
    assert (s1["cliques_afiliados"], s1["pedidos_afiliados"], s1["conversao_afiliados"]) == (
        400, 8, 2.0,
    )


async def test_fechamento_que_falha_nao_perde_o_resultado(client, db, monkeypatch):
    """Cálculo quebrado: a loja fica gravada (200) e o varredor fecha depois."""
    ex = await _execucao(db, UMA)
    job = await _lease(client)

    async def _quebra(*a, **k):
        raise RuntimeError("bug no cálculo")

    montar = fila.montar
    monkeypatch.setattr(fila, "montar", _quebra)
    r = await client.post(
        f"{API}/agent/coletas/{job['coleta_id']}/resultado",
        json={"status": "ok", "dados": dados_loja(ex.semanas)},
        headers=AGENTE,
    )
    assert r.status_code == 200
    assert (r.json()["status"], r.json()["execucao_pronta"]) == ("ok", False)
    (c,) = await coletas_de(db, ex.id)
    assert c.status == "ok" and c.dados is not None
    await db.refresh(ex)
    assert ex.status == "coletando"
    monkeypatch.setattr(fila, "montar", montar)
    fechadas = await fila.varrer(db, AGORA + timedelta(minutes=10))
    await db.commit()
    assert [e.id for e in fechadas] == [ex.id]


async def test_lease_que_esgota_tentativas_fecha_a_rodada(client, db):
    ex = await _execucao(db, UMA)
    (c,) = await coletas_de(db, ex.id)
    c.status, c.tentativas = "coletando", 3
    c.claimed_at = AGORA - timedelta(minutes=30)
    await db.commit()
    assert await _lease(client) is None
    await db.refresh(ex)
    assert ex.status == "pronto"


# ───────────────────────────────────────────────────────────── tela: permissões


async def test_anonimo_401(client, db, auth_as):
    auth_as(None)
    for metodo, url in (
        ("GET", f"{API}/execucoes"),
        ("POST", f"{API}/execucoes"),
        ("GET", f"{API}/execucoes/{uuid.uuid4()}"),
        ("GET", f"{API}/execucoes/{uuid.uuid4()}/arquivo/xlsx"),
        ("GET", f"{API}/contas"),
    ):
        corpo = {"tipo": "semanal"} if metodo == "POST" else None
        r = await client.request(metodo, url, json=corpo)
        assert r.status_code == 401, (metodo, url)


async def test_quem_so_ve_nao_mexe(client, db, make_user, auth_as):
    ex = await _execucao(db)
    conta = (await db.execute(select(ConferenciaShopeeConta))).scalars().first()
    auth_as(await make_user(permissions={"marketing": {"view": True}}))
    assert (await client.get(f"{API}/execucoes")).status_code == 200
    assert (await client.get(f"{API}/execucoes/{ex.id}")).status_code == 200
    assert (await client.get(f"{API}/contas")).status_code == 200
    for metodo, url, corpo in (
        ("POST", f"{API}/execucoes", {"tipo": "semanal"}),
        ("POST", f"{API}/execucoes/{ex.id}/recalcular", None),
        ("POST", f"{API}/execucoes/{ex.id}/cancelar", None),
        ("PUT", f"{API}/contas/{conta.id}", {"nome": "X"}),
    ):
        r = await client.request(metodo, url, json=corpo)
        assert r.status_code == 403, (metodo, url)
        assert r.json()["detail"] == {
            "code": "forbidden", "resource": "marketing", "action": "edit"
        }
    # Sem nada de Marketing: nem vê.
    auth_as(await make_user(permissions={}))
    assert (await client.get(f"{API}/execucoes")).status_code == 403


async def test_gerar_agora_e_409_com_outra_coletando(client, db, make_user, auth_as):
    await semear_contas(db)
    editor = await make_user(permissions={"marketing": {"view": True, "edit": True}})
    auth_as(editor)
    r = await client.post(f"{API}/execucoes", json={"tipo": "semanal"})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["relatorio"] is None
    assert corpo["execucao"]["origem"] == "manual"
    assert corpo["execucao"]["criado_por"] == editor.email
    assert corpo["execucao"]["semanas"][0] == {"inicio": "2026-09-28", "fim": "2026-10-04"}
    assert [c["nome"] for c in corpo["coletas"]] == ["Caio", "Ana", "Bia"]
    r = await client.post(f"{API}/execucoes", json={"tipo": "parcial"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "conferencia_em_andamento"
    assert (await client.post(f"{API}/execucoes", json={"tipo": "mensal"})).status_code == 422


async def test_lista_mais_nova_primeiro_com_placar(client, db, admin):
    velha = await _execucao(db)
    await fila.cancelar(db, velha, AGORA)
    await db.commit()
    nova = await fila.criar_execucao(db, "parcial", "manual", None, AGORA + timedelta(days=2))
    await db.commit()
    caio, ana, _bia = await coletas_de(db, nova.id)
    caio.status, ana.status = "ok", "deslogada"
    await db.commit()
    lista = (await client.get(f"{API}/execucoes?limite=30")).json()
    assert [e["id"] for e in lista] == [str(nova.id), str(velha.id)]
    assert lista[0]["resumo"] == {"contas": 3, "ok": 1, "sem_dados": 1}
    assert lista[1]["resumo"] == {"contas": 3, "ok": 0, "sem_dados": 3}  # expiradas
    assert lista[0]["tipo"] == "parcial" and lista[1]["status"] == "cancelado"
    assert set(lista[0]) == {
        "id", "plataforma", "tipo", "origem", "status", "criado_em", "finalizado_em", "semanas",
        "resumo",
    }
    assert lista[0]["plataforma"] == "shopee"
    assert len((await client.get(f"{API}/execucoes?limite=1")).json()) == 1


async def test_detalhe_404(client, db, admin):
    r = await client.get(f"{API}/execucoes/{uuid.uuid4()}")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "conferencia_nao_encontrada"


async def test_recalcular_e_cancelar(client, db, admin):
    ex = await _execucao(db)
    r = await client.post(f"{API}/execucoes/{ex.id}/recalcular")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "conferencia_nao_pronta"
    r = await client.post(f"{API}/execucoes/{ex.id}/cancelar")
    assert r.status_code == 200
    assert r.json()["execucao"]["status"] == "cancelado"
    assert {c["status"] for c in r.json()["coletas"]} == {"expirada"}
    r = await client.post(f"{API}/execucoes/{ex.id}/cancelar")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "conferencia_nao_coletando"
    assert (await client.post(f"{API}/execucoes/{uuid.uuid4()}/cancelar")).status_code == 404

    pronta = await _pronta(db, client)
    r = await client.post(f"{API}/execucoes/{pronta.id}/recalcular")
    assert r.status_code == 200
    assert r.json()["relatorio"]["gerado_em"] == AGORA.isoformat()
    # Coleta de antes de 07/10/2026 (sem os cliques de afiliados): recalcula
    # sem quebrar — cliques e conversão de afiliados vazios, nunca 0; os
    # pedidos e a conversão de Ads (que sempre vieram) aparecem.
    s1 = r.json()["relatorio"]["geral"]["semanas"][0]
    assert (s1["cliques_afiliados"], s1["conversao_afiliados"]) == (None, None)
    assert s1["pedidos_afiliados"] == 8
    assert (s1["cliques_ads"], s1["pedidos_ads"], s1["conversao_ads"]) == (100, 6, 6.0)


# ───────────────────────────────────────────────────────────── arquivos


async def test_arquivos(client, db, admin):
    ex = await _execucao(db)
    r = await client.get(f"{API}/execucoes/{ex.id}/arquivo/xlsx")
    assert r.status_code == 404  # coletando: ainda não há relatório
    assert r.json()["detail"]["code"] == "conferencia_sem_relatorio"
    await fila.cancelar(db, ex, AGORA)
    await db.commit()

    pronta = await _pronta(db, client)
    base = f"{API}/execucoes/{pronta.id}/arquivo"
    esperado = {
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "csv": "text/csv",
        "md": "text/markdown",
        "json": "application/json",
        "html": "text/html",
    }
    for fmt, tipo in esperado.items():
        r = await client.get(f"{base}/{fmt}")
        assert r.status_code == 200, (fmt, r.text)
        assert r.headers["content-type"].startswith(tipo), fmt
        assert r.headers["content-disposition"] == (
            f'attachment; filename="conferencia-shopee-2026-09-28_2026-10-04.{fmt}"'
        )
    assert (await client.get(f"{base}/xlsx")).content[:2] == b"PK"
    assert (await client.get(f"{base}/csv")).content.startswith("﻿Grupo;".encode())
    assert (await client.get(f"{base}/md")).text.startswith("# Conferência Shopee")
    assert (await client.get(f"{base}/json")).json() == pronta.relatorio
    assert (await client.get(f"{base}/html")).text.lower().startswith("<!doctype html>")
    assert (await client.get(f"{base}/pdf")).status_code == 422


async def test_excel_pelo_link_sem_login(client, db, auth_as):
    auth_as(None)
    pronta = await _pronta(db, client)
    url = f"{API}/execucoes/{pronta.id}/excel/link"
    t = threema_aviso.token_excel(pronta.id, AGORA)
    r = await client.get(f"{url}?t={t}")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert r.content[:2] == b"PK"

    vencido = threema_aviso.token_excel(pronta.id, AGORA - timedelta(days=8))
    ate, _, assinatura = t.partition(".")
    outra = threema_aviso.token_excel(uuid.uuid4(), AGORA)
    for ruim in (vencido, f"{int(ate) + 60}.{assinatura}", outra, "1.x", ""):
        r = await client.get(f"{url}?t={ruim}")
        assert r.status_code == 403, ruim
        assert r.json()["detail"]["code"] == "conferencia_link_invalido"
    assert (await client.get(url)).status_code == 403
    # Login normal continua exigido no download da tela.
    assert (await client.get(f"{API}/execucoes/{pronta.id}/arquivo/xlsx")).status_code == 401


async def test_link_de_execucao_sem_relatorio_404(client, db, auth_as):
    auth_as(None)
    ex = await _execucao(db)
    t = threema_aviso.token_excel(ex.id, AGORA)
    r = await client.get(f"{API}/execucoes/{ex.id}/excel/link?t={t}")
    assert r.status_code == 404


async def test_token_do_link_fora_do_access_log():
    logger = logging.getLogger("uvicorn.access")
    antes = list(logger.filters)
    try:
        mc.mascarar_link_no_access_log()
        filtro = logger.filters[-1]
        rec = logging.LogRecord(
            "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
            ("1.2.3.4:5", "GET",
             f"{API}/execucoes/{uuid.uuid4()}/excel/link?t=1999999999.abcdef0123", "1.1", 200),
            None,
        )
        filtro.filter(rec)
        assert rec.args[2].endswith("/excel/link?t=***")
        assert "abcdef" not in rec.getMessage()
        outro = logging.LogRecord(
            "uvicorn.access", logging.INFO, __file__, 1, "%s %s %s %s %d",
            ("1.2.3.4:5", "GET", f"{API}/execucoes?limite=30", "1.1", 200), None,
        )
        filtro.filter(outro)
        assert outro.args[2] == f"{API}/execucoes?limite=30"
    finally:
        logger.filters[:] = antes


# ───────────────────────────────────────────────────────────── contas


async def test_contas_listar_e_editar(client, db, admin):
    contas = await semear_contas(db)
    lista = (await client.get(f"{API}/contas")).json()
    assert [c["nome"] for c in lista] == ["Caio", "Velha", "Ana", "Bia"]
    assert lista[0] == {
        "id": str(contas["Caio"].id), "plataforma": "shopee", "adspower_user_id": "k-caio",
        "integration_id": None, "integracao_nome": None, "integracao_arquivada": None,
        "bling_loja_id": None, "nome": "Caio", "grupo": "celular", "ativo": True, "ordem": 0,
        "conta_key": "caio", "observacao": None,
    }
    url = f"{API}/contas/{contas['Bia'].id}"
    r = await client.put(url, json={"nome": "  Bia Nova ", "grupo": "mala", "ativo": False,
                                    "ordem": 5, "observacao": " perfil Firefox "})
    assert r.status_code == 200, r.text
    assert {k: r.json()[k] for k in ("nome", "grupo", "ativo", "ordem", "observacao")} == {
        "nome": "Bia Nova", "grupo": "mala", "ativo": False, "ordem": 5,
        "observacao": "perfil Firefox",
    }
    # Só o que veio muda; observação vazia apaga.
    r = await client.put(url, json={"observacao": ""})
    assert (r.json()["nome"], r.json()["observacao"]) == ("Bia Nova", None)
    for ruim in ({"grupo": "eletro"}, {"nome": "   "}, {"ordem": -1}):
        assert (await client.put(url, json=ruim)).status_code == 422, ruim
    r = await client.put(f"{API}/contas/{uuid.uuid4()}", json={"nome": "X"})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "conta_nao_encontrada"


async def test_informar_aceita_o_contexto(client, db, admin):
    r = await client.get("/api/informar/conferencia_shopee")
    assert r.status_code == 200, r.text
