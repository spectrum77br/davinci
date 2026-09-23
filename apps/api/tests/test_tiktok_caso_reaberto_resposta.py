# ruff: noqa: E501
"""TikTok só reembolso: o caso REABERTO é respondido — sozinho e pela réplica.

Vinicius 23/09, caso 293798 / return 4042406038488843708 (TikTok Mini, R$ 640,54):
o lançamento recusou o primeiro caso em 18/09, a compradora abriu disputa e o
suporte da TikTok criou um caso novo esperando a nossa resposta até 23/09 08:24.
O painel mostrou o prazo e:

  - nada respondeu sozinho (a recusa automática só sai no lançamento, uma vez);
  - a réplica do Cairo ficou "registrada — Shopee/TikTok não têm API de resposta
    na disputa", mas o pedido de reembolso esperando a loja TEM API (é a mesma
    recusa do lançamento);
  - ele respondeu à mão no Seller Center 25 min antes do prazo, e lá o campo
    aceitava só 150 caracteres.

"1 e 3 pode fazer": a réplica recusa o caso que espera a nossa resposta, e o
caso reaberto é respondido sozinho com a mesma contestação do lançamento.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from app.models import ChamadoMensagem
from app.services import chamados_devolucao as svc
from app.services import chamados_devolucao_sync as sync
from app.services import chamados_tiktok_reembolso as tr
from tests.test_chamados_devolucao import PNG_1PX, _FakeML, _sistema_txts, _status_aba
from tests.test_devolucao_tiktok_linha_do_tempo import RID_NOVO, _abrir, _caso, _TikTokCasos

VIDEO = "https://drive.x/video-296936"  # o vídeo do lançamento (coluna Vídeo)


@pytest.fixture
def ml(monkeypatch):
    fake = _FakeML()

    async def _client(session, conta):
        return fake

    monkeypatch.setattr(svc.chamados_svc, "_ml_client_para", _client)
    monkeypatch.setattr(svc, "ENFILEIRAR", False)  # dispara inline (sem Redis)
    return fake


class _Reaberto(_TikTokCasos):
    """Motivos de recusa de SÓ REEMBOLSO pra qualquer caso (o fake de origem só dá
    esses pro caso do lançamento) e um limite de tamanho do texto sob controle."""

    def __init__(self):
        super().__init__()
        self.limite: int | None = None
        self.falha: str | None = None

    async def get_reject_reasons(self, return_id, *, locale="pt-BR"):
        return [{"name": "reverse_reject_request_reason_4", "text": "A entrega do produto está dentro do prazo"},
                {"name": "reverse_reject_request_reason_1", "text": "O motivo da devolução do comprador não é válido"}]

    async def reject_return(self, return_id, *, decision, reject_reason, comment, images=None, idempotency_key=None):
        if self.falha and return_id == RID_NOVO:
            raise RuntimeError(self.falha)
        if self.limite is not None and len(comment.encode("utf-8")) > self.limite:
            raise RuntimeError("tiktok_reject_return code=98001004 msg=Invalid parameters. the length of seller words is over limit")
        return await super().reject_return(
            return_id, decision=decision, reject_reason=reject_reason, comment=comment,
            images=images, idempotency_key=idempotency_key,
        )


def _reaberto(fake, *, prazo_em: timedelta = timedelta(days=2)) -> int:
    """O suporte da TikTok cria o caso novo, esperando a NOSSA resposta."""
    novo_em = fake.update_time + 7200
    prazo = int(novo_em + prazo_em.total_seconds())
    caso = _caso(RID_NOVO, "RETURN_OR_REFUND_REQUEST_PENDING", criado=novo_em)
    caso["seller_next_action_response"] = [{"action": "SELLER_RESPOND_REFUND", "deadline": prazo}]
    fake.outros = [caso]
    fake.linhas[RID_NOVO] = [
        {"event": "ORDER_REFUND", "role": "BUYER", "create_time": novo_em,
         "note": "Pacote recebido, mas faltam alguns itens"},
    ]
    return prazo


def _respondido(fake) -> None:
    """A TikTok registrou a nossa recusa do caso novo."""
    fake.outros[0]["return_status"] = "REFUND_OR_RETURN_REQUEST_REJECT"
    fake.outros[0]["seller_next_action_response"] = []


async def _replicas(db, chamado_id) -> list[ChamadoMensagem]:
    return list(
        (
            await db.execute(
                select(ChamadoMensagem)
                .where(ChamadoMensagem.chamado_id == chamado_id, ChamadoMensagem.tipo == "replica")
                .order_by(ChamadoMensagem.created_at)
            )
        ).scalars()
    )


# ---------------------------------------------------------------- 3: responde sozinho


async def test_caso_reaberto_e_respondido_sozinho_com_a_contestacao_do_lancamento(
    client, make_user, auth_as, db, ml, monkeypatch
):
    fake = _Reaberto()
    ch, recusa = await _abrir(client, db, make_user, auth_as, monkeypatch, fake)
    idem_lancamento = fake.rejects[0]["idem"]
    _reaberto(fake)

    await sync.sync_respostas(db, agora=recusa + timedelta(hours=3))
    await db.refresh(ch)

    assert ch.chamado == RID_NOVO
    assert len(fake.rejects) == 2, fake.rejects
    rj = fake.rejects[1]
    assert (rj["return_id"], rj["decision"], rj["reason"]) == (RID_NOVO, "REJECT_REFUND", "reverse_reject_request_reason_1")
    # a mesma contestação do lançamento: fatos da entrega, vídeo, alegação do caso NOVO
    txt = rj["comment"]
    assert txt.startswith("Contestamos o pedido de reembolso.") and VIDEO in txt, txt
    assert "999882054197026" in txt and "faltam alguns itens" in txt, txt
    assert len(txt.encode("utf-8")) <= tr.COMENTARIO_MAX_BYTES
    assert rj["idem"] and rj["idem"] != idem_lancamento  # outra chave: é outra resposta
    # aparece no histórico como a NOSSA fala (do robô), com o texto que saiu
    rep = await _replicas(db, ch.id)
    assert [(m.autor_nome, m.status, m.texto) for m in rep] == [("robô", "enviada", txt)]
    sist = await _sistema_txts(db, ch.id)
    assert any("CONTESTADO" in t and f"sozinho no caso reaberto {RID_NOVO}" in t for t in sist), sist
    # é a nossa vez que acabou: a coluna Status vai pra Aguard. Plataforma
    assert (await _status_aba(db, ch))[0] == "aguard_plataforma"

    # a TikTok ainda não atualizou o caso: a passada seguinte NÃO manda de novo
    await sync.sync_respostas(db, agora=recusa + timedelta(hours=4))
    assert len(fake.rejects) == 2
    # registrou a recusa: nada mais sai
    _respondido(fake)
    await sync.sync_respostas(db, agora=recusa + timedelta(hours=5))
    assert len(fake.rejects) == 2
    assert len(await _replicas(db, ch.id)) == 1


async def test_sem_contestacao_de_reembolso_no_chamado_nao_responde_sozinho(
    client, make_user, auth_as, db, ml, monkeypatch
):
    """Só repete a contestação de SÓ REEMBOLSO que o lançamento já fez — chamado sem
    ela (ex.: devolução com pacote) fica com a gente, pela réplica."""
    fake = _Reaberto()
    ch, recusa = await _abrir(client, db, make_user, auth_as, monkeypatch, fake)
    await db.execute(
        delete(ChamadoMensagem).where(
            ChamadoMensagem.chamado_id == ch.id,
            ChamadoMensagem.texto.like(f"{tr.MARCA_AUTO}%"),
        )
    )
    await db.commit()
    _reaberto(fake)

    await sync.sync_respostas(db, agora=recusa + timedelta(hours=3))
    await db.refresh(ch)

    assert ch.chamado == RID_NOVO and len(fake.rejects) == 1
    assert await _replicas(db, ch.id) == []


async def test_resposta_automatica_que_nao_sai_avisa_uma_vez_e_nao_insiste(
    client, make_user, auth_as, db, ml, monkeypatch
):
    fake = _Reaberto()
    ch, recusa = await _abrir(client, db, make_user, auth_as, monkeypatch, fake)
    _reaberto(fake)
    fake.falha = "tiktok_reject_return code=25011010 msg=in arbitration"

    await sync.sync_respostas(db, agora=recusa + timedelta(hours=3))
    await sync.sync_respostas(db, agora=recusa + timedelta(hours=4))

    avisos = [t for t in await _sistema_txts(db, ch.id) if "NÃO saiu" in t]
    assert len(avisos) == 1, avisos
    assert f"caso reaberto {RID_NOVO}" in avisos[0] and "25011010" in avisos[0]
    assert "responda pela réplica" in avisos[0]
    assert len(fake.rejects) == 1 and await _replicas(db, ch.id) == []


async def test_texto_longo_recusado_pela_tiktok_sai_curto_com_o_video(
    client, make_user, auth_as, db, ml, monkeypatch
):
    """Seller Center, 23/09: o campo aceitava 150 caracteres. A API não diz o limite
    — quando ela recusa o tamanho, a recusa sai de novo curta, link inteiro."""
    fake = _Reaberto()
    ch, recusa = await _abrir(client, db, make_user, auth_as, monkeypatch, fake)
    _reaberto(fake)
    fake.limite = 150

    await sync.sync_respostas(db, agora=recusa + timedelta(hours=3))

    assert len(fake.rejects) == 2
    txt = fake.rejects[1]["comment"]
    assert len(txt.encode("utf-8")) <= 150 and VIDEO in txt, txt
    assert txt.startswith("Contestamos: entregue em 16/09 sem avaria."), txt
    rep = await _replicas(db, ch.id)
    assert rep[0].texto == txt  # o histórico mostra o que a TikTok aceitou
    assert any("texto curto" in t for t in await _sistema_txts(db, ch.id))


async def test_lista_atrasada_logo_depois_do_lancamento_nao_responde_de_novo(
    client, make_user, auth_as, db, ml, monkeypatch
):
    """A TikTok ainda mostra o caso que o lançamento acabou de recusar como
    pendente (a lista atrasa): não é caso reaberto, nada sai de novo."""
    fake = _Reaberto()
    ch, recusa = await _abrir(client, db, make_user, auth_as, monkeypatch, fake)
    fake.status_reembolso = "RETURN_OR_REFUND_REQUEST_PENDING"  # com o prazo de antes

    await sync.sync_respostas(db, agora=recusa + timedelta(minutes=30))

    assert len(fake.rejects) == 1 and await _replicas(db, ch.id) == []


async def test_mesmo_caso_devolvido_pra_nos_depois_da_resposta_e_respondido(
    client, make_user, auth_as, db, ml, monkeypatch
):
    """Arbitragem encerrada devolve o MESMO caso pra loja (22/09): a TikTok mexeu nele
    depois da nossa resposta e o relógio voltou a correr — responde de novo, com outra
    chave de idempotência (a de antes devolveria a resposta velha)."""
    fake = _Reaberto()
    ch, _recusa = await _abrir(client, db, make_user, auth_as, monkeypatch, fake)
    idem = fake.rejects[0]["idem"]
    agora = datetime.now(UTC)
    fake.status_reembolso = "RETURN_OR_REFUND_REQUEST_PENDING"
    fake.update_time = int((agora + timedelta(minutes=5)).timestamp())
    fake.acoes = [{"action": "SELLER_RESPOND_REFUND", "deadline": int((agora + timedelta(days=2)).timestamp())}]

    await sync.sync_respostas(db, agora=agora + timedelta(minutes=10))

    assert len(fake.rejects) == 2 and fake.rejects[1]["idem"] != idem
    assert fake.rejects[1]["decision"] == "REJECT_REFUND"
    assert [m.autor_nome for m in await _replicas(db, ch.id)] == ["robô"]


# ---------------------------------------------------------------- 1: a réplica responde


async def test_replica_recusa_o_caso_que_espera_a_nossa_resposta(
    client, make_user, auth_as, db, ml, monkeypatch
):
    fake = _Reaberto()
    ch, _recusa = await _abrir(client, db, make_user, auth_as, monkeypatch, fake)
    _reaberto(fake)  # o sync ainda não passou: o chamado aponta pro caso velho

    digitado = "Contestamos o pedido de reembolso. Pedido entregue sem avaria, cliente já reclamou 3 vezes."
    r = await client.post(
        f"/api/chamados/{ch.id}/mensagens",
        data={"texto": digitado},
        files=[("files", ("caixa.png", PNG_1PX, "image/png"))],
    )
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "enviada" and r.json()["erro"] is None, r.json()
    rj = fake.rejects[-1]
    assert (rj["return_id"], rj["decision"], rj["reason"]) == (RID_NOVO, "REJECT_REFUND", "reverse_reject_request_reason_1")
    # o que a pessoa digitou + o vídeo que faltou no texto
    assert rj["comment"] == f"{digitado} Vídeo da expedição: {VIDEO}"
    assert r.json()["texto"] == rj["comment"]
    # a foto da réplica vai junto
    assert [i["image_id"] for i in rj["images"]] == ["tos/caixa.png"]
    await db.refresh(ch)
    assert ch.chamado == RID_NOVO
    assert any("pela réplica de" in t and RID_NOVO in t for t in await _sistema_txts(db, ch.id))


async def test_replica_sem_caso_esperando_fica_so_no_historico(
    client, make_user, auth_as, db, ml, monkeypatch
):
    fake = _Reaberto()
    ch, _recusa = await _abrir(client, db, make_user, auth_as, monkeypatch, fake)
    # o único caso é o que já recusamos, sem prazo pra nós: disputa sem API
    r = await client.post(f"/api/chamados/{ch.id}/mensagens", data={"texto": "mais uma"})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "registrada" and r.json()["erro"] == "plataforma_sem_api_replica"
    assert len(fake.rejects) == 1


async def test_replica_que_a_tiktok_recusa_mostra_o_erro(client, make_user, auth_as, db, ml, monkeypatch):
    """Antes a réplica ficava "registrada" em silêncio; agora, se a TikTok recusar,
    a pessoa vê o motivo e sabe que não saiu."""
    fake = _Reaberto()
    ch, _recusa = await _abrir(client, db, make_user, auth_as, monkeypatch, fake)
    _reaberto(fake)
    fake.falha = "tiktok_reject_return code=25011010 msg=in arbitration"
    r = await client.post(f"/api/chamados/{ch.id}/mensagens", data={"texto": "Contestamos."})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "falhou" and "25011010" in r.json()["erro"], r.json()


# ---------------------------------------------------------------- o texto no limite


def test_caber_com_link_nunca_parte_o_link():
    link = "https://drive.google.com/file/d/1XTchb77Gqa7_7xZDKql95Xy4k-IVRLF2/view?usp=drivesdk"
    # sem o link no texto: entra no fim
    assert tr.caber_com_link("Contestamos.", link) == f"Contestamos. Vídeo da expedição: {link}"
    # o link na outra forma do Drive (a que o Seller Center gera) conta como presente
    outra = "https://drive.google.com/open?id=1XTchb77Gqa7_7xZDKql95Xy4k-IVRLF2"
    assert tr.caber_com_link(f"Veja {outra} obrigado", link) == f"Veja {outra} obrigado"
    # texto grande com o link no meio: corta em volta, o link fica inteiro
    antes = "Contestamos o pedido de reembolso. " + "Pedido entregue sem avaria e sem violação. " * 5
    depois = "Alegação do comprador feita 12 dias após a entrega. Pedimos que seja negado."
    for limite in (150, 200, 500):
        out = tr.caber_com_link(f"{antes}Vídeo: {link} {depois}", link, limite)
        assert link in out and len(out.encode("utf-8")) <= limite, (limite, out)
    # o curto da contestação cabe nos 150 com o link
    curto = tr.texto_curto({"quando": 1757098680}, link)
    assert link in curto and len(curto.encode("utf-8")) <= 150 and curto.startswith("Contestamos: entregue em"), curto
    assert tr.texto_curto({}, None) == "Contestamos o reembolso. Pedimos que o reembolso seja negado."


def test_espera_nossa_recusa_segue_o_prazo_da_tiktok():
    base = {"return_type": "REFUND", "seller_next_action_response": [{"action": "SELLER_RESPOND_REFUND", "deadline": 9}]}
    assert tr.espera_nossa_recusa({**base, "return_status": "RETURN_OR_REFUND_REQUEST_PENDING"})
    # arbitragem encerrada devolve o relógio pra loja (22/09): recusado + prazo = espera
    assert tr.espera_nossa_recusa({**base, "return_status": "REFUND_OR_RETURN_REQUEST_REJECT"})
    assert not tr.espera_nossa_recusa({**base, "return_status": "RETURN_OR_REFUND_REQUEST_COMPLETE"})
    assert not tr.espera_nossa_recusa({**base, "return_type": "RETURN_AND_REFUND", "return_status": "RETURN_OR_REFUND_REQUEST_PENDING"})
    assert not tr.espera_nossa_recusa({"return_type": "REFUND", "return_status": "RETURN_OR_REFUND_REQUEST_PENDING"})
