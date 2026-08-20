"""logistica_shopee — montagem da assinatura da Shopee (order_status) + rastreio
+ localização física da SPX + divergência a partir da API v2. Usa um client falso
(sem HTTP/DB) pra travar o mapeamento, o despacho por-plataforma e o cruzamento
comercial × físico."""

from __future__ import annotations

import pytest

from app.services import logistica_rules, logistica_shopee


class FakeShopee:
    """Client Shopee mínimo: só o que build_enrichment usa.
    `status_by_sn` = {order_sn: STATUS_UPPER} (ausente => pedido não devolvido);
    `tracking_by_sn` = {order_sn: tracking_number};
    `info_by_sn` = {order_sn: {logistics_status, tracking_info[]}}."""

    def __init__(
        self,
        status_by_sn: dict[str, str],
        tracking_by_sn: dict[str, str] | None = None,
        info_by_sn: dict[str, dict] | None = None,
    ):
        self._status = status_by_sn
        self._tracking = tracking_by_sn or {}
        self._info = info_by_sn or {}

    async def get_order_status_map(self, order_sns):
        out = {}
        for sn in order_sns:
            st = self._status.get(str(sn))
            if st:
                out[str(sn)] = {"status": st, "update_time": None}
        return out

    async def get_tracking_number(self, order_sn):
        return self._tracking.get(str(order_sn))

    async def get_tracking_info(self, order_sn):
        return self._info.get(str(order_sn), {})


@pytest.mark.asyncio
async def test_build_enrichment_completo():
    client = FakeShopee(
        {"2504ABC": "SHIPPED"},
        tracking_by_sn={"2504ABC": "BR263091239254C"},
        info_by_sn={
            "2504ABC": {
                "logistics_status": "LOGISTICS_PICKUP_DONE",
                "tracking_info": [
                    # fora de ordem de propósito: escolhe pelo maior update_time.
                    {"update_time": 100, "description": "Pedido em preparação"},
                    {"update_time": 200, "description": "Pedido postado Piracicaba - SP"},
                ],
            }
        },
    )
    enr = await logistica_shopee.build_enrichment(client, "2504ABC")
    assert enr == {
        "meli_status": {
            "order_status": "SHIPPED",
            "logistics_status": "LOGISTICS_PICKUP_DONE",
        },
        "rastreio": "BR263091239254C",
        "localizacao": "Pedido postado Piracicaba - SP",
        # O status físico é datado pelo último evento da SPX (este fake não data
        # o pedido, então order_status fica sem proposta). Ver
        # test_logistica_status_datas.
        "datas": {
            "logistics_status": {"em": "1970-01-01T00:03:20+00:00", "fonte": "plataforma"},
        },
    }


@pytest.mark.asyncio
async def test_build_enrichment_pedido_ausente_fica_vazio():
    client = FakeShopee({})  # Shopee não devolve nada
    enr = await logistica_shopee.build_enrichment(client, "2504XYZ")
    assert enr == {"meli_status": {}, "rastreio": None, "localizacao": None, "datas": {}}


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
    # A assinatura da Shopee ignora o logistics_status (não faz parte da chave).
    assert logistica_rules.assinatura_shopee(
        {"order_status": "COMPLETED", "logistics_status": "LOGISTICS_DELIVERY_DONE"}
    ) == "Concluído"


def test_divergencia_shopee_entregue_mas_cancelado():
    # SPX entregou, mas o pedido consta cancelamento/devolução: cliente recebeu.
    d = logistica_rules.detectar_divergencia_shopee(
        {"order_status": "TO_RETURN", "logistics_status": "LOGISTICS_DELIVERY_DONE"}
    )
    assert d is not None
    assert "Cliente recebeu" in d


def test_divergencia_shopee_concluido_mas_problema_fisico():
    d = logistica_rules.detectar_divergencia_shopee(
        {"order_status": "COMPLETED", "logistics_status": "LOGISTICS_LOST"},
        "Objeto extraviado",
    )
    assert d is not None
    assert "físico mostra problema" in d


def test_divergencia_shopee_sem_divergencia():
    # Concluído + entregue = batem => None.
    assert (
        logistica_rules.detectar_divergencia_shopee(
            {"order_status": "COMPLETED", "logistics_status": "LOGISTICS_DELIVERY_DONE"}
        )
        is None
    )
    # Sem sinal físico (só order_status) => None (conservador).
    assert logistica_rules.detectar_divergencia_shopee({"order_status": "CANCELLED"}) is None
    assert logistica_rules.detectar_divergencia_shopee({}) is None
    assert logistica_rules.detectar_divergencia_shopee(None) is None
