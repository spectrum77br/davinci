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


def pode_responder(user: User) -> bool:
    """Responde e cuida das conversas: toda pessoa que vê a caixa.

    28/10/2026, o Eduardo, ao trocar o Duoke pelo DaVinci: "ative resposta pra
    todos — a IA só sugere por enquanto, mas a pessoa já pode responder em
    todos". Vale para as ações DA CONVERSA (`routers/atendimento.py`,
    `ROTAS_DE_QUEM_RESPONDE`: responder, foto, nota, etiqueta, fechar, marcar
    lida...). Configurar a caixa (modo das lojas, manual, automáticas,
    respostas prontas, Zap, zerar não lidas) continua de `pode_mexer`.
    """
    return pode_ver(user)


def so_le(user: User) -> bool:
    """Vê, mas não mexe: a tela esconde as ações e a API recusa a escrita (403)."""
    return pode_ver(user) and not pode_mexer(user)


def ve_aba_email(user: User) -> bool:
    """Vê a ABA E-MAIL inteira do /atendimento: admin, ou quem tem a permissão
    `atendimento_email` view (28/10/2026, o Eduardo: "bote o user marrocos no
    davinci pra poder ver tambem a aba de emails" — sem virar admin).

    Só VER: as ações da aba seguem com `pode_mexer`, e a trava da caixa
    (`pode_ver`, no router) vale antes. Quem não vê a aba lê e-mail só pela
    barra de lojas da Caixa (403 `aba_email_so_admin` no resto). A mesma conta
    do `useCan('atendimento_email', 'view')` do web (admin sempre passa)."""
    if user.role == UserRole.ADMIN:
        return True
    perm = (user.permissions or {}).get("atendimento_email")
    return isinstance(perm, dict) and perm.get("view") is True


def responde_email(user: User) -> bool:
    """RESPONDE e-mail pela aba E-mail (28/10/2026, o Eduardo: "passe para o
    usuario marrocos acesso para ele conseguir responder os emails de dentro do
    davinci, hoje so adm pode"): quem NÃO é admin e tem a permissão
    `atendimento_email` com ver E editar. Só responder o e-mail recebido, nas
    caixas que a pessoa vê (`mail_admin.pode_responder_caixa`): escrever e-mail
    novo, encaminhar, anexos e configurar a caixa continuam do dono/admin.
    Admin fica de fora de propósito: ele segue as regras de sempre (dono/admin
    e, na caixa da empresa, só quem mexe — `pode_mexer`)."""
    if user.status != UserStatus.ACTIVE or user.role == UserRole.ADMIN:
        return False
    perm = (user.permissions or {}).get("atendimento_email")
    return isinstance(perm, dict) and perm.get("view") is True and perm.get("edit") is True
