"""Cadastros › Centro de custo (M-76, Harry 25/11/2026; migration 0514).

Celular, Eletro e Mala NÃO viram uma lista paralela: são as três raízes de Cadastros › Segmentos
(`segment_id` aponta para a raiz; `fixo` = true). A raiz "Catálogo" não é centro de custo e fica
de fora. O centro de custo alimenta o menu "Centro de custo" das Categorias de venda
(Cadastros › Produtos, M-75) e, depois da M-73, o do cadastro do produto no Produtos 2.

Centro novo (ex.: "Brinquedos") nasce SEM raiz (`segment_id` NULL): não vira botão na Tabela de
Preços, na Importação, nas Lojas… — essas telas têm Celular/Eletro/Mala fixos. Por isso ainda não
recebe Categoria de venda (services/categorias_venda.centro_para_nova_categoria).

- `nome`: até 60 letras, sem repetir sem olhar maiúsculas (índice único em lower(nome)).
  Os fixos não mudam de nome; o de um centro novo com raiz troca junto o nome da raiz (o slug não).
- `segment_id`: a raiz (único; RESTRICT: a raiz de um centro não sai pela aba Segmentos).
- `criado_por`: quem incluiu (SET NULL se a pessoa sair do cadastro de usuários).
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

NOME_MAX = 60


class CentroCusto(Base):
    __tablename__ = "centros_custo"
    __table_args__ = (Index("uq_centros_custo_nome", text("lower(nome)"), unique=True),)

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    nome: Mapped[str] = mapped_column(String(NOME_MAX), nullable=False)
    segment_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("segments.id", ondelete="RESTRICT"),
        nullable=True,
        unique=True,
    )
    fixo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    criado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
