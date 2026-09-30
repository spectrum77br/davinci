"""O cron do atendimento: garante os canais, lê as lojas e avisa o prazo.

Entrada por CONSULTA periódica (a cada 2 min, `worker.atendimento_sincronizar`).
Webhook fica para depois — a consulta é a fonte confiável: webhook perdido
num deploy é mensagem perdida; a consulta seguinte acha de novo.

Três cuidados que valem para toda loja:

- CADA CANAL NA SUA SESSÃO e na sua transação: erro na Shopee "kfa" não
  desfaz o que a Shopee "kia" leu, e uma loja lenta não segura as outras
  (Semaphore com `atendimento_sync_concorrencia`).
- TRAVA POR CANAL no Redis (SET NX EX 240): o cron de 2 min, o botão
  "Sincronizar" da tela e um cron atrasado não leem o mesmo canal juntos —
  cada leitura gastaria cota da API e o cursor andaria duas vezes. A trava é
  otimização, não garantia: a idempotência de verdade está no banco
  (gravar.*), então Redis fora do ar não para a leitura.
- ERRO SEM DADO PESSOAL: `ultimo_erro` e o log levam classe, HTTP e código
  da plataforma — nunca a mensagem crua da exceção, que pode trazer o corpo
  da resposta (texto de comprador) ou a URL assinada (token).

`sem_escopo` (401/403, TikTok sem `seller.customer_service`, ML com 403 de
política, Magalu sem o escopo da caixa no token) não é erro passageiro: o
canal só é tentado de novo de hora em hora, até a permissão sair.

Magalu (30/09/2026): uma loja vira TRÊS canais (pergunta, chat e SAC), cada
um na sua rodada, com o mesmo `MagaluClient` da integração — o token é
renovado pelo próprio cliente, sob a trava da linha da integração
(`clientes.PLATAFORMAS_COM_TRAVA_PROPRIA`). O teto de leituras por rodada
de cada canal (`magalu.MAX_LEITURAS_RODADA`) mantém a loja longe do limite
de ~200 leituras/min da Magalu.
"""

from __future__ import annotations

import asyncio
import html
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx
import structlog
from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

import app.db as _db
from app.config import get_settings
from app.models import AtendimentoCanal, AtendimentoConversa, Integration, IntegrationPlatform
from app.redis_client import redis
from app.services.atendimento import clientes, enviar
from app.services.atendimento.constantes import (
    CANAIS_POR_PLATAFORMA,
    CONVERSA_FECHADA,
    MODO_OBSERVAR,
    PLATAFORMAS,
    STATUS_CANAL,
    ResultadoSync,
)
from app.services.telegram import TelegramClient

logger = structlog.get_logger()

SP_TZ = ZoneInfo("America/Sao_Paulo")

STATUS_NOVO = "novo"
STATUS_OK = "ok"
STATUS_SEM_ESCOPO = "sem_escopo"
STATUS_ERRO = "erro"
STATUS_DESLIGADO = "desligado"
# Resultado de uma rodada que NÃO leu (outro processo estava com a trava).
# Só volta no resumo — nunca é gravado em `atendimento_canais.status`.
RESULTADO_PULADO = "pulado"

# Trava por canal. 240 s cabe folgado dentro do tick de 2 min + a rodada
# mais lenta; se o processo morrer, o TTL solta sozinho.
TRAVA_TTL_S = 240
_CHAVE_TRAVA = "atendimento:sync:canal:{}"

# Canal sem permissão é tentado de novo só depois disto.
SEM_ESCOPO_RETENTAR = timedelta(hours=1)

# ── Alerta de prazo ───────────────────────────────────────────────────────
# "Vencendo" = menos de 2 h para o prazo da plataforma. A Amazon mede 24 h
# corridas: o aviso chega com tempo de alguém abrir a fila no celular.
ALERTA_ANTECEDENCIA = timedelta(hours=2)
# Dedupe por conversa + nível + prazo: a mesma conversa avisa uma vez
# "vencendo" e uma vez "vencida"; mensagem nova do cliente = prazo novo =
# aviso novo. 48 h cobre com folga o maior SLA (24 h) depois do vencimento.
ALERTA_DEDUP_TTL_S = 48 * 3600
_CHAVE_ALERTA = "atendimento:alerta:{}:{}:{}"
# Linhas por aviso: a mensagem do Telegram é para triagem na tela de
# bloqueio, não uma lista completa (a lista completa é o filtro da tela).
ALERTA_MAX_LINHAS = 15
ALERTA_MAX_CONVERSAS = 200


# ── Canais ────────────────────────────────────────────────────────────────


def _amazon_configurada() -> bool:
    """Amazon só lê com a caixa de e-mail (IMAP) configurada no servidor.

    Host, usuário e senha: sem qualquer um deles não há login, e o canal
    ficaria alternando entre `novo` (aqui) e `desligado` (no adaptador).
    A regra é a do PRÓPRIO adaptador (`leitura_configurada`, que também tira
    espaço em volta): duas cópias da regra acabariam discordando.
    """
    from app.services.atendimento import amazon_email

    return amazon_email.leitura_configurada()


async def garantir_canais(session: AsyncSession) -> int:
    """Cria os canais que faltam (todos em `observar`); devolve quantos criou.

    Uma linha por (integração × canal) das plataformas lidas por API
    (`PLATAFORMAS`), para integração não arquivada. Loja conectada amanhã ganha canal na rodada
    seguinte, sem ninguém lembrar de cadastrar. Amazon sem IMAP nasce (e
    fica) `desligado`; configurado o IMAP, volta a `novo` sozinha.

    `ON CONFLICT DO NOTHING`: dois workers rodando juntos não brigam no
    UNIQUE (integration_id, canal). Nunca commita.
    """
    amazon_ligada = _amazon_configurada()
    integracoes = (
        await session.execute(
            select(Integration.id, Integration.platform).where(
                Integration.archived_at.is_(None),
                Integration.platform.in_([IntegrationPlatform(p) for p in PLATAFORMAS]),
            )
        )
    ).all()
    existentes = {
        (integration_id, canal)
        for integration_id, canal in (
            await session.execute(select(AtendimentoCanal.integration_id, AtendimentoCanal.canal))
        ).all()
    }
    linhas: list[dict[str, Any]] = []
    for integration_id, platform in integracoes:
        plataforma = getattr(platform, "value", platform)
        for canal_nome in CANAIS_POR_PLATAFORMA.get(plataforma, ()):
            if (integration_id, canal_nome) in existentes:
                continue
            desligado = plataforma == "amazon" and not amazon_ligada
            linhas.append(
                {
                    "id": uuid4(),
                    "integration_id": integration_id,
                    "plataforma": plataforma,
                    "canal": canal_nome,
                    "modo": MODO_OBSERVAR,
                    "status": STATUS_DESLIGADO if desligado else STATUS_NOVO,
                    "cursor": {},
                    "auto_categorias": [],
                }
            )
    criados = 0
    if linhas:
        resultado = await session.execute(
            pg_insert(AtendimentoCanal)
            .values(linhas)
            .on_conflict_do_nothing(index_elements=["integration_id", "canal"])
            .returning(AtendimentoCanal.id)
        )
        criados = len(resultado.all())

    # A caixa da Amazon pode ser configurada (ou tirada) depois de o canal
    # existir: o status acompanha o servidor, para ninguém ficar esperando
    # leitura de um canal que não tem como ler.
    if amazon_ligada:
        await session.execute(
            update(AtendimentoCanal)
            .where(
                AtendimentoCanal.plataforma == "amazon",
                AtendimentoCanal.status == STATUS_DESLIGADO,
            )
            .values(status=STATUS_NOVO)
            .execution_options(synchronize_session=False)
        )
    else:
        await session.execute(
            update(AtendimentoCanal)
            .where(
                AtendimentoCanal.plataforma == "amazon",
                AtendimentoCanal.status != STATUS_DESLIGADO,
            )
            .values(status=STATUS_DESLIGADO)
            .execution_options(synchronize_session=False)
        )
    await session.flush()
    if criados:
        logger.info("atendimento_canais_criados", quantidade=criados)
    return criados


# ── Erro de operação (sem dado pessoal) ───────────────────────────────────

# Sinais de "não tenho permissão" que chegam como texto de RuntimeError
# (os clientes do repo embrulham o HTTP assim): TikTok 105005, ML
# PA_UNAUTHORIZED_*, Shopee error_auth/error_permission.
_SINAIS_SEM_ESCOPO = (
    "105005",
    "pa_unauthorized",
    "error_auth",
    "error_permission",
    "access denied",
    "insufficient_scope",
    "invalid_scope",
)
_RE_HTTP = re.compile(r"(?:HTTP|status[=:]?)\s*(\d{3})\b", re.IGNORECASE)
# Só formatos de CÓDIGO (sem espaço, ASCII): nunca carregam frase de comprador.
# 105xxx é a família de autorização da TikTok (105005 = app sem o escopo).
_RE_CODIGO = re.compile(r"\b(error_[a-z_]+|PA_[A-Z_]+|105\d{3})\b")


def erro_de_operacao(e: BaseException) -> tuple[str, bool]:
    """(texto seguro para `ultimo_erro`/log, é falta de permissão?).

    O texto é montado só com a classe da exceção, o HTTP e o código da
    plataforma. A mensagem crua NUNCA passa: ela pode trazer o corpo da
    resposta (com texto de comprador) ou a URL assinada (com token).
    """
    status: int | None = None
    bruto = str(e)
    if isinstance(e, httpx.HTTPStatusError):
        status = e.response.status_code
        try:
            bruto = f"{bruto} {e.response.text[:500]}"
        except Exception:  # noqa: BLE001, S110 — corpo ilegível não muda o diagnóstico
            pass
    elif isinstance(e, httpx.TimeoutException):
        return "timeout", False
    else:
        achado = _RE_HTTP.search(bruto)
        if achado:
            status = int(achado.group(1))
    codigo = _RE_CODIGO.search(bruto)
    partes = [type(e).__name__]
    if status is not None:
        partes.append(f"HTTP {status}")
    if codigo:
        partes.append(codigo.group(1))
    baixo = bruto.lower()
    sem_escopo = status in (401, 403) or any(s in baixo for s in _SINAIS_SEM_ESCOPO)
    return " · ".join(partes), sem_escopo


def _registrar(canal: AtendimentoCanal, resultado: ResultadoSync) -> None:
    """Saúde do canal a partir do que o adaptador devolveu."""
    agora = datetime.now(UTC)
    status = resultado.status if resultado.status in STATUS_CANAL else STATUS_ERRO
    canal.status = status
    if resultado.nao_lidas is not None:
        canal.nao_lidas_plataforma = resultado.nao_lidas
    if status == STATUS_OK:
        canal.ultimo_ok_em = agora
        if resultado.erro:
            # Rodada que leu, mas com PARTE das conversas falhando (o ML
            # devolve "3 de 20 conversas com erro: HTTP 404 ..."): o canal
            # segue `ok`, e o aviso fica visível em Lojas e modo — senão uma
            # conversa quebrada ficaria invisível para sempre.
            canal.ultimo_erro_em = agora
            canal.ultimo_erro = resultado.erro[:300]
    elif status in (STATUS_ERRO, STATUS_SEM_ESCOPO):
        canal.ultimo_erro_em = agora
        # O adaptador promete texto de operação; o corte é o cinto.
        canal.ultimo_erro = (resultado.erro or status)[:300]


# ── Leitura ───────────────────────────────────────────────────────────────


async def _soltar_trava(chave: str, token: str) -> None:
    """Solta a trava só se ainda for NOSSA (a rodada pode ter passado do TTL)."""
    try:
        await redis.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end",
            1,
            chave,
            token,
        )
    except Exception:  # noqa: BLE001 — o TTL solta sozinho
        logger.warning("atendimento_sync_trava_soltar_falhou", chave=chave)


async def sincronizar_canal(canal_id: UUID) -> ResultadoSync:
    """Uma rodada de leitura de um canal, em sessão própria, sob trava Redis.

    Nunca levanta: erro vira `status` no canal (e no resultado devolvido).
    """
    chave = _CHAVE_TRAVA.format(canal_id)
    token: str | None = uuid4().hex
    try:
        pegou = await redis.set(chave, token, nx=True, ex=TRAVA_TTL_S)
    except Exception as e:  # noqa: BLE001
        # Sem Redis, lê sem trava: o pior caso é ler duas vezes o que o
        # banco já deduplica. Parar a leitura é pior.
        logger.warning(
            "atendimento_sync_trava_indisponivel", canal_id=str(canal_id), err=type(e).__name__
        )
        pegou, token = True, None
    if not pegou:
        logger.info("atendimento_sync_canal_ocupado", canal_id=str(canal_id))
        return ResultadoSync(status=RESULTADO_PULADO)
    try:
        return await _sincronizar_canal(canal_id)
    finally:
        if token is not None:
            await _soltar_trava(chave, token)


async def _sincronizar_canal(canal_id: UUID) -> ResultadoSync:
    # `_db.SessionLocal` lido na hora da chamada (os testes trocam o engine).
    async with _db.SessionLocal() as session:
        canal = await session.get(AtendimentoCanal, canal_id)
        if canal is None:
            return ResultadoSync(status=STATUS_ERRO, erro="canal_inexistente")
        if canal.status == STATUS_DESLIGADO:
            return ResultadoSync(status=STATUS_DESLIGADO)
        if canal.integration_id is None:
            # Loja do robô do Mac mini (Temu/AliExpress, migration 0347): quem
            # a lê é o robô (services/atendimento/robo.py), nunca o cliente da
            # API — a rodada não a escolhe, e chamada direta também não lê.
            return ResultadoSync(status=STATUS_DESLIGADO)
        integration = await session.get(Integration, canal.integration_id)
        if integration is None or integration.archived_at is not None:
            return ResultadoSync(status=STATUS_DESLIGADO)
        plataforma = canal.plataforma
        inicio = datetime.now(UTC)
        try:
            # Amazon lê pela caixa de e-mail (settings), não pela API da loja.
            cliente = (
                None
                if plataforma == "amazon"
                else await clientes.cliente_da_integracao(integration)
            )
            resultado = await enviar.adaptador(plataforma).sincronizar(
                session, canal, integration, cliente
            )
            _registrar(canal, resultado)
            await session.commit()
        except Exception as e:  # noqa: BLE001
            texto, sem_escopo = erro_de_operacao(e)
            resultado = ResultadoSync(
                status=STATUS_SEM_ESCOPO if sem_escopo else STATUS_ERRO, erro=texto
            )
            logger.warning(
                "atendimento_sync_falhou",
                canal_id=str(canal_id),
                plataforma=plataforma,
                erro=texto,
                sem_escopo=sem_escopo,
            )
            # O que a rodada gravou pela metade vai embora; a saúde do canal
            # é gravada numa transação limpa.
            await session.rollback()
            canal = await session.get(AtendimentoCanal, canal_id, populate_existing=True)
            if canal is None:
                return resultado
            _registrar(canal, resultado)
            await session.commit()
        logger.info(
            "atendimento_sync_canal",
            canal_id=str(canal_id),
            plataforma=plataforma,
            status=resultado.status,
            conversas_novas=resultado.conversas_novas,
            conversas_atualizadas=resultado.conversas_atualizadas,
            mensagens_novas=resultado.mensagens_novas,
            nao_lidas=resultado.nao_lidas,
            ms=int((datetime.now(UTC) - inicio).total_seconds() * 1000),
        )
        return resultado


async def _canais_da_rodada(session: AsyncSession) -> list[UUID]:
    """Canais a ler agora: loja ativa, canal ligado, e `sem_escopo` só de hora em hora.

    Quem leu há mais tempo vai primeiro — com o Semaphore, é assim que
    nenhuma loja fica sempre no fim da fila.
    """
    agora = datetime.now(UTC)
    return list(
        (
            await session.execute(
                select(AtendimentoCanal.id)
                .join(Integration, Integration.id == AtendimentoCanal.integration_id)
                .where(
                    Integration.archived_at.is_(None),
                    AtendimentoCanal.plataforma.in_(PLATAFORMAS),
                    AtendimentoCanal.status != STATUS_DESLIGADO,
                    or_(
                        AtendimentoCanal.status != STATUS_SEM_ESCOPO,
                        AtendimentoCanal.ultimo_erro_em.is_(None),
                        AtendimentoCanal.ultimo_erro_em < agora - SEM_ESCOPO_RETENTAR,
                    ),
                )
                .order_by(AtendimentoCanal.ultimo_ok_em.asc().nulls_first())
            )
        )
        .scalars()
        .all()
    )


async def sincronizar_tudo() -> dict:
    """Todos os canais ativos, com concorrência limitada; devolve o resumo da rodada."""
    settings = get_settings()
    async with _db.SessionLocal() as session:
        criados = await garantir_canais(session)
        presos = await enviar.aposentar_envios_presos(session)
        await session.commit()
        ids = await _canais_da_rodada(session)

    semaforo = asyncio.Semaphore(max(1, int(settings.atendimento_sync_concorrencia or 1)))

    async def _um(canal_id: UUID) -> ResultadoSync:
        async with semaforo:
            return await sincronizar_canal(canal_id)

    resultados = await asyncio.gather(*(_um(c) for c in ids), return_exceptions=True)
    resumo: dict[str, int] = {
        "canais": len(ids),
        "canais_criados": criados,
        "envios_presos": presos,
        "ok": 0,
        "erro": 0,
        "sem_escopo": 0,
        "pulados": 0,
        "conversas_novas": 0,
        "conversas_atualizadas": 0,
        "mensagens_novas": 0,
    }
    for canal_id, r in zip(ids, resultados, strict=True):
        if isinstance(r, BaseException):
            # `sincronizar_canal` não levanta; se levantou, é bug — conta e segue.
            resumo["erro"] += 1
            logger.error(
                "atendimento_sync_canal_explodiu",
                canal_id=str(canal_id),
                err=type(r).__name__,
            )
            continue
        if r.status == STATUS_OK:
            resumo["ok"] += 1
        elif r.status == STATUS_SEM_ESCOPO:
            resumo["sem_escopo"] += 1
        elif r.status == RESULTADO_PULADO:
            resumo["pulados"] += 1
        elif r.status != STATUS_DESLIGADO:
            resumo["erro"] += 1
        resumo["conversas_novas"] += r.conversas_novas
        resumo["conversas_atualizadas"] += r.conversas_atualizadas
        resumo["mensagens_novas"] += r.mensagens_novas
    return resumo


# ── Alerta de prazo (Telegram) ────────────────────────────────────────────


def _nivel(prazo: datetime, agora: datetime) -> str:
    return "vencida" if prazo <= agora else "vencendo"


async def _reservar_aviso(chave: str) -> bool:
    """SET NX da chave de dedupe. Redis fora do ar = avisa (avisar duas vezes < perder)."""
    try:
        return bool(await redis.set(chave, "1", nx=True, ex=ALERTA_DEDUP_TTL_S))
    except Exception as e:  # noqa: BLE001
        logger.warning("atendimento_alerta_dedup_falhou", err=type(e).__name__)
        return True


async def _devolver_avisos(chaves: list[str]) -> None:
    """O Telegram falhou: solta as reservas para a próxima rodada tentar de novo."""
    if not chaves:
        return
    try:
        await redis.delete(*chaves)
    except Exception:  # noqa: BLE001 — o TTL solta sozinho (e aí o aviso se perde)
        logger.warning("atendimento_alerta_devolver_falhou", quantidade=len(chaves))


def _link_da_tela(conversa_id: UUID | None = None) -> str:
    """A caixa do DaVinci (`app_url`); com id, já aberta na conversa (`?conversa=`)."""
    base = (get_settings().app_url or "").rstrip("/")
    return f"{base}/atendimento" + (f"?conversa={conversa_id}" if conversa_id else "")


def _linha_do_aviso(c: AtendimentoConversa, prazo: datetime, nivel: str) -> str:
    """Uma linha por conversa, SEM texto nem nome de comprador.

    Termina num link que abre a conversa direto na tela: quem recebe no
    celular chega na resposta com um toque.
    """
    icone = "🔴" if nivel == "vencida" else "🟠"
    local = prazo.astimezone(SP_TZ).strftime("%d/%m %H:%M")
    conta = html.escape(c.conta or "loja?")
    pedido = f" · pedido {html.escape(c.pedido_marketplace)}" if c.pedido_marketplace else ""
    quando = f"venceu {local}" if nivel == "vencida" else f"vence {local}"
    onde = f"{html.escape(c.plataforma)} {html.escape(c.canal)}"
    abrir = f' · <a href="{html.escape(_link_da_tela(c.id), quote=True)}">abrir</a>'
    return f"{icone} {onde} · {conta}{pedido} · {quando}{abrir}"


async def alertar_prazos(session: AsyncSession) -> int:
    """Avisa no Telegram as conversas com prazo vencendo (< 2 h) ou vencido.

    Só com `atendimento_alerta_telegram`. Um aviso por rodada, agrupado (a
    fila inteira mora na tela, com o filtro "vencendo"); cada conversa entra
    uma vez por nível. Devolve quantas conversas entraram no aviso. Nunca
    levanta por causa do Telegram.
    """
    if not get_settings().atendimento_alerta_telegram:
        return 0
    agora = datetime.now(UTC)
    base = select(AtendimentoConversa).where(
        AtendimentoConversa.aguardando_resposta.is_(True),
        AtendimentoConversa.prazo_resposta_em.is_not(None),
        AtendimentoConversa.situacao != CONVERSA_FECHADA,
        AtendimentoConversa.sem_resposta_necessaria.is_(False),
    )
    # DUAS consultas, cada uma com o seu teto. Numa só, pelo prazo mais
    # antigo, as vencidas de semanas atrás (o "ok, obrigado" que ficou na
    # fila, a primeira leitura de 7 dias) enchiam o LIMIT; o dedupe as
    # descartava e a conversa que vence em 1 h nem era lida — nenhum aviso.
    vencendo = (
        (
            await session.execute(
                base.where(
                    AtendimentoConversa.prazo_resposta_em >= agora,
                    AtendimentoConversa.prazo_resposta_em < agora + ALERTA_ANTECEDENCIA,
                )
                .order_by(AtendimentoConversa.prazo_resposta_em.asc())
                .limit(ALERTA_MAX_CONVERSAS)
            )
        )
        .scalars()
        .all()
    )
    # Vencidas só as RECENTES (dentro do TTL do dedupe), mais nova primeiro:
    # a mais velha já foi avisada; depois do TTL ela não volta a avisar.
    vencidas = (
        (
            await session.execute(
                base.where(
                    AtendimentoConversa.prazo_resposta_em < agora,
                    AtendimentoConversa.prazo_resposta_em
                    >= agora - timedelta(seconds=ALERTA_DEDUP_TTL_S),
                )
                .order_by(AtendimentoConversa.prazo_resposta_em.desc())
                .limit(ALERTA_MAX_CONVERSAS)
            )
        )
        .scalars()
        .all()
    )
    conversas = [*vencidas, *vencendo]
    novas: list[tuple[AtendimentoConversa, datetime, str]] = []
    reservadas: list[str] = []
    for c in conversas:
        prazo = c.prazo_resposta_em
        if prazo.tzinfo is None:
            prazo = prazo.replace(tzinfo=UTC)
        nivel = _nivel(prazo, agora)
        chave = _CHAVE_ALERTA.format(c.id, nivel, int(prazo.timestamp()))
        if await _reservar_aviso(chave):
            novas.append((c, prazo, nivel))
            reservadas.append(chave)
    if not novas:
        return 0

    vencidas = sum(1 for _c, _p, n in novas if n == "vencida")
    linhas = [
        f"⏰ <b>Atendimento</b>: {len(novas)} conversa(s) sem resposta "
        f"({vencidas} vencida(s), {len(novas) - vencidas} vencendo)"
    ]
    linhas += [_linha_do_aviso(c, p, n) for c, p, n in novas[:ALERTA_MAX_LINHAS]]
    if len(novas) > ALERTA_MAX_LINHAS:
        linhas.append(f"… e mais {len(novas) - ALERTA_MAX_LINHAS} — filtro “vencendo” na tela")
    linhas.append(f'— <a href="{html.escape(_link_da_tela(), quote=True)}">abrir o Atendimento</a>')
    try:
        enviado = await TelegramClient().safe_send("\n".join(linhas))
    except Exception as e:  # noqa: BLE001
        logger.warning("atendimento_alerta_falhou", err=type(e).__name__)
        enviado = False
    if not enviado:
        await _devolver_avisos(reservadas)
        return 0
    logger.info("atendimento_alerta_prazos", conversas=len(novas), vencidas=vencidas)
    return len(novas)
