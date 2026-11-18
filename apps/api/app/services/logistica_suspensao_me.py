"""Suspender a entrega do Envio próprio direto na API do Melhor Envio (18/11/2026).

Vinicius: "estou tentando suspender entrega e está dando falhou". O robô de tela do
Mac mini abria o menu de ações mas não via "Suspender entrega" (309193 duas vezes
em 18/11, 298281 em 12/11), com a opção aparecendo no site. A tela do ME faz a
suspensão por `POST /api/v2/me/orders/{envio}/suspend` (ver `melhor_envio`), com o
mesmo token que já lê e responde os atendimentos — então o DaVinci pede direto e
mostra o resultado na hora, sem fila nem robô.

- Acha o envio no ME pelo rastreio (a mesma busca do "Abrir chamado").
- Motivo: "Meu cliente desistiu da compra" (o mesmo que o robô usava).
- Aceito → `solicitada`; recusado ou envio não achado → `falhou`, com a mensagem do
  ME no detalhe (a coluna Suspensão e o Raio-X mostram).
- Sem o token do ME no DaVinci → cai na fila do robô, como antes.

As travas de quem pode pedir continuam em `logistica_robo.pode_suspender`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Logistica
from app.services import chamados_melhor_envio_abrir as abrir
from app.services import logistica_robo
from app.services import melhor_envio as me

logger = structlog.get_logger()


def _detalhe(**kw) -> str:
    return json.dumps({"via": "api", **kw}, ensure_ascii=False)[:2000]


async def suspender(
    session: AsyncSession,
    row: Logistica,
    *,
    user_id: UUID | None,
    motivo: str = me.MOTIVO_SUSPENSAO_PADRAO,
) -> str:
    """Pede a suspensão e grava o resultado na linha (commita). Devolve o status
    novo: `solicitada`, `falhou` ou `pendente` (sem token: foi pro robô)."""
    codigo = logistica_robo.pode_suspender(row)
    if codigo:
        raise logistica_robo.RoboError(codigo)
    token, _ = await me.token_do_painel(session)
    if not token:
        await logistica_robo.solicitar_suspensao(session, row, user_id=user_id)
        return logistica_robo.STATUS_PENDENTE

    nome = me.MOTIVOS_SUSPENSAO.get(motivo, motivo)
    row.suspensao_em = datetime.now(UTC)
    try:
        envio = await abrir._envio_da_linha(token, row)
    except abrir.AberturaRecusadaError as e:
        row.suspensao_status = logistica_robo.STATUS_FALHOU
        row.suspensao_detalhe = _detalhe(ok=False, found=False, reason=e.mensagem)
        await session.commit()
        logger.warning("suspensao_me_envio_nao_achado", pedido=row.pedido_bling, code=e.code)
        return row.suspensao_status

    http, corpo = await me.suspender_envio(token, envio.id, motivo=motivo)
    if http // 100 == 2:
        row.suspensao_status = logistica_robo.STATUS_SOLICITADA
        row.suspensao_detalhe = _detalhe(
            ok=True,
            found=True,
            requested=True,
            envio=envio.id,
            suspended_at=corpo.get("suspended_at"),
            reason=f"suspensão pedida ao Melhor Envio pela API (motivo: {nome})",
        )
    else:
        msg = str(corpo.get("message") or corpo.get("error") or corpo.get("texto") or "")
        erros = corpo.get("errors")
        if isinstance(erros, dict) and erros:
            msg = "; ".join(
                f"{k}: {' '.join(map(str, v)) if isinstance(v, list) else v}"
                for k, v in erros.items()
            )
        row.suspensao_status = logistica_robo.STATUS_FALHOU
        row.suspensao_detalhe = _detalhe(
            ok=False,
            found=True,
            requested=False,
            http=http,
            envio=envio.id,
            reason=f"o Melhor Envio recusou a suspensão: {msg[:300] or f'HTTP {http}'}",
        )
    await session.commit()
    logger.info(
        "suspensao_me_api",
        pedido=row.pedido_bling,
        rastreio=row.rastreio,
        status=row.suspensao_status,
        http=http,
    )
    return row.suspensao_status
