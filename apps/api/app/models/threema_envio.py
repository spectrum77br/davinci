"""Envios do Threema (Cadastros › Envios Threema, tarefa 16).

`threema_envio_registro` (migration 0504): uma linha por destinatário e por
tentativa de envio, gravada por `ThreemaClient.send_to_all` (services/
threema.py) através de `services/threema_registro.py`. Diz quando saiu, de
qual envio (`envio_chave`, a chave do catálogo `services/threema_catalogo.K`),
por qual remetente, para quem e se chegou. Uma chamada de envio é um LOTE
(`lote_id`): a tela do Histórico agrupa por ele.

  • `envio_chave` sem FK: o catálogo mora no código (e a linha do robô do Mac
    pode trazer chave desconhecida). NULL = ponto de envio sem marca — em
    produção quer dizer "envio que o levantamento não pegou".
  • Nunca guarda o segredo nem o texto inteiro: só a 1ª linha (`titulo`) e o
    tamanho (`bytes`).
  • Fica FORA do Histórico de alterações (`historico/sql.EXCLUIDAS`): é
    escrito pela máquina e já é, ele mesmo, a trilha. A limpeza diária
    (`worker.alerts_cleanup`) apaga o que passou de 180 dias.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

ORIGENS_REGISTRO = ("automatico", "manual", "teste")
VIAS_REGISTRO = ("cadastro", "legado", "cache", "forcado", "env")
ONDE_RODA_REGISTRO = ("servidor", "mac")


def _in(coluna: str, valores: tuple[str, ...]) -> str:
    return f"{coluna} IN ({', '.join(repr(v) for v in valores)})"


class ThreemaEnvioRegistro(Base):
    __tablename__ = "threema_envio_registro"
    __table_args__ = (
        CheckConstraint(_in("origem", ORIGENS_REGISTRO), name="origem"),
        CheckConstraint(f"via IS NULL OR {_in('via', VIAS_REGISTRO)}", name="via"),
        CheckConstraint(_in("onde_roda", ONDE_RODA_REGISTRO), name="onde_roda"),
        Index("ix_threema_envio_registro_chave_criado", "envio_chave", text("criado_em DESC")),
        Index("ix_threema_envio_registro_criado", text("criado_em DESC")),
        Index(
            "ix_threema_envio_registro_falhas",
            text("criado_em DESC"),
            postgresql_where=text("NOT ok"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Uma chamada de envio (`send_to_all`) = um lote.
    lote_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    envio_chave: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # Item 1: o id da regra de status da Logística.
    escopo: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # O `contexto` pedido ao ThreemaClient e o canal efetivo de credenciais.
    contexto: Mapped[str | None] = mapped_column(String(20), nullable=True)
    canal: Mapped[str | None] = mapped_column(String(20), nullable=True)
    onde_roda: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=text("'servidor'")
    )
    origem: Mapped[str] = mapped_column(String(12), nullable=False)
    # De onde veio a lista de quem recebe (fases seguintes). NULL = antes do
    # Cadastro (cada ponto monta a sua lista como sempre montou).
    via: Mapped[str | None] = mapped_column(String(10), nullable=True)
    ator_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    ator_nome: Mapped[str | None] = mapped_column(String(120), nullable=True)
    req_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    # NULL quando o envio nem chegou a tentar ninguém (erro de configuração).
    destinatario_id: Mapped[str | None] = mapped_column(String(8), nullable=True)
    destinatario_nome: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Sem FK: a pessoa pode sair do cadastro e o registro continua.
    user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    contato_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    http_status: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    erro: Mapped[str | None] = mapped_column(String(200), nullable=True)
    message_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Só a 1ª linha da mensagem — nunca o texto inteiro.
    titulo: Mapped[str | None] = mapped_column(String(160), nullable=True)
    bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    host: Mapped[str | None] = mapped_column(String(60), nullable=True)
