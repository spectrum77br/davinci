"""O QUE A EQUIPE DIZ sobre NFC, 5G e zíper expansivo (a decisão do dono, 05/11/2026).

"confie no q a eqpe diz sempre pegue as mensagens sobre esses assuntos da eqpe pra validar". Sobre
um modelo ter ou não o recurso vale a equipe, mesmo contra o título do anúncio e o site da marca.
Quem usa: `especificacoes` (a fonte "equipe", acima de todas) e `agente.checagens` (a oferta de
outro anúncio e a negativa barrada).

`o_que_a_equipe_diz` responde True, False ou None (sem veredito: não muda nada) para o título, mais
o nome da variação quando há. Só com exatamente 1 código de modelo; a entrada por versão só vale
quando o texto diz a versão, e versões de resposta diferente dão None. PURO, sem importar o agente.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from app.services.atendimento.validador import plano_de

NFC = "nfc"
G5 = "5g"
ZIPER = "ziper_expansivo"

# 05/11/2026: o que a equipe disse no chat, lido por dois leitores independentes (decisão do dono).
# Modelo inteiro: True (tem) ou False (não tem). Por versão: um dict (nomes em `_VERSOES`; None = a
# versão existe, mas não tem veredito). Quem não está aqui não tem veredito.
EQUIPE: dict[str, dict[str, bool | dict[str, bool | None]]] = {
    NFC: {
        "a18": True,
        "s5": True,
        "wp60": True,
        "f112": True,
        "marine1": True,
        "f109": True,
        "s7": False,
        "f105": False,
        "c2": False,
        "c65": False,
        "c57s": False,
        "flat3c": False,
        "f110": {"pro": True},
        "p1": {"pro": True},
        "c68": {"24/512": True, "16/128": False, "24/256": None},
        "a17": {"12/64": False, "12/128": False, "premium": True},
    },
    G5: {
        "f109": True,
        "f112": True,
        "f110": {"pro": True},
        "a18": False,
        "c68": False,
        "f109s": False,
        "a17": False,
        "f110l": False,
        "f105": False,
        "s7": False,
        "s5": False,
        "marine1": False,
    },
    ZIPER: {
        "p7": True,
        "p8": True,
        "m4": False,
    },
}
# O A17 premium tem NFC, mas nunca é oferecido: a equipe também o chama de A18 Pro Max.
NUNCA_OFERECER = frozenset({"a17"})
# Os códigos que a busca da oferta procura quando o termo sozinho não acha ninguém: o corte de
# `catalogo.MAX_ANUNCIOS` deixava de fora quem a equipe confirma (o zíper no ML, 05/11/2026).
CODIGOS_PARA_BUSCA: dict[str, tuple[str, ...]] = {
    NFC: ("a18", "wp60", "f112"),
    G5: ("f112", "f109", "f110 pro"),
    ZIPER: ("p7", "p8"),
}


def _ram_rom(ram: int, rom: int) -> str:
    """ "12.64", "12/64", "12 GB / 64", "12gb+64gb", "12 Ram 64 Gb", "64 Gb Memoria 12 Gb",
    "64 Gb / 12 Gb". Lidos nos títulos reais de `product_links` (05/11/2026)."""
    return (
        rf"\b{ram}(?:\s*gb)?(?:\s*ram)?\s*[./+]\s*{rom}\b"
        rf"|\b{ram}(?:\s*gb)?\s*ram\s+{rom}\b"
        rf"|\b{rom}\s*gb\s*(?:/|memoria)?\s*{ram}\s*gb\b"
    )


def _memoria(rom: int) -> str:
    """A memória sozinha: "512 Gb", "24.512". O "128db" do alto-falante não é memória."""
    return rf"\b{rom}(?:\s*gb)?\b"


# Como cada versão de `EQUIPE` aparece no título. O C68 só tem uma versão de cada memória: 128 é a
# de 16/128 (o título real diz "4gb+128gb"), 256 é a de 24/256 e 512 é a de 24/512.
_VERSOES: dict[str, str] = {
    "pro": r"\bpro\b",
    "premium": r"\bpremium\b|" + _ram_rom(16, 128),
    "12/64": _ram_rom(12, 64),
    "12/128": _ram_rom(12, 128),
    "16/128": _memoria(128),
    "24/256": _memoria(256),
    "24/512": _memoria(512),
}

# ── A leitura do código de modelo ─────────────────────────────────────────

# O MODELO dito na pergunta ou no título ("A17", "A18", "C2", "F109", "S7"): letra(s) e
# número juntos. Fora o que é característica ou padrão ("IP68", "Wi-Fi 6", "USB-C"…).
_MODELO = re.compile(r"\b[a-z]{1,6}\d{1,4}[a-z]?\b")
_NAO_E_MODELO = re.compile(
    r"^(?:ipx?|wifi|android|ios|bt|usb|pd|qc|hdmi|sd|ddr|lpddr|ufs|mt|lte|cat|type|rs|rj|sp|mg"
    r"|ba|pa|am|uf|nf|cep)\d"
)


def _plano(texto: str | None) -> str:
    return " ".join(plano_de(texto or "").split())


def _modelos_do(texto: Any) -> set[str]:
    """Os modelos (`_MODELO`) de um texto, no plano. PURA."""
    return {
        m.group(0)
        for m in _MODELO.finditer(_plano(str(texto or "")))
        if not _NAO_E_MODELO.match(m.group(0))
    }


# O que só esta tabela precisa, medido nos 16,2 mil títulos de `product_links` (05/11/2026); o
# `_modelos_do` dos outros não muda.
# "Marine 1", "Flat 3c" (são 100% dos títulos reais), "F110 L", "F109 S", "WP53 S": o modelo em duas
# palavras. O "F109 S" lido como F109 dava a resposta oposta à do F109S em NFC e em 5G.
_DUAS_PALAVRAS = (
    (re.compile(r"\bmarine\s+1\b"), "marine1"),
    (re.compile(r"\bflat\s+3\s*c\b"), "flat3c"),
    (re.compile(r"\bhyper\s+7\s*s\b"), "hyper7s"),
    (re.compile(r"\bf110[\s-]+l\b"), "f110l"),
    (re.compile(r"\bf109[\s-]+s\b"), "f109s"),
    (re.compile(r"\bwp53[\s-]+s\b"), "wp53s"),
)
# "+ Fone U9", "+ Fone Com Fio S10+", "+ Relogio": o acessório do kit não é o modelo do aparelho.
_ACESSORIO = re.compile(
    r"\+\s*(?:fones?|relogio|smart\s*watch|oculos|air\s*tag|tag|acessorios?)\b[^+,]*"
)
# "P1-P8", "M1-M6": a faixa da linha toda não diz modelo nenhum.
_FAIXA = re.compile(r"\b([a-z]{1,2})\d{1,2}\s*-\s*\1\d{1,2}\b")
# "Helio G99", "Unisoc T606": o processador.
_PROCESSADOR = re.compile(
    r"\b(?:helio|mediatek|mtk|unisoc|tiger|dimensity|snapdragon|exynos)\s+[a-z]{0,2}\d+[a-z]{0,2}\b"
)
# O que parece modelo e não é: processador solto, os acessórios Uranyx, o código do fornecedor da
# mala, a cor numerada ("Preto2") e o "KIT4".
_NAO_CONTA = re.compile(
    r"^(?:g99|g96|g88|g81|t606|t612|t616|t618|t760|t820|u9|ufb\d+|usw\d+|uat\d+|ucm\d+|uaf\d+"
    r"|usg\d+|dt\d+|dtlg\d+[a-z]?|ppdt\d+|kit\d+|preto\d+|branco\d+|azul\d+)$"
)
# "A18 Pro Max … S5 Edition", "A17 … S5 Premium": é o nome de venda do aparelho, não outro modelo.
_S5_DE_VENDA = re.compile(r"\bs5\s+(?:premium|edition)\b")
_A1X = frozenset({"a17", "a18"})
# Só sobre este catálogo: o "P1" da mala não é o celular P1, nem o "M4" do MacBook é a mala M4.
_BAGAGEM = re.compile(r"\b(?:malas?|maletas?|frasqueiras?|necessaires?|mochilas?)\b")
# A equipe falou dos aparelhos da loja: o "Poco C65" e o "C65 Pro" dela não são o mesmo.
_OUTRA_MARCA = re.compile(
    r"\b(?:poco|xiaomi|redmi|realme|samsung|galaxy|motorola|moto|apple|iphone|ipad|macbook)\b"
)
# "Fossibot F112 Pro ... 4g": a equipe confirmou o 5G sem separar versão (poucas falas, em junho) e
# 100 anúncios vivos do ML dizem 4G. O título que só diz 4G fica sem veredito, não vira 5G.
_SO_4G = re.compile(r"\b4\s?g\b")
_TEM_5G = re.compile(r"\b5\s?g\b")


def _limpo(texto: Any) -> str:
    plano = _plano(str(texto or ""))
    for padrao, junto in _DUAS_PALAVRAS:
        plano = padrao.sub(junto, plano)
    return plano


def codigos_do(texto: Any) -> set[str]:
    """Os códigos de modelo do texto, sem o que não é modelo (ver acima). PURA."""
    plano = _limpo(texto)
    plano = _FAIXA.sub(" ", plano)
    plano = _PROCESSADOR.sub(" ", plano)
    codigos = {c for c in _modelos_do(_ACESSORIO.sub(" ", plano)) if not _NAO_CONTA.match(c)}
    if "s5" in codigos and codigos & _A1X and _S5_DE_VENDA.search(plano):
        codigos.discard("s5")
    return codigos


def _casadas(versoes: Mapping[str, bool | None], plano: str) -> set[bool | None]:
    return {resposta for nome, resposta in versoes.items() if re.search(_VERSOES[nome], plano)}


def _da_versao(
    versoes: Mapping[str, bool | None], titulo: Any, variacao: Any = None
) -> bool | None:
    """A resposta da versão que o texto diz: a variação é a mais específica. PURA."""
    for onde in (variacao, titulo):
        casadas = _casadas(versoes, _limpo(onde))
        if casadas:
            return casadas.pop() if len(casadas) == 1 else None
    return None


def o_que_a_equipe_diz(recurso: str, titulo: Any, variacao: Any = None) -> bool | None:
    """O que a equipe diz do `recurso` (`NFC`, `G5`, `ZIPER`) para o título e a variação. PURA.

    True, False, ou None: sem veredito (0 ou 2+ códigos, modelo fora da tabela, a versão que o
    texto não diz, versões de resposta diferente). A variação é a mais específica: "24.512" no
    anúncio "16.128 ou 24.512" é a de 24/512; se ela não diz a versão, vale o título."""
    if not isinstance(recurso, str) or recurso not in EQUIPE:
        return None
    texto = " ".join(str(t) for t in (titulo, variacao) if t)
    plano = _limpo(texto)
    if _OUTRA_MARCA.search(plano) or bool(_BAGAGEM.search(plano)) != (recurso == ZIPER):
        return None
    codigos = codigos_do(texto)
    if len(codigos) != 1:
        return None
    (modelo,) = codigos
    dito = EQUIPE[recurso].get(modelo)
    if isinstance(dito, Mapping):
        dito = _da_versao(dito, titulo, variacao)
    if recurso == G5 and dito is True and _SO_4G.search(plano) and not _TEM_5G.search(plano):
        return None
    return dito


def pode_oferecer(recurso: str, titulo: Any, variacao: Any = None) -> bool:
    """Este anúncio pode ser indicado como o que tem o recurso? Só com o True da equipe, e nunca o
    de `NUNCA_OFERECER`. PURA."""
    if o_que_a_equipe_diz(recurso, titulo, variacao) is not True:
        return False
    return not codigos_do(" ".join(str(t) for t in (titulo, variacao) if t)) & NUNCA_OFERECER
