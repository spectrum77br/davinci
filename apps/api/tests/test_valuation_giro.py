"""Página Valuation — linha "Giro de Venda" do bloco Operacional.

Giro = (custo de venda dos produtos faturados no mês ÷ valor de estoque do mês)
× 100. Numerador = SUM(preço de custo × quantidade) dos pedidos em situação de
faturamento (exclui lojas internas); denominador = snapshot `valuation.estoque`
do mês. Cobre:
  * o cálculo × 100 (percentual, não fração) no mês corrente;
  * só situações de faturamento entram no numerador; loja interna é ignorada;
  * mês sem snapshot de estoque → None ("—").
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, User, UserRole, UserStatus
from app.routers.financeiro import _make_valuation_token


async def _admin(db: AsyncSession) -> User:
    email = f"val-{uuid.uuid4().hex[:6]}@davinci-test.com"
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


async def _pedido(
    db: AsyncSession, *, bling_id: int, loja: str, situacao: str,
    preco_custo: float, qtd: int, n_itens: int = 1,
) -> None:
    now = datetime.now(UTC)
    for i in range(n_itens):
        db.add(BlingOrder(
            bling_id=bling_id, numero=str(bling_id),
            item_codigo=f"sku-{bling_id}-{i}", item_index=i,
            situacao=situacao, data=now, loja=loja,
            preco_custo=preco_custo, item_quantidade=qtd,
            total=Decimal("100.00"),
        ))
    await db.commit()


async def _snapshot_estoque(db: AsyncSession, estoque: float) -> None:
    # Tabela sem ORM model; INSERT não-qualificado resolve pelo search_path do
    # engine de teste (davinci_test,public).
    now = datetime.now(UTC)
    await db.execute(text(
        "INSERT INTO valuation (data, caixa, estoque, receber) "
        "VALUES (:d, :c, :e, :r)"
    ).bindparams(d=now.date(), c=0, e=estoque, r=0))
    await db.commit()


def _giro_linha(body: dict) -> dict:
    for linha in body["operacional"]["linhas"]:
        if linha["chave"] == "giro_venda":
            return linha
    raise AssertionError("linha giro_venda ausente do operacional")


def _idx_mes_atual(body: dict) -> int:
    hoje_mes = datetime.now(UTC).date().replace(day=1).isoformat()
    return body["operacional"]["meses"].index(hoje_mes)


@pytest.mark.asyncio
async def test_giro_venda_multiplica_por_100(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    # Estoque do mês = 1000. Faturamento: 100×3 + 100×2 = 500 de custo.
    await _snapshot_estoque(db, 1000.0)
    await _pedido(db, bling_id=7001, loja="5001", situacao="83953",
                  preco_custo=100.0, qtd=3)
    await _pedido(db, bling_id=7002, loja="5001", situacao="6",
                  preco_custo=100.0, qtd=2)
    admin = await _admin(db)
    auth_as(admin)

    r = await client.get("/api/financeiro/valuation", headers=_headers())
    assert r.status_code == 200, r.text
    body = r.json()
    linha = _giro_linha(body)
    assert linha["formato"] == "pct"
    i = _idx_mes_atual(body)
    # 500 / 1000 * 100 = 50.0 (percentual), NÃO 0.5.
    assert linha["valores"][i] == 50.0


@pytest.mark.asyncio
async def test_giro_ignora_nao_faturamento_e_loja_interna(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    await _snapshot_estoque(db, 1000.0)
    # Conta (faturamento, loja normal): 200 de custo.
    await _pedido(db, bling_id=7101, loja="5001", situacao="83953",
                  preco_custo=100.0, qtd=2)
    # Não conta: situação fora de faturamento (Cancelado 12).
    await _pedido(db, bling_id=7102, loja="5001", situacao="12",
                  preco_custo=100.0, qtd=9)
    # Não conta: loja interna ignorada.
    await _pedido(db, bling_id=7103, loja="205632678", situacao="83953",
                  preco_custo=100.0, qtd=9)
    admin = await _admin(db)
    auth_as(admin)

    r = await client.get("/api/financeiro/valuation", headers=_headers())
    assert r.status_code == 200, r.text
    linha = _giro_linha(r.json())
    i = _idx_mes_atual(r.json())
    # Só os 200 do pedido válido → 200 / 1000 * 100 = 20.0.
    assert linha["valores"][i] == 20.0


@pytest.mark.asyncio
async def test_giro_none_sem_snapshot_estoque(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    # Faturamento existe, mas NÃO há snapshot de estoque → sem denominador → "—".
    await _pedido(db, bling_id=7201, loja="5001", situacao="83953",
                  preco_custo=100.0, qtd=2)
    admin = await _admin(db)
    auth_as(admin)

    r = await client.get("/api/financeiro/valuation", headers=_headers())
    assert r.status_code == 200, r.text
    linha = _giro_linha(r.json())
    i = _idx_mes_atual(r.json())
    assert linha["valores"][i] is None
