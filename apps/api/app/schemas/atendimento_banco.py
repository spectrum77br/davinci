"""Contratos da tela do BANCO DE RESPOSTAS (routers/atendimento_banco.py, 13/11/2026).

A revisão diária do que a IA mandou SOZINHA (👍/👎 e a correção) e a busca no banco
(`atendimento_respostas`). Os textos de cliente só vão para a tela de admin: nunca para o log.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

# A correção do 👎: até 4.000 caracteres (no banco vai a versão generalizada, até 600).
MAX_CHARS_CORRECAO = 4000


# ── A revisão do dia ──────────────────────────────────────────────────────────


class RevisaoNotaOut(BaseModel):
    """A revisão gravada (a `AtendimentoAvaliacao` com a ação `automatico`)."""

    nota: str  # ok | erro
    correcao: str | None = None
    por: str | None = None
    em: datetime | None = None
    # Só no 👎: a correção está no banco (ativa)?
    no_banco: bool | None = None


class RevisaoItemOut(BaseModel):
    """Um envio da IA sozinha (um rascunho; a NF automática manda o arquivo e a frase)."""

    rascunho_id: UUID
    conversa_id: UUID
    integration_id: UUID | None = None
    loja: str | None = None
    plataforma: str
    canal: str
    assunto: str | None = None
    assunto_nome: str
    # Reembolso, cancelamento, troca/devolução e reclamação forte (a régua de 90%).
    sensivel: bool
    # O turno do cliente logo antes do envio (as mensagens seguidas dele, a última incluída).
    pergunta: str | None = None
    # O texto que saiu (as mensagens da IA daquele rascunho, em ordem).
    resposta: str | None = None
    com_anexo: bool = False
    enviada_em: datetime | None = None
    avaliacao: RevisaoNotaOut | None = None


class RevisaoContagensOut(BaseModel):
    # O que a IA mandou sozinha no dia (sem o que uma pessoa já avaliou na conversa).
    total: int
    sensiveis: int
    revisados: int
    erros: int
    mostrados: int


class RevisaoOut(BaseModel):
    dia: date
    hoje: date
    # Até quantos dos não sensíveis entram na amostra do dia.
    amostra: int
    contagens: RevisaoContagensOut
    itens: list[RevisaoItemOut]


class RevisaoIn(BaseModel):
    nota: Literal["ok", "erro"]
    correcao: str | None = Field(default=None, max_length=MAX_CHARS_CORRECAO)

    @model_validator(mode="after")
    def _correcao_no_erro(self) -> RevisaoIn:
        texto = (self.correcao or "").strip()
        if self.nota == "erro" and not texto:
            raise ValueError("no 👎, a correção (como seria o certo) é obrigatória")
        # No 👍 não há correção.
        self.correcao = texto if self.nota == "erro" else None
        return self


class BancoDaCorrecaoOut(BaseModel):
    """O que aconteceu no banco com a revisão."""

    # A correção está no banco agora (entrou ou foi atualizada).
    entrou: bool
    # Por que não entrou (código do `banco_respostas`) e a frase para a tela.
    motivo: str | None = None
    frase: str | None = None
    # O 👍 (ou a correção que não serve) tirou do banco a correção anterior deste envio.
    retirada: bool = False


class RevisaoSalvaOut(BaseModel):
    item: RevisaoItemOut
    banco: BancoDaCorrecaoOut


# ── A busca no banco ──────────────────────────────────────────────────────────


class RespostaBancoOut(BaseModel):
    id: int
    tipo: str
    tipo_nome: str
    integration_id: UUID | None = None
    loja: str | None = None
    plataforma: str
    canal: str
    modelos: list[str]
    pergunta: str
    resposta: str
    # equipe_fora | equipe_escreveu | equipe_ajustou | equipe_aprovou
    fonte: str
    # A correção da revisão diária (o 👎 ao que a IA mandou sozinha).
    da_revisao: bool = False
    respondido_em: datetime
    conversa_id: UUID | None = None


class TipoContagemOut(BaseModel):
    tipo: str
    nome: str
    n: int


class LojaContagemOut(BaseModel):
    integration_id: UUID
    plataforma: str
    loja: str
    n: int


class RespostasBancoOut(BaseModel):
    # Quantas respostas ativas batem com os filtros (a lista vem até `limite`).
    total: int
    itens: list[RespostaBancoOut]
    # Os assuntos e as lojas do banco (as respostas ativas), para os filtros da tela.
    tipos: list[TipoContagemOut]
    lojas: list[LojaContagemOut]
