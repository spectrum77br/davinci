"""Fase 3 da aba Kit: criação/atualização de pricing_product.

Cobertura:
  * helpers (extract_sizes, has_accessories, build_kit_pricing_sku,
    kit_pricing_segment_slug, kit_pricing_name) — pure functions
  * _ensure_pricing_product_for_kit:
      - cria nova row quando não existe
      - atualiza sku adicionando piece quando já existe
      - não duplica pieces
      - associa o segment correto (12, 18-20, 24-acima, acessorios)
      - usa "{family} + Acessorios" pra variations com acessórios
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ImportKitBase,
    ImportKitMark,
    ImportKitVariation,
    Segment,
    User,
    UserRole,
    UserStatus,
)
from app.services.bling_kit_create import _ensure_pricing_product_for_kit
from app.services.importacao_naming import (
    build_kit_pricing_sku,
    extract_sizes,
    has_accessories,
    kit_pricing_name,
    kit_pricing_segment_slug,
)

# ── Pure helper tests ────────────────────────────────────────────────


def test_extract_sizes_simple():
    assert extract_sizes("8+18") == [8, 18]


def test_extract_sizes_comma():
    assert extract_sizes("12,14,16") == [12, 14, 16]


def test_extract_sizes_with_accessories_ignored():
    assert extract_sizes("12+20+24+a075+bp002+a076") == [12, 20, 24]


def test_extract_sizes_complete_kit():
    assert extract_sizes("8+12+13+18+20+24") == [8, 12, 13, 18, 20, 24]


def test_extract_sizes_empty():
    assert extract_sizes("") == []


def test_has_accessories_true():
    assert has_accessories("12+20+24+a075") is True


def test_has_accessories_false():
    assert has_accessories("8+18") is False
    assert has_accessories("12,14,16") is False


def test_build_sku_simple():
    assert build_kit_pricing_sku("b045", "8+18") == "b045.8.18"


def test_build_sku_three_sizes():
    assert build_kit_pricing_sku("b045", "8+12+20") == "b045.8.12.20"


def test_build_sku_with_accessories():
    assert build_kit_pricing_sku(
        "b045", "12+20+24+a075+bp002+a076",
    ) == "b045.12.20.24+a075+bp002+a076"


def test_build_sku_complete_kit_special_case():
    """A variation 8+12+13+18+20+24 (kit completo) usa SKU bare base."""
    assert build_kit_pricing_sku("b045", "8+12+13+18+20+24") == "b045"


def test_build_sku_comma_separated_sizes():
    """12,14,16 — vírgulas viram pontos."""
    assert build_kit_pricing_sku("b045", "12,14,16") == "b045.12.14.16"


def test_segment_slug_maleta_8_goes_to_acessorios():
    assert kit_pricing_segment_slug("8") == "acessorios"


def test_segment_slug_12():
    assert kit_pricing_segment_slug("12") == "12"


def test_segment_slug_8_12_still_12():
    """max=12 → segment '12'."""
    assert kit_pricing_segment_slug("8+12") == "12"


def test_segment_slug_18_20():
    assert kit_pricing_segment_slug("18") == "18-20"
    assert kit_pricing_segment_slug("20") == "18-20"
    assert kit_pricing_segment_slug("12+20") == "18-20"


def test_segment_slug_24_acima():
    assert kit_pricing_segment_slug("24") == "24-acima"
    assert kit_pricing_segment_slug("12+24") == "24-acima"
    assert kit_pricing_segment_slug("8+12+13+18+20+24") == "24-acima"


def test_segment_slug_accessories_always_18_20():
    assert kit_pricing_segment_slug("12+20+24+a075+bp003+a076") == "18-20"


def test_name_mala_when_max_18():
    assert kit_pricing_name("M5 mista", "8+18") == "M5 mista mala 8+18"


def test_name_maleta_when_max_12():
    assert kit_pricing_name("M5 mista", "12") == "M5 mista maleta 12"


def test_name_maleta_8():
    assert kit_pricing_name("M5 mista", "8") == "M5 mista maleta 8"


def test_name_acessorios_uses_family_prefix():
    """+ Acessorios usa só o primeiro token do modelo."""
    assert kit_pricing_name(
        "M5 mista", "12+20+24+a075+bp003+a076",
    ) == "M5 + Acessorios"
    assert kit_pricing_name(
        "ME1 executivo", "12+20+24+a075+bp003+a076",
    ) == "ME1 + Acessorios"
    assert kit_pricing_name(
        "P6 minecraft", "8+12+20+24+a075+bp003+a076",
    ) == "P6 + Acessorios"


# ── _ensure_pricing_product_for_kit (integration) ──────────────────


@pytest_asyncio.fixture
async def segments_setup(db: AsyncSession) -> dict[str, Any]:
    """Cria segmentos 'mala' + filhos {12, 18-20, 24-acima, acessorios}."""
    mala = Segment(name="Mala", slug="mala", sort_order=1)
    db.add(mala)
    await db.commit()
    await db.refresh(mala)
    children = {}
    seg_labels = [
        ("12", "12\""),
        ("18-20", "18\" e 20\""),
        ("24-acima", "24\" acima"),
        ("acessorios", "Acessórios"),
    ]
    for slug, label in seg_labels:
        s = Segment(name=label, slug=slug, parent_id=mala.id, sort_order=1)
        db.add(s)
        children[slug] = s
    await db.commit()
    for s in children.values():
        await db.refresh(s)
    return {"mala": mala, **children}


@pytest_asyncio.fixture
async def owner_user(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:o-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"o-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
        permissions={},
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


_make_mark_setup_counter = 0


async def _make_mark_setup(
    db: AsyncSession, *, modelo: str, sku_base: str, cor: str, variation_code: str,
) -> dict[str, Any]:
    global _make_mark_setup_counter  # noqa: PLW0603
    _make_mark_setup_counter += 1
    # Reusa variation existente quando o code já foi inserido (mesma
    # variation em testes que adicionam várias cores).
    v = (await db.execute(
        select(ImportKitVariation).where(ImportKitVariation.code == variation_code)
    )).scalar_one_or_none()
    if v is None:
        v = ImportKitVariation(
            code=variation_code, label=variation_code,
            ordem=_make_mark_setup_counter, highlight=False,
        )
        db.add(v)
    b = ImportKitBase(
        modelo_bling=modelo, sku_base=sku_base, cor=cor,
        ordem=_make_mark_setup_counter,
    )
    db.add(b)
    await db.commit()
    await db.refresh(v)
    await db.refresh(b)
    mark = ImportKitMark(
        base_id=b.id, variation_id=v.id, bling_sync_status="sent",
        bling_product_id=12345,
    )
    db.add(mark)
    await db.commit()
    await db.refresh(mark)
    return {"base": b, "variation": v, "mark": mark}


@pytest.mark.asyncio
async def test_ensure_creates_new_pricing_product(
    db: AsyncSession, owner_user: User, segments_setup: dict[str, Any],
):
    """Primeira mark do tipo cria uma row nova."""
    s = await _make_mark_setup(
        db, modelo="M5 mista", sku_base="b045", cor="preto", variation_code="8+18",
    )
    pp = await _ensure_pricing_product_for_kit(
        db, s["mark"], s["base"], s["variation"], owner_user_id=owner_user.id,
    )
    await db.commit()
    await db.refresh(pp)
    assert pp.name == "M5 mista mala 8+18"
    assert pp.sku == "b045.8.18"
    assert pp.segment_id == segments_setup["18-20"].id
    assert pp.user_id == owner_user.id


@pytest.mark.asyncio
async def test_ensure_appends_sku_when_existing(
    db: AsyncSession, owner_user: User, segments_setup: dict[str, Any],
):
    """Segunda cor da mesma família reusa a row, anexando o piece ao sku."""
    # Primeiro b045
    s1 = await _make_mark_setup(
        db, modelo="M5 mista", sku_base="b045", cor="preto", variation_code="8+18",
    )
    pp1 = await _ensure_pricing_product_for_kit(
        db, s1["mark"], s1["base"], s1["variation"], owner_user_id=owner_user.id,
    )
    await db.commit()
    # Segundo b046 (mesma família + variation)
    s2 = await _make_mark_setup(
        db, modelo="M5 mista", sku_base="b046", cor="prata", variation_code="8+18",
    )
    pp2 = await _ensure_pricing_product_for_kit(
        db, s2["mark"], s2["base"], s2["variation"], owner_user_id=owner_user.id,
    )
    await db.commit()
    assert pp1.id == pp2.id
    assert pp2.sku == "b045.8.18,b046.8.18"


@pytest.mark.asyncio
async def test_ensure_no_duplicate_sku(
    db: AsyncSession, owner_user: User, segments_setup: dict[str, Any],
):
    """Reprocessar a MESMA mark não duplica o piece."""
    s = await _make_mark_setup(
        db, modelo="M5 mista", sku_base="b045", cor="preto", variation_code="8+18",
    )
    await _ensure_pricing_product_for_kit(
        db, s["mark"], s["base"], s["variation"], owner_user_id=owner_user.id,
    )
    await db.commit()
    pp2 = await _ensure_pricing_product_for_kit(
        db, s["mark"], s["base"], s["variation"], owner_user_id=owner_user.id,
    )
    await db.commit()
    await db.refresh(pp2)
    # Piece 'b045.8.18' aparece só uma vez
    assert pp2.sku.count("b045.8.18") == 1


@pytest.mark.asyncio
async def test_ensure_acessorios_uses_family_prefix_and_18_20_segment(
    db: AsyncSession, owner_user: User, segments_setup: dict[str, Any],
):
    """Variation com acessórios usa nome 'M5 + Acessorios' e segment 18-20."""
    s = await _make_mark_setup(
        db, modelo="M5 mista", sku_base="b045", cor="preto",
        variation_code="12+20+24+a075+bp003+a076",
    )
    pp = await _ensure_pricing_product_for_kit(
        db, s["mark"], s["base"], s["variation"], owner_user_id=owner_user.id,
    )
    await db.commit()
    assert pp.name == "M5 + Acessorios"
    assert pp.sku == "b045.12.20.24+a075+bp003+a076"
    assert pp.segment_id == segments_setup["18-20"].id


@pytest.mark.asyncio
async def test_ensure_complete_kit_uses_bare_base_sku(
    db: AsyncSession, owner_user: User, segments_setup: dict[str, Any],
):
    """Variation 8+12+13+18+20+24 → SKU é o base puro."""
    s = await _make_mark_setup(
        db, modelo="M5 mista", sku_base="b045", cor="preto",
        variation_code="8+12+13+18+20+24",
    )
    pp = await _ensure_pricing_product_for_kit(
        db, s["mark"], s["base"], s["variation"], owner_user_id=owner_user.id,
    )
    await db.commit()
    assert pp.sku == "b045"
    assert pp.name == "M5 mista mala 8+12+13+18+20+24"
    assert pp.segment_id == segments_setup["24-acima"].id


@pytest.mark.asyncio
async def test_ensure_maleta_12_uses_12_segment(
    db: AsyncSession, owner_user: User, segments_setup: dict[str, Any],
):
    s = await _make_mark_setup(
        db, modelo="M5 mista", sku_base="b045", cor="preto", variation_code="12",
    )
    pp = await _ensure_pricing_product_for_kit(
        db, s["mark"], s["base"], s["variation"], owner_user_id=owner_user.id,
    )
    await db.commit()
    assert pp.name == "M5 mista maleta 12"
    assert pp.segment_id == segments_setup["12"].id


@pytest.mark.asyncio
async def test_ensure_maleta_8_uses_acessorios_segment(
    db: AsyncSession, owner_user: User, segments_setup: dict[str, Any],
):
    """Existing prod: maleta 8 cai em segment 'acessorios'."""
    s = await _make_mark_setup(
        db, modelo="M5 mista", sku_base="b045", cor="preto", variation_code="8",
    )
    pp = await _ensure_pricing_product_for_kit(
        db, s["mark"], s["base"], s["variation"], owner_user_id=owner_user.id,
    )
    await db.commit()
    assert pp.name == "M5 mista maleta 8"
    assert pp.segment_id == segments_setup["acessorios"].id


@pytest.mark.asyncio
async def test_ensure_24_acima_segment(
    db: AsyncSession, owner_user: User, segments_setup: dict[str, Any],
):
    s = await _make_mark_setup(
        db, modelo="M5 mista", sku_base="b045", cor="preto", variation_code="12+24",
    )
    pp = await _ensure_pricing_product_for_kit(
        db, s["mark"], s["base"], s["variation"], owner_user_id=owner_user.id,
    )
    await db.commit()
    assert pp.segment_id == segments_setup["24-acima"].id


@pytest.mark.asyncio
async def test_ensure_raises_when_segment_missing(
    db: AsyncSession, owner_user: User,
):
    """Sem segments seedados → ValueError."""
    s = await _make_mark_setup(
        db, modelo="M5 mista", sku_base="b045", cor="preto", variation_code="8+18",
    )
    with pytest.raises(ValueError, match="segment not found"):
        await _ensure_pricing_product_for_kit(
            db, s["mark"], s["base"], s["variation"], owner_user_id=owner_user.id,
        )
