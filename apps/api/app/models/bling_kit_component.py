"""Composição (estrutura) dos kits Bling, cacheada localmente.

Cada linha é um par (kit → componente) com a quantidade, espelhando
`estrutura.componentes` do produto composto (`formato='E'`) no Bling. Populado
semanalmente por `app.services.kit_components_sync`. O order-lookup de
devoluções usa essa tabela pra explodir um SKU de kit (ex.: `b011` "Kit 6
Malas") nos componentes individuais, fazendo o retorno de estoque cair em cada
produto-componente correto — a composição não está na string do SKU, vive aqui.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, Numeric, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class BlingKitComponent(Base):
    __tablename__ = "bling_kit_components"
    __table_args__ = (
        UniqueConstraint(
            "kit_bling_product_id",
            "component_bling_product_id",
            name="uq_bling_kit_components_kit_comp",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    kit_bling_product_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    component_bling_product_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quantidade: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, server_default=text("1"))
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
