from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Float, Text, text
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
