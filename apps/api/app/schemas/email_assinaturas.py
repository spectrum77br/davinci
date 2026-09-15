from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.marcas import EmailContexto


class AssinaturaWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    texto: str = Field(default="", max_length=4000)
    incluir_logo: bool = True
    incluir_dados_marca: bool = True
    ativo: bool = True


class AssinaturaOut(AssinaturaWrite):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    marca_id: UUID
    contexto: EmailContexto
    updated_at: datetime


class AssinaturaPreviewIn(AssinaturaWrite):
    marca_id: UUID


class AssinaturaPreviewOut(BaseModel):
    html: str
    text: str
    avisos: list[str]


class AssinaturaImagem(BaseModel):
    content_id: str
    mime: str
    base64: str


class AssinaturaRenderOut(AssinaturaPreviewOut):
    ativo: bool
    inline_images: list[AssinaturaImagem]


class MarcaAssinaturaRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nome: str
    slug: str
    ativo: bool
    has_logo: bool = False
    company_id: UUID | None = None
    empresa_razao_social: str | None = None
    site: str | None = None
    sac_email: str | None = None
    sac_fone: str | None = None
    updated_at: datetime


class AssinaturasGridRow(BaseModel):
    marca: MarcaAssinaturaRef
    cells: dict[str, AssinaturaOut | None]


class AssinaturasGridOut(BaseModel):
    contextos: list[str]
    rows: list[AssinaturasGridRow]
