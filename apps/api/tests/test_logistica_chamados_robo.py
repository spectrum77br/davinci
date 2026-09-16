# ruff: noqa: E501
"""Abrir chamado da aba Status pra TikTok e Shopee — direto pro robô (16/09/2026).

Vinicius: "quando eu cadastrar status no TikTok, Shopee e colocar chamado sim,
ele precisa abrir o chamado" / "se chegar no painel de chamado, o robô do
Eduardo vai abrir e resolver". A regra com `abrir_chamado` põe o chamado na
aba Chamados no canal robô (abertura pendente com a mensagem e as imagens da
regra); o robô puxa no /agent/lease, abre no Seller Center e devolve o
protocolo, que cai na linha da Logística.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.models import (
    Chamado,
    ChamadoAnexo,
    ChamadoMensagem,
    Logistica,
    LogisticaStatus,
    LogisticaStatusAnexo,
)
from app.services import logistica_chamados_robo as svc
from app.services import logistica_rules
from sqlalchemy import select

pytestmark = pytest.mark.asyncio

TIKTOK_CANCELADO = {"order_status": "CANCELLED"}
SHOPEE_CANCELADO = {"order_status": "CANCELLED"}


def _linha(plataforma: str, **kw) -> Logistica:
    base = {
        "plataforma": plataforma,
        "conta": "mini",
        "pedido_marketplace": "TT1",
        "pedido_bling": "300001",
        "meli_status": TIKTOK_CANCELADO,
        "status_bling": "Em aberto",
    }
    base.update(kw)
    return Logistica(**base)


async def _mensagens(db, ch: Chamado) -> list[ChamadoMensagem]:
    return (
        await db.execute(
            select(ChamadoMensagem).where(ChamadoMensagem.chamado_id == ch.id)
            .order_by(ChamadoMensagem.created_at)
        )
    ).scalars().all()


async def test_regra_tiktok_com_chamado_vai_pro_robo(db):
    chave = logistica_rules.assinatura_para("TikTok", TIKTOK_CANCELADO)
    assert chave == "Cancelado"
    regra = LogisticaStatus(plataforma="TikTok", status_plataforma=chave, abrir_chamado=True,
                            mensagem_chamado="Pedido cancelado com pacote já postado — solicitamos análise.")
    db.add(regra)
    await db.flush()
    db.add(LogisticaStatusAnexo(status_id=regra.id, filename="etiqueta.png",
                                content_type="image/png", size_bytes=3, blob=b"png"))
    row = _linha("TikTok")
    db.add(row)
    await db.commit()
    t0 = datetime(2026, 9, 16, 15, 0, tzinfo=UTC)

    out = await svc.abrir_chamados_em_lote(db, None, agora=t0)

    assert out == {"robo": 1, "adiados": 0, "pulados": 0, "falhas": 0}
    await db.refresh(row)
    assert row.chamado is None
    assert row.chamado_auto_at == t0 and row.chamado_auto_erro == "encaminhado_ao_robo"
    ch = (await db.execute(select(Chamado).where(Chamado.origem == "logistica"))).scalar_one()
    assert ch.canal == "robo" and ch.chamado is None and ch.origem_ref == str(row.id)
    assert ch.plataforma == "TikTok" and ch.conta == "mini" and ch.pedido_marketplace == "TT1"
    msgs = await _mensagens(db, ch)
    assert [m.tipo for m in msgs] == ["sistema", "abertura"]
    assert "Seller Center" in msgs[0].texto and "TikTok" in msgs[0].texto and "Cancelado" in msgs[0].texto
    assert msgs[1].status == "pendente" and msgs[1].canal == "robo"
    assert msgs[1].texto == "Pedido cancelado com pacote já postado — solicitamos análise."
    anexos = (await db.execute(select(ChamadoAnexo).where(ChamadoAnexo.chamado_id == ch.id))).scalars().all()
    assert [(a.filename, a.mensagem_id) for a in anexos] == [("etiqueta.png", msgs[1].id)]

    # rodada seguinte: robô ainda não devolveu → adia, não duplica
    out2 = await svc.abrir_chamados_em_lote(db, None, agora=t0 + timedelta(hours=7))
    assert out2 == {"robo": 0, "adiados": 1, "pulados": 0, "falhas": 0}
    assert len((await db.execute(select(Chamado))).scalars().all()) == 1
    assert len([m for m in await _mensagens(db, ch) if m.tipo == "abertura"]) == 1


async def test_regra_shopee_tambem_abre(db):
    chave = logistica_rules.assinatura_para("Shopee", SHOPEE_CANCELADO)
    db.add(LogisticaStatus(plataforma="Shopee", status_plataforma=chave, abrir_chamado=True,
                           mensagem_chamado="Cancelamento indevido — pedir revisão."))
    row = _linha("Shopee", conta="atv", pedido_marketplace="2609SN", pedido_bling="300002",
                 meli_status=SHOPEE_CANCELADO)
    db.add(row)
    await db.commit()

    out = await svc.abrir_chamados_em_lote(db, None)

    assert out["robo"] == 1
    ch = (await db.execute(select(Chamado).where(Chamado.origem == "logistica"))).scalar_one()
    assert ch.plataforma == "Shopee" and ch.canal == "robo"
    assert "Shopee" in (await _mensagens(db, ch))[0].texto


async def test_regra_sem_mensagem_pula_e_carimba(db):
    chave = logistica_rules.assinatura_para("TikTok", TIKTOK_CANCELADO)
    db.add(LogisticaStatus(plataforma="TikTok", status_plataforma=chave, abrir_chamado=True))
    row = _linha("TikTok", pedido_bling="300003")
    db.add(row)
    await db.commit()

    out = await svc.abrir_chamados_em_lote(db, None)

    assert out == {"robo": 0, "adiados": 0, "pulados": 1, "falhas": 0}
    await db.refresh(row)
    assert row.chamado_auto_erro == "logistica_sem_mensagem_chamado"
    assert (await db.execute(select(Chamado))).scalars().all() == []


async def test_respeita_estado_atual_plataforma_e_ids(db):
    chave = logistica_rules.assinatura_para("TikTok", TIKTOK_CANCELADO)
    # regra só vale saindo de "Entregue"; a linha está "Em aberto" → fora
    db.add(LogisticaStatus(plataforma="TikTok", status_plataforma=chave, status_atual="Entregue",
                           abrir_chamado=True, mensagem_chamado="msg"))
    fora = _linha("TikTok", pedido_bling="300004", status_bling="Em aberto")
    dentro = _linha("TikTok", pedido_bling="300005", status_bling="Entregue", pedido_marketplace="TT5")
    # ML não é deste executor (tem o dele) e Amazon não tem robô
    ml = _linha("Mercado Livre", pedido_bling="300006", status_bling="Entregue",
                meli_status={"order_status": "cancelled"})
    db.add_all([fora, dentro, ml])
    await db.commit()

    assert (await svc.abrir_chamados_em_lote(db, [fora.id]))["robo"] == 0
    out = await svc.abrir_chamados_em_lote(db, [fora.id, dentro.id, ml.id])
    assert out["robo"] == 1
    ch = (await db.execute(select(Chamado).where(Chamado.origem == "logistica"))).scalar_one()
    assert ch.origem_ref == str(dentro.id)


async def test_linha_com_chamado_do_operador_nao_abre(db):
    chave = logistica_rules.assinatura_para("TikTok", TIKTOK_CANCELADO)
    db.add(LogisticaStatus(plataforma="TikTok", status_plataforma=chave, abrir_chamado=True,
                           mensagem_chamado="msg"))
    db.add(_linha("TikTok", pedido_bling="300007", chamado="MANUAL-1"))
    await db.commit()

    out = await svc.abrir_chamados_em_lote(db, None)

    assert out["robo"] == 0
    assert (await db.execute(select(Chamado))).scalars().all() == []


async def test_robo_puxa_a_tarefa_e_o_protocolo_volta_pra_logistica(db, client, monkeypatch):
    """Ponta a ponta com a fila do robô: a tarefa sai no /agent/lease com a
    plataforma TikTok; o resultado com protocolo cai na linha da Logística."""
    from app.config import get_settings

    token = "tok-robo-tiktok"  # noqa: S105
    monkeypatch.setattr(get_settings(), "nf_agent_token", token)
    hdr = {"X-Agent-Token": token}

    chave = logistica_rules.assinatura_para("TikTok", TIKTOK_CANCELADO)
    db.add(LogisticaStatus(plataforma="TikTok", status_plataforma=chave, abrir_chamado=True,
                           mensagem_chamado="Abrir no Seller Center."))
    row = _linha("TikTok", pedido_bling="300008", pedido_marketplace="TT8")
    db.add(row)
    await db.commit()
    assert (await svc.abrir_chamados_em_lote(db, None))["robo"] == 1

    # o robô pede pela plataforma que atende (sem `plataforma` = robô do ML,
    # que não vê tarefa de TikTok)
    r = await client.post("/api/chamados/agent/lease", headers=hdr,
                          json={"tipo": "abrir", "plataforma": "tiktok"})
    assert r.status_code == 200, r.text
    tarefas = r.json()["tarefas"]
    assert len(tarefas) == 1
    assert tarefas[0]["tipo"] == "abrir" and tarefas[0]["plataforma"] == "TikTok"
    assert tarefas[0]["conta"] == "mini" and tarefas[0]["pedido_marketplace"] == "TT8"
    assert tarefas[0]["texto"] == "Abrir no Seller Center."

    r = await client.post(
        "/api/chamados/agent/resultado", headers=hdr,
        json={"mensagem_id": tarefas[0]["mensagem_id"], "ok": True, "chamado": "TT-CASE-77"},
    )
    assert r.status_code == 200, r.text
    await db.refresh(row)
    assert row.chamado == "TT-CASE-77" and row.chamado_auto_erro is None
    ch = (await db.execute(select(Chamado).where(Chamado.origem == "logistica"))).scalar_one()
    assert ch.chamado == "TT-CASE-77"
    # rodada seguinte: linha já tem chamado → nada
    assert (await svc.abrir_chamados_em_lote(db, None)) == {"robo": 0, "adiados": 0, "pulados": 0, "falhas": 0}


# ─── achados da revisão (16/09) ──────────────────────────────────────────


async def _regra_tiktok(db, **kw) -> LogisticaStatus:
    chave = logistica_rules.assinatura_para("TikTok", TIKTOK_CANCELADO)
    base = {"plataforma": "TikTok", "status_plataforma": chave, "abrir_chamado": True,
            "mensagem_chamado": "msg"}
    base.update(kw)
    regra = LogisticaStatus(**base)
    db.add(regra)
    return regra


async def test_robo_falhou_nao_reenfileira_e_carimba(db):
    await _regra_tiktok(db)
    row = _linha("TikTok", pedido_bling="300020")
    db.add(row)
    await db.commit()
    t0 = datetime(2026, 9, 16, 15, 0, tzinfo=UTC)
    assert (await svc.abrir_chamados_em_lote(db, None, agora=t0))["robo"] == 1
    ch = (await db.execute(select(Chamado).where(Chamado.origem == "logistica"))).scalar_one()
    abertura = [m for m in await _mensagens(db, ch) if m.tipo == "abertura"][0]
    abertura.status = "falhou"
    abertura.erro = "não sei abrir na TikTok"
    await db.commit()

    for i in range(3):
        out = await svc.abrir_chamados_em_lote(db, None, agora=t0 + timedelta(minutes=5 * (i + 1)))
        assert out == {"robo": 0, "adiados": 0, "pulados": 0, "falhas": 1}
    await db.refresh(row)
    assert row.chamado_auto_erro == "robo_falhou: não sei abrir na TikTok"
    assert len([m for m in await _mensagens(db, ch) if m.tipo == "abertura"]) == 1
    assert len((await db.execute(select(Chamado))).scalars().all()) == 1


async def test_robo_enviou_sem_protocolo_nao_conta_como_novo(db):
    await _regra_tiktok(db)
    row = _linha("TikTok", pedido_bling="300021")
    db.add(row)
    await db.commit()
    assert (await svc.abrir_chamados_em_lote(db, None))["robo"] == 1
    ch = (await db.execute(select(Chamado).where(Chamado.origem == "logistica"))).scalar_one()
    abertura = [m for m in await _mensagens(db, ch) if m.tipo == "abertura"][0]
    abertura.status = "enviada"
    await db.commit()

    out = await svc.abrir_chamados_em_lote(db, None)
    assert out == {"robo": 0, "adiados": 1, "pulados": 0, "falhas": 0}
    await db.refresh(row)
    assert row.chamado_auto_erro == "robo_sem_protocolo"


async def test_chamado_de_outra_origem_segura_o_robo(db):
    await _regra_tiktok(db)
    db.add(Chamado(pedido_bling="300022", plataforma="TikTok", conta="mini", origem="devolucao",
                   canal="api"))
    row = _linha("TikTok", pedido_bling="300022")
    db.add(row)
    await db.commit()

    out = await svc.abrir_chamados_em_lote(db, None)
    assert out == {"robo": 0, "adiados": 1, "pulados": 0, "falhas": 0}
    await db.refresh(row)
    assert row.chamado_auto_erro == "chamado_de_outra_origem"
    assert len((await db.execute(select(Chamado))).scalars().all()) == 1

    # quando o outro chamado ganha protocolo, a linha espelha
    outro = (await db.execute(select(Chamado))).scalar_one()
    outro.chamado = "DEV-9"
    await db.commit()
    assert (await svc.abrir_chamados_em_lote(db, None))["robo"] == 0
    await db.refresh(row)
    assert row.chamado == "DEV-9" and row.chamado_auto_erro is None


async def test_chamado_manual_do_operador_nao_e_sequestrado(db):
    await _regra_tiktok(db)
    row = _linha("TikTok", pedido_bling="300023")
    db.add(row)
    await db.flush()
    db.add(Chamado(pedido_bling="300023", plataforma="TikTok", conta="mini", origem="logistica",
                   origem_ref=str(row.id), canal="manual"))
    await db.commit()

    out = await svc.abrir_chamados_em_lote(db, None)
    assert out == {"robo": 0, "adiados": 1, "pulados": 0, "falhas": 0}
    ch = (await db.execute(select(Chamado))).scalar_one()
    assert ch.canal == "manual" and await _mensagens(db, ch) == []
    await db.refresh(row)
    assert row.chamado_auto_erro == "chamado_manual_na_aba"


async def test_chamado_resolvido_sem_protocolo_encerra(db):
    await _regra_tiktok(db)
    row = _linha("TikTok", pedido_bling="300024")
    db.add(row)
    await db.flush()
    db.add(Chamado(pedido_bling="300024", plataforma="TikTok", conta="mini", origem="logistica",
                   origem_ref=str(row.id), canal="robo", resolvido=True))
    await db.commit()

    out = await svc.abrir_chamados_em_lote(db, None)
    assert out == {"robo": 0, "adiados": 0, "pulados": 0, "falhas": 0}
    await db.refresh(row)
    assert row.chamado_auto_erro == "chamado_resolvido_na_aba"
    assert len((await db.execute(select(Chamado))).scalars().all()) == 1


async def test_linha_antiga_fica_de_fora(db):
    await _regra_tiktok(db)
    hoje = datetime(2026, 9, 16, 15, 0, tzinfo=UTC)
    velha = _linha("TikTok", pedido_bling="300025", data=(hoje - timedelta(days=60)).date())
    nova = _linha("TikTok", pedido_bling="300026", pedido_marketplace="TT26",
                  data=(hoje - timedelta(days=3)).date())
    db.add_all([velha, nova])
    await db.commit()

    out = await svc.abrir_chamados_em_lote(db, None, agora=hoje)
    assert out["robo"] == 1
    ch = (await db.execute(select(Chamado))).scalar_one()
    assert ch.origem_ref == str(nova.id)


async def test_lease_so_entrega_tiktok_shopee_pra_quem_pede(db, client, monkeypatch):
    """O robô do formulário do ML (consumidor padrão, sem `plataforma`) nunca
    recebe tarefa de TikTok/Shopee; quem pede `plataforma` recebe só a sua."""
    from app.config import get_settings

    token = "tok-lease-plat"  # noqa: S105
    monkeypatch.setattr(get_settings(), "nf_agent_token", token)
    hdr = {"X-Agent-Token": token}

    await _regra_tiktok(db)
    chave_sh = logistica_rules.assinatura_para("Shopee", SHOPEE_CANCELADO)
    db.add(LogisticaStatus(plataforma="Shopee", status_plataforma=chave_sh, abrir_chamado=True,
                           mensagem_chamado="msg shopee"))
    db.add_all([
        _linha("TikTok", pedido_bling="300030", pedido_marketplace="TT30"),
        _linha("Shopee", conta="atv", pedido_bling="300031", pedido_marketplace="SP31",
               meli_status=SHOPEE_CANCELADO),
    ])
    # um chamado robô do ML, pendente, pra provar que o padrão continua pegando ML
    ml = Chamado(pedido_bling="300032", pedido_marketplace="ML32", plataforma="Mercado Livre",
                 conta="loja", origem="logistica", canal="robo")
    db.add(ml)
    await db.flush()
    from app.services.chamados import nova_mensagem
    m = nova_mensagem(ml, texto="msg ml", tipo="abertura", direcao="enviada",
                      autor_nome="sistema", status="pendente")
    m.canal = "robo"
    db.add(m)
    await db.commit()
    assert (await svc.abrir_chamados_em_lote(db, None))["robo"] == 2

    def _plats(resp):
        return sorted((t["plataforma"], t["pedido_marketplace"]) for t in resp.json()["tarefas"])

    r = await client.post("/api/chamados/agent/lease", headers=hdr, json={"tipo": "abrir"})
    assert r.status_code == 200, r.text
    assert _plats(r) == [("Mercado Livre", "ML32")]  # padrão: só ML
    r = await client.post("/api/chamados/agent/lease", headers=hdr,
                          json={"tipo": "abrir", "plataforma": "tiktok"})
    assert _plats(r) == [("TikTok", "TT30")]
    r = await client.post("/api/chamados/agent/lease", headers=hdr,
                          json={"tipo": "abrir", "plataforma": "shopee"})
    assert _plats(r) == [("Shopee", "SP31")]
    # tudo já está `enviando`: nova rodada não devolve nada
    r = await client.post("/api/chamados/agent/lease", headers=hdr, json={"tipo": "abrir", "plataforma": "ml"})
    assert r.json()["tarefas"] == []
