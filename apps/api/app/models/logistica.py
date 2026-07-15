from datetime import date
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Date, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Logistica(Base, TimestampMixin):
    """Caso de logística/pós-venda a acompanhar. Registro manual, no formato da
    aba `logistica`: Data | pedido bling | pedido marketplace | plataforma |
    conta | STATUS PLATAFORMA | rastreio | localização | STATUS BLING | Chamado.

    `meli_status` guarda a assinatura de status do Meli (os 8 campos:
    order_status, ship_status, ship_substatus, cancel_group, return_status,
    claim_stage, claim_status, benefited) que alimenta a sugestão de Status
    Bling candidato (app.services.logistica_rules.sugerir). A classificação
    final (`status_bling`) é decisão do operador — a sugestão nunca grava
    sozinha.
    """

    __tablename__ = "logistica"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    data: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    pedido_bling: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    pedido_marketplace: Mapped[str | None] = mapped_column(Text, nullable=True)
    plataforma: Mapped[str | None] = mapped_column(Text, nullable=True)
    conta: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Assinatura de status do Meli (dict dos 8 campos). Vazio pra plataformas
    # sem esse fluxo — a sugestão simplesmente não se aplica.
    meli_status: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    # Número de rastreio do envio (manual por enquanto).
    rastreio: Mapped[str | None] = mapped_column(Text, nullable=True)
    localizacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Classificação escolhida pelo operador (dica vem de logistica_rules.sugerir).
    status_bling: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Número/ref do chamado aberto na plataforma (manual).
    chamado: Mapped[str | None] = mapped_column(Text, nullable=True)
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


class LogisticaStatus(Base, TimestampMixin):
    """Aba "Status": cadastro/referência do que fazer pra cada STATUS PLATAFORMA.

    Formato da aba `status pla.`: STATUS PLATAFORMA | alterar status bling |
    abrir chamado | mensagem chamado. É cadastro manual — o operador preenche as
    células à mão (todos os campos são opcionais). A `mensagem_chamado` também
    guarda o que anexar (foto/link/o que for) do envio.
    """

    __tablename__ = "logistica_status"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    # Marketplace a que a regra se aplica (ex. "Mercado Livre"); vazio = geral.
    plataforma: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Opcional: linha pode nascer vazia pra ser completada pelo operador.
    status_plataforma: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Novo status do Bling; se vazio "não faz nada" (obs "alterado logística").
    alterar_status_bling: Mapped[str | None] = mapped_column(Text, nullable=True)
    monitoramento: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    abrir_chamado: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Mensagem do chamado + o que anexar (foto/link/o que for) no envio.
    mensagem_chamado: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
