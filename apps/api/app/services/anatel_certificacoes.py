"""Read Makisa certificates from Anatel's official, public CSV distribution.

One homologation can occupy many source rows (models, commercial names and
product types). Keep that evidence intact while exposing one stable record per
homologation. The source's certificate and application statuses are independent;
``IC_ATIVO`` is deliberately never translated into a regulatory status.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import re
import zipfile
import zlib
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

SOURCE_URL = (
    "https://www.anatel.gov.br/dadosabertos/paineis_de_dados/"
    "certificacao_de_produtos/produtos_certificados.zip"
)
MAKISA_CNPJ = "40191104000145"
CSV_FILENAME = "Produtos_Homologados_Anatel.csv"
MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
MAX_ZIP_MEMBERS = 16
DOWNLOAD_TIMEOUT_SECONDS = 45

HOMOLOGATION = "Número de Homologação"
CNPJ = "CNPJ do Solicitante"
COMPANY = "Nome do Solicitante"
START_DATE = "Data do Certificado de Conformidade Técnica"
END_DATE = "Data de Validade do Certificado"
CERTIFICATE_STATUS = "Situação do Certificado"
APPLICATION_STATUS = "Situação do Requerimento"
REQUIRED_COLUMNS = frozenset({
    "Data da Homologação", HOMOLOGATION, COMPANY, CNPJ,
    "Certificado de Conformidade Técnica", START_DATE, END_DATE,
    CERTIFICATE_STATUS, APPLICATION_STATUS, "Nome do Fabricante",
    "Modelo", "Nome Comercial", "Tipo do Produto",
})


class AnatelSourceError(RuntimeError):
    """A safe, user-displayable failure that must preserve the last good import."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _identifier(value: str) -> str:
    # Only the usual presentation punctuation is allowed: letters must not be
    # silently discarded and turn a malformed identifier into a valid match.
    return re.sub(r"[\s./-]", "", value)


def _source_date(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    try:
        if re.fullmatch(r"\d{2}/\d{2}/\d{4}", value):
            return datetime.strptime(value, "%d/%m/%Y").date().isoformat()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value).isoformat()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?", value):
            return datetime.fromisoformat(value).date().isoformat()
    except ValueError:
        pass
    raise AnatelSourceError(
        "anatel_source_invalid_record",
        "A base da Anatel contém uma data inválida em uma certificação da Makisa.",
    )


def _values(rows: list[dict[str, str]], column: str) -> list[str]:
    return sorted({row[column].strip() for row in rows if row[column].strip()})


def _unique_date(rows: list[dict[str, str]], column: str, alerts: list[str]) -> str | None:
    values = {_source_date(row[column]) for row in rows}
    if len(values) > 1:
        alerts.append(f"{column}: há datas divergentes na fonte; confira os registros originais.")
        return None
    value = next(iter(values))
    if value is None:
        alerts.append(f"{column}: não informada na fonte.")
    return value


def _group_rows(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, dict[str, str]]] = {}
    for row in rows:
        number = _identifier(row[HOMOLOGATION])
        if not re.fullmatch(r"[0-9]{12}", number) or any(
            not row[column].strip()
            for column in (
                COMPANY, "Modelo", "Tipo do Produto", "Certificado de Conformidade Técnica",
                CERTIFICATE_STATUS, APPLICATION_STATUS,
            )
        ):
            raise AnatelSourceError(
                "anatel_source_invalid_record",
                "A base da Anatel contém uma certificação da Makisa incompleta ou inválida.",
            )
        # Validate every matching date, including the retained homologation date.
        start = _source_date(row[START_DATE])
        end = _source_date(row[END_DATE])
        _source_date(row["Data da Homologação"])
        if start and end and start > end:
            raise AnatelSourceError(
                "anatel_source_invalid_record",
                "A base da Anatel contém um período de validade inválido para a Makisa.",
            )
        canonical = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        groups.setdefault(number, {})[canonical] = row

    records = []
    for number, unique in sorted(groups.items()):
        original = [unique[key] for key in sorted(unique)]
        alerts: list[str] = []
        types = _values(original, "Tipo do Produto")
        certificate_statuses = _values(original, CERTIFICATE_STATUS)
        application_statuses = _values(original, APPLICATION_STATUS)
        for label, values in (
            (CERTIFICATE_STATUS, certificate_statuses),
            (APPLICATION_STATUS, application_statuses),
        ):
            if len(values) > 1:
                alerts.append(f"{label}: há situações diferentes nos registros da fonte.")
        records.append({
            "numero": number,
            "cnpj": MAKISA_CNPJ,
            "nome_empresa": "; ".join(_values(original, COMPANY)),
            "produto": "; ".join(types),
            "modelos": _values(original, "Modelo"),
            "nomes_comerciais": _values(original, "Nome Comercial"),
            "tipos_produto": types,
            "fabricantes": _values(original, "Nome do Fabricante"),
            "certificados": _values(original, "Certificado de Conformidade Técnica"),
            "inicio": _unique_date(original, START_DATE, alerts),
            "fim": _unique_date(original, END_DATE, alerts),
            "situacao_certificado": "; ".join(certificate_statuses),
            "situacao_requerimento": "; ".join(application_statuses),
            "dados_origem": original,
            "alertas": alerts,
        })
    return records


def parse_certificacoes_zip(data: bytes) -> list[dict[str, Any]]:
    """Validate the complete ZIP/CSV before returning a nonempty Makisa snapshot."""
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise AnatelSourceError(
            "anatel_source_too_large", "O arquivo da Anatel excedeu o tamanho permitido.",
        )
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) > MAX_ZIP_MEMBERS or sum(
                member.file_size for member in members
            ) > MAX_UNCOMPRESSED_BYTES:
                raise AnatelSourceError(
                    "anatel_source_too_large", "A base da Anatel excedeu o tamanho permitido.",
                )
            csv_members = [member for member in members if member.filename == CSV_FILENAME]
            if len(csv_members) != 1 or csv_members[0].flag_bits & 1:
                raise AnatelSourceError(
                    "anatel_source_invalid_zip",
                    "O arquivo da Anatel não contém a base de certificações esperada.",
                )
            # No archive member is ever extracted to disk. ZipExtFile limits
            # decompression to the already bounded declared uncompressed size.
            with archive.open(csv_members[0]) as source:
                reader = csv.DictReader(
                    io.TextIOWrapper(source, encoding="utf-8-sig", newline=""),
                    delimiter=";", strict=True,
                )
                headers = reader.fieldnames or []
                if len(headers) != len(set(headers)) or not REQUIRED_COLUMNS.issubset(headers):
                    raise AnatelSourceError(
                        "anatel_source_invalid_schema",
                        "As colunas da base da Anatel mudaram; a importação foi preservada.",
                    )
                rows = []
                for row in reader:
                    if None in row or any(value is None for value in row.values()):
                        raise AnatelSourceError(
                            "anatel_source_invalid_csv",
                            "A base da Anatel contém uma linha incompleta ou inválida.",
                        )
                    if _identifier(row[CNPJ]) == MAKISA_CNPJ:
                        rows.append(row)
    except (
        zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError,
        NotImplementedError, EOFError, OSError, zlib.error,
    ) as exc:
        if isinstance(exc, AnatelSourceError):
            raise
        raise AnatelSourceError(
            "anatel_source_invalid_zip", "Não foi possível validar o arquivo ZIP da Anatel.",
        ) from exc
    except (UnicodeError, csv.Error) as exc:
        raise AnatelSourceError(
            "anatel_source_invalid_csv", "Não foi possível ler a base CSV da Anatel.",
        ) from exc
    if not rows:
        raise AnatelSourceError(
            "anatel_source_empty",
            "A consulta não retornou certificações para o CNPJ da Makisa; "
            "os dados foram preservados.",
        )
    return _group_rows(rows)


def _last_modified(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(UTC).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


async def fetch_certificacoes() -> dict[str, Any]:
    """Download only the fixed official source, with bounded time and size."""
    try:
        # A total deadline also bounds slow streams that never hit a read timeout.
        async with asyncio.timeout(DOWNLOAD_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(DOWNLOAD_TIMEOUT_SECONDS, connect=15),
                follow_redirects=False,
            ) as client:
                async with client.stream("GET", SOURCE_URL) as response:
                    if response.status_code != 200:
                        raise AnatelSourceError(
                            "anatel_source_http",
                            "A base oficial da Anatel está indisponível no momento.",
                        )
                    length = response.headers.get("content-length", "")
                    if length.isdecimal() and int(length) > MAX_DOWNLOAD_BYTES:
                        raise AnatelSourceError(
                            "anatel_source_too_large",
                            "O arquivo da Anatel excedeu o tamanho permitido.",
                        )
                    content = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                        if len(content) + len(chunk) > MAX_DOWNLOAD_BYTES:
                            raise AnatelSourceError(
                                "anatel_source_too_large",
                                "O arquivo da Anatel excedeu o tamanho permitido.",
                            )
                        content.extend(chunk)
                    source_updated_at = _last_modified(response.headers.get("last-modified"))
    except (httpx.HTTPError, TimeoutError) as exc:
        raise AnatelSourceError(
            "anatel_source_network", "Não foi possível consultar a Anatel; tente novamente depois.",
        ) from exc
    data = bytes(content)
    records = await asyncio.to_thread(parse_certificacoes_zip, data)
    return {
        "source_url": SOURCE_URL,
        "source_updated_at": source_updated_at,
        "sha256": hashlib.sha256(data).hexdigest(),
        "records": records,
    }
