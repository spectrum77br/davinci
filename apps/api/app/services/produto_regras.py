"""Margem mín, Condição especial e DUIMP padrão POR PRODUTO (M-74, migration 0516).

Tudo o que o resto do sistema precisa saber das tabelas `produto_regras` e
`produto_condicoes_especiais` (models/produto_regras.py):

* `sku_base(sku, tags)` — a chave do produto: cada peça do código (separada por "+"), em
  minúsculas e sem espaços em volta, SEM A TAG DE ENVIO do fim. Tag de envio = a lista "Tag de
  envio" de Cadastros › Produtos (migration 0508; hoje .cd .ci .pi .ra .sa .sp). Só uma tag sai, e
  só se for o fim da peça: a mesma regra base/tag da M-44 e da M-46
  (services/sku_tag_envio.numeracao_do_sku), peça a peça. dg019.ra → dg019;
  dg019.ra+a020.ra → dg019+a020; b001.12.sp → b001.12; b001.12 → b001.12; uaf001m1.110 →
  uaf001m1.110; uaf001m1.2l → uaf001m1.2l; z2821 → z2821. Assim tamanhos de mala e capacidades e
  voltagens do Eletro NÃO se juntam (correção do coordenador, 26/11: a 1ª versão cortava tudo
  depois do ponto). O que não está na lista fica no código: ".us", ".mala", ".kit2", ".usado"…
  A mesma regra em SQL (`sku_base_sql`, que lê a lista por subconsulta NÃO correlacionada) é
  usada pela Margem; a view vw_bling_pedidos tem uma cópia congelada dela na migration 0516.
  Python × SQL conferidos com os SKUs reais (tests/test_produto_regras.py).
* `margem_herdada` — o que vale quando o campo do produto está em branco, com a MESMA ordem da
  view: categoria de venda (segmento filho, achado pela variante como a view acha) → "Queima de
  estoque" para SKU que começa com "z" → 9%.
* `condicoes` / `resumo_condicao` — a Condição especial do produto para a tela.
* `duimp_padrao_dos_produtos` — o texto do produto para a nota (nf_emissao_gerar).

Com as duas tabelas vazias (o dia em que for ao ar) nada daqui muda o que a Margem, o robô que
segura pedido e a nota já fazem.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import ProdutoCondicaoEspecial, ProdutoRegra
from app.services.sku_tag_envio import numeracao_do_sku, tags_de_envio

_SCHEMA = get_settings().database_schema
_T = f'"{_SCHEMA}"'

SKU_MAX = 120
MARGEM_PADRAO = Decimal("0.09")
QUEIMA = "queima de estoque"


def sku_base(sku: str | None, tags: Sequence[str]) -> str:
    """A chave do produto: cada peça do código sem a tag de envio do fim, em minúsculas.

    `tags` = a lista "Tag de envio" de Cadastros › Produtos (`tags_de_envio`). Peça a peça, a
    regra base/tag da M-44/M-46 (`numeracao_do_sku`): tira a tag da lista em que a peça termina
    (a mais longa, se mais de uma casar), uma só, e só se sobrar numeração.
    dg019.ra → dg019; dg019.ra+a020.ra → dg019+a020; b001.12.sp → b001.12; uaf001m1.110 →
    uaf001m1.110; z2821 → z2821."""
    lista = list(tags)
    return "+".join(numeracao_do_sku(p, lista) for p in str(sku or "").split("+"))


async def sku_base_de(session: AsyncSession, sku: str | None) -> str:
    """`sku_base` com a lista de tags lida do banco agora."""
    return sku_base(sku, await tags_de_envio(session))


def tags_padrao_sql(schema: str = _SCHEMA) -> str:
    """Subconsulta NÃO correlacionada (o banco avalia uma vez por consulta): a lista "Tag de
    envio" como padrão de regex que casa UMA tag no fim de cada peça, com numeração antes dela.

    Cada tag como em `tags_de_envio` (sem espaços em volta, minúscula; vazias fora). Tag com "+"
    nunca casa uma peça em Python, então fica fora. Cada caractere que não é 0-9/a-z vai com uma
    barra invertida na frente (no regex do Postgres, barra + caractere que não é letra/dígito
    ASCII = o próprio caractere). O "olhar para trás" exige um caractere da peça antes da tag
    (sobra numeração); o "olhar para frente" exige o "+" ou o fim (a tag é o fim da peça). Entre
    tags que casam no fim da mesma peça, o regex fica com a que começa mais à esquerda, ou seja a
    MAIS LONGA, como `tag_do_sku`. Lista vazia → NULL (quem usa cai no código sem tirar nada)."""
    return (
        "(SELECT '(?<=[^+])(?:'"  # noqa: S608 — só o schema das settings, sem dado da pessoa
        r" || string_agg(regexp_replace(t.tag, '([^0-9a-z])', '\\\1', 'g'),"
        r" '|' ORDER BY length(t.tag) DESC, t.tag) || ')(?=\+|$)'"
        r" FROM (SELECT DISTINCT lower(regexp_replace(c.nome, '^\s+|\s+$', '', 'g')) AS tag"
        f' FROM "{schema}".produto_cadastros c'
        f' JOIN "{schema}".produto_cadastro_tipos tp ON tp.id = c.tipo_id'
        " WHERE tp.chave = 'tag_envio') t"
        " WHERE t.tag <> '' AND strpos(t.tag, '+') = 0)"
    )


def sku_base_sql(coluna: str, schema: str = _SCHEMA) -> str:
    """A mesma regra de `sku_base`, em SQL, para a coluna dada (ex.: 'v.sku').

    1º minúsculas; 2º tira os espaços em volta do "+"; 3º tira os espaços do começo e do fim
    (= cada peça sem espaços em volta); 4º tira a tag do fim de cada peça pelo padrão de
    `tags_padrao_sql` (lista vazia → o código do 3º passo). Conferido contra `sku_base` com os
    SKUs reais (tests/test_produto_regras.py). A view vw_bling_pedidos tem uma cópia congelada
    (migration 0516)."""
    n = (
        "regexp_replace(regexp_replace(lower(" + coluna + r"), '\s*\+\s*', '+', 'g'),"
        r" '^\s+|\s+$', '', 'g')"
    )
    return f"COALESCE(regexp_replace({n}, {tags_padrao_sql(schema)}, '', 'g'), {n})"


# --------------------------------------------------------------- margem herdada

# Mesmo caminho da view vw_bling_pedidos (0235/0516): a variante da Tabela de Preços que casa com
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
    fora. Tabela vazia → {} (duas consultas pequenas, a lista de tags e a tabela; sem efeito)."""
    if not any(skus):
        return {}
    tags = await tags_de_envio(session)
    por_base: dict[str, set[str]] = {}
    for s in skus:
        if s and (b := sku_base(s, tags)):
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
