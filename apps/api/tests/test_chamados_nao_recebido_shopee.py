# ruff: noqa: E501
"""Shopee, motivo "Não recebido" (o pacote de VOLTA não chegou) — Vinicius 21 e 22/09.

Em 21/09 o critério era a SPX: reversa entregue → disputa pela API; em trânsito →
robô. Em 22/09 o dono fechou a porta de vez ("quando o item é 'não recebi' não vai
poder abrir pela disputa, somente por assistente do vendedor — estamos perdendo
tudo"): 4 disputas abertas pela API, 0 ganhas, e em 3 delas a SPX JÁ dava a reversa
como entregue; e como a disputa é TIRO ÚNICO por devolução, a errada queima a certa
(293460). Então, com pacote de VOLTA:
  - entregue ou não, NUNCA sai `client.dispute`: a abertura vira tarefa do robô no
    Seller Center (`_RoboError` → `_encaminhar_robo`, canal `robo`, `pendente`);
  - o texto é o FACTUAL (`_texto_nao_recebido`: SPX + rastreio + pedido), sem cartão
    do vídeo, sem "comprovante da expedição", com o que o operador digitou virando
    observação; a nota do histórico conta se a SPX deu entrega ou não;
  - nem o estado do return segura mais o caso (não espera "pacote chegar"), porque a
    janela de contestação some em horas;
  - Link envio deixa de ser obrigatório nesse motivo e, quando vem, tem que ser URL.
Só reembolso (return_solution 1 / needs_logistics false) é outro caso: ali "não
recebi" é a alegação do COMPRADOR sobre a IDA e a disputa segue pela API com o
cartão do vídeo como prova.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import (
    ChamadoAnexo,
    DevolucaoAnexo,
    DevolucaoRastreio,
    Devolution,
    EstoquePedidoVideo,
)
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
        link_envio="https://mega.nz/file/video-296001#K3yDoVideoNaMega0123456789abcdefghij", observacao="Cliente diz que postou dia 12.",
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


async def test_nao_recebido_entregue_tambem_vai_pro_robo_sem_disputa(client, make_user, auth_as, db, inline, monkeypatch):
    """22/09: a SPX ter dado a reversa como ENTREGUE não muda mais nada — 3 das 4
    disputas perdidas estavam assim. O texto factual vira a tarefa do robô, e a API
    de motivos nem é consultada."""
    fake = _FakeShopee()  # entregue: LOGISTICS_DELIVERY_DONE em 16/09 11:11
    chamadas = {"reasons": 0}
    original = fake.get_return_dispute_reason

    async def _reasons(return_sn):
        chamadas["reasons"] += 1
        return await original(return_sn)

    fake.get_return_dispute_reason = _reasons
    r, _user = await _lancar(
        client, db, make_user, auth_as, monkeypatch, fake, numero="296002", sn="2609ATVNR002",
        link_envio="https://mega.nz/file/video-296002#K3yDoVideoNaMega0123456789abcdefghij", observacao="Portaria não recebeu nada.",
    )
    assert r.json()["chamado_ml_status"] == "pendente" and r.json()["chamado_ml_erro"] is None, r.json()
    assert fake.disputes == [] and chamadas["reasons"] == 0
    ch = await _chamado_de(db, "296002")
    ab = await _abertura(db, ch.id)
    assert ch.canal == "robo" and ab.canal == "robo" and ab.status == "pendente" and ch.chamado is None
    txt = ab.texto
    assert txt.startswith(ENTREGUE_TXT + HOJE + "."), txt
    assert "Pedido 2609ATVNR002 · SKU b001.26 · Mala Listrada tamanho 26." in txt
    assert "Observação: Portaria não recebeu nada." in txt
    assert txt.endswith("Solicitamos o comprovante de entrega (foto/assinatura), a apuração junto à transportadora e que o reembolso não seja liberado até a conclusão.")
    for proibido in ("QR code", "Comprovante da expedição", "vídeo", "foto(s) em anexo", "Solicitamos a análise do caso"):
        assert proibido not in txt, (proibido, txt)
    # a nota do histórico conta que a SPX deu entrega (e o pacote não chegou assim mesmo)
    hist = await _sistema_txts(db, ch.id)
    assert any("SPX deu como entregue em 16/09/2026 11:11, mas o pacote não chegou até nós" in t
               and "Assistente do Vendedor" in t for t in hist), hist
    await _sem_cartao(db, ch.id)
    assert fake.converted == []


async def test_nao_recebido_entregue_sem_observacao_nao_tem_linha_vazia(client, make_user, auth_as, db, inline, monkeypatch):
    fake = _FakeShopee()
    r, _user = await _lancar(client, db, make_user, auth_as, monkeypatch, fake, numero="296003", sn="2609ATVNR003")
    assert r.json()["chamado_ml_status"] == "pendente" and r.json()["chamado_ml_erro"] is None, r.json()
    ch = await _chamado_de(db, "296003")
    linhas = (await _abertura(db, ch.id)).texto.split("\n")
    assert len(linhas) == 3 and not any(ln.startswith("Observação") for ln in linhas), linhas


async def test_nao_recebido_nota_do_historico_traz_o_prazo_da_shopee(client, make_user, auth_as, db, inline, monkeypatch):
    """O `return_seller_due_date` entra na nota: o robô precisa saber até quando dá
    pra abrir a solicitação no Seller Center. Módulo de evidência obrigatório (a
    série 81 vinha com module_index 0, que a API nunca aceitava) não muda nada —
    ninguém mais consulta os motivos nesse caso."""
    fake = _FakeShopee()
    due = 1789700000  # 17/09/2026 23:53 BRT

    async def _det(return_sn):
        det = await _FakeShopee.get_return_detail(fake, return_sn)
        det["return_seller_due_date"] = due
        return det

    async def _reasons(return_sn):
        raise AssertionError("get_return_dispute_reason não pode ser chamado em 'Não recebido'")

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
    assert any("prazo da Shopee: 17/09 23:53" in t and "tiro único" in t for t in hist), hist


async def test_nao_recebido_nunca_chama_dispute_e_outro_motivo_ainda_falha(client, make_user, auth_as, db, inline, monkeypatch):
    """O `dispute` explodiria se fosse chamado: em "Não recebido" ele não é. Em outro
    motivo o erro cru da Shopee continua virando `falhou` com o erro."""
    fake = _FakeShopee()

    async def _dispute(return_sn, **kw):
        raise AssertionError("dispute não pode ser chamado em 'Não recebido'")

    fake.dispute = _dispute
    r, _user = await _lancar(client, db, make_user, auth_as, monkeypatch, fake, numero="296005", sn="2609ATVNR005")
    assert r.json()["chamado_ml_status"] == "pendente" and r.json()["chamado_ml_erro"] is None, r.json()
    ch = await _chamado_de(db, "296005")
    ab = await _abertura(db, ch.id)
    assert ch.canal == "robo" and ab.status == "pendente" and ab.texto.startswith(ENTREGUE_TXT)
    # outro motivo (Golpe, com o cartão do vídeo como foto): erro cru continua `falhou`
    fake2 = _FakeShopee()

    async def _dispute2(return_sn, **kw):
        raise RuntimeError("shopee_dispute error=returnsn.illegal")

    fake2.dispute = _dispute2
    r2, _u = await _lancar(client, db, make_user, auth_as, monkeypatch, fake2, numero="296006", sn="2609ATVNR006",
                           motivo="Golpe", link_envio="https://mega.nz/file/video-296006#K3yDoVideoNaMega0123456789abcdefghij")
    assert r2.json()["chamado_ml_status"] == "falhou" and "returnsn.illegal" in r2.json()["chamado_ml_erro"]


async def test_outro_motivo_continua_igual_com_cartao_e_texto_padrao(client, make_user, auth_as, db, inline, monkeypatch):
    """Golpe: nada muda — a SPX não entra na conta, o cartão do vídeo vai como a
    foto do módulo obrigatório e o texto é o padrão (com o comprovante da expedição)."""
    fake = _FakeShopee(entregue=False)
    r, _user = await _lancar(
        client, db, make_user, auth_as, monkeypatch, fake, numero="296007", sn="2609ATVNR007",
        motivo="Golpe", link_envio="https://mega.nz/file/video-296007#K3yDoVideoNaMega0123456789abcdefghij",
    )
    assert r.json()["chamado_ml_status"] == "enviada", r.json()
    d = fake.disputes[0]
    assert d["reason"] == 84
    assert d["image_list"] == [{"module_index": 1, "requirement": "Photos",
                                "image_url": ["https://fileproxy/video-expedicao.png"]}]
    assert d["text"].startswith("Recebemos o pacote da devolução sem o produto dentro.")
    assert "QR code" in d["text"] and "Comprovante da expedição (fotos/vídeo do envio): https://mega.nz/file/video-296007#K3yDoVideoNaMega0123456789abcdefghij" in d["text"]


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
        rastreio={"devolucao_tipo_auto": "REFUND"}, link_envio="https://mega.nz/file/video-296008#K3yDoVideoNaMega0123456789abcdefghij",
    )
    assert r.json()["chamado_ml_status"] == "enviada", r.json()
    d = fake.disputes[0]
    assert d["reason"] == 1 and d["image_list"][0]["image_url"] == ["https://fileproxy/video-expedicao.png"]
    assert "QR code" in d["text"] and "https://mega.nz/file/video-296008#K3yDoVideoNaMega0123456789abcdefghij" in d["text"]
    # 24/09: não há pacote de volta — o texto é o do só reembolso, não "o pacote não chegou"
    assert d["text"].startswith("O comprador pediu reembolso SEM devolução do produto alegando que não recebeu o pedido. A Shopee aprovou"), d["text"]
    assert "O pacote da devolução" not in d["text"] and "Solicitamos a análise do caso" not in d["text"]
    assert "Pedido 2609ATVNR008 · SKU b001.26 · Mala Listrada tamanho 26." in d["text"]


async def test_link_envio_obrigatorio_exclui_nao_recebido(client, make_user, auth_as, db, inline, monkeypatch):
    assert svc.link_envio_obrigatorio(Devolution(sku="b001.26", motivo_devolucao="Não recebido")) is False
    # Vinicius 21/09 (caso 294554, Oukitel voltou com senha): Bloqueado não pede o link.
    assert svc.link_envio_obrigatorio(Devolution(sku="dg091.sp", motivo_devolucao="Bloqueado")) is False
    assert svc.link_envio_obrigatorio(Devolution(sku="dg091.sp", motivo_devolucao="Mudou de ideia")) is False
    assert svc.link_envio_obrigatorio(Devolution(sku="dg091.sp", motivo_devolucao="Danificado (Outros)")) is True
    assert svc.link_envio_obrigatorio(Devolution(sku="b001.26", motivo_devolucao="Golpe")) is True
    assert svc.link_envio_obrigatorio(Devolution(sku="dg048.ra", motivo_devolucao="Item faltando")) is True
    assert svc.link_envio_obrigatorio(Devolution(sku="a003.ra", motivo_devolucao="Golpe")) is False
    # pela rota: mala + Não recebido sem link → 201 (antes: 422 link_envio_obrigatorio)
    fake = _FakeShopee()
    r, _user = await _lancar(client, db, make_user, auth_as, monkeypatch, fake, numero="296009", sn="2609ATVNR009")
    assert r.json()["link_envio"] is None and r.json()["chamado_ml_status"] == "pendente"
    assert r.json()["chamado_ml_erro"] is None and fake.disputes == []  # tarefa do robô
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
    p = await client.patch(f"/api/devolutions/{dev_id}", json={"link_envio": " https://mega.nz/file/video-296010#K3yDoVideoNaMega0123456789abcdefghij "})
    assert p.status_code == 200 and p.json()["link_envio"] == "https://mega.nz/file/video-296010#K3yDoVideoNaMega0123456789abcdefghij", p.text
    p = await client.patch(f"/api/devolutions/{dev_id}", json={"link_envio": "drive.x/video"})
    assert p.status_code == 422 and p.json()["detail"][0]["type"] == "link_envio_invalido", p.text
    p = await client.patch(f"/api/devolutions/{dev_id}", json={"link_envio": ""})
    assert p.status_code == 200 and p.json()["link_envio"] is None, p.text


async def test_link_envio_novo_so_mega_e_o_video_que_o_sistema_ja_tem_vale(
    client, make_user, auth_as, db, inline, monkeypatch
):
    """Vinicius 23/09: o vídeo da expedição mora na MEGA. Link NOVO do Drive ou da
    MEGA sem a chave (a parte depois do #) → 422. O link que o sistema já tem pro
    pedido continua valendo: o do Controle de Estoque (pedido embalado antes da
    troca tem o vídeo no Drive) e o que já estava gravado na linha."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero="296030", numeroloja="2609ATVNR030", platform="shopee", conta="atv", loja="88")
    base = {"conta": "atv", "pedido_bling": "296030", "pedido_marketplace": "2609ATVNR030",
            "sku": "a003.ra", "condicao_produto": "Novo"}
    for ruim, code in (
        ("https://drive.google.com/file/d/1Novo/view", "video_link_nao_mega"),
        ("https://mega.nz/file/oMV0HRyS", "video_link_sem_chave"),
        ("https://mega.nz/folder/AbCdEfGh#Kk0123456789abcdefghij", "video_link_pasta_mega"),
    ):
        r = await client.post("/api/devolutions", json={**base, "link_envio": ruim})
        assert r.status_code == 422 and r.json()["detail"]["code"] == code, (ruim, r.text)

    estoque = "https://drive.google.com/file/d/1DoControleDeEstoque/view"
    db.add(EstoquePedidoVideo(pedido_bling="296030", link=estoque, salvo_por=user.id))
    await db.commit()
    r = await client.post("/api/devolutions", json={**base, "link_envio": estoque})
    assert r.status_code == 201 and r.json()["link_envio"] == estoque, r.text
    dev_id = r.json()["id"]

    # linha com Drive de antes da regra: o resto dela continua salvando
    legado = "https://drive.google.com/file/d/1DeAntesDaRegra/view"
    row = await db.get(Devolution, dev_id)
    row.link_envio = legado
    await db.commit()
    p = await client.patch(f"/api/devolutions/{dev_id}", json={"link_envio": legado, "observacao": "conferido"})
    assert p.status_code == 200 and p.json()["link_envio"] == legado, p.text
    # trocar por outro Drive não entra; pela MEGA sim
    p = await client.patch(f"/api/devolutions/{dev_id}", json={"link_envio": "https://drive.google.com/file/d/1Outro/view"})
    assert p.status_code == 422 and p.json()["detail"]["code"] == "video_link_nao_mega", p.text
    mega = "https://mega.nz/file/oMV0HRyS#vK2VCD7kON8nv9zplMPFaPfWSzP5jxYrhprVtjQd3V8"
    p = await client.patch(f"/api/devolutions/{dev_id}", json={"link_envio": mega})
    assert p.status_code == 200 and p.json()["link_envio"] == mega, p.text


def test_texto_nao_recebido_sem_rastreio_e_status_desconhecido():
    dev = Devolution(pedido_marketplace="2609X", sku="b001.26", produtos="Mala", motivo_devolucao="Não recebido")
    det = {"reverse_logistics_status": "LOGISTICS_FOO"}
    txt = svc._texto_nao_recebido(dev, det, "", None)
    assert txt.split("\n")[0] == "O pacote da devolução (sem código de rastreio) ainda não chegou até nós — situação na SPX: LOGISTICS_FOO."
    assert svc._reversa_legivel({}) == "sem movimentação registrada"
    assert svc._reversa_legivel({"reverse_logistics_status": "LOGISTICS_REQUEST_CREATED"}) == "aguardando postagem"


async def test_nao_recebido_entregue_com_foto_real_leva_a_foto_pro_robo(client, make_user, auth_as, db, inline, monkeypatch):
    """Foto REAL na linha (ex. print do rastreio) vai junto na tarefa do robô — nada
    de `convert_image`, que só serve pra disputa da API. O texto factual não muda."""
    fake = _FakeShopee()
    # lança sem motivo de chamado, anexa a foto e só então marca "Não recebido"
    r, _user = await _lancar(client, db, make_user, auth_as, monkeypatch, fake, numero="296011", sn="2609ATVNR011",
                             motivo="Dano funcional / Não funciona")
    assert r.json()["tem_chamado"] is False
    up = await client.post(f"/api/devolutions/{r.json()['id']}/anexos",
                           files={"file": ("rastreio.png", PNG_1PX, "image/png")})
    assert up.status_code == 201, up.text
    p = await client.patch(f"/api/devolutions/{r.json()['id']}", json={"motivo_devolucao": "Não recebido"})
    assert p.status_code == 200 and p.json()["chamado_ml_status"] == "pendente", p.text
    assert p.json()["chamado_ml_erro"] is None, p.json()
    assert fake.disputes == [] and fake.converted == []
    ch = await _chamado_de(db, "296011")
    ab = await _abertura(db, ch.id)
    assert ch.canal == "robo" and ab.canal == "robo"
    assert ab.texto.startswith(ENTREGUE_TXT) and "foto(s) em anexo" not in ab.texto
    robo = (await db.execute(select(ChamadoAnexo).where(ChamadoAnexo.chamado_id == ch.id))).scalars().all()
    assert [a.filename for a in robo] == ["rastreio.png"]


# ---- Revisão 21/09: o cartão do vídeo é barrado DENTRO de `_disparar_shopee`, pelo
# detalhe do caso — vale pro cartão já persistido (rodada/motivo anterior) com foto real
# junto, e pra linha do Acompanhamento divergente do que a Shopee tem.


async def _cartao_persistido_com_golpe(client, db, make_user, auth_as, monkeypatch, fake, *, numero, sn):
    """Abertura pendente de Golpe (status desconhecido → aguardando pacote) que já gerou o
    cartão do vídeo; depois a operadora anexa um print do rastreio."""
    r, user = await _lancar(
        client, db, make_user, auth_as, monkeypatch, fake, numero=numero, sn=sn,
        motivo="Golpe", link_envio=f"https://mega.nz/file/video-{numero}#K3yDoVideoNaMega0123456789abcdefghij",
    )
    assert r.json()["chamado_ml_erro"] == "shopee_aguardando_pacote", r.json()
    anexos = (await db.execute(select(DevolucaoAnexo))).scalars().all()
    assert [a.filename for a in anexos] == [cartao.CARTAO_VIDEO_NOME]
    up = await client.post(f"/api/devolutions/{r.json()['id']}/anexos",
                           files={"file": ("rastreio.png", PNG_1PX, "image/png")})
    assert up.status_code == 201, up.text
    assert fake.disputes == []
    return r.json()["id"]


async def test_cartao_persistido_com_foto_real_entregue_vai_pro_robo_sem_cartao(client, make_user, auth_as, db, inline, monkeypatch):
    fake = _FakeShopee(status="")  # entregue por padrão; status vazio segura a abertura
    dev_id = await _cartao_persistido_com_golpe(client, db, make_user, auth_as, monkeypatch, fake,
                                                numero="296012", sn="2609ATVNR012")
    fake.status = "ACCEPTED"
    p = await client.patch(f"/api/devolutions/{dev_id}", json={"motivo_devolucao": "Não recebido"})
    assert p.status_code == 200 and p.json()["chamado_ml_status"] == "pendente", p.text
    assert p.json()["chamado_ml_erro"] is None, p.json()
    # nada sobe pela API; o cartão (QR pro Link envio) não vai nem fica na linha
    assert fake.disputes == [] and fake.converted == []
    ch = await _chamado_de(db, "296012")
    ab = await _abertura(db, ch.id)
    assert ch.canal == "robo" and ab.canal == "robo"
    assert ab.texto.startswith(ENTREGUE_TXT) and "QR code" not in ab.texto
    await _sem_cartao(db, ch.id)
    assert [a.filename for a in (await db.execute(select(DevolucaoAnexo))).scalars().all()] == ["rastreio.png"]
    robo = (await db.execute(select(ChamadoAnexo).where(ChamadoAnexo.chamado_id == ch.id))).scalars().all()
    assert [a.filename for a in robo] == ["rastreio.png"]


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
        link_envio="https://mega.nz/file/video-296014#K3yDoVideoNaMega0123456789abcdefghij",
    )
    assert r.json()["chamado_ml_status"] == "pendente" and r.json()["chamado_ml_erro"] is None, r.json()
    ch = await _chamado_de(db, "296014")
    ab = await _abertura(db, ch.id)
    assert ch.canal == "robo" and fake.disputes == [] and fake.converted == []
    assert ab.texto.startswith(ENTREGUE_TXT) and "QR code" not in ab.texto
    await _sem_cartao(db, ch.id)
    # em trânsito: a tarefa do robô também sai sem cartão
    fake2 = _FakeShopee(entregue=False)

    async def _det2(return_sn):
        det = await _FakeShopee.get_return_detail(fake2, return_sn)
        det.update({"return_solution": 0, "needs_logistics": True})
        return det

    fake2.get_return_detail = _det2
    r2, _u = await _lancar(
        client, db, make_user, auth_as, monkeypatch, fake2, numero="296015", sn="2609ATVNR015",
        rastreio={"fila_manual": "fraude"}, link_envio="https://mega.nz/file/video-296015#K3yDoVideoNaMega0123456789abcdefghij",
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
            rastreio=rastreio, link_envio=f"https://mega.nz/file/video-{numero}#K3yDoVideoNaMega0123456789abcdefghij",
        )
        assert r.json()["chamado_ml_status"] == "enviada", (numero, r.json())
        d = fake.disputes[-1]
        assert d["reason"] == 1 and d["image_list"][0]["image_url"] == ["https://fileproxy/video-expedicao.png"]
        assert "QR code" in d["text"] and f"https://mega.nz/file/video-{numero}#K3yDoVideoNaMega0123456789abcdefghij" in d["text"]
        ch = await _chamado_de(db, numero)
        ab = await _abertura(db, ch.id)
        assert ab.texto == d["text"]
        anexos = (await db.execute(select(ChamadoAnexo).where(ChamadoAnexo.chamado_id == ch.id))).scalars().all()
        assert [a.filename for a in anexos] == [cartao.CARTAO_VIDEO_NOME]
    assert len(fake.disputes) == 2


def test_reversa_legivel_e_nota_do_robo_com_e_sem_entrega():
    """A nota do histórico conta histórias diferentes: com entrega registrada pela SPX
    (e o pacote sem chegar assim mesmo) e sem entrega registrada — "sem entrega
    registrada" e não "em trânsito", que o status pode ser extravio/falha na coleta."""
    assert svc._reversa_legivel({"reverse_logistics_status": "LOGISTICS_NOT_STARTED"}) == "não iniciado"
    assert svc._reversa_legivel({"reverse_logistics_status": "LOGISTICS_DELIVERY_DONE"}) == "entregue"
    det = {"reverse_logistics_status": "LOGISTICS_LOST"}
    nota = svc._nota_robo_nao_recebido(det, "2609X", None)
    assert nota.startswith("Pacote da devolução 2609X: sem entrega registrada pela SPX (extraviado).")
    assert "em trânsito" not in nota and "Assistente do Vendedor" in nota
    assert "prazo da Shopee" not in nota  # sem return_seller_due_date, sem prazo na nota
    entregue_em = datetime(2026, 9, 16, 14, 11, tzinfo=UTC)  # 11:11 BRT
    nota2 = svc._nota_robo_nao_recebido({**det, "return_seller_due_date": 1789700000}, "2609X", entregue_em)
    assert nota2.startswith(
        "Pacote da devolução 2609X: SPX deu como entregue em 16/09/2026 11:11, mas o pacote não chegou até nós."
    ), nota2
    assert "sem entrega registrada" not in nota2 and "prazo da Shopee: 17/09 23:53" in nota2
    assert "Assistente do Vendedor" in nota2 and "tiro único" in nota2


async def test_cartao_persistido_com_link_apagado_some_e_a_tarefa_do_robo_sai_sem_foto(client, make_user, auth_as, db, inline, monkeypatch):
    """Cartão de rodada anterior e a operadora apagou o Link envio ao trocar o motivo:
    o cartão órfão some (apagado duas vezes na mesma sessão — `disparar` e
    `_cartao_nao_recebido_shopee` — sem erro) e a tarefa do robô sai sem foto."""
    fake = _FakeShopee(status="")
    r, _user = await _lancar(
        client, db, make_user, auth_as, monkeypatch, fake, numero="296018", sn="2609ATVNR018",
        motivo="Golpe", link_envio="https://mega.nz/file/video-296018#K3yDoVideoNaMega0123456789abcdefghij",
    )
    assert r.json()["chamado_ml_erro"] == "shopee_aguardando_pacote", r.json()
    assert [a.filename for a in (await db.execute(select(DevolucaoAnexo))).scalars().all()] == [cartao.CARTAO_VIDEO_NOME]
    fake.status = "ACCEPTED"
    p = await client.patch(f"/api/devolutions/{r.json()['id']}", json={"motivo_devolucao": "Não recebido", "link_envio": ""})
    assert p.status_code == 200 and p.json()["chamado_ml_status"] == "pendente", p.text
    assert p.json()["chamado_ml_erro"] is None, p.json()
    ch = await _chamado_de(db, "296018")
    ab = await _abertura(db, ch.id)
    assert ch.canal == "robo" and ab.texto.startswith(ENTREGUE_TXT)
    assert fake.disputes == [] and fake.converted == []
    assert (await db.execute(select(DevolucaoAnexo))).scalars().all() == []
    assert (await db.execute(select(ChamadoAnexo).where(ChamadoAnexo.chamado_id == ch.id))).scalars().all() == []
    await _sem_cartao(db, ch.id)


# ---- Buracos apontados na auditoria de 22/09: os caminhos que NÃO passam pelo
# primeiro disparo da linha (réplica manual, reabertura de uma abertura `falhou`) e
# os estados do return que antes ficavam presos em `shopee_aguardando_pacote`.


async def test_replica_manual_no_chamado_do_robo_nao_abre_disputa(client, make_user, auth_as, db, inline, monkeypatch):
    """A operadora responde pela aba num chamado "Não recebido" que já está com o
    robô: a réplica entra na fila do robô e o `dispute` não é chamado."""
    fake = _FakeShopee()
    r, _user = await _lancar(client, db, make_user, auth_as, monkeypatch, fake, numero="296020", sn="2609ATVNR020")
    assert r.json()["chamado_ml_status"] == "pendente" and r.json()["chamado_ml_erro"] is None, r.json()
    ch = await _chamado_de(db, "296020")
    assert ch.canal == "robo"
    rep = await client.post(f"/api/chamados/{ch.id}/mensagens",
                            data={"texto": "Cliente mandou o print do rastreio."})
    assert rep.status_code == 201, rep.text
    assert rep.json()["status"] == "pendente" and rep.json()["erro"] is None, rep.json()
    assert fake.disputes == []
    ch = await _chamado_de(db, "296020")
    ab = await _abertura(db, ch.id)
    assert ch.canal == "robo" and ab.canal == "robo" and ab.status == "pendente"


async def test_replica_manual_com_abertura_pendente_em_api_devolve_pro_robo(client, make_user, auth_as, db, inline, monkeypatch):
    """Abertura ainda pendente no canal `api` (a Shopee não tinha o return da primeira
    vez): a réplica manual reabre o disparo — e o disparo devolve pro robô em vez de
    queimar a disputa com o texto do operador (era por aqui que ela escapava)."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    fake = _FakeShopee()

    async def _sem_return(*a, **kw):
        return []

    fake.get_return_list = _sem_return

    async def _c(session, *a):
        return fake

    monkeypatch.setattr(svc, "_shopee_client_para", _c)
    await _seed_pedido(db, user, numero="296021", numeroloja="2609ATVNR021", platform="shopee", conta="atv", loja="88")
    await db.commit()
    r = await client.post(
        "/api/devolutions",
        json={"conta": "atv", "pedido_bling": "296021", "pedido_marketplace": "2609ATVNR021",
              "sku": "b001.26", "produtos": "Mala Listrada tamanho 26",
              "condicao_produto": "Não devolvido", "motivo_devolucao": "Não recebido"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["chamado_ml_status"] == "pendente" and r.json()["chamado_ml_erro"] == "devolucao_sem_return", r.json()
    ch = await _chamado_de(db, "296021")
    assert ch.canal == "api"
    # o sync acha a devolução e a operadora responde à mão no mesmo minuto
    db.add(DevolucaoRastreio(pedido_bling="296021", devolucao_id_auto="2609296021RSN", fonte_auto="shopee"))
    await db.commit()
    rep = await client.post(f"/api/chamados/{ch.id}/mensagens",
                            data={"texto": "Pacote não chegou até hoje, segue rastreio."})
    assert rep.status_code == 201, rep.text
    assert rep.json()["status"] == "pendente" and rep.json()["erro"] is None, rep.json()
    assert fake.disputes == []
    ch = await _chamado_de(db, "296021")
    ab = await _abertura(db, ch.id)
    assert ch.canal == "robo" and ab.canal == "robo" and ab.status == "pendente"
    # o que ela digitou vira observação do texto factual, não o texto da contestação
    assert ab.texto.startswith(ENTREGUE_TXT), ab.texto
    assert "Observação: Pacote não chegou até hoje, segue rastreio." in ab.texto


async def test_abertura_falhou_reaberta_nao_cai_na_disputa(client, make_user, auth_as, db, inline, monkeypatch):
    """`garantir_chamado` devolve uma abertura `falhou` pra `pendente` com
    `canal="api"`. O disparo seguinte não pode ler isso como "pode disputar": o
    "Não recebido" volta pro robô."""
    fake = _FakeShopee(status="CLOSED")
    r, _user = await _lancar(client, db, make_user, auth_as, monkeypatch, fake, numero="296022", sn="2609ATVNR022")
    assert r.json()["chamado_ml_status"] == "falhou", r.json()
    assert r.json()["chamado_ml_erro"] == "shopee_devolucao_encerrada", r.json()
    ch = await _chamado_de(db, "296022")
    ab = await _abertura(db, ch.id)
    assert ch.canal == "api" and ab.canal == "api" and ab.status == "falhou"
    # a Shopee reabriu o caso e a operadora mexe na linha → a abertura volta pra fila
    fake.status = "ACCEPTED"
    p = await client.patch(f"/api/devolutions/{r.json()['id']}",
                           json={"motivo_devolucao": "Não recebido", "observacao": "Shopee reabriu o caso."})
    assert p.status_code == 200, p.text
    assert p.json()["chamado_ml_status"] == "pendente" and p.json()["chamado_ml_erro"] is None, p.json()
    assert fake.disputes == []
    ch = await _chamado_de(db, "296022")
    ab = await _abertura(db, ch.id)
    await db.refresh(ab)
    assert ch.canal == "robo" and ab.canal == "robo" and ab.status == "pendente"
    assert ab.texto.startswith(ENTREGUE_TXT), ab.texto


async def test_nao_recebido_em_estado_que_esperava_pacote_vai_pro_robo(client, make_user, auth_as, db, inline, monkeypatch):
    """REFUND_PAID com `needs_logistics` caía no `shopee_aguardando_pacote` e ficava
    pendente de hora em hora — mas a janela de contestação some em horas (293460:
    6h28). Agora o caso sai na hora pro robô; nos outros motivos a guarda fica."""
    fake = _FakeShopee(status="REFUND_PAID", entregue=False)

    async def _det(return_sn):
        det = await _FakeShopee.get_return_detail(fake, return_sn)
        det.update({"return_solution": 0, "needs_logistics": True})
        return det

    fake.get_return_detail = _det
    r, _user = await _lancar(client, db, make_user, auth_as, monkeypatch, fake, numero="296023", sn="2609ATVNR023")
    assert r.json()["chamado_ml_status"] == "pendente" and r.json()["chamado_ml_erro"] is None, r.json()
    assert fake.disputes == []
    ch = await _chamado_de(db, "296023")
    ab = await _abertura(db, ch.id)
    assert ch.canal == "robo" and ab.canal == "robo" and ab.texto.split("\n")[0] == TRANSITO_TXT
    assert any("sem entrega registrada pela SPX (coletado)" in t for t in await _sistema_txts(db, ch.id))
    # Golpe no mesmo estado continua esperando o pacote (a disputa desse motivo vale)
    fake2 = _FakeShopee(status="REFUND_PAID", entregue=False)

    async def _det2(return_sn):
        det = await _FakeShopee.get_return_detail(fake2, return_sn)
        det.update({"return_solution": 0, "needs_logistics": True})
        return det

    fake2.get_return_detail = _det2
    r2, _u = await _lancar(client, db, make_user, auth_as, monkeypatch, fake2, numero="296024", sn="2609ATVNR024",
                           motivo="Golpe", link_envio="https://mega.nz/file/video-296024#K3yDoVideoNaMega0123456789abcdefghij")
    assert r2.json()["chamado_ml_status"] == "pendente", r2.json()
    assert r2.json()["chamado_ml_erro"] == "shopee_aguardando_pacote", r2.json()
    assert fake2.disputes == []


# ---- 24/09 (Vinicius, 296012): só reembolso sem prova + pedido com 2 produtos

async def test_so_reembolso_sem_foto_nem_video_vai_pro_robo_com_os_produtos_com_problema(client, make_user, auth_as, db, inline, monkeypatch):
    """Só reembolso ("o cliente foi reembolsado e não devolveu o produto") lançado como
    "Não recebido", sem foto e sem vídeo: ficava `pendente` "aguardando foto" de um
    produto que nunca voltou, com o texto "O pacote da devolução ainda não chegou". Agora
    vai pro robô na hora, com o texto do só reembolso. E o pedido tinha 2 produtos com
    problema e o texto citava 1: cita os 2 — e só eles (a linha de Item Incorreto fica
    de fora: "se apenas 1 produto tá com problema coloca apenas 1")."""
    fake = _FakeShopee(entregue=False)
    chamadas = {"reasons": 0}

    async def _det(return_sn):
        return {"return_sn": return_sn, "status": "ACCEPTED", "return_solution": 1, "needs_logistics": False,
                "reason": "NOT_RECEIPT", "refund_amount": 189.9,
                "seller_compensation": {"seller_compensation_status": "PENDING_REQUEST"}}

    async def _reasons(return_sn):
        chamadas["reasons"] += 1
        return [{"dispute_reason": 1, "dispute_requirement": "Prova de envio",
                 "evidence_module_list": [{"module_index": 1, "requirement": "Comprovante", "is_required": True}]}]

    fake.get_return_detail = _det
    fake.get_return_dispute_reason = _reasons
    # as outras linhas do pedido já estão na tela (a tela grava uma linha por produto)
    db.add(Devolution(conta="atv", pedido_bling="296012", pedido_marketplace="260910MATESNVN",
                      sku="a001.pi", produtos="Fone com fio Uranyx UFF001", condicao_produto="Não devolvido",
                      motivo_devolucao="Não recebido"))
    db.add(Devolution(conta="atv", pedido_bling="296012", pedido_marketplace="260910MATESNVN",
                      sku="c777.az", produtos="Capa Azul", condicao_produto="Novo",
                      motivo_devolucao="Item Incorreto"))
    await db.commit()
    r, _user = await _lancar(
        client, db, make_user, auth_as, monkeypatch, fake, numero="296012", sn="260910MATESNVN",
        observacao="o cliente foi reembolsado, e nao devolveu o produto",
    )
    assert r.json()["chamado_ml_status"] == "pendente" and r.json()["chamado_ml_erro"] is None, r.json()
    assert fake.disputes == [] and chamadas["reasons"] == 0
    ch = await _chamado_de(db, "296012")
    ab = await _abertura(db, ch.id)
    assert ch.canal == "robo" and ab.canal == "robo" and ab.status == "pendente", (ch.canal, ab.canal, ab.status)
    txt = ab.texto
    assert txt.startswith(
        "O comprador pediu reembolso SEM devolução do produto alegando que não recebeu o pedido. "
        "A Shopee aprovou o reembolso de R$ 189.9"
    ), txt
    assert ("Pedido 260910MATESNVN · Produtos: SKU b001.26 (Mala Listrada tamanho 26); "
            "SKU a001.pi (Fone com fio Uranyx UFF001).") in txt, txt
    assert "c777.az" not in txt and "Capa Azul" not in txt
    assert "Observação: o cliente foi reembolsado, e nao devolveu o produto" in txt
    assert "O pacote da devolução" not in txt and "QR code" not in txt
    hist = await _sistema_txts(db, ch.id)
    assert any("não tem foto nem vídeo da expedição" in t and "Assistente do Vendedor" in t for t in hist), hist
    await _sem_cartao(db, ch.id)


async def test_texto_so_reembolso_ainda_nao_aprovado_nao_diz_que_a_shopee_aprovou(db):
    dev = Devolution(pedido_marketplace="2609X", sku="b001.26", produtos="Mala", motivo_devolucao="Não recebido")
    det = {"status": "REQUESTED", "reason": "NOT_RECEIPT", "refund_amount": 89.9}
    base = svc.texto_padrao(dev, svc.reason_para(dev))
    txt = await svc._texto_robo_shopee_reembolso(db, dev, det, [], base)
    assert txt.split("\n")[0] == (
        "O comprador pediu reembolso SEM devolução do produto alegando que não recebeu o pedido (R$ 89.9)."
    ), txt
    assert "aprovou" not in txt and "Pedido 2609X · SKU b001.26 · Mala." in txt


def test_texto_padrao_cita_so_os_produtos_com_problema_e_soma_unidades():
    dev = Devolution(pedido_marketplace="2609X", sku="b001.26", produtos="Mala", motivo_devolucao="Golpe",
                     observacao="caixa aberta")
    outra_unidade = Devolution(pedido_marketplace="2609X", sku="b001.26", produtos="Mala", motivo_devolucao="Golpe",
                               observacao="caixa aberta")
    fone = Devolution(pedido_marketplace="2609X", sku="a001.pi", produtos="Fone", motivo_devolucao="Item faltando",
                      observacao="sem o cabo")
    sem_problema = Devolution(pedido_marketplace="2609X", sku="c777.az", produtos="Capa", motivo_devolucao=None)
    # uma linha só: igual a antes
    assert svc.texto_padrao(dev, "SRF5").split("\n")[1] == "Pedido 2609X · SKU b001.26 · Mala."
    txt = svc.texto_padrao(dev, "SRF5", linhas=[dev, sem_problema, fone, outra_unidade])
    linhas = txt.split("\n")
    assert linhas[1] == "Pedido 2609X · Produtos: 2x SKU b001.26 (Mala); SKU a001.pi (Fone).", txt
    assert linhas[2] == "Observação: caixa aberta sem o cabo", txt
    assert "Capa" not in txt
    # 2 unidades do mesmo produto e nada mais
    assert svc.texto_padrao(dev, "SRF5", linhas=[dev, outra_unidade]).split("\n")[1] == (
        "Pedido 2609X · 2x SKU b001.26 · Mala."
    )
