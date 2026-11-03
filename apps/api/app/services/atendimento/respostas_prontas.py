"""Respostas prontas por equipe (03/11/2026).

O Eduardo: "separe por 3 equipes que separamos la mas as q ja tem la deixe pra
todos mas as novas que eles vao criar vai ter essa ideia de especifico por
equipe". A equipe é a de vendas de 30/10/2026 (`deps/team_scope.py`): o número
de `users.sales_teams` e de `store_info.sales_team` — 101 Londres, 102 Israel,
103 Marrocos (`NOMES_EQUIPES`). Os outros códigos de `sales_teams` (o cairo tem
201 e 10001) não são equipe do atendimento: quem só tem esses fica sem equipe.

`atendimento_modelos.equipe` NULL = para todos (as de antes da 0443).

  • quem vê: o admin, todas; os outros, as "para todos" + as da equipe dele;
  • o agente (`consultar_manual`): as "para todos" + as da equipe da LOJA da
    conversa (`equipes_da_loja`).

Quem cria, edita e apaga fica no router (depende da fase de observação).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.team_scope import NOMES_EQUIPES
from app.models import AtendimentoModelo, User, UserRole
from app.models.pricing import StoreInfo


def equipe_valida(numero: object) -> bool:
    return isinstance(numero, int) and not isinstance(numero, bool) and numero in NOMES_EQUIPES


def equipes_do_usuario(user: User) -> frozenset[int]:
    """As equipes do atendimento da pessoa. Admin não é "de equipe": vê todas."""
    if user.role == UserRole.ADMIN:
        return frozenset()
    return frozenset(t for t in user.sales_teams or [] if equipe_valida(t))


def ve_todas(user: User) -> bool:
    return user.role == UserRole.ADMIN


def clausula_visivel(user: User):
    """O WHERE da lista de quem pediu (None = sem filtro, o admin)."""
    if ve_todas(user):
        return None
    return clausula_das_equipes(equipes_do_usuario(user))


def clausula_das_equipes(equipes: set[int] | frozenset[int]):
    """As "para todos" + as destas equipes."""
    m = AtendimentoModelo
    if not equipes:
        return m.equipe.is_(None)
    return or_(m.equipe.is_(None), m.equipe.in_(sorted(equipes)))


async def equipes_da_loja(session: AsyncSession, integration_id: UUID | None) -> set[int]:
    """A equipe da loja da conversa: a `sales_team` da ficha da integração.

    Pelo FK da ficha; sem ele, pela ponte do e-mail (`rotear.integracoes_das_fichas`:
    o FK ativo, senão o par nome + plataforma), a mesma do escopo do atendimento.
    Loja sem equipe (ou conversa sem integração) = só as "para todos".
    """
    if integration_id is None:
        return set()
    times = set(
        (
            await session.execute(
                select(StoreInfo.sales_team).where(StoreInfo.integration_id == integration_id)
            )
        ).scalars()
    )
    if not {t for t in times if equipe_valida(t)}:
        from app.services.mail_atendimento import rotear

        da_ficha = await rotear.integracoes_das_fichas(session)
        fichas = {f for f, i in da_ficha.items() if i == integration_id}
        if fichas:
            times |= set(
                (
                    await session.execute(
                        select(StoreInfo.sales_team).where(StoreInfo.id.in_(fichas))
                    )
                ).scalars()
            )
    return {t for t in times if equipe_valida(t)}
