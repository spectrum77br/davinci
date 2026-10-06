"""O comparador das mensagens automáticas com o Duoke (05/10/2026).

O modo seco só serve se der para dizer, automação por automação e loja por
loja, se o DaVinci mandaria o MESMO que o Duoke manda — é isso que diz quando
dá para trocar (docs/atendimento-automacoes.md §6). Roda no fim de cada
rodada do motor (`automacoes.rodada`), só lendo o banco:

  1. PENDENTES — cada linha decidida com `duoke = 'pendente'` procura a
     mensagem do Duoke da mesma automação (o modelo pela ASSINATURA do texto,
     `automacoes_catalogo.assinatura`) dentro da JANELA dela: na mesma
     conversa, ou — nas do pedido — pelo cartão do pedido que o Duoke manda
     junto (`content.order_sn` a até 10 s do texto), pelo pedido ligado à
     conversa ou pelo comprador do índice. Achou → `mandou` (a mensagem, a
     hora e a diferença: Duoke − a hora em que a NOSSA sairia — a decisão no
     modo seco, a mensagem gravada no modo enviar; não o `devido_em`, que
     escondia os 1 a 2 min da rodada). A janela fechou sem achar → `nao_mandou` — só se a
     LEITURA da loja já passou do fim da janela (`atendimento_canais.
     ultimo_ok_em`); com a leitura parada (deploy, token), continua pendente.
     Só conta mensagem de FORA (`externo`/`sistema`) e sem a marca do motor
     (`payload.automacao`): a nossa não casa consigo mesma.
  2. SÓ DUOKE — a mensagem do Duoke (das últimas 48 h, com o motor e a regra
     ligados há tempo bastante) que nenhuma linha usou vira a linha
     `so_duoke`: o que o DaVinci deixaria de mandar.
  3. DISJUNTOR — o Duoke mandou numa regra que já está em `enviar` (depois
     da troca): a regra volta sozinha para `simular` e o log avisa.

A resposta da avaliação (05/10, noite) compara pela própria AVALIAÇÃO
(`_comparar_avaliacoes`: a resposta pública gravada, com o chat do Duoke junto)
e tem o "só Duoke" dela (`_so_duoke_avaliacoes`); o pedido não pago visto
tarde pelo índice de hora em hora é a diferença combinada `visto_de_hora_em_hora`.

`estatisticas` é a conta que a tela mostra (precisão, cobertura e a % que
bateu, separadas; a diferença para o Duoke e o atraso real do DaVinci) e o
critério da troca. `duoke_dos_pedidos` é a mesma régua do pedido para o motor
no modo enviar: o Duoke já mandou ESTA automação para ESTE pedido?

As consultas em `atendimento_mensagens` levam também o `enviada_em` (tem
índice; o `coalesce(enviada_em, created_at)` sozinho varria a tabela inteira a
cada 2 min — medido em produção, 05/10: nenhuma mensagem de 30 dias sem
`enviada_em`).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AtendimentoAutomacaoRegistro,
    AtendimentoAutomacaoRegra,
    AtendimentoAvaliacaoLoja,
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoPedidoComprador,
    AtendimentoReclamacao,
    BlingOrder,
    Logistica,
)
from app.services.atendimento import automacoes_catalogo as cat
from app.services.atendimento.constantes import (
    AUTOR_CLIENTE,
    ORIGEM_EXTERNO,
    ORIGEM_SISTEMA,
    _trecho_like,
)

logger = structlog.get_logger()

_R = AtendimentoAutomacaoRegistro
_G = AtendimentoAutomacaoRegra
_M = AtendimentoMensagem
_C = AtendimentoConversa

# Até quantas linhas pendentes por rodada (as mais velhas primeiro).
MAX_PENDENTES = 3000
# INSERT de muitas linhas em lotes: o asyncpg recusa mais de 32.767 parâmetros
# (são 23 por linha do "só Duoke").
LOTE_INSERT = 500
# O cartão do pedido do Duoke vai 1 s antes do texto.
CARTAO_JUNTO = timedelta(seconds=10)
# O "só Duoke" olha só as últimas 48 h, e só depois de 1 h (o gatilho pode
# ainda estar chegando: a leitura, o Bling, a varredura da Logística).
SO_DUOKE_JANELA = timedelta(hours=48)
SO_DUOKE_ESPERA = timedelta(hours=1)
# O menu do Duoke sem mensagem do comprador nestes minutos é o "fim de sessão".
MENU_SEM_COMPRADOR = timedelta(minutes=3)
# A resposta de opção repetida pelo Duoke (o ciclo de 12 h do robô).
OPCAO_REPETIDA = timedelta(hours=36)
# O "carrinho": o índice de pedidos vê o pedido na 1ª rodada (no :22, de 0 a
# ~60 min depois da criação, mais a rodada). Pago no Bling até tanto depois da
# criação = pago antes de o índice o ver não pago (`carrinhos_vistos_tarde`).
VISTO_PELO_INDICE = timedelta(hours=2)
# Bling no fluxo de devolução (Resolvido, Aguardando Devolução...).
_SITUACOES_DEVOLUCAO_BLING = cat.SITUACOES_BLING_DEVOLUCAO
# Critério da troca (proposta; decisão do Eduardo, §6.5 do doc).
CRITERIO_MINIMO = 0.95
CRITERIO_CASOS = 30
# ... e 7 dias de DADOS daquela regra/loja (06/10: a aba mostrava "pode trocar"
# no "aguarde" da ATV com 17 h de motor — os 30 casos vieram num dia só).
CRITERIO_DIAS = 7

_ESTADOS_DECIDIDOS = (
    cat.ESTADO_SIMULADO,
    cat.ESTADO_PULADO,
    cat.ESTADO_ENVIANDO,
    cat.ESTADO_ENVIADO,
    cat.ESTADO_REVISAR,
    cat.ESTADO_FALHOU,
)
_ESTADOS_QUE_MANDAM = (
    cat.ESTADO_SIMULADO,
    cat.ESTADO_ENVIADO,
    cat.ESTADO_ENVIANDO,
    cat.ESTADO_REVISAR,
)


def _utc(quando: datetime | None) -> datetime | None:
    if quando is None:
        return None
    return quando.replace(tzinfo=UTC) if quando.tzinfo is None else quando.astimezone(UTC)


def _momento():
    return func.coalesce(_M.enviada_em, _M.created_at)


def _no_intervalo(ini: datetime, fim: datetime | None = None):
    """`momento` no intervalo, com o `enviada_em` indexado na frente (a mesma conta)."""
    momento = _momento()
    filtro = [_M.enviada_em >= ini, momento >= ini]
    if fim is not None:
        filtro += [_M.enviada_em <= fim, momento <= fim]
    return and_(*filtro)


def _filtro_assinaturas():
    """Pré-filtro barato no SQL: o texto pode ser um modelo do Duoke (o Python confirma)."""
    minusculo = func.lower(func.left(_M.texto, 300))
    return or_(*(minusculo.like(_trecho_like(trecho)) for _, _, _, trecho in cat.ASSINATURAS_DUOKE))


def _de_fora():
    """A mensagem é de FORA (Duoke) e não é nossa (sem a marca do motor)."""
    return and_(
        _M.autor != AUTOR_CLIENTE,
        _M.origem.in_((ORIGEM_EXTERNO, ORIGEM_SISTEMA)),
        func.coalesce(func.jsonb_typeof(_M.payload.op("->")("automacao")), "") != "object",
    )


async def _mensagens_do_duoke(
    session: AsyncSession, filtro, ini: datetime, fim: datetime
) -> list[tuple[UUID, cat.Msg]]:
    """(conversa, mensagem classificada) do Duoke no intervalo — com o tipo do modelo."""
    momento = _momento()
    linhas = (
        await session.execute(
            select(
                _M.id,
                _M.conversa_id,
                _M.autor,
                _M.origem,
                _M.status,
                momento.label("em"),
                _M.created_at,
                func.left(_M.texto, 400).label("inicio"),
            ).where(
                filtro,
                _de_fora(),
                _no_intervalo(ini, fim),
                _filtro_assinaturas(),
            )
        )
    ).all()
    saida = []
    for r in linhas:
        m = cat.classificar(
            id=r.id,
            autor=r.autor,
            origem=r.origem,
            status=r.status,
            texto=r.inicio,
            payload={},
            em=r.em,
            visto_em=r.created_at,
        )
        if m.tipo_duoke:
            saida.append((r.conversa_id, m))
    return saida


async def _cartoes(
    session: AsyncSession, filtro, ini: datetime, fim: datetime
) -> dict[UUID, list[tuple[datetime, str]]]:
    """Os cartões de pedido da LOJA por conversa: (hora, pedido)."""
    momento = _momento()
    linhas = (
        await session.execute(
            select(_M.conversa_id, momento, _M.payload[("content", "order_sn")].astext).where(
                filtro,
                _M.autor != AUTOR_CLIENTE,
                _M.payload["message_type"].astext == "order",
                _no_intervalo(ini - CARTAO_JUNTO, fim + CARTAO_JUNTO),
            )
        )
    ).all()
    saida: dict[UUID, list[tuple[datetime, str]]] = defaultdict(list)
    for conversa_id, em, sn in linhas:
        if sn:
            saida[conversa_id].append((_utc(em), sn.strip()))
    return saida


async def _ultimo_ok(session: AsyncSession) -> dict[tuple[UUID, str], datetime | None]:
    linhas = (
        await session.execute(
            select(
                AtendimentoCanal.integration_id,
                AtendimentoCanal.canal,
                AtendimentoCanal.ultimo_ok_em,
            )
            .where(AtendimentoCanal.integration_id.is_not(None))
            .execution_options(populate_existing=True)
        )
    ).all()
    return {(i, c): _utc(ok) for i, c, ok in linhas}


def _casa(aut: cat.Automacao, m: cat.Msg) -> bool:
    return m.tipo_duoke == aut.tipo and (aut.opcao is None or m.opcao_duoke == aut.opcao)


async def _usadas(session: AsyncSession, ids: list[UUID]) -> set[UUID]:
    if not ids:
        return set()
    return set(
        (await session.execute(select(_R.duoke_mensagem_id).where(_R.duoke_mensagem_id.in_(ids))))
        .scalars()
        .all()
    )


def _uuid(valor: Any) -> UUID | None:
    try:
        return valor if isinstance(valor, UUID) else UUID(str(valor))
    except (TypeError, ValueError):
        return None


async def _saidas(
    session: AsyncSession, linhas: list[AtendimentoAutomacaoRegistro]
) -> dict[UUID, datetime]:
    """Quando a NOSSA mensagem saiu (ou sairia): a 1ª parte gravada no modo
    enviar; sem mensagem, a decisão (no modo seco é a hora em que ela sairia).
    A linha que não manda (pulada) não tem hora de saída: fica o `devido_em`."""
    ids = {u for x in linhas for i in (x.mensagem_ids or []) if (u := _uuid(i)) is not None}
    momentos: dict[UUID, datetime] = {}
    if ids:
        for mid, em in (
            await session.execute(select(_M.id, _momento()).where(_M.id.in_(sorted(ids, key=str))))
        ).all():
            momentos[mid] = _utc(em)
    saida: dict[UUID, datetime] = {}
    for x in linhas:
        tempos = [
            momentos[u]
            for i in (x.mensagem_ids or [])
            if (u := _uuid(i)) is not None and u in momentos
        ]
        if tempos:
            saida[x.id] = min(tempos)
        elif x.estado in _ESTADOS_QUE_MANDAM and x.decidido_em is not None:
            saida[x.id] = _utc(x.decidido_em)
        else:
            saida[x.id] = _utc(x.devido_em)
    return saida


def _marcar_mandou(
    linha: AtendimentoAutomacaoRegistro, m: cat.Msg, agora: datetime, saiu: datetime | None = None
) -> None:
    """Duoke mandou. A diferença é Duoke − a hora em que a NOSSA saiu (ou sairia)."""
    linha.duoke = cat.DUOKE_MANDOU
    linha.duoke_mensagem_id = m.id
    linha.duoke_em = m.em
    base = saiu or _utc(linha.devido_em)
    linha.duoke_diferenca_s = int((m.em - base).total_seconds())
    linha.comparado_em = agora


async def _pedidos_do_comprador(
    session: AsyncSession, conversas: dict[UUID, AtendimentoConversa]
) -> dict[UUID, set[str]]:
    """Conversa → os pedidos do comprador dela na mesma loja (pelo índice)."""
    pares = {(c.integration_id, c.comprador_id) for c in conversas.values() if c.comprador_id}
    if not pares:
        return {}
    P = AtendimentoPedidoComprador  # noqa: N806
    linhas = (
        await session.execute(
            select(P.integration_id, P.comprador_id, P.pedido).where(
                P.comprador_id.in_(sorted({c for _, c in pares}))
            )
        )
    ).all()
    por_par: dict[tuple, set[str]] = defaultdict(set)
    for integ, comprador, pedido in linhas:
        por_par[(integ, comprador)].add(pedido)
    return {
        cid: por_par.get((c.integration_id, c.comprador_id), set()) for cid, c in conversas.items()
    }


async def _comparar_conversas(
    session: AsyncSession,
    linhas: list[AtendimentoAutomacaoRegistro],
    *,
    agora: datetime,
    ultimo_ok: dict,
) -> tuple[int, int]:
    """As linhas de conversa: a mensagem do Duoke da mesma automação na mesma conversa."""
    if not linhas:
        return 0, 0
    janelas = {
        x.id: cat.janela_comparacao(cat.CATALOGO[x.automacao], x.evento_em, x.devido_em)
        for x in linhas
    }
    ini = min(j[0] for j in janelas.values())
    fim = min(max(j[1] for j in janelas.values()), agora)
    ids = sorted({x.conversa_id for x in linhas}, key=str)
    duoke = await _mensagens_do_duoke(session, _M.conversa_id.in_(ids), ini, fim)
    usadas = await _usadas(session, [m.id for _, m in duoke])
    por_conversa: dict[UUID, list[cat.Msg]] = defaultdict(list)
    for cid, m in duoke:
        if m.id not in usadas:
            por_conversa[cid].append(m)
    saidas = await _saidas(session, linhas)
    mandou = nao = 0
    for x in sorted(linhas, key=lambda y: y.devido_em):
        aut = cat.CATALOGO[x.automacao]
        a, b = janelas[x.id]
        candidatas = [
            m for m in por_conversa.get(x.conversa_id, []) if _casa(aut, m) and a <= m.em <= b
        ]
        if candidatas:
            m = min(candidatas, key=lambda y: abs((y.em - _utc(x.devido_em)).total_seconds()))
            por_conversa[x.conversa_id].remove(m)
            _marcar_mandou(x, m, agora, saidas.get(x.id))
            mandou += 1
            continue
        lido = ultimo_ok.get((x.integration_id, aut.canal))
        if agora > b and lido is not None and lido > b:
            x.duoke = cat.DUOKE_NAO_MANDOU
            x.comparado_em = agora
            nao += 1
    return mandou, nao


_Candidata = tuple[cat.Msg, UUID | None, set[str], set[str]]


async def _candidatas_de_pedido(
    session: AsyncSession,
    integs: list[UUID],
    ini: datetime,
    fim: datetime,
    *,
    agora: datetime,
    excluir_usadas: bool,
) -> list[_Candidata]:
    """As mensagens do Duoke nas conversas das lojas, cada uma com os pedidos a que pode ser.

    (mensagem, loja, pedidos pelo cartão a até 10 s, os outros pedidos da
    conversa: o ligado, os do comprador no índice e os dos cartões de 45 dias).
    """
    da_loja = _M.conversa_id.in_(
        select(_C.id).where(_C.integration_id.in_(integs), _C.canal == "chat")
    )
    duoke = await _mensagens_do_duoke(session, da_loja, ini, fim)
    if excluir_usadas:
        usadas = await _usadas(session, [m.id for _, m in duoke])
        duoke = [(cid, m) for cid, m in duoke if m.id not in usadas]
    if not duoke:
        return []
    cartoes = await _cartoes(session, da_loja, ini, fim)
    conversa_ids = sorted({cid for cid, _ in duoke}, key=str)
    conversas = {
        c.id: c
        for c in (await session.execute(select(_C).where(_C.id.in_(conversa_ids)))).scalars().all()
    }
    do_comprador = await _pedidos_do_comprador(session, conversas)
    # Os pedidos de cada conversa pelos cartões (do comprador ou da campanha) dos
    # últimos 45 dias: o pós do Duoke não traz cartão, mas a conversa dele é a
    # que recebeu o "pedido recebido"/"entregue" daquele pedido.
    sn = _M.payload[("content", "order_sn")].astext
    for cid, pedido in (
        await session.execute(
            select(_M.conversa_id, sn).where(
                _M.conversa_id.in_(conversa_ids),
                _M.payload["message_type"].astext == "order",
                _M.enviada_em >= agora - timedelta(days=45),
            )
        )
    ).all():
        if pedido:
            do_comprador.setdefault(cid, set()).add(pedido.strip())
    candidatas: list[_Candidata] = []
    for cid, m in duoke:
        pelo_cartao = {sn for em, sn in cartoes.get(cid, []) if abs(em - m.em) <= CARTAO_JUNTO}
        outros = set(do_comprador.get(cid, set()))
        c = conversas.get(cid)
        if c is not None and c.pedido_marketplace:
            outros.add(c.pedido_marketplace)
        candidatas.append((m, c.integration_id if c is not None else None, pelo_cartao, outros))
    return candidatas


def _do_pedido(x: AtendimentoAutomacaoRegistro, aut: cat.Automacao, candidata: _Candidata) -> bool:
    """A mensagem do Duoke é desta automação e deste pedido? (O cartão; sem ele, a conversa.)"""
    m, integ, cartao, outros = candidata
    return (
        integ == x.integration_id
        and _casa(aut, m)
        and (x.pedido in cartao or (not cartao and x.pedido in outros))
    )


async def _comparar_pedidos(
    session: AsyncSession,
    linhas: list[AtendimentoAutomacaoRegistro],
    *,
    agora: datetime,
    ultimo_ok: dict,
) -> tuple[int, int]:
    """As linhas do pedido: pelo cartão do Duoke, pelo pedido da conversa ou pelo comprador."""
    if not linhas:
        return 0, 0
    janelas = {
        x.id: cat.janela_comparacao(cat.CATALOGO[x.automacao], x.evento_em, x.devido_em)
        for x in linhas
    }
    ini = min(j[0] for j in janelas.values())
    fim = min(max(j[1] for j in janelas.values()), agora)
    integs = sorted({x.integration_id for x in linhas}, key=str)
    candidatas = await _candidatas_de_pedido(
        session, integs, ini, fim, agora=agora, excluir_usadas=True
    )
    saidas = await _saidas(session, linhas)
    usadas_agora: set = set()
    mandou = nao = 0
    for x in sorted(linhas, key=lambda y: y.devido_em):
        aut = cat.CATALOGO[x.automacao]
        a, b = janelas[x.id]
        achadas = [
            (0 if x.pedido in c[2] else 1, c[0])
            for c in candidatas
            if c[0].id not in usadas_agora and a <= c[0].em <= b and _do_pedido(x, aut, c)
        ]
        if achadas:
            _, m = min(
                achadas,
                key=lambda par: (par[0], abs((par[1].em - _utc(x.devido_em)).total_seconds())),
            )
            usadas_agora.add(m.id)
            _marcar_mandou(x, m, agora, saidas.get(x.id))
            mandou += 1
            continue
        lido = ultimo_ok.get((x.integration_id, aut.canal))
        if agora > b and lido is not None and lido > b:
            x.duoke = cat.DUOKE_NAO_MANDOU
            x.comparado_em = agora
            nao += 1
    return mandou, nao


async def chats_de_avaliacao(
    session: AsyncSession, conversa_ids: list[UUID], ini: datetime, fim: datetime
) -> dict[UUID, list[tuple[UUID, datetime, str]]]:
    """A mensagem do CHAT que o Duoke manda junto com a resposta da avaliação.

    Conversa → [(mensagem, hora, tipo)], só de FORA (sem a marca do motor), pelas
    duas frases do modelo (`automacoes_catalogo.assinatura_avaliacao`).
    """
    if not conversa_ids:
        return {}
    minusculo = func.lower(func.left(_M.texto, 300))
    linhas = (
        await session.execute(
            select(_M.id, _M.conversa_id, _momento(), func.left(_M.texto, 600)).where(
                _M.conversa_id.in_(sorted(set(conversa_ids), key=str)),
                _de_fora(),
                _no_intervalo(ini, fim),
                or_(
                    minusculo.like(_trecho_like("obrigado pela confianca")),
                    minusculo.like(_trecho_like("sentimos muito pela experiencia")),
                ),
            )
        )
    ).all()
    saida: dict[UUID, list[tuple[UUID, datetime, str]]] = defaultdict(list)
    for mid, cid, em, texto in linhas:
        tipo = cat.assinatura_avaliacao(texto)
        if tipo:
            saida[cid].append((mid, _utc(em), tipo))
    return saida


def hora_da_resposta_publica(resposta_em: datetime | None) -> datetime | None:
    """A hora REAL da resposta pública: o `resposta_em` da Shopee menos os 59 min a mais."""
    em = _utc(resposta_em)
    return em - cat.ATRASO_RESPOSTA_EM_SHOPEE if em is not None else None


def _lida_em(av: Any) -> datetime | None:
    """Quando a leitura das avaliações viu esta avaliação pela última vez."""
    dados = av.dados if isinstance(getattr(av, "dados", None), dict) else {}
    conferida = None
    try:
        conferida = _utc(
            datetime.fromisoformat(str(dados.get("conferida_em")).replace("Z", "+00:00"))
        )
    except (TypeError, ValueError):
        conferida = None
    candidatas = [t for t in (_utc(getattr(av, "atualizado_em", None)), conferida) if t is not None]
    return max(candidatas) if candidatas else None


async def _comparar_avaliacoes(
    session: AsyncSession, linhas: list[AtendimentoAutomacaoRegistro], *, agora: datetime
) -> tuple[int, int]:
    """As respostas de avaliação: pela resposta PÚBLICA que a avaliação já tem.

    O Duoke responde na própria Shopee (`atendimento_avaliacoes_loja.
    resposta_loja`, lida a cada 30 min) e manda uma mensagem no chat junto.
    Casou o modelo dele → `mandou`, com a hora do chat (achado na conversa do
    comprador) ou o `resposta_em` menos os 59 min que a Shopee grava a mais. Sem
    a resposta do Duoke e com a avaliação relida depois do fim da janela (ou
    respondida por pessoa) → `nao_mandou`; senão continua pendente.
    """
    if not linhas:
        return 0, 0
    from app.services.atendimento.automacoes import avaliacoes_das_linhas

    avaliacoes = await avaliacoes_das_linhas(session, linhas)
    janelas = {
        x.id: cat.janela_comparacao(cat.CATALOGO[x.automacao], x.evento_em, x.devido_em)
        for x in linhas
    }
    ini = min(j[0] for j in janelas.values())
    fim = min(max(j[1] for j in janelas.values()), agora)
    chats = await chats_de_avaliacao(
        session, [x.conversa_id for x in linhas if x.conversa_id], ini, fim
    )
    usadas = await _usadas(session, [mid for lista in chats.values() for mid, _, _ in lista])
    saidas = await _saidas(session, linhas)
    mandou = nao = 0
    for x in sorted(linhas, key=lambda y: y.devido_em):
        aut = cat.CATALOGO[x.automacao]
        av = avaliacoes.get(x.id)
        if av is None:
            continue
        a, b = janelas[x.id]
        resposta = (av.resposta_loja or "").strip()
        tipo = cat.assinatura_avaliacao(resposta) if resposta else None
        chat = next(
            (
                (mid, em)
                for mid, em, t in chats.get(x.conversa_id, [])
                if t == aut.tipo and a <= em <= b and mid not in usadas
            ),
            None,
        )
        if tipo == aut.tipo or chat is not None:
            em = chat[1] if chat is not None else hora_da_resposta_publica(av.resposta_em)
            x.duoke = cat.DUOKE_MANDOU
            x.duoke_mensagem_id = chat[0] if chat is not None else None
            x.duoke_em = em
            base = saidas.get(x.id) or _utc(x.devido_em)
            x.duoke_diferenca_s = int((em - base).total_seconds()) if em is not None else None
            x.comparado_em = agora
            if chat is not None:
                usadas.add(chat[0])
            mandou += 1
            continue
        lida = _lida_em(av)
        if agora > b and ((lida is not None and lida > b) or (resposta and tipo is None)):
            x.duoke = cat.DUOKE_NAO_MANDOU
            x.comparado_em = agora
            nao += 1
    return mandou, nao


async def duoke_dos_pedidos(
    session: AsyncSession, linhas: list[AtendimentoAutomacaoRegistro], *, agora: datetime
) -> dict[UUID, list[datetime]]:
    """Modo enviar: o Duoke já mandou ESTA automação para ESTE pedido depois do gatilho?

    A mesma régua do comparador (`_comparar_pedidos`): o modelo pela assinatura,
    o pedido pelo cartão do Duoke a até 10 s do texto ou, sem cartão, pela
    conversa. Conta qualquer mensagem do Duoke, mesmo a que o comparador já
    casou com outra linha: o comprador já recebeu. Linha → os horários.
    """
    alvo = [x for x in linhas if x.pedido and x.evento_em is not None]
    if not alvo:
        return {}
    ini = min(_utc(x.evento_em) for x in alvo)
    integs = sorted({x.integration_id for x in alvo}, key=str)
    candidatas = await _candidatas_de_pedido(
        session, integs, ini, agora, agora=agora, excluir_usadas=False
    )
    saida: dict[UUID, list[datetime]] = {}
    for x in alvo:
        aut = cat.CATALOGO.get(x.automacao)
        if aut is None:
            continue
        evento = _utc(x.evento_em)
        saida[x.id] = sorted(
            c[0].em for c in candidatas if c[0].em >= evento and _do_pedido(x, aut, c)
        )
    return saida


async def _alertas_e_divergencias(
    session: AsyncSession, linhas: list[AtendimentoAutomacaoRegistro]
) -> None:
    """Só DaVinci para quem devolveu/cancelou (trava a troca) e as diferenças do entregue."""
    alvo = [x for x in linhas if x.duoke in (cat.DUOKE_NAO_MANDOU, cat.DUOKE_MANDOU)]
    pedidos = sorted({x.pedido for x in alvo if x.pedido})
    conversas = sorted({x.conversa_id for x in alvo if x.conversa_id}, key=str)
    cancelados: set[str] = set()
    devolvidos: set[str] = set()
    concluido_em: dict[str, datetime] = {}
    conversas_recl: set[UUID] = set()
    if pedidos:
        for sn, situacao in (
            await session.execute(
                select(BlingOrder.numeroloja, BlingOrder.situacao).where(
                    BlingOrder.numeroloja.in_(pedidos),
                    BlingOrder.situacao.in_(("12", "excluido", *_SITUACOES_DEVOLUCAO_BLING)),
                )
            )
        ).all():
            # Bling no fluxo de devolução (Aguardando Devolução, Resolvido...)
            # também é "devolveu" — o mesmo do motor.
            (devolvidos if situacao in _SITUACOES_DEVOLUCAO_BLING else cancelados).add(sn)
        for sn, status, retorno, em in (
            await session.execute(
                select(
                    Logistica.pedido_marketplace,
                    Logistica.meli_status["order_status"].astext,
                    Logistica.meli_status["return_status"].astext,
                    Logistica.status_datas[("order_status", "em")].astext,
                ).where(Logistica.pedido_marketplace.in_(pedidos))
            )
        ).all():
            if retorno or status == "TO_RETURN":
                devolvidos.add(sn)
            if status == "COMPLETED" and em:
                try:
                    concluido_em[sn] = _utc(datetime.fromisoformat(em.replace("Z", "+00:00")))
                except ValueError:
                    pass
    filtro = []
    if pedidos:
        filtro.append(AtendimentoReclamacao.pedido_marketplace.in_(pedidos))
    if conversas:
        filtro.append(AtendimentoReclamacao.conversa_id.in_(conversas))
    if filtro:
        for sn, cid in (
            await session.execute(
                select(
                    AtendimentoReclamacao.pedido_marketplace, AtendimentoReclamacao.conversa_id
                ).where(or_(*filtro))
            )
        ).all():
            if sn:
                devolvidos.add(sn)
            if cid:
                conversas_recl.add(cid)
    for x in alvo:
        aut = cat.CATALOGO[x.automacao]
        so_davinci = x.estado in _ESTADOS_QUE_MANDAM and x.duoke == cat.DUOKE_NAO_MANDOU
        if so_davinci:
            if x.pedido and x.pedido in cancelados:
                x.alerta = "cancelado"
            elif x.pedido and x.pedido in devolvidos:
                x.alerta = "devolucao"
            elif aut.campanha and x.conversa_id in conversas_recl:
                x.alerta = "reclamacao"
        if (
            aut.tipo == cat.TIPO_NAO_PAGO
            and x.divergencia is None
            and x.estado == cat.ESTADO_PULADO
            and x.duoke == cat.DUOKE_MANDOU
            and x.motivo in ("pedido_pago", "status_mudou", "pedido_cancelado")
        ):
            # O Duoke mandou aos 30 min, com o pedido ainda não pago; quando o
            # DaVinci o viu (o índice é de hora em hora), já tinha pago ou cancelado.
            x.divergencia = "visto_de_hora_em_hora"
        if aut.tipo == cat.TIPO_ENTREGUE and x.pedido in concluido_em and x.divergencia is None:
            concluiu = concluido_em[x.pedido]
            if so_davinci and concluiu > _utc(x.devido_em):
                # Concluiu depois do nosso horário e antes do lote do Duoke.
                x.divergencia = "concluiu_entre_horarios"
            elif (
                x.estado == cat.ESTADO_PULADO
                and x.motivo == "ja_concluido"
                and x.duoke == cat.DUOKE_MANDOU
                and x.duoke_em is not None
                and concluiu > _utc(x.duoke_em)
            ):
                x.divergencia = "concluiu_entre_horarios"


async def carrinhos_vistos_tarde(
    session: AsyncSession,
    carrinhos: list[tuple[cat.Msg, AtendimentoConversa, str | None]],
) -> tuple[dict[UUID, str], set[UUID]]:
    """Os "carrinhos" do Duoke sem par: (mensagem → pedido, as vistas tarde).

    `carrinhos` é [(mensagem do Duoke, conversa, pedido do cartão ou None)]. O
    pedido é o do cartão; sem cartão (o 2º carrinho da mesma conversa vem sem),
    o do comprador da conversa no índice criado logo antes (até
    `atraso_max_duoke`). É a diferença combinada `visto_de_hora_em_hora` SÓ
    quando o DaVinci nunca o viu não pago por causa do índice de hora em hora:

      • o pedido foi criado logo antes do carrinho (o Duoke sai aos 30 min) —
        não um pedido antigo da conversa;
      • o DaVinci não tem linha dele (se tivesse, o teria visto não pago);
      • ele ficou pago (no Bling) até `VISTO_PELO_INDICE` depois da criação, ou
        está cancelado no índice — mudou antes de o índice olhar.

    Fora disso, o "só Duoke" conta (é falha de verdade).
    """
    aut = cat.CATALOGO.get("shopee_nao_pago")
    if not carrinhos or aut is None:
        return {}, set()
    antes = aut.atraso_max_duoke
    P = AtendimentoPedidoComprador  # noqa: N806
    integs = sorted({c.integration_id for _, c, _ in carrinhos}, key=str)
    sns = sorted({sn for _, _, sn in carrinhos if sn})
    compradores = sorted({c.comprador_id for _, c, sn in carrinhos if not sn and c.comprador_id})
    ini = min(_utc(m.em) for m, _, _ in carrinhos) - antes
    fim = max(_utc(m.em) for m, _, _ in carrinhos)
    filtros = []
    if sns:
        filtros.append(P.pedido.in_(sns))
    if compradores:
        filtros.append(
            and_(P.comprador_id.in_(compradores), P.criado_em >= ini, P.criado_em <= fim)
        )
    if not filtros:
        return {}, set()
    indice = (
        await session.execute(
            select(P.integration_id, P.pedido, P.comprador_id, P.criado_em, P.status).where(
                P.integration_id.in_(integs), or_(*filtros)
            )
        )
    ).all()
    por_pedido = {(r.integration_id, r.pedido): r for r in indice}
    por_comprador: dict[tuple[UUID, str], list[Any]] = defaultdict(list)
    for r in indice:
        if r.comprador_id and r.criado_em is not None:
            por_comprador[(r.integration_id, r.comprador_id)].append(r)
    pedidos: dict[UUID, str] = {}
    for m, c, sn in carrinhos:
        em = _utc(m.em)
        if sn:
            pedidos[m.id] = sn
            continue
        logo_antes = [
            r
            for r in por_comprador.get((c.integration_id, c.comprador_id or ""), [])
            if em - antes <= _utc(r.criado_em) <= em
        ]
        if logo_antes:
            pedidos[m.id] = max(logo_antes, key=lambda r: _utc(r.criado_em)).pedido
    escolhidos = sorted(set(pedidos.values()))
    if not escolhidos:
        return pedidos, set()
    pago_em = {
        sn: _utc(em)
        for sn, em in (
            await session.execute(
                select(BlingOrder.numeroloja, func.min(BlingOrder.created_at))
                .where(
                    BlingOrder.numeroloja.in_(escolhidos),
                    BlingOrder.situacao.not_in(("12", "excluido")),
                )
                .group_by(BlingOrder.numeroloja)
            )
        ).all()
    }
    com_linha = set(
        (
            await session.execute(
                select(_R.integration_id, _R.pedido).where(
                    _R.automacao == aut.codigo,
                    _R.integration_id.in_(integs),
                    _R.pedido.in_(escolhidos),
                    _R.estado != cat.ESTADO_SO_DUOKE,
                )
            )
        ).all()
    )
    vistos: set[UUID] = set()
    for m, c, _ in carrinhos:
        sn = pedidos.get(m.id)
        r = por_pedido.get((c.integration_id, sn)) if sn else None
        if r is None or r.criado_em is None or (c.integration_id, sn) in com_linha:
            continue
        criado = _utc(r.criado_em)
        if not (_utc(m.em) - antes <= criado <= _utc(m.em)):
            continue  # um pedido antigo da conversa: não é deste carrinho
        pago = pago_em.get(sn)
        if (pago is not None and pago <= criado + VISTO_PELO_INDICE) or r.status in (
            "CANCELLED",
            "IN_CANCEL",
        ):
            vistos.add(m.id)
    return pedidos, vistos


async def _so_duoke(
    session: AsyncSession,
    *,
    agora: datetime,
    motor_desde: datetime,
    regras: dict[tuple[str, UUID], AtendimentoAutomacaoRegra],
) -> int:
    """A mensagem do Duoke que nenhuma linha usou vira `so_duoke` (o falso negativo do DaVinci)."""
    ativas = {k: r for k, r in regras.items() if r.modo != cat.MODO_DESLIGADO}
    if not ativas:
        return 0
    integs = sorted({i for _, i in ativas}, key=str)
    ini = max(agora - SO_DUOKE_JANELA, motor_desde)
    fim = agora - SO_DUOKE_ESPERA
    if fim <= ini:
        return 0
    da_loja = _M.conversa_id.in_(select(_C.id).where(_C.integration_id.in_(integs)))
    duoke = await _mensagens_do_duoke(session, da_loja, ini, fim)
    usadas = await _usadas(session, [m.id for _, m in duoke])
    duoke = [(cid, m) for cid, m in duoke if m.id not in usadas]
    if not duoke:
        return 0
    conversa_ids = sorted({cid for cid, _ in duoke}, key=str)
    conversas = {
        c.id: c
        for c in (await session.execute(select(_C).where(_C.id.in_(conversa_ids)))).scalars().all()
    }
    # A resposta de opção que o Duoke REPETE (a cada 12 h, enquanto o comprador
    # escreve): a anterior, do Duoke, nas 36 h antes.
    anteriores = []
    if any(m.tipo_duoke == cat.TIPO_OPCAO for _, m in duoke):
        anteriores = [
            (cid, m)
            for cid, m in await _mensagens_do_duoke(
                session, _M.conversa_id.in_(conversa_ids), ini - OPCAO_REPETIDA, fim
            )
            if m.tipo_duoke == cat.TIPO_OPCAO
        ]
    cartoes = await _cartoes(session, _M.conversa_id.in_(conversa_ids), ini, fim)
    # Mensagem do comprador logo antes (o menu "de fim de sessão" não tem).
    momento = _momento()
    compradores: dict[UUID, list[datetime]] = defaultdict(list)
    for cid, em in (
        await session.execute(
            select(_M.conversa_id, momento).where(
                _M.conversa_id.in_(conversa_ids),
                _M.autor == AUTOR_CLIENTE,
                _no_intervalo(ini - timedelta(hours=1), fim),
            )
        )
    ).all():
        compradores[cid].append(_utc(em))
    # O pedido que o DaVinci VÊ entregar/concluir: o da Logística e — fora
    # dela — o do índice com o status (`automacoes.descobrir_indice`).
    com_logistica: set[str] = set()
    sns = {sn for lista in cartoes.values() for _, sn in lista}
    sns |= {c.pedido_marketplace for c in conversas.values() if c.pedido_marketplace}
    if sns:
        com_logistica = set(
            (
                await session.execute(
                    select(Logistica.pedido_marketplace).where(
                        Logistica.pedido_marketplace.in_(sorted(sns))
                    )
                )
            )
            .scalars()
            .all()
        )
        com_logistica |= set(
            (
                await session.execute(
                    select(AtendimentoPedidoComprador.pedido).where(
                        AtendimentoPedidoComprador.pedido.in_(sorted(sns)),
                        AtendimentoPedidoComprador.status.in_(("TO_CONFIRM_RECEIVE", "COMPLETED")),
                    )
                )
            )
            .scalars()
            .all()
        )
    # O "carrinho" do Duoke sem par: o pedido dele (o do cartão; sem cartão, o
    # do comprador criado logo antes) e se é a diferença combinada
    # `visto_de_hora_em_hora` (o DaVinci nunca o viu não pago).
    carrinhos = []
    for cid, m in duoke:
        c = conversas.get(cid)
        if m.tipo_duoke == cat.TIPO_NAO_PAGO and c is not None and c.integration_id is not None:
            cartao = next(
                (sn for em, sn in cartoes.get(cid, []) if abs(em - m.em) <= CARTAO_JUNTO), None
            )
            carrinhos.append((m, c, cartao))
    pedido_do_carrinho, vistos_tarde = await carrinhos_vistos_tarde(session, carrinhos)
    novas = []
    for cid, m in duoke:
        c = conversas.get(cid)
        if c is None or c.integration_id is None:
            continue
        codigo = cat.automacao_do_modelo(m.tipo_duoke, m.opcao_duoke, c.plataforma, c.canal)
        regra = ativas.get((codigo, c.integration_id)) if codigo else None
        if regra is None:
            continue
        aut = cat.CATALOGO[codigo]
        ligada = _utc(regra.ligada_desde) or motor_desde
        if m.em < max(ligada, motor_desde) + aut.atraso_max_duoke:
            continue  # o gatilho pode ser de antes de o motor/regra estar ligado
        if m.em > agora - aut.so_duoke_espera:
            continue  # a nossa linha ainda pode ser decidida e casar com ela
        pedido = next(
            (sn for em, sn in cartoes.get(cid, []) if abs(em - m.em) <= CARTAO_JUNTO), None
        )
        pedido = (
            pedido
            or pedido_do_carrinho.get(m.id)
            or (c.pedido_marketplace if aut.alvo == cat.ALVO_PEDIDO else None)
        )
        divergencia = None
        if aut.tipo == cat.TIPO_OPCAO and any(
            om.em < m.em <= om.em + OPCAO_REPETIDA
            for ocid, om in anteriores
            if ocid == cid and om.opcao_duoke == m.opcao_duoke and om.id != m.id
        ):
            divergencia = "opcao_repetida"
        elif aut.tipo == cat.TIPO_MENU and not any(
            m.em - MENU_SEM_COMPRADOR <= t <= m.em for t in compradores.get(cid, [])
        ):
            divergencia = "menu_fim_de_sessao"
        elif (
            aut.tipo in (cat.TIPO_ENTREGUE, cat.TIPO_POS) and pedido and pedido not in com_logistica
        ):
            divergencia = "sem_logistica"
        elif aut.tipo == cat.TIPO_NAO_PAGO and m.id in vistos_tarde:
            divergencia = "visto_de_hora_em_hora"
        novas.append(
            {
                "id": uuid4(),
                "automacao": codigo,
                "regra_id": regra.id,
                "regra_versao": regra.versao,
                "integration_id": c.integration_id,
                "plataforma": c.plataforma,
                "alvo": cat.ALVO_DUOKE,
                "chave": f"duoke:{m.id}",
                "conversa_id": cid,
                "pedido": pedido,
                "comprador_id": c.comprador_id,
                "evento_em": m.em,
                "visto_em": agora,
                "devido_em": m.em,
                "decidido_em": agora,
                "estado": cat.ESTADO_SO_DUOKE,
                "modo": regra.modo,
                "duoke": cat.DUOKE_MANDOU,
                "duoke_mensagem_id": m.id,
                "duoke_em": m.em,
                "duoke_diferenca_s": 0,
                "divergencia": divergencia,
                "comparado_em": agora,
            }
        )
    criadas = 0
    for i in range(0, len(novas), LOTE_INSERT):
        resultado = await session.execute(
            pg_insert(_R)
            .values(novas[i : i + LOTE_INSERT])
            .on_conflict_do_nothing()
            .returning(_R.id)
        )
        criadas += len(resultado.all())
    return criadas


async def _so_duoke_avaliacoes(
    session: AsyncSession,
    *,
    agora: datetime,
    motor_desde: datetime,
    regras: dict[tuple[str, UUID], AtendimentoAutomacaoRegra],
) -> int:
    """A avaliação que o Duoke respondeu (o modelo dele) sem linha nossa vira `so_duoke`.

    Só as avaliações feitas depois de a regra e o motor estarem ligados, e só
    depois de a resposta ter `so_duoke_espera` (a nossa linha pode chegar com a
    leitura das avaliações). A chave é `duoke:avaliacao:<comentario>`.
    """
    ativas = {
        k: r
        for k, r in regras.items()
        if r.modo != cat.MODO_DESLIGADO and cat.CATALOGO[k[0]].alvo == cat.ALVO_AVALIACAO
    }
    if not ativas:
        return 0
    por_tipo = {cat.CATALOGO[codigo].tipo: codigo for codigo, _ in ativas if codigo in cat.CATALOGO}
    integs = sorted({i for _, i in ativas}, key=str)
    A = AtendimentoAvaliacaoLoja  # noqa: N806
    linhas = (
        await session.execute(
            select(
                A.integration_id,
                A.comentario_id,
                A.pedido,
                A.comprador_id,
                A.resposta_loja,
                A.resposta_em,
                A.criado_em,
            ).where(
                A.integration_id.in_(integs),
                A.plataforma == "shopee",
                A.resposta_loja.is_not(None),
                A.criado_em >= max(agora - SO_DUOKE_JANELA, motor_desde),
                A.criado_em <= agora,
            )
        )
    ).all()
    candidatas = []
    for r in linhas:
        codigo = por_tipo.get(cat.assinatura_avaliacao(r.resposta_loja) or "")
        regra = ativas.get((codigo, r.integration_id)) if codigo else None
        if regra is None:
            continue
        aut = cat.CATALOGO[codigo]
        ligada = max(_utc(regra.ligada_desde) or motor_desde, motor_desde)
        duoke_em = hora_da_resposta_publica(r.resposta_em)
        if _utc(r.criado_em) < ligada or duoke_em is None:
            continue
        if duoke_em > agora - aut.so_duoke_espera:
            continue  # a nossa linha ainda pode chegar (a leitura das avaliações)
        candidatas.append((codigo, regra, r, duoke_em))
    if not candidatas:
        return 0
    chaves = {f"{cat.ALVO_AVALIACAO}:{r.comentario_id}" for _, _, r, _ in candidatas}
    nossas = set(
        (
            await session.execute(
                select(_R.integration_id, _R.chave).where(
                    _R.integration_id.in_(integs), _R.chave.in_(sorted(chaves))
                )
            )
        ).all()
    )
    novas = []
    for codigo, regra, r, duoke_em in candidatas:
        if (r.integration_id, f"{cat.ALVO_AVALIACAO}:{r.comentario_id}") in nossas:
            continue
        novas.append(
            {
                "id": uuid4(),
                "automacao": codigo,
                "regra_id": regra.id,
                "regra_versao": regra.versao,
                "integration_id": r.integration_id,
                "plataforma": "shopee",
                "alvo": cat.ALVO_DUOKE,
                "chave": f"duoke:{cat.ALVO_AVALIACAO}:{r.comentario_id}",
                "pedido": r.pedido,
                "comprador_id": r.comprador_id,
                "evento_em": _utc(r.criado_em),
                "visto_em": agora,
                "devido_em": duoke_em,
                "decidido_em": agora,
                "estado": cat.ESTADO_SO_DUOKE,
                "modo": regra.modo,
                "duoke": cat.DUOKE_MANDOU,
                "duoke_em": duoke_em,
                "duoke_diferenca_s": 0,
                "comparado_em": agora,
            }
        )
    criadas = 0
    for i in range(0, len(novas), LOTE_INSERT):
        resultado = await session.execute(
            pg_insert(_R)
            .values(novas[i : i + LOTE_INSERT])
            .on_conflict_do_nothing()
            .returning(_R.id)
        )
        criadas += len(resultado.all())
    return criadas


async def _disjuntor(
    session: AsyncSession,
    *,
    agora: datetime,
    regras: dict[tuple[str, UUID], AtendimentoAutomacaoRegra],
) -> int:
    """O Duoke mandou DEPOIS de a regra ir para `enviar`: volta para `simular`.

    É o "Duoke ainda ligado?": o comprador recebeu duas.
    """
    from app.services.atendimento.automacoes import disparar_disjuntor

    em_enviar = [
        r for r in regras.values() if r.modo == cat.MODO_ENVIAR and r.enviar_desde is not None
    ]
    disparados = 0
    for regra in em_enviar:
        achou = await session.scalar(
            select(_R.id)
            .where(
                _R.automacao == regra.automacao,
                _R.integration_id == regra.integration_id,
                _R.duoke == cat.DUOKE_MANDOU,
                _R.duoke_em >= regra.enviar_desde,
            )
            .limit(1)
        )
        if achou is not None:
            await disparar_disjuntor(session, regra, agora=agora, motivo="duoke_ainda_ligado")
            disparados += 1
    return disparados


async def comparar(
    session: AsyncSession, *, agora: datetime, motor_desde: datetime
) -> dict[str, int]:
    """Uma passada do comparador. Não commita (a rodada commita)."""
    from app.services.atendimento.automacoes import regras_por_chave

    agora = _utc(agora)
    linhas = list(
        (
            await session.execute(
                select(_R)
                .where(
                    _R.duoke == cat.DUOKE_PENDENTE,
                    _R.estado.in_(_ESTADOS_DECIDIDOS),
                    _R.devido_em >= agora - timedelta(days=4),
                )
                .order_by(_R.devido_em)
                .limit(MAX_PENDENTES)
            )
        )
        .scalars()
        .all()
    )
    abertas = []
    for x in linhas:
        aut = cat.CATALOGO.get(x.automacao)
        if aut is None:
            x.duoke = cat.DUOKE_NAO_SE_APLICA
            continue
        ini, _ = cat.janela_comparacao(aut, x.evento_em, x.devido_em)
        if ini <= agora:
            abertas.append(x)
    ultimo_ok = await _ultimo_ok(session)
    # Na conversa: as que respondem conversa e as do pedido que a TikTok não
    # numera (`compara_na_conversa`); pelo pedido: as do pedido da Shopee; pela
    # avaliação: a resposta da avaliação.
    de_conversa = [x for x in abertas if x.conversa_id and cat.CATALOGO[x.automacao].na_conversa]
    de_pedido = [
        x
        for x in abertas
        if cat.CATALOGO[x.automacao].alvo == cat.ALVO_PEDIDO
        and not cat.CATALOGO[x.automacao].na_conversa
        and x.pedido
    ]
    de_avaliacao = [x for x in abertas if cat.CATALOGO[x.automacao].alvo == cat.ALVO_AVALIACAO]
    m1, n1 = await _comparar_conversas(session, de_conversa, agora=agora, ultimo_ok=ultimo_ok)
    m2, n2 = await _comparar_pedidos(session, de_pedido, agora=agora, ultimo_ok=ultimo_ok)
    m3, n3 = await _comparar_avaliacoes(session, de_avaliacao, agora=agora)
    await _alertas_e_divergencias(session, de_conversa + de_pedido + de_avaliacao)
    await session.flush()
    regras = await regras_por_chave(session, ativas=False)
    so = await _so_duoke(session, agora=agora, motor_desde=_utc(motor_desde), regras=regras)
    so += await _so_duoke_avaliacoes(
        session, agora=agora, motor_desde=_utc(motor_desde), regras=regras
    )
    disjuntor = await _disjuntor(session, agora=agora, regras=regras)
    return {
        "mandou": m1 + m2 + m3,
        "nao_mandou": n1 + n2 + n3,
        "so_duoke": so,
        "disjuntor": disjuntor,
    }


# ── A conta da tela ───────────────────────────────────────────────────────


def _pct(a: int, b: int) -> float | None:
    return round(a / b, 4) if b else None


def resumir(contagem: dict[str, Any], aut: cat.Automacao | None) -> dict[str, Any]:
    """Precisão, cobertura, % que bateu e o critério da troca, a partir das contagens. PURA.

    Precisão = o DaVinci mandaria e o Duoke mandou (sem "só DaVinci");
    cobertura = o Duoke mandou e o DaVinci mandaria (sem "só Duoke"). As
    duas ≥ 95%, separadas — uma % única deixaria passar um erro de 2% a 5%
    justo nos piores casos. As combinadas ficam fora. Nas OPÇÕES a precisão
    não se mede pelo Duoke (ele responde 12 h depois e só se ninguém
    respondeu): vale a cobertura e o texto passando no validador.
    """
    bm = int(contagem.get("bateu_mandou", 0))
    bn = int(contagem.get("bateu_nao_mandou", 0))
    sdv = int(contagem.get("so_davinci", 0))
    sdk = int(contagem.get("so_duoke", 0))
    total = bm + bn + sdv + sdk
    opcao = aut is not None and aut.tipo == cat.TIPO_OPCAO
    precisao = None if opcao else _pct(bm, bm + sdv)
    cobertura = _pct(bm, bm + sdk)
    concordancia = None if opcao else _pct(bm + bn, total)
    motivos = []
    if total < CRITERIO_CASOS:
        motivos.append(f"poucos casos ({total} de {CRITERIO_CASOS})")
    if not opcao:
        if precisao is None or precisao < CRITERIO_MINIMO:
            motivos.append("precisão abaixo de 95%")
        if concordancia is None or concordancia < CRITERIO_MINIMO:
            motivos.append("concordância abaixo de 95%")
    if cobertura is None or cobertura < CRITERIO_MINIMO:
        motivos.append("cobertura abaixo de 95%")
    if int(contagem.get("alertas", 0)):
        motivos.append("mandaria para quem devolveu, cancelou ou reclamou")
    if int(contagem.get("texto_invalido", 0)):
        motivos.append("texto que não passa no validador")
    if int(contagem.get("so_davinci_2d", 0)) and not opcao:
        motivos.append('"só DaVinci" nos últimos 2 dias')
    return {
        "casos": total,
        "precisao": precisao,
        "cobertura": cobertura,
        "concordancia": concordancia,
        "pode_trocar": not motivos,
        "por_que_nao": motivos,
    }


def dados_desde(
    *,
    modo: str | None,
    ligada_desde: datetime | None,
    enviar_desde: datetime | None,
    primeira_linha: datetime | None,
) -> datetime | None:
    """Desde quando a regra da loja tem dados no modo de agora. PURA.

    O mais tarde entre a regra ligada (`ligada_desde`; em `enviar`, também
    `enviar_desde`) e a primeira linha do registro dela (pela `devido_em`, a
    mesma da conta): ligada com o motor parado não tem dado nenhum, e a regra
    religada não herda a semana de antes. Desligada, sem regra ou sem linha
    nenhuma: None.
    """
    if modo in (None, cat.MODO_DESLIGADO) or primeira_linha is None:
        return None
    marcos = [primeira_linha, ligada_desde]
    if modo == cat.MODO_ENVIAR:
        marcos.append(enviar_desde)
    return max(_utc(x) for x in marcos if x is not None)


def _horas(h: int) -> str:
    return f"{h} h"


def _dias(d: int) -> str:
    return f"{d} dia" if d == 1 else f"{d} dias"


def texto_falta(faltam_h: int) -> str:
    """O que falta: "faltam 6 dias", "falta 1 dia", "faltam 10 h" (como a tela mostra). PURA."""
    if faltam_h < 24:
        return f"falta{'' if faltam_h == 1 else 'm'} {_horas(faltam_h)}"
    d = max(1, int(faltam_h / 24 + 0.5))
    return f"falta{'' if d == 1 else 'm'} {_dias(d)}"


def com_os_dias(
    criterio: dict[str, Any], *, desde: datetime | None, agora: datetime
) -> dict[str, Any]:
    """O critério da troca com os `CRITERIO_DIAS` dias de dados da regra/loja. PURA.

    Acrescenta `dados_desde`, `dias_de_dados`, `completa_em` e `faltam_h` (0 =
    já tem os 7 dias) e, faltando, o motivo à frente dos outros — `pode_trocar`
    só com os dias E os números (`resumir`).
    """
    agora = _utc(agora)
    saida = dict(criterio)
    exigido = timedelta(days=CRITERIO_DIAS)
    if desde is None:
        tem = timedelta(0)
        saida["completa_em"] = None
    else:
        desde = _utc(desde)
        tem = max(timedelta(0), agora - desde)
        saida["completa_em"] = desde + exigido
    falta = max(timedelta(0), exigido - tem)
    faltam_h = int(-(-falta.total_seconds() // 3600))
    saida["dados_desde"] = desde
    saida["dias_de_dados"] = round(tem.total_seconds() / 86400, 1)
    saida["faltam_h"] = faltam_h
    if faltam_h:
        if desde is None:
            motivo = f"sem dados ainda (o critério pede {_dias(CRITERIO_DIAS)})"
        else:
            horas = int(tem.total_seconds() // 3600)
            ja = _horas(horas) if horas < 24 else _dias(int(horas // 24))
            motivo = (
                f"{texto_falta(faltam_h)} de dados (o critério pede {_dias(CRITERIO_DIAS)}; "
                f"tem {ja})"
            )
        saida["por_que_nao"] = [motivo, *(saida.get("por_que_nao") or [])]
        saida["pode_trocar"] = False
    return saida


async def inicio_dos_dados(
    session: AsyncSession,
    *,
    automacao: str | None = None,
    integration_id: UUID | None = None,
) -> dict[tuple[str, UUID], datetime | None]:
    """(automação, loja) → `dados_desde` de cada regra que existe."""
    filtro_g, filtro_r = [], []
    if automacao:
        filtro_g.append(_G.automacao == automacao)
        filtro_r.append(_R.automacao == automacao)
    if integration_id:
        filtro_g.append(_G.integration_id == integration_id)
        filtro_r.append(_R.integration_id == integration_id)
    regras = (
        await session.execute(
            select(
                _G.automacao, _G.integration_id, _G.modo, _G.ligada_desde, _G.enviar_desde
            ).where(*filtro_g)
        )
    ).all()
    if not regras:
        return {}
    primeiras = {
        (codigo, integ): primeira
        for codigo, integ, primeira in (
            await session.execute(
                select(_R.automacao, _R.integration_id, func.min(_R.devido_em))
                .where(*filtro_r)
                .group_by(_R.automacao, _R.integration_id)
            )
        ).all()
    }
    return {
        (r.automacao, r.integration_id): dados_desde(
            modo=r.modo,
            ligada_desde=r.ligada_desde,
            enviar_desde=r.enviar_desde,
            primeira_linha=primeiras.get((r.automacao, r.integration_id)),
        )
        for r in regras
    }


async def estatisticas(
    session: AsyncSession,
    *,
    desde: datetime,
    ate: datetime,
    automacao: str | None = None,
    integration_id: UUID | None = None,
    inicios: dict[tuple[str, UUID], datetime | None] | None = None,
) -> dict[tuple[str, UUID], dict[str, Any]]:
    """As contagens por (automação, loja) no período (pela `devido_em`), com a conta pronta.

    Com `inicios` (`inicio_dos_dados`), o critério da troca de cada par também
    exige os 7 dias de dados até `ate` (`com_os_dias`).
    """
    mandam = _R.estado.in_(_ESTADOS_QUE_MANDAM)
    sem_div = _R.divergencia.is_(None)
    so_davinci = and_(mandam, _R.duoke == cat.DUOKE_NAO_MANDOU, sem_div)
    colunas = {
        "total": func.count(),
        "simulado": func.count().filter(_R.estado == cat.ESTADO_SIMULADO),
        "enviado": func.count().filter(_R.estado == cat.ESTADO_ENVIADO),
        "pulado": func.count().filter(_R.estado == cat.ESTADO_PULADO),
        "falhou": func.count().filter(_R.estado.in_((cat.ESTADO_FALHOU, cat.ESTADO_REVISAR))),
        "agendado": func.count().filter(_R.estado == cat.ESTADO_AGENDADO),
        "pendente": func.count().filter(
            _R.duoke == cat.DUOKE_PENDENTE, _R.estado != cat.ESTADO_AGENDADO
        ),
        "bateu_mandou": func.count().filter(mandam, _R.duoke == cat.DUOKE_MANDOU, sem_div),
        "bateu_nao_mandou": func.count().filter(
            _R.estado == cat.ESTADO_PULADO, _R.duoke == cat.DUOKE_NAO_MANDOU, sem_div
        ),
        "so_davinci": func.count().filter(so_davinci),
        "so_davinci_2d": func.count().filter(so_davinci, _R.devido_em >= ate - timedelta(days=2)),
        "so_duoke": func.count().filter(
            or_(
                and_(_R.estado == cat.ESTADO_PULADO, _R.duoke == cat.DUOKE_MANDOU),
                _R.estado == cat.ESTADO_SO_DUOKE,
            ),
            sem_div,
        ),
        "combinada": func.count().filter(_R.divergencia.is_not(None)),
        "alertas": func.count().filter(_R.alerta.is_not(None)),
        "texto_invalido": func.count().filter(_R.motivo == "texto_invalido"),
        "envio_desligado": func.count().filter(_R.motivo == "envio_desligado"),
    }
    filtro = [_R.devido_em >= desde, _R.devido_em <= ate]
    if automacao:
        filtro.append(_R.automacao == automacao)
    if integration_id:
        filtro.append(_R.integration_id == integration_id)
    linhas = (
        await session.execute(
            select(_R.automacao, _R.integration_id, *(c.label(k) for k, c in colunas.items()))
            .where(*filtro)
            .group_by(_R.automacao, _R.integration_id)
        )
    ).all()
    saida: dict[tuple[str, UUID], dict[str, Any]] = {}
    for r in linhas:
        contagem = {k: int(getattr(r, k) or 0) for k in colunas}
        saida[(r.automacao, r.integration_id)] = contagem
    if not saida:
        return saida
    # A diferença para o Duoke (Duoke − a nossa) e o atraso REAL do DaVinci
    # (do gatilho até a hora em que a nossa sai, ou sairia no modo seco).
    tempos = (
        await session.execute(
            select(
                _R.automacao,
                _R.integration_id,
                _R.duoke,
                _R.duoke_diferenca_s,
                _R.evento_em,
                _R.decidido_em,
            ).where(*filtro, mandam)
        )
    ).all()
    por_par: dict[tuple, list[int]] = defaultdict(list)
    atrasos: dict[tuple, list[int]] = defaultdict(list)
    for codigo, integ, duoke, d, evento, decidido in tempos:
        if duoke == cat.DUOKE_MANDOU and d is not None:
            por_par[(codigo, integ)].append(int(d))
        if evento is not None and decidido is not None:
            atrasos[(codigo, integ)].append(int((_utc(decidido) - _utc(evento)).total_seconds()))
    motivos = (
        await session.execute(
            select(_R.automacao, _R.integration_id, _R.motivo, func.count())
            .where(*filtro, _R.estado == cat.ESTADO_PULADO, _R.motivo.is_not(None))
            .group_by(_R.automacao, _R.integration_id, _R.motivo)
        )
    ).all()
    por_motivo: dict[tuple, dict[str, int]] = defaultdict(dict)
    for codigo, integ, motivo, n in motivos:
        por_motivo[(codigo, integ)][motivo] = int(n)
    for chave, contagem in saida.items():
        lista = por_par.get(chave, [])
        contagem["diferenca_mediana_s"] = int(median(lista)) if lista else None
        lista_atraso = atrasos.get(chave, [])
        contagem["atraso_mediana_s"] = int(median(lista_atraso)) if lista_atraso else None
        # Para a soma das lojas (`somar`) refazer as medianas; a rota não devolve.
        contagem["_diferencas"] = lista
        contagem["_atrasos"] = lista_atraso
        contagem["motivos"] = dict(sorted(por_motivo.get(chave, {}).items(), key=lambda kv: -kv[1]))
        contagem.update(resumir(contagem, cat.CATALOGO.get(chave[0])))
        if inicios is not None:
            contagem["_agora"] = ate
            contagem.update(com_os_dias(contagem, desde=inicios.get(chave), agora=ate))
    return saida


def somar(contagens: list[dict[str, Any]], aut: cat.Automacao | None) -> dict[str, Any]:
    """A soma de várias lojas (a linha da automação na tela). PURA."""
    total: dict[str, Any] = defaultdict(int)
    motivos: dict[str, int] = defaultdict(int)
    diferencas: list[int] = []
    atrasos: list[int] = []
    for c in contagens:
        for k, v in c.items():
            if (
                isinstance(v, int)
                and not isinstance(v, bool)
                and k not in ("diferenca_mediana_s", "atraso_mediana_s")
            ):
                total[k] += v
        for m, n in (c.get("motivos") or {}).items():
            motivos[m] += n
        diferencas += list(c.get("_diferencas") or [])
        atrasos += list(c.get("_atrasos") or [])
    saida = dict(total)
    saida["motivos"] = dict(sorted(motivos.items(), key=lambda kv: -kv[1]))
    saida["diferenca_mediana_s"] = int(median(diferencas)) if diferencas else None
    saida["atraso_mediana_s"] = int(median(atrasos)) if atrasos else None
    saida.update(resumir(saida, aut))
    # Os 7 dias de dados (só com `inicios` na conta das lojas): a soma vale
    # desde a loja mais antiga — é só informação, a troca é loja por loja. O
    # `_agora` (interno) fica nas contas das lojas; a soma vai direto para a
    # resposta da rota e não o leva.
    agoras = [c["_agora"] for c in contagens if c.get("_agora") is not None]
    if agoras:
        desdes = [c["dados_desde"] for c in contagens if c.get("dados_desde") is not None]
        saida.update(com_os_dias(saida, desde=min(desdes) if desdes else None, agora=max(agoras)))
    return saida


def sem_internos(conta: dict[str, Any] | None) -> dict[str, Any] | None:
    """A conta sem as chaves internas (`_diferencas`) — o que a rota devolve."""
    if conta is None:
        return None
    return {k: v for k, v in conta.items() if not k.startswith("_")}
