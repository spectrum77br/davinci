"""Cérebro dos chamados com senha própria (24/09, Hermes no Mac Santiago).

Vinicius escolheu SUBSTITUIR o cérebro do Eduardo. O token antigo (NF) é o mesmo
das mãos do Eduardo (lease/resultado/recebida), então a troca de guarda não pode
derrubá-lo: com o Hermes `exclusivo`, o cérebro antigo só para de ver
(`/analisar` vazio) e de decidir (`/analise` 409); o resto segue igual."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.models import Chamado, ChamadoCerebro, ChamadoMensagem

pytestmark = pytest.mark.asyncio

_LEGADO = "tok-nf-legado"  # noqa: S105
_HERMES = "cerebro_hermes_teste"  # noqa: S105


def _perms() -> dict:
    return {"chamados": {"view": True, "edit": True, "delete": True}}


@pytest.fixture
async def cenario(client, make_user, auth_as, db, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "nf_agent_token", _LEGADO)
    auth_as(await make_user(permissions=_perms()))
    hermes = ChamadoCerebro(
        nome="IA de Chamado", token_hash=hashlib.sha256(_HERMES.encode()).hexdigest(), ligada=True
    )
    db.add(hermes)
    await db.commit()
    legado = {"X-Agent-Token": _LEGADO}
    r = await client.post(
        "/api/chamados/agent/registrar",
        headers=legado,
        json={
            "pedido_bling": "293413",
            "origem": "margem",
            "conta": "forpaper",
            "chamado": "479765445",
            "mensagem": "Frete anúncio R$ 32,48; frete cobrado R$ 54,08",
            "status_envio": "enviada",
        },
    )
    assert r.status_code == 200, r.text
    rec = await client.post(
        "/api/chamados/agent/recebida",
        headers=legado,
        json={"chamado": "479765445", "texto": "Informe as medidas da embalagem."},
    )
    assert rec.status_code == 200, rec.text
    return {"cid": r.json()["chamado_id"], "hermes": hermes}


async def _analisar(client, token: str) -> list[dict]:
    r = await client.post("/api/chamados/agent/analisar", headers={"X-Agent-Token": token}, json={})
    assert r.status_code == 200, r.text
    return r.json()["chamados"]


async def test_hermes_analisa_e_assina(client, db, cenario):
    """Senha própria: vê o mesmo que o antigo, e a análise sai com o nome dele."""
    assert [c["chamado_id"] for c in await _analisar(client, _HERMES)] == [cenario["cid"]]
    r = await client.post(
        "/api/chamados/agent/analise",
        headers={"X-Agent-Token": _HERMES},
        json={
            "chamado_id": cenario["cid"],
            "classe": "pede_medidas",
            "resumo": "ML pediu as medidas",
            "acao": "humano",
        },
    )
    assert r.status_code == 200, r.text
    texto = (
        await db.execute(select(ChamadoMensagem.texto).where(ChamadoMensagem.tipo == "analise"))
    ).scalar_one()
    assert texto.startswith("Análise da IA de Chamado [pede_medidas]:")
    # a aba ainda lê a ação pelo fim do texto
    assert texto.endswith("precisa de humano")
    await db.refresh(cenario["hermes"])
    assert cenario["hermes"].last_used_at is not None

    # token errado ou revogado → 401
    assert (
        await client.post("/api/chamados/agent/analisar", headers={"X-Agent-Token": "x"}, json={})
    ).status_code == 401
    cenario["hermes"].revoked_at = datetime.now(UTC)
    await db.commit()
    assert (
        await client.post(
            "/api/chamados/agent/analisar", headers={"X-Agent-Token": _HERMES}, json={}
        )
    ).status_code == 401


async def test_troca_de_guarda_para_so_o_cerebro_antigo(client, db, cenario):
    legado = {"X-Agent-Token": _LEGADO}
    hermes = {"X-Agent-Token": _HERMES}
    assert len(await _analisar(client, _LEGADO)) == 1

    # o antigo não pode assumir nem consultar a troca
    assert (
        await client.post("/api/chamados/agent/cerebro", headers=legado, json={"exclusivo": True})
    ).status_code == 403

    r = await client.post("/api/chamados/agent/cerebro", headers=hermes, json={"exclusivo": True})
    assert r.status_code == 200, r.text
    assert r.json()["exclusivo"] is True and r.json()["legado_ignorado_at"] is None

    # cérebro antigo: lista vazia, decisão recusada
    assert await _analisar(client, _LEGADO) == []
    an = await client.post(
        "/api/chamados/agent/analise",
        headers=legado,
        json={"chamado_id": cenario["cid"], "classe": "x", "resumo": "y", "acao": "esperar"},
    )
    assert an.status_code == 409 and an.json()["detail"]["code"] == "cerebro_substituido"
    # as mãos do Eduardo seguem com o mesmo token
    assert (
        await client.post("/api/chamados/agent/lease", headers=legado, json={})
    ).status_code == 200
    assert (
        await client.post(
            "/api/chamados/agent/recebida",
            headers=legado,
            json={"chamado": "479765445", "texto": "Aguardamos as medidas."},
        )
    ).status_code == 200
    # o Hermes continua vendo
    assert len(await _analisar(client, _HERMES)) == 1
    # e fica registrado que o antigo ainda bate na porta
    estado = (await client.post("/api/chamados/agent/cerebro", headers=hermes, json={})).json()
    assert estado["exclusivo"] is True and estado["legado_ignorado_at"] is not None

    # desfazer devolve a vez pro antigo
    r = await client.post("/api/chamados/agent/cerebro", headers=hermes, json={"exclusivo": False})
    assert r.json()["exclusivo"] is False
    assert len(await _analisar(client, _LEGADO)) == 1


async def test_so_um_cerebro_exclusivo(client, db, cenario):
    outro = "cerebro_outro_teste"  # noqa: S105
    db.add(ChamadoCerebro(nome="Outro", token_hash=hashlib.sha256(outro.encode()).hexdigest()))
    await db.commit()
    ok = await client.post(
        "/api/chamados/agent/cerebro", headers={"X-Agent-Token": _HERMES}, json={"exclusivo": True}
    )
    assert ok.status_code == 200
    r = await client.post(
        "/api/chamados/agent/cerebro", headers={"X-Agent-Token": outro}, json={"exclusivo": True}
    )
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "outro_cerebro_exclusivo", "cerebro": "IA de Chamado"}


async def test_exemplos_traz_o_que_ja_foi_analisado(client, db, cenario):
    legado = {"X-Agent-Token": _LEGADO}
    hermes = {"X-Agent-Token": _HERMES}
    # antes de qualquer análise: nada
    r = await client.post("/api/chamados/agent/exemplos", headers=hermes, json={})
    assert r.status_code == 200, r.text
    assert r.json() == {"total": 0, "chamados": []}

    # cérebro antigo decidiu → vira exemplo, com a conversa e a sugestão de valor
    an = await client.post(
        "/api/chamados/agent/analise",
        headers=legado,
        json={
            "chamado_id": cenario["cid"],
            "classe": "reembolso_confirmado",
            "resumo": "ML devolveu a diferença",
            "acao": "resolver",
            "valor_recuperado": 21.6,
        },
    )
    assert an.status_code == 200, an.text
    r = await client.post("/api/chamados/agent/exemplos", headers=hermes, json={"plataforma": "ml"})
    body = r.json()
    assert body["total"] == 1
    c = body["chamados"][0]
    assert c["chamado_id"] == cenario["cid"] and c["valor_sugerido"] == "21.60"
    assert c["status_plataforma"] == "encerrado"
    tipos = [m["tipo"] for m in c["mensagens"]]
    assert "analise" in tipos and "resposta" in tipos
    # filtro de plataforma e paginação
    r = await client.post(
        "/api/chamados/agent/exemplos", headers=hermes, json={"plataforma": "shopee"}
    )
    assert r.json()["total"] == 0
    r = await client.post("/api/chamados/agent/exemplos", headers=hermes, json={"offset": 1})
    assert r.json() == {"total": 1, "chamados": []}
    # só cérebro cadastrado
    assert (
        await client.post("/api/chamados/agent/exemplos", headers=legado, json={})
    ).status_code == 403
    # o chamado continua lá, sem ser tocado pela consulta
    ch = await db.get(Chamado, cenario["cid"])
    assert ch is not None and ch.resolvido is False


async def test_caso_acha_pelo_pedido_ou_protocolo(client, db, cenario):
    hermes = {"X-Agent-Token": _HERMES}
    por_pedido = await client.post(
        "/api/chamados/agent/caso", headers=hermes, json={"pedido_bling": "293413"}
    )
    assert por_pedido.status_code == 200, por_pedido.text
    assert [c["chamado_id"] for c in por_pedido.json()["chamados"]] == [cenario["cid"]]
    por_protocolo = await client.post(
        "/api/chamados/agent/caso", headers=hermes, json={"chamado": "479765445"}
    )
    assert por_protocolo.json()["chamados"][0]["mensagens"][-1]["texto"].startswith("Informe")
    assert (
        await client.post("/api/chamados/agent/caso", headers=hermes, json={"pedido_bling": "1"})
    ).json() == {"chamados": []}
    # sem filtro nenhum → 422; token antigo → 403
    assert (
        await client.post("/api/chamados/agent/caso", headers=hermes, json={})
    ).status_code == 422
    assert (
        await client.post(
            "/api/chamados/agent/caso",
            headers={"X-Agent-Token": _LEGADO},
            json={"pedido_bling": "293413"},
        )
    ).status_code == 403


async def test_ia_desligada_nao_ve_nem_decide(client, db, cenario):
    cenario["hermes"].ligada = False
    await db.commit()
    assert await _analisar(client, _HERMES) == []
    r = await client.post(
        "/api/chamados/agent/analise",
        headers={"X-Agent-Token": _HERMES},
        json={"chamado_id": cenario["cid"], "classe": "x", "resumo": "y", "acao": "humano"},
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "ia_desligada"


async def test_aba_ia_de_chamado(client, make_user, auth_as, db, cenario):
    """Aba Chamados › IA de Chamado: liga/desliga, manual e o que ela decidiu."""
    hermes = {"X-Agent-Token": _HERMES}
    est = (await client.get("/api/chamados/ia")).json()
    assert est["nome"] == "IA de Chamado" and est["ligada"] is True
    assert est["esperando"] == 1 and est["regras"] == [] and est["decisoes"] == []

    # manual: cria, a IA recebe as ativas
    r = await client.post(
        "/api/chamados/ia/regras",
        json={
            "quando": "A Shopee pedir prova numa devolução",
            "faca": "Responder com o vídeo da embalagem; sem vídeo, chamar humano",
            "plataforma": "Shopee",
        },
    )
    assert r.status_code == 201, r.text
    regra = r.json()
    assert regra["plataforma"] == "shopee" and regra["ativa"] is True and regra["autor"]
    r2 = await client.post(
        "/api/chamados/ia/regras", json={"quando": "Qualquer caso", "faca": "Seja educado"}
    )
    assert r2.json()["plataforma"] is None
    manual = (await client.post("/api/chamados/agent/cerebro", headers=hermes, json={})).json()
    assert manual["ligada"] is True
    assert [x["quando"] for x in manual["regras"]] == [
        "A Shopee pedir prova numa devolução",
        "Qualquer caso",
    ]
    # desativar tira do manual da IA, mas continua na aba
    ed = await client.patch(f"/api/chamados/ia/regras/{regra['id']}", json={"ativa": False})
    assert ed.status_code == 200 and ed.json()["ativa"] is False
    manual = (await client.post("/api/chamados/agent/cerebro", headers=hermes, json={})).json()
    assert [x["quando"] for x in manual["regras"]] == ["Qualquer caso"]
    assert len((await client.get("/api/chamados/ia")).json()["regras"]) == 2
    # editar texto e voltar a valer pra todas
    ed = await client.patch(
        f"/api/chamados/ia/regras/{regra['id']}", json={"faca": "Chamar humano", "plataforma": None}
    )
    assert ed.json()["faca"] == "Chamar humano" and ed.json()["plataforma"] is None
    assert (
        await client.patch(f"/api/chamados/ia/regras/{regra['id']}", json={"quando": ""})
    ).status_code == 422
    # apagar
    assert (await client.delete(f"/api/chamados/ia/regras/{regra['id']}")).status_code == 204
    assert len((await client.get("/api/chamados/ia")).json()["regras"]) == 1

    # o que ela decidiu aparece na aba; o caso sai do "esperando"
    an = await client.post(
        "/api/chamados/agent/analise",
        headers=hermes,
        json={
            "chamado_id": cenario["cid"],
            "classe": "pede_medidas",
            "resumo": "ok",
            "acao": "humano",
        },
    )
    assert an.status_code == 200, an.text
    est = (await client.get("/api/chamados/ia")).json()
    assert est["esperando"] == 0
    assert est["decisoes"][0]["pedido_bling"] == "293413"
    assert est["decisoes"][0]["texto"].startswith("Análise da IA de Chamado [pede_medidas]")

    # desligar pela aba
    off = await client.patch("/api/chamados/ia", json={"ligada": False})
    assert off.status_code == 200 and off.json()["ligada"] is False
    assert await _analisar(client, _HERMES) == []

    # só quem edita chamados mexe
    auth_as(await make_user(permissions={"chamados": {"view": True, "edit": False}}))
    assert (await client.get("/api/chamados/ia")).status_code == 200
    assert (await client.patch("/api/chamados/ia", json={"ligada": True})).status_code == 403
    assert (
        await client.post("/api/chamados/ia/regras", json={"quando": "a", "faca": "b"})
    ).status_code == 403


async def test_avaliacao_acertou_errou(client, db, cenario):
    """✓/✗ nas decisões (24/09): o ✗ vira instrução no chamado (a IA refaz) e as
    avaliações vão pra IA como aprendizado."""
    hermes = {"X-Agent-Token": _HERMES}
    an = await client.post(
        "/api/chamados/agent/analise",
        headers=hermes,
        json={
            "chamado_id": cenario["cid"],
            "classe": "pede_medidas",
            "resumo": "ML pediu medidas",
            "acao": "esperar",
        },
    )
    assert an.status_code == 200, an.text
    dec = (await client.get("/api/chamados/ia")).json()["decisoes"][0]
    assert dec["avaliacao"] is None
    mid = dec["mensagem_id"]
    assert await _analisar(client, _HERMES) == []

    # ✗ sem correção → 422
    r = await client.put(f"/api/chamados/ia/decisoes/{mid}/avaliacao", json={"certo": False})
    assert r.status_code == 422
    # ✗ com correção: fica marcada, vira instrução, a IA recebe o caso de novo
    r = await client.put(
        f"/api/chamados/ia/decisoes/{mid}/avaliacao",
        json={"certo": False, "correcao": "Responder com as medidas: 52×35×23 cm, 7 kg"},
    )
    assert r.status_code == 200, r.text
    av = r.json()["avaliacao"]
    assert av["certo"] is False and av["correcao"].startswith("Responder") and av["autor"]
    casos = await _analisar(client, _HERMES)
    assert len(casos) == 1 and "52×35×23" in casos[0]["instrucao"]["texto"]
    # salvar a mesma correção de novo não manda outra instrução
    await client.put(
        f"/api/chamados/ia/decisoes/{mid}/avaliacao",
        json={"certo": False, "correcao": "Responder com as medidas: 52×35×23 cm, 7 kg"},
    )
    n_instr = (
        (
            await db.execute(
                select(ChamadoMensagem).where(
                    ChamadoMensagem.chamado_id == cenario["cid"],
                    ChamadoMensagem.tipo == "instrucao",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(n_instr) == 1

    # a IA recebe a correção como aprendizado
    ia = (await client.post("/api/chamados/agent/cerebro", headers=hermes, json={})).json()
    assert ia["aprendizado"] == [
        {
            "certo": False,
            "pedido_bling": "293413",
            "plataforma": "ml",
            "decisao": dec["texto"],
            "correcao": "Responder com as medidas: 52×35×23 cm, 7 kg",
        }
    ]

    # ✓ troca a marcação (sem instrução nova) e desfazer limpa
    r = await client.put(f"/api/chamados/ia/decisoes/{mid}/avaliacao", json={"certo": True})
    assert r.json()["avaliacao"]["certo"] is True and r.json()["avaliacao"]["correcao"] is None
    ia = (await client.post("/api/chamados/agent/cerebro", headers=hermes, json={})).json()
    assert [a["certo"] for a in ia["aprendizado"]] == [True]
    r = await client.delete(f"/api/chamados/ia/decisoes/{mid}/avaliacao")
    assert r.status_code == 200 and r.json()["avaliacao"] is None

    # só decisão da IA pode ser avaliada
    outra = (
        (
            await db.execute(
                select(ChamadoMensagem).where(
                    ChamadoMensagem.chamado_id == cenario["cid"], ChamadoMensagem.tipo == "resposta"
                )
            )
        )
        .scalars()
        .first()
    )
    assert (
        await client.put(f"/api/chamados/ia/decisoes/{outra.id}/avaliacao", json={"certo": True})
    ).status_code == 404


async def test_instrucao_fura_a_fila(client, db, cenario):
    """24/09: instrução de pessoa passa na frente dos casos com resposta nova."""
    legado = {"X-Agent-Token": _LEGADO}
    r = await client.post(
        "/api/chamados/agent/registrar",
        headers=legado,
        json={
            "pedido_bling": "293999",
            "origem": "margem",
            "conta": "forpaper",
            "chamado": "479765999",
            "mensagem": "Frete cobrado a mais",
            "status_envio": "enviada",
        },
    )
    novo = r.json()["chamado_id"]
    # o caso do cenário tem resposta mais antiga; o novo recebe só uma instrução
    ins = await client.post(f"/api/chamados/{novo}/instrucao", json={"texto": "vê como está"})
    assert ins.status_code == 200, ins.text
    r = await client.post(
        "/api/chamados/agent/analisar",
        headers={"X-Agent-Token": _HERMES},
        json={"limite": 1, "plataforma": None, "canais": ["robo", "api", "manual"]},
    )
    assert [c["chamado_id"] for c in r.json()["chamados"]] == [novo]


async def test_avaliacao_no_historico_e_ao_fechar(client, db, cenario):
    """24/09: o histórico marca as decisões da IA (da_ia + avaliação) e o ✗ dado
    ao FECHAR o chamado (refazer=false) não manda a IA refazer."""
    hermes = {"X-Agent-Token": _HERMES}
    await client.post(
        "/api/chamados/agent/analise",
        headers=hermes,
        json={"chamado_id": cenario["cid"], "classe": "x", "resumo": "y", "acao": "esperar"},
    )
    msgs = (await client.get(f"/api/chamados/{cenario['cid']}/mensagens")).json()
    da_ia = [m for m in msgs if m["da_ia"]]
    assert len(da_ia) == 1 and da_ia[0]["avaliacao_ia"] is None
    assert all(not m["da_ia"] for m in msgs if m["tipo"] != "analise")

    r = await client.put(
        f"/api/chamados/ia/decisoes/{da_ia[0]['id']}/avaliacao",
        json={"certo": False, "correcao": "Era pra fechar como perdido", "refazer": False},
    )
    assert r.status_code == 200, r.text
    # sem instrução nova: a IA não refaz
    assert await _analisar(client, _HERMES) == []
    msgs = (await client.get(f"/api/chamados/{cenario['cid']}/mensagens")).json()
    av = next(m for m in msgs if m["da_ia"])["avaliacao_ia"]
    assert av["certo"] is False and av["correcao"] == "Era pra fechar como perdido"
    assert not [m for m in msgs if m["tipo"] == "instrucao"]
