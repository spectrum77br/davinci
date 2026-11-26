"""M-72 (26/11/2026): GET /api/pricing/mega/products/{id} — as colunas Fotos e Embalagens de UMA
linha da Tabela de Preços, para o cadastro do produto no Produtos 2 mostrar o mesmo selo e a
moldura do DaVinci abrir a mesma janela (MidiasProdutoDrawer).

Só leitura, com a mesma permissão de ver a Tabela de Preços › Produtos; `pode_editar` segue a
regra da tela ("—" no lugar do "+ Adicionar" para quem só vê) e `irmaos` são as linhas que dividem
a pasta (a janela avisa que o envio aparece nelas também).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PricingProduct, Segment, User, UserRole, UserStatus
from app.models.enums import Department

PERM_FULL = {"tabela_precos_produtos": {"view": True, "edit": True, "delete": True}}
PERM_VIEW = {"tabela_precos_produtos": {"view": True, "edit": False, "delete": False}}


async def _usuario(db: AsyncSession, perms: dict, role: UserRole = UserRole.USER) -> User:
    email = f"m72-{uuid.uuid4().hex[:8]}@davinci-test.com"
    u = User(
        open_id=f"email:{email}",
        email=email,
        role=role,
        status=UserStatus.ACTIVE,
        permissions=perms,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _folha(db: AsyncSession) -> Segment:
    raiz = Segment(name="celular", slug=f"celular-{uuid.uuid4().hex[:6]}", sort_order=0)
    db.add(raiz)
    await db.flush()
    folha = Segment(name="Regular", slug=f"regular-{uuid.uuid4().hex[:6]}", parent_id=raiz.id)
    db.add(folha)
    await db.flush()
    return folha


async def _linha(db: AsyncSession, dono: User, folha: Segment, sku: str, **extra: Any):
    p = PricingProduct(
        user_id=dono.id,
        segment_id=folha.id,
        sku=sku,
        name=f"Produto {sku}",
        department=Department.CELULAR,
        **extra,
    )
    db.add(p)
    await db.commit()
    return p


async def test_a_linha_traz_o_selo_as_irmas_e_quem_pode_editar(
    client: AsyncClient, db: AsyncSession, auth_as: Callable[[User | None], None]
):
    dono = await _usuario(db, PERM_FULL)
    folha = await _folha(db)
    pasta = "/Celular/Uranyx F105"
    linha = await _linha(
        db,
        dono,
        folha,
        "dg019,dg020",
        fotos_path=pasta,
        fotos_url="https://mega.nz/folder/f105",
        fotos_count=3,
        videos_count=1,
        embalagens_path=f"{pasta}/Embalagens",
        embalagens_url="https://mega.nz/folder/f105-emb",
        embalagens_count=2,
    )
    await _linha(db, dono, folha, "dg021", fotos_path=pasta)
    await _linha(db, dono, folha, "dg018", fotos_path=pasta)
    await _linha(db, dono, folha, "a020", fotos_path="/Celular/Outra")

    auth_as(dono)
    r = await client.get(f"/api/pricing/mega/products/{linha.id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == str(linha.id) and body["sku"] == "dg019,dg020"
    assert body["name"] == "Produto dg019,dg020" and body["department"] == "celular"
    assert body["fotos_path"] == pasta and body["fotos_url"] == "https://mega.nz/folder/f105"
    assert (body["fotos_count"], body["videos_count"], body["embalagens_count"]) == (3, 1, 2)
    assert body["embalagens_path"] == f"{pasta}/Embalagens"
    assert body["embalagens_url"] == "https://mega.nz/folder/f105-emb"
    assert [i["sku"] for i in body["irmaos"]] == ["dg018", "dg021"]
    assert body["pode_editar"] is True

    # Quem só vê lê o mesmo selo, sem poder enviar ("—" onde não há pasta).
    auth_as(await _usuario(db, PERM_VIEW))
    r = await client.get(f"/api/pricing/mega/products/{linha.id}")
    assert r.status_code == 200 and r.json()["pode_editar"] is False

    # O administrador (quem entra no Produtos 2) edita.
    auth_as(await _usuario(db, {}, UserRole.ADMIN))
    assert (await client.get(f"/api/pricing/mega/products/{linha.id}")).json()["pode_editar"]


async def test_linha_sem_pasta_sem_permissao_e_linha_que_nao_existe(
    client: AsyncClient, db: AsyncSession, auth_as: Callable[[User | None], None]
):
    dono = await _usuario(db, PERM_FULL)
    folha = await _folha(db)
    linha = await _linha(db, dono, folha, "z2821")
    auth_as(dono)
    body = (await client.get(f"/api/pricing/mega/products/{linha.id}")).json()
    assert body["fotos_path"] is None and body["fotos_url"] is None
    assert body["fotos_count"] is None and body["embalagens_path"] is None
    assert body["irmaos"] == []
    assert (await client.get(f"/api/pricing/mega/products/{uuid.uuid4()}")).status_code == 404

    auth_as(await _usuario(db, {}))
    r = await client.get(f"/api/pricing/mega/products/{linha.id}")
    assert r.status_code == 403
