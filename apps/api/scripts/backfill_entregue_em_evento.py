"""Corrige o `logistica.entregue_em` antigo pela hora do EVENTO de entrega (16/11).

Até 16/11 o `entregue_em` dos envios Correios era a hora em que o DaVinci LEU a
entrega no 17track, não a dos Correios — pedido 306097 (Amazon KIA,
AE071697531BR): 29/10 11:01 (reconsulta forçada) × entregue 28/10 15:23. O
código novo (`logistica_track_sync.momento_da_entrega`) só vale pras entregas
carimbadas DEPOIS do deploy: o carimbo é de uma vez só, as linhas antigas
ficam como estão. Este script relê no 17track as linhas Correios que já têm
`entregue_em` e troca pela hora do evento.

Fonte: o `gettrackinfo` do 17track (o mesmo `fetch_detalhado` do botão ⟳ —
leitura pura, NÃO gasta quota e não registra número). O `delivered_at` do
Melhor Envio (`conciliacao_fretes_pagos.entregue_em`) NÃO serve: na revisão de
16/11 ele ficou de -2,6 h a +11,1 h do DaVinci — às vezes DEPOIS da leitura.

Regras (as mesmas do carimbo novo):
  - só com o 17track dizendo "Delivered" e a data do evento no payload;
  - a data só ANDA PRA TRÁS (`momento_da_entrega(evento, atual)`): o que está
    gravado é a hora da leitura, que nunca é antes da entrega — se o evento
    vier depois do gravado, fica o gravado;
  - recuo maior que `--max-dias` não é gravado: vai em "suspeitas" pra olhar
    à mão (rastreio trocado, número reaproveitado...).
O DBA (sem `rastreio_17track`) fica de fora: a SP-API não data a entrega.
A garantia relê a entrega de hora em hora (`garantia.recalcular_todas`) e
recalcula os prazos sozinha, com linha no log da garantia.

SECO POR PADRÃO: sem `--gravar` só lê o 17track e mostra o que mudaria.
Gravar em produção só com o OK do Eduardo.

Uso (de dentro de apps/api, no servidor):
    uv run python -m scripts.backfill_entregue_em_evento                 # seco
    uv run python -m scripts.backfill_entregue_em_evento --pedidos 306097
    uv run python -m scripts.backfill_entregue_em_evento --gravar

  --seco          só mostra (o padrão; pode ser escrito, para deixar claro).
  --gravar        grava as correções (sem ele, nada é gravado).
  --pedidos N...  só esses pedidos do Bling.
  --max-dias N    recuo máximo gravado (padrão 15); acima disso é "suspeita".

A saída é o resumo em JSON: contagens + pedido, rastreio e as duas horas
(Brasília) de cada correção — nada de comprador.
"""

# ruff: noqa: T201

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import Logistica
from app.services import logistica_track, logistica_track_sync

MAX_DIAS_PADRAO = 15.0
SITUACOES = ("corrigir", "suspeita", "igual", "sem_data", "nao_entregue", "desconhecido")


def avaliar(
    atual: datetime, info: dict[str, Any] | None, *, max_dias: float = MAX_DIAS_PADRAO
) -> tuple[str, datetime | None]:
    """(situação, hora nova) de uma linha. Situações: 'corrigir' (grava a hora
    nova), 'suspeita' (recuo maior que `max_dias`: não grava), 'igual' (o
    evento não é antes do gravado), 'sem_data' (entregue, sem data do evento
    no payload), 'nao_entregue' (o 17track não diz "Delivered") e
    'desconhecido' (o 17track não devolveu o número)."""
    if atual.tzinfo is None:
        atual = atual.replace(tzinfo=UTC)
    if not info:
        return "desconhecido", None
    if info.get("status") != "Delivered":
        return "nao_entregue", None
    evento = info.get("entregue_evento_em")
    if not isinstance(evento, datetime):
        return "sem_data", None
    novo = logistica_track_sync.momento_da_entrega(evento, atual)
    if novo >= atual:
        return "igual", None
    if atual - novo > timedelta(days=max_dias):
        return "suspeita", novo
    return "corrigir", novo


def _brt(dt: datetime | None) -> str | None:
    return dt.astimezone(logistica_track.FUSO_CORREIOS).strftime("%d/%m/%Y %H:%M") if dt else None


async def rodar(
    session: AsyncSession,
    *,
    gravar: bool,
    pedidos: list[str] | None = None,
    max_dias: float = MAX_DIAS_PADRAO,
) -> dict[str, Any]:
    stmt = select(Logistica).where(
        Logistica.entregue_em.isnot(None), Logistica.rastreio_17track.isnot(None)
    )
    if pedidos:
        stmt = stmt.where(Logistica.pedido_bling.in_(pedidos))
    linhas = [
        r
        for r in (await session.execute(stmt)).scalars().all()
        if logistica_track_sync._num(r.rastreio_17track)
    ]
    numeros = sorted({logistica_track_sync._num(r.rastreio_17track) for r in linhas})
    det = await logistica_track.fetch_detalhado(numeros)
    resumo: dict[str, Any] = {
        "seco": not gravar,
        "linhas": len(linhas),
        "numeros": len(numeros),
        **dict.fromkeys(SITUACOES, 0),
        "correcoes": [],
        "suspeitas": [],
    }
    for r in linhas:
        num = logistica_track_sync._num(r.rastreio_17track)
        atual = r.entregue_em if r.entregue_em.tzinfo else r.entregue_em.replace(tzinfo=UTC)
        situacao, novo = avaliar(atual, det["info"].get(num), max_dias=max_dias)
        resumo[situacao] += 1
        if novo is None:
            continue
        item = {
            "pedido": r.pedido_bling,
            "rastreio": num,
            "antes": _brt(atual),
            "depois": _brt(novo),
            "recuo_h": round((atual - novo).total_seconds() / 3600, 1),
        }
        resumo["correcoes" if situacao == "corrigir" else "suspeitas"].append(item)
        if gravar and situacao == "corrigir":
            r.entregue_em = novo
    if gravar and resumo["corrigir"]:
        await session.commit()
    return resumo


def _argumentos(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="backfill_entregue_em_evento",
        description="Troca o entregue_em antigo (hora da leitura) pela hora do evento dos"
        " Correios (seco por padrão).",
    )
    modo = p.add_mutually_exclusive_group()
    modo.add_argument("--seco", action="store_true", help="só mostra (o padrão)")
    modo.add_argument("--gravar", action="store_true", help="grava (sem ele, nada é gravado)")
    p.add_argument("--pedidos", nargs="+", help="só esses pedidos do Bling")
    p.add_argument(
        "--max-dias",
        type=float,
        default=MAX_DIAS_PADRAO,
        help=f"recuo máximo gravado (padrão {MAX_DIAS_PADRAO:g})",
    )
    args = p.parse_args(argv)
    if args.max_dias <= 0:
        p.error("--max-dias precisa ser maior que zero")
    return args


async def _rodar(args: argparse.Namespace) -> int:
    # Sessão sem commit automático: no seco nada é gravado nem por engano.
    try:
        async with SessionLocal() as session:
            resumo = await rodar(
                session, gravar=args.gravar, pedidos=args.pedidos, max_dias=args.max_dias
            )
    except logistica_track.Track17Error as e:
        print(f"17track sem resposta confiável, nada gravado: {e}", file=sys.stderr)
        return 1
    print(json.dumps(resumo, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_rodar(_argumentos(argv)))


if __name__ == "__main__":
    sys.exit(main())
