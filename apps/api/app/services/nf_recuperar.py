"""Recuperador de NF (varredura anti-esquecimento + retry com teto).

Complementa o sweep (`nf_auto_enfileirar`): aquele só ENTRA pedido novo; este
garante que nada fica pra trás depois que entrou. Três frentes por tick:

  1. RETRY — comando 'failed' recente (24h) com menos de 3 tentativas volta
     pra 'pending' e a etapa do painel re-marca 'processando'. Na 3ª falha o
     comando fica 'failed' de vez (o painel mostra 'erro' — re-enfileirar é
     humano).
  2. DESTRAVA — comando 'claimed' há mais de 45min sem resultado (executor
     caiu no meio) volta pra 'pending' se ainda tem tentativa; senão fecha
     'failed' definitivo e a etapa vira 'erro'.
  3. ÓRFÃOS — pedido 'processando' no painel SEM nenhum comando ativo da sua
     etapa (o encadeamento se perdeu: crash entre o done e o próximo elo).
     Re-encadeia a partir do último comando 'done' que cobre o pedido; sem
     rastro, re-gera do zero (mesmo caminho do sweep).

O teto de tentativas usa `NfCommand.attempts`, que o /agent/lease incrementa
a cada reivindicação — 3 leases = 3 execuções, sem migration.

Serializado por advisory lock transacional próprio (chave distinta do sweep;
os dois ticks podem coexistir). Gated pela MESMA flag `nf_auto_enfileirar`
(checada no wrapper do worker) — default DESLIGADO.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select, text

from app.db import session_scope
from app.models import NfCommand, NfFaturamento
from app.services import nf_emissao_gerar
from app.services.advisory_lock import SYNC_NAMESPACE

logger = structlog.get_logger()

# Chave do advisory lock que serializa o recuperador (≠ da chave do sweep).
_LOCK_KEY = 0x6E667263  # ascii "nfrc"

# 3 leases = 3 execuções (o /agent/lease incrementa attempts ao reivindicar).
_MAX_TENTATIVAS = 3

# Só re-tenta falha RECENTE — não ressuscita comando failed antigo quando o
# recuperador entra no ar pela primeira vez.
_RETRY_JANELA = timedelta(hours=24)

# Comando claimed sem resultado além disso = executor caiu no meio (o passe de
# etiqueta pode segurar o lease por bastante tempo; 45min dá folga).
_CLAIM_TTL = timedelta(minutes=45)

# Janela dos órfãos: 'processando' mais velho que isso é caso de painel/humano.
_ORFAO_JANELA = timedelta(days=7)

# Teto de re-enfileiramentos de órfãos por tick (mesmo espírito do sweep).
_MAX_POR_TICK = 30

# Qual etapa do painel cada action alimenta (capturar_nf fica fora — não tem
# coluna própria no nf_faturamento).
_ETAPA_FATURAMENTO = ("import_avulsa", "emitir_nf_upseller", "emitir_nf_bling")
_ETAPA_ETIQUETA = ("import_etiqueta", "imprimir_etiqueta")


async def run_recuperar_nf() -> dict:
    """Uma passada do recuperador. Devolve um resumo com contadores."""
    summary: dict = {
        "retry": 0,
        "esgotados": 0,
        "destravados": 0,
        "orfaos_faturamento": 0,
        "orfaos_etiqueta": 0,
    }
    async with session_scope() as session:
        got = (
            await session.execute(
                text("SELECT pg_try_advisory_xact_lock(:ns, :key)"),
                {"ns": SYNC_NAMESPACE, "key": _LOCK_KEY},
            )
        ).scalar()
        if not got:
            summary["skipped"] = "lock_busy"
            return summary

        await _retry_failed(session, summary)
        await _destravar_claimed(session, summary)
        await _reenfileirar_orfaos(session, summary)
    # session_scope commita na saída — tudo numa transação só.
    return summary


async def _marcar_etapa(
    session, action: str, numeros: list[str], *, status_txt: str, erro: str | None
) -> None:
    """Reflete no painel (nf_faturamento) a etapa que a action alimenta."""
    from app.routers.nf import _marcar_etiqueta, _marcar_faturamento

    if action in _ETAPA_FATURAMENTO:
        await _marcar_faturamento(session, numeros, status_txt=status_txt, erro=erro)
    elif action in _ETAPA_ETIQUETA:
        await _marcar_etiqueta(session, numeros, status_txt=status_txt, erro=erro)


async def _retry_failed(session, summary: dict) -> None:
    """Comando failed recente com tentativa sobrando volta pra fila."""
    from app.routers.nf import _em_fila

    cutoff = datetime.now(UTC) - _RETRY_JANELA
    cmds = (
        await session.execute(
            select(NfCommand).where(
                NfCommand.status == "failed",
                NfCommand.attempts < _MAX_TENTATIVAS,
                NfCommand.completed_at.is_not(None),
                NfCommand.completed_at >= cutoff,
            )
        )
    ).scalars().all()
    fila_por_action: dict[str, set[str]] = {}
    for cmd in cmds:
        if cmd.action not in fila_por_action:
            fila_por_action[cmd.action] = await _em_fila(session, cmd.action)
        # Já há outro comando ativo cobrindo esses pedidos (ex. re-enfileirar
        # humano) → não duplica o trabalho.
        if cmd.numeros and any(n in fila_por_action[cmd.action] for n in cmd.numeros):
            continue
        cmd.status = "pending"
        cmd.claimed_at = None
        cmd.completed_at = None
        fila_por_action[cmd.action].update(cmd.numeros or [])
        await _marcar_etapa(
            session, cmd.action, cmd.numeros or [], status_txt="processando", erro=None
        )
        summary["retry"] += 1
        logger.info(
            "nf_recuperar_retry",
            command_id=str(cmd.id),
            action=cmd.action,
            attempts=cmd.attempts,
            numeros=cmd.numeros,
        )


async def _destravar_claimed(session, summary: dict) -> None:
    """Lease expirado: executor sumiu no meio — devolve pra fila ou esgota."""
    cutoff = datetime.now(UTC) - _CLAIM_TTL
    cmds = (
        await session.execute(
            select(NfCommand).where(
                NfCommand.status == "claimed",
                NfCommand.claimed_at.is_not(None),
                NfCommand.claimed_at < cutoff,
            )
        )
    ).scalars().all()
    for cmd in cmds:
        if cmd.attempts < _MAX_TENTATIVAS:
            cmd.status = "pending"
            cmd.claimed_at = None
            summary["destravados"] += 1
            logger.info(
                "nf_recuperar_destravado",
                command_id=str(cmd.id),
                action=cmd.action,
                attempts=cmd.attempts,
                numeros=cmd.numeros,
            )
        else:
            cmd.status = "failed"
            cmd.result = "executor não reportou resultado (lease expirado)"
            cmd.completed_at = datetime.now(UTC)
            await _marcar_etapa(
                session,
                cmd.action,
                cmd.numeros or [],
                status_txt="erro",
                erro=cmd.result,
            )
            summary["esgotados"] += 1
            logger.warning(
                "nf_recuperar_esgotado",
                command_id=str(cmd.id),
                action=cmd.action,
                numeros=cmd.numeros,
            )


async def _dones_recentes(session, actions: tuple[str, ...]) -> dict[str, NfCommand]:
    """numero → último comando 'done' (na janela) de cada action, pra retomar o
    encadeamento de onde parou. numeros é JSONB → mapeia em Python."""
    cutoff = datetime.now(UTC) - _ORFAO_JANELA
    cmds = (
        await session.execute(
            select(NfCommand)
            .where(
                NfCommand.action.in_(actions),
                NfCommand.status == "done",
                NfCommand.completed_at.is_not(None),
                NfCommand.completed_at >= cutoff,
            )
            .order_by(NfCommand.completed_at)
        )
    ).scalars().all()
    mapa: dict[str, NfCommand] = {}
    for cmd in cmds:  # ordem asc → o mais recente vence
        for n in cmd.numeros or []:
            mapa[n] = cmd
    return mapa


async def _orfaos_de(session, status_col, actions: tuple[str, ...]) -> list[str]:
    """Pedidos 'processando' na etapa SEM nenhum comando ativo que os cubra."""
    from app.routers.nf import _em_fila

    cutoff = datetime.now(UTC) - _ORFAO_JANELA
    numeros = (
        await session.execute(
            select(NfFaturamento.pedido_bling).where(
                status_col == "processando",
                NfFaturamento.updated_at >= cutoff,
            )
        )
    ).scalars().all()
    if not numeros:
        return []
    ativos: set[str] = set()
    for action in actions:
        ativos |= await _em_fila(session, action)
    return [n for n in numeros if n not in ativos][:_MAX_POR_TICK]


async def _reenfileirar_orfaos(session, summary: dict) -> None:
    """Re-encadeia pedidos 'processando' cuja cadeia de comandos se perdeu."""
    from app.routers.nf import (
        _criar_comandos_etiqueta,
        _enfileirar_emissao_bling,
        _enfileirar_emissao_upseller,
        _enfileirar_etiqueta_ml,
        _marcar_faturamento,
    )

    # --- Etapa FATURAMENTO -------------------------------------------------
    orfaos = await _orfaos_de(
        session, NfFaturamento.status_faturamento, _ETAPA_FATURAMENTO
    )
    if orfaos:
        import_done = await _dones_recentes(session, ("import_avulsa",))
        regerar: list[str] = []
        for n in orfaos:
            done = import_done.get(n)
            if done is not None:
                # Import já fechou — o elo perdido é a emissão da NF
                # (Upseller ou Bling destino).
                emitindo = await _enfileirar_emissao_upseller(
                    session, done.faturador_id, [n]
                ) or await _enfileirar_emissao_bling(
                    session, done.faturador_id, [n]
                )
                if not emitindo:
                    # Faturador sumiu: nada mais a encadear — espelha o
                    # endpoint /result e fecha o faturamento.
                    await _marcar_faturamento(
                        session, [n], status_txt="ok", erro=None
                    )
                summary["orfaos_faturamento"] += 1
            else:
                regerar.append(n)
        if regerar:
            res = await nf_emissao_gerar.gerar_por_faturador(session, regerar)
            for p in res.pulados:
                await _marcar_faturamento(
                    session, [p.numero], status_txt="erro", erro=p.motivo
                )
                logger.warning(
                    "nf_recuperar_orfao_pulado", numero=p.numero, motivo=p.motivo
                )
            for bloco in res.blocos:
                session.add(
                    NfCommand(
                        faturador_id=bloco.faturador_id,
                        action="import_avulsa",
                        numeros=bloco.numeros,
                        planilha=bloco.planilha,
                        nome_arquivo=bloco.nome_arquivo,
                        status="pending",
                        source="auto",
                    )
                )
                await _marcar_faturamento(
                    session, bloco.numeros, status_txt="processando", erro=None
                )
                summary["orfaos_faturamento"] += len(bloco.numeros)
        if summary["orfaos_faturamento"]:
            logger.info(
                "nf_recuperar_orfaos_faturamento",
                pedidos=orfaos,
                regerados=regerar,
            )

    # --- Etapa ETIQUETA ----------------------------------------------------
    orfaos = await _orfaos_de(session, NfFaturamento.status_etiqueta, _ETAPA_ETIQUETA)
    if orfaos:
        dones = await _dones_recentes(
            session, ("import_etiqueta", "emitir_nf_upseller")
        )
        sem_rastro: list[str] = []
        for n in orfaos:
            done = dones.get(n)
            if done is not None:
                # A importação/emissão fechou — falta só a captura da etiqueta,
                # no mesmo perfil AdsPower que o comando done usou.
                await _criar_comandos_etiqueta(
                    session,
                    [n],
                    faturador_id=done.faturador_id,
                    ads_power=done.ads_power,
                )
                summary["orfaos_etiqueta"] += 1
            else:
                sem_rastro.append(n)
        if sem_rastro:
            # Sem rastro do elo anterior: refaz a importação da etiqueta (fluxo
            # ML). Pedido que o gerador pular fica 'processando' e volta no
            # próximo tick — logado pra não virar churn invisível.
            blocos = await _enfileirar_etiqueta_ml(session, sem_rastro)
            summary["orfaos_etiqueta"] += len(sem_rastro)
            logger.info(
                "nf_recuperar_orfaos_etiqueta_sem_rastro",
                pedidos=sem_rastro,
                blocos=blocos,
            )
        logger.info("nf_recuperar_orfaos_etiqueta", pedidos=orfaos)
