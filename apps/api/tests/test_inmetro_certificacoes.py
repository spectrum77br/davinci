"""ProdCert público: identidade, campos oficiais e completude das quatro consultas."""

import asyncio
from copy import deepcopy
from html import escape
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from app.services import inmetro_certificacoes as source


def certificate(**changes):
    result = {
        "certificador": "MODERNA", "numero": "MODERNA-0069/25", "inicio": "30/04/2026",
        "fim": "30/04/2032", "situacao": "Ativo", "cnpj": source.MAKISA_CNPJ,
        "empresa": "MAKISA TRADING LTDA", "papel": "SOLICITANTE", "empresa_status": "ATIVO",
        "produtos": [["URANYX", "UAF001-M1", "SIM", "Fritadeira 4L, 127V e 220V"]],
    }
    result.update(changes)
    return result


def _cells(values, css="listagem"):
    return "<tr>" + "".join(
        f'<td class="{css}">{escape(str(value))}&nbsp;</td>' for value in values
    ) + "</tr>"


def page(status="ativo", certificates=None, *, total_cert=None, total_prod=None,
         page_number=1, links=(), hidden_cnpj=source.MAKISA_CNPJ):
    certificates = [certificate()] if certificates is None else certificates
    total_cert = len(certificates) if total_cert is None else total_cert
    total_prod = (
        sum(len(cert["produtos"]) for cert in certificates) if total_prod is None else total_prod
    )
    content = (
        '<html><form method="post"><input type="hidden" name="cnpj_cpf" '
        f'value="{hidden_cnpj}"><input type="hidden" name="status_certificado" '
        f'value="{status}"><table><tr><td>Resultado da Consulta:<br><b>{total_cert}</b> '
        f'Certificado(s)<br><b>{total_prod}</b> Produtos(s)<br><b>0</b> Serviços(s)</td>'
        f'<td>P&aacute;gina <font>{page_number}</font></td></tr></table><table><tr><td>'
    )
    for cert in certificates:
        content += (
            '<table><tr><td colspan="6" class="listagem"><strong><font size="2">'
            f'Certificador: <font>{escape(cert["certificador"])}</font>&nbsp;'
            f'N&ordm; Certificado: <font>{escape(cert["numero"])}</font>&nbsp;Tipo: '
            f'<font>Produto</font>&nbsp;Emiss&atilde;o: <font>{cert["inicio"]}</font>&nbsp;'
            f'Validade: <font>{cert["fim"]}</font>&nbsp;Status do Certificado: '
            f'<font>{escape(cert["situacao"])}</font></font></strong>&nbsp;'
            '<strong>Doc.Normativo</strong><textarea></textarea><iframe></iframe></td></tr>'
        )
        content += _cells([
            "CNPJ/CPF", "Razão Social / Nome (PF)", "Nome fantasia", "Endereço", "Status",
            "Papel da empresa",
        ], "titulo_tabela")
        content += _cells([
            cert["cnpj"], cert["empresa"], "", "ITAJAÍ, SC, BRASIL", cert["empresa_status"],
            cert["papel"],
        ])
        content += '<tr><td colspan="6"><table>'
        content += _cells(["Marca", "Modelo", "Importado", "Descrição"], "titulo_tabela")
        content += "".join(_cells(product) for product in cert["produtos"])
        content += "</table></td></tr></table>"
    content += "</td></tr></table>"
    content += "".join(
        f'<span onclick="Pagina({number},{start},{end})">{number}</span>'
        for number, start, end in links
    )
    content += "</form></html>"
    return content.encode("iso-8859-1")


def test_two_models_sharing_certificate_have_distinct_official_identities():
    products = [
        ["URANYX", "UAF001-M1", "SIM", "Fritadeira 4L"],
        ["URANYX", "UAF002-M1", "SIM", "Fritadeira 8L"],
    ]
    parsed = source.parse_certificacoes_page(
        page(certificates=[certificate(produtos=products)]), status_filtro="ativo",
    )
    assert parsed["certificados_total"] == parsed["certificados_pagina"] == 1
    assert parsed["produtos_total"] == parsed["produtos_pagina"] == 2
    first, second = parsed["records"]
    assert first["chave"] == "MODERNA|MODERNA-0069/25|UAF001-M1"
    assert second["chave"] == "MODERNA|MODERNA-0069/25|UAF002-M1"
    assert first["numero"] == "MODERNA-0069/25"
    assert first["modelo"] == "UAF001-M1"
    assert first["marca"] == "URANYX"
    assert first["descricao"] == "Fritadeira 4L"
    assert first["produto"] == first["descricao"]
    assert first["inicio"] == "2026-04-30"
    assert first["fim"] == "2032-04-30"
    assert first["situacao_certificado"] == "Ativo"
    assert first["source_updated_at"] is None
    assert first["source_url"] == source.SOURCE_URL
    assert first["campos_confirmados"] == ["modelo", "certificado", "numero", "inicio", "fim"]
    assert "nome_comercial" not in first
    assert "valor" not in first
    assert first["dados_origem"]["certificado"]["Validade"] == "30/04/2032"
    assert first["dados_origem"]["produto"]["Modelo"] == "UAF001-M1"


def test_company_status_does_not_replace_certificate_status():
    record = source.parse_certificacoes_page(
        page("suspenso", [certificate(situacao="Suspenso", empresa_status="ATIVO")]),
        status_filtro="suspenso",
    )["records"][0]
    assert record["situacao_certificado"] == "Suspenso"
    assert record["dados_origem"]["empresas"][0]["Status"] == "ATIVO"


def test_missing_dates_stay_unconfirmed_and_preserve_manual_date_permission():
    record = source.parse_certificacoes_page(
        page(certificates=[certificate(inicio="", fim="")]), status_filtro="ativo",
    )["records"][0]
    assert record["inicio"] is record["fim"] is None
    assert record["campos_confirmados"] == ["modelo", "certificado", "numero"]
    assert len(record["alertas"]) == 2


def test_empty_description_uses_exact_model_and_identity_only_normalizes_whitespace_case():
    record = source.parse_certificacoes_page(page(certificates=[certificate(
        certificador=" moderna ", numero=" Moderna-0069/25 ",
        produtos=[["URANYX", " uaf001-M1 ", "SIM", ""]],
    )]), status_filtro="ativo")["records"][0]
    assert record["chave"] == "MODERNA|MODERNA-0069/25|UAF001-M1"
    assert record["produto"] == "uaf001-M1"


@pytest.mark.parametrize("fields", [
    {"cnpj": "11111111000111"}, {"papel": "FABRICANTE"}, {"papel": "IMPORTADOR"},
    {"cnpj": "401911040001450"}, {"empresa": ""},
    {"inicio": "31/02/2026"}, {"fim": "2028-07-30"}, {"fim": "30/04/2025"},
    {"certificador": ""}, {"numero": ""}, {"situacao": "Suspenso"},
    {"produtos": [["URANYX", "", "SIM", "Descrição"]]},
    {"produtos": [["URANYX", "A|B", "SIM", "Descrição"]]},
    {"produtos": []},
])
def test_invalid_official_identity_dates_or_applicant_fail_closed(fields):
    with pytest.raises(source.InmetroSourceError):
        source.parse_certificacoes_page(
            page(certificates=[certificate(**fields)]), status_filtro="ativo",
        )


@pytest.mark.parametrize("status,state", [
    ("ativo", "Ativo"), ("vencido", "Expirado"),
    ("suspenso", "Suspenso"), ("naoativo", "Cancelado"),
])
def test_status_filters_are_supported_and_zero_is_valid_for_an_individual_status(status, state):
    assert source.parse_certificacoes_page(
        page(status, [certificate(situacao=state)]), status_filtro=status,
    )["records"][0]["situacao_certificado"] == state
    assert source.parse_certificacoes_page(page(status, []), status_filtro=status)["records"] == []


@pytest.mark.parametrize("content", [
    b"<html>Service unavailable</html>",
    b"<html>Complete o captcha</html>",
    page(hidden_cnpj="11111111000111"),
    page("suspenso", []),
    page().replace(b"Modelo", b"NovaColuna"),
    page().replace(b"</td></tr></table></form></html>", b""),
    page(total_cert=0, total_prod=0),
    page(total_cert=1, total_prod=0),
    page(page_number=2),
    page(links=[(source.MAX_PAGES_PER_STATUS + 1, 1, 10)]),
])
def test_incomplete_page_changed_schema_filter_or_totals_are_rejected(content):
    with pytest.raises(source.InmetroSourceError):
        source.parse_certificacoes_page(content, status_filtro="ativo")


def setup_source(pages=None):
    pages = pages or {}

    def respond(request):
        body = parse_qs(request.content.decode())
        assert body["cnpj_cpf"] == [source.MAKISA_CNPJ]
        assert body["papel_empresa"] == ["22"]
        state = body["status_certificado"][0]
        number = int(body.get("pagina", ["1"])[0])
        content = pages.get((state, number))
        if content is None:
            content = page(state, [certificate()] if state == "ativo" else [])
        return httpx.Response(200, content=content)

    return respx.post(source.QUERY_URL).mock(side_effect=respond)


@respx.mock
async def test_fetch_checks_all_four_statuses_sequentially_without_inventing_source_update():
    route = setup_source()
    result = await source.fetch_certificacoes()
    assert route.call_count == 4
    assert [parse_qs(call.request.content.decode())["status_certificado"][0]
            for call in route.calls] == list(source.STATUS_FILTERS)
    assert result["source_url"] == source.SOURCE_URL
    assert result["source_updated_at"] is None
    assert len(result["sha256"]) == 64
    assert len(result["records"]) == 1


@respx.mock
async def test_fetch_follows_observed_pagination_and_reconciles_all_counts():
    first = page(total_cert=2, total_prod=2, links=[(2, 1, 2)])
    second = page(
        certificates=[certificate(numero="MODERNA-0070/25")], page_number=2,
        total_cert=2, total_prod=2, links=[(1, 1, 2)],
    )
    route = setup_source({("ativo", 1): first, ("ativo", 2): second})
    result = await source.fetch_certificacoes()
    assert route.call_count == 5
    assert len(result["records"]) == 2
    second_request = parse_qs(route.calls[1].request.content.decode())
    assert {key: second_request[key] for key in ("pagina", "paginaini", "paginafim")} == {
        "pagina": ["2"], "paginaini": ["1"], "paginafim": ["2"],
    }


@respx.mock
@pytest.mark.parametrize("second", [
    page(certificates=[certificate(numero="MODERNA-0070/25")], page_number=2),
    page(page_number=1, total_cert=2, total_prod=2),
    page(certificates=[], page_number=2, total_cert=2, total_prod=2),
])
async def test_fetch_rejects_changed_totals_wrong_page_or_missing_rows(second):
    setup_source({
        ("ativo", 1): page(total_cert=2, total_prod=2, links=[(2, 1, 2)]),
        ("ativo", 2): second,
    })
    with pytest.raises(source.InmetroSourceError):
        await source.fetch_certificacoes()


@respx.mock
async def test_larger_total_without_pagination_is_not_a_complete_snapshot():
    setup_source({("ativo", 1): page(total_cert=2, total_prod=2)})
    with pytest.raises(source.InmetroSourceError, match="todas as páginas"):
        await source.fetch_certificacoes()


@respx.mock
async def test_exact_duplicate_product_is_deduplicated_after_counting_source_rows():
    products = certificate()["produtos"]
    setup_source({("ativo", 1): page(certificates=[certificate(produtos=products * 2)])})
    assert len((await source.fetch_certificacoes())["records"]) == 1


@respx.mock
@pytest.mark.parametrize("column", [0, 3])
async def test_same_identity_with_divergent_brand_or_description_rejects_whole_snapshot(column):
    products = certificate()["produtos"]
    second = deepcopy(products[0])
    second[column] = "Outro conteúdo"
    setup_source({("ativo", 1): page(certificates=[certificate(produtos=[products[0], second])])})
    with pytest.raises(source.InmetroSourceError, match="mesma identificação"):
        await source.fetch_certificacoes()


@respx.mock
async def test_cross_status_conflict_is_not_silently_resolved():
    setup_source({("suspenso", 1): page("suspenso", [certificate(situacao="Suspenso")])})
    with pytest.raises(source.InmetroSourceError, match="mesma identificação"):
        await source.fetch_certificacoes()


@respx.mock
async def test_zero_across_all_statuses_preserves_existing_data():
    route = setup_source({(status, 1): page(status, []) for status in source.STATUS_FILTERS})
    with pytest.raises(source.InmetroSourceError) as raised:
        await source.fetch_certificacoes()
    assert route.call_count == 4
    assert raised.value.code == "inmetro_source_empty"


@respx.mock
@pytest.mark.parametrize("status", [301, 302, 403, 429, 500, 503])
async def test_http_error_or_redirect_stops_without_following_other_urls(status):
    respx.post(source.QUERY_URL).mock(return_value=httpx.Response(
        status, headers={"location": "https://example.invalid/other"}, content=page(),
    ))
    with pytest.raises(source.InmetroSourceError) as raised:
        await source.fetch_certificacoes()
    assert raised.value.code == "inmetro_source_http"
    assert len(respx.calls) == 1


@respx.mock
async def test_failure_in_last_status_does_not_return_earlier_valid_records():
    count = 0

    def respond(request):
        nonlocal count
        count += 1
        if count == 4:
            raise httpx.ReadTimeout("upstream details")
        state = parse_qs(request.content.decode())["status_certificado"][0]
        return httpx.Response(200, content=page(state, [certificate()] if state == "ativo" else []))

    respx.post(source.QUERY_URL).mock(side_effect=respond)
    with pytest.raises(source.InmetroSourceError) as raised:
        await source.fetch_certificacoes()
    assert count == 4
    assert raised.value.code == "inmetro_source_network"
    assert "upstream" not in raised.value.message


@respx.mock
@pytest.mark.parametrize("header", ["99999999", "invalid"])
async def test_response_size_is_bounded_by_header_and_stream(monkeypatch, header):
    content = page()
    monkeypatch.setattr(source, "MAX_PAGE_BYTES", len(content) - 1)
    respx.post(source.QUERY_URL).mock(return_value=httpx.Response(
        200, content=content, headers={"content-length": header},
    ))
    with pytest.raises(source.InmetroSourceError) as raised:
        await source.fetch_certificacoes()
    assert raised.value.code == "inmetro_source_too_large"


@respx.mock
async def test_total_download_budget_and_total_deadline_are_bounded(monkeypatch):
    setup_source()
    monkeypatch.setattr(source, "MAX_DOWNLOAD_BYTES", 10)
    with pytest.raises(source.InmetroSourceError) as raised:
        await source.fetch_certificacoes()
    assert raised.value.code == "inmetro_source_too_large"
    monkeypatch.setattr(source, "MAX_DOWNLOAD_BYTES", 8 * 1024 * 1024)
    monkeypatch.setattr(source, "TOTAL_TIMEOUT_SECONDS", 0.01)

    async def slow_response(request):
        await asyncio.sleep(0.1)
        return httpx.Response(200, content=page())

    respx.post(source.QUERY_URL).mock(side_effect=slow_response)
    with pytest.raises(source.InmetroSourceError) as raised:
        await source.fetch_certificacoes()
    assert raised.value.code == "inmetro_source_network"
