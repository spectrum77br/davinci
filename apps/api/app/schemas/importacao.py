"""Pydantic schemas for the Importação module."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


# ── Config ─────────────────────────────────────────────────────────────


class ImportConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    tempo_reposicao: int
    tempo_estoque: int


class ImportConfigPatch(BaseModel):
    tempo_reposicao: int | None = None
    tempo_estoque: int | None = None


# ── Product ────────────────────────────────────────────────────────────


class ImportProductBase(BaseModel):
    fornecedor: str | None = None
    modelo_china: str | None = None
    cor_china: str | None = None
    fechamento: str | None = None
    tsa: int | None = None
    modelo_bling: str | None = None
    sku: str
    cor: str | None = None
    custo_bling: Decimal = Decimal("0")
    estoque_bling: int | None = None
    consumo_diario: Decimal | None = None
    maior_media_30d: Decimal | None = None
    obs: str | None = None


class ImportProductCreate(ImportProductBase):
    pass


class ImportProductPatch(BaseModel):
    fornecedor: str | None = None
    modelo_china: str | None = None
    cor_china: str | None = None
    fechamento: str | None = None
    tsa: int | None = None
    modelo_bling: str | None = None
    sku: str | None = None
    cor: str | None = None
    custo_bling: Decimal | None = None
    estoque_bling: int | None = None
    consumo_diario: Decimal | None = None
    maior_media_30d: Decimal | None = None
    obs: str | None = None


class ImportProductOut(ImportProductBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    # Computed fields appended by the router (not stored).
    memoria_consumo: Decimal | None = None
    reposicao_estoque: int | None = None
    saldo_reposicao: int | None = None
    # Quantities keyed by lote_id (str) → quantity. The frontend uses
    # this to render dynamic per-lote columns without needing a second
    # request per row.
    lote_quantidades: dict[str, int] = {}
    created_at: datetime
    updated_at: datetime


# ── Lote ───────────────────────────────────────────────────────────────


class ImportLoteCreate(BaseModel):
    nome: str
    abertura: date


class ImportLotePatch(BaseModel):
    nome: str | None = None
    abertura: date | None = None
    fechamento: date | None = None
    realizado: Decimal | None = None


class ImportLoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    nome: str
    abertura: date
    fechamento: date | None = None
    realizado: Decimal
    # Computed by the router:
    previsto: Decimal = Decimal("0")
    saldo: Decimal = Decimal("0")
    prazo: int | None = None  # days between abertura and fechamento
    is_aberto: bool = True


# ── LoteItem (quantity per SKU per lote) ───────────────────────────────


class ImportLoteItemUpsert(BaseModel):
    product_id: UUID
    quantidade: int


# ── Resumo ─────────────────────────────────────────────────────────────


class ImportResumoCreate(BaseModel):
    data: date
    lote_id: UUID | None = None
    lote_nome: str | None = None
    saldo: Decimal
    obs: str | None = None


class ImportResumoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    data: date
    lote_id: UUID | None = None
    lote_nome: str | None = None
    saldo: Decimal
    obs: str | None = None
    # ImportResumo is intentionally append-only (no TimestampMixin)
    # so the model doesn't expose created_at — keep the schema in sync
    # or the GET endpoint 500s on Pydantic validation.


class ImportResumoList(BaseModel):
    items: list[ImportResumoOut]
    total: Decimal
