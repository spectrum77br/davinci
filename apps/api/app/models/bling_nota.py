from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class BlingNota(Base, TimestampMixin):
    """Conta Bling dedicada à emissão de NF.

    Propositalmente separada de `integrations`: são apps OAuth distintos
    da conta principal de sync e não devem se misturar.
    """

    __tablename__ = "bling_notas"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    # Nome da conta no Bling (ex.: "josefinaapp")
    nome: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    client_id: Mapped[str] = mapped_column(Text, nullable=False)
    # base64("client_id:client_secret") — header `Authorization: Basic`
    # usado no fluxo de refresh token da API v3 do Bling.
    basic_auth_b64: Mapped[str] = mapped_column(Text, nullable=False)
    # Code retornado pelo link de convite OAuth (uso único na troca inicial).
    authorization_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'active'")
    )
    # CNPJ/razão social do EMITENTE da conta — fixos por conta (cada conta
    # Bling emite por uma única empresa). Preenchidos pelo sync do Pós Vendas
    # a partir do primeiro XML autorizado; usados pra classificar a NF de um
    # pedido: conta com CNPJ == store_info.cnpj da loja → NF de embalagem;
    # qualquer outra conta → NF de produto (avulsa).
    cnpj: Mapped[str | None] = mapped_column(Text, nullable=True)
    emitente: Mapped[str | None] = mapped_column(Text, nullable=True)


class BlingNotaEmitida(Base, TimestampMixin):
    """NF-e espelhada das contas de emissão (`bling_notas`) pelo sync do
    Pós Vendas.

    A página Pós Vendas casa pedido ↔ nota SEM bater no Bling ao vivo: o
    cron lista as notas de cada conta e faz upsert aqui. O `valor` só existe
    no DETALHE da nota (a listagem do Bling não traz `valorNota`), então ele
    começa NULL e o próprio sync completa aos poucos, com teto por rodada.
    """

    __tablename__ = "bling_notas_emitidas"
    __table_args__ = (UniqueConstraint("conta_id", "bling_id"),)

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    conta_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("bling_notas.id", ondelete="CASCADE"),
        nullable=False,
    )
    # id da nota na API do Bling (GET /nfe/{id}).
    bling_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    numero: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Situação Bling (5=autorizada, 6=emitida DANFE, 7=registrada…).
    situacao: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Como o Bling entrega: "YYYY-MM-DD HH:MM:SS" em horário local (BRT),
    # guardado literal (naive) — é exibição/janela de casamento, não cálculo.
    data_emissao: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True, index=True
    )
    chave_acesso: Mapped[str | None] = mapped_column(Text, nullable=True)
    # CPF/CNPJ do destinatário, só dígitos — 2ª chave de casamento com
    # bling_orders.documento_destinatario.
    cpf_dest: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    nome_dest: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Complemento do endereço do contato NA NOTA: o fluxo de emissão grava
    # aqui o pedido do marketplace (numeroloja) — 1ª chave de casamento,
    # exata (medido 9/9 em produção, ago/2026).
    complemento: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    valor: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    # True depois que o detalhe foi buscado (mesmo que valorNota viesse
    # vazio) — evita rebuscar pra sempre nota sem valor.
    detalhe_ok: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
