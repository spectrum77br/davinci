"""Preenche a ETIQUETA das conversas do `/atendimento` que ainda não têm (RF1, 01/10/2026).

A migration 0353 criou a coluna vazia (NULL) para todas as conversas; dali
em diante o gravar e o cron (`etiqueta_cron.atendimento_etiquetas`) dão
etiqueta a quem é lido ou muda. As conversas antigas e paradas ficam NULL
até este script — enquanto isso, a lista as filtra e conta pela regra de
pré/pós-venda (`routers/atendimento._etiqueta_efetiva`).

SECO POR PADRÃO: sem `--gravar` só calcula e conta (quantas iriam para cada
etiqueta), sem gravar nada. Com `--gravar`, grava a etiqueta (a primeira
classificação NÃO vira linha no histórico: não é mudança), um lote por
transação, com as conversas travadas (a que estiver sendo lida agora fica
para a próxima vez). Retomável: rodar de novo continua das que ficaram NULL.
Só lê o banco do DaVinci (espelho do Bling, trilha da Margem, reclamações):
nenhuma chamada a loja, nada sai para o comprador, nada vai para o Bling.

Uso (de dentro de apps/api, no servidor, DEPOIS do `alembic upgrade head`):
    uv run python -m scripts.atendimento_etiquetas_backfill            # seco: só conta
    uv run python -m scripts.atendimento_etiquetas_backfill --gravar
    uv run python -m scripts.atendimento_etiquetas_backfill --gravar --limite 200

  --seco        só conta (o padrão; pode ser escrito, para deixar claro).
  --gravar      grava (sem ele, só conta).
  --todas       recalcula TODAS as conversas, não só as sem etiqueta (respeita
                a troca à mão; uma etiqueta que mudar vira linha no histórico).
  --lote N      conversas por transação (padrão 500).
  --limite N    para depois de N conversas.

A saída é o resumo em JSON: só contagens (nada de comprador).
"""

# ruff: noqa: T201

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.services.atendimento import etiqueta_cron


def _argumentos(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="atendimento_etiquetas_backfill",
        description="Preenche a etiqueta das conversas do atendimento (seco por padrão).",
    )
    modo = p.add_mutually_exclusive_group()
    modo.add_argument("--seco", action="store_true", help="só conta (o padrão)")
    modo.add_argument("--gravar", action="store_true", help="grava (sem ele, só conta)")
    p.add_argument("--todas", action="store_true", help="recalcula todas, não só as sem etiqueta")
    p.add_argument("--lote", type=int, default=500, help="conversas por transação")
    p.add_argument("--limite", type=int, default=None, help="para depois de N conversas")
    args = p.parse_args(argv)
    if args.lote < 1 or (args.limite is not None and args.limite < 1):
        p.error("--lote e --limite precisam ser maiores que zero")
    return args


async def _rodar(args: argparse.Namespace) -> int:
    resumo = await etiqueta_cron.preencher(
        seco=not args.gravar, todas=args.todas, lote=args.lote, limite=args.limite
    )
    print(json.dumps(resumo, ensure_ascii=False, indent=2))
    return 1 if resumo.get("falhas") else 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_rodar(_argumentos(argv)))


if __name__ == "__main__":
    sys.exit(main())
