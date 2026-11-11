# ruff: noqa: E501
"""Ouvidoria › Jurídico (11/11/2026): os processos num lugar só.

Vinicius: data, nº do processo, pedido/referência, origem (Denúncia / Logística / à mão), autor,
réu, advogado, última movimentação, Jusbrasil — Opção B (+ Assunto, Situação, Raio-X).
O caso da Denúncia que foi ao advogado entra sozinho; o que a pessoa edita no painel não é
sobrescrito pelo caso."""

import hashlib
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.models import Chamado, DenunciaRemetente

TOKEN = "dnc_juridico_painel_" + uuid.uuid4().hex
H = {"Authorization": f"Bearer {TOKEN}"}

_TABELAS = (
    "juridico_movimentacoes", "juridico_processos", "denuncia_anuncios", "denuncia_casos",
    "denuncia_compras", "denuncia_remetentes", "denuncia_robo_comandos", "denuncia_casos_extra",
    "denuncia_juridico_pastas",
)


@pytest_asyncio.fixture(autouse=True)
async def _limpa(db):
    for t in _TABELAS:
        await db.execute(text(f"DELETE FROM {t}"))  # noqa: S608
    db.add(DenunciaRemetente(nome="mini painel juridico", token_hash=hashlib.sha256(TOKEN.encode()).hexdigest()))
    await db.commit()
    yield
    for t in _TABELAS:
        await db.execute(text(f"DELETE FROM {t}"))  # noqa: S608
    await db.commit()


@pytest_asyncio.fixture
async def editor(make_user, auth_as):
    u = await make_user(permissions={"denuncia": {"view": True, "edit": True, "delete": True}})
    auth_as(u)
    return u


async def _sync(client, tabela, linhas):
    r = await client.post(f"/api/denuncia/sync/{tabela}", json={"linhas": linhas}, headers=H)
    assert r.status_code == 200, r.text


async def _casos(client):
    """Caso 006 (nosso certificado, já com o advogado) e Caso 012 (ainda aberto)."""
    await _sync(client, "anuncios", [
        {"id": "A6", "marketplace": "Shopee", "shop_id": "606", "loja": "Express Eletrônicos",
         "titulo": "Hotwav A17", "propria": 0, "grupo": "GRUPO 1"},
        {"id": "A12", "marketplace": "Shopee", "shop_id": "612", "loja": "TECH TUDO",
         "titulo": "Hotwav A17", "propria": 0, "grupo": "GRUPO 1"},
    ])
    await _sync(client, "casos", [
        {"id": 6, "codigo": "Caso 006", "anuncio_id": "A6", "titulo": "Caso 6", "status": "Com jurídico",
         "aberto_em": "2026-09-29", "juridico": "Dr. Paulo Dias",
         "juridico_enviado_em": "2026-10-13 13:10:00"},
        {"id": 12, "codigo": "Caso 012", "anuncio_id": "A12", "titulo": "Caso 12", "status": "Aberto",
         "aberto_em": "2026-10-20"},
    ])
    # a compra de prova do Caso 006 (o pedido dela NÃO vira a referência: a referência é o caso)
    await _sync(client, "compras", [{"id": 3, "anuncio_id": "A6", "caso_id": 6, "status": "Recebido",
                                     "pedido": "260929BFHR9PH3"}])


async def _lista(client):
    r = await client.get("/api/juridico/processos")
    assert r.status_code == 200, r.text
    return r.json()["itens"]


@pytest.mark.asyncio
async def test_caso_que_foi_ao_advogado_entra_sozinho_com_o_que_ja_estava_no_caso(client, editor):
    await _casos(client)
    r = await client.put("/api/denuncia/casos/6/extra", json={
        "processo_numero": "1000123-45.2026.8.26.0451",
        "processo_link": "https://www.jusbrasil.com.br/processos/123",
        "mov_data": "2026-11-05", "mov_texto": "Distribuído por sorteio",
    })
    assert r.status_code == 200, r.text

    itens = await _lista(client)
    assert len(itens) == 1  # o Caso 012 ainda não foi ao advogado
    p = itens[0]
    assert p["origem"] == "denuncia" and p["caso_codigo"] == "Caso 006" and p["denuncia_caso_id"] == 6
    assert p["pedido"] is None  # na Denúncia a referência é o caso, não o pedido da compra de prova
    assert p["encaminhado_em"] == "2026-10-13"
    assert p["autor"] == "MAKISA Trading" and p["lado"] == "autor"
    assert p["reu"] == "Express Eletrônicos" and p["reu_obs"] == "Shopee"
    assert p["advogado"] == "Dr. Paulo Dias"
    assert p["assunto"] == "Certificado Anatel"
    assert p["numero"] == "1000123-45.2026.8.26.0451" and p["situacao"] == "em_andamento"
    assert p["jusbrasil_link"] == "https://www.jusbrasil.com.br/processos/123"
    assert p["ultima_mov"]["texto"] == "Distribuído por sorteio" and p["ultima_mov"]["data"] == "2026-11-05"

    # abrir de novo não duplica
    assert len(await _lista(client)) == 1


@pytest.mark.asyncio
async def test_o_que_foi_editado_no_painel_nao_volta_ao_do_caso(client, editor):
    await _casos(client)
    pid = (await _lista(client))[0]["id"]
    assert (await _lista(client))[0]["situacao"] == "aguardando_numero"

    r = await client.put(f"/api/juridico/processos/{pid}", json={
        "reu": "EXPRESS COMERCIO LTDA", "numero": "0001111-22.2026.8.26.0100",
        "assunto": "Marca Uranyx", "advogado_escritorio": "Coelho Dias Advogados",
    })
    assert r.status_code == 200, r.text
    # chegou o nº: sai de "Aguardando nº"
    assert r.json()["processo"]["situacao"] == "em_andamento"

    p = (await _lista(client))[0]
    assert p["reu"] == "EXPRESS COMERCIO LTDA" and p["assunto"] == "Marca Uranyx"
    assert p["advogado_escritorio"] == "Coelho Dias Advogados" and p["advogado"] == "Dr. Paulo Dias"

    # o que veio do caso não se apaga pelo painel (voltaria sozinho)
    r = await client.delete(f"/api/juridico/processos/{pid}")
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_novo_processo_a_mao_com_movimentacoes(client, editor):
    r = await client.post("/api/juridico/processos", json={
        "origem": "manual", "lado": "reu", "autor": "Comprador do pedido 301234",
        "reu": "Kia Comércio", "numero": "0012345-67.2026.8.26.0451", "valor_causa": "1.249,90",
        "pedido": "REF-2026-07", "vara": "JEC de Piracicaba", "encaminhado_em": "2026-11-03",
    })
    assert r.status_code == 200, r.text
    p = r.json()["processo"]
    assert p["origem"] == "manual" and p["lado"] == "reu" and p["situacao"] == "em_andamento"
    assert p["valor_causa"] == 1249.90 and p["pedido"] == "REF-2026-07"
    assert "proximo_prazo" not in p
    pid = p["id"]

    for data, texto in (("2026-11-06", "Distribuído"), ("2026-11-07", "Citação recebida")):
        r = await client.post(f"/api/juridico/processos/{pid}/movimentacoes", json={"data": data, "texto": texto})
        assert r.status_code == 200, r.text

    itens = await _lista(client)
    assert [i["id"] for i in itens] == [pid]
    assert itens[0]["ultima_mov"]["texto"] == "Citação recebida"

    d = (await client.get(f"/api/juridico/processos/{pid}")).json()
    assert [m["texto"] for m in d["movimentacoes"]] == ["Citação recebida", "Distribuído"]
    assert d["documentos"] == {"pasta": None, "pasta_url": None, "dossie_url": None}

    # apaga a movimentação errada e depois o processo incluído à mão
    mid = d["movimentacoes"][0]["id"]
    assert (await client.delete(f"/api/juridico/movimentacoes/{mid}")).status_code == 200
    assert (await _lista(client))[0]["ultima_mov"]["texto"] == "Distribuído"
    assert (await client.delete(f"/api/juridico/processos/{pid}")).status_code == 200
    assert await _lista(client) == []


@pytest.mark.asyncio
async def test_chamado_encaminhado_ao_juridico_entra_como_logistica(client, editor, db):
    db.add(Chamado(
        origem="logistica", pedido_bling="300001", canal="Mercado Livre",
        juridico_enviado_at=datetime(2026, 11, 6, 2, 30, tzinfo=UTC),  # 05/11 23:30 em Brasília
        juridico_obs="extravio",
    ))
    await db.commit()
    itens = await _lista(client)
    assert len(itens) == 1
    p = itens[0]
    assert p["origem"] == "logistica" and p["pedido"] == "300001"
    assert p["encaminhado_em"] == "2026-11-05" and p["observacao"] == "extravio"
    assert len(await _lista(client)) == 1
    await db.execute(text("DELETE FROM chamados WHERE pedido_bling = '300001'"))
    await db.commit()


@pytest.mark.asyncio
async def test_validacoes(client, editor):
    r = await client.post("/api/juridico/processos", json={"origem": "denuncia"})
    assert r.status_code == 422
    pid = (await client.post("/api/juridico/processos", json={"reu": "X"})).json()["processo"]["id"]
    for corpo, code in (
        ({"jusbrasil_link": "jusbrasil.com.br/x"}, "juridico_link_invalido"),
        ({"situacao": "ganhamos"}, "juridico_situacao_invalida"),
        ({"lado": "terceiro"}, "juridico_lado_invalido"),
        ({"valor_causa": "mil reais"}, "juridico_valor_invalido"),
        ({"encaminhado_em": "14/11"}, "juridico_data_invalida"),
    ):
        r = await client.put(f"/api/juridico/processos/{pid}", json=corpo)
        assert r.status_code == 422 and r.json()["detail"]["code"] == code, (corpo, r.text)
    r = await client.post(f"/api/juridico/processos/{pid}/movimentacoes", json={"texto": "  "})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_sem_permissao_da_denuncia_nao_ve(client, make_user, auth_as):
    auth_as(await make_user(permissions={"chamados": {"view": True}}))
    assert (await client.get("/api/juridico/processos")).status_code == 403


@pytest.mark.asyncio
async def test_ajuizado_saiu_dos_casos_vira_com_juridico(client, editor):
    """11/11 (Vinicius: "pode tirar esse ajuizado, tem que deixar com jurídico")."""
    await _casos(client)
    await _sync(client, "casos", [
        {"id": 6, "codigo": "Caso 006", "anuncio_id": "A6", "titulo": "Caso 6", "status": "Ajuizado",
         "aberto_em": "2026-09-29", "juridico": "Dr. Paulo Dias", "juridico_enviado_em": "2026-10-13 13:10:00"},
    ])
    casos = {c["codigo"]: c for c in (await client.get("/api/denuncia/casos")).json()["itens"]}
    assert casos["Caso 006"]["status"] == "Com jurídico"
    r = await client.put("/api/denuncia/casos/6/status", json={"status": "Ajuizado"})
    assert r.status_code == 422
