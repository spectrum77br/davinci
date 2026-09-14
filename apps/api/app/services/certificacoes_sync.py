"""Atualização diária da Anatel sem perder cadastros, anexos ou a última base válida."""

import json
import re
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.financeiro import (
    CertificacoesSyncHistorico,
    CertificacoesSyncState,
    FinanceiroSuprimentos,
)

STATE_ID = "anatel_makisa"
MAKISA_CNPJ = "40191104000145"
# Lock transacional, compartilhado pelo worker e pelo botão Atualizar agora.
ADVISORY_LOCK_KEY = 4_014_110_400_014_5
INTERVALO = timedelta(days=1)
INTERVALO_ERRO = timedelta(hours=1)
ERRO_CONSULTA = (
    "Não foi possível atualizar a base da Anatel. "
    "Os dados anteriores foram preservados; haverá uma nova tentativa em uma hora."
)
logger = structlog.get_logger(__name__)


async def fetch_certificacoes() -> dict:
    """Importação tardia mantém o cliente substituível nos testes do sincronizador."""
    from app.services.anatel_certificacoes import fetch_certificacoes as fetch

    return await fetch()


def normalizar_numero(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def _formatar_numero(numero: str) -> str:
    return f"{numero[:5]}-{numero[5:7]}-{numero[7:]}"


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
    """Valida o lote inteiro antes de tocar em qualquer certificação."""
    if not isinstance(payload, dict):
        raise ValueError("invalid_source_payload")
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("empty_source_records")
    sha256 = payload.get("sha256")
    if not isinstance(sha256, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", sha256):
        raise ValueError("invalid_source_checksum")
    numbers = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("invalid_source_record")
        numero = record.get("numero")
        if not isinstance(numero, str) or not re.fullmatch(r"[0-9]{12}", numero):
            raise ValueError("invalid_source_number")
        if numero in numbers:
            raise ValueError("duplicate_source_number")
        numbers.add(numero)
        if normalizar_numero(record.get("cnpj")) != MAKISA_CNPJ:
            raise ValueError("unexpected_source_company")
        for field in ("modelos", "nomes_comerciais"):
            if not isinstance(record.get(field), list) or any(
                not isinstance(item, str) for item in record[field]
            ):
                raise ValueError("invalid_source_text_list")
        if not isinstance(record.get("produto"), str) or not record["produto"].strip():
            raise ValueError("invalid_source_product")
        _date(record.get("inicio"))
        _date(record.get("fim"))
        # Não aceitar objetos não serializáveis/NaN antes de iniciar os writes JSONB.
        json.dumps(record, allow_nan=False)
    return records, _datetime(payload.get("source_updated_at"))


def _snapshot(row: FinanceiroSuprimentos) -> dict:
    result = {}
    for field in (
        "produto", "modelo", "nome_comercial", "certificado", "numero", "valor",
        "inicio", "fim", "pdf_nome", "anatel_numero", "anatel_dados", "anatel_encontrado",
    ):
        value = getattr(row, field)
        if isinstance(value, date):
            value = value.isoformat()
        elif isinstance(value, Decimal):
            value = str(value)
        result[field] = value
    return result


def _history(
    session: AsyncSession, row: FinanceiroSuprimentos, before: dict | None,
    evento: str, now: datetime,
) -> bool:
    after = _snapshot(row)
    if before == after:
        return False
    session.add(CertificacoesSyncHistorico(
        suprimento_id=row.id,
        anatel_numero=row.anatel_numero,
        ocorrido_em=now,
        evento=evento,
        antes=before,
        depois=after,
    ))
    return True


def _contadores(status: str) -> dict:
    return {"status": status, "criados": 0, "atualizados": 0, "nao_localizados": 0, "conflitos": 0}


async def _aplicar(session: AsyncSession, records: list[dict], now: datetime) -> dict:
    result = _contadores("ok")
    rows = (await session.scalars(
        select(FinanceiroSuprimentos).order_by(FinanceiroSuprimentos.id).with_for_update()
    )).all()
    linked = {row.anatel_numero: row for row in rows if row.anatel_numero}
    manual = defaultdict(list)
    for row in rows:
        if not row.anatel_numero and (row.certificado or "").strip().lower() == "anatel":
            manual[normalizar_numero(row.numero)].append(row)

    seen = set()
    for record in records:
        numero = record["numero"]
        seen.add(numero)
        row = linked.get(numero)
        is_new = False
        evento = "atualizacao"
        if row is None:
            candidates = manual.get(numero, [])
            if len(candidates) > 1:
                # A base não permite decidir a qual cadastro manual pertence o certificado.
                result["conflitos"] += 1
                continue
            row = candidates[0] if candidates else FinanceiroSuprimentos()
            is_new = not candidates
            evento = "criacao" if is_new else "vinculacao"
        elif row.anatel_encontrado is False:
            evento = "reencontrado"
        before = None if is_new else _snapshot(row)
        if not (row.produto or "").strip():
            row.produto = record["produto"]
        row.modelo = ", ".join(record["modelos"]) or None
        row.nome_comercial = ", ".join(record["nomes_comerciais"]) or None
        row.certificado = "anatel"
        row.numero = _formatar_numero(numero)
        row.inicio = _date(record.get("inicio"))
        row.fim = _date(record.get("fim"))
        row.anatel_numero = numero
        row.anatel_dados = record
        row.anatel_encontrado = True
        row.anatel_consultado_em = now
        if is_new:
            session.add(row)
            await session.flush()
            result["criados"] += 1
        changed = _history(session, row, before, evento, now)
        if changed and not is_new:
            result["atualizados"] += 1

    for numero, row in linked.items():
        if numero in seen:
            continue
        before = _snapshot(row)
        row.anatel_encontrado = False
        row.anatel_consultado_em = now
        _history(session, row, before, "nao_localizado", now)
        result["nao_localizados"] += 1
    return result


async def sincronizar_certificacoes(session: AsyncSession, *, force: bool = False) -> dict:
    """Use uma sessão dedicada; o commit libera o lock e publica o lote atomicamente.

    Falhas de download, parsing ou aplicação preservam os registros e a última
    consulta válida. O savepoint permite registrar a falha sem soltar o lock antes.
    """
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
        logger.exception("certificacoes_anatel_sync_failed")
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
