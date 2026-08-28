"""Recorte da Declaração de Conteúdo por armazém (pedido dividido).

Cobre a camada PURA `filtrar_por_armazem(pdf, tags)` com uma Declaração
SINTÉTICA que imita o layout real: cabeçalho "IDENTIFICAÇÃO DOS BENS", colunas
Nº/SKU/DESCRIÇÃO/QTD, uma linha por item e a linha do "Total".

O caso de referência é o pedido 292860: `a055.sa` sai do SA e
`dg053.ci+a001.ci` sai do CI — cada armazém só pode ver o que despacha.
"""

from __future__ import annotations

import fitz  # PyMuPDF

from app.services.nf_etiqueta_armazem import filtrar_por_armazem

# Colunas medidas na declaração real (x0 de cada uma).
_X_NUM = 10.9
_X_SKU = 26.2
_X_DESC = 86.5
_X_QTD = 273.2
_X_VALOR = 284.0


def _declaracao(itens: list[tuple[str, str, int]]) -> bytes:
    """Declaração de Conteúdo sintética com uma linha por item (sku, nome, qtd)."""
    doc = fitz.open()
    page = doc.new_page(width=297, height=421)

    def escreve(x: float, y: float, txt: str) -> None:
        page.insert_text((x, y), txt, fontsize=9, fontname="helv")

    escreve(10, 80, "IDENTIFICAÇÃO DOS BENS")
    for x, rotulo in ((_X_NUM, "Nº"), (_X_SKU, "SKU"), (_X_DESC, "DESCRIÇÃO"), (_X_QTD, "QTD")):
        escreve(x, 100, rotulo)

    y = 120.0
    for i, (sku, nome, qtd) in enumerate(itens, start=1):
        escreve(_X_NUM, y, str(i))
        escreve(_X_SKU, y, sku)
        escreve(_X_DESC, y, nome)
        escreve(_X_VALOR, y, str(qtd))
        y += 20

    escreve(238, y + 5, "Total")
    escreve(_X_VALOR, y + 5, str(sum(q for _, _, q in itens)))
    return doc.tobytes()


def _texto(pdf_bytes: bytes) -> str:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    return "\n".join(p.get_text() for p in doc)


_DIVIDIDO = [
    ("a055.sa", "Carregador 20W", 1),
    ("dg053.ci+a001.ci", "Uranyx Hotwav A17", 1),
]


def test_armazem_ve_so_o_proprio_item():
    out = filtrar_por_armazem(_declaracao(_DIVIDIDO), ["sa"])
    texto = _texto(out)
    assert "a055.sa" in texto
    assert "dg053.ci" not in texto
    assert "Uranyx" not in texto
    assert "Carregador" in texto


def test_outro_armazem_ve_o_outro_item():
    out = filtrar_por_armazem(_declaracao(_DIVIDIDO), ["ci"])
    texto = _texto(out)
    assert "dg053.ci+a001.ci" in texto
    assert "a055.sa" not in texto
    assert "Carregador" not in texto


def test_total_e_recalculado():
    original = _declaracao([("a055.sa", "Carregador", 1), ("dg053.ci", "Uranyx", 3)])
    assert "Total\n4" in _texto(original).replace(" ", "")
    out = filtrar_por_armazem(original, ["sa"])
    # Sobra só a linha do SA (qtd 1) — o total tem que acompanhar.
    assert "Total\n1" in _texto(out).replace(" ", "")


def test_pedido_de_um_armazem_so_nao_muda():
    """Sem divisão não há o que recortar — devolve os bytes originais."""
    pdf = _declaracao([("a055.sa", "Carregador", 1), ("a060.sa", "Cabo", 1)])
    assert filtrar_por_armazem(pdf, ["sa"]) == pdf


def test_armazem_sem_nenhuma_linha_recebe_a_etiqueta_inteira():
    """Declaração vazia seria pior que a completa — degrada pro original."""
    pdf = _declaracao(_DIVIDIDO)
    assert filtrar_por_armazem(pdf, ["pi"]) == pdf


def test_sem_tag_nao_mexe():
    pdf = _declaracao(_DIVIDIDO)
    assert filtrar_por_armazem(pdf, []) == pdf
    assert filtrar_por_armazem(pdf, [""]) == pdf


def test_pdf_sem_declaracao_passa_direto():
    doc = fitz.open()
    doc.new_page(width=300, height=442).insert_text(
        (20, 20), "ETIQUETA", fontsize=10, fontname="helv"
    )
    pdf = doc.tobytes()
    assert filtrar_por_armazem(pdf, ["sa"]) == pdf


def test_pdf_ilegivel_devolve_original():
    assert filtrar_por_armazem(b"nao sou pdf", ["sa"]) == b"nao sou pdf"
