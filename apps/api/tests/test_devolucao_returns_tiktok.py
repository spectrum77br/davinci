"""devolucao_returns × TikTok — `logistica_tiktok.returns_por_pedido`: o rastreio
do pacote que VOLTA (returns/search por `order_ids`) mapeado no contrato
`ReturnInfo`. Client falso (sem HTTP) + linhas Logistica no banco de teste;
mais o modo `order_ids` do `TikTokClient.get_return_list` (lotes de 50) com o
`_post` dublado."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from app.models import Logistica
from app.services import logistica_tiktok
from app.services.devolucao_returns import ReturnInfo
from app.services.marketplaces.tiktok import TikTokClient

# ---- returns_por_pedido -------------------------------------------------------

ORDER_A = "585673041600415018"  # caso real de 03/09 (rastreio AP418496864BR)
ORDER_B = "585612645547804469"
ORDER_C = "585411441781475242"


class FakeTikTokReturns:
    """Só o que returns_por_pedido usa: `get_return_list(order_ids=[...])`.
    Devolve os casos de `returns` cujo order_id está no lote pedido e registra
    os lotes em `calls`. `fail=True` simula a API caindo."""

    def __init__(self, returns: list[dict], *, fail: bool = False):
        self._returns = returns
        self._fail = fail
        self.calls: list[list[str]] = []

    async def get_return_list(
        self, *, order_ids=None, update_time_from=None, update_time_to=None, page_size=50
    ):
        if self._fail:
            raise RuntimeError("tiktok caiu")
        self.calls.append(list(order_ids or []))
        wanted = {str(o) for o in (order_ids or [])}
        return [r for r in self._returns if str(r.get("order_id")) in wanted]


def _patch(monkeypatch, fakes: dict[str, FakeTikTokReturns]) -> None:
    """Conta com chave em `fakes` "tem integração" (a própria conta faz de
    integração); o builder devolve o fake da conta. Conta fora do dict → None."""

    async def _integ(session, conta):
        return conta if conta in fakes else None

    def _build(session, integ, *, lock=None):
        return fakes[integ]

    monkeypatch.setattr(logistica_tiktok, "_tiktok_integration_for_conta", _integ)
    monkeypatch.setattr(logistica_tiktok, "_build_tiktok_client", _build)


def _row(pedido_bling: str | None, order_id: str | None, *, conta="loja", plataforma="TikTok"):
    return Logistica(
        plataforma=plataforma, conta=conta, pedido_bling=pedido_bling,
        pedido_marketplace=order_id, data=date(2026, 5, 1), status_bling="Em andamento",
    )


async def _seed(db, rows: list[Logistica]) -> list[Logistica]:
    db.add_all(rows)
    await db.commit()
    return list((await db.execute(select(Logistica))).scalars().all())


def _caso(order_id: str, **extra) -> dict:
    base = {
        "order_id": order_id,
        "return_id": f"R-{order_id[-4:]}",
        "return_status": "BUYER_SHIPPED_ITEM",
        "return_type": "RETURN_AND_REFUND",
        "return_tracking_number": "AP418496864BR",
        "return_provider_name": "Correios",
        "create_time": 1756900000,
        "update_time": 1756950000,
        "handover_method": "DROP_OFF",
        "shipment_type": "PLATFORM",
    }
    return {**base, **extra}


@pytest.mark.asyncio
async def test_devolucao_viva_com_rastreio_mapeia_campos(db, monkeypatch):
    linhas = await _seed(db, [_row("291001", ORDER_A)])
    fake = FakeTikTokReturns([_caso(ORDER_A, return_id="R1")])
    _patch(monkeypatch, {"loja": fake})

    out = await logistica_tiktok.returns_por_pedido(db, linhas)

    assert out == {
        "291001": ReturnInfo(
            fonte="tiktok",
            status="BUYER_SHIPPED_ITEM",
            tracking="AP418496864BR",
            carrier="Correios",
            created_at=datetime.fromtimestamp(1756900000, tz=UTC),
            updated_at=datetime.fromtimestamp(1756950000, tz=UTC),
            return_id="R1",
        )
    }
    assert out["291001"].created_at.tzinfo is UTC
    # Buscou por order_ids (sem janela de tempo), um lote pra conta.
    assert fake.calls == [[ORDER_A]]


@pytest.mark.asyncio
async def test_devolucao_sem_rastreio_tracking_none(db, monkeypatch):
    linhas = await _seed(db, [_row("291001", ORDER_A), _row("291002", ORDER_B)])
    fake = FakeTikTokReturns(
        [
            # Só reembolso: sem pacote, mas o caso existe e é reportado.
            _caso(
                ORDER_A, return_type="REFUND", return_status="RETURN_OR_REFUND_REQUEST_PENDING",
                return_tracking_number="", return_provider_name=None,
            ),
            # Comprador ainda não postou: rastreio só espaço em branco.
            _caso(
                ORDER_B, return_status="AWAITING_BUYER_SHIP",
                return_tracking_number="   ", return_provider_name="  ",
            ),
        ]
    )
    _patch(monkeypatch, {"loja": fake})

    out = await logistica_tiktok.returns_por_pedido(db, linhas)

    assert set(out) == {"291001", "291002"}
    assert out["291001"].status == "RETURN_OR_REFUND_REQUEST_PENDING"
    assert out["291001"].tracking is None
    assert out["291001"].carrier is None
    assert out["291002"].status == "AWAITING_BUYER_SHIP"
    assert out["291002"].tracking is None
    assert out["291002"].carrier is None
    assert out["291002"].fonte == "tiktok"


@pytest.mark.asyncio
async def test_varios_casos_vence_o_vivo_mais_recente(db, monkeypatch):
    linhas = await _seed(db, [_row("291001", ORDER_A), _row("291002", ORDER_B)])
    fake = FakeTikTokReturns(
        [
            # ORDER_A: vivo velho, vivo mais novo, cancelado novíssimo → o vivo
            # mais novo (R-novo), não o cancelado apesar de ser o mais recente.
            _caso(
                ORDER_A, return_id="R-velho", return_status="AWAITING_BUYER_SHIP",
                return_tracking_number="", update_time=100,
            ),
            _caso(
                ORDER_A, return_id="R-novo", return_status="BUYER_SHIPPED_ITEM",
                return_tracking_number="AP111BR", update_time=200,
            ),
            _caso(
                ORDER_A, return_id="R-cancel", return_status="RETURN_OR_REFUND_REQUEST_CANCEL",
                return_tracking_number="AP999BR", update_time=300,
            ),
            # ORDER_B: só encerrados → o mais recente (rejeitado, 500 > 400).
            _caso(
                ORDER_B, return_id="R-cancel-b", return_status="RETURN_OR_REFUND_REQUEST_CANCEL",
                return_tracking_number="", update_time=400,
            ),
            _caso(
                ORDER_B, return_id="R-reject-b", return_status="REFUND_OR_RETURN_REQUEST_REJECT",
                return_tracking_number="", update_time="500",  # string: payload cru
            ),
        ]
    )
    _patch(monkeypatch, {"loja": fake})

    out = await logistica_tiktok.returns_por_pedido(db, linhas)

    assert out["291001"].return_id == "R-novo"
    assert out["291001"].status == "BUYER_SHIPPED_ITEM"
    assert out["291001"].tracking == "AP111BR"
    assert out["291001"].updated_at == datetime.fromtimestamp(200, tz=UTC)
    assert out["291002"].return_id == "R-reject-b"
    assert out["291002"].status == "REFUND_OR_RETURN_REQUEST_REJECT"
    assert out["291002"].tracking is None


@pytest.mark.asyncio
async def test_conta_sem_integracao_ou_api_com_erro_pula_sem_levantar(db, monkeypatch):
    linhas = await _seed(
        db,
        [
            _row("291001", ORDER_A, conta="loja"),
            _row("291002", ORDER_B, conta="semint"),  # conta sem integração TikTok
            _row("291003", ORDER_C, conta="quebrada"),  # API cai nessa conta
        ],
    )
    ok = FakeTikTokReturns([_caso(ORDER_A), _caso(ORDER_B), _caso(ORDER_C)])
    quebrada = FakeTikTokReturns([_caso(ORDER_C)], fail=True)
    _patch(monkeypatch, {"loja": ok, "quebrada": quebrada})

    out = await logistica_tiktok.returns_por_pedido(db, linhas)  # não levanta

    assert set(out) == {"291001"}
    assert out["291001"].tracking == "AP418496864BR"
    # Cada conta só pergunta pelos SEUS pedidos.
    assert ok.calls == [[ORDER_A]]


@pytest.mark.asyncio
async def test_pedido_sem_devolucao_fica_fora_e_linhas_alheias_sao_ignoradas(db, monkeypatch):
    linhas = await _seed(
        db,
        [
            _row("291001", ORDER_A),
            _row("291002", ORDER_B),  # sem caso no TikTok
            _row("291003", "SHP-1", plataforma="Shopee"),  # não é TikTok
            _row(None, ORDER_C),  # sem pedido Bling: não tem chave no dict
            _row("291005", None),  # sem pedido de marketplace: nada a buscar
            _row("291006", ORDER_A, plataforma="tik tok"),  # 2ª linha da mesma venda
        ],
    )
    fake = FakeTikTokReturns([_caso(ORDER_A), _caso(ORDER_C), _caso("SHP-1")])
    _patch(monkeypatch, {"loja": fake})

    out = await logistica_tiktok.returns_por_pedido(db, linhas)

    assert set(out) == {"291001", "291006"}
    assert out["291001"] == out["291006"]
    assert "291002" not in out  # ausente = desconhecido
    # Só os pedidos TikTok com pedido Bling + marketplace foram ao
    # returns/search (um lote pra conta): ORDER_B mesmo sem caso; ORDER_C não
    # (sem chave Bling), nem o da Shopee.
    assert len(fake.calls) == 1
    assert sorted(fake.calls[0]) == sorted([ORDER_A, ORDER_B])


@pytest.mark.asyncio
async def test_lista_vazia_nao_consulta_nada(db, monkeypatch):
    fake = FakeTikTokReturns([_caso(ORDER_A)])
    _patch(monkeypatch, {"loja": fake})
    assert await logistica_tiktok.returns_por_pedido(db, []) == {}
    assert fake.calls == []


# ---- TikTokClient.get_return_list(order_ids=...) -------------------------------


def _client() -> TikTokClient:
    return TikTokClient(
        {
            "app_key": "k", "app_secret": "s", "access_token": "t",
            "shop_cipher": "c", "token_expires_at": 4102444800,  # 2100: não renova
        }
    )


@pytest.mark.asyncio
async def test_client_order_ids_em_lotes_de_50_sem_filtro_de_tempo(monkeypatch):
    client = _client()
    chamadas: list[tuple[str, dict, dict]] = []

    async def _post(path, body=None, extra_params=None, **kw):
        chamadas.append((path, dict(body or {}), dict(extra_params or {})))
        ids = body["order_ids"]
        return {
            "code": 0,
            "data": {"return_orders": [{"order_id": ids[0], "return_id": f"R{ids[0]}"}]},
        }

    monkeypatch.setattr(client, "_post", _post)
    ids = [str(600000000000000000 + i) for i in range(120)]

    out = await client.get_return_list(order_ids=ids)

    assert [len(b["order_ids"]) for _, b, _ in chamadas] == [50, 50, 20]
    assert [b["order_ids"] for _, b, _ in chamadas] == [ids[:50], ids[50:100], ids[100:]]
    assert all(set(b) == {"order_ids"} for _, b, _ in chamadas)  # sem update_time_*
    assert all(p == "/return_refund/202309/returns/search" for p, _, _ in chamadas)
    assert all(x == {"page_size": "50"} for _, _, x in chamadas)
    assert [r["order_id"] for r in out] == [ids[0], ids[50], ids[100]]


@pytest.mark.asyncio
async def test_client_order_ids_limpa_vazios_duplicados_e_pagina(monkeypatch):
    client = _client()
    chamadas: list[tuple[dict, dict]] = []

    async def _post(path, body=None, extra_params=None, **kw):
        chamadas.append((dict(body or {}), dict(extra_params or {})))
        if (extra_params or {}).get("page_token"):
            return {"code": 0, "data": {"return_orders": [{"order_id": "2"}]}}
        return {
            "code": 0,
            "data": {"return_orders": [{"order_id": "1"}], "next_page_token": "p2"},
        }

    monkeypatch.setattr(client, "_post", _post)

    out = await client.get_return_list(order_ids=[" 1 ", "", None, "2", "1"])

    assert [b for b, _ in chamadas] == [{"order_ids": ["1", "2"]}] * 2
    assert [x.get("page_token") for _, x in chamadas] == [None, "p2"]
    assert [r["order_id"] for r in out] == ["1", "2"]

    # Lista sem id útil: nem chama a API.
    chamadas.clear()
    assert await client.get_return_list(order_ids=["", None]) == []
    assert chamadas == []


@pytest.mark.asyncio
async def test_client_janela_de_tempo_continua_igual(monkeypatch):
    client = _client()
    chamadas: list[dict] = []

    async def _post(path, body=None, extra_params=None, **kw):
        chamadas.append(dict(body or {}))
        return {"code": 0, "data": {"return_orders": []}}

    monkeypatch.setattr(client, "_post", _post)

    assert await client.get_return_list(update_time_from=100, update_time_to=200) == []
    assert chamadas == [{"update_time_ge": 100, "update_time_lt": 200}]

    with pytest.raises(ValueError):
        await client.get_return_list()
