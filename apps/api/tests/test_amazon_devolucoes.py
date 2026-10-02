"""Teste do relatório de devoluções da Amazon (02/10/2026, 701-7824777-7251447).

O que é garantido aqui:
- o client pede o relatório com o tipo e o período (dataStartTime/dataEndTime)
  e a lista de anúncios continua igual depois de separar o `baixar_relatorio`;
- o log do teste não leva dado pessoal do comprador (nome, endereço…);
- uma conta recusada (403, sem o papel) não impede as outras.
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


def test_resumo_sem_dado_pessoal():
    r = amazon_devolucoes.resumir(TSV)
    assert r["total"] == 1
    assert "Buyer name" in r["colunas"]  # a coluna existe no relatório…
    (linha,) = r["linhas"]
    assert "Buyer name" not in linha and "Ship-to address" not in linha  # …mas não vai pro log
    assert "Thatiana" not in json.dumps(linha) and "Rua X" not in json.dumps(linha)
    assert linha["Order ID"] == "701-7824777-7251447"
    assert linha["Return Reason"] == "NO_LONGER_NEEDED"
    assert linha["Tracking ID"] == "QB123BR"
    assert linha["Refunded Amount"] == "273.60"


async def test_conta_recusada_nao_para_as_outras(db, make_user, monkeypatch):
    u = await make_user()
    for nome in ("kfa", "kia", "velha"):
        integ = Integration(
            user_id=u.id, platform=IntegrationPlatform.AMAZON, name=nome,
            credentials=encrypt_json(_creds()),
        )
        if nome == "velha":
            integ.archived_at = datetime.now(UTC)
        db.add(integ)
    await db.commit()

    class _Cliente:
        def __init__(self, nome: str):
            self.nome = nome

        async def baixar_relatorio(self, tipo, **kw):
            assert tipo == amazon_devolucoes.RELATORIO and kw["data_inicio"] < kw["data_fim"]
            if self.nome == "kfa":
                raise RuntimeError('amazon_create_report_failed status=403 body={"errors":[]}')
            return TSV

    monkeypatch.setattr(
        amazon_devolucoes, "_build_amazon_client", lambda s, integ: _Cliente(integ.name)
    )
    r = await amazon_devolucoes.testar_relatorio(db)
    assert [(x["conta"], x["ok"]) for x in r] == [("kfa", False), ("kia", True)]
    assert "status=403" in r[0]["erro"]
    assert r[1]["total"] == 1
