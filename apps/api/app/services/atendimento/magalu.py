"""Adaptador da Magalu: perguntas (pré-venda), chat com o cliente e SAC (30/09/2026).

Três caixas por loja, cada uma um canal (`canal.canal`), todas pela API do
vendedor com o `MagaluClient` da integração — o mesmo token, o mesmo proxy e a
mesma renovação (sob a trava da linha da integração) do estoque e do preço.
Pesquisa completa em `docs/atendimento-magalu.md`.

- `pergunta` (services.magalu.com, /v0/questions). UMA CONVERSA POR PERGUNTA
  (`q:<question_id>`), como no ML: a pergunta é a mensagem `q:<id>` do
  cliente e a resposta, `a:<id>:<external_id>` da loja. O `external_id` da
  resposta é o nosso `ref` quando ela sai daqui (vai no POST e volta na
  leitura); a dada no portal traz o dela. Uma resposta que a moderação
  RECUSOU (status RESPONSE_REJECTED) vira `falhou` e a pergunta volta para a
  fila — a Magalu deixa responder de novo, e a nova resposta é outra linha.
  Pergunta recusada ou apagada (REJECTED, DELETED, 404) fecha a conversa com
  o status como motivo; se voltar, reabre.
- `chat` (services.magalu.com, /v0/conversations). Uma conversa por conversa
  da Magalu. O comprador abre pelo produto e a conversa segue depois da
  compra; o vendedor só responde. A lista vem pela ÚLTIMA INTERAÇÃO
  (`cursor.desde`), e só se leem as mensagens da conversa que mexeu desde a
  última leitura (`dados.lida_ate`). Mensagem de integração (cartão de pedido,
  cupom) é `sistema`: não é fala de ninguém.
- `sac` (api.magalu.com, /seller/v0/tickets). Um protocolo = uma conversa.
  A fila é o STATUS do protocolo (`waiting_seller` = a vez é da loja), não
  quem falou por último: vai em `dados[CHAVE_VEZ_DA_LOJA]` e o `gravar`
  obedece — a mediação da Magalu (que escreve como `sistema`) cobrando a
  loja entra na fila, e o cliente falando com a Magalu não. A resposta da
  loja tira da fila por `CARENCIA_SAC` enquanto a Magalu não vira o status;
  depois, se o protocolo segue com a loja (a recusada pela moderação some da
  API), volta. O prazo é o `due_date` DO PROTOCOLO, lido junto com as
  mensagens (`dados[CHAVE_PRAZO_PLATAFORMA]`): vale mesmo vencido. A
  resposta vai para quem pediu por último (o comprador, ou a Magalu quando
  é ela cobrando). O SAC recusa `_offset` acima de 100: a fila lê 2 páginas
  de 100 (prazo mais curto primeiro), os recentes andam pela DATA, as
  mensagens vêm da mais nova. Protocolo encerrado fecha a conversa.

A MODERAÇÃO da Magalu decide depois do envio (202/201): a resposta sai daqui
como `enviada` com `payload.envio.moderacao = "em_moderacao"`; a leitura
seguinte grava o que a Magalu decidiu em `payload.moderacao`, e a recusada
vira `falhou` (`moderacao_magalu`) e deixa de contar como resposta — o
cliente volta para a fila. CPF, Pix, e-mail e telefone o validador já barra
antes de sair (`validador._DOCUMENTO_MAGALU` e as regras de contato).

Resposta dada NO PORTAL da Magalu chega pela leitura como `loja`/`externo`
(o `gravar` decide) e tira a conversa da fila. A nossa, quando volta, é
reconhecida pelo `ref` que mandamos (`external_id` na pergunta e no chat,
`code` no SAC) ou pelo id que o POST devolveu (chat); sem nenhum dos dois,
pelo texto.

Por que cada leitura é assim:

- NUNCA marca como lido: o `PATCH .../read_by` não existe aqui nem no
  cliente (o `MagaluClient._request` o recusa). O "não lido" é de quem lê
  pelo portal.
- Limite de ~200 leituras/min por vendedor: cada canal lê no máximo
  `MAX_LEITURAS_RODADA` por rodada (três canais × 60 a cada 2 min). O 429 é
  esperado no cliente (1 s, 2 s, 4 s); se insistir, a rodada PARA onde está,
  o que já foi gravado fica, e o canal só volta a ler depois de
  `ESPERA_429` (`cursor.espera_ate`).
- Sem permissão (401/403 que não é o HTML da Azion): o token não tem o
  escopo da caixa → `sem_escopo` com "reautorize a Magalu (Integrações ›
  Autorizar no Magalu)", sem gravar nada — igual ao TikTok. O sync só tenta
  de novo de hora em hora.
- Datas sem fuso (o SAC devolve assim) são lidas como UTC. Se forem de
  Brasília, o prazo fica 3 h ANTES do real — o lado seguro (o `due_date`
  vale mesmo vencido: não ganha SLA novo). A janela do cursor tem
  `MARGEM_CURSOR` de folga pelo mesmo motivo.
- A NOSSA resposta em moderação faz reler as mensagens da conversa mesmo sem
  interação nova (a recusa não muda a última interação), no máximo a cada
  `RELER_DEPOIS_DE`; fora da lista, a releitura pelo id escolhe no SQL quem
  espera resposta ou moderação.

A rodada commita a cada conversa (`gravar.fim_do_item`) e o cursor só anda
até a última conversa lida sem erro, na ordem da última interação. No SAC,
o protocolo que falha `MAX_FALHAS_SEGUIDAS` rodadas deixa o cursor passar e
é relido pelo id (`cursor.pulados`). Texto de comprador nunca vai para o log
— só ids e contagens.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import structlog
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AtendimentoCanal, AtendimentoConversa, AtendimentoMensagem, Integration
from app.services.atendimento import gravar, lojas
from app.services.atendimento.constantes import (
    AUTOR_CLIENTE,
    AUTOR_LOJA,
    AUTOR_SISTEMA,
    CANAL_CHAT,
    CANAL_PERGUNTA,
    CANAL_SAC,
    CHAVE_PRAZO_LIDO_EM,
    CHAVE_PRAZO_PLATAFORMA,
    CHAVE_VEZ_DA_LOJA,
    CONVERSA_ABERTA,
    CONVERSA_FECHADA,
    MSG_ENVIADA,
    MSG_FALHOU,
    MSG_REVISAR,
    ORIGENS_DAVINCI,
    ResultadoEnvio,
    ResultadoSync,
    limite_caracteres,
)

logger = structlog.get_logger()

# Um dos valores de `constantes.PLATAFORMAS`.
PLATAFORMA = "magalu"

# Valores de `constantes.STATUS_CANAL` que este adaptador devolve.
STATUS_OK = "ok"
STATUS_SEM_ESCOPO = "sem_escopo"
STATUS_ERRO = "erro"

# O que "Lojas e modo" mostra quando o token não tem o escopo da caixa. O
# consentimento do app já inclui as caixas (30/09); a loja precisa aceitar de
# novo para o token novo carregar os escopos.
MOTIVO_SEM_PERMISSAO = "sem permissão: reautorize a Magalu (Integrações › Autorizar no Magalu)"

# Quem assina a resposta na Magalu (`owner`): o nome da loja, pelo DaVinci.
AUTOR_EXTERNO_ID = "davinci"
# O nosso `ref` (external_id/code do envio): é por ele que a resposta que saiu
# daqui é reconhecida quando a leitura a traz de volta.
PREFIXO_REF = "davinci-"

# ── Leitura: limite da Magalu ─────────────────────────────────────────────
MAX_LEITURAS_RODADA = 60
ESPERA_429 = timedelta(seconds=60)
PAGINA = 100
MAX_PAGINAS = 5
MAX_PAGINAS_MENSAGENS = 5
# Conversas fora da lista da rodada que ainda esperam resposta (ou com a
# NOSSA resposta em moderação): relidas pelo id, a mais antiga primeiro. O
# mesmo freio de 15 min vale para reler as mensagens de uma conversa DA
# lista que tem a nossa em moderação (`dados.mensagens_lidas_em`).
MAX_RELEITURAS = 10
RELER_DEPOIS_DE = timedelta(minutes=15)
JANELA_ATIVAS = timedelta(days=2)
JANELA_PRIMEIRA_LEITURA = timedelta(days=7)
# Folga da janela do cursor (datas sem fuso, relógio da Magalu). O que já foi
# lido é pulado pelo `dados.lida_ate`, então a folga custa só a página da lista.
MARGEM_CURSOR = timedelta(hours=4)

# ── Perguntas ─────────────────────────────────────────────────────────────
PREFIXO_PERGUNTA = "q:"
PREFIXO_RESPOSTA = "a:"
PERGUNTA_AGUARDANDO = "WAITING_RESPONSE"
PERGUNTA_APROVADA = "APPROVED"
PERGUNTA_RESPOSTA_REJEITADA = "RESPONSE_REJECTED"
# Estas duas não vêm na lista (só no webhook e no GET por id): a pergunta que
# a moderação barrou ou que foi apagada não deixa responder.
PERGUNTA_FECHADA = ("REJECTED", "DELETED")
PERGUNTAS_QUE_ESPERAM = (PERGUNTA_AGUARDANDO, PERGUNTA_RESPOSTA_REJEITADA)

# ── Moderação (Q&A e chat em maiúscula, SAC em minúscula: compara-se minúscula)
MODERACAO_REJEITADA = frozenset({"rejected"})
# Ainda sem decisão (o SAC nasce `new`; perguntas e chat, `waiting_moderation`).
MODERACAO_PENDENTE = frozenset({"waiting_moderation", "new"})
ERRO_MODERACAO = "moderacao_magalu"
ENVIO_EM_MODERACAO = "em_moderacao"
# A moderação decide DEPOIS do envio, mas os relógios (o nosso e o da Magalu)
# não batem ao segundo.
FOLGA_RELOGIO = timedelta(minutes=1)

# ── Chat ──────────────────────────────────────────────────────────────────
CONVERSA_ABERTA_MAGALU = "OPENED"
_TIPO_VENDEDOR = "SELLER"
_TIPO_CLIENTE = "CUSTOMER"
# Marcadores (`tags`, {name, value}) que podem trazer o pedido ou o produto.
# A doc não lista os nomes — o que vier com estes nomes vira elo do painel.
_TAGS_PEDIDO = ("order_code", "codigo_pedido", "pedido", "order_number", "order", "order_id")
_TAGS_PRODUTO = ("sku", "product_sku", "offer_sku", "produto", "product", "offer_id", "product_id")
MAX_TAGS = 20

# ── SAC ───────────────────────────────────────────────────────────────────
TICKET_AGUARDA_LOJA = "waiting_seller"
TICKET_FECHADO = "closed"
# A OpenAPI do SAC (get_tickets e get_ticket_messages) limita `_offset` a 100:
# acima disso, 422. A fila lê as páginas 0 e 100 (os de prazo mais curto
# primeiro), os recentes andam pela DATA e as mensagens vêm da mais nova.
PAGINA_SAC = 100
OFFSETS_SAC = (0, 100)
# Protocolo que falha rodadas seguidas segura o cursor só até aqui; depois o
# cursor passa e ele é relido pelo id (`cursor.pulados`) por até 7 dias.
MAX_FALHAS_SEGUIDAS = 3
MAX_GUARDADOS = 50
# A resposta da loja tira o protocolo da fila mesmo com a Magalu ainda em
# waiting_seller (ela vira o status depois da moderação). Passada a folga com
# o protocolo ainda na vez da loja, ele volta: a resposta recusada pela
# moderação SOME da API (FAQ do SAC), e o prazo corre.
CARENCIA_SAC = timedelta(hours=4)
DESTINO_CLIENTE = "customer"
DESTINO_MAGALU = "channel"
_DESTINO_LOJA = "seller"
_REMETENTE_CLIENTE = "customer"
_REMETENTE_LOJA = "seller"
_REMETENTE_MAGALU = "channel"

# Motivo que ESTE adaptador põe ao fechar (só esses ele reabre sozinho —
# conversa que a pessoa fechou continua fechada).
PREFIXO_MOTIVO = "magalu_"

_RE_FRACAO = re.compile(r"(\.\d{6})\d+")
_RE_CODIGO = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
_EXTENSOES_IMAGEM = (".png", ".jpg", ".jpeg", ".jpe", ".gif", ".webp")


def _agora() -> datetime:
    """Relógio do adaptador (os testes trocam)."""
    return datetime.now(UTC)


# ── Utilitários ───────────────────────────────────────────────────────────


def _id(bruto: Any) -> str:
    return "" if bruto is None else str(bruto).strip()


def _texto(bruto: Any) -> str | None:
    return bruto if isinstance(bruto, str) and bruto.strip() else None


def _dict(bruto: Any) -> dict:
    return bruto if isinstance(bruto, dict) else {}


def _lista(bruto: Any) -> list[dict]:
    return [x for x in bruto if isinstance(x, dict)] if isinstance(bruto, list) else []


def _inteiro(bruto: Any) -> int:
    try:
        return int(bruto or 0)
    except (TypeError, ValueError):
        return 0


def _data(bruto: Any) -> datetime | None:
    """Data da Magalu → UTC. Sem fuso = UTC (ver o docstring do módulo)."""
    if isinstance(bruto, datetime):
        quando = bruto
    elif isinstance(bruto, str) and bruto.strip():
        texto = _RE_FRACAO.sub(r"\1", bruto.strip().replace("Z", "+00:00"))
        try:
            quando = datetime.fromisoformat(texto)
        except ValueError:
            return None
    else:
        return None
    return quando.replace(tzinfo=UTC) if quando.tzinfo is None else quando.astimezone(UTC)


def _iso(quando: datetime) -> str:
    """O formato que vai para a Magalu nos filtros de data (UTC, sem fração)."""
    return quando.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _carimbo(quando: datetime | None) -> str | None:
    """A data como a conversa guarda (`dados`), para comparar entre rodadas."""
    return quando.isoformat(timespec="seconds") if quando is not None else None


def _https(url: Any) -> str | None:
    """Só link https (vira botão/imagem na tela)."""
    texto = _texto(url)
    if texto is None:
        return None
    texto = texto.strip()
    return texto if texto.lower().startswith("https://") and len(texto) <= 2048 else None


def _nova_ref() -> str:
    return f"{PREFIXO_REF}{uuid4().hex[:24]}"


def _moderacao(bruto: Any) -> str:
    """O status da moderação, em minúscula ("" sem moderação)."""
    return _id(_dict(bruto).get("status")).lower()


def _json(r: httpx.Response) -> Any:
    try:
        return r.json()
    except ValueError:
        return None


def _eh_html(r: httpx.Response) -> bool:
    """403/5xx em HTML é a borda (Azion), não a API: não é falta de escopo."""
    tipo = (r.headers.get("content-type") or "").lower()
    if "html" in tipo:
        return True
    try:
        return r.text.lstrip()[:1] == "<"
    except Exception:  # noqa: BLE001
        return False


def _codigo_erro(r: httpx.Response) -> str:
    """O código de erro da Magalu (`slug`/`error`/`code`/`detail[0].type`), curto.

    Só formato de CÓDIGO (sem espaço): a descrição pode ecoar o texto enviado.
    """
    if _eh_html(r):
        return "html"
    corpo = _json(r)
    candidatos: list[Any] = []
    if isinstance(corpo, dict):
        candidatos += [corpo.get("slug"), corpo.get("error"), corpo.get("code")]
        for chave in ("detail", "details"):
            for d in _lista(corpo.get(chave)):
                candidatos += [d.get("slug"), d.get("type")]
    for c in candidatos:
        if isinstance(c, str) and _RE_CODIGO.match(c.strip()):
            return c.strip()
    return ""


# ── Erros de leitura ──────────────────────────────────────────────────────


class ErroMagalu(Exception):  # noqa: N818 — nome do domínio, como ErroML
    """Falha de LEITURA na API da Magalu, já em texto de operação (sem dado de comprador)."""

    def __init__(self, http: int | None, codigo: str, *, html: bool = False) -> None:
        self.http = http
        self.codigo = codigo
        self.html = html
        super().__init__(self.texto)

    @property
    def texto(self) -> str:
        partes = (f"HTTP {self.http}" if self.http else "", self.codigo)
        return " ".join(p for p in partes if p) or "erro"

    @property
    def sem_escopo(self) -> bool:
        """401 (mesmo depois do refresh) ou 403 da API: o token não tem o escopo."""
        return self.http in (401, 403) and not self.html

    @property
    def limite(self) -> bool:
        return self.http == 429


class _Parar(Exception):  # noqa: N818
    """A rodada para aqui (teto de leituras); o resto fica para a próxima."""


async def _ler[T](chamada: Awaitable[T]) -> T:
    """Faz uma LEITURA e traduz qualquer falha da plataforma em `ErroMagalu`."""
    try:
        return await chamada
    except httpx.HTTPStatusError as e:
        raise ErroMagalu(
            e.response.status_code, _codigo_erro(e.response), html=_eh_html(e.response)
        ) from e
    except httpx.HTTPError as e:
        raise ErroMagalu(None, type(e).__name__) from e
    except RuntimeError as e:
        # O `refresh` do cliente levanta RuntimeError quando o token não renova.
        raise ErroMagalu(None, "token_nao_renovou") from e
    except ValueError as e:
        # 200 com corpo que não é JSON.
        raise ErroMagalu(None, "resposta_invalida") from e


class _Leitor:
    """As leituras da rodada, com teto (`MAX_LEITURAS_RODADA`). Estourou → `_Parar`."""

    def __init__(self, maximo: int = MAX_LEITURAS_RODADA) -> None:
        self.restam = maximo
        self.feitas = 0

    async def __call__[T](self, fabrica: Callable[[], Awaitable[T]]) -> T:
        if self.restam <= 0:
            raise _Parar("teto de leituras da rodada")
        self.restam -= 1
        self.feitas += 1
        return await _ler(fabrica())


@dataclass
class _Rodada:
    """Contagem de uma rodada — uma conversa conta uma vez, mesmo lida duas."""

    resultado: ResultadoSync
    novas: set[UUID] = field(default_factory=set)
    atualizadas: set[UUID] = field(default_factory=set)
    lidas: int = 0
    falhas: list[ErroMagalu] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    def contar(
        self, conversa: AtendimentoConversa, criada: bool, mensagens: int, mudou: bool
    ) -> None:
        self.lidas += 1
        self.resultado.mensagens_novas += mensagens
        if criada:
            self.novas.add(conversa.id)
        elif mensagens or mudou:
            self.atualizadas.add(conversa.id)

    def fechar(self) -> ResultadoSync:
        self.resultado.conversas_novas = len(self.novas)
        self.resultado.conversas_atualizadas = len(self.atualizadas - self.novas)
        avisos = list(self.avisos)
        if self.falhas:
            codigos = sorted({f.texto for f in self.falhas})
            avisos.insert(
                0,
                f"{len(self.falhas)} de {len(self.falhas) + self.lidas} conversas com erro: "
                f"{', '.join(codigos)}",
            )
            if not self.lidas:
                # Nada leu além da lista. Sem escopo em TODAS (o SAC tem um
                # escopo para os protocolos e outro para as mensagens) é
                # permissão; senão é erro.
                if all(f.sem_escopo for f in self.falhas):
                    self.resultado.status = STATUS_SEM_ESCOPO
                    avisos[0] = f"{MOTIVO_SEM_PERMISSAO} ({', '.join(codigos)})"
                else:
                    self.resultado.status = STATUS_ERRO
        if avisos:
            self.resultado.erro = "; ".join(avisos)
        return self.resultado


def _reabrir(conversa: AtendimentoConversa) -> None:
    """A plataforma voltou a deixar responder: sai de fechada e a fila se refaz."""
    conversa.situacao = CONVERSA_ABERTA
    conversa.bloqueio_motivo = None
    gravar.recalcular(conversa)


def _fechada_por_nos(conversa: AtendimentoConversa) -> bool:
    return conversa.situacao == CONVERSA_FECHADA and (conversa.bloqueio_motivo or "").startswith(
        PREFIXO_MOTIVO
    )


def _ajustar_situacao(conversa: AtendimentoConversa, motivo: str | None) -> None:
    """Fecha a conversa que a Magalu fechou (`motivo`); reabre quando ela volta.

    Só mexe no que ESTE adaptador fechou (motivo com `PREFIXO_MOTIVO`):
    conversa que a pessoa fechou na tela continua fechada.
    """
    if motivo is None:
        if _fechada_por_nos(conversa):
            _reabrir(conversa)
        return
    motivo = f"{PREFIXO_MOTIVO}{motivo}"[:500]
    if conversa.situacao == CONVERSA_FECHADA and not _fechada_por_nos(conversa):
        return  # a pessoa deu por resolvida
    if conversa.situacao != CONVERSA_FECHADA or conversa.bloqueio_motivo != motivo:
        conversa.situacao = CONVERSA_FECHADA
        conversa.bloqueio_motivo = motivo
        gravar.recalcular(conversa)
        logger.info("atendimento_magalu_fechada", conversa_id=str(conversa.id), motivo=motivo)


async def _conversas_existentes(
    session: AsyncSession, canal: AtendimentoCanal, canal_nome: str, externos: list[str]
) -> dict[str, AtendimentoConversa]:
    if not externos:
        return {}
    linhas = await session.execute(
        select(AtendimentoConversa).where(
            AtendimentoConversa.integration_id == canal.integration_id,
            AtendimentoConversa.canal == canal_nome,
            AtendimentoConversa.externo_id.in_(externos),
        )
    )
    return {c.externo_id: c for c in linhas.scalars()}


def _sql_nossa_em_moderacao():
    """EXISTS: resposta NOSSA enviada e ainda sem decisão da moderação nesta conversa.

    O mesmo critério do `_tem_nossa_em_moderacao`, no SQL.
    """
    decidida = func.lower(AtendimentoMensagem.payload["moderacao"].astext)
    return (
        select(AtendimentoMensagem.id)
        .where(
            AtendimentoMensagem.conversa_id == AtendimentoConversa.id,
            AtendimentoMensagem.autor == AUTOR_LOJA,
            AtendimentoMensagem.origem.in_(ORIGENS_DAVINCI),
            AtendimentoMensagem.status == MSG_ENVIADA,
            AtendimentoMensagem.payload["envio"]["moderacao"].astext == ENVIO_EM_MODERACAO,
            or_(decidida.is_(None), decidida.in_(MODERACAO_PENDENTE)),
        )
        .exists()
    )


async def _para_reler(
    session: AsyncSession,
    canal: AtendimentoCanal,
    canal_nome: str,
    excluir: set[str],
    agora: datetime,
    *,
    chave_relida: str = "relida_em",
) -> list[AtendimentoConversa]:
    """As conversas (até 10) para uma ida pelo id, fora da lista da rodada.

    Esperando resposta (a resposta dada no portal chega; a conversa que a
    Magalu encerrou sai da fila) ou, com movimento nos últimos 2 dias, com a
    NOSSA resposta ainda sem decisão da moderação (decidida depois do envio).
    Relidas há mais de 15 min, a mais tempo sem releitura primeiro.

    O critério inteiro fica no SQL: filtrar DEPOIS do LIMIT deixava as
    descartadas (respondidas, que nunca ganham o carimbo da releitura)
    sempre na frente — com 30 delas, a que esperava nunca era relida.
    """
    relida = AtendimentoConversa.dados[chave_relida].astext
    corte = agora - RELER_DEPOIS_DE
    em_moderacao = [_sql_nossa_em_moderacao()]
    if canal_nome == CANAL_PERGUNTA:
        # A pergunta guarda a moderação na assinatura (o status é o fim dela).
        assinatura = AtendimentoConversa.dados["assinatura"].astext
        em_moderacao += [assinatura.like(f"%|{m}") for m in sorted(MODERACAO_PENDENTE)]
    q = (
        select(AtendimentoConversa)
        .where(
            AtendimentoConversa.integration_id == canal.integration_id,
            AtendimentoConversa.canal == canal_nome,
            AtendimentoConversa.situacao != CONVERSA_FECHADA,
            or_(
                AtendimentoConversa.aguardando_resposta.is_(True),
                and_(
                    AtendimentoConversa.ultima_mensagem_em >= agora - JANELA_ATIVAS,
                    or_(*em_moderacao),
                ),
            ),
            or_(relida.is_(None), relida <= corte.isoformat(timespec="seconds")),
        )
        .order_by(relida.asc().nulls_first(), AtendimentoConversa.ultima_mensagem_em.asc())
        .limit(MAX_RELEITURAS)
    )
    if excluir:
        q = q.where(AtendimentoConversa.externo_id.notin_(excluir))
    return list((await session.execute(q)).scalars().all())


def _mensagens_lidas_ha_tempo(conversa: AtendimentoConversa, agora: datetime) -> bool:
    """As mensagens desta conversa não são lidas há `RELER_DEPOIS_DE` (ou nunca)?"""
    lidas = _data(_dict(conversa.dados).get("mensagens_lidas_em"))
    return lidas is None or lidas <= agora - RELER_DEPOIS_DE


async def _forcar_por_moderacao(
    session: AsyncSession, existente: AtendimentoConversa | None, agora: datetime
) -> bool:
    """Reler as mensagens de uma conversa DA LISTA mesmo sem interação nova?

    Sim quando a NOSSA resposta ainda espera a moderação: a recusa não muda o
    `last_interaction_at`/`updated_at` (a última mensagem da conversa é a
    última APROVADA), e a conversa segue na lista por horas (a folga do
    cursor) sem que ninguém releia. No máximo a cada `RELER_DEPOIS_DE`.
    """
    if existente is None or not _mensagens_lidas_ha_tempo(existente, agora):
        return False
    return await _tem_nossa_em_moderacao(session, existente)


# ── Mensagens (chat e SAC) ────────────────────────────────────────────────


@dataclass
class _Mensagem:
    externo_id: str
    autor: str
    texto: str | None
    enviada_em: datetime | None
    tipo: str
    anexos: list[dict]
    payload: dict
    barrada: bool
    ref: str | None
    # Quem mandou (id, nome) — só para achar o comprador do protocolo do SAC.
    remetente_id: str | None = None
    remetente_nome: str | None = None


def _tipo(texto: str | None, anexos: list[dict]) -> str:
    if texto:
        return "texto"
    if anexos:
        imagens = all(str(a.get("tipo") or "").startswith("image") for a in anexos)
        return "imagem" if imagens else "arquivo"
    return "outro"


def _anexos_chat(bruto: Any) -> list[dict]:
    """Os anexos do chat são LINKS (podem ser assinados): guarda nome e tipo, nunca a URL."""
    saida = []
    for link in bruto if isinstance(bruto, list) else []:
        caminho = str(link or "").split("?", 1)[0].rstrip("/")
        nome = caminho.rsplit("/", 1)[-1][:200] if caminho else None
        if not nome:
            continue
        imagem = nome.lower().endswith(_EXTENSOES_IMAGEM)
        saida.append({"nome": nome, "tipo": "image" if imagem else "arquivo"})
    return saida


def _anexos_sac(bruto: Any) -> list[dict]:
    saida = []
    for a in _lista(bruto):
        item = {
            "arquivo": a.get("file_name"),
            "nome": a.get("provided_file_name"),
            "tipo": a.get("file_type"),
            "tamanho": a.get("file_size"),
        }
        saida.append({k: v for k, v in item.items() if v is not None})
    return saida


def _payload_moderacao(payload: dict, moderacao: str) -> dict:
    if moderacao:
        payload["moderacao"] = moderacao
    return payload


def _mensagem_chat(m: dict) -> _Mensagem | None:
    mid = _id(m.get("id"))
    if not mid:
        return None
    de = _dict(m.get("from_user"))
    tipo_de = _id(de.get("type")).upper()
    if m.get("integration") is True:
        autor = AUTOR_SISTEMA  # cartão de pedido, cupom: contexto, não fala
    elif tipo_de == _TIPO_VENDEDOR:
        autor = AUTOR_LOJA
    elif tipo_de == _TIPO_CLIENTE:
        autor = AUTOR_CLIENTE
    else:
        autor = AUTOR_SISTEMA
    texto = _texto(m.get("content"))
    anexos = _anexos_chat(m.get("attachments"))
    moderacao = _moderacao(m.get("moderation"))
    payload = _payload_moderacao({}, moderacao)
    if m.get("integration") is True:
        payload["integracao"] = True
    return _Mensagem(
        externo_id=mid,
        autor=autor,
        texto=texto,
        enviada_em=_data(m.get("when_at")),
        tipo=_tipo(texto, anexos),
        anexos=anexos,
        payload=payload,
        barrada=moderacao in MODERACAO_REJEITADA,
        ref=_texto(m.get("external_id")),
    )


def _mensagem_sac(m: dict) -> _Mensagem | None:
    mid = _id(m.get("id"))
    if not mid:
        return None
    remetente = _dict(m.get("sender"))
    tipo_de = _id(remetente.get("type")).lower()
    if tipo_de == _REMETENTE_CLIENTE:
        autor = AUTOR_CLIENTE
    elif tipo_de == _REMETENTE_LOJA:
        autor = AUTOR_LOJA
    else:
        # A Magalu (mediação) ou algo que não reconhecemos: não conta como
        # pergunta do cliente nem como resposta da loja.
        autor = AUTOR_SISTEMA
    texto = _texto(m.get("message"))
    anexos = _anexos_sac(m.get("attachments"))
    moderacao = _moderacao(m.get("moderation"))
    payload: dict[str, Any] = {"destino": _id(m.get("destination")).lower() or None}
    if tipo_de == _REMETENTE_MAGALU:
        payload["remetente"] = "magalu"
    return _Mensagem(
        externo_id=mid,
        autor=autor,
        texto=texto,
        enviada_em=_data(m.get("created_at")),
        tipo=_tipo(texto, anexos),
        anexos=anexos,
        payload=_payload_moderacao({k: v for k, v in payload.items() if v}, moderacao),
        barrada=moderacao in MODERACAO_REJEITADA,
        ref=_texto(m.get("code")),
        remetente_id=_id(remetente.get("id")) or None,
        remetente_nome=_texto(remetente.get("name")),
    )


async def _adotar_nossa(
    session: AsyncSession, conversa: AtendimentoConversa, item: _Mensagem
) -> None:
    """A resposta que saiu daqui voltando pela leitura, sem o id da Magalu na nossa linha.

    O SAC responde 202 sem id, e a moderação pode segurar a mensagem por mais
    que os 15 min da adoção do `gravar`: sem isto, a mesma resposta viraria
    uma segunda linha `externo` (que ainda cala a IA como "respondida por
    fora"). Casa pelo `ref` que mandamos; sem ele, pelo texto.
    """
    existente = await session.scalar(
        select(AtendimentoMensagem.id).where(
            AtendimentoMensagem.conversa_id == conversa.id,
            AtendimentoMensagem.externo_id == item.externo_id,
        )
    )
    if existente is not None:
        return
    estados = [
        AtendimentoMensagem.status.in_((MSG_ENVIADA, MSG_REVISAR)),
    ]
    if item.barrada:
        # A nossa que o próprio POST já deu por recusada (`falhou`): voltando
        # barrada, é ela — não uma segunda linha.
        estados.append(
            (AtendimentoMensagem.status == MSG_FALHOU)
            & (AtendimentoMensagem.erro == ERRO_MODERACAO)
        )
    candidatas = (
        (
            await session.execute(
                select(AtendimentoMensagem)
                .where(
                    AtendimentoMensagem.conversa_id == conversa.id,
                    AtendimentoMensagem.autor == AUTOR_LOJA,
                    AtendimentoMensagem.origem.in_(ORIGENS_DAVINCI),
                    AtendimentoMensagem.externo_id.is_(None),
                    or_(*estados),
                )
                .order_by(AtendimentoMensagem.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    alvo = gravar.normalizar_para_comparar(item.texto)
    escolhida = None
    if item.ref:
        escolhida = next(
            (m for m in candidatas if _dict(_dict(m.payload).get("envio")).get("ref") == item.ref),
            None,
        )
    if escolhida is None and alvo:
        escolhida = next(
            (m for m in candidatas if gravar.normalizar_para_comparar(m.texto) == alvo), None
        )
    if escolhida is None:
        return
    escolhida.externo_id = item.externo_id
    if escolhida.status != MSG_FALHOU:
        escolhida.status = MSG_ENVIADA
        escolhida.erro = None
    if item.enviada_em is not None:
        escolhida.enviada_em = item.enviada_em
    gravar.recalcular(conversa, [escolhida])
    await session.flush()
    logger.info(
        "atendimento_magalu_nossa_adotada",
        conversa_id=str(conversa.id),
        mensagem_id=str(escolhida.id),
    )


async def _gravar_mensagens(
    session: AsyncSession, conversa: AtendimentoConversa, itens: list[_Mensagem], agora: datetime
) -> tuple[int, bool]:
    """Grava as mensagens (idempotente) e aplica a moderação. → (novas, a fila mudou)."""
    novas = 0
    refazer = False
    for item in sorted(itens, key=lambda x: x.enviada_em or agora):
        if item.autor == AUTOR_LOJA:
            await _adotar_nossa(session, conversa, item)
        msg, criada = await gravar.gravar_mensagem(
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
        if criada:
            novas += 1
        refazer |= _aplicar_moderacao(msg, item.autor, item.payload.get("moderacao"), item.barrada)
    if refazer:
        await gravar.recalcular_conversa(session, conversa)
    return novas, refazer


def _aplicar_moderacao(msg: AtendimentoMensagem, autor: str, moderacao: Any, barrada: bool) -> bool:
    """O que a moderação decidiu vai para a linha. → a fila precisa ser refeita?

    Da loja e recusada: `falhou` (`moderacao_magalu`) — o comprador não a
    recebeu, e contar como resposta tiraria da fila quem ainda espera. Vale
    para a linha que já existia (a nossa, enviada com 2xx e moderada depois).
    """
    refazer = False
    if moderacao and _dict(msg.payload).get("moderacao") != moderacao:
        # Dicionário NOVO: mutar o JSONB no lugar não marca a coluna como suja.
        msg.payload = {**_dict(msg.payload), "moderacao": moderacao}
    if autor == AUTOR_LOJA and barrada and msg.status != MSG_FALHOU:
        msg.status = MSG_FALHOU
        msg.erro = ERRO_MODERACAO
        refazer = True
    return refazer


# ── Entrada ───────────────────────────────────────────────────────────────


async def sincronizar(
    session: AsyncSession,
    canal: AtendimentoCanal,
    integration: Integration,
    cliente: Any,
) -> ResultadoSync:
    """Uma rodada de leitura do canal (pergunta, chat ou SAC). Commita por conversa.

    Falha na leitura PRINCIPAL (a lista) vira `ResultadoSync(status="erro"|
    "sem_escopo")` — nunca exceção; falha numa conversa isolada não derruba
    a rodada (vai no `erro`, com status ok). 429 que insiste para a rodada e
    segura o canal por `ESPERA_429`.
    """
    agora = _agora()
    cursor = dict(canal.cursor or {})
    espera = _data(cursor.get("espera_ate"))
    if espera is not None and agora < espera:
        return ResultadoSync(
            status=STATUS_ERRO,
            erro=f"HTTP 429: limite de leituras da Magalu — nova leitura depois de {_iso(espera)}",
        )
    ler = _Leitor()
    rodada = _Rodada(ResultadoSync(status=STATUS_OK))
    try:
        if canal.canal == CANAL_PERGUNTA:
            await _sincronizar_perguntas(session, canal, integration, cliente, ler, agora, rodada)
        elif canal.canal == CANAL_CHAT:
            await _sincronizar_chat(session, canal, integration, cliente, ler, agora, rodada)
        elif canal.canal == CANAL_SAC:
            await _sincronizar_sac(session, canal, integration, cliente, ler, agora, rodada)
        else:
            return ResultadoSync(status=STATUS_ERRO, erro=f"canal desconhecido: {canal.canal}")
    except ErroMagalu as e:
        if e.limite:
            canal.cursor = {
                **(canal.cursor or {}),
                "espera_ate": (agora + ESPERA_429).isoformat(timespec="seconds"),
            }
        logger.warning(
            "atendimento_magalu_leitura_falhou",
            canal_id=str(canal.id),
            canal=canal.canal,
            http=e.http,
            codigo=e.codigo,
            conversas=rodada.lidas,
        )
        if rodada.lidas == 0 and not rodada.falhas:
            # Nada gravado: a lista principal não veio.
            if e.sem_escopo:
                return ResultadoSync(
                    status=STATUS_SEM_ESCOPO, erro=f"{MOTIVO_SEM_PERMISSAO} ({e.texto})"
                )
            if e.limite:
                return ResultadoSync(
                    status=STATUS_ERRO,
                    erro="HTTP 429: limite de leituras da Magalu — a leitura volta em 1 min",
                )
            return ResultadoSync(status=STATUS_ERRO, erro=e.texto)
        rodada.avisos.append(
            "limite de leituras da Magalu (HTTP 429): o resto fica para a próxima rodada"
            if e.limite
            else f"leitura interrompida: {e.texto}"
        )
    except _Parar as p:
        rodada.avisos.append(f"{p}: o resto fica para a próxima rodada")
    # A espera de um 429 antigo sai; a desta rodada (se houve) fica.
    atual = {
        k: v for k, v in (canal.cursor or {}).items() if k != "espera_ate" or not _passou(v, agora)
    }
    canal.cursor = {**atual, "ultima_rodada": agora.isoformat(timespec="seconds")}
    resultado = rodada.fechar()
    logger.info(
        "atendimento_magalu_rodada",
        canal_id=str(canal.id),
        canal=canal.canal,
        status=resultado.status,
        conversas_novas=resultado.conversas_novas,
        conversas_atualizadas=resultado.conversas_atualizadas,
        mensagens_novas=resultado.mensagens_novas,
        nao_lidas=resultado.nao_lidas,
        leituras=ler.feitas,
    )
    return resultado


def _passou(bruto: Any, agora: datetime) -> bool:
    """A espera do 429 guardada no cursor já passou (ou é ilegível)?"""
    quando = _data(bruto)
    return quando is None or quando <= agora


async def _nome_da_loja(session: AsyncSession, integration: Integration | None) -> str | None:
    return await lojas.nome_da_loja(session, integration) or None


# ── Perguntas ─────────────────────────────────────────────────────────────


def _qid(conversa: AtendimentoConversa) -> str:
    """O id da pergunta desta conversa (uma conversa por pergunta)."""
    return _id(_dict(conversa.dados).get("question_id")) or conversa.externo_id.removeprefix(
        PREFIXO_PERGUNTA
    )


def _externo_resposta(qid: str, resposta: dict) -> str:
    """`a:<pergunta>:<external_id>` — cada resposta é uma linha (a recusada e a nova)."""
    externo = _id(resposta.get("external_id"))
    return f"{PREFIXO_RESPOSTA}{qid}:{externo}" if externo else f"{PREFIXO_RESPOSTA}{qid}"


def _assinatura_pergunta(q: dict) -> str:
    """O que muda numa pergunta entre rodadas (status, resposta, moderação).

    Com a HORA da decisão da moderação: duas recusas seguidas (RESPONSE_REJECTED
    sem `answer`) só diferem nela. O status da moderação fica por último
    (o SQL do `_para_reler` lê o fim).
    """
    resposta = _dict(q.get("answer"))
    return "|".join(
        (
            _id(q.get("status")).upper(),
            _id(resposta.get("external_id")),
            _id(resposta.get("when_at")),
            _id(_dict(q.get("moderation")).get("when_at")),
            _moderacao(q.get("moderation")),
        )
    )


def _cartao_produto(item_id: str | None, titulo: str | None, imagem: Any, link: Any) -> dict | None:
    """O cartão do anúncio para o painel, no formato do `enriquecer` (sem ida à loja)."""
    if not (item_id or titulo):
        return None
    return {
        "tipo": "produto",
        "item_id": item_id or "",
        "titulo": titulo,
        "imagem": _https(imagem),
        "preco": None,
        "preco_original": None,
        "moeda": "BRL",
        "link": _https(link),
    }


def _dados_pergunta(q: dict) -> tuple[dict, dict]:
    """(dados da conversa, campos do upsert) de uma pergunta como a Magalu devolve."""
    qid = _id(q.get("id"))
    pergunta = _dict(q.get("question"))
    produto = _dict(pergunta.get("product"))
    assunto = _dict(q.get("subject"))
    extra = _dict(assunto.get("extra"))
    dono = _dict(pergunta.get("owner"))
    identificadores = _lista(q.get("identifiers"))
    sku = (
        _texto(produto.get("sku"))
        or next((v for i in identificadores if (v := _texto(i.get("value")))), None)
        or _texto(assunto.get("id"))
    )
    titulo = _texto(extra.get("name")) or _texto(produto.get("description"))
    dados: dict[str, Any] = {
        "question_id": qid,
        "status_magalu": _id(q.get("status")).upper() or None,
        "assinatura": _assinatura_pergunta(q),
    }
    cartao = _cartao_produto(sku, titulo, extra.get("url_img"), extra.get("url"))
    if cartao is not None:
        dados["produto"] = cartao
    campos = {
        "comprador_id": _id(dono.get("customer_id"))
        or _id(dono.get("external_id"))
        or _id(dono.get("ref_key"))
        or None,
        "comprador_nome": _texto(dono.get("name")),
        "anuncio_id": sku,
        "anuncio_titulo": titulo,
    }
    return dados, campos


def _resposta_rejeitada(q: dict) -> bool:
    status = _id(q.get("status")).upper()
    return (
        status == PERGUNTA_RESPOSTA_REJEITADA
        or _moderacao(q.get("moderation")) in MODERACAO_REJEITADA
    )


async def _adotar_resposta_nossa(
    session: AsyncSession, conversa: AtendimentoConversa, qid: str, externo: str, texto: str | None
) -> None:
    """A NOSSA resposta com outro `external_id` na leitura: a linha ganha o id da Magalu.

    Mandamos o `ref` em `external_id`; se a Magalu o trocar, a nossa linha
    (`a:<id>:<ref>`) e a da leitura não casariam, e a mesma resposta viraria
    uma segunda linha `externo`. Casa pelo texto, na mesma pergunta.
    """
    if await session.scalar(
        select(AtendimentoMensagem.id).where(
            AtendimentoMensagem.conversa_id == conversa.id,
            AtendimentoMensagem.externo_id == externo,
        )
    ):
        return
    alvo = gravar.normalizar_para_comparar(texto)
    if not alvo:
        return
    candidatas = (
        (
            await session.execute(
                select(AtendimentoMensagem)
                .where(
                    AtendimentoMensagem.conversa_id == conversa.id,
                    AtendimentoMensagem.autor == AUTOR_LOJA,
                    AtendimentoMensagem.origem.in_(ORIGENS_DAVINCI),
                    AtendimentoMensagem.status.in_((MSG_ENVIADA, MSG_REVISAR)),
                    or_(
                        AtendimentoMensagem.externo_id.is_(None),
                        AtendimentoMensagem.externo_id.like(f"{PREFIXO_RESPOSTA}{qid}:%"),
                    ),
                )
                .order_by(AtendimentoMensagem.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    for m in candidatas:
        if gravar.normalizar_para_comparar(m.texto) == alvo:
            m.externo_id = externo
            m.status = MSG_ENVIADA
            m.erro = None
            await session.flush()
            return


async def _aplicar_pergunta(
    session: AsyncSession,
    canal: AtendimentoCanal,
    integration: Integration,
    q: dict,
    rodada: _Rodada,
    *,
    existente: AtendimentoConversa | None = None,
) -> None:
    """A pergunta como a Magalu a devolveu → conversa, mensagens e situação."""
    qid = _id(q.get("id"))
    if not qid:
        return
    status = _id(q.get("status")).upper()
    dados, campos = _dados_pergunta(q)
    pergunta = _dict(q.get("question"))
    resposta = _dict(q.get("answer"))
    tem_resposta = bool(_texto(resposta.get("message")))
    rejeitada = tem_resposta and _resposta_rejeitada(q)
    pendente = status in PERGUNTAS_QUE_ESPERAM and (not tem_resposta or rejeitada)
    mudou = existente is None or _dict(existente.dados).get("assinatura") != dados["assinatura"]
    if not mudou and not await _tem_nossa_em_moderacao(session, existente):
        # Nada mudou desde a última rodada — e nada NOSSO espera a moderação
        # (a recusa dela pode chegar com a mesma cara da anterior).
        return
    conversa, criada = await gravar.upsert_conversa(
        session,
        canal=canal,
        integration=integration,
        plataforma=PLATAFORMA,
        canal_nome=CANAL_PERGUNTA,
        externo_id=f"{PREFIXO_PERGUNTA}{qid}",
        conta=await _nome_da_loja(session, integration),
        nao_lidas=1 if pendente else 0,
        dados=dados,
        **campos,
    )
    antes = conversa.situacao
    novas = 0
    msg_q, criada_q = await gravar.gravar_mensagem(
        session,
        conversa,
        externo_id=f"{PREFIXO_PERGUNTA}{qid}",
        autor=AUTOR_CLIENTE,
        texto=_texto(pergunta.get("message")),
        enviada_em=_data(pergunta.get("when_at")),
        payload={"status": status or None, "sku": campos["anuncio_id"]},
    )
    novas += int(criada_q)
    if not criada_q and _dict(msg_q.payload).get("status") != (status or None):
        msg_q.payload = {**_dict(msg_q.payload), "status": status or None}

    refazer = False
    if status == PERGUNTA_RESPOSTA_REJEITADA and not tem_resposta:
        # A Magalu recusou e não mostra mais a resposta: a última da loja
        # (a nossa ou a do portal) ANTERIOR à decisão não chegou ao
        # comprador. A que saiu depois dela é outra, ainda na moderação.
        recusada_em = _data(_dict(q.get("moderation")).get("when_at"))
        if mudou or recusada_em is not None:
            refazer = await _recusar_ultima_resposta(session, conversa, qid, ate=recusada_em)
    if tem_resposta:
        externo = _externo_resposta(qid, resposta)
        await _adotar_resposta_nossa(session, conversa, qid, externo, resposta.get("message"))
        moderacao = (
            PERGUNTA_RESPOSTA_REJEITADA.lower() if rejeitada else _moderacao(q.get("moderation"))
        )
        # Sem `origem`: o `gravar` decide se é a NOSSA voltando (adota) ou a
        # dada no portal da Magalu (`externo`).
        msg_a, criada_a = await gravar.gravar_mensagem(
            session,
            conversa,
            externo_id=externo,
            autor=AUTOR_LOJA,
            texto=_texto(resposta.get("message")),
            enviada_em=_data(resposta.get("when_at")),
            payload=_payload_moderacao({"question_id": qid}, moderacao),
        )
        novas += int(criada_a)
        refazer = _aplicar_moderacao(msg_a, AUTOR_LOJA, moderacao, rejeitada) or refazer
    if refazer:
        await gravar.recalcular_conversa(session, conversa)
    _ajustar_situacao(conversa, status if status in PERGUNTA_FECHADA else None)
    rodada.contar(conversa, criada, novas, antes != conversa.situacao or refazer)


async def _listar_perguntas(
    ler: _Leitor, cliente: Any, status: str, teto: int, max_paginas: int
) -> tuple[list[dict], int, bool]:
    """(perguntas, total que a Magalu diz ter, leu todas?) de um status."""
    perguntas: list[dict] = []
    total = 0
    offset = 0
    completa = False
    for _ in range(max_paginas):
        corpo = await ler(
            lambda offset=offset: cliente.perguntas(status=status, offset=offset, limit=PAGINA)
        )
        lote = _lista(_dict(corpo).get("results"))
        perguntas.extend(lote)
        offset += len(lote)
        total_magalu = _inteiro(_dict(_dict(corpo.get("meta")).get("page")).get("total"))
        total = max(total_magalu, offset)
        if len(lote) < PAGINA or (total_magalu and offset >= total_magalu):
            completa = True
            break
        if len(perguntas) >= teto:
            break
    return perguntas, total, completa


async def _sincronizar_perguntas(
    session: AsyncSession,
    canal: AtendimentoCanal,
    integration: Integration,
    cliente: Any,
    ler: _Leitor,
    agora: datetime,
    rodada: _Rodada,
) -> None:
    # Uma página cheia no mínimo: a lista não tem ordem documentada, e o
    # "sumiu da lista" (respondida no portal, apagada) só vale com a lista inteira.
    teto = max(PAGINA, int(get_settings().atendimento_sync_max_conversas or 1))
    # 1. A fila (sem resposta) e as respostas que a moderação recusou.
    abertas, total_abertas, completa = await _listar_perguntas(
        ler, cliente, PERGUNTA_AGUARDANDO, teto, MAX_PAGINAS
    )
    rejeitadas, total_rejeitadas, _ = await _listar_perguntas(
        ler, cliente, PERGUNTA_RESPOSTA_REJEITADA, PAGINA, 1
    )
    rodada.resultado.nao_lidas = total_abertas + total_rejeitadas

    perguntas: dict[str, dict] = {}
    for q in [*abertas, *rejeitadas]:
        qid = _id(q.get("id"))
        if qid:
            perguntas[qid] = q
    existentes = await _conversas_existentes(
        session, canal, CANAL_PERGUNTA, [f"{PREFIXO_PERGUNTA}{qid}" for qid in perguntas]
    )
    for qid, q in sorted(
        perguntas.items(),
        key=lambda par: _data(_dict(par[1].get("question")).get("when_at")) or agora,
    ):
        await _aplicar_pergunta(
            session,
            canal,
            integration,
            q,
            rodada,
            existente=existentes.get(f"{PREFIXO_PERGUNTA}{qid}"),
        )
        await gravar.fim_do_item(session)

    # 2. Quem esperamos resposta e saiu da lista: respondida no portal,
    #    recusada, apagada. Pelo id. Com a lista cortada no teto, "saiu" não
    #    prova nada — fica para a rodada em que ela vier inteira.
    if completa:
        await _reconsultar_perguntas(
            session, canal, integration, cliente, ler, set(perguntas), agora, rodada
        )


async def _reconsultar_perguntas(
    session: AsyncSession,
    canal: AtendimentoCanal,
    integration: Integration,
    cliente: Any,
    ler: _Leitor,
    listadas: set[str],
    agora: datetime,
    rodada: _Rodada,
) -> None:
    candidatas = await _para_reler(
        session,
        canal,
        CANAL_PERGUNTA,
        {f"{PREFIXO_PERGUNTA}{q}" for q in listadas},
        agora,
        chave_relida="reconsultada_em",
    )
    for conversa in candidatas:
        qid = _qid(conversa)
        if not qid:
            continue
        conversa.dados = {
            **_dict(conversa.dados),
            "reconsultada_em": agora.isoformat(timespec="seconds"),
        }
        try:
            q = await ler(lambda qid=qid: cliente.pergunta(qid))
        except ErroMagalu as e:
            if e.limite:
                raise
            if e.http != 404:
                rodada.falhas.append(e)
                logger.info(
                    "atendimento_magalu_reconsulta_falhou",
                    conversa_id=str(conversa.id),
                    http=e.http,
                    codigo=e.codigo,
                )
                await gravar.fim_do_item(session)
                continue
            q = {"id": qid, "status": "DELETED"}
        q = {**_dict(q), "id": qid}
        if not _dict(q).get("question"):
            # 404 (ou corpo vazio): só a situação muda — a pergunta já está gravada.
            _ajustar_situacao(conversa, _id(q.get("status")).upper() or "DELETED")
            conversa.dados = {**_dict(conversa.dados), "status_magalu": "DELETED"}
            rodada.contar(conversa, False, 0, True)
        else:
            await _aplicar_pergunta(session, canal, integration, q, rodada)
        await gravar.fim_do_item(session)


async def _recusar_ultima_resposta(
    session: AsyncSession, conversa: AtendimentoConversa, qid: str, *, ate: datetime | None = None
) -> bool:
    """A resposta da loja mais recente desta pergunta vira `falhou` (moderação). → mudou?

    `ate` = quando a moderação recusou: só conta a resposta que já existia
    ali. A lista atrasada, ainda com a recusa ANTIGA, não derruba a resposta
    nova que saiu depois dela.
    """
    momento = func.coalesce(AtendimentoMensagem.enviada_em, AtendimentoMensagem.created_at)
    q = select(AtendimentoMensagem).where(
        AtendimentoMensagem.conversa_id == conversa.id,
        AtendimentoMensagem.autor == AUTOR_LOJA,
        AtendimentoMensagem.status.in_((MSG_ENVIADA, MSG_REVISAR)),
        or_(
            AtendimentoMensagem.externo_id.is_(None),
            AtendimentoMensagem.externo_id.like(f"{PREFIXO_RESPOSTA}{qid}%"),
        ),
    )
    if ate is not None:
        q = q.where(momento <= ate + FOLGA_RELOGIO)
    msg = await session.scalar(q.order_by(momento.desc()).limit(1))
    if msg is None:
        return False
    return _aplicar_moderacao(msg, AUTOR_LOJA, PERGUNTA_RESPOSTA_REJEITADA.lower(), True)


async def _pergunta_pendente(
    session: AsyncSession, conversa: AtendimentoConversa
) -> AtendimentoMensagem | None:
    """A pergunta desta conversa, se ela ainda espera resposta na Magalu; senão None.

    Pendente = último status visto WAITING_RESPONSE ou RESPONSE_REJECTED e
    nenhuma resposta da loja que conte (a nossa enviada ou em conferência, ou
    a do portal). A recusada pela moderação é `falhou` e não conta.
    """
    qid = _qid(conversa)
    status = _id(_dict(conversa.dados).get("status_magalu")).upper()
    if not qid or status not in PERGUNTAS_QUE_ESPERAM:
        return None
    respondida = await session.scalar(
        select(AtendimentoMensagem.id)
        .where(
            AtendimentoMensagem.conversa_id == conversa.id,
            AtendimentoMensagem.autor == AUTOR_LOJA,
            AtendimentoMensagem.status.in_((MSG_ENVIADA, MSG_REVISAR)),
        )
        .limit(1)
    )
    if respondida is not None:
        return None
    return await session.scalar(
        select(AtendimentoMensagem).where(
            AtendimentoMensagem.conversa_id == conversa.id,
            AtendimentoMensagem.externo_id == f"{PREFIXO_PERGUNTA}{qid}",
        )
    )


# ── Chat ──────────────────────────────────────────────────────────────────


def _tags(conv: dict) -> dict[str, str]:
    saida: dict[str, str] = {}
    for t in _lista(conv.get("tags"))[:MAX_TAGS]:
        nome = _id(t.get("name")).lower()[:100]
        valor = _id(t.get("value"))[:200]
        if nome and valor:
            saida[nome] = valor
    return saida


def _primeira(tags: dict[str, str], nomes: tuple[str, ...]) -> str | None:
    return next((tags[n] for n in nomes if tags.get(n)), None)


def _comprador(conv: dict) -> tuple[str | None, str | None]:
    """(id, nome) de quem abriu a conversa — o vendedor não abre conversa."""
    de = _dict(conv.get("from_user"))
    uid = (
        _id(de.get("external_id"))
        or _id(de.get("id"))
        or _id(de.get("ref_key"))
        or _id(de.get("customer_id"))
    )
    nome = _texto(de.get("full_name")) or _texto(de.get("name"))
    return uid or None, nome


async def _ler_mensagens_chat(ler: _Leitor, cliente: Any, cid: str) -> list[_Mensagem]:
    """Todas as mensagens da conversa (paginado, até o teto). Só HTTP."""
    itens: dict[str, _Mensagem] = {}
    offset = 0
    for _ in range(MAX_PAGINAS_MENSAGENS):
        corpo = await ler(
            lambda offset=offset: cliente.mensagens_da_conversa(cid, offset=offset, limit=PAGINA)
        )
        lote = _lista(_dict(corpo).get("results"))
        for m in lote:
            item = _mensagem_chat(m)
            if item is not None:
                itens[item.externo_id] = item
        offset += len(lote)
        total = _inteiro(_dict(_dict(corpo.get("meta")).get("page")).get("total"))
        if len(lote) < PAGINA or (total and offset >= total):
            break
    return list(itens.values())


async def _processar_conversa(
    session: AsyncSession,
    canal: AtendimentoCanal,
    integration: Integration,
    cliente: Any,
    ler: _Leitor,
    conv: dict,
    agora: datetime,
    rodada: _Rodada,
    *,
    existente: AtendimentoConversa | None,
    forcar: bool = False,
) -> bool:
    """Uma conversa do chat: lê as mensagens se ela mexeu e grava. → leu da Magalu?"""
    cid = _id(conv.get("id"))
    if not cid:
        return False
    ultima = _data(conv.get("last_interaction_at")) or _data(conv.get("updated_at"))
    lida_ate = _carimbo(ultima)
    status = _id(conv.get("status")).upper() or CONVERSA_ABERTA_MAGALU
    precisa_ler = existente is None or forcar or _dict(existente.dados).get("lida_ate") != lida_ate
    # HTTP antes de qualquer escrita: falha no meio não deixa conversa pela metade.
    itens = await _ler_mensagens_chat(ler, cliente, cid) if precisa_ler else []

    tags = _tags(conv)
    comprador_id, comprador_nome = _comprador(conv)
    dados: dict[str, Any] = {
        "conversation_id": cid,
        "status_magalu": status,
        "relida_em": agora.isoformat(timespec="seconds"),
    }
    if precisa_ler:
        dados["lida_ate"] = lida_ate
        dados["mensagens_lidas_em"] = agora.isoformat(timespec="seconds")
    if tags:
        dados["tags"] = tags
    pedido = _primeira(tags, _TAGS_PEDIDO)
    sku = _primeira(tags, _TAGS_PRODUTO)
    titulo = _texto(conv.get("display_name"))
    conversa, criada = await gravar.upsert_conversa(
        session,
        canal=canal,
        integration=integration,
        plataforma=PLATAFORMA,
        canal_nome=CANAL_CHAT,
        externo_id=cid,
        conta=await _nome_da_loja(session, integration),
        comprador_id=comprador_id,
        comprador_nome=comprador_nome,
        pedido_marketplace=pedido,
        anuncio_id=sku,
        anuncio_titulo=titulo,
        nao_lidas=_inteiro(conv.get("unread_to_count")),
        dados=dados,
    )
    antes = conversa.situacao
    novas, refazer = await _gravar_mensagens(session, conversa, itens, agora)
    # Situação DEPOIS das mensagens: mensagem nova do cliente reabre a
    # conversa fechada (gravar._reabrir_se_nova), e só então a Magalu decide.
    _ajustar_situacao(conversa, None if status == CONVERSA_ABERTA_MAGALU else status)
    await session.flush()
    if criada or novas or refazer or antes != conversa.situacao:
        rodada.contar(conversa, criada, novas, antes != conversa.situacao or refazer)
    else:
        rodada.lidas += 1
    return precisa_ler


async def _tem_nossa_em_moderacao(session: AsyncSession, conversa: AtendimentoConversa) -> bool:
    """Há resposta NOSSA ainda sem decisão da moderação nesta conversa?"""
    linhas = await session.execute(
        select(AtendimentoMensagem.payload).where(
            AtendimentoMensagem.conversa_id == conversa.id,
            AtendimentoMensagem.autor == AUTOR_LOJA,
            AtendimentoMensagem.origem.in_(ORIGENS_DAVINCI),
            AtendimentoMensagem.status == MSG_ENVIADA,
        )
    )
    for payload in linhas.scalars():
        p = _dict(payload)
        decidida = _id(p.get("moderacao")).lower()
        if _dict(p.get("envio")).get("moderacao") == ENVIO_EM_MODERACAO and (
            not decidida or decidida in MODERACAO_PENDENTE
        ):
            return True
    return False


async def _sincronizar_chat(
    session: AsyncSession,
    canal: AtendimentoCanal,
    integration: Integration,
    cliente: Any,
    ler: _Leitor,
    agora: datetime,
    rodada: _Rodada,
) -> None:
    teto = max(1, int(get_settings().atendimento_sync_max_conversas or 1))
    cursor = dict(canal.cursor or {})
    desde = _data(cursor.get("desde")) or (agora - JANELA_PRIMEIRA_LEITURA)

    # 1. As conversas abertas com interação desde o cursor (com folga).
    conversas: dict[str, dict] = {}
    offset = 0
    cortada = True
    for _ in range(MAX_PAGINAS):
        corpo = await ler(
            lambda offset=offset: cliente.conversas(
                status=CONVERSA_ABERTA_MAGALU,
                desde=_iso(desde - MARGEM_CURSOR),
                offset=offset,
                limit=PAGINA,
            )
        )
        lote = _lista(_dict(corpo).get("results"))
        for c in lote:
            if _id(c.get("id")):
                conversas[_id(c.get("id"))] = c
        offset += len(lote)
        total = _inteiro(_dict(_dict(corpo.get("meta")).get("page")).get("total"))
        if len(lote) < PAGINA or (total and offset >= total):
            cortada = False
            break
    rodada.resultado.nao_lidas = sum(_inteiro(c.get("unread_to_count")) for c in conversas.values())
    existentes = await _conversas_existentes(session, canal, CANAL_CHAT, list(conversas))

    def _quando(c: dict) -> datetime:
        return _data(c.get("last_interaction_at")) or _data(c.get("updated_at")) or agora

    novo_desde = desde
    travado = cortada  # lista cortada ou conversa que falhou: o cursor não passa dali
    lidas_com_mensagens = 0
    for cid, conv in sorted(conversas.items(), key=lambda par: _quando(par[1])):
        if lidas_com_mensagens >= teto:
            travado = True
            break
        existente = existentes.get(cid)
        try:
            leu = await _processar_conversa(
                session,
                canal,
                integration,
                cliente,
                ler,
                conv,
                agora,
                rodada,
                existente=existente,
                forcar=await _forcar_por_moderacao(session, existente, agora),
            )
        except ErroMagalu as e:
            if e.limite:
                raise
            travado = True
            rodada.falhas.append(e)
            logger.warning(
                "atendimento_magalu_conversa_falhou",
                conversation_id=cid,
                http=e.http,
                codigo=e.codigo,
            )
            continue
        lidas_com_mensagens += int(leu)
        if not travado:
            novo_desde = max(novo_desde, _quando(conv))
            canal.cursor = {**(canal.cursor or {}), "desde": _carimbo(novo_desde)}
        await gravar.fim_do_item(session)
    if not travado:
        # A lista inteira foi lida: tudo o que mexeu até AGORA está gravado.
        canal.cursor = {**(canal.cursor or {}), "desde": _carimbo(max(novo_desde, agora))}

    # 2. Fora da lista: esperando resposta (a Magalu pode ter encerrado) ou
    #    com a NOSSA resposta ainda na moderação.
    for conversa in await _para_reler(session, canal, CANAL_CHAT, set(conversas), agora):
        cid = conversa.externo_id
        try:
            conv = await ler(lambda cid=cid: cliente.conversa(cid))
        except ErroMagalu as e:
            if e.limite:
                raise
            if e.http == 404:
                _ajustar_situacao(conversa, "NOT_FOUND")
                conversa.dados = {
                    **_dict(conversa.dados),
                    "relida_em": agora.isoformat(timespec="seconds"),
                }
                rodada.contar(conversa, False, 0, True)
            else:
                rodada.falhas.append(e)
            await gravar.fim_do_item(session)
            continue
        forcar = await _tem_nossa_em_moderacao(session, conversa)
        try:
            await _processar_conversa(
                session,
                canal,
                integration,
                cliente,
                ler,
                {**_dict(conv), "id": cid},
                agora,
                rodada,
                existente=conversa,
                forcar=forcar,
            )
        except ErroMagalu as e:
            if e.limite:
                raise
            rodada.falhas.append(e)
            continue
        await gravar.fim_do_item(session)


# ── SAC ───────────────────────────────────────────────────────────────────


def _retrato_ticket(t: dict) -> dict | None:
    """O pedido do protocolo para o painel (`dados["pedido_mkt"]`), sem ida à loja."""
    pedido = _dict(t.get("order"))
    numero = _texto(pedido.get("code")) or _texto(pedido.get("id"))
    if numero is None:
        return None
    entrega = _dict(pedido.get("delivery"))
    itens = []
    for it in _lista(entrega.get("items")):
        itens.append(
            {
                "titulo": _texto(it.get("name")) or _texto(it.get("description")),
                "imagem": _https(it.get("image")),
                "variacao": None,
                "sku": _texto(it.get("external_sku")) or _texto(it.get("sku")),
                "quantidade": _inteiro(it.get("quantity")) or 1,
                "preco": None,
            }
        )
    return {
        "fonte": PLATAFORMA,
        "pedido": numero,
        "status": None,
        "status_texto": None,
        "criado_em": None,
        "total": None,
        "moeda": "BRL",
        "itens": itens,
    }


async def _ler_mensagens_ticket(ler: _Leitor, cliente: Any, tid: str) -> list[_Mensagem]:
    """As mensagens do protocolo, da mais NOVA para a mais antiga (até 200). Só HTTP.

    O `_offset` do SAC vai no máximo a 100: pedir a página 200 dava 422 e
    derrubava o protocolo (e o cursor com ele). Protocolo com mais de 200
    mensagens fica com as 200 mais recentes — as mais antigas já foram lidas
    nas rodadas em que chegaram.
    """
    itens: dict[str, _Mensagem] = {}
    for offset in OFFSETS_SAC:
        corpo = await ler(
            lambda offset=offset: cliente.mensagens_do_ticket(
                tid, offset=offset, limit=PAGINA_SAC, ordem="created_at:desc"
            )
        )
        lote = _lista(_dict(corpo).get("results"))
        for m in lote:
            item = _mensagem_sac(m)
            if item is not None:
                itens[item.externo_id] = item
        if len(lote) < PAGINA_SAC:
            break
    else:
        logger.info("atendimento_magalu_ticket_mensagens_cortadas", ticket_id=tid)
    return list(itens.values())


def _momento_sql():
    return func.coalesce(AtendimentoMensagem.enviada_em, AtendimentoMensagem.created_at)


async def _ultimo_pedido_sac(
    session: AsyncSession, conversa: AtendimentoConversa
) -> AtendimentoMensagem | None:
    """A última fala de quem não é a loja no protocolo: o cliente ou a Magalu (mediação)."""
    momento = _momento_sql()
    return await session.scalar(
        select(AtendimentoMensagem)
        .where(
            AtendimentoMensagem.conversa_id == conversa.id,
            AtendimentoMensagem.autor.in_((AUTOR_CLIENTE, AUTOR_SISTEMA)),
        )
        .order_by(momento.desc(), AtendimentoMensagem.created_at.desc())
        .limit(1)
    )


def _cobranca_da_magalu(msg: AtendimentoMensagem | None) -> bool:
    """A mensagem é a mediação da Magalu pedindo algo à LOJA (channel → seller)?"""
    p = _dict(msg.payload) if msg is not None else {}
    return (
        msg is not None
        and msg.autor == AUTOR_SISTEMA
        and p.get("remetente") == "magalu"
        and p.get("destino") == _DESTINO_LOJA
    )


async def _vez_da_loja(
    session: AsyncSession, conversa: AtendimentoConversa, aguarda_loja: bool, agora: datetime
) -> str | None:
    """De quem é a vez no protocolo → `dados[CHAVE_VEZ_DA_LOJA]` (ISO = da loja; None = não).

    Quem diz é a MAGALU (status waiting_seller), não o autor da última
    mensagem: a mediação que cobra a loja é `sistema`, e o cliente que fala
    com a Magalu (destination channel) não pede nada à loja. A exceção é a
    loja ter acabado de responder o último pedido (do cliente ou da
    mediação) e a Magalu ainda não ter virado o status: `CARENCIA_SAC` de
    folga. Resposta ao canal da Magalu não responde o COMPRADOR.
    """
    if not aguarda_loja:
        return None
    momento = _momento_sql()
    pedido = await _ultimo_pedido_sac(session, conversa)
    q = select(AtendimentoMensagem.id).where(
        AtendimentoMensagem.conversa_id == conversa.id,
        AtendimentoMensagem.autor == AUTOR_LOJA,
        AtendimentoMensagem.status != MSG_FALHOU,
        momento > agora - CARENCIA_SAC,
    )
    if pedido is not None:
        q = q.where(momento > (pedido.enviada_em or pedido.created_at))
        if pedido.autor == AUTOR_CLIENTE:
            destino = func.coalesce(
                AtendimentoMensagem.payload["destino"].astext,
                AtendimentoMensagem.payload["envio"]["destino"].astext,
                DESTINO_CLIENTE,
            )
            q = q.where(destino != DESTINO_MAGALU)
    if await session.scalar(q.limit(1)) is not None:
        return None
    return agora.isoformat(timespec="seconds")


async def _destino_sac(session: AsyncSession, conversa: AtendimentoConversa) -> str:
    """Para quem vai a resposta do SAC: para quem pediu por último.

    A mediação da Magalu cobrando a loja (channel → seller) é respondida à
    Magalu; o resto, ao comprador.
    """
    pedido = await _ultimo_pedido_sac(session, conversa)
    return DESTINO_MAGALU if _cobranca_da_magalu(pedido) else DESTINO_CLIENTE


async def _processar_ticket(
    session: AsyncSession,
    canal: AtendimentoCanal,
    integration: Integration,
    cliente: Any,
    ler: _Leitor,
    t: dict,
    agora: datetime,
    rodada: _Rodada,
    *,
    existente: AtendimentoConversa | None,
    forcar: bool = False,
) -> bool:
    """Um protocolo: lê as mensagens se ele mexeu e grava. → leu da Magalu?"""
    tid = _id(t.get("id"))
    if not tid:
        return False
    atualizado = _data(t.get("updated_at")) or _data(t.get("created_at"))
    lida_ate = _carimbo(atualizado)
    status = _id(t.get("status")).lower()
    fechado = t.get("closed") is True or status == TICKET_FECHADO
    precisa_ler = existente is None or forcar or _dict(existente.dados).get("lida_ate") != lida_ate
    itens = await _ler_mensagens_ticket(ler, cliente, tid) if precisa_ler else []

    pedido = _dict(t.get("order"))
    entrega = _dict(pedido.get("delivery"))
    primeiro = next(iter(_lista(entrega.get("items"))), {})
    prazo = _data(t.get("due_date"))
    aguarda_loja = status == TICKET_AGUARDA_LOJA and not fechado
    dados: dict[str, Any] = {
        "ticket_id": tid,
        "protocolo": _texto(t.get("protocol")),
        "motivo": _texto(t.get("reason")),
        "tipo_ticket": _texto(t.get("type")),
        "origem_ticket": _texto(t.get("origin")),
        "status_magalu": status or None,
        "order_id": _texto(pedido.get("id")),
        # O prazo da Magalu só vale enquanto a vez é da loja; fora disso sai
        # (None regrava a chave: o `upsert_conversa` mescla os dados). Lido
        # AGORA, junto com as mensagens: vale mesmo vencido (`gravar._derivar`).
        CHAVE_PRAZO_PLATAFORMA: _carimbo(prazo) if aguarda_loja else None,
        CHAVE_PRAZO_LIDO_EM: agora.isoformat(timespec="seconds"),
        "relida_em": agora.isoformat(timespec="seconds"),
    }
    if precisa_ler:
        dados["lida_ate"] = lida_ate
        dados["mensagens_lidas_em"] = agora.isoformat(timespec="seconds")
    retrato = _retrato_ticket(t)
    if retrato is not None:
        dados["pedido_mkt"] = retrato
    # O protocolo não traz o comprador: vem de quem escreveu como cliente.
    comprador = next((i for i in itens if i.autor == AUTOR_CLIENTE), None)
    conversa, criada = await gravar.upsert_conversa(
        session,
        canal=canal,
        integration=integration,
        plataforma=PLATAFORMA,
        canal_nome=CANAL_SAC,
        externo_id=tid,
        conta=await _nome_da_loja(session, integration),
        comprador_id=comprador.remetente_id if comprador else None,
        comprador_nome=comprador.remetente_nome if comprador else None,
        pedido_marketplace=_texto(pedido.get("code")) or _texto(pedido.get("id")),
        anuncio_id=_texto(primeiro.get("external_sku")) or _texto(primeiro.get("sku")),
        anuncio_titulo=_texto(primeiro.get("name")),
        nao_lidas=1 if aguarda_loja else 0,
        dados=dados,
    )
    antes = conversa.situacao
    antes_prazo = conversa.prazo_resposta_em
    antes_fila = conversa.aguardando_resposta
    novas, refazer = await _gravar_mensagens(session, conversa, itens, agora)
    # A fila do SAC é o STATUS do protocolo (depois das mensagens: a resposta
    # que acabou de chegar conta na folga).
    conversa.dados = {
        **_dict(conversa.dados),
        CHAVE_VEZ_DA_LOJA: await _vez_da_loja(session, conversa, aguarda_loja, agora),
    }
    _ajustar_situacao(conversa, TICKET_FECHADO if fechado else None)
    # O prazo e a vez da Magalu podem ter mudado sem mensagem nova: refaz a fila.
    gravar.recalcular(conversa)
    await session.flush()
    mudou = (
        antes != conversa.situacao
        or refazer
        or antes_prazo != conversa.prazo_resposta_em
        or antes_fila != conversa.aguardando_resposta
    )
    if criada or novas or mudou:
        rodada.contar(conversa, criada, novas, mudou)
    else:
        rodada.lidas += 1
    return precisa_ler


async def _listar_fila(ler: _Leitor, cliente: Any) -> tuple[list[dict], bool]:
    """A fila da Magalu (a vez é da loja), do prazo mais curto ao mais longo.

    → (protocolos, cortada?).

    O SAC recusa `_offset` acima de 100: são no máximo duas páginas de 100 —
    os mais urgentes primeiro (`due_date:asc`), para caberem.
    """
    tickets: list[dict] = []
    for offset in OFFSETS_SAC:
        try:
            corpo = await ler(
                lambda offset=offset: cliente.tickets(
                    status=TICKET_AGUARDA_LOJA,
                    ordem="due_date:asc",
                    offset=offset,
                    limit=PAGINA_SAC,
                )
            )
        except ErroMagalu as e:
            if offset and e.http == 422:
                return tickets, True  # a paginação, não a fila: fica o que veio
            raise
        lote = _lista(_dict(corpo).get("results"))
        tickets.extend(lote)
        if len(lote) < PAGINA_SAC:
            return tickets, False
    return tickets, True


async def _listar_recentes(ler: _Leitor, cliente: Any, desde: datetime) -> tuple[list[dict], bool]:
    """Os protocolos mexidos desde `desde`, do mais antigo ao mais novo. → (protocolos, cortada?)

    Sem offset: a página seguinte anda pela DATA (`updated_at_gte` = o
    `updated_at` do último da página cheia; quem se repete na virada sai pelo
    id). Uma página cheia inteira no mesmo segundo não tem como andar — fica
    cortada, e o cursor anda até onde leu.
    """
    tickets: dict[str, dict] = {}
    corte = desde
    for pagina in range(MAX_PAGINAS):
        try:
            corpo = await ler(
                lambda corte=corte: cliente.tickets(
                    atualizado_desde=_iso(corte),
                    ordem="updated_at:asc",
                    offset=0,
                    limit=PAGINA_SAC,
                )
            )
        except ErroMagalu as e:
            if pagina and e.http == 422:
                return list(tickets.values()), True
            raise
        lote = _lista(_dict(corpo).get("results"))
        for t in lote:
            if _id(t.get("id")):
                tickets[_id(t.get("id"))] = t
        if len(lote) < PAGINA_SAC:
            return list(tickets.values()), False
        ultimo = _data(lote[-1].get("updated_at"))
        if ultimo is None or ultimo.replace(microsecond=0) <= corte.replace(microsecond=0):
            return list(tickets.values()), True
        corte = ultimo
    return list(tickets.values()), True


def _guardar_falhas(canal: AtendimentoCanal, falhas: dict[str, int], pulados: dict) -> None:
    """Grava no cursor os protocolos com erro (contagem) e os que o cursor pulou."""
    cursor = {k: v for k, v in (canal.cursor or {}).items() if k not in ("falhas", "pulados")}
    if falhas:
        cursor["falhas"] = dict(list(falhas.items())[-MAX_GUARDADOS:])
    if pulados:
        cursor["pulados"] = dict(list(pulados.items())[-MAX_GUARDADOS:])
    canal.cursor = cursor


async def _sincronizar_sac(
    session: AsyncSession,
    canal: AtendimentoCanal,
    integration: Integration,
    cliente: Any,
    ler: _Leitor,
    agora: datetime,
    rodada: _Rodada,
) -> None:
    teto = max(1, int(get_settings().atendimento_sync_max_conversas or 1))
    cursor = dict(canal.cursor or {})
    desde = _data(cursor.get("desde")) or (agora - JANELA_PRIMEIRA_LEITURA)
    falhas = {k: _inteiro(v) for k, v in _dict(cursor.get("falhas")).items()}
    pulados = {k: _dict(v) for k, v in _dict(cursor.get("pulados")).items()}

    # 1. A fila da Magalu (a vez é da loja) e o que mexeu desde o cursor
    #    (resposta dada no portal, protocolo encerrado, mensagem nova). A fila
    #    é gravada mesmo com a lista dos recentes falhando (o cursor não anda).
    fila, fila_cortada = await _listar_fila(ler, cliente)
    if fila_cortada:
        rodada.avisos.append(
            f"fila do SAC com mais de {len(fila)} protocolos: "
            "os de prazo mais longo ficam para as próximas rodadas"
        )
    try:
        recentes, cortada = await _listar_recentes(ler, cliente, desde - MARGEM_CURSOR)
    except ErroMagalu as e:
        if e.limite:
            raise
        rodada.falhas.append(e)
        logger.warning(
            "atendimento_magalu_recentes_falhou",
            canal_id=str(canal.id),
            http=e.http,
            codigo=e.codigo,
        )
        recentes, cortada = [], True
    rodada.resultado.nao_lidas = len(fila)
    tickets: dict[str, dict] = {}
    for t in [*fila, *recentes]:
        if _id(t.get("id")):
            tickets[_id(t.get("id"))] = t
    do_cursor = {_id(t.get("id")) for t in recentes}
    existentes = await _conversas_existentes(session, canal, CANAL_SAC, list(tickets))

    def _quando(t: dict) -> datetime:
        return _data(t.get("updated_at")) or _data(t.get("created_at")) or agora

    novo_desde = desde
    # Protocolo que falhou (ou o teto da rodada): o cursor não passa dali. A
    # lista cortada não trava: ela vem do mais antigo ao mais novo, e o
    # cursor anda até o último lido.
    travado = False
    lidos = 0
    for tid, t in sorted(tickets.items(), key=lambda par: _quando(par[1])):
        if lidos >= teto:
            travado = True
            break
        existente = existentes.get(tid)
        try:
            leu = await _processar_ticket(
                session,
                canal,
                integration,
                cliente,
                ler,
                t,
                agora,
                rodada,
                existente=existente,
                forcar=await _forcar_por_moderacao(session, existente, agora),
            )
        except ErroMagalu as e:
            if e.limite:
                raise
            rodada.falhas.append(e)
            falhas[tid] = falhas.get(tid, 0) + 1
            if tid in pulados or falhas[tid] >= MAX_FALHAS_SEGUIDAS:
                # Falha que persiste não segura o cursor para sempre (a janela
                # cresceria até a lista cortar): ele passa, e o protocolo é
                # relido pelo id (`_reler_pulados`).
                pulados.setdefault(tid, {"desde": agora.isoformat(timespec="seconds")})
                rodada.avisos.append(
                    f"protocolo {tid}: {falhas[tid]} rodadas seguidas com erro — "
                    "o cursor passou; ele é relido pelo id"
                )
            else:
                travado = True
            _guardar_falhas(canal, falhas, pulados)
            logger.warning(
                "atendimento_magalu_ticket_falhou",
                ticket_id=tid,
                http=e.http,
                codigo=e.codigo,
                seguidas=falhas[tid],
            )
            continue
        tinha_falha = falhas.pop(tid, None) is not None
        if pulados.pop(tid, None) is not None or tinha_falha:
            _guardar_falhas(canal, falhas, pulados)
        lidos += int(leu)
        if tid in do_cursor and not travado:
            novo_desde = max(novo_desde, _quando(t))
            canal.cursor = {**(canal.cursor or {}), "desde": _carimbo(novo_desde)}
        await gravar.fim_do_item(session)
    if not travado and not cortada:
        canal.cursor = {**(canal.cursor or {}), "desde": _carimbo(max(novo_desde, agora))}
    # A contagem só vale para quem ainda vem na lista (o resto o cursor já passou).
    falhas = {k: v for k, v in falhas.items() if k in tickets}
    _guardar_falhas(canal, falhas, pulados)

    # 2. Fora das listas e esperando resposta (ou com a nossa em moderação): pelo id.
    listados = set(tickets)
    for conversa in await _para_reler(session, canal, CANAL_SAC, listados | set(pulados), agora):
        tid = conversa.externo_id
        forcar = await _tem_nossa_em_moderacao(session, conversa)
        try:
            t = await ler(lambda tid=tid: cliente.ticket(tid))
            await _processar_ticket(
                session,
                canal,
                integration,
                cliente,
                ler,
                {**_dict(t), "id": tid},
                agora,
                rodada,
                existente=conversa,
                forcar=forcar,
            )
        except ErroMagalu as e:
            if e.limite:
                raise
            rodada.falhas.append(e)
            continue
        await gravar.fim_do_item(session)

    # 3. Os que o cursor pulou depois de falhar, fora da lista: pelo id.
    await _reler_pulados(
        session, canal, integration, cliente, ler, agora, rodada, pulados, listados
    )


async def _reler_pulados(
    session: AsyncSession,
    canal: AtendimentoCanal,
    integration: Integration,
    cliente: Any,
    ler: _Leitor,
    agora: datetime,
    rodada: _Rodada,
    pulados: dict[str, dict],
    listados: set[str],
) -> None:
    """Relê pelo id (no máximo a cada 15 min, por até 7 dias) o protocolo que o cursor pulou.

    A falha aqui vai como aviso (o canal leu o resto): o protocolo continua
    na lista dos pulados até ler, sumir (404) ou passar dos 7 dias.
    """
    feitas = 0
    for tid, info in list(pulados.items()):
        if tid in listados:
            continue  # já tentado na lista desta rodada
        primeira = _data(info.get("desde"))
        if primeira is not None and agora - primeira > JANELA_PRIMEIRA_LEITURA:
            pulados.pop(tid, None)
            _guardar_falhas(canal, _dict(canal.cursor).get("falhas") or {}, pulados)
            rodada.avisos.append(
                f"protocolo {tid}: com erro há mais de 7 dias — deixou de ser relido"
            )
            logger.warning("atendimento_magalu_ticket_abandonado", ticket_id=tid)
            continue
        tentado = _data(info.get("tentado"))
        if tentado is not None and agora - tentado < RELER_DEPOIS_DE:
            continue
        if feitas >= MAX_RELEITURAS:
            break
        feitas += 1
        pulados[tid] = {**info, "tentado": agora.isoformat(timespec="seconds")}
        _guardar_falhas(canal, _dict(canal.cursor).get("falhas") or {}, pulados)
        try:
            t = await ler(lambda tid=tid: cliente.ticket(tid))
            existente = (await _conversas_existentes(session, canal, CANAL_SAC, [tid])).get(tid)
            await _processar_ticket(
                session,
                canal,
                integration,
                cliente,
                ler,
                {**_dict(t), "id": tid},
                agora,
                rodada,
                existente=existente,
            )
        except ErroMagalu as e:
            if e.limite:
                raise
            if e.http == 404:
                pulados.pop(tid, None)
            else:
                rodada.avisos.append(f"protocolo {tid}: releitura pelo id falhou ({e.texto})")
            _guardar_falhas(canal, _dict(canal.cursor).get("falhas") or {}, pulados)
            logger.info(
                "atendimento_magalu_pulado_falhou", ticket_id=tid, http=e.http, codigo=e.codigo
            )
            continue
        pulados.pop(tid, None)
        _guardar_falhas(canal, _dict(canal.cursor).get("falhas") or {}, pulados)
        await gravar.fim_do_item(session)


# ── Saída ─────────────────────────────────────────────────────────────────


def _payload_envio(r: httpx.Response, corpo: Any, ref: str) -> dict:
    """O que guardar da resposta da Magalu: status, ids e moderação — nunca o texto."""
    corpo = _dict(corpo)
    payload: dict[str, Any] = {"http": r.status_code, "ref": ref, "moderacao": ENVIO_EM_MODERACAO}
    for chave in ("id", "transaction_id"):
        if _id(corpo.get(chave)):
            payload[chave] = _id(corpo.get(chave))
    decidida = _moderacao(corpo.get("moderation"))
    if decidida and decidida not in MODERACAO_PENDENTE:
        payload["moderacao"] = decidida
    return payload


async def _postar(
    chamada: Callable[[], Awaitable[httpx.Response]],
    *,
    conversa: AtendimentoConversa,
    ref: str,
    externo_id: str | None = None,
    id_da_resposta: bool = False,
) -> ResultadoEnvio:
    """Faz o POST e traduz para `ResultadoEnvio`. Nunca levanta.

    2xx = a Magalu recebeu e a resposta vai para a MODERAÇÃO (`enviada`, com
    `moderacao = em_moderacao`), a não ser que o próprio retorno já diga que
    ela recusou · 4xx = não saiu · 5xx / sem resposta = AMBÍGUO (pode ter
    saído — `revisar`, ninguém retenta; o cliente não repete 5xx no POST).
    Falha de CONEXÃO é recusa limpa: o pedido nem chegou à Magalu.
    """
    try:
        r = await chamada()
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ProxyError) as e:
        return ResultadoEnvio(ok=False, erro=f"sem_conexao:{type(e).__name__}")
    except httpx.HTTPError as e:
        return ResultadoEnvio(ok=False, ambiguo=True, erro=f"sem_resposta:{type(e).__name__}")
    except ValueError as e:
        # O cliente recusou antes de mandar (texto acima do limite da API).
        return ResultadoEnvio(ok=False, erro=f"envio_invalido:{str(e).split(' ', 1)[0]}")
    except RuntimeError:
        # O token não renovou ANTES do envio: nada saiu.
        return ResultadoEnvio(ok=False, erro="token_nao_renovou")
    except Exception as e:  # noqa: BLE001 — erro da plataforma nunca levanta daqui
        logger.exception("atendimento_magalu_envio_inesperado", conversa_id=str(conversa.id))
        return ResultadoEnvio(ok=False, ambiguo=True, erro=f"inesperado:{type(e).__name__}")

    corpo = _json(r)
    if 200 <= r.status_code < 300:
        payload = _payload_envio(r, corpo, ref)
        if payload.get("moderacao") in MODERACAO_REJEITADA:
            # Ninguém retenta em cima: o mesmo texto seria recusado de novo.
            logger.info("atendimento_magalu_envio_moderado", conversa_id=str(conversa.id))
            return ResultadoEnvio(ok=False, erro=ERRO_MODERACAO, payload=payload)
        externo = externo_id
        if externo is None and id_da_resposta:
            externo = _id(_dict(corpo).get("id")) or None
        return ResultadoEnvio(ok=True, externo_id=externo, payload=payload)

    codigo = _codigo_erro(r)
    html = _eh_html(r)
    logger.info(
        "atendimento_magalu_envio_recusado",
        conversa_id=str(conversa.id),
        http=r.status_code,
        codigo=codigo,
    )
    if r.status_code >= 500:
        return ResultadoEnvio(
            ok=False,
            ambiguo=True,
            erro=f"HTTP {r.status_code} {codigo}".strip(),
            payload={"http": r.status_code, "ref": ref},
        )
    if r.status_code in (401, 403) and not html:
        erro = f"HTTP {r.status_code} sem_escopo: {MOTIVO_SEM_PERMISSAO}"
    elif r.status_code == 429:
        erro = "HTTP 429: limite da Magalu, tente de novo em 1 minuto"
    else:
        erro = f"HTTP {r.status_code} {codigo}".strip()
    return ResultadoEnvio(
        ok=False, erro=erro, payload={"http": r.status_code, "codigo": codigo, "ref": ref}
    )


async def _quem_assina(session: AsyncSession, integration: Integration | None) -> str:
    """O `owner.name` da resposta: o nome da LOJA (como a equipe a conhece)."""
    try:
        nome = await lojas.nome_da_loja(session, integration)
    except Exception:  # noqa: BLE001 — o nome é enfeite; a resposta sai
        nome = ""
    return (nome or (integration.name if integration is not None else "") or "Loja")[:100]


async def enviar_texto(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    integration: Integration | None,
    cliente: Any,
    texto: str,
) -> ResultadoEnvio:
    """Envia `texto` na conversa. Timeout/5xx = ambiguo. Nunca levanta.

    Pergunta: responde EXATAMENTE a pergunta desta conversa, se ela ainda
    espera resposta (POST /answer). A mensagem ganha o id `a:<pergunta>:<ref>`
    — o mesmo que a leitura vai trazer, então ela reconhece a linha.
    Chat: POST na conversa (até 2200); a linha ganha o `id` do 201.
    SAC: POST no protocolo (até 3000) para quem pediu por último: o comprador
    (`destination=customer`) ou, quando é a mediação da Magalu cobrando a
    loja, a Magalu (`channel`) — vai em `payload.destino`. O 202 não traz id:
    a leitura acha a nossa pelo `ref` (em `code`).
    """
    if not (texto or "").strip():
        return ResultadoEnvio(ok=False, erro="texto_vazio")
    limite = limite_caracteres(PLATAFORMA, conversa.canal)
    if len(texto) > limite:
        return ResultadoEnvio(ok=False, erro=f"envio_invalido:acima_do_limite_{limite}")
    ref = _nova_ref()
    nome = await _quem_assina(session, integration)
    if conversa.canal == CANAL_PERGUNTA:
        if await _pergunta_pendente(session, conversa) is None:
            return ResultadoEnvio(ok=False, erro="sem_pergunta_pendente")
        qid = _qid(conversa)
        return await _postar(
            lambda: cliente.responder_pergunta(
                qid, texto, autor_nome=nome, autor_id=AUTOR_EXTERNO_ID, ref=ref
            ),
            conversa=conversa,
            ref=ref,
            externo_id=f"{PREFIXO_RESPOSTA}{qid}:{ref}",
        )
    if conversa.canal == CANAL_CHAT:
        cid = conversa.externo_id
        return await _postar(
            lambda: cliente.enviar_mensagem_conversa(
                cid, texto, autor_nome=nome, autor_id=AUTOR_EXTERNO_ID, ref=ref
            ),
            conversa=conversa,
            ref=ref,
            id_da_resposta=True,
        )
    if conversa.canal == CANAL_SAC:
        tid = conversa.externo_id
        destino = await _destino_sac(session, conversa)
        resultado = await _postar(
            lambda: cliente.enviar_mensagem_ticket(
                tid,
                texto,
                autor_nome=nome,
                autor_codigo=AUTOR_EXTERNO_ID,
                destino=destino,
                ref=ref,
            ),
            conversa=conversa,
            ref=ref,
        )
        resultado.payload = {**(resultado.payload or {}), "destino": destino}
        return resultado
    return ResultadoEnvio(ok=False, erro=f"canal_desconhecido:{conversa.canal}")
