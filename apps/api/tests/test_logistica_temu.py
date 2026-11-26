"""Temu na Logística pela Open API (26/11/2026, piloto da Barbosa).

Caso real: 306833 (Barbosa) — Anjun coletou em 03/11, o rastreio parou em
07/11 ("chegou ao ponto de entrega" em Rio Negro/SC), prazo de entrega 17/11,
a Temu reembolsou a cliente em 18/11. Os payloads abaixo são os da sonda.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingOrder,
    Company,
    Integration,
    IntegrationPlatform,
    Logistica,
    Marketplace,
    SituacaoBling,
    Store,
    StoreInfo,
    StoreStatus,
    User,
    UserRole,
    UserStatus,
)
from app.security.cipher import encrypt_json
from app.services import (
    logistica_amazon_avisos,
    logistica_bling,
    logistica_ingest,
    logistica_rules,
    logistica_temu,
    temu_rastreio,
)
from app.services import marketplace_shipment_check as msc
from app.services.marketplaces.temu import CLASSE_RETRYABLE, TemuClient, TemuErro

PO = "PO-029-11708709484150003"
PACOTE = "PK-0539624944435992333"

# 03/11 10:39:03 BRT = 13:39:03 UTC, etc.
T_COLETA = 1793713143
T_PRONTO = 1793713261
T_SP1 = 1793732063
T_SP2 = 1793751355
T_SC = 1794038397  # 07/11 04:59:57 BRT

DETALHE = {
    "parentOrderMap": {
        "parentOrderSn": PO,
        "parentOrderStatus": 4,
        "parentOrderTime": 1793472011,
        "parentShippingTime": 1793668996,
        "expectShipLatestTime": 1793849399,  # 05/11 00:29:59 BRT
        "latestDeliveryTime": 1794970799,  # 17/11 23:59:59 BRT
        "regionName3": "Diamantina",
    },
    "orderList": [
        {
            "orderSn": "029-11708751427190003",
            "orderStatus": 4,
            "packageSnInfo": [{"callSuccess": True, "packageSn": PACOTE}],
        }
    ],
}
RASTREIO = {
    "trackingNum": "AJ261102157415301",
    "packageSn": PACOTE,
    "trackingInfo": [
        {
            "statusText": "Objeto coletado",
            "logisticsUpdatedAt": T_COLETA,
            "logisticsStatus": "Last Mile Carrier Picked up",
        },
        {
            "statusText": "Objeto está pronto para ser transportado",
            "logisticsUpdatedAt": T_PRONTO,
            "logisticsStatus": "In transit",
        },
        {
            "statusText": "O pacote está a caminho do centro de trânsito",
            "logisticsUpdatedAt": T_SP1,
            "logisticsStatus": "In transit",
        },
        {
            "statusText": "O pacote está a caminho do centro de trânsito",
            "logisticsUpdatedAt": T_SP2,
            "logisticsStatus": "In transit",
        },
        {
            "statusText": "Objeto chegou ao ponto de entrega",
            "logisticsUpdatedAt": T_SC,
            "logisticsStatus": "Arrived at Post Office",
        },
    ],
}


def _utc(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, tz=UTC)


class FakeTemu:
    """Responde as duas consultas com os payloads da sonda."""

    def __init__(self, detalhe=None, rastreio=None):
        self.detalhe = DETALHE if detalhe is None else detalhe
        self.rastreio = RASTREIO if rastreio is None else rastreio
        self.chamadas: list[tuple[str, dict]] = []

    async def _chamar(self, api, params=None, **_kw):
        self.chamadas.append((api, dict(params or {})))
        if api == logistica_temu.API_PEDIDO:
            return {"success": True, "result": self.detalhe}
        if api == logistica_temu.API_RASTREIO:
            return {"success": True, "result": self.rastreio}
        raise AssertionError(f"API inesperada: {api}")


# ---- regras (puro) ----------------------------------------------------------


def test_assinatura_e_detalhe_da_temu():
    m = {"order_status": "SHIPPED", "logistics_status": "ARRIVED_AT_POST_OFFICE"}
    assert logistica_rules.assinatura_para("Temu", m) == "Enviado | Chegou ao ponto de entrega"
    assert [linha["rotulo"] for linha in logistica_rules.detalhe_para("Temu", m)] == [
        "Status do pedido",
        "Status do envio",
    ]
    # Status desconhecido aparece como veio (nunca some da assinatura).
    assert logistica_rules.assinatura_temu({"logistics_status": "WEIRD"}) == "WEIRD"
    assert logistica_rules.temu_status_logistico("Last Mile Carrier Picked up") == (
        "LAST_MILE_CARRIER_PICKED_UP"
    )


def test_ida_e_saida_da_temu():
    ida = logistica_rules.situacao_da_ida
    assert ida("Temu", {"order_status": "SHIPPED"}) is None  # só a etiqueta
    assert ida("Temu", {"order_status": "SHIPPED", "logistics_status": "IN_TRANSIT"}) == "a_caminho"
    assert ida("Temu", {"logistics_status": "DELIVERED"}) == "entregue"
    assert ida("Temu", {"order_status": "RECEIPTED"}) == "entregue"
    assert ida("Temu", {"logistics_status": "LOST"}) == "falhou"
    assert not logistica_rules.temu_saiu({"order_status": "SHIPPED"})
    assert not logistica_rules.temu_saiu({"logistics_status": "MANIFEST"})
    assert logistica_rules.temu_saiu({"logistics_status": "LAST_MILE_CARRIER_PICKED_UP"})
    # Lista POSITIVA: status desconhecido não é coleta.
    assert not logistica_rules.temu_saiu({"logistics_status": "SHIPPING_LABEL_CREATED"})
    assert ida("Temu", {"logistics_status": "AWAITING_PICKUP"}) is None


def test_pacote_ainda_com_o_vendedor_na_temu():
    com_vendedor = logistica_bling.pacote_ainda_com_o_vendedor
    assert com_vendedor("Temu", {"order_status": "SHIPPED"})  # etiqueta comprada, não saiu
    assert not com_vendedor("Temu", {"order_status": "SHIPPED", "logistics_status": "IN_TRANSIT"})
    assert not com_vendedor("Temu", {})  # sem leitura: não barra (como as outras)
    assert com_vendedor("Temu", {"order_status": "SHIPPED", "logistics_status": "AWAITING_PICKUP"})


def test_montar_le_o_306833():
    enr = logistica_temu.montar(DETALHE, RASTREIO)
    assert enr["meli_status"] == {
        "order_status": "SHIPPED",
        "logistics_status": "ARRIVED_AT_POST_OFFICE",
    }
    assert enr["rastreio"] == "AJ261102157415301"
    assert enr["localizacao"] == "Objeto chegou ao ponto de entrega"
    assert enr["localizacao_at"] == _utc(T_SC)
    assert enr["prazo_entrega"] == date(2026, 11, 17)
    assert enr["entregue_em"] is None
    assert enr["datas"]["logistics_status"]["fonte"] == "plataforma"


def test_montar_sem_rastreio_e_cancelado():
    detalhe = {**DETALHE, "parentOrderMap": {**DETALHE["parentOrderMap"], "parentOrderStatus": 3}}
    enr = logistica_temu.montar(detalhe, None)
    assert enr["meli_status"] == {"order_status": "CANCELED"}
    assert enr["rastreio"] is None and enr["localizacao"] is None


def test_montar_entregue_carimba_a_hora_do_evento():
    rastreio = {
        **RASTREIO,
        "trackingInfo": [
            *RASTREIO["trackingInfo"],
            {
                "statusText": "Objeto entregue",
                "logisticsUpdatedAt": T_SC + 3600,
                "logisticsStatus": "Delivered",
            },
        ],
    }
    enr = logistica_temu.montar(DETALHE, rastreio)
    assert enr["meli_status"]["logistics_status"] == "DELIVERED"
    assert enr["entregue_em"] == _utc(T_SC + 3600)


# ---- banco -------------------------------------------------------------------


@pytest_asyncio.fixture
async def dono(db: AsyncSession) -> User:
    email = f"adm-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(open_id=f"email:{email}", email=email, role=UserRole.ADMIN, status=UserStatus.ACTIVE)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _integracao(db: AsyncSession, dono: User, nome: str = "Barbosa", **kw) -> Integration:
    integ = Integration(
        user_id=dono.id,
        platform=IntegrationPlatform.TEMU,
        name=nome,
        status=kw.pop("status", "active"),
        credentials=kw.pop(
            "credentials", encrypt_json({"app_key": "k", "app_secret": "s", "access_token": "t"})
        ),
        **kw,
    )
    db.add(integ)
    await db.commit()
    await db.refresh(integ)
    return integ


def _linha(**kw) -> Logistica:
    base = {
        "data": date(2026, 10, 31),
        "pedido_bling": "306833",
        "pedido_marketplace": PO,
        "plataforma": "Temu",
        "conta": "barbosa",
        "meli_status": {},
        "status_bling": "Em andamento",
    }
    base.update(kw)
    return Logistica(**base)


@pytest.mark.asyncio
async def test_enrich_recent_grava_a_linha(db: AsyncSession, dono, monkeypatch):
    await _integracao(db, dono)
    db.add(_linha())
    await db.commit()
    fake = FakeTemu()
    monkeypatch.setattr(logistica_temu, "_build_temu_client", lambda _s, _i: fake)

    out = await logistica_temu.enrich_recent(db, only_empty=True)

    assert out["updated"] == 1 and out["failed"] == 0
    row = (await db.execute(select(Logistica))).scalars().one()
    assert logistica_rules.assinatura_para(row.plataforma, row.meli_status) == (
        "Enviado | Chegou ao ponto de entrega"
    )
    assert row.rastreio == "AJ261102157415301"
    assert row.localizacao == "Objeto chegou ao ponto de entrega"
    assert row.prazo_entrega_amazon == date(2026, 11, 17)
    assert row.status_datas["logistics_status"]["em"].startswith("2026-11-07T07:59:57")
    assert [a for a, _ in fake.chamadas] == [logistica_temu.API_PEDIDO, logistica_temu.API_RASTREIO]
    assert fake.chamadas[1][1] == {"packageSn": PACOTE}


@pytest.mark.asyncio
async def test_motor_nao_rele_antes_de_30_min_e_conta_sem_api_fica_de_fora(
    db: AsyncSession, dono, monkeypatch
):
    await _integracao(db, dono)
    # Âncora da Conciliação (arquivada) não é API.
    await _integracao(
        db,
        dono,
        nome="eron",
        status="ancora",
        credentials=b"",
        archived_at=datetime(2026, 11, 17, tzinfo=UTC),
    )
    agora = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
    lida = _linha(
        pedido_bling="306834",
        pedido_marketplace="PO-029-1",
        status_lido_em=agora - timedelta(minutes=10),
        meli_status={"order_status": "SHIPPED"},
    )
    velha = _linha(
        pedido_bling="306835",
        pedido_marketplace="PO-029-2",
        status_lido_em=agora - timedelta(hours=2),
        meli_status={"order_status": "SHIPPED"},
    )
    eron = _linha(pedido_bling="306836", pedido_marketplace="PO-029-3", conta="eron")
    db.add_all([lida, velha, eron])
    await db.commit()
    fake = FakeTemu()
    monkeypatch.setattr(logistica_temu, "_build_temu_client", lambda _s, _i: fake)

    out = await logistica_temu.enrich_recent(
        db, ids=[lida.id, velha.id, eron.id], only_empty=False, agora=agora
    )

    assert out == {
        "seen": 2,
        "updated": 1,
        "skipped": 1,
        "failed": 0,
        "recentes": 1,
        "adiadas": 0,
        "interrompida": 0,
    }
    assert {p["parentOrderSn"] for a, p in fake.chamadas if a == logistica_temu.API_PEDIDO} == {
        "PO-029-2"
    }
    assert await logistica_temu.contas_com_api(db) == ["barbosa"]


@pytest_asyncio.fixture
async def lojas(db: AsyncSession, dono) -> None:
    db.add_all(
        [
            StoreInfo(
                user_id=dono.id, platform="temu", account_name="barbosa", bling_store_id="205925390"
            ),
            StoreInfo(
                user_id=dono.id, platform="temu", account_name="eron", bling_store_id="206288707"
            ),
        ]
    )
    if await db.get(SituacaoBling, 15) is None:
        db.add(SituacaoBling(id=15, nome="Em andamento"))
    await db.commit()


def _pedido_bling(numero: str, loja: str, po: str) -> BlingOrder:
    return BlingOrder(
        bling_id=int(numero) * 10,
        numero=numero,
        numeroloja=po,
        loja=loja,
        situacao="15",
        data=datetime.now(UTC),
        item_index=0,
        item_codigo="b012.12.20",
        item_quantidade=1,
    )


@pytest.mark.asyncio
async def test_ingestao_so_poe_a_loja_temu_com_api(db: AsyncSession, dono, lojas):
    db.add_all(
        [_pedido_bling("306833", "205925390", PO), _pedido_bling("306900", "206288707", "PO-029-9")]
    )
    await db.commit()

    # Sem nenhuma integração: a Temu nem entra.
    assert await logistica_ingest.inserir_pedidos_novos(db) == []

    await _integracao(db, dono)
    ids = await logistica_ingest.inserir_pedidos_novos(db)

    linhas = (await db.execute(select(Logistica))).scalars().all()
    assert [(r.pedido_bling, r.plataforma, r.conta) for r in linhas] == [
        ("306833", "Temu", "barbosa")
    ]
    assert len(ids) == 1


# ---- aviso do prazo de entrega ------------------------------------------------


def test_aviso_do_prazo_da_temu():
    hoje = date(2026, 11, 18)
    row = _linha(
        meli_status={"order_status": "SHIPPED", "logistics_status": "ARRIVED_AT_POST_OFFICE"},
        prazo_entrega_amazon=date(2026, 11, 17),
        rastreio="AJ261102157415301",
        localizacao="Objeto chegou ao ponto de entrega",
        localizacao_at=_utc(T_SC),
    )
    assert logistica_amazon_avisos.avisos_devidos(row, hoje) == [
        logistica_amazon_avisos.TIPO_PRAZO_VENCIDO
    ]
    texto = logistica_amazon_avisos.mensagem_aviso(
        row, logistica_amazon_avisos.TIPO_PRAZO_VENCIDO, hoje
    )
    assert "Temu: prazo de entrega da Temu VENCEU" in texto
    assert "Temu PO-029-11708709484150003" in texto
    assert "Amazon" not in texto
    # 3 dias antes.
    assert logistica_amazon_avisos.avisos_devidos(row, date(2026, 11, 14)) == [
        logistica_amazon_avisos.TIPO_PRAZO_3D
    ]
    # Cancelado (a Temu já reembolsou) e entregue não avisam.
    cancelado = _linha(
        meli_status={"order_status": "CANCELED"}, prazo_entrega_amazon=date(2026, 11, 17)
    )
    assert logistica_amazon_avisos.avisos_devidos(cancelado, hoje) == []
    entregue = _linha(
        meli_status={"order_status": "SHIPPED"},
        prazo_entrega_amazon=date(2026, 11, 17),
        entregue_em=_utc(T_SC),
    )
    assert logistica_amazon_avisos.avisos_devidos(entregue, hoje) == []


@pytest.mark.asyncio
async def test_lista_do_informar_da_amazon_nao_leva_a_temu(db: AsyncSession):
    db.add(
        _linha(
            meli_status={"order_status": "SHIPPED"},
            prazo_entrega_amazon=date(2026, 11, 17),
            data=date.today(),
        )
    )
    # Amazon: Envio próprio entra nas duas listas; DBA em nenhuma.
    amazon = {
        "plataforma": "Amazon",
        "conta": "kia",
        "data": date.today(),
        "meli_status": {"order_status": "Shipped"},
    }
    db.add(
        _linha(
            pedido_bling="296762",
            pedido_marketplace="701-1",
            amazon_canal="proprio",
            prazo_entrega_amazon=date(2026, 11, 20),
            **amazon,
        )
    )
    db.add(
        _linha(
            pedido_bling="296763",
            pedido_marketplace="701-2",
            amazon_canal="dba",
            prazo_entrega_amazon=date(2026, 11, 20),
            **amazon,
        )
    )
    await db.commit()
    so_amazon = await logistica_amazon_avisos.pedidos_em_transito(db)
    assert [r.pedido_bling for r in so_amazon] == ["296762"]
    com_temu = await logistica_amazon_avisos.pedidos_em_transito(db, temu=True)
    # Prazo mais próximo primeiro: Temu 17/11, Amazon 20/11.
    assert [r.pedido_bling for r in com_temu] == ["306833", "296762"]


# ---- revisão de 26/11 -------------------------------------------------------------


def _pacote_rastreio(codigo: str, *eventos: tuple[int, str, str]) -> dict:
    return {
        "trackingNum": codigo,
        "trackingInfo": [
            {"statusText": t, "logisticsUpdatedAt": q, "logisticsStatus": st}
            for q, st, t in eventos
        ],
    }


class FakeTemuPacotes(FakeTemu):
    """Um rastreio por pacote; `quebrado` = pacotes cujo rastreio falha."""

    def __init__(self, detalhe, por_pacote, quebrado=()):
        super().__init__(detalhe=detalhe)
        self.por_pacote = por_pacote
        self.quebrado = set(quebrado)

    async def _chamar(self, api, params=None, **_kw):
        self.chamadas.append((api, dict(params or {})))
        if api == logistica_temu.API_PEDIDO:
            return {"success": True, "result": self.detalhe}
        pk = (params or {}).get("packageSn")
        if pk in self.quebrado:
            raise TemuErro("Temu: HTTP 429", codigo=None, classe=CLASSE_RETRYABLE, http=429)
        return {"success": True, "result": self.por_pacote[pk]}


DOIS_PACOTES = {
    **DETALHE,
    "orderList": [
        {"orderSn": "a", "packageSnInfo": [{"packageSn": "PK-1"}]},
        {"orderSn": "b", "packageSnInfo": [{"packageSn": "PK-2"}]},
    ],
}


@pytest.mark.asyncio
async def test_varios_pacotes_entregue_so_com_todos():
    p1 = _pacote_rastreio(
        "AJ1",
        (T_COLETA, "Last Mile Carrier Picked up", "Objeto coletado"),
        (T_SC, "Delivered", "Objeto entregue"),
    )
    p2 = _pacote_rastreio(
        "AJ2",
        (T_PRONTO, "Last Mile Carrier Picked up", "Objeto coletado"),
        (T_SP1, "In transit", "A caminho"),
    )
    fake = FakeTemuPacotes(DOIS_PACOTES, {"PK-1": p1, "PK-2": p2})
    enr = await logistica_temu.build_enrichment(fake, PO)  # type: ignore[arg-type]
    assert enr["meli_status"]["logistics_status"] == "IN_TRANSIT"  # o menos adiantado
    assert enr["entregue_em"] is None
    assert enr["localizacao"] == "Objeto entregue"  # o evento mais novo
    assert enr["rastreio"] == "AJ1, AJ2"
    assert logistica_temu.coleta([p1, p2]) == _utc(T_COLETA)  # a coleta mais antiga

    p2_entregue = _pacote_rastreio(
        "AJ2",
        (T_PRONTO, "Last Mile Carrier Picked up", "Objeto coletado"),
        (T_SC + 60, "Delivered", "Objeto entregue"),
    )
    fake = FakeTemuPacotes(DOIS_PACOTES, {"PK-1": p1, "PK-2": p2_entregue})
    enr = await logistica_temu.build_enrichment(fake, PO)  # type: ignore[arg-type]
    assert enr["meli_status"]["logistics_status"] == "DELIVERED"
    assert enr["entregue_em"] == _utc(T_SC + 60)


@pytest.mark.asyncio
async def test_rastreio_que_falha_nao_apaga_o_status_do_envio():
    fake = FakeTemuPacotes(DETALHE, {}, quebrado={PACOTE})
    enr = await logistica_temu.build_enrichment(fake, PO)  # type: ignore[arg-type]
    assert enr["rastreio_falhou"] is True
    carimbo = {"em": "2026-11-03T13:39:03+00:00", "fonte": "plataforma"}
    row = _linha(
        meli_status={"order_status": "SHIPPED", "logistics_status": "IN_TRANSIT"},
        status_datas={"order_status": carimbo, "logistics_status": carimbo},
        rastreio="AJ261102157415301",
        localizacao="A caminho",
        localizacao_at=_utc(T_SP1),
    )
    logistica_temu.aplicar(row, enr, agora=datetime(2026, 11, 26, tzinfo=UTC))
    assert row.meli_status == {"order_status": "SHIPPED", "logistics_status": "IN_TRANSIT"}
    assert row.status_datas["logistics_status"] == carimbo
    assert (row.rastreio, row.localizacao) == ("AJ261102157415301", "A caminho")
    assert row.prazo_entrega_amazon == date(2026, 11, 17)  # o pedido em si foi lido
    # Sem pacote ainda (etiqueta não comprada): aí não há envio, e tudo bem.
    sem_pacote = {**DETALHE, "orderList": [{"orderSn": "x", "packageSnInfo": []}]}
    enr = await logistica_temu.build_enrichment(FakeTemu(detalhe=sem_pacote), PO)  # type: ignore[arg-type]
    assert enr["rastreio_falhou"] is False and "logistics_status" not in enr["meli_status"]


@pytest.mark.asyncio
async def test_coleta_pela_api():
    assert await logistica_temu.coleta_pela_api(FakeTemu(), PO) == (  # type: ignore[arg-type]
        logistica_temu.COLETA_SAIU,
        _utc(T_COLETA),
    )
    so_etiqueta = _pacote_rastreio("AJ1", (T_COLETA, "Shipping label created", "Etiqueta"))
    assert await logistica_temu.coleta_pela_api(  # type: ignore[arg-type]
        FakeTemu(rastreio=so_etiqueta), PO
    ) == (logistica_temu.COLETA_NAO_SAIU, None)
    quebrado = FakeTemuPacotes(DETALHE, {}, quebrado={PACOTE})
    assert await logistica_temu.coleta_pela_api(quebrado, PO) == (  # type: ignore[arg-type]
        logistica_temu.COLETA_SEM_RESPOSTA,
        None,
    )


def test_temu_viva():
    hoje = date(2026, 11, 26)
    viva = _linha(meli_status={"order_status": "SHIPPED"}, data=date(2026, 11, 20))
    assert logistica_temu.temu_viva(viva, hoje=hoje)
    assert logistica_temu.temu_viva(_linha(data=date(2026, 11, 20)), hoje=hoje)  # nunca lida
    for morta in (
        _linha(meli_status={"order_status": "RECEIPTED"}, data=date(2026, 11, 20)),
        _linha(meli_status={"order_status": "CANCELED"}, data=date(2026, 11, 20)),
        _linha(entregue_em=_utc(T_SC), data=date(2026, 11, 20)),
        _linha(status_bling="Entregue", data=date(2026, 11, 20)),
        _linha(data=date(2026, 9, 1)),
        _linha(plataforma="Amazon", data=date(2026, 11, 20)),
    ):
        assert not logistica_temu.temu_viva(morta, hoje=hoje)


@pytest.mark.asyncio
async def test_motor_rele_a_temu_viva_mesmo_escondida_por_regra(db: AsyncSession, monkeypatch):
    from app.services import logistica_match

    hoje = datetime.now(UTC).date()
    viva = _linha(meli_status={"order_status": "SHIPPED"}, data=hoje)
    entregue = _linha(
        pedido_bling="306834",
        meli_status={"order_status": "RECEIPTED"},
        data=hoje,
    )
    amazon = _linha(pedido_bling="296762", plataforma="Amazon", conta="kia", data=hoje)
    db.add_all([viva, entregue, amazon])
    await db.commit()
    # Toda linha "resolvida" pela regra (escondida no painel).
    monkeypatch.setattr(logistica_match, "estado_resolvido", lambda *a, **k: True)
    monkeypatch.setattr(logistica_match, "deve_monitorar", lambda *a, **k: False)
    monkeypatch.setattr(logistica_match, "devolucao_travada", lambda *a, **k: False)

    pend = await logistica_ingest._ids_pendentes(db)

    assert pend["temu"] == [viva.id]
    assert pend["amazon"] == []


@pytest.mark.asyncio
async def test_motor_tem_teto_e_para_na_primeira_falha_de_rede(db: AsyncSession, dono, monkeypatch):
    await _integracao(db, dono)
    agora = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
    linhas = [
        _linha(
            pedido_bling=str(306840 + i),
            pedido_marketplace=f"PO-029-{i}",
            meli_status={"order_status": "SHIPPED"},
            status_lido_em=None if i == 4 else agora - timedelta(hours=10 - i),
        )
        for i in range(5)
    ]
    db.add_all(linhas)
    await db.commit()
    fake = FakeTemu()
    monkeypatch.setattr(logistica_temu, "_build_temu_client", lambda _s, _i: fake)

    out = await logistica_temu.enrich_recent(
        db, ids=[r.id for r in linhas], only_empty=False, agora=agora, teto=2
    )
    assert (out["seen"], out["updated"], out["adiadas"]) == (2, 2, 3)
    lidos = [p["parentOrderSn"] for a, p in fake.chamadas if a == logistica_temu.API_PEDIDO]
    assert sorted(lidos) == ["PO-029-0", "PO-029-4"]  # nunca lida + a lida há mais tempo

    # Temu fora do ar: o 1º lote falha e a rodada para (não tenta as outras).
    quebrado = FakeTemuPacotes(DETALHE, {}, quebrado=set())

    async def fora_do_ar(api, params=None, **_kw):
        quebrado.chamadas.append((api, dict(params or {})))
        raise TemuErro("Temu fora do ar", codigo=None, classe=CLASSE_RETRYABLE)

    quebrado._chamar = fora_do_ar  # type: ignore[method-assign]
    monkeypatch.setattr(logistica_temu, "_build_temu_client", lambda _s, _i: quebrado)
    novas = [
        _linha(pedido_bling=str(306900 + i), pedido_marketplace=f"PO-029-9{i}") for i in range(7)
    ]
    db.add_all(novas)
    await db.commit()
    out = await logistica_temu.enrich_recent(
        db, ids=[r.id for r in novas], only_empty=False, agora=agora
    )
    assert out["interrompida"] == 1
    assert out["failed"] == logistica_temu._CONCURRENCY  # só o 1º lote


@pytest.mark.asyncio
async def test_motor_corta_a_temu_lenta(db: AsyncSession, monkeypatch):
    import asyncio

    async def lenta(_session, **_kw):
        await asyncio.sleep(5)
        return {"seen": 1}

    monkeypatch.setattr(logistica_temu, "enrich_recent", lenta)
    monkeypatch.setattr(logistica_ingest, "TEMU_TETO_SEGUNDOS", 0.05)
    assert await logistica_ingest._temu_best_effort(db, ids=[], only_empty=False) == {"falhou": 1}


def test_aviso_temu_velho_ou_ja_tratado_nao_sai():
    hoje = date(2026, 11, 26)
    base = {
        "meli_status": {"order_status": "SHIPPED", "logistics_status": "IN_TRANSIT"},
        "prazo_entrega_amazon": date(2026, 11, 17),
    }
    # Prazo vencido há 9 dias (a Temu já reembolsou): não manda "VENCEU".
    assert logistica_amazon_avisos.avisos_devidos(_linha(**base), hoje) == []
    # Vencido ontem: manda.
    assert logistica_amazon_avisos.avisos_devidos(
        _linha(**{**base, "prazo_entrega_amazon": date(2026, 11, 24)}), hoje
    ) == [logistica_amazon_avisos.TIPO_PRAZO_VENCIDO]
    # Já tratado por gente (Aguardando Devolução / Problemas no Bling): nada.
    for situacao in ("Aguardando Devolução", "Problemas"):
        row = _linha(**{**base, "prazo_entrega_amazon": date(2026, 11, 24)}, status_bling=situacao)
        assert logistica_amazon_avisos.avisos_devidos(row, hoje) == []
    # Entregue em parte: o pacote que falta ainda pode estourar.
    parte = _linha(
        meli_status={"order_status": "PARTIALLY_RECEIPTED"},
        prazo_entrega_amazon=date(2026, 11, 24),
    )
    assert logistica_amazon_avisos.avisos_devidos(parte, hoje) == [
        logistica_amazon_avisos.TIPO_PRAZO_VENCIDO
    ]


# ---- "Em andamento" no Bling: API da Temu dentro do temu_rastreio ------------------


@pytest.mark.asyncio
async def test_varredura_de_1_min_nao_mexe_na_temu(db: AsyncSession, dono, monkeypatch):
    integ = await _integracao(db, dono)

    async def nao_pode(*_a, **_k):
        raise AssertionError("a varredura de 1 min não consulta a Temu")

    monkeypatch.setattr(TemuClient, "_chamar", nao_pode)
    order = _pedido_bling("306833", "205925390", PO)
    assert await msc._check_marketplace_shipped(db, integ, [order], {}) == {}


async def _loja_barbosa(db: AsyncSession, dono: User) -> Integration:
    integ = await _integracao(db, dono)
    company = Company(
        razao_social="Barbosa",
        cnpj=uuid.uuid4().hex[:14],
        apelido=f"bb-{uuid.uuid4().hex[:6]}",
        responsavel_id=dono.id,
    )
    db.add(company)
    await db.flush()
    db.add(
        Store(
            company_id=company.id,
            marketplace=Marketplace.TEMU,
            status=StoreStatus.ACTIVE,
            integration_id=integ.id,
            bling_store_id=205925390,
        )
    )
    await db.commit()
    return integ


@pytest.mark.asyncio
async def test_coletas_pela_api_separa_e_cai_pro_17track(db: AsyncSession, dono, monkeypatch):
    await _loja_barbosa(db, dono)
    pedidos = []
    for numero in ("1", "2", "3", "4"):
        o = _pedido_bling(numero, "205925390", f"PO-029-100000000{numero}")
        o.situacao = "21"
        pedidos.append(o)
    eron = _pedido_bling("5", "206288707", "PO-029-1000000005")
    eron.situacao = "21"
    respostas = {
        "PO-029-1000000001": (logistica_temu.COLETA_SAIU, _utc(T_COLETA)),
        "PO-029-1000000002": (logistica_temu.COLETA_NAO_SAIU, None),
        "PO-029-1000000003": "erro",
        # Depois do erro a loja vai toda pro 17track nesta rodada.
        "PO-029-1000000004": (logistica_temu.COLETA_SAIU, _utc(T_COLETA)),
    }

    async def coleta(_cliente, po):
        r = respostas[po]
        if r == "erro":
            raise TemuErro("Temu 3000034: token vencido", codigo=3000034, classe="conta")
        return r

    monkeypatch.setattr(logistica_temu, "coleta_pela_api", coleta)

    coletados, restantes, cont = await temu_rastreio._coletas_pela_api(db, [*pedidos, eron])

    assert [(o.numero, h) for o, h in coletados] == [("1", _utc(T_COLETA))]
    assert [o.numero for o in restantes] == ["3", "4", "5"]
    assert cont == {"api_coletados": 1, "api_ainda_nao_saiu": 1, "api_falhou": 1}
    assert await temu_rastreio.lojas_temu_com_api(db) == {"205925390"}
    # A Barbosa continua candidata do 17track (de reserva).
    db.add_all([*pedidos, eron])
    await db.commit()
    assert {o.numero for o in await temu_rastreio._candidatos(db)} == {"1", "2", "3", "4", "5"}


class BlingFalso:
    def __init__(self, bid: int, numero: str, po: str):
        self.bid, self.numero, self.po = bid, numero, po
        self.leituras = 0
        self.escritas: list[tuple[int, int]] = []

    async def _request(self, method, path):
        import httpx

        estado = 21 if self.leituras == 0 else 15
        self.leituras += 1
        return httpx.Response(
            200,
            json={
                "data": {
                    "id": self.bid,
                    "numero": self.numero,
                    "numeroLoja": self.po,
                    "situacao": {"id": estado},
                }
            },
        )

    async def update_order_situacao(self, bid, estado):
        self.escritas.append((bid, estado))
        return True


@pytest.mark.asyncio
async def test_rodada_coleta_pela_api_fica_verde_com_as_travas(db: AsyncSession, dono, monkeypatch):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    from app import db as app_db
    from app.models import BlingEnvioEvento, MargemAudit
    from app.services import logistica_track

    await _loja_barbosa(db, dono)
    o = _pedido_bling("306833", "205925390", PO)
    o.situacao = "21"
    o.created_at = datetime.now(UTC)
    db.add(o)
    await db.commit()
    bling = BlingFalso(o.bling_id, "306833", PO)

    @asynccontextmanager
    async def scope():
        yield db
        await db.commit()

    async def sem_17track(*_a, **_k):
        raise AssertionError("a Barbosa não vai ao 17track quando a API responde")

    monkeypatch.setattr(app_db, "session_scope", scope)
    monkeypatch.setattr(msc, "_get_bling_integration", AsyncMock(return_value=object()))
    monkeypatch.setattr(msc, "_build_bling_client", AsyncMock(return_value=bling))
    monkeypatch.setattr(logistica_track, "_chamar", sem_17track)
    monkeypatch.setattr(TemuClient, "_chamar", FakeTemu()._chamar)

    resumo = await temu_rastreio.rodada()

    assert resumo["verdes"] == 1 and resumo["api_coletados"] == 1 and resumo["erros"] == 0
    assert bling.escritas == [(o.bling_id, 15)]
    await db.refresh(o)
    assert o.situacao == "15" and o.em_andamento_data == date(2026, 11, 3)  # dia da coleta
    ev = (
        await db.scalars(select(BlingEnvioEvento).where(BlingEnvioEvento.bling_id == o.bling_id))
    ).one()
    assert ev.occurred_at == _utc(T_COLETA)
    origens = set(
        (
            await db.scalars(select(MargemAudit.origem).where(MargemAudit.pedido_bling == "306833"))
        ).all()
    )
    assert origens == {"job_envio_temu", "temu_api_coleta"}
