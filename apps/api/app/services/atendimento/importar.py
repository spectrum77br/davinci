"""Importação do histórico do atendimento — UMA VEZ, depois da aprovação do Eduardo.

A caixa nasce vazia: a leitura periódica (`sync`) só olha os últimos 7 dias. A
IA aprende com a resposta que a equipe dá (inclusive a dada fora do DaVinci,
no Duoke), e o cartão "Cliente" precisa do histórico de compras e avaliações.
Esta importação traz, de uma vez, os últimos N dias (padrão 90):

  Shopee  chat        — lista de conversas do mais novo para trás
                        (`direction=older`, a ordem medida em 28/09) até o
                        começo da janela + `get_message` paginado de cada uma.
          pedidos     — `get_order_list` por dia (criação) + `get_order_detail`
                        em lotes de 50 → índice `atendimento_pedidos_comprador`.
          avaliacoes  — `get_comment` até o começo da janela → índice
                        `atendimento_avaliacoes_loja`.
  ML      perguntas   — as RESPONDIDAS (`search ANSWERED`); as sem resposta
                        são da leitura periódica.
          pos_venda   — os packs dos pedidos da janela (`/orders/search`),
                        lidos com `mark_as_read=false` (fixo no cliente).

Tudo é gravado pela mesma porta da leitura (`gravar.*`, e as rotinas do
adaptador do ML): mensagem da loja que não é nossa entra como `externo`, a do
comprador como `cliente`, e rodar de novo não duplica nada.

As travas, e por que cada uma:

- NUNCA MARCA COMO LIDO. Shopee: o cliente não tem `read_conversation`. ML: o
  `mark_as_read=false` é fixo em `mensagens_do_pack`. Enquanto o Duoke estiver
  ligado, o "não lido" é da equipe.
- SEM IA E SEM ALERTA. Nada aqui chama a IA nem o Telegram. A conversa que a
  importação CRIA e que ficou esperando resposta há mais de 7 dias (a janela da
  leitura periódica e da IA) entra FECHADA — senão centenas de "vencidas" de
  meses atrás tomariam a fila. A que termina com a resposta da loja entra
  `respondida` (o `gravar` deduz). A dos últimos 7 dias é da leitura periódica
  (com a leitura ligada, ela já existe quando a importação passa).
- RECUSA SEM A LEITURA LIGADA (`atendimento_leitura_ativa`), a não ser com
  `forcar`: importar com a leitura desligada criaria uma caixa que ninguém
  mantém em dia.
- TETO DE CHAMADAS POR MINUTO (`por_minuto`, padrão 60): o Client ID do ML e a
  cota da Shopee são os mesmos da importação de pedidos e dos robôs.
- RETOMÁVEL. Cada etapa de cada loja guarda no Redis onde parou (cursor da
  lista, offset, dia, cursor das avaliações) e o começo da janela; rodar de
  novo continua dali, e a etapa concluída é pulada. Sem Redis, roda do começo
  (o banco deduplica). O item que falhou (conversa, pack) fica PENDENTE no
  estado e é tentado de novo — no fim da mesma execução e nas seguintes, até
  `MAX_TENTATIVAS_ITEM`; a etapa só se diz concluída sem pendente (o que
  passou do teto de tentativas fica no log e em `desistidos`).
- UMA IMPORTAÇÃO POR VEZ (trava no Redis, renovada enquanto anda): o comando
  e o job do worker juntos dobrariam o teto de chamadas na mesma cota.
- CANAL `desligado` NÃO ENTRA: é o interruptor da equipe quando a plataforma
  devolve 429. A etapa daquele canal é pulada; a loja com tudo desligado nem
  abre o cliente.
- `seco` SÓ CONTA: lê as listas (conversas, perguntas, pedidos, avaliações) e
  diz quantas são e quantas já existem — não grava nada, nem o estado.

Log e resumo só com ids e contagens — nunca texto nem nome de comprador.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.db as _db
from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    Integration,
    IntegrationPlatform,
)
from app.redis_client import redis
from app.services.atendimento import clientes, enriquecer, gravar, indexar, indice
from app.services.atendimento import ml as ml_adaptador
from app.services.atendimento import shopee as shopee_adaptador
from app.services.atendimento.constantes import (
    AUTOR_LOJA,
    CANAL_CHAT,
    CANAL_PERGUNTA,
    CANAL_POS_VENDA,
    CONVERSA_FECHADA,
    MSG_FALHOU,
    ResultadoSync,
)

logger = structlog.get_logger()

PLATAFORMAS_IMPORTADAS = ("shopee", "ml")

DIAS_PADRAO = 90
DIAS_MAX = 365
CHAMADAS_POR_MINUTO = 60

# A janela da leitura periódica (primeira leitura da Shopee) e da IA
# (`ia.JANELA_PENDENTES`): o que esperou mais que isso é histórico.
JANELA_VIVA = timedelta(days=7)

# ── Shopee ────────────────────────────────────────────────────────────────
# `direction=older` SEM horário = as mais novas primeiro, e com
# `next_timestamp_nano=X` = as anteriores a X (medido em 28/09). `latest`
# sem horário devolve as de 2023 — nunca no topo.
DIRECAO_LISTA = "older"
PAGINA_CONVERSAS = 25  # 60 devolveu lista vazia em algumas lojas
PAGINA_MENSAGENS = 25
MAX_PAGINAS_LISTA = 400  # 10 mil conversas
MAX_PAGINAS_MENSAGENS = 20  # 500 mensagens por conversa
FATIA_PEDIDOS_S = 24 * 3600
PAGINA_PEDIDOS_SHOPEE = 100
MAX_PAGINAS_POR_FATIA = 50
LOTE_DETALHE = 50
MAX_PAGINAS_AVALIACOES = 40

# ── Mercado Livre ─────────────────────────────────────────────────────────
PAGINA_PERGUNTAS = 50
MAX_PAGINAS_PERGUNTAS = 40
PAGINA_PEDIDOS_ML = 50
MAX_PAGINAS_PEDIDOS_ML = 200

# ── Estado (retomada) ─────────────────────────────────────────────────────
ESTADO_TTL_S = 30 * 24 * 3600
_CHAVE_ESTADO = "atendimento:importar:{}:{}:{}"
# Tentativas de um item que falhou (a da lista + as de depois) antes de a
# importação desistir dele. Um item quebrado não segura a etapa para sempre;
# um erro passageiro ("error_server: busy") não perde a conversa.
MAX_TENTATIVAS_ITEM = 3

# ── Uma importação por vez ────────────────────────────────────────────────
# Validade curta, RENOVADA enquanto a importação anda: processo morto solta
# a vez em minutos; importação de horas nunca a perde.
CHAVE_TRAVA = "atendimento:importar:rodada"
TRAVA_TTL_S = 15 * 60
TRAVA_RENOVAR_S = 5 * 60

# O canal que cada etapa lê. Na Shopee, pedidos e avaliações seguem o chat
# (o canal único da loja): desligado, a loja inteira sai.
_CANAL_DA_ETAPA = {
    "chat": CANAL_CHAT,
    "pedidos": CANAL_CHAT,
    "avaliacoes": CANAL_CHAT,
    "perguntas": CANAL_PERGUNTA,
    "pos_venda": CANAL_POS_VENDA,
}

FabricaCliente = Callable[[Integration], Awaitable[Any]]


class ImportacaoRecusada(Exception):  # noqa: N818 — nome do domínio, como EnvioRecusado
    """A importação não roda: `code` diz por quê (leitura_desligada, dias_invalido...)."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def _agora() -> datetime:
    """Relógio do módulo (os testes trocam)."""
    return datetime.now(UTC)


def _erro(exc: BaseException) -> str:
    """Texto de operação: classe, HTTP e código — nunca o corpo (pode ter texto de comprador)."""
    if isinstance(exc, ml_adaptador.ErroML):
        return exc.texto
    from app.services.atendimento.sync import erro_de_operacao

    return erro_de_operacao(exc)[0]


def _id(bruto: Any) -> str:
    return "" if bruto is None or isinstance(bruto, bool) else str(bruto).strip()


def _int(bruto: Any) -> int | None:
    try:
        return int(bruto)
    except (TypeError, ValueError):
        return None


# ── Teto de chamadas ──────────────────────────────────────────────────────


class Limitador:
    """No máximo `por_minuto` chamadas em qualquer janela de 60 s (janela deslizante)."""

    def __init__(
        self,
        por_minuto: int,
        *,
        relogio: Callable[[], float] = time.monotonic,
        dormir: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self.por_minuto = max(1, int(por_minuto))
        self.total = 0
        self._relogio = relogio
        self._dormir = dormir
        self._feitas: deque[float] = deque()

    def _limpar(self, agora: float) -> None:
        while self._feitas and agora - self._feitas[0] >= 60.0:
            self._feitas.popleft()

    async def esperar(self) -> None:
        agora = self._relogio()
        self._limpar(agora)
        if len(self._feitas) >= self.por_minuto:
            espera = 60.0 - (agora - self._feitas[0])
            if espera > 0:
                await self._dormir(espera)
            agora = self._relogio()
            self._limpar(agora)
            while len(self._feitas) >= self.por_minuto:
                self._feitas.popleft()
        self._feitas.append(agora)
        self.total += 1


class ClienteLimitado:
    """O cliente da loja com TODA chamada assíncrona passando pelo `Limitador`.

    Atributos (`shop_id`, `creds`) passam direto. Chamadas que o cliente faz
    por dentro (renovar token, repetir no 429) não contam — o teto é das
    chamadas que a importação decide fazer.
    """

    def __init__(self, cliente: Any, limitador: Limitador) -> None:
        self._cliente = cliente
        self._limitador = limitador
        self.chamadas: list[str] = []

    def __getattr__(self, nome: str) -> Any:
        alvo = getattr(self._cliente, nome)
        if not inspect.iscoroutinefunction(alvo):
            return alvo

        async def chamada(*args: Any, **kwargs: Any) -> Any:
            await self._limitador.esperar()
            self.chamadas.append(nome)
            return await alvo(*args, **kwargs)

        return chamada


# ── Estado da etapa (Redis) ───────────────────────────────────────────────


@dataclass
class _Estado:
    """Onde a etapa de uma loja parou. `chave=None` = não guarda (seco, ou sem Redis)."""

    chave: str | None
    dados: dict

    @classmethod
    async def abrir(
        cls, integration_id: UUID, etapa: str, dias: int, desde: datetime, *, guardar: bool
    ) -> _Estado:
        base = {"desde": desde.isoformat(timespec="seconds"), "concluida": False}
        if not guardar:
            return cls(None, base)
        chave = _CHAVE_ESTADO.format(integration_id, etapa, dias)
        try:
            bruto = await redis.get(chave)
        except Exception as exc:  # noqa: BLE001 — sem Redis, roda do começo
            logger.warning("atendimento_importar_estado_indisponivel", erro=type(exc).__name__)
            return cls(None, base)
        try:
            salvo = json.loads(bruto) if bruto else None
        except (TypeError, ValueError):
            salvo = None
        if isinstance(salvo, dict) and indice.de_iso(salvo.get("desde")) is not None:
            return cls(chave, salvo)
        return cls(chave, base)

    @property
    def desde(self) -> datetime:
        return indice.de_iso(self.dados.get("desde")) or _agora()

    @property
    def concluida(self) -> bool:
        return bool(self.dados.get("concluida"))

    async def salvar(self, **mudancas: Any) -> None:
        self.dados = {**self.dados, **mudancas, "atualizado_em": _agora().isoformat()}
        if self.chave is None:
            return
        try:
            await redis.set(self.chave, json.dumps(self.dados), ex=ESTADO_TTL_S)
        except Exception as exc:  # noqa: BLE001 — perde a retomada, não a importação
            logger.warning("atendimento_importar_estado_falhou", erro=type(exc).__name__)


# ── A rodada de uma etapa ─────────────────────────────────────────────────


@dataclass
class _Etapa:
    session: AsyncSession
    integration: Integration
    cliente: Any
    canais: dict[str, AtendimentoCanal]
    estado: _Estado
    agora: datetime
    seco: bool
    contagem: dict[str, int] = field(default_factory=dict)
    # Refs (conversa, pack) que falharam nesta passada — viram pendentes.
    falhados: list[str] = field(default_factory=list)

    @property
    def desde(self) -> datetime:
        return self.estado.desde

    def contar(self, chave: str, n: int = 1) -> None:
        if n:
            self.contagem[chave] = self.contagem.get(chave, 0) + n

    async def desfazer(self) -> None:
        """Rollback do item que falhou — e relê a loja e os canais que o rollback expirou.

        Sem reler, o próximo item tocaria num objeto expirado, e numa sessão
        assíncrona isso é erro (carga preguiçosa fora do greenlet).
        """
        await self.session.rollback()
        await self.session.refresh(self.integration)
        for canal in self.canais.values():
            await self.session.refresh(canal)


# Itens seguidos com erro antes de a etapa parar (e ser retomada depois): um
# item quebrado não trava a loja; a loja fora do ar não gasta a cota à toa.
MAX_FALHAS_SEGUIDAS = 5


async def _item(
    e: _Etapa, falhas: list[int], chave_log: str, ref: str, corrotina: Awaitable[None]
) -> None:
    """Roda a importação de UM item (conversa, pack); erro da loja pula o item.

    `falhas` é o contador de erros SEGUIDOS (lista de um, para mutar): na
    `MAX_FALHAS_SEGUIDAS`-ésima, o erro sobe e a etapa para — o estado guarda
    a página, e a próxima execução tenta de novo dali. O item que falhou vai
    para `e.falhados` (e daí para os pendentes da etapa).
    """
    try:
        await corrotina
    except Exception as exc:  # noqa: BLE001 — ver docstring
        await e.desfazer()
        falhas[0] += 1
        e.contar("itens_com_erro")
        if ref:
            e.falhados.append(ref)
        logger.warning(chave_log, integration_id=str(e.integration.id), ref=ref, erro=_erro(exc))
        if falhas[0] >= MAX_FALHAS_SEGUIDAS:
            raise
    else:
        falhas[0] = 0


def _pendentes(e: _Etapa) -> dict[str, int]:
    """Os itens que falharam nas execuções anteriores da etapa (ref → tentativas)."""
    bruto = e.estado.dados.get("pendentes")
    if not isinstance(bruto, dict):
        return {}
    return {
        str(ref): n
        for ref, n in bruto.items()
        if isinstance(n, int) and not isinstance(n, bool) and n > 0
    }


def _com_os_falhados(e: _Etapa, pendentes: dict[str, int]) -> dict[str, int]:
    """Os pendentes mais o que falhou nesta passada (`e.falhados`, que esvazia)."""
    novo = dict(pendentes)
    for ref in e.falhados:
        novo[ref] = novo.get(ref, 0) + 1
    e.falhados.clear()
    return novo


async def _retentar(
    e: _Etapa,
    falhas: list[int],
    chave_log: str,
    pendentes: dict[str, int],
    fazer: Callable[[str], Awaitable[None]],
) -> dict[str, int]:
    """Tenta de novo cada item pendente; devolve os que ainda faltam.

    Sem isto, o item que falhou uma vez era pulado para sempre: o cursor
    andava, a etapa se dizia concluída e a leitura periódica (7 dias) nunca
    voltava na conversa antiga. Quem chega a `MAX_TENTATIVAS_ITEM` sai da
    lista — fica no log (com o ref) e na conta `desistidos`.
    """
    restam: dict[str, int] = {}
    for ref, tentativas in pendentes.items():
        await _item(e, falhas, chave_log, ref, fazer(ref))
        if not e.falhados:
            e.contar("recuperados")
            continue
        e.falhados.clear()
        if tentativas + 1 >= MAX_TENTATIVAS_ITEM:
            e.contar("desistidos")
            logger.warning(
                "atendimento_importar_item_desistido",
                integration_id=str(e.integration.id),
                ref=ref,
                tentativas=tentativas + 1,
            )
            continue
        restam[ref] = tentativas + 1
    return restam


async def _fechar_etapa(e: _Etapa, pendentes: dict[str, int]) -> None:
    """Conclui a etapa — ou, com pendente, deixa para a próxima execução."""
    if pendentes:
        await e.estado.salvar(pendentes=pendentes)
        e.contar("pendentes", len(pendentes))
        return
    await e.estado.salvar(pendentes={}, concluida=True)


async def _conversa(
    session: AsyncSession, integration_id: UUID, canal: str, externo_id: str
) -> AtendimentoConversa | None:
    return (
        await session.execute(
            select(AtendimentoConversa)
            .where(
                AtendimentoConversa.integration_id == integration_id,
                AtendimentoConversa.canal == canal,
                AtendimentoConversa.externo_id == externo_id,
            )
            .limit(1)
        )
    ).scalar_one_or_none()


async def _mensagem_gravada(session: AsyncSession, conversa_id: UUID, externo_id: str) -> bool:
    return (
        await session.scalar(
            select(AtendimentoMensagem.id)
            .where(
                AtendimentoMensagem.conversa_id == conversa_id,
                AtendimentoMensagem.externo_id == externo_id,
            )
            .limit(1)
        )
    ) is not None


def assentar(conversa: AtendimentoConversa, criada: bool, agora: datetime) -> bool:
    """A conversa que a importação CRIOU: marca a origem e fecha o que esperou demais.

    Devolve se fechou. Só mexe na criada agora — conversa que já existia é da
    leitura periódica e da equipe. Esperando resposta há mais de 7 dias
    (inclusive bloqueada pelo ML por prazo) = histórico: entra `fechada`, fora
    da fila, do alerta e da IA. Se o cliente escrever de novo, o `gravar`
    reabre sozinho.
    """
    if not criada:
        return False
    ultima = conversa.ultima_do_cliente_em
    if ultima is not None and ultima.tzinfo is None:
        ultima = ultima.replace(tzinfo=UTC)
    fechar = bool(
        conversa.aguardando_resposta and ultima is not None and ultima < agora - JANELA_VIVA
    )
    conversa.dados = {
        **(conversa.dados or {}),
        "importacao": {"em": agora.isoformat(timespec="seconds"), "fechada": fechar},
    }
    if fechar:
        conversa.situacao = CONVERSA_FECHADA
        gravar.recalcular(conversa)
    return fechar


# ── Shopee: chat ──────────────────────────────────────────────────────────


async def _mensagens_shopee(e: _Etapa, conversation_id: str) -> list[dict]:
    """As mensagens da conversa, das mais novas para trás, até passar do começo da janela."""
    coletadas: dict[str, dict] = {}
    offset: str | None = None
    for _ in range(MAX_PAGINAS_MENSAGENS):
        resp = await e.cliente.chat_mensagens_pagina(
            conversation_id, offset=offset, page_size=PAGINA_MENSAGENS
        )
        resp = resp if isinstance(resp, dict) else {}
        pagina = [
            m
            for m in (resp.get("messages") or [])
            if isinstance(m, dict) and _id(m.get("message_id"))
        ]
        for m in pagina:
            coletadas.setdefault(_id(m["message_id"]), m)
        if not pagina:
            break
        horarios = [
            q for m in pagina if (q := shopee_adaptador.para_datetime(m.get("created_timestamp")))
        ]
        if horarios and min(horarios) < e.desde:
            break
        proximo = _id((resp.get("page_result") or {}).get("next_offset"))
        if not proximo or proximo == "0" or proximo == offset:
            break
        offset = proximo
    return list(coletadas.values())


async def _conversa_shopee(
    e: _Etapa, canal: AtendimentoCanal | None, conv: dict, shop_id: int
) -> None:
    externo_id = _id(conv.get("conversation_id"))
    if not externo_id:
        return
    e.contar("conversas")
    existente = await _conversa(e.session, e.integration.id, CANAL_CHAT, externo_id)
    ultima = _id(conv.get("latest_message_id"))
    if (
        existente is not None
        and ultima
        and await _mensagem_gravada(e.session, existente.id, ultima)
    ):
        e.contar("ja_em_dia")  # a leitura periódica já tem até a última mensagem
        return
    if e.seco:
        e.contar("a_importar")
        return

    to_id = conv.get("to_id")
    conversa, criada = await gravar.upsert_conversa(
        e.session,
        canal=canal,
        integration=e.integration,
        plataforma=shopee_adaptador.PLATAFORMA,
        canal_nome=CANAL_CHAT,
        externo_id=externo_id,
        comprador_id=str(to_id) if to_id not in (None, "", 0) else None,
        comprador_nome=conv.get("to_name") if isinstance(conv.get("to_name"), str) else None,
        comprador_avatar=conv.get("to_avatar") if isinstance(conv.get("to_avatar"), str) else None,
        nao_lidas=_int(conv.get("unread_count")),
    )
    brutas = await _mensagens_shopee(e, externo_id)
    brutas.sort(
        key=lambda m: (
            shopee_adaptador.para_datetime(m.get("created_timestamp"))
            or datetime.min.replace(tzinfo=UTC),
            _id(m.get("message_id")),
        )
    )
    novas = 0
    pedido: str | None = None
    anuncio: str | None = None
    afiliado = False
    barrou = False
    for bruta in brutas:
        lida = shopee_adaptador.interpretar(bruta, shop_id)
        if not lida.externo_id:
            continue
        # Sem `origem`: o `gravar` decide — resposta da loja que não é nossa
        # é `externo` (Duoke, Seller Center); a do comprador, `cliente`.
        mensagem, criada_msg = await gravar.gravar_mensagem(
            e.session,
            conversa,
            externo_id=lida.externo_id,
            autor=lida.autor,
            texto=lida.texto,
            enviada_em=lida.enviada_em,
            tipo=lida.tipo,
            anexos=lida.anexos,
            payload=bruta,
        )
        novas += int(criada_msg)
        pedido = lida.pedido or pedido
        anuncio = lida.anuncio or anuncio
        afiliado = afiliado or lida.afiliado
        if lida.barrada and mensagem.autor == AUTOR_LOJA and mensagem.status != MSG_FALHOU:
            # A Shopee barrou a resposta: o comprador não recebeu.
            mensagem.status = MSG_FALHOU
            mensagem.erro = f"shopee {lida.barrada}"
            barrou = True
    if pedido or anuncio or afiliado:
        await gravar.upsert_conversa(
            e.session,
            canal=canal,
            integration=e.integration,
            plataforma=shopee_adaptador.PLATAFORMA,
            canal_nome=CANAL_CHAT,
            externo_id=externo_id,
            pedido_marketplace=pedido,
            anuncio_id=anuncio,
            dados={"afiliado": True} if afiliado else None,
        )
    if barrou:
        await gravar.recalcular_conversa(e.session, conversa)
    if assentar(conversa, criada, e.agora):
        e.contar("fechadas")
    await e.session.commit()
    e.contar("conversas_novas" if criada else "conversas_atualizadas")
    e.contar("mensagens_novas", novas)


async def _conversa_shopee_de_novo(
    e: _Etapa, canal: AtendimentoCanal | None, conversation_id: str, shop_id: int
) -> None:
    """A conversa pendente, relida sozinha (`get_one_conversation`, não marca lida)."""
    conv = await e.cliente.chat_one_conversation(conversation_id)
    if not isinstance(conv, dict) or _id(conv.get("conversation_id")) != conversation_id:
        e.contar("sumiram")  # a Shopee não tem mais a conversa: nada a importar
        return
    await _conversa_shopee(e, canal, conv, shop_id)


async def _shopee_chat(e: _Etapa) -> None:
    canal = e.canais.get(CANAL_CHAT)
    if canal is None and not e.seco:
        e.contar("sem_canal")
        return
    shop_id = _int(getattr(e.cliente, "shop_id", 0)) or 0
    falhas = [0]
    pendentes = _pendentes(e)
    if not e.estado.dados.get("lista_ok"):
        pagina = e.estado.dados.get("retomar")
        pagina = (
            pagina if isinstance(pagina, dict) and pagina.get("next_message_time_nano") else None
        )
        for _ in range(MAX_PAGINAS_LISTA):
            resp = await e.cliente.chat_conversation_list(
                direction=DIRECAO_LISTA,
                tipo="all",
                page_size=PAGINA_CONVERSAS,
                next_timestamp_nano=(pagina or {}).get("next_message_time_nano"),
            )
            resp = resp if isinstance(resp, dict) else {}
            conversas = [c for c in (resp.get("conversations") or []) if isinstance(c, dict)]
            passou = False
            for conv in conversas:
                quando = shopee_adaptador.para_datetime(conv.get("last_message_timestamp"))
                if quando is not None and quando < e.desde:
                    # Fixada pode vir fora da ordem: não decide o fim.
                    passou = passou or not conv.get("pinned")
                    continue
                await _item(
                    e,
                    falhas,
                    "atendimento_importar_conversa_falhou",
                    _id(conv.get("conversation_id")),
                    _conversa_shopee(e, canal, conv, shop_id),
                )
            info = resp.get("page_result") or {}
            proximo = info.get("next_cursor")
            if (
                passou
                or not conversas
                or not info.get("more")
                or not isinstance(proximo, dict)
                or not proximo.get("next_message_time_nano")
            ):
                break
            seguinte = {
                "next_message_time_nano": str(proximo.get("next_message_time_nano")),
                "conversation_id": str(proximo.get("conversation_id") or ""),
            }
            if seguinte == pagina:
                break  # o mesmo cursor de volta: seguir seria girar em falso
            pagina = seguinte
            # A página inteira está gravada (commit por conversa): daqui se
            # retoma — com as que falharam nela entre os pendentes.
            pendentes = _com_os_falhados(e, pendentes)
            await e.estado.salvar(retomar=pagina, pendentes=pendentes)
        else:
            # O teto de páginas desta execução: a próxima continua do `retomar`.
            e.contar("teto")
            return
        pendentes = _com_os_falhados(e, pendentes)
        await e.estado.salvar(retomar=None, lista_ok=True, pendentes=pendentes)
    pendentes = await _retentar(
        e,
        falhas,
        "atendimento_importar_conversa_falhou",
        pendentes,
        lambda ref: _conversa_shopee_de_novo(e, canal, ref, shop_id),
    )
    await _fechar_etapa(e, pendentes)


# ── Shopee: índice de pedidos e avaliações ────────────────────────────────


async def _shopee_pedidos(e: _Etapa) -> None:
    inicio = _int(e.estado.dados.get("pedidos_de")) or int(e.desde.timestamp())
    fim = int(e.agora.timestamp())
    while inicio <= fim:
        fatia_fim = min(fim, inicio + FATIA_PEDIDOS_S - 1)
        pedidos: dict[str, None] = {}
        cursor = ""
        for _ in range(MAX_PAGINAS_POR_FATIA):
            resp = await e.cliente.get_order_list(
                time_from=inicio,
                time_to=fatia_fim,
                time_range_field="create_time",
                page_size=PAGINA_PEDIDOS_SHOPEE,
                cursor=cursor,
            )
            resp = resp if isinstance(resp, dict) else {}
            for o in resp.get("order_list") or []:
                if isinstance(o, dict) and _id(o.get("order_sn")):
                    pedidos[_id(o["order_sn"])] = None
            cursor = _id(resp.get("next_cursor"))
            if not resp.get("more") or not cursor:
                break
        e.contar("pedidos", len(pedidos))
        if not e.seco:
            lista = list(pedidos)
            for i in range(0, len(lista), LOTE_DETALHE):
                for o in await e.cliente.get_order_detail_indice(lista[i : i + LOTE_DETALHE]):
                    linha = indice.pedido_shopee(o)
                    if linha is not None:
                        await indice.registrar_pedido(
                            e.session, integration_id=e.integration.id, **linha
                        )
                        e.contar("pedidos_indexados")
            await e.session.commit()
            await e.estado.salvar(pedidos_de=fatia_fim + 1)
        inicio = fatia_fim + 1
    await e.estado.salvar(concluida=True)


async def _shopee_avaliacoes(e: _Etapa) -> None:
    if not e.seco:
        leitura = await indexar.indexar_avaliacoes(
            e.session,
            e.integration,
            e.cliente,
            max_paginas=MAX_PAGINAS_AVALIACOES,
            desde=e.desde,
            parar_nas_conhecidas=False,
            cursor=_id(e.estado.dados.get("cursor_avaliacoes")),
        )
        await e.session.commit()
        e.contar("paginas", leitura.paginas)
        e.contar("avaliacoes_novas", leitura.novas)
        if not leitura.acabou:
            # O teto desta execução, sem ter chegado ao começo da janela: a
            # próxima continua deste cursor (a loja com mais de 2.000
            # avaliações em 90 dias não perde as antigas — as ruins inclusive).
            await e.estado.salvar(cursor_avaliacoes=leitura.cursor)
            e.contar("teto")
            return
        await e.estado.salvar(cursor_avaliacoes=None, concluida=True)
        return
    cursor = ""
    for _ in range(MAX_PAGINAS_AVALIACOES):
        resp = await e.cliente.get_comments(cursor=cursor, page_size=indexar.PAGINA_AVALIACOES)
        resp = resp if isinstance(resp, dict) else {}
        linhas = [
            linha
            for c in (resp.get("item_comment_list") or [])
            if (linha := indice.avaliacao_shopee(c)) is not None
        ]
        na_janela = [x for x in linhas if x["criado_em"] is None or x["criado_em"] >= e.desde]
        e.contar("avaliacoes", len(na_janela))
        proximo = _id(resp.get("next_cursor"))
        if len(na_janela) < len(linhas) or not resp.get("more") or not proximo or proximo == cursor:
            break
        cursor = proximo
    else:
        e.contar("teto")  # a contagem parou no teto: há mais para trás


# ── Mercado Livre: perguntas respondidas ──────────────────────────────────


async def _titulos_ml(e: _Etapa, faltando: dict[str, list[AtendimentoConversa]]) -> None:
    """O título do anúncio das perguntas importadas (UMA ida ao /items por 20). Enfeite."""
    try:
        corpos = await e.cliente.itens(list(faltando))
    except Exception as exc:  # noqa: BLE001 — sem título, a lista mostra o id
        logger.info("atendimento_importar_titulos_falhou", erro=_erro(exc))
        return
    for corpo in corpos or []:
        if not isinstance(corpo, dict):
            continue
        titulo = corpo.get("title")
        if not isinstance(titulo, str) or not titulo.strip():
            continue
        for conversa in faltando.get(_id(corpo.get("id")), []):
            conversa.anuncio_titulo = titulo.strip()


async def _ml_perguntas(e: _Etapa) -> None:
    canal = e.canais.get(CANAL_PERGUNTA)
    if canal is None and not e.seco:
        e.contar("sem_canal")
        return
    offset = _int(e.estado.dados.get("offset")) or 0
    for _ in range(MAX_PAGINAS_PERGUNTAS):
        corpo = await ml_adaptador._ler(
            e.cliente.perguntas_recebidas(
                status=ml_adaptador.PERGUNTA_RESPONDIDA, offset=offset, limit=PAGINA_PERGUNTAS
            )
        )
        corpo = corpo if isinstance(corpo, dict) else {}
        perguntas = [q for q in (corpo.get("questions") or []) if isinstance(q, dict)]
        if not perguntas:
            break
        passou = False
        faltando: dict[str, list[AtendimentoConversa]] = {}
        for q in perguntas:
            quando = ml_adaptador._data(q.get("date_created"))
            if quando is not None and quando < e.desde:
                passou = True  # mais novas primeiro: daqui para trás é fora da janela
                continue
            dados = ml_adaptador._dados_pergunta(q)
            if not dados.get("question_id") or not dados.get("item_id") or not dados.get("from_id"):
                continue
            externo_id = f"{ml_adaptador.PREFIXO_PERGUNTA}{dados['question_id']}"
            e.contar("perguntas")
            if e.seco:
                existe = await _conversa(e.session, e.integration.id, CANAL_PERGUNTA, externo_id)
                e.contar("ja_existem" if existe is not None else "a_importar")
                continue
            conversa, criada = await gravar.upsert_conversa(
                e.session,
                canal=canal,
                integration=e.integration,
                plataforma=ml_adaptador.PLATAFORMA,
                canal_nome=CANAL_PERGUNTA,
                externo_id=externo_id,
                comprador_id=dados["from_id"],
                anuncio_id=dados["item_id"],
                nao_lidas=0,  # respondida: nada a ler
                dados=dados,
            )
            novas = await ml_adaptador._aplicar_pergunta(e.session, conversa, q)
            if assentar(conversa, criada, e.agora):
                e.contar("fechadas")
            if not conversa.anuncio_titulo:
                faltando.setdefault(dados["item_id"], []).append(conversa)
            e.contar("conversas_novas" if criada else "conversas_atualizadas")
            e.contar("mensagens_novas", novas)
        if faltando:
            await _titulos_ml(e, faltando)
        if not e.seco:
            await e.session.commit()
        offset += len(perguntas)
        if not e.seco:
            await e.estado.salvar(offset=offset)
        total = _int(corpo.get("total")) or 0
        if passou or offset >= total:
            break
    else:
        e.contar("teto")  # a próxima execução continua do `offset`
        return
    await e.estado.salvar(concluida=True)


# ── Mercado Livre: pós-venda ──────────────────────────────────────────────


async def _ml_pos_venda(e: _Etapa) -> None:
    canal = e.canais.get(CANAL_POS_VENDA)
    if canal is None and not e.seco:
        e.contar("sem_canal")
        return
    seller_id = ml_adaptador._seller_id(e.cliente)
    if not seller_id:
        e.contar("sem_user_id")
        return
    offset = _int(e.estado.dados.get("offset")) or 0
    vistos: set[str] = set()
    rodada = ml_adaptador._Rodada(ResultadoSync(status="ok"))
    # A cota ZERADA desliga o retrato do pedido: importar é ler conversa, não
    # gastar uma ida ao /orders por pack antigo.
    sem_retrato = enriquecer.Cota(restam=0)
    formato = ml_adaptador._FORMATO_DATA_PEDIDOS
    falhas = [0]

    async def ler(pack: str) -> None:
        antes = len(rodada.novas)
        mensagens_antes = rodada.resultado.mensagens_novas
        await ml_adaptador._ler_pack(
            e.session,
            canal,
            e.integration,
            e.cliente,
            pack,
            seller_id,
            0,
            e.agora,
            rodada,
            criar_vazia=False,
            cota=sem_retrato,
        )
        if len(rodada.novas) > antes:
            conversa = await _conversa(e.session, e.integration.id, CANAL_POS_VENDA, pack)
            if conversa is not None and assentar(conversa, True, e.agora):
                e.contar("fechadas")
            e.contar("conversas_novas")
            e.contar("mensagens_novas", rodada.resultado.mensagens_novas - mensagens_antes)
        await e.session.commit()

    async def de_novo(pack: str) -> None:
        if await _conversa(e.session, e.integration.id, CANAL_POS_VENDA, pack) is not None:
            return  # a leitura periódica (ou outra passada) já trouxe
        await ler(pack)

    pendentes = _pendentes(e)
    if not e.estado.dados.get("lista_ok"):
        for _ in range(MAX_PAGINAS_PEDIDOS_ML):
            corpo = await ml_adaptador._ler(
                e.cliente.search_orders(
                    seller_id=seller_id,
                    date_from=e.desde.strftime(formato),
                    date_to=(e.agora + timedelta(hours=1)).strftime(formato),
                    limit=PAGINA_PEDIDOS_ML,
                    offset=offset,
                )
            )
            corpo = corpo if isinstance(corpo, dict) else {}
            pedidos = [p for p in (corpo.get("results") or []) if isinstance(p, dict)]
            if not pedidos:
                break
            for pedido in pedidos:
                # Pedido sem carrinho usa o próprio id como pack (regra do ML).
                pack = _id(pedido.get("pack_id")) or _id(pedido.get("id"))
                if not pack or pack in vistos:
                    continue
                vistos.add(pack)
                e.contar("packs")
                if await _conversa(e.session, e.integration.id, CANAL_POS_VENDA, pack) is not None:
                    e.contar("ja_existem")  # a leitura periódica cuida dela
                    continue
                if e.seco:
                    e.contar("a_ler")
                    continue
                await _item(e, falhas, "atendimento_importar_pack_falhou", pack, ler(pack))
            offset += len(pedidos)
            if not e.seco:
                pendentes = _com_os_falhados(e, pendentes)
                await e.estado.salvar(offset=offset, pendentes=pendentes)
            total = _int((corpo.get("paging") or {}).get("total")) or 0
            if offset >= total:
                break
        else:
            e.contar("teto")  # a próxima execução continua do `offset`
            return
        pendentes = _com_os_falhados(e, pendentes)
        await e.estado.salvar(lista_ok=True, pendentes=pendentes)
    pendentes = await _retentar(e, falhas, "atendimento_importar_pack_falhou", pendentes, de_novo)
    await _fechar_etapa(e, pendentes)


# ── Lojas ─────────────────────────────────────────────────────────────────

Etapa = Callable[[_Etapa], Awaitable[None]]

ETAPAS: dict[str, tuple[tuple[str, Etapa], ...]] = {
    "shopee": (
        ("chat", _shopee_chat),
        ("pedidos", _shopee_pedidos),
        ("avaliacoes", _shopee_avaliacoes),
    ),
    "ml": (
        ("perguntas", _ml_perguntas),
        ("pos_venda", _ml_pos_venda),
    ),
}


async def _nome_da_loja(session: AsyncSession, integration: Integration) -> str:
    """O nome da LOJA ("Marquezini"), quando o serviço de lojas existe; senão o da integração."""
    try:
        from app.services.atendimento import lojas
    except ImportError:
        return integration.name
    try:
        return await lojas.nome_da_loja(session, integration)
    except Exception:  # noqa: BLE001 — o nome é rótulo do resumo
        return integration.name


async def _lojas(session: AsyncSession, filtro: str | None) -> list[tuple[UUID, str, str]]:
    """(id, plataforma, nome) das lojas a importar; `filtro` = id, ou pedaço do nome."""
    integracoes = (
        (
            await session.execute(
                select(Integration)
                .where(
                    Integration.archived_at.is_(None),
                    Integration.platform.in_(
                        [IntegrationPlatform(p) for p in PLATAFORMAS_IMPORTADAS]
                    ),
                )
                .order_by(Integration.name)
            )
        )
        .scalars()
        .all()
    )
    alvo = (filtro or "").strip().lower()
    saida = []
    for integ in integracoes:
        nome = await _nome_da_loja(session, integ)
        if alvo and alvo != str(integ.id) and alvo not in integ.name.lower():
            if alvo not in nome.lower():
                continue
        saida.append((integ.id, getattr(integ.platform, "value", integ.platform), nome))
    return saida


async def _importar_loja(
    integration_id: UUID,
    *,
    dias: int,
    seco: bool,
    agora: datetime,
    limitador: Limitador,
    fabrica: FabricaCliente,
) -> dict:
    resumo: dict[str, Any] = {"integration_id": str(integration_id), "etapas": {}}
    async with _db.SessionLocal() as session:
        integration = await session.get(Integration, integration_id)
        if integration is None:
            return resumo
        plataforma = getattr(integration.platform, "value", integration.platform)
        resumo["plataforma"] = plataforma
        resumo["loja"] = await _nome_da_loja(session, integration)
        canais = {
            c.canal: c
            for c in (
                await session.execute(
                    select(AtendimentoCanal).where(
                        AtendimentoCanal.integration_id == integration_id
                    )
                )
            ).scalars()
        }
        etapas: list[tuple[str, Etapa]] = []
        for nome, funcao in ETAPAS.get(plataforma, ()):
            canal = canais.get(_CANAL_DA_ETAPA.get(nome, ""))
            if canal is not None and canal.status == indexar.CANAL_DESLIGADO:
                # O interruptor da equipe (429 da plataforma): nenhuma chamada.
                resumo["etapas"][nome] = {"canal_desligado": 1}
                continue
            etapas.append((nome, funcao))
        if not etapas:
            logger.info("atendimento_importar_loja_desligada", integration_id=str(integration_id))
            return resumo
        try:
            cliente = ClienteLimitado(await fabrica(integration), limitador)
        except Exception as exc:  # noqa: BLE001 — loja sem credencial não para as outras
            resumo["erro"] = _erro(exc)
            logger.warning(
                "atendimento_importar_cliente_falhou",
                integration_id=str(integration_id),
                erro=resumo["erro"],
            )
            return resumo
        desde = agora - timedelta(days=dias)
        for nome, funcao in etapas:
            estado = await _Estado.abrir(integration_id, nome, dias, desde, guardar=not seco)
            if estado.concluida:
                resumo["etapas"][nome] = {"ja_concluida": 1}
                continue
            etapa = _Etapa(session, integration, cliente, canais, estado, agora, seco)
            saida: dict[str, Any]
            try:
                await funcao(etapa)
            except Exception as exc:  # noqa: BLE001 — a etapa para; o estado guarda onde
                await etapa.desfazer()
                saida = {**etapa.contagem, "erro": _erro(exc)}
                logger.warning(
                    "atendimento_importar_etapa_falhou",
                    integration_id=str(integration_id),
                    etapa=nome,
                    erro=saida["erro"],
                )
            else:
                saida = dict(etapa.contagem)
            resumo["etapas"][nome] = saida
            logger.info(
                "atendimento_importar_etapa",
                integration_id=str(integration_id),
                etapa=nome,
                seco=seco,
                **etapa.contagem,
            )
            if plataforma == "shopee" and nome == "pedidos" and not seco and estado.concluida:
                # O índice desta loja tem o histórico inteiro desde `desde`: o
                # cartão pode afirmar "primeira compra".
                await indice.marcar_cobertura(integration_id, estado.desde)
    resumo["chamadas"] = len(cliente.chamadas)
    return resumo


async def _pegar_trava() -> tuple[bool, str | None]:
    """SET NX da importação → (pegou, token). Redis fora do ar = roda sem trava.

    Sem Redis não há estado (roda do começo) nem trava: é o mesmo risco de
    antes, e a importação é chamada à mão por quem sabe o que está rodando.
    """
    token = uuid4().hex
    try:
        pegou = await redis.set(CHAVE_TRAVA, token, nx=True, ex=TRAVA_TTL_S)
    except Exception as exc:  # noqa: BLE001
        logger.warning("atendimento_importar_trava_indisponivel", erro=type(exc).__name__)
        return True, None
    return bool(pegou), token


async def _renovar_trava(token: str) -> None:
    """Estende a validade da trava enquanto ela for nossa (roda até ser cancelada)."""
    while True:
        await asyncio.sleep(TRAVA_RENOVAR_S)
        try:
            await redis.eval(
                "if redis.call('get', KEYS[1]) == ARGV[1] then "
                "return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end",
                1,
                CHAVE_TRAVA,
                token,
                TRAVA_TTL_S,
            )
        except Exception as exc:  # noqa: BLE001 — tenta de novo na próxima volta
            logger.warning("atendimento_importar_trava_renovar_falhou", erro=type(exc).__name__)


async def _soltar_trava(token: str | None) -> None:
    if token is None:
        return
    try:
        await redis.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end",
            1,
            CHAVE_TRAVA,
            token,
        )
    except Exception:  # noqa: BLE001 — o TTL solta sozinho
        logger.warning("atendimento_importar_trava_soltar_falhou")


async def importar_historico(
    *,
    dias: int = DIAS_PADRAO,
    loja: str | None = None,
    seco: bool = False,
    forcar: bool = False,
    por_minuto: int = CHAMADAS_POR_MINUTO,
    fabrica_cliente: FabricaCliente | None = None,
    limitador: Limitador | None = None,
) -> dict:
    """Importa (ou, com `seco`, só conta) o histórico dos últimos `dias` dias. Devolve o resumo.

    Levanta `ImportacaoRecusada` sem a leitura ligada (a não ser com
    `forcar`), com `dias` fora de 1..365, com `loja` que não bate com
    nenhuma loja da Shopee/ML, ou com outra importação rodando
    (`importacao_em_andamento` — o `seco` também: ele lê as mesmas listas na
    mesma cota). Erro de uma loja não para as outras.
    """
    if not get_settings().atendimento_leitura_ativa and not forcar:
        raise ImportacaoRecusada(
            "leitura_desligada",
            "ATENDIMENTO_LEITURA_ATIVA está desligado; ligue a leitura antes, ou use --forcar",
        )
    if not isinstance(dias, int) or not 1 <= dias <= DIAS_MAX:
        raise ImportacaoRecusada("dias_invalido", f"use de 1 a {DIAS_MAX} dias")
    pegou, token = await _pegar_trava()
    if not pegou:
        raise ImportacaoRecusada(
            "importacao_em_andamento",
            "outra importação do histórico está rodando (comando ou worker); espere ela acabar",
        )
    renovar = asyncio.ensure_future(_renovar_trava(token)) if token is not None else None
    try:
        return await _importar(
            dias=dias,
            loja=loja,
            seco=seco,
            limitador=limitador or Limitador(por_minuto),
            fabrica=fabrica_cliente or clientes.cliente_da_integracao,
        )
    finally:
        if renovar is not None:
            renovar.cancel()
            with suppress(asyncio.CancelledError):
                await renovar
        await _soltar_trava(token)


async def _importar(
    *,
    dias: int,
    loja: str | None,
    seco: bool,
    limitador: Limitador,
    fabrica: FabricaCliente,
) -> dict:
    agora = _agora()
    async with _db.SessionLocal() as session:
        if not seco:
            # Import tardio: `sync` puxa o envio; aqui só a criação dos canais.
            from app.services.atendimento.sync import garantir_canais

            await garantir_canais(session)
            await session.commit()
        lojas = await _lojas(session, loja)
    if loja and not lojas:
        raise ImportacaoRecusada("loja_nao_encontrada", "nenhuma loja da Shopee/ML com esse nome")
    logger.info("atendimento_importar_inicio", dias=dias, seco=seco, lojas=len(lojas))
    resumo: dict[str, Any] = {"dias": dias, "seco": seco, "lojas": []}
    for integration_id, _plataforma, _nome in lojas:
        resumo["lojas"].append(
            await _importar_loja(
                integration_id,
                dias=dias,
                seco=seco,
                agora=agora,
                limitador=limitador,
                fabrica=fabrica,
            )
        )
    resumo["chamadas"] = limitador.total
    logger.info("atendimento_importar_fim", dias=dias, seco=seco, chamadas=limitador.total)
    return resumo
