from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.schemas.permissions import Permissions


# Single source of truth for the valid operator-of-stock tags. The UI
# shows labels in PT-BR (CI, PI, RA, SA, SP, Usados, Centro de
# Distribuição, Fake, Mala, Eletro, Insumos) — the stored values are
# always the short slug below.
STOCK_TAGS = ("ci", "pi", "ra", "sa", "sp", "us", "cd", "fake", "mala", "eletro", "insumos")
_STOCK_TAG_SET = frozenset(STOCK_TAGS)


def _normalize_stock_tags(raw: list | None) -> list[str] | None:
    """Trim/lowercase + dedupe + drop unknowns. Empty list becomes
    None so the DB column stays null when the admin clears all tags."""
    if raw is None:
        return None
    out: list[str] = []
    seen: set[str] = set()
    for v in raw:
        if not isinstance(v, str):
            continue
        t = v.strip().lower()
        if t and t in _STOCK_TAG_SET and t not in seen:
            seen.add(t)
            out.append(t)
    return out or None


class UserOut(BaseModel):
    id: str
    open_id: str
    email: EmailStr
    name: str | None = None
    role: str
    status: str
    tuta: str | None = None
    upseller: str | None = None
    bling_login: str | None = None
    adspower: str | None = None
    duoke: str | None = None
    stock_tags: list[str] | None = None
    permissions: dict
    last_login_at: datetime | None = None
    disabled_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class UserListOut(BaseModel):
    items: list[UserOut]
    total: int
    page: int
    per_page: int


class UserCreate(BaseModel):
    email: EmailStr
    name: str | None = None
    tuta: str | None = None
    upseller: str | None = None
    bling_login: str | None = None
    adspower: str | None = None
    duoke: str | None = None
    stock_tags: list[str] | None = None
    permissions: Permissions | None = None


class UserPatch(BaseModel):
    name: str | None = None
    email: EmailStr | None = None
    tuta: str | None = None
    upseller: str | None = None
    bling_login: str | None = None
    adspower: str | None = None
    duoke: str | None = None
    # Pass a list of slugs (any subset of STOCK_TAGS) or [] / null to
    # clear. Backend dedupes / lowercases / drops unknowns.
    stock_tags: list[str] | None = None
    status: str | None = Field(default=None, pattern="^(pending|active|suspended)$")


class PermissionsPatch(BaseModel):
    permissions: Permissions


class MePermissionsOut(BaseModel):
    role: str
    is_admin: bool
    permissions: dict
