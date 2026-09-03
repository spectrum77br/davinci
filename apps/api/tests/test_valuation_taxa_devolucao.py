"""Página Valuation — bloco Comercial, linha "Taxa de Devolução".

Taxa = (qtd de PEDIDOS devolvidos condição Novo+Usado, contados uma vez por
número de pedido do Bling — DISTINCT pedido_bling —, por mês de criação) ÷
(qtd de pedidos do faturamento, situações aplicáveis, por mês da data do
pedido) × 100. Cobre:
  * o denominador = pedidos do faturamento (NÃO só entregues);
  * o numerador conta PEDIDOS distintos, não linhas: um kit devolvido vira
    várias linhas com o mesmo pedido, mas conta 1 (só Novo+Usado).
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, Devolution, User, UserRole, UserStatus
from app.routers.financeiro import _make_valuation_token


async def _admin(db: AsyncSession) -> User:
    email = f"txd-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(
        open_id=f"email:{email}", email=email,
        role=UserRole.ADMIN, status=UserStatus.ACTIVE,
        permissions={}, sales_teams=None,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _headers() -> dict[str, str]:
    token, _ = _make_valuation_token()
    return {"X-Valuation-Token": token}


async def _pedido(db: AsyncSession, *, bling_id: int, situacao: str,
                  loja: str = "5001") -> None:
    db.add(BlingOrder(
        bling_id=bling_id, numero=str(bling_id), item_codigo=f"s-{bling_id}",
        item_index=0, situacao=situacao, data=datetime.now(UTC),
        loja=loja, total=100,
    ))
    await db.commit()


async def _devolucao(db: AsyncSession, *, pedido: int, condicao: str,
                     quantidade: int = 1) -> None:
    db.add(Devolution(
        conta="teste", pedido_bling=str(pedido), condicao_produto=condicao,
        quantidade=quantidade, data=datetime.now(UTC),
    ))
    await db.commit()


def _taxa_total(body: dict) -> list:
    return body["comercial"]["total_taxa_devolucao"]


def _idx_mes_atual(body: dict) -> int:
    hoje = datetime.now(UTC).date().replace(day=1).isoformat()
    return body["comercial"]["meses"].index(hoje)


@pytest.mark.asyncio
async def test_taxa_denominador_faturamento_numerador_pedidos(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    # Denominador = 5 pedidos de faturamento (situações aplicáveis variadas).
    await _pedido(db, bling_id=8001, situacao="83953")  # Entregue
    await _pedido(db, bling_id=8002, situacao="6")       # Em aberto
    await _pedido(db, bling_id=8003, situacao="15")      # Em andamento
    await _pedido(db, bling_id=8004, situacao="83965")   # Enviado Etiqueta/Importado (legado)
    await _pedido(db, bling_id=8006, situacao="21")      # Em digitação (etiqueta enviada)
    # Não faturamento → fora do denominador.
    await _pedido(db, bling_id=8005, situacao="12")      # Cancelado
    # Numerador = PEDIDOS distintos com devolução Novo+Usado: {8001, 8002} = 2.
    await _devolucao(db, pedido=8001, condicao="Novo", quantidade=2)
    await _devolucao(db, pedido=8002, condicao="Usado", quantidade=1)
    # Não conta: outra condição.
    await _devolucao(db, pedido=8003, condicao="Extraviado", quantidade=5)
    admin = await _admin(db)
    auth_as(admin)

    r = await client.get("/api/financeiro/valuation", headers=_headers())
    assert r.status_code == 200, r.text
    body = r.json()
    i = _idx_mes_atual(body)
    # 2 pedidos devolvidos ÷ 5 pedidos de faturamento × 100 = 40.0.
    assert _taxa_total(body)[i] == 40.0


@pytest.mark.asyncio
async def test_taxa_kit_ramificado_conta_um_pedido(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    # 2 pedidos de faturamento. Um kit devolvido do pedido 8101 ramificou em
    # 5 linhas com o MESMO pedido_bling (uma extraviada, o resto Novo/Usado).
    await _pedido(db, bling_id=8101, situacao="83953")
    await _pedido(db, bling_id=8102, situacao="83953")
    await _devolucao(db, pedido=8101, condicao="Novo")
    await _devolucao(db, pedido=8101, condicao="Novo")
    await _devolucao(db, pedido=8101, condicao="Novo")
    await _devolucao(db, pedido=8101, condicao="Usado")
    await _devolucao(db, pedido=8101, condicao="Extraviado")  # ignorado
    admin = await _admin(db)
    auth_as(admin)

    r = await client.get("/api/financeiro/valuation", headers=_headers())
    assert r.status_code == 200, r.text
    body = r.json()
    i = _idx_mes_atual(body)
    # 1 pedido devolvido (as 4 linhas Novo/Usado do 8101 contam 1) ÷ 2 = 50.0.
    # (SUM(quantidade) daria 4 ÷ 2 = 200.0 — a regra antiga.)
    assert _taxa_total(body)[i] == 50.0
