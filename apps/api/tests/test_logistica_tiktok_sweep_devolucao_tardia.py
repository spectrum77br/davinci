"""Devolução do TikTok que MUDA depois que a venda saiu da janela de 45 dias
do sweep (caso real 21/09: TikTok 585358874025494337 / Bling 288403, venda de
03/08, 3ª devolução cancelada pela TikTok em 19/09 por atraso do cliente).

O sweep só olhava linhas com data de venda ≤ 45 dias; o encerramento veio
depois disso e ninguém viu: o painel seguiu "Devolução solicitada", o Bling em
Aguardando Devolução — e cada vez que a equipe lançava Entregue a regra da aba
Status devolvia pra Aguardando Devolução. O botão ⟳ re-lia o pedido mas
preservava a devolução velha. Agora:

- linha velha com devolução VIVA na assinatura entra no sweep de qualquer
  jeito e o caso é re-lido POR PEDIDO (sem filtro de data);
- a lista da loja casa com linha de QUALQUER idade; venda viva sem linha é
  recriada do espelho do Bling (mesmo furo da Shopee, 19/09);
- o ⟳ (`enrich_row(reler_devolucao=True)`) re-lê o caso da linha viva.

Client falso (sem HTTP) + banco de teste."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import BlingOrder, Logistica, SituacaoBling, StoreInfo, UserRole
from app.services import logistica_rules, logistica_tiktok

pytestmark = pytest.mark.asyncio

VENDA_VELHA = "585358874025494337"
VENDA_NOVA = "585930105453577483"
VENDA_SEM_LINHA = "585379568505029884"
LOJA = "205945427"
ENTREGUE, RESOLVIDO = "83953", "545902"
AGOSTO = datetime(2026, 8, 3, 3, tzinfo=UTC)
CANCEL = "RETURN_OR_REFUND_REQUEST_CANCEL"
CANCELADA_EM = 1789787250  # 19/09/2026 03:07:30 UTC — o "encerrada automaticamente"


class FakeTikTokSweep:
    """Só o que o sweep e o enrich usam. Guarda como o returns/search foi
    chamado (janela × por pedido) pra provar quem re-lê o quê."""

    def __init__(self, *, janela: list[dict] = (), por_pedido: dict[str, list[dict]] | None = None):
        self._janela = list(janela)
        self._por_pedido = por_pedido or {}
        self.status_pedidos: list[list[str]] = []
        self.chamadas_por_pedido: list[list[str]] = []
        self.chamadas_janela = 0

    async def get_order_status_map(self, order_ids):
        self.status_pedidos.append([str(o) for o in order_ids])
        return {str(o): {"status": "COMPLETED", "update_time": CANCELADA_EM} for o in order_ids}

    async def get_return_list(self, *, order_ids=None, update_time_from=None, update_time_to=None):
        if order_ids is not None:
            ids = [str(o) for o in order_ids]
            self.chamadas_por_pedido.append(ids)
            return [d for oid in ids for d in self._por_pedido.get(oid, [])]
        assert update_time_from is not None
        self.chamadas_janela += 1
        return list(self._janela)

    # Só o enrich_row usa (build_enrichment) — o teste do ⟳ troca o build.


def _patch(monkeypatch, fake: FakeTikTokSweep) -> None:
    async def _integ(session, conta):
        return conta

    monkeypatch.setattr(logistica_tiktok, "_tiktok_integration_for_conta", _integ)
    monkeypatch.setattr(logistica_tiktok, "_build_tiktok_client", lambda s, i, *, lock=None: fake)


def _caso(oid: str, status: str, *, update_time: int, return_id: str, tipo: str = "RETURN_AND_REFUND") -> dict:
    return {
        "order_id": oid,
        "return_id": return_id,
        "return_status": status,
        "return_type": tipo,
        "create_time": update_time - 100,
        "update_time": update_time,
    }


def _historico_real() -> list[dict]:
    """As 3 devoluções do 585358874025494337, todas canceladas."""
    return [
        _caso(VENDA_VELHA, CANCEL, update_time=1786368622, return_id="4041881700033267521"),
        _caso(VENDA_VELHA, CANCEL, update_time=1787272345, return_id="4042007100767438657"),
        _caso(VENDA_VELHA, CANCEL, update_time=CANCELADA_EM, return_id="4042198884757768001"),
    ]


def _linha(oid: str, *, data: date, **campos) -> Logistica:
    campos.setdefault("meli_status", {"order_status": "COMPLETED"})
    campos.setdefault("status_bling", "Entregue")
    return Logistica(plataforma="TikTok", conta="atv", pedido_marketplace=oid, data=data, **campos)


def _linha_presa() -> Logistica:
    """A linha como estava em produção em 21/09: venda de 03/08, assinatura
    ainda com a devolução de 28/08 viva."""
    return _linha(
        VENDA_VELHA, data=AGOSTO.date(), pedido_bling="288403",
        status_bling="Aguardando Devolução",
        meli_status={
            "order_status": "COMPLETED",
            "return_status": "AWAITING_BUYER_SHIP",
            "return_type": "RETURN_AND_REFUND",
        },
        status_datas={"return_status": {"em": "2026-08-28T13:11:26+00:00", "fonte": "davinci"}},
    )


async def test_sweep_re_le_por_pedido_a_linha_velha_com_devolucao_viva(db, monkeypatch):
    """Venda de 03/08 fora da janela de 45 dias, devolução cancelada em 19/09
    (fora também da janela de 15 dias da lista da loja, se o sweep rodar
    tarde): o sweep consulta o caso POR PEDIDO, grava CANCEL com a data do
    TikTok, e a assinatura volta a ser o status do pedido — a regra
    "Devolução solicitada → Aguardando Devolução" para de disparar."""
    presa = _linha_presa()
    db.add_all([presa, _linha(VENDA_NOVA, data=date.today(), pedido_bling="294865")])
    await db.commit()
    fake = FakeTikTokSweep(janela=[], por_pedido={VENDA_VELHA: _historico_real()})
    _patch(monkeypatch, fake)

    out = await logistica_tiktok.sweep_pos_venda(db)

    await db.refresh(presa)
    assert out["velhas_vivas"] == 1 and out["returns"] == 1 and out["recriadas"] == 0
    assert presa.id in out["ids"]
    assert presa.meli_status == {
        "order_status": "COMPLETED", "return_status": CANCEL, "return_type": "RETURN_AND_REFUND",
    }
    assert presa.status_datas["return_status"] == {
        "em": "2026-09-19T03:07:30+00:00", "fonte": "plataforma",
    }
    assert logistica_rules.assinatura_para("TikTok", presa.meli_status) == "Concluído"
    # A velha entrou no lote do status vivo junto com a da janela e o caso
    # foi pedido por número do pedido — 1 chamada a mais na conta, só isso.
    assert fake.status_pedidos == [[VENDA_NOVA, VENDA_VELHA]]
    assert fake.chamadas_por_pedido == [[VENDA_VELHA]]
    assert fake.chamadas_janela == 1


async def test_sweep_ignora_linha_velha_com_caso_ja_encerrado(db, monkeypatch):
    """Linha velha cuja assinatura já diz cancelado/concluído não tem o que
    re-ler: nenhuma chamada por pedido, nada muda."""
    encerrada = _linha(
        VENDA_VELHA, data=AGOSTO.date(), pedido_bling="288403",
        meli_status={"order_status": "COMPLETED", "return_status": CANCEL, "return_type": "RETURN_AND_REFUND"},
    )
    concluida = _linha(
        VENDA_SEM_LINHA, data=AGOSTO.date(), pedido_bling="288623",
        meli_status={"order_status": "COMPLETED", "return_status": "RETURN_OR_REFUND_REQUEST_COMPLETE", "return_type": "REFUND"},
    )
    db.add_all([encerrada, concluida, _linha(VENDA_NOVA, data=date.today(), pedido_bling="294865")])
    await db.commit()
    fake = FakeTikTokSweep(janela=[], por_pedido={VENDA_VELHA: _historico_real()})
    _patch(monkeypatch, fake)

    out = await logistica_tiktok.sweep_pos_venda(db)

    assert out["velhas_vivas"] == 0 and out["returns"] == 0 and out["ids"] == []
    assert fake.chamadas_por_pedido == []
    assert fake.status_pedidos == [[VENDA_NOVA]]


async def test_sweep_casa_lista_da_loja_com_linha_fora_da_janela(db, monkeypatch):
    """Devolução NOVA (na lista de 15 dias da loja) de venda de 60 dias sem
    devolução na assinatura: antes o sweep descartava por não ter linha na
    janela; agora casa com a linha velha e a regra dispara."""
    velha = _linha(VENDA_VELHA, data=date.today() - timedelta(days=60), pedido_bling="288403")
    db.add_all([velha, _linha(VENDA_NOVA, data=date.today(), pedido_bling="294865")])
    await db.commit()
    fake = FakeTikTokSweep(
        janela=[_caso(VENDA_VELHA, "RETURN_OR_REFUND_REQUEST_PENDING", update_time=CANCELADA_EM, return_id="R9")]
    )
    _patch(monkeypatch, fake)

    out = await logistica_tiktok.sweep_pos_venda(db)

    await db.refresh(velha)
    assert out["returns"] == 1 and out["recriadas"] == 0 and velha.id in out["ids"]
    assert velha.meli_status["return_status"] == "RETURN_OR_REFUND_REQUEST_PENDING"
    assert logistica_rules.assinatura_para("TikTok", velha.meli_status) == "Devolução solicitada"
    assert fake.status_pedidos == [[VENDA_NOVA]]
    assert fake.chamadas_por_pedido == []


def _pedido(numero: str, numeroloja: str, *, situacao: str, data: datetime) -> BlingOrder:
    return BlingOrder(
        id=uuid4(),
        numero=numero,
        numeroloja=numeroloja,
        bling_id=int(numero),
        situacao=situacao,
        loja=LOJA,
        data=data,
        item_index=0,
        item_codigo="dg055.ci+a001.ci",
        item_descricao="Hotwav A17 Pro Max 12.64 - Preto + Fone",
        item_quantidade=1,
    )


async def _seed_bling(db, make_user) -> None:
    admin = await make_user(role=UserRole.ADMIN)
    db.add_all(
        [
            StoreInfo(user_id=admin.id, platform="tiktok", account_name="atv", bling_store_id=LOJA),
            SituacaoBling(id=int(ENTREGUE), nome="Entregue"),
            SituacaoBling(id=int(RESOLVIDO), nome="Resolvido"),
        ]
    )
    await db.commit()


async def test_sweep_recria_linha_da_venda_velha_com_devolucao_viva(db, make_user, monkeypatch):
    """Venda de agosto que já saiu da aba, devolução PENDING na lista da loja:
    o sweep recria a linha do espelho do Bling e já carimba a devolução.
    Venda já lançada (Resolvido) e devolução cancelada de venda velha não
    recriam nada."""
    await _seed_bling(db, make_user)
    db.add_all(
        [
            _pedido("288623", VENDA_SEM_LINHA, situacao=ENTREGUE, data=AGOSTO),
            _pedido("288409", "585359056371222486", situacao=RESOLVIDO, data=AGOSTO),
            _linha(VENDA_NOVA, data=date.today(), pedido_bling="294865"),
        ]
    )
    await db.commit()
    fake = FakeTikTokSweep(
        janela=[
            _caso(VENDA_SEM_LINHA, "RETURN_OR_REFUND_REQUEST_PENDING", update_time=CANCELADA_EM, return_id="R1"),
            _caso("585359056371222486", "AWAITING_BUYER_SHIP", update_time=CANCELADA_EM, return_id="R2"),
            _caso(VENDA_VELHA, CANCEL, update_time=CANCELADA_EM, return_id="R3"),  # sem linha, cancelada
        ]
    )
    _patch(monkeypatch, fake)

    out = await logistica_tiktok.sweep_pos_venda(db)

    nova = (
        await db.execute(select(Logistica).where(Logistica.pedido_marketplace == VENDA_SEM_LINHA))
    ).scalar_one()
    assert out["recriadas"] == 1 and out["returns"] == 1 and nova.id in out["ids"]
    assert (nova.pedido_bling, nova.plataforma, nova.conta, nova.status_bling, nova.data) == (
        "288623", "TikTok", "atv", "Entregue", date(2026, 8, 3)
    )
    assert nova.meli_status == {
        "return_status": "RETURN_OR_REFUND_REQUEST_PENDING", "return_type": "RETURN_AND_REFUND",
    }
    assert logistica_rules.assinatura_para("TikTok", nova.meli_status) == "Devolução solicitada"
    vendas = sorted((await db.execute(select(Logistica.pedido_marketplace))).scalars().all())
    assert vendas == sorted([VENDA_NOVA, VENDA_SEM_LINHA])


async def test_botao_atualizar_re_le_a_devolucao_da_linha_viva(db, monkeypatch):
    """⟳ na linha presa: além do pedido, re-lê o caso por pedido e grava o
    encerramento. Linha sem devolução viva não gasta a chamada."""
    presa = _linha_presa()
    limpa = _linha(VENDA_NOVA, data=date.today(), pedido_bling="294865")
    db.add_all([presa, limpa])
    await db.commit()
    fake = FakeTikTokSweep(por_pedido={VENDA_VELHA: _historico_real()})
    _patch(monkeypatch, fake)

    async def _build(client, order_id):
        return {"meli_status": {"order_status": "COMPLETED"}, "datas": {}}

    monkeypatch.setattr(logistica_tiktok, "build_enrichment", _build)

    assert await logistica_tiktok.enrich_row(db, presa, reler_devolucao=True)
    assert await logistica_tiktok.enrich_row(db, limpa, reler_devolucao=True)
    await db.commit()

    await db.refresh(presa)
    assert presa.meli_status["return_status"] == CANCEL
    assert presa.status_datas["return_status"]["em"] == "2026-09-19T03:07:30+00:00"
    assert logistica_rules.assinatura_para("TikTok", presa.meli_status) == "Concluído"
    assert fake.chamadas_por_pedido == [[VENDA_VELHA]]


async def test_lote_automatico_nao_re_le_a_devolucao(db, monkeypatch):
    """Sem `reler_devolucao` (enrich_recent / recarregar), o enrich preserva a
    devolução como antes e NÃO chama o returns/search — a rajada é do sweep."""
    presa = _linha_presa()
    db.add(presa)
    await db.commit()
    fake = FakeTikTokSweep(por_pedido={VENDA_VELHA: _historico_real()})
    _patch(monkeypatch, fake)

    async def _build(client, order_id):
        return {"meli_status": {"order_status": "COMPLETED"}, "datas": {}}

    monkeypatch.setattr(logistica_tiktok, "build_enrichment", _build)

    assert await logistica_tiktok.enrich_row(db, presa)
    await db.commit()

    assert presa.meli_status["return_status"] == "AWAITING_BUYER_SHIP"
    assert fake.chamadas_por_pedido == []
