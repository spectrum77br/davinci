"""Pós-venda › Atendimento: a porta do robô do Mac mini (Temu e AliExpress, 30/09/2026).

Temu e AliExpress não têm API de chat. Um robô no Mac mini mantém um perfil do
AdsPower por loja com a lista de conversas do Seller Center aberta, só ESCUTA
o que a página já recebe e manda para cá:

  POST /api/atendimento/robo/pulso    — "estou vivo": estado da leitura da loja
  POST /api/atendimento/robo/eventos  — cópias cruas do que a página recebeu
                                        (fetch/XHR e WebSocket), até 200 por vez

Quem interpreta e grava é `services/atendimento/robo.py` (e os leitores
`robo_temu`/`robo_aliexpress`). Contrato em `schemas/atendimento_robo.py`.

AUTENTICAÇÃO: `Authorization: Bearer <ATENDIMENTO_ROBO_TOKEN>`, comparado em
tempo constante. Setting vazia = endpoints DESLIGADOS (404, como se não
existissem): deploy não liga nada. Não há usuário por trás (o Histórico não
marca nada: é máquina, como o sync).

O CORPO só é lido DEPOIS do token: pedido sem token não custa a leitura de
uma leva de eventos. Teto por chamada (`EVENTOS_MAX_BYTES`, 413 acima); o
erro de formato (422) diz o CAMPO e o tipo do erro, nunca o valor — pode ser
texto de comprador.
"""

from __future__ import annotations

import secrets
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.schemas.atendimento_robo import EventosIn, EventosOut, PulsoIn, RoboOkOut
from app.services.atendimento import robo
from app.services.atendimento.robo_leitura import Evento

logger = structlog.get_logger()

router = APIRouter(prefix="/api/atendimento/robo", tags=["atendimento"])

# Um pulso é uma linha de JSON; a leva de eventos é grande (corpo de até 2 MB
# por evento), mas uma chamada acima disto o robô deve partir em duas.
PULSO_MAX_BYTES = 64 * 1024
EVENTOS_MAX_BYTES = 64 * 1024 * 1024


async def _robo_autorizado(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    esperado = get_settings().atendimento_robo_token
    if not esperado:
        raise HTTPException(404, detail={"code": "robo_desligado"})
    esquema, _, valor = (authorization or "").strip().partition(" ")
    enviado = valor.strip() if esquema.lower() == "bearer" else ""
    # Em bytes: compare_digest com texto fora do ASCII levanta TypeError (500).
    if not enviado or not secrets.compare_digest(
        enviado.encode("utf-8", "surrogateescape"), esperado.encode("utf-8")
    ):
        raise HTTPException(
            401,
            detail={"code": "robo_nao_autorizado"},
            headers={"WWW-Authenticate": "Bearer"},
        )


async def _corpo(request: Request, teto: int) -> bytes:
    """O corpo cru, com teto (413) — pelo cabeçalho e, sem ele, lendo aos pedaços."""
    declarado = request.headers.get("content-length", "")
    if declarado.isdigit() and int(declarado) > teto:
        raise HTTPException(413, detail={"code": "corpo_grande_demais", "teto_bytes": teto})
    partes = bytearray()
    async for pedaco in request.stream():
        partes.extend(pedaco)
        if len(partes) > teto:
            raise HTTPException(413, detail={"code": "corpo_grande_demais", "teto_bytes": teto})
    return bytes(partes)


def _validar[M: BaseModel](modelo: type[M], bruto: bytes) -> M:
    """JSON → modelo; 422 com o CAMPO e o tipo do erro, sem o valor recebido."""
    try:
        return modelo.model_validate_json(bruto or b"{}")
    except ValidationError as e:
        erros = [
            {"campo": ".".join(str(p) for p in erro.get("loc", ())), "tipo": erro.get("type")}
            for erro in e.errors(include_input=False, include_url=False)[:20]
        ]
        raise HTTPException(422, detail={"code": "corpo_invalido", "erros": erros}) from None


def _recusa(e: robo.RoboRecusado) -> HTTPException:
    return HTTPException(e.status, detail={"code": e.code, "detail": e.detail})


@router.post("/pulso", response_model=RoboOkOut, dependencies=[Depends(_robo_autorizado)])
async def pulso(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RoboOkOut:
    """O robô diz como está a leitura da loja (e prova que está vivo)."""
    body = _validar(PulsoIn, await _corpo(request, PULSO_MAX_BYTES))
    try:
        await robo.registrar_pulso(
            session,
            perfil_id=body.perfil_id,
            plataforma=body.plataforma,
            loja=body.loja,
            estado=body.estado,
            url=body.url,
            detalhe=body.detalhe,
            versao=body.versao,
        )
        await session.commit()
    except robo.RoboRecusado as e:
        await session.rollback()
        raise _recusa(e) from None
    return RoboOkOut()


@router.post("/eventos", response_model=EventosOut, dependencies=[Depends(_robo_autorizado)])
async def eventos(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> EventosOut:
    """Uma leva de cópias do que a página recebeu → conversas e mensagens na caixa.

    Idempotente: a mesma leva de novo não duplica nada (o robô pode reenviar
    à vontade quando não tiver certeza de que chegou).
    """
    body = _validar(EventosIn, await _corpo(request, EVENTOS_MAX_BYTES))
    lidos = [
        Evento(
            tipo=e.tipo,
            url=e.url,
            metodo=e.metodo,
            status=e.status,
            recebido_em=e.recebido_em,
            corpo=e.corpo,
        )
        for e in body.eventos
    ]
    try:
        resultado = await robo.receber_eventos(
            session,
            perfil_id=body.perfil_id,
            plataforma=body.plataforma,
            loja=body.loja,
            eventos=lidos,
        )
    except robo.RoboRecusado as e:
        await session.rollback()
        raise _recusa(e) from None
    except Exception as e:  # noqa: BLE001 — o 500 não pode levar o SQL junto
        # A mensagem de um erro de banco traz os PARÂMETROS do SQL — texto de
        # comprador. Sem isto, o traceback do uvicorn a punha no log da API.
        # Só o tipo do erro; o robô manda de novo (a gravação é idempotente).
        await session.rollback()
        logger.error(
            "atendimento_robo_eventos_falhou",
            perfil_id=body.perfil_id,
            plataforma=body.plataforma,
            erro=type(e).__name__,
        )
        raise HTTPException(500, detail={"code": "robo_erro_interno"}) from None
    return EventosOut(
        gravadas=resultado.gravadas,
        conversas=resultado.conversas,
        ignorados=resultado.ignorados,
        erros=resultado.erros,
    )
