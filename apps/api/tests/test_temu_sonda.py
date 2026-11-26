"""Sonda da Open API da Temu (26/11/2026): só consultas e sem dado do comprador."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import temu_sonda
from app.services.marketplaces import temu as temu_mod
from app.services.marketplaces.temu import (
    _APIS_LEITURA,
    APIS_LEITURA_PEDIDOS,
    TemuClient,
    TemuErro,
    TemuEscritaBloqueada,
)

# Escritas que existem no app da Barbosa e nunca podem sair pela sonda.
ESCRITAS = {
    "temu.aftersales.refund.issue",
    "bg.aftersales.cancel.agree",
    "bg.logistics.shipment.create",
    "bg.logistics.shipment.confirm",
    "bg.logistics.shipment.v2.confirm",
    "bg.order.changeaddress.handle",
    "temu.order.cancel.appeal.apply",
}
# Consultas de endereço do comprador: fora de propósito.
ENDERECO = {"bg.order.shippinginfo.v2.get", "bg.order.decryptshippinginfo.get"}


def test_lista_so_tem_consultas():
    assert APIS_LEITURA_PEDIDOS <= _APIS_LEITURA
    assert not (APIS_LEITURA_PEDIDOS & ESCRITAS)
    assert not (APIS_LEITURA_PEDIDOS & ENDERECO)
    assert not (_APIS_LEITURA & (ESCRITAS | ENDERECO))


async def test_cliente_recusa_escrita_antes_de_sair(monkeypatch):
    def nao_pode_sair(*_a, **_k):
        raise AssertionError("não podia abrir conexão")

    monkeypatch.setattr(temu_mod.httpx, "AsyncClient", nao_pode_sair)
    cliente = TemuClient({"app_key": "k", "app_secret": "s", "access_token": "t"})
    for api in ESCRITAS | ENDERECO:
        with pytest.raises(TemuEscritaBloqueada):
            await cliente._request(api, {})


def test_oculta_dado_do_comprador():
    resposta = {
        "parentOrderSn": "PO-029-1",
        "receiptName": "Fulana",
        "buyerId": 123,
        "mobile": "+55 11 9",
        "email": "x@y",
        "receiptAddress": {"city": "Diamantina", "street": "Rua A"},
        "addressLine1": "Rua A, 10",
        "orderList": [{"goodsName": "Mala M2", "skuId": 9, "name": "Fulana"}],
        "vazio": None,
        "receiptPhone": "",
    }
    out = temu_sonda.ocultar_pessoal(resposta)
    assert out["parentOrderSn"] == "PO-029-1"
    assert out["receiptName"] == temu_sonda.OCULTO
    assert out["buyerId"] == temu_sonda.OCULTO
    assert out["mobile"] == temu_sonda.OCULTO
    assert out["email"] == temu_sonda.OCULTO
    assert out["receiptAddress"] == temu_sonda.OCULTO  # objeto inteiro
    assert out["addressLine1"] == temu_sonda.OCULTO
    assert out["orderList"][0] == {"goodsName": "Mala M2", "skuId": 9, "name": temu_sonda.OCULTO}
    assert out["vazio"] is None
    assert out["receiptPhone"] == ""  # vazio continua vazio
    assert resposta["receiptName"] == "Fulana"  # não mexe no original


def test_rastreio_mantem_o_local_do_evento_mas_nao_o_do_comprador():
    resposta = {
        "trackList": [{"eventDesc": "chegou ao ponto de entrega", "eventAddress": "Rio Negro/SC"}],
        "receiverAddress": "Rua A, 10",
    }
    out = temu_sonda.ocultar_pessoal(resposta, manter_endereco=True)
    assert out["trackList"][0]["eventAddress"] == "Rio Negro/SC"
    assert out["receiverAddress"] == temu_sonda.OCULTO


async def test_sonda_recusa_api_fora_da_lista():
    for api in ("temu.aftersales.refund.issue", "bg.order.shippinginfo.v2.get", ""):
        with pytest.raises(temu_sonda.SondaRecusada):
            await temu_sonda.sondar(None, api, {})  # type: ignore[arg-type]


async def test_sonda_chama_e_oculta(monkeypatch):
    integ = SimpleNamespace(name="Barbosa", credentials=b"x")

    async def achar(_session, _iid):
        return integ

    chamadas = []

    async def chamar(self, api, params):
        chamadas.append((api, params))
        return {"success": True, "result": {"parentOrderSn": "PO-1", "receiptName": "Fulana"}}

    monkeypatch.setattr(temu_sonda, "_integracao", achar)
    monkeypatch.setattr(temu_sonda, "decrypt_json", lambda _b: {"app_key": "k"})
    monkeypatch.setattr(TemuClient, "_chamar", chamar)
    out = await temu_sonda.sondar(None, "bg.order.detail.v2.get", {"parentOrderSn": "PO-1"})  # type: ignore[arg-type]
    assert chamadas == [("bg.order.detail.v2.get", {"parentOrderSn": "PO-1"})]
    assert out == {
        "integracao": "Barbosa",
        "api": "bg.order.detail.v2.get",
        "ok": True,
        "resposta": {"parentOrderSn": "PO-1", "receiptName": temu_sonda.OCULTO},
    }


async def test_sonda_devolve_erro_da_temu_sem_estourar(monkeypatch):
    async def achar(_session, _iid):
        return SimpleNamespace(name="Barbosa", credentials=b"x")

    async def chamar(self, api, params):
        raise TemuErro("Temu 7000016: sem permissão", codigo=7000016, classe="conta")

    monkeypatch.setattr(temu_sonda, "_integracao", achar)
    monkeypatch.setattr(temu_sonda, "decrypt_json", lambda _b: {})
    monkeypatch.setattr(TemuClient, "_chamar", chamar)
    out = await temu_sonda.sondar(None, "temu.track.trackinginfo.get", {})  # type: ignore[arg-type]
    assert out["ok"] is False
    assert "sem permissão" in out["erro"]
