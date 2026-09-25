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

    # download devolve exatamente os bytes originais (round-trip de cifra) —
    # com senha, só com a senha certa (a senha é a trava do certificado).
    url_dl = f"/api/companies/{cid}/certificates/{cert_id}/download"
    dl = await client.post(url_dl, json={"password": "senha-secreta"})
    assert dl.status_code == 200
    assert dl.content == P12_BYTES

    # Não existe mais rota que mostre a senha guardada (furava a trava).
    pw = await client.get(f"/api/companies/{cid}/certificates/{cert_id}/password")
    assert pw.status_code in (404, 405)


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

    # Sem senha: baixa direto, sem pedir nada.
    dl = await client.post(f"/api/companies/{cid}/certificates/{cert_id}/download")
    assert dl.status_code == 200
    assert dl.content == P12_BYTES

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

    url = f"/api/companies/{cid}/certificates/{cert_id}"
    # Trocar ou excluir a senha de um certificado travado exige a atual.
    sem_atual = await client.patch(url, json={"password": "outra"})
    assert sem_atual.status_code == 403
    assert sem_atual.json()["detail"]["code"] == "senha_obrigatoria"
    errada = await client.patch(url, json={"password": "", "current_password": "nao-e"})
    assert errada.status_code == 403
    assert errada.json()["detail"]["code"] == "senha_incorreta"
    # Mexer só no rótulo não pede senha.
    rot = await client.patch(url, json={"label": "A1 renovado"})
    assert rot.status_code == 200
    assert rot.json()["has_password"] is True

    troca = await client.patch(url, json={"password": "xyz789", "current_password": "abc123"})
    assert troca.status_code == 200
    dl_velha = await client.post(f"{url}/download", json={"password": "abc123"})
    assert dl_velha.status_code == 403
    dl_nova = await client.post(f"{url}/download", json={"password": "xyz789"})
    assert dl_nova.status_code == 200

    # remove a senha mandando string vazia + a senha atual
    p2 = await client.patch(url, json={"password": "", "current_password": " xyz789 "})
    assert p2.status_code == 200
    assert p2.json()["has_password"] is False
    # sem senha: baixa direto
    assert (await client.post(f"{url}/download")).status_code == 200


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


class _RedisDeMentira:
    """Contagem de erros em memória: o teste não escreve no Redis de verdade."""

    def __init__(self):
        self.d: dict[str, int] = {}

    async def incr(self, k):
        self.d[k] = self.d.get(k, 0) + 1
        return self.d[k]

    async def expire(self, k, s, nx=False):
        return True

    async def delete(self, k):
        self.d.pop(k, None)


@pytest.fixture(autouse=True)
def _redis_falso(monkeypatch):
    from app.routers import company_certificates as rota

    falso = _RedisDeMentira()

    async def _fake():
        return falso

    monkeypatch.setattr(rota, "_redis", _fake)
    return falso


@pytest.mark.asyncio
async def test_cert_download_trava_com_senha(client, make_user, auth_as, _redis_falso):
    """Eduardo, 25/09/2026: "a senha quando colocarmos dentro é para travar e
    não deixarem baixar". Sem senha ou com a errada, o arquivo não sai; 5 erros
    seguidos travam aquele certificado por 15 min (mesmo com a senha certa)."""
    falso = _redis_falso

    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    cid = await _make_company(client)
    r = await client.post(
        f"/api/companies/{cid}/certificates", files=FILES, data={"password": "certa"}
    )
    url = f"/api/companies/{cid}/certificates/{r.json()['id']}/download"

    sem = await client.post(url)
    assert sem.status_code == 403
    assert sem.json()["detail"]["code"] == "senha_obrigatoria"
    assert P12_BYTES not in sem.content

    errada = await client.post(url, json={"password": "errada"})
    assert errada.status_code == 403
    assert errada.json()["detail"]["code"] == "senha_incorreta"
    assert P12_BYTES not in errada.content

    # Acertar zera a contagem.
    assert (await client.post(url, json={"password": "certa"})).status_code == 200
    assert falso.d == {}

    for _ in range(5):
        assert (await client.post(url, json={"password": "x"})).status_code == 403
    travado = await client.post(url, json={"password": "certa"})
    assert travado.status_code == 429
    assert travado.json()["detail"]["code"] == "muitas_tentativas"

    # A antiga rota GET de download não existe mais.
    assert (await client.get(url)).status_code in (404, 405)



@pytest.mark.asyncio
async def test_cert_mesmo_arquivo_de_novo_e_recusado(client, make_user, auth_as):
    """25/09/2026: subiam o MESMO arquivo de novo só para pôr a data. Agora o
    repetido é recusado (409) e a data se põe no PATCH do que já existe."""
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    cid = await _make_company(client)
    r1 = await client.post(f"/api/companies/{cid}/certificates", files=FILES)
    assert r1.status_code == 201, r1.text

    outro_nome = {"file": ("empresa - venc 01-07-2027.pfx", P12_BYTES, "application/x-pkcs12")}
    r2 = await client.post(
        f"/api/companies/{cid}/certificates", files=outro_nome, data={"expires_at": "2027-07-01"}
    )
    assert r2.status_code == 409
    assert r2.json()["detail"]["code"] == "certificado_repetido"
    assert r2.json()["detail"]["filename"] == "empresa.p12"
    assert len((await client.get(f"/api/companies/{cid}/certificates")).json()) == 1

    # A data vai no que já existe.
    p = await client.patch(
        f"/api/companies/{cid}/certificates/{r1.json()['id']}", json={"expires_at": "2027-07-01"}
    )
    assert p.status_code == 200
    assert p.json()["expires_at"] == "2027-07-01"

    # Arquivo diferente (renovação) entra normal; e o mesmo arquivo em OUTRA empresa também.
    novo = {"file": ("empresa-2027.pfx", P12_BYTES + b"renovado", "application/x-pkcs12")}
    assert (await client.post(f"/api/companies/{cid}/certificates", files=novo)).status_code == 201
    outra = await client.post("/api/companies", json={"razao_social": "OUTRA LTDA", "apelido": "outra"})
    assert (
        await client.post(f"/api/companies/{outra.json()['id']}/certificates", files=FILES)
    ).status_code == 201
