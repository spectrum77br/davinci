"""Entradas e saídas de Cadastros › Contabilidade › Compra (23/10/2026)."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.nfse import _digitos, _dinheiro_br, _numero_br

_MAX_UNITARIO = Decimal("9999999.99")
_MAX_QUANTIDADE = 1_000_000
UFS = frozenset(
    "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split()
)
_MILHAR_QTD = re.compile(r"[1-9]\d{0,2}(?:\.\d{3})+")


def _texto(v: object) -> object:
    if isinstance(v, str):
        v = re.sub(r"\s+", " ", v).strip()
        return v or None
    return v


def _ncm(v: object) -> str:
    d = _digitos(v) if isinstance(v, str) else None
    if not d or len(d) != 8:
        raise ValueError("O NCM tem 8 números (ex.: 3923.21.10).")
    return d


def _cfop(v: object) -> str | None:
    d = _digitos(v) if isinstance(v, str) else None
    if d is None:
        return None
    if len(d) != 4 or d[0] not in "1234567":
        raise ValueError(
            "O CFOP tem 4 números (ex.: 5102 no mesmo estado, 6102 para outro estado)."
        )
    return d


# --- produtos -------------------------------------------------------------------


class ProdutoIn(BaseModel):
    nome: str = Field(min_length=1, max_length=120)
    sku: str = Field(min_length=1, max_length=60)
    ncm: str
    cfop: str | None = None
    unidade: str = Field(default="UN", min_length=1, max_length=6)
    preco_padrao: Decimal | None = Field(default=None, gt=0, le=_MAX_UNITARIO, decimal_places=2)
    ativo: bool = True

    _txt = field_validator("nome", "sku", mode="before")(_texto)
    _din = field_validator("preco_padrao", mode="before")(_dinheiro_br)

    @field_validator("ncm", mode="before")
    @classmethod
    def _v_ncm(cls, v: object) -> str:
        return _ncm(v)

    @field_validator("cfop", mode="before")
    @classmethod
    def _v_cfop(cls, v: object) -> str | None:
        return _cfop(v)

    @field_validator("unidade", mode="before")
    @classmethod
    def _v_un(cls, v: object) -> object:
        v = _texto(v)
        return v.upper() if isinstance(v, str) else (v or "UN")


class ProdutoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nome: str
    sku: str
    ncm: str
    cfop: str | None
    unidade: str
    preco_padrao: Decimal | None
    ativo: bool
    usado: int = 0  # em quantas compras aparece (não dá pra apagar se > 0)


# --- quem compra / quem vende ---------------------------------------------------------


class Endereco(BaseModel):
    # Tamanhos da NF-e (xLgr/xCpl/xBairro/xMun até 60; bairro com 2 ou mais —
    # bairro de 1 letra é recusado na SEFAZ, memória avulso_bairro_curto).
    logradouro: str | None = Field(default=None, min_length=2, max_length=60)
    numero: str | None = Field(default=None, max_length=60)
    complemento: str | None = Field(default=None, max_length=60)
    bairro: str | None = Field(default=None, min_length=2, max_length=60)
    cep: str | None = None
    municipio: str | None = Field(default=None, min_length=2, max_length=60)
    uf: str | None = None

    _txt = field_validator(
        "logradouro", "numero", "complemento", "bairro", "municipio", mode="before"
    )(_texto)

    @field_validator("cep", mode="before")
    @classmethod
    def _v_cep(cls, v: object) -> str | None:
        d = _digitos(v) if isinstance(v, str) else None
        if d is None:
            return None
        if len(d) != 8:
            raise ValueError("O CEP tem 8 números.")
        return d

    @field_validator("uf", mode="before")
    @classmethod
    def _v_uf(cls, v: object) -> str | None:
        v = _texto(v)
        if v is None:
            return None
        v = str(v).upper()
        if v not in UFS:
            raise ValueError("UF inválida (2 letras de um estado, ex.: SP).")
        return v


class CompradorOut(BaseModel):
    company_id: UUID
    razao_social: str
    apelido: str | None
    cnpj: str
    inscricao_estadual: str | None
    endereco: Endereco
    # De onde veio o endereço: "nfeio" (cadastro da NFE.io), "nota" (a última NF
    # que a própria empresa emitiu) ou None (não achou — digitar na tela).
    endereco_origem: Literal["nfeio", "nota"] | None


class VendedoraOut(BaseModel):
    cnpj: str
    nome: str
    uf: str | None
    municipio: str | None
    notas_90d: int
    ultima: datetime | None
    adspower_serial: str | None = None
    automatico: bool = False
    ativo: bool = True
    observacao: str | None = None


class FaturamentoOut(BaseModel):
    company_id: UUID
    competencia: date
    valor: Decimal
    pedidos: int
    lojas: list[dict]
    tem_loja: bool  # False = a empresa não tem loja com o CNPJ dela (faturamento desconhecido)


class OpcoesOut(BaseModel):
    vendedoras: list[VendedoraOut]
    produtos: list[ProdutoOut]


# --- compras ----------------------------------------------------------------------------


class ItemIn(BaseModel):
    produto_id: UUID | None = None
    nome: str = Field(min_length=1, max_length=120)
    sku: str = Field(min_length=1, max_length=60)
    ncm: str
    unidade: str = Field(default="UN", min_length=1, max_length=6)
    quantidade: int = Field(gt=0, le=_MAX_QUANTIDADE)
    valor_unitario: Decimal = Field(gt=0, le=_MAX_UNITARIO, decimal_places=2)

    _txt = field_validator("nome", "sku", mode="before")(_texto)
    _din = field_validator("valor_unitario", mode="before")(_dinheiro_br)

    @field_validator("ncm", mode="before")
    @classmethod
    def _v_ncm(cls, v: object) -> str:
        return _ncm(v)

    @field_validator("unidade", mode="before")
    @classmethod
    def _v_un(cls, v: object) -> object:
        v = _texto(v)
        return v.upper() if isinstance(v, str) else (v or "UN")

    @field_validator("quantidade", mode="before")
    @classmethod
    def _v_qtd(cls, v: object) -> object:
        # "450" / "1.000" (milhar) / 450 — nunca fração de embalagem ("1.5" e
        # "4.50" são recusados, não viram 15 e 450).
        if isinstance(v, str):
            s = v.strip()
            if _MILHAR_QTD.fullmatch(s):
                s = s.replace(".", "")
            if not s.isdigit():
                raise ValueError("A quantidade é um número inteiro (ex.: 450 ou 1.000).")
            return int(s)
        if isinstance(v, float) and not v.is_integer():
            raise ValueError("A quantidade é um número inteiro (ex.: 450).")
        return v


class CompraIn(BaseModel):
    company_id: UUID
    vendedor_cnpj: str
    competencia: date
    data_pedido: date | None = None  # vazio = hoje
    modo: Literal["quantidade", "percentual"] = "quantidade"
    percentual: Decimal | None = Field(default=None, gt=0, le=100, decimal_places=4)
    cfop: str | None = None  # vazio = 5102 / 6102 pela UF
    inscricao_estadual: str | None = Field(default=None, max_length=20)
    endereco: Endereco | None = None  # vazio = o endereço que o DaVinci achou
    observacoes: str | None = Field(default=None, max_length=500)
    itens: list[ItemIn] = Field(min_length=1, max_length=20)

    _pct = field_validator("percentual", mode="before")(_numero_br)
    _obs = field_validator("observacoes", mode="before")(_texto)

    @field_validator("vendedor_cnpj", mode="before")
    @classmethod
    def _v_cnpj(cls, v: object) -> str:
        d = _digitos(v) if isinstance(v, str) else None
        if not d or len(d) != 14:
            raise ValueError("Escolha a empresa que vende (CNPJ com 14 números).")
        return d

    @field_validator("cfop", mode="before")
    @classmethod
    def _v_cfop(cls, v: object) -> str | None:
        return _cfop(v)

    @field_validator("inscricao_estadual", mode="before")
    @classmethod
    def _v_ie(cls, v: object) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
        if s.upper() in ("ISENTO", "ISENTA"):
            return "ISENTO"
        d = _digitos(s)
        if not d:
            raise ValueError("A inscrição estadual tem só números (ou ISENTO).")
        return d


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    produto_id: UUID | None
    nome: str
    sku: str
    ncm: str
    unidade: str
    quantidade: int
    valor_unitario: Decimal
    valor_total: Decimal


class CompraOut(BaseModel):
    id: UUID
    numero: int
    company_id: UUID
    comprador: dict
    vendedor_cnpj: str
    vendedor_nome: str
    vendedor_uf: str | None
    competencia: date
    data_pedido: date
    faturamento: Decimal | None
    modo: str
    percentual: Decimal | None
    cfop: str
    total: Decimal
    status: str
    nf_numero: str | None
    nf_chave: str | None = None
    emissao_pedida_em: datetime | None = None
    emissao_iniciada_em: datetime | None = None
    emissao_tentativas: int = 0
    emissao_erro: str | None = None
    nf_emitida_em: datetime | None = None
    observacoes: str | None
    itens: list[ItemOut]
    created_at: datetime
    updated_at: datetime
    criado_por: str | None = None


class NfIn(BaseModel):
    nf_numero: str = Field(min_length=1, max_length=20)

    @field_validator("nf_numero", mode="before")
    @classmethod
    def _v_nf(cls, v: object) -> str:
        d = _digitos(v) if isinstance(v, str) else (str(v) if isinstance(v, int) else None)
        if not d:
            raise ValueError("Digite o número da NF (só números).")
        return d.lstrip("0") or "0"


class PlanilhaIn(BaseModel):
    ids: list[UUID] = Field(min_length=1, max_length=200)
