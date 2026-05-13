from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator


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


class SegmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID | None
    parent_id: UUID | None
    name: str
    slug: str
    sort_order: int
    active: bool
    created_at: datetime
    updated_at: datetime


class SegmentTreeNode(SegmentOut):
    children: list["SegmentTreeNode"] = []


SegmentTreeNode.model_rebuild()


__all__ = ["SegmentCreate", "SegmentPatch", "SegmentOut", "SegmentTreeNode"]
