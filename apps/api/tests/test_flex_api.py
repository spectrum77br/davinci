"""API do Flex por anúncio (routers/flex.py — projeto Flex, etapa 3).

Mesma permissão da Logística: `logistica:view` vê, `logistica:edit` age
(sincronizar, aprovar, emergência); admin passa. Nada sai da máquina: o
cliente do marketplace é falso e o "Sincronizar" só enfileira (pool falso).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import worker_pool
from app.config import get_settings
from app.models import (
    BlingOrder,
    FlexAnuncioEstado,
    FlexLog,
    FlexPedido,
    Integration,
    IntegrationPlatform,
    StoreInfo,
    UserRole,
)
from app.security.cipher import encrypt_json
from app.services import flex_config, flex_motor
from app.services.marketplaces import flex_api


@pytest.fixture(autouse=True)
def _flex_visivel(monkeypatch):
    # Estes testes são do Flex em si; quem vê (`flex_usuarios`) tem teste
    # próprio em test_flex_visibilidade.py.
    monkeypatch.setattr(flex_config, "pode_ver", lambda user: True)


class _Fila:
    """Pool do arq de mentira: guarda o que seria enfileirado (nada vai para o
    Redis de verdade — um worker local pegaria o job)."""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple, dict]] = []

    async def enqueue_job(self, nome, *args, **kw):
        self.jobs.append((nome, args, kw))
        return object()


class _SoLeitura:
    """Cliente que só confere a conta: qualquer escrita estoura."""

    def __init__(self) -> None:
        self.chamadas: list[str] = []

    async def ler_assinatura_flex(self):
        self.chamadas.append("assinatura")
        return flex_api.AssinaturaFlex(True, "in")

    async def ids_da_conta(self, status, *, max_paginas=100):
        self.chamadas.append(f"descoberta:{status}")
        return flex_api.ListagemConta(completo=True)

    async def desligar_flex(self, item):
        raise AssertionError("observar não escreve")

    ligar_flex = desligar_flex


@pytest_asyncio.fixture
async def cena(db: AsyncSession, make_user, monkeypatch):
    fila = _Fila()

    async def _pool():
        return fila

    monkeypatch.setattr(worker_pool, "get_arq_pool", _pool)

    # Nenhuma chamada de verdade ao ML: o cliente padrão só confere a conta.
    async def _so_leitura(integ):
        return _SoLeitura()

    monkeypatch.setattr(flex_motor, "montar_cliente", _so_leitura)
    cfg = get_settings()
    monkeypatch.setattr(cfg, "flex_modo", "observar")
    monkeypatch.setattr(cfg, "flex_shopee_escrita", False)
    admin = await make_user(role=UserRole.ADMIN)
    conta = Integration(user_id=admin.id, platform=IntegrationPlatform.ML, name="vita",
                        credentials=encrypt_json({"access_token": "t"}))
    db.add(conta)
    await db.flush()
    fantasma = uuid.uuid4()
    monkeypatch.setattr(cfg, "flex_contas", f"{conta.id}, {fantasma}, lixo")
    agora = datetime.now(UTC)
    db.add_all(
        [
            FlexAnuncioEstado(integration_id=conta.id, external_id="MLB1", plataforma="ml",
                              desejado="ligado", motivo="saldo Flex 5", observado="desligado",
                              observado_em=agora, aguardando_aprovacao=True, tentativas=0,
                              saldo_sp=5, familias="dg053"),
            FlexAnuncioEstado(integration_id=conta.id, external_id="MLB2", plataforma="ml",
                              desejado="inelegivel", motivo="kit", observado="ligado",
                              observado_em=agora, aguardando_aprovacao=False, tentativas=0),
            FlexAnuncioEstado(integration_id=conta.id, external_id="MLB3", plataforma="ml",
                              desejado="desligado", motivo="saldo 0", observado="desligado",
                              observado_em=agora, aguardando_aprovacao=False, tentativas=0),
        ]
    )
    db.add(FlexLog(integration_id=conta.id, external_id="MLB1", plataforma="ml", acao="decidir",
                   modo="observar", resultado="ok", estado_depois="ligado", motivo="saldo 5"))
    db.add_all(
        [
            BlingOrder(numero="5001", bling_id=5001, loja="1", item_index=0,
                       item_codigo="dg053.ci", item_quantidade=1, situacao="6", data=agora),
            FlexPedido(bling_id=5001, plataforma="ml", integration_id=conta.id,
                       numeroloja="2000001", envio_tipo="self_service", no_sp=False,
                       alerta="o .sp não cobre", prazo=agora + timedelta(hours=3)),
            FlexPedido(bling_id=5002, plataforma="shopee", envio_tipo="90022", no_sp=True,
                       prazo=agora + timedelta(hours=1)),
        ]
    )
    await db.commit()
    return {"admin": admin, "conta_id": conta.id, "fantasma": fantasma, "cfg": cfg, "fila": fila}


async def _emergencia(client: AsyncClient, cena) -> dict:
    """Aperta o botão e roda o job (o que o worker faria) — devolve o
    andamento final que a tela leria."""
    from app import worker

    r = await client.post("/api/flex/emergencia")
    assert r.status_code == 200, r.text
    corpo = r.json()
    if corpo["id"] is None:
        return corpo
    assert corpo["status"] == "na_fila"
    nome, args, kw = cena["fila"].jobs[-1]
    assert (nome, args, kw["_job_id"]) == (
        "flex_emergencia_run", (corpo["id"],), f"flex_emergencia:{corpo['id']}"
    )
    await worker.flex_emergencia_run({}, corpo["id"])
    r = await client.get(f"/api/flex/emergencia/{corpo['id']}")
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_config_sem_segredo(client: AsyncClient, cena, auth_as: Callable):
    auth_as(cena["admin"])
    r = await client.get("/api/flex/config")
    assert r.status_code == 200
    c = r.json()
    assert (c["modo"], c["pode_escrever"]) == ("observar", False)
    assert (c["n_liga"], c["n_desliga"], c["max_anuncios_por_familia"]) == (3, 1, 2)
    assert c["shopee_canais"] == ["90022"]
    contas = {x["id"]: x for x in c["contas"]}
    assert contas[str(cena["conta_id"])] == {
        "id": str(cena["conta_id"]), "nome": "vita", "plataforma": "ml", "existe": True,
        # Ainda não conferida pelo motor: tudo vazio (a tela diz "não conferida").
        "flex_ativo": None, "flex_status": None, "flex_detalhe": None, "flex_motivo": None,
        "flex_lido_em": None, "flex_erro": None, "descoberta_em": None,
        "descoberta_ok": None, "descoberta_total": None, "descoberta_novos": None,
        "descoberta_erro": None,
    }
    assert contas[str(cena["fantasma"])]["existe"] is False
    assert "token" not in r.text and "credentials" not in r.text


@pytest.mark.asyncio
async def test_permissoes(client: AsyncClient, cena, make_user, auth_as: Callable):
    ninguem = await make_user()
    auth_as(ninguem)
    assert (await client.get("/api/flex/anuncios")).status_code == 403
    assert (await client.post("/api/flex/emergencia")).status_code == 403

    so_ve = await make_user(permissions={"logistica": {"view": True}})
    auth_as(so_ve)
    assert (await client.get("/api/flex/anuncios")).status_code == 200
    assert (await client.get("/api/flex/log")).status_code == 200
    assert (await client.post("/api/flex/emergencia")).status_code == 403
    assert (await client.post("/api/flex/sincronizar")).status_code == 403
    r = await client.post(f"/api/flex/anuncios/{cena['conta_id']}/MLB1/aprovar")
    assert r.status_code == 403

    edita = await make_user(permissions={"logistica": {"view": True, "edit": True}})
    auth_as(edita)
    assert (await client.post("/api/flex/emergencia")).status_code == 200


@pytest.mark.asyncio
async def test_anuncios_com_filtros(client: AsyncClient, cena, auth_as: Callable):
    auth_as(cena["admin"])
    r = await client.get("/api/flex/anuncios")
    corpo = r.json()
    assert corpo["total"] == 3
    # Quem espera aprovação vem primeiro; depois o que está ligado.
    assert [i["external_id"] for i in corpo["itens"]] == ["MLB1", "MLB2", "MLB3"]
    assert corpo["itens"][0]["conta"] == "vita"
    r = await client.get("/api/flex/anuncios?aguardando=true")
    assert [i["external_id"] for i in r.json()["itens"]] == ["MLB1"]
    r = await client.get("/api/flex/anuncios?desejado=inelegivel&plataforma=ml")
    assert [i["external_id"] for i in r.json()["itens"]] == ["MLB2"]
    r = await client.get(f"/api/flex/anuncios?integration_id={uuid.uuid4()}")
    assert (r.json()["total"], r.json()["itens"]) == (0, [])
    assert (await client.get("/api/flex/anuncios?desejado=talvez")).status_code == 422


@pytest.mark.asyncio
async def test_aprovar(client: AsyncClient, cena, db: AsyncSession, auth_as: Callable):
    auth_as(cena["admin"])
    base = f"/api/flex/anuncios/{cena['conta_id']}"
    r = await client.post(f"{base}/MLB1/aprovar")
    assert r.status_code == 200
    corpo = r.json()
    # Em observar fica só a aprovação: nada é escrito na plataforma.
    assert (corpo["aprovado"], corpo["aplicado"], corpo["modo"]) == (True, False, "observar")
    assert corpo["estado"]["aprovado_por"] == str(cena["admin"].id)
    assert corpo["estado"]["aguardando_aprovacao"] is False

    r = await client.post(f"{base}/MLB3/aprovar")
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "nao_elegivel")
    r = await client.post(f"{base}/MLB404/aprovar")
    assert (r.status_code, r.json()["detail"]["code"]) == (404, "nao_avaliado")
    r = await client.post(f"/api/flex/anuncios/{uuid.uuid4()}/MLB1/aprovar")
    assert (r.status_code, r.json()["detail"]["code"]) == (403, "conta_nao_permitida")


@pytest.mark.asyncio
async def test_aprovar_em_piloto_liga_na_hora(
    client: AsyncClient, cena, db: AsyncSession, auth_as: Callable, monkeypatch
):
    monkeypatch.setattr(cena["cfg"], "flex_modo", "piloto")
    chamadas: list[tuple[str, str]] = []

    class ML:
        async def ler_assinatura_flex(self):
            return flex_api.AssinaturaFlex(True, "in")

        async def ler_flex(self, item):
            chamadas.append(("ler", item))
            return flex_api.ResultadoFlex(flex_api.OK, has_flex=False)

        async def ligar_flex(self, item):
            chamadas.append(("ligar", item))
            return flex_api.ResultadoFlex(flex_api.OK, has_flex=True, status_http=204)

    async def _montar(integ):
        return ML()

    monkeypatch.setattr(flex_motor, "montar_cliente", _montar)

    # Antes de ligar, o saldo do .sp é conferido no Bling (de mentira aqui).
    async def _saldos_bling(skus):
        return dict.fromkeys(skus, 5)

    monkeypatch.setattr(flex_motor, "saldos_bling", _saldos_bling)
    # O motor recalcula com o saldo de agora: precisa do .sp no banco.
    from app.models import Product, ProductLink

    p_ci = Product(user_id=cena["admin"].id, sku="dg053.ci", name="x", stock=9, situacao="A")
    p_sp = Product(user_id=cena["admin"].id, sku="dg053.sp", name="x", stock=5, situacao="A")
    db.add_all([p_ci, p_sp])
    await db.flush()
    db.add(ProductLink(user_id=cena["admin"].id, product_id=p_ci.id,
                       integration_id=cena["conta_id"], platform=IntegrationPlatform.ML,
                       external_id="MLB1", stock=3))
    await db.commit()

    auth_as(cena["admin"])
    r = await client.post(f"/api/flex/anuncios/{cena['conta_id']}/MLB1/aprovar")
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["aplicado"] is True
    assert corpo["estado"]["observado"] == "ligado"
    assert chamadas == [("ler", "MLB1"), ("ligar", "MLB1")]


@pytest.mark.asyncio
async def test_sincronizar_enfileira_no_modo_atual(
    client: AsyncClient, cena, auth_as: Callable, monkeypatch
):
    pedidos: list[tuple] = []

    class Pool:
        async def enqueue_job(self, nome, *args, **kw):
            pedidos.append((nome, args, kw))
            return object()

    async def _pool():
        return Pool()

    monkeypatch.setattr(worker_pool, "get_arq_pool", _pool)
    auth_as(cena["admin"])
    r = await client.post("/api/flex/sincronizar")
    assert r.json() == {"enfileirado": True, "modo": "observar", "motivo": None}
    assert pedidos[0][0] == "flex_motor_run"
    assert pedidos[0][1] == (str(cena["admin"].id),)
    assert pedidos[0][2]["_job_id"].startswith("flex_motor_manual:")

    monkeypatch.setattr(cena["cfg"], "flex_modo", "desligado")
    r = await client.post("/api/flex/sincronizar")
    assert r.json()["enfileirado"] is False
    assert len(pedidos) == 1


@pytest.mark.asyncio
async def test_emergencia_em_observar_simula(client: AsyncClient, cena, auth_as: Callable,
                                             monkeypatch):
    cli = _SoLeitura()

    async def _montar(integ):
        return cli

    monkeypatch.setattr(flex_motor, "montar_cliente", _montar)
    auth_as(cena["admin"])
    andamento = await _emergencia(client, cena)
    assert andamento["status"] == "concluida"
    assert (andamento["modo"], andamento["escreve"]) == ("observar", False)
    corpo = andamento["resumo"]
    assert corpo["simulados"] == 1  # MLB2 é o único ligado
    assert corpo["desligados"] == 0
    # Só leitura: a conta conferida e descoberta, nenhuma escrita.
    assert cli.chamadas == ["assinatura", "descoberta:active", "descoberta:paused"]
    r = await client.get("/api/flex/log?acao=emergencia")
    linhas = r.json()
    assert {x["resultado"] for x in linhas} == {"simulado"}
    assert {x["por"] for x in linhas} == {str(cena["admin"].id)}


@pytest.mark.asyncio
async def test_log_e_pedidos(client: AsyncClient, cena, auth_as: Callable):
    auth_as(cena["admin"])
    r = await client.get(f"/api/flex/log?integration_id={cena['conta_id']}&external_id=MLB1")
    linhas = r.json()
    assert [(x["acao"], x["conta"]) for x in linhas] == [("decidir", "vita")]
    assert (await client.get("/api/flex/log?acao=voar")).status_code == 422

    r = await client.get("/api/flex/pedidos")
    pedidos = r.json()
    # O pedido com aviso primeiro, mesmo com prazo mais longo.
    assert [p["bling_id"] for p in pedidos] == [5001, 5002]
    assert pedidos[0]["numero"] == "5001"
    assert pedidos[0]["skus"] == ["dg053.ci"]
    assert pedidos[0]["conta"] == "vita"
    assert pedidos[0]["situacao"] == "6"
    r = await client.get("/api/flex/pedidos?so_alerta=true")
    assert [p["bling_id"] for p in r.json()] == [5001]


@pytest_asyncio.fixture
async def equipe(db: AsyncSession, cena, make_user):
    """Uma segunda conta liberada (da equipe 7) e um usuário só da equipe 7
    com logistica:view+edit. A conta "vita" do `cena` NÃO é da equipe dele."""
    admin = cena["admin"]
    loja7 = Integration(user_id=admin.id, platform=IntegrationPlatform.ML, name="loja7",
                        credentials=encrypt_json({"access_token": "t"}))
    db.add(loja7)
    await db.flush()
    db.add(StoreInfo(user_id=admin.id, platform="ml", account_name="loja7", sales_team=7,
                     integration_id=loja7.id, bling_store_id="77"))
    agora = datetime.now(UTC)
    db.add_all([
        FlexAnuncioEstado(integration_id=loja7.id, external_id="MLB70", plataforma="ml",
                          desejado="ligado", motivo="saldo Flex 5", observado="desligado",
                          observado_em=agora, aguardando_aprovacao=True, tentativas=0),
        FlexLog(integration_id=loja7.id, external_id="MLB70", plataforma="ml", acao="decidir",
                modo="observar", resultado="ok"),
        BlingOrder(numero="7701", bling_id=7701, loja="77", item_index=0,
                   item_codigo="dg053.ci", item_quantidade=1, situacao="6", data=agora),
        # Pedido marcado só pela Logística: sem conta, casa pela loja do Bling.
        FlexPedido(bling_id=7701, plataforma="ml", no_sp=False, alerta="o .sp não cobre"),
    ])
    await db.commit()
    membro = await make_user(permissions={"logistica": {"view": True, "edit": True}})
    membro.sales_teams = [7]
    await db.commit()
    return {"loja7": loja7.id, "membro": membro}


@pytest.mark.asyncio
async def test_telas_do_flex_respeitam_a_equipe(
    client: AsyncClient, cena, equipe, auth_as: Callable, monkeypatch
):
    """Achado da revisão: /api/flex/* não aplicava o escopo por equipe — o
    usuário da equipe 7 via anúncios, trilha e pedidos de todas as contas,
    aprovava anúncio de outra equipe e a emergência desligava tudo."""
    monkeypatch.setattr(cena["cfg"], "flex_contas", f"{cena['conta_id']}, {equipe['loja7']}")
    auth_as(equipe["membro"])
    corpo = (await client.get("/api/flex/anuncios")).json()
    assert [i["external_id"] for i in corpo["itens"]] == ["MLB70"]
    assert corpo["resumo"]["avaliados"] == 1 and corpo["resumo"]["aguardando"] == 1
    assert [x["external_id"] for x in (await client.get("/api/flex/log")).json()] == ["MLB70"]
    assert [p["bling_id"] for p in (await client.get("/api/flex/pedidos")).json()] == [7701]
    assert [c["id"] for c in (await client.get("/api/flex/config")).json()["contas"]] == [
        str(equipe["loja7"])
    ]
    r = await client.post(f"/api/flex/anuncios/{cena['conta_id']}/MLB1/aprovar")
    assert (r.status_code, r.json()["detail"]["code"]) == (403, "fora_do_escopo")
    r = await client.post(f"/api/flex/anuncios/{equipe['loja7']}/MLB70/aprovar")
    assert r.status_code == 200
    # Emergência: só as contas da equipe (a "vita" tem o MLB2 ligado).
    corpo = (await _emergencia(client, cena))["resumo"]
    assert corpo["alvos"] == 0 and corpo["simulados"] == 0
    # A emergência do membro é dele: o andamento não aparece para outro.
    ultima = (await client.get("/api/flex/emergencia/ultima")).json()
    assert ultima["resumo"]["alvos"] == 0

    # O admin continua vendo tudo.
    auth_as(cena["admin"])
    assert (await client.get("/api/flex/anuncios")).json()["total"] == 4
    assert (await _emergencia(client, cena))["resumo"]["simulados"] == 1


@pytest.mark.asyncio
async def test_aguardando_so_conta_contas_liberadas(
    client: AsyncClient, cena, db: AsyncSession, auth_as: Callable, monkeypatch
):
    """Achado da revisão: a conta saiu de flex_contas com anúncios esperando
    aprovação — o motor não regrava conta fora da lista e a bolinha "1
    esperando sua aprovação" ficava para sempre, sem botão para limpar."""
    auth_as(cena["admin"])
    r = (await client.get("/api/flex/anuncios")).json()
    assert r["resumo"]["aguardando"] == 1
    monkeypatch.setattr(cena["cfg"], "flex_contas", "")
    r = (await client.get("/api/flex/anuncios")).json()
    assert r["resumo"]["aguardando"] == 0
    mlb1 = next(i for i in r["itens"] if i["external_id"] == "MLB1")
    assert mlb1["aguardando_aprovacao"] is False
    r = (await client.get("/api/flex/anuncios?aguardando=true")).json()
    assert r["itens"] == []
    monkeypatch.setattr(cena["cfg"], "flex_contas", str(cena["conta_id"]))
    assert (await client.get("/api/flex/anuncios")).json()["resumo"]["aguardando"] == 1
    # Conta arquivada também sai (o motor não mexe mais nela).
    await db.execute(text("UPDATE integrations SET archived_at = now() WHERE id = :i"),
                     {"i": cena["conta_id"]})
    await db.commit()
    assert (await client.get("/api/flex/anuncios")).json()["resumo"]["aguardando"] == 0


@pytest.mark.asyncio
async def test_pedido_que_saiu_sem_sp_espera_o_acerto(
    client: AsyncClient, cena, db: AsyncSession, auth_as: Callable
):
    """Achado da revisão: o pedido Flex que saiu (15) sem passar pelo .sp
    continua na lista (e no desconto do saldo Flex) até alguém marcar que
    acertou o estoque no Bling."""
    await db.execute(text("UPDATE bling_orders SET situacao = '15' WHERE bling_id = 5001"))
    await db.commit()
    auth_as(cena["admin"])
    r = (await client.get("/api/flex/pedidos?so_alerta=true&abertos=true")).json()
    assert [(p["bling_id"], p["acerto_pendente"]) for p in r] == [(5001, True)]
    r = await client.post("/api/flex/pedidos/5002/acertado")  # não saiu: nada a acertar
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "sem_acerto_pendente")
    r = await client.post("/api/flex/pedidos/5001/acertado")
    assert r.status_code == 200
    assert (await client.get("/api/flex/pedidos?so_alerta=true&abertos=true")).json() == []
    linhas = (await client.get("/api/flex/log?acao=acertar_estoque")).json()
    assert [(x["bling_id"], x["por"]) for x in linhas] == [(5001, str(cena["admin"].id))]
    r = await client.post("/api/flex/pedidos/5001/acertado")
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_sem_login_nao_entra(client: AsyncClient, auth_as: Callable):
    auth_as(None)
    assert (await client.get("/api/flex/config")).status_code == 401



@pytest.mark.asyncio
async def test_config_mostra_o_flex_de_cada_conta(
    client: AsyncClient, cena, db: AsyncSession, auth_as: Callable
):
    """A conta conferida pelo motor (`flex_conta`): a tela mostra se ela pode
    ter Flex e por quê não — sem chamar a plataforma."""
    from app.models import FlexConta

    agora = datetime.now(UTC)
    db.add(FlexConta(integration_id=cena["conta_id"], plataforma="ml", flex_ativo=False,
                     status="pending", detalhe="assinatura do Flex: pending", lido_em=agora,
                     descoberta_em=agora, descoberta_ok=True, descoberta_total=1545,
                     descoberta_novos=12))
    await db.commit()
    auth_as(cena["admin"])
    contas = {x["id"]: x for x in (await client.get("/api/flex/config")).json()["contas"]}
    c = contas[str(cena["conta_id"])]
    assert (c["flex_ativo"], c["flex_status"]) == (False, "pending")
    assert c["flex_motivo"] == "conta sem Flex ativo no ML (status pending)"
    assert (c["descoberta_total"], c["descoberta_novos"], c["descoberta_ok"]) == (1545, 12, True)

    # E aprovar um anúncio dela: a conta não pode (409), nada é gravado.
    r = await client.post(f"/api/flex/anuncios/{cena['conta_id']}/MLB1/aprovar")
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "conta_sem_flex")


@pytest.mark.asyncio
async def test_aprovar_shopee_so_leitura_e_409(
    client: AsyncClient, cena, db: AsyncSession, auth_as: Callable, monkeypatch
):
    loja = Integration(user_id=cena["admin"].id, platform=IntegrationPlatform.SHOPEE,
                       name="loja", credentials=encrypt_json({"access_token": "t"}))
    db.add(loja)
    await db.flush()
    loja_id = loja.id
    db.add(FlexAnuncioEstado(integration_id=loja_id, external_id="777", plataforma="shopee",
                             desejado="ligado", motivo="saldo Flex 5", observado="desligado",
                             aguardando_aprovacao=False, tentativas=0))
    await db.commit()
    monkeypatch.setattr(cena["cfg"], "flex_contas", f"{cena['conta_id']},{loja_id}")
    auth_as(cena["admin"])
    r = await client.post(f"/api/flex/anuncios/{loja_id}/777/aprovar")
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "shopee_so_leitura")


@pytest.mark.asyncio
async def test_emergencia_em_andamento_nao_cria_outra(
    client: AsyncClient, cena, db: AsyncSession, auth_as: Callable, monkeypatch
):
    monkeypatch.setattr(cena["cfg"], "flex_modo", "piloto")
    auth_as(cena["admin"])
    r1 = (await client.post("/api/flex/emergencia")).json()
    assert r1["status"] == "na_fila" and r1["escreve"] is True
    # O job ainda não rodou: o segundo clique devolve a mesma (sem outro job).
    r2 = (await client.post("/api/flex/emergencia")).json()
    assert (r2["id"], r2["ja_em_andamento"]) == (r1["id"], True)
    assert [j[0] for j in cena["fila"].jobs] == ["flex_emergencia_run"]
    ultima = (await client.get("/api/flex/emergencia/ultima")).json()
    assert ultima["id"] == r1["id"]
    assert (await client.get("/api/flex/emergencia/999999")).status_code == 404
    # O job morreu no meio (worker reiniciado): "rodando" sem andamento há
    # mais de 10 min não segura o botão — o clique cria outra.
    await db.execute(
        text("UPDATE flex_emergencia SET status = 'rodando',"
             " atualizado_em = now() - interval '11 minutes' WHERE id = :i"),
        {"i": r1["id"]},
    )
    await db.commit()
    r3 = (await client.post("/api/flex/emergencia")).json()
    assert r3["id"] != r1["id"] and r3["ja_em_andamento"] is False


@pytest.mark.asyncio
async def test_emergencia_sem_fila_avisa_e_marca_falhou(
    client: AsyncClient, cena, auth_as: Callable, monkeypatch
):
    async def _sem_redis():
        raise ConnectionError("redis fora")

    monkeypatch.setattr(worker_pool, "get_arq_pool", _sem_redis)
    auth_as(cena["admin"])
    r = await client.post("/api/flex/emergencia")
    assert (r.status_code, r.json()["detail"]["code"]) == (503, "fila_indisponivel")
    ultima = (await client.get("/api/flex/emergencia/ultima")).json()
    assert ultima["status"] == "falhou" and "fila" in ultima["erro"]
