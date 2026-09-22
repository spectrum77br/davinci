"""Pedido do Bling que não entra — robô da Ouvidoria (22/09/2026).

O caminho normal de um pedido é: o Bling manda o webhook → `webhooks.py` grava
um `BackgroundJob(type=ingest_bling_order)` e enfileira o arq → o arq tenta 3
vezes → falhou em definitivo, o job fica FAILED e o `ingest_orders_retry_sweep`
(a cada 5 min) re-enfileira até 8 rodadas. Quando ISSO acaba e o pedido
continua fora de `bling_orders`, ninguém mais tenta e ninguém é avisado: o
sweep só escreve um `logger.warning`. O pedido não existe na Margem, na NF nem
na Logística — e a operação só descobre quando o cliente cobra (incidente de
28-29/06).

Este robô é o olho nesse fim de linha. A cada 15 min ele lê o ESTADO (os jobs
falhos + a presença do pedido em `bling_orders`) e abre uma ocorrência
`pedido:<bling_id>` por pedido esgotado que continua fora. Fecha sozinha quando
o pedido entra — inclusive quando alguém o importa à mão ou a rede diária o
pega.

## Por que "esgotado" e não "falhou"
`failed_jobs_alert_scan` já alerta cada FAILED dos últimos 10 min, e o job
falha de novo a cada volta do sweep: alertar por falha seria o mesmo barulho
11 vezes. O robô só fala quando não há mais ninguém tentando, e uma vez só.
Um job é ESGOTADO quando está FAILED com `finished_at` e:
- `payload.sweep_attempts` chegou ao teto do sweep (8), ou
- `created_at` passou da janela do sweep (3 dias — ele nem olha mais), ou
- o sweep está desligado por flag (aí qualquer FAILED é final).
FAILED ainda dentro do loop conta em `em_retentativa` e não abre nada.

## Peneira do Bling ao vivo
Job de ingest só tem o `bling_order_id` no payload — não o número do pedido,
que é como a operação fala. Então, pra candidato NOVO, o robô faz UM
`get_order` no Bling (teto de _MAX_CONFERENCIAS_BLING por rodada, resultado
cacheado em `dados`): o Bling responde → o título sai com o número e fica
provado que "o Bling tem, o DaVinci não gravou"; 404 → o pedido não existe
mais no Bling (pedido de teste apagado antes do ingest) e NÃO vira ocorrência;
Bling instável → abre do mesmo jeito com o id cru, que é melhor que calar.

## O que NUNCA fecha uma ocorrência
O tempo. O `background_jobs_gc` poda os FAILED de ingest 7 dias depois, e
alguém pode re-disparar o job (volta a PENDING): nos dois casos o job some da
lista de candidatos, mas o pedido CONTINUA fora do banco. Por isso a rodada
re-vê as abertas pelo `dados.bling_id` e chama `rever` — sem esse passo o
`fechar_nao_vistas` mataria como "sumiu" justamente o caso mais antigo, que é
o mais grave.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import structlog
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import session_scope
from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    BlingOrder,
    OuvidoriaOcorrencia,
    OuvidoriaRobo,
)
from app.services import bling_orders, ouvidoria
from app.services.advisory_lock import SYNC_NAMESPACE

logger = structlog.get_logger()

ROBO = "vigia_ingest_bling"

# Advisory lock do sweep (namespace SYNC compartilhado).
_SWEEP_LOCK_KEY = 0x76676962  # ascii "vgib"

# Tetos do `ingest_orders_retry_sweep` (app/worker.py, INGEST_SWEEP_MAX_ATTEMPTS
# e INGEST_SWEEP_MAX_AGE). São COPIADOS de propósito: `app.worker` importa este
# serviço no tick, então importar de lá fecharia um ciclo. Se alguém mexer no
# teto do sweep, mexa aqui junto (ou mova as constantes pra um módulo leve —
# está anotado como pendência).
_SWEEP_MAX_ATTEMPTS = 8
_SWEEP_MAX_AGE = timedelta(days=3)

# Padrão quando a config do robô não tem a chave (a linha de ouvidoria_robos
# nasce com este valor — services/ouvidoria.ROBOS).
_IDADE_MIN = 30
# Conferências ao vivo no Bling por rodada (1 GET por pedido). O que passar
# fica pra próxima rodada — protege a cota do Bling no dia em que a importação
# inteira parou e a lista de esgotados vem grande.
_MAX_CONFERENCIAS_BLING = 20
# Teto de jobs falhos lidos por rodada, do MAIS NOVO pro mais velho. A ordem
# é o que importa: no dia em que a importação para em massa cabem mais de 500
# FAILED na janela de 7 dias do `background_jobs_gc`, e lendo do mais velho a
# cota inteira ficava com jobs que já viraram ocorrência — nenhum pedido novo
# ganharia linha até os antigos envelhecerem. O que passa do teto não se perde:
# ocorrência já aberta é re-vista pelo `dados.bling_id` (passo 3, `rever`).
_LIMITE_JOBS = 500
# Eventos em que "o pedido não entrou" não faz sentido: o Bling APAGOU o pedido.
_EVENTOS_EXCLUSAO = ("pedido.exclusao", "order.deleted")

_TZ_BR = ZoneInfo("America/Sao_Paulo")

ACAO = (
    "Ver o erro em Sistema › Sincronizações; abrir o pedido no Bling e salvar "
    "de novo (re-dispara o webhook). Se o pedido não existe mais no Bling, "
    "marcar Ignorar."
)
# A lista de jobs já filtrada pelo tipo e pelo status — sem isso a pessoa cai
# em centenas de sync_product e não acha o pedido.
LINK = "/sincronizacoes?type=ingest_bling_order&status=failed"

# Contadores de uma rodada (ouvidoria_rodadas.contadores), em linguagem de
# operação: jobs_falhos = jobs FAILED lidos; em_retentativa = ainda no loop do
# sweep (ou novos demais pro `idade_min`); esgotados = ninguém mais tenta e o
# pedido continua fora; entraram = esgotados cujo pedido já está no banco;
# na_fila = PENDING/RUNNING parados; nao_existe_no_bling = o Bling devolveu
# 404; conferidos_bling / bling_falhou = conferência ao vivo; novas / persistem
# / sumiram = ocorrências.
_CONTADORES = (
    "jobs_falhos", "em_retentativa", "esgotados", "entraram", "na_fila",
    "novas", "persistem", "sumiram", "nao_existe_no_bling",
    "conferidos_bling", "bling_falhou",
)


# ─── regras puras ──────────────────────────────────────────────────────────


def esgotado(job: BackgroundJob, agora: datetime, sweep_ligado: bool) -> bool:
    """O ingest deste pedido acabou — nenhuma rede automática vai tentar de
    novo. É o que separa "falhou" (barulho de 5 em 5 min, já coberto pelo
    `failed_jobs_alert_scan`) de "ninguém mais tenta", que é o que precisa de
    gente."""
    if job.status != BackgroundJobStatus.FAILED or job.finished_at is None:
        return False
    if not sweep_ligado:
        # Sem o sweep re-dirigindo, as 3 tentativas do arq são tudo que houve.
        return True
    if _tentativas_sweep(job) >= _SWEEP_MAX_ATTEMPTS:
        return True
    # Passou da janela do sweep: ele nem lista mais este job.
    return job.created_at is not None and agora - _utc(job.created_at) >= _SWEEP_MAX_AGE


def _tentativas_sweep(job: BackgroundJob) -> int:
    """Rodadas que o `ingest_orders_retry_sweep` já gastou neste job."""
    try:
        return int((job.payload or {}).get("sweep_attempts", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _utc(dt: datetime) -> datetime:
    """Postgres devolve tudo com fuso; linha montada à mão num teste pode vir
    ingênua — tratar como UTC evita o TypeError de subtrair datas mistas."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _bling_id(payload: dict | None) -> int | None:
    try:
        return int((payload or {}).get("bling_order_id"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _quando(dt: datetime | None) -> str:
    return _utc(dt).astimezone(_TZ_BR).strftime("%d/%m %H:%M") if dt else "?"


def titulo_ocorrencia(bling_id: int, numero: str | None) -> str:
    """Como a operação fala do pedido: pelo número quando o Bling deu, pelo id
    cru quando não deu pra conferir."""
    alvo = numero or f"#{bling_id}"
    return f"Pedido {alvo} do Bling não entrou no DaVinci"


def detalhe_ocorrencia(job: BackgroundJob, conferencia: str | None) -> str:
    """Quando chegou, quantas vezes se tentou e o último erro — é o que a
    pessoa precisa antes de abrir o Bling."""
    tentativas_sweep = _tentativas_sweep(job)
    total = 3 * (tentativas_sweep + 1)
    evento = (job.payload or {}).get("event") or "de pedido"
    partes = [
        f"Webhook {evento} chegou em {_quando(job.created_at)}",
        f"{total} tentativas (3 do arq × {tentativas_sweep + 1} rodadas do sweep)",
        f"último erro: {job.error or '—'}",
    ]
    if conferencia:
        partes.append(conferencia)
    return " · ".join(partes)


# ─── leitura do banco ──────────────────────────────────────────────────────


async def _cliente_bling(session: AsyncSession):
    """BlingClient da conta principal (persiste o refresh). None = não há
    integração Bling cadastrada — aí não dá pra conferir ao vivo."""
    return await bling_orders._bling_client_for_user(session, None)  # noqa: SLF001


async def _jobs_falhos(session: AsyncSession) -> list[BackgroundJob]:
    """Os ingests de pedido que terminaram em falha (usa o índice
    `ix_background_jobs_type_status`). A peneira de esgotado/idade/evento é em
    Python: o payload é JSONB sem índice.

    O TETO pega os mais RECENTES (ver `_LIMITE_JOBS`) e a lista volta ordenada
    do mais antigo pro mais novo, que é o que o laço de candidatos espera: um
    job por pedido, o último vence."""
    rows = list(
        (
            await session.execute(
                select(BackgroundJob)
                .where(
                    BackgroundJob.type == BackgroundJobType.INGEST_BLING_ORDER,
                    BackgroundJob.status == BackgroundJobStatus.FAILED,
                    BackgroundJob.finished_at.is_not(None),
                )
                .order_by(BackgroundJob.created_at.desc())
                .limit(_LIMITE_JOBS)
            )
        )
        .scalars()
        .all()
    )
    rows.reverse()
    return rows


async def _na_fila(session: AsyncSession, corte: datetime) -> int:
    """Ingests parados em PENDING/RUNNING há mais que a idade mínima. Só
    resumo por enquanto: o `background_jobs_gc` recicla RUNNING sem heartbeat,
    e PENDING preso é o arq ter perdido o job — se aparecer em produção, vira
    ocorrência na mesma chave `pedido:<id>`."""
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(BackgroundJob)
                .where(
                    BackgroundJob.type == BackgroundJobType.INGEST_BLING_ORDER,
                    BackgroundJob.status.in_(
                        (BackgroundJobStatus.PENDING, BackgroundJobStatus.RUNNING)
                    ),
                    BackgroundJob.created_at < corte,
                )
            )
        ).scalar_one()
    )


async def _no_banco(session: AsyncSession, ids: set[int]) -> set[int]:
    """Quais desses pedidos já existem em `bling_orders` — QUALQUER situação,
    inclusive 'excluido': a linha existe, o pedido passou pelo DaVinci e "não
    entrou" deixou de ser verdade."""
    lista = list(ids)
    out: set[int] = set()
    for i in range(0, len(lista), 500):
        out |= {
            int(n)
            for n in (
                await session.execute(
                    select(BlingOrder.bling_id).where(
                        BlingOrder.bling_id.in_(lista[i : i + 500])
                    )
                )
            )
            .scalars()
            .all()
            if n is not None
        }
    return out


async def _apagados_no_bling(session: AsyncSession, ids: set[int]) -> set[int]:
    """Pedidos que o Bling APAGOU depois (um job de exclusão que terminou bem
    pro mesmo id). Não há o que importar — a ocorrência, se existir, fecha."""
    if not ids:
        return set()
    rows = (
        await session.execute(
            select(BackgroundJob.payload["bling_order_id"].astext).where(
                BackgroundJob.type == BackgroundJobType.INGEST_BLING_ORDER,
                BackgroundJob.status == BackgroundJobStatus.SUCCEEDED,
                BackgroundJob.payload["event"].astext.in_(_EVENTOS_EXCLUSAO),
                BackgroundJob.payload["bling_order_id"].astext.in_(
                    [str(i) for i in ids]
                ),
            )
        )
    ).scalars().all()
    out: set[int] = set()
    for v in rows:
        try:
            out.add(int(v))
        except (TypeError, ValueError):
            continue
    return out


async def _abertas(session: AsyncSession) -> dict[int, OuvidoriaOcorrencia]:
    """bling_id → ocorrência aberta deste robô. A chave é `pedido:<bling_id>`,
    mas quem manda é o `dados.bling_id` (é o que a rodada compara com o
    banco)."""
    rows = (
        (
            await session.execute(
                select(OuvidoriaOcorrencia).where(
                    OuvidoriaOcorrencia.robo_chave == ROBO,
                    OuvidoriaOcorrencia.fechada_em.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    out: dict[int, OuvidoriaOcorrencia] = {}
    for o in rows:
        bid = _bling_id(o.dados)
        if bid is None:
            # Ocorrência sem o id em `dados` (linha antiga ou mexida à mão):
            # tira da chave, que é o contrato desde o primeiro dia.
            try:
                bid = int(str(o.chave).split(":", 1)[1])
            except (IndexError, ValueError):
                continue
        out[bid] = o
    return out


# ─── conferência ao vivo ───────────────────────────────────────────────────


async def _conferir_no_bling(client, bling_id: int) -> tuple[str, dict]:
    """('achou', data) | ('nao_existe', {}) | ('falhou', {'erro': …}).

    404 (e resposta vazia, que é o outro jeito de o Bling dizer que não tem o
    pedido) significa pedido apagado antes do ingest — não é problema de
    ninguém. Qualquer outro erro é instabilidade: na dúvida o robô abre, só
    sem o número."""
    try:
        data = await client.get_order(bling_id)
    except httpx.HTTPStatusError as e:
        if e.response is not None and e.response.status_code == 404:
            return "nao_existe", {}
        return "falhou", {"erro": f"HTTP {e.response.status_code if e.response else '?'}"}
    except Exception as e:  # noqa: BLE001 — timeout, 5xx, rede: nunca derruba
        return "falhou", {"erro": str(e)[:200]}
    if not data:
        return "nao_existe", {}
    return "achou", data


def _situacao(data: dict) -> str | None:
    s = (data or {}).get("situacao")
    if isinstance(s, dict):
        v = s.get("id") or s.get("valor")
        return str(v) if v is not None else None
    return str(s) if s not in (None, "") else None


# ─── rodada ────────────────────────────────────────────────────────────────


async def vigia_ingest_bling_run(session: AsyncSession) -> dict:
    """Uma rodada: lê os ingests que acabaram em falha, separa os ESGOTADOS
    cujo pedido continua fora de `bling_orders` e abre/mantém uma ocorrência
    por pedido. O que entrou (ou o Bling apagou) some da rodada e fecha
    sozinho no `fechar_nao_vistas`."""
    agora = datetime.now(UTC)
    async with ouvidoria.Rodada(session, ROBO) as r:
        for k in _CONTADORES:
            r.contadores[k] = 0
        robo = await session.get(OuvidoriaRobo, ROBO)
        cfg = ouvidoria.config_do_robo(robo, ROBO)
        idade = timedelta(minutes=int(cfg.get("idade_min") or _IDADE_MIN))
        # Sweep desligado por flag = as 3 tentativas do arq foram tudo.
        sweep_ligado = bool(get_settings().enable_ingest_orders_retry_sweep)

        # 1) Jobs falhos → candidatos (um por pedido, o mais recente).
        jobs = await _jobs_falhos(session)
        r.contadores["jobs_falhos"] = len(jobs)
        candidatos: dict[int, BackgroundJob] = {}
        for job in jobs:
            payload = job.payload or {}
            if payload.get("event") in _EVENTOS_EXCLUSAO:
                continue  # pedido apagado não "entra" — não há o que cobrar
            bling_id = _bling_id(payload)
            if bling_id is None:
                continue
            if not esgotado(job, agora, sweep_ligado):
                r.contadores["em_retentativa"] += 1
                continue
            if job.created_at is None or agora - _utc(job.created_at) < idade:
                # Esgotou rápido demais (sweep desligado, por exemplo): dá o
                # tempo da config antes de chamar gente — vale como "ainda no
                # caminho normal".
                r.contadores["em_retentativa"] += 1
                continue
            candidatos[bling_id] = job  # ordenado por created_at: o último vence

        r.contadores["na_fila"] = await _na_fila(session, agora - idade)

        # 2) Abertas + candidatos → uma consulta de presença pra todos.
        abertas = await _abertas(session)
        ids = set(candidatos) | set(abertas)
        presentes = await _no_banco(session, ids)
        apagados = await _apagados_no_bling(session, ids - presentes)

        # 3) Julgamento, pedido a pedido. O client do Bling é montado UMA vez,
        #    e só se algum candidato precisar de conferência (montar custa um
        #    decrypt e pode renovar token).
        conferencias = 0
        cliente_pronto = False
        cliente = None
        for bling_id in sorted(ids):
            aberta = abertas.get(bling_id)
            job = candidatos.get(bling_id)
            if bling_id in presentes:
                # Entrou. Não é vista → o passo 4 fecha a ocorrência como sumiu.
                r.contadores["entraram"] += 1
                continue
            if bling_id in apagados:
                r.contadores["nao_existe_no_bling"] += 1
                continue
            if job is None:
                # Aberta órfã: o gc podou o job (FAILED > 7 dias) ou alguém
                # re-disparou (voltou a PENDING) e o pedido CONTINUA fora.
                # `rever` mantém aberta — fechar aqui seria dizer "resolveu"
                # justamente no caso mais velho.
                if aberta is not None:
                    r.rever(aberta, agora=agora)
                    r.contadores["persistem"] += 1
                continue

            dados_cache = aberta.dados if aberta is not None else {}
            numero = (dados_cache or {}).get("numero")
            situacao = (dados_cache or {}).get("situacao_bling")
            conferido_em = (dados_cache or {}).get("conferido_bling_em")
            conferencia: str | None = None
            if numero:
                # Já conferido numa rodada anterior — o Bling não muda de ideia
                # sobre o número do pedido.
                conferencia = (
                    f"o Bling responde o pedido (nº {numero}"
                    + (f", situação {situacao}" if situacao else "")
                    + ") — é o DaVinci que não gravou"
                )
            elif conferencias < _MAX_CONFERENCIAS_BLING:
                conferencias += 1
                if not cliente_pronto:
                    cliente_pronto = True
                    try:
                        cliente = await _cliente_bling(session)
                    except Exception as e:  # noqa: BLE001
                        cliente = None
                        logger.warning(
                            "vigia_ingest_bling_sem_cliente", error=str(e)[:200]
                        )
                if cliente is None:
                    # Sem integração Bling cadastrada: abre do mesmo jeito (o
                    # Vigia de credenciais é quem cobra a integração).
                    conferencia = "não consegui conferir no Bling: sem integração cadastrada"
                    r.contadores["bling_falhou"] += 1
                else:
                    estado, data = await _conferir_no_bling(cliente, bling_id)
                    if estado == "achou":
                        r.contadores["conferidos_bling"] += 1
                        numero = str(data.get("numero") or "") or None
                        situacao = _situacao(data)
                        conferido_em = agora.isoformat()
                        conferencia = (
                            f"o Bling responde o pedido (nº {numero or '?'}"
                            + (f", situação {situacao}" if situacao else "")
                            + ") — é o DaVinci que não gravou"
                        )
                    elif estado == "nao_existe":
                        # Pedido de teste apagado antes do ingest: abrir aqui
                        # seria ruído "pessoa" por pedido que não existe.
                        r.contadores["conferidos_bling"] += 1
                        r.contadores["nao_existe_no_bling"] += 1
                        continue
                    else:
                        r.contadores["bling_falhou"] += 1
                        conferencia = (
                            f"não consegui conferir no Bling: {data.get('erro') or 'erro'}"
                        )

            row = await r.registrar(
                chave=f"pedido:{bling_id}",
                plataforma="bling",
                pedido=numero or str(bling_id),
                titulo=titulo_ocorrencia(bling_id, numero),
                detalhe=detalhe_ocorrencia(job, conferencia),
                acao=ACAO,
                link=LINK,
                severidade="pessoa",
                precisa_pessoa=True,
                dados={
                    "bling_id": bling_id,
                    "job_id": str(job.id),
                    "event": (job.payload or {}).get("event"),
                    "webhook_em": (
                        _utc(job.created_at).isoformat() if job.created_at else None
                    ),
                    "tentativas_sweep": _tentativas_sweep(job),
                    "erro": job.error,
                    "ultima_falha_em": (
                        _utc(job.finished_at).isoformat() if job.finished_at else None
                    ),
                    "numero": numero,
                    "situacao_bling": situacao,
                    "conferido_bling_em": conferido_em,
                },
                agora=agora,
            )
            if row.fechada_em is None and row.aberta_em == agora:
                r.contadores["novas"] += 1
            else:
                r.contadores["persistem"] += 1

        # 4) O que a rodada não viu, entrou (ou o Bling apagou) → fecha sozinha.
        r.contadores["sumiram"] = await r.fechar_nao_vistas()
        r.contadores["esgotados"] = r.contadores["novas"] + r.contadores["persistem"]

        esgotados = r.contadores["esgotados"]
        entraram = r.contadores["entraram"]
        partes = [
            f"{esgotados} esgotado{'s' if esgotados != 1 else ''}",
            f"{entraram} entraram" if entraram != 1 else "1 entrou",
        ]
        if r.contadores["na_fila"]:
            partes.append(f"{r.contadores['na_fila']} na fila")
        if r.contadores["nao_existe_no_bling"]:
            n = r.contadores["nao_existe_no_bling"]
            partes.append(
                f"{n} não existe{'m' if n != 1 else ''} mais no Bling"
            )
        if falhou := r.contadores["bling_falhou"]:
            # O NÚMERO importa: 1 soluço e 20 pedidos sem conferência são
            # coisas diferentes pra quem lê a última rodada no painel.
            partes.append(f"Bling não respondeu à conferência de {falhou}")
        r.resumo = " · ".join(partes)

    aviso = await ouvidoria.avisar_pendentes(session, ROBO)
    return {**dict(r.contadores), "avisadas": aviso.get("avisadas", 0), "resumo": r.resumo}


async def vigia_ingest_bling_sweep() -> dict:
    """Sweep do cron / "Rodar agora": sessão própria, serializado por advisory
    lock transacional numa sessão SÓ do lock — a `Rodada` commita ao sair e um
    commit soltaria o lock se ele estivesse na mesma sessão. O modo do robô NÃO
    é olhado aqui: o tick do worker é quem sai quando está `desligado`; o botão
    "Rodar agora" roda mesmo desligado (a pessoa pediu)."""
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
            return await vigia_ingest_bling_run(session)
