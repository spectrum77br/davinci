"""Junção da etiqueta com a NF num PDF só (impressão Correios/ML — Fase 3b).

Regra do faturador (áudios 25/07): na impressão tipo Correios (ML) a etiqueta
NÃO leva declaração de conteúdo — leva a NF junto. O operador imprime UM PDF
que já traz a etiqueta (pra colar no pacote) seguida da NF-e (pra ir dentro).

Camada PURA (sem rede, sem banco): `juntar_etiqueta_nf(etiqueta_pdf, nf_pdf)`.
A etiqueta vem antes (é a 1ª página, a que cola no volume); a NF vem depois.
Quem baixa o PDF da NF do Bling e o blob da etiqueta transformada é outra
camada — aqui só se juntam os bytes.
"""

from __future__ import annotations

import fitz  # PyMuPDF


class EtiquetaJuntarError(RuntimeError):
    """Falha ao abrir/juntar os PDFs da etiqueta e da NF."""


def _abrir(pdf_bytes: bytes, rotulo: str) -> fitz.Document:
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise EtiquetaJuntarError(f"{rotulo}_invalido: {exc}") from exc
    if doc.page_count == 0:
        raise EtiquetaJuntarError(f"{rotulo}_vazio")
    return doc


def juntar_etiqueta_nf(etiqueta_pdf: bytes, nf_pdf: bytes) -> bytes:
    """Concatena etiqueta (primeiro) + NF (depois) num único PDF.

    A ordem é fixa: etiqueta na frente porque é a página que cola no volume;
    a NF-e vai atrás pra ir dentro do pacote. Levanta `EtiquetaJuntarError`
    se qualquer um dos dois PDFs for inválido ou vazio.
    """
    etiqueta = _abrir(etiqueta_pdf, "etiqueta")
    nf = _abrir(nf_pdf, "nf")
    etiqueta.insert_pdf(nf)
    return etiqueta.tobytes()


def juntar_varios(pdfs: list[bytes]) -> bytes:
    """Concatena N PDFs na ordem recebida (impressão em LOTE de etiquetas).

    Cada item já é o PDF final de um pedido (etiqueta, ou etiqueta+NF quando o
    fluxo é correios). Levanta `EtiquetaJuntarError` se a lista vier vazia ou
    se algum PDF for inválido.
    """
    if not pdfs:
        raise EtiquetaJuntarError("lote_vazio")
    saida = _abrir(pdfs[0], "etiqueta")
    for pdf in pdfs[1:]:
        saida.insert_pdf(_abrir(pdf, "etiqueta"))
    return saida.tobytes()
