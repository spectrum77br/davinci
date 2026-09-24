"""Vigia Robô Melhor Envio (robô da Ouvidoria) — o que este arquivo trava.

- sem sinal do executor: `executor:offline`, `urgente` quando há suspensão
  esperando e `pessoa` quando não há; nunca-reportou sem nada na fila não
  cobra ninguém (ambiente sem executor); fecha quando o sinal volta;
- AdsPower fora, perfil do Melhor Envio vazio e a trava (modo seco) abrem as
  suas próprias linhas;
- pedido de suspensão parado na fila (`pending`) ou preso (`claimed`) além de
  `pendente_min`: `pessoa`, e `urgente` depois de 2×; fecha quando conclui;
- linha `falhou` com o pacote ainda a caminho abre `suspensao:<id>` com o
  motivo do robô; login pedido e clique-sem-confirmação mudam a ação; fecha
  quando pede de novo, dá certo ou o pacote é entregue; falha velha (fora da
  janela) não vira passivo;
- o heartbeat do Melhor Envio não conta como executor da Shopee (e vice-versa).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Logistica, LogisticaRoboComando, OuvidoriaOcorrencia, OuvidoriaRobo
from app.models.marketing import MarketingAgentHeartbeat
from app.services import ouvidoria as svc
from app.services import vigia_robo_melhorenvio as vigia

pytestmark = pytest.mark.asyncio

ROBO = vigia.ROBO


async def _limpar(db: AsyncSession) -> None:
    for tbl in (
        "ouvidoria_ocorrencias",
        "ouvidoria_rodadas",
        "ouvidoria_robos",
        "logistica_robo_comando",
        "marketing_agent_heartbeat",
    ):
        await db.execute(text(f"DELETE FROM {tbl}"))  # noqa: S608
    await db.execute(text("DELETE FROM logistica WHERE pedido_bling LIKE 'vme-%'"))
    await db.commit()


@pytest_asyncio.fixture(autouse=True)
async def _limpa(db: AsyncSession):
    await _limpar(db)
    yield
    await _limpar(db)


def _agora() -> datetime:
    return datetime.now(UTC)


async def _linha(db: AsyncSession, pedido: str = "vme-1", **kw) -> Logistica:
    base = {
        "data": date.today(),
        "pedido_bling": pedido,
        "pedido_marketplace": "701-0000000-0000000",
        "plataforma": "Amazon",
        "conta": "kia",
        "amazon_canal": "proprio",
        "rastreio": "AD952521063BR",
    }
    base.update(kw)
    row = Logistica(**base)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def _comando(
    db: AsyncSession, row: Logistica, *, status: str = "pending", criado_min: int = 0,
    pego_min: int | None = None,
) -> LogisticaRoboComando:
    cmd = LogisticaRoboComando(
        logistica_id=row.id,
        acao="melhorenvio_suspender",
        payload={"rastreio": row.rastreio, "commit": True},
        status=status,
        attempts=1 if status == "claimed" else 0,
        claimed_at=_agora() - timedelta(minutes=pego_min) if pego_min is not None else None,
    )
    db.add(cmd)
    await db.commit()
    cmd.created_at = _agora() - timedelta(minutes=criado_min)
    await db.commit()
    await db.refresh(cmd)
    return cmd


async def _heartbeat(
    db: AsyncSession, *, visto_min: int = 0, adspower_ok: bool = True,
    calibrado: bool = True, perfil: bool = True, nome: str = "logistica:santiago",
) -> MarketingAgentHeartbeat:
    hb = MarketingAgentHeartbeat(
        agent_name=nome,
        last_seen_at=_agora() - timedelta(minutes=visto_min),
        adspower_ok=adspower_ok,
        info={
            "version": "1.1.0",
            "filas": ["melhorenvio"],
            "melhorenvio_calibrated": calibrado,
            "perfil_melhorenvio": perfil,
        },
    )
    db.add(hb)
    await db.commit()
    return hb


async def _por_chave(db: AsyncSession, chave: str) -> OuvidoriaOcorrencia | None:
    return (
        await db.execute(
            select(OuvidoriaOcorrencia)
            .where(OuvidoriaOcorrencia.robo_chave == ROBO, OuvidoriaOcorrencia.chave == chave)
            .order_by(OuvidoriaOcorrencia.aberta_em.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _abertas(db: AsyncSession) -> list[str]:
    return sorted(
        (
            await db.execute(
                select(OuvidoriaOcorrencia.chave).where(
                    OuvidoriaOcorrencia.robo_chave == ROBO,
                    OuvidoriaOcorrencia.fechada_em.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )


# ─── executor ──────────────────────────────────────────────────────────────


async def test_sem_executor_e_sem_fila_nao_cobra_ninguem(db):
    r = await vigia.vigia_robo_melhorenvio_run(db)
    assert await _abertas(db) == [] and r["resumo"] == "robô nunca deu sinal"


async def test_nunca_deu_sinal_com_suspensao_esperando_e_urgente(db):
    row = await _linha(db)
    await _comando(db, row, criado_min=5)
    await vigia.vigia_robo_melhorenvio_run(db)
    o = await _por_chave(db, "executor:offline")
    assert o.severidade == "urgente" and "1 suspensão esperando" in o.detalhe


async def test_sem_sinal_abre_e_fecha_quando_volta(db):
    hb = await _heartbeat(db, visto_min=25)
    r = await vigia.vigia_robo_melhorenvio_run(db)
    o = await _por_chave(db, "executor:offline")
    assert o.titulo == "Robô do Melhor Envio (Mac Santiago) sem sinal há 25 min"
    # Nada esperando: avisa, mas não é urgência.
    assert o.severidade == "pessoa" and o.plataforma == "amazon"
    assert o.dados["agent_name"] == "santiago" and o.dados["calibrado"] is True
    assert "tampa aberta" in o.acao and o.link == vigia.LINK_PAINEL
    assert r["executor_ok"] == 0 and r["resumo"] == "robô sem sinal há 25 min"

    row = await _linha(db)
    await _comando(db, row, criado_min=3)
    await vigia.vigia_robo_melhorenvio_run(db)
    o = await _por_chave(db, "executor:offline")
    assert o.severidade == "urgente" and "há 1 suspensão esperando" in o.detalhe

    hb.last_seen_at = _agora()
    await db.commit()
    r = await vigia.vigia_robo_melhorenvio_run(db)
    assert (await _por_chave(db, "executor:offline")).fechamento == "sumiu"
    assert r["executor_ok"] == 1 and r["resumo"].endswith("robô ok")


async def test_adspower_perfil_e_trava(db):
    hb = await _heartbeat(db, adspower_ok=False, calibrado=False, perfil=False)
    r = await vigia.vigia_robo_melhorenvio_run(db)
    assert await _abertas(db) == ["executor:adspower", "executor:perfil", "executor:trava"]
    trava = await _por_chave(db, "executor:trava")
    assert trava.severidade == "info" and trava.precisa_pessoa is False
    assert "MELHORENVIO_CALIBRATED" in trava.acao
    assert r["resumo"] == "robô online, AdsPower fechado · sem perfil do Melhor Envio · modo seco"

    hb.adspower_ok = True
    hb.info = {**hb.info, "melhorenvio_calibrated": True, "perfil_melhorenvio": True}
    hb.last_seen_at = _agora()
    await db.commit()
    r = await vigia.vigia_robo_melhorenvio_run(db)
    assert r["sumiram"] == 3 and await _abertas(db) == []


async def test_executor_da_shopee_nao_conta_como_robo_do_melhor_envio(db):
    await _heartbeat(db, nome="executor-mac")  # o da Shopee, sem prefixo
    row = await _linha(db)
    await _comando(db, row, criado_min=5)
    await vigia.vigia_robo_melhorenvio_run(db)
    assert (await _por_chave(db, "executor:offline")).titulo.endswith("nunca deu sinal")


# ─── fila ──────────────────────────────────────────────────────────────────


async def test_suspensao_parada_na_fila_sobe_e_fecha_quando_conclui(db):
    await _heartbeat(db)
    row = await _linha(db)
    cmd = await _comando(db, row, criado_min=10)
    r = await vigia.vigia_robo_melhorenvio_run(db)
    assert r["suspensoes_pendentes"] == 0 and await _abertas(db) == []  # ainda no prazo

    cmd.created_at = _agora() - timedelta(minutes=40)
    await db.commit()
    r = await vigia.vigia_robo_melhorenvio_run(db)
    o = await _por_chave(db, f"comando:{cmd.id}")
    assert o.titulo == "Suspensão da entrega esperando o robô há 40 min"
    assert o.severidade == "pessoa" and o.pedido == "vme-1" and o.conta == "kia"
    assert "AD952521063BR" in o.detalhe and o.link.endswith("&q=vme-1")
    assert r["resumo"] == "1 esperando · robô ok"

    cmd.created_at = _agora() - timedelta(minutes=70)
    await db.commit()
    await vigia.vigia_robo_melhorenvio_run(db)
    assert (await _por_chave(db, f"comando:{cmd.id}")).severidade == "urgente"

    cmd.status = "done"
    await db.commit()
    await vigia.vigia_robo_melhorenvio_run(db)
    assert (await _por_chave(db, f"comando:{cmd.id}")).fechamento == "sumiu"


async def test_suspensao_presa_no_robo(db):
    await _heartbeat(db)
    row = await _linha(db)
    cmd = await _comando(db, row, status="claimed", criado_min=50, pego_min=45)
    r = await vigia.vigia_robo_melhorenvio_run(db)
    o = await _por_chave(db, f"comando:{cmd.id}")
    assert o.titulo == "Suspensão da entrega presa no robô há 45 min"
    assert "não respondeu" in o.detalhe and r["suspensoes_presas"] == 1


# ─── falhas ────────────────────────────────────────────────────────────────


def _detalhe(**kw) -> str:
    return json.dumps({"ok": False, "found": True, "requested": False, "dry": True, **kw})


async def test_falha_abre_com_o_motivo_e_fecha_quando_pede_de_novo(db):
    await _heartbeat(db)
    row = await _linha(
        db,
        suspensao_status="falhou",
        suspensao_em=_agora() - timedelta(hours=2),
        suspensao_detalhe=_detalhe(reason="envio não encontrado na lista de postados"),
    )
    r = await vigia.vigia_robo_melhorenvio_run(db)
    o = await _por_chave(db, f"suspensao:{row.id}")
    assert o.titulo == "Suspensão da entrega falhou — o pacote segue pro cliente"
    assert "envio não encontrado" in o.detalhe and o.severidade == "pessoa"
    assert o.acao == vigia.ACAO_FALHOU and r["suspensoes_falhas"] == 1
    assert r["resumo"] == "1 falhou · robô ok"

    row.suspensao_status = "pendente"
    await db.commit()
    await vigia.vigia_robo_melhorenvio_run(db)
    assert (await _por_chave(db, f"suspensao:{row.id}")).fechamento == "sumiu"


async def test_falha_fecha_quando_o_pacote_e_entregue(db):
    await _heartbeat(db)
    row = await _linha(
        db, suspensao_status="falhou", suspensao_em=_agora(),
        suspensao_detalhe="needs_manual_login: entre no Melhor Envio no perfil do AdsPower",
    )
    await vigia.vigia_robo_melhorenvio_run(db)
    o = await _por_chave(db, f"suspensao:{row.id}")
    assert o.acao == vigia.ACAO_LOGIN

    row.entregue_em = _agora()
    await db.commit()
    await vigia.vigia_robo_melhorenvio_run(db)
    assert (await _por_chave(db, f"suspensao:{row.id}")).fechamento == "sumiu"


async def test_clique_sem_confirmacao_e_urgente(db):
    await _heartbeat(db)
    row = await _linha(
        db, suspensao_status="falhou", suspensao_em=_agora(),
        suspensao_detalhe=_detalhe(dry=False, reason="ATENÇÃO: cliquei e nada apareceu"),
    )
    await vigia.vigia_robo_melhorenvio_run(db)
    o = await _por_chave(db, f"suspensao:{row.id}")
    assert o.severidade == "urgente" and o.acao == vigia.ACAO_SEM_CONFIRMACAO


async def test_falha_velha_nao_vira_passivo_mas_aberta_continua(db):
    await _heartbeat(db)
    velha = await _linha(
        db, "vme-2", suspensao_status="falhou",
        suspensao_em=_agora() - timedelta(days=20), suspensao_detalhe=_detalhe(reason="x"),
    )
    await vigia.vigia_robo_melhorenvio_run(db)
    assert await _abertas(db) == []

    # Já aberta (ex.: aberta quando era recente) continua sendo re-vista.
    await svc.registrar(db, ROBO, f"suspensao:{velha.id}", titulo="t")
    await db.commit()
    await vigia.vigia_robo_melhorenvio_run(db)
    assert await _abertas(db) == [f"suspensao:{velha.id}"]


# ─── sweep e tick ──────────────────────────────────────────────────────────


async def test_sweep_e_serializado_pelo_advisory_lock(db, monkeypatch):
    from app.services.advisory_lock import SYNC_NAMESPACE

    got = (
        await db.execute(
            text("SELECT pg_try_advisory_xact_lock(:ns, :key)"),
            {"ns": SYNC_NAMESPACE, "key": vigia._SWEEP_LOCK_KEY},
        )
    ).scalar()
    assert got
    assert await vigia.vigia_robo_melhorenvio_sweep() == {"skipped": "lock_busy"}
    await db.rollback()


async def test_nasce_silencioso(db):
    await svc.sincronizar_catalogo(db)
    await db.commit()
    robo = await db.get(OuvidoriaRobo, ROBO)
    assert robo.modo == "silencioso" and robo.nome == "Vigia Robô Melhor Envio"
