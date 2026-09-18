"""Cartão do vídeo da expedição: a "foto" que vai no lugar do vídeo.

Vinicius 18/09 (289545, Shopee Vortan, Apple Watch, Golpe): a devolução tinha o
link do vídeo da expedição e nenhuma foto, e a contestação ficou parada em
"falta foto" — a Shopee exige imagem no módulo de evidência e a API de disputa
(Shopee, TikTok e ML) não tem campo de vídeo; o link só entra no texto. "Faz um
PDF escrito 'segue vídeo da expedição, clique para ver o vídeo' e manda o link
… coloca também uma foto da etiqueta e fala que não temos foto do pacote,
temos vídeo".

Este módulo desenha essa imagem (PNG, PyMuPDF + segno): título, identificação
do pedido, o aviso "não temos foto do pacote, temos o vídeo", a etiqueta que
foi colada no pacote (`nf_etiqueta_arquivo`, quando existe), o link do vídeo
por extenso e um QR code pequeno do mesmo link. PNG e não PDF porque Shopee e
TikTok só aceitam jpg/png como evidência; o ML aceita os dois.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pymupdf
import segno

# Nome do anexo gerado (o cartão é reconhecido pelo nome + `created_by` vazio).
CARTAO_VIDEO_NOME = "video-expedicao.png"

_SAO_PAULO = ZoneInfo("America/Sao_Paulo")
# Página em pontos; renderizada a 2x → 1200 × 1500 px.
_LARGURA, _ALTURA = 600.0, 750.0
_MARGEM = 40.0
_ESCALA = 2
# Cores (0–1): faixa escura, destaque, texto e cinza dos rótulos.
_ESCURO = (0.11, 0.16, 0.27)
_DESTAQUE = (0.93, 0.33, 0.14)
_TEXTO = (0.13, 0.13, 0.13)
_CINZA = (0.45, 0.45, 0.45)
_FUNDO_CAIXA = (0.96, 0.96, 0.97)
_FUNDO_AVISO = (1.0, 0.95, 0.88)
_BORDA = (0.85, 0.85, 0.88)
_BRANCO = (1, 1, 1)


def _latin1(texto: str) -> str:
    """As fontes base-14 do PyMuPDF escrevem em Latin-1: o que não cabe vira '?'.
    Troca os sinais mais comuns antes pra não sujar o cartão."""
    return (
        texto.replace("—", "-").replace("–", "-").replace("’", "'")
        .encode("latin-1", "replace").decode("latin-1")
    )


def _quebrar_monoespacado(texto: str, por_linha: int) -> list[str]:
    """Quebra uma string sem espaços (URL) em pedaços de `por_linha` caracteres."""
    return [texto[i : i + por_linha] for i in range(0, len(texto), por_linha)] or [""]


def _desenhar_qr(page: pymupdf.Page, link: str, *, x: float, y: float, lado: float) -> None:
    """QR code como retângulos vetoriais (nítido em qualquer escala)."""
    qr = segno.make(link, error="m")
    matriz = [list(linha) for linha in qr.matrix]
    n = len(matriz)
    quieto = 2  # módulos de margem em volta
    modulo = lado / (n + 2 * quieto)
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(x, y, x + lado, y + lado))
    shape.finish(color=None, fill=_BRANCO)
    for i, linha in enumerate(matriz):
        for j, escuro in enumerate(linha):
            if escuro:
                x0 = x + (j + quieto) * modulo
                y0 = y + (i + quieto) * modulo
                shape.draw_rect(pymupdf.Rect(x0, y0, x0 + modulo, y0 + modulo))
    shape.finish(color=None, fill=(0, 0, 0))
    shape.commit()


def _texto(
    page: pymupdf.Page,
    rect: pymupdf.Rect,
    texto: str,
    *,
    tamanho: float,
    fonte: str = "helv",
    cor: tuple[float, float, float] = _TEXTO,
    alinhar: int = pymupdf.TEXT_ALIGN_LEFT,
) -> None:
    # insert_textbox precisa de ~1,7 × tamanho de altura por linha (medido);
    # rótulo de uma linha ganha isso sozinho.
    rect = pymupdf.Rect(rect.x0, rect.y0, rect.x1, max(rect.y1, rect.y0 + 1.75 * tamanho))
    sobra = page.insert_textbox(
        rect, _latin1(texto), fontsize=tamanho, fontname=fonte, color=cor, align=alinhar
    )
    if sobra < 0:
        raise ValueError(f"texto não coube no cartão: {texto[:40]!r}")


def _caixa(
    page: pymupdf.Page,
    rect: pymupdf.Rect,
    *,
    fundo: tuple[float, float, float] | None,
    borda: tuple[float, float, float] | None = _BORDA,
) -> None:
    shape = page.new_shape()
    shape.draw_rect(rect)
    shape.finish(color=borda, fill=fundo, width=1)
    shape.commit()


def _recorte_conteudo(src_page: pymupdf.Page) -> pymupdf.Rect:
    """Área da etiqueta que tem conteúdo: a página costuma vir com um terço em
    branco embaixo. Varre uma renderização cinza de cima a baixo pela última
    linha com tinta (+ folga)."""
    rect = src_page.rect
    pix = src_page.get_pixmap(
        matrix=pymupdf.Matrix(0.5, 0.5), colorspace=pymupdf.csGRAY, alpha=False
    )
    ultima = 0
    for y in range(pix.height - 1, -1, -1):
        if any(pix.pixel(x, y)[0] < 235 for x in range(0, pix.width, 2)):
            ultima = y + 1
            break
    if ultima == 0:
        return rect
    fim = min(rect.y1, ultima / 0.5 + 8)
    return pymupdf.Rect(rect.x0, rect.y0, rect.x1, max(fim, rect.y0 + 40))


def _etiqueta(page: pymupdf.Page, rect: pymupdf.Rect, pdf: bytes) -> pymupdf.Rect | None:
    """Primeira página da etiqueta (só a parte com conteúdo) encaixada em `rect`
    pela largura, proporção mantida; devolve a área ocupada. PDF ilegível →
    None (o cartão sai sem a etiqueta)."""
    try:
        src = pymupdf.open(stream=pdf, filetype="pdf")
        if len(src) == 0:
            return None
        clip = _recorte_conteudo(src[0])
        altura = rect.width * clip.height / clip.width
        if altura > rect.height:  # etiqueta mais alta que o espaço: encaixa pela altura
            altura = rect.height
        destino = pymupdf.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + altura)
        page.show_pdf_page(destino, src, 0, clip=clip)
        return destino
    except (RuntimeError, ValueError):
        return None


def gerar_cartao_video(
    link: str,
    *,
    pedido: str | None,
    produto: str | None,
    sku: str | None,
    etiqueta_pdf: bytes | None = None,
    agora: datetime | None = None,
) -> bytes:
    """PNG do cartão: "Vídeo da expedição" + pedido/produto + aviso + etiqueta
    do pacote (se houver) + link + QR code."""
    link = (link or "").strip()
    agora = agora or datetime.now(_SAO_PAULO)
    doc = pymupdf.open()
    page = doc.new_page(width=_LARGURA, height=_ALTURA)
    shape = page.new_shape()
    shape.draw_rect(page.rect)
    shape.finish(color=None, fill=_BRANCO)
    # Faixa do título.
    faixa_h = 110.0
    shape.draw_rect(pymupdf.Rect(0, 0, _LARGURA, faixa_h))
    shape.finish(color=None, fill=_ESCURO)
    # Ícone "play": círculo laranja + triângulo branco.
    cx, cy, r = _MARGEM + 30, faixa_h / 2, 27.0
    shape.draw_circle(pymupdf.Point(cx, cy), r)
    shape.finish(color=None, fill=_DESTAQUE)
    shape.draw_polyline(
        [
            pymupdf.Point(cx - 8, cy - 13),
            pymupdf.Point(cx + 14, cy),
            pymupdf.Point(cx - 8, cy + 13),
            pymupdf.Point(cx - 8, cy - 13),
        ]
    )
    shape.finish(color=None, fill=_BRANCO, closePath=True)
    shape.commit()
    x_txt = cx + r + 20
    _texto(
        page, pymupdf.Rect(x_txt, 22, _LARGURA - _MARGEM, 70),
        "VÍDEO DA EXPEDIÇÃO", tamanho=25, fonte="hebo", cor=_BRANCO,
    )
    _texto(
        page, pymupdf.Rect(x_txt, 64, _LARGURA - _MARGEM, 98),
        "Comprovante de envio do pedido, gravado no momento da expedição",
        tamanho=12, cor=(0.85, 0.87, 0.92),
    )

    # Identificação do pedido.
    y = faixa_h + 22
    for rotulo, valor in (("Pedido", pedido), ("Produto", produto), ("SKU", sku)):
        valor = (valor or "").strip()
        if not valor:
            continue
        _texto(page, pymupdf.Rect(_MARGEM, y, _MARGEM + 80, y + 20), rotulo, tamanho=11, cor=_CINZA)
        _texto(
            page, pymupdf.Rect(_MARGEM + 80, y - 2, _LARGURA - _MARGEM, y + 22),
            valor, tamanho=14, fonte="hebo",
        )
        y += 24

    # Aviso: não temos foto do pacote, temos o vídeo.
    y += 10
    aviso = pymupdf.Rect(_MARGEM, y, _LARGURA - _MARGEM, y + 74)
    _caixa(page, aviso, fundo=_FUNDO_AVISO, borda=_DESTAQUE)
    _texto(
        page, pymupdf.Rect(aviso.x0 + 12, aviso.y0 + 9, aviso.x1 - 12, aviso.y0 + 30),
        "NÃO TEMOS FOTO DO PACOTE: TEMOS O VÍDEO DA EXPEDIÇÃO.",
        tamanho=12, fonte="hebo", cor=_DESTAQUE,
    )
    _texto(
        page, pymupdf.Rect(aviso.x0 + 12, aviso.y0 + 30, aviso.x1 - 12, aviso.y1 - 4),
        "O vídeo, gravado no momento do envio, mostra o produto sendo conferido e "
        "embalado. Acesse o link abaixo (o mesmo link está no texto desta contestação) "
        "ou escaneie o QR code para assistir.",
        tamanho=11,
    )
    y = aviso.y1 + 18

    # Etiqueta (coluna da esquerda) + vídeo (coluna da direita). Sem etiqueta,
    # o bloco do vídeo ocupa a largura toda.
    rodape_y = _ALTURA - 44
    com_etiqueta = False
    fim_conteudo = y
    if etiqueta_pdf:
        larg_et = 236.0
        ocupado = _etiqueta(
            page, pymupdf.Rect(_MARGEM, y + 18, _MARGEM + larg_et, rodape_y - 8), etiqueta_pdf
        )
        com_etiqueta = ocupado is not None
        if ocupado is not None:
            _texto(
                page, pymupdf.Rect(_MARGEM, y, _MARGEM + larg_et, y + 16),
                "ETIQUETA COLADA NO PACOTE", tamanho=10, fonte="hebo", cor=_CINZA,
            )
            _caixa(page, ocupado, fundo=None)
            fim_conteudo = ocupado.y1
    if com_etiqueta:
        x0 = _MARGEM + 236.0 + 22
    else:
        x0 = _MARGEM
    x1 = _LARGURA - _MARGEM
    _texto(
        page, pymupdf.Rect(x0, y, x1, y + 16),
        "VÍDEO DA EXPEDIÇÃO", tamanho=10, fonte="hebo", cor=_CINZA,
    )
    y += 18

    # Link por extenso numa caixa (Courier: 0,6 × tamanho por caractere).
    tamanho_link = 10.0
    por_linha = max(8, int((x1 - x0 - 20) / (tamanho_link * 0.6)))
    pedacos = _quebrar_monoespacado(link, por_linha)
    alt_caixa = 20 + len(pedacos) * (tamanho_link + 4)
    caixa = pymupdf.Rect(x0, y, x1, y + alt_caixa)
    _caixa(page, caixa, fundo=_FUNDO_CAIXA)
    _texto(
        page, pymupdf.Rect(caixa.x0 + 10, caixa.y0 + 8, caixa.x1 - 10, caixa.y1),
        "\n".join(pedacos), tamanho=tamanho_link, fonte="cour",
    )
    # O PDF intermediário ganha o link clicável (não sobrevive ao PNG, mas não custa).
    page.insert_link({"kind": pymupdf.LINK_URI, "from": caixa, "uri": link})
    y = caixa.y1 + 14

    # QR code pequeno, centralizado na coluna.
    lado = 118.0
    _desenhar_qr(page, link, x=(x0 + x1 - lado) / 2, y=y, lado=lado)
    y += lado + 6
    _texto(
        page, pymupdf.Rect(x0, y, x1, y + 16),
        "Escaneie com a câmera do celular", tamanho=9.5, cor=_CINZA,
        alinhar=pymupdf.TEXT_ALIGN_CENTER,
    )
    fim_conteudo = max(fim_conteudo, y + 16)

    # Rodapé logo abaixo do conteúdo; a imagem é cortada ali (sem sobra branca).
    rodape_y = min(rodape_y, fim_conteudo + 22)
    _texto(
        page, pymupdf.Rect(_MARGEM, rodape_y, _LARGURA - _MARGEM, rodape_y + 20),
        f"Imagem gerada automaticamente em {agora.strftime('%d/%m/%Y %H:%M')}",
        tamanho=9, cor=_CINZA, alinhar=pymupdf.TEXT_ALIGN_CENTER,
    )

    recorte = pymupdf.Rect(0, 0, _LARGURA, min(_ALTURA, rodape_y + 34))
    pix = page.get_pixmap(matrix=pymupdf.Matrix(_ESCALA, _ESCALA), clip=recorte, alpha=False)
    png = pix.tobytes("png")
    doc.close()
    return png
