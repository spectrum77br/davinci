"""Conferência Shopee — aviso no Threema, link do Excel e os dois crons do worker.

Threema (services/conferencia_shopee/threema_aviso): o texto do contrato §7
("📊 … (semana fechada)", uma linha por grupo + Geral, sem dados, afiliados
incompletos, os dois links) em até 3500 bytes; o token do link (7 dias, só
daquela execução); manda UMA vez (carimbo), só com a chave ligada e alguém
cadastrado, e falha de envio não carimba; a última loja pelo HTTP avisa, e o
Threema que explode não derruba o resultado.

Worker: `conferencia_shopee_agenda` só cria com CONFERENCIA_SHOPEE_CRON (e o
Marketing ligado), pula com outra coletando; `conferencia_shopee_varrer`
expira e fecha no prazo e manda o aviso pendente só com a chave do Threema.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app import worker
from app.config import get_settings
from app.models import ConferenciaShopeeExecucao, ThreemaInformarConfig
from app.routers import marketing_conferencia as mc
from app.services import threema
from app.services.conferencia_shopee import fila, periodos
from app.services.conferencia_shopee import threema_aviso as ta
from tests.test_conferencia_shopee_calculo import relatorio_exemplo
from tests.test_conferencia_shopee_fila import AGORA, coletas_de, dados_loja, semear_contas

pytestmark = pytest.mark.asyncio

API = "/api/marketing/conferencia-shopee"
TOKEN = "tok-conferencia-threema"  # noqa: S105 (só do teste)
SEMANAS = periodos.semanas("semanal", date(2026, 10, 6))
VAZIA = {"vendas": None, "invest_afiliados": None, "invest_ads": None, "pct": None}


@pytest.fixture(scope="module", autouse=True)
def _monta_router():
    from app.main import app

    if not any(getattr(r, "path", "").startswith(API) for r in app.routes):
        app.include_router(mc.router)


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    """`liga(nome, valor)` muda a config dos DOIS lados: o worker guarda o
    objeto da subida (`worker._settings`) e um teste anterior pode ter limpado
    o cache do `get_settings()` — aí são objetos diferentes."""

    def liga(nome: str, valor) -> None:
        for cfg in {id(c): c for c in (get_settings(), worker._settings)}.values():
            monkeypatch.setattr(cfg, nome, valor)

    liga("app_url", "https://davinci.teste")
    liga("marketing_agent_token", TOKEN)
    liga("conferencia_shopee_threema", True)
    liga("conferencia_shopee_cron", False)
    monkeypatch.setattr(mc, "_agora", lambda: AGORA)
    return liga


class Lista(list):
    pass


@pytest.fixture
def enviados(monkeypatch):
    lst = Lista()
    lst.falhar = False
    lst.explodir = False

    async def _send_to_all(self, text, recipients=None):
        if lst.explodir:
            raise RuntimeError("gateway fora")
        if lst.falhar:
            return {"sent": [], "failed": list(recipients or [])}
        lst.append((text, list(recipients or [])))
        return {"sent": list(recipients or []), "failed": []}

    monkeypatch.setattr(threema.ThreemaClient, "send_to_all", _send_to_all)
    monkeypatch.setattr(threema.ThreemaClient, "disabled", property(lambda self: False))
    return lst


async def _cadastro(db, ids: str = "M5TT27JA,9BH6R7HJ") -> None:
    db.add(ThreemaInformarConfig(contexto=ta.CONTEXTO, recipients=ids))
    await db.commit()


async def _pronta(db, *, finalizado: datetime = AGORA, enviado: datetime | None = None):
    ex = ConferenciaShopeeExecucao(
        tipo="semanal", origem="agenda", semanas=SEMANAS, afiliados_ate=date(2026, 10, 4),
        esperar_afiliados_ate=AGORA, corte=AGORA, prazo=AGORA, status="pronto",
        relatorio=relatorio_exemplo(), finalizado_em=finalizado, threema_enviado_em=enviado,
        criado_em=AGORA,
    )
    db.add(ex)
    await db.commit()
    return ex


# ───────────────────────────────────────────────────────────── texto


def _total(contas: int, s1: dict, s2: dict | None = None) -> dict:
    return {"contas": contas, "sem_dados": 0, "semanas": [s1, s2 or VAZIA, VAZIA, VAZIA]}


def _rel(tipo: str = "semanal", semanas: list[dict] = SEMANAS, **extra) -> dict:
    return {
        "tipo": tipo,
        "semanas": [{**s, "rotulo": periodos.rotulo(s["inicio"], s["fim"])} for s in semanas],
        "grupos": [
            {"chave": "mala", "rotulo": "Mala", "linhas": [], "total": _total(
                4,
                {"vendas": 120345.0, "invest_afiliados": 5000.0, "invest_ads": 4876.0,
                 "pct": 8.21},
                {"vendas": 111225.0, "invest_afiliados": 4500.0, "invest_ads": 5076.0,
                 "pct": 8.61},
            )},
            {"chave": "celular", "rotulo": "Celular", "linhas": [], "total": _total(
                9, {"vendas": 50000.0, "invest_afiliados": 1500.0, "invest_ads": 500.0,
                    "pct": 4.0},
            )},
            # Nenhuma conta com eletro: a linha some.
            {"chave": "eletro", "rotulo": "Eletro", "linhas": [], "total": _total(0, VAZIA)},
        ],
        "geral": _total(
            13,
            {"vendas": 170345.0, "invest_afiliados": 6500.0, "invest_ads": 5376.0, "pct": 6.97},
            {"vendas": 111225.0, "invest_afiliados": 4500.0, "invest_ads": 4500.0, "pct": 8.09},
        ),
        "contas_sem_dados": [
            {"conta": "Luno", "status": "sem_automacao", "erro": "perfil Firefox"},
            {"conta": "Oliveira", "status": "perfil_em_uso", "erro": None},
        ],
        "afiliados_incompletos": [{"conta": "Mega", "ate": "2026-10-03"}],
        **extra,
    }


async def test_texto_do_contrato():
    msg = ta.texto(_rel(), "https://app/x", "https://app/marketing?aba=conferencia&execucao=1")
    assert msg == (
        "📊 Conferência Shopee — 28/09 a 04/10/2026 (semana fechada)\n"
        "Mala: vendas R$ 120.345 (▲ 8,2%) · invest. R$ 9.876 · 8,2% s/ vendas (▼ 0,4 p.p.)\n"
        "Celular: vendas R$ 50.000 · invest. R$ 2.000 · 4,0% s/ vendas\n"
        "Geral: vendas R$ 170.345 (▲ 53,2%) · invest. R$ 11.876 · 7,0% s/ vendas (▼ 1,1 p.p.)\n"
        "⚠️ Sem dados: Luno (sem automação), Oliveira (perfil em uso)\n"
        "⚠️ Afiliados incompletos: Mega (até 03/10)\n"
        "📎 Excel: https://app/x\n"
        "🔗 No DaVinci: https://app/marketing?aba=conferencia&execucao=1"
    )


async def test_texto_parcial_e_sem_avisos():
    sem = periodos.semanas("parcial", date(2026, 10, 8))
    msg = ta.texto(_rel("parcial", sem, contas_sem_dados=[], afiliados_incompletos=[]), "L", "D")
    linhas = msg.split("\n")
    assert linhas[0] == "📊 Conferência Shopee — 05/10 a 07/10/2026 (parcial seg–qua)"
    assert not any(linha.startswith("⚠️") for linha in linhas)


async def test_texto_cabe_em_3500_bytes():
    muitas = [
        {"conta": f"Conta com um nome bem comprido número {i}", "status": "erro", "erro": "x"}
        for i in range(400)
    ]
    msg = ta.texto(_rel(contas_sem_dados=muitas), "https://app/x", "https://app/d")
    assert len(msg.encode("utf-8")) <= 3500
    assert "e mais 392" in msg
    assert msg.endswith("📎 Excel: https://app/x\n🔗 No DaVinci: https://app/d")
    # Nem os links cabem (app_url absurdo): corta sem quebrar caractere.
    enorme = ta.texto(_rel(), "https://app/" + "é" * 4000, "D")
    assert len(enorme.encode("utf-8")) <= 3500


async def test_texto_do_relatorio_de_verdade():
    rel = relatorio_exemplo()
    msg = ta.texto(rel, "L", "D")
    linhas = msg.split("\n")
    assert linhas[0] == "📊 Conferência Shopee — 28/09 a 04/10/2026 (semana fechada)"
    assert [linha.split(":")[0] for linha in linhas[1:5]] == ["Mala", "Celular", "Eletro", "Geral"]
    assert "⚠️ Sem dados: Luno (sem automação)" in linhas
    assert "⚠️ Afiliados incompletos: Barbosa (até 03/10)" in linhas


# ───────────────────────────────────────────────────────────── link


async def test_link_do_excel_so_daquela_execucao_e_por_7_dias():
    eid = "3f1c2d4e-0000-4000-8000-000000000001"
    link = ta.link_excel(eid, AGORA)
    assert link.startswith(
        f"https://davinci.teste/api/marketing/conferencia-shopee/execucoes/{eid}/excel/link?t="
    )
    t = link.split("t=", 1)[1]
    assert ta.confere_excel(eid, t, AGORA)
    assert ta.confere_excel(eid, t, AGORA + timedelta(days=6, hours=23))
    assert not ta.confere_excel(eid, t, AGORA + timedelta(days=7, seconds=1))  # venceu
    assert not ta.confere_excel("3f1c2d4e-0000-4000-8000-000000000002", t, AGORA)
    ate, _, assinatura = t.partition(".")
    assert not ta.confere_excel(eid, f"{int(ate) + 999}.{assinatura}", AGORA)
    assert not ta.confere_excel(eid, "", AGORA)
    assert not ta.confere_excel(eid, "abc", AGORA)
    # Link público: token torto é "não", nunca exceção (que viraria 500).
    for torto in (f"{ate}.é{assinatura[1:]}", "9999999999.é", "².abc", f"{'1' * 5000}.ab",
                  f"{ate}.", f".{assinatura}", f"{ate}.{assinatura}\udcff"):
        assert not ta.confere_excel(eid, torto, AGORA), torto
    assert ta.link_davinci(eid) == (
        f"https://davinci.teste/marketing?aba=conferencia&execucao={eid}"
    )


# ───────────────────────────────────────────────────────────── envio


async def test_link_do_excel_com_token_torto_e_403_nao_500(client, db):
    eid = "3f1c2d4e-0000-4000-8000-000000000001"
    for t in ("9999999999.%C3%A9", "%C2%B2.abc", "1" * 5000 + ".ab"):
        r = await client.get(f"{API}/execucoes/{eid}/excel/link?t={t}")
        assert r.status_code == 403, (t[:20], r.status_code)
        assert r.json()["detail"]["code"] == "conferencia_link_invalido"


async def test_denuncia_tambem_recusa_token_torto_sem_excecao():
    from app.services import denuncia_relatorio_threema as dn

    dia = date(2026, 10, 5)
    bom = dn.link_excel(dia, AGORA).split("t=", 1)[1]
    assert dn.confere_excel(dia, bom, AGORA)
    for torto in ("9999999999.é", "².abc", f"{'1' * 5000}.ab"):
        assert not dn.confere_excel(dia, torto, AGORA), torto


async def test_rede_que_cai_no_segundo_nao_reenvia_ao_primeiro(db, monkeypatch):
    """O send_to_all de verdade só segura ThreemaSendError: um timeout de rede
    no 2º destinatário escapava depois de o 1º já ter recebido, o carimbo não
    saía e o varredor reenviava a todos de 10 em 10 min."""
    import httpx

    monkeypatch.setattr(threema.ThreemaClient, "disabled", property(lambda self: False))
    monkeypatch.setattr(threema.ThreemaClient, "_require_config", lambda self: None)
    entregues: list[str] = []

    async def _send_simple(self, to, text):
        if to == "9BH6R7HJ":
            raise httpx.ConnectTimeout("timeout")
        entregues.append(to)
        return "mid"

    monkeypatch.setattr(threema.ThreemaClient, "send_simple", _send_simple)
    await _cadastro(db)
    ex = await _pronta(db)
    for _ in range(3):  # três voltas do varredor
        await ta.enviar_pendentes(db, AGORA + timedelta(minutes=10))
        await db.commit()
    await db.refresh(ex)
    assert entregues == ["M5TT27JA"], "o 1º recebe UMA vez"
    assert ex.threema_enviado_em == AGORA + timedelta(minutes=10)


async def test_manda_uma_vez_so(db, enviados):
    await _cadastro(db)
    ex = await _pronta(db)
    r = await ta.enviar_pendente(db, ex, AGORA)
    await db.commit()
    assert r["enviado"] is True and r["sent"] == ["M5TT27JA", "9BH6R7HJ"]
    # Um destinatário por envio (a falha de rede de um não pega o outro).
    assert [alvos for _, alvos in enviados] == [["M5TT27JA"], ["9BH6R7HJ"]]
    msg = enviados[0][0]
    assert enviados[1][0] == msg
    link = next(linha for linha in msg.split("\n") if linha.startswith("📎 Excel: "))
    assert ta.confere_excel(ex.id, link.split("t=", 1)[1], AGORA)
    assert msg.endswith(
        f"🔗 No DaVinci: https://davinci.teste/marketing?aba=conferencia&execucao={ex.id}"
    )
    await db.refresh(ex)
    assert ex.threema_enviado_em == AGORA
    r = await ta.enviar_pendente(db, ex, AGORA)
    assert r == {"enviado": False, "motivo": "já enviado"}
    assert len(enviados) == 2


async def test_nao_manda_desligado_sem_ninguem_ou_falhando(db, enviados, _config):
    ex = await _pronta(db)
    # Ninguém cadastrado (a migration deixa a linha vazia).
    assert (await ta.enviar_pendente(db, ex, AGORA))["motivo"] == "ninguém cadastrado"
    await _cadastro(db, "")
    assert (await ta.enviar_pendente(db, ex, AGORA))["motivo"] == "ninguém cadastrado"
    await db.execute(
        ThreemaInformarConfig.__table__.update().values(recipients="M5TT27JA")
    )
    await db.commit()
    _config("conferencia_shopee_threema", False)
    assert (await ta.enviar_pendente(db, ex, AGORA))["motivo"] == "aviso no Threema desligado"
    _config("conferencia_shopee_threema", True)
    enviados.falhar = True
    assert (await ta.enviar_pendente(db, ex, AGORA))["motivo"] == "envio falhou"
    enviados.falhar, enviados.explodir = False, True
    assert (await ta.enviar_pendente(db, ex, AGORA))["motivo"] == "envio falhou"
    await db.commit()
    await db.refresh(ex)
    assert ex.threema_enviado_em is None and enviados == []


async def test_reenvio_so_das_recentes_nao_avisadas(db, enviados):
    await _cadastro(db)
    recente = await _pronta(db, finalizado=AGORA - timedelta(hours=1))
    await _pronta(db, finalizado=AGORA - timedelta(days=2))
    await _pronta(db, finalizado=AGORA - timedelta(hours=2), enviado=AGORA)
    r = await ta.enviar_pendentes(db, AGORA)
    await db.commit()
    assert [x["execucao"] for x in r] == [str(recente.id)]
    assert len(enviados) == 2  # uma rodada, dois destinatários


async def test_ultima_loja_pelo_http_avisa_no_threema(client, db, enviados):
    await _cadastro(db)
    await semear_contas(db, (("k-ana", "Ana", "mala", True, 0),))
    ex = await fila.criar_execucao(db, "semanal", "agenda", None, AGORA)
    await db.commit()
    agente = {"X-Agent-Token": TOKEN}
    job = (await client.post(f"{API}/agent/lease", json={}, headers=agente)).json()["job"]
    r = await client.post(
        f"{API}/agent/coletas/{job['coleta_id']}/resultado",
        json={"status": "ok", "dados": dados_loja(ex.semanas)},
        headers=agente,
    )
    assert r.status_code == 200 and r.json()["execucao_pronta"] is True
    assert len(enviados) == 2  # um envio por destinatário
    assert enviados[0][0].startswith("📊 Conferência Shopee — 28/09 a 04/10/2026")
    await db.refresh(ex)
    assert ex.threema_enviado_em == AGORA


async def test_threema_que_explode_nao_derruba_o_resultado(client, db, enviados, monkeypatch):
    await _cadastro(db)
    await semear_contas(db, (("k-ana", "Ana", "mala", True, 0),))
    ex = await fila.criar_execucao(db, "semanal", "agenda", None, AGORA)
    await db.commit()

    async def _quebra(*a, **k):
        raise RuntimeError("banco do Threema fora")

    monkeypatch.setattr(ta, "destinatarios", _quebra)
    agente = {"X-Agent-Token": TOKEN}
    job = (await client.post(f"{API}/agent/lease", json={}, headers=agente)).json()["job"]
    r = await client.post(
        f"{API}/agent/coletas/{job['coleta_id']}/resultado",
        json={"status": "ok", "dados": dados_loja(ex.semanas)},
        headers=agente,
    )
    assert r.status_code == 200
    assert r.json()["execucao_pronta"] is True
    await db.refresh(ex)
    assert (ex.status, ex.threema_enviado_em) == ("pronto", None)


# ───────────────────────────────────────────────────────────── worker


async def _execucoes(db) -> list[ConferenciaShopeeExecucao]:
    return list((await db.execute(select(ConferenciaShopeeExecucao))).scalars())


async def test_agenda_so_com_a_chave_ligada(db, _config, monkeypatch):
    await semear_contas(db)
    monkeypatch.setattr(periodos, "tipo_da_agenda", lambda dia: "semanal")
    _config("enable_marketing", True)
    await worker.conferencia_shopee_agenda({})
    assert await _execucoes(db) == []  # CONFERENCIA_SHOPEE_CRON desligada

    _config("conferencia_shopee_cron", True)
    _config("enable_marketing", False)
    await worker.conferencia_shopee_agenda({})
    assert await _execucoes(db) == []  # Marketing desligado: ninguém coletaria

    _config("enable_marketing", True)
    await worker.conferencia_shopee_agenda({})
    (ex,) = await _execucoes(db)
    assert (ex.origem, ex.criado_por, ex.status) == ("agenda", None, "coletando")
    assert len(await coletas_de(db, ex.id)) == 3
    # Outra coletando: pula, sem erro.
    await worker.conferencia_shopee_agenda({})
    assert len(await _execucoes(db)) == 1


async def test_agenda_em_dia_sem_rodada_nao_cria(db, _config, monkeypatch):
    await semear_contas(db)
    _config("enable_marketing", True)
    _config("conferencia_shopee_cron", True)
    monkeypatch.setattr(periodos, "tipo_da_agenda", lambda dia: None)
    await worker.conferencia_shopee_agenda({})
    assert await _execucoes(db) == []


async def test_varrer_fecha_no_prazo_e_avisa_uma_vez(db, enviados):
    await _cadastro(db)
    await semear_contas(db, (("k-ana", "Ana", "mala", True, 0),))
    ontem = datetime.now(UTC) - timedelta(days=1)
    ex = await fila.criar_execucao(db, "semanal", "agenda", None, ontem)
    await db.commit()
    await worker.conferencia_shopee_varrer({})
    await db.refresh(ex)
    assert ex.status == "pronto" and ex.threema_enviado_em is not None
    ((c),) = await coletas_de(db, ex.id)
    assert (c.status, c.erro) == ("expirada", fila.ERRO_PRAZO)
    assert len(enviados) == 2  # um envio por destinatário
    await worker.conferencia_shopee_varrer({})
    assert len(enviados) == 2


async def test_varrer_sem_a_chave_do_threema_nao_manda(db, enviados, _config):
    await _cadastro(db)
    _config("conferencia_shopee_threema", False)
    await semear_contas(db, (("k-ana", "Ana", "mala", True, 0),))
    ex = await fila.criar_execucao(
        db, "semanal", "agenda", None, datetime.now(UTC) - timedelta(days=1)
    )
    await db.commit()
    await worker.conferencia_shopee_varrer({})
    await db.refresh(ex)
    assert ex.status == "pronto" and ex.threema_enviado_em is None
    assert enviados == []
