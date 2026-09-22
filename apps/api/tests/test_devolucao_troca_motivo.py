# ruff: noqa: E501
"""Troca de motivo com chamado aberto — Vinicius 21/09 (caso 260901SXJCU6EG).

A operadora lançou "Não recebido", o chamado abriu; dias depois o pacote chegou
danificado e ela troca pra "Danificado (Outros)". Até aqui NADA acontecia no
chamado (o dedupe achava o antigo e a abertura enviada fazia `garantir_chamado`
sair calado) e ele excluía o lançamento e refazia — que também não abria outro.
Agora `trocar_motivo_chamado` decide pelo estado da contestação antiga:
  - ainda não saiu (pendente) → mesmo chamado, evento no histórico, o disparo
    seguinte já vai com o motivo novo;
  - com o robô (Shopee em trânsito → Seller Center) → chamado antigo Encerrado
    com o evento "usuário trocou o motivo…" e OUTRO aberto, que sai pela API;
  - já enviada pela API → mesmo chamado; o relato novo fica no histórico e a
    pessoa responde no painel (nenhuma plataforma aceita segunda contestação
    nem tem "cancelar"; o robô não fala em canal api).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import Chamado, ChamadoMensagem, DevolucaoRastreio
from app.services import chamados_devolucao as svc
from tests.test_chamados_devolucao import (
    PNG_1PX,
    _abertura,
    _FakeML,
    _FakeShopee,
    _perms,
    _seed_pedido,
    _sistema_txts,
    _status_aba,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def ml(monkeypatch):
    fake = _FakeML()

    async def _client(session, conta):
        return fake

    monkeypatch.setattr(svc.chamados_svc, "_ml_client_para", _client)
    monkeypatch.setattr(svc, "ENFILEIRAR", False)  # dispara inline (sem Redis)
    return fake


async def _chamados_de(db, pedido: str) -> list[Chamado]:
    rows = (
        await db.execute(
            select(Chamado)
            .where(Chamado.origem == "devolucao", Chamado.pedido_bling == pedido)
            .order_by(Chamado.created_at)
            .execution_options(populate_existing=True)
        )
    ).scalars().all()
    return list(rows)


async def _instrucoes(db, chamado_id) -> list[str]:
    rows = (
        await db.execute(
            select(ChamadoMensagem)
            .where(ChamadoMensagem.chamado_id == chamado_id, ChamadoMensagem.tipo == "instrucao")
            .order_by(ChamadoMensagem.created_at)
        )
    ).scalars().all()
    return [m.texto for m in rows]


async def _lancar_ml(client, db, make_user, auth_as, *, numero: str, sn: str, motivo="Não recebido", **extra):
    user = await make_user(email="thays@davinci-test.com", permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero=numero, numeroloja=sn)
    r = await client.post(
        "/api/devolutions",
        json={"conta": "aguiar", "pedido_bling": numero, "pedido_marketplace": sn,
              "sku": "b001.26", "produtos": "Mala Listrada tamanho 26",
              "condicao_produto": "Não devolvido", "motivo_devolucao": motivo, **extra},
    )
    assert r.status_code == 201, r.text
    return r, user


# ------------------------------------------------------------ pendente → mesmo chamado


async def test_abertura_pendente_troca_no_mesmo_chamado_e_dispara_com_o_motivo_novo(client, make_user, auth_as, db, ml):
    ml.acao = False  # ML ainda não liberou a revisão → abertura pendente
    r, _ = await _lancar_ml(client, db, make_user, auth_as, numero="297001", sn="2609020KA97001")
    assert r.json()["chamado_ml_status"] == "pendente" and r.json()["chamado_ml_erro"] == "return_review_indisponivel"
    did = r.json()["id"]
    (ch,) = await _chamados_de(db, "297001")
    assert ch.observacao == "Aberto automaticamente pela devolução — motivo: Não recebido"

    p = await client.patch(f"/api/devolutions/{did}", json={"motivo_devolucao": "Danificado (Outros)", "link_envio": "https://drive.x/v"})
    assert p.status_code == 200, p.text
    assert p.json()["chamado_troca_motivo"] == "atualizado"
    # continua UM chamado, agora pedindo a foto do dano (SRF2)
    (ch,) = await _chamados_de(db, "297001")
    assert ch.status_plataforma is None and ch.resolvido is False
    assert ch.observacao == "Aberto automaticamente pela devolução — motivo: Danificado (Outros)"
    assert p.json()["chamado_ml_status"] == "pendente" and p.json()["chamado_ml_erro"] == "devolucao_sem_foto"
    hist = await _sistema_txts(db, ch.id)
    assert any('Devoluções (thays@davinci-test.com) trocou o motivo da devolução de "Não recebido" para "Danificado (Outros)"' in t and "ainda não tinha saído" in t for t in hist), hist
    assert ml.reviews == []

    # salvar de novo sem mudar o motivo (o front manda o motivo em todo save) não repete
    p2 = await client.patch(f"/api/devolutions/{did}", json={"motivo_devolucao": "danificado (outros)", "observacao": "x"})
    assert p2.status_code == 200 and p2.json()["chamado_troca_motivo"] is None
    assert len(await _sistema_txts(db, ch.id)) == len(hist)

    # ML libera a revisão + foto do dano → sai SRF2 (danificado), não SRF7
    ml.acao = True
    up = await client.post(f"/api/devolutions/{did}/anexos", files={"file": ("dano.png", PNG_1PX, "image/png")})
    assert up.status_code == 201, up.text
    assert up.json()["chamado_ml_status"] == "enviada", up.json()
    assert len(ml.reviews) == 1 and ml.reviews[0][1] == "SRF2"
    assert "chegou danificado" in ml.reviews[0][2]


# ------------------------------------------------------------ robô → encerra + novo


async def _lancar_shopee(client, db, make_user, auth_as, monkeypatch, fake, *, numero, sn, **extra):
    user = await make_user(email="thays@davinci-test.com", permissions=_perms())
    auth_as(user)

    async def _c(session, *a):
        return fake

    monkeypatch.setattr(svc, "_shopee_client_para", _c)
    monkeypatch.setattr(svc, "ENFILEIRAR", False)
    await _seed_pedido(db, user, numero=numero, numeroloja=sn, platform="shopee", conta="atv", loja="88")
    db.add(DevolucaoRastreio(pedido_bling=numero, devolucao_id_auto=f"2609{numero}RSN", fonte_auto="shopee"))
    await db.commit()
    r = await client.post(
        "/api/devolutions",
        json={"conta": "atv", "pedido_bling": numero, "pedido_marketplace": sn,
              "sku": "b001.26", "produtos": "Mala Listrada tamanho 26",
              "condicao_produto": "Não devolvido", "motivo_devolucao": "Não recebido", **extra},
    )
    assert r.status_code == 201, r.text
    return r, user


async def test_robo_shopee_pendente_encerra_o_antigo_e_abre_outro_pela_api(client, make_user, auth_as, db, monkeypatch):
    fake = _FakeShopee(entregue=False)  # pacote de volta em trânsito → tarefa do robô
    r, _ = await _lancar_shopee(client, db, make_user, auth_as, monkeypatch, fake, numero="297002", sn="2609ATVTM002")
    did = r.json()["id"]
    (antigo,) = await _chamados_de(db, "297002")
    ab = await _abertura(db, antigo.id)
    assert antigo.canal == "robo" and ab.canal == "robo" and ab.status == "pendente"

    # mala + Danificado exige o link de envio
    p = await client.patch(f"/api/devolutions/{did}", json={"motivo_devolucao": "Danificado (Outros)"})
    assert p.status_code == 422 and p.json()["detail"]["code"] == "link_envio_obrigatorio"
    assert len(await _chamados_de(db, "297002")) == 1

    p = await client.patch(f"/api/devolutions/{did}", json={"motivo_devolucao": "Danificado (Outros)", "link_envio": "https://drive.x/v297002"})
    assert p.status_code == 200, p.text
    assert p.json()["chamado_troca_motivo"] == "substituido"
    antigo, novo = await _chamados_de(db, "297002")
    # antigo: Encerrado (a pessoa conclui), tarefa do robô cancelada, evento do Vinicius
    assert antigo.status_plataforma == "encerrado" and antigo.resolvido is False and antigo.auto_ligada is False
    ab = await _abertura(db, antigo.id)
    await db.refresh(ab)
    assert ab.status == "registrada" and ab.erro == "contestacao_cancelada"
    hist = await _sistema_txts(db, antigo.id)
    assert any('Devoluções (thays@davinci-test.com) trocou o motivo da devolução de "Não recebido" para "Danificado (Outros)" — chamado encerrado; outro chamado foi aberto com o motivo novo' in t for t in hist), hist
    assert any("pendente na fila do robô" in t for t in hist), hist
    assert (await _status_aba(db, antigo))[0] == "encerrado"
    # novo: canal api, esperando a foto do dano; observação diz de onde veio
    assert novo.canal == "api" and novo.status_plataforma is None
    assert novo.observacao == 'Aberto automaticamente pela devolução — motivo: Danificado (Outros) (substitui o chamado anterior, motivo "Não recebido")'
    assert any('substitui o chamado anterior (motivo "Não recebido")' in t for t in await _sistema_txts(db, novo.id))
    ab2 = await _abertura(db, novo.id)
    assert ab2.canal == "api" and ab2.status == "pendente" and ab2.erro == "devolucao_sem_foto"
    # a coluna Chamado da tela mostra o novo
    assert p.json()["chamado_ml_status"] == "pendente" and p.json()["chamado_ml_erro"] == "devolucao_sem_foto"
    assert fake.disputes == []

    # o robô da Shopee não recebe mais a tarefa antiga
    token = "tok-shopee-2109b"  # noqa: S105
    monkeypatch.setattr(get_settings(), "nf_agent_token", token)
    lease = await client.post("/api/chamados/agent/lease", headers={"X-Agent-Token": token}, json={"limite": 10, "plataforma": "shopee"})
    assert lease.status_code == 200 and [t for t in lease.json()["tarefas"] if t["mensagem_id"] == str(ab.id)] == []

    # a lixeira do chamado antigo NÃO pré-marca a linha (ela alimenta o chamado novo)
    prev = await client.get(f"/api/chamados/{antigo.id}/exclusao")
    assert prev.status_code == 200, prev.text
    assert [x["marcado_padrao"] for x in prev.json()["lancamentos"]] == [False]
    prev_novo = await client.get(f"/api/chamados/{novo.id}/exclusao")
    assert [x["marcado_padrao"] for x in prev_novo.json()["lancamentos"]] == [True]

    # foto do dano → disputa pela API com "physical damage" (82) e a foto
    up = await client.post(f"/api/devolutions/{did}/anexos", files={"file": ("dano.png", PNG_1PX, "image/png")})
    assert up.status_code == 201, up.text
    assert up.json()["chamado_ml_status"] == "enviada", up.json()
    assert len(fake.disputes) == 1 and fake.disputes[0]["reason"] == 82
    assert fake.disputes[0]["image_list"] and "chegou danificado" in fake.disputes[0]["text"]
    antigo, novo = await _chamados_de(db, "297002")
    assert novo.chamado == "2609297002RSN" and antigo.status_plataforma == "encerrado"


async def test_robo_ja_abriu_no_seller_center_avisa_pra_desistir(client, make_user, auth_as, db, monkeypatch):
    fake = _FakeShopee(entregue=False)
    r, _ = await _lancar_shopee(client, db, make_user, auth_as, monkeypatch, fake, numero="297003", sn="2609ATVTM003")
    did = r.json()["id"]
    (antigo,) = await _chamados_de(db, "297003")
    # simula o robô: abriu a solicitação e devolveu o protocolo
    ab = await _abertura(db, antigo.id)
    ab.status = "enviada"
    ab.enviada_at = datetime(2026, 9, 20, 15, 0, tzinfo=UTC)
    antigo.chamado = "REQ-88123"
    await db.commit()

    p = await client.patch(f"/api/devolutions/{did}", json={"motivo_devolucao": "Danificado (Outros)", "link_envio": "https://drive.x/v297003"})
    assert p.status_code == 200 and p.json()["chamado_troca_motivo"] == "substituido", p.text
    antigo, novo = await _chamados_de(db, "297003")
    ab = await _abertura(db, antigo.id)
    await db.refresh(ab)
    assert ab.status == "enviada" and ab.erro == "retirar_contestacao"
    hist = await _sistema_txts(db, antigo.id)
    assert any("aberta pelo robô na tela da plataforma (Shopee, 20/09 12:00, protocolo REQ-88123)" in t and "desista dela" in t for t in hist), hist
    assert novo.canal == "api" and novo.chamado == "2609297003RSN"  # return_sn da disputa nova, não o protocolo do robô


async def test_robo_com_a_tarefa_em_maos_nao_recebe_a_abertura_antiga_de_volta(client, make_user, auth_as, db, monkeypatch):
    """Revisão 21/09: a abertura `enviando` de um chamado Encerrado voltava pra fila do
    robô (falha transitória ou lease vencido) e ele abria a solicitação antiga."""
    from datetime import timedelta

    fake = _FakeShopee(entregue=False)
    r, _ = await _lancar_shopee(client, db, make_user, auth_as, monkeypatch, fake, numero="297008", sn="2609ATVTM008")
    did = r.json()["id"]
    (antigo,) = await _chamados_de(db, "297008")
    ab = await _abertura(db, antigo.id)
    token = "tok-shopee-2109c"  # noqa: S105
    monkeypatch.setattr(get_settings(), "nf_agent_token", token)
    hdr = {"X-Agent-Token": token}
    lease = await client.post("/api/chamados/agent/lease", headers=hdr, json={"limite": 10, "plataforma": "shopee"})
    assert [t["mensagem_id"] for t in lease.json()["tarefas"]] == [str(ab.id)]
    await db.refresh(ab)
    assert ab.status == "enviando"

    p = await client.patch(f"/api/devolutions/{did}", json={"motivo_devolucao": "Danificado (Outros)", "link_envio": "https://drive.x/v297008"})
    assert p.status_code == 200 and p.json()["chamado_troca_motivo"] == "substituido", p.text
    antigo, novo = await _chamados_de(db, "297008")
    await db.refresh(ab)
    assert ab.status == "enviando" and ab.erro == "contestacao_cancelada"  # o resultado do robô ainda cai aqui
    assert any("tarefa de abertura em mãos" in t and "não volta pra fila" in t for t in await _sistema_txts(db, antigo.id))

    # lease vencido (>30 min em `enviando`): a abertura do Encerrado NÃO é reentregue
    ab.updated_at = datetime.now(UTC) - timedelta(minutes=31)
    await db.commit()
    lease = await client.post("/api/chamados/agent/lease", headers=hdr, json={"limite": 10, "plataforma": "shopee"})
    assert [t for t in lease.json()["tarefas"] if t["mensagem_id"] == str(ab.id)] == []

    # falha transitória do robô: não volta pra `pendente`, fica cancelada
    ab.updated_at = datetime.now(UTC)
    await db.commit()
    res = await client.post("/api/chamados/agent/resultado", headers=hdr, json={"mensagem_id": str(ab.id), "ok": False, "erro": "timeout no Seller Center"})
    assert res.status_code == 200, res.text
    m = (await db.execute(select(ChamadoMensagem).where(ChamadoMensagem.id == ab.id).execution_options(populate_existing=True))).scalar_one()
    assert m.status == "registrada" and m.erro == "contestacao_cancelada"
    assert any("Tarefa do robô falhou (timeout no Seller Center) e o chamado já está Encerrado — cancelada." in t for t in await _sistema_txts(db, antigo.id))
    lease = await client.post("/api/chamados/agent/lease", headers=hdr, json={"limite": 10, "plataforma": "shopee"})
    assert [t for t in lease.json()["tarefas"] if t["mensagem_id"] == str(ab.id)] == []
    # o chamado novo segue vivo, esperando a foto
    assert novo.status_plataforma is None and (await _abertura(db, novo.id)).erro == "devolucao_sem_foto"


async def test_robo_que_abre_depois_do_encerramento_deixa_a_marca_de_retirar(client, make_user, auth_as, db, monkeypatch):
    fake = _FakeShopee(entregue=False)
    r, _ = await _lancar_shopee(client, db, make_user, auth_as, monkeypatch, fake, numero="297009", sn="2609ATVTM009")
    did = r.json()["id"]
    (antigo,) = await _chamados_de(db, "297009")
    ab = await _abertura(db, antigo.id)
    token = "tok-shopee-2109d"  # noqa: S105
    monkeypatch.setattr(get_settings(), "nf_agent_token", token)
    hdr = {"X-Agent-Token": token}
    await client.post("/api/chamados/agent/lease", headers=hdr, json={"limite": 10, "plataforma": "shopee"})
    # motivo retirado (caminho de 15/09) com o robô ainda com a tarefa
    p = await client.patch(f"/api/devolutions/{did}", json={"motivo_devolucao": "Item Incorreto"})
    assert p.status_code == 200 and p.json()["chamado_troca_motivo"] == "encerrado", p.text
    res = await client.post("/api/chamados/agent/resultado", headers=hdr, json={"mensagem_id": str(ab.id), "ok": True, "chamado": "REQ-777"})
    assert res.status_code == 200, res.text
    m = (await db.execute(select(ChamadoMensagem).where(ChamadoMensagem.id == ab.id).execution_options(populate_existing=True))).scalar_one()
    assert m.status == "enviada" and m.erro == "retirar_contestacao"
    (antigo,) = await _chamados_de(db, "297009")
    assert antigo.chamado == "REQ-777" and antigo.status_plataforma == "encerrado"
    assert any("abriu na tela da plataforma (protocolo REQ-777) depois de o chamado ficar Encerrado" in t for t in await _sistema_txts(db, antigo.id))
    # a tela Devoluções mostra o aviso âmbar de retirar no painel
    lista = await client.get("/api/devolutions", params={"search": "297009"})
    linha = next(x for x in lista.json()["items"] if x["id"] == did)
    assert linha["chamado_ml_status"] == "enviada" and linha["chamado_ml_erro"] == "retirar_contestacao"


async def test_lixeira_do_substituido_sem_pedido_bling_nao_premarca_a_linha(client, make_user, auth_as, db, ml):
    """Revisão 21/09: sem pedido Bling o par de chamados se liga pelo origem_ref."""
    from datetime import timedelta

    user = await make_user(email="thays@davinci-test.com", permissions=_perms())
    auth_as(user)
    r = await client.post(
        "/api/devolutions",
        json={"conta": "aguiar", "pedido_bling": None, "pedido_marketplace": "SEM-BLING-1",
              "sku": "b001.26", "produtos": "Mala Listrada tamanho 26",
              "condicao_produto": "Não devolvido", "motivo_devolucao": None},
    )
    assert r.status_code == 201, r.text
    did = r.json()["id"]
    agora = datetime.now(UTC)
    antigo = Chamado(origem="devolucao", origem_ref=did, canal="robo", conta="aguiar", sku="b001.26",
                     status_plataforma="encerrado", observacao="antigo", created_at=agora - timedelta(hours=1))
    novo = Chamado(origem="devolucao", origem_ref=did, canal="api", conta="aguiar", sku="b001.26",
                   observacao="novo", created_at=agora)
    db.add_all([antigo, novo])
    await db.commit()
    # a linha precisa de motivo que abre chamado pra ser pré-marcada por padrão
    p = await client.patch(f"/api/devolutions/{did}", json={"motivo_devolucao": "Golpe", "link_envio": "https://drive.x/v"})
    assert p.status_code == 200, p.text
    prev = await client.get(f"/api/chamados/{antigo.id}/exclusao")
    assert prev.status_code == 200, prev.text
    assert [x["marcado_padrao"] for x in prev.json()["lancamentos"]] == [False]
    prev_novo = await client.get(f"/api/chamados/{novo.id}/exclusao")
    assert [x["marcado_padrao"] for x in prev_novo.json()["lancamentos"]] == [True]


# ------------------------------------------------------------ enviada pela API → mesmo chamado


async def test_contestacao_ja_enviada_fica_no_mesmo_chamado_com_relato_no_historico(client, make_user, auth_as, db, ml):
    r, _ = await _lancar_ml(client, db, make_user, auth_as, numero="297004", sn="2609020KA97004")
    assert r.json()["chamado_ml_status"] == "enviada" and len(ml.reviews) == 1 and ml.reviews[0][1] == "SRF7"
    did = r.json()["id"]

    p = await client.patch(f"/api/devolutions/{did}", json={"motivo_devolucao": "Danificado (Outros)", "link_envio": "https://drive.x/v297004"})
    assert p.status_code == 200, p.text
    assert p.json()["chamado_troca_motivo"] == "ja_enviada"
    (ch,) = await _chamados_de(db, "297004")
    assert ch.status_plataforma is None and ch.resolvido is False
    assert ch.observacao == "Aberto automaticamente pela devolução — motivo: Danificado (Outros)"
    ab = await _abertura(db, ch.id)
    assert ab.status == "enviada" and ab.erro is None  # nada de "retirar": o chamado segue vivo
    assert p.json()["chamado_ml_status"] == "enviada" and p.json()["chamado_ml_erro"] is None
    hist = await _sistema_txts(db, ch.id)
    evento = next((t for t in hist if 'trocou o motivo da devolução de "Não recebido" para "Danificado (Outros)"' in t), None)
    assert evento and "não aceita uma segunda pela API" in evento and "mediação do Mercado Livre" in evento, hist
    assert "Relato: Recebemos a devolução e o produto chegou danificado." in evento, evento
    # o robô não responde em canal api: nada de instrução (viraria 422 canal_sem_robo no cérebro)
    assert await _instrucoes(db, ch.id) == []
    assert len(ml.reviews) == 1  # nenhuma segunda revisão foi tentada

    # foto anexada depois não reabre nem reenvia (a abertura já está enviada)
    up = await client.post(f"/api/devolutions/{did}/anexos", files={"file": ("dano.png", PNG_1PX, "image/png")})
    assert up.status_code == 201 and len(ml.reviews) == 1
    assert len(await _chamados_de(db, "297004")) == 1


async def test_troca_a_partir_de_danificado_preserva_o_resto_da_observacao(client, make_user, auth_as, db, ml):
    """Revisão 21/09: o helper da observação cortava no " (" do próprio motivo
    ("Danificado (Outros)" → "Golpe (Outros)") e apagava linha anexada por pessoa."""
    ml.acao = False
    r, _ = await _lancar_ml(client, db, make_user, auth_as, numero="297006", sn="2609020KA97006", motivo="Danificado (Outros)", link_envio="https://drive.x/v297006")
    did = r.json()["id"]
    (ch,) = await _chamados_de(db, "297006")
    ch.observacao = ch.observacao + "\ncliente ligou 2x"
    await db.commit()
    p = await client.patch(f"/api/devolutions/{did}", json={"motivo_devolucao": "Golpe", "link_envio": "https://drive.x/v297006"})
    assert p.status_code == 200 and p.json()["chamado_troca_motivo"] == "atualizado", p.text
    (ch,) = await _chamados_de(db, "297006")
    assert ch.observacao == "Aberto automaticamente pela devolução — motivo: Golpe\ncliente ligou 2x"
    # observação escrita à mão (não começa pelo texto automático + motivo anterior) fica como está
    ch.observacao = "texto meu"
    await db.commit()
    p = await client.patch(f"/api/devolutions/{did}", json={"motivo_devolucao": "Item faltando", "link_envio": "https://drive.x/v297006"})
    assert p.status_code == 200 and p.json()["chamado_troca_motivo"] == "atualizado", p.text
    (ch,) = await _chamados_de(db, "297006")
    assert ch.observacao == "texto meu"


async def test_plataforma_sem_api_devolve_atualizado_sem_api(client, make_user, auth_as, db, ml):
    user = await make_user(email="thays@davinci-test.com", permissions=_perms())
    auth_as(user)
    await _seed_pedido(db, user, numero="297007", numeroloja="AMZ-297007", platform="amazon", conta="poofy", loja="99")
    r = await client.post(
        "/api/devolutions",
        json={"conta": "poofy", "pedido_bling": "297007", "pedido_marketplace": "AMZ-297007",
              "sku": "b001.26", "produtos": "Mala Listrada tamanho 26",
              "condicao_produto": "Não devolvido", "motivo_devolucao": "Não recebido"},
    )
    assert r.status_code == 201 and r.json()["chamado_ml_status"] == "registrada", r.text
    did = r.json()["id"]
    p = await client.patch(f"/api/devolutions/{did}", json={"motivo_devolucao": "Danificado (Outros)", "link_envio": "https://drive.x/v297007"})
    assert p.status_code == 200 and p.json()["chamado_troca_motivo"] == "atualizado_sem_api", p.text
    (ch,) = await _chamados_de(db, "297007")
    hist = await _sistema_txts(db, ch.id)
    assert any("Amazon não tem API — abrir na mão já com o motivo novo" in t for t in hist), hist


# ------------------------------------------------------------ o caminho de 15/09 continua


async def test_motivo_que_deixa_de_pedir_chamado_devolve_encerrado_na_resposta(client, make_user, auth_as, db, ml):
    r, _ = await _lancar_ml(client, db, make_user, auth_as, numero="297005", sn="2609020KA97005")
    did = r.json()["id"]
    p = await client.patch(f"/api/devolutions/{did}", json={"motivo_devolucao": "Item Incorreto"})
    assert p.status_code == 200 and p.json()["chamado_troca_motivo"] == "encerrado", p.text
    (ch,) = await _chamados_de(db, "297005")
    assert ch.status_plataforma == "encerrado"
    # e voltar pra um motivo que pede chamado não mexe no Encerrado (fora do escopo de 21/09)
    p = await client.patch(f"/api/devolutions/{did}", json={"motivo_devolucao": "Golpe", "link_envio": "https://drive.x/v"})
    assert p.status_code == 200 and p.json()["chamado_troca_motivo"] is None
    assert len(await _chamados_de(db, "297005")) == 1
