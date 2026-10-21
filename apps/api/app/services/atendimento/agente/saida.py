"""A resposta final do agente (`Proposta`, spec 3.3.3) e a ida dela à `conferir_saida`.

O esquema da `Proposta` vai no pedido em `output_config.format`
(`agente.argumentos_do_laco`, com `esquema()`: o JSON Schema que o SDK
`anthropic` 1.9.0 geraria pelo pydantic e passaria por `transform_schema` —
`lib/_parse/_transform.py` —, que fecha o objeto, `additionalProperties:
false`, e só mantém `required` o que o pydantic marcou). NÃO vai como
`output_format`: o SDK só valida a resposta com ele (`lib/_parse/_response.py`)
e validaria TODO bloco de texto de toda resposta do laço. A leitura é só na
resposta final (`agente.proposta_da_mensagem` → `ler_proposta`). Por isso
NENHUM campo tem valor padrão: com padrão o campo sai de `required` e o
modelo pode omiti-lo. `acao_pessoa` é `AcaoPessoa | None` SEM padrão:
obrigatório, com `anyOf [string, null]`.

E nenhuma restrição que o esquema não carrega (`ge`, `le`, `max_length`...):
o `transform_schema` joga essas no texto da descrição e a API não as impõe;
a `confianca` é limitada aqui (`confianca`), não no modelo.

Na volta, `para_conferir` traduz a proposta para os argumentos da
`ia.conferir_saida(..., agente=True)` (a leitura que o relatório do legado
combinou): texto, assunto (inválido vira `outro` — spec 6.5.7), precisa de
pessoa, motivo do modelo e `perguntar`.
"""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal, TypedDict

import pydantic
from pydantic import BaseModel, Field

from app.services.atendimento import constantes

RESPONDER = "responder"
PERGUNTAR = "perguntar"
PESSOA = "pessoa"
ACOES_AGENTE = (RESPONDER, PERGUNTAR, PESSOA)

Acao = Literal["responder", "perguntar", "pessoa"]
# Os 15 códigos da régua (spec 6.3), na ordem dela — os mesmos de `constantes`.
AcaoPessoa = Literal[*constantes.ACOES_PESSOA]  # type: ignore[valid-type]

ASSUNTO_OUTRO = "outro"
# O "perguntar" sobre produto ou variação (spec 3.3.3).
ASSUNTO_CONFIRMAR_VARIACAO = "confirmar_variacao"

MOTIVO_PESSOA_SEM_ACAO = "o agente pediu pessoa sem uma ação da régua"
MAX_EVIDENCIAS = 20
MAX_CHARS_MOTIVO = 300


class Proposta(BaseModel):
    """A resposta final do agente (o JSON que fecha o laço). Sem valor padrão (ver o topo)."""

    acao: Annotated[
        Acao,
        Field(
            description=(
                "responder = o texto vai ao cliente; perguntar = o texto é UMA pergunta de "
                "esclarecimento ao cliente; pessoa = alguém da equipe precisa agir ou decidir"
            )
        ),
    ]
    texto: Annotated[
        str,
        Field(
            description=(
                "O que iria ao cliente, em português, de 1 a 3 frases. Em 'pessoa', a "
                "resposta que vale depois que a pessoa agir (sem espera nem promessa da "
                "equipe); vazio quando não há nada verdadeiro a dizer antes dela."
            )
        ),
    ]
    assunto: Annotated[str, Field(description="O id do assunto, da lista de assuntos do sistema.")]
    confianca: Annotated[
        float, Field(description="De 0 a 1: o quanto a resposta está certa e completa.")
    ]
    acao_pessoa: Annotated[
        AcaoPessoa | None,
        Field(
            description=(
                "Obrigatório quando acao = 'pessoa': o código da ação de pessoa. "
                "null nos outros casos."
            )
        ),
    ]
    motivo_pessoa: Annotated[
        str,
        Field(
            description=(
                "Em 'pessoa', o que a pessoa precisa fazer ou decidir e por quê, numa frase "
                "curta, sem dado pessoal nem lacuna; vazio nos outros casos."
            )
        ),
    ]
    evidencias: Annotated[
        list[str],
        Field(
            description=(
                "Os ids do que sustenta a resposta, como vieram nos dados: 'pedido', "
                "'ficha:<item>', 'variacoes:<item>', 'garantia:<id>', 'fato:<id>', "
                "'regra:<assunto>'."
            )
        ),
    ]


def esquema() -> dict:
    """O JSON Schema que vai à API (o mesmo do SDK: pydantic + `transform_schema`)."""
    from anthropic.lib._parse._transform import transform_schema

    return transform_schema(pydantic.TypeAdapter(Proposta).json_schema())


def ler_proposta(texto: str | None) -> Proposta | None:
    """A proposta a partir do texto JSON cru (plano B do laço manual); inválida = None."""
    if not (texto or "").strip():
        return None
    try:
        return Proposta.model_validate_json(texto or "")
    except pydantic.ValidationError:
        return None


# ── A leitura da proposta ─────────────────────────────────────────────────


def confianca(p: Proposta) -> float:
    """A `confianca` entre 0 e 1 (NaN ou infinito = 0)."""
    valor = float(p.confianca)
    if not math.isfinite(valor):
        return 0.0
    return min(1.0, max(0.0, valor))


def evidencias(p: Proposta) -> list[str]:
    """Os ids citados, limpos, sem repetir, até `MAX_EVIDENCIAS`."""
    limpos = (" ".join(str(e or "").split()) for e in p.evidencias)
    return list(dict.fromkeys(e for e in limpos if e))[:MAX_EVIDENCIAS]


def acoes_do_modelo(p: Proposta) -> list[str]:
    """A ação de pessoa que o MODELO declarou (vale mesmo com `acao` != 'pessoa').

    O modelo pode acrescentar ação, nunca tirar (spec 6.3): uma `acao_pessoa`
    preenchida numa resposta "responder" é uma ação — o lado seguro.
    """
    return [p.acao_pessoa] if p.acao_pessoa else []


def precisa_pessoa(p: Proposta) -> bool:
    return p.acao == PESSOA or p.acao_pessoa is not None


def motivo_do_modelo(p: Proposta) -> str:
    """O motivo que o modelo deu, para a coluna `motivo` (vazio fora de 'pessoa')."""
    if not precisa_pessoa(p):
        return ""
    frase = " ".join((p.motivo_pessoa or "").split())[:MAX_CHARS_MOTIVO]
    if p.acao_pessoa is None:
        cabeca = MOTIVO_PESSOA_SEM_ACAO
    else:
        cabeca = f"ação de pessoa: {constantes.rotulo_acao_pessoa(p.acao_pessoa)}"
    return f"{cabeca} — {frase}" if frase else cabeca


def assunto_valido(
    assunto: str | None, ids_categorias: list[str] | tuple[str, ...]
) -> tuple[str, bool]:
    """(assunto, válido): fora da lista vira `outro` (spec 6.5.7) quando `outro` existe."""
    nome = (assunto or "").strip()
    if nome in ids_categorias:
        return nome, True
    if ASSUNTO_OUTRO in ids_categorias:
        return ASSUNTO_OUTRO, True
    return nome, False


class EntradaConferir(TypedDict):
    """Os argumentos da `ia.conferir_saida` que vêm da proposta (o resto é do `_gerar`)."""

    texto: str
    categoria: str
    categoria_valida: bool
    precisa_humano: bool
    motivo_modelo: str
    perguntar: bool


def para_conferir(p: Proposta, ids_categorias: list[str] | tuple[str, ...]) -> EntradaConferir:
    """A proposta nos argumentos da `ia.conferir_saida(..., agente=True)`.

    `texto` = o texto (com as lacunas, cru: a conferência preenche);
    `categoria` / `categoria_valida` = o assunto (`assunto_valido`);
    `precisa_humano` = 'pessoa' ou ação de pessoa declarada;
    `motivo_modelo` = `motivo_do_modelo`; `perguntar` = acao 'perguntar'.
    As ações de pessoa do CÓDIGO não entram aqui: o laço as junta depois
    (`checagens.aplicar`).
    """
    categoria, valida = assunto_valido(p.assunto, ids_categorias)
    return EntradaConferir(
        texto=(p.texto or "").strip(),
        categoria=categoria,
        categoria_valida=valida,
        precisa_humano=precisa_pessoa(p),
        motivo_modelo=motivo_do_modelo(p),
        perguntar=p.acao == PERGUNTAR,
    )


def para_fatos(p: Proposta) -> dict[str, Any]:
    """A proposta como veio (replay/eval: `atendimento_agente_replay.proposta`)."""
    return p.model_dump(mode="json")
