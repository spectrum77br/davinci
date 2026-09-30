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
             "nome_original": "x.png", "sha256": sha, "tamanho": len(conteudo)},
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
