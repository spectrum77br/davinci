"""Entrada avulsa de estoque no Bling — botão "Incluir" do estoque do produto em Produtos 2 (M-43).

Uma linha por entrada: quem, quando, produto, quantidade, observação, o status do lançamento e o
id do lançamento no Bling. Mesmo jeito seguro da transferência (models/estoque_transferencia.py):
a linha é gravada ANTES de lançar e marcada "enviando" antes do POST /estoques.

Status:
  pendente (gravada, ainda não lançada) → enviando (gravado ANTES do POST) → ok | falhou (o Bling
  com certeza não gravou: nada entrou) | conferindo (o Bling respondeu erro depois de receber: o
  worker procura no extrato antes de qualquer coisa) → ok | sem_confirmacao (10 min sem achar: a
  pessoa confere o extrato no Bling).
`mov_ref` = o movimento do espelho do extrato (stock_movements) que confirmou a entrada — não
serve de prova para outra entrada, transferência ou devolução.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Sequence,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

ESTADOS_ENTRADA = ("pendente", "enviando", "ok", "falhou", "conferindo", "sem_confirmacao")
NUMERO_SEQ = Sequence("estoque_entradas_numero_seq")


class EstoqueEntrada(Base, TimestampMixin):
    __tablename__ = "estoque_entradas"
    __table_args__ = (
        CheckConstraint("quantidade BETWEEN 1 AND 100000", name="quantidade"),
        CheckConstraint(
            "status IN ('pendente','enviando','ok','falhou','conferindo','sem_confirmacao')",
            name="status",
        ),
        # A lista da tela é por produto (sem olhar maiúsculas).
        Index("ix_estoque_entradas_sku", text("lower(sku)")),
        # O worker só olha as entradas em andamento: índice parcial pequeno.
        Index(
            "ix_estoque_entradas_andamento",
            "status",
            postgresql_where=text("status IN ('pendente','enviando','conferindo')"),
        ),
        # A conferência no extrato conta as entradas do mesmo produto perto da tentativa.
        Index("ix_estoque_entradas_bling_product_id", "bling_product_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    # Número curto para a tela e para a observação do extrato do Bling ("Entrada DaVinci #N").
    numero: Mapped[int] = mapped_column(
        BigInteger,
        NUMERO_SEQ,
        server_default=NUMERO_SEQ.next_value(),
        nullable=False,
        unique=True,
    )
    # Vem da tela (uma por abertura da janela): clique duplo devolve a mesma entrada.
    idem_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    bling_product_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quantidade: Mapped[int] = mapped_column(Integer, nullable=False)
    # Custo do cadastro do produto, mandado no lançamento (sem ele o Bling grava custo 0 e mexe
    # no custo médio). NULL = o produto não tem custo nem no DaVinci nem no Bling.
    custo: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    criado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    criado_por_nome: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pendente", server_default=text("'pendente'")
    )
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    tentativa_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lancada_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mov_ref: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    # O id do lançamento que o POST /estoques devolve (para a lista de lançamentos do produto
    # mostrar a observação digitada). NULL quando o Bling não respondeu direito.
    bling_lancamento_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
