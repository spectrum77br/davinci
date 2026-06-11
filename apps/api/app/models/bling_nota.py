from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class BlingNota(Base, TimestampMixin):
    """Conta Bling dedicada à emissão de NF.

    Propositalmente separada de `integrations`: são apps OAuth distintos
    da conta principal de sync e não devem se misturar.
    """

    __tablename__ = "bling_notas"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    # Nome da conta no Bling (ex.: "josefinaapp")
    nome: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    client_id: Mapped[str] = mapped_column(Text, nullable=False)
    # base64("client_id:client_secret") — header `Authorization: Basic`
    # usado no fluxo de refresh token da API v3 do Bling.
    basic_auth_b64: Mapped[str] = mapped_column(Text, nullable=False)
    # Code retornado pelo link de convite OAuth (uso único na troca inicial).
    authorization_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'active'")
    )
