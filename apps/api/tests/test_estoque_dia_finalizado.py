"""Trava do badge `conferencia_estoque` quando admin tica CONFERIDO em
um dia (section='envio'). Antes, o badge regredia de "total" → "parcial"
sempre que entrasse produto novo na tag, porque o `total_produtos`
current era comparado com o histórico `estoque_conferidos`.

Migration 0134 cria estoque_dia_finalizado. O router lê o set de datas
travadas pro período e força "total" no badge nesses dias.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingEnvioEvento,
    EstoqueDiaFinalizado,
    Product,
    StockCheck,
    User,
    UserRole,
    UserStatus,
)

PERM = {"controle_estoque": {"view": True, "edit": True, "delete": False}}

_DIA = date(2026, 6, 5)


@pytest_asyncio.fixture
async def admin(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:fd-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"fd-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
        permissions=PERM,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


# Produtos existiam antes de _DIA (denominador do dia = 2).
_ANTES = datetime(2026, 6, 1, tzinfo=UTC)


async def _seed_products_and_order(db: AsyncSession, admin: User) -> None:
    """2 produtos ativos (criados antes de _DIA) + 1 evento de envio no
    ledger em _DIA (envios=1, denominador do dia=2).

    Insere o BlingEnvioEvento direto (shipping_day=_DIA) em vez de
    depender do trigger de bling_orders, que carimbaria 'hoje' e deixaria
    o teste frágil à data corrente."""
    for sku in ("aa1.sa", "aa2.sa"):
        db.add(Product(
            user_id=admin.id, sku=sku, name=f"prod {sku}",
            stock=10, min_stock=0, situacao="A", formato="S",
            created_at=_ANTES,
        ))
    db.add(BlingEnvioEvento(
        bling_id=910001, item_index=0, item_codigo="aa1.sa",
        numero="910001", shipping_day=_DIA,
    ))
    await db.commit()


async def _get_envios_dia(client: AsyncClient) -> dict:
    r = await client.get(
        f"/api/estoque/envios?data_inicio={_DIA.isoformat()}&data_fim={_DIA.isoformat()}"
    )
    assert r.status_code == 200, r.text
    items = r.json()["data"]
    assert len(items) == 1, items
    return items[0]


@pytest.mark.asyncio
async def test_dia_sem_lock_e_produto_novo_cai_pra_parcial(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin: User,
):
    """Comportamento atual preservado pra dias NÃO finalizados:
    1 conferido de 2 produtos → 'parcial'."""
    auth_as(admin)
    await _seed_products_and_order(db, admin)
    # Conferiu só 1 dos 2.
    db.add(StockCheck(
        user_id=admin.id, section="estoque",
        reference_id="aa1.sa", reference_date=_DIA, conferido=True,
    ))
    await db.commit()

    item = await _get_envios_dia(client)
    assert item["conferencia_estoque"] == "parcial"


@pytest.mark.asyncio
async def test_dia_travado_continua_total_mesmo_com_produto_novo(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin: User,
):
    """Lock em estoque_dia_finalizado força 'total' independente do
    count atual de produtos. Reproduz o bug do operador: produto novo
    entra na tag, total_produtos cresce, mas o dia FINALIZADO continua
    travado em 'total'."""
    auth_as(admin)
    await _seed_products_and_order(db, admin)
    # Confere só 1 dos 2 (espelha o cenário do bug).
    db.add(StockCheck(
        user_id=admin.id, section="estoque",
        reference_id="aa1.sa", reference_date=_DIA, conferido=True,
    ))
    # Admin tinha fechado o dia — carimba o lock.
    db.add(EstoqueDiaFinalizado(data=_DIA))
    await db.commit()

    item = await _get_envios_dia(client)
    assert item["conferencia_estoque"] == "total"


@pytest.mark.asyncio
async def test_toggle_admin_envio_grava_lock(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin: User,
):
    """POST /check (section=envio, admin, conferido=true) deve gravar
    a row em estoque_dia_finalizado."""
    auth_as(admin)
    r = await client.post(
        f"/api/estoque/check?section=envio&reference_id={_DIA.isoformat()}"
        f"&reference_date={_DIA.isoformat()}&conferido=true"
    )
    assert r.status_code == 200, r.text
    rows = (await db.execute(
        select(EstoqueDiaFinalizado).where(EstoqueDiaFinalizado.data == _DIA)
    )).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_lock_nao_e_removido_no_destique(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin: User,
):
    """Lock é permanente: admin destickar não remove a trava. O badge
    do dia continua 'total' (carimbado), mesmo se conferido=false."""
    auth_as(admin)
    # Tica.
    await client.post(
        f"/api/estoque/check?section=envio&reference_id={_DIA.isoformat()}"
        f"&reference_date={_DIA.isoformat()}&conferido=true"
    )
    # Destika.
    r = await client.post(
        f"/api/estoque/check?section=envio&reference_id={_DIA.isoformat()}"
        f"&reference_date={_DIA.isoformat()}&conferido=false"
    )
    assert r.status_code == 200, r.text
    rows = (await db.execute(
        select(EstoqueDiaFinalizado).where(EstoqueDiaFinalizado.data == _DIA)
    )).scalars().all()
    assert len(rows) == 1  # ainda lá


@pytest.mark.asyncio
async def test_produto_criado_depois_nao_regride_dia_ja_conferido(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin: User,
):
    """Bug: dia com TODOS os produtos conferidos ('total') regredia pra
    'parcial' ao entrar produto novo no Bling, porque o novo inflava o
    denominador de TODOS os dias anteriores. Com o denominador POR DIA
    (só conta produtos criados até o dia), um produto criado DEPOIS de
    _DIA não entra no denominador de _DIA — o dia continua 'total' sem
    precisar de lock."""
    auth_as(admin)
    await _seed_products_and_order(db, admin)  # 2 produtos criados antes de _DIA
    # Conferiu os 2 produtos existentes em _DIA → dia = 'total'.
    for sku in ("aa1.sa", "aa2.sa"):
        db.add(StockCheck(
            user_id=admin.id, section="estoque",
            reference_id=sku, reference_date=_DIA, conferido=True,
        ))
    await db.commit()
    assert (await _get_envios_dia(client))["conferencia_estoque"] == "total"

    # Entra produto novo no Bling DEPOIS de _DIA (created_at = hoje).
    db.add(Product(
        user_id=admin.id, sku="aa3.sa", name="prod novo",
        stock=10, min_stock=0, situacao="A", formato="S",
    ))
    await db.commit()

    # Dia continua 'total' — o produto novo só conta do dia da criação
    # pra frente, não regride _DIA pra 'parcial'.
    assert (await _get_envios_dia(client))["conferencia_estoque"] == "total"
