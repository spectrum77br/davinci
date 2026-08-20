from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, LargeBinary, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

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
    # "Isso é de quando?" — carimbo por campo do `meli_status`:
    # {"ship_substatus": {"em": "2026-08-12T08:05:00+00:00", "fonte": "aprox"}}.
    # Fica FORA do meli_status de propósito: aquele dict é a assinatura que casa
    # com as regras da aba Status (`_clean_meli` descarta qualquer chave extra).
    # Ver app.services.logistica_datas.
    status_datas: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    # Número de rastreio do envio (manual por enquanto).
    rastreio: Mapped[str | None] = mapped_column(Text, nullable=True)
    localizacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Explicação da divergência entre o status do ML e o rastreio físico dos
    # Correios (auto-calculada; vazia quando batem). Ver logistica_rules.
    divergencia: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Classificação escolhida pelo operador (dica vem de logistica_rules.sugerir).
    status_bling: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Número/ref do chamado aberto na plataforma (manual).
    chamado: Mapped[str | None] = mapped_column(Text, nullable=True)
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Instante em que o aviso Threema desta linha foi enviado. Enquanto NULL, a
    # regra casada com `mensagem_threema` mantém o pedido no painel; depois de
    # enviado a mensagem deixa de contar como pendência (o pedido resolve/some).
    threema_enviado_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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
    # Status que se identifica hoje no Bling (referência, antes de alterar).
    status_atual: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Novo status do Bling; se vazio "não faz nada" (obs "alterado logística").
    alterar_status_bling: Mapped[str | None] = mapped_column(Text, nullable=True)
    monitoramento: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    abrir_chamado: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    abrir_reembolso: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Mensagem do chamado + o que anexar (foto/link/o que for) no envio.
    mensagem_chamado: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Mensagem a colar no Bling.
    mensagem_bling: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Mensagem a enviar às pessoas (Threema) notificando o problema.
    mensagem_threema: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Threema IDs escolhidos pra ESTA regra (separados por vírgula). Salvos pelo
    # seletor 👤 da aba Status; o envio usa essa lista por padrão. Vazio = lista
    # fixa do `.env`.
    threema_recipients: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    anexos: Mapped[list["LogisticaStatusAnexo"]] = relationship(
        back_populates="status",
        cascade="all, delete-orphan",
        order_by="LogisticaStatusAnexo.created_at",
    )


class LogisticaStatusAnexo(Base, TimestampMixin):
    """Imagem anexada à `mensagem_chamado` de uma linha da aba Status.

    Guarda o arquivo como blob (bytea) no próprio banco — o app não tem storage
    externo. Servido de volta pelo endpoint autenticado (cookie) pra o <img>.
    """

    __tablename__ = "logistica_status_anexo"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    status_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("logistica_status.id", ondelete="CASCADE"),
        nullable=False,
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

    status: Mapped["LogisticaStatus"] = relationship(back_populates="anexos")
