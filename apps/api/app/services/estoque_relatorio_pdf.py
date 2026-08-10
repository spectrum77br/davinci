"""Relatório dos pedidos selecionados como páginas PDF (A4 paisagem).

Serve a impressão "etiquetas + relatório num PDF só" da aba Pedidos: as
etiquetas vêm primeiro (é o que cola no volume) e estas páginas fecham o
documento, pro operador conferir o que despachou sem trocar de papel.

Camada PURA — recebe as linhas já prontas (dicts) e devolve bytes de PDF.
Quem consulta o banco é o router.
"""

from __future__ import annotations

import io
from html import escape
from typing import Any

import fitz  # PyMuPDF

# A4 paisagem em pontos (842 x 595) com margem de ~8mm.
_PAGINA = fitz.paper_rect("a4-l")
_MARGEM = 22.0

_COLUNAS: list[tuple[str, str, str]] = [
    # (chave, rótulo, classe css)
    ("data_envio", "Data Envio", "c"),
    ("loja", "Nome da Loja", "loja"),
    ("pedido_bling", "Pedido Bling", "c"),
    ("pedido_marketplace", "Pedido Marketplace", "mono"),
    ("cliente", "Cliente", "l"),
    ("sku", "Código (SKU)", "mono"),
    ("quantidade", "Qtd", "c"),
    ("produto", "Descrição", "l"),
]

_CSS = """
body { font-family: sans-serif; font-size: 8px; color: #000; }
h1 { font-size: 10px; margin: 0 0 4px 0; }
table { width: 100%; border-collapse: collapse; }
th, td { border: 1px solid #000; padding: 2px 3px; text-align: center;
         vertical-align: top; }
th { font-weight: bold; background-color: #eeeeee; }
td.l { text-align: left; }
td.loja { text-align: left; color: #558b2f; font-weight: bold; }
td.mono { font-family: monospace; }
.corte { font-size: 6px; color: #555555; }
"""


def _celula(linha: dict[str, Any], chave: str, classe: str) -> str:
    valor = linha.get(chave)
    texto = escape("" if valor is None else str(valor))
    if chave == "loja" and linha.get("corte"):
        texto += f'<div class="corte">{escape(str(linha["corte"]))}</div>'
    return f'<td class="{classe}">{texto or "—"}</td>'


def relatorio_pedidos_pdf(linhas: list[dict[str, Any]], titulo: str) -> bytes:
    """Renderiza a tabela do relatório em uma ou mais páginas A4 paisagem."""
    corpo = "".join(
        "<tr>" + "".join(_celula(ln, k, c) for k, _, c in _COLUNAS) + "</tr>"
        for ln in linhas
    )
    cabecalho = "".join(f"<th>{escape(rot)}</th>" for _, rot, _ in _COLUNAS)
    html = (
        f"<h1>{escape(titulo)}</h1>"
        f"<table><thead><tr>{cabecalho}</tr></thead><tbody>{corpo}</tbody></table>"
    )

    story = fitz.Story(html=html, user_css=_CSS)
    area = _PAGINA + (_MARGEM, _MARGEM, -_MARGEM, -_MARGEM)
    saida = io.BytesIO()
    writer = fitz.DocumentWriter(saida)
    mais = True
    while mais:
        dispositivo = writer.begin_page(_PAGINA)
        mais, _ = story.place(area)
        story.draw(dispositivo)
        writer.end_page()
    writer.close()
    return saida.getvalue()
