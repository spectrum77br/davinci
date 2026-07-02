import pytest
from sqlalchemy import text as _text

from app.config import get_settings
from app.models import UserRole
from app.security.cipher import decrypt_bytes

# Bytes arbitrários — os endpoints só validam extensão, não o formato PKCS#12.
P12_BYTES = b"\x30\x82\x03\x00fake-pkcs12-payload-\x00\x01\x02\xff"
FILES = {"file": ("empresa.p12", P12_BYTES, "application/x-pkcs12")}


async def _make_company(client) -> str:
    r = await client.post(
        "/api/companies", json={"razao_social": "ACME LTDA", "apelido": "acme"}
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_cert_upload_download_roundtrip(client, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    cid = await _make_company(client)

    r = await client.post(
        f"/api/companies/{cid}/certificates",
        files=FILES,
        data={"password": "senha-secreta", "label": "A1 2026", "expires_at": "2026-12-31"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["filename"] == "empresa.p12"
    assert body["has_password"] is True
    assert body["size_bytes"] == len(P12_BYTES)
    assert body["label"] == "A1 2026"
    assert body["expires_at"] == "2026-12-31"
    assert body["uploaded_by_name"]  # nome/e-mail de quem subiu
    cert_id = body["id"]

    lst = await client.get(f"/api/companies/{cid}/certificates")
    assert lst.status_code == 200
    assert any(c["id"] == cert_id for c in lst.json())

    # download devolve exatamente os bytes originais (round-trip de cifra)
    dl = await client.get(f"/api/companies/{cid}/certificates/{cert_id}/download")
    assert dl.status_code == 200
    assert dl.content == P12_BYTES

    pw = await client.get(f"/api/companies/{cid}/certificates/{cert_id}/password")
    assert pw.status_code == 200
    assert pw.json()["password"] == "senha-secreta"


@pytest.mark.asyncio
async def test_cert_encrypted_at_rest(client, make_user, auth_as, db):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    cid = await _make_company(client)
    r = await client.post(
        f"/api/companies/{cid}/certificates", files=FILES, data={"password": "p@ss"}
    )
    assert r.status_code == 201, r.text
    cert_id = r.json()["id"]

    schema = get_settings().database_schema
    row = (
        await db.execute(
            _text(
                f"SELECT blob, password_enc FROM {schema}.company_certificates "  # noqa: S608
                "WHERE id = CAST(:i AS uuid)"
            ),
            {"i": cert_id},
        )
    ).first()
    blob = bytes(row[0])
    pwd_enc = bytes(row[1])
    # o que está no banco NÃO é o texto claro, e não vaza o conteúdo/senha
    assert blob != P12_BYTES
    assert P12_BYTES not in blob
    assert b"p@ss" not in pwd_enc
    # e decifra de volta pro original
    assert decrypt_bytes(blob) == P12_BYTES
    assert decrypt_bytes(pwd_enc).decode() == "p@ss"


@pytest.mark.asyncio
async def test_cert_admin_only(client, make_user, auth_as):
    # cria a empresa como admin
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    cid = await _make_company(client)

    # usuário COM todas as permissões de empresa ainda é barrado (admin_only)
    user = await make_user(
        role=UserRole.USER,
        permissions={"empresa": {"view": True, "edit": True, "delete": True}},
    )
    auth_as(user)
    r = await client.get(f"/api/companies/{cid}/certificates")
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "admin_only"

    up = await client.post(f"/api/companies/{cid}/certificates", files=FILES)
    assert up.status_code == 403


@pytest.mark.asyncio
async def test_cert_rejects_bad_extension(client, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    cid = await _make_company(client)
    r = await client.post(
        f"/api/companies/{cid}/certificates",
        files={"file": ("nota.pdf", b"%PDF-1.4 nope", "application/pdf")},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "unsupported_file_type"


@pytest.mark.asyncio
async def test_cert_upload_without_password_and_delete(client, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    cid = await _make_company(client)

    r = await client.post(f"/api/companies/{cid}/certificates", files=FILES)
    assert r.status_code == 201, r.text
    assert r.json()["has_password"] is False
    cert_id = r.json()["id"]

    pw = await client.get(f"/api/companies/{cid}/certificates/{cert_id}/password")
    assert pw.status_code == 200
    assert pw.json()["password"] is None

    d = await client.delete(f"/api/companies/{cid}/certificates/{cert_id}")
    assert d.status_code == 204
    lst = await client.get(f"/api/companies/{cid}/certificates")
    assert lst.json() == []


@pytest.mark.asyncio
async def test_cert_patch_metadata_and_password(client, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    cid = await _make_company(client)
    r = await client.post(f"/api/companies/{cid}/certificates", files=FILES)
    cert_id = r.json()["id"]
    assert r.json()["has_password"] is False

    # adiciona senha + rótulo via PATCH
    p = await client.patch(
        f"/api/companies/{cid}/certificates/{cert_id}",
        json={"label": "A1 novo", "password": "abc123"},
    )
    assert p.status_code == 200, p.text
    assert p.json()["label"] == "A1 novo"
    assert p.json()["has_password"] is True

    pw = await client.get(f"/api/companies/{cid}/certificates/{cert_id}/password")
    assert pw.json()["password"] == "abc123"

    # remove a senha mandando string vazia
    p2 = await client.patch(
        f"/api/companies/{cid}/certificates/{cert_id}", json={"password": ""}
    )
    assert p2.status_code == 200
    assert p2.json()["has_password"] is False


@pytest.mark.asyncio
async def test_cert_cascade_on_company_delete(client, make_user, auth_as, db):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    cid = await _make_company(client)
    r = await client.post(f"/api/companies/{cid}/certificates", files=FILES)
    assert r.status_code == 201, r.text

    d = await client.delete(f"/api/companies/{cid}")
    assert d.status_code == 204

    schema = get_settings().database_schema
    cnt = (
        await db.execute(
            _text(
                f"SELECT count(*) FROM {schema}.company_certificates "  # noqa: S608
                "WHERE company_id = CAST(:i AS uuid)"
            ),
            {"i": cid},
        )
    ).scalar_one()
    assert cnt == 0
