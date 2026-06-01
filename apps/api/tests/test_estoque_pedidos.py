"""Filtro da aba `/controle-estoque/pedidos`: inclui situacao 83953 (Entregue)
como verde, mantém 15 verde, 83965 vermelho, esconde 6.

Fix companion: antes 83953 sumia da aba quando o Bling marcava entregue —
pedido continuava com `em_andamento_data` válido mas não era selecionado.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, User, UserRole, UserStatus

PERM_VIEW = {"controle_estoque": {"view": True, "edit": False, "delete": False}}


@pytest_asyncio.fixture
async def admin_view(db: AsyncSession) -> User:
    """Admin pra não cair no filtro de stock_tags."""
    u = User(
        open_id=f"email:av-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"av-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
        permissions=PERM_VIEW,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def four_orders(db: AsyncSession) -> dict[str, int]:
    """4 pedidos no dia 28/05 cobrindo cada situação relevante."""
    d = date(2026, 5, 28)
    orders = [
        BlingOrder(
            bling_id=910001, numero="910001", item_codigo="sku-15",
            item_index=0, situacao="15", em_andamento_data=d,
        ),
        BlingOrder(
            bling_id=910002, numero="910002", item_codigo="sku-83953",
            item_index=0, situacao="83953", em_andamento_data=d,
        ),
        BlingOrder(
            bling_id=910003, numero="910003", item_codigo="sku-83965",
            item_index=0, situacao="83965", em_andamento_data=d,
        ),
        BlingOrder(
            bling_id=910004, numero="910004", item_codigo="sku-6",
            item_index=0, situacao="6", em_andamento_data=d,
        ),
        BlingOrder(
            bling_id=910005, numero="910005", item_codigo="sku-83957",
            item_index=0, situacao="83957", em_andamento_data=date(2026, 5, 25),
        ),
        BlingOrder(
            bling_id=910006, numero="910006", item_codigo="sku-545902",
            item_index=0, situacao="545902", em_andamento_data=date(2026, 5, 23),
        ),
    ]
    db.add_all(orders)
    await db.commit()
    return {o.numero: o.bling_id for o in orders}


@pytest.mark.asyncio
async def test_aba_inclui_83953_como_enviado(
    client: AsyncClient, admin_view: User,
    auth_as: Callable[[User | None], None], four_orders: dict[str, int],
):
    """83953 (Entregue) aparece na aba com status="enviado" (verde)."""
    auth_as(admin_view)
    r = await client.get(
        "/api/estoque/pedidos?data_inicio=2026-05-28&data_fim=2026-05-28"
    )
    assert r.status_code == 200, r.text
    by_numero = {p["pedido_bling"]: p for p in r.json()["data"]}

    # 83953 presente + verde
    assert "910002" in by_numero
    assert by_numero["910002"]["status"] == "enviado"

    # 15 continua presente + verde
    assert "910001" in by_numero
    assert by_numero["910001"]["status"] == "enviado"

    # 83965 com data presente + vermelho
    assert "910003" in by_numero
    assert by_numero["910003"]["status"] == "nao_enviado"

    # 6 NÃO aparece (não pertence ao fluxo da aba)
    assert "910004" not in by_numero


@pytest.mark.asyncio
async def test_aba_inclui_devolucao_e_resolvido_como_enviado(
    client: AsyncClient, admin_view: User,
    auth_as: Callable[[User | None], None], four_orders: dict[str, int],
):
    """83957 (Aguardando Devolução) e 545902 (Resolvido) ficam visíveis
    como verde — pedido já saiu do estoque, fluxo de devolução é tratado
    em outra aba."""
    auth_as(admin_view)
    # Janela 23-25/05 cobre os 2 cenários novos.
    r = await client.get(
        "/api/estoque/pedidos?data_inicio=2026-05-23&data_fim=2026-05-25"
    )
    assert r.status_code == 200, r.text
    by_numero = {p["pedido_bling"]: p for p in r.json()["data"]}

    # 83957 (Aguardando Devolução) — verde
    assert "910005" in by_numero
    assert by_numero["910005"]["status"] == "enviado"

    # 545902 (Resolvido) — verde
    assert "910006" in by_numero
    assert by_numero["910006"]["status"] == "enviado"
