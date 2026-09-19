"""Devolução do ML com revisão: "entregue" no GALPÃO do ML não é "Chegou em".

Vinicius, 19/09/2026: 2000014922944893 (295359), 2000014722914581 (292659),
2000018302595310 (294679) e 2000014729924813 (292729) apareciam na tela
Devoluções com "Chegou em 18/09" enquanto o ML dizia "Devolução a caminho.
Vamos revisar o produto". O ML (`intermediate_check`) manda o pacote primeiro
pro galpão dele em Cajamar (`shipments[].destination.name = warehouse`) e só
depois cria a perna pra loja (`return_from_triage` → `seller_address`, com
OUTRO rastreio). Cinco caminhos liam "entregue" nessa perna como chegada na
loja: o texto "entregue ao destinatário" do 17track, o pull dos Correios
("Delivered"), o push do 17track, o `return_status = delivered` da Logística
e o `devolucao_status_auto = delivered` do sync. Agora todos olham o destino
da perna (`devolucao_destino_auto` / `meli_status.return_destino`), e a
Logística velha (ainda com o `delivered` do galpão) não manda mais sozinha.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete

from app.models import DevolucaoRastreio, Logistica, User, UserRole, UserStatus
from app.routers.devolutions import _chegou_em, _com_status_da_devolucao
from app.services import devolucao_rastreio_sync as svc
from app.services import logistica_meli, logistica_rules, logistica_shopee, logistica_tiktok
from app.services.devolucao_returns import ReturnInfo

# 18/09/2026 21:30 BRT — o carimbo real que o pull dos Correios deixou no 295359.
GALPAO_EM = datetime(2026, 9, 19, 0, 30, 53, tzinfo=UTC)


# ── Regras puras ──────────────────────────────────────────────────────────


def test_return_status_delivered_no_galpao_nao_e_chegada():
    """292729: `return_status = delivered` da perna do galpão (Cajamar)."""
    ms = {"order_status": "cancelled", "return_status": "delivered", "return_destino": "warehouse"}
    sd = {"return_status": {"em": "2026-09-18T13:12:38+00:00", "fonte": "aprox"}}
    assert logistica_rules.data_retorno_concluido("Mercado Livre", ms, sd) is None
    # Perna da loja (ou linha antiga sem a chave): segue valendo.
    assert logistica_rules.data_retorno_concluido(
        "Mercado Livre", {**ms, "return_destino": "seller_address"}, sd
    ) == "2026-09-18T13:12:38+00:00"
    sem_chave = {k: v for k, v in ms.items() if k != "return_destino"}
    assert logistica_rules.data_retorno_concluido("Mercado Livre", sem_chave, sd) == (
        "2026-09-18T13:12:38+00:00"
    )
    # Cancelamento por não entrega (pacote volta pelo envio de ida): o
    # `returned` é a loja mesmo — o destino da devolução não bloqueia.
    ida = {"ship_substatus": "returned", "return_destino": "warehouse"}
    assert logistica_rules.data_retorno_concluido(
        "Mercado Livre", ida, {"ship_substatus": {"em": "2026-09-17T10:00:00+00:00"}}
    ) == "2026-09-17T10:00:00+00:00"


def test_status_pt_diz_que_esta_no_galpao():
    assert logistica_rules.devolucao_status_pt(
        "Mercado Livre", {"return_status": "delivered", "return_destino": "warehouse"}
    ) == "Devolução no galpão do ML — em revisão, ainda não chegou na loja"
    assert logistica_rules.devolucao_status_pt(
        "Mercado Livre", {"return_status": "shipped", "return_destino": "warehouse"}
    ) == "Devolução a caminho do galpão do ML (revisão)"
    assert logistica_rules.devolucao_status_pt(
        "Mercado Livre", {"return_status": "delivered", "return_destino": "seller_address"}
    ) == "Devolução entregue ao vendedor"
    assert logistica_rules.devolucao_status_pt(
        "Mercado Livre", {"return_status": "delivered"}
    ) == "Devolução entregue ao vendedor"


def test_chegou_em_ignora_todos_os_caminhos_enquanto_a_perna_e_do_galpao():
    base = dict(
        plataforma="Mercado Livre",
        status_datas={"return_status": {"em": "2026-09-18T13:12:38+00:00", "fonte": "aprox"}},
        fonte_auto="ml",
        devolucao_atualizada_em=datetime(2026, 9, 18, 20, 35, tzinfo=UTC),
        devolucao_destino_auto="warehouse",
    )
    # 295359/292659/294679: carimbo deixado pelos Correios na perna do galpão.
    assert _chegou_em(
        **base, meli_status={"return_status": "shipped"}, devolucao_status_auto="shipped",
        pacote_entregue_em=GALPAO_EM,
    ) is None
    # 292729: return_status delivered na Logística SEM a chave return_destino
    # (linha escondida não é re-enriquecida) — o destino do sync manda.
    assert _chegou_em(
        **base, meli_status={"return_status": "delivered"}, devolucao_status_auto="delivered",
    ) is None
    # 2ª perna aberta e ainda A CAMINHO (sync: seller_address + shipped) com a
    # Logística velha ainda dizendo `delivered` do galpão (linha escondida não
    # é re-enriquecida): NÃO pode voltar a mostrar a data de Cajamar.
    loja = {**base, "devolucao_destino_auto": "seller_address"}
    assert _chegou_em(
        **loja, meli_status={"return_status": "delivered"}, devolucao_status_auto="shipped",
    ) is None
    # Mesma linha, perna da loja entregue: aí sim.
    assert _chegou_em(
        **loja, meli_status={"return_status": "delivered"}, devolucao_status_auto="delivered",
    ) == date(2026, 9, 18)  # 13:12 UTC → 18/09 em SP
    assert _chegou_em(
        **loja, meli_status={"return_status": "shipped"}, devolucao_status_auto="shipped",
        pacote_entregue_em=GALPAO_EM,
    ) == date(2026, 9, 18)
    # Devolução direta (sem revisão) de linha antiga: destino None não bloqueia.
    antiga = {**base, "devolucao_destino_auto": None}
    assert _chegou_em(
        **antiga, meli_status={"return_status": "shipped"}, devolucao_status_auto="shipped",
        pacote_entregue_em=GALPAO_EM,
    ) == date(2026, 9, 18)
    # TikTok/Shopee nunca têm destino: nada muda pra eles.
    assert _chegou_em(
        plataforma="TikTok", meli_status=None, status_datas=None, fonte_auto="tiktok",
        devolucao_status_auto="RETURN_OR_REFUND_REQUEST_SUCCESS",
        devolucao_atualizada_em=GALPAO_EM, devolucao_destino_auto=None,
    ) == date(2026, 9, 18)


def test_localizacao_da_tela_conta_o_galpao_e_nao_diz_entregue_ao_vendedor():
    d = _com_status_da_devolucao(
        {"localizacao": "Piracicaba/SP — entregue"},
        localizacao_manual=None,
        lg_plataforma="Mercado Livre",
        lg_meli_status={"return_status": "delivered"},
        status_auto="delivered",
        fonte_auto="ml",
        localizacao_auto="Cajamar/SP — Objeto entregue ao destinatário",
        pacote_entregue_em=GALPAO_EM,
        destino_auto="warehouse",
    )
    assert d["localizacao"] == (
        "Devolução no galpão do ML — em revisão, ainda não chegou na loja"
        " · Cajamar/SP — Objeto entregue ao destinatário"
    )
    # 295359: sem texto do 17track, só o carimbo — não pode virar "Pacote
    # entregue ao vendedor".
    d = _com_status_da_devolucao(
        {"localizacao": None},
        localizacao_manual=None,
        lg_plataforma="Mercado Livre",
        lg_meli_status={"return_status": "shipped"},
        status_auto="shipped",
        fonte_auto="ml",
        localizacao_auto=None,
        pacote_entregue_em=GALPAO_EM,
        destino_auto="warehouse",
    )
    assert d["localizacao"] == "Devolução a caminho do galpão do ML (revisão)"


# ── ReturnInfo do ML carrega o destino da perna ───────────────────────────


async def test_return_info_do_ml_traz_o_destino_da_perna_atual():
    """295359: só a perna `return` → warehouse existe; depois da triagem a
    perna `return_from_triage` → seller_address passa a mandar."""
    galpao = {
        "shipment_id": 48003191084, "type": "return", "status": "shipped",
        "destination": {"name": "warehouse"}, "tracking_number": "AP489069705BR",
    }
    ret = {
        "id": 161530676, "claim_id": 5576230842, "status": "shipped", "intermediate_check": True,
        "date_created": "2026-09-13T09:09:01.000-04:00", "last_updated": "2026-09-18T16:35:22.000-04:00",
        "shipments": [galpao],
    }

    class _Fake:
        async def get_order(self, oid):
            return {"id": oid, "mediations": [{"id": 5576230842}]}

        async def get_claim_returns(self, cid):
            return ret

        async def get_shipment(self, sid):
            leg = next(s for s in ret["shipments"] if s["shipment_id"] == int(sid))
            return {"id": sid, "status": leg["status"], "tracking_number": leg["tracking_number"],
                    "tracking_method": "PAC", "status_history": {"date_delivered": None},
                    "last_updated": ret["last_updated"]}

        async def get_claim(self, cid):
            return {"id": cid, "players": []}

    info = await logistica_meli._return_info_for_pedido(_Fake(), "2000014922944893")
    assert info.destino == "warehouse"
    assert info.tracking == "AP489069705BR"
    assert info.entregue_em is None

    # Galpão entregue, ainda sem a perna da loja: continua "warehouse".
    galpao["status"] = "delivered"
    ret["status"] = "delivered"
    info = await logistica_meli._return_info_for_pedido(_Fake(), "2000014922944893")
    assert info.destino == "warehouse" and info.status == "delivered"
    assert info.entregue_em is None

    # Depois da triagem: a perna da loja manda (em qualquer ordem da lista).
    entregue_iso = "2026-09-25T10:00:00.000-04:00"
    loja = {
        "shipment_id": 48099999999, "type": "return_from_triage", "status": "delivered",
        "destination": {"name": "seller_address"}, "tracking_number": "LF72NSD7LBLGTB2JUOOPXJQDZQ",
    }
    ret["shipments"] = [galpao, loja]
    fake = _Fake()

    async def _sh(sid):
        leg = next(s for s in ret["shipments"] if s["shipment_id"] == int(sid))
        return {"id": sid, "status": leg["status"], "tracking_number": leg["tracking_number"],
                "tracking_method": "Mercado Envios",
                "status_history": {"date_delivered": entregue_iso}, "last_updated": entregue_iso}

    fake.get_shipment = _sh
    for ordem in ([galpao, loja], [loja, galpao]):
        ret["shipments"] = ordem
        info = await logistica_meli._return_info_for_pedido(fake, "2000014922944893")
        assert info.destino == "seller_address"
        assert info.tracking == "LF72NSD7LBLGTB2JUOOPXJQDZQ"
        assert info.entregue_em == datetime(2026, 9, 25, 14, 0, tzinfo=UTC)


# ── Sync + pull dos Correios ──────────────────────────────────────────────


def _info(**kw) -> ReturnInfo:
    base = dict(
        fonte="ml", status="shipped", tracking="AP489069705BR", carrier="PAC",
        created_at=datetime(2026, 9, 13, 13, 9, tzinfo=UTC),
        updated_at=datetime(2026, 9, 18, 20, 35, tzinfo=UTC),
        return_id="5576230842", destino="warehouse",
    )
    base.update(kw)
    return ReturnInfo(**base)


@pytest.fixture
def fakes(monkeypatch):
    """Só o fetcher do ML responde; o 17track falso aceita todo registro e não
    devolve evento nenhum."""
    respostas: dict[str, ReturnInfo] = {}

    async def _ml(session, linhas):
        return {r.pedido_bling: respostas[r.pedido_bling] for r in linhas if r.pedido_bling in respostas}

    async def _vazio(session, linhas):
        return {}

    async def _register(numbers):
        return {"ok": list(numbers), "sem_quota": False}

    async def _fetch_detalhado(numbers):
        return {"info": {}, "desconhecidos": []}

    async def _em_quarentena(numeros):
        return set()

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(logistica_meli, "returns_por_pedido", _ml)
    monkeypatch.setattr(logistica_tiktok, "returns_por_pedido", _vazio)
    monkeypatch.setattr(logistica_shopee, "returns_por_pedido", _vazio)
    monkeypatch.setattr(svc.logistica_track, "register", _register)
    monkeypatch.setattr(svc.logistica_track, "fetch_detalhado", _fetch_detalhado)
    monkeypatch.setattr(svc.logistica_track_sync, "em_quarentena", _em_quarentena)
    monkeypatch.setattr(svc.logistica_track_sync, "por_de_quarentena", _noop)
    monkeypatch.setattr(svc.logistica_track_sync, "marcar_sem_quota", _noop)
    return respostas


async def test_sync_grava_o_destino_e_apaga_o_carimbo_falso_da_perna_do_galpao(db, fakes):
    await db.execute(delete(DevolucaoRastreio))
    db.add(Logistica(pedido_bling="295359", plataforma="Mercado Livre",
                     pedido_marketplace="2000014922944893", conta="mini"))
    # 295359 como estava em produção: carimbo deixado pelo pull dos Correios.
    db.add(DevolucaoRastreio(
        pedido_bling="295359", rastreio_auto="AP489069705BR", fonte_auto="ml",
        devolucao_status_auto="shipped", pacote_entregue_em=GALPAO_EM,
    ))
    await db.commit()

    fakes["295359"] = _info()
    await svc.run(db, pedidos=["295359"])
    row = await db.get(DevolucaoRastreio, "295359")
    await db.refresh(row)
    assert row.devolucao_destino_auto == "warehouse"
    assert row.pacote_entregue_em is None  # apagado: era Cajamar

    # Perna da loja chegou: a chegada vem da API, com a data exata.
    chegou = datetime(2026, 9, 25, 14, 0, tzinfo=UTC)
    fakes["295359"] = _info(
        destino="seller_address", status="delivered", tracking="LF72NSD7LB", entregue_em=chegou
    )
    await svc.run(db, pedidos=["295359"])
    await db.refresh(row)
    assert row.devolucao_destino_auto == "seller_address"
    assert row.rastreio_auto == "LF72NSD7LB"
    assert row.pacote_entregue_em == chegou


async def test_pull_dos_correios_nao_carimba_a_perna_do_galpao(db, monkeypatch):
    await db.execute(delete(DevolucaoRastreio))
    db.add_all([
        # 294679: texto "entregue" já salvo, perna do galpão → não carimba.
        DevolucaoRastreio(
            pedido_bling="294679", rastreio_auto="AP493857219BR", fonte_auto="ml",
            devolucao_status_auto="delivered", devolucao_destino_auto="warehouse",
            localizacao_auto="Cajamar/SP — Objeto entregue ao destinatário",
            localizacao_auto_data=GALPAO_EM,
        ),
        # 295359: galpão ainda "shipped" no ML → consulta os Correios, grava a
        # localização, mas "Delivered" não carimba.
        DevolucaoRastreio(
            pedido_bling="295359", rastreio_auto="AP489069705BR", fonte_auto="ml",
            devolucao_status_auto="shipped", devolucao_destino_auto="warehouse",
        ),
        # Devolução direta do ML (sem revisão): perna única pra loja → carimba.
        DevolucaoRastreio(
            pedido_bling="291428", rastreio_auto="AP111111111BR", fonte_auto="ml",
            devolucao_status_auto="shipped", devolucao_destino_auto="seller_address",
        ),
        # TikTok: nada muda.
        DevolucaoRastreio(
            pedido_bling="900001", rastreio_auto="AP222222222BR", fonte_auto="tiktok",
            devolucao_status_auto="BUYER_SHIPPED_ITEM",
        ),
    ])
    await db.commit()

    perguntados: list[list[str]] = []

    async def fake_fetch(numbers):
        perguntados.append(sorted(numbers))
        return {
            "info": {
                "AP489069705BR": {
                    "localizacao": "Cajamar/SP — Objeto entregue ao destinatário",
                    "status": "Delivered", "sync_at": GALPAO_EM,
                },
                "AP111111111BR": {
                    "localizacao": "Piracicaba/SP — Objeto entregue ao destinatário",
                    "status": "Delivered", "sync_at": GALPAO_EM,
                },
                "AP222222222BR": {
                    "localizacao": "Piracicaba/SP — Objeto entregue ao destinatário",
                    "status": "Delivered", "sync_at": GALPAO_EM,
                },
            },
            "desconhecidos": [],
        }

    monkeypatch.setattr(svc.logistica_track, "fetch_detalhado", fake_fetch)
    resumo = await svc._puxar_correios(db)

    # 294679 (galpão já entregue) fica fora da consulta; o resto entra.
    assert perguntados == [["AP111111111BR", "AP222222222BR", "AP489069705BR"]]
    assert resumo["entregues"] == 2 and resumo["localizacoes"] == 3
    for pedido, esperado in (("294679", None), ("295359", None), ("291428", GALPAO_EM), ("900001", GALPAO_EM)):
        row = await db.get(DevolucaoRastreio, pedido)
        await db.refresh(row)
        assert row.pacote_entregue_em == esperado, pedido
    row = await db.get(DevolucaoRastreio, "295359")
    assert row.localizacao_auto == "Cajamar/SP — Objeto entregue ao destinatário"

    # Rodada seguinte: o galpão já "recebeu" (texto salvo) → 295359 sai da
    # consulta também.
    perguntados.clear()
    await svc._puxar_correios(db)
    assert perguntados == []


def test_perna_do_galpao_so_vale_pro_ml():
    assert svc._perna_do_galpao(DevolucaoRastreio(pedido_bling="1", fonte_auto="ml", devolucao_destino_auto="warehouse"))
    assert not svc._perna_do_galpao(DevolucaoRastreio(pedido_bling="1", fonte_auto="ml", devolucao_destino_auto="seller_address"))
    assert not svc._perna_do_galpao(DevolucaoRastreio(pedido_bling="1", fonte_auto="ml", devolucao_destino_auto=None))
    assert not svc._perna_do_galpao(DevolucaoRastreio(pedido_bling="1", fonte_auto="tiktok", devolucao_destino_auto="warehouse"))


async def test_carimbo_velho_do_galpao_cai_quando_o_ml_abre_a_perna_da_loja(db, fakes):
    """296009/291645: o pull dos Correios carimbou a perna AP… do galpão ANTES
    da correção; depois o ML abriu a perna da loja (código próprio, LF72…).
    Sem apagar, "Chegou em" ficaria com a data de Cajamar pra sempre — e a
    data real, quando a loja receber, não entraria (o sync só carimba vazio)."""
    await db.execute(delete(DevolucaoRastreio))
    db.add(Logistica(pedido_bling="296009", plataforma="Mercado Livre",
                     pedido_marketplace="2000015000000001", conta="zorvex"))
    db.add(DevolucaoRastreio(
        pedido_bling="296009", rastreio_auto="MM48021343069BRSP02", fonte_auto="ml",
        devolucao_status_auto="delivered", pacote_entregue_em=GALPAO_EM,
        localizacao_auto="Cajamar/SP — Objeto entregue ao destinatário",
    ))
    await db.commit()

    # Código novo (perna da loja) ainda a caminho.
    fakes["296009"] = _info(destino="seller_address", status="shipped", tracking="53M3DL736VM6PDYQHUFQWN6NIQ", carrier="Mercado Envios")
    await svc.run(db, pedidos=["296009"])
    row = await db.get(DevolucaoRastreio, "296009")
    await db.refresh(row)
    assert row.rastreio_auto == "53M3DL736VM6PDYQHUFQWN6NIQ"
    assert row.pacote_entregue_em is None and row.localizacao_auto is None

    # Mesma perna (código repetido, não-Correios), ainda sem entrega pela API:
    # um carimbo que sobrou também cai.
    row.pacote_entregue_em = GALPAO_EM
    await db.commit()
    await svc.run(db, pedidos=["296009"])
    await db.refresh(row)
    assert row.pacote_entregue_em is None

    # Devolução DIRETA (perna única pra loja, Correios): o carimbo dos Correios
    # é legítimo e fica, mesmo com a API ainda em "shipped".
    db.add(Logistica(pedido_bling="291428", plataforma="Mercado Livre",
                     pedido_marketplace="2000015000000002", conta="jlas2"))
    db.add(DevolucaoRastreio(
        pedido_bling="291428", rastreio_auto="AP111111111BR", fonte_auto="ml",
        devolucao_status_auto="shipped", pacote_entregue_em=GALPAO_EM,
    ))
    await db.commit()
    fakes["291428"] = _info(destino="seller_address", status="shipped", tracking="AP111111111BR")
    await svc.run(db, pedidos=["291428"])
    direto = await db.get(DevolucaoRastreio, "291428")
    await db.refresh(direto)
    assert direto.pacote_entregue_em == GALPAO_EM


def test_push_do_17track_nao_carimba_a_perna_do_galpao():
    agora = datetime(2026, 9, 19, 3, 0, tzinfo=UTC)
    galpao = DevolucaoRastreio(pedido_bling="292659", rastreio_auto="AP498078192BR", fonte_auto="ml",
                               devolucao_destino_auto="warehouse")
    svc.aplicar_push(galpao, "Sao Paulo/SP — Objeto entregue ao destinatário", entregue=True, agora=agora)
    assert galpao.localizacao_auto == "Sao Paulo/SP — Objeto entregue ao destinatário"
    assert galpao.localizacao_auto_data == agora
    assert galpao.pacote_entregue_em is None

    loja = DevolucaoRastreio(pedido_bling="291428", rastreio_auto="AP111111111BR", fonte_auto="ml",
                             devolucao_destino_auto="seller_address")
    svc.aplicar_push(loja, "Piracicaba/SP — Objeto entregue ao destinatário", entregue=True, agora=agora)
    assert loja.pacote_entregue_em == agora

    tiktok = DevolucaoRastreio(pedido_bling="900001", rastreio_auto="AP222222222BR", fonte_auto="tiktok")
    svc.aplicar_push(tiktok, "Piracicaba/SP — Objeto entregue ao destinatário", entregue=True, agora=agora)
    assert tiktok.pacote_entregue_em == agora
    # Nunca apaga nem reescreve um carimbo existente.
    svc.aplicar_push(tiktok, "outro evento", entregue=True, agora=agora + timedelta(hours=1))
    assert tiktok.pacote_entregue_em == agora


# ── Logística: a chave extra `return_destino` ─────────────────────────────


async def test_build_enrichment_grava_o_destino_da_perna_fora_da_assinatura():
    from tests.test_logistica_meli import _RETURNS_ENTREGUE_NA_LOJA, _pedido_devolvido

    enr = await logistica_meli.build_enrichment(_pedido_devolvido(_RETURNS_ENTREGUE_NA_LOJA), "2000014771832357")
    assert enr["meli_status"]["return_status"] == "delivered"
    assert enr["meli_status"]["return_destino"] == "seller_address"
    # A assinatura (aba Status) não muda com a chave extra.
    assert logistica_rules.assinatura_pt(enr["meli_status"]) == logistica_rules.assinatura_pt(
        {k: v for k, v in enr["meli_status"].items() if k != "return_destino"}
    )

    so_galpao = {**_RETURNS_ENTREGUE_NA_LOJA, "shipments": [_RETURNS_ENTREGUE_NA_LOJA["shipments"][1]]}
    enr = await logistica_meli.build_enrichment(_pedido_devolvido(so_galpao), "2000014771832357")
    assert enr["meli_status"]["return_status"] == "delivered"
    assert enr["meli_status"]["return_destino"] == "warehouse"
    assert logistica_rules.data_retorno_concluido(
        "Mercado Livre", enr["meli_status"], {"return_status": {"em": "2026-09-14T13:02:48+00:00"}}
    ) is None
    assert enr["localizacao"] == "Devolvido → galpão do ML (Cajamar/SP)"


@pytest_asyncio.fixture
async def admin(db) -> User:
    import uuid

    email = f"adm-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(open_id=f"email:{email}", email=email, role=UserRole.ADMIN, status=UserStatus.ACTIVE)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def test_editar_o_modal_da_logistica_nao_apaga_o_destino(client, admin: User, auth_as):
    auth_as(admin)
    r = await client.post(
        "/api/logistica",
        json={
            "pedido_bling": "292729",
            "plataforma": "Mercado Livre",
            "meli_status": {"order_status": "cancelled", "return_status": "delivered", "return_destino": "warehouse"},
        },
    )
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert r.json()["meli_status"]["return_destino"] == "warehouse"

    # O modal reenvia só os campos da assinatura.
    r = await client.patch(
        f"/api/logistica/{cid}",
        json={"meli_status": {"order_status": "cancelled", "return_status": "delivered", "claim_stage": "recontact"}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["meli_status"]["return_destino"] == "warehouse"
    assert r.json()["meli_status"]["claim_stage"] == "recontact"
    await client.delete(f"/api/logistica/{cid}")
