# ruff: noqa: E501
"""Só reembolso da TikTok: vigia do prazo (avisos 12 h / 3 h, desfecho) — o caso cai em
Devoluções › Fraude e é o LANÇAMENTO que responde (services/chamados_tiktok_reembolso,
Vinicius 18/09). Origem: Eduardo 16/09, o 294865 aprovado pela TikTok por falta de
resposta (R$ 744) porque o robô só olhava devolução com pacote."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import (
    BlingOrder,
    Chamado,
    ChamadoMensagem,
    DevolucaoRastreio,
    Integration,
    IntegrationPlatform,
    StoreInfo,
)
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


def _em(h_antes: float) -> datetime:
    return datetime.fromtimestamp(PRAZO - int(h_antes * 3600), UTC)


def _chamado_devolucao(**kw) -> Chamado:
    """O chamado que o LANÇAMENTO abre (origem devolucao) e que respondeu o caso RID."""
    base = {"data": AGORA.date(), "pedido_bling": "293798", "pedido_marketplace": OID, "plataforma": "tiktok",
            "conta": "mini", "origem": "devolucao", "origem_ref": str(uuid4()), "chamado": RID, "canal": "api"}
    base.update(kw)
    return Chamado(**base)


async def test_vigia_nao_abre_chamado_so_avisa_12h_e_3h(db, make_user, monkeypatch, threema_fake):
    """Vinicius 18/09: o caso cai em Fraude e é o lançamento que responde — o vigia não abre
    chamado nem contesta sozinho; avisa faltando 12 h e, sem resposta, faltando 3 h."""
    fake = _FakeTikTok()
    await _seed(db, make_user, fake, monkeypatch)

    r = await svc.run_vigia(db, agora=AGORA)  # faltam ~44 h
    assert r["pendentes"] == 1 and r["avisos_12h"] == 0 and r["avisos_3h"] == 0, r
    assert (await db.execute(select(Chamado))).scalars().all() == []
    assert threema_fake == [] and fake.rejects == []

    r2 = await svc.run_vigia(db, agora=_em(11))
    assert r2["avisos_12h"] == 1 and len(threema_fake) == 1 and threema_fake[0][1] == ["CDSA84BZ"], r2
    aviso = threema_fake[0][0]
    assert aviso.startswith("⚠️") and "SÓ REEMBOLSO" in aviso and "640.54" in aviso and "293798" in aviso, aviso
    assert "Faltam 11 h" in aviso and "18/09 10:19" in aviso and "Pacote chegou vazio" in aviso, aviso
    assert "LANÇAMENTO" in aviso and "Fraude" in aviso and "vídeo obrigatório" in aviso, aviso
    rast = await db.get(DevolucaoRastreio, "293798")
    assert rast is not None and rast.aviso_prazo_acao_para == datetime.fromtimestamp(PRAZO, UTC)

    # mesma faixa: não repete
    r3 = await svc.run_vigia(db, agora=_em(10))
    assert r3["avisos_12h"] == 0 and r3["avisos_3h"] == 0 and len(threema_fake) == 1

    # faltando menos de 3 h: último aviso, uma vez
    r4 = await svc.run_vigia(db, agora=_em(2))
    assert r4["avisos_3h"] == 1 and len(threema_fake) == 2, r4
    assert threema_fake[1][0].startswith("🚨") and "ÚLTIMO AVISO" in threema_fake[1][0]
    r5 = await svc.run_vigia(db, agora=_em(1.5))
    assert r5["avisos_3h"] == 0 and len(threema_fake) == 2

    assert (await db.execute(select(Chamado))).scalars().all() == [] and fake.rejects == []


async def test_vigia_lancamento_respondido_nao_avisa_e_registra_desfecho(db, make_user, monkeypatch, threema_fake):
    fake = _FakeTikTok()
    await _seed(db, make_user, fake, monkeypatch)
    from app.services import chamados as chamados_svc
    ch = _chamado_devolucao()
    db.add(ch)
    await db.flush()
    db.add(chamados_svc.nova_mensagem(ch, texto="Contestamos...", tipo="abertura",
                                      autor_nome=chamados_svc.AUTOR_SISTEMA, status="enviada"))
    await db.commit()

    r = await svc.run_vigia(db, agora=_em(1))
    assert r["respondidos"] == 1 and r["avisos_12h"] == 0 and r["avisos_3h"] == 0, r
    assert threema_fake == []

    # "vendedor recusou" é a NOSSA recusa, não desfecho (Vinicius 18/09, 296936): o vigia
    # não decreta ganhamos — o comprador ainda pode recorrer; o sync acompanha.
    fake.status = "REFUND_OR_RETURN_REQUEST_REJECT"
    r2 = await svc.run_vigia(db, agora=_em(0.5))
    assert r2["desfechos"] == 0, r2
    assert not any(svc.MARCA_DESFECHO in t for t in await _sistema(db, ch.id))
    assert ch.status_plataforma is None
    # a TikTok decidiu (recusa mantida, solicitação cancelada): desfecho, uma vez
    fake.status = "RETURN_OR_REFUND_REQUEST_CANCEL"
    r3 = await svc.run_vigia(db, agora=_em(0.4))
    assert r3["desfechos"] == 1, r3
    hist = await _sistema(db, ch.id)
    assert any(svc.MARCA_DESFECHO in t and "cancelou" in t for t in hist), hist
    assert ch.status_plataforma == chamados_svc.STATUS_GANHAMOS
    r4 = await svc.run_vigia(db, agora=_em(0.3))
    assert r4["desfechos"] == 0


async def test_vigia_lancamento_travado_avisa_o_que_falta(db, make_user, monkeypatch, threema_fake):
    fake = _FakeTikTok()
    await _seed(db, make_user, fake, monkeypatch)
    from app.services import chamados as chamados_svc
    ch = _chamado_devolucao()
    db.add(ch)
    await db.flush()
    msg = chamados_svc.nova_mensagem(ch, texto="x", tipo="abertura", autor_nome=chamados_svc.AUTOR_SISTEMA,
                                     status="pendente")
    msg.erro = "devolucao_sem_video"
    db.add(msg)
    await db.commit()

    r = await svc.run_vigia(db, agora=_em(5))
    assert r["avisos_12h"] == 1 and len(threema_fake) == 1, r
    assert "resposta ainda NÃO saiu (devolucao_sem_video)" in threema_fake[0][0], threema_fake[0][0]


async def test_vigia_desfecho_aprovado_por_falta_de_resposta_em_chamado_antigo(db, make_user, monkeypatch, threema_fake):
    fake = _FakeTikTok()
    await _seed(db, make_user, fake, monkeypatch)
    ch = _chamado_devolucao(origem="vendas", origem_ref=f"tiktok_reembolso:{RID}")  # chamado do vigia antigo
    db.add(ch)
    await db.commit()
    # TikTok aprovou por falta de resposta → desfecho dito com todas as letras, uma vez
    fake.status = "RETURN_OR_REFUND_REQUEST_COMPLETE"
    fake.records.append({"event": "SELLER_REJECT_APPLICATION_TIMEOUT_REFUND", "create_time": PRAZO})
    r4 = await svc.run_vigia(db, agora=datetime.fromtimestamp(PRAZO + 60, UTC))
    assert r4["desfechos"] == 1, r4
    hist = await _sistema(db, ch.id)
    assert any("reembolso PAGO" in t and "FALTA DE RESPOSTA" in t for t in hist), hist
    r5 = await svc.run_vigia(db, agora=datetime.fromtimestamp(PRAZO + 120, UTC))
    assert r5["desfechos"] == 0


async def test_replica_em_chamado_antigo_continua_contestando_com_foto(client, make_user, auth_as, db, monkeypatch, threema_fake):
    fake = _FakeTikTok()
    await _seed(db, make_user, fake, monkeypatch)
    ch = _chamado_devolucao(origem="vendas", origem_ref=f"tiktok_reembolso:{RID}")
    db.add(ch)
    await db.commit()
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


def test_texto_contestacao_cabe_no_limite_da_tiktok_e_preserva_o_video():
    """18/09, 296936: 560 caracteres voltaram 98001004 "seller words is over limit". O texto
    tem que caber em COMENTARIO_MAX_BYTES mantendo entrega + link do vídeo inteiros."""
    caso = {"return_id": RID, "refund_amount": {"refund_total": "803.2"}}
    entrega = {"quando": 1789588803, "transp": "J&T Express Brazil", "rastreio": "999882054197026"}
    video = "https://drive.google.com/file/d/1D2sVeoy7bVzpkzqd0OJve0Fgv4xbxzlM/view?usp=drivesdk"
    nota = "Recebi uma caixa de sabonete ao invés do celular!\nTenho fotos e video abrindo o pacote !"
    txt = svc.texto_contestacao(
        caso, oid=OID, produto="Hotwav A17 Pro Max 12.128 - Laranja", entrega=entrega, nota=nota,
        pedido_em=1789620000, comprador_mandou_prova=True, fotos=0, video=video,
        observacao="Peso conferido na expedição: 0,412 kg, igual ao da etiqueta. " * 6,
    )
    assert len(txt.encode("utf-8")) <= svc.COMENTARIO_MAX_BYTES, len(txt.encode("utf-8"))
    assert txt.startswith("Contestamos o pedido de reembolso.")
    assert "entregue em 16/09 17:00 pela J&T Express Brazil (rastreio 999882054197026)" in txt
    assert video in txt and "Peso conferido" in txt, txt
    # o que não coube saiu do fim (a alegação do comprador / o fechamento), não do meio
    assert "caixa de sabonete" not in txt or txt.endswith(("negado.", "…"))

    # sem nada opcional o texto fica inteiro, com o fechamento
    curto = svc.texto_contestacao(caso, oid=OID, produto=None, entrega=entrega, nota="",
                                  pedido_em=None, comprador_mandou_prova=False, fotos=2)
    assert curto.endswith("Pedimos que o reembolso seja negado.") and "2 foto(s)" in curto
    # o caso real do 296936 (nota do comprador + link do Drive) cabe inteiro
    real = svc.texto_contestacao(
        caso, oid=OID, produto="Hotwav A17 Pro Max 12.128 - Laranja", entrega=entrega, nota=nota,
        pedido_em=1789620000, comprador_mandou_prova=True, fotos=0, video=video,
    )
    assert len(real.encode("utf-8")) <= svc.COMENTARIO_MAX_BYTES, real
    assert video in real and "caixa de sabonete" in real and real.endswith("negado."), real


def test_caber_corta_em_bytes_sem_partir_caractere():
    assert svc.caber(["a" * 10], limite=10) == "a" * 10
    assert svc.caber(["ação " * 200], limite=50).encode("utf-8").__len__() <= 50
    # parte que não cabe e sobra pouca (< 40 bytes): fica fora inteira
    assert svc.caber(["x" * 480, "segunda parte longa demais"], limite=500) == "x" * 480
    # cabe cortada num espaço quando sobra espaço razoável
    t = svc.caber(["x" * 400, "palavra " * 30], limite=500)
    assert t.startswith("x" * 400 + " palavra") and len(t.encode()) <= 500 and t.endswith("…")
