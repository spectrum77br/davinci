# ruff: noqa: E501
"""Vigia do Jurídico (11/11/2026): as publicações do Diário de Justiça Eletrônico Nacional
(DJEN, CNJ) entram sozinhas no histórico de cada processo com nº.

Vinicius: "vincular esse jusbrasil … deixar ele atualizando os últimos passos sozinho". O teste
de 11/11 com o processo do Caso 006 trouxe 3 publicações (intimação, distribuição, decisão) —
o formato abaixo é o mesmo que o DJEN devolveu."""

import httpx
import pytest
import pytest_asyncio
import respx
from sqlalchemy import text

from app.services import vigia_juridico

NUM = "4206943-64.2026.8.26.0100"
NUM20 = "42069436420268260100"


def _item(id_, data, tipo, doc, orgao, texto, **extra):
    return {
        "id": id_, "data_disponibilizacao": data, "tipoComunicacao": tipo, "tipoDocumento": doc,
        "nomeOrgao": orgao, "texto": texto, "ativo": True, "data_cancelamento": None,
        "link": f"https://eproc1g.tjsp.jus.br/eproc/externo_controlador.php?acao=consulta_publica_documento&numProcesso={NUM20}&idDocumento={id_}",
        "numero_processo": NUM20, **extra,
    }


ITENS = [
    _item(101, "2026-10-23", "Intimação", "Ato ordinatório", "UPJ da 1ª a 3ª Vara Empresarial - Foro Central Cível",
          "<b>Procedimento Comum C&iacute;vel</b></br><p>ATO ORDINATÓRIO</p><p>A parte requerente fica intimada para recolher as custas iniciais.</p>"),
    _item(102, "2026-10-26", "Lista de distribuição", "Outros", "UPJ da 1ª a 3ª Vara Empresarial - Foro Central Cível",
          "Processo 4206943-64.2026.8.26.0100 distribuido na data de 22/10/2026."),
    _item(103, "2026-11-03", "Intimação", "DESPACHO/DECISÃO", "UPJ da 31ª a 35ª Varas Cíveis - Foro Central Cível",
          "<html><head><style>p{}</style></head><body><section><b>DESPACHO/DECISÃO</b></section><p>Vistos.</p><p>INDEFIRO o pedido de tutela de urgência. Cite-se.</p></body></html>"),
    _item(104, "2026-11-04", "Intimação", "Despacho", "UPJ da 31ª a 35ª Varas Cíveis - Foro Central Cível",
          "publicação cancelada", ativo=False, data_cancelamento="2026-11-05"),
]


@pytest_asyncio.fixture(autouse=True)
async def _limpa(db, monkeypatch):
    monkeypatch.setattr(vigia_juridico, "PAUSA_S", 0)
    for t in ("juridico_movimentacoes", "juridico_processos", "denuncia_casos", "denuncia_anuncios"):
        await db.execute(text(f"DELETE FROM {t}"))  # noqa: S608
    await db.commit()
    yield
    for t in ("juridico_movimentacoes", "juridico_processos"):
        await db.execute(text(f"DELETE FROM {t}"))  # noqa: S608
    await db.commit()


@pytest_asyncio.fixture
async def editor(make_user, auth_as):
    u = await make_user(permissions={"denuncia": {"view": True, "edit": True, "delete": True}})
    auth_as(u)
    return u


def _djen(itens):
    return httpx.Response(200, json={"status": "success", "message": "", "count": len(itens), "items": itens})


async def _processo(client, numero=NUM):
    r = await client.post("/api/juridico/processos", json={"origem": "manual", "numero": numero, "reu": "Express"})
    assert r.status_code == 200, r.text
    return r.json()["processo"]["id"]


@pytest.mark.asyncio
async def test_buscar_no_diario_lanca_as_publicacoes_uma_vez(client, editor):
    pid = await _processo(client)
    with respx.mock(assert_all_called=True) as m:
        rota = m.get(vigia_juridico.URL_DJEN).mock(return_value=_djen(ITENS))
        r = await client.post(f"/api/juridico/processos/{pid}/diario")
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True, "novas": 3, "publicacoes": 4}  # a cancelada não entra
        assert rota.calls[0].request.url.params["numeroProcesso"] == NUM20

        d = (await client.get(f"/api/juridico/processos/{pid}")).json()
        movs = d["movimentacoes"]
        assert [x["data"] for x in movs] == ["2026-11-03", "2026-10-26", "2026-10-23"]
        assert movs[0]["texto"] == "Intimação · Despacho/decisão"
        assert movs[0]["fonte"] == "djen" and movs[0]["criado_por"] == "Diário oficial (DJEN)"
        assert "INDEFIRO o pedido de tutela" in movs[0]["texto_completo"]
        assert "<" not in movs[0]["texto_completo"] and "p{}" not in movs[0]["texto_completo"]
        assert movs[0]["link"].startswith("https://eproc1g.tjsp.jus.br/")
        assert movs[1]["texto"] == "Distribuído para UPJ da 1ª a 3ª Vara Empresarial - Foro Central Cível"
        assert movs[2]["texto"] == "Intimação · Ato ordinatório"
        assert "Procedimento Comum Cível" in movs[2]["texto_completo"]
        # a Vara vazia vem do órgão da publicação mais nova
        assert d["vara"] == "UPJ da 31ª a 35ª Varas Cíveis - Foro Central Cível"

        # na tabela, a última movimentação é a do Diário (sem o texto inteiro)
        lista = (await client.get("/api/juridico/processos")).json()["itens"]
        assert lista[0]["ultima_mov"]["texto"] == "Intimação · Despacho/decisão"
        assert "texto_completo" not in lista[0]["ultima_mov"]

        # de novo: nada duplica
        r = await client.post(f"/api/juridico/processos/{pid}/diario")
        assert r.json()["novas"] == 0
    d = (await client.get(f"/api/juridico/processos/{pid}")).json()
    assert len(d["movimentacoes"]) == 3


@pytest.mark.asyncio
async def test_sem_numero_ou_diario_fora_do_ar(client, editor):
    pid = await _processo(client, numero="")
    r = await client.post(f"/api/juridico/processos/{pid}/diario")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "juridico_sem_numero"

    pid2 = await _processo(client)
    with respx.mock() as m:
        m.get(vigia_juridico.URL_DJEN).mock(return_value=httpx.Response(503))
        r = await client.post(f"/api/juridico/processos/{pid2}/diario")
    assert r.status_code == 502 and r.json()["detail"]["code"] == "juridico_djen_indisponivel"


@pytest.mark.asyncio
async def test_rodada_diaria_le_so_os_processos_com_numero(client, editor, db):
    com = await _processo(client)
    await _processo(client, numero="")  # aguardando nº: não consulta
    with respx.mock(assert_all_called=True) as m:
        rota = m.get(vigia_juridico.URL_DJEN).mock(return_value=_djen(ITENS[:2]))
        res = await vigia_juridico.vigia_juridico_run(db)
    assert rota.call_count == 1
    assert res["processos"] == 1 and res["novas"] == 2
    n = (await db.execute(text("SELECT count(*) FROM juridico_movimentacoes WHERE processo_id = :p"), {"p": com})).scalar()
    assert n == 2
    rodada = (await db.execute(text("SELECT ok, resumo FROM ouvidoria_rodadas WHERE robo_chave = 'vigia_juridico' ORDER BY id DESC LIMIT 1"))).one()
    assert rodada.ok is True and "2 publicação(ões) nova(s)" in rodada.resumo
