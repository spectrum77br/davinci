"""ProdCert: modelos distintos, correspondência comprovada e atualização independente."""

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text

import app.db as app_db
from app.models.financeiro import (
    CertificacoesSyncHistorico,
    CertificacoesSyncState,
    FinanceiroSuprimentos,
)
from app.services import certificacoes_inmetro_sync as sync
from app.services.certificacoes_sync import ADVISORY_LOCK_KEY as ANATEL_LOCK


def _record(modelo="UAF001-M1", numero="MODERNA-0069/25", **fields):
    certifier = fields.pop("certificador", "OCP-EXEMPLO")
    return {
        "chave": f"{certifier}|{numero}|{modelo}", "certificador": certifier,
        "numero": numero, "modelo": modelo, "cnpj": "40191104000145",
        "nome_empresa": "MAKISA TRADING LTDA", "marca": "URANYX",
        "descricao": "Produto elétrico", "produto": "Produto elétrico",
        "inicio": "2026-08-03", "fim": "2032-08-03", "situacao_certificado": "Ativo",
        "dados_origem": {"Modelo": modelo, "Número do certificado": numero}, "alertas": [],
        "campos_confirmados": ["modelo", "certificado", "numero", "inicio", "fim"],
        **fields,
    }


def _payload(*records, **fields):
    return {
        "source_url": "https://certifiq.inmetro.gov.br/Consulta/ConsultaEmpresas",
        "source_updated_at": None, "sha256": "c" * 64,
        "records": list(records) or [_record()], **fields,
    }


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
    monkeypatch.setattr(sync, "fetch_certificacoes", mocked)
    return mocked


async def _rows(db):
    return (await db.scalars(
        select(FinanceiroSuprimentos).order_by(FinanceiroSuprimentos.modelo)
    )).all()


async def _history(db):
    return (await db.scalars(select(CertificacoesSyncHistorico).order_by(
        CertificacoesSyncHistorico.ocorrido_em,
    ))).all()


async def test_five_verified_models_link_without_merging_repeated_certificate(db, source):
    identities = [
        ("UAF001", "MODERNA-0069/25"), ("UAF002", "MODERNA-0069/25"),
        ("UCM001", "MODERNA-0070/25"), ("USL001", "MODERNA-0071/25"),
        ("UCO001", "MODERNA-0072/26"),
    ]
    original = []
    for index, (model, number) in enumerate(identities):
        row = FinanceiroSuprimentos(
            produto=f"Nome amigável {index}", modelo=model, nome_comercial="M1, M2, M3",
            certificado="inmetro", numero=number if index < 3 else None,
            valor=Decimal("100.00"), inicio=date(2026, 8, 3), fim=date(2027, 8, 3),
            pdf_nome=f"certificado{index}.pdf", pdf_arquivo=f"PDF {index}".encode(),
        )
        db.add(row)
        original.append(row)
    cookware = FinanceiroSuprimentos(
        modelo="UCW001", nome_comercial="M1, M2", certificado="inmetro",
    )
    anatel = FinanceiroSuprimentos(
        modelo="UAF001", nome_comercial="M1", certificado="anatel",
        anatel_numero="071972618234", anatel_dados={"modelo": "UAF001"},
    )
    db.add_all([cookware, anatel])
    await db.commit()
    ids = {row.id for row in original}
    source.return_value = _payload(*[
        _record(f"{model}-M1", number) for model, number in identities
    ])
    result = await sync.sincronizar_certificacoes(db)
    assert result == {"status": "ok", "criados": 0, "atualizados": 5,
                      "nao_localizados": 0, "conflitos": 0}
    rows = await _rows(db)
    assert len(rows) == 7
    linked = [row for row in rows if row.inmetro_chave]
    assert {row.id for row in linked} == ids
    assert len({row.inmetro_chave for row in linked}) == 5
    assert sum(row.numero == "MODERNA-0069/25" for row in linked) == 2
    for index, row in enumerate(original):
        assert row.produto == f"Nome amigável {index}"
        assert row.valor == Decimal("100.00")
        assert row.nome_comercial == "M1, M2, M3"
        assert row.modelo == identities[index][0] + "-M1"
        assert row.fim == date(2032, 8, 3)
        assert row.pdf_nome == f"certificado{index}.pdf"
        assert row.pdf_arquivo == f"PDF {index}".encode()
    assert cookware.inmetro_chave is None
    assert anatel.inmetro_chave is None
    assert anatel.anatel_numero == "071972618234"
    history = await _history(db)
    assert len(history) == 5
    assert all(item.fonte == "inmetro" and item.anatel_numero is None for item in history)
    assert all(item.evento == "vinculacao" for item in history)
    first = next(item for item in history if item.suprimento_id == original[0].id)
    assert first.antes["modelo"] == "UAF001"
    assert first.antes["fim"] == "2027-08-03"
    assert first.antes["pdf_nome"] == "certificado0.pdf"
    assert first.chave_fonte == first.depois["inmetro_chave"]
    assert first.depois["inmetro_dados"] == _record()


@pytest.mark.parametrize(("model", "commercial", "number", "matches"), [
    ("UAF001-M1", None, "MODERNA-0069/25", True),
    (" uaf001-m1 ", None, " moderna-0069/25 ", True),
    ("UAF001", "M1, M2", "MODERNA-0069/25", True),
    ("UAF001", "M1, M2", None, True),
    ("UAF001", "M2, M3", "MODERNA-0069/25", False),
    ("UAF001", None, "MODERNA-0069/25", False),
    ("UAF001", "AM1, M10", "MODERNA-0069/25", False),
    ("UAF001-X", "M1", "MODERNA-0069/25", False),
    ("UAF001", "M1", "OUTRO-0069/25", False),
    ("UAF001", "M1", "0069/25", False),
    ("UAF002", "M1", "MODERNA-0069/25", False),
])
def test_only_exact_model_or_explicit_alias_can_match(model, commercial, number, matches):
    row = FinanceiroSuprimentos(modelo=model, nome_comercial=commercial, numero=number)
    assert sync._candidato(row, _record()) is matches


async def test_unmatched_official_model_creates_one_separate_row_and_is_idempotent(db, source):
    manual = FinanceiroSuprimentos(
        modelo="UAF001", certificado="inmetro", numero="MODERNA-0069/25",
    )
    db.add(manual)
    await db.commit()
    result = await sync.sincronizar_certificacoes(db)
    assert result["criados"] == 1
    assert manual.inmetro_chave is None  # Nunca supor M1 quando não foi informado.
    assert len(await _rows(db)) == 2
    assert len(await _history(db)) == 1
    result = await sync.sincronizar_certificacoes(db, force=True)
    assert result["criados"] == result["atualizados"] == 0
    assert len(await _history(db)) == 1


async def test_several_manual_candidates_are_conflict_without_new_duplicate(db, source):
    db.add_all([
        FinanceiroSuprimentos(modelo="UAF001-M1", certificado="inmetro"),
        FinanceiroSuprimentos(modelo="UAF001", nome_comercial="M1", certificado="inmetro"),
    ])
    await db.commit()
    result = await sync.sincronizar_certificacoes(db)
    assert result["conflitos"] == 1
    assert result["criados"] == result["atualizados"] == 0
    assert len(await _rows(db)) == 2
    assert await _history(db) == []


async def test_one_manual_row_matching_several_official_models_is_conflict(db, source):
    row = FinanceiroSuprimentos(
        modelo="UAF001", nome_comercial="M1, M2", certificado="inmetro",
    )
    db.add(row)
    await db.commit()
    source.return_value = _payload(_record(), _record("UAF001-M2"))
    result = await sync.sincronizar_certificacoes(db)
    assert result["conflitos"] == 2
    assert result["criados"] == result["atualizados"] == 0
    assert len(await _rows(db)) == 1
    assert row.inmetro_chave is None


async def test_ambiguity_includes_official_records_already_linked_elsewhere(db, source):
    await sync.sincronizar_certificacoes(db)
    manual = FinanceiroSuprimentos(
        modelo="UAF001", nome_comercial="M1, M2", certificado="inmetro",
    )
    db.add(manual)
    await db.commit()
    source.return_value = _payload(_record(), _record("UAF001-M2"))
    result = await sync.sincronizar_certificacoes(db, force=True)
    assert result["conflitos"] == 1
    assert result["criados"] == result["atualizados"] == 0
    assert manual.inmetro_chave is None
    assert len(await _rows(db)) == 2


async def test_null_or_unconfirmed_dates_never_erase_manual_values_and_stay_editable(db, source):
    row = FinanceiroSuprimentos(
        modelo="UAF001-M1", certificado="inmetro", inicio=date(2025, 1, 1), fim=date(2027, 1, 1),
    )
    db.add(row)
    await db.commit()
    source.return_value = _payload(_record(inicio=None, fim=None))
    assert (await sync.sincronizar_certificacoes(db))["atualizados"] == 1
    assert row.inicio == date(2025, 1, 1)
    assert row.fim == date(2027, 1, 1)
    assert sync.campos_oficiais_inmetro(row) == {"modelo", "certificado", "numero"}
    source.return_value = _payload(_record(
        campos_confirmados=["modelo", "certificado", "numero"],
    ))
    await sync.sincronizar_certificacoes(db, force=True)
    assert row.fim == date(2027, 1, 1)
    source.return_value = _payload()
    await sync.sincronizar_certificacoes(db, force=True)
    assert row.fim == date(2032, 8, 3)
    assert sync.campos_oficiais_inmetro(row) == {"modelo", "certificado", "numero", "inicio", "fim"}


async def test_official_status_changes_absence_and_return_preserve_previous_data(db, source):
    source.return_value = _payload(_record(), _record("UAF002-M1"))
    await sync.sincronizar_certificacoes(db)
    missing = (await _rows(db))[0]
    source.return_value = _payload(_record("UAF002-M1", situacao_certificado="Suspenso"))
    result = await sync.sincronizar_certificacoes(db, force=True)
    assert result["nao_localizados"] == 1
    assert result["atualizados"] == 1
    assert missing.inmetro_encontrado is False
    assert missing.inmetro_dados == _record()
    assert missing.fim == date(2032, 8, 3)
    assert len(await _history(db)) == 4
    await sync.sincronizar_certificacoes(db, force=True)
    assert len(await _history(db)) == 4
    source.return_value = _payload(_record(), _record("UAF002-M1", situacao_certificado="Suspenso"))
    assert (await sync.sincronizar_certificacoes(db, force=True))["atualizados"] == 1
    assert missing.inmetro_encontrado is True
    assert (await _history(db))[-1].evento == "reencontrado"


@pytest.mark.parametrize("bad_payload", [
    _payload(records=[]), _payload(_record(), _record()), _payload(sha256="bad"),
    _payload(_record(cnpj="12345678000199")), _payload(_record(modelo="")),
    _payload(_record(certificador="")), _payload(_record(fim="invalid-date")),
    _payload(_record(campos_confirmados=["modelo", "numero", "certificado", "valor"])),
    _payload(_record(campos_confirmados=[])), _payload(_record(dados_origem=None)),
    _payload(source_updated_at="invalid-date"),
])
async def test_invalid_snapshot_preserves_every_row_and_last_success(db, source, bad_payload):
    await sync.sincronizar_certificacoes(db)
    row = (await _rows(db))[0]
    previous = deepcopy(sync._snapshot(row))
    consulted = row.inmetro_consultado_em
    state = await db.get(CertificacoesSyncState, sync.STATE_ID)
    success = state.ultimo_sucesso_em
    source.return_value = bad_payload
    result = await sync.sincronizar_certificacoes(db, force=True)
    assert result["status"] == "error"
    assert sync._snapshot(row) == previous
    assert row.inmetro_consultado_em == consulted
    assert state.ultimo_sucesso_em == success
    assert state.source_sha256 == "c" * 64
    assert state.erro == result["erro"]
    assert state.proxima_tentativa_em == state.ultima_tentativa_em + timedelta(hours=1)
    assert len(await _history(db)) == 1


async def test_savepoint_rolls_back_partial_batch_and_keeps_failure_state(db, source, monkeypatch):
    await sync.sincronizar_certificacoes(db)
    source.return_value = _payload(_record(fim="2033-08-03"), _record("UAF002-M1"))
    history = sync._history

    def fail_on_second(session, row, before, evento, now):
        if row.modelo == "UAF002-M1":
            raise RuntimeError("failure after first updated row")
        return history(session, row, before, evento, now)

    monkeypatch.setattr(sync, "_history", fail_on_second)
    assert (await sync.sincronizar_certificacoes(db, force=True))["status"] == "error"
    db.expire_all()
    rows = await _rows(db)
    assert len(rows) == 1
    assert rows[0].fim == date(2032, 8, 3)
    assert len(await _history(db)) == 1
    assert (await db.get(CertificacoesSyncState, sync.STATE_ID)).erro


async def test_daily_schedule_retry_and_state_do_not_interfere_with_anatel(db, source):
    anatel_state = CertificacoesSyncState(
        id="anatel_makisa", ultimo_sucesso_em=datetime(2026, 9, 14, tzinfo=UTC),
        erro="Erro da outra fonte", source_sha256="a" * 64,
    )
    db.add(anatel_state)
    await db.commit()
    source.side_effect = TimeoutError("source unavailable")
    assert (await sync.sincronizar_certificacoes(db))["status"] == "error"
    state = await db.get(CertificacoesSyncState, sync.STATE_ID)
    assert state.ultimo_sucesso_em is None
    assert state.proxima_tentativa_em == state.ultima_tentativa_em + timedelta(hours=1)
    assert (await sync.sincronizar_certificacoes(db))["status"] == "skipped"
    source.side_effect = None
    state.proxima_tentativa_em = datetime.now(UTC) - timedelta(seconds=1)
    await db.commit()
    assert (await sync.sincronizar_certificacoes(db))["status"] == "ok"
    assert state.erro is None
    assert state.proxima_tentativa_em == state.ultimo_sucesso_em + timedelta(days=1)
    assert (await sync.sincronizar_certificacoes(db))["status"] == "skipped"
    assert (await sync.sincronizar_certificacoes(db, force=True))["status"] == "ok"
    assert source.await_count == 3
    assert anatel_state.erro == "Erro da outra fonte"
    assert anatel_state.source_sha256 == "a" * 64


async def test_lock_blocks_same_source_but_not_anatel_and_history_default_is_compatible(db, source):
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": sync.ADVISORY_LOCK_KEY})
    async with app_db.SessionLocal() as other:
        assert (await sync.sincronizar_certificacoes(other, force=True))["status"] == "busy"
    source.assert_not_awaited()
    await db.commit()
    assert ANATEL_LOCK != sync.ADVISORY_LOCK_KEY
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": ANATEL_LOCK})
    async with app_db.SessionLocal() as other:
        assert (await sync.sincronizar_certificacoes(other, force=True))["status"] == "ok"
    await db.commit()
    previous_style = CertificacoesSyncHistorico(
        anatel_numero="071972618234", evento="criacao", depois={"original": True},
    )
    db.add(previous_style)
    await db.commit()
    assert previous_style.fonte == "anatel"
    assert previous_style.chave_fonte is None
