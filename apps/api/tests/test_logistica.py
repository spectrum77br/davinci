"""Logística — CRUD dos casos + aba Status + sugestão de Status Bling.

Registro manual no formato da planilha. Gated pelo recurso `logistica`
(view p/ listar/opções/sugestão/status, edit p/ criar/editar/remover). A
sugestão devolve os Status Bling candidatos que a curadoria da planilha já viu
pra a assinatura de status do Meli informada — nunca decide sozinho.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UserRole, UserStatus
from app.services import logistica_rules


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
        permissions={"logistica": {"view": True}},
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
        "/api/logistica",
        json={
            "data": "2026-07-10",
            "pedido_bling": "283041",
            "pedido_marketplace": "2000012345",
            "plataforma": "Mercado Livre",
            "conta": "inova",
            "meli_status": meli,
            "rastreio": "BR123456789BR",
            "localizacao": "em trânsito",
            "status_bling": "Aguardando Devolução",
            "chamado": "CH-9981",
            "observacao": "cliente pediu troca",
        },
    )
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert r.json()["pedido_bling"] == "283041"
    assert r.json()["meli_status"] == meli
    assert r.json()["rastreio"] == "BR123456789BR"
    assert r.json()["chamado"] == "CH-9981"
    assert r.json()["status_bling"] == "Aguardando Devolução"

    # Lista.
    r = await client.get("/api/logistica")
    assert any(c["id"] == cid for c in r.json())

    # Edita (troca classificação + limpa um campo do meli + rastreio novo).
    r = await client.patch(
        f"/api/logistica/{cid}",
        json={
            "status_bling": "Resolvido",
            "meli_status": {"order_status": "cancelled"},
            "rastreio": "NEW999",
        },
    )
    assert r.status_code == 200
    assert r.json()["status_bling"] == "Resolvido"
    assert r.json()["meli_status"] == {"order_status": "cancelled"}
    assert r.json()["rastreio"] == "NEW999"
    assert r.json()["chamado"] == "CH-9981"  # inalterado

    # Remove.
    r = await client.delete(f"/api/logistica/{cid}")
    assert r.status_code == 204
    r = await client.get("/api/logistica")
    assert not any(c["id"] == cid for c in r.json())


@pytest.mark.asyncio
async def test_status_crud(
    client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)
    r = await client.post(
        "/api/logistica/status",
        json={
            "status_plataforma": "Devolução em trânsito",
            "alterar_status_bling": "Aguardando Devolução",
            "abrir_chamado": True,
            "mensagem_chamado": "acompanhar devolução",
        },
    )
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert r.json()["status_plataforma"] == "Devolução em trânsito"
    assert r.json()["abrir_chamado"] is True

    r = await client.get("/api/logistica/status")
    assert any(s["id"] == sid for s in r.json())

    r = await client.patch(
        f"/api/logistica/status/{sid}", json={"abrir_chamado": False, "alterar_status_bling": ""}
    )
    assert r.status_code == 200
    assert r.json()["abrir_chamado"] is False
    assert r.json()["alterar_status_bling"] is None

    r = await client.delete(f"/api/logistica/status/{sid}")
    assert r.status_code == 204
    r = await client.get("/api/logistica/status")
    assert not any(s["id"] == sid for s in r.json())


@pytest.mark.asyncio
async def test_viewer_can_list_not_edit(
    client: AsyncClient, admin: User, viewer: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)
    r = await client.post("/api/logistica", json={"pedido_bling": "999"})
    assert r.status_code == 201

    auth_as(viewer)
    # view-only: lista, opções, sugestão e status OK
    assert (await client.get("/api/logistica")).status_code == 200
    assert (await client.get("/api/logistica/opcoes")).status_code == 200
    assert (await client.get("/api/logistica/status")).status_code == 200
    r = await client.post("/api/logistica/sugestao", json={"meli_status": {"order_status": "paid"}})
    assert r.status_code == 200
    # mas NÃO cria (nem caso nem status)
    assert (await client.post("/api/logistica", json={"pedido_bling": "hack"})).status_code == 403
    assert (
        await client.post("/api/logistica/status", json={"status_plataforma": "x"})
    ).status_code == 403


@pytest.mark.asyncio
async def test_outsider_forbidden(
    client: AsyncClient, outsider: User, auth_as: Callable[[User | None], None]
):
    auth_as(outsider)
    assert (await client.get("/api/logistica")).status_code == 403


@pytest.mark.asyncio
async def test_patch_not_found(
    client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)
    r = await client.patch(f"/api/logistica/{uuid.uuid4()}", json={"status_bling": "z"})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "logistica_not_found"

    r = await client.patch(f"/api/logistica/status/{uuid.uuid4()}", json={"abrir_chamado": True})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "logistica_status_not_found"


@pytest.mark.asyncio
async def test_sugestao_candidatos_ordenados(
    client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)
    r = await client.post(
        "/api/logistica/sugestao",
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
    r = await client.get("/api/logistica/opcoes")
    assert r.status_code == 200
    body = r.json()
    assert body["field_order"] == logistica_rules.FIELD_ORDER
    assert "cancelled" in body["field_options"]["order_status"]


def test_sugerir_selecao_vazia_retorna_lista_vazia():
    assert logistica_rules.sugerir({}) == []
    assert logistica_rules.sugerir({"order_status": ""}) == []
