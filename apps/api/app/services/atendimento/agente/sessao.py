"""A sessão de banco de cada ferramenta do agente (spec 3.3.1, regra 8; 20/10/2026).

Cada ferramenta abre a SUA sessão, curta e só de leitura:

- `SET TRANSACTION READ ONLY`: um INSERT/UPDATE/DELETE por engano (ou um
  `flush` de objeto sujo) falha na hora com "cannot execute ... in a
  read-only transaction" — a ferramenta transforma em `ToolError`;
- `statement_timeout` de 5 s (`SET LOCAL`, só nesta transação): consulta
  lenta não segura o laço de 45 s;
- sai sempre com ROLLBACK — nada a gravar — e fecha a conexão.

Por que não a sessão do `_gerar`: uma exceção no meio de uma consulta deixa a
transação abortada e o `_salvar` do rascunho falharia; e o `_gerar` faz
commit ANTES do laço para não ficar "idle in transaction" por até 45 s (o
Postgres de produção não tem `idle_in_transaction_session_timeout`).

Cuidados de quem usa:

- converta para dicionário DENTRO do `async with`: o rollback da saída
  expira os objetos do ORM (ler atributo depois vai ao banco de novo);
- o token da loja renovado no meio da ida à loja (`ficha_loja`) é gravado
  pela sessão PRÓPRIA de `clientes.gravar_credenciais` — esta só lê;
- `_db.SessionLocal` é lido na hora da chamada (os testes trocam o engine).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import app.db as _db

STATEMENT_TIMEOUT_MS = 5_000


@asynccontextmanager
async def sessao_leitura(*, timeout_ms: int = STATEMENT_TIMEOUT_MS) -> AsyncIterator[AsyncSession]:
    """Uma sessão própria, só de leitura, com `statement_timeout`; sai com rollback."""
    async with _db.SessionLocal() as session:
        try:
            # O 1º comando da transação (o SET TRANSACTION tem de vir antes de
            # qualquer consulta).
            await session.execute(text("SET TRANSACTION READ ONLY"))
            await session.execute(
                text("SELECT set_config('statement_timeout', :ms, true)"),
                {"ms": str(max(1, int(timeout_ms)))},
            )
            yield session
        finally:
            await session.rollback()
