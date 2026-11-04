"""O pedido em foco no chat da Shopee com devolução aberta (04/11/2026).

A decisão do dono: "vai ser basicamente igual, só quando um produto tiver
devolução mantém no mesmo chat, caso não, segue igual"; "o negócio é pra aquele
pedido, então se reconhecer a devolução fica naquele". O chat da Shopee é UM por
comprador na loja e fica ligado ao ÚLTIMO pedido citado. Com devolução aberta
LIGADA a ele (`atendimento_reclamacoes.conversa_id`), o `pedido_marketplace` do
chat fica no pedido DEVOLVIDO, mesmo com cartão ou citação de outra compra; o
último citado fica guardado em `dados.pedido_citado`. Encerrou: o chat volta
para o citado e a chave sai. Sem devolução aberta, nada muda.

GRAVAR, e não escolher só na hora de mostrar: contexto, etiqueta, abas, NF,
agente e retrato leem `pedido_marketplace`; escolher na tela deixaria a tela em
um pedido e as ações em outro. Quem aplica é `gravar.upsert_conversa` (a leitura,
qualquer um dos escritores) e `gravar.aplicar_foco_da_devolucao` (a passada do
cron). Este módulo não importa `gravar` — é `gravar` que importa este.

Quem vale: SÓ o chat da Shopee, fora o de afiliado (`vale_para`). TikTok, ML,
Magalu, Amazon, e-mail e o chat de afiliado continuam como antes.

QUAL DEVOLUÇÃO (`pedido_em_foco`): a aberta (`encerrada_em IS NULL`) com pedido;
duas abertas no mesmo chat: a de prazo mais curto, sem prazo por último, empate
pela mais recente (`aberta_em`), depois pelo número; prazo já vencido conta como
sem prazo. Aberta há mais de `JANELA_FOCO` NÃO prende o foco, nem a que saiu da
Logística (`_ainda_na_logistica`) — medido em 04/11: 147 das 230 "abertas" não
estão mais lá, porque a faxina apaga a linha quando o Bling finaliza o pedido e
ninguém encerra o caso. O cartão delas continua aparecendo, como antes.

A LISTA (`conversas_da_devolucao_com_chat`): a conversa `reclamacao` da Shopee
some da lista quando existe chat NÃO FECHADO do mesmo comprador na mesma loja e
nenhum caso aberto continua ligado a ela, sem apagar nada: um filtro na leitura.
Consulta pequena antes, e os ids vão como
valores (`fora_da_lista_sql`): com subconsulta, o /resumo pagava o JIT a mais
(ver `humano.lojas_da_caixa`). Tirar o filtro devolve tudo como era.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import case, exists, func, not_, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models import AtendimentoConversa, AtendimentoReclamacao, Logistica
from app.services.atendimento.constantes import (
    CANAL_CHAT,
    CANAL_RECLAMACAO,
    CHAVE_AFILIADO,
    CONVERSA_FECHADA,
    FONTE_LOGISTICA,
    e_chat_de_afiliado,
)

PLATAFORMA = "shopee"
# Aberta há mais tempo que isto não prende o foco (ver o topo).
JANELA_FOCO = timedelta(days=30)
CHAVE_PEDIDO_CITADO = "pedido_citado"


def _agora() -> datetime:
    return datetime.now(UTC)


def _texto(bruto: Any) -> str | None:
    texto = "" if bruto is None else str(bruto).strip()
    return texto or None


def vale_para(conversa: Any) -> bool:
    """O foco vale para esta conversa? Só o chat da Shopee, fora o de afiliado. PURA."""
    plataforma = getattr(conversa, "plataforma", None)
    return (
        plataforma == PLATAFORMA
        and getattr(conversa, "canal", None) == CANAL_CHAT
        and not e_chat_de_afiliado(plataforma, getattr(conversa, "dados", None))
    )


def chat_do_comprador_serve(conversa: Any, integration_id: UUID | None) -> bool:
    """Este chat é onde a devolução da loja `integration_id` entra? PURA.

    O chat da Shopee (`vale_para`), da MESMA loja (o id do comprador é por loja)
    e não fechado: fechado sai da lista, e a devolução ficaria invisível.
    """
    return (
        integration_id is not None
        and vale_para(conversa)
        and getattr(conversa, "integration_id", None) == integration_id
        and getattr(conversa, "situacao", None) != CONVERSA_FECHADA
    )


def pedido_citado(dados: Any) -> str | None:
    """O último pedido citado, guardado enquanto o foco está em outro. PURA."""
    return _texto(dados.get(CHAVE_PEDIDO_CITADO)) if isinstance(dados, dict) else None


def citou_outro_pedido(conversa: Any) -> bool:
    """O chat está preso num pedido e o comprador citou OUTRO por último? PURA.

    Ele pode estar falando da outra compra: nada sai sozinho sobre o pedido preso
    (a devolução de rotina, o "responde e marca", a NF automática).
    """
    citado = pedido_citado(getattr(conversa, "dados", None))
    atual = _texto(getattr(conversa, "pedido_marketplace", None))
    return citado is not None and citado != atual


def _ainda_na_logistica(r: Any) -> Any:
    """SQL: o caso desta linha ainda está na Logística (a de outra fonte não depende dela)."""
    return or_(
        r.dados["fonte"].astext.is_distinct_from(FONTE_LOGISTICA),
        exists().where(
            Logistica.pedido_marketplace == r.pedido_marketplace,
            func.coalesce(Logistica.meli_status["return_status"].astext, "") != "",
        ),
    )


async def pedido_em_foco(
    session: AsyncSession, conversa: AtendimentoConversa, agora: datetime | None = None
) -> str | None:
    """O pedido da devolução que segura este chat agora; None = nenhuma (ver o topo)."""
    if not vale_para(conversa):
        return None
    r = AtendimentoReclamacao
    momento = agora or _agora()
    inicio = momento - JANELA_FOCO
    prazo_vivo = case((r.prazo_em > momento, r.prazo_em), else_=None)
    return (
        await session.execute(
            select(r.pedido_marketplace)
            .where(
                r.conversa_id == conversa.id,
                r.plataforma == PLATAFORMA,
                r.encerrada_em.is_(None),
                r.pedido_marketplace.is_not(None),
                r.pedido_marketplace != "",
                func.coalesce(r.aberta_em, r.created_at) >= inicio,
                _ainda_na_logistica(r),
            )
            .order_by(
                prazo_vivo.asc().nulls_last(),
                r.aberta_em.desc().nulls_last(),
                r.externo_id.asc(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()


def decidir_pedido(
    atual: str | None, citado: str | None, foco: str | None, lido: str | None
) -> tuple[str | None, str | None]:
    """(pedido do chat, pedido citado) depois desta passada. PURA.

    `lido` = o pedido que a LEITURA trouxe (a última citação do comprador); None
    = a passada do cron, que só aplica a regra.

      leitura, com foco A   → o pedido fica A; citado = lido. Sem chave ainda e o
                              lido sendo o próprio A, não nasce chave;
      leitura, sem foco     → o pedido é o lido; a chave sai;
      cron, foco A, atual B → citado = B, se ainda vazio; o pedido é A;
      cron, sem foco        → com citado, volta para ele e a chave sai; sem
                              citado, nada.
    """
    if lido is not None:
        if foco is None:
            return lido, None
        return foco, lido if (lido != foco or citado) else None
    if foco is not None:
        return foco, citado or (atual if atual != foco else None)
    if citado:
        return citado, None
    return atual, None


def _chats_do_mesmo_comprador(devolucao: Any) -> Any:
    """SQL: existe chat NÃO FECHADO do comprador desta conversa `reclamacao`, na mesma loja."""
    chat = aliased(AtendimentoConversa)
    return exists().where(
        chat.plataforma == PLATAFORMA,
        chat.canal == CANAL_CHAT,
        chat.integration_id == devolucao.integration_id,
        chat.comprador_id == devolucao.comprador_id,
        chat.situacao != CONVERSA_FECHADA,
        # `gravar.chat_de_afiliado_sql` (este módulo não importa `gravar`).
        not_(chat.dados.contains({CHAVE_AFILIADO: True})),
    )


async def conversas_da_devolucao_com_chat(session: AsyncSession) -> frozenset[UUID]:
    """As conversas `reclamacao` da Shopee que a lista esconde: o comprador tem chat aberto
    e nenhum caso aberto ficou ligado a ela (o que está ligado precisa de um lugar na tela).

    A régua única da lista, do /resumo e da Caixa Humano (ver o topo). Abas,
    `conversas_do_pedido` e o acesso direto por id continuam vendo todas.
    """
    devolucao = aliased(AtendimentoConversa)
    r = AtendimentoReclamacao
    linhas = await session.execute(
        select(devolucao.id).where(
            devolucao.plataforma == PLATAFORMA,
            devolucao.canal == CANAL_RECLAMACAO,
            devolucao.integration_id.is_not(None),
            devolucao.comprador_id.is_not(None),
            _chats_do_mesmo_comprador(devolucao),
            not_(exists().where(r.conversa_id == devolucao.id, r.encerrada_em.is_(None))),
        )
    )
    return frozenset(linhas.scalars().all())


def fora_da_lista_sql(ocultas: Collection[UUID]) -> Any:
    """WHERE da régua: a conversa não é uma das `ocultas`. Nunca NULL (`id` é NOT NULL)."""
    if not ocultas:
        return true()
    # Ordenados: o mesmo texto de SQL a cada pedido (o cache de comandos).
    return AtendimentoConversa.id.not_in(sorted(ocultas, key=str))
