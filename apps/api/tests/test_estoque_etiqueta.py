"""Landing zone da etiqueta transformada (item 4 da Fase 3b).

A aba Controle de Estoque → Pedidos ganha o flag `etiqueta_disponivel` por
pedido e um endpoint que serve o blob (autenticado por cookie/sessão). O botão
"Imprimir Etiqueta" só habilita quando existe blob pro pedido. A etapa de
transformação (visualização) grava em nf_etiqueta_arquivo; aqui só provamos
que a leitura/serving funciona e é gated por permissão.
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
from app.models.nf import NfEtiquetaArquivo

PERM_VIEW = {"controle_estoque": {"view": True, "edit": False, "delete": False}}

# PDF mínimo válido (header) — só pra provar que o blob volta byte-a-byte.
_PDF_BYTES = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<</Type/Catalog>>endobj\n"


@pytest_asyncio.fixture
async def admin_view(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:et-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"et-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
        permissions=PERM_VIEW,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def dois_pedidos(db: AsyncSession) -> None:
    """Dois pedidos enviados no mesmo dia; só um tem etiqueta transformada."""
    d = date(2026, 5, 28)
    db.add_all([
        BlingOrder(
            bling_id=920001, numero="920001", item_codigo="sku-com",
            item_index=0, situacao="15", em_andamento_data=d,
        ),
        BlingOrder(
            bling_id=920002, numero="920002", item_codigo="sku-sem",
            item_index=0, situacao="15", em_andamento_data=d,
        ),
    ])
    db.add(NfEtiquetaArquivo(
        pedido_bling="920001",
        filename="etiqueta_920001.pdf",
        content_type="application/pdf",
        size_bytes=len(_PDF_BYTES),
        blob=_PDF_BYTES,
    ))
    await db.commit()


@pytest.mark.asyncio
async def test_pedidos_flag_etiqueta_disponivel(
    client: AsyncClient, admin_view: User,
    auth_as: Callable[[User | None], None], dois_pedidos: None,
):
    """A lista marca etiqueta_disponivel=True só pro pedido que tem blob."""
    auth_as(admin_view)
    r = await client.get(
        "/api/estoque/pedidos?data_inicio=2026-05-28&data_fim=2026-05-28"
    )
    assert r.status_code == 200, r.text
    by_numero = {p["pedido_bling"]: p for p in r.json()["data"]}
    assert by_numero["920001"]["etiqueta_disponivel"] is True
    assert by_numero["920002"]["etiqueta_disponivel"] is False


@pytest.mark.asyncio
async def test_serve_etiqueta_blob(
    client: AsyncClient, admin_view: User,
    auth_as: Callable[[User | None], None], dois_pedidos: None,
):
    """O endpoint devolve o blob exato + content-type + inline disposition."""
    auth_as(admin_view)
    r = await client.get("/api/estoque/pedidos/920001/etiqueta")
    assert r.status_code == 200, r.text
    assert r.content == _PDF_BYTES
    assert r.headers["content-type"].startswith("application/pdf")
    assert "etiqueta_920001.pdf" in r.headers.get("content-disposition", "")


@pytest.mark.asyncio
async def test_serve_etiqueta_404(
    client: AsyncClient, admin_view: User,
    auth_as: Callable[[User | None], None], dois_pedidos: None,
):
    """Pedido sem etiqueta transformada → 404 (a etapa de visualização não
    rodou pra ele)."""
    auth_as(admin_view)
    r = await client.get("/api/estoque/pedidos/920002/etiqueta")
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "nf_etiqueta_nao_encontrada"


@pytest.mark.asyncio
async def test_etiqueta_requer_permissao(
    client: AsyncClient, db: AsyncSession,
    auth_as: Callable[[User | None], None], dois_pedidos: None,
):
    """Usuário sem controle_estoque.view não serve a etiqueta (403)."""
    outsider = User(
        open_id=f"email:out-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"out-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions={},
    )
    db.add(outsider)
    await db.commit()
    await db.refresh(outsider)
    auth_as(outsider)
    r = await client.get("/api/estoque/pedidos/920001/etiqueta")
    assert r.status_code == 403, r.text
