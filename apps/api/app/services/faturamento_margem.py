"""A coluna Margem da aba Faturamento (M-37, 24/11/2026; prévia aprovada pelo
usuário em 24/11).

Era a faixa menor–maior das margens da aba Contas (a margem de formação de
preço). Virou o resultado das vendas:

    Margem da loja no período = (Σ Sobrou de TODOS os pedidos − Ads) ÷ Σ custo

Regras (os itens do pedido entre parênteses):

* Fonte: a Conciliação (`conciliacao_pedidos`) das contas ativas, pela DATA DO
  PEDIDO no dia de Brasília — o mesmo dia da aba Pedidos da Conciliação —, nos
  mesmos dias do filtro do Faturamento (item 1).
* Cada pedido entra assim (item 2; `TIPO_DO_PEDIDO` e `SOBROU_DO_PEDIDO`):
  a) com Sobrou (o dinheiro já mexeu; cancelado inclusive, que tem custo zero):
     o `sobrou` da Conciliação. Cancelado conta em "cancelados", o resto em
     "pagos";
  b) sem Sobrou e com valor previsto (aguardando repasse, vencido): Sobrou
     previsto = venda_liquido − custo − reembolso − venda_frete_pago − imposto.
     É conta nova: a Conciliação deixa o Sobrou vazio até o dinheiro entrar.
     Vale em qualquer situação do Bling (Aguardando Devolução, Aguardando
     Cancelamento, Resolvido, Manutenção — decisão do usuário);
  c) sem Sobrou e sem valor de venda ("Venda não lida"): fora da conta — nem
     sobrou nem custo —, só aparece na contagem do balão; entra sozinho quando
     a plataforma calcular a venda;
  d) "fora da loja" (saiu desta loja no Bling): fora de tudo.
* Custo: o `custo` da Conciliação, com as regras de lá (cancelado e Manutenção
  zeram); vazio = 0 (item 3).
* Ads: só a parte de Ads do Investimento da M-36 (`Investimento.ads`); sem
  dado = 0 (item 4). Afiliados NÃO saem de novo: na Shopee e na TikTok a
  comissão já sai do recebido e do previsto de cada pedido (item 5). O Ads é
  descontado no router, que já tem o Investimento — aqui não se relê o
  Marketing.
* A Conciliação é por conta (integração) e o Faturamento é por loja: cada
  pedido vai para a loja do Bling do próprio pedido (`bling_orders.loja` do
  mesmo `bling_id`), nunca pelo nome (item 10). Pedido sem linha no Bling fica
  de fora (a Conciliação nasce das linhas do Bling; sem elas o recálculo o põe
  em "fora da loja").
* "—" (item 6): loja sem conta na Conciliação e período que começa antes do
  início da Conciliação das contas da loja (`conciliacao_lojas.inicio`: 24/10/
  2026 em todas desde a 0437). Antes do início a Conciliação não tem os
  pedidos; mostrar só os dias depois dele como se fossem o período todo
  enganaria.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, ConciliacaoLoja, ConciliacaoPedido
from app.services.faturamento_contas import _loja_da_integracao

_BRT = ZoneInfo("America/Sao_Paulo")
ZERO = Decimal("0")

# Como cada pedido entra (regras a–c do módulo; o "fora da loja" nem é lido).
PAGO = "pago"
PREVISTO = "previsto"
CANCELADO = "cancelado"
SEM_VALOR = "sem_valor"

_PED = ConciliacaoPedido
TIPO_DO_PEDIDO = case(
    (_PED.sobrou.isnot(None), case((_PED.situacao == "cancelado", CANCELADO), else_=PAGO)),
    (_PED.venda_liquido.isnot(None), PREVISTO),
    else_=SEM_VALOR,
)
# O Sobrou que entra na Margem: o da Conciliação; sem ele, o previsto (regra b).
SOBROU_DO_PEDIDO = case(
    (_PED.sobrou.isnot(None), _PED.sobrou),
    (
        _PED.venda_liquido.isnot(None),
        _PED.venda_liquido
        - func.coalesce(_PED.custo, 0)
        - func.coalesce(_PED.reembolso, 0)
        - func.coalesce(_PED.venda_frete_pago, 0)
        - func.coalesce(_PED.imposto, 0),
    ),
    else_=None,
)


@dataclass
class PedidosDaLoja:
    """Os pedidos de uma loja na Conciliação, no período. `inicio`: o início
    mais recente das contas da Conciliação da loja (None = nenhuma conta)."""

    sobrou: Decimal = ZERO
    custo: Decimal = ZERO
    pagos: int = 0
    previstos: int = 0
    cancelados: int = 0
    sem_valor: int = 0
    inicio: date | None = None

    @property
    def pedidos(self) -> int:
        return self.pagos + self.previstos + self.cancelados + self.sem_valor

    def contar(self, tipo: str, sobrou: Decimal | None, custo: Decimal | None, n: int) -> None:
        """Soma um grupo de `n` pedidos do mesmo tipo. O "sem valor de venda"
        só conta no número: nem o sobrou (não tem) nem o custo entram."""
        if tipo == SEM_VALOR:
            self.sem_valor += n
            return
        self.sobrou += sobrou or ZERO
        self.custo += custo or ZERO
        if tipo == PAGO:
            self.pagos += n
        elif tipo == CANCELADO:
            self.cancelados += n
        else:
            self.previstos += n

    def marcar_inicio(self, inicio: date | None) -> None:
        if inicio is not None and (self.inicio is None or inicio > self.inicio):
            self.inicio = inicio


def _comeco_do_dia(dia: date) -> datetime:
    return datetime.combine(dia, time.min, tzinfo=_BRT)


async def pedidos_das_lojas(
    session: AsyncSession,
    inicio: date,
    fim: date,
    bling_das_linhas: Mapping[str, str],
    integ_por_loja: Mapping[UUID, set[UUID]],
) -> dict[str, PedidosDaLoja]:
    """Linha do Faturamento (`store_id`) → os pedidos dela na Conciliação nos
    dias [inicio, fim] (de Brasília).

    `bling_das_linhas`: a loja do Bling de cada linha (a loja cadastrada pelo
    `bling_store_id`, a órfã "Sem cadastro" pelo próprio id). `integ_por_loja`:
    as integrações de cada loja cadastrada (regra 1 de faturamento_contas) —
    junto com a `bling_loja` da conta (Temu), diz quais lojas TÊM conta na
    Conciliação mesmo sem pedido no período (a célula fica "—" e a tela
    deixa a conta e o Ads dela fora do Total, como no exemplo aprovado).
    Linha sem conta e sem pedido não volta ("—")."""
    contas = {
        integ: (comeco, (bling or "").strip() or None)
        for integ, comeco, bling in (
            await session.execute(
                select(
                    ConciliacaoLoja.integration_id,
                    ConciliacaoLoja.inicio,
                    ConciliacaoLoja.bling_loja,
                ).where(ConciliacaoLoja.ativa.is_(True))
            )
        ).all()
    }
    if not contas:
        return {}
    linha_do_bling = {bling: linha for linha, bling in bling_das_linhas.items() if bling}
    out: dict[str, PedidosDaLoja] = defaultdict(PedidosDaLoja)

    # As lojas que têm conta na Conciliação (pela integração ou pela loja do
    # Bling escrita na conta), com o início dela.
    loja_da_integ = _loja_da_integracao(dict(integ_por_loja))
    for integ, (comeco, bling) in contas.items():
        linha = linha_do_bling.get(bling) if bling else None
        if linha is None:
            dona = loja_da_integ.get(integ)
            linha = str(dona) if dona is not None else None
        if linha is not None and linha in bling_das_linhas:
            out[linha].marcar_inicio(comeco)

    # A loja do Bling de cada pedido pelo bling_id (bling_orders tem uma linha
    # por item; a loja é a mesma em todas — lê a do 1º item pelo índice único).
    loja_do_pedido = (
        select(BlingOrder.loja)
        .where(BlingOrder.bling_id == _PED.bling_id)
        .order_by(BlingOrder.item_index)
        .limit(1)
        .scalar_subquery()
    )
    por_pedido = (
        select(
            loja_do_pedido.label("loja"),
            _PED.integration_id.label("integration_id"),
            TIPO_DO_PEDIDO.label("tipo"),
            SOBROU_DO_PEDIDO.label("sobrou"),
            func.coalesce(_PED.custo, 0).label("custo"),
        )
        .where(
            _PED.integration_id.in_(list(contas)),
            _PED.data_pedido >= _comeco_do_dia(inicio),
            _PED.data_pedido < _comeco_do_dia(fim + timedelta(days=1)),
            _PED.situacao != "fora_da_loja",
            _PED.bling_id.isnot(None),
        )
        .subquery("por_pedido")
    )
    rows = (
        await session.execute(
            select(
                por_pedido.c.loja,
                por_pedido.c.integration_id,
                por_pedido.c.tipo,
                func.sum(por_pedido.c.sobrou),
                func.sum(por_pedido.c.custo),
                func.count(),
            )
            .where(por_pedido.c.loja.isnot(None))
            .group_by(por_pedido.c.loja, por_pedido.c.integration_id, por_pedido.c.tipo)
        )
    ).all()
    for loja_bling, integ, tipo, sobrou, custo, n in rows:
        linha = linha_do_bling.get(str(loja_bling).strip())
        if linha is None:
            continue  # loja do Bling que não está na tela (fora do escopo/filtro)
        res = out[linha]
        res.marcar_inicio(contas[integ][0])
        res.contar(tipo, sobrou, custo, int(n))
    return dict(out)


@dataclass
class Margem:
    """A Margem de uma linha, pronta para a tela. `valor` = (sobrou − ads) ÷
    custo, em fração (0,252 = 25,2%); None = "—" (sem custo para dividir ou
    período antes do início da Conciliação). `ads_com_dado` False = a loja
    não tem Ads no Marketing no período (conta 0)."""

    valor: Decimal | None
    sobrou: Decimal
    ads: Decimal
    ads_com_dado: bool
    custo: Decimal
    pedidos: PedidosDaLoja
    antes_do_inicio: bool


def margem(pedidos: PedidosDaLoja, ads: Decimal | None, inicio: date) -> Margem:
    """(Σ sobrou − Ads) ÷ Σ custo. `ads`: a parte de Ads do Investimento da
    loja (None = sem dado = 0). `inicio`: o 1º dia do período."""
    antes = pedidos.inicio is not None and inicio < pedidos.inicio
    gasto = ads if ads is not None else ZERO
    valor = None
    if not antes and pedidos.custo > 0:
        valor = (pedidos.sobrou - gasto) / pedidos.custo
    return Margem(
        valor=valor,
        sobrou=pedidos.sobrou,
        ads=gasto,
        ads_com_dado=ads is not None,
        custo=pedidos.custo,
        pedidos=pedidos,
        antes_do_inicio=antes,
    )
