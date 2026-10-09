"""Corpos das rotas NOSSAS por cima da Central de e-mail (configuração da caixa).

Mesma regra da Central: campo desconhecido = 422 (extra="forbid").
"""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, EmailStr, Field, model_validator

# Campos que não aceitam null (os outros: null = limpar).
_SEM_NULO = (
    "visibilidade",
    "ponte_ligada",
    "ponte_so_aliases_de_loja",
    "remetente_estrito",
    "envio_modo",
    "destinatarios_teste",
    "teto_hora",
    "teto_dia",
    "teto_conta_hora",
)


class ConfigCaixaPatch(BaseModel):
    """Só os campos MANDADOS mudam (`model_fields_set`). Os do agente
    (`agente_tipo`, `agente_info`) não entram: vêm do heartbeat v2."""

    model_config = ConfigDict(extra="forbid")

    visibilidade: Literal["privada", "empresa"] | None = None
    ponte_ligada: bool | None = None
    ponte_desde: AwareDatetime | None = None
    ponte_so_aliases_de_loja: bool | None = None
    remetente_estrito: bool | None = None
    envio_modo: Literal["teste", "real"] | None = None
    destinatarios_teste: list[EmailStr] | None = Field(default=None, max_length=20)
    # A conta inteira do Tuta aceita 100/h (somando a equipe e os robôs).
    teto_hora: int | None = Field(default=None, ge=0, le=100)
    teto_dia: int | None = Field(default=None, ge=0, le=1000)
    teto_conta_hora: int | None = Field(default=None, ge=0, le=100)
    envio_pausado_ate: AwareDatetime | None = None
    envio_pausa_motivo: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _sem_nulo(self) -> "ConfigCaixaPatch":
        for nome in _SEM_NULO:
            if nome in self.model_fields_set and getattr(self, nome) is None:
                raise ValueError(f"{nome}_nao_pode_ser_nulo")
        return self

    def mudancas(self) -> dict:
        return {nome: getattr(self, nome) for nome in self.model_fields_set}


class LeitoresPut(BaseModel):
    """Quem mais vê a caixa (só leitura): a lista INTEIRA de user_ids."""

    model_config = ConfigDict(extra="forbid")

    leitores: list[UUID] = Field(max_length=50)
