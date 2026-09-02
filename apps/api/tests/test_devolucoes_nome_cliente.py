"""Nome do cliente nas devoluções (bug report Eduardo, 2026-09-02).

Pedidos Amazon chegam com transporte.contato.nome = nome da CONTA
("Amazon DBA"/"Amazon KFA") — o cliente real vem em transporte.etiqueta.nome
e contato.nome. Cobre:
- helper `_melhor_nome_destinatario` (cai pra etiqueta/contato só quando o
  nome é genérico da conta);
- `_row_from_item` gravando o nome certo num pedido Amazon;
- GET /api/devolutions expondo `cliente` (JOIN bling_orders pelo número).
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.bling_orders import _melhor_nome_destinatario, _row_from_item

pytestmark = pytest.mark.asyncio


def _perm(*, view: bool = True, edit: bool = True) -> dict:
    return {"devolucoes": {"view": view, "edit": edit, "delete": False}}


# ── Helper ───────────────────────────────────────────────────────────────


async def test_amazon_generico_cai_pra_etiqueta() -> None:
    got = _melhor_nome_destinatario("Amazon DBA", "Karolaine Araujo", "Karolaine Araujo")
    assert got == "Karolaine Araujo"


async def test_amazon_generico_sem_etiqueta_cai_pro_contato() -> None:
    got = _melhor_nome_destinatario("Amazon KFA", None, "João Souza")
    assert got == "João Souza"


async def test_amazon_minusculo_tambem_e_generico() -> None:
    got = _melhor_nome_destinatario("amazon dba", "Karolaine Araujo", None)
    assert got == "Karolaine Araujo"


async def test_nome_normal_nao_e_trocado() -> None:
    # Nos demais marketplaces transporte.contato É o destinatário — a
    # precedência original tem que se manter mesmo com etiqueta diferente.
    got = _melhor_nome_destinatario("Maria da Silva", "Outro Nome", "Terceiro Nome")
    assert got == "Maria da Silva"


async def test_amazonas_nao_e_generico() -> None:
    # \b: só "amazon" como palavra inteira é conta — "Amazonas..." é cliente.
    got = _melhor_nome_destinatario("Amazonas Comercio LTDA", "Outro Nome", None)
    assert got == "Amazonas Comercio LTDA"


async def test_generico_sem_fallback_mantem_generico() -> None:
    # Melhor mostrar "Amazon DBA" do que célula vazia.
    got = _melhor_nome_destinatario("Amazon DBA", None, "")
    assert got == "Amazon DBA"


async def test_tudo_vazio_vira_none() -> None:
    assert _melhor_nome_destinatario(None, None, None) is None
    assert _melhor_nome_destinatario("  ", "", None) is None


async def test_row_from_item_pedido_amazon_usa_nome_da_etiqueta() -> None:
    raw = {
        "id": 999,
        "numero": "273393",
        "situacao": {"id": 83957},
        "transporte": {
            "contato": {"nome": "Amazon DBA"},
            "etiqueta": {"nome": "Karolaine Araujo", "municipio": "Macapá", "uf": "AP"},
        },
        "contato": {"nome": "Karolaine Araujo"},
        "itens": [],
    }
    row = _row_from_item(raw, {}, item_index=0, store_id=None)
    assert row["nome_destinatario"] == "Karolaine Araujo"
    assert row["cidade_destino"] == "Macapá"


# ── GET /api/devolutions expõe `cliente` ─────────────────────────────────


async def test_listagem_traz_cliente_do_bling_orders(
    client, db: AsyncSession, make_user, auth_as
):
    user = await make_user(permissions=_perm(edit=False))
    auth_as(user)
    # Grão de bling_orders é ITEM: 2 linhas do mesmo pedido, uma sem nome —
    # MAX ignora NULL e colapsa pro nome real.
    await db.execute(
        text(
            """
            INSERT INTO bling_orders (id, numero, nome_destinatario)
            VALUES (:a, '777001', NULL), (:b, '777001', 'Karolaine Araujo')
            """
        ),
        {
            "a": UUID("bbbbbbbb-0000-0000-0000-000000000001"),
            "b": UUID("bbbbbbbb-0000-0000-0000-000000000002"),
        },
    )
    await db.execute(
        text(
            """
            INSERT INTO devolutions (pedido_bling, conta)
            VALUES ('777001', 'Amazon DBA'), (NULL, 'Loja Avulsa')
            """
        )
    )
    await db.commit()

    response = await client.get("/api/devolutions")

    assert response.status_code == 200
    by_conta = {i["conta"]: i for i in response.json()["items"]}
    assert by_conta["Amazon DBA"]["cliente"] == "Karolaine Araujo"
    assert by_conta["Loja Avulsa"]["cliente"] is None
