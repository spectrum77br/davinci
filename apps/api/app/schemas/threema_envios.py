"""Corpos das rotas de Cadastros › Envios Threema (`routers/threema_envios`,
tarefa 16)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

# Teto de itens por POST do robô do Mac (um item = um destinatário).
MAX_REGISTROS_ROBO = 200


class RegistroRoboIn(BaseModel):
    """Um envio que o Mac fez (um destinatário). `lote` agrupa os itens de
    uma mesma mensagem (mesmo texto para várias pessoas)."""

    chave: str = Field(max_length=60)
    criado_em: datetime | None = None
    destinatario: str | None = Field(default=None, max_length=16)
    ok: bool
    http_status: int | None = Field(default=None, ge=0, le=999)
    erro: str | None = Field(default=None, max_length=500)
    titulo: str | None = Field(default=None, max_length=2000)
    host: str | None = Field(default=None, max_length=200)
    via: str | None = Field(default=None, max_length=20)
    origem: str | None = Field(default=None, max_length=20)
    lote: str | None = Field(default=None, max_length=64)


class RegistrosRoboIn(BaseModel):
    itens: list[RegistroRoboIn] = Field(max_length=MAX_REGISTROS_ROBO)
