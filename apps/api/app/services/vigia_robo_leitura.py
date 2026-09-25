"""Vigia Robô Leitura de Chamados — o robô da Ouvidoria que olha o executor de
leitura (Mac Santiago).

Vinicius, 24/09/2026: o executor de leitura abre as devoluções da Shopee no
Seller Center e traz pro chamado o que a Shopee escreveu ("Histórico da
Solicitação") — a API só dá códigos (296012: recusa às 15:59, API ainda
"aguardando análise" às 16:09). Se o Mac dormir, o login do Seller Center cair
ou a página da Shopee mudar, o chamado volta a ficar mudo, e sem este vigia
ninguém fica sabendo ("não tô achando no ouvidoria robôs … esse aí").

## O que ele abre (tudo de ESTADO: a rodada re-vê e o que não re-viu fecha)

1. `executor:sem_sinal` — o robô parou de perguntar a fila. Ele pergunta a
   cada 10 min, e cada pergunta carimba `chamados_leitores.last_used_at`.
   Nunca-deu-sinal só vira ocorrência se houver caso esperando leitura.
2. `caso:<chamado_id>` — devolução ou consulta do Portal de Atendimento (25/09)
   da fila (mesma régua da fila do robô, `chamados_leitura.condicoes_do_leitor`)
   sem leitura além da
   cadência + `atraso_horas`: 3 h + 3 h = 6 h no caso normal; caso frio
   (ninguém fala há 15 dias) é lido 1×/dia, então 24 h + 3 h. Pega login
   caído, perfil sempre em uso, loja sem perfil no AdsPower e página mudada
   — a leitura que falha não avança `leitura_robo_at`.

Só lê banco (nenhuma API externa) e só AVISA: nada aqui mexe na fila.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import Chamado, ChamadoLeitor, ChamadoMensagem, OuvidoriaRobo
from app.services import chamados_leitura, ouvidoria
from app.services.advisory_lock import SYNC_NAMESPACE

logger = structlog.get_logger()

ROBO = "vigia_robo_leitura"

# Advisory lock do sweep (namespace SYNC compartilhado).
_SWEEP_LOCK_KEY = 0x76726C63  # ascii "vrlc"

# Padrões quando a config do robô não tem a chave (a linha de ouvidoria_robos
# nasce com estes mesmos valores — services/ouvidoria.ROBOS).
_SEM_SINAL_MIN = 30
_ATRASO_HORAS = 3

_TZ_BR = ZoneInfo("America/Sao_Paulo")

LINK_PAINEL = "/chamados"
QUEM = "Robô de leitura de chamados (Mac Santiago)"

ACAO_SEM_SINAL = (
    "Ver se o Mac Santiago está ligado, acordado e com internet — o robô de "
    "leitura liga sozinho quando o Mac liga"
)
ACAO_CASO = (
    "Ver o registro do robô no Mac Santiago (DaVinci › executor-leitura-chamado "
    "› logs): login do Seller Center caído, perfil da loja aberto por alguém, "
    "loja sem perfil no AdsPower ou página da Shopee mudada. Enquanto isso, "
    "conferir a devolução direto no Seller Center"
)

_CONTADORES = (
    "casos_na_fila", "casos_sem_leitura", "executor_ok", "executor_idade_min",
    "novas", "persistem", "sumiram",
)


def _br(dt: datetime | None) -> str:
    if dt is None:
        return "?"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(_TZ_BR).strftime("%d/%m %H:%M")


def _utc(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _minutos(inicio: datetime | None, agora: datetime) -> int:
    inicio = _utc(inicio)
    if inicio is None:
        return 0
    return max(0, int((agora - inicio).total_seconds() // 60))


def _idade(minutos: int) -> str:
    if minutos < 90:
        return f"{minutos} min"
    if minutos < 48 * 60:
        return f"{minutos // 60} h"
    return f"{minutos // 1440} dias"


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


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


# ─── regras ────────────────────────────────────────────────────────────────


async def _casos(
    session: AsyncSession, r: ouvidoria.Rodada, *, agora: datetime, atraso: timedelta
) -> int:
    """Regra 2. Devolve quantos casos a fila do robô tem hoje."""
    ultima_fala = chamados_leitura._ultima_fala_at()  # noqa: SLF001
    entrou = (
        select(func.max(func.coalesce(ChamadoMensagem.enviada_at, ChamadoMensagem.created_at)))
        .where(ChamadoMensagem.chamado_id == Chamado.id, ChamadoMensagem.tipo == "abertura")
        .correlate(Chamado)
        .scalar_subquery()
    )
    linhas = (
        await session.execute(
            select(Chamado, ultima_fala.label("ultima_fala"), entrou.label("entrou"))
            .where(*chamados_leitura.condicoes_do_leitor())
            .order_by(Chamado.created_at)
        )
    ).all()
    for ch, ult, ent in linhas:
        ult, ent = _utc(ult), _utc(ent)
        frio = ult is None or ult < agora - chamados_leitura.FRIO
        esperado = chamados_leitura.INTERVALO_FRIO if frio else chamados_leitura.INTERVALO
        lido = _utc(ch.leitura_robo_at)
        desde = lido or ent or _utc(ch.created_at)
        if desde is None or agora - desde <= esperado + atraso:
            continue
        r.contadores["casos_sem_leitura"] += 1
        minutos = _minutos(desde, agora)
        # 25/09 (292592): consulta do Portal de Atendimento também é da fila
        portal = chamados_leitura.e_portal_shopee(ch)
        o_que = "Consulta do Portal" if portal else "Devolução"
        if lido is None:
            titulo = f"{o_que} sem leitura nenhuma há {_idade(minutos)}"
            detalhe = f"Na fila do robô desde {_br(ent or ch.created_at)} e nunca foi lida"
        else:
            titulo = f"{o_que} sem leitura há {_idade(minutos)}"
            onde = "no Portal de Atendimento" if portal else "no Seller Center"
            detalhe = f"Última leitura {onde} em {_br(lido)}"
        numero = f"consulta {ch.chamado or '?'}" if portal else f"solicitação {ch.chamado or '?'}"
        detalhe += (
            f" — {numero}, pedido Shopee {ch.pedido_marketplace or '?'}. "
            "Se a Shopee respondeu nesse meio-tempo, o chamado não sabe"
        )
        await _registrar(
            r,
            agora,
            chave=f"caso:{ch.id}",
            plataforma="shopee",
            conta=ch.conta,
            pedido=ch.pedido_bling,
            titulo=titulo,
            detalhe=detalhe,
            acao=ACAO_CASO,
            link=f"{LINK_PAINEL}?search={ch.pedido_bling}" if ch.pedido_bling else LINK_PAINEL,
            severidade="pessoa",
            precisa_pessoa=True,
            dados={
                "chamado_id": str(ch.id),
                "solicitacao": ch.chamado,
                "ultima_leitura": lido.isoformat() if lido else None,
                "minutos": minutos,
            },
        )
    r.contadores["casos_na_fila"] = len(linhas)
    return len(linhas)


async def _executor(
    session: AsyncSession, r: ouvidoria.Rodada, *, agora: datetime, sem_sinal: timedelta,
    na_fila: int,
) -> str:
    """Regra 1. Devolve o pedaço do resumo (o estado do robô aparece sempre)."""
    leitor = (
        await session.execute(
            select(ChamadoLeitor)
            .where(ChamadoLeitor.revoked_at.is_(None))
            .order_by(ChamadoLeitor.last_used_at.desc().nulls_last())
            .limit(1)
        )
    ).scalar_one_or_none()
    visto = _utc(leitor.last_used_at) if leitor is not None else None
    if visto is None:
        if not na_fila:
            return "robô nunca deu sinal"
        await _registrar(
            r,
            agora,
            chave="executor:sem_sinal",
            plataforma="shopee",
            titulo=f"{QUEM} nunca deu sinal",
            detalhe=(
                "O robô nunca perguntou a fila e há "
                + _plural(na_fila, "devolução esperando leitura", "devoluções esperando leitura")
            ),
            acao=ACAO_SEM_SINAL,
            link=LINK_PAINEL,
            severidade="pessoa",
            precisa_pessoa=True,
            dados={"ultimo_sinal_em": None},
        )
        return "robô nunca deu sinal"

    minutos = _minutos(visto, agora)
    r.contadores["executor_idade_min"] = minutos
    if agora - visto > sem_sinal:
        detalhe = (
            f"Último sinal às {_br(visto)} (BRT); enquanto isso nenhuma resposta escrita "
            "da Shopee chega aos chamados"
        )
        if na_fila:
            detalhe += " — " + _plural(
                na_fila, "devolução na fila", "devoluções na fila"
            )
        await _registrar(
            r,
            agora,
            chave="executor:sem_sinal",
            plataforma="shopee",
            titulo=f"{QUEM} sem sinal há {_idade(minutos)}",
            detalhe=detalhe,
            acao=ACAO_SEM_SINAL,
            link=LINK_PAINEL,
            severidade="pessoa",
            precisa_pessoa=True,
            dados={
                "leitor": leitor.nome,
                "ultimo_sinal_em": visto.isoformat(),
                "idade_min": minutos,
            },
        )
        return f"robô sem sinal há {_idade(minutos)}"
    r.contadores["executor_ok"] = 1
    return "robô ok"


# ─── rodada, sweep ─────────────────────────────────────────────────────────


async def vigia_robo_leitura_run(session: AsyncSession) -> dict:
    """Uma varredura dentro de uma `Rodada`. Erro derruba a rodada inteira
    (ok=False) sem fechar nada: "não consegui olhar" nunca vira "sumiu"."""
    async with ouvidoria.Rodada(session, ROBO) as r:
        for k in _CONTADORES:
            r.contadores[k] = 0
        robo = await session.get(OuvidoriaRobo, ROBO)
        cfg = ouvidoria.config_do_robo(robo, ROBO)
        sem_sinal = timedelta(minutes=int(cfg.get("sem_sinal_min") or _SEM_SINAL_MIN))
        atraso = timedelta(hours=int(cfg.get("atraso_horas") or _ATRASO_HORAS))
        agora = datetime.now(UTC)

        na_fila = await _casos(session, r, agora=agora, atraso=atraso)
        executor_txt = await _executor(
            session, r, agora=agora, sem_sinal=sem_sinal, na_fila=na_fila
        )

        # Todas as ocorrências deste robô são de ESTADO: o que a rodada não
        # re-viu, sumiu.
        r.contadores["sumiram"] = await r.fechar_nao_vistas()

        partes = [_plural(na_fila, "devolução na fila", "devoluções na fila")]
        if r.contadores["casos_sem_leitura"]:
            partes.append(f"{r.contadores['casos_sem_leitura']} sem leitura")
        partes.append(executor_txt)
        r.resumo = " · ".join(partes)

    aviso = await ouvidoria.avisar_pendentes(session, ROBO)
    return {**dict(r.contadores), "avisadas": aviso.get("avisadas", 0), "resumo": r.resumo}


async def vigia_robo_leitura_sweep() -> dict:
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
            return await vigia_robo_leitura_run(session)
