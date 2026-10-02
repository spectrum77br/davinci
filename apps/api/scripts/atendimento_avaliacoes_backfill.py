"""Importação inicial das AVALIAÇÕES de venda do `/atendimento` (RF8, 02/10/2026).

O cron `atendimento_avaliacoes` já importa sozinho na primeira rodada de
cada loja (Shopee: a janela de 30 dias; ML: os produtos vendidos, 40 por
conta e rodada, continuando a cada 30 min até acabar). Este script é para
fazer a importação de uma vez, À MÃO, antes de ligar o cron — e para ver o
plano antes.

SECO POR PADRÃO: sem `--gravar` só conta, pelo BANCO (nenhuma chamada à
plataforma, nada gravado): lojas e contas que seriam lidas, produtos do ML a
vigiar, avaliações sem resposta perto de virar pendência. Com `--gravar`:
lê as avaliações (SÓ GET — Shopee `get_comment`, ML `/reviews/item` e,
quando o pedido não está no espelho financeiro, `/orders/{id}`), grava em
`atendimento_avaliacoes_loja` e roda a pendência (a conversa `avaliacao` e a
etiqueta AVALIAÇÃO nas sem resposta). Nada sai para o comprador: responder é
outro caminho (o envio, desligado em produção). Precisa da leitura ligada
(`ATENDIMENTO_LEITURA_ATIVA`): desligada, o DaVinci não fala com loja nenhuma.

Uso (de dentro de apps/api, no servidor, DEPOIS do `alembic upgrade head`):
    uv run python -m scripts.atendimento_avaliacoes_backfill              # seco
    uv run python -m scripts.atendimento_avaliacoes_backfill --gravar
    uv run python -m scripts.atendimento_avaliacoes_backfill --gravar --so-shopee

  --seco             só conta (o padrão; pode ser escrito, para deixar claro).
  --gravar           lê e grava (sem ele, só conta).
  --so-shopee        só a Shopee.  --so-ml   só o ML.
  --sem-pendencias   só a leitura (a pendência fica para o cron).
  --max-produtos N   produtos do ML lidos inteiros por conta (padrão 1000).

A saída é o resumo em JSON: só contagens e ids (nada de comprador).
"""

# ruff: noqa: T201

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.config import get_settings
from app.services.atendimento import avaliacoes


def _argumentos(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="atendimento_avaliacoes_backfill",
        description="Importa as avaliações de venda do atendimento (seco por padrão).",
    )
    modo = p.add_mutually_exclusive_group()
    modo.add_argument("--seco", action="store_true", help="só conta (o padrão)")
    modo.add_argument("--gravar", action="store_true", help="lê e grava (sem ele, só conta)")
    so = p.add_mutually_exclusive_group()
    so.add_argument("--so-shopee", action="store_true", help="só a Shopee")
    so.add_argument("--so-ml", action="store_true", help="só o Mercado Livre")
    p.add_argument("--sem-pendencias", action="store_true", help="não roda a pendência")
    p.add_argument(
        "--max-produtos", type=int, default=1000, help="produtos do ML por conta (padrão 1000)"
    )
    args = p.parse_args(argv)
    if args.max_produtos < 1:
        p.error("--max-produtos precisa ser maior que zero")
    return args


async def _rodar(args: argparse.Namespace) -> int:
    if not args.gravar:
        print(json.dumps({"seco": True, **await avaliacoes.plano()}, ensure_ascii=False, indent=2))
        return 0
    if not get_settings().atendimento_leitura_ativa:
        print("ATENDIMENTO_LEITURA_ATIVA desligada: nada lido (o DaVinci não fala com as lojas).")
        return 2
    resumo: dict = {"seco": False}
    if not args.so_ml:
        resumo["shopee"] = await avaliacoes.sincronizar_todas_shopee(importar=True)
    if not args.so_shopee:
        resumo["ml"] = await avaliacoes.sincronizar_todas_ml(max_produtos=args.max_produtos)
    if not args.sem_pendencias:
        resumo["pendencias"] = await avaliacoes.atualizar_pendencias()
    print(json.dumps(resumo, ensure_ascii=False, indent=2, default=str))
    erros = sum(
        int(parte.get(chave) or 0)
        for parte in resumo.values()
        if isinstance(parte, dict)
        for chave in ("lojas_com_erro", "contas_com_erro", "erros")
    )
    return 1 if erros else 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_rodar(_argumentos(argv)))


if __name__ == "__main__":
    sys.exit(main())
