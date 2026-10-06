"""Regras do Imobilizado que a tela de Usuários também usa (RN08)."""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Imobilizado, ImobilizadoHistorico, User, UserStatus


def nome_pessoa(u: User | None) -> str:
    return (u.name or u.email) if u else "—"


def pessoa_ativa(u: User | None) -> bool:
    """Mesma regra de "ativo" do login: status ativo e não excluído."""
    return u is not None and u.status == UserStatus.ACTIVE and u.disabled_at is None


def texto_valor(v: Decimal) -> str:
    return f"{Decimal(v):.2f}"


async def itens_ativos_de(session: AsyncSession, user_id: UUID) -> list[Imobilizado]:
    return list(
        (
            await session.scalars(
                select(Imobilizado)
                .where(Imobilizado.responsavel_id == user_id, Imobilizado.status == "ativo")
                .order_by(Imobilizado.numero)
            )
        ).all()
    )


def registrar(
    session: AsyncSession,
    item: Imobilizado,
    campo: str,
    anterior: str | None,
    novo: str | None,
    ator: User,
) -> None:
    """Uma linha na aba Histórico do item (RN07)."""
    session.add(
        ImobilizadoHistorico(
            imobilizado_id=item.id,
            campo=campo,
            valor_anterior=anterior,
            valor_novo=novo,
            alterado_por=ator.id,
        )
    )


def trocar_responsavel(
    session: AsyncSession, item: Imobilizado, de: User | None, para: User, ator: User
) -> None:
    item.responsavel_id = para.id
    item.atualizado_em = func.now()
    item.atualizado_por = ator.id
    registrar(session, item, "responsavel", nome_pessoa(de), nome_pessoa(para), ator)
