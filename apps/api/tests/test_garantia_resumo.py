"""Resumo do Painel de Garantia (28/10/2026): a garantia POR PRODUTO — uma
linha por produto da Tabela de Preços (o modelo, com as cores juntas), as
vendas com garantia e os PEDIDOS de garantia (a porcentagem da tela é pediram ÷
vendidas), e o total por departamento (Celular, Eletro, Acessórios) pela
categoria do item no pedido do Bling — a mesma do Faturamento."""

import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select

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
    # O número da célula abre só os pedidos dele (30/10/2026): os ids por
    # coluna da tela — o "Outros" junta atendimento, cadastro à mão e equipe.
    assert a17["pedidos_por_coluna"] == {"ativa": a17["pedidos"], "mensagem": a17["pedidos"]}
    assert t8["pedidos_por_coluna"] == {"aguardando": [g3.id], "sac": [g3.id]}
    assert ar["pedidos_por_coluna"] == {
        "ativa": [g4.id],
        "outros": [g4.id],
        "atendimentos": [g4.id],
    }
    # O número da célula é o tamanho da lista que ela abre.
    for p in h["produtos"]:
        col = {k: len(v) for k, v in p["pedidos_por_coluna"].items()}
        o, st = p["origens"], p["status"]
        assert col.get("mensagem", 0) == o["mensagem"]
        assert col.get("sac", 0) == o["sac"]
        assert col.get("devolucao", 0) == o["devolucao"]
        assert col.get("outros", 0) == o["atendimento"] + o["manual"] + o["pessoa"]
        assert col.get("ativa", 0) == st["ativa"]
        assert col.get("aguardando", 0) == st["aguardando_entrega"] + st["entregue_sem_data"]
        assert col.get("atendimentos", 0) == p["com_atendimento"]

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
    # Na tela os dois são o "Aguardando": a célula abre os dois.
    assert sorted(p["pedidos_por_coluna"]["aguardando"]) == sorted(p["pedidos"])


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
):
    db.add(
        Devolution(
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
    )
    await db.commit()


async def test_manutencao_e_perdas_dos_que_pediram(client, db, make_user, auth_as):
    """30/10/2026 ("em garantias precisamos do que realmente foi perdido e o
    que está na manutenção — o que foi gasto de dinheiro"): dos que PEDIRAM, o
    aparelho da garantia na tela Devoluções. O fone do kit no mesmo pedido
    não conta; o conserto de R$ 1 (para tirar da Manutenção) não é gasto."""
    dono = await make_user(role=UserRole.ADMIN)
    auth_as(dono)
    await _tabela(db, dono, ("dg052,dg053,dg054", "uranyx A17 pro max 12.128"))
    # No técnico, com o prazo de 30 dias vencido; o fone do kit, Extraviado, fica de fora.
    g1 = await _garantia(
        db, "300101", "dg053.sp+a001.sp", "A17 + Fone", "Celular Kit", pedido="mensagem"
    )
    await _devolucao(
        db,
        "300101",
        "dg053.sp",
        "Manutenção",
        custo=600,
        prazo=datetime(2026, 9, 30, 15, tzinfo=UTC),
    )
    await _devolucao(db, "300101", "a001.sp", "Extraviado", custo=4.5)
    # Consertados: um com o conserto de verdade, outro só com o R$ 1.
    g2 = await _garantia(db, "300102", "dg052.ci", "A17 Preto", "Celular", pedido="devolucao")
    await _devolucao(db, "300102", "dg052.ci", "Usado", manutencao=True, conserto=229)
    g3 = await _garantia(db, "300103", "dg054.pi", "A17 Branco", "Celular", pedido="devolucao")
    await _devolucao(db, "300103", "dg054.pi", "Novo", manutencao=True, conserto=1)
    # Perdido pelo destino Sucata do modal (a condição continua Manutenção).
    g4 = await _garantia(db, "300104", "dg053.ci", "A17 Laranja", "Celular", pedido="mensagem")
    await _devolucao(
        db, "300104", "dg053.ci", "Manutenção", destino="Sucata", custo=560, conserto=80
    )
    # Voltou Novo sem manutenção: só "Seguiram". A venda SEM pedido com o
    # aparelho perdido: fora. "Não devolvido" (o cliente ficou com ele) e o
    # pedido só no chat (sem devolução): pediram, mas não seguiram.
    g5 = await _garantia(db, "300105", "dg053.sp", "A17", "Celular", pedido="mensagem")
    await _devolucao(db, "300105", "dg053.sp", "Novo")
    await _garantia(db, "300106", "dg053.sp", "A17", "Celular")
    await _devolucao(db, "300106", "dg053.sp", "Extraviado", custo=600)
    await _garantia(db, "300107", "dg053.ci", "A17", "Celular", pedido="mensagem")
    await _devolucao(db, "300107", "dg053.ci", "Não devolvido")
    await _garantia(db, "300108", "dg053.pi", "A17", "Celular", pedido="mensagem")

    h = (await client.post("/api/garantias/resumo", json={})).json()
    assert h["ve_valores"] is True
    [a17] = h["produtos"]
    # A % certa é dos que DEVOLVERAM (30/10/2026): 5 dos 7 que pediram.
    assert (a17["vendidas"], a17["pediram"], a17["seguiram"]) == (8, 7, 5)
    assert (h["seguiram"], h["departamentos"][0]["seguiram"]) == (5, 5)
    seguiram = [g1.id, g2.id, g3.id, g4.id, g5.id]
    assert sorted(a17["pedidos_por_coluna"]["seguiram"]) == sorted(seguiram)
    assert (a17["no_tecnico"], a17["no_tecnico_vencidos"]) == (1, 1)
    assert (a17["consertados"], a17["consertados_sem_custo"]) == (2, 1)
    assert a17["perdidos"] == 1
    # Gasto = o conserto de verdade (229 + 80; o R$ 1 não) + o aparelho perdido (560).
    assert (a17["gasto"], a17["gasto_conserto"], a17["gasto_perdidos"]) == (869.0, 309.0, 560.0)
    assert a17["valor_no_tecnico"] == 600.0
    col = a17["pedidos_por_coluna"]
    assert col["no_tecnico"] == [g1.id]
    assert sorted(col["consertados"]) == sorted([g2.id, g3.id])
    assert col["perdidos"] == [g4.id]
    assert sorted(col["gasto"]) == sorted([g2.id, g4.id])
    assert all(
        g5.id not in v
        for k, v in col.items()
        if k in ("no_tecnico", "consertados", "perdidos", "gasto")
    )

    # Na lista, o selo do aparelho — com os reais para o admin.
    corpo = {"ids": [g1.id, g2.id, g3.id, g4.id, g5.id]}
    itens = (await client.post("/api/garantias/lista", json=corpo)).json()["itens"]
    selo = {
        i["pedido_bling"]: (i["devolucao_situacao"], i["devolucao_rotulo"], i["devolucao_gasto"])
        for i in itens
    }
    assert selo["300101"] == ("no_tecnico", "No técnico · vencido há 20 dias", 0.0)
    assert selo["300102"] == ("consertado", "Consertado (Usado) · R$ 229,00", 229.0)
    assert selo["300103"] == ("consertado", "Consertado (Novo) · sem custo lançado", 0.0)
    assert selo["300104"] == ("perdido", "Perdido (Sucata) · R$ 640,00", 640.0)
    assert selo["300105"] == ("devolvido", "Devolvido (Novo)", 0.0)
    vencido = {i["pedido_bling"]: i["devolucao_vencido"] for i in itens}
    assert vencido == {
        "300101": True,
        "300102": False,
        "300103": False,
        "300104": False,
        "300105": False,
    }

    # "Não devolvido" não tem selo; o topo do painel também conta os que seguiram.
    lista = (await client.post("/api/garantias/lista", json={})).json()
    assert {i["pedido_bling"]: i["devolucao_situacao"] for i in lista["itens"]}["300107"] is None
    assert (lista["indicadores"]["total"], lista["indicadores"]["seguiram"]) == (7, 5)

    # Quem não é admin vê as quantidades e o selo, sem os reais.
    auth_as(await make_user(permissions={"garantias": {"view": True}}))
    h = (await client.post("/api/garantias/resumo", json={})).json()
    assert h["ve_valores"] is False
    [a17] = h["produtos"]
    assert (a17["no_tecnico"], a17["consertados"], a17["perdidos"]) == (1, 2, 1)
    assert a17["gasto"] is None and a17["valor_no_tecnico"] is None
    assert "gasto" not in a17["pedidos_por_coluna"]
    itens = (await client.post("/api/garantias/lista", json=corpo)).json()["itens"]
    selo = {i["pedido_bling"]: (i["devolucao_rotulo"], i["devolucao_gasto"]) for i in itens}
    assert selo["300102"] == ("Consertado (Usado)", None)
    assert selo["300104"] == ("Perdido (Sucata)", None)


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
    # O conserto lançado numa linha antiga (sem "passou em manutenção") é conserto.
    gasto = resumo.Desfecho(conserto=1234.5)
    assert resumo.rotulo_do_desfecho(gasto, HOJE, True) == "Conserto · R$ 1.234,50"
    assert resumo.rotulo_do_desfecho(gasto, HOJE, False) is None


def _linha_dev(
    condicao,
    sku="dg053.sp",
    *,
    manutencao=False,
    destino=None,
    custo=600.0,
    conserto=None,
    prazo=None,
):
    return SimpleNamespace(
        sku=sku,
        condicao_produto=condicao,
        manutencao=manutencao,
        manutencao_destino=destino,
        custo_produto=custo,
        custo_manutencao=conserto,
        prazo=prazo,
    )


def test_o_prazo_vence_pelo_dia_em_sao_paulo_e_so_de_quem_esta_no_tecnico():
    """Revisão 30/10: o vencido é pelo dia em São Paulo (como o selo e a tela
    Devoluções — no dia do prazo é "vence hoje"); e a garantia em Perdidos
    não entra no "vencido" nem nos reais do No técnico."""
    tabela = {"dg053": ("1", "A17", ["dg053"])}
    # Prazo hoje às 08:00 em São Paulo (11:00 UTC): ainda não venceu.
    hoje_cedo = datetime(2026, 10, 30, 11, tzinfo=UTC)
    f = resumo.desfecho(
        [_linha_dev("Manutenção", prazo=hoje_cedo)], "tabela:1", tabela, date(2026, 10, 30)
    )
    assert (f.situacao, f.vencido, f.no_tecnico) == ("no_tecnico", False, 600.0)
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
    # garantia é Perdidos — sem vencido e sem os reais do técnico.
    ontem = datetime(2026, 10, 1, 15, tzinfo=UTC)
    f = resumo.desfecho(
        [_linha_dev("Extraviado", custo=560), _linha_dev("Manutenção", prazo=ontem)],
        "tabela:1",
        tabela,
        date(2026, 10, 30),
    )
    assert (f.situacao, f.condicao, f.vencido, f.no_tecnico, f.prazo, f.gasto) == (
        "perdido",
        "Extraviado",
        False,
        0.0,
        None,
        560.0,
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
    f = resumo.desfecho([_linha_dev(None)], "tabela:1", tabela, hoje)
    assert (f.situacao, f.condicao, f.devolveu) == ("devolvido", None, True)
    f = resumo.desfecho([_linha_dev("Extraviado", custo=560)], "tabela:1", tabela, hoje)
    assert (f.situacao, f.devolveu, f.seguiu) == ("perdido", True, True)
    # Seguiu fora da tela Devoluções (a plataforma ou o Reembolso): sem selo
    # do aparelho, o selo é o do caminho.
    f = resumo.desfecho([], "tabela:1", tabela, hoje, "a_caminho")
    assert (f.situacao, f.devolveu, f.seguiu) == ("a_caminho", False, True)
    f = resumo.desfecho([_linha_dev("Não devolvido")], "tabela:1", tabela, hoje, "reembolso")
    assert (f.situacao, f.devolveu, f.seguiu) == ("reembolso", False, True)
    f = resumo.desfecho([_linha_dev("Manutenção")], "tabela:1", tabela, hoje, "reembolso")
    assert (f.situacao, f.seguiu) == ("no_tecnico", True)


async def test_duas_garantias_do_mesmo_pedido_contam_o_aparelho_uma_vez(
    client, db, make_user, auth_as
):
    """Revisão 30/10: o mesmo pedido com duas notas (uma garantia por nota)
    olha as mesmas devoluções — o aparelho perdido conta uma vez só."""
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
    await _devolucao(db, "300301", "dg053.sp", "Extraviado", custo=600)
    [a17] = (await client.post("/api/garantias/resumo", json={})).json()["produtos"]
    assert (a17["pediram"], a17["perdidos"], a17["gasto"], a17["seguiram"]) == (2, 1, 600.0, 1)
    assert a17["pedidos_por_coluna"]["perdidos"] == [g1.id]


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
    seguiram = sorted(gs[n].id for n in (1, 3, 7, 8))
    assert (a17["pediram"], a17["seguiram"]) == (9, 4)
    assert sorted(a17["pedidos_por_coluna"]["seguiram"]) == seguiram
    assert h["seguiram"] == 4
    # Na lista, o selo diz o caminho; e o topo conta igual.
    lista = (await client.post("/api/garantias/lista", json={})).json()
    selo = {i["pedido_bling"]: i["devolucao_rotulo"] for i in lista["itens"]}
    assert selo["300401"] == "Reembolso ao cliente"
    assert selo["300403"] == "Devolução a caminho"
    assert selo["300407"] == "Devolução a caminho"
    assert selo["300408"] == "Reembolso ao cliente"
    assert all(selo[f"30040{n}"] is None for n in (2, 4, 5, 6, 9))
    assert lista["indicadores"]["seguiram"] == 4


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
    assert resumo._casos_do_pedido([c(1, "CANCELLED", 10), c(2, "PROCESSING", 20)]) == "a_caminho"
    # A disputa sozinha não conta; o ML vale caso a caso.
    assert resumo._casos_do_pedido([c(1, "JUDGING", 12, tipo="reclamacao")]) is None
    ml = [
        c(1, "opened", 12, plataforma="ml"),
        c(2, "opened", 9, plataforma="ml", tipo="reclamacao"),
    ]
    assert resumo._casos_do_pedido(ml) == "a_caminho"
