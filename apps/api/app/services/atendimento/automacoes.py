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
         motor vê (o status anterior se perde); os pedidos FORA da Logística
         (Resolvido/Cancelado/Perdimento no Bling) pelo índice do cartão
         "Cliente" (`descobrir_indice`), com a mesma chave;
       • (05/10, noite — SÓ SIMULAÇÃO, `Automacao.so_simular`) o pedido
         criado e não pago pelo índice (`descobrir_nao_pagos`, o "carrinho"
         com cupom; o índice é de hora em hora), a avaliação pela leitura das
         avaliações (`descobrir_avaliacoes`) e o aviso de pedido da TikTok pela
         leitura da caixa (`descobrir_tiktok_pedidos`).
     Grava `INSERT … ON CONFLICT DO NOTHING` pela chave única.
  2. DECIDIR — as linhas que venceram (até 200 por rodada, no máximo 50 da
     mesma regra: a fila parada de uma loja não segura as outras): validade,
     HORÁRIO (fora da janela da regra, a linha espera a próxima abertura — o
     da descoberta não basta: a linha pode esperar a leitura, o motor parado,
     o rearme), condições (`automacoes_catalogo.decidir`), texto, teto. Regra
     em `simular` → `simulado`; em `enviar` com as chaves desligadas →
     `simulado` com `envio_desligado`; com tudo ligado → RECONFERE tudo
     relido do banco (`_reconferir`) e envia pelo `enviar.enviar_automatica`
     uma PARTE por vez (o cartão do pedido, o texto, a figurinha); sem
     conversa no DaVinci (Shopee, linha do pedido) a 1ª parte vai pelo
     `to_id` do comprador (`enviar.enviar_automatica_sem_conversa`). A
     mensagem pela metade (uma parte saiu e a seguinte não) vira `revisar` e
     dispara o disjuntor (`_parou`).
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
sem a chave confirmada E o envio por ela no adaptador
(`enviar_parte(auto_reply=True)`), `campanha_sem_auto_reply`.

O log leva só ids, contagens, códigos e a duração de cada fase. Texto de
comprador nunca sai daqui: a classificação lê o texto e devolve só sinais.
"""

from __future__ import annotations

import asyncio
import time as _time
from collections import defaultdict
from dataclasses import dataclass, field
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
    IntegrationPlatform,
    Logistica,
    MarketplaceOrderFinancial,
    Store,
)
from app.redis_client import redis
from app.services.atendimento import automacoes_catalogo as cat
from app.services.atendimento.constantes import (
    AUTOR_CLIENTE,
    AUTOR_SISTEMA,
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
# Nenhum envio NOVO começa depois disto (segundos desde o começo da rodada):
# a rodada tem 110 s (timeout do arq) e um envio cortado no meio das partes
# deixa o cartão do pedido sem o texto. A linha fica `agendado` para a
# próxima rodada (2 min); o modo seco não chama ninguém e não tem prazo.
ORCAMENTO_ENVIO_S = 50
# A 2ª parte em diante recusada por um motivo que passa sozinho (a leitura
# gravando a conversa que o cartão acabou de abrir — `conversa_ocupada`):
# tenta de novo na hora, antes de desistir com o cartão já entregue.
TENTATIVAS_PARTE = 3
ESPERA_PARTE_S = 2.0
# Recusa da PLATAFORMA que não passa sozinha (o corpo ou a permissão): na
# campanha da Shopee (o `auto_reply`, o cartão e a figurinha, formatos que só
# o 1º envio real confirma), a 1ª já volta a regra para simular.
_RECUSAS_DA_PLATAFORMA = ("error_param", "error_permission", "api_suspended", "error_forbidden")
# Recusas que são o OPERADOR desligando (as chaves, a regra): nada mais sai
# mesmo — não é defeito, não dispara o disjuntor.
_RECUSAS_FREIO = (
    "envio_desligado",
    "automacoes_envio_desligado",
    "shopee_mensagens_desligadas",
    "regra_nao_envia",
    "campanha_sem_auto_reply",
    "simulador_em_producao",
    "so_simulacao",
)
# Mensagens normais do DaVinci para o mesmo comprador sem resposta dele.
TETO_COMPRADOR = 3
# A janela de mensagem da Shopee (medida na senha da devolução).
JANELA_SHOPEE_COMPRADOR = timedelta(days=7)
JANELA_SHOPEE_PEDIDO = timedelta(days=30)
SHOPEE_PEDIDO = r"^[0-9]{6}[0-9A-Z]{8}$"
_STATUS_SEM_ACESSO = ("sem_escopo", "desligado")
_SITUACOES_CANCELADO = ("12", "excluido")
_STATUS_INDICE_CANCELADO = ("CANCELLED", "IN_CANCEL")
# Bling no fluxo de devolução (Resolvido, Aguardando Devolução...): o
# entregue e o pós não vão (`automacoes_catalogo.SITUACOES_BLING_DEVOLUCAO`).
_SITUACOES_DEVOLUCAO = cat.SITUACOES_BLING_DEVOLUCAO
# Sem conversa no DaVinci (o pedido recebido de quem nunca escreveu à loja,
# ~88% aos 5 min, medido em 05/10): o `to_id` é o comprador do pedido, que só
# o índice tem (`atendimento_indexar_pedidos`, de hora em hora no :22, janela
# de 2 h). No modo enviar, a linha espera o índice trazê-lo por até isto
# depois do `devido_em`; passou, `pulado: sem_comprador`. Nenhuma chamada nova.
ESPERA_COMPRADOR = timedelta(minutes=90)
# O índice como fonte do entregue/concluído dos pedidos FORA da Logística
# (`descobrir_indice`): as linhas atualizadas nas últimas 6 h.
LOOKBACK_INDICE = timedelta(hours=6)
_STATUS_INDICE_EVENTO = {
    "TO_CONFIRM_RECEIVE": "shopee_entregue",
    "COMPLETED": "shopee_pos_conclusao",
}
# O pedido NÃO PAGO ("carrinho" do Duoke) pelo índice (`descobrir_nao_pagos`):
# os criados nas últimas 4 h (o índice roda no :22 com janela de 2 h).
LOOKBACK_NAO_PAGO = timedelta(hours=4)
# A avaliação (`descobrir_avaliacoes`): as criadas nas últimas 24 h (a leitura
# das avaliações é a cada 30 min, e a que chega atrasada ainda vale).
LOOKBACK_AVALIACOES = timedelta(hours=24)
# O aviso de pedido da própria TikTok (papel ROBOT, gravado como `sistema`):
# o começo do texto, nas línguas que a TikTok usa (em 14 dias até 05/10/2026:
# português, português de Portugal, inglês e espanhol — "¡Gracias por tu
# pedido!" e "¡Gracias por el pedido!", com o "¡").
AVISOS_DE_PEDIDO_TIKTOK = (
    "agradecemos pelo seu pedido",
    "agradecemos a tua encomenda",
    "thanks for your order",
    "¡gracias por tu pedido",
    "¡gracias por el pedido",
    "gracias por tu pedido",
    "gracias por el pedido",
)
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
) -> dict[tuple[UUID, str], tuple[str, str | None, datetime | None, float | None]]:
    """(loja, pedido) → (comprador, status, criado_em, total) pelo índice do cartão "Cliente"."""
    if not pares:
        return {}
    pedidos = sorted({p for _, p in pares})
    linhas = (
        await session.execute(
            select(
                _P.integration_id, _P.pedido, _P.comprador_id, _P.status, _P.criado_em, _P.total
            ).where(_P.pedido.in_(pedidos))
        )
    ).all()
    return {
        (r.integration_id, r.pedido): (r.comprador_id, r.status, _utc(r.criado_em), r.total)
        for r in linhas
    }


async def _nomes_do_escrow(
    session: AsyncSession, pares: list[tuple[UUID, str]]
) -> dict[tuple[UUID, str], str]:
    """(loja, pedido) → o usuário do comprador na Shopee, pelo escrow que o
    financeiro já gravou (`marketplace_order_financials.raw.escrow.buyer_user_name`).

    Medido em 05/10/2026 (14 dias, 1.841 pedidos): é o MESMO `to_name` da
    conversa em 1.776 de 1.776 em que os dois existem, sem máscara, e chega
    segundos depois do Bling (95% dos pedidos já o têm aos 5 min). Serve para
    achar a conversa de quem escreveu ANTES de comprar e para o `{comprador}`
    do texto sem conversa. O id do comprador (`to_id`) NÃO está no escrow.
    """
    if not pares:
        return {}
    fin = MarketplaceOrderFinancial
    nome = fin.raw[("escrow", "buyer_user_name")].astext
    linhas = (
        await session.execute(
            select(fin.integration_id, fin.external_order_id, nome.label("nome")).where(
                # A chave única (platform, integration_id, external_order_id) é o índice.
                fin.platform == IntegrationPlatform.SHOPEE,
                fin.integration_id.in_(sorted({i for i, _ in pares}, key=str)),
                fin.external_order_id.in_(sorted({p for _, p in pares})),
            )
        )
    ).all()
    por_par: dict[tuple[UUID, str], str] = {}
    for r in linhas:
        limpo = " ".join(str(r.nome or "").split())
        if limpo:
            por_par[(r.integration_id, r.external_order_id)] = limpo
    return {par: por_par[par] for par in pares if par in por_par}


async def _conversas_pelo_nome(
    session: AsyncSession,
    nomes: dict[tuple[UUID, str], str],
    *,
    compradores: dict[tuple[UUID, str], str] | None = None,
) -> dict[tuple[UUID, str], UUID]:
    """(loja, pedido) → a conversa de chat da loja com o usuário do comprador (o do escrow).

    É a conversa de quem escreveu à loja ANTES de comprar (11% dos pedidos,
    medido): o pedido ainda não está ligado nela nem no índice aos 5 min.

    O nome é só a pista; quem manda é o comprador. Com o comprador do pedido
    em `compradores` (o índice), só vale a conversa DELE — a de outro
    comprador com o mesmo usuário (troca de nome na Shopee, conversa velha)
    nunca. Sem o comprador ainda, o nome só vale se for de UM comprador na
    loja (dois compradores com o mesmo usuário = ambíguo, nenhuma).
    """
    if not nomes:
        return {}
    integs = sorted({i for i, _ in nomes}, key=str)
    linhas = (
        await session.execute(
            select(_C.id, _C.integration_id, _C.comprador_nome, _C.comprador_id)
            .where(
                _C.integration_id.in_(integs),
                _C.plataforma == "shopee",
                _C.canal == "chat",
                _C.comprador_nome.in_(sorted(set(nomes.values()))),
            )
            .order_by(_C.ultima_mensagem_em.desc().nulls_last())
        )
    ).all()
    por_nome: dict[tuple[UUID, str], list[tuple[UUID, str | None]]] = defaultdict(list)
    for r in linhas:
        por_nome[(r.integration_id, r.comprador_nome)].append((r.id, r.comprador_id))
    saida: dict[tuple[UUID, str], UUID] = {}
    for par, nome in nomes.items():
        candidatas = por_nome.get((par[0], nome), [])
        if not candidatas:
            continue
        esperado = str((compradores or {}).get(par) or "").strip()
        if esperado:
            dele = next((cid for cid, comp in candidatas if str(comp or "") == esperado), None)
            if dele is not None:
                saida[par] = dele
            continue
        if len({str(comp or "") for _, comp in candidatas}) == 1:
            saida[par] = candidatas[0][0]
    return saida


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
    faltam = [p for p in pares if p not in conversas]
    conversas.update(
        await _conversas_pelo_nome(
            session,
            await _nomes_do_escrow(session, faltam),
            compradores={par: c[0] for par, c in compradores.items()},
        )
    )
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


async def descobrir_indice(
    session: AsyncSession,
    regras: dict[tuple[str, UUID], AtendimentoAutomacaoRegra],
    *,
    agora: datetime,
    motor_desde: datetime,
) -> int:
    """Entregue e concluído dos pedidos Shopee FORA da Logística, pelo índice.

    A Logística não tem os pedidos que foram para Cancelado, Resolvido ou
    Perdimento no Bling (`logistica_ingest`: nunca entram, e o
    `cleanup_finalizados` tira os que viram isso depois). O índice do cartão
    "Cliente" (`atendimento_pedidos_comprador`, de hora em hora pelo
    `update_time` da Shopee) tem o status de quase todo pedido pago (98,6% em
    05/10/2026). O evento é o `atualizado_em` do índice: a ÚLTIMA vez que o
    DaVinci gravou a linha (o job de hora em hora, a leitura de uma conversa,
    a importação) — o índice não guarda o horário da mudança (medido: mediana
    de 13 h depois da Logística). Por isso o concluído (pós) só nasce de pedido que o
    motor JÁ viu entregue (tem a linha `shopee_entregue`): a linha do índice
    que a leitura de uma conversa reescreve semanas depois não vira pós velho.
    A mesma chave da Logística (`pedido:<sn>`): o pedido visto pelas duas
    fontes nasce uma vez só. Nenhuma chamada à plataforma.
    """
    ativas: dict[str, dict[UUID, AtendimentoAutomacaoRegra]] = defaultdict(dict)
    for (codigo, integ), regra in regras.items():
        if codigo in _STATUS_INDICE_EVENTO.values():
            ativas[codigo][integ] = regra
    if not ativas:
        return 0
    integs = sorted({i for por in ativas.values() for i in por}, key=str)
    na_logistica = (
        select(_L.id)
        .where(
            _L.pedido_marketplace == _P.pedido,
            func.lower(func.trim(_L.plataforma)) == "shopee",
        )
        .exists()
    )
    linhas = (
        await session.execute(
            select(
                _P.integration_id, _P.pedido, _P.comprador_id, _P.status, _P.atualizado_em
            ).where(
                _P.integration_id.in_(integs),
                _P.plataforma == "shopee",
                _P.status.in_(tuple(_STATUS_INDICE_EVENTO)),
                _P.atualizado_em >= agora - LOOKBACK_INDICE,
                _P.atualizado_em <= agora,
                _P.criado_em >= agora - JANELA_SHOPEE_PEDIDO,
                ~na_logistica,
            )
        )
    ).all()
    if not linhas:
        return 0
    entregues = set(
        (
            await session.execute(
                select(_R.integration_id, _R.pedido).where(
                    _R.automacao == "shopee_entregue",
                    _R.pedido.in_(sorted({r.pedido for r in linhas})),
                )
            )
        ).all()
    )
    piso = motor_desde - timedelta(hours=2)
    escolhidas = []
    for r in linhas:
        codigo = _STATUS_INDICE_EVENTO[r.status]
        regra = ativas.get(codigo, {}).get(r.integration_id)
        evento = _utc(r.atualizado_em)
        if regra is None or evento is None or evento < _desde_da_regra(regra, piso):
            continue
        if codigo == "shopee_pos_conclusao" and (r.integration_id, r.pedido) not in entregues:
            continue
        escolhidas.append((codigo, regra, r.pedido, evento, r.comprador_id))
    pares = [(regra.integration_id, sn) for _, regra, sn, _, _ in escolhidas]
    conversas = await _conversas_do_pedido(session, pares, agora=agora)
    novas = []
    for codigo, regra, sn, evento, comprador in escolhidas:
        aut = cat.CATALOGO[codigo]
        devido = cat.ajustar_janela(
            evento + timedelta(minutes=regra.atraso_min),
            *(cat.janela_da_regra(regra, aut) or (None, None)),
        )
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
                comprador_id=comprador,
            )
        )
    return await _gravar_candidatos(session, novas)


def _ativas_do_gatilho(
    regras: dict[tuple[str, UUID], AtendimentoAutomacaoRegra], gatilho: str
) -> dict[str, dict[UUID, AtendimentoAutomacaoRegra]]:
    """Automação → loja → regra, só as do gatilho."""
    saida: dict[str, dict[UUID, AtendimentoAutomacaoRegra]] = defaultdict(dict)
    for (codigo, integ), regra in regras.items():
        if cat.CATALOGO[codigo].gatilho == gatilho:
            saida[codigo][integ] = regra
    return saida


async def descobrir_nao_pagos(
    session: AsyncSession,
    regras: dict[tuple[str, UUID], AtendimentoAutomacaoRegra],
    *,
    agora: datetime,
    motor_desde: datetime,
) -> int:
    """Pedido criado e ainda NÃO pago → o "carrinho" com cupom, 30 min depois da criação.

    O Duoke manda 30 min depois da CRIAÇÃO de um pedido que ainda não foi pago
    (medido em produção, 14 dias: p10 = p50 = p90 = 30 min). A única fonte do
    DaVinci sem chamada nova é o índice do cartão "Cliente"
    (`atendimento_pedidos_comprador`, status `UNPAID`), que roda de hora em hora
    (no :22, janela de 2 h): o DaVinci vê o não pago de 0 a ~60 min depois da
    criação. Não há push de pedido da Shopee no DaVinci e o Bling só recebe
    pedido pago. A linha nasce com o `devido_em` de 30 min depois da criação
    (como o Duoke) e é decidida quando o motor a vê — o atraso fica no registro
    (`visto_em`, `decidido_em`, o atraso real da conta) e é a diferença
    combinada `visto_de_hora_em_hora`. Uma vez por pedido (a chave). Nenhuma
    chamada à plataforma.
    """
    ativas = _ativas_do_gatilho(regras, cat.GATILHO_NAO_PAGO)
    if not ativas:
        return 0
    piso = max(agora - LOOKBACK_NAO_PAGO, motor_desde)
    integs = sorted({i for por in ativas.values() for i in por}, key=str)
    linhas = (
        await session.execute(
            select(_P.integration_id, _P.pedido, _P.comprador_id, _P.criado_em).where(
                _P.integration_id.in_(integs),
                _P.plataforma == "shopee",
                _P.status == "UNPAID",
                _P.criado_em >= piso,
                _P.criado_em <= agora,
            )
        )
    ).all()
    if not linhas:
        return 0
    pares = [(r.integration_id, r.pedido) for r in linhas]
    conversas = await _conversas_do_pedido(session, pares, agora=agora)
    pelo_comprador = await _conversas_do_comprador(
        session, [(r.integration_id, r.comprador_id) for r in linhas if r.comprador_id]
    )
    novas = []
    for codigo, por_loja in ativas.items():
        aut = cat.CATALOGO[codigo]
        for r in linhas:
            regra = por_loja.get(r.integration_id)
            criado = _utc(r.criado_em)
            if regra is None or criado is None or criado < _desde_da_regra(regra, piso):
                continue
            novas.append(
                _linha_registro(
                    aut,
                    regra,
                    chave=f"pedido:{r.pedido}",
                    agora=agora,
                    evento_em=criado,
                    devido_em=criado + timedelta(minutes=regra.atraso_min),
                    conversa_id=conversas.get((r.integration_id, r.pedido))
                    or pelo_comprador.get((r.integration_id, r.comprador_id)),
                    pedido=r.pedido,
                    comprador_id=r.comprador_id,
                )
            )
    return await _gravar_candidatos(session, novas)


def comentario_da_chave(chave: str | None) -> str | None:
    """`avaliacao:<comentario_id>` → o id do comentário da plataforma."""
    if not chave or not chave.startswith(f"{cat.ALVO_AVALIACAO}:"):
        return None
    return chave.split(":", 1)[1] or None


async def descobrir_avaliacoes(
    session: AsyncSession,
    regras: dict[tuple[str, UUID], AtendimentoAutomacaoRegra],
    *,
    agora: datetime,
    motor_desde: datetime,
) -> int:
    """A avaliação da Shopee (4–5★ ou 1–3★) → a resposta pública e a do chat, 1 h depois.

    A fonte é a leitura das avaliações (`atendimento_avaliacoes_loja`, a cada
    30 min). O evento é a hora da avaliação na Shopee; a faixa de estrelas
    escolhe a automação. A conversa do comprador (para a mensagem do chat) é a
    do pedido, a do comprador do índice ou a do usuário dele na loja. SÓ
    SIMULAÇÃO: a resposta é pública e o envio continua proibido.
    """
    ativas = _ativas_do_gatilho(regras, cat.GATILHO_AVALIACAO)
    if not ativas:
        return 0
    piso = max(agora - LOOKBACK_AVALIACOES, motor_desde)
    integs = sorted({i for por in ativas.values() for i in por}, key=str)
    linhas = (
        await session.execute(
            select(
                _A.integration_id,
                _A.comentario_id,
                _A.pedido,
                _A.comprador_id,
                _A.comprador_nome_loja,
                _A.estrelas,
                _A.criado_em,
            ).where(
                _A.integration_id.in_(integs),
                _A.plataforma == "shopee",
                _A.criado_em >= piso,
                _A.criado_em <= agora,
            )
        )
    ).all()
    escolhidas = []
    for r in linhas:
        codigo = cat.estrelas_da_faixa(r.estrelas)
        regra = ativas.get(codigo, {}).get(r.integration_id) if codigo else None
        criado = _utc(r.criado_em)
        if regra is None or criado is None or criado < _desde_da_regra(regra, piso):
            continue
        escolhidas.append((codigo, regra, r, criado))
    if not escolhidas:
        return 0
    com_pedido = [(r.integration_id, r.pedido) for _, _, r, _ in escolhidas if r.pedido]
    pelo_pedido = await _conversas_do_pedido(session, com_pedido, agora=agora)
    pelo_comprador = await _conversas_do_comprador(
        session, [(r.integration_id, r.comprador_id) for _, _, r, _ in escolhidas if r.comprador_id]
    )
    pelo_nome = await _conversas_pelo_nome(
        session,
        {
            (r.integration_id, r.pedido): " ".join(r.comprador_nome_loja.split())
            for _, _, r, _ in escolhidas
            if r.pedido and r.comprador_nome_loja and r.comprador_nome_loja.strip()
        },
        compradores={
            (r.integration_id, r.pedido): r.comprador_id
            for _, _, r, _ in escolhidas
            if r.pedido and r.comprador_id
        },
    )
    novas = []
    for codigo, regra, r, criado in escolhidas:
        par = (r.integration_id, r.pedido)
        conversa = (
            (pelo_pedido.get(par) if r.pedido else None)
            or (pelo_comprador.get((r.integration_id, r.comprador_id)) if r.comprador_id else None)
            or (pelo_nome.get(par) if r.pedido else None)
        )
        novas.append(
            _linha_registro(
                cat.CATALOGO[codigo],
                regra,
                chave=f"{cat.ALVO_AVALIACAO}:{r.comentario_id}",
                agora=agora,
                evento_em=criado,
                devido_em=criado + timedelta(minutes=regra.atraso_min),
                conversa_id=conversa,
                pedido=r.pedido,
                comprador_id=r.comprador_id,
            )
        )
    return await _gravar_candidatos(session, novas)


async def descobrir_tiktok_pedidos(
    session: AsyncSession,
    regras: dict[tuple[str, UUID], AtendimentoAutomacaoRegra],
    *,
    agora: datetime,
    motor_desde: datetime,
) -> int:
    """O aviso de pedido da própria TikTok → o "pedido recebido" 5 min depois.

    A TikTok põe "Agradecemos pelo seu pedido! Confirme se o seu endereço…"
    (papel ROBOT, gravado como `sistema`) na conversa do comprador quando o
    pedido é criado — é a 1ª mensagem da conversa em 97% dos casos, e o "pedido
    recebido" do Duoke sai 5 min depois dela (medido: p10 5,03 min, p90 5,08
    min, 247 casos). O aviso não traz o nº do pedido: a linha fica na conversa
    (`conversa:<id>:msg:<aviso>`). SÓ SIMULAÇÃO: enviar exigiria abrir a
    conversa e o cartão do pedido pela API da TikTok.
    """
    ativas = _ativas_do_gatilho(regras, cat.GATILHO_PEDIDO_TIKTOK)
    if not ativas:
        return 0
    piso = max(agora - LOOKBACK_MENSAGENS, motor_desde)
    integs = sorted({i for por in ativas.values() for i in por}, key=str)
    inicio = func.lower(func.left(_M.texto, 60))
    linhas = (
        await session.execute(
            select(
                _M.id,
                _M.conversa_id,
                _M.enviada_em,
                _C.integration_id,
                _C.comprador_id,
            )
            .join(_C, _C.id == _M.conversa_id)
            .where(
                _C.integration_id.in_(integs),
                _C.plataforma == "tiktok",
                _C.canal == "chat",
                _M.autor == AUTOR_SISTEMA,
                or_(*(inicio.like(f"{aviso}%") for aviso in AVISOS_DE_PEDIDO_TIKTOK)),
                # `enviada_em` tem índice; `created_at` é quando a leitura gravou.
                _M.enviada_em >= piso - timedelta(hours=2),
                _M.created_at >= piso,
                _M.created_at <= agora,
            )
        )
    ).all()
    novas = []
    for codigo, por_loja in ativas.items():
        aut = cat.CATALOGO[codigo]
        for r in linhas:
            regra = por_loja.get(r.integration_id)
            evento = _utc(r.enviada_em)
            ligada = _utc(regra.ligada_desde) if regra is not None else None
            if regra is None or evento is None or (ligada is not None and evento < ligada):
                continue
            novas.append(
                _linha_registro(
                    aut,
                    regra,
                    chave=f"conversa:{r.conversa_id}:msg:{r.id}",
                    agora=agora,
                    evento_em=evento,
                    devido_em=evento + timedelta(minutes=regra.atraso_min),
                    conversa_id=r.conversa_id,
                    gatilho_mensagem_id=r.id,
                    comprador_id=r.comprador_id,
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
    cortes: dict[UUID, dict[str, datetime]] = field(default_factory=dict)
    # O começo do motor (o corte do modo seco); a reconferência relê com o mesmo.
    motor_desde: datetime | None = None
    # Por linha: os fatos do banco (reclamação/devolução, pedido, convite, compra).
    extras: dict[UUID, dict] = field(default_factory=dict)
    # Por linha do pedido SEM conversa: o comprador (`to_id`, do índice) e o
    # usuário dele (do escrow) — o envio pelo `to_id` e o `{comprador}`.
    compradores: dict[UUID, str] = field(default_factory=dict)
    nomes: dict[UUID, str] = field(default_factory=dict)
    # Por linha do pedido no modo enviar: quando o Duoke mandou a mesma
    # automação depois do gatilho (a troca não manda em dobro).
    duoke_pedido: dict[UUID, list[datetime]] = field(default_factory=dict)
    # O nome de cada loja (o cupom do "carrinho" é pelo tipo dela: celular ou mala).
    nomes_loja: dict[UUID, str] = field(default_factory=dict)
    # Pedido não pago: (loja, comprador) → o `devido_em` dos "carrinhos" que a
    # rodada já decidiu mandar (um por comprador em 24 h, também no mesmo lote).
    recebidos: dict[tuple[UUID, str], list[datetime]] = field(default_factory=dict)
    # O relógio real do começo: a reconferência anda o `agora` pelo que passou.
    inicio: float = field(default_factory=_time.monotonic)
    # Depois disto (relógio monotônico) nenhum envio novo começa (`ORCAMENTO_ENVIO_S`).
    prazo_envio: float | None = None
    adiadas: int = 0

    def agora_real(self) -> datetime:
        return self.agora + timedelta(seconds=_time.monotonic() - self.inicio)


@dataclass
class _Veredito:
    """O que fazer com a linha: esperar, pular (com o motivo), simular ou enviar."""

    acao: str
    modo: str | None = None
    motivo: str | None = None
    divergencia: str | None = None
    disjuntor: bool = False
    partes: list[dict] = field(default_factory=list)
    ctx: _Contexto | None = None


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
    """(loja, família ou automação, "seco"/"envio") → linhas decididas em 24 h corridas.

    Conta o que saiu ou pode ter saído (`enviando`, `enviado`, `revisar`) e o
    `falhou` que chegou a falar com a plataforma (com `mensagem_ids`).
    """
    linhas = (
        await session.execute(
            select(_R.integration_id, _R.automacao, _R.estado, func.count())
            .where(
                _R.decidido_em >= agora - timedelta(hours=24),
                or_(
                    _R.estado.in_(
                        (
                            cat.ESTADO_SIMULADO,
                            cat.ESTADO_ENVIANDO,
                            cat.ESTADO_ENVIADO,
                            cat.ESTADO_REVISAR,
                        )
                    ),
                    # `falhou` que chegou a falar com a plataforma (tem mensagem):
                    # conta — senão um erro repetido nunca encostaria no teto.
                    and_(
                        _R.estado == cat.ESTADO_FALHOU,
                        func.jsonb_array_length(_R.mensagem_ids) > 0,
                    ),
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
            select(_B.numeroloja, _B.situacao, _B.data, _B.created_at).where(
                _B.numeroloja.in_(pedidos)
            )
        )
    ).all()
    cancelados = {r.numeroloja for r in bling if (r.situacao or "") in _SITUACOES_CANCELADO}
    devolucao_bling = {r.numeroloja for r in bling if (r.situacao or "") in _SITUACOES_DEVOLUCAO}
    data_bling: dict[str, datetime] = {}
    # Quando o Bling recebeu o pedido (o Bling só recebe pedido PAGO): o
    # "carrinho" não vai para quem já pagou.
    pago_em: dict[str, datetime] = {}
    for r in bling:
        if r.data is not None:
            d = _utc(r.data)
            data_bling[r.numeroloja] = min(d, data_bling.get(r.numeroloja, d))
        if r.created_at is not None and (r.situacao or "") not in _SITUACOES_CANCELADO:
            c = _utc(r.created_at)
            pago_em[r.numeroloja] = min(c, pago_em.get(r.numeroloja, c))
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
        tem_logistica = x.pedido in logistica
        # Fora da Logística (`descobrir_indice`), o status é o do índice.
        status_idx = (idx[1] or None) if idx else None
        status = status_logi if tem_logistica else status_idx
        saida[x.id] = {
            "pedido_cancelado": x.pedido in cancelados
            or bool(idx and (idx[1] or "") in _STATUS_INDICE_CANCELADO),
            "status_pedido": status,
            "devolucao_logistica": bool(retorno) or status == "TO_RETURN",
            "devolucao_bling": x.pedido in devolucao_bling,
            "avaliou": (x.integration_id, x.pedido) in avaliados,
            "pedido_criado_em": criado,
            "tem_logistica": tem_logistica,
            "pedido_pago": x.pedido in pago_em,
            "pago_em": pago_em.get(x.pedido),
            "total_pedido": idx[3] if idx else None,
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


async def avaliacoes_das_linhas(session: AsyncSession, linhas: list[Any]) -> dict[UUID, Any]:
    """Linha da resposta de avaliação → a avaliação de AGORA (a nota, a resposta, o usuário)."""
    alvo = []
    for x in linhas:
        aut = cat.CATALOGO.get(x.automacao)
        comentario = comentario_da_chave(x.chave)
        if aut is not None and aut.alvo == cat.ALVO_AVALIACAO and comentario:
            alvo.append((x, comentario))
    if not alvo:
        return {}
    linhas_av = (
        await session.execute(
            select(
                _A.integration_id,
                _A.comentario_id,
                _A.pedido,
                _A.estrelas,
                _A.resposta_loja,
                _A.resposta_em,
                _A.comprador_nome_loja,
                _A.criado_em,
                _A.atualizado_em,
                _A.dados,
            )
            .where(
                _A.plataforma == "shopee",
                _A.comentario_id.in_(sorted({c for _, c in alvo})),
            )
            .execution_options(populate_existing=True)
        )
    ).all()
    por = {(r.integration_id, r.comentario_id): r for r in linhas_av}
    return {x.id: por[(x.integration_id, c)] for x, c in alvo if (x.integration_id, c) in por}


async def _ja_recebeu_carrinho(
    session: AsyncSession, linha: AtendimentoAutomacaoRegistro, horas: float
) -> bool:
    """O comprador já tem um "carrinho" do DaVinci (simulado ou enviado) nas `horas` antes."""
    if not linha.comprador_id or linha.devido_em is None:
        return False
    devido = _utc(linha.devido_em)
    achou = await session.scalar(
        select(_R.id)
        .where(
            _R.automacao == linha.automacao,
            _R.integration_id == linha.integration_id,
            _R.comprador_id == linha.comprador_id,
            _R.id != linha.id,
            _R.estado.in_(
                (cat.ESTADO_SIMULADO, cat.ESTADO_ENVIANDO, cat.ESTADO_ENVIADO, cat.ESTADO_REVISAR)
            ),
            _R.devido_em >= devido - timedelta(hours=horas),
            _R.devido_em <= devido,
        )
        .limit(1)
    )
    return achou is not None


async def _extras(
    session: AsyncSession, ctx: _Contexto, linhas: list[AtendimentoAutomacaoRegistro]
) -> dict[UUID, dict]:
    """Os fatos do banco de cada linha: reclamação/devolução, pedido, convite, compra,
    o "carrinho" do mesmo comprador e a avaliação de agora."""
    reclamacoes = await _fatos_de_reclamacao(session, linhas)
    pedidos = await _fatos_de_pedido(session, linhas, agora=ctx.agora)
    avaliacoes = await avaliacoes_das_linhas(session, linhas)
    saida: dict[UUID, dict] = {}
    for linha in linhas:
        aut = cat.CATALOGO.get(linha.automacao)
        extras: dict[str, Any] = {}
        extras.update(reclamacoes.get(linha.id, {}))
        extras.update(pedidos.get(linha.id, {}))
        if extras.get("devolucao_logistica") or extras.get("devolucao_bling"):
            extras["devolucao_qualquer"] = True
        conversa = ctx.conversas.get(linha.conversa_id) if linha.conversa_id else None
        if aut is not None and aut.tipo == cat.TIPO_CONVITE and linha.evento_em is not None:
            extras["ja_recebeu"] = await _ja_recebeu_convite(session, linha, conversa)
            if aut.plataforma == "tiktok":
                extras["nao_e_primeira"] = await _nao_e_primeira(session, linha)
        if aut is not None and aut.tipo in (cat.TIPO_DUVIDA_1, cat.TIPO_DUVIDA_2):
            extras["ja_comprou"] = await _ja_comprou(session, linha, conversa)
        if aut is not None and aut.tipo == cat.TIPO_NAO_PAGO:
            regra = ctx.regras.get((linha.automacao, linha.integration_id))
            horas = float(cat._cond(regra, aut, "um_por_comprador_h", cat.UM_POR_COMPRADOR_H))
            extras["ja_recebeu"] = await _ja_recebeu_carrinho(session, linha, horas)
        av = avaliacoes.get(linha.id)
        if aut is not None and aut.alvo == cat.ALVO_AVALIACAO and av is not None:
            menor, maior = aut.estrelas or (1, 5)
            extras["estrelas_mudaram"] = not (menor <= int(av.estrelas or 0) <= maior)
            resposta = (av.resposta_loja or "").strip()
            # Respondida por PESSOA (a resposta não é a do Duoke): o DaVinci não
            # responde de novo. A do Duoke não conta no modo seco (é a comparação).
            extras["pessoa_respondeu"] = (
                bool(resposta) and cat.assinatura_avaliacao(resposta) is None
            )
            nome = " ".join((av.comprador_nome_loja or "").split())
            if nome and linha.id not in ctx.nomes:
                ctx.nomes[linha.id] = nome
        saida[linha.id] = extras
    return saida


async def _contexto(
    session: AsyncSession,
    linhas: list[AtendimentoAutomacaoRegistro],
    *,
    agora: datetime,
    motor_desde: datetime | None = None,
) -> _Contexto:
    """Tudo o que a decisão das linhas lê do banco — relido AGORA (`populate_existing`).

    É o mesmo para o lote da rodada e para a RECONFERÊNCIA logo antes do
    envio (uma linha): a decisão e a reconferência nunca discordam por terem
    lido coisas diferentes. `motor_desde` é o corte do modo seco
    (`cortes_do_modo_seco`): a reconferência relê com o mesmo do lote.
    """
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
    # ligado, pelo comprador do índice ou pelo usuário do escrow (quem escreveu
    # antes de comprar).
    # O comprador de cada pedido pelo índice (o `to_id`): quem a conversa
    # achada tem de ter — nunca a conversa de outro comprador.
    do_pedido = [x for x in linhas if x.pedido]
    indice = (
        await _compradores_do_pedido(session, [(x.integration_id, x.pedido) for x in do_pedido])
        if do_pedido
        else {}
    )

    def _comprador_do_pedido(x: AtendimentoAutomacaoRegistro) -> str | None:
        idx = indice.get((x.integration_id, x.pedido))
        return (idx[0] if idx else None) or x.comprador_id

    sem_conversa = [x for x in linhas if x.conversa_id is None and x.pedido]
    nomes_escrow: dict[tuple[UUID, str], str] = {}
    escrow_lido: set[tuple[UUID, str]] = set()
    if sem_conversa:
        pares = [(x.integration_id, x.pedido) for x in sem_conversa]
        escrow_lido.update(pares)
        achadas = await _conversas_do_pedido(session, pares, agora=agora)
        pelo_comprador = await _conversas_do_comprador(
            session, [(x.integration_id, x.comprador_id) for x in sem_conversa if x.comprador_id]
        )
        nomes_escrow = await _nomes_do_escrow(session, pares)
        pelo_nome = await _conversas_pelo_nome(
            session,
            {p: n for p, n in nomes_escrow.items() if p not in achadas},
            compradores={
                (x.integration_id, x.pedido): c
                for x in sem_conversa
                if (c := _comprador_do_pedido(x))
            },
        )
        for x in sem_conversa:
            achada = (
                achadas.get((x.integration_id, x.pedido))
                or (
                    pelo_comprador.get((x.integration_id, x.comprador_id))
                    if x.comprador_id
                    else None
                )
                or pelo_nome.get((x.integration_id, x.pedido))
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
    # A linha do pedido cuja conversa é de OUTRO comprador (a gravada na
    # descoberta, ou achada pelo pedido/nome) perde a conversa: a mensagem do
    # pedido só vai para quem comprou — pelo `to_id` do índice, ou espera.
    for x in do_pedido:
        conversa = conversas.get(x.conversa_id) if x.conversa_id else None
        esperado = str(_comprador_do_pedido(x) or "").strip()
        dono = str(conversa.comprador_id or "").strip() if conversa is not None else ""
        if conversa is not None and esperado and dono and dono != esperado:
            aut_x = cat.CATALOGO.get(x.automacao)
            if aut_x is not None and aut_x.alvo == cat.ALVO_PEDIDO:
                logger.warning(
                    "atendimento_automacoes_conversa_de_outro_comprador",
                    automacao=x.automacao,
                    integration_id=str(x.integration_id),
                    conversa_id=str(conversa.id),
                )
                x.conversa_id = None
    conversa_ids = sorted({x.conversa_id for x in linhas if x.conversa_id}, key=str)
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
        # O modo seco mede o DaVinci sozinho (o estado sem o Duoke que ele simula).
        cortes={
            integ: cat.cortes_do_modo_seco(por_loja.get(integ, []), motor_desde)
            for integ in {x.integration_id for x in linhas}
        },
        motor_desde=motor_desde,
    )
    # Ainda sem conversa: o comprador do pedido (o `to_id` do envio pela
    # Shopee) só o índice tem; o usuário dele (o `{comprador}`), o escrow.
    ainda = [x for x in linhas if x.conversa_id is None and x.pedido]
    if ainda:
        faltam_nomes = [
            (x.integration_id, x.pedido)
            for x in ainda
            if (x.integration_id, x.pedido) not in escrow_lido
        ]
        if faltam_nomes:  # a linha que perdeu a conversa de outro comprador
            nomes_escrow.update(await _nomes_do_escrow(session, faltam_nomes))
        for x in ainda:
            comprador = _comprador_do_pedido(x)
            if comprador and not x.comprador_id:
                x.comprador_id = comprador
            if comprador:
                ctx.compradores[x.id] = comprador
            nome = nomes_escrow.get((x.integration_id, x.pedido))
            if nome:
                ctx.nomes[x.id] = nome
    ctx.nomes_loja = dict(
        (
            await session.execute(
                select(Integration.id, Integration.name).where(Integration.id.in_(integs))
            )
        ).all()
    )
    ctx.extras = await _extras(session, ctx, linhas)
    # Modo enviar nas do PEDIDO: o Duoke já mandou esta automação para este
    # pedido depois do gatilho? A mesma régua do comparador
    # (`automacoes_comparar.duoke_dos_pedidos`: o cartão do pedido do Duoke ou
    # a conversa do pedido). A conversa tem o `duoke_depois` dela; o pedido, este.
    do_pedido_enviar = [
        x
        for x in linhas
        if x.pedido
        and (aut_x := cat.CATALOGO.get(x.automacao)) is not None
        and aut_x.alvo == cat.ALVO_PEDIDO
        and (r := regras.get((x.automacao, x.integration_id))) is not None
        and _modo_efetivo(r)[0] == cat.MODO_ENVIAR
    ]
    if do_pedido_enviar:
        from app.services.atendimento import automacoes_comparar

        ctx.duoke_pedido = await automacoes_comparar.duoke_dos_pedidos(
            session, do_pedido_enviar, agora=agora
        )
    return ctx


def _validade_e_abertura(
    aut: cat.Automacao,
    regra: AtendimentoAutomacaoRegra,
    linha: AtendimentoAutomacaoRegistro,
    agora: datetime,
) -> tuple[datetime, datetime]:
    """(até quando a linha vale, quando o horário da regra abre a partir de `agora`).

    O HORÁRIO vale na decisão, não só na descoberta: a linha pode ter esperado
    a leitura, o motor parado, o PATCH rearmado — fora da janela da regra
    AGORA, ela espera a próxima abertura, se ainda valer.
    """
    janela = cat.janela_da_regra(regra, aut)
    validade = cat.validade_ate(aut, linha.devido_em, janela)
    abre = cat.ajustar_janela(agora, *janela) if janela else agora
    return validade, abre


def _avaliar(
    ctx: _Contexto,
    aut: cat.Automacao,
    regra: AtendimentoAutomacaoRegra,
    linha: AtendimentoAutomacaoRegistro,
) -> _Veredito:
    """A decisão de UMA linha sobre o que o contexto leu. PURA sobre o contexto.

    Condições e exclusões (`automacoes_catalogo.decidir`), a janela da
    Shopee, no modo enviar a espera pela leitura e o "o Duoke já mandou", o
    texto (validador) e o teto. Sem conversa no modo enviar: o comprador do
    pedido (Shopee) ou espera por ele.
    """
    agora = ctx.agora
    modo, motivo_simulado = _modo_efetivo(regra)
    if aut.so_simular:
        # SÓ SIMULA (o pedido não pago, a resposta da avaliação, o "pedido
        # recebido" do TikTok): nunca envia, nem com a regra em `enviar` e as
        # chaves ligadas — o PATCH recusa `enviar`; isto é a rede.
        modo = cat.MODO_SIMULAR
        motivo_simulado = "so_simulacao" if regra.modo == cat.MODO_ENVIAR else None
    fatos = _fatos_comuns(ctx, aut, linha)
    fatos.update(ctx.extras.get(linha.id, {}))
    conversa = ctx.conversas.get(linha.conversa_id) if linha.conversa_id else None
    fatos["tem_conversa"] = conversa is not None
    if (
        linha.conversa_id
        and linha.evento_em is not None
        and aut.alvo not in (cat.ALVO_PEDIDO, cat.ALVO_AVALIACAO)
    ):
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
    if aut.alvo == cat.ALVO_PEDIDO:
        fatos["duoke_depois"] = list(ctx.duoke_pedido.get(linha.id, []))
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
    valores = None
    if aut.tipo == cat.TIPO_NAO_PAGO:
        valor = cat.valor_cupom(ctx.nomes_loja.get(linha.integration_id), fatos.get("total_pedido"))
        fatos["valor_cupom"] = valor
        if valor is not None:
            valores = {"valor_cupom": str(valor)}
        if linha.comprador_id and not fatos.get("ja_recebeu"):
            # Um por comprador no intervalo da regra (24 h), também no mesmo lote.
            janela = timedelta(
                hours=float(cat._cond(regra, aut, "um_por_comprador_h", cat.UM_POR_COMPRADOR_H))
            )
            devido = _utc(linha.devido_em)
            fatos["ja_recebeu"] = any(
                devido - janela <= t <= devido
                for t in ctx.recebidos.get((linha.integration_id, linha.comprador_id), [])
            )
    if modo == cat.MODO_ENVIAR and fatos.get("canal_erro"):
        return _Veredito("esperar", modo)  # a leitura da loja falhou agora: a próxima rodada
    motivo = cat.decidir(aut, fatos, regra)
    if motivo is None and modo == cat.MODO_ENVIAR:
        # A troca não manda em dobro: na transição, espera a leitura da loja
        # passar do `devido_em` + a espera do Duoke; e confere se ele mandou
        # DEPOIS do gatilho (na conversa, ou — nas do pedido — pelo cartão do
        # pedido ou pela conversa do pedido: `duoke_dos_pedidos`, no `_contexto`).
        if _em_transicao(regra, agora):
            espera = _utc(linha.devido_em) + aut.espera_duoke
            canal = ctx.canais.get((linha.integration_id, aut.canal))
            lido = _utc(canal.ultimo_ok_em) if canal is not None else None
            if agora < espera or lido is None or lido < espera:
                return _Veredito("esperar", modo)
        duoke = fatos.get("duoke_depois") or []
        if duoke:
            depois_da_troca = regra.enviar_desde is not None and any(
                t >= _utc(regra.enviar_desde) for t in duoke
            )
            # O disjuntor NA HORA (`_decidir_uma`): o resto do lote desta regra
            # já decide em `simular`.
            return _Veredito("pular", modo, "duoke_mandou", disjuntor=depois_da_troca)
        if aut.campanha:
            # Import TARDIO (o modo seco nunca carrega o envio): a campanha só
            # sai como resposta automática — a chave confirmada E o envio por
            # ela no adaptador (`enviar_parte(auto_reply=True)`). Sem os dois,
            # sairia como mensagem normal.
            from app.services.atendimento import enviar as envio

            if not envio.campanha_por_auto_reply():
                motivo = "campanha_sem_auto_reply"
    if motivo is not None:
        return _Veredito("pular", modo, motivo, cat.divergencia_do_motivo(motivo, aut))
    comprador = (conversa.comprador_nome if conversa is not None else None) or ctx.nomes.get(
        linha.id
    )
    partes, motivos = cat.renderizar(
        regra.partes or cat.partes_padrao(aut),
        comprador=comprador,
        plataforma=aut.plataforma,
        canal=aut.canal,
        valores=valores,
    )
    if motivos:
        return _Veredito("pular", modo, "texto_invalido")
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
        return _Veredito("pular", modo, teto, teto if modo == cat.MODO_SIMULAR else None)
    if modo == cat.MODO_ENVIAR:
        if conversa is None:
            # Sem conversa no DaVinci: só a Shopee manda, pelo comprador do
            # pedido (`to_id`). Sem ele ainda, espera o índice trazê-lo.
            if not (aut.plataforma == "shopee" and aut.alvo == cat.ALVO_PEDIDO and linha.pedido):
                return _Veredito("pular", modo, "sem_conversa")
            if not ctx.compradores.get(linha.id):
                if agora < _utc(linha.devido_em) + ESPERA_COMPRADOR:
                    return _Veredito("esperar", modo)
                return _Veredito("pular", modo, "sem_comprador")
        return _Veredito("enviar", modo, partes=partes)
    return _Veredito("simular", modo, motivo_simulado, partes=partes)


async def _reconferir(
    session: AsyncSession, ctx: _Contexto, aut: cat.Automacao, linha: AtendimentoAutomacaoRegistro
) -> _Veredito:
    """RECONFERÊNCIA logo antes do envio: tudo de novo, relido do banco AGORA.

    Entre a leitura do lote (no começo da decisão) e o envio passam segundos
    ou minutos (as outras linhas da rodada, até 90 s por parte): a devolução
    ou a reclamação (aberta ou encerrada) que chegou, o pedido cancelado, a
    pessoa que respondeu, o Duoke que mandou, a janela da Shopee e o teto
    (contado de novo no banco) são conferidos outra vez. Mudou → `pulado`
    com o motivo. Commita antes (o que a rodada já decidiu fica gravado e a
    leitura nova vê o banco de agora).
    """
    await session.commit()
    novo = await _contexto(session, [linha], agora=ctx.agora_real(), motor_desde=ctx.motor_desde)
    regra = novo.regras.get((linha.automacao, linha.integration_id))
    if regra is None or regra.modo == cat.MODO_DESLIGADO:
        return _Veredito("pular", None, "regra_desligada", "regra_desligada", ctx=novo)
    validade, abre = _validade_e_abertura(aut, regra, linha, novo.agora)
    if novo.agora > validade or abre > validade:
        return _Veredito("pular", regra.modo, "atrasado", "motor_atrasado", ctx=novo)
    if abre > novo.agora:
        # O horário da regra fechou no meio da rodada: fica `agendado` (a
        # próxima rodada anda o `devido_em` para a abertura).
        return _Veredito("esperar", regra.modo, ctx=novo)
    veredito = _avaliar(novo, aut, regra, linha)
    veredito.ctx = novo
    return veredito


def _codigo_da_plataforma(erro: str | None) -> str | None:
    """A recusa da plataforma que não passa sozinha ("shopee error_param" → "error_param")."""
    texto = erro or ""
    return next((c for c in _RECUSAS_DA_PLATAFORMA if c in texto), None)


async def _parou(
    session: AsyncSession,
    ctx: _Contexto,
    aut: cat.Automacao,
    regra: AtendimentoAutomacaoRegra,
    linha: AtendimentoAutomacaoRegistro,
    *,
    indice: int,
    estado: str,
    erro: str,
    saidas: int,
    ids: list[str],
) -> None:
    """Fecha (e commita) a linha cuja parte `indice` não saiu.

    `estado` é o que a parte sozinha daria: `pulado` (recusa nossa — nada foi
    à plataforma), `falhou` (a plataforma recusou) ou `revisar` (ambíguo).

    - Nenhuma parte saiu antes (`saidas == 0`): esse estado.
    - Alguma JÁ SAIU (o cartão do pedido) e esta não: o comprador ficou com a
      mensagem pela METADE. A linha vira `revisar` (pessoa confere) e o
      DISJUNTOR volta a regra para simular (`parte_falhou`) — acontece uma
      vez por loja, não em todo pedido. Menos quando quem parou foi o
      operador (as chaves, a regra: `_RECUSAS_FREIO`) — aí nada mais sai.
    - Campanha da Shopee recusada pela plataforma com um código que não passa
      sozinho (`_RECUSAS_DA_PLATAFORMA`: o corpo ou a permissão) já na 1ª
      parte: nada saiu, mas todo pedido daria o mesmo erro — disjuntor
      `plataforma_recusou`.
    """
    agora = _agora()
    no_meio = saidas > 0
    if no_meio:
        final, motivo = cat.ESTADO_REVISAR, f"parte_{indice + 1}"
    elif estado == cat.ESTADO_PULADO:
        final, motivo = cat.ESTADO_PULADO, "envio_recusado"
    else:
        final, motivo = estado, (f"parte_{indice + 1}" if indice else None)
    _fechar(linha, estado=final, agora=agora, modo=cat.MODO_ENVIAR, motivo=motivo, regra=regra)
    linha.erro = (erro or estado)[:200]
    linha.mensagem_ids = ids
    if final == cat.ESTADO_REVISAR or (final == cat.ESTADO_FALHOU and ids):
        # Falou com a plataforma: conta no teto do dia (`_contagens_do_dia` também).
        ctx.contagem[(regra.integration_id, aut.familia, "envio")] += 1
        ctx.contagem[(regra.integration_id, aut.codigo, "envio")] += 1
    disjuntor = None
    if erro not in _RECUSAS_FREIO:
        if no_meio:
            disjuntor = "parte_falhou"
        elif aut.campanha and final == cat.ESTADO_FALHOU and _codigo_da_plataforma(erro):
            disjuntor = "plataforma_recusou"
    if disjuntor:
        # A regra relida: outro processo pode tê-la mudado no meio do envio.
        await session.refresh(regra)
        await disparar_disjuntor(session, regra, agora=agora, motivo=disjuntor)
    logger.warning(
        "atendimento_automacoes_envio_parou",
        automacao=aut.codigo,
        integration_id=str(linha.integration_id),
        parte=indice + 1,
        partes_que_sairam=saidas,
        estado=final,
        erro=(erro or "")[:80],
        disjuntor=disjuntor,
    )
    await session.commit()


async def _enviar(
    session: AsyncSession,
    ctx: _Contexto,
    aut: cat.Automacao,
    regra: AtendimentoAutomacaoRegra,
    linha: AtendimentoAutomacaoRegistro,
) -> None:
    """O modo enviar: reconfere, marca `enviando` (commitado ANTES), depois parte a parte.

    Cada parte é uma mensagem, na ordem da regra: o cartão do pedido, o
    texto, a figurinha. Sem conversa no DaVinci (Shopee, linha do pedido), a
    1ª parte sai pelo `to_id` do comprador e a conversa que a Shopee devolve
    recebe as outras. Parte que não sai depois de outra ter saído: `revisar`
    e o disjuntor (`_parou`).
    """
    # Import TARDIO de propósito: o modo seco nunca carrega o envio (o teste prova).
    from app.services.atendimento import enviar as envio

    veredito = await _reconferir(session, ctx, aut, linha)
    novo = veredito.ctx or ctx
    regra = novo.regras.get((linha.automacao, linha.integration_id)) or regra
    if veredito.acao == "esperar":
        return
    if veredito.acao != "enviar":
        if veredito.acao == "simular":
            # A regra saiu de `enviar` (ou a chave desligou) no meio da rodada.
            _fechar(
                linha,
                estado=cat.ESTADO_SIMULADO,
                agora=novo.agora,
                modo=cat.MODO_SIMULAR,
                motivo=veredito.motivo,
                regra=regra,
            )
            ctx.contagem[(regra.integration_id, aut.familia, "seco")] += 1
            ctx.contagem[(regra.integration_id, aut.codigo, "seco")] += 1
            await _seguinte(session, novo, aut, linha)
        else:
            _fechar(
                linha,
                estado=cat.ESTADO_PULADO,
                agora=novo.agora,
                modo=veredito.modo,
                motivo=veredito.motivo,
                regra=regra,
                divergencia=veredito.divergencia,
            )
            if veredito.disjuntor:
                await disparar_disjuntor(
                    session, regra, agora=novo.agora, motivo="duoke_ainda_ligado"
                )
        logger.info(
            "atendimento_automacoes_reconferencia",
            automacao=aut.codigo,
            integration_id=str(linha.integration_id),
            estado=linha.estado,
            motivo=linha.motivo,
        )
        await session.commit()
        return
    partes = veredito.partes
    conversa = novo.conversas.get(linha.conversa_id) if linha.conversa_id else None
    to_id = novo.compradores.get(linha.id)
    nome = novo.nomes.get(linha.id)
    linha.estado = cat.ESTADO_ENVIANDO
    linha.modo = cat.MODO_ENVIAR
    linha.tentativas = (linha.tentativas or 0) + 1
    linha.regra_versao = regra.versao
    linha.updated_at = novo.agora
    await session.commit()
    ids: list[str] = list(linha.mensagem_ids or [])
    saidas = 0  # partes que a plataforma ACEITOU

    async def parou(indice: int, estado: str, erro: str, ja_sairam: int) -> None:
        await _parou(
            session,
            ctx,
            aut,
            regra,
            linha,
            indice=indice,
            estado=estado,
            erro=erro,
            saidas=ja_sairam,
            ids=ids,
        )

    for i, parte in enumerate(partes):
        if conversa is None:
            try:
                saiu = await envio.enviar_automatica_sem_conversa(
                    session,
                    integration_id=linha.integration_id,
                    codigo=aut.codigo,
                    to_id=to_id or "",
                    pedido=linha.pedido,
                    parte=parte,
                    registro_id=linha.id,
                    regra_versao=regra.versao,
                    indice=i,
                    comprador_nome=nome,
                )
            except envio.EnvioRecusado as recusa:
                await session.refresh(linha)
                if not saidas and recusa.code in envio.RECUSAS_TEMPORARIAS:
                    linha.estado = cat.ESTADO_AGENDADO  # nada saiu: a próxima rodada
                    linha.updated_at = _agora()
                    await session.commit()
                    return
                await parou(i, cat.ESTADO_PULADO, recusa.code, saidas)
                return
            await session.refresh(linha)
            if saiu.status != "enviada":
                if saiu.saiu and i + 1 < len(partes):
                    # A Shopee ACEITOU esta parte (o banco é que falhou depois):
                    # as seguintes não vão — a mensagem ficou pela metade.
                    await parou(i + 1, cat.ESTADO_REVISAR, saiu.erro or saiu.status, saidas + 1)
                    return
                estado = cat.ESTADO_REVISAR if saiu.status == "revisar" else cat.ESTADO_FALHOU
                await parou(i, estado, saiu.erro or saiu.status or "", saidas)
                return
            saidas += 1
            ids.append(str(saiu.mensagem_id))
            linha.mensagem_ids = ids
            linha.conversa_id = saiu.conversa_id
            await session.commit()
            conversa = await session.get(AtendimentoConversa, saiu.conversa_id)
            continue
        mensagem = None
        for tentativa in range(TENTATIVAS_PARTE):
            try:
                mensagem = await envio.enviar_automatica(
                    session,
                    conversa,
                    codigo=aut.codigo,
                    parte=parte,
                    pedido=linha.pedido,
                    registro_id=linha.id,
                    regra_versao=regra.versao,
                    indice=i,
                )
                break
            except envio.EnvioRecusado as recusa:
                await session.refresh(linha)
                temporaria = recusa.code in envio.RECUSAS_TEMPORARIAS
                if not saidas and temporaria:
                    linha.estado = cat.ESTADO_AGENDADO  # nada saiu: a próxima rodada
                    linha.updated_at = _agora()
                    await session.commit()
                    return
                if saidas and temporaria and tentativa + 1 < TENTATIVAS_PARTE:
                    # O cartão já saiu: insiste agora (a leitura solta a conversa
                    # em segundos) em vez de deixar o cartão sozinho.
                    await session.commit()
                    await asyncio.sleep(ESPERA_PARTE_S)
                    continue
                if saidas and recusa.code == envio.RECUSA_ENVIO_REPETIDO:
                    # A MESMA frase acabou de sair nesta conversa (o mesmo
                    # comprador, dois pedidos na mesma rodada): já está lá.
                    logger.info(
                        "atendimento_automacoes_parte_ja_na_conversa",
                        automacao=aut.codigo,
                        integration_id=str(linha.integration_id),
                        parte=i + 1,
                    )
                    break
                await parou(i, cat.ESTADO_PULADO, recusa.code, saidas)
                return
        if mensagem is None:
            continue  # a parte repetida: segue para a próxima
        ids.append(str(mensagem.id))
        await session.refresh(linha)
        linha.mensagem_ids = ids
        if mensagem.status != "enviada":
            estado = (
                cat.ESTADO_REVISAR
                if mensagem.status in ("revisar", "enviando")
                else cat.ESTADO_FALHOU
            )
            await parou(i, estado, mensagem.erro or mensagem.status or "", saidas)
            return
        saidas += 1
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
    validade, abre = _validade_e_abertura(aut, regra, linha, agora)
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
    veredito = _avaliar(ctx, aut, regra, linha)
    if veredito.acao == "esperar":
        return linha.estado
    if veredito.acao == "pular":
        _fechar(
            linha,
            estado=cat.ESTADO_PULADO,
            agora=agora,
            modo=veredito.modo,
            motivo=veredito.motivo,
            regra=regra,
            divergencia=veredito.divergencia,
        )
        if veredito.disjuntor:
            await disparar_disjuntor(session, regra, agora=agora, motivo="duoke_ainda_ligado")
        return linha.estado
    if veredito.acao == "enviar":
        if ctx.prazo_envio is not None and _time.monotonic() > ctx.prazo_envio:
            # Fica `agendado` para a próxima rodada: começado agora, o envio
            # arriscaria ser cortado pelo timeout da rodada entre as partes.
            ctx.adiadas += 1
            return linha.estado
        await _enviar(session, ctx, aut, regra, linha)
        return linha.estado
    _fechar(
        linha,
        estado=cat.ESTADO_SIMULADO,
        agora=agora,
        modo=cat.MODO_SIMULAR,
        motivo=veredito.motivo,
        regra=regra,
    )
    ctx.contagem[(regra.integration_id, aut.familia, "seco")] += 1
    ctx.contagem[(regra.integration_id, aut.codigo, "seco")] += 1
    if aut.tipo == cat.TIPO_NAO_PAGO and linha.comprador_id:
        # O próximo "carrinho" do mesmo comprador no lote já vê este.
        ctx.recebidos.setdefault((linha.integration_id, linha.comprador_id), []).append(
            _utc(linha.devido_em)
        )
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
    session: AsyncSession,
    *,
    agora: datetime,
    motor_desde: datetime | None = None,
    prazo_envio: float | None = None,
) -> dict[str, int]:
    """As linhas `agendado` que venceram: até `MAX_DECISOES` por rodada, a mais
    velha primeiro, no máximo `MAX_POR_REGRA` da mesma loja × automação.

    `prazo_envio` (relógio monotônico): depois dele nenhum envio novo começa
    (a linha fica para a próxima rodada). Sem ele, `ORCAMENTO_ENVIO_S` a
    partir de agora.
    """
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
    ctx = await _contexto(session, linhas, agora=agora, motor_desde=motor_desde)
    ctx.prazo_envio = (
        prazo_envio if prazo_envio is not None else _time.monotonic() + ORCAMENTO_ENVIO_S
    )
    for linha in linhas:
        estado = await _decidir_uma(session, ctx, linha)
        contagem[estado] += 1
    await session.commit()
    if ctx.adiadas:
        logger.info("atendimento_automacoes_envio_adiado", quantidade=ctx.adiadas)
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

    comeco = _time.monotonic()
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
            # As que faltavam do Duoke (só simulação): o pedido não pago pelo
            # índice, a avaliação pela leitura das avaliações e o aviso de
            # pedido da TikTok pela leitura da caixa.
            t0 = _time.monotonic()
            resumo["nao_pagos"] = await descobrir_nao_pagos(
                session, regras, agora=agora, motor_desde=motor_desde
            )
            resumo["avaliacoes"] = await descobrir_avaliacoes(
                session, regras, agora=agora, motor_desde=motor_desde
            )
            resumo["tiktok_pedidos"] = await descobrir_tiktok_pedidos(
                session, regras, agora=agora, motor_desde=motor_desde
            )
            await session.commit()
            _marca("novas", t0)
            if agora.minute % LOGISTICA_A_CADA_MIN in (0, 1):
                t0 = _time.monotonic()
                resumo["logistica"] = await descobrir_logistica(
                    session, regras, agora=agora, motor_desde=motor_desde
                )
                await session.commit()
                # Os pedidos fora da Logística (Resolvido/Cancelado/Perdimento no
                # Bling), pelo índice — DEPOIS da Logística: a mesma chave.
                resumo["indice"] = await descobrir_indice(
                    session, regras, agora=agora, motor_desde=motor_desde
                )
                await session.commit()
                _marca("logistica", t0)
        t0 = _time.monotonic()
        resumo["presos"] = await aposentar_enviando_presos(session, agora=agora)
        await session.commit()
        resumo["decididas"] = await decidir_vencidas(
            session,
            agora=agora,
            motor_desde=motor_desde,
            prazo_envio=comeco + ORCAMENTO_ENVIO_S,
        )
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
