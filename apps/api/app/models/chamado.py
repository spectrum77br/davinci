from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

# Origem do chamado — de qual aba/fluxo ele nasceu (coluna "origem" da planilha).
# `vendas` (Eduardo 04/09: "em origens coloque o status vendas") — chamado aberto
# a partir de uma venda, sem passar pela Margem/Logística/Devolução.
ORIGENS = ("margem", "logistica", "devolucao", "vendas")
# Como a réplica sai: `api` = mediação do Mercado Livre via API (claim já
# aberto pelo comprador); `robo` = formulário web/protocolo — fica na fila do
# robô de browser; `manual` = só registra no histórico (operador respondeu
# fora do sistema).
CANAIS = ("api", "robo", "manual")


class Chamado(Base, TimestampMixin):
    """Um chamado aberto na plataforma, no formato da aba `Chamados`:
    Data | pedido bling | pedido marketplace | plataforma | produto | sku |
    conta | status bling | origem | chamado | réplica | réplica automática |
    alterar status bling | monitoramento.

    Os dados do pedido são espelho do `bling_orders` no momento da criação;
    `status_bling` é o snapshot — a listagem mostra o ATUAL (lookup vivo).
    """

    __tablename__ = "chamados"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    data: Mapped[date | None] = mapped_column(Date, nullable=True)
    pedido_bling: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    pedido_marketplace: Mapped[str | None] = mapped_column(Text, nullable=True)
    plataforma: Mapped[str | None] = mapped_column(Text, nullable=True)
    conta: Mapped[str | None] = mapped_column(Text, nullable=True)
    produto: Mapped[str | None] = mapped_column(Text, nullable=True)
    sku: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_bling: Mapped[str | None] = mapped_column(Text, nullable=True)
    origem: Mapped[str] = mapped_column(Text, nullable=False)
    # id da linha de origem (logistica/refunds/devolutions), quando veio de lá.
    origem_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Nº do chamado: claim_id da mediação (canal api) ou protocolo do
    # formulário (canal robo) — nunca inventado, só o que a plataforma devolveu.
    chamado: Mapped[str | None] = mapped_column(Text, nullable=True)
    chamado_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    canal: Mapped[str] = mapped_column(
        Text, nullable=False, default="manual", server_default="manual"
    )
    # Nome da situação do Bling a aplicar (dropdown de situacao_bling.nome).
    alterar_status_bling: Mapped[str | None] = mapped_column(Text, nullable=True)
    monitoramento: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Réplica automática: reenvia `auto_mensagem` (+ anexos sem mensagem_id) a
    # cada `auto_dias` enquanto ligada e o chamado não estiver resolvido.
    auto_ligada: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    auto_dias: Mapped[int | None] = mapped_column(Integer, nullable=True)
    auto_mensagem: Mapped[str | None] = mapped_column(Text, nullable=True)
    auto_ultimo_envio_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolvido: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false", index=True
    )
    resolvido_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Valor recuperado com o chamado (R$) — coluna "Valor" do grupo Controle
    # (Eduardo 03/09, migration 0240). NULL = ainda sem valor.
    valor_recuperado: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Encaminhado ao JURÍDICO (Eduardo 04/09, migration 0248): quem/quando, a
    # observação digitada ao encaminhar, o token do dossiê público (link no
    # Threema) e os IDs Threema que receberam. Preenchido = aparece na aba Jurídico.
    juridico_enviado_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    juridico_enviado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    juridico_obs: Mapped[str | None] = mapped_column(Text, nullable=True)
    juridico_token: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    juridico_destinatarios: Mapped[str | None] = mapped_column(Text, nullable=True)

    mensagens: Mapped[list["ChamadoMensagem"]] = relationship(
        back_populates="chamado",
        cascade="all, delete-orphan",
        order_by="ChamadoMensagem.created_at",
    )
    anexos: Mapped[list["ChamadoAnexo"]] = relationship(
        back_populates="chamado",
        cascade="all, delete-orphan",
        order_by="ChamadoAnexo.created_at",
    )


class ChamadoMensagem(Base, TimestampMixin):
    """Histórico do chamado: cada réplica (manual ou automática) enviada, cada
    resposta recebida da plataforma e eventos do sistema — com quem e quando.

    `status`: registrada (canal manual, nada a enviar) | pendente (na fila do
    robô) | enviada | falhou (`erro` explica).
    """

    __tablename__ = "chamado_mensagem"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    chamado_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("chamados.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # enviada (nós → plataforma) | recebida (plataforma → nós) | sistema
    direcao: Mapped[str] = mapped_column(Text, nullable=False)
    # replica | replica_auto | abertura | sistema
    tipo: Mapped[str] = mapped_column(Text, nullable=False)
    texto: Mapped[str] = mapped_column(Text, nullable=False)
    canal: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    autor_nome: Mapped[str | None] = mapped_column(Text, nullable=True)
    autor_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    enviada_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    chamado: Mapped["Chamado"] = relationship(back_populates="mensagens")
    anexos: Mapped[list["ChamadoAnexo"]] = relationship(
        back_populates="mensagem",
        order_by="ChamadoAnexo.created_at",
    )


class ChamadoAnexo(Base, TimestampMixin):
    """Foto anexada a uma réplica (`mensagem_id`) ou à réplica automática do
    chamado (`mensagem_id` NULL). Blob no próprio banco, servido pelo endpoint
    autenticado — mesmo esquema do logistica_status_anexo."""

    __tablename__ = "chamado_anexo"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    chamado_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("chamados.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mensagem_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("chamado_mensagem.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    chamado: Mapped["Chamado"] = relationship(back_populates="anexos")
    mensagem: Mapped["ChamadoMensagem | None"] = relationship(back_populates="anexos")
