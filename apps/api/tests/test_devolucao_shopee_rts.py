# ruff: noqa: E501
"""Devolução Shopee que volta pela SPX do envio de IDA (16/09/2026).

Pedido 295534 (e mais 28 no mesmo dia): a SPX não entregou, o comprador abriu
"não recebi" e a Shopee aceitou o caso como SÓ REEMBOLSO — `needs_logistics`
false, `tracking_number` vazio, `reverse_logistics_status` LOGISTICS_NOT_STARTED.
O pacote voltou pro vendedor pelo MESMO rastreio da ida ("Pedido devolvido",
16/09 10:32), mas a aba Acompanhamento seguia em "Devolução aceita" sem
localização: o sync só olhava o caso. Outros 7 pedidos nem caso tinham
(cancelados após a falha) e a aba ficava em branco.

Aqui: o leitor puro dos eventos da ida, o ramo de `returns_por_pedido` com
um client falso e a gravação da localização pelo sync.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DevolucaoRastreio, Logistica
from app.services import devolucao_rastreio_sync as sync_svc
from app.services import logistica_meli, logistica_rules, logistica_shopee, logistica_tiktok, logistica_track
from app.services.devolucao_returns import ReturnInfo

# Eventos reais do pedido 295534 (BR264343187915J), epoch em segundos.
_IDA_DEVOLVIDA = {
    "logistics_status": "LOGISTICS_DELIVERY_FAILED",  # agregado NÃO muda com a volta
    "tracking_info": [
        {"update_time": 1789565570, "logistics_status": "RETURNED", "description": "Pedido devolvido"},
        {"update_time": 1789542720, "logistics_status": "RETURN_STARTED", "description": "Pedido será devolvido ao vendedor"},
        {"update_time": 1789530781, "logistics_status": "RETURN_INITIATED", "description": "Pedido de devolução está a caminho ."},
        {"update_time": 1789392428, "logistics_status": "RETURN_STARTED", "description": "Pedido será devolvido ao vendedor"},
        {"update_time": 1789008462, "logistics_status": "PICKED_UP", "description": "Seu pedido chegou ao centro logístico: Louveira - SP"},
        {"update_time": 1788907845, "logistics_status": "PICKED_UP", "description": "Pedido postado Capivari - SP"},
    ],
}
_IDA_VOLTANDO = {
    "logistics_status": "LOGISTICS_DELIVERY_FAILED",
    "tracking_info": [
        {"update_time": 1789392428, "logistics_status": "RETURN_STARTED", "description": "Pedido será devolvido ao vendedor"},
        {"update_time": 1789008462, "logistics_status": "PICKED_UP", "description": "Seu pedido chegou ao centro logístico: Louveira - SP"},
    ],
}
_IDA_NORMAL = {
    "logistics_status": "LOGISTICS_DELIVERY_DONE",
    "tracking_info": [
        {"update_time": 1789100000, "logistics_status": "DELIVERED", "description": "Pedido entregue"},
        {"update_time": 1789008462, "logistics_status": "PICKED_UP", "description": "Seu pedido chegou ao centro logístico"},
    ],
}


# ─── leitor puro ─────────────────────────────────────────────────────────


def test_rts_da_ida_le_a_volta_ao_vendedor():
    rts = logistica_shopee.rts_da_ida(_IDA_DEVOLVIDA)
    assert rts is not None
    assert rts.iniciado_em == datetime.fromtimestamp(1789392428, tz=UTC)  # 1º RETURN_STARTED
    assert rts.entregue_em == datetime.fromtimestamp(1789565570, tz=UTC)  # RETURNED
    assert rts.ultimo_em == datetime.fromtimestamp(1789565570, tz=UTC)
    assert rts.localizacao == "Pedido devolvido"


def test_rts_da_ida_voltando_nao_carimba_entrega():
    rts = logistica_shopee.rts_da_ida(_IDA_VOLTANDO)
    assert rts is not None
    assert rts.entregue_em is None
    assert rts.localizacao == "Pedido será devolvido ao vendedor"
    assert rts.iniciado_em == datetime.fromtimestamp(1789392428, tz=UTC)


def test_rts_da_ida_sem_evento_de_volta_e_none():
    assert logistica_shopee.rts_da_ida(_IDA_NORMAL) is None
    assert logistica_shopee.rts_da_ida({}) is None
    assert logistica_shopee.rts_da_ida(None) is None


def test_rotulo_do_status_sintetico_rts():
    txt = logistica_rules.devolucao_status_pt("shopee", {"return_status": logistica_shopee.STATUS_RTS})
    assert txt and "Entrega falhou" in txt


# ─── returns_por_pedido com client falso ─────────────────────────────────


class _FakeShopee:
    def __init__(self, *, returns: list[dict], details: dict[str, dict], tracking: dict[str, dict], numbers: dict[str, str] | None = None):
        self._returns = returns
        self._details = details
        self._tracking = tracking
        self._numbers = numbers or {}
        self.calls: list[tuple[str, str]] = []

    async def get_return_list(self, *, create_time_from, create_time_to):
        self.calls.append(("list", f"{create_time_from}-{create_time_to}"))
        return list(self._returns)

    async def get_return_detail(self, return_sn):
        self.calls.append(("detail", return_sn))
        return dict(self._details.get(return_sn, {}))

    async def get_tracking_info(self, order_sn):
        self.calls.append(("track", order_sn))
        return dict(self._tracking.get(order_sn, {}))

    async def get_tracking_number(self, order_sn):
        self.calls.append(("number", order_sn))
        return self._numbers.get(order_sn)


def _linha(pedido: str, order_sn: str, *, logistics_status: str | None, rastreio: str | None = None) -> Logistica:
    meli = {"order_status": "COMPLETED"}
    if logistics_status:
        meli["logistics_status"] = logistics_status
    return Logistica(
        pedido_bling=pedido, plataforma="Shopee", pedido_marketplace=order_sn, conta="atv",
        meli_status=meli, rastreio=rastreio, data=datetime(2026, 9, 8, tzinfo=UTC).date(),
    )


@pytest.fixture
def shopee_client(monkeypatch):
    """Troca a resolução de integração/cliente por um client falso configurável."""
    holder: dict[str, _FakeShopee] = {}

    async def _integ(session, conta):
        return object()

    def _build(session, integ):
        return holder["client"]

    monkeypatch.setattr(logistica_shopee, "_shopee_integration_for_conta", _integ)
    monkeypatch.setattr(logistica_shopee, "_build_shopee_client", _build)
    return holder


_CASO_SO_REEMBOLSO = {
    "return_sn": "26091402Q30WFKX", "order_sn": "260909HMCYJ1DP", "status": "ACCEPTED",
    "reason": "NOT_RECEIPT", "tracking_number": "", "needs_logistics": False,
    "create_time": 1789394318, "update_time": 1789444321,
}


@pytest.mark.asyncio
async def test_caso_so_reembolso_com_ida_falhada_le_a_volta_pela_spx(shopee_client):
    client = _FakeShopee(
        returns=[_CASO_SO_REEMBOLSO],
        details={"26091402Q30WFKX": {**_CASO_SO_REEMBOLSO, "reverse_logistics_status": "LOGISTICS_NOT_STARTED", "logistics_status": ""}},
        tracking={"260909HMCYJ1DP": _IDA_DEVOLVIDA},
    )
    shopee_client["client"] = client
    linha = _linha("295534", "260909HMCYJ1DP", logistics_status="LOGISTICS_DELIVERY_FAILED", rastreio="BR264343187915J")

    out = await logistica_shopee.returns_por_pedido(None, [linha])

    info = out["295534"]
    assert info.status == "ACCEPTED"  # o caso continua sendo o da Shopee
    assert info.return_id == "26091402Q30WFKX"
    assert info.tracking == "BR264343187915J"  # rastreio da IDA, da própria linha
    assert info.entregue_em == datetime.fromtimestamp(1789565570, tz=UTC)
    assert info.localizacao == "Pedido devolvido"
    assert info.updated_at == datetime.fromtimestamp(1789565570, tz=UTC)  # evento da SPX manda
    assert ("track", "260909HMCYJ1DP") in client.calls
    assert ("number", "260909HMCYJ1DP") not in client.calls  # a linha já tinha o código


@pytest.mark.asyncio
async def test_sem_caso_na_shopee_mas_ida_falhada_vira_rts(shopee_client):
    client = _FakeShopee(
        returns=[], details={}, tracking={"2609CANCEL": _IDA_VOLTANDO}, numbers={"2609CANCEL": "BR265490105327L"},
    )
    shopee_client["client"] = client
    linha = _linha("292849", "2609CANCEL", logistics_status="LOGISTICS_DELIVERY_FAILED")

    out = await logistica_shopee.returns_por_pedido(None, [linha])

    info = out["292849"]
    assert info.status == logistica_shopee.STATUS_RTS
    assert info.return_id is None
    assert info.tracking == "BR265490105327L"  # buscou o código porque a linha estava vazia
    assert info.entregue_em is None
    assert info.localizacao == "Pedido será devolvido ao vendedor"
    assert info.created_at == datetime.fromtimestamp(1789392428, tz=UTC)  # "Em devolução desde"


@pytest.mark.asyncio
async def test_sem_caso_e_ida_normal_fica_de_fora(shopee_client):
    client = _FakeShopee(returns=[], details={}, tracking={"2609OK": _IDA_NORMAL})
    shopee_client["client"] = client
    linha = _linha("299001", "2609OK", logistics_status="LOGISTICS_DELIVERY_DONE")

    out = await logistica_shopee.returns_por_pedido(None, [linha])

    assert "299001" not in out
    assert ("track", "2609OK") not in client.calls  # ida entregue: nem pergunta


@pytest.mark.asyncio
async def test_caso_com_perna_reversa_nao_olha_a_ida(shopee_client):
    caso = {**_CASO_SO_REEMBOLSO, "return_sn": "2609REV", "order_sn": "2609REVSN", "tracking_number": "AP444879986BR", "needs_logistics": True}
    client = _FakeShopee(
        returns=[caso],
        details={"2609REV": {**caso, "reverse_logistics_status": "LOGISTICS_PICKUP_DONE"}},
        tracking={"2609REVSN": _IDA_DEVOLVIDA},
    )
    shopee_client["client"] = client
    linha = _linha("299002", "2609REVSN", logistics_status="LOGISTICS_DELIVERY_FAILED")

    out = await logistica_shopee.returns_por_pedido(None, [linha])

    info = out["299002"]
    assert info.tracking == "AP444879986BR"
    assert info.entregue_em is None and info.localizacao is None
    assert ("track", "2609REVSN") not in client.calls


@pytest.mark.asyncio
async def test_quem_ja_chegou_nao_e_reconsultado(shopee_client):
    client = _FakeShopee(returns=[_CASO_SO_REEMBOLSO], details={"26091402Q30WFKX": _CASO_SO_REEMBOLSO}, tracking={"260909HMCYJ1DP": _IDA_DEVOLVIDA})
    shopee_client["client"] = client
    linha = _linha("295534", "260909HMCYJ1DP", logistics_status="LOGISTICS_DELIVERY_FAILED")

    out = await logistica_shopee.returns_por_pedido(None, [linha], ja_entregues={"295534"})

    assert out["295534"].status == "ACCEPTED"
    assert not [c for c in client.calls if c[0] in ("detail", "track")]


# ─── o sync grava a localização que o marketplace informou ───────────────


@pytest.mark.asyncio
async def test_sync_grava_localizacao_e_chegada_da_volta_pela_ida(db: AsyncSession, monkeypatch):
    pedido = f"4{uuid4().hex[:6]}"
    db.add(Logistica(pedido_bling=pedido, plataforma="Shopee", pedido_marketplace=f"mk-{pedido}", conta="atv"))
    await db.commit()

    entregue = datetime.fromtimestamp(1789565570, tz=UTC)
    info = ReturnInfo(
        fonte="shopee", status="ACCEPTED", tracking="BR264343187915J", carrier="Shopee Xpress (volta pela ida)",
        created_at=datetime(2026, 9, 14, 13, 58, tzinfo=UTC), updated_at=entregue, return_id="26091402Q30WFKX",
        entregue_em=entregue, localizacao="Pedido devolvido", localizacao_em=entregue,
    )

    async def _shopee(session, linhas, **kw):
        return {pedido: info}

    async def _vazio(session, linhas, **kw):
        return {}

    monkeypatch.setattr(logistica_shopee, "returns_por_pedido", _shopee, raising=False)
    monkeypatch.setattr(logistica_tiktok, "returns_por_pedido", _vazio, raising=False)
    monkeypatch.setattr(logistica_meli, "returns_por_pedido", _vazio, raising=False)

    async def _register(numbers):
        return {"ok": True}

    monkeypatch.setattr(logistica_track, "register", _register)

    await sync_svc.run(db, pedidos=[pedido])

    row = (await db.execute(select(DevolucaoRastreio).where(DevolucaoRastreio.pedido_bling == pedido))).scalar_one()
    assert row.rastreio_auto == "BR264343187915J"
    assert row.localizacao_auto == "Pedido devolvido"
    assert row.localizacao_auto_data == entregue
    assert row.pacote_entregue_em == entregue
    assert row.devolucao_status_auto == "ACCEPTED"
    assert row.rastreio is None and row.localizacao is None  # manual intocado


# ─── achados da revisão (16/09) ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_caso_cancelado_com_ida_falhada_vira_rts(shopee_client):
    """Caso CANCELLED não pinta a aba (devolucao_status_pt → None) e esconderia
    a volta pela SPX: o status vira o sintético RTS, o return_id fica."""
    caso = {**_CASO_SO_REEMBOLSO, "return_sn": "2609CANC", "order_sn": "2609CANCSN", "status": "CANCELLED"}
    client = _FakeShopee(returns=[caso], details={"2609CANC": caso}, tracking={"2609CANCSN": _IDA_DEVOLVIDA})
    shopee_client["client"] = client
    linha = _linha("299003", "2609CANCSN", logistics_status="LOGISTICS_DELIVERY_FAILED", rastreio="BR1")

    info = (await logistica_shopee.returns_por_pedido(None, [linha]))["299003"]

    assert info.status == logistica_shopee.STATUS_RTS
    assert info.return_id == "2609CANC"
    assert info.entregue_em == datetime.fromtimestamp(1789565570, tz=UTC)
    assert logistica_rules.devolucao_status_pt("shopee", {"return_status": info.status})


@pytest.mark.asyncio
async def test_depois_da_chegada_mantem_codigo_e_transportadora_sem_api(shopee_client):
    """Rodada seguinte à chegada (ja_entregues): nada de API, mas o código e a
    transportadora continuam vindo — senão o sync regravaria vazio."""
    client = _FakeShopee(returns=[_CASO_SO_REEMBOLSO], details={}, tracking={})
    shopee_client["client"] = client
    linha = _linha("295534", "260909HMCYJ1DP", logistics_status="LOGISTICS_DELIVERY_FAILED", rastreio="BR264343187915J")

    info = (await logistica_shopee.returns_por_pedido(None, [linha], ja_entregues={"295534"}))["295534"]

    assert info.tracking == "BR264343187915J"
    assert info.carrier == logistica_shopee._CARRIER_IDA
    assert info.status == "ACCEPTED"
    assert not [c for c in client.calls if c[0] != "list"]


@pytest.mark.asyncio
async def test_lista_falhando_nao_apaga_caso_vivo_conhecido(shopee_client):
    """get_return_list é best-effort: numa rodada em que ela volta vazia, a
    linha que já conhece um caso VIVO (return_status da Logística) não pode
    virar RTS sintético por cima."""
    client = _FakeShopee(returns=[], details={}, tracking={"260909HMCYJ1DP": _IDA_DEVOLVIDA})
    shopee_client["client"] = client
    linha = _linha("295534", "260909HMCYJ1DP", logistics_status="LOGISTICS_DELIVERY_FAILED")
    linha.meli_status = {**linha.meli_status, "return_status": "ACCEPTED"}

    out = await logistica_shopee.returns_por_pedido(None, [linha])

    assert "295534" not in out
    assert ("track", "260909HMCYJ1DP") not in client.calls


def test_evento_com_update_time_ilegivel_nao_derruba():
    track = {
        "tracking_info": [
            {"update_time": "abc", "logistics_status": "RETURN_STARTED", "description": "Pedido será devolvido ao vendedor"},
            {"update_time": "1789565570.0", "logistics_status": "RETURNED", "description": "Pedido devolvido"},
        ]
    }
    rts = logistica_shopee.rts_da_ida(track)
    assert rts is not None
    assert rts.entregue_em == datetime.fromtimestamp(1789565570, tz=UTC)
    assert rts.localizacao == "Pedido devolvido"


@pytest.mark.asyncio
async def test_sync_nao_regride_a_ultima_mexida_nem_apaga_o_codigo(db: AsyncSession, monkeypatch):
    """Tick 1 grava a volta (evento da SPX 16/09); tick 2 vem sem reconsulta da
    ida (pacote já chegou) e com o update_time velho do caso: o carimbo da
    última mexida não volta pra trás e o código/transportadora ficam."""
    pedido = f"4{uuid4().hex[:6]}"
    db.add(Logistica(pedido_bling=pedido, plataforma="Shopee", pedido_marketplace=f"mk-{pedido}", conta="atv"))
    await db.commit()
    entregue = datetime.fromtimestamp(1789565570, tz=UTC)
    caso_velho = datetime.fromtimestamp(1789444321, tz=UTC)
    tick = {"n": 0}

    async def _shopee(session, linhas, **kw):
        tick["n"] += 1
        if tick["n"] == 1:
            return {pedido: ReturnInfo(
                fonte="shopee", status="ACCEPTED", tracking="BR264343187915J", carrier=logistica_shopee._CARRIER_IDA,
                created_at=caso_velho, updated_at=entregue, return_id="26091402Q30WFKX",
                entregue_em=entregue, localizacao="Pedido devolvido", localizacao_em=entregue,
            )}
        return {pedido: ReturnInfo(
            fonte="shopee", status="ACCEPTED", tracking="BR264343187915J", carrier=logistica_shopee._CARRIER_IDA,
            created_at=caso_velho, updated_at=caso_velho, return_id="26091402Q30WFKX",
        )}

    async def _vazio(session, linhas, **kw):
        return {}

    monkeypatch.setattr(logistica_shopee, "returns_por_pedido", _shopee, raising=False)
    monkeypatch.setattr(logistica_tiktok, "returns_por_pedido", _vazio, raising=False)
    monkeypatch.setattr(logistica_meli, "returns_por_pedido", _vazio, raising=False)

    async def _register(numbers):
        return {"ok": True}

    monkeypatch.setattr(logistica_track, "register", _register)

    await sync_svc.run(db, pedidos=[pedido])
    await sync_svc.run(db, pedidos=[pedido])

    row = (await db.execute(select(DevolucaoRastreio).where(DevolucaoRastreio.pedido_bling == pedido))).scalar_one()
    assert row.devolucao_atualizada_em == entregue
    assert row.rastreio_auto == "BR264343187915J"
    assert row.transportadora_auto == logistica_shopee._CARRIER_IDA
    assert row.localizacao_auto == "Pedido devolvido"
    assert row.pacote_entregue_em == entregue


@pytest.mark.asyncio
async def test_patch_da_aba_mantem_chegou_em(client, db: AsyncSession, make_user, auth_as):
    """Editar a observação de uma linha já devolvida não pode sumir com o
    "Chegou em" na resposta (o PATCH remontava a linha sem pacote_entregue_em)."""
    from uuid import UUID as _UUID

    from sqlalchemy import text

    user = await make_user(permissions={"devolucoes": {"view": True, "edit": True, "delete": False}})
    auth_as(user)
    await db.execute(
        text("INSERT INTO bling_orders (id, numero, situacao) VALUES (:id, '555777', '83957')"),
        {"id": _UUID("aaaaaaaa-0000-0000-0000-000000000777")},
    )
    entregue = datetime.fromtimestamp(1789565570, tz=UTC)
    db.add(DevolucaoRastreio(
        pedido_bling="555777", rastreio_auto="BR264343187915J", transportadora_auto=logistica_shopee._CARRIER_IDA,
        localizacao_auto="Pedido devolvido", localizacao_auto_data=entregue, devolucao_status_auto="ACCEPTED",
        devolucao_id_auto="26091402Q30WFKX", fonte_auto="shopee", pacote_entregue_em=entregue,
    ))
    await db.commit()

    r = await client.patch("/api/devolutions/acompanhamento/555777", json={"observacao": "conferido"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["devolucao_chegou_em"] == "2026-09-16"
    assert body["localizacao"] == "Devolução aceita · Pedido devolvido"
