"""Filtro de Tag do Controle de Estoque com caixinhas (30/09): `?tag=` aceita
várias tags separadas por vírgula (`mala,fake`) e a cerca do operador vale
pra cada uma delas."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.models import User, UserRole
from app.routers.estoque import _resolve_tags, _tags_pedidos


def _user(role: UserRole = UserRole.USER, stock_tags=None, permissions=None) -> User:
    return User(role=role, stock_tags=stock_tags, permissions=permissions or {})


def test_admin_varias_tags():
    admin = _user(UserRole.ADMIN)
    assert _resolve_tags(admin, "mala,fake") == ["mala", "fake"]
    assert _resolve_tags(admin, " Mala , fake,mala,") == ["mala", "fake"]
    assert _resolve_tags(admin, "mala") == ["mala"]
    assert _resolve_tags(admin, "") is None
    assert _resolve_tags(admin, None) is None


def test_tag_invalida_no_meio_da_lista():
    with pytest.raises(HTTPException) as e:
        _resolve_tags(_user(UserRole.ADMIN), "mala,xyz")
    assert e.value.status_code == 400


def test_operador_so_dentro_das_suas_tags():
    op = _user(stock_tags=["mala", "fake", "sp"])
    assert _resolve_tags(op, "mala,fake") == ["mala", "fake"]
    assert _resolve_tags(op, "") == ["mala", "fake", "sp"]
    with pytest.raises(HTTPException) as e:
        _resolve_tags(op, "mala,ci")
    assert e.value.status_code == 403
    assert e.value.detail == {"code": "tag_not_allowed"}


def test_gerente_etiquetas_pedidos_varias_tags():
    gerente = _user(
        stock_tags=["sa"],
        permissions={"controle_estoque_pedidos_todas_tags": True},
    )
    assert _tags_pedidos(gerente, "mala,ci") == ["mala", "ci"]
    assert _tags_pedidos(gerente, "") is None
