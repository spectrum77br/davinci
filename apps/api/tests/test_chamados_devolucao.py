# ruff: noqa: E501
"""Devolução → chamado AUTOMÁTICO na plataforma com fotos (services/
chamados_devolucao). Eduardo 04/09: "Todos esses motivos aí, se for adicionado
lá, vai abrir o chamado automático … vai ter foto sim e vídeo"."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.models import (
    BlingOrder,
    Chamado,
    ChamadoAnexo,
    ChamadoMensagem,
    DevolucaoAnexo,
    Devolution,
    StoreInfo,
)
from app.services import chamados_devolucao as svc

pytestmark = pytest.mark.asyncio

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360f8cfc00000030101000ec8f0d50000000049454e44ae426082"
)


def _perms() -> dict:
    return {
        "devolucoes": {"view": True, "edit": True, "delete": True},
        "chamados": {"view": True, "edit": True, "delete": True},
    }


async def _seed_pedido(db, user, *, numero: str, numeroloja: str, platform: str = "ml",
                       conta: str = "aguiar", loja: str = "55") -> None:
    if (
        await db.execute(select(StoreInfo).where(StoreInfo.bling_store_id == loja))
    ).scalar_one_or_none() is None:
        db.add(
            StoreInfo(user_id=user.id, platform=platform, account_name=conta, bling_store_id=loja)
        )
    db.add(
        BlingOrder(
            id=uuid4(),
            numero=numero,
            numeroloja=numeroloja,
            bling_id=123456,
            situacao="83957",
            loja=loja,
            data=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
            item_index=0,
            item_codigo="b001.26",
            item_descricao="Mala Listrada tamanho 26",
            item_quantidade=1,
        )
    )
    await db.commit()


class _FakeML:
    """API do ML só com o que o fluxo usa. `acao` controla se o ML já liberou
    `return_review_fail` pro vendedor."""

    def __init__(self, *, acao: bool = True, claims: tuple[str, ...] = ("777",)):
        self.acao = acao
        self.claims = claims
        self.uploads: list[tuple[str, str, int, str]] = []
        self.reviews: list[tuple[str, str, str, list[str] | None]] = []

    async def get_order(self, order_id):
        return {"id": order_id, "mediations": [{"id": c} for c in self.claims]}

    async def get_claim(self, claim_id):
        actions = ["return_review_ok", "return_review_fail"] if self.acao else ["return_review_ok"]
        return {
            "id": claim_id,
            "status": "opened",
            "players": [
                {"role": "respondent", "type": "seller",
                 "available_actions": [{"action": a} for a in actions]},
                {"role": "complainant", "type": "buyer", "available_actions": []},
            ],
        }

    async def get_claim_returns(self, claim_id):
        return {"id": f"ret-{claim_id}", "status": "delivered",
                "shipments": [{"shipment_id": "s1", "status": "delivered"}]}

    async def upload_return_attachment(self, claim_id, filename, content, content_type="image/jpeg"):  # noqa: E501
        self.uploads.append((str(claim_id), filename, len(content), content_type))
        return f"ml_{len(self.uploads)}_{filename}"

    async def return_review_fail(self, return_id, reason, message, *, attachments=None):
        self.reviews.append((str(return_id), reason, message, attachments))
        return {"id": return_id, "status": "closed"}


@pytest.fixture
def ml(monkeypatch):
    fake = _FakeML()

    async def _client(session, conta):
        return fake

    monkeypatch.setattr(svc.chamados_svc, "_ml_client_para", _client)
    monkeypatch.setattr(svc, "ENFILEIRAR", False)  # dispara inline (sem Redis)
    return fake


async def _abertura(db, chamado_id) -> ChamadoMensagem | None:
    return (
        await db.execute(
            select(ChamadoMensagem)
            .where(ChamadoMensagem.chamado_id == chamado_id, ChamadoMensagem.tipo == "abertura")
            .order_by(ChamadoMensagem.created_at.desc())
        )
    ).scalars().first()


async def test_danificado_espera_foto_e_abre_ao_anexar(client, make_user, auth_as, db, ml):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero="293100", numeroloja="2000099")

    r = await client.post(
        "/api/devolutions",
        json={"conta": "aguiar", "pedido_bling": "293100", "pedido_marketplace": "2000099",
              "sku": "b001.26", "produtos": "Mala Listrada tamanho 26",
              "condicao_produto": "Usado", "motivo_devolucao": "Danificado (Outros)"},
    )
    # mala + motivo de chamado → Link de envio obrigatório (trava do Eduardo)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "link_envio_obrigatorio"
    r = await client.post(
        "/api/devolutions",
        json={"conta": "aguiar", "pedido_bling": "293100", "pedido_marketplace": "2000099",
              "sku": "b001.26", "produtos": "Mala Listrada tamanho 26",
              "condicao_produto": "Usado", "motivo_devolucao": "Danificado (Outros)",
              "link_envio": "https://drive.google.com/expedicao"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["link_envio"] == "https://drive.google.com/expedicao"
    # chamado registrado na aba, canal api, abertura PENDENTE esperando foto
    assert body["tem_chamado"] is True
    assert body["chamado_ml_status"] == "pendente"
    assert body["chamado_ml_erro"] == "devolucao_sem_foto"
    assert ml.reviews == []
    ch = (await db.execute(select(Chamado).where(Chamado.pedido_bling == "293100"))).scalar_one()
    assert ch.origem == "devolucao" and ch.canal == "api" and ch.plataforma == "ml"

    # anexa a foto → abre no ML na hora (SRF2 com o anexo subido)
    dev_id = body["id"]
    up = await client.post(
        f"/api/devolutions/{dev_id}/anexos",
        files={"file": ("mala.png", PNG_1PX, "image/png")},
    )
    assert up.status_code == 201, up.text
    out = up.json()
    assert out["chamado_ml_status"] == "enviada"
    assert out["chamado_ml_erro"] is None
    assert [a["filename"] for a in out["anexos"]] == ["mala.png"]
    assert out["anexos"][0]["ml_file_name"] == "ml_1_mala.png"
    assert ml.uploads == [("777", "mala.png", len(PNG_1PX), "image/png")]
    assert len(ml.reviews) == 1
    ret_id, reason, texto, anexos = ml.reviews[0]
    assert (ret_id, reason, anexos) == ("ret-777", "SRF2", ["ml_1_mala.png"])
    assert "danificado" in texto and "2000099" in texto and "b001.26" in texto
    assert "Comprovante da expedição (fotos/vídeo do envio): https://drive.google.com/expedicao" in texto
    await db.refresh(ch)
    assert ch.chamado == "777" and ch.monitoramento is True and ch.canal == "api"
    msg = await _abertura(db, ch.id)
    assert msg.status == "enviada" and msg.enviada_at is not None and msg.canal == "api"
    # foto copiada pro histórico do chamado
    fotos = (
        await db.execute(select(ChamadoAnexo).where(ChamadoAnexo.mensagem_id == msg.id))
    ).scalars().all()
    assert [f.filename for f in fotos] == ["mala.png"]
    # anexo servido pelo endpoint
    g = await client.get(f"/api/devolutions/anexos/{out['anexos'][0]['id']}")
    assert g.status_code == 200 and g.content == PNG_1PX

    # anexar outra foto depois NÃO reabre nem reenvia
    up2 = await client.post(
        f"/api/devolutions/{dev_id}/anexos",
        files={"file": ("outra.png", PNG_1PX, "image/png")},
    )
    assert up2.status_code == 201
    assert len(ml.reviews) == 1 and len(ml.uploads) == 1


async def test_nao_recebido_abre_na_hora_sem_foto(client, make_user, auth_as, db, ml):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero="293101", numeroloja="2000101")
    r = await client.post(
        "/api/devolutions",
        json={"conta": "aguiar", "pedido_bling": "293101", "pedido_marketplace": "2000101",
              "condicao_produto": "Extraviado", "link_abertura": "http://x",
              "motivo_devolucao": "Não recebido"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["chamado_ml_status"] == "enviada"
    assert ml.reviews[0][1] == "SRF7"
    assert ml.reviews[0][3] is None  # motivo do pacote: sem anexo
    assert ml.uploads == []


async def test_golpe_pacote_vazio_item_incorreto_e_bloqueado(client, make_user, auth_as, db, ml):
    user = await make_user(permissions=_perms())
    auth_as(user)
    # Golpe = pacote veio vazio (SRF5): abre na hora, sem foto obrigatória
    await _seed_pedido(db, user, numero="293102", numeroloja="2000102")
    r = await client.post(
        "/api/devolutions",
        json={"conta": "aguiar", "pedido_bling": "293102", "pedido_marketplace": "2000102",
              "condicao_produto": "Usado", "motivo_devolucao": "Golpe"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["chamado_ml_status"] == "enviada"
    assert ml.reviews[-1][1] == "SRF5"

    # Item Incorreto = produto diferente (SRF4): exige foto → espera
    await _seed_pedido(db, user, numero="293103", numeroloja="2000103")
    r2 = await client.post(
        "/api/devolutions",
        json={"conta": "aguiar", "pedido_bling": "293103", "pedido_marketplace": "2000103",
              "condicao_produto": "Usado", "motivo_devolucao": "Item Incorreto"},
    )
    assert r2.status_code == 201, r2.text
    assert r2.json()["chamado_ml_status"] == "pendente"
    assert r2.json()["chamado_ml_erro"] == "devolucao_sem_foto"
    up = await client.post(
        f"/api/devolutions/{r2.json()['id']}/anexos",
        files={"file": ("errado.png", PNG_1PX, "image/png")},
    )
    assert up.status_code == 201, up.text
    assert up.json()["chamado_ml_status"] == "enviada"
    assert ml.reviews[-1][1] == "SRF4"
    assert ml.reviews[-1][3] == ["ml_1_errado.png"]

    # Bloqueado (mala travada por senha) → SRF6 com o texto explicando
    await _seed_pedido(db, user, numero="293108", numeroloja="2000108")
    r3 = await client.post(
        "/api/devolutions",
        json={"conta": "aguiar", "pedido_bling": "293108", "pedido_marketplace": "2000108",
              "condicao_produto": "Usado", "motivo_devolucao": "Bloqueado"},
    )
    assert r3.status_code == 201, r3.text
    assert r3.json()["chamado_ml_status"] == "enviada"
    assert ml.reviews[-1][1] == "SRF6"
    assert "bloqueado por senha" in ml.reviews[-1][2]

    # link do vídeo informado DEPOIS numa abertura pendente entra no texto
    await _seed_pedido(db, user, numero="293109", numeroloja="2000109")
    r4 = await client.post(
        "/api/devolutions",
        json={"conta": "aguiar", "pedido_bling": "293109", "pedido_marketplace": "2000109",
              "condicao_produto": "Usado", "motivo_devolucao": "Danificado (Outros)"},
    )
    assert r4.json()["chamado_ml_status"] == "pendente"
    p = await client.patch(
        f"/api/devolutions/{r4.json()['id']}", json={"observacao": "produto trocado"}
    )
    assert p.status_code == 200, p.text
    assert p.json()["chamado_ml_status"] == "pendente"  # ainda sem foto
    up2 = await client.post(
        f"/api/devolutions/{r4.json()['id']}/anexos",
        files={"file": ("dano.png", PNG_1PX, "image/png")},
    )
    assert up2.json()["chamado_ml_status"] == "enviada"


async def test_ml_ainda_nao_liberou_revisao_fica_pendente_e_cron_retenta(
    client, make_user, auth_as, db, ml
):
    user = await make_user(permissions=_perms())
    auth_as(user)
    ml.acao = False
    await _seed_pedido(db, user, numero="293104", numeroloja="2000104")
    r = await client.post(
        "/api/devolutions",
        json={"conta": "aguiar", "pedido_bling": "293104", "pedido_marketplace": "2000104",
              "condicao_produto": "Usado", "motivo_devolucao": "Item faltando"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["chamado_ml_status"] == "pendente"
    assert r.json()["chamado_ml_erro"] == "return_review_indisponivel"
    ch = (await db.execute(select(Chamado).where(Chamado.pedido_bling == "293104"))).scalar_one()
    assert ch.chamado == "777"  # claim guardado pra retentativa
    assert ml.reviews == []

    # cron: ML liberou → abre (SRF3, sem foto)
    ml.acao = True
    summary = await svc.processar_pendentes(db)
    assert summary["abertos"] == 1 and summary["pendentes"] == 0
    assert ml.reviews[-1][1] == "SRF3"
    msg = await _abertura(db, ch.id)
    assert msg.status == "enviada"

    # pendente velha demais vira falhou
    await _seed_pedido(db, user, numero="293105", numeroloja="2000105")
    ml.acao = False
    r2 = await client.post(
        "/api/devolutions",
        json={"conta": "aguiar", "pedido_bling": "293105", "pedido_marketplace": "2000105",
              "condicao_produto": "Usado", "motivo_devolucao": "Item faltando"},
    )
    assert r2.json()["chamado_ml_status"] == "pendente"
    summary2 = await svc.processar_pendentes(db, agora=datetime.now(UTC) + timedelta(days=60))
    assert summary2["falhas"] == 1
    ch2 = (await db.execute(select(Chamado).where(Chamado.pedido_bling == "293105"))).scalar_one()
    msg2 = await _abertura(db, ch2.id)
    assert msg2.status == "falhou" and msg2.erro == "devolucao_prazo_esgotado"


async def test_plataforma_sem_api_so_registra_na_aba(client, make_user, auth_as, db, ml):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero="293106", numeroloja="MG-123", platform="magalu",
                       conta="magalu x", loja="66")
    r = await client.post(
        "/api/devolutions",
        json={"conta": "magalu x", "pedido_bling": "293106", "pedido_marketplace": "MG-123",
              "condicao_produto": "Usado", "motivo_devolucao": "Danificado (Outros)"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["tem_chamado"] is True
    assert r.json()["chamado_plataforma"] == "magalu"
    assert r.json()["chamado_ml_status"] == "registrada"
    assert r.json()["chamado_ml_erro"] == "plataforma_sem_api"
    ch = (await db.execute(select(Chamado).where(Chamado.pedido_bling == "293106"))).scalar_one()
    assert ch.canal == "manual"
    assert ml.reviews == []


async def test_anexo_valida_tipo_e_video_fica_so_guardado(client, make_user, auth_as, db, ml):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero="293107", numeroloja="2000107")
    r = await client.post(
        "/api/devolutions",
        json={"conta": "aguiar", "pedido_bling": "293107", "pedido_marketplace": "2000107",
              "condicao_produto": "Usado", "motivo_devolucao": "Danificado (Outros)"},
    )
    dev_id = r.json()["id"]
    bad = await client.post(
        f"/api/devolutions/{dev_id}/anexos", files={"file": ("x.gif", b"GIF89a", "image/gif")}
    )
    assert bad.status_code == 400
    assert bad.json()["detail"]["code"] == "devolucao_anexo_tipo_invalido"
    # vídeo: guarda, mas não conta como foto → continua esperando foto
    vid = await client.post(
        f"/api/devolutions/{dev_id}/anexos", files={"file": ("v.mp4", b"\x00" * 100, "video/mp4")}
    )
    assert vid.status_code == 201, vid.text
    assert vid.json()["chamado_ml_status"] == "pendente"
    assert vid.json()["chamado_ml_erro"] == "devolucao_sem_foto"
    assert ml.uploads == []
    anexos = (await db.execute(select(DevolucaoAnexo))).scalars().all()
    assert [a.content_type for a in anexos] == ["video/mp4"]
    # remove o anexo
    d = await client.delete(f"/api/devolutions/anexos/{vid.json()['anexos'][0]['id']}")
    assert d.status_code == 204
    assert (await db.execute(select(DevolucaoAnexo))).scalars().all() == []


async def test_texto_e_reason():
    dev = Devolution(conta="aguiar", motivo_devolucao="Golpe",
                     pedido_marketplace="2000000001", sku="b001.26", produtos="Mala",
                     observacao="veio uma mala velha")
    assert svc.reason_para(dev) == "SRF5"
    dev.motivo_devolucao = "Item Incorreto"
    assert svc.reason_para(dev) == "SRF4"
    dev.motivo_devolucao = "Dano funcional / Não funciona"
    assert svc.reason_para(dev) is None
    dev.motivo_devolucao = "Não recebido"
    assert svc.reason_para(dev) == "SRF7"
    t = svc.texto_padrao(dev, "SRF7")
    assert t.startswith("O pacote da devolução ainda não chegou")
    assert "Pedido 2000000001" in t and "SKU b001.26" in t and "veio uma mala velha" in t
    assert "foto" not in t
    assert "2 foto(s)" in svc.texto_padrao(dev, "SRF2", fotos=2)


# ---------------------------------------------------------------- TikTok / Shopee / Amazon


class _FakeTikTok:
    def __init__(self, *, status: str = "BUYER_SHIPPED_ITEM", quick: bool = False):
        self.status = status
        self.quick = quick
        self.arb = ""
        self.uploads: list[tuple[str, int, str]] = []
        self.rejects: list[dict] = []

    async def get_return_list(self, *, order_ids=None, **kw):
        return [
            {
                "order_id": order_ids[0], "return_id": "4042116781741081611",
                "return_type": "RETURN_AND_REFUND", "return_status": self.status,
                "is_quick_refund": self.quick, "arbitration_status": "",
                "seller_next_action_response": [
                    {"action": "SELLER_RESPOND_RECEIVE_PACKAGE", "deadline": 1}
                ] if self.status == "BUYER_SHIPPED_ITEM" else [],
                "update_time": 10,
            }
        ]

    async def get_return_records(self, return_id, *, locale="pt-BR"):
        return []

    async def get_reject_reasons(self, return_id, *, locale="pt-BR"):
        return [
            {"name": f"reverse_reject_return_parcel_reason_{i}", "text": t}
            for i, t in enumerate(
                ["not the product", "not eligible", "missing", "haven't received", "damaged or used"], 1
            )
        ]

    async def upload_image(self, filename, content, mime="image/jpeg", *, use_case="DESCRIPTION_IMAGE"):
        self.uploads.append((filename, len(content), mime))
        return {"uri": f"tos/{filename}", "url": "https://x/y", "width": 100, "height": 80}

    async def reject_return(self, return_id, *, decision, reject_reason, comment, images=None, idempotency_key=None):
        self.rejects.append({"return_id": return_id, "decision": decision, "reason": reject_reason,
                             "comment": comment, "images": images, "idem": idempotency_key})
        return {}


class _FakeShopee:
    def __init__(self, *, status: str = "ACCEPTED"):
        self.status = status
        self.converted: list[tuple[str, str]] = []
        self.disputes: list[dict] = []

    async def get_return_detail(self, return_sn):
        return {"return_sn": return_sn, "status": self.status,
                "seller_compensation": {"seller_compensation_status": "COMPENSATION_PENDING_REQUEST"}}

    async def get_return_dispute_reason(self, return_sn):
        return [
            {"dispute_reason": 82, "dispute_reason_text": "Received return products with physical damage",
             "evidence_module_list": [
                 {"module_index": 1, "requirement": "Unboxing photo with AWB", "is_required": True},
                 {"module_index": 2, "requirement": "Photos of the damage", "is_required": True},
             ]},
            {"dispute_reason": 84, "dispute_reason_text": "Received wrong return product",
             "evidence_module_list": [{"module_index": 1, "requirement": "Photos", "is_required": True}]},
            {"dispute_reason": 81, "dispute_reason_text": "Did not receive the return product",
             "evidence_module_list": []},
        ]

    async def convert_image(self, return_sn, filename, content, mime="image/jpeg"):
        self.converted.append((return_sn, filename))
        return f"https://fileproxy/{filename}"

    async def dispute(self, return_sn, *, email, dispute_reason_id, image_list, text):
        self.disputes.append({"return_sn": return_sn, "email": email, "reason": dispute_reason_id,
                              "image_list": image_list, "text": text})
        return {}


async def test_tiktok_recusa_pacote_com_foto(client, make_user, auth_as, db, ml, monkeypatch):
    user = await make_user(permissions=_perms())
    auth_as(user)
    fake = _FakeTikTok()

    async def _c(session, *a):
        return fake

    monkeypatch.setattr(svc, "_tiktok_client_para", _c)
    await _seed_pedido(db, user, numero="290845", numeroloja="585585025945338891",
                       platform="tiktok", conta="injox", loja="77")
    r = await client.post(
        "/api/devolutions",
        json={"conta": "injox", "pedido_bling": "290845", "pedido_marketplace": "585585025945338891",
              "condicao_produto": "Não devolvido", "motivo_devolucao": "Danificado (Outros)"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["chamado_plataforma"] == "tiktok"
    assert r.json()["chamado_ml_status"] == "pendente"
    assert r.json()["chamado_ml_erro"] == "devolucao_sem_foto"
    up = await client.post(
        f"/api/devolutions/{r.json()['id']}/anexos",
        files={"file": ("mala.png", PNG_1PX, "image/png")},
    )
    assert up.status_code == 201, up.text
    assert up.json()["chamado_ml_status"] == "enviada", up.json()
    assert fake.uploads == [("mala.png", len(PNG_1PX), "image/png")]
    assert len(fake.rejects) == 1
    rj = fake.rejects[0]
    assert rj["return_id"] == "4042116781741081611"
    assert rj["decision"] == "REJECT_RECEIVED_PACKAGE"
    assert rj["reason"] == "reverse_reject_return_parcel_reason_5"
    assert rj["images"] == [{"image_id": "tos/mala.png", "mime_type": "image/png", "width": 100, "height": 80}]
    assert "danificado" in rj["comment"] and rj["idem"]
    ch = (await db.execute(select(Chamado).where(Chamado.pedido_bling == "290845"))).scalar_one()
    assert ch.chamado == "4042116781741081611" and ch.canal == "api" and ch.monitoramento is False
    assert ml.reviews == []  # nada foi pro ML


async def test_tiktok_aguarda_pacote_e_quick_refund(client, make_user, auth_as, db, ml, monkeypatch):
    user = await make_user(permissions=_perms())
    auth_as(user)
    fake = _FakeTikTok(status="AWAITING_BUYER_SHIP")

    async def _c(session, *a):
        return fake

    monkeypatch.setattr(svc, "_tiktok_client_para", _c)
    await _seed_pedido(db, user, numero="290846", numeroloja="585585025945338892",
                       platform="tiktok", conta="injox", loja="77")
    r = await client.post(
        "/api/devolutions",
        json={"conta": "injox", "pedido_bling": "290846", "pedido_marketplace": "585585025945338892",
              "condicao_produto": "Não devolvido", "motivo_devolucao": "Golpe"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["chamado_ml_erro"] == "tiktok_aguardando_pacote"
    # pacote chegou → cron abre com motivo "faltam produtos" (pacote vazio)
    fake.status = "BUYER_SHIPPED_ITEM"
    s = await svc.processar_pendentes(db)
    assert s["abertos"] == 1
    assert fake.rejects[-1]["reason"] == "reverse_reject_return_parcel_reason_3"
    assert fake.rejects[-1]["images"] is None
    # quick refund → falha definitiva
    fake2 = _FakeTikTok(quick=True)

    async def _c2(session, *a):
        return fake2

    monkeypatch.setattr(svc, "_tiktok_client_para", _c2)
    await _seed_pedido(db, user, numero="290847", numeroloja="585585025945338893",
                       platform="tiktok", conta="injox", loja="77")
    r2 = await client.post(
        "/api/devolutions",
        json={"conta": "injox", "pedido_bling": "290847", "pedido_marketplace": "585585025945338893",
              "condicao_produto": "Não devolvido", "motivo_devolucao": "Item faltando"},
    )
    assert r2.json()["chamado_ml_status"] == "falhou"
    assert r2.json()["chamado_ml_erro"] == "tiktok_quick_refund"


async def test_shopee_disputa_com_modulos_de_foto(client, make_user, auth_as, db, ml, monkeypatch):
    from app.models import DevolucaoRastreio

    user = await make_user(permissions=_perms())
    auth_as(user)
    fake = _FakeShopee()

    async def _c(session, *a):
        return fake

    monkeypatch.setattr(svc, "_shopee_client_para", _c)
    await _seed_pedido(db, user, numero="294260", numeroloja="2609045AM9GKAQ",
                       platform="shopee", conta="atv", loja="88")
    # o Acompanhamento já conhece o return_sn (sync de 30 min)
    db.add(DevolucaoRastreio(pedido_bling="294260", devolucao_id_auto="2609RSN001", fonte_auto="shopee"))
    await db.commit()
    r = await client.post(
        "/api/devolutions",
        json={"conta": "atv", "pedido_bling": "294260", "pedido_marketplace": "2609045AM9GKAQ",
              "condicao_produto": "Não devolvido", "motivo_devolucao": "Danificado (Outros)"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["chamado_plataforma"] == "shopee"
    assert r.json()["chamado_ml_erro"] == "devolucao_sem_foto"
    up = await client.post(
        f"/api/devolutions/{r.json()['id']}/anexos",
        files={"file": ("dano.jpg", PNG_1PX, "image/jpeg")},
    )
    assert up.status_code == 201, up.text
    assert up.json()["chamado_ml_status"] == "enviada", up.json()
    assert fake.converted == [("2609RSN001", "dano.jpg")]
    d = fake.disputes[0]
    assert d["return_sn"] == "2609RSN001" and d["reason"] == 82
    assert d["email"] == user.email
    assert [m["module_index"] for m in d["image_list"]] == [1, 2]
    assert d["image_list"][0]["image_url"] == ["https://fileproxy/dano.jpg"]
    assert d["image_list"][0]["requirement"] == "Unboxing photo with AWB"
    # não recebido: sem foto obrigatória, motivo "did not receive" (81)
    await _seed_pedido(db, user, numero="294261", numeroloja="2609045AM9GKAR",
                       platform="shopee", conta="atv", loja="88")
    db.add(DevolucaoRastreio(pedido_bling="294261", devolucao_id_auto="2609RSN002", fonte_auto="shopee"))
    await db.commit()
    r2 = await client.post(
        "/api/devolutions",
        json={"conta": "atv", "pedido_bling": "294261", "pedido_marketplace": "2609045AM9GKAR",
              "condicao_produto": "Não devolvido", "motivo_devolucao": "Não recebido"},
    )
    assert r2.json()["chamado_ml_status"] == "enviada", r2.json()
    assert fake.disputes[-1]["reason"] == 81 and fake.disputes[-1]["image_list"] is None


async def test_amazon_sem_api_fica_registrado(client, make_user, auth_as, db, ml):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero="294149", numeroloja="701-9431449-5435416",
                       platform="amazon", conta="poofy", loja="99")
    r = await client.post(
        "/api/devolutions",
        json={"conta": "poofy", "pedido_bling": "294149", "pedido_marketplace": "701-9431449-5435416",
              "condicao_produto": "Não devolvido", "motivo_devolucao": "Danificado (Outros)"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["chamado_plataforma"] == "amazon"
    assert r.json()["chamado_ml_status"] == "registrada"
    assert r.json()["chamado_ml_erro"] == "plataforma_sem_api"
    ch = (await db.execute(select(Chamado).where(Chamado.pedido_bling == "294149"))).scalar_one()
    assert ch.canal == "manual"
    msg = await _abertura(db, ch.id)
    assert "SAFE-T" in msg.texto and msg.status == "registrada"
    assert ml.reviews == []


async def test_tiktok_usa_unico_motivo_disponivel(client, make_user, auth_as, db, ml, monkeypatch):
    """Medido ao vivo 04/09 (290845): a TikTok BR só ofereceu o reason_2
    ("produto usado… inadequado para revenda") — Danificado tem que usar ele."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    fake = _FakeTikTok()

    async def _reasons(return_id, *, locale="pt-BR"):
        return [
            {"name": "reverse_reject_return_parcel_reason_2",
             "text": "O produto foi usado e devolvido em uma condição inadequada para revenda"},
            {"name": "seller_reject_apply_you_have_reached_an_agreement_with_the_buyer",
             "text": "Você chegou a um acordo com o cliente"},
        ]

    fake.get_reject_reasons = _reasons

    async def _c(session, *a):
        return fake

    monkeypatch.setattr(svc, "_tiktok_client_para", _c)
    await _seed_pedido(db, user, numero="290848", numeroloja="585585025945338894",
                       platform="tiktok", conta="injox", loja="77")
    r = await client.post(
        "/api/devolutions",
        json={"conta": "injox", "pedido_bling": "290848", "pedido_marketplace": "585585025945338894",
              "condicao_produto": "Não devolvido", "motivo_devolucao": "Item faltando"},
    )
    assert r.json()["chamado_ml_status"] == "enviada", r.json()
    assert fake.rejects[-1]["reason"] == "reverse_reject_return_parcel_reason_2"
    assert svc._tiktok_reason_por_texto(await _reasons("x"), "não recebido") is None


async def test_shopee_so_reembolso_pacote_vazio_usa_id_53(client, make_user, auth_as, db, ml, monkeypatch):
    """Medido ao vivo 04/09 (292617 / 2608310QMDCH65V): comprador alega pacote
    vazio, só reembolso, motivos vêm SÓ com id (53/54) + requisito em pt."""
    from app.models import DevolucaoRastreio

    user = await make_user(permissions=_perms())
    auth_as(user)
    fake = _FakeShopee()

    async def _det(return_sn):
        return {"return_sn": return_sn, "status": "ACCEPTED", "return_solution": 1,
                "needs_logistics": False, "reason": "SUSPICIOUS_PARCEL",
                "seller_compensation": {"seller_compensation_status": "PENDING_REQUEST"}}

    async def _reasons(return_sn):
        return [
            {"dispute_reason": 53, "dispute_requirement": "Envie imagens dos itens enviados…",
             "evidence_module_list": [{"module_index": 1, "requirement": "Anexe fotos/vídeos gerados por você no momento da expedição do pedido", "is_required": True}]},
            {"dispute_reason": 54, "dispute_requirement": "Anexe fotos/vídeos…",
             "evidence_module_list": [{"module_index": 1, "requirement": "Envie imagens…", "is_required": True}]},
        ]

    fake.get_return_detail = _det
    fake.get_return_dispute_reason = _reasons

    async def _c(session, *a):
        return fake

    monkeypatch.setattr(svc, "_shopee_client_para", _c)
    await _seed_pedido(db, user, numero="292617", numeroloja="260827DBUMDT1W",
                       platform="shopee", conta="mega", loja="90")
    db.add(DevolucaoRastreio(pedido_bling="292617", devolucao_id_auto="2608310QMDCH65V", fonte_auto="shopee"))
    await db.commit()
    r = await client.post(
        "/api/devolutions",
        json={"conta": "mega", "pedido_bling": "292617", "pedido_marketplace": "260827DBUMDT1W",
              "condicao_produto": "Não devolvido", "motivo_devolucao": "Golpe"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["chamado_ml_erro"] == "devolucao_sem_foto"  # Shopee exige foto
    up = await client.post(
        f"/api/devolutions/{r.json()['id']}/anexos",
        files={"file": ("expedicao.jpg", PNG_1PX, "image/jpeg")},
    )
    assert up.json()["chamado_ml_status"] == "enviada", up.json()
    d = fake.disputes[-1]
    assert d["return_sn"] == "2608310QMDCH65V" and d["reason"] == 53
    assert d["image_list"] == [{"module_index": 1,
                                "requirement": "Anexe fotos/vídeos gerados por você no momento da expedição do pedido",
                                "image_url": ["https://fileproxy/expedicao.jpg"]}]
    assert "sem o produto dentro" in d["text"]
    # já contestada (PENDING_REQUEST → REQUESTED) não manda de novo
    ch = (await db.execute(select(Chamado).where(Chamado.pedido_bling == "292617"))).scalar_one()
    msg = await _abertura(db, ch.id)
    assert msg.status == "enviada"


async def test_link_envio_regra_mala_eletro_e_conta_fallback(client, make_user, auth_as, db, ml):
    from app.services import chamados_devolucao as cd

    assert cd.produto_mala_ou_eletro("b001.26") is True  # mala
    assert cd.produto_mala_ou_eletro("a006") is True  # acessório de mala
    assert cd.produto_mala_ou_eletro("dg048.ra+a003.ra") is True  # celular (kit)
    assert cd.produto_mala_ou_eletro("uaf001m1.220") is True  # airfryer
    assert cd.produto_mala_ou_eletro("a003.ra") is False  # fone
    assert cd.produto_mala_ou_eletro("e3") is False
    user = await make_user(permissions=_perms())
    auth_as(user)
    # eletro sem motivo de chamado: link não é exigido
    await _seed_pedido(db, user, numero="293110", numeroloja="2000110")
    r = await client.post(
        "/api/devolutions",
        json={"conta": "aguiar", "pedido_bling": "293110", "pedido_marketplace": "2000110",
              "sku": "dg048.ra", "condicao_produto": "Não devolvido",
              "motivo_devolucao": "Dano funcional / Não funciona"},
    )
    assert r.status_code == 201, r.text
    # trocar pro motivo de chamado sem link → 422; com link → ok
    p = await client.patch(f"/api/devolutions/{r.json()['id']}", json={"motivo_devolucao": "Golpe"})
    assert p.status_code == 422 and p.json()["detail"]["code"] == "link_envio_obrigatorio"
    p2 = await client.patch(
        f"/api/devolutions/{r.json()['id']}",
        json={"motivo_devolucao": "Golpe", "link_envio": "https://x/envio"},
    )
    assert p2.status_code == 200, p2.text
    assert p2.json()["link_envio"] == "https://x/envio"
    # conta da linha = nome da loja no Bling ("Loja 55") → cai pro store_info do pedido (aguiar)
    ch = (await db.execute(select(Chamado).where(Chamado.pedido_bling == "293110"))).scalar_one()
    dev = await db.get(Devolution, UUID(r.json()["id"]))
    dev.conta = "Loja 55"
    ch.conta = "Loja 55"
    contas = await cd._contas_candidatas(db, ch, dev)
    assert contas == ["Loja 55", "aguiar"]


async def test_shopee_acha_return_sn_varrendo_a_api(client, make_user, auth_as, db, ml, monkeypatch):
    """Sem linha no Acompanhamento, o return_sn vem da returns API (create_time
    em fatias de 15 dias), com o cliente já resolvido pela loja do pedido."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    fake = _FakeShopee()
    chamadas = []

    async def _lista(*, create_time_from=None, create_time_to=None, **kw):
        chamadas.append((create_time_from, create_time_to))
        return [
            {"order_sn": "260827OUTRO", "return_sn": "X1", "status": "ACCEPTED", "update_time": 5},
            {"order_sn": "260827DBUMDT1W", "return_sn": "2608310QMDCH65V", "status": "ACCEPTED", "update_time": 9},
            {"order_sn": "260827DBUMDT1W", "return_sn": "CANCELADA", "status": "CANCELLED", "update_time": 99},
        ]

    fake.get_return_list = _lista

    async def _c(session, *a):
        return fake

    monkeypatch.setattr(svc, "_shopee_client_para", _c)
    await _seed_pedido(db, user, numero="292618", numeroloja="260827DBUMDT1W",
                       platform="shopee", conta="mega", loja="90")
    r = await client.post(
        "/api/devolutions",
        json={"conta": "Shopee Marquezini", "pedido_bling": "292618",
              "pedido_marketplace": "260827DBUMDT1W", "data": "2026-08-26T00:00:00Z",
              "condicao_produto": "Não devolvido", "motivo_devolucao": "Não recebido"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["chamado_ml_status"] == "enviada", r.json()
    assert fake.disputes[-1]["return_sn"] == "2608310QMDCH65V"
    assert chamadas and all(b - a <= 15 * 86400 for a, b in chamadas)


# ---------------------------------------------------------------- acompanhamento (resposta no histórico)


async def _recebidas(db, chamado_id):
    rows = (
        await db.execute(
            select(ChamadoMensagem)
            .where(ChamadoMensagem.chamado_id == chamado_id, ChamadoMensagem.direcao == "recebida")
            .order_by(ChamadoMensagem.created_at)
        )
    ).scalars().all()
    return [m.texto for m in rows]


async def test_sync_tiktok_resposta_no_historico_e_encerra(client, make_user, auth_as, db, ml, monkeypatch):
    from app.services import chamados_devolucao_sync as sync

    user = await make_user(permissions=_perms())
    auth_as(user)
    fake = _FakeTikTok()

    async def _c(session, *a):
        return fake

    monkeypatch.setattr(svc, "_tiktok_client_para", _c)
    await _seed_pedido(db, user, numero="290850", numeroloja="585585025945338850",
                       platform="tiktok", conta="injox", loja="77")
    r = await client.post(
        "/api/devolutions",
        json={"conta": "injox", "pedido_bling": "290850", "pedido_marketplace": "585585025945338850",
              "condicao_produto": "Não devolvido", "motivo_devolucao": "Golpe"},
    )
    assert r.json()["chamado_ml_status"] == "enviada", r.json()
    ch = (await db.execute(select(Chamado).where(Chamado.pedido_bling == "290850"))).scalar_one()

    # 1) recusa registrada + comprador contestou (arbitragem) + nota do comprador
    fake.status = "REJECT_RECEIVE_PACKAGE"
    fake.arb = "IN_PROGRESS"
    orig = fake.get_return_list

    async def _lista(*, order_ids=None, **kw):
        out = await orig(order_ids=order_ids)
        out[0]["arbitration_status"] = fake.arb
        return out

    fake.get_return_list = _lista

    async def _records(return_id, *, locale="pt-BR"):
        return [{"role": "BUYER", "create_time": 1788540000, "note": "Discordo, o produto estava novo"},
                {"role": "SELLER", "create_time": 1788540001, "note": "nossa nota (ignorada)"}]

    fake.get_return_records = _records
    s1 = await sync.sync_respostas(db)
    assert s1["verificados"] == 1 and s1["novos"] == 3 and s1["encerrados"] == 0
    txts = await _recebidas(db, ch.id)
    assert any("Recusa do pacote registrada" in t for t in txts)
    assert any("ARBITRAGEM" in t for t in txts)
    assert any("Comprador" in t and "Discordo" in t for t in txts)
    # rodar de novo não duplica
    s2 = await sync.sync_respostas(db)
    assert s2["novos"] == 0
    # 2) decisão a favor do vendedor + devolução cancelada → encerra
    fake.arb = "SUPPORT_SELLER"
    fake.status = "RETURN_OR_REFUND_REQUEST_CANCEL"
    s3 = await sync.sync_respostas(db)
    assert s3["novos"] == 2 and s3["encerrados"] == 1
    await db.refresh(ch)
    assert ch.resolvido is True
    # resolvido some da varredura
    assert (await sync.sync_respostas(db))["verificados"] == 0


async def test_sync_shopee_prova_extra_e_compensacao(client, make_user, auth_as, db, ml, monkeypatch):
    from app.models import DevolucaoRastreio
    from app.services import chamados_devolucao_sync as sync

    user = await make_user(permissions=_perms())
    auth_as(user)
    fake = _FakeShopee()
    estado = {"status": "SELLER_DISPUTE", "proof": "PENDING", "comp": "PENDING_REQUEST"}

    async def _det(return_sn):
        return {"return_sn": return_sn, "status": estado["status"], "return_solution": 0,
                "needs_logistics": True,
                "seller_proof": {"seller_proof_status": estado["proof"], "seller_evidence_deadline": 1788600000},
                "seller_compensation": {"seller_compensation_status": estado["comp"], "compensation_amount": 786.71}}

    fake.get_return_detail = _det

    async def _c(session, *a):
        return fake

    monkeypatch.setattr(svc, "_shopee_client_para", _c)
    await _seed_pedido(db, user, numero="292620", numeroloja="260827SYNC01",
                       platform="shopee", conta="mega", loja="90")
    db.add(DevolucaoRastreio(pedido_bling="292620", devolucao_id_auto="2608SYNC01", fonte_auto="shopee"))
    await db.commit()
    estado["status"] = "ACCEPTED"
    estado["proof"] = ""
    r = await client.post(
        "/api/devolutions",
        json={"conta": "mega", "pedido_bling": "292620", "pedido_marketplace": "260827SYNC01",
              "condicao_produto": "Não devolvido", "motivo_devolucao": "Não recebido"},
    )
    assert r.json()["chamado_ml_status"] == "enviada", r.json()
    ch = (await db.execute(select(Chamado).where(Chamado.pedido_bling == "292620"))).scalar_one()
    estado["status"] = "SELLER_DISPUTE"
    estado["proof"] = "PENDING"
    s1 = await sync.sync_respostas(db)
    assert s1["novos"] == 2
    txts = await _recebidas(db, ch.id)
    assert any("PROVA ADICIONAL" in t and "prazo até" in t for t in txts)
    assert any("Disputa registrada" in t for t in txts)
    estado["proof"] = "UPLOADED"
    estado["comp"] = "COMPENSATION_APPROVED"
    estado["status"] = "CLOSED"
    s2 = await sync.sync_respostas(db)
    assert s2["encerrados"] == 1
    txts = await _recebidas(db, ch.id)
    assert any("APROVOU a compensação" in t and "786.71" in t for t in txts)
    await db.refresh(ch)
    assert ch.resolvido is True


async def test_sync_ml_mensagens_do_mediador_e_decisao(client, make_user, auth_as, db, ml, monkeypatch):
    from app.services import chamados_devolucao_sync as sync

    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero="293120", numeroloja="2000120")
    r = await client.post(
        "/api/devolutions",
        json={"conta": "aguiar", "pedido_bling": "293120", "pedido_marketplace": "2000120",
              "condicao_produto": "Não devolvido", "motivo_devolucao": "Não recebido"},
    )
    assert r.json()["chamado_ml_status"] == "enviada", r.json()
    ch = (await db.execute(select(Chamado).where(Chamado.pedido_bling == "293120"))).scalar_one()
    fake = ml
    fake.closed = False

    async def _msgs(claim_id):
        return [{"sender_role": "mediator", "message": "Precisamos do comprovante de postagem", "date_created": "2026-09-04T12:00:00.000-03:00"},
                {"sender_role": "respondent", "message": "nossa (ignorada)"}]

    fake.get_claim_messages = _msgs
    s1 = await sync.sync_respostas(db)
    assert s1["novos"] == 1 and s1["encerrados"] == 0
    txts = await _recebidas(db, ch.id)
    assert txts == ["Mediador do ML 04/09 12:00: Precisamos do comprovante de postagem"]
    # encerrou a favor do vendedor
    orig = fake.get_claim

    async def _claim(claim_id):
        c = await orig(claim_id)
        c["status"] = "closed"
        c["resolution"] = {"benefited": "respondent", "reason": "seller_return_failed"}
        return c

    fake.get_claim = _claim
    s2 = await sync.sync_respostas(db)
    assert s2["encerrados"] == 1
    txts = await _recebidas(db, ch.id)
    assert any("a favor do VENDEDOR" in t for t in txts)
    await db.refresh(ch)
    assert ch.resolvido is True
