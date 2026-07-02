from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel


class CertificateOut(BaseModel):
    id: UUID
    company_id: UUID
    filename: str
    content_type: str | None = None
    size_bytes: int
    label: str | None = None
    expires_at: date | None = None
    notes: str | None = None
    # Nunca expõe o material cifrado — só sinaliza se há senha guardada.
    has_password: bool = False
    uploaded_by: UUID | None = None
    uploaded_by_name: str | None = None
    created_at: datetime
    updated_at: datetime


class CertificatePasswordOut(BaseModel):
    password: str | None = None


class CertificatePatch(BaseModel):
    label: str | None = None
    expires_at: date | None = None
    notes: str | None = None
    # `password` presente e não-vazio troca a senha; presente e vazio/nulo
    # remove a senha guardada; ausente mantém a atual (exclude_unset).
    password: str | None = None
