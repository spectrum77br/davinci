"""Rodapé de picking da etiqueta com os itens do pedido no Bling (09/11/2026).

A etiqueta da Shopee que sai do Upseller traz, no pé da página da ETIQUETA
(não da declaração), o rodapé de picking que o galpão usa pra separar o
pedido:

    SKU: 1; Total Items: 1; #UP2NYY264253; Deadline:09/11/2026
    1. dg062.pi+a003.pi                                          *1

(ArialUnicodeMS, cabeçalho 9 pt na baseline 391,5 e itens 8 pt de 9,8 em
9,8 pt numa página 300×442; quantidade 1 = "*1", maior = "( *2 )" alinhada
à esquerda em x 263,3; cabeçalho e quantidade em negrito — o Upseller pinta
e contorna o texto). Só a Shopee tem: o levantamento de ~1300 páginas
cruas em `~/nf-executor/saidas/etiquetas` achou o rodapé SÓ nas etiquetas
de remetente "Shopee …"; TikTok, Mercado Livre, Amazon, Temu e SEDEX não
têm.

Desde 04/08 o `transformar_etiqueta` APAGA esse rodapé (regra 4 do
transform). Pedido do Eduardo (07/11, aprovado 09/11): o rodapé volta, mas
com o SKU e a quantidade do PEDIDO NO BLING — os mesmos itens da declaração
(`nf_etiqueta_itens`) —, não o SKU do anúncio. Quando a equipe troca o
produto por falta de estoque (dg054.ci → dg054.sp, kit desmontado, outra
cor), o papel mostra o que vai de fato na caixa. O "#<ref>" e o
"Deadline:<data>" do rodapé original ficam quando conhecidos.

Camada PURA (sem banco, sem rede), como `nf_etiqueta_declaracao`:

- `ler_picking(pdf)` lê o rodapé da página da etiqueta (a 1ª página que não
  é DECLARAÇÃO DE CONTEÚDO): o cru do Upseller E o que nós mesmos
  escrevemos (pra reescrever depois). None = sem rodapé.
- `escrever_picking(pdf, itens, rodape, criar_se_faltar=False)` apaga o
  rodapé atual (só ele: os spans da linha "SKU:" e das linhas de item —
  texto que não é do rodapé na mesma faixa faz desistir com `nao_coube`) e
  escreve o novo em Helvetica (cabeçalho e quantidade em negrito, como o
  Upseller), no MESMO topo. Nunca invade as linhas "DUIMP:" que o
  transform carimba no pé da etiqueta nem a borda da página: encolhe a
  fonte até 6 pt e, se ainda não couber, resume o que sobra numa última
  linha "+N itens". Cabeçalho largo demais (ref comprida) encolhe até 6 pt,
  depois perde o Deadline e por fim encurta o #ref com "...".
- ORDEM dos itens: a da DECLARAÇÃO na mesma etiqueta quando ela tem os
  mesmos (SKU, quantidade) — a declaração não reescreve quando só a ordem
  muda, e o papel não pode ter "1." e "2." trocados entre o rodapé e a
  tabela —; senão, a do rodapé que já está no papel (mesmo conteúdo); senão,
  a do Bling.

Quem chama:

- SUBIDA (`/agent/etiqueta` e `/agent/etiqueta-lote`): `ler_picking` no PDF
  CRU (antes do transform apagar) e `escrever_picking` no transformado,
  depois da declaração. Sem itens do Bling (pedido fake, consulta que
  falhou) o rodapé volta com os mesmos itens, #ref e Deadline (inclusive o
  "Deadline: -") que o Upseller imprimiu.
- IMPRESSÃO (Controle de Estoque): sobre a cópia do blob que vai pra
  impressora, depois da declaração — realinha com os itens ATUAIS (troca
  depois da subida) e, em pedido da Shopee, `criar_se_faltar=True` devolve o
  rodapé às etiquetas guardadas entre 04/08 e hoje (sem #ref/Deadline). Só
  onde o transform APAGOU um rodapé — o retângulo branco da largura toda
  até a borda que a regra 4 deixa (no levantamento: 595 de 595 Shopee com
  rodapé têm, nenhuma das 16 Shopee sem rodapé nem das 2209 de outros
  marketplaces tem) — e se a faixa de baixo estiver vazia. Layout da Shopee
  que nunca teve rodapé (DANFE no pé, PAC) fica como está. O blob NÃO é
  regravado.

IDEMPOTENTE: rodar de novo com os mesmos itens devolve `igual` e os MESMOS
bytes (a comparação é pelo texto que já está no papel). Nunca levanta
exceção: qualquer falha devolve os bytes de ENTRADA com status `erro`.
Página girada (nunca vista nas reais) não é escrita: `nao_coube`/`sem_rodape`
com detalhe "girada", sem virar `erro`.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

import fitz  # PyMuPDF

from app.services.nf_etiqueta_declaracao import (
    ItemDeclaracao,
    _largura,
    _quantidade,
    _texto_imprimivel,
    ler_declaracao,
)

ANCORA = "Total Items"
# O rodapé só existe na parte de baixo da etiqueta (mesma regra do transform:
# "Total Items" acima disto não é o rodapé de picking).
_MIN_TOPO = 0.55
# Topo do rodapé do Upseller (384,6 / 442) — onde ele nasce quando é criado.
_TOPO_PADRAO = 384.6 / 442.0
# Faixa que precisa estar vazia pra criar o rodapé numa etiqueta antiga.
_FAIXA_LIVRE = 0.86

# Geometria medida no rodapé do Upseller (página 300×442).
_X_ESQ = 5.0
_X_QTD = 263.3 / 300.0  # fração da largura: a quantidade começa aqui
# Na leitura: a linha do rodapé começa na margem (x 5 de 300 no Upseller) e a
# coluna da quantidade fica à direita disto; span solto entre as duas (ou
# longe do vizinho) NÃO é do rodapé.
_X_INICIO = 0.10
_X_CORTE = 0.75
_GRUDADO = 1.5  # vão máximo (× fs) entre spans do mesmo pedaço da linha
_FS_CAB = 9.0
_FS_ITEM = 8.0
_FS_MIN = 6.0  # até onde os itens (e o cabeçalho) encolhem
# O Upseller engrossa (pinta + contorna) o cabeçalho e a quantidade; o SKU é
# normal. Aqui: Helvetica-Bold e Helvetica (Base14, texto extraível).
_FONTE_CAB = "hebo"
_FONTE_SKU = "helv"
_FONTE_QTD = "hebo"
_SOBE = 0.767  # tinta acima da baseline (× fs): 391,5 − 384,6 = 6,9 em 9 pt
_DESCE = 0.23  # tinta abaixo da baseline (× fs): 403,2 − 401,4 = 1,8 em 8 pt
_PASSO_CAB = 1.1  # baseline do cabeçalho → 1º item (× fs do cabeçalho): 9,9
# Vão mínimo de tinta entre o cabeçalho e o 1º item quando o cabeçalho
# encolhe mais que os itens (no Upseller: 393,6 → 395,3).
_FOLGA_LINHA = 1.2
_PASSO_ITEM = 1.225  # item → item (× fs do item): 9,8
# Folga mínima até a 1ª linha DUIMP e até a borda de baixo da página.
_FOLGA_DUIMP = 2.5
_MARGEM_PAGINA = 2.0
# Caixa do glifo que o MuPDF usa na redação: qualquer toque apaga o caractere.
_GLIFO_SOBE = 0.95

_RE_CAB = re.compile(r"^SKU:\s*(\d+)\s*;\s*Total Items:\s*(\d+)\s*(.*)$", re.IGNORECASE)
_RE_ITEM = re.compile(r"^(\d+)\.\s*(.*?)\s*$")
_RE_MAIS = re.compile(r"^\+\s*(\d+)\s+ite(?:m|ns)\b", re.IGNORECASE)
_RE_QTD = re.compile(r"\*\s*(\d+)")
_RE_PRAZO_VAZIO = re.compile(r"^deadline:\s*-?\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class PickingRodape:
    """O rodapé de picking como está no papel (cru do Upseller ou nosso)."""

    pagina: int
    altura_pagina: float
    topo: float  # topo da tinta da linha "SKU: …; Total Items: …"
    fundo: float  # fim da tinta da última linha do rodapé
    ref: str | None  # "UP2NYY264253" (sem o '#')
    deadline: str | None  # "09/11/2026"; "Deadline: -" vira None (e sem_prazo)
    itens: tuple[tuple[str, int], ...]  # (sku, quantidade), na ordem do papel
    mais: int = 0  # itens resumidos na linha "+N itens"
    total_skus: int = 0  # o "SKU: N" do cabeçalho
    total_itens: int = 0  # o "Total Items: N" do cabeçalho
    fs_cab: float = _FS_CAB
    fs_itens: float = _FS_ITEM
    base_cab: float = 0.0
    base_ultima: float = 0.0
    fs_ultima: float = _FS_ITEM
    cabecalho: str = ""  # texto da linha do cabeçalho, como está
    linhas: tuple[tuple[str, str], ...] = ()  # (esquerda, direita) de cada linha
    sem_prazo: bool = False  # o cabeçalho tem "Deadline: -" (sem data): volta igual

    def itens_declaracao(self) -> list[ItemDeclaracao]:
        """Os itens do papel no formato da declaração (só SKU e quantidade):
        é o que a subida usa quando o pedido não tem itens no Bling."""
        return [
            ItemDeclaracao(sku=s, descricao="", variacao="", quantidade=q) for s, q in self.itens
        ]


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------


def _sem_acento(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", texto or "") if not unicodedata.combining(c)
    ).upper()


def _e_declaracao(page: fitz.Page) -> bool:
    t = " ".join(_sem_acento(page.get_text("text")).split())
    return "DECLARACAO DE CONTEUDO" in t or "IDENTIFICACAO DOS BENS" in t


def _pagina_da_etiqueta(doc: fitz.Document) -> int | None:
    for page in doc:
        if not _e_declaracao(page):
            return page.number
    return None


# Texto sem os bytes das imagens: os spans saem IGUAIS (conferido em 2654
# páginas reais) e a leitura fica 5–20× mais rápida — roda em toda impressão.
_FLAGS_TEXTO = fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES


def _spans(page: fitz.Page) -> list[dict]:
    out = []
    for bl in page.get_text("dict", flags=_FLAGS_TEXTO)["blocks"]:
        for ln in bl.get("lines", []):
            for s in ln["spans"]:
                if s["text"].strip():
                    out.append(s)
    return out


def _tinta(s: dict) -> tuple[float, float]:
    """(topo, fundo) da TINTA do span pela baseline. O bbox do `get_text` usa
    o ascender/descender da fonte (na Helvetica Base14, 1,075/−0,299) e
    encosta na linha de cima e na de baixo."""
    y = float(s["origin"][1])
    fs = float(s["size"])
    return y - _SOBE * fs, y + _DESCE * fs


@dataclass
class _Linha:
    base: float
    spans: list[dict]

    @property
    def fs(self) -> float:
        return max(float(s["size"]) for s in self.spans)


def _linhas(spans: list[dict]) -> list[_Linha]:
    """Spans agrupados pela baseline (o '*1' da direita fica na linha do SKU)."""
    out: list[_Linha] = []
    for s in sorted(spans, key=lambda s: (s["origin"][1], s["bbox"][0])):
        y = float(s["origin"][1])
        if out and abs(out[-1].base - y) < 0.8:
            out[-1].spans.append(s)
        else:
            out.append(_Linha(y, [s]))
    for ln in out:
        ln.spans.sort(key=lambda s: s["bbox"][0])
    return out


def _juntar(spans: list[dict]) -> str:
    partes: list[str] = []
    fim = None
    for s in spans:
        if fim is not None and s["bbox"][0] - fim > 0.15 * float(s["size"]):
            partes.append(" ")
        partes.append(s["text"])
        fim = s["bbox"][2]
    return " ".join("".join(partes).split())


def _e_duimp(s: dict) -> bool:
    return s["text"].strip().upper().startswith("DUIMP")


def _fs(spans: list[dict]) -> float:
    return max(float(s["size"]) for s in spans)


def _pedacos(ln: _Linha, w: float) -> tuple[list[dict], list[dict]]:
    """(esquerda, direita) de uma linha do rodapé. A esquerda começa na margem
    (x < 10% da largura) e segue enquanto os spans estão colados; a direita é
    a coluna da quantidade (x ≥ 75%), também colada. Qualquer outro span da
    linha (carimbo, coluna extra, texto por cima) NÃO é do rodapé: não entra
    no SKU lido e, na escrita, conta como ocupante da faixa (→ `nao_coube`)."""
    corte = w * _X_CORTE

    def colado(ant: dict, s: dict) -> bool:
        vao = float(s["bbox"][0]) - float(ant["bbox"][2])
        return -1.0 <= vao <= _GRUDADO * float(s["size"])

    esq: list[dict] = []
    for s in ln.spans:  # já em ordem de x
        x0 = float(s["bbox"][0])
        if not esq:
            if x0 >= w * _X_INICIO:
                break
        elif x0 >= corte or not colado(esq[-1], s):
            break
        esq.append(s)
    dir_: list[dict] = []
    for s in ln.spans:
        if float(s["bbox"][0]) < corte or any(s is e for e in esq):
            continue
        if dir_ and not colado(dir_[-1], s):
            break
        dir_.append(s)
    return esq, dir_


def _ler_pagina(page: fitz.Page, spans: list[dict]) -> PickingRodape | None:
    return _ler(page, spans)[0]


def _ler(page: fitz.Page, spans: list[dict]) -> tuple[PickingRodape | None, list[dict]]:
    """(rodapé, os spans que são dele). Só esses spans são apagados na
    reescrita; o resto da faixa é ocupante."""
    h, w = page.rect.height, page.rect.width
    linhas = _linhas(spans)
    for k, ln in enumerate(linhas):
        if ln.base - _SOBE * ln.fs < h * _MIN_TOPO:
            continue
        esq_cab, _ = _pedacos(ln, w)
        if not esq_cab:
            continue
        cab = _juntar(esq_cab)
        if ANCORA.lower() not in cab.lower():
            continue
        m = _RE_CAB.match(cab)
        if not m:
            continue
        ref = deadline = None
        sem_prazo = False
        for parte in m.group(3).split(";"):
            parte = parte.strip()
            if parte.startswith("#"):
                ref = parte[1:].strip() or None
            elif parte.lower().startswith("deadline:"):
                d = parte.split(":", 1)[1].strip()
                deadline = d if d and d != "-" else None
                sem_prazo = deadline is None and bool(_RE_PRAZO_VAZIO.match(parte))
        fs_cab = _fs(esq_cab)
        do_rodape: list[dict] = list(esq_cab)
        itens: list[tuple[str, int]] = []
        texto_linhas: list[tuple[str, str]] = []
        mais = 0
        ultima, fs_ultima, fs_itens = ln, fs_cab, _FS_ITEM
        for nxt in linhas[k + 1 :]:
            if nxt.base - ultima.base > 1.6 * max(nxt.fs, fs_ultima) or any(
                _e_duimp(s) for s in nxt.spans
            ):
                break
            pe, pd = _pedacos(nxt, w)
            esq, dir_ = _juntar(pe), _juntar(pd)
            mm = _RE_MAIS.match(esq)
            mi = _RE_ITEM.match(esq)
            if mm and not dir_:
                mais = int(mm.group(1))
            elif mi and mi.group(2):
                q = _RE_QTD.search(dir_)
                itens.append((mi.group(2), int(q.group(1)) if q else 1))
            else:
                break
            if not texto_linhas:
                fs_itens = _fs(pe + pd)
            texto_linhas.append((esq, dir_))
            do_rodape.extend(pe + pd)
            ultima, fs_ultima = nxt, _fs(pe + pd)
            if mais:
                break  # "+N itens" é sempre a última
        rodape = PickingRodape(
            pagina=page.number,
            altura_pagina=h,
            topo=ln.base - _SOBE * fs_cab,
            fundo=ultima.base + _DESCE * fs_ultima,
            ref=ref,
            deadline=deadline,
            itens=tuple(itens),
            mais=mais,
            total_skus=int(m.group(1)),
            total_itens=int(m.group(2)),
            fs_cab=fs_cab,
            fs_itens=fs_itens,
            base_cab=ln.base,
            base_ultima=ultima.base,
            fs_ultima=fs_ultima,
            cabecalho=cab,
            linhas=tuple(texto_linhas),
            sem_prazo=sem_prazo,
        )
        return rodape, do_rodape
    return None, []


# Nº do pedido da Shopee ('261107NM1344HA'): AAMMDD + 8 letras/dígitos.
_RE_PEDIDO_SHOPEE = re.compile(r"^\d{6}[0-9A-Z]{8}$")


def parece_pedido_shopee(numeroloja: str | None) -> bool:
    """Reserva da impressão quando a loja do pedido não está em `store_info`:
    o formato do nº da Shopee (AAMMDD + 8, com pelo menos uma letra). ML e
    TikTok são só dígitos (16/18), Amazon tem hífens, Temu começa com 'PO-'."""
    n = (numeroloja or "").strip().upper()
    return bool(_RE_PEDIDO_SHOPEE.match(n)) and any(c.isalpha() for c in n[6:])


def ler_picking(pdf_bytes: bytes) -> PickingRodape | None:
    """O rodapé de picking da página da etiqueta (a 1ª página que não é
    DECLARAÇÃO DE CONTEÚDO), cru do Upseller ou escrito por nós. None = sem
    rodapé (TikTok, ML, Amazon, etiqueta já transformada, PDF ilegível).
    Nunca levanta."""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if doc.needs_pass or doc.page_count == 0:
            return None
        idx = _pagina_da_etiqueta(doc)
        if idx is None:
            return None
        page = doc[idx]
        return _ler_pagina(page, _spans(page))
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Montagem
# ---------------------------------------------------------------------------


def _norm(texto: str) -> str:
    return " ".join(_texto_imprimivel(texto or "").split())


def _texto_qtd(q: int) -> str:
    """Como o Upseller escreve: 1 = "*1"; mais de 1 = "( *2 )" (o parêntese
    chama o olho de quem separa)."""
    return "*1" if q == 1 else f"( *{q} )"


def _cortar(texto: str, larg: float, fs: float) -> str:
    """SKU que não cabe na linha (nunca visto: o maior kit tem ~25
    caracteres) termina em '...' em vez de passar por cima da quantidade."""
    if _largura(texto, _FONTE_SKU, fs) <= larg:
        return texto
    while texto and _largura(texto + "...", _FONTE_SKU, fs) > larg:
        texto = texto[:-1]
    return texto + "..."


@dataclass(frozen=True)
class _Plano:
    fs_cab: float
    fs_item: float
    cabecalho: str
    base_cab: float
    linhas: tuple[tuple[str, str, float], ...]  # (esquerda, direita, baseline)
    fundo: float
    mais: int


def _cabecalho(
    itens: Sequence[tuple[str, int]],
    ref: str | None,
    deadline: str | None,
    sem_prazo: bool = False,
) -> str:
    cab = f"SKU: {len(itens)}; Total Items: {sum(q for _s, q in itens)}"
    if ref:
        cab += f"; #{_norm(ref)}"
    if deadline:
        cab += f"; Deadline:{_norm(deadline)}"
    elif sem_prazo:
        cab += "; Deadline: -"  # como o Upseller imprime quando não tem data
    return cab


def _cabecalho_que_cabe(
    itens: Sequence[tuple[str, int]],
    ref: str | None,
    deadline: str | None,
    sem_prazo: bool,
    largura: float,
    fs_max: float,
) -> tuple[str, float] | None:
    """(texto, fonte) do cabeçalho na largura da página: encolhe de `fs_max`
    até 6 pt; se nem assim couber (ref comprida — as reais têm 12
    caracteres e o maior cabeçalho mede 260 de 290 pt), sai o Deadline e
    depois o #ref encurta com "...". O texto encurtado relido e replanejado
    dá o mesmo texto (idempotente). None = nem "SKU: N; Total Items: N" cabe."""
    larg = largura - 2 * _X_ESQ
    cab = _cabecalho(itens, ref, deadline, sem_prazo)
    fs = fs_max
    while fs >= _FS_MIN - 1e-6:
        if _largura(cab, _FONTE_CAB, fs) <= larg:
            return cab, fs
        fs -= 0.5
    fs = _FS_MIN
    sem_data = _cabecalho(itens, ref, None)
    if _largura(sem_data, _FONTE_CAB, fs) <= larg:
        return sem_data, fs
    base = _cabecalho(itens, None, None)
    r = _norm(ref or "")
    while r:
        r = r[:-1]
        cab = f"{base}; #{r}..." if r else base
        if _largura(cab, _FONTE_CAB, fs) <= larg:
            return cab, fs
    return (base, fs) if _largura(base, _FONTE_CAB, fs) <= larg else None


def _planejar(
    itens: Sequence[tuple[str, int]],
    ref: str | None,
    deadline: str | None,
    topo: float,
    limite: float,
    largura: float,
    sem_prazo: bool = False,
) -> _Plano | None:
    """Maior fonte (8 → 6 pt) em que o cabeçalho e TODAS as linhas cabem entre
    `topo` e `limite`; a 6 pt, o que não couber vira "+N itens" na última
    linha. None = nem o cabeçalho + 1 item cabem (ou o cabeçalho não cabe
    na largura)."""
    x_qtd = largura * _X_QTD
    larg_esq = x_qtd - 3.0 - _X_ESQ
    fs = _FS_ITEM
    while True:
        cabe = _cabecalho_que_cabe(
            itens, ref, deadline, sem_prazo, largura, min(_FS_CAB, fs + 1.0)
        )
        if cabe is None:
            return None
        cab, fs_cab = cabe
        base_cab = topo + _SOBE * fs_cab
        # 1º item: o passo do Upseller (9,9 em 9 pt) ou, se o cabeçalho
        # encolheu mais que os itens, o que a tinta das duas fontes pede.
        primeira = base_cab + max(
            _PASSO_CAB * fs_cab, _DESCE * fs_cab + _FOLGA_LINHA + _SOBE * fs
        )
        cabem = 0
        if base_cab + _DESCE * fs_cab <= limite:
            while primeira + cabem * _PASSO_ITEM * fs + _DESCE * fs <= limite:
                cabem += 1
        ultima_fonte = fs - 0.5 < _FS_MIN - 1e-6
        if cabem >= len(itens) or ultima_fonte:
            break
        fs -= 0.5
    if cabem >= len(itens):
        mostra, mais = list(itens), 0
    elif cabem >= 2:
        mostra, mais = list(itens[: cabem - 1]), len(itens) - (cabem - 1)
    else:
        return None
    linhas: list[tuple[str, str, float]] = []
    for i, (sku, q) in enumerate(mostra, 1):
        b = primeira + (i - 1) * _PASSO_ITEM * fs
        linhas.append((_cortar(f"{i}. {sku}", larg_esq, fs), _texto_qtd(q), b))
    if mais:
        b = primeira + len(mostra) * _PASSO_ITEM * fs
        linhas.append((f"+{mais} {'item' if mais == 1 else 'itens'}", "", b))
    return _Plano(
        fs_cab=fs_cab,
        fs_item=fs,
        cabecalho=cab,
        base_cab=base_cab,
        linhas=tuple(linhas),
        fundo=linhas[-1][2] + _DESCE * fs,
        mais=mais,
    )


def _mesmo_texto(atual: PickingRodape, plano: _Plano) -> bool:
    def n(s: str) -> str:
        return " ".join((s or "").split())

    return n(atual.cabecalho) == n(plano.cabecalho) and [
        (n(e), n(d)) for e, d in atual.linhas
    ] == [(n(e), n(d)) for e, d, _b in plano.linhas]


# ---------------------------------------------------------------------------
# Faixa livre / DUIMP
# ---------------------------------------------------------------------------


def _branco(cor: Sequence[float] | None) -> bool:
    return cor is None or sum(cor) >= 2.97


def _ocupantes(
    page: fitz.Page, faixa: fitz.Rect, spans: list[dict], ignora: set[int]
) -> list[str]:
    """O que tem dentro de `faixa` além do que `ignora` (ids dos spans do
    rodapé atual) e das linhas DUIMP: texto (pela tinta), imagem e desenho
    que não seja branco (o retângulo branco das redações do transform e o
    fundo branco da etiqueta não ocupam nada)."""
    achados: list[str] = []
    for s in spans:
        if id(s) in ignora or _e_duimp(s):
            continue
        # Por cima, a caixa do glifo (a que a redação usa), não só a tinta.
        y, fs = float(s["origin"][1]), float(s["size"])
        if y + _DESCE * fs > faixa.y0 + 0.3 and y - _GLIFO_SOBE * fs < faixa.y1 - 0.3:
            achados.append("texto")
    for im in page.get_image_info():
        r = fitz.Rect(im["bbox"])
        if r.y1 > faixa.y0 + 0.3 and r.y0 < faixa.y1 - 0.3:
            achados.append("imagem")
    for d in page.get_drawings():
        r = d["rect"]
        if not (r.y1 > faixa.y0 + 0.3 and r.y0 < faixa.y1 - 0.3):
            continue
        tipo = d.get("type") or ""
        tinta_traco = "s" in tipo and not _branco(d.get("color"))
        tinta_fundo = "f" in tipo and not _branco(d.get("fill"))
        if tinta_traco or tinta_fundo:
            achados.append("desenho")
    return achados


def _rastro_do_transform(page: fitz.Page) -> bool:
    """A etiqueta teve um rodapé de picking que o transform APAGOU: a regra 4
    redige de "Total Items" até a borda, na largura toda, e a redação deixa
    um retângulo branco exatamente assim. No levantamento das etiquetas
    cruas transformadas: 595 de 595 Shopee com rodapé têm; nenhuma das 16
    Shopee sem rodapé (DANFE no pé) nem das 2209 de outros marketplaces."""
    w, h = page.rect.width, page.rect.height
    for d in page.get_drawings():
        r = d["rect"]
        if (
            "f" in (d.get("type") or "")
            and d.get("fill") is not None
            and _branco(d.get("fill"))
            and r.x0 <= 1.0
            and r.x1 >= w - 1.0
            and r.y1 >= h - 1.0
            and h * _MIN_TOPO - 2.0 <= r.y0 < h - 1.0
        ):
            return True
    return False


def _duimp(spans: list[dict], altura: float) -> tuple[float | None, float | None]:
    """(topo da tinta, topo da caixa do glifo) da 1ª linha DUIMP do pé da
    página (o transform carimba de baixo pra cima a partir da borda)."""
    ds = [s for s in spans if _e_duimp(s) and float(s["origin"][1]) >= altura * _MIN_TOPO]
    if not ds:
        return None, None
    tinta = min(_tinta(s)[0] for s in ds)
    glifo = min(float(s["origin"][1]) - _GLIFO_SOBE * float(s["size"]) for s in ds)
    return tinta, glifo


# ---------------------------------------------------------------------------
# Escrita
# ---------------------------------------------------------------------------


def _itens_limpos(itens: Sequence[ItemDeclaracao]) -> list[tuple[str, int]]:
    out = []
    for it in itens:
        sku = _norm(str(getattr(it, "sku", "") or ""))
        if sku:
            out.append((sku, _quantidade(getattr(it, "quantidade", 1))))
    return out


def _chave(sku: str) -> str:
    # A declaração lê o SKU sem espaço (ele quebra entre linhas na célula).
    return "".join((sku or "").split())


def _na_ordem_de(
    lista: list[tuple[str, int]], referencia: Sequence[tuple[str, int]]
) -> list[tuple[str, int]] | None:
    """`lista` na ordem de `referencia` quando as duas têm exatamente os
    mesmos (SKU, quantidade) e só a ordem muda. None = conteúdo diferente."""
    if len(lista) != len(referencia):
        return None
    sobra: dict[tuple[str, int], list[int]] = {}
    for k, (sku, q) in enumerate(lista):
        sobra.setdefault((_chave(sku), q), []).append(k)
    out: list[tuple[str, int]] = []
    for sku, q in referencia:
        ks = sobra.get((_chave(sku), int(q)))
        if not ks:
            return None
        out.append(lista[ks.pop(0)])
    return out


def _ordem_do_papel(
    pdf_bytes: bytes, lista: list[tuple[str, int]], atual: PickingRodape | None
) -> list[tuple[str, int]]:
    """A ordem em que os itens saem no rodapé. Com o mesmo conteúdo, a do
    papel: primeiro a da DECLARAÇÃO desta etiqueta (que não reescreve quando
    só a ordem muda — 'o Bling e o marketplace às vezes invertem'), depois a
    do rodapé que já está impresso. Conteúdo diferente: a ordem dada (Bling),
    que é a mesma em que a declaração acabou de ser reescrita."""
    if len(lista) < 2:
        return lista
    decl = ler_declaracao(pdf_bytes) or []
    ordem = _na_ordem_de(lista, [(i.sku, i.quantidade) for i in decl])
    if ordem is None and atual is not None and not atual.mais:
        ordem = _na_ordem_de(lista, atual.itens)
    return ordem if ordem is not None else lista


def _escrever(
    pdf_bytes: bytes,
    itens: Sequence[ItemDeclaracao] | None,
    rodape: PickingRodape | None,
    criar_se_faltar: bool,
) -> tuple[bytes, str, str]:
    lista = _itens_limpos(list(itens or []))
    if not lista:
        return pdf_bytes, "sem_itens", ""
    if not pdf_bytes:
        return pdf_bytes, "erro", "pdf_vazio"
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    if doc.needs_pass:
        return pdf_bytes, "erro", "pdf_protegido"
    if doc.page_count == 0:
        return pdf_bytes, "erro", "pdf_sem_paginas"
    idx = _pagina_da_etiqueta(doc)
    if idx is None:
        return pdf_bytes, "sem_rodape", "so_declaracao"
    page = doc[idx]
    spans = _spans(page)
    atual, spans_do_rodape = _ler(page, spans)
    if atual is None and rodape is None and not criar_se_faltar:
        return pdf_bytes, "sem_rodape", ""
    # Criando do zero (etiqueta antiga da Shopee): só onde o transform apagou
    # um rodapé. Layout que nunca teve (DANFE no pé, PAC) fica como está.
    criando = atual is None and rodape is None
    if page.rotation:
        # Nunca visto nas reais: as coordenadas do texto lido não batem com
        # as da escrita. Não escreve, e não é erro (não loga warning a cada
        # impressão).
        status = "sem_rodape" if criando else "nao_coube"
        return pdf_bytes, status, f"pagina={idx} girada {page.rotation}"
    if criando and not _rastro_do_transform(page):
        return pdf_bytes, "sem_rodape", f"pagina={idx} sem_rastro"
    w, h = page.rect.width, page.rect.height

    if atual is not None:
        topo = atual.topo
    elif rodape is not None:
        topo = rodape.topo * h / (rodape.altura_pagina or h)
    else:
        topo = h * _TOPO_PADRAO
    fonte = rodape or atual
    ref = fonte.ref if fonte else None
    deadline = fonte.deadline if fonte else None
    sem_prazo = bool(fonte.sem_prazo) if fonte else False
    lista = _ordem_do_papel(pdf_bytes, lista, atual)

    duimp_tinta, duimp_glifo = _duimp(spans, h)
    limite = h - _MARGEM_PAGINA
    if duimp_tinta is not None:
        limite = min(limite, duimp_tinta - _FOLGA_DUIMP)
    plano = _planejar(lista, ref, deadline, topo, limite, w, sem_prazo)
    if plano is None:
        return pdf_bytes, "nao_coube", f"pagina={idx} itens={len(lista)} limite={limite:.1f}"
    if atual is not None and _mesmo_texto(atual, plano) and atual.fundo <= limite + 0.05:
        return pdf_bytes, "igual", f"pagina={idx}"

    # A faixa onde o rodapé vai precisa estar vazia (fora os spans do rodapé
    # atual e as linhas DUIMP): qualquer outro texto nela — até na mesma
    # linha do rodapé — faz desistir. Criando do zero numa etiqueta antiga,
    # a faixa inteira de baixo (0,86 da altura até a DUIMP ou a borda).
    do_rodape = {id(s) for s in spans_do_rodape}
    if criando:
        faixa = fitz.Rect(0, h * _FAIXA_LIVRE, w, duimp_tinta if duimp_tinta is not None else h)
    else:
        fim = max(plano.fundo, atual.fundo if atual else 0.0)
        faixa = fitz.Rect(0, topo - 1.0, w, fim + 0.5)
    ocupado = _ocupantes(page, faixa, spans, do_rodape)
    if ocupado:
        status = "sem_rodape" if criando else "nao_coube"
        return pdf_bytes, status, f"pagina={idx} faixa_ocupada {','.join(ocupado[:3])}"

    if atual is not None:
        # Só o texto do rodapé: da tinta do cabeçalho até a baseline da última
        # linha (a caixa do glifo de qualquer caractere tocado some inteira),
        # parando antes da caixa do glifo da 1ª DUIMP; na largura, só até
        # onde vão os spans do rodapé.
        y0 = atual.base_cab - 0.75 * atual.fs_cab
        y1 = atual.base_ultima + 0.1 * atual.fs_ultima
        if duimp_glifo is not None:
            y1 = min(y1, duimp_glifo - 0.5)
        x0 = max(0.0, min(float(s["bbox"][0]) for s in spans_do_rodape) - 1.0)
        x1 = min(w, max(float(s["bbox"][2]) for s in spans_do_rodape) + 1.0)
        page.add_redact_annot(fitz.Rect(x0, y0, x1, y1), fill=False)
        page.apply_redactions(
            images=fitz.PDF_REDACT_IMAGE_NONE,
            graphics=fitz.PDF_REDACT_LINE_ART_NONE,
            text=fitz.PDF_REDACT_TEXT_REMOVE,
        )

    preto = (0, 0, 0)
    page.insert_text(
        (_X_ESQ, plano.base_cab), plano.cabecalho, fontsize=plano.fs_cab, fontname=_FONTE_CAB,
        color=preto,
    )
    x_qtd = w * _X_QTD
    for esq, dir_, base in plano.linhas:
        page.insert_text(
            (_X_ESQ, base), esq, fontsize=plano.fs_item, fontname=_FONTE_SKU, color=preto
        )
        if dir_:
            larg = _largura(dir_, _FONTE_QTD, plano.fs_item)
            x = min(x_qtd, w - _MARGEM_PAGINA - larg)
            page.insert_text(
                (x, base), dir_, fontsize=plano.fs_item, fontname=_FONTE_QTD, color=preto
            )
    novo = doc.tobytes(garbage=3, deflate=True, no_new_id=True)
    acao = "trocado" if atual is not None else "criado" if criando else "devolvido"
    return (
        novo,
        "ok",
        f"pagina={idx} {acao} fs={plano.fs_item:g} itens={len(lista)} mais={plano.mais}",
    )


def escrever_picking(
    pdf_bytes: bytes,
    itens: Sequence[ItemDeclaracao] | None,
    rodape: PickingRodape | None,
    *,
    criar_se_faltar: bool = False,
) -> tuple[bytes, str, str]:
    """Escreve o rodapé de picking com `itens` na página da etiqueta — na
    ordem da declaração desta etiqueta (ou do rodapé impresso) quando o
    conteúdo é o mesmo, senão na ordem dada. `rodape` = o que `ler_picking`
    leu ANTES (o cru da subida: dá o topo, o #ref e o Deadline quando o PDF
    de agora já não tem rodapé).

    Devolve (bytes, status, detalhe). Só `ok` devolve bytes novos; os outros
    devolvem os bytes de ENTRADA intactos:

    - `igual`: o papel já mostra exatamente este rodapé;
    - `sem_rodape`: o PDF não tem rodapé, não veio `rodape` e não é pra criar
      — ou é pra criar mas o transform não apagou rodapé nenhum ali
      (`sem_rastro`), a faixa de baixo está ocupada ou a página é girada;
    - `sem_itens`: `itens` vazio/None (o rodapé que houver fica como está);
    - `nao_coube`: nem a 6 pt com "+N itens", a faixa tem outro texto (até
      na mesma linha do rodapé) ou a página é girada;
    - `erro`: qualquer exceção (detalhe diz).
    """
    try:
        return _escrever(pdf_bytes, itens, rodape, criar_se_faltar)
    except Exception as exc:  # noqa: BLE001 — nunca trava subida nem impressão
        return pdf_bytes, "erro", f"{type(exc).__name__}: {exc}"[:300]
