from sqlalchemy import BigInteger, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class SituacaoBling(Base, TimestampMixin):
    """Catalogo de situacoes de pedido do Bling (id Bling -> nome).

    Tabela criada na migration 0017 (vinda do userdb); o model existe para
    consultas ORM e para o create_all dos testes.
    """

    __tablename__ = "situacao_bling"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    nome: Mapped[str] = mapped_column(Text, nullable=False)
