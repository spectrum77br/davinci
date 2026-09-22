"""Requisição de personagem — a agência propõe, a casa decide.

Eduardo, 22/09/2026: "queria que deixasse criar personagem no studio dai iria
como requisicao pra aprovar se aprovado cria o personagem".

## Por que não é só um `MarketingPersonagem` com status

Porque ela carrega o que um personagem não carrega, e não deveria carregar
depois de aprovado: de ONDE veio o rosto, de onde veio a voz, e se existe
cessão escrita. São campos de decisão, não de cadastro — servem para alguém
olhar antes de dizer sim, e ficam como rastro de por que o sim foi dado.

## Por que os campos de origem são obrigatórios

Súmula 403 do STJ: "independe de prova do prejuízo a indenização pela publicação
não autorizada de imagem de pessoa com fins econômicos ou comerciais". Uso
comercial basta. Voz é direito da personalidade autônomo (CC arts. 11 a 21), e
foto de rosto com amostra de voz é dado pessoal sob LGPD.

Um canal externo que pudesse mandar rosto sem dizer de onde veio seria um cano
de importar passivo para dentro de casa com um clique em "aprovar". Com os
campos obrigatórios, é o contrário: a requisição vira o lugar onde a procedência
fica registrada, e quem aprova aprova sabendo.

O ARQUIVO não sobe aqui de propósito. Quem guarda a foto e o MP3 é quem responde
por eles — a casa carrega os arquivos depois de aprovar, junto com o papel da
cessão. A agência propõe a pessoa; ela não empurra o ativo.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# Fila curta de propósito: uma requisição ou está esperando, ou foi decidida.
STATUS_PENDENTE = "pendente"
STATUS_APROVADA = "aprovada"
STATUS_RECUSADA = "recusada"


class MarketingPersonagemRequisicao(Base, TimestampMixin):
    __tablename__ = "marketing_personagem_requisicoes"
    __table_args__ = (
        Index(
            "ix_marketing_personagem_requisicoes_fila",
            "status",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    justificativa: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── procedência ──
    origem_imagem: Mapped[str] = mapped_column(Text, nullable=False)
    origem_voz: Mapped[str] = mapped_column(Text, nullable=False)
    cessao: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    cessao_obs: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Mesma string de equipe que o token do portal carrega.
    equipe: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=STATUS_PENDENTE, server_default=text("'pendente'")
    )
    motivo: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Preenchido na aprovação: o rastro de que esta requisição virou aquele
    # personagem. É por ele que se audita a procedência de um rosto meses depois.
    personagem_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketing_personagens.id", ondelete="SET NULL"),
        nullable=True,
    )
    decidido_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decidido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
