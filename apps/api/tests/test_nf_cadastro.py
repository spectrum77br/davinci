"""Cadastros do sistema de NF automáticas (admin-only): Faturador, Etiqueta,
Impressão. Espelham a aba NF de `tarefa 25.xlsx` + os áudios da spec.

- Faturador: senha nunca volta (só `has_senha`); percentual/NCM/SKU-fonte etc.
- Etiqueta: plataforma + modo (amazon/upseller/NULL).
- Impressão: tipo + flags usa_etiqueta/usa_declaracao/usa_nota + visualização.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from collections.abc import Callable

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingOrder,
    NfFaturador,
    SituacaoBling,
    StoreInfo,
    User,
    UserRole,
    UserStatus,
)


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

    # Revelar a senha salva (botão do olho) devolve o texto descriptografado.
    r = await client.get(f"/api/nf-cadastro/faturadores/{fid}/senha")
    assert r.status_code == 200, r.text
    assert r.json()["senha"] == "segredo123"

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
async def test_catalogo_mala_crud(
    client: AsyncClient,
    admin: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(admin)

    # Casamento é automático pela família (modelo/tamanho); NCM default.
    r = await client.post(
        "/api/nf-cadastro/catalogo-mala",
        json={"modelo": "abs", "tamanho": "20", "valor": "161.00"},
    )
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert r.json()["modelo"] == "abs"
    assert r.json()["tamanho"] == "20"
    assert r.json()["valor"] == "161.00"
    assert r.json()["ncm"] == "4202.12.10"

    r = await client.get("/api/nf-cadastro/catalogo-mala")
    assert any(c["id"] == cid for c in r.json())

    # Ajusta modelo/tamanho/valor.
    r = await client.patch(
        f"/api/nf-cadastro/catalogo-mala/{cid}",
        json={"modelo": "pp", "tamanho": "24", "valor": "176.40"},
    )
    assert r.status_code == 200
    assert r.json()["modelo"] == "pp"
    assert r.json()["tamanho"] == "24"
    assert r.json()["valor"] == "176.40"

    r = await client.delete(f"/api/nf-cadastro/catalogo-mala/{cid}")
    assert r.status_code == 204
    r = await client.get("/api/nf-cadastro/catalogo-mala")
    assert not any(c["id"] == cid for c in r.json())


@pytest.mark.asyncio
async def test_non_admin_forbidden(
    client: AsyncClient,
    normal: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(normal)
    for path in ("faturadores", "etiquetas", "impressoes", "catalogo-mala"):
        r = await client.get(f"/api/nf-cadastro/{path}")
        assert r.status_code == 403, path


@pytest.mark.asyncio
async def test_painel_faturamento(
    db: AsyncSession,
    client: AsyncClient,
    admin: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(admin)

    sit = SituacaoBling(id=999001, nome="Em andamento")
    db.add(sit)

    fat = NfFaturador(nome="bling avulso", modo="bling")
    db.add(fat)
    await db.flush()

    # Loja COM cadastro de NF (aparece no painel).
    info = StoreInfo(
        user_id=admin.id,
        platform="amazon",
        account_name="loja-amz",
        bling_store_id="880011",
        nf_faturador_id=fat.id,
    )
    db.add(info)
    # Loja SEM cadastro de NF (não deve aparecer).
    info_sem = StoreInfo(
        user_id=admin.id,
        platform="shopee",
        account_name="loja-shp",
        bling_store_id="880022",
    )
    db.add(info_sem)
    await db.flush()

    db.add(
        BlingOrder(
            numero="700001",
            numeroloja="123-ABC",
            data=datetime.now(UTC),
            loja="880011",
            situacao=str(sit.id),
        )
    )
    db.add(
        BlingOrder(
            numero="700002",
            data=datetime.now(UTC),
            loja="880022",
            situacao=str(sit.id),
        )
    )
    await db.commit()

    r = await client.get("/api/nf-cadastro/faturamento")
    assert r.status_code == 200, r.text
    rows = r.json()
    pedidos = {row["pedido_bling"]: row for row in rows}
    # Só o pedido da loja com cadastro de NF.
    assert "700001" in pedidos
    assert "700002" not in pedidos
    row = pedidos["700001"]
    assert row["plataforma"] == "Amazon"
    assert row["conta"] == "loja-amz"
    assert row["status_bling"] == "Em andamento"
    assert row["pedido_marketplace"] == "123-ABC"
    # Sem linha em nf_faturamento → tudo pendente.
    assert row["status_faturamento"] == "pendente"
    assert row["status_etiqueta"] == "pendente"
    assert row["status_impressao"] == "pendente"


@pytest.mark.asyncio
async def test_painel_faturamento_non_admin_forbidden(
    client: AsyncClient,
    normal: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(normal)
    r = await client.get("/api/nf-cadastro/faturamento")
    assert r.status_code == 403


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

    r = await client.patch(
        f"/api/nf-cadastro/catalogo-mala/{uuid.uuid4()}", json={"modelo": "x"}
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "nf_catalogo_mala_not_found"
