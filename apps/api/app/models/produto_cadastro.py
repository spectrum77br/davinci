"""Cadastros › Produtos: a LISTA OFICIAL de categorias e tags de envio (M-29, migration 0508).

Pedido M-29 do Harry (24/11/2026): uma tabela Tipo | Nome, com os tipos "Categoria" e
"Tag de envio" no começo. Os filtros Tags e Categoria do Produtos 2 (M-30) leem esta lista.

  • `produto_cadastro_tipos` — os tipos. `chave` marca os dois tipos que têm regra ('categoria',
    'tag_envio'): a regra segue o tipo mesmo depois de renomeado (renomear tipo é permitido).
    Tipo criado na tela fica sem chave (só lista).
  • `produto_cadastros` — os nomes de cada tipo. Nome repetido no mesmo tipo é recusado, sem
    olhar maiúsculas ("Celular" = "celular"): índice único em (tipo_id, lower(nome)).

A ligação produto → categoria NÃO mora aqui: continua na base do ERP (own_categories /
own_product_categories) e é pelo NOME. Por isso excluir ou renomear uma Categoria que ainda tem
produto é recusado no servidor (routers/produto_cadastros.py). A tag de envio do produto é o fim
do SKU (dg105.sp e dg105.sp+a001.sp têm .sp): nada é gravado por produto.

M-107 (27/11/2026, migration 0521): o tipo "Cor" (chave 'cor'), na Tabela "Produtos 2". A cor
de cada produto mora no DaVinci, em `produto_regras.cor_id` (pelo código sem a tag de envio, a
chave da Margem mín), apontando para o NOME da cor aqui (o id): renomear a cor muda o que todos os
produtos mostram; cor em uso não sai (a mesma regra da Categoria, routers/produto_cadastros.py).
"""

from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

CHAVE_CATEGORIA = "categoria"
CHAVE_TAG_ENVIO = "tag_envio"
CHAVE_COR = "cor"  # M-107 (migration 0521)
NOME_MAX = 60


class ProdutoCadastroTipo(TimestampMixin, Base):
    __tablename__ = "produto_cadastro_tipos"
    __table_args__ = (Index("uq_produto_cadastro_tipos_nome", text("lower(nome)"), unique=True),)

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    nome: Mapped[str] = mapped_column(String(NOME_MAX), nullable=False)
    chave: Mapped[str | None] = mapped_column(String(30), nullable=True, unique=True)


class ProdutoCadastro(TimestampMixin, Base):
    __tablename__ = "produto_cadastros"
    __table_args__ = (
        Index("uq_produto_cadastros_tipo_nome", "tipo_id", text("lower(nome)"), unique=True),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    tipo_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("produto_cadastro_tipos.id", ondelete="RESTRICT"),
        nullable=False,
    )
    nome: Mapped[str] = mapped_column(String(NOME_MAX), nullable=False)
