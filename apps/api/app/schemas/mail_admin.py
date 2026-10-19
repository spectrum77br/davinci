"""Administração do e-mail: dados estritos, sem comandos para a caixa de origem."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class PedidoPermitido(BaseModel):
    model_config = ConfigDict(extra="forbid")
    loja: str = Field(min_length=38, max_length=38, pattern=r"^[ifm]:[0-9a-fA-F-]{36}$")
    numero: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class AcessoPut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    modo: Literal["padrao", "selecionadas", "bloqueado"]
    lojas_permitidas: list[str] = Field(default_factory=list, max_length=500)
    lojas_bloqueadas: list[str] = Field(default_factory=list, max_length=500)
    tipos_permitidos: list[Literal["informativo", "chamado", "venda"]] = Field(
        default_factory=lambda: ["informativo", "chamado", "venda"], max_length=3
    )
    pedidos_permitidos: list[PedidoPermitido] = Field(default_factory=list, max_length=500)
    expected_updated_at: AwareDatetime | None = None


class ExcluirEmailIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    motivo: str | None = Field(default=None, max_length=300)
