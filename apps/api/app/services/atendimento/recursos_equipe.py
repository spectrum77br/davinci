"""O QUE A EQUIPE DIZ sobre NFC, 5G e zíper expansivo (a decisão do dono, 05/11/2026), o que vem na
caixa e os fatos das malas (06/11/2026).

"confie no q a eqpe diz sempre pegue as mensagens sobre esses assuntos da eqpe pra validar". Sobre
um modelo ter ou não o recurso vale a equipe, mesmo contra o título do anúncio e o site da marca.
Quem usa: `especificacoes` (a fonte "equipe", acima de todas) e `agente.checagens` (a oferta de
outro anúncio e a negativa barrada).

`o_que_a_equipe_diz` responde True, False ou None (sem veredito: não muda nada) para o título, mais
o nome da variação quando há. Só com exatamente 1 código de modelo; a entrada por versão só vale
quando o texto diz a versão, e versões de resposta diferente dão None. `fatos_da_equipe` faz o mesmo
com a caixa e a mala: devolve só os itens com veredito. PURO, sem importar o agente.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping
from typing import Any

from app.services.atendimento import condicao_anuncio, sites_marca
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
        "p2": False,
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

# ── A caixa e a mala (06/11/2026) ─────────────────────────────────────────
#
# 870 afirmações da equipe, dois leitores, juiz, consolidador e cético. O formato é o de `EQUIPE`.
# O carregador do S7 ficou sem veredito na conferência: a equipe se divide, com "acompanha apenas
# cabo" em 11/2026 e "vem sem carregador de fábrica, enviamos de brinde".
# De propósito sem veredito: fone e relógio (dependem da loja e da variação), o carregador de C68
# Plus, F105, F110 Pro, F117 e C2, a rodinha de P5 e P6 (conflito). Nada de regra geral: o que não
# está aqui fica como hoje.
CARREGADOR = "carregador"
CAPINHA = "capinha"
CABO = "cabo"
PELICULA = "pelicula"
CHAVE_CHIP = "chave_chip"
VENDE_AVULSO = "vende_acessorio_avulso"
RODAS = "rodas_removiveis"
MATERIAL = "material"
PADRAO_ANAC = "padrao_anac"

CAIXA: dict[str, dict[str, bool | dict[str, bool | None]]] = {
    CARREGADOR: {
        "a17": True,
        "a18": True,
        "wp60": True,
        "f110l": True,
        "c3": True,
        "marine1": True,
        "t8": True,
        "c53": True,
        "c57": {"pro": True},
        "wp35s": True,
    },
    CAPINHA: {
        "a17": True,
        "a18": True,
        "s5": True,
        "s7": True,
        "c68": {"plus": True},
        "redmi15c": True,
        "s3": {"pro": True},
        "wp60": False,
        "f110": {"pro": False},
    },
    CABO: {
        "a17": True,
        "s7": True,
        "c68": {"plus": True},
        "wp60": True,
        "c3": True,
        "f105": True,
        "sparkgo1s": True,
        "f117": True,
        "marine1": True,
        "s5": True,
    },
}
_NAO_NA_SHOPEE_NEM_NO_TIKTOK: dict[str, bool | dict[str, bool | None]] = {
    "a17": False,
    "c68": {"plus": False},
    "wp60": False,
    "a18": False,
    "s7": False,
}
# O que depende da plataforma: fora daqui, a plataforma não tem veredito.
CAIXA_POR_PLATAFORMA: dict[str, dict[str, dict[str, bool | dict[str, bool | None]]]] = {
    PELICULA: {
        "ml": {
            "a17": True,
            "c68": {"plus": True},
            "wp60": True,
            "s7": True,
            "sparkgo1s": True,
            "marine1": True,
            "s5": True,
        },
        "shopee": _NAO_NA_SHOPEE_NEM_NO_TIKTOK,
        "tiktok": _NAO_NA_SHOPEE_NEM_NO_TIKTOK,
    },
    CHAVE_CHIP: {
        "ml": {
            "a17": True,
            "c68": {"plus": True},
            "s7": True,
            "wp60": True,
            "sparkgo1s": True,
            "marine1": True,
            "s5": True,
        },
    },
}
# O M2 depende do tamanho que o texto diz, e o tamanho é o da mala (`_tamanhos_da_mala`).
RODAS_REMOVIVEIS: dict[str, bool | dict[int, bool]] = {
    "p7": True,
    "p8": True,
    "p2": False,
    "p4": False,
    "m2": {18: True, 20: False},
}
POLIPROPILENO = "polipropileno"
MATERIAIS: dict[str, str] = dict.fromkeys(("p1", "p2", "p3", "p4", "p5", "p6", "p7"), POLIPROPILENO)
BORDO = "bordo"
DESPACHO = "despacho"
ANAC_POR_TAMANHO: dict[int, str] = {18: BORDO, 20: BORDO, 24: DESPACHO}
# Os aparelhos que o campo do avulso reconhece: quem está em alguma tabela de celular.
_CELULARES = frozenset(
    {
        *EQUIPE[NFC],
        *EQUIPE[G5],
        *(c for tabela in CAIXA.values() for c in tabela),
        *(
            c
            for por_plataforma in CAIXA_POR_PLATAFORMA.values()
            for tabela in por_plataforma.values()
            for c in tabela
        ),
    }
)


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
    "plus": r"\bplus\b",
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
# mala, a cor numerada ("Preto2") e o "KIT4". O nome do Bling traz o do fornecedor ("Mala Onda P7
# tamanho 20 - Rosa (OM - PPB810 - Pink)"; 06/11/2026: ppb, xs e alob em 1.450 vínculos vivos).
_NAO_CONTA = re.compile(
    r"^(?:g99|g96|g88|g81|t606|t612|t616|t618|t760|t820|u9|ufb\d+|usw\d+|uat\d+|ucm\d+|uaf\d+"
    r"|usg\d+|dt\d+|dtlg\d+[a-z]?|ppdt\d+|ppb\d+|xs\d+|alob\d+|kit\d+|preto\d+|branco\d+"
    r"|azul\d+)$"
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
# Os dois da caixa que `codigos_do` não lê: o nome não tem letra e número juntos. Lê-los lá mudaria
# quem usa `codigos_do` para contar modelos; aqui, só a tabela da caixa os vê.
_SEM_CODIGO_NO_TITULO = (
    (re.compile(r"\bredmi\s+15\s*c\b"), "redmi15c"),
    (re.compile(r"\bspark\s+go\s+1\s*s\b"), "sparkgo1s"),
)
# O Redmi 15C é da tabela: "Xiaomi Redmi 15c" não é a outra marca.
_DA_CAIXA_COM_OUTRA_MARCA = frozenset({"redmi15c"})
# O que `condicao_anuncio` não lê no título e também não é novo. O "AVULSO SALVADO" do cadastro é o
# estoque que voltou (o título "Xiaomi Redmi 15c Verde" não diz); há título com "Avarias Completas".
_NAO_E_NOVO = re.compile(
    r"\bdevolvid[oa]s?\b|\bavari\w+|\bsalvad[oa]s?\b|\bcom\s+defeito\b|\bretirada\s+de\s+pecas\b"
)
# A linha toda: "Uranyx Séries C ... - C53", "Diversos Modelos M3".
_LINHA_TODA = re.compile(r"\bseries\b|\bmodelos\b")
# O acessório à venda, não o aparelho nem a mala: "Capinha Para A17", "Rodinhas Para Malas". O
# "Capa...uranyx A18" e o "Case Uranyx S5" são o celular com o título disfarçado (produção).
_ACESSORIO_A_VENDA = re.compile(
    r"\b(?:capas?|capinhas?|peliculas?|carregador(?:es)?|cabos?|fones?)\s+(?:\S+\s+){0,2}?"
    r"(?:para|pra|p/|compativel)\b|\bpara\s+malas?\b"
)
# A mala na cabeça do título: "Frasqueira Mala De Mão" é a frasqueira, sem rodinha nem ANAC.
_MALA_NA_CABECA = frozenset({"mala", "malas"})
_MOCHILA_NA_CABECA = frozenset({"mochila", "mochilas"})
_KIT = re.compile(
    r"\b(?:kit\d*|conjunto|jogo)\b|\b\d\s*pecas\b|\+|\bfrasqueiras?\b|\bnecessaires?\b"
    r"|\bmochilas?\b"
)
# A polegada escrita em aspas simples ("20''"): `_tamanhos` só conhece a aspa dupla.
_ASPAS_SIMPLES = re.compile(r"''|′′|′")


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


def codigos_de_outro(nome: Any, modelos: Collection[str]) -> set[str]:
    """Os códigos do nome que não são dos `modelos`. O "A18 Pro Max - S5 Edition" do cadastro é o S5
    do título com o nome de venda: o mesmo aparelho (`_S5_DE_VENDA`). PURA."""
    codigos = codigos_do(nome) - set(modelos)
    if "s5" in modelos and _S5_DE_VENDA.search(_limpo(nome)):
        codigos -= _A1X
    return codigos


def nao_casa_com_o_cadastro(titulo: Any, nomes: Iterable[Any]) -> bool:
    """O cadastro ligado ao anúncio não é novo ou é de outro modelo que o do título? Sem o código de
    um dos lados não há o que comparar. A simulação de 06/11/2026: "Mala De Bordo 20 Polegadas 10Kg
    M2" ligada à "Mala Chanfrada M3 tamanho 20" (131 títulos da Amazon), o "Uranyx F109 5g" ligado
    ao F109S, o "Xiaomi Redmi 15c Verde" ligado ao "AVULSO SALVADO". Não se sabe qual dos dois está
    errado: o que não casa com segurança fica sem veredito da caixa e da mala. PURA."""
    do_titulo = codigos_do(titulo)
    for nome in nomes:
        plano = _limpo(nome)
        if False in condicao_anuncio._do_titulo(str(nome or "")).get("novo", ()) or (
            _NAO_E_NOVO.search(plano)
        ):
            return True
        if do_titulo and codigos_de_outro(nome, do_titulo):
            return True
    return False


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


def _do_modelo(
    dito: bool | Mapping[str, bool | None] | None, titulo: Any, variacao: Any
) -> bool | None:
    return _da_versao(dito, titulo, variacao) if isinstance(dito, Mapping) else dito


def _tamanhos_do_texto(texto: Any) -> list[int]:
    """As polegadas do texto. O "P8" e o "M1-M6" do código não são tamanho. PURA."""
    plano = _ASPAS_SIMPLES.sub('"', _plano(str(texto or "")))
    return sites_marca._tamanhos(_MODELO.sub(" ", _FAIXA.sub(" ", plano)))


def _tamanhos_lidos(titulo: Any, variacao: Any) -> list[int]:
    """As polegadas da variação, a mais específica, ou as do título. PURA."""
    return _tamanhos_do_texto(variacao) or _tamanhos_do_texto(titulo)


def _rodas_da_mala(modelo: str | None, titulo: Any, variacao: Any) -> bool | None:
    """O veredito da rodinha removível. No M2 vale o tamanho da mala: a frasqueira do kit (até 14
    polegadas) não conta, e dois tamanhos de mala ou nenhum ficam sem veredito. PURA."""
    dito = RODAS_REMOVIVEIS.get(modelo or "")
    if not isinstance(dito, Mapping):
        return dito
    da_mala = {t for t in _tamanhos_lidos(titulo, variacao) if t > 14}
    return dito.get(da_mala.pop()) if len(da_mala) == 1 else None


def _fatos_da_mala(
    modelo: str | None, plano: str, titulo: Any, variacao: Any
) -> dict[str, bool | str]:
    saida: dict[str, bool | str] = {}
    achada = _BAGAGEM.search(plano)
    cabeca = achada.group(0) if achada else ""
    if cabeca in _MALA_NA_CABECA:
        rodas = _rodas_da_mala(modelo, titulo, variacao)
        if rodas is not None:
            saida[RODAS] = rodas
        tamanhos = _tamanhos_lidos(titulo, variacao)
        if len(tamanhos) == 1 and not _KIT.search(plano) and tamanhos[0] in ANAC_POR_TAMANHO:
            saida[PADRAO_ANAC] = ANAC_POR_TAMANHO[tamanhos[0]]
    if modelo and modelo in MATERIAIS and cabeca not in _MOCHILA_NA_CABECA:
        saida[MATERIAL] = MATERIAIS[modelo]
    return saida


def _fatos_do_celular(
    modelo: str, titulo: Any, variacao: Any, plataforma: str
) -> dict[str, bool | str]:
    saida: dict[str, bool | str] = {}
    for item, tabela in CAIXA.items():
        dito = _do_modelo(tabela.get(modelo), titulo, variacao)
        if dito is not None:
            saida[item] = dito
    for item, por_plataforma in CAIXA_POR_PLATAFORMA.items():
        dito = _do_modelo(por_plataforma.get(plataforma, {}).get(modelo), titulo, variacao)
        if dito is not None:
            saida[item] = dito
    if modelo in _CELULARES:
        saida[VENDE_AVULSO] = False
    return saida


def fatos_da_equipe(
    titulo: Any, variacao: Any = None, plataforma: Any = None
) -> dict[str, bool | str]:
    """O que a equipe diz da caixa ou da mala para o título, a variação e a plataforma. PURA.

    Só os itens com veredito: True (vem, removível), False (não vem, não removível), "polipropileno"
    no material, `BORDO` ou `DESPACHO` no padrão ANAC. Vazio: anúncio de usado, recondicionado,
    seminovo ou devolvido, a linha toda ("Séries"), 2+ códigos, outra marca, acessório à venda. Sem
    código, só o padrão ANAC, que depende do tamanho e não do modelo. `plataforma` = a do catálogo
    ("ml", "shopee", "tiktok"): a película e a chave do chip dependem dela."""
    texto = " ".join(str(t) for t in (titulo, variacao) if t)
    plano = _limpo(texto)
    if (
        not plano
        or False in condicao_anuncio._do_titulo(texto).get("novo", ())
        or _NAO_E_NOVO.search(plano)
        or _LINHA_TODA.search(plano)
        or _ACESSORIO_A_VENDA.search(plano)
    ):
        return {}
    codigos = codigos_do(texto) | {c for rx, c in _SEM_CODIGO_NO_TITULO if rx.search(plano)}
    if len(codigos) > 1 or (_OUTRA_MARCA.search(plano) and not codigos & _DA_CAIXA_COM_OUTRA_MARCA):
        return {}
    modelo = next(iter(codigos), None)
    if _BAGAGEM.search(plano):
        return _fatos_da_mala(modelo, plano, titulo, variacao)
    if modelo is None:
        return {}
    return _fatos_do_celular(modelo, titulo, variacao, str(plataforma or ""))


def pode_oferecer(recurso: str, titulo: Any, variacao: Any = None) -> bool:
    """Este anúncio pode ser indicado como o que tem o recurso? Só com o True da equipe, e nunca o
    de `NUNCA_OFERECER`. PURA."""
    if o_que_a_equipe_diz(recurso, titulo, variacao) is not True:
        return False
    return not codigos_do(" ".join(str(t) for t in (titulo, variacao) if t)) & NUNCA_OFERECER
