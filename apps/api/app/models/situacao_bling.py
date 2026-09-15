from sqlalchemy import BigInteger, Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class SituacaoBling(Base, TimestampMixin):
    """Catalogo de situacoes de pedido do Bling (id Bling -> nome).

    Tabela criada na migration 0017 (vinda do userdb); o model existe para
    consultas ORM e para o create_all dos testes.

    `ativo` (migration 0270): o sync diário (services/bling_situacoes_sync)
    espelha o módulo Vendas do Bling — situação que o Bling apagou vira
    `ativo = false` e some dos dropdowns, mas a linha FICA: pedidos antigos em
    `bling_orders.situacao` ainda apontam pro id e precisam do nome.
    """

    __tablename__ = "situacao_bling"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    ativo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
