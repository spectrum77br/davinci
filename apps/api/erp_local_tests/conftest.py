"""Independent of tests/conftest.py: temporary SQLite only, network blocked."""

import socket

import pytest

from app.erp_local.core import LocalERP


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Outbound network is forbidden in local ERP tests")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture
def erp(tmp_path):
    local = LocalERP(tmp_path / "test.sqlite3")
    local.command("seed", "demo.seed", {})
    return local
