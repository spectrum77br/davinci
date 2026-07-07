"""permissions.controle_estoque_see_all libera view agregada (papel
de gerente) sem dar outros poderes de admin.

O bug: churchill (role=user, 9 stock_tags) só via os próprios checks
no /envios. Operador gerente — deve ver agregado igual admin, mas
restrito às tags dele e SEM ticar section='envio' (que segue admin-only)."""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingEnvioEvento,
    Product,
    StockCheck,
    User,
    UserRole,
    UserStatus,
)

PERM_EDIT = {"controle_estoque": {"view": True, "edit": True, "delete": False}}
PERM_SEE_ALL = {
    "controle_estoque": {"view": True, "edit": True, "delete": False},
    "controle_estoque_see_all": True,
}

_DIA = date(2026, 6, 7)


@pytest_asyncio.fixture
async def gerente(db: AsyncSession) -> User:
    """User comum (role=USER) com a flag controle_estoque_see_all=true
    e a stock_tag 'sa' (mesma do produto+pedido seedados)."""
    u = User(
        open_id=f"email:gr-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"gr-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions=PERM_SEE_ALL,
        stock_tags=["sa"],
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def other_admin(db: AsyncSession) -> User:
    """Outro user (admin) cujo StockCheck será visível pro gerente
    quando a flag está ativa."""
    u = User(
        open_id=f"email:ot-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"ot-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
        permissions=PERM_EDIT,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _seed_minimal_envio(db: AsyncSession, gerente: User) -> None:
    """1 produto sa + 1 evento de envio no ledger em _DIA. Suficiente pra
    ter 1 envio na /envios. Insere o BlingEnvioEvento direto (em vez de
    depender do trigger, que carimbaria 'hoje' e deixaria o teste frágil
    à data corrente)."""
    db.add(Product(
        user_id=gerente.id, sku="aa1.sa", name="prod",
        stock=10, min_stock=0, situacao="A", formato="S",
    ))
    db.add(BlingEnvioEvento(
        bling_id=920001, item_index=0, item_codigo="aa1.sa",
        numero="920001", shipping_day=_DIA,
    ))
    await db.commit()


@pytest.mark.asyncio
async def test_gerente_ve_check_de_outro_user_no_envio(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None],
    gerente: User, other_admin: User,
):
    """Outro user marca conferido=true; gerente (see_all) deve ver
    `conferido=true` no /envios mesmo sem ter marcado ele mesmo."""
    await _seed_minimal_envio(db, gerente)
    # Outro user (admin) marca o dia como conferido.
    db.add(StockCheck(
        user_id=other_admin.id, section="envio",
        reference_id=_DIA.isoformat(), reference_date=_DIA, conferido=True,
    ))
    await db.commit()

    auth_as(gerente)
    r = await client.get(
        f"/api/estoque/envios?data_inicio={_DIA.isoformat()}&data_fim={_DIA.isoformat()}"
    )
    assert r.status_code == 200, r.text
    items = r.json()["data"]
    assert len(items) == 1
    # Sem a flag, gerente veria conferido=False (só o próprio check).
    # Com a flag, vê o agregado (bool_or → True).
    assert items[0]["conferido"] is True


@pytest.mark.asyncio
async def test_user_sem_flag_continua_vendo_so_proprios_checks(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], other_admin: User,
):
    """Sanity: user sem a flag não ganha o agregado — só vê os próprios
    checks. Garante que a flag é o único gate de mudança."""
    common = User(
        open_id=f"email:cm-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"cm-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER, status=UserStatus.ACTIVE,
        permissions=PERM_EDIT, stock_tags=["sa"],
    )
    db.add(common)
    await db.commit()
    await db.refresh(common)
    await _seed_minimal_envio(db, common)
    db.add(StockCheck(
        user_id=other_admin.id, section="envio",
        reference_id=_DIA.isoformat(), reference_date=_DIA, conferido=True,
    ))
    await db.commit()

    auth_as(common)
    r = await client.get(
        f"/api/estoque/envios?data_inicio={_DIA.isoformat()}&data_fim={_DIA.isoformat()}"
    )
    assert r.status_code == 200, r.text
    items = r.json()["data"]
    assert len(items) == 1
    # Sem flag + sem check próprio → False (não pega o do other_admin).
    assert items[0]["conferido"] is False


@pytest.mark.asyncio
async def test_gerente_nao_pode_ticar_envio(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], gerente: User,
):
    """A flag see_all NÃO concede poder de ticar section='envio' —
    isso continua admin-only (toggle_estoque_check)."""
    auth_as(gerente)
    r = await client.post(
        f"/api/estoque/check?section=envio&reference_id={_DIA.isoformat()}"
        f"&reference_date={_DIA.isoformat()}&conferido=true"
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "admin_only"
