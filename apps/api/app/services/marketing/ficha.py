"""Especificação real do produto, extraída do que o time já escreveu.

Eduardo, 24/09/2026: "a descrição está muito genérica".

E está, por um motivo estrutural: existem 9 modelos de legenda, TODOS de
marca, nenhum de produto — então a cascata cai sempre no degrau da marca, e
dois ou três textos giram entre todos os vídeos. O único dado de produto que
entrava numa legenda era o nome ("Fossibot F109S 256 GB Preto").

Mas a informação existe e é boa: 738 anúncios têm descrição escrita pelo
próprio time, com bateria, tela, câmera, processador e memória de verdade.
Este módulo lê essa descrição e monta UMA FRASE pronta.

POR QUE FRASE PRONTA, e não `{{ bateria }}` solto: o sandbox do template
bloqueia `{% if %}` de propósito (sem isso, `{{ 'x' * 10**9 }}` trava o
servidor — está no comentário do `_checa_no`). Sem condicional, um placeholder
vazio vira frase quebrada: "Bateria de  que aguenta o dia" iria ao ar assim,
na conta da marca. Carregando a frase inteira, produto sem ficha simplesmente
não rende frase nenhuma, e o texto fecha do mesmo jeito.
"""

from __future__ import annotations

import re

# Cada regra é (rótulo, regex, como formatar). A ordem é a de importância pro
# público deste catálogo (celular robusto): bateria vende mais que processador.
#
# As descrições são texto livre de marketplace, escritas por gente diferente
# em anos diferentes — então o que não casar limpo fica DE FORA. Meia frase
# certa é melhor que uma frase inteira chutada.
_REGRAS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    # O ":" opcional é de 24/09/2026: o bloco do Oukitel G1 escreve "Bateria:
    # 10600 Mah", "Camera Traseira: 48 MP" — sem ele nada casava ali e a
    # frase saía com os números do WP28E, o aparelho do bloco seguinte.
    (
        "bateria",
        re.compile(r"bateria\s*:?\s*(?:de\s*)?([\d.\s]{3,7})\s*mah", re.I),
        "bateria de {} mAh",
    ),
    ("tela", re.compile(r"tela\s*:?\s*(?:de\s*)?(\d{1,2}[.,]\d{1,2})\s*pol", re.I), "tela de {}”"),
    (
        "camera",
        re.compile(r"c[âa]mera\s+traseira\s*:?\s*(?:de\s*)?(\d{1,3})\s*mp", re.I),
        "câmera de {} MP",
    ),
    ("ram", re.compile(r"mem[óo]ria\s+ram\s*:?\s*(?:de\s*)?(\d{1,3})\s*gb", re.I), "{} GB de RAM"),
)

# Selos que aparecem no texto e valem por si — não precisam de número.
#
# A lista cobre DOIS catálogos, e de propósito: o mesmo extrator serve celular
# (Uranyx) e mala (Charlots). Descrição de mala não casa com nenhuma regra de
# celular e vice-versa, então misturar não confunde — o que não casa fica de
# fora, que é a regra da casa aqui.
#
# Os termos de mala saíram de uma contagem nas 1.591 descrições reais, não de
# palpite: ABS aparece 5.957 vezes, rodas 360° e puxador telescópico 5.010,
# cadeado com segredo 5.001. TSA e policarbonato aparecem 9 vezes — estão na
# lista porque quando aparecem valem, mas não são o caso comum.
_SELOS: tuple[tuple[re.Pattern[str], str], ...] = (
    # celular
    (re.compile(r"\bip6[89]\b", re.I), "resistente a água e poeira"),
    (re.compile(r"\bnfc\b", re.I), "NFC"),
    (re.compile(r"\b5g\b", re.I), "5G"),
    (re.compile(r"impress[ãa]o\s+digital", re.I), "leitor de digital"),
    # mala
    (re.compile(r"\bpolicarbonato\b", re.I), "casco em policarbonato"),
    (re.compile(r"\bABS\b"), "casco rígido em ABS"),
    (re.compile(r"\bTSA\b"), "fechadura TSA"),
    (re.compile(r"cadeado\s+com\s+segredo|segredo\s+num[ée]rico", re.I), "cadeado com segredo"),
    (re.compile(r"rodas?\s*360|rodinhas?\s*360", re.I), "rodinhas 360°"),
    (re.compile(r"telesc[óo]p", re.I), "puxador telescópico"),
    (re.compile(r"expans[íi]v", re.I), "expansível"),
)

# Teto de itens na frase. Mais que isso vira lista de supermercado e o leitor
# desiste antes do fim — a legenda é pra vender, não pra catalogar.
MAX_ITENS = 3


def _numero(bruto: str) -> str:
    """"10300" → "10.300". Milhar com ponto, que é como se lê em português."""
    so_digitos = re.sub(r"\D", "", bruto)
    if not so_digitos:
        return ""
    return f"{int(so_digitos):,}".replace(",", ".")


def _valor(rotulo: str, bruto: str) -> str | None:
    """O número de UMA ocorrência, normalizado — ou None se não vale."""
    valor = bruto.strip()
    if rotulo == "bateria":
        valor = _numero(valor)
        # Celular com menos de 1000 mAh não existe: é o relógio ou o fone do
        # kit ("Bateria 450 mAh"), ou erro de leitura.
        if not valor or int(valor.replace(".", "")) < 1000:
            return None
    return valor.replace(",", ".")


# Um anúncio, várias respostas (Eduardo, 24/09/2026 — criativo do F110L).
#
# Medido em produção: dos 262 anúncios de celular, 48 citam DUAS RAMs e 32
# duas baterias. Dois formatos:
#
#   VERSÕES   um aparelho, duas memórias: "Memoria Interna 128 ou 256 GB /
#             Memoria Ram 24 GB … / Memoria Ram 16 GB …". Qual vale está no
#             NOME do produto ("C68 Plus 16.256" → 16 GB).
#   APARELHOS dois aparelhos separados por uma linha de traços: "Fossibot F110
#             Pro 5G … Memoria Ram 20 GB" / "------" / "Fossibot F110 L …
#             Memoria Ram 8 GB". Qual vale é o bloco cujo título tem o modelo
#             do nome.
#
# Antes, a regra pegava a PRIMEIRA ocorrência: o F110L 8.128 ia ao ar com
# "20 GB de RAM" (a do Pro 5G) e "NFC" (que ele não tem). Na dúvida, o dado
# sai da frase — meia frase certa é melhor que uma frase inteira errada.
_DIVISORIA = re.compile(r"\n\s*[-=_*—]{5,}\s*\n")
# RAM no nome: "F110L 8.128" → 8 (RAM.armazenamento, como o catálogo escreve).
_RE_RAM_NOME = re.compile(r"(?<![\d.])(\d{1,2})\.(?:16|32|64|128|256|512|1024)(?![\d.])")
# Onde o MODELO acaba no nome: na memória ("8.128", "4+128"), no " - cor" ou
# no " + acessório".
_RE_FIM_MODELO = re.compile(r"\s+\+\s|\s-\s|\b\d{1,2}\s*[.+]\s*\d{2,4}\b")
# Palavra que, logo depois do modelo, faz dele OUTRO aparelho: "Poco M7" não é
# o bloco do "Poco M7 Pro 5G", nem "F110" o do "F110 L".
_QUALIFICA = frozenset(
    {"pro", "plus", "max", "lite", "ultra", "mini", "prime", "neo", "5g", "4g", "s", "l"}
)


def _palavras(texto: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (texto or "").lower())


def _modelo_do_nome(nome: str | None) -> list[list[str]]:
    """As formas de procurar o modelo, da mais longa pra mais curta.

    "Uranyx C57 PRO 4+128 Preto + Fone U9" → [uranyx c57 pro], [c57 pro]. A
    loja põe a própria marca na frente ("Uranyx") e o anúncio usa a do
    fabricante ("Oukitel C57 Pro"), por isso a segunda forma. Só vale forma
    que ainda tenha palavra com número — "pro" sozinho casaria com tudo.
    """
    palavras = _palavras(_RE_FIM_MODELO.split(nome or "", maxsplit=1)[0])
    formas = []
    for i in range(len(palavras)):
        forma = palavras[i:]
        if any(re.search(r"\d", w) for w in forma):
            formas.append(forma)
    return formas


def _casa_titulo(titulo: str, modelo: list[str]) -> bool:
    """O modelo aparece no título como palavras INTEIRAS e não é seguido de
    qualificador. Compara grudado ("F110L" casa "F110 L"), mas começando e
    terminando em fronteira de palavra — senão "G1" casaria "G1s"."""
    palavras = _palavras(titulo)
    alvo = "".join(modelo)
    inicios, pos = [], 0
    for w in palavras:
        inicios.append(pos)
        pos += len(w)
    compacto = "".join(palavras)
    for ini in inicios:
        fim = ini + len(alvo)
        if not compacto.startswith(alvo, ini):
            continue
        if fim != len(compacto) and fim not in inicios:
            continue  # acabou no meio de uma palavra
        prox = palavras[inicios.index(fim)] if fim in inicios else ""
        if prox not in _QUALIFICA:
            return True
    return False


def _tem_ficha(texto: str) -> bool:
    return any(padrao.search(texto) for _, padrao, _ in _REGRAS)


def _bloco_do_nome(blocos: list[str], nome: str | None) -> str | None:
    """O bloco cujo TÍTULO tem o modelo do nome — se for um só.

    Título = as 3 primeiras linhas: muito anúncio abre com o aviso "Caixa
    Slim, o aparelho acompanha apenas o cabo…" e só depois diz o aparelho.
    Título e não o bloco inteiro: no corpo aparece de tudo ("compatível com
    Poco M7 Pro"). A forma mais longa do modelo que achar bloco decide; se
    ela achar mais de um, não há como saber e ninguém é escolhido.
    """
    titulos = [
        "\n".join([linha for linha in b.splitlines() if linha.strip()][:3]) for b in blocos
    ]
    for modelo in _modelo_do_nome(nome):
        casam = [b for b, t in zip(blocos, titulos, strict=True) if _casa_titulo(t, modelo)]
        if casam:
            return casam[0] if len(casam) == 1 else None
    return None


def _atributos_do_texto(texto: str, nome: str | None) -> dict[str, str]:
    achados: dict[str, str] = {}
    ram_do_nome = _RE_RAM_NOME.search(nome or "")
    for rotulo, padrao, forma in _REGRAS:
        valores = [
            v for v in (_valor(rotulo, m.group(1)) for m in padrao.finditer(texto)) if v
        ]
        distintos = list(dict.fromkeys(valores))
        if not distintos:
            continue
        if len(distintos) == 1:
            valor = distintos[0]
        elif rotulo == "ram" and ram_do_nome and ram_do_nome.group(1) in distintos:
            valor = ram_do_nome.group(1)
        else:
            continue  # duas respostas e nada que desempate: fica de fora
        achados[rotulo] = forma.format(valor)
    for padrao, frase in _SELOS:
        if padrao.search(texto):
            achados.setdefault(frase, frase)
    return achados


def atributos(descricao: str | None, nome: str | None = None) -> dict[str, str]:
    """{rótulo: frase} do que deu pra extrair. Vazio quando não deu nada.

    `nome` é o do produto: é ele que diz qual bloco (ou qual RAM) vale quando
    o anúncio traz mais de uma resposta — ver o comentário acima.
    """
    texto = descricao or ""
    blocos = [b for b in _DIVISORIA.split(texto) if _tem_ficha(b)]
    if len(blocos) > 1:
        escolhido = _bloco_do_nome(blocos, nome)
        if escolhido is not None:
            return _atributos_do_texto(escolhido, nome)
        # Nenhum bloco é claramente o do produto: vale só o que TODOS dizem
        # igual (bateria e câmera iguais nos dois F110, por exemplo).
        por_bloco = [_atributos_do_texto(b, nome) for b in blocos]
        return {
            k: v for k, v in por_bloco[0].items() if all(o.get(k) == v for o in por_bloco[1:])
        }
    return _atributos_do_texto(texto, nome)


# O que diferencia UM produto do outro nem sempre está na descrição.
#
# Nos celulares está: cada um tem sua bateria, sua tela, sua câmera, e a frase
# sai diferente pra cada. Nas malas NÃO — as 1.591 descrições são o mesmo
# texto padrão do catálogo, então a frase sairia idêntica em todos os vídeos.
# O que diferencia mala ("Kit 6", "Kit 8") está no NOME do produto.
_DO_NOME: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bkit\s*(\d{1,2})\b", re.I), "kit com {} peças"),
    # Mala avulsa: o que separa uma da outra é o TAMANHO (em polegadas), que o
    # catálogo guarda como "tamanho 12", "tamanho 28". Sem isto, as 104 malas
    # de 12 polegadas renderiam a mesma frase — a descrição do anúncio é o
    # mesmo texto padrão pro catálogo inteiro.
    (re.compile(r"\btamanho\s*(\d{1,2})\b", re.I), "{} polegadas"),
)


def _do_nome(nome: str | None) -> list[str]:
    achados = []
    for padrao, forma in _DO_NOME:
        m = padrao.search(nome or "")
        if m:
            achados.append(forma.format(m.group(1)))
    return achados


def destaque(descricao: str | None, nome: str | None = None) -> str:
    """UMA frase com o que o produto tem de melhor, ou vazio.

    Vazio é resposta legítima e não quebra nada: o template que usa
    `{{ destaque }}` simplesmente fecha sem ela.
    """
    # O que vem do NOME entra primeiro: é o que diferencia este produto dos
    # irmãos dele, e por isso vale mais que a característica de catálogo.
    itens = (_do_nome(nome) + list(atributos(descricao, nome).values()))[:MAX_ITENS]
    if not itens:
        return ""
    frase = itens[0] if len(itens) == 1 else ", ".join(itens[:-1]) + " e " + itens[-1]
    return _maiuscula_inicial(frase) + "."


def _maiuscula_inicial(texto: str) -> str:
    """Sobe SÓ a primeira letra.

    `.capitalize()` do Python abaixa todo o resto, e aqui isso estraga a
    unidade: "bateria de 10.300 mAh" virava "...mah", "câmera de 20 MP" virava
    "...mp". Unidade com caixa errada numa legenda pública é o tipo de detalhe
    que ninguém revisa e todo mundo vê.
    """
    return texto[:1].upper() + texto[1:] if texto else texto
