"""Conciliação — frete pago no Melhor Envio (envio próprio da Amazon), 15/10/2026.

O Melhor Envio lista os envios da conta (`GET /api/v2/me/orders`, 10 por página, do
mais novo pro mais velho) com as etiquetas que o Bling grava: `mi:reference_code` =
nº do pedido no Bling, `mi:marketplace_code` = id do pedido na Amazon. Frete pago =
preço − desconto + conferência de peso cobrada a mais. Envio cancelado não conta.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConciliacaoFretePago, ConciliacaoLoja, ConciliacaoPedido

logger = structlog.get_logger()
BRT = ZoneInfo("America/Sao_Paulo")
FONTE = "melhor_envio"
ZERO = Decimal("0")


def _d(v: Any) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _quando(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=BRT)


def etiquetas(envio: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for t in envio.get("tags") or []:
        if isinstance(t, dict) and t.get("tag"):
            out[str(t["tag"])] = str(t.get("url") or "").strip()
    return out


def conferencia_cobrada(envio: dict[str, Any]) -> Decimal:
    """Conferência de peso: o que o Melhor Envio cobrou A MAIS (`type` debit). Crédito
    não devolve dinheiro (06/10), então conta zero."""
    conc = envio.get("conciliation")
    itens = conc if isinstance(conc, list) else ([conc] if isinstance(conc, dict) else [])
    total = ZERO
    for c in itens:
        if not isinstance(c, dict):
            continue
        tipo = str(c.get("type") or "").lower()
        valor = _d(c.get("value")) or ZERO
        if tipo == "debit" or (not tipo and valor > ZERO):
            total += valor.copy_abs()
    return total


def envio_para_frete(envio: dict[str, Any]) -> dict[str, Any] | None:
    eid = envio.get("id")
    if not eid:
        return None
    tags = etiquetas(envio)
    preco = _d(envio.get("price")) or ZERO
    desconto = _d(envio.get("discount")) or ZERO
    conferencia = conferencia_cobrada(envio)
    servico = envio.get("service") if isinstance(envio.get("service"), dict) else {}
    cancelado_em = _quando(envio.get("canceled_at"))
    status = str(envio.get("status") or "")
    cancelado = bool(cancelado_em) or status == "canceled"
    return {
        "fonte": FONTE,
        "envio_id": str(eid),
        "pedido_bling": tags.get("mi:reference_code") or None,
        "pedido_marketplace": tags.get("mi:marketplace_code") or None,
        "protocolo": envio.get("protocol"),
        "status": status or None,
        "servico": servico.get("name"),
        "rastreio": envio.get("tracking") or envio.get("self_tracking"),
        "preco": preco,
        "desconto": desconto,
        "conferencia": conferencia,
        # cancelado: o Melhor Envio devolve o valor pra carteira — não é frete pago
        "frete_pago": ZERO if cancelado else (preco - desconto + conferencia),
        "pago_em": _quando(envio.get("paid_at")),
        "postado_em": _quando(envio.get("posted_at")),
        "entregue_em": _quando(envio.get("delivered_at")),
        "cancelado_em": cancelado_em,
        "raw": {
            k: envio.get(k)
            for k in (
                "id",
                "protocol",
                "status",
                "price",
                "discount",
                "insurance_value",
                "billed_weight",
                "conciliation",
                "paid_at",
                "posted_at",
                "delivered_at",
                "canceled_at",
                "created_at",
                "tracking",
                "self_tracking",
                "tags",
                "service",
                "freight_arrangements",
            )
        },
    }


async def run_me_sync(
    session: AsyncSession,
    loja: ConciliacaoLoja,
    *,
    agora: datetime | None = None,
    max_paginas: int = 40,
    ler: Any = None,
) -> dict[str, Any]:
    """Lê os envios do Melhor Envio (do mais novo pro mais velho) até passar do início
    da loja e guarda os que casam com um pedido desta loja. `ler` = função
    `(token, page=) -> (http, corpo)` (padrão: a API)."""
    from app.services.melhor_envio import ler_envios, token_do_painel

    agora = agora or datetime.now(UTC)
    ler = ler or ler_envios
    token, _ = await token_do_painel(session)
    resumo: dict[str, Any] = {
        "paginas": 0,
        "vistos": 0,
        "casados": 0,
        "novos": 0,
        "atualizados": 0,
    }
    if not token:
        resumo["erro"] = "sem token do Melhor Envio no painel"
        return resumo
    pedidos = {
        p.pedido_bling: p
        for p in (
            await session.execute(
                select(ConciliacaoPedido).where(
                    ConciliacaoPedido.integration_id == loja.integration_id
                )
            )
        ).scalars()
    }
    por_marketplace = {p.numeroloja: p for p in pedidos.values() if p.numeroloja}
    limite = datetime.combine(loja.inicio - timedelta(days=7), datetime.min.time(), tzinfo=BRT)
    existentes = {
        f.envio_id: f
        for f in (
            await session.execute(
                select(ConciliacaoFretePago).where(
                    ConciliacaoFretePago.integration_id == loja.integration_id,
                    ConciliacaoFretePago.fonte == FONTE,
                )
            )
        ).scalars()
    }
    for page in range(1, max_paginas + 1):
        http, corpo = await ler(token, page=page)
        if http != 200:
            resumo["erro"] = f"HTTP {http} na página {page}"
            break
        dados = corpo.get("data") if isinstance(corpo.get("data"), list) else []
        if not dados:
            break
        resumo["paginas"] += 1
        mais_antigo: datetime | None = None
        for envio in dados:
            if not isinstance(envio, dict):
                continue
            resumo["vistos"] += 1
            criado = _quando(envio.get("created_at")) or _quando(envio.get("paid_at"))
            if criado and (mais_antigo is None or criado < mais_antigo):
                mais_antigo = criado
            f = envio_para_frete(envio)
            if f is None:
                continue
            ped = pedidos.get(f["pedido_bling"] or "") or por_marketplace.get(
                f["pedido_marketplace"] or ""
            )
            if ped is None:
                continue
            f["pedido_bling"] = ped.pedido_bling
            f["pedido_marketplace"] = f["pedido_marketplace"] or ped.numeroloja
            resumo["casados"] += 1
            atual = existentes.get(f["envio_id"])
            if atual is None:
                atual = ConciliacaoFretePago(integration_id=loja.integration_id, **f)
                session.add(atual)
                existentes[f["envio_id"]] = atual
                resumo["novos"] += 1
            else:
                mudou = False
                for k, v in f.items():
                    if k in ("fonte", "envio_id"):
                        continue
                    if getattr(atual, k) != v:
                        setattr(atual, k, v)
                        mudou = True
                if mudou:
                    resumo["atualizados"] += 1
        if mais_antigo and mais_antigo < limite:
            break
    await session.flush()
    logger.info(
        "conciliacao_melhor_envio_sync",
        loja=loja.conta,
        **{k: v for k, v in resumo.items() if k != "erro"},
    )
    return resumo


async def fretes_por_pedido(session: AsyncSession, loja: ConciliacaoLoja) -> dict[str, Decimal]:
    """nº do pedido → frete pago (soma dos envios não cancelados)."""
    rows = (
        await session.execute(
            select(ConciliacaoFretePago.pedido_bling, ConciliacaoFretePago.frete_pago).where(
                ConciliacaoFretePago.integration_id == loja.integration_id,
                ConciliacaoFretePago.pedido_bling.isnot(None),
                ConciliacaoFretePago.cancelado_em.is_(None),
            )
        )
    ).all()
    out: dict[str, Decimal] = {}
    for numero, valor in rows:
        out[str(numero)] = out.get(str(numero), ZERO) + (valor or ZERO)
    return out
