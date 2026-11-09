"""Ajudante dos testes: DECLARAÇÃO DE CONTEÚDO sintética nos dois layouts reais.

Nenhum PDF de verdade (nome, CPF, endereço de cliente) entra no repositório: a
página é montada com a GEOMETRIA medida nas etiquetas reais e dados fictícios.

- `declaracao_upseller`: página 297×421, grade de segmentos de linha lw 1 com
  as colunas em 5.5|21.7|82.0|208.3|257.1|291.5, cabeçalho na baseline 102,
  linhas em 9 pt (altura 9n+3,5), Total cinza com 'Total' em x 238 e, abaixo,
  a caixa "DECLARAÇÃO" com texto e assinatura (ML/Shopee/Amazon). Sem rabo =
  TikTok. O texto sai em Helvetica (a ArialUnicodeMS do Upseller não embute).
- `declaracao_nossa`: a do `~/nf-executor/temu_declaracao.py` (Temu/
  AliExpress), 283×435, uma célula `re` lw 0,5 por campo, Helvetica 6,5.
"""

from __future__ import annotations

from collections.abc import Sequence

import fitz  # PyMuPDF

_XS = (5.5, 21.7, 82.0, 208.3, 257.1, 291.5)
_SEG = ((5.0, 22.2), (22.2, 82.5), (82.5, 208.8), (208.8, 257.6), (257.6, 292.0))
_CINZA = (0.75294, 0.75294, 0.75294)
DATA = "01-01-2026"

Linha = tuple  # (sku, descricao, variacao, qtd); textos podem ser str ou list[str]


def _linhas(v: str | list[str]) -> list[str]:
    return list(v) if isinstance(v, list) else [v]


def _etiqueta(doc: fitz.Document) -> None:
    """1ª página: a etiqueta de transporte (o que a declaração acompanha)."""
    page = doc.new_page(width=300, height=442)
    page.insert_text((20, 20), "DESTINATÁRIO", fontsize=7, fontname="helv")
    page.insert_text((20, 32), "Fulano De Tal", fontsize=8, fontname="helv")
    page.insert_text((20, 44), "Rua Exemplo, 100 - Cidade Teste", fontsize=7, fontname="helv")
    page.insert_text((20, 280), "REMETENTE", fontsize=7, fontname="helv")
    page.insert_text((20, 294), "Loja Origem XYZ", fontsize=8, fontname="helv")


def _hor(page: fitz.Page, y: float) -> None:
    for x0, x1 in _SEG:  # o Upseller corta a horizontal por coluna
        page.draw_line((x0, y), (x1, y), width=1.0)


def pagina_upseller(
    doc: fitz.Document,
    linhas: list[Linha],
    *,
    com_total: bool = True,
    com_rabo: bool = True,
    altura: float = 421,
) -> None:
    p = doc.new_page(width=297, height=altura)

    def t(x: float, y: float, s: str, fs: float = 7) -> None:
        p.insert_text((x, y), s, fontsize=fs, fontname="helv")

    t(70, 22, "DECLARAÇÃO DE CONTEÚDO", 12)
    t(80, 34, "Código de Rastreamento: XX000000000BR", 8)
    for y in (40.0, 48.0, 78.0):
        p.draw_line((5.0, y), (292.0, y), width=1.0)
    for x in (5.5, 148.5, 291.5):
        p.draw_line((x, 39.5), (x, 78.5), width=1.0)
    t(55, 46, "REMETENTE")
    t(195, 46, "DESTINATÁRIO")
    for k, (rem, dst) in enumerate(
        (
            ("NOME: Loja Origem XYZ", "NOME: Fulano De Tal"),
            ("CPF/CNPJ:", "CPF/CNPJ: 00000000000"),
            ("CEP:", "CEP: 00000000"),
        )
    ):
        t(9, 56 + 7 * k, rem)
        t(152, 56 + 7 * k, dst)

    # IDENTIFICAÇÃO DOS BENS + cabeçalho
    for y in (83.2, 93.4):
        p.draw_line((5.0, y), (292.0, y), width=1.0)
    t(102.5, 90.9, "IDENTIFICAÇÃO DOS BENS")
    t(10.9, 100.5, "Nº", 5)  # o Upseller sobe o 'Nº' (5 pt) pra alinhar pelo topo
    for x, rot in ((26.2, "SKU"), (86.5, "DESCRIÇÃO"), (212.8, "VARIAÇÃO"), (273.2, "QTD")):
        t(x, 102.0, rot)
    for x in (5.5, 291.5):
        p.draw_line((x, 82.8), (x, 105.0), width=1.0)
    for x in (21.7, 82.0, 208.3, 257.1):
        p.draw_line((x, 93.9), (x, 105.0), width=1.0)
    _hor(p, 104.5)

    y = 104.5
    total = 0
    for i, (sku, desc, var, q) in enumerate(linhas, 1):
        cels = [[str(i)], _linhas(sku), _linhas(desc), _linhas(var)]
        n = max(len(c) for c in cels)
        h = 9 * n + 3.5
        for k, x in enumerate((11.1, 26.2, 86.5, 212.8)):
            for j, s in enumerate(cels[k]):
                if s:
                    t(x, y + 10 + 9 * j, s, 9)
        qs = str(q) if q == 1 else f"({q})"  # o Upseller usa '（2）'
        t(288.0 - fitz.get_text_length(qs, fontname="helv", fontsize=9), y + 10, qs, 9)
        for x in _XS:
            p.draw_line((x, y + 0.5), (x, y + h + 0.5), width=1.0)
        _hor(p, y + h)
        y += h
        total += q
    if not com_total:  # tabela continua na página seguinte
        return

    y0, y1 = y, y + 11.1
    p.draw_rect(fitz.Rect(5.0, y0 + 0.5, 62.4, y1 + 0.5), color=None, fill=_CINZA, width=0)
    p.draw_rect(fitz.Rect(62.4, y0 + 0.5, 257.6, y1 + 0.5), color=None, fill=_CINZA, width=0)
    for x in (5.5, 257.1, 291.5):
        p.draw_line((x, y0 + 0.5), (x, y1 + 0.5), width=1.0)
    p.draw_line((5.0, y1), (292.0, y1), width=1.0)
    t(9.0, y0 + 8.6, DATA)
    t(238.0, y0 + 8.6, "Total")
    tot = str(total)
    t(288.0 - fitz.get_text_length(tot, fontname="helv", fontsize=7), y0 + 8.6, tot)

    if com_rabo:
        top = y1 + 5.0
        p.draw_line((5.0, top), (292.0, top), width=1.0)
        p.draw_line((5.0, top + 10.1), (292.0, top + 10.1), width=1.0)
        for x in (5.5, 291.5):
            p.draw_line((x, top - 0.5), (x, top + 101.4), width=1.0)
        t(124.4, top + 7.6, "DECLARAÇÃO")
        for k, frase in enumerate(
            (
                "Texto legal sintetico da declaracao, primeira linha do paragrafo.",
                "Segunda linha do texto legal sintetico, usada so nos testes.",
                "Terceira linha: o conteudo deste volume confere com a tabela.",
                "Quarta linha do texto legal sintetico.",
            )
        ):
            t(9.0, top + 18.7 + 7 * k, frase)
        t(9.0, top + 93.5, DATA)
        t(80.0, top + 93.5, "Assinatura do Declarante/Remetente")
        p.draw_line((200.0, top + 94.0), (290.0, top + 94.0), width=1.0)
        p.draw_line((5.0, top + 100.9), (292.0, top + 100.9), width=1.0)


def declaracao_upseller(
    linhas: list[Linha],
    *,
    etiqueta: bool = True,
    com_total: bool = True,
    com_rabo: bool = True,
    altura: float = 421,
    paginas: int = 1,
) -> bytes:
    doc = fitz.open()
    if etiqueta:
        _etiqueta(doc)
    for _ in range(paginas):
        pagina_upseller(doc, linhas, com_total=com_total, com_rabo=com_rabo, altura=altura)
    return doc.tobytes()


# --- a nossa (temu_declaracao.pagina_declaracao) ---------------------------


def _quebrar(s: str, larg: float, tam: float, fonte: str) -> list[str]:
    linhas: list[str] = []
    atual = ""
    for palavra in (s or "").split():
        teste = f"{atual} {palavra}".strip()
        if fitz.get_text_length(teste, fontname=fonte, fontsize=tam) <= larg or not atual:
            atual = teste
        else:
            linhas.append(atual)
            atual = palavra
    linhas.append(atual)
    return linhas


def declaracao_nossa(itens: list[tuple[str, str, int]], *, etiqueta: bool = True) -> bytes:
    """itens = [(sku, descricao inteira do Bling, qtd)] — VARIAÇÃO sai vazia,
    como no executor."""
    doc = fitz.open()
    if etiqueta:
        _etiqueta(doc)
    larg, alt = 283.0, 435.0
    pg = doc.new_page(width=larg, height=alt)
    m = 8
    w = larg - 2 * m
    cinza = (0.85, 0.85, 0.85)

    def escrever(x0, y0, x1, s, tam=6.5, negrito=False, alinhar=0):
        fonte = "hebo" if negrito else "helv"
        passo = tam * 1.18
        for k, ln in enumerate(_quebrar(s, x1 - x0 - 4, tam, fonte)):
            lw = fitz.get_text_length(ln, fontname=fonte, fontsize=tam)
            x = (
                x0 + 2
                if alinhar == 0
                else (x0 + (x1 - x0 - lw) / 2 if alinhar == 1 else x1 - 2 - lw)
            )
            pg.insert_text((x, y0 + 2 + tam + k * passo), ln, fontsize=tam, fontname=fonte)

    def linha_tabela(y, cels, fundo=None, minimo=10):
        alturas = [
            len(_quebrar(c[2], c[1] - c[0] - 4, c[3], "hebo" if c[4] else "helv")) for c in cels
        ]
        h = max(
            minimo,
            max(4 + c[3] + (n - 1) * c[3] * 1.18 for c, n in zip(cels, alturas, strict=True)) + 2,
        )
        for x0, x1, s, tam, neg, al in cels:
            pg.draw_rect(fitz.Rect(x0, y, x1, y + h), color=(0, 0, 0), fill=fundo, width=0.5)
            escrever(x0, y, x1, s, tam, neg, al)
        return y + h

    escrever(m, 18, larg - m, "DECLARAÇÃO DE CONTEÚDO", 12, True, 1)
    escrever(m, 34, larg - m, "Código de Rastreamento: XX000000000BR", 7.5, False, 1)
    meio = m + w / 2
    y = linha_tabela(
        50,
        [(m, meio, "REMETENTE", 6.5, True, 1), (meio, larg - m, "DESTINATÁRIO", 6.5, True, 1)],
        cinza,
    )
    y = linha_tabela(
        y,
        [
            (m, meio, "NOME: Temu - Loja Teste CPF/CNPJ: CEP:", 6, False, 0),
            (
                meio,
                larg - m,
                "NOME: Fulano De Tal CPF/CNPJ: 00000000000 CEP: 00000000",
                6,
                False,
                0,
            ),
        ],
    )
    y += 6
    y = linha_tabela(y, [(m, larg - m, "IDENTIFICAÇÃO DOS BENS", 6.5, True, 1)], cinza)
    larguras = [14, 46, w - 14 - 46 - 40 - 22, 40, 22]
    xs = [m]
    for cw in larguras:
        xs.append(xs[-1] + cw)
    cab = ["Nº", "SKU", "DESCRIÇÃO", "VARIAÇÃO", "QTD"]
    y = linha_tabela(y, [(xs[k], xs[k + 1], cab[k], 6, True, 0 if k < 4 else 2) for k in range(5)])
    total = 0
    for i, (sku, desc, q) in enumerate(itens, 1):
        vals = [str(i), sku, desc, "", str(q)]
        y = linha_tabela(
            y, [(xs[k], xs[k + 1], vals[k], 6.5, k in (1, 4), 0 if k < 4 else 2) for k in range(5)]
        )
        total += q
    linha_tabela(
        y,
        [
            (m, xs[3], DATA, 6, False, 0),
            (xs[3], xs[4], "Total", 6, False, 2),
            (xs[4], xs[5], str(total), 6.5, True, 2),
        ],
    )
    return doc.tobytes()


def sem_declaracao() -> bytes:
    """Etiqueta sem declaração (Amazon, SEDEX)."""
    doc = fitz.open()
    _etiqueta(doc)
    return doc.tobytes()


def abrir_tabela(pdf: bytes) -> tuple[fitz.Document, fitz.Page]:
    """(documento, página da declaração) — segure o documento enquanto usa a página."""
    doc = fitz.open(stream=pdf, filetype="pdf")
    for page in doc:
        if page.search_for("IDENTIFICAÇÃO DOS BENS"):
            return doc, page
    raise AssertionError("sem página de declaração")


def texto_da_tabela(pdf: bytes) -> str:
    _doc, page = abrir_tabela(pdf)
    return page.get_text()


# --- etiqueta da Shopee com o rodapé de picking (Upseller) ------------------

CHAVE_FICTICIA = "35261100000000000000550050000001031000000000"


def _imagem(page: fitz.Page, rect: tuple[float, float, float, float], larg: int, alt: int) -> None:
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, larg, alt), False)
    pix.set_rect(pix.irect, (0, 0, 0))
    page.insert_image(fitz.Rect(*rect), pixmap=pix)


def texto_qtd_upseller(q: int) -> str:
    return "*1" if q == 1 else f"( *{q} )"


def pagina_etiqueta_shopee(
    doc: fitz.Document,
    rodape: list[tuple[str, int]] | None,
    *,
    ref: str = "UP2NYY000001",
    deadline: str | None = "09/11/2026",
    pedido_loja: str = "261107AB12CD34",
) -> None:
    """Página da ETIQUETA da Shopee (Upseller, 300×442) na geometria medida
    nas etiquetas reais (dados fictícios): fundo branco 15..282 × 10..386,
    QR, código de barras de rastreio, bloco DANFE SIMPLIFICADO com a chave e
    o código de barras dela, e o rodapé de picking — cabeçalho 9 pt na
    baseline 391,5 e itens 8 pt de 9,8 em 9,8 a partir de 401,4, quantidade
    em x 263,3 ("*1" / "( *2 )"). Fonte Helvetica (a ArialUnicodeMS do
    Upseller não embute). `rodape=None` = sem rodapé."""
    p = doc.new_page(width=300, height=442)
    p.draw_rect(fitz.Rect(15.2, 10.4, 282.5, 386.0), color=None, fill=(1, 1, 1), width=0)

    def t(x: float, y: float, s: str, fs: float = 7, fonte: str = "helv") -> None:
        p.insert_text((x, y), s, fontsize=fs, fontname=fonte)

    t(20, 20, "DESTINATÁRIO")
    t(20, 32, "Fulano De Tal", 8)
    t(20, 44, "Rua Exemplo, 100 - Cidade Teste", 7)
    t(20, 56, f"Pedido: {pedido_loja}", 7)
    _imagem(p, (209.8, 30.3, 268.3, 88.9), 40, 40)  # QR
    _imagem(p, (32.7, 226.2, 185.5, 261.9), 200, 40)  # rastreio
    t(20, 280, "REMETENTE")
    t(20, 294, "Shopee Loja Teste", 8)
    t(27.9, 317.8, "CEP: 00000000", 6.73)
    t(27.9, 326.5, "Envio previsto: 09/11/2026", 6.73)
    p.draw_rect(fitz.Rect(15.2, 329.2, 282.5, 330.6), color=None, fill=(0, 0, 0), width=0)
    t(92.3, 336.6, "DANFE SIMPLIFICADO - ETIQUETA", 6.73, "hebo")
    t(62.2, 343.4, "NF: 103", 5.39)
    t(82.9, 350.1, CHAVE_FICTICIA, 5.39)
    _imagem(p, (28.0, 353.5, 269.7, 373.7), 240, 20)  # barras da chave
    if rodape is None:
        return
    cab = f"SKU: {len(rodape)}; Total Items: {sum(q for _s, q in rodape)}; #{ref}"
    cab += f"; Deadline:{deadline}" if deadline else "; Deadline: -"
    t(5.0, 391.5, cab, 9.0)
    for i, (sku, q) in enumerate(rodape):
        y = 401.4 + 9.8 * i
        t(5.0, y, f"{i + 1}. {sku}", 8.0)
        t(263.3, y, texto_qtd_upseller(q), 8.0)


def etiqueta_shopee(
    rodape: Sequence[tuple[str, int]] | None = (("dg054.ci+a001.ci", 1),),
    *,
    ref: str = "UP2NYY000001",
    deadline: str | None = "09/11/2026",
    pedido_loja: str = "261107AB12CD34",
    declaracao: list[Linha] | None = None,
) -> bytes:
    """Etiqueta da Shopee como sai do Upseller: página da etiqueta (com o
    rodapé de picking do ANÚNCIO) + declaração de conteúdo."""
    doc = fitz.open()
    pagina_etiqueta_shopee(
        doc, list(rodape) if rodape is not None else None,
        ref=ref, deadline=deadline, pedido_loja=pedido_loja,
    )
    linhas = declaracao or [
        ("dg054.ci+a001.ci", ["Uranyx A17 Pro Max", "12.128"], ["Branco + Fone"], 1)
    ]
    pagina_upseller(doc, linhas)
    return doc.tobytes()
