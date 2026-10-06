"""Imobilizado (Cadastros › Imobilizado, 06/10/2026): os bens da empresa —
computador, móvel, máquina, veículo — com número de patrimônio, valor e quem
responde por cada um. Projeto "Cadastro de Imobilizado no DaVinci" v0.1.

Item não se apaga: sai por baixa (data + motivo) e fica na lista como
`baixado`. Cada troca de descrição, valor, responsável ou status vira uma linha
em `imobilizado_historico` (aba Histórico do item), além do Histórico geral do
sistema, que o gatilho pega sozinho.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

IMOBILIZADO_STATUS = ("ativo", "baixado")


class Imobilizado(Base):
    __tablename__ = "imobilizado"
    __table_args__ = (
        CheckConstraint("valor > 0", name="valor_positivo"),
        CheckConstraint("status IN ('ativo', 'baixado')", name="status_valido"),
        # Baixado sempre tem data e motivo (RN05); ativo nunca tem.
        CheckConstraint(
            "(status = 'baixado') = (baixa_data IS NOT NULL AND baixa_motivo IS NOT NULL)",
            name="baixa_completa",
        ),
        Index("ix_imobilizado_responsavel", "responsavel_id"),
        Index("ix_imobilizado_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Número do patrimônio (plaqueta). Único e fixo depois de salvo (RN01/RN02).
    numero: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    descricao: Mapped[str] = mapped_column(String(200), nullable=False)
    valor: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    responsavel_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="ativo")
    baixa_data: Mapped[date | None] = mapped_column(Date, nullable=True)
    baixa_motivo: Mapped[str | None] = mapped_column(String(200), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    criado_por: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    atualizado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    atualizado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )


class ImobilizadoHistorico(Base):
    """Uma mudança num item (RN07). Responsável guarda o NOME da pessoa na
    hora — renomear ou desativar o usuário depois não reescreve o passado."""

    __tablename__ = "imobilizado_historico"
    __table_args__ = (Index("ix_imobilizado_historico_item", "imobilizado_id", "alterado_em"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    imobilizado_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("imobilizado.id", ondelete="RESTRICT"), nullable=False
    )
    # 'descricao', 'valor', 'responsavel' ou 'status'
    campo: Mapped[str] = mapped_column(String(30), nullable=False)
    valor_anterior: Mapped[str | None] = mapped_column(Text, nullable=True)
    valor_novo: Mapped[str | None] = mapped_column(Text, nullable=True)
    alterado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    alterado_por: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
