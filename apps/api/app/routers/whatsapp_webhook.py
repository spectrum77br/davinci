"""Webhook PÚBLICO do Zap (WhatsApp Cloud API oficial da Meta) — RF10, Onda 1.

Desenho: `docs/zap/desenho.md` §5.1 e §10.2. Uma rota por APP da Meta (pode
haver vários: o "DaVinci Zap Teste" e o "DaVinci Zap"), no molde de
`routers/nfse_webhook.py` (router próprio, assinatura pura):

  GET  /api/webhooks/meta/whatsapp/{app_id}   handshake (`hub.challenge` em
       TEXTO PURO — devolver JSON é a causa nº 1 de "URL couldn't be validated").
  POST /api/webhooks/meta/whatsapp/{app_id}   eventos, assinados com
       `X-Hub-Signature-256` = HMAC-SHA256 do corpo CRU com o app secret DESTE app.

O POST, em ordem:
  0. Só no host principal (o do `api_url`): o verify token vai na query do GET
     e o access log do outro host (`gestaoestoque.com`) não tem filtro. 404.
  1. O app pelo `{app_id}` (cache de 60 s, inclusive NEGATIVO). Sem app: 404.
  2. Freio por app e IP SÓ para quem já errou a assinatura DESTE app (mais de
     30 em 10 min): 429 ANTES de ler o corpo e de calcular o HMAC — o
     `app_id` é público. O IP que acertou a assinatura (de qualquer app) nos
     últimos 10 min nunca é freado: os IPs da Meta são os mesmos para todos
     os apps, e uma troca de segredo não pode congelar a entrega.
  3. O corpo em streaming, com teto de 4 MB (a Meta manda até 3 MB; o corpo
     pode vir em partes, sem `Content-Length`, e o Caddy não limita). 413.
  4. A assinatura (o segredo atual; o anterior só na janela de 1 h de uma
     troca de rotina, contada no Redis). Inválida: 403, contada SÓ no Redis
     (`webhook:meta_wa:*`, nunca no banco, nunca no contador do Bling nem no
     do Instagram).
  5. JSON inválido (com assinatura certa): gravado como `ignorado`
     (`json_invalido`) — nada some em silêncio.
  6. Quebra em itens (`zap_entrada.itens_do_corpo`; se a quebra falhar, o
     corpo cru vai como no passo 5 — corpo assinado nunca dá 500 por formato)
     e um INSERT … ON CONFLICT (chave) DO NOTHING. COMMIT. Banco fora: 500
     (a Meta reenvia).
  7. Enfileira o processador, COALESCIDO (`SET NX EX 3`; nada de `_job_id`
     fixo, que sumiria calado por 1 h com o resultado guardado pelo arq). Se
     falhar, o cron de 1 min pega.
  8. 200 `{"ack": true, "novos": n}`.

O webhook GRAVA sempre que a assinatura confere, mesmo com as chaves
desligadas — quem decide processar é o worker. Sem app cadastrado no banco,
a rota responde 404 e não faz nada.
"""

from __future__ import annotations

import hmac
import json
import time
from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import urlsplit

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import AtendimentoZapApp
from app.redis_client import redis
from app.routers import webhooks as _webhooks
from app.services.atendimento import zap_contas, zap_entrada
from app.worker_pool import get_arq_pool

logger = structlog.get_logger()
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

# A Meta manda no máximo 3 MB por POST.
TETO_CORPO = 4 * 1024 * 1024

# Falha de assinatura: SÓ Redis, chaves próprias do Zap (o alarme da Onda 4 é
# por proporção — falhas E nenhum evento válido do app no mesmo período).
SIG_FAIL_KEY = "webhook:meta_wa:sig_fail_count"
SIG_FAIL_SNAPSHOT_KEY = "webhook:meta_wa:sig_fail_last"
# Assinatura que bateu só com o segredo ANTERIOR (troca de rotina, 1 h).
ANTERIOR_KEY = "webhook:meta_wa:sig_anterior_count"
# Corpo acima do teto.
GRANDE_KEY = "webhook:meta_wa:corpo_grande_count"
# Freio por APP e IP: quem errou a assinatura deste app mais de FREIO_MAX
# vezes em FREIO_JANELA_S (`ip_falhas:<app>:<ip>`). Os IPs de saída da Meta
# são os mesmos para todos os apps: o app de teste com o segredo desatualizado
# não pode frear o de produção.
FREIO_PREFIXO = "webhook:meta_wa:ip_falhas:"
FREIO_MAX = 30
FREIO_JANELA_S = 600
# IP que acertou a assinatura (de qualquer app) nos últimos FREIO_JANELA_S:
# nunca é freado (§5.1 passo 3 — a Meta no meio de uma troca de segredo).
IP_OK_PREFIXO = "webhook:meta_wa:ip_ok:"
CONTADOR_TTL_S = 3600

# Saúde do app (`ultimo_evento_em`): no máximo uma escrita por minuto por app.
_ULTIMO_EVENTO_A_CADA_S = 60.0
_ultimo_evento: dict[str, float] = {}

_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


class CorpoGrande(Exception):  # noqa: N818 — português, como `ErroDeMidia`
    pass


def _host_do_pedido(request: Request) -> str:
    bruto = (request.headers.get("host") or "").strip().lower()
    if bruto.startswith("["):  # IPv6 com porta: [::1]:8000
        return bruto[1:].split("]", 1)[0]
    return bruto.rsplit(":", 1)[0] if bruto.count(":") == 1 else bruto


def host_principal_ok(request: Request) -> bool:
    """O `Host` do pedido é o do `api_url`? Fora de produção, os nomes de
    loopback valem um pelo outro (localhost × 127.0.0.1)."""
    s = get_settings()
    principal = (urlsplit(s.api_url).hostname or "").lower()
    host = _host_do_pedido(request)
    if not principal or not host:
        return False
    if host == principal:
        return True
    return (not s.is_prod) and host in _LOOPBACK and principal in _LOOPBACK


def _ip(request: Request) -> str:
    """O IP de quem chamou (o primeiro do X-Forwarded-For que o Caddy põe)."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()[:64] or "?"
    return request.client.host if request.client else "?"


async def _contar(chave: str) -> None:
    try:
        await redis.incr(chave)
        await redis.expire(chave, CONTADOR_TTL_S)
    except Exception as e:  # noqa: BLE001 — contador é acessório
        logger.warning("zap_webhook_contador_falhou", err=type(e).__name__)


def _chave_freio(app_ref: object, ip: str) -> str:
    return f"{FREIO_PREFIXO}{app_ref}:{ip}"


async def _freado(app_ref: object, ip: str) -> bool:
    try:
        bruto = await redis.get(_chave_freio(app_ref, ip))
        if int(bruto or 0) <= FREIO_MAX:
            return False
        # Acertou há pouco (é a Meta): nunca freado.
        return not await redis.exists(IP_OK_PREFIXO + ip)
    except Exception:  # noqa: BLE001 — sem Redis, sem freio (a assinatura ainda vale)
        return False


async def _contar_falha_do_ip(app_ref: object, ip: str) -> None:
    chave = _chave_freio(app_ref, ip)
    try:
        n = await redis.incr(chave)
        if int(n or 0) <= 1:
            await redis.expire(chave, FREIO_JANELA_S)
    except Exception as e:  # noqa: BLE001
        logger.warning("zap_webhook_freio_falhou", err=type(e).__name__)


async def _marcar_ip_ok(ip: str) -> None:
    try:
        await redis.set(IP_OK_PREFIXO + ip, "1", ex=FREIO_JANELA_S)
    except Exception as e:  # noqa: BLE001 — a marca é acessória
        logger.warning("zap_webhook_ip_ok_falhou", err=type(e).__name__)


async def _ler_corpo(request: Request) -> bytes:
    """O corpo em streaming; para ao passar do teto (sem ler o resto)."""
    declarado = request.headers.get("content-length")
    if declarado and declarado.strip().isdigit() and int(declarado) > TETO_CORPO:
        raise CorpoGrande
    partes = bytearray()
    async for pedaco in request.stream():
        partes.extend(pedaco)
        if len(partes) > TETO_CORPO:
            raise CorpoGrande
    return bytes(partes)


async def _enfileirar() -> None:
    """Coalescido: só o primeiro POST de cada 3 s enfileira (o job apaga a
    chave ao começar). Falhou: o cron `atendimento_zap_varrer` pega."""
    if not zap_entrada.leitura_ligada():
        return  # o job sairia na hora; os eventos esperam a chave ligar
    try:
        pegou = await redis.set(zap_entrada.CHAVE_PEDIDO, "1", nx=True, ex=3)
        if not pegou:
            return
        pool = await get_arq_pool()
        await pool.enqueue_job("atendimento_zap_processar")
    except Exception as e:  # noqa: BLE001 — o 200 já é devido (o evento está gravado)
        logger.warning("zap_webhook_enfileirar_falhou", err=type(e).__name__)


@router.get("/meta/whatsapp/{app_id}")
async def verificar_zap(
    app_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Handshake da Meta: `hub.challenge` em TEXTO PURO, com o verify token DESTE app."""
    if not host_principal_ok(request):
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    app = await zap_contas.segredos_do_app(session, app_id)
    await session.rollback()
    if app is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    p = request.query_params
    recebido = p.get("hub.verify_token") or ""
    if (
        app.verify_token
        and p.get("hub.mode") == "subscribe"
        # BYTES: `?hub.verify_token=ção` viraria 500 com str.
        and hmac.compare_digest(app.verify_token.encode(), recebido.encode("utf-8", "ignore"))
    ):
        logger.info("zap_webhook_verificado", app=str(app.id))
        return Response(content=p.get("hub.challenge") or "", media_type="text/plain")
    logger.warning("zap_webhook_verify_recusado", app=str(app.id))
    return Response(status_code=status.HTTP_403_FORBIDDEN)


async def _marcar_ultimo_evento(session: AsyncSession, app: zap_contas.SegredosApp) -> None:
    agora = time.monotonic()
    chave = str(app.id)
    if agora - _ultimo_evento.get(chave, 0.0) < _ULTIMO_EVENTO_A_CADA_S:
        return
    _ultimo_evento[chave] = agora
    await session.execute(
        update(AtendimentoZapApp)
        .where(AtendimentoZapApp.id == app.id)
        .values(ultimo_evento_em=datetime.now(UTC))
    )


@router.post("/meta/whatsapp/{app_id}")
async def receber_zap(
    app_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_hub_signature_256: Annotated[str | None, Header(alias="X-Hub-Signature-256")] = None,
) -> dict[str, Any]:
    if not host_principal_ok(request):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    app = await zap_contas.segredos_do_app(session, app_id)
    await session.rollback()
    if app is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    ip = _ip(request)
    if await _freado(app.id, ip):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS)

    try:
        corpo = await _ler_corpo(request)
    except CorpoGrande:
        await _contar(GRANDE_KEY)
        logger.warning("zap_webhook_corpo_grande", app=str(app.id))
        raise HTTPException(status_code=413) from None  # Content Too Large

    if not _webhooks.assinatura_meta_ok(corpo, x_hub_signature_256, [app.atual]):
        anterior = app.anterior_valido()
        if anterior is not None and _webhooks.assinatura_meta_ok(
            corpo, x_hub_signature_256, [anterior]
        ):
            await _contar(ANTERIOR_KEY)
            logger.warning("zap_webhook_assinatura_anterior", app=str(app.id))
        else:
            await _webhooks._bump_sig_failure(
                "meta_wa_bad_signature",
                {"app": str(app.id), "body_len": len(corpo)},
                contador=SIG_FAIL_KEY,
                snapshot_key=SIG_FAIL_SNAPSHOT_KEY,
            )
            await _contar_falha_do_ip(app.id, ip)
            logger.warning("zap_webhook_sig_invalida", app=str(app.id), body_len=len(corpo))
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="assinatura inválida")
    await _marcar_ip_ok(ip)

    try:
        parsed: Any = json.loads(corpo)
    except (json.JSONDecodeError, UnicodeDecodeError):
        itens = [zap_entrada.item_json_invalido(corpo)]
        logger.error("zap_webhook_json_invalido", app=str(app.id), body_len=len(corpo))
    else:
        try:
            itens = zap_entrada.itens_do_corpo(parsed)
        except Exception as e:  # noqa: BLE001 — corpo assinado nunca dá 500 por formato
            # Guardado cru como `ignorado`, igual ao JSON quebrado: um 500 aqui
            # faria a Meta reenviar o lote inteiro (até 1.000 updates) por 7 dias.
            itens = [zap_entrada.item_json_invalido(corpo)]
            logger.error("zap_webhook_corpo_nao_quebrou", app=str(app.id), err=type(e).__name__)
        if any(it.resultado == zap_entrada.R_OBJETO_INESPERADO for it in itens):
            logger.warning("zap_webhook_objeto_inesperado", app=str(app.id))

    try:
        novos = await zap_entrada.gravar_itens(session, itens, app_ref=app.id)
        await _marcar_ultimo_evento(session, app)
        await session.commit()
    except Exception as e:  # noqa: BLE001 — 500: a Meta reenvia (o UNIQUE barra o repetido)
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001, S110 — a conexão já caiu
            pass
        _ultimo_evento.pop(str(app.id), None)
        logger.error("zap_webhook_gravar_falhou", app=str(app.id), err=type(e).__name__)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR) from None

    if novos and any(it.tipo != zap_entrada.TIPO_IGNORADO for it in itens):
        await _enfileirar()
    return {"ack": True, "novos": novos}
