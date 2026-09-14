"""Sincronização independente do ProdCert, preservando cada produto/modelo local."""

import json
import re
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import structlog
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.financeiro import (
    CertificacoesSyncHistorico,
    CertificacoesSyncState,
    FinanceiroSuprimentos,
)

STATE_ID = "inmetro_makisa"
MAKISA_CNPJ = "40191104000145"
ADVISORY_LOCK_KEY = 40_191_104_000_146
INTERVALO = timedelta(days=1)
INTERVALO_ERRO = timedelta(hours=1)
ERRO_CONSULTA = (
    "Não foi possível atualizar a base do Inmetro. "
    "Os dados anteriores foram preservados; haverá uma nova tentativa em uma hora."
)
_CAMPOS_IDENTIDADE = {"modelo", "certificado", "numero"}
_CAMPOS_DATAS = {"inicio", "fim"}
logger = structlog.get_logger(__name__)


async def fetch_certificacoes() -> dict:
    from app.services.inmetro_certificacoes import fetch_certificacoes as fetch

    return await fetch()


def normalizar_identificador(value: str | None) -> str:
    """Ignora apenas caixa e espaços, mantendo prefixos, hífens e barras."""
    return re.sub(r"\s+", "", value or "").upper()


def campos_oficiais_inmetro(row: FinanceiroSuprimentos) -> set[str]:
    """Campos protegidos no PATCH; datas só são oficiais quando a fonte as informa."""
    if not row.inmetro_chave:
        return set()
    dados = row.inmetro_dados or {}
    confirmados = set(dados.get("campos_confirmados") or [])
    return _CAMPOS_IDENTIDADE | {
        field for field in _CAMPOS_DATAS if field in confirmados and dados.get(field)
    }


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _validar_fonte(payload: dict) -> tuple[list[dict], datetime | None]:
    if not isinstance(payload, dict):
        raise ValueError("invalid_source_payload")
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("empty_source_records")
    checksum = payload.get("sha256")
    if not isinstance(checksum, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", checksum):
        raise ValueError("invalid_source_checksum")
    keys = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("invalid_source_record")
        for field in ("chave", "numero", "modelo", "certificador", "produto"):
            if not isinstance(record.get(field), str) or not record[field].strip():
                raise ValueError("invalid_source_identity")
        if record["chave"] in keys:
            raise ValueError("duplicate_source_key")
        keys.add(record["chave"])
        if re.sub(r"\D", "", record.get("cnpj", "")) != MAKISA_CNPJ:
            raise ValueError("unexpected_source_company")
        confirmed = record.get("campos_confirmados")
        if not isinstance(confirmed, list) or any(not isinstance(v, str) for v in confirmed):
            raise ValueError("invalid_source_fields")
        if not _CAMPOS_IDENTIDADE <= set(confirmed) <= _CAMPOS_IDENTIDADE | _CAMPOS_DATAS:
            raise ValueError("invalid_source_fields")
        if not isinstance(record.get("dados_origem"), dict):
            raise ValueError("invalid_source_evidence")
        for field in _CAMPOS_DATAS:
            _date(record.get(field))
        json.dumps(record, allow_nan=False)
    return records, _datetime(payload.get("source_updated_at"))


def _modelos_manuais(row: FinanceiroSuprimentos) -> set[str]:
    base = normalizar_identificador(row.modelo)
    models = {base} if base else set()
    if not re.fullmatch(r"[A-Z0-9]+", base):
        return models
    # M1 é um nome comercial já informado pelo usuário, não um sufixo descartável.
    aliases = re.split(r"[,;\s]+", (row.nome_comercial or "").upper().strip())
    for alias in aliases:
        if re.fullmatch(r"M[0-9]+", alias):
            models.add(f"{base}-{alias}")
    return models


def _candidato(row: FinanceiroSuprimentos, record: dict) -> bool:
    number = normalizar_identificador(row.numero)
    return (
        (not number or number == normalizar_identificador(record["numero"]))
        and normalizar_identificador(record["modelo"]) in _modelos_manuais(row)
    )


def _snapshot(row: FinanceiroSuprimentos) -> dict:
    result = {}
    for field in (
        "produto", "modelo", "nome_comercial", "certificado", "numero", "valor",
        "inicio", "fim", "pdf_nome", "anatel_numero", "anatel_dados", "anatel_encontrado",
        "inmetro_chave", "inmetro_dados", "inmetro_encontrado",
    ):
        value = getattr(row, field)
        if isinstance(value, date):
            value = value.isoformat()
        elif isinstance(value, Decimal):
            value = str(value)
        result[field] = value
    return result


def _history(session, row, before, evento, now) -> bool:
    after = _snapshot(row)
    if after == before:
        return False
    session.add(CertificacoesSyncHistorico(
        suprimento_id=row.id, fonte="inmetro", chave_fonte=row.inmetro_chave,
        anatel_numero=None, ocorrido_em=now, evento=evento, antes=before, depois=after,
    ))
    return True


def _contadores(status: str) -> dict:
    return {"status": status, "criados": 0, "atualizados": 0, "nao_localizados": 0, "conflitos": 0}


async def _aplicar(session: AsyncSession, records: list[dict], now: datetime) -> dict:
    result = _contadores("ok")
    rows = (await session.scalars(select(FinanceiroSuprimentos).where(or_(
        FinanceiroSuprimentos.inmetro_chave.is_not(None),
        func.lower(func.trim(FinanceiroSuprimentos.certificado)) == "inmetro",
    )).order_by(FinanceiroSuprimentos.id).with_for_update())).all()
    linked = {row.inmetro_chave: row for row in rows if row.inmetro_chave}
    manual = [row for row in rows if not row.inmetro_chave and not row.anatel_numero]
    candidates = {}
    manual_matches = defaultdict(list)
    for record in records:
        matches = [row for row in manual if _candidato(row, record)]
        candidates[record["chave"]] = matches
        for row in matches:
            manual_matches[row.id].append(record["chave"])

    seen = set()
    for record in records:
        key = record["chave"]
        seen.add(key)
        row = linked.get(key)
        is_new = False
        evento = "atualizacao"
        if row is None:
            matches = candidates[key]
            if len(matches) > 1 or any(len(manual_matches[row.id]) > 1 for row in matches):
                result["conflitos"] += 1
                continue
            row = matches[0] if matches else FinanceiroSuprimentos()
            is_new = not matches
            evento = "criacao" if is_new else "vinculacao"
        elif row.inmetro_encontrado is False:
            evento = "reencontrado"
        before = None if is_new else _snapshot(row)
        if not (row.produto or "").strip():
            row.produto = record["produto"]
        for field in record["campos_confirmados"]:
            if field in _CAMPOS_DATAS:
                if record.get(field):
                    setattr(row, field, _date(record[field]))
            else:
                setattr(row, field, "inmetro" if field == "certificado" else record[field])
        row.inmetro_chave = key
        row.inmetro_dados = record
        row.inmetro_consultado_em = now
        row.inmetro_encontrado = True
        if is_new:
            session.add(row)
            await session.flush()
            result["criados"] += 1
        changed = _history(session, row, before, evento, now)
        if changed and not is_new:
            result["atualizados"] += 1

    for key, row in linked.items():
        if key in seen:
            continue
        before = _snapshot(row)
        row.inmetro_encontrado = False
        row.inmetro_consultado_em = now
        _history(session, row, before, "nao_localizado", now)
        result["nao_localizados"] += 1
    return result


async def sincronizar_certificacoes(session: AsyncSession, *, force: bool = False) -> dict:
    """Sessão dedicada; somente este serviço publica o lote e libera seu lock."""
    locked = await session.scalar(
        text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": ADVISORY_LOCK_KEY},
    )
    if not locked:
        await session.commit()
        return _contadores("busy")
    now = datetime.now(UTC)
    state = await session.get(CertificacoesSyncState, STATE_ID)
    if state and not force and state.proxima_tentativa_em and state.proxima_tentativa_em > now:
        await session.commit()
        return _contadores("skipped")
    if state is None:
        state = CertificacoesSyncState(id=STATE_ID)
        session.add(state)
    state.ultima_tentativa_em = now
    await session.flush()
    try:
        payload = await fetch_certificacoes()
        records, source_updated_at = _validar_fonte(payload)
        if (source_updated_at and state.source_updated_at
                and source_updated_at < state.source_updated_at):
            raise ValueError("source_older_than_last_success")
        async with session.begin_nested():
            result = await _aplicar(session, records, now)
            await session.flush()
    except Exception:
        logger.exception("certificacoes_inmetro_sync_failed")
        state.erro = ERRO_CONSULTA
        state.proxima_tentativa_em = now + INTERVALO_ERRO
        result = {**_contadores("error"), "erro": ERRO_CONSULTA}
    else:
        state.ultimo_sucesso_em = now
        state.proxima_tentativa_em = now + INTERVALO
        state.source_updated_at = source_updated_at
        state.source_sha256 = payload["sha256"]
        state.erro = None
        state.resumo = result
    await session.commit()
    return result
