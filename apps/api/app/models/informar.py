"""Cadastro dos destinatários Threema dos botões INFORMAR (admin-only).

Uma linha por contexto ('logistica' | 'controle_estoque'); `recipients` guarda
os IDs Threema escolhidos (mesmo formato CSV do `.env`). O diretório de
nomes continua vindo do `.env` (threema_recipient_names/threema_recipients) —
aqui só persiste QUEM recebe cada relatório.
"""
from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ThreemaInformarConfig(Base, TimestampMixin):
    __tablename__ = "threema_informar_config"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    contexto: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    recipients: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
