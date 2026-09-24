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
    ("bateria", re.compile(r"bateria\s*(?:de\s*)?([\d.\s]{3,7})\s*mah", re.I), "bateria de {} mAh"),
    ("tela", re.compile(r"tela\s*(?:de\s*)?(\d{1,2}[.,]\d{1,2})\s*pol", re.I), "tela de {}”"),
    ("camera", re.compile(r"c[âa]mera\s+traseira\s*(?:de\s*)?(\d{1,3})\s*mp", re.I), "câmera de {} MP"),
    ("ram", re.compile(r"mem[óo]ria\s+ram\s*(?:de\s*)?(\d{1,3})\s*gb", re.I), "{} GB de RAM"),
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


def atributos(descricao: str | None) -> dict[str, str]:
    """{rótulo: frase} do que deu pra extrair. Vazio quando não deu nada."""
    texto = descricao or ""
    achados: dict[str, str] = {}
    for rotulo, padrao, forma in _REGRAS:
        m = padrao.search(texto)
        if not m:
            continue
        valor = m.group(1).strip()
        if rotulo == "bateria":
            valor = _numero(valor)
            # Celular com menos de 1000 mAh não existe: é erro de leitura.
            if not valor or int(valor.replace(".", "")) < 1000:
                continue
        achados[rotulo] = forma.format(valor.replace(",", "."))
    for padrao, frase in _SELOS:
        if padrao.search(texto):
            achados.setdefault(frase, frase)
    return achados


# O que diferencia UM produto do outro nem sempre está na descrição.
#
# Nos celulares está: cada um tem sua bateria, sua tela, sua câmera, e a frase
# sai diferente pra cada. Nas malas NÃO — as 1.591 descrições são o mesmo
# texto padrão do catálogo, então a frase sairia idêntica em todos os vídeos.
# O que diferencia mala ("Kit 6", "Kit 8") está no NOME do produto.
_DO_NOME: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bkit\s*(\d{1,2})\b", re.I), "kit com {} peças"),
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
    itens = (_do_nome(nome) + list(atributos(descricao).values()))[:MAX_ITENS]
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
