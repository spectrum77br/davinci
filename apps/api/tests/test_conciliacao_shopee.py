"""Conciliação — Shopee: a carteira como extrato (passo 3, 15/10/2026)."""

from __future__ import annotations

import time
from datetime import UTC, date, datetime
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
    chamados: list[tuple[int, str]] = []

    async def falso(session, *, bling_order_id, trigger="manual"):
        chamados.append((bling_order_id, trigger))
        return {"ok": True}

    import app.services.marketplace_financials as mf

    monkeypatch.setattr(mf, "run_sync_marketplace_financials_for_bling_order", falso)
    r = await mod.reler_escrow_liberados(db, loja)
    assert r == {"candidatos": 1, "relidos": 1, "erros": []}
    assert chamados == [(5000, "conciliacao_liberacao")]
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


async def test_saldo_que_pula_nao_ganha_ajuste_e_os_do_robo_saem(db, make_user):
    """16/10 à noite (Vinicius, essência do painel): a carteira muda de saldo sem listar o
    lançamento (renda 292,39, saldo sobe 291,41) → NÃO se inventa linha de ajuste pra
    fechar o dia: a diferença de −0,98 aparece e eles decidem. Ajuste que o robô tinha
    criado sai e o dia reabre; ajuste que uma pessoa mexeu fica."""
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
    assert not [m for m in movs if m.tipo == "ajuste_shopee"]
    # o dia 11 não fecha sozinho: diferença de −0,98 fica à vista
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
    ajustes = [m for m in movs if m.tipo == "ajuste_shopee"]
    assert [m.id_externo for m in ajustes] == ["ajuste-saldo-2026-10-11-2"]  # o mexido fica
    dia = (await db.execute(select(ConciliacaoDia).where(ConciliacaoDia.dia == d11))).scalar_one()
    assert dia.fechado_em is None and "ajuste automático" in (dia.reaberto_motivo or "")
