"""Aba Contas: campos próprios para Desconto até, Cupom até, Afiliado % e
Oferta limite % (M-11, 18/11/2026, migration 0502).

- A conversão da migration ("26.06" → 26/06, "4%" → 4, "7% (amostra 3 vendas)"
  → 7 com "amostra 3 vendas" somado à Obs; o que não converte vai para a Obs).
  Roda a função de verdade e o UPDATE no schema de teste (contas FABRICADAS).
- O banco só aceita dia/mês que existem e % de 0 a 100.
- PATCH grava os campos novos (quem edita Contas, não só o ADM); valor
  inválido → 422; quem só vê Contas não grava.
- A coluna ML Catálogo copia os campos novos da conta de kit dela.
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Base, PricingAccount, PricingPlatform, Segment, UserRole
from app.services.pricing.anuncios import CANAL_CATALOGO

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "0502_contas_campos_proprios.py"
)

PERM_EDITA_CONTAS = {
    "tabela_precos": {"view": True, "edit": False, "delete": False},
    "tabela_precos_contas": {"view": True, "edit": True, "delete": False},
}
PERM_SO_VE_CONTAS = {
    "tabela_precos": {"view": True, "edit": False, "delete": False},
    "tabela_precos_contas": {"view": True, "edit": False, "delete": False},
}


def _carregar():
    spec = importlib.util.spec_from_file_location("migration_0502", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.SCHEMA = Base.metadata.schema
    return mod


async def _rodar(db: AsyncSession, passo: Callable) -> None:
    def _executar(conn) -> None:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            passo(conn)

    conn = await db.connection()
    await conn.run_sync(_executar)
    await db.commit()


# ------------------------------------------------------------- a conversão


def test_conversao_dos_exemplos_do_pedido():
    mod = _carregar()
    # Datas.
    assert mod.converter_data("26.06") == ("26/06", None)
    assert mod.converter_data("31.12") == ("31/12", None)
    assert mod.converter_data("05.04") == ("05/04", None)
    assert mod.converter_data("31.12 - 1%RE") == ("31/12", "1%RE")
    assert mod.converter_data("0") == (None, "0")
    assert mod.converter_data("valor minimo alto d+") == (None, "valor minimo alto d+")
    assert mod.converter_data("31.02") == (None, "31.02")
    assert mod.converter_data(None) == (None, None)
    assert mod.converter_data("  ") == (None, None)
    # Percentuais.
    assert mod.converter_pct("4%") == (Decimal("4"), None)
    assert mod.converter_pct("4") == (Decimal("4"), None)
    assert mod.converter_pct("7% (amostra 3 vendas)") == (Decimal("7"), "amostra 3 vendas")
    assert mod.converter_pct("4,5%") == (Decimal("4.5"), None)
    assert mod.converter_pct("101") == (None, "101")
    assert mod.converter_pct("muito") == (None, "muito")

    # Conta inteira: o que sobra vai para a Obs, depois do que já existe lá.
    novos, notas = mod.converter_conta(
        {
            "discount": "26.06",
            "affiliate": "7% (amostra 3 vendas)",
            "coupon": "valor minimo alto d+",
            "offer": "5",
        }
    )
    assert novos == {
        "desconto_ate": "26/06",
        "afiliado_pct": Decimal("7"),
        "cupom_ate": None,
        "oferta_limite_pct": Decimal("5"),
    }
    assert notas == ["Afiliado: amostra 3 vendas", "Cupom: valor minimo alto d+"]
    obs = mod.somar_obs("c68,s5,s7", notas)
    assert obs == "c68,s5,s7 | Afiliado: amostra 3 vendas | Cupom: valor minimo alto d+"
    assert mod.somar_obs(None, notas) == "Afiliado: amostra 3 vendas | Cupom: valor minimo alto d+"
    assert mod.somar_obs("c68", []) == "c68"
    # O downgrade tira exatamente o que foi somado.
    assert mod.tirar_da_obs(obs, notas) == "c68,s5,s7"
    assert mod.tirar_da_obs(mod.somar_obs(None, notas), notas) is None
    assert mod.tirar_da_obs("mexida depois", notas) == "mexida depois"


@pytest.mark.asyncio
async def test_migration_converte_as_contas_e_o_downgrade_desfaz_a_obs(db: AsyncSession, make_user):
    mod = _carregar()
    dono = await make_user(role=UserRole.ADMIN)
    celular = Segment(name="Celular", slug="celular", sort_order=0)
    db.add(celular)
    await db.flush()

    def conta(nome: str, **campos) -> PricingAccount:
        return PricingAccount(
            user_id=dono.id,
            name=nome,
            platform=PricingPlatform.SHOPEE,
            segment_id=celular.id,
            **campos,
        )

    aguiar = conta("aguiar", discount="19.06", affiliate="4%", coupon="31.12")
    kia = conta(
        "kia",
        discount="31.12 - 1%RE",
        affiliate="7% (amostra 3 vendas)",
        coupon="valor minimo alto d+",
        offer="5",
        observation="c68,s5,s7",
    )
    zero = conta("zero", discount="0")
    vazia = conta("vazia", observation="só obs")
    db.add_all([aguiar, kia, zero, vazia])
    await db.commit()

    await _rodar(db, mod.converter)
    for c in (aguiar, kia, zero, vazia):
        await db.refresh(c)

    assert (aguiar.desconto_ate, aguiar.afiliado_pct, aguiar.cupom_ate) == (
        "19/06",
        Decimal("4.00"),
        "31/12",
    )
    assert aguiar.oferta_limite_pct is None
    assert aguiar.observation is None
    assert (kia.desconto_ate, kia.afiliado_pct, kia.cupom_ate, kia.oferta_limite_pct) == (
        "31/12",
        Decimal("7.00"),
        None,
        Decimal("5.00"),
    )
    assert kia.observation == (
        "c68,s5,s7 | Desconto: 1%RE | Afiliado: amostra 3 vendas | Cupom: valor minimo alto d+"
    )
    assert zero.desconto_ate is None
    assert zero.observation == "Desconto: 0"
    assert vazia.observation == "só obs"
    # As colunas antigas ficam como estavam.
    assert (kia.discount, kia.affiliate, kia.coupon, kia.offer) == (
        "31.12 - 1%RE",
        "7% (amostra 3 vendas)",
        "valor minimo alto d+",
        "5",
    )

    await _rodar(db, mod.desfazer_obs)
    for c in (kia, zero, vazia):
        await db.refresh(c)
    assert kia.observation == "c68,s5,s7"
    assert zero.observation is None
    assert vazia.observation == "só obs"


@pytest.mark.asyncio
async def test_banco_so_aceita_dia_mes_valido_e_pct_de_0_a_100(db: AsyncSession, make_user):
    dono = await make_user(role=UserRole.ADMIN)
    celular = Segment(name="Celular", slug="celular", sort_order=0)
    db.add(celular)
    await db.commit()
    dono_id, celular_id = dono.id, celular.id
    schema = Base.metadata.schema
    for campo, valor in (
        ("desconto_ate", "31/02"),
        ("cupom_ate", "26.06"),
        ("desconto_ate", "00/05"),
        ("afiliado_pct", 101),
        ("oferta_limite_pct", -1),
    ):
        with pytest.raises(IntegrityError, match="ck_pricing_accounts_"):
            await db.execute(
                text(
                    f"INSERT INTO {schema}.pricing_accounts (id, user_id, name, platform, "
                    f"segment_id, {campo}) VALUES (gen_random_uuid(), :u, 'x', 'shopee', :s, :v)"
                ),
                {"u": dono_id, "s": celular_id, "v": valor},
            )
        await db.rollback()
    # Sem o CHECK, o erro seria outro (FK, NOT NULL): a mesma linha válida entra.
    db.add(
        PricingAccount(
            user_id=dono_id,
            name="ok",
            platform=PricingPlatform.SHOPEE,
            segment_id=celular_id,
            desconto_ate="29/02",
            cupom_ate="31/12",
            afiliado_pct=Decimal("0"),
            oferta_limite_pct=Decimal("100"),
        )
    )
    await db.commit()


# ------------------------------------------------------------- a API


@pytest_asyncio.fixture
async def cenario(db: AsyncSession, make_user) -> SimpleNamespace:
    """ "kia" (ML) com o Catálogo ML ligado e "aguiar" (Shopee), no Celular."""
    admin = await make_user(role=UserRole.ADMIN)
    vendedor = await make_user(role=UserRole.USER, permissions=PERM_EDITA_CONTAS)
    leitor = await make_user(role=UserRole.USER, permissions=PERM_SO_VE_CONTAS)
    celular = Segment(name="Celular", slug="celular", sort_order=0)
    db.add(celular)
    await db.flush()
    kia = PricingAccount(
        user_id=admin.id,
        name="kia",
        platform=PricingPlatform.ML,
        listing_type="ml classico",
        segment_id=celular.id,
        desconto_ate="19/06",
        afiliado_pct=Decimal("4"),
    )
    aguiar = PricingAccount(
        user_id=admin.id,
        name="aguiar",
        platform=PricingPlatform.SHOPEE,
        segment_id=celular.id,
    )
    db.add_all([kia, aguiar])
    await db.flush()
    catalogo = PricingAccount(
        user_id=admin.id,
        name="kia",
        platform=PricingPlatform.ML,
        listing_type="ml classico",
        segment_id=celular.id,
        canal=CANAL_CATALOGO,
        conta_base_id=kia.id,
    )
    db.add(catalogo)
    await db.commit()
    for o in (kia, aguiar, catalogo):
        await db.refresh(o)
    return SimpleNamespace(
        admin=admin, vendedor=vendedor, leitor=leitor, kia=kia, aguiar=aguiar, catalogo=catalogo
    )


@pytest.mark.asyncio
async def test_patch_grava_os_campos_novos(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cenario
):
    auth_as(cenario.vendedor)
    url = f"/api/pricing/accounts/{cenario.aguiar.id}"
    r = await client.patch(
        url,
        json={
            "desconto_ate": "19/06",
            "afiliado_pct": 4,
            "cupom_ate": "31/12",
            "oferta_limite_pct": 7.5,
        },
    )
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["desconto_ate"] == "19/06"
    assert corpo["cupom_ate"] == "31/12"
    assert Decimal(corpo["afiliado_pct"]) == Decimal("4")
    assert Decimal(corpo["oferta_limite_pct"]) == Decimal("7.5")

    # Apagar volta a vazio.
    r = await client.patch(url, json={"oferta_limite_pct": None, "cupom_ate": None})
    assert r.status_code == 200, r.text
    assert r.json()["oferta_limite_pct"] is None
    assert r.json()["cupom_ate"] is None

    # Valor inválido: 422 e nada muda.
    for corpo in (
        {"desconto_ate": "31/02"},
        {"desconto_ate": "26.06"},
        {"cupom_ate": "1/6"},
        {"afiliado_pct": 101},
        {"afiliado_pct": -1},
        {"oferta_limite_pct": "abc"},
    ):
        r = await client.patch(url, json=corpo)
        assert r.status_code == 422, (corpo, r.text)
    await db.refresh(cenario.aguiar)
    assert cenario.aguiar.desconto_ate == "19/06"
    assert cenario.aguiar.afiliado_pct == Decimal("4.00")


@pytest.mark.asyncio
async def test_quem_so_ve_contas_nao_grava(client: AsyncClient, auth_as: Callable, cenario):
    auth_as(cenario.leitor)
    r = await client.patch(
        f"/api/pricing/accounts/{cenario.aguiar.id}", json={"desconto_ate": "19/06"}
    )
    assert r.status_code == 403
    r = await client.get("/api/pricing/accounts", params={"department": "celular"})
    assert r.status_code == 200, r.text
    kia = next(c for c in r.json() if c["id"] == str(cenario.kia.id))
    assert kia["desconto_ate"] == "19/06"
    assert Decimal(kia["afiliado_pct"]) == Decimal("4")


@pytest.mark.asyncio
async def test_catalogo_ml_copia_os_campos_da_conta_de_kit(
    client: AsyncClient, auth_as: Callable, cenario
):
    auth_as(cenario.admin)
    r = await client.patch(
        f"/api/pricing/accounts/{cenario.kia.id}",
        json={"cupom_ate": "31/12", "oferta_limite_pct": 10},
    )
    assert r.status_code == 200, r.text
    r = await client.get("/api/pricing/accounts", params={"department": "celular"})
    assert r.status_code == 200, r.text
    filha = next(c for c in r.json() if c["id"] == str(cenario.catalogo.id))
    assert filha["desconto_ate"] == "19/06"
    assert filha["cupom_ate"] == "31/12"
    assert Decimal(filha["afiliado_pct"]) == Decimal("4")
    assert Decimal(filha["oferta_limite_pct"]) == Decimal("10")
