"""Recorta a Declaração de Conteúdo pro armazém que vai despachar.

Pedido que se divide entre armazéns (ex. 292860: `dg053.ci+a001.ci` sai do CI e
`a055.sa` sai do SA) tem UMA etiqueta só no banco — `nf_etiqueta_arquivo` é
chaveada por pedido. Como cada armazém despacha o seu, a etiqueta impressa em
cada um não pode listar o item do outro: o conferente separaria produto que não
está com ele.

A face da etiqueta não tem SKU (o rodapé de picking já é apagado por
`nf_etiqueta_transform`). Os SKUs aparecem só na tabela "IDENTIFICAÇÃO DOS BENS"
da Declaração de Conteúdo — é essa tabela que este módulo recorta, apagando as
linhas dos outros armazéns palavra por palavra (a grade de linhas do quadro fica
intacta) e corrigindo o "Total".

Age SÓ quando o pedido de fato se divide (itens de 2+ armazéns) e quando sobra
alguma linha pro chamador — senão devolve o PDF original, que é sempre melhor do
que uma declaração vazia.
"""
from __future__ import annotations

import fitz

from app.services.nf_etiqueta_transform import _plano_da_linha, _redigir
from app.services.sku_tags import classify_sku_tag

# Folga entre a base do número da linha e o traço da grade acima dela.
_FOLGA_LINHA = 2.5


def _colunas(page: fitz.Page) -> tuple[float, float, float] | None:
    """(x0 do SKU, x0 da DESCRIÇÃO, x0 da QTD) do cabeçalho da tabela."""
    sku = page.search_for("SKU")
    desc = page.search_for("DESCRIÇÃO")
    qtd = page.search_for("QTD")
    if not sku or not desc or not qtd:
        return None
    return (sku[0].x0, desc[0].x0, qtd[0].x0)


def _texto_coluna(words: list, x0: float, x1: float, y0: float, y1: float) -> str:
    alvo = [w for w in words if x0 - 2 <= w[0] < x1 - 2 and y0 <= w[1] < y1]
    # O SKU composto quebra em fragmentos ("dg053.ci+a0" / "01.ci") — junta na
    # ordem de leitura pra remontar o código inteiro.
    alvo.sort(key=lambda w: (round(w[1], 1), w[0]))
    return "".join(w[4] for w in alvo).strip()


def _filtrar_pagina(page: fitz.Page, tags: set[str]) -> bool:
    """Apaga da tabela as linhas de outros armazéns. True se mexeu na página."""
    if not page.search_for("IDENTIFICAÇÃO DOS BENS"):
        return False
    cols = _colunas(page)
    if not cols:
        return False
    sku_x, desc_x, qtd_x = cols
    totais = page.search_for("Total")
    if not totais:
        return False
    fim_tabela = min(t.y0 for t in totais)

    words = page.get_text("words")
    cabecalho = max(
        (w[3] for w in words if w[4] == "SKU" and abs(w[0] - sku_x) < 2), default=None
    )
    if cabecalho is None:
        return False

    # Cada item começa com o seu número de ordem na coluna à esquerda do SKU.
    marcadores = sorted(
        (w for w in words
         if w[2] <= sku_x - 2 and cabecalho < w[1] < fim_tabela and w[4].isdigit()),
        key=lambda w: w[1],
    )
    if len(marcadores) < 2:
        return False  # uma linha só: não há o que recortar

    faixas = []
    for i, marc in enumerate(marcadores):
        topo = marc[1] - _FOLGA_LINHA
        base = (
            marcadores[i + 1][1] if i + 1 < len(marcadores) else fim_tabela
        ) - _FOLGA_LINHA
        sku = _texto_coluna(words, sku_x, desc_x, topo, base)
        faixas.append((topo, base, sku))

    if len({t for _, _, s in faixas if (t := classify_sku_tag(s))}) < 2:
        return False  # pedido de um armazém só

    manter = [f for f in faixas if classify_sku_tag(f[2]) in tags]
    if not manter or len(manter) == len(faixas):
        return False  # nada a tirar — ou tirar tudo, que deixaria a etiqueta inútil

    for topo, base, sku in faixas:
        if classify_sku_tag(sku) in tags:
            continue
        # Palavra por palavra e SEM tarja branca (`fill=None`): a base do
        # texto encosta no traço da grade, então um retângulo pintado por
        # cima comeria pedaço da linha do quadro. `apply_redactions` já
        # apaga o texto de verdade; o fundo da declaração é branco.
        for w in words:
            if topo <= w[1] < base:
                page.add_redact_annot(fitz.Rect(w[0], w[1], w[2], w[3]), fill=None)

    plano = _plano_total(page, words, manter, qtd_x, fim_tabela)
    page.apply_redactions()
    if plano:
        texto, x, baseline, fs = plano
        # Depois do apply_redactions — senão a própria redação apagaria o
        # número recém-escrito.
        page.insert_text((x, baseline), texto, fontsize=fs, fontname="helv", color=(0, 0, 0))
    return True


def _plano_total(
    page: fitz.Page, words: list, manter: list, qtd_x: float, fim_tabela: float
) -> tuple[str, float, float, float] | None:
    """Marca o "Total" antigo pra redação e devolve como reescrever a soma."""
    total = 0
    for topo, base, _ in manter:
        texto = _texto_coluna(words, qtd_x, page.rect.width + 1, topo, base)
        total += int(texto) if texto.isdigit() else 1

    alvo = [
        w for w in words
        if w[1] >= fim_tabela - 1 and w[0] >= qtd_x - 5 and w[4].isdigit()
    ]
    if not alvo:
        return None
    caixa, _, baseline, fs = _plano_da_linha(page, alvo)
    _redigir(page, caixa, pad=0)
    novo = str(total)
    # Direita alinhada, como o número original.
    x = max(w[2] for w in alvo) - fitz.get_text_length(novo, fontname="helv", fontsize=fs)
    return (novo, x, baseline, fs)


def filtrar_por_armazem(pdf_bytes: bytes, tags: list[str] | set[str]) -> bytes:
    """Devolve a etiqueta com a declaração recortada pros armazéns `tags`.

    Qualquer imprevisto (PDF ilegível, layout diferente, nenhuma linha do
    chamador) devolve os bytes originais — imprimir a etiqueta completa é
    sempre preferível a não imprimir.
    """
    alvo = {t.strip().lower() for t in tags if t and t.strip()}
    if not alvo or not pdf_bytes:
        return pdf_bytes
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        mexeu = any(_filtrar_pagina(page, alvo) for page in doc)
        return doc.tobytes() if mexeu else pdf_bytes
    except Exception:  # noqa: BLE001 - degrada pra etiqueta inteira
        return pdf_bytes
