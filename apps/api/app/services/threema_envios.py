"""Cadastro dos envios do Threema (Cadastros › Envios Threema, tarefa 16).

O CÓDIGO DESCREVE cada envio (`services/threema_catalogo`); o BANCO decide quem
recebe, se está ligado e a observação (tabelas da migration 0505).

Nesta fase (C3) só existe a SINCRONIZAÇÃO do catálogo: o upsert das colunas
do código em `threema_envio`. Ela roda no startup do worker (ao lado do
catálogo da Ouvidoria) e pela linha de comando:

    python -m app.services.threema_envios sincronizar

A sincronização NUNCA semeia (não cria quem recebe, não carimba
`semeado_em`) e nunca toca nas colunas da pessoa (`ativo`, observação):
sem semente, todo envio continua na configuração de hoje.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

import structlog
from sqlalchemy import func, literal_column, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ThreemaEnvio
from app.services.threema_catalogo import ENVIOS

logger = structlog.get_logger()

# As colunas que o CÓDIGO escreve (o resto é da pessoa ou de controle).
COLUNAS_DO_CODIGO = (
    "painel",
    "nome",
    "descricao",
    "frequencia_tipo",
    "frequencia_texto",
    "onde_roda",
)


def _linhas_do_catalogo() -> list[dict]:
    return [
        {
            "chave": d.chave,
            "painel": d.painel,
            "nome": d.nome,
            "descricao": d.descricao,
            "frequencia_tipo": d.frequencia_tipo,
            "frequencia_texto": d.frequencia_texto,
            "onde_roda": d.onde_roda,
        }
        for d in ENVIOS
    ]


async def sincronizar(session: AsyncSession) -> dict[str, int]:
    """Upsert do catálogo em `threema_envio`. Só reescreve as colunas do
    código, e só nas linhas em que algo mudou (o gatilho do Histórico e o
    `updated_at` não acusam mudança que não houve). Chave que saiu do código
    ganha `descontinuado_em` (nunca é apagada: o registro e o Histórico a
    citam); chave que voltou perde o carimbo. Flush, sem commit.

    Devolve {"novos", "atualizados", "descontinuados"}."""
    tabela = ThreemaEnvio.__table__
    linhas = _linhas_do_catalogo()
    stmt = pg_insert(tabela).values(linhas)
    exc = stmt.excluded
    mudou = or_(
        tuple_(*(tabela.c[c] for c in COLUNAS_DO_CODIGO)).is_distinct_from(
            tuple_(*(exc[c] for c in COLUNAS_DO_CODIGO))
        ),
        tabela.c.descontinuado_em.is_not(None),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[tabela.c.chave],
        set_={
            **{c: exc[c] for c in COLUNAS_DO_CODIGO},
            "descontinuado_em": None,
            "updated_at": func.now(),
        },
        where=mudou,
    ).returning(
        tabela.c.chave,
        # xmax = 0 só na linha que o INSERT criou (a do UPDATE tem xmax).
        literal_column("(xmax = 0)").label("novo"),
    )
    escritas = (await session.execute(stmt)).all()
    novos = sum(1 for r in escritas if r.novo)
    codigo = [linha["chave"] for linha in linhas]
    sumidas = await session.execute(
        update(tabela)
        .where(tabela.c.chave.not_in(codigo), tabela.c.descontinuado_em.is_(None))
        .values(descontinuado_em=func.now(), updated_at=func.now())
        .returning(tabela.c.chave)
    )
    descontinuados = [r[0] for r in sumidas.all()]
    await session.flush()
    resumo = {
        "novos": novos,
        "atualizados": len(escritas) - novos,
        "descontinuados": len(descontinuados),
    }
    if novos or resumo["atualizados"] or descontinuados:
        logger.info("threema_envios_sincronizado", **resumo, sairam=descontinuados[:20])
    return resumo


async def chaves_no_banco(session: AsyncSession) -> list[str]:
    return list((await session.execute(select(ThreemaEnvio.chave))).scalars().all())


# ─────────────────────────────────────────────────────────── linha de comando


async def _cmd_sincronizar(_args: argparse.Namespace) -> int:
    from app.db import session_scope

    async with session_scope() as session:
        resumo = await sincronizar(session)
    total = len(ENVIOS)
    print(  # noqa: T201
        f"threema_envios: catálogo com {total} envios — {resumo['novos']} novo(s), "
        f"{resumo['atualizados']} atualizado(s), {resumo['descontinuados']} descontinuado(s)"
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m app.services.threema_envios",
        description="Cadastro dos envios do Threema (Cadastros › Envios Threema).",
    )
    sub = p.add_subparsers(dest="comando", required=True)
    sub.add_parser("sincronizar", help="upsert do catálogo do código em threema_envio")
    return p


_COMANDOS = {"sincronizar": _cmd_sincronizar}


async def _main(argv: Sequence[str]) -> int:
    from app.db import engine

    args = _parser().parse_args(list(argv))
    try:
        return await _COMANDOS[args.comando](args)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))
