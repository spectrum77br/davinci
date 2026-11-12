"""Conciliação — Shopee: a carteira como extrato (passo 3, 15/10/2026)."""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

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
from app.services.conciliacao_shopee import (
    ROTA_CARTEIRA,
    run_carteira_sync,
    transacao_para_movimento,
)

BRT = ZoneInfo("America/Sao_Paulo")


def _epoch(d: date, h: int, mi: int = 0) -> int:
    return int(datetime(d.year, d.month, d.day, h, mi, tzinfo=BRT).timestamp())


def _tx(tid: int, tipo: str, valor: float, saldo: float, quando: int, order_sn: str = "", **kw):
    return {
        "transaction_id": tid,
        "status": "COMPLETED",
        "wallet_type": "shopee_pay",
        "transaction_type": tipo,
        "amount": valor,
        "current_balance": saldo,
        "create_time": quando,
        "order_sn": order_sn,
        "refund_sn": "",
        "reason": kw.get("reason", ""),
        "description": kw.get("description", ""),
        "money_flow": "MONEY_IN" if valor > 0 else "MONEY_OUT",
        "transaction_tab_type": "wallet_order_income" if order_sn else "wallet_adjustment_filter",
    }


def test_transacao_para_movimento_mapeia_tipos():
    d = date(2026, 10, 10)
    renda = transacao_para_movimento(
        _tx(
            1,
            "ESCROW_VERIFIED_ADD",
            4151.0,
            18077.64,
            _epoch(d, 9),
            "261006VX383P9J",
            description="Renda do pedido  #261006VX383P9J",
        )
    )
    assert renda["tipo"] == "venda_liberada" and renda["pedido_marketplace"] == "261006VX383P9J"
    assert renda["credito"] == Decimal("4151.00") and renda["debito"] == Decimal("0")
    assert renda["saldo_apos"] == Decimal("18077.64") and renda["classificacao"] is None
    assert renda["data"] == datetime(2026, 10, 10, 12, tzinfo=UTC)  # 09:00 BRT
    saque = transacao_para_movimento(_tx(2, "WITHDRAWAL_CREATED", -3000.0, 15077.64, _epoch(d, 10)))
    assert saque["tipo"] == "saque" and saque["debito"] == Decimal("3000.00")
    assert saque["classificacao"] == "saque"  # sem pedido e o robô sabe o que é
    pena = transacao_para_movimento(
        _tx(
            3,
            "ADJUSTMENT_CENTER_DEDUCT",
            -250.0,
            14827.64,
            _epoch(d, 11),
            reason="Faixa 2 de Penalização",
        )
    )
    # cobrança da plataforma sem pedido fica SEM DONO (16/10 à noite, Vinicius)
    assert pena["tipo"] == "ajuste_shopee" and pena["classificacao"] is None
    assert pena["descricao"] == "Faixa 2 de Penalização"
    ads = transacao_para_movimento(_tx(4, "SPM_DEDUCT", -100.0, 14727.64, _epoch(d, 12)))
    assert ads["tipo"] == "publicidade" and ads["classificacao"] == "publicidade"
    assert transacao_para_movimento({"transaction_type": "X"}) is None


class _ShopeeFalsa:
    """Só a carteira: devolve o que cair na janela pedida."""

    def __init__(self, transacoes: list[dict]):
        self.transacoes = transacoes
        self.chamadas: list[dict] = []

    async def _request(self, method: str, path: str, *, params=None, json=None):
        assert method == "POST" and path == ROTA_CARTEIRA
        self.chamadas.append(json)
        lista = [
            t
            for t in self.transacoes
            if json["create_time_from"] <= t["create_time"] <= json["create_time_to"]
        ]
        return Response(
            200, json={"error": "", "response": {"transaction_list": lista, "more": False}}
        )


async def _loja_shopee(db, make_user, *, inicio=date(2026, 10, 10)) -> ConciliacaoLoja:
    dono = await make_user()
    integ = Integration(
        user_id=dono.id,
        platform=IntegrationPlatform.SHOPEE,
        name="Jlas",
        status="active",
        credentials=encrypt_json(
            {
                "access_token": "tok",
                "refresh_token": "rt",
                "expires_at": int(time.time()) + 3600,
                "shop_id": 1,
                "partner_id": 2,
                "partner_key": "k",
            }
        ),
    )
    db.add(integ)
    await db.flush()
    db.add(
        StoreInfo(
            user_id=dono.id, platform="shopee", account_name="Jlas", bling_store_id="204000002"
        )
    )
    loja = ConciliacaoLoja(
        integration_id=integ.id, platform=IntegrationPlatform.SHOPEE, conta="Jlas", inicio=inicio
    )
    db.add(loja)
    db.add(
        BlingOrder(
            bling_id=5000,
            numero="310001",
            numeroloja="261006VX383P9J",
            data=datetime(2026, 10, 6, 12, tzinfo=UTC),
            total=Decimal("4925"),
            totalprodutos=Decimal("4925"),
            situacao="Atendido",
            loja="204000002",
            item_index=0,
            preco_custo=3000.0,
            item_quantidade=1,
            item_codigo="i222.sa",
        )
    )
    await db.commit()
    return loja


async def test_run_carteira_sync_le_dia_a_dia_e_resolve_o_pedido(db, make_user):
    loja = await _loja_shopee(db, make_user)
    d10, d11 = date(2026, 10, 10), date(2026, 10, 11)
    cliente = _ShopeeFalsa(
        [
            _tx(10, "ESCROW_VERIFIED_ADD", 4151.0, 4151.0, _epoch(d10, 9), "261006VX383P9J"),
            _tx(11, "WITHDRAWAL_CREATED", -4000.0, 151.0, _epoch(d10, 15)),
            _tx(12, "WITHDRAWAL_COMPLETED", 0.0, 151.0, _epoch(d11, 8)),
            _tx(13, "ESCROW_VERIFIED_ADD", 878.95, 1029.95, _epoch(d11, 10), "2610095P6JURXJ"),
        ]
    )
    r = await run_carteira_sync(db, loja, cliente, hoje=date(2026, 10, 13))
    assert [d["dia"] for d in r["dias"]] == ["2026-10-10", "2026-10-11", "2026-10-12"]
    assert r["inseridos"] == 4 and not r["erros"]
    assert [c["create_time_from"] for c in cliente.chamadas] == [
        _epoch(d10, 0),
        _epoch(d11, 0),
        _epoch(date(2026, 10, 12), 0),
    ]
    movs = (
        (await db.execute(select(ConciliacaoMovimento).order_by(ConciliacaoMovimento.data)))
        .scalars()
        .all()
    )
    assert [m.tipo for m in movs] == [
        "venda_liberada",
        "saque",
        "saque_confirmado",
        "venda_liberada",
    ]
    assert movs[0].pedido_bling == "310001" and movs[0].bling_id == 5000  # casou pelo order_sn
    assert movs[3].pedido_bling is None  # pedido ainda não importado no Bling
    assert movs[1].classificacao == "saque" and movs[2].classificacao == "saque"
    assert movs[0].origem == "shopee_carteira" and movs[0].saldo_apos == Decimal("4151.00")
    assert loja.ultimo_extrato_em is not None
    # segunda rodada: relê só o último dia (sobreposição) + os novos; nada duplica
    cliente.chamadas.clear()
    r2 = await run_carteira_sync(db, loja, cliente, hoje=date(2026, 10, 14))
    assert [d["dia"] for d in r2["dias"]] == ["2026-10-12", "2026-10-13"] and r2["inseridos"] == 0
    syncs = (await db.execute(select(ConciliacaoSync))).scalars().all()
    assert {s.chave for s in syncs} == {"2026-10-10", "2026-10-11", "2026-10-12", "2026-10-13"}
    assert all(s.status == "lido" for s in syncs)


async def test_extrato_do_dia_shopee_fecha_a_conta(db, make_user, auth_as, client):
    loja = await _loja_shopee(db, make_user)
    d10 = date(2026, 10, 10)
    cliente = _ShopeeFalsa(
        [
            _tx(10, "ESCROW_VERIFIED_ADD", 4151.0, 4151.81, _epoch(d10, 9), "261006VX383P9J"),
            _tx(
                11,
                "ADJUSTMENT_CENTER_DEDUCT",
                -250.0,
                3901.81,
                _epoch(d10, 10),
                reason="Penalização",
            ),
            _tx(12, "WITHDRAWAL_CREATED", -3000.0, 901.81, _epoch(d10, 15)),
        ]
    )
    await run_carteira_sync(db, loja, cliente, hoje=date(2026, 10, 11))
    await db.commit()
    auth_as(await make_user(permissions={"conciliacao": {"view": True}}))
    r = await client.get("/api/conciliacao/extrato", params={"loja": "Jlas", "dia": "2026-10-10"})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert [m["tipo"] for m in corpo["movimentos"]] == ["venda_liberada", "ajuste_shopee", "saque"]
    # a penalização (cobrança da plataforma) fica sem dono até alguém olhar (16/10 à noite)
    assert [m["status"] for m in corpo["movimentos"]] == ["automatico", "pendente", "classificado"]
    assert corpo["totais"]["saldo_inicial"] == 0.81 and corpo["totais"]["saldo_informado"] == 901.81
    assert corpo["totais"]["diferenca"] == 0.0 and corpo["contagem"]["pendente"] == 1
    r = await client.get("/api/conciliacao/lojas")
    assert [x["platform"] for x in r.json()["lojas"]] == ["shopee"]


async def test_reler_escrow_liberados_so_relê_leitura_mais_velha_que_a_liberacao(
    db, make_user, monkeypatch
):
    from app.models import MarketplaceOrderFinancial
    from app.services import conciliacao_shopee as mod

    loja = await _loja_shopee(db, make_user)
    d10 = date(2026, 10, 10)
    cliente = _ShopeeFalsa(
        [_tx(10, "ESCROW_VERIFIED_ADD", 4151.0, 4151.0, _epoch(d10, 9), "261006VX383P9J")]
    )
    await run_carteira_sync(db, loja, cliente, hoje=date(2026, 10, 11))
    # a Margem leu o escrow em 07/10 (antes da liberação de 10/10): tem que reler
    db.add(
        MarketplaceOrderFinancial(
            integration_id=loja.integration_id,
            platform="shopee",
            external_order_id="261006VX383P9J",
            status="posted",
            fetched_at=datetime(2026, 10, 7, 12, tzinfo=UTC),
            net_amount=Decimal("4206.01"),
            raw={},
        )
    )
    await db.commit()
    chamados: list[tuple[int, str, bool]] = []

    async def falso(session, *, bling_order_id, trigger="manual", mesmo_excluido=False):
        chamados.append((bling_order_id, trigger, mesmo_excluido))
        return {"ok": True}

    import app.services.marketplace_financials as mf

    monkeypatch.setattr(mf, "run_sync_marketplace_financials_for_bling_order", falso)
    r = await mod.reler_escrow_liberados(db, loja)
    assert r == {"candidatos": 1, "relidos": 1, "erros": []}
    # 27/10 (303469): a Shopee já pagou — relê mesmo com o pedido excluído no Bling
    assert chamados == [(5000, "conciliacao_liberacao", True)]
    # leitura mais nova que a liberação: não relê
    fin = (
        await db.execute(
            select(MarketplaceOrderFinancial).where(
                MarketplaceOrderFinancial.integration_id == loja.integration_id
            )
        )
    ).scalar_one()
    fin.fetched_at = datetime(2026, 10, 12, 12, tzinfo=UTC)
    await db.commit()
    chamados.clear()
    r = await mod.reler_escrow_liberados(db, loja)
    assert r["candidatos"] == 0 and chamados == []


async def test_margem_so_rele_pedido_excluido_quando_a_conciliacao_pede(db):
    """27/10 (303469): excluído no Bling a Margem pula — menos na releitura da liberação."""
    from app.services.marketplace_financials import (
        run_sync_marketplace_financials_for_bling_order as reler,
    )

    db.add(
        BlingOrder(
            bling_id=7000,
            numero="303469",
            data=datetime(2026, 10, 12, 12, tzinfo=UTC),
            situacao="excluido",
            item_index=0,
        )
    )
    await db.commit()
    assert await reler(db, bling_order_id=7000) == {"ok": True, "skipped": "deleted_order"}
    # passa da trava (e para no passo seguinte: este pedido de teste não tem nº da loja)
    assert await reler(db, bling_order_id=7000, mesmo_excluido=True) == {
        "ok": True,
        "skipped": "missing_external_order_id",
    }


def test_comparativo_shopee_preenche_o_real_com_o_escrow_da_liberacao():
    from app.models import ConciliacaoPedido
    from app.routers.conciliacao import _comparativo

    base = {
        "platform": IntegrationPlatform.SHOPEE,
        "pedido_bling": "302371",
        "situacao": "bateu",
        "venda_valor": Decimal("1969.56"),
        "venda_tarifa": Decimal("314.40"),
        "venda_frete": Decimal("9.62"),
        "venda_cupom": Decimal("933.00"),
        "venda_liquido": Decimal("1742.11"),
    }
    versoes = {
        "venda": {
            "valor": 1969.56,
            "tarifa": 314.40,
            "frete": 9.62,
            "cupom": 933.0,
            "liquido": 1742.11,
        },
        "extrato": {"valor": None, "frete": None, "tarifa": None},
    }
    liberado = ConciliacaoPedido(
        **base, recebido=Decimal("1742.11"), liberado_valor=Decimal("1742.11")
    )
    c = _comparativo(liberado, versoes, [])
    por = {x["chave"]: x for x in c["linhas"]}
    assert por["tarifa"]["real"] == 314.4 and por["tarifa"]["dif"] == 0.0
    assert por["liquido"] == {
        "chave": "liquido",
        "rotulo": "= Líquido",
        "previsto": 1742.11,
        "real": 1742.11,
        "dif": 0.0,
        "estilo": "bold",
    }
    assert c["real_rotulo"] == "escrow na liberação + carteira" and c["nota"] is None
    assert "a_parte" not in por and "frete_comprador" not in por
    # antes de liberar: só o previsto
    aguardando = ConciliacaoPedido(**{**base, "situacao": "aguardando"}, recebido=None)
    c2 = _comparativo(aguardando, versoes, [])
    por2 = {x["chave"]: x for x in c2["linhas"]}
    assert por2["tarifa"]["real"] is None and por2["liquido"]["real"] is None
    assert "até lá só existe o previsto" in c2["nota"]


_INCOME_301438 = {
    "order_selling_price": 4925,
    "cost_of_goods_sold": 4925,
    "buyer_total_amount": 4524.91,
    "seller_product_rebate": {
        "amount": 417,
        "commission_fee_offset": 400.23,
        "service_fee_offset": 16.77,
    },
    "commission_fee": 620.55,
    "service_fee": 101.81,
    "net_commission_fee": 220.32,
    "net_service_fee": 85.04,
    "order_ams_commission_fee": 49,
    "shipping_seller_protection_fee_amount": 0.49,
    "actual_shipping_fee": 16.91,
    "buyer_paid_shipping_fee": 16.91,
    "seller_coin_cash_back": 3,
    "seller_discount": 5075,
    "pix_discount": 392,
    "voucher_from_shopee": 25,
    "escrow_amount": 4150.15,
}


def test_conta_shopee_e_a_tela_de_detalhes_de_renda():
    """301438 (15/10): a tela da Shopee mostra 4.508,00 · 0,00 · −3,00 · −354,85 = 4.150,15."""
    from app.models import MarketplaceOrderFinancial
    from app.services.conciliacao_calculo import conta_shopee

    fin = MarketplaceOrderFinancial(
        platform="shopee",
        external_order_id="261005TKS8KUQ3",
        raw={"escrow": {"order_income": _INCOME_301438}},
    )
    c = conta_shopee(fin)
    assert c["preco"] == Decimal("4925") and c["subsidio"] == Decimal("417")
    assert c["subtotal_produtos"] == Decimal("4508")
    assert c["frete_loja"] == Decimal("0.00") and c["cupons"] == Decimal("3")
    assert c["taxas"] == Decimal("305.85") and c["afiliados"] == Decimal("49")
    assert c["renda"] == Decimal("4150.15")
    assert (
        c["subtotal_produtos"] + c["frete_loja"] - c["cupons"] - c["taxas"] - c["afiliados"]
        == c["renda"]
    )
    assert conta_shopee(None) is None
    assert (
        conta_shopee(MarketplaceOrderFinancial(platform="shopee", external_order_id="x", raw={}))
        is None
    )


def test_conta_shopee_preco_diferente_do_custo_dos_produtos():
    """303469 ATV (27/10): a tela mostra Preço do Produto 627,00 − Cupom 25,00 = Subtotal
    602,00 · −3,00 · −74,90 = 524,10. O preço de venda (627) ≠ cost_of_goods_sold (681):
    o subtotal é 681 − rebate 79, e a linha do meio é a diferença (25)."""
    from app.models import MarketplaceOrderFinancial
    from app.services.conciliacao_calculo import conta_shopee

    income = {
        "order_selling_price": 627,
        "cost_of_goods_sold": 681,
        "buyer_total_amount": 601.95,
        "seller_product_rebate": {
            "amount": 79,
            "commission_fee_offset": 60.63,
            "service_fee_offset": 18.37,
        },
        "commission_fee": 85.81,
        "net_commission_fee": 25.18,
        "service_fee": 60.38,
        "net_service_fee": 42.01,
        "order_ams_commission_fee": 7.22,
        "shipping_seller_protection_fee_amount": 0.49,
        "actual_shipping_fee": 12.04,
        "shopee_shipping_rebate": 12.04,
        "seller_coin_cash_back": 3,
        "seller_discount": 1069,
        "voucher_from_shopee": 25,
        "escrow_amount": 524.1,
    }
    c = conta_shopee(
        MarketplaceOrderFinancial(
            platform="shopee",
            external_order_id="261013FRE3CFH3",
            raw={"escrow": {"order_income": income}},
        )
    )
    assert c["preco"] == Decimal("627") and c["subsidio"] == Decimal("25")
    assert c["subtotal_produtos"] == Decimal("602")
    assert c["cupons"] == Decimal("3") and c["taxas"] == Decimal("67.68")
    assert c["afiliados"] == Decimal("7.22")
    assert (
        c["subtotal_produtos"] + c["frete_loja"] - c["cupons"] - c["taxas"] - c["afiliados"]
        == c["renda"]
        == Decimal("524.1")
    )


def test_comparativo_shopee_com_as_linhas_da_tela():
    from app.models import ConciliacaoPedido, MarketplaceOrderFinancial
    from app.routers.conciliacao import _comparativo
    from app.services.conciliacao_calculo import conta_shopee

    conta = conta_shopee(
        MarketplaceOrderFinancial(
            platform="shopee",
            external_order_id="x",
            raw={"escrow": {"order_income": _INCOME_301438}},
        )
    )
    p = ConciliacaoPedido(
        platform=IntegrationPlatform.SHOPEE,
        pedido_bling="301438",
        situacao="bateu",
        venda_liquido=Decimal("4150.15"),
        recebido=Decimal("4150.15"),
        liberado_valor=Decimal("4150.15"),
    )
    c = _comparativo(p, {"venda": {}, "extrato": {"valor": 4150.15}}, [], shopee=conta)
    por = {x["chave"]: x for x in c["linhas"]}
    assert [x["chave"] for x in c["linhas"]] == [
        "preco",
        "subsidio",
        "produtos",
        "frete",
        "cupom",
        "tarifa",
        "afiliados",
        "depois",
        "liquido",
    ]
    assert por["produtos"]["previsto"] == 4508.0 and por["tarifa"]["previsto"] == 305.85
    assert por["afiliados"]["previsto"] == 49.0 and por["afiliados"]["real"] == 49.0
    assert por["liquido"] == {
        "chave": "liquido",
        "rotulo": "= Renda do pedido",
        "previsto": 4150.15,
        "real": 4150.15,
        "dif": 0.0,
        "estilo": "bold",
    }
    assert "Detalhes de Renda" in c["rodape"]


async def test_difal_da_shopee_vai_pro_pedido_do_texto(db, make_user):
    """09/11 (Vinicius: "jogar o difal pra cada pedido"): o DIFAL vem sem o nº no campo
    do pedido, mas o texto diz "… referente ao pedido <order_sn>". A linha vai pro
    pedido: o de pedido da Conciliação entra no Sobrou (fora do recebido e da situação);
    o de antes do início vira "pedido anterior ao início". Sem nº nenhum, sem dono."""
    from app.models import ConciliacaoMovimento, ConciliacaoPedido
    from app.services.conciliacao_calculo import recalcular_loja

    d = date(2026, 10, 20)
    texto = (
        "Débito por diferencial de alíquota do ICMS (DIFAL) cobrada pelo fisco governamental "
        "durante fiscalização referente ao pedido {}"
    )
    difal = transacao_para_movimento(
        _tx(
            91,
            "ADJUSTMENT_CENTER_DEDUCT",
            -9.9,
            100.0,
            _epoch(d, 10),
            description=texto.format("261012ABCDEF12"),
        )
    )
    assert difal["tipo"] == "imposto" and difal["classificacao"] is None
    assert difal["pedido_marketplace"] == "261012ABCDEF12" and difal["debito"] == Decimal("9.90")
    sem_numero = transacao_para_movimento(
        _tx(
            92,
            "ADJUSTMENT_CENTER_DEDUCT",
            -2.57,
            100.0,
            _epoch(d, 11),
            description="Débito por diferencial de alíquota do ICMS (DIFAL) - Pedido 2610...",
        )
    )
    assert sem_numero["tipo"] == "imposto" and sem_numero["classificacao"] is None
    assert sem_numero["pedido_marketplace"] is None
    penalidade = transacao_para_movimento(
        _tx(
            93,
            "ADJUSTMENT_CENTER_DEDUCT",
            -250.0,
            100.0,
            _epoch(d, 12),
            description="Cobrança de taxa referente ao atingimento de meta de pedidos",
        )
    )
    assert penalidade["tipo"] == "ajuste_shopee" and penalidade["classificacao"] is None

    loja = await _loja_shopee(db, make_user)  # início 10/10; o 310001 é de 06/10
    db.add(
        BlingOrder(
            bling_id=5002,
            numero="310002",
            numeroloja="261012ABCDEF12",
            data=datetime(2026, 10, 12, 12, tzinfo=UTC),
            total=Decimal("150"),
            totalprodutos=Decimal("150"),
            situacao="Atendido",
            loja="204000002",
            item_index=0,
            preco_custo=100.0,
            item_quantidade=1,
            item_codigo="i223.sa",
        )
    )

    def gravada(mov: dict, **kw) -> ConciliacaoMovimento:
        return ConciliacaoMovimento(
            integration_id=loja.integration_id, platform=loja.platform, **{**mov, **kw}
        )

    renda = transacao_para_movimento(
        _tx(
            94, "ESCROW_VERIFIED_ADD", 130.0, 230.0, _epoch(date(2026, 10, 18), 9), "261012ABCDEF12"
        )
    )
    # DIFAL do pedido de dentro: chega já ligado pelo nº do texto
    db.add(gravada(renda))
    db.add(gravada(difal))
    # DIFAL do pedido de antes do início, gravado pela regra de 19/10 (imposto classificado,
    # sem o nº)
    antiga = gravada(
        {
            **difal,
            "id_externo": "95",
            "pedido_marketplace": None,
            "descricao": texto.format("261006VX383P9J"),
        },
        tipo="imposto",
        classificacao="imposto",
    )
    db.add(antiga)
    db.add(gravada(sem_numero))
    await db.commit()
    res = await recalcular_loja(db, loja, agora=datetime(2026, 10, 21, 12, tzinfo=UTC))
    await db.commit()
    assert res["difal_ligado"] == 2
    await db.refresh(antiga)
    assert antiga.pedido_bling == "310001" and antiga.classificacao == "anterior_ao_inicio"
    assert antiga.pedido_marketplace == "261006VX383P9J"
    p = (
        await db.execute(
            select(ConciliacaoPedido).where(ConciliacaoPedido.pedido_bling == "310002")
        )
    ).scalar_one()
    assert p.recebido == Decimal("130.00")  # o DIFAL não mexe no recebido
    assert p.imposto == Decimal("9.90")
    assert p.sobrou == Decimal("20.10")  # 130 − custo 100 − DIFAL 9,90
    sem = (
        await db.execute(
            select(ConciliacaoMovimento).where(ConciliacaoMovimento.id_externo == "92")
        )
    ).scalar_one()
    assert sem.classificacao is None and sem.pedido_bling is None  # sem dono
    # rodada seguinte: nada a ligar
    res = await recalcular_loja(db, loja, agora=datetime(2026, 10, 21, 13, tzinfo=UTC))
    assert "difal_ligado" not in res


async def test_credito_por_item_perdido_vai_pro_pedido_do_fim_do_texto(db, make_user):
    """12/11 (Vinicius: "sim, ensina"): o crédito da Shopee por item perdido ou danificado
    vem sem o nº no campo do pedido, mas o texto termina com " - <order_sn>". Os 8 desde
    24/10 foram ligados à mão ao pedido desse nº; agora o robô liga: o de pedido da
    Conciliação entra no recebido dele, o de antes do início vira "anterior ao início".
    O que uma pessoa mexeu fica como está; nº que não é pedido da loja, sem dono."""
    from app.models import ConciliacaoPedido
    from app.services.conciliacao_calculo import recalcular_loja
    from app.services.conciliacao_shopee import pedido_do_ajuste

    d = date(2026, 10, 20)
    texto = (
        "Credito por item perdido ou danificado no envio. Valor do produto descontado de "
        "taxas e comissoes (-R$ 136.46) - {}"
    )
    credito = transacao_para_movimento(
        _tx(
            101,
            "ADJUSTMENT_CENTER_ADD",
            50.0,
            100.0,
            _epoch(d, 10),
            reason=texto.format("261012ABCDEF12"),
        )
    )
    assert credito["tipo"] == "ajuste_shopee" and credito["classificacao"] is None
    assert credito["pedido_marketplace"] == "261012ABCDEF12" and credito["credito"] == Decimal(
        "50.00"
    )
    assert (
        pedido_do_ajuste(
            "Desconto por pedido com pagamento duplicado - retirada de saldo excedente - "
            "261006VSJYEKH3"
        )
        == "261006VSJYEKH3"
    )
    # sem " - <nº>" no fim, nada muda: penalidade e meta seguem sem dono
    assert pedido_do_ajuste("Cobrança de taxa referente ao atingimento de meta de pedidos") is None
    assert pedido_do_ajuste("Taxa de serviço - Pedido 2610...") is None

    loja = await _loja_shopee(db, make_user)  # início 10/10; o 310001 é de 06/10
    db.add(
        BlingOrder(
            bling_id=5002,
            numero="310002",
            numeroloja="261012ABCDEF12",
            data=datetime(2026, 10, 12, 12, tzinfo=UTC),
            total=Decimal("150"),
            totalprodutos=Decimal("150"),
            situacao="Atendido",
            loja="204000002",
            item_index=0,
            preco_custo=100.0,
            item_quantidade=1,
            item_codigo="i223.sa",
        )
    )

    def gravada(mov: dict, **kw) -> ConciliacaoMovimento:
        return ConciliacaoMovimento(
            integration_id=loja.integration_id, platform=loja.platform, **{**mov, **kw}
        )

    renda = transacao_para_movimento(
        _tx(
            102,
            "ESCROW_VERIFIED_ADD",
            130.0,
            230.0,
            _epoch(date(2026, 10, 18), 9),
            "261012ABCDEF12",
        )
    )
    db.add(gravada(renda))
    # crédito do pedido de dentro, gravado antes da regra (sem o nº)
    db.add(gravada(credito, pedido_marketplace=None))
    # crédito do pedido de antes do início, gravado antes da regra
    antiga = gravada(
        {
            **credito,
            "id_externo": "103",
            "pedido_marketplace": None,
            "descricao": texto.format("261006VX383P9J"),
        }
    )
    db.add(antiga)
    # uma pessoa desvinculou: fica como ela deixou
    mexida = gravada(
        {
            **credito,
            "id_externo": "104",
            "pedido_marketplace": None,
            "descricao": texto.format("261006VX383P9J"),
        },
        vinculado_por="cairo sa",
        vinculado_em=datetime(2026, 10, 20, 15, tzinfo=UTC),
    )
    db.add(mexida)
    # nº que não é pedido desta loja: sem dono
    de_fora = gravada(
        {
            **credito,
            "id_externo": "105",
            "pedido_marketplace": None,
            "descricao": texto.format("2610129ZZZZZZZ"),
        }
    )
    db.add(de_fora)
    await db.commit()
    res = await recalcular_loja(db, loja, agora=datetime(2026, 10, 21, 12, tzinfo=UTC))
    await db.commit()
    assert res["ajuste_ligado"] == 2
    await db.refresh(antiga)
    assert antiga.pedido_bling == "310001" and antiga.classificacao == "anterior_ao_inicio"
    assert antiga.pedido_marketplace == "261006VX383P9J" and antiga.vinculado_por is None
    await db.refresh(mexida)
    assert mexida.pedido_bling is None and mexida.classificacao is None
    await db.refresh(de_fora)
    assert de_fora.pedido_bling is None and de_fora.classificacao is None
    p = (
        await db.execute(
            select(ConciliacaoPedido).where(ConciliacaoPedido.pedido_bling == "310002")
        )
    ).scalar_one()
    assert p.recebido == Decimal("180.00")  # renda 130 + crédito 50, como o vínculo à mão
    # rodada seguinte: nada a ligar
    res = await recalcular_loja(db, loja, agora=datetime(2026, 10, 21, 13, tzinfo=UTC))
    assert "ajuste_ligado" not in res


async def test_saldo_que_pula_nao_ganha_ajuste_e_os_do_robo_saem(db, make_user):
    """16/10 à noite (Vinicius, essência do painel): a carteira muda de saldo sem listar o
    lançamento (renda 292,39, saldo sobe 291,41) → NÃO se inventa ajuste classificado pra
    fechar o dia: a diferença de −0,98 aparece e eles decidem (23/10: como linha sem dono,
    ver o teste seguinte). Ajuste que o robô tinha criado sai e o dia reabre; ajuste que
    uma pessoa mexeu fica."""
    from app.models import ConciliacaoDia
    from app.services.conciliacao_jobs import fechar_dias_prontos

    loja = await _loja_shopee(db, make_user)
    d10, d11 = date(2026, 10, 10), date(2026, 10, 11)
    cliente = _ShopeeFalsa(
        [
            _tx(10, "ESCROW_VERIFIED_ADD", 100.0, 100.0, _epoch(d10, 9), "261006VX383P9J"),
            _tx(11, "ESCROW_VERIFIED_ADD", 292.39, 391.41, _epoch(d11, 10), "2610095P6JURXJ"),
        ]
    )
    r = await run_carteira_sync(db, loja, cliente, hoje=date(2026, 10, 12))
    assert {x["dia"]: x["ajustes_de_saldo"] for x in r["dias"]} == {
        "2026-10-10": [],
        "2026-10-11": [],
    }
    movs = (await db.execute(select(ConciliacaoMovimento))).scalars().all()
    # 23/10: o pulo ganha a linha que faltou, SEM DONO (nenhum pedido devolvido de −0,98)
    assert [
        (m.id_externo, m.debito, m.classificacao, m.pedido_bling)
        for m in movs
        if m.tipo == "ajuste_shopee"
    ] == [("nao-listado-11", Decimal("0.98"), None, None)]
    # o dia 11 não fecha sozinho: a linha de −0,98 sem dono fica à vista
    fechados = await fechar_dias_prontos(db, loja)
    assert "2026-10-11" in fechados["abertos"]
    # linhas que o robô tinha criado antes da regra (uma sem dono, outra mexida por alguém)
    from app.services.conciliacao_shopee import inserir_movimentos

    def ajuste(n: int, por: str | None) -> dict:
        return {
            "origem": "shopee_carteira",
            "id_externo": f"ajuste-saldo-2026-10-11-{n}",
            "data": datetime.fromtimestamp(_epoch(d11, 10), tz=UTC),
            "tipo": "ajuste_shopee",
            "descricao": "Diferença de saldo não detalhada pela Shopee (teste)",
            "sub_tipo": "AJUSTE_SALDO",
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
            "credito": Decimal("0"),
            "debito": Decimal("0.98"),
            "saldo_apos": None,
            "descontado_da_venda": None,
            "sku": None,
            "item_id": None,
            "data_aprovacao": None,
            "classificacao": "ajuste",
            "vinculado_por": por,
            "vinculado_em": datetime(2026, 10, 12, 9, tzinfo=UTC) if por else None,
            "raw": {},
        }

    assert await inserir_movimentos(db, loja, [ajuste(1, None), ajuste(2, "cairo")]) == 2
    db.add(
        ConciliacaoDia(
            integration_id=loja.integration_id,
            dia=d11,
            fechado_em=datetime(2026, 10, 12, 9, tzinfo=UTC),
            fechado_por="cairo",
        )
    )
    await db.commit()
    r2 = await run_carteira_sync(db, loja, cliente, hoje=date(2026, 10, 12))
    assert r2["ajustes_removidos"] == ["ajuste-saldo-2026-10-11-1"]
    movs = (await db.execute(select(ConciliacaoMovimento))).scalars().all()
    ajustes = [m for m in movs if m.id_externo.startswith("ajuste-saldo-")]
    assert [m.id_externo for m in ajustes] == ["ajuste-saldo-2026-10-11-2"]  # o mexido fica
    dia = (await db.execute(select(ConciliacaoDia).where(ConciliacaoDia.dia == d11))).scalar_one()
    assert dia.fechado_em is None and "ajuste automático" in (dia.reaberto_motivo or "")


async def test_pulo_da_carteira_ganha_a_linha_que_faltou_ligada_ao_pedido_devolvido(
    db, make_user, auth_as, client
):
    """23/10 (ATV e mega "saldo não bate −0,49"): a Shopee desconta a renda negativa do
    pedido devolvido (−0,49) sem listar a linha. No pulo o robô escreve a linha que faltou
    e liga ao pedido devolvido provável (o mais antigo esperando a liberação, venda −0,49,
    nada na carteira); pulo sem pedido provável fica sem dono; pulo pra cima e dia fechado
    não são mexidos; se a linha de verdade aparecer, a do robô sai."""
    from app.models import ConciliacaoDia, ConciliacaoPedido
    from app.services.conciliacao_shopee import (
        PREFIXO_NAO_LISTADO,
        inserir_movimentos,
        lancar_descontos_nao_listados,
    )

    loja = await _loja_shopee(db, make_user)
    d10, d11, d12, d13 = (date(2026, 10, d) for d in (10, 11, 12, 13))

    def pedido(numero: str, sn: str, situacao: str, dia: int) -> ConciliacaoPedido:
        return ConciliacaoPedido(
            integration_id=loja.integration_id,
            platform=IntegrationPlatform.SHOPEE,
            pedido_bling=numero,
            bling_id=int(numero),
            numeroloja=sn,
            situacao=situacao,
            venda_liquido=Decimal("-0.49"),
            data_pedido=datetime(2026, 10, dia, 12, tzinfo=UTC),
        )

    db.add_all(
        [
            pedido("310007", "261007DEVOLV7", "aguardando", 7),
            pedido("310005", "261005DEVOLV5", "aguardando", 5),  # o mais antigo: 1º pulo
            pedido("310004", "261004BATEU04", "bateu", 4),  # já liberou: não serve
            pedido("310006", "261006COMLINH", "aguardando", 6),  # já tem linha na carteira
        ]
    )
    await db.commit()
    txs = [
        _tx(10, "ESCROW_VERIFIED_ADD", 100.0, 100.0, _epoch(d10, 9), "261006VX383P9J"),
        # 11/10: a renda de 200,00 entra e o saldo sobe 199,51 (−0,49 não listado)
        _tx(11, "ESCROW_VERIFIED_ADD", 200.0, 299.51, _epoch(d11, 10), "2610AAAAAAAAA"),
        _tx(12, "ESCROW_VERIFIED_MINUS", -0.49, 299.02, _epoch(d11, 11), "261006COMLINH"),
        # 12/10: −0,49 (2º pedido), −5,00 (sem pedido) e +0,47 (pra cima: fica à vista)
        _tx(13, "ESCROW_VERIFIED_ADD", 50.0, 348.53, _epoch(d12, 9), "2610BBBBBBBBB"),
        _tx(14, "ESCROW_VERIFIED_ADD", 50.0, 393.53, _epoch(d12, 10), "2610CCCCCCCCC"),
        _tx(15, "ESCROW_VERIFIED_ADD", 10.0, 404.0, _epoch(d12, 11), "2610DDDDDDDDD"),
    ]
    r = await run_carteira_sync(db, loja, _ShopeeFalsa(txs), hoje=d13)
    await db.commit()
    assert [(x["dia"], x["valor"], x["pedido"]) for x in r["nao_listados"]] == [
        ("2026-10-11", "-0.49", "310005"),
        ("2026-10-12", "-0.49", "310007"),
        ("2026-10-12", "-5.00", None),
    ]

    async def do_robo() -> dict[str, ConciliacaoMovimento]:
        return {
            m.id_externo: m
            for m in (
                await db.execute(
                    select(ConciliacaoMovimento).where(
                        ConciliacaoMovimento.id_externo.like(f"{PREFIXO_NAO_LISTADO}%")
                    )
                )
            ).scalars()
        }

    robo = await do_robo()
    assert set(robo) == {"nao-listado-11", "nao-listado-13", "nao-listado-14"}
    l11 = robo["nao-listado-11"]
    assert (l11.tipo, l11.sub_tipo, l11.pedido_bling, l11.pedido_marketplace, l11.bling_id) == (
        "venda_liberada",
        "ESCROW_NAO_LISTADO",
        "310005",
        "261005DEVOLV5",
        310005,
    )
    assert l11.debito == Decimal("0.49") and l11.saldo_apos == Decimal("99.51")
    assert l11.data == datetime.fromtimestamp(_epoch(d11, 10), tz=UTC) - timedelta(seconds=1)
    assert "não mostrou no extrato" in l11.descricao
    assert robo["nao-listado-13"].pedido_bling == "310007"
    sem_dono = robo["nao-listado-14"]
    assert (sem_dono.tipo, sem_dono.debito, sem_dono.pedido_bling, sem_dono.classificacao) == (
        "ajuste_shopee",
        Decimal("5.00"),
        None,
        None,
    )

    auth_as(await make_user(permissions={"conciliacao": {"view": True}}))
    r11 = (
        await client.get("/api/conciliacao/extrato", params={"loja": "Jlas", "dia": "2026-10-11"})
    ).json()
    assert r11["totais"]["diferenca"] == 0.0
    assert [m["pedido_bling"] for m in r11["movimentos"]][0] == "310005"
    assert r11["movimentos"][0]["status"] == "automatico"
    r12 = (
        await client.get("/api/conciliacao/extrato", params={"loja": "Jlas", "dia": "2026-10-12"})
    ).json()
    assert r12["totais"]["diferenca"] == 0.47  # o pulo pra cima continua à vista
    assert r12["contagem"]["pendente"] >= 1  # a linha de −5,00 sem dono

    # a releitura não duplica nem troca o pedido
    ids = {k: m.id for k, m in robo.items()}
    r2 = await run_carteira_sync(db, loja, _ShopeeFalsa(txs), hoje=d13)
    await db.commit()
    assert "nao_listados" not in r2
    assert {k: m.id for k, m in (await do_robo()).items()} == ids

    # a Shopee passa a listar o −5,00: o pulo some e a linha do robô sai; dia fechado
    # por pessoa (13/10, pulo de −0,49) não é mexido
    real = transacao_para_movimento(
        _tx(17, "ADJUSTMENT_CENTER_DEDUCT", -5.0, 343.53, _epoch(d12, 9, 30))
    )
    d13_linha = transacao_para_movimento(
        _tx(16, "ESCROW_VERIFIED_ADD", 20.0, 423.51, _epoch(d13, 9), "2610EEEEEEEEE")
    )
    assert await inserir_movimentos(db, loja, [real, d13_linha], reabrir=False) == 2
    db.add(
        ConciliacaoDia(
            integration_id=loja.integration_id,
            dia=d13,
            fechado_em=datetime(2026, 10, 14, 9, tzinfo=UTC),
            fechado_por="cairo",
        )
    )
    await db.flush()
    assert await lancar_descontos_nao_listados(db, loja, ate=d13) == []
    await db.commit()
    assert set(await do_robo()) == {"nao-listado-11", "nao-listado-13"}
    r13 = (
        await client.get("/api/conciliacao/extrato", params={"loja": "Jlas", "dia": "2026-10-13"})
    ).json()
    assert r13["totais"]["diferenca"] == -0.49


async def test_pulo_de_dois_devolvidos_e_o_pulo_mais_novo_fica_com_o_pedido(
    db, make_user, auth_as, client
):
    """Barbosa 15/10: um pulo de −0,98 = dois devolvidos de −0,49 → duas linhas, uma por
    pedido. E o pulo antigo (11/10) não leva os pedidos: liga do pulo mais novo pro mais
    velho, e o antigo fica sem dono (é de pedido de antes do painel)."""
    from app.models import ConciliacaoPedido
    from app.services.conciliacao_shopee import PREFIXO_NAO_LISTADO

    loja = await _loja_shopee(db, make_user)
    d10, d11, d12 = (date(2026, 10, d) for d in (10, 11, 12))
    db.add_all(
        [
            ConciliacaoPedido(
                integration_id=loja.integration_id,
                platform=IntegrationPlatform.SHOPEE,
                pedido_bling=numero,
                bling_id=int(numero),
                numeroloja=f"2610DEVOLV{numero[-2:]}",
                situacao="aguardando",
                venda_liquido=Decimal("-0.49"),
                data_pedido=datetime(2026, 10, 6, 12, tzinfo=UTC),
            )
            for numero in ("310011", "310012")
        ]
    )
    await db.commit()
    txs = [
        _tx(10, "ESCROW_VERIFIED_ADD", 100.0, 100.0, _epoch(d10, 9), "261006VX383P9J"),
        _tx(11, "ESCROW_VERIFIED_ADD", 50.0, 149.51, _epoch(d11, 10), "2610AAAAAAAAA"),
        _tx(12, "ESCROW_VERIFIED_ADD", 292.39, 440.92, _epoch(d12, 10), "2610BBBBBBBBB"),
    ]
    r = await run_carteira_sync(db, loja, _ShopeeFalsa(txs), hoje=date(2026, 10, 13))
    await db.commit()
    assert sorted((x["dia"], x["valor"], x["pedido"] or "") for x in r["nao_listados"]) == [
        ("2026-10-11", "-0.49", ""),
        ("2026-10-12", "-0.49", "310011"),
        ("2026-10-12", "-0.49", "310012"),
    ]
    robo = {
        m.id_externo: m
        for m in (
            await db.execute(
                select(ConciliacaoMovimento).where(
                    ConciliacaoMovimento.id_externo.like(f"{PREFIXO_NAO_LISTADO}%")
                )
            )
        ).scalars()
    }
    assert set(robo) == {"nao-listado-11", "nao-listado-12", "nao-listado-12-2"}
    assert robo["nao-listado-11"].tipo == "ajuste_shopee"
    assert [robo["nao-listado-12"].saldo_apos, robo["nao-listado-12-2"].saldo_apos] == [
        Decimal("149.02"),
        Decimal("148.53"),
    ]
    auth_as(await make_user(permissions={"conciliacao": {"view": True}}))
    for dia in ("2026-10-11", "2026-10-12"):
        corpo = (
            await client.get("/api/conciliacao/extrato", params={"loja": "Jlas", "dia": dia})
        ).json()
        assert corpo["totais"]["diferenca"] == 0.0, dia
    # a releitura mantém as três
    r2 = await run_carteira_sync(db, loja, _ShopeeFalsa(txs), hoje=date(2026, 10, 13))
    assert "nao_listados" not in r2


def test_transacao_liberacao_negativa_e_compensacao():
    """22/10 (kfa 300588): a renda negativa é a liberação; compensação tem nome próprio."""
    d = date(2026, 10, 14)
    neg = transacao_para_movimento(
        _tx(20, "ESCROW_VERIFIED_MINUS", -0.49, 100.0, _epoch(d, 13), "261002H6M2V4DX")
    )
    assert neg["tipo"] == "venda_liberada" and neg["debito"] == Decimal("0.49")
    comp = transacao_para_movimento(
        _tx(21, "RETURN_COMPENSATION_SERVICE_ADD", 33.04, 133.04, _epoch(d, 14), "261002H6M2V4DX")
    )
    assert comp["tipo"] == "compensacao_shopee" and comp["credito"] == Decimal("33.04")
    vendedor = transacao_para_movimento(
        _tx(22, "SELLER_COMPENSATE_ADD", 128.23, 261.27, _epoch(d, 15), "2610070HT8Q2MW")
    )
    assert vendedor["tipo"] == "compensacao_shopee"


async def test_recalculo_renomeia_a_liberacao_negativa_e_o_raio_x_mostra_o_depois(
    db, make_user, auth_as, client
):
    """kfa 300588 (22/10): renda −0,49 gravada como "outro", compensação +33,04 e o
    "pagamento duplicado" −32,55 vinculado à mão. O pedido liberou (−0,49 = previsto);
    o Raio-X mostra a renda batendo, +0,49 depois da liberação e 0,00 com a loja."""
    from app.models import ConciliacaoPedido, MarketplaceOrderFinancial
    from app.services.conciliacao_calculo import recalcular_loja

    loja = await _loja_shopee(db, make_user, inicio=date(2026, 10, 1))
    db.add(
        MarketplaceOrderFinancial(
            platform=IntegrationPlatform.SHOPEE,
            integration_id=loja.integration_id,
            external_order_id="261006VX383P9J",
            status="posted",
            gross_amount=Decimal("0"),
            fee_amount=Decimal("0.49"),
            freight_amount=Decimal("0"),
            net_amount=Decimal("-0.49"),
            raw={},
        )
    )

    def _linha(n: int, tipo: str, sub: str, dia: int, credito="0", debito="0", **kw):
        return ConciliacaoMovimento(
            integration_id=loja.integration_id,
            platform=IntegrationPlatform.SHOPEE,
            origem="shopee_carteira",
            id_externo=f"s{n}",
            data=datetime(2026, 10, dia, 15, tzinfo=UTC),
            tipo=tipo,
            descricao=sub,
            sub_tipo=sub,
            pedido_marketplace=kw.get("pedido_mkt", "261006VX383P9J"),
            pedido_bling=kw.get("pedido_bling"),
            vinculado_por=kw.get("vinculado_por"),
            credito=Decimal(credito),
            debito=Decimal(debito),
            raw={},
        )

    db.add_all(
        [
            _linha(1, "outro", "ESCROW_VERIFIED_MINUS", 14, debito="0.49"),
            _linha(2, "outro", "RETURN_COMPENSATION_SERVICE_ADD", 16, credito="33.04"),
            _linha(
                3,
                "ajuste_shopee",
                "ADJUSTMENT_CENTER_DEDUCT",
                21,
                debito="32.55",
                pedido_mkt=None,
                pedido_bling="310001",
                vinculado_por="vinicius",
            ),
        ]
    )
    await db.commit()
    r = await recalcular_loja(db, loja, agora=datetime(2026, 10, 22, 12, tzinfo=UTC))
    await db.commit()
    assert r.get("liberacao_renomeada") == 2
    tipos = {m.sub_tipo: m.tipo for m in (await db.execute(select(ConciliacaoMovimento))).scalars()}
    assert tipos["ESCROW_VERIFIED_MINUS"] == "venda_liberada"
    assert tipos["RETURN_COMPENSATION_SERVICE_ADD"] == "compensacao_shopee"
    p = (
        await db.execute(
            select(ConciliacaoPedido).where(ConciliacaoPedido.pedido_bling == "310001")
        )
    ).scalar_one()
    assert p.situacao == "estornado_depois"
    assert p.motivo == "fora da liberação entrou 33.04 e saiu 32.55"
    assert p.liberado_valor == Decimal("-0.49") and p.recebido == Decimal("0.00")
    auth_as(await make_user(permissions={"conciliacao": {"view": True}}))
    resp = await client.get("/api/conciliacao/pedidos/310001", params={"loja": "Jlas"})
    assert resp.status_code == 200, resp.text
    por = {x["chave"]: x for x in resp.json()["comparativo"]["linhas"]}
    assert por["liquido"]["previsto"] == -0.49 and por["liquido"]["real"] == -0.49
    assert por["liquido"]["dif"] == 0.0
    assert por["depois"]["real"] == 0.49 and por["ficou"]["real"] == 0.0


def _income_306115(*, liberado: bool) -> dict:
    """vortan 306115 (03/11): na venda a Shopee prometia 732,10; na liberação cobrou
    afiliados 10,32 e +1,44 de taxas e pagou 720,34."""
    return {
        "order_selling_price": 885,
        "cost_of_goods_sold": 885,
        "seller_product_rebate": {"amount": 25},
        "net_commission_fee": 100,
        "net_service_fee": 26.34 if liberado else 24.90,
        "order_ams_commission_fee": 10.32 if liberado else 0,
        "actual_shipping_fee": 0,
        "buyer_paid_shipping_fee": 0,
        "voucher_from_seller": 3,
        "escrow_amount": 720.34 if liberado else 732.10,
    }


async def test_previsto_da_shopee_e_a_conta_da_venda_e_recebido_a_menos(
    db, make_user, auth_as, client
):
    """03/11 (Vinicius, vortan 306115: "o previsto e o real não era pra ser exatamente
    isso?"): a conta da venda fica guardada até liberar; liberou menos que ela (afiliados
    e taxas cobrados só na liberação) → "recebido a menos", com o que mudou no motivo, na
    grade e no Raio-X. Pedido que o painel já viu liberado, sem a conta de antes, fica
    como estava."""
    from app.models import ConciliacaoPedido, MarketplaceOrderFinancial
    from app.services.conciliacao_calculo import recalcular_loja

    loja = await _loja_shopee(db, make_user, inicio=date(2026, 10, 1))
    fin = MarketplaceOrderFinancial(
        platform=IntegrationPlatform.SHOPEE,
        integration_id=loja.integration_id,
        external_order_id="261006VX383P9J",
        status="posted",
        gross_amount=Decimal("885"),
        fee_amount=Decimal("124.90"),
        freight_amount=Decimal("0"),
        net_amount=Decimal("732.10"),
        raw={"escrow": {"order_income": _income_306115(liberado=False)}},
    )
    # o outro pedido: o painel só o viu já liberado (antes de 03/11) — sem conta da venda
    db.add_all(
        [
            fin,
            BlingOrder(
                bling_id=5001,
                numero="310002",
                numeroloja="261006JAPAGO",
                data=datetime(2026, 10, 6, 12, tzinfo=UTC),
                total=Decimal("885"),
                totalprodutos=Decimal("885"),
                situacao="Atendido",
                loja="204000002",
                item_index=0,
                preco_custo=500.0,
                item_quantidade=1,
                item_codigo="i222.sa",
            ),
            MarketplaceOrderFinancial(
                platform=IntegrationPlatform.SHOPEE,
                integration_id=loja.integration_id,
                external_order_id="261006JAPAGO",
                status="posted",
                gross_amount=Decimal("885"),
                fee_amount=Decimal("126.34"),
                freight_amount=Decimal("0"),
                net_amount=Decimal("720.34"),
                raw={"escrow": {"order_income": _income_306115(liberado=True)}},
            ),
        ]
    )

    def _liberacao(n: int, pedido_mkt: str, dia: int) -> ConciliacaoMovimento:
        return ConciliacaoMovimento(
            integration_id=loja.integration_id,
            platform=IntegrationPlatform.SHOPEE,
            origem="shopee_carteira",
            id_externo=f"lib{n}",
            data=datetime(2026, 10, dia, 15, tzinfo=UTC),
            tipo="venda_liberada",
            descricao="ESCROW_VERIFIED_ADD",
            sub_tipo="ESCROW_VERIFIED_ADD",
            pedido_marketplace=pedido_mkt,
            credito=Decimal("720.34"),
            debito=Decimal("0"),
            raw={},
        )

    db.add(_liberacao(2, "261006JAPAGO", 9))
    await db.commit()

    async def _pedido(numero: str) -> ConciliacaoPedido:
        return (
            await db.execute(
                select(ConciliacaoPedido)
                .where(ConciliacaoPedido.pedido_bling == numero)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()

    await recalcular_loja(db, loja, agora=datetime(2026, 10, 10, 12, tzinfo=UTC))
    await db.commit()
    p = await _pedido("310001")
    assert p.situacao == "aguardando" and p.venda_liquido == Decimal("732.10")
    assert Decimal(p.conta_na_venda["renda"]) == Decimal("732.10")
    assert Decimal(p.conta_na_venda["afiliados"]) == 0
    ja_pago = await _pedido("310002")
    assert ja_pago.situacao == "bateu" and ja_pago.conta_na_venda is None
    # a Shopee liberou 720,34 e a Margem releu a conta fechada na liberação
    db.add(_liberacao(1, "261006VX383P9J", 14))
    fin.raw = {"escrow": {"order_income": _income_306115(liberado=True)}}
    fin.net_amount = Decimal("720.34")
    fin.fee_amount = Decimal("126.34")
    await db.commit()
    for dia in (15, 16):  # a rodada seguinte não sobrescreve a conta da venda
        await recalcular_loja(db, loja, agora=datetime(2026, 10, dia, 12, tzinfo=UTC))
        await db.commit()
        p = await _pedido("310001")
        assert p.situacao == "recebido_a_menos" and p.recebido == Decimal("720.34")
        assert p.venda_liquido == Decimal("732.10")
        assert p.dif_previsto_recebido == Decimal("-11.76")
        assert (
            p.motivo == "na liberação a Shopee mudou taxas 124.90 → 126.34, afiliados 0.00 → 10.32"
        )
        assert p.venda_tarifa == Decimal("124.90") and p.venda_afiliados == Decimal("0")
        assert Decimal(p.conta_na_venda["renda"]) == Decimal("732.10")
    ja_pago = await _pedido("310002")
    assert ja_pago.situacao == "bateu" and ja_pago.conta_na_venda is None
    # Raio-X: previsto = a conta da venda, real = a da liberação; a linha rosa sai
    auth_as(await make_user(permissions={"conciliacao": {"view": True}}))
    resp = await client.get("/api/conciliacao/pedidos/310001", params={"loja": "Jlas"})
    assert resp.status_code == 200, resp.text
    c = resp.json()["comparativo"]
    por = {x["chave"]: x for x in c["linhas"]}
    assert por["afiliados"]["previsto"] == 0.0 and por["afiliados"]["real"] == 10.32
    assert por["tarifa"]["previsto"] == 124.9 and por["tarifa"]["real"] == 126.34
    assert por["preco"]["dif"] == 0.0
    assert por["liquido"]["previsto"] == 732.1 and por["liquido"]["real"] == 720.34
    assert por["liquido"]["dif"] == -11.76
    assert "previsto_na_venda" not in por
    assert "previsto = a conta como estava na venda" in c["rodape"]
    resp = await client.get("/api/conciliacao/pedidos/310002", params={"loja": "Jlas"})
    por = {x["chave"]: x for x in resp.json()["comparativo"]["linhas"]}
    assert por["afiliados"]["previsto"] == 10.32 and por["liquido"]["dif"] == 0.0


async def test_reembolso_antes_de_liberar_continua_com_a_conta_da_liberacao(
    db, make_user, auth_as, client
):
    """03/11: a conta da venda só vira o previsto quando a diferença é cobrança da Shopee
    (taxas, afiliados, cupons). Reembolso antes de liberar (a renda cai 300 sem mudar
    taxa nenhuma) continua comparando com a conta da liberação — "bateu", como antes."""
    from app.models import ConciliacaoPedido, MarketplaceOrderFinancial
    from app.services.conciliacao_calculo import recalcular_loja

    loja = await _loja_shopee(db, make_user, inicio=date(2026, 10, 1))
    fin = MarketplaceOrderFinancial(
        platform=IntegrationPlatform.SHOPEE,
        integration_id=loja.integration_id,
        external_order_id="261006VX383P9J",
        status="posted",
        gross_amount=Decimal("885"),
        fee_amount=Decimal("124.90"),
        freight_amount=Decimal("0"),
        net_amount=Decimal("732.10"),
        raw={"escrow": {"order_income": _income_306115(liberado=False)}},
    )
    db.add(fin)
    await db.commit()
    await recalcular_loja(db, loja, agora=datetime(2026, 10, 10, 12, tzinfo=UTC))
    await db.commit()
    db.add(
        ConciliacaoMovimento(
            integration_id=loja.integration_id,
            platform=IntegrationPlatform.SHOPEE,
            origem="shopee_carteira",
            id_externo="lib1",
            data=datetime(2026, 10, 14, 15, tzinfo=UTC),
            tipo="venda_liberada",
            descricao="ESCROW_VERIFIED_ADD",
            sub_tipo="ESCROW_VERIFIED_ADD",
            pedido_marketplace="261006VX383P9J",
            credito=Decimal("432.10"),
            debito=Decimal("0"),
            raw={},
        )
    )
    fin.raw = {
        "escrow": {"order_income": {**_income_306115(liberado=False), "escrow_amount": 432.10}}
    }
    fin.net_amount = Decimal("432.10")
    await db.commit()
    await recalcular_loja(db, loja, agora=datetime(2026, 10, 15, 12, tzinfo=UTC))
    await db.commit()
    p = (
        await db.execute(
            select(ConciliacaoPedido)
            .where(ConciliacaoPedido.pedido_bling == "310001")
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert p.situacao == "bateu" and p.venda_liquido == Decimal("432.10")
    assert Decimal(p.conta_na_venda["renda"]) == Decimal("732.10")  # guardada, não usada
    assert "a plataforma mudou a conta em -300.00" in p.motivo
    auth_as(await make_user(permissions={"conciliacao": {"view": True}}))
    resp = await client.get("/api/conciliacao/pedidos/310001", params={"loja": "Jlas"})
    por = {x["chave"]: x for x in resp.json()["comparativo"]["linhas"]}
    assert por["liquido"]["previsto"] == 432.1 and por["liquido"]["dif"] == 0.0
    assert por["previsto_na_venda"]["previsto"] == 732.1


def test_baixa_de_afiliados_so_quando_a_conta_fecha_ao_centavo():
    """05/11 (Vinicius): a baixa sozinha vale só quando a diferença inteira é o afiliado
    mais os 14% em cima dele. Qualquer outra coisa fica pra alguém olhar."""
    from app.services.conciliacao_calculo import baixa_de_afiliados

    def conta(**mudou: str) -> dict[str, Decimal]:
        base = {
            "preco": "885",
            "subsidio": "25",
            "frete_loja": "0",
            "cupons": "3",
            "taxas": "124.90",
            "afiliados": "0",
        }
        return {k: Decimal(v) for k, v in {**base, **mudou}.items()}

    venda = conta()
    fechou = conta(taxas="126.34", afiliados="10.32")
    assert (
        baixa_de_afiliados(venda, fechou, Decimal("-11.76"))
        == "só afiliados: 10.32 + 1.44 de taxa em cima deles = toda a diferença"
    )
    assert baixa_de_afiliados(venda, fechou, None) is None
    # um centavo sobrando na diferença
    assert baixa_de_afiliados(venda, fechou, Decimal("-11.77")) is None
    # taxa um centavo acima dos 14%
    taxa_maior = conta(taxas="126.35", afiliados="10.32")
    assert baixa_de_afiliados(venda, taxa_maior, Decimal("-11.77")) is None
    # taxa subiu sem afiliado
    assert baixa_de_afiliados(venda, conta(taxas="126.34"), Decimal("-1.44")) is None
    # cupom ou frete junto com o afiliado
    com_cupom = conta(taxas="126.34", afiliados="10.32", cupons="3.50")
    assert baixa_de_afiliados(venda, com_cupom, Decimal("-12.26")) is None
    com_frete = conta(taxas="126.34", afiliados="10.32", frete_loja="1")
    assert baixa_de_afiliados(venda, com_frete, Decimal("-12.76")) is None


async def _liberou_306115(db, make_user, *, servico: float = 26.34, renda: float = 720.34):
    """O 306115 até a liberação: conta da venda guardada, a Shopee pagou `renda` e a
    Margem releu a conta. `servico` é a taxa de serviço fechada na liberação."""
    from app.models import MarketplaceOrderFinancial
    from app.services.conciliacao_calculo import recalcular_loja

    loja = await _loja_shopee(db, make_user, inicio=date(2026, 10, 1))
    fin = MarketplaceOrderFinancial(
        platform=IntegrationPlatform.SHOPEE,
        integration_id=loja.integration_id,
        external_order_id="261006VX383P9J",
        status="posted",
        gross_amount=Decimal("885"),
        fee_amount=Decimal("124.90"),
        freight_amount=Decimal("0"),
        net_amount=Decimal("732.10"),
        raw={"escrow": {"order_income": _income_306115(liberado=False)}},
    )
    db.add(fin)
    await db.commit()
    await recalcular_loja(db, loja, agora=datetime(2026, 10, 10, 12, tzinfo=UTC))
    await db.commit()
    db.add(
        ConciliacaoMovimento(
            integration_id=loja.integration_id,
            platform=IntegrationPlatform.SHOPEE,
            origem="shopee_carteira",
            id_externo="lib1",
            data=datetime(2026, 10, 14, 15, tzinfo=UTC),
            tipo="venda_liberada",
            descricao="ESCROW_VERIFIED_ADD",
            sub_tipo="ESCROW_VERIFIED_ADD",
            pedido_marketplace="261006VX383P9J",
            credito=Decimal(str(renda)),
            debito=Decimal("0"),
            raw={},
        )
    )
    income = {
        **_income_306115(liberado=True),
        "net_service_fee": servico,
        "escrow_amount": renda,
    }
    fin.raw = {"escrow": {"order_income": income}}
    fin.net_amount = Decimal(str(renda))
    await db.commit()
    return loja


async def _pedido_310001(db):
    from app.models import ConciliacaoPedido

    return (
        await db.execute(
            select(ConciliacaoPedido)
            .where(ConciliacaoPedido.pedido_bling == "310001")
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def test_recebido_a_menos_so_de_afiliado_finaliza_sozinho(db, make_user, auth_as, client):
    """05/11 (Vinicius: "não gostaria que ficasse esses amarelo para sempre"): o pedido
    continua "recebido a menos", com a diferença e o motivo, mas o robô finaliza e ele
    sai do "Onde agir". Movimento novo reabre, e aí só a pessoa fecha."""
    from app.services.conciliacao_calculo import recalcular_loja

    loja = await _liberou_306115(db, make_user)
    agora = datetime.now(UTC)
    r = await recalcular_loja(db, loja, agora=agora)
    await db.commit()
    assert r["finalizado_so_afiliados"] == 1
    p = await _pedido_310001(db)
    assert p.situacao == "recebido_a_menos" and p.dif_previsto_recebido == Decimal("-11.76")
    assert p.venda_liquido == Decimal("732.10") and p.recebido == Decimal("720.34")
    assert p.motivo == "na liberação a Shopee mudou taxas 124.90 → 126.34, afiliados 0.00 → 10.32"
    assert p.etapa == "finalizado" and p.etapa_por == "robô"
    assert p.etapa_motivo == "só afiliados: 10.32 + 1.44 de taxa em cima deles = toda a diferença"
    # a rodada seguinte não finaliza de novo nem mexe na data
    finalizado_em = p.etapa_em
    r = await recalcular_loja(db, loja, agora=agora + timedelta(days=1))
    await db.commit()
    assert "finalizado_so_afiliados" not in r
    p = await _pedido_310001(db)
    assert p.etapa == "finalizado" and p.etapa_em == finalizado_em
    # fora do "Onde agir hoje", que é também a lista do vigia no Threema
    auth_as(await make_user(permissions={"conciliacao": {"view": True}}))
    corpo = (await client.get("/api/conciliacao/resumo", params={"dia": "2026-10-14"})).json()
    assert not [a for a in corpo["agir"] if "a menos" in a["titulo"]]
    # cobrança nova no pedido: reabre e o robô não fecha de novo
    db.add(
        ConciliacaoMovimento(
            integration_id=loja.integration_id,
            platform=IntegrationPlatform.SHOPEE,
            origem="shopee_carteira",
            id_externo="cobranca1",
            data=agora + timedelta(days=2),
            tipo="ajuste_shopee",
            descricao="ADJUSTMENT_CENTER_DEDUCT",
            sub_tipo="ADJUSTMENT_CENTER_DEDUCT",
            pedido_marketplace="261006VX383P9J",
            pedido_bling="310001",
            vinculado_por="vinicius",
            credito=Decimal("0"),
            debito=Decimal("5"),
            raw={},
        )
    )
    await db.commit()
    for dias in (3, 4):
        await recalcular_loja(db, loja, agora=agora + timedelta(days=dias))
        await db.commit()
        p = await _pedido_310001(db)
        assert p.etapa == "reaberto" and "movimento novo" in p.etapa_motivo


async def test_diferenca_alem_do_afiliado_continua_pendencia(db, make_user):
    """R$ 1,00 a mais de taxa de serviço junto com o afiliado: a conta dos 14% não fecha
    e o pedido fica no "Onde agir"."""
    from app.services.conciliacao_calculo import recalcular_loja

    loja = await _liberou_306115(db, make_user, servico=27.34, renda=719.34)
    r = await recalcular_loja(db, loja, agora=datetime.now(UTC))
    await db.commit()
    assert "finalizado_so_afiliados" not in r
    p = await _pedido_310001(db)
    assert p.situacao == "recebido_a_menos" and p.dif_previsto_recebido == Decimal("-12.76")
    assert p.etapa == "pendencia" and p.etapa_motivo is None


async def test_afiliado_reaberto_pela_pessoa_nao_volta_a_finalizar(db, make_user):
    from app.services.conciliacao_calculo import recalcular_loja

    loja = await _liberou_306115(db, make_user)
    agora = datetime.now(UTC)
    await recalcular_loja(db, loja, agora=agora)
    await db.commit()
    p = await _pedido_310001(db)
    p.etapa, p.etapa_por, p.etapa_motivo = "reaberto", "cairo sa", "quero conferir"
    await db.commit()
    await recalcular_loja(db, loja, agora=agora + timedelta(days=1))
    await db.commit()
    p = await _pedido_310001(db)
    assert p.etapa == "reaberto" and p.etapa_por == "cairo sa"
    assert p.etapa_motivo == "quero conferir"
