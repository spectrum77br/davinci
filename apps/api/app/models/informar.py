"""Cadastro dos destinatários Threema dos botões INFORMAR (admin-only).

Uma linha por contexto ('logistica' | 'controle_estoque'); `recipients` guarda
os IDs Threema escolhidos (mesmo formato CSV do `.env`) — aqui persistia QUEM
recebe cada relatório. Desde a tarefa 16 (F2 e F3), quem recebe é escolhido em
Cadastros › Envios Threema, só por admin: as linhas ficam CONGELADAS como a
configuração antiga (reserva de emergência e fonte da semente; o PUT do
Informar responde 410). Apagá-las é tarefa futura, 30 dias depois da F4.

A linha de contexto `diretorio` (services/threema.CONTEXTO_CONTATOS) é a
exceção e guarda outra coisa: os CONTATOS AVULSOS, no formato `ID:Nome` do
`threema_recipient_names` do `.env`. Vinicius, 22/09/2026: precisava mandar
aviso pro "roma", que não tem login no DaVinci, e a única porta era editar o
`.env` do servidor. Fica aqui, e não em tabela nova, porque é o mesmo dado
que esta tabela já tirou do arquivo — quem lê é o mesmo
`parse_recipient_directory`.

Desde a migration 0507 (tarefa 16, C4) os contatos moram em `threema_contato`
(Cadastros › Envios Threema). A linha `diretorio` virou ESPELHO: o
`threema.espelhar_contatos` a reescreve a cada gravação de contato do
Cadastro, para que reverter o código não perca ninguém. Sai junto com as
outras linhas (tarefa futura).
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
