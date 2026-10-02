"""Adaptador da Shopee (chat do vendedor) para a caixa de atendimento.

Leitura por CONSULTA periódica (o push da Shopee fica para depois): a lista de
conversas vem da mais recente para a mais antiga, e cada rodada desce por ela
até encontrar o ponto em que a rodada anterior parou. Formato das respostas
medido em produção em 25/09/2026 e o sentido da lista em 28/09 (só leitura).

NUNCA marcar como lido. O cliente não tem `read_conversation` de propósito:
enquanto o Duoke estiver ligado, o "não lido" da Shopee é o aviso da equipe.

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
Resposta automática, chatbot, notificação e chat de afiliado entram como
`sistema` — ficam na conversa, mas não contam como pergunta nem como resposta
(a Shopee não conta resposta automática na taxa de resposta do chat).

Cartões e painel (28/09/2026, "a cara do Duoke"): a mensagem de produto e a
de pedido viram cartões com foto, título, preço e status, e a conversa com
pedido ligado ganha o retrato do pedido (`dados["pedido_mkt"]`) — tudo por
`enriquecer`, SÓ LEITURA, com uma cota por rodada. A foto do comprador é a
`to_avatar` da própria lista de conversas.

Texto de comprador nunca vai para o log — só ids e contagens.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AtendimentoCanal, AtendimentoConversa, AtendimentoMensagem, Integration
from app.services.atendimento import enriquecer, gravar, lojas
from app.services.atendimento.constantes import (
    AUTOR_CLIENTE,
    AUTOR_LOJA,
    AUTOR_SISTEMA,
    CANAL_CHAT,
    MSG_ENVIADA,
    MSG_FALHOU,
    ORIGENS_DAVINCI,
    ResultadoEnvio,
    ResultadoSync,
    rotulo_tipo_plataforma,
)

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
# `business_type` 11 = chat de afiliado, não é comprador (push de 05/11/2024).
_NEGOCIO_AFILIADO = 11

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
        afiliado
        or tipo_shopee in _TIPOS_SISTEMA
        or "notification" in tipo_shopee
        or status in _STATUS_AUTOMATICOS
    ):
        return AUTOR_SISTEMA
    if shop_id and _int(msg.get("from_shop_id")) == shop_id:
        return AUTOR_LOJA
    return AUTOR_CLIENTE


def interpretar(msg: dict, shop_id: int) -> _Lida:
    """Mensagem crua do `get_message` → autor, tipo, texto, anexos e o que liga ao pedido."""
    tipo_shopee = str(msg.get("message_type") or "").strip().lower()
    status = str(msg.get("status") or "").strip().lower()
    conteudo = msg.get("content") if isinstance(msg.get("content"), dict) else {}
    origem = msg.get("source_content") if isinstance(msg.get("source_content"), dict) else {}
    afiliado = _int(msg.get("business_type")) == _NEGOCIO_AFILIADO

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
    session: AsyncSession, cliente: Any, conversa: AtendimentoConversa
) -> list[dict]:
    """Desce pelas mensagens (mais novas primeiro) até achar uma já gravada.

    A página em que a gravada aparece é lida inteira (as já gravadas não mudam
    nada); no máximo `PAGINAS_MENSAGENS` páginas.
    """
    coletadas: dict[str, dict] = {}
    offset: str | None = None
    for _ in range(PAGINAS_MENSAGENS):
        resp = await cliente.chat_mensagens_pagina(
            conversa.externo_id, offset=offset, page_size=PAGINA_MENSAGENS
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
) -> int:
    """Mensagens cruas do `get_message` → gravadas, da mais antiga para a mais nova.

    Devolve quantas são novas. Liga a conversa ao pedido/anúncio e marca como
    `falhou` a resposta da loja que a Shopee barrou — também a que já estava
    gravada como enviada (a nossa, barrada depois de aceita). A mensagem de
    produto/pedido ganha o cartão com foto e status (`_preencher_cartao`) —
    também a já gravada cujo cartão ficou incompleto numa rodada anterior.
    """
    # Da mais antiga para a mais nova: a fila anda na ordem em que a conversa
    # aconteceu (e "cliente escreveu de novo" reabre na hora certa).
    brutas = sorted(
        brutas, key=lambda m: (_ns(m.get("created_timestamp")) or 0, str(m.get("message_id")))
    )
    novas = 0
    pedido: str | None = None
    anuncio: str | None = None
    afiliado = False
    barrou = False
    for bruta in brutas:
        lida = interpretar(bruta, shop_id)
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
        afiliado = afiliado or lida.afiliado
        if lida.barrada and mensagem.autor == AUTOR_LOJA and mensagem.status != MSG_FALHOU:
            # A Shopee barrou a resposta: o comprador não recebeu. Não pode
            # contar como respondida.
            mensagem.status = MSG_FALHOU
            mensagem.erro = f"shopee {lida.barrada}"
            barrou = True

    if pedido or anuncio or afiliado:
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
            dados={"afiliado": True} if afiliado else None,
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
) -> tuple[bool, int]:
    """Uma conversa da lista → (conversa criada?, mensagens novas).

    `lidas` recebe o id da conversa cujas mensagens foram lidas nesta rodada.
    Conversa com mensagem nova e pedido ligado ganha o retrato do pedido.
    """
    externo_id = str(conv.get("conversation_id") or "").strip()
    if not externo_id:
        return False, 0
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
    )
    # A lista já diz qual é a última mensagem: se ela está gravada, só mudou
    # o contador de não lidas — sem ida ao get_message. (A nossa resposta
    # recém-enviada, que cai aqui, é reconferida por `_reconferir_enviadas`.)
    ultima = str(conv.get("latest_message_id") or "").strip()
    if not criada and ultima and await _gravadas(session, conversa.id, [ultima]):
        return criada, 0

    brutas = await _mensagens_novas(session, cliente, conversa)
    lidas.add(externo_id)
    novas = await _gravar_brutas(
        session, canal, integration, conversa, brutas, shop_id, cliente, cota
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
) -> _Passo:
    """Desce pela lista de `inicio` (None = o topo, do agora para trás) até o `piso`.

    Lê no máximo `limite` conversas. A página pedida nunca passa do que falta
    — a passada nunca para no MEIO de uma página, então o `next_cursor` da
    última é o ponto exato de retomada, sem pular e sem reler conversa.
    Levanta `_Interrompida` com o começo da página que falhou.
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
            )
            conversas = [c for c in (resp.get("conversations") or []) if isinstance(c, dict)]
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
        try:
            resp = await cliente.chat_mensagens_pagina(
                conversa.externo_id, page_size=PAGINA_MENSAGENS
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
            session, canal, integration, conversa, brutas, _shop_id(cliente), cliente, cota
        )
        await gravar.fim_do_item(session)
    return novas


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
    fica apontando para o começo da página que falhou — nada é pulado.
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

    # 2) O TOPO primeiro (do agora até o `ultimo_ts`), depois as lacunas com o
    #    que sobrar do teto de conversas por rodada.
    maximo = max(1, int(get_settings().atendimento_sync_max_conversas or 1))
    janela = _ns_de(datetime.now(UTC) - JANELA_PRIMEIRA_LEITURA)
    cursor = _Cursor.ler(canal.cursor, piso_inicial=janela)
    piso_topo = cursor.ultimo_ts if cursor.ultimo_ts is not None else janela
    ultimo_ts = cursor.ultimo_ts
    novas_lacunas: list[_Lacuna] = []
    pendentes = list(cursor.lacunas)
    lidas: set[str] = set()
    restante = maximo
    processadas = 0
    # Teto de idas à loja para cartões e retratos de pedido nesta rodada.
    cota = enriquecer.Cota()
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
        # Recomeça, na próxima rodada, do começo da página que falhou: as
        # conversas dela que já passaram são relidas sem duplicar nada.
        exc = falha.exc
        canal.cursor = _Cursor(
            ultimo_ts=ultimo_ts,
            lacunas=_juntar_lacunas(novas_lacunas + pendentes),
            falhas=cursor.falhas,
        ).json()
        resultado.status = STATUS_SEM_ESCOPO if _sem_escopo(exc) else STATUS_ERRO
        resultado.erro = _erro_operacao(exc)
        logger.warning(
            "atendimento_shopee_sync_falhou",
            canal_id=str(canal.id),
            status=resultado.status,
            erro=resultado.erro,
            conversas=processadas,
            mensagens=resultado.mensagens_novas,
        )
        return resultado

    lacunas = _juntar_lacunas(novas_lacunas + pendentes)
    canal.cursor = _Cursor(ultimo_ts=ultimo_ts, lacunas=lacunas, falhas=cursor.falhas).json()

    # 3) A nossa resposta recente que a Shopee pode ter barrado depois.
    resultado.mensagens_novas += await _reconferir_enviadas(
        session, canal, integration, cliente, lidas, cota
    )
    # 4) O que sobrou da cota: o painel das conversas que ficaram sem retrato.
    enriquecidas = await enriquecer.enriquecer_canal(session, canal, integration, cliente, cota)
    logger.info(
        "atendimento_shopee_sync",
        canal_id=str(canal.id),
        conversas=processadas,
        conversas_novas=resultado.conversas_novas,
        mensagens_novas=resultado.mensagens_novas,
        lacunas=len(lacunas),
        enriquecidas=enriquecidas,
    )
    return resultado


def _maior(base: int, visto: int | None) -> int:
    return base if visto is None else max(base, visto)


# ── Envio ─────────────────────────────────────────────────────────────────


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
    try:
        resp = await cliente.chat_send_message(to_id, text=texto)
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
            conversa_id=str(conversa.id),
            erro=type(exc).__name__,
        )
        return ResultadoEnvio(ok=False, ambiguo=True, erro=f"shopee {type(exc).__name__}")

    resp = resp if isinstance(resp, dict) else {}
    message_id = str(resp.get("message_id") or "").strip()
    # Sem `message_id` na resposta a mensagem saiu do mesmo jeito: a próxima
    # leitura a traz de volta e `gravar` a adota pelo texto.
    return ResultadoEnvio(ok=True, externo_id=message_id or None, payload=resp)


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
