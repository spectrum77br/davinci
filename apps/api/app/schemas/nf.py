from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class NfFaturadorOut(BaseModel):
    id: UUID
    nome: str
    modo: str
    nf_cheia: bool
    percentual: Decimal | None = None
    sku_fonte: str | None = None
    nome_fonte: str | None = None
    ncm: str | None = None
    ads_power: str | None = None
    usuario: str | None = None
    # A senha nunca é devolvida; só sinaliza se está preenchida.
    has_senha: bool = False
    observacao: str | None = None
    sort_order: int
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


class NfFaturadorCreate(BaseModel):
    nome: str = Field(min_length=1)
    modo: str = Field(min_length=1)
    nf_cheia: bool = False
    percentual: Decimal | None = None
    sku_fonte: str | None = None
    nome_fonte: str | None = None
    ncm: str | None = None
    ads_power: str | None = None
    usuario: str | None = None
    senha: str | None = None
    observacao: str | None = None
    sort_order: int | None = None


class NfFaturadorPatch(BaseModel):
    nome: str | None = Field(default=None, min_length=1)
    modo: str | None = Field(default=None, min_length=1)
    nf_cheia: bool | None = None
    percentual: Decimal | None = None
    sku_fonte: str | None = None
    nome_fonte: str | None = None
    ncm: str | None = None
    ads_power: str | None = None
    usuario: str | None = None
    # None = não altera; "" = limpa a senha.
    senha: str | None = None
    observacao: str | None = None
    sort_order: int | None = None


class NfCatalogoMalaOut(BaseModel):
    id: UUID
    modelo: str
    tamanho: str | None = None
    valor: Decimal
    ncm: str | None = None
    sort_order: int
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


class NfCatalogoMalaCreate(BaseModel):
    modelo: str = Field(min_length=1)
    tamanho: str | None = None
    valor: Decimal
    ncm: str | None = None
    sort_order: int | None = None


class NfCatalogoMalaPatch(BaseModel):
    modelo: str | None = Field(default=None, min_length=1)
    tamanho: str | None = None
    valor: Decimal | None = None
    ncm: str | None = None
    sort_order: int | None = None


class NfEtiquetaOut(BaseModel):
    id: UUID
    plataforma: str
    modo: str | None = None
    ads_power: str | None = None
    observacao: str | None = None
    sort_order: int
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


class NfEtiquetaCreate(BaseModel):
    plataforma: str = Field(min_length=1)
    modo: str | None = None
    ads_power: str | None = None
    observacao: str | None = None
    sort_order: int | None = None


class NfEtiquetaPatch(BaseModel):
    plataforma: str | None = Field(default=None, min_length=1)
    modo: str | None = None
    ads_power: str | None = None
    observacao: str | None = None
    sort_order: int | None = None


class NfImpressaoOut(BaseModel):
    id: UUID
    tipo: str
    observacao: str | None = None
    visualizacao: str | None = None
    usa_etiqueta: bool
    usa_declaracao: bool
    usa_nota: bool
    sort_order: int
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


class NfImpressaoCreate(BaseModel):
    tipo: str = Field(min_length=1)
    observacao: str | None = None
    visualizacao: str | None = None
    usa_etiqueta: bool = False
    usa_declaracao: bool = False
    usa_nota: bool = False
    sort_order: int | None = None


class NfImpressaoPatch(BaseModel):
    tipo: str | None = Field(default=None, min_length=1)
    observacao: str | None = None
    visualizacao: str | None = None
    usa_etiqueta: bool | None = None
    usa_declaracao: bool | None = None
    usa_nota: bool | None = None
    sort_order: int | None = None


class GerarPlanilhaIn(BaseModel):
    """Números dos pedidos do Bling a incluir na planilha de importação avulsa."""

    numeros: list[str] = Field(min_length=1)


class ConferirFreteProduto(BaseModel):
    """Uma caixa/volume no formato do Melhor Envio (cm e kg)."""

    id: str = "1"
    width: Decimal = Field(gt=0)
    height: Decimal = Field(gt=0)
    length: Decimal = Field(gt=0)
    weight: Decimal = Field(gt=0)
    insurance_value: Decimal = Field(default=Decimal("0"), ge=0)
    quantity: int = Field(default=1, ge=1)


class ConferirFreteIn(BaseModel):
    """Cota o frete no Melhor Envio e confere contra o frete projetado.

    Impressão tipo "próprio" (Amazon): depois da NF, cota a etiqueta e vê se a
    menor opção cabe no `frete_projetado` (Tabela de Preços). `frete_projetado`
    None → só cota (não decide libera)."""

    from_cep: str = Field(min_length=8)
    to_cep: str = Field(min_length=8)
    produtos: list[ConferirFreteProduto] = Field(min_length=1)
    frete_projetado: Decimal | None = None
    servicos: str | None = None


class ConferirFreteAutoIn(BaseModel):
    """Puxa CEP destino, dimensões e frete projetado direto de um pedido."""

    pedido_bling: str = Field(min_length=1)


class ConferirFreteAutoOut(BaseModel):
    """Prefill do confere-frete resolvido do pedido — o operador revê no modal
    antes de cotar. `frete_projetado` None quando não resolve com segurança
    (nunca chuta — cai em `avisos`)."""

    from_cep: str
    to_cep: str
    produtos: list[ConferirFreteProduto] = []
    frete_projetado: Decimal | None = None
    plataforma: str | None = None
    conta: str | None = None
    nome_destinatario: str | None = None
    avisos: list[str] = []


class ConferirFreteCotacaoOut(BaseModel):
    servico_id: int | None = None
    servico_nome: str
    empresa: str
    preco: Decimal | None = None
    prazo_dias: int | None = None
    erro: str | None = None


class ConferirFreteOut(BaseModel):
    libera: bool
    motivo: str
    menor_frete: Decimal | None = None
    servico_escolhido: str | None = None
    empresa_escolhida: str | None = None
    prazo_dias: int | None = None
    frete_projetado: Decimal | None = None
    diferenca: Decimal | None = None
    cotacoes: list[ConferirFreteCotacaoOut] = []


class ConferirFretePedidoIn(BaseModel):
    """Confere-frete AUTO ponta a ponta: puxa do pedido E cota numa chamada só.

    Fluxo "impressão tipo próprio" (Amazon): dado só o nº do pedido, resolve
    CEP/caixa/frete projetado (como o /auto) e já cota no Melhor Envio (como o
    /conferir-frete), devolvendo a decisão `libera` + o contexto pra exibir."""

    pedido_bling: str = Field(min_length=1)
    servicos: str | None = None


class ConferirFretePedidoOut(BaseModel):
    """Decisão do confere-frete + contexto resolvido do pedido. `prefill_ok`
    False quando o frete projetado não pôde ser resolvido com segurança (a
    cotação ainda roda, mas a decisão `libera` fica sem base — ver `avisos`)."""

    pedido_bling: str
    from_cep: str
    to_cep: str
    plataforma: str | None = None
    conta: str | None = None
    nome_destinatario: str | None = None
    avisos: list[str] = []
    prefill_ok: bool = True
    conferencia: ConferirFreteOut


class NfFaturamentoRowOut(BaseModel):
    """Uma linha do Painel de Faturamento: descrição do pedido + os 3 status de
    etapa. Derivada (read-only) de bling_orders × store_info × nf_faturamento —
    não é o shape da tabela, é a linha exibida no painel."""

    data: date | None = None
    pedido_bling: str
    pedido_marketplace: str | None = None
    plataforma: str | None = None
    conta: str | None = None
    status_bling: str | None = None
    # 'pendente' quando não há registro em nf_faturamento; senão 'ok'/'erro'/...
    status_faturamento: str = "pendente"
    erro_faturamento: str | None = None
    status_etiqueta: str = "pendente"
    erro_etiqueta: str | None = None
    status_impressao: str = "pendente"
    erro_impressao: str | None = None
