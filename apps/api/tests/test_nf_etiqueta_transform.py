"""Transforma a etiqueta pra a "visualização" da impressão (item 2 da Fase 3b).

Cobre a camada PURA `transformar_etiqueta(pdf_bytes, nome?) -> bytes` com uma
etiqueta SINTÉTICA (sem depender de nenhum PDF de amostra): as 3 regras do
faturador — remetente=destinatário, sem bloco NF, sem logo do marketplace — e
a preservação do QR code.
"""

from __future__ import annotations

import fitz  # PyMuPDF
import pytest

from app.services.nf_etiqueta_transform import (
    EtiquetaTransformError,
    transformar_etiqueta,
)

_CHAVE = "35260730734713000140550040000029931203736630"

# PDF válido com zero páginas (o fitz não SALVA um doc sem páginas, então o
# caso "pdf_vazio" precisa vir de bytes montados à mão).
_PDF_SEM_PAGINAS = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\n"
    b"trailer<</Root 1 0 R>>\n"
    b"%%EOF"
)


def _etiqueta_sintetica(*, com_logo: bool = True) -> bytes:
    """Monta uma etiqueta tipo Correios: DESTINATÁRIO/REMETENTE, bloco DANFE com
    a chave, um logo pequeno no topo (marketplace) e um QR quadrado (preservar).
    """
    doc = fitz.open()
    page = doc.new_page(width=300, height=442)
    page.insert_text((20, 20), "DESTINATÁRIO", fontsize=7, fontname="helv")
    page.insert_text((20, 32), "Fulano De Tal", fontsize=8, fontname="helv")
    page.insert_text((20, 280), "REMETENTE", fontsize=7, fontname="helv")
    page.insert_text((20, 294), "Loja Origem XYZ", fontsize=8, fontname="helv")
    page.insert_text((20, 340), "DANFE SIMPLIFICADO - ETIQUETA", fontsize=6, fontname="helv")
    page.insert_text((20, 350), _CHAVE, fontsize=5, fontname="helv")
    page.insert_text(
        (5, 392), "SKU: 1; Total Items: 1; #UP123; Deadline:05/08/2026",
        fontsize=6, fontname="helv",
    )
    page.insert_text((5, 402), "1. dg053.sp+a001.sp", fontsize=6, fontname="helv")
    if com_logo:
        logo = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 79, 22), False)
        logo.set_rect(logo.irect, (200, 120, 255))
        page.insert_image(fitz.Rect(216, 14, 260, 27), pixmap=logo)
    qr = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 60, 60), False)
    qr.set_rect(qr.irect, (0, 0, 0))
    page.insert_image(fitz.Rect(20, 60, 80, 120), pixmap=qr)
    return doc.tobytes()


def _etiqueta_jt() -> bytes:
    """Etiqueta tipo J&T (TikTok): o nome do destinatário começa um pouco à
    ESQUERDA do rótulo e a chave da NF vem solta, sem o rótulo DANFE/NF:.
    """
    doc = fitz.open()
    page = doc.new_page(width=300, height=442)
    page.insert_text((42.9, 20), "DESTINATÁRIO", fontsize=7, fontname="helv")
    page.insert_text((34, 32), "yasmin", fontsize=8, fontname="helv")
    page.insert_text((34, 280), "REMETENTE:", fontsize=7, fontname="helv")
    page.insert_text((34, 292), "m**1", fontsize=8, fontname="helv")
    page.insert_text((60, 376), _CHAVE, fontsize=5, fontname="helv")
    return doc.tobytes()


def _texto(pdf_bytes: bytes) -> str:
    return fitz.open(stream=pdf_bytes, filetype="pdf")[0].get_text()


def _imagens_renderizadas(pdf_bytes: bytes) -> list[tuple[int, int]]:
    page = fitz.open(stream=pdf_bytes, filetype="pdf")[0]
    return [
        (round(r.width), round(r.height))
        for img in page.get_images(full=True)
        for r in page.get_image_rects(img[0])
    ]


def test_remetente_vira_destinatario():
    out = transformar_etiqueta(_etiqueta_sintetica())
    txt = _texto(out)
    # o nome antigo do remetente sumiu e virou o nome do destinatário
    assert "Loja Origem" not in txt
    assert txt.count("Fulano") == 2


def test_remove_bloco_nf():
    out = transformar_etiqueta(_etiqueta_sintetica())
    txt = _texto(out)
    assert "DANFE" not in txt
    assert _CHAVE not in txt


def test_remove_logo_preserva_qr():
    imgs = _imagens_renderizadas(transformar_etiqueta(_etiqueta_sintetica()))
    # o logo pequeno (79x22) sai; o QR quadrado (60x60) fica
    assert (60, 60) in imgs
    assert (79, 22) not in imgs


def test_remove_rodape_picking():
    out = transformar_etiqueta(_etiqueta_sintetica())
    txt = _texto(out)
    assert "Total Items" not in txt
    assert "Deadline" not in txt
    assert "dg053.sp+a001.sp" not in txt
    # o resto da etiqueta continua lá
    assert "Fulano" in txt


def test_destinatario_nome_sobrepoe_o_lido():
    out = transformar_etiqueta(_etiqueta_sintetica(), destinatario_nome="Beltrano Oficial")
    txt = _texto(out)
    assert "Beltrano Oficial" in txt
    assert "Loja Origem" not in txt


def test_etiqueta_jt_nome_desalinhado_e_chave_sem_rotulo():
    txt = _texto(transformar_etiqueta(_etiqueta_jt()))
    # 1) o nome do destinatário é lido mesmo começando à esquerda do rótulo
    assert "m**1" not in txt
    assert txt.count("yasmin") == 2
    # 2) a chave da NF sai mesmo sem o rótulo DANFE/NF: como âncora
    assert _CHAVE not in txt


def test_pdf_invalido_levanta():
    with pytest.raises(EtiquetaTransformError):
        transformar_etiqueta(b"nao sou um pdf")


def test_pdf_vazio_levanta():
    with pytest.raises(EtiquetaTransformError):
        transformar_etiqueta(_PDF_SEM_PAGINAS)
