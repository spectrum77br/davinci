"""Administração do e-mail: dados estritos, sem comandos para a caixa de origem."""


from pydantic import BaseModel, ConfigDict, Field


class ExcluirEmailIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    motivo: str | None = Field(default=None, max_length=300)
