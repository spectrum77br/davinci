from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


def _slugify(v: str) -> str:
    s = v.strip().lower()
    out = []
    prev_dash = False
    for ch in s:
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif ch in (" ", "-", "_", "/", "."):
            if not prev_dash and out:
                out.append("-")
                prev_dash = True
    return "".join(out).strip("-")


# migration 0488: o texto vai na observação da NF (infCpl) e na etiqueta, onde
# " | " separa uma DUIMP da outra (uma linha cada) — por isso "|" é recusado.
# 75 caracteres: o que cabe numa linha "DUIMP: …" da etiqueta de 10 cm na
# fonte mínima (5,5 pt), mesmo em MAIÚSCULAS (medido com _largura em 16/11).
DUIMP_PADRAO_MAX = 75
# A Helvetica da etiqueta só imprime Latin-1: o que o teclado/Mac troca sozinho
# (travessão, aspas curvas, reticências) vira o equivalente simples; o resto
# fora do Latin-1 (emoji etc.) é recusado — sairia "·" na etiqueta.
_TROCAS = str.maketrans({
    "\u2014": "-", "\u2013": "-", "\u2012": "-", "\u2212": "-",
    "\u201c": '"', "\u201d": '"', "\u201e": '"',
    "\u2018": "'", "\u2019": "'", "\u201a": "'",
    "\u2026": "...", "\u00a0": " ",
})


def _limpar_duimp_padrao(v: Any) -> str | None:
    """strip; vazio → None (apaga); "|", caractere que a etiqueta não imprime
    ou mais de 75 caracteres → erro."""
    if v is None:
        return None
    s = " ".join(str(v).translate(_TROCAS).split())
    if not s:
        return None
    if "|" in s:
        raise ValueError("duimp_padrao_sem_barra")
    if any(ord(c) > 0xFF or (ord(c) < 0x20) or (0x7F <= ord(c) < 0xA0) for c in s):
        raise ValueError("duimp_padrao_caractere_invalido")
    if len(s) > DUIMP_PADRAO_MAX:
        raise ValueError("duimp_padrao_muito_longo")
    return s

class SegmentCreate(BaseModel):
    name: str
    slug: str | None = None
    parent_id: UUID | None = None
    sort_order: int = 0
    active: bool = True
    min_margin: Decimal | None = None
    altura: Decimal | None = None
    largura: Decimal | None = None
    comprimento: Decimal | None = None
    peso: Decimal | None = None
    duimp_padrao: str | None = None

    @field_validator("duimp_padrao", mode="before")
    @classmethod
    def _v_duimp_padrao(cls, v: Any) -> str | None:
        return _limpar_duimp_padrao(v)

    @field_validator("name", mode="before")
    @classmethod
    def _v_name(cls, v: Any) -> str:
        if v is None:
            raise ValueError("name_required")
        s = str(v).strip()
        if not s:
            raise ValueError("name_required")
        if len(s) > 128:
            raise ValueError("name_too_long")
        return s

    @field_validator("slug", mode="before")
    @classmethod
    def _v_slug(cls, v: Any) -> str | None:
        if v is None or v == "":
            return None
        s = _slugify(str(v))
        if not s:
            raise ValueError("slug_invalid")
        if len(s) > 128:
            raise ValueError("slug_too_long")
        return s


class SegmentPatch(BaseModel):
    name: str | None = None
    slug: str | None = None
    parent_id: UUID | None = None
    sort_order: int | None = None
    active: bool | None = None
    min_margin: Decimal | None = None
    altura: Decimal | None = None
    largura: Decimal | None = None
    comprimento: Decimal | None = None
    peso: Decimal | None = None
    duimp_padrao: str | None = None

    @field_validator("duimp_padrao", mode="before")
    @classmethod
    def _v_duimp_padrao(cls, v: Any) -> str | None:
        return _limpar_duimp_padrao(v)

    @field_validator("name", mode="before")
    @classmethod
    def _v_name(cls, v: Any) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            raise ValueError("name_required")
        if len(s) > 128:
            raise ValueError("name_too_long")
        return s

    @field_validator("slug", mode="before")
    @classmethod
    def _v_slug(cls, v: Any) -> str | None:
        if v is None:
            return None
        s = _slugify(str(v))
        if not s:
            raise ValueError("slug_invalid")
        if len(s) > 128:
            raise ValueError("slug_too_long")
        return s


class SegmentSpecialDateCreate(BaseModel):
    """Condição Especial (ex-"Datas Especiais"): exceção da margem do segmento.

    Condições opcionais, combinadas em E — pelo menos UMA é obrigatória:
      • período `date_start`..`date_end` (data do pedido, BRT, inclusivo; as
        duas datas juntas ou nenhuma);
      • `nome_contem`: nome do produto contém o texto (sem caixa);
      • `sku_contem`: SKU contém o texto (kit "a+b" entra, o texto está dentro).
    `min_margin` em FRAÇÃO (mesma escala de segments.min_margin: -0.15 =
    -15%); NULL = aprova qualquer margem quando a condição casa.
    """

    date_start: date | None = None
    date_end: date | None = None
    nome_contem: str | None = None
    sku_contem: str | None = None
    min_margin: Decimal | None = None

    @field_validator("nome_contem", "sku_contem", mode="before")
    @classmethod
    def _v_texto(cls, v: object) -> str | None:
        if v is None:
            return None
        texto = str(v).strip()
        if not texto:
            return None
        if len(texto) > 120:
            raise ValueError("texto_muito_longo")
        return texto

    @model_validator(mode="after")
    def _v_condicao(self) -> "SegmentSpecialDateCreate":
        if (self.date_start is None) != (self.date_end is None):
            raise ValueError("date_range_incomplete")
        if self.date_start and self.date_end and self.date_end < self.date_start:
            raise ValueError("date_range_invalid")
        if self.date_start is None and not self.nome_contem and not self.sku_contem:
            raise ValueError("condicao_vazia")
        return self


class SegmentSpecialDateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    segment_id: UUID
    date_start: date | None = None
    date_end: date | None = None
    nome_contem: str | None = None
    sku_contem: str | None = None
    min_margin: Decimal | None = None


class SegmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID | None
    parent_id: UUID | None
    name: str
    slug: str
    sort_order: int
    active: bool
    min_margin: Decimal | None = None
    altura: Decimal | None = None
    largura: Decimal | None = None
    comprimento: Decimal | None = None
    peso: Decimal | None = None
    duimp_padrao: str | None = None
    special_dates: list[SegmentSpecialDateOut] = []
    created_at: datetime
    updated_at: datetime


class SegmentTreeNode(SegmentOut):
    children: list["SegmentTreeNode"] = []


SegmentTreeNode.model_rebuild()


__all__ = [
    "SegmentCreate",
    "SegmentOut",
    "SegmentPatch",
    "SegmentSpecialDateCreate",
    "SegmentSpecialDateOut",
    "SegmentTreeNode",
]
