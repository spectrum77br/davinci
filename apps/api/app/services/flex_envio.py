"""Flex: reconhecer o pedido que sai pelo Flex e registrar isso.

Projeto Flex (Downloads/procedimento-flex.md; fatos das APIs em
relatorios/Flex_analise_02-10-2026.md). Este módulo é a ETAPA 1: saber, de
cada pedido, se a plataforma o mandou pelo Flex — sem nenhuma chamada a mais.

Como cada plataforma diz isso (FATO, documentação oficial):

  • Mercado Livre: o PEDIDO não traz o tipo de logística, só `shipping.id`. O
    ENVIO (`GET /shipments/{id}`) traz `logistic.type = "self_service"` no
    formato novo (cabeçalho `x-format-new: true`, obrigatório desde
    12/10/2025) e `logistic_type = "self_service"` no topo no formato antigo.
    A Logística e o shipment check já leem esse envio (o primeiro sem o
    cabeçalho), então lemos as DUAS chaves. O Turbo também vem como
    `self_service` — e conta como Flex: sai de São Bernardo do mesmo jeito.
  • Shopee: o Flex é a "Shopee Entrega Direta", canal 90022 (guia 290 da Open
    Platform, de 2024 — a confirmar num pedido real, por isso a lista de canais
    fica em `flex_shopee_canais`). O pedido traz
    `package_list[].logistics_channel_id` e `shipping_carrier`. Vale o canal
    OU o nome — o id é o sinal forte (a Shopee renomeou "Entrega Turbo" para
    "Turbo" em 13/07/2026 e o `shipping_carrier` mudou junto), o nome cobre o
    pedido "mascarado" ou o canal ainda não cadastrado.

`eh_envio_flex` devolve True/False quando a plataforma DISSE o tipo e None
quando não disse (envio que não respondeu, payload sem as chaves) — "não sei"
nunca vira "não é Flex" no banco: quem grava só grava o que foi dito.

Pedido Flex SEMPRE sai do .sp: `flex_pedido.no_sp` diz se o Bling já baixa
dele (todos os itens no lote sp). Os que ainda não estão no .sp descontam do
saldo Flex da família (etapas seguintes).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import BlingOrder, FlexPedido, Logistica
from app.services import logistica_rules
from app.services.estoque_familia import lote_de

logger = structlog.get_logger()

PLATAFORMA_ML = "ml"
PLATAFORMA_SHOPEE = "shopee"

# Tipo de logística do ML que é Flex (e Turbo, que é Flex com prazo menor).
ML_TIPO_FLEX = "self_service"
# Nome da transportadora da Shopee no pedido Flex (comparado sem maiúscula e
# sem espaço sobrando).
SHOPEE_TRANSPORTADORA_FLEX = "shopee entrega direta"

# Rótulos de `logistica.plataforma` (e valores de `integrations.platform`)
# que são cada plataforma — os mesmos conjuntos que a Logística usa.
_ROTULOS_ML = frozenset(logistica_rules._ML_PLATAFORMAS)
_ROTULOS_SHOPEE = frozenset(logistica_rules._SHOPEE_PLATAFORMAS)


def plataforma_flex(plataforma: Any) -> str | None:
    """'ml' | 'shopee' | None. Aceita o rótulo da Logística ("Mercado
    Livre"), a chave ("ml") ou o enum da integração."""
    valor = getattr(plataforma, "value", plataforma)
    p = str(valor or "").strip().lower()
    if p in _ROTULOS_ML:
        return PLATAFORMA_ML
    if p in _ROTULOS_SHOPEE:
        return PLATAFORMA_SHOPEE
    return None


def canais_shopee_flex(valor: str | None = None) -> frozenset[str]:
    """Os `logistics_channel_id` da Shopee que são Flex (config
    `flex_shopee_canais`, separados por vírgula)."""
    bruto = get_settings().flex_shopee_canais if valor is None else valor
    return frozenset(c.strip() for c in (bruto or "").split(",") if c.strip())


# ---- Mercado Livre ------------------------------------------------------------


def tipo_envio_ml(envio: Mapping[str, Any] | None) -> str | None:
    """`logistic.type` (formato novo) ou `logistic_type` (antigo) do envio do
    ML. O novo vence quando os dois vêm."""
    if not isinstance(envio, Mapping):
        return None
    logistic = envio.get("logistic")
    if isinstance(logistic, Mapping):
        novo = str(logistic.get("type") or "").strip().lower()
        if novo:
            return novo
    antigo = str(envio.get("logistic_type") or "").strip().lower()
    return antigo or None


# ---- Shopee -------------------------------------------------------------------


def _canais_do_pedido(pedido: Mapping[str, Any]) -> list[str]:
    """Os canais do pedido Shopee, sem repetir: o `logistics_channel_id` já
    resumido (get_order_status_map) e o de cada pacote (`package_list`)."""
    canais: list[str] = []

    def _add(v: Any) -> None:
        s = str(v if v is not None else "").strip()
        if s and s != "0" and s not in canais:
            canais.append(s)

    _add(pedido.get("logistics_channel_id"))
    for pacote in pedido.get("package_list") or []:
        if isinstance(pacote, Mapping):
            _add(pacote.get("logistics_channel_id"))
    return canais


def _transportadora(pedido: Mapping[str, Any]) -> str:
    return " ".join(str(pedido.get("shipping_carrier") or "").split())


def tipo_envio_shopee(pedido: Mapping[str, Any] | None) -> str | None:
    """ "90022 · Shopee Entrega Direta" (canal · transportadora), só o que vier.
    Vários pacotes em canais diferentes: os canais separados por vírgula."""
    if not isinstance(pedido, Mapping):
        return None
    partes = [",".join(_canais_do_pedido(pedido)), _transportadora(pedido)]
    return " · ".join(p for p in partes if p) or None


# ---- as duas ------------------------------------------------------------------


def tipo_envio(plataforma: Any, dados: Mapping[str, Any] | None) -> str | None:
    """O tipo de envio cru, como a plataforma informou (None = não informou)."""
    p = plataforma_flex(plataforma)
    if p == PLATAFORMA_ML:
        return tipo_envio_ml(dados)
    if p == PLATAFORMA_SHOPEE:
        return tipo_envio_shopee(dados)
    return None


def eh_envio_flex(
    plataforma: Any,
    dados: Mapping[str, Any] | None,
    *,
    canais_shopee: Collection[str] | None = None,
) -> bool | None:
    """O envio é Flex? True/False quando a plataforma disse; None quando não.

    `dados`: no ML, o ENVIO (`/shipments/{id}`, formato novo ou antigo); na
    Shopee, o PEDIDO (`get_order_detail` com `package_list,shipping_carrier`,
    ou a entrada do `get_order_status_map`). Plataforma que não tem Flex
    (Amazon, TikTok…) devolve None."""
    p = plataforma_flex(plataforma)
    if not isinstance(dados, Mapping):
        return None
    if p == PLATAFORMA_ML:
        tipo = tipo_envio_ml(dados)
        return None if tipo is None else tipo == ML_TIPO_FLEX
    if p == PLATAFORMA_SHOPEE:
        canais = _canais_do_pedido(dados)
        transportadora = _transportadora(dados)
        if not canais and not transportadora:
            return None
        flex = canais_shopee_flex() if canais_shopee is None else canais_shopee
        alvo = {str(c).strip() for c in flex}
        return bool(alvo.intersection(canais)) or (
            transportadora.lower() == SHOPEE_TRANSPORTADORA_FLEX
        )
    return None


def campos_envio(
    plataforma: Any,
    dados: Mapping[str, Any] | None,
    *,
    canais_shopee: Collection[str] | None = None,
) -> dict[str, Any]:
    """`{"envio_tipo", "envio_flex"}` para gravar na Logística, ou `{}` quando
    a plataforma não disse o tipo — quem grava só grava o que foi dito."""
    flex = eh_envio_flex(plataforma, dados, canais_shopee=canais_shopee)
    if flex is None:
        return {}
    return {"envio_tipo": tipo_envio(plataforma, dados), "envio_flex": flex}


def aplicar_na_linha(row: Logistica, campos: Mapping[str, Any]) -> None:
    """Grava o tipo de envio na linha da Logística (só quando foi lido)."""
    if not campos or campos.get("envio_flex") is None:
        return
    row.envio_tipo = campos.get("envio_tipo")
    row.envio_flex = bool(campos["envio_flex"])


# ---- registro (marketplace_shipment_check) -------------------------------------


@dataclass(frozen=True)
class EnvioLido:
    """O tipo de envio de UM pedido, lido pelo shipment check."""

    bling_id: int
    plataforma: str  # 'ml' | 'shopee'
    integration_id: UUID | None
    numero: str | None  # número do pedido no Bling (= logistica.pedido_bling)
    numeroloja: str | None
    envio_tipo: str | None
    envio_flex: bool
    prazo: datetime | None = None


def item_no_sp(codigo: str | None) -> bool:
    """O item já sai do lote .sp? Todos os pedaços com lote são `sp` (kit
    `dg053.sp+a001` também conta; pedaço sem lote não decide)."""
    pedacos = [p.strip() for p in (codigo or "").lower().split("+") if p.strip()]
    lotes = {lote_de(p) for p in pedacos}
    lotes.discard(None)
    return lotes == {"sp"}


async def _no_sp_por_pedido(session: AsyncSession, bling_ids: Collection[int]) -> dict[int, bool]:
    """bling_id → todos os itens do pedido já estão no .sp (espelho do Bling)."""
    if not bling_ids:
        return {}
    itens: dict[int, list[str | None]] = defaultdict(list)
    rows = await session.execute(
        select(BlingOrder.bling_id, BlingOrder.item_codigo).where(
            BlingOrder.bling_id.in_(list(bling_ids))
        )
    )
    for bid, codigo in rows.all():
        if bid is not None:
            itens[int(bid)].append(codigo)
    return {bid: bool(cods) and all(item_no_sp(c) for c in cods) for bid, cods in itens.items()}


async def registrar_envios(session: AsyncSession, lidos: Iterable[EnvioLido]) -> dict[str, int]:
    """Grava o que o shipment check leu: o pedido Flex em `flex_pedido`
    (upsert por bling_id) e o tipo de envio na linha da Logística que já
    existir (a aba Flex mostra o pedido sem esperar o enriquecimento).

    Só regrava quando algo mudou — o shipment check roda de minuto em minuto
    sobre os mesmos pedidos em aberto. Não faz commit: quem chama decide."""
    lidos = list(lidos)
    out = {"flex_pedidos": 0, "logistica": 0}
    if not lidos:
        return out

    flex = {e.bling_id: e for e in lidos if e.envio_flex}
    if flex:
        no_sp = await _no_sp_por_pedido(session, flex.keys())
        valores = [
            {
                "bling_id": e.bling_id,
                "plataforma": e.plataforma,
                "integration_id": e.integration_id,
                "numeroloja": e.numeroloja,
                "envio_tipo": e.envio_tipo,
                "prazo": e.prazo,
                "no_sp": no_sp.get(e.bling_id, False),
            }
            for e in flex.values()
        ]
        stmt = pg_insert(FlexPedido).values(valores)
        novo = stmt.excluded
        # Leitura que veio sem um campo (ex.: o ML só lê o prazo uma vez) não
        # apaga o que já estava gravado.
        mudar = {
            "plataforma": novo.plataforma,
            "integration_id": func.coalesce(novo.integration_id, FlexPedido.integration_id),
            "numeroloja": func.coalesce(novo.numeroloja, FlexPedido.numeroloja),
            "envio_tipo": func.coalesce(novo.envio_tipo, FlexPedido.envio_tipo),
            "prazo": func.coalesce(novo.prazo, FlexPedido.prazo),
            "no_sp": novo.no_sp,
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=[FlexPedido.bling_id],
            set_={**mudar, "atualizado_em": func.now()},
            where=or_(
                *(getattr(FlexPedido, col).is_distinct_from(expr) for col, expr in mudar.items())
            ),
        )
        res = await session.execute(stmt)
        out["flex_pedidos"] = res.rowcount or 0

    # Linha da Logística que já existe: grava o tipo (as novas recebem no
    # enriquecimento da importação de hora em hora). Um UPDATE por tipo de
    # envio (são poucos: self_service, cross_docking, o canal da Shopee…),
    # não um por pedido.
    grupos: dict[tuple[str, bool, str | None], list[str]] = defaultdict(list)
    for e in lidos:
        if e.numero:
            grupos[(e.plataforma, e.envio_flex, e.envio_tipo)].append(str(e.numero))
    for (plataforma, envio_flex, envio_tipo), numeros in grupos.items():
        rotulos = _ROTULOS_ML if plataforma == PLATAFORMA_ML else _ROTULOS_SHOPEE
        res = await session.execute(
            update(Logistica)
            .where(Logistica.pedido_bling.in_(numeros))
            .where(func.lower(func.trim(Logistica.plataforma)).in_(tuple(rotulos)))
            .where(
                or_(
                    Logistica.envio_flex.is_distinct_from(envio_flex),
                    Logistica.envio_tipo.is_distinct_from(envio_tipo),
                )
            )
            .values(envio_flex=envio_flex, envio_tipo=envio_tipo)
            # Sessão própria do registro: nada carregado para sincronizar.
            .execution_options(synchronize_session=False)
        )
        out["logistica"] += res.rowcount or 0
    return out
