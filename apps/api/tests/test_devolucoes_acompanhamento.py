"""Aba Acompanhamento de Devoluções (folha do Eduardo, 2026-09-02).

Cobre as três peças novas:
- helper `_next_aguardando_devolucao_data` (carimbo de entrada em 83957 no
  ingest, mesmo padrão da em_andamento_data);
- GET /api/devolutions/acompanhamento (lista por item com dias em devolução,
  rastreio manual e flag "lançada");
- PATCH /api/devolutions/acompanhamento/{pedido} (rastreio/localização com
  carimbo automático da data da última movimentação).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services.bling_orders import _next_aguardando_devolucao_data

pytestmark = pytest.mark.asyncio

_SP = ZoneInfo("America/Sao_Paulo")


def _perm(*, view: bool = True, edit: bool = True) -> dict:
    return {"devolucoes": {"view": view, "edit": edit, "delete": False}}


# ── Helper do ingest ─────────────────────────────────────────────────────


async def test_carimba_na_entrada_em_83957() -> None:
    # 02:00 UTC = 23:00 do dia ANTERIOR em SP — o carimbo tem que ser no fuso SP.
    agora = datetime(2026, 9, 2, 2, 0, tzinfo=UTC)
    got = _next_aguardando_devolucao_data(
        nova_situacao="83957", situacao_antiga="15", data_existente=None, agora=agora
    )
    assert got == date(2026, 9, 1)


async def test_reentrada_recarimba() -> None:
    agora = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    got = _next_aguardando_devolucao_data(
        nova_situacao="83957",
        situacao_antiga="545902",
        data_existente=date(2026, 1, 10),
        agora=agora,
    )
    assert got == date(2026, 9, 2)


async def test_ja_na_situacao_preserva_data() -> None:
    got = _next_aguardando_devolucao_data(
        nova_situacao="83957",
        situacao_antiga="83957",
        data_existente=date(2026, 8, 20),
        agora=datetime.now(UTC),
    )
    assert got == date(2026, 8, 20)


async def test_ja_na_situacao_sem_data_carimba_aproximacao() -> None:
    agora = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    got = _next_aguardando_devolucao_data(
        nova_situacao="83957", situacao_antiga="83957", data_existente=None, agora=agora
    )
    assert got == date(2026, 9, 2)


async def test_saida_da_situacao_preserva_trilha() -> None:
    got = _next_aguardando_devolucao_data(
        nova_situacao="545902",
        situacao_antiga="83957",
        data_existente=date(2026, 8, 20),
        agora=datetime.now(UTC),
    )
    assert got == date(2026, 8, 20)


async def test_fora_da_situacao_sem_data_nao_mexe() -> None:
    got = _next_aguardando_devolucao_data(
        nova_situacao="15", situacao_antiga="6", data_existente=None, agora=datetime.now(UTC)
    )
    assert got is None


# ── Endpoints ────────────────────────────────────────────────────────────

_ITEM_A = UUID("aaaaaaaa-0000-0000-0000-000000000001")
_ITEM_B = UUID("aaaaaaaa-0000-0000-0000-000000000002")


async def _seed_acompanhamento(db: AsyncSession, schema: str) -> None:
    """Dois itens do MESMO pedido 555001 em 83957 (entrou há 5 dias) e a view
    fake com as colunas que o endpoint consome."""
    entrada = datetime.now(_SP).date() - timedelta(days=5)
    await db.execute(text(f'DROP VIEW IF EXISTS "{schema}".vw_devolucoes'))
    await db.execute(
        text(
            """
            INSERT INTO bling_orders
                (id, numero, numeroloja, situacao, aguardando_devolucao_data)
            VALUES
                (:item_a, '555001', 'MKT-555001', '83957', :entrada),
                (:item_b, '555001', 'MKT-555001', '83957', :entrada)
            """
        ),
        {"item_a": _ITEM_A, "item_b": _ITEM_B, "entrada": entrada},
    )
    await db.execute(
        text(
            f"""
            CREATE VIEW "{schema}".vw_devolucoes AS
            SELECT * FROM (VALUES
                (
                    '2026-08-20T12:00:00+00:00'::timestamptz,
                    '555001'::text, 'MKT-555001'::text, '83957'::text,
                    'Shopee'::text, 'Shopee Jlas'::text, 1::bigint,
                    'dg019.ra'::text, 'Fossibot F105 - Preto'::text, 1::integer,
                    '{_ITEM_A}'::uuid, 'Maria da Silva'::text,
                    'Curitiba'::text, 'PR'::text
                ),
                (
                    '2026-08-20T12:00:00+00:00'::timestamptz,
                    '555001'::text, 'MKT-555001'::text, '83957'::text,
                    'Shopee'::text, 'Shopee Jlas'::text, 1::bigint,
                    'a003.pi'::text, 'Fone UFB10 - Branco'::text, 2::integer,
                    '{_ITEM_B}'::uuid, 'Maria da Silva'::text,
                    'Curitiba'::text, 'PR'::text
                )
            ) AS t(
                data, pedido_bling, pedido_marketplace, situacao,
                plataforma_bling, loja_nome, bling_loja_id,
                sku, produto, quantidade,
                bling_order_item_id, nome_destinatario,
                cidade_destino, uf_destino
            )
            """  # noqa: S608
        )
    )
    await db.commit()


async def _drop_view(db: AsyncSession, schema: str) -> None:
    await db.execute(text(f'DROP VIEW IF EXISTS "{schema}".vw_devolucoes'))
    await db.commit()


async def test_acompanhamento_lista_itens_com_dias_e_rastreio(
    client, db: AsyncSession, make_user, auth_as
):
    user = await make_user(permissions=_perm(edit=False))
    auth_as(user)
    schema = get_settings().database_schema
    await _seed_acompanhamento(db, schema)
    # Rastreio manual já salvo pro pedido (grão = pedido, vale pras 2 linhas).
    await db.execute(
        text(
            """
            INSERT INTO devolucao_rastreio (pedido_bling, rastreio, localizacao, localizacao_data)
            VALUES ('555001', 'BR123', 'CD Curitiba', '2026-09-01T10:00:00+00:00'::timestamptz)
            """
        )
    )
    await db.commit()

    try:
        response = await client.get("/api/devolutions/acompanhamento")
    finally:
        await _drop_view(db, schema)

    assert response.status_code == 200
    body = response.json()
    assert body["total_pedidos"] == 1  # 2 itens, 1 pedido
    assert len(body["items"]) == 2
    by_sku = {i["sku"]: i for i in body["items"]}
    item = by_sku["dg019.ra"]
    assert item["pedido_bling"] == "555001"
    assert item["cliente"] == "Maria da Silva"
    assert item["cidade"] == "Curitiba"
    assert item["uf"] == "PR"
    assert item["loja"] == "Shopee Jlas"
    assert item["plataforma"] == "Shopee"
    assert item["dias_em_devolucao"] == 5
    assert item["rastreio"] == "BR123"
    assert item["localizacao"] == "CD Curitiba"
    assert item["lancada"] is False
    assert by_sku["a003.pi"]["quantidade"] == 2


async def test_acompanhamento_marca_pedido_ja_lancado(
    client, db: AsyncSession, make_user, auth_as
):
    user = await make_user(permissions=_perm())
    auth_as(user)
    schema = get_settings().database_schema
    await _seed_acompanhamento(db, schema)
    await db.execute(
        text("INSERT INTO devolutions (pedido_bling, conta) VALUES ('555001', 'Shopee Jlas')")
    )
    await db.commit()

    try:
        response = await client.get("/api/devolutions/acompanhamento")
    finally:
        await _drop_view(db, schema)

    assert response.status_code == 200
    assert all(i["lancada"] is True for i in response.json()["items"])


async def test_acompanhamento_exige_permissao_view(client, db: AsyncSession, make_user, auth_as):
    user = await make_user(permissions={"devolucoes": {"view": False}})
    auth_as(user)
    response = await client.get("/api/devolutions/acompanhamento")
    assert response.status_code == 403


async def test_patch_rastreio_upsert_e_carimbo_de_movimentacao(
    client, db: AsyncSession, make_user, auth_as
):
    user = await make_user(permissions=_perm())
    auth_as(user)
    await db.execute(
        text(
            "INSERT INTO bling_orders (id, numero, situacao) VALUES (:id, '555002', '83957')"
        ),
        {"id": UUID("aaaaaaaa-0000-0000-0000-000000000003")},
    )
    await db.commit()

    # 1) Só o rastreio: cria a linha, SEM data de movimentação.
    r1 = await client.patch(
        "/api/devolutions/acompanhamento/555002", json={"rastreio": "  BR999  "}
    )
    assert r1.status_code == 200
    assert r1.json()["rastreio"] == "BR999"
    assert r1.json()["localizacao"] is None
    assert r1.json()["localizacao_data"] is None

    # 2) Localização nova → carimba a data da última movimentação.
    r2 = await client.patch(
        "/api/devolutions/acompanhamento/555002", json={"localizacao": "CTE Cajamar"}
    )
    assert r2.status_code == 200
    assert r2.json()["localizacao"] == "CTE Cajamar"
    carimbo = r2.json()["localizacao_data"]
    assert carimbo is not None
    assert r2.json()["rastreio"] == "BR999"  # campo ausente não é tocado

    # 3) Mesma localização de novo → data NÃO muda (não houve movimentação).
    r3 = await client.patch(
        "/api/devolutions/acompanhamento/555002", json={"localizacao": "CTE Cajamar"}
    )
    assert r3.status_code == 200
    assert r3.json()["localizacao_data"] == carimbo

    # 4) Localização mudou → recarimba.
    r4 = await client.patch(
        "/api/devolutions/acompanhamento/555002", json={"localizacao": "Saiu para entrega"}
    )
    assert r4.status_code == 200
    assert r4.json()["localizacao_data"] != carimbo

    # 5) String vazia limpa localização E a data.
    r5 = await client.patch(
        "/api/devolutions/acompanhamento/555002", json={"localizacao": ""}
    )
    assert r5.status_code == 200
    assert r5.json()["localizacao"] is None
    assert r5.json()["localizacao_data"] is None


async def test_patch_rastreio_pedido_inexistente_404(client, make_user, auth_as):
    user = await make_user(permissions=_perm())
    auth_as(user)
    response = await client.patch(
        "/api/devolutions/acompanhamento/999999", json={"rastreio": "X"}
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "pedido_not_found"


async def test_patch_rastreio_exige_permissao_edit(
    client, db: AsyncSession, make_user, auth_as
):
    user = await make_user(permissions=_perm(edit=False))
    auth_as(user)
    response = await client.patch(
        "/api/devolutions/acompanhamento/555001", json={"rastreio": "X"}
    )
    assert response.status_code == 403
