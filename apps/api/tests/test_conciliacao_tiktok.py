"""Conciliação — TikTok: o extrato diário e o repasse (passo 4, 15/10/2026)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

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
from app.services.conciliacao_calculo import conta_tiktok_de_transacoes, recalcular_loja
from app.services.conciliacao_tiktok import (
    ROTA_EXTRATOS,
    ROTA_REPASSES,
    repasse_para_movimento,
    run_tiktok_sync,
    transacao_para_movimento,
)

AGORA = datetime(2026, 10, 16, 12, tzinfo=UTC)
PEDIDO = "586488303325120242"


def _meia_noite_utc(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp())


def _tx(tid: int, order_id: str, **kw) -> dict:
    """Uma transação do extrato com os números reais da sondagem de 15/10 (Mini):
    1.300,65 de tabela, 888,35 de desconto da loja, 67,85 de taxas → 344,45."""
    v = {
        "gross": "1300.65",
        "seller_disc": "-888.35",
        "apos": "412.3",
        "revenue": "412.3",
        "commission": "-24.74",
        "tx_fee": "-6.0",
        "affiliate": "-37.11",
        "fee": "-67.85",
        "shipping": "0",
        "settlement": "344.45",
        "tipo": "ORDER",
        "created": 1791510365,
    }
    v.update(kw)
    return {
        "id": str(tid),
        "type": v["tipo"],
        "order_id": order_id,
        "currency": "BRL",
        "order_create_time": v["created"],
        "gross_sales_amount": v["gross"],
        "seller_discount_amount": v["seller_disc"],
        "after_seller_discounts_subtotal_amount": v["apos"],
        "platform_discount_amount": "0",
        "customer_payment_amount": v["apos"],
        "revenue_amount": v["revenue"],
        "platform_commission_amount": v["commission"],
        "transaction_fee_amount": v["tx_fee"],
        "referral_fee_amount": "0",
        "affiliate_commission_amount": v["affiliate"],
        "affiliate_ads_commission_amount": "0",
        "affiliate_partner_commission_amount": "0",
        "fee_amount": v["fee"],
        "actual_shipping_fee_amount": "-23.9",
        "customer_paid_shipping_fee_amount": "23.9",
        "shipping_cost_amount": v["shipping"],
        "adjustment_amount": "0",
        "customer_refund_amount": "0",
        "settlement_amount": v["settlement"],
    }


_ZERADO = {
    "gross": "0",
    "seller_disc": "0",
    "commission": "0",
    "tx_fee": "0",
    "affiliate": "0",
    "fee": "0",
}


def _extrato(sid: int, d: date, total: str, payment_id: int, status: str = "PAID") -> dict:
    t = _meia_noite_utc(d)  # a TikTok fecha o extrato à meia-noite UTC (21h BRT da véspera)
    return {
        "id": str(sid),
        "statement_time": t,
        "currency": "BRL",
        "revenue_amount": total,
        "fee_amount": "0",
        "shipping_cost_amount": "0",
        "adjustment_amount": "0",
        "net_sales_amount": total,
        "settlement_amount": total,
        "payment_id": str(payment_id),
        "payment_status": status,
        "payment_time": t + 12180,
    }


def _repasse(payment_id: int, total: str, d: date) -> dict:
    t = _meia_noite_utc(d)
    return {
        "id": str(payment_id),
        "amount": {"value": total, "currency": "BRL"},
        "bank_account": "***1234",
        "status": "PAID",
        "create_time": t + 12000,
        "paid_time": t + 12180,
    }


class _TikTokFalsa:
    def __init__(
        self, extratos: list[dict], transacoes: dict[str, list[dict]], repasses: list[dict]
    ):
        self.extratos = extratos
        self.transacoes = transacoes
        self.repasses = repasses
        self.chamadas: list[tuple[str, dict | None]] = []

    async def _get(self, path: str, params: dict | None = None) -> dict:
        self.chamadas.append((path, params))
        params = params or {}
        if path == ROTA_EXTRATOS:
            ge, lt = int(params["statement_time_ge"]), int(params["statement_time_lt"])
            lista = [e for e in self.extratos if ge <= e["statement_time"] < lt]
            return {"code": 0, "message": "Success", "data": {"statements": lista}}
        if path == ROTA_REPASSES:
            return {"code": 0, "data": {"payments": self.repasses, "next_page_token": ""}}
        if path.startswith(ROTA_EXTRATOS + "/") and path.endswith("/statement_transactions"):
            sid = path.split("/")[-2]
            return {"code": 0, "data": {"statement_transactions": self.transacoes.get(sid, [])}}
        raise AssertionError(path)


async def _loja_tiktok(db, make_user, *, inicio=date(2026, 10, 1)) -> ConciliacaoLoja:
    dono = await make_user()
    integ = Integration(
        user_id=dono.id,
        platform=IntegrationPlatform.TIKTOK,
        name=" Mini",
        status="active",
        credentials=encrypt_json(
            {"access_token": "tok", "refresh_token": "rt", "shop_cipher": "c"}
        ),
    )
    db.add(integ)
    await db.flush()
    db.add(
        StoreInfo(
            user_id=dono.id, platform="tiktok", account_name="Mini", bling_store_id="204000003"
        )
    )
    loja = ConciliacaoLoja(
        integration_id=integ.id, platform=IntegrationPlatform.TIKTOK, conta="Mini", inicio=inicio
    )
    db.add(loja)
    db.add(
        BlingOrder(
            bling_id=6000,
            numero="320001",
            numeroloja=PEDIDO,
            data=datetime(2026, 10, 9, 3, tzinfo=UTC),
            total=Decimal("412.30"),
            totalprodutos=Decimal("412.30"),
            situacao="Atendido",
            loja="204000003",
            item_index=0,
            preco_custo=200.0,
            item_quantidade=1,
            item_codigo="a18.pr",
        )
    )
    await db.commit()
    return loja


def test_transacao_e_repasse_viram_movimentos():
    st = _extrato(1, date(2026, 10, 14), "344.45", 900)
    m = transacao_para_movimento(_tx(10, PEDIDO), st, ordem=0, saldo_apos=Decimal("344.45"))
    assert m["tipo"] == "venda_liberada" and m["pedido_marketplace"] == PEDIDO
    assert m["credito"] == Decimal("344.45") and m["debito"] == Decimal("0")
    assert m["bruto"] == Decimal("1300.65") and m["tarifa"] == Decimal("67.85")
    assert m["cupom"] == Decimal("888.35") and m["frete"] is None
    assert m["data"] == datetime(2026, 10, 14, 3, tzinfo=UTC)  # 00:00 BRT de 14/10 (dia do banco)
    assert m["origem"] == "tiktok_extrato" and m["classificacao"] is None
    assert m["pagamento_id"] == "900" and m["raw"]["_statement_id"] == "1"
    est = transacao_para_movimento(
        _tx(11, PEDIDO, settlement="-100", revenue="-100", apos="-100", **_ZERADO),
        st,
        ordem=1,
        saldo_apos=Decimal("244.45"),
    )
    assert est["tipo"] == "estorno" and est["debito"] == Decimal("100.00")
    assert est["descricao"] == f"Estorno do pedido {PEDIDO}"
    aj = transacao_para_movimento(
        _tx(12, "", tipo="ADJUSTMENT", settlement="-5", revenue="-5", apos="0", **_ZERADO),
        st,
        ordem=2,
        saldo_apos=Decimal("239.45"),
    )
    assert aj["tipo"] == "ajuste_tiktok" and aj["classificacao"] == "ajuste"
    assert aj["descricao"] == "Ajuste da TikTok (adjustment)" and aj["debito"] == Decimal("5.00")
    assert est["data"] == datetime(2026, 10, 14, 3, 0, 1, tzinfo=UTC)
    rep = repasse_para_movimento(st, _repasse(900, "344.45", date(2026, 10, 14)), ordem=2)
    assert rep["tipo"] == "saque" and rep["debito"] == Decimal("344.45")
    assert rep["saldo_apos"] == Decimal("0") and rep["classificacao"] == "saque"
    assert rep["id_externo"] == "repasse:900" and rep["pagamento_id"] == "900"
    assert "***1234" in rep["descricao"] and "pago 14/10 00:23" in rep["descricao"]
    assert rep["data"] == datetime(2026, 10, 14, 3, 0, 2, tzinfo=UTC)
    assert transacao_para_movimento({"type": "ORDER"}, st, ordem=0, saldo_apos=Decimal(0)) is None


def test_conta_tiktok_soma_as_transacoes_do_pedido():
    c = conta_tiktok_de_transacoes(
        [_tx(10, PEDIDO), _tx(11, PEDIDO, settlement="-50", revenue="-50", apos="-50", **_ZERADO)]
    )
    assert c["vendas_brutas"] == Decimal("1300.65") and c["desconto_loja"] == Decimal("-888.35")
    assert c["apos_desconto"] == Decimal("362.30") and c["receita"] == Decimal("362.30")
    assert c["outros_receita"] == Decimal("0")
    assert c["taxas"] == Decimal("-67.85") and c["comissao"] == Decimal("-24.74")
    assert c["taxa_transacao"] == Decimal("-6.00") and c["afiliados"] == Decimal("-37.11")
    assert c["outras_taxas"] == Decimal("0") and c["frete"] == Decimal("0")
    assert c["liquido"] == Decimal("294.45") and c["outros"] == Decimal("0")
    assert conta_tiktok_de_transacoes([]) is None


async def test_run_tiktok_sync_le_extratos_pagos_e_casa_o_pedido(db, make_user):
    loja = await _loja_tiktok(db, make_user)
    d13, d14, d15 = date(2026, 10, 13), date(2026, 10, 14), date(2026, 10, 15)
    e1, e2 = _extrato(1, d13, "344.45", 901), _extrato(2, d14, "544.45", 902)
    e3 = _extrato(3, d15, "200.00", 903, status="PROCESSING")
    outro = _tx(
        21,
        "586400000000000001",
        gross="200",
        apos="200",
        revenue="200",
        settlement="200",
        seller_disc="0",
        commission="0",
        tx_fee="0",
        affiliate="0",
        fee="0",
    )
    cliente = _TikTokFalsa(
        [e1, e2, e3],
        {
            "1": [_tx(10, PEDIDO)],
            "2": [_tx(20, PEDIDO), outro],
            "3": [_tx(30, PEDIDO, settlement="200", revenue="200", apos="200", **_ZERADO)],
        },
        [_repasse(901, "344.45", d13), _repasse(902, "544.45", d14)],
    )
    r = await run_tiktok_sync(db, loja, cliente, agora=AGORA)
    assert r["inseridos"] == 5 and r["pulados_processando"] == 1 and not r["erros"]
    assert [x["extrato"] for x in r["extratos"]] == ["1", "2"]
    movs = (
        (await db.execute(select(ConciliacaoMovimento).order_by(ConciliacaoMovimento.data)))
        .scalars()
        .all()
    )
    assert [m.tipo for m in movs] == [
        "venda_liberada",
        "saque",
        "venda_liberada",
        "venda_liberada",
        "saque",
    ]
    assert movs[0].pedido_bling == "320001" and movs[0].bling_id == 6000  # casou pelo order_id
    assert movs[0].data == datetime(2026, 10, 13, 3, tzinfo=UTC)
    assert [m.saldo_apos for m in movs[2:]] == [Decimal("344.45"), Decimal("544.45"), Decimal("0")]
    assert movs[3].pedido_bling is None  # pedido que o Bling ainda não importou
    assert movs[4].debito == Decimal("544.45") and "pago 14/10 00:23" in movs[4].descricao
    syncs = (
        (await db.execute(select(ConciliacaoSync).order_by(ConciliacaoSync.chave))).scalars().all()
    )
    assert [(s.chave, s.status, s.linhas) for s in syncs] == [("1", "lido", 2), ("2", "lido", 3)]
    assert loja.ultimo_extrato_em is not None
    # segunda rodada: o 3 foi pago; o 1 e o 2 não são relidos e nada duplica
    e3["payment_status"] = "PAID"
    cliente.repasses.append(_repasse(903, "200.00", d15))
    cliente.chamadas.clear()
    r2 = await run_tiktok_sync(db, loja, cliente, agora=AGORA)
    assert r2["inseridos"] == 2 and [x["extrato"] for x in r2["extratos"]] == ["3"]
    assert not any("/statements/1/" in p or "/statements/2/" in p for p, _ in cliente.chamadas)
    assert (await db.execute(select(ConciliacaoMovimento))).scalars().all().__len__() == 7


async def test_extrato_do_dia_tiktok_fecha_a_conta(db, make_user, auth_as, client):
    loja = await _loja_tiktok(db, make_user)
    d14 = date(2026, 10, 14)
    outro = _tx(
        21,
        "586400000000000001",
        gross="200",
        apos="200",
        revenue="200",
        settlement="200",
        seller_disc="0",
        commission="0",
        tx_fee="0",
        affiliate="0",
        fee="0",
    )
    cliente = _TikTokFalsa(
        [_extrato(2, d14, "544.45", 902)],
        {"2": [_tx(20, PEDIDO), outro]},
        [_repasse(902, "544.45", d14)],
    )
    await run_tiktok_sync(db, loja, cliente, agora=AGORA)
    await db.commit()
    auth_as(await make_user(permissions={"conciliacao": {"view": True}}))
    # extrato de 14/10 00:00 UTC = o repasse que cai no banco em 14/10: dia 14 no painel
    r = await client.get("/api/conciliacao/extrato", params={"loja": "Mini", "dia": "2026-10-14"})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert [m["tipo"] for m in corpo["movimentos"]] == ["venda_liberada", "venda_liberada", "saque"]
    assert [m["status"] for m in corpo["movimentos"]] == ["automatico", "pendente", "classificado"]
    t = corpo["totais"]
    assert t["saldo_inicial"] == 0.0 and t["creditos"] == 544.45 and t["debitos"] == 544.45
    assert t["saldo_final"] == 0.0 and t["saldo_informado"] == 0.0 and t["diferenca"] == 0.0
    assert corpo["contagem"]["pendente"] == 1
    r = await client.get("/api/conciliacao/lojas")
    assert [x["platform"] for x in r.json()["lojas"]] == ["tiktok"]


async def test_raiox_tiktok_previsto_x_real(db, make_user, auth_as, client):
    from app.models import MarketplaceOrderFinancial

    loja = await _loja_tiktok(db, make_user)
    tx = _tx(20, PEDIDO)
    cliente = _TikTokFalsa(
        [_extrato(2, date(2026, 10, 14), "344.45", 902)],
        {"2": [tx]},
        [_repasse(902, "344.45", date(2026, 10, 14))],
    )
    await run_tiktok_sync(db, loja, cliente, agora=AGORA)
    db.add(
        MarketplaceOrderFinancial(
            integration_id=loja.integration_id,
            platform="tiktok",
            external_order_id=PEDIDO,
            status="posted",
            fetched_at=datetime(2026, 10, 14, 12, tzinfo=UTC),
            gross_amount=Decimal("1300.65"),
            fee_amount=Decimal("67.85"),
            freight_amount=Decimal("0"),
            net_amount=Decimal("344.45"),
            raw={"settlements": {"code": 0, "data": {"statement_transactions": [tx]}}},
        )
    )
    await db.commit()
    await recalcular_loja(db, loja, agora=AGORA)
    await db.commit()
    auth_as(await make_user(permissions={"conciliacao": {"view": True}}))
    r = await client.get("/api/conciliacao/pedidos", params={"loja": "Mini"})
    assert r.status_code == 200, r.text
    (ped,) = r.json()["itens"]
    assert ped["situacao"] == "bateu" and ped["recebido"] == 344.45
    # a grade: valor = produtos após o desconto da loja; tarifa = todas as taxas
    assert ped["venda_valor"] == 412.3 and ped["venda_tarifa"] == 67.85
    assert ped["venda_frete"] == 0 and ped["venda_liquido"] == 344.45
    assert ped["venda_extras"] == 0
    r = await client.get(f"/api/conciliacao/pedidos/{ped['pedido_bling']}", params={"loja": "Mini"})
    assert r.status_code == 200, r.text
    comp = r.json()["comparativo"]
    assert comp["real_rotulo"] == "extrato diário da TikTok"
    por_chave = {linha["chave"]: linha for linha in comp["linhas"]}
    assert list(por_chave) == [
        "vendas",
        "desconto",
        "produtos",
        "subsidio",
        "receita",
        "comissao",
        "taxa",
        "afiliados",
        "frete",
        "depois",
        "liquido",
    ]
    assert por_chave["desconto"]["previsto"] == 888.35 and por_chave["desconto"]["real"] == 888.35
    assert por_chave["produtos"]["previsto"] == 412.3 and por_chave["produtos"]["dif"] == 0
    assert por_chave["afiliados"]["previsto"] == 37.11 and por_chave["afiliados"]["real"] == 37.11
    assert por_chave["liquido"]["previsto"] == 344.45 and por_chave["liquido"]["real"] == 344.45
    assert por_chave["depois"]["real"] == 0


async def test_reler_financeiro_liquidados_so_relê_estimado_ou_velho(db, make_user, monkeypatch):
    from app.models import MarketplaceOrderFinancial
    from app.services import conciliacao_tiktok as mod

    loja = await _loja_tiktok(db, make_user)
    cliente = _TikTokFalsa(
        [_extrato(2, date(2026, 10, 14), "344.45", 902)], {"2": [_tx(20, PEDIDO)]}, []
    )
    await run_tiktok_sync(db, loja, cliente, agora=AGORA)
    fin = MarketplaceOrderFinancial(
        integration_id=loja.integration_id,
        platform="tiktok",
        external_order_id=PEDIDO,
        status="estimated",
        fetched_at=datetime(2026, 10, 15, 12, tzinfo=UTC),
        net_amount=Decimal("340"),
        raw={"unsettled_estimate": {"est_settlement_amount": "340"}},
    )
    db.add(fin)
    await db.flush()
    chamados: list[tuple[int, str]] = []

    async def _fake(session, *, bling_order_id, trigger):
        chamados.append((bling_order_id, trigger))

    monkeypatch.setattr(
        "app.services.marketplace_financials.run_sync_marketplace_financials_for_bling_order",
        _fake,
    )
    r = await mod.reler_financeiro_liquidados(db, loja)
    assert r["relidos"] == 1 and chamados == [(6000, "conciliacao_liquidacao")]
    # já é a liquidação de verdade, lida depois do extrato: não mexe
    fin.status = "posted"
    fin.fetched_at = datetime(2026, 10, 16, tzinfo=UTC)
    await db.flush()
    r = await mod.reler_financeiro_liquidados(db, loja)
    assert r["relidos"] == 0 and r["candidatos"] == 0


async def test_loja_do_bling_pelo_nome_so_na_mesma_plataforma_e_pedido_fora_some(db, make_user):
    """15/10: a Shopee também tem uma loja "Mini"; os pedidos dela entravam no painel
    da TikTok Mini. Pelo nome só vale a mesma plataforma, e pedido que saiu do escopo
    some no recálculo."""
    from app.models import ConciliacaoPedido

    loja = await _loja_tiktok(db, make_user)
    dono = (await db.execute(select(Integration))).scalar_one().user_id
    db.add(
        StoreInfo(user_id=dono, platform="shopee", account_name="Mini", bling_store_id="204000009")
    )
    db.add(
        BlingOrder(
            bling_id=7000,
            numero="330001",
            numeroloja="261015N1BY9U6Q",
            data=datetime(2026, 10, 15, 12, tzinfo=UTC),
            total=Decimal("100"),
            totalprodutos=Decimal("100"),
            situacao="Atendido",
            loja="204000009",
            item_index=0,
            preco_custo=50.0,
            item_quantidade=1,
            item_codigo="x.sa",
        )
    )
    # um pedido que entrou errado antes do conserto
    db.add(
        ConciliacaoPedido(
            integration_id=loja.integration_id,
            pedido_bling="330001",
            platform=IntegrationPlatform.TIKTOK,
            numeroloja="261015N1BY9U6Q",
            conta="Mini",
            situacao="sem_venda",
            etapa="aguardando",
        )
    )
    await db.commit()
    r = await recalcular_loja(db, loja, agora=AGORA)
    await db.commit()
    assert r["pedidos"] == 1 and r.get("removidos") == 1
    numeros = (
        (
            await db.execute(
                select(ConciliacaoPedido.pedido_bling).where(
                    ConciliacaoPedido.integration_id == loja.integration_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert numeros == ["320001"]


async def test_liquidacao_zerada_sem_pedido_no_bling_nao_trava_o_dia(
    db, make_user, auth_as, client
):
    """08/10 na Mini: pedido desfeito antes de faturar aparece no extrato com 0,00 e o
    Bling não tem o pedido — vira "outro" sozinho (não é dinheiro) e o dia fecha."""
    loja = await _loja_tiktok(db, make_user)
    d14 = date(2026, 10, 14)
    zerada = _tx(
        21,
        "586482278473107237",
        settlement="0",
        revenue="0",
        apos="412.3",
        fee="0",
        commission="0",
        tx_fee="0",
        affiliate="0",
    )
    cliente = _TikTokFalsa(
        [_extrato(2, d14, "344.45", 902)],
        {"2": [_tx(20, PEDIDO), zerada]},
        [_repasse(902, "344.45", d14)],
    )
    await run_tiktok_sync(db, loja, cliente, agora=AGORA)
    await db.commit()
    auth_as(await make_user(permissions={"conciliacao": {"view": True}}))
    r = await client.get("/api/conciliacao/extrato", params={"loja": "Mini", "dia": "2026-10-14"})
    corpo = r.json()
    linha = next(m for m in corpo["movimentos"] if m["pedido_marketplace"] == "586482278473107237")
    assert linha["status"] == "classificado" and linha["classificacao"] == "outro"
    assert "zerada" in linha["descricao"] and linha["credito"] == 0 and linha["debito"] == 0
    assert corpo["contagem"]["pendente"] == 0 and corpo["totais"]["diferenca"] == 0
