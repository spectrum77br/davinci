from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class DevolucaoMensagemComprador(Base, TimestampMixin):
    """Mensagem que a LOJA manda pro COMPRADOR por causa de uma devolução.

    Vinicius, 22/09/2026: "quando o pessoal faz o lançamento no painel
    Devoluções e coloca motivo Bloqueado, além de abrir chamado, também envie
    mensagem para o cliente solicitando a senha". O aparelho volta travado com
    a senha do comprador (ou a mala com o segredo do cadeado) e sem isso não há
    revenda — e a disputa desse motivo não ganha (medido: 0 em 5).

    Hoje só a Shopee tem canal: o chat da loja responde pelas 14 integrações e
    o comprador vem do próprio pedido (`buyer_user_id`). No Mercado Livre a
    devolução cancela o pedido e o ML fecha o chat ("blocked_by_cancelled_order",
    10 de 10 casos conferidos em 22/09); na TikTok falta o escopo
    `seller.customer_service` (401 nas 8 lojas). Por isso a linha nasce com
    `plataforma` e só vira envio quando há canal.

    Uma linha por (pedido, conta, evento) — a UNIQUE é o que segura o kit (3
    linhas de devolução do mesmo pedido) e o gancho do router, que roda em todo
    save e em todo upload de foto. Ciclo igual ao da `LogisticaMensagemCliente`:
    pendente → enviada | falhou, com teto de tentativas.

    `devolution_id`/`chamado_id` são SET NULL: a lixeira da aba Devoluções apaga
    a linha e o chamado, e a prova de que o comprador JÁ recebeu a mensagem não
    pode sumir junto (foi assim que o 293460 virou "disputa aberta fora do
    DaVinci"). Por isso o snapshot de pedido/conta fica aqui.
    """

    __tablename__ = "devolucao_mensagem_comprador"
    __table_args__ = (
        UniqueConstraint("pedido_bling", "conta", "evento", name="uq_dev_msg_comprador"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    devolution_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("devolutions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    chamado_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("chamados.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Snapshot: é o que sobrevive à exclusão da linha e do chamado.
    pedido_bling: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    pedido_marketplace: Mapped[str | None] = mapped_column(Text, nullable=True)
    conta: Mapped[str] = mapped_column(Text, nullable=False)
    plataforma: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "senha" hoje; o campo existe pros próximos avisos não pedirem tabela nova.
    evento: Mapped[str] = mapped_column(Text, nullable=False, default="senha")
    # Quem recebe e onde a conversa foi parar (a Shopee só devolve o
    # `conversation_id` na resposta do envio — não há busca por comprador).
    destinatario_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    conversa_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    mensagem_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    texto: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    # Anexo da linha que foi junto como imagem (None = só texto).
    anexo_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("devolucao_anexo.id", ondelete="SET NULL"),
        nullable=True,
    )
    # pendente | enviada | falhou | cancelada (motivo deixou de pedir senha)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pendente", server_default="pendente", index=True
    )
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    tentativas: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    enviada_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Resposta do comprador, quando alguém (ou o leitor de chat) trouxer.
    resposta_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resposta_texto: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Quem pediu (None = automático do lançamento).
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
