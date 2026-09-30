"""Busca de município pelo nome (ou pelo código IBGE) sem sair pra internet.

Eduardo (29/09): "dados reais do que testar" — pra preencher a empresa sem
precisar saber o código IBGE de cor.

ORIGEM do `municipios.json` (baixado UMA vez, fica no repositório):
    https://servicodados.ibge.gov.br/api/v1/localidades/municipios?view=nivelado
(API pública de localidades do IBGE, sem autenticação), guardando só
`municipio-id` → cmun_ibge, `municipio-nome` → nome e `UF-sigla` → uf, um
município por linha, em ordem de código. Baixado em 29/09/2026: 5.571
municípios. Pra atualizar (município novo é raro — o último foi Boa Esperança
do Norte/MT, 2023), baixe de novo o endereço acima e gere o arquivo no mesmo
formato ({"fonte", "baixado_em", "campos", "municipios": [[código, nome, UF]]}).
"""

from __future__ import annotations

import json
import re
import unicodedata
from functools import cache
from pathlib import Path

ARQUIVO = Path(__file__).with_name("municipios.json")
LIMITE = 20


def normalizar(texto: str) -> str:
    """Sem acento, minúsculo e só letras/números: "Embu-Guaçu" → "embu guacu"."""
    sem_acento = "".join(
        ch for ch in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(ch)
    )
    return re.sub(r"[^a-z0-9]+", " ", sem_acento.lower()).strip()


@cache
def _tabela() -> tuple[tuple[str, str, str, str], ...]:
    """(código, nome, UF, nome normalizado), em ordem de código."""
    dados = json.loads(ARQUIVO.read_text(encoding="utf-8"))
    return tuple((c, n, uf, normalizar(n)) for c, n, uf in dados["municipios"])


@cache
def _por_codigo() -> dict[str, tuple[str, str]]:
    return {c: (n, uf) for c, n, uf, _ in _tabela()}


def nome_do_codigo(cmun_ibge: str | None) -> tuple[str, str] | None:
    """(nome oficial com acento, UF) do código IBGE, ou None."""
    return _por_codigo().get(cmun_ibge or "")


def buscar(q: str | None, uf: str | None = None, limite: int = LIMITE) -> list[dict]:
    """Até `limite` municípios. `q` com só dígitos procura pelo código (7 dígitos
    = aquele município; menos = começo do código). Por nome: igual primeiro,
    depois os que começam com o texto, depois os que contêm; empate por nome."""
    uf = (uf or "").strip().upper() or None
    texto = (q or "").strip()
    if not texto:
        return []
    linhas = [r for r in _tabela() if uf is None or r[2] == uf]
    if texto.isdigit():
        achados = [r for r in linhas if r[0].startswith(texto)]
    else:
        alvo = normalizar(texto)
        if not alvo:
            return []
        com = [r for r in linhas if alvo in r[3]]
        com.sort(key=lambda r: (r[3] != alvo, not r[3].startswith(alvo), r[3], r[2]))
        achados = com
    return [{"cmun_ibge": c, "nome": n, "uf": u} for c, n, u, _ in achados[:limite]]
