# ruff: noqa: E501
"""Só reembolso da TikTok → chamado com prazo, aviso e contestação pela réplica
(services/chamados_tiktok_reembolso). Eduardo 16/09: o 294865 foi aprovado pela
TikTok por falta de resposta (R$ 744) porque o robô só olhava devolução com pacote."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import BlingOrder, Chamado, ChamadoAnexo, ChamadoMensagem, Integration, IntegrationPlatform, StoreInfo
from app.services import chamados_devolucao, logistica_tiktok, threema
from app.services import chamados_tiktok_reembolso as svc

pytestmark = pytest.mark.asyncio

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360f8cfc00000030101000ec8f0d50000000049454e44ae426082"
)
OID = "585845785461163452"
RID = "4042339029758936508"
AGORA = datetime(2026, 9, 16, 17, 0, tzinfo=UTC)
PRAZO = int(datetime(2026, 9, 18, 13, 19, tzinfo=UTC).timestamp())  # 18/09 10:19 BRT


class _FakeTikTok:
    def __init__(self, *, status: str = "RETURN_OR_REFUND_REQUEST_PENDING", prazo: int = PRAZO):
        self.status = status
        self.prazo = prazo
        self.records = [{"event": "ORDER_REFUND", "note": "Pacote chegou vazio",
                         "reason_text": "Pacote recebido, mas faltam alguns itens", "create_time": 1789478344},]
        self.entregue = True
        self.uploads: list[tuple] = []
        self.rejects: list[dict] = []

    def _caso(self) -> dict:
        return {
            "order_id": OID, "return_id": RID, "return_type": "REFUND", "return_status": self.status,
            "seller_next_action_response": [{"action": "SELLER_RESPOND_REFUND", "deadline": self.prazo}]
            if self.status == "RETURN_OR_REFUND_REQUEST_PENDING" else [],
            "refund_amount": {"currency": "BRL", "refund_total": "640.54"},
            "return_reason_text": "Package received but missing item", "update_time": 10,
        }

    async def get_return_list(self, *, order_ids=None, update_time_from=None, update_time_to=None, **kw):
        outros = {"order_id": "1", "return_id": "9", "return_type": "RETURN_AND_REFUND",
                  "return_status": "RETURN_OR_REFUND_REQUEST_PENDING",
                  "seller_next_action_response": [{"action": "SELLER_RESPOND_RETURN", "deadline": 1}]}
        return [self._caso(), outros] if order_ids is None else [self._caso()]

    async def get_return_records(self, return_id, *, locale="pt-BR"):
        return list(self.records)

    async def get_order_detail(self, order_id):
        if not self.entregue:
            return {"orders": [{"status": "IN_TRANSIT", "line_items": []}]}
        return {"orders": [{"status": "DELIVERED", "delivery_time": 1788958680,
                            "line_items": [{"tracking_number": "999881910210731", "shipping_provider_name": "J&T Express Brazil"}]}]}

    async def get_reject_reasons(self, return_id, *, locale="pt-BR"):
        return [{"name": "reverse_reject_request_reason_4", "text": "A entrega do produto está dentro do prazo"},
                {"name": "reverse_reject_request_reason_1", "text": "O motivo da devolução do comprador não é válido"}]

    async def upload_image(self, filename, content, mime="image/jpeg", *, use_case="DESCRIPTION_IMAGE"):
        self.uploads.append((filename, len(content), mime))
        return {"uri": f"tos/{filename}", "width": 10, "height": 8}

    async def reject_return(self, return_id, *, decision, reject_reason, comment, images=None, idempotency_key=None):
        self.rejects.append({"return_id": return_id, "decision": decision, "reason": reject_reason,
                             "comment": comment, "images": images, "idem": idempotency_key})
        self.status = "REFUND_OR_RETURN_REQUEST_REJECT"  # a TikTok tira o caso de pendente
        return {}


@pytest.fixture
def threema_fake(monkeypatch):
    enviados: list[tuple[str, list[str]]] = []

    async def _send_to_all(self, text, recipients=None):
        enviados.append((text, list(recipients or [])))
        return {"sent": list(recipients or []), "failed": []}

    monkeypatch.setattr(threema.ThreemaClient, "send_to_all", _send_to_all)
    monkeypatch.setattr(get_settings(), "tiktok_reembolso_threema_recipients", "CDSA84BZ")
    return enviados


async def _seed(db, make_user, fake, monkeypatch) -> None:
    user = await make_user()
    db.add(Integration(user_id=user.id, platform=IntegrationPlatform.TIKTOK, name=" Mini",
                       credentials=b"x", status="active"))
    db.add(StoreInfo(user_id=user.id, platform="tiktok", account_name="mini", bling_store_id="88"))
    db.add(BlingOrder(id=uuid4(), numero="293798", numeroloja=OID, bling_id=1, situacao="6", loja="88",
                      data=datetime(2026, 9, 1, 12, tzinfo=UTC), item_index=0, item_codigo="dg056.sp",
                      item_descricao="Hotwav A17 Pro Max", item_quantidade=1))
    await db.commit()
    monkeypatch.setattr(logistica_tiktok, "_build_tiktok_client", lambda session, integ: fake)


async def _sistema(db, ch_id) -> list[str]:
    return list((await db.execute(
        select(ChamadoMensagem.texto).where(ChamadoMensagem.chamado_id == ch_id, ChamadoMensagem.tipo == "sistema")
        .order_by(ChamadoMensagem.created_at))).scalars().all())


async def test_vigia_abre_chamado_pede_foto_e_contesta_sozinho_12h_antes(db, make_user, monkeypatch, threema_fake):
    fake = _FakeTikTok()
    await _seed(db, make_user, fake, monkeypatch)

    r = await svc.run_vigia(db, agora=AGORA)
    assert r["pendentes"] == 1 and r["abertos"] == 1, r
    ch = (await db.execute(select(Chamado).where(Chamado.origem_ref == f"tiktok_reembolso:{RID}"))).scalar_one()
    assert (ch.pedido_bling, ch.plataforma, ch.conta, ch.chamado, ch.canal, ch.origem) == ("293798", "tiktok", "mini", RID, "api", "vendas")
    hist = await _sistema(db, ch.id)
    assert any("SÓ REEMBOLSO" in t and "640.54" in t and "Pacote chegou vazio" in t and "18/09 10:19" in t
               and "999881910210731" in t for t in hist), hist
    # 17/09: o Threema PEDE foto e vídeo e diz a hora da contestação automática
    assert len(threema_fake) == 1 and threema_fake[0][1] == ["CDSA84BZ"]
    aviso = threema_fake[0][0]
    assert "FOTO e VÍDEO" in aviso and "17/09 22:19" in aviso and "18/09 10:19" in aviso, aviso

    # 2ª passada: não abre de novo nem pede foto de novo
    r2 = await svc.run_vigia(db, agora=AGORA)
    assert r2["abertos"] == 0 and len(threema_fake) == 1 and fake.rejects == []

    # alguém anexou foto no chamado; faltando menos de 12 h o robô contesta SOZINHO com ela
    db.add(ChamadoAnexo(chamado_id=ch.id, filename="pesagem.png", content_type="image/png",
                        size_bytes=len(PNG_1PX), blob=PNG_1PX))
    await db.commit()
    quase = datetime.fromtimestamp(PRAZO - 3600, UTC)
    r3 = await svc.run_vigia(db, agora=quase)
    assert r3["urgentes"] == 1 and r3.get("contestados") == 1, r3
    assert len(fake.rejects) == 1
    rj = fake.rejects[0]
    assert (rj["decision"], rj["reason"]) == ("REJECT_REFUND", "reverse_reject_request_reason_1")
    assert len(rj["images"]) == 1 and fake.uploads[0][0] == "pesagem.png"
    assert "entregue em 09/09 09:58" in rj["comment"] and "999881910210731" in rj["comment"], rj["comment"]
    assert "Pacote chegou vazio" in rj["comment"] and "6 dia(s) após a entrega" in rj["comment"], rj["comment"]
    assert "1 foto(s)" in rj["comment"]
    rep = (await db.execute(select(ChamadoMensagem).where(ChamadoMensagem.chamado_id == ch.id,
                                                          ChamadoMensagem.tipo == "replica"))).scalar_one()
    assert rep.status == "enviada" and rep.autor_nome == "robô"
    assert len(threema_fake) == 2 and "CONTESTOU sozinho" in threema_fake[1][0]
    assert any("CONTESTADO" in t and "automática" in t for t in await _sistema(db, ch.id))

    # a TikTok tirou de pendente: não contesta de novo; o desfecho vai pro histórico
    r4 = await svc.run_vigia(db, agora=quase)
    assert len(fake.rejects) == 1 and r4["desfechos"] == 1
    assert any("RECUSADO" in t for t in await _sistema(db, ch.id))


async def test_vigia_sem_entrega_nao_contesta_sozinho_e_alerta_uma_vez(db, make_user, monkeypatch, threema_fake):
    fake = _FakeTikTok()
    fake.entregue = False
    await _seed(db, make_user, fake, monkeypatch)
    await svc.run_vigia(db, agora=AGORA)
    quase = datetime.fromtimestamp(PRAZO - 3600, UTC)
    await svc.run_vigia(db, agora=quase)
    await svc.run_vigia(db, agora=quase)
    assert fake.rejects == []
    urg = [t for t, _ in threema_fake if "NÃO contestou" in t]
    assert len(urg) == 1 and "sem entrega" in urg[0], threema_fake


async def test_vigia_desfecho_aprovado_por_falta_de_resposta(db, make_user, monkeypatch, threema_fake):
    fake = _FakeTikTok()
    await _seed(db, make_user, fake, monkeypatch)
    await svc.run_vigia(db, agora=AGORA)
    ch = (await db.execute(select(Chamado).where(Chamado.origem_ref == f"tiktok_reembolso:{RID}"))).scalar_one()
    # TikTok aprovou por falta de resposta → desfecho dito com todas as letras, uma vez
    fake.status = "RETURN_OR_REFUND_REQUEST_COMPLETE"
    fake.records.append({"event": "SELLER_REJECT_APPLICATION_TIMEOUT_REFUND", "create_time": PRAZO})
    r4 = await svc.run_vigia(db, agora=datetime.fromtimestamp(PRAZO + 60, UTC))
    assert r4["desfechos"] == 1
    hist = await _sistema(db, ch.id)
    assert any("reembolso PAGO" in t and "FALTA DE RESPOSTA" in t for t in hist), hist
    r5 = await svc.run_vigia(db, agora=datetime.fromtimestamp(PRAZO + 120, UTC))
    assert r5["desfechos"] == 0


async def test_replica_do_chamado_contesta_o_reembolso_com_foto(client, make_user, auth_as, db, monkeypatch, threema_fake):
    fake = _FakeTikTok()
    await _seed(db, make_user, fake, monkeypatch)
    await svc.run_vigia(db, agora=AGORA)
    ch = (await db.execute(select(Chamado).where(Chamado.origem_ref == f"tiktok_reembolso:{RID}"))).scalar_one()
    user = await make_user(permissions={"chamados": {"view": True, "edit": True, "delete": True}})
    auth_as(user)

    r = await client.post(
        f"/api/chamados/{ch.id}/mensagens",
        data={"texto": "Pacote entregue com o aparelho — segue foto da pesagem na expedição."},
        files=[("files", ("pesagem.png", PNG_1PX, "image/png"))],
    )
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "enviada", r.json()
    assert fake.uploads == [("pesagem.png", len(PNG_1PX), "image/png")]
    assert len(fake.rejects) == 1
    rj = fake.rejects[0]
    assert (rj["return_id"], rj["decision"], rj["reason"]) == (RID, "REJECT_REFUND", "reverse_reject_request_reason_1")
    assert rj["images"] == [{"image_id": "tos/pesagem.png", "mime_type": "image/png", "width": 10, "height": 8}]
    assert "pesagem" in rj["comment"] and rj["idem"]
    assert any("CONTESTADO" in t for t in await _sistema(db, ch.id))

    # caso já fora de pendente: não recusa, registra o desfecho e falha com código legível
    fake.status = "RETURN_OR_REFUND_REQUEST_COMPLETE"
    r2 = await client.post(f"/api/chamados/{ch.id}/mensagens", data={"texto": "de novo"})
    assert r2.status_code == 201 and r2.json()["status"] == "falhou"
    assert r2.json()["erro"] == "tiktok_reembolso_nao_pendente"
    # já contestado (recusa registrada): foto nova só pela Central do Vendedor
    fake.status = "REFUND_OR_RETURN_REQUEST_REJECT"
    r3 = await client.post(f"/api/chamados/{ch.id}/mensagens", data={"texto": "mais foto"},
                           files=[("files", ("x.png", PNG_1PX, "image/png"))])
    assert r3.json()["erro"] == "tiktok_reembolso_ja_contestado"
    assert len(fake.rejects) == 1


async def test_devolucao_cancelada_nao_diz_que_valor_ficou_com_vendedor_se_outro_caso_pagou():
    class _C:
        async def get_return_list(self, *, order_ids=None, **kw):
            return [
                {"return_id": "A", "return_type": "RETURN_AND_REFUND", "return_status": "RETURN_OR_REFUND_REQUEST_CANCEL", "update_time": 1},
                {"return_id": "B", "return_type": "REFUND", "return_status": "RETURN_OR_REFUND_REQUEST_COMPLETE", "update_time": 2,
                 "refund_amount": {"refund_total": "744"}},
            ]

        async def get_return_records(self, rid, *, locale="pt-BR"):
            return [{"event": "SELLER_REJECT_APPLICATION_TIMEOUT_REFUND"}, {"event": "REFUND_SUCCESS"}]

    outro = await chamados_devolucao._tiktok_outro_caso_pago(_C(), "585930105453577483", "A")
    assert outro is not None and outro[0]["return_id"] == "B" and outro[1] is True
    caso = {"return_id": "A", "return_status": "RETURN_OR_REFUND_REQUEST_CANCEL", "refund_amount": {"refund_total": "744"}}
    txt = chamados_devolucao._texto_tiktok_encerrada(caso, None, outro)
    assert "valor ficou com o vendedor" not in txt
    assert "só reembolso B" in txt and "PAGOU o comprador" in txt and "744" in txt and "FALTA DE RESPOSTA" in txt
    # sem outro caso: texto antigo
    assert "valor ficou com o vendedor" in chamados_devolucao._texto_tiktok_encerrada(caso, None, None)
