"""logistica_amazon_avisos — avisos Threema do Envio próprio (3 dias antes do
prazo da Amazon, previsão dos Correios vencida, prazo vencido) + linhas do
botão Informar. Threema falso; carimbos no banco."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Logistica, ThreemaInformarConfig
from app.services import logistica_amazon_avisos as avisos

HOJE = date(2026, 10, 5)


class FakeThreema:
    def __init__(self, ok: bool = True):
        self.enviadas: list[tuple[str, list[str]]] = []
        self.ok = ok

    async def send_to_all(self, text, recipients):
        self.enviadas.append((text, list(recipients)))
        if self.ok:
            return {"sent": list(recipients), "failed": []}
        return {"sent": [], "failed": list(recipients)}


def _linha(**kw) -> Logistica:
    base = {
        "data": date(2026, 9, 12),
        "pedido_bling": "296762",
        "pedido_marketplace": "701-3967231-6921832",
        "plataforma": "Amazon",
        "conta": "kia",
        "meli_status": {"order_status": "Shipped", "fulfillment_channel": "MFN"},
        "amazon_canal": "proprio",
        "servico_envio": "SEDEX",
        "rastreio": "AD912266053BR",
        "localizacao": "Aracaju/SE — Objeto em trânsito",
        "cliente_nome": "Rosana Vieira de Melo",
        "previsao_correios": date(2026, 9, 23),
        "prazo_entrega_amazon": date(2026, 10, 8),
        "status_bling": "Em digitação",
    }
    base.update(kw)
    return Logistica(**base)


# ---- avisos_devidos (puro) ----


def test_avisos_devidos_3_dias_antes_e_previsao_vencida():
    r = _linha()
    # 05/10: faltam 3 dias pra 08/10 e a previsão dos Correios (23/09) já passou.
    assert avisos.avisos_devidos(r, HOJE) == [avisos.TIPO_PREVISAO, avisos.TIPO_PRAZO_3D]


def test_avisos_devidos_respeita_carimbos():
    r = _linha(
        aviso_previsao_correios_at=datetime.now(UTC), aviso_prazo_amazon_3d_at=datetime.now(UTC)
    )
    assert avisos.avisos_devidos(r, HOJE) == []


def test_avisos_devidos_antes_da_janela_nao_avisa():
    r = _linha(previsao_correios=date(2026, 10, 20))
    assert avisos.avisos_devidos(r, date(2026, 10, 1)) == []


def test_avisos_devidos_prazo_vencido_substitui_o_de_3_dias():
    r = _linha(previsao_correios=None)
    assert avisos.avisos_devidos(r, date(2026, 10, 9)) == [avisos.TIPO_PRAZO_VENCIDO]


def test_avisos_devidos_ignora_entregue_dba_e_encerrado():
    assert avisos.avisos_devidos(_linha(entregue_em=datetime.now(UTC)), HOJE) == []
    assert avisos.avisos_devidos(_linha(amazon_canal="dba"), HOJE) == []
    assert avisos.avisos_devidos(_linha(status_bling="Entregue"), HOJE) == []


def test_mensagem_aviso_traz_o_essencial():
    txt = avisos.mensagem_aviso(_linha(), avisos.TIPO_PRAZO_3D, HOJE)
    assert "faltam 3 dia(s)" in txt
    assert "296762 (kia)" in txt and "701-3967231-6921832" in txt
    assert "AD912266053BR (SEDEX)" in txt
    assert "Previsão Correios: 23/09" in txt and "Entregar até (Amazon): 08/10" in txt
    assert "Rosana" in txt
    assert "Acione os Correios" in txt


def test_linha_informar_formato():
    linha = avisos.linha_informar(_linha(), HOJE)
    assert linha == (
        "701-3967231-6921832 - kia - AD912266053BR (SEDEX) - Correios: previsão 23/09 - "
        "Amazon até 08/10 (faltam 3 dia(s)) - última posição: Aracaju/SE — Objeto em trânsito"
    )


# ---- run (banco) ----


@pytest.mark.asyncio
async def test_run_envia_uma_vez_e_carimba(db: AsyncSession):
    db.add(_linha())
    db.add(ThreemaInformarConfig(contexto=avisos.CONTEXTO_AUTO, recipients="ABCD1234,EFGH5678"))
    await db.commit()
    fake = FakeThreema()

    out = await avisos.run(db, hoje=HOJE, client=fake)

    assert out["pedidos"] == 1 and out["avisos"] == 2 and out["enviados"] == 2
    assert [r for _, r in fake.enviadas] == [["ABCD1234", "EFGH5678"]] * 2
    row = (await db.execute(__import__("sqlalchemy").select(Logistica))).scalars().one()
    assert row.aviso_previsao_correios_at is not None
    assert row.aviso_prazo_amazon_3d_at is not None
    assert row.aviso_prazo_amazon_vencido_at is None

    # Rodada seguinte: nada a fazer.
    fake2 = FakeThreema()
    out2 = await avisos.run(db, hoje=HOJE, client=fake2)
    assert out2["avisos"] == 0 and fake2.enviadas == []


@pytest.mark.asyncio
async def test_run_sem_destinatarios_nao_manda_nem_carimba(db: AsyncSession):
    db.add(_linha())
    await db.commit()
    fake = FakeThreema()
    out = await avisos.run(db, hoje=HOJE, client=fake)
    assert out["sem_destinatarios"] == 1 and fake.enviadas == []
    row = (await db.execute(__import__("sqlalchemy").select(Logistica))).scalars().one()
    assert row.aviso_prazo_amazon_3d_at is None


@pytest.mark.asyncio
async def test_run_falha_de_envio_nao_carimba(db: AsyncSession):
    db.add(_linha(previsao_correios=None))
    db.add(ThreemaInformarConfig(contexto=avisos.CONTEXTO_AUTO, recipients="ABCD1234"))
    await db.commit()
    out = await avisos.run(db, hoje=HOJE, client=FakeThreema(ok=False))
    assert out["falhas"] == 1 and out["enviados"] == 0
    row = (await db.execute(__import__("sqlalchemy").select(Logistica))).scalars().one()
    assert row.aviso_prazo_amazon_3d_at is None


@pytest.mark.asyncio
async def test_pedidos_em_transito_ordena_pelo_prazo_e_ignora_dba(db: AsyncSession):
    db.add(_linha(pedido_bling="1", prazo_entrega_amazon=date(2026, 10, 20)))
    db.add(_linha(pedido_bling="2", prazo_entrega_amazon=date(2026, 10, 8)))
    db.add(_linha(pedido_bling="3", amazon_canal="dba"))
    db.add(_linha(pedido_bling="4", data=date.today() - timedelta(days=200)))
    await db.commit()
    rows = await avisos.pedidos_em_transito(db)
    assert [r.pedido_bling for r in rows] == ["2", "1"]
