"""Conciliação — pedido × financeiro das lojas (passo 2, 14/10/2026).

Avaliação e decisões em `docs/conciliacao-avaliacao.md`. Aqui só o formato:

* `conciliacao_lojas` — quais contas entram na conciliação e desde quando
  (`inicio`: pedidos vendidos a partir desta data entram; linha do extrato de
  pedido anterior é classificada "anterior ao início", não cobra ninguém).
* `conciliacao_movimentos` — o livro-razão: cada linha do extrato do Mercado
  Pago (Relatório de liberações) e cada cobrança/bonificação da fatura do ML,
  normalizadas, com o pedido do marketplace e o pedido do Bling resolvidos.
* `conciliacao_pedidos` — uma linha por pedido do Bling com as quatro versões
  do dinheiro (Bling, venda, fatura, extrato), o resultado (custo, reembolso,
  sobrou, margem), a situação e a etapa (aguardando → conciliado/pendência →
  finalizado → reaberto). Reconstruída pelo recálculo, com as marcas manuais
  preservadas.
* `conciliacao_sync` — controle das leituras (janela do extrato pedida/lida,
  última página da fatura por período e tipo).
* `conciliacao_dias` — o fechamento do dia por loja (saldo inicial, entradas,
  saídas, saldo final, quem fechou).

As regras ficam em `services/conciliacao_*.py`.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import IntegrationPlatform
from app.models.integration import _enum

# Origem de um movimento.
ORIGEM_EXTRATO = "mp_extrato"  # Relatório de liberações do Mercado Pago
ORIGEM_CARTEIRA = "shopee_carteira"  # carteira da Shopee (get_wallet_transaction_list)
# As origens que são "o dinheiro" (uma por plataforma); a fatura do ML é a conta de tarifas.
ORIGEM_TIKTOK = "tiktok_extrato"  # extratos (statements) diários da TikTok + repasses
ORIGEM_AMAZON = "amazon_transacoes"  # Finances API: transações dia a dia + repasses
ORIGENS_EXTRATO = (ORIGEM_EXTRATO, ORIGEM_CARTEIRA, ORIGEM_TIKTOK, ORIGEM_AMAZON)
ORIGEM_FATURA = "ml_fatura"  # detalhes de faturamento do ML por período

# Situação do pedido na conciliação (o que a coluna mostra).
SITUACOES = (
    "aguardando",  # vendeu, ainda não liberou
    "vencido",  # passou a previsão e nada entrou
    "bateu",  # liberou e o valor fechou com o previsto
    "recebido_a_menos",
    "recebido_a_mais",
    "bling_diferente",  # dinheiro ok, mas o Bling não bate com a venda
    "cancelado",  # venda desfeita: nada a receber
    "estornado_depois",  # liberou e depois saiu dinheiro (mediação, estorno, chargeback)
    "sem_venda",  # pedido do Bling sem venda do ML encontrada
    "fora_da_loja",  # saiu desta loja no Bling (apagado ou mudou de loja) — 16/10, revisão
)
# Etapa do pedido (o ciclo): ver docs, seção 12.
ETAPAS = ("aguardando", "conciliado", "pendencia", "finalizado", "reaberto")
ETAPA_PELO_ROBO = "robô"


class ConciliacaoLoja(Base, TimestampMixin):
    __tablename__ = "conciliacao_lojas"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    integration_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("integrations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    platform: Mapped[IntegrationPlatform] = mapped_column(
        _enum(IntegrationPlatform, "integration_platform"), nullable=False
    )
    conta: Mapped[str] = mapped_column(String(128), nullable=False)
    ativa: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    inicio: Mapped[date] = mapped_column(Date, nullable=False)
    # "api" = o robô lê a plataforma; "upload" = alguém envia o arquivo de transações
    # (KFA: a Amazon nega a API, caso 21731531021 — 16/10)
    modo_extrato: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'api'"), default="api"
    )
    extrato_config_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ultimo_extrato_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ultimo_fatura_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ultimo_recalculo_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ultimo_erro: Mapped[str | None] = mapped_column(Text, nullable=True)


class ConciliacaoMovimento(Base, TimestampMixin):
    __tablename__ = "conciliacao_movimentos"
    __table_args__ = (
        UniqueConstraint(
            "integration_id", "origem", "id_externo", name="uq_conciliacao_mov_externo"
        ),
        Index("ix_conciliacao_mov_loja_data", "integration_id", "data"),
        Index("ix_conciliacao_mov_pedido_mkt", "pedido_marketplace"),
        Index("ix_conciliacao_mov_pedido_bling", "pedido_bling"),
        Index("ix_conciliacao_mov_tipo", "integration_id", "tipo"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    integration_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[IntegrationPlatform] = mapped_column(
        _enum(IntegrationPlatform, "integration_platform"), nullable=False
    )
    origem: Mapped[str] = mapped_column(String(20), nullable=False)
    id_externo: Mapped[str] = mapped_column(String(80), nullable=False)
    data: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tipo: Mapped[str] = mapped_column(String(40), nullable=False)
    descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    sub_tipo: Mapped[str | None] = mapped_column(String(120), nullable=True)
    pagamento_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    pedido_marketplace: Mapped[str | None] = mapped_column(String(120), nullable=True)
    pack_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    shipping_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    pedido_bling: Mapped[str | None] = mapped_column(String(20), nullable=True)
    bling_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    bruto: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    tarifa: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    parcelamento: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    frete: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    cupom: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    credito: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0")
    )
    debito: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0")
    )
    saldo_apos: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    # Fatura: a cobrança foi descontada da própria venda (YES) ou cobrada à parte?
    descontado_da_venda: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    sku: Mapped[str | None] = mapped_column(String(500), nullable=True)
    item_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    data_aprovacao: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Classificação dada por regra ou à mão quando não há pedido (saque,
    # fatura do ML, publicidade, anterior ao início, ajuste, outro).
    classificacao: Mapped[str | None] = mapped_column(String(40), nullable=True)
    vinculado_por: Mapped[str | None] = mapped_column(String(120), nullable=True)
    vinculado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class ConciliacaoPedido(Base, TimestampMixin):
    __tablename__ = "conciliacao_pedidos"
    __table_args__ = (
        UniqueConstraint("integration_id", "pedido_bling", name="uq_conciliacao_pedido_loja"),
        Index("ix_conciliacao_ped_numeroloja", "numeroloja"),
        Index("ix_conciliacao_ped_situacao", "integration_id", "situacao"),
        Index("ix_conciliacao_ped_etapa", "integration_id", "etapa"),
        Index("ix_conciliacao_ped_data", "integration_id", "data_pedido"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    integration_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[IntegrationPlatform] = mapped_column(
        _enum(IntegrationPlatform, "integration_platform"), nullable=False
    )
    pedido_bling: Mapped[str] = mapped_column(String(20), nullable=False)
    bling_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    numeroloja: Mapped[str | None] = mapped_column(String(40), nullable=True)
    pack_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    conta: Mapped[str | None] = mapped_column(String(128), nullable=True)
    data_pedido: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    situacao_bling: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # Bling
    bling_total: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    bling_produtos: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    bling_valorbase: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    bling_custofrete: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    bling_taxacomissao: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    # Venda (marketplace_order_financials)
    venda_valor: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    venda_tarifa: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    venda_frete: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    venda_cupom: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    # comissão de afiliados (Shopee AMS / TikTok): sai da tarifa e vai pra Extras (15/10)
    venda_afiliados: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    # frete pago FORA da plataforma (Melhor Envio, envio próprio da Amazon): entra no
    # Sobrou, não no Líquido (15/10, Vinicius)
    venda_frete_pago: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    venda_estorno: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    venda_liquido: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    venda_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    previsao_liberacao: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Fatura do ML
    fatura_tarifa: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    fatura_parcelamento: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    fatura_frete: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    fatura_bonificacao: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    fatura_outros: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    # Extrato
    liberado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    liberado_valor: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    saidas_depois: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    recebido: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    ultimo_movimento_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Resultado
    custo: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    reembolso: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    sobrou: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    margem: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    # Conciliação
    dif_bling_venda: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    dif_previsto_recebido: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    situacao: Mapped[str] = mapped_column(String(30), nullable=False, server_default="aguardando")
    motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    etapa: Mapped[str] = mapped_column(String(20), nullable=False, server_default="aguardando")
    etapa_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    etapa_por: Mapped[str | None] = mapped_column(String(120), nullable=True)
    etapa_motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    bateu_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    conferido_por: Mapped[str | None] = mapped_column(String(120), nullable=True)
    conferido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    recalculado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ConciliacaoSync(Base, TimestampMixin):
    __tablename__ = "conciliacao_sync"
    __table_args__ = (
        UniqueConstraint("integration_id", "origem", "chave", name="uq_conciliacao_sync_chave"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    integration_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False
    )
    origem: Mapped[str] = mapped_column(String(20), nullable=False)
    chave: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pedido")
    arquivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    pedido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    linhas: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detalhe: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)


class ConciliacaoDia(Base, TimestampMixin):
    __tablename__ = "conciliacao_dias"
    __table_args__ = (UniqueConstraint("integration_id", "dia", name="uq_conciliacao_dia_loja"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    integration_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False
    )
    dia: Mapped[date] = mapped_column(Date, nullable=False)
    saldo_inicial: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    creditos: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    debitos: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    saldo_final: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    saldo_informado: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    linhas: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pendentes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fechado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fechado_por: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # 16/10 (revisão): fechado à mão com o saldo diferente exige motivo; reaberto por
    # quem (pessoa ou "robô") e por quê — o robô não refecha o que a pessoa reabriu
    fechado_motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    reaberto_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reaberto_por: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reaberto_motivo: Mapped[str | None] = mapped_column(Text, nullable=True)


class ConciliacaoFretePago(Base, TimestampMixin):
    """Um envio pago fora da plataforma (Melhor Envio), casado com o pedido do Bling
    pela etiqueta `mi:reference_code` (15/10/2026). `frete_pago` = preço − desconto +
    conferência de peso cobrada a mais."""

    __tablename__ = "conciliacao_fretes_pagos"
    __table_args__ = (UniqueConstraint("fonte", "envio_id", name="uq_conciliacao_frete_envio"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    integration_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("integrations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    fonte: Mapped[str] = mapped_column(String(20), nullable=False, server_default="melhor_envio")
    envio_id: Mapped[str] = mapped_column(String(80), nullable=False)
    pedido_bling: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    pedido_marketplace: Mapped[str | None] = mapped_column(String(40), nullable=True)
    protocolo: Mapped[str | None] = mapped_column(String(60), nullable=True)
    status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    servico: Mapped[str | None] = mapped_column(String(80), nullable=True)
    rastreio: Mapped[str | None] = mapped_column(String(60), nullable=True)
    preco: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    desconto: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    conferencia: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    frete_pago: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    pago_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    postado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    entregue_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
