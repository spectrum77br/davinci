"""Webhook da NFE.io — aviso de NFS-e emitida, recusada ou cancelada (29/09/2026).

Eduardo: "vamos usar o webhook também". A NFE.io avisa na hora em vez de o
DaVinci esperar a conferência de 2 em 2 minutos (que continua sendo a rede).

- Um webhook por CONTA: chega aviso de todas as 25 empresas, inclusive de nota
  emitida pelo painel da NFE.io. Nota que o DaVinci não conhece → 200 e ignora.
- Assinatura: `X-Hub-Signature: sha1=<HEX>` = HMAC-SHA1 do corpo CRU com o
  segredo cadastrado (`NFEIO_WEBHOOK_SECRET`). Sem segredo configurado → 503
  (a NFE.io tenta de novo depois); assinatura errada → 401.
- O corpo NUNCA decide o status: dele sai só o id da nota, e o estado vem de um
  GET na NFE.io (`emissao.atualizar`) — um aviso forjado não mexe em nada.
- Entrega "pelo menos uma vez" (até 16 tentativas em ~45 h): o `X-Hook-Id` só é
  marcado como visto DEPOIS de processar; falha nossa → 500 e a NFE.io repete.
- Cadastro do webhook na conta: só com OK do Eduardo, em produção.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models.nfse import NfseEmissao
from app.redis_client import redis
from app.services.nfse import emissao as svc
from app.services.nfse import nfeio

logger = structlog.get_logger()
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

VISTO_TTL = 3 * 86_400  # a NFE.io repete por ~45 h


def assinatura_ok(corpo: bytes, cabecalho: str | None, segredo: str) -> bool:
    """`sha1=<hex>` (a NFE.io manda o hex em MAIÚSCULAS) sobre o corpo cru."""
    if not cabecalho or not segredo:
        return False
    valor = cabecalho.strip()
    if valor.lower().startswith("sha1="):
        valor = valor[5:]
    esperado = hmac.new(segredo.encode(), corpo, hashlib.sha1).hexdigest()
    return hmac.compare_digest(valor.strip().lower(), esperado)


def _nota_do_corpo(dados: Any) -> dict:
    if not isinstance(dados, dict):
        return {}
    nota = dados.get("payload")
    if isinstance(nota, dict):
        return nota
    return dados if dados.get("id") or dados.get("externalId") else {}


@router.post("/nfeio")
async def receber_nfeio(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_hub_signature: Annotated[str | None, Header(alias="X-Hub-Signature")] = None,
    x_hook_id: Annotated[str | None, Header(alias="X-Hook-Id")] = None,
) -> dict[str, Any]:
    segredo = (get_settings().nfeio_webhook_secret or "").strip()
    if not segredo:
        raise HTTPException(503, detail={"code": "webhook_sem_segredo"})
    corpo = await request.body()
    try:
        nota = _nota_do_corpo(json.loads(corpo or b"{}"))
    except json.JSONDecodeError:
        nota = {}
    nfeio_id = str(nota.get("id") or "").strip()
    externo = str(nota.get("externalId") or "").strip()
    if not nfeio_id and not externo:
        # Sem nota nenhuma (ex.: o teste que a NFE.io faz ao cadastrar o
        # webhook, que exige 2xx): responde e não mexe em nada — mesmo sem
        # assinatura, porque aqui não há o que um aviso forjado possa mudar.
        return {"ack": True, "ignorado": "sem_id"}
    if not assinatura_ok(corpo, x_hub_signature, segredo):
        logger.warning("nfeio_webhook_assinatura_invalida", hook=x_hook_id)
        raise HTTPException(401, detail={"code": "assinatura_invalida"})

    chave_visto = f"webhook:nfeio:visto:{x_hook_id or hashlib.sha256(corpo).hexdigest()}"
    if await redis.exists(chave_visto):
        return {"ack": True, "duplicado": True}

    filtros = []
    if nfeio_id:
        filtros.append(NfseEmissao.nfeio_id == nfeio_id)
    if externo:
        filtros.append(NfseEmissao.nfeio_external_id == externo)
    e = (
        await session.execute(select(NfseEmissao).where(or_(*filtros)).limit(1))
    ).scalar_one_or_none()
    if e is None:
        # Nota de fora do DaVinci (painel da NFE.io, outra integração).
        await redis.set(chave_visto, "1", ex=VISTO_TTL)
        return {"ack": True, "ignorado": "nota_desconhecida"}

    if not nfeio.chave_configurada():
        raise HTTPException(503, detail={"code": "chave_nfeio"})
    try:
        await svc.atualizar(session, e)  # estado verdadeiro por GET
    except Exception as ex:  # noqa: BLE001 — 500 = a NFE.io entrega de novo
        logger.warning("nfeio_webhook_falhou", emissao=str(e.id), erro=type(ex).__name__)
        raise HTTPException(500, detail={"code": "falha_ao_atualizar"}) from ex
    await redis.set(chave_visto, "1", ex=VISTO_TTL)
    logger.info("nfeio_webhook", emissao=str(e.id), status=e.status, hook=x_hook_id)
    return {"ack": True, "status": e.status}
