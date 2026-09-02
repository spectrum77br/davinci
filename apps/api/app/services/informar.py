"""Montagem das mensagens dos botões INFORMAR (Threema, admin-only).

Funções PURAS (sem banco) pra dar teste fácil:
- `linhas_logistica`: uma linha por pedido acompanhado no painel Logística, no
  formato pedido do usuário — `pedido marketplace - conta - status plataforma
  - status bling`.
- `linhas_estoque`: uma linha por pedido movido pra Aguardando Cancelamento
  por falta de estoque (espelha o aviso automático do sweep de NF).
- `mensagem_margem_pedido`/`mensagens_margem`: UMA mensagem POR PEDIDO da
  Margem, com conta, motivo, margem vs mínima e lucro em R$ — pedido do
  Eduardo (02/09): "veio todas as margens que deram negativa juntos, tem que
  ser separado com o nome da conta, a diferença de valor". Usadas pelo botão
  Informar da Margem E pelo aviso automático do auto-hold.
- `montar_mensagens`: fatia as linhas em mensagens que cabem no limite de
  3500 bytes do Threema Basic (com folga), numerando as partes.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import NamedTuple

from app.models import Logistica
from app.services import logistica_rules

# Folga sob os 3500 bytes do send_simple (cabeçalho + margem de segurança).
_MAX_BYTES_MENSAGEM = 3200


def linhas_logistica(rows: Iterable[Logistica]) -> list[str]:
    """`pedido marketplace - conta - status plataforma - status bling`, em
    ordem de plataforma > conta > pedido (agrupa visualmente por marketplace).
    Campo vazio vira "-"; sem pedido de marketplace cai no pedido Bling."""
    itens: list[tuple[str, str, str, str]] = []
    for r in rows:
        assinatura = logistica_rules.assinatura_para(r.plataforma, r.meli_status or {})
        pedido = (r.pedido_marketplace or "").strip() or (r.pedido_bling or "").strip()
        linha = " - ".join(
            [
                pedido or "-",
                (r.conta or "").strip() or "-",
                (assinatura or "").strip() or "-",
                (r.status_bling or "").strip() or "-",
            ]
        )
        itens.append(
            (
                (r.plataforma or "").strip().lower(),
                (r.conta or "").strip().lower(),
                pedido,
                linha,
            )
        )
    return [t[3] for t in sorted(itens)]


def linhas_estoque(entries: Iterable[tuple[str, str, str]]) -> list[str]:
    """Entradas `(numero, rótulo da loja, skus)` → `Pedido N (loja): skus`.
    Mesmo formato do aviso automático de estoque negativo do sweep de NF."""
    out: list[str] = []
    for numero, loja, skus in sorted(entries):
        linha = f"Pedido {numero}"
        if (loja or "").strip():
            linha += f" ({loja.strip()})"
        if (skus or "").strip():
            linha += f": {skus.strip()}"
        out.append(linha)
    return out


class MargemPedido(NamedTuple):
    """Um pedido pendente da Margem, já agregado por pedido (dedup de itens).

    `margem` = a PIOR margem entre os itens que dispararam o gatilho;
    `minima` = a mínima exigida desses itens; `lucro` = soma do lucro real
    (marketplace) de todos os itens do pedido. Números None ficam de fora da
    mensagem (pedido segurado por saldo, por exemplo, não tem margem baixa)."""

    pedido: str
    loja: str
    motivo: str
    margem: float | None = None
    minima: float | None = None
    lucro: float | None = None


def _pct(v: float) -> str:
    """Percentual em pt-BR: inteiro sem casas (`8%`), senão 1 casa (`-3,2%`).

    Arredonda ANTES de decidir — o valor chega de `fração × 100` e a dízima
    binária faria 7.000000000000001 virar "7,0%" em vez de "7%"."""
    v = round(v, 1)
    if v == int(v):
        return f"{int(v)}%"
    return f"{v:.1f}%".replace(".", ",")


def _moeda(v: float) -> str:
    """`R$ -1.234,56` — 2 casas, vírgula decimal, ponto de milhar."""
    s = f"{v:,.2f}".replace(",", "\0").replace(".", ",").replace("\0", ".")
    return f"R$ {s}"


def mensagem_margem_pedido(
    p: MargemPedido, *, cabecalho: str, rodape: str | None = None
) -> str:
    """Texto de UM pedido da Margem pro Threema.

    Cabeçalho, `Pedido N — loja`, motivo, margem vs mínima e lucro (linhas de
    número só quando o dado existe), e um rodapé opcional (o aviso automático
    usa pra dizer que a situação foi movida)."""
    linhas = [cabecalho]
    titulo = f"Pedido {p.pedido}"
    if (p.loja or "").strip():
        titulo += f" — {p.loja.strip()}"
    linhas.append(titulo)
    linhas.append(f"Motivo: {p.motivo}")
    if p.margem is not None:
        margem = f"Margem: {_pct(p.margem)}"
        if p.minima is not None:
            margem += f" (mínimo {_pct(p.minima)})"
        linhas.append(margem)
    if p.lucro is not None:
        linhas.append(f"Lucro: {_moeda(p.lucro)}")
    if rodape:
        linhas.append(rodape)
    return "\n".join(linhas)


def mensagens_margem(
    entries: Iterable[MargemPedido],
    cabecalho: str,
    *,
    rodape_pedido: Callable[[str], str] | None = None,
) -> list[str]:
    """Relatório do botão Informar da Margem: UMA mensagem por pedido, em ordem
    de pedido, com `(i/n)` no cabeçalho quando há mais de um. Sem pendentes →
    lista vazia (quem chama manda o texto de "nada a informar").

    `rodape_pedido(pedido)` opcional gera um rodapé por mensagem — o router usa
    pro link "Aprovar pelo celular" de cada pedido."""
    ordenados = sorted(entries, key=lambda p: p.pedido)
    total = len(ordenados)
    out: list[str] = []
    for i, p in enumerate(ordenados, start=1):
        cab = cabecalho if total == 1 else f"{cabecalho} ({i}/{total})"
        rodape = rodape_pedido(p.pedido) if rodape_pedido else None
        out.append(mensagem_margem_pedido(p, cabecalho=cab, rodape=rodape))
    return out


def montar_mensagens(
    cabecalho: str, linhas: list[str], *, max_bytes: int = _MAX_BYTES_MENSAGEM
) -> list[str]:
    """Junta cabeçalho + linhas em 1..N textos prontos pro Threema.

    Lista longa é fatiada pra caber no limite por mensagem; quando há mais de
    uma parte o cabeçalho ganha `— parte i/n`. Sem linhas → lista vazia (quem
    chama decide o que mandar no caso "nada a informar")."""
    if not linhas:
        return []
    blocos: list[list[str]] = [[]]
    tamanho = 0
    for linha in linhas:
        peso = len(linha.encode("utf-8")) + 1  # +1 do \n
        if blocos[-1] and tamanho + peso > max_bytes:
            blocos.append([])
            tamanho = 0
        blocos[-1].append(linha)
        tamanho += peso
    total = len(blocos)
    out: list[str] = []
    for i, bloco in enumerate(blocos, start=1):
        head = cabecalho if total == 1 else f"{cabecalho} — parte {i}/{total}"
        out.append(head + "\n" + "\n".join(bloco))
    return out
