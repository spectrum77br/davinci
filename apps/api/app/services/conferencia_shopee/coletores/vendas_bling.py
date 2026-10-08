"""Vendas da Conferência no Mercado Livre e na Amazon: os itens dos pedidos do Bling.

Decisão do dono (07/10/2026): "Vendas" do ML e da Amazon = as linhas de item
dos pedidos do Bling da loja na semana, que o DaVinci já espelha em
`davinci.bling_orders` (uma linha por item; a mesma tabela da aba
Faturamento). Nenhuma chamada a API: só leitura do banco.

  • Loja: `bling_orders.loja` = o `bling_loja_id` da conta (lista de contas da
    Conferência).
  • Semana: o dia do pedido no fuso de Brasília (`bling_orders.data`), a
    mesma data que a aba Faturamento usa.
  • Valor: `itemvalor × item_quantidade` de cada linha — o valor de TABELA dos
    produtos: SEM o frete e SEM tirar os descontos/promoções do pedido (o
    `total` do pedido tem frete e desconto; a soma das linhas é o
    `totalprodutos`, conferido em 295 de 295 pedidos na semana 28/09–04/10).
    O `bling_orders` não guarda o desconto do pedido; a nota do relatório diz
    que o número pode ficar acima do que o cliente pagou (Amazon KFA,
    28/09–04/10: R$ 9.156 nos itens × R$ 8.566,30 nos totais).
    Linha sem quantidade conta 1; linha sem valor conta R$ 0,00 e a semana
    ganha um aviso.
  • Fora: pedido CANCELADO na hora da coleta — situação 12 (Cancelado) e
    `excluido` (o pedido sumiu do Bling). Todo o resto entra, inclusive "Em
    digitação" (na Amazon é pedido normal com etiqueta emitida) e devolução
    em andamento. Por isso refazer uma semana antiga pode dar outro número
    (o pedido cancelado depois sai).
  • Itens: agrupados pelo SKU da linha (`item_codigo`), que é o SKU do
    DaVinci; `item_id` = o SKU. Linha sem SKU vira "(sem SKU) <descrição>" —
    o eletro dela sai pelo nome (classificacao.TITULO_ELETRO).
  • Eletro: a marca `eletro` de cada item é a lista do DaVinci pelo SKU
    (classificacao.eletro_por_sku: SKU `u…`, categoria do Bling "Eletro…" ou
    segmento Eletro). O cálculo refaz pelo SKU a cada "Recalcular"; a marca
    fica nos dados para conferência.

Zero só quando dá para confiar nele (o "—" quer dizer "não veio"; falha do
Bling nunca vira R$ 0,00):

  • Espelho parado: semana que termina DEPOIS do último pedido do espelho
    inteiro (de qualquer loja — o DaVinci recebe centenas por dia) fica SEM
    Vendas ("—", a coleta vira `parcial`) com aviso: o webhook/token do Bling
    parou e somar daria menos que o real.
  • Loja que o espelho não conhece (nenhum pedido dela, nunca — loja errada
    ou digitada errado em Contas): nenhuma semana tem Vendas ("—") e a loja
    ganha o aviso para conferir a loja ligada.
  • Loja conhecida sem pedido nas semanas da rodada: R$ 0,00 (pode ser loja
    parada), mas com aviso dizendo a data do último pedido dela.
  • Semana do relatório (S1) zerada com pedido nas anteriores: R$ 0,00 com
    aviso para conferir o espelho.
Fora isso, semana sem pedido = R$ 0,00 de verdade. Erro de banco sobe (a
coleta da loja vira `erro`).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder
from app.services.conferencia_shopee import classificacao
from app.services.conferencia_shopee.coletores import Semana
from app.services.conferencia_shopee.periodos import FUSO

# Situações do Bling que tiram o pedido das Vendas (cancelado na hora da
# coleta). "excluido" é a marca do DaVinci para o pedido apagado no Bling.
SITUACOES_FORA = ("12", "excluido")
_FUSO_SQL = "America/Sao_Paulo"
SEM_SKU = "(sem SKU)"
_CENTAVO = Decimal("0.01")


def _dinheiro(v: Decimal) -> float:
    return float(v.quantize(_CENTAVO))


def _semana_do_dia(dia: date, semanas: Sequence[Semana]) -> Semana | None:
    for s in semanas:
        if s.inicio <= dia <= s.fim:
            return s
    return None


def _plural(n: int, um: str, varios: str) -> str:
    return f"{n} {um if n == 1 else varios}"


def _dia_brt(quando: datetime | None) -> date | None:
    return None if quando is None else quando.astimezone(FUSO).date()


def _ddmmaaaa(dia: date) -> str:
    return dia.strftime("%d/%m/%Y")


def _nao_cancelado() -> Any:
    return func.coalesce(BlingOrder.situacao, "").not_in(SITUACOES_FORA)


async def ultimo_dia_do_espelho(session: AsyncSession) -> date | None:
    """O dia (Brasília) do pedido mais novo do espelho do Bling, de QUALQUER
    loja (índice em `data`). None = espelho vazio."""
    return _dia_brt((await session.execute(select(func.max(BlingOrder.data)))).scalar())


async def _ultimo_da_loja(session: AsyncSession, loja: str) -> tuple[bool, date | None]:
    """(a loja tem alguma linha no espelho?, dia do último pedido NÃO cancelado)."""
    linhas, ultimo = (
        await session.execute(
            select(
                func.count(),
                func.max(case((_nao_cancelado(), BlingOrder.data))),
            ).where(BlingOrder.loja == loja)
        )
    ).one()
    return bool(linhas), _dia_brt(ultimo)


async def vendas_por_semana(
    session: AsyncSession,
    loja: str,
    semanas: Sequence[Semana],
    *,
    marcar_eletro: bool = True,
    avisos_loja: list[str] | None = None,
) -> dict[Semana, dict[str, Any]]:
    """{semana: {"vendas": {"valor", "pedidos"}, "vendas_itens": [...],
    "avisos": [...]}} de cada semana pedida. Semana em que não dá para confiar
    no zero (espelho parado, loja que o espelho não conhece) vem SEM "vendas" e
    "vendas_itens" — só os avisos (o relatório mostra "—").
    `marcar_eletro=False` (conta de Mala) não consulta a lista de eletro.
    Os avisos da LOJA (não de uma semana) vão para `avisos_loja`; sem a lista,
    entram nos da primeira semana."""
    if not semanas:
        return {}
    loja = str(loja).strip()
    da_loja: list[str] = [] if avisos_loja is None else avisos_loja
    de = datetime.combine(min(s.inicio for s in semanas), time(0), tzinfo=FUSO)
    ate = datetime.combine(max(s.fim for s in semanas) + timedelta(days=1), time(0), tzinfo=FUSO)
    dia = cast(func.timezone(_FUSO_SQL, BlingOrder.data), Date)
    linhas = (
        await session.execute(
            select(
                dia.label("dia"),
                BlingOrder.id,
                BlingOrder.bling_id,
                BlingOrder.item_codigo,
                BlingOrder.item_descricao,
                BlingOrder.itemvalor,
                BlingOrder.item_quantidade,
            ).where(
                BlingOrder.loja == loja,
                BlingOrder.data >= de,
                BlingOrder.data < ate,
                _nao_cancelado(),
            )
        )
    ).all()

    valor: dict[Semana, Decimal] = defaultdict(Decimal)
    pedidos: dict[Semana, set[str]] = defaultdict(set)
    sem_valor: dict[Semana, int] = defaultdict(int)
    sem_sku: dict[Semana, int] = defaultdict(int)
    # (semana, item_id) → agregados do item.
    itens: dict[Semana, dict[str, dict[str, Any]]] = defaultdict(dict)
    for dia_pedido, linha_id, bling_id, codigo, descricao, item_valor, quantidade in linhas:
        semana = _semana_do_dia(dia_pedido, semanas) if dia_pedido is not None else None
        if semana is None:
            continue  # dia fora das semanas (parcial: qui–dom de cada semana)
        pedido = str(bling_id) if bling_id is not None else f"linha:{linha_id}"
        if item_valor is None:
            sem_valor[semana] += 1
        v = Decimal(str(item_valor or 0)) * Decimal(quantidade if quantidade is not None else 1)
        sku = (codigo or "").strip()
        nome = (descricao or "").strip()
        if sku:
            item_id = sku
        else:
            sem_sku[semana] += 1
            item_id = f"{SEM_SKU} {nome}".strip()[:120]
        item = itens[semana].get(item_id)
        if item is None:
            item = {"item_id": item_id, "sku": sku or None, "nome": nome or None,
                    "valor": Decimal(0), "pedidos": set(), "unidades": 0}
            itens[semana][item_id] = item
        elif not item["nome"] and nome:
            item["nome"] = nome
        item["valor"] += v
        item["pedidos"].add(pedido)
        item["unidades"] += quantidade if quantidade is not None else 1
        valor[semana] += v
        pedidos[semana].add(pedido)

    eletro: dict[str, bool] = {}
    if marcar_eletro:
        eletro = await classificacao.eletro_por_sku(
            session, (it["sku"] for por_item in itens.values() for it in por_item.values())
        )

    # Semana em que o zero não é confiável → sem Vendas, com o porquê ("" = o
    # porquê vai nos avisos da loja).
    sem_numero: dict[Semana, str] = {}
    ultimo_espelho = await ultimo_dia_do_espelho(session)
    for s in semanas:
        if ultimo_espelho is None:
            sem_numero[s] = "Vendas: o espelho do Bling no DaVinci está vazio — semana sem Vendas"
        elif s.fim > ultimo_espelho:
            sem_numero[s] = (
                "Vendas: o espelho do Bling no DaVinci não tem pedido nenhum (de loja nenhuma) "
                f"depois de {_ddmmaaaa(ultimo_espelho)} — semana sem Vendas (conferir a "
                "sincronização do Bling)"
            )
    confiaveis = [s for s in semanas if s not in sem_numero]
    s1_zerada: str | None = None
    if confiaveis and not any(pedidos[s] for s in confiaveis):
        conhecida, ultimo_loja = await _ultimo_da_loja(session, loja)
        if not conhecida:
            sem_numero.update(dict.fromkeys(confiaveis, ""))
            da_loja.append(
                f"Vendas: a loja do Bling {loja} não tem nenhum pedido no DaVinci — confira a "
                "loja ligada em Contas (Vendas não coletadas)"
            )
        else:
            ultimo = (
                f"o último é de {_ddmmaaaa(ultimo_loja)}"
                if ultimo_loja
                else "a loja só tem pedidos cancelados"
            )
            da_loja.append(
                f"Vendas: nenhum pedido da loja do Bling {loja} nas semanas da conferência "
                f"({ultimo}) — confira a loja ligada em Contas e o espelho do Bling"
            )
    elif semanas[0] in confiaveis and not pedidos[semanas[0]]:
        s1_zerada = (
            "Vendas: nenhum pedido desta loja do Bling na semana, mas houve nas anteriores — "
            "confira o espelho do Bling"
        )

    saida: dict[Semana, dict[str, Any]] = {}
    for s in semanas:
        if s in sem_numero:
            saida[s] = {"avisos": [sem_numero[s]] if sem_numero[s] else []}
            continue
        lista = []
        for it in sorted(itens[s].values(), key=lambda x: (-x["valor"], x["item_id"])):
            item = {
                "item_id": it["item_id"],
                "nome": it["nome"],
                "valor": _dinheiro(it["valor"]),
                "pedidos": len(it["pedidos"]),
                "unidades": it["unidades"],
            }
            if it["sku"]:
                item["sku"] = it["sku"]
                chave = it["sku"].strip().lower()
                if chave in eletro:
                    item["eletro"] = eletro[chave]
            lista.append(item)
        avisos = []
        if sem_valor[s]:
            avisos.append(
                f"Vendas: {_plural(sem_valor[s], 'linha', 'linhas')} de item sem valor no Bling "
                "(contadas como R$ 0,00)"
            )
        if sem_sku[s]:
            avisos.append(
                f"Vendas: {_plural(sem_sku[s], 'linha', 'linhas')} sem SKU no Bling — o eletro "
                "dessas sai pelo nome do produto"
            )
        if s1_zerada and s == semanas[0]:
            avisos.append(s1_zerada)
        saida[s] = {
            "vendas": {"valor": _dinheiro(valor[s]), "pedidos": len(pedidos[s])},
            "vendas_itens": lista,
            "avisos": avisos,
        }
    if avisos_loja is None and da_loja:
        saida[semanas[0]]["avisos"][:0] = da_loja
    return saida
