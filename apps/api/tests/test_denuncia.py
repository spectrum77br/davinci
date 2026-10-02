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
    "denuncia_robo_status", "denuncia_robo_comandos", "denuncia_robo_tratadas", "denuncia_anexos",
    "denuncia_casos_extra", "denuncia_robo_agenda",
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
    assert j["itens"][0]["compra"]["status"] == "Recebido" and j["itens"][0]["loja"] == "loja_x"
    # status pelos fatos (01/10): não enviado ao advogado + compra recebida = "Produto recebido"
    assert j["itens"][0]["status"] == "Produto recebido" and j["itens"][0]["status_mini"] == "Com jurídico"
    # lista de compra (01/10): link do anúncio e a compra do caso vão juntos
    assert j["itens"][0]["url"] == "https://shopee.com.br/p/A1"
    assert j["itens"][0]["compra"]["valor_pago"] == 99.9
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
         "dados": {"rodando": {"acao": "procura", "nome": "procurar anúncios novos",
                               "desde": "2026-10-01T12:07:00-03:00",
                               "progresso": "Shopee pág 3 de 17"},
                   "proximos": [{"acao": "denuncias", "nome": "denúncias"}], "n_proximos": 1}},
        *({"chave": f"fila_{f}", "estado": "ok", "detalhe": "livre", "o_que_fazer": "",
           "dados": {"rodando": None}} for f in ("S", "E")),
        *tarefas,
    ]
    for k, v in itens.items():
        base.append({"chave": k, **v})
    return {"quando": "2026-10-01T12:30:00-03:00", "itens": base}


def test_painel_frentes_agenda_e_alarme():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.services.denuncia_robo import montar_painel

    br = ZoneInfo("America/Sao_Paulo")
    agora = datetime(2026, 10, 1, 12, 30, tzinfo=br)
    j = "2026-10-01_12h"
    resumo = _resumo(
        _tarefa("checagem", j, "concluida", pedido_em="2026-10-01T11:45:00-03:00"),
        _tarefa("procura", j, "rodando", progresso="Shopee pág 3 de 17"),
        _tarefa("denuncias", j, "erro", pedido_em="2026-10-01T12:00:00-03:00",
                erro="captcha"),
        _tarefa("denuncias", j, "fila", pedido_em="2026-10-01T12:20:00-03:00"),
        sei_assinatura={"estado": "atencao", "detalhe": "loja X aguardando assinatura desde 12:10",
                        "o_que_fazer": "Assinar no Safari", "dados": {}},
        problemas={"estado": "erro", "detalhe": "", "o_que_fazer": "", "dados": {"lista": [
            {"quando": "2026-10-01 12:05", "tarefa": "código do sei", "problema": "não chegou",
             "pergunta": "encaminhar o e-mail", "bloqueia": True},
            {"quando": "2026-10-01 11:00", "tarefa": "ML 429", "problema": "limite",
             "pergunta": "nada", "bloqueia": False},
        ]}},
    )
    resumo["itens"].append(_tarefa("ciclo_emails", "2026-10-01_06h", "concluida"))
    # 02/10: agenda por passo (a tabela do DaVinci) — some o horário fixo 06/12/18h
    agenda = {
        "checagem": {"ligado": True, "horarios": ["11:45"]},
        "procura": {"ligado": True, "horarios": ["06:00", "12:00"]},
        "anatel": {"ligado": True, "horarios": ["12:00"]},
        "compras": {"ligado": False, "horarios": ["06:00"]},   # desligado: não é alarme
        "juridico": {"ligado": True, "horarios": ["12:15"]},   # ainda nos 20 min de folga
    }
    p = montar_painel(resumo, agora, agora, agenda=agenda)

    assert p["conectado"] is True and p["agente"]["versao"] == "20"
    assert "rodadas" not in p

    m = next(f for f in p["frentes"] if f["fila"] == "M")
    assert m["fazendo"] == "Procurar anúncios novos" and m["progresso"] == "Shopee pág 3 de 17"
    assert m["proximos"] == ["Denúncias Lojas"]

    # aba Passos (02/10): 0 a 9, com a última vez de hoje; os antigos não têm botão
    passos = {x["acao"]: x for x in p["passos"]}
    assert [x["ordem"] for x in p["passos"]] == list(range(10))   # 7 = Diversos (02/10)
    assert p["passos"][0]["acao"] == "checagem" and p["passos"][-1]["acao"] == "ativos_inativos"
    assert [x["acao"] for x in p["passos"][2:5]] == ["procura", "denuncias", "anatel"]
    assert "varredura_mercadolivre" not in passos and "conferencia" not in passos
    assert passos["procura"]["ultima"]["status"] == "rodando"
    assert passos["denuncias"]["ultima"]["vezes"] == 2
    assert passos["compras"]["ultima"] is None
    assert passos["procura"]["agenda"] == {"ligado": True, "horarios": ["06:00", "12:00"], "no_robo": None}
    assert passos["denuncias"]["agenda"] == {"ligado": False, "horarios": [], "no_robo": None}

    pessoa = [x["titulo"] for x in p["ocorrencias"] if x["tipo"] == "pessoa"]
    assert "SEI esperando a assinatura da titular" in pessoa
    assert "código do sei" in pessoa
    # procura das 06:00 nunca pedida e anatel das 12:00 sem tarefa: alarme; o das 12:00 da
    # procura rodou, a checagem foi pedida, compras está desligado e o jurídico ainda tem folga
    nao = sorted(x["titulo"] for x in p["ocorrencias"] if "não começou" in x["titulo"])
    assert nao == ["2 · Procurar anúncios novos das 06:00 não começou",
                   "4 · Denúncias Anatel das 12:00 não começou"]
    assert [x["titulo"] for x in p["ocorrencias"] if x["tipo"] == "aviso"] == ["ML 429"]
    # "Tratado" tira da lista (só a marcada)
    sei_cod = next(x for x in p["ocorrencias"] if x["titulo"] == "código do sei")
    assert sei_cod["origem"] == "robo" and sei_cod["chave"].startswith("prob:")
    p2 = montar_painel(resumo, agora, agora, tratadas={sei_cod["chave"]}, agenda=agenda)
    assert "código do sei" not in [x["titulo"] for x in p2["ocorrencias"]]
    assert len(p2["ocorrencias"]) == len(p["ocorrencias"]) - 1


def test_painel_mini_sem_noticia_e_nunca():
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from app.services.denuncia_robo import montar_painel

    agora = datetime(2026, 10, 1, 9, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    p = montar_painel(_resumo(), agora - timedelta(minutes=12), agora)
    assert p["conectado"] is False
    assert p["ocorrencias"][0]["titulo"] == "O Mac mini não dá notícia há 12 min"
    p = montar_painel(None, None, agora, agenda={"procura": {"ligado": True, "horarios": ["06:00"]}})
    assert p["ocorrencias"][0]["titulo"] == "O Mac mini nunca mandou o estado do robô"
    # sem notícia do mini o alarme é o de cima, não "não começou"
    assert not any("não começou" in x["titulo"] for x in p["ocorrencias"])


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
    assert "rodadas" not in j and len(j["frentes"]) == 3
    assert any(x["titulo"] == "Robô parado" for x in j["ocorrencias"])


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


def test_painel_passos_antigos_ganham_nome():
    """Até 02/10 os passos 2–6 eram por site (e havia o 7, Relatório): sem botão, mas com nome."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.services.denuncia_robo import montar_painel

    agora = datetime(2026, 10, 1, 12, 30, tzinfo=ZoneInfo("America/Sao_Paulo"))
    resumo = _resumo()
    m = next(i for i in resumo["itens"] if i["chave"] == "fila_M")
    m["dados"] = {"rodando": {"acao": "varredura_tiktok", "nome": "varredura tiktok"},
                  "proximos": [{"acao": "relatorio", "nome": "relatorio"}]}
    p = montar_painel(resumo, agora, agora)
    f = next(x for x in p["frentes"] if x["fila"] == "M")
    assert f["fazendo"] == "TikTok (antigo)" and f["proximos"] == ["Relatório (fora da rotina)"]
    assert "relatorio" not in {x["acao"] for x in p["passos"]}


def test_painel_modo_manual_nao_acusa_rodada():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.services.denuncia_robo import montar_painel

    agora = datetime(2026, 10, 1, 19, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    # o mini manda a agenda que o despertador dele está seguindo
    resumo = dict(_resumo(_tarefa("procura", "2026-10-01_12h", "concluida")),
                  despertador={"ligado": False, "agenda": {
                      "procura": {"ligado": True, "horarios": ["06:00"]}}})
    agenda = {"procura": {"ligado": True, "horarios": ["06:00"]},
              "anatel": {"ligado": True, "horarios": ["06:00"]}}
    p = montar_painel(resumo, agora, agora, agenda=agenda)
    assert p["modo"] == "manual"
    assert not any("não começou" in x["titulo"] for x in p["ocorrencias"])
    passos = {x["acao"]: x for x in p["passos"]}
    assert passos["procura"]["agenda"]["no_robo"] is True     # o robô já segue
    assert passos["anatel"]["agenda"]["no_robo"] is False     # ainda não chegou lá
    assert passos["compras"]["agenda"]["no_robo"] is True     # desligado nos dois



async def test_robo_botoes_ligar_e_rodar_passo(client, make_user, auth_as):
    auth_as(await make_user(permissions={"denuncia": {"view": True}}))
    r = await client.post("/api/denuncia/robo/automatico", json={"ligado": True})
    assert r.status_code == 403  # só quem edita a Denúncia aperta os botões

    auth_as(await make_user(permissions={"denuncia": {"view": True, "edit": True}}))
    r = await client.post("/api/denuncia/robo/automatico", json={"ligado": "sim"})
    assert r.status_code == 422
    r = await client.post("/api/denuncia/robo/passo", json={"acao": "rm -rf"})
    assert r.status_code == 422
    r = await client.post("/api/denuncia/robo/automatico", json={"ligado": False})
    assert r.status_code == 200
    r = await client.post("/api/denuncia/robo/passo", json={"acao": "varredura_mercadolivre"})
    assert r.status_code == 422  # passo antigo (até 02/10) não tem mais botão
    r = await client.post("/api/denuncia/robo/passo", json={"acao": "procura"})
    assert r.status_code == 200, r.text

    # o mini busca os pendentes, executa e responde
    assert (await client.get("/api/denuncia/sync/robo/comandos")).status_code == 401
    cmds = (await client.get("/api/denuncia/sync/robo/comandos", headers=H)).json()["comandos"]
    assert [(c["tipo"], c["dados"]) for c in cmds] == [
        ("automatico", {"ligado": False}), ("passo", {"acao": "procura"})]
    r = await client.post(f"/api/denuncia/sync/robo/comandos/{cmds[0]['id']}",
                          json={"ok": True, "resultado": "rotina automática desligada"}, headers=H)
    assert r.status_code == 200
    resto = (await client.get("/api/denuncia/sync/robo/comandos", headers=H)).json()["comandos"]
    assert [c["id"] for c in resto] == [cmds[1]["id"]]

    j = (await client.get("/api/denuncia/robo")).json()
    assert [c["tipo"] for c in j["comandos"]] == ["passo", "automatico"]
    assert j["comandos"][1]["ok"] is True and j["comandos"][1]["entregue_em"]
    assert len(j["passos"]) == 10  # 02/10: passos 0 a 9 (o 7 virou Denúncias Diversos)


async def test_robo_agenda_salva_e_mini_puxa(client, make_user, auth_as):
    auth_as(await make_user(permissions={"denuncia": {"view": True}}))
    r = await client.put("/api/denuncia/robo/agenda/procura", json={"ligado": True})
    assert r.status_code == 403

    auth_as(await make_user(permissions={"denuncia": {"view": True, "edit": True}}))
    r = await client.put("/api/denuncia/robo/agenda/procura",
                         json={"ligado": True, "horarios": ["06:00", "05:30", "06:00"]})
    assert r.status_code == 200, r.text
    assert r.json() == {"acao": "procura", "ligado": True, "horarios": ["05:30", "06:00"]}
    # só a chave: os horários ficam
    r = await client.put("/api/denuncia/robo/agenda/procura", json={"ligado": False})
    assert r.json()["horarios"] == ["05:30", "06:00"] and r.json()["ligado"] is False
    r = await client.put("/api/denuncia/robo/agenda/ativos_inativos", json={"ligado": True, "horarios": ["23:00"]})
    assert r.status_code == 200
    for corpo, acao in (({"horarios": ["25:00"]}, "procura"), ({"horarios": "06:00"}, "procura"),
                        ({"ligado": "sim"}, "procura"), ({}, "procura"), ({"ligado": True}, "relatorio")):
        assert (await client.put(f"/api/denuncia/robo/agenda/{acao}", json=corpo)).status_code == 422

    # o mini puxa a agenda inteira (e recebe o comando "agenda" pra puxar na hora)
    assert (await client.get("/api/denuncia/sync/robo/agenda")).status_code == 401
    ag = (await client.get("/api/denuncia/sync/robo/agenda", headers=H)).json()["agenda"]
    assert ag == {"procura": {"ligado": False, "horarios": ["05:30", "06:00"]},
                  "ativos_inativos": {"ligado": True, "horarios": ["23:00"]}}
    cmds = (await client.get("/api/denuncia/sync/robo/comandos", headers=H)).json()["comandos"]
    assert [(c["tipo"], c["dados"]["acao"], c["dados"]["ligado"]) for c in cmds] == [
        ("agenda", "procura", True), ("agenda", "procura", False), ("agenda", "ativos_inativos", True)]

    j = (await client.get("/api/denuncia/robo")).json()
    passos = {x["acao"]: x for x in j["passos"]}
    assert passos["ativos_inativos"]["agenda"]["ligado"] is True
    assert passos["procura"]["agenda"]["horarios"] == ["05:30", "06:00"]



async def test_robo_tratado_some_e_resolve_no_mini(client, make_user, auth_as):
    problema = {"chave": "problemas", "estado": "erro", "detalhe": "", "o_que_fazer": "",
                "dados": {"lista": [
        {"quando": "2026-09-30 12:26", "tarefa": "código do sei", "problema": "não chegou",
         "pergunta": "encaminhar", "bloqueia": True, "chave": "codigo_sei"},
        {"quando": "2026-09-30 12:00", "tarefa": "Shopee parou", "problema": "modal",
         "pergunta": "conferir", "bloqueia": False, "chave": None},
    ]}}
    resumo = _resumo()
    resumo["itens"].append(problema)
    assert (await client.post("/api/denuncia/sync/robo", json=resumo, headers=H)).status_code == 200

    auth_as(await make_user(permissions={"denuncia": {"view": True}}))
    oc = (await client.get("/api/denuncia/robo")).json()["ocorrencias"]
    sei = next(o for o in oc if o["titulo"] == "código do sei")
    shopee = next(o for o in oc if o["titulo"] == "Shopee parou")
    r = await client.post("/api/denuncia/robo/ocorrencias/tratar", json={"chave": sei["chave"]})
    assert r.status_code == 403

    auth_as(await make_user(permissions={"denuncia": {"view": True, "edit": True}}))
    r = await client.post("/api/denuncia/robo/ocorrencias/tratar", json={"chave": "rm -rf"})
    assert r.status_code == 422
    for o in (sei, shopee):
        r = await client.post("/api/denuncia/robo/ocorrencias/tratar",
                              json={"chave": o["chave"], "titulo": o["titulo"],
                                    "robo_chave": o["robo_chave"]})
        assert r.status_code == 200, r.text
    j = (await client.get("/api/denuncia/robo")).json()
    assert not [o for o in j["ocorrencias"] if o["origem"] == "robo"]
    # só o que tem chave própria no robô vira comando pro mini
    cmds = (await client.get("/api/denuncia/sync/robo/comandos", headers=H)).json()["comandos"]
    assert [(c["tipo"], c["dados"]) for c in cmds] == [("resolver", {"chave": "codigo_sei"})]



# ───────────────────────────────── visão por loja (01/10/2026)


async def test_anuncios_por_loja_soma_vendas_e_abre_a_loja(client, make_user, auth_as):
    await _carga(client)
    r = await client.post(
        "/api/denuncia/sync/anuncios",
        json={"linhas": [
            _anuncio("B1", shop_id="222", loja="loja_y", vendas=300, grupo="GRUPO 2"),
            _anuncio("B2", shop_id="222", loja="loja_y", vendas=700, grupo="GRUPO 1",
                     situacao="fora do ar"),
            _anuncio("C1", shop_id=None, loja="sem id", vendas=1, grupo="GRUPO 2"),
        ]},
        headers=H,
    )
    assert r.status_code == 200, r.text
    auth_as(await make_user(permissions={"denuncia": {"view": True}}))
    j = (await client.get("/api/denuncia/anuncios/lojas")).json()
    lojas = {x["loja"]: x for x in j["itens"]}
    # loja_x: A1 (50) + A2 (5); A3 descartado e A4 própria ficam fora, como na lista
    assert lojas["loja_x"]["anuncios"] == 2 and lojas["loja_x"]["vendas"] == 55
    assert lojas["loja_x"]["denuncias"] == 3 and lojas["loja_x"]["com_denuncia"] == 2
    y = lojas["loja_y"]
    assert (y["anuncios"], y["vendas"], y["nosso"], y["diversos"], y["no_ar"], y["fora_do_ar"]) == (
        2, 1000, 1, 1, 1, 1)
    assert [x["loja"] for x in j["itens"]][:2] == ["loja_y", "loja_x"]   # mais vendas primeiro
    assert lojas["sem id"]["chave"] == "_sem"
    assert j["numeros"]["total"] == 5 and j["total"] == 3
    # abrir a loja: a lista de sempre filtrada por ela
    def ids(r):
        return sorted(x["id"] for x in r.json()["itens"])
    assert ids(await client.get("/api/denuncia/anuncios", params={"loja": "222"})) == ["B1", "B2"]
    assert ids(await client.get("/api/denuncia/anuncios", params={"loja": "_sem"})) == ["C1"]
    # os filtros de cima valem: só o Nosso
    j = (await client.get("/api/denuncia/anuncios/lojas", params={"grupo": "GRUPO 1"})).json()
    assert {x["loja"]: x["anuncios"] for x in j["itens"]} == {"loja_x": 2, "loja_y": 1}


async def test_denuncias_por_loja(client, make_user, auth_as):
    await _carga(client)
    await client.post(
        "/api/denuncia/sync/denuncias",
        json={"linhas": [
            {"id": 4, "anuncio_id": "A2", "canal": "Mercado Livre", "protocolo": None,
             "data": "2026-09-30", "situacao": "Improcedente", "resultado": "Improcedente",
             "tipo": "normal"},
            {"id": 5, "anuncio_id": "A1", "canal": "Mercado Livre", "protocolo": None,
             "data": "2026-10-01", "situacao": "Procedente", "resultado": "Anúncio removido",
             "tipo": "normal"},
        ]},
        headers=H,
    )
    auth_as(await make_user(permissions={"denuncia": {"view": True}}))
    j = (await client.get("/api/denuncia/denuncias/lojas")).json()
    assert j["total"] == 1
    x = j["itens"][0]
    # P1 (Shopee, cobre A1 e A2) conta 1; Anatel SEI 1; ML 2
    assert (x["loja"], x["denuncias"], x["removidas"], x["recusadas"], x["aguardando"]) == (
        "loja_x", 4, 1, 1, 2)
    assert x["canais"] == {"Shopee": 1, "Anatel SEI": 1, "Mercado Livre": 2}
    assert x["anuncios"] == 2 and x["anuncios_no_ar"] == 1 and x["ultima"].startswith("2026-10-01")
    j = (await client.get("/api/denuncia/denuncias/lojas",
                          params={"canal": "Mercado Livre"})).json()
    assert j["itens"][0]["denuncias"] == 2
    j = (await client.get("/api/denuncia/denuncias", params={"loja": "111"})).json()
    assert j["total"] == 4
    r = await client.get("/api/denuncia/denuncias", params={"loja": "999"})
    assert r.json()["total"] == 0


def test_painel_status_na_loja_e_na_anatel():
    """01/10 (Vinicius): o que a loja fez com a denúncia e onde o anúncio está no caminho da
    Anatel — o Nosso só vai depois da recusa; o Diversos vai sem denúncia na loja; sem o
    print da página não vai."""
    from app.services import denuncia_painel as p

    nosso = {"situacao": "ativo", "grupo": "GRUPO 1", "hom": "08216-25-18234",
             "marketplace": "Shopee"}
    diversos = {**nosso, "grupo": "GRUPO 2", "hom": "04814-15-08787"}
    loja = lambda *ds: p.status_loja(list(ds))  # noqa: E731
    def den(data, situacao, resultado):
        return {"canal": "Shopee", "data": data, "situacao": situacao, "resultado": resultado}
    pend = den("2026-09-20", "Em análise", "Aguardando")
    rec = den("2026-09-25", "Improcedente", "Improcedente")
    rem = den("2026-09-22", "Procedente", "Anúncio removido")
    assert loja()["chave"] == "nao"
    assert loja(pend)["chave"] == "aguardando"
    assert loja(pend, rec)["chave"] == "recusou" and loja(pend, rec)["tentativas"] == 2
    assert loja(rec, rem)["chave"] == "removido"   # removido vale mesmo com recusa antes
    # Anatel não é loja
    assert loja({"canal": "Anatel SEI", "data": "2026-09-30"})["chave"] == "nao"
    # a resposta já chegou e o robô confere o anúncio; no Diversos a denúncia velha é "antiga"
    conf = {**pend, "resultado_nota": "ML respondeu em 2026-09-21 18:33: 'não identificamos…' — conferindo"}
    assert p.status_loja([conf])["chave"] == "conferindo"
    # Diversos (não é mais denunciado nas lojas): resposta de recusa = recusou; pendente = em branco
    assert p.status_loja([pend], "GRUPO 2")["chave"] == "vazio"
    assert p.status_loja([conf], "GRUPO 2")["chave"] == "recusou"
    assert p.status_loja([rec], "GRUPO 2")["chave"] == "recusou"   # desfecho continua valendo
    assert p.status_anatel(nosso, [conf], p.status_loja([conf]), True)["chave"] == "esperando_recusa"

    def anatel(a, ds, tem_print=True):
        return p.status_anatel(a, ds, p.status_loja(ds), tem_print)["chave"]
    sei = {"canal": "Anatel SEI", "protocolo": "53500.144118/2026-11", "data": "2026-10-01"}
    assert anatel(nosso, [pend]) == "esperando_recusa"
    assert anatel(nosso, []) == "falta_loja"
    assert anatel(nosso, [rec]) == "fila"
    assert anatel(nosso, [rec], tem_print=False) == "falta_print"
    assert anatel(nosso, [rem]) == "nada"
    assert anatel(diversos, []) == "fila"                     # Diversos sem denúncia na loja vai
    assert anatel({**diversos, "hom": ""}, []) == "nada"      # sem nº declarado não cabe
    assert anatel({**diversos, "hom": "", "marketplace": "TikTok Shop"}, []) == "fila"
    assert anatel({**diversos, "situacao": "fora do ar"}, []) == "nada"
    st = p.status_anatel(diversos, [pend, sei], p.status_loja([pend]), False)
    assert st["chave"] == "processo" and st["protocolo"] == "53500.144118/2026-11"


async def test_painel_junta_anuncios_e_denuncias(client, make_user, auth_as):
    await _carga(client)
    r = await client.post(
        "/api/denuncia/sync/anuncios",
        json={"linhas": [
            _anuncio("B1", shop_id="222", loja="loja_y", vendas=300, grupo="GRUPO 2",
                     hom="04814-15-08787"),
            _anuncio("B2", shop_id="222", loja="loja_y", vendas=700, grupo="GRUPO 1"),
            _anuncio("B3", shop_id="222", loja="loja_y", vendas=1, grupo="GRUPO 1"),
        ]},
        headers=H,
    )
    assert r.status_code == 200, r.text
    await client.post(
        "/api/denuncia/sync/denuncias",
        json={"linhas": [
            {"id": 10, "anuncio_id": "B2", "canal": "Shopee", "data": "2026-09-20",
             "situacao": "Improcedente", "resultado": "Improcedente", "tipo": "normal"},
            {"id": 11, "anuncio_id": "B1", "canal": "Anatel SEI", "protocolo": "53500.1/2026-1",
             "data": "2026-10-01", "situacao": "Enviada", "resultado": "Aguardando"},
            {"id": 12, "anuncio_id": "B3", "canal": "Anatel SEI", "protocolo": "53500.1/2026-1",
             "data": "2026-10-01", "situacao": "Enviada", "resultado": "Aguardando"},
        ]},
        headers=H,
    )
    await client.post(
        "/api/denuncia/sync/provas",
        json={"linhas": [{"id": 90, "anuncio_id": "B2", "tipo": "Captura no ato",
                          "nome_original": "c.png"}]},
        headers=H,
    )
    auth_as(await make_user(permissions={"denuncia": {"view": True}}))
    j = (await client.get("/api/denuncia/painel")).json()
    lojas = {x["loja"]: x for x in j["itens"]}
    y = lojas["loja_y"]
    assert (y["anuncios"], y["vendas"], y["nosso"], y["diversos"]) == (3, 1001, 2, 1)
    assert y["na_loja"]["recusou"] == 1 and y["na_loja"]["nao"] == 2
    # B2 (Nosso, recusou, com print) na fila; B1 e B3 com o mesmo processo SEI
    assert y["na_anatel"]["fila"] == 1 and y["na_anatel"]["processo"] == 2
    assert y["processos"] == ["53500.1/2026-1"]
    assert j["numeros"]["processos"] == 1 and j["numeros"]["na_loja"]["recusou"] == 1
    # loja_x: A1 com denúncia Shopee "Enviada" → aguardando; o SEI da carga não tem protocolo
    assert lojas["loja_x"]["na_loja"]["aguardando"] == 2
    # o caso da carga (CASO-001, anúncio A1) aparece na loja
    assert [k["codigo"] for k in lojas["loja_x"]["casos"]] == ["CASO-001"]
    # por anúncio, filtrando o que está na fila da Anatel
    j = (await client.get("/api/denuncia/painel",
                          params={"visao": "anuncios", "na_anatel": "fila"})).json()
    assert [x["id"] for x in j["itens"]] == ["B2"]
    assert j["itens"][0]["loja_st"]["rotulo"] == "recusou"
    assert j["itens"][0]["anatel_st"]["rotulo"] == "na fila"
    j = (await client.get("/api/denuncia/painel",
                          params={"visao": "anuncios", "loja": "222", "ordem": "vendas"})).json()
    assert [x["id"] for x in j["itens"]] == ["B2", "B1", "B3"]
    # a ficha junta tudo: status e os outros anúncios da mesma petição
    f = (await client.get("/api/denuncia/anuncios/B1")).json()
    assert f["status"]["na_anatel"]["chave"] == "processo"
    assert [x["id"] for x in f["junto"]["Anatel SEI|53500.1/2026-1"]] == ["B3"]
    assert f["denuncias"][0]["id"] == 11


async def test_anexar_prova_no_caso_e_o_mini_entrega(client, make_user, auth_as, pasta_uploads):
    """01/10 (Vinicius: "chegou o produto, onde eu vou colocar as provas?"): anexa na ficha do
    caso; o mini busca, baixa e marca entregue (o arquivo daqui some)."""
    await _carga(client)
    auth_as(await make_user(permissions={"denuncia": {"view": True, "edit": True}}))
    r = await client.post("/api/denuncia/casos/7/anexos", data={"tipo": "nfe", "obs": "NF da compra"},
                          files={"arquivo": ("nf.pdf", b"%PDF-1.4 nota", "application/pdf")})
    assert r.status_code == 200, r.text
    a = r.json()["anexos"][0]
    assert (a["tipo_prova"], a["anuncio_id"], a["tem_arquivo"], a["entregue_em"]) == ("NF-e", "A1", True, None)
    # vídeo só como link do MEGA (com a chave)
    r = await client.post("/api/denuncia/casos/7/anexos", data={"tipo": "video", "link": "https://youtu.be/x"})
    assert r.status_code == 422
    r = await client.post("/api/denuncia/casos/7/anexos",
                          data={"tipo": "video", "link": "https://mega.nz/file/AbC#chave"})
    assert r.status_code == 200 and r.json()["anexos"][0]["link"].startswith("https://mega.nz/")
    r = await client.post("/api/denuncia/casos/7/anexos", data={"tipo": "foto"})
    assert r.status_code == 422   # foto sem arquivo
    # a ficha do caso mostra os anexos e os tipos
    f = (await client.get("/api/denuncia/casos/7")).json()
    assert len(f["anexos"]) == 2 and any(t["chave"] == "devolucao" for t in f["tipos_anexo"])
    # o mini: pendentes, baixa o arquivo e responde
    pend = (await client.get("/api/denuncia/sync/anexos", headers=H)).json()["anexos"]
    assert [x["tipo"] for x in pend] == ["nfe", "video"]
    nf = pend[0]
    r = await client.get(f"/api/denuncia/sync/anexos/{nf['id']}/arquivo", headers=H)
    assert r.status_code == 200 and r.content == b"%PDF-1.4 nota"
    r = await client.post(f"/api/denuncia/sync/anexos/{nf['id']}", json={"ok": True, "resultado": "prova #900"},
                          headers=H)
    assert r.status_code == 200
    pend = (await client.get("/api/denuncia/sync/anexos", headers=H)).json()["anexos"]
    assert [x["tipo"] for x in pend] == ["video"]
    assert not list((pasta_uploads / "denuncia" / "anexos").glob(f"{nf['id']}.*"))
    # sem permissão de editar não anexa
    auth_as(await make_user(permissions={"denuncia": {"view": True}}))
    r = await client.post("/api/denuncia/casos/7/anexos", data={"tipo": "nfe"},
                          files={"arquivo": ("nf.pdf", b"x", "application/pdf")})
    assert r.status_code == 403


async def test_caso_extra_compra_e_processo(client, make_user, auth_as):
    """01/10 (Vinicius): onde comprou, pedido, previsão de entrega; nº do processo, link do
    Jusbrasil e a última movimentação — preenchidos na ficha do caso."""
    await _carga(client)
    auth_as(await make_user(permissions={"denuncia": {"view": True, "edit": True}}))
    r = await client.put("/api/denuncia/casos/7/extra", json={
        "compra_data": "2026-09-29", "compra_loja": "loja_x (Shopee)", "compra_pedido": "2609ABC",
        "compra_previsao": "2026-10-05",
        "processo_numero": "1001234-56.2026.8.26.0100",
        "processo_link": "https://www.jusbrasil.com.br/processos/123",
        "mov_data": "2026-10-01", "mov_texto": "Distribuído", "mov_status": "Em andamento"})
    assert r.status_code == 200, r.text
    e = (await client.get("/api/denuncia/casos")).json()["itens"][0]["extra"]
    assert e["compra_previsao"] == "2026-10-05" and e["processo_numero"].startswith("1001234")
    assert e["compra_data"] == "2026-09-29"
    assert (await client.get("/api/denuncia/casos/7")).json()["extra"]["mov_status"] == "Em andamento"
    # só o que vem muda; "" apaga
    r = await client.put("/api/denuncia/casos/7/extra", json={"mov_status": ""})
    assert r.json()["extra"]["mov_status"] is None and r.json()["extra"]["mov_texto"] == "Distribuído"
    assert (await client.put("/api/denuncia/casos/7/extra", json={"mov_data": "01/10"})).status_code == 422
    assert (await client.put("/api/denuncia/casos/7/extra",
                             json={"processo_link": "jusbrasil.com"})).status_code == 422
    assert (await client.put("/api/denuncia/casos/99/extra", json={})).status_code == 404


Y = {"lojas": [{"marketplace": "Shopee", "shop_id": "222"}]}


async def test_criar_caso_por_loja_e_excluir(client, make_user, auth_as):
    """01/10: "criar" / "enviar para caso" pede ao mini um caso por loja (todos os anúncios dela);
    a lixeira estorna — a loja volta a ficar sem caso."""
    await _carga(client)  # loja_x (111): A1 já tem o CASO-001
    await client.post(
        "/api/denuncia/sync/anuncios",
        json={"linhas": [
            _anuncio("B1", shop_id="222", loja="loja_y", vendas=300, preco=999.9,
                     preco_em="2026-10-01 10:00:00"),
            _anuncio("B2", shop_id="222", loja="loja_y", vendas=700, situacao="fora do ar"),
            _anuncio("B3", shop_id="222", loja="loja_y", vendas=5),
            _anuncio("B4", shop_id="222", loja="loja_y", propria=1),
        ]},
        headers=H,
    )
    auth_as(await make_user(permissions={"denuncia": {"view": True}}))
    r = await client.post("/api/denuncia/casos/criar", json={"lojas": [{"shop_id": "222"}]})
    assert r.status_code == 403

    auth_as(await make_user(permissions={"denuncia": {"view": True, "edit": True}}))
    assert (await client.post("/api/denuncia/casos/criar", json={"lojas": []})).status_code == 422
    r = await client.post("/api/denuncia/casos/criar", json={"lojas": [
        *Y["lojas"], {"marketplace": "Shopee", "shop_id": "111"}]})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["criados"] == [{"loja": "loja_y", "shop_id": "222", "anuncios": 3}]
    assert j["pulados"][0]["motivo"].startswith("já tem caso (CASO-001")

    # o mini recebe o pedido: principal = o mais vendido NO AR; a loja própria fica de fora
    cmds = (await client.get("/api/denuncia/sync/robo/comandos", headers=H)).json()["comandos"]
    assert [(c["tipo"], c["dados"]["anuncio_ids"]) for c in cmds] == [
        ("criar_caso", ["B1", "B3", "B2"])]
    # enquanto isso a loja aparece "criando…" e não deixa pedir de novo
    lojas = {x["loja"]: x for x in (await client.get("/api/denuncia/painel")).json()["itens"]}
    assert lojas["loja_y"]["caso_pendente"] is True
    assert lojas["loja_y"]["casos"] == []
    r = await client.post("/api/denuncia/casos/criar", json=Y)
    assert r.json()["pulados"][0]["motivo"] == "o caso já está sendo criado"

    # o mini abre o Caso 008 da loja e a cópia traz: vale para TODOS os anúncios da loja
    await client.post(f"/api/denuncia/sync/robo/comandos/{cmds[0]['id']}",
                      json={"ok": True, "resultado": "Caso 008 aberto"}, headers=H)
    await client.post(
        "/api/denuncia/sync/casos",
        json={"linhas": [
            {"id": 7, "codigo": "CASO-001", "anuncio_id": "A1", "titulo": "Caso 1",
             "status": "Com jurídico", "aberto_em": "2026-09-15"},
            {"id": 8, "codigo": "Caso 008", "anuncio_id": "B1", "titulo": "loja_y · 3 anúncios",
             "status": "Aberto", "aberto_em": "2026-10-01", "shop_id": "222",
             "marketplace": "Shopee", "anuncios": '["B1", "B3", "B2"]'},
        ]},
        headers=H,
    )
    j = (await client.get("/api/denuncia/painel",
                          params={"visao": "anuncios", "loja": "222"})).json()
    assert {x["id"]: [k["codigo"] for k in x["casos"]] for x in j["itens"]} == {
        "B1": ["Caso 008"], "B2": ["Caso 008"], "B3": ["Caso 008"]}
    lojas = {x["loja"]: x for x in (await client.get("/api/denuncia/painel")).json()["itens"]}
    assert [k["codigo"] for k in lojas["loja_y"]["casos"]] == ["Caso 008"]
    assert lojas["loja_y"]["caso_pendente"] is False
    casos = (await client.get("/api/denuncia/casos")).json()["itens"]
    c8 = next(c for c in casos if c["id"] == 8)
    assert (c8["n_anuncios"], c8["por_loja"], c8["loja"]) == (3, True, "loja_y")
    assert (c8["preco"], c8["preco_em"]) == (999.9, "2026-10-01 10:00:00")  # lista de compra
    d = (await client.get("/api/denuncia/casos/8")).json()
    assert [x["id"] for x in d["anuncios_do_caso"]] == ["B1", "B3", "B2"]
    f = (await client.get("/api/denuncia/anuncios/B3")).json()
    assert f["casos"][0]["codigo"] == "Caso 008"

    # lixeira: some na hora; o mini recebe o pedido
    r = await client.post("/api/denuncia/casos/8/excluir", json={})
    assert r.status_code == 200, r.text
    assert [c["id"] for c in (await client.get("/api/denuncia/casos")).json()["itens"]] == [7]
    lojas = {x["loja"]: x for x in (await client.get("/api/denuncia/painel")).json()["itens"]}
    assert lojas["loja_y"]["casos"] == []
    cmds = (await client.get("/api/denuncia/sync/robo/comandos", headers=H)).json()["comandos"]
    assert [(c["tipo"], c["dados"]["caso_id"]) for c in cmds] == [("excluir_caso", 8)]
    # o mini estorna (status Excluído, sem anúncio) e a cópia traz: continua fora, sem apagar
    await client.post(f"/api/denuncia/sync/robo/comandos/{cmds[0]['id']}",
                      json={"ok": True, "resultado": "Caso 008 excluído"}, headers=H)
    await client.post(
        "/api/denuncia/sync/casos",
        json={"linhas": [{"id": 8, "codigo": "Caso 008", "anuncio_id": None, "status": "Excluído",
                          "excluido_anuncio_id": "B1", "shop_id": "222", "anuncios": '["B1"]'}]},
        headers=H,
    )
    assert [c["id"] for c in (await client.get("/api/denuncia/casos")).json()["itens"]] == [7]
    assert (await client.post("/api/denuncia/casos/8/excluir", json={})).status_code == 409
    # e a loja pode ganhar caso de novo
    r = await client.post("/api/denuncia/casos/criar", json=Y)
    assert r.json()["criados"][0]["anuncios"] == 3


async def test_criar_caso_que_falhou_aparece(client, make_user, auth_as):
    await _carga(client)
    await client.post("/api/denuncia/sync/anuncios",
                      json={"linhas": [_anuncio("B1", shop_id="222", loja="loja_y")]}, headers=H)
    auth_as(await make_user(permissions={"denuncia": {"view": True, "edit": True}}))
    await client.post("/api/denuncia/casos/criar", json=Y)
    cmd = (await client.get("/api/denuncia/sync/robo/comandos", headers=H)).json()["comandos"][0]
    await client.post(f"/api/denuncia/sync/robo/comandos/{cmd['id']}",
                      json={"ok": False, "resultado": "erro: 404 sem anúncio"}, headers=H)
    j = (await client.get("/api/denuncia/painel")).json()
    assert j["falhas_caso"][0]["texto"] == "criar o caso da loja loja_y"
    assert "404" in j["falhas_caso"][0]["resultado"]
    lojas = {x["loja"]: x for x in j["itens"]}
    assert lojas["loja_y"]["caso_pendente"] is False
