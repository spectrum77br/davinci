"""Motivos do Flex em português claro, para a tela (Logística › Flex ›
Anúncios Flex). Projeto Flex, etapa 4.

O motor (services/flex_motor) grava o motivo de cada decisão do jeito de quem
programa — "saldo Flex 2 em dg053.sp: precisa de 3 para ligar" — e o MESMO
texto vai para a trilha (flex_log), que serve para conferir o que o robô fez
letra por letra. Quem decide (o dono) lê a tela, não a trilha: aqui o texto
vira uma frase simples, sem "histerese", "saldo Flex" ou nome de chave do .env.

Fica separado do motor de propósito: não muda o que já está gravado nem o que
os testes do motor conferem. Os testes deste arquivo rodam a regra de verdade
(`flex_motor.decidir`) em cada ramo e falham se o motor ganhar um motivo novo
sem tradução. Texto que não casa com nada volta como veio — nunca some.
"""

from __future__ import annotations

import re
from collections.abc import Callable

# "São Bernardo" é o lote .sp — é assim que a operação chama o estoque de lá.
_SP = "São Bernardo"


def _pecas(n: int) -> str:
    if n <= 0:
        return "nenhuma peça livre"
    return "1 peça livre" if n == 1 else f"{n} peças livres"


def _so(n: int) -> str:
    """ "Só 2 peças livres" / "Sem peça livre" (saldo zero ou negativo: há
    pedido Flex esperando mais peça do que o .sp tem)."""
    return "Sem peça livre" if n <= 0 else f"Só {_pecas(n)}"


def _cap(texto: str) -> str:
    return texto[:1].upper() + texto[1:]


def _sem_ponto(texto: str) -> str:
    """Frase traduzida dentro de parênteses: sem o ponto final e em minúscula."""
    t = texto.strip().rstrip(".")
    return t[:1].lower() + t[1:]


def _anuncios(m: int) -> str:
    return "1 anúncio" if m == 1 else f"{m} anúncios"


_SALDO = r"saldo Flex (?P<n>-?\d+) em (?P<sku>\S+)"

# (padrão, frase). A ordem importa: o "limite" carrega outro motivo no fim e
# é traduzido primeiro (o resto passa por motivo_claro de novo).
_REGRAS: list[tuple[re.Pattern[str], Callable[[re.Match[str]], str]]] = [
    (
        re.compile(
            r"^limite de (?P<m>\d+) anúncio\(s\) com Flex na família (?P<f>\S+) — (?P<resto>.+)$"
        ),
        lambda m: (
            f"A família {m['f']} já tem {_anuncios(int(m['m']))} com Flex, que é o limite. "
            f"{motivo_claro(m['resto'])}"
        ),
    ),
    (
        re.compile(rf"^{_SALDO} abaixo de (?P<d>\d+)$"),
        lambda m: (
            f"{_so(int(m['n']))} em {_SP} ({m['sku']}) — com menos de {m['d']} "
            "o Flex fica desligado."
        ),
    ),
    (
        re.compile(rf"^{_SALDO} \(liga com (?P<l>\d+)\)$"),
        lambda m: (
            f"{_cap(_pecas(int(m['n'])))} em {_SP} ({m['sku']}) — dá para ter Flex "
            f"(o mínimo é {m['l']})."
        ),
    ),
    (
        re.compile(rf"^{_SALDO}: entre (?P<d>\d+) e (?P<l>\d+), continua ligado \(histerese\)$"),
        lambda m: (
            f"{_cap(_pecas(int(m['n'])))} em {_SP} ({m['sku']}). O Flex já estava ligado e "
            f"continua até ficar com menos de {m['d']}."
        ),
    ),
    (
        re.compile(rf"^{_SALDO}: precisa de (?P<l>\d+) para ligar$"),
        lambda m: (
            f"{_so(int(m['n']))} em {_SP} ({m['sku']}) — precisa de {m['l']} para ligar o Flex."
        ),
    ),
    (
        re.compile(r"^(?P<sku>\S+) não existe ativo — sem estoque \.sp conhecido, nunca liga$"),
        lambda m: (
            f"O produto {m['sku']} não existe (ou está inativo) no DaVinci — sem saber quanto "
            f"há em {_SP}, o Flex não liga."
        ),
    ),
    (
        re.compile(r"^(?P<sku>\S+) não existe ativo$"),
        lambda m: f"o produto {m['sku']} não existe (ou está inativo) no DaVinci",
    ),
    (
        re.compile(r"^variação (?P<v>.+) com vínculo morto no DaVinci$"),
        lambda m: (
            f"A variação {m['v']} do anúncio não recebe mais o estoque do DaVinci (vínculo "
            f"desfeito). Com Flex ela também sairia de {_SP}, sem peça garantida — o Flex "
            "fica desligado."
        ),
    ),
    (
        re.compile(r"^variação (?P<v>.+) sem vínculo com produto do DaVinci$"),
        lambda m: (
            f"A variação {m['v']} do anúncio não está ligada a nenhum produto do DaVinci. Com "
            f"Flex ela também sairia de {_SP}, sem peça garantida — o Flex fica desligado."
        ),
    ),
    (
        re.compile(
            r"^variação (?P<v>.+?) parada sem \.sp \((?P<x>.+)\) — volta a vender quando o "
            r"estoque for publicado$"
        ),
        lambda m: (
            f"A variação {m['v']} está sem estoque agora, mas não pode ter Flex "
            f"({_sem_ponto(motivo_claro(m['x']) or m['x'])}). Quando o estoque voltar, ela "
            f"venderia pelo Flex sem peça em {_SP} — o Flex fica desligado."
        ),
    ),
    (
        re.compile(r"^anúncio sem vínculo vivo com produto do DaVinci$"),
        lambda m: "O anúncio não está ligado a nenhum produto do DaVinci — o Flex fica desligado.",
    ),
    (
        re.compile(r"^nenhuma variação com estoque publicado$"),
        lambda m: "Nenhuma variação do anúncio está com estoque à venda.",
    ),
    (
        re.compile(r"^variação sem produto no DaVinci$"),
        lambda m: "Uma variação do anúncio não tem produto no DaVinci.",
    ),
    (
        re.compile(r"^(?P<sku>\S+): produto inativo ou excluído$"),
        lambda m: f"O produto {m['sku']} está inativo ou foi excluído.",
    ),
    (
        re.compile(r"^(?P<sku>\S+): sem lote de venda \(ou kit com lotes misturados\)$"),
        lambda m: (
            f"O produto {m['sku']} não é de um lote de venda (.ci, .ra, .sa, .pi ou .sp) "
            "ou é um kit com lotes misturados."
        ),
    ),
    (
        re.compile(r"^(?P<sku>\S+): lote \.(?P<tag>\w+) fora dos lotes de venda$"),
        lambda m: f"O produto {m['sku']} é do lote .{m['tag']}, que não é de venda.",
    ),
    (
        re.compile(r"^(?P<sku>\S+): kit — kits fora do Flex nesta fase \(flex_kits\)$"),
        lambda m: f"O produto {m['sku']} é um kit — por enquanto kits não entram no Flex.",
    ),
    (
        re.compile(r"^a plataforma recusou ligar: (?P<x>.+)$"),
        lambda m: (
            f"A plataforma não aceitou ligar o Flex ({m['x']}). Só tenta de novo se uma "
            "pessoa aprovar outra vez."
        ),
    ),
    (
        re.compile(r"^emergência: desligado por uma pessoa$"),
        lambda m: "Desligado pelo botão de emergência.",
    ),
]


def motivo_claro(motivo: str | None) -> str | None:
    """O motivo do motor numa frase para o dono. Desconhecido: como veio."""
    if motivo is None:
        return None
    texto = motivo.strip()
    if not texto:
        return None
    for padrao, frase in _REGRAS:
        m = padrao.match(texto)
        if m:
            return frase(m)
    return texto
