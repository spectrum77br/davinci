# ruff: noqa: E501
"""Denúncia › Casos › Jurídico: "Montar pasta jurídico" e "Enviar ao jurídico" (13/10/2026).

Vinicius: o passo 9 do robô virou botão na ficha do caso — "eu acionar esses botão dentro dos casos
e enviar quando eu quiser". O DaVinci confere o checklist e pede ao Mac mini (comandos
`juridico_montar` / `juridico_enviar`); o mini sobe o zip, conta como foi e, enviado ao advogado,
o caso vira "Com jurídico". Enviado a outro e-mail (teste) o caso não muda."""

import hashlib
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.config import get_settings
from app.models import DenunciaRemetente
from app.routers import denuncia as rota

TOKEN = "dnc_juridico_" + uuid.uuid4().hex
H = {"Authorization": f"Bearer {TOKEN}"}
ZIP = b"PK\x03\x04 pasta do caso para o advogado"
ADVOGADO = {"nome": "Dr. Fulano", "email": "adv@escritorio.com.br"}

_TABELAS = (
    "denuncia_anuncios", "denuncia_denuncias", "denuncia_casos", "denuncia_compras",
    "denuncia_provas", "denuncia_remetentes", "denuncia_robo_comandos", "denuncia_casos_extra",
    "denuncia_juridico_pastas",
)


@pytest_asyncio.fixture(autouse=True)
async def _limpa(db):
    for t in _TABELAS:
        await db.execute(text(f"DELETE FROM {t}"))  # noqa: S608
    db.add(DenunciaRemetente(nome="mini juridico", token_hash=hashlib.sha256(TOKEN.encode()).hexdigest()))
    await db.commit()
    yield
    for t in _TABELAS:
        await db.execute(text(f"DELETE FROM {t}"))  # noqa: S608
    await db.commit()


@pytest.fixture
def pasta_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "uploads_dir", str(tmp_path))
    return tmp_path


@pytest_asyncio.fixture
async def editor(make_user, auth_as):
    u = await make_user(permissions={"denuncia": {"view": True, "edit": True}})
    auth_as(u)
    return u


async def _sync(client, tabela, linhas):
    r = await client.post(f"/api/denuncia/sync/{tabela}", json={"linhas": linhas}, headers=H)
    assert r.status_code == 200, r.text


async def _caso(client, provas=True):
    """CASO-001 do anúncio A1, com uma denúncia; `provas` = todas as do checklist."""
    await _sync(client, "anuncios", [{"id": "A1", "marketplace": "Shopee", "shop_id": "111",
                                      "loja": "loja_x", "titulo": "Celular X", "propria": 0}])
    await _sync(client, "denuncias", [{"id": 1, "anuncio_id": "A1", "canal": "Shopee",
                                       "protocolo": "P1", "data": "2026-09-20", "situacao": "Enviada"}])
    await _sync(client, "casos", [{"id": 7, "codigo": "CASO-001", "anuncio_id": "A1",
                                   "titulo": "Caso 1", "status": "Aberto", "aberto_em": "2026-09-15"}])
    await _sync(client, "compras", [{"id": 3, "anuncio_id": "A1", "caso_id": 7, "status": "Recebido"}])
    if provas:
        await _sync(client, "provas", [
            {"id": 10 + i, "anuncio_id": "A1", "caso_id": 7 if i else None, "tipo": tipo}
            for i, tipo in enumerate(("Captura no ato", "Tela do pedido", "Vídeo", "Foto", "NF-e"))
        ])


async def _pronta(client, *, para=ADVOGADO):
    """Pede a pasta (checklist completo) e faz o papel do mini: montando → zip → pronta."""
    assert (await client.put("/api/denuncia/casos/7/extra", json={"selo_anatel": "sim"})).status_code == 200
    r = await client.post("/api/denuncia/casos/7/pasta")
    assert r.status_code == 200, r.text
    pid = r.json()["pasta"]["id"]
    assert (await client.post(f"/api/denuncia/sync/juridico/pastas/{pid}",
                              json={"status": "montando"}, headers=H)).status_code == 200
    r = await client.put(f"/api/denuncia/sync/juridico/pastas/{pid}/arquivo", content=ZIP, headers=H)
    assert r.status_code == 200 and r.json()["tamanho"] == len(ZIP)
    info = {"arquivos": ["Dossie.pdf", "Provas.zip", "video.mp4"], "avisos": ["vídeo grande"],
            "nome_zip": "Caso_001_para_o_advogado.zip", "para": para,
            "cc": "sac@makisa.com.br", "de": "sac@makisa.com.br"}
    r = await client.post(f"/api/denuncia/sync/juridico/pastas/{pid}",
                          json={"status": "pronta", "info": info}, headers=H)
    assert r.status_code == 200, r.text
    return pid


async def _comandos(client, tipo):
    cmds = (await client.get("/api/denuncia/sync/robo/comandos", headers=H)).json()["comandos"]
    return [c["dados"] for c in cmds if c["tipo"] == tipo]


async def test_checklist_incompleto_nao_pede_a_pasta(client, make_user, auth_as, editor):
    await _caso(client, provas=False)
    r = await client.post("/api/denuncia/casos/7/pasta")
    assert r.status_code == 422
    assert r.json()["detail"] == {"code": "denuncia_juridico_incompleto", "faltam": [
        "Tela do anúncio (print)", "Tela do pedido (compra de prova)",
        "Vídeo da embalagem sendo aberta", "Fotos do produto (com o selo Anatel)",
        "NF-e da compra (PDF)", "Selo Anatel respondido"]}   # a denúncia do anúncio vale de comprovante
    await _caso(client)
    r = await client.post("/api/denuncia/casos/7/pasta")
    assert r.json()["detail"]["faltam"] == ["Selo Anatel respondido"]
    # a pergunta do selo: sim / não / em branco
    assert (await client.put("/api/denuncia/casos/7/extra", json={"selo_anatel": "talvez"})).status_code == 422
    r = await client.put("/api/denuncia/casos/7/extra", json={"selo_anatel": "não"})
    assert r.json()["extra"]["selo_anatel"] == "nao"
    r = await client.put("/api/denuncia/casos/7/extra", json={"selo_anatel": ""})
    assert r.json()["extra"]["selo_anatel"] is None
    assert (await client.put("/api/denuncia/casos/7/extra", json={"selo_anatel": "sim"})).status_code == 200
    d = (await client.get("/api/denuncia/casos/7")).json()
    assert d["extra"]["selo_anatel"] == "sim" and d["pasta"] is None
    assert await _comandos(client, "juridico_montar") == []
    # quem só vê não monta
    auth_as(await make_user(permissions={"denuncia": {"view": True}}))
    assert (await client.post("/api/denuncia/casos/7/pasta")).status_code == 403
    assert (await client.post("/api/denuncia/casos/99/pasta")).status_code == 403


async def test_montar_baixar_e_enviar_ao_advogado(client, editor, pasta_uploads):
    await _caso(client)
    await client.put("/api/denuncia/casos/7/extra", json={"selo_anatel": "sim"})
    r = await client.post("/api/denuncia/casos/7/pasta")
    assert r.status_code == 200, r.text
    p = r.json()["pasta"]
    assert (p["status"], p["pedido_por"], p["envio_status"]) == ("pedida", editor.email, None)
    assert await _comandos(client, "juridico_montar") == [{
        "pasta_id": p["id"], "caso_id": 7, "codigo": "CASO-001", "anuncio_id": "A1", "selo_anatel": "sim"}]
    # montando: nem outra pasta, nem envio
    r = await client.post("/api/denuncia/casos/7/pasta")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "denuncia_pasta_em_andamento"
    r = await client.post("/api/denuncia/casos/7/enviar", json={"para": ADVOGADO["email"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "denuncia_pasta_nao_pronta"
    assert (await client.get("/api/denuncia/casos/7/pasta/arquivo")).status_code == 404

    # o mini: sem token não entra; pasta que não existe; status inválido
    assert (await client.post(f"/api/denuncia/sync/juridico/pastas/{p['id']}",
                              json={"status": "pronta"})).status_code == 401
    assert (await client.post("/api/denuncia/sync/juridico/pastas/999999",
                              json={"status": "pronta"}, headers=H)).status_code == 404
    assert (await client.post(f"/api/denuncia/sync/juridico/pastas/{p['id']}",
                              json={"status": "feita"}, headers=H)).status_code == 422
    await client.post(f"/api/denuncia/sync/juridico/pastas/{p['id']}", json={"status": "montando"}, headers=H)
    assert (await client.get("/api/denuncia/casos/7")).json()["pasta"]["status"] == "montando"
    r = await client.put(f"/api/denuncia/sync/juridico/pastas/{p['id']}/arquivo", content=ZIP, headers=H)
    assert r.status_code == 200 and r.json()["sha256"] == hashlib.sha256(ZIP).hexdigest()
    info = {"arquivos": ["Dossie.pdf", "Provas.zip"], "avisos": [], "nome_zip": "Caso_001_para_o_advogado.zip",
            "para": ADVOGADO, "cc": "sac@makisa.com.br", "de": "sac@makisa.com.br"}
    r = await client.post(f"/api/denuncia/sync/juridico/pastas/{p['id']}",
                          json={"status": "pronta", "info": info}, headers=H)
    assert r.status_code == 200
    p = (await client.get("/api/denuncia/casos/7")).json()["pasta"]
    assert (p["status"], p["tamanho"], p["tem_arquivo"], p["info"]["para"]) == ("pronta", len(ZIP), True, ADVOGADO)
    assert p["pronta_em"]
    r = await client.get("/api/denuncia/casos/7/pasta/arquivo")
    assert r.status_code == 200 and r.content == ZIP
    assert r.headers["content-type"] == "application/zip"
    assert 'filename="Caso_001_para_o_advogado.zip"' in r.headers["content-disposition"]

    # enviar: e-mail inválido; o do advogado (sem diferença de maiúscula) não é teste
    for corpo in ({}, {"para": "adv"}, {"para": ADVOGADO["email"], "cc": "sac@makisa, x"}):
        r = await client.post("/api/denuncia/casos/7/enviar", json=corpo)
        assert r.status_code == 422 and r.json()["detail"]["code"] == "denuncia_email_invalido"
    r = await client.post("/api/denuncia/casos/7/enviar",
                          json={"para": "ADV@escritorio.com.br", "cc": "sac@makisa.com.br"})
    assert r.status_code == 200, r.text
    p = r.json()["pasta"]
    assert (p["envio_status"], p["envio_pedido_por"]) == ("pedido", editor.email)
    assert p["envio_info"] == {"para": "ADV@escritorio.com.br", "cc": "sac@makisa.com.br", "teste": False}
    assert await _comandos(client, "juridico_enviar") == [{
        "pasta_id": p["id"], "caso_id": 7, "codigo": "CASO-001", "anuncio_id": "A1",
        "para": "ADV@escritorio.com.br", "cc": "sac@makisa.com.br", "teste": False}]
    for url in ("/api/denuncia/casos/7/enviar", "/api/denuncia/casos/7/pasta"):
        r = await client.post(url, json={"para": ADVOGADO["email"]})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "denuncia_envio_em_andamento"

    # o mini manda pelo Tuta: enviado → "Com jurídico" na hora (o dia do envio em Brasília)
    url = f"/api/denuncia/sync/juridico/pastas/{p['id']}/envio"
    assert (await client.post(url, json={"status": "enviando"}, headers=H)).status_code == 200
    assert (await client.post(url, json={"status": "enviado", "enviado_em": "2026-10-13T23:30:00-03:00",
                                         "resultado": "e-mail enviado"}, headers=H)).status_code == 200
    d = (await client.get("/api/denuncia/casos/7")).json()
    assert d["pasta"]["envio_status"] == "enviado" and d["pasta"]["envio_em"].startswith("2026-10-14T02:30")
    assert d["extra"]["juridico_data"] == "2026-10-13" and d["caso"]["juridico_enviado_em"] == "2026-10-13"
    assert d["status_tela"] == "Com jurídico" and d["extra"]["status_manual"] is None
    assert (await client.get("/api/denuncia/casos")).json()["itens"][0]["status"] == "Com jurídico"
    for url in ("/api/denuncia/casos/7/enviar", "/api/denuncia/casos/7/pasta"):
        r = await client.post(url, json={"para": ADVOGADO["email"]})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "denuncia_juridico_ja_enviado"
    assert (await client.get("/api/denuncia/casos/7/pasta/arquivo")).status_code == 200


async def test_envio_de_teste_nao_muda_o_caso(client, editor, pasta_uploads):
    """Vinicius escolhe o e-mail na hora: manda pra ele mesmo antes. Teste enviado não prende nada."""
    await _caso(client)
    pid = await _pronta(client)
    r = await client.post("/api/denuncia/casos/7/enviar", json={"para": "vinicius@makisa.com.br", "cc": None})
    assert r.status_code == 200, r.text
    assert r.json()["pasta"]["envio_info"] == {"para": "vinicius@makisa.com.br", "cc": None, "teste": True}
    assert (await _comandos(client, "juridico_enviar"))[0]["teste"] is True
    url = f"/api/denuncia/sync/juridico/pastas/{pid}/envio"
    assert (await client.post(url, json={"status": "enviado"}, headers=H)).status_code == 200
    d = (await client.get("/api/denuncia/casos/7")).json()
    assert d["pasta"]["envio_status"] == "teste" and d["pasta"]["envio_em"]
    assert d["extra"]["juridico_data"] is None and d["caso"]["juridico_enviado_em"] is None
    assert d["status_tela"] == "Produto recebido"
    # o teste não prende: dá pra enviar de novo (deu erro no Tuta → libera de novo)
    r = await client.post("/api/denuncia/casos/7/enviar", json={"para": ADVOGADO["email"]})
    assert r.status_code == 200 and r.json()["pasta"]["envio_info"]["teste"] is False
    assert r.json()["pasta"]["envio_em"] is None
    assert (await client.post(url, json={"status": "erro", "resultado": "Tuta fechado"}, headers=H)).status_code == 200
    p = (await client.get("/api/denuncia/casos/7")).json()["pasta"]
    assert (p["envio_status"], p["envio_resultado"]) == ("erro", "Tuta fechado")
    assert (await client.get("/api/denuncia/casos/7")).json()["extra"]["juridico_data"] is None

    # montar de novo (depois do teste/erro): pasta nova; o zip da velha sai do disco quando chega o novo
    r = await client.post("/api/denuncia/casos/7/pasta")
    assert r.status_code == 200, r.text
    nova = r.json()["pasta"]["id"]
    assert nova != pid and (pasta_uploads / f"denuncia/juridico/{pid}.zip").exists()
    r = await client.put(f"/api/denuncia/sync/juridico/pastas/{nova}/arquivo", content=b"PK novo", headers=H)
    assert r.status_code == 200
    assert not (pasta_uploads / f"denuncia/juridico/{pid}.zip").exists()
    assert (pasta_uploads / f"denuncia/juridico/{nova}.zip").read_bytes() == b"PK novo"


async def test_pedido_que_o_mini_nao_pegou_caduca(client, db, editor, pasta_uploads):
    """O mini só vê comando de até 1 h: o pedido que ficou parado vira erro e libera o botão."""
    await _caso(client)
    await client.put("/api/denuncia/casos/7/extra", json={"selo_anatel": "nao"})
    r = await client.post("/api/denuncia/casos/7/pasta")
    pid = r.json()["pasta"]["id"]
    await db.execute(text("UPDATE denuncia_juridico_pastas SET pedido_em = now() - interval '61 minutes'"))
    await db.commit()
    p = (await client.get("/api/denuncia/casos/7")).json()["pasta"]
    assert p["status"] == "erro" and "não pegou o pedido em 1 h" in p["resultado"]
    r = await client.post("/api/denuncia/casos/7/pasta")
    assert r.status_code == 200 and r.json()["pasta"]["id"] != pid

    # o envio também
    await db.execute(text("DELETE FROM denuncia_juridico_pastas"))
    await db.commit()
    pid = await _pronta(client)
    assert (await client.post("/api/denuncia/casos/7/enviar", json={"para": ADVOGADO["email"]})).status_code == 200
    await db.execute(text("UPDATE denuncia_juridico_pastas SET envio_pedido_em = now() - interval '61 minutes'"))
    await db.commit()
    p = (await client.get("/api/denuncia/casos/7")).json()["pasta"]
    assert p["envio_status"] == "erro" and "não pegou o envio em 1 h" in p["envio_resultado"]
    assert p["status"] == "pronta"   # a pasta continua montada
    assert (await client.post("/api/denuncia/casos/7/enviar", json={"para": ADVOGADO["email"]})).status_code == 200


async def test_zip_grande_demais(client, editor, pasta_uploads, monkeypatch):
    await _caso(client)
    await client.put("/api/denuncia/casos/7/extra", json={"selo_anatel": "sim"})
    pid = (await client.post("/api/denuncia/casos/7/pasta")).json()["pasta"]["id"]
    monkeypatch.setattr(rota, "TETO_PASTA_BYTES", 10)
    r = await client.put(f"/api/denuncia/sync/juridico/pastas/{pid}/arquivo", content=ZIP, headers=H)
    assert r.status_code == 413 and r.json()["detail"]["code"] == "denuncia_pasta_grande_demais"
    assert list((pasta_uploads / "denuncia/juridico").iterdir()) == []
    assert (await client.get("/api/denuncia/casos/7")).json()["pasta"]["tem_arquivo"] is False
