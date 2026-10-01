"""Denúncia: o Mac mini manda a cópia (sync) e as três telas leem."""

import hashlib
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.config import get_settings
from app.models import DenunciaRemetente, UserRole

TOKEN = "dnc_teste_" + uuid.uuid4().hex
H = {"Authorization": f"Bearer {TOKEN}"}

_TABELAS = (
    "denuncia_anuncios", "denuncia_lojas", "denuncia_denuncias", "denuncia_casos",
    "denuncia_compras", "denuncia_provas", "denuncia_verificacoes", "denuncia_remetentes",
    "denuncia_robo_status",
)


@pytest_asyncio.fixture(autouse=True)
async def _limpa(db):
    for t in _TABELAS:
        await db.execute(text(f"DELETE FROM {t}"))  # noqa: S608
    db.add(DenunciaRemetente(nome="mini teste", token_hash=hashlib.sha256(TOKEN.encode()).hexdigest()))
    await db.commit()
    yield
    for t in _TABELAS:
        await db.execute(text(f"DELETE FROM {t}"))  # noqa: S608
    await db.commit()


@pytest.fixture
def pasta_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "uploads_dir", str(tmp_path))
    return tmp_path


def _anuncio(aid, **kw):
    base = {
        "id": aid, "marketplace": "Shopee", "shop_id": "111", "loja": "loja_x",
        "titulo": "Celular X", "url": f"https://shopee.com.br/p/{aid}", "hom": "09604-24-17288",
        "vendas": 10, "grupo": "GRUPO 1", "escopo": "celular", "situacao": "ativo",
        "propria": 0, "visto_primeiro": "2026-09-10 00:00:00", "teste": 0,
    }
    base.update(kw)
    return base


async def _carga(client):
    r = await client.post(
        "/api/denuncia/sync/anuncios",
        json={"linhas": [
            _anuncio("A1", vendas=50),
            _anuncio("A2", vendas=5, situacao="fora do ar"),
            _anuncio("A3", grupo="DESCARTADO"),
            _anuncio("A4", propria=1),
        ]},
        headers=H,
    )
    assert r.status_code == 200, r.text
    r = await client.post(
        "/api/denuncia/sync/denuncias",
        json={"linhas": [
            {"id": 1, "anuncio_id": "A1", "canal": "Shopee", "protocolo": "P1", "data": "2026-09-20",
             "situacao": "Enviada", "tipo": "normal", "texto": "denúncia"},
            {"id": 2, "anuncio_id": "A2", "canal": "Shopee", "protocolo": "P1", "data": "2026-09-20",
             "situacao": "Enviada", "tipo": "normal"},
            {"id": 3, "anuncio_id": "A1", "canal": "Anatel SEI", "protocolo": None, "data": "2026-09-25",
             "situacao": "Pendente", "tipo": "reiteração"},
        ]},
        headers=H,
    )
    assert r.status_code == 200, r.text
    await client.post(
        "/api/denuncia/sync/casos",
        json={"linhas": [{"id": 7, "codigo": "CASO-001", "anuncio_id": "A1", "titulo": "Caso 1",
                          "status": "Com jurídico", "aberto_em": "2026-09-15"}]},
        headers=H,
    )
    await client.post(
        "/api/denuncia/sync/compras",
        json={"linhas": [{"id": 3, "anuncio_id": "A1", "caso_id": 7, "status": "Recebido", "valor_pago": 99.9}]},
        headers=H,
    )


async def test_sync_exige_token(client):
    r = await client.post("/api/denuncia/sync/anuncios", json={"linhas": []})
    assert r.status_code == 401
    r = await client.post(
        "/api/denuncia/sync/anuncios", json={"linhas": []}, headers={"Authorization": "Bearer errado"}
    )
    assert r.status_code == 401


async def test_sync_revogado_nao_entra(client, db):
    await db.execute(text("UPDATE denuncia_remetentes SET revoked_at = now()"))
    await db.commit()
    r = await client.post("/api/denuncia/sync/anuncios", json={"linhas": []}, headers=H)
    assert r.status_code == 401


async def test_sync_tabela_desconhecida(client):
    r = await client.post("/api/denuncia/sync/usuarios", json={"linhas": []}, headers=H)
    assert r.status_code == 404


async def test_sync_regrava_e_remove(client, db):
    await _carga(client)
    r = await client.post(
        "/api/denuncia/sync/anuncios", json={"linhas": [_anuncio("A1", vendas=77)]}, headers=H
    )
    assert r.json() == {"gravadas": 1, "apagadas": 0}
    vendas = (await db.execute(text("SELECT vendas, dados->>'vendas' FROM denuncia_anuncios WHERE id='A1'"))).one()
    assert vendas == (77, "77")
    r = await client.post("/api/denuncia/sync/anuncios", json={"removidos": ["A2"]}, headers=H)
    assert r.json()["apagadas"] == 1
    ultimo = (await db.execute(text("SELECT ultimo_envio_em FROM denuncia_remetentes"))).scalar()
    assert ultimo is not None


async def test_tela_anuncios_esconde_descartado_e_propria(client, make_user, auth_as):
    await _carga(client)
    auth_as(await make_user(permissions={"denuncia": {"view": True}}))
    r = await client.get("/api/denuncia/anuncios")
    assert r.status_code == 200, r.text
    j = r.json()
    assert [i["id"] for i in j["itens"]] == ["A1", "A2"]  # vendas desc; A3 descartado, A4 própria
    a1 = j["itens"][0]
    assert a1["nden"] == 2 and a1["caso"] == "CASO-001" and a1["hom"] == "09604-24-17288"
    assert j["numeros"] == {"total": 2, "ativos": 1, "fora_do_ar": 1, "com_denuncia": 2}
    r = await client.get("/api/denuncia/anuncios", params={"grupo": "DESCARTADO"})
    assert [i["id"] for i in r.json()["itens"]] == ["A3"]
    r = await client.get("/api/denuncia/anuncios", params={"q": "17288", "situacao": "ativo"})
    assert [i["id"] for i in r.json()["itens"]] == ["A1"]


async def test_tela_sem_permissao(client, make_user, auth_as):
    auth_as(await make_user(permissions={}))
    r = await client.get("/api/denuncia/anuncios")
    assert r.status_code == 403


async def test_tela_denuncias_junta_protocolo(client, make_user, auth_as):
    await _carga(client)
    auth_as(await make_user(role=UserRole.ADMIN))
    j = (await client.get("/api/denuncia/denuncias")).json()
    assert j["total"] == 2
    shopee = next(g for g in j["itens"] if g["protocolo"] == "P1")
    assert sorted(shopee["ids"]) == [1, 2]
    assert {a["id"] for a in shopee["anuncios"]} == {"A1", "A2"}
    assert j["resumo"]["Shopee"] == {"total": 1, "Enviada": 1}
    j = (await client.get("/api/denuncia/denuncias", params={"canal": "Anatel SEI"})).json()
    assert [g["id"] for g in j["itens"]] == [3]
    d = (await client.get("/api/denuncia/denuncias/2")).json()
    assert {x["id"] for x in d["linhas"]} == {1, 2}


async def test_tela_casos(client, make_user, auth_as):
    await _carga(client)
    auth_as(await make_user(role=UserRole.ADMIN))
    j = (await client.get("/api/denuncia/casos")).json()
    assert j["itens"][0]["codigo"] == "CASO-001"
    assert j["itens"][0]["ncompras"] == 1 and j["itens"][0]["loja"] == "loja_x"
    c = (await client.get("/api/denuncia/casos/7")).json()
    assert c["compras"][0]["valor_pago"] == 99.9
    assert {d["id"] for d in c["denuncias"]} == {1, 3}


async def test_prova_arquivo_sobe_e_confere_hash(client, make_user, auth_as, pasta_uploads):
    conteudo = b"\x89PNG fake"
    sha = hashlib.sha256(conteudo).hexdigest()
    r = await client.post(
        "/api/denuncia/sync/provas",
        json={"linhas": [
            {"id": 10, "anuncio_id": "A1", "tipo": "Captura no ato", "arquivo": "A1/x.png",
             "nome_original": "x.png", "sha256": sha, "tamanho": len(conteudo),
             "mega_caminho": "/Fiscalização/Denuncias/A1 - loja_x/x.png", "mega_em": "2026-09-30 00:11:33"},
            {"id": 11, "anuncio_id": "A1", "tipo": "Outro", "arquivo": "A1/y.pdf", "sha256": "0" * 64},
        ]},
        headers=H,
    )
    assert r.status_code == 200
    faltam = (await client.get("/api/denuncia/sync/provas-sem-arquivo", headers=H)).json()["ids"]
    assert faltam == [11, 10]
    r = await client.put("/api/denuncia/sync/provas/11/arquivo", content=conteudo, headers=H)
    assert r.status_code == 409  # hash diferente: nada guardado
    r = await client.put("/api/denuncia/sync/provas/10/arquivo", content=conteudo, headers=H)
    assert r.status_code == 200, r.text
    assert (pasta_uploads / "denuncia/provas/10.png").read_bytes() == conteudo
    assert (await client.get("/api/denuncia/sync/provas-sem-arquivo", headers=H)).json()["ids"] == [11]
    auth_as(await make_user(role=UserRole.ADMIN))
    r = await client.get("/api/denuncia/provas/10/arquivo")
    assert r.status_code == 200 and r.content == conteudo
    assert r.headers["content-type"] == "image/png"
    assert (await client.get("/api/denuncia/provas/11/arquivo")).status_code == 404
    # sumiu no mini → some a linha e o arquivo
    await client.post("/api/denuncia/sync/provas", json={"removidos": [10]}, headers=H)
    assert not (pasta_uploads / "denuncia/provas/10.png").exists()


async def test_prova_html_sempre_baixa_com_sandbox(client, make_user, auth_as, pasta_uploads):
    """Página salva do marketplace nunca abre na origem do DaVinci."""
    html = b"<html><script>alert(1)</script></html>"
    await client.post(
        "/api/denuncia/sync/provas",
        json={"linhas": [{"id": 20, "anuncio_id": "A1", "tipo": "Dossiê", "arquivo": "A1/pagina.html",
                          "sha256": hashlib.sha256(html).hexdigest()}]},
        headers=H,
    )
    assert (await client.put("/api/denuncia/sync/provas/20/arquivo", content=html, headers=H)).status_code == 200
    auth_as(await make_user(role=UserRole.ADMIN))
    r = await client.get("/api/denuncia/provas/20/arquivo")
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("attachment")
    assert r.headers["content-security-policy"] == "sandbox"
    assert r.headers["x-content-type-options"] == "nosniff"


async def test_resumo_conta_provas_fora_do_mega_e_ficha_traz_caminho(client, make_user, auth_as):
    await _carga(client)
    await client.post(
        "/api/denuncia/sync/provas",
        json={"linhas": [
            {"id": 30, "anuncio_id": "A1", "tipo": "Print", "arquivo": "A1/a.png",
             "mega_caminho": "/Fiscalização/Denuncias/A1 - loja_x/a.png", "mega_em": "2026-09-30 00:11:33"},
            {"id": 31, "anuncio_id": "A1", "tipo": "Print", "arquivo": "A1/b.png", "mega_caminho": None, "mega_em": None},
        ]},
        headers=H,
    )
    auth_as(await make_user(role=UserRole.ADMIN))
    r = (await client.get("/api/denuncia/resumo")).json()
    assert r["provas"] == 2 and r["provas_fora_do_mega"] == 1
    ficha = (await client.get("/api/denuncia/anuncios/A1")).json()
    caminhos = {p["id"]: p["mega_caminho"] for p in ficha["provas"]}
    assert caminhos == {30: "/Fiscalização/Denuncias/A1 - loja_x/a.png", 31: None}


async def test_pulso_marca_contato_sem_mandar_nada(client, db):
    assert (await db.execute(text("SELECT ultimo_envio_em FROM denuncia_remetentes"))).scalar() is None
    r = await client.post("/api/denuncia/sync/pulso", headers=H)
    assert r.status_code == 200
    db.expire_all()
    assert (await db.execute(text("SELECT ultimo_envio_em FROM denuncia_remetentes"))).scalar() is not None
    assert (await client.post("/api/denuncia/sync/pulso")).status_code == 401


# ───────────────────────────────── aba Robô (01/10/2026)


def _tarefa(acao, janela, status, **kw):
    pedido = kw.pop("pedido_em", f"{janela[:10]}T{janela[11:13]}:00:00-03:00")
    return {
        # no mini a chave é o nome do gatilho: a retomada (_r1220) tem outra
        "chave": f"tarefa_{acao}_{janela}_r{pedido[11:13]}{pedido[14:16]}",
        "estado": "erro" if status == "erro" else "ok", "detalhe": "", "o_que_fazer": "",
        "dados": {"acao": acao, "nome": acao, "janela": janela, "status": status,
                  "pedido_em": pedido, **kw},
    }


def _resumo(*tarefas, **itens):
    base = [
        {"chave": "agente", "estado": "ok", "detalhe": "v20 no ar", "o_que_fazer": "",
         "dados": {"versao": "20", "desde": "2026-10-01T10:13:56-03:00"}},
        {"chave": "fila_M", "estado": "ok", "detalhe": "rodando: Mercado Livre", "o_que_fazer": "",
         "dados": {"rodando": {"acao": "varredura_mercadolivre", "nome": "Mercado Livre",
                               "desde": "2026-10-01T12:07:00-03:00",
                               "progresso": "denunciando 3 de 9"},
                   "proximos": [{"acao": "varredura_shopee", "nome": "Shopee"}], "n_proximos": 1}},
        *({"chave": f"fila_{f}", "estado": "ok", "detalhe": "livre", "o_que_fazer": "",
           "dados": {"rodando": None}} for f in ("S", "E")),
        *tarefas,
    ]
    for k, v in itens.items():
        base.append({"chave": k, **v})
    return {"quando": "2026-10-01T12:30:00-03:00", "itens": base}


def test_painel_rodadas_frentes_e_alarme():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.services.denuncia_robo import montar_painel

    br = ZoneInfo("America/Sao_Paulo")
    agora = datetime(2026, 10, 1, 12, 30, tzinfo=br)
    j = "2026-10-01_12h"
    resumo = _resumo(
        _tarefa("checagem", j, "concluida", pedido_em="2026-10-01T11:45:00-03:00"),
        _tarefa("varredura_mercadolivre", j, "rodando", progresso="denunciando 3 de 9"),
        _tarefa("varredura_shopee", j, "erro", pedido_em="2026-10-01T12:00:00-03:00",
                erro="captcha"),
        _tarefa("varredura_shopee", j, "fila", pedido_em="2026-10-01T12:20:00-03:00"),
        sei_assinatura={"estado": "atencao", "detalhe": "loja X aguardando assinatura desde 12:10",
                        "o_que_fazer": "Assinar no Safari", "dados": {}},
        problemas={"estado": "erro", "detalhe": "", "o_que_fazer": "", "dados": {"lista": [
            {"quando": "2026-10-01 12:05", "tarefa": "código do sei", "problema": "não chegou",
             "pergunta": "encaminhar o e-mail", "bloqueia": True},
            {"quando": "2026-10-01 11:00", "tarefa": "ML 429", "problema": "limite",
             "pergunta": "nada", "bloqueia": False},
        ]}},
    )
    # 06h: só e-mails e conferência (rodam pelo relógio do robô) — não é rodada
    resumo["itens"].append(_tarefa("ciclo_emails", "2026-10-01_06h", "concluida"))
    p = montar_painel(resumo, agora, agora)

    assert p["conectado"] is True and p["agente"]["versao"] == "20"
    r6, r12, r18 = p["rodadas"]
    assert r6["estado"] == "nao_comecou" and r18["estado"] == "futura"
    assert [x["acao"] for x in r6["passos"]] == ["ciclo_emails"]
    assert r12["estado"] == "rodando"
    acoes = [x["acao"] for x in r12["passos"]]
    assert acoes == ["checagem", "varredura_mercadolivre", "varredura_shopee"]
    shopee = r12["passos"][2]
    assert shopee["status"] == "fila" and shopee["tentativas"] == 2  # vale a retomada mais nova

    m = next(f for f in p["frentes"] if f["fila"] == "M")
    assert m["fazendo"] == "Mercado Livre" and m["progresso"] == "denunciando 3 de 9"
    assert m["proximos"] == ["Shopee"]

    titulos = [x["titulo"] for x in p["precisa"]]
    assert "SEI esperando a assinatura da titular" in titulos
    assert "código do sei" in titulos
    assert "A rodada das 06h não começou" in titulos
    assert [x["titulo"] for x in p["avisos"]] == ["ML 429"]


def test_painel_mini_sem_noticia_e_nunca():
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from app.services.denuncia_robo import montar_painel

    agora = datetime(2026, 10, 1, 9, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    p = montar_painel(_resumo(), agora - timedelta(minutes=12), agora)
    assert p["conectado"] is False
    assert p["precisa"][0]["titulo"] == "O Mac mini não dá notícia há 12 min"
    p = montar_painel(None, None, agora)
    assert p["precisa"][0]["titulo"] == "O Mac mini nunca mandou o estado do robô"
    assert [r["estado"] for r in p["rodadas"]] == ["nao_comecou", "futura", "futura"]


async def test_robo_sync_e_tela(client, make_user, auth_as):
    assert (await client.post("/api/denuncia/sync/robo", json=_resumo())).status_code == 401
    r = await client.post("/api/denuncia/sync/robo", json={"itens": "x"}, headers=H)
    assert r.status_code == 422
    r = await client.post("/api/denuncia/sync/robo", json=_resumo(), headers=H)
    assert r.status_code == 200, r.text
    # o segundo resumo substitui o primeiro (uma linha por remetente)
    r = await client.post("/api/denuncia/sync/robo",
                          json=_resumo(agente={"estado": "erro", "detalhe": "agente parado",
                                               "o_que_fazer": "Abrir o 6", "dados": {}}),
                          headers=H)
    assert r.status_code == 200, r.text

    auth_as(await make_user(permissions={}))
    assert (await client.get("/api/denuncia/robo")).status_code == 403
    auth_as(await make_user(permissions={"denuncia": {"view": True}}))
    j = (await client.get("/api/denuncia/robo")).json()
    assert j["conectado"] is True
    assert len(j["rodadas"]) == 3 and len(j["frentes"]) == 3
    assert any(x["titulo"] == "Robô parado" for x in j["precisa"])


# ───────────────────────────────── provas sob demanda (01/10/2026)


async def _prova(client, pid, conteudo, nome="x.png"):
    r = await client.post(
        "/api/denuncia/sync/provas",
        json={"linhas": [{"id": pid, "anuncio_id": "A1", "tipo": "Captura no ato",
                          "arquivo": f"A1/{nome}", "nome_original": nome, "sha256": hashlib.sha256(conteudo).hexdigest(),
                          "tamanho": len(conteudo)}]},
        headers=H,
    )
    assert r.status_code == 200, r.text


async def test_prova_sob_demanda_pede_ao_mini_e_abre(client, make_user, auth_as, pasta_uploads):
    conteudo = b"\x89PNG prova 20"
    await _prova(client, 20, conteudo)
    auth_as(await make_user(permissions={"denuncia": {"view": True}}))
    # ninguém pediu ainda: o mini não tem o que mandar
    assert (await client.get("/api/denuncia/sync/provas-pedidas", headers=H)).json()["ids"] == []

    r = await client.post("/api/denuncia/provas/20/preparar")
    assert r.status_code == 200 and r.json()["pronto"] is False
    assert (await client.get("/api/denuncia/sync/provas-pedidas", headers=H)).json()["ids"] == [20]
    assert (await client.get("/api/denuncia/sync/provas-pedidas")).status_code == 401

    r = await client.put("/api/denuncia/sync/provas/20/arquivo", content=conteudo, headers=H)
    assert r.status_code == 200, r.text
    assert (await client.get("/api/denuncia/sync/provas-pedidas", headers=H)).json()["ids"] == []
    assert (await client.post("/api/denuncia/provas/20/preparar")).json() == {"pronto": True}
    r = await client.get("/api/denuncia/provas/20/arquivo")
    assert r.status_code == 200 and r.content == conteudo
    assert (await client.post("/api/denuncia/provas/999/preparar")).status_code == 404


async def test_prova_pedido_velho_caduca(client, db, make_user, auth_as, pasta_uploads):
    await _prova(client, 21, b"x")
    auth_as(await make_user(permissions={"denuncia": {"view": True}}))
    await client.post("/api/denuncia/provas/21/preparar")
    await db.execute(text(
        "UPDATE denuncia_provas SET arquivo_pedido_em = now() - interval '11 minutes' WHERE id = 21"
    ))
    await db.commit()
    # mini desligado na hora do clique: o pedido não fica pendurado pra sempre
    assert (await client.get("/api/denuncia/sync/provas-pedidas", headers=H)).json()["ids"] == []
    # clicou de novo: pede de novo
    await client.post("/api/denuncia/provas/21/preparar")
    assert (await client.get("/api/denuncia/sync/provas-pedidas", headers=H)).json()["ids"] == [21]


async def test_prova_teto_apaga_a_aberta_ha_mais_tempo(
    client, db, make_user, auth_as, pasta_uploads, monkeypatch
):
    import os

    from app.routers import denuncia as rota

    monkeypatch.setattr(rota, "TETO_PROVAS_BYTES", 25)
    a, b = b"a" * 15, b"b" * 15
    await _prova(client, 30, a)
    await _prova(client, 31, b)
    r = await client.put("/api/denuncia/sync/provas/30/arquivo", content=a, headers=H)
    assert r.status_code == 200
    velho = pasta_uploads / "denuncia/provas/30.png"
    os.utime(velho, (1, 1))
    r = await client.put("/api/denuncia/sync/provas/31/arquivo", content=b, headers=H)
    assert r.status_code == 200
    # 30 + 31 passam de 25 bytes: sai a 30 (a mais antiga), fica a que acabou de chegar
    assert not velho.exists()
    assert (pasta_uploads / "denuncia/provas/31.png").exists()
    db.expire_all()
    locais = dict((await db.execute(text("SELECT id, arquivo_local FROM denuncia_provas"))).all())
    assert locais[30] is None and locais[31]
    auth_as(await make_user(permissions={"denuncia": {"view": True}}))
    assert (await client.post("/api/denuncia/provas/30/preparar")).json()["pronto"] is False


def test_painel_modo_manual_nao_acusa_rodada():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.services.denuncia_robo import montar_painel

    agora = datetime(2026, 10, 1, 19, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    resumo = dict(_resumo(_tarefa("varredura_mercadolivre", "2026-10-01_12h", "concluida")),
                  despertador={"ligado": False, "rodadas": [6, 12, 18]})
    p = montar_painel(resumo, agora, agora)
    assert p["modo"] == "manual"
    assert [r["estado"] for r in p["rodadas"]] == ["manual", "feita", "manual"]
    assert not any("não começou" in x["titulo"] for x in p["precisa"])
