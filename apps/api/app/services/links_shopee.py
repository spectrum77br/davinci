"""O "Abrir na Shopee": o endereço certo no Seller Center (02/10/2026).

POR QUE EXISTE. A página do pedido no Seller Center é
`/portal/sale/order/<order_id>` — e o `order_id` é o número INTERNO da
Shopee (15 dígitos, ex. 244141571124463), não o `order_sn` que o DaVinci
guarda (26092743U4QU7F). A rota do próprio Seller Center é
`/portal/sale/order/:id(\\d+)`: com o order_sn ela não abre o pedido (caso
da Vortan, avaliação do pedido 26092743U4QU7F). Os quatro montadores de link
(avaliações, reclamações, painel do pedido, Vigia de importação) usavam o
order_sn; agora todos passam por aqui.

DE ONDE VEM O NÚMERO. A Open API (get_order_list/detail, escrow, returns,
get_comment) só devolve o order_sn. O order_id aparece apenas no payload cru
de quatro cartões do chat (`atendimento_mensagens.payload`), que o
`shopee.interpretar` grava como TIPO_OUTRO:

  crm_order_rate        content.unrated_order_reminder.order_id
  logistics_card        content.common_info.order_id
  return_refund_card    content.order_info.order_id   + content.rr_detail.return_id
  track_rr_status_card  content.order_detail.order_id / tracking_info.order_id
                        + content.rr_detail.return_id

Nenhum deles traz o order_sn: o par order_sn → order_id vem da CONVERSA
(`pedido_marketplace`), e a conversa pode falar de outro pedido do mesmo
comprador. Por isso o par só é aceito quando é SEGURO (`ids_shopee`):

  1. a conversa (mesma integração, `pedido_marketplace` = o order_sn) cita
     exatamente 1 order_sn — o dela, somando todo `order_sn` dos payloads;
  2. os cartões da conversa trazem exatamente 1 order_id;
  3. a data e a hora batem (`par_coerente`). O order_id É a hora em que o
     pedido nasceu: `order_id // 1000` = milissegundos desde 01/01/2019
     00:00 UTC (os 3 últimos dígitos são sequência). E o order_sn começa
     com o AAMMDD dessa hora no fuso de Singapura (UTC+8). Então:
       - a data (UTC+8) do order_id tem de ser o AAMMDD do order_sn, E
       - a hora do order_id tem de bater, em até 60 s, com a criação do
         pedido que o DaVinci já tem (`conversa.dados.pedido_mkt.criado_em`
         do mesmo pedido, `atendimento_pedidos_comprador.criado_em`). Sem
         hora conhecida, NÃO aceita.

MEDIDO EM PRODUÇÃO (02/10/2026, só SELECT). Dos 183 pares conversa →
(order_sn, order_id) dos cartões: 174 com a data UTC+8 igual ao AAMMDD, e
173 com a hora a menos de 2 s da criação gravada do pedido. Os que não batem
são OUTRO pedido do comprador: em 10 deles o comprador tem outro pedido
criado no mesmo segundo do order_id. Rodando ESTE código sobre as linhas
da consulta em produção: aceita 164 dos 182 pedidos com cartão (o caso
26092743U4QU7F → 244141571124463 incluso; os 164 batem a < 2 s com a
criação do índice de pedidos), 0 conflitos; dos 22 pares conferidos por
VALOR (buyer_pay_amount do cartão = gross_amount do escrow) aceita 20 e
recusa 2 (só pela regra 1: conversa com 2–3 order_sn); dos 3 reprovados
por valor e dos 10 errados conhecidos, aceita 0. A checagem só por data
(±3 dias pela reta ~85,04 bilhões de ids/dia da investigação) aceitaria 1
errado conhecido (outro pedido do comprador, 1 dia antes) — por isso a
data é exata e a hora é obrigatória. Consulta: ~15 ms para 31 pedidos.
Devoluções: 13 return_id aceitos (das 129 reclamações com conversa e
return_sn).

DEVOLUÇÃO. A página é `/portal/sale/return/<return_id>` (o número interno,
`rr_detail.return_id`), não o return_sn que a reclamação guarda. Também é
hora: a data UTC+8 do return_id é o AAMMDD do return_sn (13 de 15 em
produção; os 2 que não batem são a OUTRA devolução do mesmo pedido, que tem
2 reclamações). Aceita só da conversa DA PRÓPRIA reclamação
(`conversa_id`), com 1 return_id distinto nos cartões rr, o order_id desses
cartões coerente com o pedido da reclamação (regra 3) e a data do return_id
igual à do return_sn.

SEM O NÚMERO (~95% dos pedidos): a lista de pedidos já buscando o order_sn,
`/portal/sale/order?search=<order_sn>` (o Seller Center lê `?search=` e usa
a categoria de busca salva no perfil — a padrão é o nº do pedido), e para a
devolução `/portal/sale/returnrefundcancel?keyword=<return_sn>&keywordType=
return_sn`. Na dúvida, sempre a busca: nunca abrir o pedido de outra pessoa.

Nada aqui grava: o número é lido na hora de montar o link.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from sqlalchemy import and_, func, literal_column, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AtendimentoConversa, AtendimentoMensagem, AtendimentoPedidoComprador

SELLER_CENTER = "https://seller.shopee.com.br"
PLATAFORMA = "shopee"

# order_id / return_id internos: só dígitos (15 hoje); nada que mexa na URL.
_RE_ID = re.compile(r"^[0-9]{10,20}$")
# order_sn / return_sn: letra e dígito (14–15 hoje).
_RE_SN = re.compile(r"^[A-Za-z0-9]{1,64}$")

# O order_id/return_id = milissegundos desde esta época × 1000 + sequência.
_EPOCA_ID = datetime(2019, 1, 1, tzinfo=UTC)
# O AAMMDD do order_sn/return_sn é a data em Singapura (sem horário de verão).
_FUSO_SN = timezone(timedelta(hours=8))
# Hora do order_id × criação gravada do pedido: medido < 1 s (máx. 0,898 s
# nos 164 pares aceitos). Folga curta de propósito: o comprador pode ter outro
# pedido criado 24 a 54 s depois, e esse NÃO pode passar.
TOLERANCIA_HORA = timedelta(seconds=5)

CARTOES = ("crm_order_rate", "logistics_card", "return_refund_card", "track_rr_status_card")
CARTOES_DEVOLUCAO = ("return_refund_card", "track_rr_status_card")
_CAMINHOS_PEDIDO = (
    ("content", "unrated_order_reminder", "order_id"),
    ("content", "common_info", "order_id"),
    ("content", "order_info", "order_id"),
    ("content", "order_detail", "order_id"),
    ("content", "tracking_info", "order_id"),
)
_CAMINHO_DEVOLUCAO = ("content", "rr_detail", "return_id")
# Todo order_sn do payload, em qualquer profundidade (source_content,
# cartão "order", citação...). Constante: vai como literal, sem parâmetro.
_TODOS_OS_SN = literal_column("'lax $.**.order_sn'::jsonpath")


# ── Os endereços ──────────────────────────────────────────────────────────


def _texto(valor: Any) -> str:
    if valor is None or isinstance(valor, bool):
        return ""
    return str(valor).strip()


def id_interno(valor: Any) -> str | None:
    """O order_id/return_id interno (só dígitos, 10–20) ou None."""
    s = _texto(valor)
    return s if _RE_ID.match(s) and s.strip("0") else None


def numero_seguro(valor: Any) -> str | None:
    """O order_sn/return_sn (só letra e dígito) ou None."""
    s = _texto(valor)
    return s if _RE_SN.match(s) else None


def url_pedido_shopee(order_sn: Any, order_id: Any = None) -> str | None:
    """A página do pedido (com o order_id) ou a lista de pedidos buscando o order_sn."""
    oid = id_interno(order_id)
    if oid:
        return f"{SELLER_CENTER}/portal/sale/order/{oid}"
    sn = numero_seguro(order_sn)
    if sn:
        return f"{SELLER_CENTER}/portal/sale/order?{urlencode({'search': sn})}"
    return None


def url_devolucao_shopee(return_sn: Any, return_id: Any = None) -> str | None:
    """A página da devolução (com o return_id) ou a lista de devoluções buscando o return_sn."""
    rid = id_interno(return_id)
    if rid:
        return f"{SELLER_CENTER}/portal/sale/return/{rid}"
    sn = numero_seguro(return_sn)
    if sn:
        busca = urlencode({"keyword": sn, "keywordType": "return_sn"})
        return f"{SELLER_CENTER}/portal/sale/returnrefundcancel?{busca}"
    return None


# ── A conferência do par (data e hora) ────────────────────────────────────


def criado_em_do_id(valor: Any) -> datetime | None:
    """A hora (UTC) em que a Shopee criou o pedido/devolução deste id interno."""
    oid = id_interno(valor)
    if oid is None:
        return None
    try:
        return _EPOCA_ID + timedelta(milliseconds=int(oid) // 1000)
    except (OverflowError, ValueError):
        return None


def data_do_sn(valor: Any) -> date | None:
    """O AAMMDD do começo do order_sn/return_sn, ou None."""
    s = numero_seguro(valor)
    if s is None or len(s) < 7 or not s[:6].isdigit():
        return None
    try:
        return date(2000 + int(s[:2]), int(s[2:4]), int(s[4:6]))
    except ValueError:
        return None


def mesma_data(sn: Any, id_: Any) -> bool:
    """A data (UTC+8) do id interno é o AAMMDD do sn? Sem um dos dois: False."""
    nasceu = criado_em_do_id(id_)
    dia = data_do_sn(sn)
    return nasceu is not None and dia is not None and nasceu.astimezone(_FUSO_SN).date() == dia


def _utc(valor: Any) -> datetime | None:
    if isinstance(valor, datetime):
        return valor.astimezone(UTC) if valor.tzinfo is not None else None
    s = _texto(valor)
    if not s:
        return None
    try:
        lido = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return lido.astimezone(UTC) if lido.tzinfo is not None else None


def par_coerente(order_sn: Any, order_id: Any, criados: Iterable[Any]) -> bool:
    """O order_id é MESMO deste order_sn? Data exata (UTC+8) e hora (até 60 s).

    `criados` = as horas de criação do pedido que o DaVinci tem (do retrato
    da conversa e do índice de pedidos do comprador). Nenhuma conhecida →
    False: sem a conferência, não aceita. Todas as conhecidas têm de bater.
    """
    if not mesma_data(order_sn, order_id):
        return False
    nasceu = criado_em_do_id(order_id)
    horas = [h for h in (_utc(c) for c in criados) if h is not None]
    if nasceu is None or not horas:
        return False
    return all(abs(nasceu - h) <= TOLERANCIA_HORA for h in horas)


# ── A leitura em lote ─────────────────────────────────────────────────────


@dataclass
class IdsShopee:
    """O que `ids_shopee` achou: só os pares SEGUROS (o resto cai na busca)."""

    # (integração, order_sn) → order_id
    pedidos: dict[tuple[UUID, str], str] = field(default_factory=dict)
    # (conversa da reclamação, return_sn) → return_id
    devolucoes: dict[tuple[UUID, str], str] = field(default_factory=dict)

    def pedido(self, integration_id: UUID | None, order_sn: Any) -> str | None:
        sn = numero_seguro(order_sn)
        if integration_id is None or sn is None:
            return None
        return self.pedidos.get((integration_id, sn))

    def devolucao(self, conversa_id: UUID | None, return_sn: Any) -> str | None:
        sn = numero_seguro(return_sn)
        if conversa_id is None or sn is None:
            return None
        return self.devolucoes.get((conversa_id, sn))


@dataclass
class _Conversa:
    integration_id: UUID | None
    pedido: str | None
    sns: set[str] = field(default_factory=set)
    pedidos: set[str] = field(default_factory=set)
    pedidos_rr: set[str] = field(default_factory=set)
    devolucoes: set[str] = field(default_factory=set)


def _so_um(valores: set[str]) -> str | None:
    return next(iter(valores)) if len(valores) == 1 else None


async def ids_shopee(
    session: AsyncSession,
    *,
    pedidos: Iterable[tuple[UUID | None, Any]] = (),
    devolucoes: Iterable[tuple[UUID | None, Any, Any]] = (),
) -> IdsShopee:
    """Os ids internos SEGUROS, numa consulta só (ver o topo).

    `pedidos` = (integração, order_sn) — o order_id de cada um, das conversas
    Shopee da MESMA integração com esse `pedido_marketplace`.
    `devolucoes` = (conversa da reclamação, order_sn, return_sn) — o
    return_id, só dos cartões rr dessa conversa.
    """
    alvos = {
        (i, sn)
        for i, sn in ((i, numero_seguro(s)) for i, s in pedidos)
        if i is not None and sn is not None
    }
    pedidos_dev = []
    for conversa_id, order_sn, return_sn in devolucoes:
        sn, rsn = numero_seguro(order_sn), numero_seguro(return_sn)
        if conversa_id is not None and sn is not None and rsn is not None:
            pedidos_dev.append((conversa_id, sn, rsn))
    if not alvos and not pedidos_dev:
        return IdsShopee()

    c, m = AtendimentoConversa, AtendimentoMensagem
    tipo = m.payload["message_type"].astext
    criado_indice = (
        select(func.max(AtendimentoPedidoComprador.criado_em))
        .where(
            AtendimentoPedidoComprador.integration_id == c.integration_id,
            AtendimentoPedidoComprador.plataforma == PLATAFORMA,
            AtendimentoPedidoComprador.pedido == c.pedido_marketplace,
        )
        .scalar_subquery()
    )
    filtros = []
    if alvos:
        filtros.append(
            and_(
                c.integration_id.in_(sorted({i for i, _ in alvos})),
                c.pedido_marketplace.in_(sorted({sn for _, sn in alvos})),
            )
        )
    if pedidos_dev:
        # A conversa da reclamação (os cartões rr) e as do pedido dela (a hora
        # de criação); a integração é a da conversa da reclamação.
        filtros.append(c.id.in_(sorted({cid for cid, _, _ in pedidos_dev})))
        filtros.append(c.pedido_marketplace.in_(sorted({sn for _, sn, _ in pedidos_dev})))
    linhas = (
        await session.execute(
            select(
                c.id,
                c.integration_id,
                c.pedido_marketplace,
                c.dados[("pedido_mkt", "pedido")].astext,
                c.dados[("pedido_mkt", "criado_em")].astext,
                criado_indice,
                tipo,
                *(m.payload[caminho].astext for caminho in _CAMINHOS_PEDIDO),
                m.payload[_CAMINHO_DEVOLUCAO].astext,
                func.jsonb_path_query_array(m.payload, _TODOS_OS_SN, type_=JSONB),
            )
            .select_from(c)
            .outerjoin(
                m,
                and_(
                    m.conversa_id == c.id,
                    or_(tipo.in_(CARTOES), func.jsonb_path_exists(m.payload, _TODOS_OS_SN)),
                ),
            )
            .where(c.plataforma == PLATAFORMA, or_(*filtros))
        )
    ).all()
    return montar_ids(linhas, alvos, pedidos_dev)


def montar_ids(
    linhas: Iterable[Any],
    alvos: Iterable[tuple[UUID, str]],
    pedidos_dev: Iterable[tuple[UUID, str, str]],
) -> IdsShopee:
    """As linhas da consulta de `ids_shopee` → os pares SEGUROS (sem I/O).

    Cada linha: (conversa, integração, pedido_marketplace, pedido_mkt.pedido,
    pedido_mkt.criado_em, criado_em do índice, message_type, os 5 order_id,
    o return_id, [order_sn do payload]) — a mensagem é NULL na conversa sem
    cartão nem order_sn.
    """
    saida = IdsShopee()
    conversas: dict[UUID, _Conversa] = {}
    criados: dict[tuple[UUID, str], list[Any]] = {}
    for linha in linhas:
        cid, integ, pm, pmk_pedido, pmk_criado, criado_idx, tipo_msg = linha[:7]
        ids_pedido = {i for i in (id_interno(v) for v in linha[7:12]) if i}
        devolucao, sns_msg = linha[12], linha[13]
        conv = conversas.get(cid)
        if conv is None:
            conv = conversas[cid] = _Conversa(integration_id=integ, pedido=numero_seguro(pm))
            if conv.pedido:
                conv.sns.add(conv.pedido)
            if integ is not None:
                sn_pmk = numero_seguro(pmk_pedido)
                if sn_pmk and pmk_criado:
                    criados.setdefault((integ, sn_pmk), []).append(pmk_criado)
                if conv.pedido and criado_idx is not None:
                    criados.setdefault((integ, conv.pedido), []).append(criado_idx)
        for v in sns_msg if isinstance(sns_msg, list) else ():
            if isinstance(v, str | int) and _texto(v):
                conv.sns.add(_texto(v))
        if tipo_msg in CARTOES:
            conv.pedidos |= ids_pedido
            if tipo_msg in CARTOES_DEVOLUCAO:
                conv.pedidos_rr |= ids_pedido
                rid = id_interno(devolucao)
                if rid:
                    conv.devolucoes.add(rid)

    do_pedido: dict[tuple[UUID, str], list[_Conversa]] = {}
    for conv in conversas.values():
        if conv.integration_id is not None and conv.pedido:
            do_pedido.setdefault((conv.integration_id, conv.pedido), []).append(conv)
    for integ, sn in alvos:
        aceitos = set()
        for conv in do_pedido.get((integ, sn), ()):
            # Conversa que cita outro pedido ou traz 2 order_id: não conta.
            oid = _so_um(conv.pedidos) if len(conv.sns) == 1 else None
            if oid and par_coerente(sn, oid, criados.get((integ, sn), ())):
                aceitos.add(oid)
        oid = _so_um(aceitos)  # duas conversas discordando: busca
        if oid:
            saida.pedidos[(integ, sn)] = oid

    for conversa_id, sn, rsn in pedidos_dev:
        conv = conversas.get(conversa_id)
        if conv is None or conv.integration_id is None:
            continue
        rid, oid = _so_um(conv.devolucoes), _so_um(conv.pedidos_rr)
        if (
            rid
            and oid
            and mesma_data(rsn, rid)
            and par_coerente(sn, oid, criados.get((conv.integration_id, sn), ()))
        ):
            saida.devolucoes[(conversa_id, rsn)] = rid
    return saida
