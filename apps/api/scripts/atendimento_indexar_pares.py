"""Indexa as respostas da equipe como exemplos de TOM do agente (20/10/2026).

O mesmo que o cron de hora em hora faz (`agente.pares.indexar`): monta os
pares turno do cliente × turno da equipe das conversas, filtra (cara de
instrução, promessa que o validador barraria, turno curto, texto repetido em
5+ conversas, resposta da IA enviada sozinha), generaliza (`mascarar` +
`_generalizar`) e grava em `atendimento_pares` — e apaga o que passou dos 180
dias (LGPD). Com `--desde`, é o backfill único: lê as conversas com fala
desde a data, em lotes, com commit por lote (nada antes da retenção é
guardado, então o backfill começa, na prática, há 180 dias).

Uso (de dentro de apps/api, no servidor):
    uv run python -m scripts.atendimento_indexar_pares --dry-run                 # só conta
    uv run python -m scripts.atendimento_indexar_pares --desde 2024-10-09 --dry-run
    uv run python -m scripts.atendimento_indexar_pares --desde 2024-10-09        # backfill
    uv run python -m scripts.atendimento_indexar_pares                           # incremental

  --desde AAAA-MM-DD  lê as conversas com fala desde a data (o padrão é o
                      incremental: o último par menos a margem).
  --dry-run           lê e conta (pares, novos, descartados por motivo); não grava.
  --lote N            conversas por lote (padrão 500; um commit por lote).
  --expurgar-dado-pessoal  só apaga os pares já gravados que a régua de dado
                      pessoal de hoje tiraria (revisão 2 de 20/10/2026: a de
                      antes exigia maiúscula) — rodar uma vez depois do deploy;
                      com --dry-run, só conta.

A saída é o resumo em JSON: só contagens (nada de texto de cliente).
"""

# ruff: noqa: T201

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from typing import Any

import app.db as _db
from app.services.atendimento.agente import pares

# Teto de lotes por execução (o backfill de produção cabe com folga: ~12 mil
# conversas em out/2026, 500 por lote).
MAX_LOTES = 200


def _data(texto: str) -> datetime:
    try:
        return datetime.strptime(texto, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"data inválida: {texto!r} (use AAAA-MM-DD)") from e


def _somar(total: dict[str, int], parte: dict[str, int]) -> None:
    for chave, valor in parte.items():
        if chave in ("ate_ts", "mais"):
            continue
        total[chave] = total.get(chave, 0) + int(valor)


async def rodar(
    *, desde: datetime | None, gravar: bool, lote: int = 500, max_lotes: int = MAX_LOTES
) -> dict[str, Any]:
    """Indexa em lotes (commit por lote) a partir de `desde` — ou o incremental (um lote).

    Sem `desde`, é UMA rodada do cron. Com `desde`, segue de onde o lote
    anterior parou (`ate_ts`) enquanto o lote vier cheio. `gravar=False`: lê
    e conta; nada é gravado (rollback em cada lote).
    """
    total: dict[str, int] = {}
    lotes = 0
    proximo = desde
    while True:
        async with _db.session_scope() as session:
            resumo = await pares.indexar(
                session, desde=proximo, limite_conversas=lote, gravar=gravar
            )
            if not gravar:
                await session.rollback()
        lotes += 1
        _somar(total, resumo)
        if desde is None or not resumo.get("mais") or lotes >= max_lotes:
            break
        seguinte = datetime.fromtimestamp(int(resumo.get("ate_ts") or 0), tz=UTC)
        if proximo is not None and seguinte <= proximo:
            # O lote inteiro tem o mesmo instante: não há como andar sem repetir.
            break
        proximo = seguinte
    return {
        "gravar": gravar,
        "desde": desde.date().isoformat() if desde else None,
        "lotes": lotes,
        **total,
    }


async def expurgar(*, gravar: bool) -> dict[str, Any]:
    """Apaga (ou só conta) os pares gravados com dado pessoal (`pares.expurgar_dado_pessoal`)."""
    async with _db.session_scope() as session:
        resumo = await pares.expurgar_dado_pessoal(session, gravar=gravar)
        if not gravar:
            await session.rollback()
    return {"gravar": gravar, **resumo}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--desde", type=_data, help="AAAA-MM-DD (o backfill: 2024-10-09)")
    parser.add_argument("--dry-run", action="store_true", help="só lê e conta; não grava")
    parser.add_argument("--lote", type=int, default=500, help="conversas por lote")
    parser.add_argument(
        "--expurgar-dado-pessoal",
        action="store_true",
        help="só apaga os pares gravados com dado pessoal",
    )
    args = parser.parse_args(argv)
    if args.expurgar_dado_pessoal:
        print(json.dumps(asyncio.run(expurgar(gravar=not args.dry_run)), indent=2))
        return 0
    resumo = asyncio.run(rodar(desde=args.desde, gravar=not args.dry_run, lote=max(1, args.lote)))
    print(json.dumps(resumo, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
