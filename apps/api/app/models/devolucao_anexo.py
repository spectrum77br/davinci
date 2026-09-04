from uuid import UUID, uuid4

from sqlalchemy import BigInteger, ForeignKey, LargeBinary, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class DevolucaoAnexo(Base, TimestampMixin):
    """Foto/vídeo anexado a uma linha da tela Devoluções (ver alembic 0243).

    Serve de evidência pro chamado automático de devolução no Mercado Livre:
    as FOTOS sobem pela API (`ml_file_name` = nome que o ML devolveu no upload,
    pra não reenviar na retentativa); vídeo só fica guardado — a API do ML
    aceita JPG/PNG/PDF/TXT até 5 MB. Blob no próprio banco, servido pelo
    endpoint autenticado (mesmo esquema do chamado_anexo)."""

    __tablename__ = "devolucao_anexo"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    devolution_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("devolutions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    ml_file_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
