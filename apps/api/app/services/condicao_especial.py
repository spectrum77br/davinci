"""Condição Especial dos segmentos — limpeza das encerradas.

Pedido de 10/09/2026: "quando acabar essa data pode excluir essa condição
especial, não precisa deixar lá parada no painel". A regra vale pela DATA DO
PEDIDO (não pelo dia de hoje), então um pedido feito dentro do período ainda
pode estar em triagem depois do fim — a aba Pendentes da Margem mostra até 30
dias. Por isso a limpeza é em duas etapas:

1. o painel de Segmentos ESCONDE a condição no dia seguinte ao fim do período
   (front: `condicaoVigente`);
2. aqui ela é apagada de vez CARENCIA_DIAS depois do fim — quando nenhum
   pedido do período pode mais estar visível na Margem.

Condições sem período (só nome/SKU) nunca expiram. Chamado pelo cron diário
`condicao_especial_gc` (worker, 03:10 BRT).
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SegmentSpecialDate

# Mesma janela da aba Pendentes da Margem (routers/margens._TRIAGEM_DIAS).
CARENCIA_DIAS = 30


async def limpar_encerradas(session: AsyncSession, *, hoje_sp: date) -> int:
    """Apaga condições cujo período terminou há mais de CARENCIA_DIAS (contado
    em dias de São Paulo). Devolve quantas saíram. NÃO commita."""
    limite = hoje_sp - timedelta(days=CARENCIA_DIAS)
    result = await session.execute(
        delete(SegmentSpecialDate).where(
            SegmentSpecialDate.date_end.is_not(None),
            SegmentSpecialDate.date_end < limite,
        )
    )
    return int(result.rowcount or 0)
