"""Põe o gatilho do Histórico nas tabelas que ainda não têm.

    python -m app.historico.instalar

Roda no deploy, logo depois da migration 0330, e o worker repete ao subir e
todo dia (`historico_manutencao`): cobre tabela criada depois e tabela que
estava ocupada. Cada tabela na sua transação curta, com lock_timeout de 3 s —
se um robô está segurando a tabela, ela fica para a próxima rodada em vez de
travar a fila.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.historico import sql as hsql
from app.historico.banco import garantir_gatilhos


async def instalar(engine, schema: str) -> tuple[list[str], list[str]]:
    """Devolve (cobertas agora, ainda faltando)."""
    async with engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        cobertas = await garantir_gatilhos(conn, schema)
        faltando = hsql.a_cobrir(
            [
                r[0]
                for r in await conn.execute(
                    text(hsql.TABELAS_SEM_GATILHO),
                    {"schema": schema, "gatilho": hsql.NOME_GATILHO},
                )
            ]
        )
    return cobertas, faltando


async def _main() -> None:
    from app.config import get_settings
    from app.db import engine

    cobertas, faltando = await instalar(engine, get_settings().database_schema)
    print(f"historico: gatilho posto em {len(cobertas)} tabela(s) agora")  # noqa: T201
    if faltando:
        print(f"historico: ficaram para o worker: {', '.join(faltando)}")  # noqa: T201
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
