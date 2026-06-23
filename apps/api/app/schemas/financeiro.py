from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel


# ── Consórcio ──────────────────────────────────────────────────────────


class ConsorcioOut(BaseModel):
    id: UUID
    credito: Decimal | None = None
    emp: str | None = None
    grupo: int | None = None
    cota: int | None = None
    alienacao: str | None = None
    nf: str | None = None
    parc_a_pagar: int | None = None
    lance: Decimal | None = None
    valor_parc: Decimal | None = None
    atualizado: date | None = None
    fundo_reserva: str | None = None
    obs: str | None = None
    created_at: datetime
    updated_at: datetime


class ConsorcioPatch(BaseModel):
    credito: Decimal | None = None
    emp: str | None = None
    grupo: int | None = None
    cota: int | None = None
    alienacao: str | None = None
    nf: str | None = None
    parc_a_pagar: int | None = None
    lance: Decimal | None = None
    valor_parc: Decimal | None = None
    atualizado: date | None = None
    fundo_reserva: str | None = None
    obs: str | None = None


# ── Suprimentos ────────────────────────────────────────────────────────


class SuprimentosOut(BaseModel):
    id: UUID
    produto: str | None = None
    modelo: str | None = None
    nome_comercial: str | None = None
    certificado: str | None = None
    numero: str | None = None
    valor: Decimal | None = None
    inicio: date | None = None
    fim: date | None = None
    created_at: datetime
    updated_at: datetime


class SuprimentosPatch(BaseModel):
    produto: str | None = None
    modelo: str | None = None
    nome_comercial: str | None = None
    certificado: str | None = None
    numero: str | None = None
    valor: Decimal | None = None
    inicio: date | None = None
    fim: date | None = None


# ── Simulação ──────────────────────────────────────────────────────────


class SimulacaoOut(BaseModel):
    id: UUID
    numero_cotacao: str | None = None
    cliente: str | None = None
    data: date | None = None
    processo: str | None = None
    exportador: str | None = None
    pais_origem: str | None = None
    descricao: str | None = None
    fornecedor: str | None = None
    quantidade: int | None = None
    ncm: str | None = None
    descricao_ncm: str | None = None
    invoice_numero: str | None = None
    porto_origem: str | None = None
    porto_destino: str | None = None
    etd: date | None = None
    eta: date | None = None
    taxa_cambio: Decimal | None = None
    frete_seguro_usd: Decimal | None = None
    valor_unitario_usd: Decimal | None = None
    aliquota_ii: Decimal | None = None
    aliquota_ipi: Decimal | None = None
    aliquota_pis: Decimal | None = None
    aliquota_cofins: Decimal | None = None
    taxa_siscomex_usd: Decimal | None = None
    armazenagem_usd: Decimal | None = None
    despachante_sda_usd: Decimal | None = None
    despachante_honorarios_usd: Decimal | None = None
    corretagem_cambio_usd: Decimal | None = None
    inspecao_usd: Decimal | None = None
    outras_taxas_usd: Decimal | None = None
    aliquota_taxas_gerais: Decimal | None = None
    aliquota_impostos_fed: Decimal | None = None
    aliquota_icms: Decimal | None = None
    frete_nacional_usd: Decimal | None = None
    aliquota_intermediacao: Decimal | None = None
    created_at: datetime
    updated_at: datetime


# Patch reusa todos os campos opcionais (model_dump(exclude_unset=True)
# permite editar 1 célula por vez sem mexer no resto).
class SimulacaoPatch(BaseModel):
    numero_cotacao: str | None = None
    cliente: str | None = None
    data: date | None = None
    processo: str | None = None
    exportador: str | None = None
    pais_origem: str | None = None
    descricao: str | None = None
    fornecedor: str | None = None
    quantidade: int | None = None
    ncm: str | None = None
    descricao_ncm: str | None = None
    invoice_numero: str | None = None
    porto_origem: str | None = None
    porto_destino: str | None = None
    etd: date | None = None
    eta: date | None = None
    taxa_cambio: Decimal | None = None
    frete_seguro_usd: Decimal | None = None
    valor_unitario_usd: Decimal | None = None
    aliquota_ii: Decimal | None = None
    aliquota_ipi: Decimal | None = None
    aliquota_pis: Decimal | None = None
    aliquota_cofins: Decimal | None = None
    taxa_siscomex_usd: Decimal | None = None
    armazenagem_usd: Decimal | None = None
    despachante_sda_usd: Decimal | None = None
    despachante_honorarios_usd: Decimal | None = None
    corretagem_cambio_usd: Decimal | None = None
    inspecao_usd: Decimal | None = None
    outras_taxas_usd: Decimal | None = None
    aliquota_taxas_gerais: Decimal | None = None
    aliquota_impostos_fed: Decimal | None = None
    aliquota_icms: Decimal | None = None
    frete_nacional_usd: Decimal | None = None
    aliquota_intermediacao: Decimal | None = None


# ── NCM Cache ──────────────────────────────────────────────────────────


class NCMOut(BaseModel):
    ncm: str
    descricao: str | None = None
    aliquota_ii: Decimal | None = None
    aliquota_ipi: Decimal | None = None
    aliquota_pis: Decimal | None = None
    aliquota_cofins: Decimal | None = None
    fetched_at: datetime
    updated_at: datetime
    # `cached` = True quando veio do banco; False quando acabou de bater
    # na brasilapi. Front pode mostrar um indicador "consultado agora".
    cached: bool = True


class NCMPatch(BaseModel):
    aliquota_ii: Decimal | None = None
    aliquota_ipi: Decimal | None = None
    aliquota_pis: Decimal | None = None
    aliquota_cofins: Decimal | None = None


# ── DNP ────────────────────────────────────────────────────────────────


class DNPConfigOut(BaseModel):
    dolar_dia: Decimal | None = None
    certificado: Decimal | None = None
    updated_at: datetime


class DNPConfigPatch(BaseModel):
    dolar_dia: Decimal | None = None
    certificado: Decimal | None = None


class DNPProdutoOut(BaseModel):
    id: UUID
    produto: str | None = None
    link: str | None = None
    fabrica: str | None = None
    modelo: str | None = None
    moq: int | None = None
    descricao: str | None = None
    foto_url: str | None = None
    valor_usd: Decimal | None = None
    projecao_compra: int | None = None
    fator: Decimal | None = None
    venda_estimada: Decimal | None = None
    frete: Decimal | None = None
    comissao: Decimal | None = None
    inmetro: str | None = None
    obs: str | None = None
    created_at: datetime
    updated_at: datetime


class DNPProdutoPatch(BaseModel):
    produto: str | None = None
    link: str | None = None
    fabrica: str | None = None
    modelo: str | None = None
    moq: int | None = None
    descricao: str | None = None
    # foto_url is set via the dedicated photo-upload endpoint, not by
    # the cell-by-cell PATCH used for the rest of the row.
    valor_usd: Decimal | None = None
    projecao_compra: int | None = None
    fator: Decimal | None = None
    venda_estimada: Decimal | None = None
    frete: Decimal | None = None
    comissao: Decimal | None = None
    inmetro: str | None = None
    obs: str | None = None


# ── Valuation (relatório 3 meses) ────────────────────────────────────────
# Porta web do antigo PDF faturamento_3meses_pdf.py (projeto ClaudeCode).
# Mesmas regras de cálculo; lê davinci.bling_orders / valuation / stores /
# situacao_bling ao vivo (atualizadas pela rotina diária das 5h).


class ValuationMesOut(BaseModel):
    """Saldos = snapshot do último dia disponível do mês; rentabilidade =
    SUM do mês (fluxo). `data_snapshot` = data do saldo usado. Valores em
    float (arredondados) p/ o front consumir como número direto no JSON."""

    mes: date
    caixa: float | None = None
    receber: float | None = None
    estoque: float | None = None
    total: float | None = None
    rentabilidade: float | None = None
    data_snapshot: date | None = None


class SituacaoLinhaOut(BaseModel):
    situacao_nome: str
    pedidos: int
    faturamento: float


class SituacaoSecaoOut(BaseModel):
    """Resumo de ontem / Eficácia operacional — pedidos por situação."""

    data: date | None = None
    linhas: list[SituacaoLinhaOut]
    total_pedidos: int
    total_faturamento: float


class FaturamentoGrpLinhaOut(BaseModel):
    """Uma linha (marketplace ou categoria) de um mês. Faturamento considera
    as situações aplicáveis; custo/rentabilidade/margem só Entregue."""

    grp: str
    faturamento: float
    custo: float
    rentabilidade: float
    margem: float | None = None  # rentabilidade / custo (%)


class FaturamentoMesSecaoOut(BaseModel):
    mes: date
    linhas: list[FaturamentoGrpLinhaOut]
    total_faturamento: float
    total_custo: float
    total_rentabilidade: float
    total_margem: float | None = None


class ValuationReportOut(BaseModel):
    gerado_em: datetime
    situacoes_label: str
    valuation_meses: list[ValuationMesOut]
    resumo_ontem: SituacaoSecaoOut
    eficacia: SituacaoSecaoOut
    por_marketplace: list[FaturamentoMesSecaoOut]
    por_categoria: list[FaturamentoMesSecaoOut]


# ── Estoque Bling (aba "Estoque Bling" da página Valuation) ──────────────


class EstoqueBlingLocalOut(BaseModel):
    local: str  # PI, SA, SP, RA, CD, CI, US, Eletro, Mala, Outros
    qtd: int
    valor: float


class EstoqueBlingSnapshotOut(BaseModel):
    """Último snapshot diário do estoque Bling, gravado pelo cron arq
    `valuation_estoque_snapshot` (~08h BRT). `data` é a data SP do crawl;
    `updated_at` é o momento exato em que o snapshot foi gravado."""

    data: date
    updated_at: datetime
    total_qtd: int
    total_valor: float
    locais: list[EstoqueBlingLocalOut]
