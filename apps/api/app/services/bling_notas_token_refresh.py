"""Refresh de tokens das contas Bling de emissão de NF (`bling_notas`).

São apps OAuth distintos da integração principal (ver modelo BlingNota) —
as credenciais ficam em colunas próprias (sem cipher) e o refresh usa o
header `Authorization: Basic` armazenado por conta.

O AT do Bling dura 6h; o cron roda a cada 5h e renova TODAS as contas
ativas incondicionalmente — 1 chamada /oauth/token por conta por ciclo,
bem abaixo do rate gate. O Bling rotaciona o refresh_token a cada uso,
então cada conta é commitada individualmente logo após o HTTP 200
(perder o RT novo = lockout permanente da conta).

Contas recém-cadastradas (só `authorization_code`, sem tokens) fazem a
troca inicial grant_type=authorization_code no mesmo tick; o code é de
uso único e é limpo após sucesso.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingNota
from app.redis_client import redis
from app.services.marketplaces.bling import (
    BLING_TOKEN_URL,
    _acquire_bling_rate_slot,
)
from app.services.token_refresh_lock import token_refresh_lock

logger = structlog.get_logger()

# Bling AT dura 6h; fallback se a resposta não trouxer expires_in.
DEFAULT_EXPIRES_IN_S = 21600


def _basic_header(b64: str) -> str:
    """Coluna guarda só o base64("client_id:client_secret"); tolera o
    valor já vir com o prefixo "Basic "."""
    b64 = b64.strip()
    return b64 if b64.lower().startswith("basic ") else f"Basic {b64}"


async def _post_oauth_token(basic_auth_b64: str, data: dict) -> dict:
    # Cooldown CF compartilhado com o BlingClient — o ban 1015 é por IP,
    # então vale igualmente pros apps OAuth de NF.
    blocked = await redis.get("bling:cf_cooldown_until")
    if blocked is not None:
        try:
            ttl = int(blocked) - int(time.time())
        except (TypeError, ValueError):
            ttl = 0
        if ttl > 0:
            raise RuntimeError(f"bling_cf_cooldown_active ttl_s={ttl}")

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": _basic_header(basic_auth_b64),
    }
    await _acquire_bling_rate_slot()
    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.post(BLING_TOKEN_URL, headers=headers, data=data)
    if r.status_code >= 400:
        body_preview = r.text[:500]
        logger.warning(
            "bling_nota_token_http_error",
            status=r.status_code,
            body=body_preview,
            grant_type=data.get("grant_type"),
        )
        if r.status_code == 429 and "cloudflare" in body_preview.lower():
            cooldown_s = 3600
            until = int(time.time()) + cooldown_s
            await redis.set(
                "bling:cf_cooldown_until", str(until), ex=cooldown_s
            )
            logger.warning(
                "bling_cf_cooldown_armed",
                cooldown_s=cooldown_s,
                until_epoch=until,
            )
        r.raise_for_status()
    return r.json()


async def _apply_token_payload(
    s: AsyncSession, nota_id: UUID, payload: dict, *, clear_code: bool
) -> None:
    nota = await s.get(BlingNota, nota_id)
    if nota is None:
        logger.error("bling_nota_missing_on_persist", nota_id=str(nota_id))
        return
    nota.access_token = payload["access_token"]
    if payload.get("refresh_token"):
        nota.refresh_token = payload["refresh_token"]
    expires_in = int(payload.get("expires_in") or DEFAULT_EXPIRES_IN_S)
    nota.token_expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
    if clear_code:
        nota.authorization_code = None
    await s.commit()


async def run_refresh_bling_notas_tokens(s: AsyncSession) -> dict:
    """Renova o token de cada conta `bling_notas` ativa.

    - `refresh_token` presente → grant_type=refresh_token
    - só `authorization_code` (conta recém-cadastrada) → troca inicial
      grant_type=authorization_code
    """
    rows = (
        (
            await s.execute(
                select(BlingNota).where(
                    BlingNota.status == "active",
                    or_(
                        BlingNota.refresh_token.is_not(None),
                        BlingNota.authorization_code.is_not(None),
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    # Snapshot dos campos antes do loop: um rollback no meio expiraria os
    # objetos ORM e quebraria o acesso a atributos das contas seguintes.
    infos = [
        (n.id, n.nome, n.basic_auth_b64, n.refresh_token, n.authorization_code)
        for n in rows
    ]

    refreshed = exchanged = failed = skipped = 0
    for nota_id, nome, basic_b64, rt, code in infos:
        try:
            async with token_refresh_lock(f"bling_nota:{nota_id}") as acquired:
                if not acquired:
                    skipped += 1
                    continue
                if rt:
                    payload = await _post_oauth_token(
                        basic_b64,
                        {"grant_type": "refresh_token", "refresh_token": rt},
                    )
                    await _apply_token_payload(
                        s, nota_id, payload, clear_code=False
                    )
                    refreshed += 1
                else:
                    payload = await _post_oauth_token(
                        basic_b64,
                        {"grant_type": "authorization_code", "code": code},
                    )
                    await _apply_token_payload(
                        s, nota_id, payload, clear_code=True
                    )
                    exchanged += 1
            logger.info("bling_nota_token_ok", nota=nome)
        except Exception as e:  # noqa: BLE001
            await s.rollback()
            failed += 1
            logger.warning(
                "bling_nota_token_failed", nota=nome, err=str(e)[:300]
            )
    return {
        "total": len(infos),
        "refreshed": refreshed,
        "exchanged": exchanged,
        "failed": failed,
        "skipped": skipped,
    }
