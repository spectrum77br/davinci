"""A CONDIÇÃO do produto que o próprio anúncio declara (só o agente; 21/10/2026).

Decisão do dono (fontes v1.3, item 10; caso paulovitor85, "Esse relógio é
recondicionado?" → "É novo, não recondicionado": "já seria o suficiente para
ele, ele poderia responder sozinho"). No caminho do AGENTE, "novo", "lacrado",
"não é recondicionado" e "original" são afirmáveis quando o ANÚNCIO da conversa
diz isso — no título, no atributo de condição da plataforma (Shopee
"Condição: Novo", o `condition` do item; ML `ITEM_CONDITION`/`condition`) ou na
descrição. O legado continua com a trava antiga (só o título,
`evidencias.conferir_afirmacoes`).

O que sai (`condicao_do_anuncio`, na ficha do agente como "condicao"):

    {"novo": {"valor": true, "fontes": ["titulo", "atributo"]},
     "lacrado": {"valor": true, "fontes": ["descricao"]},
     "conflito_no_anuncio": false}

- `valor` true = o anúncio diz (novo, lacrado, original); false = diz o
  contrário (usado, seminovo, recondicionado, vitrine, open box; não lacrado;
  réplica, "não original"); "conflito" = diz as duas coisas — o anúncio se
  contradiz e a resposta não escolhe um lado ("corrigir anúncio", pessoa).
- só os campos que o anúncio declara; nada declarado = None.
- nunca o trecho da descrição (ela pode ter contato ou assunto que a ficha
  limpa tira): só de onde veio.

Cada fonte é lida com a forma dela: o TÍTULO pela mesma leitura do legado
(`evidencias._afirmacoes`, que a trava antiga já aceita); o ATRIBUTO pelo nome
de condição ("Condição", "Estado", "Condition", `ITEM_CONDITION`) e o valor;
a DESCRIÇÃO frase a frase, só na forma de condição do produto ("produto
novo", "100% novo", "novo e lacrado", "é original", "não é recondicionado",
"vem lacrado") — "nova versão", "novo design", "pode ser usado na água" e
"carregador original" não declaram a condição do produto.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Mapping
from typing import Any

from app.services.atendimento import evidencias
from app.services.atendimento.validador import plano_de

CAMPOS = ("novo", "lacrado", "original")
FONTE_TITULO = "titulo"
FONTE_ATRIBUTO = "atributo"
FONTE_DESCRICAO = "descricao"
CONFLITO = "conflito"
# A descrição crua lida (o começo; o resto é ficha técnica e política).
MAX_CHARS_DESCRICAO = 8_000


def _plano(texto: Any) -> str:
    return " ".join(plano_de(str(texto or "")).split())


# ── O contrário de NOVO: usado, seminovo, recondicionado, vitrine ──────────

# Sem ambiguidade em qualquer lugar.
_NAO_NOVO_SEMPRE = (
    r"semi\s*-?\s*nov[oa]s?|recondicionad[oa]s?|remanufaturad[oa]s?|recertificad[oa]s?"
    r"|refurbished|refurb|open\s*-?\s*box|de\s+vitrine|de\s+mostruario|mostruario"
)
_NAO_NOVO = re.compile(r"\b(?:" + _NAO_NOVO_SEMPRE + r")\b")
# Revisão L1–L3 (achado 4, 23/10/2026): no TÍTULO, "Vitrine", "Mostruário", "Showroom" e
# "Exposição" soltos ("Apple Watch Series 9 45mm Vitrine") também dizem que não é novo; na
# descrição, o "de exposição" e o "showroom".
_NAO_NOVO_NO_TITULO = re.compile(r"\b(?:vitrine|mostruario|showroom|exposicao)\b")
_NAO_NOVO_NA_DESCRICAO = re.compile(r"\b(?:de\s+exposicao|showroom)\b")
# "usado" só como CONDIÇÃO: no título, no atributo, ou na descrição com o
# produto/copula antes ("produto usado", "aparelho usado", "é usado", "estado de
# usado") e sem o complemento do verbo usar depois ("usado para", "usado na água").
_USADO_NA_DESCRICAO = re.compile(
    r"\b(?:produto|aparelho|item|celular|smartphone|relogio|smartwatch|e|eh|esta|estado\s+de"
    r"|condicao\s*:?|ja)\s+usad[oa]s?\b(?!\s+(?:para|pra|por|na|no|nas|nos|em|com|como|de|do|da"
    r"|ate|sem|apenas|somente|so|diariamente|frequentemente))"
)
# No título, o "usado" solto ("iPhone 11 Usado"), menos o do verbo usar ("o mais
# usado", "usado para corrida").
_USADO_SOLTO = re.compile(
    r"(?<!\bmais\s)\busad[oa]s?\b(?!\s+(?:para|pra|por|na|no|em|com|como|de|do|da))"
)
# A negação que vale para o termo (até 2 palavras antes: "não é
# recondicionado", "nunca usado", "sem ser usado").
_NEGA_ANTES = re.compile(r"\b(?:nao|nunca|nem|jamais)\b(?:\s+\S+){0,2}\s*$")
# "Sem uso", "nunca usado", "zero uso": o novo dito pelo avesso.
_SEM_USO = re.compile(r"\b(?:sem\s+uso|zero\s+uso|nunca\s+(?:foi\s+)?usad[oa]s?)\b")

# ── NOVO, LACRADO e ORIGINAL na descrição (a forma da condição) ───────────

_PRODUTO = (
    r"(?:produto|produtos|aparelho|aparelhos|item|itens|celular|celulares|smartphone|relogio"
    r"|smartwatch|fone|fones|mala|malas|unidade|unidades|mercadoria|pecas?)"
)
# O "novo" do produto: o produto/copula/intensidade antes, ou a condição
# depois ("novo e lacrado", "novo na caixa", "novo sem uso", "novo de fábrica").
_NOVO_DO_PRODUTO = re.compile(
    r"\b(?:" + _PRODUTO + r"|e|eh|sao|100\s*%|cem\s+por\s+cento|totalmente|completamente"
    r"|absolutamente|todos|todas|ja|esta|estao|chega|chegam|vem|vao|enviad[oa]s?|e\s+100\s*%)"
    r"\s+(?:\w+\s+)?nov[oa]s?\b(?!\s+(?:versao|modelo|design|geracao|lancamento|tecnologia"
    r"|func\w*|sistema|recurso\w*|layout|colecao|formato|padrao|chip|processador|visual|estilo"
    r"|cor|cores|opcao|opcoes|edicao|serie|linha|projeto))"
    r"|\bnov[oa]s?\s+(?:e\s+|,\s*)?(?:lacrad[oa]s?|na\s+caixa|sem\s+uso|de\s+fabrica|nunca\s+usad"
    r"|origina(?:l|is)|com\s+nota|com\s+garantia|em\s+folha)\b"
)
_LACRADO = re.compile(r"\blacrad[oa]s?\b")
# Revisão L1–L3: "caixa aberta", "pode ter sido aberto", "aberto para teste/conferência"
# também dizem que não vem lacrado (o "Lacrado" do título vira conflito: pessoa).
_NAO_LACRADO = re.compile(
    r"\b(?:sem\s+lacre|lacre\s+(?:rompido|violado|aberto)|deslacrad[oa]s?|caixa\s+aberta"
    r"|(?:pode|podem)\s+ter\s+sido\s+abert[oa]s?"
    r"|abert[oa]s?\s+(?:para|pra)\s+(?:teste|testes|conferencia|inspecao|revisao))\b"
)
# O "original" do PRODUTO (não "carregador original", "caixa original").
_ACESSORIO_ANTES = re.compile(
    r"\b(?:carregador\w*|cabo\w*|fonte\w*|caixa\w*|embalage\w*|pulseira\w*|peca\w*|tela\w*"
    r"|bateria\w*|acessorio\w*|capa\w*|pelicula\w*|manual\w*|nota|valor|preco|cor|foto\w*"
    r"|imagem\w*|anuncio\w*|texto|mensagem)\s+(?:\w+\s+)?$"
)
_ORIGINAL = re.compile(r"\borigina(?:l|is)\b")
# O que diz que NÃO é original (sem "similar", "cópia" e "genérico", que
# aparecem em "modelos similares", "cópia da nota" e "carregador genérico").
_NAO_ORIGINAL = re.compile(r"\b(?:replica\w*|primeira\s+linha|imitac\w*|falsificad\w*)\b")

# O nome do atributo de condição (Shopee, ML, TikTok) e o item-level do raw.
_NOME_CONDICAO = re.compile(r"\b(?:condicao|condition|estado|item\s*condition)\b")
_NOME_ORIGINAL = re.compile(r"\b(?:original|originalidade|autenticidade|genuino)\b")
_VALOR_NOVO = re.compile(r"^(?:nov[oa]s?|new|brand\s+new|novo\s+com\s+etiqueta|lacrad[oa])\b")
_VALOR_NAO_NOVO = re.compile(
    r"^(?:usad[oa]s?|used|" + _NAO_NOVO_SEMPRE + r"|not\s+new|seminovo|vitrine|showroom)\b"
)
_VALOR_SIM = re.compile(r"^(?:sim|yes|s|original|genuino|autentico)\b")
_VALOR_NAO = re.compile(r"^(?:nao|no|n)\b")

_TAG = re.compile(r"<[^<>]{0,500}>")
_FRASES = re.compile(r"[^.!?;\n]+")


def _sinal(trecho: str, inicio: int) -> bool:
    """O termo que começa em `inicio` vem negado (até 2 palavras antes)?"""
    return bool(_NEGA_ANTES.search(trecho[:inicio]))


def _do_titulo(titulo: Any) -> dict[str, set[bool]]:
    """O título pela leitura do legado (`evidencias._afirmacoes`) + o "usado"."""
    plano = _plano(titulo)
    achado: dict[str, set[bool]] = {}
    if not plano:
        return achado
    for campo, negativa in evidencias._afirmacoes(str(titulo)):
        if campo in CAMPOS:
            achado.setdefault(campo, set()).add(not negativa)
    for padrao in (_NAO_NOVO, _USADO_SOLTO, _NAO_NOVO_NO_TITULO):
        for m in padrao.finditer(plano):
            achado.setdefault("novo", set()).add(_sinal(plano, m.start()))
    for m in _NAO_ORIGINAL.finditer(plano):
        achado.setdefault("original", set()).add(_sinal(plano, m.start()))
    return achado


def _da_frase(frase: str) -> dict[str, set[bool]]:
    """Uma frase da descrição (plana): só a forma de condição do produto."""
    achado: dict[str, set[bool]] = {}
    for m in _NOVO_DO_PRODUTO.finditer(frase):
        achado.setdefault("novo", set()).add(not _sinal(frase, m.start()))
    if _SEM_USO.search(frase):
        achado.setdefault("novo", set()).add(True)
    for padrao in (_NAO_NOVO, _USADO_NA_DESCRICAO, _NAO_NOVO_NA_DESCRICAO):
        for m in padrao.finditer(frase):
            # "não é recondicionado" = novo; "é recondicionado" = não novo.
            achado.setdefault("novo", set()).add(_sinal(frase, m.start()))
    for m in _LACRADO.finditer(frase):
        if not re.search(r"\bdes$", frase[: m.start()]):
            achado.setdefault("lacrado", set()).add(not _sinal(frase, m.start()))
    if _NAO_LACRADO.search(frase):
        achado.setdefault("lacrado", set()).add(False)
    for m in _ORIGINAL.finditer(frase):
        if _ACESSORIO_ANTES.search(frase[: m.start()]):
            continue
        achado.setdefault("original", set()).add(not _sinal(frase, m.start()))
    for m in _NAO_ORIGINAL.finditer(frase):
        achado.setdefault("original", set()).add(_sinal(frase, m.start()))
    return achado


def _da_descricao(descricao: Any) -> dict[str, set[bool]]:
    if not isinstance(descricao, str) or not descricao.strip():
        return {}
    texto = _TAG.sub("\n", html.unescape(descricao[:MAX_CHARS_DESCRICAO]))
    achado: dict[str, set[bool]] = {}
    plano = _plano_linhas(texto)
    for m in _FRASES.finditer(plano):
        if _frase_e_pergunta(plano, m.end()):
            continue
        for campo, sinais in _da_frase(m.group(0)).items():
            achado.setdefault(campo, set()).update(sinais)
    return achado


def _plano_linhas(texto: str) -> str:
    """O texto plano com as quebras de linha (cada linha é uma frase)."""
    return "\n".join(" ".join(plano_de(linha).split()) for linha in texto.splitlines())


def _lista(valor: Any) -> list:
    return valor if isinstance(valor, list) else []


def _texto(valor: Any) -> str:
    return valor.strip() if isinstance(valor, str) else ""


def _atributos(raw: Mapping[str, Any]) -> Iterable[tuple[str, str]]:
    """(nome, valor) planos dos atributos do raw (Shopee, ML, TikTok) e o
    `condition` do item (Shopee "NEW"/"USED", ML "new"/"used")."""
    item = raw.get("item") if isinstance(raw.get("item"), Mapping) else None
    base = item if item else raw
    condicao = _texto(base.get("condition"))
    if condicao:
        yield "condition", _plano(condicao.replace("_", " "))
    for chave in ("attribute_list", "attributes", "product_attributes"):
        for a in _lista(base.get(chave)):
            if not isinstance(a, Mapping):
                continue
            nome = next(
                (
                    _texto(a.get(k))
                    for k in (
                        "display_attribute_name",
                        "original_attribute_name",
                        "attribute_name",
                        "name",
                    )
                    if _texto(a.get(k))
                ),
                "",
            )
            ident = _texto(a.get("id")).replace("_", " ")
            valores = [
                _texto(v.get(k))
                for v in (
                    _lista(a.get("attribute_value_list"))
                    or _lista(a.get("values"))
                    or _lista(a.get("value_list"))
                )
                if isinstance(v, Mapping)
                for k in ("display_value_name", "original_value_name", "value_name", "name")
                if _texto(v.get(k))
            ][:1] or [_texto(a.get("value_name"))]
            for valor in valores:
                if valor:
                    yield _plano(f"{nome} {ident}"), _plano(valor)


def _dos_atributos(raw: Any) -> dict[str, set[bool]]:
    achado: dict[str, set[bool]] = {}
    if not isinstance(raw, Mapping):
        return achado
    for nome, valor in _atributos(raw):
        if _NOME_CONDICAO.search(nome):
            if _VALOR_NOVO.search(valor):
                achado.setdefault("novo", set()).add(True)
                if _LACRADO.search(valor):
                    achado.setdefault("lacrado", set()).add(True)
            elif _VALOR_NAO_NOVO.search(valor):
                achado.setdefault("novo", set()).add(False)
        elif _NOME_ORIGINAL.search(nome):
            if _VALOR_SIM.search(valor):
                achado.setdefault("original", set()).add(True)
            elif _VALOR_NAO.search(valor) or _NAO_ORIGINAL.search(valor):
                achado.setdefault("original", set()).add(False)
    return achado


def condicao_do_anuncio(titulo: Any, descricao: Any, raw: Any) -> dict | None:
    """A condição que o anúncio declara (formato no topo), ou None. PURA.

    `titulo` = o título atual do anúncio; `descricao` = a descrição CRUA (a da
    ficha fresca ou do catálogo — a limpa já tirou as frases de condição);
    `raw` = o `raw_data` do anúncio (atributos e o `condition` do item).
    """
    por_fonte = (
        (FONTE_TITULO, _do_titulo(titulo)),
        (FONTE_ATRIBUTO, _dos_atributos(raw)),
        (FONTE_DESCRICAO, _da_descricao(descricao)),
    )
    saida: dict[str, Any] = {}
    conflito = False
    for campo in CAMPOS:
        sinais: set[bool] = set()
        fontes: list[str] = []
        for fonte, achado in por_fonte:
            if campo in achado:
                sinais |= achado[campo]
                fontes.append(fonte)
        if not sinais:
            continue
        if len(sinais) > 1:
            conflito = True
            saida[campo] = {"valor": CONFLITO, "fontes": fontes}
        else:
            saida[campo] = {"valor": next(iter(sinais)), "fontes": fontes}
    if not saida:
        return None
    saida["conflito_no_anuncio"] = conflito
    return saida


# ── O que um TEXTO afirma da condição (a resposta, a fala da loja) ────────


def afirmacoes(texto: Any) -> dict[str, bool]:
    """O que o texto AFIRMA da condição: campo → True (é novo/lacrado/original) ou
    False (não é). PURA. A leitura do legado (`evidencias._afirmacoes`, que já
    ignora pergunta, hipótese e "não posso confirmar") mais o recondicionado,
    o seminovo e o usado ("não é recondicionado" = novo). As duas polaridades
    no mesmo texto: nenhuma (o texto não decide).
    """
    texto = str(texto or "")
    sinais: dict[str, set[bool]] = {}
    for campo, negativa in evidencias._afirmacoes(texto):
        if campo in CAMPOS:
            sinais.setdefault(campo, set()).add(not negativa)
    plano = _plano(texto)
    for m in _FRASES.finditer(plano):
        frase = m.group(0)
        if _frase_e_pergunta(plano, m.end()):
            continue
        for padrao in (_NAO_NOVO, _USADO_NA_DESCRICAO):
            for achado in padrao.finditer(frase):
                sinais.setdefault("novo", set()).add(_sinal(frase, achado.start()))
        if _SEM_USO.search(frase):
            sinais.setdefault("novo", set()).add(True)
    return {campo: next(iter(s)) for campo, s in sinais.items() if len(s) == 1}


def _frase_e_pergunta(plano: str, fim: int) -> bool:
    return plano[fim : fim + 1] == "?"
