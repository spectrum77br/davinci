from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MargemAudit(Base):
    """Trilha de auditoria das ações feitas PELO app sobre pedidos na página
    Margem (e fluxos relacionados): mudança de situação no Bling, edição do
    Saldo Final (valor_base) e da Observação. Cada linha registra quando,
    quem (ou sistema), a ação e o valor antigo → novo.

    Mudanças feitas direto no painel do Bling NÃO entram aqui — a API do Bling
    não expõe o autor.
    """

    __tablename__ = "margem_audit"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
        index=True,
    )
    pedido_bling: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    bling_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    sku: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 'situacao' | 'saldo_final' | 'observacao' | 'sku' (troca por
    # prioridade de estoque — services/prioridade_estoque.py)
    acao: Mapped[str] = mapped_column(Text, nullable=False)
    valor_antigo: Mapped[str | None] = mapped_column(Text, nullable=True)
    valor_novo: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 'margens' | 'devolucao' | 'job_envio' | 'job_envio_espelho'
    # (espelho = Bling já estava em 15 quando o sweep ia mudar; registro
    #  do carimbo local pra trilha não ficar cega — incidente 17/08).
    origem: Mapped[str] = mapped_column(Text, nullable=False)
    # NULL = ação disparada pelo sistema (job automático)
    mudado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
