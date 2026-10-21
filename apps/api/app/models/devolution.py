from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, SmallInteger, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Devolution(Base, TimestampMixin):
    __tablename__ = "devolutions"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    data: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pedido_bling: Mapped[str | None] = mapped_column(Text, nullable=True)
    pedido_marketplace: Mapped[str | None] = mapped_column(Text, nullable=True)
    conta: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    sku: Mapped[str | None] = mapped_column(Text, nullable=True)
    produtos: Mapped[str | None] = mapped_column(Text, nullable=True)
    custo_produto: Mapped[float | None] = mapped_column(Float, nullable=True)
    condicao_produto: Mapped[str | None] = mapped_column(Text, nullable=True)
    link_abertura: Mapped[str | None] = mapped_column(Text, nullable=True)
    reembolso: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    motivo_devolucao: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Link do vídeo da devolução (a API do ML não aceita vídeo como anexo, então
    # o link vai no texto do chamado automático). Ver alembic 0244.
    # Link da prova da expedição (fotos/vídeo do pacote sendo fechado) — obrigatório
    # pra mala/eletro quando o motivo abre chamado (Eduardo 04/09). Ver alembic 0245.
    link_envio: Mapped[str | None] = mapped_column(Text, nullable=True)
    custo_manutencao: Mapped[float | None] = mapped_column(Float, nullable=True)
    tecnico: Mapped[str | None] = mapped_column(Text, nullable=True)
    devolver_estoque: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # True quando a devolução (condição Manutenção) já PASSOU em manutenção —
    # marcado automaticamente ao devolver ao estoque (inclui Sucata). É um fato
    # histórico: não volta a false. Ver alembic 0129.
    manutencao: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Escolhas dos modais de devolução (ver alembic 0104). Persistidas para
    # auditoria e para o PATCH re-rodar o estoque de forma determinística.
    troca_sku: Mapped[str | None] = mapped_column(Text, nullable=True)
    troca_condicao: Mapped[str | None] = mapped_column(Text, nullable=True)
    estoque_suffix: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Unidades a lançar no estoque Bling (ver alembic 0110). Hoje a busca
    # expande 1 linha por unidade, então fica 1; guardado p/ a entrada e o
    # estoque inicial do produto z criado.
    quantidade: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    # Destino do estoque escolhido no modal (ver alembic 0110):
    #   estoque_destino_sku — bin já existente `base.<sufixo>` (entrada direta);
    #   estoque_nova_tag     — tag pra criar produto novo `z000N.<tag>` quando
    #                          nenhuma variante existe.
    estoque_destino_sku: Mapped[str | None] = mapped_column(Text, nullable=True)
    estoque_nova_tag: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "Novo"/"Usado"/"Sucata" escolhido no modal quando condição == Manutenção.
    manutencao_destino: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Tags de sufixo regional dos SKUs do pedido (`.sp`, `.ra`, …); ver 0105.
    tag: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Setado quando o toggle "devolver estoque" passa a TRUE (auto no router).
    data_devolvido_estoque: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Prazo = created_at + 30 dias. Só para Manutenção (NULL nas demais).
    # Preenchido no create/patch quando a condição é Manutenção. Ver alembic 0130.
    prazo: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Prazo da PLATAFORMA pra contestar a devolução recebida (Shopee
    # `return_seller_due_date`; TikTok `seller_next_action_response.deadline`).
    # Eduardo 09/09: o 291835 perdeu a contestação porque o motivo foi
    # preenchido 3 dias depois de o pacote voltar. `_at` = quando consultou;
    # `aviso_prazo_at` = quando o Threema avisou (uma vez só).
    prazo_contestacao: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    prazo_contestacao_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    aviso_prazo_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Movimento de estoque efetivamente lançado no Bling (ver alembic 0115).
    # Guardado pra poder ESTORNAR (dar baixa "S") quando o toggle "devolver
    # estoque" é desligado depois — ex.: Usado que vira Sucata. `*_action`
    # distingue bin existente de produto z criado; `*_revertido_at` marca o
    # estorno já feito (evita estornar duas vezes).
    estoque_mov_sku: Mapped[str | None] = mapped_column(Text, nullable=True)
    estoque_mov_bling_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    estoque_mov_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    estoque_mov_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estoque_mov_revertido_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Resultado da entrada no Bling (alembic 0421, caso 300588): ok | falhou |
    # conferindo | sem_confirmacao. NULL = não se aplica ou linha antiga (com
    # estoque_mov_* conta como ok — ver services/devolution_stock_return
    # .status_estoque). `data_devolvido_estoque` é a hora do PEDIDO; quem diz
    # se entrou é este campo.
    estoque_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    estoque_erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    estoque_tentativa_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Entrada que o Bling respondeu com erro DEPOIS de receber (5xx/timeout):
    # o worker procura o movimento no espelho e confirma (ou desiste em 10 min).
    estoque_conferir_bling_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    estoque_conferir_sku: Mapped[str | None] = mapped_column(Text, nullable=True)
    estoque_conferir_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    # stock_movements.id usado pela conferência (um movimento confirma uma linha só).
    estoque_mov_ref: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    # Tentativas automáticas do worker depois de o Bling recusar por excesso
    # de chamadas (21/10, caso 297390) — até 5, a cada ~2 min.
    estoque_auto_tentativas: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default=text("0")
    )
