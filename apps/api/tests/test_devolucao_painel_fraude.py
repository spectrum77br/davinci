# ruff: noqa: E501
"""Abas Acompanhamento × Fraude da tela Devoluções (Vinicius 17/09).

"na aba Acompanhamento uma pessoa cuida só dos pedidos que foram devolvidos e
que vão devolver; na aba Fraude é outra pessoa, ela cuida dos pedidos que o
cliente alega que chegou vazio, que pede reembolso sem devolução". Regra
automática = tipo do caso (REFUND = não volta pacote → Fraude); o botão da
tela fixa o painel na mão (`fila_manual`) e a regra não mexe mais.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import DevolucaoRastreio
from app.routers.devolutions import _fila
from app.services import logistica_meli, logistica_shopee

pytestmark = pytest.mark.asyncio


def test_regra_automatica_e_manual_do_painel():
    assert _fila(None, "REFUND") == ("fraude", False)
    assert _fila(None, "RETURN_AND_REFUND") == ("acompanhamento", False)
    assert _fila(None, None) == ("acompanhamento", False)  # sem caso conhecido
    assert _fila("fraude", "RETURN_AND_REFUND") == ("fraude", True)  # movido na mão
    assert _fila("acompanhamento", "REFUND") == ("acompanhamento", True)
    assert _fila("lixo", "REFUND") == ("fraude", False)  # valor inválido = automático


def test_shopee_needs_logistics_vira_tipo_do_caso():
    # Medido 17/09: ACCEPTED "NOT_RECEIPT" sem pacote → só dinheiro (Fraude).
    so_dinheiro = logistica_shopee._return_info({"status": "ACCEPTED", "needs_logistics": False, "refund_amount": 525, "update_time": 1}, "ACCEPTED")
    assert so_dinheiro.return_type == "REFUND" and so_dinheiro.tracking is None
    com_pacote = logistica_shopee._return_info({"status": "PROCESSING", "needs_logistics": True, "tracking_number": "AP456427997BR"}, "PROCESSING")
    assert com_pacote.return_type == "RETURN_AND_REFUND"
    assert logistica_shopee._return_info({"status": "REQUESTED"}, "REQUESTED").return_type is None


def test_ml_mediacao_sem_devolucao_e_so_dinheiro():
    info = logistica_meli._so_reembolso(logistica_meli._ReembolsoML(False, None, None, None), "5578079870")
    assert info.return_type == "REFUND"


def _perms() -> dict:
    return {"devolucoes": {"view": True, "edit": True, "delete": True}}


async def _monta(db: AsyncSession, pedidos: list[str]) -> None:
    """Fake da vw_devolucoes com N pedidos em Aguardando Devolução (83957)."""
    schema = get_settings().database_schema
    entrada = (datetime.now(UTC) - timedelta(days=2)).date()
    ids = {p: uuid4() for p in pedidos}
    await db.execute(text(f'DROP VIEW IF EXISTS "{schema}".vw_devolucoes'))
    for p, oid in ids.items():
        await db.execute(
            text("INSERT INTO bling_orders (id, numero, aguardando_devolucao_data) VALUES (:o, :p, :e)"),
            {"o": oid, "p": p, "e": entrada},
        )
    linhas = ",\n".join(
        f"('2026-09-10T03:00:00+00:00'::timestamptz, '{p}'::text, 'MP-{p}'::text, 'shopee'::text, 'Loja'::text, NULL::bigint, "
        f"'Cliente'::text, 'Curitiba'::text, 'PR'::text, 'a001'::text, 'Fone'::text, 1::integer, '{oid}'::uuid, '83957'::text)"
        for p, oid in ids.items()
    )
    await db.execute(
        text(
            f"""
            CREATE VIEW "{schema}".vw_devolucoes AS
            SELECT * FROM (VALUES {linhas}) AS t(
                data, pedido_bling, pedido_marketplace, plataforma_bling, loja_nome, bling_loja_id,
                nome_destinatario, cidade_destino, uf_destino, sku, produto, quantidade,
                bling_order_item_id, situacao
            )
            """  # noqa: S608
        )
    )
    await db.commit()


async def _derruba_view(db: AsyncSession) -> None:
    schema = get_settings().database_schema
    await db.execute(text(f'DROP VIEW IF EXISTS "{schema}".vw_devolucoes'))
    await db.commit()


async def test_get_classifica_e_patch_move_entre_os_paineis(client, db, make_user, auth_as):
    user = await make_user(permissions=_perms())
    auth_as(user)
    vazio, pacote, nada = (f"4{uuid4().hex[:6]}" for _ in range(3))
    await _monta(db, [vazio, pacote, nada])
    db.add_all([
        # "Chegou vazio": Shopee só reembolso, disputa aberta → Fraude.
        DevolucaoRastreio(pedido_bling=vazio, fonte_auto="shopee", devolucao_status_auto="REQUESTED", devolucao_tipo_auto="REFUND"),
        # Pacote voltando e já reembolsado → continua no Acompanhamento (Vinicius: "esses 38 ficariam").
        DevolucaoRastreio(pedido_bling=pacote, fonte_auto="shopee", devolucao_status_auto="ACCEPTED", devolucao_tipo_auto="RETURN_AND_REFUND",
                          rastreio_auto="AP1BR", reembolso_auto=True, reembolso_valor_auto=Decimal("100")),
    ])
    await db.commit()
    try:
        r = await client.get("/api/devolutions/acompanhamento")
        assert r.status_code == 200
        por = {i["pedido_bling"]: i for i in r.json()["items"]}
        assert (por[vazio]["fila"], por[vazio]["fila_manual"]) == ("fraude", False)
        assert (por[pacote]["fila"], por[pacote]["fila_manual"]) == ("acompanhamento", False)
        assert (por[nada]["fila"], por[nada]["fila_manual"]) == ("acompanhamento", False)  # sem caso: fica onde sempre esteve

        # Botão "mover": fixa na mão e vale mais que a regra.
        r = await client.patch(f"/api/devolutions/acompanhamento/{vazio}", json={"fila": "acompanhamento"})
        assert r.status_code == 200
        assert (r.json()["fila"], r.json()["fila_manual"]) == ("acompanhamento", True)
        r = await client.patch(f"/api/devolutions/acompanhamento/{nada}", json={"fila": "fraude"})
        assert r.status_code == 200 and r.json()["fila"] == "fraude"
        # Valor inválido é recusado; omitido não mexe.
        r = await client.patch(f"/api/devolutions/acompanhamento/{nada}", json={"fila": "outra"})
        assert r.status_code == 422
        r = await client.patch(f"/api/devolutions/acompanhamento/{nada}", json={"observacao": "x"})
        assert r.status_code == 200 and r.json()["fila"] == "fraude" and r.json()["fila_manual"] is True

        r = await client.get("/api/devolutions/acompanhamento")
        por = {i["pedido_bling"]: i for i in r.json()["items"]}
        assert por[vazio]["fila"] == "acompanhamento" and por[vazio]["fila_manual"] is True
        assert por[nada]["fila"] == "fraude" and por[nada]["fila_manual"] is True
        row = await db.get(DevolucaoRastreio, nada)
        await db.refresh(row)
        assert row.fila_manual == "fraude" and row.fila_manual_at is not None
    finally:
        await _derruba_view(db)
