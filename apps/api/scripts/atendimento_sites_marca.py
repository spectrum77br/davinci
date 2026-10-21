"""Lê as páginas de PRODUTO dos sites da marca e grava o instantâneo (20/10/2026).

O mesmo que o cron de madrugada faz (`sites_marca.atualizar_sites`): GET nas
páginas de produto de uranyx.com.br (lista pelo sitemap e pelo llms.txt) e
charlots.com.br (lista pela página /produtos), ~1 pedido por segundo, User-
Agent próprio, sem cookie, sem formulário — e grava em
`atendimento_site_produtos` só o que mudou (o `hash`). Roda mesmo com
`atendimento_sites_marca_ativo` desligado (é um pedido manual).

Uso (de dentro de apps/api, no servidor):
    uv run python -m scripts.atendimento_sites_marca --dry-run     # só lê e conta
    uv run python -m scripts.atendimento_sites_marca               # lê e grava
    uv run python -m scripts.atendimento_sites_marca --marca uranyx

  --dry-run     lê as páginas e conta (lista, lidas, não produto, falhas); não grava.
  --marca M     só um site (uranyx | charlots); o padrão são os dois.

A saída é o resumo em JSON: só contagens. Cerca de 1 minuto (60 páginas).
"""

# ruff: noqa: T201

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

import app.db as _db
from app.services.atendimento import sites_marca
from app.services.atendimento.constantes import SITES


async def rodar(*, gravar: bool, marcas: tuple[str, ...]) -> dict[str, Any]:
    async with _db.session_scope() as session:
        resumo = await sites_marca.atualizar_sites(
            session, marcas=marcas, gravar=gravar, forcar=True
        )
        if not gravar:
            await session.rollback()
    return {"gravar": gravar, **resumo}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="só lê e conta; não grava")
    parser.add_argument("--marca", choices=SITES, help="só um site")
    args = parser.parse_args(argv)
    marcas = (args.marca,) if args.marca else SITES
    resumo = asyncio.run(rodar(gravar=not args.dry_run, marcas=marcas))
    print(json.dumps(resumo, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
