"""Personagens — o elenco da casa para os vídeos.

Eduardo, 21/09/2026: "os personagens são ideias de personagens que podem ser
usadas nos vídeos dadas pela gente no DaVinci (...) vão aparecer também no
nosso domínio".

## O que um personagem é, na prática

Não é um texto de inspiração: é uma IDENTIDADE que precisa sair igual em todo
vídeo. O roteiro que já rodava em produção pede exatamente isso —
"Preserve the character's face, blonde hair, body proportions and identity" —
e chama o personagem por um id de gerador (`<<<48dbb6ed-…>>>`) ou por apelido
(`@Lívia`). Por isso o cadastro tem três partes, e não uma:

- `nome` — como a equipe fala dele ("Lívia").
- `descricao` — quem é ("estudante brasileira de 22 anos").
- `referencia` — a etiqueta que o GERADOR entende, colada dentro do prompt.
  É ela que faz o rosto sair o mesmo; sem ela o roteiro descreve uma pessoa
  genérica e cada geração inventa outra.

Mais as fotos, que são a referência visual que a agência olha.

`referencia` é texto livre porque a etiqueta muda de ferramenta pra
ferramenta (UUID numa, `@apelido` noutra) e o DaVinci não fala com nenhuma
delas — ele guarda e entrega.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class MarketingPersonagem(Base, TimestampMixin):
    __tablename__ = "marketing_personagens"
    __table_args__ = (
        # Nome repetido vira dois "Lívia" no select do roteiro e ninguém sabe
        # qual é qual. Mesmo molde do cadastro de Marcas.
        Index(
            "ix_marketing_personagens_nome_unico",
            func.lower(text("nome")),
            unique=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    # A etiqueta que o gerador de vídeo entende — o `<<<uuid>>>` ou o `@nome`
    # que vai DENTRO do texto do roteiro.
    referencia: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ativo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    imagens: Mapped[list[MarketingPersonagemImagem]] = relationship(
        back_populates="personagem",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="MarketingPersonagemImagem.created_at",
    )


class MarketingPersonagemImagem(Base, TimestampMixin):
    """Foto de referência do personagem. Só imagem — sem PDF, sem vídeo."""

    __tablename__ = "marketing_personagem_imagens"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    personagem_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketing_personagens.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_name: Mapped[str] = mapped_column(String(256), nullable=False)
    file_mime: Mapped[str | None] = mapped_column(String(128), nullable=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # "personagens/<personagem_id>/<nome>", relativo a settings.uploads_dir.
    file_rel: Mapped[str] = mapped_column(String(512), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    personagem: Mapped[MarketingPersonagem] = relationship(back_populates="imagens")
