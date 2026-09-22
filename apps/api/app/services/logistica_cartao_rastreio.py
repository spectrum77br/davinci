"""Cartão de rastreio: PNG com o histórico dos Correios, anexado à mensagem
ao comprador da Amazon.

Vinicius, 22/09/2026: a Amazon não deixa link na mensagem ao comprador, então
o cliente não tem como clicar e ver onde está o pacote — o texto só diz o
código. O cartão resolve isso: a mesma lista de passagens que ele veria no
site dos Correios, desenhada numa imagem, indo anexada.

Desenho no formato da página de rastreamento dos Correios (trilha amarela,
título azul, data embaixo), com o logo dos Correios no canto superior
direito — Vinicius, 22/09/2026, olhando o cartão pronto: é o que faz o
comprador reconhecer de quem é a informação antes de ler a primeira linha. O
PNG vem do banco (`imagem_publica`, migração 0305), lido por
`services/imagem_publica.py`; o cartão só desenha o que recebe em `logo`.

Ressalva registrada junto com a decisão: a peça nasce aqui e às vezes termina
em contestação de marketplace, onde uma imagem com o logo da transportadora
pode ser lida como documento dela. O que está escrito é verdade — é o
histórico que o 17track leu, e o rodapé diz que as movimentações são dos
Correios e quando foram consultadas.

Vinicius, 22/09: o cartão vai em TODOS os eventos, mesmo quando o 17track só
tem "Etiqueta emitida" (pré-postagem) — mostrar que a etiqueta saiu já é
informação pro comprador. Sem evento nenhum não há o que desenhar, e aí a
mensagem vai sem anexo.

Limite conhecido: o 17track dá a unidade só como UF ("MG", "SP"). O
"de Unidade de Tratamento, Belo Horizonte - MG" que aparece no site dos
Correios exige a API deles (contrato), que a gente não tem. Quando tiver,
é só preencher `de`/`para` no Evento — o desenho já aceita.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

import pymupdf

SAO_PAULO = ZoneInfo("America/Sao_Paulo")

CARTAO_NOME = "rastreio.png"
CARTAO_MIME = "image/png"

_LARGURA = 620.0
_MARGEM = 34.0
_ESCALA = 2

_AZUL = (0.0, 0.31, 0.53)
_AZUL_CLARO = (0.13, 0.47, 0.71)
_AMARELO = (1.0, 0.80, 0.0)
_CINZA = (0.40, 0.40, 0.40)
_CINZA_ICONE = (0.87, 0.89, 0.91)
_BORDA = (0.85, 0.85, 0.88)
_BRANCO = (1, 1, 1)
_VERDE = (0.09, 0.55, 0.33)

# Espaço do logo: da faixa amarela até um pouco antes da linha do cabeçalho
# (y=82), à direita do código do objeto.
_LOGO_CAIXA = pymupdf.Rect(_LARGURA - _MARGEM - 170, 16, _LARGURA - _MARGEM, 78)
# A partir deste valor o pixel do logo conta como fundo e some (ver _logo_pronto).
_FUNDO_CLARO = 240


@dataclass(frozen=True)
class Evento:
    quando: datetime
    titulo: str
    de: str = ""  # só com a API dos Correios; o 17track não dá
    para: str = ""
    local: str = ""
    entregue: bool = False


def _latin1(texto: str) -> str:
    """As fontes base-14 do PyMuPDF escrevem em Latin-1: o que não cabe vira
    '?'. Troca os sinais mais comuns antes pra não sujar o cartão."""
    return (
        texto.replace("—", "-").replace("–", "-").replace("’", "'")
        .encode("latin-1", "replace").decode("latin-1")
    )


def _linhas(texto: str, largura: float, tamanho: float, fonte: str) -> int:
    f = pymupdf.Font(fontname=fonte)
    linhas, atual = 1, ""
    for p in _latin1(texto).split():
        teste = f"{atual} {p}".strip()
        if f.text_length(teste, tamanho) > largura and atual:
            linhas, atual = linhas + 1, p
        else:
            atual = teste
    return linhas


def _texto(
    page: pymupdf.Page,
    x: float,
    y: float,
    texto: str,
    *,
    tamanho: float,
    fonte: str = "helv",
    cor: tuple[float, float, float] = (0.20, 0.20, 0.20),
    largura: float | None = None,
    alinhar: int = pymupdf.TEXT_ALIGN_LEFT,
) -> float:
    """Escreve a partir de (x, y); devolve o y depois da última linha."""
    largura = largura or (_LARGURA - _MARGEM - x)
    # Folga generosa na altura: quem controla o fluxo é o y devolvido.
    rect = pymupdf.Rect(x, y, x + largura, y + 2.2 * tamanho + 200)
    if page.insert_textbox(
        rect, _latin1(texto), fontsize=tamanho, fontname=fonte, color=cor, align=alinhar
    ) < 0:
        raise ValueError(f"texto não coube no cartão: {texto[:40]!r}")
    return y + _linhas(texto, largura, tamanho, fonte) * 1.32 * tamanho


def _detalhe(ev: Evento) -> list[str]:
    if ev.de or ev.para:
        linhas = (f"de {ev.de}" if ev.de else "", f"para {ev.para}" if ev.para else "")
        return [t for t in linhas if t]
    return [ev.local] if ev.local else []


def _altura_evento(ev: Evento, largura: float) -> float:
    h = _linhas(ev.titulo, largura, 11.5, "hebo") * 1.32 * 11.5
    for linha in _detalhe(ev):
        h += _linhas(linha, largura, 9.5, "helv") * 1.32 * 9.5
    return h + 1.32 * 9.5 + 20  # + data/hora + respiro


def _icone(page: pymupdf.Page, cx: float, cy: float, *, entregue: bool) -> None:
    """Círculo cinza com um pacote dentro; visto verde quando é a entrega."""
    shape = page.new_shape()
    shape.draw_circle(pymupdf.Point(cx, cy), 15)
    shape.finish(color=None, fill=_CINZA_ICONE)
    shape.commit()
    shape = page.new_shape()
    if entregue:
        shape.draw_polyline([
            pymupdf.Point(cx - 6, cy + 0.5),
            pymupdf.Point(cx - 1.5, cy + 5),
            pymupdf.Point(cx + 6.5, cy - 4.5),
        ])
        shape.finish(color=_VERDE, width=2.4)
    else:
        shape.draw_rect(pymupdf.Rect(cx - 6.5, cy - 5, cx + 6.5, cy + 5.5))
        shape.finish(color=_AZUL, width=1.2)
        shape.draw_line(pymupdf.Point(cx - 6.5, cy - 1), pymupdf.Point(cx + 6.5, cy - 1))
        shape.draw_line(pymupdf.Point(cx, cy - 5), pymupdf.Point(cx, cy - 1))
        shape.finish(color=_AZUL, width=1.0)
    shape.commit()


@lru_cache(maxsize=4)
def _logo_pronto(logo: bytes) -> tuple[bytes, float, float]:
    """PNG do logo pronto pra desenhar, mais a largura e a altura dele.

    Tira o fundo chapado claro: o PNG dos Correios que está no banco veio de
    um recorte de tela, com fundo #F7F7F7 OPACO — desenhado no cartão branco,
    viraria um quadradinho cinza em volta do logo. Aqui pixel quase branco
    fica transparente; num logo que tenha letra branca o resultado é o mesmo
    de antes (branco em cima de branco).

    Guardado em cache porque uma rodada do robô desenha dezenas de cartões com
    o mesmo logo — a varredura dos pixels roda uma vez. Imagem ilegível estoura
    aqui, e o `gerar` segue sem ela.
    """
    pix = pymupdf.Pixmap(logo)
    if pix.colorspace is None or pix.colorspace.n != 3:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)  # cinza/CMYK → RGB
    if not pix.alpha:
        pix = pymupdf.Pixmap(pix, 1)
    if not pix.width or not pix.height:
        raise ValueError("logo sem dimensão")
    amostras = bytearray(pix.samples)
    for i in range(0, len(amostras), pix.n):
        if all(amostras[i + c] >= _FUNDO_CLARO for c in (0, 1, 2)):
            amostras[i + 3] = 0
    limpo = pymupdf.Pixmap(pix.colorspace, pix.width, pix.height, bytes(amostras), True)
    return limpo.tobytes("png"), float(pix.width), float(pix.height)


def _caixa_logo(larg: float, alt: float) -> pymupdf.Rect:
    """Onde o logo entra: dentro de `_LOGO_CAIXA`, sem distorcer e encostado na
    margem direita.

    O `keep_proportion` do PyMuPDF centraliza a imagem no retângulo que recebe
    — um logo mais alto que largo (o dos Correios é 205x161) ficaria boiando no
    meio da caixa, longe da margem, parecendo desalinhado com o resto. Então a
    caixa sai do tamanho exato do logo.
    """
    escala = min(_LOGO_CAIXA.width / larg, _LOGO_CAIXA.height / alt)
    larg, alt = larg * escala, alt * escala
    topo = _LOGO_CAIXA.y0 + (_LOGO_CAIXA.height - alt) / 2
    return pymupdf.Rect(_LOGO_CAIXA.x1 - larg, topo, _LOGO_CAIXA.x1, topo + alt)


def codigo_formatado(codigo: str) -> str:
    c = (codigo or "").strip().upper()
    return f"{c[:2]} {c[2:5]} {c[5:8]} {c[8:11]} {c[11:]}" if len(c) == 13 else c


def gerar(
    *,
    codigo: str,
    pedido: str,
    servico: str,
    previsao: date | None,
    eventos: list[Evento],
    consultado_em: datetime,
    logo: bytes | None = None,
) -> bytes:
    """PNG do cartão. `eventos` do mais novo pro mais antigo; `logo` é o PNG do
    canto superior direito (hoje o dos Correios) — sem ele o cartão sai igual,
    só sem a figura."""
    if not eventos:
        raise ValueError("cartão de rastreio sem eventos")
    x_icone = _MARGEM + 22
    x_texto = x_icone + 38
    larg_texto = _LARGURA - _MARGEM - x_texto

    topo_h = 132.0
    alturas = [_altura_evento(e, larg_texto) for e in eventos]
    altura = topo_h + sum(alturas) + 58

    doc = pymupdf.open()
    page = doc.new_page(width=_LARGURA, height=altura)
    shape = page.new_shape()
    shape.draw_rect(page.rect)
    shape.finish(color=None, fill=_BRANCO)
    shape.draw_rect(pymupdf.Rect(0, 0, _LARGURA, 5))
    shape.finish(color=None, fill=_AMARELO)
    shape.commit()

    _texto(page, _MARGEM, 26, codigo_formatado(codigo), tamanho=21, fonte="hebo", cor=_AZUL)
    _texto(page, _MARGEM, 62, f"Pedido {pedido}", tamanho=10, cor=_CINZA)

    # Logo (o dos Correios), canto superior direito.
    if logo:
        try:
            png_logo, larg_logo, alt_logo = _logo_pronto(logo)
            page.insert_image(
                _caixa_logo(larg_logo, alt_logo), stream=png_logo, keep_proportion=True
            )
        # FzErrorBase entra na lista porque o erro que o PyMuPDF 1.27 levanta
        # em imagem corrompida NÃO herda de RuntimeError (o `except` antigo
        # deixaria passar e derrubaria o cartão inteiro).
        except (RuntimeError, ValueError, pymupdf.mupdf.FzErrorBase):
            pass  # logo ilegível não derruba o cartão

    y = 90.0
    shape = page.new_shape()
    shape.draw_line(pymupdf.Point(_MARGEM, y - 8), pymupdf.Point(_LARGURA - _MARGEM, y - 8))
    shape.finish(color=_BORDA, width=1)
    shape.commit()
    cabecalho = f"Transportadora: Correios{f' - {servico}' if servico else ''}"
    _texto(page, _MARGEM, y, cabecalho, tamanho=11, fonte="hebo", cor=_AZUL)
    if previsao:
        _texto(
            page, _MARGEM, y + 15,
            f"Previsão de Entrega: {previsao.strftime('%d/%m/%Y')}",
            tamanho=11, fonte="hebo", cor=_AZUL,
        )

    y = topo_h
    centros, yy = [], y
    for h in alturas:
        centros.append(yy + 13)
        yy += h
    if len(centros) > 1:
        shape = page.new_shape()
        shape.draw_line(pymupdf.Point(x_icone, centros[0]), pymupdf.Point(x_icone, centros[-1]))
        shape.finish(color=_AMARELO, width=3)
        shape.commit()

    for ev, h, cy in zip(eventos, alturas, centros, strict=True):
        _icone(page, x_icone, cy, entregue=ev.entregue)
        cursor = _texto(
            page, x_texto, y + 4, ev.titulo, tamanho=11.5, fonte="hebo",
            cor=_VERDE if ev.entregue else _AZUL_CLARO, largura=larg_texto,
        )
        for linha in _detalhe(ev):
            cursor = _texto(page, x_texto, cursor, linha, tamanho=9.5, cor=_CINZA,
                            largura=larg_texto)
        _texto(page, x_texto, cursor, ev.quando.astimezone(SAO_PAULO).strftime("%d/%m/%Y %H:%M"),
               tamanho=9.5, cor=_CINZA, largura=larg_texto)
        y += h

    shape = page.new_shape()
    shape.draw_line(
        pymupdf.Point(_MARGEM, altura - 44), pymupdf.Point(_LARGURA - _MARGEM, altura - 44)
    )
    shape.finish(color=_BORDA, width=1)
    shape.commit()
    _texto(
        page, _MARGEM, altura - 34,
        "Movimentações registradas pelos Correios, consultadas em "
        + consultado_em.astimezone(SAO_PAULO).strftime("%d/%m/%Y às %H:%M")
        + ". Dúvidas sobre a entrega: responda esta mensagem.",
        tamanho=8.5, cor=_CINZA,
    )
    return page.get_pixmap(matrix=pymupdf.Matrix(_ESCALA, _ESCALA)).tobytes("png")
