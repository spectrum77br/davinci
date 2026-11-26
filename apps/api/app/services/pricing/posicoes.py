"""Quantas categorias de venda cabem numa tabela de Contas (M-93, Harry 26/11/2026).

A categoria de venda (segmento filho de Celular, Eletro ou Mala) liga o produto à margem e ao
frete da conta pela POSIÇÃO: a categoria de sort_order N-1 usa margin{N} / shipping{N} e o
vínculo slot{N}_segment_id de cada linha de `pricing_accounts` (kit, as duas linhas do par do
ML e a coluna de catálogo, cujas margens próprias também são margin{N}). Ver
services/categorias_venda.py.

Até a M-93 eram 5 posições; a migration 0520 criou as posições 6 a 10 (todas vazias). Este é o
ÚNICO lugar com o número: todo laço, lista de campos ou trava por posição no servidor sai
daqui. Módulo sem dependências (o cálculo do preço, o modelo e o Histórico o importam).

Trocar o número exige uma migration com as colunas novas, como a 0520.
"""

from __future__ import annotations

# Máximo de categorias de venda por centro de custo (contando as inativas) = quantos pares
# margem/frete (e vínculos) cada linha de `pricing_accounts` tem.
MAX_CATEGORIAS_VENDA = 10

# As posições, 1..MAX (a categoria de sort_order 0 está na posição 1).
POSICOES: tuple[int, ...] = tuple(range(1, MAX_CATEGORIAS_VENDA + 1))

CAMPOS_MARGEM: tuple[str, ...] = tuple(f"margin{n}" for n in POSICOES)
CAMPOS_FRETE: tuple[str, ...] = tuple(f"shipping{n}" for n in POSICOES)
CAMPOS_SLOT: tuple[str, ...] = tuple(f"slot{n}_segment_id" for n in POSICOES)
# margin1, shipping1, margin2, shipping2… (a ordem de sempre das listas do router).
CAMPOS_MARGEM_FRETE: tuple[str, ...] = tuple(
    campo for par in zip(CAMPOS_MARGEM, CAMPOS_FRETE, strict=True) for campo in par
)

# Até a M-93 o cálculo prendia o "tipo" em 1..5. Só o caminho antigo, sem a categoria do
# produto resolvida (cai no kit da conta, 1..8 — ex.: services/audit/runner), continua preso
# em 5: assim nenhum preço já calculado muda por causa da M-93.
POSICOES_ANTES_DA_M93 = 5
