from datetime import date
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Date, ForeignKey, LargeBinary, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class CompanyCertificate(Base, TimestampMixin):
    """Certificado digital (.p12/.pfx) de uma empresa.

    O arquivo e a senha ficam SEMPRE cifrados (AES-GCM, `nonce || ct`, mesma
    chave dos tokens de integração — `app/security/cipher.py`). Em claro eles
    só existem em memória no momento do download/reveal. Acesso restrito a
    admin (ver `app/routers/company_certificates.py`).
    """

    __tablename__ = "company_certificates"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # AES-GCM (nonce || ct) do arquivo .p12/.pfx.
    blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # Senha do certificado, também cifrada. NULL = sem senha guardada.
    password_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
