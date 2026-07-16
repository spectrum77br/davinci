"""Rotas do 17track — localização física REAL dos Correios (`...BR`) na Logística.

Dois endpoints, prefixos diferentes:

  * `POST /api/webhooks/17track/{secret}` — PÚBLICO. O 17track empurra (push) o
    evento novo quando o pacote se move. O 17track NÃO assina o push (sem HMAC
    documentado), então o guard é um segmento secreto no path
    (`logi_17track_webhook_secret`); path errado => 404 (não vaza que existe).
    Dedupe no Redis pelo hash do corpo. Atualiza `logistica.localizacao` das
    linhas cujo `rastreio` casa com o número do push.

  * `POST /api/logistica/17track/register` — gated por `logistica.edit`.
    Registra no 17track os `...BR` em trânsito (idempotente do lado deles). A
    partir daí o 17track busca nos Correios e passa a empurrar atualizações.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_permission
from app.models import Logistica, User
from app.redis_client import redis
from app.services import logistica_rules, logistica_track

logger = structlog.get_logger()
router = APIRouter(tags=["logistica_track"])

_DEDUPE_TTL = 86_400


async def _claim_push(payload_hash: str) -> bool:
    """True na 1ª vez que vemos este push; False se duplicado (Redis SET NX)."""
    return bool(
        await redis.set(f"17track:push:{payload_hash}", "1", nx=True, ex=_DEDUPE_TTL)
    )


@router.post("/api/webhooks/17track/{secret}")
async def receive_17track_push(
    secret: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    expected = (get_settings().logi_17track_webhook_secret or "").strip()
    if not expected or not hmac.compare_digest(secret, expected):
        # Segredo errado/ausente => 404 (não confirma a existência da rota).
        return {"ack": False}

    body = await request.body()
    payload_hash = hashlib.sha256(body or b"").hexdigest()
    if not await _claim_push(payload_hash):
        return {"ack": True, "duplicate": True}

    try:
        parsed = json.loads(body or b"{}")
        if not isinstance(parsed, dict):
            parsed = {}
    except json.JSONDecodeError:
        return {"ack": True, "ignored": "invalid_json"}

    # Loga o corpo cru nas 1ªs observações — o formato do push do 17track (v2.2
    # vs v2.4) não é 100% documentado; sem isso não dá pra ajustar o parser.
    logger.info(
        "logistica_17track_push", push_event=parsed.get("event"), body_len=len(body)
    )

    updates = logistica_track.parse_push(parsed)
    applied = 0
    for number, loc in updates:
        rows = (
            await session.execute(
                select(Logistica).where(Logistica.rastreio == number)
            )
        ).scalars().all()
        for row in rows:
            row.localizacao = loc
            # Recalcula a divergência ML × físico com o local novo dos Correios.
            row.divergencia = logistica_rules.detectar_divergencia(row.meli_status, loc)
            applied += 1
    await session.commit()

    logger.info("logistica_17track_push_applied", numbers=len(updates), rows=applied)
    return {"ack": True, "numbers": len(updates), "rows": applied}


@router.post("/api/logistica/17track/register")
async def register_correios(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> dict[str, Any]:
    """Registra no 17track os rastreios Correios (`...BR`) das linhas de
    Logística que ainda não têm localização física real. Idempotente do lado do
    17track — repetir número não gasta quota extra."""
    rows = (
        await session.execute(
            select(Logistica.rastreio).where(Logistica.rastreio.isnot(None))
        )
    ).scalars().all()
    numbers = sorted(
        {r.strip() for r in rows if logistica_track.is_correios(r)}
    )
    if not numbers:
        return {"registered": 0, "numbers": 0}

    body = await logistica_track.register(numbers)
    logger.info("logistica_17track_register_batch", numbers=len(numbers))
    return {"registered": len(numbers), "numbers": len(numbers), "raw": body}
