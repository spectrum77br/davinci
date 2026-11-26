import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

from app.schemas.users import STOCK_TAGS, CommercialTeam

# --------------------------------------------------------------- pricing accounts

_DIAS_NO_MES = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _dia_mes(valor: str) -> str:
    """Desconto até / Cupom até (M-11): só "DD/MM" com dia e mês que existem
    (sem ano: 29/02 vale)."""
    m = re.fullmatch(r"(\d{2})/(\d{2})", valor)
    if not m or not 1 <= int(m[2]) <= 12 or not 1 <= int(m[1]) <= _DIAS_NO_MES[int(m[2]) - 1]:
        raise ValueError("use dia/mês válido, ex.: 26/06")
    return valor


def duas_casas(valor: Decimal | None) -> Decimal | None:
    """% com as 2 casas do banco (NUMERIC(5,2)), arredondado meio para cima.

    Revisão da M-56 (25/11/2026): a conferência do Custo total tem de usar o
    valor que FICA gravado; sem isto, 3,333 + 3,333 + 3,333 = 9,999 passava num
    teto de 9,99 e o banco gravava 3,33 × 3 = 9,99 (ou o contrário: a mensagem
    mostrava um número que não é o gravado)."""
    if valor is None:
        return None
    return valor.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# "26/06" (sem ano) e % de 0 a 100 (4 = 4%; o banco guarda 2 casas) — o mesmo
# CHECK do banco (migration 0502); fora disso, 422 em vez de erro do banco. O %
# já sai com as 2 casas do banco (`duas_casas`).
DiaMes = Annotated[str, AfterValidator(_dia_mes)]
PctConta = Annotated[Decimal, Field(ge=0, le=100), AfterValidator(duas_casas)]


class PricingAccountBase(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    platform: str
    listing_type: str | None = None
    # Inputs may set the root either via `segment_id` (preferred) or via the
    # legacy `department` slug; outputs always include both for UI compat.
    segment_id: UUID | None = None
    department: str | None = None
    kit_number: int = Field(default=1, ge=1, le=8)
    commission: Decimal | None = None
    margin1: Decimal | None = None
    shipping1: Decimal | None = None
    margin2: Decimal | None = None
    shipping2: Decimal | None = None
    margin3: Decimal | None = None
    shipping3: Decimal | None = None
    margin4: Decimal | None = None
    shipping4: Decimal | None = None
    margin5: Decimal | None = None
    shipping5: Decimal | None = None
    server: str | None = None
    email: str | None = None
    phone: str | None = None
    shipping_address: str | None = None
    return_address: str | None = None
    observation: str | None = None
    observation2: str | None = None
    observation3: str | None = None
    discount: str | None = None
    affiliate: str | None = None
    ads: str | None = None
    coupon: str | None = None
    offer: str | None = None
    # M-11: os campos da tela no lugar de discount/coupon/affiliate/offer.
    desconto_ate: DiaMes | None = None
    cupom_ate: DiaMes | None = None
    afiliado_pct: PctConta | None = None
    oferta_limite_pct: PctConta | None = None
    # M-55/M-56 (25/11/2026): Desconto % e Custo total % (este só o ADM grava).
    desconto_pct: PctConta | None = None
    custo_total_pct: PctConta | None = None
    # M-85 (26/11/2026): Margem mínima % (25 = 25%), só o ADM vê e grava.
    margem_minima_pct: PctConta | None = None
    store_info_id: UUID | None = None
    integration_id: UUID | None = None
    sort_order: int = 0
    # Slot ↔ segment binding. Each slot{N}_segment_id pins the segment that
    # margin{N}/shipping{N} apply to (e.g. slot1=Acessórios, slot2=Diversos).
    # NULL means "not assigned"; UI falls back to TYPE_HEADERS ordering when
    # rendering. Mirrors the columns added by migration 0048.
    slot1_segment_id: UUID | None = None
    slot2_segment_id: UUID | None = None
    slot3_segment_id: UUID | None = None
    slot4_segment_id: UUID | None = None
    slot5_segment_id: UUID | None = None


class PricingAccountCreate(PricingAccountBase):
    password: str | None = None


class PricingAccountPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    platform: str | None = None
    listing_type: str | None = None
    segment_id: UUID | None = None
    department: str | None = None
    kit_number: int | None = Field(default=None, ge=1, le=8)
    commission: Decimal | None = None
    margin1: Decimal | None = None
    shipping1: Decimal | None = None
    margin2: Decimal | None = None
    shipping2: Decimal | None = None
    margin3: Decimal | None = None
    shipping3: Decimal | None = None
    margin4: Decimal | None = None
    shipping4: Decimal | None = None
    margin5: Decimal | None = None
    shipping5: Decimal | None = None
    server: str | None = None
    email: str | None = None
    password: str | None = None
    phone: str | None = None
    shipping_address: str | None = None
    return_address: str | None = None
    observation: str | None = None
    observation2: str | None = None
    observation3: str | None = None
    discount: str | None = None
    affiliate: str | None = None
    ads: str | None = None
    coupon: str | None = None
    offer: str | None = None
    # M-11: os campos da tela no lugar de discount/coupon/affiliate/offer.
    desconto_ate: DiaMes | None = None
    cupom_ate: DiaMes | None = None
    afiliado_pct: PctConta | None = None
    oferta_limite_pct: PctConta | None = None
    desconto_pct: PctConta | None = None
    custo_total_pct: PctConta | None = None
    margem_minima_pct: PctConta | None = None
    store_info_id: UUID | None = None
    integration_id: UUID | None = None
    sort_order: int | None = None
    slot1_segment_id: UUID | None = None
    slot2_segment_id: UUID | None = None
    slot3_segment_id: UUID | None = None
    slot4_segment_id: UUID | None = None
    slot5_segment_id: UUID | None = None


class EquipeDaConta(BaseModel):
    """Uma equipe de vendas que enxerga a conta: o número de
    store_info.sales_team (101/102/103) e o rótulo da tela ("Equipe 1/2/3")."""

    numero: int
    nome: str


class PricingAccountOut(PricingAccountBase):
    model_config = ConfigDict(from_attributes=True)
    # M-10 (18/11/2026): kit, comissão, margens e fretes saem null para quem
    # não é administrador (routers/pricing.py, _CAMPOS_SO_ADMIN).
    kit_number: int | None = None
    id: UUID
    user_id: UUID
    has_password: bool = False
    created_at: datetime
    updated_at: datetime
    # Resolved segment names for each slot (read-only; router fills these).
    slot1_segment_name: str | None = None
    slot2_segment_name: str | None = None
    slot3_segment_name: str | None = None
    slot4_segment_name: str | None = None
    slot5_segment_name: str | None = None
    # Catálogo ML (06/10/2026). Só saída: a filha nasce/morre pelo
    # POST /accounts/{id}/catalogo. Na filha (canal='catalogo') comissão,
    # margens, fretes, kit_number e anotações saem com os valores da BASE
    # (a filha não guarda números). Na base, `conta_catalogo_id` é a filha
    # (None = catálogo desligado).
    canal: str = "kit"
    conta_base_id: UUID | None = None
    conta_catalogo_id: UUID | None = None
    # Margem própria do catálogo (07/10/2026), só na filha: {"1".."5": margem
    # própria do tipo, ou null = usa a da base}. Os margin1..5 acima continuam
    # com os da base. Grava com PATCH /accounts/{filha} {"marginN": ...} (só
    # margens; null volta à da base). Desligar o catálogo apaga a filha e as
    # margens próprias: ao religar, a filha nova nasce sem nenhuma.
    margens_catalogo: dict[str, Decimal | None] | None = None
    # M-53 (25/11/2026): o Clássico e o Premium da mesma conta ML de kit têm o
    # mesmo `par_id` (a aba Contas junta os dois numa linha; gravar numa linha
    # grava nas duas). Null = linha sem par (as outras plataformas, a coluna de
    # catálogo e conta ML com um tipo só).
    par_id: UUID | None = None
    # Coluna "Equipe" da aba Contas (03/11/2026): as equipes de vendas cuja
    # cerca deixa ver esta conta (a loja dela, pela régua de
    # `escopo_das_equipes`). Vazia = sem equipe. A filha sai com as da base.
    # Preenchida no /accounts e nas respostas de UMA conta (create/patch/
    # department/catálogo); o /grid não calcula (a grade não mostra).
    equipes: list[EquipeDaConta] = []


class ContaCatalogoIn(BaseModel):
    ativo: bool


# Comissão é fração (0.14 = 14%), como `commission`.
ComissaoMl = Annotated[Decimal | None, Field(ge=0, lt=1)]


class ComissaoMlIn(BaseModel):
    """M-53: a comissão do Clássico e a do Premium da conta ML, gravadas
    juntas (PUT /accounts/{id}/comissao-ml). Null apaga."""

    classico: ComissaoMl
    premium: ComissaoMl


class ComissaoMlOut(BaseModel):
    classico: PricingAccountOut
    premium: PricingAccountOut


class ContaCatalogoOut(BaseModel):
    ativo: bool
    conta: PricingAccountOut
    conta_catalogo: PricingAccountOut | None = None


# --------------------------------------------------------------- pricing products

def _norm_prioridade_estoque(v: str | None) -> str | None:
    """Normaliza a tag de prioridade de estoque: lowercase, vazio→None.

    O conjunto válido é SUFFIX_TAGS (ci/pi/ra/sa/sp/us/cd) de
    services/sku_tags.py — os sufixos FÍSICOS de SKU (dg053.ci, dg053.sp…).
    fake/mala/eletro/insumos ficam de fora: são grupos de operador, não
    sufixos trocáveis num código de produto.
    """
    from app.services.sku_tags import SUFFIX_TAGS

    if v is None:
        return None
    tag = v.strip().lower().lstrip(".")
    if not tag:
        return None
    if tag not in SUFFIX_TAGS:
        raise ValueError(
            f"prioridade_estoque deve ser uma de {', '.join(SUFFIX_TAGS)}"
        )
    return tag


class PricingProductBase(BaseModel):
    sku: str = Field(min_length=1, max_length=2048)
    name: str = Field(min_length=1, max_length=512)
    # Either `segment_id` (preferred, must be a leaf) or the legacy
    # (`department` + `product_type`) pair. Router resolves to segment_id.
    segment_id: UUID | None = None
    department: str | None = None
    product_type: int | None = None
    bling_cost_price: Decimal | None = None
    cost_kit1: Decimal = Decimal("0")
    cost_kit2: Decimal | None = None
    cost_kit3: Decimal | None = None
    cost_kit4: Decimal | None = None
    cost_kit5: Decimal | None = None
    cost_kit6: Decimal | None = None
    cost_kit7: Decimal | None = None
    cost_kit8: Decimal | None = None
    # Custo base do anúncio de catálogo do ML (coluna "Catálogo"). None = sem.
    preco_catalogo: Decimal | None = None
    description: str | None = None
    model: str | None = None
    ean: str | None = None
    is_active: bool = True
    # Link das fotos do produto (pasta do MEGA com todas as cores).
    fotos_url: str | None = None
    # Caminho da pasta na conta MEGA (gerido pela sincronização/upload).
    fotos_path: str | None = None
    product_id: UUID | None = None
    # Tag de estoque prioritária (Eduardo 2026-08-27: "a tag que eu colocar
    # la, o sku com a tag, ja deve trocar, porque a prioridade e ele").
    prioridade_estoque: str | None = None
    # Prazo da garantia do produto, em meses (coluna "Garantia (meses)",
    # 06/11/2026). None = sem prazo cadastrado; 0 = sem garantia ("se for
    # avulso não tem garantia", 06/11/2026).
    garantia_meses: int | None = Field(None, ge=0, le=120)

    @field_validator("prioridade_estoque")
    @classmethod
    def _valida_prioridade(cls, v: str | None) -> str | None:
        return _norm_prioridade_estoque(v)


class PricingProductCreate(PricingProductBase):
    pass


class PricingProductPatch(BaseModel):
    sku: str | None = Field(default=None, min_length=1, max_length=2048)
    name: str | None = Field(default=None, min_length=1, max_length=512)
    segment_id: UUID | None = None
    department: str | None = None
    product_type: int | None = None
    bling_cost_price: Decimal | None = None
    cost_kit1: Decimal | None = None
    cost_kit2: Decimal | None = None
    cost_kit3: Decimal | None = None
    cost_kit4: Decimal | None = None
    cost_kit5: Decimal | None = None
    cost_kit6: Decimal | None = None
    cost_kit7: Decimal | None = None
    cost_kit8: Decimal | None = None
    preco_catalogo: Decimal | None = None
    description: str | None = None
    model: str | None = None
    ean: str | None = None
    is_active: bool | None = None
    fotos_url: str | None = None
    product_id: UUID | None = None
    prioridade_estoque: str | None = None
    # Coluna Modelo da aba Produtos: só leitura desde a M-69 (26/11/2026).
    # Fica aqui só para o PATCH que ainda o mandar receber 400
    # certificado_pela_certificacoes ("definido em Importação › Certificações")
    # em vez de ser ignorado calado.
    certificacao_id: UUID | None = None
    # Coluna "Garantia (meses)" (06/11/2026): 0 a 120 (0 = sem garantia);
    # null apaga o prazo. Produto cujo certificado tem garantia: grava no
    # CERTIFICADO e vale para todos os produtos dele (null aí = 422
    # garantia_do_certificado: apaga-se na Certificações).
    garantia_meses: int | None = Field(None, ge=0, le=120)
    # Coluna Validade (06/11/2026, "deixe a opção de editar na tabela de
    # preços"): o Fim do certificado do produto — vale para todos os produtos
    # dele. null apaga a data. Produto sem certificado: 422 sem_certificado.
    certificado_fim: date | None = None

    @field_validator("prioridade_estoque")
    @classmethod
    def _valida_prioridade(cls, v: str | None) -> str | None:
        return _norm_prioridade_estoque(v)


class CertificacaoDoProdutoOut(BaseModel):
    """A linha da Certificações do produto: a da coluna SKU da Certificações
    ou, sem ela, a gravada (certificacao_id) — M-69,
    services/certificacao_produto.py. Só saída."""

    id: UUID
    produto: str | None = None
    modelo: str | None = None
    certificado: str | None = None  # anatel / inmetro / anvisa / isento
    numero: str | None = None
    fim: date | None = None  # validade (coluna Fim da Certificações)
    # Garantia (meses) do certificado (06/11/2026). Com valor, a coluna
    # Garantia (meses) do produto vem dele; None = o produto tem a dele.
    garantia_meses: int | None = None


class PricingProductOut(PricingProductBase):
    model_config = ConfigDict(from_attributes=True)
    sku: str = Field(max_length=2048)
    name: str = Field(max_length=512)
    id: UUID
    user_id: UUID
    created_at: datetime
    updated_at: datetime
    # Contagem de mídias na pasta do MEGA (NULL = nunca contado).
    fotos_count: int | None = None
    videos_count: int | None = None
    # Embalagens: subpasta "Embalagens" dentro da pasta de fotos (29/09/2026).
    # Só saída — quem grava é o envio/contagem do MEGA (routers/pricing_mega),
    # por isso Patch e Import não ganham estes campos.
    embalagens_url: str | None = None
    embalagens_path: str | None = None
    embalagens_count: int | None = None
    midias_contadas_em: datetime | None = None
    # Colunas Modelo / Nº do certificado / Validade da aba Produtos
    # (05/11/2026). Desde a M-69 (26/11/2026): o certificado da coluna SKU da
    # Certificações; sem ele, o gravado (certificacao_id). A ligação pelo nome
    # saiu.
    certificacao_id: UUID | None = None
    certificacao: CertificacaoDoProdutoOut | None = None
    # Os códigos da linha ("dg052,dg053") caem em certificados DIFERENTES na
    # coluna SKU: os nomes deles (Modelo, senão Nome comercial, Número,
    # Produto). O sistema não escolhe sozinho: vale o gravado e a tela avisa.
    certificacao_conflito: list[str] = Field(default_factory=list)


class PricingProductImportItem(BaseModel):
    """Lenient row used by /products/import — empty SKUs are skipped, not rejected."""
    sku: str = ""
    name: str = ""
    segment_id: UUID | None = None
    department: str | None = None
    product_type: int | None = None
    bling_cost_price: Decimal | None = None
    cost_kit1: Decimal = Decimal("0")
    cost_kit2: Decimal | None = None
    cost_kit3: Decimal | None = None
    cost_kit4: Decimal | None = None
    cost_kit5: Decimal | None = None
    cost_kit6: Decimal | None = None
    cost_kit7: Decimal | None = None
    cost_kit8: Decimal | None = None
    preco_catalogo: Decimal | None = None
    description: str | None = None
    model: str | None = None
    ean: str | None = None
    is_active: bool = True
    product_id: UUID | None = None


class PricingProductImport(BaseModel):
    """Bulk import payload (idempotent on (user_id, sku))."""
    items: list[PricingProductImportItem]


class PricingProductImportResult(BaseModel):
    created: int
    updated: int
    skipped: int


# -------------------------------------------------------------- pricing overrides

class PricingOverrideBase(BaseModel):
    pricing_product_id: UUID
    pricing_account_id: UUID
    price_override: Decimal | None = None
    cell_status: str = "auto"
    # Optional on the upsert path: if absent we preserve whatever's
    # already stored. The dedicated /overrides/cell-color endpoint is
    # the canonical way to set it.
    cell_color: str | None = None


class PricingOverrideUpsert(PricingOverrideBase):
    pass


class PricingOverrideCellStatus(BaseModel):
    pricing_product_id: UUID
    pricing_account_id: UUID
    cell_status: str


class PricingOverrideCellColor(BaseModel):
    pricing_product_id: UUID
    pricing_account_id: UUID
    cell_color: str | None = None  # None = clear the highlight


class PricingOverrideOut(PricingOverrideBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    user_id: UUID
    created_at: datetime
    updated_at: datetime


# ----------------------------------------------------------------- push + grid

class PricingPushIn(BaseModel):
    pricing_account_id: UUID
    pricing_product_id: UUID


class PricingPushBatchIn(BaseModel):
    items: list[PricingPushIn]


class PricingPushItemOut(BaseModel):
    pricing_account_id: UUID
    pricing_product_id: UUID
    ok: bool
    code: str
    detail: str | None = None
    price: Decimal | None = None
    item_id: str | None = None
    variation_id: str | None = None
    cached: bool = False
    # O detalhe do envio (conta usada e um item por anúncio em `links`) — a tela
    # conta os anúncios que receberam o preço.
    payload: dict | None = None


class PricingPushOut(BaseModel):
    results: list[PricingPushItemOut]


class PricingGridCatalogoAnuncio(BaseModel):
    external_id: str
    status: str | None = None  # active/paused/under_review/closed/inactive (lido na varredura)
    listing_type: str | None = None
    sincronizado_com: list[str] = []
    # Motivo de ESTE anúncio não receber: mesmo_anuncio (outra linha da tabela
    # também casa com ele) | sincronizado | pausado | em_revisao | encerrado |
    # so_catalogo (célula de Kit: o anúncio é da coluna Catálogo).
    bloqueio: str | None = None


class PricingGridCatalogo(BaseModel):
    """Célula de conta de catálogo: para onde vai o preço e, se não vai, por quê."""
    anuncios: list[PricingGridCatalogoAnuncio] = []
    # None | sem_anuncio | mesmo_anuncio | sincronizado | pausado | em_revisao |
    # encerrado | sem_tipo | sem_preco_catalogo | so_catalogo (só célula de Kit)
    bloqueio: str | None = None
    texto: str | None = None


class PricingGridCell(BaseModel):
    pricing_account_id: UUID
    pricing_product_id: UUID
    price: Decimal | None = None
    source: str  # computed | override | locked | disabled | missing_inputs
    cell_status: str = "auto"
    has_override: bool = False
    cell_color: str | None = None
    # Nas colunas de catálogo (conta canal='catalogo'). Na de Kit só quando a
    # conta tem o catálogo ligado e o único anúncio que casa é de catálogo
    # (bloqueio 'so_catalogo'); None nas outras.
    catalogo: PricingGridCatalogo | None = None


class PricingGridOut(BaseModel):
    accounts: list[PricingAccountOut]
    products: list[PricingProductOut]
    cells: list[PricingGridCell]


# ----------------------------------------------------------------- audit (9d)

class SkuAuditRow(BaseModel):
    sku: str
    title: str | None = None
    stock: int | None = None
    accounts: list[str] = []
    account_count: int = 0
    issues: list[str] = []
    bling_cost: str | None = None
    pricing_cost: str | None = None
    # Legacy/back-compat fields (kept so older callers don't break).
    listing_count: int = 0
    platforms: list[str] = []
    integration_ids: list[str] = []
    sample_titles: list[str] = []
    dismissed: bool = False


# ---------------------------------------------------------- competitor (9d)

class CompetitorPriceRow(BaseModel):
    item_id: str
    title: str
    price: float
    currency: str
    permalink: str
    seller_id: int | None = None
    condition: str | None = None
    sold_quantity: int | None = None
    available_quantity: int | None = None
    thumbnail: str | None = None


# ----------------------------------------------------------------- store_info (9d)

class StoreExcecao(BaseModel):
    """Regra de exceção de envio da loja (campo "Exceções" da tela Lojas).

    A UF vem do campo "Restrição" da loja (uf_restrictions) — pedido pra
    essas UFs é barrado e a regra diz o que PODE ir mesmo assim:
      - tipo "valor":   o valor total do pedido é < `valor`;
      - tipo "sku":     algum item tem um dos SKUs em `termos`;
      - tipo "palavra": o nome de algum item contém uma palavra de `termos`.
    (Regras antigas salvas com `uf` seguem válidas — a chave é ignorada.)
    """

    tipo: Literal["valor", "sku", "palavra"]
    valor: float | None = None
    termos: list[str] | None = None


def normaliza_etiqueta_horarios(v: str | None) -> str | None:
    """"HH:MM" separados por vírgula, ordenados e sem repetir.

    Aceita "10:00" / "10h" / "1000" e devolve None quando vazio (= contínuo:
    a etiqueta é impressa assim que a NF fecha).
    """
    if v is None:
        return None
    saida: set[str] = set()
    for bruto in str(v).replace(";", ",").split(","):
        txt = bruto.strip().lower().replace("h", ":")
        if not txt:
            continue
        if ":" not in txt and txt.isdigit() and len(txt) in (3, 4):
            txt = f"{txt[:-2]}:{txt[-2:]}"
        hora, _, minuto = txt.partition(":")
        minuto = minuto or "0"
        if not hora.isdigit() or not minuto.isdigit():
            raise ValueError(f"horário inválido: {bruto}")
        h, m = int(hora), int(minuto)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError(f"horário inválido: {bruto}")
        saida.add(f"{h:02d}:{m:02d}")
    return ", ".join(sorted(saida)) or None


def normaliza_hora_sabado(v: str | None) -> str | None:
    """Um único "HH:MM" (BRT). Vazio = a loja não emite no sábado."""
    saida = normaliza_etiqueta_horarios(v)
    if saida and "," in saida:
        raise ValueError("informe um único horário para o sábado")
    return saida


def normaliza_faturador_por_tipo(v: dict | None) -> dict[str, str] | None:
    """Faturador POR TIPO (migration 0228): {"celular": "<uuid>", …}.

    Valores como UUID em string (a coluna é JSONB — UUID objeto quebraria o
    json.dumps do driver). Chaves vazias/valores vazios caem fora; dict vazio
    vira None (= regra única, nf_faturador_id).
    """
    if not v:
        return None
    saida: dict[str, str] = {}
    for chave, valor in v.items():
        slug = str(chave or "").strip().lower()
        if not slug or not valor:
            continue
        try:
            saida[slug] = str(UUID(str(valor)))
        except ValueError as exc:  # noqa: PERF203
            raise ValueError(f"faturador inválido para {slug}") from exc
    return saida or None


def normaliza_tags_estoque(v: str | None) -> str | None:
    """Slugs de estoque separados por vírgula, na ordem canônica de STOCK_TAGS.

    Ex. "SP, pi" → "pi, sp". Vazio = nenhum estoque marcado.
    """
    if v is None:
        return None
    escolhidas = {t.strip().lower() for t in str(v).replace(";", ",").split(",") if t.strip()}
    invalidas = escolhidas - set(STOCK_TAGS)
    if invalidas:
        raise ValueError(f"tag de estoque inválida: {', '.join(sorted(invalidas))}")
    return ", ".join(t for t in STOCK_TAGS if t in escolhidas) or None


class StoreInfoBase(BaseModel):
    platform: str = Field(min_length=1, max_length=64)
    segment: str | None = None
    freight: str | None = None
    cpf_name: str | None = None
    account_name: str | None = None
    server: str | None = None
    cnpj: str | None = None
    email: str | None = None
    observation: str | None = None
    shipping_address: str | None = None
    return_address: str | None = None
    phone: str | None = None
    link: str | None = None
    integration_id: UUID | None = None
    sort_order: int = 0
    # Lojas screen extras (migration 0043).
    bling_store_id: str | None = None
    upseseller: bool | None = None
    duoker: bool | None = None
    uf_restrictions: list[str] | None = None
    # Exceções de envio por loja (migration 0216) — ver StoreExcecao.
    excecoes: list[StoreExcecao] | None = None
    # Código interno de acesso (migration 0136), independente da equipe comercial.
    sales_team: int | None = None
    commercial_team: CommercialTeam | None = None
    # NF automáticas (migration 0196): cadastros Faturador/Etiqueta/Impressão.
    nf_faturador_id: UUID | None = None
    # Faturador POR TIPO (migration 0228) — contas com 2+ tipos na coluna
    # Tipo: {"celular": "<uuid>", "eletro": "<uuid>"}. None = regra única.
    nf_faturador_por_tipo: dict[str, str] | None = None
    # Faturador da NF PRODUTO (migration 0226) — coluna "Faturador produto".
    nf_faturador_produto_id: UUID | None = None
    nf_etiqueta_id: UUID | None = None
    nf_impressao_id: UUID | None = None
    # Horários (BRT) em que as etiquetas da loja são impressas (migration
    # 0222), ex. "10:00, 14:00". Vazio = contínuo.
    etiqueta_horarios: str | None = None
    # Sábado (migration 0223) — só Mercado Livre. Correios segue contínuo;
    # agência emite uma vez neste horário, só nos estoques marcados.
    etiqueta_sabado_horario: str | None = None
    etiqueta_sabado_tags: str | None = None

    _norm_horarios = field_validator("etiqueta_horarios")(normaliza_etiqueta_horarios)
    _norm_sabado_hora = field_validator("etiqueta_sabado_horario")(normaliza_hora_sabado)
    _norm_sabado_tags = field_validator("etiqueta_sabado_tags")(normaliza_tags_estoque)
    _norm_fat_tipo = field_validator("nf_faturador_por_tipo")(normaliza_faturador_por_tipo)


class StoreInfoCreate(StoreInfoBase):
    password: str | None = None


class StoreInfoPatch(BaseModel):
    platform: str | None = None
    segment: str | None = None
    freight: str | None = None
    cpf_name: str | None = None
    account_name: str | None = None
    server: str | None = None
    cnpj: str | None = None
    email: str | None = None
    observation: str | None = None
    shipping_address: str | None = None
    return_address: str | None = None
    phone: str | None = None
    password: str | None = None
    link: str | None = None
    integration_id: UUID | None = None
    sort_order: int | None = None
    bling_store_id: str | None = None
    upseseller: bool | None = None
    duoker: bool | None = None
    uf_restrictions: list[str] | None = None
    excecoes: list[StoreExcecao] | None = None
    sales_team: int | None = None
    commercial_team: CommercialTeam | None = None
    nf_faturador_id: UUID | None = None
    nf_faturador_por_tipo: dict[str, str] | None = None
    nf_faturador_produto_id: UUID | None = None
    nf_etiqueta_id: UUID | None = None
    nf_impressao_id: UUID | None = None
    etiqueta_horarios: str | None = None
    etiqueta_sabado_horario: str | None = None
    etiqueta_sabado_tags: str | None = None

    _norm_horarios = field_validator("etiqueta_horarios")(normaliza_etiqueta_horarios)
    _norm_sabado_hora = field_validator("etiqueta_sabado_horario")(normaliza_hora_sabado)
    _norm_sabado_tags = field_validator("etiqueta_sabado_tags")(normaliza_tags_estoque)
    _norm_fat_tipo = field_validator("nf_faturador_por_tipo")(normaliza_faturador_por_tipo)


class StoreInfoOut(StoreInfoBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    user_id: UUID
    has_password: bool = False
    # Computed fields (mirroring the SSH "Tipo / Tab.Preço / Integração" badges)
    departments: list[str] = []
    has_pricing: bool = False
    has_integration: bool = False
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------- auto-match (9d)

class AutoMatchResult(BaseModel):
    matched: int
    skipped: int
    accounts: list[UUID] = []


class AccountSetDepartmentIn(BaseModel):
    department: str
