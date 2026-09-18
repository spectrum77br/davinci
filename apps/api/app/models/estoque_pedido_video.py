"""EstoquePedidoVideo — vídeo da embalagem do pedido (Controle de Estoque).

Vinicius, 18/09/2026: "todo pedido que a quantidade for mais de 1 pede vídeo
(ex.: 2 Apple Watch no mesmo pedido)". Na aba Pedidos, um botão antes de Obs
salva o link do vídeo (sempre Google Drive); a aba Envios mostra por dia
quantos pedidos com mais de 1 unidade já têm o vídeo (Feito / Parcial /
Não feito). O link é a prova pra disputa "chegou vazio / veio só um".

Grão = PEDIDO (bling_orders.numero), igual à previsao_impressa: o espelho
bling_orders tem uma linha por item, o vídeo é da caixa. Sem FK por isso.
Migration 0295.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class EstoquePedidoVideo(Base, TimestampMixin):
    __tablename__ = "estoque_pedido_video"

    pedido_bling: Mapped[str] = mapped_column(Text, primary_key=True)
    link: Mapped[str] = mapped_column(Text, nullable=False)
    # Quem colou o link (SET NULL se o usuário sumir; o vídeo continua valendo).
    salvo_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
