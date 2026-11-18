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
    saque_para_movimento,
    situacao_estranha_do_saque,
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
# saques no formato da Barbosa (18/11): a Temu saca a cada 14 dias
SAQUE_VELHO = "LWO0026110308407002700094840014"
SAQUE = "LWO0026111708406002980128860014"
FIM_DO_PERIODO = datetime(2026, 11, 17, 2, 59, 59, 999000, tzinfo=UTC)  # 16/11 23:59:59,999
COMECO_DO_SEGUINTE = datetime(2026, 11, 17, 3, 0, tzinfo=UTC)  # 17/11 00:00
SAQUE_CRIADO = datetime(2026, 11, 17, 8, 42, 7, tzinfo=UTC)  # 17/11 05:42


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


def _reserva(flow: str, saque: str, valor: int, quando: datetime, *, volta: bool = False) -> dict:
    """A linha de reserva que o saque deixa no extrato: sai no fim do período ("_in") e
    volta no começo do seguinte ("_off")."""
    c = valor if volta else -valor
    nome = "Released amount" if volta else "Reserve amount"
    texto = "Montante libertado" if volta else "Montante de reserva"
    ref = f"{saque}_{'off' if volta else 'in'}"
    return {
        "flow_id": flow,
        "mall_id": 635517739909443,
        "batch_id": "",
        "prod_amount": _v(0),
        "fee_amount": _v(0),
        "other_amount": _v(c),
        "total_amount": _v(c),
        "created_at": int(quando.timestamp() * 1000),
        "trans_sn": ref,
        "order_id": ref,
        "parent_order_sn": "",
        "biz_category": nome,
        "ori_biz_category": "Reserve",
        "sub_biz_category": nome,
        "biz_category_text": texto,
        "sub_biz_category_text": texto,
        "note": nome,
    }


def _saque(saque: str, valor: int, criado: datetime) -> dict:
    """Um saque da aba Pagamentos (`mall_withdraw_order_simple_volist`)."""
    ms = int(criado.timestamp() * 1000)
    return {
        "id": 899286,
        "mall_id": 635517739909443,
        "withdraw_order_id": saque,
        "amount": _v(valor),
        "currency": "BRL",
        "service_fee_amount": _v(0),
        "receive_amount": _v(0),
        "withdraw_type": 1,
        "business_type": 1,
        "status": 2,
        "status_time_map": {"CREATED": ms, "PROCESSING": ms + 8_745_000, "PAID": ms + 8_765_000},
        "created_at": ms,
        "bank_account_suffix": "5685",
        "is_refund": 0,
        "statement_begin_time": ms - 14 * 86_400_000 - 20_527_000,
        "statement_end_time": ms - 20_527_001,
        "source_id": "STC0026111708404002980406730014",
        "source_type": 1,
    }


def _ciclo_com_saque() -> dict:
    """Um período inteiro como o da Barbosa: abre com 57,40, entram 83,00 e 26,00
    (166,40); a Temu segura 78,50 de reserva, saca 87,90 e devolve a reserva; depois
    entra mais 26,00 → 104,50. O saque de 03/11 é anterior à primeira linha."""
    return _leitura(
        [
            _linha("PFI-1", PO_PAGO, 8300, datetime(2026, 11, 3, 14, 56, tzinfo=UTC)),
            _linha("PFI-2", PO_ESPERA, 2600, datetime(2026, 11, 11, 4, 47, tzinfo=UTC)),
            _reserva("PFI-3", SAQUE, 7850, FIM_DO_PERIODO),
            _reserva("PFI-4", SAQUE, 7850, COMECO_DO_SEGUINTE, volta=True),
            _linha("PFI-5", PO_SEM_DATA, 2600, datetime(2026, 11, 17, 17, 56, tzinfo=UTC)),
        ],
        [],
        total=10450,
        reserva=7850,
        saques=[
            _saque(SAQUE, 8790, SAQUE_CRIADO),
            _saque(SAQUE_VELHO, 51030, datetime(2026, 11, 3, 8, 42, tzinfo=UTC)),
        ],
    )


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
    devolvido = linha_para_movimento(
        _linha("PFI-4", None, 5000, quando, categoria="Payment Refund", texto="Saque devolvido")
    )
    assert (devolvido["tipo"], devolvido["classificacao"]) == ("outro", None)
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
    # a Margem não é tocada: nenhuma venda da Temu gravada lá (só as da Temu: rodando
    # junto com outros arquivos, o banco de teste guarda venda de outra plataforma)
    da_temu = select(MarketplaceOrderFinancial).where(
        MarketplaceOrderFinancial.platform == IntegrationPlatform.TEMU
    )
    assert (await db.execute(da_temu)).scalars().all() == []
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


# ---------- saque e reserva (18/11, Barbosa) ----------


def test_saque_e_reserva_viram_movimentos():
    s = saque_para_movimento(_saque(SAQUE, 19690, SAQUE_CRIADO))
    assert (s["tipo"], s["classificacao"], s["id_externo"], s["pagamento_id"]) == (
        "saque",
        "saque",
        f"saque:{SAQUE}",
        None,
    )
    assert (s["credito"], s["debito"], s["data"]) == (Decimal("0"), Decimal("196.90"), SAQUE_CRIADO)
    assert s["descricao"] == "Saque pro banco · conta final 5685 · período 03/11 a 16/11"
    # sem nº, sem valor ou sem data não é saque que dê pra gravar
    assert (
        saque_para_movimento({**_saque(SAQUE, 100, SAQUE_CRIADO), "withdraw_order_id": ""}) is None
    )
    assert saque_para_movimento({**_saque(SAQUE, 100, SAQUE_CRIADO), "amount": None}) is None
    assert saque_para_movimento({**_saque(SAQUE, 0, SAQUE_CRIADO)}) is None
    sem_data = {**_saque(SAQUE, 100, SAQUE_CRIADO), "created_at": None, "status_time_map": {}}
    assert saque_para_movimento(sem_data) is None
    assert situacao_estranha_do_saque(_saque(SAQUE, 100, SAQUE_CRIADO)) is None
    falhou = _saque(SAQUE, 100, SAQUE_CRIADO)
    falhou["status_time_map"]["FAILED"] = 1
    assert situacao_estranha_do_saque(falhou) == "FAILED"
    assert situacao_estranha_do_saque({**_saque(SAQUE, 100, SAQUE_CRIADO), "is_refund": 1})

    saida = linha_para_movimento(_reserva("PFI-3", SAQUE, 7850, FIM_DO_PERIODO))
    volta = linha_para_movimento(_reserva("PFI-4", SAQUE, 7850, COMECO_DO_SEGUINTE, volta=True))
    assert (saida["tipo"], saida["pagamento_id"], saida["debito"], saida["credito"]) == (
        "reserva_temu",
        SAQUE,
        Decimal("78.50"),
        Decimal("0"),
    )
    assert (volta["tipo"], volta["pagamento_id"], volta["credito"]) == (
        "reserva_temu",
        SAQUE,
        Decimal("78.50"),
    )
    # sem pedido e SEM classificação: quem decide se é "vai e volta" é o par, no recálculo
    assert saida["classificacao"] is None and saida["pedido_marketplace"] is None
    assert saida["descricao"] == "Reserva segurada no saque"
    assert volta["descricao"] == "Reserva devolvida ao saldo"
    # reserva que não cita saque: continua reserva, sem "pagamento" pra formar par
    solta = _reserva("PFI-9", SAQUE, 500, FIM_DO_PERIODO)
    solta["order_id"] = solta["trans_sn"] = "TT-029-1"
    solta = linha_para_movimento(solta)
    assert (solta["pagamento_id"], solta["descricao"]) == (
        None,
        "Reserva segurada · Montante de reserva",
    )


async def test_saque_vira_linha_o_saldo_fecha_e_a_reserva_vai_e_volta(
    db, make_user, auth_as, client
):
    from app.services.conciliacao_jobs import fechar_dias_prontos

    loja = await _loja_temu(db, make_user)
    r = await ingerir_leitura(db, loja, _ciclo_com_saque(), agora=AGORA + timedelta(days=1))
    await recalcular_loja(db, loja, agora=AGORA + timedelta(days=1))
    await db.commit()
    # o saque de 03/11 é anterior à primeira linha do extrato: passado, não entra
    assert r["saques"] == {"lidos": 2, "no_periodo": 1, "no_painel": 1}
    assert (r["linhas"], r["inseridos"]) == (5, 6) and "aviso" not in r
    assert loja.ultimo_erro is None
    movs = {
        m.id_externo: m
        for m in (
            await db.execute(
                select(ConciliacaoMovimento).where(
                    ConciliacaoMovimento.integration_id == loja.integration_id
                )
            )
        ).scalars()
    }
    assert set(movs) == {"PFI-1", "PFI-2", "PFI-3", "PFI-4", "PFI-5", f"saque:{SAQUE}"}
    # 57,40 → +83 → +26 → reserva −78,50 → +78,50 → saque −87,90 → +26 = saldo da tela
    assert [movs[k].saldo_apos for k in ("PFI-1", "PFI-2", "PFI-3", "PFI-4")] == [
        Decimal("140.40"),
        Decimal("166.40"),
        Decimal("87.90"),
        Decimal("166.40"),
    ]
    saque = movs[f"saque:{SAQUE}"]
    assert (saque.tipo, saque.classificacao, saque.debito, saque.saldo_apos) == (
        "saque",
        "saque",
        Decimal("87.90"),
        Decimal("78.50"),
    )
    assert saque.data == SAQUE_CRIADO and saque.pedido_bling is None
    assert movs["PFI-5"].saldo_apos == Decimal("104.50")
    # o par (saída e volta do mesmo saque) fechou: "Reserva (vai e volta)"
    assert (movs["PFI-3"].tipo, movs["PFI-3"].classificacao) == ("reserva_temu", "reserva")
    assert movs["PFI-4"].classificacao == "reserva"

    auth_as(await make_user(permissions={"conciliacao": {"view": True, "edit": True}}))
    r16 = await client.get("/api/conciliacao/extrato", params={"loja": CONTA, "dia": "2026-11-16"})
    assert r16.status_code == 200, r16.text
    t = r16.json()["totais"]
    assert (t["saldo_inicial"], t["saldo_final"], t["diferenca"]) == (166.4, 87.9, 0.0)
    r17 = await client.get("/api/conciliacao/extrato", params={"loja": CONTA, "dia": "2026-11-17"})
    t = r17.json()["totais"]
    assert (t["saldo_inicial"], t["saldo_final"], t["saldo_informado"], t["diferenca"]) == (
        87.9,
        104.5,
        104.5,
        0.0,
    )
    linhas = {m["tipo"]: m for m in r17.json()["movimentos"]}
    assert set(linhas) == {"reserva_temu", "saque", "venda_liberada"}
    assert (linhas["saque"]["status"], linhas["saque"]["debito"]) == ("classificado", 87.9)
    # par fechado: a tela pode esconder (não é dinheiro retido)
    volta = linhas["reserva_temu"]
    assert (volta["status"], volta["reserva_aberta"], volta["credito"]) == (
        "classificado",
        False,
        78.5,
    )
    dias = await fechar_dias_prontos(db, loja, maximo_dias=3650)
    await db.commit()
    assert {"2026-11-16", "2026-11-17"} <= set(dias["fechados"])

    # a mesma tela lida de novo: nada duplica, nada é reescrito
    r = await ingerir_leitura(db, loja, _ciclo_com_saque(), agora=AGORA + timedelta(days=2))
    await db.commit()
    assert (r["inseridos"], r["ja_existiam"]) == (0, 6) and "aviso" not in r


async def test_reserva_que_so_saiu_fica_sem_dono_ate_a_volta_chegar(db, make_user, auth_as, client):
    from app.services.conciliacao_jobs import fechar_dias_prontos

    loja = await _loja_temu(db, make_user)
    # 57,40 + 83,00 = 140,40; a Temu segurou 78,50, sacou 61,90 e NÃO devolveu a reserva
    so_saida = _leitura(
        [
            _linha("PFI-1", PO_PAGO, 8300, datetime(2026, 11, 3, 14, 56, tzinfo=UTC)),
            _reserva("PFI-3", SAQUE, 7850, FIM_DO_PERIODO),
        ],
        [],
        total=0,
        saques=[_saque(SAQUE, 6190, SAQUE_CRIADO)],
    )
    await ingerir_leitura(db, loja, so_saida, agora=AGORA + timedelta(days=1))
    await recalcular_loja(db, loja, agora=AGORA + timedelta(days=1))
    await db.commit()
    saida = (
        await db.execute(
            select(ConciliacaoMovimento)
            .where(ConciliacaoMovimento.id_externo == "PFI-3")
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert (saida.tipo, saida.classificacao) == ("reserva_temu", None)
    auth_as(await make_user(permissions={"conciliacao": {"view": True, "edit": True}}))
    r = await client.get("/api/conciliacao/extrato", params={"loja": CONTA, "dia": "2026-11-16"})
    linha = next(m for m in r.json()["movimentos"] if m["tipo"] == "reserva_temu")
    # dinheiro retido: aparece sempre e o dia não fecha sozinho
    assert (linha["status"], linha["reserva_aberta"]) == ("pendente", True)
    dias = await fechar_dias_prontos(db, loja, maximo_dias=3650)
    await db.commit()
    assert "2026-11-16" not in dias["fechados"]

    # a volta chega numa leitura seguinte: aí o par fecha
    com_volta = _leitura(
        [
            *so_saida["extrato"],
            _reserva("PFI-4", SAQUE, 7850, datetime(2026, 11, 18, 3, 0, tzinfo=UTC), volta=True),
        ],
        [],
        total=7850,
        saques=so_saida["saques"],
    )
    r2 = await ingerir_leitura(db, loja, com_volta, agora=AGORA + timedelta(days=2))
    await recalcular_loja(db, loja, agora=AGORA + timedelta(days=2))
    await db.commit()
    assert r2["inseridos"] == 1 and "aviso" not in r2
    par = (
        (
            await db.execute(
                select(ConciliacaoMovimento.classificacao)
                .where(ConciliacaoMovimento.tipo == "reserva_temu")
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    assert par == ["reserva", "reserva"]


async def test_leitura_com_saque_pela_metade_e_recusada_inteira(db, make_user):
    loja = await _loja_temu(db, make_user)

    async def _gravadas() -> int:
        return len((await db.execute(select(ConciliacaoMovimento.id))).scalars().all())

    # o extrato cita um saque que a aba Pagamentos não trouxe (e a reserva é de hoje)
    sem_o_saque = _ciclo_com_saque()
    sem_o_saque["saques"] = sem_o_saque["saques"][1:]
    sem_o_saque["saques_total"] = 1
    with pytest.raises(LeituraInvalidaError, match=f"cita o saque {SAQUE}"):
        await ingerir_leitura(db, loja, sem_o_saque, agora=AGORA)
    # saque sem data (a Temu mudou o formato): não dá pra saber se é do período
    ilegivel = _ciclo_com_saque()
    ilegivel["saques"][1] = {"withdraw_sn": "W1", "amount": _v(10000)}
    with pytest.raises(LeituraInvalidaError, match="1 saque\\(s\\) do período sem data"):
        await ingerir_leitura(db, loja, ilegivel, agora=AGORA)
    # saque do período com data mas sem valor
    sem_valor = _ciclo_com_saque()
    sem_valor["saques"][0]["amount"] = None
    with pytest.raises(LeituraInvalidaError, match="1 saque\\(s\\) do período sem data, valor"):
        await ingerir_leitura(db, loja, sem_valor, agora=AGORA)
    # o robô parou antes do fim da lista e nenhum saque lido é anterior ao extrato
    cortada = _ciclo_com_saque()
    cortada["saques"] = cortada["saques"][:1]
    cortada["saques_total"] = 30
    with pytest.raises(LeituraInvalidaError, match="leitura incompleta dos saques"):
        await ingerir_leitura(db, loja, cortada, agora=AGORA)
    # saque de outra loja da Temu no meio
    de_outra = _ciclo_com_saque()
    de_outra["saques"][0]["mall_id"] = 111
    with pytest.raises(LeituraInvalidaError, match="mais de uma loja"):
        await ingerir_leitura(db, loja, de_outra, agora=AGORA)
    await db.rollback()
    await db.refresh(loja)
    assert await _gravadas() == 0
    assert (await db.execute(select(ConciliacaoPrevisto.id))).scalars().all() == []

    # lista cortada, mas já passou do começo do extrato (o saque de 03/11): serve. E o
    # saque ANTIGO num formato que o leitor não lê não trava a loja: é passado
    serve = _ciclo_com_saque()
    serve["saques_total"] = 30
    serve["saques"][1]["amount"] = None
    r = await ingerir_leitura(db, loja, serve, agora=AGORA + timedelta(days=1))
    await db.commit()
    assert r["saques"] == {"lidos": 2, "no_periodo": 1, "no_painel": 1}
    assert await _gravadas() == 6 and "aviso" not in r


async def test_saque_que_a_temu_ainda_nao_pagou_espera_e_o_devolvido_nao_e_gravado(db, make_user):
    loja = await _loja_temu(db, make_user)

    async def _gravadas() -> int:
        return len((await db.execute(select(ConciliacaoMovimento.id))).scalars().all())

    # 06:00 do dia do saque: a Temu criou o saque às 05:42 e ainda não pagou. Não se sabe
    # se o Saldo total da tela já desceu — a leitura espera
    criado = _ciclo_com_saque()
    criado["saques"][0]["status_time_map"] = {"CREATED": criado["saques"][0]["created_at"]}
    with pytest.raises(LeituraInvalidaError, match=f"o saque {SAQUE} ainda está em andamento"):
        await ingerir_leitura(db, loja, criado, agora=AGORA)
    # saque marcado como devolvido, ou numa situação nova: o leitor não adivinha o sinal
    devolvido = _ciclo_com_saque()
    devolvido["saques"][0]["is_refund"] = 1
    with pytest.raises(LeituraInvalidaError, match="situação que o leitor não conhece"):
        await ingerir_leitura(db, loja, devolvido, agora=AGORA)
    falhou = _ciclo_com_saque()
    falhou["saques"][0]["status_time_map"]["FAILED"] = 1
    with pytest.raises(LeituraInvalidaError, match="não conhece \\(FAILED\\)"):
        await ingerir_leitura(db, loja, falhou, agora=AGORA)
    await db.rollback()
    await db.refresh(loja)
    assert await _gravadas() == 0

    # pago: entra. Se DEPOIS de gravado a Temu mudar a situação dele, só avisa — a volta
    # do dinheiro, se houver, vem em linha do extrato
    await ingerir_leitura(db, loja, _ciclo_com_saque(), agora=AGORA + timedelta(days=1))
    await db.commit()
    assert await _gravadas() == 6
    r = await ingerir_leitura(db, loja, devolvido, agora=AGORA + timedelta(days=2))
    await db.commit()
    assert r["inseridos"] == 0 and await _gravadas() == 6
    assert f"o saque {SAQUE}, já gravado, mudou de situação na Temu (devolvido)" in r["aviso"]

    # saque já gravado que some da aba Pagamentos: a lista veio pela metade
    sumiu = _ciclo_com_saque()
    sumiu["saques"] = sumiu["saques"][1:]
    sumiu["saques_total"] = 1
    sumiu["extrato"] = [x for x in sumiu["extrato"] if x["flow_id"] not in ("PFI-3", "PFI-4")]
    sumiu["extrato_total"] = 3
    with pytest.raises(LeituraInvalidaError, match="linha\\(s\\) já gravada"):
        await ingerir_leitura(db, loja, sumiu, agora=AGORA + timedelta(days=3))
    sumiu = _ciclo_com_saque()
    sumiu["saques"] = sumiu["saques"][1:]
    sumiu["saques_total"] = 1
    # (a reserva que cita o saque já tem mais de 48 h: sozinha ela só avisaria)
    with pytest.raises(LeituraInvalidaError, match=f"não traz o saque {SAQUE}, que já"):
        await ingerir_leitura(db, loja, sumiu, agora=AGORA + timedelta(days=5))


async def test_o_saque_chega_numa_leitura_depois_e_a_reserva_cai_no_dia_anterior(
    db, make_user, auth_as, client
):
    """O caso real: a leitura das 06:00 de um dia pega as vendas; a do dia seguinte traz o
    saque e o par de reserva, com a saída DATADA do dia anterior (23:59:59,999)."""
    loja = await _loja_temu(db, make_user)
    inteiro = _ciclo_com_saque()
    antes = _leitura(inteiro["extrato"][:2], [], total=16640, saques=inteiro["saques"][1:])
    r1 = await ingerir_leitura(db, loja, antes, agora=datetime(2026, 11, 16, 9, 0, tzinfo=UTC))
    await recalcular_loja(db, loja, agora=datetime(2026, 11, 16, 9, 0, tzinfo=UTC))
    await db.commit()
    assert (r1["inseridos"], r1["saques"]["no_painel"]) == (2, 0) and "aviso" not in r1
    r2 = await ingerir_leitura(db, loja, inteiro, agora=AGORA + timedelta(days=1))
    await recalcular_loja(db, loja, agora=AGORA + timedelta(days=1))
    await db.commit()
    assert (r2["inseridos"], r2["ja_existiam"]) == (4, 2) and "aviso" not in r2
    saldos = {
        m.id_externo: m.saldo_apos
        for m in (await db.execute(select(ConciliacaoMovimento))).scalars()
    }
    # as linhas da 1ª leitura não foram reescritas e a corrente continua nelas
    assert saldos == {
        "PFI-1": Decimal("140.40"),
        "PFI-2": Decimal("166.40"),
        "PFI-3": Decimal("87.90"),
        "PFI-4": Decimal("166.40"),
        f"saque:{SAQUE}": Decimal("78.50"),
        "PFI-5": Decimal("104.50"),
    }
    auth_as(await make_user(permissions={"conciliacao": {"view": True, "edit": True}}))
    for dia, inicial, final in (("2026-11-16", 166.4, 87.9), ("2026-11-17", 87.9, 104.5)):
        t = (
            await client.get("/api/conciliacao/extrato", params={"loja": CONTA, "dia": dia})
        ).json()["totais"]
        assert (t["saldo_inicial"], t["saldo_final"], t["diferenca"]) == (inicial, final, 0.0)


async def test_reserva_que_cita_saque_fora_da_lista_so_espera_48_horas(db, make_user):
    loja = await _loja_temu(db, make_user)
    # o par cita um saque que a aba Pagamentos nunca mostrou (outro tipo de saque?); o
    # Saldo total não desceu: 57,40 + 83,00 = 140,40
    orfa = _leitura(
        [
            _linha("PFI-1", PO_PAGO, 8300, datetime(2026, 11, 3, 14, 56, tzinfo=UTC)),
            _reserva("PFI-3", SAQUE, 7850, FIM_DO_PERIODO),
            _reserva("PFI-4", SAQUE, 7850, COMECO_DO_SEGUINTE, volta=True),
        ],
        [],
        total=14040,
    )
    with pytest.raises(LeituraInvalidaError, match=f"cita o saque {SAQUE}"):
        await ingerir_leitura(db, loja, orfa, agora=COMECO_DO_SEGUINTE + timedelta(hours=47))
    await db.rollback()
    await db.refresh(loja)
    # depois de 48 h não trava mais a loja: grava e avisa
    r = await ingerir_leitura(db, loja, orfa, agora=COMECO_DO_SEGUINTE + timedelta(hours=49))
    await db.commit()
    assert r["inseridos"] == 3
    assert f"cita 1 saque(s) que a aba Pagamentos não mostra ({SAQUE})" in r["aviso"]
    assert loja.ultimo_erro.startswith("temu: o extrato cita 1 saque(s)")


async def test_saque_depois_da_ultima_linha_gravada_entra_mesmo_sem_reserva(db, make_user):
    loja = await _loja_temu(db, make_user)
    await ingerir_leitura(db, loja, _primeira_leitura(), agora=AGORA)  # termina em 252,00
    await db.commit()
    # 45 dias depois o extrato da tela está vazio (nenhuma linha em 30 dias) e a Temu
    # sacou 200,00 sem deixar reserva: o saque mais novo que a última linha gravada
    # entra; o de 03/11, mais velho que ela, não
    velho = _saque(SAQUE_VELHO, 51030, datetime(2026, 11, 3, 8, 42, tzinfo=UTC))
    saque = _saque(SAQUE, 20000, datetime(2026, 12, 20, 8, 42, tzinfo=UTC))
    vazia = _leitura([], [], total=5200, saques=[saque, velho])
    r = await ingerir_leitura(db, loja, vazia, agora=AGORA + timedelta(days=45))
    await db.commit()
    assert r["saques"] == {"lidos": 2, "no_periodo": 1, "no_painel": 1}
    assert r["inseridos"] == 1 and "aviso" not in r

    async def _saldo_do_saque() -> tuple:
        m = (
            await db.execute(
                select(ConciliacaoMovimento)
                .where(ConciliacaoMovimento.tipo == "saque")
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        return (m.debito, m.saldo_apos)

    assert await _saldo_do_saque() == (Decimal("200.00"), Decimal("52.00"))


async def test_saque_entre_a_ultima_linha_gravada_e_a_primeira_da_tela_entra(db, make_user):
    loja = await _loja_temu(db, make_user)
    await ingerir_leitura(db, loja, _primeira_leitura(), agora=AGORA)  # termina em 252,00
    await db.commit()
    # o robô ficou 60 dias sem ler: a única linha da tela (10/01) é mais nova que o saque
    # de 20/12, que não deixou reserva. Ele entra do mesmo jeito — senão o dia ficaria
    # com a diferença do saque e sem linha pra explicar
    com_linha = _leitura(
        [_linha("PFI-C", PO_ESPERA, 2600, datetime(2027, 1, 10, 15, 0, tzinfo=UTC))],
        [],
        total=7800,
        saques=[
            _saque(SAQUE, 20000, datetime(2026, 12, 20, 8, 42, tzinfo=UTC)),
            _saque(SAQUE_VELHO, 51030, datetime(2026, 11, 3, 8, 42, tzinfo=UTC)),
        ],
    )
    r = await ingerir_leitura(db, loja, com_linha, agora=AGORA + timedelta(days=60))
    await db.commit()
    assert (r["inseridos"], r["saques"]["no_painel"]) == (2, 1) and "aviso" not in r
    saldos = {
        m.id_externo: m.saldo_apos
        for m in (await db.execute(select(ConciliacaoMovimento))).scalars()
    }
    assert saldos[f"saque:{SAQUE}"] == Decimal("52.00") and saldos["PFI-C"] == Decimal("78.00")


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

    # a Temu tirou 100,00 do saldo sem linha nenhuma e sem saque na aba Pagamentos: a
    # linha nova termina no saldo novo e o dia dela NÃO fecha
    depois = AGORA + timedelta(days=2)
    com_saque = _leitura(
        [
            *_primeira_leitura()["extrato"],
            _linha("PFI-C", PO_ESPERA, 2600, datetime(2026, 11, 18, 19, 51, tzinfo=UTC)),
        ],
        [],
        total=1400 + 21200 + 2600 + 2600 - 10000,
    )
    r3 = await ingerir_leitura(db, loja, com_saque, agora=depois)
    await recalcular_loja(db, loja, agora=depois)
    await db.commit()
    assert r3["saques"]["lidos"] == 0
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


_MIGRATION_LOJAS = _MIGRATION.with_name("0501_conciliacao_temu_lojas.py")


async def test_0501_liga_barbosa_e_jlas_e_o_downgrade_deixa_a_eron(db, make_user):
    spec = importlib.util.spec_from_file_location("migration_0501", _MIGRATION_LOJAS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "0501_conciliacao_temu_lojas"
    assert mod.down_revision == "0500_organizar_ocultos"
    schema = Base.metadata.schema
    # a Eron já está no ar (0495) na âncora de um dono; outro usuário tem mais integrações
    eron = await _loja_temu(db, make_user)
    dono_da_eron = (await db.get(Integration, eron.integration_id)).user_id
    outro = await make_user()
    for nome in ("kia", "injox", "mega"):
        db.add(
            Integration(
                user_id=outro.id,
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
    try:
        await (await db.connection()).run_sync(_rodar, "upgrade")
        await db.commit()
        await (await db.connection()).run_sync(_rodar, "upgrade")  # de novo: não duplica
        await db.commit()
        lojas = (
            await db.execute(
                text(
                    f"SELECT c.conta, c.platform::text AS platform, c.ativa, c.inicio,"
                    f" c.modo_extrato, c.bling_loja, i.name, i.status, i.archived_at,"
                    f" i.user_id, length(i.credentials) AS cred,"
                    f" (SELECT s.detalhe->>'mall_id' FROM \"{schema}\".conciliacao_sync s"
                    f"   WHERE s.integration_id = i.id AND s.chave = 'identidade') AS mall_id"
                    f' FROM "{schema}".conciliacao_lojas c'
                    f' JOIN "{schema}".integrations i ON i.id = c.integration_id'
                    f" WHERE c.conta <> 'eron (temu)' ORDER BY c.conta"
                )
            )
        ).all()
        assert [(x.conta, x.bling_loja, x.mall_id) for x in lojas] == [
            ("barbosa (temu)", "205925390", "635517739923741"),
            ("jlas (temu)", "206288651", "635517739909677"),
        ]
        for x in lojas:
            assert (x.platform, x.ativa, x.inicio, x.modo_extrato) == (
                "temu",
                True,
                date(2026, 10, 24),
                "upload",
            )
            # âncora arquivada, sem credencial, do mesmo dono da âncora da Eron
            assert (x.status, x.cred, x.user_id) == ("ancora", 0, dono_da_eron)
            assert x.archived_at is not None and x.name == f"{x.conta} · só conciliação"

        await (await db.connection()).run_sync(_rodar, "downgrade")
        await db.commit()
        restam = (
            (await db.execute(text(f'SELECT conta FROM "{schema}".conciliacao_lojas')))
            .scalars()
            .all()
        )
        assert restam == ["eron (temu)"]
        assert (
            await db.execute(
                text(f"SELECT count(*) FROM \"{schema}\".integrations WHERE platform = 'temu'")
            )
        ).scalar_one() == 1
    finally:
        await db.rollback()
        mod.SCHEMA = "davinci"
