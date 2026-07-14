"""Chamados de pós-venda — CRUD + sugestão de Status Bling.

Registro manual no formato da Planilha2. Gated pelo recurso `chamados`
(view p/ listar/opções/sugestão, edit p/ criar/editar/remover). A sugestão
devolve os Status Bling candidatos que a curadoria da planilha já viu pra a
assinatura de status do Meli informada — nunca decide sozinho.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UserRole, UserStatus
from app.services import chamados_rules


@pytest_asyncio.fixture
async def admin(db: AsyncSession) -> User:
    email = f"adm-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(open_id=f"email:{email}", email=email, role=UserRole.ADMIN, status=UserStatus.ACTIVE)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def viewer(db: AsyncSession) -> User:
    email = f"vw-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(
        open_id=f"email:{email}",
        email=email,
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions={"chamados": {"view": True}},
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def outsider(db: AsyncSession) -> User:
    email = f"out-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(open_id=f"email:{email}", email=email, role=UserRole.USER, status=UserStatus.ACTIVE)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest.mark.asyncio
async def test_crud_lifecycle(
    client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)

    meli = {"order_status": "cancelled", "ship_status": "delivered", "cancel_group": "mediations"}
    r = await client.post(
        "/api/chamados",
        json={
            "data": "2026-07-10",
            "pedido_bling": "283041",
            "pedido_marketplace": "2000012345",
            "plataforma": "Mercado Livre",
            "conta": "inova",
            "meli_status": meli,
            "localizacao": "em trânsito",
            "status_bling": "Aguardando Devolução",
            "observacao": "cliente pediu troca",
        },
    )
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert r.json()["pedido_bling"] == "283041"
    assert r.json()["meli_status"] == meli
    assert r.json()["status_bling"] == "Aguardando Devolução"

    # Lista.
    r = await client.get("/api/chamados")
    assert any(c["id"] == cid for c in r.json())

    # Edita (troca classificação + limpa um campo do meli).
    r = await client.patch(
        f"/api/chamados/{cid}",
        json={"status_bling": "Resolvido", "meli_status": {"order_status": "cancelled"}},
    )
    assert r.status_code == 200
    assert r.json()["status_bling"] == "Resolvido"
    assert r.json()["meli_status"] == {"order_status": "cancelled"}

    # Remove.
    r = await client.delete(f"/api/chamados/{cid}")
    assert r.status_code == 204
    r = await client.get("/api/chamados")
    assert not any(c["id"] == cid for c in r.json())


@pytest.mark.asyncio
async def test_viewer_can_list_not_edit(
    client: AsyncClient, admin: User, viewer: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)
    r = await client.post("/api/chamados", json={"pedido_bling": "999"})
    assert r.status_code == 201

    auth_as(viewer)
    # view-only: lista, opções e sugestão OK
    assert (await client.get("/api/chamados")).status_code == 200
    assert (await client.get("/api/chamados/opcoes")).status_code == 200
    r = await client.post("/api/chamados/sugestao", json={"meli_status": {"order_status": "paid"}})
    assert r.status_code == 200
    # mas NÃO cria
    assert (await client.post("/api/chamados", json={"pedido_bling": "hack"})).status_code == 403


@pytest.mark.asyncio
async def test_outsider_forbidden(
    client: AsyncClient, outsider: User, auth_as: Callable[[User | None], None]
):
    auth_as(outsider)
    assert (await client.get("/api/chamados")).status_code == 403


@pytest.mark.asyncio
async def test_patch_not_found(
    client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)
    r = await client.patch(f"/api/chamados/{uuid.uuid4()}", json={"status_bling": "z"})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "chamado_not_found"


@pytest.mark.asyncio
async def test_sugestao_candidatos_ordenados(
    client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)
    r = await client.post(
        "/api/chamados/sugestao",
        json={"meli_status": {"order_status": "cancelled", "ship_status": "delivered", "cancel_group": "mediations"}},
    )
    assert r.status_code == 200
    cand = r.json()["candidatos"]
    assert len(cand) > 1
    # ordenado por frequência decrescente
    counts = [c["matches"] for c in cand]
    assert counts == sorted(counts, reverse=True)
    labels = {c["status_bling"] for c in cand}
    assert "Aguardando Devolução" in labels


@pytest.mark.asyncio
async def test_opcoes_expostas(
    client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)
    r = await client.get("/api/chamados/opcoes")
    assert r.status_code == 200
    body = r.json()
    assert body["field_order"] == chamados_rules.FIELD_ORDER
    assert "cancelled" in body["field_options"]["order_status"]


def test_sugerir_selecao_vazia_retorna_lista_vazia():
    assert chamados_rules.sugerir({}) == []
    assert chamados_rules.sugerir({"order_status": ""}) == []
