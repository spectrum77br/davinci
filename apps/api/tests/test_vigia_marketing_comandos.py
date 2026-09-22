"""Comandos de Ads não aplicados (robô da Ouvidoria) — o que este arquivo trava.

- falha MANUAL (ou Oferta Relâmpago) abre `comando:<id>` em `pessoa`, com a
  ação traduzida do erro que o executor devolveu, e fecha como "sumiu" quando
  um comando POSTERIOR da mesma conta + ação + campanha volta `done`;
- a janela de 24 h vale só pra ABRIR: falha velha que ninguém abriu não vira
  passivo, e ocorrência já aberta continua sendo re-vista sem limite de idade;
- falha da AGENDA é UMA ocorrência por CONTA (`agenda:<account_id>`) com o
  número de tentativas desde o último acerto — nunca uma por comando (o
  reconciler enfileira 1 por minuto enquanto o executor falha);
- `pending` mais velho que `pendente_min` abre em `baixa` sem pessoa e vira
  `pessoa` depois de 2×; `claimed` sem resposta é "preso no executor" (o caso
  real de produção, que trava a agenda da loja) e, quando vira `failed`,
  mantém a MESMA chave trocando o título (manual) ou fecha e abre a agenda;
- heartbeat ausente/velho abre `executor:offline` em `urgente` e fecha quando
  o sinal volta; AdsPower fora do ar e a trava SELECTORS_CALIBRATED abrem as
  suas próprias linhas;
- o sweep é serializado por advisory lock e o tick sai sem rodar com o robô
  `desligado`.

Nada aqui fala com o Mac nem com marketplace: o robô só lê `marketing_commands`
e `marketing_agent_heartbeat`, então os testes escrevem nessas tabelas direto.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OuvidoriaOcorrencia, OuvidoriaRobo, OuvidoriaRodada
from app.models.marketing import (
    MarketingAccount,
    MarketingAgentHeartbeat,
    MarketingCommand,
)
from app.services import ouvidoria as svc
from app.services import vigia_marketing_comandos as vigia

pytestmark = pytest.mark.asyncio

ROBO = vigia.ROBO


# ─── fixtures ──────────────────────────────────────────────────────────────


async def _limpar(db: AsyncSession) -> None:
    for tbl in (
        "ouvidoria_ocorrencias",
        "ouvidoria_rodadas",
        "ouvidoria_robos",
        # marketing_commands tem FK CASCADE pra marketing_accounts: filhos antes.
        "marketing_commands",
        "marketing_accounts",
        "marketing_agent_heartbeat",
    ):
        await db.execute(text(f"DELETE FROM {tbl}"))  # noqa: S608
    await db.commit()


@pytest_asyncio.fixture(autouse=True)
async def _limpa(db: AsyncSession):
    await _limpar(db)
    yield
    await _limpar(db)


class _Threema:
    enviados: list[tuple[str, list[str]]] = []

    async def send_to_all(self, texto: str, recipients=None) -> dict:
        self.enviados.append((texto, list(recipients or [])))
        return {"sent": list(recipients or []), "failed": []}


@pytest.fixture(autouse=True)
def _sem_threema(monkeypatch) -> list[tuple[str, list[str]]]:
    _Threema.enviados = []
    monkeypatch.setattr(svc.threema, "ThreemaClient", _Threema)
    return _Threema.enviados


def _agora() -> datetime:
    return datetime.now(UTC)


async def _conta(
    db: AsyncSession,
    *,
    nome: str = "Inova",
    plataforma: str = "shopee",
    adspower: str | None = None,
    applied_state: str | None = None,
) -> MarketingAccount:
    """Uma conta do Marketing. `adspower` nasce None de propósito: sem perfil
    do AdsPower a regra do executor sai de cena (ela só faz sentido onde o
    executor do Mac teria trabalho), então cada teste liga o que quer olhar."""
    acc = MarketingAccount(
        name=nome,
        platform=plataforma,
        department="mala",
        adspower_user_id=adspower,
        applied_state=applied_state,
    )
    db.add(acc)
    await db.commit()
    await db.refresh(acc)
    return acc


async def _comando(
    db: AsyncSession,
    acc: MarketingAccount,
    *,
    acao: str = "pause",
    status: str = "failed",
    fonte: str = "manual",
    executor: str = "browser",
    resultado: str | None = None,
    tentativas: int = 1,
    criado: datetime | None = None,
    pego: datetime | None = None,
    concluido: datetime | None = None,
    campanha: str | None = None,
) -> MarketingCommand:
    quando = criado or _agora()
    if concluido is None and status in ("done", "failed"):
        concluido = quando
    cmd = MarketingCommand(
        account_id=acc.id,
        platform=acc.platform,
        action=acao,
        payload={},
        status=status,
        source=fonte,
        executor=executor,
        result=resultado,
        attempts=tentativas,
        campaign_external_id=campanha,
        claimed_at=pego,
        completed_at=concluido,
        created_at=quando,
        updated_at=quando,
    )
    db.add(cmd)
    await db.commit()
    await db.refresh(cmd)
    return cmd


async def _heartbeat(
    db: AsyncSession,
    *,
    nome: str = "executor-mac",
    visto: datetime | None = None,
    adspower_ok: bool | None = True,
    calibrado: bool = True,
) -> MarketingAgentHeartbeat:
    hb = MarketingAgentHeartbeat(
        agent_name=nome,
        last_seen_at=visto if visto is not None else _agora(),
        adspower_ok=adspower_ok,
        accounts_online=100,
        info={"version": "1.0.0", "calibrated": calibrado, "default_mode": "manual"},
    )
    db.add(hb)
    await db.commit()
    await db.refresh(hb)
    return hb


async def _abertas(db: AsyncSession) -> list[OuvidoriaOcorrencia]:
    return list(
        (
            await db.execute(
                select(OuvidoriaOcorrencia)
                .where(
                    OuvidoriaOcorrencia.robo_chave == ROBO,
                    OuvidoriaOcorrencia.fechada_em.is_(None),
                )
                .order_by(OuvidoriaOcorrencia.chave)
            )
        )
        .scalars()
        .all()
    )


async def _por_chave(db: AsyncSession, chave: str) -> OuvidoriaOcorrencia | None:
    return (
        await db.execute(
            select(OuvidoriaOcorrencia)
            .where(
                OuvidoriaOcorrencia.robo_chave == ROBO,
                OuvidoriaOcorrencia.chave == chave,
            )
            .order_by(OuvidoriaOcorrencia.aberta_em.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _rodadas(db: AsyncSession) -> list[OuvidoriaRodada]:
    return list(
        (
            await db.execute(
                select(OuvidoriaRodada)
                .where(OuvidoriaRodada.robo_chave == ROBO)
                .order_by(OuvidoriaRodada.iniciada_em)
            )
        )
        .scalars()
        .all()
    )


# ─── 1) falha de comando manual / Oferta Relâmpago ─────────────────────────


async def test_falha_manual_abre_pessoa_e_fecha_quando_refeito(db):
    acc = await _conta(db, nome=" Inova")  # nome com espaço na frente (prod)
    cmd = await _comando(db, acc, resultado="needs_manual_login", tentativas=2)

    r1 = await vigia.vigia_marketing_comandos_run(db)
    o = await _por_chave(db, f"comando:{cmd.id}")
    assert o is not None and o.fechada_em is None
    assert o.titulo == "Pausar anúncios não aplicado — Shopee Inova"
    assert o.severidade == "pessoa" and o.precisa_pessoa is True
    assert o.plataforma == "shopee" and o.conta == "Shopee Inova"
    assert o.link == "/marketing"
    assert "Entrar na Shopee no perfil do AdsPower" in o.acao
    assert "O executor do Mac devolveu: needs_manual_login" in o.detalhe
    assert "tentativa 2" in o.detalhe and "pedido por manual" in o.detalhe
    assert o.dados["comando_id"] == str(cmd.id) and o.dados["fonte"] == "manual"
    assert o.dados["executor"] == "browser" and o.dados["campanha"] is None
    assert r1["comandos_falhos"] == 1 and r1["novas"] == 1 and r1["sumiram"] == 0
    # Sem conta com perfil do AdsPower o executor nem entra na conversa.
    assert r1["resumo"] == "1 falho · sem executor configurado"

    # Rodada seguinte: a mesma linha, sem abrir nada novo.
    r2 = await vigia.vigia_marketing_comandos_run(db)
    assert r2["novas"] == 0 and r2["persistem"] == 1 and r2["sumiram"] == 0
    assert len(await _abertas(db)) == 1

    # Alguém refez a mesma ação e desta vez foi: fecha sozinha.
    await _comando(db, acc, status="done", criado=_agora())
    r3 = await vigia.vigia_marketing_comandos_run(db)
    assert r3["comandos_falhos"] == 0 and r3["sumiram"] == 1
    o = await _por_chave(db, f"comando:{cmd.id}")
    assert o.fechada_em is not None and o.fechamento == "sumiu"
    assert o.fechada_por == "robô"


async def test_refeito_so_conta_quando_e_a_mesma_acao_e_campanha(db):
    """`done` de OUTRA ação (ou de outra campanha) não desfaz a falha."""
    acc = await _conta(db, plataforma="ml", nome="kfa")
    cmd = await _comando(
        db, acc, acao="pause", executor="api", campanha="C-1",
        resultado="account_has_no_integration",
    )
    await _comando(db, acc, acao="resume", status="done", campanha="C-1")
    await _comando(db, acc, acao="pause", status="done", campanha="C-2")

    r = await vigia.vigia_marketing_comandos_run(db)
    o = await _por_chave(db, f"comando:{cmd.id}")
    assert o.fechada_em is None and r["comandos_falhos"] == 1
    assert o.titulo == "Pausar campanha C-1 não aplicado — Mercado Livre kfa"
    assert o.conta == "Mercado Livre kfa" and o.plataforma == "ml"
    assert o.acao == "Vincular a integração à conta em Marketing"
    assert "O worker_marketing_agent devolveu" in o.detalhe

    # Agora sim, a MESMA ação na MESMA campanha depois da falha.
    await _comando(db, acc, acao="pause", status="done", campanha="C-1")
    r = await vigia.vigia_marketing_comandos_run(db)
    assert r["sumiram"] == 1


async def test_falha_flash_duplicate_da_agenda_entra_como_comando(db):
    """Oferta Relâmpago nasce com source='schedule', mas é 1 por dia por loja:
    fica na chave do comando (e não na agregada por conta)."""
    acc = await _conta(db)
    cmd = await _comando(
        db, acc, acao="flash_duplicate", fonte="schedule",
        resultado="ação não suportada pelo executor: flash_duplicate",
    )
    r = await vigia.vigia_marketing_comandos_run(db)
    o = await _por_chave(db, f"comando:{cmd.id}")
    assert o is not None and r["comandos_falhos"] == 1 and r["agendas_falhas"] == 0
    assert o.titulo == "Duplicar Oferta Relâmpago não aplicado — Shopee Inova"
    assert o.acao == "Atualizar o executor do Mac (versão antiga)"
    assert "pedido por agenda" in o.detalhe


async def test_janela_de_24h_so_vale_pra_abrir(db):
    acc = await _conta(db)
    velho = _agora() - timedelta(days=30)
    await _comando(db, acc, resultado="needs_manual_login", criado=velho)

    r = await vigia.vigia_marketing_comandos_run(db)
    assert r["comandos_falhos"] == 0 and await _abertas(db) == []

    # A MESMA falha, agora recente: abre. Depois envelhece — e continua aberta.
    cmd = await _comando(db, acc, resultado="needs_manual_login")
    assert (await vigia.vigia_marketing_comandos_run(db))["novas"] == 1
    cmd.completed_at = velho
    await db.commit()
    r = await vigia.vigia_marketing_comandos_run(db)
    assert r["persistem"] == 1 and r["sumiram"] == 0
    o = await _por_chave(db, f"comando:{cmd.id}")
    assert o.fechada_em is None


# ─── 2) agenda não aplicada ────────────────────────────────────────────────


async def test_agenda_vira_uma_ocorrencia_por_conta(db):
    acc = await _conta(db, applied_state="on")
    base = _agora()
    # O reconciler enfileira 1 por minuto enquanto o executor falha.
    for i in (30, 20, 10):
        await _comando(
            db, acc, fonte="schedule", acao="pause", status="failed",
            resultado="AdsPower inacessivel em http://local.adspower.net:50325",
            criado=base - timedelta(minutes=i),
        )
    r = await vigia.vigia_marketing_comandos_run(db)

    abertas = await _abertas(db)
    assert [o.chave for o in abertas] == [f"agenda:{acc.id}"]
    o = abertas[0]
    assert o.titulo == "Agenda não aplicada (pausar) — Shopee Inova"
    assert o.severidade == "pessoa" and o.precisa_pessoa is True
    assert "A agenda tentou pausar 3 vezes desde" in o.detalhe
    assert "AdsPower inacessivel" in o.detalhe
    assert o.acao.endswith("a agenda tenta de novo sozinha a cada minuto")
    assert o.dados["falhas_seguidas"] == 3 and o.dados["applied_state"] == "on"
    assert o.dados["conta_id"] == str(acc.id)
    assert r["agendas_falhas"] == 1 and r["comandos_falhos"] == 0 and r["novas"] == 1

    # A agenda voltou a pegar: fecha sozinha.
    await _comando(db, acc, fonte="schedule", acao="resume", status="done")
    r = await vigia.vigia_marketing_comandos_run(db)
    assert r["agendas_falhas"] == 0 and r["sumiram"] == 1


async def test_agenda_conta_as_tentativas_desde_o_ultimo_acerto(db):
    acc = await _conta(db)
    base = _agora()
    await _comando(
        db, acc, fonte="schedule", acao="resume", status="done",
        criado=base - timedelta(hours=5),
    )
    # Duas falhas DEPOIS do acerto + uma falha de antes (que não conta).
    await _comando(
        db, acc, fonte="schedule", acao="pause", status="failed",
        criado=base - timedelta(hours=9), resultado="needs_manual_login",
    )
    for h in (3, 1):
        await _comando(
            db, acc, fonte="schedule", acao="pause", status="failed",
            criado=base - timedelta(hours=h), resultado="needs_manual_login",
        )
    await vigia.vigia_marketing_comandos_run(db)
    o = await _por_chave(db, f"agenda:{acc.id}")
    assert o.dados["falhas_seguidas"] == 2


async def test_agenda_velha_sem_ocorrencia_aberta_nao_vira_passivo(db):
    acc = await _conta(db)
    await _comando(
        db, acc, fonte="schedule", acao="pause", status="failed",
        criado=_agora() - timedelta(days=60), resultado="needs_manual_login",
    )
    r = await vigia.vigia_marketing_comandos_run(db)
    assert r["agendas_falhas"] == 0 and await _abertas(db) == []


# ─── 3) pendente e preso ───────────────────────────────────────────────────


async def test_pendente_sobe_de_baixa_pra_pessoa_e_fecha_no_done(db):
    acc = await _conta(db)
    cmd = await _comando(db, acc, status="pending", tentativas=0)

    # Novinho: a fila normal, ninguém precisa saber.
    r = await vigia.vigia_marketing_comandos_run(db)
    assert r["comandos_pendentes"] == 0 and await _abertas(db) == []

    cmd.created_at = _agora() - timedelta(minutes=40)
    await db.commit()
    r = await vigia.vigia_marketing_comandos_run(db)
    o = await _por_chave(db, f"comando:{cmd.id}")
    assert o.titulo == "Pausar anúncios pendente há 40 min — Shopee Inova"
    assert o.severidade == "baixa" and o.precisa_pessoa is False
    assert "o executor do mac ainda não pegou" in o.detalhe.lower()
    assert o.acao == vigia.ACAO_PENDENTE_BROWSER
    assert o.dados["status"] == "pending" and o.dados["idade_min"] == 40
    assert o.dados["pego_em"] is None
    assert r["comandos_pendentes"] == 1 and r["novas"] == 1

    # Passou de 2× `pendente_min`: a MESMA linha vira caso de gente.
    cmd.created_at = _agora() - timedelta(minutes=70)
    await db.commit()
    r = await vigia.vigia_marketing_comandos_run(db)
    o = await _por_chave(db, f"comando:{cmd.id}")
    assert o.severidade == "pessoa" and o.precisa_pessoa is True
    assert "pendente há 70 min" in o.titulo and r["novas"] == 0

    cmd.status = "done"
    cmd.completed_at = _agora()
    await db.commit()
    r = await vigia.vigia_marketing_comandos_run(db)
    assert r["sumiram"] == 1 and (await _por_chave(db, f"comando:{cmd.id}")).fechamento == "sumiu"


async def test_pendente_do_ml_aponta_pro_worker_e_mostra_o_quique(db):
    acc = await _conta(db, plataforma="ml", nome="kfa")
    await _comando(
        db, acc, status="pending", executor="api", acao="set_budget",
        criado=_agora() - timedelta(hours=2), resultado="rate_limited:shopee",
    )
    await vigia.vigia_marketing_comandos_run(db)
    o = (await _abertas(db))[0]
    assert o.titulo.startswith("Orçamento esperando o worker_marketing_agent há 2 h")
    assert o.acao == vigia.ACAO_FILA_API
    assert "último retorno: rate_limited:shopee" in o.detalhe


async def test_claimed_preso_e_a_troca_de_titulo_quando_vira_falha(db):
    acc = await _conta(db)
    pego = _agora() - timedelta(hours=3)
    cmd = await _comando(
        db, acc, status="claimed", pego=pego, concluido=None,
        criado=pego - timedelta(minutes=1), tentativas=1,
    )
    r = await vigia.vigia_marketing_comandos_run(db)
    o = await _por_chave(db, f"comando:{cmd.id}")
    assert o.titulo == "Pausar anúncios preso no executor há 3 h — Shopee Inova"
    assert o.severidade == "pessoa" and o.precisa_pessoa is True
    assert o.acao == vigia.ACAO_PRESO_BROWSER
    assert "não respondeu" in o.detalhe and o.dados["pego_em"] is not None
    assert r["comandos_presos"] == 1 and r["comandos_pendentes"] == 0

    # O executor finalmente reportou a falha: MESMA chave, título novo.
    cmd.status = "failed"
    cmd.result = "needs_manual_login"
    cmd.completed_at = _agora()
    await db.commit()
    r = await vigia.vigia_marketing_comandos_run(db)
    depois = await _por_chave(db, f"comando:{cmd.id}")
    assert depois.id == o.id and depois.fechada_em is None
    assert depois.titulo == "Pausar anúncios não aplicado — Shopee Inova"
    assert r["comandos_presos"] == 0 and r["comandos_falhos"] == 1
    assert r["sumiram"] == 0


async def test_preso_da_agenda_fecha_e_abre_a_ocorrencia_da_conta(db):
    acc = await _conta(db)
    pego = _agora() - timedelta(days=60)
    cmd = await _comando(
        db, acc, status="claimed", fonte="schedule", pego=pego, concluido=None,
        criado=pego,
    )
    await vigia.vigia_marketing_comandos_run(db)
    assert (await _abertas(db))[0].chave == f"comando:{cmd.id}"
    assert "preso no executor há 60 dias" in (await _abertas(db))[0].titulo

    cmd.status = "failed"
    cmd.result = "needs_manual_login"
    cmd.completed_at = _agora()
    await db.commit()
    r = await vigia.vigia_marketing_comandos_run(db)
    assert r["sumiram"] == 1 and r["agendas_falhas"] == 1
    assert [o.chave for o in await _abertas(db)] == [f"agenda:{acc.id}"]


# ─── 4 e 5) o executor do Mac ──────────────────────────────────────────────


async def test_executor_sem_sinal_abre_urgente_e_fecha_quando_volta(db):
    acc = await _conta(db, adspower="ads-1")
    await _comando(db, acc, status="pending", criado=_agora() - timedelta(hours=1))

    # Nunca reportou (e a loja tem perfil do AdsPower: o executor faz falta).
    r = await vigia.vigia_marketing_comandos_run(db)
    o = await _por_chave(db, "executor:offline")
    assert o.titulo == "Executor do Mac nunca reportou"
    assert o.severidade == "urgente" and o.precisa_pessoa is True
    assert o.plataforma == "shopee" and o.conta is None
    assert "launchctl load" in o.acao and o.link == "/marketing"
    assert r["executor_ok"] == 0 and r["resumo"].endswith("executor nunca reportou")

    hb = await _heartbeat(db, visto=_agora() - timedelta(minutes=60))
    r = await vigia.vigia_marketing_comandos_run(db)
    o = await _por_chave(db, "executor:offline")
    assert o.titulo == "Executor do Mac (executor-mac) sem sinal há 60 min"
    assert "há 1 comando pendente esperando" in o.detalhe
    assert o.dados["agent_name"] == "executor-mac" and o.dados["idade_min"] == 60
    assert o.dados["versao"] == "1.0.0" and o.dados["calibrado"] is True
    assert r["executor_idade_min"] == 60 and r["executor_ok"] == 0
    assert "executor sem sinal há 60 min" in r["resumo"]

    hb.last_seen_at = _agora()
    await db.commit()
    r = await vigia.vigia_marketing_comandos_run(db)
    assert (await _por_chave(db, "executor:offline")).fechamento == "sumiu"
    assert r["executor_ok"] == 1 and r["resumo"].endswith("executor ok")


async def test_sem_loja_com_adspower_nao_cobra_executor(db):
    """Ambiente sem executor (dev, ou marketing desligado) não abre urgência."""
    await _conta(db)  # sem adspower_user_id
    r = await vigia.vigia_marketing_comandos_run(db)
    assert await _abertas(db) == [] and r["resumo"] == "sem executor configurado"


async def test_adspower_fechado_e_trava_de_seguranca(db):
    await _conta(db, adspower="ads-1")
    hb = await _heartbeat(db, adspower_ok=False, calibrado=False)
    r = await vigia.vigia_marketing_comandos_run(db)

    ads = await _por_chave(db, "executor:adspower")
    assert ads.titulo == "Executor do Mac online, mas o AdsPower não responde"
    assert ads.severidade == "pessoa" and "local.adspower.net" in ads.acao
    trava = await _por_chave(db, "executor:trava")
    assert trava.severidade == "info" and trava.precisa_pessoa is False
    assert "SELECTORS_CALIBRATED" in trava.detalhe
    assert await _por_chave(db, "executor:offline") is None
    assert r["executor_ok"] == 1
    assert r["resumo"] == "executor online, AdsPower fechado · trava ligada"

    hb.adspower_ok = True
    hb.info = {**hb.info, "calibrated": True}
    hb.last_seen_at = _agora()
    await db.commit()
    r = await vigia.vigia_marketing_comandos_run(db)
    assert r["sumiram"] == 2 and await _abertas(db) == []


async def test_heartbeat_mais_recente_ganha_mesmo_com_linha_sem_sinal(db):
    """DESC no Postgres põe NULL na frente: a linha que nunca carimbou não
    pode roubar o lugar do executor que está vivo."""
    await _conta(db, adspower="ads-1")
    await _heartbeat(db, nome="marionete", visto=None)
    await _heartbeat(db, nome="executor-mac", visto=_agora())
    r = await vigia.vigia_marketing_comandos_run(db)
    assert r["executor_ok"] == 1 and await _abertas(db) == []


# ─── rodada, aviso, sweep e tick ───────────────────────────────────────────


async def test_resumo_e_contadores_da_rodada(db):
    acc = await _conta(db, adspower="ads-1")
    await _comando(db, acc, resultado="needs_manual_login")
    for i in (5, 3):
        await _comando(
            db, acc, fonte="schedule", status="failed", resultado="needs_manual_login",
            criado=_agora() - timedelta(minutes=i),
        )
    await _comando(db, acc, status="pending", criado=_agora() - timedelta(hours=1))
    pego = _agora() - timedelta(hours=2)
    await _comando(db, acc, status="claimed", pego=pego, criado=pego, concluido=None)
    await _heartbeat(db)

    r = await vigia.vigia_marketing_comandos_run(db)
    assert r["resumo"] == (
        "1 falho · 1 agenda parada · 1 pendente · 1 preso no executor · executor ok"
    )
    assert r["novas"] == 4 and r["sumiram"] == 0 and r["avisadas"] == 0

    rodadas = await _rodadas(db)
    assert len(rodadas) == 1 and rodadas[0].ok is True
    assert rodadas[0].contadores["agendas_falhas"] == 1
    assert rodadas[0].contadores["executor_ok"] == 1
    robo = await db.get(OuvidoriaRobo, ROBO)
    assert robo.ultima_rodada_ok is True and robo.ultima_rodada_resumo == r["resumo"]
    # Robô novo nasce silencioso: registra no painel e não manda Threema.
    assert robo.modo == "silencioso"


async def test_so_avisa_no_threema_com_o_robo_ligado(db, _sem_threema):
    acc = await _conta(db)
    await _comando(db, acc, resultado="needs_manual_login")
    await svc.sincronizar_catalogo(db)
    robo = await db.get(OuvidoriaRobo, ROBO)
    robo.threema_recipients = "ABCDEFGH"
    await db.commit()

    assert (await vigia.vigia_marketing_comandos_run(db))["avisadas"] == 0
    assert _sem_threema == []

    robo.modo = "ligado"
    await db.commit()
    assert (await vigia.vigia_marketing_comandos_run(db))["avisadas"] == 1
    assert "Pausar anúncios não aplicado" in _sem_threema[0][0]


async def test_erro_de_banco_derruba_a_rodada_sem_fechar_nada(db, monkeypatch):
    acc = await _conta(db)
    cmd = await _comando(db, acc, resultado="needs_manual_login")
    # A chave fica guardada agora: o `rollback` do Rodada.__aexit__ expira os
    # objetos da sessão e ler `cmd.id` depois dispararia I/O fora do await.
    chave = f"comando:{cmd.id}"
    await vigia.vigia_marketing_comandos_run(db)

    async def _explode(*a, **kw):
        raise RuntimeError("banco caiu")

    monkeypatch.setattr(vigia, "_em_voo", _explode)
    with pytest.raises(RuntimeError, match="banco caiu"):
        await vigia.vigia_marketing_comandos_run(db)
    assert (await _rodadas(db))[-1].ok is False
    assert (await _por_chave(db, chave)).fechada_em is None


async def test_sweep_e_serializado_pelo_advisory_lock(db, monkeypatch):
    """Sessão que segura o lock do sweep → o outro sweep sai na hora."""
    from app.services.advisory_lock import SYNC_NAMESPACE

    got = (
        await db.execute(
            text("SELECT pg_try_advisory_xact_lock(:ns, :key)"),
            {"ns": SYNC_NAMESPACE, "key": vigia._SWEEP_LOCK_KEY},
        )
    ).scalar()
    assert got
    assert await vigia.vigia_marketing_comandos_sweep() == {"skipped": "lock_busy"}
    await db.rollback()  # solta o lock

    chamado = []

    async def _run(session):
        chamado.append(1)
        return {"ok": True}

    monkeypatch.setattr(vigia, "vigia_marketing_comandos_run", _run)
    assert await vigia.vigia_marketing_comandos_sweep() == {"ok": True}
    assert chamado == [1]


async def test_tick_nao_roda_com_o_robo_desligado(db, monkeypatch):
    from app import worker

    chamadas: list[int] = []

    async def _sweep():
        chamadas.append(1)
        return {"novas": 0}

    monkeypatch.setattr(vigia, "vigia_marketing_comandos_sweep", _sweep)
    await svc.sincronizar_catalogo(db)
    robo = await db.get(OuvidoriaRobo, ROBO)
    robo.modo = "desligado"
    await db.commit()
    await worker.vigia_marketing_comandos_tick({})
    assert chamadas == [] and await _rodadas(db) == []

    for modo in ("silencioso", "ligado"):
        robo.modo = modo
        await db.commit()
        await worker.vigia_marketing_comandos_tick({})
    assert chamadas == [1, 1]
