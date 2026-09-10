"""Datas Especiais dos Segmentos — CRUD (POST/DELETE /api/segments/{id}/special-dates).

Pedido do Eduardo (01/09/2026): janela de exceção da margem por segmento
("está com margem negativa, aprova"). Aqui cobre só o CRUD + exposição no
tree; o efeito na triagem da Margem (e no auto-hold) é coberto em
test_margens_router.py::test_marketplace_margem_isenta_por_data_especial e
test_margem_auto_hold.py::test_nao_segura_margem_baixa_em_data_especial.
"""

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


async def test_special_date_requer_permissao_edit(client, db, make_user, auth_as):
    user = await make_user(permissions=_perms(edit=False))
    auth_as(user)
    seg = await _seed_segment(db)

    response = await client.post(
        f"/api/segments/{seg.id}/special-dates",
        json={"date_start": "2026-09-01", "date_end": "2026-09-10"},
    )

    assert response.status_code == 403


async def test_cria_lista_e_remove_special_date(client, db, make_user, auth_as):
    """POST guarda a janela (margem em FRAÇÃO, como segments.min_margin); a
    janela aparece no /tree (é de lá que a tela de Segmentos desenha os
    chips); DELETE remove."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    seg = await _seed_segment(db)

    created = await client.post(
        f"/api/segments/{seg.id}/special-dates",
        json={
            "date_start": "2026-09-01",
            "date_end": "2026-09-10",
            "min_margin": "-0.15",
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["segment_id"] == str(seg.id)
    assert body["date_start"] == "2026-09-01"
    assert body["date_end"] == "2026-09-10"
    assert float(body["min_margin"]) == pytest.approx(-0.15)

    tree = await client.get("/api/segments/tree")
    assert tree.status_code == 200
    node = next(n for n in tree.json() if n["id"] == str(seg.id))
    assert [sd["id"] for sd in node["special_dates"]] == [body["id"]]

    deleted = await client.delete(
        f"/api/segments/{seg.id}/special-dates/{body['id']}"
    )
    assert deleted.status_code == 204
    tree2 = await client.get("/api/segments/tree")
    node2 = next(n for n in tree2.json() if n["id"] == str(seg.id))
    assert node2["special_dates"] == []


async def test_special_date_intervalo_invertido_rejeitado(
    client, db, make_user, auth_as
):
    user = await make_user(permissions=_perms())
    auth_as(user)
    seg = await _seed_segment(db)

    response = await client.post(
        f"/api/segments/{seg.id}/special-dates",
        json={"date_start": "2026-09-10", "date_end": "2026-09-01"},
    )

    assert response.status_code == 422


async def test_condicao_especial_por_nome_e_sku_sem_periodo(client, db, make_user, auth_as):
    """Condição Especial (10/09): regra só por nome/SKU, sem período — cria,
    apara espaços, aparece no tree com os campos novos e o período vem nulo."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    seg = await _seed_segment(db)

    r1 = await client.post(
        f"/api/segments/{seg.id}/special-dates",
        json={"nome_contem": "  M3 ", "min_margin": "0.0600"},
    )
    assert r1.status_code == 201, r1.text
    assert r1.json()["nome_contem"] == "M3"
    assert r1.json()["sku_contem"] is None
    assert r1.json()["date_start"] is None and r1.json()["date_end"] is None

    r2 = await client.post(
        f"/api/segments/{seg.id}/special-dates",
        json={"sku_contem": "a001", "date_start": "2026-09-01", "date_end": "2026-09-30"},
    )
    assert r2.status_code == 201, r2.text
    assert r2.json()["sku_contem"] == "a001"
    assert r2.json()["min_margin"] is None

    tree = await client.get("/api/segments/tree")
    assert tree.status_code == 200
    no = next(n for n in tree.json() if n["id"] == str(seg.id))
    regras = {
        (sd["nome_contem"], sd["sku_contem"], sd["date_start"]) for sd in no["special_dates"]
    }
    assert regras == {("M3", None, None), (None, "a001", "2026-09-01")}


async def test_condicao_especial_vazia_ou_periodo_incompleto_rejeitada(
    client, db, make_user, auth_as
):
    user = await make_user(permissions=_perms())
    auth_as(user)
    seg = await _seed_segment(db)

    vazia = await client.post(f"/api/segments/{seg.id}/special-dates", json={"min_margin": "0.06"})
    assert vazia.status_code == 422
    so_espacos = await client.post(
        f"/api/segments/{seg.id}/special-dates", json={"nome_contem": "   ", "sku_contem": ""}
    )
    assert so_espacos.status_code == 422
    incompleto = await client.post(
        f"/api/segments/{seg.id}/special-dates", json={"date_start": "2026-09-01"}
    )
    assert incompleto.status_code == 422


async def test_special_date_404s(client, db, make_user, auth_as):
    """Segmento inexistente no POST e janela inexistente no DELETE → 404."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    seg = await _seed_segment(db)

    ghost = uuid.uuid4()
    post = await client.post(
        f"/api/segments/{ghost}/special-dates",
        json={"date_start": "2026-09-01", "date_end": "2026-09-10"},
    )
    assert post.status_code == 404

    delete = await client.delete(f"/api/segments/{seg.id}/special-dates/{ghost}")
    assert delete.status_code == 404
    assert delete.json()["detail"]["code"] == "special_date_not_found"
