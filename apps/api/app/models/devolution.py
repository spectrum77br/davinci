from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Devolution(Base, TimestampMixin):
    __tablename__ = "devolutions"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    data: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pedido_bling: Mapped[str | None] = mapped_column(Text, nullable=True)
    pedido_marketplace: Mapped[str | None] = mapped_column(Text, nullable=True)
    conta: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    sku: Mapped[str | None] = mapped_column(Text, nullable=True)
    produtos: Mapped[str | None] = mapped_column(Text, nullable=True)
    custo_produto: Mapped[float | None] = mapped_column(Float, nullable=True)
    condicao_produto: Mapped[str | None] = mapped_column(Text, nullable=True)
    link_abertura: Mapped[str | None] = mapped_column(Text, nullable=True)
    reembolso: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    motivo_devolucao: Mapped[str | None] = mapped_column(Text, nullable=True)
    custo_manutencao: Mapped[float | None] = mapped_column(Float, nullable=True)
    tecnico: Mapped[str | None] = mapped_column(Text, nullable=True)
    devolver_estoque: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Escolhas dos modais de devolução (ver alembic 0104). Persistidas para
    # auditoria e para o PATCH re-rodar o estoque de forma determinística.
    troca_sku: Mapped[str | None] = mapped_column(Text, nullable=True)
    troca_condicao: Mapped[str | None] = mapped_column(Text, nullable=True)
    estoque_suffix: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Unidades a lançar no estoque Bling (ver alembic 0110). Hoje a busca
    # expande 1 linha por unidade, então fica 1; guardado p/ a entrada e o
    # estoque inicial do produto z criado.
    quantidade: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    # Destino do estoque escolhido no modal (ver alembic 0110):
    #   estoque_destino_sku — bin já existente `base.<sufixo>` (entrada direta);
    #   estoque_nova_tag     — tag pra criar produto novo `z000N.<tag>` quando
    #                          nenhuma variante existe.
    estoque_destino_sku: Mapped[str | None] = mapped_column(Text, nullable=True)
    estoque_nova_tag: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "Novo"/"Usado"/"Sucata" escolhido no modal quando condição == Manutenção.
    manutencao_destino: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Tags de sufixo regional dos SKUs do pedido (`.sp`, `.ra`, …); ver 0105.
    tag: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Setado quando o toggle "devolver estoque" passa a TRUE (auto no router).
    data_devolvido_estoque: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Movimento de estoque efetivamente lançado no Bling (ver alembic 0115).
    # Guardado pra poder ESTORNAR (dar baixa "S") quando o toggle "devolver
    # estoque" é desligado depois — ex.: Usado que vira Sucata. `*_action`
    # distingue bin existente de produto z criado; `*_revertido_at` marca o
    # estorno já feito (evita estornar duas vezes).
    estoque_mov_sku: Mapped[str | None] = mapped_column(Text, nullable=True)
    estoque_mov_bling_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    estoque_mov_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    estoque_mov_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estoque_mov_revertido_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
