"""Biblioteca de legendas do robô de postagem (Eduardo, 16/09/2026).

"com base no produto do criativo e se não, cai num padrão da marca… também
tem que sair quando o post sai automático". Cada linha de
`marketing_legenda_modelos` é UMA VARIAÇÃO de legenda de uma marca —
opcionalmente presa a um produto. Não há unicidade: várias variações por
chave é justamente o ponto (o rodízio escolhe a menos usada recentemente
naquela conta, pra o mesmo texto não sair duas vezes seguidas).

O `texto` é template Jinja com allowlist de AST (`services/marketing/
legenda.py`, mesmo desenho do `services/email_marca.py`) e é validado AQUI,
no salvamento: variação inválida gravada quebraria na hora de publicar — e
ali não tem operador olhando, tem cron.

Só `field_validator` por campo (nunca `model_validator` mode="before"): um
validador de modelo injetaria chaves e o `exclude_unset` do PATCH gravaria
None em campo que o front não mandou — regressão já vista em companies.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator


def _validar(texto: str) -> str:
    """Passa o texto pela validação do serviço e devolve como veio.

    Quem valida é `legenda.validar_modelo` — sandbox, allowlist de AST, teto
    de 2200 e a armadilha de concordância ("uso do {{ produto }}" vira "uso
    do Cafeteira"). Nada disso é reimplementado aqui: a mesma regra que o
    publicador respeita é a que barra o salvamento.

    Import tardio de propósito (molde do `_template` em schemas/marcas.py): o
    schema é importado no boot da API, o serviço arrasta modelo e sessão, e
    nada disso precisa existir pra descrever o corpo de um POST.
    """
    from app.services.marketing.legenda import TemplateInvalidoError, validar_modelo

    try:
        validar_modelo(texto)
    except TemplateInvalidoError as e:
        raise ValueError(str(e)) from e
    return texto


def legenda_opcional(v: Any) -> str | None:
    """Legenda escrita à mão (override do vídeo): vazio vira NULL.

    NULL não é "sem legenda": é o que faz a cascata seguir pro próximo degrau
    (modelo do produto → modelo da marca). Gravar "" pararia a cascata num
    texto vazio e o Reel sairia mudo.

    Validada como template porque É template: o degrau do criativo também
    passa pelo `renderizar` do serviço, e `{{ produtoo }}` gravado aqui só
    apareceria na hora de publicar — quando não tem mais operador olhando.
    """
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    return _validar(s)


def legenda_obrigatoria(v: Any) -> str:
    """`texto` da variação: sem texto a linha não é variação de nada."""
    if v is None:
        raise ValueError("texto_required")
    s = str(v).strip()
    if not s:
        raise ValueError("texto_required")
    return _validar(s)


class LegendaModeloCreate(BaseModel):
    marca_id: UUID
    # NULL = padrão DA MARCA (o degrau que segura o criativo sem produto
    # vinculado); preenchido = legenda daquele produto.
    product_id: UUID | None = None
    texto: str
    ativo: bool = True

    _v_texto = field_validator("texto", mode="before")(legenda_obrigatoria)


class LegendaModeloPatch(BaseModel):
    """`marca_id` fica de fora de propósito.

    A coluna é NOT NULL, então um `"marca_id": null` explícito viraria
    IntegrityError (500) num PATCH que o front acha inofensivo; e mudar a
    marca de uma variação é tirá-la de um cadastro e pô-la noutro sem rastro
    — quem errou a marca apaga e cria. `product_id` SIM: virar padrão da
    marca (null) ou apontar pra outro produto é edição de todo dia.
    """

    product_id: UUID | None = None
    texto: str | None = None
    ativo: bool | None = None

    @field_validator("texto", mode="before")
    @classmethod
    def _v_texto(cls, v: Any) -> str | None:
        return None if v is None else legenda_obrigatoria(v)


class LegendaModeloOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    marca_id: UUID
    marca_nome: str = ""
    product_id: UUID | None = None
    # Nome e SKU vêm de join: a tela lista "Cafeteira Elétrica (dg017)" —
    # sem eles o operador escolheria entre UUIDs.
    product_nome: str | None = None
    product_sku: str | None = None
    texto: str
    ativo: bool
    created_at: datetime
    updated_at: datetime


class LegendaPreviewIn(BaseModel):
    """Prévia de uma variação ainda NÃO salva (o operador está digitando)."""

    texto: str
    marca_id: UUID
    product_id: UUID | None = None

    _v_texto = field_validator("texto", mode="before")(legenda_obrigatoria)


class LegendaPreviewOut(BaseModel):
    texto: str
    # A tela mostra "412/2200" ao lado da prévia, e quem conta é o backend
    # porque o teto vale pro texto RENDERIZADO: `{{ produto }}` ocupa 13
    # caracteres no editor e 17 no Instagram.
    tamanho: int


class LegendaResolvidaOut(BaseModel):
    """O que a cascata escolheria AGORA pro modal de publicar.

    `texto` é NULL quando nenhum degrau respondeu (`origem="nenhuma"`): a
    tela avisa e exige confirmação no clique manual, e o robô automático
    recusa — Reel sem legenda é criativo queimado, é o único texto que a
    busca do Instagram lê daquele vídeo.
    """

    texto: str | None = None
    # manual | criativo | produto | marca | nenhuma — a tela escreve
    # "padrão da marca · variação 2 de 4".
    origem: str
    total_variacoes: int = 0
    # 1-based, NULL quando a legenda não veio da biblioteca (override).
    indice: int | None = None


__all__ = [
    "LegendaModeloCreate",
    "LegendaModeloOut",
    "LegendaModeloPatch",
    "LegendaPreviewIn",
    "LegendaPreviewOut",
    "LegendaResolvidaOut",
    "legenda_obrigatoria",
    "legenda_opcional",
]
