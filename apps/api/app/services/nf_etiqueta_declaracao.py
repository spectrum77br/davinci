"""Declaração de conteúdo com os itens do pedido no Bling (06/11/2026).

A etiqueta que sobe do Upseller traz, na DECLARAÇÃO DE CONTEÚDO, o título e a
variação do ANÚNCIO do marketplace — não o item que vai na caixa. Quando o robô
de prioridade (ou alguém no painel do Bling) troca o SKU, desmonta um kit ou
troca o produto, o papel colado no pacote continua com o anúncio. Regra do
Eduardo: a tabela "IDENTIFICAÇÃO DOS BENS" mostra SEMPRE os itens ATUAIS do
pedido no espelho do Bling (`bling_orders`), uma linha por item, na ordem do
Bling, e o Total é a soma das quantidades.

Camada PURA (sem banco, sem rede). Quem chama busca os itens
(`nf_etiqueta_itens.itens_dos_pedidos`) e passa pra cá:

- na SUBIDA da etiqueta (`/agent/etiqueta` e `/agent/etiqueta-lote`), logo
  depois de `transformar_etiqueta` — o blob guardado já sai certo;
- na IMPRESSÃO (Controle de Estoque), sobre o blob guardado, antes do
  redimensionamento — cobre troca feita no Bling depois da subida. O blob
  corrigido NÃO é gravado de volta.

Só a faixa das linhas de item (e a quantidade do Total) é refeita; o resto da
página (remetente, destinatário, texto legal, assinatura) fica como está — e
desce junto quando as linhas novas não cabem na altura antiga. Dois layouts,
medidos nas etiquetas reais:

- Upseller (Shopee/TikTok/ML/Amazon/Temu via Upseller): página 297×421, grade
  de segmentos de linha (lw 1), linhas em 9 pt, Total com fundo cinza e, em
  ML/Shopee/Amazon, a caixa "DECLARAÇÃO" + assinatura abaixo;
- a nossa (`~/nf-executor/temu_declaracao.py` / `declaracao_pedido.py`):
  Helvetica 6,5 pt, uma célula `re` (lw 0,5) por campo.

IDEMPOTENTE: depois de reescrever, grava nos metadados do PDF (keywords) a
marca `davinci-decl:<assinatura dos itens>`. Rodar de novo com os mesmos itens
devolve `igual` sem abrir a tabela (caminho rápido da impressão). Blob sem a
marca (subido antes desta regra, ou corrigido à mão) é reescrito.

Nunca levanta exceção: qualquer falha devolve os bytes de ENTRADA com status
`erro` — imprimir a declaração antiga é melhor do que travar o despacho.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

import fitz  # PyMuPDF

ANCORA = "IDENTIFICAÇÃO DOS BENS"
_CAB = ("Nº", "SKU", "DESCRIÇÃO", "VARIAÇÃO", "QTD")
_MARCA = "davinci-decl:"
# Entra na assinatura: mudar o desenho da tabela invalida as marcas antigas
# (a impressão reescreve de novo com a regra nova).
_VERSAO = "1"
# Folga mínima entre o fim do conteúdo e a borda de baixo da página.
_MARGEM_PAGINA = 4.0


@dataclass(frozen=True)
class ItemDeclaracao:
    """Uma linha da tabela, já no formato do papel (ver `formatar_item`)."""

    sku: str
    descricao: str
    variacao: str = ""
    quantidade: int = 1


# ---------------------------------------------------------------------------
# Texto (regras aprovadas pelo Eduardo)
# ---------------------------------------------------------------------------

# Base14 (helv/hebo) do insert_text só escreve Latin-1: o resto vira '·'.
_SUBSTITUI = {
    "‐": "-",
    "‑": "-",
    "‒": "-",
    "–": "-",
    "—": "-",
    "―": "-",
    "−": "-",
    "‘": "'",
    "’": "'",
    "‚": "'",
    "‛": "'",
    "“": '"',
    "”": '"',
    "„": '"',
    "‟": '"',
    "…": "...",
    "•": "-",
    " ": " ",
    "（": "(",
    "）": ")",
}
_PARENTESES = re.compile(r"\([^()]*\)")
_INTERNAS = re.compile(r"(?<!\w)(?:avulso|salvado|usado)(?!\w)", re.IGNORECASE)
_SEPARADOR_REPETIDO = re.compile(r"(?: -)+ ")
_TRACO_NA_BORDA = re.compile(r"^(?:\s*-)+\s*|(?:\s*-)+\s*$")


def _texto_imprimivel(texto: str) -> str:
    """Troca/tira o que a Helvetica Base14 não escreve (fora do Latin-1)."""
    out: list[str] = []
    for ch in texto:
        sub = _SUBSTITUI.get(ch)
        if sub is not None:
            out.append(sub)
            continue
        o = ord(ch)
        if o < 32 or 127 <= o < 160:
            out.append(" ")
        elif o < 256:
            out.append(ch)
        else:  # 'ő' → 'o', 'ﬁ' → 'fi'; o que não decompõe (emoji, 'ł') sai
            out.append(
                "".join(
                    c
                    for c in unicodedata.normalize("NFKD", ch)
                    if 32 <= ord(c) < 127 or 160 <= ord(c) < 256
                )
            )
    return "".join(out)


def _sem_traco_na_borda(s: str) -> str:
    return _TRACO_NA_BORDA.sub("", s).strip()


def separar_descricao(item_descricao: str | None) -> tuple[str, str]:
    """`item_descricao` do Bling → (DESCRIÇÃO, VARIAÇÃO) do papel.

    Tira todo grupo "( ... )" (código do fornecedor, ex. "(DT - PPDT802 -
    DT01)") e as palavras internas AVULSO/SALVADO/USADO; corta no ÚLTIMO
    " - ": antes é a descrição, depois a variação ("Preto", "Laranja + Fone").
    Sem " - ", variação vazia."""
    s = _texto_imprimivel(item_descricao or "")
    antes = None
    while antes != s:  # parênteses aninhados saem de dentro pra fora
        antes, s = s, _PARENTESES.sub(" ", s)
    s = _INTERNAS.sub(" ", s)
    s = _SEPARADOR_REPETIDO.sub(" - ", " ".join(s.split()))
    s = _sem_traco_na_borda(s)
    if not s:  # a descrição era só o que a regra tira: mostra como veio
        return " ".join(_texto_imprimivel(item_descricao or "").split()), ""
    if " - " not in s:
        return s, ""
    desc, var = s.rsplit(" - ", 1)
    desc, var = _sem_traco_na_borda(desc), _sem_traco_na_borda(var)
    return (desc, var) if desc else (var, "")


def _quantidade(valor: object) -> int:
    try:
        n = int(round(float(valor)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1
    return n if n > 0 else 1


def formatar_item(
    item_codigo: str | None, item_descricao: str | None, item_quantidade: object
) -> ItemDeclaracao:
    """Linha do `bling_orders` → linha da declaração. SKU como está (kit
    'dg053.sp+a001.sp' é uma linha só); QTD inteira, 1 quando vazia."""
    desc, var = separar_descricao(item_descricao)
    return ItemDeclaracao(
        sku=" ".join(_texto_imprimivel(str(item_codigo or "")).split()),
        descricao=desc,
        variacao=var,
        quantidade=_quantidade(item_quantidade),
    )


def assinatura_itens(itens: Sequence[ItemDeclaracao]) -> str:
    """Hash curto e estável dos itens. Não depende da ordem (o mesmo conjunto
    em outra ordem não é troca — o Bling e o marketplace às vezes invertem)."""
    chave = sorted((i.sku, i.descricao, i.variacao, int(i.quantidade)) for i in itens)
    bruto = json.dumps([_VERSAO, chave], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Geometria da tabela
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Perfil:
    nome: str
    fs: float  # fonte das linhas no layout original
    fs_min: float  # até onde encolhe antes de desistir
    entrelinha: float  # múltiplo de fs
    topo_base: float  # baseline da 1ª linha = topo da linha + topo_base + fs
    lw: float  # espessura da grade
    x_pad: float  # recuo do texto a partir da borda esquerda da coluna
    x_num: float  # x do Nº a partir da borda esquerda da tabela
    qtd_pad: float  # QTD alinhada à direita: borda direita − qtd_pad
    celulas: bool  # True = um 're' por célula (a nossa); False = segmentos
    fonte_sku: str
    fonte_qtd: str


_UPSELLER = _Perfil(
    "upseller",
    fs=9.0,
    fs_min=6.0,
    entrelinha=1.0,
    topo_base=1.0,
    lw=1.0,
    x_pad=4.5,
    x_num=5.6,
    qtd_pad=3.5,
    celulas=False,
    fonte_sku="helv",
    fonte_qtd="helv",
)
_NOSSA = _Perfil(
    "nossa",
    fs=6.5,
    fs_min=5.5,
    entrelinha=1.18,
    topo_base=2.0,
    lw=0.5,
    x_pad=2.0,
    x_num=2.0,
    qtd_pad=2.0,
    celulas=True,
    fonte_sku="hebo",
    fonte_qtd="hebo",
)


def _altura_linha(p: _Perfil, n: int, fs: float) -> float:
    if p.celulas:  # temu_declaracao.linha_tabela
        return max(10.0, 4 + fs + (n - 1) * fs * p.entrelinha + 2)
    return max(n * fs * p.entrelinha + 3.5 * fs / p.fs, 11.1)  # Upseller: 9n+3,5


@dataclass
class _Tabela:
    erro: str | None = None
    perfil: _Perfil | None = None
    bordas: tuple[float, ...] = ()
    y_cab: float = 0.0
    y_tot0: float = 0.0
    y_tot1: float = 0.0
    linhas: tuple[tuple[float, float], ...] = ()
    xs_total: tuple[float, ...] = ()


def _fundir(vals: list[float], tol: float = 1.2) -> list[float]:
    out: list[float] = []
    for v in sorted(vals):
        if out and v - out[-1] < tol:
            continue
        out.append(v)
    return out


def _segmentos(desenhos: list[dict]) -> tuple[list[tuple], list[tuple]]:
    """Grade = traços ESCUROS: segmentos 'l' e as 4 bordas de 're' com
    contorno. Traço branco (sobra de redação/remendo à mão) não é grade.
    H: (x0, x1, y); V: (x, y0, y1, tipo)."""
    hor: list[tuple] = []
    ver: list[tuple] = []
    for d in desenhos:
        cor = d.get("color")
        if cor is None or "s" not in (d.get("type") or "") or sum(cor) > 1.5:
            continue
        for it in d["items"]:
            if it[0] == "l":
                a, b = it[1], it[2]
                if abs(a.y - b.y) < 0.6:
                    hor.append((min(a.x, b.x), max(a.x, b.x), (a.y + b.y) / 2))
                elif abs(a.x - b.x) < 0.6:
                    ver.append(((a.x + b.x) / 2, min(a.y, b.y), max(a.y, b.y), "l"))
            elif it[0] == "re":
                r = it[1]
                hor += [(r.x0, r.x1, r.y0), (r.x0, r.x1, r.y1)]
                ver += [(r.x0, r.y0, r.y1, "re"), (r.x1, r.y0, r.y1, "re")]
    return hor, ver


def _localizar(hits: list, words: list, desenhos: list[dict]) -> _Tabela | None:
    """Âncora (`hits` = page.search_for(ANCORA)) + os 5 rótulos do cabeçalho
    numa linha → tabela. None = página sem tabela de itens. `_Tabela.erro` =
    tem tabela mas não deu pra ler."""
    if not hits:
        return None
    if len(hits) > 1:
        return _Tabela(erro="varias_tabelas")
    a = hits[0]
    cand = [w for w in words if a.y1 - 0.5 <= w[1] <= a.y1 + 15 and w[4] in _CAB]
    sku = next((w for w in cand if w[4] == "SKU"), None)
    if sku is None:
        return None
    # Mesma linha = topo OU base alinhados (o 'Nº' é menor que os outros).
    hdr = {w[4]: w for w in cand if abs(w[1] - sku[1]) < 1.5 or abs(w[3] - sku[3]) < 1.5}
    if set(hdr) != set(_CAB):
        return None

    hor, ver = _segmentos(desenhos)
    ym = (sku[1] + sku[3]) / 2
    cruzam = [v for v in ver if v[1] - 0.6 <= ym <= v[2] + 0.6]
    xs = _fundir([v[0] for v in cruzam], tol=0.8)

    def entre(lo: float, hi: float) -> float | None:
        c = [x for x in xs if lo - 0.5 <= x <= hi + 0.5]
        return c[0] if len(c) == 1 else None

    esq = [x for x in xs if x < hdr["Nº"][0]]
    dir_ = [x for x in xs if x > hdr["QTD"][2]]
    bordas = (
        esq[-1] if esq else None,
        entre(hdr["Nº"][2], hdr["SKU"][0]),
        entre(hdr["SKU"][2], hdr["DESCRIÇÃO"][0]),
        entre(hdr["DESCRIÇÃO"][2], hdr["VARIAÇÃO"][0]),
        entre(hdr["VARIAÇÃO"][2], hdr["QTD"][0]),
        dir_[0] if dir_ else None,
    )
    if None in bordas:
        return _Tabela(erro="grade_nao_achada")
    b = tuple(float(x) for x in bordas)  # type: ignore[arg-type]
    de_re = sum(1 for v in cruzam if v[3] == "re" and any(abs(v[0] - x) < 0.8 for x in b))
    de_l = sum(1 for v in cruzam if v[3] == "l" and any(abs(v[0] - x) < 0.8 for x in b))
    perfil = _NOSSA if de_re > de_l else _UPSELLER

    xc = (b[1] + b[2]) / 2  # meio da coluna SKU
    hy = _fundir([y for x0, x1, y in hor if x0 - 0.6 <= xc <= x1 + 0.6])
    abaixo = [y for y in hy if y >= sku[3] - 0.6]
    if not abaixo:
        return _Tabela(erro="sem_linha_do_cabecalho")
    y_cab = abaixo[0]
    tot = [w for w in words if w[4] == "Total" and w[1] > y_cab and b[3] - 1 <= w[0] <= b[4] + 1]
    if not tot:
        return _Tabela(erro="sem_total_na_pagina")
    t = min(tot, key=lambda w: w[1])
    y_tot0 = max([y for y in hy if y <= t[1] + 0.3], default=None)
    y_tot1 = min([y for y in hy if y >= t[3] - 0.3], default=None)
    if y_tot0 is None or y_tot1 is None or y_tot0 <= y_cab + 1:
        return _Tabela(erro="linha_do_total_nao_achada")
    seps = [y for y in hy if y_cab + 1 < y < y_tot0 - 1]
    linhas = tuple(zip([y_cab, *seps], [*seps, y_tot0], strict=True))
    ym_tot = (y_tot0 + y_tot1) / 2
    xs_total = tuple(
        _fundir(
            [
                v[0]
                for v in ver
                if v[1] - 0.6 <= ym_tot <= v[2] + 0.6 and b[0] - 1 <= v[0] <= b[5] + 1
            ],
            tol=0.8,
        )
    )
    if len(xs_total) < 2:
        xs_total = (b[0], b[4], b[5])
    return _Tabela(
        perfil=perfil,
        bordas=b,
        y_cab=y_cab,
        y_tot0=y_tot0,
        y_tot1=y_tot1,
        linhas=linhas,
        xs_total=xs_total,
    )


def _spans(page: fitz.Page) -> list[dict]:
    out = []
    for bl in page.get_text("dict")["blocks"]:
        for ln in bl.get("lines", []):
            for s in ln["spans"]:
                if s["text"].strip():
                    out.append(s)
    return out


def _negrito(span: dict) -> bool:
    return bool(span.get("flags", 0) & 16) or "bold" in (span.get("font") or "").lower()


# ---------------------------------------------------------------------------
# Leitura (conferência / testes)
# ---------------------------------------------------------------------------


def _ler_linhas(words: list, tab: _Tabela) -> list[ItemDeclaracao]:
    b = tab.bordas
    out = []
    for y0, y1 in tab.linhas:
        cel: dict[int, list] = {k: [] for k in range(5)}
        for w in words:
            if not (y0 < (w[1] + w[3]) / 2 < y1):
                continue
            for k in range(5):
                if b[k] - 0.5 <= w[0] < b[k + 1] - 0.5:
                    cel[k].append(w)

        def junta(ws: list, sep: str) -> str:
            por_linha: dict[int, list[str]] = {}
            for w in sorted(ws, key=lambda w: (round(w[1]), w[0])):
                por_linha.setdefault(round(w[1]), []).append(w[4])
            return sep.join(" ".join(v) for v in por_linha.values())

        digitos = re.sub(r"\D", "", junta(cel[4], ""))
        out.append(
            ItemDeclaracao(
                sku=junta(cel[1], ""),
                descricao=junta(cel[2], " "),
                variacao=junta(cel[3], " "),
                quantidade=int(digitos) if digitos else 1,
            )
        )
    return out


def ler_declaracao(pdf_bytes: bytes) -> list[ItemDeclaracao] | None:
    """O que a tabela de itens mostra hoje (SKU sem espaço, descrição e
    variação juntadas com espaço, QTD só os dígitos). None = sem tabela legível
    numa página só. Para conferência e testes — a reescrita não depende disto."""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for page in doc:
            hits = page.search_for(ANCORA)
            if not hits:
                continue
            words = page.get_text("words")
            tab = _localizar(hits, words, page.get_drawings())
            if tab is None:
                continue
            return None if tab.erro else _ler_linhas(words, tab)
    except Exception:  # noqa: BLE001
        return None
    return None


def total_declaracao(pdf_bytes: bytes) -> int | None:
    """Número escrito na célula de quantidade do Total (conferência/testes)."""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for page in doc:
            hits = page.search_for(ANCORA)
            if not hits:
                continue
            words = page.get_text("words")
            tab = _localizar(hits, words, page.get_drawings())
            if tab is None:
                continue
            if tab.erro:
                return None
            dig = "".join(
                w[4]
                for w in words
                if tab.y_tot0 < (w[1] + w[3]) / 2 < tab.y_tot1 and w[0] >= tab.bordas[4] - 1
            )
            dig = re.sub(r"\D", "", dig)
            return int(dig) if dig else None
    except Exception:  # noqa: BLE001
        return None
    return None


# ---------------------------------------------------------------------------
# Montagem das linhas novas
# ---------------------------------------------------------------------------


def _quebrar_palavras(texto: str, larg: float, fs: float, fonte: str) -> list[str]:
    linhas: list[str] = []
    atual = ""
    for p in (texto or "").split():
        while fitz.get_text_length(p, fontname=fonte, fontsize=fs) > larg and len(p) > 1:
            k = len(p)  # palavra maior que a coluna: parte no caractere
            while k > 1 and fitz.get_text_length(p[:k], fontname=fonte, fontsize=fs) > larg:
                k -= 1
            if atual:
                linhas.append(atual)
                atual = ""
            linhas.append(p[:k])
            p = p[k:]
        teste = f"{atual} {p}".strip()
        if not atual or fitz.get_text_length(teste, fontname=fonte, fontsize=fs) <= larg:
            atual = teste
        else:
            linhas.append(atual)
            atual = p
    if atual:
        linhas.append(atual)
    return linhas or [""]


def _quebrar_sku(sku: str, larg: float, fs: float, fonte: str) -> list[str]:
    """Kit 'dg053.sp+a001.sp' quebra depois de '+' ou '.'; por caractere só
    se um pedaço sozinho não couber."""
    partes: list[str] = []
    cur = ""
    for ch in sku or "":
        cur += ch
        if ch in "+.":
            partes.append(cur)
            cur = ""
    if cur:
        partes.append(cur)
    linhas: list[str] = []
    atual = ""
    for p in partes:
        if atual and fitz.get_text_length(atual + p, fontname=fonte, fontsize=fs) > larg:
            linhas.append(atual)
            atual = ""
        while fitz.get_text_length(p, fontname=fonte, fontsize=fs) > larg and len(p) > 1:
            k = len(p)
            while k > 1 and fitz.get_text_length(p[:k], fontname=fonte, fontsize=fs) > larg:
                k -= 1
            if atual:
                linhas.append(atual)
                atual = ""
            linhas.append(p[:k])
            p = p[k:]
        atual += p
    if atual:
        linhas.append(atual)
    return linhas or [""]


def _montar(
    itens: Sequence[ItemDeclaracao], b: tuple[float, ...], p: _Perfil, fs: float
) -> list[tuple[list[list[str]], float]]:
    larg = [b[k + 1] - b[k] - 2 * p.x_pad for k in range(5)]
    rows = []
    for i, it in enumerate(itens, 1):
        cel = [
            [str(i)],
            _quebrar_sku(_texto_imprimivel(it.sku), larg[1], fs, p.fonte_sku),
            _quebrar_palavras(_texto_imprimivel(it.descricao), larg[2], fs, "helv"),
            _quebrar_palavras(_texto_imprimivel(it.variacao), larg[3], fs, "helv"),
            [str(_quantidade(it.quantidade))],
        ]
        n = max(len(c) for c in cel)
        rows.append((cel, _altura_linha(p, n, fs)))
    return rows


def _tamanhos(p: _Perfil) -> list[float]:
    out = []
    fs = p.fs
    while fs >= p.fs_min - 1e-6:
        out.append(round(fs, 2))
        fs -= 0.5
    return out


def _deslocar_desenho(sh: fitz.Shape, d: dict, dy: float) -> bool:
    """Redesenha `d` deslocado dy pra baixo. False = tem item que não sei
    redesenhar (aí o rabo não desce)."""
    m = fitz.Matrix(1, 0, 0, 1, 0, dy)
    for it in d["items"]:
        op = it[0]
        if op == "l":
            sh.draw_line(it[1] * m, it[2] * m)
        elif op == "re":
            sh.draw_rect(it[1] * m)
        elif op == "qu":
            sh.draw_quad(it[1] * m)
        elif op == "c":
            sh.draw_bezier(it[1] * m, it[2] * m, it[3] * m, it[4] * m)
        else:
            return False
    lc = d.get("lineCap")
    sh.finish(
        color=d.get("color"),
        fill=d.get("fill"),
        width=d.get("width") or 1.0,
        closePath=bool(d.get("closePath")),
        even_odd=bool(d.get("even_odd")),
        lineCap=max(lc) if isinstance(lc, (tuple, list)) and lc else int(lc or 0),
        lineJoin=int(d.get("lineJoin") or 0),
        dashes=d.get("dashes") or None,
        fill_opacity=d.get("fill_opacity", 1) if d.get("fill") is not None else 1,
        stroke_opacity=d.get("stroke_opacity", 1) if d.get("color") is not None else 1,
    )
    return True


def _redesenhavel(d: dict) -> bool:
    return all(it[0] in ("l", "re", "qu", "c") for it in d["items"])


def _marca_do_pdf(doc: fitz.Document) -> str | None:
    kw = (doc.metadata or {}).get("keywords") or ""
    for tok in re.split(r"[\s,;]+", kw):
        if tok.startswith(_MARCA):
            return tok[len(_MARCA) :]
    return None


def _gravar_marca(doc: fitz.Document, assinatura: str) -> None:
    meta = {k: v for k, v in (doc.metadata or {}).items() if k not in ("format", "encryption")}
    outros = [
        t
        for t in re.split(r"[\s,;]+", meta.get("keywords") or "")
        if t and not t.startswith(_MARCA)
    ]
    meta["keywords"] = " ".join([*outros, _MARCA + assinatura])
    doc.set_metadata(meta)


# ---------------------------------------------------------------------------
# Reescrita
# ---------------------------------------------------------------------------


def _reescrever_pagina(
    page: fitz.Page,
    tab: _Tabela,
    itens: Sequence[ItemDeclaracao],
    desenhos: list[dict],
    spans: list[dict],
) -> tuple[str, str]:
    p = tab.perfil
    assert p is not None
    b = tab.bordas
    lw = p.lw
    larg_pag, alt_pag = page.rect.width, page.rect.height
    y_cab, y_tot0, y_tot1 = tab.y_cab, tab.y_tot0, tab.y_tot1
    corpo_antigo = y_tot0 - y_cab
    fim_total = y_tot1 + lw / 2

    # Rabo = tudo abaixo da linha do Total (caixa DECLARAÇÃO, assinatura).
    rabo_spans = [s for s in spans if s["bbox"][1] >= fim_total]
    rabo_des = [d for d in desenhos if d["rect"].y0 >= fim_total]
    tem_rabo = bool(rabo_spans or rabo_des)
    fim_conteudo = max(
        [s["bbox"][3] for s in rabo_spans] + [d["rect"].y1 for d in rabo_des] + [y_tot1]
    )
    # Só desce o rabo se der pra redesenhar tudo dele e nada atravessa a
    # linha do Total (desenho que começa em cima e termina embaixo ficaria
    # no lugar, rasgando a tabela nova).
    imagens_abaixo = [i for i in page.get_image_info() if fitz.Rect(i["bbox"]).y1 > fim_total]
    atravessa = [
        d
        for d in desenhos
        if d["rect"].y0 < y_tot1 - 0.5
        and d["rect"].y1 > fim_total + 1.0
        and d.get("color") is not None
        and sum(d["color"]) <= 1.5
    ]
    pode_descer = not imagens_abaixo and not atravessa and all(_redesenhavel(d) for d in rabo_des)
    limite = alt_pag - _MARGEM_PAGINA

    escolha = None
    for fs in _tamanhos(p):
        rows = _montar(itens, b, p, fs)
        delta = sum(h for _, h in rows) - corpo_antigo
        if delta <= 0.05:
            escolha = (fs, rows, 0.0, delta)
            break
        if pode_descer and fim_conteudo + delta <= limite:
            escolha = (fs, rows, delta, delta)
            break
    if escolha is None:
        motivo = "rabo_fixo" if not pode_descer else "pagina_cheia"
        return "nao_coube", f"{motivo} itens={len(itens)}"
    fs, rows, delta, sobra = escolha
    if delta == 0.0:  # corpo menor (ou igual): estica a última linha, Total e rabo ficam
        cel, h = rows[-1]
        rows[-1] = (cel, h - sobra)

    # Conteúdo da linha do Total (data, 'Total', fundo cinza) pra redesenhar.
    no_total = [s for s in spans if y_tot0 < (s["bbox"][1] + s["bbox"][3]) / 2 < y_tot1]
    qtd_spans = [s for s in no_total if s["bbox"][0] >= b[4] - 1 and re.search(r"\d", s["text"])]
    qtd_span = max(qtd_spans, key=lambda s: s["bbox"][2]) if qtd_spans else None
    outros_total = [s for s in no_total if s is not qtd_span]
    fundos_total = [
        d
        for d in desenhos
        if d.get("fill") is not None
        and sum(d["fill"]) < 2.9
        and "f" in (d.get("type") or "")
        and all(it[0] == "re" for it in d["items"])
        and y_tot0 - 1 <= (d["rect"].y0 + d["rect"].y1) / 2 <= y_tot1 + 1
        and d["rect"].height <= (y_tot1 - y_tot0) + 2
    ]

    # 1) Apaga corpo + linha do Total (+ rabo, se desce). REMOVE_IF_COVERED: só
    #    some o traço INTEIRO dentro da caixa — a borda externa do Upseller às
    #    vezes é UM segmento do título até a 1ª linha (k_4874bf) e não pode
    #    sumir do cabeçalho. A caixa começa lw/2+0,1 acima da linha de baixo do
    #    cabeçalho (ela some e é redesenhada).
    x_lo, x_hi = b[0] - lw - 1.5, b[5] + lw + 1.5
    page.add_redact_annot(fitz.Rect(x_lo, y_cab - lw / 2 - 0.1, x_hi, fim_total + 0.2), fill=False)
    descer = delta > 0.05 and tem_rabo
    if descer:
        topo_rabo = min([s["bbox"][1] for s in rabo_spans] + [d["rect"].y0 for d in rabo_des])
        page.add_redact_annot(fitz.Rect(0, topo_rabo - 0.5, larg_pag, fim_conteudo + 1), fill=False)
    page.apply_redactions(
        images=fitz.PDF_REDACT_IMAGE_NONE,
        graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
        text=fitz.PDF_REDACT_TEXT_REMOVE,
    )

    # 2) Fundo branco só por dentro da faixa antiga (esconde qualquer sobra
    #    parcial de traço), depois a grade nova por cima.
    page.draw_rect(
        fitz.Rect(b[0] + lw / 2, y_cab + lw / 2, b[5] - lw / 2, y_tot1 - lw / 2),
        color=None,
        fill=(1, 1, 1),
        width=0,
    )
    sh = page.new_shape()
    sh.draw_line((b[0] - lw / 2, y_cab), (b[5] + lw / 2, y_cab))
    textos: list[tuple[float, float, str, float, str]] = []
    entre = fs * p.entrelinha
    y = y_cab
    for cel, h in rows:
        y1 = y + h
        if p.celulas:
            for k in range(5):
                sh.draw_rect(fitz.Rect(b[k], y, b[k + 1], y1))
        else:
            for x in b:
                sh.draw_line((x, y), (x, y1))
            sh.draw_line((b[0] - lw / 2, y1), (b[5] + lw / 2, y1))
        base = y + p.topo_base + fs
        for k, linhas in enumerate(cel):
            fonte = p.fonte_sku if k == 1 else p.fonte_qtd if k == 4 else "helv"
            for j, t in enumerate(linhas):
                if not t:
                    continue
                larg_t = fitz.get_text_length(t, fontname=fonte, fontsize=fs)
                if k == 0:
                    x = min(b[0] + p.x_num, b[1] - 1.5 - larg_t)
                elif k == 4:
                    x = b[5] - p.qtd_pad - larg_t
                else:
                    x = b[k] + p.x_pad
                textos.append((x, base + j * entre, t, fs, fonte))
        y = y1

    # 3) Linha do Total, deslocada junto (delta), com a soma nova.
    yt0, yt1 = y, y + (y_tot1 - y_tot0)
    dy = yt0 - y_tot0
    sh.finish(color=(0, 0, 0), fill=None, width=lw)
    for d in fundos_total:
        for it in d["items"]:
            r = it[1]
            sh.draw_rect(fitz.Rect(r.x0, r.y0 + dy, r.x1, r.y1 + dy))
        sh.finish(color=None, fill=d["fill"], width=0)
    xs_t = tab.xs_total
    if p.celulas:
        for x0, x1 in zip(xs_t, xs_t[1:], strict=False):
            sh.draw_rect(fitz.Rect(x0, yt0, x1, yt1))
    else:
        for x in xs_t:
            sh.draw_line((x, yt0), (x, yt1 + lw / 2))
        sh.draw_line((b[0] - lw / 2, yt1), (b[5] + lw / 2, yt1))
    sh.finish(color=(0, 0, 0), fill=None, width=lw)
    sh.commit()
    for s in outros_total:
        ox, oy = s["origin"]
        textos.append((ox, oy + dy, s["text"], s["size"], "hebo" if _negrito(s) else "helv"))
    total = str(sum(_quantidade(i.quantidade) for i in itens))
    if qtd_span is not None:
        q_fs, q_f = qtd_span["size"], "hebo" if _negrito(qtd_span) else "helv"
        q_dir, q_base = qtd_span["bbox"][2], qtd_span["origin"][1] + dy
    else:
        q_fs, q_f = 7.0 if not p.celulas else 6.5, p.fonte_qtd
        q_dir, q_base = b[5] - p.qtd_pad, yt0 + (yt1 - yt0) * 0.75
    textos.append(
        (q_dir - fitz.get_text_length(total, fontname=q_f, fontsize=q_fs), q_base, total, q_fs, q_f)
    )
    for x, yb, t, size, fonte in textos:
        page.insert_text(
            (x, yb), _texto_imprimivel(t), fontsize=size, fontname=fonte, color=(0, 0, 0)
        )

    # 4) Rabo descido (texto em Helvetica na mesma posição/tamanho, +delta).
    if descer:
        sh = page.new_shape()
        for d in rabo_des:
            _deslocar_desenho(sh, d, delta)
        sh.commit()
        for s in rabo_spans:
            ox, oy = s["origin"]
            page.insert_text(
                (ox, oy + delta),
                _texto_imprimivel(s["text"]),
                fontsize=s["size"],
                fontname="hebo" if _negrito(s) else "helv",
                color=fitz.sRGB_to_pdf(s["color"]),
            )
    return "ok", f"{p.nome} fs={fs:g} itens={len(itens)} delta={delta:.1f}"


def _reescrever(pdf_bytes: bytes, itens: Sequence[ItemDeclaracao]) -> tuple[bytes, str, str]:
    if not itens:
        return pdf_bytes, "sem_itens", ""
    if not pdf_bytes:
        return pdf_bytes, "erro", "pdf_vazio"
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    if doc.needs_pass:
        return pdf_bytes, "erro", "pdf_protegido"
    if doc.page_count == 0:
        return pdf_bytes, "erro", "pdf_sem_paginas"
    assinatura = assinatura_itens(itens)
    if _marca_do_pdf(doc) == assinatura:
        return pdf_bytes, "igual", assinatura
    achadas = []
    for page in doc:
        hits = page.search_for(ANCORA)
        if not hits:  # etiqueta, NF: nem extrai desenho
            continue
        desenhos = page.get_drawings()
        tab = _localizar(hits, page.get_text("words"), desenhos)
        if tab is not None:
            achadas.append((page, tab, desenhos))
    if not achadas:
        return pdf_bytes, "sem_declaracao", ""
    if len(achadas) > 1 or achadas[0][1].erro == "varias_tabelas":
        return pdf_bytes, "multipagina", f"varias_declaracoes={len(achadas)}"
    page, tab, desenhos = achadas[0]
    if tab.erro == "sem_total_na_pagina":
        return pdf_bytes, "multipagina", f"pagina={page.number} sem Total"
    if tab.erro:
        return pdf_bytes, "erro", f"pagina={page.number} {tab.erro}"
    if page.rotation:
        return pdf_bytes, "erro", f"pagina={page.number} girada {page.rotation}"
    st, det = _reescrever_pagina(page, tab, itens, desenhos, _spans(page))
    if st != "ok":
        return pdf_bytes, st, det
    _gravar_marca(doc, assinatura)
    return (
        doc.tobytes(garbage=3, deflate=True),
        "ok",
        f"pagina={page.number} {det} {_MARCA}{assinatura}",
    )


def reescrever_declaracao(
    pdf_bytes: bytes, itens: Sequence[ItemDeclaracao] | None
) -> tuple[bytes, str, str]:
    """Reescreve a tabela de itens da declaração com `itens` (na ordem dada).

    Devolve (bytes, status, detalhe). Só `ok` devolve bytes novos; os outros
    status devolvem os bytes de ENTRADA intactos:

    - `igual`: o PDF já tem a marca destes itens (caminho rápido);
    - `sem_itens`: `itens` vazio/None (pedido sem espelho, pedido fake);
    - `sem_declaracao`: nenhuma página com a tabela (Amazon, SEDEX, NF);
    - `multipagina`: a tabela continua em outra página (sem 'Total' na
      página) ou há mais de uma declaração no PDF — fica pra mão;
    - `nao_coube`: nem encolhendo a fonte nem descendo o rabo;
    - `erro`: qualquer exceção ou layout que não deu pra ler (detalhe diz).
    """
    try:
        return _reescrever(pdf_bytes, list(itens or []))
    except Exception as exc:  # noqa: BLE001 — nunca trava subida nem impressão
        return pdf_bytes, "erro", f"{type(exc).__name__}: {exc}"[:300]
