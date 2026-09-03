from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, Margens
from app.routers import margens as margens_router

pytestmark = pytest.mark.asyncio


class FakeBlingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    async def update_order_situacao(self, bling_order_id: int, situacao_id: int) -> None:
        self.calls.append((bling_order_id, situacao_id))


async def _create_margem_with_order(
    db: AsyncSession,
    *,
    situacao: str = "15",
) -> tuple[Margens, BlingOrder]:
    margem = Margens(
        pedido_bling=123456,
        sku="sku-1",
        produtos="Produto teste",
        status="Pendente",
    )
    order = BlingOrder(
        bling_id=987654,
        numero="123456",
        item_codigo="sku-1",
        item_index=0,
        situacao=situacao,
    )
    db.add_all([margem, order])
    await db.commit()
    await db.refresh(margem)
    await db.refresh(order)
    return margem, order


def _margem_permissions() -> dict:
    return {"margem": {"view": True, "edit": True, "delete": False}}


async def test_list_margens_requires_view_permission(client, make_user, auth_as):
    user = await make_user(permissions={})
    auth_as(user)

    response = await client.get("/api/margens")

    assert response.status_code == 403


async def test_patch_margem_requires_edit_permission(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
):
    user = await make_user(permissions={"margem": {"view": True}})
    auth_as(user)
    margem, _order = await _create_margem_with_order(db)

    response = await client.patch(
        f"/api/margens/{margem.id}",
        json={"status": "Aprovado"},
    )

    assert response.status_code == 403


async def test_patch_margem_uses_global_bling_client_for_authorized_user(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
    monkeypatch,
):
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    margem, order = await _create_margem_with_order(db)
    fake_client = FakeBlingClient()

    async def fake_global_bling_client(session):
        return fake_client

    monkeypatch.setattr(
        margens_router,
        "_global_bling_client",
        fake_global_bling_client,
    )

    response = await client.patch(
        f"/api/margens/{margem.id}",
        json={"status": "Aprovado"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "Aprovado"
    assert fake_client.calls == [
        (987654, margens_router.SITUACAO_ATENDIDO),
        (987654, margens_router.SITUACAO_APROVADO),
    ]

    await db.refresh(order)
    assert order.status == "Aprovado"
    assert order.aprovado_por == user.id
    assert order.situacao == str(margens_router.SITUACAO_APROVADO)
    assert order.verificado is True


async def test_patch_margem_aprovado_from_atendido_skips_atendido_step(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
    monkeypatch,
):
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    margem, order = await _create_margem_with_order(
        db,
        situacao=str(margens_router.SITUACAO_ATENDIDO),
    )
    fake_client = FakeBlingClient()

    async def fake_global_bling_client(session):
        return fake_client

    monkeypatch.setattr(
        margens_router,
        "_global_bling_client",
        fake_global_bling_client,
    )

    response = await client.patch(
        f"/api/margens/{margem.id}",
        json={"status": "Aprovado"},
    )

    assert response.status_code == 200
    assert fake_client.calls == [(987654, margens_router.SITUACAO_APROVADO)]
    await db.refresh(order)
    assert order.situacao == str(margens_router.SITUACAO_APROVADO)


async def test_patch_margem_reprovado_when_situacao_not_em_aberto_skips_bling(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
    monkeypatch,
):
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    margem, order = await _create_margem_with_order(db, situacao="15")

    async def fail_if_called(session):
        raise AssertionError("Bling client should not be needed")

    monkeypatch.setattr(margens_router, "_global_bling_client", fail_if_called)

    response = await client.patch(
        f"/api/margens/{margem.id}",
        json={"status": "Reprovado"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "Reprovado"

    await db.refresh(order)
    assert order.status == "Reprovado"
    assert order.aprovado_por == user.id
    assert order.situacao == "15"
    assert order.verificado is True


async def test_patch_margem_reprovado_from_em_aberto_patches_bling(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
    monkeypatch,
):
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    margem, order = await _create_margem_with_order(
        db,
        situacao=str(margens_router.SITUACAO_APROVADO),
    )
    fake_client = FakeBlingClient()

    async def fake_global_bling_client(session):
        return fake_client

    monkeypatch.setattr(
        margens_router,
        "_global_bling_client",
        fake_global_bling_client,
    )

    response = await client.patch(
        f"/api/margens/{margem.id}",
        json={"status": "Reprovado"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "Reprovado"
    assert fake_client.calls == [(987654, margens_router.SITUACAO_REPROVADO)]

    await db.refresh(order)
    assert order.status == "Reprovado"
    assert order.aprovado_por == user.id
    assert order.situacao == str(margens_router.SITUACAO_REPROVADO)
    assert order.verificado is True


async def test_patch_margem_skips_bling_when_order_is_already_target_situacao(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
    monkeypatch,
):
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    margem, order = await _create_margem_with_order(
        db,
        situacao=str(margens_router.SITUACAO_APROVADO),
    )

    async def fail_if_called(session):
        raise AssertionError("Bling client should not be needed")

    monkeypatch.setattr(margens_router, "_global_bling_client", fail_if_called)

    response = await client.patch(
        f"/api/margens/{margem.id}",
        json={"status": "Aprovado"},
    )

    assert response.status_code == 200
    await db.refresh(order)
    assert order.status == "Aprovado"
    assert order.aprovado_por == user.id
    assert order.verificado is True


async def test_patch_margem_local_only_marks_order_verified_without_changing_situacao(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
    monkeypatch,
):
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    margem, order = await _create_margem_with_order(db, situacao="12")

    async def fail_if_called(session):
        raise AssertionError("Bling client should not be needed")

    monkeypatch.setattr(margens_router, "_global_bling_client", fail_if_called)

    response = await client.patch(
        f"/api/margens/{margem.id}",
        json={"status": "Aprovado", "local_only": True},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "Aprovado"
    await db.refresh(order)
    assert order.status == "Aprovado"
    assert order.aprovado_por == user.id
    assert order.situacao == "12"
    assert order.verificado is True


async def test_marketplace_status_updates_snapshot_without_view_refresh(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
):
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    order = BlingOrder(
        bling_id=987654,
        numero="123456",
        item_codigo="sku-1",
        item_index=0,
        situacao="15",
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    await db.execute(
        text(
            """
            INSERT INTO verificar_margem (
                bling_order_item_id, pedido_bling, bling_id, sku,
                bling_status_margem, verificado
            )
            VALUES (:id, '123456', 987654, 'sku-1', NULL, false)
            """
        ),
        {"id": str(order.id)},
    )
    await db.commit()

    response = await client.patch(
        "/api/margens/marketplace/status/123456",
        json={"status": "Aprovado", "sku": "sku-1", "local_only": True},
    )

    assert response.status_code == 200
    snapshot = (
        await db.execute(
            text(
                """
                SELECT bling_status_margem, aprovado_por::text AS aprovado_por, verificado
                FROM verificar_margem
                WHERE bling_order_item_id = CAST(:id AS uuid)
                """
            ),
            {"id": str(order.id)},
        )
    ).mappings().one()
    assert snapshot["bling_status_margem"] == "Aprovado"
    assert snapshot["aprovado_por"] == str(user.id)
    assert snapshot["verificado"] is True


async def test_marketplace_saldo_filter_uses_absolute_cent_threshold(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
):
    """The 'saldo' attention filter must flag ANY divergence above R$0,01,
    matching the per-row 'corrigir' marker in the UI
    (Math.abs(saldo_plataforma - saldo_bling) > 0.01).

    Regression: the filter used a relative 1% threshold, so a real R$60
    divergence on a R$7.000 item (0.85%) showed the marker in the detail but
    was filtered out of the 'saldo divergente' list. A high-value, still-pending
    order with a sub-1% (but >R$0,01) gap must now appear.

    Fixtures destes testes usam 'amazon' (plataforma ainda sujeita à triagem
    de saldo) — ML/Shopee/TikTok são ISENTAS do motivo saldo desde 01/09 (o
    saldo da plataforma é a fonte da verdade, real ou projetado); ver
    test_marketplace_saldo_isenta_ml_shopee_e_ancora_efetivo_na_plataforma.
    """
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    order = BlingOrder(
        bling_id=987654,
        numero="123456",
        item_codigo="sku-1",
        item_index=0,
        situacao="15",
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    # saldo_plataforma=7000, saldo_bling=6940 → R$60 gap = 0.857% (< 1%, > R$0,01).
    # Pending (bling_status_margem NULL) so it must show under status=Pendente.
    await db.execute(
        text(
            """
            INSERT INTO verificar_margem (
                bling_order_item_id, pedido_bling, bling_id, sku,
                situacao, situacao_nome, plataforma_bling, item_proportion,
                bling_valorbase_item, bling_custofrete_item, bling_taxacomissao_item,
                marketplace_liquido_base_margem_item,
                bling_status_margem
            )
            VALUES (
                :id, '123456', 987654, 'sku-1',
                '6', 'Em aberto', 'amazon', 1,
                6940, 0, 0,
                7000,
                NULL
            )
            """
        ),
        {"id": str(order.id)},
    )
    await db.commit()

    # Pending + saldo filter → the sub-1% (but > R$0,01) divergence must appear.
    response = await client.get(
        "/api/margens/marketplace?attention_type=saldo&status=Pendente"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["pedido_bling"] == "123456"
    assert body["items"][0]["attention_saldo"] is True


async def test_marketplace_saldo_filter_ignores_sub_cent_noise(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
):
    """A gap of R$0,01 or less is rounding noise, not a divergence — it must NOT
    appear in the 'saldo' filter (mirrors the UI's > 0.01 cutoff)."""
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    order = BlingOrder(
        bling_id=987655,
        numero="123457",
        item_codigo="sku-2",
        item_index=0,
        situacao="15",
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    # R$0,01 gap → not flagged (strictly greater-than).
    await db.execute(
        text(
            """
            INSERT INTO verificar_margem (
                bling_order_item_id, pedido_bling, bling_id, sku,
                situacao, situacao_nome, plataforma_bling, item_proportion,
                bling_valorbase_item, bling_custofrete_item, bling_taxacomissao_item,
                marketplace_liquido_base_margem_item,
                bling_status_margem
            )
            VALUES (
                :id, '123457', 987655, 'sku-2',
                '6', 'Em aberto', 'amazon', 1,
                100.00, 0, 0,
                100.01,
                NULL
            )
            """
        ),
        {"id": str(order.id)},
    )
    await db.commit()

    response = await client.get(
        "/api/margens/marketplace?attention_type=saldo&status=Pendente"
    )
    assert response.status_code == 200
    assert response.json()["total"] == 0


async def test_marketplace_saldo_filter_only_considers_shippable_situacoes(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
):
    """Saldo divergence is only triaged for orders in situação 6, 21 (Em digitação
    = etiqueta enviada) ou 83965 (legado). A real gap (R$60) on an order in any
    other situação must NOT appear in the 'saldo' filter, matching the per-row
    'corrigir' marker gate in the UI."""
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    order = BlingOrder(
        bling_id=987656,
        numero="123458",
        item_codigo="sku-3",
        item_index=0,
        situacao="9",
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    # Same R$60 gap as the passing test, but situação 9 (not 6/21/83965) → excluded.
    await db.execute(
        text(
            """
            INSERT INTO verificar_margem (
                bling_order_item_id, pedido_bling, bling_id, sku,
                situacao, situacao_nome, plataforma_bling, item_proportion,
                bling_valorbase_item, bling_custofrete_item, bling_taxacomissao_item,
                marketplace_liquido_base_margem_item,
                bling_status_margem
            )
            VALUES (
                :id, '123458', 987656, 'sku-3',
                '9', 'Atendido', 'amazon', 1,
                6940, 0, 0,
                7000,
                NULL
            )
            """
        ),
        {"id": str(order.id)},
    )
    await db.commit()

    # situacao=all: prova que é o gatilho de SALDO que exclui a situação 9,
    # não o filtro padrão da aba (que já esconde tudo fora de 6/21/83965).
    response = await client.get(
        "/api/margens/marketplace?attention_type=saldo&status=Pendente&situacao=all"
    )
    assert response.status_code == 200
    assert response.json()["total"] == 0


@pytest.mark.parametrize(
    ("situacao", "situacao_nome"),
    [("21", "Em digitação"), ("83965", "Enviado Etiqueta")],
    ids=["21-canonico", "83965-legado"],
)
async def test_marketplace_saldo_filter_includes_situacao_etiqueta(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
    situacao: str,
    situacao_nome: str,
):
    """Etiqueta enviada — 21 (Em digitação, canônico desde 03/09/2026) e 83965
    (Enviado Etiqueta, legado) — também conta como saldo-divergente: mesmo gap
    R$60 → deve aparecer no filtro 'saldo'."""
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    order = BlingOrder(
        bling_id=987657,
        numero="123459",
        item_codigo="sku-4",
        item_index=0,
        situacao=situacao,
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    await db.execute(
        text(
            """
            INSERT INTO verificar_margem (
                bling_order_item_id, pedido_bling, bling_id, sku,
                situacao, situacao_nome, plataforma_bling, item_proportion,
                bling_valorbase_item, bling_custofrete_item, bling_taxacomissao_item,
                marketplace_liquido_base_margem_item,
                bling_status_margem
            )
            VALUES (
                :id, '123459', 987657, 'sku-4',
                :situacao, :situacao_nome, 'amazon', 1,
                6940, 0, 0,
                7000,
                NULL
            )
            """
        ),
        {"id": str(order.id), "situacao": situacao, "situacao_nome": situacao_nome},
    )
    await db.commit()

    response = await client.get(
        "/api/margens/marketplace?attention_type=saldo&status=Pendente"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["pedido_bling"] == "123459"
    assert body["items"][0]["attention_saldo"] is True


async def test_marketplace_saldo_isenta_ml_shopee_e_ancora_efetivo_na_plataforma(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
):
    """ML/Shopee/TikTok: o saldo da PLATAFORMA é a fonte da verdade (Eduardo,
    01/09 em três tempos; a versão final é a da noite — "está aprovando tudo
    até com margem negativa: o saldo efetivo deixe sempre em branco, retirar
    a projeção, sempre pegar a da plataforma"). Três linhas em situação 6:
      - ML com líquido real (divergência de R$60 com o Bling): SEM pendência
        de saldo — Efetivo = líquido da plataforma (7000), não o Bling
        (6940); Aprovado.
      - Shopee/TikTok com líquido NULL: Efetivo EM BRANCO (nunca a projeção,
        nunca o Bling) e a linha fica PENDENTE por "aguardando saldo da
        plataforma" (motivo 'saldo') — sem margem oficial, nada aprova às
        cegas. saldo_projetado agora é sempre False (projeção retirada).
    """
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    ml = BlingOrder(
        bling_id=987658, numero="123460", item_codigo="sku-ml", item_index=0, situacao="6",
    )
    shopee = BlingOrder(
        bling_id=987659, numero="123461", item_codigo="sku-sh", item_index=0, situacao="6",
    )
    tiktok = BlingOrder(
        bling_id=987660, numero="123462", item_codigo="sku-tk", item_index=0, situacao="6",
    )
    db.add_all([ml, shopee, tiktok])
    await db.commit()
    await db.refresh(ml)
    await db.refresh(shopee)
    await db.refresh(tiktok)
    await db.execute(
        text(
            """
            INSERT INTO verificar_margem (
                bling_order_item_id, pedido_bling, bling_id, sku,
                situacao, situacao_nome, plataforma_bling, item_proportion,
                bling_valorbase_item, bling_custofrete_item, bling_taxacomissao_item,
                marketplace_liquido_base_margem_item,
                bling_status_margem
            )
            VALUES
                (:ml, '123460', 987658, 'sku-ml',
                 '6', 'Em aberto', 'ml', 1,
                 6940, 0, 0,
                 7000,
                 NULL),
                (:sh, '123461', 987659, 'sku-sh',
                 '6', 'Em aberto', 'shopee', 1,
                 150, 0, 30,
                 NULL,
                 NULL),
                (:tk, '123462', 987660, 'sku-tk',
                 '6', 'Em aberto', 'tiktok', 1,
                 480, 0, 40,
                 NULL,
                 NULL)
            """
        ),
        {"ml": str(ml.id), "sh": str(shopee.id), "tk": str(tiktok.id)},
    )
    await db.commit()

    # ML (líquido real presente): aprovado, Efetivo = plataforma, sem
    # divergência apesar dos R$60 de diferença com o Bling.
    aprovado = await client.get("/api/margens/marketplace?status=Aprovado")
    assert aprovado.status_code == 200
    por_pedido = {it["pedido_bling"]: it for it in aprovado.json()["items"]}
    assert por_pedido["123460"]["attention_saldo"] is False
    assert por_pedido["123460"]["saldo_efetivo"] == 7000  # líquido real
    assert por_pedido["123460"]["saldo_final"] == 7000
    assert por_pedido["123460"]["saldo_projetado"] is False
    assert "123461" not in por_pedido
    assert "123462" not in por_pedido

    # Shopee/TikTok (líquido NULL): pendentes por "aguardando saldo da
    # plataforma" (motivo 'saldo'), Efetivo/Plataforma em branco, sem ≈.
    pendente = await client.get(
        "/api/margens/marketplace?attention_type=saldo&status=Pendente"
    )
    assert pendente.status_code == 200
    body = pendente.json()
    assert body["total"] == 2
    por_pedido = {it["pedido_bling"]: it for it in body["items"]}
    for pedido in ("123461", "123462"):
        item = por_pedido[pedido]
        assert item["attention_saldo"] is True
        assert item["saldo_plataforma"] is None
        assert item["saldo_efetivo"] is None  # nunca o Bling, nunca projeção
        assert item["saldo_final"] is None
        assert item["saldo_projetado"] is False
        assert item["status"] == "Pendente"


async def test_marketplace_margem_filter_excludes_aguardando_devolucao(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
):
    """Margem baixa não triata pedidos em "Aguardando Devolução" (situação
    83957). Duas linhas com margem < mínima: a em 83957 some do filtro 'margem',
    a em outra situação aparece."""
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    excluido = BlingOrder(
        bling_id=987658,
        numero="123460",
        item_codigo="sku-5",
        item_index=0,
        situacao="83957",
    )
    incluido = BlingOrder(
        bling_id=987659,
        numero="123461",
        item_codigo="sku-6",
        item_index=0,
        situacao="15",
    )
    db.add_all([excluido, incluido])
    await db.commit()
    await db.refresh(excluido)
    await db.refresh(incluido)
    # Ambos com margem 5% < mínima 20% → "margem baixa".
    await db.execute(
        text(
            """
            INSERT INTO verificar_margem (
                bling_order_item_id, pedido_bling, bling_id, sku,
                situacao, situacao_nome, plataforma_bling, item_proportion,
                marketplace_margem, margem_minima,
                bling_status_margem
            )
            VALUES
                (:ex, '123460', 987658, 'sku-5',
                 '83957', 'Aguardando Devolução', 'ml', 1,
                 5, 20, NULL),
                (:inc, '123461', 987659, 'sku-6',
                 '6', 'Em aberto', 'ml', 1,
                 5, 20, NULL)
            """
        ),
        {"ex": str(excluido.id), "inc": str(incluido.id)},
    )
    await db.commit()

    # situacao=all: sem isso o filtro padrão (só 6/21/83965) esconderia o 83957
    # antes do gatilho de margem ser avaliado e o teste passaria à toa.
    response = await client.get(
        "/api/margens/marketplace?attention_type=margem&status=Pendente&situacao=all"
    )
    assert response.status_code == 200
    body = response.json()
    pedidos = {item["pedido_bling"] for item in body["items"]}
    assert "123461" in pedidos
    assert "123460" not in pedidos


async def test_marketplace_frete_estourado_fica_fora_da_listagem(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
):
    """Frete estourado (real > anúncio) NÃO aparece na aba Margem.

    Desde 77302a7 ("fix(margem): excluir pedidos com diferença de frete da
    visualização") a listagem inteira carrega `NOT _ATTENTION_FRETE_SQL` no
    WHERE: frete só se conhece depois do envio, então não há decisão de
    margem a tomar aqui — a linha some da aba (Pendentes E Aprovados) e a UI
    nem oferece mais o filtro 'frete' no dropdown. Caso real 278867: frete
    real 104.175 (proporção 0.5 do item) vs anúncio 78.26 → resultado
    25.915 > 0 → excluído. Este teste substitui o antigo
    test_marketplace_frete_result_uses_full_anuncio_per_item, que assertava
    a presença da linha (comportamento pré-77302a7).
    """
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    order = BlingOrder(
        bling_id=25959686080,
        numero="278867",
        numeroloja="2000016712859896",
        item_codigo="b008.12.18",
        item_index=0,
        situacao="15",
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    await db.execute(
        text(
            """
            INSERT INTO verificar_margem (
                bling_order_item_id, pedido_bling, pedido_marketplace,
                bling_id, sku, produto, situacao, situacao_nome,
                plataforma_bling, loja_nome, item_proportion,
                marketplace_frete_real_cobrado_item,
                evento_frete_anuncio, frete_projetado_item,
                bling_status_margem
            )
            VALUES (
                :id, '278867', '2000016712859896',
                25959686080, 'b008.12.18', 'Kit Malas',
                '6', 'Em aberto', 'ml', 'ML Marquezini', 0.5,
                104.175,
                78.26, 90,
                NULL
            )
            """
        ),
        {"id": str(order.id)},
    )
    await db.commit()

    # Fora da listagem em qualquer recorte: filtro frete, Pendente, Aprovado.
    for query in (
        "attention_type=frete&status=Pendente",
        "status=Pendente",
        "status=Aprovado",
    ):
        response = await client.get(f"/api/margens/marketplace?{query}")
        assert response.status_code == 200
        body = response.json()
        pedidos = {item["pedido_bling"] for item in body["items"]}
        assert "278867" not in pedidos, query


async def test_sync_from_marketplace_updates_snapshot_financials_without_view_refresh(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
):
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    order = BlingOrder(
        bling_id=987654,
        numero="123456",
        item_codigo="sku-1",
        item_index=0,
        situacao="15",
        valorbase=80,
        taxacomissao=20,
        custofrete=10,
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    await db.execute(
        text(
            """
            INSERT INTO verificar_margem (
                bling_order_item_id, pedido_bling, bling_id, sku,
                plataforma_bling, item_proportion,
                marketplace_valor_bruto_item, marketplace_taxas_item,
                marketplace_frete_real_cobrado_item,
                bling_valorbase_item, bling_taxacomissao_item,
                bling_custofrete_item, bling_custo_produtos
            )
            VALUES (
                :id, '123456', 987654, 'sku-1',
                'ml', 1,
                120, 10,
                5,
                80, 20,
                10, 50
            )
            """
        ),
        {"id": str(order.id)},
    )
    await db.commit()

    response = await client.post(f"/api/margens/marketplace/{order.id}/sync-from-marketplace")

    assert response.status_code == 200
    await db.refresh(order)
    assert float(order.valorbase) == 120.0
    assert float(order.taxacomissao) == 10.0
    assert float(order.custofrete) == 5.0
    snapshot = (
        await db.execute(
            text(
                """
                SELECT
                    bling_valorbase_item,
                    bling_taxacomissao_item,
                    bling_custofrete_item,
                    bling_lucro_calculado,
                    bling_margem_calculado
                FROM verificar_margem
                WHERE bling_order_item_id = CAST(:id AS uuid)
                """
            ),
            {"id": str(order.id)},
        )
    ).mappings().one()
    assert float(snapshot["bling_valorbase_item"]) == 120.0
    assert float(snapshot["bling_taxacomissao_item"]) == 10.0
    assert float(snapshot["bling_custofrete_item"]) == 5.0
    assert float(snapshot["bling_lucro_calculado"]) == 55.0
    assert float(snapshot["bling_margem_calculado"]) == 1.1


async def test_sync_saldo_final_approves_when_margin_clears_minimum(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
):
    """Editar o Saldo Efetivo aprova automaticamente SÓ quando a margem
    recalculada (a partir do novo saldo) atinge a mínima."""
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    order = BlingOrder(
        bling_id=987660,
        numero="500001",
        item_codigo="sku-ok",
        item_index=0,
        situacao="6",
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    await db.execute(
        text(
            """
            INSERT INTO verificar_margem (
                bling_order_item_id, pedido_bling, bling_id, sku,
                situacao, situacao_nome, plataforma_bling, item_proportion,
                bling_valorbase_item, bling_custofrete_item, bling_taxacomissao_item,
                bling_custo_produtos, margem_minima, ajustes, bling_status_margem
            )
            VALUES (
                :id, '500001', 987660, 'sku-ok',
                '6', 'Em aberto', 'ml', 1,
                100, 0, 0,
                100, 0.20, 0, NULL
            )
            """
        ),
        {"id": str(order.id)},
    )
    await db.commit()

    # valor_base=130 → margem (130-100)/100 = 30% ≥ 20% mínima → aprova.
    response = await client.post(
        f"/api/margens/marketplace/{order.id}/sync-saldo-final",
        json={"valor_base": 130},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "Aprovado"

    snapshot = (
        await db.execute(
            text(
                """
                SELECT bling_status_margem, verificado
                FROM verificar_margem
                WHERE bling_order_item_id = CAST(:id AS uuid)
                """
            ),
            {"id": str(order.id)},
        )
    ).mappings().one()
    assert snapshot["bling_status_margem"] == "Aprovado"
    assert snapshot["verificado"] is True


async def test_sync_saldo_final_keeps_pending_when_margin_below_minimum(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
):
    """Quando a margem recalculada fica ABAIXO da mínima, a edição do Saldo
    Efetivo NÃO aprova: o pedido é fixado como Pendente (bling_status_margem=
    'Pendente'), mesmo sem nenhum gatilho de atenção ativo — fecha o back-door
    em que, resolvida a divergência de saldo, o pedido cairia em Aprovado."""
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    order = BlingOrder(
        bling_id=987661,
        numero="500002",
        item_codigo="sku-low",
        item_index=0,
        situacao="6",
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    # marketplace_margem e marketplace_liquido NULL → nenhum gatilho de atenção
    # (margem/frete/saldo) dispara após a edição.
    await db.execute(
        text(
            """
            INSERT INTO verificar_margem (
                bling_order_item_id, pedido_bling, bling_id, sku,
                situacao, situacao_nome, plataforma_bling, item_proportion,
                bling_valorbase_item, bling_custofrete_item, bling_taxacomissao_item,
                bling_custo_produtos, margem_minima, ajustes, bling_status_margem
            )
            VALUES (
                :id, '500002', 987661, 'sku-low',
                '6', 'Em aberto', 'ml', 1,
                100, 0, 0,
                100, 0.20, 0, NULL
            )
            """
        ),
        {"id": str(order.id)},
    )
    await db.commit()

    # valor_base=110 → margem (110-100)/100 = 10% < 20% mínima → NÃO aprova.
    response = await client.post(
        f"/api/margens/marketplace/{order.id}/sync-saldo-final",
        json={"valor_base": 110},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "Pendente"

    snapshot = (
        await db.execute(
            text(
                """
                SELECT bling_status_margem, verificado
                FROM verificar_margem
                WHERE bling_order_item_id = CAST(:id AS uuid)
                """
            ),
            {"id": str(order.id)},
        )
    ).mappings().one()
    assert snapshot["bling_status_margem"] == "Pendente"
    assert snapshot["verificado"] is False

    # Fixado: aparece na aba Pendente mesmo sem gatilho de atenção ativo...
    pending = await client.get("/api/margens/marketplace?status=Pendente")
    assert pending.status_code == 200
    assert "500002" in [it["pedido_bling"] for it in pending.json()["items"]]
    # ...e NÃO cai em Aprovado (regressão do back-door).
    approved = await client.get("/api/margens/marketplace?status=Aprovado")
    assert "500002" not in [it["pedido_bling"] for it in approved.json()["items"]]


async def test_marketplace_margem_isenta_por_data_especial(
    client,
    db: AsyncSession,
    make_user,
    auth_as,
):
    """Datas Especiais do segmento (Eduardo 01/09: "por exemplo está com margem
    negativa, aprova"): no período, margem abaixo da mínima NÃO trava o pedido.

    Árvore Pai→Filho; janela SEM piso no PAI (01–10/09, desce pra família) e
    janela COM piso -15% no FILHO (01–10/10). Todas as linhas apontam pro
    FILHO com mínima 15%:
      - 05/09, margem -10%  → isenta pela janela do pai (aprova tudo)
      - 20/09, margem -10%  → fora de qualquer janela → margem baixa
      - 05/10, margem -10%  → -10% ≥ piso -15% → isenta
      - 05/10, margem -20%  → -20% < piso -15% → margem baixa
      - 05/09, margem -10% SEM segmento → margem baixa (exceção não vaza)
    """
    from datetime import UTC, datetime
    from decimal import Decimal
    from uuid import uuid4

    from app.models import Segment, SegmentSpecialDate

    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    pai = Segment(name="Pai DE", slug=f"pai-{uuid4().hex[:6]}")
    db.add(pai)
    await db.flush()
    filho = Segment(
        name="Filho DE",
        slug=f"filho-{uuid4().hex[:6]}",
        parent_id=pai.id,
        min_margin=Decimal("0.15"),
    )
    db.add(filho)
    await db.flush()
    db.add_all(
        [
            SegmentSpecialDate(
                segment_id=pai.id,
                date_start=datetime(2026, 9, 1).date(),
                date_end=datetime(2026, 9, 10).date(),
                min_margin=None,
            ),
            SegmentSpecialDate(
                segment_id=filho.id,
                date_start=datetime(2026, 10, 1).date(),
                date_end=datetime(2026, 10, 10).date(),
                min_margin=Decimal("-0.15"),
            ),
        ]
    )
    await db.commit()

    async def seed(pedido: str, dt: datetime, margem: str, leaf) -> None:
        await db.execute(
            text(
                """
                INSERT INTO verificar_margem (
                    bling_order_item_id, pedido_bling, sku, data,
                    situacao, situacao_nome, plataforma_bling, item_proportion,
                    marketplace_margem, margem_minima, pricing_leaf_segment_id
                ) VALUES (
                    :id, :pedido, :sku, :dt,
                    '6', 'Em aberto', 'ml', 1,
                    :margem, 0.15, :leaf
                )
                """
            ),
            {
                "id": str(uuid4()),
                "pedido": pedido,
                "sku": f"sku-{pedido}",
                "dt": dt,
                "margem": margem,
                "leaf": None if leaf is None else str(leaf),
            },
        )
        await db.commit()

    meiodia = {"hour": 12, "tzinfo": UTC}  # 09:00 em SP — longe da virada de dia
    await seed("601001", datetime(2026, 9, 5, **meiodia), "-0.10", filho.id)
    await seed("601002", datetime(2026, 9, 20, **meiodia), "-0.10", filho.id)
    await seed("601003", datetime(2026, 10, 5, **meiodia), "-0.10", filho.id)
    await seed("601004", datetime(2026, 10, 5, **meiodia), "-0.20", filho.id)
    await seed("601005", datetime(2026, 9, 5, **meiodia), "-0.10", None)

    resp = await client.get(
        "/api/margens/marketplace?attention_type=margem&status=Pendente"
    )
    assert resp.status_code == 200
    flagged = {it["pedido_bling"] for it in resp.json()["items"]}
    assert {"601002", "601004", "601005"} <= flagged
    assert "601001" not in flagged
    assert "601003" not in flagged

    # Isentas viram Aprovado (sem outro gatilho ativo) e levam o badge.
    aprovado = await client.get("/api/margens/marketplace?status=Aprovado")
    por_pedido = {it["pedido_bling"]: it for it in aprovado.json()["items"]}
    assert por_pedido["601001"]["data_especial"] is True
    assert por_pedido["601003"]["data_especial"] is True
    # Abaixo do piso especial: continua Pendente e SEM badge (a janela não
    # aprovou esta margem).
    pendente = await client.get("/api/margens/marketplace?status=Pendente")
    pend = {it["pedido_bling"]: it for it in pendente.json()["items"]}
    assert pend["601004"]["data_especial"] is False
    assert pend["601005"]["data_especial"] is False
