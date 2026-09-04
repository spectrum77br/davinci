# ruff: noqa: E501
"""Encaminhar chamado ao JURÍDICO (services/chamados_juridico): aviso no Threema
com link do dossiê (histórico + fotos), carimbo no chamado, aba Jurídico."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import ChamadoMensagem, ThreemaInformarConfig
from app.services import threema

pytestmark = pytest.mark.asyncio

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360f8cfc00000030101000ec8f0d50000000049454e44ae426082"
)


def _perms() -> dict:
    return {"chamados": {"view": True, "edit": True, "delete": True}}


@pytest.fixture
def threema_fake(monkeypatch):
    enviados: list[tuple[str, list[str]]] = []

    async def _send_to_all(self, text, recipients=None):
        enviados.append((text, list(recipients or [])))
        return {"sent": list(recipients or []), "failed": []}

    monkeypatch.setattr(threema.ThreemaClient, "send_to_all", _send_to_all)
    return enviados


async def _criar_chamado(client, *, com_foto=True) -> dict:
    r = await client.post(
        "/api/chamados",
        json={"origem": "logistica", "pedido_bling": "293000", "pedido_marketplace": "2000011",
              "plataforma": "ml", "conta": "aguiar", "produto": "Mala M2", "sku": "b003.18",
              "canal": "manual", "chamado": "479700000", "observacao": "cliente diz que não recebeu"},
    )
    assert r.status_code == 201, r.text
    ch = r.json()
    files = {"files": ("prova.png", PNG_1PX, "image/png")} if com_foto else None
    rep = await client.post(f"/api/chamados/{ch['id']}/mensagens", data={"texto": "Enviamos o produto, segue comprovante"}, files=files)
    assert rep.status_code == 201, rep.text
    return ch


async def test_encaminhar_exige_destinatarios(client, make_user, auth_as, db, threema_fake):
    user = await make_user(permissions=_perms())
    auth_as(user)
    ch = await _criar_chamado(client)
    r = await client.post(f"/api/chamados/{ch['id']}/juridico", json={"observacao": "x"})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "sem_destinatarios"
    assert threema_fake == []


async def test_encaminhar_manda_threema_com_link_e_dossie_publico(client, make_user, auth_as, db, threema_fake):
    user = await make_user(permissions=_perms())
    auth_as(user)
    db.add(ThreemaInformarConfig(contexto="juridico", recipients="ABCDEFGH,IJKLMNOP"))
    await db.commit()
    ch = await _criar_chamado(client)

    r = await client.post(f"/api/chamados/{ch['id']}/juridico", json={"observacao": "cliente ameaça processar"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["sent"] == ["ABCDEFGH", "IJKLMNOP"] and out["failed"] == []
    assert out["link"].endswith("/api/chamados/juridico/dossie/" + out["link"].rsplit("/", 1)[1])
    c = out["chamado"]
    assert c["juridico_enviado_at"] and c["juridico_link"] == out["link"]
    assert c["juridico_obs"] == "cliente ameaça processar"
    assert c["juridico_enviados"] == ["ABCDEFGH", "IJKLMNOP"]
    assert c["juridico_enviado_por_nome"] in (user.name, user.email)
    # texto do Threema
    assert len(threema_fake) == 1
    texto, dest = threema_fake[0]
    assert dest == ["ABCDEFGH", "IJKLMNOP"]
    assert "JURÍDICO" in texto and "479700000" in texto and "293000" in texto
    assert "cliente ameaça processar" in texto and out["link"] in texto
    assert "1 fotos" in texto
    # histórico ganhou evento de sistema
    msgs = (await db.execute(select(ChamadoMensagem).where(ChamadoMensagem.chamado_id == ch["id"]))).scalars().all()
    assert any(m.direcao == "sistema" and "Encaminhado ao jurídico" in m.texto for m in msgs)

    # dossiê público (sem login): cabeçalho, mensagem e foto
    auth_as(None)
    token = out["link"].rsplit("/", 1)[1]
    d = await client.get(f"/api/chamados/juridico/dossie/{token}")
    assert d.status_code == 200, d.text
    html = d.text
    assert "Dossiê jurídico" in html and "479700000" in html and "Enviamos o produto" in html
    assert "cliente ameaça processar" in html
    assert "/anexo/c/" in html
    anexo_id = html.split("/anexo/c/")[1].split('"')[0]
    a = await client.get(f"/api/chamados/juridico/dossie/{token}/anexo/c/{anexo_id}")
    assert a.status_code == 200 and a.content == PNG_1PX
    # token errado → 404
    assert (await client.get("/api/chamados/juridico/dossie/tokeninvalido12345678")).status_code == 404

    # aba Jurídico lista o chamado (mesmo resolvido)
    auth_as(user)
    lst = await client.get("/api/chamados?juridico=true")
    assert lst.status_code == 200
    ids = [x["id"] for x in lst.json()["items"]]
    assert ch["id"] in ids
    # reenvio funciona e mantém o mesmo link
    r2 = await client.post(f"/api/chamados/{ch['id']}/juridico", json={})
    assert r2.status_code == 200 and r2.json()["link"] == out["link"]
    assert len(threema_fake) == 2


async def test_informar_config_juridico_admin(client, make_user, auth_as, db):
    from app.models import UserRole

    admin = await make_user(permissions=_perms(), role=UserRole.ADMIN)
    auth_as(admin)
    r = await client.get("/api/informar/juridico")
    assert r.status_code == 200, r.text
    assert r.json()["contexto"] == "juridico"
    # envio manual pelo Informar NÃO existe pro jurídico (sai pelo chamado)
    assert (await client.post("/api/informar/juridico/enviar")).status_code == 404
