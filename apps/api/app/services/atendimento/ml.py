"""Adaptador do Mercado Livre: perguntas pré-venda e mensagens pós-venda.

O ML tem DUAS caixas por conta, e `canal.canal` diz qual este canal lê:

- `pergunta` (pré-venda, pública no anúncio). UMA CONVERSA POR PERGUNTA
  (`externo_id = q:<question_id>`; `dados` guarda `question_id`, `item_id`
  e `from_id`). A pergunta é a mensagem `q:<id>` do cliente e a resposta é
  `a:<id>` da loja, na mesma conversa. Juntar as perguntas do mesmo
  comprador no mesmo anúncio numa conversa só (como a tela do ML) fazia a
  resposta a uma delas tirar da fila a outra, que continuava sem resposta
  e pública no anúncio. Assim, cada pergunta tem a sua fila, o seu prazo
  de 1 h e o envio responde EXATAMENTE a pergunta da conversa. Status
  fechado no ML (apagada, banida, em revisão, resposta derrubada) → a
  conversa fecha com o status como motivo. Até 2.000 caracteres.
- `pos_venda` (por pack). Uma conversa por pack; mensagem com o id do ML.
  Até 350 caracteres, só ISO-8859-1. O ML bloqueia a conversa (prazo
  vencido, pedido cancelado, mediação) — aí ela vira `bloqueada` com o
  `substatus` como motivo, e volta a abrir quando o ML reativa.

Por que cada leitura é assim:

- `mark_as_read=false` SEMPRE, fixo no cliente (`mensagens_do_pack`):
  enquanto o Duoke estiver ligado, o "não lido" é da equipe, não nosso.
- Os NÃO LIDOS dizem o que mudou no pós-venda — mas SÓ enquanto ninguém
  leu. Resposta dada pelo Duoke não gera "não lido", e mensagem que a
  equipe abriu no Duoke (ou no app do ML) antes da rodada sai da lista.
  Por isso, além deles, toda rodada:
    • relê até 20 conversas esperando resposta há mais de 5 min (a resposta
      dada por fora chega e a conversa sai da fila);
    • relê até 10 conversas com movimento nos últimos 2 dias, de 15 em
      15 min (o comprador que volta a escrever numa conversa já respondida,
      e a moderação da NOSSA mensagem, que o ML decide depois do envio);
    • varre uma página dos pedidos dos últimos 10 dias, girando rodada a
      rodada, e lê o pack de cada um (o pack NOVO que alguém leu antes de a
      rodada passar — sem a varredura ele nunca entraria aqui).
  O jeito definitivo é o tópico `messages` das notificações do ML
  (webhook); a varredura é a rede de segurança da leitura por consulta.
- Nas perguntas, a busca por status só mostra quem ESTÁ naquele status. A
  pergunta que sai de UNANSWERED sem aparecer entre as respondidas
  recentes (apagada, fechada com o anúncio, banida, em revisão) só se
  descobre pelo id — sem essa reconsulta ela ficaria na fila para sempre,
  com alerta de prazo de 1 h.
- A resposta que NÓS mandamos volta pelo sync e é adotada pelo `gravar`
  (mesmo texto, ±15 min); o que não for nosso é `externo`.

A rodada commita a cada pack/pergunta (`gravar.fim_do_item`: a conversa não
fica travada enquanto a rodada fala com o ML); o cursor do canal só anda no
fim (o sync commita). Texto de comprador nunca vai para o log — só ids e
contagens.

Painel com a cara do Duoke (28/09/2026), por `enriquecer` (SÓ LEITURA, com
uma cota por rodada): a pergunta ganha o cartão do anúncio perguntado
(`dados["produto"]`: foto, título, preço, link) e o pós-venda, o retrato do
pedido (`dados["pedido_mkt"]`) pelo `order_id` do pack. O ML não entrega foto
do comprador: a tela mostra as iniciais.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AtendimentoCanal, AtendimentoConversa, AtendimentoMensagem, Integration
from app.services.atendimento import enriquecer, gravar, lojas
from app.services.atendimento.constantes import (
    AUTOR_CLIENTE,
    AUTOR_LOJA,
    CANAL_PERGUNTA,
    CANAL_POS_VENDA,
    CONVERSA_ABERTA,
    CONVERSA_BLOQUEADA,
    CONVERSA_FECHADA,
    MSG_ENVIADA,
    MSG_FALHOU,
    MSG_REVISAR,
    ORIGENS_DAVINCI,
    ResultadoEnvio,
    ResultadoSync,
)

logger = structlog.get_logger()

PLATAFORMA = "ml"

# Status do canal que este adaptador devolve (vocabulário de
# constantes.STATUS_CANAL): 403 é permissão — a conta Poofy responde
# `PA_UNAUTHORIZED_RESULT_FROM_POLICIES` — e não adianta insistir.
STATUS_OK = "ok"
STATUS_SEM_ESCOPO = "sem_escopo"
STATUS_ERRO = "erro"

# ── Perguntas (pré-venda) ─────────────────────────────────────────────────
PERGUNTA_SEM_RESPOSTA = "UNANSWERED"
PERGUNTA_RESPONDIDA = "ANSWERED"
# Saíram da fila sem resposta e o ML não deixa (mais) responder. A conversa
# fecha com o status como motivo; `UNDER_REVIEW` pode voltar a UNANSWERED,
# e aí ela reabre sozinha.
PERGUNTA_FECHADA = ("CLOSED_UNANSWERED", "UNDER_REVIEW", "BANNED", "DELETED", "DISABLED")
# Resposta que o ML DERRUBOU depois de publicada (a doc: "retornamos o texto
# vazio nas perguntas e respostas com o status BANNED"). A pergunta segue
# ANSWERED — o ML não aceita outra resposta —, mas o comprador não vê a
# nossa: a `a:<id>` vira `falhou` e a conversa fecha com `ANSWER_<status>`.
RESPOSTA_DERRUBADA = ("BANNED", "DISABLED")
PREFIXO_MOTIVO_RESPOSTA = "ANSWER_"
# Os motivos que ESTE adaptador põe ao fechar — só esses ele reabre sozinho;
# conversa que a PESSOA fechou continua fechada.
MOTIVOS_DO_ML = (*PERGUNTA_FECHADA, *(f"{PREFIXO_MOTIVO_RESPOSTA}{s}" for s in RESPOSTA_DERRUBADA))
PREFIXO_PERGUNTA = "q:"
PREFIXO_RESPOSTA = "a:"
PAGINA_PERGUNTAS = 50
# As respondidas mais recentes: é por elas que a resposta dada no Duoke ou
# no celular chega aqui e a conversa sai de "aguardando".
RESPONDIDAS_RECENTES = 50
# Título do anúncio é enfeite da lista: no máximo 10 idas ao /items por rodada.
MAX_TITULOS = 10
MAX_RECONSULTAS = 20

# ── Pós-venda ─────────────────────────────────────────────────────────────
TAG_POS_VENDA = "post_sale"
PAGINA_PACK = 20
# 100 mensagens por pack cobrem qualquer conversa real; o teto só existe
# para uma resposta estranha da API não prender a rodada.
MAX_PAGINAS_PACK = 5
MAX_RELEITURAS = 20
RELER_DEPOIS_DE = timedelta(minutes=5)
# Conversas com movimento recente, mesmo já respondidas: o comprador volta a
# escrever e alguém lê no Duoke antes da rodada (a mensagem nunca aparece
# nos não lidos), ou o ML modera a NOSSA mensagem depois de aceitar o envio.
MAX_RELEITURAS_ATIVAS = 10
RELER_ATIVAS_DEPOIS_DE = timedelta(minutes=15)
JANELA_ATIVAS = timedelta(days=2)
# Varredura dos pedidos recentes: acha o pack NOVO que já saiu dos não lidos.
# Uma página por rodada, girando pela janela; pack relido há pouco é pulado.
VARREDURA_DIAS = 10
VARREDURA_PAGINA = 10
VARRER_DE_NOVO_DEPOIS_DE = timedelta(minutes=30)
_FORMATO_DATA_PEDIDOS = "%Y-%m-%dT%H:%M:%S.000-00:00"  # o da doc do ML (ver vigia_importacao)
ML_BLOQUEADA = "blocked"
ML_ATIVA = "active"
MODERACAO_LIMPA = "clean"

# Agente de Mensageria do ML no Brasil. Pela doc de pós-venda (migração
# progressiva desde 02/02/2026, começando pelo Full), na conversa que já passou
# para a arquitetura nova o GET traz o AGENTE em `from` (no lugar do comprador)
# e o POST tem de ir com `to.user_id` = agente. Medido em 25/09/2026 (16 packs
# de 5 contas, só leitura): nenhum ainda passava pelo agente — por isso o
# destino é decidido por conversa, olhando o que o próprio pack mostra, e não
# fixo para todas.
AGENTE_ML_BR = "3037675074"
# Moderação que barrou a mensagem (o comprador não a recebe). A doc lista os
# valores em minúscula (clean, rejected, pending, non_moderated), mas um
# exemplo antigo vem em maiúscula — compara-se sempre em minúscula. O
# `status` da própria mensagem também diz: available | moderated | rejected.
MODERACAO_BARRADA = frozenset({"rejected"})
STATUS_MENSAGEM_BARRADA = frozenset({"rejected", "moderated"})
ERRO_MODERADA = "moderada_ml"

_RE_RECURSO = re.compile(r"/packs/(?P<pack>\d+)/sellers/(?P<seller>\d+)")
_RE_BLOQUEIO = re.compile(r"blocked_by_[a-z_]+")
_RE_FRACAO = re.compile(r"(\.\d{6})\d+")


def _agora() -> datetime:
    """Relógio do adaptador (os testes trocam)."""
    return datetime.now(UTC)


# ── Erros de leitura ──────────────────────────────────────────────────────


class ErroML(Exception):  # noqa: N818 — nome do domínio, como EnvioRecusado
    """Falha de LEITURA na API do ML, já em texto de operação (sem dado de comprador)."""

    def __init__(self, http: int | None, codigo: str) -> None:
        self.http = http
        self.codigo = codigo
        super().__init__(self.texto)

    @property
    def texto(self) -> str:
        partes = (f"HTTP {self.http}" if self.http else "", self.codigo)
        return " ".join(p for p in partes if p) or "erro"

    @property
    def status_canal(self) -> str:
        return STATUS_SEM_ESCOPO if self.http == 403 else STATUS_ERRO


def _json(r: httpx.Response) -> Any:
    try:
        return r.json()
    except ValueError:
        return None


def _codigo_erro(r: httpx.Response) -> str:
    """O código de erro do ML (`code`/`error`), curto. Nunca o corpo inteiro."""
    corpo = _json(r)
    if isinstance(corpo, dict):
        for chave in ("code", "error"):
            valor = corpo.get(chave)
            if isinstance(valor, str) and valor.strip():
                return valor.strip()[:80]
    return ""


async def _ler[T](chamada: Awaitable[T]) -> T:
    """Faz uma LEITURA e traduz qualquer falha da plataforma em `ErroML`."""
    try:
        return await chamada
    except httpx.HTTPStatusError as e:
        raise ErroML(e.response.status_code, _codigo_erro(e.response)) from e
    except httpx.HTTPError as e:
        raise ErroML(None, type(e).__name__) from e
    except RuntimeError as e:
        # O `refresh` do cliente levanta RuntimeError quando o token não renova.
        raise ErroML(None, "token_nao_renovou") from e


# ── Conversão ─────────────────────────────────────────────────────────────


def _data(bruto: Any) -> datetime | None:
    """Data do ML → UTC. As perguntas vêm com até 9 casas de fração e fuso -04:00."""
    if not isinstance(bruto, str) or not bruto.strip():
        return None
    texto = _RE_FRACAO.sub(r"\1", bruto.strip().replace("Z", "+00:00"))
    try:
        quando = datetime.fromisoformat(texto)
    except ValueError:
        return None
    if quando.tzinfo is None:
        return quando.replace(tzinfo=UTC)
    return quando.astimezone(UTC)


def _texto(bruto: Any) -> str | None:
    """Texto da mensagem; versões antigas da API mandavam `{"plain": ...}`."""
    if isinstance(bruto, dict):
        bruto = bruto.get("plain")
    if isinstance(bruto, str) and bruto.strip():
        return bruto
    return None


def _lista(bruto: Any) -> list[dict]:
    return [x for x in bruto if isinstance(x, dict)] if isinstance(bruto, list) else []


def _inteiro(bruto: Any) -> int:
    try:
        return int(bruto or 0)
    except (TypeError, ValueError):
        return 0


def _id(bruto: Any) -> str:
    return "" if bruto is None else str(bruto).strip()


def _seller_id(cliente: Any) -> str:
    """O id do vendedor: `creds["user_id"]` (gravado no primeiro /users/me)."""
    return _id((getattr(cliente, "creds", None) or {}).get("user_id"))


@dataclass
class _Rodada:
    """Contagem de uma rodada — uma conversa conta uma vez, mesmo lida duas."""

    resultado: ResultadoSync
    novas: set[UUID] = field(default_factory=set)
    atualizadas: set[UUID] = field(default_factory=set)

    def contar(
        self, conversa: AtendimentoConversa, criada: bool, mensagens: int, mudou: bool
    ) -> None:
        self.resultado.mensagens_novas += mensagens
        if criada:
            self.novas.add(conversa.id)
        elif mensagens or mudou:
            self.atualizadas.add(conversa.id)

    def fechar(self) -> ResultadoSync:
        self.resultado.conversas_novas = len(self.novas)
        self.resultado.conversas_atualizadas = len(self.atualizadas - self.novas)
        return self.resultado


def _reabrir(conversa: AtendimentoConversa) -> None:
    """A plataforma voltou a deixar responder: sai de bloqueada/fechada e a fila se refaz."""
    conversa.situacao = CONVERSA_ABERTA
    conversa.bloqueio_motivo = None
    gravar.recalcular(conversa)


async def _zerar_nao_lidas(
    session: AsyncSession, canal: AtendimentoCanal, canal_nome: str, manter: set[str]
) -> None:
    """Quem não está mais na lista de não lidas do ML tem zero não lidas."""
    q = select(AtendimentoConversa).where(
        AtendimentoConversa.integration_id == canal.integration_id,
        AtendimentoConversa.canal == canal_nome,
        AtendimentoConversa.nao_lidas > 0,
    )
    if manter:
        q = q.where(AtendimentoConversa.externo_id.notin_(manter))
    for conversa in (await session.execute(q)).scalars():
        conversa.nao_lidas = 0


# ── Entrada ───────────────────────────────────────────────────────────────


async def sincronizar(
    session: AsyncSession,
    canal: AtendimentoCanal,
    integration: Integration,
    cliente: Any,
) -> ResultadoSync:
    """Uma rodada de leitura do canal (pergunta ou pós-venda). Commita por item.

    Falha na leitura PRINCIPAL (a busca de perguntas, a lista de não lidos)
    vira `ResultadoSync(status="erro"|"sem_escopo")` — nunca exceção; falha
    num pack isolado não derruba a rodada (vai no `erro`, com status ok).
    """
    agora = _agora()
    # Teto de idas ao ML para cartões e retratos de pedido nesta rodada.
    cota = enriquecer.Cota()
    try:
        if canal.canal == CANAL_PERGUNTA:
            resultado = await _sincronizar_perguntas(
                session, canal, integration, cliente, agora, cota
            )
        elif canal.canal == CANAL_POS_VENDA:
            resultado = await _sincronizar_pos_venda(
                session, canal, integration, cliente, agora, cota
            )
        else:
            return ResultadoSync(status=STATUS_ERRO, erro=f"canal desconhecido: {canal.canal}")
    except ErroML as e:
        logger.warning(
            "atendimento_ml_leitura_falhou",
            canal_id=str(canal.id),
            canal=canal.canal,
            http=e.http,
            codigo=e.codigo,
        )
        return ResultadoSync(status=e.status_canal, erro=e.texto)
    canal.cursor = {**(canal.cursor or {}), "ultima_rodada": agora.isoformat()}
    logger.info(
        "atendimento_ml_rodada",
        canal_id=str(canal.id),
        canal=canal.canal,
        conversas_novas=resultado.conversas_novas,
        conversas_atualizadas=resultado.conversas_atualizadas,
        mensagens_novas=resultado.mensagens_novas,
        nao_lidas=resultado.nao_lidas,
    )
    return resultado


# ── Perguntas ─────────────────────────────────────────────────────────────


class _Titulos:
    """Título do anúncio, com cache na rodada e no máximo `MAX_TITULOS` chamadas."""

    def __init__(self, cliente: Any) -> None:
        self._cliente = cliente
        self._restam = MAX_TITULOS
        self._cache: dict[str, str | None] = {}

    async def de(self, item_id: str) -> str | None:
        if item_id in self._cache:
            return self._cache[item_id]
        if self._restam <= 0:
            return None
        self._restam -= 1
        titulo: str | None = None
        try:
            item = await _ler(self._cliente.get_item(item_id))
        except ErroML as e:
            logger.info("atendimento_ml_titulo_falhou", item_id=item_id, http=e.http)
        else:
            bruto = item.get("title") if isinstance(item, dict) else None
            titulo = bruto if isinstance(bruto, str) and bruto.strip() else None
        self._cache[item_id] = titulo
        return titulo


def _payload_pergunta(q: dict) -> dict:
    """O que guardar da pergunta além do texto. `status` é o do ML, sempre atual."""
    resposta = q.get("answer") if isinstance(q.get("answer"), dict) else {}
    bruto = {
        "status": q.get("status"),
        "item_id": q.get("item_id"),
        "hold": q.get("hold"),
        "deleted_from_listing": q.get("deleted_from_listing"),
        "resposta_status": resposta.get("status"),
    }
    return {k: v for k, v in bruto.items() if v is not None}


def _estado_resposta(q: dict) -> str:
    """O `answer.status` do ML (ACTIVE, BANNED, DISABLED...), em maiúscula; "" sem resposta."""
    resposta = q.get("answer")
    return _id(resposta.get("status")).upper() if isinstance(resposta, dict) else ""


def _qid(conversa: AtendimentoConversa) -> str:
    """O id da pergunta desta conversa (uma conversa por pergunta)."""
    return _id((conversa.dados or {}).get("question_id")) or conversa.externo_id.removeprefix(
        PREFIXO_PERGUNTA
    )


def _momento():
    return func.coalesce(AtendimentoMensagem.enviada_em, AtendimentoMensagem.created_at)


async def _mensagem_externa(
    session: AsyncSession, conversa: AtendimentoConversa, externo_id: str
) -> AtendimentoMensagem | None:
    return (
        await session.execute(
            select(AtendimentoMensagem)
            .where(
                AtendimentoMensagem.conversa_id == conversa.id,
                AtendimentoMensagem.externo_id == externo_id,
            )
            .limit(1)
        )
    ).scalar_one_or_none()


async def _nossa_sem_id(
    session: AsyncSession, conversa: AtendimentoConversa
) -> AtendimentoMensagem | None:
    """A resposta que mandamos pelo DaVinci e que ainda não tem o id do ML.

    Com uma conversa por pergunta, a linha nossa sem id É a resposta desta
    pergunta (a ambígua que saiu, ou a enviada sem id). `enviando` fica de
    fora: quem decide essa é o envio, que está em andamento.
    """
    return (
        await session.execute(
            select(AtendimentoMensagem)
            .where(
                AtendimentoMensagem.conversa_id == conversa.id,
                AtendimentoMensagem.autor == AUTOR_LOJA,
                AtendimentoMensagem.origem.in_(ORIGENS_DAVINCI),
                AtendimentoMensagem.externo_id.is_(None),
                AtendimentoMensagem.status.in_((MSG_REVISAR, MSG_ENVIADA)),
            )
            .order_by(_momento().desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _resposta_derrubada(
    session: AsyncSession, conversa: AtendimentoConversa, qid: str, resposta: dict, estado: str
) -> int:
    """O ML derrubou a resposta publicada: a `a:<id>` vira `falhou`. Devolve se criou linha.

    Vale para a nossa (enviada pelo DaVinci: já tem o id `a:<id>`, ou é
    adotada agora) e para a dada por fora. O texto vem vazio — a linha
    existente guarda o que foi escrito; a nova fica sem texto.
    """
    externo = f"{PREFIXO_RESPOSTA}{qid}"
    erro = f"resposta_{estado.lower()}_ml"
    criada = False
    msg = await _mensagem_externa(session, conversa, externo)
    if msg is None:
        msg = await _nossa_sem_id(session, conversa)
        if msg is not None:
            msg.externo_id = externo
    if msg is None:
        msg, criada = await gravar.gravar_mensagem(
            session,
            conversa,
            externo_id=externo,
            autor=AUTOR_LOJA,
            texto=None,
            enviada_em=_data(resposta.get("date_created")),
            tipo="outro",
            payload={"question_id": qid, "status": estado},
        )
    if msg.status != MSG_FALHOU or msg.erro != erro:
        msg.status = MSG_FALHOU
        msg.erro = erro
        # Dicionário NOVO: mutar o JSONB no lugar não marca a coluna como suja.
        msg.payload = {**(msg.payload or {}), "status": estado}
        await gravar.recalcular_conversa(session, conversa)
    return int(criada)


async def _gravar_pergunta(
    session: AsyncSession, conversa: AtendimentoConversa, q: dict
) -> int:
    """Grava a pergunta (`q:<id>`) e a resposta, se houver (`a:<id>`). Devolve quantas novas.

    A pergunta que já existe só tem o `status` do ML (e o da resposta)
    atualizado no payload — é ele que diz se ela ainda está aberta (ver
    `_pergunta_pendente`). Resposta que o ML derrubou vira `falhou`, mesmo
    a que já estava gravada como enviada.
    """
    qid = _id(q.get("id"))
    novas = 0
    pergunta, criada = await gravar.gravar_mensagem(
        session,
        conversa,
        externo_id=f"{PREFIXO_PERGUNTA}{qid}",
        autor=AUTOR_CLIENTE,
        texto=_texto(q.get("text")),
        enviada_em=_data(q.get("date_created")),
        payload=_payload_pergunta(q),
    )
    if criada:
        novas += 1
    else:
        atual = {
            k: v for k, v in _payload_pergunta(q).items() if k in ("status", "resposta_status")
        }
        if any((pergunta.payload or {}).get(k) != v for k, v in atual.items()):
            # Dicionário NOVO: mutar o JSONB no lugar não marca a coluna como suja.
            pergunta.payload = {**(pergunta.payload or {}), **atual}

    resposta = q.get("answer")
    if not isinstance(resposta, dict):
        return novas
    estado = _estado_resposta(q)
    if estado in RESPOSTA_DERRUBADA:
        return novas + await _resposta_derrubada(session, conversa, qid, resposta, estado)
    if _texto(resposta.get("text")):
        # Sem `origem`: o `gravar` decide se é a NOSSA voltando (adota) ou
        # uma resposta dada fora do DaVinci (`externo`).
        _, criada_r = await gravar.gravar_mensagem(
            session,
            conversa,
            externo_id=f"{PREFIXO_RESPOSTA}{qid}",
            autor=AUTOR_LOJA,
            texto=_texto(resposta.get("text")),
            enviada_em=_data(resposta.get("date_created")),
            payload={"question_id": qid, "status": resposta.get("status")},
        )
        if criada_r:
            novas += 1
    return novas


def _motivo_de_fechar(q: dict) -> str | None:
    """Por que o ML não deixa (mais) responder esta pergunta — None se deixa ou já foi."""
    status = _id(q.get("status")).upper()
    if status in PERGUNTA_FECHADA:
        return status
    estado = _estado_resposta(q)
    if status == PERGUNTA_RESPONDIDA and estado in RESPOSTA_DERRUBADA:
        return f"{PREFIXO_MOTIVO_RESPOSTA}{estado}"
    return None


def _ajustar_situacao_pergunta(conversa: AtendimentoConversa, q: dict) -> None:
    """Fecha a conversa cuja pergunta o ML fechou; reabre quando ela volta.

    Só mexe no que ESTE adaptador fechou (motivo em `MOTIVOS_DO_ML`):
    conversa que a pessoa fechou na tela continua fechada.
    """
    motivo = _motivo_de_fechar(q)
    fechada_por_nos = (
        conversa.situacao == CONVERSA_FECHADA and conversa.bloqueio_motivo in MOTIVOS_DO_ML
    )
    if motivo is None:
        if fechada_por_nos:
            _reabrir(conversa)
        return
    if conversa.situacao == CONVERSA_FECHADA and not fechada_por_nos:
        return  # a pessoa deu por resolvida
    if conversa.situacao != CONVERSA_FECHADA or conversa.bloqueio_motivo != motivo:
        conversa.situacao = CONVERSA_FECHADA
        conversa.bloqueio_motivo = motivo
        gravar.recalcular(conversa)
        logger.info("atendimento_ml_pergunta_fechada", conversa_id=str(conversa.id), status=motivo)


async def _aplicar_pergunta(session: AsyncSession, conversa: AtendimentoConversa, q: dict) -> int:
    """A pergunta como o ML a devolveu → mensagens + situação. Devolve quantas mensagens novas."""
    novas = await _gravar_pergunta(session, conversa, q)
    _ajustar_situacao_pergunta(conversa, q)
    return novas


async def _pergunta_pendente(
    session: AsyncSession, conversa: AtendimentoConversa
) -> AtendimentoMensagem | None:
    """A pergunta desta conversa, se ela ainda espera resposta no ML; senão None.

    Pendente = `q:<id>` cujo último status visto no ML não é de respondida
    nem de fechada, e sem `a:<id>` válida (a nossa que falhou não conta).
    """
    qid = _qid(conversa)
    if not qid:
        return None
    pergunta = await _mensagem_externa(session, conversa, f"{PREFIXO_PERGUNTA}{qid}")
    if pergunta is None:
        return None
    status = _id((pergunta.payload or {}).get("status")).upper()
    if status == PERGUNTA_RESPONDIDA or status in PERGUNTA_FECHADA:
        return None
    resposta = await _mensagem_externa(session, conversa, f"{PREFIXO_RESPOSTA}{qid}")
    if resposta is not None and resposta.status != MSG_FALHOU:
        return None
    return pergunta


async def _respostas_conhecidas(
    session: AsyncSession, canal: AtendimentoCanal, externos: list[str]
) -> set[str]:
    """Quais `a:<id>` já estão gravadas numa pergunta que já vimos RESPONDIDA.

    Para não reprocessar as 50 de sempre. A nossa resposta recém-enviada
    (conversa ainda com `status_ml` UNANSWERED) passa mais uma vez — é aí
    que a conversa registra que o ML a deu por respondida.
    """
    if not externos:
        return set()
    linhas = await session.execute(
        select(AtendimentoMensagem.externo_id)
        .join(AtendimentoConversa, AtendimentoConversa.id == AtendimentoMensagem.conversa_id)
        .where(
            AtendimentoConversa.integration_id == canal.integration_id,
            AtendimentoConversa.canal == CANAL_PERGUNTA,
            AtendimentoConversa.dados["status_ml"].astext == PERGUNTA_RESPONDIDA,
            AtendimentoMensagem.externo_id.in_(externos),
        )
    )
    return {x for x in linhas.scalars() if x}


def _dados_pergunta(q: dict) -> dict:
    """O que a conversa guarda da pergunta: os ids do elo e o status do ML."""
    bruto = {
        "question_id": _id(q.get("id")),
        "item_id": _id(q.get("item_id")),
        "from_id": _id((q.get("from") or {}).get("id")),
        "status_ml": _id(q.get("status")).upper(),
    }
    return {k: v for k, v in bruto.items() if v}


async def _sincronizar_perguntas(
    session: AsyncSession,
    canal: AtendimentoCanal,
    integration: Integration,
    cliente: Any,
    agora: datetime,
    cota: enriquecer.Cota | None = None,
) -> ResultadoSync:
    teto = max(1, get_settings().atendimento_sync_max_conversas)

    # 1. As sem resposta — todas as páginas, até o teto de conversas por rodada.
    abertas: list[dict] = []
    total_abertas = 0
    offset = 0
    while True:
        corpo = await _ler(
            cliente.perguntas_recebidas(
                status=PERGUNTA_SEM_RESPOSTA, offset=offset, limit=PAGINA_PERGUNTAS
            )
        )
        lote = _lista(corpo.get("questions"))
        abertas.extend(lote)
        offset += len(lote)
        total_abertas = max(_inteiro(corpo.get("total")), offset)
        if not lote or offset >= total_abertas or len(abertas) >= teto:
            break
    completa = offset >= total_abertas

    # 2. As respondidas mais recentes (resposta dada fora do DaVinci).
    respondidas = _lista(
        (
            await _ler(
                cliente.perguntas_recebidas(
                    status=PERGUNTA_RESPONDIDA, offset=0, limit=RESPONDIDAS_RECENTES
                )
            )
        ).get("questions")
    )

    abertas_ids = {_id(q.get("id")) for q in abertas}
    conhecidas = await _respostas_conhecidas(
        session, canal, [f"{PREFIXO_RESPOSTA}{_id(q.get('id'))}" for q in respondidas]
    )
    perguntas: dict[str, dict] = {}
    for q in [*abertas, *respondidas]:
        qid = _id(q.get("id"))
        if not qid or not _id(q.get("item_id")) or not _id((q.get("from") or {}).get("id")):
            continue
        if (
            qid not in abertas_ids
            and f"{PREFIXO_RESPOSTA}{qid}" in conhecidas
            and _estado_resposta(q) not in RESPOSTA_DERRUBADA
        ):
            continue  # respondida que já está gravada: nada novo
        perguntas[qid] = q  # a respondida (lida depois) vale mais que a aberta

    rodada = _Rodada(ResultadoSync(status=STATUS_OK, nao_lidas=total_abertas))
    titulos = _Titulos(cliente)
    com_abertas: set[str] = set()
    for q in sorted(perguntas.values(), key=lambda q: _data(q.get("date_created")) or agora):
        dados = _dados_pergunta(q)
        externo = f"{PREFIXO_PERGUNTA}{dados['question_id']}"
        aberta = dados.get("status_ml") == PERGUNTA_SEM_RESPOSTA
        if aberta:
            com_abertas.add(externo)
        conversa, criada = await gravar.upsert_conversa(
            session,
            canal=canal,
            integration=integration,
            plataforma=PLATAFORMA,
            canal_nome=CANAL_PERGUNTA,
            externo_id=externo,
            # O nome da LOJA (cadastro), não o da integração.
            conta=await lojas.nome_da_loja(session, integration) or None,
            comprador_id=dados["from_id"],
            anuncio_id=dados["item_id"],
            nao_lidas=1 if aberta else 0,
            dados=dados,
        )
        antes = conversa.situacao
        # O cartão do anúncio perguntado (foto, preço) para o painel — e, de
        # carona, o título da lista, sem a ida ao /items/{id} do `_Titulos`.
        await enriquecer.enriquecer_conversa(session, conversa, integration, cliente, cota=cota)
        if not conversa.anuncio_titulo:
            titulo = _titulo_do_cartao(conversa) or await titulos.de(dados["item_id"])
            if titulo:
                conversa.anuncio_titulo = titulo
        novas = await _aplicar_pergunta(session, conversa, q)
        rodada.contar(conversa, criada, novas, antes != conversa.situacao)
        await gravar.fim_do_item(session)

    if completa:
        await _zerar_nao_lidas(session, canal, CANAL_PERGUNTA, com_abertas)
        await gravar.fim_do_item(session)

    # 3. Pendentes nossas que sumiram da busca: pergunta por pergunta, pelo id.
    #    Com a busca cortada no teto, só dá para afirmar "sumiu" do que é mais
    #    novo que a mais velha listada (a busca vem da mais nova para trás).
    datas = [d for d in (_data(q.get("date_created")) for q in abertas) if d is not None]
    desde = None if completa else (min(datas) if datas else agora)
    await _reconsultar_perguntas(session, canal, cliente, abertas_ids, desde, agora, rodada)
    if cota is not None:
        # O que sobrou da cota: o cartão do anúncio de quem ficou sem.
        await enriquecer.enriquecer_canal(session, canal, integration, cliente, cota)
    return rodada.fechar()


def _titulo_do_cartao(conversa: AtendimentoConversa) -> str | None:
    """O título do anúncio que o cartão da pergunta já trouxe (`dados["produto"]`)."""
    produto = (conversa.dados or {}).get(enriquecer.CHAVE_PRODUTO)
    titulo = produto.get("titulo") if isinstance(produto, dict) else None
    return titulo if isinstance(titulo, str) and titulo.strip() else None


async def _reconsultar_perguntas(
    session: AsyncSession,
    canal: AtendimentoCanal,
    cliente: Any,
    abertas_ids: set[str],
    desde: datetime | None,
    agora: datetime,
    rodada: _Rodada,
) -> None:
    """Pergunta que esperamos resposta mas saiu da busca de UNANSWERED: o que houve?

    Respondida (por fora e fora das 50 recentes) → grava a resposta.
    Fechada/apagada/banida/em revisão → conversa fechada com o motivo.
    404 = apagada. No máximo `MAX_RECONSULTAS` idas à API por rodada, a
    reconsultada há mais tempo primeiro (`dados.reconsultada_em`): uma
    pergunta que o ML insiste em dar como aberta não prende as outras.
    """
    reconsultada = AtendimentoConversa.dados["reconsultada_em"].astext
    conversas = (
        (
            await session.execute(
                select(AtendimentoConversa)
                .where(
                    AtendimentoConversa.integration_id == canal.integration_id,
                    AtendimentoConversa.canal == CANAL_PERGUNTA,
                    AtendimentoConversa.aguardando_resposta.is_(True),
                )
                .order_by(
                    reconsultada.asc().nulls_first(),
                    AtendimentoConversa.ultima_do_cliente_em.asc(),
                )
                .limit(MAX_RECONSULTAS * 5)
            )
        )
        .scalars()
        .all()
    )
    restam = MAX_RECONSULTAS
    for conversa in conversas:
        if restam <= 0:
            break
        qid = _qid(conversa)
        if not qid or qid in abertas_ids:
            continue
        quando = conversa.ultima_do_cliente_em
        if desde is not None and (quando is None or quando < desde):
            continue
        if await _pergunta_pendente(session, conversa) is None:
            continue
        restam -= 1
        conversa.dados = {
            **(conversa.dados or {}),
            "reconsultada_em": agora.isoformat(timespec="seconds"),
        }
        try:
            q = await _ler(cliente.detalhe_pergunta(qid))
        except ErroML as e:
            if e.http != 404:
                logger.info(
                    "atendimento_ml_reconsulta_falhou",
                    conversa_id=str(conversa.id),
                    question_id=qid,
                    http=e.http,
                    codigo=e.codigo,
                )
                continue
            q = {"id": qid, "status": "DELETED"}
        if not isinstance(q, dict):
            continue
        q = {**q, "id": qid}
        antes = conversa.situacao
        status = _id(q.get("status")).upper()
        if status:
            conversa.dados = {**(conversa.dados or {}), "status_ml": status}
        novas = await _aplicar_pergunta(session, conversa, q)
        rodada.contar(conversa, False, novas, antes != conversa.situacao)


# ── Pós-venda ─────────────────────────────────────────────────────────────


@dataclass
class _MensagemPack:
    externo_id: str
    autor: str
    texto: str | None
    enviada_em: datetime | None
    tipo: str
    anexos: list[dict]
    payload: dict
    barrada: bool


def _anexos(m: dict) -> list[dict]:
    """Nome e tipo do anexo, nunca o arquivo (URL de marketplace expira)."""
    saida = []
    for a in _lista(m.get("message_attachments")):
        item = {
            "arquivo": a.get("filename"),
            "nome": a.get("original_filename"),
            "tipo": a.get("type"),
            "tamanho": a.get("size"),
        }
        saida.append({k: v for k, v in item.items() if v is not None})
    return saida


def _moderacao(m: dict) -> dict:
    """O bloco de moderação da mensagem: `message_moderation` (formato atual) ou `moderation`."""
    for chave in ("message_moderation", "moderation"):
        valor = m.get(chave)
        if isinstance(valor, dict):
            return valor
    return {}


def _barrada(m: dict) -> bool:
    """A moderação do ML barrou esta mensagem (o destinatário não a recebe)?

    Duas pistas, sem depender da caixa: `message_moderation.status`
    (rejected) e o `status` da própria mensagem (rejected | moderated).
    """
    moderacao = _id(_moderacao(m).get("status")).lower()
    status = _id(m.get("status")).lower()
    return moderacao in MODERACAO_BARRADA or status in STATUS_MENSAGEM_BARRADA


def _mensagem_pack(m: dict, seller_id: str) -> _MensagemPack | None:
    mid = _id(m.get("id"))
    if not mid:
        return None
    de = _id((m.get("from") or {}).get("user_id"))
    texto = _texto(m.get("text"))
    anexos = _anexos(m)
    if texto:
        tipo = "texto"
    elif anexos:
        imagens = all(str(a.get("tipo") or "").startswith("image") for a in anexos)
        tipo = "imagem" if imagens else "arquivo"
    else:
        tipo = "outro"
    datas = m.get("message_date") or {}
    enviada_em = _data(datas.get("created")) or _data(datas.get("received"))
    payload: dict[str, Any] = {"status": m.get("status")}
    moderacao = _moderacao(m)
    status_moderacao = _id(moderacao.get("status"))
    if status_moderacao and status_moderacao.lower() != MODERACAO_LIMPA:
        payload["moderacao"] = {
            k: moderacao.get(k) for k in ("status", "reason", "source", "is_automatic")
        }
    return _MensagemPack(
        externo_id=mid,
        autor=AUTOR_LOJA if de and de == seller_id else AUTOR_CLIENTE,
        texto=texto,
        enviada_em=enviada_em,
        tipo=tipo,
        anexos=anexos,
        payload=payload,
        barrada=_barrada(m),
    )


def _comprador(mensagens: list[dict], seller_id: str) -> str | None:
    """O user_id de from/to que não é o vendedor nem o Agente do ML.

    O agente não é o comprador: gravá-lo em `comprador_id` apagaria o id real
    de quem comprou (e o upsert regrava a cada leitura).
    """
    for m in mensagens:
        for lado in ("from", "to"):
            uid = _id((m.get(lado) or {}).get("user_id"))
            if uid and uid not in (seller_id, AGENTE_ML_BR):
                return uid
    return None


def _via_agente(mensagens: list[dict]) -> bool:
    """O pack já passa pelo Agente de Mensageria do ML (from/to = agente)."""
    return any(
        _id((m.get(lado) or {}).get("user_id")) == AGENTE_ML_BR
        for m in mensagens
        for lado in ("from", "to")
    )


def _order_id(mensagens: list[dict]) -> str | None:
    """O pedido do pack: `message_resources` (name "orders") ou `data.order_id`."""
    for m in mensagens:
        for r in _lista(m.get("message_resources")):
            if r.get("name") == "orders" and _id(r.get("id")):
                return _id(r.get("id"))
    for m in mensagens:
        order_id = _id((m.get("data") or {}).get("order_id"))
        if order_id:
            return order_id
    return None


async def _ler_mensagens_do_pack(
    cliente: Any, pack_id: str, seller_id: str
) -> tuple[list[dict], dict]:
    """Todas as mensagens do pack (paginado, até o teto) + o corpo da 1ª página.

    Só HTTP: nenhuma escrita acontece antes de o pack inteiro ter sido lido,
    então uma falha no meio não deixa conversa pela metade.
    """
    mensagens: list[dict] = []
    primeira: dict | None = None
    offset = 0
    for _ in range(MAX_PAGINAS_PACK):
        corpo = await _ler(
            cliente.mensagens_do_pack(pack_id, seller_id, offset=offset, limit=PAGINA_PACK)
        )
        corpo = corpo if isinstance(corpo, dict) else {}
        if primeira is None:
            primeira = corpo
        lote = _lista(corpo.get("messages"))
        mensagens.extend(lote)
        offset += len(lote)
        total = _inteiro((corpo.get("paging") or {}).get("total"))
        if not lote or offset >= total:
            break
    return mensagens, primeira or {}


async def _adotar_nossa_barrada(
    session: AsyncSession, conversa: AtendimentoConversa, item: _MensagemPack
) -> None:
    """A nossa mensagem que o ML moderou na hora do envio voltando pelo sync.

    O envio já a deu por `falhou` (`moderada_ml`) — e linha que falhou o
    `gravar` não adota. Sem isto, a mesma mensagem viraria uma segunda linha
    `externo` (que ainda por cima cala a IA como "alguém respondeu por
    fora"). Casa pelo id que o POST devolveu; sem ele, pelo texto (±15 min).
    """
    if await _mensagem_externa(session, conversa, item.externo_id) is not None:
        return
    candidatas = (
        (
            await session.execute(
                select(AtendimentoMensagem).where(
                    AtendimentoMensagem.conversa_id == conversa.id,
                    AtendimentoMensagem.autor == AUTOR_LOJA,
                    AtendimentoMensagem.origem.in_(ORIGENS_DAVINCI),
                    AtendimentoMensagem.externo_id.is_(None),
                    AtendimentoMensagem.status == MSG_FALHOU,
                    AtendimentoMensagem.erro == ERRO_MODERADA,
                )
            )
        )
        .scalars()
        .all()
    )
    alvo = gravar.normalizar_para_comparar(item.texto)
    for m in candidatas:
        envio = (m.payload or {}).get("envio") or {}
        mesmo_id = _id(envio.get("id")) == item.externo_id
        perto = (
            item.enviada_em is None
            or m.enviada_em is None
            or abs(item.enviada_em - m.enviada_em) <= gravar.JANELA_ADOCAO
        )
        mesmo_texto = bool(alvo) and gravar.normalizar_para_comparar(m.texto) == alvo and perto
        if mesmo_id or mesmo_texto:
            m.externo_id = item.externo_id
            await session.flush()
            return


async def _ler_pack(
    session: AsyncSession,
    canal: AtendimentoCanal,
    integration: Integration,
    cliente: Any,
    pack_id: str,
    seller_id: str,
    nao_lidas: int,
    agora: datetime,
    rodada: _Rodada,
    *,
    criar_vazia: bool = True,
    cota: enriquecer.Cota | None = None,
) -> None:
    """Lê o pack inteiro e grava. `criar_vazia=False` (varredura de pedidos):
    pack sem mensagem nenhuma não vira conversa — a maioria dos pedidos não
    tem conversa, e a lista não pode encher de conversa vazia. A conversa
    ganha o retrato do pedido (no máximo a cada 30 min; nunca levanta)."""
    mensagens, corpo = await _ler_mensagens_do_pack(cliente, pack_id, seller_id)
    status_ml = corpo.get("conversation_status")
    status_ml = status_ml if isinstance(status_ml, dict) else {}
    estado = _id(status_ml.get("status"))
    substatus = _id(status_ml.get("substatus"))

    itens: dict[str, _MensagemPack] = {}
    for m in mensagens:
        item = _mensagem_pack(m, seller_id)
        if item is not None:
            itens[item.externo_id] = item  # páginas podem repetir se chegou mensagem no meio
    if not itens and not criar_vazia:
        existe = await session.scalar(
            select(func.count())
            .select_from(AtendimentoConversa)
            .where(
                AtendimentoConversa.integration_id == canal.integration_id,
                AtendimentoConversa.canal == CANAL_POS_VENDA,
                AtendimentoConversa.externo_id == pack_id,
            )
        )
        if not existe:
            return
    ordenados = sorted(itens.values(), key=lambda x: x.enviada_em or agora)

    order_id = _order_id(mensagens)
    dados: dict[str, Any] = {
        "pack_id": pack_id,
        "relida_em": agora.isoformat(timespec="seconds"),
        # Regravado a cada leitura: a conversa pode migrar para o agente.
        "via_agente": _via_agente(mensagens),
    }
    if order_id:
        dados["order_id"] = order_id
    limite = corpo.get("seller_max_message_length")
    if isinstance(limite, int) and limite > 0:
        dados["limite"] = limite
    if estado:
        dados["status_ml"] = estado
        dados["substatus_ml"] = substatus or None
    if status_ml:
        # SEMPRE regravado, mesmo vazio: reclamação encerrada tem de sumir
        # daqui — quem lê (a IA, a tela) não pode ver mediação que já acabou.
        claims = status_ml.get("claim_ids")
        dados["claim_ids"] = [str(c) for c in claims] if isinstance(claims, list) else []

    conversa, criada = await gravar.upsert_conversa(
        session,
        canal=canal,
        integration=integration,
        plataforma=PLATAFORMA,
        canal_nome=CANAL_POS_VENDA,
        externo_id=pack_id,
        conta=await lojas.nome_da_loja(session, integration) or None,
        comprador_id=_comprador(mensagens, seller_id),
        pedido_marketplace=order_id,
        nao_lidas=nao_lidas,
        dados=dados,
    )
    if conversa.pedido_marketplace is None:
        # Sem order_id à vista, o pack é o pedido (pedido sem carrinho usa o
        # próprio order_id como pack_id). Nunca por cima de um order_id já achado.
        conversa.pedido_marketplace = pack_id
    antes = conversa.situacao

    novas = 0
    refazer_fila = False
    for item in ordenados:
        if item.autor == AUTOR_LOJA and item.barrada:
            await _adotar_nossa_barrada(session, conversa, item)
        msg, criada_m = await gravar.gravar_mensagem(
            session,
            conversa,
            externo_id=item.externo_id,
            autor=item.autor,
            texto=item.texto,
            enviada_em=item.enviada_em,
            tipo=item.tipo,
            anexos=item.anexos,
            payload=item.payload,
        )
        if criada_m:
            novas += 1
        if item.autor == AUTOR_LOJA and item.barrada and msg.status != MSG_FALHOU:
            # A moderação do ML barrou a mensagem da loja: o comprador não a
            # recebeu. Contar como resposta tiraria da fila quem ainda espera.
            # Vale também para a linha que já existia (a nossa, enviada com
            # 2xx e moderada depois).
            msg.status = MSG_FALHOU
            msg.erro = ERRO_MODERADA
            refazer_fila = True
    if refazer_fila:
        await gravar.recalcular_conversa(session, conversa)

    # Situação DEPOIS das mensagens: pergunta nova reabre a conversa fechada
    # (gravar._reabrir_se_nova), e só então o bloqueio do ML é aplicado.
    if estado == ML_BLOQUEADA:
        motivo = substatus or estado
        # Fechada = a pessoa deu por resolvida: o bloqueio do ML não a devolve à fila.
        if conversa.situacao != CONVERSA_FECHADA and (
            conversa.situacao != CONVERSA_BLOQUEADA or conversa.bloqueio_motivo != motivo
        ):
            conversa.situacao = CONVERSA_BLOQUEADA
            conversa.bloqueio_motivo = motivo
            gravar.recalcular(conversa)
    elif estado == ML_ATIVA and conversa.situacao == CONVERSA_BLOQUEADA:
        _reabrir(conversa)
    await session.flush()
    await enriquecer.enriquecer_conversa(session, conversa, integration, cliente, cota=cota)
    rodada.contar(
        conversa,
        criada,
        novas,
        antes != conversa.situacao or refazer_fila,
    )


async def _priorizar(
    session: AsyncSession, canal: AtendimentoCanal, nao_lidos: dict[str, tuple[str, int]]
) -> list[str]:
    """Ordem de leitura dos não lidos: novos e com contagem mudada primeiro.

    Os não lidos continuam não lidos (nunca marcamos) até alguém ler no
    Duoke. Com mais packs que o teto, sem esta ordem os mesmos 40 seriam
    lidos toda rodada e o resto nunca; assim, o que mudou vai na frente e o
    resto roda pelo mais tempo sem leitura.
    """
    if not nao_lidos:
        return []
    existentes = {
        c.externo_id: c
        for c in (
            await session.execute(
                select(AtendimentoConversa).where(
                    AtendimentoConversa.integration_id == canal.integration_id,
                    AtendimentoConversa.canal == CANAL_POS_VENDA,
                    AtendimentoConversa.externo_id.in_(list(nao_lidos)),
                )
            )
        ).scalars()
    }

    def chave(pack_id: str) -> tuple[int, str]:
        conversa = existentes.get(pack_id)
        if conversa is None:
            return (0, "")
        mudou = conversa.nao_lidas != nao_lidos[pack_id][1]
        return (0 if mudou else 1, str((conversa.dados or {}).get("relida_em") or ""))

    return sorted(nao_lidos, key=chave)


async def _para_reler(
    session: AsyncSession, canal: AtendimentoCanal, excluir: set[str], agora: datetime
) -> list[AtendimentoConversa]:
    """As conversas a reler nesta rodada, fora dos não lidos.

    1. Esperando resposta há mais de 5 min (até 20): resposta dada no Duoke
       não gera "não lido" — sem reler, a conversa ficaria na fila (e no
       alerta) mesmo já respondida.
    2. Com movimento nos últimos 2 dias, relidas há mais de 15 min (até
       10), mesmo já respondidas: o comprador que volta a escrever e alguém
       lê no Duoke antes da rodada, e a moderação da NOSSA mensagem, que o
       ML decide depois de aceitar o envio.
    A mais tempo sem releitura primeiro (`dados.relida_em`), para todas rodarem.
    """
    relida = AtendimentoConversa.dados["relida_em"].astext
    base = select(AtendimentoConversa).where(
        AtendimentoConversa.integration_id == canal.integration_id,
        AtendimentoConversa.canal == CANAL_POS_VENDA,
    )
    if excluir:
        base = base.where(AtendimentoConversa.externo_id.notin_(excluir))

    corte = agora - RELER_DEPOIS_DE
    esperando = list(
        (
            await session.execute(
                base.where(
                    AtendimentoConversa.aguardando_resposta.is_(True),
                    AtendimentoConversa.ultima_do_cliente_em <= corte,
                    or_(relida.is_(None), relida <= corte.isoformat(timespec="seconds")),
                )
                .order_by(
                    relida.asc().nulls_first(), AtendimentoConversa.ultima_do_cliente_em.asc()
                )
                .limit(MAX_RELEITURAS)
            )
        )
        .scalars()
        .all()
    )

    corte_ativas = agora - RELER_ATIVAS_DEPOIS_DE
    ativas = list(
        (
            await session.execute(
                base.where(
                    AtendimentoConversa.aguardando_resposta.is_(False),
                    AtendimentoConversa.ultima_mensagem_em >= agora - JANELA_ATIVAS,
                    or_(relida.is_(None), relida <= corte_ativas.isoformat(timespec="seconds")),
                )
                .order_by(
                    relida.asc().nulls_first(), AtendimentoConversa.ultima_mensagem_em.desc()
                )
                .limit(MAX_RELEITURAS_ATIVAS)
            )
        )
        .scalars()
        .all()
    )
    return esperando + ativas


async def _varrer_pedidos(
    session: AsyncSession,
    canal: AtendimentoCanal,
    cliente: Any,
    seller_id: str,
    agora: datetime,
    ja_lidos: set[str],
) -> tuple[list[str], int]:
    """Os packs de UMA página dos pedidos dos últimos 10 dias, a (re)ler nesta rodada.

    Devolve (packs, offset da próxima página). Quem chama grava o offset no
    cursor DEPOIS de ler os packs: com o commit por pack, gravá-lo antes faria
    uma rodada que cai no meio pular o resto da página.

    O pack novo que a equipe leu no Duoke (ou no app) antes de a rodada
    passar nunca aparece nos não lidos: é por aqui que ele entra. A página
    gira rodada a rodada (`cursor.varredura_offset`) e volta ao começo no
    fim da janela; pack relido há menos de 30 min é pulado. Levanta ErroML.
    """
    cursor = dict(canal.cursor or {})
    offset = max(0, _inteiro(cursor.get("varredura_offset")))
    # Começo do dia: a janela (e o offset dentro dela) não anda a cada rodada.
    desde = (agora - timedelta(days=VARREDURA_DIAS)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    corpo = await _ler(
        cliente.search_orders(
            seller_id=seller_id,
            date_from=desde.strftime(_FORMATO_DATA_PEDIDOS),
            date_to=(agora + timedelta(hours=1)).strftime(_FORMATO_DATA_PEDIDOS),
            limit=VARREDURA_PAGINA,
            offset=offset,
        )
    )
    corpo = corpo if isinstance(corpo, dict) else {}
    pedidos = _lista(corpo.get("results"))
    total = _inteiro((corpo.get("paging") or {}).get("total"))
    proximo = offset + len(pedidos)
    novo_offset = 0 if not pedidos or proximo >= total else proximo

    packs: list[str] = []
    for pedido in pedidos:
        # Pedido sem carrinho usa o próprio id como pack (regra do ML).
        pack = _id(pedido.get("pack_id")) or _id(pedido.get("id"))
        if pack and pack not in ja_lidos and pack not in packs:
            packs.append(pack)
    if not packs:
        return [], novo_offset
    relida = AtendimentoConversa.dados["relida_em"].astext
    frescos = set(
        (
            await session.execute(
                select(AtendimentoConversa.externo_id).where(
                    AtendimentoConversa.integration_id == canal.integration_id,
                    AtendimentoConversa.canal == CANAL_POS_VENDA,
                    AtendimentoConversa.externo_id.in_(packs),
                    relida > (agora - VARRER_DE_NOVO_DEPOIS_DE).isoformat(timespec="seconds"),
                )
            )
        ).scalars()
    )
    return [p for p in packs if p not in frescos], novo_offset


async def _sincronizar_pos_venda(
    session: AsyncSession,
    canal: AtendimentoCanal,
    integration: Integration,
    cliente: Any,
    agora: datetime,
    cota: enriquecer.Cota | None = None,
) -> ResultadoSync:
    teto = max(1, get_settings().atendimento_sync_max_conversas)
    corpo = await _ler(cliente.mensagens_nao_lidas(TAG_POS_VENDA))
    corpo = corpo if isinstance(corpo, dict) else {}
    seller_id = _seller_id(cliente) or _id(corpo.get("user_id"))

    nao_lidos: dict[str, tuple[str, int]] = {}
    for r in _lista(corpo.get("results")):
        achou = _RE_RECURSO.search(str(r.get("resource") or ""))
        if achou is not None:
            nao_lidos[achou["pack"]] = (achou["seller"], _inteiro(r.get("count")))

    rodada = _Rodada(
        ResultadoSync(status=STATUS_OK, nao_lidas=sum(c for _, c in nao_lidos.values()))
    )
    falhas: list[ErroML] = []
    erro_varredura: ErroML | None = None
    tentativas = 0
    lidos: set[str] = set()

    async def _tentar(pack_id: str, seller: str, count: int, *, criar_vazia: bool = True) -> None:
        nonlocal tentativas
        tentativas += 1
        lidos.add(pack_id)
        try:
            await _ler_pack(
                session,
                canal,
                integration,
                cliente,
                pack_id,
                seller,
                count,
                agora,
                rodada,
                criar_vazia=criar_vazia,
                cota=cota,
            )
        except ErroML as e:
            falhas.append(e)
            logger.warning(
                "atendimento_ml_pack_falhou", pack_id=pack_id, http=e.http, codigo=e.codigo
            )
        # Commit por pack: a conversa gravada não fica travada enquanto a
        # rodada lê os outros packs (a tela esperaria por ela).
        await gravar.fim_do_item(session)

    fila = await _priorizar(session, canal, nao_lidos)
    for pack_id in fila[:teto]:
        seller_do_pack, count = nao_lidos[pack_id]
        await _tentar(pack_id, seller_do_pack, count)

    if seller_id:
        for conversa in await _para_reler(session, canal, set(nao_lidos), agora):
            await _tentar(conversa.externo_id, seller_id, 0)

        # Varredura dos pedidos recentes: o pack que saiu dos não lidos antes
        # de passarmos (lido no Duoke) só aparece aqui.
        try:
            varrer, novo_offset = await _varrer_pedidos(
                session, canal, cliente, seller_id, agora, lidos | set(nao_lidos)
            )
        except ErroML as e:
            erro_varredura = e
            logger.warning("atendimento_ml_varredura_falhou", http=e.http, codigo=e.codigo)
        else:
            for pack_id in varrer:
                await _tentar(pack_id, seller_id, 0, criar_vazia=False)
            # Só agora a página conta como lida (ver `_varrer_pedidos`).
            canal.cursor = {**(canal.cursor or {}), "varredura_offset": novo_offset}

    await _zerar_nao_lidas(session, canal, CANAL_POS_VENDA, set(nao_lidos))
    if cota is not None:
        # O que sobrou da cota: o retrato do pedido de quem ficou sem.
        await enriquecer.enriquecer_canal(session, canal, integration, cliente, cota)

    resultado = rodada.fechar()
    avisos: list[str] = []
    if falhas:
        codigos = sorted({f.texto for f in falhas})
        avisos.append(f"{len(falhas)} de {tentativas} conversas com erro: {', '.join(codigos)}")
        if len(falhas) == tentativas:
            # Nada leu além da lista de não lidos. Mesmo com 403, é `erro` e
            # não `sem_escopo`: a lista respondeu, então o canal TEM permissão
            # — um pack com 403 (pedido de outra conta, recurso restrito) não
            # pode tirar a conta inteira da leitura por uma hora.
            resultado.status = STATUS_ERRO
    if erro_varredura is not None:
        # A varredura é a rede de segurança: falhar nela não para a leitura,
        # mas tem de aparecer em "Lojas e modo".
        avisos.append(f"varredura de pedidos: {erro_varredura.texto}")
    if avisos:
        resultado.erro = "; ".join(avisos)
    return resultado


# ── Saída ─────────────────────────────────────────────────────────────────


def _motivo_bloqueio(corpo: Any) -> str | None:
    """Se a recusa do ML é "conversa bloqueada", o motivo (ex.: blocked_by_time)."""
    if not isinstance(corpo, dict):
        return None
    campos = {k: corpo.get(k) for k in ("code", "error", "message", "cause")}
    texto = json.dumps(campos, ensure_ascii=False, default=str).lower()
    especifico = _RE_BLOQUEIO.search(texto)
    if especifico:
        return especifico.group(0)
    if "block" in texto:
        for chave in ("code", "error"):
            valor = corpo.get(chave)
            if isinstance(valor, str) and valor.strip():
                return valor.strip()[:80]
        return ML_BLOQUEADA
    return None


def _mensagem_criada(corpo: Any) -> dict:
    """A mensagem (ou a pergunta) que o POST devolveu, onde quer que o ML a ponha."""
    if isinstance(corpo, list):
        corpo = corpo[0] if corpo else None
    if not isinstance(corpo, dict):
        return {}
    interno = corpo.get("message")
    if not isinstance(interno, dict):
        interno = next(iter(_lista(corpo.get("messages"))), None)
    return interno if isinstance(interno, dict) else corpo


def _id_enviado(corpo: Any) -> str | None:
    """O id da mensagem criada, onde quer que o ML o devolva."""
    if isinstance(corpo, list):
        corpo = corpo[0] if corpo else None
    if not isinstance(corpo, dict):
        return None
    for chave in ("id", "message_id"):
        if _id(corpo.get(chave)):
            return _id(corpo.get(chave))
    interno = corpo.get("message")
    if not isinstance(interno, dict):
        interno = next(iter(_lista(corpo.get("messages"))), None)
    if isinstance(interno, dict) and _id(interno.get("id")):
        return _id(interno.get("id"))
    return None


def _barrada_no_envio(corpo: Any) -> str | None:
    """O POST respondeu 2xx, mas o ML já barrou o que mandamos? → o código do erro.

    A moderação do pós-venda é "online" (decidida na criação, diz a doc):
    `message_moderation.status=rejected` / `status=rejected|moderated` na
    própria resposta = o comprador NÃO recebeu. Na pergunta, a resposta
    que volta já derrubada (`answer.status` BANNED) idem.
    """
    criada = _mensagem_criada(corpo)
    if not criada:
        return None
    if _barrada(criada):
        return ERRO_MODERADA
    resposta = criada.get("answer")
    if isinstance(resposta, dict):
        estado = _id(resposta.get("status")).upper()
        if estado in RESPOSTA_DERRUBADA:
            return f"resposta_{estado.lower()}_ml"
    return None


def _payload_envio(http: int, corpo: Any) -> dict:
    """O que guardar da resposta do ML: status e ids, nunca o texto de volta."""
    payload: dict[str, Any] = {"http": http}
    if isinstance(corpo, dict):
        for chave in ("id", "status"):
            if corpo.get(chave) is not None:
                payload[chave] = corpo.get(chave)
    criada = _mensagem_criada(corpo)
    if "id" not in payload and _id_enviado(corpo):
        payload["id"] = _id_enviado(corpo)
    moderacao = _moderacao(criada)
    if moderacao and _id(moderacao.get("status")).lower() != MODERACAO_LIMPA:
        payload["moderacao"] = {k: moderacao.get(k) for k in ("status", "reason")}
    return payload


async def _postar(
    chamada: Callable[[], Awaitable[httpx.Response]],
    *,
    conversa: AtendimentoConversa,
    externo_id: str | None = None,
) -> ResultadoEnvio:
    """Faz o POST e traduz para `ResultadoEnvio`. Nunca levanta.

    2xx = saiu (a não ser que a própria resposta diga que a moderação
    barrou: aí não chegou ao comprador → falha) · 4xx = o ML recusou (não
    saiu; bloqueio se for o caso) · 5xx / sem resposta = AMBÍGUO (pode ter
    saído — vira `revisar`, ninguém retenta). Falha de CONEXÃO é a exceção:
    o pedido nem chegou ao ML, então é recusa limpa e a pessoa pode tentar
    de novo.
    """
    try:
        r = await chamada()
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        return ResultadoEnvio(ok=False, erro=f"sem_conexao:{type(e).__name__}")
    except httpx.HTTPError as e:
        return ResultadoEnvio(ok=False, ambiguo=True, erro=f"sem_resposta:{type(e).__name__}")
    except RuntimeError:
        # O token não renovou ANTES do envio: nada saiu.
        return ResultadoEnvio(ok=False, erro="token_nao_renovou")
    except Exception as e:  # noqa: BLE001 — erro da plataforma nunca levanta daqui
        logger.exception("atendimento_ml_envio_inesperado", conversa_id=str(conversa.id))
        return ResultadoEnvio(ok=False, ambiguo=True, erro=f"inesperado:{type(e).__name__}")

    corpo = _json(r)
    if 200 <= r.status_code < 300:
        payload = _payload_envio(r.status_code, corpo)
        barrada = _barrada_no_envio(corpo)
        if barrada is not None:
            # Ninguém retenta em cima: o mesmo texto seria barrado de novo.
            # O id fica no payload — é por ele que a leitura do pack casa
            # esta linha com a mensagem moderada (`_adotar_nossa_barrada`).
            logger.info(
                "atendimento_ml_envio_moderado", conversa_id=str(conversa.id), erro=barrada
            )
            return ResultadoEnvio(ok=False, erro=barrada, payload=payload)
        return ResultadoEnvio(
            ok=True,
            externo_id=externo_id or _id_enviado(corpo),
            payload=payload,
        )
    codigo = _codigo_erro(r)
    erro = f"HTTP {r.status_code} {codigo}".strip()
    logger.info(
        "atendimento_ml_envio_recusado",
        conversa_id=str(conversa.id),
        http=r.status_code,
        codigo=codigo,
    )
    if r.status_code >= 500:
        return ResultadoEnvio(ok=False, ambiguo=True, erro=erro, payload={"http": r.status_code})
    return ResultadoEnvio(
        ok=False,
        erro=erro,
        bloqueio=_motivo_bloqueio(corpo),
        payload={"http": r.status_code, "codigo": codigo},
    )


async def enviar_texto(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    integration: Integration | None,
    cliente: Any,
    texto: str,
) -> ResultadoEnvio:
    """Envia `texto` na conversa. Timeout/5xx = ambiguo. Nunca levanta.

    Pergunta: responde EXATAMENTE a pergunta desta conversa (uma conversa
    por pergunta), se ela ainda espera resposta no ML (POST /answers). A
    mensagem ganha o id `a:<pergunta>` — o mesmo que o sync vai trazer,
    então ele reconhece a linha em vez de duplicar.
    Pós-venda: POST no pack, do vendedor para o comprador da conversa — ou
    para o Agente do ML, quando o pack já passa por ele (`dados.via_agente`).
    """
    if conversa.canal == CANAL_PERGUNTA:
        if await _pergunta_pendente(session, conversa) is None:
            return ResultadoEnvio(ok=False, erro="sem_pergunta_pendente")
        qid = _qid(conversa)
        return await _postar(
            lambda: cliente.responder_pergunta(qid, texto),
            conversa=conversa,
            externo_id=f"{PREFIXO_RESPOSTA}{qid}",
        )
    if conversa.canal == CANAL_POS_VENDA:
        seller_id = _seller_id(cliente)
        if not seller_id:
            return ResultadoEnvio(ok=False, erro="sem_seller_id")
        if (conversa.dados or {}).get("via_agente"):
            destinatario = AGENTE_ML_BR
        else:
            destinatario = conversa.comprador_id
        if not destinatario:
            return ResultadoEnvio(ok=False, erro="sem_comprador")
        pack_id = conversa.externo_id
        return await _postar(
            lambda: cliente.enviar_mensagem_pack(pack_id, seller_id, destinatario, texto),
            conversa=conversa,
        )
    return ResultadoEnvio(ok=False, erro=f"canal_desconhecido:{conversa.canal}")
