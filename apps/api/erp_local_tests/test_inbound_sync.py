"""Note search and manifestation with a simulated SEFAZ. Nothing here reaches the network."""
# ruff: noqa: S314 - Synthetic local XML.

import base64
import gzip
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from test_inbound_invoices import key_for, note

from app.erp_local import inbound_sync as sync
from app.erp_local import nfe_sefaz, nfe_xml
from app.erp_local.core import ERPError

NS = "http://www.portalfiscal.inf.br/nfe"
CNPJ = "12345678000195"
COMPANY = {"cnpj": CNPJ, "address": {"uf": "SP"}}
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
KEY = "3" * 44


def packed(xml):
    return base64.b64encode(gzip.compress(xml.encode())).decode()


def summary_xml(key=KEY):
    return (
        f'<resNFe xmlns="{NS}" versao="1.01"><chNFe>{key}</chNFe><CNPJ>11222333000181</CNPJ>'
        "<xNome>Fornecedor</xNome><dhEmi>2026-11-05T10:00:00-03:00</dhEmi><tpNF>1</tpNF>"
        "<vNF>115.00</vNF><cSitNFe>1</cSitNFe></resNFe>"
    )


def answer(code, last, top, *docs):
    body = "".join(
        f'<docZip NSU="{nsu}" schema="{schema}">{packed(xml)}</docZip>' for nsu, schema, xml in docs
    )
    return (
        f'<soap:Envelope xmlns:soap="s"><soap:Body><retDistDFeInt xmlns="{NS}">'
        f"<cStat>{code}</cStat><xMotivo>m</xMotivo><ultNSU>{last}</ultNSU><maxNSU>{top}</maxNSU>"
        f"<loteDistDFeInt>{body}</loteDistDFeInt></retDistDFeInt></soap:Body></soap:Envelope>"
    )


def client(*replies):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text=replies[min(len(calls) - 1, len(replies) - 1)])

    return nfe_sefaz.Sefaz("SP", transport=httpx.MockTransport(handler)), calls


def test_summaries_and_full_notes_each_go_to_their_place_and_cursor_moves(erp):
    sefaz, calls = client(
        answer(
            "138",
            "000000000000002",
            "000000000000002",
            ("1", "resNFe_v1.01.xsd", summary_xml()),
            ("2", "procNFe_v4.00.xsd", note()),
        )
    )
    report = sync.run_company(erp, COMPANY, sefaz, now=NOW)
    assert report["notes"] == 1 and report["summaries"] == 1
    assert calls[0].url.host == "hom1.nfe.fazenda.gov.br"
    assert b"<ultNSU>000000000000000</ultNSU>" in calls[0].content
    with erp.connection() as db:
        keys = [r[0] for r in db.execute("SELECT key FROM inbound_invoices")]
        snapshot = sync.snapshot(db)
    assert keys == [key_for()]
    assert snapshot["summaries"][0]["issuer_name"] == "Fornecedor"
    assert snapshot["cursors"][0]["last_nsu"] == "000000000000002"


def test_keeps_reading_while_there_is_more_and_stops_at_the_top(erp):
    sefaz, calls = client(
        answer(
            "138", "000000000000001", "000000000000002", ("1", "resNFe_v1.01.xsd", summary_xml())
        ),
        answer("138", "000000000000002", "000000000000002"),
    )
    assert sync.run_company(erp, COMPANY, sefaz, now=NOW)["pages"] == 2
    assert b"<ultNSU>000000000000001</ultNSU>" in calls[1].content


@pytest.mark.parametrize("code", ["137", "656"])
def test_empty_or_refused_answer_waits_an_hour_without_asking_again(erp, code):
    sefaz, calls = client(answer(code, "000000000000000", "000000000000000"))
    assert sync.run_company(erp, COMPANY, sefaz, now=NOW)["code"] == code
    assert (
        sync.run_company(erp, COMPANY, sefaz, now=NOW + timedelta(minutes=59))["state"] == "waiting"
    )
    assert len(calls) == 1
    sync.run_company(erp, COMPANY, sefaz, now=NOW + timedelta(minutes=61))
    assert len(calls) == 2


def test_oversized_document_is_refused(monkeypatch):
    monkeypatch.setattr(nfe_sefaz, "DOCUMENT_LIMIT", 10)
    sefaz, _ = client(answer("138", "1", "1", ("1", "resNFe_v1.01.xsd", summary_xml())))
    with pytest.raises(ValueError, match="limite"):
        sefaz.distribution(CNPJ, "SP", "0")


def test_production_address_stays_closed(monkeypatch):
    monkeypatch.setitem(nfe_sefaz.NATIONAL, "manifest", "https://www1.nfe.fazenda.gov.br/x")
    sefaz, calls = client("<x/>")
    with pytest.raises(nfe_sefaz.ProductionClosedError):
        sefaz.manifest("<evento/>")
    assert calls == []


def test_manifest_event_goes_to_the_national_organ():
    xml = nfe_xml.event(
        "210240",
        uf="SP",
        cnpj=CNPJ,
        key=KEY,
        moment="2026-11-06T10:00:00-03:00",
        sequence=1,
        text="Mercadoria nunca recebida",
    )
    assert "<cOrgao>91</cOrgao>" in xml and "<tpEvento>210240</tpEvento>" in xml
    assert "<xJust>Mercadoria nunca recebida</xJust>" in xml
    confirmed = nfe_xml.event(
        "210200", uf="SP", cnpj=CNPJ, key=KEY, moment="2026-11-06T10:00:00-03:00", sequence=1
    )
    assert "xJust" not in confirmed and "Confirmacao da Operacao" in confirmed


def reply_event(code):
    return (
        f'<retEvento xmlns="{NS}"><infEvento><cStat>{code}</cStat><xMotivo>ok</xMotivo>'
        "<nProt>999</nProt></infEvento></retEvento>"
    )


@pytest.fixture
def unsigned(monkeypatch):
    monkeypatch.setattr(nfe_xml, "sign_element", lambda xml, *_: xml)


def test_accepted_manifest_is_recorded(erp, unsigned):
    sefaz, _ = client(reply_event("135"))
    sync.manifest(erp, KEY, "210210", "", COMPANY, sefaz, b"", b"")
    with erp.connection() as db:
        row = sync.snapshot(db)["summaries"][0]
    assert row["manifest"] == "210210" and row["manifest_label"] == "Ciência"


def test_refused_manifest_records_nothing(erp, unsigned):
    sefaz, _ = client(reply_event("573"))
    with pytest.raises(ERPError, match="recusou"):
        sync.manifest(erp, KEY, "210200", "", COMPANY, sefaz, b"", b"")
    with erp.connection() as db:
        assert sync.snapshot(db)["summaries"] == []


def test_not_realized_needs_a_real_justification(erp, unsigned):
    sefaz, calls = client(reply_event("135"))
    with pytest.raises(ERPError, match="15 letras"):
        sync.manifest(erp, KEY, "210240", "curta", COMPANY, sefaz, b"", b"")
    with pytest.raises(ERPError, match="Escolha"):
        sync.manifest(erp, KEY, "999", "", COMPANY, sefaz, b"", b"")
    assert calls == []
