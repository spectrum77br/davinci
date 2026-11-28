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
* M-73: `linha_da_tabela` (a linha da Tabela de Preços do produto, a categoria de venda dela e as
  opções do menu) e `centro_herdado` (o Centro de custo de um produto em branco: o departamento,
  calculado e não copiado — o da linha; sem linha, o da categoria).

* M-107: `cor_id` (a Cor do produto, um nome do tipo "Cor" de Cadastros › Produtos).
* M-114…M-116: `lista` — o que a LISTA do Produtos 2 mostra de cada produto (Cor, Centro de
  custo, Categoria de venda, Margem mínima, Condição especial, Foto e Embalagem), para todos os
  produtos numa consulta só (sem uma chamada por produto), com as MESMAS regras do cadastro:
  `margens_herdadas` (a margem herdada em lote, o mesmo SQL de `margem_herdada`), `centro_herdado`
  sobre linhas lidas de uma vez (`_herdado`) e o resumo da condição.

Com as duas tabelas vazias (o dia em que for ao ar) nada daqui muda o que a Margem, o robô que
segura pedido e a nota já fazem.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import ColumnElement, select, text, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    CentroCusto,
    PricingProduct,
    ProdutoCadastro,
    ProdutoCondicaoEspecial,
    ProdutoRegra,
    Segment,
)
from app.services import categorias_venda
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


def base_valida(sku: str | None, tags: Sequence[str]) -> str | None:
    """A chave do produto, ou None quando o código não serve de chave: vazio, longo demais, com
    peça vazia ("a++b") ou com peça que é só a tag (".ra"). A rota de um produto recusa (400); a
    lista pula."""
    texto = (sku or "").strip()
    base = sku_base(texto, tags)
    lista_tags = list(tags)
    if (
        not texto
        or len(texto) > SKU_MAX
        or not base
        or any(not p or p in lista_tags for p in base.split("+"))
    ):
        return None
    return base


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
# M-115 (27/11/2026): em LOTE — uma linha por código do array (o mesmo SQL serve a um produto só,
# `margem_herdada`, e à lista inteira, `margens_herdadas`: nunca duas regras). `k` = o código em
# minúsculas, calculado uma vez por código (2.763 códigos × ~1.100 variantes: ~0,3 s).
_HERDADAS_SQL = text(
    f"""
    SELECT s.sku           AS sku,
           leaf.name       AS leaf_nome,
           leaf.min_margin AS leaf_min,
           root.name       AS raiz_nome,
           qz.min_margin   AS queima_min
    FROM (SELECT DISTINCT x AS sku, lower(x) AS k FROM unnest(CAST(:skus AS text[])) AS x) s
    LEFT JOIN LATERAL (
        SELECT v.segment_id
        FROM {_T}.pricing_product_variant v
        WHERE s.k = v.variant_norm
           OR s.k LIKE (v.variant_norm || '.%')
           OR s.k LIKE (v.variant_norm || '+%')
        ORDER BY (CASE WHEN s.k = v.variant_norm THEN 1
                       WHEN s.k LIKE (v.variant_norm || '+%') THEN 2
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
    """  # noqa: S608 — só o schema das settings; os SKUs vão por parâmetro
)


@dataclass(frozen=True)
class Herdada:
    valor: Decimal
    origem: str


def _herdada_de(sku: str, r) -> Herdada:
    if r["leaf_min"] is not None:
        nome = r["leaf_nome"] or "?"
        raiz = f" ({r['raiz_nome']})" if r["raiz_nome"] else ""
        return Herdada(Decimal(r["leaf_min"]), f"da categoria de venda {nome}{raiz}")
    if sku.strip().lower().startswith("z") and r["queima_min"] is not None:
        return Herdada(Decimal(r["queima_min"]), "da Queima de estoque (código começa com z)")
    return Herdada(MARGEM_PADRAO, "padrão do sistema")


async def margens_herdadas(session: AsyncSession, skus: Sequence[str]) -> dict[str, Herdada]:
    """{SKU (como veio): a margem mínima que o pedido dele usa quando o produto está em branco}."""
    lista = sorted({str(s) for s in skus})
    if not lista:
        return {}
    rows = (await session.execute(_HERDADAS_SQL, {"skus": lista})).mappings().all()
    return {r["sku"]: _herdada_de(r["sku"], r) for r in rows}


async def margem_herdada(session: AsyncSession, sku: str) -> Herdada:
    """A margem mínima que o pedido deste SKU usa quando o produto está em branco."""
    return (await margens_herdadas(session, [sku]))[sku]


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


# --------------------------------------------------------------- M-73: linha e centro de custo


@dataclass(frozen=True)
class LinhaTabela:
    """A linha da Tabela de Preços do produto, como o menu "Categoria de venda" precisa.

    `raiz` = o departamento da linha (Celular, Eletro, Mala… ou o Catálogo); `centro` = o
    Centro de custo dessa raiz (None quando a raiz não é centro, como o Catálogo); `opcoes` = as
    categorias de venda ATIVAS da raiz, na ordem das colunas de margem/frete."""

    produto: PricingProduct
    segmento: Segment | None
    raiz: Segment | None
    centro: CentroCusto | None
    opcoes: list[Segment]


def codigos_da_linha(sku: str | None) -> list[str]:
    """ "dg052,dg053, dg054" → ["dg052", "dg053", "dg054"]."""
    return [c.strip() for c in str(sku or "").split(",") if c.strip()]


async def linha_da_tabela(session: AsyncSession, linha_id: UUID | None) -> LinhaTabela | None:
    """A linha `linha_id` (o `pricing_id` que o ERP acha para o SKU, a mesma linha das Fotos e da
    Embalagem da M-72), lida AGORA do banco. Não existe mais → None."""
    if linha_id is None:
        return None
    produto = await session.get(PricingProduct, linha_id)
    if produto is None:
        return None
    seg = await session.get(Segment, produto.segment_id) if produto.segment_id else None
    if seg is not None and seg.parent_id is None:
        raiz: Segment | None = seg  # linha apontando para a própria raiz (não deveria)
    else:
        raiz = await categorias_venda.raiz_de(session, seg) if seg is not None else None
    centro = None
    opcoes: list[Segment] = []
    if raiz is not None:
        centro = (
            await session.execute(select(CentroCusto).where(CentroCusto.segment_id == raiz.id))
        ).scalar_one_or_none()
        opcoes = [f for f in await categorias_venda.filhas(session, raiz.id) if f.active]
    return LinhaTabela(produto, seg, raiz, centro, opcoes)


def linha_out(linha: LinhaTabela | None) -> dict | None:
    if linha is None:
        return None
    seg, raiz = linha.segmento, linha.raiz
    folha = seg is not None and raiz is not None and seg.id != raiz.id
    return {
        "id": str(linha.produto.id),
        "nome": linha.produto.name,
        "codigos": codigos_da_linha(linha.produto.sku),
        "departamento": raiz.name if raiz is not None else None,
        "centro_custo_id": str(linha.centro.id) if linha.centro is not None else None,
        "centro_custo": linha.centro.nome if linha.centro is not None else None,
        "segment_id": str(seg.id) if seg is not None else None,
        "categoria_venda": seg.name if folha else None,
        "categoria_venda_ativa": bool(seg.active) if folha else None,
        # A posição na raiz (1 a 10 desde a M-93): é o que a coluna "Tabela" da Tabela de Preços
        # manda (o Produtos 2 troca a categoria de venda mandando este número no PATCH).
        "product_type": int(seg.sort_order) + 1 if folha else None,
        "opcoes": [
            {"segment_id": str(o.id), "nome": o.name, "product_type": int(o.sort_order) + 1}
            for o in linha.opcoes
        ],
    }


def _sem_acento(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )


def _chave_nome(texto: str | None) -> str:
    return " ".join(_sem_acento(str(texto or "")).casefold().split())


def centro_por_categoria(
    centros: Sequence[CentroCusto], categoria: str | None
) -> CentroCusto | None:
    """`centro_da_categoria` sobre os centros já lidos (a lista do Produtos 2 lê uma vez só)."""
    cat = _chave_nome(categoria)
    if not cat:
        return None
    achados = [
        c
        for c in centros
        if (nome := _chave_nome(c.nome)) and (cat == nome or cat.startswith(nome + " "))
    ]
    return max(achados, key=lambda c: len(_chave_nome(c.nome)), default=None)


async def centro_da_categoria(session: AsyncSession, categoria: str | None) -> CentroCusto | None:
    """O centro de custo de uma CATEGORIA do produto (Cadastros › Produtos): o centro cujo nome é
    a categoria ou o começo dela ("Celular Kit" → Celular, "Mala" → Mala; "Acessórios" e
    "Insumos" → nenhum). Lido agora: um centro criado em Cadastros › Centro de custo vale na hora.
    Se dois servirem, o de nome mais longo."""
    if not _chave_nome(categoria):
        return None
    centros = list((await session.execute(select(CentroCusto))).scalars())
    return centro_por_categoria(centros, categoria)


@dataclass(frozen=True)
class CentroHerdado:
    centro: CentroCusto | None
    origem: str | None


async def centro_herdado(
    session: AsyncSession, linha: LinhaTabela | None, categoria: str | None
) -> CentroHerdado:
    """O Centro de custo de um produto que está em branco: o departamento que ele já tem,
    CALCULADO na hora e nunca copiado para `produto_regras`.

    1. Com linha na Tabela de Preços: o departamento da linha (a raiz da categoria de venda).
       Raiz que não é centro de custo (o Catálogo) → nenhum.
    2. Sem linha: o da categoria do produto (`centro_da_categoria`)."""
    centro = None if linha is not None else await centro_da_categoria(session, categoria)
    return _herdado(linha, centro, categoria)


def _herdado(
    linha: LinhaTabela | None, centro_da_cat: CentroCusto | None, categoria: str | None
) -> CentroHerdado:
    """A regra de `centro_herdado`, com o centro da categoria já achado (a lista lê tudo de uma
    vez e chama esta mesma função)."""
    if linha is not None:
        if linha.centro is not None:
            return CentroHerdado(linha.centro, "do departamento da linha da Tabela de Preços")
        if linha.raiz is not None:
            return CentroHerdado(
                None,
                f"a linha da Tabela de Preços é do {linha.raiz.name}, que não é centro de custo",
            )
        return CentroHerdado(None, "a linha da Tabela de Preços não tem departamento")
    if centro_da_cat is not None:
        return CentroHerdado(centro_da_cat, f"da categoria {str(categoria).strip()}")
    return CentroHerdado(None, None)


# --------------------------------------------------------------- M-114…M-116: a lista do Produtos 2


@dataclass(frozen=True)
class ItemLista:
    """Um produto da lista do Produtos 2, como o ERP o conhece: o código, a linha da Tabela de
    Preços que o ERP acha para ele (`pricing_id`, texto; o que não é uuid vale como sem linha) e
    a categoria dele no ERP (o nome). Os mesmos `linha` e `categoria` do GET de um produto."""

    sku: str
    linha: str | None = None
    categoria: str | None = None


def _f(d: Decimal | None) -> float | None:
    return None if d is None else float(d)


def _linhas_em_memoria(
    produtos: Sequence[PricingProduct],
    segmentos: dict[UUID, Segment],
    centro_da_raiz: dict[UUID, CentroCusto],
) -> dict[UUID, LinhaTabela]:
    """`linha_da_tabela` para várias linhas lidas de uma vez (sem as opções do menu, que a lista
    não usa): o mesmo segmento, a mesma raiz (`categorias_venda.raiz_de`: o pai, se ele é raiz) e
    o mesmo centro."""
    out: dict[UUID, LinhaTabela] = {}
    for pp in produtos:
        seg = segmentos.get(pp.segment_id) if pp.segment_id else None
        raiz: Segment | None
        if seg is None:
            raiz = None
        elif seg.parent_id is None:
            raiz = seg  # linha apontando para a própria raiz (não deveria)
        else:
            pai = segmentos.get(seg.parent_id)
            raiz = pai if pai is not None and pai.parent_id is None else None
        centro = centro_da_raiz.get(raiz.id) if raiz is not None else None
        out[pp.id] = LinhaTabela(pp, seg, raiz, centro, [])
    return out


def _piso_vigente(condicoes: list[ProdutoCondicaoEspecial], hoje: date) -> Decimal | None:
    """O piso da condição que o resumo mostra (a primeira vigente); None = sem condição vigente
    ou condição "sem piso"."""
    vigentes = sorted(
        (c for c in condicoes if c.pedido_ate >= hoje), key=lambda c: (c.pedido_de, c.pedido_ate)
    )
    return vigentes[0].piso if vigentes else None


async def lista(
    session: AsyncSession,
    itens: Sequence[ItemLista],
    hoje: date,
    escopo_linhas: ColumnElement[bool] | None = None,
) -> tuple[dict[str, dict], list[str]]:
    """O que a lista do Produtos 2 mostra de cada produto, com as regras do cadastro, para todos
    de uma vez: ({SKU como veio: dados}, [SKUs que não servem de chave]).

    Consultas fixas (não crescem com a lista): a lista de tags, `produto_regras` + os nomes das
    cores, as condições especiais, os centros de custo, os segmentos, as linhas da Tabela de Preços
    pedidas e a margem herdada em lote. `escopo_linhas` = o filtro de quem pede nas linhas
    (`user_scope`, o mesmo da rota de Fotos/Embalagem)."""
    tags = await tags_de_envio(session)
    validos: list[tuple[ItemLista, str]] = []
    ignorados: list[str] = []
    for item in itens:
        base = base_valida(item.sku, tags)
        if base is None:
            ignorados.append(item.sku)
        else:
            validos.append((item, base))
    if not validos:
        return {}, ignorados
    bases = sorted({b for _, b in validos})

    regras = {
        r.sku_base: r
        for r in (
            await session.execute(select(ProdutoRegra).where(ProdutoRegra.sku_base.in_(bases)))
        ).scalars()
    }
    cor_ids = sorted({r.cor_id for r in regras.values() if r.cor_id is not None}, key=str)
    cores = (
        dict(
            (
                await session.execute(
                    select(ProdutoCadastro.id, ProdutoCadastro.nome).where(
                        ProdutoCadastro.id.in_(cor_ids)
                    )
                )
            ).all()
        )
        if cor_ids
        else {}
    )
    conds: dict[str, list[ProdutoCondicaoEspecial]] = {}
    for c in (
        await session.execute(
            select(ProdutoCondicaoEspecial).where(ProdutoCondicaoEspecial.sku_base.in_(bases))
        )
    ).scalars():
        conds.setdefault(c.sku_base, []).append(c)
    centros = list((await session.execute(select(CentroCusto))).scalars())
    centro_por_id = {c.id: c for c in centros}
    centro_da_raiz = {c.segment_id: c for c in centros if c.segment_id is not None}

    linha_ids = sorted(
        {u for item, _ in validos if item.linha and (u := uuid_ou_none(item.linha))}, key=str
    )
    linhas: dict[UUID, LinhaTabela] = {}
    if linha_ids:
        produtos = list(
            (
                await session.execute(
                    select(PricingProduct).where(
                        PricingProduct.id.in_(linha_ids),
                        escopo_linhas if escopo_linhas is not None else true(),
                    )
                )
            ).scalars()
        )
        segmentos = {s.id: s for s in (await session.execute(select(Segment))).scalars()}
        linhas = _linhas_em_memoria(produtos, segmentos, centro_da_raiz)

    herdadas = await margens_herdadas(session, [item.sku for item, _ in validos])
    out: dict[str, dict] = {}
    for item, base in validos:
        regra = regras.get(base)
        linha = linhas.get(uuid_ou_none(item.linha)) if item.linha else None
        herdado = _herdado(
            linha,
            None if linha is not None else centro_por_categoria(centros, item.categoria),
            item.categoria,
        )
        escolhido = (
            centro_por_id.get(regra.centro_custo_id)
            if regra is not None and regra.centro_custo_id is not None
            else None
        )
        seg, raiz = (linha.segmento, linha.raiz) if linha is not None else (None, None)
        folha = seg is not None and raiz is not None and seg.id != raiz.id
        pp = linha.produto if linha is not None else None
        do_produto = conds.get(base, [])
        piso = _piso_vigente(do_produto, hoje)
        resumo = resumo_condicao(do_produto, hoje)
        out[item.sku] = {
            "sku_base": base,
            "cor_id": str(regra.cor_id) if regra is not None and regra.cor_id else None,
            "cor": cores.get(regra.cor_id) if regra is not None and regra.cor_id else None,
            "margem_min": _f(regra.margem_min) if regra is not None else None,
            "margem_min_herdada": float(herdadas[item.sku].valor),
            "centro_custo": escolhido.nome if escolhido is not None else None,
            "centro_custo_herdado": herdado.centro.nome if herdado.centro is not None else None,
            "tem_linha": linha is not None,
            "categoria_venda": seg.name if folha else None,
            "categoria_venda_ativa": bool(seg.active) if folha else None,
            "condicao_resumo": resumo,
            "condicao_vigente": resumo != "—",
            "condicao_piso": _f(piso),
            "fotos_count": pp.fotos_count if pp is not None else None,
            "foto": bool(pp is not None and (pp.fotos_count or 0) > 0),
            "embalagem": bool(pp is not None and (pp.embalagens_path or "").strip()),
            "embalagens_count": pp.embalagens_count if pp is not None else None,
        }
    return out, ignorados
