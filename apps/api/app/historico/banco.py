"""Liga o pedido da pessoa ao gatilho do banco, e cuida da manutenção.

`after_begin` roda a cada transação nova de QUALQUER sessão (API e workers
usam a mesma fábrica). Sem Ator de pessoa em pedido de escrita não faz nada —
é o caso de todos os robôs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import delete, event, text
from sqlalchemy.orm import Session

from app.historico import sql as hsql
from app.historico.contexto import MARCAR_TRANSACAO, ator_atual, deve_marcar

logger = structlog.get_logger()

GUARDAR_DIAS = 365


def _ao_comecar_transacao(session, transaction, connection) -> None:
    a = ator_atual()
    if not deve_marcar(a):
        return
    connection.execute(MARCAR_TRANSACAO, {"ator": str(a.user_id), "req": str(a.req_id)})


def ligar() -> None:
    if not event.contains(Session, "after_begin", _ao_comecar_transacao):
        event.listen(Session, "after_begin", _ao_comecar_transacao)


async def garantir_gatilhos(conn, schema: str) -> list[str]:
    """Põe o gatilho em tabela que ainda não tem (criada depois da 0329, ou
    que estava ocupada no deploy). Uma tabela por vez, com lock curto.
    `conn` é uma AsyncConnection em autocommit. Devolve as tabelas cobertas."""
    await conn.execute(text("SET lock_timeout = '3s'"))
    nomes = [
        r[0]
        for r in await conn.execute(
            text(hsql.TABELAS_SEM_GATILHO), {"schema": schema, "gatilho": hsql.NOME_GATILHO}
        )
    ]
    cobertas = []
    for tabela in hsql.a_cobrir(nomes):
        try:
            await conn.execute(text(hsql.criar_gatilho(schema, tabela)))
            cobertas.append(tabela)
        except Exception as e:  # noqa: BLE001 - ocupada agora, tenta amanhã
            logger.warning("historico_gatilho_adiado", tabela=tabela, erro=type(e).__name__)
    await conn.execute(text("RESET lock_timeout"))
    return cobertas


async def limpar_antigos(session) -> tuple[int, int]:
    """Apaga o que passou de GUARDAR_DIAS. Devolve (eventos, alterações)."""
    from app.models import HistoricoAlteracao, HistoricoEvento

    limite = datetime.now(UTC) - timedelta(days=GUARDAR_DIAS)
    ev = await session.execute(delete(HistoricoEvento).where(HistoricoEvento.criado_em < limite))
    al = await session.execute(
        delete(HistoricoAlteracao).where(HistoricoAlteracao.criado_em < limite)
    )
    return ev.rowcount or 0, al.rowcount or 0
