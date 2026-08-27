from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class VigiaImportacao(Base):
    """Estado do vigia de importação (services/vigia_importacao.py): um
    pedido PAGO no marketplace que (ainda) não apareceu no espelho
    bling_orders. Uma linha por pedido (UNIQUE plataforma+numero_loja).

    Ciclo: detectado → avisado no Threema (avisado_em; re-aviso a cada 24h
    enquanto persistir) → resolvido sozinho (resolvido_em) quando o pedido
    aparece no Bling. NÃO confundir com models/importacao.py (importação de
    planilhas) — aqui é a importação de PEDIDOS do canal multi loja do Bling.
    """

    __tablename__ = "vigia_importacao"
    __table_args__ = (
        UniqueConstraint(
            "plataforma", "numero_loja", name="uq_vigia_importacao_plataforma_numero"
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    # 'ml' (fase 1). Shopee/TikTok/Amazon quando os clients ganharem listagem.
    plataforma: Mapped[str] = mapped_column(Text, nullable=False)
    # Nome amigável da conta (store_info.account_name; fallback integration.name).
    conta: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ID do pedido no marketplace — o que casa com bling_orders.numeroloja.
    numero_loja: Mapped[str] = mapped_column(Text, nullable=False)
    # Carrinho ML: o Bling às vezes grava o pack_id como numeroloja — o
    # matching aceita os dois.
    pack_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # payments.date_approved (fallback date_created) — base da tolerância.
    pago_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    detectado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    ultima_verificacao: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    avisado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolvido_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
