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
     hora e a diferença). A janela fechou sem achar → `nao_mandou` — só se a
     LEITURA da loja já passou do fim da janela (`atendimento_canais.
     ultimo_ok_em`); com a leitura parada (deploy, token), continua pendente.
     Só conta mensagem de FORA (`externo`/`sistema`) e sem a marca do motor
     (`payload.automacao`): a nossa não casa consigo mesma.
  2. SÓ DUOKE — a mensagem do Duoke (das últimas 48 h, com o motor e a regra
     ligados há tempo bastante) que nenhuma linha usou vira a linha
     `so_duoke`: o que o DaVinci deixaria de mandar.
  3. DISJUNTOR — o Duoke mandou numa regra que já está em `enviar` (depois
     da troca): a regra volta sozinha para `simular` e o log avisa.

`estatisticas` é a conta que a tela mostra (precisão, cobertura e a % que
bateu, separadas) e o critério da troca.
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
_M = AtendimentoMensagem
_C = AtendimentoConversa

# Até quantas linhas pendentes por rodada (as mais velhas primeiro).
MAX_PENDENTES = 3000
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
# Critério da troca (proposta; decisão do Eduardo, §6.5 do doc).
CRITERIO_MINIMO = 0.95
CRITERIO_CASOS = 30

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
                momento >= ini,
                momento <= fim,
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
                momento >= ini - CARTAO_JUNTO,
                momento <= fim + CARTAO_JUNTO,
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


def _marcar_mandou(linha: AtendimentoAutomacaoRegistro, m: cat.Msg, agora: datetime) -> None:
    linha.duoke = cat.DUOKE_MANDOU
    linha.duoke_mensagem_id = m.id
    linha.duoke_em = m.em
    linha.duoke_diferenca_s = int((m.em - _utc(linha.devido_em)).total_seconds())
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
            _marcar_mandou(x, m, agora)
            mandou += 1
            continue
        lido = ultimo_ok.get((x.integration_id, aut.canal))
        if agora > b and lido is not None and lido > b:
            x.duoke = cat.DUOKE_NAO_MANDOU
            x.comparado_em = agora
            nao += 1
    return mandou, nao


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
    da_loja = _M.conversa_id.in_(
        select(_C.id).where(_C.integration_id.in_(integs), _C.canal == "chat")
    )
    duoke = await _mensagens_do_duoke(session, da_loja, ini, fim)
    usadas = await _usadas(session, [m.id for _, m in duoke])
    duoke = [(cid, m) for cid, m in duoke if m.id not in usadas]
    cartoes = await _cartoes(session, da_loja, ini, fim)
    conversa_ids = sorted({cid for cid, _ in duoke}, key=str)
    conversas = (
        {
            c.id: c
            for c in (await session.execute(select(_C).where(_C.id.in_(conversa_ids))))
            .scalars()
            .all()
        }
        if conversa_ids
        else {}
    )
    do_comprador = await _pedidos_do_comprador(session, conversas)
    # Os pedidos de cada conversa pelos cartões (do comprador ou da campanha) dos
    # últimos 45 dias: o pós do Duoke não traz cartão, mas a conversa dele é a
    # que recebeu o "pedido recebido"/"entregue" daquele pedido.
    if conversa_ids:
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
    # Cada mensagem do Duoke → os pedidos a que ela pode ser.
    candidatas: list[tuple[cat.Msg, UUID, set[str], set[str]]] = []
    for cid, m in duoke:
        pelo_cartao = {sn for em, sn in cartoes.get(cid, []) if abs(em - m.em) <= CARTAO_JUNTO}
        outros = set(do_comprador.get(cid, set()))
        c = conversas.get(cid)
        if c is not None and c.pedido_marketplace:
            outros.add(c.pedido_marketplace)
        candidatas.append(
            (m, conversas[cid].integration_id if cid in conversas else None, pelo_cartao, outros)
        )
    usadas_agora: set = set()
    mandou = nao = 0
    for x in sorted(linhas, key=lambda y: y.devido_em):
        aut = cat.CATALOGO[x.automacao]
        a, b = janelas[x.id]
        achadas = [
            (0 if x.pedido in cartao else 1, m)
            for m, integ, cartao, outros in candidatas
            if m.id not in usadas_agora
            and integ == x.integration_id
            and _casa(aut, m)
            and a <= m.em <= b
            and (x.pedido in cartao or (not cartao and x.pedido in outros))
        ]
        if achadas:
            _, m = min(
                achadas,
                key=lambda par: (par[0], abs((par[1].em - _utc(x.devido_em)).total_seconds())),
            )
            usadas_agora.add(m.id)
            _marcar_mandou(x, m, agora)
            mandou += 1
            continue
        lido = ultimo_ok.get((x.integration_id, aut.canal))
        if agora > b and lido is not None and lido > b:
            x.duoke = cat.DUOKE_NAO_MANDOU
            x.comparado_em = agora
            nao += 1
    return mandou, nao


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
        cancelados = set(
            (
                await session.execute(
                    select(BlingOrder.numeroloja).where(
                        BlingOrder.numeroloja.in_(pedidos),
                        BlingOrder.situacao.in_(("12", "excluido")),
                    )
                )
            )
            .scalars()
            .all()
        )
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


def _automacao_do_duoke(m: cat.Msg, plataforma: str, canal: str) -> str | None:
    """O código da nossa automação para uma mensagem do Duoke (None = fora do catálogo)."""
    tipo = m.tipo_duoke
    if tipo == cat.TIPO_OPCAO:
        codigo = f"{plataforma}_opcao_{m.opcao_duoke}"
    else:
        codigo = next(
            (
                a.codigo
                for a in cat.CATALOGO.values()
                if a.plataforma == plataforma and a.canal == canal and a.tipo == tipo
            ),
            None,
        )
    return codigo if codigo in cat.CATALOGO else None


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
                momento >= ini - timedelta(hours=1),
                momento <= fim,
            )
        )
    ).all():
        compradores[cid].append(_utc(em))
    com_logistica: set[str] = set()
    sns = {sn for lista in cartoes.values() for _, sn in lista}
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
    novas = []
    for cid, m in duoke:
        c = conversas.get(cid)
        if c is None or c.integration_id is None:
            continue
        codigo = _automacao_do_duoke(m, c.plataforma, c.canal)
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
        pedido = pedido or (c.pedido_marketplace if aut.alvo == cat.ALVO_PEDIDO else None)
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
    if not novas:
        return 0
    resultado = await session.execute(
        pg_insert(_R).values(novas).on_conflict_do_nothing().returning(_R.id)
    )
    return len(resultado.all())


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
    de_conversa = [
        x for x in abertas if x.conversa_id and cat.CATALOGO[x.automacao].alvo != cat.ALVO_PEDIDO
    ]
    de_pedido = [
        x for x in abertas if cat.CATALOGO[x.automacao].alvo == cat.ALVO_PEDIDO and x.pedido
    ]
    m1, n1 = await _comparar_conversas(session, de_conversa, agora=agora, ultimo_ok=ultimo_ok)
    m2, n2 = await _comparar_pedidos(session, de_pedido, agora=agora, ultimo_ok=ultimo_ok)
    await _alertas_e_divergencias(session, de_conversa + de_pedido)
    await session.flush()
    regras = await regras_por_chave(session, ativas=False)
    so = await _so_duoke(session, agora=agora, motor_desde=_utc(motor_desde), regras=regras)
    disjuntor = await _disjuntor(session, agora=agora, regras=regras)
    return {"mandou": m1 + m2, "nao_mandou": n1 + n2, "so_duoke": so, "disjuntor": disjuntor}


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


async def estatisticas(
    session: AsyncSession,
    *,
    desde: datetime,
    ate: datetime,
    automacao: str | None = None,
    integration_id: UUID | None = None,
) -> dict[tuple[str, UUID], dict[str, Any]]:
    """As contagens por (automação, loja) no período (pela `devido_em`), com a conta pronta."""
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
    diffs = (
        await session.execute(
            select(_R.automacao, _R.integration_id, _R.duoke_diferenca_s).where(
                *filtro, _R.duoke == cat.DUOKE_MANDOU, mandam, _R.duoke_diferenca_s.is_not(None)
            )
        )
    ).all()
    por_par: dict[tuple, list[int]] = defaultdict(list)
    for codigo, integ, d in diffs:
        por_par[(codigo, integ)].append(int(d))
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
        # Para a soma das lojas (`somar`) refazer a mediana; a rota não devolve.
        contagem["_diferencas"] = lista
        contagem["motivos"] = dict(sorted(por_motivo.get(chave, {}).items(), key=lambda kv: -kv[1]))
        contagem.update(resumir(contagem, cat.CATALOGO.get(chave[0])))
    return saida


def somar(contagens: list[dict[str, Any]], aut: cat.Automacao | None) -> dict[str, Any]:
    """A soma de várias lojas (a linha da automação na tela). PURA."""
    total: dict[str, Any] = defaultdict(int)
    motivos: dict[str, int] = defaultdict(int)
    diferencas: list[int] = []
    for c in contagens:
        for k, v in c.items():
            if isinstance(v, int) and not isinstance(v, bool) and k != "diferenca_mediana_s":
                total[k] += v
        for m, n in (c.get("motivos") or {}).items():
            motivos[m] += n
        diferencas += list(c.get("_diferencas") or [])
    saida = dict(total)
    saida["motivos"] = dict(sorted(motivos.items(), key=lambda kv: -kv[1]))
    saida["diferenca_mediana_s"] = int(median(diferencas)) if diferencas else None
    saida.update(resumir(saida, aut))
    return saida


def sem_internos(conta: dict[str, Any] | None) -> dict[str, Any] | None:
    """A conta sem as chaves internas (`_diferencas`) — o que a rota devolve."""
    if conta is None:
        return None
    return {k: v for k, v in conta.items() if not k.startswith("_")}
