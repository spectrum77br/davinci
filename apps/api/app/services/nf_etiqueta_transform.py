"""Transforma a etiqueta pra a "visualização" da impressão (item 2 da Fase 3b).

Regra do faturador (áudios 25/07): antes de imprimir a etiqueta tipo Correios/
Agência, ela passa por uma limpeza pra NÃO expor a origem real do envio:

1. REMETENTE = DESTINATÁRIO — o nome do remetente (a loja/marketplace, ex.
   "Ribeiro Malas" / "Shopee Minas") é trocado pelo nome do destinatário.
2. Sem NF — apaga o bloco DANFE SIMPLIFICADO (número, série, emissão, a chave
   de acesso em texto E o código de barras da chave).
3. Sem marketplace — remove o logo do marketplace (imagem pequena no topo).
4. Sem picking — apaga o rodapé "SKU: N; Total Items: N; #PEDIDO; Deadline:..."
   e a linha de itens abaixo (a Declaração de Conteúdo já traz SKU e descrição).

O que é PRESERVADO: QR codes, código de barras de rastreio dos Correios, blocos
de roteirização (agência/rota) e a Declaração de Conteúdo. O nome do destinatário
é lido da própria etiqueta (bloco DESTINATÁRIO) quando não vem explícito, então o
transform funciona só com os bytes do PDF.

Camada PURA (sem rede, sem banco): `transformar_etiqueta(pdf_bytes, nome?) -> bytes`.
O landing zone (nf_etiqueta_arquivo) e o endpoint de impressão são de outra camada.
"""

from __future__ import annotations

import fitz  # PyMuPDF

_MIN_CHAVE_DIGITOS = 40  # a chave da NF-e tem 44 dígitos
_MAX_BLOCO_NF = 0.25     # bloco NF nunca passa de 1/4 da altura da página
_GAP_IMAGEM = 6.0        # imagem só estende o bloco se estiver colada nele
_MIN_TOPO_PICKING = 0.55  # rodapé de picking só existe na parte de baixo


class EtiquetaTransformError(RuntimeError):
    """Falha ao abrir/transformar o PDF da etiqueta."""


def _nome_destinatario(page: fitz.Page) -> str | None:
    """Lê o nome logo abaixo do cabeçalho DESTINATÁRIO da etiqueta."""
    hits = page.search_for("DESTINATÁRIO")
    if not hits:
        return None
    hdr = hits[0]
    linha = [
        w for w in page.get_text("words")
        if hdr.y1 <= w[1] <= hdr.y1 + 14 and (hdr.x0 - 20) <= w[0] <= (hdr.x0 + 130)
    ]
    linha.sort(key=lambda w: w[0])
    nome = " ".join(w[4] for w in linha).strip()
    return nome or None


def _redigir(page: fitz.Page, rect: fitz.Rect, pad: float = 1.0) -> None:
    r = fitz.Rect(rect.x0 - pad, rect.y0 - pad, rect.x1 + pad, rect.y1 + pad)
    page.add_redact_annot(r, fill=(1, 1, 1))


def _plano_remetente(page: fitz.Page) -> tuple[fitz.Rect, float, float, float] | None:
    """Localiza o NOME do remetente e devolve (caixa_p/redigir, x, baseline, fonte)."""
    rem = page.search_for("REMETENTE")
    if not rem:
        return None
    rr = rem[0]
    words = page.get_text("words")
    nomes = [w for w in words if w[4].upper().rstrip(":").startswith("NOME")]
    if nomes:
        # DECLARAÇÃO: valor após o "NOME:" do lado do REMETENTE (metade esquerda)
        dest = page.search_for("DESTINATÁRIO")
        corte = (rr.x1 + dest[0].x0) / 2 if dest else page.rect.width / 2
        candidatos = [w for w in nomes if w[0] < corte]
        if not candidatos:
            return None
        nome_lbl = min(candidatos, key=lambda w: abs(w[1] - rr.y1))
        alvo = [
            w for w in words
            if abs(w[1] - nome_lbl[1]) < 3 and nome_lbl[2] < w[0] < corte
        ]
    else:
        # ETIQUETA: nome na linha logo abaixo do rótulo REMETENTE, mesma coluna
        alvo = [
            w for w in words
            if rr.y1 <= w[1] <= rr.y1 + 12 and (rr.x0 - 8) <= w[0] <= (rr.x0 + 130)
        ]
    if not alvo:
        return None
    alvo.sort(key=lambda w: w[0])
    x0 = min(w[0] for w in alvo)
    y0 = min(w[1] for w in alvo)
    y1 = max(w[3] for w in alvo)
    x1 = max(w[2] for w in alvo)
    fs = (y1 - y0) * 0.92
    # caixa apertada: encolhe o fundo pra não invadir a linha seguinte (ex. CPF/CNPJ:)
    caixa = fitz.Rect(x0 - 0.5, y0 + 0.3, x1 + 0.5, y1 - 0.8)
    return (caixa, x0, y1, fs)


def _redigir_numero_nf(page: fitz.Page) -> None:
    """Apaga só o rótulo "NF:" e o número ao lado, sem tocar no resto da linha."""
    words = page.get_text("words")
    for hit in page.search_for("NF:"):
        alvo = fitz.Rect(hit)
        for w in words:
            mesma_linha = abs(w[1] - hit.y0) < 3
            a_direita = hit.x1 - 1 <= w[0] <= hit.x1 + 60
            if mesma_linha and a_direita and w[4].strip(".-/").isdigit():
                alvo |= fitz.Rect(w[0], w[1], w[2], w[3])
        _redigir(page, alvo)


def _remover_nf(page: fitz.Page) -> None:
    """Redige o bloco DANFE/NF: número, série, emissão, chave texto + barras."""
    chaves = [
        w for w in page.get_text("words")
        if len(w[4]) >= _MIN_CHAVE_DIGITOS and w[4].isdigit()
    ]
    # o "NF:" só vale como âncora de bloco quando a chave de acesso está junto.
    # Nas etiquetas do Mercado Livre ele aparece solto no cabeçalho dos Correios
    # (Contrato/Sedex/PESO/rastreio) — ali a faixa comeria o código de rastreio.
    anchors = page.search_for("DANFE") or (page.search_for("NF:") if chaves else [])
    if not anchors:
        # etiqueta sem bloco DANFE (ex. J&T, Mercado Livre): apaga a chave solta
        # e o número da NF, preservando o cabeçalho postal em volta.
        for w in chaves:
            _redigir(page, fitz.Rect(w[0], w[1], w[2], w[3]))
        _redigir_numero_nf(page)
        return
    top = min(a.y0 for a in anchors) - 2
    teto = top + page.rect.height * _MAX_BLOCO_NF
    bottom = top + 24
    for w in chaves:
        if w[1] <= teto:
            bottom = max(bottom, w[3])
    # só a imagem COLADA no bloco (o código de barras da chave) o estende. Uma
    # imagem solta mais abaixo não pode arrastar a faixa branca até o fim da
    # página — foi o que apagava o destinatário nas etiquetas do Mercado Livre.
    rects = sorted(
        (r for img in page.get_images(full=True) for r in page.get_image_rects(img[0])),
        key=lambda r: r.y0,
    )
    for r in rects:
        if top - 4 <= r.y0 <= bottom + _GAP_IMAGEM:
            bottom = max(bottom, r.y1)
    bottom = min(bottom + 2, teto, page.rect.height)
    _redigir(page, fitz.Rect(0, top, page.rect.width, bottom), pad=0)


def _remover_logo_marketplace(page: fitz.Page) -> None:
    """Remove logo de marketplace: imagem pequena, não-quadrada (não QR) e não
    código de barras, posicionada no topo da etiqueta."""
    for img in page.get_images(full=True):
        for r in page.get_image_rects(img[0]):
            if r.height <= 0:
                continue
            asp = r.width / r.height
            quadrada = 0.7 <= asp <= 1.4      # QR code
            barra = asp >= 5                  # código de barras
            no_topo = r.y0 < 32
            pequena = (r.width * r.height) < 2500
            if no_topo and pequena and not quadrada and not barra:
                _redigir(page, r, pad=0.5)


def _remover_picking(page: fitz.Page) -> None:
    """Redige o rodapé de picking da etiqueta (SKU/Total Items/pedido/Deadline
    + a linha de itens abaixo). A âncora é "Total Items", que só existe nesse
    rodapé — a Declaração de Conteúdo tem a coluna "SKU", mas não esse rótulo."""
    anchors = page.search_for("Total Items")
    if not anchors:
        return
    top = min(a.y0 for a in anchors) - 2
    if top < page.rect.height * _MIN_TOPO_PICKING:
        return  # âncora fora do rodapé: apagar daqui até o fim comeria a etiqueta
    _redigir(page, fitz.Rect(0, top, page.rect.width, page.rect.height), pad=0)


def transformar_etiqueta(pdf_bytes: bytes, destinatario_nome: str | None = None) -> bytes:
    """Aplica as 4 regras de visualização e devolve os bytes do PDF transformado.

    `destinatario_nome` opcional sobrepõe o nome lido da etiqueta (o caller que
    tem o pedido pode passar o nome oficial). Sem ele, lê do bloco DESTINATÁRIO.
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise EtiquetaTransformError(f"pdf_invalido: {exc}") from exc
    if doc.page_count == 0:
        raise EtiquetaTransformError("pdf_vazio")

    nome = (destinatario_nome or _nome_destinatario(doc[0]) or "").strip()
    inserts: list[tuple[fitz.Page, float, float, float]] = []
    for page in doc:
        _remover_logo_marketplace(page)          # 3) logo (imagem)
        _remover_nf(page)                        # 2) bloco NF
        _remover_picking(page)                   # 4) rodapé de picking
        if nome:                                 # 1) remetente = destinatário
            plano = _plano_remetente(page)
            if plano:
                caixa, x0, y1, fs = plano
                _redigir(page, caixa, pad=0)
                inserts.append((page, x0, y1, fs))
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_REMOVE)
    for page, x0, y1, fs in inserts:
        page.insert_text(
            (x0, y1 - fs * 0.15), nome, fontsize=fs, fontname="helv", color=(0, 0, 0)
        )
    return doc.tobytes()
