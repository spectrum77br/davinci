"""Aba Faturamento — endpoint GET /api/faturamento.

Cobre:
  * Admin vê todas as lojas; user com sales_teams=[X] vê só lojas com
    StoreInfo.sales_team=X.
  * Dedup por bling_id: pedido com 2 itens conta como 1 pedido e usa
    max(total) (não a soma das linhas).
  * situacao faturável ('6', '15', '83953') conta; demais são ignoradas; o
    filtro de período funciona.
  * Filtro "Equipe" = equipe de vendas com nome (30/10/2026): admin vê
    Londres/Israel/Marrocos, 102 traz só Israel, quem é de equipe não pede
    a de outra (403).
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingOrder,
    Company,
    Marketplace,
    Store,
    StoreInfo,
    StoreStatus,
    User,
    UserRole,
    UserStatus,
)

PERM = {"faturamento": {"view": True, "edit": False, "delete": False}}


async def _seed_user(
    db: AsyncSession, *, role: UserRole, sales_teams: list[int] | None,
) -> User:
    email = f"ft-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(
        open_id=f"email:{email}", email=email,
        role=role, status=UserStatus.ACTIVE,
        permissions=PERM, sales_teams=sales_teams,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _seed_lojas(db: AsyncSession, owner: User) -> dict[str, dict]:
    """Cria 2 lojas (ml/shopee) + companies + StoreInfo com sales_team."""
    co = Company(razao_social="C", apelido="c")
    db.add(co)
    await db.flush()
    out: dict[str, dict] = {}
    plan = [
        ("ml", 1001, "Conta ML", "ml", 1),
        ("shopee", 1002, "Conta Shopee", "shopee", 2),
    ]
    for key, bling_id, account, plat, team in plan:
        s = Store(
            company_id=co.id,
            marketplace=Marketplace(key),
            bling_store_id=bling_id,
            status=StoreStatus.ACTIVE,
        )
        db.add(s)
        await db.flush()
        si = StoreInfo(
            user_id=owner.id, platform=plat,
            account_name=account,
            bling_store_id=str(bling_id),
            sales_team=team, commercial_team=team,
        )
        db.add(si)
        await db.flush()
        out[key] = {"store_id": str(si.id), "bling_store_id": bling_id, "team": team}
    await db.commit()
    return out


async def _seed_pedido(
    db: AsyncSession, *, bling_id: int, store_id, total: Decimal,
    data: datetime, situacao: str = "83953", n_itens: int = 1,
) -> None:
    """Cria 1 pedido com `n_itens` linhas em bling_orders (uma por item).
    `total` se repete em cada linha — espelha o cenário de prod."""
    loja = await db.get(StoreInfo, uuid.UUID(store_id))
    for i in range(n_itens):
        db.add(BlingOrder(
            bling_id=bling_id, numero=str(bling_id),
            item_codigo=f"sku-{bling_id}-{i}", item_index=i,
            situacao=situacao, total=total, data=data,
            loja=loja.bling_store_id,
        ))
    await db.commit()


_HOJE = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)
_PERIODO = (
    (_HOJE - timedelta(days=30)).isoformat(),
    (_HOJE + timedelta(days=1)).isoformat(),
)


@pytest_asyncio.fixture
async def owner(db: AsyncSession) -> User:
    return await _seed_user(db, role=UserRole.ADMIN, sales_teams=None)


@pytest.mark.asyncio
async def test_admin_ve_todas_as_lojas(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], owner: User,
):
    lojas = await _seed_lojas(db, owner)
    await _seed_pedido(
        db, bling_id=9001, store_id=lojas["ml"]["store_id"],
        total=Decimal("100.00"), data=_HOJE,
    )
    await _seed_pedido(
        db, bling_id=9002, store_id=lojas["shopee"]["store_id"],
        total=Decimal("250.00"), data=_HOJE,
    )
    admin = await _seed_user(db, role=UserRole.ADMIN, sales_teams=None)
    auth_as(admin)

    r = await client.get(
        "/api/faturamento",
        params={"start": _PERIODO[0], "end": _PERIODO[1]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    store_ids = {it["store_id"] for it in body["itens"]}
    assert lojas["ml"]["store_id"] in store_ids
    assert lojas["shopee"]["store_id"] in store_ids
    assert body["total_pedidos"] == 2
    assert Decimal(str(body["total_faturamento"])) == Decimal("350.00")


@pytest.mark.asyncio
async def test_user_com_equipe_ve_so_lojas_da_equipe(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], owner: User,
):
    lojas = await _seed_lojas(db, owner)
    await _seed_pedido(
        db, bling_id=9011, store_id=lojas["ml"]["store_id"],
        total=Decimal("100.00"), data=_HOJE,
    )
    await _seed_pedido(
        db, bling_id=9012, store_id=lojas["shopee"]["store_id"],
        total=Decimal("250.00"), data=_HOJE,
    )
    user = await _seed_user(db, role=UserRole.USER, sales_teams=[1])
    auth_as(user)

    r = await client.get(
        "/api/faturamento",
        params={"start": _PERIODO[0], "end": _PERIODO[1]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["itens"]) == 1
    assert body["itens"][0]["store_id"] == lojas["ml"]["store_id"]
    assert body["total_pedidos"] == 1
    assert Decimal(str(body["total_faturamento"])) == Decimal("100.00")


@pytest.mark.asyncio
async def test_dedup_por_bling_id_usa_max_total(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], owner: User,
):
    """Pedido com 2 itens → 1 pedido, total = max(total) (não soma).
    bling_orders.total repete em cada linha do mesmo bling_id."""
    lojas = await _seed_lojas(db, owner)
    await _seed_pedido(
        db, bling_id=9021, store_id=lojas["ml"]["store_id"],
        total=Decimal("150.00"), data=_HOJE, n_itens=3,
    )
    admin = await _seed_user(db, role=UserRole.ADMIN, sales_teams=None)
    auth_as(admin)

    r = await client.get(
        "/api/faturamento",
        params={"start": _PERIODO[0], "end": _PERIODO[1]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_pedidos"] == 1
    assert Decimal(str(body["total_faturamento"])) == Decimal("150.00")


@pytest.mark.asyncio
async def test_ignora_outras_situacoes_e_fora_do_periodo(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], owner: User,
):
    lojas = await _seed_lojas(db, owner)
    # Status faturáveis, dentro do período → contam.
    await _seed_pedido(
        db, bling_id=9031, store_id=lojas["ml"]["store_id"],
        total=Decimal("100.00"), data=_HOJE, situacao="83953",
    )
    await _seed_pedido(
        db, bling_id=9032, store_id=lojas["ml"]["store_id"],
        total=Decimal("200.00"), data=_HOJE, situacao="6",
    )
    await _seed_pedido(
        db, bling_id=9033, store_id=lojas["ml"]["store_id"],
        total=Decimal("300.00"), data=_HOJE, situacao="15",
    )
    # Outra situação no mesmo período → não conta.
    await _seed_pedido(
        db, bling_id=9034, store_id=lojas["ml"]["store_id"],
        total=Decimal("777.00"), data=_HOJE, situacao="12",
    )
    # Faturável mas fora do período → não conta.
    await _seed_pedido(
        db, bling_id=9035, store_id=lojas["ml"]["store_id"],
        total=Decimal("500.00"), data=_HOJE - timedelta(days=120),
    )
    admin = await _seed_user(db, role=UserRole.ADMIN, sales_teams=None)
    auth_as(admin)

    r = await client.get(
        "/api/faturamento",
        params={"start": _PERIODO[0], "end": _PERIODO[1]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_pedidos"] == 3
    assert Decimal(str(body["total_faturamento"])) == Decimal("600.00")


@pytest.mark.asyncio
async def test_mesma_equipe_comercial_preserva_contas_individuais(
    db, client, auth_as, owner,
):
    lojas = await _seed_lojas(db, owner)
    await db.execute(update(StoreInfo).values(commercial_team=1))
    user = await _seed_user(db, role=UserRole.USER, sales_teams=[1])
    user.commercial_team = 1
    await db.commit()
    auth_as(user)
    for filtro in (None, 1):
        params = {"start": _PERIODO[0], "end": _PERIODO[1]}
        if filtro is not None:
            params["team"] = filtro
        response = await client.get("/api/faturamento", params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["teams"] == [1]
        assert {row["store_id"] for row in body["itens"]} == {lojas["ml"]["store_id"]}
    response = await client.get("/api/faturamento", params={"team": 2})
    assert response.status_code == 403
    # Alterar apenas a equipe organizacional do usuário não muda o acesso.
    user.commercial_team = 2
    await db.commit()
    response = await client.get("/api/faturamento", params={"team": 1})
    assert response.status_code == 200, response.text
    assert {row["store_id"] for row in response.json()["itens"]} == {lojas["ml"]["store_id"]}


# ── Filtro "Equipe" pelas equipes de vendas (30/10/2026) ────────────────────
# 101 Londres e 102 Israel (Celular/Eletro), 103 Marrocos (Mala). A comercial
# é 1 em toda loja, como na produção: se ela voltasse a ser o filtro, a tela
# teria uma equipe só e o <select> sumiria.
async def _seed_tres_equipes(db: AsyncSession, owner: User) -> dict[str, str]:
    plano = (
        ("londres_ml", "2101", "ml", 101),
        ("londres_shopee", "2102", "shopee", 101),
        ("israel_ml", "2201", "ml", 102),
        ("israel_amazon", "2202", "amazon", 102),
        ("marrocos_ml", "2301", "ml", 103),
        ("sem_equipe", "2401", "magalu", None),
    )
    lojas: dict[str, str] = {}
    for chave, bling_store_id, plataforma, equipe in plano:
        si = StoreInfo(
            user_id=owner.id,
            platform=plataforma,
            account_name=chave,
            bling_store_id=bling_store_id,
            sales_team=equipe,
            commercial_team=1,
        )
        db.add(si)
        await db.flush()
        lojas[chave] = str(si.id)
    await db.commit()
    return lojas


async def _seed_vendas_das_equipes(db: AsyncSession, lojas: dict[str, str]) -> None:
    vendas = (
        (9101, "londres_ml", "100.00", 1),
        (9201, "israel_ml", "200.00", 2),
        (9202, "israel_amazon", "50.00", 1),
        (9301, "marrocos_ml", "400.00", 1),
    )
    for bling_id, chave, total, n_itens in vendas:
        await _seed_pedido(
            db,
            bling_id=bling_id,
            store_id=lojas[chave],
            total=Decimal(total),
            data=_HOJE,
            n_itens=n_itens,
        )
    # Pedido de loja sem cadastro: só entra na visão sem filtro de equipe.
    db.add(
        BlingOrder(
            bling_id=9901,
            numero="9901",
            item_codigo="sku-orfao",
            item_index=0,
            situacao="83953",
            total=Decimal("70.00"),
            data=_HOJE,
            loja="9999",
        )
    )
    await db.commit()


def _faturamento_params(**extra) -> dict:
    return {"start": _PERIODO[0], "end": _PERIODO[1], **extra}


_TRES_EQUIPES = [
    {"numero": 101, "nome": "Londres"},
    {"numero": 102, "nome": "Israel"},
    {"numero": 103, "nome": "Marrocos"},
]


@pytest.mark.asyncio
async def test_equipe_admin_ve_londres_israel_marrocos(
    db: AsyncSession, client: AsyncClient, auth_as: Callable[[User | None], None], owner: User
):
    lojas = await _seed_tres_equipes(db, owner)
    await _seed_vendas_das_equipes(db, lojas)
    auth_as(await _seed_user(db, role=UserRole.ADMIN, sales_teams=None))

    r = await client.get("/api/faturamento", params=_faturamento_params())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["equipes"] == _TRES_EQUIPES
    # A lista antiga, só com os números, segue igual para quem já a lê.
    assert body["teams"] == [101, 102, 103]
    assert body["team"] is None
    # Sem filtro: todas as lojas (até a sem equipe) e o pedido órfão.
    ids = {it["store_id"] for it in body["itens"]}
    assert set(lojas.values()) <= ids
    assert "9999" in ids
    assert body["total_pedidos"] == 5
    assert Decimal(str(body["total_faturamento"])) == Decimal("820.00")

    # A equipe comercial (1 em toda loja) não é mais o filtro.
    r = await client.get("/api/faturamento", params=_faturamento_params(team=1))
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "team_not_allowed"


@pytest.mark.asyncio
async def test_equipe_filtro_102_traz_so_lojas_de_israel(
    db: AsyncSession, client: AsyncClient, auth_as: Callable[[User | None], None], owner: User
):
    lojas = await _seed_tres_equipes(db, owner)
    await _seed_vendas_das_equipes(db, lojas)
    auth_as(await _seed_user(db, role=UserRole.ADMIN, sales_teams=None))

    r = await client.get("/api/faturamento", params=_faturamento_params(team=102))
    assert r.status_code == 200, r.text
    body = r.json()
    israel = {lojas["israel_ml"], lojas["israel_amazon"]}
    assert {it["store_id"] for it in body["itens"]} == israel
    # O pedido de 2 itens conta uma vez; Londres, Marrocos e o órfão ficam fora.
    assert body["total_pedidos"] == 2
    assert Decimal(str(body["total_faturamento"])) == Decimal("250.00")
    assert body["team"] == 102
    # As opções não encolhem com o filtro: dá para trocar de equipe.
    assert body["equipes"] == _TRES_EQUIPES


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("equipes_do_usuario", "outras"), (([102], (101, 103)), ([101, 102], (103,)))
)
async def test_equipe_usuario_de_equipe_nao_pede_a_de_outra(
    db: AsyncSession,
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
    owner: User,
    equipes_do_usuario: list[int],
    outras: tuple[int, ...],
):
    lojas = await _seed_tres_equipes(db, owner)
    await _seed_vendas_das_equipes(db, lojas)
    auth_as(await _seed_user(db, role=UserRole.USER, sales_teams=equipes_do_usuario))
    das_equipes = {
        101: {lojas["londres_ml"], lojas["londres_shopee"]},
        102: {lojas["israel_ml"], lojas["israel_amazon"]},
    }

    r = await client.get("/api/faturamento", params=_faturamento_params())
    assert r.status_code == 200, r.text
    body = r.json()
    # Só as lojas dele, sem a sem-equipe nem o órfão; o filtro só oferece as
    # equipes dele (uma só → a tela esconde o <select>).
    minhas = set().union(*(das_equipes[n] for n in equipes_do_usuario))
    assert {it["store_id"] for it in body["itens"]} == minhas
    assert body["equipes"] == [e for e in _TRES_EQUIPES if e["numero"] in equipes_do_usuario]
    assert body["teams"] == equipes_do_usuario

    r = await client.get("/api/faturamento", params=_faturamento_params(team=102))
    assert r.status_code == 200, r.text
    assert {it["store_id"] for it in r.json()["itens"]} == das_equipes[102]

    for outra in outras:
        r = await client.get("/api/faturamento", params=_faturamento_params(team=outra))
        assert r.status_code == 403, r.text
        assert r.json()["detail"]["code"] == "team_not_allowed"
