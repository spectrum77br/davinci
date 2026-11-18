"""Quem abre o DaVinci ERP (Marco, 18/11/2026: por enquanto só thorfinn e heisenberg).

Admin E e-mail em `ERP_USUARIOS`; com a lista vazia, vale a do App Uranyx (`APP_URANYX_USUARIOS`),
que é exatamente essas duas pessoas. O portão do Caddy (/api/erp-gate/authorize) e a chave `erp`
do `/api/auth/me` (que o menu lê) usam esta mesma função, então nunca discordam. Sem ninguém nas
duas listas, ninguém entra.
"""

from app.config import get_settings
from app.models import User, UserRole


def usuarios_liberados() -> frozenset[str]:
    settings = get_settings()
    bruto = settings.erp_usuarios or settings.app_uranyx_usuarios or ""
    return frozenset(e.strip().lower() for e in bruto.split(",") if e.strip())


def liberado(user: User) -> bool:
    if user.role != UserRole.ADMIN:
        return False
    return (user.email or "").strip().lower() in usuarios_liberados()
