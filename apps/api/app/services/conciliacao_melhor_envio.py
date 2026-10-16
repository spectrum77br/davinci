"""Conciliação — frete pago no Melhor Envio (envio próprio da Amazon), 15/10/2026.

O Melhor Envio lista os envios da conta (`GET /api/v2/me/orders`, 10 por página, do
mais novo pro mais velho) com as etiquetas que o Bling grava: `mi:reference_code` =
nº do pedido no Bling, `mi:marketplace_code` = id do pedido na Amazon. Frete pago =
preço − desconto + conferência de peso cobrada a mais.

Regra de 16/10 (Vinicius, revisão): só etiqueta PAGA conta. Etiqueta no carrinho
(`pending`) não entra; a que sumiu da listagem, voltou pro carrinho ou foi cancelada
sai de `conciliacao_fretes_pagos` (não existe marca manual nessa tabela). Leitura
cortada no limite de páginas avisa no resumo e em `loja.ultimo_erro` e não apaga nada.
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


# Status do envio no Melhor Envio (sonda de 15/10 + revisão de 16/10): "pending" =
# etiqueta no carrinho, ainda não paga; "released" = paga, aguardando postagem;
# "posted" / "delivered" / "undelivered" vêm depois da postagem; "canceled" devolve o
# valor pra carteira. Só o que já foi pago é frete pago.
STATUS_PAGO = ("released", "paid", "posted", "delivered", "undelivered")
STATUS_NAO_PAGO = ("pending", "canceled")


def etiqueta_paga(envio: dict[str, Any]) -> bool:
    status = str(envio.get("status") or "").lower()
    if status in STATUS_NAO_PAGO or _quando(envio.get("canceled_at")):
        return False
    return status in STATUS_PAGO or _quando(envio.get("paid_at")) is not None


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
    paga = etiqueta_paga(envio)
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
        # no carrinho (pending) ainda não saiu dinheiro; cancelado volta pra carteira
        "frete_pago": (preco - desconto + conferencia) if paga else ZERO,
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
    janela_dias: int = 60,
    ler: Any = None,
) -> dict[str, Any]:
    """Lê os envios do Melhor Envio (do mais novo pro mais velho) até passar do início
    da loja e guarda os que casam com um pedido desta loja e estão PAGOS. `ler` =
    função `(token, page=) -> (http, corpo)` (padrão: a API).

    16/10, revisão: etiqueta no carrinho não entra; a que foi vista como não paga,
    cancelada ou que sumiu do trecho lido sai da tabela (`removidos`). Leitura cortada
    em `max_paginas` → `cortado` no resumo + aviso em `loja.ultimo_erro`, sem apagar."""
    from app.services.melhor_envio import ler_envios, token_do_painel

    agora = agora or datetime.now(UTC)
    ler = ler or ler_envios
    token, _ = await token_do_painel(session)
    resumo: dict[str, Any] = {
        "paginas": 0,
        "vistos": 0,
        "casados": 0,
        "nao_pagos": 0,
        "novos": 0,
        "atualizados": 0,
        "removidos": 0,
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
    # janela móvel (2ª rodada): ler sempre até inicio − 7 estourava as 40 páginas com o
    # tempo e o aviso de "cortada" virava permanente; 60 dias cobrem qualquer etiqueta viva
    piso = max(
        loja.inicio - timedelta(days=7), datetime.now(BRT).date() - timedelta(days=janela_dias)
    )
    limite = datetime.combine(piso, datetime.min.time(), tzinfo=BRT)
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
    vistos: set[str] = set()
    mais_antigo: datetime | None = None  # o envio mais velho que esta leitura alcançou
    fim_da_lista = False  # a conta inteira foi vista
    passou_do_inicio = False  # parou porque já estava antes do início da loja
    for page in range(1, max_paginas + 1):
        http, corpo = await ler(token, page=page)
        if http != 200:
            resumo["erro"] = f"HTTP {http} na página {page}"
            break
        dados = corpo.get("data") if isinstance(corpo.get("data"), list) else []
        if not dados:
            fim_da_lista = True
            break
        resumo["paginas"] += 1
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
            vistos.add(f["envio_id"])
            # casa pelo nº do Bling; se a etiqueta também traz o id da Amazon e ele
            # aponta pra outro pedido, vale o id da Amazon (é único; o nº do Bling não)
            ped = pedidos.get(f["pedido_bling"] or "")
            mkt = f["pedido_marketplace"]
            if ped is not None and mkt and ped.numeroloja and ped.numeroloja != mkt:
                ped = None
            if ped is None and mkt:
                ped = por_marketplace.get(mkt)
            if ped is None:
                continue
            atual = existentes.get(f["envio_id"])
            if not etiqueta_paga(envio):
                resumo["nao_pagos"] += 1
                if atual is not None:  # voltou pro carrinho ou foi cancelada: sai
                    await session.delete(atual)
                    del existentes[f["envio_id"]]
                    resumo["removidos"] += 1
                continue
            f["pedido_bling"] = ped.pedido_bling
            f["pedido_marketplace"] = mkt or ped.numeroloja
            resumo["casados"] += 1
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
            passou_do_inicio = True
            break
    if "erro" not in resumo and not (fim_da_lista or passou_do_inicio):
        # bateu no teto de páginas com envio ainda por ler: avisa, não corta em silêncio
        aviso = f"melhor envio: leitura cortada em {max_paginas} páginas"
        resumo["cortado"] = True
        resumo["aviso"] = aviso
        loja.ultimo_erro = aviso
        logger.warning("conciliacao_melhor_envio_cortado", loja=loja.conta, paginas=max_paginas)
    elif "erro" not in resumo:
        # etiqueta que sumiu do trecho lido (tirada do carrinho, apagada): sai. Só dentro
        # do que esta leitura cobriu — mais velho que `mais_antigo` não foi visto.
        for envio_id, atual in list(existentes.items()):
            if envio_id in vistos:
                continue
            criado = _quando((atual.raw or {}).get("created_at")) or atual.pago_em
            coberto = fim_da_lista or (
                criado is not None and mais_antigo is not None and criado >= mais_antigo
            )
            if coberto:
                await session.delete(atual)
                del existentes[envio_id]
                resumo["removidos"] += 1
    await session.flush()
    logger.info(
        "conciliacao_melhor_envio_sync",
        loja=loja.conta,
        **{k: v for k, v in resumo.items() if k != "erro"},
    )
    return resumo


async def fretes_por_pedido(session: AsyncSession, loja: ConciliacaoLoja) -> dict[str, Decimal]:
    """nº do pedido → frete pago (soma das etiquetas pagas; cancelada não conta)."""
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
