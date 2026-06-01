"""Gravação da trilha de auditoria de mudanças de situação no Bling.

Ponto único chamado pelos 3 fluxos do app que mudam a situação de um pedido
no Bling (margens, devolução, job de envio). Best-effort: nunca levanta nem
faz commit (o caller é dono da transação)."""

from __future__ import annotations

from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingSituacaoAudit

logger = structlog.get_logger()


async def record_situacao_change(
    session: AsyncSession,
    *,
    pedido_bling: str,
    bling_id: str | int | None,
    situacao_antiga: str | int | None,
    situacao_nova: str | int,
    origem: str,
    mudado_por: UUID | None,
    sku: str | None = None,
) -> None:
    """Adiciona uma linha de auditoria à sessão (commit fica com o caller).

    Só registra quando houve mudança real (antiga != nova). `mudado_por=None`
    significa mudança disparada pelo sistema (job automático).
    """
    antiga = None if situacao_antiga is None else str(situacao_antiga)
    nova = str(situacao_nova)
    if antiga == nova:
        return
    try:
        session.add(
            BlingSituacaoAudit(
                pedido_bling=str(pedido_bling),
                bling_id=None if bling_id is None else str(bling_id),
                sku=sku,
                situacao_antiga=antiga,
                situacao_nova=nova,
                origem=origem,
                mudado_por=mudado_por,
            )
        )
    except Exception as exc:  # noqa: BLE001 — auditoria nunca quebra o fluxo
        logger.warning(
            "situacao_audit_record_failed",
            pedido_bling=str(pedido_bling),
            origem=origem,
            error=str(exc),
        )
