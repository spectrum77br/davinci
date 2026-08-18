"""Montagem das mensagens dos botões INFORMAR (Threema, admin-only).

Funções PURAS (sem banco) pra dar teste fácil:
- `linhas_logistica`: uma linha por pedido acompanhado no painel Logística, no
  formato pedido do usuário — `pedido marketplace - conta - status plataforma
  - status bling`.
- `linhas_estoque`: uma linha por pedido movido pra Aguardando Cancelamento
  por falta de estoque (espelha o aviso automático do sweep de NF).
- `montar_mensagens`: fatia as linhas em mensagens que cabem no limite de
  3500 bytes do Threema Basic (com folga), numerando as partes.
"""
from __future__ import annotations

from collections.abc import Iterable

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
