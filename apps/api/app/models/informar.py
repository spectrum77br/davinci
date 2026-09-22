"""Cadastro dos destinatários Threema dos botões INFORMAR (admin-only).

Uma linha por contexto ('logistica' | 'controle_estoque'); `recipients` guarda
os IDs Threema escolhidos (mesmo formato CSV do `.env`) — aqui persiste QUEM
recebe cada relatório.

A linha de contexto `diretorio` (services/threema.CONTEXTO_CONTATOS) é a
exceção e guarda outra coisa: os CONTATOS AVULSOS, no formato `ID:Nome` do
`threema_recipient_names` do `.env`. Vinicius, 22/09/2026: precisava mandar
aviso pro "roma", que não tem login no DaVinci, e a única porta era editar o
`.env` do servidor. Fica aqui, e não em tabela nova, porque é o mesmo dado
que esta tabela já tirou do arquivo — quem lê é o mesmo
`parse_recipient_directory`.
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
