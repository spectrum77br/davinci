"""Pedido novo entra na Logística pelo motor de 5 min (19/11/2026).

Vinicius: "esse pedido parece que caiu bem cedo no bling e demorou para
aparecer no davinci" — 309723 (atlas ML): pago 09:32, no espelho `bling_orders`
às 09:34, linha na Logística só às 10:00, porque só o cron de hora em hora
inseria. Medido no ar: mediana de 27 min, até ~1 h. Agora `recarregar_ml`
insere os pedidos novos (janela curta) e os enriquece na mesma rodada.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, Logistica, SituacaoBling, StoreInfo, User
from app.models.enums import UserRole, UserStatus
from app.services import logistica_ingest

LOJA_ML = "206365164"
LOJA_SHOPEE = "205000001"


@pytest_asyncio.fixture
async def base(db: AsyncSession) -> None:
    email = f"adm-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(open_id=f"email:{email}", email=email, role=UserRole.ADMIN, status=UserStatus.ACTIVE)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    db.add_all(
        [
            StoreInfo(user_id=u.id, platform="ml", account_name="atlas", bling_store_id=LOJA_ML),
            StoreInfo(
                user_id=u.id, platform="shopee", account_name="fiore", bling_store_id=LOJA_SHOPEE
            ),
        ]
    )
    for sid, nome in ((21, "Em digitação"), (12, "Cancelado"), (6, "Em aberto")):
        if await db.get(SituacaoBling, sid) is None:
            db.add(SituacaoBling(id=sid, nome=nome))
    await db.commit()


async def _pedido(
    db: AsyncSession,
    numero: str,
    *,
    loja: str = LOJA_ML,
    situacao: str = "21",
    dias_atras: int = 0,
    itens: int = 1,
) -> None:
    data = datetime.now(UTC) - timedelta(days=dias_atras)
    db.add_all(
        [
            BlingOrder(
                bling_id=int(numero) * 10,
                numero=numero,
                numeroloja=f"2000{numero}",
                loja=loja,
                situacao=situacao,
                data=data,
                item_index=i,
                item_codigo=f"sku-{i}",
                item_quantidade=1,
            )
            for i in range(itens)
        ]
    )
    await db.commit()


async def _linhas(db: AsyncSession, numero: str) -> list[Logistica]:
    res = await db.execute(select(Logistica).where(Logistica.pedido_bling == numero))
    return list(res.scalars().all())


@pytest.mark.asyncio
async def test_pedido_novo_entra_uma_vez_com_os_dados_da_loja(db: AsyncSession, base):
    await _pedido(db, "309723", itens=2)  # 2 itens = 2 linhas no espelho, 1 na Logística
    await _pedido(db, "309724", loja=LOJA_SHOPEE)

    ids = await logistica_ingest.inserir_pedidos_novos(db)

    assert len(ids) == 2
    [ml] = await _linhas(db, "309723")
    assert ml.id in ids
    assert (ml.plataforma, ml.conta, ml.status_bling) == ("Mercado Livre", "atlas", "Em digitação")
    assert ml.pedido_marketplace == "2000309723"
    assert ml.meli_status == {}
    [sh] = await _linhas(db, "309724")
    assert (sh.plataforma, sh.conta) == ("Shopee", "fiore")

    # Rodar de novo (próximo ciclo do motor, ou o cron da hora) não duplica.
    assert await logistica_ingest.inserir_pedidos_novos(db) == []
    assert await logistica_ingest._ingest_platform(db, "ml", 60) == 0
    assert len(await _linhas(db, "309723")) == 1


@pytest.mark.asyncio
async def test_motor_so_olha_janela_curta_e_pula_finalizado(db: AsyncSession, base):
    await _pedido(db, "300001", dias_atras=10)  # velho: fica pro cron de hora em hora
    await _pedido(db, "300002", situacao="12")  # Cancelado nunca entra
    await _pedido(db, "300003", situacao="excluido")

    assert await logistica_ingest.inserir_pedidos_novos(db) == []

    # O de hora em hora (60 dias) continua pegando o velho.
    assert await logistica_ingest._ingest_platform(db, "ml", 60) == 1
    assert len(await _linhas(db, "300001")) == 1
    assert await _linhas(db, "300002") == []
    assert await _linhas(db, "300003") == []


@pytest.mark.asyncio
async def test_recarregar_insere_e_enriquece_o_novo_na_mesma_rodada(
    db: AsyncSession, base, monkeypatch
):
    from app.services import (
        logistica_amazon,
        logistica_bling,
        logistica_chamados_robo,
        logistica_meli,
        logistica_shopee,
        logistica_tiktok,
    )

    await _pedido(db, "309723")

    enriquecidos: dict[str, list] = {}

    def _enrich_de(nome):
        async def _enrich(session, *, ids, only_empty=False):
            enriquecidos[nome] = list(ids)
            return {"seen": len(ids)}

        return _enrich

    async def _vazio(session, ids):
        return {}

    async def _nada(session, *a, **k):
        return {}

    async def _sem_mudanca(session):  # SQL cru com schema fixo — fora do banco de teste
        return []

    async def _zero(session, *a, **k):
        return 0

    for nome, mod in (
        ("ml", logistica_meli),
        ("shopee", logistica_shopee),
        ("tiktok", logistica_tiktok),
        ("amazon", logistica_amazon),
    ):
        monkeypatch.setattr(mod, "enrich_recent", _enrich_de(nome))
    monkeypatch.setattr(logistica_meli, "abrir_chamados_em_lote", _vazio)
    monkeypatch.setattr(logistica_chamados_robo, "abrir_chamados_em_lote", _vazio)
    monkeypatch.setattr(logistica_bling, "enviar_threema_em_lote", _vazio)
    monkeypatch.setattr(logistica_bling, "escrever_mensagem_bling_em_lote", _vazio)
    monkeypatch.setattr(logistica_bling, "aplicar_status_em_lote", _vazio)
    monkeypatch.setattr(logistica_ingest, "_amazon_bling_best_effort", _nada)
    monkeypatch.setattr(logistica_ingest, "refresh_status_bling", _sem_mudanca)
    monkeypatch.setattr(logistica_ingest, "cleanup_finalizados", _zero)
    monkeypatch.setattr(logistica_ingest, "ler_correios_do_painel", _zero)

    out = await logistica_ingest.recarregar_ml(db)

    [linha] = await _linhas(db, "309723")
    assert out["novos"] == 1
    assert linha.id in enriquecidos["ml"]
