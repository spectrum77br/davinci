"""Leitores do preço VIVO dos anúncios, para a Conferência de preços da Tabela
de Preços (02/11/2026, tarefa "Melhorias na Tabela de Preços" do Eduardo: botão
só de administrador com "% margem de erro" que marca (!) na célula cujo anúncio
está fora da margem).

Um leitor por plataforma (`conferencia_ml`, `conferencia_amazon`,
`conferencia_shopee`, `conferencia_tiktok`), todos com a mesma assinatura:
recebe o cliente da integração (já com a renovação de token segura,
`atendimento.clientes.cliente_da_integracao`) e os vínculos a ler, e devolve
uma `LeituraAnuncio` por vínculo (chave = `ProductLink.id`).

Regra de ouro: `preco` é o MESMO conceito de preço que o envio da Tabela grava
naquela plataforma (services/pricing/push.py + o set de preço do cliente). Na
dúvida, `preco=None` com `motivo` — melhor "não conferido" do que um (!) falso.
O leitor nunca levanta por causa de UM anúncio: falha pontual vira
`preco=None, motivo=...`. Falha da conta inteira (401/403, token recusado)
levanta, e a conferência marca as células daquela conta como "erro na conta".
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.models import ProductLink


@dataclass(frozen=True)
class LeituraAnuncio:
    # Preço comparável com a Tabela (o que o envio grava). None = não lido.
    preco: Decimal | None
    # Preço que o comprador vê quando há promoção por cima (informativo).
    preco_promocional: Decimal | None = None
    # Anúncio ativo no marketplace (None = não informado).
    ativo: bool | None = None
    # Por que não leu, ou observação curta (ex.: "em promoção").
    motivo: str | None = None


Leitor = Callable[[Any, list[ProductLink]], Awaitable[dict[UUID, LeituraAnuncio]]]

LEITORES: dict[str, Leitor] = {}


def registrar(plataforma: str) -> Callable[[Leitor], Leitor]:
    """Decorador: registra o leitor da plataforma ('ml', 'amazon', 'shopee',
    'tiktok' — o valor de IntegrationPlatform)."""

    def _registrar(fn: Leitor) -> Leitor:
        LEITORES[plataforma] = fn
        return fn

    return _registrar


def dinheiro(valor: Any) -> Decimal | None:
    """Número do marketplace → Decimal com 2 casas; None/vazio/inválido → None."""
    if valor is None or valor == "":
        return None
    try:
        d = Decimal(str(valor))
    except Exception:  # noqa: BLE001
        return None
    if not d.is_finite():
        return None
    return d.quantize(Decimal("0.01"))
