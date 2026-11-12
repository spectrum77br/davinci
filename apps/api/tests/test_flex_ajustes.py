"""Ajustes do Flex trocados na tela (Vinicius, 11/11/2026): o modo, a escrita
da Shopee e o pedido Flex no .sp saíram do .env do servidor (só o Eduardo
mexia) para a aba Flex — "pode fazer, mas ainda deixa tudo desligado".

Campo vazio na tela = vale o .env; sem linha nenhuma, nada muda.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import worker
from app.config import get_settings
from app.models import FlexAjuste, Integration, IntegrationPlatform, UserRole
from app.security.cipher import encrypt_json
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


# ---- "Kits entram" (0475, 12/11/2026) -------------------------------------------


def test_kits_da_tela_vencem_o_env(monkeypatch):
    """Vinicius: "os kit tem que entrar, desde que tenha tudo em .sp do kit" —
    a chave saiu do .env (flex_kits) para a tela; vazio = vale o .env."""
    monkeypatch.setattr(get_settings(), "flex_kits", False)
    assert flex_config.kits() is False
    assert flex_motor.ConfigFlex.das_configuracoes().kits is False
    flex_config.usar(flex_config.Ajustes(kits=True))
    assert flex_config.kits() is True
    assert flex_motor.ConfigFlex.das_configuracoes().kits is True
    assert flex_config.origem("kits") == "tela"
    # Com a chave, o kit vira o kit .sp (o estoque da peça mais escassa).
    assert flex_motor.analisar_sku("dg053.ci+a003.ci", kits=True) == (
        "dg053+a003",
        "dg053.sp+a003.sp",
    )
    flex_config.usar(flex_config.Ajustes(kits=None))
    assert (flex_config.kits(), flex_config.origem("kits")) == (False, "servidor")


@pytest.mark.asyncio
async def test_tela_liga_os_kits_e_a_tela_antiga_nao_mexe(
    client: AsyncClient, db: AsyncSession, make_user, auth_as: Callable
):
    auth_as(await make_user(role=UserRole.ADMIN))
    c = (await client.get("/api/flex/config")).json()
    assert (c["kits"], c["ajustes"]["kits_origem"]) == (False, "servidor")
    base = {"modo": "observar", "shopee_escrita": False, "pedido_no_sp": False}
    liga = {**base, "kits": True, "kits_antes": False, "modo_antes": "observar"}
    r = await client.put("/api/flex/ajustes", json=liga)
    assert r.status_code == 200, r.text
    assert r.json()["kits_origem"] == "tela"
    c = (await client.get("/api/flex/config")).json()
    assert c["kits"] is True
    linha = await db.get(FlexAjuste, 1)
    await db.refresh(linha)
    assert linha.kits is True

    # A tela antiga (sem o campo) troca o resto e NÃO mexe nos kits.
    r = await client.put("/api/flex/ajustes", json={**base, "modo": "piloto"})
    assert r.status_code == 200, r.text
    await db.refresh(linha)
    assert (linha.modo, linha.kits) == ("piloto", True)
    # Quem abriu com "kits NÃO" na tela não sobrescreve: 409.
    r = await client.put("/api/flex/ajustes", json={**base, "kits": False, "kits_antes": False})
    assert r.status_code == 409


# ---- Contas do Flex na tela (0477, 12/11/2026) -----------------------------------


def test_contas_da_tela_vencem_o_env(monkeypatch):
    """Vinicius, 12/11/2026 ("Sim, na tela"): a atlas do ML entrou no DaVinci e
    a lista de contas do Flex só mudava no .env. Vazio na tela = vale o .env;
    lista vazia na tela = nenhuma conta."""
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(get_settings(), "flex_contas", f"{a},{b}")
    assert flex_config.contas() == {a, b}
    assert flex_config.origem("contas") == "servidor"
    flex_config.usar(flex_config.Ajustes(contas=frozenset({c})))
    assert flex_config.contas() == {c}
    assert flex_config.origem("contas") == "tela"
    # O valor passado à mão (o motor/testes) continua sendo lido como texto.
    assert flex_config.contas(f"{b}") == {b}
    flex_config.usar(flex_config.Ajustes(contas=frozenset()))
    assert flex_config.contas() == frozenset()
    flex_config.usar(flex_config.Ajustes(contas=None))
    assert flex_config.contas() == {a, b}


async def _contas_de_teste(db: AsyncSession, dono) -> dict[str, Integration]:
    agora = datetime.now(UTC)
    contas = {
        "poofy": Integration(user_id=dono.id, platform=IntegrationPlatform.ML, name="Poofy"),
        "atlas": Integration(user_id=dono.id, platform=IntegrationPlatform.ML, name="atlas"),
        "fiore": Integration(user_id=dono.id, platform=IntegrationPlatform.SHOPEE, name="Fiore"),
        "jlas": Integration(
            user_id=dono.id, platform=IntegrationPlatform.ML, name="jlas", archived_at=agora
        ),
        "bling": Integration(user_id=dono.id, platform=IntegrationPlatform.BLING, name="Bling"),
    }
    for i in contas.values():
        i.credentials = encrypt_json({"access_token": "t"})
    db.add_all(list(contas.values()))
    await db.commit()
    return contas


@pytest.mark.asyncio
async def test_tela_escolhe_as_contas(
    client: AsyncClient, db: AsyncSession, make_user, auth_as: Callable, monkeypatch
):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    k = await _contas_de_teste(db, admin)
    fantasma = uuid.uuid4()
    # O .env de hoje: Poofy, Fiore, uma arquivada (jlas) e um id que não existe.
    monkeypatch.setattr(
        get_settings(), "flex_contas", f"{k['poofy'].id},{k['fiore'].id},{k['jlas'].id},{fantasma}"
    )
    c = (await client.get("/api/flex/config")).json()
    assert c["ajustes"]["contas_origem"] == "servidor"
    # As opções: só ML/Shopee ATIVAS, ML primeiro; arquivada e Bling ficam fora.
    ops = [(o["nome"], o["plataforma"], o["no_flex"]) for o in c["contas_opcoes"]]
    assert ops == [("atlas", "ml", False), ("Poofy", "ml", True), ("Fiore", "shopee", True)]

    base = {"modo": "observar", "shopee_escrita": False, "pedido_no_sp": False}
    marcadas_antes = [str(k["poofy"].id), str(k["fiore"].id)]
    novas = [*marcadas_antes, str(k["atlas"].id)]
    r = await client.put(
        "/api/flex/ajustes", json={**base, "contas": novas, "contas_antes": marcadas_antes}
    )
    assert r.status_code == 200, r.text
    assert r.json()["contas_origem"] == "tela"
    # A lista inteira vira a da tela: a arquivada e o fantasma saem.
    assert flex_config.contas() == {k["poofy"].id, k["fiore"].id, k["atlas"].id}
    linha = await db.get(FlexAjuste, 1)
    await db.refresh(linha)
    assert set(linha.contas) == {k["poofy"].id, k["fiore"].id, k["atlas"].id}
    c = (await client.get("/api/flex/config")).json()
    assert {x["nome"] for x in c["contas"]} == {"Poofy", "Fiore", "atlas"}
    assert all(o["no_flex"] for o in c["contas_opcoes"])

    # Outro processo (o worker) relê do banco.
    flex_config.limpar_cache()
    await flex_config.carregar(forcar=True)
    assert k["atlas"].id in flex_config.contas()

    # A tela antiga (sem o campo) troca o resto e NÃO mexe nas contas.
    r = await client.put("/api/flex/ajustes", json={**base, "modo": "piloto"})
    assert r.status_code == 200, r.text
    assert flex_config.contas() == {k["poofy"].id, k["fiore"].id, k["atlas"].id}

    # Quem abriu com a lista velha não sobrescreve: 409.
    r = await client.put(
        "/api/flex/ajustes",
        json={**base, "modo": "piloto", "contas": marcadas_antes, "contas_antes": marcadas_antes},
    )
    assert r.status_code == 409
    # Conta arquivada ou do Bling não entra: 400.
    for estranha in (k["jlas"], k["bling"]):
        r = await client.put(
            "/api/flex/ajustes",
            json={
                **base,
                "modo": "piloto",
                "contas": [*novas, str(estranha.id)],
                "contas_antes": novas,
            },
        )
        assert r.status_code == 400, r.text
        assert r.json()["detail"]["code"] == "conta_invalida"
    # Desmarcar tudo = nenhuma conta (o motor para de mexer em todas).
    r = await client.put(
        "/api/flex/ajustes",
        json={**base, "modo": "piloto", "contas": [], "contas_antes": novas},
    )
    assert r.status_code == 200, r.text
    assert flex_config.contas() == frozenset()


@pytest.mark.asyncio
async def test_so_quem_ajusta_ve_as_opcoes_de_conta(
    client: AsyncClient, db: AsyncSession, make_user, auth_as: Callable
):
    admin = await make_user(role=UserRole.ADMIN)
    await _contas_de_teste(db, admin)
    churchill = await make_user(role=UserRole.USER, permissions=PERMS)
    churchill.name = "churchill"
    auth_as(churchill)
    c = (await client.get("/api/flex/config")).json()
    assert (c["pode_ajustar"], c["contas_opcoes"]) == (False, [])
