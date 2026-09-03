from uuid import UUID

from sqlalchemy import ForeignKey, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class MargemSaldoManual(Base, TimestampMixin):
    """Saldo Efetivo digitado NA MÃO na aba Margem (Eduardo, 03/09).

    Vale só para ML/Shopee/TikTok enquanto o líquido REAL da plataforma não
    sincroniza (a célula fica "—" e a linha presa em "aguardando saldo").
    O valor preenche o vazio; quando o repasse real chega, ele VENCE o manual
    (regra de 01/09 — "sempre pegar a da plataforma" — continua valendo).
    Grão de ITEM (`verificar_margem.bling_order_item_id`), sem FK: o snapshot
    é rebuilt a cada 30min e o id vem do espelho bling_orders.
    """

    __tablename__ = "margem_saldo_manual"

    bling_order_item_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True
    )
    # Denormalizados para debug/auditoria (o snapshot pode ser rebuilt).
    pedido_bling: Mapped[str | None] = mapped_column(Text, nullable=True)
    sku: Mapped[str | None] = mapped_column(Text, nullable=True)
    valor: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    updated_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
