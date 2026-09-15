"""Assinaturas por canal: persistência, isolamento, prévia e contrato para o robô."""

import base64
import uuid

import pytest
from sqlalchemy import func, select

from app.models import Marca, MarcaEmailAssinatura, MarcaEmailPadrao, UserRole
from app.services.email_marca import WHATSAPP_ICON

pytestmark = pytest.mark.asyncio


async def setup(db, make_user, auth_as, *, edit=True):
    user = await make_user(permissions={"email_padroes": {"view": True, "edit": edit}})
    auth_as(user)
    marca = Marca(
        nome="Marca Teste",
        slug="marca-teste",
        sac_email="sac@example.com",
        sac_fone="11912345678",
        site="https://example.com",
        senha_enc="segredo-registro",
        sac_senha_enc="segredo-sac",
    )
    db.add(marca)
    await db.commit()
    await db.refresh(marca)
    return marca


async def test_save_por_canal_sem_alterar_mensagem_ou_marca(client, db, make_user, auth_as):
    m = await setup(db, make_user, auth_as)
    legado = MarcaEmailPadrao(
        marca_id=m.id, contexto="sac", nome="Existente", assunto="Pedido", corpo="Texto do robô"
    )
    db.add(legado)
    await db.commit()
    url = f"/api/email-assinaturas/{m.id}"
    sac = await client.put(f"{url}/sac", json={"texto": "Equipe SAC"})
    assert sac.status_code == 200
    ml = await client.put(f"{url}/ml", json={"texto": "Equipe ML", "incluir_logo": False})
    assert ml.status_code == 200
    edited = await client.put(f"{url}/sac", json={"texto": "Novo rodapé"})
    assert edited.json()["id"] == sac.json()["id"]
    assert await db.scalar(select(func.count()).select_from(MarcaEmailAssinatura)) == 2
    await db.refresh(legado)
    await db.refresh(m)
    assert legado.corpo == "Texto do robô" and legado.assunto == "Pedido"
    assert m.senha_enc == "segredo-registro" and m.sac_senha_enc == "segredo-sac"
    grid = (await client.get("/api/email-assinaturas/grid")).json()
    assert grid["rows"][0]["cells"]["sac"]["texto"] == "Novo rodapé"
    assert grid["rows"][0]["cells"]["ml"]["texto"] == "Equipe ML"
    assert grid["rows"][0]["cells"]["shopee"] is None
    assert "senha" not in str(grid)


async def test_preview_escapa_texto_sem_gravar_ou_enviar(
    client, db, make_user, auth_as, monkeypatch
):
    m = await setup(db, make_user, auth_as)

    def proibido():
        raise AssertionError("cadastro de assinatura não deve enviar e-mail")

    monkeypatch.setattr("app.services.email.get_email_sender", proibido)
    r = await client.post(
        "/api/email-assinaturas/preview",
        json={
            "marca_id": str(m.id),
            "texto": "Atenciosamente,\n<script>alert(1)</script>",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert "&lt;script&gt;" in data["html"] and "<script>" not in data["html"]
    assert "<br>" in data["html"]
    assert 'width="28" height="28"' in data["html"]
    assert "data:image/png;base64," in data["html"]
    assert "cid:" not in data["html"]
    assert "Recebemos sua mensagem" not in data["text"]
    assert "assunto" not in data and "corpo" not in data
    assert await db.scalar(select(func.count()).select_from(MarcaEmailAssinatura)) == 0


async def test_render_entrega_footer_com_imagens_e_respeita_inativo(client, db, make_user, auth_as):
    m = await setup(db, make_user, auth_as)
    m.logo = WHATSAPP_ICON.read_bytes()
    m.logo_mime = "image/png"
    await db.commit()
    url = f"/api/email-assinaturas/{m.id}/sac"
    await client.put(url, json={"texto": "Equipe SAC"})
    r = await client.get(f"{url}/render")
    assert r.status_code == 200
    data = r.json()
    assert data["ativo"] is True
    assert 'src="cid:assinatura-logo"' in data["html"]
    assert 'src="cid:assinatura-whatsapp"' in data["html"]
    assert "data:image" not in data["html"]
    assert len(data["inline_images"]) == 2
    for img in data["inline_images"]:
        assert img["mime"] == "image/png"
        assert base64.b64decode(img["base64"]) == WHATSAPP_ICON.read_bytes()
    assert (await client.get(f"/api/email-assinaturas/{m.id}/ml/render")).status_code == 404
    await client.put(url, json={"ativo": False})
    disabled = (await client.get(f"{url}/render")).json()
    assert disabled == {"ativo": False, "html": "", "text": "", "avisos": [], "inline_images": []}


async def test_permissoes_e_campos_limitados(client, db, make_user, auth_as):
    m = await setup(db, make_user, auth_as, edit=False)
    url = f"/api/email-assinaturas/{m.id}/sac"
    assert (await client.get("/api/email-assinaturas/grid")).status_code == 200
    assert (await client.put(url, json={"texto": "Não permitido"})).status_code == 403
    user = await make_user(role=UserRole.ADMIN)
    auth_as(user)
    for field in ("assunto", "corpo", "senha", "sac_senha"):
        assert (await client.put(url, json={field: "não permitido"})).status_code == 422
    assert (await client.put(url, json={"texto": "x" * 4001})).status_code == 422
    assert (await client.put(f"/api/email-assinaturas/{m.id}/invalido", json={})).status_code == 422
    assert (
        await client.put(f"/api/email-assinaturas/{uuid.uuid4()}/sac", json={})
    ).status_code == 404
    auth_as(await make_user())
    assert (await client.get("/api/email-assinaturas/grid")).status_code == 403
    assert (
        await client.post("/api/email-assinaturas/preview", json={"marca_id": str(m.id)})
    ).status_code == 403


async def test_render_sem_campos_omitidos(client, db, make_user, auth_as):
    m = await setup(db, make_user, auth_as)
    r = await client.post(
        "/api/email-assinaturas/preview",
        json={
            "marca_id": str(m.id),
            "texto": "Só minha assinatura",
            "incluir_logo": False,
            "incluir_dados_marca": False,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["text"] == "Só minha assinatura" and data["avisos"] == []
    assert "WhatsApp" not in data["html"] and "example.com" not in data["html"]
