"""Alert emission helpers.

Emit alerts with optional `dedupe_key`. Dois índices únicos seguram a repetição
(e.g. same product crossing low-stock threshold every 2min):
- `(user_id, dedupe_key)`: o aviso dirigido a uma pessoa;
- `(dedupe_key)` do aviso sem destinatário (ADM de todos, equipe) — M-19.

Quem vê (M-19, 23/11/2026): `escopo` = "adm" | "equipe" | "pessoal" (ver
`models/alert.py`). Sem `escopo`, a classificação vem do tipo
(`classificar`): tarefa → pessoal; Falha sync / Revisão / Banido com
`link_id` → equipe; o resto → ADM. Na dúvida é ADM (só administrador vê).

By default, every emitted alert is also pushed to Telegram when the user has
`user_settings.notify_telegram` on. Callers that already format their own
Telegram message (richer body / custom header) should pass
`notify_telegram=False` to avoid double-send. Fora do alerta pessoal e do ADM
dirigido, o `user_id` passado só diz a quem vai o Telegram (o sino é do escopo).
"""

from __future__ import annotations

import html
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Alert,
    AlertSeverity,
    AlertType,
    Integration,
    ProductLink,
    StoreInfo,
    User,
    UserRole,
    UserSettings,
)
from app.models.alert import ESCOPO_ADM, ESCOPO_EQUIPE, ESCOPO_PESSOAL, ESCOPOS

logger = structlog.get_logger()

_SEVERITY_PREFIX = {
    AlertSeverity.ERROR: "🔴",
    AlertSeverity.WARNING: "🟠",
    AlertSeverity.SUCCESS: "🟢",
    AlertSeverity.INFO: "🔵",
}

# Alerta de anúncio (M-19): vai para a equipe da loja quando tem o vínculo.
TIPOS_DE_ANUNCIO = frozenset(
    {AlertType.SYNC_FAILURE, AlertType.REQUIRES_REVIEW, AlertType.LISTING_BANNED}
)


def classificar(type: AlertType, payload: dict[str, Any] | None) -> str:
    """O escopo pelo tipo (M-19), para quem não diz o seu."""
    if type == AlertType.TAREFA_ATRIBUIDA:
        return ESCOPO_PESSOAL
    if type in TIPOS_DE_ANUNCIO and (payload or {}).get("link_id"):
        return ESCOPO_EQUIPE
    return ESCOPO_ADM


async def _loja_da_integracao(
    session: AsyncSession, integration_id: UUID
) -> tuple[str | None, str | None, int | None]:
    """(plataforma, nome da conta, equipe de vendas) da integração, agora."""
    row = (
        await session.execute(
            select(Integration.platform, Integration.name).where(Integration.id == integration_id)
        )
    ).first()
    if row is None:
        return None, None, None
    equipe = (
        await session.execute(
            select(StoreInfo.sales_team)
            .where(StoreInfo.integration_id == integration_id, StoreInfo.sales_team.is_not(None))
            .order_by(StoreInfo.sales_team)
            .limit(1)
        )
    ).scalar_one_or_none()
    plataforma = getattr(row.platform, "value", row.platform)
    return plataforma, row.name, equipe


async def _integracao_do_vinculo(session: AsyncSession, link_id: Any) -> UUID | None:
    try:
        lid = UUID(str(link_id))
    except (TypeError, ValueError):
        return None
    return (
        await session.execute(select(ProductLink.integration_id).where(ProductLink.id == lid))
    ).scalar_one_or_none()


async def _e_admin(session: AsyncSession, user_id: UUID) -> bool:
    role = (await session.execute(select(User.role).where(User.id == user_id))).scalar_one_or_none()
    return role == UserRole.ADMIN


async def emit_alert(
    session: AsyncSession,
    *,
    type: AlertType,
    title: str,
    user_id: UUID | None = None,
    severity: AlertSeverity = AlertSeverity.INFO,
    message: str | None = None,
    payload: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
    notify_telegram: bool = True,
    escopo: str | None = None,
    integration_id: UUID | None = None,
    plataforma: str | None = None,
    conta: str | None = None,
    equipe: int | None = None,
    dirigido: bool = False,
) -> Alert | None:
    """Insert an alert. With `dedupe_key`, conflicts are silently ignored
    (returns None when the dedupe row already exists).

    M-19: `escopo` diz quem vê:
    - "pessoal": só `user_id` (obrigatório);
    - "adm": todos os administradores; com `dirigido=True`, só o `user_id`
      (se ele for administrador — senão ninguém veria, e vai para todos);
    - "equipe": a equipe da loja da `integration_id` (ou do `link_id` do
      payload) e os administradores; sem a loja, vira "adm".
    Fora do pessoal e do ADM dirigido, `user_id` só diz a quem vai o Telegram.
    """
    payload = payload or {}
    escopo = escopo or classificar(type, payload)
    if escopo not in ESCOPOS:
        raise ValueError(f"escopo inválido: {escopo}")

    if escopo == ESCOPO_EQUIPE:
        if integration_id is None and payload.get("link_id"):
            integration_id = await _integracao_do_vinculo(session, payload["link_id"])
        if integration_id is not None:
            plat, nome, eq = await _loja_da_integracao(session, integration_id)
            plataforma = plataforma or plat
            conta = conta or nome
            equipe = equipe if equipe is not None else eq
        if integration_id is None and equipe is None:
            # Sem a loja ninguém da equipe veria: fica para o administrador.
            escopo = ESCOPO_ADM

    if escopo == ESCOPO_PESSOAL:
        if user_id is None:
            raise ValueError("alerta pessoal sem user_id")
        destinatario: UUID | None = user_id
    elif (
        escopo == ESCOPO_ADM
        and dirigido
        and user_id is not None
        and await _e_admin(session, user_id)
    ):
        destinatario = user_id
    else:
        destinatario = None

    values = {
        "user_id": destinatario,
        "type": type.value,
        "severity": severity.value,
        "title": title,
        "message": message,
        "payload": payload,
        "dedupe_key": dedupe_key,
        "escopo": escopo,
        "equipe": equipe,
        "plataforma": plataforma,
        "integration_id": integration_id,
        "conta": conta,
    }
    if dedupe_key is None:
        a = Alert(**{**values, "type": type, "severity": severity})
        session.add(a)
        await session.flush()
    else:
        # Sem alvo no ON CONFLICT: vale para os dois índices únicos (o por
        # pessoa e o do aviso sem destinatário).
        stmt = (
            pg_insert(Alert.__table__)
            .values(**values)
            .on_conflict_do_nothing()
            .returning(Alert.__table__.c.id)
        )
        result = await session.execute(stmt)
        row = result.first()
        if row is None:
            return None
        a = (await session.execute(select(Alert).where(Alert.id == row[0]))).scalar_one_or_none()
        if a is None:
            return None

    if notify_telegram and user_id is not None:
        await _push_telegram(session, user_id=user_id, alert=a)
    return a


async def _push_telegram(session: AsyncSession, *, user_id: UUID, alert: Alert) -> None:
    """Best-effort Telegram dispatch. Honors `user_settings.notify_telegram`."""
    try:
        us = await session.get(UserSettings, user_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("alert_telegram_lookup_failed", err=str(e))
        return
    if us is None or not us.notify_telegram:
        return

    from app.services.telegram import TelegramClient

    prefix = _SEVERITY_PREFIX.get(alert.severity, "")
    # O texto pode vir do usuário (tarefa ditada: "caixas < 10 kg"): sem
    # escapar, o Telegram recusa a mensagem inteira (parse_mode=HTML).
    head = f"{prefix} <b>{html.escape(alert.title)}</b>".strip()
    body = head if not alert.message else f"{head}\n{html.escape(alert.message)}"
    tg = TelegramClient()
    await tg.safe_send(body, chat_id=us.telegram_chat_id)
