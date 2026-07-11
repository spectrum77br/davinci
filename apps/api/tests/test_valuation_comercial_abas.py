"""Página Valuation — bloco Comercial em abas (empresa → membros).

Hoje há UMA empresa: cada `store_info.sales_team` vira um membro dela, rotulado
"1.<rank>". A saída tem: `total_*` (Total geral), e `empresas[]` com o subtotal
da empresa + `membros[]`. Duas métricas por mês: `cancelamento` (R$ aguardando
cancelamento) e `taxa_devolucao` (%). Cobre:
  * agrupamento empresa → membros com rótulo "1.1"/"1.2";
  * taxa por membro (devoluções Novo+Usado ÷ pedidos do faturamento do membro);
  * Total geral somando todos os membros;
  * cancelamento (R$) por membro.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, Devolution, StoreInfo, User, UserRole, UserStatus
from app.routers.financeiro import _make_valuation_token


async def _admin(db: AsyncSession) -> User:
    email = f"aba-{uuid.uuid4().hex[:6]}@davinci-test.com"
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


async def _loja(db: AsyncSession, owner: User, *, loja: str, team: int) -> None:
    db.add(StoreInfo(
        user_id=owner.id, platform="ml",
        account_name=f"loja-{loja}", bling_store_id=loja, sales_team=team,
    ))
    await db.commit()


async def _pedido(db: AsyncSession, *, bling_id: int, loja: str, situacao: str,
                  total: float = 100.0) -> None:
    db.add(BlingOrder(
        bling_id=bling_id, numero=str(bling_id), item_codigo=f"s-{bling_id}",
        item_index=0, situacao=situacao, data=datetime.now(UTC),
        loja=loja, total=total,
    ))
    await db.commit()


async def _devolucao(db: AsyncSession, *, pedido: int, condicao: str,
                     quantidade: int) -> None:
    db.add(Devolution(
        conta="teste", pedido_bling=str(pedido), condicao_produto=condicao,
        quantidade=quantidade, data=datetime.now(UTC),
    ))
    await db.commit()


def _idx_mes_atual(body: dict) -> int:
    hoje = datetime.now(UTC).date().replace(day=1).isoformat()
    return body["comercial"]["meses"].index(hoje)


@pytest.mark.asyncio
async def test_comercial_agrupa_empresa_membros(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    admin = await _admin(db)
    await _loja(db, admin, loja="9001", team=1)
    await _loja(db, admin, loja="9002", team=2)
    # Membro 1.1 (equipe 1): 2 pedidos faturamento; 1 produto devolvido → 50%.
    await _pedido(db, bling_id=9101, loja="9001", situacao="83953")
    await _pedido(db, bling_id=9102, loja="9001", situacao="6")
    await _devolucao(db, pedido=9101, condicao="Novo", quantidade=1)
    # + 1 pedido aguardando cancelamento (83955) → cancelamento R$ 500 (não é faturamento).
    await _pedido(db, bling_id=9103, loja="9001", situacao="83955", total=500.0)
    # Membro 1.2 (equipe 2): 4 pedidos faturamento; 3 produtos devolvidos → 75%.
    await _pedido(db, bling_id=9201, loja="9002", situacao="83953")
    await _pedido(db, bling_id=9202, loja="9002", situacao="83953")
    await _pedido(db, bling_id=9203, loja="9002", situacao="6")
    await _pedido(db, bling_id=9204, loja="9002", situacao="15")
    await _devolucao(db, pedido=9201, condicao="Usado", quantidade=3)
    auth_as(admin)

    r = await client.get("/api/financeiro/valuation", headers=_headers())
    assert r.status_code == 200, r.text
    com = r.json()["comercial"]
    i = _idx_mes_atual(r.json())

    # Uma empresa, dois membros rotulados 1.1 / 1.2.
    assert len(com["empresas"]) == 1
    emp = com["empresas"][0]
    assert emp["label"] == "Empresa 1"
    membros = {m["label"]: m for m in emp["membros"]}
    assert set(membros) == {"1.1", "1.2"}

    # Taxa por membro.
    assert membros["1.1"]["taxa_devolucao"][i] == 50.0   # 1 ÷ 2 × 100
    assert membros["1.2"]["taxa_devolucao"][i] == 75.0   # 3 ÷ 4 × 100
    # Cancelamento (R$) no membro 1.1.
    assert membros["1.1"]["cancelamento"][i] == 500.0
    assert membros["1.2"]["cancelamento"][i] == 0.0

    # Total geral = 4 produtos ÷ 6 pedidos × 100 = 66.67; subtotal da empresa idem.
    assert com["total_taxa_devolucao"][i] == 66.67
    assert emp["taxa_devolucao"][i] == 66.67


@pytest.mark.asyncio
async def test_comercial_sem_equipe_vira_aba_sem_membros(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    admin = await _admin(db)
    # Pedido numa loja SEM store_info → cai em "Sem equipe".
    await _pedido(db, bling_id=9301, loja="7777", situacao="83953")
    await _pedido(db, bling_id=9302, loja="7777", situacao="83953")
    await _devolucao(db, pedido=9301, condicao="Novo", quantidade=1)
    auth_as(admin)

    r = await client.get("/api/financeiro/valuation", headers=_headers())
    assert r.status_code == 200, r.text
    com = r.json()["comercial"]
    i = _idx_mes_atual(r.json())

    # Sem nenhum sales_team → não há "Empresa 1"; só "Sem equipe" (sem membros).
    labels = {e["label"]: e for e in com["empresas"]}
    assert "Sem equipe" in labels
    assert labels["Sem equipe"]["membros"] == []
    # Total = 1 ÷ 2 × 100 = 50.
    assert com["total_taxa_devolucao"][i] == 50.0
