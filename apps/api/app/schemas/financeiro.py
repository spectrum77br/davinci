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


class OperacionalLinhaOut(BaseModel):
    """Uma métrica do quadro "Operacional — 3 meses". `valores` tem um item por
    mês, alinhado a `OperacionalSecaoOut.meses`. `formato` diz como o front
    renderiza: 'brl' (moeda; Reembolso pode vir com sinal) ou 'pct' (Taxa de
    Perdimento). Valor None = sem dado no mês (Taxa quando faturamento = 0)."""

    chave: str
    label: str
    formato: str  # 'brl' | 'pct'
    descricao: str  # explicação da fórmula (tooltip "informativo" no hover)
    valores: list[float | None]


class OperacionalSecaoOut(BaseModel):
    """Quadro operacional dos últimos 3 meses (substitui a antiga Eficácia).
    `meses` = 1º-dia-do-mês de cada coluna; `linhas` = as métricas, em ordem."""

    meses: list[date]
    linhas: list[OperacionalLinhaOut]


class ComercialMembroOut(BaseModel):
    """Um membro (ex. "1.1") dentro de uma empresa, com duas séries por mês
    (alinhadas a `ComercialSecaoOut.meses`): `cancelamento` (R$ aguardando
    cancelamento) e `taxa_devolucao` (%)."""

    label: str
    cancelamento: list[float | None]
    taxa_devolucao: list[float | None]


class ComercialEmpresaOut(BaseModel):
    """Uma empresa = uma aba do bloco Comercial. `label` = "Empresa 1" /
    "Sem equipe". As séries são o subtotal da empresa (o que aparece na aba
    "Geral"); `membros` são as linhas mostradas ao abrir a aba da empresa
    ("Sem equipe" não tem membros)."""

    empresa: int | None = None
    label: str
    cancelamento: list[float | None]
    taxa_devolucao: list[float | None]
    membros: list[ComercialMembroOut]


class ComercialSecaoOut(BaseModel):
    """Bloco Comercial em abas: uma aba "Geral" (Total geral + subtotal por
    empresa) e uma aba por empresa (com os membros). Duas métricas por mês:
    Cancelamento (R$) e Taxa de Devolução (%). Hoje há 1 empresa — os
    `store_info.sales_team` viram membros 1.1/1.2/… dela; a ramificação real
    (loja → empresa) virá depois."""

    meses: list[date]
    total_cancelamento: list[float | None]
    total_taxa_devolucao: list[float | None]
    desc_cancelamento: str
    desc_taxa_devolucao: str
    empresas: list[ComercialEmpresaOut]


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
    operacional: OperacionalSecaoOut
    comercial: ComercialSecaoOut
    por_marketplace: list[FaturamentoMesSecaoOut]
    por_categoria: list[FaturamentoMesSecaoOut]


# ── Estoque Bling (aba "Estoque Bling" da página Valuation) ──────────────


class EstoqueBlingLocalOut(BaseModel):
    local: str  # PI, SA, SP, RA, CD, CI, US, Eletro, Mala, Outros
    qtd: int
    valor: float


class ValuationUnlockIn(BaseModel):
    password: str


class ValuationUnlockOut(BaseModel):
    token: str
    expires_in: int  # segundos


class EstoqueBlingSnapshotOut(BaseModel):
    """Último snapshot diário do estoque Bling, gravado pelo cron arq
    `valuation_estoque_snapshot` (~08h BRT). `data` é a data SP do crawl;
    `updated_at` é o momento exato em que o snapshot foi gravado."""

    data: date
    updated_at: datetime
    total_qtd: int
    total_valor: float
    locais: list[EstoqueBlingLocalOut]


# ── Saldo por marketplace (aba "Saldo Marketplace" da página Valuation) ──
# Snapshot gravado pela rotina externa AdsPower Contabilidade (run_all.py),
# desktop-only. Uma célula por loja × marketplace mostra disponível / a receber.


class SaldoMarketplaceCelulaOut(BaseModel):
    disponivel: float | None = None
    a_receber: float | None = None
    # Nota operacional quando não há valor real (ex.: "Deslogado",
    # "Verificação pendente"). Quando presente, a página mostra a nota
    # em vez de "—" / "R$ 0,00". Célula com nota não entra nos totais.
    nota: str | None = None


class SaldoMarketplaceLojaOut(BaseModel):
    loja: str
    # chave = marketplace (ml, shopee, amazon, …). Só vêm os presentes.
    saldos: dict[str, SaldoMarketplaceCelulaOut]
    # Total a receber da loja (soma dos a_receber dos marketplaces presentes).
    total_a_receber: float


class SaldoMarketplaceSnapshotOut(BaseModel):
    """Último snapshot diário de saldos por loja/marketplace. `total_a_receber`
    coincide com valuation.receber do dia (aba Resumo → "A Receber")."""

    data: date
    updated_at: datetime
    # Colunas da planilha, em ordem fixa preferida + extras no fim.
    marketplaces: list[str]
    lojas: list[SaldoMarketplaceLojaOut]
    total_a_receber: float
    total_disponivel: float
