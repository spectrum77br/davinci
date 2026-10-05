from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.marcas import _email


class MarcaEmailsWrite(BaseModel):
    """Os três endereços de uma vez; vazio/null apaga o daquele tipo."""

    model_config = ConfigDict(extra="forbid")

    sac: str | None = Field(default=None, max_length=254)
    duvidas: str | None = Field(default=None, max_length=254)
    atacado: str | None = Field(default=None, max_length=254)

    _v_email = field_validator("sac", "duvidas", "atacado", mode="before")(_email)


class MarcaEmailsRef(BaseModel):
    """Marca enxuta (email_padroes:view): `site` só pra tela sugerir sac@dominio."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nome: str
    slug: str
    ativo: bool
    site: str | None = None
    has_logo: bool = False
    updated_at: datetime


class MarcaEmailsRow(BaseModel):
    marca: MarcaEmailsRef
    emails: MarcaEmailsWrite


class MarcaEmailsGridOut(BaseModel):
    tipos: list[str]
    rows: list[MarcaEmailsRow]
