"""M-74 (26/11/2026): Margem mín, Condição especial e DUIMP padrão POR PRODUTO (Produtos 2).

- A chave do produto (`sku_base`) = cada peça sem a TAG DE ENVIO do fim (lista de Cadastros ›
  Produtos; correção do coordenador de 26/11): os exemplos do contrato; Python e SQL dão o mesmo
  resultado nas amostras e nos SKUs REAIS da cópia previa70 (só leitura); a migration tem uma
  cópia congelada igual.
- A view vw_bling_pedidos da 0516, num schema de teste: com produto_regras VAZIA é igual à da
  0235, linha a linha; o valor do produto vence (SKU simples, outra tag, kit, SKU "z"); tamanho de
  mala e voltagem são produtos diferentes; apagar o valor volta a herdar; pedido com vários itens:
  cada item com a sua; o downgrade volta tudo.
- A API: GET mostra o herdado; PUT grava e vazio volta a herdar; só administrador grava (403);
  Condição especial do produto (criar, resumo, apagar); Histórico com os nomes certos.
- Margem: a Condição especial do produto vale antes da do segmento.
- Nota: DUIMP da Importação → DUIMP padrão do produto → segmento (kit e peça).
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import asyncpg
import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.historico import leitura, nomes
from app.models import (
    Base,
    BlingOrder,
    HistoricoAlteracao,
    ImportProduct,
    Product,
    ProdutoCadastro,
    ProdutoCadastroTipo,
    ProdutoCondicaoEspecial,
    ProdutoRegra,
    Segment,
    SegmentSpecialDate,
    UserRole,
)
from app.models.produto_cadastro import CHAVE_TAG_ENVIO
from app.routers import produto_regras as rotas
from app.security.jwt import issue_session_token
from app.services import nf_emissao_gerar as nf
from app.services import produto_regras as svc
from app.services.sku_tag_envio import tags_de_envio
from tests.conftest import TEST_SCHEMA

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "0516_produto_regras_margem_duimp.py"
)

_DUIMP_REGULAR = "celular importado pela duimp 26BR0003333333-3"
_DUIMP_ACESS = "acessorio importado pela duimp 26BR0004444444-4"
_DUIMP_IMPORT = "produto importado pela duimp 26BR0001398015-8"


def _carregar():
    spec = importlib.util.spec_from_file_location("migration_0516", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# =========================================================== chave do produto

# A lista "Tag de envio" da 0508 (Cadastros › Produtos), como está na produção.
TAGS_0508 = (".cd", ".ci", ".pi", ".ra", ".sa", ".sp")


async def _semear_tags(db: AsyncSession, nomes: tuple[str, ...] = TAGS_0508) -> None:
    """A lista "Tag de envio" no schema de teste (troca a que houver)."""
    tipo = (
        await db.execute(
            select(ProdutoCadastroTipo).where(ProdutoCadastroTipo.chave == CHAVE_TAG_ENVIO)
        )
    ).scalar_one_or_none()
    if tipo is None:
        tipo = ProdutoCadastroTipo(nome="Tag de envio", chave=CHAVE_TAG_ENVIO)
        db.add(tipo)
        await db.flush()
    await db.execute(ProdutoCadastro.__table__.delete().where(ProdutoCadastro.tipo_id == tipo.id))
    db.add_all(ProdutoCadastro(tipo_id=tipo.id, nome=t) for t in nomes)
    await db.commit()


@pytest_asyncio.fixture
async def tags_envio(db: AsyncSession) -> tuple[str, ...]:
    await _semear_tags(db)
    return TAGS_0508


# Os exemplos do contrato (seção "Correção da regra da chave do produto") e casos de borda.
_EXEMPLOS_DO_CONTRATO = [
    ("dg019.ra", "dg019"),
    ("dg019.ra+a020.ra", "dg019+a020"),
    ("b001.12", "b001.12"),
    ("b001.12.sp", "b001.12"),
    ("uaf001m1.110", "uaf001m1.110"),
    ("uaf001m1.2l", "uaf001m1.2l"),
    ("z2821", "z2821"),
]
_AMOSTRAS = [
    *_EXEMPLOS_DO_CONTRATO,
    ("b001.24.sp", "b001.24"),  # outro tamanho: outro produto
    ("uaf001m1.6l", "uaf001m1.6l"),
    ("DG019.RA + A020.RA", "dg019+a020"),
    ("  dg019.sp  ", "dg019"),
    ("dg019.ra.ra", "dg019.ra"),  # só UMA tag sai
    ("dg019.rax", "dg019.rax"),  # .ra no meio não é o fim da peça
    ("dg019.ra+a020", "dg019+a020"),
    ("b089.20+a075+bp003+a076.sp", "b089.20+a075+bp003+a076"),
    ("z0207.mala.us", "z0207.mala.us"),  # .us não está na lista
    ("dg001.kit2", "dg001.kit2"),
    ("0002.usado", "0002.usado"),
    (".ra", ".ra"),  # a peça é só a tag: nada sobra, nada sai
    ("dg019.ra+.sp", "dg019+.sp"),
    ("caixa 10.ci", "caixa 10"),
    ("x .ra", "x "),  # o que sobra fica como está (mesma regra da M-44/M-46)
    ("a++b.sp", "a++b"),
    ("a+ c.cd", "a+c"),
    ("", ""),
]


def test_sku_base_exemplos_do_contrato():
    for sku, base in _AMOSTRAS:
        assert svc.sku_base(sku, TAGS_0508) == base, sku
    # Sem lista de tags (lista vazia): só minúsculas e espaços; nada sai do código.
    assert svc.sku_base(" DG019.RA + A020.RA ", []) == "dg019.ra+a020.ra"
    # A tag mais longa que casa vence (como tag_do_sku da M-44/M-46).
    assert svc.sku_base("ab.x.sp", [".sp", ".x.sp"]) == "ab"
    assert svc.sku_base(".x.sp", [".sp", ".x.sp"]) == ".x"


async def _sql_da_chave(db: AsyncSession, skus: list[str]) -> dict[str, str | None]:
    rows = await db.execute(
        text(f"SELECT u.s, {svc.sku_base_sql('u.s')} FROM unnest(CAST(:skus AS text[])) AS u(s)"),
        {"skus": skus},
    )
    return {r[0]: r[1] for r in rows}


async def test_sku_base_em_sql_igual_ao_python_e_a_copia_da_migration(db: AsyncSession, tags_envio):
    mod = _carregar()
    assert mod.sku_base_sql("x", "s") == svc.sku_base_sql("x", "s")
    assert mod.tags_padrao_sql("s") == svc.tags_padrao_sql("s")
    # A lista é lida por subconsulta NÃO correlacionada (não cita a coluna do SKU).
    assert "u.s" not in svc.tags_padrao_sql() and "tag_envio" in svc.tags_padrao_sql()
    skus = [s for s, _ in _AMOSTRAS]
    sql = await _sql_da_chave(db, skus)
    for sku, base in _AMOSTRAS:
        assert sql[sku] == base, sku
    assert await svc.sku_base_de(db, "b001.12.sp") == "b001.12"
    # Lista vazia: o SQL não tira nada (igual ao Python com lista vazia).
    await _semear_tags(db, ())
    sql = await _sql_da_chave(db, skus)
    for sku in skus:
        assert sql[sku] == svc.sku_base(sku, []), sku
    # Tags com caracteres especiais do regex: escapadas, iguais ao Python.
    estranhas = (".ra", ".sp", " .US ", "[x]", "a-b", "(?)", "\\d", ".x.sp", "a+b", "é")
    await _semear_tags(db, estranhas)
    tags = await tags_de_envio(db)
    assert "a+b" in tags and " .US " not in tags and ".us" in tags
    extras = ["q[x]", "qa-b", "r(?)", "s\\d", "ab.x.sp", "zé", "c.us", "ka+b", "[x]", "x+y[x]"]
    sql = await _sql_da_chave(db, skus + extras)
    for sku in skus + extras:
        assert sql[sku] == svc.sku_base(sku, tags), sku


# Os SKUs REAIS: a cópia mascarada da produção (Postgres local 54400, banco previa70), SÓ LEITURA.
# Fora da máquina que tem a cópia, o teste é pulado.
_URL_SKUS_REAIS = os.environ.get(
    "DAVINCI_SKUS_REAIS_URL", "postgresql://teste:teste@127.0.0.1:54400/previa70"
)
_SQL_SKUS_REAIS = """
    SELECT s FROM (
        SELECT sku AS s FROM davinci.products
        UNION SELECT item_codigo FROM davinci.bling_orders
        UNION SELECT external_sku FROM davinci.product_links
        UNION SELECT btrim(x) FROM davinci.pricing_products, unnest(string_to_array(sku, ',')) x
        UNION SELECT variant_norm FROM davinci.pricing_product_variant
    ) t WHERE s IS NOT NULL ORDER BY s
"""


async def _skus_reais() -> list[str]:
    try:
        conn = await asyncpg.connect(
            _URL_SKUS_REAIS,
            timeout=5,
            server_settings={"default_transaction_read_only": "on"},
        )
    except (OSError, TimeoutError, asyncpg.PostgresError) as e:
        pytest.skip(f"sem a cópia com os SKUs reais ({_URL_SKUS_REAIS}): {e}")
    try:
        return [r["s"] for r in await conn.fetch(_SQL_SKUS_REAIS)]
    finally:
        await conn.close()


async def test_sku_base_python_igual_ao_sql_com_os_skus_reais(db: AsyncSession, tags_envio):
    skus = await _skus_reais()
    assert len(skus) > 5000, len(skus)
    assert sum("+" in s for s in skus) > 1000  # kits
    # A lista da produção, uma lista maior (com tags que pedem escape no regex) e lista vazia.
    for lista in (TAGS_0508, (*TAGS_0508, ".us", ".mala", "[x]", "a-b"), ()):
        await _semear_tags(db, lista)
        tags = await tags_de_envio(db)
        sql = await _sql_da_chave(db, skus)
        py = {s: svc.sku_base(s, tags) for s in skus}
        diferentes = [(s, py[s], sql[s]) for s in skus if py[s] != sql[s]]
        assert diferentes == [], (lista, len(diferentes), diferentes[:20])
    # Com a lista da produção: tamanhos de mala e voltagens/capacidades não se juntam; a tag sai.
    bases = {s: svc.sku_base(s, TAGS_0508) for s in skus}
    for sku, base in (
        ("b001.12", "b001.12"),
        ("b001.24", "b001.24"),
        ("uaf001m1.110", "uaf001m1.110"),
        ("uaf001m1.2l", "uaf001m1.2l"),
    ):
        if sku in bases:
            assert bases[sku] == base, sku
    assert any(s.endswith(".sp") and not b.endswith(".sp") for s, b in bases.items())


# =========================================================== view vw_bling_pedidos (0516)


async def _arvore(db: AsyncSession) -> SimpleNamespace:
    celular = Segment(name="Celular", slug="celular")
    mala = Segment(name="Mala", slug="mala")
    db.add_all([celular, mala])
    await db.flush()
    regular = Segment(
        name="Regular",
        slug="regular",
        parent_id=celular.id,
        sort_order=2,
        min_margin=Decimal("0.18"),
        duimp_padrao=_DUIMP_REGULAR,
    )
    acess = Segment(
        name="Acessórios",
        slug="acessorios",
        parent_id=celular.id,
        sort_order=0,
        min_margin=Decimal("0.18"),
        duimp_padrao=_DUIMP_ACESS,
    )
    queima = Segment(
        name="Queima de estoque",
        slug="queima-de-estoque",
        parent_id=mala.id,
        sort_order=4,
        active=False,
        min_margin=Decimal("-0.15"),
    )
    db.add_all([regular, acess, queima])
    await db.flush()
    return SimpleNamespace(celular=celular, mala=mala, regular=regular, acess=acess, queima=queima)


def _item(numero: str, idx: int, sku: str, valor: int = 100) -> BlingOrder:
    return BlingOrder(
        bling_id=int(numero),
        numero=numero,
        numeroloja=f"L{numero}",
        data=datetime(2026, 11, 20, 12, tzinfo=UTC),
        situacao="6",
        loja="204000000",
        total=Decimal(valor),
        valorbase=Decimal(valor),
        custofrete=Decimal(10),
        taxacomissao=Decimal(12),
        preco_custo=50.0,
        item_index=idx,
        itemvalor=Decimal(valor),
        item_codigo=sku,
        item_quantidade=1,
    )


async def _linhas(db: AsyncSession, schema: str) -> list[tuple]:
    return [
        tuple(r)
        for r in await db.execute(text(f'SELECT * FROM "{schema}".vw_bling_pedidos ORDER BY id'))
    ]


async def _minimas(db: AsyncSession, schema: str) -> dict[tuple[str, str], tuple]:
    rows = await db.execute(
        text(
            f"SELECT numero, item_codigo, min_margin, margin_floor_diff, margem"
            f' FROM "{schema}".vw_bling_pedidos'
        )
    )
    return {(r[0], r[1]): (r[2], r[3], r[4]) for r in rows}


async def _colunas(db: AsyncSession, schema: str) -> list[tuple]:
    return [
        tuple(r)
        for r in await db.execute(
            text(
                "SELECT a.attname, format_type(a.atttypid, a.atttypmod) FROM pg_attribute a"
                " WHERE a.attrelid = CAST(:v AS regclass) AND a.attnum > 0 ORDER BY a.attnum"
            ),
            {"v": f'"{schema}".vw_bling_pedidos'},
        )
    ]


async def _estrutura(db: AsyncSession, schema: str, tabela: str) -> dict[str, list]:
    colunas = (
        await db.execute(
            text(
                "SELECT column_name, data_type, character_maximum_length, numeric_precision,"
                " numeric_scale, is_nullable, column_default, is_generated, generation_expression"
                " FROM information_schema.columns"
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
    indices = (
        (
            await db.execute(
                text(
                    "SELECT indexname FROM pg_indexes WHERE schemaname = :s AND tablename = :t"
                    " ORDER BY indexname"
                ),
                {"s": schema, "t": tabela},
            )
        )
        .scalars()
        .all()
    )

    def _limpa(d: str) -> str:
        return d.replace(f'"{schema}".', "").replace(f"{schema}.", "")

    return {
        "colunas": [tuple(r) for r in colunas],
        "constraints": [(r[0], _limpa(r[1])) for r in constraints],
        "indices": list(indices),
    }


async def test_migration_0516_view_igual_com_tabela_vazia_e_produto_na_frente(db: AsyncSession):
    mod = _carregar()
    assert (mod.revision, mod.down_revision) == (
        "0516_produto_regras_margem_duimp",
        "0515_certificacoes_sku_anvisa",
    )
    seg = await _arvore(db)
    db.add_all(
        [
            _item("700001", 0, "dg019.ra"),  # simples
            _item("700002", 1, "dg019.sp"),  # pedido com 2 itens
            _item("700002", 2, "a020.sp", 40),
            _item("700003", 0, "dg019.ra+a020.ra"),  # kit
            _item("700004", 0, "z2821"),  # "z" sem cadastro → Queima
            _item("700005", 0, "x999"),  # nada → 9%
            _item("700006", 0, "z0093.mala"),  # "z" com valor do produto (.mala não é tag)
            _item("700007", 0, "DG019.RA"),  # maiúsculas
            _item("700008", 0, "b001.12.sp"),  # mala 12": a chave é b001.12
            _item("700009", 0, "b001.24.sp"),  # mala 24": outro produto
            _item("700010", 0, "uaf001m1.110"),  # voltagem fica na chave
            _item("700011", 0, "uaf001m1.2l"),
        ]
    )
    await db.commit()

    rascunho = f"{TEST_SCHEMA}_m0516"
    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    # Só o que a view e a 0513 precisam além das tabelas do ORM (que ficam no schema de teste).
    await db.execute(text(f'CREATE TABLE "{rascunho}".users (id uuid PRIMARY KEY)'))
    await db.execute(
        text(
            f'CREATE TABLE "{rascunho}".pricing_product_variant (pricing_product_id uuid NOT NULL,'
            " segment_id uuid, variant_norm text NOT NULL, vlen int NOT NULL)"
        )
    )
    await db.execute(
        text(
            f'CREATE VIEW "{rascunho}".vw_bling_item_mp_weight AS SELECT bo.id AS'
            " bling_order_item_id, bo.numero, NULL::numeric AS eff_weight"
            f' FROM "{TEST_SCHEMA}".bling_orders bo'
        )
    )
    # A lista "Tag de envio" (0508) no schema da migration: a view lê dali.
    await db.execute(
        text(f'CREATE TABLE "{rascunho}".produto_cadastro_tipos (id uuid PRIMARY KEY, chave text)')
    )
    await db.execute(
        text(f'CREATE TABLE "{rascunho}".produto_cadastros (tipo_id uuid NOT NULL, nome text)')
    )
    tipo_tag = uuid.uuid4()
    await db.execute(
        text(f"INSERT INTO \"{rascunho}\".produto_cadastro_tipos VALUES (:id, 'tag_envio')"),
        {"id": tipo_tag},
    )
    for tag in TAGS_0508:
        await db.execute(
            text(f'INSERT INTO "{rascunho}".produto_cadastros VALUES (:t, :n)'),
            {"t": tipo_tag, "n": tag},
        )
    for variante, folha in (("dg019", seg.regular), ("a020", seg.acess)):
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".pricing_product_variant VALUES'
                " (gen_random_uuid(), :seg, :v, length(:v))"
            ),
            {"seg": str(folha.id), "v": variante},
        )
    await db.commit()

    def _passo(conn, passo: str) -> None:
        ctx = MigrationContext.configure(conn, opts={"target_metadata": Base.metadata})
        with Operations.context(ctx):
            getattr(mod, passo)()

    mod.SCHEMA = rascunho
    caminho = f'SET LOCAL search_path TO "{rascunho}", "{TEST_SCHEMA}", public'
    try:
        # Estado de hoje: a view da 0235.
        await db.execute(text(caminho))
        await db.execute(text(mod.view_atual()))
        await db.commit()
        antes = await _linhas(db, rascunho)
        colunas_antes = await _colunas(db, rascunho)
        def_antes = (
            await db.execute(text(f"SELECT pg_get_viewdef('\"{rascunho}\".vw_bling_pedidos')"))
        ).scalar_one()
        assert len(antes) == 12
        minimas = await _minimas(db, rascunho)
        assert minimas[("700001", "dg019.ra")][0] == Decimal("0.1800")
        assert minimas[("700004", "z2821")][0] == Decimal("-0.1500")
        assert minimas[("700005", "x999")][0] == Decimal("0.0900")

        # A 0516, com produto_regras vazia: as mesmas colunas e as mesmas linhas.
        await db.execute(text(caminho))
        conn = await db.connection()
        await conn.run_sync(_passo, "upgrade")
        await db.commit()
        assert await _colunas(db, rascunho) == colunas_antes
        assert await _linhas(db, rascunho) == antes
        for tabela in ("produto_regras", "produto_condicoes_especiais"):
            da_migration = await _estrutura(db, rascunho, tabela)
            assert da_migration["colunas"], tabela
            assert da_migration == await _estrutura(db, TEST_SCHEMA, tabela), tabela

        # O valor do produto vence: outra tag, maiúsculas, kit e "z".
        await db.execute(
            text(
                f'INSERT INTO "{rascunho}".produto_regras (sku_base, margem_min) VALUES'
                " ('dg019', 0.12), ('dg019+a020', 0.05), ('z0093.mala', 0.30),"
                " ('b001.12', 0.07), ('uaf001m1.110', 0.11)"
            )
        )
        await db.commit()
        m = await _minimas(db, rascunho)
        assert m[("700001", "dg019.ra")][0] == Decimal("0.1200")
        assert m[("700007", "DG019.RA")][0] == Decimal("0.1200")
        # Pedido com 2 itens: cada item com a sua mínima (a020 não tem valor: continua 18%).
        assert m[("700002", "dg019.sp")][0] == Decimal("0.1200")
        assert m[("700002", "a020.sp")][0] == Decimal("0.1800")
        assert m[("700003", "dg019.ra+a020.ra")][0] == Decimal("0.0500")
        assert m[("700004", "z2821")][0] == Decimal("-0.1500")
        assert m[("700006", "z0093.mala")][0] == Decimal("0.3000")
        assert m[("700005", "x999")][0] == Decimal("0.0900")
        # Tamanho de mala e voltagem são produtos diferentes (a chave só perde a tag de envio).
        assert m[("700008", "b001.12.sp")][0] == Decimal("0.0700")
        assert m[("700009", "b001.24.sp")][0] == Decimal("0.0900")
        assert m[("700010", "uaf001m1.110")][0] == Decimal("0.1100")
        assert m[("700011", "uaf001m1.2l")][0] == Decimal("0.0900")
        margem, diff = m[("700001", "dg019.ra")][2], m[("700001", "dg019.ra")][1]
        assert diff == margem - Decimal("0.12")

        # Apagar o valor (NULL) volta a herdar; sem linha nenhuma, tudo igual à 0235.
        await db.execute(
            text(
                f'UPDATE "{rascunho}".produto_regras SET margem_min = NULL'
                " WHERE sku_base = 'dg019'"
            )
        )
        await db.commit()
        m = await _minimas(db, rascunho)
        assert m[("700001", "dg019.ra")][0] == Decimal("0.1800")
        assert m[("700003", "dg019.ra+a020.ra")][0] == Decimal("0.0500")
        await db.execute(text(f'DELETE FROM "{rascunho}".produto_regras'))
        await db.commit()
        assert await _linhas(db, rascunho) == antes

        # Downgrade: a view volta ao texto da 0235 e as tabelas saem.
        await db.execute(text(caminho))
        conn = await db.connection()
        await conn.run_sync(_passo, "downgrade")
        await db.commit()
        assert (
            await db.execute(text(f"SELECT pg_get_viewdef('\"{rascunho}\".vw_bling_pedidos')"))
        ).scalar_one() == def_antes
        assert await _linhas(db, rascunho) == antes
        sobrou = (
            await db.execute(
                text(
                    "SELECT count(*) FROM pg_tables WHERE schemaname = :s"
                    " AND tablename IN ('produto_regras', 'produto_condicoes_especiais')"
                ),
                {"s": rascunho},
            )
        ).scalar_one()
        assert sobrou == 0
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()


def test_conferencia_le_um_cte_vazio_e_nao_a_tabela_nova():
    mod = _carregar()
    sql = mod.select_conferencia()
    assert "pr_vazia AS (SELECT NULL::text AS sku_base" in sql
    assert "produto_regras" not in sql
    assert sql.count("COALESCE(pr.margem_min, leaf.min_margin,") == 2
    # A chave lê a lista de tags da produção (tabela da 0508, já no ar).
    assert '"davinci".produto_cadastros' in sql and "tp.chave = 'tag_envio'" in sql


# =========================================================== API


@pytest_asyncio.fixture
async def cenario(db: AsyncSession, make_user, tags_envio):
    admin = await make_user(role=UserRole.ADMIN)
    vendedor = await make_user()
    seg = await _arvore(db)
    db.add_all(
        [
            Product(
                user_id=admin.id, sku="dg019.ra", name="Uranyx F105", segment_id=seg.regular.id
            ),
            Product(
                user_id=admin.id, sku="dg019.sp", name="Uranyx F105", segment_id=seg.regular.id
            ),
            Product(user_id=admin.id, sku="a020.ra", name="Óculos", segment_id=seg.acess.id),
        ]
    )
    await db.commit()
    await db.execute(
        text(
            f'CREATE TABLE IF NOT EXISTS "{TEST_SCHEMA}".pricing_product_variant ('
            " pricing_product_id uuid NOT NULL, segment_id uuid, variant_norm text NOT NULL,"
            " vlen int NOT NULL)"
        )
    )
    await db.execute(text(f'DELETE FROM "{TEST_SCHEMA}".pricing_product_variant'))
    await db.execute(
        text(
            f'INSERT INTO "{TEST_SCHEMA}".pricing_product_variant VALUES'
            " (gen_random_uuid(), :seg, 'dg019', 5)"
        ),
        {"seg": str(seg.regular.id)},
    )
    await db.commit()
    yield SimpleNamespace(admin=admin, vendedor=vendedor, seg=seg)
    await db.execute(text(f'DELETE FROM "{TEST_SCHEMA}".pricing_product_variant'))
    await db.commit()


async def _get(client: AsyncClient, sku: str):
    return await client.get(f"/api/produto-regras/{sku}")


async def _put(client: AsyncClient, sku: str, **corpo):
    return await client.put(f"/api/produto-regras/{sku}", json=corpo)


def _hoje() -> date:
    return rotas._hoje()


async def test_get_sem_valor_mostra_o_herdado_em_cada_caso(client, auth_as, cenario):
    auth_as(cenario.admin)
    r = await _get(client, "dg019.ra")
    assert r.status_code == 200
    b = r.json()
    assert b["sku"] == "dg019.ra" and b["sku_base"] == "dg019"
    assert b["margem_min"] is None
    assert b["margem_min_herdada"] == 0.18
    assert b["margem_min_origem"] == "da categoria de venda Regular (Celular)"
    assert b["duimp_padrao"] is None
    assert b["duimp_herdada"] == _DUIMP_REGULAR
    assert b["duimp_importacao"] is None
    assert b["condicoes"] == [] and b["condicao_resumo"] == "—"
    assert b["pode_editar"] is True
    z = (await _get(client, "z2821")).json()
    assert (z["margem_min_herdada"], z["margem_min_origem"]) == (
        -0.15,
        "da Queima de estoque (código começa com z)",
    )
    nada = (await _get(client, "x999")).json()
    assert (nada["margem_min_herdada"], nada["margem_min_origem"]) == (0.09, "padrão do sistema")
    assert (await _get(client, "a020.ra")).json()["duimp_herdada"] == _DUIMP_ACESS
    # Código que não vira chave (peça vazia ou que é só a tag): recusado.
    for ruim in (".ra", "dg019.ra+.sp", "a++b"):
        assert (await _get(client, ruim)).status_code == 400, ruim
    # Só a tag de envio sai: o tamanho e a voltagem ficam na chave.
    assert (await _get(client, "b001.12.sp")).json()["sku_base"] == "b001.12"
    assert (await _get(client, "uaf001m1.110")).json()["sku_base"] == "uaf001m1.110"
    assert (await _get(client, "z0207.mala.us")).json()["sku_base"] == "z0207.mala.us"


async def test_put_grava_vale_para_as_outras_tags_e_vazio_volta_a_herdar(
    client, auth_as, cenario, db: AsyncSession
):
    auth_as(cenario.admin)
    r = await _put(client, "dg019.ra", margem_min=0.12)
    assert r.status_code == 200, r.text
    assert r.json()["margem_min"] == 0.12
    # O mesmo produto em outra tag lê o mesmo valor; o herdado continua visível.
    outra = (await _get(client, "dg019.sp")).json()
    assert (outra["margem_min"], outra["margem_min_herdada"]) == (0.12, 0.18)
    linha = await db.get(ProdutoRegra, "dg019", populate_existing=True)
    assert linha.margem_min == Decimal("0.1200") and linha.atualizado_por == cenario.admin.id
    assert linha.sku == "dg019"
    # Só a DUIMP: a margem fica como está.
    r = await _put(client, "dg019.sp", duimp_padrao="  celular  dg019 pela duimp X  ")
    assert r.json()["duimp_padrao"] == "celular dg019 pela duimp X"
    assert r.json()["margem_min"] == 0.12
    # Vazio volta a herdar (a linha fica, com o campo em branco).
    r = await _put(client, "dg019.ra", margem_min=None, duimp_padrao="")
    assert (r.json()["margem_min"], r.json()["duimp_padrao"]) == (None, None)
    linha = await db.get(ProdutoRegra, "dg019", populate_existing=True)
    assert linha.margem_min is None and linha.duimp_padrao is None
    # Apagar o que não existe não cria linha.
    await _put(client, "x999", margem_min="")
    assert await db.get(ProdutoRegra, "x999") is None
    # Kit tem o seu próprio valor; "12,5%" em fração com vírgula também vale.
    r = await _put(client, "dg019.ra+a020.ra", margem_min="0,125")
    assert (r.json()["sku_base"], r.json()["margem_min"]) == ("dg019+a020", 0.125)
    assert (await _get(client, "dg019.ra")).json()["margem_min"] is None
    # Mala de 12" e de 24" são produtos diferentes; a de 12" vale em todas as tags.
    r = await _put(client, "b001.12.sp", margem_min=0.07)
    assert (r.json()["sku_base"], r.json()["margem_min"]) == ("b001.12", 0.07)
    assert (await _get(client, "b001.12.ci")).json()["margem_min"] == 0.07
    assert (await _get(client, "b001.12")).json()["margem_min"] == 0.07
    assert (await _get(client, "b001.24.sp")).json()["margem_min"] is None
    assert await db.get(ProdutoRegra, "b001") is None


async def test_put_recusa_valor_fora_da_regra(client, auth_as, cenario, db: AsyncSession):
    auth_as(cenario.admin)
    casos = [
        ({"margem_min": 1.5}, "margem_invalida"),
        ({"margem_min": -0.01}, "margem_invalida"),
        ({"margem_min": "doze"}, "margem_invalida"),
        ({"duimp_padrao": "duimp 1 | duimp 2"}, "duimp_padrao_sem_barra"),
        ({"duimp_padrao": "x" * 76}, "duimp_padrao_muito_longo"),
        ({"preco": 10}, "campo_desconhecido"),
    ]
    for corpo, code in casos:
        r = await _put(client, "dg019.ra", **corpo)
        assert r.status_code == 400, corpo
        assert r.json()["detail"]["code"] == code
        assert r.json()["detail"]["message"]
    assert await db.get(ProdutoRegra, "dg019") is None


async def test_quem_nao_e_admin_ve_travado_e_recebe_403(client, auth_as, cenario, db):
    auth_as(cenario.vendedor)
    r = await _get(client, "dg019.ra")
    assert r.status_code == 200 and r.json()["pode_editar"] is False
    assert (await _put(client, "dg019.ra", margem_min=0.1)).status_code == 403
    hoje = _hoje().isoformat()
    corpo = {"pedido_de": hoje, "pedido_ate": hoje}
    assert (
        await client.post("/api/produto-regras/dg019.ra/condicoes", json=corpo)
    ).status_code == 403
    assert (
        await client.delete(f"/api/produto-regras/dg019.ra/condicoes/{uuid.uuid4()}")
    ).status_code == 403
    assert await db.get(ProdutoRegra, "dg019") is None


async def test_condicao_especial_do_produto_criar_resumir_e_apagar(client, auth_as, cenario):
    auth_as(cenario.admin)
    hoje = _hoje()
    url = "/api/produto-regras/dg019.ra/condicoes"
    r = await client.post(
        url,
        json={
            "pedido_de": hoje.isoformat(),
            "pedido_ate": (hoje + timedelta(days=4)).isoformat(),
            "piso": "0.05",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["condicao_resumo"] == f"até {(hoje + timedelta(days=4)):%d/%m} · piso 5%"
    futuro_de, futuro_ate = hoje + timedelta(days=10), hoje + timedelta(days=12)
    r = await client.post(
        "/api/produto-regras/dg019.sp/condicoes",
        json={"pedido_de": futuro_de.isoformat(), "pedido_ate": futuro_ate.isoformat()},
    )
    b = r.json()
    assert len(b["condicoes"]) == 2  # dg019.ra e dg019.sp são o mesmo produto
    assert b["condicao_resumo"].endswith(" +1")
    # Período invertido ou sem data: recusado.
    assert (
        await client.post(
            url,
            json={
                "pedido_de": hoje.isoformat(),
                "pedido_ate": (hoje - timedelta(days=1)).isoformat(),
            },
        )
    ).status_code == 400
    assert (await client.post(url, json={"pedido_de": hoje.isoformat()})).status_code == 400
    assert (
        await client.post(
            url, json={"pedido_de": hoje.isoformat(), "pedido_ate": hoje.isoformat(), "piso": 2}
        )
    ).status_code == 400
    primeira = next(c for c in b["condicoes"] if c["piso"] is not None)
    assert primeira["vigente"] is True and primeira["piso"] == "0.0500"
    assert (await client.delete(f"{url}/{primeira['id']}")).status_code == 204
    depois = (await _get(client, "dg019.ra")).json()
    assert depois["condicao_resumo"] == f"{futuro_de:%d/%m} a {futuro_ate:%d/%m} · sem piso"
    assert (await client.delete(f"{url}/{primeira['id']}")).status_code == 404
    # A condição de outro produto não se apaga por este código.
    outra = depois["condicoes"][0]["id"]
    assert (
        await client.delete(f"/api/produto-regras/a020.ra/condicoes/{outra}")
    ).status_code == 404


def test_resumo_da_condicao():
    hoje = date(2026, 11, 26)

    def c(de, ate, piso=None):
        return ProdutoCondicaoEspecial(pedido_de=de, pedido_ate=ate, piso=piso)

    assert svc.resumo_condicao([], hoje) == "—"
    assert svc.resumo_condicao([c(date(2026, 11, 1), date(2026, 11, 25))], hoje) == "—"
    assert (
        svc.resumo_condicao([c(date(2026, 11, 20), date(2026, 11, 30), Decimal("0.05"))], hoje)
        == "até 30/11 · piso 5%"
    )
    assert (
        svc.resumo_condicao(
            [
                c(date(2026, 12, 1), date(2026, 12, 5), Decimal("-0.125")),
                c(date(2026, 12, 10), date(2026, 12, 12)),
            ],
            hoje,
        )
        == "01/12 a 05/12 · piso -12,5% +1"
    )


async def test_historico_registra_quem_mudou_antes_e_depois(client, db: AsyncSession, cenario):
    """Pelo login de verdade (cookie): é o caminho que diz ao Histórico quem é."""
    token, _, _ = issue_session_token(sub=cenario.admin.open_id, role=cenario.admin.role.value)
    client.cookies.set(get_settings().cookie_name, token)
    assert (await _put(client, "dg019.ra", margem_min=0.12)).status_code == 200
    assert (
        await _put(client, "dg019.ra", margem_min=None, duimp_padrao="texto")
    ).status_code == 200
    hoje = _hoje().isoformat()
    assert (
        await client.post(
            "/api/produto-regras/dg019.ra/condicoes",
            json={"pedido_de": hoje, "pedido_ate": hoje, "piso": 0.05},
        )
    ).status_code == 201
    alteracoes = (
        (
            await db.execute(
                select(HistoricoAlteracao)
                .where(
                    HistoricoAlteracao.tabela.in_(["produto_regras", "produto_condicoes_especiais"])
                )
                .order_by(HistoricoAlteracao.id)
            )
        )
        .scalars()
        .all()
    )
    assert [(a.tabela, a.operacao) for a in alteracoes] == [
        ("produto_regras", "I"),
        ("produto_regras", "U"),
        ("produto_condicoes_especiais", "I"),
    ]
    criou, mudou, condicao = alteracoes
    assert {str(a.ator_id) for a in alteracoes} == {str(cenario.admin.id)}
    assert criou.rotulo == "dg019" and criou.depois["margem_min"] == 0.12
    assert mudou.antes["margem_min"] == 0.12 and mudou.depois["margem_min"] is None
    assert mudou.depois["duimp_padrao"] == "texto"
    assert condicao.rotulo == "dg019" and condicao.depois["piso"] == 0.05
    # Os nomes que aparecem na tela do Histórico.
    assert nomes.nome_campo("margem_min") == "Margem mín (%)"
    assert nomes.nome_campo("duimp_padrao") == "DUIMP padrão"
    assert nomes.nome_tabela("produto_condicoes_especiais") == "Condição especial (Produtos 2)"
    assert nomes.nome_tabela("produto_regras") == "Regras do produto (Produtos 2)"
    assert leitura.fmt(0.12, "margem_min") == "12%"
    assert leitura.fmt(0.05, "piso") == "5%"
    assert nomes.tela_da_api("/api/produto-regras/dg019.ra") == "Produtos 2 › Cadastro do produto"


# =========================================================== Margem: Condição especial


def _margem_permissions() -> dict:
    return {"margem": {"view": True, "edit": True, "delete": False}}


async def test_condicao_do_produto_vale_antes_da_do_segmento(
    client, db: AsyncSession, make_user, auth_as, tags_envio
):
    """Segmento com Condição especial de 01 a 30/09 SEM piso (aprova tudo). O produto dg019 tem
    a dele de 01 a 10/09 com piso de 5%:
      - dg019.ra em 05/09, margem 3%  → vale a do produto: 3% < 5% → margem baixa;
      - dg019.sp em 05/09, margem 8%  → vale a do produto e passa → aprovado;
      - a001.sp  em 05/09, margem 3%  → sem condição do produto: a do segmento aprova;
      - dg019.ra em 20/09, margem 3%  → fora do período do produto: a do segmento aprova.
    Sem a condição do produto, o primeiro volta a ser aprovado pela do segmento."""
    user = await make_user(permissions=_margem_permissions())
    auth_as(user)
    folha = Segment(
        name="Folha M74", slug=f"folha-{uuid.uuid4().hex[:6]}", min_margin=Decimal("0.15")
    )
    db.add(folha)
    await db.flush()
    db.add_all(
        [
            SegmentSpecialDate(
                segment_id=folha.id,
                date_start=date(2026, 9, 1),
                date_end=date(2026, 9, 30),
                min_margin=None,
            ),
            ProdutoCondicaoEspecial(
                sku_base="dg019",
                pedido_de=date(2026, 9, 1),
                pedido_ate=date(2026, 9, 10),
                piso=Decimal("0.05"),
            ),
            # Mala de 12": vale para b001.12 em qualquer tag, não para a de 24".
            ProdutoCondicaoEspecial(
                sku_base="b001.12",
                pedido_de=date(2026, 9, 1),
                pedido_ate=date(2026, 9, 10),
                piso=Decimal("0.05"),
            ),
        ]
    )
    await db.commit()

    async def seed(pedido: str, sku: str, dia: int, margem: str) -> None:
        await db.execute(
            text(
                """
                INSERT INTO verificar_margem (
                    bling_order_item_id, pedido_bling, sku, produto, data,
                    situacao, situacao_nome, plataforma_bling, item_proportion,
                    marketplace_margem, margem_minima, pricing_leaf_segment_id
                ) VALUES (
                    :id, :pedido, :sku, 'Produto', :dt, '6', 'Em aberto', 'ml', 1,
                    :margem, 0.15, :leaf
                )
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "pedido": pedido,
                "sku": sku,
                "dt": datetime(2026, 9, dia, 15, tzinfo=UTC),
                "margem": margem,
                "leaf": str(folha.id),
            },
        )
        await db.commit()

    await seed("740001", "dg019.ra", 5, "0.03")
    await seed("740002", "dg019.sp", 5, "0.08")
    await seed("740003", "a001.sp", 5, "0.03")
    await seed("740004", "dg019.ra", 20, "0.03")
    await seed("740005", "b001.12.sp", 5, "0.03")
    await seed("740006", "b001.24.sp", 5, "0.03")

    async def presos() -> set[str]:
        r = await client.get(
            "/api/margens/marketplace?situacao=all&attention_type=margem&status=Pendente"
        )
        assert r.status_code == 200
        return {it["pedido_bling"] for it in r.json()["items"]}

    assert await presos() == {"740001", "740005"}
    todos = await client.get("/api/margens/marketplace?situacao=all")
    especial = {it["pedido_bling"]: it["data_especial"] for it in todos.json()["items"]}
    assert especial == {
        "740001": False,
        "740002": True,
        "740003": True,
        "740004": True,
        "740005": False,  # b001.12.sp → a do produto b001.12 (3% < 5%)
        "740006": True,  # b001.24.sp é outro produto: a do segmento aprova
    }

    await db.execute(text("DELETE FROM produto_condicoes_especiais"))
    await db.commit()
    assert await presos() == set()


# =========================================================== Nota: DUIMP


async def test_duimp_da_nota_importacao_produto_segmento(db: AsyncSession, make_user, tags_envio):
    admin = await make_user(role=UserRole.ADMIN)
    seg = await _arvore(db)
    db.add_all(
        [
            Product(user_id=admin.id, sku="dg019.ra", name="F105", segment_id=seg.regular.id),
            Product(user_id=admin.id, sku="dg020.ra", name="F106", segment_id=seg.regular.id),
            Product(user_id=admin.id, sku="a020.ra", name="Óculos", segment_id=seg.acess.id),
            Product(user_id=admin.id, sku="a001.ra", name="Fone", segment_id=seg.acess.id),
        ]
    )
    await db.commit()
    skus = {"dg019.ra", "dg020.ra", "dg019.ra+a020.ra", "dg020.ra+a001.ra", "a020.ra"}
    # Ninguém preencheu: igual a antes (texto do segmento).
    antes = await nf._duimp_por_sku(db, skus)
    assert antes == {
        "dg019.ra": _DUIMP_REGULAR,
        "dg020.ra": _DUIMP_REGULAR,
        "dg019.ra+a020.ra": f"{_DUIMP_REGULAR} | {_DUIMP_ACESS}",
        "dg020.ra+a001.ra": f"{_DUIMP_REGULAR} | {_DUIMP_ACESS}",
        "a020.ra": _DUIMP_ACESS,
    }
    db.add_all(
        [
            ProdutoRegra(sku_base="dg019", duimp_padrao="dg019 pela duimp P"),
            ProdutoRegra(sku_base="dg019+a020", duimp_padrao="kit pela duimp K"),
            ProdutoRegra(sku_base="a001", duimp_padrao="fone pela duimp F"),
            ProdutoRegra(sku_base="dg020", margem_min=Decimal("0.1")),  # só margem: DUIMP herda
            ImportProduct(
                categoria="celular", sku="a020.ra", custo_bling=Decimal("0"), duimp=_DUIMP_IMPORT
            ),
        ]
    )
    await db.commit()
    depois = await nf._duimp_por_sku(db, skus)
    assert depois == {
        # produto vence o segmento
        "dg019.ra": "dg019 pela duimp P",
        # produto sem DUIMP (só margem): segmento, como antes
        "dg020.ra": _DUIMP_REGULAR,
        # kit: a peça com DUIMP na Importação leva a dela; a outra leva a do kit
        "dg019.ra+a020.ra": f"kit pela duimp K | {_DUIMP_IMPORT}",
        # kit sem valor próprio: cada peça com a sua (produto da peça → segmento)
        "dg020.ra+a001.ra": f"{_DUIMP_REGULAR} | fone pela duimp F",
        # a Importação continua vencendo
        "a020.ra": _DUIMP_IMPORT,
    }


async def test_nota_leva_a_duimp_padrao_do_produto(db: AsyncSession, make_user, tags_envio):
    """Ponta a ponta: a observação da nota (faturador com observacao_duimp)."""
    from app.models import NfFaturador, StoreInfo

    admin = await make_user(role=UserRole.ADMIN)
    seg = await _arvore(db)
    fat = NfFaturador(
        nome="bling 100%",
        modo="bling",
        nf_cheia=True,
        sku_fonte="principal",
        nome_fonte="produto",
        observacao_duimp=True,
    )
    db.add(fat)
    await db.flush()
    db.add_all(
        [
            StoreInfo(
                user_id=admin.id,
                platform="amazon",
                account_name="am1",
                bling_store_id="950074",
                nf_faturador_id=fat.id,
            ),
            Product(user_id=admin.id, sku="dg019.ra", name="F105", segment_id=seg.regular.id),
            Product(user_id=admin.id, sku="a020.ra", name="Óculos", segment_id=seg.acess.id),
            ProdutoRegra(sku_base="dg019", duimp_padrao="dg019 pela duimp P"),
        ]
    )
    for i, sku in enumerate(["dg019.ra", "a020.ra"]):
        db.add(
            BlingOrder(
                numero="85074",
                data=datetime(2026, 11, 26, tzinfo=UTC),
                loja="950074",
                situacao="15",
                item_index=i,
                item_codigo=sku,
                item_descricao=f"Item {sku}",
                item_quantidade=1,
                itemvalor=100,
                nome_destinatario="Cliente Teste",
                cep_destino="30570050",
                endereco_destino="Rua A",
                numero_destino="1",
                bairro_destino="Centro",
                cidade_destino="Belo Horizonte",
                uf_destino="MG",
            )
        )
    await db.commit()
    import csv
    import io

    res = await nf.gerar_por_faturador(db, ["85074"])
    linhas = list(
        csv.reader(io.StringIO(res.blocos[0].planilha.decode("utf-8-sig")), delimiter=";")
    )
    obs = linhas[1][linhas[0].index("Observações")]
    assert obs == f"dg019 pela duimp P | {_DUIMP_ACESS}"


async def test_duimp_padrao_pela_chave_sem_a_tag(db: AsyncSession, tags_envio):
    db.add(ProdutoRegra(sku_base="b001.12", duimp_padrao="mala 12 pela duimp M"))
    await db.commit()
    got = await svc.duimp_padrao_dos_produtos(
        db, {"b001.12.sp", "B001.12", "b001.24.sp", "b001.12.18", ""}
    )
    assert got == {"b001.12.sp": "mala 12 pela duimp M", "B001.12": "mala 12 pela duimp M"}
    assert await svc.duimp_padrao_dos_produtos(db, set()) == {}


@pytest.mark.parametrize("valor", [None, ""])
def test_margem_vazia_e_none(valor):
    assert rotas._margem(valor) is None
