"""Vigia Robô Leitura de Chamados (robô da Ouvidoria) — o que este arquivo trava.

Vinicius, 24/09/2026: o executor de leitura (Mac Santiago) não aparecia em
Ouvidoria › Robôs; se ele parasse, os chamados da Shopee voltavam a ficar mudos
sem ninguém saber.

- robô sem sinal (não pergunta a fila há mais de `sem_sinal_min`) abre
  `executor:sem_sinal` e fecha quando ele volta; nunca-deu-sinal sem caso na
  fila não cobra ninguém;
- devolução da fila sem leitura além da cadência + `atraso_horas` abre
  `caso:<id>` (6 h no caso normal, 27 h no caso frio) e fecha quando é lida;
- nunca lida conta desde que entrou na fila (a disputa saiu);
- chamado que não é da fila do robô nunca vira ocorrência;
- nasce silencioso, com o nome que aparece no painel.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chamado, ChamadoLeitor, OuvidoriaOcorrencia, OuvidoriaRobo
from app.services import chamados as chamados_svc
from app.services import ouvidoria as svc
from app.services import vigia_robo_leitura as vigia

pytestmark = pytest.mark.asyncio

ROBO = vigia.ROBO


async def _limpar(db: AsyncSession) -> None:
    for tbl in ("ouvidoria_ocorrencias", "ouvidoria_rodadas", "ouvidoria_robos"):
        await db.execute(text(f"DELETE FROM {tbl}"))  # noqa: S608
    await db.commit()


@pytest_asyncio.fixture(autouse=True)
async def _limpa(db: AsyncSession):
    await _limpar(db)
    yield
    await _limpar(db)


def _agora() -> datetime:
    return datetime.now(UTC)


async def _leitor(db: AsyncSession, *, visto_min: int | None) -> ChamadoLeitor:
    row = ChamadoLeitor(
        nome="Executor de leitura (teste)",
        token_hash=hashlib.sha256(uuid4().hex.encode()).hexdigest(),
        last_used_at=_agora() - timedelta(minutes=visto_min) if visto_min is not None else None,
    )
    db.add(row)
    await db.commit()
    return row


async def _devolucao(
    db: AsyncSession,
    *,
    disputa_h: float = 1,
    lido_h: float | None = None,
    ultima_fala_h: float | None = None,
    plataforma: str = "shopee",
    resolvido: bool = False,
) -> Chamado:
    """Devolução Shopee contestada pela API (como a do 296012). `disputa_h` =
    há quanto tempo a disputa saiu; `ultima_fala_h` = última fala da Shopee."""
    ch = Chamado(
        id=uuid4(),
        pedido_bling=f"vrl-{uuid4().hex[:6]}",
        pedido_marketplace="260910MATESNVN",
        plataforma=plataforma,
        conta="Shopee Vortan",
        origem="devolucao",
        chamado=f"26092{uuid4().hex[:10].upper()}",
        canal="api",
        resolvido=resolvido,
        leitura_robo_at=_agora() - timedelta(hours=lido_h) if lido_h is not None else None,
    )
    db.add(ch)
    await db.flush()
    m = chamados_svc.nova_mensagem(
        ch, texto="Solicitamos a análise do caso.", tipo="abertura", direcao="enviada",
        autor_nome="robô", status="enviada",
    )
    m.canal = "api"
    m.created_at = m.enviada_at = _agora() - timedelta(hours=disputa_h)
    db.add(m)
    if ultima_fala_h is not None:
        f = chamados_svc.nova_mensagem(
            ch, texto="Analisamos sua solicitação.", tipo="resposta", direcao="recebida",
            autor_nome="Agente da Shopee", status="registrada",
        )
        f.created_at = f.enviada_at = _agora() - timedelta(hours=ultima_fala_h)
        db.add(f)
    await db.commit()
    await db.refresh(ch)
    return ch


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


async def _por_chave(db: AsyncSession, chave: str) -> OuvidoriaOcorrencia | None:
    return (
        await db.execute(
            select(OuvidoriaOcorrencia)
            .where(OuvidoriaOcorrencia.robo_chave == ROBO, OuvidoriaOcorrencia.chave == chave)
            .order_by(OuvidoriaOcorrencia.aberta_em.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


# ─── o robô ────────────────────────────────────────────────────────────────


async def test_sem_robo_e_sem_fila_nao_cobra_ninguem(db):
    r = await vigia.vigia_robo_leitura_run(db)
    assert await _abertas(db) == []
    assert r["resumo"] == "0 devoluções na fila · robô nunca deu sinal"


async def test_nunca_deu_sinal_com_caso_na_fila(db):
    await _leitor(db, visto_min=None)
    await _devolucao(db)
    await vigia.vigia_robo_leitura_run(db)
    o = await _por_chave(db, "executor:sem_sinal")
    assert o is not None and "1 devolução esperando leitura" in o.detalhe


async def test_sem_sinal_abre_e_fecha_quando_volta(db):
    leitor = await _leitor(db, visto_min=45)
    await vigia.vigia_robo_leitura_run(db)
    o = await _por_chave(db, "executor:sem_sinal")
    assert o is not None and o.fechada_em is None and "sem sinal há 45 min" in o.titulo
    leitor.last_used_at = _agora() - timedelta(minutes=2)
    await db.commit()
    r = await vigia.vigia_robo_leitura_run(db)
    assert await _abertas(db) == [] and r["executor_ok"] == 1 and r["sumiram"] == 1


# ─── os casos ──────────────────────────────────────────────────────────────


async def test_caso_lido_dentro_da_cadencia_nao_abre(db):
    await _leitor(db, visto_min=1)
    await _devolucao(db, disputa_h=30, lido_h=4, ultima_fala_h=10)
    r = await vigia.vigia_robo_leitura_run(db)
    assert await _abertas(db) == [] and r["casos_na_fila"] == 1


async def test_caso_sem_leitura_ha_mais_de_6h_abre_e_fecha_quando_lido(db):
    await _leitor(db, visto_min=1)
    ch = await _devolucao(db, disputa_h=30, lido_h=7, ultima_fala_h=10)
    r = await vigia.vigia_robo_leitura_run(db)
    o = await _por_chave(db, f"caso:{ch.id}")
    assert o is not None and o.fechada_em is None
    assert o.pedido == ch.pedido_bling and o.conta == "Shopee Vortan"
    assert o.link == f"/chamados?search={ch.pedido_bling}"
    assert "Última leitura" in o.detalhe and r["resumo"].startswith("1 devolução na fila · 1 sem")
    ch.leitura_robo_at = _agora()
    await db.commit()
    await vigia.vigia_robo_leitura_run(db)
    assert await _abertas(db) == []


async def test_nunca_lido_conta_desde_a_disputa(db):
    await _leitor(db, visto_min=1)
    recente = await _devolucao(db, disputa_h=2)
    velho = await _devolucao(db, disputa_h=7, ultima_fala_h=7)
    await vigia.vigia_robo_leitura_run(db)
    assert await _abertas(db) == [f"caso:{velho.id}"]
    o = await _por_chave(db, f"caso:{velho.id}")
    assert "nunca foi lida" in o.detalhe
    assert await _por_chave(db, f"caso:{recente.id}") is None


async def test_caso_frio_so_cobra_depois_de_27h(db):
    """Ninguém fala há mais de 15 dias: o robô lê 1×/dia, então 10 h sem leitura
    é o normal — 28 h não."""
    await _leitor(db, visto_min=1)
    ch = await _devolucao(db, disputa_h=24 * 20, lido_h=10, ultima_fala_h=24 * 20)
    await vigia.vigia_robo_leitura_run(db)
    assert await _abertas(db) == []
    ch.leitura_robo_at = _agora() - timedelta(hours=28)
    await db.commit()
    await vigia.vigia_robo_leitura_run(db)
    assert await _abertas(db) == [f"caso:{ch.id}"]


@pytest.mark.parametrize("kw", [{"plataforma": "ml"}, {"resolvido": True}])
async def test_chamado_fora_da_fila_nao_vira_ocorrencia(db, kw):
    await _leitor(db, visto_min=1)
    await _devolucao(db, disputa_h=48, lido_h=40, ultima_fala_h=40, **kw)
    r = await vigia.vigia_robo_leitura_run(db)
    assert await _abertas(db) == [] and r["casos_na_fila"] == 0


# ─── sweep e catálogo ──────────────────────────────────────────────────────


async def test_sweep_e_serializado_pelo_advisory_lock(db):
    from app.services.advisory_lock import SYNC_NAMESPACE

    got = (
        await db.execute(
            text("SELECT pg_try_advisory_xact_lock(:ns, :key)"),
            {"ns": SYNC_NAMESPACE, "key": vigia._SWEEP_LOCK_KEY},
        )
    ).scalar()
    assert got
    assert await vigia.vigia_robo_leitura_sweep() == {"skipped": "lock_busy"}
    await db.rollback()


async def test_nasce_silencioso_com_o_nome_do_painel(db):
    await svc.sincronizar_catalogo(db)
    await db.commit()
    robo = await db.get(OuvidoriaRobo, ROBO)
    assert robo.modo == "silencioso" and robo.nome == "Vigia Robô Leitura de Chamados"
    assert robo.area == "chamados"
