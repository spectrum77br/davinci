"""Coluna "Tracken" da aba Flex (Vinicius, 13/11/2026).

O DaVinci lê o rastreio PÚBLICO da transportadora do Flex (TRACKen) — número
do pedido + CEP do cliente, sem login — e mostra na aba Flex. A resposta de
exemplo é a do 308933 de 13/11/2026 (nome e endereço trocados).

O Tracken responde 403 ao servidor (fora do Brasil): quem consulta é a tela da
aba Flex — o DaVinci dá a lista (`/tracken/alvos`) e grava a resposta crua que a
tela devolve (`/tracken/resultado`). Nenhuma chamada externa aqui.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime

import pytest
from httpx import AsyncClient
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


@pytest.fixture
async def admin(db: AsyncSession) -> User:
    email = f"adm-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(open_id=f"email:{email}", email=email, role=UserRole.ADMIN, status=UserStatus.ACTIVE)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest.mark.asyncio
async def test_alvos_so_flex_com_cep_e_para_depois_de_entregue(
    client: AsyncClient,
    db: AsyncSession,
    admin: User,
    auth_as: Callable[[User | None], None],
    monkeypatch,
):
    flex = await _pedido(
        db, bling="308933", loja="26111383SWQS3R", rastreio="BR262058740263A",
        flex=True, cep="06840050",
    )
    sem_cep = await _pedido(
        db, bling="308936", loja="2611SEMCEP", rastreio=None, flex=True, cep=None
    )
    comum = await _pedido(
        db, bling="308937", loja="2611COMUM", rastreio=None, flex=False, cep="01001000"
    )
    entregue = await _pedido(
        db, bling="308940", loja="2611ENTREGUE", rastreio=None, flex=True, cep="01001000"
    )
    db.add(
        LogisticaTracken(
            logistica_id=entregue.id,
            achado=True,
            status="ENTREGUE",
            entregue_em=datetime(2026, 1, 1, tzinfo=UTC),
            historico=[],
        )
    )
    await db.commit()

    auth_as(admin)
    r = await client.get("/api/logistica/tracken/alvos")
    assert r.status_code == 200, r.text
    por_id = {a["logistica_id"]: a for a in r.json()}
    assert set(por_id) == {str(flex.id), str(sem_cep.id)}
    assert por_id[str(flex.id)] == {
        "logistica_id": str(flex.id),
        "pedido": "26111383SWQS3R",
        "rastreio": "BR262058740263A",
        "cep": "06840-050",
    }
    assert por_id[str(sem_cep.id)]["cep"] is None
    # O botão do Raio-X pede um só — mesmo entregue há dias.
    r = await client.get(f"/api/logistica/tracken/alvos?ids={entregue.id}")
    assert [a["logistica_id"] for a in r.json()] == [str(entregue.id)]
    # Pedido comum nunca é alvo, nem pedido por id.
    r = await client.get(f"/api/logistica/tracken/alvos?ids={comum.id}")
    assert r.json() == []
    # Quem não vê o Flex: 404.
    monkeypatch.setattr(flex_config, "pode_ver", lambda user: False)
    assert (await client.get("/api/logistica/tracken/alvos")).status_code == 404


@pytest.mark.asyncio
async def test_resultado_grava_e_a_aba_flex_mostra(
    client: AsyncClient,
    db: AsyncSession,
    admin: User,
    auth_as: Callable[[User | None], None],
    monkeypatch,
):
    achado = await _pedido(
        db, bling="308933", loja="26111383SWQS3R", rastreio="BR262058740263A",
        flex=True, cep="06840050",
    )
    ainda_nao = await _pedido(
        db, bling="308935", loja="2611NAOCHEGOU", rastreio=None, flex=True, cep="01001000"
    )
    com_erro = await _pedido(
        db, bling="308936", loja="2611ERRO", rastreio=None, flex=True, cep="01001000"
    )
    comum = await _pedido(
        db, bling="308937", loja="2611COMUM", rastreio=None, flex=False, cep="01001000"
    )
    auth_as(admin)
    r = await client.post(
        "/api/logistica/tracken/resultado",
        json={
            "itens": [
                {"logistica_id": str(achado.id), "lista": _ENTREGUE["list"]},
                {"logistica_id": str(ainda_nao.id), "lista": []},
                {"logistica_id": str(com_erro.id), "erro": "o Tracken respondeu 500"},
                # Pedido que não é Flex: ignorado.
                {"logistica_id": str(comum.id), "lista": _RESPOSTA["list"]},
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"gravados": 3, "achados": 1}

    t = await db.get(LogisticaTracken, achado.id, populate_existing=True)
    assert (t.achado, t.status, t.tentativas, t.recebedor) == (True, "ENTREGUE", 1, "PORTEIRO")
    t = await db.get(LogisticaTracken, ainda_nao.id, populate_existing=True)
    assert (t.achado, t.erro) == (False, None)
    t = await db.get(LogisticaTracken, com_erro.id, populate_existing=True)
    assert (t.achado, t.erro) == (False, "Tracken: o Tracken respondeu 500")
    assert await db.get(LogisticaTracken, comum.id) is None

    lista = (await client.get("/api/logistica?envio=flex")).json()
    tk = next(x for x in lista if x["id"] == str(achado.id))["tracken"]
    assert (tk["achado"], tk["status"], tk["problema"]) == (True, "ENTREGUE", True)
    assert [e["descricao"] for e in tk["historico"]][-1] == "ENTREGA REALIZADA"

    # Achado antes e "não achou" agora: o que se sabia fica.
    await client.post(
        "/api/logistica/tracken/resultado",
        json={"itens": [{"logistica_id": str(achado.id), "lista": []}]},
    )
    t = await db.get(LogisticaTracken, achado.id, populate_existing=True)
    assert (t.achado, t.status) == (True, "ENTREGUE")
    assert t.erro == "o Tracken não achou o pedido nesta leitura"

    # Quem não vê o Flex: a linha (na aba Shopee) vem sem o Tracken e não grava.
    monkeypatch.setattr(flex_config, "pode_ver", lambda user: False)
    lista = (await client.get("/api/logistica?plataforma=shopee")).json()
    assert next(x for x in lista if x["id"] == str(achado.id))["tracken"] is None
    r = await client.post("/api/logistica/tracken/resultado", json={"itens": []})
    assert r.status_code == 404
