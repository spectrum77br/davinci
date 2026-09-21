"""Exclusão de um lançamento de devolução COM o estorno do estoque no Bling.

Miolo compartilhado pela rota DELETE /api/devolutions/{id} (lixeira da aba
Devoluções) e pela lixeira do chamado (POST /api/chamados/{id}/excluir), que
apaga várias linhas do mesmo pedido de uma vez — Vinicius, 21/09/2026: caso
294263, a operadora lançou "Não recebido" nos 2 itens e o pacote chegou; a
regra do estorno tem que ser UMA só, senão uma lixeira desfaz o estoque e a
outra deixa fantasma.
"""

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Devolution
from app.services import devolution_stock_return as dsr

logger = structlog.get_logger()


class EstornoFalhouError(Exception):
    """O Bling recusou a baixa do estoque devolvido: a linha NÃO foi excluída."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


async def excluir_lancamento(session: AsyncSession, row: Devolution) -> dict:
    """Exclui o lançamento. Se ele já devolveu estoque ao Bling (movimento
    registrado em estoque_mov_* e ainda não estornado), dá BAIXA da mesma
    quantidade no Bling ANTES de excluir — Eduardo 03/09: "quando eu clicar em
    excluir você lança um estoque de saída e remove o estoque que foi lançado".
    Se o Bling recusar, NÃO exclui (rollback + `EstornoFalhouError`) — senão sobraria
    estoque fantasma. Lançamento antigo sem registro do movimento não tem como
    ser estornado daqui (o front avisa: ajustar direto no Bling).

    COMMITA a exclusão: o estorno já feito no Bling nunca pode ficar sem a
    exclusão correspondente (senão o operador exclui de novo e estorna 2×)."""
    estorno: dict | None = None
    if row.estoque_mov_bling_id and row.estoque_mov_revertido_at is None:
        rev = await dsr.reverse_stock_movement(session, row)
        if rev is not None and not rev.get("ok"):
            await session.rollback()
            raise EstornoFalhouError(
                (
                    "Não consegui estornar o estoque devolvido no Bling — o "
                    f"lançamento NÃO foi excluído. {rev.get('message') or ''}"
                ).strip()
            )
        estorno = rev

    dev_id, pedido_bling = row.id, row.pedido_bling
    await session.delete(row)
    await session.commit()
    estornado = bool(estorno and estorno.get("ok"))
    logger.info(
        "devolution_deleted",
        id=str(dev_id),
        pedido_bling=pedido_bling,
        estoque_estornado=estornado,
    )
    return {
        "ok": True,
        "estoque_estornado": estornado,
        "mensagem": (estorno or {}).get("message"),
    }
