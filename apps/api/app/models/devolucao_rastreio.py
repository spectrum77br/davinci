from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class DevolucaoRastreio(Base, TimestampMixin):
    """Rastreio do pacote de UM pedido em devolução (aba Acompanhamento).

    Grão de PEDIDO (`bling_orders.numero` — o espelho bling_orders tem uma
    linha por ITEM, por isso sem FK). Preenchimento manual pelo operador:
    código de rastreio e última localização vista no site da transportadora.
    `localizacao_data` é carimbada automaticamente quando a localização muda —
    é a "data da última movimentação" da folha do Eduardo (2026-09-02), usada
    depois pra detectar pacote parado há N dias.
    """

    __tablename__ = "devolucao_rastreio"

    pedido_bling: Mapped[str] = mapped_column(Text, primary_key=True)
    rastreio: Mapped[str | None] = mapped_column(Text, nullable=True)
    localizacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    localizacao_data: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # --- Automático (migration 0241, Eduardo 03/09): o pacote que VOLTA ---
    # Preenchido pelo job services/devolucao_rastreio_sync a partir da returns
    # API de TikTok/Shopee/ML (contrato: services/devolucao_returns.ReturnInfo).
    # O manual acima continua mandando na aba; estes só entram quando o manual
    # está vazio. `localizacao_auto` vem do 17track (códigos Correios).
    rastreio_auto: Mapped[str | None] = mapped_column(Text, nullable=True)
    transportadora_auto: Mapped[str | None] = mapped_column(Text, nullable=True)
    localizacao_auto: Mapped[str | None] = mapped_column(Text, nullable=True)
    localizacao_auto_data: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    devolucao_status_auto: Mapped[str | None] = mapped_column(Text, nullable=True)
    devolucao_id_auto: Mapped[str | None] = mapped_column(Text, nullable=True)
    fonte_auto: Mapped[str | None] = mapped_column(Text, nullable=True)
    # TIPO do caso (migration 0285): TikTok `return_type` — REFUND = só
    # reembolso, o cliente fica com o produto e nenhum pacote volta. None
    # quando a plataforma não separa.
    devolucao_tipo_auto: Mapped[str | None] = mapped_column(Text, nullable=True)
    # PRAZO DE RESPOSTA DA LOJA (migration 0286, Vinicius 16/09: "colocar no
    # painel o prazo pra não perder mais prazo"): o que o marketplace espera
    # da loja neste caso (TikTok `seller_next_action_response.action`) e até
    # quando. Passado o prazo a plataforma decide sozinha. Reescrito a cada
    # rodada do sync — None quando não há mais nada pendente da loja.
    acao_auto: Mapped[str | None] = mapped_column(Text, nullable=True)
    prazo_acao_auto: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Aviso Threema do prazo: quando saiu e PRA QUAL prazo (um aviso por caso
    # e por prazo — se a plataforma abrir outra ação com prazo novo, avisa de
    # novo).
    aviso_prazo_acao_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    aviso_prazo_acao_para: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # REEMBOLSO (migration 0289, Vinicius 17/09: "o importante é saber se
    # estamos com o dinheiro ainda ou se já devolveu para o cliente"). True =
    # já saiu dinheiro do NOSSO (a plataforma devolveu ao cliente e desconta da
    # loja); False = nada saiu (caso vivo/cancelado, ou a plataforma pagou do
    # próprio bolso — ML cobertura/BPP, Shopee compensação); NULL = não se sabe.
    # Fontes: o caso no marketplace (returns_por_pedido) cruzado com o extrato
    # financeiro já baixado (marketplace_order_financials). `_detalhe` é o
    # texto do balão (de onde veio a resposta).
    reembolso_auto: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    reembolso_valor_auto: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    reembolso_em_auto: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reembolso_detalhe_auto: Mapped[str | None] = mapped_column(Text, nullable=True)
    # PAINEL (migration 0290, Vinicius 17/09): a aba Devoluções se divide em
    # "Acompanhamento" (volta pacote — uma pessoa cuida) e "Fraude" (só
    # dinheiro: "chegou vazio", reembolso sem devolução, mediação — outra
    # pessoa cuida). A regra automática é o tipo do caso (`devolucao_tipo_auto`
    # REFUND = fraude); quem mover na mão fixa aqui, e a regra não mexe mais.
    # 'acompanhamento' | 'fraude' | NULL (automático).
    fila_manual: Mapped[str | None] = mapped_column(Text, nullable=True)
    fila_manual_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Quando a devolução foi ABERTA no marketplace → "Em devolução desde" real
    # (o backfill da 0236 carimbou 02/09 em todo mundo).
    devolucao_criada_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    devolucao_atualizada_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Perna REVERSA entregue: dia/hora em que o marketplace confirmou que o
    # pacote de volta chegou ao vendedor (Shopee: reverse_logistics_status =
    # LOGISTICS_DELIVERY_DONE, só no detalhe da devolução).
    pacote_entregue_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    auto_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # "Em devolução desde" digitado na mão (migration 0242) — vale mais que o
    # automático (devolução aberta no marketplace / carimbo da Logística /
    # entrada em 83957). Caso 287144: entrou em 19/08 pela Viena no Bling.
    entrada_manual: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Observação livre da aba Acompanhamento (migration 0255, 10/09): recado
    # pra quem acompanha o pacote — por PEDIDO, independente de a devolução já
    # estar lançada (a Observação da aba Lançamentos só existe após o
    # lançamento). Editada inline, salva ao sair do campo.
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
