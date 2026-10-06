"""Módulo App Uranyx: quem vê (Eduardo, 06/10/2026: só thorfinn e heisenberg).

Admin E e-mail em APP_URANYX_USUARIOS; lista vazia = desligado para todo mundo
(contrato conteudo-e-catalogo-v1, seção 6). A trava do router e a chave
`app_uranyx` do /api/auth/me usam a mesma função, então nunca discordam.
"""

from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.models import UserRole
from app.services.app_uranyx import acesso

THORFINN = "thorfinn@davinci-test.com"
HEISENBERG = "heisenberg@davinci-test.com"


def _user(role: UserRole, email: str) -> SimpleNamespace:
    return SimpleNamespace(role=role, email=email)


@pytest.fixture
def lista(monkeypatch):
    def _f(valor: str) -> None:
        monkeypatch.setattr(get_settings(), "app_uranyx_usuarios", valor)

    return _f


def test_lista_vazia_desliga_para_todo_mundo(lista):
    lista("")
    assert acesso.usuarios_liberados() == frozenset()
    assert not acesso.liberado(_user(UserRole.ADMIN, THORFINN))
    lista(" , ,")
    assert not acesso.liberado(_user(UserRole.ADMIN, THORFINN))


def test_so_admin_da_lista(lista):
    lista(f" {THORFINN.upper()} ,{HEISENBERG},, ")
    assert acesso.usuarios_liberados() == frozenset({THORFINN, HEISENBERG})
    assert acesso.liberado(_user(UserRole.ADMIN, THORFINN))
    assert acesso.liberado(_user(UserRole.ADMIN, f" {HEISENBERG.upper()} "))
    # Admin fora da lista: não.
    assert not acesso.liberado(_user(UserRole.ADMIN, "outro@davinci-test.com"))
    # Na lista mas não admin: não.
    assert not acesso.liberado(_user(UserRole.USER, THORFINN))
    # Sem e-mail: não.
    assert not acesso.liberado(_user(UserRole.ADMIN, None))


async def _me(client) -> dict:
    r = await client.get("/api/auth/me")
    assert r.status_code == 200, r.text
    return r.json()


async def test_me_traz_app_uranyx_sempre(client, make_user, auth_as, lista):
    lista(f"{THORFINN},{HEISENBERG}")

    liberado = await make_user(email=THORFINN, role=UserRole.ADMIN)
    auth_as(liberado)
    me = await _me(client)
    assert me["app_uranyx"] is True
    assert me["email"] == THORFINN

    fora = await make_user(email="admin-fora@davinci-test.com", role=UserRole.ADMIN)
    auth_as(fora)
    assert (await _me(client))["app_uranyx"] is False

    nao_admin = await make_user(email=HEISENBERG, role=UserRole.USER)
    auth_as(nao_admin)
    assert (await _me(client))["app_uranyx"] is False

    # Lista vazia: ninguém, nem quem estava nela.
    lista("")
    auth_as(liberado)
    assert (await _me(client))["app_uranyx"] is False


async def test_me_sem_login_continua_null(client):
    r = await client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json() is None
