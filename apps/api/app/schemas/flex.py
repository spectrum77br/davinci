"""Respostas da API do Flex por anúncio (routers/flex.py)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class FlexContaOut(BaseModel):
    """Uma conta de `flex_contas`. `existe=False`: o id não é de uma conta
    ML/Shopee ativa (digitado errado, arquivada, outra plataforma)."""

    id: UUID
    nome: str | None = None
    plataforma: str | None = None
    existe: bool = True


class FlexConfigOut(BaseModel):
    """A configuração em vigor — os números como o motor os usa (já
    normalizados). Nenhum segredo."""

    modo: str
    pode_escrever: bool
    contas: list[FlexContaOut]
    n_liga: int
    n_desliga: int
    kits: bool
    max_anuncios_por_familia: int
    teto_escritas_por_rodada: int
    shopee_canais: list[str]
    shopee_escrita: bool
    intervalo_min: int
    pedido_no_sp: bool


class FlexAnuncioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    integration_id: UUID
    conta: str | None = None
    external_id: str
    plataforma: str
    titulo: str | None = None
    desejado: str
    motivo: str | None = None
    observado: str | None = None
    observado_em: datetime | None = None
    aguardando_aprovacao: bool
    aprovado_por: UUID | None = None
    aprovado_em: datetime | None = None
    aplicado_em: datetime | None = None
    tentativas: int
    proxima_tentativa: datetime | None = None
    ultimo_erro: str | None = None
    recusa: str | None = None
    saldo_sp: int | None = None
    familias: str | None = None
    atualizado_em: datetime


class FlexAnunciosOut(BaseModel):
    total: int
    itens: list[FlexAnuncioOut]


class FlexAprovarOut(BaseModel):
    aprovado: bool
    modo: str
    aplicado: bool
    estado: FlexAnuncioOut | None = None
    motor: dict[str, Any] | None = None


class FlexSincronizarOut(BaseModel):
    enfileirado: bool
    modo: str
    motivo: str | None = None


class FlexLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    criado_em: datetime
    integration_id: UUID | None = None
    conta: str | None = None
    external_id: str | None = None
    plataforma: str | None = None
    bling_id: int | None = None
    sku: str | None = None
    saldo_sp: int | None = None
    acao: str
    estado_antes: str | None = None
    estado_depois: str | None = None
    modo: str
    resultado: str
    erro: str | None = None
    motivo: str | None = None
    por: UUID | None = None


class FlexPedidoOut(BaseModel):
    """Pedido Flex detectado (flex_pedido) com o que o Bling diz dele."""

    bling_id: int
    numero: str | None = None
    plataforma: str
    integration_id: UUID | None = None
    conta: str | None = None
    numeroloja: str | None = None
    envio_tipo: str | None = None
    prazo: datetime | None = None
    detectado_em: datetime
    no_sp: bool
    alerta: str | None = None
    situacao: str | None = None
    skus: list[str] = []
