from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# "Sucata" (10/09): reembolso automático da devolução em condição Sucata — mesmo
# padrão do Extraviado (prejuízo = custo do produto).
RefundTipo = Literal["Logistica", "Cliente", "Manutenção", "Extraviado", "Sucata", "Frete"]


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _clean_optional_number(value: object) -> object:
    """Empty/whitespace strings -> None for optional float fields.
    Frontend number inputs (v-model.number) can emit '' when cleared, which
    would otherwise 422. Real numbers (and numeric strings) pass through to
    pydantic's normal coercion."""
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _clamp_cliente_reembolso(tipo: str | None, reembolso: float | None) -> float | None:
    """Mirror frontend behavior: when tipo='Cliente', reembolso must be <= 0.
    Positive values are auto-negated. Negative/null/zero pass through."""
    if tipo == "Cliente" and reembolso is not None and reembolso > 0:
        return -reembolso
    return reembolso


class RefundOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    data: datetime | None = None
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    plataforma: str | None = None
    conta: str
    tipo: RefundTipo | None = None
    prejuizo: float | None = None
    reembolso: float | None = None
    chamado: str | None = None
    chamado_url: str | None = None
    chamado_resolvido: bool = False
    operacao: str | None = None
    conferido: bool
    conferido_at: datetime | None = None
    # Quando o valor do Reembolso foi lançado no DaVinci (coluna "Reembolso em").
    reembolso_at: datetime | None = None
    observacao: str | None = None
    created_at: datetime
    updated_at: datetime
    # Situacao ATUAL do pedido no Bling (lookup em bling_orders na listagem;
    # nao persiste no refund).
    situacao_bling: str | None = None


class RefundCreate(BaseModel):
    data: datetime | None = None
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    plataforma: str | None = None
    conta: str = Field(min_length=1)
    tipo: RefundTipo | None = None
    prejuizo: float | None = None
    reembolso: float | None = None
    chamado: str | None = None
    chamado_url: str | None = None
    chamado_resolvido: bool = False
    operacao: str | None = None
    observacao: str | None = None

    @field_validator(
        "pedido_bling",
        "pedido_marketplace",
        "plataforma",
        "tipo",
        "chamado",
        "chamado_url",
        "operacao",
        "observacao",
        mode="before",
    )
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        return _clean_optional_text(value)

    @field_validator("prejuizo", "reembolso", mode="before")
    @classmethod
    def clean_optional_number(cls, value: object) -> object:
        return _clean_optional_number(value)

    @field_validator("conta")
    @classmethod
    def clean_conta(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("conta cannot be blank")
        return value

    @model_validator(mode="after")
    def _enforce_cliente_reembolso(self) -> "RefundCreate":
        self.reembolso = _clamp_cliente_reembolso(self.tipo, self.reembolso)
        return self


class RefundPatch(BaseModel):
    data: datetime | None = None
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    plataforma: str | None = None
    conta: str | None = None
    tipo: RefundTipo | None = None
    prejuizo: float | None = None
    reembolso: float | None = None
    chamado: str | None = None
    chamado_url: str | None = None
    chamado_resolvido: bool | None = None
    operacao: str | None = None
    conferido: bool | None = None
    observacao: str | None = None

    @field_validator(
        "pedido_bling",
        "pedido_marketplace",
        "plataforma",
        "conta",
        "tipo",
        "chamado",
        "chamado_url",
        "operacao",
        "observacao",
        mode="before",
    )
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        return _clean_optional_text(value)

    @field_validator("prejuizo", "reembolso", mode="before")
    @classmethod
    def clean_optional_number(cls, value: object) -> object:
        return _clean_optional_number(value)


class RefundPage(BaseModel):
    items: list[RefundOut]
    total: int
    limit: int
    offset: int
    platforms: list[str]
    # Totais do CONJUNTO FILTRADO INTEIRO (não só a página carregada) — batem
    # com a linha Reembolso do quadro Operacional quando o filtro é conferido_at.
    total_prejuizo: float
    total_reembolso: float
    total_a_conferir: int


class RefundLookupOut(BaseModel):
    data: datetime | None = None
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    plataforma: str | None = None
    conta: str
    custo_produto: float | None = None
    custo_manutencao: float | None = None


class RefundExistenteOut(BaseModel):
    """Reembolso que JÁ existe para o pedido consultado, para a tela mostrar
    antes de adicionar outro. Um pedido pode ter vários lançamentos legítimos
    (tipos diferentes, contas diferentes), então o aviso precisa dizer QUAIS
    são — só o número treina o operador a ignorar."""

    data: datetime | None = None
    conta: str | None = None
    tipo: str | None = None
    reembolso: float | None = None
    conferido: bool = False
    criado_por: str | None = None
    # Lançamento de equipe que o usuário não enxerga: a tela avisa que existe,
    # com a data, mas sem valor, conta nem autor — o suficiente para ele parar e
    # perguntar, sem expor número de outra equipe.
    de_outra_equipe: bool = False


class RefundLookupPage(BaseModel):
    items: list[RefundLookupOut]
    # Lookup-only: True when the recent conciliation view missed the order but
    # bling_orders has it, so the frontend can offer the slow history search.
    historico_disponivel: bool = False
    # O que JÁ existe para esse pedido (qualquer autor, qualquer equipe, e
    # também o que já foi finalizado). Ignora o escopo de propósito: os dois
    # caminhos que escondem um lançamento são justamente a equipe e o filtro
    # "a finalizar", e é deles que o operador precisa ser avisado.
    reembolsos_existentes: int = 0
    reembolsos_do_pedido: list[RefundExistenteOut] = []


class RefundOrderCostOut(BaseModel):
    pedido_bling: str
    conta: str
    custo_produto: float | None = None
