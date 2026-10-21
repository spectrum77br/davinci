"""AS ESPECIFICAÇÕES DO PRODUTO por fonte, para o AGENTE (20/10/2026).

Não há tabela de especificação no DaVinci. A decisão 3 do dono (20/10/2026):
"o site às vezes não tem exatamente o que o anúncio tem, por isso é
interessante ver o anúncio do produto que está sendo conversado". A ordem:

  1. "anuncio": o anúncio da conversa — o título atual, os atributos e a
     descrição LIMPA da ficha (`ficha.ficha_do_anuncio`). A ficha de OUTRO
     anúncio do mesmo produto só dá o título (o kit e a descrição variam por
     anúncio);
  2. "davinci": o cadastro — RAM.ROM e 5G pelo NOME dos produtos vinculados
     (`troca_sugestoes._RE_SPEC`/`_RE_5G`, por import) e os fatos aprovados
     de `ficha_respostas.REGRAS_APROVADAS["espec_celular"]` (o celular é
     desbloqueado; RAM física × virtual; Wi-Fi de 5 GHz não é 5G; o Uranyx
     S5 tem NFC) e de `espec_eletro` (a air fryer Uranyx não é bivolt) —
     cada fato só vale enquanto o texto aprovado ainda o diz;
  3. "site": a página do produto no site da marca (`sites_marca`), só para
     o campo que faltar.
Conflito entre fontes: vale a de cima, e o conflito vai em `conflitos` (o
agente não mistura). O anúncio que se CONTRADIZ (título × atributo ×
descrição, ex. "NFC" no título e "NFC: Não" no atributo) marca o campo e
`conflito_no_anuncio=true`: o agente manda para pessoa com
`dado_indisponivel` e o motivo "corrigir anúncio" — e a fonte de baixo não
tapa o buraco. Valor que varia por variação ("12.64 ou 12.128") não é
contradição: os valores vão juntos.

RAM FÍSICA × VIRTUAL: o anúncio costuma somar as duas ("16.128" no A18, cujo
site diz "16 GB de RAM"; o site do F110 Pro 5G diz "20 GB de RAM (8 GB + até
12 GB de RAM expansível)"). O campo "ram" é o número como a fonte escreve;
"ram_fisica" e "ram_virtual" só quando alguma fonte separa — e só da fonte
cuja RAM bate com a escolhida. 16 contra 8 com "virtual"/"expansível" no
anúncio não é conflito (é a soma); sem essa palavra, é.

Nunca condição, originalidade, resistência ou garantia (a regra de segurança
8: só pelo título): a linha do site passa pela mesma limpeza da descrição da
ficha. SÓ LEITURA.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Product, ProductLink
from app.models.enums import IntegrationPlatform
from app.services.atendimento import ficha as ficha_svc
from app.services.atendimento import sites_marca
from app.services.atendimento.ficha_respostas import REGRAS_APROVADAS
from app.services.atendimento.troca_sugestoes import _RE_5G, _RE_SPEC
from app.services.atendimento.validador import plano_de

logger = structlog.get_logger()

FONTE_ANUNCIO = "anuncio"
FONTE_DAVINCI = "davinci"
FONTE_SITE = "site"

# Os campos, na ordem em que vão ao agente.
CAMPOS = (
    "ram",
    "ram_fisica",
    "ram_virtual",
    "armazenamento",
    "rede_5g",
    "wifi_5ghz",
    "nfc",
    "chip",
    "desbloqueado",
    "sistema",
    "processador",
    "bateria",
    "tela",
    "dimensoes",
    "peso",
    "voltagem",
    "potencia",
    "capacidade",
    "tamanho",
    "material",
    "carga_maxima",
)
MAX_PRODUTOS = 24
MAX_CHARS_VALOR = 120

SIM = "sim"
NAO = "não"

# Os fatos aprovados de `espec_celular` que viram campo: o trecho que o texto
# aprovado tem de conter (mudou o texto, o fato deixa de valer aqui).
_FATO_DESBLOQUEADO = "o celular é desbloqueado"
_FATO_S5_NFC = "o Uranyx S5 tem NFC"
_FATO_WIFI = "Wi-Fi de 5 GHz não é rede móvel 5G"
_FATO_RAM = "separar as duas (a virtual usa parte do armazenamento)"
# E o de `espec_eletro` (o mesmo fato semeado em `atendimento_fatos`).
_FATO_AIR_FRYER = "a air fryer Uranyx não é bivolt"

NOTA_WIFI = "Wi-Fi de 5 GHz não é rede móvel 5G"
NOTA_RAM_SEM_SEPARAR = "a fonte não diz se inclui RAM virtual"
NOTA_VARIA = "varia conforme a variação"


def _aprovado(trecho: str) -> bool:
    return trecho in (REGRAS_APROVADAS.get("espec_celular") or "")


# ── Achados (puros) ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class Achado:
    """Um valor de um campo numa fonte. `chaves` = a forma comparável."""

    campo: str
    valor: str
    chaves: frozenset[str]
    nota: str | None = None
    # RAM: (física, total) quando a fonte separa; o resto fica None.
    ram_fisica: int | None = None
    ram_virtual: str | None = None


def _plano(texto: Any) -> str:
    return " ".join(plano_de(str(texto or "")).split())


def _num(texto: str) -> float:
    return float(texto.replace(".", "").replace(",", ".")) if "," in texto else float(texto)


def _gb(n: int) -> str:
    return f"{n} GB"


# "8 GB + 8 GB virtual", "8 GB de RAM física + até 8 GB virtual" e o plural
# ("8 GB de RAM físicos + 8 GB virtuais", 20/10/2026).
_RAM_SOMA = re.compile(
    r"(\d{1,2})\s*gb\s*(?:de\s+)?(?:ram\s*)?(?:fisic[oa]s?\s*)?\+\s*(?:ate\s+)?(\d{1,2})\s*gb\s*"
    r"(?:de\s+)?(?:ram\s+)?(?:virtua(?:l|is)|expansive(?:l|is)|estendidas?|expandidas?|extras?)"
)
_RAM_TOTAL_PARENTESE = re.compile(
    r"(\d{1,2})\s*gb\s*(?:de\s+)?ram\s*\(\s*(\d{1,2})\s*gb\s*\+\s*(?:ate\s+)?(\d{1,2})\s*gb"
)
_RAM_EXPANSIVEL = re.compile(
    r"(\d{1,2})\s*gb\s*(?:de\s+)?ram\s*\(?\s*(?:expansivel|expandivel)\s+(?:ate\s+)?(\d{1,2})\s*gb"
)
_RAM_SIMPLES = re.compile(
    r"(?<![\d.,/+])(\d{1,2})\s*gb\s*(?:de\s+)?(?:memoria\s+)?ram\b|\bram\s*:?\s*(\d{1,2})\s*gb\b"
)
# "16.128", "8/128", "12+256", "8gb/128gb" (o título do anúncio). A RAM só
# até 48 GB (a maior em produção, 20/10/2026: "48.512") e nunca o cartão
# depois: "64 + 64 Sd Card" e "64/128 Gb Memoria" eram 64 GB de RAM em 11
# anúncios vivos (revisão de 20/10/2026) — e a checagem barrava o valor certo.
_RAM_ROM = re.compile(
    r"(?<![\d.,])([1-9]|[1-3]\d|4[0-8])\s*(?:gb)?\s*[./+]\s*(32|64|128|256|512)(?:\s*gb)?"
    r"(?![\d.,])(?!\s*(?:gb\s*)?(?:de\s+)?(?:sd\b|micro\s*sd\b|cartao|card\b))"
)
# Duas ou mais capacidades de armazenamento ("64/128 Gb Memoria", "64,128,256 e
# 512 GB Memoria"): cada uma é uma variação (NOTA_VARIA no `_unir`).
_ROM_LISTA = re.compile(
    r"(?<![\d.,/+])((?:64|128|256|512)(?:\s*gb)?"
    r"(?:\s*(?:,|/|\be\b|\bou\b)\s*(?:64|128|256|512|1024)(?:\s*gb)?)+)"
    r"\s*(?:de\s+)?(?:memoria(?:\s+interna)?|armazenamento|rom)\b(?!\s*ram)"
)
# "64 + 64 Sd Card", "64 GB + 128 cartão": o armazenamento e o cartão, sem RAM.
_ROM_MAIS_CARTAO = re.compile(
    r"(?<![\d.,/+])(\d{2,3})\s*(?:gb)?\s*\+\s*\d{2,4}\s*(?:gb)?\s*(?:de\s+)?"
    r"(?:sd\b|micro\s*sd\b|cartao|card\b)"
)
_ROM = re.compile(
    r"(?<![\d.,/+])(\d{2,3}|1|2)\s*(gb|tb)\s*(?:de\s+)?(?:memoria(?:\s+interna)?|armazenamento|rom)\b"
    r"(?!\s*ram)|\b(?:armazenamento|memoria\s+interna|rom)\s*(?:interno)?\s*:?\s*(\d{2,3}|1|2)\s*(gb|tb)\b"
)
_VIRTUAL = re.compile(r"\b(?:virtual|virtualmente|expansivel|expandivel|estendida|expandida)\b")
_NFC_NAO = re.compile(
    r"\b(?:sem|nao\s+(?:tem|possui|vem\s+com|suporta|conta\s+com|acompanha))\s+"
    r"(?:(?:o|a)\s+)?(?:(?:recurso|funcao|tecnologia)\s+(?:de\s+)?)?nfc\b"
    r"|\bnfc\s*[-:=]?\s*(?:nao|no)\b"
)
_NFC = re.compile(r"\bnfc\b")
_WIFI_5 = re.compile(r"\bwi\s*-?\s*fi\s*(?:de\s+|dual\s+band\s+)?5\s*(?:g|ghz)\b|\b5\s*ghz\b")
_5G_NAO = re.compile(
    r"\b(?:sem|nao\s+(?:e|tem|possui|suporta|pega|funciona\s+(?:no|com|em)))\s+(?:(?:rede|o|a)\s+)?5g\b"
    r"|\b5g\s*[-:=]?\s*(?:nao|no)\b"
)
_5G = re.compile(r"\b5g\b")
_SISTEMA = re.compile(r"\b(android|ios|harmonyos)\s*(\d{1,2})?\b")
_CHIP_DUAL = re.compile(r"\bdual\s*-?\s*(?:sim|chip)\b|\b(?:2|dois)\s*chips?\b")
_CHIP_UM = re.compile(r"\b(?:1|um|unico)\s*chip\b|\bsingle\s*sim\b")
_ESIM = re.compile(r"\besim\b")
_BATERIA = re.compile(r"(?<![\d.,])(\d{1,2}[.,]?\d{3})\s*mah\b")
# A polegada pela unidade ou pelo tipo da tela logo depois ("Tela 6.9 HD+",
# a descrição da Kia, 20/10/2026).
_TELA = re.compile(
    r"\b(?:tela|display|screen)\b(?![^\n]{0,12}\b(?:traseira|secundaria|externa))[^\n]{0,30}?"
    r"(?<![\d.,])(\d{1,2}[.,]\d{1,3})\s*(?:\"|”|″|''|polegadas|pol\b"
    r"|(?=(?:full\s*)?(?:hd|fhd|qhd|amoled|oled|lcd|ips|tft)\b))"
)
_CHIPSET = re.compile(
    r"\b(helio|dimensity|unisoc|snapdragon|exynos|tiger|mediatek|bionic|a\d{2}\s+bionic)\s*"
    r"([a-z]?\d{2,4}[a-z]?)\b"
)
_VOLT = re.compile(r"\b(110|127|220)\s*v\b")
_BIVOLT_NAO = re.compile(r"\bnao\s+(?:e\s+)?bivolt\b")
_BIVOLT = re.compile(r"\bbivolt\b")
_POTENCIA = re.compile(r"(?<![\d.,])(\d{3,4})\s*w\b")
_LITROS = re.compile(r"(?<![\d.,])(\d{1,2}(?:[.,]\d)?)\s*(?:l|litros)\b")
_KG = re.compile(r"(?<![\d.,])(\d{1,2}(?:[.,]\d{1,3})?)\s*kg\b")
_GRAMAS = re.compile(r"(?<![\d.,])(\d{1,4}(?:[.,]\d{1,3})?)\s*(kg|g)\b")
_NUMEROS = re.compile(r"\d+(?:[.,]\d+)?")


def _a(campo: str, valor: str, chaves: Iterable[Any], **extra: Any) -> Achado:
    return Achado(
        campo,
        valor[:MAX_CHARS_VALOR],
        frozenset(str(c) for c in chaves),
        **extra,
    )


def _ram(plano: str) -> list[Achado]:
    saida: list[Achado] = []
    for m in _RAM_TOTAL_PARENTESE.finditer(plano):
        total, fisica, virtual = int(m[1]), int(m[2]), int(m[3])
        saida.append(
            _a(
                "ram",
                _gb(total),
                {total, fisica},
                ram_fisica=fisica,
                ram_virtual=f"até {_gb(virtual)}",
            )
        )
    if not saida:
        for m in _RAM_SOMA.finditer(plano):
            fisica, virtual = int(m[1]), int(m[2])
            total = fisica + virtual
            saida.append(
                _a("ram", _gb(total), {total, fisica}, ram_fisica=fisica, ram_virtual=_gb(virtual))
            )
    if not saida:
        for m in _RAM_EXPANSIVEL.finditer(plano):
            fisica = int(m[1])
            saida.append(
                _a(
                    "ram",
                    _gb(fisica),
                    {fisica},
                    ram_fisica=fisica,
                    ram_virtual=f"expansível até {_gb(int(m[2]))}",
                )
            )
    if not saida:
        for m in _RAM_SIMPLES.finditer(plano):
            n = int(m[1] or m[2])
            saida.append(_a("ram", _gb(n), {n}))
    return saida


def _rom(plano: str) -> list[Achado]:
    saida: list[Achado] = []
    ocupados: list[tuple[int, int]] = []
    for m in _ROM_LISTA.finditer(plano):
        for n in re.findall(r"\d+", m[1]):
            saida.append(_a("armazenamento", _gb(int(n)), {int(n)}))
        ocupados.append(m.span())
    for m in _ROM_MAIS_CARTAO.finditer(plano):
        saida.append(_a("armazenamento", _gb(int(m[1])), {int(m[1])}))
        ocupados.append(m.span())
    for m in _ROM.finditer(plano):
        if any(inicio <= m.start() < fim for inicio, fim in ocupados):
            continue
        n, unidade = (m[1], m[2]) if m[1] else (m[3], m[4])
        gb = int(n) * (1024 if unidade == "tb" else 1)
        saida.append(_a("armazenamento", f"{n} {unidade.upper()}", {gb}))
    return saida


def achados_do_texto(texto: Any, *, tela_sem_rotulo: bool = False) -> list[Achado]:
    """Os campos que um texto diz (título, linha de descrição, linha do site). PURA.

    `tela_sem_rotulo` = o texto já é da tela (o atributo/medida "Tela").
    """
    plano = _plano(texto)
    if not plano:
        return []
    saida: list[Achado] = []
    ram_rom = list(_RAM_ROM.finditer(plano))
    saida.extend(_ram(plano))
    if not any(a.campo == "ram" for a in saida):
        for m in ram_rom:
            saida.append(_a("ram", _gb(int(m[1])), {int(m[1])}))
    saida.extend(_rom(plano))
    if not any(a.campo == "armazenamento" for a in saida):
        for m in ram_rom:
            saida.append(_a("armazenamento", _gb(int(m[2])), {int(m[2])}))
    if _NFC_NAO.search(plano):
        saida.append(_a("nfc", NAO, {NAO}))
    elif _NFC.search(plano):
        saida.append(_a("nfc", SIM, {SIM}))
    sem_wifi = _WIFI_5.sub(" ", plano)
    if _WIFI_5.search(plano):
        saida.append(_a("wifi_5ghz", SIM, {SIM}, nota=NOTA_WIFI if _aprovado(_FATO_WIFI) else None))
    if _5G_NAO.search(sem_wifi):
        saida.append(_a("rede_5g", NAO, {NAO}))
    elif _5G.search(sem_wifi):
        saida.append(_a("rede_5g", SIM, {SIM}))
    for m in _SISTEMA.finditer(plano):
        nome = {"android": "Android", "ios": "iOS", "harmonyos": "HarmonyOS"}[m[1]]
        valor = f"{nome} {m[2]}" if m[2] else nome
        saida.append(_a("sistema", valor, {_plano(valor)}))
    if _ESIM.search(plano):
        saida.append(_a("chip", "eSIM", {"esim"}))
    if _CHIP_DUAL.search(plano):
        saida.append(_a("chip", "dual chip", {"dual"}))
    elif _CHIP_UM.search(plano):
        saida.append(_a("chip", "1 chip", {"um"}))
    for m in _CHIPSET.finditer(plano):
        saida.append(_a("processador", f"{m[1].title()} {m[2].upper()}", {f"{m[1]} {m[2]}"}))
    for m in _BATERIA.finditer(plano):
        mah = int(m[1].replace(".", "").replace(",", ""))
        saida.append(_a("bateria", f"{mah:,} mAh".replace(",", "."), {mah}))
    if tela_sem_rotulo:
        if m := re.search(r"(\d{1,2}[.,]\d{1,3})\s*(?:\"|”|″|''|polegadas|pol\b)?", plano):
            pol = _num(m[1].replace(".", ","))
            saida.append(_a("tela", f'{str(pol).replace(".", ",")}"', {pol}))
    else:
        for m in _TELA.finditer(plano):
            pol = _num(m[1].replace(".", ","))
            saida.append(_a("tela", f'{str(pol).replace(".", ",")}"', {pol}))
    if re.search(r"\b(?:capacidade|air\s*fryer|airfryer|fritadeira|cesto|panela)\b", plano):
        litros = sorted({_num(m[1].replace(".", ",")) for m in _LITROS.finditer(plano)})
        if litros:
            valor = " ou ".join(f"{str(v).replace('.', ',').removesuffix(',0')} L" for v in litros)
            saida.append(_a("capacidade", valor, litros))
    # Bivolt × uma voltagem só: "110 V" bate com "não é bivolt", não com "bivolt".
    volts = sorted({m[1] for m in _VOLT.finditer(plano)})
    if _BIVOLT_NAO.search(plano):
        valor = (
            " ou ".join(f"{v} V" for v in volts) + " (não é bivolt)" if volts else "não é bivolt"
        )
        saida.append(_a("voltagem", valor, {"nao bivolt", *volts}))
    elif _BIVOLT.search(plano):
        saida.append(_a("voltagem", "bivolt", {"bivolt"}))
    elif volts:
        saida.append(_a("voltagem", " ou ".join(f"{v} V" for v in volts), {"nao bivolt", *volts}))
    return saida


# O atributo (ou a medida do site) pelo NOME → o campo.
_PELO_NOME: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bram\b|memoria ram"), "ram"),
    (re.compile(r"armazenamento|memoria interna|\brom\b|capacidade de memoria"), "armazenamento"),
    (re.compile(r"\bnfc\b"), "nfc"),
    (re.compile(r"sistema operacional|operating system|^sistema$|^os$"), "sistema"),
    (re.compile(r"processador|chipset|processor|\bcpu\b"), "processador"),
    (re.compile(r"bateria|battery"), "bateria"),
    (re.compile(r"^tela$|tamanho da tela|screen size|display size"), "tela"),
    (re.compile(r"dimens|medidas"), "dimensoes"),
    (re.compile(r"^peso|weight"), "peso"),
    (re.compile(r"voltagem|tensao|voltage"), "voltagem"),
    (re.compile(r"potencia|\bpower\b"), "potencia"),
    (re.compile(r"^capacidade$|capacidade \(l\)|volume"), "capacidade"),
    (re.compile(r"^tamanho$|tamanho da mala"), "tamanho"),
    (re.compile(r"^material|material externo|material do casco"), "material"),
    (re.compile(r"carga maxima|capacidade de carga|suporta ate"), "carga_maxima"),
    (re.compile(r"chip|\bsim\b"), "chip"),
)


def _sim_nao(valor: str) -> str | None:
    p = _plano(valor)
    if re.match(r"^(?:sim|yes|possui|tem|com)\b", p):
        return SIM
    if re.match(r"^(?:nao|no|sem)\b", p):
        return NAO
    return None


def achados_do_atributo(nome: Any, valor: Any) -> list[Achado]:
    """Os campos de um atributo do anúncio ou de uma medida do site ("Peso: 218 g"). PURA."""
    rotulo = _plano(nome)
    texto = " ".join(str(valor or "").split())
    if not rotulo or not texto:
        return []
    campo = next((c for rx, c in _PELO_NOME if rx.search(rotulo)), None)
    plano = _plano(texto)
    if campo is None:
        if re.search(r"\b(?:rede|network|conectividade|tecnologia)\b", rotulo) and re.search(
            r"\b[2-5]g\b|lte", plano
        ):
            if _5G.search(plano):
                return [_a("rede_5g", SIM, {SIM})]
            return [_a("rede_5g", NAO, {NAO}, nota=f"o anúncio diz {texto[:40]}")]
        return achados_do_texto(f"{nome} {texto}")
    if campo == "nfc":
        if sn := _sim_nao(texto):
            return [_a("nfc", sn, {sn})]
        return [a for a in achados_do_texto(f"nfc {texto}") if a.campo == "nfc"]
    if campo == "ram":
        return _ram(f"{plano} ram" if "ram" not in plano else plano) or (
            [_a("ram", _gb(int(m[1])), {int(m[1])})]
            if (m := re.search(r"(\d{1,2})\s*gb", plano))
            else []
        )
    if campo == "armazenamento":
        if m := re.search(r"(\d{1,4})\s*(gb|tb)", plano):
            gb = int(m[1]) * (1024 if m[2] == "tb" else 1)
            return [_a("armazenamento", f"{m[1]} {m[2].upper()}", {gb})]
        return []
    if campo == "tela":
        return achados_do_texto(texto, tela_sem_rotulo=True)
    if campo == "chip":
        sn = _sim_nao(texto)
        if "dual" in rotulo and sn:
            return [
                _a("chip", "dual chip" if sn == SIM else "1 chip", {"dual" if sn == SIM else "um"})
            ]
        if m := re.search(r"\b([12])\b", plano):
            return [
                _a(
                    "chip",
                    "dual chip" if m[1] == "2" else "1 chip",
                    {"dual" if m[1] == "2" else "um"},
                )
            ]
        return [a for a in achados_do_texto(texto) if a.campo == "chip"]
    if campo in ("dimensoes", "peso", "potencia", "capacidade", "carga_maxima", "processador"):
        chaves: set[Any] = set()
        if campo == "peso":
            if m := _GRAMAS.search(plano):
                chaves = {round(_num(m[1].replace(".", ",")) * (1000 if m[2] == "kg" else 1))}
        elif campo == "carga_maxima":
            chaves = {round(_num(m[1].replace(".", ",")), 1) for m in _KG.finditer(plano)}
        elif campo == "potencia":
            chaves = {m[1] for m in _POTENCIA.finditer(plano)}
        elif campo == "capacidade":
            chaves = {_num(m[1].replace(".", ",")) for m in _LITROS.finditer(plano)}
        elif campo == "processador":
            chaves = {f"{m[1]} {m[2]}" for m in _CHIPSET.finditer(plano)}
        else:
            chaves = {_num(n.replace(".", ",")) for n in _NUMEROS.findall(plano)}
        return [_a(campo, texto, chaves)]
    if campo == "tamanho":
        pol = sites_marca._tamanhos(texto if re.search(r"pol|\"", plano) else f"{texto} polegadas")
        return [_a("tamanho", texto, pol)] if pol else []
    if campo == "material":
        if m := sites_marca._material(texto):
            return [_a("material", texto, {m})]
        return [_a("material", texto, {plano})]
    if campo == "voltagem":
        return [a for a in achados_do_texto(texto) if a.campo == "voltagem"]
    if campo == "bateria":
        return [a for a in achados_do_texto(texto) if a.campo == "bateria"] or (
            [_a("bateria", texto, {int(m[1])})] if (m := re.search(r"(\d{4,5})", plano)) else []
        )
    if campo == "sistema":
        return [a for a in achados_do_texto(texto) if a.campo == "sistema"] or [
            _a("sistema", texto, {plano})
        ]
    return []


# ── Juntar e comparar (puros) ─────────────────────────────────────────────


def _compativeis(a: Sequence[Achado], b: Sequence[Achado], *, virtual: bool = False) -> bool:
    """Dois conjuntos de achados do mesmo campo dizem a mesma coisa? (algum valor em comum)"""
    ca = frozenset().union(*(x.chaves for x in a)) if a else frozenset()
    cb = frozenset().union(*(x.chaves for x in b)) if b else frozenset()
    if not ca or not cb or ca & cb:
        return True
    if virtual and a and a[0].campo == "ram":
        # 16 × 8 com "virtual"/"expansível" no anúncio: a soma, não contradição.
        try:
            na, nb = max(int(c) for c in ca), max(int(c) for c in cb)
        except ValueError:
            return False
        maior, menor = max(na, nb), min(na, nb)
        return menor > 0 and maior <= 3 * menor
    return False


def _unir(achados: Sequence[Achado]) -> Achado:
    """Os valores de UMA parte do anúncio para um campo: variações, não contradição
    ("12.64 ou 12.128" → "64 GB ou 128 GB")."""
    primeiro = achados[0]
    valores = list(dict.fromkeys(a.valor for a in achados))
    # A RAM física/virtual só quando a parte diz UMA separação: duas diferentes
    # ("8 + 12" de um modelo e "4 + 4" do outro, revisão de 20/10/2026) não
    # viram a do primeiro.
    separadas = {(a.ram_fisica, a.ram_virtual) for a in achados if a.ram_fisica is not None}
    separada = separadas.pop() if len(separadas) == 1 else (None, None)
    return Achado(
        primeiro.campo,
        " ou ".join(valores)[:MAX_CHARS_VALOR],
        frozenset().union(*(a.chaves for a in achados)),
        NOTA_VARIA if len(valores) > 1 else primeiro.nota,
        separada[0],
        separada[1],
    )


def _contraditorios(achados: Sequence[Achado], *, virtual: bool) -> bool:
    """O campo se contradiz no anúncio? Entre as partes (título × atributo ×
    descrição), ou "sim" e "não" na mesma parte."""
    if any({SIM, NAO} <= a.chaves for a in achados):
        return True
    for i, x in enumerate(achados):
        for y in achados[i + 1 :]:
            if not _compativeis([x], [y], virtual=virtual):
                return True
    return False


def _valor(achados: Sequence[Achado]) -> tuple[str, str | None]:
    valores = list(dict.fromkeys(a.valor for a in achados))
    if len(valores) == 1:
        return valores[0], achados[0].nota
    return " ou ".join(valores), NOTA_VARIA


def juntar(
    do_anuncio: Mapping[str, Sequence[Sequence[Achado]]],
    do_davinci: Mapping[str, Sequence[Achado]],
    do_site: Mapping[str, Sequence[Achado]],
    *,
    virtual_no_anuncio: bool = False,
) -> dict:
    """A precedência anúncio > DaVinci > site, campo a campo. PURA.

    `do_anuncio[campo]` = os achados de cada PARTE do anúncio (título,
    atributos, descrição), na ordem de preferência; as outras fontes, uma
    lista só. Ver o topo.
    """
    campos: list[dict] = []
    conflitos: list[dict] = []
    no_anuncio = False
    escolhida_ram: list[Achado] = []
    for campo in CAMPOS:
        if campo in ("ram_fisica", "ram_virtual"):
            continue
        partes = [list(p) for p in do_anuncio.get(campo, ()) if p]
        todos = [a for p in partes for a in p]
        if todos:
            if _contraditorios(todos, virtual=virtual_no_anuncio):
                valores = list(dict.fromkeys(a.valor for a in todos))
                campos.append(
                    {
                        "campo": campo,
                        "valor": " / ".join(valores),
                        "fonte": FONTE_ANUNCIO,
                        "conflito_no_anuncio": True,
                    }
                )
                no_anuncio = True
                continue
            escolhidos, fonte = partes[0], FONTE_ANUNCIO
            outras = (
                (FONTE_DAVINCI, do_davinci.get(campo) or []),
                (FONTE_SITE, do_site.get(campo) or []),
            )
        elif do_davinci.get(campo):
            escolhidos, fonte = list(do_davinci[campo]), FONTE_DAVINCI
            outras = ((FONTE_SITE, do_site.get(campo) or []),)
        elif do_site.get(campo):
            escolhidos, fonte = list(do_site[campo]), FONTE_SITE
            outras = ()
        else:
            continue
        valor, nota = _valor(escolhidos)
        linha: dict[str, Any] = {"campo": campo, "valor": valor, "fonte": fonte}
        if nota:
            linha["nota"] = nota
        campos.append(linha)
        if campo == "ram":
            escolhida_ram = escolhidos
        for outra_fonte, outros in outras:
            if outros and not _compativeis(escolhidos, outros, virtual=virtual_no_anuncio):
                conflitos.append(
                    {
                        "campo": campo,
                        "valor": valor,
                        "fonte": fonte,
                        "outro_valor": _valor(outros)[0],
                        "outra_fonte": outra_fonte,
                    }
                )
    _ram_separada(campos, escolhida_ram, do_anuncio, do_davinci, do_site)
    return {"campos": campos, "conflitos": conflitos, "conflito_no_anuncio": no_anuncio}


def _ram_separada(
    campos: list[dict],
    escolhida: Sequence[Achado],
    do_anuncio: Mapping[str, Sequence[Sequence[Achado]]],
    do_davinci: Mapping[str, Sequence[Achado]],
    do_site: Mapping[str, Sequence[Achado]],
) -> None:
    """ "ram_fisica"/"ram_virtual" da 1ª fonte que separa E cuja RAM bate com a escolhida."""
    ram = next((c for c in campos if c["campo"] == "ram"), None)
    if ram is None or ram.get("conflito_no_anuncio"):
        return
    fontes = (
        (FONTE_ANUNCIO, [a for p in do_anuncio.get("ram", ()) for a in p]),
        (FONTE_DAVINCI, list(do_davinci.get("ram") or [])),
        (FONTE_SITE, list(do_site.get("ram") or [])),
    )
    for fonte, achados in fontes:
        for a in achados:
            if a.ram_fisica is None or not _compativeis(escolhida, [a]):
                continue
            posicao = campos.index(ram) + 1
            campos.insert(
                posicao, {"campo": "ram_fisica", "valor": _gb(a.ram_fisica), "fonte": fonte}
            )
            if a.ram_virtual:
                campos.insert(
                    posicao + 1, {"campo": "ram_virtual", "valor": a.ram_virtual, "fonte": fonte}
                )
            return
    if "nota" not in ram and _aprovado(_FATO_RAM):
        ram["nota"] = NOTA_RAM_SEM_SEPARAR


def _por_campo(achados: Iterable[Achado]) -> dict[str, list[Achado]]:
    saida: dict[str, list[Achado]] = {}
    for a in achados:
        saida.setdefault(a.campo, []).append(a)
    return saida


# ── As fontes ─────────────────────────────────────────────────────────────


def do_anuncio(
    ficha: Mapping[str, Any] | None, titulo: str | None
) -> dict[str, list[list[Achado]]]:
    """Os achados do anúncio por campo, por parte (título, atributos, descrição). PURA.

    A ficha de OUTRO anúncio do mesmo produto (`ficha.FONTE_OUTRO_ANUNCIO`) só
    dá o título atual.
    """
    ficha = ficha if isinstance(ficha, Mapping) else {}
    titulo_atual = titulo or ficha.get("titulo")
    outro = str(ficha.get("fonte") or "").startswith(ficha_svc.FONTE_OUTRO_ANUNCIO)
    partes: list[list[Achado]] = [achados_do_texto(titulo_atual)]
    if not outro:
        atributos: list[Achado] = []
        for linha in ficha.get("atributos") or []:
            if isinstance(linha, str) and ":" in linha:
                nome, valor = linha.split(":", 1)
                atributos.extend(achados_do_atributo(nome, valor))
        partes.append(atributos)
        descricao: list[Achado] = []
        for linha in str(ficha.get("descricao") or "").split("\n"):
            descricao.extend(achados_do_texto(linha))
        partes.append(descricao)
    saida: dict[str, list[list[Achado]]] = {}
    for parte in partes:
        for campo, lista in _por_campo(parte).items():
            saida.setdefault(campo, []).append([_unir(lista)])
    return saida


def _virtual_no_anuncio(ficha: Mapping[str, Any] | None, titulo: str | None) -> bool:
    ficha = ficha if isinstance(ficha, Mapping) else {}
    textos = [titulo or "", str(ficha.get("descricao") or ""), *(ficha.get("atributos") or [])]
    return bool(_VIRTUAL.search(_plano(" ".join(str(t) for t in textos))))


_ROMS = frozenset({16, 32, 64, 128, 256, 512, 1024})
_CELULAR = re.compile(r"\b(?:celular|smartphone|iphone|telefone)\b")
_SKU_CELULAR = re.compile(r"^dg\d", re.IGNORECASE)


def do_cadastro(
    nomes: Sequence[str | None],
    skus: Sequence[str | None],
    titulo: str | None,
    marca: str | None,
) -> dict[str, list[Achado]]:
    """Os achados do cadastro do DaVinci (nomes dos produtos + fatos aprovados). PURA."""
    achados: list[Achado] = []
    rams: dict[int, None] = {}
    roms: dict[int, None] = {}
    com_5g: list[bool] = []
    for nome in nomes:
        if not nome:
            continue
        if m := _RE_SPEC.search(nome):
            numeros = [int(n) for n in re.findall(r"\d+", m.group())]
            # RAM.ROM de verdade ("16.128"), não "2.5 L" da air fryer.
            if len(numeros) >= 2 and numeros[0] <= 64 and numeros[1] in _ROMS:
                rams[numeros[0]] = None
                roms[numeros[1]] = None
                com_5g.append(bool(_RE_5G.search(nome)))
                continue
        if _RE_5G.search(nome):
            com_5g.append(True)
    # O 5G só quando TODOS os celulares do anúncio dizem (revisão de 20/10/2026:
    # "F109 5G" e "F109S" no mesmo anúncio não fazem o F109S 5G).
    g5 = bool(com_5g) and all(com_5g)
    if rams:
        achados.append(
            _a(
                "ram",
                " ou ".join(_gb(n) for n in rams),
                rams,
                nota=NOTA_VARIA if len(rams) > 1 else None,
            )
        )
    if roms:
        achados.append(
            _a(
                "armazenamento",
                " ou ".join(_gb(n) for n in roms),
                roms,
                nota=NOTA_VARIA if len(roms) > 1 else None,
            )
        )
    if g5:
        achados.append(_a("rede_5g", SIM, {SIM}))
    texto = _plano(" ".join([titulo or "", *(n or "" for n in nomes)]))
    celular = (
        bool(_CELULAR.search(texto))
        or any(s and _SKU_CELULAR.match(s.strip()) for s in skus)
        or bool(rams)
    )
    if celular and _aprovado(_FATO_DESBLOQUEADO):
        achados.append(_a("desbloqueado", SIM, {SIM}, nota="não vem preso a operadora"))
    palavras = set(texto.split())
    if marca == sites_marca.URANYX and "s5" in palavras and _aprovado(_FATO_S5_NFC):
        achados.append(_a("nfc", SIM, {SIM}))
    if (
        marca == sites_marca.URANYX
        and re.search(r"\bair\s*fryer\b|\bairfryer\b|\bfritadeira\b", texto)
        and not re.search(r"\b(?:cesto|tigela|forma|papel)\b", texto)
        and _FATO_AIR_FRYER in (REGRAS_APROVADAS.get("espec_eletro") or "")
    ):
        achados.append(_a("voltagem", "não é bivolt", {"nao bivolt", "110", "127", "220"}))
    return _por_campo(achados)


def _das_opcoes(opcoes: str) -> list[Achado]:
    """As versões que as opções da página listam ("110 V, 220 V", "2,5 L, 4 L, 6 L")."""
    plano = _plano(opcoes)
    saida: list[Achado] = []
    volts = sorted({m[1] for m in _VOLT.finditer(plano)})
    if len(volts) > 1:
        saida.append(
            _a(
                "voltagem",
                " ou ".join(f"{v} V" for v in volts),
                {"nao bivolt", *volts},
                nota=NOTA_VARIA,
            )
        )
    litros = sorted({_num(m[1].replace(".", ",")) for m in _LITROS.finditer(plano)})
    if len(litros) > 1:
        valor = " ou ".join(f"{str(v).replace('.', ',').removesuffix(',0')} L" for v in litros)
        saida.append(_a("capacidade", valor, litros, nota=NOTA_VARIA))
    return saida


def do_site(specs: Sequence[Mapping[str, Any]] | None) -> dict[str, list[Achado]]:
    """Os achados de uma página do site (`atendimento_site_produtos.specs`). PURA.

    A linha que a ficha não aceitaria (garantia, resistência, condição,
    preço, contato) fica de fora; aviso, categoria e linha não viram campo.
    As opções da página com várias voltagens ou capacidades (a air fryer de
    2,5, 4 e 6 L) valem no lugar da medida de UMA versão.
    """
    achados: list[Achado] = []
    versoes: list[Achado] = []
    for s in specs or []:
        if not isinstance(s, Mapping):
            continue
        rotulo = str(s.get("rotulo") or "")
        valor = str(s.get("valor") or "")
        if rotulo == sites_marca.ROTULO_OPCOES:
            versoes.extend(_das_opcoes(valor))
            continue
        if not valor or rotulo in (
            sites_marca.ROTULO_AVISO,
            sites_marca.ROTULO_CATEGORIA,
            sites_marca.ROTULO_LINHA,
        ):
            continue
        if rotulo == sites_marca.ROTULO_DESCRICAO:
            linhas = [f for f in re.split(r"(?<=[.!?])\s+|\n", valor) if ficha_svc._frase_serve(f)]
            for linha in linhas:
                achados.extend(achados_do_texto(linha))
            continue
        if not ficha_svc._frase_serve(f"{rotulo} {valor}"):
            continue
        if rotulo == sites_marca.ROTULO_CARACTERISTICA:
            achados.extend(achados_do_texto(valor))
        else:
            achados.extend(achados_do_atributo(rotulo, valor))
    campos_das_versoes = {a.campo for a in versoes}
    return _por_campo([*(a for a in achados if a.campo not in campos_das_versoes), *versoes])


# ── Os modelos do anúncio (puros; revisão de 20/10/2026) ───────────────────
#
# 24% dos anúncios Uranyx da Shopee que casam com uma página do site ligam
# produtos de DOIS modelos ("F109 5G 24.256 ou F109S 24.256", "F110 Pro 5G ou
# F110L", "A18 S5 + C17"): o título e a descrição falam dos dois, o site casava
# um só, e o 5G, o processador e o sistema de um viravam os do outro. Agora:
#   - com a variação selecionada (UM produto), o cadastro e o site são os dela;
#   - o site só quando TODOS os produtos da vez casam com a MESMA página;
#   - com 2+ modelos no anúncio, os campos de hardware que vêm do anúncio
#     ganham `varia_por_modelo` (a checagem manda para pessoa, não impõe um
#     valor).

NOTA_VARIA_MODELO = "varia conforme o modelo do anúncio"
# Os campos que mudam de um modelo para outro (a RAM e a memória o título já
# diz por modelo e por variação: "20.128 ou 8.128").
CAMPOS_DO_MODELO = frozenset(
    {
        "rede_5g",
        "wifi_5ghz",
        "nfc",
        "chip",
        "sistema",
        "processador",
        "bateria",
        "tela",
        "ram_fisica",
        "ram_virtual",
    }
)
_ACESSORIO_DO_NOME = re.compile(r"\s\+\s.*$")
_PARENTESES = re.compile(r"\([^()]*\)")


def _sem_acessorio(nome: str) -> str:
    """O nome sem o acessório do kit ("… - Prata + Fone UFB10" → "… - Prata")."""
    return _ACESSORIO_DO_NOME.sub("", nome).strip()


def modelo_do_nome(nome: str | None) -> str | None:
    """O MODELO de um produto do cadastro: o nome até o 1º " - ", sem o
    acessório do kit, sem o RAM.ROM (e o que vem depois: a cor) e sem código
    entre parênteses — "Uranyx F109 5G 24.256 - Preto" → "uranyx f109 5g". PURA."""
    if not nome:
        return None
    base = _sem_acessorio(nome.split(" - ")[0])
    if m := _RE_SPEC.search(base):
        base = base[: m.start()]
    return _plano(_PARENTESES.sub(" ", base)) or None


def modelos_do_cadastro(nomes: Iterable[str | None]) -> list[str]:
    """Os modelos distintos dos produtos (na ordem). PURA."""
    return list(dict.fromkeys(m for n in nomes if (m := modelo_do_nome(n))))


def pagina_dos_produtos(
    titulo: str | None,
    nomes_da_vez: Sequence[str | None],
    paginas: Sequence[Any],
    marca: str | None,
    *,
    varios_no_anuncio: bool,
) -> Any | None:
    """A página do site do produto da vez — ou None (na dúvida, nenhuma). PURA.

    `nomes_da_vez` = os produtos da variação selecionada (um) ou do anúncio
    inteiro; `varios_no_anuncio` = o anúncio liga 2+ modelos. Cada produto
    casa pelo NOME (sem o acessório do kit): todos na mesma página → ela; um
    produto sem página ou duas páginas → nenhuma. Sem produto que case, o
    título só vale no anúncio de UM modelo (o caso de antes).
    """
    nomes = [_sem_acessorio(n) for n in nomes_da_vez if n]
    por_nome = [sites_marca.casar(n, paginas, marca) for n in nomes]
    achadas = {getattr(p, "slug", None): p for p in por_nome if p is not None}
    if varios_no_anuncio or len(modelos_do_cadastro(nomes)) >= 2:
        todos_na_mesma = len(achadas) == 1 and all(p is not None for p in por_nome)
        return next(iter(achadas.values())) if todos_na_mesma else None
    if len(achadas) == 1:
        return next(iter(achadas.values()))
    if achadas:
        return None
    return sites_marca.casar(titulo, paginas, marca)


def marcar_varia_por_modelo(resultado: dict) -> dict:
    """Os campos de hardware que vêm do ANÚNCIO de 2+ modelos: `varia_por_modelo`. PURA."""
    for linha in resultado.get("campos") or []:
        if linha.get("campo") in CAMPOS_DO_MODELO and linha.get("fonte") == FONTE_ANUNCIO:
            linha["varia_por_modelo"] = True
            linha["nota"] = NOTA_VARIA_MODELO
    return resultado


# ── O banco ───────────────────────────────────────────────────────────────


def _uuids(ids: Iterable[Any]) -> list[UUID]:
    saida: list[UUID] = []
    for i in ids or ():
        try:
            u = i if isinstance(i, UUID) else UUID(str(i))
        except (TypeError, ValueError):
            continue
        if u not in saida:
            saida.append(u)
    return saida[:MAX_PRODUTOS]


async def produtos_do_anuncio(
    session: AsyncSession,
    plataforma: IntegrationPlatform,
    integration_id: UUID | None,
    item_id: str,
) -> list[UUID]:
    """Os produtos dos vínculos VIVOS do anúncio na loja da conversa (todas as variações).

    O `produtos_vinculados` da ficha é a identidade da variação selecionada
    (e fica vazio acima de 12): para as especificações vale o anúncio inteiro.
    """
    if integration_id is None:
        return []
    return list(
        (
            await session.scalars(
                select(ProductLink.product_id)
                .where(
                    ProductLink.integration_id == integration_id,
                    ProductLink.platform == plataforma,
                    ProductLink.external_id == item_id,
                    ProductLink.morto_desde.is_(None),
                )
                .distinct()
                .limit(MAX_PRODUTOS)
            )
        ).all()
    )


async def especificacoes_do_produto(
    session: AsyncSession,
    ficha: Mapping[str, Any] | None,
    produto_ids: Iterable[UUID | str],
    titulo: str | None,
) -> dict:
    """As especificações do produto do anúncio, com a fonte de cada campo.

    `{"campos": [{campo, valor, fonte: "anuncio"|"davinci"|"site", nota?,
    conflito_no_anuncio?, varia_por_modelo?}], "conflitos": [{campo, valor,
    fonte, outro_valor, outra_fonte}], "conflito_no_anuncio": bool, "site":
    slug|None}`. `ficha` = a de `ficha.ficha_do_anuncio` (ou None; os
    `produtos_vinculados` dela com UM produto = a variação selecionada);
    `produto_ids` = os produtos vinculados ao anúncio inteiro
    (`produtos_do_anuncio`); `titulo` = o título ATUAL do anúncio. Os modelos
    do anúncio: ver "Os modelos do anúncio". Só leitura; erro de banco sobe.
    """
    ficha = ficha if isinstance(ficha, Mapping) else None
    titulo_atual = titulo or (ficha or {}).get("titulo")
    ids = _uuids(produto_ids)
    selecao = _uuids(
        v.get("product_id")
        for v in (ficha or {}).get("produtos_vinculados") or []
        if isinstance(v, Mapping)
    )
    selecao = selecao if len(selecao) == 1 else []
    todos: dict[UUID, tuple[str | None, str | None]] = {}
    if ids or selecao:
        for pid, nome, sku in (
            await session.execute(
                select(Product.id, Product.name, Product.sku)
                .where(Product.id.in_([*ids, *selecao]))
                .order_by(Product.sku)
            )
        ).all():
            todos[pid] = (nome, sku)
    da_vez = [todos[i] for i in selecao if i in todos] or list(todos.values())
    nomes = [n for n, _ in da_vez]
    skus = [k for _, k in da_vez]
    varios = len(modelos_do_cadastro(n for n, _ in todos.values())) >= 2
    marca = sites_marca.marca_do_anuncio(titulo_atual, [k for _, k in todos.values()])
    site: dict[str, list[Achado]] = {}
    slug: str | None = None
    if get_settings().atendimento_sites_marca_ativo and titulo_atual:
        paginas = await sites_marca.paginas_da_marca(session, marca)
        pagina = pagina_dos_produtos(titulo_atual, nomes, paginas, marca, varios_no_anuncio=varios)
        if pagina is not None:
            site, slug = do_site(pagina.specs), pagina.slug
    resultado = juntar(
        do_anuncio(ficha, titulo),
        do_cadastro(nomes, skus, titulo_atual, marca),
        site,
        virtual_no_anuncio=_virtual_no_anuncio(ficha, titulo),
    )
    if varios:
        marcar_varia_por_modelo(resultado)
    return {**resultado, "site": slug}
