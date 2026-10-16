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
SEM_BLING = "2000018499999999"  # venda do ML que não existe no Bling

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
    # dia seguinte: pede só o dia 15 (e confere a config de novo, 1×/dia: completa, nada a mudar)
    loja.extrato_config_em = datetime.now(UTC) - timedelta(days=1)
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{MP}/v1/account/release_report/config").mock(
            return_value=Response(200, json=CONFIG_COMPLETA)
        )
        router.get(f"{MP}/v1/account/release_report/list").mock(return_value=Response(200, json=[]))
        router.post(f"{MP}/v1/account/release_report").mock(return_value=Response(202, json={}))
        r3 = await ext.run_extrato_sync(db, loja, cliente, hoje=date(2026, 10, 16))
    # folga de um dia: pede de novo o último dia lido (14) junto com o 15; repetido é ignorado
    assert r3["pedido"]["janela"] == "2026-10-14..2026-10-15"
    assert r3["config"]["status"] == "existia"


CONFIG_COMPLETA = {"columns": [{"key": c} for c in ext.COLUNAS]}
# 16/10: a aguiar tinha uma config antiga de 11 colunas (sem ORDER_ID) — 14 pagamentos
# "sem dono" porque o relatório não dizia de que venda eram.
CONFIG_POBRE = {
    "columns": [
        {"key": c}
        for c in (
            "DATE",
            "SOURCE_ID",
            "RECORD_TYPE",
            "DESCRIPTION",
            "NET_CREDIT_AMOUNT",
            "NET_DEBIT_AMOUNT",
            "GROSS_AMOUNT",
            "BALANCE_AMOUNT",
            "PAYMENT_METHOD",
            "BUSINESS_UNIT",
        )
    ]
}
# Como o da aguiar: sem RECORD_TYPE, sem ORDER_ID, sem tarifas.
CSV_POBRE = "\n".join(
    [
        "DATE,SOURCE_ID,DESCRIPTION,NET_CREDIT_AMOUNT,NET_DEBIT_AMOUNT,GROSS_AMOUNT,"
        "BALANCE_AMOUNT,PAYMENT_METHOD,BUSINESS_UNIT",
        "2026-10-01T06:57:55.000-03:00,181760519746,reserve_for_payout,0.00,9147.00,-9147.00,"
        "0.81,available_money,Mercado Libre",
        "2026-10-01T06:57:57.000-03:00,181760519746,payout,0.00,9147.00,-9147.00,0.81,"
        "available_money,Mercado Libre",
        "2026-10-01T08:11:09.000-03:00,177489533941,payment,610.65,0.00,880.98,611.46,"
        "master,Mercado Libre",
        "2026-10-01T23:00:44.000-03:00,180879957389,mediation,0.00,732.35,-732.35,0.00,"
        "pix,Mercado Libre",
        "2026-10-01T23:00:49.000-03:00,180879957389,refund,116.81,0.00,0.00,116.81,"
        "pix,Mercado Libre",
        "2026-10-02T11:16:33.000-03:00,176527200959,reserve_for_dispute,0.00,611.46,"
        "-611.46,0.00,pix,Mercado Libre",
    ]
)


async def test_garantir_config_completa_as_colunas_e_rele_desde_o_inicio(db, make_user):
    loja, _integ = await _loja(db, make_user)
    loja.extrato_config_em = datetime(2026, 10, 2, tzinfo=UTC)
    db.add(
        ConciliacaoSync(
            integration_id=loja.integration_id,
            origem="mp_extrato",
            chave="2026-10-01..2026-10-13",
            status="lido",
            linhas=10,
        )
    )
    await db.flush()
    cliente = _ClienteFalso()
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{MP}/v1/account/release_report/config").mock(
            return_value=Response(200, json=CONFIG_POBRE)
        )
        put = router.put(f"{MP}/v1/account/release_report/config").mock(
            return_value=Response(200, json={})
        )
        router.get(f"{MP}/v1/account/release_report/list").mock(return_value=Response(200, json=[]))
        router.post(f"{MP}/v1/account/release_report").mock(return_value=Response(202, json={}))
        r = await ext.run_extrato_sync(db, loja, cliente, hoje=date(2026, 10, 15))
    assert r["config"]["status"] == "atualizada" and "ORDER_ID" in r["config"]["faltavam"]
    enviado = __import__("json").loads(put.calls[0].request.content)
    assert [c["key"] for c in enviado["columns"]] == ext.COLUNAS
    # os relatórios lidos com a config capenga são esquecidos: pede tudo desde o início
    assert r["reler_desde"] == "2026-10-01"
    assert r["pedido"]["janela"] == "2026-10-01..2026-10-14"
    syncs = (await db.execute(__import__("sqlalchemy").select(ConciliacaoSync))).scalars().all()
    assert [(s.chave, s.status) for s in syncs] == [("2026-10-01..2026-10-14", "pedido")]


async def test_garantir_config_nao_mexe_quando_a_conta_so_aceita_o_minimo(db, make_user):
    cliente = _ClienteFalso()
    minima = {"columns": [{"key": c} for c in ext.COLUNAS_MINIMO]}
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{MP}/v1/account/release_report/config").mock(
            return_value=Response(200, json=minima)
        )
        router.put(f"{MP}/v1/account/release_report/config").mock(
            return_value=Response(400, json={"message": "invalid columns"})
        )
        r = await ext.garantir_config(cliente)
    assert r["status"] == "existia_minima"


async def test_ingerir_csv_completa_as_linhas_de_um_relatorio_pobre(db, make_user):
    loja, _integ = await _loja(db, make_user)
    db.add(
        _pedido_bling(
            "301102", "2000018405611066", datetime(2026, 9, 11, 13, tzinfo=UTC), "880.98", 500.0
        )
    )
    await db.commit()
    pobre = await ext.ingerir_csv(db, loja, CSV_POBRE)
    assert pobre["inseridos"] == 6 and pobre["sem_pedido"] == 6
    completo = await ext.ingerir_csv(db, loja, CSV_JLAS2)
    # nenhuma linha nova: as 6 já existiam e foram completadas (todas trocaram de
    # identidade: o hash inclui RECORD_TYPE e ORDER_ID, que o pobre não tinha)
    assert completo["inseridos"] == 0 and completo["atualizados"] == 6
    assert completo["reidentificados"] == 6 and completo["duplicatas_apagadas"] == 0
    todas = (
        (await db.execute(__import__("sqlalchemy").select(ConciliacaoMovimento))).scalars().all()
    )
    assert len(todas) == 6
    venda = next(m for m in todas if m.tipo == "venda_liberada")
    assert venda.pedido_marketplace == "2000018405611066" and venda.pedido_bling == "301102"
    assert venda.tarifa == Decimal("-92.36") and venda.sku == "dg057.ci+a001.ci"
    assert venda.raw["BUSINESS_UNIT"] == "Mercado Libre" and venda.raw["ORDER_ID"]
    assert venda.sub_tipo == "release"  # o pobre não tinha RECORD_TYPE; o completo preenche
    saque = next(m for m in todas if m.tipo == "saque")
    assert saque.classificacao == "saque"


def test_linha_para_movimento_reconhece_a_cobranca_da_fatura_e_a_divida():
    base = {
        "DATE": "2026-10-15T01:29:31.000-03:00",
        "SOURCE_ID": "000gs6qwm7",
        "RECORD_TYPE": "release",
        "NET_CREDIT_AMOUNT": "0.00",
        "NET_DEBIT_AMOUNT": "377.27",
        "GROSS_AMOUNT": "-377.27",
        "PAYMENT_METHOD": "available_money",
    }
    cobranca = ext.linha_para_movimento(
        {
            **base,
            "DESCRIPTION": "payment",
            "EXTERNAL_REFERENCE": "MELIPAYMENTS-COLLECTIONATTEMPT-3392363225-20261010T011705466",
        }
    )
    assert cobranca["tipo"] == "fatura_cobrada" and cobranca["classificacao"] == "fatura_ml"
    assert cobranca["pedido_marketplace"] is None and cobranca["debito"] == Decimal("377.27")
    divida = ext.linha_para_movimento({**base, "DESCRIPTION": "reserve_for_debt_payment"})
    assert divida["tipo"] == "cobranca_divida" and divida["classificacao"] == "fatura_ml"
    venda = ext.linha_para_movimento(
        {**base, "DESCRIPTION": "payment", "ORDER_ID": "2000018405611066"}
    )
    assert venda["tipo"] == "venda_liberada" and venda["classificacao"] is None


async def test_recalcular_loja_classifica_cobranca_da_fatura_antiga_e_venda_desfeita(db, make_user):
    loja, _integ = await _loja(db, make_user)
    agora = datetime(2026, 10, 15, 12, tzinfo=UTC)
    d = lambda dia, h=10: datetime(2026, 10, dia, h, tzinfo=UTC)  # noqa: E731
    # gravadas antes da regra: cobrança da fatura como "venda" com a referência no nº
    # da venda, e a reserva pra dívida como reserva_outra
    cobranca = _mov(
        loja, "venda_liberada", d(12), debito="1676.57", pagamento="000gpgtumv", sub="release"
    )
    cobranca.descricao = "payment"
    cobranca.pedido_marketplace = "MELIPAYMENTS-COLLECTIONATTEMPT-1423186352-20261005T001908682"
    divida = _mov(loja, "reserva_outra", d(12, 11), debito="354.31", pagamento="98664089")
    divida.descricao = "reserve_for_debt_payment"
    # venda sem pedido no Bling que entrou e saiu inteira (reclamação): venda desfeita
    grupo = [
        _mov(loja, "venda_liberada", d(2), credito="320.69", pedido_mkt=SEM_BLING, pagamento="P7"),
        _mov(loja, "mediacao", d(2, 11), debito="396.17", pedido_mkt=SEM_BLING, pagamento="P7"),
        _mov(loja, "estorno", d(2, 12), credito="75.48", pedido_mkt=SEM_BLING, pagamento="P7"),
    ]
    # venda sem pedido que só entrou: continua sem dono
    solta = _mov(loja, "venda_liberada", d(13), credito="232.10", pedido_mkt="2000018499999998")
    solta.pagamento_id = "P8"
    db.add_all([cobranca, divida, *grupo, solta])
    await db.commit()
    r = await calc.recalcular_loja(db, loja, agora=agora)
    assert r["cobranca_fatura"] == 2 and r["venda_desfeita"] == 3
    await db.refresh(cobranca)
    await db.refresh(divida)
    await db.refresh(solta)
    assert cobranca.tipo == "fatura_cobrada" and cobranca.pedido_marketplace is None
    assert cobranca.classificacao == "fatura_ml"
    assert divida.tipo == "cobranca_divida" and divida.classificacao == "fatura_ml"
    for m in grupo:
        await db.refresh(m)
        assert m.classificacao == "venda_desfeita" and m.pedido_bling is None
    assert solta.classificacao is None
    # o pedido aparece no Bling depois: a ligação desfaz a classificação do robô
    db.add(_pedido_bling("301202", SEM_BLING, d(1), "396.17", 100.0))
    await db.commit()
    await calc.recalcular_loja(db, loja, agora=agora)
    for m in grupo:
        await db.refresh(m)
        assert m.pedido_bling == "301202" and m.classificacao is None


async def test_recalcular_loja_apaga_a_gemea_pobre_que_sobrou(db, make_user):
    """A releitura de 16/10 na aguiar não casou a linha antiga (sub_tipo vazio) e a regra
    da irmã do pagamento ligou as duas ao pedido: recebido em dobro. A gêmea pobre some."""
    loja, _integ = await _loja(db, make_user)
    agora = datetime(2026, 10, 15, 12, tzinfo=UTC)
    d = lambda dia, h=10: datetime(2026, 10, dia, h, tzinfo=UTC)  # noqa: E731
    db.add(_pedido_bling("301300", "2000018400000002", d(2), "100.00", 40.0))
    cheia = _mov(
        loja, "venda_liberada", d(5), credito="80", pedido_mkt="2000018400000002", pagamento="P2"
    )
    pobre = _mov(loja, "venda_liberada", d(5), credito="80", pagamento="P2")
    manual = _mov(loja, "venda_liberada", d(6), credito="80", pagamento="P2")
    manual.vinculado_por = "cairo"  # uma pessoa vinculou: fica
    # saque: nenhum dos dois tem nº da venda; o pobre não tem RECORD_TYPE (sub_tipo)
    saque_cheio = _mov(loja, "saque", d(7), debito="500", pagamento="S1", sub="release")
    saque_pobre = _mov(loja, "saque", d(7), debito="500", pagamento="S1")
    saque_cheio.classificacao = saque_pobre.classificacao = "saque"
    db.add_all([cheia, pobre, manual, saque_cheio, saque_pobre])
    await db.commit()
    r = await calc.recalcular_loja(db, loja, agora=agora)
    assert r["duplicata_pobre_apagada"] == 2
    movs = (await db.execute(__import__("sqlalchemy").select(ConciliacaoMovimento))).scalars().all()
    assert {m.id for m in movs} == {cheia.id, manual.id, saque_cheio.id}
    pedido = (await db.execute(__import__("sqlalchemy").select(ConciliacaoPedido))).scalar_one()
    assert pedido.recebido == Decimal("80")


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
    # sub-tipos vistos na jlas2 em 14/10 (18 diferentes): os desconhecidos vão pelo prefixo
    assert fat.tipo_do_detalhe("CHARGE", "CFONPN") == "fatura_parcelamento_comprador"
    assert fat.tipo_do_detalhe("CHARGE", "CVVFNU") == "fatura_parcelamento"
    assert fat.tipo_do_detalhe("CHARGE", "CDSDB") == "fatura_frete"
    assert fat.tipo_do_detalhe("CHARGE", "CXDED") == "fatura_frete"
    assert fat.tipo_do_detalhe("BONUS", "BFONPN") == "fatura_bonificacao"
    assert fat.tipo_do_detalhe("CHARGE", "ZZZ") == "fatura_outro"


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

    db.add(
        _pedido_bling(
            "301500", "2000018600988674", datetime(2026, 9, 23, 3, tzinfo=UTC), "1054.39", 600.0
        )
    )
    await db.commit()
    r = await fat.run_fatura_sync(
        db, loja, cliente, hoje=date(2026, 10, 14), pausa=13.0, dormir=dormir
    )
    assert r["inseridos"] == 4 and r["chamadas"] == 5 and not r["erros"]
    # a cobrança já nasce com o pedido do Bling resolvido pelo nº da venda
    com_pedido = (
        (
            await db.execute(
                __import__("sqlalchemy")
                .select(ConciliacaoMovimento.pedido_bling)
                .where(ConciliacaoMovimento.origem == "ml_fatura")
            )
        )
        .scalars()
        .all()
    )
    assert com_pedido == ["301500"] * 4
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
    pagamento=None,
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
        pagamento_id=pagamento,
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
    assert esperando.sobrou is None and esperando.margem is None  # só com dinheiro que entrou

    estornado = pedidos["299520"]
    assert estornado.situacao == "estornado_depois" and estornado.etapa == "pendencia"
    assert estornado.recebido == Decimal("0.00") and estornado.saidas_depois == Decimal("459.00")
    assert estornado.sobrou == Decimal("-210.00")

    bling = pedidos["299377"]
    # Bling diferente da venda não é mais situação (15/10): fica só gravado
    assert bling.situacao == "bateu" and bling.dif_bling_venda == Decimal("100.00")
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


async def test_recalcular_loja_herda_o_pedido_da_irma_e_classifica_reserva_solta(db, make_user):
    loja, _integ = await _loja(db, make_user)
    agora = datetime(2026, 10, 15, 12, tzinfo=UTC)
    d = lambda dia, h=10: datetime(2026, 10, dia, h, tzinfo=UTC)  # noqa: E731
    db.add(_pedido_bling("301200", "2000018400000001", d(2), "100.00", 40.0))
    # a venda liberada veio com ORDER_ID; a reserva do mesmo pagamento veio sem (relatório
    # antigo) — herda o pedido pela irmã
    db.add_all(
        [
            _mov(
                loja,
                "venda_liberada",
                d(5),
                credito="80",
                pedido_mkt="2000018400000001",
                pagamento="P1",
            ),
            _mov(loja, "reserva_disputa", d(6), debito="80", pagamento="P1"),
            _mov(loja, "reserva_disputa", d(6, 11), credito="80", pagamento="P1"),
            # reserva de uma venda que não existe no Bling: vai e volta, soma zero → "reserva"
            _mov(
                loja,
                "reserva_estorno",
                d(7),
                debito="50",
                pedido_mkt="2000018499999999",
                pagamento="P9",
            ),
            _mov(
                loja,
                "reserva_estorno",
                d(7, 11),
                credito="50",
                pedido_mkt="2000018499999999",
                pagamento="P9",
            ),
            # estorno de verdade sem pedido: continua sem dono (saiu dinheiro)
            _mov(
                loja,
                "estorno",
                d(7, 12),
                debito="50",
                pedido_mkt="2000018499999999",
                pagamento="P9",
            ),
        ]
    )
    await db.commit()
    r = await calc.recalcular_loja(db, loja, agora=agora)
    assert r["irma_do_pagamento"] == 2 and r["reserva_sem_pedido"] == 2
    movs = (await db.execute(__import__("sqlalchemy").select(ConciliacaoMovimento))).scalars().all()
    por_pag = {}
    for m in movs:
        por_pag.setdefault(m.pagamento_id, []).append(m)
    assert all(m.pedido_bling == "301200" for m in por_pag["P1"])
    p9 = {m.tipo: m for m in por_pag["P9"] if m.tipo != "estorno"}
    assert all(m.classificacao == "reserva" and m.pedido_bling is None for m in p9.values())
    estorno = next(m for m in por_pag["P9"] if m.tipo == "estorno")
    assert estorno.classificacao is None and estorno.pedido_bling is None
    # o pedido do Bling aparece depois: a ligação desfaz a classificação do robô
    db.add(_pedido_bling("301201", "2000018499999999", d(3), "50.00", 20.0))
    await db.commit()
    await calc.recalcular_loja(db, loja, agora=agora)
    movs = (await db.execute(__import__("sqlalchemy").select(ConciliacaoMovimento))).scalars().all()
    for m in movs:
        if m.pagamento_id == "P9":
            assert m.pedido_bling == "301201" and m.classificacao is None


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


async def test_ler_pagina_espera_um_minuto_no_429_e_tenta_de_novo():
    class _Cliente429(_ClienteFalso):
        def __init__(self):
            super().__init__({("2026-10-01", "CHARGE", 0): {"results": [], "last_id": 0}})
            self.vezes = 0

        async def _request(self, method, path, *, params=None, json=None):
            self.vezes += 1
            if self.vezes <= 2:
                return Response(429, json={"message": "Rate limit exceeded: 5 requests per minute"})
            return await super()._request(method, path, params=params, json=json)

    esperas: list[float] = []

    async def dormir(s: float) -> None:
        esperas.append(s)

    cliente = _Cliente429()
    pagina = await fat.ler_pagina(cliente, "2026-10-01", "CHARGE", 0, dormir=dormir)
    assert pagina == {"results": [], "last_id": 0}
    assert esperas == [fat.ESPERA_429, fat.ESPERA_429] and cliente.vezes == 3


# --- painel (2b): lista, Raio-X, marcações e Excel ---------------------------


async def _pedido_conciliado(loja, **kw) -> ConciliacaoPedido:
    base: dict = {}
    base.update(
        integration_id=loja.integration_id,
        platform=loja.platform,
        pedido_bling="301102",
        numeroloja="2000018405611066",
        conta="jlas2",
        data_pedido=datetime(2026, 10, 1, 12, tzinfo=UTC),
        bling_total=Decimal("880.98"),
        bling_valorbase=Decimal("880.98"),
        bling_custofrete=Decimal("62.33"),
        bling_taxacomissao=Decimal("92.36"),
        venda_valor=Decimal("880.98"),
        venda_tarifa=Decimal("92.36"),
        venda_frete=Decimal("62.33"),
        venda_liquido=Decimal("610.65"),
        venda_status="posted",
        liberado_em=datetime(2026, 10, 10, 9, tzinfo=UTC),
        liberado_valor=Decimal("610.65"),
        recebido=Decimal("610.65"),
        custo=Decimal("500.00"),
        reembolso=Decimal("0.00"),
        sobrou=Decimal("110.65"),
        margem=Decimal("0.2213"),
        dif_previsto_recebido=Decimal("0.00"),
        situacao="bateu",
        etapa="conciliado",
        bateu_em=datetime(2026, 10, 10, 9, tzinfo=UTC),
    )
    base.update(kw)
    return ConciliacaoPedido(**base)


async def test_painel_lista_pedidos_com_totais_e_filtros(db, make_user, auth_as, client):
    loja, _integ = await _loja(db, make_user)
    db.add_all(
        [
            await _pedido_conciliado(loja),
            await _pedido_conciliado(
                loja,
                pedido_bling="300991",
                numeroloja="2000018600988674",
                data_pedido=datetime(2026, 10, 2, 12, tzinfo=UTC),
                venda_liquido=Decimal("268.12"),
                recebido=Decimal("240.37"),
                liberado_valor=Decimal("240.37"),
                custo=Decimal("150.00"),
                sobrou=Decimal("90.37"),
                dif_previsto_recebido=Decimal("-27.75"),
                situacao="recebido_a_menos",
                etapa="pendencia",
                motivo="frete cobrado 63.40 × previsto 35.65",
            ),
            await _pedido_conciliado(
                loja,
                pedido_bling="299012",
                numeroloja="2000018999",
                data_pedido=datetime(2026, 10, 3, 12, tzinfo=UTC),
                liberado_em=None,
                liberado_valor=None,
                recebido=None,
                sobrou=Decimal("39.22"),
                custo=Decimal("62.00"),
                dif_previsto_recebido=None,
                situacao="aguardando",
                etapa="aguardando",
                bateu_em=None,
            ),
        ]
    )
    await db.commit()
    auth_as(await make_user(permissions={"conciliacao": {"view": True}}))

    r = await client.get("/api/conciliacao/pedidos", params={"loja": "jlas2"})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["total"] == 3 and [i["pedido_bling"] for i in corpo["itens"]] == [
        "299012",
        "300991",
        "301102",
    ]
    assert (
        corpo["itens"][2]["situacao_label"] == "Bateu"
        and corpo["itens"][2]["etapa_label"] == "conciliado"
    )
    resumo = corpo["resumo"]
    assert resumo["pedidos"] == 3 and resumo["liberados"] == 2
    assert resumo["recebido"] == 851.02 and resumo["sobrou"] == 240.24
    assert resumo["por_situacao"] == {"bateu": 1, "recebido_a_menos": 1, "aguardando": 1}

    r2 = await client.get(
        "/api/conciliacao/pedidos", params={"loja": "jlas2", "situacao": "recebido_a_menos,bateu"}
    )
    assert r2.json()["total"] == 2
    r3 = await client.get("/api/conciliacao/pedidos", params={"loja": "jlas2", "q": "2000018999"})
    assert [i["pedido_bling"] for i in r3.json()["itens"]] == ["299012"]
    r4 = await client.get(
        "/api/conciliacao/pedidos",
        params={"loja": "jlas2", "data": "liberacao", "de": "2026-10-10", "ate": "2026-10-10"},
    )
    assert r4.json()["total"] == 2
    r5 = await client.get("/api/conciliacao/lojas")
    assert r5.json()["lojas"][0]["conta"] == "jlas2"


async def test_painel_raio_x_junta_as_quatro_versoes_e_os_movimentos(
    db, make_user, auth_as, client
):
    loja, _integ = await _loja(db, make_user)
    db.add(await _pedido_conciliado(loja))
    db.add_all(
        [
            _mov(
                loja,
                "venda_liberada",
                datetime(2026, 10, 10, 9, tzinfo=UTC),
                credito="610.65",
                pedido_mkt="2000018405611066",
                pedido_bling="301102",
                frete="-62.33",
                tarifa="-92.36",
                parcelamento="-115.64",
            ),
            _mov(
                loja,
                "fatura_tarifa_venda",
                datetime(2026, 10, 2, 1, tzinfo=UTC),
                debito="82.77",
                pedido_mkt="2000018405611066",
                origem="ml_fatura",
                sub="CVVML",
            ),
        ]
    )
    await db.commit()
    auth_as(await make_user(permissions={"margem": {"view": True}}))
    r = await client.get("/api/conciliacao/pedidos/301102", params={"loja": "jlas2"})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["pedido"]["pedido_bling"] == "301102"
    assert corpo["versoes"]["extrato"] == {
        "valor": None,
        "tarifa": 92.36,
        "parcelamento": 115.64,
        "frete": 62.33,
        "cupom": None,
        "liquido": 610.65,
        "saidas_depois": None,
        "recebido": 610.65,
        # 15/10: frete do comprador (bruto − valor da venda; sem bruto no teste = None)
        "frete_comprador": None,
        "produtos": None,
        "frete_liquido": 62.33,
    }
    assert corpo["versoes"]["bling"]["liquido"] == 726.29
    # Previsto × Real × Dif.: o líquido bate (610,65 dos dois lados) e a linha de
    # frete compara o frete inteiro (sem frete do comprador no teste = o próprio frete)
    comp = {x["chave"]: x for x in corpo["comparativo"]["linhas"]}
    assert comp["liquido"] == {
        "chave": "liquido",
        "rotulo": "= Líquido",
        "previsto": 610.65,
        "real": 610.65,
        "dif": 0.0,
        "estilo": "bold",
    }
    assert comp["frete"]["previsto"] == 62.33 and comp["frete"]["real"] == 62.33
    assert "frete_comprador" not in comp and corpo["comparativo"]["comparar"] is True
    assert corpo["pedido"]["venda_extras"] == 115.64
    assert [m["origem"] for m in corpo["movimentos"]] == ["ml_fatura", "mp_extrato"]
    assert (
        client
        and (await client.get("/api/conciliacao/pedidos/999", params={"loja": "jlas2"})).status_code
        == 404
    )


async def test_painel_marca_conferido_finaliza_e_exige_motivo_na_pendencia(
    db, make_user, auth_as, client
):
    loja, _integ = await _loja(db, make_user)
    db.add(await _pedido_conciliado(loja))
    db.add(
        await _pedido_conciliado(
            loja,
            pedido_bling="300991",
            numeroloja="2000018600988674",
            situacao="recebido_a_menos",
            etapa="pendencia",
        )
    )
    await db.commit()
    quem = await make_user(permissions={"conciliacao": {"view": True, "edit": True}})
    auth_as(quem)
    r = await client.patch(
        "/api/conciliacao/pedidos/301102",
        json={"loja": "jlas2", "conferido": True, "observacao": " ok "},
    )
    assert r.status_code == 200, r.text
    p = r.json()["pedido"]
    assert p["conferido"] is True and p["conferido_por"] == quem.email and p["observacao"] == "ok"
    # pendência sem motivo não finaliza
    r2 = await client.patch(
        "/api/conciliacao/pedidos/300991", json={"loja": "jlas2", "etapa": "finalizado"}
    )
    assert r2.status_code == 422 and r2.json()["detail"]["code"] == "motivo_obrigatorio"
    r3 = await client.patch(
        "/api/conciliacao/pedidos/300991",
        json={"loja": "jlas2", "etapa": "finalizado", "etapa_motivo": "frete a mais, chamado 4471"},
    )
    assert (
        r3.json()["pedido"]["etapa"] == "finalizado"
        and r3.json()["pedido"]["etapa_por"] == quem.email
    )
    # quem só vê não marca
    auth_as(await make_user(permissions={"conciliacao": {"view": True}}))
    r4 = await client.patch(
        "/api/conciliacao/pedidos/301102", json={"loja": "jlas2", "conferido": False}
    )
    assert r4.status_code == 403


async def test_painel_exporta_excel(db, make_user, auth_as, client):
    loja, _integ = await _loja(db, make_user)
    db.add(await _pedido_conciliado(loja))
    await db.commit()
    auth_as(await make_user(permissions={"conciliacao": {"view": True}}))
    r = await client.post("/api/conciliacao/export.xlsx", params={"loja": "jlas2"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats")
    from io import BytesIO

    from openpyxl import load_workbook

    ws = load_workbook(BytesIO(r.content)).active
    linhas = list(ws.iter_rows(values_only=True))
    assert (
        linhas[0][1] == "Pedido Bling"
        and linhas[1][1] == "301102"
        and linhas[1][linhas[0].index("Liberado em")] == "10/10/2026 06:00"
    )
    assert (
        linhas[0][linhas[0].index("Extras (cupom, parcelamento, outros)")]
        and linhas[1][linhas[0].index("Extras (cupom, parcelamento, outros)")] == 115.64
    )


# --- Extrato do dia (2c) ---------------------------------------------------------


def _d13(h: int, mi: int = 0) -> datetime:
    # 13/10/2026 em BRT (UTC-3) = +3h em UTC
    return datetime(2026, 10, 13, h + 3, mi, tzinfo=UTC)


async def _dia_de_exemplo(db, loja) -> dict[str, ConciliacaoMovimento]:
    movs = {
        "reserva": _mov(loja, "reserva_saque", _d13(6, 57), debito="9147.00"),
        "saque": _mov(loja, "saque", _d13(6, 57) + timedelta(seconds=2), debito="9147.00"),
        "venda": _mov(
            loja,
            "venda_liberada",
            _d13(8, 11),
            credito="610.65",
            pedido_mkt="2000018405611066",
            pedido_bling="301102",
            frete="-62.33",
            tarifa="-92.36",
        ),
        "pendente": _mov(loja, "credito", _d13(9, 2), credito="48.00"),
        "mediacao": _mov(
            loja,
            "mediacao",
            _d13(14, 10),
            debito="459.00",
            pedido_mkt="2000018737867860",
            pedido_bling="299520",
        ),
    }
    # saldos encadeados (BALANCE_AMOUNT): 9147.81 → 0.81 → 611.46 → 659.46 → 200.46
    movs["reserva"].saldo_apos = Decimal("0.81")
    movs["saque"].saldo_apos = Decimal(
        "0.81"
    )  # reserva + saque: 9147.81 − 9147 (reserva) ... o saque devolve a reserva
    movs["reserva"].credito = Decimal("9147.00")
    movs["reserva"].debito = Decimal("0.00")
    movs["reserva"].saldo_apos = Decimal("9147.81")
    movs["saque"].saldo_apos = Decimal("0.81")
    movs["venda"].saldo_apos = Decimal("611.46")
    movs["pendente"].saldo_apos = Decimal("659.46")
    movs["mediacao"].saldo_apos = Decimal("200.46")
    movs["saque"].classificacao = "saque"
    movs["reserva"].classificacao = "saque"
    db.add_all(movs.values())
    db.add(await _pedido_conciliado(loja))  # 301102 bateu
    db.add(
        await _pedido_conciliado(
            loja,
            pedido_bling="299520",
            numeroloja="2000018737867860",
            situacao="estornado_depois",
            etapa="pendencia",
            recebido=Decimal("0.00"),
            saidas_depois=Decimal("459.00"),
        )
    )
    await db.commit()
    return movs


async def test_extrato_do_dia_lista_ordena_e_fecha_a_conta(db, make_user, auth_as, client):
    loja, _integ = await _loja(db, make_user)
    await _dia_de_exemplo(db, loja)
    auth_as(await make_user(permissions={"conciliacao": {"view": True}}))
    r = await client.get("/api/conciliacao/extrato", params={"loja": "jlas2", "dia": "2026-10-13"})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["dia"] == "2026-10-13" and corpo["fechado"] is None
    tipos = [m["tipo"] for m in corpo["movimentos"]]
    # empate de horário (reserva e saque às 06:57): a ordem é a que encadeia o saldo
    assert tipos == ["reserva_saque", "saque", "venda_liberada", "credito", "mediacao"]
    assert [m["status"] for m in corpo["movimentos"]] == [
        "classificado",
        "classificado",
        "automatico",
        "pendente",
        "automatico",
    ]
    assert corpo["contagem"] == {
        "automatico": 2,
        "manual": 0,
        "classificado": 2,
        "anterior": 0,
        "pendente": 1,
        "divergente": 0,
    }
    assert corpo["resolvidos"] == 4
    t = corpo["totais"]
    assert t["linhas"] == 5 and t["saldo_inicial"] == 0.81 and t["saldo_informado"] == 200.46
    assert (
        t["creditos"] == 9805.65
        and t["debitos"] == 9606.0
        and t["saldo_final"] == 200.46
        and t["diferenca"] == 0.0
    )
    r2 = await client.get(
        "/api/conciliacao/extrato",
        params={"loja": "jlas2", "dia": "2026-10-13", "mostrar": "pendentes"},
    )
    assert [m["tipo"] for m in r2.json()["movimentos"]] == ["credito"]
    r3 = await client.get(
        "/api/conciliacao/dias", params={"loja": "jlas2", "de": "2026-10-13", "ate": "2026-10-13"}
    )
    assert r3.json()["dias"] == [
        {
            "dia": "2026-10-13",
            "linhas": 5,
            "pendentes": 1,
            "creditos": 9805.65,
            "debitos": 9606.0,
            "fechado_em": None,
            "fechado_por": None,
        }
    ]


async def test_extrato_vincular_classificar_e_fechar_o_dia(db, make_user, auth_as, client):
    loja, _integ = await _loja(db, make_user)
    movs = await _dia_de_exemplo(db, loja)
    db.add(
        _pedido_bling(
            "301500", "2000018600988674", datetime(2026, 10, 2, 12, tzinfo=UTC), "48.00", 20.0
        )
    )
    await db.commit()
    quem = await make_user(permissions={"conciliacao": {"view": True, "edit": True}})
    auth_as(quem)
    pend = movs["pendente"]
    # o dia não fecha com pendência
    r = await client.post("/api/conciliacao/dias/2026-10-13/fechar", params={"loja": "jlas2"})
    assert r.status_code == 409 and r.json()["detail"]["pendentes"] == 1
    # sugestões: mesmo valor (48,00) acha o 301500
    r = await client.get(f"/api/conciliacao/movimentos/{pend.id}/sugestoes")
    assert r.status_code == 200, r.text
    assert [s["pedido_bling"] for s in r.json()["sugestoes"]] == ["301500"]
    assert r.json()["sugestoes"][0]["motivo"] == "mesmo valor"
    # vincula a um pedido que não é da loja → 404
    r = await client.patch(
        f"/api/conciliacao/movimentos/{pend.id}", json={"pedido_bling": "999999"}
    )
    assert r.status_code == 404
    # vincula
    r = await client.patch(
        f"/api/conciliacao/movimentos/{pend.id}", json={"pedido_bling": "301500"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["movimento"]["pedido_bling"] == "301500" and r.json()["status"] == "manual"
    # agora fecha
    r = await client.post("/api/conciliacao/dias/2026-10-13/fechar", params={"loja": "jlas2"})
    assert r.status_code == 200, r.text
    assert r.json()["fechado"]["por"] == quem.email and r.json()["totais"]["diferenca"] == 0.0
    r = await client.get("/api/conciliacao/extrato", params={"loja": "jlas2", "dia": "2026-10-13"})
    assert r.json()["fechado"]["por"] == quem.email
    # dia fechado não se mexe (15/10): vincular, classificar e desvincular dão 409
    for body in ({"classificacao": "ajuste"}, {"desvincular": True}, {"pedido_bling": "301500"}):
        r = await client.patch(f"/api/conciliacao/movimentos/{pend.id}", json=body)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "dia_fechado", body
        assert r.json()["detail"]["por"] == quem.email
    r = await client.post("/api/conciliacao/dias/2026-10-13/reabrir", params={"loja": "jlas2"})
    assert r.status_code == 200
    # desvincular uma linha que o robô casou pelo nº da venda: fica desvinculada (o
    # recálculo não religa o que uma pessoa desfez) até alguém vincular ou classificar
    db.add(
        _pedido_bling(
            "301102", "2000018405611066", datetime(2026, 10, 1, 12, tzinfo=UTC), "880.98", 500.0
        )
    )
    await db.commit()
    venda = movs["venda"]
    r = await client.patch(f"/api/conciliacao/movimentos/{venda.id}", json={"desvincular": True})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pendente" and r.json()["movimento"]["pedido_bling"] is None
    r = await client.get("/api/conciliacao/extrato", params={"loja": "jlas2", "dia": "2026-10-13"})
    linha = next(x for x in r.json()["movimentos"] if x["id"] == str(venda.id))
    assert linha["status"] == "pendente" and linha["vinculado_por"] == quem.email
    r = await client.patch(
        f"/api/conciliacao/movimentos/{venda.id}", json={"pedido_bling": "301102"}
    )
    assert r.json()["status"] == "manual" and r.json()["movimento"]["pedido_bling"] == "301102"
    # fecha de novo pra seguir o teste como antes
    r = await client.post("/api/conciliacao/dias/2026-10-13/fechar", params={"loja": "jlas2"})
    assert r.status_code == 200, r.text
    r = await client.post("/api/conciliacao/dias/2026-10-13/reabrir", params={"loja": "jlas2"})
    assert r.status_code == 200
    # classifica (vira sem pedido) e desvincula
    r = await client.patch(
        f"/api/conciliacao/movimentos/{pend.id}", json={"classificacao": "ajuste"}
    )
    assert (
        r.json()["movimento"]["pedido_bling"] is None
        and r.json()["movimento"]["classificacao"] == "ajuste"
    )
    r = await client.patch(
        f"/api/conciliacao/movimentos/{pend.id}", json={"classificacao": "nao_existe"}
    )
    assert r.status_code == 422
    r = await client.patch(f"/api/conciliacao/movimentos/{pend.id}", json={"desvincular": True})
    assert r.json()["status"] == "pendente"
    # reabrir um dia já aberto não quebra
    r = await client.post("/api/conciliacao/dias/2026-10-13/reabrir", params={"loja": "jlas2"})
    assert r.status_code == 200 and r.json()["fechado"] is None
    # quem só vê não mexe
    auth_as(await make_user(permissions={"conciliacao": {"view": True}}))
    r = await client.patch(
        f"/api/conciliacao/movimentos/{pend.id}", json={"classificacao": "ajuste"}
    )
    assert r.status_code == 403


# --- resumo (tela de entrada) e fechamento do mês (passo 2d) -----------------


async def test_resumo_le_o_dia_de_ontem_e_monta_onde_agir(db, make_user, auth_as, client):
    loja, _integ = await _loja(db, make_user)
    await _dia_de_exemplo(db, loja)
    loja.ultimo_extrato_em = datetime.now(UTC)
    await db.commit()
    auth_as(await make_user(permissions={"conciliacao": {"view": True}}))
    r = await client.get("/api/conciliacao/resumo", params={"dia": "2026-10-13"})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["dia"] == "2026-10-13" and corpo["mes"] == "2026-10"
    (linha,) = corpo["lojas"]
    assert linha["conta"] == "jlas2" and linha["platform"] == "ml"
    # o dia: 5 linhas, 1 sem dono, saldo bate — mas não fechou
    assert linha["dia"]["status"] == "aberto"
    assert linha["dia"]["linhas"] == 5 and linha["dia"]["pendentes"] == 1
    assert linha["dia"]["saldo_bate"] is True and linha["dia"]["diferenca"] == 0.0
    # os pedidos: um conciliado, um com pendência (estornado depois)
    assert linha["pedidos"]["pendencia"] == 1 and linha["pedidos"]["conciliados"] == 1
    assert linha["pedidos"]["estornados"] == 1 and linha["pedidos"]["reabertos"] == 0
    assert linha["leituras"]["extrato_atrasado"] is False
    assert linha["gravidade"] == "bad"
    assert "1 linha(s) sem dono" in linha["situacao"] and "estornado" in linha["situacao"]
    # o mês até ontem (os dois pedidos são de 01/10)
    assert linha["mes"]["pedidos"] == 2 and linha["mes"]["vendido"] == 1761.96
    assert linha["mes"]["por_etapa"] == {"conciliado": 1, "pendencia": 1}
    assert linha["mes"]["ajustes_meses_anteriores"] == 0.0 and linha["mes"]["ajustes_qtd"] == 0
    # onde agir: a linha sem dono (bad) vem antes do estornado (warn)
    assert [a["acao"] for a in corpo["agir"]] == ["extrato", "pedidos"]
    assert corpo["agir"][0]["gravidade"] == "bad" and corpo["agir"][0]["dia"] == "2026-10-13"
    assert corpo["agir"][1]["filtro"] == {"situacao": "estornado_depois"}
    assert corpo["cartoes"]["pendentes"] == 1 and corpo["cartoes"]["dias_fechados"] == 0
    assert corpo["cartoes"]["saldo_bate"] == 1 and corpo["cartoes"]["extrato_atrasado"] == 0
    assert corpo["por_plataforma"]["ml"]["pedidos"] == 2 and corpo["empresa"]["lojas"] == 1
    # dinheiro nas plataformas: saldo da última linha do extrato, nada a liberar no exemplo
    assert linha["dinheiro"]["na_conta"] == 200.46 and linha["dinheiro"]["saldo_em"]
    assert linha["dinheiro"]["a_liberar"] == 0.0 and linha["dinheiro"]["a_liberar_pedidos"] == 0
    assert linha["dinheiro"]["reservado"] == 0.0
    assert corpo["empresa"]["dinheiro"]["na_conta"] == 200.46

    # extrato parado há mais de 36 h: vira "extrato não lido" e pede pra sincronizar
    loja.ultimo_extrato_em = datetime.now(UTC) - timedelta(days=3)
    await db.commit()
    r = await client.get("/api/conciliacao/resumo", params={"dia": "2026-10-13"})
    linha = r.json()["lojas"][0]
    assert linha["leituras"]["extrato_atrasado"] is True
    assert "extrato não lido" in linha["situacao"]
    assert [a["acao"] for a in r.json()["agir"]] == ["extrato", "sincronizar", "pedidos"]

    # um dia sem movimento e sem leitura: "extrato não lido"; com leitura: "sem movimento"
    r = await client.get("/api/conciliacao/resumo", params={"dia": "2026-10-12"})
    assert r.json()["lojas"][0]["dia"]["status"] == "extrato_nao_lido"
    loja.ultimo_extrato_em = datetime.now(UTC)
    await db.commit()
    r = await client.get("/api/conciliacao/resumo", params={"dia": "2026-10-12"})
    assert r.json()["lojas"][0]["dia"]["status"] == "sem_movimento"
    assert r.json()["lojas"][0]["dia"]["saldo_bate"] is None


async def test_resumo_dia_pronto_e_dia_fechado(db, make_user, auth_as, client):
    loja, _integ = await _loja(db, make_user)
    movs = await _dia_de_exemplo(db, loja)
    movs["pendente"].classificacao = "ajuste"
    loja.ultimo_extrato_em = datetime.now(UTC)
    await db.commit()
    auth_as(await make_user(permissions={"conciliacao": {"view": True, "edit": True}}))
    r = await client.get("/api/conciliacao/resumo", params={"dia": "2026-10-13"})
    linha = r.json()["lojas"][0]
    assert linha["dia"]["status"] == "pronto" and linha["dia"]["pendentes"] == 0
    assert [a["acao"] for a in r.json()["agir"]] == ["pedidos", "extrato"]
    assert r.json()["agir"][-1]["gravidade"] == "ok"
    assert r.json()["cartoes"]["dias_prontos"] == 1
    r = await client.post("/api/conciliacao/dias/2026-10-13/fechar", params={"loja": "jlas2"})
    assert r.status_code == 200, r.text
    r = await client.get("/api/conciliacao/resumo", params={"dia": "2026-10-13"})
    linha = r.json()["lojas"][0]
    assert linha["dia"]["status"] == "fechado" and linha["dia"]["fechado_por"]
    assert linha["gravidade"] == "warn"  # ainda tem o estornado depois
    assert r.json()["cartoes"]["dias_fechados"] == 1


async def test_fechamento_do_mes_soma_vendas_caixa_e_fatura(db, make_user, auth_as, client):
    loja, _integ = await _loja(db, make_user)
    await _dia_de_exemplo(db, loja)
    # uma mediação de setembro caindo em outubro: "ajuste de mês anterior"
    db.add(
        await _pedido_conciliado(
            loja,
            pedido_bling="298000",
            numeroloja="2000018000000001",
            data_pedido=datetime(2026, 9, 20, 12, tzinfo=UTC),
            situacao="estornado_depois",
            etapa="pendencia",
        )
    )
    antiga = _mov(
        loja,
        "mediacao",
        _d13(15, 0),
        debito="100.00",
        pedido_mkt="2000018000000001",
        pedido_bling="298000",
    )
    antiga.saldo_apos = Decimal("100.46")
    db.add(antiga)
    # a fatura do ML: resumo do período + dois detalhes lidos (CV 92,36 e CXD 62,33)
    db.add(
        ConciliacaoSync(
            integration_id=loja.integration_id,
            origem="ml_fatura",
            chave="2026-10-01|resumo",
            status="lido",
            lido_em=datetime(2026, 10, 14, 9, tzinfo=UTC),
            detalhe={
                "resumo": {
                    "period": {
                        "key": "2026-10-01",
                        "date_from": "2026-09-23",
                        "date_to": "2026-10-22",
                        "expiration_date": "2026-10-28",
                    },
                    "bill_includes": {
                        "total_amount": 154.69,
                        "charges": [
                            {"type": "CV", "label": "Cargo por venda", "amount": 92.36},
                            {"type": "CXD", "label": "Cargo por Mercado Envios", "amount": 62.33},
                        ],
                        "bonuses": [{"type": "BVVML", "label": "BVVML", "amount": -10.0}],
                    },
                    "payment_collected": {"total_collected": 154.69, "total_debt": 0.0},
                }
            },
        )
    )
    # um cancelado por reclamação: o ML liberou (+500) e tirou (−500) no mesmo dia
    db.add(
        await _pedido_conciliado(
            loja,
            pedido_bling="303700",
            numeroloja="2000018000000002",
            situacao="cancelado",
            etapa="conciliado",
            recebido=Decimal("0.00"),
            sobrou=Decimal("0.00"),
            custo=Decimal("0.00"),
        )
    )
    db.add(
        _mov(
            loja,
            "venda_liberada",
            _d13(16, 0),
            credito="500.00",
            pedido_mkt="2000018000000002",
            pedido_bling="303700",
        )
    )
    db.add(
        _mov(
            loja,
            "mediacao",
            _d13(16, 5),
            debito="500.00",
            pedido_mkt="2000018000000002",
            pedido_bling="303700",
        )
    )
    cobrancas = {}
    for sub, tipo, valor in (("CVVML", "fatura_tarifa", "92.36"), ("CXD", "fatura_frete", "62.33")):
        cobrancas[sub] = _mov(
            loja,
            tipo,
            _d13(10, 0),
            debito=valor,
            pedido_bling="301102",
            origem="ml_fatura",
            sub=sub,
        )
        db.add(cobrancas[sub])
    # duas bonificações: uma devolve a cobrança CVVML desta fatura (entra no resumo do
    # ML), outra devolve uma cobrança de fatura anterior (crédito real, fora do resumo)
    b1 = _mov(
        loja, "fatura_bonificacao", _d13(11, 0), credito="10.00", origem="ml_fatura", sub="BVVML"
    )
    b1.raw = {"charge_info": {"charge_bonified_id": cobrancas["CVVML"].id_externo}}
    b2 = _mov(
        loja, "fatura_bonificacao", _d13(11, 5), credito="5.00", origem="ml_fatura", sub="BDSB"
    )
    b2.raw = {"charge_info": {"charge_bonified_id": 999999}}
    db.add_all([b1, b2])
    await db.commit()
    auth_as(await make_user(permissions={"conciliacao": {"view": True}}))
    r = await client.get("/api/conciliacao/fechamento", params={"loja": "jlas2", "mes": "2026-10"})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert (
        corpo["mes"] == "2026-10" and corpo["de"] == "2026-10-01" and corpo["ate"] == "2026-10-31"
    )
    # vendas pela data do pedido: os dois de outubro + o cancelado
    assert corpo["vendas"]["pedidos"] == 3 and corpo["vendas"]["vendido"] == 2642.94
    assert corpo["vendas"]["tarifa"] == 277.08
    # "liberado" = liberou e ficou; o cancelado por reclamação fica à parte
    assert corpo["vendas"]["liberados"] == 2 and corpo["vendas"]["liberados_cancelados"] == 1
    # caixa pela data do movimento
    caixa = corpo["caixa"]
    assert caixa["linhas"] == 8
    assert caixa["cancelados_entrou"] == 500.0 and caixa["cancelados_saiu"] == 500.0
    assert caixa["cancelados_liquido"] == 0.0 and caixa["cancelados_linhas"] == 2
    assert caixa["liberado"] == 610.65 and caixa["mediacoes"] == -459.0
    assert caixa["outros"] == 48.0 and caixa["saques"] == -9147.0
    assert caixa["reservas_liquido"] == 9147.0
    assert caixa["ajustes_meses_anteriores"] == -100.0 and caixa["ajustes_qtd"] == 1
    assert caixa["resultado_caixa"] == 99.65
    assert caixa["movimento_liquido"] == 99.65
    assert caixa["saldo_inicial"] == 0.81 and caixa["saldo_final"] == 100.46
    assert caixa["bate"] is True
    # dias: um dia com movimento, não fechado
    assert len(corpo["dias"]) == 1 and corpo["dias_fechados"] == 0
    # fatura do ML: resumo × detalhes
    fat = corpo["fatura"]
    assert fat["periodo"] == {
        "chave": "2026-10-01",
        "de": "2026-09-23",
        "ate": "2026-10-22",
        "expira": "2026-10-28",
    }
    assert [c["tipo"] for c in fat["resumo_cobrancas"]] == ["CV", "CXD"]
    assert fat["resumo_total"] == 154.69
    assert fat["detalhes_cobrancas"] == 154.69 and fat["detalhes_bonificacoes"] == 15.0
    assert {d["sub_tipo"] for d in fat["detalhes_por_sub_tipo"]} == {
        "CVVML",
        "CXD",
        "BVVML",
        "BDSB",
    }
    assert fat["bate"] is True and fat["pago"]["total_collected"] == 154.69
    # resumo do ML (10,00) = bonificações desta fatura (10,00); a de fatura anterior à parte
    assert fat["bonificacoes_mesma_fatura"] == 10.0
    assert fat["bonificacoes_de_faturas_anteriores"] == 5.0
    assert fat["bonificacoes_anteriores_linhas"] == 1 and fat["bate_bonificacoes"] is True
    # o Excel traz as quatro abas
    r = await client.post(
        "/api/conciliacao/fechamento.xlsx", params={"loja": "jlas2", "mes": "2026-10"}
    )
    assert r.status_code == 200, r.text
    assert "spreadsheetml" in r.headers["content-type"]
    from io import BytesIO

    from openpyxl import load_workbook

    wb = load_workbook(BytesIO(r.content))
    assert wb.sheetnames == ["Vendas", "Caixa", "Fatura ML", "Dias"]
    assert wb["Vendas"]["A2"].value == "Pedidos" and wb["Vendas"]["B2"].value == 3
    assert wb["Dias"]["A2"].value == "2026-10-13"
    # mês sem nada: tudo zerado, fatura sem resumo
    r = await client.get("/api/conciliacao/fechamento", params={"loja": "jlas2", "mes": "2026-08"})
    assert r.status_code == 200
    assert r.json()["vendas"]["pedidos"] == 0 and r.json()["caixa"]["linhas"] == 0
    assert r.json()["caixa"]["bate"] is None and r.json()["fatura"]["bate"] is None
    r = await client.get("/api/conciliacao/fechamento", params={"loja": "jlas2", "mes": "2026-13"})
    assert r.status_code == 422


def test_cancelado_finaliza_de_cara():
    """Venda desfeita é zero a zero: nem espera os 7 dias (15/10)."""
    from app.services.conciliacao_calculo import etapa_do_pedido

    out = etapa_do_pedido(
        agora=datetime(2026, 10, 15, 12, tzinfo=UTC),
        atual=None,
        situacao="cancelado",
        ultimo_movimento_em=datetime(2026, 10, 15, 11, tzinfo=UTC),
    )
    assert out["etapa"] == "finalizado" and out["etapa_por"] == "robô"
    assert out["etapa_motivo"] == "venda desfeita, zero a zero"


# --- 15/10: fechar o dia sozinho, vigia de conciliação, cancelado pelo Bling -------


async def test_fechar_dias_prontos_so_fecha_dia_100_por_cento(db, make_user):
    from sqlalchemy import select

    from app.models import ConciliacaoDia
    from app.services.conciliacao_jobs import fechar_dias_prontos

    loja, _integ = await _loja(db, make_user)
    movs = await _dia_de_exemplo(db, loja)
    # 13/10 tem uma linha sem dono: fica aberto
    r = await fechar_dias_prontos(db, loja)
    assert r["fechados"] == [] and "2026-10-13" in r["abertos"]
    # deu destino à linha: o robô fecha
    movs["pendente"].classificacao = "ajuste"
    await db.commit()
    r = await fechar_dias_prontos(db, loja)
    assert r["fechados"] == ["2026-10-13"]
    dia = (await db.execute(select(ConciliacaoDia))).scalar_one()
    assert dia.fechado_por == "robô" and dia.pendentes == 0 and dia.linhas == 5
    assert dia.saldo_informado == Decimal("200.46")
    # já fechado: não mexe de novo
    r = await fechar_dias_prontos(db, loja)
    assert r["fechados"] == [] and "2026-10-13" not in r["abertos"]


async def test_vigia_conciliacao_registra_o_onde_agir(db, make_user):
    from sqlalchemy import select

    from app.models import OuvidoriaOcorrencia
    from app.services.vigia_conciliacao import ROBO, vigia_conciliacao_run

    loja, _integ = await _loja(db, make_user)
    await _dia_de_exemplo(db, loja)  # 13/10: 1 linha sem dono + 1 estornado depois
    loja.ultimo_extrato_em = datetime.now(UTC)
    await db.commit()
    # o vigia olha "ontem": no teste o dia de exemplo é 13/10, então olha com esse dia
    import app.routers.conciliacao as rc

    original = rc.montar_resumo

    async def ontem_13(session, dia=None):
        return await original(session, date(2026, 10, 13))

    rc.montar_resumo = ontem_13
    try:
        r = await vigia_conciliacao_run(db)
    finally:
        rc.montar_resumo = original
    assert r["lojas"] == 1 and r["itens"] == 2 and r["novas"] == 2
    ocorrencias = (
        (
            await db.execute(
                select(OuvidoriaOcorrencia).where(OuvidoriaOcorrencia.robo_chave == ROBO)
            )
        )
        .scalars()
        .all()
    )
    chaves = {o.chave for o in ocorrencias}
    assert chaves == {"jlas2:extrato:", "jlas2:pedidos:situacao=estornado_depois"}
    extrato = next(o for o in ocorrencias if o.chave == "jlas2:extrato:")
    assert extrato.link == "/conciliacao?loja=jlas2&visao=extrato&dia=2026-10-13"
    assert extrato.conta == "jlas2" and extrato.plataforma == "Mercado Livre"
    assert "pra agir" in r["resumo"]


def test_cancelado_no_bling_sem_dinheiro_e_cancelado():
    from app.services.conciliacao_calculo import situacao_do_pedido

    agora = datetime(2026, 10, 15, 12, tzinfo=UTC)
    out = situacao_do_pedido(
        agora=agora,
        data_pedido=datetime(2026, 10, 13, 12, tzinfo=UTC),
        bling_total=Decimal("1803.20"),
        venda=None,
        previsao=None,
        movs_extrato=[],
        fatura={},
        bling_cancelado=True,
    )
    assert out["situacao"] == "cancelado" and "Bling" in out["motivo"]
    out = situacao_do_pedido(
        agora=agora,
        data_pedido=datetime(2026, 10, 13, 12, tzinfo=UTC),
        bling_total=Decimal("1803.20"),
        venda=None,
        previsao=None,
        movs_extrato=[],
        fatura={},
    )
    assert out["situacao"] == "sem_venda"


def test_ordem_do_dia_com_ancora_resolve_ciclo_no_mesmo_segundo():
    """injox 15/10 (16/10): pagamento (+362,15 → 362,15) e reserva (−362,15 → 0) no mesmo
    segundo formam um ciclo; sem âncora a ordem saía trocada e o saldo inicial do dia
    virava 362,15. Com o fechamento do dia anterior (0) a ordem e a prova dos nove fecham."""
    from decimal import Decimal
    from uuid import uuid4

    from app.models import ConciliacaoMovimento
    from app.routers.conciliacao import _ordena_dia, _totais_do_dia

    quando = datetime(2026, 10, 15, 17, 21, 6, tzinfo=UTC)
    reserva = ConciliacaoMovimento(
        id=uuid4(),
        origem="mp_extrato",
        id_externo="r",
        data=quando,
        tipo="reserva_disputa",
        credito=Decimal("0"),
        debito=Decimal("362.15"),
        saldo_apos=Decimal("0"),
    )
    pagamento = ConciliacaoMovimento(
        id=uuid4(),
        origem="mp_extrato",
        id_externo="p",
        data=quando,
        tipo="venda_liberada",
        credito=Decimal("362.15"),
        debito=Decimal("0"),
        saldo_apos=Decimal("362.15"),
    )
    ordem = _ordena_dia([reserva, pagamento], saldo_anterior=Decimal("0"))
    assert [m.tipo for m in ordem] == ["venda_liberada", "reserva_disputa"]
    t = _totais_do_dia(ordem, saldo_anterior=Decimal("0"))
    assert t["saldo_inicial"] == 0.0 and t["saldo_final"] == 0.0 and t["diferenca"] == 0.0
    # sem âncora a prova dos nove parte da primeira linha (comportamento antigo)
    t2 = _totais_do_dia(_ordena_dia([reserva, pagamento]))
    assert t2["diferenca"] in (0.0, -362.15)
