"""Resumo do Painel de Garantia (28/10/2026): as garantias por departamento
(Celular, Eletro, Acessórios), pela categoria do item no pedido do Bling — a
mesma do Faturamento —, com o cadastro do produto e a família do SKU de
reserva."""

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from app.models import (
    BlingOrder,
    Garantia,
    GarantiaAtendimento,
    GarantiaConversa,
    Product,
    UserRole,
)
from app.models.atendimento import AtendimentoConversa, AtendimentoMensagem
from app.models.product import ProductCategory
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
    db, numero, sku, descricao, categoria=None, *, entrega=date(2026, 10, 5), nf=None
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
    db.add(g)
    await db.commit()
    return g


async def test_resumo_por_departamento(client, db, make_user, auth_as):
    dono = await make_user(role=UserRole.ADMIN)
    auth_as(dono)
    # Cadastro do produto sem categoria no pedido (dg101 — o T8 novo).
    db.add(ProductCategory(bling_category_id=8680318, name="Celular"))
    db.add(Product(user_id=dono.id, sku="dg101.pi", name="Uranyx T8", category="8680318"))
    await db.commit()
    g1 = await _garantia(
        db, "300001", "dg053.sp+a001.sp", "A17 Pro Max - Laranja + Fone", "Celular Kit"
    )
    await _garantia(db, "300002", "dg053.ci", "A17 Pro Max - Laranja", "Celular")
    await _garantia(db, "300003", "dg101.pi", "Uranyx T8 - Preto", None, entrega=None)
    g4 = await _garantia(db, "300004", "u015", "Air Fryer 4L", "Eletro")
    await _garantia(db, "300005", "a001.sp", "Fone com fio", None)  # sem categoria nenhuma
    # Conversa na 1 e atendimento (hardware) na 4.
    c = AtendimentoConversa(plataforma="shopee", canal="chat", externo_id="x")
    so_loja = AtendimentoConversa(plataforma="shopee", canal="chat", externo_id="y")
    db.add_all([c, so_loja])
    await db.flush()
    for conv, autor, quando in (
        (c, "cliente", datetime(2026, 10, 10, tzinfo=UTC)),
        (c, "cliente", datetime(2026, 9, 20, tzinfo=UTC)),  # antes da compra
        (so_loja, "loja", datetime(2026, 10, 10, tzinfo=UTC)),
    ):
        db.add(
            AtendimentoMensagem(
                conversa_id=conv.id,
                autor=autor,
                origem=autor,
                tipo="texto",
                texto="oi",
                enviada_em=quando,
            )
        )
    db.add(GarantiaConversa(garantia_id=g1.id, conversa_id=c.id, como="pedido"))
    g2 = (await db.execute(select(Garantia).where(Garantia.pedido_bling == "300002"))).scalar_one()
    db.add(GarantiaConversa(garantia_id=g2.id, conversa_id=so_loja.id, como="pedido"))
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
    assert h["total"] == 5
    deps = {d["chave"]: d for d in h["departamentos"]}
    assert [d["chave"] for d in h["departamentos"]] == ["celular", "eletro", "acessorios"]
    cel = deps["celular"]
    assert cel["total"] == 3
    assert {c["nome"]: c["total"] for c in cel["categorias"]} == {
        "Celular Kit": 1,
        "Celular": 2,  # a do pedido (dg053.ci) + a do cadastro (dg101.pi)
    }
    assert cel["status"]["aguardando_entrega"] == 1 and cel["status"]["ativa"] == 2
    # Conversa ligada só com a fala da LOJA não conta como "cliente escreveu".
    assert cel["cliente_escreveu"] == 1
    assert [(p["base"], p["total"]) for p in cel["produtos"]] == [("dg053", 2), ("dg101", 1)]
    assert cel["produtos"][0]["nome"] == "A17 Pro Max - Laranja"
    ele = deps["eletro"]
    assert (ele["total"], ele["com_atendimento"], ele["atendimentos_hardware"]) == (1, 1, 1)
    ace = deps["acessorios"]
    assert ace["categorias"] == [
        {"nome": "sem categoria no Bling", "total": 1, "sem_categoria": True}
    ]

    # Os filtros da barra valem: só as aguardando entrega.
    r = await client.post("/api/garantias/resumo", json={"status": "aguardando_entrega"})
    assert r.json()["total"] == 1
    assert r.json()["departamentos"][0]["produtos"][0]["base"] == "dg101"


async def test_resumo_sem_as_descartadas_e_no_escopo_da_equipe(client, db, make_user, auth_as):
    auth_as(await make_user(role=UserRole.ADMIN))
    g = await _garantia(db, "300010", "dg053.sp", "A17", "Celular")
    await _garantia(db, "300011", "u015", "Air Fryer", "Eletro")
    g.descartada_em = datetime(2026, 10, 19, tzinfo=UTC)
    await db.commit()
    h = (await client.post("/api/garantias/resumo", json={})).json()
    assert h["total"] == 1 and h["departamentos"][0]["chave"] == "eletro"


async def test_cliente_escreveu_conta_a_compra_de_cada_garantia(client, db, make_user, auth_as):
    """A fala do cliente ANTES da compra da própria garantia não conta, mesmo
    depois da compra de OUTRA garantia (a subconsulta da compra tem de estar
    presa à garantia da linha)."""
    auth_as(await make_user(role=UserRole.ADMIN))
    await _garantia(db, "300020", "dg053.sp", "A17", "Celular")  # compra 01/10
    tarde = await _garantia(db, "300021", "dg054.sp", "A17 Branco", "Celular")
    linha = (await db.execute(select(BlingOrder).where(BlingOrder.numero == "300021"))).scalar_one()
    linha.data = datetime(2026, 10, 15, 3, tzinfo=UTC)  # comprou em 15/10
    c = AtendimentoConversa(plataforma="shopee", canal="chat", externo_id="z")
    db.add(c)
    await db.flush()
    db.add(
        AtendimentoMensagem(
            conversa_id=c.id,
            autor="cliente",
            origem="cliente",
            tipo="texto",
            texto="tem na cor branca?",  # 10/10: antes da compra dele (15/10)
            enviada_em=datetime(2026, 10, 10, tzinfo=UTC),
        )
    )
    db.add(GarantiaConversa(garantia_id=tarde.id, conversa_id=c.id, como="comprador"))
    await db.commit()
    h = (await client.post("/api/garantias/resumo", json={})).json()
    assert h["departamentos"][0]["cliente_escreveu"] == 0


async def test_entregue_sem_data_fica_fora_do_aguardando(client, db, make_user, auth_as):
    auth_as(await make_user(role=UserRole.ADMIN))
    await _garantia(db, "300030", "dg053.sp", "A17", "Celular", entrega=None)
    await _garantia(db, "300031", "dg053.ci", "A17", "Celular", entrega=None)
    linha = (await db.execute(select(BlingOrder).where(BlingOrder.numero == "300031"))).scalar_one()
    linha.situacao = "83953"  # Entregue no Bling, sem data no DaVinci
    await db.commit()
    st = (await client.post("/api/garantias/resumo", json={})).json()["departamentos"][0]["status"]
    assert (st["aguardando_entrega"], st["entregue_sem_data"]) == (1, 1)
