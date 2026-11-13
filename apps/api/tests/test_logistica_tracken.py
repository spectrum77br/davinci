"""Coluna "Tracken" da aba Flex (Vinicius, 13/11/2026).

O DaVinci lê o rastreio PÚBLICO da transportadora do Flex (TRACKen) — número
do pedido + CEP do cliente, sem login — e mostra na aba Flex. A resposta de
exemplo é a do 308933 de 13/11/2026 (nome e endereço trocados).

Nenhuma chamada externa: um transporte falso do httpx responde no lugar do
Tracken.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, Logistica, LogisticaTracken, User, UserRole, UserStatus
from app.services import flex_config, tracken

_RESPOSTA = {
    "success": True,
    "list": [
        {
            "codpedido": "636803384",
            "ped_codpedido": 636803384,
            "ped_remetente": "M48P1",
            "ped_destinatario": "Cliente Teste",
            "ped_cepdest": "06840-050",
            "ped_notafiscal": "26111383SWQS3R",
            "ped_pedidonf": "BR262058740263A",
            "ped_dtpreventrega": None,
            "hsp_dtentrega": None,
            "hsp_hrentrega": None,
            "hsp_recebedor": None,
            "hsp_observacao": None,
            "sts_nome": "PEDIDO POSTADO",
            "statushsp": "ARQUIVO IMPORTADO",
            "laststatus": "13/11/2026 12:34 - ARQUIVO IMPORTADO",
            "historico": [
                {
                    "tipo": "Histórico",
                    "hsp_dtstatus": "2026-11-13",
                    "hsp_hrstatus": "12:34:03",
                    "hsp_observacao": None,
                    "previsao": "&nbsp;",
                    "sts_nome": "ARQUIVO IMPORTADO",
                    "deschistorico": "ARQUIVO IMPORTADO  ",
                }
            ],
        }
    ],
}

_ENTREGUE = {
    "success": True,
    "list": [
        {
            **_RESPOSTA["list"][0],
            "sts_nome": "ENTREGUE",
            "statushsp": "ENTREGA REALIZADA",
            "hsp_dtentrega": "2026-11-13",
            "hsp_hrentrega": "17:05:00",
            "hsp_recebedor": "PORTEIRO",
            "hsp_observacao": "deixado na portaria",
            "historico": [
                *_RESPOSTA["list"][0]["historico"],
                {
                    "hsp_dtstatus": "2026-11-13",
                    "hsp_hrstatus": "15:10:00",
                    "deschistorico": "DESTINATÁRIO AUSENTE",
                },
                {
                    "hsp_dtstatus": "2026-11-13",
                    "hsp_hrstatus": "17:05:00",
                    "deschistorico": "ENTREGA REALIZADA",
                },
            ],
        }
    ],
}


def _cliente(respostas: dict[str, dict], chamadas: list[dict]) -> httpx.AsyncClient:
    """Tracken falso: responde pela chave `notafiscal`/`pedidonf` + CEP."""

    def _responder(request: httpx.Request) -> httpx.Response:
        corpo = json.loads(request.content)
        chamadas.append(corpo)
        if not corpo.get("cepdest"):
            return httpx.Response(
                200, json={"success": False, "error": "Cep de entrega deve ser preenchido"}
            )
        chave = corpo.get("notafiscal") or corpo.get("pedidonf")
        cep = corpo["cepdest"].replace("-", "")
        r = respostas.get(f"{chave}|{cep}")
        if r is None:
            return httpx.Response(
                200, json={"success": False, "error": "Nenhum registro encontrado"}
            )
        return httpx.Response(200, json=r)

    return httpx.AsyncClient(transport=httpx.MockTransport(_responder))


def test_interpretar_a_resposta_real():
    d = tracken.interpretar(_RESPOSTA["list"][0])
    assert d["remessa"] == "636803384"
    assert d["status"] == "PEDIDO POSTADO"
    assert d["ultimo_evento"] == "ARQUIVO IMPORTADO"
    # 12:34:03 em Brasília = 15:34:03 UTC.
    assert d["ultimo_em"] == datetime(2026, 11, 13, 15, 34, 3, tzinfo=UTC)
    assert d["entregue_em"] is None and d["previsao"] is None
    assert d["tentativas"] == 0
    assert d["historico"] == [
        {"em": "2026-11-13T15:34:03+00:00", "descricao": "ARQUIVO IMPORTADO", "observacao": None}
    ]


def test_interpretar_entregue_com_tentativa():
    d = tracken.interpretar(_ENTREGUE["list"][0])
    assert d["status"] == "ENTREGUE"
    assert d["entregue_em"] == datetime(2026, 11, 13, 20, 5, tzinfo=UTC)
    assert (d["recebedor"], d["observacao"]) == ("PORTEIRO", "deixado na portaria")
    assert d["tentativas"] == 1  # "DESTINATÁRIO AUSENTE"
    assert [e["descricao"] for e in d["historico"]] == [
        "ARQUIVO IMPORTADO",
        "DESTINATÁRIO AUSENTE",
        "ENTREGA REALIZADA",
    ]
    assert tracken.eh_problema("DESTINATÁRIO AUSENTE")
    assert not tracken.eh_problema("PEDIDO POSTADO")


async def _pedido(
    db: AsyncSession,
    *,
    bling: str,
    loja: str,
    rastreio: str | None,
    flex: bool | None,
    cep: str | None,
    plataforma: str = "Shopee",
) -> Logistica:
    linha = Logistica(
        plataforma=plataforma,
        pedido_bling=bling,
        pedido_marketplace=loja,
        rastreio=rastreio,
        envio_flex=flex,
        data=date.today(),
        meli_status={"order_status": "PROCESSED"},
        status_bling="Em digitação",
    )
    db.add(linha)
    if cep is not None:
        db.add(BlingOrder(numero=bling, numeroloja=loja, cep_destino=cep, item_index=0))
        db.add(BlingOrder(numero=bling, numeroloja=loja, cep_destino=cep, item_index=1))
    await db.commit()
    await db.refresh(linha)
    return linha


@pytest.mark.asyncio
async def test_rodada_le_so_flex_e_grava(db: AsyncSession):
    flex = await _pedido(
        db, bling="308933", loja="26111383SWQS3R", rastreio="BR262058740263A",
        flex=True, cep="06840050",
    )
    # Achado só pelo rastreio (o número da venda não bate).
    pelo_rastreio = await _pedido(
        db, bling="308934", loja="XYZ", rastreio="BR1", flex=True, cep="01001-000"
    )
    ainda_nao = await _pedido(
        db, bling="308935", loja="2611NAOCHEGOU", rastreio=None, flex=True, cep="01001000"
    )
    sem_cep = await _pedido(
        db, bling="308936", loja="2611SEMCEP", rastreio=None, flex=True, cep=None
    )
    comum = await _pedido(
        db, bling="308937", loja="2611COMUM", rastreio=None, flex=False, cep="01001000"
    )
    chamadas: list[dict] = []
    cli = _cliente(
        {"26111383SWQS3R|06840050": _RESPOSTA, "BR1|01001000": _ENTREGUE}, chamadas
    )
    resumo = await tracken.rodada(cliente=cli)
    assert resumo == {"alvos": 4, "achados": 2, "nao_achados": 1, "sem_cep": 1, "erros": 0}
    # O pedido comum nunca é consultado.
    assert all(c.get("notafiscal") != "2611COMUM" for c in chamadas)
    # O CEP vai com traço.
    assert {c["cepdest"] for c in chamadas} == {"06840-050", "01001-000"}

    t = await db.get(LogisticaTracken, flex.id, populate_existing=True)
    assert (t.achado, t.status, t.remessa, t.erro) == (True, "PEDIDO POSTADO", "636803384", None)
    t = await db.get(LogisticaTracken, pelo_rastreio.id, populate_existing=True)
    assert (t.achado, t.status, t.tentativas) == (True, "ENTREGUE", 1)
    t = await db.get(LogisticaTracken, ainda_nao.id, populate_existing=True)
    assert (t.achado, t.erro) == (False, None)
    t = await db.get(LogisticaTracken, sem_cep.id, populate_existing=True)
    assert (t.achado, t.erro) == (False, "sem o CEP do cliente no Bling")
    assert await db.get(LogisticaTracken, comum.id, populate_existing=True) is None


@pytest.mark.asyncio
async def test_entregue_ha_mais_de_um_dia_para_de_ler(db: AsyncSession):
    linha = await _pedido(
        db, bling="308940", loja="2611ENTREGUE", rastreio=None, flex=True, cep="01001000"
    )
    db.add(
        LogisticaTracken(
            logistica_id=linha.id,
            achado=True,
            status="ENTREGUE",
            entregue_em=datetime(2026, 1, 1, tzinfo=UTC),
            historico=[],
        )
    )
    await db.commit()
    chamadas: list[dict] = []
    resumo = await tracken.rodada(cliente=_cliente({}, chamadas))
    assert resumo["alvos"] == 0 and chamadas == []
    # O botão do Raio-X lê mesmo assim.
    resumo = await tracken.rodada(somente={linha.id}, cliente=_cliente({}, chamadas))
    assert resumo["alvos"] == 1


@pytest.mark.asyncio
async def test_erro_do_tracken_nao_para_os_outros(db: AsyncSession):
    a = await _pedido(db, bling="308950", loja="2611A", rastreio=None, flex=True, cep="01001000")
    b = await _pedido(db, bling="308951", loja="2611B", rastreio=None, flex=True, cep="01001000")

    def _responder(request: httpx.Request) -> httpx.Response:
        corpo = json.loads(request.content)
        if corpo.get("notafiscal") == "2611A":
            return httpx.Response(500, text="erro")
        return httpx.Response(200, json=_RESPOSTA)

    cli = httpx.AsyncClient(transport=httpx.MockTransport(_responder))
    resumo = await tracken.rodada(cliente=cli)
    assert (resumo["erros"], resumo["achados"]) == (1, 1)
    ta = await db.get(LogisticaTracken, a.id, populate_existing=True)
    assert ta.erro.startswith("Tracken:")
    assert (await db.get(LogisticaTracken, b.id, populate_existing=True)).achado is True


@pytest.fixture
async def admin(db: AsyncSession) -> User:
    email = f"adm-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(open_id=f"email:{email}", email=email, role=UserRole.ADMIN, status=UserStatus.ACTIVE)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest.mark.asyncio
async def test_aba_flex_mostra_o_tracken_so_para_quem_ve_o_flex(
    client: AsyncClient,
    db: AsyncSession,
    admin: User,
    auth_as: Callable[[User | None], None],
    monkeypatch,
):
    linha = await _pedido(
        db, bling="308933", loja="26111383SWQS3R", rastreio="BR262058740263A",
        flex=True, cep="06840050",
    )
    await tracken.rodada(cliente=_cliente({"26111383SWQS3R|06840050": _ENTREGUE}, []))

    auth_as(admin)
    lista = (await client.get("/api/logistica?envio=flex")).json()
    t = next(x for x in lista if x["id"] == str(linha.id))["tracken"]
    assert (t["achado"], t["status"], t["tentativas"], t["problema"]) == (True, "ENTREGUE", 1, True)
    assert t["recebedor"] == "PORTEIRO"
    assert [e["descricao"] for e in t["historico"]][-1] == "ENTREGA REALIZADA"

    # Quem não vê o Flex: a linha (na aba Shopee) vem sem o Tracken.
    monkeypatch.setattr(flex_config, "pode_ver", lambda user: False)
    lista = (await client.get("/api/logistica?plataforma=shopee")).json()
    assert next(x for x in lista if x["id"] == str(linha.id))["tracken"] is None


@pytest.mark.asyncio
async def test_botao_atualizar_tracken(
    client: AsyncClient,
    db: AsyncSession,
    admin: User,
    auth_as: Callable[[User | None], None],
    monkeypatch,
):
    linha = await _pedido(
        db, bling="308933", loja="26111383SWQS3R", rastreio="BR262058740263A",
        flex=True, cep="06840050",
    )
    comum = await _pedido(
        db, bling="308937", loja="2611COMUM", rastreio=None, flex=False, cep="01001000"
    )
    original = tracken.rodada

    async def _rodada(**kw):
        return await original(
            cliente=_cliente({"26111383SWQS3R|06840050": _RESPOSTA}, []), **kw
        )

    monkeypatch.setattr(tracken, "rodada", _rodada)
    auth_as(admin)
    r = await client.post(f"/api/logistica/{linha.id}/tracken")
    assert r.status_code == 200, r.text
    assert r.json()["tracken"]["status"] == "PEDIDO POSTADO"
    r = await client.post(f"/api/logistica/{comum.id}/tracken")
    assert r.status_code == 422
    assert (await db.execute(select(LogisticaTracken))).scalars().all()
