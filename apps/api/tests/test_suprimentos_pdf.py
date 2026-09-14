"""Certificações: anexos PDF, exportação e permissões da tela."""

from urllib.parse import unquote
from uuid import UUID, uuid4

import fitz
import pytest
import pytest_asyncio
from sqlalchemy import delete, inspect, select
from sqlalchemy.orm import undefer

from app.models import FinanceiroSuprimentos, UserRole
from app.routers.financeiro import _SUPRIMENTOS_PDF_MAX_BYTES

BASE = "/api/financeiro/suprimentos"


def _pdf(text="Certificado de teste", *, encrypted=False):
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((40, 40), text)
        if encrypted:
            return document.tobytes(
                encryption=fitz.PDF_ENCRYPT_AES_256,
                owner_pw="owner-test",
                user_pw="reader-test",
            )
        return document.tobytes()


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_certificacoes(db):
    await db.execute(delete(FinanceiroSuprimentos))
    await db.commit()
    yield
    await db.execute(delete(FinanceiroSuprimentos))
    await db.commit()


async def _create(client, make_user, auth_as, **fields):
    auth_as(await make_user(role=UserRole.ADMIN))
    response = await client.post(BASE, json={"produto": "Airfryer", **fields})
    assert response.status_code == 201, response.text
    assert response.json()["tem_pdf"] is False
    assert response.json()["pdf_nome"] is None
    return response.json()["id"]


async def test_attachment_roundtrip_replacement_and_deletion(client, make_user, auth_as, db):
    row_id = await _create(client, make_user, auth_as)
    first = _pdf()
    response = await client.post(
        f"{BASE}/{row_id}/pdf",
        files={"file": ("..\\pasta\\Certificação 2026.PDF", first, "application/pdf")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["tem_pdf"] is True
    assert response.json()["pdf_nome"] == "Certificação 2026.pdf"
    assert "pdf_arquivo" not in response.json()
    downloaded = await client.get(f"{BASE}/{row_id}/pdf")
    assert downloaded.content == first
    assert downloaded.headers["content-type"] == "application/pdf"
    assert unquote(downloaded.headers["content-disposition"]) == (
        "attachment; filename*=UTF-8''Certificação 2026.pdf"
    )

    second = _pdf("Certificado atualizado")
    replaced = await client.post(
        f"{BASE}/{row_id}/pdf",
        files={"file": ("novo.pdf", second, "application/octet-stream")},
    )
    assert replaced.status_code == 200
    assert replaced.json()["pdf_nome"] == "novo.pdf"
    assert (await client.get(f"{BASE}/{row_id}/pdf")).content == second
    patched = await client.patch(f"{BASE}/{row_id}", json={"numero": "ANATEL-2026"})
    assert patched.json()["tem_pdf"] is True
    assert patched.json()["pdf_nome"] == "novo.pdf"
    assert (await client.get(f"{BASE}/{row_id}/pdf")).content == second

    listed = await client.get(BASE)
    assert listed.status_code == 200
    assert listed.json()[0]["tem_pdf"] is True
    assert "pdf_arquivo" not in listed.json()[0]
    stored = (await db.execute(select(FinanceiroSuprimentos))).scalar_one()
    assert "pdf_arquivo" in inspect(stored).unloaded
    assert stored.tem_pdf is True
    await db.close()

    assert (await client.delete(f"{BASE}/{row_id}")).status_code == 204
    assert (await client.get(f"{BASE}/{row_id}/pdf")).status_code == 404
    assert (await db.execute(select(FinanceiroSuprimentos))).scalar_one_or_none() is None


@pytest.mark.parametrize(
    ("filename", "data", "status", "code"),
    [
        ("vazio.pdf", b"", 400, "invalid_pdf"),
        ("texto.pdf", b"isto nao e PDF", 400, "invalid_pdf"),
        ("quebrado.pdf", b"%PDF-1.7\nbroken", 400, "invalid_pdf"),
        ("errado.txt", _pdf(), 400, "invalid_pdf"),
        ("protegido.pdf", _pdf(encrypted=True), 400, "pdf_encrypted"),
        ("grande.pdf", b"x" * (_SUPRIMENTOS_PDF_MAX_BYTES + 1), 413, "pdf_too_large"),
    ],
)
async def test_invalid_upload_preserves_existing_pdf(
    client, make_user, auth_as, filename, data, status, code
):
    row_id = await _create(client, make_user, auth_as)
    original = _pdf()
    assert (await client.post(
        f"{BASE}/{row_id}/pdf",
        files={"file": ("original.pdf", original, "application/pdf")},
    )).status_code == 200
    response = await client.post(
        f"{BASE}/{row_id}/pdf", files={"file": (filename, data, "application/pdf")}
    )
    assert response.status_code == status, response.text
    assert response.json()["detail"]["code"] == code
    assert (await client.get(f"{BASE}/{row_id}/pdf")).content == original
    assert (await client.get(BASE)).json()[0]["pdf_nome"] == "original.pdf"


async def test_missing_row_and_attachment(client, make_user, auth_as):
    row_id = await _create(client, make_user, auth_as)
    response = await client.get(f"{BASE}/{row_id}/pdf")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "suprimentos_pdf_not_found"
    unknown = uuid4()
    response = await client.get(f"{BASE}/{unknown}/pdf")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "suprimentos_not_found"
    response = await client.post(
        f"{BASE}/{unknown}/pdf", files={"file": ("anexo.pdf", _pdf(), "application/pdf")}
    )
    assert response.status_code == 404


async def test_pdf_permissions(client, make_user, auth_as):
    row_id = await _create(client, make_user, auth_as)
    content = _pdf()
    assert (await client.post(
        f"{BASE}/{row_id}/pdf", files={"file": ("cert.pdf", content, "application/pdf")}
    )).status_code == 200
    viewer = await make_user(permissions={"financeiro_suprimentos": {"view": True}})
    auth_as(viewer)
    assert (await client.get(f"{BASE}/{row_id}/pdf")).content == content
    assert (await client.get(f"{BASE}/pdf")).status_code == 200
    assert (await client.post(
        f"{BASE}/{row_id}/pdf", files={"file": ("outro.pdf", _pdf(), "application/pdf")}
    )).status_code == 403
    assert (await client.delete(f"{BASE}/{row_id}")).status_code == 403
    auth_as(await make_user())
    assert (await client.get(f"{BASE}/{row_id}/pdf")).status_code == 403
    assert (await client.get(f"{BASE}/pdf")).status_code == 403
    assert (await client.post(
        f"{BASE}/{row_id}/pdf", files={"file": ("outro.pdf", _pdf(), "application/pdf")}
    )).status_code == 403


async def test_export_uses_current_saved_rows_without_attachment_blobs(
    client, make_user, auth_as, db, monkeypatch
):
    from app.services import certificacoes_pdf

    first = await _create(client, make_user, auth_as, produto="Zebra")
    second = (await client.post(BASE, json={"produto": "Airfryer", "numero": "123"})).json()["id"]
    assert (await client.post(
        f"{BASE}/{first}/pdf", files={"file": ("cert.pdf", _pdf(), "application/pdf")}
    )).status_code == 200
    expected = _pdf("Tabela exportada")

    def generate(rows):
        assert [str(row.id) for row in rows] == [second, first]
        assert rows[0].numero == "123"
        assert rows[1].pdf_nome == "cert.pdf"
        assert all("pdf_arquivo" in inspect(row).unloaded for row in rows)
        return expected

    monkeypatch.setattr(certificacoes_pdf, "montar_pdf", generate)
    response = await client.get(f"{BASE}/pdf")
    assert response.status_code == 200, response.text
    assert response.content == expected
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="certificacoes.pdf"'
    stored = (await db.execute(
        select(FinanceiroSuprimentos)
        .where(FinanceiroSuprimentos.id == UUID(first))
        .options(undefer(FinanceiroSuprimentos.pdf_arquivo))
    )).scalar_one()
    assert stored.pdf_arquivo is not None
