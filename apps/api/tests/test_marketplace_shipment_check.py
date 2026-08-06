"""COALESCE no UPDATE de `marketplace_shipment_check`.

Cobre o fix do segundo caminho de drift de data: o serviço sempre promove
`situacao` pra 15 (Atendido), mas só carimba `em_andamento_data` quando
ainda está NULL — nunca sobrescreve uma data já correta (ex.: Shopee/ML
reportando D+1/D+2 por delay de processamento, que antes re-carimbava).

Testa o SQL exato do bloco "Local stamp" (linhas ~258-268).
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder

_SHIPPED_SITUACAO = "15"


async def _make_order(
    db: AsyncSession, *, bling_id: int, situacao: str,
    em_andamento_data: date | None,
) -> BlingOrder:
    o = BlingOrder(
        bling_id=bling_id,
        numero=str(bling_id),
        item_codigo="sku-test",
        item_index=0,
        situacao=situacao,
        em_andamento_data=em_andamento_data,
    )
    db.add(o)
    await db.commit()
    await db.refresh(o)
    return o


def _stamp_update(bling_id: int, ship_date: date):
    """SQL idêntico ao que o serviço roda (bloco Local stamp)."""
    return (
        update(BlingOrder)
        .where(BlingOrder.bling_id == bling_id)
        .values(
            em_andamento_data=func.coalesce(
                BlingOrder.em_andamento_data, ship_date,
            ),
            situacao=_SHIPPED_SITUACAO,
        )
    )


@pytest.mark.asyncio
async def test_preserva_em_andamento_data_ja_preenchida(db: AsyncSession):
    """Marketplace reporta D+1 mas o pedido já tem ship-date correto:
    preserva data, mas atualiza situacao."""
    o = await _make_order(
        db, bling_id=900001, situacao="83965",
        em_andamento_data=date(2026, 5, 30),
    )
    # Marketplace reporta um dia depois (D+1)
    await db.execute(_stamp_update(900001, date(2026, 6, 1)))
    await db.commit()
    await db.refresh(o)
    assert o.em_andamento_data == date(2026, 5, 30)  # preservada
    assert o.situacao == "15"  # promovida


@pytest.mark.asyncio
async def test_carimba_quando_em_andamento_data_nula(db: AsyncSession):
    """Pedido sem ship-date (situacao=6 ou 83965 sem data): carimba com
    a data real do marketplace E promove situacao."""
    o = await _make_order(
        db, bling_id=900002, situacao="6",
        em_andamento_data=None,
    )
    await db.execute(_stamp_update(900002, date(2026, 5, 30)))
    await db.commit()
    await db.refresh(o)
    assert o.em_andamento_data == date(2026, 5, 30)  # carimbada
    assert o.situacao == "15"


@pytest.mark.asyncio
async def test_carimba_com_fallback_quando_marketplace_sem_data(db: AsyncSession):
    """Quando marketplace não devolve real_ship_date, o caller passa
    _operational_ship_date(now()) como ship_date. COALESCE preserva
    em_andamento existente do mesmo jeito."""
    fallback = date(2026, 6, 1)
    # cenário (a): data já preenchida — fallback é descartado
    o_a = await _make_order(
        db, bling_id=900003, situacao="83965",
        em_andamento_data=date(2026, 5, 28),
    )
    await db.execute(_stamp_update(900003, fallback))
    await db.commit()
    await db.refresh(o_a)
    assert o_a.em_andamento_data == date(2026, 5, 28)
    assert o_a.situacao == "15"

    # cenário (b): data NULL — fallback aplica
    o_b = await _make_order(
        db, bling_id=900004, situacao="83965",
        em_andamento_data=None,
    )
    await db.execute(_stamp_update(900004, fallback))
    await db.commit()
    await db.refresh(o_b)
    assert o_b.em_andamento_data == fallback
    assert o_b.situacao == "15"


@pytest.mark.asyncio
async def test_situacao_promovida_mesmo_sem_mudanca_de_data(db: AsyncSession):
    """Garantia explícita: o serviço NUNCA deixa de promover situacao=15
    por causa do COALESCE. Filtrar `em_andamento.is_(None)` no WHERE
    teria quebrado esse caso — por isso usa COALESCE."""
    o = await _make_order(
        db, bling_id=900005, situacao="83965",
        em_andamento_data=date(2026, 5, 25),
    )
    await db.execute(_stamp_update(900005, date(2026, 6, 1)))
    await db.commit()
    await db.refresh(o)
    assert o.situacao == "15"
    assert o.em_andamento_data == date(2026, 5, 25)


# ─── _load_candidates: critério de seleção ───────────────────────────


async def _make_candidate(
    db: AsyncSession,
    *,
    bling_id: int,
    situacao: str,
    em_andamento_data: date | None,
    numeroloja: str = "MP-1",
    loja: str = "100",
) -> BlingOrder:
    """Candidato precisa de numeroloja + loja preenchidos pra entrar
    no filtro do _load_candidates."""
    o = BlingOrder(
        bling_id=bling_id,
        numero=str(bling_id),
        numeroloja=numeroloja,
        loja=loja,
        item_codigo="sku-test",
        item_index=0,
        situacao=situacao,
        em_andamento_data=em_andamento_data,
    )
    db.add(o)
    await db.commit()
    await db.refresh(o)
    return o


@pytest.mark.asyncio
async def test_load_candidates_inclui_83965_com_data(db: AsyncSession):
    """Regressão guard: pedido em 83965 COM em_andamento_data deve
    entrar como candidato. Antes o filtro `IS NULL` o bloqueava,
    e o fix e081e0d carimba data já em 83965 — então TODOS os
    novos pedidos em 83965 tinham data e nenhum entrava."""
    from app.services.marketplace_shipment_check import _load_candidates

    await _make_candidate(
        db, bling_id=901001, situacao="83965",
        em_andamento_data=date(2026, 6, 2),
    )
    rows = await _load_candidates(db)
    assert any(r.bling_id == 901001 for r in rows)


@pytest.mark.asyncio
async def test_load_candidates_inclui_83965_sem_data(db: AsyncSession):
    """Pedidos antigos (pré-e081e0d) sem data continuam entrando."""
    from app.services.marketplace_shipment_check import _load_candidates

    await _make_candidate(
        db, bling_id=901002, situacao="83965", em_andamento_data=None,
    )
    rows = await _load_candidates(db)
    assert any(r.bling_id == 901002 for r in rows)


@pytest.mark.asyncio
async def test_load_candidates_inclui_6_em_aberto(db: AsyncSession):
    """Situação 6 (Em aberto) é destinada a virar 83965 + 15 — entra."""
    from app.services.marketplace_shipment_check import _load_candidates

    await _make_candidate(
        db, bling_id=901003, situacao="6", em_andamento_data=None,
    )
    rows = await _load_candidates(db)
    assert any(r.bling_id == 901003 for r in rows)


@pytest.mark.asyncio
async def test_load_candidates_exclui_situacao_15(db: AsyncSession):
    """Pedido já promovido a 15 NÃO precisa de nova checagem."""
    from app.services.marketplace_shipment_check import _load_candidates

    await _make_candidate(
        db, bling_id=901004, situacao="15",
        em_andamento_data=date(2026, 6, 2),
    )
    rows = await _load_candidates(db)
    assert all(r.bling_id != 901004 for r in rows)


@pytest.mark.asyncio
async def test_load_candidates_exclui_83953_entregue(db: AsyncSession):
    """83953 (Entregue) já passou pelo fluxo — excluído."""
    from app.services.marketplace_shipment_check import _load_candidates

    await _make_candidate(
        db, bling_id=901005, situacao="83953",
        em_andamento_data=date(2026, 6, 2),
    )
    rows = await _load_candidates(db)
    assert all(r.bling_id != 901005 for r in rows)


@pytest.mark.asyncio
async def test_load_candidates_exclui_fora_da_janela(db: AsyncSession):
    """`created_at` muito antigo (fora de _CANDIDATE_WINDOW) é excluído.
    Cria sem `created_at` (server_default=now()), depois UPDATE manual
    pra empurrar pra 1 ano atrás — fora de qualquer janela razoável."""
    from datetime import UTC, datetime, timedelta

    from app.services.marketplace_shipment_check import _load_candidates

    o = await _make_candidate(
        db, bling_id=901006, situacao="83965", em_andamento_data=None,
    )
    old_ts = datetime.now(UTC) - timedelta(days=365)
    await db.execute(
        update(BlingOrder).where(BlingOrder.bling_id == 901006).values(created_at=old_ts)
    )
    await db.commit()
    await db.refresh(o)
    rows = await _load_candidates(db)
    assert all(r.bling_id != 901006 for r in rows)


# ─── _operational_ship_date: sem cutoff ──────────────────────────────


def test_operational_ship_date_meia_manha_brt_mantem_dia():
    """12:30 UTC = 9:30 BRT do dia 03/06. Antes o cutoff de 10h jogava
    pra 02/06. Agora retorna 03/06 — dia em que a etiqueta foi gerada
    (regressão guard do bug reproduzido com 91 pedidos)."""
    from datetime import UTC, datetime

    from app.services.marketplace_shipment_check import _operational_ship_date

    dt = datetime(2026, 6, 3, 12, 30, tzinfo=UTC)
    assert _operational_ship_date(dt) == date(2026, 6, 3)


def test_operational_ship_date_madrugada_brt_mantem_dia():
    """5:00 UTC = 2:00 BRT do dia 03/06. Antes voltava pra 02/06."""
    from datetime import UTC, datetime

    from app.services.marketplace_shipment_check import _operational_ship_date

    dt = datetime(2026, 6, 3, 5, 0, tzinfo=UTC)
    assert _operational_ship_date(dt) == date(2026, 6, 3)


def test_operational_ship_date_noite_brt_dia_anterior():
    """2:30 UTC do dia 03/06 = 23:30 BRT do dia 02/06. Naturalmente
    é 02/06 BRT — confirma que conversão de fuso (não cutoff) preserva
    a semântica de noite virando dia anterior por convenção de tz."""
    from datetime import UTC, datetime

    from app.services.marketplace_shipment_check import _operational_ship_date

    dt = datetime(2026, 6, 3, 2, 30, tzinfo=UTC)
    assert _operational_ship_date(dt) == date(2026, 6, 2)


def test_operational_ship_date_apos_10h_brt_inalterado():
    """13:30 UTC = 10:30 BRT do dia 03/06. Caso que sempre funcionou —
    garantia de que a remoção do cutoff não regrediu eventos > 10h."""
    from datetime import UTC, datetime

    from app.services.marketplace_shipment_check import _operational_ship_date

    dt = datetime(2026, 6, 3, 13, 30, tzinfo=UTC)
    assert _operational_ship_date(dt) == date(2026, 6, 3)


# ---------------------------------------------------------------------------
# ML pack (compra de carrinho): numeroloja é PACK id, /orders/{pack} dá 404.
# O sweep resolve via /packs/{id} e re-checa com o pedido real. Em 06/08
# eram 12/18 pedidos ML "não enviados" do dia presos pra sempre nesse 404.
# ---------------------------------------------------------------------------

_PACK_ID = "2000014000000001"
_REAL_ID = "2000017000000009"


def _http_404() -> "httpx.HTTPStatusError":
    import httpx

    req = httpx.Request("GET", "https://api.mercadolibre.com/orders/x")
    return httpx.HTTPStatusError(
        "404", request=req, response=httpx.Response(404, request=req)
    )


class _FakeMLPackClient:
    """404 no pack id; resolve /packs; pedido real vem shipped."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    async def get_order(self, order_id: str) -> dict:
        self.calls.append(("order", order_id))
        if order_id == _PACK_ID:
            raise _http_404()
        return {
            "status": "paid",
            "shipping": {"status": "shipped", "id": 47679786004},
            "last_updated": "2026-08-05T14:00:00.000-03:00",
        }

    async def get_pack(self, pack_id: str) -> dict:
        self.calls.append(("pack", pack_id))
        return {"id": pack_id, "orders": [{"id": int(_REAL_ID)}]}


class _FakeMLNotMineClient:
    """404 tanto em /orders quanto em /packs — pedido de outra conta."""

    async def get_order(self, order_id: str) -> dict:
        raise _http_404()

    async def get_pack(self, pack_id: str) -> dict:
        raise _http_404()


def _fake_order(numeroloja: str, bling_id: int = 999):
    from types import SimpleNamespace

    return SimpleNamespace(numeroloja=numeroloja, bling_id=bling_id)


@pytest.mark.asyncio
async def test_ml_pack_id_resolve_e_confirma_envio():
    from app.services import marketplace_shipment_check as m

    m._ml_pack_real_order.clear()
    m._not_found_until.clear()
    client = _FakeMLPackClient()
    res = await m._ml_shipped_for(client, _fake_order(_PACK_ID))
    assert res == (999, date(2026, 8, 5))
    # Mapeamento pack→pedido real ficou em cache…
    assert m._ml_pack_real_order[_PACK_ID] == _REAL_ID
    # …e o pedido NÃO entrou em backoff de 404.
    assert not m._skip_not_found(f"ml:{_PACK_ID}")
    # Segunda rodada usa o cache: nada de /packs de novo.
    client.calls.clear()
    res2 = await m._ml_shipped_for(client, _fake_order(_PACK_ID))
    assert res2 == (999, date(2026, 8, 5))
    assert client.calls == [("order", _REAL_ID)]


@pytest.mark.asyncio
async def test_ml_404_sem_pack_entra_em_backoff():
    from app.services import marketplace_shipment_check as m

    m._ml_pack_real_order.clear()
    m._not_found_until.clear()
    res = await m._ml_shipped_for(_FakeMLNotMineClient(), _fake_order("123456"))
    assert res is None
    assert m._skip_not_found("ml:123456")


# ---------------------------------------------------------------------------
# Horário de corte ("despachar até") — captura do prazo de despacho que cada
# marketplace promete ao comprador, feita de carona nas MESMAS consultas do
# sweep. Persistido em bling_orders.marketplace_ship_deadline (migration
# 0210) e mostrado na aba Pedidos do Controle de Estoque.
# ---------------------------------------------------------------------------

def _fake_pending_order(numeroloja: str, bling_id: int = 999):
    """Pedido fake AINDA sem prazo capturado (deadline NULL no banco)."""
    from types import SimpleNamespace

    return SimpleNamespace(
        numeroloja=numeroloja, bling_id=bling_id,
        marketplace_ship_deadline=None,
    )


def test_epoch_to_utc_dt():
    from datetime import UTC, datetime

    from app.services.marketplace_shipment_check import _epoch_to_utc_dt

    # Shopee ship_by_date / TikTok rts_sla_time: epoch segundos (UTC).
    assert _epoch_to_utc_dt(86400) == datetime(1970, 1, 2, tzinfo=UTC)
    assert _epoch_to_utc_dt("86400") == datetime(1970, 1, 2, tzinfo=UTC)
    # 0 = "sem prazo" (alguns marketplaces mandam 0 em vez de null).
    assert _epoch_to_utc_dt(0) is None
    assert _epoch_to_utc_dt(None) is None
    assert _epoch_to_utc_dt("") is None
    assert _epoch_to_utc_dt("abc") is None


def test_iso_to_utc_dt():
    from datetime import UTC, datetime

    from app.services.marketplace_shipment_check import _iso_to_utc_dt

    # ML /sla expected_date vem com offset explícito (ex.: -04:00) —
    # o que importa aqui é só a normalização pra UTC tz-aware.
    assert _iso_to_utc_dt("2026-08-06T13:00:00.000-04:00") == datetime(
        2026, 8, 6, 17, 0, tzinfo=UTC,
    )
    # Amazon LatestShipDate vem com Z.
    assert _iso_to_utc_dt("2026-08-07T02:59:59Z") == datetime(
        2026, 8, 7, 2, 59, 59, tzinfo=UTC,
    )
    # Naive → assume UTC; lixo → None.
    assert _iso_to_utc_dt("2026-08-06T13:00:00") == datetime(
        2026, 8, 6, 13, 0, tzinfo=UTC,
    )
    assert _iso_to_utc_dt(None) is None
    assert _iso_to_utc_dt("n/a") is None


class _FakeMLPendingClient:
    """Pedido pago mas NÃO enviado (substatus printed) — com /sla."""

    def __init__(self, expected_date: str | None = "2026-08-06T13:00:00.000-04:00"):
        self.expected_date = expected_date
        self.sla_calls = 0

    async def get_order(self, order_id: str) -> dict:
        return {
            "status": "paid",
            "shipping": {"status": "to_be_agreed", "id": 44444},
        }

    async def get_shipment(self, shipment_id: str) -> dict:
        return {"status": "ready_to_ship", "substatus": "printed"}

    async def get_shipment_sla(self, shipment_id: str) -> dict:
        self.sla_calls += 1
        if self.expected_date is None:
            raise RuntimeError("sla indisponível")
        return {"expected_date": self.expected_date}


@pytest.mark.asyncio
async def test_ml_nao_enviado_captura_sla():
    from datetime import UTC, datetime

    from app.services import marketplace_shipment_check as m

    m._ml_pack_real_order.clear()
    m._not_found_until.clear()
    client = _FakeMLPendingClient()
    deadlines: dict[int, datetime] = {}
    res = await m._ml_shipped_for(client, _fake_pending_order("111"), deadlines)
    assert res is None  # continua não-enviado
    assert deadlines == {999: datetime(2026, 8, 6, 17, 0, tzinfo=UTC)}
    assert client.sla_calls == 1


@pytest.mark.asyncio
async def test_ml_sla_pulado_quando_ja_tem_prazo_no_banco():
    """Prazo já capturado → NÃO gasta o request extra de /sla de novo."""
    from types import SimpleNamespace
    from datetime import UTC, datetime

    from app.services import marketplace_shipment_check as m

    m._ml_pack_real_order.clear()
    m._not_found_until.clear()
    client = _FakeMLPendingClient()
    o = SimpleNamespace(
        numeroloja="111", bling_id=999,
        marketplace_ship_deadline=datetime(2026, 8, 6, 17, 0, tzinfo=UTC),
    )
    deadlines: dict[int, datetime] = {}
    res = await m._ml_shipped_for(client, o, deadlines)
    assert res is None
    assert deadlines == {}
    assert client.sla_calls == 0


@pytest.mark.asyncio
async def test_ml_sla_falha_nao_derruba_sweep():
    from datetime import datetime

    from app.services import marketplace_shipment_check as m

    m._ml_pack_real_order.clear()
    m._not_found_until.clear()
    client = _FakeMLPendingClient(expected_date=None)  # /sla explode
    deadlines: dict[int, datetime] = {}
    res = await m._ml_shipped_for(client, _fake_pending_order("111"), deadlines)
    assert res is None
    assert deadlines == {}


@pytest.mark.asyncio
async def test_ml_sem_deadlines_mantem_comportamento_antigo():
    """Chamada legada (sem dict) não toca /sla nem exige o atributo novo."""
    from app.services import marketplace_shipment_check as m

    m._ml_pack_real_order.clear()
    m._not_found_until.clear()
    client = _FakeMLPendingClient()
    res = await m._ml_shipped_for(client, _fake_order("111"))
    assert res is None
    assert client.sla_calls == 0


class _FakeAmazonClient:
    def __init__(self, payload: dict | None):
        self.payload = payload

    async def get_order_status(self, order_id: str) -> dict | None:
        return self.payload


@pytest.mark.asyncio
async def test_amazon_captura_latest_ship_date_mesmo_nao_enviado():
    from datetime import UTC, datetime

    from app.services import marketplace_shipment_check as m

    client = _FakeAmazonClient({
        "order_status": "Unshipped",
        "easyship_status": None,
        "last_update_date": "2026-08-06T12:00:00Z",
        "latest_ship_date": "2026-08-07T02:59:59Z",  # 06/08 23:59 BRT
    })
    deadlines: dict[int, datetime] = {}
    res = await m._amazon_shipped_for(
        client, _fake_pending_order("701-1"), deadlines,
    )
    assert res is None  # Unshipped continua não-enviado
    assert deadlines == {999: datetime(2026, 8, 7, 2, 59, 59, tzinfo=UTC)}


@pytest.mark.asyncio
async def test_amazon_enviado_tambem_carimba_prazo():
    """Mesmo Shipped o prazo vem junto — inofensivo e mantém histórico."""
    from datetime import UTC, datetime

    from app.services import marketplace_shipment_check as m

    client = _FakeAmazonClient({
        "order_status": "Shipped",
        "easyship_status": None,
        "last_update_date": "2026-08-06T12:00:00Z",
        "latest_ship_date": "2026-08-07T02:59:59Z",
    })
    deadlines: dict[int, datetime] = {}
    res = await m._amazon_shipped_for(
        client, _fake_pending_order("701-1"), deadlines,
    )
    assert res == (999, date(2026, 8, 6))
    assert deadlines == {999: datetime(2026, 8, 7, 2, 59, 59, tzinfo=UTC)}
