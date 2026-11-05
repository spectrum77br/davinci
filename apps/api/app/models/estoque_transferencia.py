from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Numeric, Sequence, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class EstoqueTransferencia(Base, TimestampMixin):
    """Transferência entre estoques feita pelo botão "Transferir estoque" do
    Controle de Estoque (Eduardo, 05/11/2026). Substitui a loja interna
    "Centro de Distribuição" do Bling (pedido de R$ 0,01 com quantidade
    positiva = saída e negativa = entrada): o DaVinci lança direto no estoque
    do Bling, sem pedido — SAÍDA na origem e depois ENTRADA no destino.

    `idem_key` vem da tela (uma por abertura do modal): clique duplo ou o
    navegador repetindo o POST devolvem a mesma transferência, sem lançar de
    novo. As linhas ficam em `estoque_transferencia_linhas`."""

    __tablename__ = "estoque_transferencias"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    # Número curto para a tela e para a observação do extrato do Bling.
    numero: Mapped[int] = mapped_column(
        BigInteger,
        Sequence("estoque_transferencias_numero_seq"),
        nullable=False,
        unique=True,
    )
    idem_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    criado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    criado_por_nome: Mapped[str | None] = mapped_column(Text, nullable=True)
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)


class EstoqueTransferenciaLinha(Base, TimestampMixin):
    """Uma linha da transferência: `quantidade` de `origem_sku` para
    `destino_sku` (mesmo produto entre lotes — dg010.ra → dg010.ci — ou troca
    de produto — uaf001m1.220 → uaf001m1.110).

    Duas pernas, cada uma UM POST /estoques, sempre nesta ordem: a SAÍDA na
    origem e, só depois que ela deu certo, a ENTRADA no destino (falha no meio
    deixa a peça faltando por alguns minutos, nunca sobrando para vender).

    Status de cada perna:
      aguardando (entrada antes da saída dar certo) → pendente → enviando
      (gravado ANTES do POST) → ok | falhou (o Bling com certeza não gravou:
      a entrada é tentada de novo sozinha) | conferindo (o Bling respondeu erro
      depois de receber: procura no extrato antes de qualquer coisa) →
      sem_confirmacao (10 min sem achar: a pessoa confere o extrato).
    `*_mov_ref` = o movimento do espelho (stock_movements) que confirmou a
    perna — não serve de prova para outra perna nem para uma devolução."""

    __tablename__ = "estoque_transferencia_linhas"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    transferencia_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("estoque_transferencias.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ordem: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    origem_sku: Mapped[str] = mapped_column(Text, nullable=False)
    origem_bling_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    destino_sku: Mapped[str] = mapped_column(Text, nullable=False)
    destino_bling_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quantidade: Mapped[int] = mapped_column(Integer, nullable=False)
    # Custo da ENTRADA no destino (o do cadastro do destino): sem ele o Bling
    # grava a entrada com custo 0 e mexe no custo médio.
    custo: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)

    saida_status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pendente", server_default=text("'pendente'")
    )
    saida_erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    saida_tentativa_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    saida_lancada_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    saida_mov_ref: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    entrada_status: Mapped[str] = mapped_column(
        Text, nullable=False, default="aguardando", server_default=text("'aguardando'")
    )
    entrada_erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    entrada_tentativa_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    entrada_lancada_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    entrada_mov_ref: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    # Tentativas automáticas da entrada que o Bling recusou (com certeza não
    # gravou). Passou de ENTRADA_AUTO_MAX: fica para a pessoa.
    entrada_auto_tentativas: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Quem resolveu à mão (conferiu o extrato) — o Histórico mostra quem.
    resolvido_manual: Mapped[str | None] = mapped_column(Text, nullable=True)
