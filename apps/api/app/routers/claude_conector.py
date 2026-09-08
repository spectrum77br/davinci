"""Conector do Claude — o chat do Claude (app do chefe) falando com o DaVinci.

Dois lados:

  * ADMIN (`/api/claude-conector`): gera/lista/revoga o link secreto de cada
    pessoa. O link é colado no claude.ai (Conectores → Adicionar conector
    personalizado) e aparece sozinho no celular. Só o HASH do token fica no
    banco; a URL completa é mostrada UMA vez, na criação.
  * MCP (`/api/claude-mcp/{token}/mcp`) — PÚBLICO, guardado pelo token no path
    (o app do Claude não manda cabeçalho de autenticação; token errado => 404,
    igual ao webhook do 17track). Fala o protocolo MCP "Streamable HTTP"
    (JSON-RPC 2.0 por POST, resposta JSON simples; GET => 405 porque não
    abrimos stream; DELETE => fim de sessão, sem estado aqui). Só o
    necessário: initialize, ping, tools/list, tools/call. Com freios: corpo
    até 64 KB, lote até 5 mensagens, 30 chamadas de ferramenta por minuto.

O token não vai pros access logs: `mascarar_token_no_access_log` (main.py)
troca o segmento por *** e o Caddy tem filtro equivalente.

Eduardo, 08/09/2026: "é só para ele mandar o áudio e a gente gravar na aba
Tarefas". O áudio vira texto no próprio app do Claude; aqui chega o comando.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from urllib.parse import urlparse
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_admin
from app.models import ClaudeConector, User, UserStatus
from app.models.enums import AlertSeverity, AlertType
from app.services import claude_tarefas
from app.services.alerts import emit_alert
from app.services.rate_limit import RateLimitError, sliding_window_check

logger = structlog.get_logger()
router = APIRouter(tags=["claude-conector"])

PROTOCOLOS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
INSTRUCOES = (
    "Você está conectado ao DaVinci, o sistema interno da empresa. Use `criar_tarefa` "
    "sempre que o usuário pedir para anotar/registrar/criar uma tarefa — inclusive quando "
    "ele ditar por áudio. Confirme ao usuário o que foi criado com o texto devolvido."
)
MAX_CORPO_BYTES = 64 * 1024
MAX_LOTE = 5
LIMITE_CHAMADAS_MIN = 30
ORIGENS_PERMITIDAS_EXTRA = ("https://claude.ai", "https://claude.com")
_RX_TOKEN_NO_PATH = re.compile(r"(/api/claude-mcp/)[0-9a-f]{16,}")


def mascarar_token_no_access_log() -> None:
    """Filtro no logger de acesso do uvicorn: o path com o token vira
    `/api/claude-mcp/***/mcp` antes de ir pro log (o uvicorn loga
    '%s - "%s %s HTTP/%s" %d' com o path em args[2])."""

    class _Mascara(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            args = record.args
            if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
                record.args = (*args[:2], _RX_TOKEN_NO_PATH.sub(r"\1***", args[2]), *args[3:])
            return True

    logging.getLogger("uvicorn.access").addFilter(_Mascara())


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------- admin


class ConectorOut(BaseModel):
    id: UUID
    user_id: UUID
    user_nome: str | None
    nome: str
    # Só na criação (o banco guarda o hash; depois não dá mais pra mostrar).
    url: str | None = None
    criado_por: str | None
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    ultimo_erro: str | None


class ConectorCreate(BaseModel):
    user_id: UUID
    nome: str = Field(min_length=1, max_length=120)


def _url(token: str) -> str:
    base = (get_settings().app_url or "").rstrip("/")
    return f"{base}/api/claude-mcp/{token}/mcp"


def _out(
    c: ClaudeConector, u: User | None, criador: User | None, url: str | None = None
) -> ConectorOut:
    return ConectorOut(
        id=c.id,
        user_id=c.user_id,
        user_nome=(u.name or u.email) if u else None,
        nome=c.nome,
        url=url,
        criado_por=(criador.name or criador.email) if criador else None,
        created_at=c.created_at,
        last_used_at=c.last_used_at,
        revoked_at=c.revoked_at,
        ultimo_erro=c.ultimo_erro,
    )


@router.get("/api/claude-conector", response_model=list[ConectorOut])
async def listar(
    session: Annotated[AsyncSession, Depends(get_session)],
    _admin: Annotated[User, Depends(require_admin)],
) -> list[ConectorOut]:
    u = aliased(User)
    cr = aliased(User)
    rows = (
        await session.execute(
            select(ClaudeConector, u, cr)
            .outerjoin(u, u.id == ClaudeConector.user_id)
            .outerjoin(cr, cr.id == ClaudeConector.created_by)
            .order_by(ClaudeConector.revoked_at.is_not(None), ClaudeConector.created_at.desc())
        )
    ).all()
    return [_out(c, usr, criador) for c, usr, criador in rows]


@router.post(
    "/api/claude-conector", response_model=ConectorOut, status_code=status.HTTP_201_CREATED
)
async def criar(
    body: ConectorCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin: Annotated[User, Depends(require_admin)],
) -> ConectorOut:
    usr = next(
        (x for x in await claude_tarefas.usuarios_atribuiveis(session) if x.id == body.user_id),
        None,
    )
    if usr is None:
        raise HTTPException(404, detail={"code": "user_not_found"})
    token = secrets.token_hex(24)
    c = ClaudeConector(
        user_id=usr.id, nome=body.nome.strip(), token_hash=_hash(token), created_by=admin.id
    )
    session.add(c)
    await session.flush()
    # Conector gerado pra OUTRA pessoa: ela fica sabendo (quem tem o link cria
    # tarefa em nome dela). Se não foi ela que pediu, revoga na aba Tarefas.
    if usr.id != admin.id:
        try:
            async with session.begin_nested():  # falha no aviso não desfaz o conector
                await emit_alert(
                    session,
                    user_id=usr.id,
                    type=AlertType.TAREFA_ATRIBUIDA,
                    title="🔗 Conector do Claude criado para você",
                    severity=AlertSeverity.INFO,
                    message=(
                        f"{admin.name or admin.email} gerou o conector '{c.nome}' ligado ao "
                        "seu usuário: quem tiver esse link cria tarefas em seu nome pelo "
                        "Claude. Se não foi você que pediu, avise um administrador para revogar."
                    ),
                    payload={"conector_id": str(c.id), "origem": "claude_conector"},
                    notify_telegram=True,
                )
        except Exception as e:  # noqa: BLE001 — aviso é acessório
            logger.warning("claude_conector_aviso_falhou", conector=str(c.id), err=str(e)[:200])
    await session.commit()
    await session.refresh(c)
    logger.info("claude_conector_criado", id=str(c.id), user_id=str(usr.id), por=str(admin.id))
    return _out(c, usr, admin, url=_url(token))


@router.delete("/api/claude-conector/{conector_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revogar(
    conector_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin: Annotated[User, Depends(require_admin)],
) -> Response:
    c = (
        await session.execute(select(ClaudeConector).where(ClaudeConector.id == conector_id))
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "not_found"})
    if c.revoked_at is None:
        c.revoked_at = datetime.now(UTC)
        await session.commit()
        logger.info("claude_conector_revogado", id=str(c.id), por=str(admin.id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- MCP


def _rpc_result(id_: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _rpc_error(id_: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _texto(texto: str, *, erro: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": texto}], "isError": erro}


def _json(payload: Any, status_code: int = 200) -> Response:
    return Response(
        content=json.dumps(payload, ensure_ascii=False),
        media_type="application/json",
        status_code=status_code,
    )


async def _conector_por_token(session: AsyncSession, token: str) -> tuple[ClaudeConector, User]:
    if not token or len(token) > 64:
        raise HTTPException(404)
    row = (
        await session.execute(
            select(ClaudeConector, User)
            .join(User, User.id == ClaudeConector.user_id)
            .where(ClaudeConector.token_hash == _hash(token), ClaudeConector.revoked_at.is_(None))
        )
    ).first()
    if row is None:
        raise HTTPException(404)
    c, u = row
    if u.status != UserStatus.ACTIVE or u.disabled_at is not None:
        raise HTTPException(404)
    return c, u


def _origem_permitida(origin: str | None) -> bool:
    """Spec MCP: validar Origin (anti DNS rebinding). O backend do Claude
    chama servidor-a-servidor (sem Origin); se vier, tem que ser o nosso ou
    o do Claude."""
    if not origin:
        return True
    o = origin.rstrip("/").lower()
    nosso = (get_settings().app_url or "").rstrip("/").lower()
    permitidas = {nosso, *ORIGENS_PERMITIDAS_EXTRA}
    host_nosso = urlparse(nosso).hostname
    return o in permitidas or (bool(host_nosso) and urlparse(o).hostname == host_nosso)


async def _chamar_ferramenta(
    session: AsyncSession, conector: ClaudeConector, dono: User, id_: Any, params: dict[str, Any]
) -> dict[str, Any]:
    nome = params.get("name")
    args = params.get("arguments") or {}
    if nome != claude_tarefas.TOOL_CRIAR_TAREFA["name"]:
        return _rpc_error(id_, -32602, f"ferramenta desconhecida: {nome}")
    if not isinstance(args, dict):
        return _rpc_error(id_, -32602, "`arguments` precisa ser um objeto")
    try:
        await sliding_window_check(
            key=f"claude_mcp:rl:{conector.id}", limit=LIMITE_CHAMADAS_MIN, window_seconds=60
        )
    except RateLimitError as e:
        conector.ultimo_erro = "limite de chamadas por minuto atingido"
        await session.commit()
        return _rpc_result(
            id_,
            _texto(
                f"Muitas tarefas em sequência; tente de novo em {e.retry_after} segundos.",
                erro=True,
            ),
        )
    except Exception as e:  # noqa: BLE001 — Redis fora do ar não bloqueia o chefe
        logger.warning("claude_mcp_rate_limit_indisponivel", err=str(e)[:120])
    try:
        texto = await claude_tarefas.criar_tarefa(session, dono=dono, args=args)
        conector.ultimo_erro = None
        await session.commit()
        return _rpc_result(id_, _texto(texto))
    except claude_tarefas.TarefaInvalidaError as e:
        await session.rollback()
        conector.ultimo_erro = str(e)[:500]
        await session.commit()
        return _rpc_result(id_, _texto(str(e), erro=True))
    except Exception as e:  # noqa: BLE001 — devolve como erro da ferramenta, não 500
        logger.exception("claude_mcp_tool_falhou", conector=str(conector.id))
        await session.rollback()
        conector.ultimo_erro = f"falha interna ({type(e).__name__}) — ver logs"
        await session.commit()
        return _rpc_result(
            id_,
            _texto(
                "O DaVinci não conseguiu criar a tarefa agora. Tente de novo em instantes.",
                erro=True,
            ),
        )


async def _tratar(
    session: AsyncSession, conector: ClaudeConector, dono: User, msg: dict[str, Any]
) -> dict[str, Any] | None:
    """Uma mensagem JSON-RPC -> resposta (ou None pra notificação/resposta do cliente)."""
    id_ = msg.get("id")
    metodo = msg.get("method")
    if "method" not in msg or id_ is None:
        # Notificação (ex.: notifications/initialized) ou resposta do cliente:
        # sem corpo de volta (HTTP 202).
        return None
    if not isinstance(metodo, str):
        return _rpc_error(id_, -32600, "requisição inválida")
    params = msg.get("params") or {}
    if not isinstance(params, dict):
        return _rpc_error(id_, -32602, "`params` precisa ser um objeto")
    if metodo == "initialize":
        pedido = str(params.get("protocolVersion") or "")
        versao = pedido if pedido in PROTOCOLOS else PROTOCOLOS[0]
        return _rpc_result(
            id_,
            {
                "protocolVersion": versao,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "DaVinci", "version": "1.0"},
                "instructions": INSTRUCOES,
            },
        )
    if metodo == "ping":
        return _rpc_result(id_, {})
    if metodo == "tools/list":
        return _rpc_result(id_, {"tools": [claude_tarefas.TOOL_CRIAR_TAREFA]})
    if metodo == "tools/call":
        return await _chamar_ferramenta(session, conector, dono, id_, params)
    if metodo in ("resources/list", "prompts/list"):
        return _rpc_result(id_, {metodo.split("/")[0]: []})
    return _rpc_error(id_, -32601, f"método não suportado: {metodo}")


@router.post("/api/claude-mcp/{token}/mcp")
async def mcp_post(
    token: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    conector, dono = await _conector_por_token(session, token)
    if not _origem_permitida(request.headers.get("origin")):
        return Response(status_code=status.HTTP_403_FORBIDDEN)
    versao_hdr = request.headers.get("mcp-protocol-version")
    if versao_hdr and versao_hdr not in PROTOCOLOS:
        return _json(_rpc_error(None, -32600, f"versão MCP não suportada: {versao_hdr}"), 400)
    try:
        if int(request.headers.get("content-length") or 0) > MAX_CORPO_BYTES:
            return Response(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    except ValueError:
        pass
    bruto = await request.body()
    if len(bruto) > MAX_CORPO_BYTES:
        return Response(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    try:
        corpo = json.loads(bruto or b"null")
    except ValueError:
        return _json(_rpc_error(None, -32700, "JSON inválido"), 400)

    # Carimbo de uso (no máximo 1x/min pra não escrever a cada ping).
    agora = datetime.now(UTC)
    if conector.last_used_at is None or conector.last_used_at < agora - timedelta(minutes=1):
        conector.last_used_at = agora
        await session.commit()

    if isinstance(corpo, list):
        if len(corpo) > MAX_LOTE:
            return _json(_rpc_error(None, -32600, f"lote maior que {MAX_LOTE} mensagens"), 400)
        mensagens = corpo
    else:
        mensagens = [corpo]
    respostas: list[dict[str, Any]] = []
    for m in mensagens:
        if not isinstance(m, dict):
            respostas.append(_rpc_error(None, -32600, "requisição inválida"))
            continue
        r = await _tratar(session, conector, dono, m)
        if r is not None:
            respostas.append(r)
    if not respostas:
        return Response(status_code=status.HTTP_202_ACCEPTED)
    return _json(respostas if isinstance(corpo, list) else respostas[0])


@router.get("/api/claude-mcp/{token}/mcp")
async def mcp_get(token: str, session: Annotated[AsyncSession, Depends(get_session)]) -> Response:
    await _conector_por_token(session, token)
    # Sem stream servidor->cliente (não mandamos notificações); o protocolo
    # manda responder 405 nesse caso.
    return Response(status_code=status.HTTP_405_METHOD_NOT_ALLOWED)


@router.delete("/api/claude-mcp/{token}/mcp")
async def mcp_delete(
    token: str, session: Annotated[AsyncSession, Depends(get_session)]
) -> Response:
    await _conector_por_token(session, token)
    return Response(status_code=status.HTTP_200_OK)
