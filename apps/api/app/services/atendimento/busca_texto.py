"""Busca por texto do agente (20/10/2026): o mesmo jeito de escrever dos dois lados.

`atendimento_fatos` e `atendimento_pares` guardam um `tsv` (tsvector, índice
GIN) para o agente achar o fato aprovado e a resposta da equipe pela pergunta
do cliente. O `unaccent` do Postgres não é IMMUTABLE (não entra em coluna
gerada nem em índice de expressão) e, em produção, mora no schema `davinci` —
o schema dos testes nem o enxerga. Então a APLICAÇÃO tira acento e caixa em
Python (`texto_de_busca`) e o Postgres só faz o `to_tsvector('portuguese')`,
que com a configuração fixa é IMMUTABLE e não depende de extensão nenhuma.

A busca passa a pergunta pelo MESMO `texto_de_busca` antes do `to_tsquery`:
"Bivolt?" acha "bivolt" e "não" acha "nao" — os dois lados escritos igual.

Quem grava pelo ORM (`AtendimentoFato`, `AtendimentoPar`) não precisa fazer
nada: o model preenche o `tsv` no INSERT e quando o texto muda
(`models/atendimento.py`). Quem grava por `insert()`/`update()` do Core (o
upsert dos pares) passa `tsv=tsv_de(...)` — a coluna é NOT NULL de propósito:
esquecer falha na hora, em vez de gravar fato que a busca nunca acha.

Uso:
    tsv=tsv_de(par.pergunta_gen, par.resposta_gen)    # A = pergunta, B = resposta
    consulta = consulta_de(pergunta)                  # OU das palavras (ranking)
    select(...).where(Fato.tsv.op("@@")(consulta))
        .order_by(func.ts_rank_cd(Fato.tsv, consulta).desc())
"""

from __future__ import annotations

import re
import unicodedata

from sqlalchemy import func, literal_column
from sqlalchemy.dialects.postgresql import REGCONFIG, TSQUERY, TSVECTOR
from sqlalchemy.sql.elements import ColumnElement

CONFIG = "portuguese"
# O peso de cada parte, na ordem em que vem em `tsv_de` (o `ts_rank_cd` dá mais
# a A que a D): no par, a pergunta do cliente pesa mais que a resposta.
PESOS = ("A", "B", "C", "D")
# Pergunta de cliente é curta; mais que isto é colagem de texto (ou tentativa
# de encher a busca) e só deixa a consulta lenta.
MAX_PALAVRAS_CONSULTA = 32
MAX_CHARS_TEXTO = 20_000

_PALAVRA = re.compile(r"[a-z0-9]+")


def texto_de_busca(texto: str | None) -> str:
    """Sem acento, minúsculo e com um espaço só. PURA.

    "Não é BIVOLT?" → "nao e bivolt?". Pontuação fica: o `to_tsvector` a
    descarta sozinho, e "5G"/"Wi-Fi" continuam como o parser do Postgres os lê.
    """
    base = unicodedata.normalize("NFKD", str(texto or "")[:MAX_CHARS_TEXTO])
    sem_acento = "".join(ch for ch in base if not unicodedata.combining(ch))
    return " ".join(sem_acento.lower().split())


def _config() -> ColumnElement:
    # Constante no SQL, não parâmetro: o asyncpg não tem codec de `regconfig`.
    return literal_column(f"'{CONFIG}'::regconfig", type_=REGCONFIG)


def tsv_de(*partes: str | None) -> ColumnElement:
    """A expressão SQL do `tsv` de uma linha: cada parte com o seu peso (A, B, C, D).

    Parte vazia entra como vetor vazio (o `tsv` nunca fica NULL). Mais de
    quatro partes: as excedentes vão juntas na última (peso D).
    """
    textos = [texto_de_busca(p) for p in partes] or [""]
    if len(textos) > len(PESOS):
        textos = [*textos[: len(PESOS) - 1], " ".join(textos[len(PESOS) - 1 :])]
    vetor: ColumnElement | None = None
    for peso, texto in zip(PESOS, textos, strict=False):
        parte = func.setweight(
            func.to_tsvector(_config(), texto, type_=TSVECTOR),
            literal_column(f"'{peso}'"),
            type_=TSVECTOR,
        )
        vetor = parte if vetor is None else vetor.op("||", return_type=TSVECTOR)(parte)
    return vetor


def palavras_de_busca(pergunta: str | None) -> list[str]:
    """As palavras da pergunta (sem acento, só letras e números), sem repetir. PURA."""
    vistas: list[str] = []
    for palavra in _PALAVRA.findall(texto_de_busca(pergunta)):
        if palavra not in vistas:
            vistas.append(palavra)
        if len(vistas) >= MAX_PALAVRAS_CONSULTA:
            break
    return vistas


def consulta_de(pergunta: str | None, *, todas: bool = False) -> ColumnElement | None:
    """O `tsquery` da pergunta do cliente, escrito como o `tsv` foi escrito.

    Padrão: qualquer palavra serve (OU) — pergunta de cliente tem palavra
    demais para exigir todas; quem ordena é o `ts_rank_cd`. `todas=True`: o
    `websearch_to_tsquery` (E entre as palavras, aspas e "-palavra"), o da spec.
    Sem palavra nenhuma → None (não há o que buscar). As palavras vazias do
    português ("de", "o", "que") o próprio Postgres descarta.
    """
    if todas:
        texto = " ".join(texto_de_busca(pergunta).split()[:MAX_PALAVRAS_CONSULTA])
        if not _PALAVRA.search(texto):
            return None
        return func.websearch_to_tsquery(_config(), texto, type_=TSQUERY)
    palavras = palavras_de_busca(pergunta)
    if not palavras:
        return None
    # Só [a-z0-9]: nada da pergunta vira operador do `to_tsquery`.
    return func.to_tsquery(_config(), " | ".join(palavras), type_=TSQUERY)
