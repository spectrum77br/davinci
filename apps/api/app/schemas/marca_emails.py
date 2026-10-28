from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.marcas import _email


class MarcaEmailsWrite(BaseModel):
    """Os dois endereços (sac@ e admin@) de uma vez; vazio/null apaga o daquele tipo."""

    model_config = ConfigDict(extra="forbid")

    sac: str | None = Field(default=None, max_length=254)
    admin: str | None = Field(default=None, max_length=254)

    _v_email = field_validator("sac", "admin", mode="before")(_email)


class MarcaEmailsRef(BaseModel):
    """Marca enxuta (email_padroes:view): `site` pra tela sugerir sac@dominio
    e, com `sac_fone` (WhatsApp da marca), montar a assinatura pro Tuta."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nome: str
    slug: str
    ativo: bool
    site: str | None = None
    sac_fone: str | None = None
    has_logo: bool = False
    updated_at: datetime
    # A assinatura (app.services.assinatura_email): o nome e a cor da marca, se
    # tem logo PÚBLICO (no site da marca) e o WhatsApp já formatado.
    nome_exibicao: str = ""
    cor: str = ""
    logo_publico: bool = False
    fone_formatado: str = ""


class MarcaEmailsRow(BaseModel):
    marca: MarcaEmailsRef
    emails: MarcaEmailsWrite


class MarcaEmailsGridOut(BaseModel):
    tipos: list[str]
    rows: list[MarcaEmailsRow]


class AssinaturaOut(BaseModel):
    """A assinatura pronta: o HTML (com logo) e a mesma em texto puro."""

    html: str
    texto: str
