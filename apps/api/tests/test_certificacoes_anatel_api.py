"""Rotas de certificações automáticas: permissões, histórico e concorrência real."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

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
from app.services import certificacoes_sync
from app.services.anatel_certificacoes import SOURCE_URL

BASE = "/api/financeiro/suprimentos"


def _record(**fields):
    return {
        "numero": "071972618234", "cnpj": "40191104000145",
        "nome_empresa": "MAKISA TRADING LTDA", "produto": "Telefone móvel celular",
        "modelos": ["USM003"], "nomes_comerciais": ["Fossibot F112"],
        "tipos_produto": ["Telefone móvel celular"], "fabricantes": ["Fabricante oficial"],
        "certificados": ["CERT-EXEMPLO"], "inicio": "2026-07-30", "fim": "2028-07-30",
        "situacao_certificado": "Ativo", "situacao_requerimento": "Homologação Emitida",
        "dados_origem": [{"Modelo": "USM003"}], "alertas": [], **fields,
    }


def _payload(**record_fields):
    return {
        "source_url": SOURCE_URL, "source_updated_at": "2026-09-14T10:00:00Z",
        "sha256": "a" * 64, "records": [_record(**record_fields)],
    }


def _pdf():
    with fitz.open() as document:
        document.new_page().insert_text((40, 40), "Certificado de teste")
        return document.tobytes()


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_certificacoes(db):
    for model in (CertificacoesSyncHistorico, CertificacoesSyncState, FinanceiroSuprimentos):
        await db.execute(delete(model))
    await db.commit()
    yield
    for model in (CertificacoesSyncHistorico, CertificacoesSyncState, FinanceiroSuprimentos):
        await db.execute(delete(model))
    await db.commit()


@pytest.fixture
def source(monkeypatch):
    mocked = AsyncMock(return_value=_payload())
    monkeypatch.setattr(certificacoes_sync, "fetch_certificacoes", mocked)
    return mocked


async def _sync(client):
    response = await client.post(f"{BASE}/anatel/sincronizar")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ok", response.text
    listed = await client.get(BASE)
    assert listed.status_code == 200, listed.text
    return listed.json()[0]


async def test_status_sync_list_and_history_show_official_metadata(
    client, make_user, auth_as, source,
):
    auth_as(await make_user(role=UserRole.ADMIN))
    status = await client.get(f"{BASE}/anatel/status")
    assert status.status_code == 200
    initial = status.json()
    assert initial["automatico"] is True
    assert initial["periodicidade"] == "diaria"
    assert initial["cnpj"] == "40191104000145"
    assert initial["fonte_url"] == SOURCE_URL
    assert initial["ultimo_sucesso_em"] is None
    assert initial["erro"] is None

    created = await client.post(BASE, json={
        "produto": "smartphone 4G", "certificado": "anatel", "numero": "07197-26-18234",
        "modelo": "Anterior", "fim": "2028-07-01", "valor": 53000,
    })
    assert created.status_code == 201
    assert created.json()["anatel_numero"] is None
    row = await _sync(client)
    assert row["id"] == created.json()["id"]
    assert row["anatel_numero"] == "071972618234"
    assert row["anatel_encontrado"] is True
    assert row["anatel_consultado_em"]
    assert row["anatel_dados"] == _record()
    assert row["modelo"] == "USM003"
    assert row["fim"] == "2028-07-30"
    assert row["produto"] == "smartphone 4G"
    assert Decimal(str(row["valor"])) == Decimal("53000")
    assert "pdf_arquivo" not in row

    state = (await client.get(f"{BASE}/anatel/status")).json()
    assert state["ultimo_sucesso_em"]
    assert state["ultima_tentativa_em"]
    assert state["proxima_tentativa_em"] > state["ultimo_sucesso_em"]
    assert state["resumo"]["atualizados"] == 1
    assert state["source_updated_at"].startswith("2026-09-14T10:00:00")
    history = await client.get(f"{BASE}/{row['id']}/historico")
    assert history.status_code == 200
    before = history.json()[0]
    assert before["evento"] == "vinculacao"
    assert before["antes"]["modelo"] == "Anterior"
    assert before["antes"]["fim"] == "2028-07-01"
    assert before["depois"]["anatel_dados"] == _record()
    source.return_value = _payload(fim="2029-07-30")
    await _sync(client)
    history = (await client.get(f"{BASE}/{row['id']}/historico")).json()
    assert len(history) == 2
    assert history[0]["evento"] == "atualizacao"
    assert history[0]["depois"]["fim"] == "2029-07-30"
    assert history[1]["evento"] == "vinculacao"


async def test_view_edit_and_delete_permissions_are_enforced(client, make_user, auth_as, source):
    auth_as(await make_user(role=UserRole.ADMIN))
    row = await _sync(client)
    source.reset_mock()
    auth_as(await make_user(permissions={"financeiro_suprimentos": {"view": True}}))
    for path in (BASE, f"{BASE}/anatel/status", f"{BASE}/{row['id']}/historico"):
        assert (await client.get(path)).status_code == 200
    assert (await client.post(f"{BASE}/anatel/sincronizar")).status_code == 403
    assert (await client.patch(f"{BASE}/{row['id']}", json={"produto": "Novo"})).status_code == 403
    assert (await client.delete(f"{BASE}/{row['id']}")).status_code == 403
    source.assert_not_awaited()
    auth_as(await make_user())
    for path in (BASE, f"{BASE}/anatel/status", f"{BASE}/{row['id']}/historico"):
        assert (await client.get(path)).status_code == 403
    assert (await client.post(f"{BASE}/anatel/sincronizar")).status_code == 403
    source.assert_not_awaited()
    auth_as(await make_user(permissions={"financeiro_suprimentos": {"view": True, "edit": True}}))
    assert (await client.post(f"{BASE}/anatel/sincronizar")).status_code == 200
    source.assert_awaited_once()
    assert (await client.delete(f"{BASE}/{row['id']}")).status_code == 403


@pytest.mark.parametrize(("field", "value"), [
    ("modelo", "Outro"), ("nome_comercial", "Outro"), ("certificado", "inmetro"),
    ("numero", "123"), ("inicio", "2026-01-01"), ("fim", None),
])
async def test_linked_official_fields_cannot_be_overwritten(
    client, make_user, auth_as, source, field, value,
):
    auth_as(await make_user(role=UserRole.ADMIN))
    row = await _sync(client)
    response = await client.patch(f"{BASE}/{row['id']}", json={field: value, "produto": "Outro"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "certificacao_automatica"
    unchanged = (await client.get(BASE)).json()[0]
    assert unchanged[field] == row[field]
    assert unchanged["produto"] == row["produto"]


async def test_linked_product_price_and_pdf_stay_editable_but_delete_is_blocked(
    client, make_user, auth_as, source,
):
    auth_as(await make_user(role=UserRole.ADMIN))
    row = await _sync(client)
    path = f"{BASE}/{row['id']}"
    response = await client.patch(path, json={"produto": "smartphone 4G", "valor": 53000.50})
    assert response.status_code == 200
    assert response.json()["produto"] == "smartphone 4G"
    assert Decimal(response.json()["valor"]) == Decimal("53000.50")
    pdf = _pdf()
    uploaded = await client.post(f"{path}/pdf", files={"file": ("certificado.pdf", pdf)})
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["anatel_numero"] == row["anatel_numero"]
    assert uploaded.json()["tem_pdf"] is True
    assert (await client.get(f"{path}/pdf")).content == pdf
    await _sync(client)
    assert (await client.get(f"{path}/pdf")).content == pdf
    listed = (await client.get(BASE)).json()[0]
    assert listed["produto"] == "smartphone 4G"
    assert Decimal(listed["valor"]) == Decimal("53000.50")
    assert (await client.delete(path)).status_code == 409
    assert len((await client.get(BASE)).json()) == 1


async def test_manual_fields_remain_editable_and_history_missing_row_is_404(
    client, make_user, auth_as,
):
    auth_as(await make_user(role=UserRole.ADMIN))
    created = await client.post(BASE, json={"produto": "Manual", "certificado": "inmetro"})
    row_id = created.json()["id"]
    assert (await client.patch(f"{BASE}/{row_id}", json={"modelo": "M1"})).status_code == 200
    assert (await client.get(f"{BASE}/{row_id}/historico")).json() == []
    assert (await client.delete(f"{BASE}/{row_id}")).status_code == 204
    response = await client.get(f"{BASE}/{uuid4()}/historico")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "suprimentos_not_found"


async def test_failed_refresh_is_reported_without_losing_last_success(
    client, make_user, auth_as, source,
):
    auth_as(await make_user(role=UserRole.ADMIN))
    row = await _sync(client)
    before = (await client.get(f"{BASE}/anatel/status")).json()
    source.side_effect = TimeoutError("source unavailable")
    response = await client.post(f"{BASE}/anatel/sincronizar")
    assert response.status_code == 200
    assert response.json()["status"] == "error"
    after = (await client.get(f"{BASE}/anatel/status")).json()
    assert after["ultimo_sucesso_em"] == before["ultimo_sucesso_em"]
    assert after["erro"] == response.json()["erro"]
    assert (await client.get(BASE)).json()[0] == row


async def _wait_for_blocked_request(blocker_pid):
    async with app_db.SessionLocal() as observer:
        while True:
            blocked = await observer.scalar(text(
                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                "WHERE :blocker = ANY(pg_blocking_pids(pid)))"
            ), {"blocker": blocker_pid})
            # Renova o snapshot de pg_stat_activity para conexões recém-abertas.
            await observer.rollback()
            if blocked:
                return
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("method", ["patch", "delete"])
async def test_waiting_mutation_rechecks_link_after_concurrent_sync_commits(
    client, make_user, auth_as, db, method,
):
    auth_as(await make_user(role=UserRole.ADMIN))
    created = await client.post(BASE, json={"certificado": "anatel", "numero": "07197-26-18234"})
    row_id = UUID(created.json()["id"])
    row = await db.scalar(select(FinanceiroSuprimentos).where(
        FinanceiroSuprimentos.id == row_id,
    ).with_for_update())
    blocker_pid = await db.scalar(text("SELECT pg_backend_pid()"))
    # Mesmo lock e vínculo que a sincronização mantém até publicar o lote.
    row.anatel_numero = "071972618234"
    row.anatel_dados = _record()
    row.anatel_encontrado = True
    row.anatel_consultado_em = datetime.now(UTC)
    await db.flush()
    if method == "patch":
        request = asyncio.create_task(client.patch(f"{BASE}/{row_id}", json={"modelo": "Outro"}))
    else:
        request = asyncio.create_task(client.delete(f"{BASE}/{row_id}"))
    try:
        await asyncio.wait_for(_wait_for_blocked_request(blocker_pid), timeout=5)
        assert not request.done()
        await db.commit()
        response = await asyncio.wait_for(request, timeout=5)
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "certificacao_automatica"
        listed = (await client.get(BASE)).json()
        assert len(listed) == 1
        assert listed[0]["id"] == str(row_id)
        assert listed[0]["anatel_numero"] == "071972618234"
        assert listed[0]["modelo"] is None
    finally:
        if not request.done():
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)
        await db.rollback()
