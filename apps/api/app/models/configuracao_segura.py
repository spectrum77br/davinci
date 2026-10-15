"""Configurações guardadas cifradas (tokens de serviços de fora, 15/10/2026).

Primeiro uso: o token do Melhor Envio, que até aqui só vivia no `.env` do servidor
(onde ninguém da operação consegue trocar). A operação cola na tela, fica cifrado
como as credenciais das integrações, e o serviço lê daqui antes do `.env`.
"""

from __future__ import annotations

from sqlalchemy import LargeBinary, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ConfiguracaoSegura(Base, TimestampMixin):
    __tablename__ = "configuracoes_seguras"

    chave: Mapped[str] = mapped_column(String(60), primary_key=True)
    valor: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)  # encrypt_json
    detalhe: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    atualizado_por: Mapped[str | None] = mapped_column(String(255), nullable=True)
