"""Quem abre o DaVinci ERP (Marco, 18/11/2026: thorfinn e heisenberg; 23/11/2026: harry potter;
25/11/2026: joana dark, para Produtos 2 e Pedidos 2).

Admin E (e-mail em `ERP_USUARIOS` ou id em `LIBERADOS_POR_ID`); com a lista do .env vazia, vale a
do App Uranyx (`APP_URANYX_USUARIOS`), que é exatamente thorfinn e heisenberg. Quem o Marco libera
depois entra pelo id do usuário no DaVinci (nome e e-mail mudam, o id não), sem mexer no .env. O
portão do Caddy (/api/erp-gate/authorize) e a chave `erp` do `/api/auth/me` (que o menu lê) usam
esta mesma função, então nunca discordam. Sem ninguém nas listas, ninguém entra.
"""

from app.config import get_settings
from app.models import User, UserRole


def usuarios_liberados() -> frozenset[str]:
    settings = get_settings()
    bruto = settings.erp_usuarios or settings.app_uranyx_usuarios or ""
    return frozenset(e.strip().lower() for e in bruto.split(",") if e.strip())


# Liberados pelo Marco pelo id do usuário no DaVinci (continua precisando ser admin).
LIBERADOS_POR_ID = frozenset(
    {
        "439ac041-2a38-4457-a2e4-61bf79ca8533",  # harry potter, 23/11/2026
        "cea25322-c4e5-4b82-b8e3-56029cc948e4",  # joana dark, 25/11/2026
    }
)


def liberado(user: User) -> bool:
    if user.role != UserRole.ADMIN:
        return False
    email = (user.email or "").strip().lower()
    return email in usuarios_liberados() or str(getattr(user, "id", "")) in LIBERADOS_POR_ID
