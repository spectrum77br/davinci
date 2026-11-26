"""M-93 (Harry 26/11/2026): até 10 categorias de venda por tabela de Contas — migration 0520.

A categoria de venda liga o produto à margem/frete da conta pela POSIÇÃO (services/
categorias_venda.py). A 0520 criou as posições 6..10 de `pricing_accounts` (margin6..10,
shipping6..10, slot6..10_segment_id), todas vazias; o número mora em
services/pricing/posicoes.MAX_CATEGORIAS_VENDA. Cobre:
- um número só: modelo, schemas, categorias_venda e Histórico com as 10 posições;
- o cálculo: margem na posição 8 gera preço; posição sem margem = sem preço (sem valor padrão);
  o caminho antigo (tipo = kit da conta) continua preso em 1..5;
- a API: o exemplo do pedido (Amazon atlas com 53,0% / 30 nas cinco primeiras e "—" nas cinco
  novas), PATCH da posição 8 só pelo administrador (M-10), o par do ML grava nas duas linhas, a
  coluna de catálogo com margem própria na posição 8, o Custo total (M-86) com margem só na
  posição 9, o Produtos 2 trocando para a 8ª, o envio (tipo do produto) e o frete da NF;
- a migration 0520: cria o que o modelo declara (vazio, nada reescrito), estende o frete
  projetado das views/funções da Margem e o downgrade desfaz (e sobe de novo).
Contas e categorias FABRICADAS no schema de teste.
"""

# ruff: noqa: S608
from __future__ import annotations

import importlib.util
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.historico import leitura, nomes
from app.models import (
    Base,
    PricingAccount,
    PricingPlatform,
    PricingProduct,
    Segment,
    StoreInfo,
    UserRole,
)
from app.schemas.pricing import PricingAccountBase, PricingAccountOut, PricingAccountPatch
from app.services import categorias_venda, nf_frete_auto, produto_regras
from app.services.pricing import push
from app.services.pricing.calc import calculate
from app.services.pricing.custo_total import calcular_custo_total
from app.services.pricing.posicoes import (
    CAMPOS_FRETE,
    CAMPOS_MARGEM,
    CAMPOS_SLOT,
    MAX_CATEGORIAS_VENDA,
    POSICOES,
)

D = Decimal
ELETRO = (
    "Acessórios",
    "airfryer",
    "barbecue",
    "slush",
    "5",
    "cafeteira",
    "cooktop",
    "cookware",
    "purificador",
    "smart cooking",
)
PERM_EDITA_CONTAS = {
    "tabela_precos": {"view": True, "edit": False, "delete": False},
    "tabela_precos_contas": {"view": True, "edit": True, "delete": True},
}


# =========================================================== um número só


def test_um_numero_so_para_as_10_posicoes():
    assert MAX_CATEGORIAS_VENDA == 10
    assert POSICOES == tuple(range(1, 11))
    assert categorias_venda.LIMITE == categorias_venda.MAX_CATEGORIAS_VENDA == 10
    colunas = PricingAccount.__table__.c
    for campos, tipo in ((CAMPOS_MARGEM, "NUMERIC(6, 4)"), (CAMPOS_FRETE, "NUMERIC(8, 2)")):
        assert [str(colunas[c].type) for c in campos] == [tipo] * 10
    for campo in CAMPOS_SLOT:
        (fk,) = colunas[campo].foreign_keys
        assert (fk.column.table.name, fk.ondelete, colunas[campo].nullable) == (
            "segments",
            "SET NULL",
            True,
        )
    # A API expõe as posições novas com os mesmos nomes de 1..5.
    for esquema in (PricingAccountBase, PricingAccountPatch, PricingAccountOut):
        assert set(CAMPOS_MARGEM + CAMPOS_FRETE + CAMPOS_SLOT) <= set(esquema.model_fields)
    assert {f"slot{n}_segment_name" for n in POSICOES} <= set(PricingAccountOut.model_fields)
    # Histórico: nome e formato das colunas novas.
    assert (nomes.nome_campo("margin8"), nomes.nome_campo("shipping10")) == ("Margem 8", "Frete 10")
    assert {"margin6", "margin10"} <= leitura.PERCENTUAIS
    assert {"shipping6", "shipping10"} <= leitura.DINHEIRO


# =========================================================== o cálculo


def _conta(**kw) -> PricingAccount:
    return PricingAccount(**{"commission": D("0.20"), "kit_number": 1, "canal": "kit", **kw})


def test_calculo_posicao_8_e_posicao_sem_margem():
    conta = _conta(margin8=D("0.30"), shipping8=D("10"))
    produto = PricingProduct(cost_kit1=D("100"))
    # (100 × 1,30 + 10) ÷ 0,80 = 175.
    r = calculate(conta, produto, None, 8)
    assert (r.price, r.source, r.inputs["product_type"]) == (D("175"), "computed", 8)
    # Categoria ainda sem margem na conta: sem preço, como hoje (nada de valor padrão).
    r = calculate(conta, produto, None, 9)
    assert (r.price, r.source) == (None, "missing_inputs")
    assert r.inputs["margin"] is None
    # Tipo acima de 10 cai na 10 (vazia aqui).
    assert calculate(conta, produto, None, 12).inputs["product_type"] == 10


def test_caminho_antigo_continua_preso_em_5():
    """Sem o tipo do produto (services/audit/runner), o tipo vira o kit da conta e continua
    preso em 1..5 como antes da M-93: kit 7 usa a margem 5, não a 7."""
    conta = _conta(margin5=D("0.50"), margin7=D("0.90"), kit_number=7)
    produto = PricingProduct(cost_kit1=D("100"), cost_kit7=D("100"))
    r = calculate(conta, produto)
    assert (r.inputs["product_type"], r.price) == (5, D("188"))  # (150 + 0) ÷ 0,8 = 187,5
    # Com o tipo resolvido, a 7.
    assert calculate(conta, produto, None, 7).price == D("238")  # 190 ÷ 0,8 = 237,5


def test_catalogo_margem_propria_na_posicao_8():
    base = _conta(margin8=D("0.30"), shipping8=D("10"))
    filha = PricingAccount(canal="catalogo")
    produto = PricingProduct(cost_kit1=D("1"), preco_catalogo=D("100"))
    assert calculate(filha, produto, None, 8, conta_base=base).price == D("175")
    filha.margin8 = D("0.50")  # (150 + 10) ÷ 0,8 = 200
    r = calculate(filha, produto, None, 8, conta_base=base)
    assert (r.price, r.inputs["margem_propria"]) == (D("200"), True)


# =========================================================== a API


@pytest_asyncio.fixture
async def cena(db: AsyncSession, make_user) -> SimpleNamespace:
    """Eletro com as 10 categorias do exemplo do pedido (as 5 primeiras de antes e as 5 novas),
    um produto em cada uma e a Amazon atlas com 53,0% / 30 nas cinco primeiras."""
    admin = await make_user(role=UserRole.ADMIN)
    vendedor = await make_user(role=UserRole.USER, permissions=PERM_EDITA_CONTAS)
    raiz = Segment(name="Eletro", slug="eletro", sort_order=2)
    db.add(raiz)
    await db.flush()
    folhas = []
    for i, nome in enumerate(ELETRO):
        folha = Segment(parent_id=raiz.id, name=nome, slug=f"eletro-{i}", sort_order=i)
        db.add(folha)
        folhas.append(folha)
    await db.flush()
    produtos = [
        PricingProduct(
            user_id=admin.id,
            sku=f"ue{i + 1:03d}",
            name=f"Produto {nome}",
            segment_id=folha.id,
            cost_kit1=D("100"),
            preco_catalogo=D("100"),
        )
        for i, (nome, folha) in enumerate(zip(ELETRO, folhas, strict=True))
    ]
    db.add_all(produtos)
    atlas = PricingAccount(
        user_id=admin.id,
        name="Amazon atlas",
        platform=PricingPlatform.AMAZON,
        segment_id=raiz.id,
        commission=D("0.14"),
        **{f"margin{n}": D("0.53") for n in range(1, 6)},
        **{f"shipping{n}": D("30") for n in range(1, 6)},
        **{f"slot{n}_segment_id": folhas[n - 1].id for n in POSICOES},
    )
    db.add(atlas)
    await db.commit()
    return SimpleNamespace(
        admin=admin, vendedor=vendedor, raiz=raiz, folhas=folhas, produtos=produtos, atlas=atlas
    )


async def _grade(client: AsyncClient) -> dict[tuple[str, str], dict]:
    r = await client.get("/api/pricing/grid", params={"department": "eletro"})
    assert r.status_code == 200, r.text
    return {(c["pricing_account_id"], c["pricing_product_id"]): c for c in r.json()["cells"]}


async def _ler(db: AsyncSession, conta_id) -> PricingAccount:
    return (
        await db.execute(
            select(PricingAccount)
            .where(PricingAccount.id == conta_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


def _preco(margem: str, frete: str, comissao: str = "0.14") -> str:
    """(100 × (1 + m) + f) ÷ (1 − k), inteiro (como o calc)."""
    valor = (D("100") * (1 + D(margem)) + D(frete)) / (1 - D(comissao))
    return str(valor.quantize(D("1"), rounding="ROUND_HALF_UP"))


async def test_exemplo_do_pedido_e_margem_na_posicao_8(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cena
):
    auth_as(cena.admin)
    conta = str(cena.atlas.id)
    r = await client.get("/api/pricing/accounts", params={"department": "eletro"})
    assert r.status_code == 200, r.text
    (atlas,) = r.json()
    # 53,0% / 30 nas cinco primeiras e "—" (vazio) nas cinco novas.
    assert [atlas[f"margin{n}"] for n in POSICOES] == ["0.5300"] * 5 + [None] * 5
    assert [atlas[f"shipping{n}"] for n in POSICOES] == ["30.00"] * 5 + [None] * 5
    assert [atlas[f"slot{n}_segment_name"] for n in POSICOES] == list(ELETRO)
    grade = await _grade(client)
    celulas = [grade[(conta, str(p.id))] for p in cena.produtos]
    assert [c["price"] for c in celulas] == [_preco("0.53", "30")] * 5 + [None] * 5
    assert [c["source"] for c in celulas[5:]] == ["missing_inputs"] * 5

    # Margem e frete na posição 8 (cookware): o produto dela ganha preço; os outros não mudam.
    r = await client.patch(
        f"/api/pricing/accounts/{conta}", json={"margin8": "0.40", "shipping8": "25"}
    )
    assert r.status_code == 200, r.text
    assert (r.json()["margin8"], r.json()["shipping8"]) == ("0.4000", "25.00")
    linha = await _ler(db, cena.atlas.id)
    assert (linha.margin8, linha.shipping8, linha.margin1) == (D("0.4"), D("25"), D("0.53"))
    depois = await _grade(client)
    novos = [depois[(conta, str(p.id))]["price"] for p in cena.produtos]
    assert novos == [_preco("0.53", "30")] * 5 + [None, None, _preco("0.40", "25"), None, None]


async def test_posicoes_novas_so_do_administrador(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cena
):
    """M-10: margem e frete (as posições novas também) só o administrador vê e grava."""
    auth_as(cena.vendedor)
    r = await client.get("/api/pricing/accounts", params={"department": "eletro"})
    assert r.status_code == 200, r.text
    (atlas,) = r.json()
    assert all(atlas[c] is None for c in CAMPOS_MARGEM + CAMPOS_FRETE)
    r = await client.patch(f"/api/pricing/accounts/{cena.atlas.id}", json={"margin9": "0.5"})
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["campos"] == ["margin9"]
    assert (await _ler(db, cena.atlas.id)).margin9 is None


async def test_par_do_ml_e_catalogo_nas_posicoes_novas(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cena
):
    auth_as(cena.admin)
    r = await client.post(
        "/api/pricing/accounts",
        json={"name": "ML atlas", "platform": "mercadolivre", "segment_id": str(cena.raiz.id)},
    )
    assert r.status_code == 201, r.text
    classico = r.json()
    # Conta nova já nasce com os 10 vínculos (o frete "Projetado" da Margem).
    assert [classico[f"slot{n}_segment_id"] for n in POSICOES] == [str(f.id) for f in cena.folhas]
    # Gravar a posição 9 numa linha do par grava nas duas (M-53).
    r = await client.patch(
        f"/api/pricing/accounts/{classico['id']}", json={"margin9": "0.35", "shipping9": "12"}
    )
    assert r.status_code == 200, r.text
    linhas = (
        (
            await db.execute(
                select(PricingAccount)
                .where(PricingAccount.par_id == classico["par_id"])
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    assert len(linhas) == 2
    assert {(x.margin9, x.shipping9, x.slot9_segment_id) for x in linhas} == {
        (D("0.35"), D("12"), cena.folhas[8].id)
    }

    # Coluna de catálogo: margem própria na posição 8 (`margens_catalogo` com as 10).
    r = await client.post(f"/api/pricing/accounts/{classico['id']}/catalogo", json={"ativo": True})
    assert r.status_code == 200, r.text
    contas = (await client.get("/api/pricing/accounts", params={"department": "eletro"})).json()
    filha = next(
        c for c in contas if c["canal"] == "catalogo" and c["conta_base_id"] == classico["id"]
    )
    assert sorted(filha["margens_catalogo"], key=int) == [str(n) for n in POSICOES]
    r = await client.patch(f"/api/pricing/accounts/{filha['id']}", json={"margin8": "0.5"})
    assert r.status_code == 200, r.text
    assert D(r.json()["margens_catalogo"]["8"]) == D("0.5")
    # A filha herda da base a margem/frete das 10 posições.
    assert (r.json()["margin9"], r.json()["shipping9"]) == ("0.3500", "12.00")


async def test_custo_total_com_margem_so_na_posicao_9(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cena
):
    """M-86: o Custo total sai da menor marg entre as categorias ATIVAS preenchidas — aqui só a
    da posição 9 (as outras vazias). Desativadas a 10ª e a 9ª, nenhuma ativa tem marg: vazio."""
    auth_as(cena.admin)
    conta = PricingAccount(
        user_id=cena.admin.id,
        name="Shopee vortan",
        platform=PricingPlatform.SHOPEE,
        segment_id=cena.raiz.id,
        commission=D("0.14"),
    )
    db.add(conta)
    await db.commit()
    url = f"/api/pricing/accounts/{conta.id}"
    r = await client.patch(url, json={"margem_minima_pct": 25})
    assert r.status_code == 200, r.text
    assert r.json()["custo_total_pct"] is None  # nenhuma marg
    r = await client.patch(url, json={"margin9": "0.356"})
    assert r.status_code == 200, r.text
    esperado = calcular_custo_total([D("0.356")], D("25"), [D("0.14")])
    assert esperado == D("6")
    assert D(r.json()["custo_total_pct"]) == esperado
    # Uma marg menor na posição 10 também conta (10 ativas).
    r = await client.patch(url, json={"margin10": "0.30"})
    assert D(r.json()["custo_total_pct"]) == calcular_custo_total([D("0.30")], D("25"), [D("0.14")])
    for folha in (cena.folhas[9], cena.folhas[8]):
        r = await client.patch(f"/api/segments/{folha.id}", json={"active": False})
        assert r.status_code == 200, r.text
    assert (await _ler(db, conta.id)).custo_total_pct is None


async def test_produtos_2_troca_para_a_8a_e_envio_le_o_tipo(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cena
):
    """Produtos 2 troca a categoria de venda mandando o `product_type` da opção (antes preso em
    1..5: a 8ª virava a 5ª). O envio às plataformas lê o tipo 8 da linha."""
    auth_as(cena.admin)
    produto = cena.produtos[0]
    linha = produto_regras.linha_out(await produto_regras.linha_da_tabela(db, produto.id))
    assert linha["product_type"] == 1
    assert [o["product_type"] for o in linha["opcoes"]] == list(POSICOES)
    oitava = next(o for o in linha["opcoes"] if o["product_type"] == 8)
    assert oitava["nome"] == "cookware"
    r = await client.patch(f"/api/pricing/products/{produto.id}", json={"product_type": 8})
    assert r.status_code == 200, r.text
    assert (r.json()["segment_id"], r.json()["product_type"]) == (oitava["segment_id"], 8)
    await db.refresh(produto)
    assert await push._resolve_product_type(db, produto) == 8


async def test_frete_da_nf_le_a_posicao_7(
    db: AsyncSession, client: AsyncClient, auth_as: Callable, cena
):
    loja = StoreInfo(
        user_id=cena.admin.id, platform="amazon", account_name="atlas", bling_store_id="m93"
    )
    db.add(loja)
    await db.flush()
    conta = await _ler(db, cena.atlas.id)
    conta.store_info_id = loja.id
    conta.shipping7 = D("44")
    await db.commit()
    valor, aviso = await nf_frete_auto._resolver_frete_projetado(db, loja.id, ["ue007"])
    assert (valor, aviso) == (D("44"), None)


# =========================================================== migration 0520

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "0520_contas_dez_categorias.py"
)
_NOVAS = [f"{c}{n}" for n in range(6, 11) for c in ("margin", "shipping")] + [
    f"slot{n}_segment_id" for n in range(6, 11)
]


def _carregar():
    spec = importlib.util.spec_from_file_location("migration_0520", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _case(n: int) -> str:
    return "\n".join(
        f"            WHEN pa.slot{i}_segment_id = leaf.id THEN pa.shipping{i}"
        for i in range(1, n + 1)
    )


def _ou(n: int) -> str:
    return " OR ".join(f"leaf.id = pa.slot{i}_segment_id" for i in range(1, n + 1))


def _sql_frete(s: str) -> str:
    """O trecho do frete projetado como nas views/funções da produção (CASE + JOIN por slot)."""
    return (
        f"SELECT pp.sku::text AS sku, pa.name::text AS conta, CASE\n{_case(5)}\n"
        "            ELSE NULL::numeric END AS frete_projetado_unit\n"
        f'FROM "{s}".pricing_products pp JOIN "{s}".segments leaf ON leaf.id = pp.segment_id\n'
        f"LEFT JOIN \"{s}\".pricing_accounts pa ON pa.name = 'atlas' AND ({_ou(5)})"
    )


async def _colunas(db: AsyncSession, schema: str) -> list[tuple]:
    return [
        tuple(r)
        for r in (
            await db.execute(
                text(
                    "SELECT column_name, data_type, numeric_precision, numeric_scale, is_nullable "
                    "FROM information_schema.columns WHERE table_schema = :s "
                    "AND table_name = 'pricing_accounts' AND column_name = ANY(:c) "
                    "ORDER BY column_name"
                ),
                {"s": schema, "c": _NOVAS},
            )
        ).all()
    ]


async def _fks_e_indices(db: AsyncSession, schema: str) -> tuple[list, list]:
    fks = (
        await db.execute(
            text(
                "SELECT co.conname, pg_get_constraintdef(co.oid) FROM pg_constraint co "
                "JOIN pg_class cl ON cl.oid = co.conrelid "
                "JOIN pg_namespace n ON n.oid = cl.relnamespace "
                "WHERE n.nspname = :s AND cl.relname = 'pricing_accounts' AND co.contype = 'f' "
                "ORDER BY 1"
            ),
            {"s": schema},
        )
    ).all()
    ix = (
        (
            await db.execute(
                text(
                    "SELECT indexname FROM pg_indexes WHERE schemaname = :s "
                    "AND tablename = 'pricing_accounts' AND indexname LIKE 'ix_%' ORDER BY 1"
                ),
                {"s": schema},
            )
        )
        .scalars()
        .all()
    )
    return [(r[0], r[1].replace(f'"{schema}".', "").replace(f"{schema}.", "")) for r in fks], list(
        ix
    )


async def test_migration_0520_cria_vazias_estende_o_frete_e_desce(db: AsyncSession, capsys):
    mod = _carregar()
    assert (mod.revision, mod.down_revision) == (
        "0520_contas_dez_categorias",
        "0519_contas_margem_minima",
    )
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    raiz = Path(__file__).resolve().parent.parent
    cfg = Config(str(raiz / "alembic.ini"))
    cfg.set_main_option("script_location", str(raiz / "alembic"))
    assert len(ScriptDirectory.from_config(cfg).get_heads()) == 1

    modelo = Base.metadata.schema
    r = f"{modelo}_mig0520"
    await db.execute(text(f'DROP SCHEMA IF EXISTS "{r}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{r}"'))
    for t in ("segments", "pricing_products", "pricing_accounts"):
        await db.execute(text(f'CREATE TABLE "{r}".{t} (LIKE "{modelo}".{t} INCLUDING ALL)'))
    for c in _NOVAS:
        await db.execute(text(f'ALTER TABLE "{r}".pricing_accounts DROP COLUMN {c}'))
    raiz_id, f1, f6, f7 = uuid4(), uuid4(), uuid4(), uuid4()
    await db.execute(
        text(
            f'INSERT INTO "{r}".segments (id, name, slug, sort_order, parent_id) VALUES '
            "(:raiz, 'Eletro', 'eletro', 0, NULL), (:f1, 'Acessórios', 'e1', 0, :raiz), "
            # Dado antigo: categoria na posição 6 com produto (a migration avisa).
            "(:f6, 'cafeteira', 'e6', 5, :raiz), (:f7, 'cooktop', 'e7', 6, :raiz)"
        ),
        {"raiz": raiz_id, "f1": f1, "f6": f6, "f7": f7},
    )
    dono = uuid4()
    await db.execute(
        text(
            f'INSERT INTO "{r}".pricing_products (id, user_id, sku, name, segment_id) VALUES '
            "(gen_random_uuid(), :u, 'ue001', 'a', :f1), "
            "(gen_random_uuid(), :u, 'ue006', 'c', :f6), "
            "(gen_random_uuid(), :u, 'ue007', 'b', :f7)"
        ),
        {"u": dono, "f1": f1, "f6": f6, "f7": f7},
    )
    await db.execute(
        text(
            f'INSERT INTO "{r}".pricing_accounts (id, user_id, name, platform, segment_id, '
            "margin1, shipping1, margin5, shipping5, slot1_segment_id, commission) VALUES "
            "(gen_random_uuid(), :u, 'atlas', 'amazon', :raiz, 0.53, 30, 0.5, 31, :f1, 0.14)"
        ),
        {"u": dono, "raiz": raiz_id, "f1": f1},
    )
    # As views (lidas sem "pretty": comparações entre parênteses) e uma das funções (corpo como
    # foi escrito, sem parênteses); a outra função não existe aqui (é pulada).
    for v in mod.VIEWS:
        await db.execute(text(f'CREATE VIEW "{r}".{v} AS {_sql_frete(r)}'))
    await db.execute(
        text(
            f'CREATE FUNCTION "{r}".conciliacao_margens_for_bling_id(p_bling_id bigint) '
            "RETURNS TABLE(sku text, conta text, frete numeric) LANGUAGE sql STABLE AS $$ "
            f"{_sql_frete(r)} $$"
        )
    )
    await db.commit()

    async def _contas() -> list[tuple]:
        return [
            tuple(x)
            for x in (
                await db.execute(
                    text(
                        "SELECT name, margin1::text, shipping1::text, margin5::text, "
                        "shipping5::text, slot1_segment_id, commission::text "
                        f'FROM "{r}".pricing_accounts ORDER BY name'
                    )
                )
            ).all()
        ]

    async def _fretes() -> dict[str, list[tuple]]:
        saida = {}
        for v in mod.VIEWS:
            saida[v] = [
                tuple(x)
                for x in (
                    await db.execute(
                        text(f'SELECT sku, conta, frete_projetado_unit FROM "{r}".{v} ORDER BY sku')
                    )
                ).all()
            ]
        saida["fn"] = [
            tuple(x)
            for x in (
                await db.execute(
                    text(f'SELECT * FROM "{r}".conciliacao_margens_for_bling_id(1) ORDER BY 1')
                )
            ).all()
        ]
        return saida

    def _rodar(conn, passo: str) -> None:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            getattr(mod, passo)()

    async def _passo(passo: str) -> None:
        conn = await db.connection()
        await conn.run_sync(_rodar, passo)
        await db.commit()

    contas_antes, fretes_antes = await _contas(), await _fretes()
    esperado_antes = [("ue001", "atlas", D("30.00")), ("ue006", None, None), ("ue007", None, None)]
    assert all(v == esperado_antes for v in fretes_antes.values())
    mod.SCHEMA = r
    try:
        await _passo("upgrade")
        saida = capsys.readouterr().out
        assert "função" in saida and "refunds_freight_for_pedido" in saida
        assert "AVISO" in saida and "cafeteira" in saida and "posição 6" in saida
        # As 15 colunas do modelo, vazias; nada do que existia muda.
        modelo_cols = await _colunas(db, modelo)
        assert len(modelo_cols) == 15
        assert await _colunas(db, r) == modelo_cols
        vazias = (
            await db.execute(
                text(
                    f'SELECT count(*) FROM "{r}".pricing_accounts WHERE '
                    + " OR ".join(f"{c} IS NOT NULL" for c in _NOVAS)
                )
            )
        ).scalar_one()
        assert vazias == 0
        assert await _contas() == contas_antes
        fks, ix = await _fks_e_indices(db, r)
        assert fks == sorted(
            (
                f"fk_pricing_accounts_slot{n}_segment",
                f"FOREIGN KEY (slot{n}_segment_id) REFERENCES segments(id) ON DELETE SET NULL",
            )
            for n in range(6, 11)
        )
        assert ix == sorted(f"ix_pricing_accounts_slot{n}_segment" for n in range(6, 11))
        # O frete projetado de hoje não muda (slot6..10 vazios)...
        assert await _fretes() == fretes_antes
        # ...e a categoria da posição 7 passa a casar pelo slot7/shipping7.
        await db.execute(
            text(
                f'UPDATE "{r}".pricing_accounts SET slot7_segment_id = :f7, shipping7 = 44, '
                "margin7 = 0.4"
            ),
            {"f7": f7},
        )
        await db.commit()
        esperado = [
            ("ue001", "atlas", D("30.00")),
            ("ue006", None, None),
            ("ue007", "atlas", D("44.00")),
        ]
        assert all(v == esperado for v in (await _fretes()).values())
        # Rodar o trecho das views de novo não estende duas vezes.
        conn = await db.connection()
        await conn.run_sync(lambda c: _rodar_trecho(c, mod))
        await db.commit()

        await _passo("downgrade")
        assert await _colunas(db, r) == []
        assert await _fks_e_indices(db, r) == ([], [])
        assert await _contas() == contas_antes
        assert await _fretes() == fretes_antes
        for v in mod.VIEWS:
            definicao = (
                await db.execute(
                    text("SELECT pg_get_viewdef(to_regclass(:q))"), {"q": f'"{r}".{v}'}
                )
            ).scalar_one()
            assert "slot5_segment_id" in definicao and "slot6" not in definicao

        await _passo("upgrade")
        assert await _colunas(db, r) == modelo_cols
        assert await _fretes() == fretes_antes
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{r}" CASCADE'))
        await db.commit()


def _rodar_trecho(conn, mod) -> None:
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        mod._views_e_funcoes(subir=True)
        for v in mod.VIEWS:
            definicao = conn.execute(
                text("SELECT pg_get_viewdef(to_regclass(:q))"), {"q": f'"{mod.SCHEMA}".{v}'}
            ).scalar_one()
            assert definicao.count("slot10_segment_id") == 2, definicao


def test_migration_0520_texto_com_e_sem_parenteses():
    """O trecho do frete projetado sai igual nas duas formas e volta ao de antes no downgrade."""
    mod = _carregar()
    sem = f"CASE\n{_case(5)}\n ELSE NULL END ... ON x AND ({_ou(5)}) AND y"
    com = (
        "CASE\n"
        + "\n".join(
            f"  WHEN (pa.slot{i}_segment_id = leaf.id) THEN pa.shipping{i}" for i in range(1, 6)
        )
        + "\n ELSE NULL END ... ON ((x) AND ("
        + " OR ".join(f"(leaf.id = pa.slot{i}_segment_id)" for i in range(1, 6))
        + ") AND (y))"
    )
    for texto in (sem, com):
        novo = mod._subir_texto("t", texto)
        assert novo.count("slot10_segment_id") == 2 and novo.count("shipping10") == 1
        # Nada fora dos dois trechos muda; o downgrade volta exatamente ao texto de antes.
        assert mod._descer_texto("t", novo) == texto
        # Já estendido: não estende de novo.
        assert mod._CASE_5.search(novo) is None and mod._OU_5.search(novo) is None
    assert "(leaf.id = pa.slot10_segment_id))" in mod._subir_texto("t", com)
    assert "leaf.id = pa.slot10_segment_id) AND y" in mod._subir_texto("t", sem)
    with pytest.raises(RuntimeError, match="trecho esperado"):
        mod._subir_texto("t", "SELECT 1")


def test_migration_0520_join_aninhado_como_na_producao():
    """26/11: o pg_get_viewdef da PRODUÇÃO devolve o JOIN aninhado "((((a OR b) OR c) OR d) OR e)".
    A 0520 tem de estender esse formato também (antes parava com "não tem o trecho esperado")."""
    mod = _carregar()
    case = "\n".join(
        f"  WHEN (pa.slot{i}_segment_id = leaf.id) THEN pa.shipping{i}" for i in range(1, 6)
    )
    join = "(leaf.id = pa.slot1_segment_id)"
    for i in range(2, 6):
        join = f"({join} OR (leaf.id = pa.slot{i}_segment_id))"
    texto = f"CASE\n{case}\n ELSE NULL END ... ON ((x) AND {join} AND (y))"
    novo = mod._subir_texto("t", texto)
    assert novo.count("slot10_segment_id") == 2 and novo.count("shipping10") == 1
    assert "((leaf.id = pa.slot5_segment_id) OR (leaf.id = pa.slot6_segment_id)" in novo
    # Já estendido: não estende de novo; o downgrade volta exatamente ao texto de antes.
    assert mod._CMP_5_ANINHADA.search(novo) is None
    assert mod._descer_texto("t", novo) == texto
    # Se o banco reescrever o texto (outro aninhamento), o downgrade troca 6..10 por NULL.
    reescrito = novo.replace(
        "((leaf.id = pa.slot5_segment_id) OR (leaf.id = pa.slot6_segment_id)",
        "(((leaf.id = pa.slot5_segment_id) OR (leaf.id = pa.slot6_segment_id))",
    ).replace("(leaf.id = pa.slot10_segment_id))", "(leaf.id = pa.slot10_segment_id)))")
    descido = mod._descer_texto("t", reescrito)
    assert "slot6_segment_id" not in descido and "shipping6" not in descido
    assert "NULL::uuid" in descido


def _aninhar(texto: str) -> str:
    """O JOIN corrido "(a OR b OR c OR d OR e)" do pg_get_viewdef local na forma da produção,
    "((((a OR b) OR c) OR d) OR e)". Só no texto de antes da 0520 (sem a 6ª)."""
    if "slot6_segment_id" in texto:
        return texto
    cmp = [f"(leaf.id = pa.slot{i}_segment_id)" for i in range(1, 6)]
    corrido = " OR ".join(cmp)
    assert texto.count(corrido) == 1, texto
    join = cmp[0]
    for c in cmp[1:4]:
        join = f"({join} OR {c})"
    return texto.replace(corrido, f"{join} OR {cmp[4]}")


async def test_migration_0520_join_aninhado_no_banco(db: AsyncSession, monkeypatch):
    """O caminho do JOIN aninhado da produção de ponta a ponta no Postgres. O Postgres local
    achata "((a OR b) OR c)" ao gravar a view, então o pg_get_viewdef que a 0520 lê é trocado
    pelo texto aninhado; o CREATE OR REPLACE que ela manda é o de verdade. Sobe, desce (volta ao
    texto exato de antes) e sobe de novo, com o frete projetado igual e a posição 7 casando."""
    import alembic.op as op_real

    mod = _carregar()
    modelo = Base.metadata.schema
    r = f"{modelo}_mig0520an"
    await db.execute(text(f'DROP SCHEMA IF EXISTS "{r}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{r}"'))
    for t in ("segments", "pricing_products", "pricing_accounts"):
        await db.execute(text(f'CREATE TABLE "{r}".{t} (LIKE "{modelo}".{t} INCLUDING ALL)'))
    for c in _NOVAS:
        await db.execute(text(f'ALTER TABLE "{r}".pricing_accounts DROP COLUMN {c}'))
    raiz_id, f1, f7 = uuid4(), uuid4(), uuid4()
    await db.execute(
        text(
            f'INSERT INTO "{r}".segments (id, name, slug, sort_order, parent_id) VALUES '
            "(:raiz, 'Eletro', 'eletro', 0, NULL), (:f1, 'Acessórios', 'e1', 0, :raiz), "
            "(:f7, 'cooktop', 'e7', 6, :raiz)"
        ),
        {"raiz": raiz_id, "f1": f1, "f7": f7},
    )
    dono = uuid4()
    await db.execute(
        text(
            f'INSERT INTO "{r}".pricing_products (id, user_id, sku, name, segment_id) VALUES '
            "(gen_random_uuid(), :u, 'ue001', 'a', :f1), (gen_random_uuid(), :u, 'ue007', 'b', :f7)"
        ),
        {"u": dono, "f1": f1, "f7": f7},
    )
    await db.execute(
        text(
            f'INSERT INTO "{r}".pricing_accounts (id, user_id, name, platform, segment_id, '
            "margin1, shipping1, slot1_segment_id, commission) VALUES "
            "(gen_random_uuid(), :u, 'atlas', 'amazon', :raiz, 0.53, 30, :f1, 0.14)"
        ),
        {"u": dono, "raiz": raiz_id, "f1": f1},
    )
    for v in mod.VIEWS:
        await db.execute(text(f'CREATE VIEW "{r}".{v} AS {_sql_frete(r)}'))
    await db.commit()

    class _Bind:
        """A conexão de verdade; só o pg_get_viewdef volta na forma aninhada."""

        def __init__(self, conn):
            self._conn = conn

        def execute(self, stmt, params=None):
            res = self._conn.execute(stmt, params)
            if "pg_get_viewdef" in str(stmt):
                definicao = _aninhar(res.scalar_one())
                return SimpleNamespace(scalar_one=lambda: definicao)
            return res

        def exec_driver_sql(self, sql):
            return self._conn.exec_driver_sql(sql)

    class _Op:
        def __getattr__(self, nome):
            return getattr(op_real, nome)

        def get_bind(self):
            return _Bind(op_real.get_bind())

    async def _definicoes() -> dict[str, str]:
        return {
            v: (
                await db.execute(
                    text("SELECT pg_get_viewdef(to_regclass(:q), false)"), {"q": f'"{r}".{v}'}
                )
            ).scalar_one()
            for v in mod.VIEWS
        }

    async def _fretes() -> list[list[tuple]]:
        return [
            [
                tuple(x)
                for x in (
                    await db.execute(
                        text(f'SELECT sku, conta, frete_projetado_unit FROM "{r}".{v} ORDER BY sku')
                    )
                ).all()
            ]
            for v in mod.VIEWS
        ]

    def _rodar(conn, passo: str) -> None:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(mod, passo)()

    async def _passo(passo: str) -> None:
        conn = await db.connection()
        await conn.run_sync(_rodar, passo)
        await db.commit()

    defs_antes, fretes_antes = await _definicoes(), await _fretes()
    assert all(_aninhar(d) != d for d in defs_antes.values())  # o texto lido é mesmo o aninhado
    assert fretes_antes == [[("ue001", "atlas", D("30.00")), ("ue007", None, None)]] * 2
    mod.SCHEMA = r
    monkeypatch.setattr(mod, "op", _Op())
    try:
        await _passo("upgrade")
        for d in (await _definicoes()).values():
            assert d.count("slot10_segment_id") == 2 and d.count("shipping10") == 1, d
        assert await _fretes() == fretes_antes
        await db.execute(
            text(f'UPDATE "{r}".pricing_accounts SET slot7_segment_id = :f7, shipping7 = 44'),
            {"f7": f7},
        )
        await db.commit()
        assert (
            await _fretes()
            == [[("ue001", "atlas", D("30.00")), ("ue007", "atlas", D("44.00"))]] * 2
        )

        await _passo("downgrade")
        assert await _definicoes() == defs_antes
        assert await _fretes() == fretes_antes
        assert await _colunas(db, r) == []

        await _passo("upgrade")
        for d in (await _definicoes()).values():
            assert d.count("slot10_segment_id") == 2, d
        assert await _fretes() == fretes_antes
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{r}" CASCADE'))
        await db.commit()
