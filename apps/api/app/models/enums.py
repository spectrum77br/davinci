from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "admin"
    USER = "user"


class UserStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"


class Marketplace(StrEnum):
    ML = "ml"
    SHOPEE = "shopee"
    AMAZON = "amazon"
    ALIEXPRESS = "aliexpress"
    TEMU = "temu"
    TIKTOK = "tiktok"
    SHEIN = "shein"
    MAGALU = "magalu"
    SITE = "site"


MARKETPLACES: tuple[str, ...] = tuple(m.value for m in Marketplace)


class StoreStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    CLOSING = "closing"
    BANNED = "banned"
    PENDING = "pending"
    UNDER_REVIEW = "under_review"


class CadastroTipo(StrEnum):
    FONE = "fone"
    EMAIL = "email"
    DOMINIO = "dominio"


class CadastroStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    EXCLUDED = "excluded"


class IntegrationPlatform(StrEnum):
    BLING = "bling"
    ML = "ml"
    SHOPEE = "shopee"
    AMAZON = "amazon"


PLATFORMS: tuple[str, ...] = tuple(p.value for p in IntegrationPlatform)
