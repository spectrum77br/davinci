"""Gravação da trilha de auditoria de ações na página Margem.

Ponto único chamado pelos fluxos do app que mudam pedidos via Margem (e
relacionados): mudança de situação no Bling, edição do Saldo Final (valor_base)
e da Observação. Best-effort: nunca levanta nem faz commit (o caller é dono da
transação)."""

from __future__ import annotations

from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MargemAudit

logger = structlog.get_logger()


async def record_margem_audit(
    session: AsyncSession,
    *,
    acao: str,
    pedido_bling: str,
    bling_id: str | int | None = None,
    sku: str | None = None,
    valor_antigo: str | int | float | None,
    valor_novo: str | int | float | None,
    origem: str,
    mudado_por: UUID | None,
) -> None:
    """Adiciona uma linha de auditoria à sessão (commit fica com o caller).

    `acao`: 'situacao' | 'saldo_final' | 'observacao'.
    Só registra quando houve mudança real (antigo != novo). `mudado_por=None`
    significa ação disparada pelo sistema (job automático).
    """
    antigo = None if valor_antigo is None else str(valor_antigo)
    novo = None if valor_novo is None else str(valor_novo)
    if antigo == novo:
        return
    try:
        session.add(
            MargemAudit(
                acao=acao,
                pedido_bling=str(pedido_bling),
                bling_id=None if bling_id is None else str(bling_id),
                sku=sku,
                valor_antigo=antigo,
                valor_novo=novo,
                origem=origem,
                mudado_por=mudado_por,
            )
        )
    except Exception as exc:  # noqa: BLE001 — auditoria nunca quebra o fluxo
        logger.warning(
            "margem_audit_record_failed",
            acao=acao,
            pedido_bling=str(pedido_bling),
            origem=origem,
            error=str(exc),
        )
