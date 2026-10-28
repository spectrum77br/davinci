"""Resumo do Painel de Garantia (28/10/2026) — as garantias por DEPARTAMENTO.

Pedido do Eduardo: "a tela de resumo de cada garantia, separada por
departamento, ou seja eletro, celular etc, e o percentil de cada uma".

Departamento = a categoria do produto no Bling, a MESMA que o Faturamento usa
(`bling_orders.categoria_nome` do item do pedido — routers/financeiro.py):
  - Celular: Celular, Celular Kit, Celular Usado
  - Eletro: Eletro, Eletro Kit, Eletro Usado
  - Acessórios: Acessórios Celular
  - Outros: o resto (Mala, Embalagem…)
Pedido sem a categoria no item: a do cadastro do produto (`products.category`
→ `product_categories`); sem as duas, a família do SKU (`dg` + dígito =
celular, `u` = eletro, `a0NN` = acessório) — e a categoria aparece como
"sem categoria no Bling" para a pessoa saber de onde veio.

O item que decide é o PRIMEIRO item Uranyx da garantia (`itens`, na ordem do
pedido): uma garantia conta uma vez só. O "produto" é a 1ª peça do SKU sem o
LOTE de estoque (`dg053.sp+a001.sp` → `dg053`), pela regra do sistema
(`estoque_familia.lote_de`: só as tags de lote; número e "4l" não são lote —
`uaf001m1.110` e `uaf001m1.220` são produtos diferentes), com a descrição
mais comum do item.

"Cliente escreveu" = alguma conversa ligada à garantia tem mensagem do
CLIENTE desde o dia da compra. Conversa ligada não basta: 4.179 garantias
tinham conversa e só 1.585 tinham fala do cliente depois da compra (28/10) —
o resto era a mensagem automática da loja ou do sistema.

Só leitura. As porcentagens a tela calcula das contagens (uma conta só).
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
from app.models.atendimento import AtendimentoMensagem
from app.models.garantia import Garantia, GarantiaAtendimento, GarantiaConversa
from app.models.product import Product, ProductCategory
from app.services import garantia as svc
from app.services.estoque_familia import lote_de

DEPARTAMENTOS = {
    "celular": "Celular",
    "eletro": "Eletro",
    "acessorios": "Acessórios",
    "outros": "Outros",
}
SEM_CATEGORIA = "sem categoria no Bling"
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
class _Produto:
    base: str
    total: int = 0
    cliente_escreveu: int = 0
    com_atendimento: int = 0
    nomes: Counter = field(default_factory=Counter)


@dataclass
class _Departamento:
    chave: str
    total: int = 0
    status: Counter = field(default_factory=Counter)
    cliente_escreveu: int = 0
    com_atendimento: int = 0
    hardware: int = 0
    software: int = 0
    categorias: Counter = field(default_factory=Counter)
    produtos: dict[str, _Produto] = field(default_factory=dict)


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


async def resumo(session: AsyncSession, filtros: list, dia: date | None = None) -> dict:
    """As garantias (com os `filtros` da lista já com escopo e sem as
    descartadas) por departamento: totais, status, quem escreveu, atendimentos
    registrados, categorias do Bling e produtos. Só contagens."""
    dia = dia or svc.hoje()
    # A compra (o Bling grava o dia, às 00:00 de São Paulo) e a fala do
    # cliente desde então numa conversa ligada.
    # `correlate(Garantia)` explícito: aninhada dentro do EXISTS, a
    # subconsulta da compra NÃO se correlaciona sozinha — virava
    # `FROM bling_orders, garantias` e pegava a compra mais antiga de TODAS as
    # garantias (achado na prévia de 28/10: 1.619 em vez de 1.585).
    compra = (
        select(func.min(BlingOrder.data))
        .where(BlingOrder.numero == Garantia.pedido_bling)
        .correlate(Garantia)
        .scalar_subquery()
    )
    m = AtendimentoMensagem
    cliente_escreveu = (
        select(GarantiaConversa.garantia_id)
        .join(m, m.conversa_id == GarantiaConversa.conversa_id)
        .where(
            GarantiaConversa.garantia_id == Garantia.id,
            m.autor == "cliente",
            func.coalesce(m.enviada_em, m.created_at) >= func.coalesce(compra, Garantia.criado_em),
        )
        .correlate(Garantia)
        .exists()
        .label("cliente_escreveu")
    )
    # "Entregue — sem data no DaVinci": sem data de entrega, mas o Bling já dá
    # o pedido como entregue. Fica à parte do "Aguardando entrega" (a regra do
    # painel: nunca dizer "aguardando" de quem já recebeu).
    sem_data_entregue = and_(
        Garantia.data_inicio.is_(None), svc._pedido_entregue_no_bling().correlate(Garantia)
    ).label("entregue_sem_data")
    linhas = (
        await session.execute(
            select(
                Garantia.id,
                Garantia.pedido_bling,
                Garantia.itens,
                Garantia.data_inicio,
                Garantia.fim_hardware,
                Garantia.fim_software,
                cliente_escreveu,
                sem_data_entregue,
            ).where(*filtros)
        )
    ).all()
    ids = [r.id for r in linhas]
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

    principais = {r.id: item_principal(r.itens) for r in linhas}
    pares = {
        (str(r.pedido_bling), (p.get("sku") or "").strip().lower())
        for r in linhas
        if (p := principais[r.id]) and p.get("sku")
    }
    do_pedido = await _categorias_dos_pedidos(session, pares)
    do_cadastro = await _categorias_dos_produtos(session, {s for _, s in pares if s})

    deps: dict[str, _Departamento] = {}
    for r in linhas:
        item = principais[r.id] or {}
        sku = (item.get("sku") or "").strip().lower()
        categoria = do_pedido.get((str(r.pedido_bling), sku)) or do_cadastro.get(sku)
        chave = departamento_da_categoria(categoria) or departamento_da_familia(sku)
        d = deps.setdefault(chave, _Departamento(chave))
        d.total += 1
        st = svc.status_em(r.data_inicio, r.fim_hardware, r.fim_software, dia)
        d.status["entregue_sem_data" if r.entregue_sem_data else st] += 1
        d.categorias[categoria or SEM_CATEGORIA] += 1
        a = atend.get(r.id) or Counter()
        if r.cliente_escreveu:
            d.cliente_escreveu += 1
        if a:
            d.com_atendimento += 1
            d.hardware += a.get("hardware", 0)
            d.software += a.get("software", 0)
        base = produto_base(sku) or "(sem sku)"
        p = d.produtos.setdefault(base, _Produto(base))
        p.total += 1
        p.cliente_escreveu += 1 if r.cliente_escreveu else 0
        p.com_atendimento += 1 if a else 0
        if nome := nome_do_produto(item.get("descricao")):
            p.nomes[nome] += 1

    ordem = list(DEPARTAMENTOS)
    return {
        "total": len(linhas),
        "hoje": dia,
        "departamentos": [
            {
                "chave": d.chave,
                "nome": DEPARTAMENTOS[d.chave],
                "total": d.total,
                "status": {
                    s: d.status.get(s, 0) for s in (*svc.STATUS_ROTULOS, "entregue_sem_data")
                },
                "cliente_escreveu": d.cliente_escreveu,
                "com_atendimento": d.com_atendimento,
                "atendimentos_hardware": d.hardware,
                "atendimentos_software": d.software,
                "categorias": [
                    {"nome": nome, "total": n, "sem_categoria": nome == SEM_CATEGORIA}
                    for nome, n in d.categorias.most_common()
                ],
                "produtos": [
                    {
                        "base": p.base,
                        "nome": p.nomes.most_common(1)[0][0] if p.nomes else p.base,
                        "total": p.total,
                        "cliente_escreveu": p.cliente_escreveu,
                        "com_atendimento": p.com_atendimento,
                    }
                    for p in sorted(d.produtos.values(), key=lambda x: (-x.total, x.base))
                ],
            }
            for d in sorted(deps.values(), key=lambda x: (-x.total, ordem.index(x.chave)))
        ],
    }
