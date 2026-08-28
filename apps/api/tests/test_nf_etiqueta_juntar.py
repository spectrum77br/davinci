"""Junção da etiqueta com a NF num PDF só (impressão Correios/ML — Fase 3b).

Cobre a camada PURA `juntar_etiqueta_nf(etiqueta_pdf, nf_pdf) -> bytes` com PDFs
SINTÉTICOS: a ordem (etiqueta antes, NF depois), a soma das páginas, o texto
preservado de cada origem e os erros de PDF inválido/vazio.
"""

from __future__ import annotations

import fitz  # PyMuPDF
import pytest

from app.services.nf_etiqueta_juntar import (
    ETIQUETA_ALTURA_PT,
    ETIQUETA_LARGURA_PT,
    EtiquetaJuntarError,
    juntar_etiqueta_nf,
    redimensionar_para_etiqueta,
)

_PDF_SEM_PAGINAS = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\n"
    b"trailer<</Root 1 0 R>>\n"
    b"%%EOF"
)


def _pdf(*textos: str) -> bytes:
    """PDF com uma página por texto (uma marca por página pra rastrear ordem)."""
    doc = fitz.open()
    for txt in textos:
        page = doc.new_page(width=300, height=442)
        page.insert_text((20, 20), txt, fontsize=10, fontname="helv")
    return doc.tobytes()


def _paginas_texto(pdf_bytes: bytes) -> list[str]:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    return [p.get_text().strip() for p in doc]


def test_junta_etiqueta_antes_da_nf():
    out = juntar_etiqueta_nf(_pdf("ETIQUETA"), _pdf("NOTA FISCAL"))
    paginas = _paginas_texto(out)
    assert paginas == ["ETIQUETA", "NOTA FISCAL"]


def test_soma_todas_as_paginas():
    # etiqueta 1 página + NF 2 páginas = 3 páginas, na ordem
    out = juntar_etiqueta_nf(_pdf("ETQ"), _pdf("NF-P1", "NF-P2"))
    assert _paginas_texto(out) == ["ETQ", "NF-P1", "NF-P2"]


def test_etiqueta_invalida_levanta():
    with pytest.raises(EtiquetaJuntarError) as exc:
        juntar_etiqueta_nf(b"nao sou pdf", _pdf("NF"))
    assert "etiqueta_invalido" in str(exc.value)


def test_nf_invalida_levanta():
    with pytest.raises(EtiquetaJuntarError) as exc:
        juntar_etiqueta_nf(_pdf("ETQ"), b"nao sou pdf")
    assert "nf_invalido" in str(exc.value)


def test_etiqueta_vazia_levanta():
    with pytest.raises(EtiquetaJuntarError) as exc:
        juntar_etiqueta_nf(_PDF_SEM_PAGINAS, _pdf("NF"))
    assert "etiqueta_vazio" in str(exc.value)


def test_nf_vazia_levanta():
    with pytest.raises(EtiquetaJuntarError) as exc:
        juntar_etiqueta_nf(_pdf("ETQ"), _PDF_SEM_PAGINAS)
    assert "nf_vazio" in str(exc.value)


def test_redimensiona_todas_as_paginas_pro_tamanho_da_etiqueta():
    # etiqueta 300×442 + NF A4 (595×842): as duas saem 104,23×152,4mm
    doc = fitz.open()
    p1 = doc.new_page(width=300, height=442)
    p1.insert_text((20, 20), "ETQ", fontsize=10, fontname="helv")
    p2 = doc.new_page(width=595, height=842)
    p2.insert_text((20, 20), "NF-A4", fontsize=10, fontname="helv")

    out = redimensionar_para_etiqueta(doc.tobytes())
    saida = fitz.open(stream=out, filetype="pdf")
    assert [p.get_text().strip() for p in saida] == ["ETQ", "NF-A4"]
    for p in saida:
        assert round(p.rect.width, 1) == round(ETIQUETA_LARGURA_PT, 1)
        assert round(p.rect.height, 1) == round(ETIQUETA_ALTURA_PT, 1)


def test_redimensionar_invalido_ou_vazio_levanta():
    with pytest.raises(EtiquetaJuntarError):
        redimensionar_para_etiqueta(b"nao sou pdf")
    with pytest.raises(EtiquetaJuntarError):
        redimensionar_para_etiqueta(_PDF_SEM_PAGINAS)
