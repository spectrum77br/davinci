"""Trocar a tag (= o fim do código) em massa no Bling — menu Configuração do Produtos 2 (M-46).

`produto_trocas_tag`: um clique em "Aplicar" — quem, quando, a tag escolhida e a chave de
idempotência vinda da tela (repetir o POST devolve o mesmo resultado, sem trocar de novo).
`produto_trocas_tag_itens`: um SKU do clique — código antigo, código novo, o id do produto no
Bling, o estado e o motivo.

Estados do item:
  pendente (vai ser trocado) → enviando (gravado ANTES do PATCH no Bling) → ok | falhou (o Bling
  com certeza não trocou) | sem_confirmacao (o Bling respondeu erro e não deu para conferir com
  GET se o código mudou — a pessoa confere no Bling; se trocou, o aviso do Bling leva a troca ao
  DaVinci sozinho);
  pulado = não foi trocado por regra (o motivo diz qual: kit, peça de kit, mala, Amazon…).
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

ESTADOS_ITEM = ("pendente", "enviando", "ok", "pulado", "falhou", "sem_confirmacao")


class ProdutoTrocaTag(Base, TimestampMixin):
    __tablename__ = "produto_trocas_tag"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    idem_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    tag: Mapped[str] = mapped_column(Text, nullable=False)
    # Os SKUs pedidos (minúsculos, na ordem, separados por vírgula): a mesma chave com outro
    # pedido é recusada.
    pedido: Mapped[str] = mapped_column(Text, nullable=False)
    criado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    criado_por_nome: Mapped[str | None] = mapped_column(Text, nullable=True)
    concluido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProdutoTrocaTagItem(Base, TimestampMixin):
    __tablename__ = "produto_trocas_tag_itens"
    __table_args__ = (
        CheckConstraint(
            "estado IN ('pendente','enviando','ok','pulado','falhou','sem_confirmacao')",
            name="estado",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    troca_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("produto_trocas_tag.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ordem: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    sku_antigo: Mapped[str] = mapped_column(Text, nullable=False)
    sku_novo: Mapped[str | None] = mapped_column(Text, nullable=True)
    bling_product_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    estado: Mapped[str] = mapped_column(
        Text, nullable=False, default="pendente", server_default=text("'pendente'")
    )
    motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    tentativa_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    concluido_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
