"""A coleta do SERVIDOR da Conferência — Mercado Livre e Amazon (07/10/2026).

A Shopee passa loja por loja no executor do Mac (AdsPower). O ML e a Amazon
passam AQUI, no worker, sem AdsPower: os números vêm dos pedidos do Bling no
DaVinci e da API de Anúncios (coletores/). O ciclo de uma rodada:

  1. `fila.criar_execucao(plataforma=ml|amazon)` — agenda (CONFERENCIA_ML_CRON
     / CONFERENCIA_AMAZON_CRON) ou "Gerar agora"; depois do commit quem criou
     chama `enfileirar` (job `conferencia_coletar_servidor`).
  2. `coletar_execucao` (o job) — pega as coletas da rodada UMA a UMA
     (`fila.lease_servidor`: mesmas regras do lease do Mac — 3 tentativas,
     coleta largada há 20 min volta, nada depois do corte), chama o coletor da
     plataforma numa sessão só daquela loja, confere a forma dos números
     (dados.confere_coleta_dados) e grava pelo mesmo `fila.registrar_resultado`
     do executor. Cada loja tem o seu commit: uma loja que trava ou explode não
     leva as outras.
  3. Sem coleta na fila: fecha o relatório e avisa no Threema
     (`fechar_e_avisar`, o mesmo da rota do executor).
  4. O varredor (worker, 10 em 10 min) fecha no prazo como na Shopee e
     re-enfileira a rodada que ficou parada (Redis fora na criação, worker
     reiniciado no meio): `enfileirar_paradas`.

Status da coleta: `ok`; `parcial` quando faltou, em alguma semana, uma seção
que a plataforma tem de trazer (plataformas.SECOES_ESPERADAS — ex.: o Ads do
ML sem permissão); `erro` quando o coletor levantou, passou do tempo ou
devolveu números num formato que o relatório não lê.

Nada de commit fora daqui: cada passo abre a sua sessão (session_scope).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app import worker_pool
from app.db import session_scope
from app.models import ConferenciaShopeeConta, ConferenciaShopeeExecucao
from app.services.conferencia_shopee import coletores, dados, fila, plataformas, threema_aviso

logger = structlog.get_logger()

JOB = "conferencia_coletar_servidor"
# Uma loja não pode segurar a rodada: passou disto, vira `erro` e segue.
LIMITE_POR_CONTA_S = 600.0
# Dedupe do enfileiramento: o mesmo job da mesma rodada no mesmo trecho de
# 10 min entra uma vez só (clique duplo, varredor + criação).
_BALDE_S = 600

ERRO_SEM_CONTA = "a conta saiu da lista da conferência"
ERRO_TEMPO = "a coleta desta loja passou do tempo"
ERRO_SEM_NUMEROS = "o coletor não devolveu os números"


def _agora() -> datetime:
    return datetime.now(UTC)


def _iso_utc(quando: datetime) -> str:
    return quando.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


# ───────────────────────────────────────────────────────────── enfileirar


async def enfileirar(execucao_id: UUID, agora: datetime | None = None) -> bool:
    """Põe o job da rodada na fila do worker. False (e log) se o Redis falhou —
    a rodada fica `coletando` e o varredor tenta de novo em até 10 min."""
    agora = agora or _agora()
    try:
        pool = await worker_pool.get_arq_pool()
        await pool.enqueue_job(
            JOB,
            str(execucao_id),
            _job_id=f"{JOB}:{execucao_id}:{int(agora.timestamp()) // _BALDE_S}",
        )
    except Exception as e:  # noqa: BLE001 — o varredor re-enfileira
        logger.warning(
            "conferencia_servidor_enfileirar_falhou",
            execucao=str(execucao_id),
            erro=f"{type(e).__name__}: {e}"[:200],
        )
        return False
    logger.info("conferencia_servidor_enfileirada", execucao=str(execucao_id))
    return True


async def enfileirar_paradas(agora: datetime | None = None) -> list[UUID]:
    """O varredor: re-enfileira as rodadas do ML/Amazon paradas (com loja
    para pegar e ninguém coletando — fila.execucoes_servidor_paradas)."""
    agora = agora or _agora()
    async with session_scope() as s:
        ids = await fila.execucoes_servidor_paradas(s, agora)
    for execucao_id in ids:
        await enfileirar(execucao_id, agora)
    return ids


# ───────────────────────────────────────────────────────────── fechar


async def fechar_e_avisar(session: AsyncSession, execucao_id: UUID, agora: datetime) -> bool:
    """Fecha a execução se não sobrou loja na fila e manda o aviso. Chamar
    DEPOIS do commit do resultado: se o cálculo falhar, o resultado da loja já
    está salvo e o varredor do worker tenta fechar de novo. O Threema nunca
    derruba quem chamou. (Também é o `_fechar_e_avisar` da rota do executor.)"""
    try:
        execucao = await fila.fechar_se_terminou(session, execucao_id, agora)
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("conferencia_shopee_fechar_falhou", execucao=str(execucao_id))
        return False
    if execucao is None:
        return False
    try:
        await threema_aviso.enviar_pendente(session, execucao, agora)
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("conferencia_shopee_threema_falhou", execucao=str(execucao_id))
    return True


# ───────────────────────────────────────────────────────────── coletar


def _faltou_secao(
    numeros: Mapping[str, Any], semanas: Sequence[coletores.Semana], plataforma: str
) -> bool:
    """Alguma semana da rodada que não veio, ou veio sem uma seção que a
    plataforma tem de trazer."""
    esperadas = plataformas.SECOES_ESPERADAS.get(plataforma, ())
    vindas = {
        (s.get("inicio"), s.get("fim")): s
        for s in numeros.get("semanas") or []
        if isinstance(s, Mapping)
    }
    for semana in semanas:
        s = vindas.get((semana.inicio.isoformat(), semana.fim.isoformat()))
        if s is None or any(not isinstance(s.get(secao), Mapping) for secao in esperadas):
            return True
    return False


async def _coletar_conta(
    conta: coletores.ContaColeta,
    semanas: Sequence[coletores.Semana],
    relogio: Callable[[], datetime],
    limite_s: float,
) -> tuple[str, str | None, dict[str, Any] | None]:
    """(status, erro, dados) de UMA loja. Nunca levanta."""
    try:
        coletor = coletores.obter(conta.plataforma)
        async with session_scope() as s:
            numeros = await asyncio.wait_for(coletor(conta, semanas, session=s), timeout=limite_s)
    except coletores.ColetorError as e:
        return "erro", e.mensagem, None
    except TimeoutError:
        return "erro", f"{ERRO_TEMPO} ({int(limite_s // 60)} min)", None
    except Exception as e:  # coletor com defeito: a loja vira erro, a rodada segue
        logger.exception(
            "conferencia_servidor_coletor_falhou", coleta=str(conta.coleta_id), conta=conta.nome
        )
        return "erro", f"falha na coleta ({type(e).__name__})", None
    if not isinstance(numeros, Mapping):
        return "erro", ERRO_SEM_NUMEROS, None
    numeros = dict(numeros)
    numeros.setdefault("versao", 1)
    numeros.setdefault("coletado_em", _iso_utc(relogio()))
    try:
        dados.confere_coleta_dados(numeros)
    except (ValidationError, ValueError, RecursionError) as e:
        logger.warning(
            "conferencia_servidor_formato_invalido",
            coleta=str(conta.coleta_id),
            conta=conta.nome,
            erro=str(e)[:300],
        )
        return "erro", fila.ERRO_DADOS_INVALIDOS, None
    status = "parcial" if _faltou_secao(numeros, semanas, conta.plataforma) else "ok"
    return status, None, numeros


async def _reservar(
    execucao_id: UUID, plataforma: str, agora: datetime
) -> tuple[coletores.ContaColeta | None, bool, str | None]:
    """(conta da próxima coleta | None, tinha coleta?, erro se a coleta não tem
    como andar). Commit da reserva antes de coletar (o lease marca
    `coletando` e gasta a tentativa)."""
    async with session_scope() as s:
        coleta, _esgotadas = await fila.lease_servidor(s, execucao_id, agora)
        if coleta is None:
            return None, False, None
        conta = (
            await s.get(ConferenciaShopeeConta, coleta.conta_id)
            if coleta.conta_id is not None
            else None
        )
        info = coletores.ContaColeta(
            coleta_id=coleta.id,
            execucao_id=coleta.execucao_id,
            conta_id=coleta.conta_id,
            plataforma=plataforma,
            nome=coleta.nome,
            grupo=coleta.grupo,
            integration_id=conta.integration_id if conta else None,
            bling_loja_id=conta.bling_loja_id if conta else None,
            conta_key=conta.conta_key if conta else None,
        )
        return info, True, None if conta is not None else ERRO_SEM_CONTA


async def coletar_execucao(
    execucao_id: UUID,
    *,
    relogio: Callable[[], datetime] = _agora,
    limite_por_conta_s: float = LIMITE_POR_CONTA_S,
) -> dict[str, Any]:
    """O job: coleta todas as lojas que sobraram na fila da rodada do ML/Amazon
    e fecha o relatório. Rodar duas vezes ao mesmo tempo não duplica nada (cada
    loja é reservada com SKIP LOCKED)."""
    resumo: dict[str, Any] = {"execucao": str(execucao_id), "contas": 0, "fechou": False}
    async with session_scope() as s:
        execucao = await s.get(ConferenciaShopeeExecucao, execucao_id)
        if (
            execucao is None
            or execucao.status != "coletando"
            or not plataformas.do_servidor(execucao.plataforma)
        ):
            resumo["motivo"] = "nada a coletar"
            return resumo
        plataforma = execucao.plataforma
        semanas = coletores.semanas_do_job(execucao.semanas)

    while True:
        conta, tinha, erro_previo = await _reservar(execucao_id, plataforma, relogio())
        if not tinha or conta is None:
            break
        status: str
        erro: str | None
        numeros: dict[str, Any] | None
        if erro_previo is not None:
            status, erro, numeros = "erro", erro_previo, None
        else:
            status, erro, numeros = await _coletar_conta(
                conta, semanas, relogio, limite_por_conta_s
            )
        try:
            async with session_scope() as s:
                await fila.registrar_resultado(s, conta.coleta_id, status, erro, numeros, relogio())
        except fila.FilaError as e:
            # Cancelada ou expirada pelo varredor enquanto coletava: segue.
            logger.info(
                "conferencia_servidor_resultado_descartado",
                coleta=str(conta.coleta_id),
                motivo=e.code,
            )
            continue
        resumo["contas"] += 1
        logger.info(
            "conferencia_servidor_conta",
            execucao=str(execucao_id),
            plataforma=plataforma,
            conta=conta.nome,
            status=status,
        )

    async with session_scope() as s:
        resumo["fechou"] = await fechar_e_avisar(s, execucao_id, relogio())
    logger.info("conferencia_servidor_fim", **resumo)
    return resumo
