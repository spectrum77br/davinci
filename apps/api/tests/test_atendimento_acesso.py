"""Quem MEXE no /atendimento: ATENDIMENTO_USUARIOS (Eduardo, 30/09 e 07/10/2026).

Entre os admins, só os e-mails da lista (hoje thorfinn e heisenberg); vazia =
todo admin. Até 07/10/2026 a lista dizia quem VIA a caixa; desde então toda
pessoa ativa vê (só leitura) e a lista diz quem mexe (`acesso.pode_mexer`; o
nome antigo `liberado` continua apontando para ela). A trava do router e as
chaves `atendimento`/`atendimento_mexe` do /me usam as mesmas funções, então
menu, página e API nunca discordam.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.config import get_settings
from app.models import UserRole, UserStatus
from app.routers import atendimento as rota
from app.services.atendimento import acesso


def _user(
    role: UserRole,
    email: str,
    *,
    status: UserStatus = UserStatus.ACTIVE,
    stock_tags: list[str] | None = None,
    permissions: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        role=role, email=email, status=status, stock_tags=stock_tags, permissions=permissions
    )


def _pedido(metodo: str, molde: str | None = None) -> SimpleNamespace:
    """O `Request` que a trava lê: o método e a rota (o molde do caminho)."""
    return SimpleNamespace(method=metodo, scope={"route": SimpleNamespace(path=molde)})


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


def test_o_nome_antigo_e_quem_mexe():
    assert acesso.liberado is acesso.pode_mexer


def test_quem_ve_toda_pessoa_ativa_menos_o_operador_de_estoque(lista):
    lista("um@x.com")
    assert acesso.pode_ver(_user(UserRole.USER, "qualquer@x.com"))
    assert acesso.pode_ver(_user(UserRole.ADMIN, "outro@x.com"))
    assert acesso.so_le(_user(UserRole.ADMIN, "outro@x.com"))
    assert not acesso.so_le(_user(UserRole.ADMIN, "um@x.com"))
    for status in (UserStatus.PENDING, UserStatus.SUSPENDED):
        assert not acesso.pode_ver(_user(UserRole.USER, "x@x.com", status=status))
    # Operador puro (etiqueta de estoque e nada fora do controle_estoque): não vê.
    operador = _user(
        UserRole.USER, "op@x.com", stock_tags=["ci"],
        permissions={"controle_estoque": {"view": True, "edit": True}},
    )  # fmt: skip
    assert acesso.operador_de_estoque(operador) and not acesso.pode_ver(operador)
    # Com qualquer outra permissão ele é supervisor (o web solta): vê.
    supervisor = _user(
        UserRole.USER, "sup@x.com", stock_tags=["ci"], permissions={"margem": {"view": True}}
    )
    assert not acesso.operador_de_estoque(supervisor) and acesso.pode_ver(supervisor)
    # Admin com etiqueta nunca é operador.
    assert acesso.pode_ver(_user(UserRole.ADMIN, "um@x.com", stock_tags=["ci"]))


async def test_trava_do_router_segue_a_lista(lista, monkeypatch):
    monkeypatch.setattr(rota, "SO_ADMIN", True)
    lista("um@x.com")
    escrita = _pedido("POST", "/api/atendimento/conversas/{conversa_id}/responder")
    leitura = _pedido("GET", "/api/atendimento/conversas")
    sugerir = _pedido("POST", "/api/atendimento/conversas/{conversa_id}/rascunho")
    liberado = _user(UserRole.ADMIN, "um@x.com")
    assert await rota._so_admin(escrita, liberado) is liberado

    # Admin fora da lista e não-admin: leem e sugerem, não escrevem.
    for quem in (_user(UserRole.ADMIN, "outro@x.com"), _user(UserRole.USER, "um@x.com")):
        assert await rota._so_admin(leitura, quem) is quem
        assert await rota._so_admin(sugerir, quem) is quem
        with pytest.raises(HTTPException) as e:
            await rota._so_admin(escrita, quem)
        assert e.value.status_code == 403
        assert e.value.detail["code"] == "atendimento_so_leitura"

    # Quem não vê (operador de estoque, inativo) nem lê.
    with pytest.raises(HTTPException) as e:
        await rota._so_admin(leitura, _user(UserRole.USER, "x@x.com", status=UserStatus.PENDING))
    assert e.value.detail == {"code": "atendimento_restrito"}

    # Fora da fase de observação, a trava não decide nada (a permissão fina decide).
    monkeypatch.setattr(rota, "SO_ADMIN", False)
    quem = _user(UserRole.USER, "x@x.com")
    assert await rota._so_admin(escrita, quem) is quem
