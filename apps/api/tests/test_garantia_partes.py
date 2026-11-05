"""As 3 partes do Painel de Garantia (04/11/2026): "resumo - visão do produto;
garantia - todas as garantias; atendimento - só o que realmente está em
atendimento no momento", com as pílulas Celular, Mala e Eletro em cima.

Aqui a lista: o filtro por departamento (a linha do produto) e por produto (a
linha do Resumo), as contagens das pílulas e das abas, quem está em
atendimento agora (cada motivo e quem já terminou), o pedido que a busca achou
sem garantia (e por quê) e a busca pelo pack do Mercado Livre."""

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import delete

from app.models import BlingOrder, Garantia, GarantiaAtendimento, UserRole
from app.models.atendimento import (
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoPedidoComprador,
    AtendimentoReclamacao,
)
from app.models.devolution import Devolution
from app.models.enums import IntegrationPlatform
from app.models.garantia import GarantiaConversa, GarantiaPendencia
from app.models.marketplace_financial import (
    MarketplaceOrderFinancial,
    MarketplaceOrderFreightReconciliation,
)
from app.models.nf import NfNota
from app.models.pricing import PricingProduct
from app.models.refund import Refund
from app.models.segment import Segment
from app.routers import garantias as rota
from app.services import garantia as svc
from app.services import garantia_atendimento as atend
from app.services import garantia_auto
from app.services import garantia_resumo as resumo
from app.services.atendimento import escopo
from tests.conftest import _make_user
from tests.lojas_da_pessoa import dar_lojas
from tests.test_garantia_resumo import _cpf
from tests.test_garantias_router import _loja_padrao

pytestmark = pytest.mark.asyncio

HOJE = date(2026, 11, 4)
CADASTRA = {"garantias": {"view": True, "edit": True}}
CONSULTA = {"garantias": {"view": True}}


@pytest.fixture(autouse=True)
def _hoje(monkeypatch):
    monkeypatch.setattr(svc, "hoje", lambda: HOJE)


@pytest.fixture(autouse=True)
def _mapa_novo():
    resumo.limpar_cache()
    yield
    resumo.limpar_cache()


@pytest.fixture
def make_user(db):
    """Quem não é admin vem com a loja "L1" dos pedidos daqui."""

    async def _f(**kwargs):
        u = await _make_user(db, **kwargs)
        if u.role != UserRole.ADMIN:
            integ, ficha = await _loja_padrao(db)
            await dar_lojas(db, u, integ, escopo.chave(escopo.FICHA, ficha.id))
        return u

    return _f


@pytest.fixture
async def pack_do_ml(db):
    """O pack do Mercado Livre → o pedido (a tabela da conciliação de frete não
    é limpa entre os testes: o que entra aqui sai no fim)."""
    criados = []

    async def _criar(pack: str, pedido: str) -> None:
        fin = MarketplaceOrderFinancial(platform=IntegrationPlatform.ML, external_order_id=pedido)
        db.add(fin)
        await db.flush()
        db.add(
            MarketplaceOrderFreightReconciliation(
                order_financial_id=fin.id,
                platform=IntegrationPlatform.ML,
                external_order_id=pedido,
                item_index=0,
                pack_id=pack,
            )
        )
        await db.commit()
        criados.append(fin.id)

    yield _criar
    if criados:
        r = MarketplaceOrderFreightReconciliation
        await db.execute(delete(r).where(r.order_financial_id.in_(criados)))
        await db.execute(
            delete(MarketplaceOrderFinancial).where(MarketplaceOrderFinancial.id.in_(criados))
        )
        await db.commit()


def _em(mes: int, dia: int, hora: int = 12) -> datetime:
    return datetime(2026, mes, dia, hora, tzinfo=UTC)


async def _pedido_bling(
    db,
    numero,
    sku="dg053.sp",
    *,
    loja="L1",
    mkt=None,
    compra=None,
    situacao="15",
    total=1000,
    nome="Fulano de Tal",
    documento="52998224725",
):
    db.add(
        BlingOrder(
            numero=numero,
            numeroloja=mkt if mkt is not None else f"MKT{numero}",
            loja=loja,
            data=compra or _em(10, 1, 15),
            total=total,
            valorbase=total,
            situacao=situacao,
            item_index=0,
            item_codigo=sku,
            item_descricao="Produto",
            item_quantidade=1,
            itemvalor=total,
            nome_destinatario=nome,
            documento_destinatario=documento,
        )
    )
    await db.commit()


async def _venda(
    db, numero, sku="dg053.sp", *, loja="L1", pediu=True, descartada=False, compra=None
):
    """A venda com garantia (o pedido no Bling e a garantia do robô)."""
    await _pedido_bling(db, numero, sku, loja=loja, compra=compra)
    g = Garantia(
        origem="robo",
        pedido_bling=numero,
        pedido_marketplace=f"MKT{numero}",
        loja=loja,
        plataforma="shopee",
        conta="loja teste",
        nf_numero="9" + numero[-4:],
        cliente_nome="Cliente",
        cpf=_cpf(),
        itens=[{"sku": sku, "descricao": "Produto", "quantidade": 1, "uranyx": True}],
        data_inicio=date(2026, 10, 5),
        entrega_origem="shopee",
    )
    g.fim_hardware, g.fim_software = svc.calcular_prazos(date(2026, 10, 5))
    if pediu:
        g.solicitada_em = _em(10, 12, 15)
        g.solicitacao_origem = "mensagem"
    if descartada:
        g.descartada_em = _em(10, 20)
    db.add(g)
    await db.commit()
    return g


async def _tabela(db, dono) -> dict[str, str]:
    """As linhas da Tabela de Preços, uma por departamento (a raiz do segmento)
    → a chave de cada linha no Resumo."""
    chaves = {}
    for raiz, skus, nome in (
        ("celular", "dg052,dg053,dg054", "uranyx A17"),
        ("eletro", "u015", "airfryer 4L"),
        ("mala", "b021", "mala de bordo"),
    ):
        seg = Segment(name=raiz.title(), slug=raiz)
        db.add(seg)
        await db.flush()
        p = PricingProduct(user_id=dono.id, sku=skus, name=nome, segment_id=seg.id)
        db.add(p)
        await db.flush()
        chaves[raiz] = f"tabela:{p.id}"
    await db.commit()
    return chaves


async def _devolucao(db, pedido, condicao, *, sku="dg053.sp", manutencao=False, prazo=None, em):
    d = Devolution(
        pedido_bling=pedido,
        conta="Loja X",
        sku=sku,
        condicao_produto=condicao,
        manutencao=manutencao,
        custo_produto=600,
        prazo=prazo,
    )
    d.created_at = em
    db.add(d)
    await db.commit()


async def _caso(db, plataforma, tipo, status, em, *, pedido_mkt, encerrada=None, **dados):
    db.add(
        AtendimentoReclamacao(
            plataforma=plataforma,
            externo_id=f"caso-{uuid.uuid4().hex[:8]}",
            tipo=tipo,
            status=status,
            pedido_marketplace=pedido_mkt,
            created_at=em,
            encerrada_em=encerrada,
            dados=dados,
        )
    )
    await db.commit()


async def _conversa(
    db, g, *, aguardando=True, cliente_em, plataforma="ml", canal="pos_venda", como="pedido", **kw
):
    c = AtendimentoConversa(
        plataforma=plataforma,
        canal=canal,
        conta="loja teste",
        externo_id=f"conv-{uuid.uuid4().hex[:8]}",
        pedido_marketplace=g.pedido_marketplace,
        aguardando_resposta=aguardando,
        ultima_do_cliente_em=cliente_em,
        ultima_mensagem_em=cliente_em,
        **kw,
    )
    db.add(c)
    await db.flush()
    db.add(GarantiaConversa(garantia_id=g.id, conversa_id=c.id, como=como))
    await db.commit()
    return c


async def _pedido_por_mensagem(db, g, conversa, pediu_em, *, respondida_em=None, nota_em=None):
    """O pedido de garantia por mensagem (`solicitacao_ref` = a mensagem do
    pedido) e, se houver, a resposta da loja (ou só uma nota interna)."""
    pedido = AtendimentoMensagem(
        conversa_id=conversa.id,
        autor="cliente",
        origem="cliente",
        texto="parou",
        enviada_em=pediu_em,
    )
    db.add(pedido)
    if respondida_em is not None:
        db.add(
            AtendimentoMensagem(
                conversa_id=conversa.id,
                autor="loja",
                origem="davinci_humano",
                texto="vamos ver",
                enviada_em=respondida_em,
            )
        )
    if nota_em is not None:
        db.add(
            AtendimentoMensagem(
                conversa_id=conversa.id,
                autor="loja",
                origem="davinci_nota",
                tipo="nota",
                texto="nota interna",
                enviada_em=nota_em,
            )
        )
    await db.flush()
    g.solicitada_em = pediu_em
    g.solicitacao_origem = "mensagem"
    g.solicitacao_ref = str(pedido.id)
    await db.commit()


async def _lista(client, **corpo):
    r = await client.post("/api/garantias/lista", json=corpo)
    assert r.status_code == 200, r.text
    return r.json()


async def test_departamento_produto_e_as_abas(client, db, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    chave = await _tabela(db, admin)
    c1 = await _venda(db, "400001", "dg053.sp")
    c2 = await _venda(db, "400002", "dg054.ci", pediu=False)
    # Fora da Tabela de Preços: pela família do SKU (dg = celular).
    c3 = await _venda(db, "400003", "dg101.pi")
    await _devolucao(db, "400003", "Manutenção", sku="dg101.pi", prazo=_em(11, 20), em=_em(10, 28))
    e1 = await _venda(db, "400004", "u015")
    m1 = await _venda(db, "400005", "b021", pediu=False)
    x1 = await _venda(db, "400006", "dg053.ci", loja="L2")
    await _venda(db, "400007", "dg053.sp", descartada=True)

    def ids(h):
        return sorted(i["id"] for i in h["itens"])

    h = await _lista(client, departamento="celular", mostrar="todas")
    assert ids(h) == sorted([c1.id, c2.id, c3.id, x1.id]) and h["total"] == 4
    assert {i["departamento"] for i in h["itens"]} == {"celular"}
    # O padrão continua sendo só quem pediu.
    assert ids(await _lista(client, departamento="celular")) == sorted([c1.id, c3.id, x1.id])
    h = await _lista(client, departamento="eletro", mostrar="todas")
    assert ids(h) == [e1.id] and h["itens"][0]["departamento"] == "eletro"
    assert ids(await _lista(client, departamento="mala", mostrar="todas")) == [m1.id]
    # A linha do Resumo: todas as garantias daquele produto.
    h = await _lista(client, produto=chave["celular"], mostrar="todas")
    assert ids(h) == sorted([c1.id, c2.id, x1.id])
    assert ids(await _lista(client, produto="sku:dg101", mostrar="todas")) == [c3.id]
    assert (await _lista(client, produto="sku:nada", mostrar="todas"))["itens"] == []
    # A busca dentro do departamento.
    assert ids(await _lista(client, departamento="celular", busca="400001")) == [c1.id]
    assert (await _lista(client, departamento="eletro", busca="400001"))["itens"] == []
    # O selo de quem está em atendimento agora vem na linha.
    por_id = {i["id"]: i for i in (await _lista(client, departamento="celular"))["itens"]}
    assert por_id[c3.id]["atendimento"]["motivo"] == "no_tecnico"
    assert por_id[c1.id]["atendimento"] is None

    # As pílulas e as abas: o que a pessoa enxerga, sem os filtros da barra.
    abas = {
        "celular": {"garantias": 4, "atendimento": 1},
        "mala": {"garantias": 1, "atendimento": 0},
        "eletro": {"garantias": 1, "atendimento": 0},
    }
    assert h["abas"] == abas
    h = await _lista(client, status="expirada", departamento="celular")
    assert (h["itens"], h["abas"]) == ([], abas)
    # Fora das três: recusa.
    r = await client.post("/api/garantias/lista", json={"departamento": "acessorios"})
    assert r.status_code == 422

    # Os cartões e a linha embaixo deles (revisão 04/11): só o departamento da
    # pílula, sem os filtros da barra — batem com o "Garantia (N)" da aba e
    # com a Tabela que o cartão filtra. Sem pílula, tudo o que a pessoa vê.
    # (O atendimento do mês é de uma garantia de Eletro.)
    db.add(
        GarantiaAtendimento(
            garantia_id=e1.id,
            data_atendimento=_em(11, 2),
            atendente_id=admin.id,
            atendente_nome="Admin",
            conversa_id=uuid.uuid4(),
            conversa_link="/atendimento",
            resumo="não liga",
            tipo_problema="hardware",
            cobertura="coberto",
            solucao="troca",
        )
    )
    await db.commit()

    def cartoes(h):
        i = h["indicadores"]
        return (i["vendidas"], i["total"], i["ativas"], i["atendimentos_no_mes"])

    assert cartoes(await _lista(client, departamento="celular", mostrar="todas")) == (4, 3, 3, 0)
    assert cartoes(await _lista(client, departamento="celular", status="expirada")) == (4, 3, 3, 0)
    assert cartoes(await _lista(client, departamento="eletro")) == (1, 1, 1, 1)
    assert cartoes(await _lista(client, departamento="mala", mostrar="todas")) == (1, 0, 0, 0)
    assert cartoes(await _lista(client, mostrar="todas")) == (6, 4, 4, 1)
    h = await _lista(client, departamento="celular", status="ativa")
    assert h["total"] == h["indicadores"]["ativas"] == 3, "o cartão Ativas = os pedidos filtrados"

    # Quem não é admin conta só as lojas dele.
    auth_as(await make_user(permissions=CONSULTA))
    h = await _lista(client, departamento="celular", mostrar="todas")
    assert ids(h) == sorted([c1.id, c2.id, c3.id])
    assert h["abas"]["celular"] == {"garantias": 3, "atendimento": 1}
    assert cartoes(h) == (3, 2, 2, 0)


async def test_a_linha_do_resumo_abre_as_vendidas_dela(client, db, make_user, auth_as):
    """A linha do Resumo (ou Vendidas, Saldo, %) abre "todas as garantias do
    produto": as MESMAS da coluna Vendidas, também com o período pela data do
    pedido de garantia (as compradas até o fim do período, não só quem pediu
    nele — revisão 04/11). O número de uma coluna abre só os pedidos dela."""
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    chave = (await _tabela(db, admin))["celular"]
    v1 = await _venda(db, "400401", "dg053.sp")  # pediu em 12/10
    v2 = await _venda(db, "400402", "dg054.ci", pediu=False)
    v3 = await _venda(db, "400403", "dg052.pi", pediu=False)
    # Comprada depois do fim do período.
    v4 = await _venda(db, "400404", "dg053.ci", pediu=False, compra=_em(11, 2))
    # Outro produto, mesmo departamento (pediu em 12/10).
    v5 = await _venda(db, "400405", "dg101.pi")

    async def vendidas(**periodo):
        r = await client.post("/api/garantias/resumo", json=periodo)
        assert r.status_code == 200, r.text
        return {p["chave"]: p for p in r.json()["produtos"]}[chave]

    async def aberta(**periodo):
        # O corpo que a tela manda no clique da linha.
        corpo = {"ordem": "atividade", "mostrar": "todas", "departamento": "celular"}
        h = await _lista(client, **corpo, **periodo, produto=chave)
        return h["total"], sorted(i["id"] for i in h["itens"])

    outubro = {"periodo": "pedido", "de": "2026-10-01", "ate": "2026-10-31"}
    linha = await vendidas(**outubro)
    assert (linha["vendidas"], linha["pediram"]) == (3, 1)
    assert await aberta(**outubro) == (3, sorted([v1.id, v2.id, v3.id]))
    # Sem período, ou só com o início: todas as vendas do produto.
    assert (await vendidas())["vendidas"] == 4
    assert await aberta() == (4, sorted([v1.id, v2.id, v3.id, v4.id]))
    so_de = {"periodo": "pedido", "de": "2026-10-01"}
    assert (await vendidas(**so_de))["vendidas"] == 4 == (await aberta(**so_de))[0]
    # Pela entrega: a mesma faixa nos dois.
    entrega = {"periodo": "entrega", "de": "2026-10-01", "ate": "2026-10-31"}
    assert (await vendidas(**entrega))["vendidas"] == 4 == (await aberta(**entrega))[0]
    # O número de uma coluna (Mensagem): só os pedidos dela, com o período.
    ids = linha["pedidos_por_coluna"]["mensagem"]
    assert ids == [v1.id]
    h = await _lista(client, **outubro, ordem="atividade", mostrar="todas", ids=ids)
    assert [i["id"] for i in h["itens"]] == [v1.id]
    # A Tabela da aba Garantia (sem produto) continua: com o período pelo
    # pedido, só quem pediu no período.
    h = await _lista(client, **outubro, mostrar="todas", departamento="celular")
    assert sorted(i["id"] for i in h["itens"]) == sorted([v1.id, v5.id])


async def test_a_lista_le_o_desfecho_uma_vez_so(client, db, make_user, auth_as, monkeypatch):
    """O desfecho de quem pediu (a tela Devoluções, o Reembolso e os casos da
    plataforma) é lido uma vez por lista: o Atendimento lê e o selo do
    aparelho reaproveita; só a venda sem pedido da página é lida à parte."""
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    await _tabela(db, admin)
    c1 = await _venda(db, "400501", "dg053.sp")
    await _devolucao(db, "400501", "Manutenção", prazo=_em(11, 20), em=_em(10, 28))
    c2 = await _venda(db, "400502", "dg053.ci", pediu=False)
    await _devolucao(db, "400502", "Novo", sku="dg053.ci", em=_em(10, 29))
    lidas: list[list[int]] = []
    original = resumo.desfechos

    async def espiao(session, garantias, dia, **kw):
        lidas.append(sorted(g.id for g in garantias))
        return await original(session, garantias, dia, **kw)

    monkeypatch.setattr(resumo, "desfechos", espiao)
    h = await _lista(client, departamento="celular", mostrar="todas")
    assert lidas == [[c1.id], [c2.id]], "quem pediu uma vez; a venda sem pedido à parte"
    por_id = {i["id"]: i for i in h["itens"]}
    assert (por_id[c1.id]["devolucao_situacao"], por_id[c1.id]["atendimento"]["motivo"]) == (
        "no_tecnico",
        "no_tecnico",
    )
    assert por_id[c1.id]["devolucao_rotulo"].startswith("No técnico")
    assert (por_id[c2.id]["devolucao_situacao"], por_id[c2.id]["devolucao_rotulo"]) == (
        "devolvido",
        "Devolvido (Novo)",
    )
    assert por_id[c2.id]["atendimento"] is None
    # Só os pedidos (o padrão): uma leitura só.
    lidas.clear()
    h = await _lista(client, departamento="celular")
    assert lidas == [[c1.id]]
    assert [i["id"] for i in h["itens"]] == [c1.id]
    assert h["itens"][0]["devolucao_situacao"] == "no_tecnico"


async def test_em_atendimento_cada_motivo_e_quem_ja_terminou(client, db, make_user, auth_as):
    """Só quem pediu e tem um caso de garantia andando: no técnico (o vencido
    primeiro), chegou sem a condição, voltando, a plataforma decidindo, o
    pedido de garantia que a loja ainda não respondeu (mensagem ou SAC). Sai
    sozinho quando termina. A conversa comum esperando a loja não entra."""
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    await _tabela(db, admin)
    integ, _ficha = await _loja_padrao(db)
    g = {}
    for n in range(1, 21):
        numero = f"4001{n:02d}"
        g[n] = await _venda(
            db,
            numero,
            loja="L2" if n == 18 else "L1",
            pediu=n != 13,
            descartada=n == 14,
        )
    # 1: no técnico, prazo vencido; 2: no técnico, no prazo.
    await _devolucao(db, "400101", "Manutenção", prazo=_em(10, 20), em=_em(10, 10))
    await _devolucao(db, "400102", "Manutenção", prazo=_em(11, 20), em=_em(10, 25))
    # 3: chegou, sem a condição lançada.
    await _devolucao(db, "400103", None, em=_em(10, 28))
    # 4: devolução aceita na Shopee, o aparelho voltando.
    await _caso(db, "shopee", "devolucao", "PROCESSING", _em(10, 30), pedido_mkt="MKT400104")
    # 5: reclamação aberta no ML (ligada pelo pedido do Bling dos dados).
    await _caso(
        db,
        "ml",
        "reclamacao",
        "opened",
        _em(10, 29),
        pedido_mkt="2000000000400105",
        pedido_bling="400105",
    )
    # 6: o pedido de garantia por mensagem que a loja ainda não respondeu (a
    # nota interna não é resposta).
    c6 = await _conversa(db, g[6], cliente_em=_em(11, 2))
    await _pedido_por_mensagem(db, g[6], c6, _em(11, 2), nota_em=_em(11, 2, 14))
    # 7: no técnico + reclamação + pedido sem resposta: vale o 1º motivo.
    await _devolucao(db, "400107", "Manutenção", prazo=_em(11, 30), em=_em(10, 31))
    await _caso(db, "ml", "mediacao", "opened", _em(10, 20), pedido_mkt="MKT400107")
    c7 = await _conversa(db, g[7], cliente_em=_em(10, 15))
    await _pedido_por_mensagem(db, g[7], c7, _em(10, 15))
    # Terminaram: 8 consertado (mesmo com o pedido sem resposta), 9 perdido,
    # 10 devolvido com a condição, 11 reembolso ao cliente (mesmo com a
    # reclamação aberta).
    await _devolucao(db, "400108", "Usado", manutencao=True, em=_em(10, 20))
    c8 = await _conversa(db, g[8], cliente_em=_em(11, 3))
    await _pedido_por_mensagem(db, g[8], c8, _em(11, 3))
    await _devolucao(db, "400109", "Extraviado", em=_em(10, 20))
    await _devolucao(db, "400110", "Novo", em=_em(10, 20))
    db.add(Refund(pedido_bling="400111", conta="Loja X", tipo="Cliente", reembolso=-50))
    await db.commit()
    await _caso(db, "ml", "reclamacao", "opened", _em(10, 21), pedido_mkt="MKT400111")
    # 12: o pedido já respondido; 13: não pediu; 14: descartada; 16: reclamação
    # encerrada.
    c12 = await _conversa(db, g[12], aguardando=False, cliente_em=_em(11, 1))
    await _pedido_por_mensagem(db, g[12], c12, _em(11, 1), respondida_em=_em(11, 1, 15))
    await _devolucao(db, "400113", "Manutenção", em=_em(10, 20))
    await _devolucao(db, "400114", "Manutenção", em=_em(10, 20))
    await _caso(
        db,
        "ml",
        "reclamacao",
        "closed",
        _em(10, 22),
        pedido_mkt="MKT400116",
        encerrada=_em(10, 25),
    )
    # 15: a conversa comum esperando a loja (o cliente agradeceu depois do
    # pedido já respondido) NÃO é caso de garantia andando.
    comprador = "comprador-15"
    db.add(
        AtendimentoPedidoComprador(
            integration_id=integ.id,
            plataforma="shopee",
            comprador_id=comprador,
            pedido="OUTRO-15",
            criado_em=_em(11, 1),
        )
    )
    c15 = await _conversa(
        db,
        g[15],
        cliente_em=_em(11, 3),
        plataforma="shopee",
        canal="chat",
        como="comprador",
        comprador_id=comprador,
        integration_id=integ.id,
    )
    await _pedido_por_mensagem(db, g[15], c15, _em(10, 30), respondida_em=_em(10, 30, 16))
    # 17: o pedido no chat do comprador, sem resposta.
    c17 = await _conversa(db, g[17], cliente_em=_em(11, 2, 18), plataforma="shopee", canal="chat")
    await _pedido_por_mensagem(db, g[17], c17, _em(11, 2, 18))
    # 18: no técnico em outra loja (L2).
    await _devolucao(db, "400118", "Manutenção", prazo=_em(11, 30), em=_em(11, 3))
    # 19: no técnico com o cliente já reembolsado: o técnico ainda é da loja.
    await _devolucao(db, "400119", "Manutenção", prazo=_em(11, 30), em=_em(10, 26))
    db.add(Refund(pedido_bling="400119", conta="Loja X", tipo="Cliente", reembolso=-80))
    await db.commit()
    # 20: o SAC do site (o formulário do protocolo) sem resposta da loja.
    sac = AtendimentoConversa(
        plataforma="site",
        canal="email",
        conta="uranyx",
        externo_id="mail-protocolo:US-26-0099",
        ultima_do_cliente_em=_em(11, 3, 9),
        ultima_mensagem_em=_em(11, 3, 9),
    )
    db.add(sac)
    await db.flush()
    db.add(GarantiaConversa(garantia_id=g[20].id, conversa_id=sac.id, como="pedido"))
    db.add(
        AtendimentoMensagem(
            conversa_id=sac.id,
            autor="cliente",
            origem="cliente",
            texto="formulário",
            enviada_em=_em(11, 3, 9),
        )
    )
    g[20].solicitada_em = _em(11, 3, 9)
    g[20].solicitacao_origem = "sac"
    g[20].solicitacao_ref = "US-26-0099"
    await db.commit()

    h = await _lista(client, em_atendimento=True, departamento="celular")
    ordem = [1, 2, 19, 3, 5, 4, 7, 6, 17, 20, 18]
    assert [i["id"] for i in h["itens"]] == [g[n].id for n in ordem]
    assert h["total"] == 11 and h["abas"]["celular"]["atendimento"] == 11
    selo = {
        i["id"]: (
            i["atendimento"]["motivo"],
            i["atendimento"]["rotulo"],
            datetime.fromisoformat(i["atendimento"]["desde"]),
            i["atendimento"]["vencido"],
        )
        for i in h["itens"]
    }
    assert selo[g[1].id] == ("no_tecnico", "No técnico · vencido", _em(10, 10), True)
    assert selo[g[2].id] == ("no_tecnico", "No técnico", _em(10, 25), False)
    assert selo[g[3].id] == ("sem_condicao", "Chegou — falta a condição", _em(10, 28), False)
    assert selo[g[4].id] == ("voltando", "Voltando", _em(10, 30), False)
    assert selo[g[5].id] == ("plataforma_decidindo", "Plataforma decidindo", _em(10, 29), False)
    assert selo[g[6].id] == (
        "pedido_sem_resposta",
        "Pedido de garantia sem resposta",
        _em(11, 2),
        False,
    )
    assert selo[g[7].id] == ("no_tecnico", "No técnico", _em(10, 31), False)
    assert selo[g[19].id] == ("no_tecnico", "No técnico", _em(10, 26), False)
    assert selo[g[17].id][0] == "pedido_sem_resposta"
    assert selo[g[20].id] == (
        "pedido_sem_resposta",
        "Pedido de garantia sem resposta",
        _em(11, 3, 9),
        False,
    )
    # A página (o mais urgente primeiro, sempre).
    h = await _lista(client, em_atendimento=True, departamento="celular", limite=3, offset=3)
    assert [i["id"] for i in h["itens"]] == [g[n].id for n in (3, 5, 4)] and h["total"] == 11
    assert (await _lista(client, em_atendimento=True, departamento="eletro"))["itens"] == []
    # A busca também vale dentro da aba.
    h = await _lista(client, em_atendimento=True, busca="400106")
    assert [i["id"] for i in h["itens"]] == [g[6].id]

    # O SAC respondido sai sozinho.
    db.add(
        AtendimentoMensagem(
            conversa_id=sac.id,
            autor="loja",
            origem="davinci_humano",
            texto="resposta",
            enviada_em=_em(11, 3, 11),
        )
    )
    await db.commit()
    h = await _lista(client, em_atendimento=True, departamento="celular")
    assert g[20].id not in [i["id"] for i in h["itens"]] and h["total"] == 10

    # Quem não é admin: só a loja dele.
    auth_as(await make_user(permissions=CONSULTA))
    h = await _lista(client, em_atendimento=True, departamento="celular")
    assert [i["id"] for i in h["itens"]] == [g[n].id for n in ordem if n not in (18, 20)]
    assert h["abas"]["celular"]["atendimento"] == 9


def test_motivo_do_atendimento_e_a_ordem():
    d = resumo.Desfecho
    t = _em(10, 1)
    assert atend.MOTIVOS_DO_ATENDIMENTO == (
        "no_tecnico",
        "sem_condicao",
        "voltando",
        "plataforma_decidindo",
        "pedido_sem_resposta",
    )
    m = atend.motivo_do_atendimento
    assert m(None) is None
    assert m(None, t) == atend.EmAtendimento(
        "pedido_sem_resposta", "Pedido de garantia sem resposta", t
    )
    assert m(d(situacao="no_tecnico", vencido=True, desde=t)) == atend.EmAtendimento(
        "no_tecnico", "No técnico · vencido", t, True
    )
    # O fim encerra tudo, até o pedido sem resposta e a plataforma decidindo.
    for fim in (
        d(situacao="perdido"),
        d(situacao="consertado"),
        d(situacao="devolvido", condicao="Usado"),
        d(situacao="reembolso", decidindo_desde=t),
    ):
        assert m(fim, t) is None, fim
    # O reembolso pago com o aparelho ainda no técnico (ou sem a condição): o
    # selo é o do aparelho, e ele continua em atendimento.
    assert m(d(situacao="no_tecnico", desde=t), t).motivo == "no_tecnico"
    assert m(d(situacao="devolvido"), t).motivo == "sem_condicao"
    assert m(d(situacao="a_caminho", decidindo_desde=t), t).motivo == "voltando"
    assert m(d(decidindo_desde=t), _em(10, 2)).desde == t
    # A urgência: o vencido primeiro, depois o mais antigo; sem data no fim.
    a = atend.EmAtendimento("no_tecnico", "x", _em(10, 9), True)
    b = atend.EmAtendimento("voltando", "x", _em(10, 1))
    c = atend.EmAtendimento("voltando", "x", None)
    assert sorted([c, b, a], key=atend.urgencia) == [a, b, c]


async def test_pedido_sem_garantia(client, db, make_user, auth_as, pack_do_ml, monkeypatch):
    """O SAC procura o nº da plataforma de um pedido que ainda não tem
    garantia (ex.: o ML 2000017677253868 = Bling 288113, de 30/07/2026, antes
    das notas fiscais de produto do DaVinci): a lista diz o pedido e por quê."""
    await _pedido_bling(db, "288113", mkt="2000017677253868", compra=_em(7, 30), situacao="83953")
    auth_as(await make_user(permissions=CADASTRA))
    for termo in ("288113", "2000017677253868"):
        h = await _lista(client, busca=termo)
        assert (h["itens"], h["total"]) == ([], 0)
        p = h["pedido_sem_garantia"]
        assert (p["pedido_bling"], p["pedido_marketplace"], p["motivo"]) == (
            "288113",
            "2000017677253868",
            "antes_das_notas",
        )
        assert (p["plataforma"], p["conta"], p["produto_uranyx"], p["pode_cadastrar"]) == (
            "shopee",
            "loja teste",
            True,
            True,
        )
        assert "30/07/2026" in p["motivo_texto"] and "19/08/2026" in p["motivo_texto"]
        # Nunca o CPF nem o nome.
        assert "Fulano" not in str(h) and "52998224725" not in str(h)
    # Pelo pack do ML (o nº da compra com vários pedidos).
    await pack_do_ml("2000099999999999", "2000017677253868")
    p = (await _lista(client, busca="2000099999999999"))["pedido_sem_garantia"]
    assert p["pedido_bling"] == "288113"
    # Quem só consulta não cadastra.
    auth_as(await make_user(permissions=CONSULTA))
    assert (await _lista(client, busca="288113"))["pedido_sem_garantia"]["pode_cadastrar"] is False

    # Cada motivo (o 1º que vale): cancelado, sem item Uranyx, a pendência
    # do robô, sem a NF de produto, outro.
    await _pedido_bling(db, "300901", situacao="12")
    await _pedido_bling(db, "300902")
    db.add(GarantiaPendencia(pedido_bling="300902", motivo="nf_sem_cpf"))
    await _pedido_bling(db, "300903")
    await _pedido_bling(db, "300904", sku="b021")
    await _pedido_bling(db, "300905")
    db.add(
        NfNota(
            chave="1" * 44,
            pedido_bling="300905",
            numero="77",
            serie="1",
            valor=900,
            situacao="100",
            xml=b"<nfeProc/>",
        )
    )
    await db.commit()
    motivos = {}
    for numero in ("300901", "300902", "300903", "300904", "300905"):
        motivos[numero] = (await _lista(client, busca=numero))["pedido_sem_garantia"]
    assert {n: p["motivo"] for n, p in motivos.items()} == {
        "300901": "cancelado",
        "300902": "pendencia",
        "300903": "sem_nf_produto",
        "300904": "sem_produto_uranyx",
        "300905": "outro",
    }
    assert garantia_auto.MOTIVOS["nf_sem_cpf"] in motivos["300902"]["motivo_texto"]
    assert all(p["motivo_texto"].endswith(".") for p in motivos.values())

    # Fora da loja da pessoa: nada (o admin vê).
    await _pedido_bling(db, "288114", mkt="2000017677253869", loja="L9")
    assert (await _lista(client, busca="288114"))["pedido_sem_garantia"] is None
    # O pedido que já tem garantia (fora dos filtros da barra) não é "sem garantia".
    await _venda(db, "400201", "dg053.sp")
    h = await _lista(client, busca="400201", departamento="eletro")
    assert (h["itens"], h["pedido_sem_garantia"]) == ([], None)
    # Achou garantia, ou a busca não é de pedido: nada.
    assert (await _lista(client, busca="400201"))["pedido_sem_garantia"] is None
    assert (await _lista(client, busca="joao"))["pedido_sem_garantia"] is None

    # A busca pelo CPF nunca procura o pedido.
    async def _ok(**_kw):
        return 1

    monkeypatch.setattr(rota, "sliding_window_check", _ok)
    assert (await _lista(client, busca="529.982.247-25"))["pedido_sem_garantia"] is None
    auth_as(await make_user(role=UserRole.ADMIN))
    p = (await _lista(client, busca="288114"))["pedido_sem_garantia"]
    assert (p["pedido_bling"], p["pode_cadastrar"]) == ("288114", True)


async def test_busca_pelo_pack_do_ml_acha_a_garantia(client, db, make_user, auth_as, pack_do_ml):
    """O Bling guarda o pedido do ML; o cliente manda o nº do pack (o que o ML
    mostra na compra com vários pedidos): a busca acha pelos dois."""
    auth_as(await make_user(permissions=CADASTRA))
    g = await _venda(db, "400301", "dg053.sp")
    g.pedido_marketplace = "2000011111111111"
    await db.commit()
    await pack_do_ml("2000022222222222", "2000011111111111")
    for termo in ("2000011111111111", "2000022222222222"):
        assert [i["id"] for i in (await _lista(client, busca=termo))["itens"]] == [g.id]
        r = await client.post("/api/garantias/busca", json={"q": termo})
        assert [i["id"] for i in r.json()] == [g.id]
    # Outro pack não acha.
    assert (await _lista(client, busca="2000033333333333"))["itens"] == []
