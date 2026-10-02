"""Reclamações, mediações e devoluções do Mercado Livre no atendimento (RF2, 01/10/2026).

A lacuna do pedido 297840 (ML 2000018509205724, conta aguiar): o ML tinha a
reclamação 5582543195 com devolução em preparação e o DaVinci dizia
"Chamados: nenhum · Devolução: nenhuma" — o atendimento só lia perguntas e
packs, pack sem mensagem não virava conversa, e ninguém listava as
reclamações da conta. Aqui, por conta do ML, a cada rodada do cron
(`atendimento_reclamacoes`, a cada 10 min, só com `ATENDIMENTO_RECLAMACOES_ATIVA`
e `ATENDIMENTO_LEITURA_ATIVA`):

  1. a BUSCA da conta (`claims/search`, pelo dono do token): as ABERTAS e as
     ENCERRADAS nos últimos `JANELA_ENCERRADAS` (ordenadas pela última mexida;
     a página que já passou da janela encerra a busca). A busca já traz o
     tipo, a etapa, o pedido, as ações liberadas para a loja (com prazo) e a
     resolução — não precisa do detalhe;
  2. a que estava ABERTA aqui e sumiu das duas listas é relida pelo id (o
     encerramento que a janela não pegou);
  3. cada reclamação vira UMA linha em `atendimento_reclamacoes` (UNIQUE
     plataforma + id) e UMA conversa `canal = 'reclamacao'` (`externo_id` =
     id da reclamação) com as mensagens da reclamação: comprador → `cliente`,
     loja → `loja`, ML → `mediador` (o "Com Meli"), mais duas linhas do
     sistema ("aberta", "encerrada") que contam a história na linha do
     tempo. A conversa nasce mesmo sem mensagem nenhuma (era o 297840: o
     pack não tinha mensagem, então não havia conversa nenhuma);
  4. o PRAZO é o da plataforma: a ação da loja com `due_date` (as
     obrigatórias primeiro) → `prazo_em` e, na conversa, a vez da loja
     (`CHAVE_VEZ_DA_LOJA`) e o prazo oficial (`CHAVE_PRAZO_PLATAFORMA`): é
     isso que põe a reclamação em "Falta responder", vencendo primeiro;
  5. a etiqueta é recalculada na conversa da reclamação E nas conversas do
     mesmo pedido (o pack do pós-venda), pelo `etiqueta.recalcular_etiqueta`.

Por que cada cuidado:

- SÓ LEITURA. Nenhuma ação na reclamação sai daqui (os métodos de ação do
  cliente do ML existem, para os chamados; este módulo não os chama). A
  conversa da reclamação nasce BLOQUEADA, com o motivo "só leitura": a caixa
  de resposta não aparece, a IA não sugere, e o envio recusa. Nada é marcado
  como lido (o GET das mensagens da reclamação não tem `mark_as_read`; medido
  em 01/10/2026: as mensagens já chegam lidas pelo Duoke segundos depois).
- POUCAS CHAMADAS, POR CONTA. Duas buscas por rodada; as mensagens (e a
  devolução, a reputação, o motivo e o pack) só quando a reclamação MEXEU
  (`last_updated` diferente do que já foi lido) e com teto por conta e rodada
  (`MAX_LEITURAS_RODADA` — o resto entra na rodada seguinte, as abertas
  primeiro). Contas em paralelo limitadas (`CONCORRENCIA_CONTAS`), uma rodada
  por vez (trava no Redis).
- ERRO CONTIDO: uma conta que falha (token, 403, 5xx) não para as outras, e
  uma reclamação que falha não para a conta (cada uma na sua transação). A
  leitura que falhou só é tentada de novo depois de `RETENTAR_FALHA`.
- SEM DADO PESSOAL NO LOG: só ids, contagens e código de erro. O endereço que
  a devolução do ML devolve nunca é guardado.

O cron só roda com o interruptor próprio `ATENDIMENTO_RECLAMACOES_ATIVA` E a
leitura (`ATENDIMENTO_LEITURA_ATIVA`): o deploy sozinho não liga nada.

Os tipos de cancelamento do ML (`cancel_purchase`, `cancel_sale`) NÃO entram:
não são reclamação nem devolução (são o pedido de cancelamento, assunto do
Ag. cancelamento — item 4).

As devoluções e disputas da Shopee e da TikTok, que a Logística já lê, entram
por `reclamacoes_devolucoes.ligar_devolucoes` (o caso e o status pela
Logística; o motivo do comprador e, para a aberta sem chat, o comprador da
conversa da devolução por GET, com cota própria); o cron daqui roda as duas.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.db as _db
from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoReclamacao,
    Integration,
    IntegrationPlatform,
)
from app.redis_client import redis
from app.services import logistica_rules
from app.services.atendimento import etiqueta, etiqueta_fatos, gravar, lojas
from app.services.atendimento.constantes import (
    AUTOR_CLIENTE,
    AUTOR_LOJA,
    AUTOR_MEDIADOR,
    AUTOR_SISTEMA,
    CANAL_POS_VENDA,
    CANAL_RECLAMACAO,
    CHAVE_PRAZO_LIDO_EM,
    CHAVE_PRAZO_PLATAFORMA,
    CHAVE_VEZ_DA_LOJA,
    CONVERSA_BLOQUEADA,
    CONVERSA_FECHADA,
    RECLAMACAO_TIPO_DEVOLUCAO,
    RECLAMACAO_TIPO_MEDIACAO,
    RECLAMACAO_TIPO_RECLAMACAO,
    TIPOS_QUE_SAO_RECLAMACAO,
)

logger = structlog.get_logger()

PLATAFORMA = "ml"
# `atendimento_reclamacoes.dados["fonte"]`: quem escreveu a linha.
FONTE_ML = "ml_claims"

# ── A busca ───────────────────────────────────────────────────────────────
# As encerradas que ainda entram: a reclamação que acabou ontem precisa
# aparecer encerrada (o cartão, a etiqueta voltando a Pós-venda). Mais velha
# que isto é história — e a busca de encerradas da conta tem centenas (953
# na aguiar em 01/10/2026).
JANELA_ENCERRADAS = timedelta(days=15)
PAGINA_BUSCA = 50  # o ML aceita até 100
MAX_PAGINAS_ABERTAS = 4
MAX_PAGINAS_ENCERRADAS = 3

# ── A cota ────────────────────────────────────────────────────────────────
# Reclamações LIDAS (mensagens + devolução + reputação + motivo) por conta e
# rodada. A primeira rodada de uma conta tem as encerradas dos 15 dias para
# ler; com o teto, ela se espalha pelas rodadas seguintes.
MAX_LEITURAS_RODADA = 20
# Abertas daqui que sumiram das duas listas: relidas pelo id, no máximo isto.
MAX_CONFERIR_SUMIDAS = 10
CONCORRENCIA_CONTAS = 3
# A leitura que falhou (404 numa reclamação antiga, 5xx) não é repetida a
# cada rodada: só depois disto, ou quando a reclamação mexer de novo.
RETENTAR_FALHA = timedelta(hours=1)

# ── A rodada ──────────────────────────────────────────────────────────────
# Menor que o intervalo do cron (10 min): se o processo morrer, a próxima roda.
TRAVA_TTL_S = 9 * 60
CHAVE_TRAVA = "atendimento:reclamacoes:rodada"
# As devoluções de Shopee/TikTok (~350 casos) commitam a cada tantos casos
# que mudaram: cada um faz UPDATE de etiqueta nas conversas do pedido, e uma
# transação só seguraria essas linhas até o último caso (o sync esperando,
# a troca à mão da tela devolvendo 409).
COMMIT_DEVOLUCOES_A_CADA = 20

# O acontecimento que vai entre parênteses na linha do tempo da etiqueta.
MOTIVO_ETIQUETA_ML = "leitura das reclamações do ML"

# O que a caixa mostra no lugar da resposta (a conversa nasce bloqueada).
BLOQUEIO_SO_LEITURA = (
    "Reclamação do Mercado Livre: só leitura no DaVinci — as respostas e as "
    "ações ficam no Mercado Livre."
)

# ── O vocabulário do ML (medido em 01/10/2026) ────────────────────────────
STATUS_ML_ABERTA = "opened"
STATUS_ML_ENCERRADA = "closed"
# `stage`: claim (com o comprador) | dispute (o ML mediando) | recontact | none
STAGE_MEDIACAO = "dispute"
# `type`: mediations | returns | change | cancel_purchase | cancel_sale |
# fulfillment | ml_case | service. Devolução/troca = DEVOLUÇÃO; os
# cancelamentos ficam de fora (ver o topo).
TIPOS_ML_DEVOLUCAO = frozenset({"returns", "change"})
TIPOS_ML_IGNORADOS = frozenset({"cancel_purchase", "cancel_sale"})
# Papel de quem escreveu → autor da mensagem no atendimento.
AUTOR_DO_PAPEL = {
    "complainant": AUTOR_CLIENTE,
    "respondent": AUTOR_LOJA,
    "mediator": AUTOR_MEDIADOR,
}
# `resolution.benefited` → como a decisão é contada.
_BENEFICIADO = {
    "complainant": "a favor do comprador",
    "respondent": "a favor da loja",
}
MODERACAO_LIMPA = "clean"

NOME_TIPO = {
    RECLAMACAO_TIPO_RECLAMACAO: "Reclamação",
    RECLAMACAO_TIPO_MEDIACAO: "Mediação",
    RECLAMACAO_TIPO_DEVOLUCAO: "Devolução",
}
NOME_PLATAFORMA = {"ml": "Mercado Livre", "shopee": "Shopee", "tiktok": "TikTok"}
# `externo_id` da devolução da Shopee/TikTok cujo id na plataforma a
# Logística ainda não tem ("pedido 250930ABC"): troca pelo id quando ele
# aparecer (`reclamacoes_devolucoes`). Lido assim na linha do tempo:
# "Devolução pedido 250930ABC aberta no Shopee".
PREFIXO_SEM_ID = "pedido "

# Motivo (`reason_id` → nome do ML) por processo: são poucas dezenas.
_MOTIVOS: dict[str, str] = {}
# O `name` do ML é um código ("not_working_item") e o `detail` às vezes não
# serve ("Chegou bem" para produto que não funciona) — medido em 02/10/2026.
# Mesma tabela de apps/web/components/AtendimentoReclamacao.vue (MOTIVOS_ML).
MOTIVOS_ML: dict[str, str] = {
    "repentant_buyer": "Desistiu da compra (chegou bem, não quer mais)",
    "bought_by_mistake": "Comprou por engano",
    "different_than_published": "Diferente do anúncio",
    "different_color_or_size": "Cor, tamanho ou modelo diferente",
    "different_item_other": "Recebeu outro produto",
    "not_working_item": "Produto não funciona",
    "broken_item": "Produto quebrado ou com defeito",
    "damaged_package_broken_item": "Embalagem danificada e produto quebrado",
    "missing_accessories": "Faltam acessórios",
    "missing_item": "Falta produto no pacote",
    "delivered_but_not_receive_package": "Consta entregue, mas não recebeu",
    "not_received": "Não recebeu o produto",
}

FabricaCliente = Callable[[Integration], Awaitable[Any]]


def _agora() -> datetime:
    """Relógio do módulo (os testes trocam)."""
    return datetime.now(UTC)


def _erro(exc: BaseException) -> str:
    """Texto de operação para o log: classe, HTTP e código — nunca o corpo."""
    from app.services.atendimento.sync import erro_de_operacao

    return erro_de_operacao(exc)[0]


# ── Conversão (pura) ──────────────────────────────────────────────────────


def _id(bruto: Any) -> str:
    return "" if bruto is None else str(bruto).strip()


def _lista(bruto: Any) -> list[dict]:
    return [x for x in bruto if isinstance(x, dict)] if isinstance(bruto, list) else []


def _dict(bruto: Any) -> dict:
    return bruto if isinstance(bruto, dict) else {}


def data_ml(bruto: Any) -> datetime | None:
    """Data do ML (ISO com fuso -04:00 e até 6+ casas) → UTC; ilegível → None."""
    if not isinstance(bruto, str) or not bruto.strip():
        return None
    texto = bruto.strip().replace("Z", "+00:00")
    # Fração com mais de 6 casas (o `fromisoformat` só aceita até 6).
    if "." in texto:
        cabeca, _, resto = texto.partition(".")
        digitos = ""
        while resto and resto[0].isdigit():
            digitos, resto = digitos + resto[0], resto[1:]
        texto = f"{cabeca}.{digitos[:6]}{resto}" if digitos else f"{cabeca}{resto}"
    try:
        quando = datetime.fromisoformat(texto)
    except ValueError:
        return None
    return quando.replace(tzinfo=UTC) if quando.tzinfo is None else quando.astimezone(UTC)


def _iso(quando: datetime | None) -> str | None:
    return quando.astimezone(UTC).isoformat(timespec="seconds") if quando else None


def tipo_da_reclamacao(claim: dict) -> str | None:
    """O tipo do atendimento (`reclamacao` | `mediacao` | `devolucao`); None = fora.

    O ML mediando (`stage = dispute`) é MEDIAÇÃO, seja qual for o tipo; a
    devolução e a troca com o comprador, DEVOLUÇÃO; o resto, RECLAMAÇÃO. Os
    cancelamentos (`cancel_purchase`/`cancel_sale`) não entram.
    """
    tipo = _id(claim.get("type")).lower()
    if tipo in TIPOS_ML_IGNORADOS:
        return None
    if _id(claim.get("stage")).lower() == STAGE_MEDIACAO:
        return RECLAMACAO_TIPO_MEDIACAO
    if tipo in TIPOS_ML_DEVOLUCAO:
        return RECLAMACAO_TIPO_DEVOLUCAO
    return RECLAMACAO_TIPO_RECLAMACAO


def esta_aberta(claim: dict) -> bool:
    return _id(claim.get("status")).lower() != STATUS_ML_ENCERRADA


def acoes_da_loja(claim: dict) -> list[dict]:
    """As ações que o ML libera para a LOJA (player `respondent`/`seller`)."""
    acoes: list[dict] = []
    for p in _lista(claim.get("players")):
        if _id(p.get("role")).lower() == "respondent" or _id(p.get("type")).lower() == "seller":
            acoes.extend(_lista(p.get("available_actions")))
    return acoes


def acao_pendente(claim: dict) -> tuple[str | None, datetime | None]:
    """O que o ML espera da loja e até quando: (ação, prazo) ou (None, None).

    A de prazo mais curto entre as OBRIGATÓRIAS com `due_date`; sem
    obrigatória, entre as que têm `due_date` (a mesma leitura da Logística,
    `logistica_meli._acao_pendente_ml`). Reclamação encerrada não tem prazo.
    """
    if not esta_aberta(claim):
        return None, None
    com_prazo = [
        (a, quando)
        for a in acoes_da_loja(claim)
        if (quando := data_ml(a.get("due_date"))) is not None and _id(a.get("action"))
    ]
    obrigatorias = [(a, q) for a, q in com_prazo if a.get("mandatory") is True]
    candidatas = obrigatorias or com_prazo
    if not candidatas:
        return None, None
    acao, quando = min(candidatas, key=lambda par: par[1])
    return _id(acao.get("action")).lower(), quando


def encerrada_em(claim: dict) -> datetime | None:
    """Quando acabou (a resolução; sem ela, a última mexida). Aberta → None."""
    if esta_aberta(claim):
        return None
    resolucao = _dict(claim.get("resolution"))
    return (
        data_ml(resolucao.get("date_created"))
        or data_ml(claim.get("last_updated"))
        or data_ml(claim.get("date_created"))
    )


def pedido_da_reclamacao(claim: dict) -> str | None:
    """O nº do pedido no ML (`resource = order` → `resource_id`)."""
    if _id(claim.get("resource")).lower() not in ("", "order"):
        return None
    return _id(claim.get("resource_id")) or None


def comprador_da_reclamacao(claim: dict) -> str | None:
    for p in _lista(claim.get("players")):
        if _id(p.get("role")).lower() == "complainant":
            return _id(p.get("user_id")) or None
    return None


def decisao(claim: dict) -> str:
    """A decisão: "a favor do comprador" / "a favor da loja" / "sem beneficiado informado"."""
    beneficiados = _dict(claim.get("resolution")).get("benefited")
    if isinstance(beneficiados, str):
        beneficiados = [beneficiados]
    lados = {_id(b).lower() for b in beneficiados or []}
    for lado, texto in _BENEFICIADO.items():
        if lado in lados and len(lados) == 1:
            return texto
    if lados:
        return "a favor das duas partes"
    return "sem beneficiado informado"


def _resolucao(claim: dict) -> dict:
    r = _dict(claim.get("resolution"))
    if not r:
        return {}
    beneficiados = r.get("benefited")
    return {
        "motivo": _id(r.get("reason")) or None,
        "beneficiado": [_id(b) for b in beneficiados] if isinstance(beneficiados, list) else [],
        "encerrada_por": _id(r.get("closed_by")) or None,
        "cobertura": (
            r.get("applied_coverage") if isinstance(r.get("applied_coverage"), bool) else None
        ),
        "em": _iso(data_ml(r.get("date_created"))),
    }


def _devolucao(corpo: dict | None) -> dict | None:
    """O que se guarda da devolução do ML: status e dinheiro — NUNCA o endereço."""
    if not isinstance(corpo, dict) or not corpo:
        return None
    envios = [
        {"status": _id(s.get("status")) or None, "tipo": _id(s.get("type")) or None}
        for s in _lista(corpo.get("shipments"))
    ]
    itens = [_id(o.get("item_id")) for o in _lista(corpo.get("orders")) if _id(o.get("item_id"))]
    return {
        "id": _id(corpo.get("id")) or None,
        "status": _id(corpo.get("status")) or None,
        "status_dinheiro": _id(corpo.get("status_money")) or None,
        "reembolso_quando": _id(corpo.get("refund_at")) or None,
        "subtipo": _id(corpo.get("subtype")) or None,
        "criada_em": _iso(data_ml(corpo.get("date_created"))),
        "encerrada_em": _iso(data_ml(corpo.get("date_closed"))),
        "envios": envios,
        "itens": itens,
    }


@dataclass(frozen=True)
class MensagemReclamacao:
    externo_id: str
    autor: str
    texto: str | None
    enviada_em: datetime | None
    tipo: str
    anexos: list[dict]
    payload: dict


def _anexos(m: dict) -> list[dict]:
    """Nome e tipo do anexo, nunca o arquivo (a URL do ML expira)."""
    saida = []
    for a in _lista(m.get("attachments")):
        item = {
            "arquivo": a.get("filename"),
            "nome": a.get("original_filename"),
            "tipo": a.get("type") or a.get("file_type") or a.get("content_type"),
            "tamanho": a.get("size"),
        }
        saida.append({k: v for k, v in item.items() if v is not None})
    return saida


def mensagem_da_reclamacao(m: dict) -> MensagemReclamacao | None:
    """Uma mensagem da reclamação → o que o `gravar` recebe. Sem data nem texto/anexo → None.

    O ML não manda id de mensagem: a chave é o `hash` dele; sem hash, um
    resumo de (data, papel, texto) — estável entre as rodadas.
    """
    papel = _id(m.get("sender_role")).lower()
    texto = m.get("message") if isinstance(m.get("message"), str) else None
    texto = texto if texto and texto.strip() else None
    anexos = _anexos(m)
    quando = data_ml(m.get("date_created")) or data_ml(m.get("message_date"))
    if texto is None and not anexos:
        return None
    chave = _id(m.get("hash"))
    if not chave:
        base = f"{m.get('date_created')}|{papel}|{texto or ''}|{len(anexos)}"
        chave = "c:" + hashlib.sha1(base.encode("utf-8"), usedforsecurity=False).hexdigest()
    if texto:
        tipo = "texto"
    else:
        imagens = all(str(a.get("tipo") or "").startswith("image") for a in anexos)
        tipo = "imagem" if imagens else "arquivo"
    payload: dict[str, Any] = {
        "papel": papel or None,
        "para": _id(m.get("receiver_role")).lower() or None,
        "etapa": _id(m.get("stage")) or None,
        "status": _id(m.get("status")) or None,
    }
    moderacao = _dict(m.get("message_moderation"))
    status_mod = _id(moderacao.get("status")).lower()
    if status_mod and status_mod != MODERACAO_LIMPA:
        payload["moderacao"] = {
            "status": status_mod,
            "motivo": _id(moderacao.get("reason")) or None,
        }
    return MensagemReclamacao(
        externo_id=chave[:191],
        autor=AUTOR_DO_PAPEL.get(papel, AUTOR_SISTEMA),
        texto=texto,
        enviada_em=quando,
        tipo=tipo,
        anexos=anexos,
        payload={k: v for k, v in payload.items() if v is not None},
    )


def mensagens_do_sistema(
    claim: dict, tipo: str, motivo: str | None, plataforma: str = PLATAFORMA
) -> list[MensagemReclamacao]:
    """As linhas do sistema que contam a história: "aberta" e "encerrada".

    Texto de operação (id, motivo e decisão da PLATAFORMA), nunca do
    comprador. Idempotentes pela chave: a abertura uma vez; o encerramento
    uma vez por data de encerramento (a reclamação reaberta e fechada de novo
    ganha outra linha).
    """
    cid = _id(claim.get("id"))
    nome = NOME_TIPO.get(tipo, "Reclamação")
    onde = NOME_PLATAFORMA.get(plataforma, plataforma)
    saida = []
    aberta = data_ml(claim.get("date_created"))
    texto = f"{nome} {cid} aberta no {onde}"
    if motivo:
        texto += f" — motivo: {motivo}"
    saida.append(
        MensagemReclamacao("sistema:abertura", AUTOR_SISTEMA, texto + ".", aberta, "texto", [], {})
    )
    fim = encerrada_em(claim)
    if fim is not None:
        codigo = _id(_dict(claim.get("resolution")).get("reason"))
        texto = f"{nome} encerrada no {onde} — decisão {decisao(claim)}"
        if codigo:
            texto += f" ({codigo})"
        saida.append(
            MensagemReclamacao(
                f"sistema:encerramento:{_iso(fim)}",
                AUTOR_SISTEMA,
                texto + ".",
                fim,
                "texto",
                [],
                {},
            )
        )
    return saida


# ── Leitura de UMA reclamação (só HTTP) ───────────────────────────────────


@dataclass
class _Cota:
    """Reclamações que ainda podem ser LIDAS nesta rodada, nesta conta."""

    restantes: int

    def pode(self) -> bool:
        return self.restantes > 0

    def gastar(self) -> None:
        self.restantes -= 1


@dataclass
class _Leitura:
    mensagens: list[dict]
    devolucao: dict | None = None
    reputacao: str | None = None
    pack_id: str | None = None


async def _motivo(session: AsyncSession, cliente: Any, reason_id: str | None) -> str | None:
    """Nome do motivo no ML ("Produto com defeito"); cache por processo e no banco.

    Falhou → None (a tela mostra o código); não guarda a falha.
    """
    if not reason_id:
        return None
    if reason_id in _MOTIVOS:
        return _MOTIVOS[reason_id]
    salvo = await session.scalar(
        select(AtendimentoReclamacao.motivo)
        .where(
            AtendimentoReclamacao.plataforma == PLATAFORMA,
            AtendimentoReclamacao.dados["reason_id"].astext == reason_id,
            AtendimentoReclamacao.motivo.is_not(None),
        )
        .limit(1)
    )
    if salvo and not salvo.replace("_", "").isalnum():
        # Já traduzido antes. O código cru ("not_working_item", gravado antes de
        # 02/10) não serve de cache: relê e traduz.
        _MOTIVOS[reason_id] = salvo
        return salvo
    try:
        corpo = await cliente.motivo_de_reclamacao(reason_id)
    except Exception as exc:  # noqa: BLE001 — o motivo é enfeite: o código fica
        logger.info("atendimento_reclamacao_motivo_falhou", reason_id=reason_id, erro=_erro(exc))
        return None
    codigo = _id(_dict(corpo).get("name"))
    nome = MOTIVOS_ML.get(codigo) or codigo or _id(_dict(corpo).get("detail"))
    if nome:
        _MOTIVOS[reason_id] = nome[:300]
        return _MOTIVOS[reason_id]
    return None


async def _pack_do_pedido(session: AsyncSession, cliente: Any, order_id: str | None) -> str | None:
    """O pack do pedido: é por ele que a reclamação chega à conversa do comprador.

    A conversa do pós-venda do ML guarda o PACK em `pedido_marketplace` (em
    produção o pack nunca traz o order à vista), e no carrinho o pack é
    outro número — sem o pack aqui, a reclamação (gravada pelo ORDER) não
    casa com a conversa onde o comprador fala (`etiqueta_fatos.
    condicao_do_pedido`). Também liga o Bling quando ele gravou o PACK em
    `numeroloja`. Primeiro o que o DaVinci já sabe (a conversa do pack desse
    pedido, inclusive pelo order do retrato); só então o GET do pedido (uma
    vez por reclamação: `pack_conferido`). Mesmo com o pedido achado no
    Bling pelo order, o pack é buscado: a conversa do comprador é do pack.
    """
    if not order_id:
        return None
    for c in await etiqueta_fatos.conversas_do_pedido(session, PLATAFORMA, order_id):
        pack = _id((c.dados or {}).get("pack_id"))
        if pack:
            return pack
    try:
        pedido = await cliente.pedido(order_id)
    except Exception as exc:  # noqa: BLE001 — sem pack, a reclamação entra igual
        logger.info("atendimento_reclamacao_pack_falhou", order_id=order_id, erro=_erro(exc))
        return None
    pack = _id(_dict(pedido).get("pack_id"))
    return pack if pack and pack != order_id else None


async def _ler(session: AsyncSession, cliente: Any, claim: dict, *, buscar_pack: bool) -> _Leitura:
    """As chamadas de UMA reclamação. As mensagens levantam (sem elas não há
    leitura); devolução, reputação e pack são enfeite: falhou, fica sem."""
    cid = _id(claim.get("id"))
    mensagens = await cliente.mensagens_da_reclamacao(cid)
    leitura = _Leitura(mensagens=mensagens)
    try:
        leitura.devolucao = _devolucao(await cliente.devolucao_da_reclamacao(cid))
    except Exception as exc:  # noqa: BLE001
        logger.info("atendimento_reclamacao_devolucao_falhou", claim_id=cid, erro=_erro(exc))
    try:
        rep = await cliente.reclamacao_afeta_reputacao(cid)
        leitura.reputacao = _id(_dict(rep).get("affects_reputation")) or None
    except Exception as exc:  # noqa: BLE001
        logger.info("atendimento_reclamacao_reputacao_falhou", claim_id=cid, erro=_erro(exc))
    if buscar_pack:
        leitura.pack_id = await _pack_do_pedido(session, cliente, pedido_da_reclamacao(claim))
    return leitura


# ── Gravação ──────────────────────────────────────────────────────────────


@dataclass
class ResumoConta:
    listadas: int = 0
    novas: int = 0
    atualizadas: int = 0
    lidas: int = 0
    adiadas: int = 0
    mensagens: int = 0
    conversas_novas: int = 0
    etiquetas: int = 0
    erros: int = 0
    erro: str | None = None
    ids: list[str] = field(default_factory=list)

    def como_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "ids"}
        return d


def _campos_da_linha(claim: dict, tipo: str) -> dict[str, Any]:
    """As colunas de `atendimento_reclamacoes` que vêm da busca (sem chamada a mais)."""
    acao, prazo = acao_pendente(claim)
    return {
        "tipo": tipo,
        "status": _id(claim.get("status")) or None,
        "pedido_marketplace": pedido_da_reclamacao(claim),
        "prazo_em": prazo,
        "aberta_em": data_ml(claim.get("date_created")),
        "encerrada_em": encerrada_em(claim),
        "_acao": acao,
    }


def _dados_da_linha(claim: dict, acao: str | None, anteriores: dict) -> dict:
    acoes = [
        {
            "acao": _id(a.get("action")) or None,
            "obrigatoria": a.get("mandatory") if isinstance(a.get("mandatory"), bool) else None,
            "prazo": _iso(data_ml(a.get("due_date"))),
        }
        for a in acoes_da_loja(claim)
    ]
    dados = dict(anteriores)
    dados.update(
        {
            "fonte": FONTE_ML,
            "tipo_ml": _id(claim.get("type")) or None,
            "etapa": _id(claim.get("stage")) or None,
            "reason_id": _id(claim.get("reason_id")) or None,
            "last_updated": _id(claim.get("last_updated")) or None,
            "acoes_loja": acoes,
            "acao_pendente": acao,
            "acao_texto": (
                logistica_rules.acao_plataforma_pt("ml", f"ML_{acao.upper()}") if acao else None
            ),
            "resolucao": _resolucao(claim),
        }
    )
    return dados


async def _linha(session: AsyncSession, cid: str) -> AtendimentoReclamacao | None:
    return (
        await session.execute(
            select(AtendimentoReclamacao).where(
                AtendimentoReclamacao.plataforma == PLATAFORMA,
                AtendimentoReclamacao.externo_id == cid,
            )
        )
    ).scalar_one_or_none()


def _falhou_ha_pouco(
    linha: AtendimentoReclamacao | None, last_updated: str, agora: datetime
) -> bool:
    falha = _dict((linha.dados if linha is not None else None) or {}).get("leitura_falhou")
    if not isinstance(falha, dict) or falha.get("last_updated") != last_updated:
        return False
    quando = data_ml(falha.get("em"))
    return quando is not None and agora - quando < RETENTAR_FALHA


def _dados_da_conversa(
    claim: dict, linha: AtendimentoReclamacao, agora: datetime
) -> dict[str, Any]:
    """O que a conversa da reclamação guarda em `dados` (a fila e o prazo são do ML)."""
    cid = _id(claim.get("id"))
    aberta = linha.encerrada_em is None
    acao = (linha.dados or {}).get("acao_pendente")
    pack_id = _id((linha.dados or {}).get("pack_id")) or None
    dados: dict[str, Any] = {
        "claim_id": cid,
        "order_id": linha.pedido_marketplace,
        "tipo_ml": _id(claim.get("type")) or None,
        "etapa_ml": _id(claim.get("stage")) or None,
        "status_ml": _id(claim.get("status")) or None,
        # O mesmo sinal que o adaptador do pack grava (`constantes.reclamacao_aberta`)
        # — só para reclamação/mediação: a etiqueta lê `claim_ids` como
        # RECLAMAÇÃO, e a devolução aberta é DEVOLUÇÃO (pela tabela).
        "claim_ids": [cid] if aberta and linha.tipo in TIPOS_QUE_SAO_RECLAMACAO else [],
        # A VEZ e o PRAZO são do ML (a ação da loja com `due_date`), não de
        # quem falou por último: sem ação pendente, não é a vez da loja.
        CHAVE_VEZ_DA_LOJA: _iso(agora) if aberta and acao and linha.prazo_em else None,
        CHAVE_PRAZO_PLATAFORMA: _iso(linha.prazo_em) if aberta else None,
        CHAVE_PRAZO_LIDO_EM: _iso(agora),
        "relida_em": _iso(agora),
    }
    if pack_id:
        dados["pack_id"] = pack_id
    return dados


async def _atualizar_conversa(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    claim: dict,
    linha: AtendimentoReclamacao,
    agora: datetime,
) -> None:
    """A reclamação mexeu mas não foi relida (cota, falha): a fila e o prazo andam igual.

    Só `dados` e a fila, sem chamada ao ML: a reclamação que encerrou não
    pode continuar "esperando a loja" até a leitura seguinte.
    """
    if not await gravar.travar_linha(session, conversa):
        return
    conversa.dados = {**(conversa.dados or {}), **_dados_da_conversa(claim, linha, agora)}
    gravar.recalcular(conversa)
    await session.flush()


async def _gravar_conversa(
    session: AsyncSession,
    integration: Integration,
    claim: dict,
    linha: AtendimentoReclamacao,
    leitura: _Leitura,
    agora: datetime,
) -> tuple[AtendimentoConversa, bool, int]:
    """A conversa `reclamacao` com as mensagens → (conversa, criada, mensagens novas)."""
    cid = _id(claim.get("id"))
    dados = _dados_da_conversa(claim, linha, agora)
    itens = (leitura.devolucao or {}).get("itens") or []
    conversa, criada = await gravar.upsert_conversa(
        session,
        canal=None,
        integration=integration,
        plataforma=PLATAFORMA,
        canal_nome=CANAL_RECLAMACAO,
        externo_id=cid,
        conta=await lojas.nome_da_loja(session, integration) or None,
        comprador_id=comprador_da_reclamacao(claim),
        pedido_marketplace=linha.pedido_marketplace,
        anuncio_id=itens[0] if itens else None,
        dados=dados,
    )
    # Só leitura: a conversa da reclamação fica bloqueada (sem caixa de
    # resposta, sem IA, o envio recusa). Fechada = a pessoa deu por
    # resolvida: não volta.
    if conversa.situacao != CONVERSA_FECHADA and (
        conversa.situacao != CONVERSA_BLOQUEADA or conversa.bloqueio_motivo != BLOQUEIO_SO_LEITURA
    ):
        conversa.situacao = CONVERSA_BLOQUEADA
        conversa.bloqueio_motivo = BLOQUEIO_SO_LEITURA
    itens_msg: list[MensagemReclamacao] = []
    for m in leitura.mensagens:
        item = mensagem_da_reclamacao(m)
        if item is not None:
            itens_msg.append(item)
    itens_msg.extend(mensagens_do_sistema(claim, linha.tipo, linha.motivo))
    itens_msg.sort(key=lambda x: x.enviada_em or agora)
    novas = 0
    vistos: set[str] = set()
    for item in itens_msg:
        if item.externo_id in vistos:
            continue
        vistos.add(item.externo_id)
        _, criada_m = await gravar.gravar_mensagem(
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
        if criada_m:
            novas += 1
    # A vez e o prazo do ML podem ter mudado sem mensagem nova: refaz a fila.
    gravar.recalcular(conversa)
    await session.flush()
    return conversa, criada, novas


async def conversas_para_etiqueta(
    session: AsyncSession,
    plataforma: str,
    chaves: Iterable[str | None],
    extra: Iterable[AtendimentoConversa] = (),
) -> list[AtendimentoConversa]:
    """As conversas que a reclamação muda: a dela e as do mesmo pedido (sem repetir)."""
    vistas: dict[UUID, AtendimentoConversa] = {c.id: c for c in extra}
    for chave in dict.fromkeys(c for c in chaves if c):
        for c in await etiqueta_fatos.conversas_do_pedido(session, plataforma, chave):
            vistas.setdefault(c.id, c)
    return list(vistas.values())


async def _travar_se_livre(session: AsyncSession, conversa: AtendimentoConversa) -> bool:
    """Trava a conversa (SKIP LOCKED, como o cron da etiqueta) e a relê; False = ocupada.

    As conversas daqui vêm de um SELECT sem trava (`conversas_do_pedido`): uma
    troca à mão commitada depois dele seria sobrescrita por um retrato velho
    de `etiqueta_manual`. Relida depois de travar, vale o que está no banco.
    Ocupada (o sync ou a tela mexendo agora) = não espera: a próxima rodada
    do cron da etiqueta pega — esperar aqui, segurando outras conversas da
    rodada, é como se faz um deadlock com o sync.
    """
    await session.flush()
    livre = (
        await session.execute(
            select(AtendimentoConversa.id)
            .where(AtendimentoConversa.id == conversa.id)
            .with_for_update(skip_locked=True, key_share=True)
        )
    ).first()
    if livre is None:
        return False
    await session.refresh(conversa)
    return True


async def recalcular_etiquetas(
    session: AsyncSession, conversas: Iterable[AtendimentoConversa], *, motivo: str, agora: datetime
) -> int:
    """`recalcular_etiqueta` em cada conversa LIVRE (travada antes); quantas mudaram. Não commita.

    A conversa que outro processo está mexendo fica como está (o cron da
    etiqueta a confere depois) — ver `_travar_se_livre`.
    """
    mudaram = 0
    for c in conversas:
        if not await _travar_se_livre(session, c):
            continue
        if await etiqueta.recalcular_etiqueta(session, c, motivo=motivo, agora=agora):
            mudaram += 1
    return mudaram


async def processar_reclamacao(
    session: AsyncSession,
    integration: Integration,
    cliente: Any,
    claim: dict,
    cota: _Cota,
    agora: datetime,
    resumo: ResumoConta,
) -> None:
    """UMA reclamação: a linha, a conversa (se der para ler) e as etiquetas. Não commita."""
    cid = _id(claim.get("id"))
    tipo = tipo_da_reclamacao(claim)
    if not cid or tipo is None:
        return
    resumo.listadas += 1
    resumo.ids.append(cid)
    last_updated = _id(claim.get("last_updated"))
    linha = await _linha(session, cid)
    anteriores = dict((linha.dados if linha is not None else None) or {})
    mexeu = linha is None or anteriores.get("last_updated") != last_updated
    lida = anteriores.get("lido_ate") == last_updated and linha is not None and linha.conversa_id
    if not mexeu and lida:
        return  # nada mudou desde a última leitura: nem escrita, nem chamada

    campos = _campos_da_linha(claim, tipo)
    acao = campos.pop("_acao")
    antes = (
        None
        if linha is None
        else (linha.tipo, linha.encerrada_em is None, linha.prazo_em, linha.conversa_id)
    )
    if linha is None:
        linha = AtendimentoReclamacao(
            id=uuid4(), integration_id=integration.id, plataforma=PLATAFORMA, externo_id=cid
        )
        session.add(linha)
        resumo.novas += 1
    else:
        resumo.atualizadas += 1
    for nome, valor in campos.items():
        setattr(linha, nome, valor)
    linha.integration_id = integration.id
    dados = _dados_da_linha(claim, acao, anteriores)

    leitura: _Leitura | None = None
    if not lida and not _falhou_ha_pouco(linha, last_updated, agora):
        if cota.pode():
            cota.gastar()
            try:
                leitura = await _ler(
                    session, cliente, claim, buscar_pack="pack_conferido" not in anteriores
                )
            except Exception as exc:  # noqa: BLE001 — a linha entra; a conversa, depois
                resumo.erros += 1
                dados["leitura_falhou"] = {"em": _iso(agora), "last_updated": last_updated}
                logger.warning(
                    "atendimento_reclamacao_leitura_falhou",
                    integration_id=str(integration.id),
                    claim_id=cid,
                    erro=_erro(exc),
                )
        else:
            resumo.adiadas += 1
    if leitura is not None:
        resumo.lidas += 1
        dados.pop("leitura_falhou", None)
        dados["pack_conferido"] = True
        if leitura.pack_id:
            dados["pack_id"] = leitura.pack_id
        if leitura.devolucao is not None:
            dados["devolucao"] = leitura.devolucao
        if leitura.reputacao:
            dados["reputacao"] = leitura.reputacao
        linha.motivo = await _motivo(session, cliente, dados.get("reason_id")) or linha.motivo
    linha.dados = dados
    await session.flush()

    conversa = None
    if leitura is not None:
        conversa, criada, novas = await _gravar_conversa(
            session, integration, claim, linha, leitura, agora
        )
        linha.conversa_id = conversa.id
        linha.dados = {**dados, "lido_ate": last_updated}
        resumo.mensagens += novas
        if criada:
            resumo.conversas_novas += 1
        await session.flush()
    elif linha.conversa_id is not None:
        conversa = await session.get(AtendimentoConversa, linha.conversa_id)
        if conversa is not None and mexeu:
            await _atualizar_conversa(session, conversa, claim, linha, agora)

    depois = (linha.tipo, linha.encerrada_em is None, linha.prazo_em, linha.conversa_id)
    if antes != depois or leitura is not None:
        alvo = await conversas_para_etiqueta(
            session,
            PLATAFORMA,
            [linha.pedido_marketplace, _id(dados.get("pack_id")) or None],
            [conversa] if conversa is not None else [],
        )
        resumo.etiquetas += await recalcular_etiquetas(
            session, alvo, motivo=MOTIVO_ETIQUETA_ML, agora=agora
        )


async def _buscar(cliente: Any, agora: datetime) -> tuple[list[dict], list[dict]]:
    """As abertas (todas, até o teto de páginas) e as encerradas da janela."""
    abertas: list[dict] = []
    for pagina in range(MAX_PAGINAS_ABERTAS):
        corpo = await cliente.buscar_reclamacoes(
            status=STATUS_ML_ABERTA, offset=pagina * PAGINA_BUSCA, limit=PAGINA_BUSCA
        )
        lote = _lista(_dict(corpo).get("data"))
        abertas.extend(lote)
        total = _dict(_dict(corpo).get("paging")).get("total")
        if len(lote) < PAGINA_BUSCA or (isinstance(total, int) and len(abertas) >= total):
            break
    encerradas: list[dict] = []
    corte = agora - JANELA_ENCERRADAS
    for pagina in range(MAX_PAGINAS_ENCERRADAS):
        corpo = await cliente.buscar_reclamacoes(
            status=STATUS_ML_ENCERRADA,
            offset=pagina * PAGINA_BUSCA,
            limit=PAGINA_BUSCA,
            sort="last_updated:desc",
        )
        lote = _lista(_dict(corpo).get("data"))
        dentro = [c for c in lote if (data_ml(c.get("last_updated")) or agora) >= corte]
        encerradas.extend(dentro)
        if len(lote) < PAGINA_BUSCA or len(dentro) < len(lote):
            break
    return abertas, encerradas


async def _abertas_sumidas(
    session: AsyncSession, integration_id: UUID, vistas: set[str]
) -> list[str]:
    """As abertas aqui que não vieram em nenhuma das listas: relidas pelo id."""
    ids = (
        (
            await session.execute(
                select(AtendimentoReclamacao.externo_id)
                .where(
                    AtendimentoReclamacao.plataforma == PLATAFORMA,
                    AtendimentoReclamacao.integration_id == integration_id,
                    AtendimentoReclamacao.encerrada_em.is_(None),
                )
                .order_by(AtendimentoReclamacao.updated_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return [i for i in ids if i not in vistas][:MAX_CONFERIR_SUMIDAS]


async def _desfazer(session: AsyncSession, integration: Integration) -> None:
    """Rollback da reclamação que falhou — e relê a loja, que o rollback expirou."""
    await session.rollback()
    await session.refresh(integration)


async def sincronizar_reclamacoes_ml(
    integration: Integration | UUID,
    *,
    fabrica_cliente: FabricaCliente | None = None,
    agora: datetime | None = None,
) -> dict:
    """As reclamações de UMA conta do ML, na sessão própria. Nunca levanta.

    Commita por reclamação (como `gravar.fim_do_item`): uma reclamação que
    falha não desfaz as outras, e a conversa não fica travada enquanto a
    rodada fala com o ML. Devolve o resumo (contagens + `erro` da conta).
    """
    from app.services.atendimento import clientes

    fabrica = fabrica_cliente or clientes.cliente_da_integracao
    agora = agora or _agora()
    integration_id = integration.id if isinstance(integration, Integration) else integration
    resumo = ResumoConta()
    async with _db.SessionLocal() as session:
        integ = await session.get(Integration, integration_id)
        plataforma = getattr(getattr(integ, "platform", None), "value", None)
        if integ is None or integ.archived_at is not None or plataforma != PLATAFORMA:
            return resumo.como_dict()
        try:
            cliente = await fabrica(integ)
            abertas, encerradas = await _buscar(cliente, agora)
        except Exception as exc:  # noqa: BLE001 — a conta que falha não para as outras
            resumo.erros += 1
            resumo.erro = _erro(exc)
            logger.warning(
                "atendimento_reclamacoes_conta_falhou",
                integration_id=str(integration_id),
                erro=resumo.erro,
            )
            return resumo.como_dict()

        vistas = {_id(c.get("id")) for c in [*abertas, *encerradas]}
        conferidas: list[dict] = []
        for cid in await _abertas_sumidas(session, integration_id, vistas):
            try:
                conferidas.append(await cliente.get_claim(cid))
            except Exception as exc:  # noqa: BLE001
                resumo.erros += 1
                logger.info("atendimento_reclamacao_conferir_falhou", claim_id=cid, erro=_erro(exc))
        cota = _Cota(MAX_LEITURAS_RODADA)
        feitas: set[str] = set()
        # As abertas primeiro: são elas que têm prazo (e a cota pode acabar).
        for claim in [*abertas, *conferidas, *encerradas]:
            cid = _id(_dict(claim).get("id"))
            if not cid or cid in feitas:
                continue
            feitas.add(cid)
            try:
                await processar_reclamacao(session, integ, cliente, claim, cota, agora, resumo)
                await session.commit()
            except Exception as exc:  # noqa: BLE001 — uma reclamação não para a conta
                await _desfazer(session, integ)
                resumo.erros += 1
                logger.warning(
                    "atendimento_reclamacao_falhou",
                    integration_id=str(integration_id),
                    claim_id=cid,
                    erro=_erro(exc),
                )
    logger.info(
        "atendimento_reclamacoes_conta",
        integration_id=str(integration_id),
        **{k: v for k, v in resumo.como_dict().items() if k != "erro"},
    )
    return resumo.como_dict()


# ── A rodada (todas as contas) ────────────────────────────────────────────

# A conta com o canal do pós-venda DESLIGADO (o interruptor da equipe) fica
# de fora; a PARADA (sem permissão, ou o token que não renova) também, até a
# hora seguinte ou até a integração mudar — a mesma regra do sync.
SEM_ESCOPO_RETENTAR = timedelta(hours=1)


async def contas_ml(session: AsyncSession, agora: datetime | None = None) -> list[UUID]:
    agora = agora or _agora()
    parado = or_(
        AtendimentoCanal.status == "desligado",
        and_(
            or_(
                AtendimentoCanal.status == "sem_escopo",
                and_(
                    AtendimentoCanal.status == "erro",
                    func.coalesce(AtendimentoCanal.ultimo_erro, "").contains("token_nao_renovou"),
                ),
            ),
            AtendimentoCanal.ultimo_erro_em >= agora - SEM_ESCOPO_RETENTAR,
            Integration.updated_at <= AtendimentoCanal.ultimo_erro_em,
        ),
    )
    fora = exists(
        select(AtendimentoCanal.id).where(
            AtendimentoCanal.integration_id == Integration.id,
            AtendimentoCanal.canal == CANAL_POS_VENDA,
            parado,
        )
    )
    return list(
        (
            await session.execute(
                select(Integration.id)
                .where(
                    Integration.platform == IntegrationPlatform(PLATAFORMA),
                    Integration.archived_at.is_(None),
                    ~fora,
                )
                .order_by(Integration.name)
            )
        )
        .scalars()
        .all()
    )


async def sincronizar_todas_ml(
    *, fabrica_cliente: FabricaCliente | None = None, agora: datetime | None = None
) -> dict:
    """Todas as contas do ML, `CONCORRENCIA_CONTAS` por vez. Nunca levanta."""
    agora = agora or _agora()
    async with _db.SessionLocal() as session:
        ids = await contas_ml(session, agora)
    sem = asyncio.Semaphore(CONCORRENCIA_CONTAS)

    async def _uma(integration_id: UUID) -> dict:
        async with sem:
            return await sincronizar_reclamacoes_ml(
                integration_id, fabrica_cliente=fabrica_cliente, agora=agora
            )

    resultados = await asyncio.gather(*(_uma(i) for i in ids), return_exceptions=True)
    total: dict[str, Any] = {"contas": len(ids), "contas_com_erro": 0}
    for integration_id, r in zip(ids, resultados, strict=True):
        if isinstance(r, BaseException):
            # A conta "nunca levanta"; se levantou (banco fora), as outras seguem.
            logger.error(
                "atendimento_reclamacoes_conta_quebrou",
                integration_id=str(integration_id),
                err=type(r).__name__,
            )
            total["contas_com_erro"] += 1
            continue
        for chave, valor in r.items():
            if isinstance(valor, int):
                total[chave] = total.get(chave, 0) + valor
        if r.get("erro"):
            total["contas_com_erro"] += 1
    return total


async def _pegar_trava() -> tuple[bool, str | None]:
    token = uuid4().hex
    try:
        pegou = await redis.set(CHAVE_TRAVA, token, nx=True, ex=TRAVA_TTL_S)
    except Exception as exc:  # noqa: BLE001
        # Sem Redis, roda sem trava: a gravação é idempotente (UNIQUE + ids).
        logger.warning("atendimento_reclamacoes_trava_indisponivel", err=type(exc).__name__)
        return True, None
    return bool(pegou), token


async def _soltar_trava(token: str | None) -> None:
    if token is None:
        return
    try:
        await redis.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end",
            1,
            CHAVE_TRAVA,
            token,
        )
    except Exception:  # noqa: BLE001 — o TTL solta sozinho
        logger.warning("atendimento_reclamacoes_trava_soltar_falhou")


async def atendimento_reclamacoes(
    ctx: dict | None = None, *, fabrica_cliente: FabricaCliente | None = None
) -> dict | None:
    """O CRON (o integrador registra no worker, a cada 10 min).

    Só roda com `ATENDIMENTO_RECLAMACOES_ATIVA` E `ATENDIMENTO_LEITURA_ATIVA`
    (interruptor próprio: a leitura já está ligada em produção, então o
    deploy sozinho não liga esta rodada); uma rodada por vez. Lê as
    reclamações de todas as contas do ML e liga as devoluções/disputas de
    Shopee e TikTok que a Logística já leu — commitando a cada
    `COMMIT_DEVOLUCOES_A_CADA` casos, para não segurar as conversas travadas
    até o fim. Nunca levanta: o erro vira log (só o tipo) e `None`.
    """
    s = get_settings()
    if not (s.atendimento_leitura_ativa and s.atendimento_reclamacoes_ativa):
        return None
    pegou, token = await _pegar_trava()
    if not pegou:
        logger.info("atendimento_reclamacoes_ocupado")
        return {"pulado": True}
    try:
        agora = _agora()
        resumo: dict[str, Any] = {
            "ml": await sincronizar_todas_ml(fabrica_cliente=fabrica_cliente, agora=agora)
        }
        from app.services.atendimento import clientes, reclamacoes_devolucoes

        try:
            async with _db.SessionLocal() as session:
                resumo["devolucoes"] = await reclamacoes_devolucoes.ligar_devolucoes(
                    session,
                    agora=agora,
                    commit_a_cada=COMMIT_DEVOLUCOES_A_CADA,
                    # O motivo do comprador e o comprador da conversa nova: só
                    # GET, com a cota de `reclamacoes_devolucoes`.
                    fabrica_cliente=fabrica_cliente or clientes.cliente_da_integracao,
                )
                await session.commit()
        except Exception as exc:  # noqa: BLE001 — o ML já foi gravado
            logger.error("atendimento_devolucoes_falhou", err=type(exc).__name__)
            resumo["devolucoes"] = {"erro": type(exc).__name__}
    except Exception as exc:  # noqa: BLE001
        logger.error("atendimento_reclamacoes_falhou", err=type(exc).__name__)
        return None
    finally:
        await _soltar_trava(token)
    logger.info("atendimento_reclamacoes_tick", **resumo)
    return resumo


# ── Para a tela (o cartão da reclamação) ─────────────────────────────────


async def reclamacoes_da_conversa(
    session: AsyncSession, conversa: AtendimentoConversa, *, limite: int = 20
) -> list[AtendimentoReclamacao]:
    """As reclamações/devoluções da conversa: pela `conversa_id` OU pelo pedido.

    As ABERTAS primeiro (prazo mais curto antes; sem prazo no fim), depois as
    encerradas, da mais recente para a mais antiga. A mesma ligação que a
    etiqueta usa (`etiqueta_fatos.reclamacoes_abertas`), com as encerradas.
    """
    chaves = etiqueta_fatos.chaves_do_pedido(conversa)
    conds = [AtendimentoReclamacao.conversa_id == conversa.id]
    if chaves:
        # O pedido da reclamação (o ORDER, no ML) OU o pack dela: a conversa
        # do pack de carrinho só tem o pack (`etiqueta_fatos.condicao_do_pedido`).
        conds.append(
            and_(
                AtendimentoReclamacao.plataforma == conversa.plataforma,
                etiqueta_fatos.condicao_do_pedido(chaves),
            )
        )
    return list(
        (
            await session.execute(
                select(AtendimentoReclamacao)
                .where(or_(*conds))
                .order_by(
                    AtendimentoReclamacao.encerrada_em.is_(None).desc(),
                    AtendimentoReclamacao.prazo_em.asc().nulls_last(),
                    func.coalesce(
                        AtendimentoReclamacao.encerrada_em, AtendimentoReclamacao.aberta_em
                    )
                    .desc()
                    .nulls_last(),
                )
                .limit(limite)
            )
        )
        .scalars()
        .all()
    )


def url_na_plataforma(r: AtendimentoReclamacao) -> str | None:
    """O "Abrir na plataforma": a página da VENDA, onde a reclamação aparece.

    ML: a venda (pelo pack, quando conhecido — é a página que o Seller
    Central abre); Shopee e TikTok: o pedido no Seller Center. Os mesmos
    endereços que o DaVinci já usa (`vigia_importacao`).
    """
    pedido = _id(r.pedido_marketplace)
    dados = r.dados or {}
    if r.plataforma == "ml":
        alvo = _id(dados.get("pack_id")) or pedido
        return f"https://www.mercadolivre.com.br/vendas/{alvo}/detalhe" if alvo else None
    if r.plataforma == "shopee" and pedido:
        return f"https://seller.shopee.com.br/portal/sale/order/{pedido}"
    if r.plataforma == "tiktok" and pedido:
        return f"https://seller-br.tiktok.com/order/detail?order_no={pedido}"
    return None


_STATUS_ML = {
    ("opened", "claim"): "Aberta — com o comprador",
    ("opened", "dispute"): "Em mediação no Mercado Livre",
    ("opened", "recontact"): "Aberta — recontato do comprador",
}


def status_para_tela(r: AtendimentoReclamacao) -> str | None:
    """O status em português ("Em mediação no Mercado Livre", "Loja contestou").

    Shopee/TikTok pela tabela de `reclamacoes_devolucoes.STATUS_TELA` (a
    mesma do cartão), lida na hora: a linha gravada antes da tabela já sai
    com o texto novo.
    """
    dados = r.dados or {}
    if r.plataforma in ("shopee", "tiktok"):
        from app.services.atendimento import reclamacoes_devolucoes

        texto = reclamacoes_devolucoes.status_tela(r.plataforma, r.status, dados.get("tipo_caso"))
        if texto:
            return texto
    if r.plataforma == "ml":
        if r.encerrada_em is not None:
            res = _dict(dados.get("resolucao"))
            lados = {_id(b).lower() for b in res.get("beneficiado") or []}
            if len(lados) == 1 and next(iter(lados)) in _BENEFICIADO:
                return f"Encerrada — {_BENEFICIADO[next(iter(lados))]}"
            return "Encerrada"
        chave = (_id(r.status).lower(), _id(dados.get("etapa")).lower())
        return _STATUS_ML.get(chave, "Aberta")
    texto = _id(dados.get("status_texto"))
    if texto:
        return texto
    if r.encerrada_em is not None:
        return "Encerrada"
    return _id(r.status) or None


def para_tela(r: AtendimentoReclamacao) -> dict[str, Any]:
    """A reclamação no formato do cartão (`AtendimentoReclamacao.vue`). Sem texto de comprador.

    `motivo` = o que o comprador alegou, em português (código do ML/Shopee
    traduzido); `solucao` = o que ele PEDIU na Shopee/TikTok ("Devolução +
    reembolso") — até 02/10/2026 esse texto ia no `motivo`, e a linha antiga
    ainda não relida é lida assim.
    """
    from app.services.atendimento import reclamacoes_devolucoes

    dados = r.dados or {}
    motivo = r.motivo
    solucao = _id(dados.get("solucao")) or None
    if (
        r.plataforma in reclamacoes_devolucoes.PLATAFORMAS_LIGADAS
        and motivo in reclamacoes_devolucoes.SOLUCOES_PT.values()
        and not dados.get("motivo_lido_em")
    ):
        solucao, motivo = solucao or motivo, None
    numero = _id(r.externo_id)
    if numero.startswith(PREFIXO_SEM_ID):
        numero = ""  # sem o id da plataforma (a Logística ainda não o tinha)
    devolucao = _dict(dados.get("devolucao"))
    reputacao = _id(dados.get("reputacao")).lower()
    tipo_nome = NOME_TIPO.get(r.tipo, r.tipo)
    if r.plataforma in ("shopee", "tiktok") and r.tipo == RECLAMACAO_TIPO_RECLAMACAO:
        tipo_nome = "Disputa"
    return {
        "id": r.id,
        "plataforma": r.plataforma,
        "plataforma_nome": NOME_PLATAFORMA.get(r.plataforma, r.plataforma),
        "numero": numero or None,
        "tipo": r.tipo,
        "tipo_rotulo": tipo_nome,
        "status": r.status,
        "status_rotulo": status_para_tela(r),
        "aberta": r.encerrada_em is None,
        "motivo": reclamacoes_devolucoes.motivo_legivel(motivo)
        or (f"Motivo {dados['reason_id']}" if dados.get("reason_id") else None),
        "solucao": solucao,
        "pedido_marketplace": r.pedido_marketplace,
        "prazo_em": r.prazo_em if r.encerrada_em is None else None,
        "aberta_em": r.aberta_em,
        "encerrada_em": r.encerrada_em,
        "acao_pendente": (_id(dados.get("acao_texto")) or None) if r.encerrada_em is None else None,
        "mediacao": r.tipo == RECLAMACAO_TIPO_MEDIACAO,
        "reputacao_afetada": (
            None if not reputacao else reputacao in ("affected", "affects", "true", "yes")
        ),
        "devolucao_status": _id(devolucao.get("status")) or None,
        "conversa_id": r.conversa_id,
        "url_plataforma": url_na_plataforma(r),
    }
