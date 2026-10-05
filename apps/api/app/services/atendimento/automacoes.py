"""O motor das mensagens automáticas (05/10/2026, docs/atendimento-automacoes.md).

Recria no DaVinci as automações que o Duoke manda hoje (o catálogo é
`automacoes_catalogo.py`), COMEÇANDO EM MODO SECO: o motor roda de verdade —
descobre o gatilho, decide com as mesmas condições, renderiza o texto, passa
pelo validador, aplica o teto e grava a linha (`simulado`, "mandaria às
HH:MM", ou `pulado` com o motivo) — mas NÃO cria mensagem, NÃO importa o
`enviar` e NÃO chama plataforma nenhuma, nem para ler. O comparador
(`automacoes_comparar.py`) confere com o que o Duoke mandou de verdade.

Cada rodada (cron `atendimento_automacoes`, minutos pares, logo depois da
leitura das caixas nos ímpares; UMA por vez, trava no Redis):

  1. DESCOBRIR — os gatilhos novos, só do banco:
       • mensagem do comprador (a leitura grava de 59 s a 110 s depois);
       • pedido pago visto no Bling (`bling_orders.created_at`; a loja por
         `stores.integration_id`);
       • entregue e concluído na varredura da Logística (`status_datas`, a
         cada 10 min) — o próprio registro guarda o evento na hora em que o
         motor vê (o status anterior se perde).
     Grava `INSERT … ON CONFLICT DO NOTHING` pela chave única.
  2. DECIDIR — as linhas que venceram (até 200 por rodada, no máximo 50 da
     mesma regra: a fila parada de uma loja não segura as outras): validade,
     HORÁRIO (fora da janela da regra, a linha espera a próxima abertura — o
     da descoberta não basta: a linha pode esperar a leitura, o motor parado,
     o rearme), condições (`automacoes_catalogo.decidir`), texto, teto. Regra
     em `simular` → `simulado`; em `enviar` com as chaves desligadas →
     `simulado` com `envio_desligado`; com tudo ligado → envia pelo
     `enviar.enviar_automatica`, uma parte de texto por vez.
     O MODO SECO MEDE O DAVINCI SOZINHO: o estado do robô (sessão do menu,
     intervalo do "aguarde", ciclo da dúvida) não conta a mensagem do Duoke
     de uma automação que o DaVinci está simulando, depois do corte (a regra
     ligada e o motor rodando) — vale a linha simulada dele
     (`automacoes_catalogo.cortes_do_modo_seco`). Senão a paridade sai
     inflada: a resposta da opção do Duoke, 12 h depois do menu, segurava o
     menu do DaVinci.
  3. COMPARAR — `automacoes_comparar.comparar`.

O ENVIO (só com `atendimento_envio_ativo` + `atendimento_automacoes_envio` +
regra em `enviar`) tem as travas contra duplicar: a chave única, o registro
`enviando` commitado ANTES da plataforma (preso há 10 min vira `revisar`,
NUNCA retentado), "já mandado" conferido de novo na decisão — no modo enviar
contando também a mensagem do Duoke DEPOIS do gatilho: na conversa e, nas do
PEDIDO (pedido recebido, entregue, pós), pela mesma régua do comparador
(`automacoes_comparar.duoke_dos_pedidos`: o cartão do pedido do Duoke ou a
conversa do pedido). Na TRANSIÇÃO (`TRANSICAO` depois de `enviar_desde`) a
decisão ainda espera a leitura da loja passar do `devido_em` + a espera do
Duoke; depois, não espera (só atrasaria o comprador). E o disjuntor: o Duoke
mandou depois de a regra ir para `enviar` → a regra volta sozinha para
`simular` ("Duoke ainda ligado?") NA HORA, e o resto do lote já decide em
`simular`. Campanha da Shopee só sai como resposta automática (`auto_reply`):
sem a chave confirmada E o envio por ela no adaptador, `campanha_sem_auto_reply`.

O log leva só ids, contagens, códigos e a duração de cada fase. Texto de
comprador nunca sai daqui: a classificação lê o texto e devolve só sinais.
"""

from __future__ import annotations

import time as _time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

import app.db as _db
from app.config import get_settings
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
    Integration,
    Logistica,
    Store,
)
from app.redis_client import redis
from app.services.atendimento import automacoes_catalogo as cat
from app.services.atendimento.constantes import (
    AUTOR_CLIENTE,
    CONVERSA_BLOQUEADA,
    MODO_AUTO,
    ORIGEM_NOTA,
    reclamacao_aberta,
)

logger = structlog.get_logger()

# Apelidos curtos dos models nas consultas.
_R = AtendimentoAutomacaoRegistro
_C = AtendimentoConversa
_M = AtendimentoMensagem
_P = AtendimentoPedidoComprador
_B = BlingOrder
_L = Logistica
_A = AtendimentoAvaliacaoLoja
_Rc = AtendimentoReclamacao

# Quanto para trás cada descoberta olha (a chave única segura o repetido).
LOOKBACK_MENSAGENS = timedelta(minutes=40)
LOOKBACK_BLING = timedelta(hours=2)
LOOKBACK_LOGISTICA = timedelta(days=3)
# O histórico da conversa que a decisão lê: o ciclo de 7 dias do "ficou
# alguma dúvida" com folga.
HISTORICO_CONVERSA = timedelta(days=8)
# A Logística a cada 10 min (a varredura da Shopee é de hora em hora).
LOGISTICA_A_CADA_MIN = 10
MAX_DECISOES = 200
# No máximo tantas da MESMA regra (loja × automação) por rodada: a linha que
# espera a leitura da loja volta igual e seria escolhida de novo, segurando
# as das outras lojas.
MAX_POR_REGRA = 50
# Na troca, os primeiros dias depois de `enviar_desde`: a decisão espera a
# leitura da loja e a espera do Duoke da automação (o que o Duoke agendou antes
# de ser desligado ainda pode sair). Depois disso a espera só atrasaria o menu.
TRANSICAO = timedelta(days=3)
# INSERT de muitas linhas em lotes: o asyncpg recusa mais de 32.767 parâmetros
# (a Logística reinsere ~600 pedidos de 3 dias a cada 10 min, 19 por linha).
LOTE_INSERT = 500
# O registro guarda 90 dias (a tela olha até 60); a limpeza roda uma vez por dia.
GUARDA_REGISTRO = timedelta(days=90)
LIMPEZA_LOTE = 5000
HORA_LIMPEZA_UTC = 6  # 3h30 de Brasília
# Uma rodada por vez (por schema). O cron é de 2 em 2 min com timeout 110 s.
RODADA_TTL_S = 110
_CHAVE_RODADA = "atendimento:automacoes:rodada:{}"
# Desde quando o motor roda sem parar (o "só Duoke" só conta daí em diante;
# a chave some quando o motor é desligado).
_CHAVE_MOTOR = "atendimento:automacoes:motor_desde:{}"
ENVIANDO_PRESO = timedelta(minutes=10)
# Mensagens normais do DaVinci para o mesmo comprador sem resposta dele.
TETO_COMPRADOR = 3
# A janela de mensagem da Shopee (medida na senha da devolução).
JANELA_SHOPEE_COMPRADOR = timedelta(days=7)
JANELA_SHOPEE_PEDIDO = timedelta(days=30)
SHOPEE_PEDIDO = r"^[0-9]{6}[0-9A-Z]{8}$"
_STATUS_SEM_ACESSO = ("sem_escopo", "desligado")
_SITUACOES_CANCELADO = ("12", "excluido")
_STATUS_INDICE_CANCELADO = ("CANCELLED", "IN_CANCEL")
# Nomes que são a MESMA loja Shopee na Logística (a conta foi renomeada no
# cadastro de lojas e a integração ficou com o nome antigo — logistica_shopee).
_MESMA_LOJA: tuple[frozenset[str], ...] = (
    frozenset({"atlas", "jlas"}),
    frozenset({"fiore", "kia", "kia/fiore"}),
)


def _agora() -> datetime:
    return datetime.now(UTC)


def _utc(quando: datetime | None) -> datetime | None:
    if quando is None:
        return None
    return quando.replace(tzinfo=UTC) if quando.tzinfo is None else quando.astimezone(UTC)


def _iso(valor: Any) -> datetime | None:
    if not isinstance(valor, str) or not valor.strip():
        return None
    try:
        return _utc(datetime.fromisoformat(valor.strip().replace("Z", "+00:00")))
    except ValueError:
        return None


# ── Regras ────────────────────────────────────────────────────────────────


async def regras_por_chave(
    session: AsyncSession, *, ativas: bool = True
) -> dict[tuple[str, UUID], AtendimentoAutomacaoRegra]:
    """As regras (automação, loja) → regra; `ativas` = só as fora de `desligado`."""
    q = select(AtendimentoAutomacaoRegra).execution_options(populate_existing=True)
    if ativas:
        q = q.where(AtendimentoAutomacaoRegra.modo != cat.MODO_DESLIGADO)
    linhas = (await session.execute(q)).scalars().all()
    return {(r.automacao, r.integration_id): r for r in linhas if r.automacao in cat.CATALOGO}


def _desde_da_regra(regra: AtendimentoAutomacaoRegra, piso: datetime) -> datetime:
    ligada = _utc(regra.ligada_desde)
    return max(piso, ligada) if ligada else piso


# ── 1. Descobrir ──────────────────────────────────────────────────────────


def _payload_minimo():
    """O pedaço do payload que a classificação usa (o item cru inteiro é grande)."""
    p = AtendimentoMensagem.payload
    return func.jsonb_strip_nulls(
        func.jsonb_build_object(
            "message_type",
            p.op("->")("message_type"),
            "type",
            p.op("->")("type"),
            "source",
            p.op("->")("source"),
            "sender",
            func.jsonb_build_object("role", p[("sender", "role")]),
            "content",
            func.jsonb_build_object(
                "order_sn",
                p[("content", "order_sn")],
                "order_id",
                p[("content", "order_id")],
                "sticker_id",
                p[("content", "sticker_id")],
            ),
            "automacao",
            p.op("->")("automacao"),
        )
    )


def _momento():
    return func.coalesce(AtendimentoMensagem.enviada_em, AtendimentoMensagem.created_at)


async def linha_do_tempo(
    session: AsyncSession, conversa_ids: list[UUID], *, agora: datetime
) -> dict[UUID, list[cat.Msg]]:
    """As mensagens dos últimos 8 dias de cada conversa, classificadas (sem texto)."""
    if not conversa_ids:
        return {}
    momento = _momento()
    linhas = (
        await session.execute(
            select(
                AtendimentoMensagem.id,
                AtendimentoMensagem.conversa_id,
                AtendimentoMensagem.autor,
                AtendimentoMensagem.origem,
                AtendimentoMensagem.status,
                momento.label("em"),
                AtendimentoMensagem.created_at,
                func.left(AtendimentoMensagem.texto, 400).label("inicio"),
                _payload_minimo().label("payload"),
            )
            .where(
                AtendimentoMensagem.conversa_id.in_(conversa_ids),
                momento >= agora - HISTORICO_CONVERSA,
                AtendimentoMensagem.created_at <= agora,
                AtendimentoMensagem.origem != ORIGEM_NOTA,
            )
            .order_by(AtendimentoMensagem.conversa_id, momento, AtendimentoMensagem.created_at)
        )
    ).all()
    saida: dict[UUID, list[cat.Msg]] = defaultdict(list)
    for r in linhas:
        saida[r.conversa_id].append(
            cat.classificar(
                id=r.id,
                autor=r.autor,
                origem=r.origem,
                status=r.status,
                texto=r.inicio,
                payload=r.payload,
                em=r.em,
                visto_em=r.created_at,
            )
        )
    return saida


async def _registro_das_conversas(
    session: AsyncSession, conversa_ids: list[UUID], *, agora: datetime
) -> dict[UUID, list[cat.Linha]]:
    if not conversa_ids:
        return {}
    linhas = (
        await session.execute(
            select(
                _R.conversa_id,
                _R.automacao,
                _R.estado,
                _R.chave,
                _R.evento_em,
                _R.devido_em,
                _R.gatilho_mensagem_id,
            ).where(
                _R.conversa_id.in_(conversa_ids),
                _R.devido_em >= agora - HISTORICO_CONVERSA - timedelta(days=2),
            )
        )
    ).all()
    saida: dict[UUID, list[cat.Linha]] = defaultdict(list)
    for r in linhas:
        saida[r.conversa_id].append(
            cat.Linha(
                automacao=r.automacao,
                estado=r.estado,
                chave=r.chave,
                evento_em=_utc(r.evento_em),
                devido_em=_utc(r.devido_em),
                gatilho_mensagem_id=r.gatilho_mensagem_id,
            )
        )
    return saida


async def _gravar_candidatos(
    session: AsyncSession, linhas: list[dict], *, atualizaveis: list[dict] | None = None
) -> int:
    """INSERT … ON CONFLICT DO NOTHING (a chave única); devolve quantas nasceram."""
    novas = 0
    for i in range(0, len(linhas), LOTE_INSERT):
        resultado = await session.execute(
            pg_insert(_R)
            .values(linhas[i : i + LOTE_INSERT])
            .on_conflict_do_nothing(constraint="uq_atendimento_automacao_registros_chave")
            .returning(_R.id)
        )
        novas += len(resultado.all())
    for linha in atualizaveis or ():
        # TikTok "ficou alguma dúvida": conta da ÚLTIMA mensagem — a linha
        # agendada anda com a mensagem nova (nunca a já decidida).
        stmt = pg_insert(_R).values(linha)
        resultado = await session.execute(
            stmt.on_conflict_do_update(
                constraint="uq_atendimento_automacao_registros_chave",
                set_={
                    "devido_em": stmt.excluded.devido_em,
                    "evento_em": stmt.excluded.evento_em,
                    "gatilho_mensagem_id": stmt.excluded.gatilho_mensagem_id,
                    "updated_at": func.now(),
                },
                where=and_(
                    _R.estado == cat.ESTADO_AGENDADO, _R.devido_em < stmt.excluded.devido_em
                ),
            ).returning(_R.id, _R.created_at, _R.updated_at)
        )
        for _id, criado, mudado in resultado.all():
            novas += int(criado == mudado)
    return novas


def _linha_registro(
    aut: cat.Automacao,
    regra: AtendimentoAutomacaoRegra,
    *,
    chave: str,
    agora: datetime,
    evento_em: datetime | None,
    devido_em: datetime,
    conversa_id: UUID | None = None,
    gatilho_mensagem_id: UUID | None = None,
    pedido: str | None = None,
    comprador_id: str | None = None,
) -> dict:
    return {
        "id": uuid4(),
        "automacao": aut.codigo,
        "regra_id": regra.id,
        "regra_versao": regra.versao,
        "integration_id": regra.integration_id,
        "plataforma": aut.plataforma,
        "alvo": aut.alvo,
        "chave": chave[:191],
        "conversa_id": conversa_id,
        "gatilho_mensagem_id": gatilho_mensagem_id,
        "pedido": pedido,
        "comprador_id": comprador_id,
        "evento_em": evento_em,
        "visto_em": agora,
        "devido_em": devido_em,
        "estado": cat.ESTADO_AGENDADO,
        "duoke": cat.DUOKE_PENDENTE,
    }


async def descobrir_mensagens(
    session: AsyncSession,
    regras: dict[tuple[str, UUID], AtendimentoAutomacaoRegra],
    *,
    agora: datetime,
    motor_desde: datetime,
) -> int:
    """Os gatilhos das mensagens novas do comprador (menu, opções, aguarde, convite, dúvida)."""
    por_loja: dict[UUID, dict[str, AtendimentoAutomacaoRegra]] = defaultdict(dict)
    todas_da_loja: dict[UUID, list[AtendimentoAutomacaoRegra]] = defaultdict(list)
    for (codigo, integ), regra in regras.items():
        aut = cat.CATALOGO[codigo]
        todas_da_loja[integ].append(regra)
        if aut.gatilho in (cat.GATILHO_MENSAGEM, cat.GATILHO_OPCAO):
            por_loja[integ][codigo] = regra
    if not por_loja:
        return 0
    # O modo seco mede o DaVinci sozinho (o estado sem o Duoke que ele simula).
    cortes = {
        integ: cat.cortes_do_modo_seco(lista, motor_desde) for integ, lista in todas_da_loja.items()
    }
    piso = max(agora - LOOKBACK_MENSAGENS, motor_desde)
    conversas = (
        await session.execute(
            select(_C.id, _C.integration_id, _C.plataforma, _C.canal, _C.comprador_id).where(
                _C.integration_id.in_(list(por_loja)),
                _C.id.in_(
                    select(_M.conversa_id).where(
                        or_(
                            _M.autor == AUTOR_CLIENTE,
                            # A pergunta pronta do chat da Shopee (o convite conta).
                            _M.payload["message_type"].astext == "bundle_message",
                        ),
                        # `enviada_em` tem índice; `created_at` é quando a leitura gravou.
                        _M.enviada_em >= piso - timedelta(hours=2),
                        _M.created_at >= piso,
                        _M.created_at <= agora,
                    )
                ),
            )
        )
    ).all()
    if not conversas:
        return 0
    ids = [c.id for c in conversas]
    msgs = await linha_do_tempo(session, ids, agora=agora)
    registro = await _registro_das_conversas(session, ids, agora=agora)
    novas: list[dict] = []
    atualizaveis: list[dict] = []
    for c in conversas:
        ativas = {
            codigo: regra
            for codigo, regra in por_loja[c.integration_id].items()
            if cat.CATALOGO[codigo].plataforma == c.plataforma
            and cat.CATALOGO[codigo].canal == c.canal
        }
        if not ativas:
            continue
        conv = cat.Conversa(
            id=c.id,
            plataforma=c.plataforma,
            canal=c.canal,
            comprador_id=c.comprador_id,
            msgs=cat.sem_o_duoke_substituido(
                msgs.get(c.id, []),
                plataforma=c.plataforma,
                canal=c.canal,
                cortes=cortes.get(c.integration_id),
            ),
            registro=registro.get(c.id, []),
            ativas=ativas,
            desde=piso,
        )
        for cand in cat.gatilhos_da_conversa(conv):
            regra = ativas.get(cand.automacao)
            if regra is None:
                continue
            ligada = _utc(regra.ligada_desde)
            if ligada is not None and cand.evento_em < ligada:
                continue  # o gatilho é de antes de a regra ser ligada
            linha = _linha_registro(
                cat.CATALOGO[cand.automacao],
                regra,
                chave=cand.chave,
                agora=agora,
                evento_em=cand.evento_em,
                devido_em=cand.devido_em,
                conversa_id=c.id,
                gatilho_mensagem_id=cand.gatilho_mensagem_id,
                comprador_id=c.comprador_id,
            )
            (atualizaveis if cand.atualizar_agendado else novas).append(linha)
    return await _gravar_candidatos(session, novas, atualizaveis=atualizaveis)


async def _conversas_do_pedido(
    session: AsyncSession, pares: list[tuple[UUID, str]], *, agora: datetime | None = None
) -> dict[tuple[UUID, str], UUID]:
    """(loja, pedido) → a conversa da loja com aquele pedido (a mais recente).

    Pelo pedido ligado à conversa e, sem ele, pelo CARTÃO do pedido na conversa
    (o do comprador, ou o da campanha "pedido recebido" que abriu a conversa)
    dos últimos 45 dias — é assim que o entregue e o pós acham a conversa de
    quem comprou antes de o índice cobrir a loja.
    """
    if not pares:
        return {}
    pedidos = sorted({p for _, p in pares})
    integs = sorted({i for i, _ in pares}, key=str)
    linhas = (
        await session.execute(
            select(_C.id, _C.integration_id, _C.pedido_marketplace)
            .where(_C.pedido_marketplace.in_(pedidos), _C.canal == "chat")
            .order_by(_C.ultima_mensagem_em.desc().nulls_last())
        )
    ).all()
    saida: dict[tuple[UUID, str], UUID] = {}
    for r in linhas:
        saida.setdefault((r.integration_id, r.pedido_marketplace), r.id)
    faltam = [p for p in pares if p not in saida]
    if faltam:
        sn = _M.payload[("content", "order_sn")].astext
        cartoes = (
            await session.execute(
                select(_M.conversa_id, _C.integration_id, sn.label("sn"))
                .join(_C, _C.id == _M.conversa_id)
                .where(
                    _C.integration_id.in_(integs),
                    _C.canal == "chat",
                    _M.payload["message_type"].astext == "order",
                    sn.in_(sorted({p for _, p in faltam})),
                    _M.enviada_em >= (agora or _agora()) - timedelta(days=45),
                )
                .order_by(_M.enviada_em.desc())
            )
        ).all()
        for r in cartoes:
            saida.setdefault((r.integration_id, r.sn), r.conversa_id)
    return saida


async def _conversas_do_comprador(
    session: AsyncSession, pares: list[tuple[UUID, str]]
) -> dict[tuple[UUID, str], UUID]:
    """(loja, comprador) → a conversa de chat da loja com ele (a mais recente)."""
    if not pares:
        return {}
    compradores = sorted({c for _, c in pares})
    integs = sorted({i for i, _ in pares}, key=str)
    linhas = (
        await session.execute(
            select(_C.id, _C.integration_id, _C.comprador_id)
            .where(
                _C.comprador_id.in_(compradores), _C.integration_id.in_(integs), _C.canal == "chat"
            )
            .order_by(_C.ultima_mensagem_em.desc().nulls_last())
        )
    ).all()
    saida: dict[tuple[UUID, str], UUID] = {}
    for r in linhas:
        saida.setdefault((r.integration_id, r.comprador_id), r.id)
    return saida


async def _compradores_do_pedido(
    session: AsyncSession, pares: list[tuple[UUID, str]]
) -> dict[tuple[UUID, str], tuple[str, str | None, datetime | None]]:
    """(loja, pedido) → (comprador, status, criado_em) pelo índice do cartão "Cliente"."""
    if not pares:
        return {}
    pedidos = sorted({p for _, p in pares})
    linhas = (
        await session.execute(
            select(_P.integration_id, _P.pedido, _P.comprador_id, _P.status, _P.criado_em).where(
                _P.pedido.in_(pedidos)
            )
        )
    ).all()
    return {
        (r.integration_id, r.pedido): (r.comprador_id, r.status, _utc(r.criado_em)) for r in linhas
    }


async def descobrir_pedidos_pagos(
    session: AsyncSession,
    regras: dict[tuple[str, UUID], AtendimentoAutomacaoRegra],
    *,
    agora: datetime,
    motor_desde: datetime,
) -> int:
    """Pedido pago visto no Bling → "pedido recebido" 5 min depois."""
    ativas = {
        integ: regra
        for (codigo, integ), regra in regras.items()
        if cat.CATALOGO[codigo].gatilho == cat.GATILHO_PEDIDO_PAGO
    }
    if not ativas:
        return 0
    aut = cat.CATALOGO["shopee_pedido_recebido"]
    piso = max(agora - LOOKBACK_BLING, motor_desde)
    linhas = (
        await session.execute(
            select(Store.integration_id, _B.numeroloja, func.min(_B.created_at).label("criado"))
            .join(Store, Store.id == _B.store_id)
            .where(
                Store.integration_id.in_(list(ativas)),
                # `data` tem índice (a varredura do `created_at` sozinho era
                # um Seq Scan de 500 ms em produção a cada rodada).
                _B.data >= agora - timedelta(days=2),
                _B.created_at >= piso,
                _B.created_at <= agora,
                _B.numeroloja.op("~")(SHOPEE_PEDIDO),
            )
            .group_by(Store.integration_id, _B.numeroloja)
        )
    ).all()
    pares = [(r.integration_id, r.numeroloja) for r in linhas]
    conversas = await _conversas_do_pedido(session, pares, agora=agora)
    compradores = await _compradores_do_pedido(session, pares)
    novas = []
    for r in linhas:
        regra = ativas[r.integration_id]
        criado = _utc(r.criado)
        if criado < _desde_da_regra(regra, piso):
            continue
        atraso = timedelta(minutes=regra.atraso_min)
        comprador = compradores.get((r.integration_id, r.numeroloja))
        novas.append(
            _linha_registro(
                aut,
                regra,
                chave=f"pedido:{r.numeroloja}",
                agora=agora,
                evento_em=criado,
                devido_em=criado + atraso,
                conversa_id=conversas.get((r.integration_id, r.numeroloja)),
                pedido=r.numeroloja,
                comprador_id=comprador[0] if comprador else None,
            )
        )
    return await _gravar_candidatos(session, novas)


def _lojas_shopee(integracoes: list[tuple[UUID, str]]) -> dict[str, UUID]:
    """Nome da conta na Logística → integração (com os nomes da mesma loja)."""
    saida: dict[str, UUID] = {}
    for integ, nome in integracoes:
        chave = cat.nome_normalizado(nome)
        saida.setdefault(chave, integ)
        for grupo in _MESMA_LOJA:
            if chave in grupo:
                for outro in grupo:
                    saida.setdefault(outro, integ)
    return saida


async def descobrir_logistica(
    session: AsyncSession,
    regras: dict[tuple[str, UUID], AtendimentoAutomacaoRegra],
    *,
    agora: datetime,
    motor_desde: datetime,
) -> int:
    """Entregue (`TO_CONFIRM_RECEIVE`) e concluído (`COMPLETED`) na varredura da Logística."""
    ativas: dict[str, dict[UUID, AtendimentoAutomacaoRegra]] = defaultdict(dict)
    for (codigo, integ), regra in regras.items():
        aut = cat.CATALOGO[codigo]
        if aut.gatilho in (cat.GATILHO_ENTREGUE, cat.GATILHO_CONCLUIDO):
            ativas[codigo][integ] = regra
    if not ativas:
        return 0
    integs = sorted({i for por in ativas.values() for i in por})
    nomes = (
        await session.execute(
            select(Integration.id, Integration.name).where(Integration.id.in_(integs))
        )
    ).all()
    loja_da_conta = _lojas_shopee([(r.id, r.name) for r in nomes])
    status = _L.meli_status["order_status"].astext
    linhas = (
        await session.execute(
            select(
                _L.pedido_marketplace,
                _L.conta,
                status.label("status"),
                _L.status_datas[("order_status", "em")].astext.label("em"),
            ).where(
                func.lower(func.trim(_L.plataforma)) == "shopee",
                _L.status_lido_em >= agora - LOOKBACK_LOGISTICA,
                _L.status_lido_em <= agora,
                status.in_(("TO_CONFIRM_RECEIVE", "COMPLETED")),
                _L.pedido_marketplace.is_not(None),
            )
        )
    ).all()
    piso = motor_desde - timedelta(hours=2)
    por_status = {"TO_CONFIRM_RECEIVE": "shopee_entregue", "COMPLETED": "shopee_pos_conclusao"}
    escolhidas = []
    for r in linhas:
        codigo = por_status[r.status]
        integ = loja_da_conta.get(cat.nome_normalizado(r.conta))
        regra = ativas.get(codigo, {}).get(integ) if integ else None
        evento = _iso(r.em)
        if regra is None or evento is None or evento > agora:
            continue
        if evento < _desde_da_regra(regra, piso):
            continue
        escolhidas.append((codigo, regra, r.pedido_marketplace.strip(), evento))
    pares = [(regra.integration_id, sn) for _, regra, sn, _ in escolhidas]
    conversas = await _conversas_do_pedido(session, pares, agora=agora)
    compradores = await _compradores_do_pedido(session, pares)
    novas = []
    for codigo, regra, sn, evento in escolhidas:
        aut = cat.CATALOGO[codigo]
        devido = cat.ajustar_janela(
            evento + timedelta(minutes=regra.atraso_min),
            *(cat.janela_da_regra(regra, aut) or (None, None)),
        )
        comprador = compradores.get((regra.integration_id, sn))
        novas.append(
            _linha_registro(
                aut,
                regra,
                chave=f"pedido:{sn}",
                agora=agora,
                evento_em=evento,
                devido_em=devido,
                conversa_id=conversas.get((regra.integration_id, sn)),
                pedido=sn,
                comprador_id=comprador[0] if comprador else None,
            )
        )
    return await _gravar_candidatos(session, novas)


# ── 2. Decidir ────────────────────────────────────────────────────────────


@dataclass
class _Contexto:
    """O que a rodada de decisões já leu (uma consulta por tipo, não por linha)."""

    agora: datetime
    regras: dict[tuple[str, UUID], AtendimentoAutomacaoRegra]
    canais: dict[tuple[UUID, str], AtendimentoCanal]
    conversas: dict[UUID, AtendimentoConversa]
    msgs: dict[UUID, list[cat.Msg]]
    registro: dict[UUID, list[cat.Linha]]
    contagem: dict[tuple[UUID, str, str], int]
    # Loja → automação em `simular` → o corte do modo seco (`cortes_do_modo_seco`).
    cortes: dict[UUID, dict[str, datetime]]


async def aposentar_enviando_presos(session: AsyncSession, *, agora: datetime) -> int:
    """`enviando` há mais de 10 min (processo morto no meio) vira `revisar`. Nunca retenta."""
    resultado = await session.execute(
        update(_R)
        .where(_R.estado == cat.ESTADO_ENVIANDO, _R.updated_at < agora - ENVIANDO_PRESO)
        .values(estado=cat.ESTADO_REVISAR, erro="envio_interrompido", updated_at=agora)
        .execution_options(synchronize_session=False)
    )
    n = int(resultado.rowcount or 0)
    if n:
        logger.warning("atendimento_automacoes_envio_preso_revisar", quantidade=n)
    return n


async def _contagens_do_dia(
    session: AsyncSession, *, agora: datetime
) -> dict[tuple[UUID, str, str], int]:
    """(loja, família ou automação, "seco"/"envio") → linhas decididas em 24 h corridas."""
    linhas = (
        await session.execute(
            select(_R.integration_id, _R.automacao, _R.estado, func.count())
            .where(
                _R.decidido_em >= agora - timedelta(hours=24),
                _R.estado.in_(
                    (
                        cat.ESTADO_SIMULADO,
                        cat.ESTADO_ENVIANDO,
                        cat.ESTADO_ENVIADO,
                        cat.ESTADO_REVISAR,
                    )
                ),
            )
            .group_by(_R.integration_id, _R.automacao, _R.estado)
        )
    ).all()
    saida: dict[tuple[UUID, str, str], int] = defaultdict(int)
    for integ, codigo, estado, n in linhas:
        aut = cat.CATALOGO.get(codigo)
        if aut is None:
            continue
        tipo = "seco" if estado == cat.ESTADO_SIMULADO else "envio"
        saida[(integ, aut.familia, tipo)] += int(n)
        saida[(integ, codigo, tipo)] += int(n)
    return saida


def _fatos_comuns(ctx: _Contexto, aut: cat.Automacao, linha: AtendimentoAutomacaoRegistro) -> dict:
    canal = ctx.canais.get((linha.integration_id, aut.canal))
    conversa = ctx.conversas.get(linha.conversa_id) if linha.conversa_id else None
    fatos: dict[str, Any] = {
        "canal_sem_acesso": canal is not None and canal.status in _STATUS_SEM_ACESSO,
        "canal_erro": canal is not None and canal.status == "erro",
        "canal_auto": canal is not None and canal.modo == MODO_AUTO,
        "conversa_bloqueada": conversa is not None and conversa.situacao == CONVERSA_BLOQUEADA,
        "etiqueta": conversa.etiqueta if conversa is not None else None,
    }
    if conversa is not None and aut.plataforma == "ml":
        dados = conversa.dados if isinstance(conversa.dados, dict) else {}
        fatos["via_agente"] = bool(dados.get("via_agente"))
        substatus = str(dados.get("substatus_ml") or "").strip().lower()
        fatos["reclamacao_ml"] = reclamacao_aberta(dados) and substatus in (
            "blocked_by_claim",
            "blocked_by_mediation",
        )
    return fatos


async def _fatos_de_reclamacao(
    session: AsyncSession, linhas: list[AtendimentoAutomacaoRegistro]
) -> dict[UUID, dict]:
    """Reclamação/devolução (aberta, e qualquer uma) por linha: pelo pedido e pela conversa."""
    pedidos = sorted({x.pedido for x in linhas if x.pedido})
    conversas = sorted({x.conversa_id for x in linhas if x.conversa_id}, key=str)
    if not pedidos and not conversas:
        return {}
    filtro = []
    if pedidos:
        filtro.append(_Rc.pedido_marketplace.in_(pedidos))
    if conversas:
        filtro.append(_Rc.conversa_id.in_(conversas))
    recl = (
        await session.execute(
            select(_Rc.pedido_marketplace, _Rc.conversa_id, _Rc.tipo, _Rc.encerrada_em).where(
                or_(*filtro)
            )
        )
    ).all()
    saida: dict[UUID, dict] = {}
    for x in linhas:
        # A linha do PEDIDO olha só o pedido (a conversa do comprador pode ter a
        # devolução de OUTRO pedido); a da conversa, a conversa.
        if x.alvo == cat.ALVO_PEDIDO:
            casam = [r for r in recl if x.pedido and r.pedido_marketplace == x.pedido]
        else:
            casam = [r for r in recl if x.conversa_id and r.conversa_id == x.conversa_id]
        abertas = [r for r in casam if r.encerrada_em is None]
        saida[x.id] = {
            "reclamacao_aberta": any(r.tipo != "devolucao" for r in abertas),
            "devolucao_aberta": any(r.tipo == "devolucao" for r in abertas),
            "devolucao_qualquer": bool(casam),
        }
    return saida


async def _fatos_de_pedido(
    session: AsyncSession, linhas: list[AtendimentoAutomacaoRegistro], *, agora: datetime
) -> dict[UUID, dict]:
    """Bling, índice, Logística e avaliação dos pedidos das linhas."""
    com_pedido = [x for x in linhas if x.pedido]
    if not com_pedido:
        return {}
    pedidos = sorted({x.pedido for x in com_pedido})
    bling = (
        await session.execute(
            select(_B.numeroloja, _B.situacao, _B.data).where(_B.numeroloja.in_(pedidos))
        )
    ).all()
    cancelados = {r.numeroloja for r in bling if (r.situacao or "") in _SITUACOES_CANCELADO}
    data_bling: dict[str, datetime] = {}
    for r in bling:
        if r.data is not None:
            d = _utc(r.data)
            data_bling[r.numeroloja] = min(d, data_bling.get(r.numeroloja, d))
    logi = (
        await session.execute(
            select(
                _L.pedido_marketplace,
                _L.meli_status["order_status"].astext,
                _L.meli_status["return_status"].astext,
                _L.data,
            ).where(
                _L.pedido_marketplace.in_(pedidos), func.lower(func.trim(_L.plataforma)) == "shopee"
            )
        )
    ).all()
    logistica = {r[0]: (r[1], r[2], r[3]) for r in logi}
    indice = await _compradores_do_pedido(
        session, [(x.integration_id, x.pedido) for x in com_pedido]
    )
    avaliados = {
        (r.integration_id, r.pedido)
        for r in (
            await session.execute(
                select(_A.integration_id, _A.pedido).where(
                    _A.pedido.in_(pedidos), _A.criado_em <= agora
                )
            )
        ).all()
    }
    saida: dict[UUID, dict] = {}
    for x in com_pedido:
        status_logi, retorno, data_logi = logistica.get(x.pedido, (None, None, None))
        idx = indice.get((x.integration_id, x.pedido))
        criado = data_bling.get(x.pedido) or (idx[2] if idx else None)
        if criado is None and data_logi is not None:
            criado = datetime(data_logi.year, data_logi.month, data_logi.day, tzinfo=UTC)
        saida[x.id] = {
            "pedido_cancelado": x.pedido in cancelados
            or bool(idx and (idx[1] or "") in _STATUS_INDICE_CANCELADO),
            "status_pedido": status_logi,
            "devolucao_logistica": bool(retorno) or status_logi == "TO_RETURN",
            "avaliou": (x.integration_id, x.pedido) in avaliados,
            "pedido_criado_em": criado,
            "tem_logistica": x.pedido in logistica,
        }
    return saida


async def _ja_recebeu_convite(
    session: AsyncSession, linha: AtendimentoAutomacaoRegistro, conversa: AtendimentoConversa | None
) -> bool:
    """O comprador já recebeu o convite ANTES do gatilho, em qualquer época (Duoke ou nosso)."""
    if conversa is None:
        return False
    alvos = [_C.id == conversa.id]
    if conversa.comprador_id and conversa.integration_id:
        alvos.append(
            and_(
                _C.integration_id == conversa.integration_id,
                _C.comprador_id == conversa.comprador_id,
            )
        )
    achou = await session.scalar(
        select(_M.id)
        .join(_C, _C.id == _M.conversa_id)
        .where(
            or_(*alvos),
            _M.autor != AUTOR_CLIENTE,
            _momento() < linha.evento_em,
            or_(
                func.lower(func.left(_M.texto, 300)).like("%segue nossa loja aqui%"),
                _M.payload[("automacao", "codigo")].astext.in_(
                    ("shopee_convite", "tiktok_convite")
                ),
            ),
        )
        .limit(1)
    )
    return achou is not None


async def _nao_e_primeira(session: AsyncSession, linha: AtendimentoAutomacaoRegistro) -> bool:
    """TikTok: o comprador já tinha escrito nesta conversa antes do gatilho (em qualquer época)?"""
    if linha.conversa_id is None or linha.evento_em is None:
        return False
    achou = await session.scalar(
        select(_M.id)
        .where(
            _M.conversa_id == linha.conversa_id,
            _M.autor == AUTOR_CLIENTE,
            _momento() < linha.evento_em,
        )
        .limit(1)
    )
    return achou is not None


async def _ja_comprou(session: AsyncSession, linha: AtendimentoAutomacaoRegistro, conversa) -> bool:
    """Shopee: pedido no índice (não `UNPAID`) para (loja, comprador) ou pedido na conversa."""
    if conversa is not None and conversa.pedido_marketplace:
        return True
    comprador = linha.comprador_id or (conversa.comprador_id if conversa is not None else None)
    if not comprador or linha.plataforma != "shopee":
        return False
    achou = await session.scalar(
        select(_P.id)
        .where(
            _P.integration_id == linha.integration_id,
            _P.comprador_id == comprador,
            func.coalesce(_P.status, "") != "UNPAID",
        )
        .limit(1)
    )
    return achou is not None


def _modo_efetivo(regra: AtendimentoAutomacaoRegra) -> tuple[str, str | None]:
    """(simular|enviar, motivo do simulado) — `enviar` só com as duas chaves ligadas."""
    s = get_settings()
    if regra.modo != cat.MODO_ENVIAR:
        return cat.MODO_SIMULAR, None
    if not (s.atendimento_automacoes_envio and s.atendimento_envio_ativo):
        return cat.MODO_SIMULAR, "envio_desligado"
    return cat.MODO_ENVIAR, None


def _em_transicao(regra: AtendimentoAutomacaoRegra, agora: datetime) -> bool:
    """Os primeiros dias da regra em `enviar` (`TRANSICAO`): o Duoke pode ainda mandar."""
    desde = _utc(regra.enviar_desde)
    return desde is None or agora - desde <= TRANSICAO


def _teto(regra: AtendimentoAutomacaoRegra, aut: cat.Automacao, ctx: _Contexto, tipo: str) -> bool:
    """Passou do teto do dia (loja × família, e o da própria regra)?"""
    s = get_settings()
    teto_loja = int(s.atendimento_automacoes_teto_dia or 0)
    if teto_loja and ctx.contagem[(regra.integration_id, aut.familia, tipo)] >= teto_loja:
        return True
    return (
        bool(regra.teto_dia)
        and ctx.contagem[(regra.integration_id, aut.codigo, tipo)] >= regra.teto_dia
    )


def _teto_comprador(
    ctx: _Contexto, aut: cat.Automacao, linha: AtendimentoAutomacaoRegistro
) -> bool:
    """Já saíram (ou sairiam) 3 mensagens normais do DaVinci sem resposta do comprador."""
    if aut.campanha or linha.conversa_id is None:
        return False
    msgs = ctx.msgs.get(linha.conversa_id, [])
    ultima = max((m.em for m in msgs if m.do_comprador and m.em <= ctx.agora), default=None)
    if ultima is None:
        return False
    nossas = [
        x
        for x in ctx.registro.get(linha.conversa_id, [])
        if x.chave != linha.chave
        and x.estado
        in (cat.ESTADO_SIMULADO, cat.ESTADO_ENVIANDO, cat.ESTADO_ENVIADO, cat.ESTADO_REVISAR)
        and x.devido_em > ultima
        and not cat.CATALOGO.get(x.automacao, aut).campanha
    ]
    return len(nossas) >= TETO_COMPRADOR


def _fechar(
    linha: AtendimentoAutomacaoRegistro,
    *,
    estado: str,
    agora: datetime,
    modo: str | None,
    motivo: str | None = None,
    regra: AtendimentoAutomacaoRegra | None = None,
    divergencia: str | None = None,
) -> None:
    linha.estado = estado
    linha.decidido_em = agora
    linha.modo = modo
    linha.motivo = motivo
    if regra is not None:
        linha.regra_versao = regra.versao
        linha.regra_id = regra.id
    if divergencia:
        linha.divergencia = divergencia
    linha.updated_at = agora


async def _seguinte(
    session: AsyncSession, ctx: _Contexto, aut: cat.Automacao, linha: AtendimentoAutomacaoRegistro
) -> None:
    """O 2 h saiu (simulado ou enviado): nasce o 26 h / 24 h, 24 h depois."""
    if not aut.seguinte:
        return
    regra = ctx.regras.get((aut.seguinte, linha.integration_id))
    if regra is None or regra.modo == cat.MODO_DESLIGADO:
        return
    seguinte = cat.CATALOGO[aut.seguinte]
    devido = cat.ajustar_janela(
        _utc(linha.devido_em) + timedelta(minutes=regra.atraso_min),
        *(cat.janela_da_regra(regra, seguinte) or (None, None)),
    )
    await _gravar_candidatos(
        session,
        [
            _linha_registro(
                seguinte,
                regra,
                chave=linha.chave,
                agora=ctx.agora,
                evento_em=_utc(linha.evento_em),
                devido_em=devido,
                conversa_id=linha.conversa_id,
                gatilho_mensagem_id=linha.gatilho_mensagem_id,
                comprador_id=linha.comprador_id,
            )
        ],
    )


async def disparar_disjuntor(
    session: AsyncSession, regra: AtendimentoAutomacaoRegra, *, agora: datetime, motivo: str
) -> None:
    """A regra em `enviar` volta sozinha para `simular`: o comprador já recebeu duas."""
    if regra.modo != cat.MODO_ENVIAR:
        return
    regra.modo = cat.MODO_SIMULAR
    regra.disjuntor_em = agora
    regra.disjuntor_motivo = motivo[:48]
    regra.enviar_desde = None
    logger.warning(
        "atendimento_automacoes_disjuntor",
        automacao=regra.automacao,
        integration_id=str(regra.integration_id),
        motivo=motivo,
    )


async def _enviar(
    session: AsyncSession,
    ctx: _Contexto,
    aut: cat.Automacao,
    regra: AtendimentoAutomacaoRegra,
    linha: AtendimentoAutomacaoRegistro,
    partes: list[dict],
) -> None:
    """O modo enviar: o registro `enviando` commitado ANTES, depois parte a parte."""
    # Import TARDIO de propósito: o modo seco nunca carrega o envio (o teste prova).
    from app.services.atendimento import enviar as envio

    conversa = ctx.conversas.get(linha.conversa_id) if linha.conversa_id else None
    if conversa is None:
        _fechar(
            linha,
            estado=cat.ESTADO_PULADO,
            agora=ctx.agora,
            modo=cat.MODO_ENVIAR,
            motivo="sem_conversa",
            regra=regra,
        )
        return
    linha.estado = cat.ESTADO_ENVIANDO
    linha.modo = cat.MODO_ENVIAR
    linha.tentativas = (linha.tentativas or 0) + 1
    linha.regra_versao = regra.versao
    linha.updated_at = ctx.agora
    await session.commit()
    ids: list[str] = list(linha.mensagem_ids or [])
    textos = [p for p in partes if p.get("tipo") == "texto"]
    for i, parte in enumerate(textos):
        try:
            mensagem = await envio.enviar_automatica(
                session,
                conversa,
                codigo=aut.codigo,
                texto=parte["texto"],
                registro_id=linha.id,
                regra_versao=regra.versao,
                indice=i,
            )
        except envio.EnvioRecusado as recusa:
            await session.refresh(linha)
            if i == 0 and recusa.code in envio.RECUSAS_TEMPORARIAS:
                linha.estado = cat.ESTADO_AGENDADO
                linha.updated_at = _agora()
                await session.commit()
                return
            if i == 0:
                _fechar(
                    linha,
                    estado=cat.ESTADO_PULADO,
                    agora=_agora(),
                    modo=cat.MODO_ENVIAR,
                    motivo="envio_recusado",
                    regra=regra,
                )
            else:
                _fechar(
                    linha,
                    estado=cat.ESTADO_FALHOU,
                    agora=_agora(),
                    modo=cat.MODO_ENVIAR,
                    motivo=f"parte_{i + 1}",
                    regra=regra,
                )
            linha.erro = recusa.code[:200]
            linha.mensagem_ids = ids
            await session.commit()
            return
        ids.append(str(mensagem.id))
        await session.refresh(linha)
        linha.mensagem_ids = ids
        if mensagem.status != "enviada":
            estado = (
                cat.ESTADO_REVISAR
                if mensagem.status in ("revisar", "enviando")
                else cat.ESTADO_FALHOU
            )
            _fechar(
                linha,
                estado=estado,
                agora=_agora(),
                modo=cat.MODO_ENVIAR,
                motivo=None if i == 0 else f"parte_{i + 1}",
                regra=regra,
            )
            linha.erro = (mensagem.erro or mensagem.status or "")[:200]
            await session.commit()
            return
        await session.commit()
    _fechar(linha, estado=cat.ESTADO_ENVIADO, agora=_agora(), modo=cat.MODO_ENVIAR, regra=regra)
    linha.mensagem_ids = ids
    ctx.contagem[(regra.integration_id, aut.familia, "envio")] += 1
    ctx.contagem[(regra.integration_id, aut.codigo, "envio")] += 1
    await _seguinte(session, ctx, aut, linha)
    await session.commit()


async def _decidir_uma(
    session: AsyncSession,
    ctx: _Contexto,
    linha: AtendimentoAutomacaoRegistro,
    extras: dict,
) -> str:
    """Decide UMA linha vencida; devolve o estado em que ela ficou."""
    aut = cat.CATALOGO.get(linha.automacao)
    regra = ctx.regras.get((linha.automacao, linha.integration_id))
    agora = ctx.agora
    if aut is None or regra is None or regra.modo == cat.MODO_DESLIGADO:
        _fechar(
            linha,
            estado=cat.ESTADO_PULADO,
            agora=agora,
            modo=None,
            motivo="regra_desligada",
            divergencia="regra_desligada",
        )
        return linha.estado
    janela = cat.janela_da_regra(regra, aut)
    validade = cat.validade_ate(aut, linha.devido_em, janela)
    # Fora do horário da regra AGORA (a linha esperou a leitura, o motor
    # parou, o PATCH rearmou): espera a próxima abertura, se ainda valer.
    abre = cat.ajustar_janela(agora, *janela) if janela else agora
    if agora > validade or abre > validade:
        _fechar(
            linha,
            estado=cat.ESTADO_PULADO,
            agora=agora,
            modo=regra.modo,
            motivo="atrasado",
            regra=regra,
            divergencia="motor_atrasado",
        )
        return linha.estado
    if abre > agora:
        linha.devido_em = abre
        linha.updated_at = agora
        return linha.estado
    modo, motivo_simulado = _modo_efetivo(regra)
    fatos = _fatos_comuns(ctx, aut, linha)
    fatos.update(extras)
    conversa = ctx.conversas.get(linha.conversa_id) if linha.conversa_id else None
    fatos["tem_conversa"] = conversa is not None
    if linha.conversa_id and linha.evento_em is not None and aut.alvo != cat.ALVO_PEDIDO:
        fatos.update(
            cat.fatos_da_conversa(
                aut,
                msgs=ctx.msgs.get(linha.conversa_id, []),
                registro=ctx.registro.get(linha.conversa_id, []),
                chave=linha.chave,
                evento_em=linha.evento_em,
                agora=agora,
                regra=regra,
                cortes=ctx.cortes.get(linha.integration_id),
            )
        )
    if aut.plataforma == "shopee":
        if aut.alvo == cat.ALVO_PEDIDO:
            criado = fatos.get("pedido_criado_em") or _utc(linha.evento_em)
            ultima_cliente = _utc(conversa.ultima_do_cliente_em) if conversa is not None else None
            fatos["janela_shopee_ok"] = bool(
                (criado is not None and agora - criado <= JANELA_SHOPEE_PEDIDO)
                or (
                    ultima_cliente is not None and agora - ultima_cliente <= JANELA_SHOPEE_COMPRADOR
                )
                or fatos.get("devolucao_aberta")
            )
        else:
            fatos["janela_shopee_ok"] = True
    if modo == cat.MODO_ENVIAR and fatos.get("canal_erro"):
        return linha.estado  # a leitura da loja falhou agora: tenta na próxima rodada
    motivo = cat.decidir(aut, fatos, regra)
    if motivo is None and modo == cat.MODO_ENVIAR:
        # A troca não manda em dobro: na transição, espera a leitura da loja
        # passar do `devido_em` + a espera do Duoke; e confere se ele mandou
        # DEPOIS do gatilho (na conversa, ou — nas do pedido — pelo cartão do
        # pedido ou pela conversa do pedido: `duoke_dos_pedidos`).
        if _em_transicao(regra, agora):
            espera = _utc(linha.devido_em) + aut.espera_duoke
            canal = ctx.canais.get((linha.integration_id, aut.canal))
            lido = _utc(canal.ultimo_ok_em) if canal is not None else None
            if agora < espera or lido is None or lido < espera:
                return linha.estado
        duoke = fatos.get("duoke_depois") or []
        if duoke:
            depois_da_troca = regra.enviar_desde is not None and any(
                t >= _utc(regra.enviar_desde) for t in duoke
            )
            _fechar(
                linha,
                estado=cat.ESTADO_PULADO,
                agora=agora,
                modo=modo,
                motivo="duoke_mandou",
                regra=regra,
            )
            if depois_da_troca:
                # NA HORA: o resto do lote desta regra já decide em `simular`.
                await disparar_disjuntor(session, regra, agora=agora, motivo="duoke_ainda_ligado")
            return linha.estado
        if aut.campanha:
            # Import TARDIO (o modo seco nunca carrega o envio): a campanha só
            # sai como resposta automática — chave confirmada E o envio por
            # ela no adaptador. Sem os dois, sairia como mensagem normal.
            from app.services.atendimento import enviar as envio

            if not envio.campanha_por_auto_reply():
                motivo = "campanha_sem_auto_reply"
    if motivo is not None:
        _fechar(
            linha,
            estado=cat.ESTADO_PULADO,
            agora=agora,
            modo=modo,
            motivo=motivo,
            regra=regra,
            divergencia=cat.divergencia_do_motivo(motivo, aut),
        )
        return linha.estado
    comprador = conversa.comprador_nome if conversa is not None else None
    partes, motivos = cat.renderizar(
        regra.partes or cat.partes_padrao(aut),
        comprador=comprador,
        plataforma=aut.plataforma,
        canal=aut.canal,
    )
    if motivos:
        _fechar(
            linha,
            estado=cat.ESTADO_PULADO,
            agora=agora,
            modo=modo,
            motivo="texto_invalido",
            regra=regra,
        )
        return linha.estado
    tipo = "envio" if modo == cat.MODO_ENVIAR else "seco"
    teto = (
        "teto_dia"
        if _teto(regra, aut, ctx, tipo)
        else ("teto_comprador" if _teto_comprador(ctx, aut, linha) else None)
    )
    if teto:
        logger.warning(
            "atendimento_automacoes_teto",
            automacao=aut.codigo,
            integration_id=str(regra.integration_id),
            teto=teto,
            modo=modo,
        )
        _fechar(
            linha,
            estado=cat.ESTADO_PULADO,
            agora=agora,
            modo=modo,
            motivo=teto,
            regra=regra,
            divergencia=teto if modo == cat.MODO_SIMULAR else None,
        )
        return linha.estado
    if modo == cat.MODO_ENVIAR:
        await _enviar(session, ctx, aut, regra, linha, partes)
        return linha.estado
    _fechar(
        linha,
        estado=cat.ESTADO_SIMULADO,
        agora=agora,
        modo=cat.MODO_SIMULAR,
        motivo=motivo_simulado,
        regra=regra,
    )
    ctx.contagem[(regra.integration_id, aut.familia, "seco")] += 1
    ctx.contagem[(regra.integration_id, aut.codigo, "seco")] += 1
    if linha.conversa_id:
        # A próxima linha desta conversa na mesma rodada já vê esta como saída.
        ctx.registro.setdefault(linha.conversa_id, []).append(
            cat.Linha(
                linha.automacao,
                linha.estado,
                linha.chave,
                _utc(linha.evento_em),
                _utc(linha.devido_em),
                linha.gatilho_mensagem_id,
            )
        )
    await _seguinte(session, ctx, aut, linha)
    return linha.estado


async def decidir_vencidas(
    session: AsyncSession, *, agora: datetime, motor_desde: datetime | None = None
) -> dict[str, int]:
    """As linhas `agendado` que venceram: até `MAX_DECISOES` por rodada, a mais
    velha primeiro, no máximo `MAX_POR_REGRA` da mesma loja × automação."""
    ordem = (
        func.row_number()
        .over(partition_by=(_R.integration_id, _R.automacao), order_by=(_R.devido_em, _R.id))
        .label("n")
    )
    fila = (
        select(_R.id.label("id"), ordem)
        .where(_R.estado == cat.ESTADO_AGENDADO, _R.devido_em <= agora)
        .subquery()
    )
    linhas = list(
        (
            await session.execute(
                select(_R)
                .join(fila, fila.c.id == _R.id)
                .where(fila.c.n <= MAX_POR_REGRA, _R.estado == cat.ESTADO_AGENDADO)
                .order_by(_R.devido_em)
                .limit(MAX_DECISOES)
                .with_for_update(skip_locked=True, of=_R)
            )
        )
        .scalars()
        .all()
    )
    contagem: dict[str, int] = defaultdict(int)
    if not linhas:
        return dict(contagem)
    regras = await regras_por_chave(session, ativas=False)
    integs = sorted({x.integration_id for x in linhas}, key=str)
    canais = {
        (c.integration_id, c.canal): c
        for c in (
            await session.execute(
                select(AtendimentoCanal)
                .where(AtendimentoCanal.integration_id.in_(integs))
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    }
    # A linha do pedido sem conversa na descoberta: a conversa pode ter nascido
    # depois (o comprador escreveu, ou o cartão do Duoke abriu) — pelo pedido
    # ligado ou pelo comprador do índice.
    sem_conversa = [x for x in linhas if x.conversa_id is None and x.pedido]
    if sem_conversa:
        achadas = await _conversas_do_pedido(
            session, [(x.integration_id, x.pedido) for x in sem_conversa], agora=agora
        )
        pelo_comprador = await _conversas_do_comprador(
            session, [(x.integration_id, x.comprador_id) for x in sem_conversa if x.comprador_id]
        )
        for x in sem_conversa:
            achada = achadas.get((x.integration_id, x.pedido)) or (
                pelo_comprador.get((x.integration_id, x.comprador_id)) if x.comprador_id else None
            )
            if achada is not None:
                x.conversa_id = achada
    conversa_ids = sorted({x.conversa_id for x in linhas if x.conversa_id}, key=str)
    conversas = (
        {
            c.id: c
            for c in (
                await session.execute(
                    select(AtendimentoConversa)
                    .where(AtendimentoConversa.id.in_(conversa_ids))
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        }
        if conversa_ids
        else {}
    )
    por_loja: dict[UUID, list[AtendimentoAutomacaoRegra]] = defaultdict(list)
    for (_codigo, integ), regra in regras.items():
        por_loja[integ].append(regra)
    ctx = _Contexto(
        agora=agora,
        regras=regras,
        canais=canais,
        conversas=conversas,
        msgs=await linha_do_tempo(session, conversa_ids, agora=agora),
        registro=await _registro_das_conversas(session, conversa_ids, agora=agora),
        contagem=await _contagens_do_dia(session, agora=agora),
        cortes={
            integ: cat.cortes_do_modo_seco(por_loja.get(integ, []), motor_desde)
            for integ in {x.integration_id for x in linhas}
        },
    )
    reclamacoes = await _fatos_de_reclamacao(session, linhas)
    pedidos = await _fatos_de_pedido(session, linhas, agora=agora)
    # Modo enviar nas do PEDIDO: o Duoke já mandou para este pedido depois do
    # gatilho? (A conversa tem o `duoke_depois` dela; o pedido, este.)
    do_pedido_enviar = []
    for x in linhas:
        aut = cat.CATALOGO.get(x.automacao)
        regra = regras.get((x.automacao, x.integration_id))
        if (
            aut is not None
            and aut.alvo == cat.ALVO_PEDIDO
            and x.pedido
            and regra is not None
            and _modo_efetivo(regra)[0] == cat.MODO_ENVIAR
        ):
            do_pedido_enviar.append(x)
    duoke_dos_pedidos: dict[UUID, list[datetime]] = {}
    if do_pedido_enviar:
        from app.services.atendimento import automacoes_comparar

        duoke_dos_pedidos = await automacoes_comparar.duoke_dos_pedidos(
            session, do_pedido_enviar, agora=agora
        )
    for linha in linhas:
        aut = cat.CATALOGO.get(linha.automacao)
        extras: dict[str, Any] = {}
        extras.update(reclamacoes.get(linha.id, {}))
        extras.update(pedidos.get(linha.id, {}))
        if extras.get("devolucao_logistica"):
            extras["devolucao_qualquer"] = True
        if linha.id in duoke_dos_pedidos:
            extras["duoke_depois"] = duoke_dos_pedidos[linha.id]
        conversa = conversas.get(linha.conversa_id) if linha.conversa_id else None
        if aut is not None and aut.tipo == cat.TIPO_CONVITE and linha.evento_em is not None:
            extras["ja_recebeu"] = await _ja_recebeu_convite(session, linha, conversa)
            if aut.plataforma == "tiktok":
                extras["nao_e_primeira"] = await _nao_e_primeira(session, linha)
        if aut is not None and aut.tipo in (cat.TIPO_DUVIDA_1, cat.TIPO_DUVIDA_2):
            extras["ja_comprou"] = await _ja_comprou(session, linha, conversa)
        estado = await _decidir_uma(session, ctx, linha, extras)
        contagem[estado] += 1
    await session.commit()
    return dict(contagem)


async def limpar_registro(session: AsyncSession, *, agora: datetime) -> int:
    """Apaga do registro o que passou de `GUARDA_REGISTRO` (em lotes; nunca o que ainda decide)."""
    velhas = (
        select(_R.id)
        .where(
            _R.devido_em < agora - GUARDA_REGISTRO,
            _R.estado.not_in((cat.ESTADO_AGENDADO, cat.ESTADO_ENVIANDO)),
        )
        .limit(LIMPEZA_LOTE)
        .scalar_subquery()
    )
    resultado = await session.execute(
        delete(_R).where(_R.id.in_(velhas)).execution_options(synchronize_session=False)
    )
    n = int(resultado.rowcount or 0)
    if n:
        logger.info("atendimento_automacoes_registro_limpo", quantidade=n)
    return n


# ── A rodada ──────────────────────────────────────────────────────────────


async def _motor_desde(agora: datetime) -> datetime:
    """Desde quando o motor roda sem parar (Redis; sem Redis, agora menos o lookback)."""
    chave = _CHAVE_MOTOR.format(get_settings().database_schema)
    try:
        await redis.set(chave, agora.isoformat(), nx=True)
        valor = await redis.get(chave)
    except Exception as e:  # noqa: BLE001
        logger.warning("atendimento_automacoes_motor_desde_indisponivel", err=type(e).__name__)
        return agora - LOOKBACK_MENSAGENS
    return _iso(valor) or agora


async def esquecer_motor() -> None:
    """O motor desligado de propósito: ao religar, o "só Duoke" recomeça do zero."""
    try:
        await redis.delete(_CHAVE_MOTOR.format(get_settings().database_schema))
    except Exception:  # noqa: BLE001, S110 — acessório
        pass


async def rodada(*, agora: datetime | None = None, motor_desde: datetime | None = None) -> dict:
    """Descobrir → aposentar `enviando` preso → decidir → comparar.

    Devolve o resumo: contagens por fase e a duração de cada uma (ms).
    """
    from app.services.atendimento import automacoes_comparar

    agora = _utc(agora) or _agora()
    motor_desde = _utc(motor_desde) or await _motor_desde(agora)
    resumo: dict[str, Any] = {}
    ms: dict[str, int] = {}

    def _marca(fase: str, t0: float) -> None:
        ms[fase] = int((_time.monotonic() - t0) * 1000)

    async with _db.SessionLocal() as session:
        regras = await regras_por_chave(session)
        if regras:
            t0 = _time.monotonic()
            resumo["mensagens"] = await descobrir_mensagens(
                session, regras, agora=agora, motor_desde=motor_desde
            )
            await session.commit()
            _marca("mensagens", t0)
            t0 = _time.monotonic()
            resumo["pedidos"] = await descobrir_pedidos_pagos(
                session, regras, agora=agora, motor_desde=motor_desde
            )
            await session.commit()
            _marca("pedidos", t0)
            if agora.minute % LOGISTICA_A_CADA_MIN in (0, 1):
                t0 = _time.monotonic()
                resumo["logistica"] = await descobrir_logistica(
                    session, regras, agora=agora, motor_desde=motor_desde
                )
                await session.commit()
                _marca("logistica", t0)
        t0 = _time.monotonic()
        resumo["presos"] = await aposentar_enviando_presos(session, agora=agora)
        await session.commit()
        resumo["decididas"] = await decidir_vencidas(session, agora=agora, motor_desde=motor_desde)
        _marca("decidir", t0)
        t0 = _time.monotonic()
        resumo["comparadas"] = await automacoes_comparar.comparar(
            session, agora=agora, motor_desde=motor_desde
        )
        await session.commit()
        _marca("comparar", t0)
        if agora.hour == HORA_LIMPEZA_UTC and agora.minute in (30, 31):
            t0 = _time.monotonic()
            resumo["limpas"] = await limpar_registro(session, agora=agora)
            await session.commit()
            _marca("limpeza", t0)
    resumo["ms"] = ms
    return resumo


async def _trava_da_rodada() -> tuple[bool, str | None]:
    """SET NX da rodada (por schema). Redis fora do ar = NÃO roda (envio é o caso caro)."""
    chave = _CHAVE_RODADA.format(get_settings().database_schema)
    token = uuid4().hex
    try:
        pegou = await redis.set(chave, token, nx=True, ex=RODADA_TTL_S)
    except Exception as e:  # noqa: BLE001
        logger.warning("atendimento_automacoes_trava_indisponivel", err=type(e).__name__)
        return False, None
    return bool(pegou), token


async def _soltar_rodada(token: str | None) -> None:
    if token is None:
        return
    chave = _CHAVE_RODADA.format(get_settings().database_schema)
    try:
        await redis.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end",
            1,
            chave,
            token,
        )
    except Exception:  # noqa: BLE001 — o TTL solta sozinho
        logger.warning("atendimento_automacoes_trava_soltar_falhou")


async def atendimento_automacoes(ctx: dict) -> dict | None:
    """Minutos pares: uma rodada do motor. Só com a leitura E `atendimento_automacoes_ativa`.

    Uma rodada por vez (trava no Redis, 110 s). Nunca levanta. O log leva só
    contagens e a duração de cada fase.
    """
    s = get_settings()
    if not (s.atendimento_leitura_ativa and s.atendimento_automacoes_ativa):
        await esquecer_motor()
        return None
    pegou, token = await _trava_da_rodada()
    if not pegou:
        logger.info("atendimento_automacoes_rodada_ocupada")
        return None
    try:
        resumo = await rodada()
    except Exception as e:  # noqa: BLE001
        logger.error("atendimento_automacoes_falhou", err=type(e).__name__)
        return None
    finally:
        await _soltar_rodada(token)
    logger.info(
        "atendimento_automacoes_tick",
        **{k: v for k, v in resumo.items() if k != "ms"},
        ms=resumo.get("ms"),
    )
    return resumo
