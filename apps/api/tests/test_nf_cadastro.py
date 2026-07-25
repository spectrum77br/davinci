"""Cadastros do sistema de NF automáticas (admin-only): Faturador, Etiqueta,
Impressão. Espelham a aba NF de `tarefa 25.xlsx` + os áudios da spec.

- Faturador: senha nunca volta (só `has_senha`); percentual/NCM/SKU-fonte etc.
- Etiqueta: plataforma + modo (amazon/upseller/NULL).
- Impressão: tipo + flags usa_etiqueta/usa_declaracao/usa_nota + visualização.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UserRole, UserStatus


@pytest_asyncio.fixture
async def admin(db: AsyncSession) -> User:
    email = f"adm-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(
        open_id=f"email:{email}",
        email=email,
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def normal(db: AsyncSession) -> User:
    email = f"usr-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(
        open_id=f"email:{email}",
        email=email,
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest.mark.asyncio
async def test_faturador_crud_e_senha_nunca_volta(
    client: AsyncClient,
    admin: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(admin)

    r = await client.post(
        "/api/nf-cadastro/faturadores",
        json={
            "nome": "bling exclusivo",
            "modo": "bling",
            "nf_cheia": False,
            "percentual": "0.1",
            "sku_fonte": "a001",
            "nome_fonte": "embalagem",
            "ncm": "4202.12.10",
            "usuario": "joana",
            "senha": "segredo123",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    fid = body["id"]
    assert body["nome"] == "bling exclusivo"
    assert body["has_senha"] is True
    assert "senha" not in body and "senha_enc" not in body

    # Lista.
    r = await client.get("/api/nf-cadastro/faturadores")
    assert any(f["id"] == fid for f in r.json())

    # Patch: senha "" limpa; texto seta.
    r = await client.patch(
        f"/api/nf-cadastro/faturadores/{fid}", json={"senha": ""}
    )
    assert r.status_code == 200
    assert r.json()["has_senha"] is False

    r = await client.patch(
        f"/api/nf-cadastro/faturadores/{fid}",
        json={"percentual": "2", "senha": "outra"},
    )
    assert r.status_code == 200
    assert r.json()["has_senha"] is True
    assert r.json()["percentual"] == "2.000"

    # Remove.
    r = await client.delete(f"/api/nf-cadastro/faturadores/{fid}")
    assert r.status_code == 204
    r = await client.get("/api/nf-cadastro/faturadores")
    assert not any(f["id"] == fid for f in r.json())


@pytest.mark.asyncio
async def test_etiqueta_crud(
    client: AsyncClient,
    admin: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(admin)

    r = await client.post(
        "/api/nf-cadastro/etiquetas",
        json={"plataforma": "Amazon", "modo": "amazon", "ads_power": "perfil-1"},
    )
    assert r.status_code == 201, r.text
    eid = r.json()["id"]
    assert r.json()["modo"] == "amazon"

    # modo NULL (plataforma ainda sem regra).
    r = await client.post(
        "/api/nf-cadastro/etiquetas",
        json={"plataforma": "Shein", "modo": ""},
    )
    assert r.status_code == 201
    assert r.json()["modo"] is None

    r = await client.patch(
        f"/api/nf-cadastro/etiquetas/{eid}", json={"modo": "upseller"}
    )
    assert r.status_code == 200
    assert r.json()["modo"] == "upseller"

    r = await client.delete(f"/api/nf-cadastro/etiquetas/{eid}")
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_impressao_crud(
    client: AsyncClient,
    admin: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(admin)

    r = await client.post(
        "/api/nf-cadastro/impressoes",
        json={
            "tipo": "correios",
            "usa_etiqueta": True,
            "usa_nota": True,
            "visualizacao": "remetente=destinatário; apaga dados NF",
        },
    )
    assert r.status_code == 201, r.text
    iid = r.json()["id"]
    assert r.json()["usa_etiqueta"] is True
    assert r.json()["usa_declaracao"] is False
    assert r.json()["usa_nota"] is True

    r = await client.patch(
        f"/api/nf-cadastro/impressoes/{iid}",
        json={"usa_declaracao": True, "usa_nota": False},
    )
    assert r.status_code == 200
    assert r.json()["usa_declaracao"] is True
    assert r.json()["usa_nota"] is False

    r = await client.delete(f"/api/nf-cadastro/impressoes/{iid}")
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_non_admin_forbidden(
    client: AsyncClient,
    normal: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(normal)
    for path in ("faturadores", "etiquetas", "impressoes"):
        r = await client.get(f"/api/nf-cadastro/{path}")
        assert r.status_code == 403, path


@pytest.mark.asyncio
async def test_patch_not_found(
    client: AsyncClient,
    admin: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(admin)
    r = await client.patch(
        f"/api/nf-cadastro/faturadores/{uuid.uuid4()}", json={"nome": "x"}
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "nf_faturador_not_found"

    r = await client.patch(
        f"/api/nf-cadastro/etiquetas/{uuid.uuid4()}", json={"modo": "x"}
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "nf_etiqueta_not_found"

    r = await client.patch(
        f"/api/nf-cadastro/impressoes/{uuid.uuid4()}", json={"tipo": "x"}
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "nf_impressao_not_found"
