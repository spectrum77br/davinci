"""Quem vê o módulo "App Uranyx" (Eduardo, 06/10/2026: thorfinn e heisenberg).

Admin E e-mail em `APP_URANYX_USUARIOS`. Lista vazia = módulo desligado para
todo mundo (diferente do atendimento, em que vazio libera todo admin). A trava
do router e a chave `app_uranyx` do `/api/auth/me` (que o menu e as telas do
web leem) usam esta mesma função, então os três nunca discordam.
"""

from app.config import get_settings
from app.models import User, UserRole


def usuarios_liberados() -> frozenset[str]:
    bruto = get_settings().app_uranyx_usuarios or ""
    return frozenset(e.strip().lower() for e in bruto.split(",") if e.strip())


def liberado(user: User) -> bool:
    if user.role != UserRole.ADMIN:
        return False
    return (user.email or "").strip().lower() in usuarios_liberados()
