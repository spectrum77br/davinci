"""Inmetro no cadastro existente, permissões e proteção dos campos comprovados."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID

import fitz
import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text

import app.db as app_db
from app.models import UserRole
from app.models.financeiro import (
    CertificacoesSyncHistorico,
    CertificacoesSyncState,
    FinanceiroSuprimentos,
)
from app.services import certificacoes_inmetro_sync as sync

BASE = "/api/financeiro/suprimentos"


def _record(**fields):
    return {
        "chave": "MODERNA|MODERNA-0069/25|UAF001-M1", "cnpj": "40191104000145",
        "nome_empresa": "MAKISA TRADING LTDA", "certificador": "MODERNA",
        "numero": "MODERNA-0069/25", "modelo": "UAF001-M1", "marca": "URANYX",
        "produto": "Fritadeira", "descricao": "Fritadeira 4L",
        "inicio": "2026-04-30", "fim": "2032-04-30", "situacao_certificado": "Ativo",
        "dados_origem": {"Modelo": "UAF001-M1"}, "alertas": [],
        "campos_confirmados": ["modelo", "certificado", "numero", "inicio", "fim"],
        **fields,
    }


@pytest_asyncio.fixture(autouse=True)
async def _cleanup(db):
    for model in (CertificacoesSyncHistorico, CertificacoesSyncState, FinanceiroSuprimentos):
        await db.execute(delete(model))
    await db.commit()
    yield
    for model in (CertificacoesSyncHistorico, CertificacoesSyncState, FinanceiroSuprimentos):
        await db.execute(delete(model))
    await db.commit()


@pytest.fixture
def source(monkeypatch):
    mocked = AsyncMock(return_value={
        "source_url": "http://www.inmetro.gov.br/prodcert/", "source_updated_at": None,
        "sha256": "a" * 64, "records": [_record()],
    })
    monkeypatch.setattr(sync, "fetch_certificacoes", mocked)
    return mocked


async def _manual(client):
    response = await client.post(BASE, json={
        "produto": "airfryer", "modelo": "UAF001", "nome_comercial": "M1, M2",
        "certificado": "inmetro", "numero": "MODERNA-0069/25", "valor": 123,
        "fim": "2027-08-03",
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_sync_links_existing_record_and_preserves_anatel_state(
    client, make_user, auth_as, db, source,
):
    auth_as(await make_user(role=UserRole.ADMIN))
    row_id = await _manual(client)
    db.add(CertificacoesSyncState(id="anatel_makisa", erro="falha somente da Anatel"))
    await db.commit()
    result = await client.post(f"{BASE}/inmetro/sincronizar")
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "ok"
    assert result.json()["atualizados"] == 1 and result.json()["criados"] == 0
    rows = (await client.get(BASE)).json()
    assert len(rows) == 1 and rows[0]["id"] == row_id
    assert rows[0]["modelo"] == "UAF001-M1"
    assert rows[0]["nome_comercial"] == "M1, M2"
    assert rows[0]["inmetro_dados"]["situacao_certificado"] == "Ativo"
    assert rows[0]["anatel_numero"] is None
    status = (await client.get(f"{BASE}/inmetro/status")).json()
    assert status["automatico"] and status["ultimo_sucesso_em"]
    assert status["source_updated_at"] is None
    assert status["erro"] is None
    assert (await client.get(f"{BASE}/anatel/status")).json()["erro"] == "falha somente da Anatel"
    history = (await client.get(f"{BASE}/{row_id}/historico")).json()
    assert len(history) == 1
    assert history[0]["fonte"] == "inmetro"
    assert history[0]["antes"]["modelo"] == "UAF001"
    assert history[0]["antes"]["fim"] == "2027-08-03"


async def test_read_only_fields_preserve_custom_labels_price_and_pdf(
    client, make_user, auth_as, source,
):
    auth_as(await make_user(role=UserRole.ADMIN))
    row_id = await _manual(client)
    assert (await client.post(f"{BASE}/inmetro/sincronizar")).json()["status"] == "ok"
    for field, value in {"modelo": "Outro", "numero": "Outro", "certificado": "anatel",
                         "inicio": "2020-01-01", "fim": "2020-01-01"}.items():
        response = await client.patch(f"{BASE}/{row_id}", json={field: value})
        assert response.status_code == 409, response.text
    assert (await client.delete(f"{BASE}/{row_id}")).status_code == 409
    response = await client.patch(f"{BASE}/{row_id}", json={
        "produto": "Minha fritadeira", "nome_comercial": "Meu nome comercial", "valor": 42,
    })
    assert response.status_code == 200
    with fitz.open() as pdf:
        pdf.new_page().insert_text((40, 40), "Certificado original")
        content = pdf.tobytes()
    response = await client.post(f"{BASE}/{row_id}/pdf", files={
        "file": ("original.pdf", content, "application/pdf"),
    })
    assert response.status_code == 200
    assert (await client.post(f"{BASE}/inmetro/sincronizar")).json()["status"] == "ok"
    assert (await client.get(f"{BASE}/{row_id}/pdf")).content == content
    row = (await client.get(BASE)).json()[0]
    assert row["produto"] == "Minha fritadeira" and row["nome_comercial"] == "Meu nome comercial"
    assert row["valor"] == "42.00"


async def test_date_not_confirmed_by_source_remains_manual(client, make_user, auth_as, source):
    auth_as(await make_user(role=UserRole.ADMIN))
    row_id = await _manual(client)
    source.return_value["records"] = [_record(
        fim=None, campos_confirmados=["modelo", "certificado", "numero", "inicio"],
    )]
    assert (await client.post(f"{BASE}/inmetro/sincronizar")).json()["status"] == "ok"
    assert (await client.get(BASE)).json()[0]["fim"] == "2027-08-03"
    assert (await client.patch(f"{BASE}/{row_id}", json={"fim": "2029-01-01"})).status_code == 200
    response = await client.patch(f"{BASE}/{row_id}", json={"inicio": "2029-01-01"})
    assert response.status_code == 409


async def test_permissions_and_failed_source_keep_last_success(client, make_user, auth_as, source):
    auth_as(await make_user(role=UserRole.ADMIN))
    await _manual(client)
    assert (await client.post(f"{BASE}/inmetro/sincronizar")).json()["status"] == "ok"
    before = (await client.get(f"{BASE}/inmetro/status")).json()
    source.side_effect = TimeoutError()
    result = await client.post(f"{BASE}/inmetro/sincronizar")
    assert result.json()["status"] == "error"
    after = (await client.get(f"{BASE}/inmetro/status")).json()
    assert after["ultimo_sucesso_em"] == before["ultimo_sucesso_em"]
    assert after["erro"]
    auth_as(await make_user(permissions={"financeiro_suprimentos": {"view": True}}))
    assert (await client.get(f"{BASE}/inmetro/status")).status_code == 200
    assert (await client.post(f"{BASE}/inmetro/sincronizar")).status_code == 403
    auth_as(await make_user())
    assert (await client.get(f"{BASE}/inmetro/status")).status_code == 403


@pytest.mark.parametrize("method", ["patch", "delete"])
async def test_mutation_waits_and_checks_inmetro_link(client, make_user, auth_as, db, method):
    auth_as(await make_user(role=UserRole.ADMIN))
    row_id = UUID(await _manual(client))
    row = await db.scalar(select(FinanceiroSuprimentos).where(
        FinanceiroSuprimentos.id == row_id,
    ).with_for_update())
    blocker = await db.scalar(text("SELECT pg_backend_pid()"))
    row.inmetro_chave = _record()["chave"]
    row.inmetro_dados = _record()
    row.inmetro_consultado_em = datetime.now(UTC)
    await db.flush()

    async def wait_for_lock():
        async with app_db.SessionLocal() as observer:
            while True:
                blocked = await observer.scalar(text(
                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                    "WHERE :blocker = ANY(pg_blocking_pids(pid)))"
                ), {"blocker": blocker})
                await observer.rollback()
                if blocked:
                    return
                await asyncio.sleep(0.01)

    request = asyncio.create_task(
        client.patch(f"{BASE}/{row_id}", json={"modelo": "Outro"}) if method == "patch"
        else client.delete(f"{BASE}/{row_id}")
    )
    try:
        await asyncio.wait_for(wait_for_lock(), timeout=5)
        await db.commit()
        assert (await asyncio.wait_for(request, timeout=5)).status_code == 409
        assert len((await client.get(BASE)).json()) == 1
    finally:
        if not request.done():
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)
        await db.rollback()
