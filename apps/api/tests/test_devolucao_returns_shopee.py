"""logistica_shopee.returns_por_pedido — a devolução (o pacote que VOLTA) de
cada linha Shopee via v2.returns.get_return_list, varrida em fatias de 15 dias
por create_time. Client falso (sem HTTP) que filtra por janela como a Shopee,
pra travar o mapeamento pro ReturnInfo, a escolha do caso vivo mais recente, a
varredura até o pedido mais velho e o best-effort por conta."""

from __future__ import annotations

import time
from datetime import UTC, date, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import Logistica
from app.services import logistica_shopee
from app.services.devolucao_returns import ReturnInfo, epoch_to_dt
from app.services.marketplaces.shopee import ShopeeClient

_DIA = 24 * 3600


class FakeShopeeReturns:
    """Só o get_return_list. Filtra por create_time como a Shopee, e registra
    as janelas pedidas em `calls` (de, ate)."""

    def __init__(self, returns: list[dict], *, erro: Exception | None = None):
        self.returns = returns
        self.calls: list[tuple[int, int]] = []
        self._erro = erro

    async def get_return_list(
        self,
        *,
        update_time_from=None,
        update_time_to=None,
        create_time_from=None,
        create_time_to=None,
        page_size=100,
    ):
        assert create_time_from is not None and create_time_to is not None
        self.calls.append((create_time_from, create_time_to))
        if self._erro is not None:
            raise self._erro
        return [
            d for d in self.returns
            if create_time_from <= int(d.get("create_time") or 0) <= create_time_to
        ]


def _patch(monkeypatch, fakes: dict[str, object | None]) -> None:
    """`fakes` = {conta: client falso | None (conta sem integração)}."""

    async def _integ(session, conta):
        if fakes.get(conta) is None:
            return None
        return SimpleNamespace(name=conta)

    def _build(session, integ, *, lock=None):
        fake = fakes[integ.name]
        if isinstance(fake, Exception):
            raise fake
        return fake

    monkeypatch.setattr(logistica_shopee, "_shopee_integration_for_conta", _integ)
    monkeypatch.setattr(logistica_shopee, "_build_shopee_client", _build)


def _row(pedido_bling, sn, *, conta="loja", data=None, plataforma="Shopee") -> Logistica:
    # Pedido de 10 dias atrás por padrão: a varredura começa na `data` menos 2
    # dias, e os returns do `_ret` nascem 3 dias atrás (depois do pedido).
    return Logistica(
        plataforma=plataforma, conta=conta, pedido_bling=pedido_bling,
        pedido_marketplace=sn, data=data or (date.today() - timedelta(days=10)),
    )


def _ret(sn, rsn, status, *, tracking="", create=None, update=None, **extra) -> dict:
    agora = int(time.time())
    return {
        "order_sn": sn, "return_sn": rsn, "status": status,
        "tracking_number": tracking, "needs_logistics": bool(tracking),
        "create_time": agora - 3 * _DIA if create is None else create,
        "update_time": agora - _DIA if update is None else update,
        **extra,
    }


# ── (1) devolução viva com rastreio → ReturnInfo ────────────────────────


@pytest.mark.asyncio
async def test_devolucao_viva_com_rastreio_mapeia_campos(db, monkeypatch):
    db.add_all([_row("291000", "2508ABC"), _row("291001", "2508DEF")])
    await db.commit()
    linhas = list((await db.execute(select(Logistica))).scalars().all())

    agora = int(time.time())
    fake = FakeShopeeReturns([
        _ret(
            "2508ABC", "RS001", "PROCESSING", tracking="BR123456789BR",
            create=agora - 3 * _DIA, update=agora - _DIA,
            return_ship_due_date=agora + 5 * _DIA, is_arrived_at_warehouse=False,
        ),
    ])
    _patch(monkeypatch, {"loja": fake})

    out = await logistica_shopee.returns_por_pedido(db, linhas)

    assert out == {
        "291000": ReturnInfo(
            fonte="shopee", status="PROCESSING", tracking="BR123456789BR",
            carrier=None,
            created_at=epoch_to_dt(agora - 3 * _DIA),
            updated_at=epoch_to_dt(agora - _DIA),
            return_id="RS001",
        )
    }
    assert out["291000"].created_at.tzinfo is UTC
    # Pedido sem devolução (291001) fica de fora, não vira None.
    assert "291001" not in out


# ── (2) devolução sem rastreio → tracking None ──────────────────────────


@pytest.mark.asyncio
async def test_devolucao_sem_rastreio_vira_none(monkeypatch):
    fake = FakeShopeeReturns([
        # Só reembolso: needs_logistics=false, tracking_number vazio.
        _ret("A1", "RS-A", "REFUND_PAID", tracking=""),
        # Aberta mas ainda não postada: tracking em branco (espaços).
        {**_ret("B2", "RS-B", "REQUESTED"), "tracking_number": "   ", "needs_logistics": True},
        # Payload sem as chaves de rastreio/datas nem quebra.
        {"order_sn": "C3", "return_sn": "RS-C", "status": "JUDGING",
         "create_time": int(time.time()) - _DIA},
    ])
    _patch(monkeypatch, {"loja": fake})
    linhas = [_row("1", "A1"), _row("2", "B2"), _row("3", "C3")]

    out = await logistica_shopee.returns_por_pedido(None, linhas)

    assert set(out) == {"1", "2", "3"}
    assert all(info.tracking is None for info in out.values())
    assert out["1"].status == "REFUND_PAID"
    assert out["3"].updated_at is None
    assert out["3"].created_at is not None
    assert out["3"].fonte == "shopee"


# ── (3) vários casos → vivo mais recente vence ──────────────────────────


@pytest.mark.asyncio
async def test_varios_casos_vivo_mais_recente_vence(monkeypatch):
    agora = int(time.time())
    fake = FakeShopeeReturns([
        # Pedido X: o CANCELLED é o mais recente de todos, mas está encerrado —
        # vale o vivo mais recente (ACCEPTED), não o REQUESTED mais velho.
        _ret("X", "RS-X-cancel", "CANCELLED", tracking="BRCANCEL",
             create=agora - 2 * _DIA, update=agora - 1 * _DIA),
        _ret("X", "RS-X-ok", "ACCEPTED", tracking="BROK",
             create=agora - 6 * _DIA, update=agora - 3 * _DIA),
        _ret("X", "RS-X-old", "REQUESTED", tracking="BROLD",
             create=agora - 9 * _DIA, update=agora - 8 * _DIA),
        # Pedido Y: só encerrados → o mais recente (CLOSED).
        _ret("Y", "RS-Y-1", "CANCELLED", create=agora - 5 * _DIA, update=agora - 4 * _DIA),
        _ret("Y", "RS-Y-2", "CLOSED", tracking="BRY2",
             create=agora - 3 * _DIA, update=agora - 2 * _DIA),
    ])
    _patch(monkeypatch, {"loja": fake})

    out = await logistica_shopee.returns_por_pedido(None, [_row("10", "X"), _row("11", "Y")])

    assert (out["10"].return_id, out["10"].status, out["10"].tracking) == (
        "RS-X-ok", "ACCEPTED", "BROK"
    )
    assert (out["11"].return_id, out["11"].status, out["11"].tracking) == (
        "RS-Y-2", "CLOSED", "BRY2"
    )


# ── (4) conta sem integração / erro de API → pula, não levanta ──────────


@pytest.mark.asyncio
async def test_conta_sem_integracao_ou_erro_api_pula(monkeypatch):
    ok = FakeShopeeReturns([_ret("OK1", "RS-OK", "PROCESSING", tracking="BROK1")])
    quebra = FakeShopeeReturns([_ret("Q1", "RS-Q", "PROCESSING", tracking="BRQ")],
                               erro=RuntimeError("shopee 500"))
    _patch(monkeypatch, {
        "ok": ok,
        "quebra": quebra,
        "semint": None,                       # conta sem integração Shopee
        "semcred": RuntimeError("decrypt"),   # _build_shopee_client estoura
    })
    linhas = [
        _row("1", "OK1", conta="ok"),
        _row("2", "Q1", conta="quebra"),
        _row("3", "S1", conta="semint"),
        _row("4", "C1", conta="semcred"),
    ]

    out = await logistica_shopee.returns_por_pedido(None, linhas)

    assert set(out) == {"1"}
    assert out["1"].tracking == "BROK1"
    # A conta quebrada foi tentada uma vez e desistiu (não fica em loop).
    assert len(quebra.calls) == 1


# ── (5) pedido sem devolução fica fora; linhas fora do escopo ignoradas ──


@pytest.mark.asyncio
async def test_pedido_sem_devolucao_fica_fora_e_ignora_linhas_fora_do_escopo(monkeypatch):
    fake = FakeShopeeReturns([_ret("COM", "RS-1", "ACCEPTED", tracking="BR1")])
    _patch(monkeypatch, {"loja": fake})
    linhas = [
        _row("100", "COM"),
        _row("101", "SEM"),                                   # sem devolução
        _row("102", "COM", plataforma="Mercado Livre"),       # não é Shopee
        _row(None, "COM"),                                    # sem pedido_bling
        _row("104", None),                                    # sem pedido_marketplace
    ]

    out = await logistica_shopee.returns_por_pedido(None, linhas)

    assert out == {
        "100": ReturnInfo(
            fonte="shopee", status="ACCEPTED", tracking="BR1", carrier=None,
            created_at=out["100"].created_at, updated_at=out["100"].updated_at,
            return_id="RS-1",
        )
    }


@pytest.mark.asyncio
async def test_sem_linhas_shopee_nao_consulta_nada(monkeypatch):
    fake = FakeShopeeReturns([_ret("Z", "RS", "ACCEPTED")])
    _patch(monkeypatch, {"loja": fake})
    out = await logistica_shopee.returns_por_pedido(None, [_row("1", "Z", plataforma="TikTok")])
    assert out == {}
    assert fake.calls == []


# ── varredura por create_time até o pedido mais velho (teto 120 dias) ───


@pytest.mark.asyncio
async def test_varre_fatias_de_15_dias_ate_o_pedido_mais_velho(monkeypatch):
    agora = int(time.time())
    hoje = date.today()
    # Devolução aberta há 40 dias (fora da janela padrão de 15 dias da API).
    fake = FakeShopeeReturns([
        _ret("VELHO", "RS-V", "ACCEPTED", tracking="BRVELHO",
             create=agora - 40 * _DIA, update=agora - 39 * _DIA),
        _ret("NOVO", "RS-N", "PROCESSING", tracking="BRNOVO",
             create=agora - 2 * _DIA, update=agora - _DIA),
    ])
    _patch(monkeypatch, {"loja": fake})
    linhas = [
        _row("1", "VELHO", data=hoje - timedelta(days=40)),
        _row("2", "NOVO", data=hoje),
    ]

    out = await logistica_shopee.returns_por_pedido(None, linhas)

    assert out["1"].tracking == "BRVELHO"
    assert out["2"].tracking == "BRNOVO"
    # Fatias de no máximo 15 dias, encostadas, da mais recente pra trás.
    assert fake.calls
    assert fake.calls[0][1] >= agora
    for de, ate in fake.calls:
        assert 0 < ate - de <= 15 * _DIA
    for (de_prev, _), (_, ate_next) in zip(fake.calls, fake.calls[1:], strict=False):
        assert ate_next == de_prev
    # Chegou até a data do pedido mais velho menos a folga de 2 dias.
    mais_velho = fake.calls[-1][0]
    assert mais_velho <= agora - 42 * _DIA
    assert mais_velho >= agora - 44 * _DIA


@pytest.mark.asyncio
async def test_teto_de_120_dias_para_pedidos_muito_antigos(monkeypatch):
    agora = int(time.time())
    hoje = date.today()
    fake = FakeShopeeReturns([
        # Aberta há 150 dias: além do teto, não é achada (fica "desconhecida").
        _ret("ANTIGO", "RS-A", "ACCEPTED", tracking="BRA",
             create=agora - 150 * _DIA, update=agora - 149 * _DIA),
        # Aberta há 100 dias: dentro do teto.
        _ret("MEIO", "RS-M", "ACCEPTED", tracking="BRM",
             create=agora - 100 * _DIA, update=agora - 99 * _DIA),
    ])
    _patch(monkeypatch, {"loja": fake})
    linhas = [
        _row("1", "ANTIGO", data=hoje - timedelta(days=300)),
        _row("2", "MEIO", data=hoje - timedelta(days=100)),
        _row("3", "SEMDATA", data=None),
    ]
    # `data=None` no _row cai em hoje; força None de verdade na 3ª linha.
    linhas[2].data = None

    out = await logistica_shopee.returns_por_pedido(None, linhas)

    assert "1" not in out
    assert out["2"].tracking == "BRM"
    mais_velho = min(de for de, _ in fake.calls)
    assert agora - 120 * _DIA - 60 <= mais_velho <= agora - 120 * _DIA + 60
    # 8 fatias de (15d - 300s) + a sobra de 40 min até o teto.
    assert len(fake.calls) == 9


# ── client: get_return_list aceita create_time_* e mantém update_time_* ─


class _Resp:
    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


@pytest.mark.asyncio
async def test_client_get_return_list_aceita_create_time_e_update_time(monkeypatch):
    client = ShopeeClient({"shop_id": 1, "access_token": "t", "expires_at": 2**31})
    pedidos: list[dict] = []

    async def _request(method, path, *, params=None, json=None):
        pedidos.append(dict(params or {}))
        return _Resp({"error": "", "response": {"more": False, "return": [
            {"order_sn": "S1", "return_sn": "R1", "status": "REQUESTED"},
        ]}})

    monkeypatch.setattr(client, "_request", _request)

    out = await client.get_return_list(create_time_from=100, create_time_to=200)
    assert out == [{"order_sn": "S1", "return_sn": "R1", "status": "REQUESTED"}]
    assert pedidos[-1] == {
        "page_no": 0, "page_size": 100, "create_time_from": 100, "create_time_to": 200,
    }

    # Assinatura antiga (sweep_pos_venda) continua igual.
    await client.get_return_list(update_time_from=10, update_time_to=20)
    assert pedidos[-1] == {
        "page_no": 0, "page_size": 100, "update_time_from": 10, "update_time_to": 20,
    }

    with pytest.raises(ValueError):
        await client.get_return_list()
