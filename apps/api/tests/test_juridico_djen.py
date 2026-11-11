# ruff: noqa: E501
"""Vigia do Jurídico (11/11/2026): as publicações do Diário de Justiça Eletrônico Nacional
(DJEN, CNJ) entram no histórico de cada processo com nº.

Vinicius: "vincular esse jusbrasil … deixar ele atualizando os últimos passos sozinho". O DJEN
dá 403 ao servidor (fora do Brasil), então quem consulta é o navegador (botão "Buscar no
Diário") e o Mac mini da Makisa (todo dia às 07:30, com o token da cópia da Denúncia); o
DaVinci só recebe e lança. O formato abaixo é o mesmo que o DJEN devolveu para o Caso 006."""

import hashlib
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.models import DenunciaRemetente
from app.services import juridico_diario

TOKEN = "dnc_juridico_djen_" + uuid.uuid4().hex
H = {"Authorization": f"Bearer {TOKEN}"}

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
async def _limpa(db):
    for t in ("juridico_pedidos", "juridico_leituras", "juridico_movimentacoes", "juridico_processos", "denuncia_casos", "denuncia_anuncios", "denuncia_remetentes"):
        await db.execute(text(f"DELETE FROM {t}"))  # noqa: S608
    db.add(DenunciaRemetente(nome="mini juridico djen", token_hash=hashlib.sha256(TOKEN.encode()).hexdigest()))
    await db.commit()
    yield
    for t in ("juridico_pedidos", "juridico_leituras", "juridico_movimentacoes", "juridico_processos", "denuncia_remetentes"):
        await db.execute(text(f"DELETE FROM {t}"))  # noqa: S608
    await db.commit()


@pytest_asyncio.fixture
async def editor(make_user, auth_as):
    u = await make_user(permissions={"denuncia": {"view": True, "edit": True, "delete": True}})
    auth_as(u)
    return u


async def _processo(client, numero=NUM):
    r = await client.post("/api/juridico/processos", json={"origem": "manual", "numero": numero, "reu": "Express"})
    assert r.status_code == 200, r.text
    return r.json()["processo"]["id"]


@pytest.mark.asyncio
async def test_buscar_no_diario_pelo_navegador_lanca_uma_vez(client, editor):
    pid = await _processo(client)
    r = await client.post(f"/api/juridico/processos/{pid}/diario", json={"itens": ITENS})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "novas": 3, "publicacoes": 4}  # a cancelada não entra

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
    r = await client.post(f"/api/juridico/processos/{pid}/diario", json={"itens": ITENS})
    assert r.json()["novas"] == 0
    assert len((await client.get(f"/api/juridico/processos/{pid}")).json()["movimentacoes"]) == 3

    r = await client.post(f"/api/juridico/processos/{pid}/diario", json={"itens": "x"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "juridico_itens_invalidos"

    # o "Buscar no Diário" do topo registra a leitura de quem clicou
    r = await client.post("/api/juridico/leituras", json={"processos": 1, "publicacoes": 4, "novas": 3})
    assert r.status_code == 200
    diario = (await client.get("/api/juridico/processos")).json()["diario"]
    assert diario["navegador"]["novas"] == 3 and diario["navegador"]["por"] == (editor.name or editor.email)


@pytest.mark.asyncio
async def test_mac_mini_le_a_lista_manda_as_publicacoes_e_a_leitura(client, editor):
    com = await _processo(client)
    await _processo(client, numero="")  # aguardando nº: fora da lista

    assert (await client.get("/api/juridico/sync/processos")).status_code == 401
    r = await client.get("/api/juridico/sync/processos", headers=H)
    assert r.status_code == 200, r.text
    assert r.json() == {"processos": [{"id": com, "numero": NUM20, "jusbrasil_link": None, "tem_jusbrasil": False}]}

    r = await client.post(f"/api/juridico/sync/processos/{com}/diario", json={"itens": ITENS[:2]}, headers=H)
    assert r.status_code == 200 and r.json()["novas"] == 2

    r = await client.post("/api/juridico/sync/leitura", json={"processos": 1, "publicacoes": 2, "novas": 2, "falhas": 0}, headers=H)
    assert r.status_code == 200
    diario = (await client.get("/api/juridico/processos")).json()["diario"]
    assert diario["mac_mini"]["novas"] == 2 and diario["mac_mini"]["processos"] == 1
    assert diario["mac_mini"]["por"] == "mini juridico djen" and diario["navegador"] is None

    # leitura com erro (DJEN fora do ar no mini): o topo do painel mostra
    await client.post("/api/juridico/sync/leitura", json={"processos": 0, "falhas": 1, "erro": "DJEN 503"}, headers=H)
    diario = (await client.get("/api/juridico/processos")).json()["diario"]
    assert diario["mac_mini"]["erro"] == "DJEN 503" and diario["mac_mini"]["falhas"] == 1
    assert (await client.post("/api/juridico/sync/leitura", json={})).status_code == 401


LINK_JB = "https://www.jusbrasil.com.br/processos/968893301/processo-n-4206943-6420268260100-do-tjsp"
ANDAMENTOS = [
    {"data": "08/11/2026", "tipo": "Andamento", "texto": "Confirmada a intimação eletrônica - Refer. aos Eventos: 17 e 19 - Ciência Tácita"},
    {"data": "03/11/2026", "tipo": "Andamento", "texto": "PROCURAÇÃO - SHPS TECNOLOGIA E SERVICOS LTDA."},
    {"data": "03/11/2026", "tipo": "Andamento", "texto": "Disponibilizado no DJ Eletrônico - no dia 03/11/2026 - Refer. ao Evento: 18"},
]


@pytest.mark.asyncio
async def test_jusbrasil_pelo_mini_lanca_os_andamentos_e_vincula_o_link(client, editor):
    pid = await _processo(client)
    r = await client.post(f"/api/juridico/sync/processos/{pid}/jusbrasil", json={"link": LINK_JB, "andamentos": ANDAMENTOS}, headers=H)
    assert r.status_code == 200 and r.json()["novas"] == 3
    # de novo (o robô relê a página todo dia): nada duplica
    r = await client.post(f"/api/juridico/sync/processos/{pid}/jusbrasil", json={"link": LINK_JB, "andamentos": ANDAMENTOS[:1]}, headers=H)
    assert r.json()["novas"] == 0

    d = (await client.get(f"/api/juridico/processos/{pid}")).json()
    assert d["jusbrasil_link"] == LINK_JB  # a coluna Jusbrasil passa a "vinculado"
    movs = d["movimentacoes"]
    assert movs[0]["texto"].startswith("Confirmada a intimação") and movs[0]["fonte"] == "jusbrasil"
    assert {m["data"] for m in movs} == {"2026-11-08", "2026-11-03"}
    r = await client.get("/api/juridico/sync/processos", headers=H)
    assert r.json()["processos"][0]["tem_jusbrasil"] is True

    await client.post("/api/juridico/sync/leitura", json={"origem": "jusbrasil", "processos": 1, "novas": 3}, headers=H)
    diario = (await client.get("/api/juridico/processos")).json()["diario"]
    assert diario["jusbrasil"]["novas"] == 3 and diario["mac_mini"] is None


@pytest.mark.asyncio
async def test_pedido_das_6h_e_do_botao_ao_mac_mini(client, editor, db):
    await _processo(client)
    ped, motivo = await juridico_diario.pedir_leitura(db, "cron")
    await db.commit()
    assert motivo == "pedido" and ped.jusbrasil is True

    # o botão com um pedido em andamento devolve o mesmo
    r = await client.post("/api/juridico/atualizar")
    assert r.status_code == 200 and r.json()["motivo"] == "em_andamento" and r.json()["pedido"]["id"] == ped.id

    # o vigia do mini pega uma vez só
    r = await client.get("/api/juridico/sync/pedido", headers=H)
    assert r.json()["pedido"]["id"] == ped.id and r.json()["pedido"]["pego_em"]
    assert (await client.get("/api/juridico/sync/pedido", headers=H)).json() == {"pedido": None}

    await client.post(f"/api/juridico/sync/pedido/{ped.id}", json={"progresso": "Jusbrasil 1/1"}, headers=H)
    lista = (await client.get("/api/juridico/processos")).json()
    assert lista["pedido"]["progresso"] == "Jusbrasil 1/1" and lista["pedido"]["terminado_em"] is None
    await client.post(f"/api/juridico/sync/pedido/{ped.id}", json={"terminado": True, "ok": True, "resultado": "3 novas"}, headers=H)
    lista = (await client.get("/api/juridico/processos")).json()
    assert lista["pedido"]["terminado_em"] and lista["pedido"]["ok"] is True

    # botão logo depois: o Jusbrasil foi pedido há menos de 2 h → não pede de novo
    r = await client.post("/api/juridico/atualizar")
    assert r.json() == {"pedido": None, "motivo": "jusbrasil_lido_ha_pouco"}
    # 3 h depois, pede
    from datetime import UTC, datetime, timedelta
    ped2, motivo2 = await juridico_diario.pedir_leitura(db, "botao", "Teste", agora=datetime.now(UTC) + timedelta(hours=3))
    assert motivo2 == "pedido" and ped2.origem == "botao" and ped2.pedido_por == "Teste"

