"""Reuse the Comunicador's existing synthetic XML fixture, without its DB conftest."""

import ast
import hashlib
import sys
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.erp_local.api import create_app
from app.erp_local.core import ERPError
from app.erp_local.fiscal import render, snapshot
from app.services.danfe_render import chave_formatada, code128c, gerar_pdf


def fixture_xml(**kwargs):
    source = Path(__file__).resolve().parents[1] / "tests/test_atendimento_danfe.py"
    tree = ast.parse(source.read_text())
    selected = [
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef)
        and n.name == "_xml"
        or isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "CHAVE" for t in n.targets)
    ]
    namespace = {}
    # Only our synthetic fixture and its constant are evaluated, without its production imports.
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(source), "exec"), namespace)  # noqa: S102
    return namespace["_xml"](**kwargs)


def test_shared_renderer_is_deterministic_preserves_identity_and_has_no_production_imports():
    xml = fixture_xml()
    pdf = gerar_pdf(xml)
    assert pdf == gerar_pdf(xml)
    text = "".join(p.get_text() for p in pymupdf.open(stream=pdf, filetype="pdf"))
    assert "DANFE" in text and "Nº 001883" in text and "SERIE: 1" in text
    assert "135260000000001" in text and "424,00" in text
    assert not {"app.db", "app.main", "app.config"} & sys.modules.keys()
    assert sum(code128c("1" * 44)) == 11 * 24 + 13
    assert len(chave_formatada("1" * 44).split()) == 11


def test_homologation_and_missing_protocol_keep_watermark_and_long_xml_paginates():
    for kwargs in ({"tp_amb": "2"}, {"protocolo": False}):
        doc = pymupdf.open(stream=gerar_pdf(fixture_xml(**kwargs)), filetype="pdf")
        assert "SEM VALOR FISCAL" in "".join(p.get_text() for p in doc)
    assert pymupdf.open(stream=gerar_pdf(fixture_xml(itens=40)), filetype="pdf").page_count > 1


def test_xml_archive_is_own_deduplicated_and_does_not_move_inventory_or_claim_verified_signature(
    erp,
):
    xml = fixture_xml()
    before = erp.snapshot()
    r = erp.command("xml1", "fiscal.xml.import", {"xml": xml.decode()})
    assert erp.command("xml2", "fiscal.xml.import", {"xml": xml.decode()})["duplicate"]
    assert erp.snapshot()["movements"] == before["movements"]
    with erp.connection() as db:
        note = snapshot(db)["documents"][0]
    assert note["source_status"] == "100" and not note["signature_verified"]
    assert note["xml_digest"] == hashlib.sha256(xml).hexdigest()
    assert render(erp, r["id"]) == gerar_pdf(xml)
    with pytest.raises(ERPError):
        erp.command("xml3", "fiscal.xml.import", {"xml": fixture_xml(v_nf="12.00").decode()})


@pytest.mark.parametrize(
    "xml", ["not xml", "<nothing/>", '<!DOCTYPE x [<!ENTITY a "x">]><x>&a;</x>']
)
def test_malformed_or_entity_xml_is_rejected_without_persisting_any_record(erp, xml):
    with pytest.raises(ERPError):
        erp.command("bad-xml", "fiscal.xml.import", {"xml": xml})
    with erp.connection() as db:
        assert not snapshot(db)["documents"]


def test_local_upload_and_pdf_endpoint_use_shared_renderer_and_reject_cross_origin(tmp_path):
    with TestClient(create_app(tmp_path / "fiscal.sqlite3"), base_url="http://127.0.0.1:8767") as c:
        xml = fixture_xml(tp_amb="2")
        r = c.post(
            "/api/erp-local/fiscal/xml",
            content=xml,
            headers={"X-ERP-Local": "1", "Content-Type": "application/xml"},
        )
        assert r.status_code == 200
        pdf = c.get("/api/erp-local/fiscal/" + r.json()["id"] + "/danfe")
        assert pdf.headers["content-type"] == "application/pdf" and pdf.content == gerar_pdf(xml)
        assert (
            c.post(
                "/api/erp-local/fiscal/xml",
                content=xml,
                headers={"X-ERP-Local": "1", "Origin": "https://example.com"},
            ).status_code
            == 403
        )
