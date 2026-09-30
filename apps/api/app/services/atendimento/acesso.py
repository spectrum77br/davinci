"""Quem vê a caixa /atendimento enquanto ela é só observação (Eduardo, 30/09/2026).

Admin e, se `ATENDIMENTO_USUARIOS` tiver e-mails, só esses. A API (a trava do
router) e o `/api/auth/me` (a chave `atendimento`, que o menu e a página do
web leem) usam esta mesma função, então os três nunca discordam.
"""

from app.config import get_settings
from app.models import User, UserRole


def usuarios_liberados() -> frozenset[str]:
    bruto = get_settings().atendimento_usuarios or ""
    return frozenset(e.strip().lower() for e in bruto.split(",") if e.strip())


def liberado(user: User) -> bool:
    if user.role != UserRole.ADMIN:
        return False
    lista = usuarios_liberados()
    return not lista or (user.email or "").strip().lower() in lista
