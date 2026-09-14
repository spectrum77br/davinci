"""Pure source/HTTP tests: no database or live regulator requests."""

import asyncio
import csv
import hashlib
import io
import zipfile

import httpx
import pytest
import respx

from app.services import anatel_certificacoes as source


def row(**changes):
    values = {
        "Data da Homologação": "18/08/2026",
        "Número de Homologação": "071972618234",
        "Nome do Solicitante": "Makisa Trading LTDA",
        "CNPJ do Solicitante": source.MAKISA_CNPJ,
        "Certificado de Conformidade Técnica": "MOD-26.1234",
        "Data do Certificado de Conformidade Técnica": "30/07/2026",
        "Data de Validade do Certificado": "2028-07-30 00:00:00.000000",
        "Código de Situação do Certificado": "2",
        "Situação do Certificado": "Preenchimento Concluído",
        "Código de Situação do Requerimento": "6",
        "Situação do Requerimento": "Homologação Emitida",
        "Nome do Fabricante": "Fabricante Exemplo",
        "Modelo": "USM003",
        "Nome Comercial": "Telefone A",
        "Categoria do Produto": "1",
        "Tipo do Produto": "Telefone Móvel Celular",
        "IC_ANTENA": "N",
        "IC_ATIVO": "S",
        "País do Fabricante": "China",
        "CodUIT": "CHN",
        "CodISO": "CHN",
    }
    values.update(changes)
    return values


def make_zip(rows=None, *, headers=None, filename=source.CSV_FILENAME, raw=None):
    if raw is None:
        rows = [row()] if rows is None else rows
        output = io.StringIO(newline="")
        writer = csv.DictWriter(
            output, headers or list(row()), delimiter=";", extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)
        raw = output.getvalue().encode("utf-8-sig")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr(filename, raw)
    return archive.getvalue()


def test_groups_models_names_types_and_preserves_original_evidence():
    originals = [
        row(**{"Nome Comercial": "Telefone B"}),
        row(**{"Tipo do Produto": "Transceptor de Radiação Restrita"}),
        row(**{"Modelo": "USM004", "Nome Comercial": "Telefone C"}),
        row(),
    ]
    records = source.parse_certificacoes_zip(make_zip([*originals, originals[0]]))
    assert len(records) == 1
    record = records[0]
    assert record["numero"] == "071972618234"
    assert record["cnpj"] == source.MAKISA_CNPJ
    assert record["modelos"] == ["USM003", "USM004"]
    assert record["nomes_comerciais"] == ["Telefone A", "Telefone B", "Telefone C"]
    assert record["tipos_produto"] == [
        "Telefone Móvel Celular", "Transceptor de Radiação Restrita",
    ]
    assert record["produto"] == "; ".join(record["tipos_produto"])
    assert record["certificados"] == ["MOD-26.1234"]
    assert record["fabricantes"] == ["Fabricante Exemplo"]
    assert record["inicio"] == "2026-07-30"
    assert record["fim"] == "2028-07-30"
    assert record["alertas"] == []
    assert len(record["dados_origem"]) == 4
    assert all(original in record["dados_origem"] for original in originals)
    assert records == source.parse_certificacoes_zip(make_zip(list(reversed(originals))))


def test_matches_only_exact_cnpj_and_normalizes_presentation_without_losing_zeroes():
    records = source.parse_certificacoes_zip(make_zip([
        row(**{"CNPJ do Solicitante": "11111111000111"}),
        row(**{"CNPJ do Solicitante": "401911040001450"}),
        row(**{"CNPJ do Solicitante": "prefix40191104000145"}),
        row(**{"CNPJ do Solicitante": "40.191.104/0001-45",
               "Número de Homologação": "07197-26-18234"}),
    ]))
    assert len(records) == 1
    assert records[0]["numero"] == "071972618234"
    assert records[0]["dados_origem"][0][source.CNPJ] == "40.191.104/0001-45"


def test_same_model_distinct_homologation_stays_separate_and_sorted():
    records = source.parse_certificacoes_zip(make_zip([
        row(**{"Número de Homologação": "082162518234"}), row(),
    ]))
    assert [record["numero"] for record in records] == ["071972618234", "082162518234"]


def test_extra_columns_and_blank_commercial_name_are_preserved():
    original = row(**{"Nova coluna oficial": "Conteúdo", "Nome Comercial": ""})
    record = source.parse_certificacoes_zip(make_zip([original], headers=list(original)))[0]
    assert record["nomes_comerciais"] == []
    assert record["dados_origem"] == [original]


@pytest.mark.parametrize("active", ["S", "N", ""])
def test_statuses_remain_exact_and_ic_ativo_is_not_a_regulatory_status(active):
    record = source.parse_certificacoes_zip(make_zip([
        row(**{"Situação do Requerimento": "Em Análise - RE", "IC_ATIVO": active}),
    ]))[0]
    assert record["situacao_requerimento"] == "Em Análise - RE"
    assert record["situacao_certificado"] == "Preenchimento Concluído"
    assert "ativa" not in record.values()


def test_conflicting_dates_and_statuses_are_reported_without_choosing_one():
    record = source.parse_certificacoes_zip(make_zip([
        row(), row(**{
            "Data do Certificado de Conformidade Técnica": "31/07/2026",
            "Data de Validade do Certificado": "2028-07-31 00:00:00.000000",
            "Situação do Certificado": "Suspenso",
            "Situação do Requerimento": "Em Análise - RE",
        }),
    ]))[0]
    assert record["inicio"] is None
    assert record["fim"] is None
    assert record["situacao_certificado"] == "Preenchimento Concluído; Suspenso"
    assert record["situacao_requerimento"] == "Em Análise - RE; Homologação Emitida"
    assert len(record["alertas"]) == 4
    assert len(record["dados_origem"]) == 2


@pytest.mark.parametrize("end", ["2028-07-30", "30/07/2028", "2028-07-30T00:00:00"])
def test_equivalent_date_formats_do_not_introduce_conflicts(end):
    record = source.parse_certificacoes_zip(make_zip([
        row(), row(**{source.END_DATE: end}),
    ]))[0]
    assert record["fim"] == "2028-07-30"
    assert record["alertas"] == []


def test_missing_date_remains_unknown_and_does_not_select_another_rows_date():
    records = source.parse_certificacoes_zip(make_zip([
        row(**{source.END_DATE: ""}), row(),
    ]))
    assert records[0]["fim"] is None
    assert "divergentes" in records[0]["alertas"][0]
    missing = source.parse_certificacoes_zip(make_zip([
        row(**{source.START_DATE: "", source.END_DATE: ""}),
    ]))[0]
    assert missing["inicio"] is missing["fim"] is None
    assert len(missing["alertas"]) == 2


@pytest.mark.parametrize("changes", [
    {source.HOMOLOGATION: "07197261823"},
    {source.HOMOLOGATION: "bad071972618234"},
    {source.HOMOLOGATION: ""},
    {"Modelo": ""},
    {source.COMPANY: ""},
    {"Tipo do Produto": ""},
    {"Certificado de Conformidade Técnica": ""},
    {source.APPLICATION_STATUS: ""},
    {source.CERTIFICATE_STATUS: ""},
    {source.START_DATE: "31/02/2026"},
    {source.END_DATE: "2028-15-30 00:00:00.000000"},
    {source.END_DATE: "2028-07-30 garbage"},
    {source.END_DATE: "2028-07-30 25:00:00.000000"},
    {"Data da Homologação": "invalida"},
    {source.END_DATE: "2025-07-30"},
])
def test_invalid_matching_row_rejects_entire_snapshot(changes):
    with pytest.raises(source.AnatelSourceError) as raised:
        source.parse_certificacoes_zip(make_zip([row(), row(**changes)]))
    assert raised.value.code == "anatel_source_invalid_record"


@pytest.mark.parametrize("rows", [[], [row(**{source.CNPJ: "11111111000111"})],
                                 [row(**{source.CNPJ: ""})]])
def test_missing_makisa_is_never_a_successful_empty_snapshot(rows):
    with pytest.raises(source.AnatelSourceError) as raised:
        source.parse_certificacoes_zip(make_zip(rows))
    assert raised.value.code == "anatel_source_empty"


@pytest.mark.parametrize("headers", [
    [key for key in row() if key != source.END_DATE],
    [*row(), source.CNPJ],
    ["unexpected", "columns"],
])
def test_unknown_schema_or_duplicate_header_rejects_snapshot(headers):
    with pytest.raises(source.AnatelSourceError) as raised:
        source.parse_certificacoes_zip(make_zip(headers=headers))
    assert raised.value.code == "anatel_source_invalid_schema"


@pytest.mark.parametrize("content", [b"not a zip", b"PK\x03\x04", b"<html>unavailable</html>"])
def test_bad_zip_has_safe_error(content):
    with pytest.raises(source.AnatelSourceError) as raised:
        source.parse_certificacoes_zip(content)
    assert raised.value.code == "anatel_source_invalid_zip"
    assert str(raised.value) == raised.value.message


def test_unexpected_archive_member_is_not_used_or_extracted():
    with pytest.raises(source.AnatelSourceError) as raised:
        source.parse_certificacoes_zip(make_zip(filename="../../elsewhere.csv"))
    assert raised.value.code == "anatel_source_invalid_zip"


def test_corrupt_compressed_data_is_reported_as_invalid_zip():
    content = bytearray(make_zip())
    # Local header: name and extra field lengths precede the compressed payload.
    name_size = int.from_bytes(content[26:28], "little")
    extra_size = int.from_bytes(content[28:30], "little")
    offset = 30 + name_size + extra_size
    content[offset:offset + 20] = bytes(20)
    with pytest.raises(source.AnatelSourceError) as raised:
        source.parse_certificacoes_zip(bytes(content))
    assert raised.value.code == "anatel_source_invalid_zip"


@pytest.mark.parametrize("raw", [
    b"\xff\xfeinvalid",
    (";".join(row()) + '\n"unterminated').encode(),
    (";".join(row()) + "\nonly;two").encode(),
    (";".join(row()) + "\n" + ";".join([*row().values(), "extra"])).encode(),
])
def test_invalid_encoding_or_csv_row_rejects_snapshot(raw):
    with pytest.raises(source.AnatelSourceError) as raised:
        source.parse_certificacoes_zip(make_zip(raw=raw))
    assert raised.value.code == "anatel_source_invalid_csv"


def test_archive_download_and_uncompressed_limits(monkeypatch):
    content = make_zip()
    monkeypatch.setattr(source, "MAX_DOWNLOAD_BYTES", len(content) - 1)
    with pytest.raises(source.AnatelSourceError, match="tamanho permitido"):
        source.parse_certificacoes_zip(content)
    monkeypatch.setattr(source, "MAX_DOWNLOAD_BYTES", len(content))
    monkeypatch.setattr(source, "MAX_UNCOMPRESSED_BYTES", 100)
    with pytest.raises(source.AnatelSourceError) as raised:
        source.parse_certificacoes_zip(content)
    assert raised.value.code == "anatel_source_too_large"


@respx.mock
async def test_fetch_returns_evidence_hash_and_official_source_timestamp():
    content = make_zip()
    route = respx.get(source.SOURCE_URL).mock(return_value=httpx.Response(
        200, content=content, headers={"last-modified": "Mon, 14 Sep 2026 10:36:02 GMT"},
    ))
    result = await source.fetch_certificacoes()
    assert route.call_count == 1
    assert result["source_url"] == source.SOURCE_URL
    assert result["source_updated_at"] == "2026-09-14T10:36:02+00:00"
    assert result["sha256"] == hashlib.sha256(content).hexdigest()
    assert result["records"] == source.parse_certificacoes_zip(content)


@respx.mock
@pytest.mark.parametrize("modified", ["", "invalid", "Mon, 14 Sep 2026 10:36:02"])
async def test_missing_or_unusable_source_timestamp_is_unknown(modified):
    respx.get(source.SOURCE_URL).mock(return_value=httpx.Response(
        200, content=make_zip(), headers={"last-modified": modified},
    ))
    assert (await source.fetch_certificacoes())["source_updated_at"] is None


@respx.mock
@pytest.mark.parametrize("status", [206, 301, 302, 403, 404, 429, 500, 503])
async def test_http_errors_and_redirects_never_import_or_follow_another_url(status):
    respx.get(source.SOURCE_URL).mock(return_value=httpx.Response(
        status, content=make_zip(), headers={"location": "https://example.invalid/other.zip"},
    ))
    with pytest.raises(source.AnatelSourceError) as raised:
        await source.fetch_certificacoes()
    assert raised.value.code == "anatel_source_http"
    assert len(respx.calls) == 1


@respx.mock
@pytest.mark.parametrize("exception", [httpx.ReadTimeout, httpx.ConnectError])
async def test_network_errors_are_safe_and_actionable(exception):
    respx.get(source.SOURCE_URL).mock(side_effect=exception("private upstream detail"))
    with pytest.raises(source.AnatelSourceError) as raised:
        await source.fetch_certificacoes()
    assert raised.value.code == "anatel_source_network"
    assert "private" not in raised.value.message


@respx.mock
@pytest.mark.parametrize("length", ["999999999", "invalid"])
async def test_download_length_header_and_actual_stream_are_bounded(monkeypatch, length):
    content = make_zip()
    monkeypatch.setattr(source, "MAX_DOWNLOAD_BYTES", len(content) - 1)
    respx.get(source.SOURCE_URL).mock(return_value=httpx.Response(
        200, content=content, headers={"content-length": length},
    ))
    with pytest.raises(source.AnatelSourceError) as raised:
        await source.fetch_certificacoes()
    assert raised.value.code == "anatel_source_too_large"


@respx.mock
async def test_total_deadline_bounds_an_upstream_that_keeps_waiting(monkeypatch):
    monkeypatch.setattr(source, "DOWNLOAD_TIMEOUT_SECONDS", 0.01)

    async def slow_response(request):
        await asyncio.sleep(0.1)
        return httpx.Response(200, content=make_zip())

    respx.get(source.SOURCE_URL).mock(side_effect=slow_response)
    with pytest.raises(source.AnatelSourceError) as raised:
        await source.fetch_certificacoes()
    assert raised.value.code == "anatel_source_network"
