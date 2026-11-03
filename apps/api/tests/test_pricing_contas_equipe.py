"""Coluna "Equipe" da Tabela de Preços › Contas (03/11/2026).

Cada conta sai com as equipes de vendas (101 Londres, 102 Israel, 103
Marrocos) que a enxergam, pela MESMA régua da cerca (`escopo_das_equipes`):
a loja apontada (`store_info_id`) ou a integração usada. A coluna de catálogo
sai com as da base; editar a conta devolve as equipes de novo (a tela troca a
linha pela resposta).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.team_scope import escopo_das_equipes, resolve_team_scope
from app.models import (
    Integration,
    IntegrationPlatform,
    PricingAccount,
    PricingPlatform,
    Segment,
    StoreInfo,
    User,
    UserRole,
    UserStatus,
)
from app.models.pricing import CANAL_CATALOGO
from app.security.cipher import encrypt_json

PERM_FULL = {
    "tabela_precos": {"view": True, "edit": True, "delete": True},
    "tabela_precos_contas": {"view": True, "edit": True, "delete": True},
}


def _usuario(role: UserRole, equipes: list[int] | None = None) -> User:
    tag = uuid.uuid4().hex[:6]
    return User(
        open_id=f"email:eq-{tag}@davinci-test.com",
        email=f"eq-{tag}@davinci-test.com",
        role=role,
        status=UserStatus.ACTIVE,
        permissions=PERM_FULL,
        sales_teams=equipes,
    )


@pytest_asyncio.fixture
async def cenario(db: AsyncSession) -> dict[str, Any]:
    """Lojas de três equipes e cinco contas de preço (aba Celular):
    - mega   → loja Shopee mega (Londres) pelo `store_info_id`;
    - inova  → integração ML inova, que é o FK da loja inova (Israel);
    - kfa    → loja kfa (Marrocos) E a integração inova (Israel): as duas;
    - avulsa → sem loja nem integração (sem equipe);
    - a coluna de catálogo da inova (sem loja: herda da base).
    """
    dono = _usuario(UserRole.ADMIN)
    db.add(dono)
    await db.flush()

    celular = Segment(name="Celular", slug="celular", sort_order=0)
    db.add(celular)
    await db.flush()
    for i in range(5):
        db.add(
            Segment(name=f"tipo {i + 1}", slug=f"cel-{i + 1}", sort_order=i, parent_id=celular.id)
        )

    integ_inova = Integration(
        user_id=dono.id,
        platform=IntegrationPlatform.ML,
        name="inova",
        credentials=encrypt_json({"access_token": "x", "expires_at": int(time.time()) + 3600}),
    )
    db.add(integ_inova)
    await db.flush()

    loja_mega = StoreInfo(user_id=dono.id, platform="shopee", account_name="mega", sales_team=101)
    loja_inova = StoreInfo(
        user_id=dono.id,
        platform="ml",
        account_name="inova",
        sales_team=102,
        integration_id=integ_inova.id,
    )
    loja_kfa = StoreInfo(user_id=dono.id, platform="shopee", account_name="kfa", sales_team=103)
    db.add_all([loja_mega, loja_inova, loja_kfa])
    await db.flush()

    def conta(nome: str, plataforma: PricingPlatform, **kw) -> PricingAccount:
        return PricingAccount(
            user_id=dono.id,
            name=nome,
            platform=plataforma,
            segment_id=celular.id,
            commission=Decimal("0.14"),
            **kw,
        )

    mega = conta("mega", PricingPlatform.SHOPEE, store_info_id=loja_mega.id)
    inova = conta(
        "inova", PricingPlatform.ML, integration_id=integ_inova.id, listing_type="ml classico"
    )
    kfa = conta(
        "kfa", PricingPlatform.SHOPEE, store_info_id=loja_kfa.id, integration_id=integ_inova.id
    )
    avulsa = conta("avulsa", PricingPlatform.SHOPEE)
    db.add_all([mega, inova, kfa, avulsa])
    await db.flush()
    catalogo = PricingAccount(
        user_id=dono.id,
        name="inova",
        platform=PricingPlatform.ML,
        segment_id=celular.id,
        canal=CANAL_CATALOGO,
        conta_base_id=inova.id,
        listing_type="ml classico",
    )
    db.add(catalogo)
    await db.commit()
    return {
        "dono": dono,
        "integ_inova": integ_inova,
        "mega": mega,
        "inova": inova,
        "kfa": kfa,
        "avulsa": avulsa,
        "catalogo": catalogo,
    }


def _equipes(conta: dict) -> list[tuple[int, str]]:
    return [(e["numero"], e["nome"]) for e in conta["equipes"]]


async def test_contas_saem_com_as_equipes_da_cerca(
    client: AsyncClient,
    cenario: dict,
    auth_as: Callable,
):
    auth_as(cenario["dono"])
    r = await client.get("/api/pricing/accounts", params={"department": "celular"})
    assert r.status_code == 200, r.text
    por_id = {c["id"]: c for c in r.json()}
    assert _equipes(por_id[str(cenario["mega"].id)]) == [(101, "Londres")]
    assert _equipes(por_id[str(cenario["inova"].id)]) == [(102, "Israel")]
    # Loja de uma equipe e integração de outra: as duas veem, as duas aparecem.
    assert _equipes(por_id[str(cenario["kfa"].id)]) == [(102, "Israel"), (103, "Marrocos")]
    assert _equipes(por_id[str(cenario["avulsa"].id)]) == []
    # A coluna de catálogo não tem loja: vai com a base.
    assert _equipes(por_id[str(cenario["catalogo"].id)]) == [(102, "Israel")]


async def test_vendedor_da_equipe_ve_so_as_contas_que_a_coluna_diz_que_sao_dele(
    db: AsyncSession,
    client: AsyncClient,
    cenario: dict,
    auth_as: Callable,
):
    londres = _usuario(UserRole.USER, [101])
    db.add(londres)
    await db.commit()
    auth_as(londres)
    contas = (await client.get("/api/pricing/accounts")).json()
    assert [(c["name"], _equipes(c)) for c in contas] == [("mega", [(101, "Londres")])]


async def test_editar_a_conta_devolve_as_equipes_e_a_integracao_nova_muda_a_equipe(
    client: AsyncClient,
    cenario: dict,
    auth_as: Callable,
):
    auth_as(cenario["dono"])
    r = await client.patch(
        f"/api/pricing/accounts/{cenario['mega'].id}", json={"discount": "31.12"}
    )
    assert r.status_code == 200, r.text
    assert _equipes(r.json()) == [(101, "Londres")]

    r = await client.patch(
        f"/api/pricing/accounts/{cenario['avulsa'].id}",
        json={"integration_id": str(cenario["integ_inova"].id)},
    )
    assert r.status_code == 200, r.text
    assert _equipes(r.json()) == [(102, "Israel")]


async def test_catalogo_e_margem_propria_devolvem_as_equipes_da_base(
    client: AsyncClient,
    cenario: dict,
    auth_as: Callable,
):
    auth_as(cenario["dono"])
    r = await client.post(
        f"/api/pricing/accounts/{cenario['inova'].id}/catalogo", json={"ativo": True}
    )
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert _equipes(corpo["conta"]) == [(102, "Israel")]
    assert _equipes(corpo["conta_catalogo"]) == [(102, "Israel")]

    r = await client.patch(
        f"/api/pricing/accounts/{cenario['catalogo'].id}", json={"margin1": "0.25"}
    )
    assert r.status_code == 200, r.text
    assert _equipes(r.json()) == [(102, "Israel")]


async def test_escopo_das_equipes_e_o_mesmo_do_vendedor(
    db: AsyncSession,
    cenario: dict,
):
    for numero in (101, 102, 103):
        vendedor = _usuario(UserRole.USER, [numero])
        db.add(vendedor)
        await db.flush()
        assert await escopo_das_equipes(db, {numero}) == await resolve_team_scope(db, vendedor)
