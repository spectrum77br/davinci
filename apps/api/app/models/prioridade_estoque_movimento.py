from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class PrioridadeEstoqueMovimento(Base, TimestampMixin):
    """Movimento de estoque que o robô de prioridade lança no Bling ao trocar
    um KIT de tag (dg053.ci+a001.ci → dg053.sp+a001.sp).

    O Bling aceita a troca do item mas, na baixa (NF), continua descontando a
    composição ANTIGA do kit (comprovado em 14/09 pelo extrato de estoque);
    produto simples ele baixa certo. Então, pra kit, o robô compensa na hora:
    ENTRADA em cada componente antigo (anula a baixa que o Bling vai fazer
    neles) e SAÍDA em cada componente novo — mesmo mecanismo da correção de
    estoque das Devoluções.

    Cada linha é UM POST /estoques, gravada em transação própria ANTES da
    chamada (o lançamento no Bling é um fato externo; não pode sumir num
    rollback do caller). `status`: pendente (gravado, ainda não lançado) →
    ok (lançado, `lancado_at`) | falhou (Bling respondeu erro; o sweep
    retenta, `tentativas`) | incerto (rede/timeout: não se sabe se entrou —
    nunca retenta sozinho, só avisa). Pedido cancelado/excluído antes de
    sair → o sweep lança o oposto e carimba `revertido_at`.
    """

    __tablename__ = "prioridade_estoque_movimentos"
    __table_args__ = (
        Index(
            "uq_prioridade_estoque_mov_vivo",
            "pedido_bling", "sku", "operacao",
            unique=True,
            postgresql_where=text("revertido_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    pedido_bling: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    bling_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    bling_product_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 'E' (entrada) | 'S' (saída) — como (será) lançado no Bling.
    operacao: Mapped[str] = mapped_column(Text, nullable=False)
    quantidade: Mapped[int] = mapped_column(Integer, nullable=False)
    # Ordem de lançamento dentro do pedido (entradas antes das saídas).
    ordem: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pendente", server_default=text("'pendente'")
    )
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    tentativas: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    lancado_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revertido_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    avisado_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
