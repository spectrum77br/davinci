# ruff: noqa: E501
"""Caso NOVO da TikTok num pedido cujo chamado já saiu da varredura (25/09, 292491).

O 292491 (TikTok ATV): o comprador devolveu o aparelho riscado, recusamos o pacote,
ele contestou e a TikTok decidiu a arbitragem A FAVOR DO VENDEDOR (devolução
cancelada). Com "ganhamos" o chamado sai da varredura das :25 na hora — e depois de
concluído também. Vinicius: "se eu fechar e o cliente reabrir, meu sistema vai
pegar?". Não pegava: o caso novo (o suporte da TikTok reabre depois da disputa, como
no 293798) só aparecia na Logística. Agora o sweep da Logística devolve o chamado pra
fila acompanhando o caso novo; e o vigia do só reembolso não confunde a resposta do
caso antigo com a do novo."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import Chamado, ChamadoMensagem, Logistica
from app.services import chamados as chamados_svc
from app.services import chamados_devolucao_sync as sync
from app.services import logistica_tiktok

pytestmark = pytest.mark.asyncio

OID = "585729776547038644"
RID_VELHO = "4042190857797273012"
RID_NOVO = "4042421234567890123"
CRIADO_CHAMADO = datetime(2026, 9, 23, 17, 15, tzinfo=UTC)
DECIDIDO = datetime(2026, 9, 24, 13, 25, tzinfo=UTC)
CASO_NOVO_EM = int(datetime(2026, 9, 26, 12, 0, tzinfo=UTC).timestamp())
PENDENTE = "RETURN_OR_REFUND_REQUEST_PENDING"
CANCEL = "RETURN_OR_REFUND_REQUEST_CANCEL"


def _caso(rid: str, status: str, *, criado: int, tipo: str = "REFUND") -> dict:
    return {
        "order_id": OID, "return_id": rid, "return_status": status, "return_type": tipo,
        "create_time": criado, "update_time": criado + 60,
    }


def _velho() -> dict:
    return _caso(RID_VELHO, CANCEL, criado=int(datetime(2026, 9, 3, 23, 28, tzinfo=UTC).timestamp()),
                 tipo="RETURN_AND_REFUND")


class _FakeSweep:
    def __init__(self, casos: list[dict]):
        self.casos = casos

    async def get_order_status_map(self, order_ids):
        return {str(o): {"status": "DELIVERED", "update_time": CASO_NOVO_EM} for o in order_ids}

    async def get_return_list(self, *, order_ids=None, update_time_from=None, update_time_to=None):
        return list(self.casos)


def _patch(monkeypatch, fake) -> None:
    async def _integ(session, conta):
        return conta

    monkeypatch.setattr(logistica_tiktok, "_tiktok_integration_for_conta", _integ)
    monkeypatch.setattr(logistica_tiktok, "_build_tiktok_client", lambda s, i, *, lock=None: fake)


def _chamado(**kw) -> Chamado:
    base = {
        "data": date(2026, 9, 23), "pedido_bling": "292491", "pedido_marketplace": OID,
        "plataforma": "tiktok", "conta": "TikTok ATV", "origem": "devolucao",
        "origem_ref": str(uuid4()), "chamado": RID_VELHO, "canal": "api",
        "created_at": CRIADO_CHAMADO,
        "status_plataforma": chamados_svc.STATUS_GANHAMOS, "status_plataforma_at": DECIDIDO,
    }
    base.update(kw)
    return Chamado(**base)


async def _seed(db, ch: Chamado) -> None:
    db.add(Logistica(plataforma="TikTok", conta="atv", pedido_marketplace=OID, pedido_bling="292491",
                     data=date.today(), status_bling="Entregue",
                     meli_status={"order_status": "DELIVERED", "return_status": CANCEL,
                                  "return_type": "RETURN_AND_REFUND"}))
    db.add(ch)
    await db.commit()


async def _falas(db, ch_id) -> list[ChamadoMensagem]:
    return list((await db.execute(
        select(ChamadoMensagem).where(ChamadoMensagem.chamado_id == ch_id)
        .order_by(ChamadoMensagem.created_at))).scalars().all())


async def test_caso_novo_reabre_chamado_ganho_e_passa_a_acompanhar(db, monkeypatch):
    """O cenário do Vinicius: arbitragem ganha, chamado em Encerrado (ganhamos) e o suporte
    abre um caso novo de só reembolso. O sweep da Logística reabre: o chamado acompanha o
    caso novo, sai do Encerrado, volta pra varredura das :25 e fica em Análise Humano."""
    ch = _chamado()
    await _seed(db, ch)
    _patch(monkeypatch, _FakeSweep([_velho(), _caso(RID_NOVO, PENDENTE, criado=CASO_NOVO_EM)]))

    out = await logistica_tiktok.sweep_pos_venda(db)

    await db.refresh(ch)
    assert out["chamados_reabertos"] == 1, out
    assert ch.chamado == RID_NOVO and ch.status_plataforma is None and not ch.resolvido
    msgs = await _falas(db, ch.id)
    fala = next(m for m in msgs if m.direcao == "recebida")
    assert "caso NOVO" in fala.texto and RID_NOVO in fala.texto and RID_VELHO in fala.texto, fala.texto
    assert "(ganhamos)" in fala.texto and "só reembolso" in fala.texto and "26/09 09:00" in fala.texto, fala.texto
    assert fala.created_at == datetime.fromtimestamp(CASO_NOVO_EM, UTC)
    aba, _q, motivo = chamados_svc.status_e_motivo_da_aba(
        ch, ultima_fala=fala, ultima_analise=None, analise_pede_humano=False
    )
    assert aba == chamados_svc.ABA_ANALISE_HUMANO, (aba, motivo)
    # volta pra varredura das :25 (antes, ganhamos saía na hora)
    assert (await db.execute(
        select(Chamado.id).where(sync._le_na_varredura(datetime.now(UTC)), Chamado.id == ch.id)
    )).scalar_one_or_none() == ch.id

    # passada seguinte: já acompanha o caso novo — nada muda, nada duplica
    out2 = await logistica_tiktok.sweep_pos_venda(db)
    assert out2["chamados_reabertos"] == 0, out2
    assert len(await _falas(db, ch.id)) == len(msgs)


async def test_caso_novo_reabre_chamado_concluido_por_pessoa(db, monkeypatch):
    ch = _chamado(resolvido=True, resolvido_at=DECIDIDO + timedelta(hours=2))
    await _seed(db, ch)
    _patch(monkeypatch, _FakeSweep([_caso(RID_NOVO, PENDENTE, criado=CASO_NOVO_EM, tipo="RETURN_AND_REFUND")]))

    out = await logistica_tiktok.sweep_pos_venda(db)

    await db.refresh(ch)
    assert out["chamados_reabertos"] == 1 and not ch.resolvido and ch.resolvido_at is None
    textos = [m.texto for m in await _falas(db, ch.id)]
    assert "Chamado reaberto por Logística" in textos, textos
    assert any("o chamado estava concluído" in t and "devolução" in t for t in textos), textos


@pytest.mark.parametrize(
    ("chamado_kw", "caso"),
    [
        # chamado ainda na varredura: quem troca de caso é o próprio sync
        ({"status_plataforma": chamados_svc.STATUS_AGUARDANDO}, _caso(RID_NOVO, PENDENTE, criado=CASO_NOVO_EM)),
        # o comprador abriu e já cancelou: nada pra fazer
        ({}, _caso(RID_NOVO, CANCEL, criado=CASO_NOVO_EM)),
        # caso de ANTES do chamado não é reabertura
        ({}, _caso(RID_NOVO, PENDENTE, criado=int((CRIADO_CHAMADO - timedelta(days=5)).timestamp()))),
        # o mesmo caso que o chamado já acompanha
        ({}, _velho()),
        # caso aberto na tela (protocolo do Seller Center, sem API) fica com o leitor
        ({"chamado_de_tela": True}, _caso(RID_NOVO, PENDENTE, criado=CASO_NOVO_EM)),
    ],
    ids=["ainda_na_varredura", "caso_novo_cancelado", "caso_antigo", "mesmo_caso", "caso_de_tela"],
)
async def test_nao_reabre(db, monkeypatch, chamado_kw, caso):
    ch = _chamado(**chamado_kw)
    antes = (ch.chamado, ch.status_plataforma)
    await _seed(db, ch)

    n = await sync.reabrir_por_caso_novo_tiktok(db, {OID: caso})

    await db.commit()
    await db.refresh(ch)
    assert n == 0 and (ch.chamado, ch.status_plataforma) == antes
    assert await _falas(db, ch.id) == []


async def test_so_o_chamado_mais_recente_do_pedido_conta(db):
    """Pedido com um chamado antigo concluído e um atual ainda lido: o atual manda — o
    antigo não reabre por causa do caso novo que o atual já acompanha."""
    velho = _chamado(resolvido=True, created_at=CRIADO_CHAMADO - timedelta(days=20))
    atual = _chamado(chamado=RID_NOVO, status_plataforma=chamados_svc.STATUS_AGUARDANDO,
                     created_at=CRIADO_CHAMADO)
    db.add_all([velho, atual])
    await db.commit()

    n = await sync.reabrir_por_caso_novo_tiktok(db, {OID: _caso(RID_NOVO, PENDENTE, criado=CASO_NOVO_EM)})

    await db.refresh(velho)
    assert n == 0 and velho.resolvido and velho.chamado == RID_VELHO
