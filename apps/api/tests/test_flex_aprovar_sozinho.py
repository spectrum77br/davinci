"""Flex: "Aprovar sozinho" (Vinicius, 13/11/2026) e a marca "desligado à mão".

"Quando ele vê um anúncio que pode ter Flex ele fica esperando aprovar —
sempre que ele ver que está tudo certo, coisa que ele já faz, ele mesmo pode
aprovar." Shopee e Mercado Livre.

  • com a chave ligada, em piloto/ativo, o anúncio que pediria aprovação é
    aprovado pelo sistema e liga na mesma rodada (as conferências de sempre);
  • em observar nada é aprovado;
  • o anúncio que uma pessoa desligou (no painel da plataforma ou no
    "Desligar selecionados") não volta sozinho — espera alguém aprovar;
  • a chave é trocada na tela (Chaves do Flex).

Nenhuma chamada externa: os clientes falsos de tests/test_flex_motor.
"""

# ruff: noqa: F811
from __future__ import annotations

from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FlexAjuste, FlexLog, UserRole
from app.services import flex_config, flex_motor
from tests.test_flex_motor import (  # noqa: F401 — fixtures
    _estados,
    _trilha,
    mundo,
)


@pytest.fixture(autouse=True)
def _flex_visivel(monkeypatch):
    monkeypatch.setattr(flex_config, "pode_ver", lambda user: True)


async def _estoque_sp(db: AsyncSession, n: int) -> None:
    await db.execute(text("UPDATE products SET stock = :n WHERE sku = 'dg053.sp'"), {"n": n})
    await db.commit()


def _ligar_sozinho(mundo, monkeypatch, modo: str = "piloto") -> None:
    monkeypatch.setattr(mundo["cfg"], "flex_modo", modo)
    monkeypatch.setattr(mundo["cfg"], "flex_aprovar_sozinho", True, raising=False)


@pytest.mark.asyncio
async def test_sem_a_chave_continua_esperando_aprovacao(db, mundo, monkeypatch):
    monkeypatch.setattr(mundo["cfg"], "flex_modo", "piloto")
    assert flex_config.aprovar_sozinho() is False
    ml = mundo["ml"]
    await flex_motor.rodar_motor()
    assert ("ligar", "MLB1") not in ml.escritas
    est = await _estados(db)
    assert (est["MLB1"].aguardando_aprovacao, est["MLB1"].aprovado_em) == (True, None)


@pytest.mark.asyncio
async def test_aprova_sozinho_e_liga_com_a_conta_relida(db, mundo, monkeypatch):
    """1ª rodada: a conta é lida agora (sem cache) — aprova e liga na mesma
    rodada. Com a conta em cache, liga na seguinte (ver o teste da peça)."""
    _ligar_sozinho(mundo, monkeypatch)
    ml = mundo["ml"]
    resumo = await flex_motor.rodar_motor()
    assert ("ligar", "MLB1") in ml.escritas and ("ligar", "MLB2") in ml.escritas
    assert resumo["aprovado_sozinho"] == 2
    est = await _estados(db)
    for ext in ("MLB1", "MLB2"):
        assert est[ext].observado == "ligado"
        assert est[ext].aguardando_aprovacao is False
        # A aprovação foi usada na ligação (como a de uma pessoa).
        assert est[ext].aprovado_em is None
    trilha = await _trilha(db, "MLB1")
    assert ("aprovar", "ok") in trilha and ("ligar", "ok") in trilha
    assert ("pedir_aprovacao", "pendente") not in trilha
    log = (
        await db.execute(
            select(FlexLog).where(FlexLog.acao == "aprovar", FlexLog.external_id == "MLB1")
        )
    ).scalar_one()
    assert log.por is None
    assert log.motivo.startswith(flex_motor.MOTIVO_APROVADO_SOZINHO)


@pytest.mark.asyncio
async def test_em_observar_nao_aprova(db, mundo, monkeypatch):
    _ligar_sozinho(mundo, monkeypatch, modo="observar")
    ml = mundo["ml"]
    await flex_motor.rodar_motor()
    assert ml.escritas == []
    est = await _estados(db)
    assert (est["MLB1"].aguardando_aprovacao, est["MLB1"].aprovado_em) == (True, None)


@pytest.mark.asyncio
async def test_desligado_no_painel_nao_volta_sozinho(db, mundo, monkeypatch):
    """Ligou sozinho; alguém desliga no painel do ML. O sistema não religa: a
    marca "desligado à mão" fica e o anúncio espera aprovação. A pessoa
    aprova: liga e a marca some."""
    _ligar_sozinho(mundo, monkeypatch)
    ml = mundo["ml"]
    await flex_motor.rodar_motor()
    assert ml.estado["MLB1"] is True

    ml.estado["MLB1"] = False
    ml.chamadas.clear()
    await flex_motor.rodar_motor()
    assert ("ligar", "MLB1") not in ml.chamadas
    est = (await _estados(db))["MLB1"]
    assert (est.observado, est.aguardando_aprovacao) == ("desligado", True)
    assert est.desligado_mao_em is not None and est.desligado_mao_por is None

    # Outra rodada: continua sem religar.
    ml.chamadas.clear()
    await flex_motor.rodar_motor()
    assert ("ligar", "MLB1") not in ml.chamadas

    res = await flex_motor.aprovar(mundo["conta_id"], "MLB1", por=mundo["dono_id"])
    assert res["aplicado"] is True
    est = (await _estados(db))["MLB1"]
    assert est.observado == "ligado"
    assert (est.desligado_mao_em, est.desligado_mao_por) == (None, None)


@pytest.mark.asyncio
async def test_desligar_selecionado_nao_volta_sozinho(db, mundo, monkeypatch):
    _ligar_sozinho(mundo, monkeypatch)
    ml = mundo["ml"]
    await flex_motor.rodar_motor()
    res = await flex_motor.desligar(mundo["conta_id"], "MLB1", por=mundo["dono_id"])
    assert res["resultado"] == "ok"
    est = (await _estados(db))["MLB1"]
    assert est.desligado_mao_por == mundo["dono_id"] and est.desligado_mao_em is not None

    ml.chamadas.clear()
    await flex_motor.rodar_motor()
    assert ("ligar", "MLB1") not in ml.chamadas
    est = await _estados(db)
    assert (est["MLB1"].observado, est["MLB1"].aguardando_aprovacao) == ("desligado", True)
    assert est["MLB2"].observado == "ligado"  # o outro segue ligado


@pytest.mark.asyncio
async def test_sistema_desliga_por_falta_de_peca_e_religa_quando_volta(db, mundo, monkeypatch):
    """Quem desligou foi o SISTEMA (a peça acabou): não é "à mão" — quando a
    peça volta, ele aprova e liga sozinho de novo."""
    _ligar_sozinho(mundo, monkeypatch)
    ml = mundo["ml"]
    await flex_motor.rodar_motor()
    assert ml.estado["MLB1"] is True

    await _estoque_sp(db, 0)
    await flex_motor.rodar_motor()
    assert ml.estado["MLB1"] is False
    est = (await _estados(db))["MLB1"]
    assert est.desligado_mao_em is None

    await _estoque_sp(db, 5)
    ml.chamadas.clear()
    resumo = await flex_motor.rodar_motor()
    # Aprovado nesta rodada; a conta não foi relida agora (cache de 1 h),
    # então LIGAR espera a próxima — que relê a conta por ter aprovação.
    assert resumo["aprovado_sozinho"] == 2
    assert resumo["ligar_esperando_releitura"] == 2
    assert ("ligar", "MLB1") not in ml.chamadas
    await flex_motor.rodar_motor()
    assert ("ligar", "MLB1") in ml.chamadas
    assert ml.estado["MLB1"] is True


@pytest.mark.asyncio
async def test_tela_liga_aprovar_sozinho(
    client: AsyncClient, db: AsyncSession, make_user, auth_as: Callable
):
    auth_as(await make_user(role=UserRole.ADMIN))
    c = (await client.get("/api/flex/config")).json()
    assert (c["aprovar_sozinho"], c["ajustes"]["aprovar_sozinho_origem"]) == (False, "servidor")
    base = {"modo": "piloto", "shopee_escrita": False, "pedido_no_sp": False}
    r = await client.put(
        "/api/flex/ajustes",
        json={**base, "aprovar_sozinho": True, "aprovar_sozinho_antes": False},
    )
    assert r.status_code == 200, r.text
    assert r.json()["aprovar_sozinho_origem"] == "tela"
    assert (await client.get("/api/flex/config")).json()["aprovar_sozinho"] is True
    linha = await db.get(FlexAjuste, 1)
    await db.refresh(linha)
    assert linha.aprovar_sozinho is True

    # A tela antiga (sem o campo) não mexe na chave.
    r = await client.put("/api/flex/ajustes", json={**base, "modo": "ativo"})
    assert r.status_code == 200, r.text
    await db.refresh(linha)
    assert (linha.modo, linha.aprovar_sozinho) == ("ativo", True)
    # Quem abriu com "NÃO" na tela não sobrescreve: 409.
    r = await client.put(
        "/api/flex/ajustes",
        json={**base, "modo": "ativo", "aprovar_sozinho": False, "aprovar_sozinho_antes": False},
    )
    assert r.status_code == 409
