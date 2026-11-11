"""Ajustes do Flex trocados na tela (Vinicius, 11/11/2026): o modo, a escrita
da Shopee e o pedido Flex no .sp saíram do .env do servidor (só o Eduardo
mexia) para a aba Flex — "pode fazer, mas ainda deixa tudo desligado".

Campo vazio na tela = vale o .env; sem linha nenhuma, nada muda.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import worker
from app.config import get_settings
from app.models import FlexAjuste, UserRole
from app.services import flex_config, flex_motor

PERMS = {"logistica": {"view": True, "edit": True}}


@pytest.fixture(autouse=True)
def _env_de_hoje(monkeypatch):
    """O .env de produção em 11/11/2026: só observando, Shopee só lendo,
    pedido Flex fora do .sp, sem espera da NF."""
    cfg = get_settings()
    monkeypatch.setattr(cfg, "flex_modo", "observar")
    monkeypatch.setattr(cfg, "flex_shopee_escrita", False)
    monkeypatch.setattr(cfg, "flex_pedido_no_sp", False)
    monkeypatch.setattr(cfg, "flex_espera_envio_min", 0)
    monkeypatch.setattr(cfg, "flex_contas", "")
    flex_config.limpar_cache()
    yield
    flex_config.limpar_cache()


def test_sem_ajuste_vale_o_env():
    assert flex_config.modo() == "observar"
    assert flex_config.pode_escrever() is False
    assert flex_config.shopee_escrita() is False
    assert flex_config.pedido_no_sp() is False
    assert flex_config.espera_envio_min() == 0
    assert {flex_config.origem(c) for c in ("modo", "shopee_escrita", "pedido_no_sp")} == {
        "servidor"
    }


def test_ajuste_da_tela_vence_o_env(monkeypatch):
    flex_config.usar(flex_config.Ajustes("piloto", True, True))
    assert (flex_config.modo(), flex_config.pode_escrever()) == ("piloto", True)
    assert flex_config.shopee_escrita() is True
    assert flex_config.pedido_no_sp() is True
    # A espera da NF anda junto com o pedido no .sp (o .env tem 0 → 10).
    assert flex_config.espera_envio_min() == flex_config.ESPERA_ENVIO_PADRAO_MIN
    monkeypatch.setattr(get_settings(), "flex_espera_envio_min", 7)
    assert flex_config.espera_envio_min() == 7
    # Campo vazio na tela = .env; pedido no .sp desligado na tela = sem espera.
    flex_config.usar(flex_config.Ajustes(None, None, False))
    assert (flex_config.modo(), flex_config.shopee_escrita()) == ("observar", False)
    assert flex_config.espera_envio_min() == 0
    assert flex_config.origem("modo") == "servidor"
    assert flex_config.origem("pedido_no_sp") == "tela"


def test_quem_pode_ajustar():
    class U:
        def __init__(self, role, email="x@y.com", status=None):
            from app.models import UserStatus

            self.role, self.email = role, email
            self.status = status or UserStatus.ACTIVE
            self.name = "churchill"

    assert flex_config.pode_ajustar(U(UserRole.ADMIN)) is True
    assert flex_config.pode_ajustar(U(UserRole.USER, "SA.Geral@tutamail.com")) is True
    assert flex_config.pode_ajustar(U(UserRole.USER)) is False  # churchill vê, não troca
    assert flex_config.pode_ajustar(None) is False


@pytest.mark.asyncio
async def test_tela_troca_grava_e_outro_processo_le(
    client: AsyncClient, db: AsyncSession, make_user, auth_as: Callable
):
    auth_as(await make_user(role=UserRole.ADMIN))
    c = (await client.get("/api/flex/config")).json()
    assert (c["modo"], c["shopee_escrita"], c["pedido_no_sp"]) == ("observar", False, False)
    assert c["ajustes"]["modo_origem"] == "servidor"
    assert c["pode_ajustar"] is True

    corpo = {
        "modo": "piloto",
        "shopee_escrita": True,
        "pedido_no_sp": False,
        "modo_antes": "observar",
        "shopee_escrita_antes": False,
        "pedido_no_sp_antes": False,
    }
    r = await client.put("/api/flex/ajustes", json=corpo)
    assert r.status_code == 200, r.text
    assert (r.json()["modo_origem"], r.json()["shopee_escrita_origem"]) == ("tela", "tela")
    assert r.json()["atualizado_por"]

    c = (await client.get("/api/flex/config")).json()
    assert (c["modo"], c["pode_escrever"], c["shopee_escrita"], c["pedido_no_sp"]) == (
        "piloto",
        True,
        True,
        False,
    )
    linha = await db.get(FlexAjuste, 1)
    await db.refresh(linha)
    assert (linha.modo, linha.shopee_escrita, linha.pedido_no_sp) == ("piloto", True, False)

    # Outro processo (o worker) começa sem cache e relê do banco.
    flex_config.limpar_cache()
    await flex_config.carregar(forcar=True)
    assert (flex_config.modo(), flex_config.shopee_escrita()) == ("piloto", True)

    # Quem abriu o formulário com o valor velho não sobrescreve: 409.
    r = await client.put("/api/flex/ajustes", json={**corpo, "modo": "ativo"})
    assert r.status_code == 409
    # Modo que não existe: 422.
    r = await client.put("/api/flex/ajustes", json={**corpo, "modo": "turbo", "modo_antes": None})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_so_admin_e_gerente_trocam(
    client: AsyncClient, make_user, auth_as: Callable
):
    corpo = {"modo": "piloto", "shopee_escrita": True, "pedido_no_sp": True}
    churchill = await make_user(role=UserRole.USER, permissions=PERMS)
    churchill.name = "churchill"
    auth_as(churchill)
    c = (await client.get("/api/flex/config")).json()
    assert c["pode_ajustar"] is False
    r = await client.put("/api/flex/ajustes", json=corpo)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "so_admin"

    gerente = await make_user(role=UserRole.USER, permissions=PERMS, email="sa.geral@tutamail.com")
    auth_as(gerente)
    assert (await client.get("/api/flex/config")).json()["pode_ajustar"] is True
    assert (await client.put("/api/flex/ajustes", json=corpo)).status_code == 200


@pytest.mark.asyncio
async def test_cron_obedece_o_modo_da_tela(db: AsyncSession, monkeypatch):
    """O .env diz "observar", a tela diz "desligado": a varredura não roda."""
    db.add(FlexAjuste(id=1, modo="desligado"))
    await db.commit()
    rodou = []

    async def _rodar(**kw):
        rodou.append(kw)
        return {}

    monkeypatch.setattr(flex_motor, "rodar_motor", _rodar)
    await worker.flex_motor_tick({})
    assert rodou == []
    # Volta para "observar" na tela: roda.
    linha = await db.get(FlexAjuste, 1)
    linha.modo = "observar"
    await db.commit()
    await worker.flex_motor_tick({})
    assert len(rodou) == 1
