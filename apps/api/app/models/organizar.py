"""ORGANIZAR da tela Produtos: o que está escondido da TELA (migration 0499).

Eduardo, 17/11/2026 (decisões 3 e 4): o "Remover os N mortos" sai da tela e
fica só o ORGANIZAR — só ADM, só ESCONDE da tela Produtos/Anúncios, dá para
desfazer. Nada é apagado, nada muda no Bling nem nos marketplaces, e nenhum
robô lê estas tabelas (o filtro mora em `services/organizar.py` e só as
leituras da tela o usam).

  • `organizar_ocultos` — uma marca por item escondido. `registro_id` aponta
    para products, product_links ou listings (por isso sem FK: marca de linha
    apagada por fora é inofensiva, a leitura junta pela linha viva). `motivo`
    é a própria CHAVE do Organizar (7, CHECK). A marca só vale enquanto o
    motivo continua verdadeiro, e isso é conferido NA LEITURA. "Mostrar de
    novo" só preenche `reexibido_em/por`: a tabela é a trilha (quem ocultou,
    quem mostrou de novo) e fica FORA do Histórico (`historico/sql.EXCLUIDAS`),
    senão um "Ocultar 7.208 itens" virava 7.208 linhas lá.
  • `organizar_previas` — os ids que a prévia MOSTROU, guardados no servidor
    por 30 minutos: o "Ocultar" grava só esses (os que ainda valem), nunca um
    item que a pessoa não viu.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

TABELAS_ORGANIZAR = ("products", "product_links", "listings")
# As 7 chaves do Organizar (services/organizar.MOTIVOS). Gravada em `motivo`.
CHAVES_ORGANIZAR = (
    "vinculo_morto",
    "produto_excluido_sem_anuncio",
    "produto_excluido_com_anuncio",
    "produto_inativo",
    "anuncio_loja_arquivada",
    "anuncio_encerrado",
    "anuncio_produto_excluido",
)


def _in(coluna: str, valores: tuple[str, ...]) -> str:
    return f"{coluna} IN ({', '.join(repr(v) for v in valores)})"


class OrganizarOculto(Base):
    __tablename__ = "organizar_ocultos"
    __table_args__ = (
        CheckConstraint(_in("tabela", TABELAS_ORGANIZAR), name="tabela"),
        CheckConstraint(_in("motivo", CHAVES_ORGANIZAR), name="motivo"),
        # Uma marca ATIVA por item: ocultar duas vezes não duplica (ON
        # CONFLICT ... WHERE reexibido_em IS NULL DO NOTHING no aplicar).
        Index(
            "uq_organizar_ocultos_ativo",
            "tabela",
            "registro_id",
            unique=True,
            postgresql_where=text("reexibido_em IS NULL"),
        ),
        Index("ix_organizar_ocultos_lote", "lote_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    tabela: Mapped[str] = mapped_column(String(20), nullable=False)
    registro_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    motivo: Mapped[str] = mapped_column(String(40), nullable=False)
    # Um clique em "Ocultar" = um lote ("Desfazer este lote").
    lote_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    oculto_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    oculto_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    reexibido_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reexibido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OrganizarPrevia(Base):
    """Os ids que a prévia mostrou, por chave ({chave: [uuid, ...]}). Vence em
    30 minutos; as vencidas são apagadas a cada prévia nova."""

    __tablename__ = "organizar_previas"

    token: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    criado_por: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ids: Mapped[dict] = mapped_column(JSONB, nullable=False)
