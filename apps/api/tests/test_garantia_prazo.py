"""Prazo da garantia por produto (06/11/2026): a coluna Garantia (meses) da
Tabela de Preços → Produtos, a vigência de cada mudança e o fim único da
garantia ("se o prazo aumentar, vale para todas as vendas do produto. Se
diminuir, vale só para as vendas feitas a partir da mudança")."""

from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import BlingOrder, Garantia, GarantiaLog, PricingGarantiaPrazo, UserRole
from app.models.pricing import PricingProduct
from app.models.segment import Segment
from app.services import garantia as svc
from app.services import garantia_prazo as gp
from app.services import garantia_resumo as resumo

pytestmark = pytest.mark.asyncio

SP = svc.SAO_PAULO
SEMPRE = datetime.min.replace(tzinfo=UTC)


def _sp(ano, mes, dia, hora=12):
    return datetime(ano, mes, dia, hora, tzinfo=SP)


@pytest.fixture(autouse=True)
def _mapa_novo():
    resumo.limpar_cache()
    yield
    resumo.limpar_cache()


# ── A regra (PURA) ───────────────────────────────────────────────────────────


def test_prazo_da_venda_e_o_maior_desde_o_dia_da_venda():
    vig = [
        (SEMPRE, None),
        (_sp(2026, 10, 10, 15), 3),
        (_sp(2026, 10, 20, 12), 12),  # aumentou: vale para todas
        (_sp(2026, 10, 25, 9), 6),  # diminuiu: só para as vendas a partir daqui
    ]
    assert gp.prazo_da_venda(vig, date(2026, 10, 1)) == 12
    assert gp.prazo_da_venda(vig, date(2026, 10, 22)) == 12
    # A venda do próprio dia da redução fica com o prazo maior (o dia começa
    # às 00:00 de São Paulo, quando o 12 ainda valia).
    assert gp.prazo_da_venda(vig, date(2026, 10, 25)) == 12
    assert gp.prazo_da_venda(vig, date(2026, 10, 26)) == 6
    # Prazo apagado não conta; o que valeu depois da venda, sim.
    apagado = [(SEMPRE, 12), (_sp(2026, 10, 10), None)]
    assert gp.prazo_da_venda(apagado, date(2026, 10, 1)) == 12
    assert gp.prazo_da_venda(apagado, date(2026, 10, 11)) is None
    assert gp.prazo_da_venda([(SEMPRE, None)], date(2026, 10, 1)) is None
    assert gp.prazo_da_venda([], date(2026, 10, 1)) is None


def test_vigencias_do_produto():
    agora = _sp(2026, 11, 6)
    # Sem mudança gravada: o valor de hoje vale desde sempre.
    assert gp.vigencias_do_produto(12, [], agora) == [(SEMPRE, 12)]
    m1 = SimpleNamespace(id=1, vale_desde=_sp(2026, 10, 1), meses=3, meses_anterior=None)
    m2 = SimpleNamespace(id=2, vale_desde=_sp(2026, 10, 9), meses=12, meses_anterior=3)
    assert gp.vigencias_do_produto(12, [m2, m1], agora) == [
        (SEMPRE, None),
        (m1.vale_desde, 3),
        (m2.vale_desde, 12),
    ]
    # O valor da linha mudou por fora da tela: vale desde agora.
    assert gp.vigencias_do_produto(6, [m1, m2], agora)[-1] == (agora, 6)


def test_fim_da_garantia_e_status_do_painel():
    assert svc.fim_da_garantia(date(2026, 11, 30), 3) == date(2027, 2, 28)
    assert svc.fim_da_garantia(date(2026, 11, 30), None) is None
    assert svc.fim_da_garantia(None, 12) is None
    dia = date(2026, 11, 6)
    assert svc.status_painel(None, 12, None, dia) == "aguardando_entrega"
    assert svc.status_painel(date(2026, 10, 1), None, None, dia) == "sem_prazo"
    assert svc.status_painel(date(2026, 8, 6), 3, date(2026, 11, 6), dia) == "ativa"  # inclusive
    assert svc.status_painel(date(2026, 8, 5), 3, date(2026, 11, 5), dia) == "expirada"
    assert svc.STATUS_PAINEL_ROTULOS["sem_prazo"] == "Prazo não cadastrado"


def test_nome_sem_apelido():
    assert svc.nome_sem_apelido("Maria Souza (maria123)") == "Maria Souza"
    assert svc.nome_sem_apelido("Maria Souza (maria12") == "Maria Souza"  # cortado na NF
    assert svc.nome_sem_apelido("João (Joãozinho) Silva") == "João (Joãozinho) Silva"
    assert svc.nome_sem_apelido("(apelido)") == "(apelido)"
    assert svc.nome_sem_apelido("  Ana   Lima ") == "Ana Lima"


# ── No banco ─────────────────────────────────────────────────────────────────


async def _produto(db, dono, sku="dg053,dg054", nome="Uranyx T8", raiz="celular", meses=None):
    seg = (
        await db.execute(select(Segment).where(Segment.slug == raiz, Segment.parent_id.is_(None)))
    ).scalar_one_or_none()
    if seg is None:
        seg = Segment(name=raiz.title(), slug=raiz)
        db.add(seg)
        await db.flush()
    p = PricingProduct(user_id=dono.id, sku=sku, name=nome, segment_id=seg.id, garantia_meses=meses)
    db.add(p)
    await db.commit()
    return p


async def _venda(db, numero, compra: datetime, *, sku="dg053.sp", entrega: date | None):
    db.add(
        BlingOrder(
            numero=numero,
            numeroloja=f"MKT{numero}",
            loja="L1",
            data=compra,
            total=1000,
            situacao="15",
            item_index=0,
            item_codigo=sku,
            item_descricao="Celular Uranyx T8 - Preto",
            item_quantidade=1,
        )
    )
    g = Garantia(
        origem="robo",
        pedido_bling=numero,
        pedido_marketplace=f"MKT{numero}",
        loja="L1",
        nf_numero="9" + numero[-4:],
        cliente_nome="Cliente",
        cpf="52998224725",
        itens=[{"sku": sku, "descricao": "Celular Uranyx T8", "quantidade": 1, "uranyx": True}],
    )
    if entrega is not None:
        svc.aplicar_entrega(g, svc.Entrega(datetime.combine(entrega, time(12), UTC), "shopee"))
    db.add(g)
    await db.commit()
    return g


async def _prazos(db, *garantias):
    for g in garantias:
        await db.refresh(g)
    return [(g.prazo_meses, g.fim_garantia) for g in garantias]


async def test_aplicar_prazos_aumento_vale_para_todas_reducao_so_para_as_novas(db, make_user):
    dono = await make_user(role=UserRole.ADMIN)
    p = await _produto(db, dono)
    antiga = await _venda(db, "500001", _sp(2026, 9, 1), entrega=date(2026, 9, 5))
    nova = await _venda(db, "500002", _sp(2026, 10, 20), entrega=date(2026, 10, 24))
    fora = await _venda(db, "500003", _sp(2026, 10, 20), sku="dg999", entrega=date(2026, 10, 24))
    # As mudanças do prazo (a vigência): 3 meses em 01/08, 12 em 10/09 e 6 em 15/10.
    db.add_all(
        [
            PricingGarantiaPrazo(pricing_product_id=p.id, meses=3, vale_desde=_sp(2026, 8, 1)),
            PricingGarantiaPrazo(
                pricing_product_id=p.id, meses=12, meses_anterior=3, vale_desde=_sp(2026, 9, 10)
            ),
            PricingGarantiaPrazo(
                pricing_product_id=p.id, meses=6, meses_anterior=12, vale_desde=_sp(2026, 10, 15)
            ),
        ]
    )
    p.garantia_meses = 6
    await db.commit()
    assert await gp.aplicar_prazos(db) == 2
    await db.commit()
    assert await _prazos(db, antiga, nova, fora) == [
        (12, date(2027, 9, 5)),  # vendida antes da redução: fica com o maior
        (6, date(2027, 4, 24)),
        (None, None),  # fora da Tabela: "Prazo não cadastrado"
    ]
    logs = list(
        (
            await db.execute(
                select(GarantiaLog).where(GarantiaLog.acao == "recalculou").order_by(GarantiaLog.id)
            )
        ).scalars()
    )
    assert [(x.garantia_id, x.campo, x.valor_anterior, x.valor_novo) for x in logs] == [
        (antiga.id, "prazo_meses", None, "12"),
        (nova.id, "prazo_meses", None, "6"),
    ]
    assert logs[0].user_id is None and logs[0].user_nome == gp.ROTULO_ROBO
    # Nada mudou: nada é gravado de novo.
    assert await gp.aplicar_prazos(db) == 0
    # A entrega que muda depois leva o prazo da garantia junto.
    solta = Garantia(prazo_meses=6)
    svc.aplicar_entrega(solta, svc.Entrega(datetime(2026, 10, 30, 12, tzinfo=UTC), "shopee"))
    assert (solta.data_inicio, solta.fim_garantia) == (date(2026, 10, 30), date(2027, 4, 30))
    svc.aplicar_entrega(solta, None)
    assert solta.fim_garantia is None


async def test_patch_da_tabela_grava_a_vigencia_e_recalcula(client, db, make_user, auth_as):
    dono = await make_user(role=UserRole.ADMIN)
    auth_as(dono)
    p = await _produto(db, dono)
    hoje = svc.hoje()
    antes = await _venda(db, "500011", _sp(2026, 10, 1), entrega=date(2026, 10, 5))
    # A venda de amanhã (depois da redução que vem aí).
    amanha = datetime.combine(hoje + timedelta(days=1), time(10), SP)
    depois = await _venda(db, "500012", amanha, entrega=None)

    r = await client.patch(f"/api/pricing/products/{p.id}", json={"garantia_meses": 12})
    assert r.status_code == 200, r.text
    assert r.json()["garantia_meses"] == 12
    assert await _prazos(db, antes, depois) == [(12, date(2027, 10, 5)), (12, None)]

    # Diminuir: a venda de antes fica com 12; a de amanhã passa a 3.
    r = await client.patch(f"/api/pricing/products/{p.id}", json={"garantia_meses": 3})
    assert r.status_code == 200, r.text
    assert await _prazos(db, antes, depois) == [(12, date(2027, 10, 5)), (3, None)]

    vig = list(
        (
            await db.execute(
                select(PricingGarantiaPrazo)
                .where(PricingGarantiaPrazo.pricing_product_id == p.id)
                .order_by(PricingGarantiaPrazo.id)
            )
        ).scalars()
    )
    assert [(v.meses, v.meses_anterior, v.user_id) for v in vig] == [
        (12, None, dono.id),
        (3, 12, dono.id),
    ]
    # O recálculo fica no log da garantia, em nome de quem mudou.
    log = (
        await db.execute(
            select(GarantiaLog)
            .where(GarantiaLog.garantia_id == depois.id, GarantiaLog.campo == "prazo_meses")
            .order_by(GarantiaLog.id.desc())
            .limit(1)
        )
    ).scalar_one()
    assert (log.valor_anterior, log.valor_novo, log.user_id) == ("12", "3", dono.id)

    # Fora de 1..120: recusa. Mudar outra coisa da linha não grava vigência.
    for ruim in (0, 121):
        r = await client.patch(f"/api/pricing/products/{p.id}", json={"garantia_meses": ruim})
        assert r.status_code == 422
    assert (
        await client.patch(f"/api/pricing/products/{p.id}", json={"name": "T8"})
    ).status_code == 200
    # null apaga: a vigência guarda o apagar; a venda de antes mantém o 12.
    r = await client.patch(f"/api/pricing/products/{p.id}", json={"garantia_meses": None})
    assert r.status_code == 200 and r.json()["garantia_meses"] is None
    assert await _prazos(db, antes) == [(12, date(2027, 10, 5))]
    n = await db.scalar(
        select(PricingGarantiaPrazo.meses)
        .where(PricingGarantiaPrazo.pricing_product_id == p.id)
        .order_by(PricingGarantiaPrazo.id.desc())
        .limit(1)
    )
    assert n is None
    assert len((await db.execute(select(PricingGarantiaPrazo))).all()) == 3


async def test_produto_novo_com_prazo_e_sku_trocado(client, db, make_user, auth_as):
    dono = await make_user(role=UserRole.ADMIN)
    auth_as(dono)
    venda = await _venda(db, "500021", _sp(2026, 10, 1), sku="dg077", entrega=date(2026, 10, 5))
    seg = Segment(name="Celular", slug="celular")
    db.add(seg)
    await db.commit()
    r = await client.post(
        "/api/pricing/products",
        json={"sku": "dg077", "name": "Uranyx X", "segment_id": str(seg.id), "garantia_meses": 6},
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert await _prazos(db, venda) == [(6, date(2027, 4, 5))]
    # O SKU sai da linha: a venda fica fora da Tabela (sem prazo).
    r = await client.patch(f"/api/pricing/products/{pid}", json={"sku": "dg078"})
    assert r.status_code == 200, r.text
    assert await _prazos(db, venda) == [(None, None)]


async def test_linha_de_departamento_vence_a_do_catalogo(db, make_user):
    """O mesmo SKU no Catálogo (criado antes) e no Celular: o prazo é o da
    linha de Celular, onde a pessoa digita."""
    dono = await make_user(role=UserRole.ADMIN)
    await _produto(db, dono, sku="dg053", nome="T8 catálogo", raiz="catalogo", meses=24)
    await _produto(db, dono, sku="dg053", nome="T8", raiz="celular", meses=3)
    venda = await _venda(db, "500031", _sp(2026, 10, 1), entrega=date(2026, 10, 5))
    await gp.aplicar_prazos(db)
    await db.commit()
    assert await _prazos(db, venda) == [(3, date(2027, 1, 5))]


async def test_aplicar_prazos_nao_sobrescreve_o_que_mudou_no_meio_da_volta(
    db, make_user, monkeypatch
):
    """Revisão de 06/11/2026: a volta do robô lê as garantias e calcula; se o
    PATCH da Tabela (outra transação) gravou o prazo de uma delas no meio do
    caminho, o cálculo daqui já está velho — não sobrescreve (nem grava log);
    a próxima volta relê."""
    from app import db as banco

    dono = await make_user(role=UserRole.ADMIN)
    p = await _produto(db, dono, meses=6)
    db.add(PricingGarantiaPrazo(pricing_product_id=p.id, meses=6, vale_desde=_sp(2026, 8, 1)))
    await db.commit()
    g = await _venda(db, "500011", _sp(2026, 9, 1), entrega=date(2026, 9, 5))
    gid = g.id
    original = gp._vigencias

    async def _com_patch_no_meio(session, ids):
        saida = await original(session, ids)
        async with banco.SessionLocal() as outra:
            await outra.execute(
                Garantia.__table__.update()
                .where(Garantia.__table__.c.id == gid)
                .values(prazo_meses=24, fim_garantia=date(2028, 9, 5))
            )
            await outra.commit()
        return saida

    monkeypatch.setattr(gp, "_vigencias", _com_patch_no_meio)
    assert await gp.aplicar_prazos(db, ids=[gid]) == 0
    await db.commit()
    assert await _prazos(db, g) == [(24, date(2028, 9, 5))]
    assert (
        await db.execute(select(GarantiaLog).where(GarantiaLog.acao == "recalculou"))
    ).all() == []
    # Sem ninguém no meio, a volta seguinte grava o que a vigência diz.
    monkeypatch.setattr(gp, "_vigencias", original)
    assert await gp.aplicar_prazos(db, ids=[gid]) == 1
    await db.commit()
    assert await _prazos(db, g) == [(6, date(2027, 3, 5))]
