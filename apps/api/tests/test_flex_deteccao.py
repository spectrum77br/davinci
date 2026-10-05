"""Flex, etapa 1: reconhecer o pedido que sai pelo Flex (sem chamada a mais).

ML: o envio diz `logistic.type = self_service` (formato novo, `x-format-new`)
ou `logistic_type = self_service` (antigo); o Turbo também vem como
self_service. Shopee: canal 90022 (`package_list[].logistics_channel_id`) ou
`shipping_carrier = "Shopee Entrega Direta"`. "Não sei" (envio sem as chaves)
nunca vira "não é Flex".

Nenhuma chamada real: clientes falsos e respx.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
import respx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    BlingOrder,
    Company,
    FlexPedido,
    Integration,
    IntegrationPlatform,
    Logistica,
    Marketplace,
    Store,
    StoreStatus,
    User,
    UserRole,
    UserStatus,
)
from app.security.cipher import encrypt_json
from app.services import (
    flex_envio,
    logistica_meli,
    logistica_shopee,
)
from app.services import marketplace_shipment_check as check
from app.services.marketplaces.shopee import ShopeeClient

# ─── a função pura: tabela de casos ─────────────────────────────────────────

_ML_NOVO_FLEX = {"logistic": {"mode": "me2", "type": "self_service", "direction": "forward"}}
_ML_ANTIGO_FLEX = {"mode": "me2", "logistic_type": "self_service", "status": "ready_to_ship"}
# Turbo: o ML devolve self_service também (doc "Envios Turbo") — conta como Flex.
_ML_TURBO = {
    "logistic": {"mode": "me2", "type": "self_service", "direction": "forward"},
    "tags": ["turbo"],
}


@pytest.mark.parametrize(
    ("plataforma", "dados", "esperado", "tipo"),
    [
        # Mercado Livre — formato novo, antigo e Turbo
        ("ml", _ML_NOVO_FLEX, True, "self_service"),
        ("ml", _ML_ANTIGO_FLEX, True, "self_service"),
        ("ml", _ML_TURBO, True, "self_service"),
        ("Mercado Livre", _ML_NOVO_FLEX, True, "self_service"),
        (IntegrationPlatform.ML, _ML_ANTIGO_FLEX, True, "self_service"),
        ("ml", {"logistic": {"mode": "me2", "type": "cross_docking"}}, False, "cross_docking"),
        ("ml", {"logistic_type": "drop_off"}, False, "drop_off"),
        ("ml", {"logistic_type": "xd_drop_off"}, False, "xd_drop_off"),
        ("ml", {"logistic": {"type": "fulfillment"}}, False, "fulfillment"),
        # Os dois formatos no mesmo envio: o novo vence.
        (
            "ml",
            {"logistic": {"type": "cross_docking"}, "logistic_type": "self_service"},
            False,
            "cross_docking",
        ),
        ("ml", {"logistic": {"type": ""}, "logistic_type": "SELF_SERVICE"}, True, "self_service"),
        # Envio sem o tipo: não sei (nunca "não é Flex").
        ("ml", {"status": "ready_to_ship", "substatus": "printed"}, None, None),
        ("ml", {"logistic": None}, None, None),
        ("ml", {}, None, None),
        ("ml", None, None, None),
        # Shopee — pelo canal (resumo ou pacote) e pelo nome
        ("shopee", {"logistics_channel_id": 90022}, True, "90022"),
        ("Shopee", {"package_list": [{"logistics_channel_id": 90022}]}, True, "90022"),
        (
            "shopee",
            {"logistics_channel_id": "90022", "shipping_carrier": "Shopee Entrega Direta"},
            True,
            "90022 · Shopee Entrega Direta",
        ),
        ("shopee", {"shipping_carrier": "Shopee Entrega Direta"}, True, "Shopee Entrega Direta"),
        (
            "shopee",
            {"shipping_carrier": "  shopee  ENTREGA   direta "},
            True,
            "shopee ENTREGA direta",
        ),
        # Canal desconhecido mas nome de Flex: vale o nome (OU).
        (
            "shopee",
            {"logistics_channel_id": 91999, "shipping_carrier": "Shopee Entrega Direta"},
            True,
            "91999 · Shopee Entrega Direta",
        ),
        # Turbo da Shopee (90011) e SPX não são Flex.
        (
            "shopee",
            {"logistics_channel_id": 90011, "shipping_carrier": "Turbo"},
            False,
            "90011 · Turbo",
        ),
        (
            "shopee",
            {"logistics_channel_id": 90001, "shipping_carrier": "Shopee Xpress"},
            False,
            "90001 · Shopee Xpress",
        ),
        # Vários pacotes: um no Flex basta.
        (
            "shopee",
            {"package_list": [{"logistics_channel_id": 90001}, {"logistics_channel_id": 90022}]},
            True,
            "90001,90022",
        ),
        # Sem canal nem transportadora (ou canal 0): não sei.
        ("shopee", {"status": "READY_TO_SHIP"}, None, None),
        ("shopee", {"logistics_channel_id": 0, "package_list": []}, None, None),
        # Plataforma sem Flex.
        ("Amazon", _ML_NOVO_FLEX, None, None),
        ("tiktok", {"logistics_channel_id": 90022}, None, None),
    ],
)
def test_eh_envio_flex_tabela(plataforma, dados, esperado, tipo):
    assert flex_envio.eh_envio_flex(plataforma, dados, canais_shopee={"90022"}) is esperado
    if esperado is None:
        assert flex_envio.campos_envio(plataforma, dados, canais_shopee={"90022"}) == {}
    else:
        assert flex_envio.tipo_envio(plataforma, dados) == tipo
        assert flex_envio.campos_envio(plataforma, dados, canais_shopee={"90022"}) == {
            "envio_tipo": tipo,
            "envio_flex": esperado,
        }


def test_canais_da_shopee_vem_da_configuracao(monkeypatch):
    # Padrão: 90022 (guia 290). O dono pode trocar sem deploy de código.
    monkeypatch.setattr(get_settings(), "flex_shopee_canais", "90022")
    assert flex_envio.canais_shopee_flex() == frozenset({"90022"})
    assert flex_envio.eh_envio_flex("shopee", {"logistics_channel_id": 90022}) is True

    monkeypatch.setattr(get_settings(), "flex_shopee_canais", " 91000, 91001 ,")
    assert flex_envio.canais_shopee_flex() == frozenset({"91000", "91001"})
    assert flex_envio.eh_envio_flex("shopee", {"logistics_channel_id": 91001}) is True
    assert flex_envio.eh_envio_flex("shopee", {"logistics_channel_id": 90022}) is False


@pytest.mark.parametrize(
    ("codigo", "no_sp"),
    [
        ("dg053.sp", True),
        ("DG053.SP", True),
        ("dg053.sp+a001.sp", True),
        ("dg053.sp+a001", True),  # pedaço sem lote não decide
        ("dg053.ci", False),
        ("dg053.sp+a001.ci", False),  # kit misturado
        ("dg053", False),
        ("b009.8.12.20.24", False),  # número não é lote
        (None, False),
    ],
)
def test_item_no_sp(codigo, no_sp):
    assert flex_envio.item_no_sp(codigo) is no_sp


# ─── Shopee: o canal vem na mesma chamada do order_status ───────────────────


@pytest.mark.asyncio
async def test_shopee_status_map_pede_e_devolve_canal_e_transportadora():
    client = ShopeeClient(
        {"shop_id": 99, "access_token": "tok", "expires_at": int(time.time()) + 3600}
    )
    corpo = {
        "error": "",
        "response": {
            "order_list": [
                {
                    "order_sn": "2510FLEX",
                    "order_status": "READY_TO_SHIP",
                    "update_time": 1759400000,
                    "ship_by_date": 1759430000,
                    "shipping_carrier": "Shopee Entrega Direta",
                    "package_list": [
                        {"package_number": "P1", "logistics_channel_id": 90022},
                    ],
                },
                {"order_sn": "2510SPX", "order_status": "shipped", "update_time": 1},
            ]
        },
    }
    with respx.mock(base_url=client._base) as router:
        rota = router.get("/api/v2/order/get_order_detail").mock(
            return_value=httpx.Response(200, json=corpo)
        )
        out = await client.get_order_status_map(["2510FLEX", "2510SPX"])

    campos = rota.calls.last.request.url.params["response_optional_fields"].split(",")
    assert {"order_status", "update_time", "ship_by_date"} <= set(campos)
    assert {"package_list", "shipping_carrier"} <= set(campos)
    assert out["2510FLEX"] == {
        "status": "READY_TO_SHIP",
        "update_time": 1759400000,
        "ship_by_date": 1759430000,
        "logistics_channel_id": 90022,
        "package_list": [{"logistics_channel_id": 90022}],
        "shipping_carrier": "Shopee Entrega Direta",
    }
    # Pedido sem pacote/transportadora: as chaves vêm vazias (não sei).
    assert out["2510SPX"]["logistics_channel_id"] is None
    assert out["2510SPX"]["shipping_carrier"] is None
    assert flex_envio.eh_envio_flex("shopee", out["2510FLEX"]) is True
    assert flex_envio.eh_envio_flex("shopee", out["2510SPX"]) is None


# ─── Logística: o enriquecimento grava o tipo do envio ──────────────────────


class _FakeML:
    def __init__(self, shipment: dict | None):
        self._shipment = shipment

    async def get_order(self, order_id):
        return {"status": "paid", "shipping": {"id": 4455}}

    async def get_shipment(self, shipment_id):
        if self._shipment is None:
            raise RuntimeError("shipment fora do ar")
        return self._shipment

    async def _request(self, method, path, **kwargs):  # previsão dedicada: sem
        return SimpleNamespace(status_code=404, json=lambda: {})


@pytest.mark.asyncio
async def test_build_enrichment_ml_le_o_tipo_do_envio_que_ja_busca():
    envio = {**_ML_NOVO_FLEX, "status": "ready_to_ship", "substatus": "printed"}
    enr = await logistica_meli.build_enrichment(_FakeML(envio), "1")
    assert enr["envio_tipo"] == "self_service"
    assert enr["envio_flex"] is True

    comum = {"logistic_type": "cross_docking", "status": "ready_to_ship"}
    enr = await logistica_meli.build_enrichment(_FakeML(comum), "1")
    assert enr["envio_tipo"] == "cross_docking"
    assert enr["envio_flex"] is False

    # Envio fora do ar: nada de envio_flex (não sei ≠ não é).
    enr = await logistica_meli.build_enrichment(_FakeML(None), "1")
    assert "envio_flex" not in enr and "envio_tipo" not in enr


@pytest.mark.asyncio
async def test_enrich_row_ml_grava_e_nao_apaga_com_leitura_vazia(db: AsyncSession, monkeypatch):
    row = Logistica(
        plataforma="Mercado Livre", pedido_marketplace="2000001", conta="kia", meli_status={}
    )
    db.add(row)
    await db.commit()

    respostas = iter([_ML_ANTIGO_FLEX, None])
    original = logistica_meli.build_enrichment

    async def _enr(client, order_id):
        return await original(_FakeML(next(respostas)), order_id)

    monkeypatch.setattr(logistica_meli, "build_enrichment", _enr)
    await logistica_meli.enrich_row(db, row, client_cache={"kia": object()})
    assert (row.envio_flex, row.envio_tipo) == (True, "self_service")
    # Segunda leitura sem o envio: o que já se sabia fica.
    await logistica_meli.enrich_row(db, row, client_cache={"kia": object()})
    assert (row.envio_flex, row.envio_tipo) == (True, "self_service")
    await db.commit()


class _FakeShopee:
    def __init__(self, info_por_sn: dict[str, dict]):
        self._info = info_por_sn

    async def get_order_status_map(self, order_sns):
        return {sn: self._info[sn] for sn in order_sns if sn in self._info}

    async def get_tracking_number(self, order_sn):
        return None

    async def get_tracking_info(self, order_sn):
        return {}

    async def get_return_list(self, **kwargs):
        return []


_SHOPEE_FLEX = {
    "status": "READY_TO_SHIP",
    "update_time": None,
    "logistics_channel_id": 90022,
    "package_list": [{"logistics_channel_id": 90022}],
    "shipping_carrier": "Shopee Entrega Direta",
}
_SHOPEE_SPX = {
    "status": "READY_TO_SHIP",
    "update_time": None,
    "logistics_channel_id": 90001,
    "package_list": [{"logistics_channel_id": 90001}],
    "shipping_carrier": "Shopee Xpress",
}


@pytest.mark.asyncio
async def test_build_enrichment_shopee_le_o_canal_do_mesmo_pedido():
    enr = await logistica_shopee.build_enrichment(_FakeShopee({"S1": _SHOPEE_FLEX}), "S1")
    assert enr["envio_flex"] is True
    assert enr["envio_tipo"] == "90022 · Shopee Entrega Direta"
    enr = await logistica_shopee.build_enrichment(_FakeShopee({"S2": _SHOPEE_SPX}), "S2")
    assert enr["envio_flex"] is False


@pytest.mark.asyncio
async def test_sweep_shopee_classifica_as_linhas_da_janela_sem_chamada_a_mais(
    db: AsyncSession, monkeypatch
):
    hoje = datetime.now(UTC).date()
    flex = Logistica(
        plataforma="Shopee",
        pedido_marketplace="S1",
        conta="vortan",
        data=hoje,
        meli_status={"order_status": "READY_TO_SHIP"},
    )
    spx = Logistica(
        plataforma="Shopee",
        pedido_marketplace="S2",
        conta="vortan",
        data=hoje,
        meli_status={"order_status": "READY_TO_SHIP"},
    )
    db.add_all([flex, spx])
    await db.commit()

    async def _integ(session, conta):
        return SimpleNamespace(name=conta)

    fake = _FakeShopee({"S1": _SHOPEE_FLEX, "S2": _SHOPEE_SPX})
    monkeypatch.setattr(logistica_shopee, "_shopee_integration_for_conta", _integ)
    monkeypatch.setattr(logistica_shopee, "_build_shopee_client", lambda s, i: fake)

    res = await logistica_shopee.sweep_pos_venda(db)
    # O tipo de envio não é "mudança de status": nada para reaplicar regra.
    assert res["ids"] == []
    await db.refresh(flex)
    await db.refresh(spx)
    assert (flex.envio_flex, flex.envio_tipo) == (True, "90022 · Shopee Entrega Direta")
    assert (spx.envio_flex, spx.envio_tipo) == (False, "90001 · Shopee Xpress")


# ─── shipment check: o envio já lido vira registro ──────────────────────────


class _FakeMLAberto:
    """Pedido pago, envio Flex ainda não despachado (passe 2 lê o envio)."""

    def __init__(self, envio: dict, sla: str = "2026-10-02T13:00:00.000-03:00"):
        self.envio = envio
        self.sla = sla
        self.chamadas: list[str] = []

    async def get_order(self, order_id):
        self.chamadas.append("order")
        return {"status": "paid", "shipping": {"status": "ready_to_ship", "id": 777}}

    async def get_shipment(self, shipment_id):
        self.chamadas.append("shipment")
        return self.envio

    async def get_shipment_sla(self, shipment_id):
        self.chamadas.append("sla")
        return {"expected_date": self.sla}


@pytest.mark.asyncio
async def test_ml_shipped_for_registra_o_tipo_sem_chamada_extra():
    check._ml_pack_real_order.clear()
    check._not_found_until.clear()
    client = _FakeMLAberto({**_ML_NOVO_FLEX, "status": "ready_to_ship", "substatus": "printed"})
    o = SimpleNamespace(numeroloja="2000001", bling_id=501, marketplace_ship_deadline=None)
    deadlines: dict[int, datetime] = {}
    envios: dict[int, dict] = {}
    res = await check._ml_shipped_for(client, o, deadlines, envios=envios)
    assert res is None  # continua não enviado
    assert envios == {501: {"envio_tipo": "self_service", "envio_flex": True}}
    # As mesmas chamadas de antes: pedido, envio e o /sla (prazo ainda vazio).
    assert client.chamadas == ["order", "shipment", "sla"]

    # Enviado no passe 2 também registra (o envio já estava em mãos).
    client = _FakeMLAberto({**_ML_ANTIGO_FLEX, "status": "shipped"})
    envios = {}
    res = await check._ml_shipped_for(client, o, {}, envios=envios)
    assert res is not None
    assert envios[501]["envio_flex"] is True


@pytest.mark.asyncio
async def test_prazo_do_flex_do_ml_nao_ganha_o_dia_da_agencia():
    """Achado da revisão: SLA 23:59 de hoje é lido como "agência" e ganha +1
    dia (a operação leva os pacotes à agência na manhã seguinte). O Flex não
    passa por agência — sai de São Bernardo no dia: o prazo fica como veio."""
    check._ml_pack_real_order.clear()
    check._not_found_until.clear()
    hoje = datetime.now(check._BRT).date()
    sla = f"{hoje.isoformat()}T23:59:59.000-03:00"
    esperado = datetime.fromisoformat(sla).astimezone(UTC)
    o = SimpleNamespace(numeroloja="2000002", bling_id=502, marketplace_ship_deadline=None)

    flex = _FakeMLAberto({**_ML_NOVO_FLEX, "status": "ready_to_ship", "substatus": "printed"},
                         sla=sla)
    prazos: dict[int, datetime] = {}
    await check._ml_shipped_for(flex, o, prazos)  # sem `envios`: vale igual
    assert prazos[502] == esperado

    agencia = _FakeMLAberto(
        {"logistic": {"type": "cross_docking"}, "status": "ready_to_ship", "substatus": "printed"},
        sla=sla,
    )
    prazos = {}
    await check._ml_shipped_for(agencia, o, prazos)
    assert prazos[502] == esperado + timedelta(days=1)


@pytest.mark.asyncio
async def test_check_shopee_registra_o_canal_do_lote(monkeypatch):
    class _Cliente:
        def __init__(self, creds, on_token_refresh=None):
            pass

        async def get_order_status_map(self, sns):
            return {"S1": _SHOPEE_FLEX, "S2": _SHOPEE_SPX}

    monkeypatch.setattr(check, "ShopeeClient", _Cliente)
    monkeypatch.setattr(check, "decrypt_json", lambda b: {})
    integ = SimpleNamespace(
        id=uuid.uuid4(), name="vortan", platform=IntegrationPlatform.SHOPEE, credentials=b""
    )
    pedidos = [
        SimpleNamespace(numeroloja="S1", bling_id=11),
        SimpleNamespace(numeroloja="S2", bling_id=12),
        SimpleNamespace(numeroloja="S3", bling_id=13),  # a Shopee não devolveu
    ]
    envios: dict[int, dict] = {}
    await check._check_marketplace_shipped(None, integ, pedidos, {}, envios=envios)
    assert envios == {
        11: {"envio_tipo": "90022 · Shopee Entrega Direta", "envio_flex": True},
        12: {"envio_tipo": "90001 · Shopee Xpress", "envio_flex": False},
    }


# ─── registro no banco ───────────────────────────────────────────────────────


async def _pedido(db: AsyncSession, bling_id: int, *itens: str, numeroloja: str = "") -> None:
    for i, codigo in enumerate(itens):
        db.add(
            BlingOrder(
                bling_id=bling_id,
                numero=str(bling_id),
                numeroloja=numeroloja or f"MKT{bling_id}",
                item_codigo=codigo,
                item_index=i,
                situacao="6",
            )
        )
    await db.commit()


def _lido(bling_id: int, *, flex: bool = True, plataforma: str = "ml", **kw):
    base = {
        "bling_id": bling_id,
        "plataforma": plataforma,
        "integration_id": None,
        "numero": str(bling_id),
        "numeroloja": f"MKT{bling_id}",
        "envio_tipo": "self_service" if flex else "cross_docking",
        "envio_flex": flex,
        "prazo": None,
    }
    return flex_envio.EnvioLido(**{**base, **kw})


@pytest.mark.asyncio
async def test_registrar_envios_grava_flex_pedido_e_a_linha_da_logistica(db: AsyncSession):
    await _pedido(db, 9101, "dg053.sp")  # já no .sp
    await _pedido(db, 9102, "dg053.ci", "a001.sp")  # um item fora do .sp
    await _pedido(db, 9103, "dg090.ci")  # não é Flex
    linha_flex = Logistica(pedido_bling="9102", plataforma="Mercado Livre", meli_status={})
    linha_comum = Logistica(pedido_bling="9103", plataforma="Mercado Livre", meli_status={})
    # Mesmo número, outra plataforma: não é a mesma venda.
    linha_outra = Logistica(pedido_bling="9102", plataforma="Amazon", meli_status={})
    db.add_all([linha_flex, linha_comum, linha_outra])
    await db.commit()

    prazo = datetime(2026, 10, 2, 16, 0, tzinfo=UTC)
    res = await flex_envio.registrar_envios(
        db,
        [_lido(9101, prazo=prazo), _lido(9102), _lido(9103, flex=False)],
    )
    await db.commit()
    assert res == {"flex_pedidos": 2, "logistica": 2}

    pedidos = {p.bling_id: p for p in (await db.execute(select(FlexPedido))).scalars().all()}
    assert set(pedidos) == {9101, 9102}  # só os Flex
    assert pedidos[9101].no_sp is True
    assert pedidos[9101].prazo == prazo
    assert pedidos[9101].plataforma == "ml"
    assert pedidos[9101].envio_tipo == "self_service"
    assert pedidos[9101].detectado_em is not None
    assert pedidos[9102].no_sp is False

    for linha in (linha_flex, linha_comum, linha_outra):
        await db.refresh(linha)
    assert (linha_flex.envio_flex, linha_flex.envio_tipo) == (True, "self_service")
    assert (linha_comum.envio_flex, linha_comum.envio_tipo) == (False, "cross_docking")
    assert linha_outra.envio_flex is None

    # O minuto seguinte lê a mesma coisa: nada é regravado.
    res = await flex_envio.registrar_envios(
        db, [_lido(9101, prazo=prazo), _lido(9102), _lido(9103, flex=False)]
    )
    await db.commit()
    assert res == {"flex_pedidos": 0, "logistica": 0}

    # Leitura sem prazo não apaga o prazo; o robô troca o item para o .sp e o
    # pedido passa a contar como "no .sp".
    await db.execute(text("UPDATE bling_orders SET item_codigo = 'dg053.sp' WHERE bling_id = 9102"))
    await db.commit()
    res = await flex_envio.registrar_envios(db, [_lido(9101), _lido(9102)])
    await db.commit()
    assert res["flex_pedidos"] == 1
    db.expire_all()
    pedidos = {p.bling_id: p for p in (await db.execute(select(FlexPedido))).scalars().all()}
    assert pedidos[9101].prazo == prazo
    assert pedidos[9102].no_sp is True


# ─── a varredura inteira (uma loja ML) ───────────────────────────────────────


async def _loja_ml_com_pedido_flex(db: AsyncSession, monkeypatch) -> Integration:
    """Uma loja ML com o pedido 9201 em aberto (dg053.ci) cujo envio é Flex."""
    dono = User(
        open_id=f"email:fx-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"fx-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
    )
    db.add(dono)
    await db.flush()
    empresa = Company(razao_social="Flex Co", apelido=f"fx-{uuid.uuid4().hex[:6]}")
    db.add(empresa)
    await db.flush()
    loja = Store(
        company_id=empresa.id,
        marketplace=Marketplace.ML,
        status=StoreStatus.ACTIVE,
        bling_store_id=203040,
    )
    db.add(loja)
    await db.flush()
    integ = Integration(
        user_id=dono.id,
        store_id=loja.id,
        platform=IntegrationPlatform.ML,
        name="marquezini",
        credentials=encrypt_json({"access_token": "x", "user_id": 7, "expires_at": 9999999999}),
    )
    bling = Integration(
        user_id=dono.id,
        platform=IntegrationPlatform.BLING,
        name="bling",
        credentials=encrypt_json({"access_token": "x"}),
    )
    db.add_all([integ, bling])
    await db.flush()
    loja.integration_id = integ.id
    await db.commit()
    await _pedido(db, 9201, "dg053.ci", numeroloja="2000009201")
    await db.execute(
        text("UPDATE bling_orders SET loja = '203040', created_at = now() WHERE bling_id = 9201")
    )
    db.add(Logistica(pedido_bling="9201", plataforma="Mercado Livre", meli_status={}))
    await db.commit()

    fake = _FakeMLAberto({**_ML_NOVO_FLEX, "status": "ready_to_ship", "substatus": "printed"})
    monkeypatch.setattr(check, "MercadoLivreClient", lambda creds, on_token_refresh=None: fake)
    check._ml_pack_real_order.clear()
    check._not_found_until.clear()
    return integ


@pytest.mark.asyncio
async def test_sweep_de_envio_registra_o_pedido_flex(db: AsyncSession, monkeypatch):
    integ = await _loja_ml_com_pedido_flex(db, monkeypatch)
    summary = await check.run_check_marketplace_shipped_orders()
    assert summary["flex_pedidos"] == 1
    assert summary["flex_logistica"] == 1
    assert summary["bling_updated"] == 0  # nada saiu: o Bling nem é chamado

    p = (await db.execute(select(FlexPedido))).scalar_one()
    assert (p.bling_id, p.plataforma, p.integration_id) == (9201, "ml", integ.id)
    assert p.numeroloja == "2000009201"
    assert p.no_sp is False
    # O prazo é o /sla que o sweep já lê (13:00 BRT = 16:00 UTC).
    assert p.prazo == datetime(2026, 10, 2, 16, 0, tzinfo=UTC)
    linha = (
        await db.execute(select(Logistica).where(Logistica.pedido_bling == "9201"))
    ).scalar_one()
    assert linha.envio_flex is True



@pytest.mark.asyncio
async def test_pedido_flex_gravado_junto_com_o_prazo(db: AsyncSession, monkeypatch):
    """Achado da revisão: o `flex_pedido` era gravado só no FIM da varredura,
    numa transação separada da do prazo — no meio, o robô de prioridade (e a
    NF automática) via o pedido com prazo e sem `flex_pedido`: "não é Flex".
    Agora os dois vão na mesma transação; a parte da Logística continua
    separada (aqui ela nem roda) e o pedido Flex já está lá com o prazo."""
    await _loja_ml_com_pedido_flex(db, monkeypatch)
    reavaliou: list[bool] = []

    async def _so_logistica(lidos, summary):
        reavaliou.append(bool(summary.get("flex_pedidos")))

    monkeypatch.setattr(check, "_registrar_flex", _so_logistica)
    await check.run_check_marketplace_shipped_orders()
    db.expire_all()
    p = (await db.execute(select(FlexPedido))).scalar_one()
    assert p.bling_id == 9201 and p.prazo == datetime(2026, 10, 2, 16, 0, tzinfo=UTC)
    prazo = (
        await db.execute(
            select(BlingOrder.marketplace_ship_deadline).where(BlingOrder.bling_id == 9201)
        )
    ).scalar_one()
    assert prazo == p.prazo
    assert reavaliou == [True]  # o fim da varredura sabe que o pedido Flex mudou
