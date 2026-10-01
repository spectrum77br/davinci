"""O cron da etiqueta e o preenchimento das conversas que ainda não têm (RF1, 01/10/2026).

O gravar recalcula a etiqueta quando a LEITURA traz algo novo (conversa
nova, pedido ligado, reclamação do pack). Mas três fontes mudam sem passar
pelo sync — e é para elas que este cron existe:

  • o BLING: o pedido entra ou sai de Aguardando Cancelamento (83955) ou de
    Aguardando Devolução (83957) pelo webhook de pedidos, pela Margem, pelo
    sweep de NF ou à mão no painel do Bling;
  • a TRILHA DA MARGEM: o robô segura e depois resgata o pedido;
  • as RECLAMAÇÕES: a leitura do ML e as devoluções/disputas de Shopee e
    TikTok que a Logística já lê (frente A) abrem e encerram linhas em
    `atendimento_reclamacoes`.

BARATO E EM LOTE: a rodada não passa por todas as conversas. Pega só as que
podem ter mudado (`ids_da_rodada`): as com mensagem nos últimos 7 dias (a
rede de segurança), as que estão numa etiqueta urgente ou trocada à mão (o
fato pode ter acabado), as de pedido HOJE em 83955/83957 no espelho do Bling
e as de reclamação aberta. Os fatos vêm em 3 ou 4 consultas por lote de 200
(`etiqueta_fatos.fatos_em_lote`), não uma por conversa. Cada lote é uma
transação, com as conversas travadas (`FOR NO KEY UPDATE SKIP LOCKED`): a
que o sync ou a troca à mão está mexendo agora fica para a próxima rodada —
nem espera, nem passa por cima de uma troca à mão que acabou de acontecer.

O PREENCHIMENTO (`preencher`, pelo `scripts/atendimento_etiquetas_backfill.py`)
dá etiqueta às conversas antigas que ainda estão sem (NULL): `--seco` por
padrão (só conta, por etiqueta, sem gravar). A primeira classificação não
vira linha no histórico (não é mudança).

Nada aqui sai para a plataforma nem escreve no Bling; o log leva só
contagens.

REGISTRO NO WORKER: `atendimento_etiquetas` está em `functions` e em
`cron_jobs`, a cada 10 min no :08… (`worker._ATENDIMENTO_ETIQUETAS_MINUTOS`:
longe do :00/:30, dos minutos ímpares da leitura e do {4,14,…} do preço da
Amazon e do espelho de NF-e), `timeout=600`. Só roda com o interruptor
próprio `ATENDIMENTO_ETIQUETAS_ATIVA` E a leitura (`ATENDIMENTO_LEITURA_ATIVA`).
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.db as _db
from app.config import get_settings
from app.models import AtendimentoConversa, AtendimentoReclamacao, BlingOrder
from app.redis_client import redis
from app.services.atendimento.constantes import ETIQUETAS, ETIQUETAS_BASE
from app.services.atendimento.etiqueta import calcular_em_lote, recalcular_em_lote
from app.services.bling_situacoes import (
    SITUACAO_AGUARDANDO_CANCELAMENTO_STR,
    SITUACAO_AGUARDANDO_DEVOLUCAO_STR,
)

logger = structlog.get_logger()

# A rede de segurança: conversa com mensagem nesta janela é sempre conferida.
JANELA_RECENTE = timedelta(days=7)
# Conversas por transação (e por consulta de fatos).
LOTE = 200
# Teto por rodada do cron: a mais recente primeiro; o que sobrar fica para a
# próxima (o log diz `cortadas`).
MAX_POR_RODADA = 5000
# Trava da rodada (por schema): o tick seguinte que chega com esta ainda
# rodando sai na hora. Folga sobre o `timeout` do cron.
RODADA_TTL_S = 900
_CHAVE_RODADA = "atendimento:etiquetas:rodada:{}"

# O acontecimento que vai entre parênteses na linha do tempo.
MOTIVO_CRON = "conferência periódica"
MOTIVO_PREENCHIMENTO = "preenchimento"

# As etiquetas que dizem "há algo aberto": o fato pode ter acabado.
_URGENTES = tuple(e for e in ETIQUETAS if e not in ETIQUETAS_BASE)
_SITUACOES_QUENTES = (SITUACAO_AGUARDANDO_CANCELAMENTO_STR, SITUACAO_AGUARDANDO_DEVOLUCAO_STR)


def _agora() -> datetime:
    return datetime.now(UTC)


# ── Quem entra na rodada ──────────────────────────────────────────────────


def _casa_pedido(subconsulta) -> list:
    """A conversa é de um destes pedidos (nº na plataforma, ou o pack/order do ML)?

    As mesmas chaves de `contexto._chaves_do_pedido`, inclusive o order do
    retrato (`dados.pedido_mkt.pedido`): a conversa do pack de carrinho
    guarda o PACK, e o Bling e a reclamação do ML vêm pelo ORDER.
    """
    return [
        AtendimentoConversa.pedido_marketplace.in_(subconsulta),
        AtendimentoConversa.dados["pack_id"].astext.in_(subconsulta),
        AtendimentoConversa.dados["order_id"].astext.in_(subconsulta),
        AtendimentoConversa.dados[("pedido_mkt", "pedido")].astext.in_(subconsulta),
    ]


async def ids_da_rodada(
    session: AsyncSession, *, agora: datetime | None = None, limite: int = MAX_POR_RODADA
) -> list[UUID]:
    """As conversas que podem ter mudado de etiqueta desde a última rodada.

    Recentes (`JANELA_RECENTE`), urgentes ou trocadas à mão, de pedido hoje
    em 83955/83957 e de reclamação aberta. A mais recente primeiro.
    """
    agora = agora or _agora()
    quentes = select(BlingOrder.numeroloja).where(
        BlingOrder.situacao.in_(_SITUACOES_QUENTES), BlingOrder.numeroloja.is_not(None)
    )
    abertas = AtendimentoReclamacao.encerrada_em.is_(None)
    pedidos_reclamados = select(AtendimentoReclamacao.pedido_marketplace).where(
        abertas, AtendimentoReclamacao.pedido_marketplace.is_not(None)
    )
    # O pack da reclamação do ML (ela é gravada pelo ORDER; a conversa do
    # carrinho com vários pedidos só tem o pack).
    pack_col = AtendimentoReclamacao.dados["pack_id"].astext
    packs_reclamados = select(pack_col).where(abertas, pack_col.is_not(None))
    conversas_reclamadas = select(AtendimentoReclamacao.conversa_id).where(
        abertas, AtendimentoReclamacao.conversa_id.is_not(None)
    )
    conds = [
        AtendimentoConversa.ultima_mensagem_em >= agora - JANELA_RECENTE,
        AtendimentoConversa.etiqueta.in_(_URGENTES),
        AtendimentoConversa.etiqueta_manual.is_(True),
        AtendimentoConversa.id.in_(conversas_reclamadas),
        *_casa_pedido(quentes),
        *_casa_pedido(pedidos_reclamados),
        *_casa_pedido(packs_reclamados),
    ]
    return list(
        (
            await session.execute(
                select(AtendimentoConversa.id)
                .where(or_(*conds))
                .order_by(
                    AtendimentoConversa.ultima_mensagem_em.desc().nulls_last(),
                    AtendimentoConversa.id,
                )
                .limit(max(1, limite))
            )
        )
        .scalars()
        .all()
    )


async def _travadas(session: AsyncSession, ids: list[UUID]) -> list[AtendimentoConversa]:
    """As conversas do lote, travadas; a que alguém está mexendo agora fica de fora."""
    return list(
        (
            await session.execute(
                select(AtendimentoConversa)
                .where(AtendimentoConversa.id.in_(ids))
                .order_by(AtendimentoConversa.id)
                .with_for_update(skip_locked=True, key_share=True)
            )
        )
        .scalars()
        .all()
    )


async def recalcular_ids(
    ids: list[UUID], *, motivo: str, agora: datetime | None = None, lote: int = LOTE
) -> dict[str, int]:
    """Recalcula estas conversas, um lote por transação (sessão própria). Nunca levanta por lote.

    `ocupadas` = travadas por outro processo (ficam para a próxima rodada);
    `falhas` = lotes cujos fatos não puderam ser lidos (nada mudou neles).
    """
    resumo = {"conversas": len(ids), "mudaram": 0, "ocupadas": 0, "falhas": 0}
    lote = max(1, lote)
    for i in range(0, len(ids), lote):
        parte = ids[i : i + lote]
        # `_db.SessionLocal` lido na hora da chamada (os testes trocam o engine).
        async with _db.SessionLocal() as session:
            try:
                conversas = await _travadas(session, parte)
                resumo["ocupadas"] += len(parte) - len(conversas)
                mudaram = await recalcular_em_lote(
                    session, conversas, motivo=motivo, agora=agora or _agora()
                )
                if mudaram is None:
                    resumo["falhas"] += 1
                    await session.rollback()
                    continue
                await session.commit()
                resumo["mudaram"] += mudaram
            except Exception as e:  # noqa: BLE001 — um lote ruim não para a rodada
                resumo["falhas"] += 1
                logger.warning(
                    "atendimento_etiquetas_lote_falhou", conversas=len(parte), err=type(e).__name__
                )
                await session.rollback()
    return resumo


async def recalcular_rodada(
    *, agora: datetime | None = None, limite: int = MAX_POR_RODADA
) -> dict[str, int]:
    """Uma rodada do cron: escolhe as conversas (`ids_da_rodada`) e recalcula em lotes."""
    async with _db.SessionLocal() as session:
        ids = await ids_da_rodada(session, agora=agora, limite=limite + 1)
    cortadas = max(0, len(ids) - limite)
    resumo = await recalcular_ids(ids[:limite], motivo=MOTIVO_CRON, agora=agora)
    resumo["cortadas"] = cortadas
    return resumo


# ── O cron ────────────────────────────────────────────────────────────────


async def _trava_da_rodada() -> tuple[bool, str | None]:
    """SET NX da rodada (por schema) → (pegou, token). Redis fora do ar = roda sem trava.

    Sem a trava o pior caso é ler duas vezes: o recálculo é idempotente e as
    conversas de cada lote vão travadas no banco.
    """
    chave = _CHAVE_RODADA.format(get_settings().database_schema)
    token = uuid4().hex
    try:
        pegou = await redis.set(chave, token, nx=True, ex=RODADA_TTL_S)
    except Exception as e:  # noqa: BLE001
        logger.warning("atendimento_etiquetas_trava_indisponivel", err=type(e).__name__)
        return True, None
    return bool(pegou), token


async def _soltar_rodada(token: str | None) -> None:
    if token is None:
        return
    chave = _CHAVE_RODADA.format(get_settings().database_schema)
    try:
        await redis.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end",
            1,
            chave,
            token,
        )
    except Exception:  # noqa: BLE001 — o TTL solta sozinho
        logger.warning("atendimento_etiquetas_trava_soltar_falhou")


async def atendimento_etiquetas(ctx: dict) -> dict[str, int] | None:
    """A cada 10 min: recalcula a etiqueta do que pode ter mudado sem passar pelo sync.

    Só lê o banco do DaVinci (Bling espelhado, trilha da Margem, reclamações)
    e grava a etiqueta e o histórico — nenhuma chamada a loja, nada sai para
    o comprador. Interruptor próprio: só roda com `atendimento_etiquetas_ativa`
    E `atendimento_leitura_ativa` (a leitura já está ligada em produção; sem
    o próprio, o deploy ligaria esta rodada sozinho). Uma rodada por vez
    (trava no Redis). Nunca levanta.
    """
    s = get_settings()
    if not (s.atendimento_leitura_ativa and s.atendimento_etiquetas_ativa):
        return None
    pegou, token = await _trava_da_rodada()
    if not pegou:
        logger.info("atendimento_etiquetas_rodada_ocupada")
        return None
    try:
        resumo = await recalcular_rodada()
    except Exception as e:  # noqa: BLE001
        logger.error("atendimento_etiquetas_falhou", err=type(e).__name__)
        return None
    finally:
        await _soltar_rodada(token)
    logger.info("atendimento_etiquetas_tick", **resumo)
    return resumo


# ── Preenchimento (o script) ──────────────────────────────────────────────


async def _ids_para_preencher(
    session: AsyncSession, *, todas: bool, depois_de: UUID | None, lote: int
) -> list[UUID]:
    consulta = select(AtendimentoConversa.id).order_by(AtendimentoConversa.id).limit(lote)
    if not todas:
        consulta = consulta.where(AtendimentoConversa.etiqueta.is_(None))
    if depois_de is not None:
        consulta = consulta.where(AtendimentoConversa.id > depois_de)
    return list((await session.execute(consulta)).scalars().all())


async def preencher(
    *,
    seco: bool = True,
    todas: bool = False,
    lote: int = 500,
    limite: int | None = None,
    agora: datetime | None = None,
) -> dict[str, Any]:
    """Dá etiqueta às conversas sem etiqueta (ou a todas, com `todas`). `seco` = só conta.

    Anda pelo id (paginação estável, retomável: rodar de novo continua das
    que ficaram NULL). No `seco` nada é gravado — cada lote é calculado e
    desfeito —, e a saída diz quantas iriam para cada etiqueta e quantas
    teriam indicador de secundária. Só contagens: nada de comprador.
    """
    lote = max(1, lote)
    contagem: Counter[str] = Counter()
    resumo: dict[str, Any] = {
        "seco": seco,
        "todas": todas,
        "conversas": 0,
        "com_secundaria": 0,
        "mudaram": 0,
        "ocupadas": 0,
        "falhas": 0,
    }
    depois_de: UUID | None = None
    while limite is None or resumo["conversas"] < limite:
        tamanho = lote if limite is None else min(lote, limite - resumo["conversas"])
        async with _db.SessionLocal() as session:
            ids = await _ids_para_preencher(session, todas=todas, depois_de=depois_de, lote=tamanho)
            if not ids:
                break
            depois_de = ids[-1]
            if seco:
                conversas = list(
                    (
                        await session.execute(
                            select(AtendimentoConversa).where(AtendimentoConversa.id.in_(ids))
                        )
                    )
                    .scalars()
                    .all()
                )
                calculos = await calcular_em_lote(session, conversas)
                await session.rollback()
                resumo["conversas"] += len(ids)
                if calculos is None:
                    resumo["falhas"] += 1
                    continue
                for calculo in calculos.values():
                    contagem[calculo.etiqueta] += 1
                    resumo["com_secundaria"] += bool(calculo.secundarias)
                continue
        parcial = await recalcular_ids(ids, motivo=MOTIVO_PREENCHIMENTO, agora=agora, lote=len(ids))
        resumo["conversas"] += len(ids)
        for k in ("mudaram", "ocupadas", "falhas"):
            resumo[k] += parcial[k]
    if seco:
        resumo["por_etiqueta"] = {e: contagem.get(e, 0) for e in ETIQUETAS}
    else:
        async with _db.SessionLocal() as session:
            resumo["por_etiqueta"] = await contar_por_etiqueta(session)
    return resumo


async def contar_por_etiqueta(session: AsyncSession) -> dict[str, int]:
    """Quantas conversas há em cada etiqueta agora (`sem` = ainda NULL)."""
    linhas = (
        await session.execute(
            select(AtendimentoConversa.etiqueta, func.count()).group_by(
                AtendimentoConversa.etiqueta
            )
        )
    ).all()
    saida = dict.fromkeys(ETIQUETAS, 0)
    saida["sem"] = 0
    for etiqueta, n in linhas:
        saida[etiqueta if etiqueta is not None else "sem"] = int(n or 0)
    return saida
