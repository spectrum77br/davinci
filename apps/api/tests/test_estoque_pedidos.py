"""Filtro da aba `/controle-estoque/pedidos`: inclui situacao 83953 (Entregue)
como verde, mantém 15 verde, 21 (Em digitação = etiqueta enviada, canônico
desde 03/09/2026) e 83965 (Enviado Etiqueta, legado) vermelhos, esconde 6.

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
    """Pedidos cobrindo cada situação relevante (5 no dia 28/05 + 2 de
    devolução em 23-25/05)."""
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
            bling_id=910007, numero="910007", item_codigo="sku-21",
            item_index=0, situacao="21", em_andamento_data=d,
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

    # 83965 (legado) com data presente: etiqueta gerada = "aguardando coleta"
    # (laranja), não "não enviado" — o pacote já saiu da nossa mão, falta a
    # transportadora escanear (Eduardo, 04/09).
    assert "910003" in by_numero
    assert by_numero["910003"]["status"] == "aguardando_coleta"

    # 21 (Em digitação = etiqueta enviada) com data presente, idem.
    assert "910007" in by_numero
    assert by_numero["910007"]["status"] == "aguardando_coleta"

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


@pytest.mark.asyncio
async def test_filtro_status_nao_enviado_inclui_etiqueta_com_data(
    client: AsyncClient, admin_view: User,
    auth_as: Callable[[User | None], None], four_orders: dict[str, int],
):
    """Bug guard: filtro status=nao_enviado deve retornar pedidos com etiqueta
    enviada — situacao IN (21, 83965-legado) — mesmo quando têm
    em_andamento_data carimbada (o sync de etiqueta carimba data provisória).
    Antes do fix o filtro usava `em_andamento_data IS NULL` — divergia do
    classificador do payload (que é por situacao) e zerava a aba mesmo
    havendo etiquetas."""
    auth_as(admin_view)
    r = await client.get(
        "/api/estoque/pedidos?data_inicio=2026-05-28&data_fim=2026-05-28"
        "&status=nao_enviado"
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    numeros = {p["pedido_bling"] for p in data}
    # 83965 (legado) com em_andamento_data SET (sku-83965 → 910003) deve aparecer.
    assert "910003" in numeros, "83965 com data carimbada deveria estar em 'nao_enviado'"
    # 21 (canônico) idem (sku-21 → 910007).
    assert "910007" in numeros, "21 com data carimbada deveria estar em 'nao_enviado'"
    # O filtro "não enviado" segue trazendo tudo que ainda não saiu; quem separa
    # "aguardando coleta" de "parado" é o badge.
    assert all(p["status"] in ("nao_enviado", "aguardando_coleta") for p in data)
    # 15/83953 (enviados) NÃO aparecem.
    assert "910001" not in numeros
    assert "910002" not in numeros


@pytest.mark.asyncio
async def test_filtro_status_enviado_exclui_etiqueta(
    client: AsyncClient, admin_view: User,
    auth_as: Callable[[User | None], None], four_orders: dict[str, int],
):
    """Espelho: status=enviado lista 15/83953 (e 83957/545902 se em range),
    NÃO inclui 21 nem 83965 (legado) mesmo se em_andamento_data setada."""
    auth_as(admin_view)
    r = await client.get(
        "/api/estoque/pedidos?data_inicio=2026-05-28&data_fim=2026-05-28"
        "&status=enviado"
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    numeros = {p["pedido_bling"] for p in data}
    assert "910001" in numeros  # situacao=15
    assert "910002" in numeros  # situacao=83953
    assert "910003" not in numeros  # situacao=83965 (legado) — não é "enviado"
    # 21 é etiqueta provisória, não "enviado": guarda que 21 continue em
    # _SITUACAO_NAO_VERDE depois de sair de _SITUACAO_NAO_ENVIADO (senão
    # 21 com data viraria VERDE).
    assert "910007" not in numeros
    assert all(p["status"] == "enviado" for p in data)


@pytest.mark.asyncio
async def test_filtro_aguardando_coleta_traz_so_etiqueta_gerada(
    client: AsyncClient, admin_view: User,
    auth_as: Callable[[User | None], None], four_orders: dict[str, int],
):
    """Etiqueta gerada (21/83965) tem badge próprio: o pacote saiu da nossa mão
    e falta a transportadora escanear. Chamar de "não enviado" fez o Eduardo
    achar que o sistema estava travado (04/09) quando o robô de confirmação é
    que estava parado."""
    auth_as(admin_view)
    r = await client.get(
        "/api/estoque/pedidos?data_inicio=2026-05-28&data_fim=2026-05-28"
        "&status=aguardando_coleta"
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    numeros = {p["pedido_bling"] for p in data}
    assert {"910003", "910007"} <= numeros  # 83965 legado + 21 canônico
    assert all(p["status"] == "aguardando_coleta" for p in data)
    # Enviados de verdade e o "Em aberto" ficam de fora.
    assert "910001" not in numeros and "910002" not in numeros
    assert "910004" not in numeros
