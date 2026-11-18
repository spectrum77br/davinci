"""ERP próprio: o Caddy só abre /erp para administrador logado no DaVinci."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.deps.auth import require_active_user
from app.models.enums import UserRole
from app.routers import erp_gate


def _client(role):
    app = FastAPI()
    app.include_router(erp_gate.router)
    user = SimpleNamespace(id="u1", email="pessoa@exemplo.com", role=role, is_active=True)
    app.dependency_overrides[require_active_user] = lambda: user
    return TestClient(app)


def test_admin_entra_e_o_erp_recebe_quem_e():
    response = _client(UserRole.ADMIN).get("/api/erp-gate/authorize")
    assert response.status_code == 204
    assert response.headers["X-ERP-Actor"] == "pessoa@exemplo.com"
    assert response.headers["X-ERP-Role"] == "admin"


def test_quem_nao_e_admin_nao_entra_por_enquanto():
    response = _client(UserRole.USER).get("/api/erp-gate/authorize")
    assert response.status_code == 403
    assert "X-ERP-Actor" not in response.headers
