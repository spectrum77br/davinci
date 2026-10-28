"""GET /api/marketing/agent/horarios — o robô do Mac lê o mapa (28/10/2026).

O que fica travado:
  • fechado sem o X-Agent-Token certo (e com o token vazio nas configurações);
  • só Shopee, sem conta arquivada e sem conta sem perfil do AdsPower;
  • `dia` 0=segunda, `fim` 0 vira 24 (a hora das 23h é gravada 23→0);
  • conta sem hora marcada vem com `blocos` vazio (não some da lista);
  • só leitura: não cria comando nem liga `schedule_enabled`.

Nomes e perfis INVENTADOS.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select, text

from app.config import get_settings
from app.models import MarketingAccount
from app.models.marketing import MarketingCommand, MarketingSchedule

pytestmark = pytest.mark.asyncio

API = "/api/marketing/agent/horarios"
TOKEN = "token-de-teste-horarios"


@pytest.fixture(scope="module", autouse=True)
def _monta_router():
    """`app/main.py` só inclui o router de Marketing com `enable_marketing`
    ligado (o ambiente de teste não liga): sem isto as rotas seriam 404."""
    from app.main import app
    from app.routers import marketing as marketing_router

    if not any(getattr(r, "path", "") == API for r in app.routes):
        app.include_router(marketing_router.router)


@pytest.fixture(autouse=True)
async def _limpa(db):
    async def wipe():
        for tbl in ("marketing_commands", "marketing_schedules", "marketing_accounts"):
            await db.execute(text(f"DELETE FROM {tbl}"))  # noqa: S608
        await db.commit()

    await wipe()
    yield
    await wipe()


@pytest.fixture
def agent_token(monkeypatch):
    monkeypatch.setattr(get_settings(), "marketing_agent_token", TOKEN)
    return {"X-Agent-Token": TOKEN}


async def _conta(db, nome, platform="shopee", perfil="k1teste", **kw) -> MarketingAccount:
    a = MarketingAccount(
        name=nome, platform=platform, department="geral", acos_target=7.0,
        agent_enabled=False, adspower_user_id=perfil, **kw,
    )
    db.add(a)
    await db.commit()
    return a


async def _horas(db, conta, *blocos):
    for dia, ini, fim in blocos:
        db.add(MarketingSchedule(account_id=conta.id, day_of_week=dia, start_hour=ini, end_hour=fim))
    await db.commit()


async def test_fechado_sem_token(client, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "marketing_agent_token", TOKEN)
    assert (await client.get(API)).status_code == 401
    assert (await client.get(API, headers={"X-Agent-Token": "errado"})).status_code == 401
    monkeypatch.setattr(get_settings(), "marketing_agent_token", "")
    assert (await client.get(API, headers={"X-Agent-Token": ""})).status_code == 401


async def test_devolve_o_mapa_da_shopee(client, db, agent_token):
    alfa = await _conta(db, "  Alfa Malas", perfil="k1alfa")
    beta = await _conta(db, "Beta Cel", perfil="k1beta")
    await _conta(db, "Gama Vazia", perfil="k1gama")
    await _conta(db, "Ml Qualquer", platform="ml", perfil=None)
    await _conta(db, "Sem Perfil", perfil=None)
    arquivada = await _conta(db, "Arquivada", perfil="k1arq")
    await db.execute(
        text("UPDATE marketing_accounts SET arquivada_em = now() WHERE id = :i"), {"i": arquivada.id}
    )
    await db.commit()
    # segunda 6h–8h e a hora das 23h (gravada 23→0); domingo 18h–22h
    await _horas(db, alfa, (0, 6, 7), (0, 7, 8), (0, 23, 0), (6, 18, 22))
    await _horas(db, beta, (2, 12, 13))

    r = await client.get(API, headers=agent_token)
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["gerado_em"]
    contas = {c["adspower_user_id"]: c for c in corpo["contas"]}
    assert set(contas) == {"k1alfa", "k1beta", "k1gama"}, "só Shopee ativa com perfil"
    assert contas["k1alfa"]["nome"] == "Alfa Malas", "nome sem espaço na frente"
    assert contas["k1alfa"]["blocos"] == [
        {"dia": 0, "inicio": 6, "fim": 7},
        {"dia": 0, "inicio": 7, "fim": 8},
        {"dia": 0, "inicio": 23, "fim": 24},
        {"dia": 6, "inicio": 18, "fim": 22},
    ]
    assert contas["k1alfa"]["alterado_em"]
    assert contas["k1beta"]["blocos"] == [{"dia": 2, "inicio": 12, "fim": 13}]
    assert contas["k1gama"]["blocos"] == [], "conta sem hora vem vazia, não some"
    assert contas["k1gama"]["alterado_em"] is None


async def test_so_leitura(client, db, agent_token):
    alfa = await _conta(db, "Alfa", perfil="k1alfa")
    await _horas(db, alfa, (0, 18, 22))
    assert (await client.get(API, headers=agent_token)).status_code == 200
    assert (await db.execute(select(func.count()).select_from(MarketingCommand))).scalar() == 0
    await db.refresh(alfa)
    assert alfa.schedule_enabled is False
