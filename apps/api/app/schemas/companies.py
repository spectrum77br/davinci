from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator
from stdnum.br import cnpj as br_cnpj
from stdnum.exceptions import ValidationError as StdValidationError


def _normalize_cnpj(v: Any) -> str | None:
    if v is None or v == "":
        return None
    raw = str(v)
    digits = "".join(c for c in raw if c.isdigit())
    if not digits:
        return None
    try:
        br_cnpj.validate(digits)
    except StdValidationError as e:
        raise ValueError(f"cnpj_invalid: {e}") from e
    return digits


class CompanyBase(BaseModel):
    razao_social: str
    apelido: str
    responsavel_id: UUID | None = None
    uf: str | None = None
    cnpj: str | None = None
    inscricao_estadual: str | None = None
    site_url: str | None = None
    obs: str | None = None

    @field_validator("cnpj", mode="before")
    @classmethod
    def _v_cnpj(cls, v: Any) -> str | None:
        return _normalize_cnpj(v)

    @field_validator("uf", mode="before")
    @classmethod
    def _v_uf(cls, v: Any) -> str | None:
        if v is None or v == "":
            return None
        s = str(v).upper().strip()
        if len(s) != 2 or not s.isalpha():
            raise ValueError("uf_invalid")
        return s


class CompanyCreate(CompanyBase):
    pass


class CompanyPatch(BaseModel):
    razao_social: str | None = None
    apelido: str | None = None
    responsavel_id: UUID | None = None
    uf: str | None = None
    cnpj: str | None = None
    inscricao_estadual: str | None = None
    site_url: str | None = None
    obs: str | None = None
    enabled_marketplaces: list[str] | None = None

    @field_validator("cnpj", mode="before")
    @classmethod
    def _v_cnpj(cls, v: Any) -> str | None:
        return _normalize_cnpj(v)

    @field_validator("uf", mode="before")
    @classmethod
    def _v_uf(cls, v: Any) -> str | None:
        if v is None or v == "":
            return None
        s = str(v).upper().strip()
        if len(s) != 2 or not s.isalpha():
            raise ValueError("uf_invalid")
        return s


class CompanyOut(CompanyBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    enabled_marketplaces: list[str] = []
    created_at: datetime
    updated_at: datetime


class StoreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    company_id: UUID
    marketplace: str
    apelido_override: str | None = None
    status: str
    integration_id: UUID | None = None
    bling_store_id: int | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class StoreCreate(BaseModel):
    company_id: UUID
    marketplace: str
    apelido_override: str | None = None
    status: str | None = None
    notes: str | None = None
    bling_store_id: int | None = None


class StorePatch(BaseModel):
    apelido_override: str | None = None
    status: str | None = None
    notes: str | None = None
    bling_store_id: int | None = None
    integration_id: UUID | None = None


class GridStoreCell(BaseModel):
    # `id` is None when the cell came from store_info alone (no Store row in
    # `stores` for that company+marketplace) — the green check still shows up
    # but per-cell actions like "Remover" are disabled on the frontend.
    id: UUID | None = None
    status: str
    label: str
    integration_id: UUID | None = None
    bling_store_id: int | None = None
    from_store_info: bool = False


class CompanyGridRow(BaseModel):
    company: CompanyOut
    stores: dict[str, GridStoreCell | None]


class CompanyGridOut(BaseModel):
    marketplaces: list[str]
    rows: list[CompanyGridRow]


class CompanyDetailOut(CompanyOut):
    stores: list[StoreOut] = []


class CadastroBase(BaseModel):
    tipo: str
    provedor: str | None = None
    responsavel_id: UUID | None = None
    codigo: str
    label: str | None = None
    status: str | None = None
    obs: str | None = None


class CadastroCreate(CadastroBase):
    store_ids: list[UUID] | None = None


class CadastroPatch(BaseModel):
    tipo: str | None = None
    provedor: str | None = None
    responsavel_id: UUID | None = None
    codigo: str | None = None
    label: str | None = None
    status: str | None = None
    obs: str | None = None


class CadastroStoreLink(BaseModel):
    store_id: UUID
    alias: str | None = None


class CadastroStoresPut(BaseModel):
    links: list[CadastroStoreLink]


class CadastroRawLinkResolve(BaseModel):
    store_id: UUID
    alias: str | None = None


class CadastroOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    tipo: str
    provedor: str | None = None
    responsavel_id: UUID | None = None
    codigo: str
    label: str | None = None
    status: str
    obs: str | None = None
    raw_links: dict[str, str] = {}
    created_at: datetime
    updated_at: datetime


class CadastroDetailOut(CadastroOut):
    stores: list[CadastroStoreLink] = []


class CadastroGridStoreCell(BaseModel):
    # `store_id` is None when the cell came from store_info alone (the code
    # is in `store_info.phone`/`email` for that platform but no Store row
    # was linked via `cadastros_stores`).
    store_id: UUID | None = None
    alias: str | None = None
    company_apelido: str
    store_status: str
    from_store_info: bool = False


class CadastroGridRow(BaseModel):
    cadastro: CadastroOut
    cells: dict[str, list[CadastroGridStoreCell]]


class CadastroGridOut(BaseModel):
    marketplaces: list[str]
    rows: list[CadastroGridRow]
