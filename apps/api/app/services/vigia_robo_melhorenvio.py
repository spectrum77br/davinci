"""Vigia Robô Melhor Envio — o robô da Ouvidoria que olha o "Suspender entrega".

Vinicius, 24/09/2026: o "Suspender entrega" da Amazon (Envio próprio) é feito
por um executor no Mac Santiago, que abre o perfil 70 do AdsPower e clica no
painel do Melhor Envio (ver services/logistica_robo.py). Quem aperta o botão
só vê "na fila do robô" — se o Mac dormiu, o AdsPower fechou ou o Melhor
Envio pediu login de novo, ninguém fica sabendo, e suspensão é corrida contra
o tempo (quanto antes, maior a chance; depois de entregue não tem volta).

## O que ele abre (tudo de ESTADO: a rodada re-vê e o que não re-viu fecha)

1. `executor:offline` — o executor parou de dar sinal (ele carimba
   `marketing_agent_heartbeat` com o nome `logistica:<agente>` a cada 60 s).
   Sem sinal nenhum desde sempre só vira ocorrência se houver suspensão
   esperando — ambiente sem executor (dev) não cobra ninguém.
2. `executor:adspower` / `executor:perfil` / `executor:trava` — online, mas o
   AdsPower não responde, o perfil do Melhor Envio não está configurado, ou a
   trava MELHORENVIO_CALIBRATED está ligada (modo seco: todo pedido volta
   "falhou" de propósito).
3. `comando:<id>` — pedido de suspensão PENDENTE (ninguém pegou) ou PRESO
   (`claimed` sem resposta) há mais que `pendente_min`. Nasce `pessoa` e vira
   `urgente` depois de 2×.
4. `suspensao:<logistica_id>` — a linha da Logística está `falhou` e o pacote
   ainda não foi entregue. Fecha quando alguém pede de novo (vira `pendente`),
   quando dá certo (`solicitada`) ou quando o pacote é entregue (não há mais
   o que suspender). Falha com mais de `_JANELA_FALHAS` só não ABRE linha nova.

Só lê banco (nenhuma API externa) e só AVISA: nada aqui mexe na fila.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import Logistica, LogisticaRoboComando, OuvidoriaOcorrencia, OuvidoriaRobo
from app.models.marketing import AGENTE_LOGISTICA_PREFIXO
from app.services import logistica_robo, ouvidoria
from app.services.advisory_lock import SYNC_NAMESPACE

logger = structlog.get_logger()

ROBO = "vigia_robo_melhorenvio"

# Advisory lock do sweep (namespace SYNC compartilhado).
_SWEEP_LOCK_KEY = 0x76726D65  # ascii "vrme"

# Padrões quando a config do robô não tem a chave (a linha de ouvidoria_robos
# nasce com estes mesmos valores — services/ouvidoria.ROBOS).
_PENDENTE_MIN = 30
_EXECUTOR_OFFLINE_MIN = 10
# Idade máxima (desde o pedido) de uma suspensão que falhou pra ela virar
# ocorrência NOVA. Não fecha nada: aberta continua aberta enquanto a linha
# estiver `falhou` e o pacote não chegar.
_JANELA_FALHAS = timedelta(days=7)

_TZ_BR = ZoneInfo("America/Sao_Paulo")

LINK_PAINEL = "/logistica?tab=amazon&sub=proprio"
QUEM = "Robô do Melhor Envio (Mac Santiago)"

ACAO_OFFLINE = (
    "Ver se o Mac Santiago está ligado, acordado, com a tampa aberta e com "
    "internet — o robô liga sozinho quando o Mac liga"
)
ACAO_ADSPOWER = (
    "Abrir o AdsPower no Mac Santiago (logado, com a API liberada) — o robô usa "
    "a API dele pra abrir o perfil do Melhor Envio"
)
ACAO_FILA = (
    "Ver se o Mac Santiago está ligado e com o AdsPower aberto; se a entrega não "
    "pode esperar, suspender à mão no Melhor Envio (Liberados e postados)"
)
ACAO_PERFIL = "Pôr MELHORENVIO_ADSPOWER_USER_ID no .env do executor (Mac Santiago)"
ACAO_TRAVA = "Virar MELHORENVIO_CALIBRATED pra true no .env do executor (Mac Santiago)"
ACAO_FALHOU = (
    "Ler o motivo e pedir de novo no botão da linha (Logística › Amazon › Envio "
    "próprio) — ou suspender à mão no Melhor Envio"
)
ACAO_LOGIN = (
    "Entrar no Melhor Envio no perfil 70 do AdsPower (Mac Santiago) e pedir de "
    "novo no botão da linha"
)
ACAO_SEM_CONFIRMACAO = (
    "Conferir no painel do Melhor Envio (Liberados e postados) se a entrega já "
    "está suspensa ANTES de pedir de novo"
)

# Contadores de uma rodada (ouvidoria_rodadas.contadores): pendentes = ninguém
# pegou; presos = `claimed` sem resposta; falhas = linha `falhou` com o pacote
# ainda a caminho; executor_ok = 1 quando o robô deu sinal no prazo.
_CONTADORES = (
    "suspensoes_pendentes", "suspensoes_presas", "suspensoes_falhas",
    "novas", "persistem", "sumiram", "executor_ok", "executor_idade_min",
)


def _br(dt: datetime | None) -> str:
    if dt is None:
        return "?"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(_TZ_BR).strftime("%d/%m %H:%M")


def _minutos(inicio: datetime | None, agora: datetime) -> int:
    if inicio is None:
        return 0
    if inicio.tzinfo is None:
        inicio = inicio.replace(tzinfo=UTC)
    return max(0, int((agora - inicio).total_seconds() // 60))


def _idade(minutos: int) -> str:
    if minutos < 90:
        return f"{minutos} min"
    if minutos < 48 * 60:
        return f"{minutos // 60} h"
    return f"{minutos // 1440} dias"


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def _link(row: Logistica | None) -> str:
    if row is not None and row.pedido_bling:
        return f"{LINK_PAINEL}&q={row.pedido_bling}"
    return LINK_PAINEL


def _motivo(detalhe: str | None) -> tuple[str, bool]:
    """(motivo em uma frase, clicou_de_verdade). O executor devolve o JSON do
    que viu; `dry=false` sem `requested` = clicou em "Suspender entrega" e
    nenhuma confirmação apareceu — pode JÁ estar suspensa."""
    if not detalhe:
        return "o robô não disse o motivo", False
    try:
        j = json.loads(detalhe)
    except (ValueError, TypeError):
        return detalhe[:300], False
    if not isinstance(j, dict):
        return detalhe[:300], False
    motivo = str(j.get("reason") or j.get("detail") or detalhe)[:300]
    return motivo, j.get("dry") is False and not j.get("requested")


async def _registrar(r: ouvidoria.Rodada, agora: datetime, **campos) -> None:
    """`r.registrar` + os contadores novas/persistem (ocorrência que o núcleo
    devolveu fechada — ignorada, ou tratada há < 24 h — não conta)."""
    row = await r.registrar(agora=agora, **campos)
    if row.fechada_em is not None:
        return
    if row.aberta_em == agora:
        r.contadores["novas"] += 1
    else:
        r.contadores["persistem"] += 1


async def _abertas(session: AsyncSession) -> set[str]:
    return set(
        (
            await session.execute(
                select(OuvidoriaOcorrencia.chave).where(
                    OuvidoriaOcorrencia.robo_chave == ROBO,
                    OuvidoriaOcorrencia.fechada_em.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )


# ─── regras ────────────────────────────────────────────────────────────────


async def _na_fila(
    session: AsyncSession, r: ouvidoria.Rodada, *, agora: datetime, pendente_min: int
) -> int:
    """Regra 3. Devolve quantas suspensões estão esperando (pro texto do
    executor offline)."""
    cmds = (
        (
            await session.execute(
                select(LogisticaRoboComando).where(
                    LogisticaRoboComando.acao == logistica_robo.ACAO_SUSPENDER,
                    LogisticaRoboComando.status.in_(("pending", "claimed")),
                )
            )
        )
        .scalars()
        .all()
    )
    for cmd in cmds:
        preso = cmd.status == "claimed"
        desde = cmd.claimed_at if preso else cmd.created_at
        minutos = _minutos(desde, agora)
        if minutos < pendente_min:
            continue
        row = await session.get(Logistica, cmd.logistica_id) if cmd.logistica_id else None
        rastreio = (cmd.payload or {}).get("rastreio") or (row.rastreio if row else None)
        pedido_em = _br(cmd.created_at)
        if preso:
            r.contadores["suspensoes_presas"] += 1
            titulo = f"Suspensão da entrega presa no robô há {_idade(minutos)}"
            detalhe = (
                f"Rastreio {rastreio or '?'}: pedida às {pedido_em}, o robô pegou às "
                f"{_br(cmd.claimed_at)} e não respondeu (tentativa {cmd.attempts})"
            )
        else:
            r.contadores["suspensoes_pendentes"] += 1
            titulo = f"Suspensão da entrega esperando o robô há {_idade(minutos)}"
            detalhe = f"Rastreio {rastreio or '?'}: pedida às {pedido_em} e o robô ainda não pegou"
        await _registrar(
            r,
            agora,
            chave=f"comando:{cmd.id}",
            plataforma="amazon",
            conta=row.conta if row else None,
            pedido=row.pedido_bling if row else None,
            titulo=titulo,
            detalhe=detalhe + " — cada minuto conta: entregue, não tem mais volta",
            acao=ACAO_FILA,
            link=_link(row),
            severidade="urgente" if minutos >= 2 * pendente_min else "pessoa",
            precisa_pessoa=True,
            dados={"rastreio": rastreio, "status": cmd.status, "minutos": minutos},
        )
    return len(cmds)


async def _falhas(
    session: AsyncSession, r: ouvidoria.Rodada, *, agora: datetime, abertas: set[str]
) -> None:
    """Regra 4 — linha `falhou` com o pacote ainda a caminho."""
    rows = (
        (
            await session.execute(
                select(Logistica).where(
                    Logistica.suspensao_status == logistica_robo.STATUS_FALHOU,
                    Logistica.entregue_em.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        chave = f"suspensao:{row.id}"
        recente = row.suspensao_em is not None and agora - row.suspensao_em <= _JANELA_FALHAS
        if not recente and chave not in abertas:
            continue
        r.contadores["suspensoes_falhas"] += 1
        motivo, clicou = _motivo(row.suspensao_detalhe)
        if clicou:
            acao, severidade = ACAO_SEM_CONFIRMACAO, "urgente"
        elif "needs_manual_login" in (row.suspensao_detalhe or ""):
            acao, severidade = ACAO_LOGIN, "pessoa"
        else:
            acao, severidade = ACAO_FALHOU, "pessoa"
        await _registrar(
            r,
            agora,
            chave=chave,
            plataforma="amazon",
            conta=row.conta,
            pedido=row.pedido_bling,
            titulo="Suspensão da entrega falhou — o pacote segue pro cliente",
            detalhe=f"Rastreio {row.rastreio or '?'}, pedida às {_br(row.suspensao_em)}: {motivo}",
            acao=acao,
            link=_link(row),
            severidade=severidade,
            precisa_pessoa=True,
            dados={"rastreio": row.rastreio, "motivo": motivo},
        )


async def _executor(
    session: AsyncSession,
    r: ouvidoria.Rodada,
    *,
    agora: datetime,
    offline: timedelta,
    esperando: int,
) -> str:
    """Regras 1 e 2. Devolve o pedaço do resumo (o estado do robô aparece
    sempre — é a primeira pergunta de quem abre o painel)."""
    hb = await logistica_robo.ultimo_heartbeat(session)
    if hb is None or hb.last_seen_at is None:
        if not esperando:
            logger.debug("vigia_robo_melhorenvio_sem_executor")
            return "robô nunca deu sinal"
        await _registrar(
            r,
            agora,
            chave="executor:offline",
            plataforma="amazon",
            titulo=f"{QUEM} nunca deu sinal",
            detalhe=(
                "Nenhum sinal de vida do robô e "
                + _plural(esperando, "suspensão esperando", "suspensões esperando")
            ),
            acao=ACAO_OFFLINE,
            link=LINK_PAINEL,
            severidade="urgente",
            precisa_pessoa=True,
            dados={"ultimo_sinal_em": None},
        )
        return "robô nunca deu sinal"

    minutos = _minutos(hb.last_seen_at, agora)
    info = hb.info or {}
    agente = hb.agent_name.removeprefix(AGENTE_LOGISTICA_PREFIXO)
    r.contadores["executor_idade_min"] = minutos
    dados = {
        "agent_name": agente,
        "ultimo_sinal_em": hb.last_seen_at.isoformat(),
        "idade_min": minutos,
        "versao": info.get("version"),
        "calibrado": info.get("melhorenvio_calibrated"),
    }
    if agora - hb.last_seen_at > offline:
        detalhe = (
            f"Último sinal às {_br(hb.last_seen_at)} (BRT); enquanto isso nenhum "
            "\"Suspender entrega\" é feito"
        )
        if esperando:
            detalhe += (
                " — há "
                + _plural(esperando, "suspensão esperando", "suspensões esperando")
            )
        await _registrar(
            r,
            agora,
            chave="executor:offline",
            plataforma="amazon",
            titulo=f"{QUEM} sem sinal há {_idade(minutos)}",
            detalhe=detalhe,
            acao=ACAO_OFFLINE,
            link=LINK_PAINEL,
            # Sem suspensão esperando ninguém perde nada AGORA; com, é corrida.
            severidade="urgente" if esperando else "pessoa",
            precisa_pessoa=True,
            dados=dados,
        )
        return f"robô sem sinal há {_idade(minutos)}"

    r.contadores["executor_ok"] = 1
    partes = ["robô ok"]
    if hb.adspower_ok is False:
        partes = ["robô online, AdsPower fechado"]
        await _registrar(
            r,
            agora,
            chave="executor:adspower",
            plataforma="amazon",
            titulo=f"{QUEM} online, mas o AdsPower não responde",
            detalhe=(
                f"Sinal de vida às {_br(hb.last_seen_at)} (BRT) com a API do AdsPower "
                "fora do ar — toda suspensão vai falhar"
            ),
            acao=ACAO_ADSPOWER,
            link=LINK_PAINEL,
            severidade="pessoa",
            precisa_pessoa=True,
            dados=dados,
        )
    if info.get("perfil_melhorenvio") is False:
        partes.append("sem perfil do Melhor Envio")
        await _registrar(
            r,
            agora,
            chave="executor:perfil",
            plataforma="amazon",
            titulo=f"{QUEM} sem o perfil do Melhor Envio configurado",
            detalhe="MELHORENVIO_ADSPOWER_USER_ID vazio: toda suspensão volta como falhou",
            acao=ACAO_PERFIL,
            link=LINK_PAINEL,
            severidade="pessoa",
            precisa_pessoa=True,
            dados=dados,
        )
    if info.get("melhorenvio_calibrated") is False:
        partes.append("modo seco")
        await _registrar(
            r,
            agora,
            chave="executor:trava",
            plataforma="amazon",
            titulo=f"{QUEM} no modo seco (trava ligada)",
            detalhe=(
                "MELHORENVIO_CALIBRATED não está 'true': o robô acha o envio e para "
                "antes do clique, então todo pedido volta como falhou"
            ),
            acao=ACAO_TRAVA,
            link=LINK_PAINEL,
            severidade="info",
            precisa_pessoa=False,
            dados=dados,
        )
    return " · ".join(partes)


# ─── rodada, sweep ─────────────────────────────────────────────────────────


async def vigia_robo_melhorenvio_run(session: AsyncSession) -> dict:
    """Uma varredura dentro de uma `Rodada`. Erro derruba a rodada inteira
    (ok=False) sem fechar nada: "não consegui olhar" nunca vira "sumiu"."""
    async with ouvidoria.Rodada(session, ROBO) as r:
        for k in _CONTADORES:
            r.contadores[k] = 0
        robo = await session.get(OuvidoriaRobo, ROBO)
        cfg = ouvidoria.config_do_robo(robo, ROBO)
        pendente_min = int(cfg.get("pendente_min") or _PENDENTE_MIN)
        offline = timedelta(
            minutes=int(cfg.get("executor_offline_min") or _EXECUTOR_OFFLINE_MIN)
        )
        agora = datetime.now(UTC)
        abertas = await _abertas(session)

        esperando = await _na_fila(session, r, agora=agora, pendente_min=pendente_min)
        await _falhas(session, r, agora=agora, abertas=abertas)
        executor_txt = await _executor(
            session, r, agora=agora, offline=offline, esperando=esperando
        )

        # Todas as ocorrências deste robô são de ESTADO: o que a rodada não
        # re-viu, sumiu.
        r.contadores["sumiram"] = await r.fechar_nao_vistas()

        partes = []
        if r.contadores["suspensoes_pendentes"]:
            partes.append(f"{r.contadores['suspensoes_pendentes']} esperando")
        if r.contadores["suspensoes_presas"]:
            partes.append(_plural(r.contadores["suspensoes_presas"], "presa", "presas"))
        if r.contadores["suspensoes_falhas"]:
            partes.append(_plural(r.contadores["suspensoes_falhas"], "falhou", "falharam"))
        partes.append(executor_txt)
        r.resumo = " · ".join(partes)

    aviso = await ouvidoria.avisar_pendentes(session, ROBO)
    return {**dict(r.contadores), "avisadas": aviso.get("avisadas", 0), "resumo": r.resumo}


async def vigia_robo_melhorenvio_sweep() -> dict:
    """Sweep do cron / "Rodar agora": sessão própria, serializado por advisory
    lock numa sessão SÓ do lock (a `Rodada` commita ao sair, e o commit
    soltaria o lock). O modo do robô é olhado no tick do worker."""
    async with session_scope() as trava:
        got = (
            await trava.execute(
                text("SELECT pg_try_advisory_xact_lock(:ns, :key)"),
                {"ns": SYNC_NAMESPACE, "key": _SWEEP_LOCK_KEY},
            )
        ).scalar()
        if not got:
            return {"skipped": "lock_busy"}
        async with session_scope() as session:
            return await vigia_robo_melhorenvio_run(session)
