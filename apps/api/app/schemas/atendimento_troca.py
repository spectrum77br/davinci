"""Lista "Ag. cancelamento" e a TROCA DE PRODUTO da caixa /atendimento (item 4).

Fase 4b (05/10/2026): os pedidos em "Aguardando Cancelamento" (83955) no
Bling, COM ou SEM conversa, com o porquê (o mesmo bloco do painel,
`AgCancelamentoOut`) e as sugestões de troca (`SugestoesTrocaOut`). Só
leitura: nada vai ao Bling.

Fase 4c (07/10/2026): a prévia, o clique, o retomar e a lista das trocas
(`services/atendimento/troca.py`).

Fase 4d (07/10/2026): a oferta ao comprador pelo chat da plataforma
(`services/atendimento/troca_oferta.py`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.atendimento_painel import (
    AgCancelamentoOut,
    SugestoesTrocaOut,
    TrocaAbertaOut,
)


class ItemPedidoAgOut(BaseModel):
    """Um item do pedido no espelho do Bling."""

    sku: str
    descricao: str | None = None
    quantidade: int | None = None


class PedidoAgCancelamentoOut(BaseModel):
    """Um pedido em 83955: o porquê, os itens, o prazo e as sugestões."""

    numero: str
    numeroloja: str | None = None
    bling_id: int | None = None
    # `bling_orders.loja` e, pelo cadastro de Lojas, a plataforma e a conta.
    loja: str | None = None
    plataforma: str | None = None
    conta: str | None = None
    data: datetime | None = None
    # `bling_orders.marketplace_ship_deadline`: o prazo de envio na plataforma.
    prazo_envio: datetime | None = None
    motivo: AgCancelamentoOut
    itens: list[ItemPedidoAgOut] = Field(default_factory=list)
    # A conversa principal do pedido (None = o pedido não tem conversa).
    conversa_id: str | None = None
    # Só na falta de estoque com `atendimento_troca_sugestoes_ativa`.
    sugestoes_troca: SugestoesTrocaOut | None = None


class ListaAgCancelamentoOut(BaseModel):
    """GET /api/atendimento/ag-cancelamento."""

    pedidos: list[PedidoAgCancelamentoOut] = Field(default_factory=list)
    # Pedidos na lista (depois do filtro `codigo`).
    total: int = 0
    # Quantos pedidos por motivo (antes do filtro) — os contadores da tela.
    por_codigo: dict[str, int] = Field(default_factory=dict)
    desde: datetime
    # A chave `atendimento_troca_sugestoes_ativa` (desligada = sem sugestões).
    sugestoes_ativas: bool = False
    # Quem pediu vê a Margem (as sugestões trazem o % do custo).
    ve_custo: bool = False
    gerado_em: datetime


# ── A troca de produto (fase 4c) ──────────────────────────────────────────


class PreviaTrocaIn(BaseModel):
    """POST /pedidos/{numero_bling}/troca/previa."""

    sku_antigo: str = Field(min_length=1, max_length=191)
    sku_novo: str = Field(min_length=1, max_length=191)
    # A conversa do pedido (a nota e as provas do aceite saem dela); sem ela,
    # a principal do pedido.
    conversa_id: UUID | None = None


class AceiteIn(BaseModel):
    """A PROVA do aceite (opcional): a mensagem do cliente OU a resposta do Duoke.

    Sem nenhuma, vale `declarado` (o nome de quem clicou). A caixinha é o
    `confirmar` do `TrocaIn`.
    """

    # Uma das `aceites_possiveis` da prévia (fonte `davinci`).
    mensagem_aceite_id: UUID | None = None
    # `duoke` = a resposta do cliente colada do Duoke (`texto` + `em`).
    fonte: Literal["davinci", "duoke"] | None = None
    texto: str | None = Field(default=None, max_length=2000)
    # Data e hora da resposta no Duoke (sem fuso = horário de Brasília).
    em: datetime | None = None


class TrocaIn(PreviaTrocaIn):
    """POST /pedidos/{numero_bling}/troca — o clique "Trocar"."""

    aceite: AceiteIn | None = None
    # O `previa_hash` da prévia: o pedido mudou desde então = 409 `previa_mudou`.
    previa_hash: str = Field(min_length=8, max_length=64)
    # Uma por abertura do diálogo: repetida, devolve a troca que já existe.
    idem_key: UUID
    # A caixinha "O cliente aceitou a troca" — obrigatória nos níveis 1 e 2.
    confirmar: bool = False


class TravaTrocaOut(BaseModel):
    code: str
    ok: bool
    texto: str


class ProdutoTrocaOut(BaseModel):
    sku: str
    nome: str | None = None
    quantidade: int | None = None
    produto_id: int | None = None
    # `products.bling_cost_price`: só para quem vê a Margem.
    custo: float | None = None
    # Só no depois: o saldo virtual do Bling lido agora.
    saldo_ao_vivo: float | None = None


class AceitePossivelOut(BaseModel):
    """Uma mensagem do cliente que vale como prova do aceite."""

    id: str
    texto: str
    enviada_em: datetime | None = None
    # Veio depois da 1ª fala da loja após a falta de estoque (a oferta): só a
    # dica da tela para achar a resposta — a mensagem vale de um jeito ou de outro.
    depois_da_oferta: bool = False


class PreviaTrocaOut(BaseModel):
    """A prévia: pode trocar? Cada trava, o antes e o depois, e o hash que o clique confere."""

    pode: bool
    travas: list[TravaTrocaOut] = Field(default_factory=list)
    # A conferência ao vivo rodou (só roda com as travas do banco em ordem).
    ao_vivo: bool = False
    numero_bling: str
    bling_id: int | None = None
    numeroloja: str | None = None
    plataforma: str | None = None
    conversa_id: str | None = None
    # 0 = o mesmo produto em outro lote (sem aceite); 1 e 2 pedem o aceite.
    nivel: int | None = None
    mesmo_produto: bool = False
    exige_aceite: bool = False
    antes: ProdutoTrocaOut
    depois: ProdutoTrocaOut
    # O valor unitário do item no Bling (o mesmo depois da troca).
    valor_unitario: float | None = None
    # A linha que vai para as Observações do Bling ("dd/mm - TROCA a -> b ...").
    observacao: str
    passos_previstos: list[str] = Field(default_factory=list)
    # Quando o sweep de NF não vai pegar o pedido depois ("enfileire na aba NF").
    aviso_nf: str | None = None
    aviso_prazo: str | None = None
    # Quanto o NOSSO custo muda (%): só para quem vê a Margem.
    dif_custo_pct: float | None = None
    aceites_possiveis: list[AceitePossivelOut] = Field(default_factory=list)
    aceite_max_dias: int
    previa_hash: str | None = None
    troca_aberta: TrocaAbertaOut | None = None
    lido_em: datetime


class PassoTrocaOut(BaseModel):
    passo: str
    em: datetime | None = None
    ok: bool
    detalhe: str | None = None


class TrocaOut(BaseModel):
    """Uma troca — o clique e o retomar devolvem 200 mesmo parada no meio: o `estado` diz onde."""

    id: str
    pedido_bling: str
    bling_id: int
    numeroloja: str | None = None
    plataforma: str | None = None
    conversa_id: str | None = None
    motivo_codigo: str
    sku_antigo: str
    sku_novo: str
    produto_novo_id: int
    descricao_nova: str | None = None
    quantidade: int
    valor_unitario: float | None = None
    nivel: int
    automatica: bool = False
    # Só para quem vê a Margem.
    custo_antigo: float | None = None
    custo_novo: float | None = None
    saldo_ao_vivo: float | None = None
    # davinci | duoke | declarado (None no nível 0).
    aceite_fonte: str | None = None
    mensagem_aceite_id: str | None = None
    aceite_texto: str | None = None
    aceite_em: datetime | None = None
    # iniciada, item_trocado, em_atendido, em_aberto, nf_liberada, concluida,
    # abortada, incerta.
    estado: str
    aberta: bool
    pode_retomar: bool = False
    codigo_erro: str | None = None
    erro: str | None = None
    passos: list[PassoTrocaOut] = Field(default_factory=list)
    criado_por_nome: str
    created_at: datetime | None = None
    concluida_em: datetime | None = None


class ListaTrocasOut(BaseModel):
    """GET /api/atendimento/trocas."""

    itens: list[TrocaOut] = Field(default_factory=list)


# ── A oferta de troca pelo chat (fase 4d) ─────────────────────────────────


class OfertaTrocaIn(BaseModel):
    """POST /pedidos/{numero_bling}/troca/oferta — o botão "Enviar oferta"."""

    sku_novo: str = Field(min_length=1, max_length=191)
    # O item em falta; sem ele, o único em falta do pedido.
    sku_antigo: str | None = Field(default=None, min_length=1, max_length=191)
    # A conversa do pedido; sem ela, a principal (dentro da equipe).
    conversa_id: UUID | None = None
    # O texto editado pela pessoa; vazio = o `texto_oferta` da sugestão (4b).
    # Sem `min_length`: o vazio é o da 4b, e o texto reprovado é do validador.
    texto: str | None = Field(default=None, max_length=10_000)
    # A última fala que a tela tinha (o `oferta_envio.ultima_mensagem_id`):
    # 409 `conversa_mudou` se o CLIENTE ou a loja escreveu depois (sem ela, a
    # referência é a marca da falta de estoque); `confirmar` = "envie mesmo assim".
    ultima_vista_id: UUID | None = None
    confirmar: bool = False


class OfertaTrocaOut(BaseModel):
    """A oferta enviada: 200 também quando a plataforma falhou (o `status` diz)."""

    # Saiu (a plataforma confirmou). `revisar` = pode ter saído: confira.
    enviada: bool
    mensagem_id: UUID
    # O texto que foi (normalizado pelo validador).
    texto: str
    # enviada | revisar | falhou (a mensagem do envio).
    status: str
    erro: str | None = None
    conversa_id: UUID
    sku_antigo: str
    sku_novo: str
    nivel: int
