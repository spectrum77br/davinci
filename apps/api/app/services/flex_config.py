"""Leitura das chaves `flex_*` do .env (app/config.py), já normalizadas.

As chaves ficam em texto (como `estoque_familia_prefixos`) para um erro de
digitação no .env não derrubar a api na subida: aqui o valor errado vira o
lado SEGURO — modo desconhecido é "desligado", id de conta inválido é
ignorado (e a lista vazia é "nenhuma conta").
"""

from __future__ import annotations

from uuid import UUID

import structlog

from app.config import get_settings
from app.models.flex import FLEX_MODOS

logger = structlog.get_logger()

MODO_DESLIGADO = "desligado"
MODO_OBSERVAR = "observar"
# Modos em que o DaVinci pode ESCREVER na plataforma (só nas contas
# permitidas, respeitando o teto por rodada).
MODOS_QUE_ESCREVEM = frozenset({"piloto", "ativo"})


def modo(valor: str | None = None) -> str:
    """`flex_modo` normalizado; desconhecido vale "desligado"."""
    bruto = get_settings().flex_modo if valor is None else valor
    m = (bruto or "").strip().lower()
    if m in FLEX_MODOS:
        return m
    logger.warning("flex_modo_desconhecido", valor=str(bruto)[:40])
    return MODO_DESLIGADO


def contas(valor: str | None = None) -> frozenset[UUID]:
    """`flex_contas`: integration_id permitidos. Vazio = nenhuma conta."""
    bruto = get_settings().flex_contas if valor is None else valor
    out: set[UUID] = set()
    for pedaco in (bruto or "").split(","):
        p = pedaco.strip()
        if not p:
            continue
        try:
            out.add(UUID(p))
        except ValueError:
            logger.warning("flex_conta_invalida", valor=p[:60])
    return frozenset(out)


def pode_escrever(modo_atual: str | None = None) -> bool:
    """O modo deixa escrever na plataforma? (observar/desligado: nunca)."""
    return modo(modo_atual) in MODOS_QUE_ESCREVEM
