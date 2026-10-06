"""Classificação das lojas usada em Lojas e Faturamento.

Tipos de contas de preço respeitam o vínculo explícito; somente contas sem
vínculo usam a correspondência legada de plataforma e nome da loja.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.auth import user_scope
from app.models import PricingAccount, Segment, StoreInfo, User

STORE_TO_PRICING_PLATFORM = {
    "ml": "mercadolivre",
    "mercadolivre": "mercadolivre",
    "shopee": "shopee",
    "amazon": "amazon",
    "tiktok": "tiktok",
    "temu": "temu",
    "aliexpress": "aliexpress",
    "magalu": "magalu",
    "shein": "shein",
}

# These platforms support registration without a price table. Choosing a
# product type must not create pricing accounts or invented fee rules.
MANUAL_DEPARTMENT_PLATFORMS = frozenset({"site", "carrefour", "netshoes"})


def manual_store_departments(row: StoreInfo) -> set[str]:
    if (row.platform or "").strip().lower() not in MANUAL_DEPARTMENT_PLATFORMS:
        return set()
    return set(row.manual_departments or [])


@dataclass(frozen=True)
class StoreDepartments:
    departments: list[str]
    has_pricing: bool


async def resolve_store_departments(
    session: AsyncSession, user: User, stores: Sequence[StoreInfo]
) -> dict[UUID, StoreDepartments]:
    """Resolve all supplied stores in one query, keeping caller's store scope.

    A pricing account contributes only to the store referenced by its FK,
    regardless of name/platform. Accounts without an FK use the same platform
    and an exact name or a name followed by a space (e.g. ``kfa classico``).
    Child segments do not classify stores; only root segments count.
    """
    if not stores:
        return {}
    accounts = (
        await session.execute(
            select(
                PricingAccount.name,
                PricingAccount.platform,
                PricingAccount.store_info_id,
                Segment.slug,
            )
            .join(Segment, Segment.id == PricingAccount.segment_id)
            .where(
                Segment.parent_id.is_(None),
                user_scope(PricingAccount, user),
                # A coluna de catálogo (06/10/2026) tem o nome da conta de kit e
                # nenhuma loja: não classifica loja (a base já classifica).
                PricingAccount.canal == "kit",
            )
        )
    ).all()
    linked: dict[UUID, set[str]] = defaultdict(set)
    legacy: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for name, platform, store_info_id, slug in accounts:
        if not slug:
            continue
        if store_info_id is not None:
            linked[store_info_id].add(slug)
            continue
        normalized_name = (name or "").strip().lower()
        if normalized_name:
            platform_value = platform.value if hasattr(platform, "value") else str(platform)
            legacy[platform_value.lower()].append((normalized_name, slug))

    result: dict[UUID, StoreDepartments] = {}
    for store in stores:
        pricing_departments = set(linked.get(store.id, ()))
        name = (store.account_name or "").strip().lower()
        platform = (store.platform or "").strip().lower()
        pricing_platform = STORE_TO_PRICING_PLATFORM.get(platform, platform)
        if name:
            pricing_departments.update(
                slug for account_name, slug in legacy.get(pricing_platform, ())
                if account_name == name or account_name.startswith(name + " ")
            )
        result[store.id] = StoreDepartments(
            departments=sorted(pricing_departments | manual_store_departments(store)),
            has_pricing=bool(pricing_departments),
        )
    return result
