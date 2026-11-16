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
from datetime import UTC, date, datetime, timedelta

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
async def test_alvos_mais_antigo_primeiro_e_pula_o_lido_ha_pouco(
    client: AsyncClient,
    db: AsyncSession,
    admin: User,
    auth_as: Callable[[User | None], None],
):
    """16/11/2026: o Tracken bloqueia o IP depois de 5 consultas seguidas e o
    309220 ficava sempre depois do 5º. A lista vem com quem está há mais tempo
    sem leitura primeiro; a leitura sozinha pula o lido há pouco (outra aba
    acabou de ler); o pedido que a transportadora não tem, já com 5 dias de
    venda, volta só de 6 em 6 h — até no botão "Atualizar Tracken"."""
    agora = datetime.now(UTC)

    async def flex(bling: str, loja: str) -> Logistica:
        return await _pedido(db, bling=bling, loja=loja, rastreio=None, flex=True, cep="01001000")

    def lido(linha: Logistica, antes: timedelta, *, achado: bool, erro: str | None = None):
        return LogisticaTracken(
            logistica_id=linha.id, achado=achado, lido_em=agora - antes, erro=erro, historico=[]
        )

    nunca = await flex("309220", "261116DM18QFVX")
    velho = await flex("309238", "261116DVAQ4G2V")
    recente = await flex("309259", "261116EKJX7W0K")
    com_erro = await flex("309177", "2000016066120677")
    nao_tem = await flex("306194", "2000015725468989")
    nao_tem_novo = await flex("309300", "261116NOVO")
    nao_tem.data = date.today() - timedelta(days=6)
    com_erro.data = date.today() - timedelta(days=6)
    db.add_all(
        [
            lido(velho, timedelta(minutes=50), achado=True),
            lido(recente, timedelta(minutes=5), achado=True),
            # Leitura bloqueada (venda velha, nunca achado): erro gravado — não é
            # "a transportadora não tem", continua no ritmo normal.
            lido(com_erro, timedelta(minutes=40), achado=False, erro="Tracken: Failed to fetch"),
            # Consultado há 1 h e a transportadora não tinha (venda de 6 dias).
            lido(nao_tem, timedelta(hours=1), achado=False),
            # Venda de hoje, ainda não chegou: continua no ritmo normal.
            lido(nao_tem_novo, timedelta(minutes=30), achado=False),
        ]
    )
    await db.commit()

    auth_as(admin)
    r = await client.get("/api/logistica/tracken/alvos")
    assert r.status_code == 200, r.text
    assert [a["logistica_id"] for a in r.json()] == [
        str(nunca.id), str(velho.id), str(com_erro.id), str(nao_tem_novo.id)
    ]
    # Botão "Atualizar Tracken": também o lido há pouco; o que a transportadora não tem, não.
    r = await client.get("/api/logistica/tracken/alvos?tudo=true")
    assert [a["logistica_id"] for a in r.json()] == [
        str(nunca.id), str(velho.id), str(com_erro.id), str(nao_tem_novo.id), str(recente.id)
    ]
    # Raio-X: sempre.
    r = await client.get(f"/api/logistica/tracken/alvos?ids={nao_tem.id}")
    assert [a["logistica_id"] for a in r.json()] == [str(nao_tem.id)]
    # Depois de 6 h ele volta.
    tk = await db.get(LogisticaTracken, nao_tem.id)
    tk.lido_em = agora - timedelta(hours=7)
    await db.commit()
    r = await client.get("/api/logistica/tracken/alvos")
    assert str(nao_tem.id) in [a["logistica_id"] for a in r.json()]


class _RedisFalso:
    """O pedaço do Redis que a vez da leitura usa (SET NX EX, GET, EXPIRE, DEL)."""

    def __init__(self) -> None:
        self.d: dict[str, str] = {}
        self.ttl: dict[str, int] = {}

    async def set(self, k, v, nx=False, ex=None):
        if nx and k in self.d:
            return None
        self.d[k], self.ttl[k] = v, ex
        return True

    async def get(self, k):
        return self.d.get(k)

    async def expire(self, k, s):
        self.ttl[k] = s
        return True

    async def delete(self, k):
        self.ttl.pop(k, None)
        return 1 if self.d.pop(k, None) is not None else 0


@pytest.mark.asyncio
async def test_uma_leitura_por_vez(
    client: AsyncClient,
    db: AsyncSession,
    admin: User,
    auth_as: Callable[[User | None], None],
    monkeypatch,
):
    """Duas abas (na mesma internet) somariam consultas e o Tracken bloquearia
    as duas: com o código da leitura, uma lê e a outra recebe 409."""
    from app import redis_client

    falso = _RedisFalso()
    monkeypatch.setattr(redis_client, "redis", falso)
    linha = await _pedido(
        db, bling="309220", loja="261116DM18QFVX", rastreio=None, flex=True, cep="06673520"
    )
    auth_as(admin)
    url = "/api/logistica/tracken/alvos?leitura="
    assert (await client.get(url + "aba-A")).status_code == 200
    assert (await client.get(url + "aba-A")).status_code == 200  # a mesma aba de novo
    r = await client.get(url + "aba-B")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "tracken_outra_tela"
    # O Raio-X (um pedido) não pede a vez.
    assert (await client.get(f"/api/logistica/tracken/alvos?ids={linha.id}")).status_code == 200
    # Cada pedido gravado renova a vez; só quem tem a vez renova.
    falso.ttl[tracken._CHAVE_VEZ] = 5
    item = {"logistica_id": str(linha.id), "lista": []}
    await client.post(
        "/api/logistica/tracken/resultado", json={"itens": [item], "leitura": "aba-B"}
    )
    assert falso.ttl[tracken._CHAVE_VEZ] == 5
    await client.post(
        "/api/logistica/tracken/resultado", json={"itens": [item], "leitura": "aba-A"}
    )
    assert falso.ttl[tracken._CHAVE_VEZ] == tracken.VEZ_SEGUNDOS
    # Só quem tem a vez solta; depois a outra aba lê.
    await client.post("/api/logistica/tracken/fim", json={"leitura": "aba-B"})
    assert (await client.get(url + "aba-B")).status_code == 409
    await client.post("/api/logistica/tracken/fim", json={"leitura": "aba-A"})
    assert (await client.get(url + "aba-B")).status_code == 200

    # Sem Redis: lê como antes (sem trava não é motivo para não ler).
    class _Quebrado:
        async def set(self, *a, **k):
            raise ConnectionError("sem redis")

    monkeypatch.setattr(redis_client, "redis", _Quebrado())
    assert (await client.get(url + "aba-C")).status_code == 200


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
    assert r.json() == {"gravados": 3, "achados": 1, "avisados": 0}

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
