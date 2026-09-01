from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


def _slugify(v: str) -> str:
    s = v.strip().lower()
    out = []
    prev_dash = False
    for ch in s:
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif ch in (" ", "-", "_", "/", "."):
            if not prev_dash and out:
                out.append("-")
                prev_dash = True
    return "".join(out).strip("-")


class SegmentCreate(BaseModel):
    name: str
    slug: str | None = None
    parent_id: UUID | None = None
    sort_order: int = 0
    active: bool = True
    min_margin: Decimal | None = None
    altura: Decimal | None = None
    largura: Decimal | None = None
    comprimento: Decimal | None = None
    peso: Decimal | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _v_name(cls, v: Any) -> str:
        if v is None:
            raise ValueError("name_required")
        s = str(v).strip()
        if not s:
            raise ValueError("name_required")
        if len(s) > 128:
            raise ValueError("name_too_long")
        return s

    @field_validator("slug", mode="before")
    @classmethod
    def _v_slug(cls, v: Any) -> str | None:
        if v is None or v == "":
            return None
        s = _slugify(str(v))
        if not s:
            raise ValueError("slug_invalid")
        if len(s) > 128:
            raise ValueError("slug_too_long")
        return s


class SegmentPatch(BaseModel):
    name: str | None = None
    slug: str | None = None
    parent_id: UUID | None = None
    sort_order: int | None = None
    active: bool | None = None
    min_margin: Decimal | None = None
    altura: Decimal | None = None
    largura: Decimal | None = None
    comprimento: Decimal | None = None
    peso: Decimal | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _v_name(cls, v: Any) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            raise ValueError("name_required")
        if len(s) > 128:
            raise ValueError("name_too_long")
        return s

    @field_validator("slug", mode="before")
    @classmethod
    def _v_slug(cls, v: Any) -> str | None:
        if v is None:
            return None
        s = _slugify(str(v))
        if not s:
            raise ValueError("slug_invalid")
        if len(s) > 128:
            raise ValueError("slug_too_long")
        return s


class SegmentSpecialDateCreate(BaseModel):
    """Datas Especiais: janela de exceção da margem (datas BRT, inclusivas).

    `min_margin` em FRAÇÃO (mesma escala de segments.min_margin: -0.15 =
    -15%); NULL = aprova qualquer margem no período.
    """

    date_start: date
    date_end: date
    min_margin: Decimal | None = None

    @model_validator(mode="after")
    def _v_range(self) -> "SegmentSpecialDateCreate":
        if self.date_end < self.date_start:
            raise ValueError("date_range_invalid")
        return self


class SegmentSpecialDateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    segment_id: UUID
    date_start: date
    date_end: date
    min_margin: Decimal | None = None


class SegmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID | None
    parent_id: UUID | None
    name: str
    slug: str
    sort_order: int
    active: bool
    min_margin: Decimal | None = None
    altura: Decimal | None = None
    largura: Decimal | None = None
    comprimento: Decimal | None = None
    peso: Decimal | None = None
    special_dates: list[SegmentSpecialDateOut] = []
    created_at: datetime
    updated_at: datetime


class SegmentTreeNode(SegmentOut):
    children: list["SegmentTreeNode"] = []


SegmentTreeNode.model_rebuild()


__all__ = [
    "SegmentCreate",
    "SegmentOut",
    "SegmentPatch",
    "SegmentSpecialDateCreate",
    "SegmentSpecialDateOut",
    "SegmentTreeNode",
]
