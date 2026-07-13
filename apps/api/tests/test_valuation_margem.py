"""Página Valuation — blocos "Margem operacional" por categoria e plataforma.

Blueprint (aba eficiencia): valor = lucro do pedido (faturamento − custo) e
% = (lucro ÷ faturamento) × 100, considerando SÓ os status "a considerar":
Em andamento (15), Entregue (83953), Perdimento (83956), Resolvido (545902) e
Enviado Fake (83958). Cobre:
  * os 5 status do conjunto entram no faturamento e no custo;
  * status FORA do conjunto (Em aberto 6, Enviado Geral SP 84674) são excluídos,
    mesmo estando no faturamento amplo da aba;
  * a % é lucro ÷ faturamento (não rentabilidade ÷ custo).
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
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
    db: AsyncSession, *, bling_id: int, situacao: str, categoria: str,
    total: float, preco_custo: float, qtd: int, loja: str = "5001",
) -> None:
    db.add(BlingOrder(
        bling_id=bling_id, numero=str(bling_id),
        item_codigo=f"sku-{bling_id}", item_index=0,
        situacao=situacao, data=datetime.now(UTC), loja=loja,
        categoria_nome=categoria,
        preco_custo=preco_custo, item_quantidade=qtd,
        total=Decimal(str(total)),
    ))
    await db.commit()


def _cat_linha(body: dict, categoria: str) -> dict:
    hoje = datetime.now(UTC).date().replace(day=1).isoformat()
    secao = next(s for s in body["por_categoria"] if s["mes"] == hoje)
    return next(l for l in secao["linhas"] if l["grp"] == categoria)


@pytest.mark.asyncio
async def test_margem_categoria_inclui_os_cinco_status(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    # Celular, todos os 5 status do conjunto. fat=3000, custo=1400,
    # lucro=1600, margem=1600/3000*100=53.33%.
    await _pedido(db, bling_id=6001, situacao="83953", categoria="Celular",
                  total=1000, preco_custo=100, qtd=6)   # Entregue    lucro 400
    await _pedido(db, bling_id=6002, situacao="83958", categoria="Celular",
                  total=500, preco_custo=100, qtd=1)    # Enviado Fake lucro 400
    await _pedido(db, bling_id=6003, situacao="83956", categoria="Celular",
                  total=300, preco_custo=100, qtd=1)    # Perdimento   lucro 200
    await _pedido(db, bling_id=6004, situacao="545902", categoria="Celular",
                  total=200, preco_custo=100, qtd=1)    # Resolvido    lucro 100
    await _pedido(db, bling_id=6005, situacao="15", categoria="Celular",
                  total=1000, preco_custo=100, qtd=5)   # Em andamento lucro 500
    admin = await _admin(db)
    auth_as(admin)
    r = await client.get("/api/financeiro/valuation", headers=_headers())
    assert r.status_code == 200, r.text
    linha = _cat_linha(r.json(), "Celular")
    assert linha["rentabilidade"] == 1600.0
    assert linha["margem"] == 53.33


@pytest.mark.asyncio
async def test_margem_categoria_exclui_status_fora_do_conjunto(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    # Só o Entregue conta. Em aberto (6) e Enviado Geral SP (84674) estão no
    # faturamento amplo da aba, mas NÃO na margem operacional → excluídos.
    await _pedido(db, bling_id=6101, situacao="83953", categoria="Celular",
                  total=1000, preco_custo=100, qtd=6)   # Entregue lucro 400
    await _pedido(db, bling_id=6102, situacao="6", categoria="Celular",
                  total=9000, preco_custo=9000, qtd=1)  # Em aberto → fora
    await _pedido(db, bling_id=6103, situacao="84674", categoria="Celular",
                  total=9000, preco_custo=9000, qtd=1)  # Enviado Geral SP → fora
    admin = await _admin(db)
    auth_as(admin)
    r = await client.get("/api/financeiro/valuation", headers=_headers())
    assert r.status_code == 200, r.text
    linha = _cat_linha(r.json(), "Celular")
    assert linha["rentabilidade"] == 400.0
    assert linha["margem"] == 40.0
