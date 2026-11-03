"""Tabela de Preços — colunas Estoque, 7d/30d e os prazos da Reposição
(02/11/2026).

  * /stock-map: no celular a linha soma os lotes (ci, pi, ra, sa, sp, cd) de
    cada peça; fica de fora .us, .kitN, kit '+', composição (formato E) e
    produto inativo; sem lote, o SKU exato. Mala/eletro: SKU exato.
    `?detalhe=1` traz as parcelas, cada uma com a peça da linha ("peca"), e
    `conjunto` (todas as parcelas são composição: Reposição "—").
  * /sales-map: vendas de services/vendas_periodo casadas pela regra de cada
    departamento; `?detalhe=1` traz as vendas de cada peça.
  * /reposicao-config: os prazos da Importação, lidos sem criar a linha.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingKitComponent,
    BlingOrder,
    ImportConfig,
    PricingProduct,
    Product,
    Segment,
    StoreInfo,
    User,
    UserRole,
    UserStatus,
)
from app.services.pricing.estoque_linha import (
    montar_indice,
    partes_da_linha,
    peca_do_lote,
)

VER_PRECOS = {"tabela_precos": {"view": True, "edit": False, "delete": False}}
LOJA_CD = "205632678"


async def _usuario(db: AsyncSession, *, role=UserRole.USER, permissions=None) -> User:
    u = User(
        open_id=f"email:pe-{uuid.uuid4().hex[:8]}@davinci-test.com",
        email=f"pe-{uuid.uuid4().hex[:8]}@davinci-test.com",
        role=role,
        status=UserStatus.ACTIVE,
        permissions=permissions if permissions is not None else VER_PRECOS,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def vendedor(db: AsyncSession) -> User:
    return await _usuario(db)


@pytest_asyncio.fixture
async def folhas(db: AsyncSession) -> dict[str, uuid.UUID]:
    """Segmento folha (tipo 1) de cada departamento."""
    out: dict[str, uuid.UUID] = {}
    for ordem, slug in enumerate(("celular", "mala", "eletro")):
        raiz = Segment(name=slug.title(), slug=slug, sort_order=ordem)
        db.add(raiz)
        await db.flush()
        folha = Segment(name=f"{slug} 1", slug=f"{slug}-1", parent_id=raiz.id, sort_order=0)
        db.add(folha)
        await db.flush()
        out[slug] = folha.id
    await db.commit()
    return out


def _linha(dono: User, folha: uuid.UUID, sku: str) -> PricingProduct:
    return PricingProduct(user_id=dono.id, sku=sku, name=sku, segment_id=folha)


def _produto(
    dono: User, sku: str, estoque: int, *, situacao: str | None = "A", formato: str | None = "S"
) -> Product:
    return Product(
        user_id=dono.id, sku=sku, name=sku, stock=estoque, situacao=situacao, formato=formato
    )


def _pedido(codigo: str, quantidade: int, *, dias_atras: float = 1, **extra) -> BlingOrder:
    return BlingOrder(
        numero=uuid.uuid4().hex[:8],
        data=datetime.now(UTC) - timedelta(days=dias_atras),
        situacao=extra.pop("situacao", "6"),
        item_codigo=codigo,
        item_quantidade=quantidade,
        **extra,
    )


# ── Regras puras do estoque da linha ─────────────────────────────────


def test_peca_do_lote():
    assert peca_do_lote("a019.cd") == "a019"
    assert peca_do_lote("A019.SP") == "a019"
    assert peca_do_lote("dg001.us") == "dg001"  # override: dg001.us é 'sa'
    assert peca_do_lote("a019.us") is None  # usado
    assert peca_do_lote("a019.kit2") is None
    assert peca_do_lote("a019.pp") is None
    assert peca_do_lote("a019.sp+a020.sp") is None
    assert peca_do_lote("z0207.mala.us") is None  # dois pontos
    assert peca_do_lote("b005.20") is None  # tamanho de mala não é lote
    assert peca_do_lote("a019") is None


def test_indice_maior_saldo_e_pecas_repetidas_contam_uma_vez():
    indice = montar_indice([
        ("a019.ci", 100, "A", "S"),
        ("A019.CI", 80, "A", "S"),  # outro dono: fica o maior
        ("a019.sp", -3, None, None),  # NULL = ativo; negativo entra como está
        ("a019.ra", 60, "I", "S"),  # inativo
        ("a019.pi", 40, "E", "S"),  # excluído
    ])
    partes = partes_da_linha(indice, "a019, A019 ,a019", "celular")
    assert [(p.sku, p.estoque, p.peca) for p in partes] == [
        ("a019.ci", 100, "a019"),
        ("a019.sp", -3, "a019"),
    ]


# ── /stock-map ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stock_map_celular_soma_os_lotes(
    db: AsyncSession, client: AsyncClient, auth_as: Callable[[User | None], None],
    vendedor: User, folhas: dict[str, uuid.UUID],
):
    outro_dono = await _usuario(db)
    airtag = _linha(vendedor, folhas["celular"], "a019")
    fone = _linha(vendedor, folhas["celular"], "dg001")
    db.add_all([
        airtag,
        fone,
        _produto(vendedor, "a019.cd", 2598),
        _produto(vendedor, "a019.ci", 100),
        _produto(outro_dono, "A019.CI", 80),  # SKU repetido: o maior saldo
        _produto(vendedor, "a019.sp", 50),
        _produto(vendedor, "a019.sa", -3),  # negativo soma como está
        _produto(vendedor, "a019.us", 7),  # usado: fora
        _produto(vendedor, "a019.kit2", 9),  # não é lote: fora
        _produto(vendedor, "a019.lj", 11),
        _produto(vendedor, "a019.sp+a020.sp", 30, formato="E"),  # kit: fora
        _produto(vendedor, "a019.pi", 40, formato="E"),  # composição no Bling: fora
        _produto(vendedor, "a019.ra", 60, situacao="I"),  # inativo: fora
        _produto(vendedor, "a019.sp.x", 5),  # dois pontos: fora
        _produto(vendedor, "a019", 1000),  # tem lote: o SKU exato não entra
        _produto(vendedor, "dg001.us", 5),  # override → 'sa': entra
        _produto(vendedor, "dg001.sp", 2),
    ])
    await db.commit()
    auth_as(vendedor)

    r = await client.get("/api/pricing/stock-map?department=celular")
    assert r.status_code == 200, r.text
    assert r.json() == {str(airtag.id): 2598 + 100 + 50 - 3, str(fone.id): 7}

    r = await client.get("/api/pricing/stock-map?department=celular&detalhe=1")
    assert r.status_code == 200, r.text
    assert r.json()[str(airtag.id)] == {
        "total": 2745,
        "partes": [
            {"sku": "a019.cd", "estoque": 2598, "peca": "a019"},
            {"sku": "a019.ci", "estoque": 100, "peca": "a019"},
            {"sku": "a019.sa", "estoque": -3, "peca": "a019"},
            {"sku": "a019.sp", "estoque": 50, "peca": "a019"},
        ],
        "conjunto": False,
    }
    assert r.json()[str(fone.id)]["partes"] == [
        {"sku": "dg001.sp", "estoque": 2, "peca": "dg001"},
        {"sku": "dg001.us", "estoque": 5, "peca": "dg001"},
    ]


@pytest.mark.asyncio
async def test_stock_map_celular_sem_lote_cai_no_sku_exato(
    db: AsyncSession, client: AsyncClient, auth_as: Callable[[User | None], None],
    vendedor: User, folhas: dict[str, uuid.UUID],
):
    relogios = _linha(vendedor, folhas["celular"], "i232,i233, I232")
    sem_nada = _linha(vendedor, folhas["celular"], "zz999")
    db.add_all([
        relogios,
        sem_nada,
        _produto(vendedor, "i232", 4),
        _produto(vendedor, "i233", 9, situacao="I"),  # inativo: não entra nem exato
    ])
    await db.commit()
    auth_as(vendedor)

    r = await client.get("/api/pricing/stock-map?detalhe=1")
    assert r.status_code == 200, r.text
    corpo = r.json()
    # Peça repetida conta uma vez.
    assert corpo[str(relogios.id)] == {
        "total": 4,
        "partes": [{"sku": "i232", "estoque": 4, "peca": "i232"}],
        "conjunto": False,
    }
    assert corpo[str(sem_nada.id)] == {"total": 0, "partes": [], "conjunto": False}


@pytest.mark.asyncio
async def test_stock_map_mala_e_eletro_sku_exato(
    db: AsyncSession, client: AsyncClient, auth_as: Callable[[User | None], None],
    vendedor: User, folhas: dict[str, uuid.UUID],
):
    malas = _linha(vendedor, folhas["mala"], "b005.20,b006.20")
    conjunto = _linha(vendedor, folhas["mala"], "b005.12.20")
    airfryer = _linha(vendedor, folhas["eletro"], "uaf001m1.110, uaf001m1.220")
    db.add_all([
        malas,
        conjunto,
        airfryer,
        _produto(vendedor, "b005.20", 10),
        _produto(vendedor, "b005.20.ci", 99),  # mala não soma "lote"
        _produto(vendedor, "b006.20", 3, situacao="E"),  # excluído: fora
        # Conjunto de mala é composição no Bling (formato E): o saldo é o que
        # o Bling calcula pelas peças e continua aparecendo, como sempre foi.
        _produto(vendedor, "b005.12.20", 7, formato="E"),
        _produto(vendedor, "uaf001m1.110", 2),
        _produto(vendedor, "uaf001m1.220", 1),
    ])
    await db.commit()
    auth_as(vendedor)

    r = await client.get("/api/pricing/stock-map")
    assert r.status_code == 200, r.text
    assert r.json() == {str(malas.id): 10, str(conjunto.id): 7, str(airfryer.id): 3}

    r = await client.get("/api/pricing/stock-map?department=mala&detalhe=1")
    assert r.json() == {
        str(malas.id): {
            "total": 10,
            "partes": [{"sku": "b005.20", "estoque": 10, "peca": "b005.20"}],
            "conjunto": False,
        },
        str(conjunto.id): {
            "total": 7,
            "partes": [{"sku": "b005.12.20", "estoque": 7, "peca": "b005.12.20"}],
            "conjunto": True,
        },
    }


@pytest.mark.asyncio
async def test_stock_map_detalhe_peca_da_linha(
    db: AsyncSession, client: AsyncClient, auth_as: Callable[[User | None], None],
    vendedor: User, folhas: dict[str, uuid.UUID],
):
    """Cada parte leva a peça da linha a que pertence (a string da linha, em
    minúsculas): no celular a base, na mala o SKU — Reposição cor a cor."""
    uranyx = _linha(vendedor, folhas["celular"], "dg002,DG003,dg004")
    malas = _linha(vendedor, folhas["mala"], "b005.20, B006.20")
    db.add_all([
        uranyx,
        malas,
        _produto(vendedor, "dg002.ci", 10),
        _produto(vendedor, "dg002.sp", 5),
        _produto(vendedor, "dg003.pi", 20),
        _produto(vendedor, "b005.20", 7),
        _produto(vendedor, "B006.20", 3),  # o SKU como no Bling; a peça, como na linha
    ])
    await db.commit()
    auth_as(vendedor)

    r = await client.get("/api/pricing/stock-map?detalhe=1")
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo[str(uranyx.id)]["partes"] == [
        {"sku": "dg002.ci", "estoque": 10, "peca": "dg002"},
        {"sku": "dg002.sp", "estoque": 5, "peca": "dg002"},
        {"sku": "dg003.pi", "estoque": 20, "peca": "dg003"},
    ]
    assert corpo[str(malas.id)]["partes"] == [
        {"sku": "b005.20", "estoque": 7, "peca": "b005.20"},
        {"sku": "B006.20", "estoque": 3, "peca": "b006.20"},
    ]


@pytest.mark.asyncio
async def test_stock_map_conjunto_so_quando_todas_as_partes_sao_composicao(
    db: AsyncSession, client: AsyncClient, auth_as: Callable[[User | None], None],
    vendedor: User, folhas: dict[str, uuid.UUID],
):
    """`conjunto`: a linha tem parte e TODAS são composição — formato 'E' ou
    kit com '+'. A compra é pelos tamanhos avulsos; a Tabela mostra "—" na
    Reposição dessas linhas."""
    linhas = {
        # nome: (linha, conjunto esperado)
        "conjuntos": (_linha(vendedor, folhas["mala"], "b005.8.12.20.24,b006.8.12.20.24"), True),
        "acessorios": (
            _linha(vendedor, folhas["mala"], "b005.20+a075+bp003+a076,b006.20+a075+bp003+a076"),
            True,
        ),
        "kit_completo": (_linha(vendedor, folhas["mala"], "b005,b006"), True),
        "avulsa": (_linha(vendedor, folhas["mala"], "b005.20"), False),
        "misturada": (_linha(vendedor, folhas["mala"], "b005.20,b005.12.20"), False),
        "rodinhas": (_linha(vendedor, folhas["mala"], "a074"), False),
        "sem_sku": (_linha(vendedor, folhas["mala"], "b999.12.20"), False),
        "eletro": (_linha(vendedor, folhas["eletro"], "uaf001m1.110"), False),
        "celular": (_linha(vendedor, folhas["celular"], "i300"), False),
    }
    db.add_all([linha for linha, _ in linhas.values()])
    db.add_all([
        _produto(vendedor, "b005.8.12.20.24", 3, formato="E"),
        _produto(vendedor, "b006.8.12.20.24", 0, formato="E"),
        _produto(vendedor, "b005.20+a075+bp003+a076", 4, formato="E"),
        # Kit com '+' é kit mesmo quando o Bling o cadastrou como simples.
        _produto(vendedor, "b006.20+a075+bp003+a076", 2),
        # Base pura de kit completo (todos os tamanhos).
        _produto(vendedor, "b005", 1, formato="E"),
        _produto(vendedor, "b006", 1, formato="E"),
        _produto(vendedor, "b005.20", 10),
        _produto(vendedor, "b005.12.20", 7, formato="E"),
        # Kit de rodinhas vendido como peça única: simples no Bling.
        _produto(vendedor, "a074", 30),
        _produto(vendedor, "uaf001m1.110", 2),
        # Celular só soma SKU simples: a composição não vira parte.
        _produto(vendedor, "i300", 5, formato="E"),
    ])
    await db.commit()
    auth_as(vendedor)

    r = await client.get("/api/pricing/stock-map?detalhe=1")
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert {nome: corpo[str(linha.id)]["conjunto"] for nome, (linha, _) in linhas.items()} == {
        nome: esperado for nome, (_, esperado) in linhas.items()
    }
    assert corpo[str(linhas["acessorios"][0].id)] == {
        "total": 6,
        "partes": [
            {"sku": "b005.20+a075+bp003+a076", "estoque": 4, "peca": "b005.20+a075+bp003+a076"},
            {"sku": "b006.20+a075+bp003+a076", "estoque": 2, "peca": "b006.20+a075+bp003+a076"},
        ],
        "conjunto": True,
    }
    assert corpo[str(linhas["celular"][0].id)] == {"total": 0, "partes": [], "conjunto": False}

    # Sem ?detalhe o formato continua {id: total}.
    r = await client.get("/api/pricing/stock-map")
    assert r.json() == {pid: linha["total"] for pid, linha in corpo.items()}


# ── /sales-map ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sales_map_celular_conta_a_base_e_as_pecas_dos_kits(
    db: AsyncSession, client: AsyncClient, auth_as: Callable[[User | None], None],
    vendedor: User, folhas: dict[str, uuid.UUID],
):
    fone = _linha(vendedor, folhas["celular"], "a001")
    celular = _linha(vendedor, folhas["celular"], "dg052,dg053")
    db.add_all([
        fone,
        celular,
        StoreInfo(user_id=vendedor.id, platform="interno", bling_store_id=LOJA_CD),
        _pedido("dg053.sp+a001.sp", 10),
        _pedido("a001.ci", 2),
        _pedido("dg052.ci", 1, situacao=None),
        _pedido("dg053.ci", 5, situacao="12"),  # cancelado
        _pedido("a001.cd", 50, loja=LOJA_CD),  # transferência entre lotes
        _pedido("a001.cd", -50, loja=LOJA_CD),
        _pedido("a001.sp", 4, dias_atras=10),  # fora dos 7 dias
    ])
    await db.commit()
    auth_as(vendedor)

    r = await client.get("/api/pricing/sales-map?days=30&department=celular")
    assert r.status_code == 200, r.text
    assert r.json() == {str(fone.id): 16, str(celular.id): 11}

    r = await client.get("/api/pricing/sales-map?days=7")
    assert r.json() == {str(fone.id): 12, str(celular.id): 11}


@pytest.mark.asyncio
async def test_sales_map_mala_regra_dos_tamanhos(
    db: AsyncSession, client: AsyncClient, auth_as: Callable[[User | None], None],
    vendedor: User, folhas: dict[str, uuid.UUID],
):
    mala_18 = _linha(vendedor, folhas["mala"], "b045.18")
    kit_acessorio = _linha(vendedor, folhas["mala"], "b045.18+a075")
    chaveiro = _linha(vendedor, folhas["mala"], "a075")
    db.add_all([
        mala_18,
        kit_acessorio,
        chaveiro,
        _pedido("b045.18", 1),
        _pedido("b045.12.18", 2),  # conjunto com o tamanho 18
        _pedido("b045.20", 4),  # outro tamanho
        _pedido("b045.18+a075", 5),  # kit: a mala e o chaveiro saíram
    ])
    await db.commit()
    auth_as(vendedor)

    r = await client.get("/api/pricing/sales-map?days=30&department=mala")
    assert r.status_code == 200, r.text
    # Linha com '+' na mala continua zerada (decisão do operador, audit.py).
    assert r.json() == {str(mala_18.id): 8, str(kit_acessorio.id): 0, str(chaveiro.id): 5}


@pytest.mark.asyncio
async def test_sales_map_detalhe_vendas_de_cada_peca(
    db: AsyncSession, client: AsyncClient, auth_as: Callable[[User | None], None],
    vendedor: User, folhas: dict[str, uuid.UUID],
):
    """`?detalhe=1`: as vendas de cada peça da linha, com as strings de "peca"
    do /stock-map — celular pela base, mala pelo SKU (regra dos tamanhos). O
    total é o do formato simples, a união das chaves: a venda que casa com
    duas peças conta uma vez na linha."""
    uranyx = _linha(vendedor, folhas["celular"], "dg002,DG003,dg004")
    malas = _linha(vendedor, folhas["mala"], "b045.18, b045.20,b045.24")
    kit_acessorio = _linha(vendedor, folhas["mala"], "b045.18+a075")
    conjunto = _linha(vendedor, folhas["mala"], "b005.8.12.20.24")
    db.add_all([
        uranyx,
        malas,
        kit_acessorio,
        conjunto,
        _pedido("dg002.ci", 3),
        _pedido("dg002.sp+a001.sp", 2),  # o kit credita a base dg002
        _pedido("dg003.pi", 4),
        _pedido("dg003.pi", 7, dias_atras=40),  # fora dos 30 dias
        _pedido("b045.18", 1),
        _pedido("b045.12.18", 2),  # conjunto com o 18
        _pedido("b045.18.20", 3),  # conjunto com o 18 e o 20: duas peças, uma venda
        _pedido("b045.20", 4),
        _pedido("b045.18+a075", 5),  # kit: a mala 18 saiu
        # Conjunto sem venda própria: as avulsas da cor não entram (sem o
        # fallback do af9465c desde 02/11/2026); a Tabela mostra "—" na
        # Reposição dessa linha (conjunto no /stock-map).
        _pedido("b005.8", 6),
        _pedido("b005.12", 2),
    ])
    await db.commit()
    auth_as(vendedor)

    r = await client.get("/api/pricing/sales-map?days=30&detalhe=1")
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo == {
        str(uranyx.id): {"total": 9, "pecas": {"dg002": 5, "dg003": 4, "dg004": 0}},
        # 18: 1 + 5 do kit + 2 + 3; 20: 4 + 3; a linha conta o b045.18.20 uma vez.
        str(malas.id): {"total": 15, "pecas": {"b045.18": 11, "b045.20": 7, "b045.24": 0}},
        str(kit_acessorio.id): {"total": 0, "pecas": {"b045.18+a075": 0}},
        str(conjunto.id): {"total": 0, "pecas": {"b005.8.12.20.24": 0}},
    }

    # Sem ?detalhe o formato continua {id: total}, com os mesmos números.
    r = await client.get("/api/pricing/sales-map?days=30")
    assert r.json() == {pid: linha["total"] for pid, linha in corpo.items()}


@pytest_asyncio.fixture
async def sem_composicao(db: AsyncSession):
    """bling_kit_components não está na limpeza do conftest: começa e termina vazia."""
    await db.execute(text("DELETE FROM bling_kit_components"))
    await db.commit()
    yield
    await db.execute(text("DELETE FROM bling_kit_components"))
    await db.commit()


@pytest.mark.asyncio
async def test_sales_map_kit_6_malas_conta_em_cada_tamanho(
    db: AsyncSession, client: AsyncClient, auth_as: Callable[[User | None], None],
    vendedor: User, folhas: dict[str, uuid.UUID], sem_composicao: None,
):
    """O "Kit 6 Malas" (SKU só a base, b047) entra nas malas avulsas pela
    composição do Bling — o 12 em dobro, como o Bling baixa; a linha do kit
    mostra as vendas dele; o conjunto que não vendeu fica com 0, sem as
    avulsas da cor."""
    kit = _produto(vendedor, "b047", 0, formato="E")
    kit.bling_product_id = 9000
    db.add(kit)
    for i, (sku, no_kit) in enumerate(
        {"b047.8": 1, "b047.12": 2, "b047.18": 1, "b047.20": 1, "b047.24": 1}.items(), start=1
    ):
        peca = _produto(vendedor, sku, 0)
        peca.bling_product_id = 9000 + i
        db.add(peca)
        db.add(BlingKitComponent(
            kit_bling_product_id=9000, component_bling_product_id=9000 + i, quantidade=no_kit,
        ))
    mala_12 = _linha(vendedor, folhas["mala"], "b047.12")
    mala_8 = _linha(vendedor, folhas["mala"], "b047.8")
    kit_6 = _linha(vendedor, folhas["mala"], "b047")
    conjunto = _linha(vendedor, folhas["mala"], "b047.12.24")
    db.add_all([
        mala_12,
        mala_8,
        kit_6,
        conjunto,
        _pedido("b047", 3),
        _pedido("b047.12", 1),
        _pedido("b047.24", 5),  # avulsa: não entra no conjunto 12+24
        _pedido("b047.18.24", 2),  # conjunto sem o 12
    ])
    await db.commit()
    auth_as(vendedor)

    r = await client.get("/api/pricing/sales-map?days=30&department=mala")
    assert r.status_code == 200, r.text
    assert r.json() == {
        str(mala_12.id): 1 + 3 * 2,
        str(mala_8.id): 3,
        str(kit_6.id): 3,
        str(conjunto.id): 0,
    }


# ── /reposicao-config ────────────────────────────────────────────────


@pytest_asyncio.fixture
async def sem_config(db: AsyncSession):
    """import_config não está na limpeza do conftest: começa e termina vazia."""
    await db.execute(text("DELETE FROM import_config"))
    await db.commit()
    yield
    await db.execute(text("DELETE FROM import_config"))
    await db.commit()


async def _linhas_config(db: AsyncSession) -> int:
    return (await db.execute(select(func.count()).select_from(ImportConfig))).scalar_one()


@pytest.mark.asyncio
async def test_reposicao_config_vendedor_le_padrao_sem_criar_linha(
    db: AsyncSession, client: AsyncClient, auth_as: Callable[[User | None], None],
    vendedor: User, sem_config: None,
):
    auth_as(vendedor)
    r = await client.get("/api/pricing/reposicao-config?department=celular")
    assert r.status_code == 200, r.text
    assert r.json() == {
        "department": "celular",
        "tempo_reposicao": 150,
        "tempo_estoque": 60,
        "pode_editar": False,
    }
    assert await _linhas_config(db) == 0


@pytest.mark.asyncio
async def test_reposicao_config_le_o_gravado_e_quem_edita_a_importacao(
    db: AsyncSession, client: AsyncClient, auth_as: Callable[[User | None], None],
    sem_config: None,
):
    db.add(ImportConfig(categoria="mala", tempo_reposicao=20, tempo_estoque=40))
    await db.commit()
    comprador = await _usuario(db, permissions={
        **VER_PRECOS, "importacao": {"view": True, "edit": True, "delete": False},
    })
    so_ve_importacao = await _usuario(db, permissions={
        **VER_PRECOS, "importacao": {"view": True, "edit": False},
    })
    admin = await _usuario(db, role=UserRole.ADMIN, permissions={})

    esperado = {"department": "mala", "tempo_reposicao": 20, "tempo_estoque": 40}
    for quem, pode in ((comprador, True), (so_ve_importacao, False), (admin, True)):
        auth_as(quem)
        r = await client.get("/api/pricing/reposicao-config?department=Mala")
        assert r.status_code == 200, r.text
        assert r.json() == {**esperado, "pode_editar": pode}
    assert await _linhas_config(db) == 1


@pytest.mark.asyncio
async def test_reposicao_config_departamento_sem_importacao(
    db: AsyncSession, client: AsyncClient, auth_as: Callable[[User | None], None],
    sem_config: None,
):
    auth_as(await _usuario(db, role=UserRole.ADMIN, permissions={}))
    for consulta, departamento in (("?department=catalogo", "catalogo"), ("", "")):
        r = await client.get(f"/api/pricing/reposicao-config{consulta}")
        assert r.status_code == 200, r.text
        assert r.json() == {
            "department": departamento,
            "tempo_reposicao": None,
            "tempo_estoque": None,
            "pode_editar": False,
        }
    assert await _linhas_config(db) == 0


@pytest.mark.asyncio
async def test_reposicao_config_exige_ver_a_tabela(
    db: AsyncSession, client: AsyncClient, auth_as: Callable[[User | None], None],
):
    auth_as(await _usuario(db, permissions={"importacao": {"view": True, "edit": True}}))
    r = await client.get("/api/pricing/reposicao-config?department=celular")
    assert r.status_code == 403
