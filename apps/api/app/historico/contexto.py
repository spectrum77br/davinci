"""Quem está agindo no pedido atual.

O middleware cria um `Ator` por pedido e o guarda numa ContextVar. A
autenticação preenche a pessoa DEPOIS, no mesmo objeto (não troca a variável:
o middleware precisa enxergar o que a dependência preencheu).

A ContextVar é copiada pelo SQLAlchemy para o greenlet do driver, então o
evento `after_begin` (banco.py) enxerga o mesmo `Ator`.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from sqlalchemy import text

# Marca a transação para o gatilho do banco. `true` = vale só nesta transação:
# com o pool de conexões, uma marca de sessão vazaria para o próximo pedido.
MARCAR_TRANSACAO = text(
    "SELECT set_config('davinci.ator', :ator, true), set_config('davinci.req', :req, true)"
)


@dataclass
class Ator:
    metodo: str
    caminho: str
    req_id: UUID = field(default_factory=uuid4)
    # Pedido de escrita de pessoa que deve marcar o banco. Falso em leitura,
    # em rota de robô disfarçada (ex. o recarregar automático da Margem) e
    # quando ninguém se identificou.
    grava: bool = False
    # A decisão original (pedido de escrita de pessoa). `grava` vira falso
    # quando a resposta sai; esta não, e diz se o evento deve ser gravado.
    escrita: bool = False
    # Registra o evento mesmo sem alteração no banco (ver a senha, enviar
    # preço ao marketplace, mandar mensagem).
    evento_sempre: bool = False
    user_id: UUID | None = None
    nome: str | None = None
    via: str = "tela"


_ator: ContextVar[Ator | None] = ContextVar("historico_ator", default=None)


def ator_atual() -> Ator | None:
    return _ator.get()


def abrir(ator: Ator):
    return _ator.set(ator)


def fechar(token) -> None:
    _ator.reset(token)


def deve_marcar(a: Ator | None) -> bool:
    return a is not None and a.grava and a.user_id is not None


async def identificar(session, user, via: str = "tela") -> None:
    """Diz ao Histórico quem é a pessoa deste pedido.

    A busca do usuário já abriu a transação (o `after_begin` rodou antes de
    sabermos quem era), então esta transação é marcada aqui mesmo; as
    seguintes, depois de cada commit, o `after_begin` marca sozinho.
    """
    a = ator_atual()
    if a is None or user is None:
        return
    a.user_id = user.id
    a.nome = user.name or user.email
    a.via = via
    if deve_marcar(a) and session.in_transaction():
        await session.execute(MARCAR_TRANSACAO, {"ator": str(a.user_id), "req": str(a.req_id)})


async def identificar_por_id(session, user_id, via: str = "tela") -> None:
    """Para rota que sabe quem é a pessoa sem o cookie (retorno do login de
    marketplace, que traz a pessoa no `state`)."""
    if user_id is None or ator_atual() is None:
        return
    from app.models import User

    await identificar(session, await session.get(User, user_id), via=via)
