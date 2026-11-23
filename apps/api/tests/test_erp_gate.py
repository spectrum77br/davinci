"""DaVinci ERP: o Caddy só abre /erp para quem está liberado (thorfinn e heisenberg, por ora)."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import get_settings
from app.deps.auth import require_active_user
from app.models.enums import UserRole
from app.routers import erp_gate
from app.services import erp_acesso


def _client(role, email="liberado@exemplo.com"):
    app = FastAPI()
    app.include_router(erp_gate.router)
    user = SimpleNamespace(id="u1", email=email, role=role, is_active=True)
    app.dependency_overrides[require_active_user] = lambda: user
    return TestClient(app)


def _lista(monkeypatch, erp="", uranyx=""):
    settings = get_settings()
    monkeypatch.setattr(settings, "erp_usuarios", erp)
    monkeypatch.setattr(settings, "app_uranyx_usuarios", uranyx)


def test_liberado_entra_e_o_erp_recebe_quem_e(monkeypatch):
    _lista(monkeypatch, erp="Liberado@Exemplo.com")
    response = _client(UserRole.ADMIN).get("/api/erp-gate/authorize")
    assert response.status_code == 204
    assert response.headers["X-ERP-Actor"] == "liberado@exemplo.com"
    assert response.headers["X-ERP-Role"] == "admin"


def test_admin_fora_da_lista_e_quem_nao_e_admin_nao_entram(monkeypatch):
    _lista(monkeypatch, erp="liberado@exemplo.com")
    assert (
        _client(UserRole.ADMIN, "outro@exemplo.com").get("/api/erp-gate/authorize").status_code
        == 403
    )
    response = _client(UserRole.USER).get("/api/erp-gate/authorize")
    assert response.status_code == 403 and "X-ERP-Actor" not in response.headers


def test_sem_lista_do_erp_vale_a_do_app_uranyx_e_sem_nenhuma_ninguem_entra(monkeypatch):
    _lista(monkeypatch, uranyx="liberado@exemplo.com")
    assert erp_acesso.liberado(SimpleNamespace(role=UserRole.ADMIN, email="liberado@exemplo.com"))
    _lista(monkeypatch)
    assert _client(UserRole.ADMIN).get("/api/erp-gate/authorize").status_code == 403


def test_liberado_pelo_id_entra_fora_das_listas_e_so_sendo_admin(monkeypatch):
    _lista(monkeypatch)
    harry = "439ac041-2a38-4457-a2e4-61bf79ca8533"
    assert erp_acesso.liberado(
        SimpleNamespace(id=harry, role=UserRole.ADMIN, email="h@exemplo.com")
    )
    assert not erp_acesso.liberado(
        SimpleNamespace(id=harry, role=UserRole.USER, email="h@exemplo.com")
    )
    assert not erp_acesso.liberado(SimpleNamespace(id="u1", role=UserRole.ADMIN, email="x@y.com"))
