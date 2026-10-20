# ruff: noqa: E501
"""Pacote de volta da Shopee pela perna REVERSA (20/10/2026).

Pedido 293379 (jlas, 260831RA27GX17): a devolução tinha código próprio da SPX
(BR265869901238B) e o pacote foi APREENDIDO pela fiscalização em 05/10, a
caminho da loja. O DaVinci só guardava o código — a localização da volta vinha
do 17track, que só conhece código dos Correios —, a Logística mostrava o
"Pedido entregue" da IDA e a apreensão ficou 15 dias sem ninguém ver.

Aqui: o leitor puro de `get_reverse_tracking_info`, o ramo de
`returns_por_pedido`, a leitura dos pedidos que já foram pra Problemas, o aviso
de ocorrência grave e as colunas `volta_*` do painel Logística.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DevolucaoRastreio, Logistica, LogisticaStatus, User, UserRole, UserStatus
from app.services import devolucao_rastreio_sync as sync_svc
from app.services import (
    logistica_match,
    logistica_meli,
    logistica_rules,
    logistica_shopee,
    logistica_tiktok,
    logistica_track,
    logistica_track_sync,
)
from app.services.devolucao_returns import ReturnInfo


def _ts(*args: int) -> int:
    return int(datetime(*args, tzinfo=UTC).timestamp())


_APREENDIDO = "Pedido apreendido pela fiscalização. Entrar em contato com a Receita Federal"
# Linha do tempo real do 293379 (Seller Center, horário de Brasília → UTC).
_REVERSA_293379 = {
    "return_sn": "2609RET293379",
    "validation_type": "seller_validation",
    "reverse_logistics_status": "LOGISTICS_PICKUP_DONE",
    "tracking_number": "BR265869901238B",
    "tracking_info": [
        {"update_time": _ts(2026, 10, 5, 22, 41, 58), "tracking_description": _APREENDIDO},
        {"update_time": _ts(2026, 9, 19, 3, 15, 2), "tracking_description": "Seu pedido saiu do centro logístico: Louveira - SP"},
        {"update_time": _ts(2026, 9, 13, 12, 30, 35), "tracking_description": "Não conseguimos entregar o pedido"},
    ],
}
_APREENDIDO_EM = datetime(2026, 10, 5, 22, 41, 58, tzinfo=UTC)


# ─── leitor puro ─────────────────────────────────────────────────────────


def test_volta_da_reversa_pega_o_evento_mais_recente():
    volta = logistica_shopee.volta_da_reversa(_REVERSA_293379)
    assert volta is not None
    assert volta.localizacao == _APREENDIDO
    assert volta.ultimo_em == _APREENDIDO_EM


def test_volta_da_reversa_le_a_perna_do_armazem():
    """Validação no armazém: a perna armazém → loja vem em outro campo."""
    resp = {
        "tracking_info": [{"update_time": 100, "tracking_description": "Entregue no armazém"}],
        "post_return_logistics_tracking_info": [{"update_time": 200, "tracking_description": "Saiu do armazém para a loja"}],
    }
    assert logistica_shopee.volta_da_reversa(resp).localizacao == "Saiu do armazém para a loja"


def test_volta_da_reversa_sem_evento_e_none():
    assert logistica_shopee.volta_da_reversa({}) is None
    assert logistica_shopee.volta_da_reversa(None) is None
    assert logistica_shopee.volta_da_reversa({"tracking_info": [{"update_time": 1, "tracking_description": " "}]}) is None


def test_le_reversa_so_codigo_que_nao_e_dos_correios():
    assert logistica_shopee.le_reversa("BR265869901238B")
    assert not logistica_shopee.le_reversa("AP444879986BR")  # o 17track lê
    assert not logistica_shopee.le_reversa("")
    assert not logistica_shopee.le_reversa(None)


def test_evento_grave_da_volta():
    assert logistica_track.evento_grave_volta(_APREENDIDO)
    assert logistica_track.evento_grave_volta("Pacote extraviado")
    # Tentativa na porta da loja é rotina, não aviso.
    assert not logistica_track.evento_grave_volta("Não conseguimos entregar o pedido")
    assert not logistica_track.evento_grave_volta("Objeto não entregue - destinatário ausente")
    assert not logistica_track.evento_grave_volta(None)


# ─── returns_por_pedido com client falso ─────────────────────────────────


class _FakeShopee:
    def __init__(self, *, returns: list[dict], reversa: dict[str, dict], details: dict[str, dict] | None = None):
        self._returns = returns
        self._reversa = reversa
        self._details = details or {}
        self.calls: list[tuple[str, str]] = []

    async def get_return_list(self, *, create_time_from, create_time_to):
        self.calls.append(("list", ""))
        return list(self._returns)

    async def get_return_detail(self, return_sn):
        self.calls.append(("detail", return_sn))
        return dict(self._details.get(return_sn, {}))

    async def get_reverse_tracking_info(self, return_sn):
        self.calls.append(("reversa", return_sn))
        if return_sn not in self._reversa:
            raise RuntimeError("shopee_reverse_tracking returns.error_reverse_logistics: sem perna")
        return dict(self._reversa[return_sn])

    async def get_tracking_info(self, order_sn):
        self.calls.append(("track", order_sn))
        return {}

    async def get_tracking_number(self, order_sn):
        return None


@pytest.fixture
def shopee_client(monkeypatch):
    holder: dict[str, _FakeShopee] = {}

    async def _integ(session, conta):
        return object()

    def _build(session, integ):
        return holder["client"]

    monkeypatch.setattr(logistica_shopee, "_shopee_integration_for_conta", _integ)
    monkeypatch.setattr(logistica_shopee, "_build_shopee_client", _build)
    return holder


def _linha(pedido: str = "293379", order_sn: str = "260831RA27GX17") -> Logistica:
    return Logistica(
        pedido_bling=pedido, plataforma="Shopee", pedido_marketplace=order_sn, conta="jlas",
        meli_status={"order_status": "COMPLETED", "logistics_status": "LOGISTICS_DELIVERY_DONE", "return_status": "ACCEPTED"},
        rastreio="BR263585950911RSPXLM16595187", data=datetime(2026, 8, 30, tzinfo=UTC).date(),
    )


_CASO = {
    "return_sn": "2609RET293379", "order_sn": "260831RA27GX17", "status": "ACCEPTED",
    "tracking_number": "BR265869901238B", "needs_logistics": True,
    "create_time": _ts(2026, 9, 5, 12), "update_time": _ts(2026, 9, 24, 18, 5),
}


@pytest.mark.asyncio
async def test_caso_com_codigo_spx_le_a_volta_na_shopee(shopee_client):
    client = _FakeShopee(
        returns=[_CASO], reversa={"2609RET293379": _REVERSA_293379},
        details={"2609RET293379": {**_CASO, "reverse_logistics_status": "LOGISTICS_PICKUP_DONE"}},
    )
    shopee_client["client"] = client

    info = (await logistica_shopee.returns_por_pedido(None, [_linha()]))["293379"]

    assert info.tracking == "BR265869901238B"
    assert info.localizacao == _APREENDIDO
    assert info.localizacao_em == _APREENDIDO_EM
    assert info.updated_at == _APREENDIDO_EM  # o evento do pacote é a última mexida
    assert info.entregue_em is None  # apreendido não é chegada
    assert ("reversa", "2609RET293379") in client.calls
    assert ("track", "260831RA27GX17") not in client.calls  # a ida não é olhada


@pytest.mark.asyncio
async def test_codigo_dos_correios_fica_com_o_17track(shopee_client):
    caso = {**_CASO, "tracking_number": "AP444879986BR"}
    client = _FakeShopee(returns=[caso], reversa={"2609RET293379": _REVERSA_293379})
    shopee_client["client"] = client

    info = (await logistica_shopee.returns_por_pedido(None, [_linha()]))["293379"]

    assert info.localizacao is None
    assert not [c for c in client.calls if c[0] == "reversa"]


@pytest.mark.asyncio
async def test_pacote_ja_chegou_nao_pergunta_a_reversa(shopee_client):
    client = _FakeShopee(returns=[_CASO], reversa={"2609RET293379": _REVERSA_293379})
    shopee_client["client"] = client

    await logistica_shopee.returns_por_pedido(None, [_linha()], ja_entregues={"293379"})

    assert not [c for c in client.calls if c[0] == "reversa"]


@pytest.mark.asyncio
async def test_so_reembolso_nao_pergunta_a_reversa(shopee_client):
    caso = {**_CASO, "needs_logistics": False}
    client = _FakeShopee(returns=[caso], reversa={}, details={"2609RET293379": caso})
    shopee_client["client"] = client

    await logistica_shopee.returns_por_pedido(None, [_linha()])

    assert not [c for c in client.calls if c[0] == "reversa"]


@pytest.mark.asyncio
async def test_erro_na_reversa_nao_derruba_o_caso(shopee_client):
    client = _FakeShopee(returns=[_CASO], reversa={})  # Shopee devolve erro
    shopee_client["client"] = client

    info = (await logistica_shopee.returns_por_pedido(None, [_linha()]))["293379"]

    assert info.status == "ACCEPTED" and info.tracking == "BR265869901238B"
    assert info.localizacao is None


# ─── o sync grava e avisa ────────────────────────────────────────────────


@pytest.fixture
def avisos(monkeypatch):
    enviados: list[tuple[list, str]] = []

    async def _avisar(graves, *, titulo="Rastreio dos Correios com ocorrência GRAVE"):
        enviados.append((list(graves), titulo))

    monkeypatch.setattr(logistica_track_sync, "avisar_graves", _avisar)

    async def _register(numbers):
        return {"ok": list(numbers)}

    monkeypatch.setattr(logistica_track, "register", _register)
    return enviados


def _sem_outros_marketplaces(monkeypatch):
    async def _vazio(session, linhas, **kw):
        return {}

    monkeypatch.setattr(logistica_tiktok, "returns_por_pedido", _vazio, raising=False)
    monkeypatch.setattr(logistica_meli, "returns_por_pedido", _vazio, raising=False)


@pytest.mark.asyncio
async def test_sync_grava_a_volta_e_avisa_a_apreensao_uma_vez(db: AsyncSession, monkeypatch, avisos):
    pedido = f"7{uuid.uuid4().hex[:6]}"
    db.add(Logistica(pedido_bling=pedido, plataforma="Shopee", pedido_marketplace=f"mk-{pedido}", conta="jlas"))
    await db.commit()
    info = ReturnInfo(
        fonte="shopee", status="ACCEPTED", tracking="BR265869901238B", carrier=None,
        created_at=datetime(2026, 9, 5, tzinfo=UTC), updated_at=_APREENDIDO_EM, return_id="2609RET293379",
        localizacao=_APREENDIDO, localizacao_em=_APREENDIDO_EM,
    )

    async def _shopee(session, linhas, **kw):
        return {pedido: info}

    monkeypatch.setattr(logistica_shopee, "returns_por_pedido", _shopee, raising=False)
    _sem_outros_marketplaces(monkeypatch)

    await sync_svc.run(db, pedidos=[pedido])

    row = (await db.execute(select(DevolucaoRastreio).where(DevolucaoRastreio.pedido_bling == pedido))).scalar_one()
    assert row.localizacao_auto == _APREENDIDO
    assert row.localizacao_auto_data == _APREENDIDO_EM
    assert row.pacote_entregue_em is None
    assert len(avisos) == 1
    graves, titulo = avisos[0]
    assert titulo == "Pacote de DEVOLUÇÃO com ocorrência GRAVE"
    assert graves == [(pedido, "jlas, Shopee", f"volta BR265869901238B — {_APREENDIDO}")]

    # Rodada seguinte com o mesmo evento: não repete o aviso.
    await sync_svc.run(db, pedidos=[pedido])
    assert len(avisos) == 1


@pytest.mark.asyncio
async def test_problemas_segue_lendo_a_volta_da_shopee(db: AsyncSession, monkeypatch, avisos):
    """O pedido saiu da aba Acompanhamento pra Problemas com o pacote retido: a
    rodada completa relê a reversa pelo return_sn já gravado."""
    pedido = f"8{uuid.uuid4().hex[:6]}"
    outro = f"9{uuid.uuid4().hex[:6]}"  # Correios: fica com o 17track
    chegou = f"6{uuid.uuid4().hex[:6]}"  # pacote já chegou: nada a ler
    for p in (pedido, outro, chegou):
        db.add(Logistica(pedido_bling=p, plataforma="Shopee", pedido_marketplace=f"mk-{p}", conta="jlas"))
    db.add(DevolucaoRastreio(
        pedido_bling=pedido, rastreio_auto="BR265869901238B", devolucao_id_auto="2609RET293379",
        fonte_auto="shopee", devolucao_status_auto="ACCEPTED",
    ))
    db.add(DevolucaoRastreio(
        pedido_bling=outro, rastreio_auto="AP444879986BR", devolucao_id_auto="2609OUTRO", fonte_auto="shopee",
    ))
    db.add(DevolucaoRastreio(
        pedido_bling=chegou, rastreio_auto="BR2600000000X", devolucao_id_auto="2609CHEGOU", fonte_auto="shopee",
        pacote_entregue_em=datetime(2026, 10, 1, tzinfo=UTC),
    ))
    await db.commit()

    async def _situacao(session, situacao):
        return [pedido, outro, chegou] if situacao == "83960" else []

    monkeypatch.setattr(sync_svc, "_pedidos_na_situacao", _situacao)
    perguntados: list[tuple[str, str]] = []

    async def _voltas(session, casos):
        perguntados.extend((linha.pedido_bling, rsn) for linha, rsn in casos)
        return {pedido: logistica_shopee.VoltaReversa(_APREENDIDO, _APREENDIDO_EM)}

    monkeypatch.setattr(logistica_shopee, "voltas_por_pedido", _voltas)

    resumo = await sync_svc.run(db)

    assert perguntados == [(pedido, "2609RET293379")]
    row = (await db.execute(select(DevolucaoRastreio).where(DevolucaoRastreio.pedido_bling == pedido))).scalar_one()
    assert row.localizacao_auto == _APREENDIDO
    assert row.devolucao_atualizada_em == _APREENDIDO_EM
    assert resumo["volta_fora_da_aba"] == 1 and resumo["voltas_graves"] == 1
    assert [g[0] for g in avisos[0][0]] == [pedido]


@pytest.mark.asyncio
async def test_voltas_por_pedido_usa_o_return_sn_gravado(shopee_client):
    client = _FakeShopee(returns=[], reversa={"2609RET293379": _REVERSA_293379})
    shopee_client["client"] = client

    out = await logistica_shopee.voltas_por_pedido(None, [(_linha(), "2609RET293379"), (_linha("1", "x"), "SEMPERNA")])

    assert out["293379"].localizacao == _APREENDIDO
    assert "1" not in out  # erro da Shopee nessa: fica de fora, sem derrubar
    assert not [c for c in client.calls if c[0] == "list"]  # nada de varrer a loja


# ─── painel Logística ────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def viewer(db: AsyncSession) -> User:
    email = f"vw-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(
        open_id=f"email:{email}", email=email, role=UserRole.USER, status=UserStatus.ACTIVE,
        permissions={"logistica": {"view": True}},
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest.mark.asyncio
async def test_painel_mostra_a_volta_ao_lado_da_ida(
    client: AsyncClient, db: AsyncSession, viewer: User, auth_as: Callable[[User | None], None]
):
    pedido = f"5{uuid.uuid4().hex[:6]}"
    sem_volta = f"4{uuid.uuid4().hex[:6]}"
    db.add(Logistica(
        pedido_bling=pedido, plataforma="Shopee", pedido_marketplace=f"mk-{pedido}", conta="jlas",
        rastreio="BR263585950911RSPXLM16595187", localizacao="Pedido entregue",
    ))
    db.add(Logistica(pedido_bling=sem_volta, plataforma="Shopee", pedido_marketplace=f"mk-{sem_volta}", conta="jlas"))
    db.add(DevolucaoRastreio(
        pedido_bling=pedido, rastreio_auto="BR265869901238B", localizacao_auto=_APREENDIDO,
        localizacao_auto_data=_APREENDIDO_EM, fonte_auto="shopee",
    ))
    await db.commit()
    auth_as(viewer)

    linhas = {c["pedido_bling"]: c for c in (await client.get("/api/logistica?plataforma=shopee")).json()}

    c = linhas[pedido]
    assert c["rastreio"] == "BR263585950911RSPXLM16595187"  # a ida continua lá
    assert c["localizacao"] == "Pedido entregue"
    assert c["volta_rastreio"] == "BR265869901238B"
    assert c["volta_localizacao"] == _APREENDIDO
    assert c["volta_localizacao_em"].startswith("2026-10-05T22:41:58")
    assert c["volta_grave"] is True
    s = linhas[sem_volta]
    assert s["volta_rastreio"] is None and s["volta_localizacao"] is None and s["volta_grave"] is False


@pytest.mark.asyncio
async def test_painel_volta_manual_vence_o_automatico(
    client: AsyncClient, db: AsyncSession, viewer: User, auth_as: Callable[[User | None], None]
):
    pedido = f"3{uuid.uuid4().hex[:6]}"
    db.add(Logistica(pedido_bling=pedido, plataforma="Shopee", pedido_marketplace=f"mk-{pedido}", conta="jlas"))
    db.add(DevolucaoRastreio(
        pedido_bling=pedido, rastreio="QB123456789BR", localizacao="Chegou na agência",
        localizacao_data=datetime(2026, 10, 10, tzinfo=UTC),
        rastreio_auto="BR265869901238B", localizacao_auto=_APREENDIDO, localizacao_auto_data=_APREENDIDO_EM,
    ))
    await db.commit()
    auth_as(viewer)

    c = next(x for x in (await client.get("/api/logistica")).json() if x["pedido_bling"] == pedido)

    assert c["volta_rastreio"] == "QB123456789BR"
    assert c["volta_localizacao"] == "Chegou na agência"
    assert c["volta_localizacao_em"].startswith("2026-10-10")
    assert c["volta_grave"] is False


# ─── a Localização da linha vira a da volta (regras "Localização contém") ──
# Vinicius 20/10: "as regras eu crio pela localização — se não aparecer preso
# aí eu não consigo criar regra de status".

_DEVOLUCAO_IDA_ENTREGUE = {
    "order_status": "COMPLETED", "logistics_status": "LOGISTICS_DELIVERY_DONE", "return_status": "ACCEPTED",
}


def test_volta_manda_na_localizacao_so_com_ida_entregue_e_devolucao_viva():
    assert logistica_rules.shopee_volta_manda_na_localizacao(_DEVOLUCAO_IDA_ENTREGUE)
    # Ida falhou: a volta é o próprio rastreio da ida (o enrich segue lendo).
    assert not logistica_rules.shopee_volta_manda_na_localizacao(
        {**_DEVOLUCAO_IDA_ENTREGUE, "logistics_status": "LOGISTICS_DELIVERY_FAILED"}
    )
    assert not logistica_rules.shopee_volta_manda_na_localizacao(
        {**_DEVOLUCAO_IDA_ENTREGUE, "return_status": "CANCELLED"}
    )
    assert not logistica_rules.shopee_volta_manda_na_localizacao({"order_status": "COMPLETED"})
    assert not logistica_rules.shopee_volta_manda_na_localizacao(None)


class _FakeIda:
    """Só o que o enrich_row usa: a ida diz "Pedido entregue"."""

    def __init__(self, logistics_status: str):
        self._log = logistics_status

    async def get_order_status_map(self, sns):
        return {str(sn): {"status": "COMPLETED", "update_time": None} for sn in sns}

    async def get_tracking_number(self, order_sn):
        return "BR263585950911RSPXLM16595187"

    async def get_tracking_info(self, order_sn):
        return {
            "logistics_status": self._log,
            "tracking_info": [{"update_time": _ts(2026, 9, 1, 14, 10), "description": "Pedido entregue"}],
        }


@pytest.mark.asyncio
async def test_enrich_da_ida_nao_apaga_a_volta_da_localizacao():
    linha = _linha()
    linha.localizacao = _APREENDIDO
    await logistica_shopee.enrich_row(None, linha, client_cache={"jlas": _FakeIda("LOGISTICS_DELIVERY_DONE")})
    assert linha.localizacao == _APREENDIDO
    assert linha.meli_status["return_status"] == "ACCEPTED"  # segue preservado


@pytest.mark.asyncio
async def test_enrich_sem_devolucao_segue_lendo_a_ida():
    linha = _linha()
    linha.meli_status = {"order_status": "SHIPPED"}
    linha.localizacao = "Pedido postado Piracicaba - SP"
    await logistica_shopee.enrich_row(None, linha, client_cache={"jlas": _FakeIda("LOGISTICS_DELIVERY_DONE")})
    assert linha.localizacao == "Pedido entregue"


@pytest.mark.asyncio
async def test_problemas_escreve_a_volta_na_localizacao_da_logistica(db: AsyncSession, monkeypatch, avisos):
    pedido = f"2{uuid.uuid4().hex[:6]}"
    rts = f"1{uuid.uuid4().hex[:6]}"  # ida falhou: Localização continua da ida
    db.add(Logistica(
        pedido_bling=pedido, plataforma="Shopee", pedido_marketplace=f"mk-{pedido}", conta="jlas",
        meli_status=dict(_DEVOLUCAO_IDA_ENTREGUE), localizacao="Pedido entregue",
    ))
    db.add(Logistica(
        pedido_bling=rts, plataforma="Shopee", pedido_marketplace=f"mk-{rts}", conta="jlas",
        meli_status={**_DEVOLUCAO_IDA_ENTREGUE, "logistics_status": "LOGISTICS_DELIVERY_FAILED"},
        localizacao="Pedido será devolvido ao vendedor",
    ))
    # A volta do primeiro JÁ estava gravada (rodada anterior): mesmo sem evento
    # novo, a Localização da Logística passa a recebê-la.
    db.add(DevolucaoRastreio(
        pedido_bling=pedido, rastreio_auto="BR265869901238B", devolucao_id_auto="2609A", fonte_auto="shopee",
        localizacao_auto=_APREENDIDO, localizacao_auto_data=_APREENDIDO_EM,
    ))
    db.add(DevolucaoRastreio(pedido_bling=rts, rastreio_auto="BR2600000001X", devolucao_id_auto="2609B", fonte_auto="shopee"))
    await db.commit()

    async def _situacao(session, situacao):
        return [pedido, rts] if situacao == "83960" else []

    async def _voltas(session, casos):
        return {
            pedido: logistica_shopee.VoltaReversa(_APREENDIDO, _APREENDIDO_EM),
            rts: logistica_shopee.VoltaReversa("Pedido apreendido pela fiscalização", _APREENDIDO_EM),
        }

    monkeypatch.setattr(sync_svc, "_pedidos_na_situacao", _situacao)
    monkeypatch.setattr(logistica_shopee, "voltas_por_pedido", _voltas)

    resumo = await sync_svc.run(db)

    linhas = {
        r.pedido_bling: r
        for r in (await db.execute(select(Logistica).where(Logistica.pedido_bling.in_([pedido, rts])))).scalars()
    }
    assert linhas[pedido].localizacao == _APREENDIDO
    assert linhas[rts].localizacao == "Pedido será devolvido ao vendedor"
    assert resumo["logistica_localizacao_da_volta"] == 1
    # Só o que VIROU grave nesta rodada avisa: o primeiro já estava gravado.
    assert [g[0] for g in avisos[0][0]] == [rts]

    # E a regra "Localização contém: apreendido" passa a casar com a linha.
    regra = LogisticaStatus(
        plataforma="Shopee", status_plataforma="Devolução solicitada", status_atual="Problemas",
        localizacao_contem="apreendido", monitoramento=True,
    )
    normal = LogisticaStatus(plataforma="Shopee", status_plataforma="Devolução solicitada", status_atual="Problemas")
    cands = logistica_match.find_matching_rules(
        [normal, regra], assinatura="Devolução solicitada", plataforma="Shopee",
        localizacao=linhas[pedido].localizacao,
    )
    assert cands[0] is regra


@pytest.mark.asyncio
async def test_acompanhamento_escreve_a_volta_na_localizacao_da_logistica(db: AsyncSession, monkeypatch, avisos):
    pedido = f"0{uuid.uuid4().hex[:6]}"
    db.add(Logistica(
        pedido_bling=pedido, plataforma="Shopee", pedido_marketplace=f"mk-{pedido}", conta="jlas",
        meli_status=dict(_DEVOLUCAO_IDA_ENTREGUE), localizacao="Pedido entregue",
    ))
    await db.commit()
    info = ReturnInfo(
        fonte="shopee", status="ACCEPTED", tracking="BR265869901238B", carrier=None,
        created_at=None, updated_at=_APREENDIDO_EM, return_id="2609C",
        localizacao="Seu pedido saiu do centro logístico: Louveira - SP", localizacao_em=_APREENDIDO_EM,
    )

    async def _shopee(session, linhas, **kw):
        return {pedido: info}

    monkeypatch.setattr(logistica_shopee, "returns_por_pedido", _shopee, raising=False)
    _sem_outros_marketplaces(monkeypatch)

    await sync_svc.run(db, pedidos=[pedido])

    linha = (await db.execute(select(Logistica).where(Logistica.pedido_bling == pedido))).scalar_one()
    assert linha.localizacao == "Seu pedido saiu do centro logístico: Louveira - SP"
    assert avisos == []  # movimento normal não avisa
