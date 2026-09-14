"""Relatório de certificações em PDF, sem consultas ao banco ou efeitos externos."""

from __future__ import annotations

import io
import re
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from html import escape
from textwrap import wrap

import fitz

PDF_MEDIA = "application/pdf"
_PAGINA = fitz.paper_rect("a4-l")
_MARGEM = 28.0
_TOPO_TABELA = 70.0
_CSS = """
body { margin: 0; font-family: sans-serif; font-size: 9px; color: #172536; }
table { width: 100%; border-collapse: collapse; }
th, td { border: 0.5px solid #d3dedb; padding: 4px 5px; vertical-align: top;
         text-align: left; }
th { color: #ffffff; background-color: #075e48; font-size: 8px; font-weight: bold; }
.alternada { background-color: #f3f7f5; }
.valor { text-align: right; white-space: nowrap; }
.data { white-space: nowrap; }
.vazia { padding: 18px; text-align: center; color: #64748b; }
"""
_COLUNAS = (
    ("produto", "Produto", "", "78px"),
    ("modelo", "Modelo", "", "52px"),
    ("nome_comercial", "Nome comercial", "", "185px"),
    ("certificado", "Certificado", "", "60px"),
    ("numero", "Número", "", "88px"),
    ("valor", "Valor", "valor", "108px"),
    ("inicio", "Início", "data", "60px"),
    ("fim", "Fim", "data", "60px"),
)
_LIMITES_TEXTO = {
    "produto": (160, 78),
    "modelo": (80, 52),
    "nome_comercial": (500, 185),
    "certificado": (100, 60),
    "numero": (160, 88),
}


def _texto(row: object, campo: str) -> str:
    valor = getattr(row, campo, None)
    if valor is None or valor == "":
        return "-"
    if campo == "valor":
        # Decimal evita perder centavos em valores grandes do Numeric(14, 2).
        numero = f"{Decimal(str(valor)):,.2f}"
        return "R$ " + numero.translate(str.maketrans({",": ".", ".": ","}))
    if campo in {"inicio", "fim"}:
        if isinstance(valor, date):
            return valor.strftime("%d/%m/%Y")
        return date.fromisoformat(str(valor)).strftime("%d/%m/%Y")
    return str(valor)


def _trechos(row: object, campo: str) -> list[str]:
    texto = _texto(row, campo)
    if campo not in _LIMITES_TEXTO:
        return [escape(texto)]
    max_trecho, largura = _LIMITES_TEXTO[campo]
    # O renderer não divide células mais altas que a página com segurança.
    # Partimos só a apresentação, mantendo todo o conteúdo em linhas seguintes.
    trechos = wrap(
        texto,
        max_trecho,
        replace_whitespace=False,
        drop_whitespace=False,
        break_on_hyphens=False,
    )

    def quebrar_palavra(match: re.Match[str]) -> str:
        partes, atual = [], ""
        for caractere in match[0]:
            if atual and fitz.get_text_length(atual + caractere, fontsize=9) > largura:
                partes.append(atual)
                atual = ""
            atual += caractere
        return "\u200b".join([*partes, atual])

    return [escape(re.sub(r"\S+", quebrar_palavra, trecho)) for trecho in trechos] or ["-"]


def _linhas_html(rows: Sequence[object]) -> str:
    linhas = []
    for i, row in enumerate(rows):
        colunas = [_trechos(row, campo) for campo, _, _, _ in _COLUNAS]
        for parte in range(max(map(len, colunas))):
            celulas = "".join(
                f'<td class="{classe}">{trechos[parte] if parte < len(trechos) else ""}</td>'
                for trechos, (_, _, classe, _) in zip(colunas, _COLUNAS, strict=True)
            )
            linhas.append(f'<tr class="{"alternada" if i % 2 else ""}">{celulas}</tr>')
    return "".join(linhas)


def montar_pdf(rows: Sequence[object]) -> bytes:
    """Exporta todas as linhas, preservando textos completos e a ordem recebida."""
    cabecalho = "".join(
        f'<th style="width: {largura}">{rotulo}</th>' for _, rotulo, _, largura in _COLUNAS
    )
    corpo = _linhas_html(rows)
    if not rows:
        corpo = '<tr><td colspan="8" class="vazia">Nenhuma certificação cadastrada.</td></tr>'
    story = fitz.Story(
        html=(
            f'<table><thead><tr id="cabecalho">{cabecalho}</tr></thead>'
            f"<tbody>{corpo}</tbody></table>"
        ),
        user_css=_CSS,
    )
    area = fitz.Rect(_MARGEM, _TOPO_TABELA, _PAGINA.width - _MARGEM, _PAGINA.height - 35)
    cabecalho_rect = None

    def registrar_cabecalho(pos: object) -> None:
        nonlocal cabecalho_rect
        if pos.id == "cabecalho" and pos.open_close == 1:
            cabecalho_rect = fitz.Rect(pos.rect)

    saida = io.BytesIO()
    writer = fitz.DocumentWriter(saida)
    try:
        mais = True
        while mais:
            dispositivo = writer.begin_page(_PAGINA)
            mais, _ = story.place(area)
            if cabecalho_rect is None:
                story.element_positions(registrar_cabecalho)
            story.draw(dispositivo)
            writer.end_page()
            # Story não repete <thead>: reservamos seu espaço em cada página.
            if cabecalho_rect is not None:
                area.y0 = cabecalho_rect.y1
    finally:
        writer.close()

    with fitz.open(stream=saida.getvalue(), filetype="pdf") as documento:
        with fitz.open() as header:
            if len(documento) > 1 and cabecalho_rect is not None:
                header.insert_pdf(documento, from_page=0, to_page=0)
                # Retira os dados antes de reutilizar a faixa do cabeçalho. Assim,
                # copiar/buscar texto no PDF não encontra linhas fora do recorte.
                header[0].add_redact_annot(
                    fitz.Rect(0, cabecalho_rect.y1, _PAGINA.width, _PAGINA.height)
                )
                header[0].apply_redactions(images=0, graphics=2)
            for i, pagina in enumerate(documento):
                if i and cabecalho_rect is not None:
                    pagina.show_pdf_page(cabecalho_rect, header, 0, clip=cabecalho_rect)
                pagina.insert_text(
                    (_MARGEM, 37),
                    "Certificações",
                    fontname="hebo",
                    fontsize=18,
                    color=(0.04, 0.21, 0.17),
                )
                quantidade = "1 certificação" if len(rows) == 1 else f"{len(rows)} certificações"
                pagina.insert_text(
                    (_MARGEM, 54),
                    f"DaVinci  |  {quantidade}",
                    fontsize=9,
                    color=(0.36, 0.42, 0.46),
                )
                pagina.insert_textbox(
                    fitz.Rect(
                        _MARGEM, _PAGINA.height - 23, _PAGINA.width - _MARGEM, _PAGINA.height
                    ),
                    f"Página {i + 1} de {len(documento)}",
                    fontsize=8,
                    align=2,
                    color=(0.36, 0.42, 0.46),
                )
        documento.set_metadata({"title": "Certificações - DaVinci", "author": "DaVinci"})
        return documento.tobytes(garbage=4, deflate=True)
