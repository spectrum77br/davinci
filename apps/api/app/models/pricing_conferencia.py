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
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, Text, text
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
