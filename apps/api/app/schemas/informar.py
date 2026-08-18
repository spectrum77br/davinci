"""Schemas dos botões INFORMAR (cadastro de destinatários + envio)."""
from __future__ import annotations

from pydantic import BaseModel

from app.schemas.logistica import ThreemaDestinatarioOut


class InformarConfigOut(BaseModel):
    contexto: str
    recipients: list[str]
    destinatarios: list[ThreemaDestinatarioOut]


class InformarConfigIn(BaseModel):
    recipients: list[str] = []


class InformarEnviarOut(BaseModel):
    pedidos: int
    mensagens: int
    sent: list[str]
    failed: list[str]
