from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import AlertSeverity, AlertType


def _enum(py_enum, name: str):
    return Enum(
        py_enum,
        name=name,
        schema=None,
        create_type=False,
        values_callable=lambda x: [e.value for e in x],
    )


# Quem vê cada alerta (M-19, 23/11/2026; migration 0505). A cerca é do
# servidor (`services/alertas_cerca.py`), não só da tela:
#   - "adm": só administrador (saúde do WhatsApp, M-/P-, robôs e sync geral,
#     fatura, Flex, estoque baixo). Com `user_id` = só aquele administrador
#     (o aviso da mudança tem texto por pessoa); sem = todos os administradores.
#   - "equipe": alerta de anúncio. Vê o administrador e quem é da equipe da
#     loja da integração (`deps/team_scope`); `user_id` é sempre NULL.
#   - "pessoal": só o `user_id` (tarefa atribuída, o que a pessoa fez).
ESCOPO_ADM = "adm"
ESCOPO_EQUIPE = "equipe"
ESCOPO_PESSOAL = "pessoal"
ESCOPOS = (ESCOPO_ADM, ESCOPO_EQUIPE, ESCOPO_PESSOAL)


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    # Para quem é (M-19): NULL = todo mundo que o `escopo` deixa ver.
    user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )
    type: Mapped[AlertType] = mapped_column(_enum(AlertType, "alert_type"), nullable=False)
    severity: Mapped[AlertSeverity] = mapped_column(
        _enum(AlertSeverity, "alert_severity"),
        nullable=False,
        server_default=text("'info'"),
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    # Legado: o "lido" de antes da M-19 (um alerta por pessoa). Desde a 0505 o
    # lido é por pessoa em `alerta_leituras`; a coluna fica para o downgrade.
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )
    # M-19 (23/11/2026): quem vê (ver ESCOPO_*) e de qual loja é o aviso.
    escopo: Mapped[str] = mapped_column(
        Text, nullable=False, default=ESCOPO_ADM, server_default=text("'adm'")
    )
    # A equipe de vendas da loja (101/102/103) NA HORA do aviso. A cerca e a
    # coluna Equipe da tela usam a equipe de AGORA da integração (a loja que
    # muda de equipe leva os alertas); esta só vale sem integração.
    equipe: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plataforma: Mapped[str | None] = mapped_column(Text, nullable=True)
    integration_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("integrations.id", ondelete="SET NULL"),
        nullable=True,
    )
    # O nome da conta na hora do aviso (a tela mostra o nome atual da integração).
    conta: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Cópia de antes da M-19 do mesmo aviso (o mesmo `dedupe_key` gravado uma
    # vez por pessoa): fica escondida, vale a primeira. Sem FK de propósito.
    duplicado_de: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    __table_args__ = (
        Index(
            "ix_alerts_user_unread_created",
            "user_id",
            "read_at",
            text("created_at DESC"),
        ),
        Index(
            "ix_alerts_user_dedupe",
            "user_id",
            "dedupe_key",
            unique=True,
            postgresql_where=text("dedupe_key IS NOT NULL"),
        ),
        # O aviso sem destinatário (ADM de todos, equipe) é um só por chave.
        Index(
            "ix_alerts_dedupe_compartilhado",
            "dedupe_key",
            unique=True,
            postgresql_where=text(
                "dedupe_key IS NOT NULL AND user_id IS NULL AND duplicado_de IS NULL"
            ),
        ),
        Index(
            "ix_alerts_escopo_created",
            "escopo",
            text("created_at DESC"),
            postgresql_where=text("duplicado_de IS NULL"),
        ),
        Index(
            "ix_alerts_integration_id",
            "integration_id",
            postgresql_where=text("integration_id IS NOT NULL"),
        ),
        CheckConstraint("escopo IN ('adm', 'equipe', 'pessoal')", name="escopo_valido"),
        CheckConstraint("escopo <> 'pessoal' OR user_id IS NOT NULL", name="pessoal_tem_dono"),
        CheckConstraint("escopo <> 'equipe' OR user_id IS NULL", name="equipe_sem_dono"),
    )


class AlertaLeitura(Base):
    """Quem já leu cada alerta (M-19, 23/11/2026): o mesmo alerta aparece para
    a equipe toda e cada pessoa marca o seu "lido"."""

    __tablename__ = "alerta_leituras"

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    alert_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("alerts.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    lido_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )
