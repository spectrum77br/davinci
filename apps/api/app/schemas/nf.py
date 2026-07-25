from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class NfFaturadorOut(BaseModel):
    id: UUID
    nome: str
    modo: str
    nf_cheia: bool
    percentual: Decimal | None = None
    sku_fonte: str | None = None
    nome_fonte: str | None = None
    ncm: str | None = None
    ads_power: str | None = None
    usuario: str | None = None
    # A senha nunca é devolvida; só sinaliza se está preenchida.
    has_senha: bool = False
    observacao: str | None = None
    sort_order: int
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


class NfFaturadorCreate(BaseModel):
    nome: str = Field(min_length=1)
    modo: str = Field(min_length=1)
    nf_cheia: bool = False
    percentual: Decimal | None = None
    sku_fonte: str | None = None
    nome_fonte: str | None = None
    ncm: str | None = None
    ads_power: str | None = None
    usuario: str | None = None
    senha: str | None = None
    observacao: str | None = None
    sort_order: int | None = None


class NfFaturadorPatch(BaseModel):
    nome: str | None = Field(default=None, min_length=1)
    modo: str | None = Field(default=None, min_length=1)
    nf_cheia: bool | None = None
    percentual: Decimal | None = None
    sku_fonte: str | None = None
    nome_fonte: str | None = None
    ncm: str | None = None
    ads_power: str | None = None
    usuario: str | None = None
    # None = não altera; "" = limpa a senha.
    senha: str | None = None
    observacao: str | None = None
    sort_order: int | None = None


class NfEtiquetaOut(BaseModel):
    id: UUID
    plataforma: str
    modo: str | None = None
    ads_power: str | None = None
    observacao: str | None = None
    sort_order: int
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


class NfEtiquetaCreate(BaseModel):
    plataforma: str = Field(min_length=1)
    modo: str | None = None
    ads_power: str | None = None
    observacao: str | None = None
    sort_order: int | None = None


class NfEtiquetaPatch(BaseModel):
    plataforma: str | None = Field(default=None, min_length=1)
    modo: str | None = None
    ads_power: str | None = None
    observacao: str | None = None
    sort_order: int | None = None


class NfImpressaoOut(BaseModel):
    id: UUID
    tipo: str
    observacao: str | None = None
    visualizacao: str | None = None
    usa_etiqueta: bool
    usa_declaracao: bool
    usa_nota: bool
    sort_order: int
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


class NfImpressaoCreate(BaseModel):
    tipo: str = Field(min_length=1)
    observacao: str | None = None
    visualizacao: str | None = None
    usa_etiqueta: bool = False
    usa_declaracao: bool = False
    usa_nota: bool = False
    sort_order: int | None = None


class NfImpressaoPatch(BaseModel):
    tipo: str | None = Field(default=None, min_length=1)
    observacao: str | None = None
    visualizacao: str | None = None
    usa_etiqueta: bool | None = None
    usa_declaracao: bool | None = None
    usa_nota: bool | None = None
    sort_order: int | None = None


class NfFaturamentoRowOut(BaseModel):
    """Uma linha do Painel de Faturamento: descrição do pedido + os 3 status de
    etapa. Derivada (read-only) de bling_orders × store_info × nf_faturamento —
    não é o shape da tabela, é a linha exibida no painel."""

    data: date | None = None
    pedido_bling: str
    pedido_marketplace: str | None = None
    plataforma: str | None = None
    conta: str | None = None
    status_bling: str | None = None
    # 'pendente' quando não há registro em nf_faturamento; senão 'ok'/'erro'/...
    status_faturamento: str = "pendente"
    erro_faturamento: str | None = None
    status_etiqueta: str = "pendente"
    erro_etiqueta: str | None = None
    status_impressao: str = "pendente"
    erro_impressao: str | None = None
