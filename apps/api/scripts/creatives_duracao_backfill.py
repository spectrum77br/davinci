"""Mede a duração e a resolução dos vídeos de criativo que ainda não têm (migration 0393).

Do deploy da 0393 em diante, o envio (portal das agências e aba Criativos)
já carimba `duracao_s`, `largura` e `altura` pelo ffprobe. Os vídeos que
chegaram ANTES ficam NULL até este script — e o relatório do portal os conta
em `sem_duracao` enquanto isso.

Só toca linha de VÍDEO com `duracao_s` NULL cujo arquivo EXISTE no disco
(`uploads_dir`). Idempotente: rodar de novo só olha o que continuou NULL — o
arquivo que o ffprobe não lê fica NULL e é tentado de novo na próxima vez,
sem estrago. Nada sai do servidor: lê o arquivo local e grava no banco do
DaVinci, um lote por transação.

Uso (de dentro de apps/api, no servidor, DEPOIS do `alembic upgrade head`):
    uv run python -m scripts.creatives_duracao_backfill --dry-run   # só conta
    uv run python -m scripts.creatives_duracao_backfill
    uv run python -m scripts.creatives_duracao_backfill --limite 50

  --dry-run   só conta (candidatos, com arquivo, sem arquivo); não roda o
              ffprobe nem grava nada.
  --lote N    arquivos por transação (padrão 50).
  --limite N  para depois de N arquivos medidos ou tentados.

A saída é o resumo em JSON: só contagens.
"""

# ruff: noqa: T201

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from sqlalchemy import select

from app import db
from app.models.marketing import MarketingCreativeFile
from app.services.marketing.anexos import caminho_confinado
from app.services.marketing.medidas_video import eh_video_sql, medir


def _argumentos(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="creatives_duracao_backfill",
        description="Mede duração/resolução dos vídeos de criativo sem medida (0393).",
    )
    p.add_argument("--dry-run", action="store_true", help="só conta; não mede nem grava")
    p.add_argument("--lote", type=int, default=50, help="arquivos por transação (padrão 50)")
    p.add_argument("--limite", type=int, default=None, help="para depois de N arquivos")
    args = p.parse_args(argv)
    if args.lote < 1 or (args.limite is not None and args.limite < 1):
        p.error("--lote e --limite precisam ser maiores que zero")
    return args


def _no_disco(rec: MarketingCreativeFile) -> bool:
    caminho = caminho_confinado(rec.file_rel)
    return caminho is not None and caminho.is_file()


async def rodar(*, seco: bool, lote: int = 50, limite: int | None = None) -> dict[str, Any]:
    """O trabalho, separado do argparse para o teste chamar direto."""
    resumo: dict[str, Any] = {
        "dry_run": seco,
        "candidatos": 0,
        "com_arquivo": 0,
        "sem_arquivo": 0,
        "medidos": 0,
        "nao_lidos": 0,
    }
    # `db.SessionLocal` lido na hora (e não importado por nome): é o mesmo
    # truque do `session_scope`, que deixa o teste trocar o engine.
    async with db.SessionLocal() as session:
        # Só os ids primeiro: a lista inteira de linhas presas à sessão por
        # minutos (o ffprobe leva até 20 s por arquivo) seria memória à toa.
        ids = (
            (
                await session.execute(
                    select(MarketingCreativeFile.id)
                    .where(MarketingCreativeFile.duracao_s.is_(None), eh_video_sql())
                    .order_by(MarketingCreativeFile.created_at, MarketingCreativeFile.id)
                )
            )
            .scalars()
            .all()
        )
    resumo["candidatos"] = len(ids)

    tentados = 0
    for inicio in range(0, len(ids), lote):
        if limite is not None and tentados >= limite:
            break
        pedaco = ids[inicio : inicio + lote]
        async with db.SessionLocal() as session:
            recs = (
                (
                    await session.execute(
                        select(MarketingCreativeFile)
                        .where(
                            MarketingCreativeFile.id.in_(pedaco),
                            # De novo aqui: um envio no meio do caminho já pode
                            # ter medido a linha — não mede duas vezes.
                            MarketingCreativeFile.duracao_s.is_(None),
                        )
                        .order_by(MarketingCreativeFile.created_at, MarketingCreativeFile.id)
                    )
                )
                .scalars()
                .all()
            )
            for rec in recs:
                if limite is not None and tentados >= limite:
                    break
                if not _no_disco(rec):
                    resumo["sem_arquivo"] += 1
                    continue
                resumo["com_arquivo"] += 1
                if seco:
                    continue
                tentados += 1
                if await medir(rec):
                    resumo["medidos"] += 1
                else:
                    resumo["nao_lidos"] += 1
            if not seco:
                await session.commit()
    return resumo


async def _rodar(args: argparse.Namespace) -> int:
    resumo = await rodar(seco=args.dry_run, lote=args.lote, limite=args.limite)
    print(json.dumps(resumo, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_rodar(_argumentos(argv)))


if __name__ == "__main__":
    sys.exit(main())
