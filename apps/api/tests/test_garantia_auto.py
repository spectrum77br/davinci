"""Garantia automática (28/10/2026): o robô cria a garantia do pedido Uranyx
pela NF de PRODUTO, liga as conversas do pedido (ponteiro, sem cópia) e o
painel mostra o histórico no estilo do Atendimento."""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import (
    BlingOrder,
    Garantia,
    GarantiaAtendimento,
    GarantiaConversa,
    GarantiaLog,
    GarantiaPendencia,
    Integration,
    IntegrationPlatform,
    Logistica,
    UserRole,
)
from app.models.atendimento import (
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoPedidoComprador,
)
from app.models.nf import NfNota
from app.security.cipher import encrypt_json
from app.services import garantia as svc
from app.services import garantia_auto as auto

pytestmark = pytest.mark.asyncio

HOJE = date(2026, 10, 20)
AGORA = datetime(2026, 10, 20, 15, tzinfo=UTC)
CPF = "52998224725"
CPF_2 = "11144477735"
COMPRA = datetime(2026, 10, 1, 15, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _hoje(monkeypatch):
    monkeypatch.setattr(svc, "hoje", lambda: HOJE)


# ── Fábrica ───────────────────────────────────────────────────────────────────


async def _pedido(
    db,
    numero="310001",
    numeroloja="261001ABCD1234",
    *,
    sku="dg053",
    descricao="Celular Uranyx T8 128GB",
    total=1000,
    situacao="15",
    data=COMPRA,
):
    db.add(
        BlingOrder(
            numero=numero,
            numeroloja=numeroloja,
            loja="L1",
            data=data,
            total=total,
            situacao=situacao,
            item_index=0,
            item_codigo=sku,
            item_descricao=descricao,
            item_quantidade=1,
            documento_destinatario=CPF,
            nome_destinatario="Destinatário da Entrega",
        )
    )
    await db.commit()


async def _nf(db, pedido="310001", numero="2611", *, serie="2", valor=1000, doc=CPF, nome=None):
    db.add(
        NfNota(
            chave=str(uuid.uuid4().int)[:44].ljust(44, "7"),
            pedido_bling=pedido,
            numero=numero,
            serie=serie,
            emitente_cnpj="12345678000199",
            destinatario_doc=doc,
            destinatario_nome=nome or "MARIA DA NOTA",
            valor=Decimal(valor),
            data_emissao=COMPRA + timedelta(days=1),
            situacao="100",
            xml=b"<nfeProc/>",
        )
    )
    await db.commit()


async def _entregue(db, numero="310001", quando=datetime(2026, 10, 5, 18, tzinfo=UTC)):
    db.add(
        Logistica(
            pedido_bling=numero,
            plataforma="Shopee",
            meli_status={"logistics_status": "LOGISTICS_DELIVERY_DONE"},
            status_datas={"logistics_status": {"em": quando.isoformat(), "fonte": "plataforma"}},
        )
    )
    await db.commit()


async def _loja(db, user) -> uuid.UUID:
    integ = Integration(
        user_id=user.id,
        platform=IntegrationPlatform.SHOPEE,
        name="Loja Teste",
        credentials=encrypt_json({"access_token": "t", "refresh_token": "r"}),
    )
    db.add(integ)
    await db.commit()
    return integ.id


async def _conversa(
    db, *, pedido=None, plataforma="shopee", canal="chat", integ=None, comprador=None, msgs=()
):
    c = AtendimentoConversa(
        plataforma=plataforma,
        canal=canal,
        externo_id=f"conv-{uuid.uuid4().hex[:8]}",
        comprador_nome="Comprador",
        comprador_id=comprador,
        pedido_marketplace=pedido,
        integration_id=integ,
        ultima_mensagem_em=AGORA - timedelta(hours=1),
    )
    db.add(c)
    await db.flush()
    for autor, texto, quando, anexos in msgs:
        db.add(
            AtendimentoMensagem(
                conversa_id=c.id,
                externo_id=uuid.uuid4().hex,
                autor=autor,
                origem=autor if autor != "loja" else "externo",
                tipo="texto" if texto else "imagem",
                texto=texto,
                enviada_em=quando,
                anexos=list(anexos),
            )
        )
    await db.commit()
    return c


async def _rodar(db, **kw):
    r = await auto.rodar(db, agora=AGORA, **kw)
    await db.commit()
    return r


async def _garantias(db):
    return list((await db.execute(select(Garantia).order_by(Garantia.id))).scalars())


# ── Criação ──────────────────────────────────────────────────────────────────


async def test_cria_a_garantia_pela_nf_de_produto(db):
    await _pedido(db)
    await _nf(db, valor=1000, nome="Maria da Nota")
    await _nf(db, numero="1946", serie="2", valor=17)  # a de embalagem
    await _entregue(db)
    r = await _rodar(db)
    assert r.criadas == 1
    [g] = await _garantias(db)
    assert g.origem == "robo" and g.criado_por is None
    assert (g.nf_numero, g.nf_serie, g.cpf) == ("2611", "2", CPF)
    assert g.cliente_nome == "Maria da Nota"
    assert g.data_inicio == date(2026, 10, 5)
    assert g.fim_hardware == date(2027, 1, 5)
    assert g.itens[0]["uranyx"] is True
    [log] = list((await db.execute(select(GarantiaLog))).scalars())
    assert log.acao == "criou_automatico" and log.user_id is None
    assert log.user_nome == auto.ROTULO_ROBO
    # Rodar de novo não cria outra.
    assert (await _rodar(db)).criadas == 0
    assert len(await _garantias(db)) == 1


async def test_so_nf_de_embalagem_espera_a_de_produto(db):
    await _pedido(db)
    await _nf(db, numero="1946", valor=17)
    assert (await _rodar(db)).avaliados == 0
    await _nf(db, numero="2611", valor=1000)
    assert (await _rodar(db)).criadas == 1


async def test_sem_entrega_nasce_aguardando(db):
    await _pedido(db)
    await _nf(db)
    await _rodar(db)
    [g] = await _garantias(db)
    assert g.data_inicio is None and svc.status_da(g, HOJE) == svc.STATUS_AGUARDANDO


async def test_cnpj_vira_pendencia_e_nao_e_relido_logo(db):
    await _pedido(db)
    await _nf(db, doc="12345678000199")
    r = await _rodar(db)
    assert r.criadas == 0 and r.pendencias == {"nf_sem_cpf": 1}
    p = await db.get(GarantiaPendencia, "310001")
    assert p.motivo == "nf_sem_cpf" and p.tentativas == 1
    assert (await _rodar(db)).avaliados == 0  # dentro do REVER_PENDENCIA


async def test_acessorio_da_charlots_nao_vira_garantia(db):
    # a017 = balança de mala (Charlots): o recorte do SKU pega, a linha do item não.
    await _pedido(db, sku="a017", descricao="Balança Digital de Bagagem Charlots")
    await _nf(db)
    r = await _rodar(db)
    assert r.pendencias == {"sem_produto_uranyx": 1}
    assert not await _garantias(db)


async def test_pedido_com_garantia_manual_nao_ganha_outra(db, make_user):
    u = await make_user(role=UserRole.ADMIN)
    await _pedido(db)
    await _nf(db)
    db.add(
        Garantia(
            pedido_bling="310001",
            nf_numero="1946",
            nf_serie="2",
            cliente_nome="Maria",
            cpf=CPF,
            criado_por=u.id,
        )
    )
    await db.commit()
    assert (await _rodar(db)).avaliados == 0
    assert len(await _garantias(db)) == 1


async def test_mesma_nf_ja_com_garantia_vira_pendencia(db, make_user):
    u = await make_user(role=UserRole.ADMIN)
    await _pedido(db)
    await _nf(db)
    db.add(
        Garantia(
            pedido_bling="999",
            nf_numero="2611",
            nf_serie="",
            cliente_nome="Maria",
            cpf=CPF,
            criado_por=u.id,
        )
    )
    await db.commit()
    r = await _rodar(db)
    assert r.pendencias == {"nf_ja_tem_garantia": 1}
    assert len(await _garantias(db)) == 1


async def test_cancelado_nao_vira_e_a_do_robo_e_descartada(db):
    await _pedido(db, numero="310002", numeroloja="261001ZZZZ0001", situacao="12")
    await _nf(db, pedido="310002")
    assert (await _rodar(db)).avaliados == 0

    await _pedido(db)
    await _nf(db)
    await _rodar(db)
    [g] = await _garantias(db)
    linha = (await db.execute(select(BlingOrder).where(BlingOrder.numero == "310001"))).scalar_one()
    linha.situacao = "12"
    await db.commit()
    r = await _rodar(db)
    assert r.descartadas == 1
    await db.refresh(g)
    assert g.descartada_em is not None
    acoes = [x.acao for x in (await db.execute(select(GarantiaLog))).scalars()]
    assert acoes == ["criou_automatico", "descartou"]


async def test_cancelado_com_atendimento_fica(db, make_user):
    u = await make_user(role=UserRole.ADMIN)
    await _pedido(db)
    await _nf(db)
    await _rodar(db)
    [g] = await _garantias(db)
    db.add(
        GarantiaAtendimento(
            garantia_id=g.id,
            data_atendimento=AGORA,
            atendente_id=u.id,
            atendente_nome="x",
            conversa_id=uuid.uuid4(),
            conversa_link="/atendimento",
            resumo="r",
            tipo_problema="hardware",
            cobertura="coberto",
            solucao="troca",
        )
    )
    linha = (await db.execute(select(BlingOrder).where(BlingOrder.numero == "310001"))).scalar_one()
    linha.situacao = "12"
    await db.commit()
    assert (await _rodar(db)).descartadas == 0


# ── Conversas ────────────────────────────────────────────────────────────────


async def test_liga_a_conversa_do_pedido_e_a_do_comprador(db, make_user):
    u = await make_user(role=UserRole.ADMIN)
    loja = await _loja(db, u)
    do_pedido = await _conversa(db, pedido="261001ABCD1234", integ=loja, comprador="77")
    # A conversa do comprador já aponta para um pedido MAIS NOVO dele.
    do_comprador = await _conversa(
        db, pedido="261015NOVO0001", integ=loja, comprador="88", plataforma="shopee"
    )
    db.add(
        AtendimentoPedidoComprador(
            integration_id=loja,
            plataforma="shopee",
            comprador_id="88",
            pedido="261001ABCD1234",
            criado_em=COMPRA,
        )
    )
    pergunta = await _conversa(db, pedido="261001ABCD1234", canal="pergunta")
    await db.commit()
    await _pedido(db)
    await _nf(db)
    r = await _rodar(db)
    assert r.criadas == 1 and r.conversas_ligadas == 2
    ligadas = {
        (x.conversa_id, x.como) for x in (await db.execute(select(GarantiaConversa))).scalars()
    }
    assert ligadas == {(do_pedido.id, "pedido"), (do_comprador.id, "comprador")}
    assert pergunta.id not in {c for c, _ in ligadas}


async def test_conversa_nova_entra_na_volta_seguinte(db):
    await _pedido(db)
    await _nf(db)
    await _rodar(db)
    c = await _conversa(db, pedido="261001ABCD1234")
    r = await _rodar(db)
    assert r.conversas_ligadas == 1
    [x] = list((await db.execute(select(GarantiaConversa))).scalars())
    assert x.conversa_id == c.id


async def test_numero_curto_nao_liga_sozinho(db):
    await _pedido(db, numeroloja="4512")
    await _nf(db)
    await _conversa(db, pedido="4512", plataforma="site", canal="email")
    r = await _rodar(db)
    assert r.criadas == 1 and r.conversas_ligadas == 0


# ── Tela ─────────────────────────────────────────────────────────────────────


async def test_historico_corta_a_conversa_do_comprador_na_compra(client, db, make_user, auth_as):
    u = await make_user(role=UserRole.ADMIN)
    auth_as(u)
    foto = {"tipo": "imagem", "url": "https://img.sp.mms.shopee.sg/foto-1"}
    video = {"tipo": "video", "url": "https://v.vod.susercontent.com/video-1.mp4"}
    c = await _conversa(
        db,
        pedido="261001ABCD1234",
        msgs=[
            ("cliente", "tem na cor preta?", COMPRA - timedelta(days=3), ()),
            ("cliente", "parou de carregar", COMPRA + timedelta(days=10), ()),
            ("cliente", None, COMPRA + timedelta(days=10, minutes=1), (foto,)),
            ("cliente", None, COMPRA + timedelta(days=10, minutes=2), (video,)),
            ("loja", "vamos ver", COMPRA + timedelta(days=10, hours=1), ()),
        ],
    )
    await _pedido(db)
    await _nf(db)
    await _entregue(db)
    await _rodar(db)
    [g] = await _garantias(db)

    r = await client.get(f"/api/garantias/{g.id}/historico")
    assert r.status_code == 200, r.text
    h = r.json()
    assert not h["restrito"]
    assert [x["id"] for x in h["conversas"]] == [str(c.id)]
    textos = [m["texto"] for m in h["mensagens"]]
    assert "tem na cor preta?" not in textos  # antes da compra (pré-venda)
    assert textos == ["parou de carregar", None, None, "vamos ver"]
    assert h["mensagens"][2]["anexos"] == [video]
    assert [m["tipo"] for m in h["marcos"]] == ["compra", "entrega"]
    acoes = [x.acao for x in (await db.execute(select(GarantiaLog))).scalars()]
    assert acoes[-1] == "consultou"


async def test_lista_conversas_em_cima_e_sem_as_descartadas(client, db, make_user, auth_as):
    u = await make_user(role=UserRole.ADMIN)
    auth_as(u)
    await _pedido(db)
    await _nf(db)
    await _pedido(db, numero="310003", numeroloja="261002EEEE0003")
    await _nf(db, pedido="310003", numero="2700", doc=CPF_2)
    await _conversa(
        db,
        pedido="261002EEEE0003",
        msgs=[("cliente", "chegou quebrado", COMPRA + timedelta(days=5), ())],
    )
    await _rodar(db)
    r = await client.post("/api/garantias/lista", json={"ordem": "atividade"})
    itens = r.json()["itens"]
    assert [i["pedido_bling"] for i in itens] == ["310003", "310001"]
    assert itens[0]["origem"] == "robo" and itens[0]["conversas"] == 1
    r = await client.post("/api/garantias/lista", json={"com_conversa": True})
    assert [i["pedido_bling"] for i in r.json()["itens"]] == ["310003"]

    g = next(x for x in await _garantias(db) if x.pedido_bling == "310001")
    g.descartada_em = AGORA
    await db.commit()
    r = await client.post("/api/garantias/lista", json={})
    assert [i["pedido_bling"] for i in r.json()["itens"]] == ["310003"]
    assert r.json()["indicadores"]["total"] == 1


async def test_detalhe_da_garantia_do_robo(client, db, make_user, auth_as):
    auth_as(await make_user(role=UserRole.ADMIN))
    await _pedido(db)
    await _nf(db)
    await _rodar(db)
    [g] = await _garantias(db)
    r = await client.get(f"/api/garantias/{g.id}")
    assert r.status_code == 200, r.text
    assert r.json()["criado_por"] == {"id": None, "nome": "Sistema (garantia automática)"}
    assert r.json()["origem"] == "robo"


async def test_pedido_que_sai_de_cancelado_reativa_a_garantia(db):
    await _pedido(db)
    await _nf(db)
    await _rodar(db)
    linha = (await db.execute(select(BlingOrder).where(BlingOrder.numero == "310001"))).scalar_one()
    linha.situacao = "12"
    await db.commit()
    assert (await _rodar(db)).descartadas == 1
    linha.situacao = "15"
    await db.commit()
    r = await _rodar(db)
    assert r.reativadas == 1 and r.criadas == 0
    [g] = await _garantias(db)
    assert g.descartada_em is None and g.descartada_motivo is None
    acoes = [
        x.acao for x in (await db.execute(select(GarantiaLog).order_by(GarantiaLog.id))).scalars()
    ]
    assert acoes == ["criou_automatico", "descartou", "reativou"]


async def test_cadastro_na_tela_e_troca_de_pedido_religam_as_conversas(
    client, db, make_user, auth_as, monkeypatch
):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "garantia_automatica", True)
    auth_as(await make_user(role=UserRole.ADMIN))
    await _pedido(db)
    await _nf(db, doc="12345678000199")  # CNPJ: o robô não cria — a pessoa cadastra
    await _pedido(db, numero="310009", numeroloja="261009NOVO0009")
    await _nf(db, pedido="310009", numero="2999", doc=CPF_2)
    velha = (await _conversa(db, pedido="261001ABCD1234")).id
    nova = (await _conversa(db, pedido="261009NOVO0009")).id
    r = await client.post(
        "/api/garantias",
        json={"pedido": "310001", "nf_numero": "2611", "cliente_nome": "Maria", "cpf": CPF},
    )
    assert r.status_code == 201, r.text
    gid = r.json()["id"]
    assert set((await db.execute(select(GarantiaConversa.conversa_id))).scalars()) == {velha}
    r = await client.put(
        f"/api/garantias/{gid}", json={"pedido": "310009", "nf_numero": "2999", "cpf": CPF_2}
    )
    assert r.status_code == 200, r.text
    assert set((await db.execute(select(GarantiaConversa.conversa_id))).scalars()) == {nova}


async def test_historico_resposta_que_nao_saiu_e_marcos_sem_hora(client, db, make_user, auth_as):
    auth_as(await make_user(role=UserRole.ADMIN))
    c = await _conversa(
        db,
        pedido="261001ABCD1234",
        plataforma="ml",
        canal="pos_venda",
        msgs=[("cliente", "chegou quebrado", COMPRA + timedelta(days=5), ())],
    )
    db.add(
        AtendimentoMensagem(
            conversa_id=c.id,
            externo_id=None,
            autor="loja",
            origem="davinci_humano",
            tipo="texto",
            texto="já enviamos a troca",
            enviada_em=None,
            status="falhou",
        )
    )
    await db.commit()
    await _pedido(db)
    await _nf(db)
    await _rodar(db)
    [g] = await _garantias(db)
    h = (await client.get(f"/api/garantias/{g.id}/historico")).json()
    ultima = h["mensagens"][-1]
    assert ultima["texto"] == "já enviamos a troca" and ultima["status"] == "falhou"
    assert ultima["enviada_em"] is not None  # a hora da gravação, como no Atendimento
    compra = next(m for m in h["marcos"] if m["tipo"] == "compra")
    assert compra["sem_hora"] is True


async def test_quem_nao_le_o_atendimento_so_ve_restrito_se_ha_conversa(
    client, db, make_user, auth_as, monkeypatch
):
    from app.services.atendimento import acesso

    monkeypatch.setattr(acesso, "pode_ver", lambda _u: False)
    auth_as(await make_user(role=UserRole.ADMIN))
    await _pedido(db)
    await _nf(db)
    await _rodar(db)
    [g] = await _garantias(db)
    h = (await client.get(f"/api/garantias/{g.id}/historico")).json()
    assert h["restrito"] is False and h["mensagens"] == []
    await _conversa(db, pedido="261001ABCD1234")
    await _rodar(db)
    h = (await client.get(f"/api/garantias/{g.id}/historico")).json()
    assert h["restrito"] is True and h["mensagens"] == []


async def test_troca_de_pedido_tira_as_conversas_mesmo_com_o_robo_desligado(
    client, db, make_user, auth_as
):
    auth_as(await make_user(role=UserRole.ADMIN))
    await _pedido(db)
    await _nf(db)
    await _conversa(db, pedido="261001ABCD1234")
    await _rodar(db)  # o robô (ligado nesta hora) criou e ligou
    [g] = await _garantias(db)
    await _pedido(db, numero="310009", numeroloja="261009NOVO0009")
    await _nf(db, pedido="310009", numero="2999", doc=CPF_2)
    assert len(set((await db.execute(select(GarantiaConversa.conversa_id))).scalars())) == 1
    r = await client.put(
        f"/api/garantias/{g.id}", json={"pedido": "310009", "nf_numero": "2999", "cpf": CPF_2}
    )
    assert r.status_code == 200, r.text
    assert not list((await db.execute(select(GarantiaConversa.conversa_id))).scalars())
    h = (await client.get(f"/api/garantias/{g.id}/historico")).json()
    assert h["conversas"] == [] and h["mensagens"] == []


async def test_falha_ao_ligar_nao_derruba_o_cadastro(client, db, make_user, auth_as, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "garantia_automatica", True)

    async def _quebra(*_a, **_k):
        raise RuntimeError("lock")

    monkeypatch.setattr(auto, "ligar_conversas", _quebra)
    auth_as(await make_user(role=UserRole.ADMIN))
    await _pedido(db)
    await _nf(db, doc="12345678000199")
    r = await client.post(
        "/api/garantias",
        json={"pedido": "310001", "nf_numero": "2611", "cliente_nome": "Maria", "cpf": CPF},
    )
    assert r.status_code == 201, r.text
    assert r.json()["pedido_bling"] == "310001"


async def test_liga_a_conversa_do_ml_pelo_pack(db):
    await _pedido(db, numeroloja="2000012345678901")
    await _nf(db)
    c = AtendimentoConversa(
        plataforma="ml",
        canal="pos_venda",
        externo_id="pack-1",
        pedido_marketplace="2000099999999999",  # o order; o Bling gravou o pack
        dados={"pack_id": "2000012345678901"},
    )
    db.add(c)
    await db.commit()
    cid = c.id
    r = await _rodar(db)
    assert r.conversas_ligadas == 1
    assert set((await db.execute(select(GarantiaConversa.conversa_id))).scalars()) == {cid}
