"""As regras do AGENTE no e-mail (12/11/2026, aba E-mail › Agente).

O agente do atendimento responde o e-mail como extensão do atendimento — a
mesma chave da loja (`services/atendimento/email_agente.py`). Estas são as
regras SÓ do e-mail: entram no pacote do agente quando o canal é e-mail. Só
admin edita (`routers/mail_agente.py`).

`chave` marca a regra que o CÓDIGO também conhece (`formulario_sac`: o pedido
do formulário de SAC do site, que só sai sozinho com o link do formulário da
marca no texto — `email_agente.pedido_do_formulario`). As outras são texto
livre. A regra com `responde_sozinho` sai sozinha quando o agente a cita.
"""

from uuid import UUID, uuid4

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class MailAgenteRegra(Base, TimestampMixin):
    __tablename__ = "mail_agente_regras"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    chave: Mapped[str | None] = mapped_column(String(32), unique=True)
    titulo: Mapped[str] = mapped_column(String(120), nullable=False)
    instrucao: Mapped[str] = mapped_column(Text, nullable=False)
    ativa: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    # Onde vale: os sites ("uranyx", "charlots"); vazio = todos.
    sites: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # A resposta que segue esta regra sai sozinha (no "Responde sozinho" do site),
    # com o agente citando a regra (`email:<id>`) e as travas de sempre.
    responde_sozinho: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    ordem: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    updated_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
