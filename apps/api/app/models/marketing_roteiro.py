"""Roteiros — o briefing de gravação, fora da linha de produção.

Eduardo, 21/09/2026: "criaremos em marketing uma aba roteiro e lá poderemos
escrever o roteiro pro item (...) desacoplar o roteiro daqui (...) em roteiro
talvez a gente já mande pra alguém específico (...) uma regra também de que se
não preenchido vai para os 2".

## Por que sair de `marketing_creatives.roteiro`

Três motivos medidos, não estéticos:

1. **O DELETE do criativo levava o texto junto.** `delete_creative`
   (routers/marketing_creatives.py) faz `session.delete(row)`: apagar uma
   linha de produção apagava o briefing que alguém escreveu — justamente o
   ativo que se queria preservar.
2. **Um roteiro serve VÁRIOS vídeos.** Duas agências recebendo o mesmo
   briefing, ou três cortes do mesmo roteiro, obrigavam a copiar o texto (e as
   imagens de referência) linha a linha.
3. **Endereçar não tinha onde morar.** `marketing_creatives.equipe` é a
   equipe DONA da linha de produção. Destino do briefing é outra pergunta, e
   pendurar as duas no mesmo campo faz uma responder pela outra.

## A regra invertida do NULL — leia antes de escrever qualquer WHERE

`equipe_destino` NULL significa **as DUAS agências veem**.
`marketing_creatives.equipe` NULL significa **ninguém de fora vê**.

São opostos, de propósito, e convivem no mesmo banco. O nome da coluna é
diferente (`equipe_destino`, não `equipe`) para que um `grep equipe_destino`
ache todo uso da regra invertida e para que aplicar o helper errado não
compile numa query plausível. O filtro de fora vive num lugar só:
`portal_criativos._enderecado_a`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.marketing_personagem import MarketingPersonagem


class MarketingRoteiro(Base, TimestampMixin):
    __tablename__ = "marketing_roteiros"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    titulo: Mapped[str] = mapped_column(String(160), nullable=False)
    # NULL/vazio enquanto está sendo escrito. O portal só lista roteiro COM
    # texto (`length(trim(texto)) > 0`), então uma linha recém-criada não
    # aparece pra agência antes de alguém escrever nela — é o que permite o
    # roteiro nascer visível sem precisar de um passo de "publicar".
    texto: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Texto livre + vínculo resolvido no salvamento, igual ao criativo. Casar
    # string na hora de usar é o que faz o vínculo trocar sozinho quando
    # alguém renomeia um anúncio.
    marca: Mapped[str | None] = mapped_column(String(64), nullable=True)
    marca_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marcas.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    sku: Mapped[str | None] = mapped_column(String(512), nullable=True)
    product_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # NULL = as DUAS agências. Releia o docstring do módulo antes de usar.
    equipe_destino: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # De onde esta versão saiu. A agência não reescreve o roteiro da casa: ela
    # cria a versão dela e as duas ficam lado a lado. Sem o par, some a forma de
    # saber se a ideia de partida prestava — que é o mesmo motivo de existir o
    # `aprovado` no criativo.
    origem_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketing_roteiros.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Único interruptor de visibilidade. Desligar tira da lista da agência E
    # das rotas de bytes — a imagem de referência para de ser servida junto,
    # senão despublicar não despublicaria nada pra quem já anotou o id.
    ativo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    refs: Mapped[list[MarketingRoteiroRef]] = relationship(
        back_populates="roteiro",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="MarketingRoteiroRef.created_at",
    )
    personagens: Mapped[list[MarketingRoteiroPersonagem]] = relationship(
        back_populates="roteiro",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="MarketingRoteiroPersonagem.created_at",
    )


class MarketingRoteiroRef(Base, TimestampMixin):
    """Referência do briefing: uma imagem anexada OU um link de produto.

    `tipo` decide quais colunas valem: 'imagem' usa file_*, 'link' usa `url`.
    O `url` só entra depois de `anexos.url_de_produto` (http/https) — ele é
    renderizado como href no portal PHP, e `javascript:` gravado aqui seria
    XSS armazenado do lado de fora.

    Pertence ao ROTEIRO e não ao criativo: é material de instrução, desce da
    equipe interna pra agência. O arquivo do criativo sobe no sentido
    contrário, vai pro MEGA na aprovação e é o que o robô publica — misturar
    os dois mandaria print de referência pra pasta do produto.
    """

    __tablename__ = "marketing_roteiro_refs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    roteiro_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketing_roteiros.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # "imagem" | "link"
    tipo: Mapped[str] = mapped_column(String(16), nullable=False)
    titulo: Mapped[str | None] = mapped_column(String(200), nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    file_mime: Mapped[str | None] = mapped_column(String(128), nullable=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Relativo a settings.uploads_dir: "roteiro_refs/<roteiro_id>/<nome>".
    file_rel: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    roteiro: Mapped[MarketingRoteiro] = relationship(back_populates="refs")


class MarketingRoteiroPersonagem(Base, TimestampMixin):
    """"Neste vídeo use a Lívia" — o vínculo N:N roteiro × personagem.

    Não é enfeite: os dois roteiros que existiam em produção em 21/09/2026 já
    citavam personagem à mão, um por id de gerador
    (`<<<48dbb6ed-…>>> (Lívia), estudante brasileira de 22 anos`) e outro por
    apelido (`@Lívia`). Com o vínculo, o id sai do cadastro e entra no texto
    por um clique, e a agência vê a foto junto do briefing em vez de receber
    um UUID solto no meio do prompt.
    """

    __tablename__ = "marketing_roteiro_personagens"
    __table_args__ = (
        UniqueConstraint("roteiro_id", "personagem_id", name="uq_roteiro_personagem"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    roteiro_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketing_roteiros.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    personagem_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketing_personagens.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    roteiro: Mapped[MarketingRoteiro] = relationship(back_populates="personagens")
    personagem: Mapped[MarketingPersonagem] = relationship(lazy="selectin")
