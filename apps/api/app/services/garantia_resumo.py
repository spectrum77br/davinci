"""Resumo do Painel de Garantia (28/10/2026) — a garantia POR PRODUTO.

Pedidos do Eduardo (28/10): "o resumo de cada garantia, separada por
departamento (eletro, celular…) e o percentil de cada uma"; "a garantia é só
para o pessoal que solicita"; e "vamos fazer a garantia por produto, pode usar
a nossa Tabela de Preços, aba Produtos, como referência… é tudo relacionado à
garantia". Então: uma linha por PRODUTO da Tabela de Preços (o modelo, com as
cores juntas — `pricing_products.sku` é a lista de SKUs da linha), com as
vendas com garantia, os PEDIDOS de garantia e a porcentagem, de onde veio o
pedido, a situação da garantia e os atendimentos; e o total por departamento
(as pílulas da tela). O produto vendido que não está na Tabela de Preços entra
pelo SKU, marcado "fora da Tabela de Preços".

Departamento = a categoria do produto no Bling, a MESMA que o Faturamento usa
(`bling_orders.categoria_nome` do item do pedido — routers/financeiro.py):
  - Celular: Celular, Celular Kit, Celular Usado
  - Eletro: Eletro, Eletro Kit, Eletro Usado
  - Acessórios: Acessórios Celular
  - Outros: o resto (Mala, Embalagem…)
Pedido sem a categoria no item: a do cadastro do produto (`products.category`
→ `product_categories`); sem as duas, a família do SKU (`dg` + dígito =
celular, `u` = eletro, `a0NN` = acessório).

O item que decide é o PRIMEIRO item Uranyx da garantia (`itens`, na ordem do
pedido; o aparelho antes do fone do kit): uma garantia conta uma vez só. O SKU
vale sem o LOTE de estoque (`dg053.sp+a001.sp` → `dg053`), pela regra do
sistema (`estoque_familia.lote_de`: só as tags de lote; número e "4l" não são
lote — `uaf001m1.110` e `uaf001m1.220` são produtos diferentes).

Só leitura e só contagens. As porcentagens a tela calcula (uma conta só).
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import Text, and_, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder
from app.models.garantia import Garantia, GarantiaAtendimento
from app.models.pricing import PricingProduct
from app.models.product import Product, ProductCategory
from app.services import garantia as svc
from app.services.estoque_familia import lote_de

DEPARTAMENTOS = {
    "celular": "Celular",
    "eletro": "Eletro",
    "acessorios": "Acessórios",
    "outros": "Outros",
}
_RX_CELULAR = re.compile(r"^dg\d", re.I)
_RX_ELETRO = re.compile(r"^u", re.I)
_RX_ACESSORIO = re.compile(r"^a0\d{2}", re.I)
_LOTE = 1000


def _plano(texto: str | None) -> str:
    base = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return " ".join(base.lower().split())


def departamento_da_categoria(categoria: str | None) -> str | None:
    """'Celular Kit' → 'celular'; 'Eletro Usado' → 'eletro'; 'Acessórios
    Celular' → 'acessorios'; outra categoria → 'outros'; vazia → None. PURA."""
    n = _plano(categoria)
    if not n:
        return None
    if n.startswith("celular"):
        return "celular"
    if n.startswith("eletro"):
        return "eletro"
    if n.startswith("acessorio"):
        return "acessorios"
    return "outros"


def departamento_da_familia(sku: str | None) -> str:
    """Sem categoria nenhuma: pela família do SKU da Uranyx. PURA."""
    s = (sku or "").strip()
    if _RX_CELULAR.match(s):
        return "celular"
    if _RX_ACESSORIO.match(s):
        return "acessorios"
    if _RX_ELETRO.match(s):
        return "eletro"
    return "outros"


def produto_base(sku: str | None) -> str:
    """A 1ª peça sem o lote de estoque: `dg053.sp+a001.sp` → `dg053`;
    `uaf001m1.110` → `uaf001m1.110` (voltagem não é lote). PURA."""
    peca = (sku or "").strip().lower().split("+", 1)[0]
    lote = lote_de(peca)
    return peca[: -(len(lote) + 1)] if lote else peca


def nome_do_produto(descricao: str | None) -> str:
    """A descrição sem o acessório do kit ("A17 Pro Max - Laranja + Fone" →
    "A17 Pro Max - Laranja"). PURA."""
    return (descricao or "").split(" + ", 1)[0].strip()


def item_principal(itens: list[Any] | None) -> dict | None:
    """O item que decide o departamento: o APARELHO Uranyx (celular `dg`+dígito
    ou eletro `u`) antes do acessório Uranyx — o fone listado antes do celular
    no pedido não leva a compra para Acessórios (revisão de 28/10/2026) —;
    senão o 1º item Uranyx; senão o 1º item. PURA."""
    lista = [
        i for i in (itens or []) if isinstance(i, dict) and (i.get("sku") or i.get("descricao"))
    ]
    uranyx = [i for i in lista if i.get("uranyx")]
    aparelho = next(
        (i for i in uranyx if departamento_da_familia(i.get("sku")) in ("celular", "eletro")),
        None,
    )
    return aparelho or (uranyx[0] if uranyx else (lista[0] if lista else None))


@dataclass
class _Linha:
    """Uma linha da tabela: um produto da Tabela de Preços (ou um SKU fora dela)."""

    chave: str
    skus: list[str]
    nome: str | None
    na_tabela: bool
    vendidas: int = 0
    pediram: int = 0
    departamentos: Counter = field(default_factory=Counter)
    nomes: Counter = field(default_factory=Counter)
    status: Counter = field(default_factory=Counter)
    origens: Counter = field(default_factory=Counter)
    com_atendimento: int = 0
    hardware: int = 0
    software: int = 0
    pedidos: list[int] = field(default_factory=list)


async def _tabela_de_precos(session: AsyncSession) -> dict[str, tuple[str, str, list[str]]]:
    """SKU sem lote → (id da linha da Tabela de Preços, nome, os SKUs da linha).
    O SKU "puro" da linha vale antes do SKU de kit (`dg053+a003` também daria
    `dg053`)."""
    linhas = (
        await session.execute(select(PricingProduct.id, PricingProduct.sku, PricingProduct.name))
    ).all()
    puros: dict[str, tuple[str, str, list[str]]] = {}
    de_kit: dict[str, tuple[str, str, list[str]]] = {}
    for pid, skus, nome in linhas:
        lista = [t.strip().lower() for t in (skus or "").split(",") if t.strip()]
        bases = list(dict.fromkeys(produto_base(t) for t in lista if produto_base(t)))
        for t in lista:
            alvo = de_kit if "+" in t else puros
            alvo.setdefault(produto_base(t), (str(pid), nome, bases))
    return {**de_kit, **puros}


async def _categorias_dos_pedidos(
    session: AsyncSession, pares: set[tuple[str, str]]
) -> dict[tuple[str, str], str]:
    """(pedido, sku) → categoria do item no pedido do Bling."""
    if not pares:
        return {}
    numeros = sorted({p for p, _ in pares})
    saida: dict[tuple[str, str], str] = {}
    for i in range(0, len(numeros), _LOTE):
        linhas = await session.execute(
            select(BlingOrder.numero, BlingOrder.item_codigo, BlingOrder.categoria_nome).where(
                BlingOrder.numero.in_(numeros[i : i + _LOTE]),
                BlingOrder.categoria_nome.is_not(None),
            )
        )
        for numero, codigo, categoria in linhas.all():
            chave = (str(numero), (codigo or "").strip().lower())
            if chave in pares and (categoria or "").strip():
                saida[chave] = categoria.strip()
    return saida


async def _categorias_dos_produtos(session: AsyncSession, skus: set[str]) -> dict[str, str]:
    """sku → categoria do cadastro do produto (a do Bling, pelo id)."""
    if not skus:
        return {}
    lista = sorted(skus)
    saida: dict[str, str] = {}
    sku_norm = func.lower(func.btrim(Product.sku))
    for i in range(0, len(lista), _LOTE):
        linhas = await session.execute(
            select(sku_norm, ProductCategory.name)
            .join(
                ProductCategory,
                cast(ProductCategory.bling_category_id, Text) == func.btrim(Product.category),
            )
            .where(sku_norm.in_(lista[i : i + _LOTE]))
        )
        for sku, nome in linhas.all():
            if nome and sku not in saida:
                saida[sku] = nome.strip()
    return saida


async def resumo(
    session: AsyncSession, filtros: list, dia: date | None = None, *, pedido: list | None = None
) -> dict:
    """As vendas com garantia (os `filtros` da barra, já com escopo e sem as
    descartadas) e os PEDIDOS de garantia por produto da Tabela de Preços e por
    departamento. `pedido` = as condições a mais do pedido (o período pela data
    do pedido). Da situação, da origem e dos atendimentos, só os pedidos."""
    from app.services import garantia_pedido

    dia = dia or svc.hoje()
    # "Entregue — sem data no DaVinci": sem data de entrega, mas o Bling já dá
    # o pedido como entregue. Fica à parte do "Aguardando entrega" (a regra do
    # painel: nunca dizer "aguardando" de quem já recebeu).
    sem_data_entregue = and_(
        Garantia.data_inicio.is_(None), svc._pedido_entregue_no_bling().correlate(Garantia)
    ).label("entregue_sem_data")
    pediu = and_(garantia_pedido.filtro_pedido(), *(pedido or [])).label("pediu")
    registros = (
        await session.execute(
            select(
                Garantia.id,
                Garantia.pedido_bling,
                Garantia.itens,
                Garantia.data_inicio,
                Garantia.fim_hardware,
                Garantia.fim_software,
                Garantia.solicitacao_origem,
                pediu,
                sem_data_entregue,
            ).where(*filtros)
        )
    ).all()
    ids = [r.id for r in registros if r.pediu]
    atend: dict[int, Counter] = defaultdict(Counter)
    for i in range(0, len(ids), _LOTE):
        for gid, tipo, n in (
            await session.execute(
                select(
                    GarantiaAtendimento.garantia_id,
                    GarantiaAtendimento.tipo_problema,
                    func.count(),
                )
                .where(GarantiaAtendimento.garantia_id.in_(ids[i : i + _LOTE]))
                .group_by(GarantiaAtendimento.garantia_id, GarantiaAtendimento.tipo_problema)
            )
        ).all():
            atend[gid][tipo] += n

    principais = {r.id: item_principal(r.itens) for r in registros}
    pares = {
        (str(r.pedido_bling), (p.get("sku") or "").strip().lower())
        for r in registros
        if (p := principais[r.id]) and p.get("sku")
    }
    do_pedido = await _categorias_dos_pedidos(session, pares)
    do_cadastro = await _categorias_dos_produtos(session, {s for _, s in pares if s})
    tabela = await _tabela_de_precos(session)

    deps: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # chave → [vendidas, pediram]
    linhas: dict[str, _Linha] = {}
    for r in registros:
        item = principais[r.id] or {}
        sku = (item.get("sku") or "").strip().lower()
        categoria = do_pedido.get((str(r.pedido_bling), sku)) or do_cadastro.get(sku)
        dep = departamento_da_categoria(categoria) or departamento_da_familia(sku)
        base = produto_base(sku) or "(sem sku)"
        if base in tabela:
            pid, nome, skus = tabela[base]
            linha = linhas.setdefault(f"tabela:{pid}", _Linha(f"tabela:{pid}", skus, nome, True))
        else:
            linha = linhas.setdefault(f"sku:{base}", _Linha(f"sku:{base}", [base], None, False))
        linha.vendidas += 1
        linha.departamentos[dep] += 1
        deps[dep][0] += 1
        if nome := nome_do_produto(item.get("descricao")):
            linha.nomes[nome] += 1
        if not r.pediu:
            continue
        linha.pediram += 1
        deps[dep][1] += 1
        linha.pedidos.append(r.id)
        st = svc.status_em(r.data_inicio, r.fim_hardware, r.fim_software, dia)
        linha.status["entregue_sem_data" if r.entregue_sem_data else st] += 1
        linha.origens[r.solicitacao_origem or garantia_pedido.ORIGEM_PESSOA] += 1
        if a := atend.get(r.id):
            linha.com_atendimento += 1
            linha.hardware += a.get("hardware", 0)
            linha.software += a.get("software", 0)

    ordem = list(DEPARTAMENTOS)
    return {
        "vendidas": len(registros),
        "pediram": len(ids),
        "hoje": dia,
        "departamentos": [
            {"chave": chave, "nome": DEPARTAMENTOS[chave], "vendidas": v, "pediram": n}
            for chave, (v, n) in sorted(
                deps.items(), key=lambda x: (-x[1][1], -x[1][0], ordem.index(x[0]))
            )
        ],
        "produtos": [
            {
                "chave": li.chave,
                "departamento": li.departamentos.most_common(1)[0][0],
                "nome": li.nome or (li.nomes.most_common(1)[0][0] if li.nomes else li.skus[0]),
                "skus": li.skus,
                "na_tabela": li.na_tabela,
                "vendidas": li.vendidas,
                "pediram": li.pediram,
                "status": {
                    s: li.status.get(s, 0) for s in (*svc.STATUS_ROTULOS, "entregue_sem_data")
                },
                "origens": {o: li.origens.get(o, 0) for o in garantia_pedido.ORIGEM_ROTULOS},
                "com_atendimento": li.com_atendimento,
                "atendimentos_hardware": li.hardware,
                "atendimentos_software": li.software,
                "pedidos": li.pedidos,
            }
            for li in sorted(
                linhas.values(), key=lambda x: (-x.pediram, -x.vendidas, x.skus[0])
            )
        ],
    }
