"""Entradas e saídas da aba Emissão de Serviço (NFS-e pela NFE.io, 29/09/2026)."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator


def _digitos(v: str | None) -> str | None:
    if v is None:
        return None
    d = re.sub(r"\D", "", v)
    return d or None


# --- números digitados (valor, base, percentual) ---------------------------------
# As mensagens vão direto pra tela (o front tira o "Value error, "): português
# simples, sem jargão do Pydantic.

_MAX_DINHEIRO = Decimal("9999999999999.99")  # NUMERIC(15, 2)


def _numero_br(v: object) -> object:
    """Texto vazio vira None; "0,5" (vírgula, sem ponto) vira "0.5"."""
    if isinstance(v, str):
        v = v.strip()
        if not v:
            return None
        if "," in v and "." not in v:
            v = v.replace(",", ".")
    return v


# "200.000" / "1.000.000" sem vírgula: pode ser 200 mil (ponto de milhar pt-BR,
# o jeito que o Eduardo escreve) ou 200 com 3 casas. O front já manda o decimal
# ("200000.00"); aqui, na dúvida, recusa — nunca lê 200 mil como R$ 200,00.
_MILHAR = re.compile(r"[1-9]\d{0,2}(?:\.\d{3})+")


def _dinheiro_br(v: object) -> object:
    """Dinheiro (valor, base): como _numero_br e mais "1.500,00" → "1500.00".
    Nunca no percentual: lá "0.500" é 0,5%."""
    if isinstance(v, str):
        v = v.strip()
        if not v:
            return None
        if "," in v:
            return v.replace(".", "").replace(",", ".")
        if _MILHAR.fullmatch(v):
            raise ValueError(
                f'Não deu pra saber se "{v}" tem ponto de milhar. '
                "Escreva com a vírgula dos centavos (ex.: 200.000,00)."
            )
    return v


def _casas(v: Decimal) -> int:
    exp = v.normalize().as_tuple().exponent
    return max(0, -exp) if isinstance(exp, int) else 0


def checar_dinheiro(v: Decimal | None, rotulo: str) -> Decimal | None:
    if v is None:
        return None
    if not v.is_finite() or v <= 0:
        raise ValueError(f"{rotulo} tem que ser maior que zero.")
    if _casas(v) > 2:
        raise ValueError(f"{rotulo} vai só até os centavos (2 casas depois da vírgula).")
    if v > _MAX_DINHEIRO:
        raise ValueError(f"{rotulo} está grande demais.")
    return v.quantize(Decimal("0.01"))  # "1500" → 1500.00, igual ao que volta do banco


def checar_percentual(v: Decimal | None, rotulo: str = "O percentual") -> Decimal | None:
    """NUMERIC(9, 4), > 0 e <= 100. Também a porcentagem da empresa
    (`companies.percentual_servico`, Cadastros › Empresas)."""
    if v is None:
        return None
    if not v.is_finite() or v <= 0:
        raise ValueError(f"{rotulo} tem que ser maior que zero.")
    if v > 100:
        raise ValueError(f"{rotulo} vai no máximo até 100%.")
    if _casas(v) > 4:
        raise ValueError(f"{rotulo} aceita até 4 casas depois da vírgula (ex.: 0,5 ou 0,1234).")
    return v.quantize(Decimal("0.0001"))  # 0.5 → 0.5000, igual ao que volta do banco


def _codigo(v: object) -> object:
    """Código do serviço: texto livre curto ("6303", "10.05"); vazio vira None."""
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


class FiscalIn(BaseModel):
    """O que é nosso na empresa. O resto (ambiente, regime, IM, certificado)
    vem da NFE.io e é só leitura (`NfeioOut`)."""

    city_service_code: str | None = Field(default="6303", max_length=30)
    federal_service_code: str | None = Field(default="10.05", max_length=30)
    c_nbs: str | None = "102010000"
    retencao_ir: Literal["auto", "sempre", "nunca"] = "auto"
    email: str | None = None
    fone: str | None = None

    @field_validator("city_service_code", "federal_service_code", mode="before")
    @classmethod
    def _codigos(cls, v: object) -> object:
        return _codigo(v)

    @field_validator("c_nbs", "fone", mode="before")
    @classmethod
    def _so_digitos(cls, v: str | None) -> str | None:
        return _digitos(v) if isinstance(v, str) else v

    @field_validator("c_nbs")
    @classmethod
    def _nbs(cls, v: str | None) -> str | None:
        if v is not None and len(v) != 9:
            raise ValueError("O código NBS tem 9 dígitos (ex.: 102010000).")
        return v

    @field_validator("email", mode="before")
    @classmethod
    def _email(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip() or None
        return v


class FiscalOut(BaseModel):
    city_service_code: str | None
    federal_service_code: str | None
    c_nbs: str | None
    retencao_ir: Literal["auto", "sempre", "nunca"]
    email: str | None
    fone: str | None


class NfeioOut(BaseModel):
    """A empresa na NFE.io (só leitura; vem do sincronizar/atualizar)."""

    company_id: str
    link: str
    ambiente: str | None
    teste: bool
    status_fiscal: str | None
    regime: str | None
    retem_ir: bool
    inscricao_municipal: str | None
    municipio: str | None
    uf: str | None
    cert_status: str | None
    cert_expira: date | None
    sincronizado_em: datetime | None


class PrestadorOut(BaseModel):
    company_id: UUID
    apelido: str
    razao_social: str
    cnpj: str | None
    # A porcentagem da empresa (Cadastros › Empresas): o % padrão das notas de
    # percentual dela. Só leitura aqui.
    percentual_servico: Decimal | None = None
    fiscal: FiscalOut | None
    nfeio: NfeioOut | None
    pronto: bool
    pendencias: list[str]
    avisos: list[str]


class LigarIn(BaseModel):
    """Id da empresa na NFE.io, o link colado do painel ou o CNPJ (vazio = o
    CNPJ da própria empresa)."""

    ref: str = Field(default="", max_length=300)


class TomadorIn(BaseModel):
    tipo: Literal["grupo", "externo"]
    company_id: UUID | None = None
    documento: str | None = None
    nome: str | None = None
    email: str | None = None
    fone: str | None = None
    cep: str | None = None
    cmun_ibge: str | None = None
    logradouro: str | None = None
    numero: str | None = None
    complemento: str | None = None
    bairro: str | None = None
    ativo: bool = True

    @field_validator("documento", "cep", "cmun_ibge", "fone", mode="before")
    @classmethod
    def _so_digitos(cls, v: str | None) -> str | None:
        return _digitos(v) if isinstance(v, str) else v


class TomadorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tipo: str
    company_id: UUID | None
    documento: str | None
    nome: str | None
    email: str | None
    fone: str | None
    cep: str | None
    cmun_ibge: str | None
    logradouro: str | None
    numero: str | None
    complemento: str | None
    bairro: str | None
    ativo: bool
    # Do grupo: o que vai na nota (vem da empresa).
    nome_nota: str | None = None
    documento_nota: str | None = None


class ModeloIn(BaseModel):
    """Nota fixa. 29/09: `tipo_valor` 'fixo' (valor) ou 'percentual' (percentual
    sobre a base digitada na hora de emitir; `base_padrao` só sugere a base).
    O que não é do tipo escolhido é descartado (vira null). No percentual,
    `percentual` null = usa a porcentagem da empresa (Cadastros › Empresas)."""

    company_id: UUID
    tomador_id: UUID
    nome: str = Field(min_length=1, max_length=200)
    descricao: str = Field(min_length=1, max_length=2000)
    # tipo_valor antes dos números: os validadores deles leem o tipo.
    tipo_valor: Literal["fixo", "percentual"] = "fixo"
    valor: Decimal | None = Field(default=None, validate_default=True)
    percentual: Decimal | None = Field(default=None, validate_default=True)
    base_padrao: Decimal | None = None
    # Preenchidos, sobrepõem os códigos da empresa.
    city_service_code: str | None = Field(default=None, max_length=30)
    federal_service_code: str | None = Field(default=None, max_length=30)
    c_nbs: str | None = None
    inf_comp: str | None = Field(default=None, max_length=2000)
    ativo: bool = True
    ordem: int = 0

    @field_validator("city_service_code", "federal_service_code", mode="before")
    @classmethod
    def _codigos(cls, v: object) -> object:
        return _codigo(v)

    @field_validator("c_nbs", mode="before")
    @classmethod
    def _so_digitos(cls, v: str | None) -> str | None:
        return _digitos(v) if isinstance(v, str) else v

    @field_validator("valor", "base_padrao", mode="before")
    @classmethod
    def _dinheiro(cls, v: object) -> object:
        return _dinheiro_br(v)

    @field_validator("percentual", mode="before")
    @classmethod
    def _numero(cls, v: object) -> object:
        return _numero_br(v)

    @field_validator("valor")
    @classmethod
    def _valor(cls, v: Decimal | None, info: ValidationInfo) -> Decimal | None:
        if info.data.get("tipo_valor", "fixo") != "fixo":
            return None
        if v is None:
            raise ValueError("Digite o valor da nota.")
        return checar_dinheiro(v, "O valor")

    @field_validator("percentual")
    @classmethod
    def _percentual(cls, v: Decimal | None, info: ValidationInfo) -> Decimal | None:
        if info.data.get("tipo_valor") != "percentual":
            return None
        return checar_percentual(v)  # None = usa a % da empresa

    @field_validator("base_padrao")
    @classmethod
    def _base_padrao(cls, v: Decimal | None, info: ValidationInfo) -> Decimal | None:
        if info.data.get("tipo_valor") != "percentual":
            return None
        return checar_dinheiro(v, "A base de cálculo")


class ModeloOut(ModeloIn):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    prestador_nome: str | None = None
    tomador_nome: str | None = None


class ItemIn(BaseModel):
    """Uma nota: de um modelo (valor pode mudar no mês) ou avulsa.

    Percentual (29/09): nota fixa de percentual usa `base_calculo` (ou a base
    sugerida do modelo) e `percentual` (ou o do modelo, ou o da empresa); avulsa
    manda OU `valor`, OU `base_calculo` + `percentual` (sem `percentual`, o da
    empresa). O servidor faz a conta."""

    modelo_id: UUID | None = None
    company_id: UUID | None = None
    tomador_id: UUID | None = None
    descricao: str | None = None
    valor: Decimal | None = None
    base_calculo: Decimal | None = None
    percentual: Decimal | None = None
    city_service_code: str | None = Field(default=None, max_length=30)
    federal_service_code: str | None = Field(default=None, max_length=30)
    c_nbs: str | None = None
    inf_comp: str | None = None

    @field_validator("city_service_code", "federal_service_code", mode="before")
    @classmethod
    def _codigos(cls, v: object) -> object:
        return _codigo(v)

    @field_validator("c_nbs", mode="before")
    @classmethod
    def _so_digitos(cls, v: str | None) -> str | None:
        return _digitos(v) if isinstance(v, str) else v

    @field_validator("valor", "base_calculo", mode="before")
    @classmethod
    def _dinheiro(cls, v: object) -> object:
        return _dinheiro_br(v)

    @field_validator("percentual", mode="before")
    @classmethod
    def _numero(cls, v: object) -> object:
        return _numero_br(v)

    @field_validator("valor")
    @classmethod
    def _valor(cls, v: Decimal | None) -> Decimal | None:
        return checar_dinheiro(v, "O valor")

    @field_validator("base_calculo")
    @classmethod
    def _base(cls, v: Decimal | None) -> Decimal | None:
        return checar_dinheiro(v, "A base de cálculo")

    @field_validator("percentual")
    @classmethod
    def _percentual(cls, v: Decimal | None) -> Decimal | None:
        return checar_percentual(v)


class PreviaIn(BaseModel):
    competencia: date
    itens: list[ItemIn] = Field(min_length=1, max_length=200)


class EmitirIn(BaseModel):
    """Sem senha do certificado desde 29/09: quem assina é a NFE.io. Continuam a
    permissão `emissao_servico`, o "digite EMITIR" e a trava de produção."""

    competencia: date
    item: ItemIn


class CancelarIn(BaseModel):
    c_motivo: Literal[1, 2, 9]
    x_motivo: str = Field(min_length=15, max_length=255)


class AtualizarIn(BaseModel):
    ids: list[UUID] = Field(min_length=1, max_length=200)


class EmissaoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    tomador_id: UUID | None
    modelo_id: UUID | None
    competencia: date
    status: str
    descricao: str
    valor_servico: Decimal
    # Nota de percentual: a base digitada e o % (valor_servico = base × % ÷ 100).
    base_calculo: Decimal | None = None
    percentual: Decimal | None = None
    provedor: str = "nfeio"
    nfeio_id: str | None = None
    nfeio_ambiente: str | None = None
    teste: bool = False
    flow_status: str | None = None
    flow_message: str | None = None
    check_code: str | None = None
    rps_serie: str | None = None
    rps_numero: int | None = None
    ir_retido: Decimal | None = None
    chave_acesso: str | None
    n_nfse: str | None
    dh_emi: datetime | None
    dh_proc: datetime | None
    v_bc: Decimal | None
    p_aliq_aplic: Decimal | None
    v_issqn: Decimal | None
    v_liq: Decimal | None
    snapshot: dict
    alertas: list | None
    erros: list | None
    tentativas: int
    enviado_em: datetime | None
    cancelada_em: datetime | None
    created_at: datetime
    prestador_nome: str | None = None
    tomador_nome: str | None = None


class ReceitaOut(BaseModel):
    """O que a Receita (via BrasilAPI) diz da empresa — só sugestão, nada é gravado."""

    cnpj: str
    razao_social: str | None
    municipio_nome: str | None
    uf: str | None
    cmun_ibge: str | None
    simples: bool | None
    mei: bool | None
    # 2 MEI · 3 Simples (não MEI) · 1 nenhum dos dois · None = não dá pra saber.
    op_simp_nac_sugerido: Literal[1, 2, 3] | None
    fonte: str


class MunicipioOut(BaseModel):
    cmun_ibge: str
    nome: str
    uf: str
