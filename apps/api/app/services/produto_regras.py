"""Margem mín, Condição especial e DUIMP padrão POR PRODUTO (M-74, migration 0513).

Tudo o que o resto do sistema precisa saber das tabelas `produto_regras` e
`produto_condicoes_especiais` (models/produto_regras.py):

* `sku_base(sku)` — a chave do produto: cada peça do código sem a tag, em minúsculas. A mesma
  regra em SQL (`sku_base_sql`) é usada pela Margem; a view vw_bling_pedidos tem uma cópia
  congelada dela na migration 0513. Formatos reais conferidos na base em 26/11 (ver o relatório
  da M-74): além da tag de envio (.ra, .sp…), o ponto também corta o tamanho da mala (b001.12 e
  b001.12.18 → b001), a voltagem/capacidade do eletro (uaf001m1.110 e uaf001m1.2l → uaf001m1), o
  ".kit1" (dg001.kit2 → dg001) e os sufixos "usado"/"avariada" (0002.usado → 0002).
* `margem_herdada` — o que vale quando o campo do produto está em branco, com a MESMA ordem da
  view: categoria de venda (segmento filho, achado pela variante como a view acha) → "Queima de
  estoque" para SKU que começa com "z" → 9%.
* `condicoes` / `resumo_condicao` — a Condição especial do produto para a tela.
* `duimp_padrao_dos_produtos` — o texto do produto para a nota (nf_emissao_gerar).

Com as duas tabelas vazias (o dia em que for ao ar) nada daqui muda o que a Margem, o robô que
segura pedido e a nota já fazem.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import ProdutoCondicaoEspecial, ProdutoRegra

_SCHEMA = get_settings().database_schema
_T = f'"{_SCHEMA}"'

SKU_MAX = 120
MARGEM_PADRAO = Decimal("0.09")
QUEIMA = "queima de estoque"


def sku_base(sku: str | None) -> str:
    """dg019.ra → dg019; dg019.ra+a020.ra → dg019+a020; uaf001m1.110 → uaf001m1.

    Regra decidida pelo coordenador da M-74:
    ``"+".join(p.split(".")[0].strip().lower() for p in sku.split("+"))``."""
    return "+".join(p.split(".")[0].strip().lower() for p in str(sku or "").split("+"))


def sku_base_sql(coluna: str) -> str:
    """A mesma regra de `sku_base`, em SQL, para a coluna dada (ex.: 'v.sku').

    1º tira de cada peça o ponto e tudo até o próximo "+"; 2º tira os espaços em volta do "+";
    3º tira os espaços do começo e do fim. Testado contra `sku_base` com os SKUs reais
    (tests/test_produto_regras.py)."""
    return (
        "regexp_replace(regexp_replace(regexp_replace(lower(" + coluna + "),"
        r" '\.[^+]*', '', 'g'), '\s*\+\s*', '+', 'g'), '^\s+|\s+$', '', 'g')"
    )


# --------------------------------------------------------------- margem herdada

# Mesmo caminho da view vw_bling_pedidos (0235/0513): a variante da Tabela de Preços que casa com
# o código vendido (exato > kit > com sufixo; a mais longa), o segmento filho dela e a raiz.
_HERDADA_SQL = text(
    f"""
    SELECT leaf.name       AS leaf_nome,
           leaf.min_margin AS leaf_min,
           root.name       AS raiz_nome,
           qz.min_margin   AS queima_min
    FROM (SELECT 1) um
    LEFT JOIN LATERAL (
        SELECT v.segment_id
        FROM {_T}.pricing_product_variant v
        WHERE lower(CAST(:sku AS text)) = v.variant_norm
           OR lower(CAST(:sku AS text)) LIKE (v.variant_norm || '.%')
           OR lower(CAST(:sku AS text)) LIKE (v.variant_norm || '+%')
        ORDER BY (CASE WHEN lower(CAST(:sku AS text)) = v.variant_norm THEN 1
                       WHEN lower(CAST(:sku AS text)) LIKE (v.variant_norm || '+%') THEN 2
                       ELSE 3 END),
                 v.vlen DESC, v.segment_id
        LIMIT 1
    ) pp ON true
    LEFT JOIN {_T}.segments leaf ON leaf.id = pp.segment_id
    LEFT JOIN {_T}.segments root ON root.id = leaf.parent_id
    LEFT JOIN (
        SELECT s2.min_margin FROM {_T}.segments s2
        WHERE lower(s2.name) = '{QUEIMA}' LIMIT 1
    ) qz ON true
    """  # noqa: S608 — só o schema das settings; o SKU vai por parâmetro
)


@dataclass(frozen=True)
class Herdada:
    valor: Decimal
    origem: str


async def margem_herdada(session: AsyncSession, sku: str) -> Herdada:
    """A margem mínima que o pedido deste SKU usa quando o produto está em branco."""
    r = (await session.execute(_HERDADA_SQL, {"sku": sku})).mappings().one()
    if r["leaf_min"] is not None:
        nome = r["leaf_nome"] or "?"
        raiz = f" ({r['raiz_nome']})" if r["raiz_nome"] else ""
        return Herdada(Decimal(r["leaf_min"]), f"da categoria de venda {nome}{raiz}")
    if sku.strip().lower().startswith("z") and r["queima_min"] is not None:
        return Herdada(Decimal(r["queima_min"]), "da Queima de estoque (código começa com z)")
    return Herdada(MARGEM_PADRAO, "padrão do sistema")


# --------------------------------------------------------------- condição especial


def _pct(fracao: Decimal | None) -> str:
    """0.05 → "5%"; -0.125 → "-12,5%"."""
    if fracao is None:
        return ""
    n = (Decimal(fracao) * 100).quantize(Decimal("0.01")).normalize()
    txt = format(n, "f")
    return txt.replace(".", ",") + "%"


def _dm(d: date) -> str:
    return d.strftime("%d/%m")


def condicao_out(c: ProdutoCondicaoEspecial, hoje: date) -> dict:
    return {
        "id": str(c.id),
        "pedido_de": c.pedido_de.isoformat(),
        "pedido_ate": c.pedido_ate.isoformat(),
        "piso": None if c.piso is None else str(c.piso),
        "vigente": c.pedido_ate >= hoje,
    }


def resumo_condicao(condicoes: list[ProdutoCondicaoEspecial], hoje: date) -> str:
    """ "—" sem condição vigente; "até 30/11 · piso 5%"; "01/12 a 05/12 · sem piso"; "… +1"."""
    vigentes = sorted(
        (c for c in condicoes if c.pedido_ate >= hoje), key=lambda c: (c.pedido_de, c.pedido_ate)
    )
    if not vigentes:
        return "—"
    c = vigentes[0]
    periodo = (
        f"até {_dm(c.pedido_ate)}"
        if c.pedido_de <= hoje
        else (f"{_dm(c.pedido_de)} a {_dm(c.pedido_ate)}")
    )
    piso = "sem piso" if c.piso is None else f"piso {_pct(c.piso)}"
    mais = f" +{len(vigentes) - 1}" if len(vigentes) > 1 else ""
    return f"{periodo} · {piso}{mais}"


async def condicoes(session: AsyncSession, base: str) -> list[ProdutoCondicaoEspecial]:
    return list(
        (
            await session.execute(
                select(ProdutoCondicaoEspecial)
                .where(ProdutoCondicaoEspecial.sku_base == base)
                .order_by(ProdutoCondicaoEspecial.pedido_de, ProdutoCondicaoEspecial.pedido_ate)
            )
        )
        .scalars()
        .all()
    )


# --------------------------------------------------------------- DUIMP padrão (nota)


async def duimp_padrao_dos_produtos(session: AsyncSession, skus: set[str]) -> dict[str, str]:
    """Mapa SKU (como veio) → DUIMP padrão do PRODUTO (pelo sku_base). SKU sem texto fica de
    fora. Tabela vazia → {} (uma consulta só, sem efeito)."""
    por_base: dict[str, set[str]] = {}
    for s in skus:
        if s and (b := sku_base(s)):
            por_base.setdefault(b, set()).add(s)
    if not por_base:
        return {}
    rows = (
        await session.execute(
            select(ProdutoRegra.sku_base, ProdutoRegra.duimp_padrao).where(
                ProdutoRegra.sku_base.in_(sorted(por_base)),
                ProdutoRegra.duimp_padrao.is_not(None),
            )
        )
    ).all()
    out: dict[str, str] = {}
    for base, texto in rows:
        if texto and texto.strip():
            for s in por_base[base]:
                out[s] = texto.strip()
    return out


async def regra(session: AsyncSession, base: str) -> ProdutoRegra | None:
    return await session.get(ProdutoRegra, base)


def uuid_ou_none(v: object) -> UUID | None:
    try:
        return UUID(str(v))
    except (TypeError, ValueError):
        return None
