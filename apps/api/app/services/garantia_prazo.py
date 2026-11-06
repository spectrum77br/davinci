"""Prazo da garantia por produto (06/11/2026).

Especificação do Eduardo ("Painel de Garantia: alterações", 05/11/2026):
"Cada produto tem um prazo único de garantia, informado na Tabela de Preços →
Produtos" — a coluna Garantia (meses), `pricing_products.garantia_meses`.

  * Cálculo: fim da garantia = data de entrega + prazo do produto (RN04: o dia
    que não existe vira o último; vale até a data final, inclusive).
  * Produto sem prazo: a garantia fica sem data final ("Prazo não cadastrado").
  * Mudança de prazo: "se o prazo aumentar, vale para todas as vendas do
    produto. Se diminuir, vale só para as vendas feitas a partir da mudança,
    para não encurtar garantias já vendidas."

A regra fica numa conta só (decidida em 06/11): o prazo de uma venda é o MAIOR
prazo que o produto teve em algum instante desde o início do dia da venda (em
São Paulo) até agora. Aumento vale para todas (o prazo novo está valendo
agora); redução só alcança as vendas a partir do dia dela (o prazo maior
valeu até a hora da mudança); prazo apagado não conta no máximo. Por isso
cada mudança fica gravada (`pricing_garantia_prazo`, a vigência e o log): o
robô da garantia cria a venda de até 45 dias atrás, e a venda feita antes de
uma redução, mesmo criada depois dela, continua com o prazo maior.

Atenção: um número digitado errado e corrigido logo depois (120 no lugar de
12) também vale para as vendas anteriores — a regra é o maior prazo que o
produto teve.

Data da venda = o dia da compra no Bling (`garantia_pedido.compra_da_garantia`);
sem o pedido no espelho, o dia do cadastro da garantia.

O produto da garantia é a linha da Tabela de Preços do item principal
(`garantia_resumo._tabela_de_precos`, que prefere a linha de Celular, Mala ou
Eletro quando o mesmo SKU está em mais de uma). Só lê a Tabela e grava nas
garantias; NÃO commita.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, time
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models.garantia import Garantia
from app.models.pricing import PricingGarantiaPrazo, PricingProduct
from app.services import garantia as svc
from app.services import garantia_pedido, garantia_resumo

# Quem "recalculou" no log da garantia quando a mudança não é de uma pessoa
# (o robô de 15 min, a carga do deploy).
ROTULO_ROBO = "Sistema (prazo da Tabela de Preços)"
# O começo de tudo: o prazo que o produto tinha antes da 1ª mudança gravada.
_DESDE_SEMPRE = datetime.min.replace(tzinfo=UTC)
_LOTE = 1000

Vigencia = tuple[datetime, int | None]


def prazo_da_venda(vigencias: Iterable[Vigencia], venda_dia: date) -> int | None:
    """O prazo (meses) da venda feita em `venda_dia`: o maior prazo que valeu em
    algum instante desde 00:00 desse dia (São Paulo) até agora. `vigencias` =
    (vale desde, meses), cada uma valendo até a seguinte; meses None = sem
    prazo (não conta). None = o produto nunca teve prazo nesse tempo. PURA."""
    inicio = datetime.combine(venda_dia, time(), svc.SAO_PAULO)
    lista = sorted(vigencias, key=lambda v: v[0])
    vale: list[int] = []
    for i, (_desde, meses) in enumerate(lista):
        ate = lista[i + 1][0] if i + 1 < len(lista) else None
        if meses and (ate is None or ate > inicio):
            vale.append(int(meses))
    return max(vale, default=None)


def vigencias_do_produto(
    atual: int | None, mudancas: list[Any], agora: datetime | None = None
) -> list[Vigencia]:
    """As vigências de um produto: o prazo de antes da 1ª mudança gravada (o
    `meses_anterior` dela), cada mudança e — se o valor da linha não é o da
    última mudança (gravado por fora da tela) — o valor de hoje, valendo desde
    agora. Sem mudança gravada, o valor de hoje vale desde sempre. PURA."""
    if not mudancas:
        return [(_DESDE_SEMPRE, atual)]
    ordem = sorted(mudancas, key=lambda m: (m.vale_desde, m.id))
    saida: list[Vigencia] = [(_DESDE_SEMPRE, ordem[0].meses_anterior)]
    saida += [(m.vale_desde, m.meses) for m in ordem]
    if ordem[-1].meses != atual:
        saida.append((agora or datetime.now(UTC), atual))
    return saida


def _sku_principal(itens: Any) -> str | None:
    return (garantia_resumo.item_principal(itens) or {}).get("sku")


def _produto_da_garantia(itens: Any, tabela: dict) -> str | None:
    """O id da linha da Tabela de Preços do item principal (None = fora)."""
    chave = garantia_resumo._linha_do_sku(_sku_principal(itens), tabela)
    return chave.split(":", 1)[1] if chave.startswith("tabela:") else None


async def _vigencias(session: AsyncSession, pids: set[str]) -> dict[str, list[Vigencia]]:
    """produto → as vigências dele (o valor de hoje e as mudanças gravadas)."""
    if not pids:
        return {}
    ids = [UUID(p) for p in pids]
    atuais = dict(
        (
            await session.execute(
                select(PricingProduct.id, PricingProduct.garantia_meses).where(
                    PricingProduct.id.in_(ids)
                )
            )
        ).all()
    )
    mudancas: dict[UUID, list[Any]] = {}
    for m in (
        await session.execute(
            select(
                PricingGarantiaPrazo.id,
                PricingGarantiaPrazo.pricing_product_id,
                PricingGarantiaPrazo.meses,
                PricingGarantiaPrazo.meses_anterior,
                PricingGarantiaPrazo.vale_desde,
            ).where(PricingGarantiaPrazo.pricing_product_id.in_(ids))
        )
    ).all():
        mudancas.setdefault(m.pricing_product_id, []).append(m)
    agora = datetime.now(UTC)
    return {
        str(pid): vigencias_do_produto(atual, mudancas.get(pid, []), agora)
        for pid, atual in atuais.items()
    }


async def prazo_dos_itens(session: AsyncSession, itens: Any, venda: datetime | None) -> int | None:
    """O prazo que uma venda (os itens do pedido, a hora da compra) teria hoje —
    a prévia do cadastro, antes de a garantia existir."""
    tabela = await garantia_resumo._tabela_de_precos(session)
    pid = _produto_da_garantia(itens, tabela)
    if pid is None:
        return None
    vigencias = (await _vigencias(session, {pid})).get(pid, [])
    dia = svc.dia_em_sp(venda) if venda else svc.hoje()
    return prazo_da_venda(vigencias, dia)


async def ids_do_produto(session: AsyncSession, pricing_product_id: UUID | str) -> list[int]:
    """As garantias cujo item principal é desta linha da Tabela de Preços (o
    gancho da coluna Garantia (meses))."""
    tabela = await garantia_resumo._tabela_de_precos(session)
    alvo = str(pricing_product_id)
    linhas = (await session.execute(select(Garantia.id, Garantia.itens))).all()
    return [gid for gid, itens in linhas if _produto_da_garantia(itens, tabela) == alvo]


def _bases(skus: Iterable[str | None]) -> set[str]:
    """Os SKUs base (sem o lote) de uma ou mais colunas `sku` da Tabela (a lista
    de cores separada por vírgula)."""
    saida: set[str] = set()
    for texto in skus:
        for t in (texto or "").split(","):
            if base := garantia_resumo.produto_base(t):
                saida.add(base)
    return saida


async def ids_dos_skus(session: AsyncSession, skus: Iterable[str | None]) -> list[int]:
    """As garantias cujo item principal tem um destes SKUs (sem o lote) — o
    gancho do SKU da linha (o de antes e o de depois): a ligação venda →
    produto dessas pode ter mudado."""
    bases = _bases(skus)
    if not bases:
        return []
    linhas = (await session.execute(select(Garantia.id, Garantia.itens))).all()
    return [
        gid for gid, itens in linhas if garantia_resumo.produto_base(_sku_principal(itens)) in bases
    ]


async def aplicar_prazos(
    session: AsyncSession,
    ids: list[int] | None = None,
    ator: User | None = None,
    *,
    registrar: bool = True,
) -> int:
    """Grava o prazo da venda (`prazo_meses`) e o fim único (`fim_garantia`) das
    garantias (todas, ou só `ids`) onde mudou, com "recalculou" no log de cada
    uma (`ator` None = o robô). `registrar` False = a garantia que acabou de
    nascer (o prazo é o de nascença, não uma mudança: sem linha no log).
    Devolve quantas mudaram. NÃO commita."""
    if ids is not None and not ids:
        return 0
    tabela = await garantia_resumo._tabela_de_precos(session)
    g = Garantia
    q = select(
        g.id,
        g.itens,
        g.prazo_meses,
        g.data_inicio,
        g.fim_garantia,
        g.criado_em,
        garantia_pedido.compra_da_garantia().label("compra"),
    ).order_by(g.id)
    linhas: list[Any] = []
    if ids is None:
        linhas = list((await session.execute(q)).all())
    else:
        lista = sorted(set(ids))
        for i in range(0, len(lista), _LOTE):
            linhas += list((await session.execute(q.where(g.id.in_(lista[i : i + _LOTE])))).all())
    produto = {r.id: _produto_da_garantia(r.itens, tabela) for r in linhas}
    vigencias = await _vigencias(session, {p for p in produto.values() if p})
    mudaram = 0
    for r in linhas:
        pid = produto[r.id]
        venda = r.compra or r.criado_em
        prazo = (
            prazo_da_venda(vigencias.get(pid, []), svc.dia_em_sp(venda))
            if pid is not None and venda is not None
            else None
        )
        fim = svc.fim_da_garantia(r.data_inicio, prazo)
        if (prazo, fim) == (r.prazo_meses, r.fim_garantia):
            continue
        # Só grava se a garantia ainda está como foi lida: o PATCH da Tabela
        # (ou a entrega) que gravou no meio da volta vale — o cálculo daqui
        # já está velho e a próxima volta relê (revisão de 06/11/2026).
        feito = await session.execute(
            update(Garantia)
            .where(
                Garantia.id == r.id,
                Garantia.prazo_meses.is_not_distinct_from(r.prazo_meses),
                Garantia.fim_garantia.is_not_distinct_from(r.fim_garantia),
                Garantia.data_inicio.is_not_distinct_from(r.data_inicio),
            )
            .values(prazo_meses=prazo, fim_garantia=fim)
            .execution_options(synchronize_session="fetch")
        )
        if not feito.rowcount:
            continue
        mudaram += 1
        if not registrar:
            continue
        if prazo != r.prazo_meses:
            svc.registrar_log(
                session,
                r.id,
                "recalculou",
                ator,
                campo="prazo_meses",
                anterior=str(r.prazo_meses) if r.prazo_meses else None,
                novo=str(prazo) if prazo else None,
                detalhe="prazo do produto na Tabela de Preços"
                + (f": {prazo} meses" if prazo else ": sem prazo cadastrado")
                + (f" · fim {svc._br(fim)}" if fim else ""),
                robo=ROTULO_ROBO,
            )
        else:
            svc.registrar_log(
                session,
                r.id,
                "recalculou",
                ator,
                campo="fim_garantia",
                anterior=svc._br(r.fim_garantia),
                novo=svc._br(fim),
                detalhe="fim da garantia = entrega + prazo do produto",
                robo=ROTULO_ROBO,
            )
    return mudaram
