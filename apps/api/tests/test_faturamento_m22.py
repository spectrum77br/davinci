"""Faturamento M-22 (24/11/2026): colunas Equipe, Pendências (contas da Tabela
de Preços), Investimento, % Investimento e Margem (só ADM).

Cobre:
  * as colunas novas por loja: `equipe`, `contas_preco` (kit + catálogo, pela
    loja ou pela integração da loja), `investimento` do período (gasto guardado
    por dia; "parcial" quando o período começa antes do 1º dia guardado) e a
    `margem` (menor/maior margin1..5 das contas de kit);
  * a `margem` NÃO vai na resposta a quem não é ADM (nem a chave);
  * a cerca de equipe e as regras de pedidos não mudaram;
  * investimento sem dia guardado = None ("—"), nunca 0; dia com gasto 0 = 0;
  * a gravação do histórico (upsert, afiliados intocado, falha não derruba a
    coleta) e a Shopee chamando a gravação;
  * a migration 0506 (estrutura igual ao modelo, cópia das linhas diárias do
    Marketing, downgrade).
"""

# ruff: noqa: S608

from __future__ import annotations

import importlib.util
import inspect
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingOrder,
    Integration,
    IntegrationPlatform,
    MarketingAccount,
    MarketingGastoDia,
    PricingAccount,
    PricingPlatform,
    Segment,
    StoreInfo,
    User,
    UserRole,
    UserStatus,
)
from app.models.base import Base
from app.services.marketing import gasto_dia as gasto_svc
from app.services.marketing import shopee_sync

PERM = {"faturamento": {"view": True, "edit": False, "delete": False}}
JUNHO = {"start": "2026-06-01T00:00:00", "end": "2026-07-01T00:00:00"}
_MIGRATION = (
    Path(__file__).resolve().parent.parent / "alembic" / "versions" / "0506_marketing_gasto_dia.py"
)


@pytest_asyncio.fixture(autouse=True)
async def _limpa_marketing(db: AsyncSession):
    """marketing_accounts não está na limpeza do conftest (o histórico vai em
    CASCADE com a conta)."""

    async def wipe():
        await db.execute(text("DELETE FROM marketing_accounts"))
        await db.commit()

    await wipe()
    yield
    await wipe()


async def _usuario(db: AsyncSession, *, role: UserRole, equipes: list[int] | None) -> User:
    email = f"m22-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(
        open_id=f"email:{email}",
        email=email,
        role=role,
        status=UserStatus.ACTIVE,
        permissions=PERM,
        sales_teams=equipes,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


class Cenario:
    pass


async def _cenario(db: AsyncSession) -> Cenario:
    """Quatro lojas, como na produção:
    - mega Shopee (equipe 102): duas contas de kit com store_info_id (celular
      e eletro), margens 10,2%…55,3% (o exemplo do pedido); Ads da Shopee sem
      integração e sem dia guardado;
    - mega ML (101): conta de kit SEM store_info_id, pela integração (FK da
      loja) + a coluna de catálogo dela; margem 42,5% em todos os tipos; Ads
      com dias guardados antes e dentro de junho;
    - forpaper ML (103): a loja sem FK casa com a integração pelo nome; conta
      de kit sem margem; Ads com o 1º dia guardado em 15/06 (parcial);
    - semconta Magalu (101): nada ligado."""
    c = Cenario()
    dono = await _usuario(db, role=UserRole.ADMIN, equipes=None)
    seg = Segment(name="Celular", slug=f"celular-{uuid.uuid4().hex[:4]}")
    db.add(seg)
    await db.flush()
    c.integ_mega = Integration(
        user_id=dono.id,
        platform=IntegrationPlatform.ML,
        name="mega",
        credentials=b"x",
        status="active",
        ads_enabled=True,
    )
    c.integ_forpaper = Integration(
        user_id=dono.id,
        platform=IntegrationPlatform.ML,
        name="forpaper",
        credentials=b"x",
        status="active",
        ads_enabled=True,
    )
    db.add_all([c.integ_mega, c.integ_forpaper])
    await db.flush()

    def loja(nome, plataforma, equipe, bling, integ=None):
        return StoreInfo(
            user_id=dono.id,
            platform=plataforma,
            account_name=nome,
            bling_store_id=bling,
            sales_team=equipe,
            commercial_team=1,
            integration_id=integ,
        )

    c.mega_shopee = loja("mega", "shopee", 102, "3001")
    c.mega_ml = loja("mega", "ml", 101, "3002", c.integ_mega.id)
    c.forpaper = loja("forpaper", "ml", 103, "3003")
    c.semconta = loja("semconta", "magalu", 101, "3004")
    db.add_all([c.mega_shopee, c.mega_ml, c.forpaper, c.semconta])
    await db.flush()

    def conta(nome, plataforma, margens, **extra):
        valores = {
            f"margin{i + 1}": (Decimal(m) if m is not None else None) for i, m in enumerate(margens)
        }
        return PricingAccount(
            user_id=dono.id, name=nome, platform=plataforma, segment_id=seg.id, **valores, **extra
        )

    c.p_shopee_cel = conta(
        "mega",
        PricingPlatform.SHOPEE,
        ["0.449", "0.368", "0.368", "0.368", "0.102"],
        store_info_id=c.mega_shopee.id,
    )
    c.p_shopee_ele = conta(
        "mega", PricingPlatform.SHOPEE, ["0.553"] * 5, store_info_id=c.mega_shopee.id
    )
    c.p_ml = conta("mega", PricingPlatform.ML, ["0.425"] * 5, integration_id=c.integ_mega.id)
    c.p_forpaper = conta(
        "forpaper", PricingPlatform.ML, [None] * 5, integration_id=c.integ_forpaper.id
    )
    db.add_all([c.p_shopee_cel, c.p_shopee_ele, c.p_ml, c.p_forpaper])
    await db.flush()
    # Coluna de catálogo: vai com a conta de kit (a margem dela não conta).
    c.p_ml_cat = conta(
        "mega", PricingPlatform.ML, ["0.9"] * 5, canal="catalogo", conta_base_id=c.p_ml.id
    )
    db.add(c.p_ml_cat)

    def ads(nome, plataforma, integ=None, arquivada=False):
        return MarketingAccount(
            integration_id=integ,
            name=nome,
            platform=plataforma,
            department="geral",
            acos_target=7.0,
            agent_enabled=False,
            arquivada_em=datetime(2026, 1, 1, tzinfo=UTC) if arquivada else None,
        )

    c.ads_mega_ml = ads("mega", "ml", c.integ_mega.id)
    c.ads_mega_shopee = ads("Mega", "shopee")
    c.ads_forpaper = ads("forpaper", "ml", c.integ_forpaper.id)
    c.ads_arquivada = ads("semconta", "magalu", arquivada=True)
    db.add_all([c.ads_mega_ml, c.ads_mega_shopee, c.ads_forpaper, c.ads_arquivada])
    await db.flush()
    for conta_ads, dia, gasto in (
        (c.ads_mega_ml, date(2026, 5, 20), "99.00"),
        (c.ads_mega_ml, date(2026, 6, 5), "10.00"),
        (c.ads_mega_ml, date(2026, 6, 10), "20.50"),
        (c.ads_mega_ml, date(2026, 7, 1), "1000.00"),  # fora de junho
        (c.ads_forpaper, date(2026, 6, 15), "7.25"),
        (c.ads_arquivada, date(2026, 6, 15), "555.00"),
    ):
        db.add(
            MarketingGastoDia(
                marketing_account_id=conta_ads.id,
                dia=dia,
                gasto_ads=Decimal(gasto),
                origem="ml_ads",
            )
        )
    # Um pedido para a loja mega Shopee no período (as regras de pedidos seguem).
    db.add(
        BlingOrder(
            bling_id=7001,
            numero="7001",
            item_codigo="sku-1",
            item_index=0,
            situacao="83953",
            total=Decimal("1000.00"),
            data=datetime(2026, 6, 10, 12, tzinfo=UTC),
            loja="3001",
        )
    )
    await db.commit()
    return c


def _por_loja(body: dict) -> dict[str, dict]:
    return {it["store_id"]: it for it in body["itens"]}


@pytest.mark.asyncio
async def test_colunas_novas_para_o_administrador(
    db: AsyncSession,
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    c = await _cenario(db)
    auth_as(await _usuario(db, role=UserRole.ADMIN, equipes=None))
    r = await client.get("/api/faturamento", params=JUNHO)
    assert r.status_code == 200, r.text
    linhas = _por_loja(r.json())

    shopee = linhas[str(c.mega_shopee.id)]
    assert shopee["equipe"] == 102
    assert shopee["contas_preco"] == sorted([str(c.p_shopee_cel.id), str(c.p_shopee_ele.id)])
    # O exemplo do pedido: "10.2% – 55.3%".
    assert shopee["margem"] == {"min": 0.102, "max": 0.553}
    # Conta de Ads sem nenhum dia guardado: sem dado.
    assert shopee["investimento"] is None
    assert shopee["investimento_parcial"] is False
    assert (shopee["pedidos"], shopee["faturamento"]) == (1, 1000.0)

    ml = linhas[str(c.mega_ml.id)]
    assert ml["equipe"] == 101
    # Sem store_info_id, pela integração da loja; o catálogo vai junto.
    assert ml["contas_preco"] == sorted([str(c.p_ml.id), str(c.p_ml_cat.id)])
    assert ml["margem"] == {"min": 0.425, "max": 0.425}, "iguais: uma margem só (catálogo fora)"
    assert ml["investimento"] == 30.5
    assert ml["investimento_desde"] == "2026-05-20"
    assert ml["investimento_parcial"] is False

    forpaper = linhas[str(c.forpaper.id)]
    assert forpaper["contas_preco"] == [str(c.p_forpaper.id)], "a loja sem FK casa pelo nome"
    assert forpaper["margem"] is None, "conta sem margem preenchida"
    assert forpaper["investimento"] == 7.25
    assert forpaper["investimento_desde"] == "2026-06-15"
    assert forpaper["investimento_parcial"] is True, "o período começa antes do 1º dia guardado"

    vazia = linhas[str(c.semconta.id)]
    assert vazia["contas_preco"] == []
    assert vazia["margem"] is None
    assert vazia["investimento"] is None, "a conta de Ads arquivada não entra"
    assert vazia["equipe"] == 101


@pytest.mark.asyncio
@pytest.mark.parametrize("equipes", [None, [101, 102, 103]])
async def test_margem_nao_vai_na_resposta_de_quem_nao_e_adm(
    db: AsyncSession,
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
    equipes: list[int] | None,
):
    c = await _cenario(db)
    auth_as(await _usuario(db, role=UserRole.USER, equipes=equipes))
    r = await client.get("/api/faturamento", params=JUNHO)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["itens"], "vê as lojas"
    for it in body["itens"]:
        assert "margem" not in it, it
    linha = _por_loja(body)[str(c.mega_ml.id)]
    # O resto das colunas novas vai normalmente.
    assert linha["investimento"] == 30.5
    assert linha["contas_preco"] == sorted([str(c.p_ml.id), str(c.p_ml_cat.id)])
    assert linha["equipe"] == 101
    assert "0.425" not in r.text and "0.553" not in r.text


@pytest.mark.asyncio
async def test_cerca_de_equipe_intacta(
    db: AsyncSession,
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    c = await _cenario(db)
    auth_as(await _usuario(db, role=UserRole.USER, equipes=[101]))
    r = await client.get("/api/faturamento", params=JUNHO)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(_por_loja(body)) == {str(c.mega_ml.id), str(c.semconta.id)}
    assert body["teams"] == [101]
    assert body["total_pedidos"] == 0, "o pedido da Shopee (equipe 102) não aparece"
    for outra in (102, 103):
        r = await client.get("/api/faturamento", params={**JUNHO, "team": outra})
        assert r.status_code == 403, r.text
        assert r.json()["detail"]["code"] == "team_not_allowed"


@pytest.mark.asyncio
async def test_investimento_sem_dia_guardado_e_none_e_dia_zero_e_zero(
    db: AsyncSession,
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    c = await _cenario(db)
    auth_as(await _usuario(db, role=UserRole.ADMIN, equipes=None))
    # Janeiro: nenhum dia guardado em loja nenhuma → None em todas (nunca 0).
    r = await client.get(
        "/api/faturamento", params={"start": "2026-01-01T00:00:00", "end": "2026-02-01T00:00:00"}
    )
    assert r.status_code == 200, r.text
    assert {it["investimento"] for it in r.json()["itens"]} == {None}

    # Dia guardado com gasto 0 é dado de verdade: R$ 0,00. Afiliados soma.
    db.add_all(
        [
            MarketingGastoDia(
                marketing_account_id=c.ads_mega_shopee.id,
                dia=date(2026, 8, 3),
                gasto_ads=Decimal("0"),
                origem="shopee_ads",
            ),
            MarketingGastoDia(
                marketing_account_id=c.ads_mega_ml.id,
                dia=date(2026, 8, 3),
                gasto_ads=Decimal("5.00"),
                gasto_afiliados=Decimal("2.50"),
                origem="ml_ads",
            ),
        ]
    )
    await db.commit()
    agosto = {"start": "2026-08-01T00:00:00", "end": "2026-09-01T00:00:00"}
    linhas = _por_loja((await client.get("/api/faturamento", params=agosto)).json())
    assert linhas[str(c.mega_shopee.id)]["investimento"] == 0.0
    # Guardado só desde 03/08 e o período começa em 01/08: parcial.
    assert linhas[str(c.mega_shopee.id)]["investimento_parcial"] is True
    assert linhas[str(c.mega_shopee.id)]["investimento_desde"] == "2026-08-03"
    assert linhas[str(c.mega_ml.id)]["investimento"] == 7.5
    assert linhas[str(c.mega_ml.id)]["investimento_parcial"] is False

    # Personalizado: o último dia (até 23:59:59) entra; o mês, fim exclusivo.
    um_dia = {"start": "2026-06-10T00:00:00", "end": "2026-06-10T23:59:59"}
    linhas = _por_loja((await client.get("/api/faturamento", params=um_dia)).json())
    assert linhas[str(c.mega_ml.id)]["investimento"] == 20.5
    julho_1 = {"start": "2026-06-30T00:00:00", "end": "2026-07-01T00:00:00"}
    linhas = _por_loja((await client.get("/api/faturamento", params=julho_1)).json())
    assert linhas[str(c.mega_ml.id)]["investimento"] is None, "01/07 fica fora de [30/06, 01/07)"


@pytest.mark.asyncio
async def test_sem_a_tabela_do_historico_a_aba_abre_com_investimento_sem_dado(
    db: AsyncSession,
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    """Revisão (24/11/2026): código novo no ar antes da 0506 (o deploy não roda
    migration, e ela já falhou calada) não pode derrubar a aba inteira — só a
    coluna Investimento fica "—"; pedidos, faturamento e o resto seguem."""
    c = await _cenario(db)
    auth_as(await _usuario(db, role=UserRole.ADMIN, equipes=None))
    await db.execute(text("ALTER TABLE marketing_gasto_dia RENAME TO marketing_gasto_dia_fora"))
    await db.commit()
    try:
        r = await client.get("/api/faturamento", params=JUNHO)
    finally:
        await db.execute(text("ALTER TABLE marketing_gasto_dia_fora RENAME TO marketing_gasto_dia"))
        await db.commit()
    assert r.status_code == 200, r.text
    linhas = _por_loja(r.json())
    assert {it["investimento"] for it in r.json()["itens"]} == {None}
    shopee = linhas[str(c.mega_shopee.id)]
    assert (shopee["pedidos"], shopee["faturamento"]) == (1, 1000.0)
    assert shopee["margem"] == {"min": 0.102, "max": 0.553}
    assert linhas[str(c.mega_ml.id)]["contas_preco"] == sorted([str(c.p_ml.id), str(c.p_ml_cat.id)])


# ── gravação do histórico ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gravar_gasto_dos_dias_faz_upsert_e_nao_mexe_em_afiliados(db: AsyncSession):
    conta = MarketingAccount(name="x", platform="ml", department="geral", acos_target=7.0)
    db.add(conta)
    await db.flush()
    db.add(
        MarketingGastoDia(
            marketing_account_id=conta.id,
            dia=date(2026, 6, 1),
            gasto_ads=Decimal("1"),
            gasto_afiliados=Decimal("3"),
            origem="marketing_metrics",
        )
    )
    await db.commit()
    n = await gasto_svc.gravar_gasto_dos_dias(
        db,
        account_id=conta.id,
        origem=gasto_svc.ORIGEM_ML,
        dias=[
            (date(2026, 6, 1), 10.005),
            (date(2026, 6, 2), 0.0),
            (date(2026, 6, 3), None),
            (date(2026, 6, 4), -1.0),
            (date(2026, 6, 5), float("nan")),
        ],
    )
    await db.commit()
    assert n == 2
    linhas = {
        g.dia: g
        for g in (
            await db.execute(
                select(MarketingGastoDia)
                .where(MarketingGastoDia.marketing_account_id == conta.id)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    }
    assert sorted(linhas) == [date(2026, 6, 1), date(2026, 6, 2)], (
        "sem valor / negativo / NaN não grava"
    )
    assert linhas[date(2026, 6, 1)].gasto_ads == Decimal("10.01")
    assert linhas[date(2026, 6, 1)].gasto_afiliados == Decimal("3.00"), "afiliados intocado"
    assert linhas[date(2026, 6, 1)].origem == "ml_ads"
    assert linhas[date(2026, 6, 2)].gasto_ads == Decimal("0.00")


@pytest.mark.asyncio
async def test_falha_na_gravacao_nao_derruba_a_coleta(db: AsyncSession):
    """Conta que não existe (FK) — como a tabela ainda sem migration: a
    gravação desiste no SAVEPOINT e a sessão da coleta segue usável."""
    conta = MarketingAccount(name="y", platform="ml", department="geral", acos_target=7.0)
    db.add(conta)
    await db.flush()
    n = await gasto_svc.gravar_gasto_dos_dias(
        db,
        account_id=uuid.uuid4(),
        origem=gasto_svc.ORIGEM_ML,
        dias=[(date(2026, 6, 1), 1.0)],
    )
    assert n == 0
    conta.spend_today = 42.0
    await db.commit()
    await db.refresh(conta)
    assert conta.spend_today == 42.0


def test_shopee_grava_o_historico_junto_da_linha_diaria():
    fonte = inspect.getsource(shopee_sync.sync_shopee_integration)
    i = fonte.index("await _upsert_daily_metrics(session, account_id=account.id, rows=daily)")
    trecho = fonte[i : i + 600]
    assert "gravar_gasto_dos_dias(" in trecho
    assert "dias=[(d.day, d.spend) for d in daily]" in trecho
    assert "origem=ORIGEM_SHOPEE" in trecho
    from app.services.marketing import amazon_sync

    assert "gravar_gasto_dos_dias" not in inspect.getsource(amazon_sync), (
        "Amazon: o gasto é a soma de 7 dias, não o do dia"
    )


# ── migration 0506 ────────────────────────────────────────────────────────────


def _carregar():
    spec = importlib.util.spec_from_file_location("migration_0506", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _estrutura(db: AsyncSession, schema: str, tabela: str) -> dict[str, list]:
    colunas = (
        await db.execute(
            text(
                "SELECT column_name, data_type, numeric_precision, numeric_scale, is_nullable,"
                " column_default FROM information_schema.columns"
                " WHERE table_schema = :s AND table_name = :t ORDER BY ordinal_position"
            ),
            {"s": schema, "t": tabela},
        )
    ).all()
    constraints = (
        await db.execute(
            text(
                "SELECT co.conname, pg_get_constraintdef(co.oid) FROM pg_constraint co"
                " JOIN pg_class cl ON cl.oid = co.conrelid"
                " JOIN pg_namespace n ON n.oid = cl.relnamespace"
                " WHERE n.nspname = :s AND cl.relname = :t ORDER BY co.conname"
            ),
            {"s": schema, "t": tabela},
        )
    ).all()
    return {
        "colunas": [tuple(r) for r in colunas],
        "constraints": [(r[0], r[1].replace(f"{schema}.", "")) for r in constraints],
    }


@pytest.mark.asyncio
async def test_migration_0506_cria_o_que_o_modelo_declara_copia_e_o_downgrade_desfaz(
    db: AsyncSession,
):
    mod = _carregar()
    assert (mod.revision, mod.down_revision) == (
        "0506_marketing_gasto_dia",
        "0505_alertas_por_equipe",
    )
    schema_model = Base.metadata.schema
    r = f"{schema_model}_mig0506"
    await db.execute(text(f'DROP SCHEMA IF EXISTS "{r}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{r}"'))
    # Só o que a 0506 toca, como na produção.
    await db.execute(
        text(
            f'CREATE TABLE "{r}".marketing_accounts (id uuid PRIMARY KEY,'
            " platform varchar(32) NOT NULL, arquivada_em timestamptz)"
        )
    )
    await db.execute(
        text(
            f'CREATE TABLE "{r}".marketing_metrics (id uuid PRIMARY KEY, account_id uuid NOT NULL'
            f' REFERENCES "{r}".marketing_accounts(id) ON DELETE CASCADE,'
            " timestamp timestamptz NOT NULL,"
            " spend double precision NOT NULL DEFAULT 0, intensity integer NOT NULL DEFAULT 50)"
        )
    )
    ml, shopee, amazon, arquivada = (uuid.uuid4() for _ in range(4))
    for conta, plataforma, arq in (
        (ml, "ml", None),
        (shopee, "shopee", None),
        (amazon, "amazon", None),
        (arquivada, "ml", "2026-10-07"),
    ):
        await db.execute(
            text(f'INSERT INTO "{r}".marketing_accounts VALUES (:id, :p, :a)'),
            {
                "id": conta,
                "p": plataforma,
                "a": datetime.fromisoformat(arq).replace(tzinfo=UTC) if arq else None,
            },
        )
    for conta, quando, gasto, intensidade in (
        # Antes de 09/09 a coleta antiga gravava número errado (inflado até
        # 09/07, zero de 09/07 a 08/09) e nada consertou: fica de fora.
        (ml, "2026-05-20T12:00:00+00:00", 4000.0, 0),
        (ml, "2026-08-15T12:00:00+00:00", 0.0, 0),
        (ml, "2026-09-08T12:00:00+00:00", 0.0, 0),
        (ml, "2026-09-09T12:00:00+00:00", 95.125, 0),
        (ml, "2026-09-10T12:00:00+00:00", 0.0, 0),
        (ml, "2026-09-10T15:00:00+00:00", 9.0, 50),  # linha por hora: fica de fora
        (ml, "2026-09-11T12:00:00+00:00", float("nan"), 0),  # NaN: fica de fora
        (shopee, "2026-10-01T12:00:00+00:00", 3.3, 0),
        (amazon, "2026-10-05T12:00:00+00:00", 70.0, 0),  # soma de 7 dias: fora
        (arquivada, "2026-10-05T12:00:00+00:00", 1.0, 0),  # demonstração: fora
    ):
        await db.execute(
            text(f'INSERT INTO "{r}".marketing_metrics VALUES (:id, :c, :t, :g, :i)'),
            {
                "id": uuid.uuid4(),
                "c": conta,
                "t": datetime.fromisoformat(quando),
                "g": gasto,
                "i": intensidade,
            },
        )
    await db.commit()

    def _rodar_passo(conn, passo: str) -> None:
        ctx = MigrationContext.configure(conn, opts={"target_metadata": Base.metadata})
        with Operations.context(ctx):
            getattr(mod, passo)()

    mod.SCHEMA = r
    try:
        conn = await db.connection()
        await conn.run_sync(_rodar_passo, "upgrade")
        await db.commit()
        da_migration = await _estrutura(db, r, "marketing_gasto_dia")
        assert da_migration["colunas"]
        assert da_migration == await _estrutura(db, schema_model, "marketing_gasto_dia")
        copiadas = (
            await db.execute(
                text(
                    "SELECT marketing_account_id, dia, gasto_ads, gasto_afiliados, origem"
                    f' FROM "{r}".marketing_gasto_dia ORDER BY dia'
                )
            )
        ).all()
        assert copiadas == [
            (ml, date(2026, 9, 9), Decimal("95.13"), None, "marketing_metrics"),
            (ml, date(2026, 9, 10), Decimal("0.00"), None, "marketing_metrics"),
            (shopee, date(2026, 10, 1), Decimal("3.30"), None, "marketing_metrics"),
        ], "só de 09/09 em diante (o que a coleta consertada gravou ou conferiu)"
        # Uma linha por (conta, dia); gasto negativo recusado; conta apagada leva o histórico.
        with pytest.raises(Exception):  # noqa: B017 — IntegrityError do asyncpg
            await db.execute(
                text(
                    f'INSERT INTO "{r}".marketing_gasto_dia'
                    " (marketing_account_id, dia, gasto_ads, origem)"
                    f" VALUES ('{ml}', '2026-09-09', 1, 'x')"
                )
            )
        await db.rollback()
        with pytest.raises(Exception):  # noqa: B017
            await db.execute(text(f'UPDATE "{r}".marketing_gasto_dia SET gasto_ads = -1'))
        await db.rollback()
        await db.execute(text(f"DELETE FROM \"{r}\".marketing_accounts WHERE id = '{shopee}'"))
        await db.commit()
        assert (
            await db.execute(text(f'SELECT count(*) FROM "{r}".marketing_gasto_dia'))
        ).scalar_one() == 2

        conn = await db.connection()
        await conn.run_sync(_rodar_passo, "downgrade")
        await db.commit()
        restantes = (
            (
                await db.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables WHERE table_schema = :s"
                    ),
                    {"s": r},
                )
            )
            .scalars()
            .all()
        )
        assert sorted(restantes) == ["marketing_accounts", "marketing_metrics"]
        assert (
            await db.execute(text(f'SELECT count(*) FROM "{r}".marketing_metrics'))
        ).scalar_one() == 9
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{r}" CASCADE'))
        await db.commit()
