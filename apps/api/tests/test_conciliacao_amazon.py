"""Conciliação — Amazon: transações como extrato, saldo encadeado e reserva (passo 5, 15/10)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from httpx import Response
from sqlalchemy import select

from app.models import (
    BlingOrder,
    ConciliacaoLoja,
    ConciliacaoMovimento,
    ConciliacaoPedido,
    ConciliacaoSync,
    Integration,
    MarketplaceOrderFinancial,
    StoreInfo,
)
from app.models.integration import IntegrationPlatform
from app.security.cipher import encrypt_json
from app.services.conciliacao_amazon import (
    BRT,
    ROTA_TRANSACOES,
    conta_amazon_de_quebras,
    quebras_da_transacao,
    run_amazon_sync,
    transacao_para_movimento,
)
from app.services.conciliacao_amazon_csv import (
    ingerir_csv_amazon,
    ler_csv_amazon,
    transacoes_do_csv,
)
from app.services.conciliacao_calculo import conta_amazon_de_transacoes, recalcular_loja

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
        and deb["classificacao"] is None  # cobrança da plataforma: sem dono (16/10 à noite)
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
    tx = _venda_proprio(status="RELEASED")  # liberado (16/10: preso na reserva = aguardando)
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
    # envio próprio sem o frete do Melhor Envio não é "bateu" (15/10, Vinicius)
    assert ped["situacao"] == "falta_frete" and ped["recebido"] == 2356.21
    # Valor = produto + frete cobrado do cliente; Tarifa = comissão + 1,5% + retenção;
    # Frete = só o da Amazon (etiqueta; no envio próprio fica 0) — 15/10, Vinicius
    assert ped["venda_valor"] == 2692.81 and ped["venda_tarifa"] == 336.6
    assert ped["motivo"] == "sem o frete do Melhor Envio"
    assert ped["venda_frete"] == 0 and ped["venda_extras"] == 0 and ped["venda_liquido"] == 2356.21
    r = await client.get("/api/conciliacao/pedidos/301635", params={"loja": "kia"})
    comp = r.json()["comparativo"]
    por = {x["chave"]: x for x in comp["linhas"]}
    assert comp["real_rotulo"] == "transações da Amazon"
    assert list(por) == [
        "produtos",
        "frete_cliente",
        "valor",
        "comissao",
        "amazon_for_all",
        "retencao",
        "depois",
        "liquido",
    ]
    assert por["frete_cliente"]["previsto"] == 94.81 and por["comissao"]["real"] == 285.78
    assert por["valor"]["previsto"] == 2692.81 and por["valor"]["real"] == 2692.81
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


async def test_token_do_melhor_envio_salva_cifrado_e_recusa_invalido(
    db, make_user, auth_as, client, monkeypatch
):
    from app.services import melhor_envio as me

    bom = "eyJ" + "x" * 40

    async def _conta_ok(token: str):
        assert token == bom
        return 200, {"firstname": "Casa", "lastname": "Decoração", "email": "x@y"}

    async def _conta_ruim(token: str):
        return 401, {"message": "Unauthenticated."}

    auth_as(await make_user(permissions={"conciliacao": {"view": True, "edit": True}}))
    r = await client.get("/api/conciliacao/melhor-envio/token")
    assert r.status_code == 200 and r.json()["origem"] is None and r.json()["tem_token"] is False
    monkeypatch.setattr(me, "ler_conta", _conta_ruim)
    r = await client.put("/api/conciliacao/melhor-envio/token", json={"token": "eyJ" + "y" * 40})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "token_recusado"
    monkeypatch.setattr(me, "ler_conta", _conta_ok)
    r = await client.put("/api/conciliacao/melhor-envio/token", json={"token": bom})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["tem_token"] and corpo["origem"] == "painel" and corpo["conta"] == "Casa Decoração"
    assert bom not in r.text  # o token nunca volta
    token, detalhe = await me.token_do_painel(db)
    assert token == bom and detalhe["conta"] == "Casa Decoração"
    cli = await me.cliente_me(db)
    assert cli.token == bom
    r = await client.delete("/api/conciliacao/melhor-envio/token")
    assert r.status_code == 200 and r.json()["origem"] is None


def _envio_me(
    eid: str,
    pedido: str,
    amazon_id: str,
    *,
    price="48.90",
    status="posted",
    conciliation=None,
    canceled_at=None,
) -> dict:
    return {
        "id": eid,
        "protocol": "ORD-2026",
        "status": status,
        "price": float(price),
        "discount": 0,
        "insurance_value": 1000,
        "billed_weight": 0.5,
        "paid_at": "2026-10-15 11:21:44",
        "posted_at": "2026-10-15 16:00:00" if status != "released" else None,
        "delivered_at": None,
        "canceled_at": canceled_at,
        "created_at": "2026-10-15 11:20:00",
        "tracking": "AE029696432BR",
        "self_tracking": None,
        "conciliation": conciliation,
        "service": {"id": 2, "name": "SEDEX"},
        "tags": [
            {"tag": "mi:reference_code", "url": pedido},
            {"tag": "mi:reference_link", "url": "https://www.bling.com.br/vendas.php#edit/1"},
            {"tag": "mi:marketplace_code", "url": amazon_id},
        ],
        "to": {"name": "cliente", "address": "rua x"},
    }


def test_envio_para_frete_le_etiquetas_e_conferencia():
    from app.services.conciliacao_melhor_envio import envio_para_frete

    f = envio_para_frete(
        _envio_me("e1", "301635", PROPRIO, conciliation={"type": "debit", "value": 3.2})
    )
    assert f["pedido_bling"] == "301635" and f["pedido_marketplace"] == PROPRIO
    assert f["preco"] == Decimal("48.90") and f["conferencia"] == Decimal("3.20")
    assert f["frete_pago"] == Decimal("52.10") and f["servico"] == "SEDEX"
    assert "to" not in f["raw"]  # nada do cliente
    c = envio_para_frete(
        _envio_me("e2", "301635", PROPRIO, status="canceled", canceled_at="2026-10-15 12:00:00")
    )
    assert c["frete_pago"] == Decimal("0") and c["cancelado_em"] is not None
    credito = envio_para_frete(
        _envio_me("e3", "301635", PROPRIO, conciliation=[{"type": "credit", "value": 0}])
    )
    assert credito["frete_pago"] == Decimal("48.90")


async def test_frete_do_melhor_envio_entra_no_sobrou_e_sem_ele_falta_frete(
    db, make_user, auth_as, client
):
    from app.models import MarketplaceOrderFinancial
    from app.services import melhor_envio as me
    from app.services.conciliacao_melhor_envio import run_me_sync

    loja = await _loja_amazon(db, make_user)
    tx = _venda_proprio(status="RELEASED")  # liberado (16/10: preso na reserva = aguardando)
    await run_amazon_sync(db, loja, _AmazonFalsa([tx]), agora=AGORA)
    db.add(
        MarketplaceOrderFinancial(
            integration_id=loja.integration_id,
            platform="amazon",
            external_order_id=PROPRIO,
            status="posted",
            fetched_at=datetime(2026, 10, 12, 12, tzinfo=UTC),
            net_amount=Decimal("2356.21"),
            raw={"transactions": {"transactions": [tx]}},
        )
    )
    await db.commit()
    # 1) sem frete do Melhor Envio: envio próprio não pode ser "bateu"
    await recalcular_loja(db, loja, agora=AGORA)
    await db.commit()
    auth_as(await make_user(permissions={"conciliacao": {"view": True, "edit": True}}))
    r = await client.get("/api/conciliacao/pedidos/301635", params={"loja": "kia"})
    ped = r.json()["pedido"]
    assert ped["situacao"] == "falta_frete" and ped["etapa"] == "pendencia"
    assert ped["motivo"] == "sem o frete do Melhor Envio" and ped["venda_frete_pago"] is None
    assert ped["sobrou"] == 1356.21  # 2356,21 − custo 1000
    res = await client.get("/api/conciliacao/resumo")
    assert any("sem o frete do Melhor Envio" in a["titulo"] for a in res.json()["agir"])
    # 2) token no painel + envio casado pela etiqueta → frete pago entra no Sobrou
    await me.guardar_token_do_painel(db, "eyJ" + "t" * 40, por="teste", detalhe={})
    await db.commit()

    async def _ler(token: str, *, page: int = 1, q=None):
        assert token == "eyJ" + "t" * 40
        if page > 1:
            return 200, {"data": []}
        return 200, {
            "data": [
                _envio_me("e1", "301635", PROPRIO, conciliation={"type": "debit", "value": 3.2}),
                _envio_me("e9", "999999", "000-0000000-0000000"),  # de outra loja: ignora
            ],
            "total": 2,
        }

    r = await run_me_sync(db, loja, agora=AGORA, ler=_ler)
    assert r["casados"] == 1 and r["novos"] == 1 and r["vistos"] == 2
    await db.commit()
    await recalcular_loja(db, loja, agora=AGORA)
    await db.commit()
    r = await client.get("/api/conciliacao/pedidos/301635", params={"loja": "kia"})
    corpo = r.json()
    ped = corpo["pedido"]
    assert ped["situacao"] == "bateu" and ped["venda_frete_pago"] == 52.1
    assert ped["venda_liquido"] == 2356.21  # o Melhor Envio NÃO entra no Líquido
    assert ped["sobrou"] == 1304.11  # 2356,21 − 1000 − 52,10
    assert ped["motivo"] == "liberado em 09/10"
    assert (
        corpo["frete_pago"][0]["servico"] == "SEDEX"
        and corpo["frete_pago"][0]["frete_pago"] == 52.1
    )
    # segunda leitura: nada novo, nada duplicado
    r2 = await run_me_sync(db, loja, agora=AGORA, ler=_ler)
    assert r2["novos"] == 0 and r2["atualizados"] == 0


async def test_cancelado_no_bling_com_estorno_integral_e_cancelado(db, make_user, auth_as, client):
    """Amazon 302142 (15/10): lançou 749,53, devolveu 759,53 (a Amazon ficou com 10,00 de
    tarifa) e o Bling está Cancelado → "cancelado", custo 0, sobrou −10,00."""
    from app.models import MarketplaceOrderFinancial

    loja = await _loja_amazon(db, make_user)
    venda = _tx(
        "tx-c-venda",
        "Shipment",
        "RELEASED",
        "2026-10-07T12:00:00Z",
        "749.53",
        pedido=DBA,
        quebras=[
            _b("ProductCharges", "729", [_b("OurPricePrincipal", "729")]),
            _b("Shipping", "164.06", [_b("ShippingPrincipal", "164.06")]),
            _b("PromoRebates", "-36.45", [_b("OurPriceDiscount", "-36.45")]),
            _b("AmazonFees", "-107.08", [_b("Commission", "-107.08")]),
        ],
    )
    estorno = _tx(
        "tx-c-estorno", "Refund", "RELEASED", "2026-10-08T12:00:00Z", "-759.53", pedido=DBA
    )
    await run_amazon_sync(db, loja, _AmazonFalsa([venda, estorno]), agora=AGORA)
    db.add(
        MarketplaceOrderFinancial(
            integration_id=loja.integration_id,
            platform="amazon",
            external_order_id=DBA,
            status="posted",
            fetched_at=datetime(2026, 10, 9, 12, tzinfo=UTC),
            net_amount=Decimal("-10.00"),
            raw={"transactions": {"transactions": [venda, estorno]}},
        )
    )
    pedido = (
        await db.execute(select(BlingOrder).where(BlingOrder.numero == "301943"))
    ).scalar_one()
    pedido.situacao = "Cancelado"
    await db.commit()
    await recalcular_loja(db, loja, agora=AGORA)
    await db.commit()
    auth_as(await make_user(permissions={"conciliacao": {"view": True}}))
    r = await client.get("/api/conciliacao/pedidos/301943", params={"loja": "kia"})
    ped = r.json()["pedido"]
    # 16/10 (revisão, aprovado): cancelado com dinheiro ≠ 0 (a Amazon ficou com 10,00)
    # não é "zero a zero" — fica pendência pra alguém olhar, em vez de finalizar sozinho
    assert ped["situacao"] == "cancelado" and ped["etapa"] == "pendencia"
    assert ped["custo"] == 0 and ped["recebido"] == -10 and ped["sobrou"] == -10
    assert "ficou com 10.00" in ped["motivo"] and ped["venda_liquido"] == 749.53
    por = {x["chave"]: x for x in r.json()["comparativo"]["linhas"]}
    assert por["liquido"]["previsto"] == 749.53 and por["liquido"]["real"] == 749.53
    assert por["depois"]["real"] == -759.53 and por["ficou"]["real"] == -10
    # o Bling deixou de ser Cancelado (Aguardando Devolução): sai de cancelado e continua
    # pendência (nunca foi finalizado, porque sobrou −10,00)
    pedido.situacao = "Aguardando Devolução"
    await db.commit()
    await recalcular_loja(db, loja, agora=AGORA)
    await db.commit()
    r = await client.get("/api/conciliacao/pedidos/301943", params={"loja": "kia"})
    ped = r.json()["pedido"]
    assert ped["situacao"] == "estornado_depois" and ped["etapa"] == "pendencia"
    # quando o robô tinha finalizado como "venda desfeita" (zero a zero) e o pedido
    # deixa de ser cancelado, aí sim volta como reaberto
    atual = (
        await db.execute(
            select(ConciliacaoPedido).where(ConciliacaoPedido.pedido_bling == "301943")
        )
    ).scalar_one()
    atual.etapa, atual.etapa_por, atual.etapa_motivo = (
        "finalizado",
        "robô",
        "venda desfeita, zero a zero",
    )
    await db.commit()
    await recalcular_loja(db, loja, agora=AGORA)
    await db.commit()
    r = await client.get("/api/conciliacao/pedidos/301943", params={"loja": "kia"})
    ped = r.json()["pedido"]
    assert ped["etapa"] == "reaberto"
    assert ped["etapa_motivo"] == "deixou de ser cancelado (estornado_depois)"


# --- por arquivo (KFA, 16/10) ---------------------------------------------------

CSV_KFA = "\n".join(
    [
        '"Data","Status da transação","Grupo de Pagamento","Tipo de transação","ID do pedido",'
        '"Detalhes do produto","Custo total do produto","Total de descontos promocionais",'
        '"Tarifas da Amazon","Outros","(total) (BRL)"',
        '"16/10/2026","Liberado","Boleto","Pagamento do pedido","701-4878844-1794663",'
        '"Kit Mala de Despacho 24 e...","387","-38.7","-53.98","0","294.32"',
        '"15/10/2026","Diferido","Crédito Mastercard e outros","Pagamento do pedido",'
        '"702-7077353-9607430","Silicone de Reposição...","68","0","-8.16","0","59.84"',
        '"15/10/2026","Liberado","Crédito Mastercard e outros","Tarifas de serviço",'
        '"702-7077353-9607430","Delivery by Amazon Charges, Estorno do f...",'
        '"0","0","-13","0","-13"',
        '"12/10/2026","Liberado","Visa Crédito","Reembolso","701-9242808-9886657",'
        '"Mala de Viagem 24...","-288","28.8","32.14","0","-227.06"',
        '"08/10/2026","Liberado","Boleto","Cobranças do Delivery by Amazon","701-4878844-1794663",'
        '"Faturamento","0","0","-21.95","0","-21.95"',
        '"08/10/2026","Liberado","Boleto","Cobranças do Delivery by Amazon","701-4878844-1794663",'
        '"Faturamento","0","0","-21.95","0","-21.95"',
        '"07/10/2026","Liberado","Visa Crédito","Tarifas de serviço","---","Taxa de assinatura:",'
        '"0","0","-19","0","-19"',
        '"04/10/2026","Liberado","Visa Crédito","Pago à Amazon | Repagamento do vendedor","---",'
        '"---","0","0","0","622.33","622.33"',
        '"02/10/2026","Liberado","Visa Crédito","Tarifas de serviço","---","Custo de Publicidade",'
        '"0","0","-338.42","0","-338.42"',
    ]
)


def test_csv_da_amazon_vira_transacoes_iguais_as_da_api():
    _cab, rows = ler_csv_amazon("\ufeff" + CSV_KFA)
    assert len(rows) == 9
    txs = transacoes_do_csv(rows)
    assert len(txs) == 9
    por_tipo = {t["_tipo_csv"]: t["transactionType"] for t in txs}
    assert por_tipo["Pagamento do pedido"] == "Shipment"
    assert por_tipo["Reembolso"] == "Refund"
    assert por_tipo["Cobranças do Delivery by Amazon"] == "ServiceFee"
    assert por_tipo["Pago à Amazon | Repagamento do vendedor"] == "DebtRecovery"
    # linha repetida (duas cobranças iguais do mesmo pedido no mesmo dia): #2
    ids = [t["transactionId"] for t in txs]
    assert len(set(ids)) == 9 and any(i.endswith("#2") for i in ids)
    movs = {m["descricao"]: m for m in (transacao_para_movimento(t) for t in txs)}
    venda = movs["Lançamento do pedido 701-4878844-1794663"]
    assert venda["tipo"] == "venda_liberada" and venda["credito"] == Decimal("294.32")
    assert venda["sub_tipo"] == "RELEASED" and venda["bruto"] == Decimal("387")
    presa = movs["Lançamento do pedido 702-7077353-9607430 (preso na reserva)"]
    assert presa["sub_tipo"] == "DEFERRED"
    etiqueta = movs["Etiqueta Easy Ship do pedido 702-7077353-9607430"]
    assert etiqueta["tipo"] == "frete" and etiqueta["debito"] == Decimal("13")
    assert movs["Etiqueta Easy Ship do pedido 701-4878844-1794663"]["tipo"] == "frete"
    assert movs["Estorno do pedido 701-9242808-9886657"]["debito"] == Decimal("227.06")
    assert movs["Tarifa da Amazon · Taxa de assinatura:"]["classificacao"] is None
    assert movs["Publicidade (Ads)"]["tipo"] == "publicidade"
    cobranca = movs["Cobrança do saldo negativo (cartão/conta)"]
    assert cobranca["credito"] == Decimal("622.33") and cobranca["classificacao"] is None
    # a conta do pedido sai como na leitura pela API
    conta = conta_amazon_de_transacoes([venda["raw"]])
    assert conta["produto"] == Decimal("387") and conta["promocao"] == Decimal("-38.7")
    assert conta["tarifas"] == Decimal("-53.98") and conta["liquido"] == Decimal("294.32")
    # mesmo dia: ordem crescente a partir de 00:00 BRT (arquivo vem da mais nova pra mais velha)
    assert etiqueta["data"] < presa["data"]


async def test_ingerir_csv_amazon_grava_atualiza_e_nao_duplica(db, make_user):
    loja = await _loja_amazon(db, make_user)
    loja.modo_extrato = "upload"
    await db.commit()
    agora = datetime(2026, 10, 16, 13, tzinfo=UTC)
    r1 = await ingerir_csv_amazon(db, loja, CSV_KFA, agora=agora)
    assert r1["inseridos"] == 9 and r1["atualizados"] == 0
    assert r1["de"] == "2026-10-02" and r1["ate"] == "2026-10-16"
    movs = (await db.execute(select(ConciliacaoMovimento))).scalars().all()
    assert len(movs) == 9 and all(m.saldo_apos is not None for m in movs)
    assert loja.ultimo_extrato_em == agora
    # o mesmo arquivo de novo, com a linha presa já liberada (a Amazon põe a data da
    # liberação na linha — 16/10, revisão)
    depois = CSV_KFA.replace('"15/10/2026","Diferido"', '"18/10/2026","Liberado"')
    r2 = await ingerir_csv_amazon(db, loja, depois, agora=agora + timedelta(days=2))
    assert r2["inseridos"] == 0 and r2["liberadas"] == 1 and r2["ja_existiam"] == 8
    assert r2["atualizados"] == 0
    movs = (await db.execute(select(ConciliacaoMovimento))).scalars().all()
    assert len(movs) == 9
    liberada = next(
        m
        for m in movs
        if m.pedido_marketplace == "702-7077353-9607430" and m.tipo == "venda_liberada"
    )
    assert liberada.sub_tipo == "RELEASED" and "preso" not in liberada.descricao
    assert liberada.descricao.endswith("· liberado em 18/10")
    assert liberada.raw["_liberado_em"].startswith("2026-10-18")
    assert liberada.data.astimezone(BRT).date() == date(2026, 10, 15)  # fica na data do lançamento
    syncs = (await db.execute(select(ConciliacaoSync))).scalars().all()
    assert sorted(s.chave for s in syncs) == [
        "upload:2026-10-16T13:00:00.000000",
        "upload:2026-10-18T13:00:00.000000",
    ]


async def test_endpoint_upload_amazon_so_para_loja_por_arquivo(db, make_user, auth_as, client):
    loja = await _loja_amazon(db, make_user)
    await db.commit()
    auth_as(await make_user(permissions={"conciliacao": {"view": True, "edit": True}}))
    arquivo = ("transacoes.csv", CSV_KFA.encode("utf-8-sig"), "text/csv")
    r = await client.post(
        "/api/conciliacao/amazon/upload", data={"loja": "kia"}, files={"arquivo": arquivo}
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "loja_nao_e_upload"
    loja.modo_extrato = "upload"
    await db.commit()
    r = await client.post(
        "/api/conciliacao/amazon/upload", data={"loja": "kia"}, files={"arquivo": arquivo}
    )
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["inseridos"] == 9 and corpo["loja"] == "kia" and "recalculo" in corpo
    r = await client.post(
        "/api/conciliacao/amazon/upload",
        data={"loja": "kia"},
        files={"arquivo": ("x.csv", b"a,b\n1,2\n", "text/csv")},
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "csv_invalido"
    r = await client.get("/api/conciliacao/lojas")
    assert r.json()["lojas"][0]["modo_extrato"] == "upload"


async def test_chave_do_script_gera_valida_e_envia_sem_login(
    db, make_user, auth_as, client, monkeypatch
):
    loja = await _loja_amazon(db, make_user)
    loja.modo_extrato = "upload"
    await db.commit()
    auth_as(await make_user(permissions={"conciliacao": {"view": True, "edit": True}}))
    r = await client.get("/api/conciliacao/amazon/upload/chave")
    assert r.status_code == 200 and r.json() == {"tem_chave": False}
    r = await client.post("/api/conciliacao/amazon/upload/chave")
    assert r.status_code == 200
    chave = r.json()["chave"]
    assert chave.startswith("kfa_") and len(chave) > 30
    r = await client.get("/api/conciliacao/amazon/upload/chave")
    assert r.json()["tem_chave"] is True and r.json()["gerada_em"]
    # o script envia sem sessão, só com a chave
    auth_as(None)
    arquivo = ("transacoes.csv", CSV_KFA.encode("utf-8-sig"), "text/csv")
    # 16/10, revisão: chave errada → 401 SEM ler o corpo (o formulário nem é aberto)
    from starlette.requests import Request

    async def nao_le(self):
        raise AssertionError("corpo lido antes de conferir a chave")

    monkeypatch.setattr(Request, "form", nao_le)
    r = await client.post(
        "/api/conciliacao/amazon/upload/script",
        data={"loja": "kia"},
        files={"arquivo": arquivo},
        headers={"X-Upload-Chave": "kfa_errada"},
    )
    assert r.status_code == 401 and r.json()["detail"]["code"] == "chave_invalida"
    # corpo acima do teto: 413 antes de abrir o formulário
    r = await client.post(
        "/api/conciliacao/amazon/upload/script",
        data={"loja": "kia"},
        files={"arquivo": arquivo},
        headers={"X-Upload-Chave": chave, "Content-Length": str(30 * 1024 * 1024)},
    )
    assert r.status_code == 413 and r.json()["detail"]["code"] == "arquivo_grande"
    monkeypatch.undo()
    # formulário sem o arquivo: 422, não 500
    r = await client.post(
        "/api/conciliacao/amazon/upload/script",
        data={"loja": "kia"},
        headers={"X-Upload-Chave": chave},
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "formulario_invalido"
    # arquivo maior que o teto: 413 (lido em pedaços, desiste no teto)
    r = await client.post(
        "/api/conciliacao/amazon/upload/script",
        data={"loja": "kia"},
        files={"arquivo": ("grande.csv", b"a" * 5_100_000, "text/csv")},
        headers={"X-Upload-Chave": chave},
    )
    assert r.status_code == 413 and r.json()["detail"]["code"] == "arquivo_grande"
    r = await client.post(
        "/api/conciliacao/amazon/upload/script",
        data={"loja": "kia"},
        files={"arquivo": arquivo},
        headers={"X-Upload-Chave": chave},
    )
    assert r.status_code == 200, r.text
    assert r.json()["inseridos"] == 9
    # chave nova invalida a anterior
    auth_as(await make_user(permissions={"conciliacao": {"view": True, "edit": True}}))
    nova = (await client.post("/api/conciliacao/amazon/upload/chave")).json()["chave"]
    auth_as(None)
    r = await client.post(
        "/api/conciliacao/amazon/upload/script",
        data={"loja": "kia"},
        files={"arquivo": arquivo},
        headers={"X-Upload-Chave": chave},
    )
    assert r.status_code == 401
    r = await client.post(
        "/api/conciliacao/amazon/upload/script",
        data={"loja": "kia"},
        files={"arquivo": arquivo},
        headers={"X-Upload-Chave": nova},
    )
    assert r.status_code == 200 and r.json()["ja_existiam"] == 9


async def test_botao_buscar_transacoes_enfileira_o_robo_e_o_resultado_volta_na_loja(
    db, make_user, auth_as, client
):
    from app.models import LogisticaRoboComando
    from app.services import logistica_robo
    from app.services.conciliacao_amazon_csv import enfileirar_busca_csv_todas

    loja = await _loja_amazon(db, make_user)
    await db.commit()
    auth_as(await make_user(permissions={"conciliacao": {"view": True, "edit": True}}))
    # loja lida pela API não tem robô de arquivo
    r = await client.post("/api/conciliacao/lojas/kia/buscar-transacoes")
    assert r.status_code == 409
    loja.modo_extrato = "upload"
    await db.commit()
    r = await client.post("/api/conciliacao/lojas/kia/buscar-transacoes")
    assert r.status_code == 200, r.text
    assert r.json()["enfileirado"] is True and r.json()["comando_id"]
    cmd_id = r.json()["comando_id"]
    # de novo: já tem na fila, não duplica
    r = await client.post("/api/conciliacao/lojas/kia/buscar-transacoes")
    assert r.json()["enfileirado"] is False and r.json()["comando_id"] == cmd_id
    cmd = await db.get(LogisticaRoboComando, __import__("uuid").UUID(cmd_id))
    assert cmd.acao == "amazon_transacoes_csv" and cmd.logistica_id is None
    assert cmd.payload["loja"] == "kia" and cmd.payload["dias"] == 35
    # o executor do mini pega pelo nome da ação
    leased = await logistica_robo.lease(db, limit=5, acoes=["amazon_transacoes_csv"])
    assert [x["id"] for x in leased] == [cmd_id] and leased[0]["logistica_id"] is None
    # o cron das 06:00 não duplica enquanto a tarefa está aberta
    assert await enfileirar_busca_csv_todas(db) == []
    # falhou → aparece na loja; deu certo → limpa
    await logistica_robo.registrar_resultado(
        db, cmd.id, status="failed", result="needs_manual_login: a Amazon pediu login"
    )
    await db.refresh(loja)
    assert loja.ultimo_erro.startswith("robô: needs_manual_login")
    # com a tarefa fechada, o cron enfileira de novo
    assert await enfileirar_busca_csv_todas(db) == ["kia"]
    novo = (
        await db.execute(
            __import__("sqlalchemy")
            .select(LogisticaRoboComando)
            .where(LogisticaRoboComando.status == "pending")
        )
    ).scalar_one()
    await logistica_robo.registrar_resultado(db, novo.id, status="done", result='{"ok": true}')
    await db.refresh(loja)
    assert loja.ultimo_erro is None
    # o executor envia o CSV com o X-Agent-Token dele
    from app.config import get_settings

    get_settings().marketing_agent_token = "tok-agente-teste"  # noqa: S105 — token de mentira
    try:
        auth_as(None)
        arquivo = ("transacoes.csv", CSV_KFA.encode("utf-8-sig"), "text/csv")
        r = await client.post(
            "/api/conciliacao/amazon/upload/script",
            data={"loja": "kia"},
            files={"arquivo": arquivo},
            headers={"X-Agent-Token": "tok-agente-teste"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["inseridos"] == 9
    finally:
        get_settings().marketing_agent_token = ""


async def test_leitura_da_kfa_grava_a_venda_na_margem(db, make_user):
    """16/10 à noite (Vinicius: "o valor esperado é o da plataforma, lido pela Margem"):
    a KFA não tem a Margem pela API, então a leitura do arquivo grava a venda na Margem
    por pedido; presa continua sendo atualizada, liberada congela."""
    loja = await _loja_amazon(db, make_user)
    loja.modo_extrato = "upload"
    # a Margem antiga deixou a venda vazia (403 da Finances API): é lugar vago
    db.add(
        MarketplaceOrderFinancial(
            integration_id=loja.integration_id,
            platform="amazon",
            external_order_id="701-4878844-1794663",
            status="error",
            last_error="403",
            fetched_at=datetime(2026, 10, 1, tzinfo=UTC),
            raw={},
        )
    )
    await db.commit()
    agora = datetime(2026, 10, 16, 13, tzinfo=UTC)
    r1 = await ingerir_csv_amazon(db, loja, CSV_KFA, agora=agora)
    assert r1["vendas_margem"] == {"criadas": 1, "atualizadas": 1}
    vendas = {
        v.external_order_id: v
        for v in (await db.execute(select(MarketplaceOrderFinancial))).scalars()
    }
    liberada = vendas["701-4878844-1794663"]
    # lançamento 294,32 menos as duas etiquetas Easy Ship (21,95 cada)
    assert liberada.net_amount == Decimal("250.42") and liberada.gross_amount == Decimal("387")
    assert liberada.status == "posted" and liberada.last_error is None
    assert liberada.raw["fonte"] == "csv_kfa" and liberada.raw["congelada"]
    assert len(liberada.raw["transactions"]["transactions"]) == 3
    presa = vendas["702-7077353-9607430"]
    # lançamento preso 59,84 menos a etiqueta de 13,00
    assert presa.net_amount == Decimal("46.84") and not presa.raw.get("congelada")
    # arquivo novo: a presa liberou com OUTRO valor (55,84) → a venda congela com o
    # lançamento como foi lançado (59,84): a diferença é do painel, não da venda
    depois = CSV_KFA.replace('"15/10/2026","Diferido"', '"18/10/2026","Liberado"').replace(
        '"-8.16","0","59.84"', '"-12.16","0","55.84"'
    )
    r2 = await ingerir_csv_amazon(db, loja, depois, agora=agora + timedelta(days=2))
    assert r2["vendas_margem"] == {"criadas": 0, "atualizadas": 1}
    await db.refresh(presa)
    assert presa.raw["congelada"].startswith("2026-10-18")
    assert presa.net_amount == Decimal("46.84") and presa.gross_amount == Decimal("68")
    r3 = await ingerir_csv_amazon(db, loja, depois, agora=agora + timedelta(days=3))
    assert r3["vendas_margem"] == {"criadas": 0, "atualizadas": 0}


async def test_loja_por_arquivo_tem_a_venda_das_proprias_transacoes(db, make_user):
    """16/10 (Vinicius: "como pode estar bateu se nem valor tem?"): a KFA não tem a venda
    da Margem (API negada); a leitura do arquivo grava a venda na Margem e o previsto
    compara com ela (16/10 à noite: "plataforma via Margem")."""
    from app.models import BlingOrder, ConciliacaoPedido

    loja = await _loja_amazon(db, make_user)
    loja.modo_extrato = "upload"
    db.add(
        BlingOrder(
            bling_id=8010,
            numero="304001",
            numeroloja="701-4878844-1794663",
            data=datetime(2026, 10, 14, 12, tzinfo=UTC),
            total=Decimal("387"),
            totalprodutos=Decimal("387"),
            situacao="Atendido",
            loja="204000004",
            item_index=0,
            preco_custo=200.0,
            item_quantidade=1,
            item_codigo="mala24",
        )
    )
    await db.commit()
    await ingerir_csv_amazon(db, loja, CSV_KFA, agora=datetime(2026, 10, 16, 13, tzinfo=UTC))
    await recalcular_loja(db, loja, agora=datetime(2026, 10, 16, 14, tzinfo=UTC))
    p = (
        await db.execute(
            select(ConciliacaoPedido).where(ConciliacaoPedido.pedido_bling == "304001")
        )
    ).scalar_one()
    # lançamento 294,32 menos as duas etiquetas Easy Ship de 21,95 (Cobranças do DBA)
    assert p.situacao == "bateu" and p.recebido == Decimal("250.42")
    assert p.venda_valor == Decimal("387") and p.venda_liquido == Decimal("250.42")
    # na grade, tarifa, extras e frete ficam positivos (o que saiu)
    assert p.venda_tarifa == Decimal("53.98") and p.venda_cupom == Decimal("38.70")
    assert p.venda_frete == Decimal("43.90")
    assert p.dif_previsto_recebido == Decimal("0.00")


_CAB_KFA = CSV_KFA.split("\n")[0]
_PRESA_02 = (
    '"02/10/2026","Diferido","Crédito Mastercard e outros","Pagamento do pedido",'
    '"701-5141233-9704209","CHARLOT\'S PARK Mala de Bordo 20 Polegada...","267","0","-41.39",'
    '"0","225.61"'
)
_LIBERADA_16 = _PRESA_02.replace('"02/10/2026","Diferido"', '"16/10/2026","Liberado"')
_ETIQUETA_02 = (
    '"02/10/2026","Liberado","Crédito Mastercard e outros","Cobranças do Delivery by Amazon",'
    '"701-5141233-9704209","Faturamento","0","0","-21.95","0","-21.95"'
)
CSV_300772_A = "\n".join([_CAB_KFA, _ETIQUETA_02, _PRESA_02])  # arquivo das 14:30 de 16/10
CSV_300772_B = "\n".join([_CAB_KFA, _LIBERADA_16, _ETIQUETA_02])  # arquivo das 16:41


async def test_venda_presa_que_libera_vem_com_outra_data_e_nao_entra_em_dobro(db, make_user):
    """16/10 (revisão, KFA 300772): a Amazon reemite a linha presa com a DATA da
    liberação ("02/10 Diferido" vira "16/10 Liberado"); com a data na identidade ela
    entrava de novo (534 em vez de 267). Agora casa com a presa e fica na data do
    lançamento, marcada "liberado em"."""
    loja = await _loja_amazon(db, make_user)
    loja.modo_extrato = "upload"
    await db.commit()
    t0 = datetime(2026, 10, 16, 17, 30, tzinfo=UTC)
    r1 = await ingerir_csv_amazon(db, loja, CSV_300772_A, agora=t0)
    assert r1["inseridos"] == 2 and r1["liberadas"] == 0
    r2 = await ingerir_csv_amazon(db, loja, CSV_300772_B, agora=t0 + timedelta(hours=2))
    assert r2["inseridos"] == 0 and r2["liberadas"] == 1 and r2["ja_existiam"] == 1
    assert r2["duplicatas_apagadas"] == 0
    movs = (await db.execute(select(ConciliacaoMovimento))).scalars().all()
    assert len(movs) == 2
    venda = next(m for m in movs if m.tipo == "venda_liberada")
    assert venda.sub_tipo == "RELEASED" and venda.credito == Decimal("225.61")
    assert venda.data.astimezone(BRT).date() == date(2026, 10, 2)
    assert venda.descricao.endswith("· liberado em 16/10")
    assert venda.raw["_liberado_em"].startswith("2026-10-16")
    # o mesmo arquivo de novo: nada muda
    r3 = await ingerir_csv_amazon(db, loja, CSV_300772_B, agora=t0 + timedelta(hours=3))
    assert r3["inseridos"] == 0 and r3["liberadas"] == 0 and r3["ja_existiam"] == 2


async def test_identidade_antiga_do_csv_e_reconhecida_e_a_duplicata_some(db, make_user):
    """Linhas gravadas antes (identidade com a data) ganham o id novo; a presa que já
    tinha entrado em dobro (300772: 02/10 presa + 16/10 liberada) vira uma só."""
    from app.services.conciliacao_shopee import inserir_movimentos

    loja = await _loja_amazon(db, make_user)
    loja.modo_extrato = "upload"
    await db.commit()
    velhos: list[dict] = []
    for texto in (CSV_300772_A, CSV_300772_B):
        _cab, rows = ler_csv_amazon(texto)
        for t in transacoes_do_csv(rows):
            legado = t.pop("_idLegado")
            m = transacao_para_movimento(t)
            m["id_externo"] = legado
            if legado not in {v["id_externo"] for v in velhos}:
                velhos.append(m)
    assert len(velhos) == 3  # etiqueta, presa de 02/10 e liberada de 16/10 (em dobro)
    assert await inserir_movimentos(db, loja, velhos) == 3
    await db.commit()
    r = await ingerir_csv_amazon(
        db, loja, CSV_300772_B, agora=datetime(2026, 10, 17, 9, tzinfo=UTC)
    )
    assert r["migrados"] == 2 and r["inseridos"] == 0 and r["duplicatas_apagadas"] == 1
    movs = (await db.execute(select(ConciliacaoMovimento))).scalars().all()
    assert len(movs) == 2 and all(not m.id_externo.endswith("#2") for m in movs)
    venda = next(m for m in movs if m.tipo == "venda_liberada")
    assert venda.sub_tipo == "RELEASED" and venda.data.astimezone(BRT).date() == date(2026, 10, 2)
    assert venda.descricao.endswith("· liberado em 16/10")
    assert venda.raw["_liberado_em"].startswith("2026-10-16")


async def test_repeticao_liberada_com_original_fora_da_janela_casa_com_o_banco(db, make_user):
    """16/10 (revisão): a repetição RELEASED só era reconhecida se o lançamento original
    viesse no mesmo lote; fora da janela de 21 dias entrava de novo (kia 301678)."""
    loja = await _loja_amazon(db, make_user)
    original = _venda_proprio(status="DEFERRED", posted="2026-10-06T14:00:00Z")
    cliente = _AmazonFalsa([original])
    assert (await run_amazon_sync(db, loja, cliente, agora=AGORA))["inseridos"] == 1
    # 22 dias depois a janela não traz mais o original; só a repetição
    repeticao = _venda_proprio(status="RELEASED", posted="2026-10-14T03:00:00Z")
    repeticao["transactionId"] = "tx-proprio-liberado"
    cliente.transacoes = [repeticao]
    # (a leitura começa 21 dias antes da última; o original de 06/10 já ficou de fora)
    depois = AGORA + timedelta(days=10)
    r2 = await run_amazon_sync(db, loja, cliente, agora=depois)
    assert r2["duplicatas_no_banco"] == 1 and r2["inseridos"] == 0
    movs = (await db.execute(select(ConciliacaoMovimento))).scalars().all()
    assert len(movs) == 1 and movs[0].sub_tipo == "DEFERRED_RELEASED"
    assert movs[0].descricao.endswith("· liberado em 14/10")
    assert movs[0].raw["_liberado_em"].startswith("2026-10-14")
    assert movs[0].raw["_repeticao"] == "tx-proprio-liberado"
    # a rodada seguinte traz a mesma repetição: continua uma só, sem reescrever
    r3 = await run_amazon_sync(db, loja, cliente, agora=depois + timedelta(days=1))
    assert r3["duplicatas_no_banco"] == 1 and r3["inseridos"] == 0 and r3["atualizados"] == 0
    assert len((await db.execute(select(ConciliacaoMovimento))).scalars().all()) == 1


def test_etiqueta_no_carrinho_nao_e_frete_pago():
    """Regra de 16/10 (Vinicius): só etiqueta paga é frete pago. "pending" = no carrinho."""
    from app.services.conciliacao_melhor_envio import envio_para_frete, etiqueta_paga

    no_carrinho = _envio_me("e4", "301635", PROPRIO, status="pending")
    no_carrinho["paid_at"] = None
    no_carrinho["posted_at"] = None
    assert not etiqueta_paga(no_carrinho)
    assert envio_para_frete(no_carrinho)["frete_pago"] == Decimal("0")
    assert etiqueta_paga(_envio_me("e5", "301635", PROPRIO, status="released"))
    assert etiqueta_paga(_envio_me("e6", "301635", PROPRIO, status="delivered"))
    assert not etiqueta_paga(
        _envio_me("e7", "301635", PROPRIO, status="canceled", canceled_at="2026-10-15 12:00:00")
    )
    sem_status = _envio_me("e8", "301635", PROPRIO, status="")
    assert etiqueta_paga(sem_status)  # tem paid_at
    sem_status["paid_at"] = None
    assert not etiqueta_paga(sem_status)


async def test_melhor_envio_so_etiqueta_paga_conta_e_a_que_some_sai(db, make_user):
    """Regra de 16/10 (Vinicius): etiqueta no carrinho (pending) não é frete pago e, se
    for tirada do carrinho, não pode ficar um frete fantasma. Só paga entra; a que voltou
    a pending, foi cancelada ou sumiu da listagem sai; nº do Bling + id da Amazon
    conferidos; leitura cortada no teto de páginas avisa e não apaga nada."""
    from app.models import ConciliacaoFretePago
    from app.services import melhor_envio as me
    from app.services.conciliacao_melhor_envio import run_me_sync

    loja = await _loja_amazon(db, make_user)
    await run_amazon_sync(db, loja, _AmazonFalsa([_venda_proprio(), _venda_dba()]), agora=AGORA)
    await recalcular_loja(db, loja, agora=AGORA)
    await me.guardar_token_do_painel(db, "eyJ" + "t" * 40, por="teste", detalhe={})
    await db.commit()
    paginas: list[list[dict]] = [[]]

    async def _ler(token: str, *, page: int = 1, q=None):
        return 200, {"data": paginas[page - 1] if page <= len(paginas) else []}

    async def _ids() -> list[str]:
        rows = (await db.execute(select(ConciliacaoFretePago))).scalars().all()
        return sorted(f.envio_id for f in rows)

    no_carrinho = _envio_me("e2", "301943", DBA, status="pending")
    no_carrinho["paid_at"] = None
    no_carrinho["posted_at"] = None
    paginas[0] = [_envio_me("e1", "301635", PROPRIO), no_carrinho]
    r = await run_me_sync(db, loja, agora=AGORA, ler=_ler)
    assert (r["casados"], r["novos"], r["nao_pagos"], r["removidos"]) == (1, 1, 1, 0)
    assert "cortado" not in r and await _ids() == ["e1"]
    # 2ª leitura: e2 foi paga (released) e e1 foi cancelada → troca
    paginas[0] = [
        _envio_me("e1", "301635", PROPRIO, status="canceled", canceled_at="2026-10-15 12:00:00"),
        _envio_me("e2", "301943", DBA, status="released"),
    ]
    r = await run_me_sync(db, loja, agora=AGORA, ler=_ler)
    assert (r["novos"], r["nao_pagos"], r["removidos"]) == (1, 1, 1)
    assert await _ids() == ["e2"]
    # 3ª leitura: e2 sumiu da listagem (tirada do carrinho) → sai, nada fica fantasma
    paginas[0] = [_envio_me("e9", "999999", "000-0000000-0000000")]  # de outra loja
    r = await run_me_sync(db, loja, agora=AGORA, ler=_ler)
    assert r["removidos"] == 1 and await _ids() == []
    # nº do Bling de um pedido com o id da Amazon de outro: vale o id da Amazon
    paginas[0] = [_envio_me("e3", "301635", DBA)]
    r = await run_me_sync(db, loja, agora=AGORA, ler=_ler)
    assert r["novos"] == 1
    (f,) = (await db.execute(select(ConciliacaoFretePago))).scalars().all()
    assert f.envio_id == "e3" and f.pedido_bling == "301943" and f.pedido_marketplace == DBA
    # leitura cortada no teto de páginas (e3 ficou fora): avisa, não apaga
    paginas[:] = [
        [_envio_me("e9", "999999", "000-0000000-0000000")],
        [_envio_me("e9b", "999999", "000-0000000-0000000")],
        [_envio_me("e9c", "999999", "000-0000000-0000000")],
    ]
    r = await run_me_sync(db, loja, agora=AGORA, ler=_ler, max_paginas=2)
    assert r["cortado"] is True and r["paginas"] == 2 and r["removidos"] == 0
    assert r["aviso"] == "melhor envio: leitura cortada em 2 páginas"
    assert loja.ultimo_erro == "melhor envio: leitura cortada em 2 páginas"
    assert await _ids() == ["e3"]
    # erro HTTP no meio: também não apaga
    paginas[:] = [[_envio_me("e9", "999999", "000-0000000-0000000")]]

    async def _ler_quebrada(token: str, *, page: int = 1, q=None):
        return (200, {"data": paginas[0]}) if page == 1 else (500, {})

    r = await run_me_sync(db, loja, agora=AGORA, ler=_ler_quebrada)
    assert r["erro"] == "HTTP 500 na página 2" and r["removidos"] == 0
    assert await _ids() == ["e3"]


async def test_leitura_da_amazon_cortada_no_teto_de_paginas_avisa(db, make_user):
    """16/10, revisão: `ler_transacoes` parava em 30 páginas sem dizer nada. Agora o
    resumo traz `cortado`/`aviso`, o sync guarda o motivo e a loja o `ultimo_erro`."""
    loja = await _loja_amazon(db, make_user)
    cliente = _AmazonFalsa([_venda_proprio(), _venda_dba(), _repasse()], por_pagina=1)
    r = await run_amazon_sync(db, loja, cliente, agora=AGORA, max_paginas=2)
    assert r["cortado"] is True and r["lidas"] == 2 and r["inseridos"] == 2 and not r["erros"]
    assert r["aviso"] == (
        "amazon: leitura cortada em 2 páginas de transações "
        "(desde 2026-10-01; o que veio depois não entrou)"
    )
    assert loja.ultimo_erro == r["aviso"]
    sync = (await db.execute(select(ConciliacaoSync))).scalar_one()
    assert sync.status == "lido" and sync.erro == r["aviso"] and sync.detalhe["cortado"] is True
    # com páginas de sobra: leitura inteira, sem aviso, e o motivo some do sync
    r2 = await run_amazon_sync(db, loja, cliente, agora=AGORA)
    assert "cortado" not in r2 and "aviso" not in r2 and r2["inseridos"] == 1
    sync = (await db.execute(select(ConciliacaoSync))).scalar_one()
    assert sync.erro is None and sync.linhas == 3


async def test_upload_da_kfa_entra_na_trava_da_loja(db, make_user, auth_as, client, monkeypatch):
    """16/10, revisão: o CSV da KFA recalculava fora da trava da rodada; com a rodada no
    meio do caminho o envio responde 409 em vez de 500 (uq_conciliacao_pedido_loja)."""
    from sqlalchemy import text

    import app.db as _db
    from app.services import conciliacao_jobs as jobs
    from app.services.advisory_lock import SYNC_NAMESPACE

    monkeypatch.setattr(jobs, "ESPERA_TRAVA_CLIQUE", "300ms")
    loja = await _loja_amazon(db, make_user)
    loja.modo_extrato = "upload"
    await db.commit()
    auth_as(await make_user(permissions={"conciliacao": {"view": True, "edit": True}}))
    arquivo = ("transacoes.csv", CSV_KFA.encode("utf-8-sig"), "text/csv")
    async with _db.SessionLocal() as rodada:
        got = (
            await rodada.execute(
                text("SELECT pg_try_advisory_xact_lock(:ns, :key)"),
                {"ns": SYNC_NAMESPACE, "key": jobs._lock_key(loja.integration_id)},
            )
        ).scalar()
        assert got is True
        r = await client.post(
            "/api/conciliacao/amazon/upload", data={"loja": "kia"}, files={"arquivo": arquivo}
        )
        assert r.status_code == 409 and r.json()["detail"]["code"] == "rodada_em_andamento"
        await rodada.rollback()
    r = await client.post(
        "/api/conciliacao/amazon/upload", data={"loja": "kia"}, files={"arquivo": arquivo}
    )
    assert r.status_code == 200, r.text
    # sem a loja no formulário: 422, não a primeira loja da lista
    r = await client.post(
        "/api/conciliacao/amazon/upload", data={"loja": " "}, files={"arquivo": arquivo}
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "informe_a_loja"


async def test_amazon_preso_na_reserva_e_aguardando_ate_liberar(db, make_user):
    """16/10 (Vinicius: "a Amazon quando cai o pedido já está marcando como bateu?"):
    lançamento DEFERRED é dinheiro preso — "aguardando"; "bateu" só quando liberar, e a
    data da liberação é a de verdade (raw._liberado_em), não a do lançamento."""
    loja = await _loja_amazon(db, make_user)
    original = _venda_proprio(status="DEFERRED", posted="2026-10-06T14:00:00Z")
    cliente = _AmazonFalsa([original])
    await run_amazon_sync(db, loja, cliente, agora=AGORA)
    await recalcular_loja(db, loja, agora=AGORA)
    await db.commit()
    ped = (
        await db.execute(
            select(ConciliacaoPedido).where(ConciliacaoPedido.pedido_bling == "301635")
        )
    ).scalar_one()
    assert ped.situacao == "aguardando" and ped.etapa == "aguardando"
    assert ped.motivo == "preso na reserva da Amazon: 2356.21"
    assert ped.recebido is None and ped.liberado_em is None
    # "A liberar" do Resumo: o preso entra uma vez só (não soma de novo pela venda)
    from app.routers.conciliacao import _dinheiro_da_loja

    dinheiro = await _dinheiro_da_loja(db, loja)
    assert dinheiro["a_liberar"] == 2356.21 and dinheiro["a_liberar_pedidos"] == 1
    # a Amazon liberou (original vira DEFERRED_RELEASED + repetição RELEASED em 14/10)
    liberado = _venda_proprio(status="DEFERRED_RELEASED", posted="2026-10-06T14:00:00Z")
    repeticao = _venda_proprio(status="RELEASED", posted="2026-10-14T03:00:00Z")
    repeticao["transactionId"] = "tx-proprio-liberado"
    cliente.transacoes = [liberado, repeticao]
    await run_amazon_sync(db, loja, cliente, agora=AGORA)
    await recalcular_loja(db, loja, agora=AGORA)
    await db.commit()
    await db.refresh(ped)
    # envio próprio sem o frete do Melhor Envio: "falta frete" (seria "bateu" com ele)
    assert ped.situacao == "falta_frete" and ped.recebido == Decimal("2356.21")
    assert ped.liberado_em == datetime(2026, 10, 14, 3, tzinfo=UTC)


async def test_estorno_antes_de_liberar_nao_vira_bateu_e_a_margem_relida_nao_dobra_o_previsto(
    db, make_user
):
    """19/10 (revisão): a Margem soma TODAS as transações do pedido no líquido — o estorno
    e, depois da liberação, o lançamento repetido. kia 302142: lançou 749,53 preso,
    devolveu 759,53 ao cliente, liberou 749,53. Com o net da Margem o pedido ficaria
    "bateu" (−10,00 × −10,00) ou "recebido a menos" pelo previsto em dobro. O previsto da
    Conciliação é a conta da venda (Shipment + ServiceFee), e o estorno aparece."""
    from app.models import MarketplaceOrderFinancial

    loja = await _loja_amazon(db, make_user)
    quebras = [
        _b("ProductCharges", "729", [_b("OurPricePrincipal", "729")]),
        _b("Shipping", "164.06", [_b("ShippingPrincipal", "164.06")]),
        _b("PromoRebates", "-36.45", [_b("OurPriceDiscount", "-36.45")]),
        _b("AmazonFees", "-107.08", [_b("Commission", "-107.08")]),
    ]
    venda = _tx(
        "tx-e-venda",
        "Shipment",
        "DEFERRED",
        "2026-10-07T12:00:00Z",
        "749.53",
        pedido=DBA,
        quebras=quebras,
    )
    estorno = _tx(
        "tx-e-estorno", "Refund", "RELEASED", "2026-10-08T12:00:00Z", "-759.53", pedido=DBA
    )
    cliente = _AmazonFalsa([venda, estorno])
    await run_amazon_sync(db, loja, cliente, agora=AGORA)
    # a Margem leu o pedido depois do estorno: net = 749,53 − 759,53 = −10,00
    mof = MarketplaceOrderFinancial(
        integration_id=loja.integration_id,
        platform="amazon",
        external_order_id=DBA,
        status="posted",
        fetched_at=datetime(2026, 10, 9, 12, tzinfo=UTC),
        net_amount=Decimal("-10.00"),
        raw={"transactions": {"transactions": [venda, estorno]}},
    )
    db.add(mof)
    await db.commit()
    await recalcular_loja(db, loja, agora=AGORA)
    await db.commit()
    ped = (
        await db.execute(
            select(ConciliacaoPedido).where(ConciliacaoPedido.pedido_bling == "301943")
        )
    ).scalar_one()
    # preso na reserva: aguardando, e o previsto é a venda (749,53), não o net da Margem
    assert ped.situacao == "aguardando" and ped.venda_liquido == Decimal("749.53")
    assert ped.recebido is None and "preso na reserva" in ped.motivo
    # liberou: original vira DEFERRED_RELEASED + repetição RELEASED; a Margem relê e soma
    # original + estorno + repetição (net 739,53) — o previsto continua 749,53
    liberado = {**venda, "transactionStatus": "DEFERRED_RELEASED"}
    repeticao = {
        **venda,
        "transactionId": "tx-e-venda-lib",
        "transactionStatus": "RELEASED",
        "postedDate": "2026-10-15T03:00:00Z",
    }
    cliente.transacoes = [liberado, estorno, repeticao]
    await run_amazon_sync(db, loja, cliente, agora=AGORA)
    mof.net_amount = Decimal("739.53")
    mof.raw = {"transactions": {"transactions": [liberado, estorno, repeticao]}}
    await db.commit()
    await recalcular_loja(db, loja, agora=AGORA)
    await db.commit()
    await db.refresh(ped)
    assert ped.venda_liquido == Decimal("749.53")
    assert ped.situacao == "estornado_depois" and ped.etapa == "pendencia"
    assert ped.recebido == Decimal("-10.00") and ped.saidas_depois == Decimal("759.53")
    assert ped.dif_previsto_recebido == Decimal("-759.53")
    assert ped.motivo.startswith("estorno de 759.53 em 08/10")
    # a conta pura: Refund fora, repetição fora
    conta = conta_amazon_de_transacoes([liberado, estorno, repeticao])
    assert conta["liquido"] == Decimal("749.53") and conta["produto"] == Decimal("729")


async def test_linha_sem_pedido_da_kfa_reconhece_a_identidade_do_215f0494_e_tira_a_duplicata(
    db, make_user
):
    """16/10 à noite: a linha sem pedido (assinatura, publicidade) ganhou a data na
    identidade, mas as gravadas pelo 215f0494 (sem data) não eram reconhecidas e a
    mesma taxa entrou em dobro. Agora a antiga casa pelo legado e, se as duas existem,
    a antiga sai."""
    from app.services.conciliacao_shopee import inserir_movimentos

    loja = await _loja_amazon(db, make_user)
    loja.modo_extrato = "upload"
    await db.commit()
    assinatura = (
        '"07/10/2026","Liberado","Visa Crédito","Tarifas de serviço","---","Taxa de assinatura:",'
        '"0","0","-19","0","-19"'
    )
    csv = "\n".join([_CAB_KFA, assinatura])
    _cab, rows = ler_csv_amazon(csv)
    (tx,) = transacoes_do_csv(rows)
    novo_id, legado = tx["transactionId"], tx.pop("_idLegado")
    assert novo_id != legado
    m_novo = transacao_para_movimento(tx)
    m_velho = {**transacao_para_movimento(tx), "id_externo": legado}
    assert await inserir_movimentos(db, loja, [m_novo, m_velho]) == 2  # em dobro, como em prod
    await db.commit()
    r = await ingerir_csv_amazon(db, loja, csv, agora=datetime(2026, 10, 17, 9, tzinfo=UTC))
    assert r["duplicatas_apagadas"] == 1 and r["inseridos"] == 0
    movs = (await db.execute(select(ConciliacaoMovimento))).scalars().all()
    assert [m.id_externo for m in movs] == [novo_id]
    # só a antiga gravada: migra pro id novo, sem duplicar
    await db.delete(movs[0])
    await db.commit()
    assert (
        await inserir_movimentos(db, loja, [{**transacao_para_movimento(tx), "id_externo": legado}])
        == 1
    )
    await db.commit()
    r2 = await ingerir_csv_amazon(db, loja, csv, agora=datetime(2026, 10, 17, 10, tzinfo=UTC))
    assert r2["migrados"] == 1 and r2["inseridos"] == 0 and r2["duplicatas_apagadas"] == 0
    movs = (await db.execute(select(ConciliacaoMovimento))).scalars().all()
    assert [m.id_externo for m in movs] == [novo_id]
