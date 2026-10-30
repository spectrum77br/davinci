"""O HTML do e-mail da caixa da barra, limpo para a equipe (30/10/2026).

O Eduardo, 30/10, com o mesmo e-mail aberto nas duas telas (o encaminhado da
Amirom, pela loja 7buyers): "temos de replicar o que acontece em e-mail ali pra
parte de email que quem trabalha no atendimento consegue ver". O e-mail da loja
aberto pela barra (AtendimentoEmailDaLojaAberto) passa a mostrar a mensagem
FORMATADA, como a aba E-mail (AtendimentoMailBody) — e a equipe não tem a aba.

A mesma régua do texto (`barra_lojas.texto_da_caixa`), agora no HTML:
  • o link de ACESSO sai do href — TODOS os links, se o e-mail fala de
    senha/código/acesso (`codigos.fala_de_acesso`); o texto do link fica, sem
    link, com o title "link de acesso removido";
  • a URL escrita (no texto, num title/alt/style…) vira "[link de acesso removido]";
  • a senha escrita vira "•" no texto VISÍVEL inteiro, mesmo quebrada em tags
    ("Senha: <b>Xp7!kL2wQz</b>") — o `mascarar_senha` guarda o tamanho, então
    cada pedaço volta para a sua tag;
  • sai o que nunca aparece: script (e iframe, xmp…), comentário, `on*`, a URL
    de outros atributos (src fora do <img>, background, action…).
Depois, a CONFERÊNCIA: o texto visível do HTML limpo é lido de novo com a mesma
régua; se ainda sobra link de acesso ou senha (a URL quebrada em duas tags), o
HTML não vai (None) e a tela mostra o texto — nunca o HTML pela metade.

O que fica: a imagem (<img src> http/https/data/cid: imagem não é link de
acesso, e a tela carrega como a aba E-mail) e a formatação. A tela ainda passa
o HTML pelo `renderMailHtml` (DOMPurify, CSS sem url(), iframe sem script).
Tudo é reescrito (texto e atributos escapados), nunca repassado cru. PURO.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser

from app.services.mail_atendimento import codigos

# Maior que isso (caracteres), só o texto.
MAXIMO = 2_000_000
TITULO_SEM_LINK = "link de acesso removido"

# Somem com o conteúdo (o parser lê o miolo como texto cru).
_SOMEM = frozenset({"script", "xmp", "iframe", "noembed", "noframes", "plaintext"})
# Atributos que levam a URL: só o href do <a> e o src do <img> ficam (conferidos).
_ATRIBUTOS_DE_URL = frozenset(
    {
        "href",
        "src",
        "srcset",
        "background",
        "poster",
        "action",
        "formaction",
        "cite",
        "longdesc",
        "data",
        "codebase",
        "classid",
        "archive",
        "usemap",
        "lowsrc",
        "dynsrc",
        "ping",
        "manifest",
        "profile",
        "xlink:href",
    }
)
# Fim de bloco: no texto visível vira quebra (a senha da célula ao lado não
# gruda no rótulo; a URL de duas linhas não vira uma só).
_BLOCOS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "caption",
        "center",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "title",
        "tr",
        "ul",
    }
)
_RE_TAG = re.compile(r"^[a-z][a-z0-9-]*(?::[a-z0-9-]+)?$")
_RE_ATRIBUTO = re.compile(r"^[a-z_][a-z0-9_.:-]*$")
_IMAGEM_OK = ("http://", "https://", "cid:", "data:image/")


@dataclass
class _Tag:
    nome: str
    atributos: list[tuple[str, str | None]]
    sozinha: bool


@dataclass
class _Css:
    texto: str


class _Leitor(HTMLParser):
    """O HTML em pedaços: a tag (`_Tag`), o fim dela (str "</x>"), o texto (int,
    o índice em `textos`) e o miolo do <style> (`_Css`)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.pecas: list[_Tag | str | int | _Css] = []
        self.textos: list[str] = []
        self.quebras: list[bool] = []  # um fim de bloco antes deste texto?
        self._quebra = False
        self._sumindo: str | None = None
        self._estilo = False

    def handle_starttag(self, tag, attrs):
        self._abre(tag, attrs, sozinha=False)

    def handle_startendtag(self, tag, attrs):
        self._abre(tag, attrs, sozinha=True)

    def _abre(self, tag: str, attrs, *, sozinha: bool) -> None:
        if self._sumindo:
            return
        if tag in _SOMEM:
            self._sumindo = None if sozinha else tag
            return
        if tag in _BLOCOS:
            self._quebra = True
        if not _RE_TAG.match(tag):
            return
        self._estilo = tag == "style" and not sozinha
        self.pecas.append(_Tag(tag, list(attrs), sozinha))

    def handle_endtag(self, tag):
        if self._sumindo:
            if tag == self._sumindo:
                self._sumindo = None
            return
        if tag in _BLOCOS:
            self._quebra = True
        if tag == "style":
            self._estilo = False
        if _RE_TAG.match(tag):
            self.pecas.append(f"</{tag}>")

    def handle_data(self, data):
        if self._sumindo or not data:
            return
        if self._estilo:
            self.pecas.append(_Css(data))
            return
        self.pecas.append(len(self.textos))
        self.textos.append(data)
        self.quebras.append(self._quebra)
        self._quebra = False

    # Comentário, <!DOCTYPE>, <?…?> e <![CDATA[…]]>: somem.
    def handle_comment(self, data):
        return

    def handle_decl(self, decl):
        return

    def handle_pi(self, data):
        return

    def unknown_decl(self, data):
        return


def _visivel(textos: list[str], quebras: list[bool]) -> tuple[str, list[tuple[int, int]]]:
    """O texto visível (os pedaços colados; quebra no fim de bloco) e onde cada pedaço está."""
    partes: list[str] = []
    onde: list[tuple[int, int]] = []
    n = 0
    for t, quebra in zip(textos, quebras, strict=True):
        if quebra:
            partes.append("\n")
            n += 1
        partes.append(t)
        onde.append((n, n + len(t)))
        n += len(t)
    return "".join(partes), onde


def _href(valor: str, todos: bool) -> tuple[str | None, bool]:
    """(o href que fica, saiu como link de acesso?). Só http(s) e mailto sem
    parâmetros ficam — o resto a tela tira de qualquer jeito (`safeLink`)."""
    v = valor.strip()
    baixo = v.lower()
    if baixo.startswith("mailto:"):
        return (v if "?" not in v else None), False
    if baixo.startswith(("http://", "https://")):
        if todos or codigos.link_de_acesso(v):
            return None, True
        return v, False
    return None, False


def _tag(t: _Tag, todos: bool, termo_no_assunto: bool) -> tuple[str, int]:
    """A tag reescrita (atributos conferidos e escapados) e os links que saíram."""
    saem = 0
    attrs: dict[str, str | None] = {}
    for nome, valor in t.atributos:
        if not _RE_ATRIBUTO.match(nome) or nome.startswith("on") or nome in attrs:
            continue
        if valor is None:
            attrs[nome] = None
        elif nome == "href" and t.nome == "a":
            fica, saiu = _href(valor, todos)
            if fica is not None:
                attrs[nome] = fica
            elif saiu:
                saem += 1
                attrs["title"] = TITULO_SEM_LINK
        elif nome == "src" and t.nome == "img":
            if valor.strip().lower().startswith(_IMAGEM_OK):
                attrs[nome] = valor.strip()
        elif nome in _ATRIBUTOS_DE_URL:
            continue
        else:
            limpo, n = codigos.mascarar_links(valor, todos=todos)
            saem += n
            if nome != "style":
                limpo, _ = codigos.mascarar_senha(limpo, termo_no_assunto=termo_no_assunto)
            attrs.setdefault(nome, limpo)
    partes = [t.nome]
    for nome, valor in attrs.items():
        partes.append(nome if valor is None else f'{nome}="{escape(valor, quote=True)}"')
    return f"<{' '.join(partes)}{' /' if t.sozinha else ''}>", saem


def limpar(assunto: str | None, texto: str | None, html: str | None) -> tuple[str | None, int]:
    """(o HTML que a equipe pode ver, links de acesso removidos) — ou (None, 0):
    sem HTML, grande demais, ou a conferência achou o que não podia sobrar."""
    if not html or not html.strip() or len(html) > MAXIMO:
        return None, 0
    leitor = _Leitor()
    try:
        leitor.feed(html)
        leitor.close()
    except Exception:  # noqa: BLE001 — HTML ilegível: fica o texto
        return None, 0
    bruto, _ = _visivel(leitor.textos, leitor.quebras)
    todos = codigos.fala_de_acesso(assunto, texto) or codigos.fala_de_acesso(assunto, bruto)
    termo_no_assunto = codigos._fala_de_senha(assunto)  # noqa: SLF001 — a régua do texto

    # Os links de cada pedaço de texto; depois a senha no visível inteiro.
    saem = 0
    textos: list[str] = []
    for t in leitor.textos:
        limpo, n = codigos.mascarar_links(t, todos=todos)
        textos.append(limpo)
        saem += n
    visivel, onde = _visivel(textos, leitor.quebras)
    sem_senha, _ = codigos.mascarar_senha(visivel, termo_no_assunto=termo_no_assunto)
    if len(sem_senha) != len(visivel):
        return None, 0
    textos = [sem_senha[a:b] for a, b in onde]

    # A conferência: o que a tela mostra, lido de novo, não tem link nem senha.
    final, _ = _visivel(textos, leitor.quebras)
    if codigos.mascarar_links(final, todos=todos)[1]:
        return None, 0
    if codigos.mascarar_senha(final, termo_no_assunto=termo_no_assunto)[0] != final:
        return None, 0

    saida: list[str] = []
    for p in leitor.pecas:
        if isinstance(p, int):
            saida.append(escape(textos[p], quote=False))
        elif isinstance(p, _Css):
            css, n = codigos.mascarar_links(p.texto, todos=todos)
            saem += n
            saida.append(css.replace("</", "<\\/"))
        elif isinstance(p, _Tag):
            tag, n = _tag(p, todos, termo_no_assunto)
            saem += n
            saida.append(tag)
        else:
            saida.append(p)
    return "".join(saida), saem
