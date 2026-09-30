"""O cartão "Cliente": quem é a pessoa que escreve, numa olhada (28/09/2026).

Antes de responder, quem atende quer saber: é a primeira compra ou é cliente
de sempre? Quanto já gastou? Já cancelou, devolveu, reclamou? Avaliou mal? E
perguntou alguma coisa antes de comprar? O Duoke mostra isso ao lado da
conversa; aqui é o cartão no topo do painel da direita, e os mesmos `sinais`
entram no contexto da IA (avaliou mal ou reclamação aberta → pessoa confere).

De onde vem cada coisa, por plataforma:

- MERCADO LIVRE: os pedidos do comprador AO VIVO (`/orders/search?buyer=`, o
  ML filtra — medido em 28/09) e a avaliação dos 5 pedidos mais recentes
  (`/orders/{id}/feedback`, 404 = sem avaliação). Cache de 2 h no Redis por
  comprador: a tela relê a conversa aberta a cada 15 s. Falha também fica em
  cache (10 min), para a API da loja não levar uma chamada a cada 15 s.
- SHOPEE: pelo índice próprio (`indice.py`: pedidos por `buyer_user_id`,
  avaliações por pedido e pelo apelido do comprador na loja), que o job de
  hora em hora e a importação do histórico alimentam. Sem a marca de
  cobertura do histórico, "1 compra" pode ser só o que o job viu — então o
  cartão não afirma "primeira compra".
- TIKTOK / AMAZON: o que existir — o pedido das conversas do mesmo comprador
  no nosso banco. Nada inventado.
- EM TODAS: as outras conversas do mesmo comprador na mesma loja (perguntas
  pré-venda, mensagens, reclamação aberta no ML) e as devoluções que o
  DaVinci já registrou para os pedidos dele.

Formato (o que o router devolve em `GET /conversas/{id}` → `cliente`):

  {"desde": ISO|null, "compras": n, "total_gasto": float, "ultima_compra": ISO|null,
   "devolucoes": n, "cancelamentos": n,
   "avaliacoes": [{"estrelas", "texto", "pedido", "criado_em", "respondida"}],
   "perguntas_pre_venda": n,
   "sinais": ["recorrente"|"avaliou_mal"|"reclamacao_aberta"|"ja_pediu_devolucao"
              |"primeira_compra"],
   "linha_do_tempo": [{"tipo", "em", "texto", "ref"}],
   "historico_completo": bool, "historico_desde": ISO|null}

`compras` conta COMPRAS (o carrinho do ML com 3 pedidos é uma), sem as
canceladas e sem as que ainda esperam pagamento (Shopee UNPAID, ML
payment_required...); `total_gasto` soma só as que contam.
`perguntas_pre_venda` conta as OUTRAS perguntas no anúncio (a aberta na tela
não) feitas antes de alguma compra; sem compra, todas as outras. Na linha do
tempo, `ref` é o número do pedido (compra, envio, entrega, avaliação) ou o id
da conversa no DaVinci (pergunta, mensagem, reclamação) — é para onde o
clique leva.

NUNCA LEVANTA: o cartão é ajuda, a conversa abre sem ele. Sem dado nenhum
além da própria conversa, devolve `{}`. Log só com ids — nunca nome, texto
de avaliação ou de mensagem.
"""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AtendimentoAvaliacaoLoja,
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoPedidoComprador,
    Devolution,
    Integration,
)
from app.redis_client import redis
from app.services.atendimento import clientes, indice
from app.services.atendimento.constantes import (
    AUTOR_CLIENTE,
    CANAL_CHAT,
    CANAL_EMAIL,
    CANAL_PERGUNTA,
    CANAL_POS_VENDA,
    reclamacao_aberta,
)

logger = structlog.get_logger()

# ── Sinais (o vocabulário que a tela e a IA leem) ─────────────────────────
SINAL_RECORRENTE = "recorrente"
SINAL_AVALIOU_MAL = "avaliou_mal"
SINAL_RECLAMACAO_ABERTA = "reclamacao_aberta"
SINAL_JA_PEDIU_DEVOLUCAO = "ja_pediu_devolucao"
SINAL_PRIMEIRA_COMPRA = "primeira_compra"
# Os que pedem PESSOA na resposta (a IA marca precisa_humano).
SINAIS_DE_CUIDADO = frozenset({SINAL_AVALIOU_MAL, SINAL_RECLAMACAO_ABERTA})

# Até 2 estrelas é avaliação ruim (o "negative" do ML vale 1; o "neutral", 3).
NOTA_RUIM = 2
ESTRELAS_ML = {"positive": 5, "neutral": 3, "negative": 1}

# Status de pedido (em minúsculas; ML e Shopee juntos).
CANCELADOS = frozenset({"cancelled", "in_cancel", "invalid"})
DEVOLVIDOS = frozenset({"to_return"})
# Pedido ainda SEM pagamento: não é compra (nem soma no gasto) até pagar — o
# comprador que desiste do Pix fica com o pedido UNPAID até a Shopee
# cancelar, dias depois. ML: os três status que a própria doc de mensagens
# bloqueadas chama de "não pago" (payment_required, payment_in_process,
# partially_paid). Na linha do tempo, a compra aparece "aguardando pagamento".
SEM_PAGAMENTO = frozenset({"unpaid", "payment_required", "payment_in_process", "partially_paid"})

# ── Economia de chamada (ML ao vivo) ──────────────────────────────────────
CACHE_ML_S = 2 * 3600
CACHE_FALHA_S = 10 * 60
# O cartão não segura a conversa: passou disto, a tela abre sem os pedidos
# do ML — e a busca termina em segundo plano e deixa o cache pronto.
TEMPO_MAX_API_S = 6.0
MAX_PEDIDOS_ML = 50
MAX_FEEDBACKS = 5
_CHAVE_ML = "atendimento:cliente:ml:{}:{}"
# Uma busca por comprador de cada vez: enquanto o cache não é gravado, a
# tela (a cada 15 s, por atendente) e a IA abririam uma busca NOVA cada uma.
# O TTL passa do pior caso da busca (3 tentativas de 30 s no 429, pedidos e
# feedbacks): processo morto solta sozinho.
_CHAVE_ML_EM_ANDAMENTO = "atendimento:cliente:ml:andamento:{}:{}"
EM_ANDAMENTO_TTL_S = 300
# Teto de buscas por loja por minuto (cada uma = 1 /orders/search + até 5
# /feedback), na mesma cota que os robôs de pedido usam — o mesmo número do
# botão "atualizar" do painel Pedido.
LIMITE_ML_POR_LOJA = 10
JANELA_LIMITE_ML_S = 60
_CHAVE_ML_LIMITE = "atendimento:cliente:ml:limite:{}"
# O interruptor que a equipe usa quando a plataforma começa a devolver 429.
CANAL_DESLIGADO = "desligado"

# ── Tamanho do cartão ─────────────────────────────────────────────────────
MAX_CONVERSAS = 50
MAX_PEDIDOS_INDICE = 200
MAX_AVALIACOES = 5
MAX_LINHA_DO_TEMPO = 30
TEXTO_CURTO = 90

_ROTULO_CANAL = {
    CANAL_CHAT: "Conversa no chat",
    CANAL_POS_VENDA: "Mensagem pós-venda",
    CANAL_EMAIL: "E-mail",
    CANAL_PERGUNTA: "Pergunta no anúncio",
}

# Buscas do ML que passaram do tempo e seguem em segundo plano (referência
# forte, senão o coletor de lixo pode matar a tarefa no meio).
_EM_SEGUNDO_PLANO: set[asyncio.Task] = set()


# ── Conversões ────────────────────────────────────────────────────────────


def _id(bruto: Any) -> str:
    return "" if bruto is None or isinstance(bruto, bool) else str(bruto).strip()


def _valor(bruto: Any) -> float | None:
    if bruto is None or isinstance(bruto, bool):
        return None
    try:
        v = float(bruto)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else round(v, 2)


def _utc(quando: datetime | None) -> datetime | None:
    if quando is None:
        return None
    return quando if quando.tzinfo else quando.replace(tzinfo=UTC)


def _iso(quando: datetime | None) -> str | None:
    quando = _utc(quando)
    return quando.astimezone(UTC).isoformat(timespec="seconds") if quando else None


def _curto(texto: str | None, tamanho: int = TEXTO_CURTO) -> str:
    texto = " ".join((texto or "").split())
    return texto if len(texto) <= tamanho else texto[: tamanho - 1].rstrip() + "…"


def _brl(valor: float | None) -> str | None:
    """1840.5 → "R$ 1.840,50"."""
    if valor is None:
        return None
    inteiro = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {inteiro}"


def _dict(bruto: Any) -> dict:
    return bruto if isinstance(bruto, dict) else {}


# ── O que o cartão junta ──────────────────────────────────────────────────


@dataclass
class _Compra:
    pedido: str
    grupo: str  # a COMPRA: o pack do ML (carrinho); nas outras, o próprio pedido
    criado_em: datetime | None
    total: float | None
    status: str | None
    itens: str | None

    @property
    def cancelada(self) -> bool:
        return (self.status or "").lower() in CANCELADOS

    @property
    def devolvida(self) -> bool:
        return (self.status or "").lower() in DEVOLVIDOS

    @property
    def sem_pagamento(self) -> bool:
        return (self.status or "").lower() in SEM_PAGAMENTO

    @property
    def conta(self) -> bool:
        """É compra de verdade: nem cancelada, nem esperando pagamento."""
        return not self.cancelada and not self.sem_pagamento


@dataclass
class _Avaliacao:
    estrelas: int
    texto: str | None
    pedido: str | None
    criado_em: datetime | None
    respondida: bool


@dataclass
class _Evento:
    tipo: str
    em: datetime
    texto: str
    ref: str


# ── Mercado Livre (ao vivo, com cache) ────────────────────────────────────


def _pedido_ml(o: dict) -> dict | None:
    """Pedido do `/orders/search` → só número, pack, data, total, status e itens.

    O pedido do ML traz nome e apelido do comprador: nada disso passa daqui.
    """
    pedido = _id(o.get("id"))
    if not pedido:
        return None
    itens = [
        (_dict(ln.get("item")).get("title"), ln.get("quantity"))
        for ln in (o.get("order_items") or [])
        if isinstance(ln, dict)
    ]
    criado = indice.de_iso(o.get("date_created"))
    return {
        "pedido": pedido,
        "grupo": _id(o.get("pack_id")) or pedido,
        "criado_em": _iso(criado),
        "total": _valor(o.get("total_amount")),
        "status": _id(o.get("status")).lower() or None,
        "itens": indice.resumo_itens(
            (t if isinstance(t, str) else None, q if isinstance(q, int) else None) for t, q in itens
        ),
    }


def _avaliacao_ml(corpo: dict, pedido: str, seller: str, comprador: str) -> dict | None:
    """A avaliação que o COMPRADOR deu à venda, no corpo do `/orders/{id}/feedback`.

    O corpo traz dois lados (`sale`, `purchase`). Vale o que veio do comprador
    (`from.id`) ou foi para a loja (`to.id`); o que a loja escreveu sobre o
    comprador nunca entra. Sem `from`/`to`, o `sale` (a venda avaliada).
    """
    lados = [x for x in (corpo.get("sale"), corpo.get("purchase")) if isinstance(x, dict)]

    def de(lado: dict, chave: str) -> str:
        return _id(_dict(lado.get(chave)).get("id"))

    escolhido = next((x for x in lados if comprador and de(x, "from") == comprador), None)
    escolhido = escolhido or next((x for x in lados if seller and de(x, "to") == seller), None)
    if escolhido is None and isinstance(corpo.get("sale"), dict):
        escolhido = corpo["sale"]
    if escolhido is None or (seller and de(escolhido, "from") == seller):
        return None
    estrelas = ESTRELAS_ML.get(_id(escolhido.get("rating")).lower())
    if estrelas is None:
        return None
    resposta = escolhido.get("reply")
    if isinstance(resposta, dict):
        resposta = resposta.get("text") or resposta.get("message")
    mensagem = escolhido.get("message")
    return {
        "estrelas": estrelas,
        "texto": (
            _curto(mensagem, indice.TEXTO_AVALIACAO_MAX) if isinstance(mensagem, str) else None
        ),
        "pedido": pedido,
        "criado_em": _iso(indice.de_iso(escolhido.get("date_created"))),
        "respondida": bool(isinstance(resposta, str) and resposta.strip()),
    }


async def _cache_ler(chave: str) -> dict | None:
    try:
        bruto = await redis.get(chave)
    except Exception:  # noqa: BLE001 — sem Redis, busca de novo
        return None
    if not bruto:
        return None
    try:
        valor = json.loads(bruto)
    except (TypeError, ValueError):
        return None
    return valor if isinstance(valor, dict) else None


async def _cache_gravar(chave: str, valor: dict, ttl: int) -> None:
    try:
        await redis.set(chave, json.dumps(valor, ensure_ascii=False), ex=ttl)
    except Exception:  # noqa: BLE001 — o cache é economia, não correção
        logger.info("atendimento_cliente_cache_falhou")


async def _buscar_ml(integration: Integration, comprador: str) -> dict:
    """Os pedidos do comprador e a avaliação dos mais recentes, direto do ML."""
    cliente = await clientes.cliente_da_integracao(integration)
    corpo = await cliente.pedidos_do_comprador(comprador, limit=MAX_PEDIDOS_ML)
    corpo = _dict(corpo)
    pedidos = [
        p for o in (corpo.get("results") or []) if isinstance(o, dict) and (p := _pedido_ml(o))
    ]
    pedidos.sort(key=lambda p: p["criado_em"] or "", reverse=True)
    total = int(_valor(_dict(corpo.get("paging")).get("total")) or len(pedidos))
    seller = _id(_dict(getattr(cliente, "creds", None)).get("user_id"))
    recentes = pedidos[:MAX_FEEDBACKS]
    respostas = await asyncio.gather(
        *(cliente.feedback_do_pedido(p["pedido"]) for p in recentes), return_exceptions=True
    )
    avaliacoes = [
        a
        for p, r in zip(recentes, respostas, strict=True)
        if isinstance(r, dict) and (a := _avaliacao_ml(r, p["pedido"], seller, comprador))
    ]
    return {"pedidos": pedidos, "total": max(total, len(pedidos)), "avaliacoes": avaliacoes}


async def _buscar_e_guardar(
    integration: Integration, comprador: str, chave: str, chave_andamento: str | None = None
) -> dict | None:
    """Busca no ML e grava no cache — também quando quem pediu já desistiu de esperar.

    No fim solta a marca de "busca em andamento" (`chave_andamento`): com o
    cache gravado, quem vier depois lê dele.
    """
    try:
        try:
            dados = await _buscar_ml(integration, comprador)
        except Exception as exc:  # noqa: BLE001 — falha da loja: cartão sem os pedidos do ML
            logger.info(
                "atendimento_cliente_ml_falhou",
                integration_id=str(integration.id),
                erro=type(exc).__name__,
            )
            await _cache_gravar(chave, {"falhou": True}, CACHE_FALHA_S)
            return None
        await _cache_gravar(chave, dados, CACHE_ML_S)
        return dados
    finally:
        if chave_andamento is not None:
            try:
                await redis.delete(chave_andamento)
            except Exception:  # noqa: BLE001, S110 — o TTL solta sozinho
                pass


async def _canal_desligado(session: AsyncSession, conversa: AtendimentoConversa) -> bool:
    """O canal da conversa está `desligado`? (o interruptor do 429, o mesmo do painel Pedido)"""
    if conversa.canal_id is None:
        return False
    status = await session.scalar(
        select(AtendimentoCanal.status).where(AtendimentoCanal.id == conversa.canal_id)
    )
    return status == CANAL_DESLIGADO


async def _pode_ir_ao_ml(integration_id: Any, comprador: str) -> str | None:
    """Pega a vez de buscar no ML → a chave de "em andamento" (None = não vá agora).

    Não vai: outra busca deste comprador em andamento (SET NX), a loja já
    passou do teto do minuto, ou o Redis fora do ar — sem ele não há como
    segurar a rajada na API da loja, e o cartão é ajuda (abre sem os pedidos).
    """
    andamento = _CHAVE_ML_EM_ANDAMENTO.format(integration_id, comprador)
    try:
        if not await redis.set(andamento, "1", nx=True, ex=EM_ANDAMENTO_TTL_S):
            return None
        limite = _CHAVE_ML_LIMITE.format(integration_id)
        feitas = int(await redis.incr(limite))
        if feitas == 1 or await redis.ttl(limite) < 0:
            await redis.expire(limite, JANELA_LIMITE_ML_S)
    except Exception as exc:  # noqa: BLE001
        logger.info("atendimento_cliente_ml_trava_falhou", erro=type(exc).__name__)
        return None
    if feitas > LIMITE_ML_POR_LOJA:
        logger.info("atendimento_cliente_ml_limite", integration_id=str(integration_id))
        try:
            await redis.delete(andamento)
        except Exception:  # noqa: BLE001, S110 — o TTL solta sozinho
            pass
        return None
    return andamento


async def _ml_ao_vivo(
    session: AsyncSession, conversa: AtendimentoConversa, comprador: str
) -> dict | None:
    """`{"pedidos", "total", "avaliacoes"}` do comprador no ML, do cache ou da loja; None = sem.

    Só com a leitura ligada (`atendimento_leitura_ativa`): desligada, o
    DaVinci não fala com loja nenhuma — nem para abrir o cartão (a tela relê
    a conversa a cada 15 s). O cartão fica com o que o banco sabe (perguntas,
    o pedido da conversa) e não afirma "primeira compra".

    As outras chaves da ida à loja (revisão de 28/09), as mesmas do botão
    "atualizar" do painel Pedido: loja arquivada ou canal `desligado` (o
    interruptor que se usa quando o ML começa a devolver 429) não vão; uma
    busca por comprador de cada vez; teto de buscas por loja por minuto.
    Quem não pode ir agora fica sem os pedidos do ML desta vez.
    """
    if not get_settings().atendimento_leitura_ativa:
        return None
    chave = _CHAVE_ML.format(conversa.integration_id, comprador)
    guardado = await _cache_ler(chave)
    if guardado is not None:
        return None if guardado.get("falhou") else guardado
    integration = await session.get(Integration, conversa.integration_id)
    if integration is None or integration.archived_at is not None:
        return None
    if await _canal_desligado(session, conversa):
        return None
    andamento = await _pode_ir_ao_ml(integration.id, comprador)
    if andamento is None:
        return None
    # A busca roda numa tarefa própria e BLINDADA: se o tempo estourar, ela
    # continua (e grava o cache). Cancelar no meio seria arriscar o refresh
    # token de uso único do ML, que pode estar sendo renovado nessa hora.
    tarefa = asyncio.ensure_future(_buscar_e_guardar(integration, comprador, chave, andamento))
    _EM_SEGUNDO_PLANO.add(tarefa)
    tarefa.add_done_callback(_EM_SEGUNDO_PLANO.discard)
    try:
        return await asyncio.wait_for(asyncio.shield(tarefa), TEMPO_MAX_API_S)
    except TimeoutError:
        logger.info("atendimento_cliente_ml_demorou", integration_id=str(integration.id))
        return None


def _compras_do_ml(dados: dict) -> list[_Compra]:
    return [
        _Compra(
            pedido=_id(p.get("pedido")),
            grupo=_id(p.get("grupo")) or _id(p.get("pedido")),
            criado_em=indice.de_iso(p.get("criado_em")),
            total=_valor(p.get("total")),
            status=p.get("status"),
            itens=p.get("itens"),
        )
        for p in (dados.get("pedidos") or [])
        if isinstance(p, dict) and _id(p.get("pedido"))
    ]


def _avaliacoes_do_ml(dados: dict) -> list[_Avaliacao]:
    saida = []
    for a in dados.get("avaliacoes") or []:
        if not isinstance(a, dict) or not isinstance(a.get("estrelas"), int):
            continue
        saida.append(
            _Avaliacao(
                estrelas=a["estrelas"],
                texto=a.get("texto"),
                pedido=a.get("pedido"),
                criado_em=indice.de_iso(a.get("criado_em")),
                respondida=bool(a.get("respondida")),
            )
        )
    return saida


# ── Shopee (pelo índice) ──────────────────────────────────────────────────


async def _compras_do_indice(
    session: AsyncSession, conversa: AtendimentoConversa, comprador: str
) -> list[_Compra]:
    t = AtendimentoPedidoComprador
    linhas = (
        (
            await session.execute(
                select(t)
                .where(t.integration_id == conversa.integration_id, t.comprador_id == comprador)
                .order_by(t.criado_em.desc().nulls_last())
                .limit(MAX_PEDIDOS_INDICE)
            )
        )
        .scalars()
        .all()
    )
    return [
        _Compra(
            pedido=linha.pedido,
            grupo=linha.pedido,
            criado_em=_utc(linha.criado_em),
            total=linha.total,
            status=linha.status,
            itens=linha.itens_resumo,
        )
        for linha in linhas
    ]


async def _avaliacoes_do_indice(
    session: AsyncSession, conversa: AtendimentoConversa, pedidos: list[str]
) -> list[_Avaliacao]:
    """Pelas compras dele e pelo apelido na loja (`buyer_username` = `to_name` do chat)."""
    a = AtendimentoAvaliacaoLoja
    condicoes = []
    if pedidos:
        condicoes.append(a.pedido.in_(pedidos))
    nome = (conversa.comprador_nome or "").strip()
    if nome:
        condicoes.append(a.comprador_nome_loja == nome)
    if not condicoes:
        return []
    linhas = (
        (
            await session.execute(
                select(a)
                .where(a.integration_id == conversa.integration_id, or_(*condicoes))
                .order_by(a.criado_em.desc().nulls_last())
                .limit(MAX_AVALIACOES)
            )
        )
        .scalars()
        .all()
    )
    return [
        _Avaliacao(
            estrelas=int(linha.estrelas),
            texto=linha.texto,
            pedido=linha.pedido,
            criado_em=_utc(linha.criado_em),
            respondida=bool((linha.resposta_loja or "").strip()),
        )
        for linha in linhas
    ]


# ── O que o nosso banco já sabe (todas as plataformas) ────────────────────


async def _conversas_do_comprador(
    session: AsyncSession, conversa: AtendimentoConversa, comprador: str
) -> list[AtendimentoConversa]:
    """As conversas do mesmo comprador NA MESMA LOJA (inclui a aberta)."""
    return list(
        (
            await session.execute(
                select(AtendimentoConversa)
                .where(
                    AtendimentoConversa.integration_id == conversa.integration_id,
                    AtendimentoConversa.comprador_id == comprador,
                )
                .order_by(AtendimentoConversa.ultima_mensagem_em.desc().nulls_last())
                .limit(MAX_CONVERSAS)
            )
        )
        .scalars()
        .all()
    )


def _compras_das_conversas(conversas: list[AtendimentoConversa]) -> list[_Compra]:
    """O pedido de cada conversa (o retrato do painel, quando há) — o que existir."""
    saida: list[_Compra] = []
    for c in conversas:
        retrato = _dict(_dict(c.dados).get("pedido_mkt"))
        numero = _id(retrato.get("pedido")) or _id(c.pedido_marketplace)
        if not numero:
            continue
        itens = [
            (it.get("titulo"), it.get("quantidade"))
            for it in (retrato.get("itens") or [])
            if isinstance(it, dict)
        ]
        saida.append(
            _Compra(
                pedido=numero,
                grupo=_id(_dict(c.dados).get("pack_id")) or numero,
                criado_em=indice.de_iso(retrato.get("criado_em")),
                total=_valor(retrato.get("total")),
                status=_id(retrato.get("status")).lower() or None,
                itens=indice.resumo_itens(
                    (t if isinstance(t, str) else None, q if isinstance(q, int) else None)
                    for t, q in itens
                ),
            )
        )
    return saida


def _juntar(principais: list[_Compra], extras: list[_Compra]) -> list[_Compra]:
    """As da loja/índice valem; as das conversas só entram se forem de outra compra."""
    conhecidos = {c.pedido for c in principais} | {c.grupo for c in principais}
    saida = list(principais)
    for c in extras:
        if c.pedido in conhecidos or c.grupo in conhecidos:
            continue
        conhecidos |= {c.pedido, c.grupo}
        saida.append(c)
    return saida


async def _grupos_devolvidos(session: AsyncSession, compras: list[_Compra]) -> set[str]:
    """Compras com devolução: status da loja (TO_RETURN) ou devolução registrada no DaVinci."""
    grupos = {c.grupo for c in compras if c.devolvida}
    por_chave = {c.pedido: c.grupo for c in compras} | {c.grupo: c.grupo for c in compras}
    chaves = [k for k in por_chave if k][:400]
    if chaves:
        achados = (
            await session.execute(
                select(Devolution.pedido_marketplace)
                .where(Devolution.pedido_marketplace.in_(chaves))
                .distinct()
            )
        ).scalars()
        grupos |= {por_chave[k] for k in achados if k in por_chave}
    return grupos


async def _primeira_mensagem(
    session: AsyncSession, conversas: list[AtendimentoConversa]
) -> datetime | None:
    ids = [c.id for c in conversas]
    if not ids:
        return None
    return _utc(
        await session.scalar(
            select(
                func.min(
                    func.coalesce(AtendimentoMensagem.enviada_em, AtendimentoMensagem.created_at)
                )
            ).where(
                AtendimentoMensagem.conversa_id.in_(ids),
                AtendimentoMensagem.autor == AUTOR_CLIENTE,
            )
        )
    )


# ── Montagem ──────────────────────────────────────────────────────────────


def _eventos_das_conversas(
    conversas: list[AtendimentoConversa], aberta: AtendimentoConversa
) -> list[_Evento]:
    eventos: list[_Evento] = []
    vistos: set[tuple[str, str]] = set()
    for c in conversas:
        quando = _utc(c.ultima_mensagem_em) or _utc(c.created_at)
        dados = _dict(c.dados)
        # Envio e entrega saem do retrato do pedido (o painel já buscou).
        retrato = _dict(dados.get("pedido_mkt"))
        numero = _id(retrato.get("pedido")) or _id(c.pedido_marketplace)
        for tipo, chave, texto in (
            ("envio", "enviado_em", "Pedido enviado"),
            ("entrega", "concluido_em", "Pedido concluído"),
        ):
            em = indice.de_iso(retrato.get(chave))
            if em is not None and numero and (tipo, numero) not in vistos:
                vistos.add((tipo, numero))
                eventos.append(_Evento(tipo, em, texto, numero))
        if quando is None:
            continue
        if reclamacao_aberta(dados):
            eventos.append(_Evento("reclamacao", quando, "Reclamação no Mercado Livre", str(c.id)))
        if c.id == aberta.id:
            continue  # a conversa aberta é a que está na tela
        if c.canal == CANAL_PERGUNTA:
            texto = c.anuncio_titulo or _dict(dados.get("produto")).get("titulo")
            # A bolinha fica na hora em que o CLIENTE perguntou (a mesma que
            # `_perguntas_pre_venda` compara com as compras), não na resposta
            # da loja: a tela decide "perguntou antes de comprar" por esta data.
            eventos.append(
                _Evento(
                    "pergunta",
                    _quando_perguntou(c) or quando,
                    _curto(texto) or "Pergunta no anúncio",
                    str(c.id),
                )
            )
        else:
            eventos.append(
                _Evento("mensagem", quando, _ROTULO_CANAL.get(c.canal, "Mensagem"), str(c.id))
            )
    return eventos


def _texto_compra(c: _Compra) -> str:
    if c.cancelada:
        situacao = "Cancelada"
    elif c.sem_pagamento:
        situacao = "Aguardando pagamento"
    else:
        situacao = _brl(c.total)
    partes = [situacao, c.itens]
    return _curto(" · ".join(p for p in partes if p)) or "Compra"


def _quando_perguntou(c: AtendimentoConversa) -> datetime | None:
    """Quando o cliente fez a pergunta (a fala dele; sem ela, a última da conversa)."""
    return _utc(c.ultima_do_cliente_em) or _utc(c.ultima_mensagem_em) or _utc(c.created_at)


def _perguntas_pre_venda(
    conversas: list[AtendimentoConversa], aberta: AtendimentoConversa, datas_compras: list[datetime]
) -> int:
    """As OUTRAS perguntas no anúncio feitas antes de uma compra.

    A pergunta aberta na tela não conta (é a que se está respondendo, e
    quem já comprou três vezes e pergunta hoje não "perguntou antes de
    comprar"). Com compra: só a pergunta que veio antes de alguma compra
    (a compra seguinte a ela). Sem compra nenhuma: todas as outras — é
    pré-venda que ainda não virou compra.
    """
    perguntas = [c for c in conversas if c.canal == CANAL_PERGUNTA and c.id != aberta.id]
    if not datas_compras:
        return len(perguntas)
    ultima = max(datas_compras)
    return sum(1 for c in perguntas if (q := _quando_perguntou(c)) is not None and q < ultima)


def _texto_avaliacao(a: _Avaliacao) -> str:
    estrelas = "★" * a.estrelas + "☆" * (5 - a.estrelas)
    return _curto(f"{estrelas} {a.texto or ''}")


def _montar(
    *,
    compras: list[_Compra],
    avaliacoes: list[_Avaliacao],
    conversas: list[AtendimentoConversa],
    aberta: AtendimentoConversa,
    devolvidos: set[str],
    primeira_mensagem: datetime | None,
    completo: bool,
    historico_desde: datetime | None,
) -> dict:
    grupos: dict[str, list[_Compra]] = {}
    for c in compras:
        grupos.setdefault(c.grupo, []).append(c)
    # Uma COMPRA vale quando algum pedido dela conta (nem cancelado, nem
    # esperando pagamento). Um carrinho do ML só é "cancelado" quando todos
    # os pedidos dele foram; o que só espera pagamento não é compra nem
    # cancelamento — ainda.
    validos = {g: cs for g, cs in grupos.items() if any(c.conta for c in cs)}
    cancelados = sum(1 for cs in grupos.values() if all(c.cancelada for c in cs))
    datas_validas = [
        d for cs in validos.values() for c in cs if c.conta and (d := _utc(c.criado_em)) is not None
    ]
    total_gasto = round(sum(c.total or 0.0 for cs in validos.values() for c in cs if c.conta), 2)
    perguntas = _perguntas_pre_venda(conversas, aberta, datas_validas)
    reclamacao = any(reclamacao_aberta(c.dados) for c in conversas) or reclamacao_aberta(
        aberta.dados
    )

    sinais: list[str] = []
    if len(validos) >= 2:
        sinais.append(SINAL_RECORRENTE)
    elif len(validos) == 1 and completo:
        sinais.append(SINAL_PRIMEIRA_COMPRA)
    if any(a.estrelas <= NOTA_RUIM for a in avaliacoes):
        sinais.append(SINAL_AVALIOU_MAL)
    if reclamacao:
        sinais.append(SINAL_RECLAMACAO_ABERTA)
    if devolvidos:
        sinais.append(SINAL_JA_PEDIU_DEVOLUCAO)

    eventos = _eventos_das_conversas(conversas, aberta)
    for cs in grupos.values():
        # Uma bolinha por COMPRA (o carrinho do ML com 3 pedidos é uma).
        datas = [d for c in cs if (d := _utc(c.criado_em)) is not None]
        if not datas:
            continue
        if all(c.cancelada for c in cs):
            status = cs[0].status
        elif not any(c.conta for c in cs):
            status = next(c.status for c in cs if c.sem_pagamento)
        else:
            status = None
        compra = _Compra(
            pedido=cs[0].pedido,
            grupo=cs[0].grupo,
            criado_em=min(datas),
            total=round(sum(c.total or 0.0 for c in cs if c.conta), 2) or None,
            status=status,
            itens=indice.resumo_itens((c.itens, None) for c in cs),
        )
        eventos.append(_Evento("compra", min(datas), _texto_compra(compra), compra.pedido))
    for a in avaliacoes:
        if a.criado_em is not None:
            eventos.append(_Evento("avaliacao", a.criado_em, _texto_avaliacao(a), a.pedido or ""))
    eventos.sort(key=lambda e: e.em)
    eventos = eventos[-MAX_LINHA_DO_TEMPO:]

    inicio = [d for d in (*(_utc(c.criado_em) for c in compras), primeira_mensagem) if d]
    return {
        "desde": _iso(min(inicio)) if inicio else None,
        "compras": len(validos),
        "total_gasto": total_gasto,
        "ultima_compra": _iso(max(datas_validas)) if datas_validas else None,
        "devolucoes": len(devolvidos),
        "cancelamentos": cancelados,
        "avaliacoes": [
            {
                "estrelas": a.estrelas,
                "texto": a.texto,
                "pedido": a.pedido,
                "criado_em": _iso(a.criado_em),
                "respondida": a.respondida,
            }
            for a in sorted(
                avaliacoes,
                key=lambda a: a.criado_em or datetime.min.replace(tzinfo=UTC),
                reverse=True,
            )[:MAX_AVALIACOES]
        ],
        "perguntas_pre_venda": perguntas,
        "sinais": sinais,
        "linha_do_tempo": [
            {"tipo": e.tipo, "em": _iso(e.em), "texto": e.texto, "ref": e.ref} for e in eventos
        ],
        "historico_completo": completo,
        "historico_desde": _iso(historico_desde),
    }


async def _cartao(session: AsyncSession, conversa: AtendimentoConversa) -> dict:
    comprador = _id(conversa.comprador_id)
    if not comprador or conversa.integration_id is None:
        return {}
    async with session.begin_nested():
        conversas = await _conversas_do_comprador(session, conversa, comprador)
        das_conversas = _compras_das_conversas(conversas)
        compras: list[_Compra] = []
        avaliacoes: list[_Avaliacao] = []
        completo = False
        historico_desde: datetime | None = None
        if conversa.plataforma == "ml":
            ao_vivo = await _ml_ao_vivo(session, conversa, comprador)
            if ao_vivo is not None:
                compras = _compras_do_ml(ao_vivo)
                avaliacoes = _avaliacoes_do_ml(ao_vivo)
                completo = int(ao_vivo.get("total") or 0) <= len(compras)
        elif conversa.plataforma == "shopee":
            compras = await _compras_do_indice(session, conversa, comprador)
            avaliacoes = await _avaliacoes_do_indice(
                session, conversa, [c.pedido for c in compras] + [c.pedido for c in das_conversas]
            )
            historico_desde = await indice.cobertura(conversa.integration_id)
            completo = historico_desde is not None
        compras = _juntar(compras, das_conversas)
        devolvidos = await _grupos_devolvidos(session, compras)
        primeira = await _primeira_mensagem(session, conversas)
    outras = [c for c in conversas if c.id != conversa.id]
    if not compras and not avaliacoes and not outras:
        return {}
    return _montar(
        compras=compras,
        avaliacoes=avaliacoes,
        conversas=conversas,
        aberta=conversa,
        devolvidos=devolvidos,
        primeira_mensagem=primeira,
        completo=completo,
        historico_desde=historico_desde,
    )


async def cartao_cliente(session: AsyncSession, conversa: AtendimentoConversa) -> dict:
    """O cartão "Cliente" da conversa (formato no topo do módulo). Nunca levanta; `{}` sem dado.

    As leituras do banco rodam num SAVEPOINT: uma consulta que falhe não
    estraga a transação de quem chamou (o `GET /conversas/{id}` segue).
    """
    conversa_id = str(getattr(conversa, "id", ""))
    try:
        return await _cartao(session, conversa)
    except Exception as exc:  # noqa: BLE001 — ver docstring
        logger.warning(
            "atendimento_cliente_falhou", conversa_id=conversa_id, erro=type(exc).__name__
        )
        return {}


def sinais_de_cuidado(cartao: dict | None) -> list[str]:
    """Os sinais do cartão que pedem pessoa na resposta (para a IA)."""
    return [s for s in (cartao or {}).get("sinais") or [] if s in SINAIS_DE_CUIDADO]
