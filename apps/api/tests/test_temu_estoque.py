# ruff: noqa: S105 - tokens e segredos abaixo são sintéticos.
"""Temu Barbosa (vendedor local BR): o DaVinci passa a mandar o ESTOQUE.

Decisão do dono, 24/11/2026. Cobre:
- assinatura igual à oficial (exemplo do doc, booleano, None, aspas);
- app_secret fora da URL/corpo/mensagens; parâmetros comuns não sobrescritos;
- leitura de errorCode/errorMsg e a classificação RETRYABLE / CONTA / FATAL;
- Testar conexão por bg.open.accesstoken.info.get (loja local, Brasil,
  validade, escopos) sem devolver o token;
- leitor de anúncios por SKU (goodsId/skuId/outSkuSn);
- envio de estoque ABSOLUTO conferido SKU a SKU;
- a TRAVA `_temu_stock_write_enabled` (padrão desligada) em todos os caminhos
  de envio: orquestrador, webhook do Bling (sync_product_run), sincronizar
  todos (sync_all_run), Vincular e importar anúncios (só leem);
- preço da Temu não sai;
- endpoint de administrador e o formulário que não injeta a trava.

Nenhuma chamada real à Temu: todo HTTP passa por httpx.MockTransport.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections.abc import Callable
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import worker
from app.models import (
    Alert,
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    Integration,
    IntegrationPlatform,
    LinkSyncStatus,
    Listing,
    Product,
    ProductLink,
    SyncLog,
    User,
    UserRole,
)
from app.routers.integrations import _validated_temu_credentials
from app.security.cipher import decrypt_json, encrypt_json
from app.services import auto_link as auto_link_mod
from app.services import listings_import
from app.services.marketplaces import temu as temu_mod
from app.services.marketplaces.base import SyncStatus
from app.services.marketplaces.temu import (
    STOCK_WRITE_FLAG,
    TemuClient,
    TemuErro,
    TemuEscritaBloqueada,
    assinar,
)
from app.services.pricing.push import _dispatch_price_update_link
from app.services.sync_orchestrator import SyncOrchestrator

SEGREDO = "segredo-sintetico-123"
TOKEN = "token-sintetico-456"
CREDS = {"app_key": "app-sintetico", "app_secret": SEGREDO, "access_token": TOKEN}
GOODS = "601099548666279"
SKU_ID = "17592352673534"


# ---------------------------------------------------------------- HTTP falso


class TemuFalsa:
    """Gateway falso: grava cada pedido e responde pela função `responder`."""

    def __init__(self, responder: Callable[[dict], dict | httpx.Response] | None = None):
        self.pedidos: list[dict] = []
        self.urls: list[str] = []
        self.brutos: list[bytes] = []
        self.responder = responder or (lambda corpo: {"success": True, "result": {}})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        corpo = json.loads(request.content)
        self.pedidos.append(corpo)
        self.urls.append(str(request.url))
        self.brutos.append(request.content)
        r = self.responder(corpo)
        return r if isinstance(r, httpx.Response) else httpx.Response(200, json=r)

    def tipos(self) -> list[str]:
        return [p["type"] for p in self.pedidos]


@pytest.fixture
def temu_http(monkeypatch):
    def instalar(responder=None) -> TemuFalsa:
        falsa = TemuFalsa(responder)
        original = httpx.AsyncClient
        monkeypatch.setattr(
            temu_mod.httpx,
            "AsyncClient",
            lambda **kw: original(transport=httpx.MockTransport(falsa), **kw),
        )
        return falsa

    return instalar


def _link(external_id=GOODS, variation_id=SKU_ID, stock=3):
    return SimpleNamespace(external_id=external_id, variation_id=variation_id, stock=stock)


def _ok_estoque(corpo: dict) -> dict:
    sku = corpo["skuStockTargetList"][0]["skuId"]
    return {
        "success": True,
        "errorCode": 1000000,
        "result": {
            "goodsId": corpo["goodsId"],
            "operateResult": True,
            "skuStockEditStatusInfoList": [{"skuId": sku, "stockEditStatus": True}],
        },
    }


# ---------------------------------------------------------------- assinatura


def _assinatura_oficial(app_secret: str, request_params: dict) -> str:
    """Cópia literal do TestSign.py da documentação da Temu."""
    temp = []
    for k, v in sorted(request_params.items()):
        v = json.dumps(v, ensure_ascii=False, separators=(",", ":"))
        temp.append(str(k) + str(v.strip('"')))
    un_sign = str(app_secret) + "".join(temp) + str(app_secret)
    return hashlib.md5(un_sign.encode("utf-8")).hexdigest().upper()  # noqa: S324


def test_assinatura_bate_com_o_exemplo_oficial():
    params = {
        "access_token": "2nifvmpyymvypwmcms5ct4uqqudrwgpmzbcnmkt1jzjkuaf3x56iixym",
        "app_key": "f9d5cc9313893a20d5aa85c654e8f503",
        "data_type": "JSON",
        "sendRequestList": [
            {
                "orderSendInfoList": [
                    {
                        "quantity": 1,
                        "orderSn": "211-21905473070712792",
                        "parentOrderSn": "PO-211-21905452099192792",
                        "goodsId": 601099548666279,
                        "skuId": 17592352673534,
                    }
                ],
                "carrierId": "699272611",
                "trackingNumber": "270324232756",
            }
        ],
        "sendType": 0,
        "timestamp": 1711009072,
        "type": "bg.logistics.shipment.confirm",
    }
    segredo = "c7e0a1a63542be4de3cb5488f9fba8149e8fc290"
    assert assinar(segredo, params) == "4CCF219942D4180C6DDA3CE36C1B838F"


def test_assinatura_de_booleano_none_e_aspas_igual_a_oficial():
    params = {
        "rejectSkuPricing": True,
        "shipLater": False,
        "vazio": None,
        "texto": 'mala "grande" \\ 20"',
        "acento": "São Paulo",
        "lista": [{"a": True, "b": None}],
        "n": 7,
    }
    assert assinar("s", params) == _assinatura_oficial("s", params)
    assert temu_mod._valor_assinado(True) == "true"
    assert temu_mod._valor_assinado(False) == "false"
    assert temu_mod._valor_assinado(None) == "null"
    assert temu_mod._valor_assinado('a"b') == 'a\\"b'


# ---------------------------------------------------------------- transporte


def test_segredo_fora_da_url_e_do_corpo_e_assinatura_confere(temu_http):
    falsa = temu_http()
    asyncio.run(TemuClient(CREDS)._request("bg.open.accesstoken.info.get"))
    assert falsa.urls == ["https://openapi-b-global.temu.com/openapi/router"]
    assert SEGREDO.encode() not in falsa.brutos[0]
    corpo = dict(falsa.pedidos[0])
    sign = corpo.pop("sign")
    assert sign == _assinatura_oficial(SEGREDO, corpo)
    assert corpo["access_token"] == TOKEN and corpo["data_type"] == "JSON"
    assert abs(corpo["timestamp"] - time.time()) < 60


@pytest.mark.parametrize(
    "chave", ["type", "app_key", "access_token", "timestamp", "data_type", "version", "sign"]
)
def test_parametro_comum_nao_pode_ser_sobrescrito(temu_http, chave):
    falsa = temu_http()
    with pytest.raises(ValueError):
        asyncio.run(TemuClient(CREDS)._request("bg.open.accesstoken.info.get", {chave: "x"}))
    assert falsa.pedidos == []


def test_transporte_recusa_escrita_sem_trava_e_qualquer_escrita_que_nao_seja_estoque(temu_http):
    falsa = temu_http()
    desligado = TemuClient(CREDS)
    ligado = TemuClient({**CREDS, STOCK_WRITE_FLAG: True})
    for cliente, api in (
        (desligado, "bg.local.goods.stock.edit"),
        (ligado, "bg.local.goods.priceorder.change.sku.price"),
        (ligado, "bg.local.goods.partial.update"),
        (ligado, "bg.local.goods.sale.status.set"),
        (ligado, "bg.local.goods.sku.out.sn.set"),
        (ligado, "bg.inventory.quantity.update"),
    ):
        with pytest.raises(TemuEscritaBloqueada):
            asyncio.run(cliente._request(api, {"goodsId": 1}))
    assert falsa.pedidos == []
    # Trava como texto "true" não vale: só booleano verdadeiro.
    assert TemuClient({**CREDS, STOCK_WRITE_FLAG: "true"}).stock_write_enabled is False


@pytest.mark.parametrize(
    ("resposta", "classe"),
    [
        ({"success": False, "errorCode": 4000004, "errorMsg": "RATE_LIMIT"}, "retryable"),
        ({"success": False, "errorCode": 4000000, "errorMsg": "SYSTEM"}, "retryable"),
        ({"success": False, "errorCode": 150010005, "errorMsg": "Try again later"}, "retryable"),
        (httpx.Response(503, text="x"), "retryable"),
        (httpx.Response(429, text="x"), "retryable"),
        ({"success": False, "errorCode": 3000034, "errorMsg": "expired"}, "conta"),
        ({"success": False, "errorCode": 3000031, "errorMsg": "not exists"}, "conta"),
        ({"success": False, "errorCode": 3000032, "errorMsg": "no api access"}, "conta"),
        ({"success": False, "errorCode": 5000003, "errorMsg": "NOT_IN_IP_WHITE_LIST"}, "conta"),
        ({"success": False, "errorCode": 150013002, "errorMsg": "Quantity"}, "fatal"),
        (httpx.Response(400, text="x"), "fatal"),
    ],
)
def test_classificacao_dos_erros(temu_http, resposta, classe):
    temu_http(lambda corpo: resposta)
    with pytest.raises(TemuErro) as erro:
        asyncio.run(TemuClient(CREDS)._chamar("bg.open.accesstoken.info.get"))
    assert erro.value.classe == classe


def test_erro_le_errorcode_errormsg_sem_segredo_nem_token(temu_http):
    temu_http(
        lambda corpo: {
            "success": False,
            "errorCode": 5000003,
            "errorMsg": f"ip recusado {SEGREDO} {TOKEN}",
        }
    )
    with pytest.raises(TemuErro) as erro:
        asyncio.run(TemuClient(CREDS)._chamar("bg.open.accesstoken.info.get"))
    texto = str(erro.value)
    assert "5000003" in texto and "lista de IPs" in texto
    assert SEGREDO not in texto and TOKEN not in texto


def test_erro_de_rede_e_retryable(monkeypatch):
    def cai(request):
        raise httpx.ConnectError("sem rede")

    original = httpx.AsyncClient
    monkeypatch.setattr(
        temu_mod.httpx,
        "AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(cai), **kw),
    )
    with pytest.raises(TemuErro) as erro:
        asyncio.run(TemuClient(CREDS)._chamar("bg.open.accesstoken.info.get"))
    assert erro.value.classe == "retryable"


# ---------------------------------------------------------------- testar conexão


def _info(**extra):
    base = {
        "accessToken": "NUNCA-DEVOLVER",
        "mallId": 635517739923741,
        "mallType": 100,
        "regionId": 29,
        "expiredTime": int(time.time()) + 300 * 86400,
        "apiScopeList": [
            "bg.open.accesstoken.info.get",
            "temu.local.sku.list.retrieve",
            "bg.local.goods.stock.edit",
        ],
    }
    base.update(extra)
    return {"success": True, "errorCode": 1000000, "result": base}


def test_testar_conexao_confere_loja_local_brasil_e_nao_devolve_token(temu_http):
    falsa = temu_http(lambda corpo: _info())
    r = asyncio.run(TemuClient(CREDS).test_connection())
    assert falsa.tipos() == ["bg.open.accesstoken.info.get"]
    assert r.ok, r.detail
    assert r.info["mall_id"] == "635517739923741"
    assert r.info["mall_type"] == 100 and r.info["loja_local"] is True
    assert r.info["region_id"] == 29 and r.info["regiao_brasil"] is True
    assert r.info["expira_em_texto"] and "Brasília" in r.info["expira_em_texto"]
    assert "bg.local.goods.stock.edit" in r.info["escopos"]
    assert r.info["escopos_faltando"] == []
    tudo = json.dumps(r.info) + (r.detail or "")
    assert "NUNCA-DEVOLVER" not in tudo and TOKEN not in tudo and SEGREDO not in tudo


@pytest.mark.parametrize(
    ("extra", "trecho"),
    [
        ({"mallType": 1}, "LOCAL"),
        ({"regionId": 211}, "Brasil"),
        ({"expiredTime": int(time.time()) - 10}, "venceu"),
    ],
)
def test_testar_conexao_recusa_loja_errada_ou_token_vencido(temu_http, extra, trecho):
    temu_http(lambda corpo: _info(**extra))
    r = asyncio.run(TemuClient(CREDS).test_connection())
    assert not r.ok
    assert trecho in r.detail


def test_testar_conexao_diz_qual_permissao_falta(temu_http):
    temu_http(lambda corpo: _info(apiScopeList=["bg.open.accesstoken.info.get"]))
    r = asyncio.run(TemuClient(CREDS).test_connection())
    assert r.info["escopos_faltando"] == [
        "temu.local.sku.list.retrieve",
        "bg.local.goods.stock.edit",
    ]
    assert "bg.local.goods.stock.edit" in r.detail and "Product Management" in r.detail


def test_testar_conexao_com_ip_fora_da_lista(temu_http):
    temu_http(lambda corpo: {"success": False, "errorCode": 5000003, "errorMsg": "NOT_IN_IP"})
    r = asyncio.run(TemuClient(CREDS).test_connection())
    assert not r.ok and "IP" in r.detail


def test_testar_conexao_sem_credencial_nao_chama(temu_http):
    falsa = temu_http()
    r = asyncio.run(TemuClient({"app_key": "a"}).test_connection())
    assert not r.ok and "access_token" in r.detail
    assert falsa.pedidos == []


# ---------------------------------------------------------------- estoque


def test_estoque_com_trava_desligada_nao_sai(temu_http):
    falsa = temu_http()
    r = asyncio.run(TemuClient(CREDS).update_stock(_link(), 5))
    assert r.status == SyncStatus.SKIPPED
    assert r.error_code == "temu_escrita_desligada"
    assert "Integrações" in r.error_detail
    assert falsa.pedidos == []


def test_estoque_absoluto_por_goods_e_sku(temu_http):
    falsa = temu_http(_ok_estoque)
    r = asyncio.run(TemuClient({**CREDS, STOCK_WRITE_FLAG: True}).update_stock(_link(), 7))
    assert r.status == SyncStatus.OK and r.qty_after == 7 and r.qty_before == 3
    assert falsa.tipos() == ["bg.local.goods.stock.edit"]
    corpo = falsa.pedidos[0]
    assert corpo["goodsId"] == int(GOODS)
    assert corpo["stockType"] == 0
    assert corpo["skuStockTargetList"] == [{"skuId": int(SKU_ID), "stockTarget": 7}]
    assert "skuStockChangeList" not in corpo


@pytest.mark.parametrize(("qty", "enviado"), [(-4, 0), (0, 0), (2_000_000, 1_000_000)])
def test_estoque_fica_na_faixa_da_temu(temu_http, qty, enviado):
    falsa = temu_http(_ok_estoque)
    r = asyncio.run(TemuClient({**CREDS, STOCK_WRITE_FLAG: True}).update_stock(_link(), qty))
    assert r.status == SyncStatus.OK
    assert falsa.pedidos[0]["skuStockTargetList"][0]["stockTarget"] == enviado


@pytest.mark.parametrize(
    ("resultado", "status", "codigo"),
    [
        (
            {
                "operateResult": True,
                "skuStockEditStatusInfoList": [
                    {
                        "skuId": int(SKU_ID),
                        "stockEditStatus": False,
                        "errorCode": 150013002,
                        "errorMsg": "Quantity must be between",
                    }
                ],
            },
            SyncStatus.FATAL,
            "temu_150013002",
        ),
        (
            {
                "operateResult": True,
                "skuStockEditStatusInfoList": [
                    {
                        "skuId": int(SKU_ID),
                        "stockEditStatus": False,
                        "errorCode": 150010005,
                        "errorMsg": "Try again later",
                    }
                ],
            },
            SyncStatus.RETRYABLE,
            "temu_150010005",
        ),
        ({"operateResult": False, "msg": "falhou"}, SyncStatus.FATAL, "temu_sku_recusado"),
        ({}, SyncStatus.FATAL, "temu_sku_recusado"),
        ({"operateResult": True}, SyncStatus.OK, None),
    ],
)
def test_envelope_de_sucesso_com_sku_recusado_e_falha(temu_http, resultado, status, codigo):
    temu_http(lambda corpo: {"success": True, "errorCode": 1000000, "result": resultado})
    r = asyncio.run(TemuClient({**CREDS, STOCK_WRITE_FLAG: True}).update_stock(_link(), 1))
    assert r.status == status
    assert r.error_code == codigo


def test_estoque_erro_de_conta_nao_e_fatal_do_vinculo(temu_http):
    temu_http(lambda corpo: {"success": False, "errorCode": 3000034, "errorMsg": "expired"})
    r = asyncio.run(TemuClient({**CREDS, STOCK_WRITE_FLAG: True}).update_stock(_link(), 1))
    assert r.status == SyncStatus.SKIPPED
    assert r.error_code == "temu_conta_3000034"
    assert r.payload == {"erro_de_conta": True}


def test_estoque_limite_de_chamadas_tenta_de_novo(temu_http):
    temu_http(lambda corpo: {"success": False, "errorCode": 4000004, "errorMsg": "RATE"})
    r = asyncio.run(TemuClient({**CREDS, STOCK_WRITE_FLAG: True}).update_stock(_link(), 1))
    assert r.status == SyncStatus.RETRYABLE


@pytest.mark.parametrize(
    "link", [_link(variation_id=None), _link(external_id="abc"), _link("", "")]
)
def test_vinculo_sem_ids_da_temu_nao_chama(temu_http, link):
    falsa = temu_http()
    r = asyncio.run(TemuClient({**CREDS, STOCK_WRITE_FLAG: True}).update_stock(link, 1))
    assert r.status == SyncStatus.FATAL and r.error_code == "temu_ids_invalidos"
    assert falsa.pedidos == []


# ---------------------------------------------------------------- preço


def test_preco_da_temu_nao_sai(temu_http):
    falsa = temu_http()
    r = asyncio.run(
        TemuClient({**CREDS, STOCK_WRITE_FLAG: True}).update_price(link=_link(), price=49.9)
    )
    assert r.status == SyncStatus.SKIPPED
    assert r.error_code == "temu_preco_nao_liberado"
    assert "preço da Temu ainda não liberado" in r.error_detail
    r = asyncio.run(
        _dispatch_price_update_link(
            TemuClient({**CREDS, STOCK_WRITE_FLAG: True}),
            IntegrationPlatform.TEMU,
            _link(),
            49.9,
        )
    )
    assert r.status == SyncStatus.SKIPPED and r.error_code == "temu_preco_nao_liberado"
    assert falsa.pedidos == []


# ---------------------------------------------------------------- anúncios


def _paginas_de_skus(corpo: dict) -> dict:
    tipo = corpo["skuSearchType"]
    token = corpo.get("pageToken")
    if tipo == "ACTIVE" and token is None:
        lista = [
            {
                "goodsId": GOODS,
                "skuId": SKU_ID,
                "outSkuSn": "dg053.ci",
                "goodsName": "Mala",
                "specName": "Azul",
                "stock": 4,
                "skuStatus": "Active",
            },
        ]
        return {"success": True, "result": {"skuList": lista, "pagination": {"nextToken": "p2"}}}
    if tipo == "ACTIVE" and token == "p2":
        lista = [
            {
                "goodsId": GOODS,
                "skuId": "17592352673535",
                "outSkuSn": "dg053.ra",
                "goodsName": "Mala",
                "specName": "Rosa",
                "stock": 0,
                "skuStatus": "Active",
            },
        ]
        return {"success": True, "result": {"skuList": lista, "pagination": {}}}
    if tipo == "INACTIVE":
        lista = [
            {
                "goodsId": "9001",
                "skuId": "9002",
                "outSkuSn": "",
                "goodsName": "Sem SKU",
                "stock": 1,
                "skuStatus": "InActive",
            }
        ]
        return {"success": True, "result": {"skuList": lista}}
    if tipo == "DELETED":
        lista = [
            {
                "goodsId": "7001",
                "skuId": "7002",
                "outSkuSn": "dg053.sp",
                "goodsName": "Velho",
                "stock": 0,
                "skuStatus": "Deleted",
            }
        ]
        return {"success": True, "result": {"skuList": lista}}
    return {"success": True, "result": {"skuList": []}}


async def _ler_tudo(cliente: TemuClient) -> list[dict]:
    return [x async for x in cliente.list_listings()]


def test_leitor_de_anuncios_um_registro_por_sku(temu_http):
    falsa = temu_http(_paginas_de_skus)
    itens = asyncio.run(_ler_tudo(TemuClient(CREDS)))
    assert set(falsa.tipos()) == {"temu.local.sku.list.retrieve"}
    assert [(p["skuSearchType"], p.get("pageToken")) for p in falsa.pedidos] == [
        ("ACTIVE", None),
        ("ACTIVE", "p2"),
        ("INACTIVE", None),
        ("INCOMPLETE", None),
        ("DELETED", None),
    ]
    assert all(p["pageSize"] == 100 for p in falsa.pedidos)
    primeiro = itens[0]
    assert primeiro["external_id"] == GOODS
    assert primeiro["variation_id"] == SKU_ID
    assert primeiro["sku"] == "dg053.ci"
    assert primeiro["stock"] == 4
    assert primeiro["status"] == "active"
    assert primeiro["title"] == "Mala — Azul"
    assert [i["status"] for i in itens] == ["active", "active", "paused", "closed"]
    assert itens[2]["sku"] is None


def test_status_do_sku(temu_http):
    f = temu_mod._temu_status_to_listing
    assert f("Active") == "active"
    assert f("InActive") == "paused"
    assert f("Incomplete") == "under_review"
    assert f("Deleted") == "closed"
    assert f("Draft") == "inactive"
    assert f(None) == "inactive"


# ---------------------------------------------------------------- formulário


def test_formulario_nao_injeta_a_trava_e_mantem_a_existente():
    for ruim in (
        {**CREDS, STOCK_WRITE_FLAG: True},
        {**CREDS, "_qualquer": "x"},
        {**CREDS, "app_key": 123},
    ):
        with pytest.raises(HTTPException) as e:
            _validated_temu_credentials(ruim)
        assert e.value.detail["code"] == "temu_credentials_invalid"
    with pytest.raises(HTTPException) as e:
        _validated_temu_credentials({**CREDS, "region": "br"})
    assert e.value.detail["code"] == "temu_region_invalid"
    existente = {**CREDS, STOCK_WRITE_FLAG: True}
    novo = _validated_temu_credentials(
        {"access_token": " novo ", "region": "GLOBAL"}, existing=existente
    )
    assert novo[STOCK_WRITE_FLAG] is True
    assert novo["access_token"] == "novo" and novo["region"] == "global"


# ---------------------------------------------------------------- banco: caminhos de envio


async def _conta_temu(db: AsyncSession, user: User, *, ligada: bool, nome: str = "barbosa"):
    creds = dict(CREDS)
    if ligada:
        creds[STOCK_WRITE_FLAG] = True
    i = Integration(
        user_id=user.id,
        platform=IntegrationPlatform.TEMU,
        name=f"{nome}-{uuid.uuid4().hex[:4]}",
        credentials=encrypt_json(creds),
    )
    db.add(i)
    await db.commit()
    await db.refresh(i)
    return i


async def _produto_com_vinculo(db, user, integ, *, stock=6, sku_id=SKU_ID):
    p = Product(
        user_id=user.id, sku=f"t-{uuid.uuid4().hex[:6]}", name="mala", stock=stock, situacao="A"
    )
    db.add(p)
    await db.flush()
    link = ProductLink(
        user_id=user.id,
        product_id=p.id,
        integration_id=integ.id,
        platform=IntegrationPlatform.TEMU,
        external_id=GOODS,
        variation_id=sku_id,
        external_sku=p.sku,
        stock=1,
        last_sync_status=LinkSyncStatus.OK,
    )
    db.add(link)
    await db.commit()
    await db.refresh(p)
    await db.refresh(link)
    return p, link


async def _alertas(db: AsyncSession) -> list[Alert]:
    return list((await db.execute(select(Alert))).scalars())


async def test_orquestrador_com_trava_desligada_nao_escreve(db, make_user, temu_http):
    falsa = temu_http()
    u = await make_user()
    integ = await _conta_temu(db, u, ligada=False)
    p, link = await _produto_com_vinculo(db, u, integ)

    report = await SyncOrchestrator(db, user_id=u.id, force=True).run([p])
    await db.commit()
    await db.refresh(link)

    assert falsa.pedidos == []
    assert report.skipped == 1 and report.fatal == 0
    assert link.last_sync_status == LinkSyncStatus.SKIPPED
    assert "temu_escrita_desligada" in link.last_error
    assert link.stock == 1
    assert await _alertas(db) == []


async def test_orquestrador_com_trava_ligada_envia_o_saldo(db, make_user, temu_http):
    falsa = temu_http(_ok_estoque)
    u = await make_user()
    integ = await _conta_temu(db, u, ligada=True)
    p, link = await _produto_com_vinculo(db, u, integ, stock=6)

    report = await SyncOrchestrator(db, user_id=u.id, force=True).run([p])
    await db.commit()
    await db.refresh(link)

    assert falsa.tipos() == ["bg.local.goods.stock.edit"]
    assert falsa.pedidos[0]["skuStockTargetList"] == [{"skuId": int(SKU_ID), "stockTarget": 6}]
    assert report.ok == 1
    assert link.last_sync_status == LinkSyncStatus.OK
    assert link.stock == 6


async def test_modo_ferias_segura_mesmo_com_trava_ligada(db, make_user, temu_http):
    falsa = temu_http(_ok_estoque)
    u = await make_user()
    integ = await _conta_temu(db, u, ligada=True)
    integ.vacation_mode = True
    await db.commit()
    p, link = await _produto_com_vinculo(db, u, integ)
    await SyncOrchestrator(db, user_id=u.id, force=True).run([p])
    assert falsa.pedidos == []


async def test_erro_de_conta_nao_mata_vinculos_e_gera_um_alerta(db, make_user, temu_http):
    temu_http(lambda corpo: {"success": False, "errorCode": 3000034, "errorMsg": "expired"})
    u = await make_user()
    integ = await _conta_temu(db, u, ligada=True)
    p1, l1 = await _produto_com_vinculo(db, u, integ)
    p2, l2 = await _produto_com_vinculo(db, u, integ, sku_id="17592352673536")

    report = await SyncOrchestrator(db, user_id=u.id, force=True).run([p1, p2])
    await db.commit()
    for link in (l1, l2):
        await db.refresh(link)
        assert link.last_sync_status == LinkSyncStatus.SKIPPED
        assert "temu_conta_3000034" in link.last_error
        assert link.morto_desde is None
    assert report.fatal == 0
    alertas = await _alertas(db)
    assert len(alertas) == 1
    assert alertas[0].dedupe_key.startswith(f"erro_conta:{integ.id}:temu_conta_3000034:")
    assert alertas[0].integration_id == integ.id
    assert TOKEN not in (alertas[0].message or "")


async def _job(db, u, tipo):
    job = BackgroundJob(type=tipo, status=BackgroundJobStatus.PENDING, created_by=u.id)
    db.add(job)
    await db.commit()
    return job


@pytest.mark.parametrize("ligada", [False, True])
async def test_webhook_do_bling_so_escreve_com_trava_ligada(db, make_user, temu_http, ligada):
    """sync_product_run = webhook de estoque do Bling, ingest de pedido,
    refresh, irmãos da família e o botão de sincronizar o produto."""
    falsa = temu_http(_ok_estoque)
    u = await make_user()
    integ = await _conta_temu(db, u, ligada=ligada)
    p, link = await _produto_com_vinculo(db, u, integ, stock=9)
    job = await _job(db, u, BackgroundJobType.SYNC_PRODUCT)

    await worker.sync_product_run({}, str(job.id), str(u.id), str(p.id), None)

    log = (
        (await db.execute(select(SyncLog).where(SyncLog.product_link_id == link.id)))
        .scalars()
        .one()
    )
    if ligada:
        assert falsa.tipos() == ["bg.local.goods.stock.edit"]
        assert falsa.pedidos[0]["skuStockTargetList"][0]["stockTarget"] == 9
        assert log.status == LinkSyncStatus.OK
    else:
        assert falsa.pedidos == []
        assert log.error_code == "temu_escrita_desligada"


@pytest.mark.parametrize("ligada", [False, True])
async def test_sincronizar_todos_so_escreve_com_trava_ligada(db, make_user, temu_http, ligada):
    """sync_all_run = sincronização noturna e o botão Sincronizar todos."""
    falsa = temu_http(_ok_estoque)
    u = await make_user()
    integ = await _conta_temu(db, u, ligada=ligada)
    p, link = await _produto_com_vinculo(db, u, integ, stock=2)
    job = await _job(db, u, BackgroundJobType.SYNC_ALL)

    # Com a lista de contas (o filtro da tela), como o botão manda; sem ela a
    # contagem usa o schema fixo `davinci`, que não existe no banco de teste.
    await worker.sync_all_run({}, str(job.id), str(u.id), [str(p.id)], True, [str(integ.id)], True)

    await db.refresh(link)
    if ligada:
        assert falsa.tipos() == ["bg.local.goods.stock.edit"]
        assert link.last_sync_status == LinkSyncStatus.OK
    else:
        assert falsa.pedidos == []
        assert link.last_sync_status == LinkSyncStatus.SKIPPED
        assert "temu_escrita_desligada" in (link.last_error or "")


async def test_vincular_cria_vinculo_por_sku_sem_escrever(db, make_user, temu_http):
    falsa = temu_http(_paginas_de_skus)
    u = await make_user()
    integ = await _conta_temu(db, u, ligada=True)  # nem ligada o Vincular escreve
    ci = Product(user_id=u.id, sku="dg053.ci", name="ci", stock=4, situacao="A")
    sp = Product(user_id=u.id, sku="dg053.sp", name="sp", stock=4, situacao="A")
    db.add_all([ci, sp])
    await db.commit()
    job = await _job(db, u, BackgroundJobType.AUTO_LINK)

    stats = await auto_link_mod._link_dispatch(db, job.id, integ)

    assert set(falsa.tipos()) == {"temu.local.sku.list.retrieve"}
    assert stats.get("error") is None
    links = (
        (await db.execute(select(ProductLink).where(ProductLink.integration_id == integ.id)))
        .scalars()
        .all()
    )
    assert [(lk.external_id, lk.variation_id, lk.external_sku, lk.product_id) for lk in links] == [
        (GOODS, SKU_ID, "dg053.ci", ci.id)
    ], "SKU excluído na Temu (dg053.sp) não vira vínculo; dg053.ra não tem produto"
    assert IntegrationPlatform.TEMU in auto_link_mod._ADAPTED_PLATFORMS


async def test_importar_anuncios_da_temu_so_le(db, make_user, temu_http):
    falsa = temu_http(_paginas_de_skus)
    u = await make_user()
    integ = await _conta_temu(db, u, ligada=True)
    job = await _job(db, u, BackgroundJobType.IMPORT_LISTINGS)
    assert isinstance(listings_import._client_for_listings(db, integ), TemuClient)

    await listings_import.run_import_listings(
        db, job_id=job.id, user_id=u.id, integration_id=integ.id
    )
    await db.refresh(job)
    assert job.status == BackgroundJobStatus.SUCCEEDED, job.error
    assert set(falsa.tipos()) == {"temu.local.sku.list.retrieve"}
    ids = {
        row.external_id
        for row in (
            await db.execute(select(Listing).where(Listing.integration_id == integ.id))
        ).scalars()
    }
    # Uma linha por produto (goodsId); o excluído que o DaVinci não conhecia
    # não vira linha nova na importação do botão de importar (que cria).
    assert GOODS in ids
    assert (
        await db.scalar(select(ProductLink.id).where(ProductLink.integration_id == integ.id))
        is None
    ), "importar a lista não cria vínculo da Temu (quem cria é o Vincular)"


# ---------------------------------------------------------------- endpoints


async def test_trava_so_admin_liga_e_formulario_nao_mexe(client, db, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    r = await client.post(
        "/api/integrations",
        json={"platform": "temu", "name": "barbosa", "credentials": {**CREDS, "region": "global"}},
    )
    assert r.status_code == 201, r.text
    iid = r.json()["id"]
    assert r.json()["temu_stock_write_enabled"] is False
    assert SEGREDO not in r.text and TOKEN not in r.text

    r = await client.post(
        "/api/integrations",
        json={"platform": "temu", "name": "x", "credentials": {**CREDS, STOCK_WRITE_FLAG: True}},
    )
    assert r.status_code == 400 and r.json()["detail"]["code"] == "temu_credentials_invalid"

    r = await client.get(f"/api/integrations/{iid}/temu-writes")
    assert r.status_code == 200
    assert r.json()["stock_write_enabled"] is False and r.json()["price_write_enabled"] is False

    r = await client.put(f"/api/integrations/{iid}/temu-writes", json={"stock_write_enabled": True})
    assert r.status_code == 200, r.text
    assert r.json()["stock_write_enabled"] is True
    integ = await db.get(Integration, uuid.UUID(iid))
    await db.refresh(integ)
    creds = decrypt_json(integ.credentials)
    assert creds[STOCK_WRITE_FLAG] is True and creds["app_secret"] == SEGREDO

    # Editar as credenciais não apaga a trava nem deixa injetar.
    r = await client.patch(
        f"/api/integrations/{iid}", json={"credentials": {STOCK_WRITE_FLAG: False}}
    )
    assert r.status_code == 400
    r = await client.patch(f"/api/integrations/{iid}", json={"credentials": {"access_token": "t2"}})
    assert r.status_code == 200, r.text
    assert r.json()["temu_stock_write_enabled"] is True
    assert r.json()["last_test_at"] is None

    r = await client.put(
        f"/api/integrations/{iid}/temu-writes", json={"stock_write_enabled": "sim"}
    )
    assert r.status_code == 422

    comum = await make_user(role=UserRole.USER, permissions={"integracoes": ["view", "edit"]})
    auth_as(comum)
    assert (await client.get(f"/api/integrations/{iid}/temu-writes")).status_code == 403
    r = await client.put(
        f"/api/integrations/{iid}/temu-writes", json={"stock_write_enabled": False}
    )
    assert r.status_code == 403


async def test_trava_so_existe_na_temu(client, db, make_user, auth_as):
    auth_as(await make_user(role=UserRole.ADMIN))
    ml = Integration(
        user_id=(await make_user()).id,
        platform=IntegrationPlatform.ML,
        name="ml",
        credentials=encrypt_json({}),
    )
    db.add(ml)
    await db.commit()
    r = await client.put(
        f"/api/integrations/{ml.id}/temu-writes", json={"stock_write_enabled": True}
    )
    assert r.status_code == 404


async def test_botao_testar_grava_validade_do_token(client, db, make_user, auth_as, temu_http):
    temu_http(lambda corpo: _info(expiredTime=1893456000))  # 2030-01-01
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    integ = await _conta_temu(db, admin, ligada=False)
    r = await client.post(f"/api/integrations/{integ.id}/test")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert "NUNCA-DEVOLVER" not in r.text and TOKEN not in r.text
    await db.refresh(integ)
    assert integ.token_expires_at is not None and integ.token_expires_at.year == 2030


async def test_importar_anuncios_pelo_botao_aceita_temu(
    client, db, make_user, auth_as, monkeypatch
):
    from app.routers import listings as lst

    class _Pool:
        async def enqueue_job(self, *a, **k):
            return SimpleNamespace(job_id="j1")

    async def _fake_pool():
        return _Pool()

    monkeypatch.setattr(lst, "get_arq_pool", _fake_pool)
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    integ = await _conta_temu(db, admin, ligada=False)
    r = await client.post("/api/listings/import", json={"integration_id": str(integ.id)})
    assert r.status_code == 201, r.text
