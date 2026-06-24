"""Ledger de envios por evento (migration 0155).

Cobre dois níveis:

  * TRIGGER `bling_orders_envio_evento_capture` — captura a entrada na situação
    15, é idempotente (1ª entrada vence, nunca move) e mantém o registro mesmo
    se o pedido depois for cancelado. O trigger é recriado no conftest
    (`_setup_schema`) porque o `create_all` só faz a tabela, não a função.
  * Endpoint `GET /api/estoque/envios` — pós-cutover 2026-06-24, `envios` é a
    contagem OFICIAL (ledger por evento); `envios_em_andamento` segue ao lado
    como comparação admin. Mesma classificação de tag e união dos dias.

Mais o corte das 08:00 BRT (`shipping_day`) validado direto no SQL.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date, datetime

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingEnvioCorrecao,
    BlingEnvioEvento,
    BlingOrder,
    User,
    UserRole,
    UserStatus,
)

# Dia claramente no passado: garante v_old_day != hoje no re-carimbo.
_DIA_ERRADO = date(2020, 1, 1)

PERM_VIEW = {"controle_estoque": {"view": True, "edit": False, "delete": False}}
_DIA = date(2026, 6, 2)


# ─── Fixtures ────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def admin_view(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:ev-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"ev-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
        permissions=PERM_VIEW,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def operador_ra(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:ra-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"ra-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions=PERM_VIEW,
        stock_tags=["ra"],
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


# ─── Helpers ─────────────────────────────────────────────────────────────


async def _add_order(
    db: AsyncSession, *, bling_id: int, situacao: str, item_index: int = 0,
    item_codigo: str | None = None,
) -> BlingOrder:
    o = BlingOrder(
        bling_id=bling_id, numero=str(bling_id),
        item_codigo=item_codigo or f"sku-{bling_id}", item_index=item_index,
        situacao=situacao,
    )
    db.add(o)
    await db.commit()
    return o


async def _ledger_count(db: AsyncSession, bling_id: int) -> int:
    return (await db.execute(
        select(func.count())
        .select_from(BlingEnvioEvento)
        .where(BlingEnvioEvento.bling_id == bling_id)
    )).scalar_one()


async def _ledger_occurred_at(db: AsyncSession, bling_id: int) -> datetime:
    return (await db.execute(
        select(BlingEnvioEvento.occurred_at)
        .where(BlingEnvioEvento.bling_id == bling_id)
        .order_by(BlingEnvioEvento.item_index)
        .limit(1)
    )).scalar_one()


async def _seed_evento(
    db: AsyncSession, *, bling_id: int, shipping_day: date,
    item_codigo: str, item_index: int = 0,
) -> None:
    """Insere direto no ledger (sem passar pelo trigger) — pros testes de
    endpoint, onde queremos controlar o shipping_day."""
    db.add(BlingEnvioEvento(
        bling_id=bling_id, item_index=item_index, item_codigo=item_codigo,
        numero=str(bling_id),
        occurred_at=datetime(shipping_day.year, shipping_day.month,
                             shipping_day.day, 12, 0),
        shipping_day=shipping_day,
    ))
    await db.commit()


async def _get_envios(client: AsyncClient, dia: date = _DIA) -> dict:
    r = await client.get(
        f"/api/estoque/envios?data_inicio={dia.isoformat()}&data_fim={dia.isoformat()}"
    )
    assert r.status_code == 200, r.text
    return r.json()


# ─── Trigger: captura ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_trigger_captura_entrada_15(db: AsyncSession):
    """INSERT já em 15 → 1 linha no ledger."""
    await _add_order(db, bling_id=930001, situacao="15")
    assert await _ledger_count(db, 930001) == 1


@pytest.mark.asyncio
async def test_trigger_captura_transicao_83965_para_15(db: AsyncSession):
    """83965 (etiqueta) não captura; UPDATE → 15 captura."""
    o = await _add_order(db, bling_id=930002, situacao="83965")
    assert await _ledger_count(db, 930002) == 0
    o.situacao = "15"
    await db.commit()
    assert await _ledger_count(db, 930002) == 1


@pytest.mark.asyncio
async def test_trigger_nao_captura_pre_envio(db: AsyncSession):
    """Em aberto (6) e etiqueta (83965) não entram no ledger."""
    await _add_order(db, bling_id=930003, situacao="6")
    await _add_order(db, bling_id=930004, situacao="83965")
    assert await _ledger_count(db, 930003) == 0
    assert await _ledger_count(db, 930004) == 0


@pytest.mark.asyncio
async def test_trigger_uma_linha_por_item(db: AsyncSession):
    """Pedido com 2 itens em 15 → 2 linhas (grão = bling_id+item_index)."""
    await _add_order(db, bling_id=930005, situacao="15", item_index=0,
                     item_codigo="a001.ra")
    await _add_order(db, bling_id=930005, situacao="15", item_index=1,
                     item_codigo="a002.ci")
    assert await _ledger_count(db, 930005) == 2


# ─── Trigger: idempotência e imutabilidade ───────────────────────────────


@pytest.mark.asyncio
async def test_trigger_idempotente_e_imutavel(db: AsyncSession):
    """Voltar a 15 depois de oscilar não duplica nem move o occurred_at.
    Simula o redisparo do sync/oscilação 15→outro→15."""
    o = await _add_order(db, bling_id=930006, situacao="15")
    primeiro = await _ledger_occurred_at(db, 930006)
    # Oscila pra fora e volta pra 15 (transição nova → trigger dispara de novo,
    # mas ON CONFLICT DO NOTHING preserva a 1ª entrada).
    o.situacao = "83953"
    await db.commit()
    o.situacao = "15"
    await db.commit()
    assert await _ledger_count(db, 930006) == 1
    assert await _ledger_occurred_at(db, 930006) == primeiro


@pytest.mark.asyncio
async def test_trigger_cancelado_depois_mantem(db: AsyncSession):
    """Entrou em 15 e depois foi cancelado (12) → registro PERMANECE
    (decisão: tudo que entrou em andamento fica contado)."""
    o = await _add_order(db, bling_id=930007, situacao="15")
    assert await _ledger_count(db, 930007) == 1
    o.situacao = "12"
    await db.commit()
    assert await _ledger_count(db, 930007) == 1


# ─── Corte das 08:00 (shipping_day) ──────────────────────────────────────


@pytest.mark.asyncio
async def test_shipping_day_corte_08h(db: AsyncSession):
    """07:59 BRT pertence ao dia anterior; 08:01 ao dia corrente."""
    antes = (await db.execute(text(
        "SELECT (((TIMESTAMPTZ '2026-06-03 07:59:00-03:00')"
        " AT TIME ZONE 'America/Sao_Paulo') - interval '8 hours')::date"
    ))).scalar_one()
    depois = (await db.execute(text(
        "SELECT (((TIMESTAMPTZ '2026-06-03 08:01:00-03:00')"
        " AT TIME ZONE 'America/Sao_Paulo') - interval '8 hours')::date"
    ))).scalar_one()
    assert antes == date(2026, 6, 2)
    assert depois == date(2026, 6, 3)


# ─── Endpoint: contagem paralela ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_endpoint_expoe_envios_evento(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin_view: User,
):
    auth_as(admin_view)
    await _seed_evento(db, bling_id=940001, shipping_day=_DIA, item_codigo="x.ra")
    body = await _get_envios(client)
    dia = next(i for i in body["data"] if i["data"] == _DIA.isoformat())
    # Pós-cutover 2026-06-24: `envios` é a contagem oficial (ledger).
    assert dia["envios"] == 1
    assert body["total_envios"] == 1


@pytest.mark.asyncio
async def test_endpoint_distinct_bling_id(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin_view: User,
):
    """Pedido multi-item conta 1× (DISTINCT bling_id)."""
    auth_as(admin_view)
    await _seed_evento(db, bling_id=940002, shipping_day=_DIA,
                       item_codigo="a.ra", item_index=0)
    await _seed_evento(db, bling_id=940002, shipping_day=_DIA,
                       item_codigo="b.ra", item_index=1)
    body = await _get_envios(client)
    dia = next(i for i in body["data"] if i["data"] == _DIA.isoformat())
    assert dia["envios"] == 1


@pytest.mark.asyncio
async def test_endpoint_dia_so_no_ledger_aparece(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin_view: User,
):
    """Dia que só existe no ledger (nenhum BlingOrder verde) aparece com
    envios_em_andamento=0 e envios>0 — a divergência fica visível."""
    auth_as(admin_view)
    await _seed_evento(db, bling_id=940003, shipping_day=_DIA, item_codigo="y.ci")
    body = await _get_envios(client)
    dia = next(i for i in body["data"] if i["data"] == _DIA.isoformat())
    assert dia["envios_em_andamento"] == 0
    assert dia["envios"] == 1


@pytest.mark.asyncio
async def test_endpoint_filtra_por_tag_do_operador(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None],
    admin_view: User, operador_ra: User,
):
    """Operador RA só conta eventos cujo SKU é da tag RA; admin conta todos."""
    await _seed_evento(db, bling_id=940004, shipping_day=_DIA, item_codigo="p.ra")
    await _seed_evento(db, bling_id=940005, shipping_day=_DIA, item_codigo="q.ci")

    auth_as(operador_ra)
    body_ra = await _get_envios(client)
    dia_ra = next(i for i in body_ra["data"] if i["data"] == _DIA.isoformat())
    assert dia_ra["envios"] == 1  # só o .ra

    auth_as(admin_view)
    body_admin = await _get_envios(client)
    dia_admin = next(i for i in body_admin["data"] if i["data"] == _DIA.isoformat())
    assert dia_admin["envios"] == 2  # .ra + .ci


# ─── Correção de dia (erro→volta→relança noutro dia) + fila Threema ───────


async def _ledger_dia(db: AsyncSession, bling_id: int, item_index: int = 0) -> date:
    return (await db.execute(
        select(BlingEnvioEvento.shipping_day)
        .where(BlingEnvioEvento.bling_id == bling_id,
               BlingEnvioEvento.item_index == item_index)
    )).scalar_one()


async def _correcoes(db: AsyncSession, bling_id: int) -> list[BlingEnvioCorrecao]:
    return list((await db.execute(
        select(BlingEnvioCorrecao).where(BlingEnvioCorrecao.bling_id == bling_id)
    )).scalars().all())


@pytest.mark.asyncio
async def test_bounce_mesmo_dia_nao_gera_correcao(db: AsyncSession):
    """6→15→6→15 no MESMO dia: re-stamp não move (mesmo dia), sem correção."""
    o = await _add_order(db, bling_id=950001, situacao="6")
    o.situacao = "15"
    await db.commit()
    o.situacao = "6"
    await db.commit()
    o.situacao = "15"
    await db.commit()
    assert await _ledger_count(db, 950001) == 1
    assert len(await _correcoes(db, 950001)) == 0


@pytest.mark.asyncio
async def test_correcao_cross_day_recarimba_e_enfileira(db: AsyncSession):
    """Pedido carimbado num dia ERRADO (passado); operador volta pra em aberto
    e relança em 15 → re-carimba pro dia de hoje E enfileira a correção. Não
    duplica (1 linha no ledger)."""
    o = await _add_order(db, bling_id=950002, situacao="6")
    # ledger já tem o dia errado (envio equivocado de um dia passado)
    await _seed_evento(db, bling_id=950002, shipping_day=_DIA_ERRADO,
                       item_codigo="sku-950002")
    o.situacao = "15"   # correção: 6 → 15 hoje
    await db.commit()
    novo = await _ledger_dia(db, 950002)
    assert novo != _DIA_ERRADO            # moveu pro dia de hoje
    assert await _ledger_count(db, 950002) == 1  # não duplicou
    cs = await _correcoes(db, 950002)
    assert len(cs) == 1
    assert cs[0].dia_anterior == _DIA_ERRADO
    assert cs[0].dia_novo == novo
    assert cs[0].threema_sent_at is None  # pendente p/ a rotina drenar


@pytest.mark.asyncio
async def test_correcao_multi_item_um_aviso_so(db: AsyncSession):
    """Pedido com 2 itens corrigido → 1 linha só na fila (dedup por
    bling_id+dia_anterior+dia_novo), mas os 2 itens movem no ledger."""
    await _add_order(db, bling_id=950003, situacao="6", item_index=0, item_codigo="a.ra")
    await _add_order(db, bling_id=950003, situacao="6", item_index=1, item_codigo="b.ra")
    await _seed_evento(db, bling_id=950003, shipping_day=_DIA_ERRADO,
                       item_codigo="a.ra", item_index=0)
    await _seed_evento(db, bling_id=950003, shipping_day=_DIA_ERRADO,
                       item_codigo="b.ra", item_index=1)
    await db.execute(text("UPDATE bling_orders SET situacao='15' WHERE bling_id=950003"))
    await db.commit()
    assert len(await _correcoes(db, 950003)) == 1            # 1 aviso só
    assert await _ledger_dia(db, 950003, 0) != _DIA_ERRADO   # item 0 moveu
    assert await _ledger_dia(db, 950003, 1) != _DIA_ERRADO   # item 1 moveu


@pytest.mark.asyncio
async def test_oscilacao_entregue_para_15_nao_recarimba(db: AsyncSession):
    """Entregue(83953)→15 (oscilação do Bling, vem de estado JÁ-enviado): NÃO
    move o dia nem enfileira — o pedido já tinha saído."""
    o = await _add_order(db, bling_id=950004, situacao="83953")
    await _seed_evento(db, bling_id=950004, shipping_day=_DIA_ERRADO,
                       item_codigo="sku-950004")
    o.situacao = "15"
    await db.commit()
    assert await _ledger_dia(db, 950004) == _DIA_ERRADO   # preservado
    assert len(await _correcoes(db, 950004)) == 0


@pytest.mark.asyncio
async def test_reinsert_sync_nao_recarimba(db: AsyncSession):
    """Reinsert do sync (DELETE+INSERT → TG_OP=INSERT) com ledger já existente:
    preserva o dia, não enfileira. É a defesa contra o re-carimbo do sync."""
    await _seed_evento(db, bling_id=950005, shipping_day=_DIA_ERRADO,
                       item_codigo="sku-950005")
    await _add_order(db, bling_id=950005, situacao="15", item_codigo="sku-950005")
    assert await _ledger_dia(db, 950005) == _DIA_ERRADO   # preservado
    assert len(await _correcoes(db, 950005)) == 0
