"""Cadastros › Contabilidade › Compra (23/10/2026).

Eduardo: "do lado de emissão de serviço vai ter a nova aba compra [...] deixar
opção de fazer pedido/compra; selecionar qual empresa vamos fazer a compra (as 4
que temos de NF); puxar valor do faturamento (opção para selecionar o mês) e
deixar opção para selecionar a quantidade a ser comprada (valor por embalagem);
cadastrar produto que será feito compra — nome: embalagem, ncm: 39232110; deixar
opção de cadastrar outros produtos caso mude". Modelo de compra: a NF-e 13917 da
COMERCIAL LEMES para a VITA (450 × Embalagem a R$ 9,30, CFOP 5102).

Quem COMPRA é a empresa do grupo (`companies`); quem VENDE é uma das contas
Bling de NF (emitentes de `nf_nota` que não são do grupo — as mesmas que viram
tomador automático na Emissão de Serviço, `services/nfse/contas_bling.py`). A
compra registrada vira a planilha de importação de pedidos do Bling da
vendedora (o mesmo CSV de 41 colunas de `services/nf_relatorio.py`); a NF sai lá.

- `CompraProduto`: o catálogo do que se compra (nasce com "Embalagem").
- `CompraPedido`: uma compra, com a foto do comprador e do faturamento do mês
  no momento em que foi registrada (a planilha sai igual mesmo que o cadastro mude).
- `CompraPedidoItem`: as linhas (produto, quantidade, preço por unidade).
- `CompraVendedora` (23/10, Eduardo: "na teoria tem que ser automático"): as contas
  Bling de NF de onde se compra, com o perfil do AdsPower logado nela e a chave
  "automático". Compra de vendedora automática vai para a fila (`na_fila`) e o robô
  do Mac (~/nf-executor/compra_nf.py) importa, emite, manda à SEFAZ (um envio só) e
  devolve número e chave (`nf_emitida`) ou o motivo (`erro`).
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# Número do pedido na planilha do Bling: precisa ser único no Bling da vendedora
# (número repetido = o Bling recusa o arquivo INTEIRO). Os pedidos de venda do
# Bling principal estão em ~305 mil (6 dígitos) e os do ML de 100% levam o 9 na
# frente (7 dígitos começando com 9): a faixa 8.000.001+ não encosta em nenhum.
NUMERO_INICIAL = 8_000_001

# registrada → (emitir automático) na_fila → emitindo → nf_emitida | erro; cancelada.
# Manual (planilha) continua: registrada → nf_emitida anotando o número.
STATUS = ("registrada", "na_fila", "emitindo", "nf_emitida", "erro", "cancelada")


class CompraVendedora(Base, TimestampMixin):
    __tablename__ = "compra_vendedora"
    __table_args__ = (
        CheckConstraint("cnpj ~ '^[0-9]{14}$'", name="cnpj_14"),
        CheckConstraint(
            "NOT automatico OR adspower_serial IS NOT NULL", name="automatico_tem_perfil"
        ),
    )

    cnpj: Mapped[str] = mapped_column(String(14), primary_key=True)
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    uf: Mapped[str | None] = mapped_column(String(2), nullable=True)
    # user_id do perfil do AdsPower logado no Bling dela (ex.: k13ksbpr = Bling 44)
    adspower_serial: Mapped[str | None] = mapped_column(Text, nullable=True)
    automatico: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class CompraProduto(Base, TimestampMixin):
    __tablename__ = "compra_produto"
    __table_args__ = (
        CheckConstraint("ncm ~ '^[0-9]{8}$'", name="ncm_8_digitos"),
        CheckConstraint("cfop IS NULL OR cfop ~ '^[1-7][0-9]{3}$'", name="cfop_4_digitos"),
        CheckConstraint("preco_padrao IS NULL OR preco_padrao > 0", name="preco_positivo"),
        Index("uq_compra_produto_nome", text("lower(nome)"), unique=True),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    # SKU que a planilha leva: o Bling da vendedora casa o produto por ele (a001 =
    # embalagem, com NCM 3923.21.10 na regra fixa do ncm_fix).
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    ncm: Mapped[str] = mapped_column(String(8), nullable=False)
    # Vazio = a tela sugere 5102 (mesma UF) / 6102 (UF diferente).
    cfop: Mapped[str | None] = mapped_column(String(4), nullable=True)
    unidade: Mapped[str] = mapped_column(String(6), nullable=False, server_default="UN")
    preco_padrao: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class CompraPedido(Base, TimestampMixin):
    __tablename__ = "compra_pedido"
    __table_args__ = (
        CheckConstraint(f"status IN {STATUS}", name="status_valido"),
        CheckConstraint("modo IN ('quantidade', 'percentual')", name="modo_valido"),
        CheckConstraint("vendedor_cnpj ~ '^[0-9]{14}$'", name="vendedor_cnpj_14"),
        CheckConstraint("cfop ~ '^[1-7][0-9]{3}$'", name="cfop_4_digitos"),
        CheckConstraint("extract(day from competencia) = 1", name="competencia_dia_1"),
        CheckConstraint(
            "(modo = 'percentual') = (percentual IS NOT NULL)", name="percentual_so_no_modo"
        ),
        CheckConstraint("total > 0", name="total_positivo"),
        CheckConstraint(
            "status <> 'nf_emitida' OR nf_numero IS NOT NULL", name="nf_emitida_tem_numero"
        ),
        Index("ix_compra_pedido_competencia", "competencia"),
        Index("ix_compra_pedido_company", "company_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    numero: Mapped[int] = mapped_column(
        BigInteger, Identity(start=NUMERO_INICIAL), unique=True, nullable=False
    )
    company_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False
    )
    vendedor_cnpj: Mapped[str] = mapped_column(String(14), nullable=False)
    vendedor_nome: Mapped[str] = mapped_column(Text, nullable=False)
    vendedor_uf: Mapped[str | None] = mapped_column(String(2), nullable=True)
    competencia: Mapped[date] = mapped_column(Date, nullable=False)  # mês do faturamento (dia 1)
    data_pedido: Mapped[date] = mapped_column(Date, nullable=False)  # coluna "Data" da planilha
    # Foto do faturamento do mês da compradora quando a compra foi registrada.
    faturamento: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    modo: Mapped[str] = mapped_column(String(12), nullable=False)
    percentual: Mapped[Decimal | None] = mapped_column(Numeric(9, 4), nullable=True)
    cfop: Mapped[str] = mapped_column(String(4), nullable=False)
    # Foto do comprador (razão, CNPJ, IE, endereço) como vai na planilha.
    comprador: Mapped[dict] = mapped_column(JSONB, nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default="registrada")
    nf_numero: Mapped[str | None] = mapped_column(Text, nullable=True)
    nf_chave: Mapped[str | None] = mapped_column(String(44), nullable=True)
    # emissão automática: quando pediu, quando o robô pegou, quantas vezes, o motivo do erro
    emissao_pedida_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    emissao_iniciada_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    emissao_tentativas: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0"
    )
    emissao_erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    nf_emitida_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    observacoes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class CompraPedidoItem(Base, TimestampMixin):
    __tablename__ = "compra_pedido_item"
    __table_args__ = (
        CheckConstraint("quantidade > 0", name="quantidade_positiva"),
        CheckConstraint("valor_unitario > 0", name="unitario_positivo"),
        CheckConstraint("ncm ~ '^[0-9]{8}$'", name="ncm_8_digitos"),
        Index("ix_compra_pedido_item_pedido", "pedido_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    pedido_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("compra_pedido.id", ondelete="CASCADE"), nullable=False
    )
    produto_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("compra_produto.id", ondelete="SET NULL"), nullable=True
    )
    ordem: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    ncm: Mapped[str] = mapped_column(String(8), nullable=False)
    unidade: Mapped[str] = mapped_column(String(6), nullable=False, server_default="UN")
    quantidade: Mapped[int] = mapped_column(Integer, nullable=False)
    valor_unitario: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    valor_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
