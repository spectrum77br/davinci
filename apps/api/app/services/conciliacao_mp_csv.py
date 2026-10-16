"""Leitura do CSV do Mercado Pago (Relatório de liberações).

Visto em 14/10/2026 no arquivo real da jlas2: as colunas de JSON (`METADATA` =
`"{"fee_discount": "2.85", ...}"`, `OPERATION_TAGS` = `"[{"logistic_type":"drop_off"}]"`)
vêm entre aspas SEM escapar as aspas de dentro — não é CSV válido e desloca as
colunas seguintes (`BALANCE_AMOUNT`, `ITEM_ID`, `PRODUCT_SKU`…) em qualquer
parser padrão. Já `SALE_DETAIL` vem no padrão (aspas internas duplicadas).
Aqui: campo que começa com `"{` ou `"[` é lido até fechar o colchete/chave; o
resto segue a regra normal de aspas duplicadas.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal, InvalidOperation
from typing import Any


def dividir_linha_mp(linha: str, sep: str = ",") -> list[str]:
    campos: list[str] = []
    atual: list[str] = []
    i, n = 0, len(linha)
    while i < n:
        ch = linha[i]
        if ch == sep:
            campos.append("".join(atual))
            atual = []
            i += 1
            continue
        if ch == '"' and not atual:
            if i + 1 < n and linha[i + 1] in "{[":
                profundidade = 0
                j = i + 1
                while j < n:
                    if linha[j] in "{[":
                        profundidade += 1
                    elif linha[j] in "}]":
                        profundidade -= 1
                        if profundidade == 0:
                            break
                    j += 1
                atual.append(linha[i + 1 : j + 1])
                i = j + 1
                if i < n and linha[i] == '"':
                    i += 1
                continue
            j = i + 1
            buf: list[str] = []
            while j < n:
                if linha[j] == '"':
                    if j + 1 < n and linha[j + 1] == '"':
                        buf.append('"')
                        j += 2
                        continue
                    break
                buf.append(linha[j])
                j += 1
            atual.append("".join(buf))
            i = j + 1
            continue
        atual.append(ch)
        i += 1
    campos.append("".join(atual))
    return campos


def ler_csv_mp(texto: str) -> tuple[list[str], list[dict[str, str]], str]:
    """Cabeçalho, linhas (dict por coluna) e separador de um CSV do Mercado Pago."""
    linhas = [x for x in texto.splitlines() if x.strip()]
    if not linhas:
        return [], [], ","
    sep = ";" if linhas[0].count(";") >= linhas[0].count(",") else ","
    cab = [c.strip() for c in linhas[0].split(sep)]
    rows: list[dict[str, str]] = []
    for linha in linhas[1:]:
        campos = dividir_linha_mp(linha, sep)
        rows.append({cab[k]: (campos[k] if k < len(campos) else "") for k in range(len(cab))})
    return cab, rows, sep


def dinheiro(v: object) -> Decimal:
    """'610.65' → Decimal('610.65'); '1.234,56' → 1234.56; vazio → 0."""
    if v is None:
        return Decimal("0")
    t = str(v).strip()
    if not t:
        return Decimal("0")
    if "," in t and "." not in t:
        t = t.replace(".", "").replace(",", ".")
    elif "," in t and "." in t and t.rfind(",") > t.rfind("."):
        t = t.replace(".", "").replace(",", ".")
    try:
        return Decimal(t).quantize(Decimal("0.01"))
    except InvalidOperation:
        return Decimal("0")


def id_externo_da_linha(row: dict[str, str]) -> str:
    """Identidade estável de uma linha do extrato (o relatório não traz id de
    linha; o mesmo pagamento aparece várias vezes — liberação, reserva,
    mediação…)."""
    partes = [
        row.get("DATE", ""),
        row.get("SOURCE_ID", ""),
        row.get("RECORD_TYPE", ""),
        row.get("DESCRIPTION", ""),
        row.get("NET_CREDIT_AMOUNT", ""),
        row.get("NET_DEBIT_AMOUNT", ""),
        row.get("GROSS_AMOUNT", ""),
        row.get("ORDER_ID", ""),
        row.get("BALANCE_AMOUNT", ""),
    ]
    return hashlib.sha1(
        "|".join(p.strip() for p in partes).encode("utf-8"), usedforsecurity=False
    ).hexdigest()


def numerar_repetidas(movimentos: list[dict[str, Any]]) -> int:
    """Duas linhas IDÊNTICAS no mesmo arquivo (mesma hora, pagamento, valores…) têm o
    mesmo hash: o upsert em lote caía com "ON CONFLICT DO UPDATE command cannot affect
    row a second time" e a janela nunca era lida (16/10, revisão). A 2ª ganha "#2", a
    3ª "#3"… pela ordem do arquivo, como a KFA faz — estável entre releituras, porque
    o Mercado Pago gera o mesmo arquivo na mesma ordem. Devolve quantas ganharam sufixo."""
    vistos: dict[str, int] = {}
    sufixadas = 0
    for m in movimentos:
        base = m["id_externo"]
        n = vistos.get(base, 0) + 1
        vistos[base] = n
        if n > 1:
            m["id_externo"] = f"{base}#{n}"
            sufixadas += 1
    return sufixadas
