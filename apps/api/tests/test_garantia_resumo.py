"""Resumo do Painel de Garantia (28/10/2026): a garantia POR PRODUTO — uma
linha por produto da Tabela de Preços (o modelo, com as cores juntas), as
vendas com garantia e o SALDO delas, os PEDIDOS de garantia (de onde vieram) e
a manutenção com o GASTO da tela Reembolso; e o total por departamento.

As 3 partes (04/11/2026): o Resumo é só do admin, os departamentos são os da
Tabela de Preços (Celular, Mala, Eletro — a raiz do segmento da linha; fora da
Tabela, a maioria das vendas) e a % da tela é gasto ÷ saldo × 100."""

import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from app.models import (
    BlingOrder,
    Garantia,
    GarantiaAtendimento,
    Product,
    UserRole,
)
from app.models.atendimento import AtendimentoReclamacao
from app.models.devolution import Devolution
from app.models.pricing import PricingProduct
from app.models.product import ProductCategory
from app.models.refund import Refund
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


@pytest.fixture(autouse=True)
def _mapa_novo():
    """O mapa garantia → linha → departamento é guardado por 60 s: cada teste
    começa sem ele."""
    resumo.limpar_cache()
    yield
    resumo.limpar_cache()


def test_departamento_pela_categoria_e_pela_familia():
    # 04/11/2026: Celular, Mala e Eletro (o acessório do celular é Celular).
    assert resumo.DEPARTAMENTOS == {"celular": "Celular", "mala": "Mala", "eletro": "Eletro"}
    assert list(resumo.DEPARTAMENTOS) == ["celular", "mala", "eletro"]
    assert resumo.departamento_da_categoria("Celular Kit") == "celular"
    assert resumo.departamento_da_categoria("Celular Usado") == "celular"
    assert resumo.departamento_da_categoria("Acessórios Celular") == "celular"
    assert resumo.departamento_da_categoria("Eletro") == "eletro"
    assert resumo.departamento_da_categoria("Mala Kit") == "mala"
    assert resumo.departamento_da_categoria("Embalagem") is None
    assert resumo.departamento_da_categoria("  ") is None
    assert resumo.departamento_da_familia("dg101.pi") == "celular"
    assert resumo.departamento_da_familia("a001.sp") == "celular"
    assert resumo.departamento_da_familia("u015") == "eletro"
    assert resumo.departamento_da_familia("b021") == "mala"
    assert resumo.departamento_da_familia("bp3.sp") == "mala"
    assert resumo.departamento_da_familia("x99") is None
    # A venda: a categoria do pedido, a do cadastro, a família; sem nada, Celular.
    assert resumo.departamento_da_venda("Eletro Kit", "Mala", "dg053") == "eletro"
    assert resumo.departamento_da_venda("Embalagem", "Mala", "dg053") == "mala"
    assert resumo.departamento_da_venda(None, None, "b021") == "mala"
    assert resumo.departamento_da_venda(None, None, "zz9") == "celular"
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
    db,
    numero,
    sku,
    descricao,
    categoria=None,
    *,
    entrega=date(2026, 10, 5),
    nf=None,
    pedido=None,
    base=1000,
    comissao=None,
    frete=None,
    situacao="15",
):
    db.add(
        BlingOrder(
            numero=numero,
            numeroloja=f"MKT{numero}",
            loja="L1",
            data=datetime(2026, 10, 1, tzinfo=UTC),
            total=1000,
            situacao=situacao,
            item_index=0,
            item_codigo=sku,
            item_descricao=descricao,
            item_quantidade=1,
            itemvalor=base,
            valorbase=base,
            taxacomissao=comissao,
            custofrete=frete,
            categoria_nome=categoria,
        )
    )
    g = Garantia(
        origem="robo",
        pedido_bling=numero,
        pedido_marketplace=f"MKT{numero}",
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


async def _raiz(db, slug, pai=None) -> Segment:
    seg = Segment(name=slug.title(), slug=slug, parent_id=pai.id if pai else None)
    db.add(seg)
    await db.flush()
    return seg


async def _tabela(db, dono, *linhas, segmento=None):
    """Linhas da Tabela de Preços: (skus separados por vírgula, nome) — na raiz
    "celular" (o departamento), ou no `segmento` dado."""
    seg = segmento or await _raiz(db, "celular")
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
    # A Tabela de Preços (04/11/2026): o departamento é a RAIZ do segmento —
    # o fone no segmento "acessorios" debaixo de "celular" é Celular.
    celular = await _raiz(db, "celular")
    acessorios = await _raiz(db, "acessorios", celular)
    eletro = await _raiz(db, "eletro")
    mala = await _raiz(db, "mala")
    # O A17 com as cores juntas; o kit não rouba o SKU puro.
    await _tabela(
        db,
        dono,
        ("dg052,dg053,dg054", "uranyx A17 pro max 12.128"),
        ("dg053+a003", "kit A17 + fone"),
        segmento=celular,
    )
    await _tabela(db, dono, ("a001", "fone com fio"), segmento=acessorios)
    await _tabela(db, dono, ("u015", "airfryer 4L"), segmento=eletro)
    await _tabela(db, dono, ("b021", "mala de bordo"), segmento=mala)
    g1 = await _garantia(
        db,
        "300001",
        "dg053.sp+a001.sp",
        "A17 Pro Max - Laranja + Fone",
        "Celular Kit",
        pedido="mensagem",
        comissao=150,
        frete=50,
    )
    await _garantia(db, "300002", "dg054.ci", "A17 Pro Max - Branco", "Celular", base=900)
    g3 = await _garantia(
        db, "300003", "dg101.pi", "Uranyx T8 - Preto", None, entrega=None, pedido="sac"
    )
    g4 = await _garantia(db, "300004", "u015", "Air Fryer 4L", "Eletro", pedido="atendimento")
    await _garantia(db, "300005", "a001.sp", "Fone com fio", None)
    await _garantia(db, "300006", "b021", "Mala de bordo", "Mala", pedido="devolucao")
    # Fora da Tabela, a maioria das vendas decide: 2 Mala × 1 Eletro.
    await _garantia(db, "300007", "u777", "Mala grande", "Mala Kit")
    await _garantia(db, "300008", "u777.sp", "Mala grande", "Mala")
    await _garantia(db, "300009", "u777", "Mala grande", "Eletro", situacao="12")
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
    assert (h["vendidas"], h["pediram"], h["ve_valores"]) == (9, 4, True)
    por = {p["skus"][0]: p for p in h["produtos"]}
    a17, t8, fone, ar, mala_b, u777 = (
        por["dg052"],
        por["dg101"],
        por["a001"],
        por["u015"],
        por["b021"],
        por["u777"],
    )
    assert (a17["nome"], a17["na_tabela"], a17["departamento"]) == (
        "uranyx A17 pro max 12.128",
        True,
        "celular",
    )
    assert (fone["departamento"], ar["departamento"], mala_b["departamento"]) == (
        "celular",
        "eletro",
        "mala",
    )
    # Fora da Tabela de Preços: pelo SKU, com a descrição da venda.
    assert (t8["na_tabela"], t8["nome"], t8["departamento"]) == (
        False,
        "Uranyx T8 - Preto",
        "celular",
    )
    assert (u777["na_tabela"], u777["departamento"], u777["vendidas"]) == (False, "mala", 3)
    # As pílulas: os três departamentos sempre, nesta ordem (a soma das linhas).
    assert [(d["chave"], d["nome"], d["vendidas"], d["pediram"]) for d in h["departamentos"]] == [
        ("celular", "Celular", 4, 2),
        ("mala", "Mala", 4, 1),
        ("eletro", "Eletro", 1, 1),
    ]
    # O SALDO de todas as vendas: base − comissão − frete (o A17: 800 + 900);
    # o pedido cancelado (12) não tem saldo.
    assert (a17["saldo"], u777["saldo"], ar["saldo"]) == (1700.0, 2000.0, 1000.0)
    # A pílula soma as linhas do departamento (o A17, o T8 e o fone).
    assert [d["saldo"] for d in h["departamentos"]] == [3700.0, 3000.0, 1000.0]
    assert [d["gasto"] for d in h["departamentos"]] == [0.0, 0.0, 0.0]
    assert (a17["vendidas"], a17["pediram"], a17["origens"]) == (
        2,
        1,
        {"mensagem": 1, "sac": 0, "devolucao": 0},
    )
    # Ordem: vendidas, depois o nome.
    assert [p["skus"][0] for p in h["produtos"]] == [
        "u777",
        "dg052",
        "u015",
        "a001",
        "b021",
        "dg101",
    ]
    # Saiu (04/11/2026): a situação de quem pediu, os atendimentos, o "Outros"
    # e o "No técnico".
    for p in h["produtos"]:
        for campo in (
            "status",
            "com_atendimento",
            "atendimentos_hardware",
            "atendimentos_software",
            "no_tecnico",
            "no_tecnico_vencidos",
            "consertados",
            "consertados_sem_custo",
            "valor_no_tecnico",
        ):
            assert campo not in p, campo
        assert set(p["origens"]) == {"mensagem", "sac", "devolucao"}
    # O número da célula abre só os pedidos dele: as origens com coluna
    # (o atendimento registrado não tem coluna).
    assert a17["pedidos"] == [g1.id] and a17["pedidos_por_coluna"] == {"mensagem": [g1.id]}
    assert t8["pedidos_por_coluna"] == {"sac": [g3.id]}
    assert ar["pedidos"] == [g4.id] and ar["pedidos_por_coluna"] == {}
    assert set(mala_b["pedidos_por_coluna"]) == {"devolucao"}

    # Os filtros da barra valem nas vendidas e nos pedidos: só as aguardando entrega.
    r = await client.post("/api/garantias/resumo", json={"status": "aguardando_entrega"})
    assert (r.json()["vendidas"], r.json()["pediram"]) == (1, 1)
    assert [p["skus"] for p in r.json()["produtos"]] == [["dg101"]]
    assert [d["chave"] for d in r.json()["departamentos"]] == ["celular", "mala", "eletro"]
    # A linha abre os pedidos dela na lista.
    itens = (await client.post("/api/garantias/lista", json={"ids": a17["pedidos"]})).json()[
        "itens"
    ]
    assert [i["pedido_bling"] for i in itens] == ["300001"]


async def test_resumo_so_para_admin(client, db, make_user, auth_as):
    """04/11/2026: "resumo vai aparecer só para os admin"."""
    await _garantia(db, "300050", "dg053.sp", "A17", "Celular", pedido="mensagem")
    auth_as(await make_user(permissions={"garantias": {"view": True, "edit": True}}))
    r = await client.post("/api/garantias/resumo", json={})
    assert r.status_code == 403
    assert r.json()["detail"] == {"code": "so_admin"}
    # A lista continua aberta para quem consulta.
    assert (await client.post("/api/garantias/lista", json={})).status_code == 200
    auth_as(await make_user())
    r = await client.post("/api/garantias/resumo", json={})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "forbidden"


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
    assert len(h["produtos"]) == 1 and h["produtos"][0]["departamento"] == "eletro"
    assert [(d["chave"], d["vendidas"]) for d in h["departamentos"]] == [
        ("celular", 0),
        ("mala", 0),
        ("eletro", 1),
    ]


async def test_periodo_pelo_pedido_conta_so_os_pedidos(client, db, make_user, auth_as):
    """O período pela data do pedido filtra os PEDIDOS; as vendidas são todas."""
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


async def _devolucao(
    db,
    pedido,
    sku,
    condicao,
    *,
    manutencao=False,
    destino=None,
    custo=100.0,
    conserto=None,
    prazo=None,
    em=None,
):
    d = Devolution(
        pedido_bling=pedido,
        conta="Loja X",
        sku=sku,
        condicao_produto=condicao,
        manutencao=manutencao,
        manutencao_destino=destino,
        custo_produto=custo,
        custo_manutencao=conserto,
        prazo=prazo,
    )
    if em is not None:
        d.created_at = em
    db.add(d)
    await db.commit()
    return d


async def _reembolso(
    db, pedido, tipo, *, reembolso=0.0, prejuizo=None, em=None, mkt=None, conferido=False
):
    r = Refund(
        pedido_bling=pedido,
        pedido_marketplace=mkt,
        conta="Loja X",
        tipo=tipo,
        reembolso=reembolso,
        prejuizo=prejuizo,
        conferido=conferido,
    )
    if em is not None:
        r.created_at = em
    db.add(r)
    await db.commit()
    return r


def _em(dia: int, hora: int = 12) -> datetime:
    return datetime(2026, 10, dia, hora, tzinfo=UTC)


async def test_manutencao_e_o_gasto_da_tela_reembolso(client, db, make_user, auth_as):
    """04/11/2026 ("manutenção, o gasto é baseado na tabela de reembolso"): dos
    que PEDIRAM, os consertos e os perdidos pelo aparelho na tela Devoluções; o
    gasto da tela Reembolso — o conserto (o −1 de tirar da Manutenção não), o
    aparelho perdido (menos o que a plataforma cobriu) e o reembolso ao
    cliente. O fone do kit no mesmo pedido não conta; Logística não é gasto."""
    dono = await make_user(role=UserRole.ADMIN)
    auth_as(dono)
    await _tabela(db, dono, ("dg052,dg053,dg054", "uranyx A17 pro max 12.128"))
    # 1: no técnico, vencido; o conserto (a linha do Reembolso nasceu com a
    # devolução do celular) conta; o fone do kit extraviado não.
    g1 = await _garantia(
        db, "300101", "dg053.sp+a001.sp", "A17 + Fone", "Celular Kit", pedido="mensagem"
    )
    await _devolucao(db, "300101", "dg053.sp", "Manutenção", custo=600, prazo=_em(1, 15), em=_em(1))
    await _reembolso(db, "300101", "Manutenção", reembolso=-150, prejuizo=0, em=_em(1))
    await _devolucao(db, "300101", "a001.sp", "Extraviado", custo=4.5, em=_em(2))
    await _reembolso(db, "300101", "Extraviado", prejuizo=4.5, em=_em(2))
    # 2: consertado, R$ 229; 3: consertado só com o −1 (sem custo lançado).
    g2 = await _garantia(db, "300102", "dg052.ci", "A17 Preto", "Celular", pedido="devolucao")
    await _devolucao(db, "300102", "dg052.ci", "Usado", manutencao=True, conserto=229, em=_em(3))
    await _reembolso(db, "300102", "Manutenção", reembolso=-229, em=_em(3), conferido=True)
    g3 = await _garantia(db, "300103", "dg054.pi", "A17 Branco", "Celular", pedido="devolucao")
    await _devolucao(db, "300103", "dg054.pi", "Novo", manutencao=True, conserto=1, em=_em(4))
    await _reembolso(db, "300103", "Manutenção", reembolso=-1, em=_em(4))
    # 4: perdido pelo destino Sucata; a plataforma cobriu R$ 100 dos 560; e o
    # conserto que não deu certo (R$ 80).
    g4 = await _garantia(db, "300104", "dg053.ci", "A17 Laranja", "Celular", pedido="mensagem")
    await _devolucao(
        db, "300104", "dg053.ci", "Manutenção", destino="Sucata", custo=560, conserto=80, em=_em(5)
    )
    await _reembolso(db, "300104", "Manutenção", reembolso=-80, em=_em(5))
    await _reembolso(db, "300104", "Sucata", prejuizo=560, reembolso=100, em=_em(6))
    # 5: voltou Novo, sem manutenção; o frete (Logística) e a linha sem tipo não são gasto.
    g5 = await _garantia(db, "300105", "dg053.sp", "A17", "Celular", pedido="mensagem")
    await _devolucao(db, "300105", "dg053.sp", "Novo", em=_em(7))
    await _reembolso(db, "300105", "Logistica", reembolso=-12, prejuizo=12)
    await _reembolso(db, "300105", None, reembolso=-40)
    # 6: a venda SEM pedido com o aparelho perdido: fora.
    await _garantia(db, "300106", "dg053.sp", "A17", "Celular")
    await _devolucao(db, "300106", "dg053.sp", "Extraviado", custo=600)
    await _reembolso(db, "300106", "Extraviado", prejuizo=600)
    # 7: "Não devolvido" e o reembolso ao cliente.
    g7 = await _garantia(db, "300107", "dg053.ci", "A17", "Celular", pedido="mensagem")
    await _devolucao(db, "300107", "dg053.ci", "Não devolvido")
    await _reembolso(db, "300107", "Cliente", reembolso=-30)
    # 8: o reembolso ao cliente lançado sem o pedido do Bling: pelo da
    # plataforma; o de OUTRO pedido do Bling com o mesmo nº da plataforma não.
    g8 = await _garantia(db, "300108", "dg053.pi", "A17", "Celular", pedido="mensagem")
    await _reembolso(db, " ", "Cliente", reembolso=-45, mkt=" MKT300108 ")
    await _reembolso(db, "999999", "Cliente", reembolso=-99, mkt="MKT300108")

    h = (await client.post("/api/garantias/resumo", json={})).json()
    assert h["ve_valores"] is True
    [a17] = h["produtos"]
    assert (a17["vendidas"], a17["pediram"], a17["seguiram"]) == (8, 7, 7)
    assert (a17["consertos"], a17["perdidos"]) == (2, 1)
    # Conserto 150 + 229 + 0 + 80; perdido 560 − 100; cliente 30 + 45.
    assert (
        a17["gasto"],
        a17["gasto_conserto"],
        a17["gasto_perdidos"],
        a17["gasto_cliente"],
    ) == (994.0, 459.0, 460.0, 75.0)
    assert h["departamentos"][0]["gasto"] == 994.0
    col = a17["pedidos_por_coluna"]
    assert sorted(col["consertos"]) == sorted([g2.id, g3.id])
    assert col["perdidos"] == [g4.id]
    assert sorted(col["gasto"]) == sorted([g1.id, g2.id, g4.id, g7.id, g8.id])
    assert set(col) == {"mensagem", "devolucao", "consertos", "perdidos", "gasto"}

    # Na lista, o selo do aparelho com o MESMO gasto (o da tela Reembolso).
    corpo = {"ids": [g.id for g in (g1, g2, g3, g4, g5, g7, g8)]}
    itens = (await client.post("/api/garantias/lista", json=corpo)).json()["itens"]
    selo = {
        i["pedido_bling"]: (i["devolucao_situacao"], i["devolucao_rotulo"], i["devolucao_gasto"])
        for i in itens
    }
    assert selo["300101"] == ("no_tecnico", "No técnico · vencido há 19 dias · R$ 150,00", 150.0)
    assert selo["300102"] == ("consertado", "Consertado (Usado) · R$ 229,00", 229.0)
    assert selo["300103"] == ("consertado", "Consertado (Novo) · sem custo lançado", 0.0)
    assert selo["300104"] == ("perdido", "Perdido (Sucata) · R$ 540,00", 540.0)
    assert selo["300105"] == ("devolvido", "Devolvido (Novo)", 0.0)
    assert selo["300107"] == ("reembolso", "Reembolso ao cliente · R$ 30,00", 30.0)
    assert selo["300108"] == ("reembolso", "Reembolso ao cliente · R$ 45,00", 45.0)
    # O topo da lista não tem mais o "seguiram" (04/11/2026).
    lista = (await client.post("/api/garantias/lista", json={})).json()
    assert "seguiram" not in lista["indicadores"]

    # Quem não é admin vê o selo, sem os reais (e não abre o Resumo).
    auth_as(await make_user(permissions={"garantias": {"view": True}}))
    itens = (await client.post("/api/garantias/lista", json=corpo)).json()["itens"]
    selo = {i["pedido_bling"]: (i["devolucao_rotulo"], i["devolucao_gasto"]) for i in itens}
    assert selo["300102"] == ("Consertado (Usado)", None)
    assert selo["300104"] == ("Perdido (Sucata)", None)
    assert selo["300107"] == ("Reembolso ao cliente", None)


def test_rotulo_do_prazo_de_quem_esta_no_tecnico():
    f = resumo.Desfecho(situacao="no_tecnico", prazo=datetime(2026, 10, 25, 15, tzinfo=UTC))
    assert resumo.rotulo_do_desfecho(f, date(2026, 10, 20), False) == "No técnico · faltam 5 dias"
    assert resumo.rotulo_do_desfecho(f, date(2026, 10, 24), False) == "No técnico · faltam 1 dia"
    assert resumo.rotulo_do_desfecho(f, date(2026, 10, 25), False) == "No técnico · vence hoje"
    assert (
        resumo.rotulo_do_desfecho(f, date(2026, 10, 26), False) == "No técnico · vencido há 1 dia"
    )
    assert (
        resumo.rotulo_do_desfecho(resumo.Desfecho(situacao="no_tecnico"), HOJE, True)
        == "No técnico"
    )
    assert resumo.rotulo_do_desfecho(resumo.Desfecho(), HOJE, True) is None
    devolvido = resumo.Desfecho(situacao="devolvido", condicao="Novo", devolveu=True)
    assert resumo.rotulo_do_desfecho(devolvido, HOJE, True) == "Devolvido (Novo)"
    sem_condicao = resumo.Desfecho(situacao="devolvido", devolveu=True)
    assert (
        resumo.rotulo_do_desfecho(sem_condicao, HOJE, False) == "Devolvido · sem condição lançada"
    )
    # O conserto da tela Reembolso sem a devolução lançada.
    gasto = resumo.Desfecho(conserto=1234.5)
    assert resumo.rotulo_do_desfecho(gasto, HOJE, True) == "Conserto · R$ 1.234,50"
    assert resumo.rotulo_do_desfecho(gasto, HOJE, False) is None
    assert resumo.rotulo_do_desfecho(resumo.Desfecho(perdido=12.5), HOJE, True) == (
        "Gasto · R$ 12,50"
    )
    assert resumo.Desfecho(conserto=10, perdido=20.25, cliente=5).gasto == 35.25


def _linha_dev(
    condicao,
    sku="dg053.sp",
    *,
    manutencao=False,
    destino=None,
    custo=600.0,
    conserto=None,
    prazo=None,
    em=None,
):
    return SimpleNamespace(
        sku=sku,
        condicao_produto=condicao,
        manutencao=manutencao,
        manutencao_destino=destino,
        custo_produto=custo,
        custo_manutencao=conserto,
        prazo=prazo,
        created_at=em,
    )


def _linha_reemb(tipo, reembolso=0.0, prejuizo=None, em=None, i=None):
    return SimpleNamespace(
        id=i or uuid.uuid4(), tipo=tipo, reembolso=reembolso, prejuizo=prejuizo, created_at=em
    )


def test_o_prazo_vence_pelo_dia_em_sao_paulo_e_so_de_quem_esta_no_tecnico():
    """Revisão 30/10: o vencido é pelo dia em São Paulo (como o selo e a tela
    Devoluções — no dia do prazo é "vence hoje"); e a garantia em Perdidos
    não entra no "vencido"."""
    tabela = {"dg053": ("1", "A17", ["dg053"])}
    # Prazo hoje às 08:00 em São Paulo (11:00 UTC): ainda não venceu.
    hoje_cedo = datetime(2026, 10, 30, 11, tzinfo=UTC)
    lancada = datetime(2026, 9, 30, 11, tzinfo=UTC)
    f = resumo.desfecho(
        [_linha_dev("Manutenção", prazo=hoje_cedo, em=lancada)],
        "tabela:1",
        tabela,
        date(2026, 10, 30),
    )
    assert (f.situacao, f.vencido, f.desde) == ("no_tecnico", False, lancada)
    assert resumo.rotulo_do_desfecho(f, date(2026, 10, 30), False) == "No técnico · vence hoje"
    f = resumo.desfecho(
        [_linha_dev("Manutenção", prazo=hoje_cedo)], "tabela:1", tabela, date(2026, 10, 31)
    )
    assert (
        f.vencido
        and resumo.rotulo_do_desfecho(f, date(2026, 10, 31), False)
        == "No técnico · vencido há 1 dia"
    )
    # Um aparelho perdido e outro (do mesmo produto) no técnico, vencido: a
    # garantia é Perdidos — sem vencido. O gasto vem da tela Reembolso (sem
    # linha lá, sem gasto).
    ontem = datetime(2026, 10, 1, 15, tzinfo=UTC)
    f = resumo.desfecho(
        [_linha_dev("Extraviado", custo=560), _linha_dev("Manutenção", prazo=ontem)],
        "tabela:1",
        tabela,
        date(2026, 10, 30),
    )
    assert (f.situacao, f.condicao, f.vencido, f.prazo, f.gasto) == (
        "perdido",
        "Extraviado",
        False,
        None,
        0.0,
    )
    # O fone do kit no mesmo pedido (outro produto) não decide nada.
    f = resumo.desfecho(
        [_linha_dev("Extraviado", sku="a001.sp", custo=4.5)], "tabela:1", tabela, date(2026, 10, 30)
    )
    assert (f.situacao, f.gasto, f.devolveu, f.seguiu) == (None, 0.0, False, False)
    # Devolveram: o aparelho voltou (a sem condição ainda também); o "Não
    # devolvido" (o cliente ficou com ele) não.
    hoje = date(2026, 10, 30)
    f = resumo.desfecho([_linha_dev("Não devolvido")], "tabela:1", tabela, hoje)
    assert (f.situacao, f.devolveu) == (None, False)
    f = resumo.desfecho([_linha_dev(None, em=lancada)], "tabela:1", tabela, hoje)
    assert (f.situacao, f.condicao, f.devolveu, f.desde) == ("devolvido", None, True, lancada)
    f = resumo.desfecho([_linha_dev("Extraviado", custo=560)], "tabela:1", tabela, hoje)
    assert (f.situacao, f.devolveu, f.seguiu) == ("perdido", True, True)
    # Seguiu fora da tela Devoluções (a plataforma ou o Reembolso): sem selo
    # do aparelho, o selo é o do caminho — desde o caso.
    f = resumo.desfecho([], "tabela:1", tabela, hoje, "a_caminho", fora_desde=lancada)
    assert (f.situacao, f.devolveu, f.seguiu, f.desde) == ("a_caminho", False, True, lancada)
    f = resumo.desfecho([_linha_dev("Não devolvido")], "tabela:1", tabela, hoje, "reembolso")
    assert (f.situacao, f.devolveu, f.seguiu, f.desde) == ("reembolso", False, True, None)
    # O aparelho na tela Devoluções decide o selo, mesmo com o reembolso pago.
    f = resumo.desfecho([_linha_dev("Manutenção")], "tabela:1", tabela, hoje, "reembolso")
    assert (f.situacao, f.seguiu) == ("no_tecnico", True)


def test_gasto_da_tela_reembolso():
    """Cada tipo da tela Reembolso vale uma conta, nunca negativa; o −1 de
    tirar da Manutenção não é conserto; Logística, Frete e sem tipo não são
    gasto."""
    v = resumo.valor_do_reembolso
    assert resumo.TIPOS_DO_GASTO == ("Manutenção", "Extraviado", "Sucata", "Cliente")
    assert v("Manutenção", 0, -150) == (150.0, 0.0, 0.0)
    assert v("Manutenção", 0, -1) == (0.0, 0.0, 0.0)
    assert v("Manutenção", 0, -0.5) == (0.0, 0.0, 0.0)
    assert v("Manutenção", 0, 20) == (0.0, 0.0, 0.0)
    assert v("Manutenção", None, None) == (0.0, 0.0, 0.0)
    assert v("Extraviado", 600, 0) == (0.0, 600.0, 0.0)
    assert v("Extraviado", 600, 200) == (0.0, 400.0, 0.0)
    assert v("Extraviado", 600, 700) == (0.0, 0.0, 0.0)
    assert v("Extraviado", 600, -50) == (0.0, 600.0, 0.0)
    assert v("Sucata", 560, None) == (0.0, 560.0, 0.0)
    assert v("Cliente", 0, -30) == (0.0, 0.0, 30.0)
    assert v("Cliente", 0, 30) == (0.0, 0.0, 0.0)
    for tipo in ("Logistica", "Frete", None):
        assert v(tipo, 50, -50) == (0.0, 0.0, 0.0)

    # A linha que nasceu com a devolução de OUTRO produto do pedido (o fone do
    # kit) fica de fora; a da devolução do mesmo produto, a de devolução sem
    # SKU e a sem devolução junto entram.
    tabela = {"dg053": ("1", "A17", ["dg053"])}
    t1, t2, t3, t4 = (datetime(2026, 10, d, 12, tzinfo=UTC) for d in (1, 2, 3, 4))
    devs = [
        _linha_dev("Manutenção", "dg053.sp", em=t1),
        _linha_dev("Extraviado", "a001.sp", em=t2),
        _linha_dev("Extraviado", None, em=t3),
    ]
    reembs = [
        _linha_reemb("Manutenção", -150, em=t1, i=1),
        _linha_reemb("Extraviado", 0, 4.5, em=t2, i=2),
        _linha_reemb("Extraviado", 0, 600, em=t3, i=3),
        _linha_reemb("Cliente", -30, em=t4, i=4),
        _linha_reemb("Logistica", -12, 12, em=t4, i=5),
    ]
    assert resumo.gasto_dos_reembolsos(reembs, devs, "tabela:1", tabela) == (
        150.0,
        600.0,
        30.0,
        [1, 3, 4],
    )
    # Do ponto de vista do fone (a outra linha), só o dele e os do pedido.
    assert resumo.gasto_dos_reembolsos(reembs, devs, "sku:a001", tabela) == (
        0.0,
        604.5,
        30.0,
        [2, 3, 4],
    )


def test_saldo_da_linha():
    """04/11/2026 ("saldo → valor de saldo de todos os itens com base na
    planilha de rentabilidade"): base − comissão − frete do pedido, na parte
    da linha pelo valor dos itens."""
    tabela = {"dg053": ("1", "A17", ["dg053"]), "u015": ("2", "Air Fryer", ["u015"])}

    def item(sku, valor, qtd=1, *, base=1000, comissao=100, frete=0, custo=None, situacao="9"):
        return SimpleNamespace(
            item_codigo=sku,
            itemvalor=valor,
            item_quantidade=qtd,
            valorbase=base,
            taxacomissao=comissao,
            custofrete=frete,
            preco_custo=custo,
            situacao=situacao,
        )

    s = resumo.saldo_da_linha
    # Um item só: o saldo inteiro (1000 − 150 − 50).
    assert s([item("dg053.sp", 1000, comissao=150, frete=50)], "tabela:1", tabela) == 800.0
    # O kit num item só é da linha do aparelho.
    assert s([item("dg053.sp+a001.sp", 1000)], "tabela:1", tabela) == 900.0
    # Dois produtos no pedido: a base, a comissão e o frete se repetem nas
    # linhas do pedido (contam uma vez); a parte é pelo valor × quantidade.
    pedido = [item("dg053.sp", 900), item("u015", 50, 2)]
    assert s(pedido, "tabela:1", tabela) == 810.0
    assert s(pedido, "tabela:2", tabela) == 90.0
    assert s(pedido, "sku:b021", tabela) == 0.0
    # O fone separado do kit (outra linha) fica com a parte dele.
    kit = [item("dg053.sp", 800), item("a003.ci", 40, 2)]
    assert s(kit, "tabela:1", tabela) == round(900 * 800 / 880, 2)
    # Sem valor nos itens: pelo número de itens.
    sem_valor = [item("dg053.sp", None), item("u015", 0)]
    assert s(sem_valor, "tabela:1", tabela) == 450.0
    # Quantidade em branco vale 1; base em branco vale 0.
    assert s([item("dg053.sp", 10, None, base=None, comissao=None)], "tabela:1", tabela) == 0.0
    # Cancelado (12) e excluído do Bling: sem saldo; o aguardando cancelamento tem.
    assert s([item("dg053.sp", 1000, situacao="12")], "tabela:1", tabela) == 0.0
    assert s([item("dg053.sp", 1000, situacao="excluido")], "tabela:1", tabela) == 0.0
    assert s([item("dg053.sp", 1000, situacao="83955")], "tabela:1", tabela) == 900.0
    assert s([], "tabela:1", tabela) == 0.0
    # O "Lucro" (desligado): tira também a parte da linha no custo dos produtos.
    assert resumo.SALDO_MENOS_CUSTO is False
    custo = [item("dg053.sp", 900, custo=500), item("u015", 100, custo=60)]
    assert s(custo, "tabela:1", tabela, menos_custo=True) == round((900 - 560) * 0.9, 2)
    assert s(custo, "tabela:1", tabela) == 810.0


async def test_duas_garantias_do_mesmo_pedido_contam_o_aparelho_uma_vez(
    client, db, make_user, auth_as
):
    """Revisão 30/10: o mesmo pedido com duas notas (uma garantia por nota)
    olha as mesmas devoluções — o aparelho perdido (e o gasto, e o saldo) conta
    uma vez só."""
    dono = await make_user(role=UserRole.ADMIN)
    auth_as(dono)
    await _tabela(db, dono, ("dg052,dg053,dg054", "uranyx A17 pro max 12.128"))
    g1 = await _garantia(db, "300301", "dg053.sp", "A17", "Celular", nf="7001", pedido="mensagem")
    # A segunda nota do mesmo pedido (o item já está no Bling pela primeira).
    g2 = Garantia(
        origem="robo",
        pedido_bling="300301",
        nf_numero="7002",
        cliente_nome="Cliente",
        cpf=_cpf(),
        itens=[{"sku": "dg053.sp", "descricao": "A17", "quantidade": 1, "uranyx": True}],
        data_inicio=date(2026, 10, 5),
        entrega_origem="shopee",
        solicitada_em=datetime(2026, 10, 12, 15, tzinfo=UTC),
        solicitacao_origem="mensagem",
    )
    g2.fim_hardware, g2.fim_software = svc.calcular_prazos(date(2026, 10, 5))
    db.add(g2)
    await db.commit()
    await _devolucao(db, "300301", "dg053.sp", "Extraviado", custo=600, em=_em(3))
    await _reembolso(db, "300301", "Extraviado", prejuizo=600, em=_em(3))
    [a17] = (await client.post("/api/garantias/resumo", json={})).json()["produtos"]
    assert (a17["vendidas"], a17["pediram"], a17["perdidos"], a17["gasto"], a17["seguiram"]) == (
        2,
        2,
        1,
        600.0,
        1,
    )
    assert a17["saldo"] == 1000.0
    assert a17["pedidos_por_coluna"]["perdidos"] == [g1.id]
    assert a17["pedidos_por_coluna"]["gasto"] == [g1.id]


def test_caso_da_plataforma_que_seguiu():
    """Reclamação e disputa sem decisão não; devolução aceita (o aparelho
    voltando) e reembolso pago sim; o caso trocado por outro não vale."""
    caso = resumo._caso_seguiu
    assert caso("shopee", "devolucao", "PROCESSING", {}) == "a_caminho"
    assert caso("shopee", "devolucao", "ACCEPTED", {}) == "reembolso"
    assert caso("shopee", "devolucao", "REQUESTED", {}) is None
    assert caso("shopee", "devolucao", "CANCELLED", {}) is None
    assert caso("shopee", "reclamacao", "JUDGING", {}) is None
    assert caso("tiktok", "devolucao", "AWAITING_BUYER_SHIP", {}) == "a_caminho"
    assert caso("tiktok", "devolucao", "RETURN_OR_REFUND_REQUEST_COMPLETE", {}) == "reembolso"
    assert caso("tiktok", "devolucao", "RETURN_OR_REFUND_REQUEST_CANCEL", {}) is None
    assert caso("tiktok", "reclamacao", "REJECT_RECEIVE_PACKAGE", {}) is None
    ganhou = {"resolucao": {"beneficiado": ["complainant"]}}
    assert caso("ml", "mediacao", "closed", ganhou) == "reembolso"
    assert caso("ml", "mediacao", "closed", {"resolucao": {"beneficiado": ["respondent"]}}) is None
    assert caso("ml", "reclamacao", "opened", {}) is None
    assert caso("ml", "devolucao", "opened", {}) == "a_caminho"


async def test_seguiram_fora_da_tela_devolucoes(client, db, make_user, auth_as):
    """30/10/2026 ("se pediram pelo chat e ocasionou em problema, reembolso
    etc. também entra; reclamação não"): pediu e foi adiante na plataforma ou
    no Reembolso — sem a devolução lançada — também é "Seguiram"."""
    dono = await make_user(role=UserRole.ADMIN)
    auth_as(dono)
    await _tabela(db, dono, ("dg052,dg053,dg054", "uranyx A17 pro max 12.128"))
    gs = {}
    for n in range(1, 10):
        g = await _garantia(db, f"30040{n}", "dg053.sp", "A17", "Celular", pedido="mensagem")
        g.pedido_marketplace = f"MKT30040{n}"
        gs[n] = g
    await db.commit()

    def caso(n, plataforma, tipo, status, **dados):
        db.add(
            AtendimentoReclamacao(
                plataforma=plataforma,
                externo_id=f"caso-{n}",
                tipo=tipo,
                status=status,
                pedido_marketplace=dados.pop("pedido_mkt", f"MKT30040{n}"),
                created_at=datetime(2026, 10, 10, tzinfo=UTC),
                dados=dados,
            )
        )

    # 1: reembolso ao cliente lançado no Reembolso; 2: frete (Logistica) não.
    db.add(Refund(pedido_bling="300401", conta="Loja X", tipo="Cliente", reembolso=-30))
    db.add(Refund(pedido_bling="300402", conta="Loja X", tipo="Logistica", reembolso=12))
    caso(3, "shopee", "devolucao", "PROCESSING")  # aceita, o aparelho voltando
    caso(4, "shopee", "reclamacao", "JUDGING")  # disputa sem decisão: não
    caso(5, "shopee", "devolucao", "CANCELLED")  # cancelada: não
    # 6: o caso antigo (a caminho) foi trocado por um novo, cancelado: vale o novo.
    caso(6, "shopee", "devolucao", "PROCESSING", substituida_por="caso-6b")
    db.add(
        AtendimentoReclamacao(
            plataforma="shopee",
            externo_id="caso-6b",
            tipo="devolucao",
            status="CANCELLED",
            pedido_marketplace="MKT300406",
            aberta_em=datetime(2026, 10, 18, tzinfo=UTC),
            created_at=datetime(2026, 10, 18, tzinfo=UTC),
            dados={},
        )
    )
    caso(7, "tiktok", "devolucao", "AWAITING_BUYER_SHIP")
    # 8: ML ligado pelo pack (o pedido da garantia), decidido a favor do comprador.
    caso(
        8,
        "ml",
        "mediacao",
        "closed",
        pedido_mkt="2000999",
        pack_id="MKT300408",
        resolucao={"beneficiado": ["complainant"]},
    )
    caso(9, "ml", "reclamacao", "opened")  # reclamação: não
    await db.commit()

    h = (await client.post("/api/garantias/resumo", json={})).json()
    [a17] = h["produtos"]
    assert (a17["pediram"], a17["seguiram"]) == (9, 4)
    assert h["seguiram"] == 4
    # O reembolso ao cliente da tela Reembolso é gasto; o pago pela plataforma não.
    assert (a17["gasto_cliente"], a17["pedidos_por_coluna"]["gasto"]) == (30.0, [gs[1].id])
    # Na lista, o selo diz o caminho.
    lista = (await client.post("/api/garantias/lista", json={})).json()
    selo = {i["pedido_bling"]: i["devolucao_rotulo"] for i in lista["itens"]}
    assert selo["300401"] == "Reembolso ao cliente · R$ 30,00"
    assert selo["300403"] == "Devolução a caminho"
    assert selo["300407"] == "Devolução a caminho"
    assert selo["300408"] == "Reembolso ao cliente"
    assert all(selo[f"30040{n}"] is None for n in (2, 4, 5, 6, 9))


def test_casos_do_pedido_reembolso_pago_conta_sempre_e_a_caminho_pelo_mais_novo():
    """Revisão 30/10: a Shopee marcou dois casos do mesmo pedido um pelo outro
    (`substituida_por` em ciclo) — o reembolso pago não pode sumir; e o "a
    caminho" vale só no caso mais novo (o antigo fica parado)."""
    from types import SimpleNamespace as C

    def c(i, status, dia, plataforma="shopee", tipo="devolucao", **dados):
        return C(
            id=i,
            plataforma=plataforma,
            tipo=tipo,
            status=status,
            dados=dados,
            aberta_em=datetime(2026, 10, dia, tzinfo=UTC),
            created_at=datetime(2026, 10, dia, tzinfo=UTC),
        )

    ciclo = [c(1, "PROCESSING", 14, substituida_por="2"), c(2, "ACCEPTED", 17, substituida_por="1")]
    assert resumo._casos_do_pedido(ciclo) == "reembolso"
    # O reembolso antigo conta mesmo com um caso novo cancelado depois.
    assert resumo._casos_do_pedido([c(1, "ACCEPTED", 10), c(2, "CANCELLED", 20)]) == "reembolso"
    # O "a caminho" antigo, parado, não vale se o novo foi cancelado.
    assert resumo._casos_do_pedido([c(1, "PROCESSING", 10), c(2, "CANCELLED", 20)]) is None
    novo = c(2, "PROCESSING", 20)
    assert resumo._decisao_dos_casos([c(1, "CANCELLED", 10), novo]) == ("a_caminho", novo)
    # A disputa sozinha não conta; o ML vale caso a caso.
    assert resumo._casos_do_pedido([c(1, "JUDGING", 12, tipo="reclamacao")]) is None
    ml = [
        c(1, "opened", 12, plataforma="ml"),
        c(2, "opened", 9, plataforma="ml", tipo="reclamacao"),
    ]
    assert resumo._casos_do_pedido(ml) == "a_caminho"


async def test_mapa_das_linhas_guardado_e_refeito_quando_muda(db, make_user):
    """O mapa garantia → linha → departamento fica guardado (lê todas as
    garantias) e é refeito na hora quando entra garantia nova ou muda a
    Tabela de Preços."""
    dono = await make_user(role=UserRole.ADMIN)
    g1 = await _garantia(db, "300501", "dg053.sp", "A17", "Celular")
    m1 = await resumo.mapa_das_linhas(db)
    assert m1.linha_da_garantia == {g1.id: "sku:dg053"}
    assert await resumo.mapa_das_linhas(db) is m1
    # A linha entra na Tabela de Preços (raiz Eletro, de propósito): refaz.
    await _tabela(db, dono, ("dg053", "A17"), segmento=await _raiz(db, "eletro"))
    m2 = await resumo.mapa_das_linhas(db)
    assert m2 is not m1
    [chave] = set(m2.linha_da_garantia.values())
    assert chave.startswith("tabela:") and m2.departamento_da_linha[chave] == "eletro"
    g2 = await _garantia(db, "300502", "b021", "Mala", None)
    m3 = await resumo.mapa_das_linhas(db)
    assert m3.departamento_do_id(g2.id) == "mala"
    assert sorted(m3.ids_do_departamento("eletro")) == [g1.id]
    assert m3.ids_da_linha("sku:b021") == [g2.id]
    # Guardado, mas por pouco tempo.
    resumo._cache["em"] -= resumo.CACHE_SEGUNDOS + 1
    assert await resumo.mapa_das_linhas(db) is not m3
