# ruff: noqa: E501
"""Shopee, motivo "Não recebido" (o pacote de VOLTA não chegou) — Vinicius 21/09.

Medido em produção: o disparo abria a disputa "não recebi" pela API com o pacote
ainda em trânsito na SPX (0 ganhas em 2), colando a observação interna, prometendo
"vídeo da expedição" e anexando o cartão com QR pro texto que a operadora digitou
no Link envio só pra passar na trava. Decisão: diferenciar pela SPX —
  - perna reversa NÃO entregue → sem disputa pela API: o robô abre a requisição no
    Seller Center (mesmo mecanismo de `_RoboError` → `_encaminhar_robo`);
  - entregue → disputa pela API com texto FACTUAL (data da entrega + rastreio), sem
    cartão, sem "comprovante da expedição", sem imagem quando não há foto real;
  - módulo de evidência quebrado (série 81, module_index 0) → robô;
  - Link envio deixa de ser obrigatório nesse motivo e, quando vem, tem que ser URL.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import ChamadoAnexo, DevolucaoAnexo, DevolucaoRastreio, Devolution
from app.services import chamados_devolucao as svc
from app.services import devolucao_cartao_video as cartao
from tests.test_chamados_devolucao import (
    PNG_1PX,
    _abertura,
    _chamado_de,
    _FakeShopee,
    _perms,
    _seed_pedido,
    _sistema_txts,
)

pytestmark = pytest.mark.asyncio

HOJE = datetime.now(UTC).astimezone(svc.chamados_svc.SAO_PAULO).strftime("%d/%m/%Y")
ENTREGUE_TXT = "A SPX registra a entrega do pacote de devolução (rastreio BR2609RSN) em 16/09/2026 11:11, mas ele não foi recebido no nosso endereço até "
TRANSITO_TXT = "O pacote da devolução (rastreio BR2609RSN) ainda não chegou até nós — situação na SPX: coletado desde 14/09/2026."


@pytest.fixture
def inline(monkeypatch):
    monkeypatch.setattr(svc, "ENFILEIRAR", False)  # dispara inline (sem Redis)


async def _lancar(client, db, make_user, auth_as, monkeypatch, fake, *, numero, sn,
                  motivo="Não recebido", rastreio: dict | None = None, **extra):
    user = await make_user(permissions=_perms())
    auth_as(user)

    async def _c(session, *a):
        return fake

    monkeypatch.setattr(svc, "_shopee_client_para", _c)
    await _seed_pedido(db, user, numero=numero, numeroloja=sn, platform="shopee", conta="atv", loja="88")
    db.add(DevolucaoRastreio(pedido_bling=numero, devolucao_id_auto=f"2609{numero}RSN", fonte_auto="shopee",
                             **(rastreio or {})))
    await db.commit()
    r = await client.post(
        "/api/devolutions",
        json={"conta": "atv", "pedido_bling": numero, "pedido_marketplace": sn,
              "sku": "b001.26", "produtos": "Mala Listrada tamanho 26",
              "condicao_produto": "Não devolvido", "motivo_devolucao": motivo, **extra},
    )
    assert r.status_code == 201, r.text
    return r, user


async def _sem_cartao(db, ch_id):
    anexos = (await db.execute(select(DevolucaoAnexo))).scalars().all()
    assert not [a for a in anexos if a.filename == cartao.CARTAO_VIDEO_NOME]
    robo = (await db.execute(select(ChamadoAnexo).where(ChamadoAnexo.chamado_id == ch_id))).scalars().all()
    assert not [a for a in robo if a.filename == cartao.CARTAO_VIDEO_NOME]


async def test_nao_recebido_em_transito_vai_direto_pro_robo_sem_disputa(client, make_user, auth_as, db, inline, monkeypatch):
    fake = _FakeShopee(entregue=False)  # reverse LOGISTICS_PICKUP_DONE, update_time 14/09
    chamadas = {"reasons": 0}
    original = fake.get_return_dispute_reason

    async def _reasons(return_sn):
        chamadas["reasons"] += 1
        return await original(return_sn)

    fake.get_return_dispute_reason = _reasons
    r, _user = await _lancar(
        client, db, make_user, auth_as, monkeypatch, fake, numero="296001", sn="2609ATVNR001",
        link_envio="https://drive.x/video-296001", observacao="Cliente diz que postou dia 12.",
    )
    # sem disputa, sem consultar motivo: tarefa do robô (canal robo, pendente sem erro)
    assert fake.disputes == [] and chamadas["reasons"] == 0
    assert r.json()["chamado_ml_status"] == "pendente" and r.json()["chamado_ml_erro"] is None, r.json()
    ch = await _chamado_de(db, "296001")
    ab = await _abertura(db, ch.id)
    assert ch.canal == "robo" and ab.canal == "robo" and ab.status == "pendente" and ch.chamado is None
    txt = ab.texto
    assert txt.split("\n")[0] == TRANSITO_TXT, txt
    assert "Pedido 2609ATVNR001 · SKU b001.26 · Mala Listrada tamanho 26." in txt
    assert "Observação: Cliente diz que postou dia 12." in txt
    assert txt.endswith("Solicitamos a localização do pacote junto à transportadora e que o reembolso não seja liberado até a conclusão.")
    assert "QR code" not in txt and "Comprovante da expedição" not in txt and "Solicitamos a análise do caso" not in txt
    hist = await _sistema_txts(db, ch.id)
    assert any("sem entrega registrada pela SPX (coletado)" in t and "Seller Center" in t for t in hist), hist
    # nem cartão do vídeo (o link da ida não prova nada aqui) nem foto na tarefa
    await _sem_cartao(db, ch.id)
    # o robô da Shopee recebe como `abrir`; o cron das pendências não mexe
    token = "tok-shopee-2109"  # noqa: S105
    monkeypatch.setattr(get_settings(), "nf_agent_token", token)
    lease = await client.post("/api/chamados/agent/lease", headers={"X-Agent-Token": token},
                              json={"limite": 10, "plataforma": "shopee"})
    assert lease.status_code == 200, lease.text
    tarefas = [t for t in lease.json()["tarefas"] if t["mensagem_id"] == str(ab.id)]
    assert len(tarefas) == 1 and tarefas[0]["tipo"] == "abrir", lease.json()
    assert (await svc.processar_pendentes(db))["verificados"] == 0


async def test_nao_recebido_entregue_abre_disputa_com_texto_factual_sem_imagem(client, make_user, auth_as, db, inline, monkeypatch):
    fake = _FakeShopee()  # entregue: LOGISTICS_DELIVERY_DONE em 16/09 11:11
    r, _user = await _lancar(
        client, db, make_user, auth_as, monkeypatch, fake, numero="296002", sn="2609ATVNR002",
        link_envio="https://drive.x/video-296002", observacao="Portaria não recebeu nada.",
    )
    assert r.json()["chamado_ml_status"] == "enviada", r.json()
    assert len(fake.disputes) == 1
    d = fake.disputes[0]
    assert d["reason"] == 81 and d["image_list"] is None  # sem foto real → sem image_list
    txt = d["text"]
    assert txt.startswith(ENTREGUE_TXT + HOJE + "."), txt
    assert "Pedido 2609ATVNR002 · SKU b001.26 · Mala Listrada tamanho 26." in txt
    assert "Observação: Portaria não recebeu nada." in txt
    assert txt.endswith("Solicitamos o comprovante de entrega (foto/assinatura), a apuração junto à transportadora e que o reembolso não seja liberado até a conclusão.")
    for proibido in ("QR code", "Comprovante da expedição", "vídeo", "foto(s) em anexo", "Solicitamos a análise do caso"):
        assert proibido not in txt, (proibido, txt)
    # o histórico guarda o que foi enviado; chamado segue pela API
    ch = await _chamado_de(db, "296002")
    ab = await _abertura(db, ch.id)
    assert ab.status == "enviada" and ab.canal == "api" and ab.texto == txt
    assert ch.canal == "api" and ch.chamado == "2609296002RSN"
    await _sem_cartao(db, ch.id)
    assert fake.converted == []


async def test_nao_recebido_entregue_sem_observacao_nao_tem_linha_vazia(client, make_user, auth_as, db, inline, monkeypatch):
    fake = _FakeShopee()
    r, _user = await _lancar(client, db, make_user, auth_as, monkeypatch, fake, numero="296003", sn="2609ATVNR003")
    assert r.json()["chamado_ml_status"] == "enviada", r.json()
    linhas = fake.disputes[0]["text"].split("\n")
    assert len(linhas) == 3 and not any(ln.startswith("Observação") for ln in linhas), linhas


async def test_nao_recebido_modulo_zero_obrigatorio_vai_pro_robo_sem_post(client, make_user, auth_as, db, inline, monkeypatch):
    """Série 81 medida 21/09: evidence_module_list [{module_index 0, "live test",
    is_required}] — a API nunca aceita. Nada de POST nem de "esperando foto"."""
    fake = _FakeShopee()
    due = 1789700000  # 17/09/2026 23:53 BRT

    async def _det(return_sn):
        det = await _FakeShopee.get_return_detail(fake, return_sn)
        det["return_seller_due_date"] = due
        return det

    async def _reasons(return_sn):
        return [{"dispute_reason": 81, "dispute_reason_text": "Did not receive the return product",
                 "evidence_module_list": [{"module_index": 0, "requirement": "live test", "is_required": True}]}]

    fake.get_return_detail = _det
    fake.get_return_dispute_reason = _reasons
    r, _user = await _lancar(client, db, make_user, auth_as, monkeypatch, fake, numero="296004", sn="2609ATVNR004")
    assert fake.disputes == []
    assert r.json()["chamado_ml_status"] == "pendente" and r.json()["chamado_ml_erro"] is None, r.json()
    ch = await _chamado_de(db, "296004")
    ab = await _abertura(db, ch.id)
    assert ch.canal == "robo" and ab.canal == "robo" and ab.status == "pendente"
    assert ab.texto.startswith(ENTREGUE_TXT), ab.texto  # o mesmo texto factual
    hist = await _sistema_txts(db, ch.id)
    assert any("exige evidência num módulo que a API não aceita" in t and "antes de 17/09 23:53" in t for t in hist), hist


async def test_nao_recebido_erro_cru_do_modulo_vai_pro_robo_nao_falha(client, make_user, auth_as, db, inline, monkeypatch):
    fake = _FakeShopee()

    async def _dispute(return_sn, **kw):
        raise RuntimeError("shopee_dispute error=error_param msg=Unable to raise dispute as mandatory module index is missing")

    fake.dispute = _dispute
    r, _user = await _lancar(client, db, make_user, auth_as, monkeypatch, fake, numero="296005", sn="2609ATVNR005")
    assert r.json()["chamado_ml_status"] == "pendente" and r.json()["chamado_ml_erro"] is None, r.json()
    ch = await _chamado_de(db, "296005")
    ab = await _abertura(db, ch.id)
    assert ch.canal == "robo" and ab.status == "pendente" and ab.texto.startswith(ENTREGUE_TXT)
    assert any("módulo que a API não aceita" in t for t in await _sistema_txts(db, ch.id))
    # outro erro cru continua `falhou` com o erro
    fake2 = _FakeShopee()

    async def _dispute2(return_sn, **kw):
        raise RuntimeError("shopee_dispute error=returnsn.illegal")

    fake2.dispute = _dispute2
    r2, _u = await _lancar(client, db, make_user, auth_as, monkeypatch, fake2, numero="296006", sn="2609ATVNR006")
    assert r2.json()["chamado_ml_status"] == "falhou" and "returnsn.illegal" in r2.json()["chamado_ml_erro"]


async def test_outro_motivo_continua_igual_com_cartao_e_texto_padrao(client, make_user, auth_as, db, inline, monkeypatch):
    """Golpe: nada muda — a SPX não entra na conta, o cartão do vídeo vai como a
    foto do módulo obrigatório e o texto é o padrão (com o comprovante da expedição)."""
    fake = _FakeShopee(entregue=False)
    r, _user = await _lancar(
        client, db, make_user, auth_as, monkeypatch, fake, numero="296007", sn="2609ATVNR007",
        motivo="Golpe", link_envio="https://drive.x/video-296007",
    )
    assert r.json()["chamado_ml_status"] == "enviada", r.json()
    d = fake.disputes[0]
    assert d["reason"] == 84
    assert d["image_list"] == [{"module_index": 1, "requirement": "Photos",
                                "image_url": ["https://fileproxy/video-expedicao.png"]}]
    assert d["text"].startswith("Recebemos o pacote da devolução sem o produto dentro.")
    assert "QR code" in d["text"] and "Comprovante da expedição (fotos/vídeo do envio): https://drive.x/video-296007" in d["text"]


async def test_so_reembolso_nao_recebido_continua_com_cartao(client, make_user, auth_as, db, inline, monkeypatch):
    """Só reembolso (needs_logistics false): não há pacote de volta — "Não recebido"
    é a alegação do comprador sobre a IDA e o vídeo da expedição segue como prova."""
    fake = _FakeShopee(entregue=False)

    async def _det(return_sn):
        return {"return_sn": return_sn, "status": "ACCEPTED", "return_solution": 1, "needs_logistics": False,
                "reason": "NOT_RECEIPT", "seller_compensation": {"seller_compensation_status": "PENDING_REQUEST"}}

    async def _reasons(return_sn):
        return [{"dispute_reason": 1, "dispute_requirement": "Prova de envio",
                 "evidence_module_list": [{"module_index": 1, "requirement": "Comprovante", "is_required": True}]}]

    fake.get_return_detail = _det
    fake.get_return_dispute_reason = _reasons
    r, _user = await _lancar(
        client, db, make_user, auth_as, monkeypatch, fake, numero="296008", sn="2609ATVNR008",
        rastreio={"devolucao_tipo_auto": "REFUND"}, link_envio="https://drive.x/video-296008",
    )
    assert r.json()["chamado_ml_status"] == "enviada", r.json()
    d = fake.disputes[0]
    assert d["reason"] == 1 and d["image_list"][0]["image_url"] == ["https://fileproxy/video-expedicao.png"]
    assert "QR code" in d["text"] and "https://drive.x/video-296008" in d["text"]


async def test_link_envio_obrigatorio_exclui_nao_recebido(client, make_user, auth_as, db, inline, monkeypatch):
    assert svc.link_envio_obrigatorio(Devolution(sku="b001.26", motivo_devolucao="Não recebido")) is False
    assert svc.link_envio_obrigatorio(Devolution(sku="b001.26", motivo_devolucao="Golpe")) is True
    assert svc.link_envio_obrigatorio(Devolution(sku="dg048.ra", motivo_devolucao="Item faltando")) is True
    assert svc.link_envio_obrigatorio(Devolution(sku="a003.ra", motivo_devolucao="Golpe")) is False
    # pela rota: mala + Não recebido sem link → 201 (antes: 422 link_envio_obrigatorio)
    fake = _FakeShopee()
    r, _user = await _lancar(client, db, make_user, auth_as, monkeypatch, fake, numero="296009", sn="2609ATVNR009")
    assert r.json()["link_envio"] is None and r.json()["chamado_ml_status"] == "enviada"
    # golpe + mala sem link segue travado
    r2 = await client.post(
        "/api/devolutions",
        json={"conta": "atv", "pedido_bling": "296009", "pedido_marketplace": "2609ATVNR009",
              "sku": "b001.26", "condicao_produto": "Não devolvido", "motivo_devolucao": "Golpe"},
    )
    assert r2.status_code == 422 and r2.json()["detail"]["code"] == "link_envio_obrigatorio"


async def test_link_envio_precisa_ser_url_http(client, make_user, auth_as, db, inline, monkeypatch):
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero="296010", numeroloja="2609ATVNR010", platform="shopee", conta="atv", loja="88")
    base = {"conta": "atv", "pedido_bling": "296010", "pedido_marketplace": "2609ATVNR010",
            "sku": "a003.ra", "condicao_produto": "Novo"}
    r = await client.post("/api/devolutions", json={**base, "link_envio": "nao ha, nao recebido"})
    assert r.status_code == 422, r.text
    erros = r.json()["detail"]
    assert isinstance(erros, list) and erros[0]["type"] == "link_envio_invalido", erros
    assert erros[0]["msg"] == "Link de envio precisa ser um endereço http(s)" and erros[0]["loc"][-1] == "link_envio"
    # vazio vira None; URL passa (com espaço em volta)
    r = await client.post("/api/devolutions", json={**base, "link_envio": "  "})
    assert r.status_code == 201 and r.json()["link_envio"] is None, r.text
    dev_id = r.json()["id"]
    p = await client.patch(f"/api/devolutions/{dev_id}", json={"link_envio": " https://drive.x/video-296010 "})
    assert p.status_code == 200 and p.json()["link_envio"] == "https://drive.x/video-296010", p.text
    p = await client.patch(f"/api/devolutions/{dev_id}", json={"link_envio": "drive.x/video"})
    assert p.status_code == 422 and p.json()["detail"][0]["type"] == "link_envio_invalido", p.text
    p = await client.patch(f"/api/devolutions/{dev_id}", json={"link_envio": ""})
    assert p.status_code == 200 and p.json()["link_envio"] is None, p.text


def test_texto_nao_recebido_sem_rastreio_e_status_desconhecido():
    dev = Devolution(pedido_marketplace="2609X", sku="b001.26", produtos="Mala", motivo_devolucao="Não recebido")
    det = {"reverse_logistics_status": "LOGISTICS_FOO"}
    txt = svc._texto_nao_recebido(dev, det, "", None)
    assert txt.split("\n")[0] == "O pacote da devolução (sem código de rastreio) ainda não chegou até nós — situação na SPX: LOGISTICS_FOO."
    assert svc._reversa_legivel({}) == "sem movimentação registrada"
    assert svc._reversa_legivel({"reverse_logistics_status": "LOGISTICS_REQUEST_CREATED"}) == "aguardando postagem"
    assert svc._modulo_quebrado([{"module_index": 1, "is_required": True}]) is False
    assert svc._modulo_quebrado([{"module_index": 0, "is_required": True}]) is True
    assert svc._modulo_quebrado([{"module_index": None}]) is True
    assert svc._modulo_quebrado([{"module_index": 0, "is_required": False}]) is False


async def test_nao_recebido_entregue_com_foto_real_manda_a_foto(client, make_user, auth_as, db, inline, monkeypatch):
    """Foto REAL na linha (ex. print do rastreio) vai como imagem; o texto factual não muda."""
    fake = _FakeShopee()
    # lança sem motivo de chamado, anexa a foto e só então marca "Não recebido"
    r, _user = await _lancar(client, db, make_user, auth_as, monkeypatch, fake, numero="296011", sn="2609ATVNR011",
                             motivo="Dano funcional / Não funciona")
    assert r.json()["tem_chamado"] is False
    up = await client.post(f"/api/devolutions/{r.json()['id']}/anexos",
                           files={"file": ("rastreio.png", PNG_1PX, "image/png")})
    assert up.status_code == 201, up.text
    p = await client.patch(f"/api/devolutions/{r.json()['id']}", json={"motivo_devolucao": "Não recebido"})
    assert p.status_code == 200 and p.json()["chamado_ml_status"] == "enviada", p.text
    d = fake.disputes[0]
    assert fake.converted == [("2609296011RSN", "rastreio.png")]
    assert d["image_list"] == [{"module_index": 1, "requirement": "", "image_url": ["https://fileproxy/rastreio.png"]}]
    assert d["text"].startswith(ENTREGUE_TXT) and "foto(s) em anexo" not in d["text"]


# ---- Revisão 21/09: o cartão do vídeo é barrado DENTRO de `_disparar_shopee`, pelo
# detalhe do caso — vale pro cartão já persistido (rodada/motivo anterior) com foto real
# junto, e pra linha do Acompanhamento divergente do que a Shopee tem.


async def _cartao_persistido_com_golpe(client, db, make_user, auth_as, monkeypatch, fake, *, numero, sn):
    """Abertura pendente de Golpe (status desconhecido → aguardando pacote) que já gerou o
    cartão do vídeo; depois a operadora anexa um print do rastreio."""
    r, user = await _lancar(
        client, db, make_user, auth_as, monkeypatch, fake, numero=numero, sn=sn,
        motivo="Golpe", link_envio=f"https://drive.x/video-{numero}",
    )
    assert r.json()["chamado_ml_erro"] == "shopee_aguardando_pacote", r.json()
    anexos = (await db.execute(select(DevolucaoAnexo))).scalars().all()
    assert [a.filename for a in anexos] == [cartao.CARTAO_VIDEO_NOME]
    up = await client.post(f"/api/devolutions/{r.json()['id']}/anexos",
                           files={"file": ("rastreio.png", PNG_1PX, "image/png")})
    assert up.status_code == 201, up.text
    assert fake.disputes == []
    return r.json()["id"]


async def test_cartao_persistido_com_foto_real_nao_sobe_na_disputa_nao_recebi(client, make_user, auth_as, db, inline, monkeypatch):
    fake = _FakeShopee(status="")  # entregue por padrão; status vazio segura a abertura
    dev_id = await _cartao_persistido_com_golpe(client, db, make_user, auth_as, monkeypatch, fake,
                                                numero="296012", sn="2609ATVNR012")
    fake.status = "ACCEPTED"
    p = await client.patch(f"/api/devolutions/{dev_id}", json={"motivo_devolucao": "Não recebido"})
    assert p.status_code == 200 and p.json()["chamado_ml_status"] == "enviada", p.text
    # só a foto real sobe; o cartão (QR pro Link envio) não vai nem fica na linha
    assert fake.converted == [("2609296012RSN", "rastreio.png")]
    d = fake.disputes[0]
    assert d["reason"] == 81 and d["image_list"] == [{"module_index": 1, "requirement": "", "image_url": ["https://fileproxy/rastreio.png"]}]
    assert d["text"].startswith(ENTREGUE_TXT) and "QR code" not in d["text"]
    ch = await _chamado_de(db, "296012")
    await _sem_cartao(db, ch.id)
    assert [a.filename for a in (await db.execute(select(DevolucaoAnexo))).scalars().all()] == ["rastreio.png"]
    robo = (await db.execute(select(ChamadoAnexo).where(ChamadoAnexo.chamado_id == ch.id))).scalars().all()
    assert [a.filename for a in robo] == ["rastreio.png"]
    assert any("(1 foto(s))" in t for t in await _sistema_txts(db, ch.id))


async def test_cartao_persistido_com_foto_real_em_transito_nao_vai_pro_robo(client, make_user, auth_as, db, inline, monkeypatch):
    fake = _FakeShopee(status="", entregue=False)
    dev_id = await _cartao_persistido_com_golpe(client, db, make_user, auth_as, monkeypatch, fake,
                                                numero="296013", sn="2609ATVNR013")
    fake.status = "ACCEPTED"
    p = await client.patch(f"/api/devolutions/{dev_id}", json={"motivo_devolucao": "Não recebido"})
    assert p.status_code == 200 and p.json()["chamado_ml_status"] == "pendente" and p.json()["chamado_ml_erro"] is None, p.text
    assert fake.disputes == [] and fake.converted == []
    ch = await _chamado_de(db, "296013")
    ab = await _abertura(db, ch.id)
    assert ch.canal == "robo" and ab.canal == "robo" and ab.texto.split("\n")[0] == TRANSITO_TXT
    # a tarefa do robô leva a foto real, não o cartão
    robo = (await db.execute(select(ChamadoAnexo).where(ChamadoAnexo.chamado_id == ch.id))).scalars().all()
    assert [a.filename for a in robo] == ["rastreio.png"]
    await _sem_cartao(db, ch.id)


async def test_linha_fraude_mas_shopee_com_pacote_de_volta_nao_manda_cartao(client, make_user, auth_as, db, inline, monkeypatch):
    """Acompanhamento diz Fraude / tipo REFUND (movido na mão ou desatualizado), mas a
    Shopee tem pacote de volta (needs_logistics true): quem manda é o detalhe."""
    fake = _FakeShopee()

    async def _det(return_sn):
        det = await _FakeShopee.get_return_detail(fake, return_sn)
        det.update({"return_solution": 0, "needs_logistics": True})
        return det

    fake.get_return_detail = _det
    r, _user = await _lancar(
        client, db, make_user, auth_as, monkeypatch, fake, numero="296014", sn="2609ATVNR014",
        rastreio={"devolucao_tipo_auto": "REFUND", "fila_manual": "fraude"},
        link_envio="https://drive.x/video-296014",
    )
    assert r.json()["chamado_ml_status"] == "enviada", r.json()
    d = fake.disputes[0]
    assert d["reason"] == 81 and d["image_list"] is None and d["text"].startswith(ENTREGUE_TXT)
    assert "QR code" not in d["text"] and fake.converted == []
    await _sem_cartao(db, (await _chamado_de(db, "296014")).id)
    # em trânsito: a tarefa do robô também sai sem cartão
    fake2 = _FakeShopee(entregue=False)

    async def _det2(return_sn):
        det = await _FakeShopee.get_return_detail(fake2, return_sn)
        det.update({"return_solution": 0, "needs_logistics": True})
        return det

    fake2.get_return_detail = _det2
    r2, _u = await _lancar(
        client, db, make_user, auth_as, monkeypatch, fake2, numero="296015", sn="2609ATVNR015",
        rastreio={"fila_manual": "fraude"}, link_envio="https://drive.x/video-296015",
    )
    assert r2.json()["chamado_ml_status"] == "pendente" and r2.json()["chamado_ml_erro"] is None, r2.json()
    ch2 = await _chamado_de(db, "296015")
    assert ch2.canal == "robo" and fake2.disputes == []
    await _sem_cartao(db, ch2.id)


async def test_so_reembolso_sem_tipo_na_linha_manda_o_cartao_na_hora(client, make_user, auth_as, db, inline, monkeypatch):
    """Linha do Acompanhamento ainda sem tipo (sync não rodou) ou até em
    'acompanhamento': a Shopee diz só reembolso → cartão como prova da IDA na hora,
    sem esperar rodada do cron (regressão apontada na revisão de 21/09)."""
    fake = _FakeShopee(entregue=False)

    async def _det(return_sn):
        return {"return_sn": return_sn, "status": "ACCEPTED", "return_solution": 1, "needs_logistics": False,
                "reason": "NOT_RECEIPT", "seller_compensation": {"seller_compensation_status": "PENDING_REQUEST"}}

    async def _reasons(return_sn):
        return [{"dispute_reason": 1, "dispute_requirement": "Prova de envio",
                 "evidence_module_list": [{"module_index": 1, "requirement": "Comprovante", "is_required": True}]}]

    fake.get_return_detail = _det
    fake.get_return_dispute_reason = _reasons
    for numero, sn, rastreio in (("296016", "2609ATVNR016", None),
                                 ("296017", "2609ATVNR017", {"fila_manual": "acompanhamento"})):
        r, _user = await _lancar(
            client, db, make_user, auth_as, monkeypatch, fake, numero=numero, sn=sn,
            rastreio=rastreio, link_envio=f"https://drive.x/video-{numero}",
        )
        assert r.json()["chamado_ml_status"] == "enviada", (numero, r.json())
        d = fake.disputes[-1]
        assert d["reason"] == 1 and d["image_list"][0]["image_url"] == ["https://fileproxy/video-expedicao.png"]
        assert "QR code" in d["text"] and f"https://drive.x/video-{numero}" in d["text"]
        ch = await _chamado_de(db, numero)
        ab = await _abertura(db, ch.id)
        assert ab.texto == d["text"]
        anexos = (await db.execute(select(ChamadoAnexo).where(ChamadoAnexo.chamado_id == ch.id))).scalars().all()
        assert [a.filename for a in anexos] == [cartao.CARTAO_VIDEO_NOME]
    assert len(fake.disputes) == 2


def test_reversa_legivel_cobre_nao_iniciado_e_entregue():
    assert svc._reversa_legivel({"reverse_logistics_status": "LOGISTICS_NOT_STARTED"}) == "não iniciado"
    assert svc._reversa_legivel({"reverse_logistics_status": "LOGISTICS_DELIVERY_DONE"}) == "entregue"
    det = {"reverse_logistics_status": "LOGISTICS_LOST"}
    nota = svc._nota_robo_nao_recebido_sem_entrega(det, "2609X")
    assert nota.startswith("Pacote da devolução 2609X sem entrega registrada pela SPX (extraviado):")
    assert "em trânsito" not in nota


async def test_cartao_persistido_com_link_apagado_some_e_a_disputa_sai_sem_imagem(client, make_user, auth_as, db, inline, monkeypatch):
    """Cartão de rodada anterior e a operadora apagou o Link envio ao trocar o motivo:
    o cartão órfão some (apagado duas vezes na mesma sessão — `disparar` e
    `_cartao_nao_recebido_shopee` — sem erro) e a disputa "não recebi" vai sem imagem."""
    fake = _FakeShopee(status="")
    r, _user = await _lancar(
        client, db, make_user, auth_as, monkeypatch, fake, numero="296018", sn="2609ATVNR018",
        motivo="Golpe", link_envio="https://drive.x/video-296018",
    )
    assert r.json()["chamado_ml_erro"] == "shopee_aguardando_pacote", r.json()
    assert [a.filename for a in (await db.execute(select(DevolucaoAnexo))).scalars().all()] == [cartao.CARTAO_VIDEO_NOME]
    fake.status = "ACCEPTED"
    p = await client.patch(f"/api/devolutions/{r.json()['id']}", json={"motivo_devolucao": "Não recebido", "link_envio": ""})
    assert p.status_code == 200 and p.json()["chamado_ml_status"] == "enviada", p.text
    d = fake.disputes[0]
    assert d["reason"] == 81 and d["image_list"] is None and d["text"].startswith(ENTREGUE_TXT)
    assert fake.converted == []
    assert (await db.execute(select(DevolucaoAnexo))).scalars().all() == []
    await _sem_cartao(db, (await _chamado_de(db, "296018")).id)
