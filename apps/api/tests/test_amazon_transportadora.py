"""Amazon envio próprio: "MelhorEnvio" → "Correios" (16/11/2026, Eduardo).

A integração do Bling confirma o envio próprio com a transportadora
"MelhorEnvio", que a Amazon não reconhece. O job reconfirma o MESMO pacote com
"Correios". O que é garantido (cliente SP-API falso, sem rede nem banco):
- a troca manda o corpo EXATO que funcionou à mão em 16/11 e relê o pacote;
- já "Correios" → nada (e a rodada seguinte nem relê o pacote);
- Easy Ship (DBA) e AFN → nada, nem a leitura do pacote;
- rastreio que não é dos Correios → nada;
- pacote despachado há 5 dias → nada (só as últimas 72 h — caso 306097);
- 429 espera e segue; 429 que não passa pula só aquele pedido;
- 2 pacotes → não mexe;
- erro numa conta (ou num pedido) não para as outras;
- itens do pacote diferentes dos do pedido, serviço vazio → não mexe;
- teto de chamadas encerra a rodada; interruptor desligado = no-op;
- no máximo 2 confirmações por pedido, 1 h entre elas (Amazon que aceita e não
  reflete, recusa 400, Bling que reconfirma "MelhorEnvio" por cima); 429 no
  POST e teto antes do POST não contam;
- fora do servidor (ENV≠production) = no-op.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from structlog.testing import capture_logs

from app.services import amazon_transportadora as svc

BR = "A2Q3Y263D00KWC"
AGORA = datetime(2026, 11, 16, 22, 0, tzinfo=UTC)
PEDIDO = "701-3967231-6921832"


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _ordem(pedido: str = PEDIDO, **extra) -> dict:
    o = {
        "AmazonOrderId": pedido,
        "OrderStatus": "Shipped",
        "FulfillmentChannel": "MFN",
        "ShipServiceLevel": "Std Dom 9",
        "EasyShipShipmentStatus": None,
        "LastUpdateDate": "2026-11-16T12:00:30Z",
    }
    o.update(extra)
    return o


def _pacote(**extra) -> dict:
    p = {
        "packageReferenceId": "1",
        "carrier": "MelhorEnvio",
        "trackingNumber": "AD912266053BR",
        "shippingService": "SEDEX",
        "shipTime": _iso(AGORA - timedelta(hours=10)),
        "createdTime": _iso(AGORA - timedelta(hours=10)),
        "packageStatus": {"status": "SHIPPED"},
    }
    p.update(extra)
    return p


class FakeAmazon:
    """SP-API falsa: v0 getOrders, v2026 getOrder (PACKAGES), v0 orderItems e
    v0 confirmShipment. O POST bem-sucedido troca a transportadora do pacote,
    como a Amazon fez em 16/11 (a releitura vê "Correios")."""

    def __init__(
        self,
        *,
        ordens: list[dict],
        pacotes: dict[str, list[dict]],
        itens: dict[str, list[dict]] | None = None,
        marketplace_id: str = BR,
        erros: dict[tuple[str, str], list] | None = None,
        reflete: bool = True,
    ):
        self.marketplace_id = marketplace_id
        # False = a Amazon responde 204 e o pacote continua "MelhorEnvio".
        self.reflete = reflete
        self.ordens = ordens
        self.pacotes = pacotes
        self.itens = itens or {}
        # (método, caminho) → fila de respostas/exceções que vêm ANTES da normal.
        self.erros = {k: list(v) for k, v in (erros or {}).items()}
        self.chamadas: list[tuple[str, str, dict | None, object]] = []

    def posts(self) -> list[tuple[str, object]]:
        return [(c[1], c[3]) for c in self.chamadas if c[0] == "POST"]

    def leituras_de_pacote(self) -> list[str]:
        return [c[1] for c in self.chamadas if c[1].startswith("/orders/2026-01-01/")]

    async def _request(self, method, path, *, params=None, json=None):
        self.chamadas.append((method, path, params, json))
        fila = self.erros.get((method, path))
        if fila:
            prox = fila.pop(0)
            if isinstance(prox, Exception):
                raise prox
            return prox
        if method == "GET" and path == "/orders/v0/orders":
            return httpx.Response(200, json={"payload": {"Orders": self.ordens}})
        if method == "GET" and path.startswith("/orders/2026-01-01/orders/"):
            pid = path.rsplit("/", 1)[1]
            return httpx.Response(
                200, json={"order": {"orderId": pid, "packages": self.pacotes.get(pid, [])}}
            )
        if method == "GET" and path.endswith("/orderItems"):
            pid = path.split("/")[4]
            itens = self.itens.get(pid, [{"OrderItemId": "11122233344455", "QuantityOrdered": 1}])
            return httpx.Response(
                200, json={"payload": {"AmazonOrderId": pid, "OrderItems": itens}}
            )
        if method == "POST" and path.endswith("/shipmentConfirmation"):
            pid = path.split("/")[4]
            if self.reflete:
                for p in self.pacotes.get(pid, []):
                    p["carrier"] = json["packageDetail"]["carrierName"]
            return httpx.Response(204)
        return httpx.Response(404, json={"errors": [{"code": "NotFound"}]})


class Relogio:
    def __init__(self):
        self.esperas: list[float] = []

    async def __call__(self, s: float) -> None:
        self.esperas.append(s)


async def _rodar(contas, **kw):
    kw.setdefault("cache", {})
    kw.setdefault("tentativas", {})
    kw.setdefault("dormir", Relogio())
    kw.setdefault("agora", AGORA)
    return await svc.processar(contas, **kw)


# ── a troca ───────────────────────────────────────────────────────────────


async def test_troca_manda_o_corpo_exato_e_rele():
    amz = FakeAmazon(ordens=[_ordem()], pacotes={PEDIDO: [_pacote()]})

    async def _bling(pedido: str) -> str:
        assert pedido == PEDIDO
        return "309180"

    with capture_logs() as logs:
        resumo = await _rodar([("kia", amz)], numero_bling=_bling)

    assert amz.posts() == [(
        f"/orders/v0/orders/{PEDIDO}/shipmentConfirmation",
        {
            "marketplaceId": BR,
            "packageDetail": {
                "packageReferenceId": "1",
                "carrierCode": "Correios",
                "carrierName": "Correios",
                "shippingMethod": "SEDEX",
                "trackingNumber": "AD912266053BR",
                "shipDate": _iso(AGORA - timedelta(hours=10)),
                "orderItems": [{"orderItemId": "11122233344455", "quantity": 1}],
            },
        },
    )]
    assert resumo["trocados"] == 1 and resumo["contas_ok"] == ["kia"]
    # Listagem: pedidos MFN "Shipped" alterados nos últimos 2 dias.
    listar = amz.chamadas[0]
    assert listar[:2] == ("GET", "/orders/v0/orders")
    assert listar[2] == {
        "MarketplaceIds": BR,
        "LastUpdatedAfter": "2026-11-14T22:00:00Z",
        "OrderStatuses": "Shipped",
        "FulfillmentChannels": "MFN",
        "MaxResultsPerPage": 100,
    }
    # Releu o pacote depois do POST e logou o resultado.
    assert len(amz.leituras_de_pacote()) == 2
    trocou = [e for e in logs if e["event"] == "amazon_transportadora_correios_trocou"]
    assert trocou and trocou[0]["pedido"] == PEDIDO and trocou[0]["conta"] == "kia"
    assert trocou[0]["rastreio"] == "AD912266053BR" and trocou[0]["pedido_bling"] == "309180"
    assert trocou[0]["http"] == 204
    conferido = [e for e in logs if e["event"] == "amazon_transportadora_correios_conferido"]
    assert conferido and conferido[0]["transportadora_depois"] == "Correios"


async def test_transportadora_sem_diferenciar_maiusculas_e_servico_pac():
    amz = FakeAmazon(
        ordens=[_ordem()],
        pacotes={PEDIDO: [_pacote(carrier="MELHORENVIO", shippingService="PAC",
                                  trackingNumber="AE123456789BR")]},
    )
    resumo = await _rodar([("poofy", amz)])
    assert resumo["trocados"] == 1
    detalhe = amz.posts()[0][1]["packageDetail"]
    assert detalhe["shippingMethod"] == "PAC" and detalhe["trackingNumber"] == "AE123456789BR"


async def test_itens_com_quantidade_vao_iguais():
    amz = FakeAmazon(
        ordens=[_ordem()],
        pacotes={PEDIDO: [_pacote(packageItems=[
            {"orderItemId": "A1", "quantity": 2}, {"orderItemId": "B2", "quantity": 1},
        ])]},
        itens={PEDIDO: [
            {"OrderItemId": "A1", "QuantityOrdered": 2, "QuantityShipped": 2},
            {"OrderItemId": "B2", "QuantityOrdered": 1, "QuantityShipped": 1},
        ]},
    )
    await _rodar([("kfa", amz)])
    assert amz.posts()[0][1]["packageDetail"]["orderItems"] == [
        {"orderItemId": "A1", "quantity": 2}, {"orderItemId": "B2", "quantity": 1},
    ]


# ── o que NÃO pode ser tocado ─────────────────────────────────────────────


async def test_ja_correios_nao_faz_nada_e_nao_rele_na_rodada_seguinte():
    amz = FakeAmazon(ordens=[_ordem()], pacotes={PEDIDO: [_pacote(carrier="Correios")]})
    cache: dict[str, str] = {}
    r1 = await _rodar([("kia", amz)], cache=cache)
    assert amz.posts() == []
    assert r1["resultado"] == {"ja_correios": 1}
    r2 = await _rodar([("kia", amz)], cache=cache)
    assert r2["resultado"] == {"ja_conferido": 1}
    assert len(amz.leituras_de_pacote()) == 1
    # A Amazon mexeu no pedido (LastUpdateDate novo) → relê.
    amz.ordens = [_ordem(LastUpdateDate="2026-11-16T21:00:00Z")]
    await _rodar([("kia", amz)], cache=cache)
    assert len(amz.leituras_de_pacote()) == 2 and amz.posts() == []


async def test_depois_da_troca_a_rodada_seguinte_nao_troca_de_novo():
    amz = FakeAmazon(ordens=[_ordem()], pacotes={PEDIDO: [_pacote()]})
    cache: dict[str, str] = {}
    await _rodar([("kia", amz)], cache=cache)
    resumo = await _rodar([("kia", amz)], cache=cache)
    assert len(amz.posts()) == 1
    assert resumo["resultado"] == {"ja_correios": 1}


async def test_easyship_nao_faz_nada():
    amz = FakeAmazon(
        ordens=[_ordem(EasyShipShipmentStatus="Delivered")], pacotes={PEDIDO: [_pacote()]}
    )
    resumo = await _rodar([("kia", amz)])
    assert amz.posts() == [] and amz.leituras_de_pacote() == []
    assert resumo["resultado"] == {"easyship": 1}


async def test_afn_nao_faz_nada():
    amz = FakeAmazon(ordens=[_ordem(FulfillmentChannel="AFN")], pacotes={PEDIDO: [_pacote()]})
    resumo = await _rodar([("kia", amz)])
    assert amz.posts() == [] and amz.leituras_de_pacote() == []
    assert resumo["resultado"] == {"afn": 1}


@pytest.mark.parametrize("rastreio", ["123456789012", "ad912266053BR", "AD91226605BR", "", None])
async def test_rastreio_que_nao_e_dos_correios_nao_faz_nada(rastreio):
    amz = FakeAmazon(ordens=[_ordem()], pacotes={PEDIDO: [_pacote(trackingNumber=rastreio)]})
    resumo = await _rodar([("kia", amz)])
    assert amz.posts() == []
    assert resumo["resultado"] == {"rastreio_nao_correios": 1}


async def test_outra_transportadora_nao_faz_nada():
    amz = FakeAmazon(ordens=[_ordem()], pacotes={PEDIDO: [_pacote(carrier="Jadlog")]})
    resumo = await _rodar([("kia", amz)])
    assert amz.posts() == []
    assert resumo["resultado"] == {"outra_transportadora": 1}


async def test_pacote_despachado_ha_5_dias_nao_faz_nada():
    """306097 (A a Z aberta): LastUpdate de hoje, envio antigo — fora."""
    amz = FakeAmazon(
        ordens=[_ordem(LastUpdateDate=_iso(AGORA - timedelta(hours=1)))],
        pacotes={PEDIDO: [_pacote(shipTime=_iso(AGORA - timedelta(days=5)))]},
    )
    resumo = await _rodar([("kia", amz)])
    assert amz.posts() == []
    assert resumo["resultado"] == {"fora_da_janela": 1}


async def test_limite_das_72_horas():
    dentro = "701-0000000-0000001"
    fora = "701-0000000-0000002"
    amz = FakeAmazon(
        ordens=[_ordem(dentro), _ordem(fora)],
        pacotes={
            dentro: [_pacote(shipTime=_iso(AGORA - timedelta(hours=71, minutes=59)))],
            fora: [_pacote(shipTime=_iso(AGORA - timedelta(hours=72, minutes=1)))],
        },
    )
    await _rodar([("kia", amz)])
    assert [p for p, _ in amz.posts()] == [f"/orders/v0/orders/{dentro}/shipmentConfirmation"]


async def test_sem_data_de_envio_nao_faz_nada():
    amz = FakeAmazon(ordens=[_ordem()], pacotes={PEDIDO: [_pacote(shipTime=None)]})
    resumo = await _rodar([("kia", amz)])
    assert amz.posts() == [] and resumo["resultado"] == {"dados_incompletos": 1}


async def test_dois_pacotes_nao_mexe_e_loga():
    amz = FakeAmazon(
        ordens=[_ordem()],
        pacotes={PEDIDO: [
            _pacote(),
            _pacote(packageReferenceId="2", trackingNumber="AD912266054BR"),
        ]},
    )
    with capture_logs() as logs:
        resumo = await _rodar([("kia", amz)])
    assert amz.posts() == []
    assert resumo["resultado"] == {"varios_pacotes": 1}
    aviso = [e for e in logs if e["event"] == "amazon_transportadora_varios_pacotes"]
    assert aviso and aviso[0]["pedido"] == PEDIDO and aviso[0]["pacotes"] == 2


async def test_servico_ou_pacote_vazio_nao_mexe():
    sem_servico = "701-0000000-0000003"
    sem_ref = "701-0000000-0000004"
    amz = FakeAmazon(
        ordens=[_ordem(sem_servico), _ordem(sem_ref)],
        pacotes={
            sem_servico: [_pacote(shippingService="")],
            sem_ref: [_pacote(packageReferenceId=None)],
        },
    )
    resumo = await _rodar([("kia", amz)])
    assert amz.posts() == []
    assert resumo["resultado"] == {"dados_incompletos": 2}


async def test_itens_do_pacote_diferentes_do_pedido_nao_mexe():
    amz = FakeAmazon(
        ordens=[_ordem()],
        pacotes={PEDIDO: [_pacote(packageItems=[{"orderItemId": "A1", "quantity": 1}])]},
        itens={PEDIDO: [{"OrderItemId": "A1", "QuantityOrdered": 2}]},
    )
    resumo = await _rodar([("kia", amz)])
    assert amz.posts() == [] and resumo["resultado"] == {"itens_divergentes": 1}


async def test_item_enviado_parcial_nao_mexe():
    amz = FakeAmazon(
        ordens=[_ordem()],
        pacotes={PEDIDO: [_pacote()]},
        itens={PEDIDO: [{"OrderItemId": "A1", "QuantityOrdered": 2, "QuantityShipped": 1}]},
    )
    resumo = await _rodar([("kia", amz)])
    assert amz.posts() == [] and resumo["resultado"] == {"itens_incertos": 1}


async def test_conta_fora_do_marketplace_br_nao_mexe():
    amz = FakeAmazon(
        ordens=[_ordem()], pacotes={PEDIDO: [_pacote()]}, marketplace_id="ATVPDKIKX0DER"
    )
    resumo = await _rodar([("us", amz)])
    assert amz.chamadas == []
    assert resumo["resultado"] == {"conta_fora_do_br": 1}


# ── 429, tetos e falhas ───────────────────────────────────────────────────


async def test_429_espera_e_segue():
    caminho = f"/orders/2026-01-01/orders/{PEDIDO}"
    amz = FakeAmazon(
        ordens=[_ordem()],
        pacotes={PEDIDO: [_pacote()]},
        erros={
            ("GET", "/orders/v0/orders"): [httpx.Response(429)],
            ("GET", caminho): [httpx.Response(429)],
        },
    )
    relogio = Relogio()
    resumo = await _rodar([("kia", amz)], dormir=relogio)
    assert resumo["trocados"] == 1
    assert relogio.esperas.count(svc.ESPERA_429_S) == 2


async def test_429_que_nao_passa_pula_so_aquele_pedido():
    preso = "701-0000000-0000005"
    amz = FakeAmazon(
        ordens=[_ordem(preso), _ordem()],
        pacotes={preso: [_pacote()], PEDIDO: [_pacote()]},
        erros={("GET", f"/orders/2026-01-01/orders/{preso}"): [httpx.Response(429)] * 10},
    )
    relogio = Relogio()
    resumo = await _rodar([("kia", amz)], dormir=relogio)
    assert [p for p, _ in amz.posts()] == [f"/orders/v0/orders/{PEDIDO}/shipmentConfirmation"]
    assert resumo["resultado"] == {"sem_resposta": 1, "trocado": 1}
    assert relogio.esperas[:3] == [2.0, 4.0, 6.0]


async def test_erro_numa_conta_nao_para_as_outras():
    quebrada = FakeAmazon(
        ordens=[_ordem()], pacotes={PEDIDO: [_pacote()]},
        erros={("GET", "/orders/v0/orders"): [httpx.ConnectError("caiu")]},
    )
    sem_acesso = FakeAmazon(
        ordens=[_ordem()], pacotes={PEDIDO: [_pacote()]},
        erros={("GET", "/orders/v0/orders"): [httpx.Response(403, json={"errors": []})]},
    )
    boa = FakeAmazon(ordens=[_ordem()], pacotes={PEDIDO: [_pacote()]})
    resumo = await _rodar([("kia", quebrada), ("kfa", sem_acesso), ("poofy", boa)])
    assert resumo["contas_erro"] == ["kia", "kfa"]
    assert resumo["contas_ok"] == ["poofy"]
    assert quebrada.posts() == [] and sem_acesso.posts() == []
    assert len(boa.posts()) == 1


async def test_erro_num_pedido_nao_para_os_outros():
    ruim = "701-0000000-0000006"
    amz = FakeAmazon(
        ordens=[_ordem(ruim), _ordem()],
        pacotes={ruim: [_pacote()], PEDIDO: [_pacote()]},
        erros={("GET", f"/orders/v0/orders/{ruim}/orderItems"): [httpx.ReadTimeout("lento")]},
    )
    resumo = await _rodar([("kia", amz)])
    assert resumo["resultado"] == {"erro": 1, "trocado": 1}
    assert [p for p, _ in amz.posts()] == [f"/orders/v0/orders/{PEDIDO}/shipmentConfirmation"]


async def test_confirmacao_recusada_loga_erro_e_segue():
    outro = "701-0000000-0000007"
    amz = FakeAmazon(
        ordens=[_ordem(), _ordem(outro)],
        pacotes={PEDIDO: [_pacote()], outro: [_pacote()]},
        erros={("POST", f"/orders/v0/orders/{PEDIDO}/shipmentConfirmation"): [
            httpx.Response(400, json={"errors": [{"code": "InvalidInput"}]})
        ]},
    )
    with capture_logs() as logs:
        resumo = await _rodar([("kia", amz)])
    assert resumo["resultado"] == {"erro_confirmacao": 1, "trocado": 1}
    falhou = [e for e in logs if e["event"] == "amazon_transportadora_correios_falhou"]
    assert falhou and falhou[0]["pedido"] == PEDIDO and falhou[0]["http"] == 400


async def test_teto_de_chamadas_encerra_a_rodada():
    ordens = [_ordem(f"701-0000000-00001{i:02d}") for i in range(10)]
    amz = FakeAmazon(
        ordens=ordens,
        pacotes={o["AmazonOrderId"]: [_pacote(carrier="Correios")] for o in ordens},
    )
    vazia = FakeAmazon(ordens=[], pacotes={})
    resumo = await _rodar([("kia", amz), ("poofy", vazia)], max_chamadas=4)
    assert resumo["teto_de_chamadas"] is True
    assert resumo["chamadas"] == 4 and len(amz.chamadas) == 4


async def test_teto_de_trocas_por_rodada():
    ordens = [_ordem(f"701-0000000-00002{i:02d}") for i in range(3)]
    amz = FakeAmazon(ordens=ordens, pacotes={o["AmazonOrderId"]: [_pacote()] for o in ordens})
    resumo = await _rodar([("kia", amz)], max_trocas=2)
    assert len(amz.posts()) == 2
    assert resumo["resultado"] == {"trocado": 2, "teto_trocas": 1}


async def test_paginacao_manda_so_o_token():
    segunda = "701-0000000-0000008"
    amz = FakeAmazon(
        ordens=[_ordem(segunda)], pacotes={PEDIDO: [_pacote()], segunda: [_pacote()]},
        erros={("GET", "/orders/v0/orders"): [httpx.Response(
            200, json={"payload": {"Orders": [_ordem()], "NextToken": "T2"}}
        )]},
    )
    resumo = await _rodar([("kia", amz)])
    listagens = [c for c in amz.chamadas if c[1] == "/orders/v0/orders"]
    assert listagens[1][2] == {"MarketplaceIds": BR, "NextToken": "T2"}
    assert resumo["trocados"] == 2


# ── teto por pedido (revisão de 16/11) ────────────────────────────────────


async def _rodadas_de_15_min(amz, horas: int, **kw) -> list[dict]:
    """Rodadas de 15 em 15 min por `horas`, dividindo cache e tentativas."""
    kw.setdefault("cache", {})
    kw.setdefault("tentativas", {})
    out = []
    for minutos in range(0, horas * 60, 15):
        r = await _rodar([("kia", amz)], agora=AGORA + timedelta(minutes=minutos), **kw)
        out.append(r["resultado"])
    return out


async def test_amazon_aceita_e_nao_reflete_no_maximo_2_confirmacoes():
    """Antes: 1 POST a cada 15 min por até 72 h (~288 no mesmo pedido)."""
    amz = FakeAmazon(ordens=[_ordem()], pacotes={PEDIDO: [_pacote()]}, reflete=False)
    with capture_logs() as logs:
        resultados = await _rodadas_de_15_min(amz, horas=6)

    assert len(amz.posts()) == 2
    assert amz.posts()[0] == amz.posts()[1]  # o mesmo corpo
    # A 2ª só 1 h depois da 1ª; depois disso o pedido nem é relido.
    assert resultados[0] == {"trocado": 1}
    assert resultados[1:4] == [{"aguardando_nova_tentativa": 1}] * 3
    assert resultados[4] == {"trocado": 1}
    assert resultados[5:] == [{"teto_por_pedido": 1}] * (len(resultados) - 5)
    assert len(amz.leituras_de_pacote()) == 2 + 3 + 2
    nao_refletiu = [e for e in logs if e["event"] == "amazon_transportadora_correios_nao_refletiu"]
    assert [e["tentativa"] for e in nao_refletiu] == [1, 2]
    desistiu = [e for e in logs if e["event"] == "amazon_transportadora_desistiu"]
    assert len(desistiu) == 1
    assert desistiu[0]["pedido"] == PEDIDO and desistiu[0]["conta"] == "kia"
    assert desistiu[0]["motivo"] == "nao_refletiu" and desistiu[0]["tentativas"] == 2


async def test_reflete_atrasado_nao_gasta_a_2a_confirmacao():
    amz = FakeAmazon(ordens=[_ordem()], pacotes={PEDIDO: [_pacote()]}, reflete=False)
    tentativas: dict = {}
    r1 = await _rodar([("kia", amz)], tentativas=tentativas)
    # A Amazon grava "Correios" uns minutos depois da releitura.
    amz.pacotes[PEDIDO][0]["carrier"] = "Correios"
    r2 = await _rodar([("kia", amz)], tentativas=tentativas, agora=AGORA + timedelta(minutes=15))
    assert r1["resultado"] == {"trocado": 1} and r2["resultado"] == {"ja_correios": 1}
    assert len(amz.posts()) == 1


async def test_bling_reconfirma_melhorenvio_por_cima_no_maximo_2_confirmacoes():
    amz = FakeAmazon(ordens=[_ordem()], pacotes={PEDIDO: [_pacote()]})
    cache: dict = {}
    tentativas: dict = {}
    resultados = []
    for horas in range(4):
        # O Bling reconfirma o pacote com "MelhorEnvio" (LastUpdateDate novo).
        amz.pacotes[PEDIDO][0]["carrier"] = "MelhorEnvio"
        amz.ordens = [_ordem(LastUpdateDate=_iso(AGORA + timedelta(hours=horas)))]
        r = await _rodar(
            [("kia", amz)], cache=cache, tentativas=tentativas,
            agora=AGORA + timedelta(hours=horas, minutes=5),
        )
        resultados.append(r["resultado"])
    assert len(amz.posts()) == 2
    assert resultados == [
        {"trocado": 1}, {"trocado": 1}, {"teto_por_pedido": 1}, {"teto_por_pedido": 1},
    ]


async def test_confirmacao_recusada_nao_se_repete_a_cada_rodada():
    caminho = f"/orders/v0/orders/{PEDIDO}/shipmentConfirmation"
    amz = FakeAmazon(
        ordens=[_ordem()],
        pacotes={PEDIDO: [_pacote()]},
        erros={("POST", caminho): [
            httpx.Response(400, json={"errors": [{"code": "InvalidInput"}]})
        ] * 50},
    )
    with capture_logs() as logs:
        resultados = await _rodadas_de_15_min(amz, horas=3)
    assert len(amz.posts()) == 2
    assert resultados[0] == {"erro_confirmacao": 1}
    assert resultados[1:4] == [{"aguardando_nova_tentativa": 1}] * 3
    assert resultados[4] == {"erro_confirmacao": 1}
    assert resultados[5:] == [{"teto_por_pedido": 1}] * (len(resultados) - 5)
    desistiu = [e for e in logs if e["event"] == "amazon_transportadora_desistiu"]
    assert len(desistiu) == 1 and desistiu[0]["motivo"] == "recusada"


async def test_429_no_post_nao_conta_como_tentativa():
    caminho = f"/orders/v0/orders/{PEDIDO}/shipmentConfirmation"
    amz = FakeAmazon(
        ordens=[_ordem()],
        pacotes={PEDIDO: [_pacote()]},
        erros={("POST", caminho): [httpx.Response(429)] * (svc.TENTATIVAS_429 + 1)},
    )
    tentativas: dict = {}
    r1 = await _rodar([("kia", amz)], tentativas=tentativas)
    assert r1["resultado"] == {"confirmacao_429": 1} and tentativas == {}
    depois = AGORA + timedelta(minutes=15)
    r2 = await _rodar([("kia", amz)], tentativas=tentativas, agora=depois)
    assert r2["resultado"] == {"trocado": 1}
    assert tentativas == {PEDIDO: [depois]}


async def test_teto_de_chamadas_antes_do_post_nao_conta_como_tentativa():
    amz = FakeAmazon(ordens=[_ordem()], pacotes={PEDIDO: [_pacote()]})
    tentativas: dict = {}
    # listagem + pacote + itens = 3: o POST seria a 4ª chamada.
    r1 = await _rodar([("kia", amz)], tentativas=tentativas, max_chamadas=3)
    assert r1["teto_de_chamadas"] is True and amz.posts() == [] and tentativas == {}
    r2 = await _rodar([("kia", amz)], tentativas=tentativas)
    assert r2["resultado"] == {"trocado": 1} and len(tentativas[PEDIDO]) == 1


async def test_erro_de_rede_no_post_conta_como_tentativa():
    """A Amazon pode ter recebido: conta (o lado seguro)."""
    caminho = f"/orders/v0/orders/{PEDIDO}/shipmentConfirmation"
    amz = FakeAmazon(
        ordens=[_ordem()],
        pacotes={PEDIDO: [_pacote()]},
        erros={("POST", caminho): [httpx.ReadTimeout("lento")]},
    )
    tentativas: dict = {}
    r = await _rodar([("kia", amz)], tentativas=tentativas)
    assert r["resultado"] == {"erro": 1}
    assert tentativas == {PEDIDO: [AGORA]}


async def test_registro_de_tentativas_velho_e_esquecido():
    recente = "701-0000000-0000009"
    tentativas = {
        PEDIDO: [AGORA - timedelta(days=5)],
        recente: [AGORA - timedelta(hours=2)],
    }
    await _rodar([], tentativas=tentativas)
    assert list(tentativas) == [recente]


# ── interruptor e agenda ──────────────────────────────────────────────────


async def test_interruptor_desligado_nao_faz_nada(monkeypatch):
    monkeypatch.setattr(
        svc, "get_settings", lambda: SimpleNamespace(amazon_transportadora_correios=False)
    )
    assert await svc.rodada(None) == {"desligado": True}  # nem toca na sessão


async def test_fora_do_servidor_nao_faz_nada(monkeypatch):
    """Worker de dev (docker-compose.override) com credenciais da produção no
    banco local: não escreve na Amazon."""
    monkeypatch.setattr(
        svc, "get_settings",
        lambda: SimpleNamespace(amazon_transportadora_correios=True, is_prod=False),
    )
    assert await svc.rodada(None) == {"so_no_servidor": True}  # nem toca na sessão


def test_interruptor_ligado_de_fabrica():
    from app.config import Settings

    assert Settings.model_fields["amazon_transportadora_correios"].default is True


def test_freio_de_mao_pela_variavel_de_ambiente(monkeypatch):
    """O nome que o config.py manda pôr no .env é o que o Settings lê (no
    servidor o .env chega como variável do container — por isso recriar)."""
    from app.config import Settings

    monkeypatch.setenv("AMAZON_TRANSPORTADORA_CORREIOS", "false")
    assert Settings().amazon_transportadora_correios is False


def test_cron_a_cada_15_min():
    from app.worker import WorkerSettings

    crons = {c.name: c for c in WorkerSettings.cron_jobs}
    job = crons["cron:amazon_transportadora_correios"]
    assert job.minute == {11, 26, 41, 56}
    assert job.timeout_s is not None and job.timeout_s < 15 * 60
