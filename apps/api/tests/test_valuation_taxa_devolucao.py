"""Página Valuation — bloco Comercial, linha "Taxa de Devolução".

Taxa = (qtd de PRODUTOS devolvidos condição Novo+Usado, SUM(quantidade), por
mês de criação) ÷ (qtd de pedidos do faturamento, situações aplicáveis, por mês
da data do pedido) × 100. Cobre:
  * o denominador = pedidos do faturamento (NÃO só entregues);
  * o numerador soma `quantidade` (produtos), não conta registros; só Novo+Usado.
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


async def _devolucao(db: AsyncSession, *, condicao: str, quantidade: int) -> None:
    db.add(Devolution(
        conta="teste", condicao_produto=condicao, quantidade=quantidade,
        data=datetime.now(UTC),
    ))
    await db.commit()


def _taxa_total(body: dict) -> list:
    for q in body["comercial"]["quadros"]:
        if q["titulo"].startswith("Total"):
            for linha in q["linhas"]:
                if linha["chave"] == "taxa_devolucao":
                    return linha["valores"]
    raise AssertionError("quadro Total / linha taxa_devolucao ausente")


def _idx_mes_atual(body: dict) -> int:
    hoje = datetime.now(UTC).date().replace(day=1).isoformat()
    return body["comercial"]["quadros"][0]["meses"].index(hoje)


@pytest.mark.asyncio
async def test_taxa_denominador_faturamento_numerador_produtos(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    # Denominador = 4 pedidos de faturamento (situações aplicáveis variadas).
    await _pedido(db, bling_id=8001, situacao="83953")  # Entregue
    await _pedido(db, bling_id=8002, situacao="6")       # Em aberto
    await _pedido(db, bling_id=8003, situacao="15")      # Em andamento
    await _pedido(db, bling_id=8004, situacao="83965")   # Enviado Importado
    # Não faturamento → fora do denominador.
    await _pedido(db, bling_id=8005, situacao="12")      # Cancelado
    # Numerador = produtos Novo+Usado (SUM quantidade): 2 + 1 = 3.
    await _devolucao(db, condicao="Novo", quantidade=2)
    await _devolucao(db, condicao="Usado", quantidade=1)
    # Não conta: outra condição.
    await _devolucao(db, condicao="Extraviado", quantidade=5)
    admin = await _admin(db)
    auth_as(admin)

    r = await client.get("/api/financeiro/valuation", headers=_headers())
    assert r.status_code == 200, r.text
    body = r.json()
    i = _idx_mes_atual(body)
    # 3 produtos ÷ 4 pedidos de faturamento × 100 = 75.0.
    assert _taxa_total(body)[i] == 75.0


@pytest.mark.asyncio
async def test_taxa_conta_quantidade_nao_registros(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    # 2 pedidos de faturamento; 1 única devolução com quantidade=4.
    await _pedido(db, bling_id=8101, situacao="83953")
    await _pedido(db, bling_id=8102, situacao="83953")
    await _devolucao(db, condicao="Usado", quantidade=4)
    admin = await _admin(db)
    auth_as(admin)

    r = await client.get("/api/financeiro/valuation", headers=_headers())
    assert r.status_code == 200, r.text
    body = r.json()
    i = _idx_mes_atual(body)
    # 4 produtos ÷ 2 pedidos × 100 = 200.0 (COUNT(*) daria 1 registro → 50.0).
    assert _taxa_total(body)[i] == 200.0
