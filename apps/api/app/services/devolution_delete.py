"""Exclusão de um lançamento de devolução COM o estorno do estoque no Bling.

Miolo compartilhado pela rota DELETE /api/devolutions/{id} (lixeira da aba
Devoluções) e pela lixeira do chamado (POST /api/chamados/{id}/excluir), que
apaga várias linhas do mesmo pedido de uma vez — Vinicius, 21/09/2026: caso
294263, a operadora lançou "Não recebido" nos 2 itens e o pacote chegou; a
regra do estorno tem que ser UMA só, senão uma lixeira desfaz o estoque e a
outra deixa fantasma.
"""

from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Devolution
from app.services import devolucao_exclusao
from app.services import devolution_stock_return as dsr

logger = structlog.get_logger()


class EstoqueConferindoError(Exception):
    """A entrada no Bling ainda está sendo conferida (o Bling respondeu erro
    depois de receber): excluir agora deixaria uma entrada sem dono, que
    ninguém mais estornaria. Espera a conferência (até 10 min)."""


class EstornoSemConfirmacaoError(Exception):
    """O estorno anterior desta linha pode ter saído (o Bling respondeu erro
    depois de receber): excluir agora estornaria de novo. A pessoa confere o
    extrato primeiro ("Estorno já saiu" ou "Tentar de novo")."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class EstornoFalhouError(Exception):
    """O Bling recusou a baixa do estoque devolvido: a linha NÃO foi excluída."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


async def excluir_lancamento(
    session: AsyncSession, row: Devolution, *, actor_id: UUID | None = None
) -> dict:
    """Exclui o lançamento. Se ele já devolveu estoque ao Bling (movimento
    registrado em estoque_mov_* e ainda não estornado), dá BAIXA da mesma
    quantidade no Bling ANTES de excluir — Eduardo 03/09: "quando eu clicar em
    excluir você lança um estoque de saída e remove o estoque que foi lançado".
    Se o Bling recusar, NÃO exclui (rollback + `EstornoFalhouError`) — senão sobraria
    estoque fantasma. Lançamento antigo sem registro do movimento não tem como
    ser estornado daqui (o front avisa: ajustar direto no Bling).

    COMMITA a exclusão: o estorno já feito no Bling nunca pode ficar sem a
    exclusão correspondente (senão o operador exclui de novo e estorna 2×).

    Prejuízo automático (caso 300978, 26/10): linha Extraviado/Sucata/Manutenção
    leva junto, no mesmo commit, o Refund que o DaVinci criou para ELA — se
    ninguém mexeu nele; se mexeram, fica e `actor_id` recebe o aviso
    (services/devolucao_exclusao). Linha que passou por Manutenção leva também
    o débito do conserto. Vale para as duas lixeiras."""
    # Estado fresco e travado: um Tentar de novo/save da mesma linha no meio
    # mudaria o que foi decidido aqui (revisão de 15/10).
    await session.refresh(row, with_for_update=True)
    if row.estoque_status == dsr.ESTOQUE_CONFERINDO:
        raise EstoqueConferindoError
    if row.estoque_status == dsr.ESTOQUE_SEM_CONFIRMACAO and dsr.estorno_pendente(row):
        raise EstornoSemConfirmacaoError(
            row.estoque_erro or "O estorno anterior pode ter saído: confira o extrato antes."
        )
    estorno: dict | None = None
    if row.estoque_mov_bling_id and row.estoque_mov_revertido_at is None:
        rev = await dsr.reverse_stock_movement(session, row)
        if rev is not None and rev.get("action") == "reverse_incerto":
            # Pode ter saído: a linha fica "sem confirmação" e a próxima
            # exclusão espera a pessoa conferir (senão sairia baixa 2×).
            dsr.marcar_estorno_incerto(row, rev)
            row.estoque_conferir_action = dsr.ESTORNO_DA_EXCLUSAO
            await session.commit()
            raise EstornoFalhouError(
                f"{rev.get('message')} O lançamento NÃO foi excluído."
            )
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
    condicao = (row.condicao_produto or "").strip()
    # O prejuízo da condição atual e — se a linha passou por Manutenção e saiu
    # (Manutenção → Novo/Usado; Extraviado → Manutenção → Extraviado) — o débito
    # do conserto que ficou de propósito no PATCH: ele também é desta linha.
    tipos: list[tuple[str, bool]] = []
    if condicao in devolucao_exclusao.REFUND_CONDICOES:
        tipos.append((condicao, True))
    if row.manutencao and condicao != "Manutenção":
        tipos.append(("Manutenção", False))
    prejuizos: list[dict] = []
    for tipo, principal in tipos:
        # Savepoint: um erro aqui não pode desfazer o estorno já feito no
        # Bling — a linha sai mesmo assim e o prejuízo fica (com log).
        try:
            async with session.begin_nested():
                r = await devolucao_exclusao.desfazer_refund_auto(
                    session, row, tipo, actor_id, exclusao=True,
                    # Busca secundária: só o certo; na dúvida, quieto.
                    avisar_duvida=principal,
                )
        except Exception:  # noqa: BLE001
            logger.exception("devolution_delete_refund_falhou", id=str(dev_id), tipo=tipo)
            r = {"acao": "erro", "tipo": tipo, "motivo": "não consegui conferir"}
        if r is not None:
            prejuizos.append(r)
    await session.delete(row)
    await session.commit()
    estornado = bool(estorno and estorno.get("ok"))
    logger.info(
        "devolution_deleted",
        id=str(dev_id),
        pedido_bling=pedido_bling,
        estoque_estornado=estornado,
        prejuizos=[p.get("acao") for p in prejuizos],
    )
    return {
        "ok": True,
        "estoque_estornado": estornado,
        "mensagem": (estorno or {}).get("message"),
        # [{acao: apagado|mantido|erro, tipo, prejuizo, reembolso, valor, motivo}]
        "prejuizos": prejuizos,
    }
