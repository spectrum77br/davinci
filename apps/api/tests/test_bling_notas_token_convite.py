"""Conta de NF com refresh_token morto tem que aceitar um convite novo.

Eduardo, 15/09/2026: renovou os tokens e a tela de Pós Vendas continuou vazia
em 13 das 14 empresas. O motivo estava aqui: o refresher escolhia o caminho por
`if refresh_token: ... else: authorization_code`, então uma conta que TINHA um
refresh_token (morto) nunca chegava a usar o código de convite novo. Colar o
convite pela tela não surtia efeito nenhum e a conta ficava trancada para
sempre.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select, text

from app.models import BlingNota
from app.services import bling_notas_token_refresh as svc

pytestmark = pytest.mark.asyncio


@asynccontextmanager
async def _lock_livre(_chave):
    yield True


def _payload(token: str) -> dict:
    return {"access_token": token, "refresh_token": f"rt-{token}", "expires_in": 21600}


async def _conta(db, nome: str, *, rt: str | None, code: str | None) -> BlingNota:
    nota = BlingNota(
        nome=nome,
        client_id="cid",
        basic_auth_b64="Y2lkOnNlY3JldA==",
        refresh_token=rt,
        authorization_code=code,
        access_token="antigo",  # noqa: S106
        status="active",
    )
    db.add(nota)
    await db.commit()
    return nota


async def test_refresh_morto_com_convite_novo_troca_pelo_codigo(db, monkeypatch):
    await db.execute(text("DELETE FROM bling_notas"))
    await _conta(db, "josefinaapp", rt="rt-morto", code="convite-novo")

    chamadas: list[str] = []

    async def fake_post(basic_b64, data):
        chamadas.append(data["grant_type"])
        if data["grant_type"] == "refresh_token":
            raise RuntimeError("invalid_grant")
        return _payload("novo")

    monkeypatch.setattr(svc, "_post_oauth_token", fake_post)
    monkeypatch.setattr(svc, "token_refresh_lock", _lock_livre)

    resumo = await svc.run_refresh_bling_notas_tokens(db)

    assert chamadas == ["refresh_token", "authorization_code"]
    assert resumo["exchanged"] == 1
    assert resumo["failed"] == 0

    nota = (await db.execute(select(BlingNota))).scalars().one()
    assert nota.access_token == "novo"  # noqa: S105
    # Código é de uso único: some depois do sucesso.
    assert nota.authorization_code is None


async def test_conta_saudavel_nao_gasta_o_convite(db, monkeypatch):
    """O código antigo que ficou na linha não pode atropelar um refresh que
    está funcionando — senão a correção quebraria as contas sãs."""
    await db.execute(text("DELETE FROM bling_notas"))
    await _conta(db, "poofy.app", rt="rt-bom", code="convite-velho")

    chamadas: list[str] = []

    async def fake_post(basic_b64, data):
        chamadas.append(data["grant_type"])
        return _payload("renovado")

    monkeypatch.setattr(svc, "_post_oauth_token", fake_post)
    monkeypatch.setattr(svc, "token_refresh_lock", _lock_livre)

    resumo = await svc.run_refresh_bling_notas_tokens(db)

    assert chamadas == ["refresh_token"]
    assert resumo["refreshed"] == 1
    nota = (await db.execute(select(BlingNota))).scalars().one()
    assert nota.authorization_code == "convite-velho"


async def test_refresh_morto_sem_convite_continua_falhando(db, monkeypatch):
    """Sem convite na linha não há o que fazer: a conta segue falhando e
    aparece na contagem de erro, como antes."""
    await db.execute(text("DELETE FROM bling_notas"))
    await _conta(db, "nexusintermediacoes.app", rt="rt-morto", code=None)

    async def fake_post(basic_b64, data):
        raise RuntimeError("invalid_grant")

    monkeypatch.setattr(svc, "_post_oauth_token", fake_post)
    monkeypatch.setattr(svc, "token_refresh_lock", _lock_livre)

    resumo = await svc.run_refresh_bling_notas_tokens(db)

    assert resumo["failed"] == 1
    assert resumo["refreshed"] == 0
    assert resumo["exchanged"] == 0
