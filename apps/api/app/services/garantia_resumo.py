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

Só leitura. As porcentagens a tela calcula (uma conta só).

Manutenção e perdas (30/10/2026, "em garantias precisamos do que realmente
foi perdido e o que está na manutenção — no caso, o que foi gasto de
dinheiro"): dos que PEDIRAM, o que aconteceu com o aparelho da garantia na
tela Devoluções (só a devolução do mesmo produto — o fone do kit no mesmo
pedido fica de fora). Os valores em reais só para admin (o custo do produto
nas Devoluções é só do admin).
"""

from __future__ import annotations

import re
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
from app.models.garantia import Garantia, GarantiaAtendimento
from app.models.pricing import PricingProduct
from app.models.product import Product, ProductCategory
from app.models.refund import Refund
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
# A coluna da tela de cada situação e de cada origem do pedido (30/10/2026,
# "se eu clicar em devolução aparecem as 4 da devolução, já vem filtrado"):
# o número da célula abre só aqueles pedidos. O "Aguardando" da tela soma o
# entregue sem data; atendimento, cadastro à mão e a equipe são o "Outros".
_COLUNA_DO_STATUS = {
    svc.STATUS_ATIVA: "ativa",
    svc.STATUS_SOMENTE_SOFTWARE: "somente_software",
    svc.STATUS_AGUARDANDO: "aguardando",
    "entregue_sem_data": "aguardando",
    svc.STATUS_EXPIRADA: "expirada",
}
_COLUNA_DA_ORIGEM = {"mensagem": "mensagem", "sac": "sac", "devolucao": "devolucao"}

# ── Manutenção e perdas (30/10/2026) ─────────────────────────────────────────
# O aparelho da garantia na tela Devoluções (uma linha por unidade):
#   - no técnico: condição Manutenção, ainda sem o destino da volta (a regra
#     de "pendente" do próprio Devoluções — devolution_stock_return);
#   - consertado: passou pela manutenção e voltou ao estoque (Novo, Usado ou
#     Trocado; ou o destino Novo/Usado do modal da Manutenção);
#   - perdido: Extraviado ou Sucata (ou o destino Sucata do modal).
# Uma garantia fica numa só: perdido > no técnico > consertado.
# Gasto = o conserto (Custo manutenção) + o custo dos aparelhos perdidos. O
# conserto de até R$ 1 não é gasto: é o valor que se digita para conseguir
# tirar o aparelho da Manutenção (a tela não aceita vazio nem zero).
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
# (`_casos_do_pedido`). A % da tela é Seguiram ÷ Vendidas.
_PERDIDO = ("Extraviado", "Sucata")
_DE_VOLTA = ("Novo", "Usado", "Trocado")
_NAO_VOLTOU = ("Não devolvido", "Entregue")
_CUSTO_DE_MENTIRA = 1.0
_COLUNA_DO_DESFECHO = {
    "no_tecnico": "no_tecnico",
    "consertado": "consertados",
    "perdido": "perdidos",
}
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


@dataclass
class Desfecho:
    """O que aconteceu com o aparelho da garantia (a devolução dele)."""

    # no_tecnico | consertado | perdido | devolvido — o aparelho na tela
    # Devoluções; reembolso | a_caminho — foi adiante só na plataforma ou no
    # Reembolso (sem a devolução lançada).
    situacao: str | None = None
    condicao: str | None = None  # a condição que decidiu (Extraviado, Novo…)
    conserto: float = 0.0  # o Custo manutenção de verdade (> R$ 1)
    perdido: float = 0.0  # o custo dos aparelhos perdidos
    no_tecnico: float = 0.0  # o custo dos aparelhos que estão no técnico
    prazo: datetime | None = None  # o prazo (30 dias) de quem está no técnico
    vencido: bool = False
    sem_custo: bool = False  # consertado sem o custo do conserto lançado
    devolveu: bool = False  # o aparelho voltou
    seguiu: bool = False  # foi adiante (a base da %): voltou, voltando ou reembolso

    @property
    def gasto(self) -> float:
        return round(self.conserto + self.perdido, 2)


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
            )
            .where(Devolution.pedido_bling.in_(numeros[i : i + _LOTE]))
            .order_by(Devolution.created_at, Devolution.id)
        )
        for d in linhas.all():
            saida[str(d.pedido_bling)].append(d)
    return saida


def desfecho(
    devolucoes: list[Any],
    chave: str,
    tabela: dict[str, tuple[str, str, list[str]]],
    dia: date,
    fora: str | None = None,
) -> Desfecho:
    """O desfecho do aparelho da garantia: só as devoluções do mesmo produto
    (`chave` = a linha da tabela) — o fone do kit no mesmo pedido fica de
    fora; a devolução sem SKU conta (é do pedido). O prazo vence pelo dia em
    São Paulo (`dia`), como na tela Devoluções: no dia do prazo é "vence hoje".
    `fora` = o pedido foi adiante fora da tela Devoluções (`reembolso` ou
    `a_caminho` — `_seguiram_fora_da_devolucao`)."""
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

    # O custo de conserto sem a marca "passou em manutenção" (linha antiga)
    # também é conserto.
    consertados = [
        d
        for d in meus
        if (d.condicao_produto in _DE_VOLTA and (d.manutencao or de_verdade(d)))
        or (d.condicao_produto == "Manutenção" and d.manutencao_destino in ("Novo", "Usado"))
    ]

    f = Desfecho(
        conserto=round(sum(d.custo_manutencao for d in meus if de_verdade(d)), 2),
        perdido=round(sum(d.custo_produto or 0 for d in perdidos), 2),
    )
    if perdidos:
        d = perdidos[0]
        f.situacao = "perdido"
        f.condicao = (
            d.manutencao_destino if d.condicao_produto == "Manutenção" else d.condicao_produto
        )
    elif no_tecnico:
        # O prazo e o valor parado só de quem está no técnico (a garantia em
        # Perdidos não entra no "vencido" nem nos reais do No técnico).
        f.situacao, f.condicao = "no_tecnico", "Manutenção"
        f.no_tecnico = round(sum(d.custo_produto or 0 for d in no_tecnico), 2)
        prazos = [d.prazo for d in no_tecnico if d.prazo]
        f.prazo = min(prazos) if prazos else None
        f.vencido = f.prazo is not None and f.prazo.astimezone(svc.SAO_PAULO).date() < dia
    elif consertados:
        d = consertados[0]
        f.situacao = "consertado"
        f.condicao = (
            d.manutencao_destino if d.condicao_produto == "Manutenção" else d.condicao_produto
        )
        f.sem_custo = not any(de_verdade(x) for x in consertados)
    voltaram = [d for d in meus if d.condicao_produto not in _NAO_VOLTOU]
    f.devolveu = bool(voltaram)
    if f.devolveu and not f.situacao:
        # Voltou sem passar pela manutenção (ou ainda sem a condição lançada).
        f.situacao, f.condicao = "devolvido", voltaram[0].condicao_produto
    f.seguiu = f.devolveu or fora is not None
    if fora and not f.situacao:
        f.situacao = fora
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


def _casos_do_pedido(casos: list[Any]) -> str | None:
    """Os casos da plataforma de UM pedido: o reembolso pago conta sempre (é
    fato, não volta atrás); o "a caminho" só pelo caso mais novo da Shopee e
    da TikTok — a Logística guarda um caso por pedido e o novo encerra o
    anterior, que fica parado. A marca `substituida_por` não serve para isso:
    dois casos podem ficar marcados um pelo outro (revisão 30/10). O ML não
    troca caso: vale cada um."""
    o_que = {c.id: _caso_seguiu(c.plataforma, c.tipo, c.status, c.dados) for c in casos}
    if "reembolso" in o_que.values():
        return "reembolso"
    for plataforma in {c.plataforma for c in casos}:
        dela = [c for c in casos if c.plataforma == plataforma]
        if plataforma == "ml":
            if any(o_que[c.id] == "a_caminho" for c in dela):
                return "a_caminho"
            continue
        # O mais novo é o que entrou por último (a Logística grava o caso novo
        # quando ele aparece); empate, o aberto por último na plataforma.
        mais_novo = max(dela, key=lambda c: (c.created_at, c.aberta_em or c.created_at))
        if o_que[mais_novo.id] == "a_caminho":
            return "a_caminho"
    return None


async def _seguiram_fora_da_devolucao(
    session: AsyncSession, pedidos: list[tuple[str, str | None]]
) -> dict[str, str]:
    """pedido do Bling → `reembolso` | `a_caminho`: o pedido de garantia foi
    adiante fora da tela Devoluções — o Reembolso tipo "Cliente" do pedido
    (dinheiro devolvido ao cliente) ou os casos da plataforma
    (`_casos_do_pedido`). Liga pelo pedido do Bling e pelo da plataforma (o do
    ML pode ser o pack)."""
    do_bling = sorted({b for b, _ in pedidos if b})
    do_mkt: dict[str, set[str]] = defaultdict(set)
    for b, m in pedidos:
        if b and (m or "").strip():
            do_mkt[m.strip()].add(b)
    mkts = sorted(do_mkt)
    saida: dict[str, str] = {}

    pm = func.btrim(Refund.pedido_marketplace)
    for i in range(0, len(do_bling), _LOTE):
        for (b,) in await session.execute(
            select(Refund.pedido_bling).where(
                Refund.tipo == "Cliente", Refund.pedido_bling.in_(do_bling[i : i + _LOTE])
            )
        ):
            saida[str(b)] = "reembolso"
    for i in range(0, len(mkts), _LOTE):
        for (m,) in await session.execute(
            select(pm).where(Refund.tipo == "Cliente", pm.in_(mkts[i : i + _LOTE]))
        ):
            for b in do_mkt.get(m, set()):
                saida[b] = "reembolso"

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
        if saida.get(b) != "reembolso" and (o := _casos_do_pedido(list(do_pedido.values()))):
            saida[b] = o
    return saida


async def desfechos(
    session: AsyncSession, garantias: list[Garantia], dia: date
) -> dict[int, Desfecho]:
    """O desfecho do aparelho de cada garantia (a lista e o detalhe). Só as
    que têm alguma coisa (situação ou gasto)."""
    devs = await _devolucoes(session, {str(g.pedido_bling) for g in garantias})
    fora = await _seguiram_fora_da_devolucao(
        session, [(str(g.pedido_bling), g.pedido_marketplace) for g in garantias]
    )
    if not devs and not fora:
        return {}
    tabela = await _tabela_de_precos(session)
    saida: dict[int, Desfecho] = {}
    for g in garantias:
        dev, outro = devs.get(str(g.pedido_bling), []), fora.get(str(g.pedido_bling))
        if not dev and not outro:
            continue
        item = item_principal(g.itens) or {}
        f = desfecho(dev, _linha_do_sku(item.get("sku"), tabela), tabela, dia, outro)
        if f.situacao or f.gasto:
            saida[g.id] = f
    return saida


async def contar_seguiram(session: AsyncSession, filtros: list, dia: date) -> int:
    """Quantas das garantias (`filtros`) seguiram — a mesma conta da tabela
    (o aparelho do mesmo pedido e produto conta uma vez só)."""
    registros = (
        await session.execute(
            select(Garantia.id, Garantia.pedido_bling, Garantia.pedido_marketplace, Garantia.itens)
            .where(*filtros)
            .order_by(Garantia.id)
        )
    ).all()
    devs = await _devolucoes(session, {str(r.pedido_bling) for r in registros})
    fora = await _seguiram_fora_da_devolucao(
        session, [(str(r.pedido_bling), r.pedido_marketplace) for r in registros]
    )
    if not devs and not fora:
        return 0
    tabela = await _tabela_de_precos(session)
    contados: set[tuple[str, str]] = set()
    seguiram = 0
    for r in registros:
        pedido = str(r.pedido_bling)
        dev, outro = devs.get(pedido, []), fora.get(pedido)
        chave = _linha_do_sku((item_principal(r.itens) or {}).get("sku"), tabela)
        if (not dev and not outro) or (pedido, chave) in contados:
            continue
        contados.add((pedido, chave))
        seguiram += desfecho(dev, chave, tabela, dia, outro).seguiu
    return seguiram


def _reais(v: float) -> str:
    return "R$ " + f"{v:,.2f}".replace(",", "x").replace(".", ",").replace("x", ".")


def rotulo_do_desfecho(f: Desfecho, dia: date, com_valor: bool) -> str | None:
    """O selo da garantia na lista e no detalhe: "No técnico · faltam 5 dias",
    "Consertado (Novo)", "Perdido (Extraviado)"; o gasto em reais só para
    admin. Curto, para caber na lista."""
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
        texto = "Conserto"
    else:
        return None
    if com_valor and f.gasto:
        texto += f" · {_reais(f.gasto)}"
    return texto


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
    # Os pedidos de cada coluna da tela (a célula abre a lista deles).
    por_coluna: defaultdict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    # Manutenção e perdas: quantos pedidos em cada desfecho e os valores.
    seguiram: int = 0
    desfechos: Counter = field(default_factory=Counter)
    vencidos: int = 0
    sem_custo: int = 0
    conserto: float = 0.0
    perdido: float = 0.0
    no_tecnico: float = 0.0


async def _tabela_de_precos(session: AsyncSession) -> dict[str, tuple[str, str, list[str]]]:
    """SKU sem lote → (id da linha da Tabela de Preços, nome, os SKUs da linha).
    O SKU "puro" da linha vale antes do SKU de kit (`dg053+a003` também daria
    `dg053`)."""
    # Ordem fixa: o mesmo SKU em duas linhas fica sempre na mesma (a ativa e a
    # mais antiga primeiro).
    linhas = (
        await session.execute(
            select(PricingProduct.id, PricingProduct.sku, PricingProduct.name).order_by(
                PricingProduct.is_active.desc(), PricingProduct.created_at, PricingProduct.id
            )
        )
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
    do pedido). Da situação, da origem, dos atendimentos e da manutenção e
    perdas, só os pedidos. `com_valores` = os reais (só admin)."""
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
                Garantia.pedido_marketplace,
                Garantia.itens,
                Garantia.data_inicio,
                Garantia.fim_hardware,
                Garantia.fim_software,
                Garantia.solicitacao_origem,
                pediu,
                sem_data_entregue,
            )
            .where(*filtros)
            .order_by(Garantia.id)
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
    devolucoes = await _devolucoes(session, {str(r.pedido_bling) for r in registros if r.pediu})
    fora = await _seguiram_fora_da_devolucao(
        session, [(str(r.pedido_bling), r.pedido_marketplace) for r in registros if r.pediu]
    )
    # Duas garantias do mesmo pedido e produto (uma por nota) olham as mesmas
    # devoluções: o aparelho conta uma vez só (na primeira).
    contados: set[tuple[str, str]] = set()

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
        if nome := nome_do_produto(item.get("descricao")):
            linha.nomes[nome] += 1
        if not r.pediu:
            continue
        linha.pediram += 1
        linha.pedidos.append(r.id)
        st = svc.status_em(r.data_inicio, r.fim_hardware, r.fim_software, dia)
        st = "entregue_sem_data" if r.entregue_sem_data else st
        linha.status[st] += 1
        if coluna := _COLUNA_DO_STATUS.get(st):
            linha.por_coluna[coluna].append(r.id)
        origem = r.solicitacao_origem or garantia_pedido.ORIGEM_PESSOA
        linha.origens[origem] += 1
        linha.por_coluna[_COLUNA_DA_ORIGEM.get(origem, "outros")].append(r.id)
        if a := atend.get(r.id):
            linha.com_atendimento += 1
            linha.por_coluna["atendimentos"].append(r.id)
            linha.hardware += a.get("hardware", 0)
            linha.software += a.get("software", 0)
        dev, outro = devolucoes.get(str(r.pedido_bling), []), fora.get(str(r.pedido_bling))
        if (dev or outro) and (str(r.pedido_bling), linha.chave) not in contados:
            contados.add((str(r.pedido_bling), linha.chave))
            f = desfecho(dev, linha.chave, tabela, dia, outro)
            if f.seguiu:
                linha.seguiram += 1
                linha.por_coluna["seguiram"].append(r.id)
            if coluna := _COLUNA_DO_DESFECHO.get(f.situacao or ""):
                linha.desfechos[f.situacao] += 1
                linha.por_coluna[coluna].append(r.id)
                linha.vencidos += f.vencido
                linha.sem_custo += f.sem_custo
            linha.conserto += f.conserto
            linha.perdido += f.perdido
            linha.no_tecnico += f.no_tecnico
            if com_valores and f.gasto:
                linha.por_coluna["gasto"].append(r.id)

    ordem = list(DEPARTAMENTOS)

    # A linha vai inteira para o departamento da maioria das vendas (empate: a
    # ordem fixa dos departamentos); as pílulas somam as LINHAS — o total da
    # pílula bate com a soma da tabela.
    def departamento_da(li: _Linha) -> str:
        return max(li.departamentos.items(), key=lambda kv: (kv[1], -ordem.index(kv[0])))[0]

    # chave → [vendidas, pediram, seguiram]
    deps: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for li in linhas.values():
        deps[departamento_da(li)][0] += li.vendidas
        deps[departamento_da(li)][1] += li.pediram
        deps[departamento_da(li)][2] += li.seguiram
    return {
        "vendidas": len(registros),
        "pediram": len(ids),
        "seguiram": sum(li.seguiram for li in linhas.values()),
        "hoje": dia,
        "ve_valores": com_valores,
        "departamentos": [
            {
                "chave": chave,
                "nome": DEPARTAMENTOS[chave],
                "vendidas": v,
                "pediram": n,
                "seguiram": sg,
            }
            for chave, (v, n, sg) in sorted(
                deps.items(), key=lambda x: (-x[1][1], -x[1][0], ordem.index(x[0]))
            )
        ],
        "produtos": [
            {
                "chave": li.chave,
                "departamento": departamento_da(li),
                "nome": li.nome or (li.nomes.most_common(1)[0][0] if li.nomes else li.skus[0]),
                "skus": li.skus,
                "na_tabela": li.na_tabela,
                "vendidas": li.vendidas,
                "pediram": li.pediram,
                "seguiram": li.seguiram,
                "status": {
                    s: li.status.get(s, 0) for s in (*svc.STATUS_ROTULOS, "entregue_sem_data")
                },
                "origens": {o: li.origens.get(o, 0) for o in garantia_pedido.ORIGEM_ROTULOS},
                "com_atendimento": li.com_atendimento,
                "atendimentos_hardware": li.hardware,
                "atendimentos_software": li.software,
                "pedidos": li.pedidos,
                "pedidos_por_coluna": dict(li.por_coluna),
                "no_tecnico": li.desfechos["no_tecnico"],
                "no_tecnico_vencidos": li.vencidos,
                "consertados": li.desfechos["consertado"],
                "consertados_sem_custo": li.sem_custo,
                "perdidos": li.desfechos["perdido"],
                **(
                    {
                        "gasto": round(li.conserto + li.perdido, 2),
                        "gasto_conserto": round(li.conserto, 2),
                        "gasto_perdidos": round(li.perdido, 2),
                        "valor_no_tecnico": round(li.no_tecnico, 2),
                    }
                    if com_valores
                    else {}
                ),
            }
            for li in sorted(linhas.values(), key=lambda x: (-x.pediram, -x.vendidas, x.skus[0]))
        ],
    }
