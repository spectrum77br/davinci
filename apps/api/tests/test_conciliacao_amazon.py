"""Conciliação — Amazon: transações como extrato, saldo encadeado e reserva (passo 5, 15/10)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from httpx import Response
from sqlalchemy import select

from app.models import (
    BlingOrder,
    ConciliacaoLoja,
    ConciliacaoMovimento,
    ConciliacaoSync,
    Integration,
    StoreInfo,
)
from app.models.integration import IntegrationPlatform
from app.security.cipher import encrypt_json
from app.services.conciliacao_amazon import (
    ROTA_TRANSACOES,
    conta_amazon_de_quebras,
    quebras_da_transacao,
    run_amazon_sync,
    transacao_para_movimento,
)
from app.services.conciliacao_calculo import recalcular_loja

AGORA = datetime(2026, 10, 16, 12, tzinfo=UTC)
PROPRIO = "701-5936812-5103405"
DBA = "701-9769797-2045861"


def _b(tipo: str, valor: str, filhos: list | None = None) -> dict:
    return {
        "breakdownType": tipo,
        "breakdownAmount": {"currencyAmount": float(valor), "currencyCode": "BRL"},
        "breakdowns": filhos or [],
    }


def _tx(
    tid: str,
    tipo: str,
    status: str,
    posted: str,
    total: str,
    *,
    pedido: str | None = None,
    quebras=None,
    desc: str = "",
) -> dict:
    return {
        "transactionId": tid,
        "transactionType": tipo,
        "transactionStatus": status,
        "postedDate": posted,
        "description": desc,
        "totalAmount": {"currencyAmount": float(total), "currencyCode": "BRL"},
        "relatedIdentifiers": [
            {"relatedIdentifierName": "ORDER_ID", "relatedIdentifierValue": pedido}
        ]
        if pedido
        else [],
        "items": [{"description": "item", "breakdowns": quebras or []}],
        "breakdowns": [],
        "contexts": [],
    }


def _venda_proprio(status: str = "DEFERRED", posted: str = "2026-10-09T14:00:00Z") -> dict:
    """Kia 301635 (15/10): 2.598,00 + frete do cliente 94,81 − 336,60 = 2.356,21."""
    return _tx(
        "tx-proprio",
        "Shipment",
        status,
        posted,
        "2356.21",
        pedido=PROPRIO,
        quebras=[
            _b("ProductCharges", "2598", [_b("OurPricePrincipal", "2598")]),
            _b("Shipping", "94.81", [_b("ShippingPrincipal", "94.81")]),
            _b(
                "AmazonFees",
                "-336.60",
                [
                    _b("Commission", "-285.78", [_b("Base", "-253.62"), _b("Tax", "-32.16")]),
                    _b("AmazonForAllFee", "-40.39", [_b("Base", "-35.84"), _b("Tax", "-4.55")]),
                    _b("ShippingHB", "-10.43", [_b("Base", "-9.26"), _b("Tax", "-1.17")]),
                ],
            ),
        ],
    )


def _venda_dba(posted: str = "2026-10-10T14:00:00Z") -> dict:
    """Kia 301943 (15/10): 2.176,00 − promoção 108,80 − etiqueta 41,90 − 1,5% 31,01 = 1.994,29."""
    return _tx(
        "tx-dba",
        "Shipment",
        "RELEASED",
        posted,
        "1994.29",
        pedido=DBA,
        quebras=[
            _b("ProductCharges", "2176", [_b("OurPricePrincipal", "2176")]),
            _b("PromoRebates", "-108.80", [_b("OurPriceDiscount", "-108.80")]),
            _b(
                "AmazonFees",
                "-72.91",
                [
                    _b("MFNPostageFee", "-41.90", [_b("Base", "-37.19"), _b("Tax", "-4.71")]),
                    _b("AmazonForAllFee", "-31.01", [_b("Base", "-27.52"), _b("Tax", "-3.49")]),
                ],
            ),
        ],
    )


def _repasse(posted: str = "2026-10-10T18:00:00Z") -> dict:
    return _tx("tx-transfer", "Transfer", "RELEASED", posted, "1316.29", desc="Disbursement")


class _AmazonFalsa:
    def __init__(self, transacoes: list[dict], por_pagina: int = 100):
        self.transacoes = transacoes
        self.por_pagina = por_pagina
        self.chamadas: list[dict] = []
        self.marketplace_id = "A2Q3Y263D00KWC"

    async def _request(self, method: str, path: str, *, params=None, json=None):
        assert method == "GET" and path == ROTA_TRANSACOES
        self.chamadas.append(params or {})
        de = datetime.fromisoformat(params["postedAfter"])
        lista = [
            t
            for t in self.transacoes
            if datetime.fromisoformat(t["postedDate"].replace("Z", "+00:00")) >= de
        ]
        ini = int(params.get("nextToken") or 0)
        pagina = lista[ini : ini + self.por_pagina]
        prox = str(ini + self.por_pagina) if ini + self.por_pagina < len(lista) else None
        return Response(200, json={"transactions": pagina, "nextToken": prox})


async def _loja_amazon(db, make_user, *, inicio=date(2026, 10, 1)) -> ConciliacaoLoja:
    dono = await make_user()
    integ = Integration(
        user_id=dono.id,
        platform=IntegrationPlatform.AMAZON,
        name="kia",
        status="active",
        credentials=encrypt_json({"refresh_token": "rt", "client_id": "c", "client_secret": "s"}),
    )
    db.add(integ)
    await db.flush()
    db.add(
        StoreInfo(
            user_id=dono.id, platform="amazon", account_name="kia", bling_store_id="204000004"
        )
    )
    loja = ConciliacaoLoja(
        integration_id=integ.id, platform=IntegrationPlatform.AMAZON, conta="kia", inicio=inicio
    )
    db.add(loja)
    for n, bid, numeroloja, total in (
        ("301635", 8001, PROPRIO, "2598"),
        ("301943", 8002, DBA, "2176"),
    ):
        db.add(
            BlingOrder(
                bling_id=bid,
                numero=n,
                numeroloja=numeroloja,
                data=datetime(2026, 10, 5, 12, tzinfo=UTC),
                total=Decimal(total),
                totalprodutos=Decimal(total),
                situacao="Atendido",
                loja="204000004",
                item_index=0,
                preco_custo=1000.0,
                item_quantidade=1,
                item_codigo="wp53",
            )
        )
    await db.commit()
    return loja


def test_conta_amazon_de_quebras_fecha_como_a_tela():
    q = quebras_da_transacao(_venda_proprio())
    c = conta_amazon_de_quebras(q)
    assert c["produto"] == Decimal("2598.00") and c["frete_cliente"] == Decimal("94.81")
    assert c["comissao"] == Decimal("-285.78") and c["amazon_for_all"] == Decimal("-40.39")
    assert c["retencao_frete"] == Decimal("-10.43") and c["etiqueta"] == Decimal("0")
    assert c["tarifas"] == Decimal("-336.60") and c["outras_tarifas"] == Decimal("0")
    assert c["total"] == Decimal("2356.21") and c["outros"] == Decimal("0")
    d = conta_amazon_de_quebras(quebras_da_transacao(_venda_dba()))
    assert d["promocao"] == Decimal("-108.80") and d["etiqueta"] == Decimal("-41.90")
    assert d["total"] == Decimal("1994.29") and d["outros"] == Decimal("0")


def test_transacao_para_movimento_mapeia_tipos():
    m = transacao_para_movimento(_venda_proprio())
    assert m["tipo"] == "venda_liberada" and m["pedido_marketplace"] == PROPRIO
    assert m["credito"] == Decimal("2356.21") and m["debito"] == Decimal("0")
    assert m["sub_tipo"] == "DEFERRED" and "(preso na reserva)" in m["descricao"]
    assert m["bruto"] == Decimal("2598.00") and m["tarifa"] == Decimal("336.60")
    assert (
        m["frete"] == Decimal("-94.81") and m["cupom"] is None
    )  # frete cobrado do cliente = crédito
    assert m["data"] == datetime(2026, 10, 9, 14, tzinfo=UTC) and m["classificacao"] is None
    d = transacao_para_movimento(_venda_dba())
    assert (
        d["frete"] == Decimal("41.90")
        and d["cupom"] == Decimal("108.80")
        and d["tarifa"] == Decimal("72.91")
    )
    assert d["sub_tipo"] == "RELEASED" and "(preso" not in d["descricao"]
    r = transacao_para_movimento(_repasse())
    assert (
        r["tipo"] == "saque" and r["debito"] == Decimal("1316.29") and r["classificacao"] == "saque"
    )
    assert r["descricao"] == "Repasse pro banco"
    deb = transacao_para_movimento(
        _tx("tx-debt", "DebtRecovery", "RELEASED", "2026-10-07T10:00:00Z", "397.99")
    )
    assert (
        deb["tipo"] == "ajuste_amazon"
        and deb["credito"] == Decimal("397.99")
        and deb["classificacao"] == "ajuste"
    )
    ads = transacao_para_movimento(
        _tx("tx-ads", "ProductAdsPayment", "RELEASED", "2026-10-07T10:00:00Z", "-574.13")
    )
    assert (
        ads["tipo"] == "publicidade"
        and ads["debito"] == Decimal("574.13")
        and ads["classificacao"] == "publicidade"
    )
    est = transacao_para_movimento(
        _tx("tx-ref", "Refund", "DEFERRED", "2026-10-08T10:00:00Z", "-759.53", pedido=PROPRIO)
    )
    assert (
        est["tipo"] == "estorno"
        and est["debito"] == Decimal("759.53")
        and est["descricao"] == f"Estorno do pedido {PROPRIO}"
    )
    assert transacao_para_movimento({"transactionType": "Shipment"}) is None


async def test_run_amazon_sync_pagina_atualiza_situacao_e_encadeia_saldo(db, make_user):
    loja = await _loja_amazon(db, make_user)
    cliente = _AmazonFalsa([_venda_proprio(), _venda_dba(), _repasse()], por_pagina=2)
    r = await run_amazon_sync(db, loja, cliente, agora=AGORA)
    assert r["lidas"] == 3 and r["inseridos"] == 3 and r["atualizados"] == 0 and not r["erros"]
    assert len(cliente.chamadas) == 2 and cliente.chamadas[1].get("nextToken") == "2"  # paginou
    movs = (
        (await db.execute(select(ConciliacaoMovimento).order_by(ConciliacaoMovimento.data)))
        .scalars()
        .all()
    )
    assert [m.tipo for m in movs] == ["venda_liberada", "venda_liberada", "saque"]
    assert [m.saldo_apos for m in movs] == [
        Decimal("2356.21"),
        Decimal("4350.50"),
        Decimal("3034.21"),
    ]
    assert movs[0].pedido_bling == "301635" and movs[0].bling_id == 8001
    assert movs[1].pedido_bling == "301943" and movs[2].classificacao == "saque"
    assert r["saldo"] == 3034.21 and loja.ultimo_extrato_em is not None
    sync = (await db.execute(select(ConciliacaoSync))).scalar_one()
    assert sync.chave == "2026-10-16" and sync.status == "lido" and sync.linhas == 3
    # segunda rodada: o próprio saiu da reserva (RELEASED) → atualiza, não duplica
    cliente.transacoes[0] = _venda_proprio(status="RELEASED")
    r2 = await run_amazon_sync(db, loja, cliente, agora=AGORA)
    assert r2["inseridos"] == 0 and r2["atualizados"] == 1
    movs = (
        (await db.execute(select(ConciliacaoMovimento).order_by(ConciliacaoMovimento.data)))
        .scalars()
        .all()
    )
    assert movs[0].sub_tipo == "RELEASED" and "(preso" not in movs[0].descricao and len(movs) == 3


async def test_extrato_do_dia_e_dinheiro_preso_amazon(db, make_user, auth_as, client):
    loja = await _loja_amazon(db, make_user)
    cliente = _AmazonFalsa([_venda_proprio(), _venda_dba(), _repasse()])
    await run_amazon_sync(db, loja, cliente, agora=AGORA)
    await db.commit()
    auth_as(await make_user(permissions={"conciliacao": {"view": True}}))
    r = await client.get("/api/conciliacao/extrato", params={"loja": "kia", "dia": "2026-10-10"})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert [m["tipo"] for m in corpo["movimentos"]] == ["venda_liberada", "saque"]
    assert [m["status"] for m in corpo["movimentos"]] == ["automatico", "classificado"]
    t = corpo["totais"]
    assert t["saldo_inicial"] == 2356.21 and t["creditos"] == 1994.29 and t["debitos"] == 1316.29
    assert t["saldo_final"] == 3034.21 and t["saldo_informado"] == 3034.21 and t["diferenca"] == 0
    r = await client.get("/api/conciliacao/resumo")
    assert r.status_code == 200, r.text
    lk = next(x for x in r.json()["lojas"] if x["conta"] == "kia")
    # preso = o lançamento DEFERRED (2.356,21); na conta = saldo − preso; a liberar inclui o preso
    assert lk["dinheiro"]["preso"] == 2356.21 and lk["dinheiro"]["na_conta"] == 678.0
    assert lk["dinheiro"]["a_liberar"] == 2356.21


async def test_raiox_amazon_previsto_x_real_e_grade(db, make_user, auth_as, client):
    from app.models import MarketplaceOrderFinancial

    loja = await _loja_amazon(db, make_user)
    tx = _venda_proprio()
    cliente = _AmazonFalsa([tx])
    await run_amazon_sync(db, loja, cliente, agora=AGORA)
    db.add(
        MarketplaceOrderFinancial(
            integration_id=loja.integration_id,
            platform="amazon",
            external_order_id=PROPRIO,
            status="posted",
            fetched_at=datetime(2026, 10, 12, 12, tzinfo=UTC),
            gross_amount=Decimal("2598"),
            fee_amount=Decimal("336.60"),
            net_amount=Decimal("2356.21"),
            raw={"transactions": {"transactions": [tx]}},
        )
    )
    await db.commit()
    await recalcular_loja(db, loja, agora=AGORA)
    await db.commit()
    auth_as(await make_user(permissions={"conciliacao": {"view": True}}))
    r = await client.get("/api/conciliacao/pedidos", params={"loja": "kia", "q": "301635"})
    (ped,) = [p for p in r.json()["itens"] if p["pedido_bling"] == "301635"]
    assert ped["situacao"] == "bateu" and ped["recebido"] == 2356.21
    # Valor = produto; Tarifa = comissão + 1,5% + retenção; Frete = −frete cobrado do cliente
    assert ped["venda_valor"] == 2598 and ped["venda_tarifa"] == 336.6
    assert (
        ped["venda_frete"] == -94.81
        and ped["venda_extras"] == 0
        and ped["venda_liquido"] == 2356.21
    )
    r = await client.get("/api/conciliacao/pedidos/301635", params={"loja": "kia"})
    comp = r.json()["comparativo"]
    por = {x["chave"]: x for x in comp["linhas"]}
    assert comp["real_rotulo"] == "transações da Amazon"
    assert list(por) == [
        "produtos",
        "frete_cliente",
        "comissao",
        "amazon_for_all",
        "retencao",
        "depois",
        "liquido",
    ]
    assert por["frete_cliente"]["previsto"] == 94.81 and por["comissao"]["real"] == 285.78
    assert (
        por["liquido"]["previsto"] == 2356.21
        and por["liquido"]["real"] == 2356.21
        and por["liquido"]["dif"] == 0
    )


def _dba_em_duas(posted: str = "2026-10-10T14:00:00Z") -> list[dict]:
    """Como vem de verdade no DBA: o lançamento (2.176 − 108,80 − 31,01 = 2.036,19) e a
    etiqueta Easy Ship como ServiceFee separado (−41,90) dois dias depois."""
    venda = _tx(
        "tx-dba-venda",
        "Shipment",
        "RELEASED",
        posted,
        "2036.19",
        pedido=DBA,
        quebras=[
            _b("ProductCharges", "2176", [_b("OurPricePrincipal", "2176")]),
            _b("PromoRebates", "-108.80", [_b("OurPriceDiscount", "-108.80")]),
            _b(
                "AmazonFees",
                "-31.01",
                [_b("AmazonForAllFee", "-31.01", [_b("Base", "-27.52"), _b("Tax", "-3.49")])],
            ),
        ],
    )
    etiqueta = _tx(
        "tx-dba-etiqueta",
        "ServiceFee",
        "RELEASED",
        "2026-10-12T09:00:00Z",
        "-41.90",
        pedido=DBA,
        quebras=[
            _b(
                "AmazonFees",
                "-41.90",
                [_b("MFNPostageFee", "-41.90", [_b("Base", "-37.19"), _b("Tax", "-4.71")])],
            )
        ],
    )
    return [venda, etiqueta]


async def test_etiqueta_easy_ship_separada_nao_e_estorno(db, make_user, auth_as, client):
    from app.models import MarketplaceOrderFinancial

    loja = await _loja_amazon(db, make_user)
    txs = _dba_em_duas()
    m = transacao_para_movimento(txs[1])
    assert m["tipo"] == "frete" and m["debito"] == Decimal("41.90")
    assert m["descricao"] == f"Etiqueta Easy Ship do pedido {DBA}"
    await run_amazon_sync(db, loja, _AmazonFalsa(txs), agora=AGORA)
    db.add(
        MarketplaceOrderFinancial(
            integration_id=loja.integration_id,
            platform="amazon",
            external_order_id=DBA,
            status="posted",
            fetched_at=datetime(2026, 10, 13, 12, tzinfo=UTC),
            net_amount=Decimal("1994.29"),
            raw={"transactions": {"transactions": txs}},
        )
    )
    await db.commit()
    await recalcular_loja(db, loja, agora=AGORA)
    await db.commit()
    auth_as(await make_user(permissions={"conciliacao": {"view": True}}))
    r = await client.get("/api/conciliacao/pedidos/301943", params={"loja": "kia"})
    assert r.status_code == 200, r.text
    ped = r.json()["pedido"]
    assert (
        ped["situacao"] == "bateu"
        and ped["recebido"] == 1994.29
        and ped["liberado_valor"] == 2036.19
    )
    assert ped["saidas_depois"] in (None, 0)
    assert (
        ped["venda_frete"] == 41.9 and ped["venda_extras"] == 108.8 and ped["venda_tarifa"] == 31.01
    )
    por = {x["chave"]: x for x in r.json()["comparativo"]["linhas"]}
    assert por["etiqueta"]["previsto"] == 41.9 and por["etiqueta"]["real"] == 41.9
    assert por["liquido"]["real"] == 1994.29 and por["liquido"]["dif"] == 0


async def test_liberacao_da_reserva_nao_conta_duas_vezes(db, make_user):
    """Kia 301678 (15/10): lançamento de 06/10 vira DEFERRED_RELEASED e a Amazon manda
    outro registro RELEASED em 14/10 com o mesmo valor — fica um só, com "liberado em"."""
    loja = await _loja_amazon(db, make_user)
    original = _venda_proprio(status="DEFERRED", posted="2026-10-06T14:00:00Z")
    cliente = _AmazonFalsa([original])
    r = await run_amazon_sync(db, loja, cliente, agora=AGORA)
    assert r["inseridos"] == 1
    # dias depois: o original liberou e veio a repetição
    liberado = _venda_proprio(status="DEFERRED_RELEASED", posted="2026-10-06T14:00:00Z")
    repeticao = _venda_proprio(status="RELEASED", posted="2026-10-14T03:00:00Z")
    repeticao["transactionId"] = "tx-proprio-liberado"
    cliente.transacoes = [liberado, repeticao]
    r2 = await run_amazon_sync(db, loja, cliente, agora=AGORA)
    assert r2["duplicatas"] == 1 and r2["inseridos"] == 0 and r2["atualizados"] == 1
    movs = (await db.execute(select(ConciliacaoMovimento))).scalars().all()
    assert len(movs) == 1 and movs[0].sub_tipo == "DEFERRED_RELEASED"
    assert movs[0].descricao.endswith("· liberado em 14/10") and movs[0].saldo_apos == Decimal(
        "2356.21"
    )
    assert movs[0].raw["_liberado_em"].startswith("2026-10-14")
    # repetição que já tinha entrado (rodada antiga) sai na rodada seguinte
    db.add(
        ConciliacaoMovimento(
            integration_id=loja.integration_id,
            platform=IntegrationPlatform.AMAZON,
            origem="amazon_transacoes",
            id_externo="tx-proprio-liberado",
            data=datetime(2026, 10, 14, 3, tzinfo=UTC),
            tipo="venda_liberada",
            descricao="Lançamento do pedido",
            sub_tipo="RELEASED",
            pedido_marketplace=PROPRIO,
            credito=Decimal("2356.21"),
            debito=Decimal("0"),
            raw={},
        )
    )
    await db.flush()
    r3 = await run_amazon_sync(db, loja, cliente, agora=AGORA)
    assert r3["removidas"] == 1
    movs = (await db.execute(select(ConciliacaoMovimento))).scalars().all()
    assert len(movs) == 1 and movs[0].saldo_apos == Decimal("2356.21")
