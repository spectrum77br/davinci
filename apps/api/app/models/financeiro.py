from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, Text, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class FinanceiroConsorcio(Base, TimestampMixin):
    __tablename__ = "financeiro_consorcio"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    credito: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    emp: Mapped[str | None] = mapped_column(Text, nullable=True)
    grupo: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cota: Mapped[int | None] = mapped_column(Integer, nullable=True)
    alienacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    nf: Mapped[str | None] = mapped_column(Text, nullable=True)
    parc_a_pagar: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lance: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    valor_parc: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    atualizado: Mapped[date | None] = mapped_column(Date, nullable=True)
    fundo_reserva: Mapped[str | None] = mapped_column(Text, nullable=True)
    obs: Mapped[str | None] = mapped_column(Text, nullable=True)


class FinanceiroSuprimentos(Base, TimestampMixin):
    __tablename__ = "financeiro_suprimentos"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    produto: Mapped[str | None] = mapped_column(Text, nullable=True)
    modelo: Mapped[str | None] = mapped_column(Text, nullable=True)
    nome_comercial: Mapped[str | None] = mapped_column(Text, nullable=True)
    certificado: Mapped[str | None] = mapped_column(Text, nullable=True)
    numero: Mapped[str | None] = mapped_column(Text, nullable=True)
    valor: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    inicio: Mapped[date | None] = mapped_column(Date, nullable=True)
    fim: Mapped[date | None] = mapped_column(Date, nullable=True)


class FinanceiroSimulacao(Base, TimestampMixin):
    __tablename__ = "financeiro_simulacao"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    # Cabeçalho
    numero_cotacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    cliente: Mapped[str | None] = mapped_column(Text, nullable=True)
    data: Mapped[date | None] = mapped_column(Date, nullable=True)
    processo: Mapped[str | None] = mapped_column(Text, nullable=True)
    exportador: Mapped[str | None] = mapped_column(Text, nullable=True)
    pais_origem: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Mercadoria
    descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    fornecedor: Mapped[str | None] = mapped_column(Text, nullable=True)
    quantidade: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ncm: Mapped[str | None] = mapped_column(Text, nullable=True)
    descricao_ncm: Mapped[str | None] = mapped_column(Text, nullable=True)
    invoice_numero: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Logística
    porto_origem: Mapped[str | None] = mapped_column(Text, nullable=True)
    porto_destino: Mapped[str | None] = mapped_column(Text, nullable=True)
    etd: Mapped[date | None] = mapped_column(Date, nullable=True)
    eta: Mapped[date | None] = mapped_column(Date, nullable=True)
    taxa_cambio: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    # Custos USD
    frete_seguro_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    valor_unitario_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    # Alíquotas NCM (snapshot)
    aliquota_ii: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    aliquota_ipi: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    aliquota_pis: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    aliquota_cofins: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    # Outras taxas (BRL — taxas brasileiras fixas; entram no cálculo USD ÷ câmbio)
    taxa_siscomex_brl: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    armazenagem_brl: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    despachante_sda_brl: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    despachante_honorarios_brl: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    corretagem_cambio_brl: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    inspecao_brl: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    outras_taxas_brl: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    taxa_bl_brl: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    # Alíquotas finais
    aliquota_taxas_gerais: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    aliquota_impostos_fed: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    aliquota_icms: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    frete_nacional_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    aliquota_intermediacao: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    # Vínculo com o lote da Importação (pra anexar o PDF da simulação)
    lote_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("import_lotes.id", ondelete="SET NULL"),
        nullable=True,
    )


class DNPConfig(Base):
    __tablename__ = "dnp_config"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    dolar_dia: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    certificado: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class DNPProduto(Base, TimestampMixin):
    __tablename__ = "dnp_produtos"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    produto: Mapped[str | None] = mapped_column(Text, nullable=True)
    link: Mapped[str | None] = mapped_column(Text, nullable=True)
    fabrica: Mapped[str | None] = mapped_column(Text, nullable=True)
    modelo: Mapped[str | None] = mapped_column(Text, nullable=True)
    moq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    foto_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    valor_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    projecao_compra: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fator: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    venda_estimada: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    frete: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    comissao: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    inmetro: Mapped[str | None] = mapped_column(Text, nullable=True)
    obs: Mapped[str | None] = mapped_column(Text, nullable=True)


class NCMCache(Base):
    __tablename__ = "ncm_cache"

    ncm: Mapped[str] = mapped_column(Text, primary_key=True)
    descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    aliquota_ii: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    aliquota_ipi: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    aliquota_pis: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    aliquota_cofins: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
