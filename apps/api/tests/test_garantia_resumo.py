"""Resumo do Painel de Garantia (28/10/2026): a garantia POR PRODUTO — uma
linha por produto da Tabela de Preços (o modelo, com as cores juntas), as
vendas com garantia e os PEDIDOS de garantia (a porcentagem da tela é pediram ÷
vendidas), e o total por departamento (Celular, Eletro, Acessórios) pela
categoria do item no pedido do Bling — a mesma do Faturamento."""

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from app.models import (
    BlingOrder,
    Garantia,
    GarantiaAtendimento,
    Product,
    UserRole,
)
from app.models.pricing import PricingProduct
from app.models.product import ProductCategory
from app.models.segment import Segment
from app.services import garantia as svc
from app.services import garantia_resumo as resumo

pytestmark = pytest.mark.asyncio

HOJE = date(2026, 10, 20)
_N = iter(range(100_000_000, 999_999_999, 7_919))


def _cpf() -> str:
    """Um CPF válido novo a cada chamada (dígitos verificadores calculados)."""
    base = [int(x) for x in str(next(_N))]
    for peso in (10, 11):
        resto = sum(d * (peso - i) for i, d in enumerate(base)) * 10 % 11
        base.append(0 if resto == 10 else resto)
    return "".join(map(str, base))


@pytest.fixture(autouse=True)
def _hoje(monkeypatch):
    monkeypatch.setattr(svc, "hoje", lambda: HOJE)


def test_departamento_pela_categoria_e_pela_familia():
    assert resumo.departamento_da_categoria("Celular Kit") == "celular"
    assert resumo.departamento_da_categoria("Celular Usado") == "celular"
    assert resumo.departamento_da_categoria("Eletro") == "eletro"
    assert resumo.departamento_da_categoria("Acessórios Celular") == "acessorios"
    assert resumo.departamento_da_categoria("Mala Kit") == "outros"
    assert resumo.departamento_da_categoria("  ") is None
    assert resumo.departamento_da_familia("dg101.pi") == "celular"
    assert resumo.departamento_da_familia("u015") == "eletro"
    assert resumo.departamento_da_familia("a001.sp") == "acessorios"
    assert resumo.departamento_da_familia("b021") == "outros"
    assert resumo.produto_base("dg053.sp+a001.sp") == "dg053"
    assert resumo.produto_base("dg053.ci") == "dg053"
    # Voltagem e outro produto não são lote: ficam separados.
    assert resumo.produto_base("uaf001m1.110") == "uaf001m1.110"
    assert resumo.produto_base("uaf001m1.220") == "uaf001m1.220"
    assert resumo.produto_base("uaf001m1.4l") == "uaf001m1.4l"
    assert (
        resumo.nome_do_produto("Hotwav A17 Pro Max - Laranja + Fone")
        == "Hotwav A17 Pro Max - Laranja"
    )
    assert (
        resumo.item_principal([{"sku": "b021", "uranyx": False}, {"sku": "dg053", "uranyx": True}])[
            "sku"
        ]
        == "dg053"
    )
    # O fone (acessório Uranyx) antes do celular no pedido: vale o celular.
    itens = [{"sku": "a003.ci", "uranyx": True}, {"sku": "dg089.ci+a001.ci", "uranyx": True}]
    assert resumo.item_principal(itens)["sku"] == "dg089.ci+a001.ci"
    assert resumo.item_principal([{"sku": "a003.ci", "uranyx": True}])["sku"] == "a003.ci"


async def _garantia(
    db, numero, sku, descricao, categoria=None, *, entrega=date(2026, 10, 5), nf=None, pedido=None
):
    db.add(
        BlingOrder(
            numero=numero,
            numeroloja=f"MKT{numero}",
            loja="L1",
            data=datetime(2026, 10, 1, tzinfo=UTC),
            total=1000,
            situacao="15",
            item_index=0,
            item_codigo=sku,
            item_descricao=descricao,
            item_quantidade=1,
            categoria_nome=categoria,
        )
    )
    g = Garantia(
        origem="robo",
        pedido_bling=numero,
        nf_numero=nf or ("9" + numero[-3:]),
        cliente_nome="Cliente",
        cpf=_cpf(),
        itens=[{"sku": sku, "descricao": descricao, "quantidade": 1, "uranyx": True}],
    )
    if entrega:
        g.data_inicio = entrega
        g.fim_hardware, g.fim_software = svc.calcular_prazos(entrega)
        g.entrega_origem = "shopee"
    if pedido:
        g.solicitada_em = datetime(2026, 10, 12, 15, tzinfo=UTC)
        g.solicitacao_origem = pedido
    db.add(g)
    await db.commit()
    return g


async def _tabela(db, dono, *linhas):
    """Linhas da Tabela de Preços: (skus separados por vírgula, nome)."""
    seg = Segment(name="Celular", slug=f"celular-{dono.id.hex[:6]}")
    db.add(seg)
    await db.flush()
    for skus, nome in linhas:
        db.add(PricingProduct(user_id=dono.id, sku=skus, name=nome, segment_id=seg.id))
    await db.commit()


async def test_resumo_por_produto(client, db, make_user, auth_as):
    dono = await make_user(role=UserRole.ADMIN)
    auth_as(dono)
    # Cadastro do produto sem categoria no pedido (dg101 — o T8 novo).
    db.add(ProductCategory(bling_category_id=8680318, name="Celular"))
    db.add(Product(user_id=dono.id, sku="dg101.pi", name="Uranyx T8", category="8680318"))
    await db.commit()
    # A Tabela de Preços: o A17 com as cores juntas; o kit não rouba o SKU puro.
    await _tabela(
        db,
        dono,
        ("dg052,dg053,dg054", "uranyx A17 pro max 12.128"),
        ("dg053+a003", "kit A17 + fone"),
        ("u015", "airfryer 4L"),
    )
    await _garantia(
        db,
        "300001",
        "dg053.sp+a001.sp",
        "A17 Pro Max - Laranja + Fone",
        "Celular Kit",
        pedido="mensagem",
    )
    await _garantia(db, "300002", "dg054.ci", "A17 Pro Max - Branco", "Celular")
    g3 = await _garantia(
        db, "300003", "dg101.pi", "Uranyx T8 - Preto", None, entrega=None, pedido="sac"
    )
    g4 = await _garantia(db, "300004", "u015", "Air Fryer 4L", "Eletro", pedido="atendimento")
    await _garantia(db, "300005", "a001.sp", "Fone com fio", None)  # sem categoria nenhuma
    db.add(
        GarantiaAtendimento(
            garantia_id=g4.id,
            data_atendimento=datetime(2026, 10, 15, tzinfo=UTC),
            atendente_id=dono.id,
            atendente_nome="x",
            conversa_id=uuid.uuid4(),
            conversa_link="/atendimento",
            resumo="r",
            tipo_problema="hardware",
            cobertura="coberto",
            solucao="troca",
        )
    )
    await db.commit()

    r = await client.post("/api/garantias/resumo", json={})
    assert r.status_code == 200, r.text
    h = r.json()
    assert (h["vendidas"], h["pediram"]) == (5, 3)
    # As pílulas: o total por departamento, em ordem de pedidos.
    assert [(d["chave"], d["vendidas"], d["pediram"]) for d in h["departamentos"]] == [
        ("celular", 3, 2),
        ("eletro", 1, 1),
        ("acessorios", 1, 0),
    ]
    por = {p["chave"].split(":", 1)[0] + ":" + p["skus"][0]: p for p in h["produtos"]}
    a17 = next(p for p in h["produtos"] if p["skus"] == ["dg052", "dg053", "dg054"])
    assert (a17["nome"], a17["na_tabela"], a17["departamento"]) == (
        "uranyx A17 pro max 12.128",
        True,
        "celular",
    )
    assert (a17["vendidas"], a17["pediram"], a17["origens"]["mensagem"]) == (2, 1, 1)
    assert a17["status"]["ativa"] == 1 and len(a17["pedidos"]) == 1
    # Fora da Tabela de Preços: pelo SKU, com a descrição da venda.
    t8 = por["sku:dg101"]
    assert (t8["na_tabela"], t8["nome"], t8["pediram"]) == (False, "Uranyx T8 - Preto", 1)
    assert t8["status"]["aguardando_entrega"] == 1 and t8["pedidos"] == [g3.id]
    ar = next(p for p in h["produtos"] if p["skus"] == ["u015"])
    assert (ar["departamento"], ar["com_atendimento"], ar["atendimentos_hardware"]) == (
        "eletro",
        1,
        1,
    )
    # Em ordem de pedidos; o produto sem pedido fica no fim.
    assert h["produtos"][-1]["pediram"] == 0

    # Os filtros da barra valem nas vendidas e nos pedidos: só as aguardando entrega.
    r = await client.post("/api/garantias/resumo", json={"status": "aguardando_entrega"})
    assert (r.json()["vendidas"], r.json()["pediram"]) == (1, 1)
    assert r.json()["produtos"][0]["skus"] == ["dg101"]
    # A linha abre os pedidos dela na lista.
    itens = (await client.post("/api/garantias/lista", json={"ids": a17["pedidos"]})).json()[
        "itens"
    ]
    assert [i["pedido_bling"] for i in itens] == ["300001"]


async def test_resumo_sem_as_descartadas_e_sem_o_nao_e_pedido(client, db, make_user, auth_as):
    auth_as(await make_user(role=UserRole.ADMIN))
    g = await _garantia(db, "300010", "dg053.sp", "A17", "Celular", pedido="mensagem")
    a = await _garantia(db, "300011", "u015", "Air Fryer", "Eletro", pedido="mensagem")
    g.descartada_em = datetime(2026, 10, 19, tzinfo=UTC)
    # A pessoa disse "não é pedido de garantia": continua vendida, sai dos pedidos.
    a.nao_solicitada_em = datetime(2026, 10, 19, tzinfo=UTC)
    await db.commit()
    h = (await client.post("/api/garantias/resumo", json={})).json()
    assert (h["vendidas"], h["pediram"]) == (1, 0)
    assert h["departamentos"][0]["chave"] == "eletro" and len(h["produtos"]) == 1


async def test_periodo_pelo_pedido_conta_so_os_pedidos(client, db, make_user, auth_as):
    """O período pela data do pedido filtra os PEDIDOS; as vendidas são todas
    (senão a porcentagem daria 100%)."""
    auth_as(await make_user(role=UserRole.ADMIN))
    await _garantia(db, "300020", "dg053.sp", "A17", "Celular", pedido="mensagem")  # 12/10
    await _garantia(db, "300021", "dg054.sp", "A17 Branco", "Celular")
    corpo = {"periodo": "pedido", "de": "2026-10-13", "ate": "2026-10-31"}
    h = (await client.post("/api/garantias/resumo", json=corpo)).json()
    assert (h["vendidas"], h["pediram"]) == (2, 0)
    corpo["de"] = "2026-10-12"
    h = (await client.post("/api/garantias/resumo", json=corpo)).json()
    assert (h["vendidas"], h["pediram"]) == (2, 1)
    # Na lista, o mesmo período filtra as garantias (só os pedidos).
    itens = (await client.post("/api/garantias/lista", json=corpo)).json()["itens"]
    assert [i["pedido_bling"] for i in itens] == ["300020"]


async def test_entregue_sem_data_fica_fora_do_aguardando(client, db, make_user, auth_as):
    auth_as(await make_user(role=UserRole.ADMIN))
    await _garantia(db, "300030", "dg053.sp", "A17", "Celular", entrega=None, pedido="mensagem")
    await _garantia(db, "300031", "dg053.ci", "A17", "Celular", entrega=None, pedido="mensagem")
    linha = (await db.execute(select(BlingOrder).where(BlingOrder.numero == "300031"))).scalar_one()
    linha.situacao = "83953"  # Entregue no Bling, sem data no DaVinci
    await db.commit()
    [p] = (await client.post("/api/garantias/resumo", json={})).json()["produtos"]
    st = p["status"]
    assert (st["aguardando_entrega"], st["entregue_sem_data"]) == (1, 1)
