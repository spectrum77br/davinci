from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.schemas.permissions import Permissions


_STOCK_TAG_PATTERN = "^(ci|pi|ra|sa|sp)$"


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
    stock_tag: str | None = None
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
    stock_tag: str | None = Field(default=None, pattern=_STOCK_TAG_PATTERN)
    permissions: Permissions | None = None


class UserPatch(BaseModel):
    name: str | None = None
    email: EmailStr | None = None
    tuta: str | None = None
    upseller: str | None = None
    bling_login: str | None = None
    adspower: str | None = None
    duoke: str | None = None
    # `Field(default=None, pattern=...)` rejects garbage but allows None
    # — the UI sends "" or null to clear the tag, both treated as null.
    stock_tag: str | None = Field(default=None)
    status: str | None = Field(default=None, pattern="^(pending|active|suspended)$")


class PermissionsPatch(BaseModel):
    permissions: Permissions


class MePermissionsOut(BaseModel):
    role: str
    is_admin: bool
    permissions: dict
