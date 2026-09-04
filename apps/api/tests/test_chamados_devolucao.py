"""Devolução → chamado AUTOMÁTICO no Mercado Livre com fotos (services/
chamados_devolucao). Eduardo 04/09: "Todos esses motivos aí, se for adicionado
lá, vai abrir o chamado automático … vai ter foto sim e vídeo"."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

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
    assert r.status_code == 201, r.text
    body = r.json()
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


async def test_golpe_espera_sub_motivo_e_bloqueado_vai_como_srf6(
    client, make_user, auth_as, db, ml
):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero="293102", numeroloja="2000102")
    r = await client.post(
        "/api/devolutions",
        json={"conta": "aguiar", "pedido_bling": "293102", "pedido_marketplace": "2000102",
              "condicao_produto": "Usado", "motivo_devolucao": "Golpe"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["chamado_ml_status"] == "pendente"
    assert r.json()["chamado_ml_erro"] == "devolucao_sem_tipo_golpe"
    dev_id = r.json()["id"]

    # operador escolhe "pacote vazio" (SRF5, sem foto obrigatória) → abre
    p = await client.patch(f"/api/devolutions/{dev_id}", json={"motivo_ml": "SRF5"})
    assert p.status_code == 200, p.text
    assert p.json()["motivo_ml"] == "SRF5"
    assert p.json()["chamado_ml_status"] == "enviada"
    assert ml.reviews[-1][1] == "SRF5"

    # Bloqueado (mala travada por senha) → SRF6 com o texto explicando
    await _seed_pedido(db, user, numero="293103", numeroloja="2000103")
    r2 = await client.post(
        "/api/devolutions",
        json={"conta": "aguiar", "pedido_bling": "293103", "pedido_marketplace": "2000103",
              "condicao_produto": "Usado", "motivo_devolucao": "Bloqueado"},
    )
    assert r2.status_code == 201, r2.text
    assert r2.json()["chamado_ml_status"] == "enviada"
    assert ml.reviews[-1][1] == "SRF6"
    assert "bloqueado por senha" in ml.reviews[-1][2]


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


async def test_conta_shopee_so_registra_na_aba(client, make_user, auth_as, db, ml):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero="293106", numeroloja="2609AAA", platform="shopee",
                       conta="minas", loja="66")
    r = await client.post(
        "/api/devolutions",
        json={"conta": "minas", "pedido_bling": "293106", "pedido_marketplace": "2609AAA",
              "condicao_produto": "Usado", "motivo_devolucao": "Danificado (Outros)"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["tem_chamado"] is True
    assert r.json()["chamado_ml_status"] is None
    ch = (await db.execute(select(Chamado).where(Chamado.pedido_bling == "293106"))).scalar_one()
    assert ch.canal == "manual"
    assert await _abertura(db, ch.id) is None
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
    dev = Devolution(conta="aguiar", motivo_devolucao="Golpe", motivo_ml="srf4",
                     pedido_marketplace="2000000001", sku="b001.26", produtos="Mala",
                     observacao="veio uma mala velha")
    assert svc.reason_para(dev) == "SRF4"
    dev.motivo_ml = "xyz"
    assert svc.reason_para(dev) is None
    dev.motivo_devolucao = "Item Incorreto"
    assert svc.reason_para(dev) is None
    dev.motivo_devolucao = "Não recebido"
    assert svc.reason_para(dev) == "SRF7"
    t = svc.texto_padrao(dev, "SRF7")
    assert t.startswith("O pacote da devolução ainda não chegou")
    assert "Pedido 2000000001" in t and "SKU b001.26" in t and "veio uma mala velha" in t
    assert "foto" not in t
    assert "2 foto(s)" in svc.texto_padrao(dev, "SRF2", fotos=2)
