"""Contrato compartilhado da DEVOLUÇÃO de um pedido no marketplace.

Eduardo (03/09): "o TikTok não está pegando o número de rastreio correto"
/ "esse rastreio está incorreto, precisa sempre estar atualizadinho" — a aba
Acompanhamento mostrava o rastreio da ENTREGA original; o pacote que interessa
ali é o que VOLTA (a devolução tem transportadora e código próprios: no TikTok
`return_tracking_number`, na Shopee `tracking_number` do return, no ML o
shipment do return do claim).

Cada marketplace expõe `returns_por_pedido(session, linhas)` no seu módulo de
logística (logistica_tiktok / logistica_shopee / logistica_meli) devolvendo
`{pedido_bling: ReturnInfo}` para as linhas da Logística recebidas (só as da
própria plataforma; pedido sem devolução conhecida fica de fora do dict).
`services/devolucao_rastreio_sync.run` junta tudo, grava em
`devolucao_rastreio.*_auto` e registra os códigos Correios no 17track.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, NamedTuple


class ReturnInfo(NamedTuple):
    fonte: str  # "tiktok" | "shopee" | "ml"
    # Status CRU da devolução no marketplace (ex.: BUYER_SHIPPED_ITEM,
    # PROCESSING, ready_to_ship). Tradução em PT: logistica_rules.devolucao_status_pt.
    status: str | None
    # Código de rastreio do pacote DE VOLTA. None = ainda não postado, só
    # reembolso (sem envio) ou o marketplace não informa.
    tracking: str | None
    # Transportadora do retorno quando o marketplace informa (ex.: "Correios").
    carrier: str | None
    created_at: datetime | None  # quando a devolução foi ABERTA (UTC)
    updated_at: datetime | None  # última mexida na devolução (UTC)
    return_id: str | None = None  # id do caso (return_id / return_sn / claim_id)


def epoch_to_dt(v: Any) -> datetime | None:
    """Epoch em segundos (ou milissegundos) → datetime UTC; None se ilegível."""
    if v in (None, "", 0, "0"):
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if n > 1e11:  # milissegundos
        n /= 1000.0
    try:
        return datetime.fromtimestamp(n, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def iso_to_dt(v: Any) -> datetime | None:
    """String ISO-8601 (com ou sem 'Z'/offset) → datetime UTC; None se ilegível."""
    if not v or not isinstance(v, str):
        return None
    s = v.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)
