"""Quem fica com a peça quando o saldo do Bling fica negativo — FIFO.

O `saldoVirtualTotal` do Bling é o físico menos a reserva de TODOS os pedidos
que seguram peça (Em aberto / Em digitação). Saldo negativo diz que FALTA
peça, mas não diz pra quem: o pedido mais antigo já está com a dele reservada
— quem estourou o saldo foram os mais novos.

O check antigo mandava pra Aguardando Cancelamento TODO pedido do lote com o
SKU negativo. O mais antigo, conferido primeiro, ia embora, soltava a reserva
(a 83955 não reserva) e o novo passava com a peça dele (Eduardo 30/10: "ao
invés de mudar os que estão negativos, ele muda os que já estão RESERVADOS, e
os negativos deixa").

Regra: déficit D da peça; anda do pedido MAIS NOVO pro mais antigo (data do
pedido, desempate pelo número) marcando como negativo até cobrir D. Pedido
marcado solta TODAS as peças dele (vai inteiro pra 83955), então desconta de
todas. Quem já está com a peça garantida (Em digitação, ou Em aberto já na
fila da NF) nunca é o negativo. Pedido que NÃO segura peça no Bling (ex.: já
em Aguardando Cancelamento) não tem reserva pra defender: saldo negativo
basta, como antes.

O déficit é −saldo virtual. Com o saldo FÍSICO em mãos, é o que os pedidos que
o espelho conhece pedem além do físico — reserva de pedido que o espelho ainda
não viu (ingest atrasado: em 30/10 os pedidos de 29/10 entraram de uma vez e
o mais antigo era conferido contra o saldo já comido pelos 9) fica com os mais
novos, que é o que ele é. Nunca passa do déficit que o próprio Bling mostra.

"Peça" é o que o Bling reserva: o produto simples ou, no kit, cada componente
(b026.12.18 e b026.18.24 disputam a mesma b026.18). Cálculo PURO — quem chama
busca os saldos e os pedidos e decide o que fazer com o resultado.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime

# Folga de ponto flutuante (quantidade de componente vem de Numeric).
_EPS = 1e-9


@dataclass(frozen=True)
class PedidoPecas:
    """Um pedido na disputa pela peça.

    `pecas`: peça → unidades que o pedido leva (kit já aberto nos
    componentes). `reserva`: está numa situação que segura peça no Bling (o
    saldo virtual já desconta). `garantido`: já está com a peça (etiqueta
    subida / já na fila da NF) — nunca é escolhido como negativo.
    """

    numero: str
    data: datetime | None
    pecas: Mapping[str, float] = field(default_factory=dict)
    reserva: bool = True
    garantido: bool = False


def _antiguidade(p: PedidoPecas) -> tuple:
    """Chave FIFO: data do pedido, desempate pelo número (numérico quando dá).
    Sem data = o mais novo (não dá pra provar que chegou antes)."""
    ts = p.data.timestamp() if p.data is not None else float("inf")
    n = (p.numero or "").strip()
    return (ts, 0, int(n), n) if n.isdigit() else (ts, 1, 0, n)


def negativos_fifo(
    saldos: Mapping[str, float],
    pedidos: Iterable[PedidoPecas],
    fisicos: Mapping[str, float] | None = None,
) -> dict[str, set[str]]:
    """numero → peças em que o pedido é o "negativo" (o que estourou o saldo).

    `saldos`: peça → saldo virtual AO VIVO; peça fora do mapa é desconhecida
    e nunca bloqueia. Saldo >= 0 não marca ninguém (0 ainda é atendível: o
    virtual já desconta a reserva do próprio pedido). `fisicos` (opcional):
    peça → saldo físico, pra não pôr nos pedidos conhecidos a reserva de quem
    o espelho ainda não viu.
    """
    pedidos = list(pedidos)
    deficit = {p: -float(v) for p, v in saldos.items() if v is not None and float(v) < 0}
    if fisicos:
        conhecida: dict[str, float] = {}
        for ped in pedidos:
            if ped.reserva:
                for p, q in ped.pecas.items():
                    if p in deficit and q > _EPS:
                        conhecida[p] = conhecida.get(p, 0.0) + float(q)
        for p in deficit:
            if fisicos.get(p) is not None:
                deficit[p] = min(deficit[p], conhecida.get(p, 0.0) - float(fisicos[p]))
    if not deficit:
        return {}
    negativos: dict[str, set[str]] = {}
    fila: list[PedidoPecas] = []
    for ped in pedidos:
        curtas = {p for p, q in ped.pecas.items() if p in deficit and q > _EPS}
        if not curtas or ped.garantido:
            continue
        if not ped.reserva:
            negativos.setdefault(ped.numero, set()).update(curtas)
            continue
        fila.append(ped)
    # Do mais novo pro mais antigo: o novo é que estourou o saldo.
    for ped in sorted(fila, key=_antiguidade, reverse=True):
        if all(d <= _EPS for d in deficit.values()):
            break
        faltam = {
            p for p, q in ped.pecas.items() if q > _EPS and deficit.get(p, 0.0) > _EPS
        }
        if not faltam:
            continue
        negativos.setdefault(ped.numero, set()).update(faltam)
        for p, q in ped.pecas.items():
            if p in deficit and q > _EPS:
                deficit[p] -= float(q)
    return negativos
