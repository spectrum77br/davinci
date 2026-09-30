"""Atendimento SÓ ADMIN por enquanto (Eduardo, 30/09/2026).

A primeira subida em produção é só observação, e a caixa fica para admin:
toda rota de /api/atendimento/* responde 403 `admin_only` para quem não é
admin, MESMO com o recurso `atendimento` inteiro (view/edit/delete). O
/api/atendimento/robo/* não passa por essa trava: é o robô do Mac, com token
próprio e sem usuário.

Os outros testes do atendimento desligam a trava (`SO_ADMIN = False`) porque
cobrem a permissão fina, que volta a valer quando a caixa abrir para a equipe.
Aqui a trava fica como vai para produção.
"""

from __future__ import annotations

import re
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute

from app.config import get_settings
from app.main import app
from app.models import UserRole
from app.routers import atendimento as rota

URL = "/api/atendimento"
TUDO = {"atendimento": {"view": True, "edit": True, "delete": True}}


def _rotas_da_caixa() -> list[APIRoute]:
    """Toda rota do app sob /api/atendimento, menos as do robô (token próprio)."""
    return [
        r
        for r in app.routes
        if isinstance(r, APIRoute)
        and r.path.startswith(URL + "/")
        and not r.path.startswith(URL + "/robo/")
    ]


def _chamadas(metodos: set[str] | None = None) -> list[tuple[str, str]]:
    """(método, caminho) de cada rota da caixa, com os {parâmetros} preenchidos."""
    saida = []
    for r in _rotas_da_caixa():
        caminho = re.sub(r"\{[^}]+\}", str(uuid4()), r.path)
        for metodo in sorted(r.methods - {"HEAD", "OPTIONS"}):
            if metodos is None or metodo in metodos:
                saida.append((metodo, caminho))
    return saida


def test_trava_ligada_e_em_todas_as_rotas_da_caixa():
    assert rota.SO_ADMIN is True, "a caixa é SÓ ADMIN por enquanto (Eduardo, 30/09/2026)"
    rotas = _rotas_da_caixa()
    # As 23 da caixa (a lista cresce: o teste pega as novas sozinho).
    assert len(rotas) >= 23
    for r in rotas:
        deps = [d.call for d in r.dependant.dependencies]
        assert rota._so_admin in deps, f"{sorted(r.methods)} {r.path} sem a trava de admin"
    # O robô fica de fora: sem a trava, só com o token dele.
    do_robo = [
        r for r in app.routes if isinstance(r, APIRoute) and r.path.startswith(URL + "/robo/")
    ]
    assert {r.path for r in do_robo} == {f"{URL}/robo/pulso", f"{URL}/robo/eventos"}
    for r in do_robo:
        assert rota._so_admin not in [d.call for d in r.dependant.dependencies]


@pytest.mark.parametrize(
    "permissoes",
    [None, TUDO, {"chamados": {"view": True, "edit": True, "delete": True}}],
    ids=["sem_permissao", "com_o_recurso_atendimento_inteiro", "com_outra_area"],
)
async def test_nao_admin_leva_403_em_todas_as_rotas(client, make_user, auth_as, permissoes):
    auth_as(await make_user(permissions=permissoes))
    chamadas = _chamadas()
    assert chamadas
    for metodo, caminho in chamadas:
        r = await client.request(metodo, caminho)
        assert r.status_code == 403, (metodo, caminho, r.status_code, r.text)
        assert r.json()["detail"] == {"code": "admin_only"}, (metodo, caminho)


async def test_admin_entra(client, make_user, auth_as, monkeypatch):
    # Nada de ler loja nem IA de verdade: só a caixa vazia.
    s = get_settings()
    for nome in ("atendimento_leitura_ativa", "atendimento_ia_ativa", "atendimento_envio_ativo"):
        monkeypatch.setattr(s, nome, False)
    auth_as(await make_user(role=UserRole.ADMIN))
    for caminho in ("/conversas", "/canais", "/modelos", "/metricas"):
        r = await client.get(URL + caminho)
        assert r.status_code == 200, (caminho, r.status_code, r.text)
    # Rota com parâmetro: passa da trava e cai no 404 da própria rota.
    r = await client.get(f"{URL}/conversas/{uuid4()}")
    assert r.status_code == 404, r.text
    # E todas as de leitura passam da trava (sem 401/403).
    for metodo, caminho in _chamadas({"GET"}):
        r = await client.request(metodo, caminho)
        assert r.status_code not in (401, 403), (metodo, caminho, r.status_code, r.text)


async def test_sem_login_e_401(client, auth_as):
    auth_as(None)
    r = await client.get(f"{URL}/conversas")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "unauthenticated"


async def test_robo_segue_no_token_sem_login(client, auth_as, monkeypatch):
    """O robô do Mac não tem usuário: a trava de admin não pode pegar ele."""
    monkeypatch.setattr(get_settings(), "atendimento_robo_token", "tok-de-teste")
    auth_as(None)
    # Sem token: 401 do robô (não o `unauthenticated` do login).
    r = await client.post(f"{URL}/robo/pulso", json={})
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "robo_nao_autorizado"
    # Com o token e o corpo vazio: passa da porta e para na validação do corpo.
    r = await client.post(
        f"{URL}/robo/pulso", json={}, headers={"Authorization": "Bearer tok-de-teste"}
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "corpo_invalido"


async def test_novo_usuario_nasce_sem_o_atendimento(client, make_user, auth_as):
    """Nenhum perfil que não seja admin nasce com o recurso: o cadastro de
    usuário sem permissões grava tudo desligado, inclusive o atendimento."""
    auth_as(await make_user(role=UserRole.ADMIN))
    email = f"novo-{uuid4().hex[:8]}@davinci-test.com"
    r = await client.post("/api/users", json={"email": email, "name": "Novo"})
    assert r.status_code == 201, r.text
    assert r.json()["role"] != "admin"
    assert r.json()["permissions"]["atendimento"] == {
        "view": False,
        "edit": False,
        "delete": False,
    }
