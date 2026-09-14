"""Contratos de atualização Anatel, preservação dos dados e agendamento persistido."""

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, text

import app.db as app_db
from app.models.financeiro import (
    CertificacoesSyncHistorico,
    CertificacoesSyncState,
    FinanceiroSuprimentos,
)
from app.services import certificacoes_sync as sync


def _record(numero="071972618234", **fields):
    return {
        "numero": numero,
        "cnpj": "40191104000145",
        "nome_empresa": "MAKISA TRADING LTDA",
        "produto": "Telefone móvel celular",
        "modelos": ["USM003"],
        "nomes_comerciais": ["Fossibot F112"],
        "tipos_produto": ["Telefone móvel celular"],
        "fabricantes": ["Fabricante oficial"],
        "certificados": ["CERT-EXEMPLO"],
        "inicio": "2026-07-30",
        "fim": "2028-07-30",
        "situacao_certificado": "Ativo",
        "situacao_requerimento": "Homologação Emitida",
        "dados_origem": [{"Modelo": "USM003"}],
        "alertas": [],
        **fields,
    }


def _payload(*records, **fields):
    return {
        "source_url": "https://www.anatel.gov.br/dadosabertos/base.zip",
        "source_updated_at": "2026-09-14T10:00:00+00:00",
        "sha256": "a" * 64,
        "records": list(records) or [_record()],
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
    mocked = AsyncMock(return_value=_payload())
    monkeypatch.setattr(sync, "fetch_certificacoes", mocked)
    return mocked


async def _rows(db):
    return (await db.scalars(
        select(FinanceiroSuprimentos).order_by(FinanceiroSuprimentos.numero)
    )).all()


async def _history(db):
    return (await db.scalars(
        select(CertificacoesSyncHistorico).order_by(CertificacoesSyncHistorico.ocorrido_em)
    )).all()


async def test_exact_match_preserves_manual_label_price_pdf_and_snapshots(db, source):
    original = FinanceiroSuprimentos(
        certificado="anatel", numero="07197-26-18234", produto="smartphone 4G",
        modelo="Modelo digitado", nome_comercial="Nome digitado", valor=Decimal("53000.00"),
        inicio=date(2026, 7, 1), fim=date(2028, 7, 1),
        pdf_nome="certificado.pdf", pdf_arquivo=b"PDF ORIGINAL",
    )
    # Modelo igual nunca basta para vincular um cadastro manual de outro certificado.
    no_number = FinanceiroSuprimentos(certificado="anatel", produto="Manual", modelo="USM003")
    inmetro = FinanceiroSuprimentos(certificado="inmetro", numero="071972618234")
    db.add_all([original, no_number, inmetro])
    await db.commit()
    original_id = original.id
    source.return_value = _payload(_record(), _record("082162518234", modelos=["USM002"]))

    result = await sync.sincronizar_certificacoes(db)
    assert result == {"status": "ok", "criados": 1, "atualizados": 1,
                      "nao_localizados": 0, "conflitos": 0}
    rows = await _rows(db)
    assert len(rows) == 4
    matched = next(row for row in rows if row.id == original_id)
    assert matched.produto == "smartphone 4G"
    assert matched.valor == Decimal("53000.00")
    assert matched.modelo == "USM003"
    assert matched.nome_comercial == "Fossibot F112"
    assert matched.inicio == date(2026, 7, 30)
    assert matched.fim == date(2028, 7, 30)
    assert matched.anatel_encontrado is True
    assert matched.pdf_nome == "certificado.pdf"
    attachment = await db.scalar(select(FinanceiroSuprimentos.pdf_arquivo).where(
        FinanceiroSuprimentos.id == original_id,
    ))
    assert attachment == b"PDF ORIGINAL"
    assert no_number.anatel_numero is None
    assert inmetro.anatel_numero is None
    history = next(item for item in await _history(db) if item.suprimento_id == original_id)
    assert history.evento == "vinculacao"
    assert history.antes["modelo"] == "Modelo digitado"
    assert history.antes["fim"] == "2028-07-01"
    assert history.antes["valor"] == "53000.00"
    assert history.antes["pdf_nome"] == "certificado.pdf"
    assert "pdf_arquivo" not in history.antes
    assert history.depois["anatel_dados"] == _record()


async def test_repeated_forced_consultation_is_idempotent(db, source):
    assert (await sync.sincronizar_certificacoes(db))["criados"] == 1
    row = (await _rows(db))[0]
    first_consulted = row.anatel_consultado_em
    result = await sync.sincronizar_certificacoes(db, force=True)
    assert result["atualizados"] == result["criados"] == 0
    assert len(await _rows(db)) == 1
    assert len(await _history(db)) == 1
    assert row.anatel_consultado_em >= first_consulted
    assert source.await_count == 2


async def test_official_correction_updates_history_and_retains_manual_edits(db, source):
    await sync.sincronizar_certificacoes(db)
    row = (await _rows(db))[0]
    row.produto = "Meu nome do produto"
    row.valor = Decimal("120.00")
    await db.commit()
    source.return_value = _payload(_record(
        modelos=["USM003", "USM003B"], fim="2029-07-30",
        situacao_certificado="Suspenso",
        situacao_requerimento="Suspenso",
    ), sha256="b" * 64)
    assert (await sync.sincronizar_certificacoes(db, force=True))["atualizados"] == 1
    assert row.produto == "Meu nome do produto"
    assert row.valor == Decimal("120.00")
    assert row.modelo == "USM003, USM003B"
    assert row.fim == date(2029, 7, 30)
    history = await _history(db)
    assert len(history) == 2
    assert history[-1].antes["anatel_dados"]["situacao_certificado"] == "Ativo"
    assert history[-1].depois["anatel_dados"]["situacao_certificado"] == "Suspenso"


async def test_missing_record_preserves_data_and_can_return(db, source):
    first, second = _record(), _record("082162518234")
    source.return_value = _payload(first, second)
    await sync.sincronizar_certificacoes(db)
    missing = next(row for row in await _rows(db) if row.anatel_numero == first["numero"])
    source.return_value = _payload(second)
    result = await sync.sincronizar_certificacoes(db, force=True)
    assert result["nao_localizados"] == 1
    assert missing.anatel_encontrado is False
    assert missing.anatel_dados == first
    assert missing.fim == date(2028, 7, 30)
    assert len(await _rows(db)) == 2
    assert len(await _history(db)) == 3
    await sync.sincronizar_certificacoes(db, force=True)
    assert len(await _history(db)) == 3  # Ausência repetida não inventa alterações.
    source.return_value = _payload(first, second)
    assert (await sync.sincronizar_certificacoes(db, force=True))["atualizados"] == 1
    assert missing.anatel_encontrado is True
    assert (await _history(db))[-1].evento == "reencontrado"


@pytest.mark.parametrize("bad_payload", [
    {"records": []},
    _payload(records=[]),
    _payload(_record(), _record()),
    _payload(_record(cnpj="12345678000199")),
    _payload(_record(numero="INVALIDO")),
    _payload(_record(fim="invalid-date")),
    _payload(_record(modelos="USM003")),
    _payload(_record(produto="")),
    _payload(sha256="bad-checksum"),
    _payload(source_updated_at="invalid-date"),
    _payload(source_updated_at="2026-09-13T10:00:00Z"),
])
async def test_invalid_response_preserves_all_rows_and_last_success(db, source, bad_payload):
    await sync.sincronizar_certificacoes(db)
    row = (await _rows(db))[0]
    original = deepcopy(sync._snapshot(row))
    consulted = row.anatel_consultado_em
    state = await db.get(CertificacoesSyncState, sync.STATE_ID)
    last_success = state.ultimo_sucesso_em
    source.return_value = bad_payload
    result = await sync.sincronizar_certificacoes(db, force=True)
    assert result["status"] == "error"
    assert sync._snapshot(row) == original
    assert row.anatel_consultado_em == consulted
    assert state.ultimo_sucesso_em == last_success
    assert state.source_sha256 == "a" * 64
    assert state.erro == result["erro"]
    assert len(await _history(db)) == 1
    assert state.proxima_tentativa_em == state.ultima_tentativa_em + timedelta(hours=1)


async def test_failed_download_schedules_retry_even_before_first_success(db, source):
    source.side_effect = TimeoutError("official service unavailable")
    assert (await sync.sincronizar_certificacoes(db))["status"] == "error"
    state = await db.get(CertificacoesSyncState, sync.STATE_ID)
    assert state.ultimo_sucesso_em is None
    assert state.source_sha256 is None
    assert state.proxima_tentativa_em == state.ultima_tentativa_em + timedelta(hours=1)
    assert await _rows(db) == []


async def test_partial_write_failure_rolls_back_whole_batch_but_persists_retry(
    db, source, monkeypatch,
):
    await sync.sincronizar_certificacoes(db)
    first_id = (await _rows(db))[0].id
    source.return_value = _payload(_record(fim="2029-07-30"), _record("082162518234"))
    real_history = sync._history

    def fail_on_second(session, row, before, evento, now):
        if row.anatel_numero == "082162518234":
            raise RuntimeError("simulated failure after first updated row")
        return real_history(session, row, before, evento, now)

    monkeypatch.setattr(sync, "_history", fail_on_second)
    assert (await sync.sincronizar_certificacoes(db, force=True))["status"] == "error"
    db.expire_all()
    rows = await _rows(db)
    assert len(rows) == 1
    assert rows[0].id == first_id
    assert rows[0].fim == date(2028, 7, 30)
    assert len(await _history(db)) == 1
    state = await db.get(CertificacoesSyncState, sync.STATE_ID)
    assert state.erro
    assert state.source_sha256 == "a" * 64


async def test_scheduler_daily_gate_manual_force_and_hourly_retry(db, source):
    await sync.sincronizar_certificacoes(db)
    state = await db.get(CertificacoesSyncState, sync.STATE_ID)
    assert state.proxima_tentativa_em == state.ultimo_sucesso_em + timedelta(days=1)
    assert (await sync.sincronizar_certificacoes(db))["status"] == "skipped"
    assert source.await_count == 1
    assert (await sync.sincronizar_certificacoes(db, force=True))["status"] == "ok"
    assert source.await_count == 2
    state.proxima_tentativa_em = datetime.now(UTC) - timedelta(seconds=1)
    await db.commit()
    source.side_effect = OSError("network unavailable")
    assert (await sync.sincronizar_certificacoes(db))["status"] == "error"
    assert (await sync.sincronizar_certificacoes(db))["status"] == "skipped"
    assert source.await_count == 3
    state.proxima_tentativa_em = datetime.now(UTC) - timedelta(seconds=1)
    await db.commit()
    source.side_effect = None
    assert (await sync.sincronizar_certificacoes(db))["status"] == "ok"
    assert state.erro is None
    assert state.proxima_tentativa_em == state.ultimo_sucesso_em + timedelta(days=1)


async def test_database_lock_excludes_overlapping_worker_and_manual_requests(db, source):
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": sync.ADVISORY_LOCK_KEY})
    async with app_db.SessionLocal() as other:
        assert (await sync.sincronizar_certificacoes(other, force=True))["status"] == "busy"
    source.assert_not_awaited()
    await db.commit()
    async with app_db.SessionLocal() as other:
        assert (await sync.sincronizar_certificacoes(other, force=True))["status"] == "ok"
    source.assert_awaited_once()


async def test_ambiguous_manual_number_never_chooses_or_duplicates_a_row(db, source):
    db.add_all([
        FinanceiroSuprimentos(certificado="anatel", numero="07197-26-18234", produto="A"),
        FinanceiroSuprimentos(certificado="ANATEL", numero="071972618234", produto="B"),
    ])
    await db.commit()
    result = await sync.sincronizar_certificacoes(db)
    assert result["conflitos"] == 1
    assert result["criados"] == result["atualizados"] == 0
    assert len(await _rows(db)) == 2
    assert all(row.anatel_numero is None for row in await _rows(db))
    assert await _history(db) == []


async def test_history_survives_deleting_its_row_reference(db, source):
    await sync.sincronizar_certificacoes(db)
    row = (await _rows(db))[0]
    await db.execute(delete(FinanceiroSuprimentos).where(FinanceiroSuprimentos.id == row.id))
    await db.commit()
    history = (await _history(db))[0]
    assert history.suprimento_id is None
    assert history.anatel_numero == "071972618234"
    assert history.depois["anatel_dados"] == _record()
    assert await db.scalar(select(func.count()).select_from(FinanceiroSuprimentos)) == 0
