"""O índice de pedidos e avaliações por comprador — o que a Shopee não filtra.

O cartão "Cliente" da caixa (`cliente.py`) responde, no topo do painel: quem
escreve já comprou? Quanto? Cancelou, devolveu, avaliou mal? No Mercado Livre
isso sai AO VIVO (`/orders/search?buyer=`). A Shopee não filtra pedido por
comprador nem avaliação por comprador — então o DaVinci guarda um índice
próprio, uma linha por pedido e uma por avaliação:

  `atendimento_pedidos_comprador` — (integração, pedido) → comprador, data,
                                    total, status e um resumo dos itens.
  `atendimento_avaliacoes_loja`   — (integração, avaliação) → pedido, apelido
                                    do comprador na loja, estrelas, texto e
                                    a resposta da loja.

Quem alimenta: o job `atendimento_indexar_pedidos` (de hora em hora, as últimas
2 h de pedidos e as avaliações novas — `indexar.py`), a importação do
histórico (`importar.py`) e, quando ligado, cada retrato de pedido que o
enriquecimento já busca (`registrar_do_retrato`).

Por que cada cuidado:

- SÓ ID DO COMPRADOR no pedido. O `buyer_user_id` da Shopee é o mesmo `to_id`
  do chat — id, não dado pessoal. Nome, endereço e CPF nunca entram.
- UPSERT, NUNCA DUPLICA. A mesma avaliação e o mesmo pedido voltam em toda
  rodada (janela de 2 h a cada 1 h, de propósito, para nada escapar entre
  duas). Campo que chega vazio não apaga o que já se sabia.
- FALHA ISOLADA. Cada gravação roda num SAVEPOINT: o índice é enfeite do
  cartão e não pode derrubar a rodada do sync nem o enriquecimento de quem o
  chama. Nunca commita — quem chama commita.

Texto de comprador (a avaliação) nunca vai para o log — só ids e contagens.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AtendimentoAvaliacaoLoja, AtendimentoPedidoComprador
from app.redis_client import redis

logger = structlog.get_logger()

# Tamanhos das colunas (o model manda; o corte aqui evita erro de banco).
PEDIDO_MAX = 64
COMPRADOR_MAX = 128
STATUS_MAX = 32
PLATAFORMA_MAX = 16
COMENTARIO_MAX = 64
ITEM_MAX = 64
# A avaliação inteira não cabe no cartão e não precisa: 500 caracteres dizem
# o que o cliente achou.
TEXTO_AVALIACAO_MAX = 500
ITENS_RESUMO_MAX = 300

# Desde quando o índice de uma loja tem o histórico INTEIRO (a importação
# grava o começo da janela que leu). Sem essa marca, "1 compra" pode ser só
# o que o job das últimas horas viu — e o cartão não afirma "primeira compra".
CHAVE_COBERTURA = "atendimento:indice:cobertura:{}"
COBERTURA_TTL_S = 400 * 24 * 3600


# ── Conversões ────────────────────────────────────────────────────────────


def _curto(valor: Any, tamanho: int) -> str | None:
    """Texto limpo e cortado no tamanho da coluna; vazio vira None. Número vira texto."""
    if valor is None or isinstance(valor, bool):
        return None
    texto = str(valor).replace("\x00", "").strip()
    return texto[:tamanho] or None


def _texto(valor: Any, tamanho: int) -> str | None:
    """Texto livre (avaliação, resposta): só string, sem NUL, cortado."""
    if not isinstance(valor, str):
        return None
    texto = valor.replace("\x00", "").strip()
    if not texto:
        return None
    return texto if len(texto) <= tamanho else texto[: tamanho - 1].rstrip() + "…"


def _valor(bruto: Any) -> float | None:
    if bruto is None or isinstance(bruto, bool):
        return None
    try:
        v = float(bruto)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return round(v, 2)


def de_epoch(bruto: Any) -> datetime | None:
    """Horário da Shopee em segundos (ms também) → UTC. Zero/lixo = None."""
    try:
        n = int(bruto)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    if n > 10**11:
        n //= 1000
    try:
        return datetime.fromtimestamp(n, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def de_iso(bruto: Any) -> datetime | None:
    """ISO (o `criado_em` do retrato do pedido) → UTC."""
    if isinstance(bruto, datetime):
        return bruto if bruto.tzinfo else bruto.replace(tzinfo=UTC)
    if not isinstance(bruto, str) or not bruto.strip():
        return None
    try:
        quando = datetime.fromisoformat(bruto.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return quando if quando.tzinfo else quando.replace(tzinfo=UTC)


def resumo_itens(itens: Iterable[tuple[str | None, int | None]]) -> str | None:
    """ "2× Mala de bordo; Cadeado TSA" — nome do anúncio (não é dado pessoal)."""
    partes: list[str] = []
    for nome, quantidade in itens:
        nome = (nome or "").strip()
        if not nome:
            continue
        q = quantidade if isinstance(quantidade, int) and quantidade > 0 else 1
        partes.append(f"{q}× {nome}" if q > 1 else nome)
    return _texto("; ".join(partes), ITENS_RESUMO_MAX)


def _inteiro(bruto: Any) -> int | None:
    try:
        return int(bruto)
    except (TypeError, ValueError):
        return None


def pedido_shopee(o: dict, *, comprador_id: str | None = None) -> dict | None:
    """Pedido do `get_order_detail` → argumentos de `registrar_pedido` (sem sessão/integração).

    `comprador_id` é o reserva para quando o pedido não traz o `buyer_user_id`
    (o retrato do painel não o pede; a conversa sabe o `to_id`). None quando
    falta o número do pedido ou o comprador.
    """
    if not isinstance(o, dict):
        return None
    pedido = _curto(o.get("order_sn"), PEDIDO_MAX)
    comprador = _curto(o.get("buyer_user_id"), COMPRADOR_MAX)
    if comprador == "0":
        comprador = None
    comprador = comprador or _curto(comprador_id, COMPRADOR_MAX)
    if not pedido or not comprador:
        return None
    itens = [
        (it.get("item_name"), _inteiro(it.get("model_quantity_purchased")))
        for it in (o.get("item_list") or [])
        if isinstance(it, dict)
    ]
    return {
        "plataforma": "shopee",
        "comprador_id": comprador,
        "pedido": pedido,
        "criado_em": de_epoch(o.get("create_time")),
        "total": _valor(o.get("total_amount")),
        "status": _curto(str(o.get("order_status") or "").upper(), STATUS_MAX),
        "itens_resumo": resumo_itens(itens),
    }


def avaliacao_shopee(c: dict) -> dict | None:
    """Item do `get_comment` → argumentos de `registrar_avaliacao` (sem sessão/integração)."""
    if not isinstance(c, dict):
        return None
    comentario = _curto(c.get("comment_id"), COMENTARIO_MAX)
    estrelas = _inteiro(c.get("rating_star"))
    if not comentario or estrelas is None:
        return None
    resposta = c.get("comment_reply") if isinstance(c.get("comment_reply"), dict) else {}
    return {
        "plataforma": "shopee",
        "comentario_id": comentario,
        "pedido": _curto(c.get("order_sn"), PEDIDO_MAX),
        "comprador_nome_loja": _curto(c.get("buyer_username"), 255),
        "item_id": _curto(c.get("item_id"), ITEM_MAX),
        "estrelas": max(1, min(5, estrelas)),
        "texto": _texto(c.get("comment"), TEXTO_AVALIACAO_MAX),
        "resposta_loja": _texto(resposta.get("reply"), TEXTO_AVALIACAO_MAX),
        "criado_em": de_epoch(c.get("create_time")),
    }


# ── Gravação ──────────────────────────────────────────────────────────────


async def _upsert(session: AsyncSession, stmt: Any, *, evento: str, **ids: Any) -> bool:
    """Roda o upsert num SAVEPOINT; erro de banco vira log (com ids) e False."""
    # O que já estava pendente vai ANTES do SAVEPOINT: um rollback dele não
    # pode levar junto o trabalho de quem chamou.
    await session.flush()
    try:
        async with session.begin_nested():
            await session.execute(stmt)
    except SQLAlchemyError as exc:
        logger.warning(evento, erro=type(exc).__name__, **{k: str(v) for k, v in ids.items()})
        return False
    return True


async def registrar_pedido(
    session: AsyncSession,
    *,
    integration_id: UUID,
    plataforma: str,
    comprador_id: str | None,
    pedido: str | None,
    criado_em: datetime | None,
    total: float | None,
    status: str | None,
    itens_resumo: str | None,
) -> None:
    """Grava (ou atualiza) a linha do pedido no índice. Nunca commita, nunca levanta.

    Sem pedido ou sem comprador não há o que indexar (a linha não seria achada
    por ninguém). Campo None não apaga o que já estava lá — o status, esse sim,
    anda (é o que diz "cancelou", "devolveu").
    """
    numero = _curto(pedido, PEDIDO_MAX)
    comprador = _curto(comprador_id, COMPRADOR_MAX)
    if integration_id is None or not numero or not comprador:
        return
    agora = datetime.now(UTC)
    tabela = AtendimentoPedidoComprador.__table__
    stmt = pg_insert(AtendimentoPedidoComprador).values(
        id=uuid4(),
        integration_id=integration_id,
        plataforma=_curto(plataforma, PLATAFORMA_MAX) or "",
        comprador_id=comprador,
        pedido=numero,
        criado_em=de_iso(criado_em) if criado_em is not None else None,
        total=_valor(total),
        status=_curto(status, STATUS_MAX),
        itens_resumo=_texto(itens_resumo, ITENS_RESUMO_MAX),
        atualizado_em=agora,
    )
    novo = stmt.excluded
    stmt = stmt.on_conflict_do_update(
        index_elements=[tabela.c.integration_id, tabela.c.pedido],
        set_={
            "comprador_id": novo.comprador_id,
            "plataforma": novo.plataforma,
            "criado_em": func.coalesce(novo.criado_em, tabela.c.criado_em),
            "total": func.coalesce(novo.total, tabela.c.total),
            "status": func.coalesce(novo.status, tabela.c.status),
            "itens_resumo": func.coalesce(novo.itens_resumo, tabela.c.itens_resumo),
            "atualizado_em": novo.atualizado_em,
        },
    )
    await _upsert(
        session,
        stmt,
        evento="atendimento_indice_pedido_falhou",
        integration_id=integration_id,
        pedido=numero,
    )


async def registrar_avaliacao(
    session: AsyncSession,
    *,
    integration_id: UUID,
    plataforma: str,
    comentario_id: str | None,
    pedido: str | None,
    comprador_nome_loja: str | None,
    item_id: str | None,
    estrelas: int | None,
    texto: str | None,
    resposta_loja: str | None,
    criado_em: datetime | None,
) -> None:
    """Grava (ou atualiza) a avaliação no índice. Nunca commita, nunca levanta.

    A mesma avaliação volta com a resposta da loja (ou editada pelo cliente):
    estrelas, texto e resposta são regravados; o que vier vazio não apaga.
    """
    comentario = _curto(comentario_id, COMENTARIO_MAX)
    nota = _inteiro(estrelas)
    if integration_id is None or not comentario or nota is None:
        return
    tabela = AtendimentoAvaliacaoLoja.__table__
    stmt = pg_insert(AtendimentoAvaliacaoLoja).values(
        id=uuid4(),
        integration_id=integration_id,
        plataforma=_curto(plataforma, PLATAFORMA_MAX) or "",
        comentario_id=comentario,
        pedido=_curto(pedido, PEDIDO_MAX),
        comprador_nome_loja=_curto(comprador_nome_loja, 255),
        item_id=_curto(item_id, ITEM_MAX),
        estrelas=max(1, min(5, nota)),
        texto=_texto(texto, TEXTO_AVALIACAO_MAX),
        resposta_loja=_texto(resposta_loja, TEXTO_AVALIACAO_MAX),
        criado_em=de_iso(criado_em) if criado_em is not None else None,
    )
    novo = stmt.excluded
    stmt = stmt.on_conflict_do_update(
        index_elements=[tabela.c.integration_id, tabela.c.comentario_id],
        set_={
            "estrelas": novo.estrelas,
            "pedido": func.coalesce(novo.pedido, tabela.c.pedido),
            "comprador_nome_loja": func.coalesce(
                novo.comprador_nome_loja, tabela.c.comprador_nome_loja
            ),
            "item_id": func.coalesce(novo.item_id, tabela.c.item_id),
            "texto": func.coalesce(novo.texto, tabela.c.texto),
            "resposta_loja": func.coalesce(novo.resposta_loja, tabela.c.resposta_loja),
            "criado_em": func.coalesce(novo.criado_em, tabela.c.criado_em),
        },
    )
    await _upsert(
        session,
        stmt,
        evento="atendimento_indice_avaliacao_falhou",
        integration_id=integration_id,
        comentario_id=comentario,
    )


async def avaliacoes_conhecidas(
    session: AsyncSession, integration_id: UUID, comentario_ids: list[str]
) -> set[str]:
    """Quais destas avaliações já estão no índice (a leitura incremental para nelas)."""
    ids = [c for c in comentario_ids if c]
    if not ids:
        return set()
    linhas = await session.execute(
        select(AtendimentoAvaliacaoLoja.comentario_id).where(
            AtendimentoAvaliacaoLoja.integration_id == integration_id,
            AtendimentoAvaliacaoLoja.comentario_id.in_(ids),
        )
    )
    return {str(x) for x in linhas.scalars().all()}


async def registrar_do_retrato(
    session: AsyncSession,
    *,
    integration_id: UUID,
    plataforma: str,
    comprador_id: str | None,
    retrato: dict | None,
) -> None:
    """O retrato do pedido que o enriquecimento acabou de buscar → linha do índice.

    Para quem enriquece (`enriquecer.retrato_pedido` da Shopee): o pedido já
    veio da loja — indexá-lo não custa ida nenhuma. O comprador é o da
    conversa (`to_id` do chat = `buyer_user_id` do pedido). Nunca levanta.
    """
    if not isinstance(retrato, dict):
        return
    itens = [
        (it.get("titulo"), _inteiro(it.get("quantidade")))
        for it in (retrato.get("itens") or [])
        if isinstance(it, dict)
    ]
    await registrar_pedido(
        session,
        integration_id=integration_id,
        plataforma=plataforma,
        comprador_id=comprador_id,
        pedido=retrato.get("pedido"),
        criado_em=de_iso(retrato.get("criado_em")),
        total=_valor(retrato.get("total")),
        status=retrato.get("status"),
        itens_resumo=resumo_itens(itens),
    )


# ── Cobertura do histórico ────────────────────────────────────────────────


async def marcar_cobertura(integration_id: UUID, desde: datetime) -> None:
    """A importação leu o histórico da loja desde `desde`: o índice é completo dali em diante.

    Fica a data MAIS ANTIGA já importada (uma importação curta depois de uma
    longa não encolhe a cobertura). Redis fora do ar = sem marca (o cartão só
    deixa de afirmar "primeira compra").
    """
    chave = CHAVE_COBERTURA.format(integration_id)
    try:
        atual = de_iso(await redis.get(chave))
        if atual is not None and atual <= desde:
            desde = atual
        await redis.set(chave, desde.isoformat(timespec="seconds"), ex=COBERTURA_TTL_S)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "atendimento_indice_cobertura_falhou",
            integration_id=str(integration_id),
            erro=type(exc).__name__,
        )


async def cobertura(integration_id: UUID) -> datetime | None:
    """Desde quando o índice da loja é completo (None = não se sabe)."""
    try:
        bruto = await redis.get(CHAVE_COBERTURA.format(integration_id))
    except Exception:  # noqa: BLE001 — sem Redis, não se afirma nada
        return None
    if isinstance(bruto, bytes):
        bruto = bruto.decode("utf-8", "ignore")
    return de_iso(bruto)
