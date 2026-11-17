"""Postgres advisory locks scoped to a SyncOrchestrator run.

Replaces the legacy `syncLock.ts` mechanism (PRD §11 Fase 4a).

Usage:

    async with try_user_sync_lock(session, user_id) as acquired:
        if not acquired:
            raise HTTPException(409, "sync_already_running")
        ...

Locks are session-scoped: released when the underlying connection is checked
back into the pool. Use `session_scope()` from `app.db` to control lifetime.
"""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

# Namespace constant: first int passed to `pg_try_advisory_lock(int4, int4)`.
# Picked arbitrarily; uniqueness inside this app is enough to avoid collisions
# with other advisory locks the DB might use.
SYNC_NAMESPACE: int = 0x53594E43  # ascii "SYNC"


def _user_lock_key(user_id: UUID) -> int:
    """Project a UUID into a signed int4 deterministically."""
    h = hashlib.blake2b(user_id.bytes, digest_size=4).digest()
    val = int.from_bytes(h, "big", signed=False)
    # Map to int4 signed range Postgres expects.
    if val >= 2**31:
        val -= 2**32
    return val


@asynccontextmanager
async def try_user_sync_lock(session: AsyncSession, user_id: UUID):
    """Try to acquire a non-blocking *transactional* advisory lock for the
    user's sync run.

    Yields True if acquired (caller proceeds), False if another process
    holds it (caller should 409).

    Uses `pg_try_advisory_xact_lock` rather than the session-level variant
    so the lock is released automatically when the surrounding transaction
    commits OR rolls back. The session-level lock has a subtle leak: if the
    transaction aborts before `pg_advisory_unlock` runs, the unlock call
    itself fails (`current transaction is aborted`) and the lock stays
    pinned to the pooled connection — every subsequent sync sees
    `sync_already_running` until the connection is recycled.
    """
    key = _user_lock_key(user_id)
    row = await session.execute(
        text("SELECT pg_try_advisory_xact_lock(:ns, :k)"),
        {"ns": SYNC_NAMESPACE, "k": key},
    )
    acquired: bool = bool(row.scalar())
    yield acquired


# Janela p/ considerar um massa "ativo". O sync_all completo roda até ~3h
# (WorkerSettings sync_all timeout=10800); esta janela é o teto de qualquer
# massa, mesmo com sinal de vida.
_MASS_SYNC_ACTIVE_WINDOW = timedelta(hours=4)
# RUNNING sem sinal de vida há mais que isto = worker morreu (reinício/deploy no
# meio do massa). O background_jobs_gc marca esses como FAILED, mas só roda 1x
# por dia (03:30): até lá o botão "Sincronizar Todos" do usuário ficava cinza
# por até 4h (25/09/2026: marrocos e harry potter travados desde 12:09). Num
# massa normal o maior intervalo entre sinais foi ~2 min (sync_all de 24-25/09);
# 15 min dá folga para um link lento sem liberar um segundo massa por engano.
# Vale para os 3 tipos de massa desde 17/11/2026: o AUTO_LINK (botão Vincular e
# varredura das 10h) e o ATUALIZAR (IMPORT_LISTINGS) dão sinal a cada 60 s pelo
# `job_details.sinal_de_vida`. Antes o AUTO_LINK ficava com a janela de 4h, e
# com a trava global um job morto prenderia os botões de TODO mundo por 4h.
_MASS_SYNC_HEARTBEAT_TIMEOUT = timedelta(minutes=15)
# PENDING só trava enquanto é recente. A fila davinci_sync pega o job em
# segundos (max_jobs=8); PENDING velho é job que o worker nunca vai pegar (ex.:
# API publicada antes do worker conhecer a função → o arq descarta e a linha
# fica PENDING para sempre).
_MASS_SYNC_PENDING_TIMEOUT = timedelta(minutes=20)

# Trava curta do DISPARO de massa (POST dos botões): dois cliques ao mesmo
# tempo (mesma pessoa ou não) não passam os dois pela conferência da linha
# PENDING/RUNNING. Transacional e NÃO bloqueante: ocupado → 409 na hora, sem
# segurar conexão da API esperando o enqueue do outro clique no Redis.
_MASS_DISPARO_LOCK_KEY = 0x6D617373  # ascii "mass"


def _rotulo_massa(tipo: str, gatilho: str | None) -> str:
    """O nome da massa para a pessoa ('Já tem <rótulo> rodando')."""
    if gatilho == "varredura_diaria":
        return "a varredura automática de vínculos"
    if gatilho == "daily_sync":
        return "a sincronização automática da noite"
    if gatilho == "botao_atualizar":
        return "o Atualizar"
    if tipo == "auto_link":
        return "o Vincular"
    if tipo == "sync_all":
        return "o Sincronizar"
    return "a leitura de anúncios"


async def massa_em_andamento(
    session: AsyncSession, *, user_id: UUID | None = None
) -> dict | None:
    """A massa em andamento de QUALQUER usuário (ou só de `user_id`), ou None.

    "Massa" = Sincronizar (SYNC_ALL), Vincular (AUTO_LINK) e Atualizar /
    leitura de anúncios (IMPORT_LISTINGS), inclusive a noturna (03:00) e a
    varredura das 10h. Gate determinístico de exclusividade: os endpoints de
    enqueue recusam (409) se já existe uma; o sync individual por SKU NÃO usa
    este gate (continua livre). Preferido ao advisory lock porque o lock é
    transacional e só é adquirido DENTRO do worker (há uma janela entre o
    enqueue e o pickup em que ninguém o segura) — uma linha PENDING/RUNNING é
    visível imediatamente após o enqueue.

    Global desde 17/11/2026: antes era por usuário, e duas pessoas (ou a
    varredura, que roda em nome do dono) rodavam duas massas juntas.

    Devolve {job_id, type, tipo, status, gatilho, quem, desde, rotulo}.
    """
    # Import local: app.models importa cedo demais p/ topo deste módulo.
    from app.models import BackgroundJob, BackgroundJobStatus, BackgroundJobType, User

    now = datetime.now(UTC)
    cutoff = now - _MASS_SYNC_ACTIVE_WINDOW
    vivo_desde = now - _MASS_SYNC_HEARTBEAT_TIMEOUT
    pendente_desde = now - _MASS_SYNC_PENDING_TIMEOUT
    filtros = [
        BackgroundJob.type.in_(
            [
                BackgroundJobType.SYNC_ALL,
                BackgroundJobType.AUTO_LINK,
                BackgroundJobType.IMPORT_LISTINGS,
            ]
        ),
        BackgroundJob.status.in_(
            [BackgroundJobStatus.PENDING, BackgroundJobStatus.RUNNING]
        ),
        BackgroundJob.finished_at.is_(None),
        BackgroundJob.created_at >= cutoff,
        or_(
            and_(
                BackgroundJob.status == BackgroundJobStatus.PENDING,
                BackgroundJob.created_at >= pendente_desde,
            ),
            and_(
                BackgroundJob.status == BackgroundJobStatus.RUNNING,
                func.coalesce(
                    BackgroundJob.last_heartbeat_at,
                    BackgroundJob.started_at,
                    BackgroundJob.created_at,
                )
                >= vivo_desde,
            ),
        ),
    ]
    if user_id is not None:
        filtros.append(BackgroundJob.created_by == user_id)
    row = (
        await session.execute(
            select(BackgroundJob, User.name, User.email)
            .outerjoin(User, User.id == BackgroundJob.created_by)
            .where(and_(*filtros))
            .order_by(BackgroundJob.created_at.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    job, nome, email = row
    tipo = job.type.value
    gatilho = (job.payload or {}).get("trigger") or None
    desde = job.started_at or job.created_at
    return {
        "job_id": str(job.id),
        "type": tipo,
        "tipo": tipo,
        "status": job.status.value,
        "gatilho": gatilho,
        "quem": nome or (email.split("@")[0] if email else None),
        "desde": desde.isoformat() if desde else None,
        "rotulo": _rotulo_massa(tipo, gatilho),
    }


async def mass_sync_active(session: AsyncSession, user_id: UUID) -> dict | None:
    """A massa em andamento deste usuário, ou None (nome antigo, mantido para
    quem ainda chama). Os botões usam `massa_em_andamento`, que é global."""
    return await massa_em_andamento(session, user_id=user_id)


async def travar_disparo_de_massa(session: AsyncSession) -> bool:
    """Trava transacional do disparo de massa (POST de Atualizar, Vincular e
    Sincronizar). Segura até o commit/rollback da requisição, então o segundo
    clique simultâneo só passa depois que o primeiro gravou a linha PENDING (e
    aí `massa_em_andamento` já o vê).

    NÃO bloqueante (pg_try_advisory_xact_lock): ocupado → 409 na hora, em vez
    de prender a conexão da API esperando o outro clique enfileirar no Redis.
    Devolve True quando pegou."""
    from fastapi import HTTPException, status

    got = (
        await session.execute(
            text("SELECT pg_try_advisory_xact_lock(:ns, :k)"),
            {"ns": SYNC_NAMESPACE, "k": _MASS_DISPARO_LOCK_KEY},
        )
    ).scalar()
    if not got:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "sync_already_running",
                "job_id": None,
                "type": None,
                "tipo": None,
                "status": None,
                "gatilho": None,
                "quem": None,
                "desde": None,
                "rotulo": "outro disparo em andamento",
            },
        )
    return True


async def massa_ocupada_para_robo(session: AsyncSession) -> dict | None:
    """Para os robôs que disparam massa SOZINHOS (varredura de vínculos das
    10h e sincronização da noite, 17/11/2026): a mesma regra dos botões, sem
    409. Pega a trava curta 'mass' (sem esperar) e confere a massa global.
    Devolve o que está rodando — ou {'rotulo': 'outro disparo em andamento'}
    quando a trava curta está ocupada — ou None quando está livre: aí quem
    chamou cria a linha do job NA MESMA transação (a trava vale até o commit).

    Antes só havia o try_user_sync_lock dentro do job, que é transacional e
    soltava no primeiro commit: a varredura rodava junto com um Atualizar,
    Vincular ou Sincronizar."""
    got = (
        await session.execute(
            text("SELECT pg_try_advisory_xact_lock(:ns, :k)"),
            {"ns": SYNC_NAMESPACE, "k": _MASS_DISPARO_LOCK_KEY},
        )
    ).scalar()
    if not got:
        return {"rotulo": "outro disparo em andamento"}
    return await massa_em_andamento(session)


async def recusar_se_massa_em_andamento(session: AsyncSession) -> None:
    """Trava do disparo + conferência global: 409 'sync_already_running' com
    o que está rodando, quem disparou e desde quando. Chamar no começo do POST,
    antes de criar a linha do job."""
    from fastapi import HTTPException, status

    await travar_disparo_de_massa(session)
    ativo = await massa_em_andamento(session)
    if ativo is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "sync_already_running", **ativo},
        )


async def release_stale_sync_locks(
    session: AsyncSession, *, idle_minutes: int = 30
) -> list[int]:
    """Terminate backends that hold our SYNC_NAMESPACE advisory lock and have
    been idle for at least `idle_minutes`. Returns the PIDs that were killed.

    Counterpart to SSH's 30-min in-memory safety timeout. A killed task or a
    pool connection that didn't run pg_advisory_unlock leaves the lock held;
    without this cleanup the next sync_all raises sync_already_running until
    the connection is recycled (which can be hours).
    """
    rows = (
        await session.execute(
            text(
                """
                SELECT l.pid
                FROM pg_locks l
                JOIN pg_stat_activity a ON a.pid = l.pid
                WHERE l.locktype = 'advisory'
                  AND l.classid = :ns
                  AND a.state = 'idle'
                  AND (now() - a.query_start) > make_interval(mins => :mins)
                """
            ),
            {"ns": SYNC_NAMESPACE, "mins": idle_minutes},
        )
    ).all()
    killed: list[int] = []
    for row in rows:
        pid = row.pid
        try:
            r = await session.execute(
                text("SELECT pg_terminate_backend(:pid)"), {"pid": pid}
            )
            if r.scalar():
                killed.append(pid)
        except Exception:  # noqa: BLE001
            continue
    return killed
