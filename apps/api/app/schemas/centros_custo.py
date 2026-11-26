"""Cadastros › Centro de custo (M-76): formatos da API /api/centros-custo."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class CentroCustoOut(BaseModel):
    id: UUID
    nome: str
    # Celular, Eletro e Mala: não mudam de nome e não saem.
    fixo: bool
    # A raiz de Cadastros › Segmentos (só os fixos têm; centro novo nasce sem).
    segment_id: UUID | None


class CentroCustoIn(BaseModel):
    # O limite de 60 letras é conferido depois de tirar os espaços (mensagem em português).
    nome: str = Field(min_length=1, max_length=200)


class UsoCentroOut(BaseModel):
    categorias: int
    contas: int
    lojas: int
    produtos: int
    produtos_tabela: int
    produtos_cadastro: int
    # M-73: produtos do Produtos 2 com este centro escolhido (produto_regras.centro_custo_id).
    produtos_centro: int = 0
    bloqueia: bool
    mensagem: str | None
