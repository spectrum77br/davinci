"""Importação do histórico da caixa `/atendimento` — UMA VEZ, depois da aprovação do Eduardo.

Traz os últimos N dias de conversa (Shopee chat, ML perguntas respondidas e ML
pós-venda) e monta o índice de pedidos e avaliações da Shopee do cartão
"Cliente". Sem IA, sem alerta, nunca marca como lido, retomável (rodar de novo
continua de onde parou). Detalhes e travas em `app/services/atendimento/importar.py`.

Uso (de dentro de apps/api, no servidor, com a leitura do atendimento ligada):
    uv run python -m scripts.atendimento_importar_historico --dias 90 --seco
    uv run python -m scripts.atendimento_importar_historico --dias 90
    uv run python -m scripts.atendimento_importar_historico --dias 90 --loja marquezini

  --seco        só conta (lê as listas, não grava nada) — rode ANTES, para ver o tamanho.
  --loja X      só a loja com X no nome (ou o id da integração).
  --forcar      roda mesmo com ATENDIMENTO_LEITURA_ATIVA desligado.
  --por-minuto  teto de chamadas às lojas por minuto (padrão 60).

A saída é o resumo em JSON: por loja e etapa, só contagens (nada de comprador).
"""

# ruff: noqa: T201

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.services.atendimento import importar


def _argumentos(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="atendimento_importar_historico",
        description="Importa o histórico do atendimento (uma vez).",
    )
    p.add_argument("--dias", type=int, default=importar.DIAS_PADRAO, help="janela, em dias")
    p.add_argument("--loja", default=None, help="só a loja com isto no nome (ou o id)")
    p.add_argument("--seco", action="store_true", help="só conta; não grava nada")
    p.add_argument(
        "--forcar", action="store_true", help="roda mesmo com a leitura do atendimento desligada"
    )
    p.add_argument(
        "--por-minuto",
        type=int,
        default=importar.CHAMADAS_POR_MINUTO,
        help="teto de chamadas às lojas por minuto",
    )
    return p.parse_args(argv)


async def _rodar(args: argparse.Namespace) -> int:
    try:
        resumo = await importar.importar_historico(
            dias=args.dias,
            loja=args.loja,
            seco=args.seco,
            forcar=args.forcar,
            por_minuto=args.por_minuto,
        )
    except importar.ImportacaoRecusada as e:
        print(f"RECUSADO ({e.code}): {e.detail}", file=sys.stderr)
        return 2
    print(json.dumps(resumo, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_rodar(_argumentos(argv)))


if __name__ == "__main__":
    sys.exit(main())
