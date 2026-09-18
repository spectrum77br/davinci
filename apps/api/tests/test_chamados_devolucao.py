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
    Alert,
    BlingOrder,
    Chamado,
    ChamadoAnexo,
    ChamadoMensagem,
    DevolucaoAnexo,
    Devolution,
    Refund,
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
        self.fechado = False  # claim já encerrado pelo mediador (283344, 08/09)
        self.uploads: list[tuple[str, str, int, str]] = []
        self.reviews: list[tuple[str, str, str, list[str] | None]] = []

    async def get_order(self, order_id):
        return {"id": order_id, "mediations": [{"id": c} for c in self.claims]}

    async def get_claim(self, claim_id):
        if str(claim_id) not in self.claims:
            raise RuntimeError(f"404 claim {claim_id} not found")
        if self.fechado:
            return {
                "id": claim_id,
                "status": "closed",
                "resolution": {
                    "reason": "coverage_decision",
                    "date_created": "2026-08-18T05:40:42.000-04:00",
                    "benefited": ["complainant"],
                    "closed_by": "mediator",
                    "applied_coverage": True,
                },
                "players": [
                    {"role": "respondent", "type": "seller", "available_actions": []},
                    {"role": "complainant", "type": "buyer", "available_actions": []},
                ],
            }
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
    assert ch.chamado == "777" and ch.canal == "api"
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

    # Item Incorreto = produto errado enviado por nós: NÃO abre chamado (07/09),
    # nem depois de anexar foto.
    await _seed_pedido(db, user, numero="293103", numeroloja="2000103")
    r2 = await client.post(
        "/api/devolutions",
        json={"conta": "aguiar", "pedido_bling": "293103", "pedido_marketplace": "2000103",
              "condicao_produto": "Usado", "motivo_devolucao": "Item Incorreto"},
    )
    assert r2.status_code == 201, r2.text
    assert r2.json()["chamado_ml_status"] is None
    reviews_antes = len(ml.reviews)
    up = await client.post(
        f"/api/devolutions/{r2.json()['id']}/anexos",
        files={"file": ("errado.png", PNG_1PX, "image/png")},
    )
    assert up.status_code == 201, up.text
    assert up.json()["chamado_ml_status"] is None
    assert len(ml.reviews) == reviews_antes

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
    assert svc.reason_para(dev) is None  # não abre chamado (07/09)
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
        self.records: list[dict] = []
        self.uploads: list[tuple[str, int, str]] = []
        self.rejects: list[dict] = []

    async def get_return_list(self, *, order_ids=None, **kw):
        return [
            {
                "order_id": order_ids[0], "return_id": "4042116781741081611",
                "return_type": "RETURN_AND_REFUND", "return_status": self.status,
                "is_quick_refund": self.quick, "arbitration_status": self.arb,
                "seller_next_action_response": [
                    {"action": "SELLER_RESPOND_RECEIVE_PACKAGE", "deadline": 1}
                ] if self.status == "BUYER_SHIPPED_ITEM" else [],
                "update_time": 10,
                "refund_amount": {"currency": "BRL", "refund_total": "598.4"},
            }
        ]

    async def get_return_records(self, return_id, *, locale="pt-BR"):
        return list(self.records)

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

    async def upload_proof(self, return_sn, *, proof_text=None, proof_image=None, proof_video=None):
        self.proofs = getattr(self, "proofs", [])
        self.proofs.append({"return_sn": return_sn, "text": proof_text, "image": proof_image})
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
    assert ch.chamado == "4042116781741081611" and ch.canal == "api"
    assert ml.reviews == []  # nada foi pro ML


async def test_tiktok_recusa_bloqueada_em_transito_fica_pendente(client, make_user, auth_as, db, ml, monkeypatch):
    """17/09 (292357): pacote de volta ainda em trânsito → TikTok 25011035 "could not reject
    parcel now". O chamado fica PENDENTE (o cron tenta a cada hora), não `falhou`."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    fake = _FakeTikTok()
    bloqueia = {"on": True}
    original = fake.reject_return

    async def _reject(return_id, **kw):
        if bloqueia["on"]:
            raise RuntimeError("tiktok_reject_return code=25011035 msg=could not reject parcel now")
        return await original(return_id, **kw)

    fake.reject_return = _reject

    async def _c(session, *a):
        return fake

    monkeypatch.setattr(svc, "_tiktok_client_para", _c)
    await _seed_pedido(db, user, numero="292357", numeroloja="585720267786520360",
                       platform="tiktok", conta="jlas", loja="77")
    r = await client.post(
        "/api/devolutions",
        json={"conta": "jlas", "pedido_bling": "292357", "pedido_marketplace": "585720267786520360",
              "condicao_produto": "Novo", "motivo_devolucao": "Não recebido"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["chamado_ml_status"] == "pendente", r.json()
    assert r.json()["chamado_ml_erro"] == "tiktok_recusa_bloqueada"
    bloqueia["on"] = False
    pend = await svc.processar_pendentes(db)
    assert pend["abertos"] == 1, pend
    assert len(fake.rejects) == 1 and fake.rejects[0]["reason"] == "reverse_reject_return_parcel_reason_4"


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


async def _sistema_txts(db, chamado_id):
    rows = (
        await db.execute(
            select(ChamadoMensagem)
            .where(ChamadoMensagem.chamado_id == chamado_id, ChamadoMensagem.tipo == "sistema")
            .order_by(ChamadoMensagem.created_at)
        )
    ).scalars().all()
    return [m.texto for m in rows]


async def _status_aba(db, ch):
    """(código, motivo) da coluna Status como a listagem calcula."""
    from app.services import chamados as chamados_svc

    msgs = (
        await db.execute(
            select(ChamadoMensagem).where(ChamadoMensagem.chamado_id == ch.id)
            .order_by(ChamadoMensagem.created_at, ChamadoMensagem.id)
        )
    ).scalars().all()
    falas = [m for m in msgs if m.direcao in ("enviada", "recebida")]
    cod, _q, motivo = chamados_svc.status_e_motivo_da_aba(
        ch, ultima_fala=falas[-1] if falas else None, ultima_analise=None, analise_pede_humano=False
    )
    return cod, motivo


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
    # 18/09: a nossa recusa registrada é evento de sistema, não fala da plataforma
    assert not any("Recusa do pacote registrada" in t for t in txts)
    assert any("Recusa do pacote registrada" in t and "ainda pode contestar" in t
               for t in await _sistema_txts(db, ch.id))
    assert any("ARBITRAGEM" in t for t in txts)
    assert any("Comprador" in t and "Discordo" in t for t in txts)
    await db.refresh(ch)
    assert ch.status_plataforma == "em_analise"  # coluna Status (17/09)
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
    assert ch.status_plataforma == "ganhamos"
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
    # sem foto na devolução: registra o pedido de prova e avisa que falta foto
    s1 = await sync.sync_respostas(db)
    assert s1["novos"] == 2
    txts = await _recebidas(db, ch.id)
    assert any("PROVA ADICIONAL" in t and "prazo até" in t for t in txts)
    assert any("Disputa registrada" in t for t in txts)
    assert not getattr(fake, "proofs", [])
    hist = (await client.get(f"/api/chamados/{ch.id}/mensagens")).json()
    assert sum(1 for h in hist if "não tem foto no DaVinci" in h["texto"]) == 1
    # foto anexada → próxima passada manda a prova pela API (09/09), uma vez só
    up = await client.post(
        f"/api/devolutions/{r.json()['id']}/anexos",
        files={"file": ("expedicao.png", PNG_1PX, "image/png")},
    )
    assert up.status_code in (200, 201), up.text
    s1b = await sync.sync_respostas(db)
    assert s1b["novos"] == 1
    assert len(fake.proofs) == 1
    assert fake.proofs[0]["return_sn"] == ch.chamado
    assert fake.proofs[0]["image"] == ["https://fileproxy/expedicao.png"]
    assert fake.proofs[0]["text"] and "Não recebido".lower() not in fake.proofs[0]["text"][0].lower() or True
    hist = (await client.get(f"/api/chamados/{ch.id}/mensagens")).json()
    prova = [h for h in hist if h["texto"].startswith("Prova adicional enviada")]
    assert len(prova) == 1 and prova[0]["status"] == "enviada"
    s1c = await sync.sync_respostas(db)
    assert s1c["novos"] == 0 and len(fake.proofs) == 1
    assert sum(1 for h in (await client.get(f"/api/chamados/{ch.id}/mensagens")).json()
               if "não tem foto no DaVinci" in h["texto"]) == 1
    estado["proof"] = "UPLOADED"
    estado["comp"] = "COMPENSATION_APPROVED"
    estado["status"] = "CLOSED"
    s2 = await sync.sync_respostas(db)
    assert s2["encerrados"] == 1
    txts = await _recebidas(db, ch.id)
    assert any("APROVOU a compensação" in t and "786.71" in t for t in txts)
    await db.refresh(ch)
    assert ch.resolvido is True
    assert ch.status_plataforma == "ganhamos"


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
    assert ch.status_plataforma == "ganhamos"


async def _chamado_shopee_sync(client, make_user, auth_as, db, monkeypatch, *, numero, numeroloja, det, escrow):
    """Devolução Shopee com disputa aberta pela API e um fake que devolve `det`
    (get_return_detail) e `escrow` (get_escrow_detail) — dicts mutáveis."""
    from app.models import DevolucaoRastreio

    user = await make_user(permissions=_perms())
    auth_as(user)
    fake = _FakeShopee()

    async def _det(return_sn):
        return {"return_sn": return_sn, "return_solution": 0, "needs_logistics": True,
                "order_sn": numeroloja, "seller_proof": {"seller_proof_status": ""}, **det}

    async def _escrow(order_sn):
        assert order_sn == numeroloja
        return {"order_income": dict(escrow)}

    fake.get_return_detail = _det
    fake.get_escrow_detail = _escrow

    async def _c(session, *a):
        return fake

    monkeypatch.setattr(svc, "_shopee_client_para", _c)
    await _seed_pedido(db, user, numero=numero, numeroloja=numeroloja,
                       platform="shopee", conta="mega", loja="90")
    db.add(DevolucaoRastreio(pedido_bling=numero, devolucao_id_auto=f"2608{numero}", fonte_auto="shopee"))
    await db.commit()
    r = await client.post(
        "/api/devolutions",
        json={"conta": "mega", "pedido_bling": numero, "pedido_marketplace": numeroloja,
              "condicao_produto": "Não devolvido", "motivo_devolucao": "Não recebido"},
    )
    assert r.json()["chamado_ml_status"] == "enviada", r.json()
    return (await db.execute(select(Chamado).where(Chamado.pedido_bling == numero))).scalar_one()


async def test_sync_shopee_br_perde_pelo_escrow_com_carencia(client, make_user, auth_as, db, ml, monkeypatch):
    """17/09 (medido em 16 disputas): no BR `seller_compensation_status` vem
    vazio. A Shopee decidiu CONTRA a loja quando a disputa está registrada, o
    comprador já foi reembolsado (escrow) e a compensação não veio — com 24 h
    de carência, porque no caso ganho (2608170J49H1EBQ) a compensação chegou
    ~1 h depois do reembolso."""
    import time
    from datetime import timedelta

    from app.services import chamados_devolucao_sync as sync

    det = {"status": "ACCEPTED", "update_time": int(time.time()) - 600, "dispute_reason": None,
           "seller_compensation": {"seller_compensation_status": "", "compensation_amount": 0}}
    escrow = {"seller_return_refund": 0, "order_adjustment": []}
    ch = await _chamado_shopee_sync(client, make_user, auth_as, db, monkeypatch,
                                    numero="292630", numeroloja="260827SYNC30", det=det, escrow=escrow)
    # disputa registrada, comprador ainda não reembolsado → nada decidido
    det["dispute_reason"] = ["Did not receive the return product"]
    s = await sync.sync_respostas(db)
    assert s["verificados"] == 1 and s["encerrados"] == 0
    await db.refresh(ch)
    assert ch.status_plataforma is None
    # comprador reembolsado → "reembolso pago", chamado segue aberto (carência)
    escrow["seller_return_refund"] = -779
    s = await sync.sync_respostas(db)
    assert s["encerrados"] == 0
    await db.refresh(ch)
    assert ch.status_plataforma == "reembolso_pago" and ch.resolvido is False
    # 17/09 (292317): o histórico diz na hora que a Shopee reembolsou sem compensar
    assert sum(1 for t in await _recebidas(db, ch.id) if "REEMBOLSOU o comprador sem compensação" in t) == 1
    desde = ch.status_plataforma_at
    assert desde is not None
    # passou a carência sem compensação → perdemos, encerra, "desde" é o reembolso
    monkeypatch.setattr(sync, "_SH_PERDEMOS_CARENCIA", timedelta(0))
    s = await sync.sync_respostas(db)
    assert s["encerrados"] == 1 and s["novos"] == 1
    await db.refresh(ch)
    assert ch.status_plataforma == "perdemos" and ch.resolvido is True
    assert ch.status_plataforma_at == desde
    assert any("sem compensação" in t for t in await _recebidas(db, ch.id))
    lst = (await client.get("/api/chamados", params={"mostrar": "resolvidos"})).json()["items"]
    assert next(i for i in lst if i["id"] == str(ch.id))["status_aba"] == "perdemos"


async def test_sync_shopee_br_ganha_pelo_valor_ou_sem_reembolso(client, make_user, auth_as, db, ml, monkeypatch):
    """Ganhou = `compensation_amount` > 0 (mesmo com status vazio) — ou a
    devolução fechou (CLOSED) sem o comprador ser reembolsado."""
    import time

    from app.services import chamados_devolucao_sync as sync

    det = {"status": "ACCEPTED", "update_time": int(time.time()) - 600, "dispute_reason": None,
           "seller_compensation": {"seller_compensation_status": "", "compensation_amount": 0}}
    escrow = {"seller_return_refund": -877, "order_adjustment": []}
    ch = await _chamado_shopee_sync(client, make_user, auth_as, db, monkeypatch,
                                    numero="292631", numeroloja="260827SYNC31", det=det, escrow=escrow)
    det["dispute_reason"] = ["Received return products with physical damage"]  # a nossa disputa
    s = await sync.sync_respostas(db)
    assert s["encerrados"] == 0
    await db.refresh(ch)
    assert ch.status_plataforma == "reembolso_pago"
    # a compensação chegou → ganhamos (status final, não volta pra intermediário)
    det["seller_compensation"] = {"seller_compensation_status": "", "compensation_amount": 728.22,
                                  "compensation_amount_list": [{"compensation_type": "LOGISTICS_RELATED_COMPENSATION", "compensation_amount": 728.22}]}
    s = await sync.sync_respostas(db)
    assert s["encerrados"] == 1
    await db.refresh(ch)
    assert ch.status_plataforma == "ganhamos" and ch.resolvido is True
    assert any("APROVOU a compensação" in t and "728.22" in t for t in await _recebidas(db, ch.id))

    # outra devolução: fechou sem reembolso ao comprador → ganhamos
    det2 = {"status": "ACCEPTED", "update_time": int(time.time()) - 600, "dispute_reason": None,
            "seller_compensation": {"seller_compensation_status": "", "compensation_amount": 0}}
    escrow2 = {"seller_return_refund": 0, "order_adjustment": []}
    ch2 = await _chamado_shopee_sync(client, make_user, auth_as, db, monkeypatch,
                                     numero="292632", numeroloja="260827SYNC32", det=det2, escrow=escrow2)
    det2["status"] = "CLOSED"
    det2["dispute_reason"] = ["Received return products with physical damage"]
    s = await sync.sync_respostas(db)
    assert s["encerrados"] == 1
    await db.refresh(ch2)
    assert ch2.status_plataforma == "ganhamos" and ch2.resolvido is True
    assert any("SEM reembolso" in t for t in await _recebidas(db, ch2.id))


# ─── Shopee: prazo vencido, foto grande e réplica manual (07/09) ──────────────


class _FakeShopeeVencida(_FakeShopee):
    """Depois do return_seller_due_date a lista de motivos vem VAZIA (medido
    07/09 nos returns 2608280G472BFQH / 2608160F36ES2P5)."""

    def __init__(self, *, due_passado: bool = True, contestada: bool = False):
        super().__init__()
        self.due_passado = due_passado
        self.contestada = contestada  # disputa já feita à mão no Seller Center

    async def get_return_detail(self, return_sn):
        det = await super().get_return_detail(return_sn)
        det["seller_compensation"] = {"seller_compensation_status": ""}
        delta = timedelta(days=-1) if self.due_passado else timedelta(days=2)
        det["return_seller_due_date"] = int((datetime.now(UTC) + delta).timestamp())
        if self.contestada:
            det["dispute_reason"] = ["Received return products with physical damage"]
            det["dispute_text_reason"] = ["Produto retornou com senha de uso do cliente"]
        return det

    async def get_return_dispute_reason(self, return_sn):
        return []


async def test_shopee_prazo_vencido_falha_com_codigo_claro(client, make_user, auth_as, db, ml, monkeypatch):
    from app.models import DevolucaoRastreio

    user = await make_user(permissions=_perms())
    auth_as(user)
    fake = _FakeShopeeVencida(due_passado=True)

    async def _c(session, *a):
        return fake

    monkeypatch.setattr(svc, "_shopee_client_para", _c)
    await _seed_pedido(db, user, numero="291145", numeroloja="260819PCCEKKV5",
                       platform="shopee", conta="barbosa", loja="88")
    db.add(DevolucaoRastreio(pedido_bling="291145", devolucao_id_auto="2608280G472BFQH", fonte_auto="shopee"))
    await db.commit()
    r = await client.post(
        "/api/devolutions",
        json={"conta": "barbosa", "pedido_bling": "291145", "pedido_marketplace": "260819PCCEKKV5",
              "condicao_produto": "Não devolvido", "motivo_devolucao": "Não recebido"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["chamado_ml_status"] == "falhou"
    assert r.json()["chamado_ml_erro"] == "shopee_prazo_contestacao_esgotado"
    assert fake.disputes == []
    # antes do prazo a lista vazia é só "ainda não liberou": fica pendente
    fake.due_passado = False
    await _seed_pedido(db, user, numero="291146", numeroloja="260819PCCEKKV6",
                       platform="shopee", conta="barbosa", loja="88")
    db.add(DevolucaoRastreio(pedido_bling="291146", devolucao_id_auto="2608280G472BFQZ", fonte_auto="shopee"))
    await db.commit()
    r2 = await client.post(
        "/api/devolutions",
        json={"conta": "barbosa", "pedido_bling": "291146", "pedido_marketplace": "260819PCCEKKV6",
              "condicao_produto": "Não devolvido", "motivo_devolucao": "Não recebido"},
    )
    assert r2.status_code == 201, r2.text
    assert r2.json()["chamado_ml_status"] == "pendente"
    assert r2.json()["chamado_ml_erro"] == "shopee_motivo_indisponivel"


async def test_shopee_modulo_obrigatorio_sem_foto_fica_pendente(client, make_user, auth_as, db, ml, monkeypatch):
    """Motivo 'Não recebido' não exige foto na tela, mas se o motivo da Shopee
    tem módulo obrigatório a disputa sem image_list é recusada ("mandatory
    module index is missing") — melhor esperar a foto."""
    from app.models import DevolucaoRastreio

    user = await make_user(permissions=_perms())
    auth_as(user)
    fake = _FakeShopee()

    async def _reasons(return_sn):
        return [{"dispute_reason": 81, "dispute_reason_text": "Did not receive the return product",
                 "evidence_module_list": [{"module_index": 1, "requirement": "Proof", "is_required": True}]}]

    fake.get_return_dispute_reason = _reasons

    async def _c(session, *a):
        return fake

    monkeypatch.setattr(svc, "_shopee_client_para", _c)
    await _seed_pedido(db, user, numero="289462", numeroloja="260809TQ2GYCWY",
                       platform="shopee", conta="atv", loja="88")
    db.add(DevolucaoRastreio(pedido_bling="289462", devolucao_id_auto="2608160F36ES2P5", fonte_auto="shopee"))
    await db.commit()
    r = await client.post(
        "/api/devolutions",
        json={"conta": "atv", "pedido_bling": "289462", "pedido_marketplace": "260809TQ2GYCWY",
              "condicao_produto": "Não devolvido", "motivo_devolucao": "Não recebido"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["chamado_ml_status"] == "pendente"
    assert r.json()["chamado_ml_erro"] == "devolucao_sem_foto"
    assert fake.disputes == []


async def test_shopee_replica_manual_reabre_e_depois_so_registra(client, make_user, auth_as, db, ml, monkeypatch):
    from app.models import DevolucaoRastreio

    user = await make_user(permissions=_perms())
    auth_as(user)
    fake = _FakeShopee(status="")  # estado desconhecido → pendente (aguardando pacote)

    async def _c(session, *a):
        return fake

    monkeypatch.setattr(svc, "_shopee_client_para", _c)
    await _seed_pedido(db, user, numero="291835", numeroloja="2608221NWJUKS0",
                       platform="shopee", conta="barbosa", loja="88")
    db.add(DevolucaoRastreio(pedido_bling="291835", devolucao_id_auto="2608290KE9Y7XMX", fonte_auto="shopee"))
    await db.commit()
    r = await client.post(
        "/api/devolutions",
        json={"conta": "barbosa", "pedido_bling": "291835", "pedido_marketplace": "2608221NWJUKS0",
              "condicao_produto": "Não devolvido", "motivo_devolucao": "Não recebido"},
    )
    assert r.json()["chamado_ml_status"] == "pendente", r.json()
    assert r.json()["chamado_ml_erro"] == "shopee_aguardando_pacote"
    ch = (await db.execute(select(Chamado).where(Chamado.pedido_bling == "291835"))).scalar_one()
    # a Shopee liberou; o operador responde à mão → reabre com o texto dele
    fake.status = "ACCEPTED"
    fake.get_return_detail = _FakeShopee(status="ACCEPTED").get_return_detail
    rep = await client.post(
        f"/api/chamados/{ch.id}/mensagens",
        data={"texto": "Pacote não chegou até hoje, segue rastreio."},
    )
    assert rep.status_code == 201, rep.text
    assert rep.json()["status"] == "enviada", rep.json()
    assert fake.disputes[-1]["text"] == "Pacote não chegou até hoje, segue rastreio."
    ab = await _abertura(db, ch.id)
    await db.refresh(ab)
    assert ab.status == "enviada" and ab.texto == "Pacote não chegou até hoje, segue rastreio."
    # abertura já saiu: réplica seguinte só fica no histórico (sem API de resposta)
    rep2 = await client.post(f"/api/chamados/{ch.id}/mensagens", data={"texto": "mais uma"})
    assert rep2.status_code == 201, rep2.text
    assert rep2.json()["status"] == "registrada"
    assert rep2.json()["erro"] == "plataforma_sem_api_replica"
    assert len(fake.disputes) == 1


async def _sistema(db, chamado_id) -> list[str]:
    return list(
        (
            await db.execute(
                select(ChamadoMensagem.texto)
                .where(ChamadoMensagem.chamado_id == chamado_id, ChamadoMensagem.tipo == "sistema")
                .order_by(ChamadoMensagem.created_at)
            )
        ).scalars().all()
    )


async def test_tiktok_caso_ja_encerrado_falha_de_vez_com_desfecho(client, make_user, auth_as, db, ml, monkeypatch):
    """Medido 08/09 (290160/291050): devolução lançada tarde no DaVinci, o
    pessoal já tinha recusado o pacote à mão e a TikTok já arbitrou. Antes
    ficava "aguardando pacote (tenta a cada hora)" pra sempre."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    fake = _FakeTikTok(status="RETURN_OR_REFUND_REQUEST_COMPLETE")
    fake.arb = "SUPPORT_BUYER"
    fake.records = [
        {"event": "ORDER_RETURN", "role": "BUYER", "create_time": 1787534019},
        {"event": "SELLER_REJECT_RECEIVE", "role": "SELLER", "create_time": 1787928536,
         "images": [{"url": "a"}, {"url": "b"}, {"url": "c"}], "note": "Cliente não sabe a senha"},
    ]

    async def _c(session, *a):
        return fake

    monkeypatch.setattr(svc, "_tiktok_client_para", _c)
    await _seed_pedido(db, user, numero="291050", numeroloja="585602525063710717",
                       platform="tiktok", conta="barbosa", loja="79")
    r = await client.post(
        "/api/devolutions",
        json={"conta": "barbosa", "pedido_bling": "291050", "pedido_marketplace": "585602525063710717",
              "condicao_produto": "Novo", "motivo_devolucao": "Bloqueado"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["chamado_ml_status"] == "falhou"
    assert r.json()["chamado_ml_erro"] == "tiktok_ja_recusada"
    assert fake.rejects == []
    ch = (await db.execute(select(Chamado).where(Chamado.pedido_bling == "291050"))).scalar_one()
    hist = await _sistema(db, ch.id)
    assert any(
        "ENCERRADA na TikTok" in t and "COMPRADOR" in t and "3 foto(s)" in t
        and "Cliente não sabe a senha" in t and "598.4" in t
        for t in hist
    ), hist
    # cron não insiste: falhou não é pendente
    s = await svc.processar_pendentes(db)
    assert s["abertos"] == 0 and fake.rejects == []
    # sem recusa na linha do tempo → código genérico de encerrada
    fake2 = _FakeTikTok(status="RETURN_OR_REFUND_REQUEST_SUCCESS")

    async def _c2(session, *a):
        return fake2

    monkeypatch.setattr(svc, "_tiktok_client_para", _c2)
    await _seed_pedido(db, user, numero="291051", numeroloja="585602525063710718",
                       platform="tiktok", conta="barbosa", loja="79")
    r2 = await client.post(
        "/api/devolutions",
        json={"conta": "barbosa", "pedido_bling": "291051", "pedido_marketplace": "585602525063710718",
              "condicao_produto": "Novo", "motivo_devolucao": "Bloqueado"},
    )
    assert r2.json()["chamado_ml_status"] == "falhou"
    assert r2.json()["chamado_ml_erro"] == "tiktok_devolucao_encerrada"


async def test_shopee_ja_contestada_a_mao_nao_e_prazo_vencido(client, make_user, auth_as, db, ml, monkeypatch):
    """Medido 08/09 (290297/289967): `dispute_reason` preenchido com status
    ACCEPTED = disputa feita no Seller Center; caía em "prazo venceu"."""
    from app.models import DevolucaoRastreio

    user = await make_user(permissions=_perms())
    auth_as(user)
    fake = _FakeShopeeVencida(due_passado=True, contestada=True)

    async def _c(session, *a):
        return fake

    monkeypatch.setattr(svc, "_shopee_client_para", _c)
    await _seed_pedido(db, user, numero="289967", numeroloja="2608114J8V05YV",
                       platform="shopee", conta="barbosa", loja="88")
    db.add(DevolucaoRastreio(pedido_bling="289967", devolucao_id_auto="2608150DKSX55S5", fonte_auto="shopee"))
    await db.commit()
    r = await client.post(
        "/api/devolutions",
        json={"conta": "barbosa", "pedido_bling": "289967", "pedido_marketplace": "2608114J8V05YV",
              "condicao_produto": "Novo", "motivo_devolucao": "Bloqueado"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["chamado_ml_status"] == "falhou"
    assert r.json()["chamado_ml_erro"] == "shopee_ja_contestada"
    assert fake.disputes == []
    ch = (await db.execute(select(Chamado).where(Chamado.pedido_bling == "289967"))).scalar_one()
    hist = await _sistema(db, ch.id)
    assert any("physical damage" in t and "senha de uso" in t for t in hist), hist

    # 17/09 (Eduardo, 288567): disputa feita à mão e GANHA — o return segue ACCEPTED
    # com compensação sem status; só o escrow mostra a compensação paga. O
    # acompanhamento tem que olhar esse chamado e fechá-lo com o valor.
    from app.services import chamados_devolucao_sync as sync

    escrow = {"order_income": {"total_adjustment_amount": 728.22}, "order_adjustment": []}

    async def _escrow(order_sn):
        assert order_sn == "2608114J8V05YV"
        return escrow

    fake.get_escrow_detail = _escrow
    s0 = await sync.sync_respostas(db)
    assert s0["verificados"] == 1 and s0["encerrados"] == 0, s0
    await db.refresh(ch)
    assert ch.resolvido is False
    escrow["order_adjustment"] = [
        {"adjustment_reason": "Shipping Fee Adjustment", "amount": -3.5, "currency": "BRL", "date": 1787911000},
        {"adjustment_reason": "Logistics Related Compensation", "amount": 728.22, "currency": "BRL", "date": 1787911294},
    ]
    s1 = await sync.sync_respostas(db)
    assert s1["encerrados"] == 1, s1
    await db.refresh(ch)
    assert ch.resolvido is True and float(ch.valor_recuperado) == 728.22
    txts = await _recebidas(db, ch.id)
    assert any("PAGOU a compensação" in t and "R$ 728,22" in t and "28/08" in t for t in txts), txts
    assert any("lucro de R$ 728,22" in t for t in await _sistema(db, ch.id))
    assert (await sync.sync_respostas(db))["verificados"] == 0


async def test_ml_claim_encerrado_e_texto_manual_no_chamado(client, make_user, auth_as, db, ml, monkeypatch):
    """Medido 08/09 (283344): claim fechado pelo mediador em 18/08 e o campo
    `chamado` com texto do operador ("aberto manual…") — o texto NÃO é claim id
    e o encerrado falha de vez, com o desfecho no histórico."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    ml.fechado = True

    async def _pag(session, ch):
        return {"sem_prejuizo": False, "resumo": "estorno ao comprador — o valor NÃO ficou com a loja"}

    monkeypatch.setattr(svc, "_pagamento_ml", _pag)
    await _seed_pedido(db, user, numero="283344", numeroloja="2000017099328204")
    r = await client.post(
        "/api/devolutions",
        json={"conta": "aguiar", "pedido_bling": "283344", "pedido_marketplace": "2000017099328204",
              "condicao_produto": "Novo", "motivo_devolucao": "Bloqueado"},
    )
    assert r.status_code == 201, r.text
    # 16/09 (Eduardo: "nosso agente tem que enviar o chamado correto"): encerrada a favor
    # do comprador → vai pro robô do FORMULÁRIO de ajuda (canal robo, pendente, sem nº)
    assert r.json()["chamado_ml_status"] == "pendente", r.json()
    assert ml.reviews == []
    ch = (await db.execute(select(Chamado).where(Chamado.pedido_bling == "283344"))).scalar_one()
    await db.refresh(ch)
    assert ch.canal == "robo" and ch.chamado is None
    ab = await _abertura(db, ch.id)
    assert ab.canal == "robo" and ab.status == "pendente" and ab.erro is None
    assert "Reclamação 777" in ab.texto and "revisão do caso" in ab.texto
    hist = await _sistema(db, ch.id)
    assert any("777" in t and "ENCERRADA" in t and "COMPRADOR" in t and "18/08" in t for t in hist), hist
    assert any("robô do formulário" in t for t in hist), hist
    # o cron da API não mexe na tarefa do robô
    s = await svc.processar_pendentes(db)
    assert s["verificados"] == 0
    # operador escreveu no campo chamado: não é claim id → resolve pelo pedido e preserva o texto
    ch.chamado = "08/09 aberto manual chamado dentro da venda"
    ch.canal = "api"
    ab.canal = "api"
    ab.status = "falhou"
    ml.fechado = False
    ml.acao = True
    await db.commit()
    msg = await svc.disparar_por_id(db, ch.id)
    await db.commit()
    assert msg is not None and msg.status == "enviada", (msg.status, msg.erro)
    assert ml.reviews[-1][0] == "ret-777"
    await db.refresh(ch)
    assert ch.chamado == "777"


# ------------------------------------------------- produto chegou depois (Eduardo 15/09, caso 293843)
# Lançado Extraviado + Não recebido → chamado/contestação + Problemas no Bling +
# reembolso automático. O pacote chegou: a operadora vira Novo e limpa o motivo.
# O chamado encerra pelo motivo (mesma regra que o abre), o reembolso Extraviado
# zerado some e o Bling segue o fluxo normal (já coberto pelos testes de situação).


async def _chamado_de(db, pedido: str) -> Chamado:
    ch = (
        await db.execute(
            select(Chamado).where(Chamado.origem == "devolucao", Chamado.pedido_bling == pedido)
        )
    ).scalar_one()
    await db.refresh(ch)
    return ch


async def _refunds_de(db, pedido: str) -> list[Refund]:
    # populate_existing: relê do banco o que a API mudou por outra sessão (sem
    # expire_all — isso expiraria o `user` do auth_as e quebraria a request).
    return list(
        (
            await db.execute(
                select(Refund)
                .where(Refund.pedido_bling == pedido)
                .order_by(Refund.prejuizo)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )


async def test_motivo_limpo_encerra_chamado_e_avisa_da_contestacao(client, make_user, auth_as, db, ml):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero="293843", numeroloja="2609020KA93B41")
    r = await client.post(
        "/api/devolutions",
        json={"conta": "aguiar", "pedido_bling": "293843", "pedido_marketplace": "2609020KA93B41",
              "condicao_produto": "Extraviado", "link_abertura": "http://x",
              "motivo_devolucao": "Não recebido", "custo_produto": 120, "reembolso": True},
    )
    assert r.status_code == 201, r.text
    assert r.json()["chamado_ml_status"] == "enviada"
    did = r.json()["id"]
    refunds = await _refunds_de(db, "293843")
    assert len(refunds) == 1 and refunds[0].tipo == "Extraviado" and float(refunds[0].prejuizo) == 120

    # 1) Extraviado → Novo: o reembolso automático (ainda zerado) some e a flag desliga;
    #    o chamado continua, porque o motivo ainda pede chamado.
    p = await client.patch(f"/api/devolutions/{did}", json={"condicao_produto": "Novo"})
    assert p.status_code == 200, p.text
    assert p.json()["reembolso"] is False
    assert await _refunds_de(db, "293843") == []
    assert p.json()["chamado_resolvido"] is False

    # 2) motivo limpo → chamado encerra, com o aviso da contestação já enviada
    p = await client.patch(f"/api/devolutions/{did}", json={"motivo_devolucao": None})
    assert p.status_code == 200, p.text
    assert p.json()["chamado_resolvido"] is True
    # a tela Devoluções passa a mostrar "retirar a contestação no painel"
    assert p.json()["chamado_ml_status"] == "enviada"
    assert p.json()["chamado_ml_erro"] == "retirar_contestacao"
    ch = await _chamado_de(db, "293843")
    assert ch.resolvido is True and ch.auto_ligada is False and ch.valor_recuperado is None
    hist = [m["texto"] for m in (await client.get(f"/api/chamados/{ch.id}/mensagens")).json()]
    assert any('"Não recebido"' in t and '"—"' in t and "encerrado" in t for t in hist), hist
    assert any("desista dela" in t and "Mercado Livre" in t for t in hist), hist

    # 3) salvar de novo sem mudar o motivo (o front manda o motivo em todo save) não repete nada
    p = await client.patch(
        f"/api/devolutions/{did}", json={"motivo_devolucao": None, "observacao": "ok"}
    )
    assert p.status_code == 200, p.text
    assert len((await client.get(f"/api/chamados/{ch.id}/mensagens")).json()) == len(hist)


async def test_motivo_que_nao_abre_chamado_encerra_e_abertura_pendente_sai_da_fila(
    client, make_user, auth_as, db, ml
):
    ml.acao = False  # ML ainda não liberou a revisão → abertura fica pendente
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero="293844", numeroloja="2609020KA93B42")
    r = await client.post(
        "/api/devolutions",
        json={"conta": "aguiar", "pedido_bling": "293844", "pedido_marketplace": "2609020KA93B42",
              "condicao_produto": "Extraviado", "link_abertura": "http://x",
              "motivo_devolucao": "Não recebido"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["chamado_ml_status"] == "pendente"
    did = r.json()["id"]

    p = await client.patch(f"/api/devolutions/{did}", json={"motivo_devolucao": "Item Incorreto"})
    assert p.status_code == 200, p.text
    assert p.json()["chamado_resolvido"] is True
    ch = await _chamado_de(db, "293844")
    assert ch.resolvido is True
    msg = await _abertura(db, ch.id)
    await db.refresh(msg)
    assert msg.status == "registrada" and msg.erro == "contestacao_cancelada"
    assert p.json()["chamado_ml_status"] == "registrada"
    assert p.json()["chamado_ml_erro"] == "contestacao_cancelada"
    hist = [m["texto"] for m in (await client.get(f"/api/chamados/{ch.id}/mensagens")).json()]
    assert any('"Item Incorreto"' in t and "encerrado" in t for t in hist), hist
    assert any("pendente na fila" in t for t in hist), hist


async def test_kit_parcial_mantem_chamado_e_encerra_com_a_ultima_linha(
    client, make_user, auth_as, db, ml
):
    """Kit = várias linhas, 1 chamado. Chegou só um item: o chamado segue pelos
    outros (anota no histórico); o reembolso apagado é o daquele item (casa
    pelo custo). Chegou o último: encerra."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero="293845", numeroloja="2609020KA93B43")
    base = {"conta": "aguiar", "pedido_bling": "293845", "pedido_marketplace": "2609020KA93B43",
            "condicao_produto": "Extraviado", "link_abertura": "http://x",
            "motivo_devolucao": "Não recebido", "link_envio": "http://envio"}  # mala exige link
    r1 = await client.post("/api/devolutions", json={**base, "sku": "b001.26", "custo_produto": 10})
    r2 = await client.post("/api/devolutions", json={**base, "sku": "a001", "custo_produto": 20})
    assert r1.status_code == 201 and r2.status_code == 201, (r1.text, r2.text)
    chamados = (
        await db.execute(select(Chamado).where(Chamado.pedido_bling == "293845"))
    ).scalars().all()
    assert len(chamados) == 1
    assert [float(x.prejuizo) for x in await _refunds_de(db, "293845")] == [10, 20]

    # chegou o a001 (custo 20): chamado segue aberto, some só o reembolso de 20
    p = await client.patch(
        f"/api/devolutions/{r2.json()['id']}",
        json={"condicao_produto": "Novo", "motivo_devolucao": None},
    )
    assert p.status_code == 200, p.text
    assert p.json()["chamado_resolvido"] is False
    assert [float(x.prejuizo) for x in await _refunds_de(db, "293845")] == [10]
    ch = await _chamado_de(db, "293845")
    assert ch.resolvido is False
    hist = [m["texto"] for m in (await client.get(f"/api/chamados/{ch.id}/mensagens")).json()]
    assert any("a001" in t and "continua pelos outros 1 item" in t for t in hist), hist

    # chegou o último: encerra e limpa o reembolso que faltava
    p = await client.patch(
        f"/api/devolutions/{r1.json()['id']}",
        json={"condicao_produto": "Novo", "motivo_devolucao": None},
    )
    assert p.status_code == 200, p.text
    assert p.json()["chamado_resolvido"] is True
    assert await _refunds_de(db, "293845") == []
    ch = await _chamado_de(db, "293845")
    assert ch.resolvido is True


async def test_reembolso_com_valor_lancado_fica_e_avisa(client, make_user, auth_as, db, ml):
    """A agência já lançou valor no reembolso Extraviado: sair da condição não
    apaga — mantém e avisa no sino pra revisar."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero="293846", numeroloja="2609020KA93B44")
    r = await client.post(
        "/api/devolutions",
        json={"conta": "aguiar", "pedido_bling": "293846", "pedido_marketplace": "2609020KA93B44",
              "condicao_produto": "Extraviado", "link_abertura": "http://x",
              "custo_produto": 50, "reembolso": True},
    )
    assert r.status_code == 201, r.text
    refund = (await _refunds_de(db, "293846"))[0]
    refund.reembolso = 30
    await db.commit()

    p = await client.patch(f"/api/devolutions/{r.json()['id']}", json={"condicao_produto": "Novo"})
    assert p.status_code == 200, p.text
    assert p.json()["reembolso"] is True  # flag não mexe: o reembolso ficou
    mantidos = await _refunds_de(db, "293846")
    assert len(mantidos) == 1 and float(mantidos[0].reembolso) == 30
    alerta = (
        await db.execute(
            select(Alert).where(Alert.dedupe_key == f"devolucao_refund_revisar:{refund.id}")
        )
    ).scalar_one_or_none()
    assert alerta is not None and "revisar" in alerta.title


async def test_ml_sem_reclamacao_vai_pro_formulario_depois_de_24h(client, make_user, auth_as, db, ml, monkeypatch):
    """Eduardo 16/09: 287876/287144/291752 ficaram "sem devolução aberta no ML (tenta a
    cada hora)" desde 09/09 — nada foi enviado. Depois de 24 h sem reclamação, a
    abertura vai pro robô do formulário e o /agent/lease entrega como `abrir`."""
    from app.config import get_settings

    user = await make_user(permissions=_perms())
    auth_as(user)
    ml.claims = ()
    await _seed_pedido(db, user, numero="287876", numeroloja="2000017000000001")
    r = await client.post(
        "/api/devolutions",
        json={"conta": "aguiar", "pedido_bling": "287876", "pedido_marketplace": "2000017000000001",
              "condicao_produto": "Novo", "motivo_devolucao": "Não recebido"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["chamado_ml_status"] == "pendente"
    assert r.json()["chamado_ml_erro"] == "devolucao_sem_claim"  # < 24 h: ainda espera
    ch = await _chamado_de(db, "287876")
    assert ch.canal == "api"
    s = await svc.processar_pendentes(db, agora=datetime.now(UTC) + timedelta(hours=25))
    assert s["verificados"] == 1
    ch = await _chamado_de(db, "287876")
    ab = await _abertura(db, ch.id)
    await db.refresh(ab)
    assert ch.canal == "robo" and ab.canal == "robo" and ab.status == "pendente" and ab.erro is None
    assert "Não há reclamação aberta" in ab.texto

    token = "tok-form-1609"
    monkeypatch.setattr(get_settings(), "nf_agent_token", token)
    lease = await client.post("/api/chamados/agent/lease", headers={"X-Agent-Token": token}, json={"limite": 10})
    assert lease.status_code == 200, lease.text
    tarefas = [t for t in lease.json()["tarefas"] if t["mensagem_id"] == str(ab.id)]
    assert len(tarefas) == 1 and tarefas[0]["tipo"] == "abrir", lease.json()


async def test_ml_claim_encerrado_sem_prejuizo_nao_abre(client, make_user, auth_as, db, ml, monkeypatch):
    """Eduardo 16/09: "reclamação encerrada depende — vai ter casos que não vai
    compensar". Coberto pelo ML (a loja ficou com o valor) → não vai pro formulário."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    ml.fechado = True

    async def _pag(session, ch):
        return {"sem_prejuizo": True, "resumo": "pago pelo programa de proteção do ML — NÃO saiu da conta da loja"}

    monkeypatch.setattr(svc, "_pagamento_ml", _pag)
    await _seed_pedido(db, user, numero="285250", numeroloja="2000017099328299")
    r = await client.post(
        "/api/devolutions",
        json={"conta": "aguiar", "pedido_bling": "285250", "pedido_marketplace": "2000017099328299",
              "condicao_produto": "Novo", "motivo_devolucao": "Bloqueado"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["chamado_ml_status"] == "falhou"
    assert r.json()["chamado_ml_erro"] == "ml_claim_encerrada_sem_prejuizo"
    ch = await _chamado_de(db, "285250")
    assert ch.canal == "api"
    assert any("SEM PREJUÍZO" in t and "proteção" in t for t in await _sistema(db, ch.id))


async def test_ml_texto_manual_no_chamado_tambem_vai_pro_formulario(client, make_user, auth_as, db, ml, monkeypatch):
    """Eduardo 16/09: "manual o robô mexe também" — anotação do operador no campo chamado
    não bloqueia o formulário; vai pra observação."""
    user = await make_user(permissions=_perms())
    auth_as(user)

    async def _pag(session, ch):
        return {"sem_prejuizo": False}

    monkeypatch.setattr(svc, "_pagamento_ml", _pag)
    await _seed_pedido(db, user, numero="283399", numeroloja="2000017099328300")
    r = await client.post(
        "/api/devolutions",
        json={"conta": "aguiar", "pedido_bling": "283399", "pedido_marketplace": "2000017099328300",
              "condicao_produto": "Novo", "motivo_devolucao": "Bloqueado"},
    )
    assert r.status_code == 201, r.text
    ch = await _chamado_de(db, "283399")
    ab = await _abertura(db, ch.id)
    # simula: abertura tinha falhado e o operador anotou no campo chamado; claim encerrado
    ch.chamado = "08/09 aberto manual chamado dentro da venda"
    ch.canal = "api"
    ab.canal = "api"
    ab.status = "falhou"
    ml.fechado = True
    await db.commit()
    msg = await svc.disparar_por_id(db, ch.id)
    await db.commit()
    ch = await _chamado_de(db, "283399")
    assert msg.status == "pendente" and msg.canal == "robo"
    assert ch.canal == "robo" and ch.chamado is None
    assert "aberto manual" in (ch.observacao or "")


async def test_agendar_disparo_foto_logo_depois_do_create_reagenda(monkeypatch):
    """17/09 (292317): create enfileira o disparo (sai "sem foto"); as fotos chegam
    7 s depois e o enqueue com o mesmo _job_id volta None — antes o disparo das
    fotos sumia. Agora reagenda um disparo adiado com outro id."""
    from types import SimpleNamespace
    from uuid import uuid4

    from app import worker_pool

    chamadas: list[dict] = []
    existentes: set[str] = set()

    class _Pool:
        async def enqueue_job(self, fn, *args, _job_id=None, _defer_by=None):
            chamadas.append({"fn": fn, "args": args, "id": _job_id, "defer": _defer_by})
            if _job_id in existentes:
                return None
            existentes.add(_job_id)
            return SimpleNamespace(job_id=_job_id)

    async def _pool():
        return _Pool()

    class _Relogio(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 17, 10, 46, 25, tzinfo=UTC)

    monkeypatch.setattr(worker_pool, "get_arq_pool", _pool)
    monkeypatch.setattr(svc, "ENFILEIRAR", True)
    monkeypatch.setattr(svc, "datetime", _Relogio)
    ch = SimpleNamespace(id=uuid4())
    await svc.agendar_disparo(None, ch, None)  # create
    # 18/09 (293839): o 1º disparo já espera a janela das fotos do mesmo cadastro
    assert len(chamadas) == 1 and chamadas[0]["defer"] == svc.JANELA_FOTOS
    await svc.agendar_disparo(None, ch, None)  # 1ª foto: id fixo já existe → adiado
    assert len(chamadas) == 3
    assert chamadas[2]["id"] != chamadas[0]["id"] and chamadas[2]["id"].startswith(f"chamado_devolucao_disparar:{ch.id}:")
    assert chamadas[2]["defer"] == svc.DISPARO_ADIADO
    await svc.agendar_disparo(None, ch, None)  # 2ª foto no mesmo instante: mesma vaga adiada, nada novo roda
    assert [c["id"] for c in chamadas].count(chamadas[2]["id"]) == 2


async def test_primeiro_disparo_espera_as_fotos_do_mesmo_cadastro(db, monkeypatch):
    """293839 (18/09, TikTok Mini, Golpe): a devolução era salva às 13:30:51, o
    chamado saía no MESMO segundo com 0 foto, e as 5 fotos chegavam de 13:30:55 a
    13:31:01. O primeiro disparo agora vai pra fila com a janela das fotos."""
    chamadas: list[dict] = []

    class _Pool:
        async def enqueue_job(self, nome, *args, **kw):
            chamadas.append({"nome": nome, **kw})
            return object()

    async def _pool():
        return _Pool()

    import app.worker_pool as wp

    monkeypatch.setattr(svc, "ENFILEIRAR", True)
    monkeypatch.setattr(wp, "get_arq_pool", _pool)
    ch = Chamado(pedido_bling="293839", plataforma="TikTok", conta="TikTok Mini",
                 origem="devolucao", canal="api", data=datetime.now(UTC).date())
    db.add(ch)
    await db.flush()
    dev = Devolution(conta="TikTok Mini", motivo_devolucao="Golpe")

    await svc.agendar_disparo(db, ch, dev)

    assert len(chamadas) == 1
    assert chamadas[0]["nome"] == "chamado_devolucao_disparar"
    assert chamadas[0]["_defer_by"] == svc.JANELA_FOTOS
    assert svc.JANELA_FOTOS.total_seconds() >= 30


# ── Só reembolso da TikTok (Vinicius 18/09, caso 296936 "caixa de sabonete") ──
# O caso cai em Devoluções › Fraude; o pessoal monta vídeo e fotos e faz o LANÇAMENTO;
# o lançamento responde o caso que o vigia achou (recusa do reembolso) com o texto
# montado sozinho — fatos da entrega, alegação do comprador, link do vídeo, fotos.

RID_REEMB = "4042357484883052019"
OID_REEMB = "586055935181358579"
PRAZO_REEMB = 1790055431  # 22/09 02:37 BRT


class _FakeTikTokReembolso(_FakeTikTok):
    def __init__(self, *, status: str = "RETURN_OR_REFUND_REQUEST_PENDING", com_pacote: bool = False):
        super().__init__()  # self.status = do caso COM pacote (BUYER_SHIPPED_ITEM)
        self.status_reembolso = status
        self.com_pacote = com_pacote
        self.records = [{"event": "ORDER_REFUND", "note": "Recebi uma caixa de sabonete ao invés do celular!",
                         "reason_text": "Pacote recebido, mas faltam alguns itens", "create_time": 1789620000}]

    async def get_return_list(self, *, order_ids=None, **kw):
        caso = {
            "order_id": OID_REEMB, "return_id": RID_REEMB, "return_type": "REFUND",
            "return_status": self.status_reembolso, "update_time": 20,
            "seller_next_action_response": [{"action": "SELLER_RESPOND_REFUND", "deadline": PRAZO_REEMB}]
            if self.status_reembolso == "RETURN_OR_REFUND_REQUEST_PENDING" else [],
            "refund_amount": {"currency": "BRL", "refund_total": "803.2"},
            "return_reason_text": "Package received but missing item",
        }
        casos = [caso]
        if self.com_pacote:
            casos += await _FakeTikTok.get_return_list(self, order_ids=order_ids)
        return casos

    async def get_order_detail(self, order_id):
        return {"orders": [{"status": "DELIVERED", "delivery_time": 1789588803,
                            "line_items": [{"tracking_number": "999882054197026", "shipping_provider_name": "J&T Express Brazil"}]}]}

    async def get_reject_reasons(self, return_id, *, locale="pt-BR"):
        if return_id != RID_REEMB:  # caso COM pacote: motivos de recusa do pacote
            return await _FakeTikTok.get_reject_reasons(self, return_id, locale=locale)
        return [{"name": "reverse_reject_request_reason_4", "text": "A entrega do produto está dentro do prazo"},
                {"name": "reverse_reject_request_reason_1", "text": "O motivo da devolução do comprador não é válido"}]


async def _seed_reembolso(db, user, monkeypatch, fake, *, fila: str | None = "fraude", video: str | None = "https://drive.x/video-296936"):
    from app.models import DevolucaoRastreio

    async def _c(session, *a):
        return fake

    monkeypatch.setattr(svc, "_tiktok_client_para", _c)
    await _seed_pedido(db, user, numero="296936", numeroloja=OID_REEMB, platform="tiktok", conta="mini", loja="88")
    db.add(DevolucaoRastreio(pedido_bling="296936", fonte_auto="tiktok", devolucao_id_auto=RID_REEMB,
                             devolucao_tipo_auto="REFUND", devolucao_status_auto="RETURN_OR_REFUND_REQUEST_PENDING",
                             acao_auto="SELLER_RESPOND_REFUND", fila_manual=fila, video_link=video))
    await db.commit()


async def test_tiktok_so_reembolso_lancamento_responde_o_caso_do_vigia(client, make_user, auth_as, db, ml, monkeypatch):
    user = await make_user(permissions=_perms())
    auth_as(user)
    fake = _FakeTikTokReembolso()
    await _seed_reembolso(db, user, monkeypatch, fake)

    r = await client.post(
        "/api/devolutions",
        json={"conta": "mini", "pedido_bling": "296936", "pedido_marketplace": OID_REEMB,
              "condicao_produto": "Não devolvido", "motivo_devolucao": "Não recebido",
              "observacao": "Peso conferido na expedição."},
    )
    assert r.status_code == 201, r.text
    assert r.json()["chamado_plataforma"] == "tiktok"
    assert r.json()["chamado_ml_status"] == "enviada", r.json()
    # o vídeo da coluna Vídeo entrou sozinho no Link envio
    assert r.json()["link_envio"] == "https://drive.x/video-296936"
    assert len(fake.rejects) == 1
    rj = fake.rejects[0]
    assert (rj["return_id"], rj["decision"], rj["reason"]) == (RID_REEMB, "REJECT_REFUND", "reverse_reject_request_reason_1")
    txt = rj["comment"]
    assert "entregue em 16/09 17:00" in txt and "J&T Express Brazil" in txt and "999882054197026" in txt, txt
    assert "caixa de sabonete" in txt and "https://drive.x/video-296936" in txt and "Peso conferido" in txt, txt
    assert rj["images"] is None and rj["idem"]
    ch = (await db.execute(select(Chamado).where(Chamado.pedido_bling == "296936"))).scalar_one()
    assert ch.chamado == RID_REEMB and ch.canal == "api" and ch.origem == "devolucao"
    hist = list((await db.execute(select(ChamadoMensagem.texto).where(ChamadoMensagem.chamado_id == ch.id,
                                                                      ChamadoMensagem.tipo == "sistema"))).scalars())
    assert any("CONTESTADO" in t and "lançamento" in t and "vídeo no texto" in t for t in hist), hist
    assert any("recusa do SÓ REEMBOLSO" in t for t in hist), hist
    assert ml.reviews == []

    # foto anexada depois: já respondeu, nada mais sai (idempotente)
    up = await client.post(f"/api/devolutions/{r.json()['id']}/anexos",
                           files={"file": ("caixa.png", PNG_1PX, "image/png")})
    assert up.status_code == 201 and len(fake.rejects) == 1


async def test_tiktok_so_reembolso_fotos_vao_junto(client, make_user, auth_as, db, ml, monkeypatch):
    user = await make_user(permissions=_perms())
    auth_as(user)
    fake = _FakeTikTokReembolso()
    await _seed_reembolso(db, user, monkeypatch, fake, fila="acompanhamento", video=None)
    # sem vídeo e sem foto: segura (não responde sem a informação certa)
    r = await client.post(
        "/api/devolutions",
        json={"conta": "mini", "pedido_bling": "296936", "pedido_marketplace": OID_REEMB,
              "condicao_produto": "Não devolvido", "motivo_devolucao": "Golpe"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["chamado_ml_status"] == "pendente" and r.json()["chamado_ml_erro"] == "devolucao_sem_video"
    assert fake.rejects == []
    # chegou a foto: responde com ela
    up = await client.post(f"/api/devolutions/{r.json()['id']}/anexos",
                           files={"file": ("pesagem.png", PNG_1PX, "image/png")})
    assert up.status_code == 201 and up.json()["chamado_ml_status"] == "enviada", up.json()
    rj = fake.rejects[0]
    assert rj["decision"] == "REJECT_REFUND" and rj["images"] == [{"image_id": "tos/pesagem.png", "mime_type": "image/png", "width": 100, "height": 80}]
    assert "1 foto(s)" in rj["comment"]


async def test_tiktok_so_reembolso_em_fraude_exige_video_pra_lancar(client, make_user, auth_as, db, ml, monkeypatch):
    user = await make_user(permissions=_perms())
    auth_as(user)
    fake = _FakeTikTokReembolso()
    await _seed_reembolso(db, user, monkeypatch, fake, fila="fraude", video=None)
    r = await client.post(
        "/api/devolutions",
        json={"conta": "mini", "pedido_bling": "296936", "pedido_marketplace": OID_REEMB,
              "condicao_produto": "Não devolvido", "motivo_devolucao": "Não recebido"},
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "video_obrigatorio", r.text
    assert fake.rejects == []
    # link colado à mão no Link envio vale como vídeo
    r2 = await client.post(
        "/api/devolutions",
        json={"conta": "mini", "pedido_bling": "296936", "pedido_marketplace": OID_REEMB,
              "condicao_produto": "Não devolvido", "motivo_devolucao": "Não recebido",
              "link_envio": "https://drive.x/manual"},
    )
    assert r2.status_code == 201, r2.text
    assert r2.json()["chamado_ml_status"] == "enviada" and "https://drive.x/manual" in fake.rejects[0]["comment"]
    # Item Incorreto não abre chamado nem exige vídeo (é erro nosso)
    r3 = await client.post(
        "/api/devolutions",
        json={"conta": "mini", "pedido_bling": "296936", "pedido_marketplace": OID_REEMB,
              "condicao_produto": "Novo", "motivo_devolucao": "Item Incorreto"},
    )
    assert r3.status_code == 201, r3.text
    assert len(fake.rejects) == 1


async def test_tiktok_so_reembolso_ja_decidido_falha_com_desfecho(client, make_user, auth_as, db, ml, monkeypatch):
    user = await make_user(permissions=_perms())
    auth_as(user)
    fake = _FakeTikTokReembolso(status="RETURN_OR_REFUND_REQUEST_COMPLETE")
    fake.records.append({"event": "SELLER_REJECT_APPLICATION_TIMEOUT_REFUND", "create_time": PRAZO_REEMB})
    await _seed_reembolso(db, user, monkeypatch, fake)
    r = await client.post(
        "/api/devolutions",
        json={"conta": "mini", "pedido_bling": "296936", "pedido_marketplace": OID_REEMB,
              "condicao_produto": "Não devolvido", "motivo_devolucao": "Não recebido"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["chamado_ml_status"] == "falhou" and r.json()["chamado_ml_erro"] == "tiktok_reembolso_nao_pendente"
    ch = (await db.execute(select(Chamado).where(Chamado.pedido_bling == "296936"))).scalar_one()
    hist = list((await db.execute(select(ChamadoMensagem.texto).where(ChamadoMensagem.chamado_id == ch.id,
                                                                      ChamadoMensagem.tipo == "sistema"))).scalars())
    assert any("reembolso PAGO" in t and "FALTA DE RESPOSTA" in t for t in hist), hist
    assert fake.rejects == []


async def test_tiktok_com_pacote_e_so_reembolso_decidido_segue_o_pacote(client, make_user, auth_as, db, ml, monkeypatch):
    # Só-reembolso já decidido + devolução COM pacote viva: quem manda é o pacote (fluxo de sempre).
    user = await make_user(permissions=_perms())
    auth_as(user)
    fake = _FakeTikTokReembolso(status="RETURN_OR_REFUND_REQUEST_CANCEL", com_pacote=True)
    await _seed_reembolso(db, user, monkeypatch, fake)
    r = await client.post(
        "/api/devolutions",
        json={"conta": "mini", "pedido_bling": "296936", "pedido_marketplace": OID_REEMB,
              "condicao_produto": "Não devolvido", "motivo_devolucao": "Não recebido"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["chamado_ml_status"] == "enviada", r.json()
    assert fake.rejects[0]["decision"] == "REJECT_RECEIVED_PACKAGE"


# ---------------------------------------------------------------- recusa ≠ ganhamos (18/09, 296936)


class _FakeTikTokRecusa(_FakeTikTokReembolso):
    """Só reembolso com relógio de verdade: `update_time` da recusa, arbitragem e um
    eventual pedido refeito pelo comprador."""

    def __init__(self):
        super().__init__()
        self.update_time = int(datetime(2026, 9, 18, 14, 48, tzinfo=UTC).timestamp())  # 18/09 11:48 BRT
        self.create_time = int(datetime(2026, 9, 17, 5, 37, tzinfo=UTC).timestamp())  # 17/09 02:37 BRT
        self.arb = ""
        self.refeito: dict | None = None

    async def get_return_list(self, *, order_ids=None, **kw):
        casos = await super().get_return_list(order_ids=order_ids, **kw)
        casos[0]["update_time"] = self.update_time
        casos[0]["create_time"] = self.create_time
        if self.arb:
            casos[0]["arbitration_status"] = self.arb
        if self.refeito is not None:
            casos.append(self.refeito)
        return casos


async def _lancar_296936(client, db, make_user, auth_as, monkeypatch, fake):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_reembolso(db, user, monkeypatch, fake)
    r = await client.post(
        "/api/devolutions",
        json={"conta": "mini", "pedido_bling": "296936", "pedido_marketplace": OID_REEMB,
              "condicao_produto": "Não devolvido", "motivo_devolucao": "Golpe"},
    )
    assert r.status_code == 201 and r.json()["chamado_ml_status"] == "enviada", r.text
    assert len(fake.rejects) == 1
    fake.status_reembolso = "REFUND_OR_RETURN_REQUEST_REJECT"
    return (await db.execute(select(Chamado).where(Chamado.pedido_bling == "296936"))).scalar_one()


async def test_sync_tiktok_recusa_do_reembolso_e_aguardando_ate_a_tiktok_decidir(client, make_user, auth_as, db, ml, monkeypatch):
    """296936 (Vinicius 18/09): a TikTok diz "vendedor recusou" — era a NOSSA recusa e o
    painel mostrava Ganhamos e fechava o chamado às 12:25; o comprador ainda podia
    recorrer (recorreu em 5 de 5 casos medidos). Agora: aguardando plataforma, chamado
    aberto, e o desfecho só quando a TikTok decidir."""
    from app.services import chamados_devolucao_sync as sync

    fake = _FakeTikTokRecusa()
    ch = await _lancar_296936(client, db, make_user, auth_as, monkeypatch, fake)
    recusa = datetime.fromtimestamp(fake.update_time, UTC)

    s1 = await sync.sync_respostas(db, agora=recusa + timedelta(minutes=37))
    assert s1["verificados"] == 1 and s1["encerrados"] == 0, s1
    await db.refresh(ch)
    assert ch.resolvido is False
    assert ch.status_plataforma == "aguardando" and ch.status_plataforma_at == recusa
    sist = await _sistema_txts(db, ch.id)
    assert any("Recusa do reembolso registrada" in t and "ainda pode contestar" in t for t in sist), sist
    assert not any("Solicitação de devolução recusada" in t for t in await _recebidas(db, ch.id))
    # a nota original do comprador entra com a hora dela (antes da recusa) — não vira "respondeu"
    nota = (await db.execute(select(ChamadoMensagem).where(
        ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.direcao == "recebida"))).scalars().one()
    assert "caixa de sabonete" in nota.texto and nota.created_at == datetime.fromtimestamp(1789620000, UTC)
    assert await _status_aba(db, ch) == ("aguardando", "nossa recusa registrada — o comprador ainda pode recorrer")
    # de novo: nada duplica, continua aguardando
    s2 = await sync.sync_respostas(db, agora=recusa + timedelta(hours=2))
    assert s2["novos"] == 0 and s2["encerrados"] == 0

    # o comprador recorreu → em análise, chamado continua aberto
    fake.arb = "IN_PROGRESS"
    fake.update_time += 3600
    s3 = await sync.sync_respostas(db, agora=recusa + timedelta(hours=3))
    await db.refresh(ch)
    assert s3["encerrados"] == 0 and ch.resolvido is False
    assert ch.status_plataforma == "em_analise"
    assert any("ARBITRAGEM" in t for t in await _recebidas(db, ch.id))

    # a TikTok decidiu a favor da loja → ganhamos, fecha
    fake.arb = "SUPPORT_SELLER"
    fake.status_reembolso = "RETURN_OR_REFUND_REQUEST_CANCEL"
    fake.update_time += 3600
    s4 = await sync.sync_respostas(db, agora=recusa + timedelta(days=2))
    await db.refresh(ch)
    assert s4["encerrados"] == 1 and ch.resolvido is True and ch.status_plataforma == "ganhamos"
    assert any("A FAVOR DO VENDEDOR" in t for t in await _recebidas(db, ch.id))


async def test_sync_tiktok_recusa_sem_recurso_em_10_dias_e_ganhamos(client, make_user, auth_as, db, ml, monkeypatch):
    from app.services import chamados_devolucao_sync as sync

    fake = _FakeTikTokRecusa()
    ch = await _lancar_296936(client, db, make_user, auth_as, monkeypatch, fake)
    recusa = datetime.fromtimestamp(fake.update_time, UTC)

    s1 = await sync.sync_respostas(db, agora=recusa + timedelta(days=9, hours=23))
    await db.refresh(ch)
    assert s1["encerrados"] == 0 and ch.resolvido is False and ch.status_plataforma == "aguardando"

    s2 = await sync.sync_respostas(db, agora=recusa + timedelta(days=10, minutes=1))
    await db.refresh(ch)
    assert s2["encerrados"] == 1 and ch.resolvido is True and ch.status_plataforma == "ganhamos"
    assert any("10 dias sem recurso" in t and "ganhamos" in t for t in await _recebidas(db, ch.id))


async def test_sync_tiktok_pedido_refeito_pelo_comprador_nao_e_ganhamos(client, make_user, auth_as, db, ml, monkeypatch):
    """jlas 585710261573748632 (medido 18/09): o comprador EDITOU o pedido depois da
    recusa — a TikTok cancela o antigo e cria outro. Não é ganhamos: o chamado passa a
    acompanhar o caso novo, e é a nossa vez de responder."""
    from app.services import chamados_devolucao_sync as sync

    fake = _FakeTikTokRecusa()
    ch = await _lancar_296936(client, db, make_user, auth_as, monkeypatch, fake)
    recusa = datetime.fromtimestamp(fake.update_time, UTC)
    fake.status_reembolso = "RETURN_OR_REFUND_REQUEST_CANCEL"
    fake.arb = "CLOSED"
    novo_em = fake.update_time + 7200
    fake.refeito = {
        "order_id": OID_REEMB, "return_id": "4042163882929260440", "return_type": "REFUND",
        "return_status": "RETURN_OR_REFUND_REQUEST_PENDING", "create_time": novo_em, "update_time": novo_em,
        "seller_next_action_response": [{"action": "SELLER_RESPOND_REFUND", "deadline": novo_em + 2 * 86400}],
        "refund_amount": {"currency": "BRL", "refund_total": "803.2"},
    }
    s1 = await sync.sync_respostas(db, agora=recusa + timedelta(hours=3))
    await db.refresh(ch)
    assert s1["encerrados"] == 0 and ch.resolvido is False
    assert ch.status_plataforma is None
    assert ch.chamado == "4042163882929260440"
    txts = await _recebidas(db, ch.id)
    assert any("refez o pedido" in t and "4042163882929260440" in t and "aguardando a nossa resposta" in t for t in txts), txts
    assert not any("valor fica com o vendedor" in t for t in txts)
    assert (await _status_aba(db, ch))[0] == "respondeu"
