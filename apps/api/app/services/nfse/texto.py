"""Contas e textos puros da NFS-e: documento, competência, % e descrição.

Sobrou do motor antigo (gov.br direto, 28/09) o que é regra NOSSA e não
depende de quem emite: a conta do percentual, os marcadores da descrição e a
validação do CNPJ/CPF do tomador. Nada aqui fala com banco nem com a NFE.io.
"""

from __future__ import annotations

import calendar
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from stdnum.br import cpf as std_cpf

MESES = (
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)

CENTAVO = Decimal("0.01")
QUATRO_CASAS = Decimal("0.0001")
VALOR_ZERO = "O valor do serviço tem que ser maior que zero."


def so_digitos(v: str | None) -> str:
    """Telefone, CEP, código IBGE: só os números."""
    return re.sub(r"\D", "", v or "")


def normalizar_documento(v: str | None) -> str:
    """CNPJ/CPF sem pontuação e em maiúsculas. Diferente de `so_digitos`, NÃO
    apaga letras: o CNPJ alfanumérico (IN RFB 2.229/2024, a partir de 07/2026)
    tem letras nas 12 primeiras posições. Número vindo da NFE.io v1 (CNPJ que
    começa com 0 perde o zero) volta com os 14 dígitos."""
    if isinstance(v, int):
        return str(v).zfill(14)
    txt = re.sub(r"[^0-9A-Za-z]", "", v or "").upper()
    if txt.isdigit() and 11 < len(txt) < 14:
        txt = txt.zfill(14)
    return txt


def _dv_cnpj(base: str) -> str:
    """Dígitos verificadores do CNPJ (numérico ou alfanumérico): cada caractere
    vale o código ASCII − 48 (os dígitos continuam valendo eles mesmos)."""
    out = base
    for pesos in ((5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2), (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)):
        soma = sum((ord(ch) - 48) * p for ch, p in zip(out, pesos, strict=True))
        resto = soma % 11
        out += "0" if resto < 2 else str(11 - resto)
    return out[-2:]


def cnpj_valido(doc: str | None) -> bool:
    d = normalizar_documento(doc)
    if not re.fullmatch(r"[0-9A-Z]{12}\d{2}", d) or len(set(d)) == 1:
        return False
    return _dv_cnpj(d[:12]) == d[12:]


def documento_valido(doc: str | None) -> bool:
    d = normalizar_documento(doc)
    if len(d) == 14:
        return cnpj_valido(d)
    if len(d) == 11 and d.isdigit():
        return std_cpf.is_valid(d)
    return False


def data_competencia(competencia: date, hoje: date) -> date:
    """Data da competência (accrualOn): último dia do mês, mas nunca depois de hoje."""
    ultimo = date(
        competencia.year,
        competencia.month,
        calendar.monthrange(competencia.year, competencia.month)[1],
    )
    return min(ultimo, hoje)


def valor_percentual(base: Decimal, percentual: Decimal) -> Decimal:
    """Nota de percentual: base × percentual ÷ 100, no centavo, meio pra cima.

    Eduardo (29/09): "quero emitir uma nota de serviço de 0,5%" — base
    R$ 200.000,00 × 0,5% = R$ 1.000,00. Meio centavo sobe (R$ 101,00 × 0,5% =
    0,505 → R$ 0,51), como na conta de calculadora, não o arredondamento de banco.
    """
    return (Decimal(base) * Decimal(percentual) / Decimal(100)).quantize(
        CENTAVO, rounding=ROUND_HALF_UP
    )


def fmt_percentual(p: Decimal) -> str:
    """0.5000 → "0,5%" · 12.25 → "12,25%" · 1 → "1%" (até 4 casas, sem zeros à direita)."""
    txt = f"{Decimal(p).quantize(QUATRO_CASAS, rounding=ROUND_HALF_UP):f}"
    if "." in txt:
        txt = txt.rstrip("0").rstrip(".")
    return txt.replace(".", ",") + "%"


def fmt_reais(v: Decimal) -> str:
    """200000 → "R$ 200.000,00"."""
    inteiro, cent = f"{Decimal(v).quantize(CENTAVO, rounding=ROUND_HALF_UP):,.2f}".split(".")
    return f"R$ {inteiro.replace(',', '.')},{cent}"


def resolver_descricao(
    texto: str,
    competencia: date,
    *,
    percentual: Decimal | None = None,
    base: Decimal | None = None,
) -> str:
    """Troca {competencia} "09/2026", {mes_nome}, {mes}, {ano}, {percentual}
    "0,5%" e {base} "R$ 200.000,00". Sem o número (ex.: prévia ainda sem a
    base), o marcador fica como está pra pessoa ver o que falta."""
    out = (
        texto.replace("{competencia}", f"{competencia.month:02d}/{competencia.year}")
        .replace("{mes_nome}", MESES[competencia.month - 1])
        .replace("{mes}", f"{competencia.month:02d}")
        .replace("{ano}", str(competencia.year))
    )
    if percentual is not None:
        out = out.replace("{percentual}", fmt_percentual(percentual))
    if base is not None:
        out = out.replace("{base}", fmt_reais(base))
    return out.strip()
