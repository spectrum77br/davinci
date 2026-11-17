"""Conciliação — Temu lida da tela (17/11/2026, piloto na loja Eron).

A Temu não tem API de extrato: o robô do Mac mini lê Finanças › Pagamentos e envia um
JSON com o saldo, as linhas de Extratos e o previsto por pedido. Estes testes usam o
formato real que a página carrega (conferido na Eron em 17/11).
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, text

from app.models import (
    Base,
    BlingOrder,
    ConciliacaoDia,
    ConciliacaoLoja,
    ConciliacaoMovimento,
    ConciliacaoPedido,
    ConciliacaoPrevisto,
    Integration,
    LogisticaRoboComando,
    MarketplaceOrderFinancial,
)
from app.models.conciliacao import ORIGEM_TEMU, ORIGENS_EXTRATO
from app.models.enums import IntegrationPlatform
from app.security.cipher import encrypt_json
from app.services import logistica_robo
from app.services.conciliacao_calculo import recalcular_loja
from app.services.conciliacao_extrato import bling_store_ids_da_loja
from app.services.conciliacao_temu import (
    LeituraInvalidaError,
    centavos,
    encadear_saldo,
    enfileirar_leitura_todas,
    ingerir_leitura,
    linha_para_movimento,
    previsto_do_item,
    reais,
    saldo_da_tela,
)

LOJA_BLING = "206288707"
CONTA = "eron (temu)"
PO_ANTIGO = "PO-029-03192061952632764"  # vendido antes de 24/10: "anterior ao início"
PO_PAGO = "PO-029-17715128424313304"  # pago antes de o robô ler o previsto
PO_ESPERA = "PO-029-15960839637110768"  # previsto com data
PO_SEM_DATA = "PO-029-11520224554871191"  # previsto "Entregue em + 7 dias"
PO_NOVO = "PO-029-20000000000000001"  # vendido, a Temu ainda não mostra
PO_REEMBOLSO = "PO-029-13244443285111217"  # pago e reembolsado no mesmo segundo
AGORA = datetime(2026, 11, 17, 15, 5, tzinfo=UTC)


def _v(c: int) -> dict:
    return {
        "market_region_id": 29,
        "symbol": "R$",
        "decimals": 2,
        "currency_code": "BRL",
        "amount": c,
        "minus": c < 0,
    }


def _linha(
    flow: str,
    po: str | None,
    valor: int,
    quando: datetime,
    *,
    categoria: str = "Order Payment",
    texto: str = "Pagamento da encomenda",
) -> dict:
    return {
        "flow_id": flow,
        "mall_id": 635517739909443,
        "batch_id": f"LOCAL-BATCH-BRL-BRL-29-{quando:%Y-%m-%d-%H}-2",
        "prod_amount": _v(valor),
        "fee_amount": _v(0),
        "other_amount": _v(0),
        "total_amount": _v(valor),
        "created_at": int(quando.timestamp() * 1000),
        "trans_sn": po or f"TT-029-{flow}",
        "order_id": po,
        "parent_order_sn": po,
        "biz_category": categoria,
        "ori_biz_category": categoria,
        "sub_biz_category": categoria,
        "biz_category_text": texto,
        "sub_biz_category_text": texto,
        "note": "Travesseiro de Pescoço para Viagem",
    }


def _previsto(po: str, valor: str = "R$26,00", quando: datetime | None = None) -> dict:
    return {
        "parent_order_sn": po,
        "estimated_settled_time": str(int(quando.timestamp() * 1000))
        if quando
        else "Entregue em + 7 dias",
        "formatted_estimated_settled_time": "x",
        "estimated_revenue": valor,
        "estimated_positive_revenue": valor,
        "estimated_negative_revenue": "R$0,00",
        "order_details_link": f"/order-detail.html?parent_order_sn={po}",
        "parent_order_time": 1794858224000,
        "order_status_desc": "Entregue" if quando else "Enviada",
        "parent_order_status": 1 if quando else 0,
        "accelerated": False,
    }


def _leitura(
    linhas: list[dict],
    previstos: list[dict],
    *,
    total: int,
    reserva: int = 0,
    saques: list[dict] | None = None,
) -> dict:
    """`total` em centavos = Saldo total da tela (Reserva + Fundos disponíveis)."""
    return {
        "loja": CONTA,
        "loja_na_tela": "Mercado Eron",
        "periodo": "10/17/2026 ~ 11/17/2026",
        "saldo": {
            "success": True,
            "result": {
                "opening_amount": _v(0),
                "sales_amount": _v(total),
                "refund_amount": _v(0),
                "reserve_amount": _v(reserva),
                "payout_amount": _v(total - reserva),
            },
        },
        "extrato": linhas,
        "extrato_total": len(linhas),
        "previstos": previstos,
        "previstos_total": len(previstos),
        "saques": saques or [],
        "saques_total": len(saques or []),
    }


async def _loja_temu(db, make_user, *, inicio=date(2026, 10, 24)) -> ConciliacaoLoja:
    """A loja como a 0495 cria: âncora arquivada, sem credencial, loja do Bling escrita."""
    dono = await make_user()
    ancora = Integration(
        user_id=dono.id,
        platform=IntegrationPlatform.TEMU,
        name="eron (temu) · só conciliação",
        status="ancora",
        credentials=b"",
        archived_at=datetime(2026, 11, 17, tzinfo=UTC),
    )
    db.add(ancora)
    await db.flush()
    loja = ConciliacaoLoja(
        integration_id=ancora.id,
        platform=IntegrationPlatform.TEMU,
        conta=CONTA,
        inicio=inicio,
        modo_extrato="upload",
        bling_loja=LOJA_BLING,
    )
    db.add(loja)
    for n, bid, po, total, dia in (
        ("303000", 9000, PO_ANTIGO, "45.80", datetime(2026, 10, 12, 12, tzinfo=UTC)),
        ("307141", 9001, PO_PAGO, "45.80", datetime(2026, 11, 2, 12, tzinfo=UTC)),
        ("306800", 9002, PO_ESPERA, "45.80", datetime(2026, 11, 4, 12, tzinfo=UTC)),
        ("309335", 9003, PO_SEM_DATA, "45.80", datetime(2026, 11, 16, 12, tzinfo=UTC)),
        ("309400", 9004, PO_NOVO, "45.80", datetime(2026, 11, 17, 12, tzinfo=UTC)),
        ("308104", 9005, PO_REEMBOLSO, "36.00", datetime(2026, 11, 8, 12, tzinfo=UTC)),
    ):
        db.add(
            BlingOrder(
                bling_id=bid,
                numero=n,
                numeroloja=po,
                data=dia,
                total=Decimal(total),
                totalprodutos=Decimal(total),
                situacao="Em andamento",
                loja=LOJA_BLING,
                item_index=0,
                preco_custo=18.0,
                item_quantidade=1,
                item_codigo="trav",
            )
        )
    await db.commit()
    return loja


def _primeira_leitura() -> dict:
    """Saldo de antes do início (14,00) + 212,00 de um pedido antigo + 26,00 do 307141."""
    return _leitura(
        [
            _linha("PFI-A", PO_ANTIGO, 21200, datetime(2026, 10, 24, 12, 46, 42, tzinfo=UTC)),
            _linha("PFI-B", PO_PAGO, 2600, datetime(2026, 11, 14, 13, 46, 39, tzinfo=UTC)),
        ],
        [
            _previsto(PO_ESPERA, quando=datetime(2026, 11, 18, 19, 51, tzinfo=UTC)),
            _previsto(PO_SEM_DATA),
        ],
        total=1400 + 21200 + 2600,
        reserva=2520,
    )


async def _pedido(db, loja: ConciliacaoLoja, numero: str) -> ConciliacaoPedido:
    return (
        await db.execute(
            select(ConciliacaoPedido)
            .where(
                ConciliacaoPedido.integration_id == loja.integration_id,
                ConciliacaoPedido.pedido_bling == numero,
            )
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


# ---------- contas puras ----------


def test_valores_da_tela_viram_decimal():
    assert centavos(_v(2600)) == Decimal("26.00")
    assert centavos(_v(-2600)) == Decimal("-26.00")
    assert centavos({"amount": 5, "decimals": 0}) == Decimal("5.00")
    assert centavos(None) is None and centavos({"x": 1}) is None
    # se a Temu mandar o valor absoluto com a marca de negativo, vale a marca
    assert centavos({"amount": 2600, "decimals": 2, "minus": True}) == Decimal("-26.00")
    assert reais("R$26,00") == Decimal("26.00")
    assert reais("-R$1.234,56") == Decimal("-1234.56")
    assert reais("R$\N{NO-BREAK SPACE}0,00") == Decimal("0.00")
    assert reais("Entregue em + 7 dias") is None and reais(None) is None


def test_saldo_total_e_reserva_mais_disponivel():
    s = saldo_da_tela({"result": {"reserve_amount": _v(5380), "payout_amount": _v(49820)}})
    assert s["total"] == Decimal("552.00")
    assert (s["reserva"], s["disponivel"]) == (Decimal("53.80"), Decimal("498.20"))
    assert saldo_da_tela({"result": {"reserve_amount": _v(1)}})["total"] is None


def test_linha_do_extrato_vira_movimento():
    quando = datetime(2026, 11, 17, 13, 6, 39, tzinfo=UTC)
    pago = linha_para_movimento(_linha("PFI-1", PO_REEMBOLSO, 2600, quando))
    assert pago["origem"] == ORIGEM_TEMU and ORIGEM_TEMU in ORIGENS_EXTRATO
    assert (pago["tipo"], pago["credito"], pago["debito"]) == (
        "venda_liberada",
        Decimal("26.00"),
        Decimal("0"),
    )
    assert pago["pedido_marketplace"] == PO_REEMBOLSO and pago["bruto"] == Decimal("26.00")
    assert pago["id_externo"] == "PFI-1" and pago["classificacao"] is None
    volta = linha_para_movimento(
        _linha("PFI-2", PO_REEMBOLSO, -2600, quando, categoria="Refund", texto="Reembolso")
    )
    assert (volta["tipo"], volta["credito"], volta["debito"]) == (
        "estorno",
        Decimal("0"),
        Decimal("26.00"),
    )
    assert volta["descricao"].startswith("Reembolso do pedido PO-029-")
    # publicidade sem pedido já nasce classificada; tipo que o leitor não conhece fica
    # "outro" sem dono (uma pessoa decide), nunca some
    ads = linha_para_movimento(
        _linha("PFI-3", None, -1500, quando, categoria="Advertising service fee", texto="Ads")
    )
    assert (ads["tipo"], ads["classificacao"], ads["pedido_marketplace"]) == (
        "publicidade",
        "publicidade",
        None,
    )
    reserva = linha_para_movimento(
        _linha("PFI-4", None, -5000, quando, categoria="Reserve amount", texto="Reserva")
    )
    assert (reserva["tipo"], reserva["classificacao"]) == ("outro", None)
    etiqueta = linha_para_movimento(
        _linha("PFI-5", PO_PAGO, -900, quando, categoria="Shipping label purchase", texto="E")
    )
    assert (etiqueta["tipo"], etiqueta["debito"]) == ("frete", Decimal("9.00"))
    # o lote da hora não é o "pagamento" do pedido (as sugestões ofereceriam o lote todo)
    assert pago["pagamento_id"] is None and pago["raw"]["batch_id"].startswith("LOCAL-BATCH")
    # sem data, valor ou identificação a linha não existe; data em segundos não é data dela
    assert linha_para_movimento({"flow_id": "x"}) is None
    em_segundos = _linha("PFI-6", PO_PAGO, 2600, quando)
    em_segundos["created_at"] = int(quando.timestamp())
    assert linha_para_movimento(em_segundos) is None


def test_saldo_e_encadeado_do_total_da_tela_pra_tras():
    t = datetime(2026, 11, 17, 13, 6, 39, tzinfo=UTC)
    movs = [
        linha_para_movimento(_linha("PFI-1", PO_PAGO, 2600, t - timedelta(days=3))),
        linha_para_movimento(_linha("PFI-3", PO_REEMBOLSO, -2600, t, categoria="Refund")),
        linha_para_movimento(_linha("PFI-2", PO_REEMBOLSO, 2600, t)),
    ]
    encadear_saldo(movs, Decimal("552.00"))
    por_id = {m["id_externo"]: m["saldo_apos"] for m in movs}
    # no mesmo segundo o pagamento vem antes do reembolso: 552 → 578 → 552
    assert por_id == {
        "PFI-1": Decimal("552.00"),
        "PFI-2": Decimal("578.00"),
        "PFI-3": Decimal("552.00"),
    }


def test_previsto_do_item_le_valor_e_data():
    quando = datetime(2026, 11, 18, 19, 51, tzinfo=UTC)
    p = previsto_do_item(_previsto(PO_ESPERA, "R$52,00", quando))
    assert (p["pedido_marketplace"], p["valor"], p["liquidacao_em"]) == (
        PO_ESPERA,
        Decimal("52.00"),
        quando,
    )
    assert previsto_do_item(_previsto(PO_SEM_DATA))["liquidacao_em"] is None
    assert previsto_do_item({"parent_order_sn": "x", "estimated_revenue": "R$1,00"}) is None


# ---------- a leitura ----------


async def test_a_loja_da_temu_acha_os_pedidos_pela_loja_do_bling_escrita_nela(db, make_user):
    loja = await _loja_temu(db, make_user)
    ancora = await db.get(Integration, loja.integration_id)
    assert await bling_store_ids_da_loja(db, ancora) == {LOJA_BLING}


async def test_leitura_grava_linhas_saldo_e_previsto_e_nao_duplica(db, make_user):
    loja = await _loja_temu(db, make_user)
    r = await ingerir_leitura(db, loja, _primeira_leitura(), agora=AGORA)
    await db.commit()
    assert (r["linhas"], r["inseridos"], r["no_painel"], r["antes_do_inicio"]) == (2, 2, 2, 0)
    assert r["por_tipo"] == {"venda_liberada": 2}
    assert r["previstos"] == {
        "lidos": 2,
        "na_tela": 2,
        "ilegiveis": 0,
        "repetidos": 0,
        "novos": 2,
        "atualizados": 0,
        "mudaram": 0,
        "ja_pagos": 0,
        "iguais": 0,
    }
    assert r["loja_na_temu"] == "635517739909443" and "aviso" not in r
    assert r["saldo_temu"]["total"] == "252.00" and r["saldo_temu"]["reserva"] == "25.20"
    movs = (
        (
            await db.execute(
                select(ConciliacaoMovimento)
                .where(ConciliacaoMovimento.integration_id == loja.integration_id)
                .order_by(ConciliacaoMovimento.data)
            )
        )
        .scalars()
        .all()
    )
    assert [(m.id_externo, m.saldo_apos, m.pedido_bling) for m in movs] == [
        ("PFI-A", Decimal("226.00"), "303000"),
        ("PFI-B", Decimal("252.00"), "307141"),
    ]
    assert loja.ultimo_extrato_em == AGORA and loja.ultimo_erro is None
    # a Margem não é tocada: nenhuma venda da Temu gravada lá
    assert (await db.execute(select(MarketplaceOrderFinancial))).scalars().all() == []
    # a mesma leitura de novo: nada entra em dobro
    r2 = await ingerir_leitura(db, loja, _primeira_leitura(), agora=AGORA + timedelta(hours=1))
    await db.commit()
    assert (r2["inseridos"], r2["ja_existiam"]) == (0, 2)
    assert r2["previstos"]["novos"] == 0 and r2["previstos"]["iguais"] == 2


async def test_leitura_incompleta_e_recusada_inteira(db, make_user):
    loja = await _loja_temu(db, make_user)
    falta_pagina = _primeira_leitura()
    falta_pagina["extrato_total"] = 16
    with pytest.raises(LeituraInvalidaError, match="a tela tem 16 linha"):
        await ingerir_leitura(db, loja, falta_pagina, agora=AGORA)
    sem_saldo = _primeira_leitura()
    sem_saldo["saldo"] = {"result": {}}
    with pytest.raises(LeituraInvalidaError, match="saldo"):
        await ingerir_leitura(db, loja, sem_saldo, agora=AGORA)
    repetida = _primeira_leitura()
    repetida["extrato"].append(repetida["extrato"][0])
    repetida["extrato_total"] = 3
    with pytest.raises(LeituraInvalidaError, match="repetida"):
        await ingerir_leitura(db, loja, repetida, agora=AGORA)
    sem_total = _primeira_leitura()
    del sem_total["previstos_total"]
    with pytest.raises(LeituraInvalidaError, match="previstos_total"):
        await ingerir_leitura(db, loja, sem_total, agora=AGORA)
    assert (await db.execute(select(ConciliacaoMovimento))).scalars().all() == []
    assert (await db.execute(select(ConciliacaoPrevisto))).scalars().all() == []


async def test_linha_de_antes_do_inicio_da_loja_nao_entra(db, make_user):
    loja = await _loja_temu(db, make_user)
    leitura = _leitura(
        [
            _linha("PFI-0", PO_ANTIGO, 1400, datetime(2026, 10, 16, 12, tzinfo=UTC)),
            _linha("PFI-A", PO_ANTIGO, 21200, datetime(2026, 10, 24, 12, 46, tzinfo=UTC)),
        ],
        [],
        total=22600,
    )
    r = await ingerir_leitura(db, loja, leitura, agora=AGORA)
    await db.commit()
    assert (r["no_painel"], r["antes_do_inicio"], r["inseridos"]) == (1, 1, 1)
    so = (await db.execute(select(ConciliacaoMovimento))).scalars().one()
    # o saldo da linha que ficou já conta a de antes do início (14,00 + 212,00)
    assert (so.id_externo, so.saldo_apos) == ("PFI-A", Decimal("226.00"))


async def test_leitura_de_outra_loja_da_temu_e_recusada(db, make_user):
    loja = await _loja_temu(db, make_user)
    await ingerir_leitura(db, loja, _primeira_leitura(), agora=AGORA)
    await db.commit()
    # perfil do AdsPower trocado: as linhas vêm com o nº de outra loja
    outra = _leitura(
        [_linha("PFI-X", "PO-029-99999999999999999", 5000, datetime(2026, 11, 16, tzinfo=UTC))],
        [],
        total=5000,
    )
    outra["extrato"][0]["mall_id"] = 111222333
    outra["loja_na_tela"] = "Outra Loja"
    with pytest.raises(LeituraInvalidaError, match="outra loja da Temu"):
        await ingerir_leitura(db, loja, outra, agora=AGORA + timedelta(hours=1))
    # duas lojas na mesma leitura também não entram
    mistura = _primeira_leitura()
    mistura["extrato"][1]["mall_id"] = 111222333
    with pytest.raises(LeituraInvalidaError, match="mais de uma loja"):
        await ingerir_leitura(db, loja, mistura, agora=AGORA + timedelta(hours=1))
    await db.rollback()
    ids = (await db.execute(select(ConciliacaoMovimento.id_externo))).scalars().all()
    assert sorted(ids) == ["PFI-A", "PFI-B"]


async def test_leitura_que_perde_linha_gravada_ou_vem_vazia_e_recusada(db, make_user):
    loja = await _loja_temu(db, make_user)
    await ingerir_leitura(db, loja, _primeira_leitura(), agora=AGORA)
    await db.commit()
    depois = AGORA + timedelta(days=1)
    # a PFI-A (24/10) e uma linha nova de 18/11, sem a PFI-B (14/11) que estava gravada
    furada = _leitura(
        [
            _primeira_leitura()["extrato"][0],
            _linha("PFI-C", PO_ESPERA, 2600, datetime(2026, 11, 18, 19, 51, tzinfo=UTC)),
        ],
        [],
        total=1400 + 21200 + 2600,
    )
    with pytest.raises(LeituraInvalidaError, match="1 linha\\(s\\) já gravada"):
        await ingerir_leitura(db, loja, furada, agora=depois)
    # tela sem linha nenhuma (conta errada, sem `mall_id` pra denunciar): não conta como
    # "lida" — a PFI-B é de três dias antes
    with pytest.raises(LeituraInvalidaError, match="já gravada"):
        await ingerir_leitura(db, loja, _leitura([], [], total=0), agora=depois)
    await db.rollback()
    await db.refresh(loja)
    assert loja.ultimo_extrato_em == AGORA


async def test_saldo_que_andou_sem_linha_avisa_e_o_quadro_mostra_o_saldo_da_tela(
    db, make_user, auth_as, client
):
    loja = await _loja_temu(db, make_user)
    await ingerir_leitura(db, loja, _primeira_leitura(), agora=AGORA)
    await recalcular_loja(db, loja, agora=AGORA)
    await db.commit()
    # as mesmas linhas, e a Temu tirou 100,00 do saldo sem lançar nada
    depois = AGORA + timedelta(days=1)
    mesma = _primeira_leitura()
    mesma["saldo"]["result"]["payout_amount"] = _v(1400 + 21200 + 2600 - 2520 - 10000)
    r = await ingerir_leitura(db, loja, mesma, agora=depois)
    await db.commit()
    assert r["inseridos"] == 0
    assert "o saldo andou sem linha" in r["aviso"] and "152.00" in r["aviso"]
    assert loja.ultimo_erro.startswith("temu: o saldo da tela da Temu (152.00)")
    # o quadro "Dinheiro nas plataformas" mostra o que a Temu informa, não a última linha
    auth_as(await make_user(permissions={"conciliacao": {"view": True, "edit": True}}))
    resumo = (await client.get("/api/conciliacao/resumo")).json()
    eron = next(x for x in resumo["lojas"] if x["conta"] == CONTA)
    assert eron["dinheiro"]["na_conta"] == 152.0
    # leitura seguinte com o saldo de volta: o aviso sai
    r = await ingerir_leitura(db, loja, _primeira_leitura(), agora=depois + timedelta(days=1))
    assert "aviso" not in r and loja.ultimo_erro is None


async def test_previsto_ilegivel_repetido_ou_a_menos_avisa_sem_travar_o_extrato(db, make_user):
    loja = await _loja_temu(db, make_user)
    leitura = _primeira_leitura()
    leitura["previstos"] = [
        _previsto(PO_ESPERA, "R$26,00"),
        _previsto(PO_ESPERA, "R$13,00"),  # o mesmo pedido de novo: vale o primeiro
        _previsto(PO_SEM_DATA, "valor a calcular"),  # a Temu mudou o formato
        # a Temu estima COBRAR (pedido reembolsado depois de pago)
        _previsto("PO-029-20000000000000001", "-R$5,00"),
    ]
    leitura["previstos_total"] = 6  # a tela diz mais do que a lista trouxe
    r = await ingerir_leitura(db, loja, leitura, agora=AGORA)
    await db.commit()
    assert r["inseridos"] == 2  # o extrato entrou
    assert (
        r["previstos"]["novos"],
        r["previstos"]["repetidos"],
        r["previstos"]["ilegiveis"],
    ) == (2, 1, 1)
    assert "1 previsto(s) sem nº do pedido ou valor legível" in r["aviso"]
    assert "a tela diz 6 pedido(s) não processado(s) e a lista trouxe 4" in r["aviso"]
    assert loja.ultimo_erro.startswith("temu: ")
    guardados = {
        p.pedido_marketplace: p.valor
        for p in (await db.execute(select(ConciliacaoPrevisto))).scalars()
    }
    assert guardados == {
        PO_ESPERA: Decimal("26.00"),
        "PO-029-20000000000000001": Decimal("-5.00"),
    }


async def test_saque_que_ja_estava_na_tela_nao_avisa_de_novo(db, make_user):
    loja = await _loja_temu(db, make_user)
    saque = {"withdraw_sn": "W1", "amount": _v(10000), "status": 3}
    com = _primeira_leitura()
    com["saques"], com["saques_total"] = [saque], 1
    r = await ingerir_leitura(db, loja, com, agora=AGORA)
    assert r["saques_novos"] == 1 and "1 saque(s) novo(s)" in r["aviso"]
    # a aba Pagamentos é um histórico: o mesmo saque volta em toda leitura
    r = await ingerir_leitura(db, loja, com, agora=AGORA + timedelta(days=1))
    assert (r["saques"], r["saques_novos"]) == (1, 0) and "aviso" not in r
    assert loja.ultimo_erro is None
    # um saque novo avisa de novo
    com["saques"], com["saques_total"] = [saque, {**saque, "withdraw_sn": "W2"}], 2
    r = await ingerir_leitura(db, loja, com, agora=AGORA + timedelta(days=2))
    assert r["saques_novos"] == 1 and "1 saque(s) novo(s)" in r["aviso"]


# ---------- os pedidos ----------


async def test_pedidos_da_temu_aguardam_batem_ou_ficam_sem_previsto(db, make_user):
    loja = await _loja_temu(db, make_user)
    await ingerir_leitura(db, loja, _primeira_leitura(), agora=AGORA)
    await recalcular_loja(db, loja, agora=AGORA)
    await db.commit()
    # previsto com data: aguardando, com o valor que a Temu estimou e a data dela
    espera = await _pedido(db, loja, "306800")
    assert (espera.situacao, espera.venda_liquido, espera.venda_valor) == (
        "aguardando",
        Decimal("26.00"),
        Decimal("26.00"),
    )
    assert espera.previsao_liberacao == datetime(2026, 11, 18, 19, 51, tzinfo=UTC)
    assert espera.motivo == "libera em 18/11" and espera.recebido is None
    # previsto sem data: a regra da Temu
    sem_data = await _pedido(db, loja, "309335")
    assert (sem_data.situacao, sem_data.venda_liquido) == ("aguardando", Decimal("26.00"))
    assert sem_data.motivo == "a Temu paga 7 dias depois da entrega"
    # vendido e a Temu ainda não mostra: aguardando, sem inventar valor
    novo = await _pedido(db, loja, "309400")
    assert (novo.situacao, novo.venda_liquido) == ("aguardando", None)
    assert novo.motivo == "a Temu ainda não mostrou o previsto deste pedido"
    # pago antes de o robô ler o previsto: não dá pra dizer que bateu
    pago = await _pedido(db, loja, "307141")
    assert (pago.situacao, pago.recebido, pago.venda_liquido) == (
        "sem_venda",
        Decimal("26.00"),
        None,
    )
    assert pago.motivo == "a Temu pagou antes de o robô ler o previsto deste pedido"
    # pedido vendido antes de 24/10 não vira pedido do painel; a linha dele é "anterior"
    antigo = (
        await db.execute(
            select(ConciliacaoMovimento).where(ConciliacaoMovimento.id_externo == "PFI-A")
        )
    ).scalar_one()
    assert antigo.classificacao == "anterior_ao_inicio"

    # dia seguinte: a Temu pagou o 306800 (26,00) e ele saiu da lista de previstos
    depois = AGORA + timedelta(days=1)
    segunda = _leitura(
        [
            *_primeira_leitura()["extrato"],
            _linha("PFI-C", PO_ESPERA, 2600, datetime(2026, 11, 18, 19, 51, tzinfo=UTC)),
        ],
        [_previsto(PO_SEM_DATA)],
        total=1400 + 21200 + 2600 + 2600,
    )
    r = await ingerir_leitura(db, loja, segunda, agora=depois)
    await recalcular_loja(db, loja, agora=depois)
    await db.commit()
    assert r["inseridos"] == 1
    bateu = await _pedido(db, loja, "306800")
    assert (bateu.situacao, bateu.recebido, bateu.venda_liquido, bateu.dif_previsto_recebido) == (
        "bateu",
        Decimal("26.00"),
        Decimal("26.00"),
        Decimal("0.00"),
    )
    # custo 18,00 → sobrou 8,00: a margem de verdade, não a do Bling (45,80)
    assert bateu.sobrou == Decimal("8.00")


async def test_temu_pagou_menos_que_estimou_aparece(db, make_user):
    loja = await _loja_temu(db, make_user)
    await ingerir_leitura(db, loja, _primeira_leitura(), agora=AGORA)
    depois = AGORA + timedelta(days=1)
    segunda = _leitura(
        [
            *_primeira_leitura()["extrato"],
            _linha("PFI-C", PO_ESPERA, 2000, datetime(2026, 11, 18, 19, 51, tzinfo=UTC)),
        ],
        # a tela ainda lista o pedido, agora com outro número: o previsto de quem já foi
        # pago não é reescrito (comparar, não copiar)
        [_previsto(PO_ESPERA, "R$20,00"), _previsto(PO_SEM_DATA)],
        total=1400 + 21200 + 2600 + 2000,
    )
    r = await ingerir_leitura(db, loja, segunda, agora=depois)
    await recalcular_loja(db, loja, agora=depois)
    await db.commit()
    assert r["previstos"]["ja_pagos"] == 1
    p = await _pedido(db, loja, "306800")
    assert (p.situacao, p.recebido, p.venda_liquido, p.dif_previsto_recebido) == (
        "recebido_a_menos",
        Decimal("20.00"),
        Decimal("26.00"),
        Decimal("-6.00"),
    )
    assert p.motivo == "a Temu estimou 26.00 e pagou 20.00"


async def test_estimativa_que_mudou_antes_de_pagar_fica_no_motivo(db, make_user):
    loja = await _loja_temu(db, make_user)
    await ingerir_leitura(db, loja, _primeira_leitura(), agora=AGORA)
    depois = AGORA + timedelta(days=1)
    mudou = _leitura(
        _primeira_leitura()["extrato"],
        [
            _previsto(PO_ESPERA, "R$13,00", datetime(2026, 11, 18, 19, 51, tzinfo=UTC)),
            _previsto(PO_SEM_DATA),
        ],
        total=1400 + 21200 + 2600,
    )
    r = await ingerir_leitura(db, loja, mudou, agora=depois)
    assert (r["previstos"]["mudaram"], r["previstos"]["atualizados"]) == (1, 1)
    await recalcular_loja(db, loja, agora=depois)
    await db.commit()
    p = await _pedido(db, loja, "306800")
    assert (p.situacao, p.venda_liquido) == ("aguardando", Decimal("13.00"))
    assert "a Temu mudou a estimativa de 26.00 para 13.00 antes de pagar" in p.motivo
    previsto = (
        await db.execute(
            select(ConciliacaoPrevisto).where(ConciliacaoPrevisto.pedido_marketplace == PO_ESPERA)
        )
    ).scalar_one()
    assert (previsto.primeiro_valor, previsto.valor) == (Decimal("26.00"), Decimal("13.00"))
    assert previsto.primeiro_lido_em == AGORA and previsto.lido_em == depois


async def test_pago_e_reembolsado_no_mesmo_segundo_e_estornado(db, make_user):
    loja = await _loja_temu(db, make_user)
    t = datetime(2026, 11, 17, 13, 6, 39, tzinfo=UTC)
    await ingerir_leitura(
        db,
        loja,
        _leitura([], [_previsto(PO_REEMBOLSO)], total=1400),
        agora=AGORA - timedelta(days=1),
    )
    leitura = _leitura(
        [
            _linha("PFI-R1", PO_REEMBOLSO, 2600, t),
            _linha("PFI-R2", PO_REEMBOLSO, -2600, t, categoria="Refund", texto="Reembolso"),
        ],
        [],
        total=1400,
    )
    await ingerir_leitura(db, loja, leitura, agora=AGORA)
    await recalcular_loja(db, loja, agora=AGORA)
    await db.commit()
    p = await _pedido(db, loja, "308104")
    assert (p.situacao, p.recebido, p.liberado_valor, p.saidas_depois) == (
        "estornado_depois",
        Decimal("0.00"),
        Decimal("26.00"),
        Decimal("26.00"),
    )
    assert p.motivo == "estorno de 26.00 em 17/11"


# ---------- o dia ----------


async def test_dia_fecha_com_o_saldo_da_tela_e_acusa_saldo_que_andou_sem_linha(
    db, make_user, auth_as, client
):
    from app.services.conciliacao_jobs import fechar_dias_prontos

    loja = await _loja_temu(db, make_user)
    await ingerir_leitura(db, loja, _primeira_leitura(), agora=AGORA)
    await recalcular_loja(db, loja, agora=AGORA)
    await db.commit()
    auth_as(await make_user(permissions={"conciliacao": {"view": True, "edit": True}}))
    r = await client.get("/api/conciliacao/extrato", params={"loja": CONTA, "dia": "2026-10-24"})
    assert r.status_code == 200, r.text
    totais = r.json()["totais"]
    # 24/10 começa nos 14,00 que a Temu já guardava e termina em 226,00
    assert (totais["saldo_inicial"], totais["saldo_final"], totais["saldo_informado"]) == (
        14.0,
        226.0,
        226.0,
    )
    assert totais["diferenca"] == 0.0
    r = await client.get("/api/conciliacao/extrato", params={"loja": CONTA, "dia": "2026-11-14"})
    totais = r.json()["totais"]
    assert (totais["saldo_inicial"], totais["saldo_final"], totais["diferenca"]) == (
        226.0,
        252.0,
        0.0,
    )
    # 24/10: a linha é de pedido anterior ao início (classificada) e o saldo bate → fecha;
    # 14/11: o pedido está "sem previsto", mas a linha tem dono e o saldo bate → fecha
    dias = await fechar_dias_prontos(db, loja, maximo_dias=3650)
    await db.commit()
    assert set(dias["fechados"]) == {"2026-10-24", "2026-11-14"}

    # a Temu tirou 100,00 do saldo sem linha nenhuma (um saque que o leitor ainda não
    # conhece): a linha nova termina no saldo novo e o dia dela NÃO fecha
    depois = AGORA + timedelta(days=2)
    com_saque = _leitura(
        [
            *_primeira_leitura()["extrato"],
            _linha("PFI-C", PO_ESPERA, 2600, datetime(2026, 11, 18, 19, 51, tzinfo=UTC)),
        ],
        [],
        total=1400 + 21200 + 2600 + 2600 - 10000,
        saques=[{"withdraw_sn": "W1", "amount": _v(10000)}],
    )
    r3 = await ingerir_leitura(db, loja, com_saque, agora=depois)
    await recalcular_loja(db, loja, agora=depois)
    await db.commit()
    assert (r3["saques"], r3["saques_novos"]) == (1, 1) and "saque" in r3["aviso"]
    assert loja.ultimo_erro.startswith("temu: 1 saque(s) novo(s) na tela")
    r = await client.get("/api/conciliacao/extrato", params={"loja": CONTA, "dia": "2026-11-18"})
    totais = r.json()["totais"]
    assert (totais["saldo_inicial"], totais["saldo_final"], totais["saldo_informado"]) == (
        252.0,
        278.0,
        178.0,
    )
    assert totais["diferenca"] == -100.0
    dias = await fechar_dias_prontos(db, loja, maximo_dias=3650)
    assert "2026-11-18" not in dias["fechados"]
    # os dias já fechados continuam fechados: as linhas antigas não foram reescritas
    fechados = (
        (await db.execute(select(ConciliacaoDia).where(ConciliacaoDia.fechado_em.isnot(None))))
        .scalars()
        .all()
    )
    assert {d.dia for d in fechados} == {date(2026, 10, 24), date(2026, 11, 14)}


async def test_raiox_da_temu_compara_estimado_com_pago(db, make_user, auth_as, client):
    loja = await _loja_temu(db, make_user)
    await ingerir_leitura(db, loja, _primeira_leitura(), agora=AGORA)
    depois = AGORA + timedelta(days=1)
    segunda = _leitura(
        [
            *_primeira_leitura()["extrato"],
            _linha("PFI-C", PO_ESPERA, 2000, datetime(2026, 11, 18, 19, 51, tzinfo=UTC)),
        ],
        [],
        total=1400 + 21200 + 2600 + 2000,
    )
    await ingerir_leitura(db, loja, segunda, agora=depois)
    await recalcular_loja(db, loja, agora=depois)
    await db.commit()
    auth_as(await make_user(permissions={"conciliacao": {"view": True, "edit": True}}))
    r = await client.get("/api/conciliacao/pedidos/306800", params={"loja": CONTA})
    assert r.status_code == 200, r.text
    comp = r.json()["comparativo"]
    assert comp["real_rotulo"] == "extrato da Temu" and comp["comparar"] is True
    liquido = next(x for x in comp["linhas"] if x["chave"] == "liquido")
    assert (liquido["previsto"], liquido["real"], liquido["dif"]) == (26.0, 20.0, -6.0)
    assert "Mercado Pago" not in comp["rodape"]


# ---------- o robô ----------


async def test_endpoint_da_leitura_so_com_o_token_do_executor(db, make_user, auth_as, client):
    from app.config import get_settings

    await _loja_temu(db, make_user)
    # loja de outra plataforma não recebe leitura da Temu
    dono = await make_user()
    outra = Integration(
        user_id=dono.id,
        platform=IntegrationPlatform.AMAZON,
        name="kia",
        status="active",
        credentials=encrypt_json({"refresh_token": "rt"}),
    )
    db.add(outra)
    await db.flush()
    db.add(
        ConciliacaoLoja(
            integration_id=outra.id,
            platform=IntegrationPlatform.AMAZON,
            conta="kia",
            inicio=date(2026, 10, 24),
            modo_extrato="upload",
        )
    )
    await db.commit()
    get_settings().marketing_agent_token = "tok-agente-teste"  # noqa: S105 — token de mentira
    try:
        auth_as(None)
        sem = await client.post("/api/conciliacao/temu/leitura", json=_primeira_leitura())
        assert sem.status_code == 401
        errado = await client.post(
            "/api/conciliacao/temu/leitura",
            json=_primeira_leitura(),
            headers={"X-Agent-Token": "outro"},
        )
        assert errado.status_code == 401
        cab = {"X-Agent-Token": "tok-agente-teste"}
        na_kia = await client.post(
            "/api/conciliacao/temu/leitura",
            json={**_primeira_leitura(), "loja": "kia"},
            headers=cab,
        )
        assert na_kia.status_code == 409 and na_kia.json()["detail"]["code"] == "loja_nao_e_temu"
        incompleta = {**_primeira_leitura(), "extrato_total": 9}
        r = await client.post("/api/conciliacao/temu/leitura", json=incompleta, headers=cab)
        assert r.status_code == 422 and "leitura incompleta" in r.json()["detail"]["mensagem"]
        r = await client.post("/api/conciliacao/temu/leitura", content=b"nao e json", headers=cab)
        assert r.status_code == 422
        r = await client.post(
            "/api/conciliacao/temu/leitura", json=_primeira_leitura(), headers=cab
        )
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert (corpo["loja"], corpo["inseridos"]) == (CONTA, 2)
        assert corpo["recalculo"]["pedidos"] == 5
        # o próprio envio já fecha os dias prontos (a rodada da manhã corre antes do robô)
        assert "2026-11-14" in corpo["dias"]["fechados"]
    finally:
        get_settings().marketing_agent_token = ""


async def test_botao_e_cron_mandam_o_robo_da_temu_e_nao_o_da_amazon(db, make_user, auth_as, client):
    from app.services.conciliacao_amazon_csv import enfileirar_busca_csv_todas

    loja = await _loja_temu(db, make_user)
    auth_as(await make_user(permissions={"conciliacao": {"view": True, "edit": True}}))
    r = await client.post(f"/api/conciliacao/lojas/{CONTA}/buscar-transacoes")
    assert r.status_code == 200, r.text
    assert r.json()["enfileirado"] is True
    cmd_id = r.json()["comando_id"]
    cmd = await db.get(LogisticaRoboComando, __import__("uuid").UUID(cmd_id))
    assert cmd.acao == "temu_extrato" and cmd.logistica_id is None
    assert cmd.payload == {
        "loja": CONTA,
        "integration_id": str(loja.integration_id),
        "plataforma": "temu",
    }
    # de novo: já tem na fila, não duplica; e o cron da Amazon não cria nada pra Temu
    r = await client.post(f"/api/conciliacao/lojas/{CONTA}/buscar-transacoes")
    assert r.json()["enfileirado"] is False and r.json()["comando_id"] == cmd_id
    assert r.json()["comando_status"] == "pending" and r.json()["na_fila_desde"]
    assert await enfileirar_busca_csv_todas(db) == []
    assert await enfileirar_leitura_todas(db) == []
    # executor antigo (sem pedir a ação pelo nome) não leva; quem pede "temu_extrato" leva
    assert await logistica_robo.lease(db, limit=5) == []
    assert await logistica_robo.lease(db, limit=5, acoes=["amazon_transacoes_csv"]) == []
    leased = await logistica_robo.lease(db, limit=5, acoes=["temu_extrato"])
    assert [x["id"] for x in leased] == [cmd_id]
    # falhou → aparece na loja; a próxima leitura boa limpa
    await logistica_robo.registrar_resultado(
        db, cmd.id, status="failed", result="needs_manual_login: a Temu pediu login"
    )
    await db.refresh(loja)
    assert loja.ultimo_erro.startswith("robô: needs_manual_login")
    assert await enfileirar_leitura_todas(db) == [CONTA]
    # o executor manda o desfecho em JSON: na loja aparece só o motivo
    novo = (
        await db.execute(
            select(LogisticaRoboComando).where(LogisticaRoboComando.status == "pending")
        )
    ).scalar_one()
    await logistica_robo.registrar_resultado(
        db,
        novo.id,
        status="failed",
        result='{"ok": false, "reason": "o saldo da Temu mudou durante a leitura", "linhas": 16}',
    )
    await db.refresh(loja)
    assert loja.ultimo_erro == "robô: o saldo da Temu mudou durante a leitura"
    await ingerir_leitura(db, loja, _primeira_leitura(), agora=AGORA)
    assert loja.ultimo_erro is None


async def test_rodada_da_loja_da_temu_nao_pede_cliente_nem_le_mercado_pago(
    db, make_user, monkeypatch
):
    from contextlib import asynccontextmanager

    from app.services import conciliacao_jobs

    loja = await _loja_temu(db, make_user)
    await ingerir_leitura(db, loja, _primeira_leitura(), agora=AGORA)
    await db.commit()

    @asynccontextmanager
    async def _mesma_sessao():
        yield db

    async def _nao_pode(*a, **k):
        raise AssertionError("a loja da Temu não tem cliente de API nem extrato do Mercado Pago")

    monkeypatch.setattr(conciliacao_jobs, "session_scope", _mesma_sessao)
    monkeypatch.setattr(conciliacao_jobs, "_cliente", _nao_pode)
    monkeypatch.setattr(conciliacao_jobs, "run_extrato_sync", _nao_pode)
    r = await conciliacao_jobs.sincronizar_loja(loja.integration_id)
    assert r["loja"] == CONTA
    assert r["extrato"] == {"modo": "robo", "pulado": True}
    assert r["recalculo"]["pedidos"] == 5 and "erro" not in r["recalculo"]
    await db.refresh(loja)
    assert loja.ultimo_erro is None


# ---------- a migration ----------

_MIGRATION = (
    Path(__file__).resolve().parent.parent / "alembic" / "versions" / "0495_conciliacao_temu.py"
)


def _carregar():
    spec = importlib.util.spec_from_file_location("migration_0495", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def test_0495_liga_a_eron_numa_ancora_arquivada_e_o_downgrade_desfaz(db, make_user):
    mod = _carregar()
    assert mod.revision == "0495_conciliacao_temu"
    assert mod.down_revision == "0494_reembolso_cliente_atendimento"
    schema = Base.metadata.schema
    dono = await make_user()
    for nome in ("eron", "kia"):
        db.add(
            Integration(
                user_id=dono.id,
                platform=IntegrationPlatform.ML,
                name=nome,
                status="active",
                credentials=encrypt_json({"access_token": "tok"}),
            )
        )
    await db.commit()

    def _rodar(conn, passo: str) -> None:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            getattr(mod, passo)()

    mod.SCHEMA = schema
    # o schema de teste nasce dos modelos (já com a coluna e a tabela): tira pra migration
    # criar de verdade
    await db.execute(text(f'DROP TABLE "{schema}".conciliacao_previstos'))
    await db.execute(text(f'ALTER TABLE "{schema}".conciliacao_lojas DROP COLUMN bling_loja'))
    await db.commit()
    try:
        await (await db.connection()).run_sync(_rodar, "upgrade")
        await db.commit()
        loja = (
            await db.execute(
                text(
                    f"SELECT c.conta, c.platform::text AS platform, c.ativa, c.inicio,"
                    f" c.modo_extrato, c.bling_loja, i.name, i.status, i.archived_at,"
                    f" i.user_id, i.store_id, i.bling_loja_id, length(i.credentials) AS cred"
                    f' FROM "{schema}".conciliacao_lojas c'
                    f' JOIN "{schema}".integrations i ON i.id = c.integration_id'
                )
            )
        ).one()
        assert (loja.conta, loja.platform, loja.ativa, loja.inicio) == (
            "eron (temu)",
            "temu",
            True,
            date(2026, 10, 24),
        )
        assert (loja.modo_extrato, loja.bling_loja) == ("upload", "206288707")
        # a âncora: arquivada desde o nascimento, sem credencial, sem loja, e com um nome
        # que não é o de nenhuma loja ("eron") — não casa por nome com nada
        assert (loja.status, loja.cred, loja.store_id, loja.bling_loja_id) == (
            "ancora",
            0,
            None,
            None,
        )
        assert loja.archived_at is not None and loja.user_id == dono.id
        assert loja.name != "eron" and "temu" in loja.name
        # o nº da loja na Temu já nasce gravado: leitura de outra loja é recusada
        identidade = (
            await db.execute(
                text(
                    f"SELECT detalhe->>'mall_id' FROM \"{schema}\".conciliacao_sync"
                    " WHERE origem = 'temu_extrato' AND chave = 'identidade'"
                )
            )
        ).scalar_one()
        assert identidade == "635517739909443"
        # rodar de novo não duplica
        await db.execute(text(f'DROP TABLE "{schema}".conciliacao_previstos'))
        await db.execute(text(f'ALTER TABLE "{schema}".conciliacao_lojas DROP COLUMN bling_loja'))
        await (await db.connection()).run_sync(_rodar, "upgrade")
        await db.commit()
        assert (
            await db.execute(text(f'SELECT count(*) FROM "{schema}".conciliacao_lojas'))
        ).scalar_one() == 1
        assert (
            await db.execute(
                text(f"SELECT count(*) FROM \"{schema}\".integrations WHERE platform = 'temu'")
            )
        ).scalar_one() == 1

        await (await db.connection()).run_sync(_rodar, "downgrade")
        await db.commit()
        assert (
            await db.execute(text(f'SELECT count(*) FROM "{schema}".conciliacao_lojas'))
        ).scalar_one() == 0
        assert (
            await db.execute(text(f'SELECT count(*) FROM "{schema}".integrations'))
        ).scalar_one() == 2  # as duas do ML ficam
    finally:
        # devolve o schema ao formato dos modelos pros outros testes
        await db.rollback()
        conn = await db.connection()
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c,
                tables=[Base.metadata.tables[f"{schema}.conciliacao_previstos"]],
                checkfirst=True,
            )
        )
        await db.execute(
            text(
                f'ALTER TABLE "{schema}".conciliacao_lojas '
                "ADD COLUMN IF NOT EXISTS bling_loja varchar(40)"
            )
        )
        await db.commit()
        mod.SCHEMA = "davinci"
