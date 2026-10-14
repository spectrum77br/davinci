"""Conciliação, fase 2a: leitura do extrato e da fatura, recálculo e etapas.

Dados de exemplo copiados do que a sondagem de 14/10/2026 trouxe da jlas2
(`docs/conciliacao-avaliacao.md` §13): uma venda liberada em 10 parcelas, uma
mediação perdida com a devolução das tarifas, as reservas por reclamação e um
saque; cobranças CVVML/CVVPRC/CVVFN/CDSB e as bonificações de cancelamento.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import respx
from httpx import Response

from app.models import (
    BlingOrder,
    ConciliacaoLoja,
    ConciliacaoMovimento,
    ConciliacaoPedido,
    ConciliacaoSync,
    Integration,
    IntegrationPlatform,
    MarketplaceOrderFinancial,
    Refund,
    StoreInfo,
)
from app.security.cipher import encrypt_json
from app.services import conciliacao_calculo as calc
from app.services import conciliacao_extrato as ext
from app.services import conciliacao_fatura as fat

MP = "https://api.mercadopago.com"
ML = "https://api.mercadolibre.com"

CSV_JLAS2 = "\n".join(
    [
        "DATE,SOURCE_ID,EXTERNAL_REFERENCE,RECORD_TYPE,DESCRIPTION,NET_CREDIT_AMOUNT,NET_DEBIT_AMOUNT,"
        "GROSS_AMOUNT,SELLER_AMOUNT,MP_FEE_AMOUNT,FINANCING_FEE_AMOUNT,SHIPPING_FEE_AMOUNT,TAXES_AMOUNT,"
        "COUPON_AMOUNT,INSTALLMENTS,"
        "PAYMENT_METHOD,TAX_DETAIL,TRANSACTION_APPROVAL_DATE,CURRENCY,SHIPPING_ID,SHIPMENT_MODE,ORDER_ID,PACK_ID,"
        "METADATA,EFFECTIVE_COUPON_AMOUNT,OPERATION_TAGS,ITEM_ID,BALANCE_AMOUNT,PAYOUT_BANK_ACCOUNT_NUMBER,PRODUCT_SKU,"
        "SALE_DETAIL,PAYMENT_METHOD_TYPE,ORDER_MP,PURCHASE_ID,SHIPPING_ORDER_ID,TRANSACTION_DATE",
        "2026-10-01T00:00:00.000-03:00,,,initial_available_balance,,9147.81,0.00,9147.81,0.00,0.00,0.00,0.00,0.00,0.00,1,"
        ",,,,,,,,,,,,9147.81,,,,,,,,",
        "2026-10-01T06:57:55.000-03:00,181760519746,,release,reserve_for_payout,0.00,9147.00,-9147.00,0.00,0.00,0.00,"
        '0.00,0.00,0.00,1,available_money,,2026-10-01T06:57:55.000-03:00,BRL,,,,,"{}",0,,,0.81,,,,,,,,'
        "2026-10-01T06:57:55.000-03:00",
        "2026-10-01T06:57:57.000-03:00,181760519746,,release,payout,0.00,9147.00,-9147.00,0.00,0.00,0.00,0.00,0.00,"
        '0.00,1,available_money,,2026-10-01T06:57:57.000-03:00,BRL,,,,,"{}",0,,,0.81,,,,,,,,2026-10-01T06:57:57.000-03:00',
        "2026-10-01T08:11:09.000-03:00,177489533941,2000018405611066,release,payment,610.65,0.00,880.98,0.00,-92.36,"
        "-115.64,-62.33,0.00,0.00,10,master,,2026-09-11T10:32:45.000-03:00,BRL,47989874154,me2,2000018405611066,,"
        '"{"fee_discount": "2.85", "payments_group_uuid": "d76a453e"}",0.00,'
        '"[{"logistic_type":"drop_off"}]",MLB4708662793,611.46,,dg057.ci+a001.ci,'
        '"""Caap uranyx""",credit_card,,,,2026-09-11T10:32:33.000-03:00',
        "2026-10-01T23:00:44.000-03:00,180879957389,2000018737867860,release,mediation,0.00,732.35,-732.35,0.00,0.00,"
        '0.00,0.00,0.00,0.00,1,pix,,2026-09-25T10:00:00.000-03:00,BRL,48145485009,me2,2000018737867860,,"{}",0.00,,'
        "MLB1,0.00,,sku-x,,account_money,,,,2026-09-25T10:00:00.000-03:00",
        "2026-10-01T23:00:49.000-03:00,180879957389,2000018737867860,release,refund,116.81,0.00,0.00,0.00,92.36,0.00,"
        '24.45,0.00,0.00,1,pix,,2026-09-25T10:00:00.000-03:00,BRL,48145485009,me2,2000018737867860,,"{}",0.00,,MLB1,'
        "116.81,,sku-x,,account_money,,,,2026-09-25T10:00:00.000-03:00",
        "2026-10-02T11:16:33.000-03:00,176527200959,2000018302114106,release,reserve_for_dispute,0.00,611.46,-611.46,"
        '0.00,0.00,0.00,0.00,0.00,0.00,1,pix,,2026-09-20T10:00:00.000-03:00,BRL,47942391760,me2,2000018302114106,,"{}",'
        "0.00,,MLB5151221221,0.00,,sku-y,,account_money,,,,2026-09-20T10:00:00.000-03:00",
        ",,,total,,2591.22,0.00,11113.46,0.00,-4704.07,-1769.96,-1616.21,0.00,3949.68,1,,,,,,,,,,,,,0.00,,,,,,,,",
    ]
)


async def _loja(
    db, make_user, *, inicio: date = date(2026, 10, 1)
) -> tuple[ConciliacaoLoja, Integration]:
    dono = await make_user()
    integ = Integration(
        user_id=dono.id,
        platform=IntegrationPlatform.ML,
        name="jlas2",
        status="active",
        credentials=encrypt_json(
            {"access_token": "tok", "refresh_token": "rt", "expires_at": int(time.time()) + 3600}
        ),
    )
    db.add(integ)
    await db.flush()
    db.add(
        StoreInfo(user_id=dono.id, platform="ml", account_name="jlas2", bling_store_id="204000001")
    )
    loja = ConciliacaoLoja(
        integration_id=integ.id, platform=IntegrationPlatform.ML, conta="jlas2", inicio=inicio
    )
    db.add(loja)
    await db.commit()
    return loja, integ


def _pedido_bling(
    numero: str, numeroloja: str, data: datetime, total: str, custo: float, loja="204000001"
) -> BlingOrder:
    return BlingOrder(
        bling_id=int(numero) * 10,
        numero=numero,
        numeroloja=numeroloja,
        data=data,
        total=Decimal(total),
        totalprodutos=Decimal(total),
        situacao="Atendido",
        loja=loja,
        item_index=0,
        preco_custo=custo,
        item_quantidade=1,
        item_codigo="dg057.ci",
    )


class _ClienteFalso:
    """Cliente ML/MP de mentira: só o que a conciliação usa."""

    access_token = "tok"  # noqa: S105 — chave de mentira do teste

    def __init__(
        self, paginas: dict[tuple[str, str, int], dict] | None = None, resumo: dict | None = None
    ):
        self.paginas = paginas or {}
        self.resumo = resumo or {"bill_includes": {"total_amount": 1}}
        self.chamadas: list[tuple[str, dict | None]] = []

    def _expired(self) -> bool:
        return False

    async def refresh(self) -> None:  # pragma: no cover
        raise AssertionError("a conciliação nunca renova token na mão")

    async def _request(self, method: str, path: str, *, params=None, json=None):
        self.chamadas.append((path, params))
        if path.endswith("/summary/details"):
            return Response(200, json=self.resumo)
        chave = path.split("/key/")[1].split("/")[0]
        pagina = self.paginas.get(
            (chave, params["detail_type"], params["from_id"]), {"results": [], "last_id": 0}
        )
        return Response(200, json=pagina)


# --- extrato -----------------------------------------------------------------


def test_linha_para_movimento_normaliza_tipos_e_valores():
    _cab, rows, _sep = ext.ler_csv_mp(CSV_JLAS2)
    assert ext.linha_para_movimento(rows[0]) is None  # saldo inicial não é movimento
    assert ext.linha_para_movimento(rows[-1]) is None  # total também não
    venda = ext.linha_para_movimento(rows[3])
    assert venda["tipo"] == "venda_liberada"
    assert venda["pedido_marketplace"] == "2000018405611066"
    assert venda["pagamento_id"] == "177489533941"
    assert venda["credito"] == Decimal("610.65") and venda["debito"] == Decimal("0.00")
    assert venda["bruto"] == Decimal("880.98") and venda["tarifa"] == Decimal("-92.36")
    assert venda["parcelamento"] == Decimal("-115.64") and venda["frete"] == Decimal("-62.33")
    assert venda["saldo_apos"] == Decimal("611.46") and venda["sku"] == "dg057.ci+a001.ci"
    assert venda["data_aprovacao"].date() == date(2026, 9, 11)
    saque = ext.linha_para_movimento(rows[2])
    assert (
        saque["tipo"] == "saque"
        and saque["classificacao"] == "saque"
        and saque["pedido_marketplace"] is None
    )
    assert ext.linha_para_movimento(rows[4])["tipo"] == "mediacao"
    assert ext.linha_para_movimento(rows[5])["tipo"] == "estorno"
    assert ext.linha_para_movimento(rows[6])["tipo"] == "reserva_disputa"
    # identidade estável e diferente por linha
    ids = {ext.linha_para_movimento(r)["id_externo"] for r in rows[1:-1]}
    assert len(ids) == 6


def test_janela_utc_cobre_o_dia_inteiro_em_brt():
    assert ext.janela_utc(date(2026, 10, 1), date(2026, 10, 14)) == {
        "begin_date": "2026-10-01T03:00:00Z",
        "end_date": "2026-10-15T02:59:59Z",
    }


async def test_ingerir_csv_resolve_pedido_bling_e_nao_duplica(db, make_user):
    loja, _integ = await _loja(db, make_user)
    db.add(
        _pedido_bling(
            "301102", "2000018405611066", datetime(2026, 9, 11, 13, tzinfo=UTC), "880.98", 500.0
        )
    )
    await db.commit()
    stats = await ext.ingerir_csv(db, loja, CSV_JLAS2)
    assert stats["movimentos"] == 6 and stats["inseridos"] == 6
    assert stats["saldo_inicial"] == "9147.81" and stats["saldo_final"] == "2591.22"
    assert stats["pedido_bling_resolvido"] == 1
    de_novo = await ext.ingerir_csv(db, loja, CSV_JLAS2)
    assert de_novo["inseridos"] == 0 and de_novo["ja_existiam"] == 6
    venda = (
        await db.execute(
            __import__("sqlalchemy")
            .select(ConciliacaoMovimento)
            .where(ConciliacaoMovimento.tipo == "venda_liberada")
        )
    ).scalar_one()
    assert venda.pedido_bling == "301102" and venda.bling_id == 3011020


async def test_run_extrato_sync_pede_janela_e_depois_le_o_arquivo(db, make_user):
    loja, integ = await _loja(db, make_user)
    cliente = _ClienteFalso()
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{MP}/v1/account/release_report/config").mock(
            return_value=Response(404, json={})
        )
        router.post(f"{MP}/v1/account/release_report/config").mock(
            return_value=Response(201, json={})
        )
        router.post(f"{MP}/v1/account/release_report").mock(
            return_value=Response(202, json={"status": "pending"})
        )
        router.get(f"{MP}/v1/account/release_report/list").mock(return_value=Response(200, json=[]))
        r1 = await ext.run_extrato_sync(db, loja, cliente, hoje=date(2026, 10, 15))
    assert r1["pedido"] == {"janela": "2026-10-01..2026-10-14", "http": 202}
    assert r1["config"]["status"] == "criada" and loja.extrato_config_em is not None
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{MP}/v1/account/release_report/list").mock(
            return_value=Response(
                200,
                json=[
                    {
                        "id": 1,
                        "begin_date": "2026-10-01T03:00:00Z",
                        "end_date": "2026-10-15T02:59:59Z",
                        "file_name": "davinci-liberacoes-1.csv",
                        "status": "enabled",
                    }
                ],
            )
        )
        router.get(f"{MP}/v1/account/release_report/davinci-liberacoes-1.csv").mock(
            return_value=Response(200, headers={"content-type": "text/csv"}, text=CSV_JLAS2)
        )
        router.post(f"{MP}/v1/account/release_report").mock(return_value=Response(202, json={}))
        r2 = await ext.run_extrato_sync(db, loja, cliente, hoje=date(2026, 10, 15))
    assert len(r2["lidos"]) == 1 and r2["lidos"][0]["inseridos"] == 6
    assert r2["pedido"] is None  # ontem (14/10) já está coberto: nada novo a pedir
    syncs = (await db.execute(__import__("sqlalchemy").select(ConciliacaoSync))).scalars().all()
    assert [(s.chave, s.status, s.linhas) for s in syncs] == [("2026-10-01..2026-10-14", "lido", 8)]
    # dia seguinte: pede só o dia 15
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{MP}/v1/account/release_report/list").mock(return_value=Response(200, json=[]))
        router.post(f"{MP}/v1/account/release_report").mock(return_value=Response(202, json={}))
        r3 = await ext.run_extrato_sync(db, loja, cliente, hoje=date(2026, 10, 16))
    assert r3["pedido"]["janela"] == "2026-10-15..2026-10-15"


# --- fatura ------------------------------------------------------------------


def _det(
    detail_id: int,
    sub: str,
    valor: float,
    *,
    tipo="CHARGE",
    debited="YES",
    order=2000018600988674,
    data="2026-09-23T00:56:49",
):
    return {
        "charge_info": {
            "detail_id": detail_id,
            "transaction_detail": "x",
            "debited_from_operation": debited,
            "detail_amount": valor,
            "detail_type": tipo,
            "detail_sub_type": sub,
            "creation_date_time": data,
            "charge_bonified_id": 1 if tipo == "BONUS" else None,
        },
        "sales_info": [
            {
                "order_id": order,
                "operation_id": 180437868186,
                "sale_date_time": "2026-09-22T23:56:34",
                "transaction_amount": 1054.39,
            }
        ]
        if order
        else None,
        "shipping_info": {"shipping_id": "48081225586", "pack_id": None} if order else None,
        "items_info": [{"item_id": "MLB6836732464", "order_id": order}] if order else None,
    }


def test_detalhe_para_movimento_mapeia_subtipos():
    tarifa = fat.detalhe_para_movimento(_det(1, "CVVML", 82.77))
    assert (
        tarifa["tipo"] == "fatura_tarifa_venda"
        and tarifa["debito"] == Decimal("82.77")
        and tarifa["credito"] == Decimal("0.00")
    )
    assert (
        tarifa["descontado_da_venda"] is True and tarifa["pedido_marketplace"] == "2000018600988674"
    )
    assert tarifa["pagamento_id"] == "180437868186" and tarifa["shipping_id"] == "48081225586"
    frete = fat.detalhe_para_movimento(_det(2, "CDSB", 27.9, debited="NO"))
    assert (
        frete["tipo"] == "fatura_frete"
        and frete["frete"] == Decimal("27.90")
        and frete["descontado_da_venda"] is False
    )
    assert fat.detalhe_para_movimento(_det(3, "CVVFN", 72.69))["tipo"] == "fatura_parcelamento"
    ads = fat.detalhe_para_movimento(_det(4, "PADS", 11.39, debited="NO", order=None))
    assert (
        ads["tipo"] == "fatura_publicidade"
        and ads["pedido_marketplace"] is None
        and ads["classificacao"] == "publicidade"
    )
    bonus = fat.detalhe_para_movimento(
        _det(5, "BVVML", 158.41, tipo="BONUS", debited="INAPPLICABLE")
    )
    assert (
        bonus["tipo"] == "fatura_bonificacao"
        and bonus["credito"] == Decimal("158.41")
        and bonus["debito"] == Decimal("0.00")
    )
    assert (
        fat.chave_periodo(date(2026, 10, 14)) == "2026-10-01"
        and fat.periodo_anterior("2026-01-01") == "2025-12-01"
    )


async def test_run_fatura_sync_le_incremental_e_respeita_a_pausa(db, make_user):
    loja, _integ = await _loja(db, make_user)
    paginas = {
        ("2026-10-01", "CHARGE", 0): {
            "results": [_det(10, "CVVML", 82.77), _det(11, "CVVPRC", 27.59)],
            "last_id": 11,
        },
        ("2026-10-01", "BONUS", 0): {"results": [], "last_id": 0},
        ("2026-09-01", "CHARGE", 0): {
            "results": [_det(5, "CDSB", 27.9, debited="NO", data="2026-08-23T05:33:33")],
            "last_id": 5,
        },
        ("2026-09-01", "BONUS", 0): {
            "results": [_det(6, "BVVML", 158.41, tipo="BONUS", data="2026-08-25T11:25:49")],
            "last_id": 6,
        },
    }
    cliente = _ClienteFalso(paginas)
    pausas: list[float] = []

    async def dormir(s: float) -> None:
        pausas.append(s)

    r = await fat.run_fatura_sync(
        db, loja, cliente, hoje=date(2026, 10, 14), pausa=13.0, dormir=dormir
    )
    assert r["inseridos"] == 4 and r["chamadas"] == 5 and not r["erros"]
    assert pausas == [13.0] * 4  # dorme entre as chamadas, nunca antes da primeira
    assert r["periodos"]["2026-10-01|CHARGE"] == {"lidos": 2, "inseridos": 2, "last_id": 11}
    # segunda rodada: começa do last_id (nada novo) e não insere de novo
    cliente2 = _ClienteFalso(paginas)
    r2 = await fat.run_fatura_sync(
        db, loja, cliente2, hoje=date(2026, 10, 14), pausa=0, dormir=dormir
    )
    assert r2["inseridos"] == 0
    assert any(
        p == ("2026-10-01", "CHARGE", 11)
        for p in [
            (c[0].split("/key/")[1].split("/")[0], c[1]["detail_type"], c[1]["from_id"])
            for c in cliente2.chamadas
            if c[1] and "detail_type" in c[1]
        ]
    )


# --- recálculo ---------------------------------------------------------------


def _fin(
    integration_id,
    numeroloja: str,
    *,
    gross="880.98",
    fee="92.36",
    freight="62.33",
    net="610.65",
    refund=None,
    status="posted",
    raw=None,
):
    return MarketplaceOrderFinancial(
        platform=IntegrationPlatform.ML,
        integration_id=integration_id,
        external_order_id=numeroloja,
        pedido_bling=None,
        status=status,
        gross_amount=Decimal(gross),
        fee_amount=Decimal(fee),
        freight_amount=Decimal(freight),
        net_amount=Decimal(net),
        refund_amount=Decimal(refund) if refund else None,
        raw=raw or {},
    )


_CONTADOR = [0]


def _mov(
    loja,
    tipo,
    data,
    *,
    credito="0",
    debito="0",
    pedido_mkt=None,
    pedido_bling=None,
    origem="mp_extrato",
    frete=None,
    parcelamento=None,
    tarifa=None,
    sub=None,
):
    _CONTADOR[0] += 1
    return ConciliacaoMovimento(
        integration_id=loja.integration_id,
        platform=loja.platform,
        origem=origem,
        id_externo=f"m{_CONTADOR[0]}",
        data=data,
        tipo=tipo,
        descricao=tipo,
        sub_tipo=sub,
        pedido_marketplace=pedido_mkt,
        pedido_bling=pedido_bling,
        credito=Decimal(credito),
        debito=Decimal(debito),
        frete=Decimal(frete) if frete is not None else None,
        parcelamento=Decimal(parcelamento) if parcelamento is not None else None,
        tarifa=Decimal(tarifa) if tarifa is not None else None,
        raw={},
    )


async def test_recalcular_loja_classifica_as_situacoes(db, make_user):
    loja, integ = await _loja(db, make_user)
    agora = datetime(2026, 10, 15, 12, tzinfo=UTC)
    d = lambda dia, h=10: datetime(2026, 10, dia, h, tzinfo=UTC)  # noqa: E731
    db.add_all(
        [
            # bateu: 610,65 previsto, 610,65 liberado
            _pedido_bling("301102", "2000018405611066", d(1), "880.98", 500.0),
            # recebido a menos: frete cobrado 63,40 × previsto 35,65
            _pedido_bling("300991", "2000018600988674", d(2), "349.90", 150.0),
            # aguardando (sem liberação, previsão à frente)
            _pedido_bling("299012", "2000018999", d(3), "129.90", 62.0),
            # estornado depois: liberou 361,38 e saiu mediação 459,00
            _pedido_bling("299520", "2000018737867860", d(4), "459.00", 210.0),
            # Bling diferente: dinheiro bate, mas o Bling diz 1.299 e a venda 1.199
            _pedido_bling("299377", "2000018888", d(5), "1299.00", 690.0),
            # anterior ao início: não entra (setembro)
            _pedido_bling(
                "290000", "2000017000", datetime(2026, 9, 20, tzinfo=UTC), "100.00", 50.0
            ),
        ]
    )
    db.add_all(
        [
            _fin(integ.id, "2000018405611066"),
            _fin(
                integ.id,
                "2000018600988674",
                gross="349.90",
                fee="46.13",
                freight="35.65",
                net="268.12",
            ),
            _fin(
                integ.id,
                "2000018999",
                gross="129.90",
                fee="17.14",
                freight="11.54",
                net="101.22",
                status="estimated",
                raw={
                    "billing": {
                        "results": [
                            {"payment_info": [{"money_release_date": "2026-10-25T10:00:00"}]}
                        ]
                    }
                },
            ),
            _fin(
                integ.id,
                "2000018737867860",
                gross="459.00",
                fee="60.59",
                freight="37.03",
                net="361.38",
            ),
            _fin(integ.id, "2000018888", gross="1199.00", fee="158.27", freight="0", net="1040.73"),
        ]
    )
    db.add_all(
        [
            _mov(
                loja,
                "venda_liberada",
                d(10),
                credito="610.65",
                pedido_mkt="2000018405611066",
                frete="-62.33",
                tarifa="-92.36",
                parcelamento="-115.64",
            ),
            _mov(
                loja,
                "venda_liberada",
                d(11),
                credito="240.37",
                pedido_mkt="2000018600988674",
                frete="-63.40",
                tarifa="-46.13",
            ),
            _mov(
                loja, "reserva_disputa", d(11, 11), debito="240.37", pedido_mkt="2000018600988674"
            ),
            _mov(
                loja,
                "venda_liberada",
                d(8),
                credito="361.38",
                pedido_mkt="2000018737867860",
                frete="-37.03",
                tarifa="-60.59",
            ),
            _mov(loja, "mediacao", d(12), debito="459.00", pedido_mkt="2000018737867860"),
            _mov(loja, "estorno", d(12, 11), credito="97.62", pedido_mkt="2000018737867860"),
            _mov(
                loja,
                "venda_liberada",
                d(9),
                credito="1040.73",
                pedido_mkt="2000018888",
                tarifa="-158.27",
            ),
            _mov(
                loja,
                "fatura_frete",
                d(2),
                debito="63.40",
                pedido_mkt="2000018600988674",
                origem="ml_fatura",
                sub="CDSB",
            ),
            # pedido de setembro: linha sem pedido_bling vira "anterior ao início"
            _mov(
                loja,
                "mediacao",
                d(13),
                debito="100.00",
                pedido_mkt="2000017000",
                pedido_bling="290000",
            ),
        ]
    )
    db.add(Refund(pedido_bling="299377", conta="jlas2", reembolso=20.0))
    await db.commit()

    r = await calc.recalcular_loja(db, loja, agora=agora)
    await db.commit()
    assert r["pedidos"] == 5
    pedidos = {
        p.pedido_bling: p
        for p in (await db.execute(__import__("sqlalchemy").select(ConciliacaoPedido))).scalars()
    }
    assert set(pedidos) == {"301102", "300991", "299012", "299520", "299377"}

    bateu = pedidos["301102"]
    assert bateu.situacao == "bateu" and bateu.etapa == "conciliado" and bateu.bateu_em == agora
    assert bateu.recebido == Decimal("610.65") and bateu.liberado_em == d(10)
    assert (
        bateu.custo == Decimal("500.00")
        and bateu.sobrou == Decimal("110.65")
        and bateu.margem == Decimal("0.2213")
    )

    menos = pedidos["300991"]
    assert menos.situacao == "recebido_a_menos" and menos.etapa == "pendencia"
    assert menos.dif_previsto_recebido == Decimal("-27.75")
    assert "frete cobrado 63.40 × previsto 35.65" in (menos.motivo or "")
    assert menos.fatura_frete == Decimal("63.40")
    assert menos.recebido == Decimal("240.37")  # a reserva não conta como saída

    esperando = pedidos["299012"]
    assert esperando.situacao == "aguardando" and esperando.etapa == "aguardando"
    assert esperando.previsao_liberacao is not None and "libera em 25/10" in esperando.motivo
    assert esperando.sobrou == Decimal("39.22")  # previsto − custo, enquanto não libera

    estornado = pedidos["299520"]
    assert estornado.situacao == "estornado_depois" and estornado.etapa == "pendencia"
    assert estornado.recebido == Decimal("0.00") and estornado.saidas_depois == Decimal("459.00")
    assert estornado.sobrou == Decimal("-210.00")

    bling = pedidos["299377"]
    assert bling.situacao == "bling_diferente" and bling.dif_bling_venda == Decimal("100.00")
    assert bling.reembolso == Decimal("20.00") and bling.sobrou == Decimal("330.73")

    antigo = (
        await db.execute(
            __import__("sqlalchemy")
            .select(ConciliacaoMovimento)
            .where(ConciliacaoMovimento.pedido_bling == "290000")
        )
    ).scalar_one()
    assert antigo.classificacao == "anterior_ao_inicio"


def test_etapa_finaliza_sozinha_em_7_dias_e_reabre_com_movimento_novo():
    agora = datetime(2026, 10, 15, 12, tzinfo=UTC)
    # bateu há 8 dias, nada depois → finaliza sozinho
    atual = ConciliacaoPedido(
        etapa="conciliado", bateu_em=agora - timedelta(days=8), etapa_em=agora - timedelta(days=8)
    )
    e = calc.etapa_do_pedido(
        agora=agora, atual=atual, situacao="bateu", ultimo_movimento_em=agora - timedelta(days=8)
    )
    assert e["etapa"] == "finalizado" and e["etapa_por"] == "robô" and "7 dias" in e["etapa_motivo"]
    # bateu há 3 dias → continua conciliado
    atual = ConciliacaoPedido(
        etapa="conciliado", bateu_em=agora - timedelta(days=3), etapa_em=agora - timedelta(days=3)
    )
    assert (
        calc.etapa_do_pedido(agora=agora, atual=atual, situacao="bateu", ultimo_movimento_em=None)[
            "etapa"
        ]
        == "conciliado"
    )
    # finalizado e chegou movimento depois → reaberto
    atual = ConciliacaoPedido(
        etapa="finalizado", bateu_em=agora - timedelta(days=20), etapa_em=agora - timedelta(days=10)
    )
    e = calc.etapa_do_pedido(
        agora=agora,
        atual=atual,
        situacao="estornado_depois",
        ultimo_movimento_em=agora - timedelta(days=1),
    )
    assert e["etapa"] == "reaberto" and "movimento novo" in e["etapa_motivo"]
    # finalizado sem novidade fica finalizado mesmo que a situação mude de nome
    atual = ConciliacaoPedido(
        etapa="finalizado", bateu_em=agora - timedelta(days=20), etapa_em=agora - timedelta(days=10)
    )
    assert (
        calc.etapa_do_pedido(
            agora=agora,
            atual=atual,
            situacao="bateu",
            ultimo_movimento_em=agora - timedelta(days=15),
        )["etapa"]
        == "finalizado"
    )
    # pendência com reaberto continua reaberto até bater
    atual = ConciliacaoPedido(
        etapa="reaberto", bateu_em=agora - timedelta(days=20), etapa_em=agora - timedelta(days=1)
    )
    assert (
        calc.etapa_do_pedido(
            agora=agora, atual=atual, situacao="recebido_a_menos", ultimo_movimento_em=agora
        )["etapa"]
        == "reaberto"
    )
    # novo pedido aguardando
    assert (
        calc.etapa_do_pedido(
            agora=agora, atual=None, situacao="aguardando", ultimo_movimento_em=None
        )["etapa"]
        == "aguardando"
    )


# --- status ------------------------------------------------------------------


async def test_status_lista_a_loja_ligada(db, make_user, auth_as, client):
    loja, _integ = await _loja(db, make_user)
    db.add(_mov(loja, "saque", datetime(2026, 10, 1, tzinfo=UTC), debito="9147.00"))
    await db.commit()
    auth_as(await make_user(permissions={"margem": {"view": True}}))
    r = await client.get("/api/conciliacao/status")
    assert r.status_code == 200, r.text
    lojas = r.json()["lojas"]
    assert lojas[0]["conta"] == "jlas2" and lojas[0]["inicio"] == "2026-10-01"
    assert lojas[0]["movimentos"] == [
        {"origem": "mp_extrato", "tipo": "saque", "linhas": 1, "credito": "0", "debito": "9147.00"}
    ]
