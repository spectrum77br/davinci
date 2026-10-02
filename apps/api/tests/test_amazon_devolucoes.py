"""Devoluções da Amazon na Logística (02/10/2026, 701-7824777-7251447).

A API de pedidos não mostra devolução (o pedido segue "Entregue ao cliente");
o relatório GET_FLAT_FILE_RETURNS_DATA_BY_RETURN_DATE mostra. O que é garantido:
- o client pede o relatório com o tipo e o período e a lista de anúncios
  continua igual depois de separar o `baixar_relatorio`;
- o relatório vira o estado da devolução (solicitada / a caminho / recebida /
  reembolso sem devolução; recusada some) e a 4ª parte da assinatura, com a
  data do pedido de devolução e a Localização descrevendo a volta;
- a releitura da SP-API (enrich_row) não apaga a devolução;
- o rastreio da volta chega ao `devolucao_rastreio_sync` (Acompanhamento/17track);
- a varredura da Amazon manda as linhas que mudaram pras regras da aba Status;
- uma conta que falha não mexe nas linhas dela nem impede as outras.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.models import Integration, IntegrationPlatform
from app.security.cipher import encrypt_json
from app.services import amazon_devolucoes
from app.services.marketplaces.amazon import SP_API_BASE_NA, AmazonClient

pytestmark = pytest.mark.asyncio

BR = "A2Q3Y263D00KWC"

TSV = (
    "Order ID\tOrder date\tReturn request date\tReturn request status\tAmazon RMA ID\t"
    "Label type\tReturn carrier\tTracking ID\tBuyer name\tShip-to address\tASIN\tMerchant SKU\t"
    "Return quantity\tReturn Reason\tResolution\tReturn delivery date\tRefunded Amount\n"
    "701-7824777-7251447\t2026-09-22\t2026-09-29\tCompleted\tDZ1\tAmazonPrePaidLabel\tCorreios\t"
    "QB123BR\tThatiana Vila\tRua X, 10\tB0HH1B8CT2\tb081.24\t1\tNO_LONGER_NEEDED\t"
    "StandardRefund\t2026-10-01\t273.60\n"
)


def _creds() -> dict:
    return {
        "lwa_app_id": "amzn1.app.x",
        "lwa_client_secret": "secret",
        "refresh_token": "Atzr|...",
        "seller_id": "ASELLER123",
        "marketplace_id": BR,
        "access_token": "Atza|tok",
        "expires_at": int(time.time()) + 3600,
    }


async def test_baixar_relatorio_pede_tipo_e_periodo():
    client = AmazonClient(_creds())
    with respx.mock() as router:
        criar = router.post(SP_API_BASE_NA + "/reports/2021-06-30/reports").mock(
            return_value=httpx.Response(202, json={"reportId": "R1"})
        )
        router.get(SP_API_BASE_NA + "/reports/2021-06-30/reports/R1").mock(
            return_value=httpx.Response(
                200, json={"processingStatus": "DONE", "reportDocumentId": "D1"}
            )
        )
        router.get(SP_API_BASE_NA + "/reports/2021-06-30/documents/D1").mock(
            return_value=httpx.Response(200, json={"url": "https://s3.example/doc"})
        )
        router.get("https://s3.example/doc").mock(
            return_value=httpx.Response(200, content=TSV.encode())
        )
        texto = await client.baixar_relatorio(
            amazon_devolucoes.RELATORIO,
            data_inicio=datetime(2026, 9, 2, tzinfo=UTC),
            data_fim=datetime(2026, 10, 2, tzinfo=UTC),
            poll_interval=0,
        )
    corpo = json.loads(criar.calls[0].request.content)
    assert corpo == {
        "reportType": "GET_FLAT_FILE_RETURNS_DATA_BY_RETURN_DATE",
        "marketplaceIds": [BR],
        "dataStartTime": "2026-09-02T00:00:00+00:00",
        "dataEndTime": "2026-10-02T00:00:00+00:00",
    }
    assert texto.startswith("Order ID\t")


async def test_lista_de_anuncios_segue_pedindo_o_mesmo_relatorio():
    client = AmazonClient(_creds())
    tsv = (
        "seller-sku\titem-name\tasin1\tquantity\tstatus\tprice\n"
        "SKU1\tMala\tB01\t3\tActive\t10.00\n"
    )
    with respx.mock() as router:
        criar = router.post(SP_API_BASE_NA + "/reports/2021-06-30/reports").mock(
            return_value=httpx.Response(202, json={"reportId": "R2"})
        )
        router.get(SP_API_BASE_NA + "/reports/2021-06-30/reports/R2").mock(
            return_value=httpx.Response(
                200, json={"processingStatus": "DONE", "reportDocumentId": "D2"}
            )
        )
        router.get(SP_API_BASE_NA + "/reports/2021-06-30/documents/D2").mock(
            return_value=httpx.Response(200, json={"url": "https://s3.example/l"})
        )
        router.get("https://s3.example/l").mock(
            return_value=httpx.Response(200, content=tsv.encode())
        )
        linhas = [r async for r in client.list_listings(poll_interval=0)]
    assert json.loads(criar.calls[0].request.content) == {
        "reportType": "GET_MERCHANT_LISTINGS_ALL_DATA",
        "marketplaceIds": [BR],
    }
    assert [r["sku"] for r in linhas] == ["SKU1"]


# ── relatório → estado ─────────────────────────────────────────────────────

CAB = (
    "Order ID\tOrder date\tReturn request date\tReturn request status\tAmazon RMA ID\t"
    "Label type\tLabel cost\tReturn carrier\tTracking ID\tLabel to be paid by\tReturn Reason\t"
    "Resolution\tReturn delivery date\tRefunded Amount\n"
)


def _linha(pedido, pedida="29-Sep-2026", status="Approved", motivo="CR-UNWANTED_ITEM",
           resolucao="RefundAtFirstScan", rastreio="AD972335343BR", pagador="Customer",
           entregue="", reembolso="288.00"):
    return (
        f"{pedido}\t22-Sep-2026\t{pedida}\t{status}\tRMA1\tAmazonPrePaidLabel\t10.77\t"
        f"Correios\t{rastreio}\t{pagador}\t{motivo}\t{resolucao}\t{entregue}\t{reembolso}\n"
    )


RELATORIO_KFA = CAB + _linha("701-7824777-7251447") + _linha(
    "701-9108002-9567447", pedida="23-Sep-2026", motivo="AMZ-PG-BAD-DESC",
    resolucao="StandardRefund", rastreio="460120489", pagador="Seller", reembolso="",
)


def test_relatorio_vira_estado_da_devolucao():
    from app.models import DevolucaoRastreio

    devs = amazon_devolucoes.ler_relatorio(RELATORIO_KFA, "kfa")
    mala = devs["701-7824777-7251447"]
    assert mala.solicitada_em.isoformat() == "2026-09-29"
    # reembolsou no 1º escaneamento = já foi postada
    assert amazon_devolucoes.estado(mala, None) == "SHIPPED"
    outra = devs["701-9108002-9567447"]
    assert amazon_devolucoes.estado(outra, None) == "PENDING"
    # Correios já viram a volta (evento que não é pré-postagem) → a caminho
    lida = DevolucaoRastreio(pedido_bling="1", localizacao_auto="Objeto postado · Sorocaba/SP")
    assert amazon_devolucoes.estado(outra, lida) == "SHIPPED"
    so_etiqueta = DevolucaoRastreio(pedido_bling="1", localizacao_auto="Etiqueta emitida")
    assert amazon_devolucoes.estado(outra, so_etiqueta) == "PENDING"
    # 17track viu a volta entregue, ou a Amazon deu a data → recebida
    chegou = DevolucaoRastreio(pedido_bling="1", pacote_entregue_em=datetime.now(UTC))
    assert amazon_devolucoes.estado(outra, chegou) == "DELIVERED"
    entregue = amazon_devolucoes.ler_relatorio(
        CAB + _linha("701-1", entregue="01-Oct-2026"), "kfa"
    )["701-1"]
    assert amazon_devolucoes.estado(entregue, None) == "DELIVERED"
    recusada = amazon_devolucoes.ler_relatorio(CAB + _linha("701-2", status="Rejected"), "kfa")
    assert amazon_devolucoes.estado(recusada["701-2"], None) is None
    sem_pacote = amazon_devolucoes.ler_relatorio(
        CAB + _linha("701-3", resolucao="ReturnlessRefund", rastreio=""), "kfa"
    )
    assert amazon_devolucoes.estado(sem_pacote["701-3"], None) == "REFUND_ONLY"


def test_assinatura_e_textos_da_devolucao_amazon():
    from app.services import logistica_rules

    ms = {"order_status": "Shipped", "return_status": "PENDING"}
    assert logistica_rules.assinatura_para("Amazon", ms) == "Enviado | Devolução solicitada"
    ms["return_status"] = "SHIPPED"
    assert logistica_rules.assinatura_para("amazon", ms) == "Enviado | Devolução a caminho"
    ms["return_status"] = "DELIVERED"
    assert logistica_rules.assinatura_para("amazon", ms) == "Enviado | Devolução recebida"
    assert logistica_rules.devolucao_status_pt("amazon", ms) == "Devolução recebida"
    sd = {"return_status": {"em": "2026-10-01T15:00:00+00:00"}}
    assert logistica_rules.data_retorno_concluido("amazon", ms, sd) == sd["return_status"]["em"]


# ── na Logística ───────────────────────────────────────────────────────────


def _linha_logistica(pedido_mk, pedido_bling, **kw):
    from datetime import date

    from app.models import Logistica

    base = {
        "plataforma": "Amazon",
        "conta": "kfa",
        "pedido_bling": pedido_bling,
        "pedido_marketplace": pedido_mk,
        "meli_status": {"order_status": "Shipped", "fulfillment_channel": "MFN"},
        "status_bling": "Entregue",
        "data": date(2026, 9, 22),
        "localizacao": "Entregue → Piracicaba/SP",
    }
    base.update(kw)
    return Logistica(**base)


class _Relatorio:
    def __init__(self, tsv=None, erro=None):
        self.tsv, self.erro = tsv, erro

    async def baixar_relatorio(self, tipo, **kw):
        if self.erro:
            raise RuntimeError(self.erro)
        return self.tsv


async def _contas(db, make_user, nomes=("kfa",)):
    u = await make_user()
    for nome in nomes:
        db.add(Integration(
            user_id=u.id, platform=IntegrationPlatform.AMAZON, name=nome,
            credentials=encrypt_json(_creds()),
        ))
    await db.commit()


async def test_sincronizar_grava_devolucao_na_linha(db, make_user, monkeypatch):
    from app.services import logistica_rules

    await _contas(db, make_user)
    mala = _linha_logistica("701-7824777-7251447", "296700")
    outra = _linha_logistica("701-9108002-9567447", "296701")
    alheia = _linha_logistica("701-0000000-0000000", "296702")
    db.add_all([mala, outra, alheia])
    await db.commit()
    monkeypatch.setattr(
        amazon_devolucoes, "_build_amazon_client", lambda s, i, **kw: _Relatorio(RELATORIO_KFA)
    )

    r = await amazon_devolucoes.sincronizar(db)
    assert r["contas_ok"] == 1 and r["devolucoes"] == 2
    assert set(r["ids"]) == {mala.id, outra.id}
    for x in (mala, outra, alheia):
        await db.refresh(x)
    assert logistica_rules.assinatura_para("amazon", mala.meli_status) == (
        "Enviado | Devolução a caminho"
    )
    assert mala.meli_status["return_tracking"] == "AD972335343BR"
    assert mala.meli_status["return_reason"] == "Não é mais necessário"
    assert mala.meli_status["return_label_payer"] == "Cliente"
    assert mala.meli_status["return_refunded"] == "R$ 288,00"
    assert mala.localizacao == "Devolução a caminho · rastreio da volta AD972335343BR"
    assert logistica_rules.assinatura_para("amazon", outra.meli_status) == (
        "Enviado | Devolução solicitada"
    )
    assert outra.meli_status["return_reason"] == "Diferente da descrição do anúncio"
    assert outra.meli_status["return_resolution"] == "Quando o pacote chegar"
    # data OFICIAL: o dia do pedido de devolução (23/09, meio-dia em Brasília)
    carimbo = outra.status_datas["return_status"]
    assert carimbo["em"].startswith("2026-09-23T15:00") and carimbo["fonte"] == "plataforma"
    assert "return_status" not in (alheia.meli_status or {})
    # nada mudou → ninguém volta pras regras
    assert (await amazon_devolucoes.sincronizar(db))["ids"] == []


async def test_conta_que_falha_nao_mexe_e_nao_para_as_outras(db, make_user, monkeypatch):
    await _contas(db, make_user, ("kfa", "kia"))
    db.add(_linha_logistica("701-7824777-7251447", "296700"))
    await db.commit()

    def _build(s, integ, **kw):
        if integ.name == "kia":
            return _Relatorio(erro="amazon_create_report_failed status=403")
        return _Relatorio(RELATORIO_KFA)

    monkeypatch.setattr(amazon_devolucoes, "_build_amazon_client", _build)
    r = await amazon_devolucoes.sincronizar(db)
    assert (r["contas"], r["contas_ok"], len(r["ids"])) == (2, 1, 1)


async def test_releitura_da_sp_api_nao_apaga_a_devolucao(db):
    from app.services import logistica_amazon

    row = _linha_logistica(
        "701-7824777-7251447", "296700",
        meli_status={
            "order_status": "Shipped", "fulfillment_channel": "MFN",
            "return_status": "SHIPPED", "return_tracking": "AD972335343BR",
        },
        localizacao="Devolução a caminho · rastreio da volta AD972335343BR",
    )
    db.add(row)
    await db.commit()

    class _Api:
        async def get_order_status(self, order_id):
            return {"order_status": "Shipped", "fulfillment_channel": "MFN",
                    "ship_city": "Piracicaba", "ship_state": "SP"}

        async def get_buyer_cancel(self, order_id):
            return {"pedido": False, "motivo": None}

        async def get_easyship_tracking(self, order_id):
            return None

    await logistica_amazon.enrich_row(db, row, client_cache={"kfa": _Api()})
    assert row.meli_status["return_status"] == "SHIPPED"
    assert row.meli_status["return_tracking"] == "AD972335343BR"
    assert row.localizacao == "Devolução a caminho · rastreio da volta AD972335343BR"
    assert row.divergencia is None


async def test_rastreio_da_volta_chega_no_acompanhamento(db):
    from app.services import devolucao_rastreio_sync

    assert devolucao_rastreio_sync._plataforma_key("Amazon") == "amazon"
    row = _linha_logistica(
        "701-7824777-7251447", "296700",
        meli_status={
            "order_status": "Shipped", "return_status": "SHIPPED",
            "return_tracking": "AD972335343BR", "return_carrier": "Correios",
            "return_refunded": "R$ 288,00", "return_requested_at": "29/09/2026",
            "return_resolution": "Na postagem da volta",
        },
    )
    got = await devolucao_rastreio_sync._fetch_por_marketplace(db, "amazon", [row])
    info = got["296700"]
    assert (info.fonte, info.status, info.tracking, info.carrier) == (
        "amazon", "SHIPPED", "AD972335343BR", "Correios"
    )
    assert info.created_at.date().isoformat() == "2026-09-29"
    assert info.reembolso is True and str(info.reembolso_valor) == "288.00"
    assert info.entregue_em is None


async def test_varredura_da_amazon_manda_devolucoes_pras_regras(db, monkeypatch):
    from uuid import uuid4

    from app.services import logistica_amazon, logistica_ingest

    uma, outra = uuid4(), uuid4()

    async def _sweep(session):
        return {"ids": [uma]}

    async def _sync(session):
        return {"ids": [uma, outra]}

    aplicados = {}

    async def _aplicar(session, alvo, *, origem):
        aplicados.update(alvo)
        return {}

    monkeypatch.setattr(logistica_amazon, "sweep_pos_venda", _sweep)
    monkeypatch.setattr(amazon_devolucoes, "sincronizar", _sync)
    monkeypatch.setattr(logistica_ingest, "_enriquecer_e_aplicar", _aplicar)
    await logistica_ingest.sweeps_pos_venda(db, apenas="amazon")
    assert aplicados["amazon"] == [uma, outra]
