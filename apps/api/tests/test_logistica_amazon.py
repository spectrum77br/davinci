"""logistica_amazon — montagem da assinatura da Amazon (OrderStatus + EasyShip) +
localização proxy + divergência. Client falso (sem HTTP/DB) pra travar o
mapeamento, o despacho por-plataforma e o cruzamento comercial × físico."""

from __future__ import annotations

import pytest

from app.services import logistica_amazon, logistica_rules


class FakeAmazon:
    """Client Amazon mínimo: só o get_order_status que build_enrichment usa.
    `status_by_id` = {order_id: {order_status, easyship_status, ship_city,
    ship_state}} (ausente => None)."""

    def __init__(
        self,
        status_by_id: dict[str, dict],
        tracking_by_id: dict[str, str] | None = None,
        cancel_by_id: dict[str, dict | None] | None = None,
    ):
        self._status = status_by_id
        self._tracking = tracking_by_id or {}
        # Ausente = cliente não pediu; valor None = Amazon sem resposta.
        self._cancel = cancel_by_id or {}
        self.cancel_consultados: list[str] = []

    async def get_order_status(self, order_id):
        return self._status.get(str(order_id))

    async def get_buyer_cancel(self, order_id):
        self.cancel_consultados.append(str(order_id))
        if str(order_id) in self._cancel:
            return self._cancel[str(order_id)]
        return {"pedido": False, "motivo": None}

    async def get_easyship_tracking(self, order_id):
        return self._tracking.get(str(order_id))


@pytest.mark.asyncio
async def test_build_enrichment_completo():
    client = FakeAmazon(
        {
            "701-2862299-1963401": {
                "order_status": "Shipped",
                "easyship_status": "OutForDelivery",
                "ship_city": "São Paulo",
                "ship_state": "SP",
            }
        }
    )
    enr = await logistica_amazon.build_enrichment(client, "701-2862299-1963401")
    assert enr["meli_status"] == {
        "order_status": "Shipped",
        "easyship_status": "OutForDelivery",
    }
    # Sem papel de shipping aprovado a EasyShip devolve None → rastreio vazio.
    assert enr["rastreio"] is None
    # Localização proxy = easyship PT + destino.
    assert enr["localizacao"] == "Saiu p/ entrega → São Paulo/SP"


@pytest.mark.asyncio
async def test_build_enrichment_rastreio_easyship():
    # Com o papel de shipping aprovado, a Easy Ship API entrega o trackingId.
    client = FakeAmazon(
        {"701-2862299-1963401": {"order_status": "Shipped", "easyship_status": "PickedUp"}},
        tracking_by_id={"701-2862299-1963401": "TBA123456789BR"},
    )
    enr = await logistica_amazon.build_enrichment(client, "701-2862299-1963401")
    assert enr["rastreio"] == "TBA123456789BR"


@pytest.mark.asyncio
async def test_build_enrichment_sem_easyship_nem_endereco():
    client = FakeAmazon({"X": {"order_status": "Pending"}})
    enr = await logistica_amazon.build_enrichment(client, "X")
    assert enr["meli_status"] == {"order_status": "Pending"}
    assert enr["localizacao"] is None


@pytest.mark.asyncio
async def test_build_enrichment_preserva_afn_para_confirmacao_sem_mudar_assinatura():
    from app.services.logistica_bling import pacote_ainda_com_o_vendedor

    client = FakeAmazon({"X": {"order_status": "Shipped", "fulfillment_channel": "AFN"}})
    enr = await logistica_amazon.build_enrichment(client, "X")
    assert enr["meli_status"]["fulfillment_channel"] == "AFN"
    assert logistica_rules.assinatura_amazon(enr["meli_status"]) == "Enviado"
    assert pacote_ainda_com_o_vendedor("Amazon", enr["meli_status"]) is False


@pytest.mark.asyncio
async def test_amazon_sem_resposta_nao_vira_assinatura_vazia():
    """A conta kfa ficou com o token LWA vencido em 14/09/2026 e a SP-API passou
    a devolver 403. Antes, isso chegava aqui como `{}` e o chamador gravava a
    assinatura VAZIA por cima: o Status Plataforma desses pedidos ficava em
    branco no painel — dado bom destruído por uma API fora do ar. Agora a
    leitura sem resposta levanta erro e a linha fica como estava."""
    chamou_easyship = []

    class FakeAmazonMudo(FakeAmazon):
        async def get_easyship_tracking(self, order_id):
            chamou_easyship.append(order_id)
            return None

    client = FakeAmazonMudo({})
    with pytest.raises(logistica_amazon.AmazonSemRespostaError):
        await logistica_amazon.build_enrichment(client, "000")
    # E sai antes do EasyShip, que gastaria uma segunda chamada no mesmo 403.
    assert chamou_easyship == []


@pytest.mark.asyncio
async def test_build_enrichment_prazo_de_entrega_em_brasilia_e_entrega():
    # Pedido do print (15/09/2026): LatestDeliveryDate 2026-10-09T02:59:59Z =
    # 08/10 23:59 em Brasília = "Prazo para entrega: qui., 8 de out." no Seller Central.
    client = FakeAmazon(
        {
            "X": {
                "order_status": "Shipped",
                "fulfillment_channel": "MFN",
                "latest_delivery_date": "2026-10-09T02:59:59Z",
            },
            "Y": {"order_status": "Shipped", "easyship_status": "Delivered"},
        }
    )
    enr = await logistica_amazon.build_enrichment(client, "X")
    assert str(enr["prazo_entrega"]) == "2026-10-08"
    assert enr["entregue"] is False
    enr_y = await logistica_amazon.build_enrichment(client, "Y")
    assert enr_y["entregue"] is True and enr_y["prazo_entrega"] is None


def test_assinatura_amazon_traduz():
    m = {"order_status": "Shipped", "easyship_status": "Delivered"}
    assert logistica_rules.assinatura_amazon(m) == "Enviado | Entregue"
    # Só order_status.
    assert logistica_rules.assinatura_amazon({"order_status": "Canceled"}) == "Cancelado"
    assert logistica_rules.assinatura_amazon({}) == ""
    assert logistica_rules.assinatura_amazon(None) == ""


def test_assinatura_para_despacha_amazon():
    m = {"order_status": "Shipped", "easyship_status": "Delivered"}
    assert logistica_rules.assinatura_para("Amazon", m) == "Enviado | Entregue"


def test_divergencia_amazon_entregue_mas_cancelado():
    d = logistica_rules.detectar_divergencia_amazon(
        {"order_status": "Canceled", "easyship_status": "Delivered"}
    )
    assert d is not None
    assert "Cliente recebeu" in d


def test_divergencia_amazon_enviado_mas_problema():
    d = logistica_rules.detectar_divergencia_amazon(
        {"order_status": "Shipped", "easyship_status": "ReturnedToSeller"}
    )
    assert d is not None
    assert "físico mostra problema" in d


def test_divergencia_amazon_sem_easyship():
    # Sem easyship (sinal físico) => None.
    assert logistica_rules.detectar_divergencia_amazon({"order_status": "Canceled"}) is None
    # Batem (entregue + shipped) => None.
    assert (
        logistica_rules.detectar_divergencia_amazon(
            {"order_status": "Shipped", "easyship_status": "Delivered"}
        )
        is None
    )
    assert logistica_rules.detectar_divergencia_amazon(None) is None


# ── sweep_pos_venda ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sweep_pos_venda_re_olha_nao_finais_e_devolve_mudados(db, monkeypatch):
    """Regressão dos ~28 pedidos 701/702-* presos em "Coletado" (02-03/set):
    linha escondida como resolvida nunca mais era consultada e a ENTREGA ficava
    invisível. O sweep re-olha só os estados NÃO-finais (poupa a cota SP-API),
    atualiza o meli_status e devolve os ids de quem mudou de assinatura."""
    from datetime import date

    from app.models import Logistica

    hoje = date.today()
    r_mudou = Logistica(
        plataforma="Amazon", conta="kfa", pedido_bling="291244",
        pedido_marketplace="702-0000001-0000001",
        meli_status={"order_status": "Shipped", "easyship_status": "PickedUp"},
        status_bling="Em andamento", data=hoje,
    )
    r_final = Logistica(
        plataforma="Amazon", conta="kfa", pedido_bling="291245",
        pedido_marketplace="702-0000002-0000002",
        meli_status={"order_status": "Shipped", "easyship_status": "Delivered"},
        status_bling="Entregue", data=hoje,
    )
    r_igual = Logistica(
        plataforma="Amazon", conta="kfa", pedido_bling="291246",
        pedido_marketplace="702-0000003-0000003",
        meli_status={"order_status": "Shipped", "easyship_status": "PickedUp"},
        status_bling="Em andamento", data=hoje,
    )
    db.add_all([r_mudou, r_final, r_igual])
    await db.commit()
    ids = {"mudou": r_mudou.id, "final": r_final.id, "igual": r_igual.id}

    consultados: list[str] = []

    class _SpyAmazon(FakeAmazon):
        async def get_order_status(self, order_id):
            consultados.append(str(order_id))
            return await super().get_order_status(order_id)

    fake = _SpyAmazon(
        {
            "702-0000001-0000001": {"order_status": "Shipped", "easyship_status": "Delivered"},
            "702-0000003-0000003": {"order_status": "Shipped", "easyship_status": "PickedUp"},
        }
    )

    async def _integ(session, conta):
        return object()  # qualquer não-None: conta "tem integração"

    def _build(session, integ, *, lock=None):
        return fake

    monkeypatch.setattr(logistica_amazon, "_amazon_integration_for_conta", _integ)
    monkeypatch.setattr(logistica_amazon, "_build_amazon_client", _build)

    out = await logistica_amazon.sweep_pos_venda(db)

    # O Delivered (estado final) não gastou chamada de API.
    assert sorted(consultados) == ["702-0000001-0000001", "702-0000003-0000003"]
    # Só quem trocou de assinatura volta como "mudado" (vira extra no
    # recarregar e fura o escondimento).
    assert out["ids"] == [ids["mudou"]]
    assert (out["seen"], out["checked"], out["changed"], out["failed"]) == (3, 2, 1, 0)
    await db.refresh(r_mudou)
    assert (r_mudou.meli_status or {}).get("easyship_status") == "Delivered"


# ── gatilho "Shipped" → reler o Bling agora ─────────────────────────────


def _linha_enrich(**kw):
    """Linha Amazon já lida no Bling (carimbo preenchido) — o enrich_row da
    SP-API decide se zera o carimbo pra o Bling ser relido na mesma rodada."""
    from datetime import UTC, date, datetime, timedelta

    from app.models import Logistica

    base = {
        "plataforma": "Amazon",
        "conta": "kfa",
        "pedido_bling": "298196",
        "pedido_marketplace": "702-0000009-0000009",
        "meli_status": {"order_status": "Unshipped", "fulfillment_channel": "MFN"},
        "status_bling": "Em andamento",
        "data": date.today(),
        "bling_enriquecido_em": datetime.now(UTC) - timedelta(minutes=5),
    }
    base.update(kw)
    return Logistica(**base)


async def _enrich_com(db, row, status: dict):
    db.add(row)
    await db.commit()
    fake = FakeAmazon({row.pedido_marketplace: status})
    await logistica_amazon.enrich_row(db, row, client_cache={"kfa": fake})
    await db.commit()
    return row


@pytest.mark.asyncio
async def test_unshipped_para_shipped_sem_rastreio_zera_carimbo_do_bling(db):
    """Pedidos 298196/298281 (21/09): etiquetados juntos às 08:49, um ganhou o
    rastreio às 08:57 e o outro só às 09:22 — o Bling é relido de hora em hora
    por linha. Ao ver o "Shipped" chegar, o carimbo é zerado e o enrich do
    Bling da MESMA rodada copia o código."""
    row = await _enrich_com(
        db, _linha_enrich(), {"order_status": "Shipped", "fulfillment_channel": "MFN"}
    )
    assert row.amazon_canal == "proprio"
    assert row.bling_enriquecido_em is None


@pytest.mark.asyncio
async def test_shipped_para_shipped_mantem_carimbo(db):
    row = await _enrich_com(
        db,
        _linha_enrich(meli_status={"order_status": "Shipped", "fulfillment_channel": "MFN"}),
        {"order_status": "Shipped", "fulfillment_channel": "MFN"},
    )
    assert row.bling_enriquecido_em is not None


@pytest.mark.asyncio
async def test_shipped_com_rastreio_correios_mantem_carimbo(db):
    # Já tem o …BR: não há o que buscar no Bling.
    row = await _enrich_com(
        db,
        _linha_enrich(rastreio="AD912266053BR"),
        {"order_status": "Shipped", "fulfillment_channel": "MFN"},
    )
    assert row.bling_enriquecido_em is not None


@pytest.mark.asyncio
async def test_shipped_dba_mantem_carimbo(db):
    # DBA: a Amazon entrega; o rastreio nunca vem do Bling.
    row = await _enrich_com(
        db,
        _linha_enrich(meli_status={"order_status": "Unshipped"}),
        {"order_status": "Shipped", "easyship_status": "PendingPickUp"},
    )
    assert row.amazon_canal == "dba"
    assert row.bling_enriquecido_em is not None


# ── pedido de cancelamento do cliente (23/09/2026) ──────────────────────


_PEDIU = {"pedido": True, "motivo": "Outro"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "assinatura"),
    [
        # Ainda sem nota: dá pra cancelar sem afetar a métrica.
        ({"order_status": "Unshipped", "fulfillment_channel": "MFN"},
         "Não enviado | Cliente pediu cancelamento"),
        # Envio próprio já despachado — o 701-0809246-1772255 do print.
        ({"order_status": "Shipped", "fulfillment_channel": "MFN"},
         "Enviado | Cliente pediu cancelamento"),
        # DBA com nota emitida, motorista ainda não buscou.
        ({"order_status": "Shipped", "easyship_status": "PendingPickUp"},
         "Enviado | Aguardando coleta | Cliente pediu cancelamento"),
    ],
)
async def test_cliente_pediu_cancelamento_entra_na_assinatura(status, assinatura):
    client = FakeAmazon({"X": status}, cancel_by_id={"X": _PEDIU})
    enr = await logistica_amazon.build_enrichment(client, "X")
    assert enr["meli_status"]["buyer_cancel"] == "Requested"
    assert enr["meli_status"]["buyer_cancel_reason"] == "Outro"
    assert logistica_rules.assinatura_amazon(enr["meli_status"]) == assinatura
    detalhe = logistica_rules.detalhe_para("Amazon", enr["meli_status"])
    assert {"campo": "buyer_cancel", "rotulo": "Cancelamento",
            "valor": "Cliente pediu cancelamento"} in detalhe
    assert {"campo": "buyer_cancel_reason", "rotulo": "Motivo do cliente",
            "valor": "Outro"} in detalhe


@pytest.mark.asyncio
async def test_sem_pedido_de_cancelamento_assinatura_nao_muda():
    client = FakeAmazon({"X": {"order_status": "Shipped", "fulfillment_channel": "MFN"}})
    enr = await logistica_amazon.build_enrichment(client, "X")
    assert "buyer_cancel" not in enr["meli_status"]
    assert logistica_rules.assinatura_amazon(enr["meli_status"]) == "Enviado"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        {"order_status": "Shipped", "easyship_status": "Delivered"},
        {"order_status": "Canceled"},
        {"order_status": "Pending"},
        {"order_status": "Shipped", "fulfillment_channel": "AFN"},
    ],
)
async def test_pedido_fora_de_jogo_nao_gasta_consulta_de_cancelamento(status):
    """Entregue, cancelado, não pago ou FBA: não há o que fazer com o pedido de
    cancelamento — e cada consulta gasta cota da SP-API."""
    client = FakeAmazon({"X": status}, cancel_by_id={"X": _PEDIU})
    enr = await logistica_amazon.build_enrichment(client, "X")
    assert client.cancel_consultados == []
    assert "buyer_cancel" not in enr["meli_status"]


@pytest.mark.asyncio
async def test_amazon_sem_resposta_mantem_o_pedido_de_cancelamento(db):
    """429/403 no getOrderItems não "desmarca" a linha — senão a assinatura
    trocava de volta e a regra da aba Status rodava à toa."""
    row = _linha_enrich(
        meli_status={
            "order_status": "Unshipped", "fulfillment_channel": "MFN",
            "buyer_cancel": "Requested", "buyer_cancel_reason": "Outro",
        },
    )
    db.add(row)
    await db.commit()
    fake = FakeAmazon(
        {row.pedido_marketplace: {"order_status": "Unshipped", "fulfillment_channel": "MFN"}},
        cancel_by_id={row.pedido_marketplace: None},
    )
    await logistica_amazon.enrich_row(db, row, client_cache={"kfa": fake})
    await db.commit()
    assert row.meli_status["buyer_cancel"] == "Requested"
    assert row.meli_status["buyer_cancel_reason"] == "Outro"

    # Com resposta, vale a Amazon (cliente desistiu do pedido → sai).
    fake._cancel = {}
    await logistica_amazon.enrich_row(db, row, client_cache={"kfa": fake})
    await db.commit()
    assert "buyer_cancel" not in row.meli_status


@pytest.mark.asyncio
async def test_sweep_devolve_linha_quando_o_cliente_pede_cancelamento(db, monkeypatch):
    """O pedido de cancelamento muda a assinatura → a linha volta do sweep de
    hora em hora e passa pelas regras da aba Status."""
    from datetime import date

    from app.models import Logistica

    row = Logistica(
        plataforma="Amazon", conta="kia", pedido_bling="299001",
        pedido_marketplace="701-0809246-1772255",
        meli_status={"order_status": "Shipped", "fulfillment_channel": "MFN"},
        status_bling="Em digitação", data=date.today(),
    )
    db.add(row)
    await db.commit()
    fake = FakeAmazon(
        {"701-0809246-1772255": {"order_status": "Shipped", "fulfillment_channel": "MFN"}},
        cancel_by_id={"701-0809246-1772255": _PEDIU},
    )

    async def _integ(session, conta):
        return object()

    monkeypatch.setattr(logistica_amazon, "_amazon_integration_for_conta", _integ)
    monkeypatch.setattr(
        logistica_amazon, "_build_amazon_client", lambda s, i, *, lock=None: fake
    )
    out = await logistica_amazon.sweep_pos_venda(db)
    assert out["ids"] == [row.id]
    await db.refresh(row)
    assert logistica_rules.assinatura_para("Amazon", row.meli_status) == (
        "Enviado | Cliente pediu cancelamento"
    )
