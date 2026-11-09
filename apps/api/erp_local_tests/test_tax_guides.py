"""DIFAL read from the notes. The guide values must be the ones written in each XML."""
# ruff: noqa: S314 - Synthetic local XML.

import xml.etree.ElementTree as ET
from decimal import Decimal

import pytest

from app.erp_local import tax_guides as tg
from app.erp_local.core import ERPError

NS = "http://www.portalfiscal.inf.br/nfe"
CNPJ = "11222333000181"
COMPANY = {
    "cnpj": CNPJ,
    "name": "Loja & Cia",
    "address": {
        "street": "Rua A",
        "number": "1",
        "city_code": "3106200",
        "uf": "MG",
        "zip": "30110000",
    },
}


def note(
    key="1" * 44, state="BA", difal="10.00", fcp="2.00", day="2026-11-05", document="52998224725"
):
    return (
        f'<nfeProc xmlns="{NS}"><NFe><infNFe Id="NFe{key}"><ide><nNF>9</nNF>'
        f"<dhEmi>{day}T10:00:00-03:00</dhEmi></ide><emit><CNPJ>{CNPJ}</CNPJ></emit>"
        f"<dest><CPF>{document}</CPF><xNome>Ana &amp; Bia</xNome><enderDest><cMun>2927408</cMun>"
        f"<UF>{state}</UF></enderDest></dest><total><ICMSTot><vICMSUFDest>{difal}</vICMSUFDest>"
        f"<vFCPUFDest>{fcp}</vFCPUFDest></ICMSTot></total></infNFe></NFe></nfeProc>"
    ).encode()


def store(erp, *notes):
    with erp.connection() as db:
        for index, raw in enumerate(notes):
            db.execute(
                "INSERT INTO fiscal_xml VALUES (?,?,?,?,?)",
                (f"n{index}", f"d{index}", raw, "{}", "2026-11-05"),
            )


def test_note_without_difal_gives_nothing():
    assert tg.read_note(note(difal="0.00", fcp="0.00")) is None


def test_report_groups_by_state_and_respects_the_period(erp):
    store(
        erp,
        note("1" * 44),
        note("2" * 44, difal="5.50", fcp="0.00"),
        note("3" * 44, state="SP", difal="7.00", fcp="0.00"),
        note("4" * 44, day="2026-10-01"),
    )
    with erp.connection() as db:
        report = tg.report(db, "2026-11-01", "2026-11-30")
    by_state = {s["state"]: s for s in report["states"]}
    assert by_state["BA"]["notes"] == 2 and Decimal(by_state["BA"]["difal"]) == Decimal("15.50")
    assert Decimal(by_state["BA"]["fcp"]) == Decimal("2.00") and by_state["BA"]["guide"] == "GNRE"
    assert by_state["SP"]["guide"] == "DARE-SP"
    assert len(report["notes"]) == 3


def test_batch_has_one_guide_per_note_with_exact_values_and_leaves_sp_out():
    notes = [tg.read_note(note("1" * 44)), tg.read_note(note("3" * 44, state="SP"))]
    batch = tg.gnre_batch(COMPANY, notes, due="2026-11-20", payment="2026-11-20")
    assert batch["guides"] == 1 and batch["left_out"] == ["3" * 44]
    root = ET.fromstring(batch["xml"])
    ns = {"g": tg.GNRE_NS}
    guide = root.find("g:guias/g:TDadosGNRE", ns)
    assert guide.find("g:ufFavorecida", ns).text == "BA"
    assert guide.find("g:valorGNRE", ns).text == "12.00"
    items = guide.findall("g:itensGNRE/g:item", ns)
    assert [i.find("g:receita", ns).text for i in items] == [tg.RECEITA_DIFAL, tg.RECEITA_FCP]
    assert [i.find("g:valor", ns).text for i in items] == ["10.00", "2.00"]
    assert items[0].find("g:documentoOrigem", ns).text == "1" * 44
    assert "Loja &amp; Cia" in batch["xml"] and "Ana &amp; Bia" in batch["xml"]


def test_batch_needs_dates_and_a_company_with_notes(erp):
    with pytest.raises(ERPError, match="vencimento"):
        tg.gnre_batch(COMPANY, [], due="", payment="2026-11-20")
    with erp.connection() as db, pytest.raises(ERPError, match="não encontrada"):
        tg.batch_from_db(db, "nada", "2026-11-01", "2026-11-30", "2026-11-20", "2026-11-20")
