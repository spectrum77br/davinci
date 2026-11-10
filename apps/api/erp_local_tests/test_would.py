"""Homologação: o que sairia para fora vira uma resposta do que seria feito, sem sair."""

import pytest
from fastapi.testclient import TestClient

from app.erp_local import (
    fiscal_invoices,
    native_ingest,
    recipient_capture,
    store_labels,
    would,
)
from app.erp_local.api import create_app
from app.erp_local.core import ERPError


@pytest.fixture
def homolog(monkeypatch):
    monkeypatch.setenv("ERP_HOMOLOG", "1")


def test_labels_become_pages_saying_what_would_be_printed(erp, homolog, monkeypatch):
    order = {"status": "READY_TO_SHIP", "items": [{"seller_sku": "mala.p", "quantity": 2}]}
    unit = {"261111ABC": {"261111ABC": order}}
    monkeypatch.setattr(native_ingest, "_units", lambda db, channel, store: unit)
    monkeypatch.setattr(store_labels, "reference", lambda db, c, s, n: "SHP-77")
    monkeypatch.setattr(
        recipient_capture, "for_order", lambda db, c, s, n: {"name": "Comprador", "city": "X"}
    )

    def never(**_kw):
        raise AssertionError("a loja não pode ser chamada na homologação")

    monkeypatch.setattr(store_labels, "host_reader", lambda: never)
    pdf, failures = store_labels.batch(
        erp, [{"channel": "shopee", "store": "loja", "number": "261111ABC"}]
    )
    assert pdf.startswith(b"%PDF") and failures == {}
    with erp.connection() as db:
        done = would.listing(db)
    assert done[0]["action"] == "label" and "envio SHP-77" in done[0]["detail"]["would"]
    assert done[0]["detail"]["items"] == ["2 × mala.p"]


def test_a_label_the_store_has_not_arranged_says_why(erp, homolog, monkeypatch):
    monkeypatch.setattr(native_ingest, "_units", lambda db, channel, store: {})
    monkeypatch.setattr(recipient_capture, "for_order", lambda *a: None)

    def not_ready(*_a):
        raise ERPError("label_reference", "A loja ainda não organizou o envio.", 409)

    monkeypatch.setattr(store_labels, "reference", not_ready)
    store_labels.batch(erp, [{"channel": "ml", "store": "loja", "number": "1"}])
    with erp.connection() as db:
        assert would.listing(db)[0]["detail"]["would"].startswith("Não pediria etiqueta")


def test_outside_homologation_labels_still_go_to_the_store(erp, monkeypatch):
    monkeypatch.delenv("ERP_HOMOLOG", raising=False)
    with pytest.raises(ERPError, match="compradores"):
        store_labels.batch(erp, [{"channel": "ml", "store": "loja", "number": "1"}])


def test_sending_a_note_says_what_would_go_to_sefaz(tmp_path, homolog, monkeypatch):
    plan = {"number": 12, "series": 1, "status": "draft", "would": "Assinaria e enviaria."}
    monkeypatch.setattr(would, "invoice_plan", lambda db, identifier: plan)

    def never(*_a, **_k):
        raise AssertionError("a SEFAZ não pode ser chamada na homologação")

    monkeypatch.setattr(fiscal_invoices, "transmit", never)
    client = TestClient(create_app(tmp_path / "erp.sqlite3"), base_url="http://localhost")
    reply = client.post(
        "/api/erp-local/fiscal/invoices/nota-1/transmit", headers={"X-ERP-Local": "1"}
    )
    assert reply.status_code == 200
    assert reply.json() == {
        "id": "nota-1",
        "status": "draft",
        "would": True,
        "message": "Assinaria e enviaria.",
    }
    state = client.get("/api/erp-local/state").json()
    assert state["would"][0]["target"] == "NF 12/1"


def test_a_sale_the_store_has_not_released_has_no_label_yet(erp, homolog, monkeypatch):
    unit = {"9": {"9": {"status": "UNPAID", "items": []}}}
    monkeypatch.setattr(native_ingest, "_units", lambda db, channel, store: unit)
    monkeypatch.setattr(recipient_capture, "for_order", lambda *a: None)
    store_labels.batch(erp, [{"channel": "shopee", "store": "loja", "number": "9"}])
    with erp.connection() as db:
        text = would.listing(db)[0]["detail"]["would"]
    assert text.startswith("Não pediria etiqueta: a loja ainda não liberou o envio")
    assert "UNPAID" in text


def test_homologation_adopts_a_company_without_technical_and_the_note_omits_it(erp, homolog):
    from app.erp_local import multi_company, nfe_xml

    with erp.connection() as db:
        db.executescript(multi_company.SCHEMA)
    candidate = {
        "cnpj": "11222333000181",
        "name": "Empresa da loja",
        "fantasy": "",
        "ie": "123456789012",
        "crt": "1",
        "address": {
            "street": "Rua A",
            "number": "1",
            "complement": "",
            "district": "Centro",
            "city_code": "3550308",
            "city": "São Paulo",
            "uf": "SP",
            "zip": "01001000",
            "phone": "",
        },
        "series": 2,
        "last_number": 41,
        "notes_30_days": 3,
        "last_note_on": "2026-11-10",
    }
    erp.command("cand", "fiscal.candidates.load", {"rows": [candidate]})
    erp.command("adopt", "fiscal.company.adopt", {"cnpj": "11222333000181"})
    with erp.connection() as db:
        company = db.execute("SELECT data FROM fiscal_companies").fetchone()[0]
    assert '"technical":{}' in company and '"next_number":42' in company
    from erp_local_tests.test_nfe import spec_for

    spec = {**spec_for(), "technical": {}}
    assert "infRespTec" not in nfe_xml.build(spec)["xml"]
    assert "infRespTec" in nfe_xml.build(spec_for())["xml"]


def test_outside_homologation_the_technical_contact_is_still_required(erp, monkeypatch):
    from app.erp_local import multi_company

    monkeypatch.delenv("ERP_HOMOLOG", raising=False)
    with erp.connection() as db:
        db.executescript(multi_company.SCHEMA)
        db.execute(
            "INSERT INTO fiscal_company_candidates VALUES ('11222333000181','{}','2026-11-10')"
        )
    with pytest.raises(ERPError, match="responsável técnico"):
        erp.command("adopt", "fiscal.company.adopt", {"cnpj": "11222333000181"})
