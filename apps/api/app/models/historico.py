"""Histórico de alterações (Sistema › Histórico). Ver app/historico/__init__.py.

Sem chave estrangeira com CASCADE para o que foi mudado: apagar uma empresa
ou um chamado não pode apagar o registro de quem apagou.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class HistoricoEvento(Base):
    """Um pedido de uma pessoa: quem, quando, em qual tela, o que pediu."""

    __tablename__ = "historico_evento"
    __table_args__ = (
        Index("ix_historico_evento_criado_em", text("criado_em DESC")),
        Index("ix_historico_evento_ator", "ator_id", text("criado_em DESC")),
        Index("ix_historico_evento_tela", "tela", text("criado_em DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    req_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, unique=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    # SET NULL + nome guardado: excluir a pessoa não apaga o que ela fez.
    ator_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    ator_nome: Mapped[str | None] = mapped_column(Text, nullable=True)
    via: Mapped[str] = mapped_column(Text, nullable=False, server_default="tela")
    metodo: Mapped[str] = mapped_column(Text, nullable=False)
    rota: Mapped[str] = mapped_column(Text, nullable=False)
    caminho: Mapped[str] = mapped_column(Text, nullable=False)
    tela: Mapped[str | None] = mapped_column(Text, nullable=True)
    pagina: Mapped[str | None] = mapped_column(Text, nullable=True)
    acao: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[int] = mapped_column(Integer, nullable=False)
    ip: Mapped[str | None] = mapped_column(Text, nullable=True)
    corpo: Mapped[dict | list | None] = mapped_column(JSONB, nullable=True)
    n_alteracoes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Nomes dos itens mudados, congelados na hora (busca por SKU, empresa…).
    itens_texto: Mapped[str | None] = mapped_column(Text, nullable=True)


class HistoricoAlteracao(Base):
    """Uma linha mudada no banco, gravada pelo gatilho `historico_captura`."""

    __tablename__ = "historico_alteracao"
    __table_args__ = (
        Index("ix_historico_alteracao_req", "req_id"),
        Index("ix_historico_alteracao_registro", "tabela", "registro_id"),
        Index("ix_historico_alteracao_criado_em", "criado_em"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    req_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    ator_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    tabela: Mapped[str] = mapped_column(Text, nullable=False)
    # I = criou, U = alterou, D = excluiu, X = "mais alterações" (teto)
    operacao: Mapped[str] = mapped_column(Text, nullable=False)
    registro_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    rotulo: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Nome do item como a tela mostra ("dg053 · ML kia"), resolvido e
    # congelado quando o evento é gravado (sobrevive a renomear/apagar).
    item: Mapped[str | None] = mapped_column(Text, nullable=True)
    antes: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    depois: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ident: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    app: Mapped[str | None] = mapped_column(Text, nullable=True)


class HistoricoAcesso(Base):
    """Quem pode ver o Histórico. Fora desta tabela, a tela e a API respondem
    "não encontrada" — inclusive para admin. `pode_gerenciar` = pode liberar
    outras pessoas (só o Eduardo)."""

    __tablename__ = "historico_acesso"

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    pode_gerenciar: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    liberado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    liberado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
