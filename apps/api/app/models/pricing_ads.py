"""Ads da aba Contas da Tabela de Preços (M-12, 18/11/2026, migration 0503).

Pedido do dono: a coluna Ads era texto livre (`pricing_accounts.ads`, "2% - 50
ROAS") e vira um painel igual ao da Margem (M-2): a Geral da conta (% que pode
gastar em Ads e gasto por dia em R$) e, por produto, o % e o gasto próprios.
O ROAS mínimo (100 ÷ %) é calculado na hora, não é gravado. Nada disso entra
no cálculo de preço.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Numeric, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PricingAdsConta(Base):
    """A Geral de Ads de uma conta. Sem linha = as duas caixas ligadas, sem %
    e sem gasto. `ads_geral_ligada` desligada: produto sem % próprio fica sem
    % (—); `ads_por_produto_ligada` desligada: os % próprios ficam gravados,
    mas todos usam a Geral."""

    __tablename__ = "pricing_ads_conta"
    __table_args__ = (
        CheckConstraint("pode_gastar_pct BETWEEN 0 AND 100", name="pode_gastar_pct"),
        CheckConstraint("gasto_dia >= 0", name="gasto_dia"),
    )

    pricing_account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("pricing_accounts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    ads_geral_ligada: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    # % do faturamento que a conta pode gastar em Ads (2 = 2%).
    pode_gastar_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    # R$ por dia, digitado.
    gasto_dia: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    ads_por_produto_ligada: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    atualizado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class PricingAdsProduto(Base):
    """O % e o gasto por dia próprios de um produto (linha da Tabela) numa
    conta. Sem linha = usa a Geral da conta."""

    __tablename__ = "pricing_ads_produto"
    __table_args__ = (
        CheckConstraint("pode_gastar_pct BETWEEN 0 AND 100", name="pode_gastar_pct"),
        CheckConstraint("gasto_dia >= 0", name="gasto_dia"),
    )

    pricing_account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("pricing_accounts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    pricing_product_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("pricing_products.id", ondelete="CASCADE"),
        primary_key=True,
    )
    pode_gastar_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    gasto_dia: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    atualizado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
