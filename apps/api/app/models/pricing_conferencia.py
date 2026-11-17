"""Conferência de preços da Tabela de Preços (02/11/2026, migration 0442).

Eduardo, tarefa "Melhorias na Tabela de Preços": botão só de administrador com
"% margem de erro" que compara o preço da Tabela com o preço do anúncio em
cada conta e marca (!) na célula quando passa da margem.

Uma linha por célula (conta × linha da tabela) com o resultado da ÚLTIMA
conferência do departamento (`services/pricing/conferencia.py`). A margem não
mora aqui: a tela compara `preco_tabela` com o `preco` de cada anúncio com a %
que o administrador digitar, sem precisar conferir de novo.

`anuncios`: um item por anúncio lido — {"anuncio": external_id, "variacao":
variation_id | null, "preco": float | null, "preco_promocional": float | null,
"ativo": bool | null, "motivo": str | null}. `preco` é o MESMO conceito de
preço que o envio da Tabela grava na plataforma (`conferencia_leitores`).

Margem no servidor (M-2, 17/11/2026, migration 0499): a "% margem de erro"
saiu do navegador do administrador e mora em `PricingConferenciaMargem` (a
Geral, uma por departamento) e `PricingConferenciaMargemProduto` (a de cada
linha da Tabela). A comparação continua na tela: a conferência grava o preço
lido, e a tela marca (!) com a margem do produto ou, sem ela, a Geral.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Situação da célula na última conferência.
SITUACAO_CONFERIDA = "conferida"  # tem anúncio e a conta foi lida
SITUACAO_SEM_ANUNCIO = "sem_anuncio"  # o envio não teria para onde mandar
SITUACAO_ERRO_CONTA = "erro_conta"  # a conta inteira falhou (token, acesso)
SITUACAO_NAO_SUPORTADA = "nao_suportada"  # plataforma sem leitor de preço
SITUACOES = (
    SITUACAO_CONFERIDA,
    SITUACAO_SEM_ANUNCIO,
    SITUACAO_ERRO_CONTA,
    SITUACAO_NAO_SUPORTADA,
)


class PricingConferencia(Base):
    __tablename__ = "pricing_conferencia"
    __table_args__ = (
        CheckConstraint(
            "situacao IN ('conferida', 'sem_anuncio', 'erro_conta', 'nao_suportada')",
            name="situacao",
        ),
    )

    pricing_account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("pricing_accounts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    pricing_product_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("pricing_products.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Slug da aba (celular/mala/eletro/...): cada conferência regrava as
    # células do departamento dela conforme lê cada conta e, ao terminar bem,
    # tira as que saíram (não mandam mais preço).
    department: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    preco_tabela: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    situacao: Mapped[str] = mapped_column(Text, nullable=False)
    motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    anuncios: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    job_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("background_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    conferido_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


# Margem de erro sem nada gravado (a mesma da faixa roxa de antes): 5%.
MARGEM_PADRAO_PCT = Decimal("5")


class PricingConferenciaMargem(Base):
    """A margem Geral de um departamento e as duas caixas do menu "Margem ▾"
    (M-2, 17/11/2026). Sem linha = 5% e as duas ligadas.

    `geral_ligada` desligada: produto sem margem própria não é conferido (sem
    (!) e sem o motivo "fora da margem" do cartão). `por_produto_ligada`
    desligada: as margens próprias ficam gravadas, mas todos usam a Geral."""

    __tablename__ = "pricing_conferencia_margem"
    __table_args__ = (CheckConstraint("margem_pct BETWEEN 0 AND 100", name="margem_pct"),)

    # Slug da aba (celular/mala/eletro/...), o mesmo de `PricingConferencia.department`.
    department: Mapped[str] = mapped_column(Text, primary_key=True)
    margem_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, server_default=text("5")
    )
    geral_ligada: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    por_produto_ligada: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    atualizado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class PricingConferenciaMargemProduto(Base):
    """A margem própria de uma linha da Tabela (M-2, 17/11/2026): "quando um
    produto tem desconto, cadastro a margem dele aqui e a tabela para de
    mostrar irregularidade nele". Sem linha = usa a Geral do departamento."""

    __tablename__ = "pricing_conferencia_margem_produto"
    __table_args__ = (CheckConstraint("margem_pct BETWEEN 0 AND 100", name="margem_pct"),)

    pricing_product_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("pricing_products.id", ondelete="CASCADE"),
        primary_key=True,
    )
    margem_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    atualizado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
