from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class BlingOrder(Base, TimestampMixin):
    __tablename__ = "bling_orders"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    bling_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    numero: Mapped[str | None] = mapped_column(Text, nullable=True)
    numeroloja: Mapped[str | None] = mapped_column(Text, nullable=True)
    numero_documento: Mapped[str | None] = mapped_column(Text, nullable=True)
    data: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    totalprodutos: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    total: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    situacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(Text, nullable=True)
    aprovado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    loja: Mapped[str | None] = mapped_column(Text, nullable=True)
    store_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="SET NULL"),
        nullable=True,
    )
    itens: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    valorbase: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    custofrete: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    taxacomissao: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    preco_custo: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Soma (com sinal) dos reembolsos (davinci.refunds) do pedido, replicada em
    # todas as linhas do mesmo bling_id. Alimentada pela página de Reembolso
    # (não vem do Bling), entra no lucro/margem da vw_bling_pedidos.
    reembolso: Mapped[float | None] = mapped_column(
        Float, nullable=True, server_default=text("0")
    )
    item_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    item_index: Mapped[int | None] = mapped_column(Integer, nullable=True, server_default=text("0"))
    itemvalor: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    item_codigo: Mapped[str | None] = mapped_column(Text, nullable=True)
    item_produto_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    item_descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    item_quantidade: Mapped[int | None] = mapped_column(Integer, nullable=True)
    item_desconto: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    item_comissao_base: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    item_comissao_valor: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    categoria_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    categoria_nome: Mapped[str | None] = mapped_column(Text, nullable=True)
    em_andamento_data: Mapped[date | None] = mapped_column(Date, nullable=True)
    # "Despachar até" prometido ao marketplace (horário de corte do pedido),
    # capturado pelo sweep de envio direto da API de cada plataforma (Shopee
    # ship_by_date, TikTok rts_sla_time, Amazon LatestShipDate, ML
    # /shipments/{id}/sla). Replicado em todas as linhas do mesmo bling_id.
    # NULL = ainda não capturado.
    marketplace_ship_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    verificado: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, server_default=text("false")
    )
    taxas_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    amazon_taxas_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    amazon_lookup_applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    nome_destinatario: Mapped[str | None] = mapped_column(Text, nullable=True)
    # CPF/CNPJ do cliente (contato.numeroDocumento), só dígitos. Chave do
    # casamento das etiquetas em lote. NÃO confundir com `numero_documento`,
    # que guarda o numeroPedidoCompra.
    documento_destinatario: Mapped[str | None] = mapped_column(Text, nullable=True)
    cep_destino: Mapped[str | None] = mapped_column(Text, nullable=True)
    endereco_destino: Mapped[str | None] = mapped_column(Text, nullable=True)
    numero_destino: Mapped[str | None] = mapped_column(Text, nullable=True)
    complemento_destino: Mapped[str | None] = mapped_column(Text, nullable=True)
    bairro_destino: Mapped[str | None] = mapped_column(Text, nullable=True)
    cidade_destino: Mapped[str | None] = mapped_column(Text, nullable=True)
    uf_destino: Mapped[str | None] = mapped_column(Text, nullable=True)


class PrevisaoImpressa(Base):
    """Carimbo "o papel de previsão deste pedido já saiu na impressora".

    Gravado pelo POST /estoque/pedidos/previsoes/impressas quando o operador
    clica no 🖨 da aba Pedidos (relatório 10×15 de separação antecipada). A
    tela mostra a hora ao lado do selo amarelo pra ninguém separar o mesmo
    pedido duas vezes (Eduardo, 2026-08-26). Reimpressão re-carimba
    `impressa_em`. Migration 0227.
    """

    __tablename__ = "previsao_impressa"

    # bling_orders.numero — grão de PEDIDO (o espelho bling_orders tem uma
    # linha por ITEM, por isso sem FK).
    pedido_bling: Mapped[str] = mapped_column(Text, primary_key=True)
    impressa_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
