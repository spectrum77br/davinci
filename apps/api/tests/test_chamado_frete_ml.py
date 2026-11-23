"""Chamado de frete do ML (Ouvidoria, 17/11/2026): a diferença de frete do ML lançada no
Reembolso vira chamado na fila da IA de Chamado; o nº da consulta volta pro Reembolso; a
consulta que não abre vira ocorrência. Ver services/chamado_frete_ml."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.models import (
    BlingOrder,
    Chamado,
    ChamadoMensagem,
    OuvidoriaOcorrencia,
    OuvidoriaRobo,
    Refund,
)
from app.models.enums import IntegrationPlatform, UserRole
from app.models.marketplace_financial import (
    MarketplaceOrderFinancial,
    MarketplaceOrderFreightReconciliation,
)
from app.services import chamado_frete_ml as cf
from app.services import ouvidoria

pytestmark = pytest.mark.asyncio

AGORA = datetime.now(UTC)
_INICIO_REAL = cf.INICIO

_TABELAS = (
    "ouvidoria_ocorrencias",
    "ouvidoria_rodadas",
    "ouvidoria_robos",
    "marketplace_order_freight_reconciliations",
    "marketplace_order_financials",
)


@pytest.fixture(autouse=True)
def _inicio_antigo(monkeypatch):
    """Os testes usam datas relativas a hoje; o corte de 17/11/2026 tem teste próprio."""
    monkeypatch.setattr(cf, "INICIO", AGORA - timedelta(days=365))


@pytest_asyncio.fixture(autouse=True)
async def _limpa(db):
    for t in _TABELAS:
        await db.execute(text(f"DELETE FROM {t}"))  # noqa: S608
    await db.commit()
    yield
    for t in _TABELAS:
        await db.execute(text(f"DELETE FROM {t}"))  # noqa: S608
    await db.commit()


async def _refund(
    db,
    *,
    pedido="297991",
    venda="2000015092875717",
    conta="ML Velasco",
    plataforma="ml",
    prejuizo=8.17,
    dias=5,
    criado_dias=None,
    **extra,
) -> Refund:
    r = Refund(
        data=AGORA - timedelta(days=dias),
        pedido_bling=pedido,
        pedido_marketplace=venda,
        plataforma=plataforma,
        conta=conta,
        tipo="Logistica",
        prejuizo=prejuizo,
        **extra,
    )
    db.add(r)
    await db.flush()
    r.created_at = AGORA - timedelta(days=dias if criado_dias is None else criado_dias)
    await db.commit()
    return r


async def _frete(
    db,
    *,
    pedido="297991",
    venda="2000015092875717",
    bling_id=9001,
    item="MLB4803162551",
    anuncio="40.85",
    cobrado="49.02",
    envio="44001",
    dims=(30, 27, 27, 4000),
    bruto=None,
):
    db.add(BlingOrder(bling_id=bling_id, numero=pedido, numeroloja=venda, item_index=0))
    fin = MarketplaceOrderFinancial(
        platform=IntegrationPlatform.ML,
        external_order_id=venda,
        bling_id=bling_id,
        gross_amount=Decimal(bruto) if bruto is not None else None,
    )
    db.add(fin)
    await db.flush()
    comp, larg, alt, peso = dims
    db.add(
        MarketplaceOrderFreightReconciliation(
            order_financial_id=fin.id,
            platform=IntegrationPlatform.ML,
            bling_id=bling_id,
            external_order_id=venda,
            item_index=0,
            shipping_id=envio,
            marketplace_item_id=item,
            freight_promised_amount=Decimal(anuncio),
            freight_actual_amount=Decimal(cobrado),
            dimension_length=Decimal(comp),
            dimension_width=Decimal(larg),
            dimension_height=Decimal(alt),
            dimension_weight=Decimal(peso),
        )
    )
    await db.commit()


async def _config(db, **cfg):
    await ouvidoria.sincronizar_catalogo(db)
    robo = await db.get(OuvidoriaRobo, cf.ROBO)
    robo.config = {**(robo.config or {}), **cfg}
    await db.commit()


async def _chamados(db) -> list[Chamado]:
    return list(
        (await db.execute(select(Chamado).order_by(Chamado.created_at))).scalars().all()
    )


async def _abertura(db, ch: Chamado) -> ChamadoMensagem:
    return (
        await db.execute(
            select(ChamadoMensagem).where(
                ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.tipo == "abertura"
            )
        )
    ).scalar_one()


async def test_cria_chamado_na_fila_da_ia_com_o_texto_do_frete(db):
    ref = await _refund(db)
    await _frete(db)

    out = await cf.chamado_frete_ml_run(db, agora=AGORA)

    assert out["criados"] == 1
    (ch,) = await _chamados(db)
    assert ch.origem == "margem"
    assert ch.origem_ref == str(ref.id)
    assert ch.canal == "robo"
    assert ch.plataforma == "ml"
    assert ch.conta == "ML Velasco"
    assert ch.pedido_bling == "297991"
    assert ch.pedido_marketplace == "2000015092875717"
    assert not (ch.chamado or "").strip()
    ab = await _abertura(db, ch)
    assert ab.status == "pendente"
    assert ab.canal == "robo"
    assert ab.direcao == "enviada"
    assert ab.texto.startswith("Solicito revisão e reembolso da diferença de frete")
    assert (
        "- Pedido 2000015092875717: frete anúncio R$ 40,85; frete cobrado R$ 49,02; "
        "diferença R$ 8,17"
    ) in ab.texto
    assert (
        "link anúncio https://produto.mercadolivre.com.br/MLB-4803162551, "
        "dimensões 30×27×27 cm (C×L×A), 4 kg"
    ) in ab.texto

    # a rodada seguinte não duplica
    out = await cf.chamado_frete_ml_run(db, agora=AGORA)
    assert out["criados"] == 0
    assert len(await _chamados(db)) == 1


async def test_sem_conciliacao_de_frete_usa_a_diferenca_do_reembolso(db):
    await _refund(db, prejuizo=12.5)

    await cf.chamado_frete_ml_run(db, agora=AGORA)

    (ch,) = await _chamados(db)
    ab = await _abertura(db, ch)
    assert (
        "- Pedido 2000015092875717: o frete cobrado foi R$ 12,50 maior que o frete do anúncio"
    ) in ab.texto


async def test_so_linhas_de_frete_do_ml_no_prazo_e_sem_dono(db, make_user):
    pessoa = await make_user()
    await _refund(db, pedido="1", venda="v1", plataforma="shopee")  # outra plataforma
    await _refund(db, pedido="2", venda="v2", prejuizo=4.99)  # abaixo de R$ 5
    await _refund(db, pedido="3", venda="v3", dias=40)  # venda velha
    await _refund(db, pedido="4", venda="v4", conferido=True)  # alguém conferiu
    await _refund(db, pedido="5", venda="v5", reembolso=8.0)  # já reembolsado
    await _refund(db, pedido="6", venda="v6", chamado="485000001")  # já tem consulta
    await _refund(db, pedido="7", venda="v7", created_by=pessoa.id)  # lançada à mão
    await _refund(db, pedido="8", venda="v8", chamado_resolvido=True)  # dado por resolvido
    # cobrança que o ML fechou tarde: lançada há 3 dias, mas a venda tem 60 — o ML nega
    await _refund(db, pedido="9", venda="v9", dias=60, criado_dias=3)
    await _refund(db, pedido="10", venda="v10")  # esta sim

    out = await cf.chamado_frete_ml_run(db, agora=AGORA)

    assert out["criados"] == 1
    (ch,) = await _chamados(db)
    assert ch.pedido_bling == "10"


async def test_so_venda_a_partir_do_inicio(db, monkeypatch):
    """Vinicius 17/11: "tenta todas que cair pedido de hoje para frente"."""
    monkeypatch.setattr(cf, "INICIO", AGORA - timedelta(days=2))
    await _refund(db, pedido="40", venda="v40", dias=5)  # venda antes do corte
    await _refund(db, pedido="41", venda="v41", dias=1)  # depois do corte

    out = await cf.chamado_frete_ml_run(db, agora=AGORA)

    assert out["criados"] == 1
    (ch,) = await _chamados(db)
    assert ch.pedido_bling == "41"


def test_inicio_e_17_de_novembro_em_brasilia():
    assert _INICIO_REAL.isoformat() == "2026-11-17T00:00:00-03:00"


async def test_pula_conta_fora_cancelado_sem_venda_e_frete_que_nao_confere(db):
    await _config(db, contas_fora="ML Injox; inova")
    await _refund(db, pedido="20", venda="v20", conta="ML Injox")
    await _refund(db, pedido="21", venda="v21", conta="ML Inova")
    await _refund(db, pedido="22", venda="v22")
    db.add(BlingOrder(numero="22", numeroloja="v22", situacao="Cancelado", item_index=0))
    await _refund(db, pedido="23", venda=None)  # sem nº da venda (nem no espelho)
    # frete relido: cobrado igual ao do anúncio — a linha do Reembolso ficou velha
    await _refund(db, pedido="24", venda="v24")
    await _frete(db, pedido="24", venda="v24", bling_id=9024, anuncio="40.00", cobrado="40.00")
    await _refund(db, pedido="25", venda="v25", conta="ML Velasco")  # esta sim
    await db.commit()

    out = await cf.chamado_frete_ml_run(db, agora=AGORA)

    assert out["criados"] == 1
    assert out["conta_fora"] == 2
    assert out["cancelado"] == 1
    assert out["sem_venda"] == 1
    assert out["sem_diferenca"] == 1
    (ch,) = await _chamados(db)
    assert ch.pedido_bling == "25"
    assert "2 de conta fora" in out["resumo"]


async def test_pula_venda_abaixo_de_79(db):
    """310187 (23/11): ML Poofy, 2 × R$ 30. Abaixo de R$ 79 o ML cobra mais ou menos o
    dobro da cotação do anúncio e nega a diferença — a linha lançada antes da trava do
    Reembolso não pode virar chamado."""
    await _refund(db, pedido="310187", venda="2000016213811845", conta="ML Poofy", prejuizo=13.8)
    await _frete(
        db,
        pedido="310187",
        venda="2000016213811845",
        bling_id=9187,
        anuncio="13.90",
        cobrado="27.70",
        bruto="60.00",
    )
    # R$ 79 em diante segue cobrando
    await _refund(db, pedido="310200", venda="v310200", prejuizo=8.17)
    await _frete(db, pedido="310200", venda="v310200", bling_id=9200, bruto="79.00")

    out = await cf.chamado_frete_ml_run(db, agora=AGORA)

    assert out["criados"] == 1
    assert out[cf.PULA_VENDA_BAIXA] == 1
    (ch,) = await _chamados(db)
    assert ch.pedido_bling == "310200"
    assert "1 de venda abaixo de R$ 79" in out["resumo"]
    # a rodada seguinte pula de novo, sem criar
    out = await cf.chamado_frete_ml_run(db, agora=AGORA)
    assert out["criados"] == 0
    assert out[cf.PULA_VENDA_BAIXA] == 1


async def test_duas_linhas_do_mesmo_pedido_viram_um_chamado(db):
    await _refund(db, pedido="30", venda="v30", conta="ML Velasco")
    await _refund(db, pedido="30", venda="v30", conta="ML Velasco Nova")  # conta renomeada

    out = await cf.chamado_frete_ml_run(db, agora=AGORA)

    assert out["criados"] == 1
    assert out["repetido"] == 1
    assert len(await _chamados(db)) == 1
    # e a rodada seguinte não cria o segundo (o pedido já tem chamado)
    out = await cf.chamado_frete_ml_run(db, agora=AGORA)
    assert out["criados"] == 0


async def test_desligado_nao_cria_nem_pelo_rodar_agora(db):
    await _config(db)
    robo = await db.get(OuvidoriaRobo, cf.ROBO)
    robo.modo = "desligado"
    await db.commit()
    await _refund(db)

    out = await cf.chamado_frete_ml_run(db, agora=AGORA)

    assert out["criados"] == 0
    assert out["na_fila"] == 1
    assert await _chamados(db) == []


async def test_apagar_o_chamado_do_robo_nao_faz_nascer_outro(client, db, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    ref = await _refund(db)
    rid = ref.id
    await cf.chamado_frete_ml_run(db, agora=AGORA)
    (ch,) = await _chamados(db)

    r = await client.delete(f"/api/chamados/{ch.id}")
    assert r.status_code == 204, r.text

    db.expire_all()
    ref = await db.get(Refund, rid)
    assert ref.chamado_resolvido is True
    assert "Chamado de frete apagado na aba Chamados" in (ref.observacao or "")
    out = await cf.chamado_frete_ml_run(db, agora=AGORA)
    assert out["criados"] == 0
    assert await _chamados(db) == []


async def test_chamado_de_frete_antigo_do_pedido_nao_ganha_outro(db):
    await _refund(db)
    # chamado do robô antigo (Eduardo), registrado pelo pedido, sem origem_ref
    db.add(Chamado(pedido_bling="297991", origem="margem", canal="robo", plataforma="ml"))
    await db.commit()

    out = await cf.chamado_frete_ml_run(db, agora=AGORA)

    assert out["criados"] == 0
    assert len(await _chamados(db)) == 1


async def test_limite_por_rodada_das_vendas_mais_velhas_pras_novas(db):
    await _config(db, por_rodada=2)
    await _refund(db, pedido="10", venda="v10", dias=3)
    await _refund(db, pedido="11", venda="v11", dias=20)
    await _refund(db, pedido="12", venda="v12", dias=10)

    out = await cf.chamado_frete_ml_run(db, agora=AGORA)

    assert out["criados"] == 2
    assert out["na_fila"] == 1
    assert sorted(c.pedido_bling for c in await _chamados(db)) == ["11", "12"]

    out = await cf.chamado_frete_ml_run(db, agora=AGORA)
    assert out["criados"] == 1
    assert out["na_fila"] == 0


async def test_consulta_aberta_volta_pro_reembolso(client, db, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("NF_AGENT_TOKEN", "tok-frete-teste")
    get_settings.cache_clear()
    ref = await _refund(db)
    rid = ref.id
    await cf.chamado_frete_ml_run(db, agora=AGORA)
    (ch,) = await _chamados(db)
    ab = await _abertura(db, ch)

    r = await client.post(
        "/api/chamados/agent/resultado",
        headers={"X-Agent-Token": "tok-frete-teste"},
        json={
            "mensagem_id": str(ab.id),
            "ok": True,
            "chamado": "485038658",
            "chamado_url": "https://www.mercadolivre.com.br/cases/detail/485038658",
        },
    )
    get_settings.cache_clear()
    assert r.status_code == 200, r.text

    db.expire_all()
    ref = await db.get(Refund, rid)
    assert ref.chamado == "485038658"
    assert ref.chamado_url == "https://www.mercadolivre.com.br/cases/detail/485038658"
    # com a consulta, a linha sai da fila do robô
    out = await cf.chamado_frete_ml_run(db, agora=AGORA)
    assert out["criados"] == 0
    assert out["na_fila"] == 0


async def test_vigia_consulta_que_nao_abriu_e_fecha_quando_abre(db):
    await _config(db, horas_parado=24)
    ref = await _refund(db)
    await cf.chamado_frete_ml_run(db, agora=AGORA)
    (ch,) = await _chamados(db)
    ab = await _abertura(db, ch)
    ab.status = "falhou"
    ab.erro = "humano: o assistente disse que a venda já tem reclamação"
    await db.commit()

    # antes de `horas_parado`: nada
    out = await cf.chamado_frete_ml_run(db, agora=AGORA + timedelta(hours=2))
    assert out["parados"] == 0

    depois = AGORA + timedelta(hours=30)
    out = await cf.chamado_frete_ml_run(db, agora=depois)
    assert out["parados"] == 1
    oc = (
        await db.execute(
            select(OuvidoriaOcorrencia).where(OuvidoriaOcorrencia.robo_chave == cf.ROBO)
        )
    ).scalar_one()
    assert oc.chave == f"refund:{ref.id}"
    assert oc.fechada_em is None
    assert "297991" in oc.titulo
    assert "não conseguiu abrir" in (oc.detalhe or "")

    # a pessoa abriu a consulta e ligou o nº: some do vigia
    ch.chamado = "485099999"
    ref.chamado = "485099999"
    await db.commit()
    out = await cf.chamado_frete_ml_run(db, agora=depois + timedelta(hours=1))
    assert out["parados"] == 0
    await db.refresh(oc)
    assert oc.fechada_em is not None


async def test_nasce_silencioso_cria_sem_threema(db):
    assert await ouvidoria.modo_efetivo(db, cf.ROBO) == "silencioso"
    await _refund(db)
    out = await cf.chamado_frete_ml_run(db, agora=AGORA)
    assert out["criados"] == 1
    assert out["avisadas"] == 0  # silencioso: sem Threema


def test_link_do_anuncio():
    assert cf.link_anuncio("MLB4803162551") == "https://produto.mercadolivre.com.br/MLB-4803162551"
    assert cf.link_anuncio("MLB-4803162551") == "https://produto.mercadolivre.com.br/MLB-4803162551"
    assert cf.link_anuncio(None) is None
