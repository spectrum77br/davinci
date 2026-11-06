"""Resumo do Painel de Garantia (28/10/2026) — a garantia POR PRODUTO.

Pedidos do Eduardo (28/10): "o resumo de cada garantia, separada por
departamento (eletro, celular…) e o percentil de cada uma"; "a garantia é só
para o pessoal que solicita"; e "vamos fazer a garantia por produto, pode usar
a nossa Tabela de Preços, aba Produtos, como referência… é tudo relacionado à
garantia". Então: uma linha por PRODUTO da Tabela de Preços (o modelo, com as
cores juntas — `pricing_products.sku` é a lista de SKUs da linha), com as
vendas com garantia, os PEDIDOS de garantia, de onde veio o pedido e o que
aconteceu com o aparelho. O produto vendido que não está na Tabela de Preços
entra pelo SKU, marcado "fora da Tabela de Preços".

As 3 partes do painel (04/11/2026, "resumo - visão do produto e o que temos
já dividido por produtos; garantia - todas as garantias; atendimento - só o
que realmente está em atendimento no momento"; "no mesmo estilo da tabela de
preços: categorias em cima: celular, mala e eletro"). Os departamentos são os
da Tabela de Preços — Celular, Mala, Eletro:
  - a linha da Tabela de Preços vai para a RAIZ do segmento dela (a aba da
    Tabela: o segmento "acessorios" debaixo de "celular" é Celular);
  - a linha fora da Tabela vai para o departamento da maioria das vendas
    dela; cada venda pela categoria do item no pedido do Bling (a MESMA que o
    Faturamento usa, `bling_orders.categoria_nome`: Celular* e Acessório* =
    Celular, Eletro* = Eletro, Mala* = Mala), senão a do cadastro do produto
    (`products.category` → `product_categories`), senão a família do SKU
    (`dg` + dígito e `a0NN` = celular, `u` = eletro, `b`/`bp` + dígito =
    mala); sem nada, Celular.
A garantia vai para o departamento da linha dela. O mesmo mapa (garantia →
linha → departamento) serve o Resumo e a lista (`mapa_das_linhas`).

O item que decide é o APARELHO Uranyx da garantia (`item_principal`): uma
garantia conta uma vez só. O SKU vale sem o LOTE de estoque (`dg053.sp+a001.sp`
→ `dg053`), pela regra do sistema (`estoque_familia.lote_de`: só as tags de
lote; número e "4l" não são lote — `uaf001m1.110` e `uaf001m1.220` são
produtos diferentes).

Vendas (04/11/2026, "vendidas → total das vendas; saldo → valor de saldo de
todos os itens com base na planilha de rentabilidade; % → (manutenção →
gastos/saldo) × 100"): o SALDO é a conta da planilha de rentabilidade — Base −
Comissão − Frete do pedido no Bling, o "Saldo Bling" da Margem — na parte da
linha (`saldo_da_linha`). A % a tela calcula (gasto ÷ saldo × 100).

Manutenção (30/10/2026, "o que realmente foi perdido e o que está na
manutenção"; 04/11/2026, "o gasto é baseado na tabela de reembolso"): dos que
PEDIRAM, os consertos e os perdidos contam pelo aparelho da garantia na tela
Devoluções (só a devolução do mesmo produto — o fone do kit no mesmo pedido
fica de fora); o GASTO em reais vem da tela Reembolso (`gasto_dos_reembolsos`).
Os valores em reais só para admin.

Só leitura.
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import Text, and_, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder
from app.models.atendimento import AtendimentoReclamacao
from app.models.devolution import Devolution
from app.models.garantia import Garantia
from app.models.pricing import PricingProduct
from app.models.product import Product, ProductCategory
from app.models.refund import Refund
from app.models.segment import Segment
from app.services import garantia as svc
from app.services.estoque_familia import lote_de

# As pílulas da tela, nesta ordem (04/11/2026: "celular, mala e eletro").
DEPARTAMENTOS = {
    "celular": "Celular",
    "mala": "Mala",
    "eletro": "Eletro",
}
# Sem categoria nem família conhecida: a linha Uranyx é quase toda celular.
DEPARTAMENTO_PADRAO = "celular"
_RX_CELULAR = re.compile(r"^dg\d", re.I)
_RX_ELETRO = re.compile(r"^u", re.I)
_RX_ACESSORIO = re.compile(r"^a0\d{2}", re.I)
_RX_MALA = re.compile(r"^bp?\d", re.I)
_LOTE = 1000
# O mapa garantia → linha → departamento fica guardado por pouco tempo (lê
# todas as garantias); muda a garantia ou a Tabela de Preços, refaz na hora.
CACHE_SEGUNDOS = 60
# A coluna da tela de cada origem do pedido (30/10/2026, "se eu clicar em
# devolução aparecem as 4 da devolução, já vem filtrado"): o número da célula
# abre só aqueles pedidos. Atendimento, cadastro à mão e a equipe não têm
# coluna (04/11/2026: "outros pode excluir").
_COLUNAS_DA_ORIGEM = ("mensagem", "sac", "devolucao")

# ── Manutenção (30/10/2026) ──────────────────────────────────────────────────
# O aparelho da garantia na tela Devoluções (uma linha por unidade):
#   - no técnico: condição Manutenção, ainda sem o destino da volta (a regra
#     de "pendente" do próprio Devoluções — devolution_stock_return);
#   - consertado: passou pela manutenção e voltou ao estoque (Novo, Usado ou
#     Trocado; ou o destino Novo/Usado do modal da Manutenção);
#   - perdido: Extraviado ou Sucata (ou o destino Sucata do modal).
# Uma garantia fica numa só: perdido > no técnico > consertado.
#
# Seguiram (30/10/2026, "no chat tem muitos que pediram a garantia, mas não
# foi para frente"; "se pediram pelo chat e ocasionou em problema, reembolso
# etc. também entra — reclamação não"): o pedido de garantia que foi adiante —
#   - o aparelho voltou: a devolução lançada na tela Devoluções, com ou sem
#     conserto (a sem condição ainda também; "Não devolvido" e "Entregue", o
#     cliente ficou com ele, não);
#   - reembolso ao cliente: o Reembolso tipo "Cliente" do pedido, ou a
#     plataforma pagou (Shopee ACCEPTED/REFUND_PAID, TikTok SUCCESS/COMPLETE,
#     ML decidido a favor do comprador);
#   - devolução a caminho: aceita na plataforma, com o aparelho voltando
#     (Shopee PROCESSING, TikTok esperando o comprador enviar ou já enviado,
#     devolução do ML aberta) — "conta já"; cancelada depois, sai sozinha.
# NÃO contam: só pedir no chat; reclamação ou disputa ainda sem decisão
# (Shopee JUDGING/SELLER_DISPUTE, reclamação/mediação aberta do ML); devolução
# pedida e ainda não aceita (REQUESTED, PENDING), cancelada ou recusada. O
# reembolso pago conta sempre; o "a caminho" só pelo caso mais novo do pedido
# (`_decisao_dos_casos`).
_PERDIDO = ("Extraviado", "Sucata")
_DE_VOLTA = ("Novo", "Usado", "Trocado")
_NAO_VOLTOU = ("Não devolvido", "Entregue")
_CUSTO_DE_MENTIRA = 1.0
# O status da plataforma que é "foi adiante" (Shopee e TikTok; o ML pela
# decisão — `_caso_seguiu`): a caminho = o aparelho voltando; reembolso = pago.
_PLATAFORMA_SEGUIU = {
    "shopee": {"PROCESSING": "a_caminho", "ACCEPTED": "reembolso", "REFUND_PAID": "reembolso"},
    "tiktok": {
        "AWAITING_BUYER_SHIP": "a_caminho",
        "BUYER_SHIPPED_ITEM": "a_caminho",
        "RETURN_OR_REFUND_REQUEST_SUCCESS": "reembolso",
        "RETURN_OR_REFUND_REQUEST_COMPLETE": "reembolso",
    },
}
# A reclamação ou a mediação ainda aberta na plataforma: ela está decidindo
# (o Atendimento do painel — services/garantia_atendimento.py).
TIPOS_DECIDINDO = ("reclamacao", "mediacao")

# ── Gasto (04/11/2026, "o gasto é baseado na tabela de reembolso") ───────────
# As linhas da tela Reembolso de quem pediu garantia, conferidas ou não. Cada
# tipo vale uma conta (nunca negativa):
#   - Manutenção: o conserto — o reembolso negativo (o custo do conserto entra
#     como débito); o −1 é o valor que se digita para tirar o aparelho da
#     Manutenção (a tela não aceita vazio nem zero), não é gasto;
#   - Extraviado/Sucata: o aparelho perdido — o prejuízo menos o que a
#     plataforma já devolveu (o reembolso positivo);
#   - Cliente: o dinheiro devolvido ao cliente (o reembolso negativo).
# Logística, Frete e a linha sem tipo nunca entram. A linha que nasceu junto
# com a devolução de OUTRO produto do pedido (o fone do kit) fica de fora.
TIPOS_DO_GASTO = ("Manutenção", "Extraviado", "Sucata", "Cliente")

# ── Saldo (04/11/2026, "saldo de todos os itens com base na planilha de
# rentabilidade") ──────────────────────────────────────────────────────────────
# Base − Comissão − Frete − Custo dos produtos do pedido no Bling: a MESMA
# conta do robô da rentabilidade (05/11/2026: "usar o mesmo cálculo da
# rentabilidade, que na teoria é somente o que sobrou"). Com False, fica só o
# Base − Comissão − Frete (o "Saldo Bling" da Margem). O robô troca a base, a
# comissão e o frete do pedido da Amazon pelos da API da Amazon; aqui ficam os
# do Bling (139 das 6.474 vendas com garantia em 05/11).
SALDO_MENOS_CUSTO = True
# O pedido cancelado ou excluído do Bling não tem saldo.
_SEM_SALDO = ("12", "excluido")


@dataclass
class Desfecho:
    """O que aconteceu com o aparelho da garantia (a devolução dele) e o que
    foi gasto com ele."""

    # no_tecnico | consertado | perdido | devolvido — o aparelho na tela
    # Devoluções; reembolso | a_caminho — foi adiante só na plataforma ou no
    # Reembolso (sem a devolução lançada).
    situacao: str | None = None
    condicao: str | None = None  # a condição que decidiu (Extraviado, Novo…)
    # O gasto, da tela Reembolso (`gasto_dos_reembolsos`).
    conserto: float = 0.0
    perdido: float = 0.0
    cliente: float = 0.0
    prazo: datetime | None = None  # o prazo (30 dias) de quem está no técnico
    vencido: bool = False
    sem_custo: bool = False  # consertado sem o custo do conserto lançado
    devolveu: bool = False  # o aparelho voltou
    seguiu: bool = False  # foi adiante: voltou, voltando ou reembolso
    # Desde quando a situação está assim: a devolução lançada (no técnico,
    # devolvido) ou o caso da plataforma (a caminho).
    desde: datetime | None = None
    # A reclamação/mediação aberta na plataforma mais antiga do pedido.
    decidindo_desde: datetime | None = None
    # As linhas da tela Reembolso que entraram no gasto (cada uma conta uma vez).
    reembolsos: list[Any] = field(default_factory=list)

    @property
    def gasto(self) -> float:
        return round(self.conserto + self.perdido + self.cliente, 2)


@dataclass
class _Fora:
    """O pedido de garantia na plataforma e no Reembolso, fora da tela
    Devoluções."""

    o_que: str | None = None  # reembolso | a_caminho
    desde: datetime | None = None  # o caso que pôs o aparelho "a caminho"
    decidindo_desde: datetime | None = None


def _linha_do_sku(sku: str | None, tabela: dict[str, tuple[str, str, list[str]]]) -> str:
    """A linha da tabela (o produto) de um SKU: a da Tabela de Preços ou o
    próprio SKU sem o lote."""
    base = produto_base(sku) or "(sem sku)"
    return f"tabela:{tabela[base][0]}" if base in tabela else f"sku:{base}"


async def _devolucoes(session: AsyncSession, pedidos: set[str]) -> dict[str, list[Any]]:
    """pedido → as linhas dele na tela Devoluções (só o que decide o desfecho)."""
    numeros = sorted(p for p in pedidos if p)
    saida: dict[str, list[Any]] = defaultdict(list)
    for i in range(0, len(numeros), _LOTE):
        linhas = await session.execute(
            select(
                Devolution.pedido_bling,
                Devolution.sku,
                Devolution.condicao_produto,
                Devolution.manutencao,
                Devolution.manutencao_destino,
                Devolution.custo_produto,
                Devolution.custo_manutencao,
                Devolution.prazo,
                Devolution.created_at,
            )
            .where(Devolution.pedido_bling.in_(numeros[i : i + _LOTE]))
            .order_by(Devolution.created_at, Devolution.id)
        )
        for d in linhas.all():
            saida[str(d.pedido_bling)].append(d)
    return saida


async def _reembolsos(
    session: AsyncSession, pedidos: list[tuple[str, str | None]]
) -> dict[str, list[Any]]:
    """pedido do Bling da garantia → as linhas dele na tela Reembolso: pelo
    pedido do Bling; a linha SEM o pedido do Bling, pelo da plataforma. Todos
    os tipos (quem usa escolhe)."""
    do_bling = sorted({b for b, _ in pedidos if b})
    do_mkt: dict[str, set[str]] = defaultdict(set)
    for b, m in pedidos:
        if b and (m or "").strip():
            do_mkt[m.strip()].add(b)
    mkts = sorted(do_mkt)
    r = Refund
    pb, pm = func.btrim(r.pedido_bling), func.btrim(r.pedido_marketplace)
    colunas = (
        r.id,
        r.tipo,
        r.prejuizo,
        r.reembolso,
        r.created_at,
        pb.label("bling"),
        pm.label("mkt"),
    )
    saida: dict[str, dict[Any, Any]] = defaultdict(dict)
    for i in range(0, len(do_bling), _LOTE):
        for x in await session.execute(select(*colunas).where(pb.in_(do_bling[i : i + _LOTE]))):
            saida[x.bling][x.id] = x
    sem_bling = func.coalesce(pb, "") == ""
    for i in range(0, len(mkts), _LOTE):
        for x in await session.execute(
            select(*colunas).where(sem_bling, pm.in_(mkts[i : i + _LOTE]))
        ):
            for b in do_mkt.get(x.mkt, set()):
                saida[b][x.id] = x
    return {
        b: sorted(linhas.values(), key=lambda x: (x.created_at, str(x.id)))
        for b, linhas in saida.items()
    }


def valor_do_reembolso(
    tipo: str | None, prejuizo: Any, reembolso: Any
) -> tuple[float, float, float]:
    """(conserto, perda, reembolso ao cliente) de UMA linha da tela Reembolso —
    nunca negativo; tipo fora de TIPOS_DO_GASTO não é gasto. PURA."""
    prej, reemb = float(prejuizo or 0), float(reembolso or 0)
    if tipo == "Manutenção":
        return (-reemb if reemb < -_CUSTO_DE_MENTIRA else 0.0), 0.0, 0.0
    if tipo in _PERDIDO:
        return 0.0, max(prej - max(reemb, 0.0), 0.0), 0.0
    if tipo == "Cliente":
        return 0.0, 0.0, max(0.0, -reemb)
    return 0.0, 0.0, 0.0


def gasto_dos_reembolsos(
    reembolsos: list[Any],
    devolucoes: list[Any],
    chave: str,
    tabela: dict[str, tuple[str, str, list[str]]],
) -> tuple[float, float, float, list[Any]]:
    """O gasto do aparelho da garantia (`chave` = a linha) na tela Reembolso:
    (conserto, perda, reembolso ao cliente, as linhas que entraram). A linha
    que nasceu junto com a devolução de OUTRO produto do pedido (o mesmo
    `created_at` — o POST de Devoluções grava as duas no mesmo commit — e a
    devolução com SKU) fica de fora: o fone do kit não é gasto do celular.
    PURA."""
    conserto = perda = cliente = 0.0
    usados: list[Any] = []
    for r in reembolsos:
        if r.tipo not in TIPOS_DO_GASTO:
            continue
        linhas = {
            _linha_do_sku(d.sku, tabela)
            for d in devolucoes
            if getattr(d, "created_at", None) is not None
            and d.created_at == r.created_at
            and (d.sku or "").strip()
        }
        if linhas and chave not in linhas:
            continue
        c, p, cl = valor_do_reembolso(r.tipo, r.prejuizo, r.reembolso)
        conserto, perda, cliente = conserto + c, perda + p, cliente + cl
        usados.append(r.id)
    return round(conserto, 2), round(perda, 2), round(cliente, 2), usados


def desfecho(
    devolucoes: list[Any],
    chave: str,
    tabela: dict[str, tuple[str, str, list[str]]],
    dia: date,
    fora: str | None = None,
    reembolsos: list[Any] | tuple = (),
    *,
    fora_desde: datetime | None = None,
    decidindo_desde: datetime | None = None,
) -> Desfecho:
    """O desfecho do aparelho da garantia: só as devoluções do mesmo produto
    (`chave` = a linha da tabela) — o fone do kit no mesmo pedido fica de
    fora; a devolução sem SKU conta (é do pedido). O prazo vence pelo dia em
    São Paulo (`dia`), como na tela Devoluções: no dia do prazo é "vence hoje".
    `fora` = o pedido foi adiante fora da tela Devoluções (`reembolso` ou
    `a_caminho` — `_seguiram_fora_da_devolucao`), desde `fora_desde`. O gasto
    sai dos `reembolsos` (a tela Reembolso). PURA."""
    meus = [
        d for d in devolucoes if not (d.sku or "").strip() or _linha_do_sku(d.sku, tabela) == chave
    ]
    perdidos = [
        d
        for d in meus
        if d.condicao_produto in _PERDIDO
        or (d.condicao_produto == "Manutenção" and d.manutencao_destino == "Sucata")
    ]
    no_tecnico = [
        d for d in meus if d.condicao_produto == "Manutenção" and not d.manutencao_destino
    ]

    def de_verdade(d: Any) -> bool:
        return (d.custo_manutencao or 0) > _CUSTO_DE_MENTIRA

    def lancada(d: Any) -> datetime | None:
        return getattr(d, "created_at", None)

    # O custo de conserto sem a marca "passou em manutenção" (linha antiga)
    # também é conserto.
    consertados = [
        d
        for d in meus
        if (d.condicao_produto in _DE_VOLTA and (d.manutencao or de_verdade(d)))
        or (d.condicao_produto == "Manutenção" and d.manutencao_destino in ("Novo", "Usado"))
    ]

    conserto, perda, cliente, usados = gasto_dos_reembolsos(
        list(reembolsos), devolucoes, chave, tabela
    )
    f = Desfecho(
        conserto=conserto,
        perdido=perda,
        cliente=cliente,
        reembolsos=usados,
        decidindo_desde=decidindo_desde,
    )
    if perdidos:
        d = perdidos[0]
        f.situacao = "perdido"
        f.condicao = (
            d.manutencao_destino if d.condicao_produto == "Manutenção" else d.condicao_produto
        )
        f.desde = lancada(d)
    elif no_tecnico:
        # O prazo só de quem está no técnico (a garantia em Perdidos não entra
        # no "vencido").
        f.situacao, f.condicao = "no_tecnico", "Manutenção"
        prazos = [d.prazo for d in no_tecnico if d.prazo]
        f.prazo = min(prazos) if prazos else None
        f.vencido = f.prazo is not None and f.prazo.astimezone(svc.SAO_PAULO).date() < dia
        desde = [x for d in no_tecnico if (x := lancada(d)) is not None]
        f.desde = min(desde) if desde else None
    elif consertados:
        d = consertados[0]
        f.situacao = "consertado"
        f.condicao = (
            d.manutencao_destino if d.condicao_produto == "Manutenção" else d.condicao_produto
        )
        # Sem o conserto na tela Reembolso (só o R$ 1 de tirar da Manutenção).
        f.sem_custo = not f.conserto
        f.desde = lancada(d)
    voltaram = [d for d in meus if d.condicao_produto not in _NAO_VOLTOU]
    f.devolveu = bool(voltaram)
    if f.devolveu and not f.situacao:
        # Voltou sem passar pela manutenção (ou ainda sem a condição lançada).
        f.situacao, f.condicao = "devolvido", voltaram[0].condicao_produto
        f.desde = lancada(voltaram[0])
    f.seguiu = f.devolveu or fora is not None
    if fora and not f.situacao:
        f.situacao = fora
        f.desde = fora_desde
    return f


def _caso_seguiu(plataforma: str, tipo: str, status: str | None, dados: Any) -> str | None:
    """O que o caso da plataforma diz, sozinho: `reembolso` (pago),
    `a_caminho` (o aparelho voltando) ou None (reclamação, disputa sem
    decisão, pedido ainda não aceito, cancelado ou recusado)."""
    dados = dados if isinstance(dados, dict) else {}
    if plataforma == "ml":
        resolucao = dados.get("resolucao")
        beneficiados = resolucao.get("beneficiado") if isinstance(resolucao, dict) else None
        if isinstance(beneficiados, list) and "complainant" in {
            str(b).lower() for b in beneficiados
        }:
            return "reembolso"
        return "a_caminho" if tipo == "devolucao" and status == "opened" else None
    return _PLATAFORMA_SEGUIU.get(plataforma, {}).get(status or "")


def _decisao_dos_casos(casos: list[Any]) -> tuple[str | None, Any | None]:
    """Os casos da plataforma de UM pedido → (`reembolso` | `a_caminho` | None,
    o caso que decidiu). O reembolso pago conta sempre (é fato, não volta
    atrás); o "a caminho" só pelo caso mais novo da Shopee e da TikTok — a
    Logística guarda um caso por pedido e o novo encerra o anterior, que fica
    parado. A marca `substituida_por` não serve para isso: dois casos podem
    ficar marcados um pelo outro (revisão 30/10). O ML não troca caso: vale
    cada um."""
    o_que = {c.id: _caso_seguiu(c.plataforma, c.tipo, c.status, c.dados) for c in casos}
    pagos = [c for c in casos if o_que[c.id] == "reembolso"]
    if pagos:
        return "reembolso", min(pagos, key=lambda c: c.created_at)
    for plataforma in sorted({c.plataforma for c in casos}):
        dela = [c for c in casos if c.plataforma == plataforma]
        if plataforma == "ml":
            voltando = [c for c in dela if o_que[c.id] == "a_caminho"]
            if voltando:
                return "a_caminho", min(voltando, key=lambda c: c.created_at)
            continue
        # O mais novo é o que entrou por último (a Logística grava o caso novo
        # quando ele aparece); empate, o aberto por último na plataforma.
        mais_novo = max(dela, key=lambda c: (c.created_at, c.aberta_em or c.created_at))
        if o_que[mais_novo.id] == "a_caminho":
            return "a_caminho", mais_novo
    return None, None


def _casos_do_pedido(casos: list[Any]) -> str | None:
    """`reembolso` | `a_caminho` | None — `_decisao_dos_casos` sem o caso."""
    return _decisao_dos_casos(casos)[0]


async def _seguiram_fora_da_devolucao(
    session: AsyncSession,
    pedidos: list[tuple[str, str | None]],
    reembolsos: dict[str, list[Any]],
) -> dict[str, _Fora]:
    """pedido do Bling → o pedido de garantia fora da tela Devoluções: foi
    adiante (o Reembolso tipo "Cliente" do pedido — dinheiro devolvido ao
    cliente — ou os casos da plataforma, `_decisao_dos_casos`) e a
    reclamação/mediação ainda aberta na plataforma. Os casos ligam pelo pedido
    do Bling e pelo da plataforma (o do ML pode ser o pack). `reembolsos` = as
    linhas da tela Reembolso dos pedidos (`_reembolsos`)."""
    do_bling = sorted({b for b, _ in pedidos if b})
    do_mkt: dict[str, set[str]] = defaultdict(set)
    for b, m in pedidos:
        if b and (m or "").strip():
            do_mkt[m.strip()].add(b)
    mkts = sorted(do_mkt)
    saida: dict[str, _Fora] = {}
    for b in do_bling:
        if any(x.tipo == "Cliente" for x in reembolsos.get(b, [])):
            saida[b] = _Fora("reembolso")

    # Os casos de cada pedido (uma consulta por chave: o pedido da plataforma
    # usa o índice; o pack e o pedido do Bling vêm dos dados).
    r = AtendimentoReclamacao
    colunas = (
        r.id,
        r.plataforma,
        r.tipo,
        r.status,
        r.pedido_marketplace,
        r.dados,
        r.aberta_em,
        r.encerrada_em,
        r.created_at,
    )
    no_bling = set(do_bling)
    lotes_mkt = [mkts[i : i + _LOTE] for i in range(0, len(mkts), _LOTE)]
    filtros = [r.pedido_marketplace.in_(lote) for lote in lotes_mkt]
    filtros += [r.dados["pack_id"].astext.in_(lote) for lote in lotes_mkt]
    filtros += [
        r.dados["pedido_bling"].astext.in_(do_bling[i : i + _LOTE])
        for i in range(0, len(do_bling), _LOTE)
    ]
    casos: dict[str, dict[Any, Any]] = defaultdict(dict)  # pedido → id do caso → caso
    for onde in filtros:
        for c in await session.execute(select(*colunas).where(onde)):
            dados = c.dados if isinstance(c.dados, dict) else {}
            numeros = set(do_mkt.get((c.pedido_marketplace or "").strip(), set()))
            numeros |= do_mkt.get(str(dados.get("pack_id") or "").strip(), set())
            if (b := str(dados.get("pedido_bling") or "").strip()) in no_bling:
                numeros.add(b)
            for b in numeros:
                casos[b][c.id] = c
    for b, do_pedido in casos.items():
        lista = list(do_pedido.values())
        f = saida.get(b) or _Fora()
        if f.o_que != "reembolso":
            o_que, caso = _decisao_dos_casos(lista)
            if o_que:
                f.o_que, f.desde = o_que, caso.created_at if o_que == "a_caminho" else None
        abertas = [
            c.created_at for c in lista if c.tipo in TIPOS_DECIDINDO and c.encerrada_em is None
        ]
        if abertas:
            f.decidindo_desde = min(abertas)
        if f.o_que or f.decidindo_desde:
            saida[b] = f
    return saida


async def desfechos(
    session: AsyncSession,
    garantias: list[Any],
    dia: date,
    *,
    tabela: dict[str, tuple[str, str, list[str]]] | None = None,
) -> dict[int, Desfecho]:
    """O desfecho do aparelho de cada garantia (a lista, o detalhe e o
    Atendimento do painel). Só as que têm alguma coisa (situação, gasto ou a
    plataforma decidindo). `garantias` = linhas com id, pedido_bling,
    pedido_marketplace e itens; `tabela` = a Tabela de Preços já lida."""
    devs = await _devolucoes(session, {str(g.pedido_bling) for g in garantias})
    pares = [(str(g.pedido_bling), g.pedido_marketplace) for g in garantias]
    reembolsos = await _reembolsos(session, pares)
    fora = await _seguiram_fora_da_devolucao(session, pares, reembolsos)
    if not devs and not fora and not reembolsos:
        return {}
    if tabela is None:
        tabela = await _tabela_de_precos(session)
    saida: dict[int, Desfecho] = {}
    for g in garantias:
        pedido = str(g.pedido_bling)
        dev, outro, rs = devs.get(pedido, []), fora.get(pedido), reembolsos.get(pedido, [])
        if not dev and not outro and not rs:
            continue
        item = item_principal(g.itens) or {}
        f = desfecho(
            dev,
            _linha_do_sku(item.get("sku"), tabela),
            tabela,
            dia,
            outro.o_que if outro else None,
            rs,
            fora_desde=outro.desde if outro else None,
            decidindo_desde=outro.decidindo_desde if outro else None,
        )
        if f.situacao or f.gasto or f.decidindo_desde:
            saida[g.id] = f
    return saida


def _reais(v: float) -> str:
    return "R$ " + f"{v:,.2f}".replace(",", "x").replace(".", ",").replace("x", ".")


def rotulo_do_desfecho(f: Desfecho, dia: date, com_valor: bool) -> str | None:
    """O selo da garantia na lista e no detalhe: "No técnico · faltam 5 dias",
    "Consertado (Novo)", "Perdido (Extraviado)"; o gasto em reais (o da tela
    Reembolso, o mesmo do Resumo) só para admin. Curto, para caber na lista."""
    if f.situacao == "no_tecnico":
        texto = "No técnico"
        if f.prazo:
            faltam = (f.prazo.astimezone(svc.SAO_PAULO).date() - dia).days
            texto += (
                f" · faltam {faltam} {'dia' if faltam == 1 else 'dias'}"
                if faltam > 0
                else " · vence hoje"
                if faltam == 0
                else f" · vencido há {-faltam} {'dia' if faltam == -1 else 'dias'}"
            )
    elif f.situacao == "consertado":
        texto = f"Consertado ({f.condicao})"
        if f.sem_custo:
            texto += " · sem custo lançado"
    elif f.situacao == "perdido":
        texto = f"Perdido ({f.condicao})"
    elif f.situacao == "devolvido":
        texto = f"Devolvido ({f.condicao})" if f.condicao else "Devolvido · sem condição lançada"
    elif f.situacao == "reembolso":
        texto = "Reembolso ao cliente"
    elif f.situacao == "a_caminho":
        texto = "Devolução a caminho"
    elif com_valor and f.gasto:
        texto = "Conserto" if f.conserto else "Gasto"
    else:
        return None
    if com_valor and f.gasto:
        texto += f" · {_reais(f.gasto)}"
    return texto


def _plano(texto: str | None) -> str:
    base = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return " ".join(base.lower().split())


def departamento_da_categoria(categoria: str | None) -> str | None:
    """'Celular Kit' → 'celular'; 'Acessórios Celular' → 'celular'; 'Eletro
    Usado' → 'eletro'; 'Mala Kit' → 'mala'; outra ou vazia → None. PURA."""
    n = _plano(categoria)
    if n.startswith(("celular", "acessorio")):
        return "celular"
    if n.startswith("eletro"):
        return "eletro"
    if n.startswith("mala"):
        return "mala"
    return None


def departamento_da_familia(sku: str | None) -> str | None:
    """Sem categoria nenhuma: pela família do SKU (`dg` + dígito e `a0NN` =
    celular, `u` = eletro, `b`/`bp` + dígito = mala); outra → None. PURA."""
    s = (sku or "").strip()
    if _RX_CELULAR.match(s) or _RX_ACESSORIO.match(s):
        return "celular"
    if _RX_ELETRO.match(s):
        return "eletro"
    if _RX_MALA.match(s):
        return "mala"
    return None


def departamento_da_venda(
    categoria_do_pedido: str | None, categoria_do_produto: str | None, sku: str | None
) -> str:
    """O departamento de UMA venda: a categoria do item no pedido do Bling,
    senão a do cadastro do produto, senão a família do SKU; sem nada,
    DEPARTAMENTO_PADRAO. PURA."""
    return (
        departamento_da_categoria(categoria_do_pedido)
        or departamento_da_categoria(categoria_do_produto)
        or departamento_da_familia(sku)
        or DEPARTAMENTO_PADRAO
    )


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


def _e_aparelho(sku: str | None) -> bool:
    s = (sku or "").strip()
    return bool(_RX_CELULAR.match(s) or _RX_ELETRO.match(s))


def item_principal(itens: list[Any] | None) -> dict | None:
    """O item que decide a linha: o APARELHO Uranyx (celular `dg`+dígito ou
    eletro `u`) antes do acessório Uranyx — o fone listado antes do celular no
    pedido não leva a compra para a linha do fone (revisão de 28/10/2026) —;
    senão o 1º item Uranyx; senão o 1º item. PURA."""
    lista = [
        i for i in (itens or []) if isinstance(i, dict) and (i.get("sku") or i.get("descricao"))
    ]
    uranyx = [i for i in lista if i.get("uranyx")]
    aparelho = next((i for i in uranyx if _e_aparelho(i.get("sku"))), None)
    return aparelho or (uranyx[0] if uranyx else (lista[0] if lista else None))


def saldo_da_linha(
    itens: list[Any],
    chave: str,
    tabela: dict[str, tuple[str, str, list[str]]],
    *,
    menos_custo: bool | None = None,
) -> float:
    """O saldo de UM pedido na parte da linha `chave` (a planilha de
    rentabilidade: Base − Comissão − Frete − Custo dos produtos).
    `itens` = as linhas do pedido em `bling_orders` (uma por item; a base, a
    comissão e o frete do pedido se repetem em todas). A parte da linha é o
    valor dos itens dela (preço × quantidade) sobre o valor de todos; sem
    valor nos itens, pelo número de itens. O pedido cancelado (12) ou excluído
    do Bling não tem saldo. `menos_custo` (padrão SALDO_MENOS_CUSTO) tira
    também o custo dos produtos do pedido antes de dividir. PURA."""
    if not itens or any(str(i.situacao or "") in _SEM_SALDO for i in itens):
        return 0.0

    def maior(campo: str) -> float:
        valores = [float(v) for i in itens if (v := getattr(i, campo)) is not None]
        return max(valores) if valores else 0.0

    def quantidade(i: Any) -> float:
        return float(i.item_quantidade if i.item_quantidade is not None else 1)

    saldo = maior("valorbase") - maior("taxacomissao") - maior("custofrete")
    if SALDO_MENOS_CUSTO if menos_custo is None else menos_custo:
        saldo -= sum(float(i.preco_custo or 0) * quantidade(i) for i in itens)
    da_linha = [i for i in itens if _linha_do_sku(i.item_codigo, tabela) == chave]
    total = sum(float(i.itemvalor or 0) * quantidade(i) for i in itens)
    if total:
        parte = sum(float(i.itemvalor or 0) * quantidade(i) for i in da_linha) / total
    else:
        parte = len(da_linha) / len(itens)
    return round(saldo * parte, 2)


async def _itens_dos_pedidos(session: AsyncSession, numeros: set[str]) -> dict[str, list[Any]]:
    """pedido → as linhas dele no Bling (o que o saldo precisa)."""
    lista = sorted(n for n in numeros if n)
    saida: dict[str, list[Any]] = defaultdict(list)
    bo = BlingOrder
    for i in range(0, len(lista), _LOTE):
        for r in await session.execute(
            select(
                bo.numero,
                bo.item_codigo,
                bo.itemvalor,
                bo.item_quantidade,
                bo.valorbase,
                bo.taxacomissao,
                bo.custofrete,
                bo.preco_custo,
                bo.situacao,
            ).where(bo.numero.in_(lista[i : i + _LOTE]))
        ):
            saida[str(r.numero)].append(r)
    return saida


@dataclass
class _Linha:
    """Uma linha da tabela: um produto da Tabela de Preços (ou um SKU fora dela)."""

    chave: str
    skus: list[str]
    nome: str | None
    na_tabela: bool
    departamento: str
    vendidas: int = 0
    pediram: int = 0
    nomes: Counter = field(default_factory=Counter)
    origens: Counter = field(default_factory=Counter)
    pedidos: list[int] = field(default_factory=list)
    # Os pedidos de cada coluna da tela (a célula abre a lista deles).
    por_coluna: defaultdict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    seguiram: int = 0
    consertos: int = 0
    perdidos: int = 0
    conserto: float = 0.0
    perdido: float = 0.0
    cliente: float = 0.0
    saldo: float = 0.0

    @property
    def nome_na_tela(self) -> str:
        return self.nome or (self.nomes.most_common(1)[0][0] if self.nomes else self.skus[0])


async def _tabela_de_precos(session: AsyncSession) -> dict[str, tuple[str, str, list[str]]]:
    """SKU sem lote → (id da linha da Tabela de Preços, nome, os SKUs da linha).
    O SKU "puro" da linha vale antes do SKU de kit (`dg053+a003` também daria
    `dg053`)."""
    # Ordem fixa: o mesmo SKU em duas linhas fica sempre na mesma (a ativa e a
    # mais antiga primeiro). Desde 06/11/2026 a linha de Celular, Mala ou
    # Eletro vem antes da do Catálogo/Shein com o mesmo SKU: é nela que se
    # digita o prazo da garantia (services/garantia_prazo.py), e o nome e o
    # departamento do produto saem da mesma linha.
    linhas = (
        await session.execute(
            select(PricingProduct.id, PricingProduct.sku, PricingProduct.name).order_by(
                PricingProduct.is_active.desc(), PricingProduct.created_at, PricingProduct.id
            )
        )
    ).all()
    do_departamento = await _departamentos_da_tabela(session)
    linhas = sorted(linhas, key=lambda r: str(r[0]) not in do_departamento)
    puros: dict[str, tuple[str, str, list[str]]] = {}
    de_kit: dict[str, tuple[str, str, list[str]]] = {}
    for pid, skus, nome in linhas:
        lista = [t.strip().lower() for t in (skus or "").split(",") if t.strip()]
        bases = list(dict.fromkeys(produto_base(t) for t in lista if produto_base(t)))
        for t in lista:
            alvo = de_kit if "+" in t else puros
            alvo.setdefault(produto_base(t), (str(pid), nome, bases))
    return {**de_kit, **puros}


async def _departamentos_da_tabela(session: AsyncSession) -> dict[str, str]:
    """id da linha da Tabela de Preços → o departamento: a RAIZ do segmento
    dela (a aba da Tabela). A raiz fora das pílulas não decide (a linha vai
    pela maioria das vendas, como a de fora da Tabela)."""
    segmentos = {
        sid: (pai, (slug or "").strip().lower())
        for sid, pai, slug in (
            await session.execute(select(Segment.id, Segment.parent_id, Segment.slug))
        ).all()
    }

    def raiz(sid: Any) -> str | None:
        vistos = set()
        while sid in segmentos and segmentos[sid][0] is not None and sid not in vistos:
            vistos.add(sid)
            sid = segmentos[sid][0]
        return segmentos[sid][1] if sid in segmentos else None

    saida: dict[str, str] = {}
    for pid, segmento in (
        await session.execute(select(PricingProduct.id, PricingProduct.segment_id))
    ).all():
        if (dep := raiz(segmento)) in DEPARTAMENTOS:
            saida[str(pid)] = dep
    return saida


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


# ── Garantia → linha → departamento (o Resumo e a lista) ─────────────────────


@dataclass
class MapaDasLinhas:
    """A linha (o produto) de cada garantia e o departamento de cada linha —
    o mesmo para o Resumo, as pílulas e os filtros da lista."""

    tabela: dict[str, tuple[str, str, list[str]]]
    linha_da_garantia: dict[int, str]
    departamento_da_linha: dict[str, str]

    def linha(self, g: Any) -> str:
        """A linha da garantia (`g` com id e itens); a que entrou depois do
        mapa, pela Tabela de Preços dele."""
        if (chave := self.linha_da_garantia.get(g.id)) is not None:
            return chave
        return _linha_do_sku((item_principal(g.itens) or {}).get("sku"), self.tabela)

    def departamento(self, g: Any) -> str:
        chave = self.linha(g)
        if (dep := self.departamento_da_linha.get(chave)) is not None:
            return dep
        sku = (item_principal(getattr(g, "itens", None)) or {}).get("sku")
        return departamento_da_familia(sku) or DEPARTAMENTO_PADRAO

    def departamento_do_id(self, garantia_id: int) -> str | None:
        chave = self.linha_da_garantia.get(garantia_id)
        return self.departamento_da_linha.get(chave) if chave is not None else None

    def ids_do_departamento(self, departamento: str) -> list[int]:
        return [
            gid
            for gid, chave in self.linha_da_garantia.items()
            if self.departamento_da_linha.get(chave) == departamento
        ]

    def ids_da_linha(self, chave: str) -> list[int]:
        return [gid for gid, c in self.linha_da_garantia.items() if c == chave]


async def _montar_mapa(session: AsyncSession) -> MapaDasLinhas:
    tabela = await _tabela_de_precos(session)
    da_tabela = await _departamentos_da_tabela(session)
    registros = (
        await session.execute(
            select(Garantia.id, Garantia.pedido_bling, Garantia.itens).order_by(Garantia.id)
        )
    ).all()
    linha_da_garantia: dict[int, str] = {}
    departamento_da_linha: dict[str, str] = {}
    # (pedido, sku, linha) das vendas que votam no departamento da linha.
    votantes: list[tuple[str, str, str]] = []
    for r in registros:
        sku = ((item_principal(r.itens) or {}).get("sku") or "").strip().lower()
        chave = _linha_do_sku(sku, tabela)
        linha_da_garantia[r.id] = chave
        if chave.startswith("tabela:") and (dep := da_tabela.get(chave.split(":", 1)[1])):
            departamento_da_linha[chave] = dep
        else:
            votantes.append((str(r.pedido_bling), sku, chave))
    pares = {(p, s) for p, s, _ in votantes if s}
    do_pedido = await _categorias_dos_pedidos(session, pares)
    do_cadastro = await _categorias_dos_produtos(session, {s for _, s in pares})
    votos: dict[str, Counter] = defaultdict(Counter)
    for pedido, sku, chave in votantes:
        votos[chave][
            departamento_da_venda(do_pedido.get((pedido, sku)), do_cadastro.get(sku), sku)
        ] += 1
    # A linha vai inteira para o departamento da maioria das vendas (empate: a
    # ordem fixa das pílulas).
    ordem = list(DEPARTAMENTOS)
    for chave, contagem in votos.items():
        departamento_da_linha[chave] = max(
            contagem.items(), key=lambda kv: (kv[1], -ordem.index(kv[0]))
        )[0]
    return MapaDasLinhas(tabela, linha_da_garantia, departamento_da_linha)


_cache: dict[str, Any] = {}


def limpar_cache() -> None:
    """Esquece o mapa guardado (os testes)."""
    _cache.clear()


async def _assinatura(session: AsyncSession) -> tuple:
    """O que, mudando, refaz o mapa na hora: as garantias (nova, corrigida) e
    a Tabela de Preços (linha nova, SKU, segmento)."""
    sub = [
        select(func.count()).select_from(Garantia),
        select(func.max(Garantia.id)),
        select(func.max(Garantia.atualizado_em)),
        select(func.count()).select_from(PricingProduct),
        select(func.max(PricingProduct.updated_at)),
        select(func.count()).select_from(Segment),
        select(func.max(Segment.updated_at)),
    ]
    return tuple((await session.execute(select(*[s.scalar_subquery() for s in sub]))).one())


async def mapa_das_linhas(session: AsyncSession) -> MapaDasLinhas:
    """O mapa garantia → linha → departamento, guardado por até
    CACHE_SEGUNDOS (refeito na hora quando as garantias ou a Tabela mudam)."""
    assinatura = await _assinatura(session)
    agora = time.monotonic()
    if (
        (mapa := _cache.get("mapa")) is not None
        and _cache.get("assinatura") == assinatura
        and agora - _cache.get("em", 0.0) < CACHE_SEGUNDOS
    ):
        return mapa
    mapa = await _montar_mapa(session)
    _cache.update(mapa=mapa, assinatura=assinatura, em=agora)
    return mapa


async def resumo(
    session: AsyncSession,
    filtros: list,
    dia: date | None = None,
    *,
    pedido: list | None = None,
    com_valores: bool = False,
) -> dict:
    """As vendas com garantia (os `filtros` da barra, já com escopo e sem as
    descartadas) e os PEDIDOS de garantia por produto da Tabela de Preços e por
    departamento. `pedido` = as condições a mais do pedido (o período pela data
    do pedido). O saldo é de TODAS as vendas; da origem e da manutenção, só os
    pedidos. `com_valores` = os reais (só admin)."""
    from app.services import garantia_pedido

    dia = dia or svc.hoje()
    pediu = and_(garantia_pedido.filtro_pedido(), *(pedido or [])).label("pediu")
    registros = (
        await session.execute(
            select(
                Garantia.id,
                Garantia.pedido_bling,
                Garantia.pedido_marketplace,
                Garantia.itens,
                Garantia.solicitacao_origem,
                pediu,
            )
            .where(*filtros)
            .order_by(Garantia.id)
        )
    ).all()
    mapa = await mapa_das_linhas(session)
    tabela = mapa.tabela
    pediram = [r for r in registros if r.pediu]
    pares = [(str(r.pedido_bling), r.pedido_marketplace) for r in pediram]
    devolucoes = await _devolucoes(session, {b for b, _ in pares})
    reembolsos = await _reembolsos(session, pares)
    fora = await _seguiram_fora_da_devolucao(session, pares, reembolsos)
    do_bling = (
        await _itens_dos_pedidos(session, {str(r.pedido_bling) for r in registros})
        if com_valores
        else {}
    )
    # Duas garantias do mesmo pedido e produto (uma por nota) olham o mesmo
    # aparelho e o mesmo pedido: contam uma vez só (na primeira). Cada linha
    # da tela Reembolso também entra uma vez só.
    com_saldo: set[tuple[str, str]] = set()
    contados: set[tuple[str, str]] = set()
    reembolsos_contados: set[Any] = set()

    linhas: dict[str, _Linha] = {}
    for r in registros:
        item = item_principal(r.itens) or {}
        chave = mapa.linha(r)
        if (linha := linhas.get(chave)) is None:
            if chave.startswith("tabela:"):
                base = produto_base(item.get("sku"))
                _pid, nome, skus = tabela.get(base) or (None, None, [base])
                linha = _Linha(chave, skus, nome, True, mapa.departamento(r))
            else:
                linha = _Linha(chave, [chave.split(":", 1)[1]], None, False, mapa.departamento(r))
            linhas[chave] = linha
        linha.vendidas += 1
        if nome := nome_do_produto(item.get("descricao")):
            linha.nomes[nome] += 1
        numero = str(r.pedido_bling)
        if com_valores and (numero, chave) not in com_saldo:
            com_saldo.add((numero, chave))
            linha.saldo += saldo_da_linha(do_bling.get(numero, []), chave, tabela)
        if not r.pediu:
            continue
        linha.pediram += 1
        linha.pedidos.append(r.id)
        if (origem := r.solicitacao_origem) in _COLUNAS_DA_ORIGEM:
            linha.origens[origem] += 1
            linha.por_coluna[origem].append(r.id)
        if (numero, chave) in contados:
            continue
        contados.add((numero, chave))
        outro = fora.get(numero)
        f = desfecho(
            devolucoes.get(numero, []),
            chave,
            tabela,
            dia,
            outro.o_que if outro else None,
            [x for x in reembolsos.get(numero, []) if x.id not in reembolsos_contados],
        )
        reembolsos_contados.update(f.reembolsos)
        linha.seguiram += f.seguiu
        if f.situacao == "consertado":
            linha.consertos += 1
            linha.por_coluna["consertos"].append(r.id)
        elif f.situacao == "perdido":
            linha.perdidos += 1
            linha.por_coluna["perdidos"].append(r.id)
        linha.conserto += f.conserto
        linha.perdido += f.perdido
        linha.cliente += f.cliente
        if com_valores and f.gasto > 0:
            linha.por_coluna["gasto"].append(r.id)

    def valores(li: _Linha) -> dict:
        if not com_valores:
            return {}
        return {
            "gasto": round(li.conserto + li.perdido + li.cliente, 2),
            "gasto_conserto": round(li.conserto, 2),
            "gasto_perdidos": round(li.perdido, 2),
            "gasto_cliente": round(li.cliente, 2),
            "saldo": round(li.saldo, 2),
        }

    produtos = sorted(
        linhas.values(), key=lambda li: (-li.vendidas, _plano(li.nome_na_tela), li.chave)
    )
    departamentos = []
    for chave, nome in DEPARTAMENTOS.items():
        do_dep = [li for li in produtos if li.departamento == chave]
        dep = {
            "chave": chave,
            "nome": nome,
            "vendidas": sum(li.vendidas for li in do_dep),
            "pediram": sum(li.pediram for li in do_dep),
        }
        if com_valores:
            dep["saldo"] = round(sum(valores(li)["saldo"] for li in do_dep), 2)
            dep["gasto"] = round(sum(valores(li)["gasto"] for li in do_dep), 2)
        departamentos.append(dep)
    return {
        "vendidas": len(registros),
        "pediram": len(pediram),
        "seguiram": sum(li.seguiram for li in produtos),
        "hoje": dia,
        "ve_valores": com_valores,
        "departamentos": departamentos,
        "produtos": [
            {
                "chave": li.chave,
                "departamento": li.departamento,
                "nome": li.nome_na_tela,
                "skus": li.skus,
                "na_tabela": li.na_tabela,
                "vendidas": li.vendidas,
                "pediram": li.pediram,
                "seguiram": li.seguiram,
                "origens": {o: li.origens.get(o, 0) for o in _COLUNAS_DA_ORIGEM},
                "consertos": li.consertos,
                "perdidos": li.perdidos,
                "pedidos": li.pedidos,
                "pedidos_por_coluna": dict(li.por_coluna),
                **valores(li),
            }
            for li in produtos
        ],
    }
