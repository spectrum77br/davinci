"""Adaptador da TikTok Shop (Customer Service API 202309) para a caixa de atendimento.

Hoje TODAS as lojas respondem `401 · 105005 Access denied`: o app do
DaVinci não tem o escopo `seller.customer_service` (pedido à TikTok depende
de vídeo da tela funcionando). Então o adaptador existe para o dia da
aprovação e, até lá, devolve `sem_escopo` SEM gravar nada e sem levantar —
o canal fica marcado e a rodada seguinte não vira alarme.

O formato é o da documentação oficial (nunca medido, sem escopo não dá);
por isso a leitura é DEFENSIVA: campo que falta vira None, `content` que não
é JSON vira mensagem `outro`, e nada disso derruba a rodada.

Cursor do canal (mesma ideia da Shopee — ver `atendimento/shopee.py`):

  ultimo_ts — o maior horário (s) de última mensagem já visto: toda conversa
              acima dele é novidade;
  lacunas   — trechos ainda por ler, a mais recente primeiro: `{retomar,
              piso}` = continuar do `page_token` até o horário `piso`. Nasce
              quando uma passada para no teto de conversas
              (`atendimento_sync_max_conversas`) antes do fim.

Toda rodada lê primeiro o TOPO (até o `ultimo_ts`) e só com o que sobrar do
teto continua as lacunas: a mensagem nova não espera a caminhada antiga.

A doc não garante a ORDEM da lista de conversas. Para não depender dela, a
passada só termina numa página em que TODAS as conversas são antigas (e a
rodada nunca passa de `MAX_PAGINAS_POR_RODADA` páginas).

Nunca marca como lido (o `Read Message` não é chamado). Texto de comprador
nunca vai para o log — só ids e contagens.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AtendimentoCanal, AtendimentoConversa, AtendimentoMensagem, Integration
from app.services.atendimento import enriquecer, gravar, lojas
from app.services.atendimento.constantes import (
    AUTOR_CLIENTE,
    AUTOR_LOJA,
    AUTOR_SISTEMA,
    CANAL_CHAT,
    CONVERSA_ABERTA,
    CONVERSA_BLOQUEADA,
    CONVERSA_FECHADA,
    ResultadoEnvio,
    ResultadoSync,
    rotulo_tipo_plataforma,
)

logger = structlog.get_logger()

# Um dos valores de `constantes.PLATAFORMAS`.
PLATAFORMA = "tiktok"

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

# ── Paginação (máximos da doc: 20 conversas, 10 mensagens por página) ─────
PAGINA_CONVERSAS = 20
PAGINA_MENSAGENS = 10
PAGINAS_MENSAGENS = 3
MAX_PAGINAS_POR_RODADA = 10
# Lacunas guardadas no cursor: acima disso, as duas mais antigas viram uma.
MAX_LACUNAS = 5
JANELA_PRIMEIRA_LEITURA = timedelta(days=7)
MAX_FALHAS_CONVERSA = 3

# ── Códigos da TikTok ─────────────────────────────────────────────────────
# Sem escopo: HTTP 401/403 (o `_get` devolve o status como `code`) ou o
# 105005 no corpo. Medido em 25/09 nas 8 lojas: 401 com 105005.
_CODIGOS_SEM_ESCOPO = frozenset({401, 403, 105005})
# "Não dá para mandar por regra da conversa" (janela fechada): a conversa
# vira `bloqueada` até a lista voltar a dizer `can_send_message=true`.
_CODIGO_JANELA_FECHADA = 45109001
# Erro interno da TikTok: a mensagem pode ter saído.
_CODIGOS_AMBIGUOS = frozenset({45101001, 36009003})
MOTIVO_JANELA = "A TikTok não deixa responder esta conversa agora (janela de atendimento)."

# ── Vocabulário das mensagens ─────────────────────────────────────────────
_PAPEL_COMPRADOR = "BUYER"
_PAPEIS_LOJA = frozenset({"SHOP", "CUSTOMER_SERVICE"})
# SYSTEM e ROBOT (chatbot) ficam como `sistema`: o que a TikTok atende
# sozinha não conta como resposta da loja.
# Marcadores de contexto e avisos — não são fala de ninguém.
_TIPOS_SISTEMA = frozenset(
    {
        "NOTIFICATION",
        "ALLOCATED_SERVICE",
        "BUYER_ENTER_FROM_TRANSFER",
        "BUYER_ENTER_FROM_PRODUCT",
        "BUYER_ENTER_FROM_ORDER",
    }
)
_TIPOS_PRODUTO = frozenset({"PRODUCT_CARD", "BUYER_ENTER_FROM_PRODUCT"})
_TIPOS_PEDIDO = frozenset(
    {"ORDER_CARD", "BUYER_ENTER_FROM_ORDER", "LOGISTICS_CARD", "RETURN_REFUND_CARD"}
)


class _RecusaError(Exception):
    """Resposta da TikTok com `code` diferente de 0 no meio da leitura."""

    def __init__(self, resp: dict) -> None:
        self.codigo = _codigo(resp)
        self.sem_escopo = _eh_sem_escopo(resp)
        super().__init__(f"tiktok code={self.codigo}")


# ── Utilitários ───────────────────────────────────────────────────────────


def _int(valor: Any) -> int | None:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _codigo(resp: Any) -> int | None:
    return _int(resp.get("code")) if isinstance(resp, dict) else None


def _eh_sem_escopo(resp: Any) -> bool:
    if not isinstance(resp, dict):
        return False
    mensagem = str(resp.get("message") or "")
    return (
        _codigo(resp) in _CODIGOS_SEM_ESCOPO
        or "105005" in mensagem
        or "access denied" in mensagem.lower()
    )


def _conferir(resp: Any) -> dict:
    """O `data` de uma resposta OK; qualquer outra coisa vira `_RecusaError`."""
    if not isinstance(resp, dict) or _codigo(resp) != 0:
        raise _RecusaError(resp if isinstance(resp, dict) else {})
    data = resp.get("data")
    return data if isinstance(data, dict) else {}


def _segundos(valor: Any) -> int | None:
    """Horário da TikTok em segundos (a doc diz segundos; ms por via das dúvidas)."""
    n = _int(valor)
    if n is None or n <= 0:
        return None
    return n // 1000 if n >= 10**11 else n


def para_datetime(valor: Any) -> datetime | None:
    s = _segundos(valor)
    return None if s is None else datetime.fromtimestamp(s, tz=UTC)


def _conteudo(bruto: Any) -> dict:
    """`content` vem como JSON EM TEXTO. Não sendo JSON de objeto, vazio."""
    if isinstance(bruto, dict):
        return bruto
    if not isinstance(bruto, str) or not bruto.strip():
        return {}
    try:
        valor = json.loads(bruto)
    except ValueError:
        return {}
    return valor if isinstance(valor, dict) else {}


def _texto(valor: Any) -> str | None:
    return valor if isinstance(valor, str) else None


def _erro_operacao(exc: BaseException) -> str:
    if isinstance(exc, _RecusaError):
        return f"tiktok code={exc.codigo}"
    return f"tiktok {type(exc).__name__}"


# ── Cursor ────────────────────────────────────────────────────────────────


@dataclass
class _Lacuna:
    """Trecho da lista ainda por ler: do `page_token` (retomar) para baixo até o `piso` (s)."""

    retomar: str
    piso: int

    def json(self) -> dict:
        return {"retomar": self.retomar, "piso": self.piso}


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
            retomar = str(item.get("retomar") or "").strip()
            piso = _int(item.get("piso"))
            if retomar and piso is not None:
                lacunas.append(_Lacuna(retomar, piso))
        # Formato anterior (uma caminhada só: `retomar` + `teto`).
        antigo = str(d.get("retomar") or "").strip()
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
    externo_id: str
    autor: str
    tipo: str
    texto: str | None
    anexos: list
    enviada_em: datetime | None
    pedido: str | None = None
    anuncio: str | None = None


def interpretar(msg: dict) -> _Lida | None:
    """Mensagem crua da TikTok → vocabulário do atendimento. None = não mostrar.

    `is_visible=false` é o que a doc manda esconder (o pedido de avaliação
    que o sistema manda ao comprador no fim da conversa).
    """
    externo_id = str(msg.get("id") or "").strip()
    if not externo_id or msg.get("is_visible") is False:
        return None
    tipo_tt = str(msg.get("type") or "").strip().upper()
    remetente = msg.get("sender") if isinstance(msg.get("sender"), dict) else {}
    papel = str(remetente.get("role") or "").strip().upper()
    conteudo = _conteudo(msg.get("content"))

    if tipo_tt in _TIPOS_SISTEMA:
        autor = AUTOR_SISTEMA
    elif papel == _PAPEL_COMPRADOR:
        autor = AUTOR_CLIENTE
    elif papel in _PAPEIS_LOJA:
        autor = AUTOR_LOJA
    else:
        autor = AUTOR_SISTEMA

    texto: str | None = None
    anexos: list = []
    pedido: str | None = None
    anuncio: str | None = None
    if tipo_tt == "TEXT":
        tipo, texto = TIPO_TEXTO, _texto(conteudo.get("content"))
    elif tipo_tt == "IMAGE":
        tipo = TIPO_IMAGEM
        if conteudo.get("url"):
            anexos = [{"tipo": TIPO_IMAGEM, "url": str(conteudo["url"])}]
    elif tipo_tt == "VIDEO":
        tipo = TIPO_VIDEO
        if conteudo.get("url"):
            anexos = [{"tipo": TIPO_VIDEO, "url": str(conteudo["url"])}]
    elif tipo_tt in _TIPOS_PRODUTO:
        tipo = TIPO_PRODUTO
        anuncio = str(conteudo.get("product_id") or "").strip() or None
        if anuncio:
            # Mesmo cartão das outras lojas (spec 2.2), ainda só com o id: o
            # TikTok não tem o escopo de produto/pedido aqui, e a tela desenha
            # o que houver.
            anexos = [enriquecer.cartao_produto_vazio(anuncio)]
    elif tipo_tt in _TIPOS_PEDIDO:
        tipo = TIPO_PEDIDO
        pedido = str(conteudo.get("order_id") or "").strip() or None
        if pedido:
            anexos = [enriquecer.cartao_pedido_vazio(pedido)]
    else:
        # Aviso do sistema traz texto (em pt-BR, pelo `locale`); o resto
        # (figurinha, cupom, OTHER) vira o rótulo em português e o cru fica
        # no payload.
        tipo = TIPO_OUTRO
        texto = _texto(conteudo.get("content")) or rotulo_tipo_plataforma(tipo_tt)

    return _Lida(
        externo_id=externo_id,
        autor=autor,
        tipo=tipo,
        texto=texto,
        anexos=anexos,
        enviada_em=para_datetime(msg.get("create_time")),
        pedido=pedido,
        anuncio=anuncio,
    )


def _comprador(conv: dict) -> tuple[str | None, str | None, str | None]:
    """(id, apelido, foto) do participante BUYER. `user_id` é o mesmo da Order API.

    A foto é a URL que o próprio chat entrega (`avatar`); `gravar.url_avatar`
    descarta o que não serve para `<img>`.
    """
    for p in conv.get("participants") or []:
        if isinstance(p, dict) and str(p.get("role") or "").upper() == _PAPEL_COMPRADOR:
            ident = str(p.get("user_id") or p.get("im_user_id") or "").strip() or None
            return ident, _texto(p.get("nickname")) or None, gravar.url_avatar(p.get("avatar"))
    return None, None, None


def _ts_conversa(conv: dict) -> int | None:
    ultima = conv.get("latest_message") if isinstance(conv.get("latest_message"), dict) else {}
    return _segundos(ultima.get("create_time")) or _segundos(conv.get("create_time"))


# ── Leitura ───────────────────────────────────────────────────────────────


async def _gravadas(session: AsyncSession, conversa_id, externo_ids: list[str]) -> set[str]:
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
    """Desce pelas mensagens (mais novas primeiro) até achar uma já gravada."""
    coletadas: dict[str, dict] = {}
    token: str | None = None
    for _ in range(PAGINAS_MENSAGENS):
        data = _conferir(
            await cliente.cs_messages(
                conversa.externo_id, page_size=PAGINA_MENSAGENS, page_token=token
            )
        )
        pagina = [
            m
            for m in (data.get("messages") or [])
            if isinstance(m, dict) and str(m.get("id") or "").strip()
        ]
        for m in pagina:
            coletadas.setdefault(str(m["id"]).strip(), m)
        if not pagina:
            break
        if await _gravadas(session, conversa.id, [str(m["id"]).strip() for m in pagina]):
            break
        proximo = str(data.get("next_page_token") or "").strip()
        if not proximo or proximo == token:
            break
        token = proximo
    return list(coletadas.values())


def _aplicar_janela(conversa: AtendimentoConversa, pode_enviar: Any) -> None:
    """`can_send_message` da lista → conversa bloqueada / desbloqueada.

    Só desbloqueia o que ESTE adaptador bloqueou (mesmo motivo): bloqueio
    posto por pessoa ou por outro motivo não é da conta da leitura. Conversa
    fechada por pessoa continua fechada.
    """
    if pode_enviar is False and conversa.situacao not in (CONVERSA_BLOQUEADA, CONVERSA_FECHADA):
        conversa.situacao = CONVERSA_BLOQUEADA
        conversa.bloqueio_motivo = MOTIVO_JANELA
    elif (
        pode_enviar is True
        and conversa.situacao == CONVERSA_BLOQUEADA
        and conversa.bloqueio_motivo == MOTIVO_JANELA
    ):
        conversa.situacao = CONVERSA_ABERTA
        conversa.bloqueio_motivo = None
        gravar.recalcular(conversa)


async def _sincronizar_conversa(
    session: AsyncSession,
    canal: AtendimentoCanal,
    integration: Integration,
    cliente: Any,
    conv: dict,
) -> tuple[bool, int]:
    externo_id = str(conv.get("id") or "").strip()
    if not externo_id:
        return False, 0
    comprador_id, comprador_nome, comprador_avatar = _comprador(conv)
    pode_enviar = conv.get("can_send_message")
    conversa, criada = await gravar.upsert_conversa(
        session,
        canal=canal,
        integration=integration,
        plataforma=PLATAFORMA,
        canal_nome=CANAL_CHAT,
        externo_id=externo_id,
        # O nome da LOJA (cadastro), não o da integração.
        conta=await lojas.nome_da_loja(session, integration) or None,
        comprador_id=comprador_id,
        comprador_nome=comprador_nome,
        comprador_avatar=comprador_avatar,
        nao_lidas=_int(conv.get("unread_count")),
        dados={"pode_enviar": pode_enviar} if isinstance(pode_enviar, bool) else None,
    )
    _aplicar_janela(conversa, pode_enviar)

    ultima = conv.get("latest_message") if isinstance(conv.get("latest_message"), dict) else {}
    ultima_id = str(ultima.get("id") or "").strip()
    if not criada and ultima_id and await _gravadas(session, conversa.id, [ultima_id]):
        return criada, 0

    brutas = await _mensagens_novas(session, cliente, conversa)
    # `index` desempata mensagens no mesmo segundo (a doc: cresce com o tempo).
    brutas.sort(key=lambda m: (_segundos(m.get("create_time")) or 0, _int(m.get("index")) or 0))
    novas = 0
    pedido: str | None = None
    anuncio: str | None = None
    for bruta in brutas:
        lida = interpretar(bruta)
        if lida is None:
            continue
        _, criada_msg = await gravar.gravar_mensagem(
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
        pedido = lida.pedido or pedido
        anuncio = lida.anuncio or anuncio
    if pedido or anuncio:
        await gravar.upsert_conversa(
            session,
            canal=canal,
            integration=integration,
            plataforma=PLATAFORMA,
            canal_nome=CANAL_CHAT,
            externo_id=externo_id,
            pedido_marketplace=pedido,
            anuncio_id=anuncio,
        )
    return criada, novas


@dataclass
class _Passo:
    """O resultado de uma passada pela lista (do topo ou de uma lacuna)."""

    terminou: bool
    parou_em: str | None  # page_token onde continuar, se não terminou
    visto: int | None  # o maior horário lido na passada
    processadas: int
    paginas: int


class _Interrompida(Exception):  # noqa: N818 — português, como `ErroCaixa`
    """A passada caiu no meio: `pagina` é o token da página que falhou (None = o topo)."""

    def __init__(
        self, pagina: str | None, visto: int | None, paginas: int, exc: BaseException
    ) -> None:
        self.pagina = pagina
        self.visto = visto
        self.paginas = paginas
        self.exc = exc
        super().__init__(str(exc))


async def _caminhar(
    session: AsyncSession,
    canal: AtendimentoCanal,
    integration: Integration,
    cliente: Any,
    *,
    inicio: str | None,
    piso: int,
    limite: int,
    max_paginas: int,
    cursor: _Cursor,
    resultado: ResultadoSync,
) -> _Passo:
    """Desce pela lista de `inicio` (None = o topo) até uma página toda antiga.

    No máximo `limite` conversas e `max_paginas` páginas. Levanta
    `_Interrompida` com o token da página que falhou.
    """
    pagina = inicio
    visto: int | None = None
    processadas = 0
    paginas = 0
    try:
        while processadas < limite and paginas < max_paginas:
            data = _conferir(
                await cliente.cs_conversations(
                    page_size=min(PAGINA_CONVERSAS, limite - processadas), page_token=pagina
                )
            )
            paginas += 1
            conversas = [c for c in (data.get("conversations") or []) if isinstance(c, dict)]
            antigas = 0
            for conv in conversas:
                ts = _ts_conversa(conv)
                if ts is not None and ts <= piso:
                    antigas += 1
                    continue
                externo_id = str(conv.get("id") or "").strip()
                try:
                    criada, novas = await _sincronizar_conversa(
                        session, canal, integration, cliente, conv
                    )
                except (_RecusaError, httpx.HTTPError, ValueError) as exc:
                    if isinstance(exc, _RecusaError) and exc.sem_escopo:
                        raise
                    vezes = cursor.falhas.get(externo_id, 0) + 1
                    cursor.falhas[externo_id] = vezes
                    if vezes < MAX_FALHAS_CONVERSA:
                        raise
                    cursor.falhas.pop(externo_id, None)
                    logger.error(
                        "atendimento_tiktok_conversa_pulada",
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
            proximo = str(data.get("next_page_token") or "").strip()
            if not conversas or antigas == len(conversas) or not proximo or proximo == pagina:
                return _Passo(True, None, visto, processadas, paginas)
            pagina = proximo
    except (_RecusaError, httpx.HTTPError, ValueError) as exc:
        raise _Interrompida(pagina, visto, paginas, exc) from exc
    return _Passo(False, pagina, visto, processadas, paginas)


async def sincronizar(
    session: AsyncSession,
    canal: AtendimentoCanal,
    integration: Integration,
    cliente: Any,
) -> ResultadoSync:
    """Lê o que mudou desde `canal.cursor`, grava e anda o cursor.

    Commita a cada conversa (`gravar.fim_do_item`: a trava da conversa não
    dura a rodada inteira); o cursor é gravado no fim.

    401/105005 (app sem `seller.customer_service`) → `sem_escopo`, sem gravar
    nada e sem levantar. Outro erro da TikTok ou de rede → `erro`, com o
    cursor no começo da página que falhou.

    O TOPO primeiro (até o `ultimo_ts`), depois as lacunas com o que sobrar
    do teto de conversas por rodada — a mensagem nova não espera a
    caminhada antiga terminar (ver `atendimento/shopee.py`).
    """
    resultado = ResultadoSync(status=STATUS_OK)
    maximo = max(1, int(get_settings().atendimento_sync_max_conversas or 1))
    janela = int((datetime.now(UTC) - JANELA_PRIMEIRA_LEITURA).timestamp())
    cursor = _Cursor.ler(canal.cursor, piso_inicial=janela)
    piso_topo = cursor.ultimo_ts if cursor.ultimo_ts is not None else janela
    ultimo_ts = cursor.ultimo_ts
    novas_lacunas: list[_Lacuna] = []
    pendentes = list(cursor.lacunas)
    restante = maximo
    paginas_restantes = MAX_PAGINAS_POR_RODADA
    processadas = 0
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
                max_paginas=paginas_restantes,
                cursor=cursor,
                resultado=resultado,
            )
        except _Interrompida as falha:
            if falha.pagina is not None:
                novas_lacunas.append(_Lacuna(falha.pagina, piso_topo))
                ultimo_ts = _maior(piso_topo, falha.visto)
            paginas_restantes -= falha.paginas
            raise
        ultimo_ts = _maior(piso_topo, topo.visto)
        if not topo.terminou and topo.parou_em:
            novas_lacunas.append(_Lacuna(topo.parou_em, piso_topo))
        restante -= topo.processadas
        processadas += topo.processadas
        paginas_restantes -= topo.paginas

        while pendentes and restante > 0 and paginas_restantes > 0:
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
                    max_paginas=paginas_restantes,
                    cursor=cursor,
                    resultado=resultado,
                )
            except _Interrompida as falha:
                pendentes[0] = _Lacuna(falha.pagina or lacuna.retomar, lacuna.piso)
                paginas_restantes -= falha.paginas
                raise
            pendentes.pop(0)
            if not passo.terminou and passo.parou_em:
                pendentes.insert(0, _Lacuna(passo.parou_em, lacuna.piso))
            restante -= passo.processadas
            processadas += passo.processadas
            paginas_restantes -= passo.paginas
    except _Interrompida as falha:
        exc = falha.exc
        sem_escopo = isinstance(exc, _RecusaError) and exc.sem_escopo
        nada_lido = processadas == 0 and paginas_restantes == MAX_PAGINAS_POR_RODADA
        if sem_escopo and nada_lido:
            # O normal hoje: o app não tem o escopo. Nada gravado, cursor intacto.
            logger.info("atendimento_tiktok_sem_escopo", canal_id=str(canal.id))
            return ResultadoSync(
                status=STATUS_SEM_ESCOPO,
                erro="tiktok 401/105005: app sem o escopo seller.customer_service",
            )
        canal.cursor = _Cursor(
            ultimo_ts=ultimo_ts,
            lacunas=_juntar_lacunas(novas_lacunas + pendentes),
            falhas=cursor.falhas,
        ).json()
        resultado.status = STATUS_SEM_ESCOPO if sem_escopo else STATUS_ERRO
        resultado.erro = _erro_operacao(exc)
        logger.warning(
            "atendimento_tiktok_sync_falhou",
            canal_id=str(canal.id),
            status=resultado.status,
            erro=resultado.erro,
            conversas=processadas,
        )
        return resultado

    lacunas = _juntar_lacunas(novas_lacunas + pendentes)
    canal.cursor = _Cursor(ultimo_ts=ultimo_ts, lacunas=lacunas, falhas=cursor.falhas).json()
    logger.info(
        "atendimento_tiktok_sync",
        canal_id=str(canal.id),
        conversas=processadas,
        conversas_novas=resultado.conversas_novas,
        mensagens_novas=resultado.mensagens_novas,
        lacunas=len(lacunas),
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
    """Envia `texto` na conversa. Timeout/erro sem código = ambiguo. Nunca levanta."""
    if not (texto or "").strip():
        return ResultadoEnvio(ok=False, erro="tiktok texto_vazio")
    try:
        resp = await cliente.cs_send_text(conversa.externo_id, texto)
    except Exception as exc:  # noqa: BLE001 — envio nunca levanta
        # Timeout, conexão caída, corpo que não é JSON depois de um 200: pode
        # ter saído. Vira `revisar`, nunca retenta.
        return ResultadoEnvio(ok=False, ambiguo=True, erro=f"tiktok {type(exc).__name__}")

    resp = resp if isinstance(resp, dict) else {}
    codigo = _codigo(resp)
    if codigo == 0:
        data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
        message_id = str(data.get("message_id") or "").strip()
        return ResultadoEnvio(ok=True, externo_id=message_id or None, payload=resp)
    if _eh_sem_escopo(resp):
        return ResultadoEnvio(ok=False, erro="tiktok sem_escopo", payload=resp)
    if codigo == _CODIGO_JANELA_FECHADA:
        return ResultadoEnvio(
            ok=False, erro=f"tiktok code={codigo}", bloqueio=MOTIVO_JANELA, payload=resp
        )
    if codigo is None or codigo in _CODIGOS_AMBIGUOS or 500 <= codigo < 600:
        return ResultadoEnvio(ok=False, ambiguo=True, erro=f"tiktok code={codigo}", payload=resp)
    # Recusa com código (conteúdo sensível 45101006, parâmetro...): não saiu.
    return ResultadoEnvio(ok=False, erro=f"tiktok code={codigo}", payload=resp)
