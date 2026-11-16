"""Segmentos › DUIMP padrão (migration 0487, Eduardo 16/11/2026): a coluna da
tela grava pelo PATCH de sempre, volta no /tree, e o texto é validado (vazio
apaga; "|", mais de 75 caracteres (o que cabe na linha da etiqueta) e o que a
etiqueta não imprime são recusados; travessão/aspas curvas viram os simples)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Segment

pytestmark = pytest.mark.asyncio


def _perms(*, edit: bool = True) -> dict:
    return {"segmentos": {"view": True, "edit": edit, "delete": False}}


async def _seed_segment(db: AsyncSession, **kw: object) -> Segment:
    seg = Segment(name="Seg", slug=f"seg-{uuid.uuid4().hex[:6]}", **kw)
    db.add(seg)
    await db.commit()
    await db.refresh(seg)
    return seg


def _no(tree: list[dict], sid: str) -> dict:
    for n in tree:
        if n["id"] == sid:
            return n
        if (achou := _no(n.get("children") or [], sid)) is not None:
            return achou
    return None  # type: ignore[return-value]


async def test_patch_grava_limpa_e_aparece_no_tree(client, db, make_user, auth_as):
    user = await make_user(permissions=_perms())
    auth_as(user)
    seg = await _seed_segment(db)

    r = await client.patch(
        f"/api/segments/{seg.id}",
        json={"duimp_padrao": "  produto importado   pela duimp 26BR0001398015-8 "},
    )
    assert r.status_code == 200, r.text
    assert r.json()["duimp_padrao"] == "produto importado pela duimp 26BR0001398015-8"

    tree = (await client.get("/api/segments/tree")).json()
    assert _no(tree, str(seg.id))["duimp_padrao"] == (
        "produto importado pela duimp 26BR0001398015-8"
    )

    r = await client.patch(f"/api/segments/{seg.id}", json={"duimp_padrao": "   "})
    assert r.status_code == 200
    assert r.json()["duimp_padrao"] is None


async def test_patch_recusa_barra_e_texto_longo(client, db, make_user, auth_as):
    user = await make_user(permissions=_perms())
    auth_as(user)
    seg = await _seed_segment(db)

    r = await client.patch(f"/api/segments/{seg.id}", json={"duimp_padrao": "a | b"})
    assert r.status_code == 422
    r = await client.patch(f"/api/segments/{seg.id}", json={"duimp_padrao": "x" * 76})
    assert r.status_code == 422
    assert "duimp_padrao_muito_longo" in r.text
    r = await client.patch(f"/api/segments/{seg.id}", json={"duimp_padrao": "x" * 75})
    assert r.status_code == 200
    for ruim in ("usado 😀", "tab\x00nul"):
        r = await client.patch(f"/api/segments/{seg.id}", json={"duimp_padrao": ruim})
        assert r.status_code == 422, ruim
    # o que o Mac troca sozinho vira o simples (a etiqueta imprimiria "·")
    r = await client.patch(
        f"/api/segments/{seg.id}", json={"duimp_padrao": "mala usada — “sem” DUIMP…"}
    )
    assert r.status_code == 200
    assert r.json()["duimp_padrao"] == 'mala usada - "sem" DUIMP...'


async def test_patch_exige_permissao_de_editar(client, db, make_user, auth_as):
    user = await make_user(permissions=_perms(edit=False))
    auth_as(user)
    seg = await _seed_segment(db)
    r = await client.patch(f"/api/segments/{seg.id}", json={"duimp_padrao": "x"})
    assert r.status_code == 403
