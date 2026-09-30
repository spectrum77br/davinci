"""/atendimento só para quem está em ATENDIMENTO_USUARIOS (Eduardo, 30/09/2026).

Entre os admins, só os e-mails da lista (hoje thorfinn e heisenberg); vazia =
todo admin. A trava do router e a chave `atendimento` do /me usam a mesma
função (`acesso.liberado`), então menu, página e API nunca discordam.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.config import get_settings
from app.models import UserRole
from app.routers import atendimento as rota
from app.services.atendimento import acesso


def _user(role: UserRole, email: str) -> SimpleNamespace:
    return SimpleNamespace(role=role, email=email)


@pytest.fixture
def lista(monkeypatch):
    def _f(valor: str) -> None:
        monkeypatch.setattr(get_settings(), "atendimento_usuarios", valor)

    return _f


def test_lista_vazia_libera_todo_admin(lista):
    lista("")
    assert acesso.liberado(_user(UserRole.ADMIN, "qualquer@x.com"))
    assert not acesso.liberado(_user(UserRole.USER, "qualquer@x.com"))


def test_lista_so_libera_os_emails_dela(lista):
    lista(" Um@X.com , dois@x.com,, ")
    assert acesso.usuarios_liberados() == frozenset({"um@x.com", "dois@x.com"})
    assert acesso.liberado(_user(UserRole.ADMIN, "um@x.com"))
    assert acesso.liberado(_user(UserRole.ADMIN, " DOIS@x.com "))
    assert not acesso.liberado(_user(UserRole.ADMIN, "tres@x.com"))
    # Não-admin nunca entra, nem estando na lista.
    assert not acesso.liberado(_user(UserRole.USER, "um@x.com"))


async def test_trava_do_router_segue_a_lista(lista, monkeypatch):
    monkeypatch.setattr(rota, "SO_ADMIN", True)
    lista("um@x.com")
    liberado = _user(UserRole.ADMIN, "um@x.com")
    assert await rota._so_admin(liberado) is liberado

    with pytest.raises(HTTPException) as e:
        await rota._so_admin(_user(UserRole.ADMIN, "outro@x.com"))
    assert e.value.status_code == 403
    assert e.value.detail == {"code": "atendimento_restrito"}

    with pytest.raises(HTTPException) as e:
        await rota._so_admin(_user(UserRole.USER, "um@x.com"))
    assert e.value.detail == {"code": "admin_only"}
