"""Margem mín, DUIMP padrão e Condição especial POR PRODUTO (M-74, migration 0516).

Pedido M-74 do Harry (26/11/2026): os três valores, que eram só do segmento (Cadastros ›
Segmentos), passam a poder ser preenchidos no cadastro do produto (Produtos 2). Em branco, o
produto continua usando o do segmento, como sempre: no dia em que for ao ar as duas tabelas estão
VAZIAS e nada muda (Margem, robô que segura pedido, nota).

A chave é o `sku_base`: cada peça do código sem a TAG DE ENVIO do fim (a lista "Tag de envio" de
Cadastros › Produtos), em minúsculas (services/produto_regras.sku_base): dg019.ra → dg019;
dg019.ra+a020.ra → dg019+a020; b001.12.sp → b001.12; uaf001m1.110 → uaf001m1.110. O valor vale
para todas as tags da mesma numeração, e o kit tem o seu próprio valor. Tamanho de mala,
capacidade e voltagem continuam na chave (b001.12 e b001.24 são produtos diferentes).

  • `produto_regras` — uma linha por produto, criada no primeiro valor preenchido:
      - `margem_min`: em FRAÇÃO como `segments.min_margin` (0,12 = 12%), de 0 a 1. Entra na
        margem mínima do pedido (view vw_bling_pedidos) ANTES do segmento;
      - `duimp_padrao`: o texto que vai na nota quando o SKU não tem DUIMP na Importação, antes
        do texto do segmento (até 75 letras, sem "|", como no segmento);
      - `sku`: cópia gerada do `sku_base`, só para o Histórico mostrar o nome do produto (o
        gatilho do Histórico procura a coluna `sku`).
    Apagar o valor (vazio) deixa a coluna NULL: o produto volta a herdar do segmento.
  • `produto_condicoes_especiais` — a Condição especial do produto: período (data do pedido, no
    fuso de São Paulo, os dois dias inclusive) e piso (margem mínima especial, em fração; vazio =
    aprova qualquer margem). Quando o produto tem condição no dia do pedido, vale só a dele; sem
    condição do produto, as do segmento continuam valendo como hoje
    (routers/margens._MARGEM_DATA_ESPECIAL_SQL). Tabela própria (e não uma coluna nova em
    `segment_special_dates`) para não mexer em nada do que o segmento já faz.

M-73 (26/11/2026, migration 0518): `produto_regras.centro_custo_id`, o Centro de custo do produto
(Cadastros › Centro de custo). Vazio = o departamento do produto, calculado (não copiado).

M-107 (27/11/2026, migration 0521): `produto_regras.cor_id`, a Cor do produto (um nome do tipo
"Cor" de Cadastros › Produtos). Vazio = sem cor: não há cor herdada nem tirada do nome.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

DUIMP_PADRAO_MAX = 75

# FIXOS: a migration 0516 escreve os mesmos textos. A chave pode ter ponto (b001.12): só a tag de
# envio sai dela.
CHECK_SKU_BASE = "sku_base <> '' AND sku_base = lower(sku_base)"
CHECK_MARGEM_MIN = "margem_min BETWEEN 0 AND 1"
CHECK_DUIMP = "strpos(duimp_padrao, '|') = 0"
CHECK_PERIODO = "pedido_ate >= pedido_de"
CHECK_PISO = "piso BETWEEN -1 AND 1"


class ProdutoRegra(Base):
    __tablename__ = "produto_regras"
    __table_args__ = (
        CheckConstraint(CHECK_SKU_BASE, name="sku_base"),
        CheckConstraint(CHECK_MARGEM_MIN, name="margem_min"),
        CheckConstraint(CHECK_DUIMP, name="duimp_padrao"),
    )

    sku_base: Mapped[str] = mapped_column(Text, primary_key=True)
    sku: Mapped[str | None] = mapped_column(Text, Computed("sku_base", persisted=True))
    margem_min: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    duimp_padrao: Mapped[str | None] = mapped_column(String(DUIMP_PADRAO_MAX), nullable=True)
    atualizado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    # M-73 (migration 0518): o Centro de custo escolhido para o produto. Vazio = o departamento
    # do produto, calculado e não copiado (services/produto_regras.centro_herdado). RESTRICT: um
    # centro que algum produto escolheu não sai (a aba Centro de custo já recusa antes).
    centro_custo_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("centros_custo.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    # M-107 (migration 0521): a Cor do produto (produto_cadastros do tipo de chave 'cor'; o
    # servidor confere o tipo ao gravar). SET NULL: a cor excluída deixa o produto sem cor (a aba
    # Cadastros › Produtos já recusa excluir cor em uso; o SET NULL cobre só a corrida).
    cor_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("produto_cadastros.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )


class ProdutoCondicaoEspecial(Base):
    __tablename__ = "produto_condicoes_especiais"
    __table_args__ = (
        CheckConstraint(CHECK_SKU_BASE, name="sku_base"),
        CheckConstraint(CHECK_PERIODO, name="periodo"),
        CheckConstraint(CHECK_PISO, name="piso"),
        Index("ix_produto_condicoes_especiais_sku_base", "sku_base"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    sku_base: Mapped[str] = mapped_column(Text, nullable=False)
    sku: Mapped[str | None] = mapped_column(Text, Computed("sku_base", persisted=True))
    pedido_de: Mapped[date] = mapped_column(Date, nullable=False)
    pedido_ate: Mapped[date] = mapped_column(Date, nullable=False)
    piso: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    criado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
