"""Escopo por equipe de vendas (Fase 2).

Fonte única que resolve, para um usuário não-admin com equipe(s), o conjunto
de "chaves de loja" que ele pode enxergar. Todas as telas de dados
(Produtos, Anúncios, Tabela de Preços, Reembolso, Logística, Integrações,
Sync Logs, etc.) chegam à `store_info.sales_team` por UMA destas chaves:

  - integration_id  → Integração / ProductLink / SyncLog / Produtos (via link)
  - bling_store_id  → BlingOrder.loja / Reembolso / Logística (via pedido)
  - account_name    → Reembolso.conta / Logística.conta (nome da conta, texto)

O resolver faz UM SELECT em `store_info` filtrado por `sales_team IN (teams)`
e monta os três conjuntos. Cada router aplica o filtro pela chave que tiver.

Semântica (igual à Fase 1 / `user_scope`):
  - admin → `unrestricted=True` (vê tudo, sem filtro)
  - usuário SEM equipe → `unrestricted=True` (vê tudo)
  - usuário COM equipe → escopo pelos conjuntos (só a sua equipe)

Assim o rollout é seguro: quem não tem equipe atribuída continua vendo tudo.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UserRole
from app.models.integration import Integration
from app.models.pricing import StoreInfo


@dataclass
class TeamScope:
    """Chaves de loja visíveis ao usuário. `unrestricted` = sem filtro."""

    unrestricted: bool
    store_info_ids: set[UUID] = field(default_factory=set)
    integration_ids: set[UUID] = field(default_factory=set)
    bling_store_ids: set[str] = field(default_factory=set)
    account_names: set[str] = field(default_factory=set)  # lower/trim


def _norm(s: str | None) -> str | None:
    if s is None:
        return None
    s = s.strip().lower()
    return s or None


async def resolve_team_scope(session: AsyncSession, user: User) -> TeamScope:
    """Monta o TeamScope do usuário a partir das lojas da(s) equipe(s) dele."""
    if user.role == UserRole.ADMIN:
        return TeamScope(unrestricted=True)

    teams = user.sales_teams or []
    if not teams:
        return TeamScope(unrestricted=True)

    rows = (
        await session.execute(
            select(
                StoreInfo.id,
                StoreInfo.integration_id,
                StoreInfo.bling_store_id,
                StoreInfo.account_name,
                StoreInfo.platform,
            ).where(StoreInfo.sales_team.in_(teams))
        )
    ).all()

    scope = TeamScope(unrestricted=False)
    # Lojas sem o FK `integration_id` preenchido (backfill preguiçoso) — caso
    # comum: só `marquezini` tinha o FK. Resolvemos a integração pelo par
    # (nome, plataforma), espelhando `_resolve_linked_integration`, pra que
    # TODAS as contas do membro apareçam (Integrações/Sync de estoque), não só
    # as que já tinham o FK. Leitura pura (sem backfill aqui).
    pendentes: set[tuple[str, str]] = set()
    for store_info_id, integration_id, bling_store_id, account_name, platform in rows:
        scope.store_info_ids.add(store_info_id)
        if integration_id is not None:
            scope.integration_ids.add(integration_id)
        else:
            nm_plat = _norm(account_name)
            if nm_plat and platform:
                pendentes.add((nm_plat, platform.strip().lower()))
        if bling_store_id:
            scope.bling_store_ids.add(bling_store_id)
        nm = _norm(account_name)
        if nm:
            scope.account_names.add(nm)

    if pendentes:
        nomes = {nome for nome, _ in pendentes}
        integ_rows = (
            await session.execute(
                select(Integration.id, Integration.name, Integration.platform).where(
                    func.lower(func.trim(Integration.name)).in_(nomes)
                )
            )
        ).all()
        for integ_id, integ_name, integ_platform in integ_rows:
            plat = getattr(integ_platform, "value", integ_platform)
            if (_norm(integ_name), str(plat).strip().lower()) in pendentes:
                scope.integration_ids.add(integ_id)
    return scope
