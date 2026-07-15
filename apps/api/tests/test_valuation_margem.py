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

from app.models import (
    BlingOrder,
    User,
    UserRole,
    UserStatus,
)
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


async def _estoque_snapshot(db: AsyncSession, *, por_local: dict) -> None:
    """Snapshot de Estoque Bling de HOJE (denominador do Giro). por_local no
    formato {"SP": {"qtd": .., "valor": ..}, ...}."""
    import json

    from sqlalchemy import text

    total_valor = sum(float(v.get("valor") or 0) for v in por_local.values())
    await db.execute(
        text(
            "INSERT INTO valuation_estoque_bling_diario"
            " (data, total_qtd, total_valor, por_local)"
            " VALUES (:d, 0, :tv, CAST(:pl AS jsonb))"
        ),
        {"d": datetime.now(UTC).date(), "tv": total_valor,
         "pl": json.dumps(por_local)},
    )
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


@pytest.mark.asyncio
async def test_margem_categoria_funde_usado_no_normal(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    # Planta não tem "Usado": Celular Usado → Celular e Mala Usada → Mala.
    # Celular: 1000-600=400 ; Celular Usado: 500-100=400 → funde em Celular
    # fat=1500 custo=700 lucro=800 margem=800/1500*100=53.33.
    await _pedido(db, bling_id=6201, situacao="83953", categoria="Celular",
                  total=1000, preco_custo=100, qtd=6)
    await _pedido(db, bling_id=6202, situacao="83953", categoria="Celular Usado",
                  total=500, preco_custo=100, qtd=1)
    # Mala: 1000-600=400 ; Mala Usada: 200-100=100 → funde em Mala
    # fat=1200 custo=700 lucro=500 margem=500/1200*100=41.67.
    await _pedido(db, bling_id=6203, situacao="83953", categoria="Mala",
                  total=1000, preco_custo=100, qtd=6)
    await _pedido(db, bling_id=6204, situacao="83953", categoria="Mala Usada",
                  total=200, preco_custo=100, qtd=1)
    admin = await _admin(db)
    auth_as(admin)
    r = await client.get("/api/financeiro/valuation", headers=_headers())
    assert r.status_code == 200, r.text
    body = r.json()
    hoje = datetime.now(UTC).date().replace(day=1).isoformat()
    secao = next(s for s in body["por_categoria"] if s["mes"] == hoje)
    grupos = {l["grp"] for l in secao["linhas"]}
    assert "Celular Usado" not in grupos
    assert "Mala Usada" not in grupos
    assert _cat_linha(body, "Celular")["rentabilidade"] == 800.0
    assert _cat_linha(body, "Celular")["margem"] == 53.33
    assert _cat_linha(body, "Mala")["rentabilidade"] == 500.0
    assert _cat_linha(body, "Mala")["margem"] == 41.67


@pytest.mark.asyncio
async def test_margem_categoria_giro(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    # Giro por categoria: giro % = (custo dos produtos vendidos da categoria ÷
    # estoque Bling do bucket no fim do mês) × 100. O denominador vem do
    # snapshot valuation_estoque_bling_diario: bucket Celular = soma dos 7
    # armazéns (PI/SA/SP/RA/CD/CI/US). Kit compartilha o estoque da base
    # (Celular Kit → Celular).
    admin = await _admin(db)
    # Estoque Bling: Celular = SP 1200 + CI 800 = 2000 (soma dos armazéns).
    await _estoque_snapshot(db, por_local={
        "SP": {"qtd": 12, "valor": 1200},
        "CI": {"qtd": 8, "valor": 800},
    })
    # Venda Celular (83953=Entregue, está no giro E na margem):
    #   fat=1000, custo=600, lucro=400, margem=40; custo vendido=600
    #   giro=600/2000*100=30.
    await _pedido(db, bling_id=6301, situacao="83953", categoria="Celular",
                  total=1000, preco_custo=100, qtd=6)
    # Venda Celular Kit (usa o estoque de Celular=2000):
    #   fat=500, custo=100, lucro=400, margem=80; custo vendido=100
    #   giro=100/2000*100=5.
    await _pedido(db, bling_id=6302, situacao="83953", categoria="Celular Kit",
                  total=500, preco_custo=50, qtd=2)
    auth_as(admin)
    r = await client.get("/api/financeiro/valuation", headers=_headers())
    assert r.status_code == 200, r.text
    body = r.json()
    cel = _cat_linha(body, "Celular")
    assert cel["giro"] == 30.0
    assert cel["margem"] == 40.0
    assert "giro_valor" not in cel
    assert "rentabilidade_final" not in cel
    kit = _cat_linha(body, "Celular Kit")
    assert kit["giro"] == 5.0          # estoque-base = Celular (2000)
    assert kit["margem"] == 80.0
