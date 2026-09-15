"""Limpeza de HTML nas mensagens que chegam das plataformas.

15/09 (Eduardo, print do chamado 291645): o `claims/{id}/messages` do Mercado
Livre devolve o texto do mediador como HTML do editor deles (`<p><span
style="white-space: pre-wrap;">…</span></p>`, `<strong class="coco-editor-…">`,
`&nbsp;`). Isso ia cru pro histórico da aba Chamados (18 mensagens em 4
reclamações) e pro cérebro, que classificava como `dev_indefinido`.
"""
from __future__ import annotations

import html
import re

# Só mexe quando parece HTML de verdade — "a < b" ou "<3" ficam intactos.
_TEM_HTML = re.compile(
    r"(?i)<\s*/?\s*(p|span|b|strong|br|div|i|em|u|li|ul|ol|a|h[1-6])(\s[^<>]*)?/?\s*>"
)
_QUEBRA = re.compile(r"(?i)<\s*(br\s*/?|/\s*p|/\s*div|/\s*li|/\s*h[1-6])\s*>")
_ITEM = re.compile(r"(?i)<\s*li(\s[^<>]*)?>")
_TAG = re.compile(r"<[^<>]+>")
_ENTIDADE = re.compile(r"&(nbsp|amp|lt|gt|quot|#\d+|#x[0-9a-f]+);", re.I)


def limpar_html(texto: str | None) -> str:
    """Texto legível: quebras de parágrafo viram linha, tags somem, entidades
    são decodificadas e linhas vazias repetidas colapsam. Texto sem HTML volta
    igual (só com strip)."""
    if not texto:
        return texto or ""
    if not _TEM_HTML.search(texto) and not _ENTIDADE.search(texto):
        return texto
    t = _QUEBRA.sub("\n", texto)
    t = _ITEM.sub("\n• ", t)
    t = _TAG.sub("", t)
    t = html.unescape(t).replace("\xa0", " ")
    linhas: list[str] = []
    for linha in t.replace("\r", "").split("\n"):
        linha = re.sub(r"[ \t]+", " ", linha).strip()
        if not linha and (not linhas or not linhas[-1]):
            continue
        linhas.append(linha)
    return "\n".join(linhas).strip()
