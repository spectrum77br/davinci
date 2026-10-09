"""O recálculo inicial da CAIXA HUMANO do `/atendimento` (09/10/2026).

A Caixa Humano mostra só o que falta responder e a IA não pode responder
(`services/atendimento/humano.py`). Quem calcula é o cron `atendimento_humano`
(a cada minuto, até 200 conversas por rodada): sozinho, ele preenche as ~500
conversas esperando resposta em umas 3 rodadas. Este script é para ver os
números ANTES (seco) e para preencher tudo de uma vez no deploy (`--gravar`).

SECO POR PADRÃO: sem `--gravar`, tria TODAS as conversas do escopo numa
transação SÓ LEITURA (qualquer escrita daria erro) e desfaz no fim — quantas
estão no escopo e quantas entrariam na Caixa Humano, por plataforma, pelo
motivo principal, por motivo e por assunto. Com `--gravar`, grava o cálculo
das desatualizadas (rodadas do mesmo `humano.recalcular` do cron, com a mesma
trava no Redis e o mesmo UPDATE protegido: só a chave `dados.humano`, linha
travada pulada, mensagem nova no meio = descartado) até não sobrar nenhuma,
e mostra a contagem de agora. Retomável: rodar de novo continua do que ficou.
Só lê o banco do DaVinci e grava esse cache: o modelo não é chamado, nada sai
para a plataforma, nada vai para o Bling.

Uso (de dentro de apps/api, no servidor):
    uv run python -m scripts.atendimento_humano_recalcular              # seco: só conta
    uv run python -m scripts.atendimento_humano_recalcular --limite 50  # seco, 50 conversas
    uv run python -m scripts.atendimento_humano_recalcular --gravar

  --seco        só conta (o padrão; pode ser escrito, para deixar claro).
  --gravar      grava (sem ele, só conta).
  --lote N      conversas por rodada no --gravar (padrão 200).
  --limite N    no seco, para depois de N conversas.

A saída é o resumo em JSON: só contagens (nada de comprador — nem nome, nem nº
de pedido, nem texto).
"""

# ruff: noqa: T201

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.services.atendimento import humano


def _argumentos(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="atendimento_humano_recalcular",
        description="Recalcula a Caixa Humano do atendimento (seco por padrão).",
    )
    modo = p.add_mutually_exclusive_group()
    modo.add_argument("--seco", action="store_true", help="só conta (o padrão)")
    modo.add_argument("--gravar", action="store_true", help="grava (sem ele, só conta)")
    p.add_argument("--lote", type=int, default=humano.MAX_POR_RODADA, help="conversas por rodada")
    p.add_argument("--limite", type=int, default=None, help="no seco, para depois de N")
    args = p.parse_args(argv)
    if args.lote < 1 or (args.limite is not None and args.limite < 1):
        p.error("--lote e --limite precisam ser maiores que zero")
    return args


async def _rodar(args: argparse.Namespace) -> int:
    if args.gravar:
        resumo = await humano.preencher(lote=args.lote)
    else:
        resumo = await humano.simular(limite=args.limite)
    print(json.dumps(resumo, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if resumo.get("falhas") else 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_rodar(_argumentos(argv)))


if __name__ == "__main__":
    sys.exit(main())
