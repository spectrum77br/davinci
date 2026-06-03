"""Aba Envios alinha contagem com aba Pedidos.

Antes o endpoint /envios contava `DISTINCT bling_id` apenas filtrando
`em_andamento_data IS NOT NULL`. Pedidos em 83965 (Etiqueta), 83955
(Aguardando Cancelamento), 12 (Cancelado) inflavam o número e
divergiam da aba Pedidos (que filtra situação ∈ {15, 83953, 83957,
545902}). Agora ambas aplicam o mesmo filtro.
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


_DIA = date(2026, 6, 2)


async def _seed(
    db: AsyncSession, *, bling_id: int, situacao: str,
    em_andamento_data: date | None = _DIA,
) -> None:
    db.add(BlingOrder(
        bling_id=bling_id, numero=str(bling_id),
        item_codigo=f"sku-{bling_id}", item_index=0,
        situacao=situacao, em_andamento_data=em_andamento_data,
    ))
    await db.commit()


async def _envios_for_day(client: AsyncClient) -> int:
    """Total de envios no dia _DIA = 2026-06-02."""
    r = await client.get(
        f"/api/estoque/envios?data_inicio={_DIA.isoformat()}&data_fim={_DIA.isoformat()}"
    )
    assert r.status_code == 200, r.text
    body = r.json()
    if not body.get("data"):
        return 0
    # Sum envios across days (geralmente 1 dia só, mas defensivo).
    return sum(item["envios"] for item in body["data"])


@pytest.mark.asyncio
async def test_envios_inclui_situacao_15(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin_view: User,
):
    """Situação 15 (Atendido/Em andamento) — verde, conta como envio."""
    auth_as(admin_view)
    await _seed(db, bling_id=920001, situacao="15")
    assert await _envios_for_day(client) == 1


@pytest.mark.asyncio
async def test_envios_inclui_situacao_83953(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin_view: User,
):
    """83953 (Entregue) — verde, conta."""
    auth_as(admin_view)
    await _seed(db, bling_id=920002, situacao="83953")
    assert await _envios_for_day(client) == 1


@pytest.mark.asyncio
async def test_envios_inclui_situacao_83957(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin_view: User,
):
    """83957 (Aguardando Devolução) — verde (pedido já saiu), conta."""
    auth_as(admin_view)
    await _seed(db, bling_id=920003, situacao="83957")
    assert await _envios_for_day(client) == 1


@pytest.mark.asyncio
async def test_envios_inclui_situacao_545902(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin_view: User,
):
    """545902 (Resolvido) — verde, conta."""
    auth_as(admin_view)
    await _seed(db, bling_id=920004, situacao="545902")
    assert await _envios_for_day(client) == 1


@pytest.mark.asyncio
async def test_envios_exclui_situacao_83955(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin_view: User,
):
    """Regressão guard do bug original: 83955 (Aguardando Cancelamento)
    NÃO é envio. Foi o caso reproduzido (pedido 279120) que divergia
    Pedidos=24 vs Envios=25 no filtro Dia 02/06 + Tag RA."""
    auth_as(admin_view)
    await _seed(db, bling_id=920005, situacao="83955")
    assert await _envios_for_day(client) == 0


@pytest.mark.asyncio
async def test_envios_exclui_situacao_83965(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin_view: User,
):
    """83965 (Etiqueta Gerada — vermelho) NÃO é envio confirmado.
    Mesmo com em_andamento_data carimbada (provisório do dia da etiqueta,
    fix e081e0d), não entra na aba Envios — ainda está aguardando
    confirmação da agência."""
    auth_as(admin_view)
    await _seed(db, bling_id=920006, situacao="83965")
    assert await _envios_for_day(client) == 0


@pytest.mark.asyncio
async def test_envios_exclui_situacao_12_cancelado(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin_view: User,
):
    """12 (Cancelado) NÃO é envio."""
    auth_as(admin_view)
    await _seed(db, bling_id=920007, situacao="12")
    assert await _envios_for_day(client) == 0
