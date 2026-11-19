"""Corpos das rotas de Cadastros › Envios Threema (`routers/threema_envios`,
tarefa 16)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

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


# ─────────────────────────────────────────────────────────── edição (C7)


class RefIn(BaseModel):
    """Quem recebe: um usuário, um grupo ou um contato externo."""

    tipo: Literal["usuario", "grupo", "contato"]
    id: UUID


class MembroIn(BaseModel):
    """Membro de grupo: usuário ou contato externo."""

    tipo: Literal["usuario", "contato"]
    id: UUID


class EnvioPatchIn(BaseModel):
    """PATCH /envios/{chave}. Campo ausente = não mexe; `observacao: null`
    apaga a observação."""

    ativo: bool | None = None
    observacao: str | None = Field(default=None, max_length=2000)
    destinatarios: list[RefIn] | None = Field(default=None, max_length=100)
    reaviso_horas: int | None = Field(default=None, ge=1, le=24 * 30)


class RegraIn(BaseModel):
    destinatarios: list[RefIn] = Field(max_length=100)


class GrupoIn(BaseModel):
    nome: str = Field(max_length=200)
    descricao: str | None = Field(default=None, max_length=1000)
    membros: list[MembroIn] = Field(default_factory=list, max_length=100)


class GrupoPatchIn(BaseModel):
    nome: str | None = Field(default=None, max_length=200)
    descricao: str | None = Field(default=None, max_length=1000)
    membros: list[MembroIn] | None = Field(default=None, max_length=100)


class ContatoIn(BaseModel):
    nome: str = Field(max_length=200)
    threema_id: str = Field(max_length=16)
    observacao: str | None = Field(default=None, max_length=1000)


class ContatoPatchIn(BaseModel):
    nome: str | None = Field(default=None, max_length=200)
    threema_id: str | None = Field(default=None, max_length=16)
    ativo: bool | None = None
    observacao: str | None = Field(default=None, max_length=1000)


class EmergenciaIn(BaseModel):
    usar_legado: bool
