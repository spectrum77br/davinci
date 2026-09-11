"""Contadores da aba Lançamentos (GET /api/devolutions).

O grão da tabela `devolutions` é ITEM — um pedido com dois SKUs vira duas
linhas com o mesmo `pedido_bling` — e o card "Total devoluções" mostrava
`total` (linhas). A listagem passou a devolver também `total_pedidos`
(pedidos distintos; linha sem número de pedido conta como 1) e o mesmo par
para reembolso (`reembolso_itens` / `reembolso_pedidos`), sempre com os
filtros da própria listagem.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


def _perm() -> dict:
    return {"devolucoes": {"view": True, "edit": False, "delete": False}}


async def _seed(db: AsyncSession) -> None:
    # 287705: 3 linhas (2 reembolsadas) · 290998: 1 linha · 291645: 2 linhas,
    # uma com espaços em volta (btrim) · 2 linhas sem pedido (NULL e vazio),
    # cada uma conta como pedido próprio.
    await db.execute(
        text(
            """
            INSERT INTO devolutions (pedido_bling, conta, condicao_produto, reembolso) VALUES
              ('287705',   'Loja 205660518', 'Manutenção', true),
              ('287705',   'Loja 205660518', 'Manutenção', true),
              ('287705',   'Loja 205660518', 'Manutenção', false),
              ('290998',   'Shopee',         'Manutenção', false),
              (' 291645 ', 'ML',             'Usado',      true),
              ('291645',   'ML',             'Usado',      false),
              (NULL,       'Avulsa',         'Usado',      true),
              ('',         'Avulsa 2',       'Usado',      false)
            """
        )
    )
    await db.commit()


async def test_listagem_conta_pedidos_distintos_alem_das_linhas(
    client, db: AsyncSession, make_user, auth_as
):
    auth_as(await make_user(permissions=_perm()))
    await _seed(db)

    response = await client.get("/api/devolutions")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 8
    assert body["total_pedidos"] == 5  # 287705, 290998, 291645 + 2 sem pedido
    assert body["reembolso_itens"] == 4
    assert body["reembolso_pedidos"] == 3  # 287705, 291645 + 1 sem pedido


async def test_contadores_seguem_os_filtros_da_listagem(
    client, db: AsyncSession, make_user, auth_as
):
    auth_as(await make_user(permissions=_perm()))
    await _seed(db)

    response = await client.get("/api/devolutions", params={"condicao": "Manutenção"})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 4
    assert body["total_pedidos"] == 2  # 287705 + 290998
    assert body["reembolso_itens"] == 2
    assert body["reembolso_pedidos"] == 1  # só o 287705
