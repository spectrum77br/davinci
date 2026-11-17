"""O bloco "Reembolso ao cliente" da aba Pedido da conversa (Novos procedimentos 3/6, 17/11/2026).

O contrato combinado com a tela (`components/AtendimentoPedido.vue`):
GET/POST /api/atendimento/conversas/{id}/reembolsos e
POST /api/atendimento/reembolsos/{refund_id}/aprovar
(`routers/atendimento_reembolso.py`). Os campos a mais (`plataforma_detalhe`,
`conta`, `origem`) só acrescentam: a tela pode ignorá-los.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

SituacaoReembolso = Literal["aguardando_adm", "em_aberto", "finalizado"]
MotivoReembolso = Literal["defeito_avaria", "produto_errado", "faltou_peca", "frete", "outro"]
FormaReembolso = Literal["pix", "cupom"]


class PedidoDoReembolsoOut(BaseModel):
    # nº do Bling (None = o pedido ainda não chegou ao Bling) e o da plataforma.
    bling: str | None = None
    marketplace: str | None = None
    plataforma: str | None = None
    # A conta no formato da tela Reembolso ("Shopee ATV").
    conta: str | None = None
    # O "Valor pago pelo comprador" da aba (sem retrato: o total do Bling).
    valor_pago: float | None = None


class LimiteDoReembolsoOut(BaseModel):
    # Acima disto o ADM aprova: R$ (valor) ou % do valor pago (pct).
    valor: int
    pct: int


class ReembolsoItemOut(BaseModel):
    id: UUID
    tipo: str | None = None
    # Positivo, para mostrar (no banco o Cliente fica negativo).
    valor: float
    motivo: str | None = None
    motivo_detalhe: str | None = None
    forma: str | None = None
    situacao: SituacaoReembolso
    # Nomes, não ids.
    criado_por: str | None = None
    criado_em: datetime
    aprovado_por: str | None = None
    aprovado_em: datetime | None = None
    # Os dados do Pix: só para ADM; para os outros, sempre None.
    dados_pagamento: str | None = None
    conta: str | None = None
    origem: str | None = None


class ReembolsosDaConversaOut(BaseModel):
    pedido: PedidoDoReembolsoOut | None = None
    plataforma_reembolsou: bool = False
    # O porquê, para o aviso ("Shopee: Reembolso pago ao comprador").
    plataforma_detalhe: str | None = None
    limite: LimiteDoReembolsoOut
    pode_lancar: bool = False
    motivo_bloqueio: str | None = None
    pode_aprovar: bool = False
    itens: list[ReembolsoItemOut] = Field(default_factory=list)


class LancarReembolsoIn(BaseModel):
    # A conta, o pedido, a plataforma e a data NÃO vêm da tela: saem da conversa.
    valor: float = Field(gt=0, le=1_000_000)
    motivo: MotivoReembolso
    motivo_detalhe: str | None = None
    forma: FormaReembolso
    # Chave, banco e nome de quem recebe; obrigatório no Pix, ignorado no cupom.
    dados_pagamento: str | None = None
    # 17/11/2026: o nº do Bling que o diálogo mostrou (opcional). Só confere: se
    # a conversa já está em outro pedido, 409 `pedido_mudou` e nada é gravado.
    pedido_bling: str | None = None


class ReembolsoItemRespostaOut(BaseModel):
    item: ReembolsoItemOut
