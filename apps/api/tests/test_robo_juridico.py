"""Robô Jurídico (Ouvidoria, 12/11/2026): confere a atualização das 06:00 dos processos e avisa
o Cairo no Threema se ela (ou algum processo) não atualizou. Ver services/robo_juridico."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.models import OuvidoriaOcorrencia, OuvidoriaRobo
from app.models.juridico import JuridicoLeitura, JuridicoPedido, JuridicoProcesso
from app.services import ouvidoria, threema
from app.services import robo_juridico as rj

pytestmark = pytest.mark.asyncio

# 12/11/2026 07:30 em Brasília; o pedido do cron nasce 06:00
AGORA = datetime(2026, 11, 12, 10, 30, tzinfo=UTC)
PEDIDO_EM = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
NUM = "42069436420268260100"

_TABELAS = (
    "ouvidoria_ocorrencias",
    "ouvidoria_rodadas",
    "ouvidoria_robos",
    "juridico_movimentacoes",
    "juridico_leituras",
    "juridico_pedidos",
    "juridico_processos",
)


@pytest_asyncio.fixture(autouse=True)
async def _limpa(db):
    for t in _TABELAS:
        await db.execute(text(f"DELETE FROM {t}"))  # noqa: S608
    await db.commit()
    yield
    for t in _TABELAS:
        await db.execute(text(f"DELETE FROM {t}"))  # noqa: S608
    await db.commit()


async def _pedido(db, *, pego=True, terminado=True, ok=True, resultado=None, progresso=None):
    p = JuridicoPedido(
        pedido_em=PEDIDO_EM,
        origem="cron",
        jusbrasil=True,
        pego_em=PEDIDO_EM + timedelta(minutes=1) if pego else None,
        terminado_em=PEDIDO_EM + timedelta(minutes=30) if terminado else None,
        ok=ok if terminado else None,
        resultado=resultado,
        progresso=progresso,
    )
    db.add(p)
    await db.commit()
    return p


async def _leitura(db, *, falhas=0, erro=None):
    db.add(
        JuridicoLeitura(
            quando=PEDIDO_EM + timedelta(minutes=5),
            origem="mini",
            processos=3,
            publicacoes=10,
            novas=0,
            falhas=falhas,
            erro=erro,
        )
    )
    await db.commit()


async def _processo(
    db, *, numero=NUM, pedido="300100", tentado=None, erro=None, situacao="em_andamento"
):
    p = JuridicoProcesso(
        origem="manual",
        numero=numero,
        pedido=pedido,
        situacao=situacao,
        jusbrasil_tentado_em=tentado,
        jusbrasil_erro=erro,
    )
    db.add(p)
    await db.commit()
    return p


async def _abertas(db) -> dict[str, OuvidoriaOcorrencia]:
    rows = (
        (
            await db.execute(
                select(OuvidoriaOcorrencia).where(
                    OuvidoriaOcorrencia.robo_chave == rj.ROBO,
                    OuvidoriaOcorrencia.fechada_em.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    return {o.chave: o for o in rows}


async def test_catalogo_nasce_silencioso(db):
    await ouvidoria.sincronizar_catalogo(db)
    await db.commit()
    robo = await db.get(OuvidoriaRobo, rj.ROBO)
    assert robo.modo == "silencioso" and robo.nome == "Robô Jurídico"


async def test_sem_pedido_hoje_avisa_que_nao_rodou(db):
    await _processo(db)
    res = await rj.robo_juridico_run(db, agora=AGORA)
    o = (await _abertas(db))["atualizacao:2026-11-12"]
    assert "não foi pedida" in o.titulo and o.link == "/ouvidoria/juridico"
    # problema da atualização inteira: não cobra processo por processo
    assert res["falhas"] == 1 and res["processos"] == 0


async def test_mini_nao_pegou_ou_nao_terminou(db):
    await _pedido(db, pego=False, terminado=False)
    await rj.robo_juridico_run(db, agora=AGORA)
    o = (await _abertas(db))["atualizacao:2026-11-12"]
    assert "Mac mini não pegou" in o.titulo and o.acao == rj.ACAO_MINI

    await db.execute(text("DELETE FROM juridico_pedidos"))
    await db.commit()
    await _pedido(db, terminado=False, progresso="Jusbrasil 3/7")
    await rj.robo_juridico_run(db, agora=AGORA + timedelta(minutes=1))
    o = (await _abertas(db))["atualizacao:2026-11-12"]
    assert "não terminou — parou em: Jusbrasil 3/7" in o.titulo


async def test_jusbrasil_que_falhou_ou_ficou_de_fora(db):
    await _pedido(db)
    await _leitura(db)
    ok = await _processo(db, pedido="1", tentado=PEDIDO_EM + timedelta(minutes=10))
    falhou = await _processo(
        db,
        pedido="2",
        tentado=PEDIDO_EM + timedelta(minutes=12),
        erro="a busca do Jusbrasil não achou o processo",
    )
    fora = await _processo(db, pedido="3", tentado=PEDIDO_EM - timedelta(days=1))
    await _processo(db, pedido="4", tentado=PEDIDO_EM, erro="falhou", situacao="encerrado")
    await _processo(db, pedido="5", numero=None)

    res = await rj.robo_juridico_run(db, agora=AGORA)

    abertas = await _abertas(db)
    assert set(abertas) == {f"jusbrasil:{falhou.id}:2026-11-12", f"jusbrasil:{fora.id}:2026-11-12"}
    o = abertas[f"jusbrasil:{falhou.id}:2026-11-12"]
    assert o.titulo == "pedido 2: Jusbrasil falhou — a busca do Jusbrasil não achou o processo"
    assert "4206943-64.2026.8.26.0100" in o.detalhe
    assert "não foi atualizado hoje" in abertas[f"jusbrasil:{fora.id}:2026-11-12"].titulo
    assert res["processos"] == 3 and res["falhas"] == 2 and ok.id


async def test_diario_nao_lido_ou_com_falha(db):
    await _pedido(db)
    await _processo(db, tentado=PEDIDO_EM + timedelta(minutes=10))
    await rj.robo_juridico_run(db, agora=AGORA)
    assert "não foi lido" in (await _abertas(db))["diario:2026-11-12"].titulo

    await _leitura(db, falhas=2, erro="DJEN 403")
    await rj.robo_juridico_run(db, agora=AGORA + timedelta(minutes=1))
    assert "com falha hoje: DJEN 403" in (await _abertas(db))["diario:2026-11-12"].titulo


async def test_tudo_ok_nao_abre_e_fecha_o_de_ontem(db):
    await ouvidoria.sincronizar_catalogo(db)
    await db.commit()
    await ouvidoria.registrar(
        db, rj.ROBO, "jusbrasil:1:2026-11-11", titulo="ontem", agora=AGORA - timedelta(days=1)
    )
    await db.commit()
    await _pedido(db)
    await _leitura(db)
    await _processo(db, tentado=PEDIDO_EM + timedelta(minutes=10))

    res = await rj.robo_juridico_run(db, agora=AGORA)

    assert await _abertas(db) == {}
    assert res["falhas"] == 0 and res["sumiram"] == 1
    assert res["resumo"] == "atualização de hoje ok · 1 processo(s)"


async def test_ligado_avisa_no_threema_quem_esta_no_avisar(db, monkeypatch):
    enviados: list[tuple[str, list[str]]] = []

    async def _send(self, msg, recipients=None):
        enviados.append((msg, list(recipients or [])))
        return {"sent": list(recipients or []), "failed": []}

    monkeypatch.setattr(threema.ThreemaClient, "send_to_all", _send)
    await ouvidoria.sincronizar_catalogo(db)
    robo = await db.get(OuvidoriaRobo, rj.ROBO)
    robo.modo = "ligado"
    robo.threema_recipients = "CAIRO123"
    await db.commit()
    await _pedido(db, ok=False, resultado="perfil 50 não abriu")

    res = await rj.robo_juridico_run(db, agora=AGORA)

    assert res["avisadas"] == 1 and len(enviados) == 1
    msg, alvos = enviados[0]
    assert alvos == ["CAIRO123"] and "perfil 50 não abriu" in msg


async def test_silencioso_registra_e_nao_manda(db, monkeypatch):
    chamado = []
    monkeypatch.setattr(threema.ThreemaClient, "send_to_all", lambda *a, **k: chamado.append(1))
    res = await rj.robo_juridico_run(db, agora=AGORA)
    assert res["falhas"] == 1 and res["avisadas"] == 0 and not chamado


def test_numero_fmt():
    assert rj.numero_fmt(NUM) == "4206943-64.2026.8.26.0100"
    assert rj.numero_fmt("123") == "123"
