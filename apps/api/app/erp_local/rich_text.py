"""Allowlist for product descriptions. Anything outside it never reaches a stored row.

Scripts, handlers, remote images and foreign styles are dropped; text is always kept escaped.
"""

import re
from html import escape
from html.parser import HTMLParser

from app.erp_local.core import ERPError

LIMIT = 60000
TAGS = {
    "a",
    "b",
    "blockquote",
    "br",
    "div",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "li",
    "ol",
    "p",
    "pre",
    "span",
    "strong",
    "sub",
    "sup",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
}
VOID = {"br", "hr"}
DROPPED = {
    "applet",
    "base",
    "embed",
    "frame",
    "frameset",
    "head",
    "iframe",
    "link",
    "math",
    "meta",
    "noscript",
    "object",
    "script",
    "select",
    "style",
    "svg",
    "template",
    "textarea",
    "title",
}
BLOCKS = {"blockquote", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "p", "pre", "tr"}
ALIGN = {"left", "center", "right", "justify"}
INDENT = re.compile(r"\d{1,3}(\.\d{1,2})?(px|em)")
MAX_DEPTH = 64


def _style(attributes):
    kept = []
    for part in (attributes.get("style") or "").split(";"):
        name, separator, value = part.partition(":")
        name, value = name.strip().lower(), value.strip().lower()
        if separator and name == "text-align" and value in ALIGN:
            kept.append(f"text-align: {value}")
        elif separator and name in {"padding-left", "margin-left"} and INDENT.fullmatch(value):
            kept.append(f"{name}: {value}")
    align = (attributes.get("align") or "").strip().lower()
    if align in ALIGN and not any(item.startswith("text-align") for item in kept):
        kept.append(f"text-align: {align}")
    return "; ".join(kept)


def _attributes(tag, attributes):
    kept = []
    style = _style(attributes)
    if style:
        kept.append(("style", style))
    if tag in {"td", "th"}:
        for name in ["colspan", "rowspan"]:
            value = attributes.get(name) or ""
            if re.fullmatch(r"[1-9]\d?", value):
                kept.append((name, value))
    if tag == "a":
        kept += [("href", attributes["href"].strip()), ("rel", "noopener noreferrer nofollow")]
    return "".join(f' {name}="{escape(value)}"' for name, value in kept)


class _Cleaner(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.output, self.text, self.open, self.dropping = [], [], [], []

    def _link_allowed(self, attributes):
        href = re.sub(r"[\x00-\x20\x7f]", "", attributes.get("href") or "")
        return re.match(r"https?://[^/]", href, re.I) is not None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if self.dropping:
            if tag == self.dropping[-1] and tag not in VOID:
                self.dropping.append(tag)
            return
        if tag in DROPPED:
            self.dropping.append(tag)
            return
        if tag not in TAGS or (tag == "a" and not self._link_allowed(attributes)):
            return
        if tag == "br":
            self.text.append("\n")
        if tag in BLOCKS and self.text and self.text[-1] != "\n":
            self.text.append("\n")
        if tag not in VOID and len(self.open) >= MAX_DEPTH:
            return
        self.output.append(f"<{tag}{_attributes(tag, attributes)}>")
        if tag not in VOID:
            self.open.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.dropping:
            if tag == self.dropping[-1]:
                self.dropping.pop()
            return
        if tag not in self.open:
            return
        while self.open:
            current = self.open.pop()
            self.output.append(f"</{current}>")
            if current == tag:
                break

    def handle_data(self, data):
        if not self.dropping:
            self.output.append(escape(data, quote=False))
            self.text.append(data)

    def finish(self):
        self.close()
        while self.open:
            self.output.append(f"</{self.open.pop()}>")
        return "".join(self.output), "".join(self.text).strip()


def clean(raw, name="descrição"):
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise ERPError("rich_text", f"Preencha {name} como texto.")
    if len(raw) > LIMIT:
        raise ERPError("rich_text", f"{name.capitalize()} ultrapassa {LIMIT} caracteres.")
    parser = _Cleaner()
    parser.feed(raw)
    html, plain = parser.finish()
    return html if plain or "<table" in html or "<hr" in html else ""


def plain_text(html):
    parser = _Cleaner()
    parser.feed(html or "")
    return parser.finish()[1]


def from_plain(value):
    paragraphs = [p for p in re.split(r"\n\s*\n", value or "") if p.strip()]
    return "".join(
        "<p>" + "<br>".join(escape(line, quote=False) for line in p.split("\n")) + "</p>"
        for p in paragraphs
    )
