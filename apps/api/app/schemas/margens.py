from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

MargensStatus = Literal["Pendente", "Reprovado", "Aprovado"]
ALLOWED_STATUS: tuple[MargensStatus, ...] = ("Pendente", "Reprovado", "Aprovado")


class MargensOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    data: datetime | None = None
    pedido_bling: int | None = None
    pedido_plataforma: str | None = None
    conta: str | None = None
    sku: str | None = None
    produtos: str | None = None
    custo: float | None = None
    margem: float | None = None
    margem_min: float | None = None
    status: MargensStatus = "Pendente"
    observacao: str | None = None

    @field_validator("status", mode="before")
    @classmethod
    def _default_status(cls, v: str | None) -> str:
        if v is None or v == "":
            return "Pendente"
        if v not in ALLOWED_STATUS:
            return "Pendente"
        return v


class MargensPatch(BaseModel):
    status: MargensStatus | None = None
    observacao: str | None = None
