from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Refund(Base, TimestampMixin):
    __tablename__ = "refunds"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    data: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pedido_bling: Mapped[str | None] = mapped_column(Text, nullable=True)
    pedido_marketplace: Mapped[str | None] = mapped_column(Text, nullable=True)
    plataforma: Mapped[str | None] = mapped_column(Text, nullable=True)
    conta: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    tipo: Mapped[str | None] = mapped_column(Text, nullable=True)
    prejuizo: Mapped[float | None] = mapped_column(Float, nullable=True)
    reembolso: Mapped[float | None] = mapped_column(Float, nullable=True)
    chamado: Mapped[str | None] = mapped_column(Text, nullable=True)
    chamado_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    chamado_resolvido: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    operacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    conferido: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # Carimbado por patch_refund quando `conferido` vira true (limpo em true->false).
    # Usado pelo quadro "Operacional — 3 meses" (Valuation) p/ agrupar reembolsos por mês.
    conferido_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Quando o valor de `reembolso` foi lançado/alterado no DaVinci (POST/PATCH
    # ou sync de manutenção da devolução). Vazio = ainda sem valor lançado.
    # Eduardo (14/09): "a data que a agência colocou o reembolso ali".
    reembolso_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Rastreio interno de autoria (DB-only): preenchido no POST /api/refunds a
    # partir do usuário autenticado. NÃO é exposto em RefundOut nem na UI.
    # (17/11/2026: o bloco "Reembolso ao cliente" da conversa mostra o NOME de
    # quem lançou — `services/atendimento/reembolso_conversa.py`.)
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ── Novos procedimentos 3/6 (17/11/2026, migration 0494) ──────────────────
    # O reembolso ao cliente lançado pela aba Pedido da conversa do Atendimento
    # (`routers/atendimento_reembolso.py`). As regras moram em
    # `services/reembolso_cliente.py`; as linhas antigas ficam com tudo vazio.
    # defeito_avaria | produto_errado | faltou_peca | frete | outro
    motivo: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # Obrigatório quando o motivo é "outro" (a API confere).
    motivo_detalhe: Mapped[str | None] = mapped_column(Text, nullable=True)
    # pix | cupom
    forma_pagamento: Mapped[str | None] = mapped_column(String(12), nullable=True)
    # Os dados do Pix (chave, banco, nome) CIFRADOS
    # (`reembolso_cliente.proteger_dados_pagamento`): só ADM lê, nunca vão para o
    # export nem para log — e o Histórico só guarda a cifra.
    dados_pagamento: Mapped[str | None] = mapped_column(Text, nullable=True)
    # O limite pediu o ADM (> R$ 100, > 25% do valor pago ou o valor inteiro):
    # sem `aprovado_em`, a linha fica "Aguardando ADM" e não pode ser Finalizada.
    requer_aprovacao: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    aprovado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    aprovado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 'atendimento' nas lançadas pela conversa; vazio nas da tela Reembolso e nas automáticas.
    origem: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # A conversa de onde o reembolso saiu (o id em texto).
    conversa_id: Mapped[str | None] = mapped_column(Text, nullable=True)
