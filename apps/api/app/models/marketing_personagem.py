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
- `referencia` — a etiqueta que o gerador entende, colada dentro do prompt.
  É ATALHO, não fonte da verdade: `hf_20260918_…` e `exec-8a44219f-…` são ids
  DENTRO da ferramenta que gerou, e somem se o asset for apagado lá, se
  trocar de conta ou se a agência usar outro gerador.

O que DURA são os arquivos, e eles são o centro do cadastro (Eduardo,
22/09/2026): as fotos do rosto e o MP3 da VOZ. Medido nas 8 personas que a
equipe já usa: as 8 têm voz, as 8 têm link de vídeo de referência, e uma tem
4 imagens (variações de expressão). Por isso a agência BAIXA o arquivo — ele
funciona em qualquer ferramenta, a etiqueta só na que a criou.

`descricao` é texto longo de propósito: nas pastas reais ele traz perfil,
aplicação em venda ("funciona bem na fórmula 'descoberta genuína'") e as
expressões disponíveis. Estruturar isso em colunas quebraria na primeira
persona que fugisse do molde — e metade delas já foge.
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
    # Atalho pra quem usa a MESMA ferramenta que gerou o rosto. Pode quebrar;
    # o arquivo, não. Ver o docstring do módulo.
    referencia: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Referência em vídeo (um Shorts, normalmente): como a persona se move e
    # fala. Só http/https, validado na escrita — vira href no portal PHP.
    video_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    ativo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    arquivos: Mapped[list[MarketingPersonagemArquivo]] = relationship(
        back_populates="personagem",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="MarketingPersonagemArquivo.created_at",
    )


class MarketingPersonagemArquivo(Base, TimestampMixin):
    """O que a agência BAIXA: as fotos do rosto e o MP3 da voz.

    Uma tabela só, com `tipo`, porque as duas coisas seguem o mesmo caminho —
    mesma pasta, mesmo teto, mesma rota de bytes, mesma trava de MIME. O que
    muda é só a lista branca de extensão, escolhida por `tipo`.
    """

    __tablename__ = "marketing_personagem_arquivos"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    personagem_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketing_personagens.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # "imagem" | "voz"
    tipo: Mapped[str] = mapped_column(
        String(16), nullable=False, default="imagem", server_default=text("'imagem'")
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

    personagem: Mapped[MarketingPersonagem] = relationship(back_populates="arquivos")
