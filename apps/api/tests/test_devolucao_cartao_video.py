# ruff: noqa: E501
"""Cartão do vídeo da expedição (services/devolucao_cartao_video +
chamados_devolucao.disparar): sem NENHUMA foto e com o link do vídeo, o disparo
gera a imagem com QR code + link + etiqueta e manda como a foto — Shopee/TikTok/ML
exigem imagem e não aceitam vídeo (Vinicius 18/09, 289545)."""

from __future__ import annotations

import pymupdf
import pytest
from sqlalchemy import select

from app.models import Chamado, DevolucaoAnexo, DevolucaoRastreio, NfEtiquetaArquivo
from app.services import chamados_devolucao as svc
from app.services import devolucao_cartao_video as cartao
from tests.test_chamados_devolucao import PNG_1PX, _FakeShopee, _perms, _seed_pedido

LINK = "https://drive.google.com/file/d/1ZCFHQmB3L7McSME7_fJSL2fhdkUTqicz/view?usp=drive_link"


@pytest.fixture
def inline(monkeypatch):
    monkeypatch.setattr(svc, "ENFILEIRAR", False)  # dispara inline (sem Redis)


def _etiqueta_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=442)
    page.insert_text((20, 40), "DESTINATARIO Fulano - Pedido 260808RXSGH0JK", fontsize=10)
    page.draw_rect(pymupdf.Rect(20, 60, 120, 160), color=(0, 0, 0), fill=(0, 0, 0))
    return doc.tobytes()


def test_gerar_cartao_e_png_com_e_sem_etiqueta():
    sem = cartao.gerar_cartao_video(LINK, pedido="260808RXSGH0JK", produto="Apple Watch SE 2", sku="i200.sa")
    com = cartao.gerar_cartao_video(
        LINK, pedido="260808RXSGH0JK", produto="Apple Watch SE 2", sku="i200.sa", etiqueta_pdf=_etiqueta_pdf()
    )
    for png in (sem, com):
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        assert len(png) < svc.SHOPEE_FOTO_MAX_BYTES  # sobe na Shopee sem reduzir
    pix_sem = pymupdf.Pixmap(sem)
    pix_com = pymupdf.Pixmap(com)
    assert pix_sem.width == pix_com.width == 1200
    assert pix_com.height > pix_sem.height  # a etiqueta ocupa a coluna da esquerda
    # etiqueta ilegível não derruba o cartão
    quebrado = cartao.gerar_cartao_video(LINK, pedido="x", produto=None, sku=None, etiqueta_pdf=b"nao e pdf")
    assert quebrado[:8] == b"\x89PNG\r\n\x1a\n"


async def test_shopee_sem_foto_com_video_manda_cartao(client, make_user, auth_as, db, inline, monkeypatch):
    user = await make_user(permissions=_perms())
    auth_as(user)
    fake = _FakeShopee()

    async def _c(session, *a):
        return fake

    monkeypatch.setattr(svc, "_shopee_client_para", _c)
    await _seed_pedido(db, user, numero="289545", numeroloja="260808RXSGH0JK",
                       platform="shopee", conta="vortan", loja="91")
    db.add(DevolucaoRastreio(pedido_bling="289545", devolucao_id_auto="26091605SYB6F9P",
                             fonte_auto="shopee", video_link=LINK))
    db.add(NfEtiquetaArquivo(pedido_bling="289545", filename="etiqueta.pdf",
                             content_type="application/pdf", size_bytes=1, blob=_etiqueta_pdf()))
    await db.commit()
    r = await client.post(
        "/api/devolutions",
        json={"conta": "vortan", "pedido_bling": "289545", "pedido_marketplace": "260808RXSGH0JK",
              "sku": "i200.sa", "produtos": "Apple Watch SE 2 GPS 44mm - Preto",
              "condicao_produto": "Não devolvido", "motivo_devolucao": "Golpe"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["chamado_ml_status"] == "enviada", r.json()
    # a disputa saiu com o cartão como imagem do módulo obrigatório
    assert len(fake.disputes) == 1
    d = fake.disputes[0]
    assert d["image_list"] == [{"module_index": 1, "requirement": "Photos",
                                "image_url": ["https://fileproxy/video-expedicao.png"]}]
    assert "QR code" in d["text"] and LINK in d["text"]
    assert "foto(s) em anexo" not in d["text"]  # o cartão não conta como foto
    # o cartão ficou anexado na linha, sem autor (gerado pelo sistema)
    anexos = (await db.execute(select(DevolucaoAnexo))).scalars().all()
    assert [a.filename for a in anexos] == [cartao.CARTAO_VIDEO_NOME]
    assert anexos[0].created_by is None and anexos[0].content_type == "image/png"
    assert anexos[0].ml_file_name == "https://fileproxy/video-expedicao.png"
    assert anexos[0].blob[:8] == b"\x89PNG\r\n\x1a\n"
    # a linha lista o cartão como anexo
    lin = (await client.get("/api/devolutions", params={"search": "289545"})).json()
    item = next(i for i in lin["items"] if i["pedido_bling"] == "289545")
    assert [a["filename"] for a in item["anexos"]] == [cartao.CARTAO_VIDEO_NOME]


async def test_cartao_reusado_e_trocado_quando_o_link_muda(client, make_user, auth_as, db, inline, monkeypatch):
    user = await make_user(permissions=_perms())
    auth_as(user)
    fake = _FakeShopee(status="")  # aguardando pacote → fica pendente, mas o cartão já é gerado

    async def _c(session, *a):
        return fake

    monkeypatch.setattr(svc, "_shopee_client_para", _c)
    await _seed_pedido(db, user, numero="289546", numeroloja="260808RXSGH0JL",
                       platform="shopee", conta="vortan", loja="91")
    db.add(DevolucaoRastreio(pedido_bling="289546", devolucao_id_auto="26091605SYB6F9Q",
                             fonte_auto="shopee", video_link=LINK))
    await db.commit()
    r = await client.post(
        "/api/devolutions",
        json={"conta": "vortan", "pedido_bling": "289546", "pedido_marketplace": "260808RXSGH0JL",
              "condicao_produto": "Não devolvido", "motivo_devolucao": "Golpe"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["chamado_ml_erro"] == "shopee_aguardando_pacote"
    dev_id = r.json()["id"]

    async def _anexos():
        return (
            await db.execute(
                select(DevolucaoAnexo)
                .order_by(DevolucaoAnexo.created_at)
                .execution_options(populate_existing=True)
            )
        ).scalars().all()

    primeiro = await _anexos()
    assert len(primeiro) == 1
    id_cartao = primeiro[0].id
    async def _disparar():
        ch = (await db.execute(select(Chamado).where(Chamado.pedido_bling == "289546"))).scalar_one()
        dev = await db.get(svc.Devolution, dev_id)
        await svc.disparar(db, ch, dev)
        await db.commit()

    # retentativa (cron) com o mesmo link: mesmo cartão, não gera outro
    await _disparar()
    assert [a.id for a in await _anexos()] == [id_cartao]
    # link trocado (operador refez o vídeo): o cartão antigo sai, entra o novo
    rastreio = await db.get(DevolucaoRastreio, "289546")
    rastreio.video_link = "https://drive.google.com/file/d/NOVO/view"
    await db.commit()
    await _disparar()
    depois = await _anexos()
    assert len(depois) == 1 and depois[0].id != id_cartao
    # foto de verdade anexada: o cartão continua, mas o texto conta só a foto
    fake.status = "ACCEPTED"
    fake.get_return_detail = _FakeShopee(status="ACCEPTED").get_return_detail
    up = await client.post(f"/api/devolutions/{dev_id}/anexos",
                           files={"file": ("pacote.png", PNG_1PX, "image/png")})
    assert up.status_code == 201, up.text
    assert len(fake.disputes) == 1
    d = fake.disputes[0]
    assert "Seguem 1 foto(s) em anexo" in d["text"]
    assert "QR code" not in d["text"]
    urls = d["image_list"][0]["image_url"]
    assert set(urls) == {"https://fileproxy/video-expedicao.png", "https://fileproxy/pacote.png"}


async def test_sem_video_e_sem_foto_continua_pendente(client, make_user, auth_as, db, inline, monkeypatch):
    user = await make_user(permissions=_perms())
    auth_as(user)
    fake = _FakeShopee()

    async def _c(session, *a):
        return fake

    monkeypatch.setattr(svc, "_shopee_client_para", _c)
    await _seed_pedido(db, user, numero="289547", numeroloja="260808RXSGH0JM",
                       platform="shopee", conta="vortan", loja="91")
    db.add(DevolucaoRastreio(pedido_bling="289547", devolucao_id_auto="26091605SYB6F9R", fonte_auto="shopee"))
    await db.commit()
    r = await client.post(
        "/api/devolutions",
        json={"conta": "vortan", "pedido_bling": "289547", "pedido_marketplace": "260808RXSGH0JM",
              "condicao_produto": "Não devolvido", "motivo_devolucao": "Golpe"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["chamado_ml_erro"] == "devolucao_sem_foto"
    assert fake.disputes == []
    assert (await db.execute(select(DevolucaoAnexo))).scalars().all() == []
