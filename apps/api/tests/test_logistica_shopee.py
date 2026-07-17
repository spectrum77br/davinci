"""logistica_shopee — montagem da assinatura da Shopee (order_status) a partir
da API v2 + tradução PT. Usa um client falso (sem HTTP/DB) pra travar o
mapeamento e o despacho por-plataforma da assinatura."""

from __future__ import annotations

import pytest

from app.services import logistica_rules, logistica_shopee


class FakeShopee:
    """Client Shopee mínimo: só o get_order_status_map que build_enrichment usa.
    `status_by_sn` = {order_sn: STATUS_UPPER}; ausente => pedido não devolvido."""

    def __init__(self, status_by_sn: dict[str, str]):
        self._status = status_by_sn

    async def get_order_status_map(self, order_sns):
        out = {}
        for sn in order_sns:
            st = self._status.get(str(sn))
            if st:
                out[str(sn)] = {"status": st, "update_time": None}
        return out


@pytest.mark.asyncio
async def test_build_enrichment_order_status():
    client = FakeShopee({"2504ABC": "COMPLETED"})
    enr = await logistica_shopee.build_enrichment(client, "2504ABC")
    assert enr == {"meli_status": {"order_status": "COMPLETED"}}


@pytest.mark.asyncio
async def test_build_enrichment_pedido_ausente_fica_vazio():
    client = FakeShopee({})  # Shopee não devolve o pedido
    enr = await logistica_shopee.build_enrichment(client, "2504XYZ")
    assert enr == {"meli_status": {}}


def test_assinatura_shopee_traduz():
    assert logistica_rules.assinatura_shopee({"order_status": "TO_RETURN"}) == "Devolução solicitada"
    assert logistica_rules.assinatura_shopee({"order_status": "CANCELLED"}) == "Cancelado"
    # Token desconhecido cai no próprio valor, humanizado.
    assert logistica_rules.assinatura_shopee({"order_status": "FOO_BAR"}) == "Foo Bar"
    assert logistica_rules.assinatura_shopee({}) == ""
    assert logistica_rules.assinatura_shopee(None) == ""


def test_assinatura_para_despacha_por_plataforma():
    shopee = {"order_status": "COMPLETED"}
    # Shopee usa o vocabulário próprio.
    assert logistica_rules.assinatura_para("Shopee", shopee) == "Concluído"
    # ML usa a assinatura de 8 campos (order_status "paid" traduz).
    ml = {"order_status": "paid", "ship_status": "shipped"}
    assert logistica_rules.assinatura_para("Mercado Livre", ml) == logistica_rules.assinatura_pt(ml)
    # Um payload Shopee lido como ML NÃO renderiza (order_status "COMPLETED" não
    # está no vocabulário do Meli) — prova que o despacho importa.
    assert logistica_rules.assinatura_pt(shopee) == "COMPLETED"
    assert logistica_rules.assinatura_para("Shopee", shopee) == "Concluído"
