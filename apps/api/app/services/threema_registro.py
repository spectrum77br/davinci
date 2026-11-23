"""Registro de cada envio do Threema (Cadastros › Envios Threema, tarefa 16, C1b).

Duas peças, SEM mudar a assinatura de nada:

1. A MARCA. Cada ponto de envio do servidor diz de qual envio do catálogo
   (`threema_catalogo.K`) é a mensagem, envolvendo a chamada:

       with threema_registro.envio(K.LOGISTICA_VIGIA_AUTOMACAO):
           await threema.ThreemaClient(contexto="logistica").send_to_all(texto, destinos)

   A marca vive numa ContextVar: atravessa os `await` e as funções do meio
   (wrapper como `avisar_graves` é marcado por quem o chama) e é copiada para
   as tarefas criadas dentro do bloco. Marcas aninhadas: vale a MAIS INTERNA.
   A marca não muda destinatário, remetente nem texto: é inerte.

2. A GRAVAÇÃO. `ThreemaClient.send_to_all` (services/threema.py) chama
   `gravar(...)` no fim de toda chamada — com sucesso, com falha de um
   destinatário, com erro de configuração (1 linha, sem destinatário) ou com
   erro de rede no meio (o que já foi tentado). Uma linha por destinatário em
   `threema_envio_registro`, todas com o mesmo `lote_id`.

   A gravação é de MELHOR ESFORÇO e nunca levanta: sessão própria
   (`session_scope`), teto de 3 s, import tardio dos modelos. Banco fora,
   tabela ausente ou lento = o envio segue igual e fica só o log
   `threema_registro_falhou`. `THREEMA_REGISTRO_ATIVO=false` desliga.

   DISJUNTOR: depois de uma falha (timeout ou erro), o registro fica PULADO
   por 60 s no processo (o log `threema_registro_falhou` leva `pausa_s`; na
   volta, `threema_registro_retomado` diz quantos lotes ficaram sem linha). Sem ele,
   com o banco degradado, cada `send_to_all` ganharia até +3 s — e os pontos
   que mandam em laço (Informar da Margem, 1 mensagem por pedido, dentro da
   requisição; avisos da Amazon e das Devoluções; Conferência) somariam
   isso. Com ele, o atraso total fica em ~3 s por minuto, não por envio.

Quem disparou vem do Histórico (`historico.contexto.ator_atual`): com pessoa
identificada no pedido, origem `manual` (com `ator_*` e `req_id`); sem pessoa
(worker, robô, rota de agente), `automatico`. A marca pode forçar `teste`.

Nunca grava o segredo nem o texto inteiro: só a 1ª linha (`titulo`, 160) e o
tamanho em bytes. Os nomes de quem recebe saem do cadastro de Usuários, dos
contatos avulsos (Ouvidoria › Robôs) e do THREEMA_RECIPIENT_NAMES, nesta ordem.

Sem marca, `envio_chave` fica NULL — em produção isso quer dizer "ponto de
envio que o levantamento não pegou" (o teste estático
`tests/test_threema_registro.py` impede que um novo nasça sem marca).
"""

from __future__ import annotations

import asyncio
import socket
import time
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

import structlog

from app.config import get_settings

logger = structlog.get_logger()

ORIGENS = ("automatico", "manual", "teste")
VIAS = ("cadastro", "legado", "cache", "forcado", "env")
# Guarda do registro (a limpeza diária do worker apaga o que passou disto).
DIAS_GUARDA = 180
_TIMEOUT_S = 3.0
# Disjuntor: depois de uma falha, quantos segundos o registro fica pulado.
_PAUSA_S = 60.0
_TAM_ESCOPO = 80
_TAM_TITULO = 160
_TAM_ERRO = 200
_TAM_NOME = 120


@dataclass(frozen=True)
class Marca:
    chave: str | None
    escopo: str | None = None
    origem: str | None = None
    via: str | None = None


@dataclass(frozen=True)
class Tentativa:
    """O resultado de um destinatário (ou do envio inteiro, quando nem chegou
    a tentar ninguém: `destinatario=None`)."""

    destinatario: str | None
    ok: bool
    http_status: int | None = None
    message_id: str | None = None
    erro: str | None = None


_marca: ContextVar[Marca | None] = ContextVar("threema_envio_marca", default=None)

# Disjuntor do processo (até quando, em `_relogio()`, o registro é pulado).
_pausado_ate = 0.0
_pulados = 0


def _relogio() -> float:
    """Relógio do disjuntor (separado para o teste adiantar sem mexer no
    relógio do asyncio)."""
    return time.monotonic()


def reiniciar_disjuntor() -> None:
    """Fecha o disjuntor (os testes chamam entre um caso e outro)."""
    global _pausado_ate, _pulados
    _pausado_ate = 0.0
    _pulados = 0


def marca_atual() -> Marca | None:
    return _marca.get()


@contextmanager
def envio(
    chave: str | None,
    *,
    escopo: object | None = None,
    origem: str | None = None,
    via: str | None = None,
) -> Iterator[Marca]:
    """Marca os envios feitos dentro do bloco como sendo do envio `chave`.

    `escopo`: o recorte dentro do envio (item 1: o id da regra de status).
    `origem`: força `teste` (ou `manual`/`automatico`); None = deduz do ator.
    `via`: de onde veio a lista (fases seguintes); None = antes do Cadastro.
    Valor fora da lista vira None com aviso no log — a marca nunca derruba
    o envio."""
    if origem is not None and origem not in ORIGENS:
        logger.warning("threema_registro_origem_invalida", origem=origem, chave=chave)
        origem = None
    if via is not None and via not in VIAS:
        logger.warning("threema_registro_via_invalida", via=via, chave=chave)
        via = None
    m = Marca(
        chave=chave or None,
        escopo=str(escopo)[:_TAM_ESCOPO] if escopo not in (None, "") else None,
        origem=origem,
        via=via,
    )
    token = _marca.set(m)
    try:
        yield m
    finally:
        _marca.reset(token)


def _titulo(texto: str) -> str | None:
    for linha in (texto or "").splitlines():
        linha = linha.strip()
        if linha:
            return linha[:_TAM_TITULO]
    return None


def _limpar_erro(erro: str | None, segredos: Sequence[str]) -> str | None:
    if not erro:
        return None
    for s in segredos:
        if s:
            erro = erro.replace(s, "***")
    return erro[:_TAM_ERRO]


def _host() -> str | None:
    try:
        return socket.gethostname()[:60] or None
    except Exception:  # noqa: BLE001
        return None


Nome = tuple[str, uuid.UUID | None, uuid.UUID | None]


async def _nomes(session, ids: set[str]) -> dict[str, Nome]:
    """{ID: (nome, user_id, contato_id)}. Usuário do cadastro primeiro (o
    ativo vence o suspenso), depois o contato externo ativo (com o id dele em
    `threema_contato`, para o registro continuar ligado ao contato se ele for
    renomeado), depois o nome do .env."""
    from sqlalchemy import func, select

    from app.models import User, UserStatus
    from app.services import threema

    out: dict[str, Nome] = {}
    if not ids:
        return out
    rows = (
        await session.execute(
            select(User.id, User.name, User.email, User.threema, User.status, User.disabled_at)
            .where(func.upper(func.trim(User.threema)).in_(sorted(ids)))
            .order_by(User.created_at)
        )
    ).all()
    ativos = sorted(
        rows, key=lambda r: not (r.status == UserStatus.ACTIVE and r.disabled_at is None)
    )
    for r in ativos:
        rid = (r.threema or "").strip().upper()
        if rid in ids and rid not in out:
            out[rid] = ((r.name or r.email or "")[:_TAM_NOME], r.id, None)
    faltam = ids - set(out)
    if faltam:
        linhas = await threema._contatos_ativos(session)
        if linhas is None:  # antes do alembic (0506): a linha antiga, sem id
            contatos = [(d["id"], d["nome"], None) for d in await threema.contatos(session)]
        else:
            contatos = [(c.threema_id, c.nome, c.id) for c in linhas]
        for rid, nome, contato_id in contatos:
            if rid in faltam and rid not in out:
                out[rid] = (nome[:_TAM_NOME], None, contato_id)
    faltam = ids - set(out)
    if faltam:
        s = get_settings()
        for d in threema.parse_recipient_directory(s.threema_recipient_names, None):
            if d["id"] in faltam and d["id"] not in out:
                out[d["id"]] = (d["nome"][:_TAM_NOME], None, None)
    return out


def _linhas(
    *,
    marca: Marca | None,
    contexto: str | None,
    canal: str | None,
    texto: str,
    tentativas: Sequence[Tentativa],
    segredos: Sequence[str],
    nomes: dict[str, Nome],
) -> list[dict]:
    from app.historico.contexto import ator_atual

    ator = ator_atual()
    pessoa = ator if ator is not None and ator.user_id is not None else None
    origem = (marca.origem if marca else None) or ("manual" if pessoa else "automatico")
    lote = uuid.uuid4()
    titulo = _titulo(texto)
    tamanho = len((texto or "").encode("utf-8"))
    host = _host()
    ator_nome = ((pessoa.nome or "")[:_TAM_NOME] or None) if pessoa else None
    base = {
        "lote_id": lote,
        "envio_chave": marca.chave if marca else None,
        "escopo": marca.escopo if marca else None,
        "contexto": (contexto or None) and contexto[:20],
        "canal": (canal or None) and canal[:20],
        "onde_roda": "servidor",
        "origem": origem,
        "via": marca.via if marca else None,
        "ator_id": pessoa.user_id if pessoa else None,
        "ator_nome": ator_nome,
        "req_id": ator.req_id if ator is not None else None,
        "titulo": titulo,
        "bytes": tamanho,
        "host": host,
    }
    out = []
    for t in tentativas:
        rid = (t.destinatario or "").strip().upper()[:8] or None
        nome, user_id, contato_id = nomes.get(rid, (None, None, None)) if rid else (None,) * 3
        out.append(
            {
                **base,
                "destinatario_id": rid,
                "destinatario_nome": nome,
                "user_id": user_id,
                "contato_id": contato_id,
                "ok": bool(t.ok),
                "http_status": t.http_status,
                "erro": _limpar_erro(t.erro, segredos),
                "message_id": (t.message_id or None) and t.message_id[:32],
            }
        )
    return out


async def _inserir(marca, contexto, canal, texto, tentativas, segredos) -> int:
    from sqlalchemy import insert, text

    from app import db as _db
    from app.models import ThreemaEnvioRegistro

    async with _db.session_scope() as session:
        await session.execute(text("SET LOCAL statement_timeout = '3s'"))
        ids = {(t.destinatario or "").strip().upper()[:8] for t in tentativas if t.destinatario}
        nomes = await _nomes(session, ids)
        linhas = _linhas(
            marca=marca,
            contexto=contexto,
            canal=canal,
            texto=texto,
            tentativas=tentativas,
            segredos=segredos,
            nomes=nomes,
        )
        if linhas:
            await session.execute(insert(ThreemaEnvioRegistro), linhas)
        return len(linhas)


async def gravar(
    *,
    contexto: str | None,
    canal: str | None,
    texto: str,
    tentativas: Sequence[Tentativa],
    segredos: Sequence[str] = (),
) -> int:
    """Grava as tentativas de UMA chamada de envio (um lote). Devolve quantas
    linhas gravou (0 quando desligado, quando falhou ou com o disjuntor
    aberto). NUNCA levanta."""
    global _pausado_ate, _pulados
    if not tentativas:
        return 0
    try:
        if not get_settings().threema_registro_ativo:
            return 0
        if _relogio() < _pausado_ate:
            _pulados += 1
            return 0
        if _pulados:
            logger.info("threema_registro_retomado", pulados=_pulados)
            _pulados = 0
        marca = _marca.get()
        async with asyncio.timeout(_TIMEOUT_S):
            return await _inserir(marca, contexto, canal, texto, list(tentativas), segredos)
    except Exception as exc:  # noqa: BLE001 — registro nunca derruba o envio
        _pausado_ate = _relogio() + _PAUSA_S
        logger.warning(
            "threema_registro_falhou",
            erro=type(exc).__name__,
            detalhe=_limpar_erro(str(exc), segredos),
            pausa_s=_PAUSA_S,
        )
        return 0


async def limpar(session, *, dias: int = DIAS_GUARDA) -> int:
    """Apaga o registro com mais de `dias` (a limpeza diária do worker).
    Commit fica com quem chama."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import delete

    from app.models import ThreemaEnvioRegistro

    corte = datetime.now(UTC) - timedelta(days=dias)
    r = await session.execute(
        delete(ThreemaEnvioRegistro).where(ThreemaEnvioRegistro.criado_em < corte)
    )
    return int(r.rowcount or 0)
