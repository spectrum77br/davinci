"""Quem VÊ e quem MEXE na caixa /atendimento (fase de observação).

30/09/2026 (Eduardo): a caixa nasceu só para os admins de
`ATENDIMENTO_USUARIOS` (hoje thorfinn e heisenberg; vazio = todo admin).

07/10/2026 (Eduardo: "pode liberar pras outras pessoas do DaVinci verem pra
já obtermos feedbacks, mas claro por enquanto só leitura ... continua só
sugerindo ali se clicar"): a MESMA lista passou a dizer quem MEXE
(`pode_mexer`: responder, fechar, atribuir, pausar a IA, etiqueta, nota,
foto, Lojas e modo, Automáticas, manual, respostas prontas...). Todo o resto
da equipe ativa LÊ (`pode_ver`) — a fila, a conversa, as abas, o painel, as
métricas, as abas de configuração só para ver — e pode pedir a sugestão da
IA e dar 👍/👎 nela (o feedback que o dono quer; nada disso sai para a
plataforma). O que passa para quem só lê está no router
(`routers/atendimento.py`, `ROTAS_DE_QUEM_LE`).

A trava do router e o `/api/auth/me` (chaves `atendimento` = vê e
`atendimento_mexe` = mexe, que o menu e a página leem) usam estas mesmas
funções: menu, página e API nunca discordam. O escopo por equipe
(`deps/team_scope.py`) continua valendo por cima: quem tem equipe vê só as
lojas dela.
"""

from app.config import get_settings
from app.models import User, UserRole, UserStatus


def usuarios_liberados() -> frozenset[str]:
    bruto = get_settings().atendimento_usuarios or ""
    return frozenset(e.strip().lower() for e in bruto.split(",") if e.strip())


def pode_mexer(user: User) -> bool:
    """Mexe em tudo: admin com o e-mail em ATENDIMENTO_USUARIOS (vazio = todo admin)."""
    if user.role != UserRole.ADMIN:
        return False
    lista = usuarios_liberados()
    return not lista or (user.email or "").strip().lower() in lista


# Nome de 30/09/2026, quando a lista dizia quem VIA a caixa. Hoje é quem mexe.
liberado = pode_mexer


def operador_de_estoque(user: User) -> bool:
    """O "operador puro" que o web prende no /controle-estoque.

    A mesma conta do `middleware/auth.global.ts` e do `isOperator` do
    AppSidebar: não-admin, com etiqueta de estoque e sem NENHUMA permissão fora
    do `controle_estoque`. Para ele a tela do atendimento nem existe; a API não
    abre o que o menu não mostra.
    """
    if user.role == UserRole.ADMIN or not (user.stock_tags or []):
        return False
    for recurso, perm in (user.permissions or {}).items():
        if recurso == "controle_estoque" or not isinstance(perm, dict):
            continue
        if perm.get("view") or perm.get("edit") or perm.get("delete"):
            return False
    return True


def pode_ver(user: User) -> bool:
    """Lê a caixa: toda pessoa ATIVA do DaVinci, admin ou não (menos o operador
    de estoque). O escopo por equipe decide QUAIS lojas ela vê."""
    if user.status != UserStatus.ACTIVE:
        return False
    return pode_mexer(user) or not operador_de_estoque(user)


def so_le(user: User) -> bool:
    """Vê, mas não mexe: a tela esconde as ações e a API recusa a escrita (403)."""
    return pode_ver(user) and not pode_mexer(user)
