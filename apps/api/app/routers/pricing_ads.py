"""Ads da aba Contas da Tabela de Preços (M-12, 18/11/2026, migration 0503).

Pedido do dono: a coluna Ads (texto livre "2% - 50 ROAS") vira o botão
"2%  ROAS 50 ▾" com um painel igual ao da Margem (M-2): [x] Geral com o % que
a conta pode gastar em Ads, o ROAS mínimo (100 ÷ %, calculado na tela, não
gravado) e o gasto por dia em R$; "% gasto real" e "ROAS real" da conta nos
últimos 7 dias; e [x] Por produto (um % e um gasto por dia para cada produto
do departamento). Nada disso entra no cálculo de preço. Desde a M-78 as duas
caixas são um ou outro (ver abaixo).

O real vem do módulo Marketing SÓ LENDO (`marketing_accounts` e
`marketing_metrics`; nenhuma coleta nova): a conta da Tabela casa com a conta
de Ads não arquivada da mesma plataforma, do mesmo nome (`chave_nome`: sem
acento, espaço nem pontuação) e do mesmo departamento. Sem conta que case (ou
com mais de uma), ou sem dado na janela: null, e a tela mostra "—" (nunca
estima). % gasto = ACOS = Σgasto ÷ Σfaturamento × 100 (a regra do Marketing);
ROAS = Σfaturamento ÷ Σgasto. Só as linhas diárias (intensity = 0): a Shopee
grava também as de hora em hora, e somar as duas contaria o dia em dobro. O
Marketing não tem dado por produto.

Vê quem vê a aba Contas (`tabela_precos_contas` ver), só as contas que a aba
mostra (a cerca de equipe da grade, `_contas_da_grade`; a coluna de catálogo
não tem Ads próprio); grava quem edita Contas.

  GET /api/pricing/ads?department=celular
  PUT /api/pricing/ads/{account_id}                       {"ads_geral_ligada"?, "pode_gastar_pct"?,
                                                            "gasto_dia"?, "ads_por_produto_ligada"?}
  PUT /api/pricing/ads/{account_id}/produto/{product_id}  {"pode_gastar_pct"?, "gasto_dia"?}

Campo ausente não muda; % e gasto null apagam (o produto sem % nem gasto fica
sem linha).

M-78 (26/11/2026): "Geral" e "Produto" (o antigo "Por produto") são UM OU
OUTRO. Ligar uma caixa desliga a outra; desligar não liga a outra (as duas
desligadas = conta sem Ads); as duas `true` no mesmo PUT = 422
`ads_um_ou_outro`. Com "Geral" vale o % Geral para todos; com "Produto", só o
% próprio de cada produto (sem % próprio = sem Ads; não usa mais o Geral).
Trocar não apaga nada: os valores da outra ficam gravados. Sem linha, ou com as
duas ligadas no banco (antes da M-78), a conta lê como "Geral"
(`caixas_ads`); o PUT de caixa grava sempre as duas, já no um ou outro.

M-53 (25/11/2026): conta ML do par (Clássico + Premium, mesmo `par_id`) grava o
mesmo na irmã, nas duas rotas. M-56: Afiliado % + Desconto % + Ads % da conta
(M-94: Desconto + o maior entre Afiliado e Ads) não passa do Custo total
(`app/services/pricing/custo_total.py`); o PUT que
estoura (inclusive religar uma caixa) é recusado com 422 `custo_total_estourado`
e nada é gravado. Revisão da M-86: com a conta já acima do teto (o Custo total
calculado pela Margem mínima ficou abaixo da soma), o PUT que não aumenta o
maior Ads (baixar o %, desligar a caixa) passa, mesmo sem caber ainda.

M-79 (26/11/2026): o GET manda também a categoria de venda de cada produto (o
`segment_id`, filho do centro de custo da aba) e a lista `categorias` para o
filtro "Categoria de venda" da janela (na ordem das colunas da aba Contas; a
inativa só entra se algum produto da lista é dela). Só leitura; o filtro é da
tela e não grava nada.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import AfterValidator, BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import (
    PricingAccount,
    PricingAdsConta,
    PricingAdsProduto,
    PricingPlatform,
    PricingProduct,
    Segment,
    User,
    UserRole,
)
from app.models.marketing import MarketingAccount, MarketingMetric
from app.routers.pricing import (
    _contas_da_grade,
    _irma_de,
    _recusar_custo_total_estourado,
    _resolve_root_segment_id,
)
from app.schemas.pricing import duas_casas
from app.services.marketing.horarios_contas import chave_nome
from app.services.pricing.anuncios import CANAL_CATALOGO
from app.services.pricing.custo_total import caixas_ads, estado_ads, produtos_do_departamento

logger = structlog.get_logger()
router = APIRouter(prefix="/api/pricing", tags=["pricing"])

# Plataforma da Tabela → a do Marketing (só estas têm Ads lá).
PLATAFORMA_NO_MARKETING = {
    PricingPlatform.ML: "ml",
    PricingPlatform.SHOPEE: "shopee",
    PricingPlatform.AMAZON: "amazon",
}
JANELA_REAL_DIAS = 7

# O % sai com as 2 casas do banco (NUMERIC(5,2)) antes da conferência do Custo
# total: o conferido é o gravado (revisão da M-56).
Pct = Annotated[Decimal | None, Field(ge=0, le=100), AfterValidator(duas_casas)]
Gasto = Annotated[Decimal | None, Field(ge=0, lt=Decimal("10000000000"))]


class AdsValoresOut(BaseModel):
    pode_gastar_pct: float | None = None
    gasto_dia: float | None = None


class AdsRealOut(BaseModel):
    """Últimos 7 dias, do Marketing; null = sem dado (a tela mostra "—")."""

    pct_gasto: float | None = None
    roas: float | None = None


class AdsContaOut(AdsValoresOut):
    ads_geral_ligada: bool
    ads_por_produto_ligada: bool
    # {pricing_product_id: valores próprios}
    produtos: dict[str, AdsValoresOut]
    real: AdsRealOut


class AdsCategoriaOut(BaseModel):
    """Categoria de venda (M-79): o segmento filho do centro de custo da aba."""

    id: UUID
    name: str


class AdsProdutoOut(BaseModel):
    id: UUID
    sku: str
    name: str
    # M-79: a categoria de venda do produto (o `segment_id` dele).
    categoria: AdsCategoriaOut


class AdsOut(BaseModel):
    department: str
    pode_editar: bool
    # Os produtos do departamento (as linhas da grade), por nome.
    produtos: list[AdsProdutoOut]
    # M-79: as opções do filtro "Categoria de venda" da janela, na ordem das
    # colunas da aba Contas (sort_order); a inativa só entra se tem produto.
    categorias: list[AdsCategoriaOut]
    # {pricing_account_id: Ads} — as contas que a aba Contas mostra.
    contas: dict[str, AdsContaOut]


class AdsContaIn(BaseModel):
    ads_geral_ligada: bool | None = None
    pode_gastar_pct: Pct = None
    gasto_dia: Gasto = None
    ads_por_produto_ligada: bool | None = None


class AdsProdutoIn(BaseModel):
    pode_gastar_pct: Pct = None
    gasto_dia: Gasto = None


def _pode_editar(user: User) -> bool:
    if user.role == UserRole.ADMIN:
        return True
    return bool(((user.permissions or {}).get("tabela_precos_contas") or {}).get("edit"))


async def _real_das_contas(
    session: AsyncSession, contas: Sequence[PricingAccount], department: str
) -> dict[UUID, AdsRealOut]:
    """O real de cada conta, lido do Marketing (ver o topo do arquivo)."""
    por_chave: dict[tuple[str, str], list[UUID]] = defaultdict(list)
    for a in (
        await session.execute(
            select(MarketingAccount).where(
                MarketingAccount.arquivada_em.is_(None),
                MarketingAccount.department == department,
            )
        )
    ).scalars():
        por_chave[(a.platform, chave_nome(a.name))].append(a.id)
    casadas: dict[UUID, UUID] = {}
    for c in contas:
        achadas = por_chave.get((PLATAFORMA_NO_MARKETING.get(c.platform, ""), chave_nome(c.name)))
        if achadas and len(achadas) == 1:
            casadas[c.id] = achadas[0]
    somas: dict[UUID, tuple[float, float]] = {}
    if casadas:
        desde = datetime.now(UTC) - timedelta(days=JANELA_REAL_DIAS)
        for conta_ads, gasto, receita in await session.execute(
            select(
                MarketingMetric.account_id,
                func.sum(MarketingMetric.spend),
                func.sum(MarketingMetric.revenue),
            )
            .where(
                MarketingMetric.account_id.in_(set(casadas.values())),
                MarketingMetric.intensity == 0,
                MarketingMetric.timestamp >= desde,
            )
            .group_by(MarketingMetric.account_id)
        ):
            somas[conta_ads] = (float(gasto or 0), float(receita or 0))
    saida: dict[UUID, AdsRealOut] = {}
    for c in contas:
        gasto, receita = somas.get(casadas.get(c.id), (0.0, 0.0))
        saida[c.id] = AdsRealOut(
            pct_gasto=round(gasto / receita * 100, 1) if receita > 0 else None,
            roas=round(receita / gasto, 2) if gasto > 0 else None,
        )
    return saida


async def _contas_out(
    session: AsyncSession, contas: Sequence[PricingAccount], department: str
) -> dict[str, AdsContaOut]:
    ids = [c.id for c in contas]
    gerais = {
        g.pricing_account_id: g
        for g in (
            await session.execute(
                select(PricingAdsConta)
                .where(PricingAdsConta.pricing_account_id.in_(ids))
                .execution_options(populate_existing=True)
            )
        ).scalars()
    }
    produtos: dict[UUID, dict[str, AdsValoresOut]] = defaultdict(dict)
    for p in (
        await session.execute(
            select(PricingAdsProduto)
            .where(PricingAdsProduto.pricing_account_id.in_(ids))
            .execution_options(populate_existing=True)
        )
    ).scalars():
        produtos[p.pricing_account_id][str(p.pricing_product_id)] = AdsValoresOut(
            pode_gastar_pct=p.pode_gastar_pct, gasto_dia=p.gasto_dia
        )
    real = await _real_das_contas(session, contas, department)
    saida: dict[str, AdsContaOut] = {}
    for c in contas:
        g = gerais.get(c.id)
        geral_ligada, por_produto_ligada = caixas_ads(g)
        saida[str(c.id)] = AdsContaOut(
            ads_geral_ligada=geral_ligada,
            pode_gastar_pct=g.pode_gastar_pct if g else None,
            gasto_dia=g.gasto_dia if g else None,
            ads_por_produto_ligada=por_produto_ligada,
            produtos=produtos[c.id],
            real=real[c.id],
        )
    return saida


async def _conta_da_aba(
    session: AsyncSession, user: User, account_id: UUID
) -> tuple[PricingAccount, str]:
    """(conta, slug do departamento) se a aba Contas desta pessoa mostra a
    conta; senão 404 (fora da cerca, coluna de catálogo ou não existe)."""
    acc = await session.get(PricingAccount, account_id)
    if acc is None or acc.canal == CANAL_CATALOGO:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "account_not_found"})
    visiveis = {c.id for c in await _contas_da_grade(session, user, acc.segment_id)}
    raiz = await session.get(Segment, acc.segment_id)
    if acc.id not in visiveis or raiz is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "account_not_found"})
    return acc, raiz.slug


@router.get("/ads", response_model=AdsOut)
async def ver_ads(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("tabela_precos_contas", "view"))],
    department: Annotated[str, Query()],
) -> AdsOut:
    root_id = await _resolve_root_segment_id(session, department)
    if root_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"code": "invalid_department"})
    contas = [
        c for c in await _contas_da_grade(session, user, root_id) if c.canal != CANAL_CATALOGO
    ]
    # M-79: com a categoria de venda (o join de `produtos_do_departamento` já
    # traz o segmento do produto).
    produtos = (
        await session.execute(
            produtos_do_departamento(
                select(
                    PricingProduct.id,
                    PricingProduct.sku,
                    PricingProduct.name,
                    Segment.id,
                    Segment.name,
                ),
                root_id,
            ).order_by(PricingProduct.name, PricingProduct.sku)
        )
    ).all()
    com_produto = {cat_id for _, _, _, cat_id, _ in produtos}
    categorias = [
        AdsCategoriaOut(id=s.id, name=s.name)
        for s in (
            await session.execute(
                select(Segment)
                .where(Segment.parent_id == root_id)
                .order_by(Segment.sort_order, Segment.name)
            )
        ).scalars()
        if s.active or s.id in com_produto
    ]
    return AdsOut(
        department=department,
        pode_editar=_pode_editar(user),
        produtos=[
            AdsProdutoOut(
                id=i, sku=s, name=n, categoria=AdsCategoriaOut(id=cat_id, name=cat_nome)
            )
            for i, s, n, cat_id, cat_nome in produtos
        ],
        categorias=categorias,
        contas=await _contas_out(session, contas, department),
    )


@router.put("/ads/{account_id}", response_model=AdsContaOut)
async def gravar_ads_da_conta(
    account_id: UUID,
    body: AdsContaIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("tabela_precos_contas", "edit"))],
) -> AdsContaOut:
    """A Geral e as duas caixas. Campo ausente não muda; caixa null não muda;
    % e gasto null apagam. As caixas são um ou outro (M-78, ver o topo)."""
    acc, department = await _conta_da_aba(session, user, account_id)
    mudancas = {
        campo: valor
        for campo, valor in body.model_dump(exclude_unset=True).items()
        if valor is not None or campo in ("pode_gastar_pct", "gasto_dia")
    }
    if mudancas.get("ads_geral_ligada") and mudancas.get("ads_por_produto_ligada"):
        raise HTTPException(422, detail={"code": "ads_um_ou_outro"})
    if mudancas.keys() & {"ads_geral_ligada", "ads_por_produto_ligada"}:
        # As duas caixas vão juntas, já no um ou outro (a linha sem as duas
        # gravadas leria o padrão do banco, as duas ligadas). O par do ML grava
        # o mesmo nas duas contas.
        caixas = (await estado_ads(session, acc.id)).com_geral(mudancas)
        mudancas["ads_geral_ligada"] = caixas.geral_ligada
        mudancas["ads_por_produto_ligada"] = caixas.por_produto_ligada
    if mudancas:
        irma = await _irma_de(session, acc)
        contas = [acc] if irma is None else [acc, irma]
        # M-56: o % e as caixas mexem na soma; o gasto por dia, não.
        # Conta que já está acima do teto (M-86): baixar o Ads passa, mesmo
        # sem caber ainda; aumentar, não (ver `_recusar_custo_total_estourado`).
        if mudancas.keys() - {"gasto_dia"}:
            for conta in contas:
                gravado = await estado_ads(session, conta.id)
                _recusar_custo_total_estourado(
                    conta, gravado.com_geral(mudancas), ads_gravado=gravado
                )
        for conta in contas:
            stmt = pg_insert(PricingAdsConta).values(
                pricing_account_id=conta.id, atualizado_por=user.id, **mudancas
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[PricingAdsConta.pricing_account_id],
                set_={**mudancas, "atualizado_por": user.id, "atualizado_em": func.now()},
            )
            await session.execute(stmt)
        await session.commit()
        for conta in contas:
            logger.info(
                "pricing_ads_conta",
                account_id=str(conta.id),
                campos=sorted(mudancas),
                user_id=str(user.id),
            )
    return (await _contas_out(session, [acc], department))[str(acc.id)]


async def _gravar_produto_na_conta(
    session: AsyncSession, user: User, account_id: UUID, product_id: UUID, mudancas: dict
) -> None:
    """Aplica as mudanças no % / gasto próprios do produto numa conta (sem
    commit). Sem % e sem gasto, a linha sai."""
    linha = await session.get(PricingAdsProduto, (account_id, product_id))
    valores = {
        "pode_gastar_pct": linha.pode_gastar_pct if linha else None,
        "gasto_dia": linha.gasto_dia if linha else None,
        **mudancas,
    }
    if valores["pode_gastar_pct"] is None and valores["gasto_dia"] is None:
        if linha is not None:
            await session.delete(linha)
    elif linha is None:
        session.add(
            PricingAdsProduto(
                pricing_account_id=account_id,
                pricing_product_id=product_id,
                atualizado_por=user.id,
                **valores,
            )
        )
    else:
        for campo, valor in mudancas.items():
            setattr(linha, campo, valor)
        linha.atualizado_por = user.id
        linha.atualizado_em = func.now()


@router.put("/ads/{account_id}/produto/{product_id}", response_model=AdsContaOut)
async def gravar_ads_do_produto(
    account_id: UUID,
    product_id: UUID,
    body: AdsProdutoIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("tabela_precos_contas", "edit"))],
) -> AdsContaOut:
    """O % e o gasto por dia de um produto do departamento da conta. Sem % e
    sem gasto, a linha sai. Grava com qualquer caixa marcada (só vale com
    "Produto"). Responde o Ads da conta inteira."""
    acc, department = await _conta_da_aba(session, user, account_id)
    prod = await session.get(PricingProduct, product_id)
    folha = await session.get(Segment, prod.segment_id) if prod is not None else None
    if folha is None or folha.parent_id != acc.segment_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "product_not_found"})
    mudancas = body.model_dump(exclude_unset=True)
    irma = await _irma_de(session, acc)
    contas = [acc] if irma is None else [acc, irma]
    # M-56: o % do produto mexe na soma; o gasto por dia, não. Conta que já
    # está acima do teto (M-86): o que não aumenta o maior Ads passa.
    if "pode_gastar_pct" in mudancas:
        for conta in contas:
            gravado = await estado_ads(session, conta.id)
            _recusar_custo_total_estourado(
                conta,
                gravado.com_produto(product_id, mudancas["pode_gastar_pct"]),
                ads_gravado=gravado,
            )
    for conta in contas:
        await _gravar_produto_na_conta(session, user, conta.id, product_id, mudancas)
    await session.commit()
    for conta in contas:
        logger.info(
            "pricing_ads_produto",
            account_id=str(conta.id),
            product_id=str(product_id),
            campos=sorted(mudancas),
            user_id=str(user.id),
        )
    return (await _contas_out(session, [acc], department))[str(acc.id)]
