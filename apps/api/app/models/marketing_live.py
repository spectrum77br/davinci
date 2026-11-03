"""Gravação das lives da Shopee e do TikTok (Eduardo, 03/11/2026).

"gravar as lives que pessoas reais vão fazer, para medir o padrão de uma boa
live" — e, mais tarde, virar persona de IA. Por enquanto só gente de verdade
apresenta: alguém cadastra a live em Marketing › Lives com o link de
compartilhar, e o robô do Mac mini (~/Desktop/lives-gravador) pega as que estão
aguardando, grava o stream inteiro com ffmpeg e conta como foi.

O vídeo fica NO MAC (o caminho vai em `arquivo`): uma live de 2 h passa de
1 GB, e o servidor não precisa dele pra nada por enquanto. `metricas` é o
resumo que o robô junta durante a live (espectadores, curtidas, comentários,
produtos fixados) e, depois, o que sair da transcrição.

Ciclo: aguardando → gravando → gravada | erro. Só o robô muda o status; a tela
só cria, edita os campos de cadastro e apaga o que ainda não começou.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from app.models.base import Base, TimestampMixin

STATUS_AGUARDANDO = "aguardando"
STATUS_GRAVANDO = "gravando"
STATUS_GRAVADA = "gravada"
STATUS_ERRO = "erro"
STATUS_LIVE = (STATUS_AGUARDANDO, STATUS_GRAVANDO, STATUS_GRAVADA, STATUS_ERRO)

PLATAFORMAS_LIVE = ("shopee", "tiktok")


class MarketingLive(Base, TimestampMixin):
    __tablename__ = "marketing_lives"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    plataforma: Mapped[str] = mapped_column(String(16), nullable=False)
    link: Mapped[str] = mapped_column(Text, nullable=False)
    apresentador: Mapped[str | None] = mapped_column(Text, nullable=True)
    integration_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("integrations.id", ondelete="SET NULL"),
        nullable=True,
    )
    loja: Mapped[str | None] = mapped_column(Text, nullable=True)
    produtos: Mapped[str | None] = mapped_column(Text, nullable=True)
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=STATUS_AGUARDANDO, index=True
    )
    # O que a plataforma diz da live (título, nome do canal, id da sala).
    titulo: Mapped[str | None] = mapped_column(Text, nullable=True)
    canal: Mapped[str | None] = mapped_column(Text, nullable=True)
    sala_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    inicio_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fim_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duracao_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    arquivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    tamanho_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    metricas: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Último sinal do robô sobre esta live (a tela mostra "parado há X min").
    robo_visto_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
