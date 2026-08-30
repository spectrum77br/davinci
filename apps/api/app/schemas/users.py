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


def _normalize_sales_teams(raw: list | None) -> list[int] | None:
    """Coerce ints/strings → positive ints; dedupe; sort ascending.
    Empty list becomes None so the DB stays null when the admin clears
    all teams. Espelha _normalize_stock_tags trocando "lista de slugs"
    por "lista de números inteiros positivos"."""
    if raw is None:
        return None
    out: list[int] = []
    seen: set[int] = set()
    for v in raw:
        try:
            if isinstance(v, bool):  # bool é subclasse de int, descartar
                continue
            n = int(v)
        except (TypeError, ValueError):
            continue
        if n > 0 and n not in seen:
            seen.add(n)
            out.append(n)
    return sorted(out) or None


def _normalize_marketing_teams(raw: list | None) -> list[str] | None:
    """Nomes livres de equipe de marketing: strip, descarta vazios,
    dedupe case-insensitive (mantém a grafia da primeira ocorrência),
    ordena. Lista vazia vira None — espelha _normalize_sales_teams."""
    if raw is None:
        return None
    out: dict[str, str] = {}
    for v in raw:
        if not isinstance(v, str):
            continue
        t = v.strip()[:64]
        if t and t.lower() not in out:
            out[t.lower()] = t
    return sorted(out.values(), key=str.lower) or None


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
    threema: str | None = None
    stock_tags: list[str] | None = None
    sales_teams: list[int] | None = None
    marketing_teams: list[str] | None = None
    permissions: dict
    # Apenas indica se há senha definida — o hash nunca sai da API.
    has_password: bool = False
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
    # Senha inicial opcional. Se omitida, o usuário entra por OTP até um
    # admin definir uma senha. Validada contra password_min_length no router.
    password: str | None = Field(default=None, max_length=128)
    tuta: str | None = None
    upseller: str | None = None
    bling_login: str | None = None
    adspower: str | None = None
    duoke: str | None = None
    threema: str | None = None
    stock_tags: list[str] | None = None
    sales_teams: list[int] | None = None
    marketing_teams: list[str] | None = None
    permissions: Permissions | None = None


class PasswordSet(BaseModel):
    password: str = Field(max_length=128)


class UserPatch(BaseModel):
    name: str | None = None
    email: EmailStr | None = None
    tuta: str | None = None
    upseller: str | None = None
    bling_login: str | None = None
    adspower: str | None = None
    duoke: str | None = None
    threema: str | None = None
    # Pass a list of slugs (any subset of STOCK_TAGS) or [] / null to
    # clear. Backend dedupes / lowercases / drops unknowns.
    stock_tags: list[str] | None = None
    # Pass a list of positive integers or [] / null to clear. Backend
    # dedupes/sorts/drops non-positives.
    sales_teams: list[int] | None = None
    marketing_teams: list[str] | None = None
    status: str | None = Field(default=None, pattern="^(pending|active|suspended)$")


class PermissionsPatch(BaseModel):
    permissions: Permissions


class MePermissionsOut(BaseModel):
    role: str
    is_admin: bool
    permissions: dict
