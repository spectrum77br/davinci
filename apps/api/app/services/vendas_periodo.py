"""Vendas por SKU nos últimos N dias — fonte única da Tabela de Preços
(colunas 7d/30d, `/api/pricing/sales-map`) e da Importação (coluna "30d" e
consumo da reposição).

Eduardo (02/11/2026): a coluna "30d" mostra as vendas dos últimos 30 dias de
cada produto e vira a base da Reposição. O que conta como venda:

  * Situação fora de `SITUACOES_FORA_DAS_VENDAS`. `bling_orders.situacao`
    guarda o NÚMERO da situação ('12' = Cancelado) ou 'excluido' (pedido
    apagado no Bling, services/bling_orders.py); os nomes ficam na lista por
    segurança. O filtro antigo só tinha os nomes: contava cancelado como
    venda e jogava fora a situação NULL — que conta.
  * Pedido de loja interna NÃO é venda. As transferências entre lotes saem
    pelas lojas `store_info.platform = 'interno'` ("Centro de Distribuição",
    "Devolução/Balanço") com quantidades positivas e negativas. Pedido sem
    loja conta.
  * Só quantidade > 0.
  * Kit vendido como `dg053.sp+a001.sp`: cada peça leva a quantidade do kit
    (o fone a001 sai em ~1.000 kits por mês e aparecia com quase zero, porque
    a regra da base do celular só olhava a 1ª peça). A chave composta não
    fica no resultado — senão a base do celular (dg053) contaria duas vezes.
  * Kit do Bling cujo SKU é só a base das peças — o "Kit 6 Malas" b047 =
    b047.8 + b047.12 ×2 + b047.18 + b047.20 + b047.24, pela composição que o
    Bling usa para baixar o estoque (`bling_kit_components`): cada peça leva
    a quantidade do kit × a quantidade dela no kit. O SKU não diz as peças,
    então a regra dos tamanhos da mala (audit.match_one_sku_to_keys) não o
    via: 151 kits por mês fora da Reposição das malas avulsas. Aqui a chave
    do kit FICA — a linha do próprio kit na Tabela mostra as vendas dele (o
    celular não tem kit assim; a base dele contaria duas vezes).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import structlog
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models import BlingKitComponent, BlingOrder, Product, StoreInfo
from app.services.bling_situacoes import SITUACAO_CANCELADO

logger = structlog.get_logger()

# Cancelado (número e nome), excluído no Bling e Devolvido.
SITUACOES_FORA_DAS_VENDAS: tuple[str, ...] = (
    str(SITUACAO_CANCELADO),
    "excluido",
    "Cancelado",
    "Devolvido",
)
# Plataforma das lojas de transferência entre lotes (store_info.platform).
PLATAFORMA_INTERNA = "interno"
# Separador das peças de um kit no SKU (`dg053.sp+a001.sp`).
SEPARADOR_KIT = "+"
# products.formato do kit (composição) no Bling.
FORMATO_COMPOSICAO = "E"
# Peça de um kit de base: "base.número" (b047.12).
_PECA_DO_KIT_DE_BASE = re.compile(r"^(?P<base>[^.+]+)\.\d+$")


def pecas_do_codigo(codigo: str | None) -> list[str]:
    """Peças de um item vendido, minúsculas e sem vazias.

    `dg053.sp+a001.sp` → [dg053.sp, a001.sp]; SKU simples → [ele mesmo]."""
    return [
        peca.strip().lower()
        for peca in (codigo or "").split(SEPARADOR_KIT)
        if peca.strip()
    ]


def kits_de_base(
    composicao: Iterable[tuple[str | None, str | None, Decimal | int | None]],
) -> dict[str, dict[str, int]]:
    """{kit: {peça: quantidade no kit}} a partir de (SKU do kit, SKU da peça,
    quantidade) da composição do Bling.

    Só o kit cujo SKU não tem '.' nem '+' e cujas peças são TODAS
    "kit.número" (b047 → b047.12), com quantidade inteira e positiva; um par
    fora disso descarta o kit inteiro. Peça repetida (o mesmo produto em duas
    fichas) conta uma vez."""
    kits: dict[str, dict[str, int]] = {}
    descartados: set[str] = set()
    for sku_kit, sku_peca, quantidade in composicao:
        kit = (sku_kit or "").strip().lower()
        if not kit or "." in kit or SEPARADOR_KIT in kit:
            continue
        peca = (sku_peca or "").strip().lower()
        casou = _PECA_DO_KIT_DE_BASE.match(peca)
        qtd = Decimal(quantidade or 0)
        if casou is None or casou["base"] != kit or qtd <= 0 or qtd != qtd.to_integral_value():
            descartados.add(kit)
            continue
        kits.setdefault(kit, {})[peca] = int(qtd)
    return {kit: pecas for kit, pecas in kits.items() if kit not in descartados}


async def pecas_dos_kits_de_base(session: AsyncSession) -> dict[str, dict[str, int]]:
    """Os kits de base (`kits_de_base`) da composição cacheada do Bling
    (`bling_kit_components`, regravada todo domingo)."""
    kit = aliased(Product)
    peca = aliased(Product)
    composicao = (
        await session.execute(
            select(kit.sku, peca.sku, BlingKitComponent.quantidade)
            .join(kit, kit.bling_product_id == BlingKitComponent.kit_bling_product_id)
            .join(peca, peca.bling_product_id == BlingKitComponent.component_bling_product_id)
            .where(kit.formato == FORMATO_COMPOSICAO)
        )
    ).all()
    return kits_de_base(composicao)


async def vendas_por_chave(
    session: AsyncSession, janelas_dias: Sequence[int]
) -> dict[str, list[int]]:
    """{sku em minúsculas: [vendas nos últimos janela_1 dias, janela_2, ...]}.

    Cada janela conta a partir de agora (são acumuladas: a de 60 dias contém
    a de 30) e a lista sai na ordem de `janelas_dias`. As vendas vêm numa
    consulta só, agrupada pelo SKU; o kit é repartido entre as peças depois,
    em Python (o '+' pelo SKU, o kit de base pela composição do Bling, que é
    a 2ª consulta).
    """
    janelas = [int(dias) for dias in janelas_dias]
    if not janelas:
        return {}
    if min(janelas) <= 0:
        raise ValueError(f"janela de vendas precisa ser positiva: {janelas}")

    agora = datetime.now(UTC)
    chave = func.lower(func.trim(BlingOrder.item_codigo))
    loja_interna = (
        select(StoreInfo.id)
        .where(
            StoreInfo.bling_store_id == BlingOrder.loja,
            func.lower(func.trim(StoreInfo.platform)) == PLATAFORMA_INTERNA,
        )
        .exists()
    )
    somas = [
        func.coalesce(
            func.sum(
                case(
                    (BlingOrder.data >= agora - timedelta(days=dias), BlingOrder.item_quantidade),
                    else_=0,
                )
            ),
            0,
        )
        for dias in janelas
    ]
    linhas = (
        await session.execute(
            select(chave, *somas)
            .where(
                BlingOrder.item_codigo.isnot(None),
                func.trim(BlingOrder.item_codigo) != "",
                BlingOrder.item_quantidade > 0,
                func.coalesce(BlingOrder.situacao, "").notin_(SITUACOES_FORA_DAS_VENDAS),
                ~loja_interna,
                BlingOrder.data >= agora - timedelta(days=max(janelas)),
            )
            .group_by(chave)
        )
    ).all()

    vendas: dict[str, list[int]] = {}
    for codigo, *por_janela in linhas:
        for peca in pecas_do_codigo(codigo):
            acumulado = vendas.setdefault(peca, [0] * len(janelas))
            for i, quantidade in enumerate(por_janela):
                acumulado[i] += int(quantidade or 0)

    kits = await pecas_dos_kits_de_base(session)
    for kit, pecas in kits.items():
        do_kit = vendas.get(kit)
        if not do_kit:
            continue
        for peca, no_kit in pecas.items():
            acumulado = vendas.setdefault(peca, [0] * len(janelas))
            for i, quantidade in enumerate(do_kit):
                acumulado[i] += quantidade * no_kit
    await _avisar_kits_sem_composicao(session, vendas, kits)
    return vendas


async def _avisar_kits_sem_composicao(
    session: AsyncSession, vendas: dict[str, list[int]], kits: dict[str, dict[str, int]]
) -> None:
    """Kit do Bling (formato 'E') de SKU sem ponto que vendeu e não está em
    `kits`: a composição não chegou ao cache (kit novo — o cache é regravado
    aos domingos) ou tem peça fora de "kit.número". As vendas dele não chegam
    às peças: só o aviso no log."""
    candidatos = [
        chave
        for chave, por_janela in vendas.items()
        if "." not in chave and chave not in kits and any(por_janela)
    ]
    if not candidatos:
        return
    sem_composicao = (
        await session.execute(
            select(func.lower(func.trim(Product.sku)))
            .where(
                func.lower(func.trim(Product.sku)).in_(candidatos),
                Product.formato == FORMATO_COMPOSICAO,
            )
            .distinct()
        )
    ).scalars().all()
    if sem_composicao:
        logger.warning("vendas_periodo.kit_sem_composicao", kits=sorted(sem_composicao))
