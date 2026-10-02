"""Devoluções e disputas de Shopee e TikTok no atendimento (RF2, 01/10/2026).

A Logística JÁ lê os casos de pós-venda das duas, a loja inteira, em lote
(`logistica_shopee.sweep_pos_venda` e `logistica_tiktok.sweep_pos_venda`):
o caso que vale para o pedido (o VIVO mais recente) fica na assinatura da
linha da Logística — `logistica.meli_status.return_status` (+ `return_type`
na TikTok) — com a data da mudança em `logistica.status_datas.return_status`.
O pedido que passou por "Aguardando Devolução" ganha ainda, em
`devolucao_rastreio` (grão de pedido do Bling), o id do caso na plataforma
(`devolucao_id_auto`), o tipo (`devolucao_tipo_auto`), a ação pendente da
loja e o PRAZO (`acao_auto`/`prazo_acao_auto` — só quando foram lidos para o
status de agora, `acao_vale`) e a abertura (`devolucao_criada_em`). O caso,
o status e o prazo vêm daí —

  caso da Logística → uma linha em `atendimento_reclamacoes` (`dados.fonte =
  'logistica'`) ligada à conversa do chat daquele pedido (quando existe) →
  `etiqueta.recalcular_etiqueta` nas conversas do pedido.

A CONVERSA DA DEVOLUÇÃO (02/10/2026). Como no ML (`reclamacoes.py`), o caso
ABERTO cujo pedido não tem conversa com o comprador (nenhum chat ligado ao
pedido) ganha uma conversa `canal = 'reclamacao'` — da loja do caso, com o
comprador, o pedido e as linhas do sistema que contam a história ("Devolução
aberta na Shopee: Produto com defeito. Pede: Devolução + reembolso.", cada
status novo, o encerramento). Em produção, 02/10/2026: 26 das 75 abertas da
Shopee e 15 das 30 da TikTok não tinham conversa nenhuma — a devolução não
aparecia na caixa. Regras:
  • só a ABERTA ganha conversa; a encerrada sem conversa continua só guardada;
  • nunca duplica: UMA conversa `reclamacao` por pedido (achada pelo pedido,
    não pelo id do caso — o id da Logística aparece depois, e o caso novo do
    mesmo pedido entra na mesma conversa); as mensagens têm chave pelo id da
    LINHA (`atendimento_reclamacoes.id`), que não muda;
  • a conversa nasce BLOQUEADA, "só leitura" (como a do ML): sem caixa de
    resposta, sem IA, o envio recusa; a vez e o prazo são os da plataforma
    (a ação pendente da loja com prazo), não de quem falou por último;
  • quando o comprador abrir o chat e ele for ligado ao pedido, o cartão
    aparece TAMBÉM no chat (o cartão casa pelo pedido) e a linha passa a
    apontar para o chat; a conversa da devolução continua, com a história.

O MOTIVO DO COMPRADOR (02/10/2026). A Logística não guarda o motivo, e o
`motivo` desta tabela guardava o TIPO de solução ("Devolução + reembolso"),
não o que o comprador alegou. Agora o tipo vai para `dados.solucao` (o cartão
mostra "Pede: …") e o motivo é lido na plataforma, só GET, medido em
produção em 02/10/2026:
  • Shopee: `v2.returns.get_return_detail` → `reason` (código: CHANGE_MIND,
    FUNCTIONAL_DMG, WRONG_ITEM, ITEM_MISSING, NOT_RECEIPT, SUSPICIOUS_PARCEL…)
    → `MOTIVOS_SHOPEE` (português); `return_solution` (0 devolução +
    reembolso, 1 só reembolso) completa o tipo quando a Logística não o tem.
    O `text_reason` (o texto livre do comprador) NUNCA é guardado;
  • TikTok: não há GET do detalhe do caso (`returns/{id}` = 404); a linha do
    tempo (`returns/{id}/records`, `locale=pt-BR`) traz no evento do
    COMPRADOR que abriu o caso (`ORDER_RETURN`/`ORDER_REFUND`) o
    `reason_text` — o rótulo da TikTok, já em português ("Item com defeito",
    "O item não corresponde à descrição"). A `note` (texto livre) NUNCA é
    guardada.
COTA MÍNIMA: só as ABERTAS novas ou que mudaram de status desde a última
leitura; no máximo `MAX_LEITURAS_RODADA` chamadas por rodada (as que vão
ganhar conversa primeiro, depois o prazo mais curto — o resto entra na rodada
seguinte); a leitura que falhou só é tentada de novo depois de
`reclamacoes.RETENTAR_FALHA` (o comprador, no máximo
`MAX_TENTATIVAS_COMPRADOR` vezes, e nunca com o chat do pedido já
ligado); a loja que falha (`MAX_ERROS_LOJA`) sai da
rodada sem parar as outras. As chamadas são feitas ANTES das gravações (sem
segurar conversa travada enquanto a plataforma responde). Sem `fabrica_cliente`
(os testes, quem só quer ligar) nada sai para a plataforma.

O TIPO (o que a etiqueta mostra):
  • disputa (a plataforma julgando) = `reclamacao` → etiqueta Reclamação:
    Shopee JUDGING / SELLER_DISPUTE; TikTok REJECT_RECEIVE_PACKAGE (a loja
    recusou o pacote e a TikTok analisa);
  • o resto (pedido de devolução ou de reembolso) = `devolucao` → Devolução.

ABERTA (`encerrada_em IS NULL`) — o "vivo" da Logística, com uma exceção:
  • encerrada: os status finais (`logistica_rules.RETURN_ENCERRADO`: Shopee
    CANCELLED/CLOSED; TikTok cancelado, recusado e concluído);
  • Shopee ACCEPTED/REFUND_PAID = o reembolso JÁ foi pago ao comprador
    (medido em 17/09: a Shopee deixa o caso em ACCEPTED para sempre — a
    Logística o mantém "vivo" por causa do pacote). No atendimento ele só
    conta como aberto enquanto a Shopee ainda espera algo da loja com prazo
    no futuro (conferir o pacote, evidência); o pacote que volta continua
    marcado pelo Bling em Aguardando Devolução, que a etiqueta já lê.

O STATUS NA TELA (`STATUS_TELA`, a mesma tabela do cartão
`AtendimentoReclamacao.vue`): o que cada status quer dizer para quem atende,
conferido com os rótulos da Logística (`logistica_rules._SHOPEE_RETURN_
LABELS_PT`, `_TIKTOK_RETURN_LABELS_PT`). Status fora da tabela cai no texto
da Logística e, sem ele, no código legível — nunca some.

O ID: `devolucao_id_auto` quando a Logística o tem (return_sn da Shopee,
return_id da TikTok); sem ele, `"pedido <nº>"` (`reclamacoes.PREFIXO_SEM_ID`),
trocado pelo id quando ele aparecer. A Logística guarda UM caso por pedido:
o caso novo de um pedido encerra aqui o anterior que ainda estava aberto.

Caso encerrado há mais de `reclamacoes.JANELA_ENCERRADAS` que nunca entrou
aqui fica de fora (história). Cada caso grava num SAVEPOINT: um caso com
problema não derruba os outros. Não commita (salvo `commit_a_cada`, que o
cron usa para não segurar as conversas travadas até o fim). Sem dado pessoal
no log: só ids, contagens e o código do erro.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AtendimentoConversa,
    AtendimentoPedidoComprador,
    AtendimentoReclamacao,
    DevolucaoRastreio,
    Integration,
    Logistica,
)
from app.services import logistica_rules
from app.services.atendimento import constantes, etiqueta_fatos, gravar, lojas, reclamacoes
from app.services.atendimento.constantes import (
    AUTOR_SISTEMA,
    CANAL_RECLAMACAO,
    CHAVE_PRAZO_LIDO_EM,
    CHAVE_PRAZO_PLATAFORMA,
    CHAVE_VEZ_DA_LOJA,
    CONVERSA_BLOQUEADA,
    CONVERSA_FECHADA,
    RECLAMACAO_TIPO_DEVOLUCAO,
    RECLAMACAO_TIPO_RECLAMACAO,
)

logger = structlog.get_logger()

FONTE_LOGISTICA = "logistica"
PLATAFORMAS_LIGADAS = ("shopee", "tiktok")
MOTIVO_ETIQUETA = "devolução lida pela Logística"

FabricaCliente = Callable[[Integration], Awaitable[Any]]

# Como a Logística escreve a plataforma (`logistica.plataforma`, texto livre).
_ROTULOS = {
    **dict.fromkeys(logistica_rules._SHOPEE_PLATAFORMAS, "shopee"),
    **dict.fromkeys(logistica_rules._TIKTOK_PLATAFORMAS, "tiktok"),
}

# A plataforma julgando: é reclamação (disputa), não só devolução.
STATUS_DISPUTA = {
    "shopee": frozenset({"JUDGING", "SELLER_DISPUTE"}),
    "tiktok": frozenset({"REJECT_RECEIVE_PACKAGE"}),
}
# Shopee: o reembolso já saiu para o comprador (ver o topo).
SHOPEE_REEMBOLSADA = frozenset({"ACCEPTED", "REFUND_PAID"})

# ── O que a tela mostra ───────────────────────────────────────────────────
# O status do caso em português, para quem atende (a mesma tabela de
# apps/web/components/AtendimentoReclamacao.vue, STATUS_PLATAFORMA).
# Significados conferidos com a Logística (`logistica_rules`): Shopee
# REQUESTED = o comprador pediu e a loja ainda não respondeu; PROCESSING = em
# andamento (aceita, pacote voltando); JUDGING = a Shopee julgando a disputa;
# SELLER_DISPUTE = a loja contestou; ACCEPTED/REFUND_PAID = reembolso pago.
# TikTok RETURN_OR_REFUND_REQUEST_PENDING = pedido esperando a loja;
# AWAITING_BUYER_SHIP = aprovado, esperando o comprador postar;
# REJECT_RECEIVE_PACKAGE = a loja recusou o pacote recebido (a TikTok analisa).
STATUS_TELA: dict[str, dict[str, str]] = {
    "shopee": {
        "REQUESTED": "Pedido de devolução aberto",
        "PROCESSING": "Em andamento",
        "JUDGING": "Em análise pela Shopee (disputa)",
        "SELLER_DISPUTE": "Loja contestou",
        "ACCEPTED": "Aceita — reembolso pago ao comprador",
        "REFUND_PAID": "Reembolso pago ao comprador",
        "CANCELLED": "Cancelada",
        "CLOSED": "Encerrada pela Shopee",
    },
    "tiktok": {
        "RETURN_OR_REFUND_REQUEST_PENDING": "Pedido de devolução/reembolso pendente",
        "AWAITING_BUYER_SHIP": "Esperando o comprador enviar",
        "BUYER_SHIPPED_ITEM": "Comprador enviou o produto",
        "AWAITING_BUYER_RESPONSE": "Esperando resposta do comprador",
        "REJECT_RECEIVE_PACKAGE": "Recebimento recusado",
        "RETURN_OR_REFUND_REQUEST_SUCCESS": "Concluída — reembolso pago",
        "RETURN_OR_REFUND_REQUEST_COMPLETE": "Concluída — reembolso pago",
        "RETURN_OR_REFUND_REQUEST_CANCEL": "Cancelada pelo comprador",
        "RETURN_OR_REFUND_REQUEST_REJECT": "Recusada pela loja",
        "REFUND_OR_RETURN_REQUEST_REJECT": "Recusada pela loja",
    },
}

# O motivo da Shopee (`reason` do get_return_detail) em português — a mesma
# tabela do cartão (MOTIVOS_SHOPEE). Os seis primeiros apareceram nas 40
# abertas lidas em 02/10/2026; os outros são os da documentação da Shopee
# e os que a Central de chamados já viu (`chamados_devolucao._SHOPEE_REASON_PT`).
# Código fora da tabela vira texto legível ("ITEM_NOT_FIT" → "Item not fit").
MOTIVOS_SHOPEE: dict[str, str] = {
    "CHANGE_MIND": "Mudou de ideia (desistiu da compra)",
    "FUNCTIONAL_DMG": "Produto com defeito (não funciona)",
    "WRONG_ITEM": "Recebeu produto errado",
    "ITEM_MISSING": "Falta produto no pacote",
    "NOT_RECEIPT": "Não recebeu o produto",
    "SUSPICIOUS_PARCEL": "Pacote vazio ou violado",
    "MISSING_ITEM": "Falta produto no pacote",
    "PHYSICAL_DMG": "Produto danificado (avaria)",
    "ITEM_DAMAGED": "Produto danificado",
    "DIFFERENT_DESCRIPTION": "Diferente do anúncio",
    "NOT_AS_DESCRIBED": "Diferente do anúncio",
    "ITEM_FAKE": "Produto falsificado",
    "EXPECTATION_FAILED": "Não atendeu à expectativa",
    "ITEM_NOT_FIT": "Não serviu (tamanho ou modelo)",
    "MUTUAL_AGREE": "Acordo entre comprador e loja",
    "OTHER": "Outro motivo",
}
# Sem motivo de verdade: não grava nada.
_MOTIVOS_VAZIOS = frozenset({"NONE", "NO_REASON", "0"})
# `return_solution` do detalhe da Shopee → o tipo do caso (como a TikTok o chama).
SOLUCAO_DA_SHOPEE = {0: "RETURN_AND_REFUND", 1: "REFUND"}
# O tipo de solução em português ("Pede: …" no cartão). Até 02/10/2026 este
# texto ia no `motivo` — a linha antiga é reconhecida por ele (`_motivo_legado`).
SOLUCOES_PT = logistica_rules.TIKTOK_RETURN_TYPE_LABELS_PT
# O evento do COMPRADOR que abre o caso na linha do tempo da TikTok (medido
# em 02/10/2026: ORDER_RETURN e ORDER_REFUND); é dele o `reason_text`.
EVENTOS_PEDIDO_TIKTOK = frozenset({"ORDER_RETURN", "ORDER_REFUND", "ORDER_REPLACEMENT"})

# ── A cota de leitura na plataforma ───────────────────────────────────────
# Chamadas por rodada (motivo + comprador, todas as lojas). A primeira rodada
# tem ~105 abertas para ler (produção, 02/10/2026): espalha por ~5 rodadas.
MAX_LEITURAS_RODADA = 30
# Falhas de uma loja numa rodada até ela sair da rodada (token, 403, 5xx).
MAX_ERROS_LOJA = 2
# Leituras do comprador por caso (uma por `RETENTAR_FALHA`): o pedido que a
# plataforma devolve sem o comprador não é perguntado a cada hora para sempre.
MAX_TENTATIVAS_COMPRADOR = 3

# ── A conversa da devolução ───────────────────────────────────────────────
# Conversas do pedido que NÃO são com o comprador: a da reclamação e a da
# avaliação (RF8; o nome vem de `constantes` quando ela existir).
CANAIS_SEM_O_COMPRADOR = frozenset(
    {CANAL_RECLAMACAO, getattr(constantes, "CANAL_AVALIACAO", "avaliacao")}
)
_NA = {"shopee": "na Shopee", "tiktok": "no TikTok"}
_DA = {"shopee": "da Shopee", "tiktok": "do TikTok"}

_LOTE_IN = 1000


def _agora() -> datetime:
    return datetime.now(UTC)


def _iso(quando: datetime | None) -> str | None:
    return quando.astimezone(UTC).isoformat(timespec="seconds") if quando else None


def _texto(bruto: Any) -> str:
    return "" if bruto is None else str(bruto).strip()


def _inteiro(bruto: Any) -> int:
    try:
        return int(bruto)
    except (TypeError, ValueError):
        return 0


def _utc(quando: datetime | None) -> datetime | None:
    if quando is None:
        return None
    return quando.replace(tzinfo=UTC) if quando.tzinfo is None else quando.astimezone(UTC)


def plataforma_da_logistica(rotulo: str | None) -> str | None:
    """'Shopee' / 'TikTok Shop' → 'shopee' / 'tiktok'; outra → None."""
    return _ROTULOS.get(_texto(rotulo).lower())


def _legivel(codigo: str) -> str:
    """'ITEM_NOT_FIT' / 'wrong_size' → 'Item not fit' / 'Wrong size' (o código desconhecido)."""
    texto = codigo.replace("_", " ").strip().lower()
    return texto[:1].upper() + texto[1:]


def _e_codigo(texto: str) -> bool:
    """Parece código da plataforma (não texto de gente)? 'not_working_item', 'CHANGE_MIND'."""
    return bool(re.fullmatch(r"[A-Za-z0-9_]+", texto)) and ("_" in texto or texto.isupper())


def motivo_legivel(bruto: str | None) -> str | None:
    """O motivo em português: código do ML ou da Shopee pela tabela; código
    desconhecido, legível; texto (o rótulo da TikTok, o já traduzido) passa."""
    texto = _texto(bruto)
    if not texto:
        return None
    if texto in reclamacoes.MOTIVOS_ML:
        return reclamacoes.MOTIVOS_ML[texto]
    if texto.upper() in MOTIVOS_SHOPEE:
        return MOTIVOS_SHOPEE[texto.upper()]
    if _e_codigo(texto) or re.fullmatch(r"[a-z0-9]+", texto):
        return _legivel(texto)
    return texto


def status_tela(
    plataforma: str | None, status: str | None, tipo_caso: str | None = None
) -> str | None:
    """O status do caso em português (`STATUS_TELA`); fora dela, o texto da
    Logística; sem ele, o código legível. None sem status."""
    codigo = _texto(status).upper()
    if not codigo:
        return None
    texto = STATUS_TELA.get(_texto(plataforma).lower(), {}).get(codigo)
    if texto:
        return texto
    da_logistica = logistica_rules.devolucao_status_pt(
        plataforma, {"return_status": codigo, "return_type": tipo_caso or ""}
    )
    return da_logistica or _legivel(codigo)


@dataclass(frozen=True)
class CasoLogistica:
    """O caso de devolução de UM pedido, como a Logística o guarda."""

    plataforma: str
    pedido_marketplace: str
    status: str
    pedido_bling: str | None = None
    conta: str | None = None
    # REFUND (só reembolso) | RETURN_AND_REFUND | REPLACEMENT
    tipo_caso: str | None = None
    # Quando o status do caso mudou (o carimbo da Logística).
    mudou_em: datetime | None = None
    logistica_id: UUID | None = None
    # De `devolucao_rastreio` (só quando o pedido passou por 83957).
    return_id: str | None = None
    acao: str | None = None
    prazo: datetime | None = None
    criada_em: datetime | None = None


def tipo_do_caso(caso: CasoLogistica) -> str:
    if caso.status in STATUS_DISPUTA.get(caso.plataforma, ()):
        return RECLAMACAO_TIPO_RECLAMACAO
    return RECLAMACAO_TIPO_DEVOLUCAO


def caso_aberto(caso: CasoLogistica, agora: datetime) -> bool:
    """O caso ainda pede a atenção da loja? (ver o topo do módulo)"""
    if not caso.status or caso.status in logistica_rules.RETURN_ENCERRADO:
        return False
    if caso.plataforma == "shopee" and caso.status in SHOPEE_REEMBOLSADA:
        return caso.prazo is not None and caso.prazo > agora
    return True


def status_texto(caso: CasoLogistica) -> str | None:
    """O status do caso em português (`status_tela`)."""
    return status_tela(caso.plataforma, caso.status, caso.tipo_caso)


def solucao_do_caso(tipo_caso: str | None) -> str | None:
    """O que o comprador PEDIU ("Devolução + reembolso", "Só reembolso (…)", "Troca")."""
    if not tipo_caso:
        return None
    return SOLUCOES_PT.get(tipo_caso.upper())


def externo_id_do_caso(caso: CasoLogistica) -> str:
    return caso.return_id or f"{reclamacoes.PREFIXO_SEM_ID}{caso.pedido_marketplace}"


def nome_do_tipo(tipo: str | None) -> str:
    """Como o caso se chama na Shopee/TikTok: a disputa é "Disputa" (como no cartão)."""
    return "Disputa" if tipo == RECLAMACAO_TIPO_RECLAMACAO else "Devolução"


# ── O motivo, a partir do que a plataforma devolve (puro) ─────────────────


def motivo_shopee(detalhe: dict | None) -> tuple[str | None, str | None]:
    """(código, motivo em português) do `get_return_detail`. Só o `reason` —
    o `text_reason` é texto livre do comprador e não é lido."""
    codigo = _texto((detalhe or {}).get("reason")).upper()
    if not codigo or codigo in _MOTIVOS_VAZIOS:
        return None, None
    return codigo, motivo_legivel(codigo)


def motivo_tiktok(registros: list[dict] | None) -> str | None:
    """O `reason_text` do evento do COMPRADOR que abriu o caso (o mais recente).

    É o rótulo da TikTok (pedido com `locale=pt-BR`), não texto do comprador —
    o texto livre dele vem em `note`, que não é lido.
    """
    do_comprador = [
        r
        for r in registros or []
        if isinstance(r, dict)
        and _texto(r.get("role")).upper() == "BUYER"
        and _texto(r.get("reason_text"))
    ]
    exatos = [r for r in do_comprador if _texto(r.get("event")).upper() in EVENTOS_PEDIDO_TIKTOK]
    candidatos = exatos or [
        r for r in do_comprador if _texto(r.get("event")).upper().startswith("ORDER_")
    ]
    if not candidatos:
        return None
    ultimo = max(candidatos, key=lambda r: _inteiro(r.get("create_time")))
    motivo = motivo_legivel(_texto(ultimo.get("reason_text")))
    return motivo[:300] if motivo else None


# ── Leitura do banco ──────────────────────────────────────────────────────


def acao_vale(dr: DevolucaoRastreio | None, status: str) -> bool:
    """A ação pendente e o prazo do rastreio ainda valem para o status de agora?

    Foram lidos junto com `devolucao_status_auto` — o status de ENTÃO. O
    rastreio só é relido enquanto o pedido está em Aguardando Devolução; a
    Logística anda depois. Status diferente = a ação já passou (produção,
    02/10/2026: 7 das abertas — TikTok REJECT_RECEIVE_PACKAGE com o
    "Confirmar ou recusar o pacote" de BUYER_SHIPPED_ITEM vencido desde
    23/09, a loja JÁ tinha recusado; Shopee SELLER_DISPUTE com o "Conferir o
    pacote" de PROCESSING, a loja JÁ tinha contestado). Sem ela, o caso não
    entra em "Falta responder" por um prazo que não existe mais.
    """
    if dr is None:
        return False
    de_entao = _texto(dr.devolucao_status_auto).upper()
    return not de_entao or de_entao == status


async def casos_da_logistica(
    session: AsyncSession, pedidos: Collection[str] | None = None
) -> list[CasoLogistica]:
    """Um caso por (plataforma, pedido): o da linha com a mudança mais recente."""
    plataforma_col = func.lower(func.trim(Logistica.plataforma))
    status_col = func.upper(func.coalesce(Logistica.meli_status["return_status"].astext, ""))
    consulta = select(
        Logistica.id,
        Logistica.plataforma,
        Logistica.conta,
        Logistica.pedido_bling,
        Logistica.pedido_marketplace,
        Logistica.meli_status,
        Logistica.status_datas,
    ).where(
        plataforma_col.in_(tuple(_ROTULOS)),
        status_col != "",
        func.coalesce(Logistica.pedido_marketplace, "") != "",
    )
    if pedidos is not None:
        consulta = consulta.where(Logistica.pedido_marketplace.in_(list(pedidos)))
    linhas = (await session.execute(consulta)).all()

    melhores: dict[tuple[str, str], tuple[datetime, Any]] = {}
    for linha in linhas:
        plataforma = plataforma_da_logistica(linha.plataforma)
        pedido = _texto(linha.pedido_marketplace)
        if plataforma is None or not pedido:
            continue
        carimbo = (linha.status_datas or {}).get("return_status") or {}
        mudou = reclamacoes.data_ml(carimbo.get("em")) if isinstance(carimbo, dict) else None
        chave = (plataforma, pedido)
        ordem = mudou or datetime.min.replace(tzinfo=UTC)
        if chave not in melhores or ordem > melhores[chave][0]:
            melhores[chave] = (ordem, (linha, mudou))

    rastreios: dict[str, DevolucaoRastreio] = {}
    bling = sorted({_texto(v[1][0].pedido_bling) for v in melhores.values()} - {""})
    for inicio in range(0, len(bling), _LOTE_IN):
        lote = bling[inicio : inicio + _LOTE_IN]
        for dr in (
            await session.execute(
                select(DevolucaoRastreio).where(DevolucaoRastreio.pedido_bling.in_(lote))
            )
        ).scalars():
            rastreios[dr.pedido_bling] = dr

    casos = []
    for (plataforma, pedido), (_, (linha, mudou)) in melhores.items():
        meli = linha.meli_status or {}
        dr = rastreios.get(_texto(linha.pedido_bling))
        # O rastreio da devolução só vale se for DESTA plataforma (o pedido do
        # Bling não muda de plataforma, mas a fonte do automático diz qual leu).
        if dr is not None and _texto(dr.fonte_auto).lower() != plataforma:
            dr = None
        tipo_caso = _texto(meli.get("return_type")).upper() or (
            _texto(dr.devolucao_tipo_auto).upper() if dr is not None else ""
        )
        status = _texto(meli.get("return_status")).upper()
        casos.append(
            CasoLogistica(
                plataforma=plataforma,
                pedido_marketplace=pedido,
                status=status,
                pedido_bling=_texto(linha.pedido_bling) or None,
                conta=_texto(linha.conta) or None,
                tipo_caso=tipo_caso or None,
                mudou_em=mudou,
                logistica_id=linha.id,
                return_id=(_texto(dr.devolucao_id_auto) or None) if dr is not None else None,
                acao=(_texto(dr.acao_auto) or None) if acao_vale(dr, status) else None,
                prazo=_utc(dr.prazo_acao_auto) if acao_vale(dr, status) else None,
                criada_em=_utc(dr.devolucao_criada_em) if dr is not None else None,
            )
        )
    return casos


async def _existentes(
    session: AsyncSession, casos: list[CasoLogistica]
) -> dict[tuple[str, str], list[AtendimentoReclamacao]]:
    """As linhas que esta fonte já escreveu, por (plataforma, pedido)."""
    saida: dict[tuple[str, str], list[AtendimentoReclamacao]] = {}
    pedidos = sorted({c.pedido_marketplace for c in casos})
    for inicio in range(0, len(pedidos), _LOTE_IN):
        lote = pedidos[inicio : inicio + _LOTE_IN]
        for r in (
            await session.execute(
                select(AtendimentoReclamacao)
                .where(
                    AtendimentoReclamacao.plataforma.in_(PLATAFORMAS_LIGADAS),
                    AtendimentoReclamacao.pedido_marketplace.in_(lote),
                    AtendimentoReclamacao.dados["fonte"].astext == FONTE_LOGISTICA,
                )
                .order_by(AtendimentoReclamacao.created_at.desc())
            )
        ).scalars():
            saida.setdefault((r.plataforma, r.pedido_marketplace or ""), []).append(r)
    return saida


async def _conversas_dos_pedidos(
    session: AsyncSession, casos: list[CasoLogistica]
) -> dict[tuple[str, str], dict[str, bool]]:
    """Os canais das conversas de cada pedido → já tem o comprador? (uma
    consulta por lote): quem já tem chat, e a conversa da devolução sem comprador."""
    saida: dict[tuple[str, str], dict[str, bool]] = {}
    pedidos = sorted({c.pedido_marketplace for c in casos})
    for inicio in range(0, len(pedidos), _LOTE_IN):
        lote = pedidos[inicio : inicio + _LOTE_IN]
        for plataforma, pedido, canal, comprador in (
            await session.execute(
                select(
                    AtendimentoConversa.plataforma,
                    AtendimentoConversa.pedido_marketplace,
                    AtendimentoConversa.canal,
                    AtendimentoConversa.comprador_id,
                ).where(
                    AtendimentoConversa.plataforma.in_(PLATAFORMAS_LIGADAS),
                    AtendimentoConversa.pedido_marketplace.in_(lote),
                )
            )
        ).all():
            canais = saida.setdefault((plataforma, pedido), {})
            canais[canal] = canais.get(canal, False) or bool(_texto(comprador))
    return saida


async def _compradores_do_indice(
    session: AsyncSession, casos: list[CasoLogistica]
) -> dict[tuple[str, str], str]:
    """O comprador do pedido pelo índice (`atendimento_pedidos_comprador`), sem API."""
    saida: dict[tuple[str, str], str] = {}
    pedidos = sorted({c.pedido_marketplace for c in casos})
    for inicio in range(0, len(pedidos), _LOTE_IN):
        lote = pedidos[inicio : inicio + _LOTE_IN]
        for plataforma, pedido, comprador in (
            await session.execute(
                select(
                    AtendimentoPedidoComprador.plataforma,
                    AtendimentoPedidoComprador.pedido,
                    AtendimentoPedidoComprador.comprador_id,
                ).where(
                    AtendimentoPedidoComprador.plataforma.in_(PLATAFORMAS_LIGADAS),
                    AtendimentoPedidoComprador.pedido.in_(lote),
                )
            )
        ).all():
            if _texto(comprador):
                saida[(plataforma, pedido)] = _texto(comprador)
    return saida


class _Integracoes:
    """A integração da conta da Logística (o mesmo casamento da Logística), com cache."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], Integration | None] = {}

    async def objeto(
        self, session: AsyncSession, plataforma: str, conta: str | None
    ) -> Integration | None:
        chave = (plataforma, _texto(conta).lower())
        if not chave[1]:
            return None
        if chave not in self._cache:
            if plataforma == "shopee":
                from app.services.logistica_shopee import _shopee_integration_for_conta as achar
            else:
                from app.services.logistica_tiktok import _tiktok_integration_for_conta as achar
            self._cache[chave] = await achar(session, conta)
        return self._cache[chave]

    async def de(self, session: AsyncSession, plataforma: str, conta: str | None) -> UUID | None:
        integ = await self.objeto(session, plataforma, conta)
        return integ.id if integ is not None else None


# ── Leitura na plataforma (só GET, com cota) ──────────────────────────────


@dataclass
class LeituraCaso:
    """O que se leu na plataforma para UM caso nesta rodada."""

    motivo: str | None = None
    motivo_codigo: str | None = None
    # O tipo pelo detalhe (Shopee `return_solution`), quando a Logística não o tem.
    tipo_caso: str | None = None
    motivo_lido: bool = False
    motivo_falhou: bool = False
    comprador_id: str | None = None
    comprador_nome: str | None = None
    # A leitura do comprador foi tentada (deu certo ou não): só de novo depois
    # de `reclamacoes.RETENTAR_FALHA`, e no máximo `MAX_TENTATIVAS_COMPRADOR`.
    comprador_tentado: bool = False
    # A cota acabou antes deste caso: a conversa nova espera a rodada seguinte
    # (nasce com o motivo e o comprador, em vez de nascer sem eles).
    adiada: bool = False


def _motivo_falhou_ha_pouco(dados: dict, status: str, agora: datetime) -> bool:
    falha = dados.get("motivo_falhou")
    if not isinstance(falha, dict) or falha.get("status") != status:
        return False
    quando = reclamacoes.data_ml(falha.get("em"))
    return quando is not None and agora - quando < reclamacoes.RETENTAR_FALHA


def _comprador_sem_tentativa(dados: dict, agora: datetime) -> bool:
    """Pode ler o comprador agora? Nem há pouco (`RETENTAR_FALHA`), nem já
    `MAX_TENTATIVAS_COMPRADOR` vezes."""
    if _inteiro(dados.get("comprador_tentativas")) >= MAX_TENTATIVAS_COMPRADOR:
        return False
    quando = reclamacoes.data_ml(dados.get("comprador_tentado_em"))
    return quando is None or agora - quando >= reclamacoes.RETENTAR_FALHA


def quer_motivo(caso: CasoLogistica, linha: AtendimentoReclamacao | None, agora: datetime) -> bool:
    """Ler o motivo deste caso agora? Só o ABERTO com id na plataforma, novo ou
    com status mudado desde a última leitura, e sem falha recente."""
    if caso.return_id is None or not caso_aberto(caso, agora):
        return False
    dados = (linha.dados if linha is not None else None) or {}
    if dados.get("motivo_lido_status") == caso.status:
        return False
    return not _motivo_falhou_ha_pouco(dados, caso.status, agora)


class _Leitores:
    """O cliente de API de cada loja, com a falha contida por loja."""

    def __init__(self, fabrica: FabricaCliente) -> None:
        self._fabrica = fabrica
        self._clientes: dict[UUID, Any] = {}
        self._erros: dict[UUID, int] = {}

    def fora(self, integ: Integration) -> bool:
        return self._erros.get(integ.id, 0) >= MAX_ERROS_LOJA

    def falhou(self, integ: Integration) -> None:
        self._erros[integ.id] = self._erros.get(integ.id, 0) + 1

    async def de(self, integ: Integration) -> Any | None:
        """O cliente da loja; None = a loja está fora desta rodada."""
        if self.fora(integ):
            return None
        if integ.id not in self._clientes:
            try:
                self._clientes[integ.id] = await self._fabrica(integ)
            except Exception as exc:  # noqa: BLE001 — a loja que falha não para as outras
                self._erros[integ.id] = MAX_ERROS_LOJA
                logger.warning(
                    "atendimento_devolucao_cliente_falhou",
                    integration_id=str(integ.id),
                    erro=reclamacoes._erro(exc),
                )
                return None
        return self._clientes[integ.id]


async def _ler_motivo(cliente: Any, caso: CasoLogistica, leitura: LeituraCaso) -> None:
    """UMA chamada GET: o detalhe da Shopee ou a linha do tempo da TikTok. Levanta em erro."""
    if caso.plataforma == "shopee":
        detalhe = await cliente.get_return_detail(caso.return_id)
        leitura.motivo_codigo, leitura.motivo = motivo_shopee(detalhe)
        leitura.tipo_caso = SOLUCAO_DA_SHOPEE.get((detalhe or {}).get("return_solution"))
    else:
        registros = await cliente.get_return_records_strict(caso.return_id)
        leitura.motivo = motivo_tiktok(registros)
    leitura.motivo_lido = True


async def _ler_comprador(cliente: Any, caso: CasoLogistica, leitura: LeituraCaso) -> None:
    """UMA chamada GET do pedido: o id do comprador (o mesmo do chat) e, na Shopee, o apelido."""
    if caso.plataforma == "shopee":
        pedido = await cliente.get_order_buyer(caso.pedido_marketplace)
        leitura.comprador_nome = _texto((pedido or {}).get("buyer_username")) or None
        bruto = (pedido or {}).get("buyer_user_id")
    else:
        pedido = await cliente.get_order_detail(caso.pedido_marketplace)
        bruto = (pedido or {}).get("user_id")
    comprador = _texto(bruto)
    leitura.comprador_id = comprador if comprador not in ("", "0") else None


async def ler_na_plataforma(
    session: AsyncSession,
    casos: list[CasoLogistica],
    existentes: dict[tuple[str, str], list[AtendimentoReclamacao]],
    integracoes: _Integracoes,
    agora: datetime,
    fabrica_cliente: FabricaCliente | None,
    resumo: dict,
) -> dict[tuple[str, str], LeituraCaso]:
    """O motivo (e o comprador de quem vai ganhar conversa) dos casos ABERTOS
    novos ou mudados, dentro da cota (ver o topo). Não grava nada; nunca levanta
    por causa da plataforma. Sem `fabrica_cliente`, só o índice de compradores."""
    abertos = [c for c in casos if caso_aberto(c, agora)]
    if not abertos:
        return {}
    canais = await _conversas_dos_pedidos(session, abertos)
    # (caso, vai ganhar conversa, quer o comprador, quer o motivo)
    candidatos: list[tuple[CasoLogistica, bool, bool, bool]] = []
    for caso in abertos:
        chave = (caso.plataforma, caso.pedido_marketplace)
        linha = _escolher(existentes.get(chave, []), externo_id_do_caso(caso), chave[1])
        dados = (linha.dados if linha is not None else None) or {}
        # Vai ganhar a conversa da devolução (`_conversa_da_devolucao`): nem
        # chat com o comprador, nem a conversa da devolução de antes.
        do_pedido = canais.get(chave, {})
        com_chat = bool(set(do_pedido) - CANAIS_SEM_O_COMPRADOR)
        sem_conversa = CANAL_RECLAMACAO not in do_pedido and not com_chat
        # O comprador: da conversa que vai nascer, ou da que nasceu sem ele
        # (com o chat do pedido, o comprador já está nele: não pergunta).
        comprador = (
            sem_conversa or (do_pedido.get(CANAL_RECLAMACAO) is False and not com_chat)
        ) and _comprador_sem_tentativa(dados, agora)
        motivo = quer_motivo(caso, linha, agora)
        if sem_conversa or comprador or motivo:
            candidatos.append((caso, sem_conversa, comprador, motivo))
    if not candidatos:
        return {}

    leituras: dict[tuple[str, str], LeituraCaso] = {}
    do_indice = await _compradores_do_indice(
        session, [c for c, _, comprador, _ in candidatos if comprador]
    )
    for chave, comprador in do_indice.items():
        leituras[chave] = LeituraCaso(comprador_id=comprador)
    if fabrica_cliente is None:
        return leituras

    # Quem vai ganhar conversa primeiro; depois o prazo mais curto.
    candidatos.sort(
        key=lambda t: (
            not t[1],
            t[0].prazo is None,
            t[0].prazo or agora,
            -(t[0].mudou_em or agora).timestamp(),
        )
    )
    restantes = MAX_LEITURAS_RODADA
    leitores = _Leitores(fabrica_cliente)
    for caso, sem_conversa, comprador, motivo in candidatos:
        chave = (caso.plataforma, caso.pedido_marketplace)
        leitura = leituras.setdefault(chave, LeituraCaso())
        comprador = comprador and leitura.comprador_id is None
        if not (motivo or comprador):
            continue
        if restantes <= 0:
            resumo["leituras_adiadas"] += 1
            leitura.adiada = sem_conversa
            continue
        integ = await integracoes.objeto(session, caso.plataforma, caso.conta)
        if integ is None:
            continue
        cliente = await leitores.de(integ)
        if cliente is None:
            resumo["leituras_adiadas"] += 1
            continue
        for quer, ler in ((motivo, _ler_motivo), (comprador, _ler_comprador)):
            if not quer or restantes <= 0 or leitores.fora(integ):
                continue
            restantes -= 1
            if ler is _ler_comprador:
                leitura.comprador_tentado = True
            try:
                await ler(cliente, caso, leitura)
            except Exception as exc:  # noqa: BLE001 — o caso fica para depois, a loja segue
                leitores.falhou(integ)
                resumo["erros_leitura"] += 1
                if ler is _ler_motivo:
                    leitura.motivo_falhou = True
                logger.warning(
                    "atendimento_devolucao_leitura_falhou",
                    plataforma=caso.plataforma,
                    pedido=caso.pedido_marketplace,
                    leitura=ler.__name__.removeprefix("_ler_"),
                    erro=reclamacoes._erro(exc),
                )
                continue
            if ler is _ler_motivo:
                resumo["motivos_lidos"] += 1
    resumo["leituras"] = MAX_LEITURAS_RODADA - restantes
    return leituras


# ── Gravação ──────────────────────────────────────────────────────────────


def _escolher(
    existentes: list[AtendimentoReclamacao], externo_id: str, pedido: str
) -> AtendimentoReclamacao | None:
    """A linha que este caso atualiza (ver "O ID" no topo do módulo)."""
    sem_id = f"{reclamacoes.PREFIXO_SEM_ID}{pedido}"
    for r in existentes:
        if r.externo_id == externo_id:
            return r
    if externo_id != sem_id:
        # O id apareceu: a linha que esperava por ele passa a usá-lo.
        return next((r for r in existentes if r.externo_id == sem_id), None)
    # Sem id agora: a linha mais recente do pedido é a do caso atual.
    return existentes[0] if existentes else None


def _retrato(r: AtendimentoReclamacao) -> tuple:
    return (
        r.externo_id,
        r.tipo,
        r.status,
        r.encerrada_em is None,
        r.prazo_em,
        r.conversa_id,
    )


def _motivo_legado(r: AtendimentoReclamacao) -> bool:
    """O `motivo` ainda é o tipo de solução (como se gravava até 02/10/2026)?"""
    return (
        bool(r.motivo)
        and r.motivo in SOLUCOES_PT.values()
        and not (r.dados or {}).get("motivo_lido_em")
    )


def bloqueio_so_leitura(plataforma: str) -> str:
    """O que a caixa mostra no lugar da resposta (a conversa nasce bloqueada)."""
    return (
        f"Devolução {_DA.get(plataforma, plataforma)}: só leitura no DaVinci — as "
        f"respostas e as ações ficam {_NA.get(plataforma, plataforma)}."
    )


def mensagens_do_sistema(
    linha: AtendimentoReclamacao, aberto: bool, mudou_em: datetime | None, agora: datetime
) -> list[reclamacoes.MensagemReclamacao]:
    """As linhas do sistema da conversa da devolução: a abertura, o status
    atual (uma por status) e o encerramento. Texto da PLATAFORMA (tipo,
    motivo traduzido, solução, status), nunca do comprador. Chave pelo id da
    linha: o id do caso que aparece depois não duplica nada."""
    na = _NA.get(linha.plataforma, linha.plataforma)
    nome = nome_do_tipo(linha.tipo)
    dados = linha.dados or {}
    solucao = _texto(dados.get("solucao"))
    texto = f"{nome} aberta {na}" + (f": {linha.motivo}." if linha.motivo else ".")
    if solucao:
        texto += f" Pede: {solucao}."
    saida = [
        reclamacoes.MensagemReclamacao(
            f"sistema:abertura:{linha.id}",
            AUTOR_SISTEMA,
            texto,
            linha.aberta_em or agora,
            "texto",
            [],
            {},
        )
    ]
    situacao = _texto(dados.get("status_texto"))
    if aberto and linha.status and situacao:
        saida.append(
            reclamacoes.MensagemReclamacao(
                f"sistema:status:{linha.id}:{linha.status}",
                AUTOR_SISTEMA,
                f"{nome} {na}: {situacao}.",
                max(mudou_em or agora, linha.aberta_em or agora),
                "texto",
                [],
                {"status": linha.status},
            )
        )
    if not aberto and linha.encerrada_em is not None:
        saida.append(
            reclamacoes.MensagemReclamacao(
                f"sistema:encerramento:{linha.id}:{_iso(linha.encerrada_em)}",
                AUTOR_SISTEMA,
                f"{nome} encerrada {na}" + (f" — {situacao}." if situacao else "."),
                linha.encerrada_em,
                "texto",
                [],
                {"status": linha.status},
            )
        )
    return saida


def _dados_da_conversa(
    caso: CasoLogistica, linha: AtendimentoReclamacao, aberto: bool, agora: datetime
) -> dict[str, Any]:
    """O que a conversa da devolução guarda em `dados`: a vez e o prazo são da
    plataforma (a ação pendente da loja com prazo), não de quem falou por último."""
    vez = aberto and bool(caso.acao) and linha.prazo_em is not None
    return {
        "reclamacao_id": str(linha.id),
        "return_id": caso.return_id,
        "order_id": caso.pedido_marketplace,
        "status_plataforma": caso.status,
        "tipo_caso": (linha.dados or {}).get("tipo_caso"),
        CHAVE_VEZ_DA_LOJA: _iso(caso.mudou_em or agora) if vez else None,
        CHAVE_PRAZO_PLATAFORMA: _iso(linha.prazo_em) if aberto else None,
        CHAVE_PRAZO_LIDO_EM: _iso(agora),
    }


def _marca(linha: AtendimentoReclamacao, aberto: bool) -> list:
    """O que, mudando, faz a conversa da devolução ser regravada (senão, nada se escreve)."""
    return [
        linha.status,
        aberto,
        _iso(linha.encerrada_em),
        linha.motivo,
        _iso(linha.prazo_em),
        (linha.dados or {}).get("acao_pendente"),
    ]


async def _conversa_da_devolucao(
    session: AsyncSession,
    caso: CasoLogistica,
    linha: AtendimentoReclamacao,
    aberto: bool,
    integracao: Integration | None,
    leitura: LeituraCaso | None,
    conversas: list[AtendimentoConversa],
    agora: datetime,
) -> tuple[AtendimentoConversa | None, bool]:
    """A conversa `reclamacao` do pedido → (conversa, criada). Nasce só para o
    caso ABERTO sem conversa com o comprador; a que já existe (de um caso
    anterior do pedido, ou de antes de o chat aparecer) continua sendo
    escrita. None = o pedido não tem (nem ganha) conversa da devolução."""
    conversa = next((c for c in conversas if c.canal == CANAL_RECLAMACAO), None)
    criada = False
    if conversa is None:
        com_o_comprador = [c for c in conversas if c.canal not in CANAIS_SEM_O_COMPRADOR]
        if not aberto or com_o_comprador or integracao is None:
            return None, False
        if leitura is not None and leitura.adiada:
            return None, False  # a cota acabou: nasce na rodada seguinte, completa
        conversa, criada = await gravar.upsert_conversa(
            session,
            canal=None,
            integration=integracao,
            plataforma=caso.plataforma,
            canal_nome=CANAL_RECLAMACAO,
            externo_id=linha.externo_id,
            conta=await lojas.nome_da_loja(session, integracao) or None,
            comprador_id=leitura.comprador_id if leitura is not None else None,
            comprador_nome=leitura.comprador_nome if leitura is not None else None,
            pedido_marketplace=caso.pedido_marketplace,
            situacao=CONVERSA_BLOQUEADA,
            bloqueio_motivo=bloqueio_so_leitura(caso.plataforma),
            dados=_dados_da_conversa(caso, linha, aberto, agora),
        )
    marca = _marca(linha, aberto)
    comprador_novo = (
        leitura is not None and bool(leitura.comprador_id) and conversa.comprador_id is None
    )
    # Reaberta pela pessoa (fechar → reabrir deixa "aberta"): volta a ser só
    # leitura nesta rodada, mesmo sem novidade do caso.
    fora_do_lugar = conversa.situacao not in (CONVERSA_BLOQUEADA, CONVERSA_FECHADA)
    if (
        not criada
        and not comprador_novo
        and not fora_do_lugar
        and (linha.dados or {}).get("conversa_marca") == marca
    ):
        return conversa, False  # nada mudou desde a última escrita: nada se escreve

    if not await gravar.travar_linha(session, conversa):
        return conversa, criada
    conversa.dados = {
        **(conversa.dados or {}),
        **_dados_da_conversa(caso, linha, aberto, agora),
    }
    if conversa.comprador_id is None and leitura is not None and leitura.comprador_id:
        conversa.comprador_id = leitura.comprador_id[:128]
        conversa.comprador_nome = conversa.comprador_nome or leitura.comprador_nome
    # Só leitura (como a do ML). Fechada = a pessoa deu por resolvida: fica.
    bloqueio = bloqueio_so_leitura(caso.plataforma)
    if conversa.situacao != CONVERSA_FECHADA and (
        conversa.situacao != CONVERSA_BLOQUEADA or conversa.bloqueio_motivo != bloqueio
    ):
        conversa.situacao = CONVERSA_BLOQUEADA
        conversa.bloqueio_motivo = bloqueio

    dados = dict(linha.dados or {})
    mensagens = mensagens_do_sistema(linha, aberto, caso.mudou_em, agora)
    if linha.motivo and dados.get("abertura_sem_motivo"):
        # A abertura já foi escrita sem o motivo (a leitura veio depois).
        na = _NA.get(linha.plataforma, linha.plataforma)
        mensagens.append(
            reclamacoes.MensagemReclamacao(
                f"sistema:motivo:{linha.id}",
                AUTOR_SISTEMA,
                f"Motivo do comprador {na}: {linha.motivo}.",
                agora,
                "texto",
                [],
                {},
            )
        )
    for item in mensagens:
        _, nova = await gravar.gravar_mensagem(
            session,
            conversa,
            externo_id=item.externo_id,
            autor=item.autor,
            texto=item.texto,
            enviada_em=item.enviada_em,
            tipo=item.tipo,
            anexos=item.anexos,
            payload=item.payload,
            origem=None,
        )
        if nova and item.externo_id == f"sistema:abertura:{linha.id}":
            dados["abertura_sem_motivo"] = not linha.motivo
        elif nova and item.externo_id == f"sistema:motivo:{linha.id}":
            dados["abertura_sem_motivo"] = False
    # A vez e o prazo da plataforma podem ter mudado sem mensagem nova.
    gravar.recalcular(conversa)
    dados["conversa_marca"] = marca
    dados["conversa_reclamacao_id"] = str(conversa.id)
    linha.dados = dados
    await session.flush()
    return conversa, criada


async def ligar_caso(
    session: AsyncSession,
    caso: CasoLogistica,
    existentes: list[AtendimentoReclamacao],
    agora: datetime,
    integracao: Integration | None,
    leitura: LeituraCaso | None = None,
) -> tuple[AtendimentoReclamacao | None, bool, bool, bool]:
    """Grava o caso → (linha, criada, mudou, conversa criada). (None, …) =
    história, fica de fora. Não commita."""
    aberto = caso_aberto(caso, agora)
    if (
        not aberto
        and not existentes
        and (caso.mudou_em is None or caso.mudou_em < agora - reclamacoes.JANELA_ENCERRADAS)
    ):
        return None, False, False, False
    externo_id = externo_id_do_caso(caso)
    alvo = _escolher(existentes, externo_id, caso.pedido_marketplace)
    if caso.return_id is None and alvo is not None:
        # Sem o id agora (o rastreio da devolução sumiu): a linha não perde o
        # id que já tinha.
        externo_id = alvo.externo_id
    if alvo is None or alvo.externo_id != externo_id:
        # O mesmo caso pode já estar aqui por outro caminho (UNIQUE plataforma
        # + id): é ele que se atualiza — a linha sem id fica encerrada abaixo.
        mesma = (
            await session.execute(
                select(AtendimentoReclamacao).where(
                    AtendimentoReclamacao.plataforma == caso.plataforma,
                    AtendimentoReclamacao.externo_id == externo_id,
                )
            )
        ).scalar_one_or_none()
        if mesma is not None:
            alvo = mesma
    criada = alvo is None
    antes = None if alvo is None else _retrato(alvo)
    if alvo is None:
        alvo = AtendimentoReclamacao(
            plataforma=caso.plataforma, externo_id=externo_id, tipo=tipo_do_caso(caso), dados={}
        )
        session.add(alvo)
    anteriores = dict(alvo.dados or {})
    alvo.externo_id = externo_id
    alvo.tipo = tipo_do_caso(caso)
    alvo.status = caso.status
    alvo.pedido_marketplace = caso.pedido_marketplace
    if integracao is not None:
        alvo.integration_id = integracao.id
    alvo.prazo_em = caso.prazo if aberto else None
    if alvo.aberta_em is None:
        alvo.aberta_em = caso.criada_em or caso.mudou_em or agora
    if aberto:
        alvo.encerrada_em = None
    elif alvo.encerrada_em is None:
        alvo.encerrada_em = caso.mudou_em or agora

    # O motivo do COMPRADOR (lido na plataforma); o tipo de solução vai para
    # `dados.solucao`. Sem leitura nova (ou a releitura que volta sem motivo),
    # fica o que já se sabia — menos o tipo de solução que até 02/10/2026 ia
    # neste campo.
    lido: dict[str, Any] = {}
    if leitura is not None and leitura.motivo_lido:
        lido = {
            "motivo_codigo": leitura.motivo_codigo or anteriores.get("motivo_codigo"),
            "motivo_lido_em": _iso(agora),
            "motivo_lido_status": caso.status,
            "motivo_falhou": None,
        }
    if leitura is not None and leitura.motivo:
        alvo.motivo = leitura.motivo
    elif _motivo_legado(alvo):
        alvo.motivo = None
    if leitura is not None and leitura.motivo_falhou:
        lido = {"motivo_falhou": {"em": _iso(agora), "status": caso.status}}
    if leitura is not None and leitura.comprador_tentado:
        lido["comprador_tentado_em"] = _iso(agora)
        lido["comprador_tentativas"] = _inteiro(anteriores.get("comprador_tentativas")) + 1
    tipo_caso = (
        caso.tipo_caso
        or (leitura.tipo_caso if leitura is not None else None)
        or anteriores.get("tipo_caso")
    )
    acao_texto = (
        logistica_rules.acao_plataforma_pt(caso.plataforma, caso.acao)
        if aberto and caso.acao
        else None
    )
    alvo.dados = {
        **anteriores,
        "fonte": FONTE_LOGISTICA,
        "logistica_id": str(caso.logistica_id) if caso.logistica_id else None,
        "pedido_bling": caso.pedido_bling,
        "conta": caso.conta,
        "return_id": caso.return_id,
        "tipo_caso": tipo_caso,
        "solucao": solucao_do_caso(tipo_caso),
        "acao_pendente": caso.acao if aberto else None,
        "acao_texto": acao_texto,
        "status_texto": status_tela(caso.plataforma, caso.status, tipo_caso),
        "mudou_em": _iso(caso.mudou_em),
        **lido,
    }
    await session.flush()

    conversas = await etiqueta_fatos.conversas_do_pedido(
        session, caso.plataforma, caso.pedido_marketplace
    )
    da_devolucao, conversa_criada = await _conversa_da_devolucao(
        session, caso, alvo, aberto, integracao, leitura, conversas, agora
    )
    # A conversa principal do pedido: a do chat com o comprador; sem chat, a
    # da devolução (o cartão aparece nas duas, pelo pedido).
    com_o_comprador = [c for c in conversas if c.canal not in CANAIS_SEM_O_COMPRADOR]
    principal = (
        com_o_comprador[0] if com_o_comprador else da_devolucao or next(iter(conversas), None)
    )
    if principal is not None:
        alvo.conversa_id = principal.id
    await session.flush()
    # A Logística guarda UM caso por pedido: o que era o caso de antes e
    # ainda estava aberto aqui acabou (foi substituído pelo atual).
    for outra in existentes:
        if outra is alvo or outra.encerrada_em is not None:
            continue
        outra.encerrada_em = agora
        outra.prazo_em = None
        outra.dados = {**(outra.dados or {}), "substituida_por": externo_id}
    await session.flush()
    return alvo, criada, criada or antes != _retrato(alvo), conversa_criada


async def ligar_devolucoes(
    session: AsyncSession,
    *,
    agora: datetime | None = None,
    pedidos: Collection[str] | None = None,
    commit_a_cada: int | None = None,
    fabrica_cliente: FabricaCliente | None = None,
) -> dict:
    """Liga as devoluções/disputas de Shopee e TikTok da Logística ao atendimento.

    Uma linha por caso em `atendimento_reclamacoes`, ligada à conversa do
    pedido (a aberta sem chat ganha a conversa da devolução), e a etiqueta
    recalculada nas conversas do pedido quando o caso mudou. `pedidos` limita
    aos nºs na plataforma dados (o gancho de quem acabou de ler um caso).
    `fabrica_cliente` liga a leitura do motivo e do comprador na plataforma
    (só GET, com cota — ver o topo); sem ela nada sai para a plataforma. Não
    commita, a não ser com `commit_a_cada` (commit a cada N casos gravados).
    Devolve as contagens.
    """
    agora = agora or _agora()
    casos = await casos_da_logistica(session, pedidos)
    existentes = await _existentes(session, casos)
    integracoes = _Integracoes()
    resumo = {
        "casos": len(casos),
        "novas": 0,
        "atualizadas": 0,
        "historia": 0,
        "ligadas": 0,
        "etiquetas": 0,
        "conversas_novas": 0,
        "leituras": 0,
        "motivos_lidos": 0,
        "leituras_adiadas": 0,
        "erros_leitura": 0,
        "erros": 0,
    }
    # As chamadas à plataforma ANTES de gravar: nenhuma conversa fica travada
    # esperando a Shopee/TikTok responder.
    leituras = await ler_na_plataforma(
        session, casos, existentes, integracoes, agora, fabrica_cliente, resumo
    )
    gravados = 0
    for caso in casos:
        chave = (caso.plataforma, caso.pedido_marketplace)
        try:
            async with session.begin_nested():
                integracao = await integracoes.objeto(session, caso.plataforma, caso.conta)
                linha, criada, mudou, conversa_criada = await ligar_caso(
                    session,
                    caso,
                    existentes.get(chave, []),
                    agora,
                    integracao,
                    leituras.get(chave),
                )
                if linha is None:
                    resumo["historia"] += 1
                    continue
                if criada:
                    resumo["novas"] += 1
                elif mudou:
                    resumo["atualizadas"] += 1
                if linha.conversa_id is not None:
                    resumo["ligadas"] += 1
                if conversa_criada:
                    resumo["conversas_novas"] += 1
                if mudou:
                    alvo = await reclamacoes.conversas_para_etiqueta(
                        session, caso.plataforma, [caso.pedido_marketplace]
                    )
                    resumo["etiquetas"] += await reclamacoes.recalcular_etiquetas(
                        session, alvo, motivo=MOTIVO_ETIQUETA, agora=agora
                    )
                    gravados += 1
        except Exception as exc:  # noqa: BLE001 — um caso não derruba os outros
            resumo["erros"] += 1
            logger.warning(
                "atendimento_devolucao_ligar_falhou",
                plataforma=caso.plataforma,
                pedido=caso.pedido_marketplace,
                err=type(exc).__name__,
            )
            continue
        if commit_a_cada and gravados and gravados % commit_a_cada == 0:
            await session.commit()
    logger.info("atendimento_devolucoes_ligadas", **resumo)
    return resumo
