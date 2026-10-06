from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Valor em R$: maior que zero, no máximo duas casas (RN03) e cabe em NUMERIC(15,2).
_VALOR = {"gt": 0, "max_digits": 15, "decimal_places": 2}


def _texto(v):
    return v.strip() if isinstance(v, str) else v


class ImobilizadoCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    numero: str = Field(min_length=1, max_length=20)
    descricao: str = Field(min_length=3, max_length=200)
    valor: Decimal = Field(**_VALOR)
    responsavel_id: UUID

    _v_texto = field_validator("descricao", mode="before")(_texto)

    @field_validator("numero", mode="before")
    @classmethod
    def _numero(cls, v):
        # Plaqueta digitada de qualquer jeito vira a mesma chave: " imb-000124 " = IMB-000124.
        return v.strip().upper() if isinstance(v, str) else v


class ImobilizadoUpdate(BaseModel):
    """Só descrição, valor e responsável. O número não está aqui de propósito
    (RN02) e `extra="forbid"` devolve 422 se alguém tentar mandar."""

    model_config = ConfigDict(extra="forbid")

    descricao: str | None = Field(default=None, min_length=3, max_length=200)
    valor: Decimal | None = Field(default=None, **_VALOR)
    responsavel_id: UUID | None = None

    _v_texto = field_validator("descricao", mode="before")(_texto)


class ImobilizadoBaixa(BaseModel):
    model_config = ConfigDict(extra="forbid")

    baixa_data: date
    baixa_motivo: str = Field(min_length=3, max_length=200)

    _v_texto = field_validator("baixa_motivo", mode="before")(_texto)


class ImobilizadoTransferir(BaseModel):
    """Passa todos os itens ativos de uma pessoa para outra (RN08)."""

    model_config = ConfigDict(extra="forbid")

    de_id: UUID
    para_id: UUID


class PessoaRef(BaseModel):
    id: UUID
    nome: str
    ativo: bool | None = None


class ImobilizadoOut(BaseModel):
    id: int
    numero: str
    descricao: str
    valor: Decimal
    responsavel: PessoaRef
    status: str
    baixa_data: date | None = None
    baixa_motivo: str | None = None
    criado_em: datetime
    criado_por: PessoaRef | None = None
    atualizado_em: datetime | None = None
    atualizado_por: PessoaRef | None = None


class ImobilizadoListaOut(BaseModel):
    itens: list[ImobilizadoOut]
    quantidade: int
    soma: Decimal


class ImobilizadoHistoricoOut(BaseModel):
    id: int
    campo: str
    valor_anterior: str | None = None
    valor_novo: str | None = None
    alterado_em: datetime
    alterado_por: PessoaRef | None = None


class ImobilizadoTransferirOut(BaseModel):
    transferidos: int
