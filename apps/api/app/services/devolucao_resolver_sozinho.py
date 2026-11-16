# ruff: noqa: E501, S608
"""Devoluções: o sistema resolve sozinho quando tudo voltou pro estoque.

Vinicius, 16/11/2026: "quando tiver estoque lançado de todos os itens da
devolução o sistema pode resolver sozinho informando que voltou no estoque".
E: "pedidos como perdimento, extraviado tal ele não vai encerrar" — esses
ficam em "A finalizar" pro adm ou o Cairo (os únicos que resolvem à mão).

Resolve sozinho quando, no pedido:

- toda linha lançada ENTROU no estoque (movimento feito, não estornado, sem
  "falhou/conferindo/sem confirmação") e nenhuma está com o técnico; e
- não sobra peça do pedido sem lançamento — a mesma conta da janela ("falta
  lançar"), com o kit aberto em peças. Pedido que a conta não enxerga (fora
  das situações da vw_devolucoes) não resolve sozinho.

Chamado aberto não segura (o chamado é outra coisa — a conta do Reembolso lê
o valor quando ele terminar). O Bling não é tocado: a entrada no estoque já
leva o pedido pra Resolvido quando todos os itens voltaram.

E o contrário: pedido que ESTE robô resolveu e deixou de estar todo no
estoque (estorno, peça nova lançada sem estoque) volta pra lista sozinho.
Resolução feita por pessoa nunca é tocada.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import text

from app.config import get_settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()
SCHEMA = get_settings().database_schema

# "Como terminou" gravado pelo robô (a tela mostra "resolvido sozinho").
COMO = "Voltou pro estoque (resolvido sozinho)"
# Gravado (com `resolvido_at` vazio = aberto) quando uma PESSOA reabre: o robô
# não fecha de novo o que alguém reabriu.
REABERTO_A_MAO = "reaberto à mão"

# Por pedido: tudo no estoque? E o estado do Resolver (mesma regra do aberto do
# painel: resolvido antes da entrada ATUAL em Aguardando Devolução não vale).
_ESTADO_SQL = f"""
WITH lanc AS (
    SELECT btrim(d.pedido_bling) AS pedido,
           bool_and(
               d.data_devolvido_estoque IS NOT NULL
               AND d.estoque_mov_revertido_at IS NULL
               AND COALESCE(d.estoque_status, 'ok') = 'ok'
               AND NOT (d.condicao_produto = 'Manutenção'
                        AND COALESCE(btrim(d.manutencao_destino), '') = '')
           ) AS tudo_no_estoque,
           max(d.created_at) AS ultimo_lanc
    FROM "{SCHEMA}".devolutions d
    WHERE d.pedido_bling IS NOT NULL AND btrim(d.pedido_bling) <> ''
    GROUP BY btrim(d.pedido_bling)
), entrada AS (
    SELECT bo.numero AS pedido, max(bo.aguardando_devolucao_data) AS entrada
    FROM "{SCHEMA}".bling_orders bo
    WHERE bo.numero IN (SELECT pedido FROM lanc)
    GROUP BY bo.numero
)
SELECT l.pedido, l.tudo_no_estoque, e.entrada, r.resolvido_como,
       -- Chamado da devolução segurado pela IA ou esperando algo (foto…): fica
       -- aberto no painel pra aparecer no "Onde agir".
       EXISTS (SELECT 1 FROM "{SCHEMA}".chamados c
               JOIN "{SCHEMA}".chamado_mensagem m ON m.chamado_id = c.id
               WHERE c.pedido_bling = l.pedido AND c.origem = 'devolucao' AND NOT c.resolvido
                 AND m.tipo = 'abertura' AND m.status IN ('pendente', 'segurada')) AS chamado_esperando,
       -- Lançamento desta devolução: o pedido que volta a cair em Aguardando
       -- Devolução (2ª devolução) não se resolve com o estoque da 1ª.
       (e.entrada IS NULL
        OR (l.ultimo_lanc AT TIME ZONE 'America/Sao_Paulo')::date >= e.entrada) AS desta_entrada,
       (r.resolvido_at IS NULL
        OR (e.entrada IS NOT NULL
            AND (r.resolvido_at AT TIME ZONE 'America/Sao_Paulo')::date < e.entrada)) AS aberto
FROM lanc l
JOIN entrada e ON e.pedido = l.pedido
LEFT JOIN "{SCHEMA}".devolucao_rastreio r ON r.pedido_bling = l.pedido
WHERE {{filtro}}
"""
_CANDIDATOS = _ESTADO_SQL.replace(
    "{filtro}",
    "(l.tudo_no_estoque AND (r.resolvido_at IS NULL OR (e.entrada IS NOT NULL AND "
    "(r.resolvido_at AT TIME ZONE 'America/Sao_Paulo')::date < e.entrada)) "
    "AND (e.entrada IS NULL OR (l.ultimo_lanc AT TIME ZONE 'America/Sao_Paulo')::date >= e.entrada) "
    "AND r.resolvido_como IS DISTINCT FROM :reaberto) "
    "OR (NOT l.tudo_no_estoque AND r.resolvido_como = :como AND r.resolvido_at IS NOT NULL)",
)
_UM_PEDIDO = _ESTADO_SQL.replace("{filtro}", "l.pedido = :pedido")

# Grava só se continua aberto (uma pessoa pode ter resolvido no meio).
_RESOLVER_SQL = f"""
INSERT INTO "{SCHEMA}".devolucao_rastreio AS r
    (pedido_bling, resolvido_at, resolvido_por, resolvido_como, created_at, updated_at)
VALUES (:pedido, :agora, NULL, :como, :agora, :agora)
ON CONFLICT (pedido_bling) DO UPDATE
    SET resolvido_at = EXCLUDED.resolvido_at,
        resolvido_por = NULL,
        resolvido_como = EXCLUDED.resolvido_como,
        updated_at = EXCLUDED.updated_at
    WHERE r.resolvido_at IS NULL
       OR (CAST(:entrada AS date) IS NOT NULL
           AND (r.resolvido_at AT TIME ZONE 'America/Sao_Paulo')::date < CAST(:entrada AS date))
"""
# Resolvido pelo robô e todos os lançamentos foram apagados (lixeira): volta.
_REABRIR_SEM_LANCAMENTO_SQL = f"""
UPDATE "{SCHEMA}".devolucao_rastreio r
   SET resolvido_at = NULL, resolvido_por = NULL, resolvido_como = NULL, updated_at = :agora
 WHERE r.resolvido_como = :como AND r.resolvido_por IS NULL AND r.resolvido_at IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM "{SCHEMA}".devolutions d WHERE btrim(d.pedido_bling) = r.pedido_bling)
RETURNING r.pedido_bling
"""
# O que o robô resolveu há pouco: uma vez por hora confere as peças de novo (uma
# peça apagada na lixeira pra relançar e ninguém relançou → volta pra lista).
_RECENTES_DO_ROBO_SQL = f"""
SELECT r.pedido_bling FROM "{SCHEMA}".devolucao_rastreio r
 WHERE r.resolvido_como = :como AND r.resolvido_por IS NULL AND r.resolvido_at >= :desde
 ORDER BY r.resolvido_at DESC
 LIMIT 300
"""
RECONFERIR_DIAS = 14
# Só desfaz o que ESTE robô fez.
_REABRIR_SQL = f"""
UPDATE "{SCHEMA}".devolucao_rastreio
   SET resolvido_at = NULL, resolvido_por = NULL, resolvido_como = NULL, updated_at = :agora
 WHERE pedido_bling = :pedido AND resolvido_como = :como AND resolvido_por IS NULL
"""


async def todas_as_pecas_lancadas(session: AsyncSession, pedido: str) -> bool | None:
    """True = nenhuma peça do pedido sem lançamento (a conta da janela).
    None = a conta não enxerga o pedido (fora das situações da vw_devolucoes)."""
    from app.routers.devolutions import pecas_do_pedido  # tardio: roteador pesado

    pecas = [
        p
        for p in await pecas_do_pedido(session, pedido, exato=True)
        if (p.get("pedido_bling") or "").strip() == pedido
    ]
    if not pecas:
        return None
    return all(p.get("ja_devolvido") for p in pecas)


async def conferir_pedido(
    session: AsyncSession, linha: Any, *, agora: datetime | None = None
) -> str | None:
    """Uma linha de `_ESTADO_SQL`. Devolve "resolvido" | "reaberto" | None."""
    agora = agora or datetime.now(UTC)
    pedido = linha.pedido
    if (
        linha.tudo_no_estoque
        and linha.aberto
        and linha.desta_entrada
        and linha.resolvido_como != REABERTO_A_MAO
        and not linha.chamado_esperando
    ):
        if not await todas_as_pecas_lancadas(session, pedido):
            return None
        res = await session.execute(
            text(_RESOLVER_SQL),
            {"pedido": pedido, "agora": agora, "como": COMO, "entrada": linha.entrada},
        )
        return "resolvido" if res.rowcount else None
    if not linha.tudo_no_estoque and not linha.aberto and linha.resolvido_como == COMO:
        res = await session.execute(
            text(_REABRIR_SQL), {"pedido": pedido, "agora": agora, "como": COMO}
        )
        return "reaberto" if res.rowcount else None
    return None


async def _reconferir_pecas(session: AsyncSession, agora: datetime) -> int:
    """Resolvido pelo robô nos últimos dias e hoje falta peça: reabre."""
    pedidos = (
        (
            await session.execute(
                text(_RECENTES_DO_ROBO_SQL),
                {"como": COMO, "desde": agora - timedelta(days=RECONFERIR_DIAS)},
            )
        )
        .scalars()
        .all()
    )
    n = 0
    for pedido in pedidos:
        if await todas_as_pecas_lancadas(session, pedido) is False:
            res = await session.execute(
                text(_REABRIR_SQL), {"pedido": pedido, "agora": agora, "como": COMO}
            )
            if res.rowcount:
                n += 1
                logger.info("devolucao_reaberta_sozinha", pedido_bling=pedido, porque="falta_peca")
    await session.commit()
    return n


async def rodada(session: AsyncSession, *, reconferir: bool | None = None) -> dict[str, int]:
    """Uma passada por todos os pedidos (worker, a cada 5 min). `reconferir` = olhar
    de novo as peças do que o robô resolveu (padrão: na rodada de :04, 1×/hora)."""
    agora = datetime.now(UTC)
    if reconferir is None:
        reconferir = agora.minute < 5
    sem_lanc = (
        await session.execute(text(_REABRIR_SEM_LANCAMENTO_SQL), {"como": COMO, "agora": agora})
    ).all()
    await session.commit()
    for (pedido,) in sem_lanc:
        logger.info("devolucao_reaberta_sozinha", pedido_bling=pedido, porque="sem_lancamento")
    linhas = (
        await session.execute(text(_CANDIDATOS), {"como": COMO, "reaberto": REABERTO_A_MAO})
    ).all()
    resumo = {
        "candidatos": len(linhas),
        "resolvidos": 0,
        "reabertos": len(sem_lanc) + (await _reconferir_pecas(session, agora) if reconferir else 0),
        "falta_peca": 0,
    }
    for linha in linhas:
        try:
            feito = await conferir_pedido(session, linha, agora=agora)
        except Exception as e:  # noqa: BLE001 — um pedido ruim não para a rodada
            await session.rollback()
            logger.error(
                "devolucao_resolver_sozinho_falhou", pedido=linha.pedido, err=type(e).__name__
            )
            continue
        if feito == "resolvido":
            resumo["resolvidos"] += 1
            logger.info("devolucao_resolvida_sozinha", pedido_bling=linha.pedido)
        elif feito == "reaberto":
            resumo["reabertos"] += 1
            logger.info("devolucao_reaberta_sozinha", pedido_bling=linha.pedido)
        elif linha.tudo_no_estoque:
            resumo["falta_peca"] += 1
        await session.commit()
    return resumo


async def conferir_um(session: AsyncSession, pedido: str) -> str | None:
    """O mesmo pra UM pedido (testes e chamada pontual)."""
    linha = (await session.execute(text(_UM_PEDIDO), {"pedido": pedido.strip()})).first()
    if linha is None:
        return None
    feito = await conferir_pedido(session, linha)
    await session.commit()
    return feito
