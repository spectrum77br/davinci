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
from uuid import uuid4

import pytest
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
        self,
        paginas: dict[tuple[str, str, int], dict] | None = None,
        resumo: dict | None = None,
        *,
        periodos: list[dict] | None = None,
        periodos_http: int = 200,
    ):
        self.paginas = paginas or {}
        self.resumo = resumo or {"bill_includes": {"total_amount": 1}}
        # `monthly/periods` (16/10): sem lista, a leitura cai no mês do calendário
        self.periodos = periodos or []
        self.periodos_http = periodos_http
        self.chamadas: list[tuple[str, dict | None]] = []

    def _expired(self) -> bool:
        return False

    async def refresh(self) -> None:  # pragma: no cover
        raise AssertionError("a conciliação nunca renova token na mão")

    async def _request(self, method: str, path: str, *, params=None, json=None):
        self.chamadas.append((path, params))
        if path.endswith("/monthly/periods"):
            return Response(self.periodos_http, json={"results": self.periodos})
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


def test_acha_relatorio_ignora_o_relatorio_criado_antes_do_pedido():
    """16/10 (revisão): na releitura da mesma janela a lista traz o relatório velho (o
    pobre); só vale o criado depois do pedido. Item sem data de criação passa."""
    janela = ext.janela_utc(date(2026, 10, 1), date(2026, 10, 14))
    velho = {**janela, "id": 1, "file_name": "velho.csv", "date_created": "2026-10-15T08:00:00Z"}
    novo = {**janela, "id": 2, "file_name": "novo.csv", "date_created": "2026-10-16T09:01:00Z"}
    sem_data = {**janela, "id": 3, "file_name": "sem-data.csv"}
    pedido_em = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)
    assert ext._acha_relatorio([velho, novo], janela)["file_name"] == "novo.csv"
    assert ext._acha_relatorio([velho, novo], janela, desde=pedido_em)["file_name"] == "novo.csv"
    assert ext._acha_relatorio([velho], janela, desde=pedido_em) is None
    assert ext._acha_relatorio([velho, sem_data], janela, desde=pedido_em)["id"] == 3
    # 5 min de folga pro relógio
    quase = {**velho, "id": 4, "date_created": "2026-10-16T08:56:00Z"}
    assert ext._acha_relatorio([quase], janela, desde=pedido_em)["id"] == 4


async def test_run_extrato_sync_repede_a_janela_vencida_e_ignora_o_relatorio_velho(db, make_user):
    """16/10 (revisão): janela "pedido" não tinha prazo — se o Mercado Pago nunca gera o
    arquivo, a loja ficava parada pra sempre. Passadas 6 h: erro na janela e na loja, e
    a mesma janela é pedida de novo."""
    loja, _integ = await _loja(db, make_user)
    loja.extrato_config_em = datetime.now(UTC)
    pedido_em = datetime.now(UTC) - timedelta(hours=7)
    janela = ext.janela_utc(date(2026, 10, 1), date(2026, 10, 14))
    db.add(
        ConciliacaoSync(
            integration_id=loja.integration_id,
            origem="mp_extrato",
            chave="2026-10-01..2026-10-14",
            status="pedido",
            pedido_em=pedido_em,
            detalhe={"janela": janela},
        )
    )
    await db.commit()
    cliente = _ClienteFalso()
    velho = {
        **janela,
        "id": 1,
        "file_name": "velho.csv",
        "date_created": (pedido_em - timedelta(days=1)).isoformat(),
    }
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{MP}/v1/account/release_report/list").mock(
            return_value=Response(200, json=[velho])
        )
        baixa = router.get(f"{MP}/v1/account/release_report/velho.csv").mock(
            return_value=Response(200, headers={"content-type": "text/csv"}, text=CSV_JLAS2)
        )
        pede = router.post(f"{MP}/v1/account/release_report").mock(
            return_value=Response(202, json={"status": "pending"})
        )
        r = await ext.run_extrato_sync(db, loja, cliente, hoje=date(2026, 10, 15))
    assert baixa.call_count == 0  # o relatório velho (de antes do pedido) não vale
    assert pede.call_count == 1 and r["pedido"] is None  # pediu a MESMA janela de novo
    assert r["repedidos"] == [{"janela": "2026-10-01..2026-10-14", "http": 202, "pedidos": 2}]
    assert r["erros"] == [
        {
            "janela": "2026-10-01..2026-10-14",
            "erro": "relatório não veio em 6 h; pedido de novo (2º pedido)",
        }
    ]
    sync = (await db.execute(__import__("sqlalchemy").select(ConciliacaoSync))).scalar_one()
    assert sync.status == "pedido" and sync.pedido_em > pedido_em
    assert sync.erro == "relatório não veio em 6 h; pedido de novo (2º pedido)"
    assert sync.detalhe["pedidos"] == 2 and sync.detalhe["repedido_em"]
    assert loja.ultimo_erro == (
        "extrato 2026-10-01..2026-10-14: relatório não veio em 6 h; pedido de novo (2º pedido)"
    )
    # dentro do prazo: nada acontece; o relatório novo (depois do pedido) é lido
    novo = {
        **janela,
        "id": 2,
        "file_name": "novo.csv",
        "date_created": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
    }
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{MP}/v1/account/release_report/list").mock(
            return_value=Response(200, json=[velho, novo])
        )
        router.get(f"{MP}/v1/account/release_report/novo.csv").mock(
            return_value=Response(200, headers={"content-type": "text/csv"}, text=CSV_JLAS2)
        )
        router.post(f"{MP}/v1/account/release_report").mock(return_value=Response(202, json={}))
        r2 = await ext.run_extrato_sync(db, loja, cliente, hoje=date(2026, 10, 15))
    assert len(r2["lidos"]) == 1 and r2["repedidos"] == [] and r2["erros"] == []
    assert sync.status == "lido" and sync.arquivo == "novo.csv" and sync.erro is None


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


_LINHAS_JLAS2 = CSV_JLAS2.split("\n")
# a linha da venda (payment) duas vezes, idêntica, como o Mercado Pago faz
CSV_COM_REPETIDA = "\n".join(_LINHAS_JLAS2[:5] + [_LINHAS_JLAS2[4]] + _LINHAS_JLAS2[5:])


def test_numerar_repetidas_da_sufixo_pela_ordem_do_arquivo():
    movs = [{"id_externo": "a"}, {"id_externo": "b"}, {"id_externo": "a"}, {"id_externo": "a"}]
    assert ext.numerar_repetidas(movs) == 2
    assert [m["id_externo"] for m in movs] == ["a", "b", "a#2", "a#3"]


async def test_ingerir_csv_aceita_duas_linhas_identicas_no_mesmo_arquivo(db, make_user):
    """16/10 (revisão): duas linhas idênticas tinham a mesma identidade e o upsert em lote
    caía ("cannot affect row a second time") — a janela nunca era lida. A 2ª ganha #2,
    estável entre releituras."""
    loja, _integ = await _loja(db, make_user)
    db.add(
        _pedido_bling(
            "301102", "2000018405611066", datetime(2026, 9, 11, 13, tzinfo=UTC), "880.98", 500.0
        )
    )
    await db.commit()
    stats = await ext.ingerir_csv(db, loja, CSV_COM_REPETIDA)
    assert stats["movimentos"] == 7 and stats["inseridos"] == 7
    assert stats["repetidas_no_arquivo"] == 1 and stats["pedido_bling_resolvido"] == 2
    de_novo = await ext.ingerir_csv(db, loja, CSV_COM_REPETIDA)
    assert de_novo["inseridos"] == 0 and de_novo["ja_existiam"] == 7
    vendas = (
        (
            await db.execute(
                __import__("sqlalchemy")
                .select(ConciliacaoMovimento)
                .where(ConciliacaoMovimento.tipo == "venda_liberada")
                .order_by(ConciliacaoMovimento.id_externo)
            )
        )
        .scalars()
        .all()
    )
    assert len(vendas) == 2 and vendas[1].id_externo == f"{vendas[0].id_externo}#2"
    assert {v.pedido_bling for v in vendas} == {"301102"}


async def test_reidentificar_nao_colide_quando_a_gemea_completa_ja_existe(db, make_user):
    """16/10 (revisão): a pobre casava pela chave natural e trocava de identidade sem
    olhar se a identidade nova já estava gravada → UniqueViolation e a leitura inteira
    caía. Agora a pobre fica e some como duplicata."""
    loja, _integ = await _loja(db, make_user)
    await ext.ingerir_csv(db, loja, CSV_JLAS2)
    venda = (
        await db.execute(
            __import__("sqlalchemy")
            .select(ConciliacaoMovimento)
            .where(ConciliacaoMovimento.tipo == "venda_liberada")
        )
    ).scalar_one()
    pobre = ConciliacaoMovimento(
        integration_id=loja.integration_id,
        platform=loja.platform,
        origem="mp_extrato",
        id_externo="pobre-antiga",
        data=venda.data,
        tipo=venda.tipo,
        descricao=venda.descricao,
        pagamento_id=venda.pagamento_id,
        credito=venda.credito,
        debito=venda.debito,
        raw={},
    )
    db.add(pobre)
    await db.commit()
    stats = await ext.ingerir_csv(db, loja, CSV_JLAS2)  # antes: UniqueViolation aqui
    assert stats["reidentificados"] == 0 and stats["colisoes_de_identidade"] == 1
    assert stats["duplicatas_apagadas"] == 1 and stats["inseridos"] == 0
    todas = (
        (await db.execute(__import__("sqlalchemy").select(ConciliacaoMovimento))).scalars().all()
    )
    assert len(todas) == 6 and all(m.id_externo != "pobre-antiga" for m in todas)


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


async def test_apagar_duplicatas_pobres_passa_o_vinculo_manual_pra_gemea(db, make_user):
    """16/10 (revisão): a pobre vinculada à mão ficava junto com a gêmea completa →
    recebido em dobro. A pobre some e o vínculo vai pra completa quando ela não tem."""
    loja, _integ = await _loja(db, make_user)
    d = lambda dia, h=10: datetime(2026, 10, dia, h, tzinfo=UTC)  # noqa: E731
    quando = datetime(2026, 10, 16, 15, tzinfo=UTC)
    pobre1 = _mov(loja, "venda_liberada", d(5), credito="80", pagamento="P2")
    pobre1.vinculado_por, pobre1.vinculado_em = "cairo", quando
    pobre1.pedido_bling, pobre1.bling_id = "301300", 3013000
    cheia1 = _mov(
        loja,
        "venda_liberada",
        d(5),
        credito="80",
        pedido_mkt="2000018400000002",
        pagamento="P2",
        sub="release",
    )
    # a completa já tem pedido (resolvido pelo nº da venda): fica o dela, sem "vinculado por"
    pobre2 = _mov(loja, "venda_liberada", d(6), credito="90", pagamento="P3")
    pobre2.vinculado_por, pobre2.vinculado_em = "cairo", quando
    pobre2.pedido_bling, pobre2.bling_id = "301301", 3013010
    cheia2 = _mov(
        loja,
        "venda_liberada",
        d(6),
        credito="90",
        pedido_mkt="2000018400000003",
        pedido_bling="301999",
        pagamento="P3",
        sub="release",
    )
    cheia2.bling_id = 3019990
    db.add_all([pobre1, cheia1, pobre2, cheia2])
    await db.commit()
    assert await ext.apagar_duplicatas_pobres(db, loja) == 2
    await db.commit()
    movs = (await db.execute(__import__("sqlalchemy").select(ConciliacaoMovimento))).scalars().all()
    assert {m.id for m in movs} == {cheia1.id, cheia2.id}
    assert (cheia1.pedido_bling, cheia1.bling_id) == ("301300", 3013000)
    assert (cheia1.vinculado_por, cheia1.vinculado_em) == ("cairo", quando)
    assert (cheia2.pedido_bling, cheia2.bling_id) == ("301999", 3019990)
    assert cheia2.vinculado_por is None and cheia2.vinculado_em is None


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
    # 6 chamadas: períodos (lista vazia → mês do calendário) + 4 páginas + resumo
    assert r["inseridos"] == 4 and r["chamadas"] == 6 and not r["erros"]
    assert r["periodo_aberto"] == {"chave": "2026-10-01", "pela_api": False}
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
    assert pausas == [13.0] * 5  # dorme entre as chamadas, nunca antes da primeira
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


# Como a jlas2 (sondagem de 14/10): o período de cobrança vai do dia 23 ao 22 e a chave
# é o mês em que ele termina.
PERIODOS_JLAS2 = [
    {
        "key": "2026-11-01",
        "period": {
            "date_from": "2026-10-23T00:00:00.000-03:00",
            "date_to": "2026-11-22",
            "expiration_date": "2026-11-30",
        },
        "period_status": "OPEN",
    },
    {"key": "2026-10-01", "period": {"date_from": "2026-09-23", "date_to": "2026-10-22"}},
    {"key": "2026-09-01", "period": {"date_from": "2026-08-23", "date_to": "2026-09-22"}},
    {"key": "quebrado", "period": {"date_from": None, "date_to": "x"}},
]


def test_periodo_que_contem_usa_as_datas_reais_da_api():
    assert fat.periodo_que_contem(PERIODOS_JLAS2, date(2026, 10, 25)) == (
        "2026-11-01",
        "2026-10-01",
    )
    assert fat.periodo_que_contem(PERIODOS_JLAS2, date(2026, 10, 10)) == (
        "2026-10-01",
        "2026-09-01",
    )
    # só o mais antigo da lista: o anterior vai pelo calendário
    assert fat.periodo_que_contem(PERIODOS_JLAS2, date(2026, 9, 1)) == ("2026-09-01", "2026-08-01")
    assert fat.periodo_que_contem(PERIODOS_JLAS2, date(2026, 8, 1)) is None
    assert fat.periodo_que_contem([], date(2026, 10, 25)) is None


async def test_run_fatura_sync_le_o_periodo_real_e_cai_no_calendario_sem_a_lista(db, make_user):
    """16/10 (revisão): do dia 23 ao 31 o período aberto da jlas2 é o do mês seguinte;
    pelo calendário as cobranças desses dias só entravam no dia 1º."""
    loja, _integ = await _loja(db, make_user)
    paginas = {
        ("2026-11-01", "CHARGE", 0): {"results": [_det(20, "CVVML", 10.0)], "last_id": 20},
        ("2026-10-01", "CHARGE", 0): {"results": [_det(21, "CVVML", 11.0)], "last_id": 21},
    }

    async def dormir(_s: float) -> None:
        return None

    cliente = _ClienteFalso(paginas, periodos=PERIODOS_JLAS2)
    r = await fat.run_fatura_sync(db, loja, cliente, hoje=date(2026, 10, 25), dormir=dormir)
    assert r["periodo_aberto"] == {"chave": "2026-11-01", "pela_api": True}
    assert {k.split("|")[0] for k in r["periodos"]} == {"2026-11-01", "2026-10-01"}
    assert r["inseridos"] == 2 and not r["erros"]
    chaves = {
        s.chave
        for s in (await db.execute(__import__("sqlalchemy").select(ConciliacaoSync))).scalars()
    }
    assert "2026-11-01|resumo" in chaves and "2026-10-01|CHARGE" in chaves
    # a lista de períodos falhou: calendário (e não é erro da fatura)
    cliente2 = _ClienteFalso(paginas, periodos_http=500)
    r2 = await fat.run_fatura_sync(db, loja, cliente2, hoje=date(2026, 10, 25), dormir=dormir)
    assert r2["periodo_aberto"] == {"chave": "2026-10-01", "pela_api": False}
    assert "HTTP 500" in r2["periodos_api"]["erro"] and not r2["erros"]
    assert {k.split("|")[0] for k in r2["periodos"]} == {"2026-10-01", "2026-09-01"}


async def test_job_da_fatura_tenta_de_novo_quando_a_trava_da_loja_esta_ocupada(monkeypatch):
    """16/10 (revisão): fatura e extrato usam a mesma trava por loja; no mesmo minuto a
    fatura era pulada e só voltava no dia seguinte. Agora o job retenta em 3 min."""
    from arq import Retry

    from app import worker
    from app.services import conciliacao_jobs as jobs

    chamadas: list[tuple[bool, bool]] = []

    async def _travada(integration_id, *, fatura, extrato, recalculo):
        chamadas.append((fatura, extrato))
        return {"skipped": "rodada_em_andamento", "integration_id": str(integration_id)}

    monkeypatch.setattr(jobs, "sincronizar_loja", _travada)
    uid = str(uuid4())
    with pytest.raises(Retry) as exc:
        await worker.conciliacao_sincronizar_loja({"job_try": 1}, uid, True, False, True)
    assert exc.value.defer_score == 3 * 60 * 1000
    with pytest.raises(Retry):
        await worker.conciliacao_sincronizar_loja({"job_try": 2}, uid, True, False, True)
    # 3ª tentativa: desiste sem estourar
    r = await worker.conciliacao_sincronizar_loja({"job_try": 3}, uid, True, False, True)
    assert r["skipped"] == "rodada_em_andamento"
    # só extrato: o tick repete a cada 10 min, não retenta
    r = await worker.conciliacao_sincronizar_loja({"job_try": 1}, uid, False, True, True)
    assert r["skipped"] == "rodada_em_andamento"
    assert chamadas == [(True, False)] * 3 + [(False, True)]


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
    # reembolso ao cliente é gravado negativo (sinal da Margem); aqui vira 20,00 que SAIU
    db.add(Refund(pedido_bling="299377", conta="jlas2", tipo="Cliente", reembolso=-20.0))
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


async def test_saldo_anterior_resolve_o_empate_do_ultimo_segundo(db, make_user):
    """kia (ml) 10/10: +7,04 / −7,04 / +7,04 no mesmo segundo; o saldo depois do grupo é o
    de antes + a soma, não o da linha que a cadeia escolher por último."""
    from app.routers.conciliacao import _saldo_anterior

    loja, _integ = await _loja(db, make_user)
    d = lambda dia, h, mi=0, s=0: datetime(2026, 10, dia, h, mi, s, tzinfo=UTC)  # noqa: E731
    antes = _mov(loja, "venda_liberada", d(10, 12), credito="100")
    antes.saldo_apos = Decimal("-7.04")
    a = _mov(loja, "venda_liberada", d(10, 13, 45, 48), credito="7.04")
    a.saldo_apos = Decimal("0")
    b = _mov(loja, "reserva_disputa", d(10, 13, 45, 48), debito="7.04")
    b.saldo_apos = Decimal("-7.04")
    c = _mov(loja, "reserva_disputa", d(10, 13, 45, 48), credito="7.04")
    c.saldo_apos = Decimal("0")
    db.add_all([antes, a, b, c])
    await db.commit()
    assert await _saldo_anterior(db, loja, date(2026, 10, 11)) == Decimal("0")


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
    # 16/10, revisão: observação que parece fórmula e com caractere de controle
    db.add(await _pedido_conciliado(loja, observacao='=HYPERLINK("http://x")\x07 conferir'))
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
    # texto vai como texto (nunca fórmula) e sem o caractere de controle
    obs = ws.cell(row=2, column=linhas[0].index("Observação") + 1)
    assert obs.value == '=HYPERLINK("http://x") conferir' and obs.data_type == "s"


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
    # os pedidos das linhas do dia existem no Bling da loja (16/10: pedido que saiu da
    # loja vira "fora da loja" e solta as linhas — não é o caso aqui)
    db.add(
        _pedido_bling(
            "301102", "2000018405611066", datetime(2026, 10, 1, 12, tzinfo=UTC), "880.98", 500.0
        )
    )
    db.add(
        _pedido_bling(
            "299520", "2000018737867860", datetime(2026, 9, 28, 12, tzinfo=UTC), "459.00", 210.0
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


async def test_resumo_le_o_dia_de_ontem_e_monta_onde_agir(
    db, make_user, auth_as, client, monkeypatch
):
    monkeypatch.setattr("app.routers.conciliacao._ontem", lambda: date(2026, 10, 15))
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
    # só o dia (16/10): o que trava o dia e o que aconteceu nele (o estornado não tem
    # data de movimento no exemplo → não é "do dia")
    assert linha["problemas_dia"] == ["1 linha(s) sem dono"] and linha["obs_dia"] == []
    assert linha["eventos_dia"]["estornados"] == 0
    # bloco Dias: fixo nos 15 dias até ontem (15/10), não no dia pedido; 13/10 com a
    # jlas2 pendente
    dias = corpo["dias"]
    assert len(dias) == 15 and dias[0]["dia"] == "2026-10-15" and dias[-1]["dia"] == "2026-10-01"
    d13 = dias[2]
    assert d13["dia"] == "2026-10-13" and d13["com_pendencia"] == 1 and d13["fechados"] == 0
    assert d13["pendentes"] == [{"conta": "jlas2", "motivo": "1 linha(s) sem dono"}]
    assert dias[3]["dia"] == "2026-10-12" and dias[3]["sem_movimento"] == 1
    # sem movimento (extrato lido) conta como fechado; só o 13 (pendente) fica fora
    assert dias[3]["fechados"] == 1 and d13["fechados"] == 0
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

    # fatura do ML com erro (16/10, lucas mei 403): não fica verde — "fatura não lida"
    loja.ultimo_extrato_em = datetime.now(UTC)
    loja.ultimo_erro = "fatura: HTTP 403 PolicyAgent"
    await db.commit()
    r = await client.get("/api/conciliacao/resumo", params={"dia": "2026-10-12"})
    linha = r.json()["lojas"][0]
    assert linha["dia"]["status"] == "fatura_nao_lida"
    assert linha["problemas_dia"] == ["fatura não lida · HTTP 403 PolicyAgent"]
    assert any("fatura do ML não lida" in a["titulo"] for a in r.json()["agir"])
    assert r.json()["dias"][3]["pendentes"] == [{"conta": "jlas2", "motivo": "fatura não lida"}]
    loja.ultimo_erro = None
    loja.ultimo_extrato_em = datetime.now(UTC) - timedelta(days=3)
    await db.commit()
    # um dia sem movimento e sem leitura: "extrato não lido"; com leitura: "sem movimento"
    r = await client.get("/api/conciliacao/resumo", params={"dia": "2026-10-12"})
    assert r.json()["lojas"][0]["dia"]["status"] == "extrato_nao_lido"
    loja.ultimo_extrato_em = datetime.now(UTC)
    await db.commit()
    r = await client.get("/api/conciliacao/resumo", params={"dia": "2026-10-12"})
    assert r.json()["lojas"][0]["dia"]["status"] == "sem_movimento"
    assert r.json()["lojas"][0]["dia"]["saldo_bate"] is None


async def test_resumo_dia_pronto_e_dia_fechado(db, make_user, auth_as, client, monkeypatch):
    monkeypatch.setattr("app.routers.conciliacao._ontem", lambda: date(2026, 10, 15))
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
    # o estornado aconteceu nesse dia (data do último movimento): vira obs do dia
    estornado = (
        await db.execute(
            __import__("sqlalchemy")
            .select(ConciliacaoPedido)
            .where(ConciliacaoPedido.pedido_bling == "299520")
        )
    ).scalar_one()
    estornado.ultimo_movimento_em = datetime(2026, 10, 13, 15, tzinfo=UTC)
    await db.commit()
    r = await client.get("/api/conciliacao/resumo", params={"dia": "2026-10-13"})
    linha = r.json()["lojas"][0]
    assert linha["dia"]["status"] == "fechado" and linha["dia"]["fechado_por"]
    assert linha["gravidade"] == "warn"  # ainda tem o estornado depois
    assert linha["problemas_dia"] == [] and linha["obs_dia"] == ["1 estornado(s) depois"]
    assert r.json()["cartoes"]["dias_fechados"] == 1
    dia13 = r.json()["dias"][2]
    assert dia13["fechados"] == 1 and dia13["com_pendencia"] == 0 and dia13["pendentes"] == []
    assert dia13["obs"] == ["1 estornado(s) depois (jlas2)"]


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


async def _limpar_ouvidoria(db) -> None:
    # as tabelas da Ouvidoria não estão no `_CLEANUP_TABLES` do conftest: a ocorrência
    # do teste anterior (mesma chave) faria o vigia contar "persistem" em vez de "novas"
    from sqlalchemy import text

    for tbl in ("ouvidoria_ocorrencias", "ouvidoria_rodadas"):
        await db.execute(text(f"DELETE FROM {tbl}"))  # noqa: S608
    await db.commit()


async def test_vigia_conciliacao_registra_o_onde_agir(db, make_user, monkeypatch):
    from sqlalchemy import select

    from app.models import OuvidoriaOcorrencia
    from app.services import ouvidoria as ouv
    from app.services.vigia_conciliacao import ROBO, vigia_conciliacao_run

    # 16/10, revisão: o aviso é carimbado com a hora do COMEÇO da rodada
    visto: dict = {}
    original_avisar = ouv.avisar_pendentes

    async def espiao(session, robo, *, agora=None):
        visto["agora"] = agora
        return await original_avisar(session, robo, agora=agora)

    monkeypatch.setattr(ouv, "avisar_pendentes", espiao)
    quando = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
    await _limpar_ouvidoria(db)
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
        r = await vigia_conciliacao_run(db, agora=quando)
    finally:
        rc.montar_resumo = original
    assert visto["agora"] == quando
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
    # a chave do extrato leva o dia (16/10): cada dia aberto é uma ocorrência
    assert chaves == {"jlas2:extrato:dia=2026-10-13", "jlas2:pedidos:situacao=estornado_depois"}
    extrato = next(o for o in ocorrencias if o.chave == "jlas2:extrato:dia=2026-10-13")
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


def test_erro_da_leitura_le_erro_e_lista_de_erros():
    """16/10 (revisão): os leitores devolvem `erros` (lista) e a rodada só olhava `erro`,
    zerando o aviso da loja na mesma rodada."""
    from app.services.conciliacao_jobs import _erro_da_leitura

    assert _erro_da_leitura(None) is None
    assert _erro_da_leitura({"inseridos": 2, "erros": []}) is None
    assert _erro_da_leitura({"erro": "x"}) == "x"
    assert (
        _erro_da_leitura({"erros": [{"janela": "2026-10-01..2026-10-02", "erro": "HTTP 500"}]})
        == "2026-10-01..2026-10-02: HTTP 500"
    )
    assert _erro_da_leitura({"erros": [{"extrato": "77", "erro": "timeout"}]}) == "77: timeout"
    assert _erro_da_leitura({"erros": ["boom"]}) == "boom"


def test_reabertura_olha_a_hora_em_que_a_linha_entrou_no_painel():
    """16/10 (revisão): o relatório de ontem é lido hoje de madrugada — uma mediação de
    ontem 10h não reabria o pedido finalizado ontem 15h (comparava a data da plataforma)."""
    from types import SimpleNamespace

    d = datetime(2026, 10, 15, 13, tzinfo=UTC)
    assert calc._entrou_em(SimpleNamespace(data=d, created_at=None)) == d
    assert calc._entrou_em(
        SimpleNamespace(data=d, created_at=d + timedelta(days=1))
    ) == d + timedelta(days=1)
    assert calc._entrou_em(SimpleNamespace(data=d, created_at=d - timedelta(days=1))) == d


# --- revisão de 16/10, lote 2 (regras aprovadas pelo Vinicius) ---------------------


def test_situacao_nada_liberou_mas_mexeu_dinheiro_e_bling_excluido_e_previsao_estimada():
    agora = datetime(2026, 10, 15, 12, tzinfo=UTC)
    loja = ConciliacaoLoja(platform=IntegrationPlatform.ML, conta="x", inicio=date(2026, 10, 1))
    loja.integration_id = None
    base = {
        "agora": agora,
        "data_pedido": datetime(2026, 10, 10, 12, tzinfo=UTC),
        "bling_total": Decimal("100.00"),
        "venda": None,
        "previsao": None,
        "fatura": {},
    }
    # TikTok devolvido antes de liquidar / frete de devolução: saiu 30 sem nada liberar
    saida = _mov(loja, "estorno", datetime(2026, 10, 12, 12, tzinfo=UTC), debito="30.00")
    out = calc.situacao_do_pedido(movs_extrato=[saida], **base)
    assert out["situacao"] == "estornado_depois" and out["zero_a_zero"] is False
    assert out["motivo"] == "nada liberou e saiu 30.00"
    # excluído no Bling, nada entrou: cancelado (zero a zero)
    out = calc.situacao_do_pedido(
        movs_extrato=[], bling_cancelado=True, bling_situacao="excluido", **base
    )
    assert out["situacao"] == "cancelado" and out["zero_a_zero"] is True
    assert out["motivo"] == "excluido no Bling, nada entrou"
    # Shopee sem previsão da plataforma (venda lida, nada liberado): entregue + 7 dias
    # (estimativa); passou → vencido
    venda = MarketplaceOrderFinancial(
        gross_amount=Decimal("100"), net_amount=Decimal("90"), fee_amount=Decimal("10"), raw={}
    )
    com_venda = {**base, "venda": venda}
    entregue = datetime(2026, 10, 11, 15, tzinfo=UTC)
    out = calc.situacao_do_pedido(
        movs_extrato=[], plataforma="shopee", entregue_em=entregue, **com_venda
    )
    assert out["situacao"] == "aguardando" and out["motivo"] == "libera em 18/10 (estimativa)"
    out = calc.situacao_do_pedido(
        movs_extrato=[],
        plataforma="shopee",
        entregue_em=entregue,
        **{**com_venda, "agora": datetime(2026, 10, 21, 12, tzinfo=UTC)},
    )
    assert out["situacao"] == "vencido" and "18/10 (estimada)" in out["motivo"]
    # sem entrega lida: pedido + 20 dias
    out = calc.situacao_do_pedido(movs_extrato=[], plataforma="tiktok", **com_venda)
    assert out["motivo"] == "libera em 30/10 (estimativa)"
    # ML não ganha estimativa (a plataforma diz a data)
    out = calc.situacao_do_pedido(movs_extrato=[], plataforma="ml", **com_venda)
    assert out["situacao"] == "aguardando" and out["motivo"] is None


def test_etapa_reaberto_pela_pessoa_fica_e_bateu_em_zera():
    agora = datetime(2026, 10, 15, 12, tzinfo=UTC)
    # a pessoa reabriu e o pedido bateu: continua reaberto (só ela fecha), motivo intacto
    atual = ConciliacaoPedido(
        etapa="reaberto",
        etapa_por="cairo@x",
        etapa_motivo="conferir o frete",
        bateu_em=agora - timedelta(days=9),
        etapa_em=agora - timedelta(days=8),
    )
    e = calc.etapa_do_pedido(agora=agora, atual=atual, situacao="bateu", ultimo_movimento_em=None)
    assert e == {"bateu_em": agora - timedelta(days=9), "etapa": "reaberto"}
    # reaberto pelo robô (movimento novo) e bateu de novo: vira conciliado como antes
    atual.etapa_por = "robô"
    atual.bateu_em = agora - timedelta(days=2)
    e = calc.etapa_do_pedido(agora=agora, atual=atual, situacao="bateu", ultimo_movimento_em=None)
    assert e["etapa"] == "conciliado"
    # deixou de bater: a marca do bateu sai
    atual = ConciliacaoPedido(etapa="conciliado", bateu_em=agora - timedelta(days=2))
    e = calc.etapa_do_pedido(
        agora=agora, atual=atual, situacao="recebido_a_menos", ultimo_movimento_em=None
    )
    assert e["bateu_em"] is None and e["etapa"] == "pendencia"
    # cancelado com dinheiro ≠ 0 não é zero a zero: pendência, não finaliza sozinho
    e = calc.etapa_do_pedido(
        agora=agora, atual=None, situacao="cancelado", ultimo_movimento_em=None, zero_a_zero=False
    )
    assert e["etapa"] == "pendencia" and e["bateu_em"] is None


def test_conta_do_pack_do_ml_soma_as_linhas_do_carrinho():
    """Poofy 301748 (16/10): 12 pagamentos de 49,00 no mesmo pack e a venda da Margem
    com um item só — o previsto vem das linhas da conta."""
    loja = ConciliacaoLoja(platform=IntegrationPlatform.ML, conta="x", inicio=date(2026, 10, 1))
    loja.integration_id = None
    libs = []
    for i in range(3):
        m = _mov(
            loja,
            "venda_liberada",
            datetime(2026, 10, 12, 12, i, tzinfo=UTC),
            credito="33.88",
            pedido_mkt=f"20000{i}",
            tarifa="-15.12",
            frete="0",
        )
        m.pack_id = "2000015384524949"
        m.bruto = Decimal("49.00")
        libs.append(m)
    conta = calc.conta_ml_do_pack(libs)
    assert conta["pedidos"] == 3 and conta["valor"] == Decimal("147.00")
    assert conta["tarifa"] == Decimal("45.36") and conta["liquido"] == Decimal("101.64")
    # um pedido só no pack: a venda da Margem serve
    assert calc.conta_ml_do_pack(libs[:1]) is None
    out = calc.situacao_do_pedido(
        agora=datetime(2026, 10, 15, 12, tzinfo=UTC),
        data_pedido=datetime(2026, 10, 10, 12, tzinfo=UTC),
        bling_total=Decimal("147.00"),
        venda=None,
        previsao=None,
        movs_extrato=libs,
        fatura={},
        liquido_previsto=conta["liquido"],
    )
    assert out["situacao"] == "bateu" and out["recebido"] == Decimal("101.64")


async def test_fechar_dia_so_ate_ontem_saldo_diferente_exige_motivo_e_reaberto_a_mao_fica(
    db, make_user, auth_as, client
):
    from zoneinfo import ZoneInfo

    from app.models import ConciliacaoDia
    from app.services.conciliacao_jobs import fechar_dias_prontos

    loja, _integ = await _loja(db, make_user)
    movs = await _dia_de_exemplo(db, loja)
    for numero, numeroloja, total, custo in (
        ("301500", "2000018600988674", "48.00", 20.0),
        ("301102", "2000018405611066", "880.98", 500.0),
        ("299520", "2000018737867860", "459.00", 210.0),
    ):
        db.add(
            _pedido_bling(numero, numeroloja, datetime(2026, 10, 1, 12, tzinfo=UTC), total, custo)
        )
    await db.commit()
    quem = await make_user(permissions={"conciliacao": {"view": True, "edit": True}})
    auth_as(quem)
    # hoje (e o futuro) não fecha
    hoje = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    r = await client.post(f"/api/conciliacao/dias/{hoje}/fechar", params={"loja": "jlas2"})
    assert r.status_code == 422
    # a pendência ganha dono
    r = await client.patch(
        f"/api/conciliacao/movimentos/{movs['pendente'].id}", json={"pedido_bling": "301500"}
    )
    assert r.status_code == 200, r.text
    # saldo informado diferente do calculado (+1,00 na última linha): exige motivo
    r = await client.get("/api/conciliacao/extrato", params={"loja": "jlas2", "dia": "2026-10-13"})
    ultima_id = r.json()["movimentos"][-1]["id"]
    saldo_final = Decimal(str(r.json()["totais"]["saldo_final"]))
    ultima = next(m for m in movs.values() if str(m.id) == ultima_id)
    ultima.saldo_apos = saldo_final + Decimal("1.00")
    await db.commit()
    r = await client.post("/api/conciliacao/dias/2026-10-13/fechar", params={"loja": "jlas2"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "saldo_nao_bate"
    assert abs(r.json()["detail"]["diferenca"]) == 1.0
    r = await client.post(
        "/api/conciliacao/dias/2026-10-13/fechar",
        params={"loja": "jlas2", "motivo": "ajuste da plataforma sem lançamento"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["fechado"]["motivo"] == "ajuste da plataforma sem lançamento"
    # reabrir à mão fica gravado…
    r = await client.post(
        "/api/conciliacao/dias/2026-10-13/reabrir",
        params={"loja": "jlas2", "motivo": "conferir de novo"},
    )
    assert r.status_code == 200 and r.json()["reaberto"]["por"] == quem.email
    # …e o robô não refecha, mesmo com tudo casado e batendo
    ultima.saldo_apos = saldo_final
    await db.commit()
    saida = await fechar_dias_prontos(db, loja)
    assert "2026-10-13" in saida["reabertos_a_mao"] and "2026-10-13" not in saida["fechados"]
    r = await client.get("/api/conciliacao/resumo", params={"dia": "2026-10-13"})
    linha = r.json()["lojas"][0]
    assert "dia reaberto por " + quem.email + ": conferir de novo" in linha["problemas_dia"]
    # a pessoa fecha: o rastro sai e o robô volta a cuidar do dia
    r = await client.post("/api/conciliacao/dias/2026-10-13/fechar", params={"loja": "jlas2"})
    assert r.status_code == 200 and r.json()["fechado"]["motivo"] is None
    dia = (await db.execute(__import__("sqlalchemy").select(ConciliacaoDia))).scalar_one()
    await db.refresh(dia)
    assert dia.reaberto_por is None and dia.fechado_motivo is None


async def test_linha_nova_em_dia_fechado_reabre_o_dia_pelo_robo(db, make_user):
    from app.models import ConciliacaoDia
    from app.services.conciliacao_shopee import inserir_movimentos

    loja, _integ = await _loja(db, make_user)
    db.add(
        ConciliacaoDia(
            integration_id=loja.integration_id,
            dia=date(2026, 10, 13),
            fechado_em=datetime(2026, 10, 14, 9, tzinfo=UTC),
            fechado_por="cairo@x",
        )
    )
    await db.commit()
    nova = {
        "origem": "mp_extrato",
        "id_externo": "atrasada-1",
        "data": _d13(15),
        "tipo": "credito",
        "descricao": "linha que o relatório trouxe depois",
        "sub_tipo": None,
        "pagamento_id": None,
        "pedido_marketplace": None,
        "pack_id": None,
        "shipping_id": None,
        "pedido_bling": None,
        "bling_id": None,
        "bruto": None,
        "tarifa": None,
        "parcelamento": None,
        "frete": None,
        "cupom": None,
        "credito": Decimal("10.00"),
        "debito": Decimal("0"),
        "saldo_apos": None,
        "descontado_da_venda": None,
        "sku": None,
        "item_id": None,
        "data_aprovacao": None,
        "classificacao": None,
        "vinculado_por": None,
        "vinculado_em": None,
        "raw": {},
    }
    assert await inserir_movimentos(db, loja, [nova]) == 1
    dia = (await db.execute(__import__("sqlalchemy").select(ConciliacaoDia))).scalar_one()
    assert dia.fechado_em is None and dia.reaberto_por == "robô"
    assert dia.reaberto_motivo == "linha nova no extrato"
    # a mesma linha de novo (identidade): nada muda
    dia.reaberto_motivo = None
    await db.commit()
    assert await inserir_movimentos(db, loja, [dict(nova)]) == 0
    assert dia.reaberto_motivo is None


async def test_onde_agir_ignora_finalizado_e_lista_recebido_a_mais_e_fora_da_loja(
    db, make_user, auth_as, client
):
    loja, _integ = await _loja(db, make_user)
    agora = datetime.now(UTC)
    for numero, situacao, etapa in (
        ("1", "estornado_depois", "finalizado"),
        ("2", "recebido_a_mais", "pendencia"),
        ("3", "fora_da_loja", "pendencia"),
        ("4", "sem_venda", "aguardando"),
        ("5", "sem_venda", "pendencia"),
    ):
        db.add(
            ConciliacaoPedido(
                integration_id=loja.integration_id,
                pedido_bling=numero,
                platform=IntegrationPlatform.ML,
                conta="jlas2",
                situacao=situacao,
                etapa=etapa,
                etapa_em=agora,
                ultimo_movimento_em=agora,
                liberado_em=agora,
            )
        )
    await db.commit()
    auth_as(await make_user(permissions={"conciliacao": {"view": True}}))
    r = await client.get("/api/conciliacao/resumo", params={"dia": "2026-10-13"})
    assert r.status_code == 200, r.text
    titulos = [a["titulo"] for a in r.json()["agir"]]
    assert "jlas2 · 1 recebido(s) a mais" in titulos
    assert "jlas2 · 1 venda(s) não lida(s) com dinheiro" in titulos
    assert "jlas2 · 1 pedido(s) que saíram da loja" in titulos
    assert not any("estornado" in t for t in titulos)  # finalizado com motivo não cobra
    ped = r.json()["lojas"][0]["pedidos"]
    assert ped["estornados"] == 0 and ped["recebido_a_mais"] == 1 and ped["fora_da_loja"] == 1


async def _limpar_ouvidoria(db) -> None:
    # as tabelas da Ouvidoria não estão no `_CLEANUP_TABLES` do conftest: a ocorrência
    # do teste anterior (mesma chave) faria o vigia contar "persistem" em vez de "novas"
    from sqlalchemy import text

    for tbl in ("ouvidoria_ocorrencias", "ouvidoria_rodadas"):
        await db.execute(text(f"DELETE FROM {tbl}"))  # noqa: S608
    await db.commit()


async def test_vigia_conciliacao_segura_dia_antigo_aberto(db, make_user, monkeypatch):
    """O vigia olhava só "ontem": dia mais antigo ainda aberto fechava como "sumiu".
    Agora os dias antigos pendentes do bloco "Dias" viram ocorrência e só fecham quando
    o dia deixa de estar pendente."""
    from sqlalchemy import select

    from app.models import OuvidoriaOcorrencia
    from app.services.vigia_conciliacao import ROBO, vigia_conciliacao_run

    monkeypatch.setattr("app.routers.conciliacao._ontem", lambda: date(2026, 10, 15))
    await _limpar_ouvidoria(db)
    loja, _integ = await _loja(db, make_user)
    movs = await _dia_de_exemplo(db, loja)  # 13/10 com 1 linha sem dono; 15/10 sem movimento
    loja.ultimo_extrato_em = datetime.now(UTC)
    await db.commit()
    r = await vigia_conciliacao_run(db)
    assert r["itens"] == 2 and r["novas"] == 2 and r["sumiram"] == 0

    async def abertas() -> dict[str, OuvidoriaOcorrencia]:
        rows = (
            (
                await db.execute(
                    select(OuvidoriaOcorrencia).where(
                        OuvidoriaOcorrencia.robo_chave == ROBO,
                        OuvidoriaOcorrencia.fechada_em.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        return {o.chave: o for o in rows}

    ab = await abertas()
    assert set(ab) == {"jlas2:extrato:dia=2026-10-13", "jlas2:pedidos:situacao=estornado_depois"}
    antigo = ab["jlas2:extrato:dia=2026-10-13"]
    assert "dia 13/10 ainda aberto" in antigo.titulo and "1 linha(s) sem dono" in antigo.titulo
    assert antigo.link == "/conciliacao?loja=jlas2&visao=extrato&dia=2026-10-13"
    # rodada seguinte: o dia continua aberto → persiste, não "sumiu"
    r = await vigia_conciliacao_run(db)
    assert r["persistem"] == 2 and r["sumiram"] == 0
    # deu destino à linha: o dia some do bloco e a ocorrência fecha como "sumiu"
    movs["pendente"].classificacao = "ajuste"
    await db.commit()
    r = await vigia_conciliacao_run(db)
    assert r["sumiram"] == 1 and set(await abertas()) == {"jlas2:pedidos:situacao=estornado_depois"}
    fechada = (
        await db.execute(
            select(OuvidoriaOcorrencia).where(
                OuvidoriaOcorrencia.chave == "jlas2:extrato:dia=2026-10-13"
            )
        )
    ).scalar_one()
    assert fechada.fechamento == "sumiu"


async def test_rotas_de_escrita_exigem_a_loja(db, make_user, auth_as, client):
    """Sem `loja`, a leitura cai na primeira loja (padrão da tela); a escrita responde 422
    em vez de mexer na primeira loja da lista (16/10, revisão)."""
    loja, _integ = await _loja(db, make_user)
    await _dia_de_exemplo(db, loja)
    auth_as(await make_user(permissions={"conciliacao": {"view": True, "edit": True}}))
    r = await client.get("/api/conciliacao/extrato", params={"dia": "2026-10-13"})
    assert r.status_code == 200 and r.json()["loja"] == "jlas2"
    for metodo, rota, kw in (
        ("POST", "/api/conciliacao/dias/2026-10-13/fechar", {}),
        ("POST", "/api/conciliacao/dias/2026-10-13/reabrir", {}),
        ("POST", "/api/conciliacao/dias/2026-10-13/reabrir", {"params": {"loja": " "}}),
        ("PATCH", "/api/conciliacao/pedidos/301102", {"json": {"conferido": True}}),
    ):
        r = await client.request(metodo, rota, **kw)
        assert r.status_code == 422, (rota, r.text)
        assert r.json()["detail"]["code"] == "informe_a_loja"
    r = await client.patch(
        "/api/conciliacao/pedidos/301102", json={"loja": "jlas2", "conferido": True}
    )
    assert r.status_code == 200, r.text


async def test_sincronizar_e_permissao_de_conciliacao(db, make_user, auth_as, client, monkeypatch):
    """ "Sincronizar" exigia Margem ou Logística em editar: quem só tem Conciliação tomava
    403 e quem só tem Logística enfileirava a rodada (16/10, revisão)."""
    import app.routers.conciliacao as rc

    class _Job:
        job_id = "j1"

    class _Pool:
        async def enqueue_job(self, *_a, **_k):
            return _Job()

    async def pool_falso():
        return _Pool()

    monkeypatch.setattr(rc, "get_arq_pool", pool_falso)
    await _loja(db, make_user)
    auth_as(await make_user(permissions={"logistica": {"view": True, "edit": True}}))
    r = await client.post("/api/conciliacao/lojas/jlas2/sincronizar")
    assert r.status_code == 403
    auth_as(await make_user(permissions={"conciliacao": {"view": True}}))
    r = await client.post("/api/conciliacao/lojas/jlas2/sincronizar")
    assert r.status_code == 403
    auth_as(await make_user(permissions={"conciliacao": {"view": True, "edit": True}}))
    r = await client.post("/api/conciliacao/lojas/jlas2/sincronizar")
    assert r.status_code == 200, r.text
    assert r.json() == {"conta": "jlas2", "job_id": "j1", "enfileirado": True}


async def test_carga_inicial_intervalo_invalido_da_422(db, make_user, auth_as, client):
    auth_as(await make_user(permissions={"conciliacao": {"view": True, "edit": True}}))
    for corpo in ({"intervalo_min": "abc"}, {"intervalo_outras_seg": -5}, {"intervalo_min": "nan"}):
        r = await client.post("/api/conciliacao/carga-inicial", json=corpo)
        assert r.status_code == 422, (corpo, r.text)
        assert r.json()["detail"]["code"] == "parametro_invalido"


async def test_recalculo_do_clique_entra_na_trava_da_loja(
    db, make_user, auth_as, client, monkeypatch
):
    """Vincular/classificar recalculava fora da trava da rodada (16/10, revisão): com a
    rodada no meio do caminho o clique responde 409; e enquanto o clique segura a trava,
    o `pg_try_advisory_xact_lock` da rodada falha (ela pula a loja)."""
    from sqlalchemy import text

    import app.db as _db
    from app.services import conciliacao_jobs as jobs
    from app.services.advisory_lock import SYNC_NAMESPACE

    monkeypatch.setattr(jobs, "ESPERA_TRAVA_CLIQUE", "300ms")
    loja, _integ = await _loja(db, make_user)
    movs = await _dia_de_exemplo(db, loja)
    auth_as(await make_user(permissions={"conciliacao": {"view": True, "edit": True}}))
    pend = movs["pendente"]
    chave = {"ns": SYNC_NAMESPACE, "key": jobs._lock_key(loja.integration_id)}
    async with _db.SessionLocal() as rodada:
        got = (
            await rodada.execute(text("SELECT pg_try_advisory_xact_lock(:ns, :key)"), chave)
        ).scalar()
        assert got is True
        r = await client.patch(
            f"/api/conciliacao/movimentos/{pend.id}", json={"classificacao": "ajuste"}
        )
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "rodada_em_andamento"
        await rodada.rollback()
    r = await client.patch(
        f"/api/conciliacao/movimentos/{pend.id}", json={"classificacao": "ajuste"}
    )
    assert r.status_code == 200, r.text
    # o helper direto: com a trava do clique aberta, a rodada não entra
    await jobs.trava_da_loja(db, loja.integration_id)
    async with _db.SessionLocal() as rodada:
        got = (
            await rodada.execute(text("SELECT pg_try_advisory_xact_lock(:ns, :key)"), chave)
        ).scalar()
        assert got is False
    await db.commit()


async def test_fechamento_do_mes_corrente_corta_em_ontem(
    db, make_user, auth_as, client, monkeypatch
):
    """O mês corrente somava até hoje contra o saldo final de ontem (16/10, revisão):
    agora vendas, caixa e dias param no mesmo dia (ontem)."""
    monkeypatch.setattr("app.routers.conciliacao._ontem", lambda: date(2026, 10, 13))
    loja, _integ = await _loja(db, make_user)
    await _dia_de_exemplo(db, loja)  # 13/10 fecha em 200,46
    hoje = _mov(loja, "credito", datetime(2026, 10, 14, 12, tzinfo=UTC), credito="50.00")
    hoje.saldo_apos = Decimal("250.46")
    hoje.classificacao = "ajuste"
    db.add(hoje)
    await db.commit()
    auth_as(await make_user(permissions={"conciliacao": {"view": True}}))
    r = await client.get("/api/conciliacao/fechamento", params={"loja": "jlas2", "mes": "2026-10"})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["ate"] == "2026-10-31" and corpo["ate_corte"] == "2026-10-13"
    assert corpo["caixa"]["linhas"] == 5 and [d["dia"] for d in corpo["dias"]] == ["2026-10-13"]
    assert corpo["caixa"]["saldo_inicial"] == 0.81 and corpo["caixa"]["saldo_final"] == 200.46
    assert corpo["caixa"]["movimento_liquido"] == 199.65 and corpo["caixa"]["bate"] is True


async def test_fechamento_excel_texto_nao_vira_formula(db, make_user, auth_as, client):
    from io import BytesIO

    from openpyxl import load_workbook

    from app.models import ConciliacaoDia

    loja, _integ = await _loja(db, make_user)
    await _dia_de_exemplo(db, loja)
    db.add(
        ConciliacaoDia(
            integration_id=loja.integration_id,
            dia=date(2026, 10, 13),
            fechado_em=datetime.now(UTC),
            fechado_por="=cmd|' /C calc'!A0\x01",
        )
    )
    await db.commit()
    auth_as(await make_user(permissions={"conciliacao": {"view": True}}))
    r = await client.post(
        "/api/conciliacao/fechamento.xlsx", params={"loja": "jlas2", "mes": "2026-10"}
    )
    assert r.status_code == 200, r.text
    por = load_workbook(BytesIO(r.content))["Dias"]["G2"]
    assert por.value == "=cmd|' /C calc'!A0" and por.data_type == "s"


async def test_dinheiro_na_conta_usa_a_ancora_do_dia_anterior(db, make_user):
    """kia (ml) 10/10: reserva e pagamento no mesmo segundo; sem a âncora do fechamento da
    véspera a última linha saía trocada e "na conta" mostrava 0 em vez de 100 (16/10)."""
    from uuid import UUID

    from app.routers.conciliacao import _dinheiro_da_loja

    loja, _integ = await _loja(db, make_user)
    vespera = _mov(loja, "credito", datetime(2026, 10, 12, 15, tzinfo=UTC), credito="100.00")
    vespera.saldo_apos = Decimal("100.00")
    vespera.classificacao = "ajuste"
    quando = datetime(2026, 10, 13, 17, 21, 6, tzinfo=UTC)
    reserva = _mov(loja, "reserva_disputa", quando, debito="100.00")
    reserva.saldo_apos = Decimal("0.00")
    reserva.classificacao = "ajuste"
    reserva.id = UUID("00000000-0000-0000-0000-000000000002")
    pagamento = _mov(loja, "venda_liberada", quando, credito="100.00")
    pagamento.saldo_apos = Decimal("100.00")
    pagamento.classificacao = "ajuste"
    pagamento.id = UUID("00000000-0000-0000-0000-000000000001")
    db.add_all([vespera, reserva, pagamento])
    await db.commit()
    dinheiro = await _dinheiro_da_loja(db, loja)
    # âncora 100 → reserva (100 − 100 = 0) e depois o pagamento (0 + 100 = 100): na conta 100
    assert dinheiro["na_conta"] == 100.0
