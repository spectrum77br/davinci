"""Página Valuation — bloco Comercial em abas (empresa → membros).

Hoje há UMA empresa: cada `store_info.sales_team` vira um membro dela, rotulado
"1.<rank>". A saída tem: `total_*` (Total geral), e `empresas[]` com o subtotal
da empresa + `membros[]`. Duas métricas por mês: `aguardando_devolucao` (R$ dos
pedidos em Aguardando Devolução) e `taxa_devolucao` (%). Cobre:
  * agrupamento empresa → membros com rótulo "1.1"/"1.2";
  * taxa por membro (PEDIDOS devolvidos Novo+Usado, distinct pedido_bling ÷
    pedidos do faturamento do membro — um kit ramificado conta 1);
  * Total geral somando todos os membros;
  * aguardando devolução (R$) por membro.
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
    # Equipes codificadas empresa.membro: 101 = 1.1, 102 = 1.2.
    await _loja(db, admin, loja="9001", team=101)
    await _loja(db, admin, loja="9002", team=102)
    # Membro 1.1 (equipe 101): 2 pedidos faturamento; 1 pedido devolvido → 50%.
    await _pedido(db, bling_id=9101, loja="9001", situacao="83953")
    await _pedido(db, bling_id=9102, loja="9001", situacao="6")
    await _devolucao(db, pedido=9101, condicao="Novo", quantidade=1)
    # + 1 pedido aguardando devolução (83957) → aguardando_devolucao R$ 500 (não é faturamento).
    await _pedido(db, bling_id=9103, loja="9001", situacao="83957", total=500.0)
    # Membro 1.2 (equipe 102): 4 pedidos faturamento; kit do pedido 9201 ramificou
    # em 2 linhas (mesmo pedido) → conta 1 pedido devolvido → 25%.
    await _pedido(db, bling_id=9201, loja="9002", situacao="83953")
    await _pedido(db, bling_id=9202, loja="9002", situacao="83953")
    await _pedido(db, bling_id=9203, loja="9002", situacao="6")
    await _pedido(db, bling_id=9204, loja="9002", situacao="15")
    await _devolucao(db, pedido=9201, condicao="Usado", quantidade=1)
    await _devolucao(db, pedido=9201, condicao="Novo", quantidade=1)
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

    # Taxa por membro (pedidos distintos devolvidos ÷ pedidos faturamento).
    assert membros["1.1"]["taxa_devolucao"][i] == 50.0   # 1 pedido ÷ 2 × 100
    assert membros["1.2"]["taxa_devolucao"][i] == 25.0   # 1 pedido ÷ 4 × 100
    # Aguardando Devolução (R$) no membro 1.1.
    assert membros["1.1"]["aguardando_devolucao"][i] == 500.0
    assert membros["1.2"]["aguardando_devolucao"][i] == 0.0

    # Total geral = 2 pedidos devolvidos ({9101, 9201}) ÷ 6 pedidos × 100 =
    # 33.33; subtotal da empresa idem.
    assert com["total_taxa_devolucao"][i] == 33.33
    assert emp["taxa_devolucao"][i] == 33.33


@pytest.mark.asyncio
async def test_comercial_multiplas_empresas(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    admin = await _admin(db)
    # Empresa 1: membro 1.1 (101). Empresa 2: membros 2.1 (201) e 2.2 (202).
    await _loja(db, admin, loja="8001", team=101)
    await _loja(db, admin, loja="8002", team=201)
    await _loja(db, admin, loja="8003", team=202)
    for bid, loja in ((8101, "8001"), (8201, "8002"), (8202, "8003")):
        await _pedido(db, bling_id=bid, loja=loja, situacao="83953")
    auth_as(admin)

    r = await client.get("/api/financeiro/valuation", headers=_headers())
    assert r.status_code == 200, r.text
    com = r.json()["comercial"]

    labels = {e["label"]: e for e in com["empresas"]}
    assert "Empresa 1" in labels and "Empresa 2" in labels
    assert {e["empresa"] for e in com["empresas"] if e["empresa"] is not None} == {1, 2}
    m1 = {m["label"] for m in labels["Empresa 1"]["membros"]}
    m2 = {m["label"] for m in labels["Empresa 2"]["membros"]}
    assert m1 == {"1.1"}
    assert m2 == {"2.1", "2.2"}


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
