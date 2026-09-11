"""Comercial agrupa Equipe 1/2 independentemente dos códigos de acesso.

Taxas usam pedidos distintos, inclusive kits, e preservam o total ponderado.
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


async def _loja(
    db: AsyncSession, owner: User, *, loja: str, team: int, commercial_team: int = 1,
) -> None:
    db.add(StoreInfo(
        user_id=owner.id, platform="ml",
        account_name=f"loja-{loja}", bling_store_id=loja,
        sales_team=team, commercial_team=commercial_team,
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
async def test_comercial_agrupa_equipes_sem_expor_membros(
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

    assert [e["label"] for e in com["empresas"]] == ["Equipe 1", "Equipe 2"]
    emp, vazia = com["empresas"]
    assert emp["membros"] == vazia["membros"] == []
    # Dois grupos de acesso somados na Equipe 1: taxa ponderada 2/6,
    # sem calcular a média incorreta das taxas 50% e 25%.
    assert emp["aguardando_devolucao"][i] == 500.0
    assert com["total_aguardando_devolucao"][i] == 500.0
    assert emp["taxa_devolucao"][i] == com["total_taxa_devolucao"][i] == 33.33
    assert vazia["aguardando_devolucao"][i] == 0.0
    assert vazia["taxa_devolucao"][i] is None


@pytest.mark.asyncio
async def test_comercial_organizacao_independe_dos_codigos_de_acesso(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    admin = await _admin(db)
    # Códigos de acesso distintos não determinam a organização comercial.
    await _loja(db, admin, loja="8001", team=101)
    await _loja(db, admin, loja="8002", team=201, commercial_team=1)
    await _loja(db, admin, loja="8003", team=101, commercial_team=2)
    for bid, loja in ((8101, "8001"), (8201, "8002"), (8202, "8003")):
        await _pedido(db, bling_id=bid, loja=loja, situacao="83953")
    auth_as(admin)

    r = await client.get("/api/financeiro/valuation", headers=_headers())
    assert r.status_code == 200, r.text
    com = r.json()["comercial"]

    labels = {e["label"]: e for e in com["empresas"]}
    assert set(labels) == {"Equipe 1", "Equipe 2"}
    assert {e["empresa"] for e in com["empresas"]} == {1, 2}
    assert all(e["membros"] == [] for e in com["empresas"])
    # Pedido aguardando devolução comprova qual agrupamento recebeu a loja.
    await _pedido(db, bling_id=8301, loja="8002", situacao="83957", total=150)
    await _pedido(db, bling_id=8302, loja="8003", situacao="83957", total=350)
    r = await client.get("/api/financeiro/valuation", headers=_headers())
    assert r.status_code == 200, r.text
    i = _idx_mes_atual(r.json())
    com = r.json()["comercial"]
    labels = {e["label"]: e for e in com["empresas"]}
    assert labels["Equipe 1"]["aguardando_devolucao"][i] == 150
    assert labels["Equipe 2"]["aguardando_devolucao"][i] == 350
    assert com["total_aguardando_devolucao"][i] == 500


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

    # Equipes vazias continuam visíveis e órfãos mantêm o total geral.
    labels = {e["label"]: e for e in com["empresas"]}
    assert set(labels) == {"Equipe 1", "Equipe 2", "Sem equipe"}
    assert labels["Sem equipe"]["membros"] == []
    # Total = 1 ÷ 2 × 100 = 50.
    assert com["total_taxa_devolucao"][i] == 50.0
