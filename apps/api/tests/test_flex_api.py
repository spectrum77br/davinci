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
    UserRole,
)
from app.security.cipher import encrypt_json
from app.services import flex_motor
from app.services.marketplaces import flex_api


@pytest_asyncio.fixture
async def cena(db: AsyncSession, make_user, monkeypatch):
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
    return {"admin": admin, "conta_id": conta.id, "fantasma": fantasma, "cfg": cfg}


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
    assert r.json() == {"total": 0, "itens": []}
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
        async def ler_flex(self, item):
            chamadas.append(("ler", item))
            return flex_api.ResultadoFlex(flex_api.OK, has_flex=False)

        async def ligar_flex(self, item):
            chamadas.append(("ligar", item))
            return flex_api.ResultadoFlex(flex_api.OK, has_flex=True, status_http=204)

    async def _montar(integ):
        return ML()

    monkeypatch.setattr(flex_motor, "montar_cliente", _montar)
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
    async def _montar(integ):
        raise AssertionError("observar não monta cliente para escrever")

    monkeypatch.setattr(flex_motor, "montar_cliente", _montar)
    auth_as(cena["admin"])
    r = await client.post("/api/flex/emergencia")
    assert r.status_code == 200
    corpo = r.json()
    assert (corpo["modo"], corpo["escreve"]) == ("observar", False)
    assert corpo["simulados"] == 1  # MLB2 é o único ligado
    assert corpo["desligados"] == 0
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


@pytest.mark.asyncio
async def test_sem_login_nao_entra(client: AsyncClient, auth_as: Callable):
    auth_as(None)
    assert (await client.get("/api/flex/config")).status_code == 401

