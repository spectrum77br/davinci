"""Adaptador da Shopee (chat do vendedor) para a caixa de atendimento.

Leitura por CONSULTA periódica (o push da Shopee fica para depois): a lista de
conversas vem da mais recente para a mais antiga, e cada rodada desce por ela
até encontrar o ponto em que a rodada anterior parou. Formato das respostas
medido em produção em 25/09/2026 e o sentido da lista em 28/09 (só leitura).

NUNCA marcar como lido AQUI (leitura e envio). Desde 14/10/2026 (Eduardo: "ao
passar também tem que marcar como visto, igual lá no Duoke") o cliente tem o
`chat_read_conversation`, e só o `lida.py` o chama — quando a PESSOA que mexe
abre a conversa de uma loja em que o DaVinci responde. Fora disso,
enquanto o Duoke estiver ligado, o "não lido" da Shopee é o aviso da equipe.
E ele é LIDO toda rodada (14/10/2026, `_atualizar_nao_lidas`): o total e a
lista só das não lidas (`type=unread`) acertam o `nao_lidas` de cada conversa
— a que a equipe abriu no Duoke volta a zero sem esperar mensagem nova.

O cursor do canal (`atendimento_canais.cursor`):

  ultimo_ts — o maior horário (ns) de última mensagem JÁ VISTO. Toda conversa
              acima dele na lista é novidade; toda conversa até ele ou já foi
              lida, ou está numa lacuna.
  lacunas   — trechos da lista ainda por ler, a mais recente primeiro: cada
              um é `{retomar, piso}` — continuar do `next_cursor` da Shopee
              (`retomar`) para baixo até o horário `piso`. Nasce quando uma
              passada para no TETO de conversas por rodada
              (`atendimento_sync_max_conversas`) antes do fim: loja com 300
              conversas novas (primeira leitura, Duoke desligado um dia) não
              pode ter a do meio pulada.

TODA rodada lê primeiro o TOPO (do agora para trás até o `ultimo_ts`) e só com
o que sobrar do teto continua as lacunas: a mensagem nova de hoje não espera
a caminhada antiga terminar para entrar na fila. A conversa da lacuna que
recebe mensagem nova sobe para o topo e é lida ali.

O SENTIDO da lista, medido em produção em 28/09/2026 (só leitura, loja kfa):

  direction=older  sem horário     → as MAIS NOVAS primeiro, decrescente; o
                                     `next_cursor.next_message_time_nano` é a
                                     hora da última da página, e `more` diz se
                                     há mais para trás.
  direction=older  + horário X     → as de ANTES de X, decrescente.
  direction=latest sem horário     → as MAIS ANTIGAS da loja (2023), CRESCENTE.
  direction=latest + horário X     → as de DEPOIS de X, crescente.

Então o topo é SEMPRE `older` sem horário (`DIRECAO_TOPO`), e as páginas
seguintes `older` com o horário do cursor. `latest` sem horário no topo
leria 2023 e daria a rodada por terminada ("já é tudo antigo") sem ver a
mensagem de hoje — os testes têm um cliente falso que devolve exatamente
isso, e reprovam se alguém usar `latest` no topo. A ordem continua não
presumida: se a página vier fora da ordem decrescente, a passada só termina
numa página em que todas as conversas (não fixadas) já são antigas.

`last_message_timestamp` da lista vem em NANOSSEGUNDOS (19 dígitos) e o
`created_timestamp` do `get_message` em segundos (10 dígitos). `page_size` 25
funciona; 60 devolveu lista VAZIA em várias lojas — nunca passar de 25.

Primeira rodada (cursor vazio): só conversas com mensagem nos últimos 7 dias —
o histórico inteiro de uma loja não é fila de atendimento.

A nossa resposta que a Shopee barra DEPOIS de aceitar (status `blocked` /
`censored_blacklist`) só aparece relendo as mensagens: as conversas com
resposta enviada pelo DaVinci nos últimos 10 min têm a 1ª página relida a
cada rodada (`_reconferir_enviadas`).

Quem é quem: `from_shop_id == shop_id` da loja → loja; senão, cliente. NUNCA
`from_shop_id != 0`: o comprador também vem com um (medido em 22/09).
Resposta automática, chatbot, notificação e a fala do afiliado entram como
`sistema` — ficam na conversa, mas não contam como pergunta nem como resposta
(a Shopee não conta resposta automática na taxa de resposta do chat).

CHAT DE AFILIADO (15/10/2026, o dono: "os números da Caixa têm que bater com
o Duoke"). O criador de conteúdo que divulga a loja conversa com ela num chat
à parte (`business_type` 11), que a lista padrão NÃO traz: medido em produção
(só leitura) — a conversa 379920374879551489 da Barbosa (1 não lida no Duoke)
não estava em nenhuma página da lista, nem no `get_unread_conversation_count`
(10 = as 10 da lista `unread`), e o `get_one_conversation`/`get_message`
dela respondiam `param_error`; com `business_type=11` as quatro chamadas a
trazem (Barbosa 1 não lida, Marquezini 3). Então cada rodada lê também a
LISTA DE AFILIADOS (`_ler_afiliados`, depois dos compradores, com teto
próprio e cursor próprio em `cursor["afiliado"]`, no mesmo formato), e o
não lido dela. A conversa ganha `dados.afiliado = true` e o `comprador_id` é
o do afiliado (`to_id`); a fala dele vira `sistema` com `from_id` = esse id
— conta no número da Caixa como no Duoke (`pendente.do_comprador_no_numero`)
sem abrir a vez na fila: a IA, a automática e a Caixa Humano não pegam (e o
DaVinci não envia nesse chat — `constantes.motivo_canal_sem_envio`). A
resposta da loja no chat de afiliado (pelo Seller Center/Duoke) é `loja`:
zera o número, como a resposta de pessoa no Duoke. Falha na lista de
afiliados não derruba a rodada (fica no log e o cursor dela espera). A
página da lista de afiliados que trouxer UMA conversa já gravada como de
comprador para a lista inteira na rodada, antes de gravar qualquer uma
(`_conferir_pagina_de_afiliados`: se a Shopee um dia ignorar o parâmetro,
nenhum comprador vira afiliado).

Cartões e painel (28/09/2026, "a cara do Duoke"): a mensagem de produto e a
de pedido viram cartões com foto, título, preço e status, e a conversa com
pedido ligado ganha o retrato do pedido (`dados["pedido_mkt"]`) — tudo por
`enriquecer`, SÓ LEITURA, com uma cota por rodada. A foto do comprador é a
`to_avatar` da própria lista de conversas.

Texto de comprador nunca vai para o log — só ids e contagens.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy import func, not_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AtendimentoCanal, AtendimentoConversa, AtendimentoMensagem, Integration
from app.services.atendimento import enriquecer, gravar, lojas
from app.services.atendimento.constantes import (
    AUTOR_CLIENTE,
    AUTOR_LOJA,
    AUTOR_SISTEMA,
    CANAL_CHAT,
    CHAVE_AFILIADO,
    MSG_ENVIADA,
    MSG_FALHOU,
    ORIGENS_DAVINCI,
    ResultadoEnvio,
    ResultadoSync,
    e_chat_de_afiliado,
    rotulo_tipo_plataforma,
)
from app.services.marketplaces.shopee import NEGOCIO_AFILIADO, NEGOCIO_COMPRADOR

logger = structlog.get_logger()

# Um dos valores de `constantes.PLATAFORMAS`.
PLATAFORMA = "shopee"

# Valores de `constantes.STATUS_CANAL` que este adaptador devolve.
STATUS_OK = "ok"
STATUS_SEM_ESCOPO = "sem_escopo"
STATUS_ERRO = "erro"

# Tipos de `atendimento_mensagens.tipo`.
TIPO_TEXTO = "texto"
TIPO_IMAGEM = "imagem"
TIPO_VIDEO = "video"
TIPO_PRODUTO = "produto"
TIPO_PEDIDO = "pedido"
TIPO_OUTRO = "outro"

# ── Paginação ─────────────────────────────────────────────────────────────
# 25 é o padrão da Shopee e o que funciona em todas as lojas; `page_size=60`
# devolveu lista VAZIA em várias (medido em 28/09) — vazio que a passada leria
# como "acabou". Nunca mais que 25.
PAGINA_CONVERSAS = 25
PAGINA_MENSAGENS = 25
# Mensagens de UMA conversa: desce até achar uma já gravada, no máximo 3
# páginas (75 mensagens). Conversa nova com histórico maior fica com o
# começo de fora — o que importa para responder é o fim.
PAGINAS_MENSAGENS = 3
JANELA_PRIMEIRA_LEITURA = timedelta(days=7)
# Do agora para trás (topo, SEM horário) e, nas páginas seguintes, mais
# antigas que o `next_timestamp_nano`: sempre `older`. NUNCA `latest` no
# topo: sem horário, a Shopee devolve as conversas MAIS ANTIGAS da loja, em
# ordem crescente (medido em 28/09 — ver o docstring do módulo).
DIRECAO_MAIS_ANTIGAS = "older"
DIRECAO_TOPO = DIRECAO_MAIS_ANTIGAS
TIPO_LISTA = "all"
# O NÃO LIDO fresco (14/10/2026, `_atualizar_nao_lidas`): a lista só das não
# lidas (`type=unread`), no máximo 4 páginas (100 conversas) por rodada —
# passou disso, marca o que viu e não zera ninguém.
TIPO_NAO_LIDAS = "unread"
MAX_PAGINAS_NAO_LIDAS = 4
# Lacunas guardadas no cursor: acima disso, as duas mais antigas viram uma
# só (relendo o trecho entre elas, que o `gravar` deduplica).
MAX_LACUNAS = 5
# A nossa resposta barrada DEPOIS de aceita: por quanto tempo a conversa tem
# a 1ª página de mensagens relida, e quantas por rodada.
RECONFERIR_ENVIO = timedelta(minutes=10)
MAX_RECONFERIR = 10
# Uma conversa que falha 3 rodadas seguidas é pulada (com log de erro) para
# não travar a caixa inteira da loja por causa de uma só. Antes disso, a
# rodada para e tenta de novo — mensagem perdida em silêncio é pior que
# canal em erro por alguns minutos.
MAX_FALHAS_CONVERSA = 3

# ── Vocabulário da Shopee ─────────────────────────────────────────────────
# Mensagem que não é fala de gente: notificação da plataforma, FAQ/chatbot
# (`bundle_message`, desde 05/11/2024).
_TIPOS_SISTEMA = frozenset({"notification", "bundle_message", "system"})
# `status` da mensagem (push de 18/04/2025): resposta automática da loja não
# conta como resposta — o comprador continua esperando gente.
_STATUS_AUTOMATICOS = frozenset({"auto_reply", "offwork_autoreply"})
# A Shopee barrou a mensagem (contato externo etc.): não chegou ao comprador.
_STATUS_BARRADOS = frozenset({"blocked", "censored_blacklist"})
# `business_type` 11 = chat de afiliado, não é comprador (push de 05/11/2024;
# na leitura é o parâmetro das chamadas, `NEGOCIO_AFILIADO` do cliente — o
# item do `get_message` não traz o campo, medido em 15/10/2026).
# A lista de afiliados por rodada: uma página (medido em 15/10/2026: 25
# conversas cobrem 5 semanas da Barbosa e 2 da Marquezini).
MAX_CONVERSAS_AFILIADO = 25
# Onde mora o cursor da lista de afiliados dentro de `atendimento_canais.cursor`.
CHAVE_CURSOR_AFILIADO = "afiliado"

# Código da Shopee no erro que o `ShopeeClient._call` levanta:
# "shopee_chat_send error_param: <mensagem>". Erro sem código ("HTTP 502",
# resposta não-JSON) não casa.
_RE_CODIGO = re.compile(r"^shopee_[a-z_]+ ([a-z][a-z0-9_.]*):")
# Permissão do app (módulo Chat não concedido): não adianta insistir.
_CODIGOS_SEM_ESCOPO = frozenset({"error_permission", "api_suspended", "error_forbidden"})
# Erro interno da Shopee: a mensagem pode ter saído.
_PISTAS_AMBIGUAS = ("server", "inner", "internal", "timeout", "unknown", "busy")


# ── Relógio ───────────────────────────────────────────────────────────────


def _ns(valor: Any) -> int | None:
    """Horário da Shopee em NANOSSEGUNDOS, qualquer que seja a unidade.

    O cursor da lista e o `last_message_timestamp` vêm em ns (19 dígitos) e o
    `created_timestamp` em segundos (medido em 28/09). A unidade continua
    deduzida pelo tamanho — até 11 dígitos é segundo, até 14 é milissegundo,
    até 17 é microssegundo, acima disso nanossegundo —, porque a Shopee já
    mudou formato de campo sem aviso e um horário lido 10⁹ vezes errado
    encerraria a caminhada na primeira conversa.
    """
    try:
        n = int(str(valor).strip())
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    if n < 10**11:
        return n * 10**9
    if n < 10**14:
        return n * 10**6
    if n < 10**17:
        return n * 10**3
    return n


def _datetime(ns: int) -> datetime:
    """ns → datetime UTC, sem passar por float (perderia os microssegundos)."""
    segundos, resto = divmod(ns, 10**9)
    return datetime.fromtimestamp(segundos, tz=UTC) + timedelta(microseconds=resto // 1000)


def para_datetime(valor: Any) -> datetime | None:
    """Horário da Shopee (s/ms/µs/ns) → datetime UTC, ou None."""
    ns = _ns(valor)
    return None if ns is None else _datetime(ns)


def _ns_de(quando: datetime) -> int:
    return int(quando.timestamp()) * 10**9 + quando.microsecond * 1000


# ── Erros ─────────────────────────────────────────────────────────────────


def _codigo_shopee(exc: BaseException) -> str | None:
    m = _RE_CODIGO.match(str(exc))
    return m.group(1) if m else None


def _sem_escopo(exc: BaseException) -> bool:
    return _codigo_shopee(exc) in _CODIGOS_SEM_ESCOPO


def _erro_operacao(exc: BaseException) -> str:
    """Erro para `ultimo_erro`/log: código e HTTP, nunca conteúdo de conversa."""
    if isinstance(exc, httpx.HTTPError):
        return f"shopee {type(exc).__name__}"
    codigo = _codigo_shopee(exc)
    if codigo:
        return f"shopee {codigo}"
    return str(exc)[:200]


def _int(valor: Any) -> int | None:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


# ── Cursor ────────────────────────────────────────────────────────────────


@dataclass
class _Lacuna:
    """Trecho da lista ainda por ler: do `retomar` (next_cursor) para baixo até o `piso` (ns)."""

    retomar: dict
    piso: int

    def json(self) -> dict:
        return {"retomar": dict(self.retomar), "piso": self.piso}


def _retomar_valido(bruto: Any) -> dict | None:
    if isinstance(bruto, dict) and bruto.get("next_message_time_nano"):
        return dict(bruto)
    return None


@dataclass
class _Cursor:
    ultimo_ts: int | None = None
    lacunas: list[_Lacuna] = field(default_factory=list)
    falhas: dict[str, int] = field(default_factory=dict)

    @classmethod
    def ler(cls, bruto: Any, *, piso_inicial: int) -> _Cursor:
        """`piso_inicial` = o começo da janela da primeira leitura (converte o formato antigo)."""
        d = bruto if isinstance(bruto, dict) else {}
        ultimo = _int(d.get("ultimo_ts"))
        lacunas: list[_Lacuna] = []
        for item in d.get("lacunas") or []:
            if not isinstance(item, dict):
                continue
            retomar, piso = _retomar_valido(item.get("retomar")), _int(item.get("piso"))
            if retomar and piso is not None:
                lacunas.append(_Lacuna(retomar, piso))
        # Formato anterior (uma caminhada só: `retomar` + `teto`): a caminhada
        # em curso vira uma lacuna, e o teto dela, o maior horário visto.
        antigo = _retomar_valido(d.get("retomar"))
        if antigo and not lacunas:
            lacunas.append(_Lacuna(antigo, ultimo if ultimo is not None else piso_inicial))
            teto = _int(d.get("teto"))
            ultimo = teto if teto is not None else ultimo
        falhas = d.get("falhas")
        return cls(
            ultimo_ts=ultimo,
            lacunas=lacunas,
            falhas={str(k): n for k, v in falhas.items() if (n := _int(v))}
            if isinstance(falhas, dict)
            else {},
        )

    def json(self) -> dict:
        # Dicionário NOVO: mutar o JSONB no lugar não marca a coluna como suja.
        return {
            "ultimo_ts": self.ultimo_ts,
            "lacunas": [lac.json() for lac in self.lacunas],
            "falhas": dict(self.falhas),
        }


def _juntar_lacunas(lacunas: list[_Lacuna]) -> list[_Lacuna]:
    """No máximo `MAX_LACUNAS`: as duas mais antigas viram uma (relendo o meio)."""
    lacunas = list(lacunas)
    while len(lacunas) > MAX_LACUNAS:
        acima, abaixo = lacunas[-2], lacunas[-1]
        lacunas[-2:] = [_Lacuna(acima.retomar, abaixo.piso)]
    return lacunas


# ── Mensagem ──────────────────────────────────────────────────────────────


@dataclass
class _Lida:
    """Uma mensagem da Shopee traduzida para o vocabulário do atendimento."""

    externo_id: str
    autor: str
    tipo: str
    texto: str | None
    anexos: list
    enviada_em: datetime | None
    pedido: str | None = None
    anuncio: str | None = None
    afiliado: bool = False
    barrada: str | None = None


def _texto_ou_none(valor: Any) -> str | None:
    return valor if isinstance(valor, str) else None


def _autor(msg: dict, shop_id: int, tipo_shopee: str, status: str, afiliado: bool) -> str:
    if (
        tipo_shopee in _TIPOS_SISTEMA
        or "notification" in tipo_shopee
        or status in _STATUS_AUTOMATICOS
    ):
        return AUTOR_SISTEMA
    if shop_id and _int(msg.get("from_shop_id")) == shop_id:
        # Também no chat de afiliado (15/10/2026): a resposta da loja é
        # resposta — zera o número da Caixa, como a de pessoa no Duoke.
        return AUTOR_LOJA
    if afiliado:
        # A fala do AFILIADO: `sistema` (não abre a vez na fila, a IA não
        # pega), e conta no número pelo `from_id` = o `comprador_id` da
        # conversa (`pendente.do_comprador_no_numero`).
        return AUTOR_SISTEMA
    return AUTOR_CLIENTE


def interpretar(msg: dict, shop_id: int, *, afiliado: bool = False) -> _Lida:
    """Mensagem crua do `get_message` → autor, tipo, texto, anexos e o que liga ao pedido.

    `afiliado` = a mensagem é do chat de AFILIADO (lida da lista de afiliados:
    o item do `get_message` não diz — só o push traz `business_type`).
    """
    tipo_shopee = str(msg.get("message_type") or "").strip().lower()
    status = str(msg.get("status") or "").strip().lower()
    conteudo = msg.get("content") if isinstance(msg.get("content"), dict) else {}
    origem = msg.get("source_content") if isinstance(msg.get("source_content"), dict) else {}
    afiliado = afiliado or _int(msg.get("business_type")) == NEGOCIO_AFILIADO

    # `source_content` = de onde o comprador veio (página do pedido/produto).
    pedido = str(origem.get("order_sn") or "").strip() or None
    anuncio = str(origem.get("item_id") or "").strip() or None
    texto: str | None = None
    anexos: list = []
    if tipo_shopee == "text":
        tipo, texto = TIPO_TEXTO, _texto_ou_none(conteudo.get("text"))
    elif tipo_shopee == "image":
        tipo = TIPO_IMAGEM
        url = conteudo.get("url") or conteudo.get("thumb_url")
        if url:
            anexos = [{"tipo": TIPO_IMAGEM, "url": str(url)}]
    elif tipo_shopee == "video":
        tipo = TIPO_VIDEO
        url = conteudo.get("video_url")
        if url:
            anexos = [{"tipo": TIPO_VIDEO, "url": str(url)}]
    elif tipo_shopee == "item":
        # Cartão só com o id: a foto, o título e o preço entram na gravação
        # (`_preencher_cartao`), que fala com a loja — aqui não há I/O.
        tipo = TIPO_PRODUTO
        anuncio = str(conteudo.get("item_id") or "").strip() or anuncio
        if anuncio:
            anexos = [enriquecer.cartao_produto_vazio(anuncio)]
    elif tipo_shopee == "order":
        tipo = TIPO_PEDIDO
        pedido = str(conteudo.get("order_sn") or "").strip() or pedido
        if pedido:
            anexos = [enriquecer.cartao_pedido_vazio(pedido)]
    else:
        # sticker, faq_liveagent, notification, bundle_message... — o texto
        # que vier junto (a opção do FAQ que o comprador tocou) ou o rótulo em
        # português; o conteúdo cru fica no payload.
        tipo = TIPO_OUTRO
        texto = _texto_ou_none(conteudo.get("text")) or rotulo_tipo_plataforma(tipo_shopee)

    autor = _autor(msg, shop_id, tipo_shopee, status, afiliado)
    return _Lida(
        externo_id=str(msg.get("message_id") or "").strip(),
        autor=autor,
        tipo=tipo,
        texto=texto,
        anexos=anexos,
        enviada_em=para_datetime(msg.get("created_timestamp")),
        pedido=pedido,
        anuncio=anuncio,
        afiliado=afiliado,
        barrada=status if status in _STATUS_BARRADOS else None,
    )


# ── Leitura ───────────────────────────────────────────────────────────────


def _negocio_kw(negocio: int) -> dict[str, int]:
    """O `business_type` das chamadas de leitura — só no chat de afiliado: a do
    comprador sai como sempre saiu, sem o parâmetro."""
    return {} if negocio == NEGOCIO_COMPRADOR else {"business_type": negocio}


def _negocio_da_conversa(conversa: AtendimentoConversa) -> int:
    """De que lista a conversa gravada é (`dados.afiliado`)."""
    if e_chat_de_afiliado(conversa.plataforma, conversa.dados):
        return NEGOCIO_AFILIADO
    return NEGOCIO_COMPRADOR


def _afiliado_sql() -> Any:
    """As conversas do chat de afiliado (a lista à parte do não lido)."""
    return gravar.chat_de_afiliado_sql(AtendimentoConversa)


class _ListaDeAfiliadosSuspeita(RuntimeError):  # noqa: N818 — português, como `_Interrompida`
    """A página da lista de afiliados trouxe conversa já gravada como de COMPRADOR."""


async def _conferir_pagina_de_afiliados(
    session: AsyncSession, integration: Integration, conversas: list[dict]
) -> None:
    """O cinto da lista de afiliados (15/10/2026): a PÁGINA inteira, antes de
    gravar qualquer conversa dela. Levanta `_ListaDeAfiliadosSuspeita`.

    Se um dia a Shopee ignorar o `business_type=11` e devolver a lista
    padrão, a conversa do comprador nasceria "afiliado" (e a IA e o envio
    parariam nela para sempre: o upsert mescla `dados`). Conferir conversa
    por conversa só pegaria a que JÁ estava gravada — a que chegou entre as
    duas listas, ou a que ficou numa lacuna da lista dos compradores,
    passaria. A página toda pega: a lista padrão sempre traz, junto, as que
    a lista dos compradores (que roda antes) já gravou. Uma só conversa de
    comprador na página = a lista inteira é suspeita: a rodada pula o resto
    dela (vira o aviso "chat de afiliado: …" em Lojas, como uma falha) e
    nada é marcado. Medido em produção em 15/10/2026: a Shopee respeita o
    parâmetro (a lista 11 traz só afiliado).
    """
    ids = sorted({str(c.get("conversation_id") or "").strip() for c in conversas} - {""})
    if not ids:
        return
    de_comprador = await session.scalar(
        select(func.count())
        .select_from(AtendimentoConversa)
        .where(
            AtendimentoConversa.integration_id == integration.id,
            AtendimentoConversa.plataforma == PLATAFORMA,
            AtendimentoConversa.canal == CANAL_CHAT,
            AtendimentoConversa.externo_id.in_(ids),
            not_(_afiliado_sql()),
        )
    )
    if de_comprador:
        raise _ListaDeAfiliadosSuspeita(
            f"a lista de afiliados trouxe {de_comprador} conversa(s) de comprador "
            "(business_type ignorado?) — nada foi marcado"
        )


async def _gravadas(session: AsyncSession, conversa_id, externo_ids: list[str]) -> set[str]:
    """Quais destes ids da Shopee já estão gravados nesta conversa."""
    if not externo_ids:
        return set()
    linhas = await session.execute(
        select(AtendimentoMensagem.externo_id).where(
            AtendimentoMensagem.conversa_id == conversa_id,
            AtendimentoMensagem.externo_id.in_(externo_ids),
        )
    )
    return {str(x) for x in linhas.scalars().all()}


async def _mensagens_novas(
    session: AsyncSession,
    cliente: Any,
    conversa: AtendimentoConversa,
    negocio: int = NEGOCIO_COMPRADOR,
) -> list[dict]:
    """Desce pelas mensagens (mais novas primeiro) até achar uma já gravada.

    A página em que a gravada aparece é lida inteira (as já gravadas não mudam
    nada); no máximo `PAGINAS_MENSAGENS` páginas. `negocio` = de que lista
    (o chat de afiliado só responde com `business_type=11`).
    """
    coletadas: dict[str, dict] = {}
    offset: str | None = None
    for _ in range(PAGINAS_MENSAGENS):
        resp = await cliente.chat_mensagens_pagina(
            conversa.externo_id, offset=offset, page_size=PAGINA_MENSAGENS, **_negocio_kw(negocio)
        )
        pagina = [
            m
            for m in (resp.get("messages") or [])
            if isinstance(m, dict) and str(m.get("message_id") or "").strip()
        ]
        for m in pagina:
            coletadas.setdefault(str(m["message_id"]).strip(), m)
        if not pagina:
            break
        if await _gravadas(session, conversa.id, [str(m["message_id"]).strip() for m in pagina]):
            break
        proximo = str((resp.get("page_result") or {}).get("next_offset") or "").strip()
        if not proximo or proximo == "0" or proximo == offset:
            break
        offset = proximo
    return list(coletadas.values())


async def _preencher_cartao(
    session: AsyncSession,
    integration: Integration,
    cliente: Any,
    lida: _Lida,
    cota: enriquecer.Cota | None,
    comprador_id: str | None = None,
) -> list | None:
    """O cartão da mensagem de produto/pedido com o que a loja informa, ou None.

    None = a loja não respondeu (ou a cota da rodada acabou): a mensagem fica
    com o cartão que já tinha, e a próxima leitura dela tenta de novo.
    `comprador_id` (o `to_id` da conversa) vai para o índice de pedidos do
    comprador quando o pedido não diz de quem é.
    """
    if lida.tipo == TIPO_PRODUTO and lida.anuncio:
        cartao = await enriquecer.cartao_produto(
            session, integration, cliente, PLATAFORMA, lida.anuncio, cota=cota
        )
        return [cartao] if cartao else None
    if lida.tipo == TIPO_PEDIDO and lida.pedido:
        retrato = await enriquecer.retrato_pedido(
            session,
            integration,
            cliente,
            PLATAFORMA,
            lida.pedido,
            cota=cota,
            comprador_id=comprador_id,
        )
        return [enriquecer.cartao_pedido(retrato)] if retrato else None
    return None


async def _gravar_brutas(
    session: AsyncSession,
    canal: AtendimentoCanal,
    integration: Integration,
    conversa: AtendimentoConversa,
    brutas: list[dict],
    shop_id: int,
    cliente: Any = None,
    cota: enriquecer.Cota | None = None,
    *,
    afiliado: bool = False,
) -> int:
    """Mensagens cruas do `get_message` → gravadas, da mais antiga para a mais nova.

    Devolve quantas são novas. Liga a conversa ao pedido/anúncio e marca como
    `falhou` a resposta da loja que a Shopee barrou — também a que já estava
    gravada como enviada (a nossa, barrada depois de aceita). A mensagem de
    produto/pedido ganha o cartão com foto e status (`_preencher_cartao`) —
    também a já gravada cujo cartão ficou incompleto numa rodada anterior.
    `afiliado` = são do chat de afiliado (`interpretar`).
    """
    # Da mais antiga para a mais nova: a fila anda na ordem em que a conversa
    # aconteceu (e "cliente escreveu de novo" reabre na hora certa).
    brutas = sorted(
        brutas, key=lambda m: (_ns(m.get("created_timestamp")) or 0, str(m.get("message_id")))
    )
    novas = 0
    pedido: str | None = None
    anuncio: str | None = None
    # A marca vem da lista (o chat de afiliado) ou, no push, da mensagem.
    marcar_afiliado = afiliado
    barrou = False
    for bruta in brutas:
        lida = interpretar(bruta, shop_id, afiliado=afiliado)
        mensagem, criada_msg = await gravar.gravar_mensagem(
            session,
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
        if (
            afiliado
            and criada_msg
            and lida.autor == AUTOR_SISTEMA
            and conversa.comprador_id
            and str(bruta.get("from_id")) == str(conversa.comprador_id)
        ):
            # O afiliado escreveu de novo: o "fechar"/"não precisa de
            # resposta" de antes não esconde a fala nova do número da Caixa.
            gravar.reabrir_pela_fala_de_fora(conversa, mensagem)
        if (
            cliente is not None
            and lida.tipo in (TIPO_PRODUTO, TIPO_PEDIDO)
            and enriquecer.cartao_incompleto(mensagem.anexos or lida.anexos)
        ):
            cartao = await _preencher_cartao(
                session, integration, cliente, lida, cota, conversa.comprador_id
            )
            if cartao is not None:
                mensagem.anexos = cartao
        pedido = lida.pedido or pedido
        anuncio = lida.anuncio or anuncio
        marcar_afiliado = marcar_afiliado or lida.afiliado
        if lida.barrada and mensagem.autor == AUTOR_LOJA and mensagem.status != MSG_FALHOU:
            # A Shopee barrou a resposta: o comprador não recebeu. Não pode
            # contar como respondida.
            mensagem.status = MSG_FALHOU
            mensagem.erro = f"shopee {lida.barrada}"
            barrou = True

    if pedido or anuncio or marcar_afiliado:
        # O elo com o pedido (Bling, rastreio, chamados) sai das mensagens.
        await gravar.upsert_conversa(
            session,
            canal=canal,
            integration=integration,
            plataforma=PLATAFORMA,
            canal_nome=CANAL_CHAT,
            externo_id=conversa.externo_id,
            pedido_marketplace=pedido,
            anuncio_id=anuncio,
            dados={CHAVE_AFILIADO: True} if marcar_afiliado else None,
        )
    if barrou:
        await gravar.recalcular_conversa(session, conversa)
    return novas


async def _sincronizar_conversa(
    session: AsyncSession,
    canal: AtendimentoCanal,
    integration: Integration,
    cliente: Any,
    conv: dict,
    shop_id: int,
    lidas: set[str],
    cota: enriquecer.Cota | None = None,
    negocio: int = NEGOCIO_COMPRADOR,
) -> tuple[bool, int]:
    """Uma conversa da lista → (conversa criada?, mensagens novas).

    `lidas` recebe o id da conversa cujas mensagens foram lidas nesta rodada.
    Conversa com mensagem nova e pedido ligado ganha o retrato do pedido.
    `negocio` = de que lista ela veio: a de afiliado grava `dados.afiliado`
    (o `to_id` é o do afiliado — vira o `comprador_id`, o `from_id` da fala
    dele) e lê as mensagens com `business_type=11`.
    """
    externo_id = str(conv.get("conversation_id") or "").strip()
    if not externo_id:
        return False, 0
    # A página da lista de afiliados já passou pelo cinto
    # (`_conferir_pagina_de_afiliados`, em `_caminhar`).
    afiliado = negocio == NEGOCIO_AFILIADO
    to_id = conv.get("to_id")
    conversa, criada = await gravar.upsert_conversa(
        session,
        canal=canal,
        integration=integration,
        plataforma=PLATAFORMA,
        canal_nome=CANAL_CHAT,
        externo_id=externo_id,
        # O nome da LOJA (cadastro), não o da integração — como o Duoke mostra.
        conta=await lojas.nome_da_loja(session, integration) or None,
        comprador_id=str(to_id) if to_id not in (None, "", 0) else None,
        comprador_nome=_texto_ou_none(conv.get("to_name")) or None,
        nao_lidas=_int(conv.get("unread_count")),
        # A foto do comprador é a que a própria Shopee entrega. Vazia (o comum,
        # medido em 25/09) ou que não sirva para `<img>` vira None no `gravar`
        # — e None não apaga a foto que já estava gravada.
        comprador_avatar=_texto_ou_none(conv.get("to_avatar")),
        dados={CHAVE_AFILIADO: True} if afiliado else None,
    )
    # A lista já diz qual é a última mensagem: se ela está gravada, só mudou
    # o contador de não lidas — sem ida ao get_message. (A nossa resposta
    # recém-enviada, que cai aqui, é reconferida por `_reconferir_enviadas`.)
    ultima = str(conv.get("latest_message_id") or "").strip()
    if not criada and ultima and await _gravadas(session, conversa.id, [ultima]):
        return criada, 0

    brutas = await _mensagens_novas(session, cliente, conversa, negocio)
    lidas.add(externo_id)
    novas = await _gravar_brutas(
        session, canal, integration, conversa, brutas, shop_id, cliente, cota, afiliado=afiliado
    )
    # Retrato do pedido para o painel (no máximo a cada 30 min; nunca levanta).
    # O pedido que o cartão da mensagem acabou de buscar sai da memória da cota.
    await enriquecer.enriquecer_conversa(session, conversa, integration, cliente, cota=cota)
    return criada, novas


def _shop_id(cliente: Any, conv: dict | None = None) -> int:
    return _int(getattr(cliente, "shop_id", 0)) or _int((conv or {}).get("shop_id")) or 0


@dataclass
class _Passo:
    """O resultado de uma passada pela lista (do topo ou de uma lacuna)."""

    terminou: bool
    parou_em: dict | None  # onde continuar, se não terminou
    visto: int | None  # o maior horário lido na passada
    processadas: int


class _Interrompida(Exception):  # noqa: N818 — português, como `ErroCaixa`
    """A passada caiu no meio: `pagina` é o começo da página que falhou (None = o topo)."""

    def __init__(self, pagina: dict | None, visto: int | None, exc: BaseException) -> None:
        self.pagina = pagina
        self.visto = visto
        self.exc = exc
        super().__init__(str(exc))


async def _caminhar(
    session: AsyncSession,
    canal: AtendimentoCanal,
    integration: Integration,
    cliente: Any,
    *,
    inicio: dict | None,
    piso: int,
    limite: int,
    cursor: _Cursor,
    resultado: ResultadoSync,
    lidas: set[str],
    cota: enriquecer.Cota | None = None,
    negocio: int = NEGOCIO_COMPRADOR,
) -> _Passo:
    """Desce pela lista de `inicio` (None = o topo, do agora para trás) até o `piso`.

    Lê no máximo `limite` conversas. A página pedida nunca passa do que falta
    — a passada nunca para no MEIO de uma página, então o `next_cursor` da
    última é o ponto exato de retomada, sem pular e sem reler conversa.
    Levanta `_Interrompida` com o começo da página que falhou. `negocio` = a
    lista (a dos compradores ou a dos afiliados, `business_type=11`).
    """
    pagina = inicio
    visto: int | None = None
    anterior: int | None = None  # último horário (não fixada) visto, para conferir a ordem
    processadas = 0
    try:
        while processadas < limite:
            resp = await cliente.chat_conversation_list(
                # Topo: `older` SEM horário = do agora para trás. Página
                # seguinte: `older` com o horário do cursor.
                direction=DIRECAO_TOPO if pagina is None else DIRECAO_MAIS_ANTIGAS,
                tipo=TIPO_LISTA,
                page_size=min(PAGINA_CONVERSAS, limite - processadas),
                next_timestamp_nano=(pagina or {}).get("next_message_time_nano"),
                **_negocio_kw(negocio),
            )
            conversas = [c for c in (resp.get("conversations") or []) if isinstance(c, dict)]
            if negocio == NEGOCIO_AFILIADO:
                await _conferir_pagina_de_afiliados(session, integration, conversas)
            horarios = [
                ts
                for c in conversas
                if not c.get("pinned") and (ts := _ns(c.get("last_message_timestamp"))) is not None
            ]
            sequencia = ([anterior] if anterior is not None else []) + horarios
            fora_de_ordem = any(b > a for a, b in zip(sequencia, sequencia[1:], strict=False))
            if fora_de_ordem:
                # Sem a ordem decrescente, "achei uma antiga" não quer dizer
                # "acabou": a passada só termina numa página toda antiga.
                logger.warning(
                    "atendimento_shopee_lista_fora_de_ordem",
                    canal_id=str(canal.id),
                    negocio=negocio,
                    conversas=len(conversas),
                )
            if horarios:
                anterior = horarios[-1]
            alcancou = False
            antigas = 0
            for conv in conversas:
                ts = _ns(conv.get("last_message_timestamp"))
                if ts is not None and ts <= piso:
                    if conv.get("pinned"):
                        # Conversa fixada pode vir fora da ordem: não decide o fim.
                        continue
                    if not fora_de_ordem:
                        alcancou = True
                        break
                    antigas += 1
                    continue
                externo_id = str(conv.get("conversation_id") or "").strip()
                try:
                    criada, novas = await _sincronizar_conversa(
                        session,
                        canal,
                        integration,
                        cliente,
                        conv,
                        _shop_id(cliente, conv),
                        lidas,
                        cota,
                        negocio,
                    )
                except (httpx.HTTPError, RuntimeError) as exc:
                    if _sem_escopo(exc):
                        raise
                    vezes = cursor.falhas.get(externo_id, 0) + 1
                    cursor.falhas[externo_id] = vezes
                    if vezes < MAX_FALHAS_CONVERSA:
                        raise
                    cursor.falhas.pop(externo_id, None)
                    logger.error(
                        "atendimento_shopee_conversa_pulada",
                        canal_id=str(canal.id),
                        conversa_externo_id=externo_id,
                        negocio=negocio,
                        vezes=vezes,
                        erro=_erro_operacao(exc),
                    )
                else:
                    cursor.falhas.pop(externo_id, None)
                    if criada:
                        resultado.conversas_novas += 1
                    else:
                        resultado.conversas_atualizadas += 1
                    resultado.mensagens_novas += novas
                processadas += 1
                if ts is not None:
                    visto = ts if visto is None else max(visto, ts)
                await gravar.fim_do_item(session)

            info = resp.get("page_result") or {}
            proximo = info.get("next_cursor")
            nao_fixadas = [c for c in conversas if not c.get("pinned")]
            toda_antiga = fora_de_ordem and nao_fixadas and antigas == len(nao_fixadas)
            if (
                alcancou
                or toda_antiga
                or not conversas
                or not info.get("more")
                or not isinstance(proximo, dict)
                or not proximo.get("next_message_time_nano")
            ):
                return _Passo(True, None, visto, processadas)
            seguinte = {
                "next_message_time_nano": str(proximo.get("next_message_time_nano")),
                "conversation_id": str(proximo.get("conversation_id") or ""),
            }
            if seguinte == pagina:
                # A Shopee devolveu o mesmo cursor: seguir seria girar em falso.
                return _Passo(True, None, visto, processadas)
            pagina = seguinte
    except (httpx.HTTPError, RuntimeError) as exc:
        raise _Interrompida(pagina, visto, exc) from exc
    return _Passo(False, pagina, visto, processadas)


async def _reconferir_enviadas(
    session: AsyncSession,
    canal: AtendimentoCanal,
    integration: Integration,
    cliente: Any,
    lidas: set[str],
    cota: enriquecer.Cota | None = None,
) -> int:
    """Relê a 1ª página das conversas com resposta NOSSA enviada nos últimos 10 min.

    Depois do envio, o `latest_message_id` da lista é a nossa própria
    mensagem, já gravada — o atalho da leitura pula o `get_message`, e o
    status `blocked`/`censored_blacklist` que a Shopee põe depois nunca
    seria visto: a conversa ficaria "respondida" com o comprador sem nada.
    Erro aqui não derruba a rodada (a próxima tenta de novo). Devolve
    quantas mensagens novas vieram de carona.
    """
    desde = datetime.now(UTC) - RECONFERIR_ENVIO
    recentes = (
        select(AtendimentoMensagem.conversa_id)
        .join(AtendimentoConversa, AtendimentoConversa.id == AtendimentoMensagem.conversa_id)
        .where(
            AtendimentoConversa.integration_id == canal.integration_id,
            AtendimentoConversa.canal == CANAL_CHAT,
            AtendimentoMensagem.autor == AUTOR_LOJA,
            AtendimentoMensagem.origem.in_(ORIGENS_DAVINCI),
            AtendimentoMensagem.status == MSG_ENVIADA,
            func.coalesce(AtendimentoMensagem.enviada_em, AtendimentoMensagem.created_at)
            >= desde,
        )
    )
    conversas = (
        (
            await session.execute(
                select(AtendimentoConversa)
                .where(AtendimentoConversa.id.in_(recentes))
                .order_by(AtendimentoConversa.ultima_mensagem_em.desc())
                .limit(MAX_RECONFERIR)
            )
        )
        .scalars()
        .all()
    )
    novas = 0
    for conversa in conversas:
        if conversa.externo_id in lidas:
            continue  # já leu as mensagens nesta rodada
        negocio = _negocio_da_conversa(conversa)
        try:
            resp = await cliente.chat_mensagens_pagina(
                conversa.externo_id, page_size=PAGINA_MENSAGENS, **_negocio_kw(negocio)
            )
        except (httpx.HTTPError, RuntimeError) as exc:
            logger.warning(
                "atendimento_shopee_reconferir_falhou",
                canal_id=str(canal.id),
                conversa_id=str(conversa.id),
                erro=_erro_operacao(exc),
            )
            continue
        brutas = [
            m
            for m in (resp.get("messages") or [])
            if isinstance(m, dict) and str(m.get("message_id") or "").strip()
        ]
        lidas.add(conversa.externo_id)
        novas += await _gravar_brutas(
            session,
            canal,
            integration,
            conversa,
            brutas,
            _shop_id(cliente),
            cliente,
            cota,
            afiliado=negocio == NEGOCIO_AFILIADO,
        )
        await gravar.fim_do_item(session)
    return novas


async def _atualizar_nao_lidas(
    session: AsyncSession,
    canal: AtendimentoCanal,
    cliente: Any,
    total: int,
    negocio: int = NEGOCIO_COMPRADOR,
) -> None:
    """O NÃO LIDO de agora em todas as conversas do canal (14/10/2026). SÓ LEITURA.

    O `nao_lidas` de cada conversa só andava quando chegava mensagem nova: a
    equipe abria a conversa no Duoke (a Shopee zera o não lido) e o DaVinci
    continuava contando como não lida (Inova: 25 no DaVinci, 0 no Duoke). É
    metade do "Falta responder" igual ao Duoke (`pendente.py`).

    `total` = o `get_unread_conversation_count` desta rodada. Zero: ninguém
    está não lido — zera, sem chamada. Senão, a lista SÓ das não lidas
    (`type=unread`, a mesma paginação da caminhada), até
    `MAX_PAGINAS_NAO_LIDAS`: a conversa que veio fica com o número dela (no
    mínimo 1 — estar nessa lista já é estar não lida); a que não veio zera,
    se a lista acabou. Falhou: não mexe em nada (fica o valor de antes) e a
    rodada segue. Roda ANTES da caminhada: a conversa que ela relê fica com o
    número da lista dela, que é mais novo.

    Cada LISTA acerta só as conversas dela (15/10/2026, `negocio`): o total e
    a lista padrão não trazem o chat de AFILIADO, que tem os seus
    (`business_type=11`) — sem a separação, a lista dos compradores zeraria
    toda conversa de afiliado a cada rodada.
    """
    da_lista = _afiliado_sql() if negocio == NEGOCIO_AFILIADO else not_(_afiliado_sql())
    candidatas = await session.scalar(
        select(func.count())
        .select_from(AtendimentoConversa)
        .where(
            AtendimentoConversa.integration_id == canal.integration_id,
            AtendimentoConversa.canal == CANAL_CHAT,
            da_lista,
            *([AtendimentoConversa.nao_lidas > 0] if total <= 0 else []),
        )
    )
    if not candidatas:
        return  # nada gravado a acertar (loja nova, ou ninguém não lido dos dois lados)
    contagens: dict[str, int] = {}
    completa = True
    if total > 0:
        pagina: dict | None = None
        completa = False
        try:
            for _ in range(MAX_PAGINAS_NAO_LIDAS):
                resp = await cliente.chat_conversation_list(
                    direction=DIRECAO_TOPO if pagina is None else DIRECAO_MAIS_ANTIGAS,
                    tipo=TIPO_NAO_LIDAS,
                    page_size=PAGINA_CONVERSAS,
                    next_timestamp_nano=(pagina or {}).get("next_message_time_nano"),
                    **_negocio_kw(negocio),
                )
                conversas = [c for c in (resp.get("conversations") or []) if isinstance(c, dict)]
                for conv in conversas:
                    externo_id = str(conv.get("conversation_id") or "").strip()
                    if externo_id:
                        contagens[externo_id] = max(1, _int(conv.get("unread_count")) or 0)
                info = resp.get("page_result") or {}
                proximo = info.get("next_cursor")
                if (
                    not conversas
                    or not info.get("more")
                    or not isinstance(proximo, dict)
                    or not proximo.get("next_message_time_nano")
                ):
                    completa = True
                    break
                seguinte = {"next_message_time_nano": str(proximo["next_message_time_nano"])}
                if seguinte == pagina:
                    completa = True  # o mesmo cursor de novo: a lista acabou
                    break
                pagina = seguinte
        except (httpx.HTTPError, RuntimeError) as exc:
            logger.warning(
                "atendimento_shopee_lista_nao_lidas_falhou",
                canal_id=str(canal.id),
                negocio=negocio,
                erro=_erro_operacao(exc),
            )
            return
    mudaram = await gravar.aplicar_nao_lidas(
        session,
        integration_id=canal.integration_id,
        canal_nome=CANAL_CHAT,
        contagens=contagens,
        completa=completa,
        restrito_a=da_lista,
    )
    await gravar.fim_do_item(session)
    if mudaram or not completa:
        logger.info(
            "atendimento_shopee_nao_lidas",
            canal_id=str(canal.id),
            negocio=negocio,
            total=total,
            vistas=len(contagens),
            completa=completa,
            mudaram=mudaram,
        )


@dataclass
class _Leitura:
    """Uma lista lida na rodada: o cursor novo dela e, se uma passada caiu, o erro."""

    cursor: _Cursor
    processadas: int
    falha: BaseException | None = None


async def _ler_lista(
    session: AsyncSession,
    canal: AtendimentoCanal,
    integration: Integration,
    cliente: Any,
    *,
    cursor: _Cursor,
    janela: int,
    maximo: int,
    resultado: ResultadoSync,
    lidas: set[str],
    cota: enriquecer.Cota,
    negocio: int = NEGOCIO_COMPRADOR,
) -> _Leitura:
    """Uma lista de conversas na rodada: o TOPO primeiro (do agora até o
    `ultimo_ts`), depois as lacunas com o que sobrar de `maximo`.

    Não levanta: a passada que cai devolve o cursor apontando para o começo
    da página que falhou (as conversas dela que já passaram são relidas sem
    duplicar nada) e o erro em `falha`. A mesma regra para a lista dos
    compradores e a dos afiliados (15/10/2026, `negocio`), cada uma com o
    seu cursor.
    """
    piso_topo = cursor.ultimo_ts if cursor.ultimo_ts is not None else janela
    ultimo_ts = cursor.ultimo_ts
    novas_lacunas: list[_Lacuna] = []
    pendentes = list(cursor.lacunas)
    restante = maximo
    processadas = 0
    erro: BaseException | None = None
    try:
        try:
            topo = await _caminhar(
                session,
                canal,
                integration,
                cliente,
                inicio=None,
                piso=piso_topo,
                limite=restante,
                cursor=cursor,
                resultado=resultado,
                lidas=lidas,
                cota=cota,
                negocio=negocio,
            )
        except _Interrompida as falha:
            if falha.pagina is not None:
                # O que veio antes da página que falhou está lido: o resto
                # dela para baixo vira lacuna.
                novas_lacunas.append(_Lacuna(falha.pagina, piso_topo))
                ultimo_ts = _maior(piso_topo, falha.visto)
            raise
        ultimo_ts = _maior(piso_topo, topo.visto)
        if not topo.terminou and topo.parou_em is not None:
            novas_lacunas.append(_Lacuna(topo.parou_em, piso_topo))
        restante -= topo.processadas
        processadas += topo.processadas

        while pendentes and restante > 0:
            lacuna = pendentes[0]
            try:
                passo = await _caminhar(
                    session,
                    canal,
                    integration,
                    cliente,
                    inicio=lacuna.retomar,
                    piso=lacuna.piso,
                    limite=restante,
                    cursor=cursor,
                    resultado=resultado,
                    lidas=lidas,
                    cota=cota,
                    negocio=negocio,
                )
            except _Interrompida as falha:
                pendentes[0] = _Lacuna(falha.pagina or lacuna.retomar, lacuna.piso)
                raise
            pendentes.pop(0)
            if not passo.terminou and passo.parou_em is not None:
                pendentes.insert(0, _Lacuna(passo.parou_em, lacuna.piso))
            restante -= passo.processadas
            processadas += passo.processadas
            if passo.processadas == 0 and not passo.terminou:
                break  # nada andou: não gira em falso
    except _Interrompida as falha:
        # Recomeça, na próxima rodada, do começo da página que falhou.
        erro = falha.exc
    novo = _Cursor(
        ultimo_ts=ultimo_ts,
        lacunas=_juntar_lacunas(novas_lacunas + pendentes),
        falhas=cursor.falhas,
    )
    return _Leitura(cursor=novo, processadas=processadas, falha=erro)


def _cursor_do_canal(compradores: _Cursor, afiliados: Any) -> dict:
    """O `atendimento_canais.cursor` inteiro: o dos compradores no formato de
    sempre e, dentro, o da lista de afiliados (`CHAVE_CURSOR_AFILIADO`)."""
    saida = compradores.json()
    if isinstance(afiliados, dict):
        saida[CHAVE_CURSOR_AFILIADO] = dict(afiliados)
    return saida


async def _ler_afiliados(
    session: AsyncSession,
    canal: AtendimentoCanal,
    integration: Integration,
    cliente: Any,
    *,
    bruto: Any,
    janela: int,
    maximo: int,
    resultado: ResultadoSync,
    lidas: set[str],
    cota: enriquecer.Cota,
) -> _Leitura:
    """O chat de AFILIADO (15/10/2026): o não lido e a lista dele. SÓ LEITURA.

    Depois dos compradores (a mensagem do comprador não espera), com teto
    próprio (`MAX_CONVERSAS_AFILIADO`) e o cursor dele (`bruto`, o
    `cursor["afiliado"]` do canal; vazio = a primeira leitura, os últimos 7
    dias). Nunca levanta: o total que falha fica no log (o não lido do
    afiliado fica como estava); a lista que falha vira o aviso da rodada
    (`resultado.erro`, com o canal `ok` — visível em Lojas) e o cursor dela
    aponta para a página que falhou. Devolve a leitura da lista (o cursor
    novo dela).
    """
    try:
        total = await cliente.chat_unread_count(**_negocio_kw(NEGOCIO_AFILIADO))
    except (httpx.HTTPError, RuntimeError) as exc:
        logger.warning(
            "atendimento_shopee_afiliados_nao_lidas_falhou",
            canal_id=str(canal.id),
            erro=_erro_operacao(exc),
        )
    else:
        if resultado.nao_lidas is not None:
            # O termômetro da loja: a Shopee conta as duas listas à parte.
            resultado.nao_lidas += total
        await _atualizar_nao_lidas(session, canal, cliente, total, NEGOCIO_AFILIADO)

    leitura = await _ler_lista(
        session,
        canal,
        integration,
        cliente,
        cursor=_Cursor.ler(bruto, piso_inicial=janela),
        janela=janela,
        maximo=min(maximo, MAX_CONVERSAS_AFILIADO),
        resultado=resultado,
        lidas=lidas,
        cota=cota,
        negocio=NEGOCIO_AFILIADO,
    )
    if leitura.falha is not None:
        aviso = f"chat de afiliado: {_erro_operacao(leitura.falha)}"
        resultado.erro = f"{resultado.erro} · {aviso}" if resultado.erro else aviso
        logger.warning(
            "atendimento_shopee_afiliados_falhou",
            canal_id=str(canal.id),
            erro=_erro_operacao(leitura.falha),
            conversas=leitura.processadas,
        )
    return leitura


async def sincronizar(
    session: AsyncSession,
    canal: AtendimentoCanal,
    integration: Integration,
    cliente: Any,
) -> ResultadoSync:
    """Lê o que mudou desde `canal.cursor`, grava e anda o cursor.

    Commita a cada conversa (`gravar.fim_do_item`: a trava da conversa não
    dura a rodada inteira); o canal (cursor, não lidas) o sync grava no fim.

    Erro da Shopee ou de rede não levanta: volta como `ResultadoSync` com
    status `erro` (ou `sem_escopo`, quando é permissão do app), e o cursor
    fica apontando para o começo da página que falhou — nada é pulado. O
    chat de afiliado (15/10/2026) é lido depois, só com a lista dos
    compradores em dia, e a falha dele não derruba a rodada (`_ler_afiliados`).
    """
    resultado = ResultadoSync(status=STATUS_OK)

    # 1) Termômetro da caixa — e a sonda de permissão: sem o módulo Chat,
    #    volta `sem_escopo` sem gravar nada.
    try:
        nao_lidas = await cliente.chat_unread_count()
    except (httpx.HTTPError, RuntimeError) as exc:
        if _sem_escopo(exc):
            logger.warning(
                "atendimento_shopee_sem_escopo",
                canal_id=str(canal.id),
                erro=_erro_operacao(exc),
            )
            return ResultadoSync(status=STATUS_SEM_ESCOPO, erro=_erro_operacao(exc))
        logger.warning(
            "atendimento_shopee_nao_lidas_falhou",
            canal_id=str(canal.id),
            erro=_erro_operacao(exc),
        )
    else:
        # O canal só é gravado no fim da rodada (`sync._registrar`): mexer nele
        # aqui o deixaria travado durante todas as chamadas à Shopee, e a troca
        # de modo pela tela esperaria.
        resultado.nao_lidas = nao_lidas
        # 1b) O não lido de agora em cada conversa (o "Falta responder" igual
        #     ao Duoke). Sem o total (a chamada acima falhou), nada muda.
        await _atualizar_nao_lidas(session, canal, cliente, nao_lidas)

    # 2) Os COMPRADORES: o TOPO primeiro (do agora até o `ultimo_ts`), depois
    #    as lacunas com o que sobrar do teto de conversas por rodada.
    maximo = max(1, int(get_settings().atendimento_sync_max_conversas or 1))
    janela = _ns_de(datetime.now(UTC) - JANELA_PRIMEIRA_LEITURA)
    cursor = _Cursor.ler(canal.cursor, piso_inicial=janela)
    bruto_afiliados = (
        canal.cursor.get(CHAVE_CURSOR_AFILIADO) if isinstance(canal.cursor, dict) else None
    )
    lidas: set[str] = set()
    # Teto de idas à loja para cartões e retratos de pedido nesta rodada.
    cota = enriquecer.Cota()
    leitura = await _ler_lista(
        session,
        canal,
        integration,
        cliente,
        cursor=cursor,
        janela=janela,
        maximo=maximo,
        resultado=resultado,
        lidas=lidas,
        cota=cota,
    )
    if leitura.falha is not None:
        caiu = leitura.falha
        canal.cursor = _cursor_do_canal(leitura.cursor, bruto_afiliados)
        resultado.status = STATUS_SEM_ESCOPO if _sem_escopo(caiu) else STATUS_ERRO
        resultado.erro = _erro_operacao(caiu)
        logger.warning(
            "atendimento_shopee_sync_falhou",
            canal_id=str(canal.id),
            status=resultado.status,
            erro=resultado.erro,
            conversas=leitura.processadas,
            mensagens=resultado.mensagens_novas,
        )
        return resultado

    # 2b) O chat de AFILIADO (15/10/2026): a lista à parte que a padrão não traz.
    #     O cursor do canal só é escrito DEPOIS dela, numa atribuição só: o
    #     `canal` sujo antes iria no autoflush do primeiro SELECT da lista de
    #     afiliados e a linha de `atendimento_canais` ficaria travada durante
    #     as chamadas à Shopee (medido na revisão de 15/10: `FOR UPDATE
    #     NOWAIT` de outra sessão caía em `LockNotAvailable`).
    afiliados = await _ler_afiliados(
        session,
        canal,
        integration,
        cliente,
        bruto=bruto_afiliados,
        janela=janela,
        maximo=maximo,
        resultado=resultado,
        lidas=lidas,
        cota=cota,
    )
    canal.cursor = _cursor_do_canal(leitura.cursor, afiliados.cursor.json())

    # 3) A nossa resposta recente que a Shopee pode ter barrado depois.
    resultado.mensagens_novas += await _reconferir_enviadas(
        session, canal, integration, cliente, lidas, cota
    )
    # 4) O que sobrou da cota: o painel das conversas que ficaram sem retrato.
    enriquecidas = await enriquecer.enriquecer_canal(session, canal, integration, cliente, cota)
    logger.info(
        "atendimento_shopee_sync",
        canal_id=str(canal.id),
        conversas=leitura.processadas,
        afiliados=afiliados.processadas,
        conversas_novas=resultado.conversas_novas,
        mensagens_novas=resultado.mensagens_novas,
        lacunas=len(leitura.cursor.lacunas),
        enriquecidas=enriquecidas,
    )
    return resultado


def _maior(base: int, visto: int | None) -> int:
    return base if visto is None else max(base, visto)


# ── Envio ─────────────────────────────────────────────────────────────────


async def _envio(
    chamada: Callable[[], Awaitable[Any]], *, conversa_id: Any = None
) -> ResultadoEnvio:
    """Faz UMA chamada de envio à Shopee e devolve o resultado. Nunca levanta.

    A régua do resultado é a de sempre: recusa com código = não saiu;
    timeout/erro sem código = ambíguo (`revisar`, nunca se retenta).
    """
    try:
        resp = await chamada()
    except httpx.HTTPStatusError as exc:
        # No caminho do envio só a renovação do token faz `raise_for_status`:
        # falhou ANTES de a mensagem sair.
        return ResultadoEnvio(ok=False, erro=f"shopee token_http_{exc.response.status_code}")
    except httpx.HTTPError as exc:
        # Timeout / conexão caída: pode ter saído. Vira `revisar`, nunca retenta.
        return ResultadoEnvio(ok=False, ambiguo=True, erro=_erro_operacao(exc))
    except ValueError:
        # `to_id` não numérico, ou a renovação do token devolveu lixo: tudo
        # isso acontece ANTES do envio — nada saiu.
        return ResultadoEnvio(ok=False, erro="shopee envio_invalido")
    except RuntimeError as exc:
        codigo = _codigo_shopee(exc)
        if codigo and not any(p in codigo for p in _PISTAS_AMBIGUAS):
            # A Shopee recusou com código: não saiu. Só o código — a mensagem
            # da Shopee pode repetir pedaço do texto.
            return ResultadoEnvio(ok=False, erro=f"shopee {codigo}")
        if "refresh_token" in str(exc):
            return ResultadoEnvio(ok=False, erro="shopee sem_refresh_token")
        return ResultadoEnvio(ok=False, ambiguo=True, erro=_erro_operacao(exc))
    except Exception as exc:  # noqa: BLE001 — envio nunca levanta; sem saber, é ambíguo
        logger.warning(
            "atendimento_shopee_envio_inesperado",
            conversa_id=str(conversa_id) if conversa_id else None,
            erro=type(exc).__name__,
        )
        return ResultadoEnvio(ok=False, ambiguo=True, erro=f"shopee {type(exc).__name__}")

    resp = resp if isinstance(resp, dict) else {}
    message_id = str(resp.get("message_id") or "").strip()
    # Sem `message_id` na resposta a mensagem saiu do mesmo jeito: a próxima
    # leitura a traz de volta e `gravar` a adota (pelo texto, ou — cartão e
    # figurinha da automação — pelo pedido/figurinha da marca).
    return ResultadoEnvio(ok=True, externo_id=message_id or None, payload=resp)


async def enviar_texto(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    integration: Integration | None,
    cliente: Any,
    texto: str,
) -> ResultadoEnvio:
    """Envia `texto` na conversa. Timeout/erro sem código = ambiguo. Nunca levanta.

    A Shopee não responde "na conversa": manda para o comprador (`to_id`),
    que é o `comprador_id` gravado pela leitura.
    """
    to_id = str(conversa.comprador_id or "").strip()
    if not to_id:
        return ResultadoEnvio(ok=False, erro="shopee sem_comprador")
    if not (texto or "").strip():
        return ResultadoEnvio(ok=False, erro="shopee texto_vazio")
    return await _envio(
        lambda: cliente.chat_send_message(to_id, text=texto), conversa_id=conversa.id
    )


# ── Partes da mensagem automática (05/10/2026) ────────────────────────────
# O motor de automações (`automacoes.py`, pelo `enviar.enviar_automatica`)
# manda cada PARTE por aqui: o texto, o CARTÃO do pedido e a FIGURINHA, no
# formato que o Duoke manda e a leitura grava (payloads de produção, 7 dias:
# cartão `{"message_type": "order", "content": {"order_sn"}}`, figurinha
# `{"message_type": "sticker", "content": {"sticker_id", "sticker_package_id"}}`,
# os dois `normal`; os textos das campanhas `auto_reply`). E manda SEM
# conversa no DaVinci: o `to_id` é o comprador do pedido (o pedido recebido
# de quem nunca escreveu à loja) — a Shopee abre a conversa e devolve o
# `conversation_id`, que o `enviar` grava.

PARTE_TEXTO = "texto"
PARTE_CARTAO = "cartao_pedido"
PARTE_FIGURINHA = "figurinha"


async def enviar_parte(
    session: AsyncSession,
    conversa: AtendimentoConversa | None,
    integration: Integration | None,
    cliente: Any,
    parte: dict,
    *,
    to_id: str | None = None,
    pedido: str | None = None,
    auto_reply: bool = False,
) -> ResultadoEnvio:
    """Envia UMA parte da mensagem automática. Nunca levanta.

    `to_id` vale quando não há conversa (senão é o comprador da conversa);
    `pedido` é o número do cartão; `auto_reply` manda o TEXTO como resposta
    automática (`send_autoreply_message`) — só as campanhas, só com a chave
    `atendimento_automacoes_shopee_auto_reply` (quem decide é o `enviar`).
    O cartão e a figurinha vão como mensagem normal, como o Duoke manda.
    """
    destino = str(to_id or (conversa.comprador_id if conversa is not None else "") or "").strip()
    if not destino:
        return ResultadoEnvio(ok=False, erro="shopee sem_comprador")
    tipo = parte.get("tipo") if isinstance(parte, dict) else None
    conversa_id = conversa.id if conversa is not None else None
    if tipo == PARTE_TEXTO:
        texto = str(parte.get("texto") or "")
        if not texto.strip():
            return ResultadoEnvio(ok=False, erro="shopee texto_vazio")
        if auto_reply:
            return await _envio(
                lambda: cliente.chat_send_autoreply_message(destino, text=texto),
                conversa_id=conversa_id,
            )
        return await _envio(
            lambda: cliente.chat_send_message(destino, text=texto), conversa_id=conversa_id
        )
    if tipo == PARTE_CARTAO:
        numero = str(pedido or "").strip()
        if not numero:
            return ResultadoEnvio(ok=False, erro="shopee cartao_sem_pedido")
        return await _envio(
            lambda: cliente.chat_send_message(destino, order_sn=numero), conversa_id=conversa_id
        )
    if tipo == PARTE_FIGURINHA:
        figurinha = str(parte.get("figurinha") or "").strip()
        pacote = str(parte.get("pacote") or "").strip()
        if not figurinha or not pacote:
            return ResultadoEnvio(ok=False, erro="shopee figurinha_invalida")
        return await _envio(
            lambda: cliente.chat_send_message(
                destino, sticker_id=figurinha, sticker_package_id=pacote
            ),
            conversa_id=conversa_id,
        )
    return ResultadoEnvio(ok=False, erro="shopee parte_desconhecida")


# ── Resposta à avaliação (RF8, 02/10/2026) ────────────────────────────────


async def responder_avaliacao(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    integration: Integration | None,
    cliente: Any,
    texto: str,
) -> ResultadoEnvio:
    """Responde a avaliação da conversa `avaliacao` (`reply_comment`). Nunca levanta.

    A resposta é PÚBLICA (aparece no anúncio). Quem chama é o `enviar`, com
    as mesmas travas do chat (envio ligado, loja fora de `observar`, uma em
    voo, validador). O `externo_id` devolvido é o mesmo que a leitura das
    avaliações grava para a resposta da loja (`resposta:<comment_id>`): a
    resposta que saiu daqui não volta duplicada. Timeout/erro sem código =
    ambíguo (`revisar`, nunca se retenta).
    """
    comentario = str(conversa.externo_id or "").strip()
    if not comentario.isdigit():
        return ResultadoEnvio(ok=False, erro="shopee avaliacao_sem_id")
    if not (texto or "").strip():
        return ResultadoEnvio(ok=False, erro="shopee texto_vazio")
    try:
        resp = await cliente.reply_comment(comentario, texto)
    except httpx.HTTPStatusError as exc:
        # Só a renovação do token faz `raise_for_status` neste caminho: falhou
        # ANTES de a resposta sair.
        return ResultadoEnvio(ok=False, erro=f"shopee token_http_{exc.response.status_code}")
    except httpx.HTTPError as exc:
        return ResultadoEnvio(ok=False, ambiguo=True, erro=_erro_operacao(exc))
    except ValueError:
        return ResultadoEnvio(ok=False, erro="shopee envio_invalido")
    except RuntimeError as exc:
        codigo = _codigo_shopee(exc)
        if codigo and not any(p in codigo for p in _PISTAS_AMBIGUAS):
            return ResultadoEnvio(ok=False, erro=f"shopee {codigo}")
        if "refresh_token" in str(exc):
            return ResultadoEnvio(ok=False, erro="shopee sem_refresh_token")
        return ResultadoEnvio(ok=False, ambiguo=True, erro=_erro_operacao(exc))
    except Exception as exc:  # noqa: BLE001 — envio nunca levanta; sem saber, é ambíguo
        logger.warning(
            "atendimento_shopee_avaliacao_inesperado",
            conversa_id=str(conversa.id),
            erro=type(exc).__name__,
        )
        return ResultadoEnvio(ok=False, ambiguo=True, erro=f"shopee {type(exc).__name__}")

    resp = resp if isinstance(resp, dict) else {}
    resultado = next(
        (
            r
            for r in resp.get("result_list") or []
            if isinstance(r, dict) and str(r.get("comment_id") or "").strip() == comentario
        ),
        None,
    )
    avisos = resp.get("warning")
    payload = {
        "comment_id": comentario,
        "avisos": len(avisos) if isinstance(avisos, list) else 0,
    }
    if resultado is None:
        # Sem erro e sem o resultado da avaliação: pode ter saído. A leitura
        # seguinte (a resposta aparece no `get_comment`) tira a dúvida.
        return ResultadoEnvio(
            ok=False, ambiguo=True, erro="shopee resposta_sem_resultado", payload=payload
        )
    falha = str(resultado.get("fail_error") or "").strip()
    if falha:
        # Só o código: a mensagem da Shopee pode repetir pedaço do texto.
        return ResultadoEnvio(ok=False, erro=f"shopee {falha[:80]}", payload=payload)
    return ResultadoEnvio(ok=True, externo_id=f"resposta:{comentario}", payload=payload)
