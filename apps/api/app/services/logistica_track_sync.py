"""Liga a Logística ao 17track sozinha: registra o rastreio novo e traz a
localização REAL dos Correios.

Antes disto o 17track só era acionado por DEVOLUÇÃO (devolucao_rastreio_sync) e
por um endpoint manual sem botão na tela — ou seja, nenhum rastreio de ENVIO era
registrado. Sem registro o 17track não busca nos Correios e nunca empurra
evento, então a coluna Localização ficava para sempre com o proxy do
marketplace. Foi o que o Eduardo viu no 291809 em 04/09 ("rastreio e localização
de correios não está atualizando"): o 17track respondeu "does not register" pro
AD828496989BR, e nenhuma linha da tabela tinha localização vinda dele.

Duas etapas por rodada:

1. REGISTRAR — números Correios (`...BR`) de pedido ainda vivo cujo
   `rastreio_17track` não bate o `rastreio` atual (nunca registrado, ou o
   marketplace trocou o código). Marca `rastreio_17track` só do que o 17track
   confirmou, então quota esgotada/erro faz tentar de novo na rodada seguinte.

2. PUXAR — `gettrackinfo` dos registrados e grava `localizacao` +
   `localizacao_at`, recalculando a divergência ML × físico. É rede de
   segurança: o tempo real de verdade é o push do webhook
   (`routers/logistica_track`), que chega no instante em que o pacote se move;
   este pull existe pra a Localização não congelar em silêncio se o push falhar
   ou nem estiver configurado no painel do 17track.

Sem saldo no 17track nada disso funciona — `sem_quota` no resumo é o aviso.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Logistica
from app.redis_client import redis
from app.services import logistica_rules, logistica_track

logger = structlog.get_logger()

# Marca "o 17track está sem saldo", pra a TELA poder avisar. Sem isto o único
# sinal seria um logger.warning que ninguém lê — e a Localização inteira fica
# parada sem explicação. Expira sozinha: se a rodada seguinte passar, some.
CHAVE_SEM_QUOTA = "17track:sem_quota"
TTL_SEM_QUOTA = 3 * 3600

# Trava pra o botão da tela e o cron de 15 min não varrerem ao mesmo tempo
# (gastariam saldo em dobro registrando os mesmos números).
CHAVE_LOCK = "17track:sync:lock"
TTL_LOCK = 600

# Número recusado por motivo que NÃO é saldo (formato inválido, transportadora
# incompatível, 5xx) voltaria à fila a cada 15 min pra sempre. Fica em quarentena
# por um dia — sem isso `pendentes` nunca convergiria.
PREFIXO_QUARENTENA = "17track:quarentena:"
TTL_QUARENTENA = 24 * 3600


async def marcar_sem_quota(sem_quota: bool) -> None:
    """Liga/desliga o aviso de saldo esgotado (best-effort — Redis fora do ar
    não pode derrubar o job)."""
    try:
        if sem_quota:
            await redis.set(CHAVE_SEM_QUOTA, datetime.now(UTC).isoformat(), ex=TTL_SEM_QUOTA)
        else:
            await redis.delete(CHAVE_SEM_QUOTA)
    except Exception as e:  # noqa: BLE001
        logger.warning("logistica_track_sync_redis_falhou", err=str(e)[:200])


async def sem_quota_desde() -> str | None:
    """Quando o 17track começou a recusar por falta de saldo (ISO), ou None."""
    try:
        return await redis.get(CHAVE_SEM_QUOTA)
    except Exception:  # noqa: BLE001
        return None


async def _em_quarentena(numeros: list[str]) -> set[str]:
    """Números que falharam por motivo não-saldo há menos de um dia."""
    if not numeros:
        return set()
    try:
        vals = await redis.mget([f"{PREFIXO_QUARENTENA}{n}" for n in numeros])
    except Exception:  # noqa: BLE001 — Redis fora do ar: sem quarentena, tenta todos
        return set()
    return {n for n, v in zip(numeros, vals, strict=False) if v}


async def _por_de_quarentena(numeros: list[str]) -> None:
    if not numeros:
        return
    try:
        async with redis.pipeline(transaction=False) as pipe:
            for n in numeros:
                pipe.set(f"{PREFIXO_QUARENTENA}{n}", "1", ex=TTL_QUARENTENA)
            await pipe.execute()
    except Exception as e:  # noqa: BLE001
        logger.warning("logistica_track_sync_quarentena_falhou", err=str(e)[:200])


# Pedido mais velho que isto não interessa mais pro rastreio físico (e gastaria
# quota à toa): o pacote ou chegou ou virou caso de devolução, que tem esteira
# própria (devolucao_rastreio_sync). O 17track também para de atualizar um
# número parado há ~30 dias, então ir além não traria dado novo.
JANELA_DIAS = 45

# Tetos por rodada. Cada requisição ao 17track leva até 40s, e o endpoint do
# botão roda DENTRO da requisição HTTP do operador — sem teto, a primeira
# execução (401 rastreios acumulados = 10 lotes de register + 10 de fetch) podia
# estourar o tempo do navegador. Com teto o cron de 15 min drena o resto sozinho.
MAX_REGISTRAR = 120
MAX_PUXAR = 120

# O tempo real é o PUSH do webhook; o pull é rede de segurança. Não faz sentido
# reconsultar de 15 em 15 min um pacote cujo evento chegou há pouco — só os sem
# evento nenhum e os parados há mais de isto entram na fila do pull.
PULL_APOS_HORAS = 6

# Depois de entregue o pacote não se move mais e o próprio 17track para de
# rastrear (ele encerra o número após ~15 dias consecutivos como entregue).
# Seguir entrega velha só queimaria saldo: dos 401 rastreios Correios da base,
# 344 já estão "Entregue" — sem este corte, 86% do custo seria inútil.
DIAS_APOS_ENTREGA = 15

# Situações do Bling em que o caso está ENCERRADO — não adianta seguir o pacote.
# "Entregue" NÃO entra aqui: entrega recente ainda vale (é comparando o
# "entregue" do marketplace com o físico dos Correios que a divergência
# aparece); quem tira a entrega velha é `DIAS_APOS_ENTREGA`.
SITUACOES_ENCERRADAS = frozenset(
    {
        "cancelado",
        "resolvido",
        "devolvido estoque",
        "devolvido estoque usado",
        "sucata",
        "perdimento",
        "golpe",
        "atendido",
    }
)


def _encerrado(status_bling: str | None) -> bool:
    return (status_bling or "").strip().lower() in SITUACOES_ENCERRADAS


def _num(rastreio: str | None) -> str:
    """Número normalizado do jeito que o 17track o devolve — MAIÚSCULAS e sem
    espaços. `is_correios` já compara assim; sem normalizar aqui também, um
    rastreio gravado em minúsculas nunca casaria com o `ok` da resposta e o job
    re-registraria o mesmo número a cada 15 min, para sempre."""
    return (rastreio or "").strip().upper()


def _entregue(row: Logistica) -> bool:
    if (row.status_bling or "").strip().lower() == "entregue":
        return True
    ship = ((row.meli_status or {}).get("ship_status") or "").strip().lower()
    return ship == "delivered"


def _entrega_velha(row: Logistica, hoje: date) -> bool:
    """Entregue há mais de `DIAS_APOS_ENTREGA` — sai do alvo.

    Usa o carimbo do `ship_status` (quando o marketplace disse "entregue") e cai
    na data do pedido quando não há carimbo."""
    if not _entregue(row):
        return False
    carimbo = ((row.status_datas or {}).get("ship_status") or {}).get("em")
    quando: date | None = None
    if isinstance(carimbo, str) and carimbo:
        try:
            quando = datetime.fromisoformat(carimbo).date()
        except ValueError:
            quando = None
    if quando is None:
        quando = row.data
    if quando is None:
        return False
    return (hoje - quando).days > DIAS_APOS_ENTREGA


async def _alvo(session: AsyncSession, pedidos: list[str] | None) -> list[Logistica]:
    hoje = date.today()
    stmt = select(Logistica).where(Logistica.rastreio.isnot(None))
    if pedidos:
        stmt = stmt.where(Logistica.pedido_bling.in_(pedidos))
    else:
        stmt = stmt.where(Logistica.data >= hoje - timedelta(days=JANELA_DIAS))
    rows = list((await session.execute(stmt)).scalars().all())
    return [
        r
        for r in rows
        if logistica_track.is_correios(r.rastreio)
        and not _encerrado(r.status_bling)
        # Busca pontual (botão/pedido específico) ignora o corte de entrega: se
        # o operador foi atrás daquele pedido, ele quer a resposta.
        and (bool(pedidos) or not _entrega_velha(r, hoje))
    ]


async def run(
    session: AsyncSession, *, pedidos: list[str] | None = None, puxar: bool = True
) -> dict[str, Any]:
    """Registra os rastreios novos no 17track e atualiza a localização dos Correios.

    `pedidos` restringe a esses pedidos Bling (ignora a janela de dias) — usado
    pelo botão da tela e por conferência pontual. `puxar=False` só registra.
    """
    # Botão da tela e cron de 15 min não podem varrer juntos — registrariam os
    # mesmos números duas vezes. Busca pontual por pedido é leve e passa direto.
    travou = False
    if not pedidos:
        try:
            travou = bool(await redis.set(CHAVE_LOCK, "1", nx=True, ex=TTL_LOCK))
        except Exception:  # noqa: BLE001 — Redis fora do ar não bloqueia o job
            travou = True
        if not travou:
            resumo = {
                "linhas": 0,
                "registrados": 0,
                "atualizados": 0,
                "sem_quota": False,
                "ja_rodando": True,
            }
            logger.info("logistica_track_sync_ja_rodando")
            return resumo
    try:
        return await _run(session, pedidos=pedidos, puxar=puxar)
    finally:
        if travou:
            try:
                await redis.delete(CHAVE_LOCK)
            except Exception as e:  # noqa: BLE001 — a trava expira sozinha
                logger.warning("logistica_track_sync_lock_release_falhou", err=str(e)[:120])


async def _run(
    session: AsyncSession, *, pedidos: list[str] | None, puxar: bool
) -> dict[str, Any]:
    linhas = await _alvo(session, pedidos)
    if not linhas:
        resumo = {"linhas": 0, "registrados": 0, "atualizados": 0, "sem_quota": False}
        logger.info("logistica_track_sync_done", **resumo)
        return resumo

    # --- 1) registrar o que ainda não está no 17track (ou mudou de código) ---
    todos_pendentes = sorted(
        {_num(r.rastreio) for r in linhas if _num(r.rastreio) != _num(r.rastreio_17track)}
    )
    presos = await _em_quarentena(todos_pendentes)
    if presos:
        logger.info("logistica_track_sync_quarentena", numeros=len(presos))
        todos_pendentes = [n for n in todos_pendentes if n not in presos]
    pendentes = todos_pendentes[:MAX_REGISTRAR]
    if len(todos_pendentes) > len(pendentes):
        logger.info(
            "logistica_track_sync_fila",
            pendentes=len(todos_pendentes),
            nesta_rodada=len(pendentes),
            mensagem="teto por rodada; o resto entra na próxima (cron de 15 min)",
        )
    registrados = 0
    sem_quota = False
    if pendentes:
        try:
            res = await logistica_track.register(pendentes)
        except Exception as e:  # noqa: BLE001 — 17track fora do ar não derruba o job
            logger.warning("logistica_track_sync_register_falhou", err=str(e)[:200])
            res = {"ok": [], "sem_quota": False}
        sem_quota = bool(res.get("sem_quota"))
        await marcar_sem_quota(sem_quota)
        ok = {_num(n) for n in (res.get("ok") or [])}
        if ok:
            agora = datetime.now(UTC)
            for r in linhas:
                if _num(r.rastreio) in ok:
                    r.rastreio_17track = _num(r.rastreio)
                    r.rastreio_17track_at = agora
                    registrados += 1
            await session.commit()
        # Recusado sem ser por saldo = problema do próprio número (formato,
        # transportadora). Quarentena de 1 dia pra não reenviar de 15 em 15 min.
        if not sem_quota:
            await _por_de_quarentena([n for n in pendentes if n not in ok])

    # --- 2) puxar a localização dos que já estão registrados ---
    atualizados = 0
    if puxar:
        corte = datetime.now(UTC) - timedelta(hours=PULL_APOS_HORAS)
        numeros = sorted(
            {
                _num(r.rastreio_17track)
                for r in linhas
                if _num(r.rastreio_17track)
                and (r.localizacao_at is None or r.localizacao_at < corte)
            }
        )[:MAX_PUXAR]
        if numeros:
            try:
                eventos = await logistica_track.fetch(numeros)
            except Exception as e:  # noqa: BLE001 — idem
                logger.warning("logistica_track_sync_fetch_falhou", err=str(e)[:200])
                eventos = []
            por_numero = {_num(n): loc for n, loc in eventos}
            agora = datetime.now(UTC)
            for r in linhas:
                loc = por_numero.get(_num(r.rastreio_17track))
                if not loc or loc == r.localizacao:
                    continue
                r.localizacao = loc
                r.localizacao_at = agora
                r.divergencia = logistica_rules.detectar_divergencia_por_plataforma(
                    r.plataforma, r.meli_status, loc
                )
                atualizados += 1
            if atualizados:
                await session.commit()

    resumo = {
        "linhas": len(linhas),
        "pendentes": len(todos_pendentes),
        "registrados": registrados,
        "atualizados": atualizados,
        "sem_quota": sem_quota,
    }
    logger.info("logistica_track_sync_done", **resumo)
    if sem_quota:
        logger.warning(
            "logistica_track_sync_sem_quota",
            pendentes=len(pendentes),
            mensagem="17track sem saldo — a Localização dos Correios não atualiza até recarregar",
        )
    return resumo
