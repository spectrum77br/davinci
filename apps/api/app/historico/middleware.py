"""Middleware do Histórico: abre o Ator de cada pedido e grava o evento no fim.

ASGI puro (não BaseHTTPMiddleware, que roda o app em outra tarefa e esconderia
o que a autenticação preencheu). O corpo é copiado enquanto o app o lê — nada
é lido duas vezes. O evento é gravado DEPOIS de a resposta ir embora: quem
pediu não espera por ele, e uma falha aqui nunca derruba o pedido.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

import structlog
from sqlalchemy import func, select

from app.historico import nomes
from app.historico.contexto import Ator, abrir, fechar
from app.historico.leitura import congelar, nomes_da_acao
from app.historico.mascara import limpar_texto, resumir_corpo

logger = structlog.get_logger()

ESCRITA = {"POST", "PUT", "PATCH", "DELETE"}
LIMITE_CORPO_LIDO = 64 * 1024


def _cabecalho(scope, nome: bytes) -> str | None:
    for k, v in scope.get("headers") or ():
        if k == nome:
            return v.decode("latin-1")
    return None


def _rota(scope) -> str:
    rota = scope.get("route")
    return getattr(rota, "path", None) or scope["path"]


def _caminho_sem_segredo(scope, rota: str) -> str:
    """O endereço de verdade (com os ids), mas com token/segredo trocado."""
    params = scope.get("path_params") or {}
    if not params:
        return scope["path"]
    caminho = rota
    for k, v in params.items():
        valor = "***" if k in nomes.PARAMETROS_SECRETOS else str(v)
        caminho = caminho.replace("{" + k + "}", valor).replace("{" + k + ":path}", valor)
    return caminho


def _pagina(scope) -> str | None:
    ref = _cabecalho(scope, b"referer")
    if not ref:
        return None
    try:
        return urlsplit(ref).path or "/"
    except ValueError:
        return None


class HistoricoMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return

        metodo = scope["method"]
        # Robô disfarçado de pedido de pessoa (ex. o recarregar automático da
        # Margem) não marca o banco: as rotas da lista não têm parâmetro, então
        # dá para saber antes de o roteador escolher.
        robo = (metodo, scope["path"].rstrip("/")) in nomes.ROTAS_DE_ROBO
        escrita = (metodo in ESCRITA or bool(nomes.GET_QUE_GRAVA.search(scope["path"]))) and not robo
        ator = Ator(metodo=metodo, caminho=scope["path"], grava=escrita, escrita=escrita)
        token = abrir(ator)
        corpo = bytearray()
        status: list[int] = []

        async def receber():
            msg = await receive()
            if ator.escrita and msg["type"] == "http.request" and len(corpo) < LIMITE_CORPO_LIDO:
                corpo.extend(msg.get("body", b"")[: LIMITE_CORPO_LIDO - len(corpo)])
            return msg

        async def enviar(msg):
            if msg["type"] == "http.response.start":
                status.append(msg["status"])
            elif msg["type"] == "http.response.body" and not msg.get("more_body", False):
                # Resposta entregue. O que rodar depois dela (BackgroundTasks —
                # ex. o "Rodar agora" da Ouvidoria, que roda o auto-hold) é
                # trabalho de robô, não da pessoa: para de marcar o banco.
                ator.grava = False
            await send(msg)

        falhou = False
        try:
            await self.app(scope, receber, enviar)
        except Exception:
            falhou = True
            raise
        finally:
            fechar(token)
            if ator.user_id is not None and (status or falhou):
                try:
                    # Com prazo: com o banco engasgado (pool esgotado), o erro
                    # da pessoa não pode esperar o Histórico.
                    await asyncio.wait_for(
                        _gravar_evento(ator, scope, bytes(corpo), status[0] if status else 500),
                        timeout=5,
                    )
                except Exception as e:  # noqa: BLE001 - o Histórico nunca derruba o pedido
                    # Só o molde da rota: o caminho de verdade pode ter token
                    # (ex. /api/claude-mcp/<token>/mcp).
                    logger.warning("historico_evento_falhou", erro=type(e).__name__, rota=_rota(scope))


async def _gravar_evento(ator: Ator, scope, corpo: bytes, status: int) -> None:
    from app.db import SessionLocal
    from app.models import HistoricoAlteracao, HistoricoEvento

    rota = _rota(scope)
    chave = (ator.metodo, rota)
    revelacao = nomes.REVELACOES.get(chave)
    acao = revelacao or nomes.ACOES.get(chave)
    frase = acao or nomes.FRASES.get(chave)
    if not ator.escrita and not revelacao:
        return  # leitura comum

    async with SessionLocal() as s:
        n = 0
        if ator.escrita:
            n = (
                await s.execute(
                    select(func.count())
                    .select_from(HistoricoAlteracao)
                    .where(HistoricoAlteracao.req_id == ator.req_id)
                )
            ).scalar_one()
        # Com alteração no banco, o evento fica mesmo que o pedido termine em
        # erro (rota que comita e depois responde 502: a mudança aconteceu).
        # Sem alteração, só ação conhecida que deu certo.
        if n == 0 and (acao is None or status >= 400):
            return  # pedido que não mudou nada (prévia, consulta, senha extra)

        pagina = _pagina(scope)
        corpo_resumo = None
        if ator.escrita and not nomes.SEM_CORPO.search(scope["path"]):
            corpo_resumo = resumir_corpo(corpo, _cabecalho(scope, b"content-type") or "")
        if n:
            itens_texto = await congelar(s, ator.req_id)
        else:
            # ação sem mudança no banco: o nome vem do endereço/corpo
            # ("viu a senha da loja kia", "enviou preço de dg053 · ML kia")
            itens_texto = await nomes_da_acao(s, scope.get("path_params") or {}, corpo_resumo)
        s.add(
            HistoricoEvento(
                req_id=ator.req_id,
                ator_id=ator.user_id,
                ator_nome=ator.nome,
                via=ator.via,
                metodo=ator.metodo,
                rota=rota,
                caminho=limpar_texto(_caminho_sem_segredo(scope, rota)),
                tela=nomes.tela_da_pagina(pagina) or nomes.tela_da_api(scope["path"]),
                pagina=pagina,
                acao=frase,
                status=status,
                ip=_cabecalho(scope, b"cf-connecting-ip"),
                corpo=corpo_resumo,
                n_alteracoes=n,
                itens_texto=itens_texto,
            )
        )
        await s.commit()
