<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted, watch } from 'vue'
import {
  Plus, Trash2, Download, RefreshCw, ChevronDown, ChevronRight, X, Loader2, Zap, Upload, Search, Tags, Link2,
  Smartphone, Briefcase, CircleDashed, Package, Megaphone, EyeOff, Eye, CloudDownload,
} from 'lucide-vue-next'
import type ProdutosAnunciosPainel from '~/components/ProdutosAnunciosPainel.vue'

// 13/11/2026 (Organização do DaVinci, item 2): Anúncios virou aba daqui. Entra
// quem vê Produtos OU Anúncios; cada aba pede a sua permissão.
definePageMeta({
  middleware: ['permission'],
  permissionAny: [
    { resource: 'produtos', action: 'view' },
    { resource: 'anuncios', action: 'view' },
  ],
})

type Integration = {
  id: string
  platform: 'bling' | 'ml' | 'shopee' | 'amazon' | 'tiktok' | 'magalu' | 'carrefour' | 'temu'
  name: string
  store_id: string | null
}

type ProductLink = {
  id: string
  product_id: string
  integration_id: string
  store_id: string | null
  platform: string
  external_id: string
  variation_id: string | null
  external_sku: string | null
  listing_title: string | null
  listing_type: string | null
  stock: number | null
  price: string | null
  last_sync_status: string
  last_sync_at: string | null
  last_error: string | null
  // Anúncio que o marketplace disse que acabou (encerrado/excluído/bloqueado):
  // não recebe mais envio e sai sozinho em 30 dias.
  morto_desde?: string | null
  morto_motivo?: string | null
}

type Product = {
  id: string
  user_id: string
  sku: string
  name: string
  category: string | null
  segment_id: string | null
  segment_name: string | null
  segment_path: string | null
  cost_price: string | null
  bling_cost_price: string | null
  price: string | null
  stock: number
  min_stock: number
  bling_product_id: number | null
  integration_id: string | null
  image_url: string | null
  observation: string | null
  observation2: string | null
  observation3: string | null
  last_imported_at: string | null
  created_at: string
  updated_at: string
  links: ProductLink[]
}

type Segment = {
  id: string
  parent_id: string | null
  name: string
  slug: string
  sort_order: number
  active: boolean
}

type SaudeVinculos = { mortos: number; sku_divergente: number; erro: number }
type ProductPage = {
  items: Product[]; total: number; page: number; page_size: number; saude?: SaudeVinculos | null
}

type BlingPreviewItem = {
  bling_product_id: number
  sku: string | null
  name: string
  cost_price: string | null
  price: string | null
  stock: number | null
  image_url: string | null
  already_imported?: boolean
}

type Job = {
  id: string
  type: string
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled'
  total: number
  processed: number
  payload: Record<string, unknown>
  result: Record<string, unknown>
  // `details` now comes from GET /api/jobs/{id}/details (paged), not the job
  // row. The light /status poll carries only `details_count`; the single-sync
  // POST still returns `details` inline. Both optional so `activeJob` (status)
  // and the single-sync response share this type.
  details?: Array<Record<string, unknown>>
  details_count?: number
  error: string | null
  started_at: string | null
  finished_at: string | null
  created_at?: string | null
}

const { api } = useApi()
const canEdit = useCan('produtos', 'edit')
const canDelete = useCan('produtos', 'delete')
// Organizar (esconder da tela o que não vale mais) é só do administrador.
const isAdmin = useIsAdmin()

// ── Departamento + aba (Organização do DaVinci, 13/11/2026, item 2) ─────────
// Mesmo padrão da Tabela de Preços: primeiro o departamento (Celular · Eletro ·
// Mala · Sem departamento — a ordem alfabética da M-23, 23/11/2026, a de
// lib/categorias.ts), depois a aba (Produtos · Anúncios). Os dois
// ficam na URL (?dept= e ?aba=), como o ?dept= da Tabela; /anuncios redireciona
// para ?aba=anuncios. O departamento sai da raiz do segmento do produto
// (Catálogo entra em Celular); sem segmento, a API deduz pela categoria do
// Bling ou pela família do SKU (15/11/2026, services/departamento.py) — por
// isso há produto sem segmento em Celular, Mala e Eletro. Anúncio, pelo produto
// vinculado — sem produto, Sem departamento. Os botões não têm número; as abas
// mostram "(N)".
const route = useRoute()
const router = useRouter()
const podeProdutos = useCan('produtos', 'view')
const podeAnuncios = useCan('anuncios', 'view')

type Departamento = 'celular' | 'mala' | 'eletro' | 'sem'
type Aba = 'produtos' | 'anuncios'
const DEPARTAMENTOS: { value: Departamento; label: string; icon: any }[] = [
  { value: 'celular', label: 'Celular', icon: Smartphone },
  { value: 'eletro', label: 'Eletro', icon: Zap },
  { value: 'mala', label: 'Mala', icon: Briefcase },
  { value: 'sem', label: 'Sem departamento', icon: CircleDashed },
]
// Raiz do segmento → departamento (o mesmo mapa de apps/api/app/services/departamento.py).
const RAIZ_PARA_DEPARTAMENTO: Record<string, Departamento> = {
  celular: 'celular', catalogo: 'celular', mala: 'mala', eletro: 'eletro',
}

type ContagemDepartamentos = Record<Departamento, number>
type DepartamentosOut = { produtos: ContagemDepartamentos | null; anuncios: ContagemDepartamentos | null }
const contagens = ref<DepartamentosOut | null>(null)
async function carregarDepartamentos() {
  try {
    contagens.value = await api<DepartamentosOut>('/api/products/departamentos')
  } catch {
    // Sem os números: as abas ficam sem "(N)" e o Sem departamento só aparece
    // se for o escolhido.
  }
}
// "Sem departamento" só aparece quando houver produto ou anúncio nele: é a
// lista do que falta classificar e some quando acaba.
const qtdSemDepartamento = computed(() => contagens.value
  ? (contagens.value.produtos?.sem ?? 0) + (contagens.value.anuncios?.sem ?? 0)
  : null)
const mostrarSemDepartamento = computed(() => qtdSemDepartamento.value == null
  ? route.query.dept === 'sem'
  : qtdSemDepartamento.value > 0)
const departamentosVisiveis = computed(() =>
  DEPARTAMENTOS.filter((d) => d.value !== 'sem' || mostrarSemDepartamento.value))
// ?dept= ausente, desconhecido ou "sem" já vazio → Celular (como a Tabela).
const departamento = computed<Departamento>({
  get() {
    const q = typeof route.query.dept === 'string' ? route.query.dept : ''
    return departamentosVisiveis.value.some((d) => d.value === q) ? q as Departamento : 'celular'
  },
  set(v) {
    router.replace({ path: route.path, query: { ...route.query, dept: v } })
  },
})
// Aba: a pedida, se a pessoa pode ver; senão a outra.
const aba = computed<Aba>(() => {
  const q = route.query.aba === 'anuncios' ? 'anuncios' : 'produtos'
  if (q === 'anuncios' && !podeAnuncios.value) return 'produtos'
  if (q === 'produtos' && !podeProdutos.value) return 'anuncios'
  return q
})
function setAba(a: Aba) {
  if (a === aba.value) return
  router.push({ path: route.path, query: { ...route.query, aba: a } })
}
const ABAS = computed(() => [
  { key: 'produtos' as Aba, label: 'Produtos', icon: Package, pode: podeProdutos.value,
    qtd: contagens.value?.produtos?.[departamento.value] },
  { key: 'anuncios' as Aba, label: 'Anúncios', icon: Megaphone, pode: podeAnuncios.value,
    qtd: contagens.value?.anuncios?.[departamento.value] },
].filter((a) => a.pode))
const painelAnuncios = ref<InstanceType<typeof ProdutosAnunciosPainel> | null>(null)
// Departamento cujos produtos estão na tela (para não recarregar à toa ao
// voltar da aba Anúncios).
const produtosCarregadosDe = ref<Departamento | null>(null)

const integrations = ref<Integration[]>([])
const items = ref<Product[]>([])
const segments = ref<Segment[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 50
const filtroIntegration = ref<string>('')
const filtroSegment = ref<string>('')
const stockFilter = ref<'' | 'low' | 'ok' | 'zero'>('')
// Saúde dos vínculos (28/09/2026): mortos, SKU do anúncio diferente, erro.
const filtroVinculos = ref<'' | 'mortos' | 'sku' | 'erro'>('')
const saude = ref<SaudeVinculos>({ mortos: 0, sku_divergente: 0, erro: 0 })
const expanded = ref<Set<string>>(new Set())
const selected = ref<Set<string>>(new Set())
// Checked product_links in the expanded rows (Item 4 — batch link delete).
// Keyed by link id (globally unique), so one Set covers every open row.
const selectedLinks = ref<Set<string>>(new Set())
const loading = ref(false)
const error = ref<string | null>(null)
const mobileProductView = ref<'cards' | 'table'>('cards')
const mobileProductTools = ref(false)
const mobileProductFilters = ref(false)
const mobileProductExpanded = ref<Set<string>>(new Set())
function setMobileProductDetails(id: string, event: Event) {
  const open = (event.target as HTMLDetailsElement).open
  if (mobileProductExpanded.value.has(id) === open) return
  const next = new Set(mobileProductExpanded.value)
  if (open) next.add(id)
  else next.delete(id)
  mobileProductExpanded.value = next
}

// The mobile overview follows the selected account just like table link columns.
function mobileLinksFor(product: Product): ProductLink[] {
  return filtroIntegration.value
    ? product.links.filter((link) => link.integration_id === filtroIntegration.value)
    : product.links
}
const mobilePlatformLabels: Record<string, string> = {
  bling: 'Bling', ml: 'Mercado Livre', shopee: 'Shopee', amazon: 'Amazon',
  tiktok: 'TikTok', magalu: 'Magalu', temu: 'Temu', aliexpress: 'AliExpress',
  carrefour: 'Carrefour',
}
function nomePlataforma(p: string | null | undefined): string {
  return (p && mobilePlatformLabels[p]) || p || ''
}

// Busca por ID do anúncio (estado declarado aqui em cima porque `refreshAll`
// — chamada no setup — consulta `anuncioAberto`). Detalhes lá embaixo, na
// seção "Anúncio por ID".
// MLB1234567 (ML) ou 6+ dígitos (Shopee/TikTok). Menos que isso é SKU/nome.
const ID_ANUNCIO_RE = /^(MLB\d{5,}|\d{6,})$/i
const search = ref('')
const buscaEhIdAnuncio = computed(() => ID_ANUNCIO_RE.test(search.value.trim()))
const anuncio = ref<AnuncioLookup | null>(null)
const anuncioLoading = ref(false)
const anuncioErro = ref<string | null>(null)
const anuncioSyncing = ref(false)
const anuncioAberto = computed(() => !!anuncio.value?.encontrado)

type Toast = {
  id: number
  kind: 'success' | 'error' | 'warning'
  title: string
  lines: string[]
}
const toasts = ref<Toast[]>([])
let _toastId = 0
function pushToast(t: Omit<Toast, 'id'>, ttl = 5000) {
  const id = ++_toastId
  toasts.value = [...toasts.value, { id, ...t }]
  window.setTimeout(() => {
    toasts.value = toasts.value.filter((x) => x.id !== id)
  }, ttl)
}
function dismissToast(id: number) {
  toasts.value = toasts.value.filter((x) => x.id !== id)
}

const showImport = ref(false)
// Barra comum às duas abas (17/11/2026): Atualizar → Vincular → Sincronizar,
// mais o Organizar (só ADM). showAutoLink = Vincular; showSyncAll = Sincronizar.
const showAtualizar = ref(false)
const showAutoLink = ref(false)
const showSyncAll = ref(false)
const showOrganizar = ref(false)
// Força a sincronização em massa a furar as travas do marketplace (zero-guard
// da B1 + short-circuit de pausado/encerrado), reativando anúncios que o ML
// pausou sozinho ao zerar o estoque. Igual ao sync individual por produto.
const forceSyncAll = ref(false)
const showRefreshStock = ref(false)

// SSH-style selection dialogs
const selectedAutoLinkIds = ref<Set<string>>(new Set())
const selectedSyncAllIds = ref<Set<string>>(new Set())

// Per-product sync popover. O popover é renderizado via <Teleport>
// pra fora do .table-card (que tem overflow-hidden pro rounded-xl) —
// senão o conteúdo é clipado pela borda inferior da tabela e o
// operador na última row da página não vê as opções. `Anchor` guarda
// o getBoundingClientRect do botão no momento do clique; o popover
// é posicionado em `position: fixed` a partir dele, e fecha no
// scroll ou click-outside (anchor invalida).
const syncPopoverProductId = ref<string | null>(null)
const syncPopoverSelectedIds = ref<Set<string>>(new Set())
const syncPopoverAnchor = ref<DOMRect | null>(null)
const SYNC_POPOVER_WIDTH = 288 // = Tailwind w-72
const syncPopoverStyle = computed((): Record<string, string> => {
  const r = syncPopoverAnchor.value
  if (!r) return { display: 'none' }
  const vw = typeof window !== 'undefined' ? window.innerWidth : 1024
  // Right-align com o botão (como o popover original `right-0 top-full`).
  // Clamp pra não vazar nas bordas do viewport.
  const left = Math.max(8, Math.min(vw - SYNC_POPOVER_WIDTH - 8, r.right - SYNC_POPOVER_WIDTH))
  return {
    position: 'fixed',
    top: `${r.bottom + 4}px`,
    left: `${left}px`,
    width: `${SYNC_POPOVER_WIDTH}px`,
  }
})

// Feature 1: CSV import
const showImportCsv = ref(false)
const csvFile = ref<File | null>(null)
const csvImporting = ref(false)
const csvImportError = ref<string | null>(null)

// Feature 2: New product
const showNewProduct = ref(false)
const newProduct = reactive({
  sku: '',
  name: '',
  cost_price: '',
  stock: 0,
  min_stock: 0,
})
const creatingProduct = ref(false)

// Feature 4: platform badge helper
function platformBadgeClass(platform: string): string {
  const baseClass = 'inline-block px-2 py-0.5 rounded text-xs font-medium'
  switch (platform) {
    case 'bling': return `${baseClass} bg-green-100 text-green-700`
    case 'shopee': return `${baseClass} bg-orange-100 text-orange-700`
    case 'amazon': return `${baseClass} bg-yellow-100 text-yellow-700`
    case 'ml': return `${baseClass} bg-blue-100 text-blue-700`
    case 'tiktok': return `${baseClass} bg-pink-100 text-pink-700`
    case 'magalu': return `${baseClass} bg-indigo-100 text-indigo-700`
    case 'temu': return `${baseClass} bg-purple-100 text-purple-700`
    case 'aliexpress': return `${baseClass} bg-red-100 text-red-700`
    default: return `${baseClass} bg-gray-100 text-gray-700`
  }
}

type UserSettings = { daily_sync_enabled: boolean }
const autoSyncEnabled = ref<boolean>(false)
const togglingAutoSync = ref(false)

// Leitura mais nova vence: resposta de uma leitura anterior (outro
// departamento, outra página) que chega depois é descartada.
let seqProdutos = 0
async function refreshAll() {
  const minha = ++seqProdutos
  loading.value = true
  error.value = null
  // Com o painel de um anúncio aberto (busca por ID), o texto do campo é o ID
  // do anúncio — mandá-lo como filtro esvaziaria a tabela de produtos à toa.
  const buscaProdutos = anuncioAberto.value ? '' : search.value
  try {
    // Primeiro os números por departamento (classificar / vincular / importar
    // mudam as contas): eles decidem se "Sem departamento" ainda existe e,
    // com isso, qual departamento a tela mostra. Falha aqui não derruba a lista.
    await carregarDepartamentos()
    const dept = departamento.value
    const [pg, integ, settings, segs] = await Promise.all([
      // ocultos=esconder: o que o ADM escondeu no "Organizar" sai desta lista
      // (e da faixa "Vínculos"). Só a tela pede isso; robô nenhum lê.
      api<ProductPage>(`/api/products?page=${page.value}&page_size=${pageSize}` +
        `&departamento=${dept}` +
        `&ocultos=esconder` +
        (buscaProdutos ? `&search=${encodeURIComponent(buscaProdutos)}` : '') +
        (filtroIntegration.value ? `&integration_id=${filtroIntegration.value}` : '') +
        (stockFilter.value === 'low' ? `&low_stock=true` : '') +
        (stockFilter.value === 'zero' ? `&zero_stock=true` : '') +
        (filtroVinculos.value ? `&vinculos=${filtroVinculos.value}` : '')),
        // 'ok' is handled client-side (filteredItems) to avoid backend changes
      api<Integration[]>('/api/integrations'),
      api<UserSettings>('/api/settings'),
      api<Segment[]>('/api/segments').catch(() => [] as Segment[]),
    ])
    if (minha !== seqProdutos) return
    produtosCarregadosDe.value = dept
    items.value = pg.items
    total.value = pg.total
    if (pg.saude) saude.value = pg.saude
    integrations.value = integ
    autoSyncEnabled.value = settings.daily_sync_enabled
    segments.value = segs
  } catch (e: any) {
    if (minha === seqProdutos) error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    if (minha === seqProdutos) loading.value = false
  }
}
if (aba.value === 'produtos') await refreshAll()
else await carregarDepartamentos()

// As contas (para os diálogos Atualizar / Vincular / Sincronizar) não podem
// depender da aba Produtos: quem entra direto em ?aba=anuncios (o /anuncios
// antigo redireciona para lá) abria os diálogos sem nenhuma conta.
async function carregarContas() {
  try {
    integrations.value = await api<Integration[]>('/api/integrations')
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  }
}
onMounted(() => {
  if (!integrations.value.length) void carregarContas()
})

// ?dept=sem com o "Sem departamento" já vazio abre o Celular; a URL acompanha,
// para a tela não pular de volta quando aparecer algo novo para classificar.
function acertarUrlDoDepartamento() {
  if (route.query.dept === 'sem' && departamento.value !== 'sem') {
    router.replace({ path: route.path, query: { ...route.query, dept: departamento.value } })
  }
}
onMounted(acertarUrlDoDepartamento)

// Trocou o departamento: página 1 do novo, sem seleção nem linhas abertas de
// antes. A aba Anúncios recarrega sozinha (o painel olha o departamento).
function aoTrocarDepartamento() {
  acertarUrlDoDepartamento()
  page.value = 1
  selected.value = new Set()
  selectedLinks.value = new Set()
  expanded.value = new Set()
  // "— sem segmento" vale em todo departamento (o reserva põe produto sem
  // segmento em Celular, Mala e Eletro); um segmento de outro departamento, não.
  if (
    filtroSegment.value &&
    filtroSegment.value !== '__none__' &&
    !segmentFilterOptions.value.some((o) => o.id === filtroSegment.value)
  ) {
    filtroSegment.value = ''
  }
  if (aba.value === 'produtos') void refreshAll()
  else produtosCarregadosDe.value = null
}
function aoTrocarAba(a: Aba) {
  if (a === 'anuncios') {
    selected.value = new Set()
    return
  }
  if (produtosCarregadosDe.value !== departamento.value) void refreshAll()
}
watch(departamento, aoTrocarDepartamento)
watch(aba, aoTrocarAba)

async function toggleAutoSync() {
  if (togglingAutoSync.value) return
  togglingAutoSync.value = true
  const next = !autoSyncEnabled.value
  try {
    await api<UserSettings>('/api/settings', {
      method: 'PATCH',
      body: { daily_sync_enabled: next },
    })
    autoSyncEnabled.value = next
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    togglingAutoSync.value = false
  }
}

const integrationById = computed(() => Object.fromEntries(integrations.value.map(i => [i.id, i])))
const blingIntegrations = computed(() => integrations.value.filter(i => i.platform === 'bling'))
const marketplaceIntegrations = computed(() => integrations.value.filter(i => i.platform !== 'bling'))

// Ordena contas por nome (alfabético, case-insensitive pt-BR, ignora espaços).
function cmpIntegrationName(a: Integration, b: Integration): number {
  return (a.name || '').trim().localeCompare((b.name || '').trim(), 'pt-BR', { sensitivity: 'base' })
}

// Contas do filtro superior agrupadas por plataforma (ordem alfabética) e, dentro
// de cada grupo, por nome (alfabético).
const integrationGroups = computed(() => {
  const groups: Record<string, Integration[]> = {}
  for (const i of integrations.value) {
    ;(groups[i.platform as string] ??= []).push(i)
  }
  return Object.keys(groups)
    .sort((a, b) => a.localeCompare(b))
    .map((platform) => ({
      platform,
      items: [...groups[platform]].sort(cmpIntegrationName),
    }))
})

// Contas de marketplace ordenadas por plataforma e, dentro dela, por nome —
// usada nos diálogos "Vincular" e "Sincronizar".
const marketplaceIntegrationsSorted = computed(() =>
  [...marketplaceIntegrations.value].sort((a, b) => {
    const p = (a.platform as string).localeCompare(b.platform as string)
    return p !== 0 ? p : cmpIntegrationName(a, b)
  }),
)

// Quem tem leitor em cada botão (o servidor monta a lista de verdade; aqui é
// só para a prévia mostrar as mesmas contas). Atualizar lê a LISTA de
// anúncios; Vincular lê os anúncios ao vivo para ligar ao produto.
const PLATAFORMAS_ATUALIZAR = ['ml', 'shopee', 'tiktok', 'magalu', 'carrefour']
const PLATAFORMAS_VINCULAR = ['ml', 'shopee', 'tiktok', 'amazon', 'magalu']
const contasVincular = computed(() =>
  marketplaceIntegrationsSorted.value.filter((i) => PLATAFORMAS_VINCULAR.includes(i.platform)))
type GrupoContas = { platform: string; label: string; nomes: string[] }
function agruparContas(lista: Integration[], ordem: string[]): GrupoContas[] {
  const grupos: Record<string, string[]> = {}
  for (const i of lista) (grupos[i.platform] ??= []).push((i.name || '').trim())
  return Object.keys(grupos)
    .sort((a, b) => (ordem.indexOf(a) + 1 || 99) - (ordem.indexOf(b) + 1 || 99) || a.localeCompare(b))
    .map((p) => ({
      platform: p,
      label: nomePlataforma(p),
      nomes: grupos[p].sort((a, b) => a.localeCompare(b, 'pt-BR', { sensitivity: 'base' })),
    }))
}
const contasAtualizarPorPlataforma = computed(() => agruparContas(
  marketplaceIntegrations.value.filter((i) => PLATAFORMAS_ATUALIZAR.includes(i.platform)), PLATAFORMAS_ATUALIZAR))
const qtdContasAtualizar = computed(() =>
  contasAtualizarPorPlataforma.value.reduce((n, g) => n + g.nomes.length, 0))
// Plataformas que ficam fora de cada botão, para a prévia dizer o porquê.
const plataformasForaDoAtualizar = computed(() => agruparContas(
  marketplaceIntegrations.value.filter((i) => !PLATAFORMAS_ATUALIZAR.includes(i.platform)), []))
const plataformasForaDoVincular = computed(() => agruparContas(
  marketplaceIntegrations.value.filter((i) => !PLATAFORMAS_VINCULAR.includes(i.platform)), []))

type MarketCol = 'shopee' | 'amazon' | 'ml_classico' | 'ml_premium' | 'tiktok' | 'magalu'

function linkCol(l: ProductLink): MarketCol | null {
  if (l.platform === 'shopee') return 'shopee'
  if (l.platform === 'amazon') return 'amazon'
  if (l.platform === 'tiktok') return 'tiktok'
  if (l.platform === 'magalu') return 'magalu'
  if (l.platform === 'ml') {
    const t = (l.listing_type || '').toLowerCase()
    // ML API values (preserved at ingestion):
    //   gold_pro / gold_premium      → Premium
    //   gold_special / free / (none) → Clássico
    if (t === 'gold_pro' || t === 'gold_premium' || t === 'ml premium') return 'ml_premium'
    return 'ml_classico'
  }
  return null
}

function linksFor(p: Product, col: MarketCol): ProductLink[] {
  return p.links.filter(l => {
    if (linkCol(l) !== col) return false
    // Filtro de conta ativo: mostra só os links da conta escolhida (o backend
    // já restringe QUAIS produtos aparecem; aqui restringimos os links da
    // linha pra não pintar as outras colunas/contas).
    if (filtroIntegration.value && l.integration_id !== filtroIntegration.value) return false
    return true
  })
}

function hasIntegrationsForCol(col: MarketCol): boolean {
  if (col === 'amazon') return integrations.value.some((i) => i.platform === 'amazon')
  if (col === 'tiktok') return integrations.value.some((i) => (i.platform as string) === 'tiktok')
  return false
}

function manualLink(_p: Product, col: MarketCol) {
  // Com uma massa rodando (ex.: o Atualizar minimizado), o diálogo mostraria o
  // andamento DELA como se fosse dele: avisa e não abre.
  if (avisarSeMassaRodando()) return
  // Backend não expõe endpoint de criação manual de link.
  // Pré-selecionamos as integrações dessa plataforma no dialog de auto-link.
  const platform = col === 'amazon' ? 'amazon' : 'tiktok'
  selectedAutoLinkIds.value = new Set(
    marketplaceIntegrations.value.filter((i) => (i.platform as string) === platform).map((i) => i.id),
  )
  activeJob.value = null
  avisoMassa.value = null
  showAutoLink.value = true
}

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))

const filteredItems = computed(() => {
  let list = items.value
  if (stockFilter.value === 'ok') {
    list = list.filter((p) => p.stock > 0 && p.stock >= p.min_stock)
  }
  if (filtroSegment.value === '__none__') {
    list = list.filter((p) => !p.segment_id)
  } else if (filtroSegment.value) {
    const want = filtroSegment.value
    const segIds = new Set<string>([want])
    // include all descendants — taxonomy may have subtypes
    let frontier = new Set<string>([want])
    while (frontier.size) {
      const next = new Set<string>()
      for (const s of segments.value) {
        if (s.parent_id && frontier.has(s.parent_id) && !segIds.has(s.id)) {
          segIds.add(s.id)
          next.add(s.id)
        }
      }
      frontier = next
    }
    list = list.filter((p) => p.segment_id && segIds.has(p.segment_id))
  }
  return list
})

const segmentsById = computed(() => Object.fromEntries(segments.value.map((s) => [s.id, s])))

// Set of segment ids that are parents (have at least one active child).
// Used to enforce "leaf-only" selection in the product picker.
const parentIds = computed(() => {
  const out = new Set<string>()
  for (const s of segments.value) {
    if (s.parent_id && s.active) out.add(s.parent_id)
  }
  return out
})

// Flattened segments with "Root / Child" labels, sorted by path — for dropdowns.
// Only LEAF segments (no active children) are returned, since a product must
// be classified to a subtype, not just a root.
const segmentOptions = computed(() => {
  const byId = segmentsById.value
  const isParent = parentIds.value
  const out: { id: string; label: string; depth: number }[] = []
  function pathOf(s: Segment): string {
    const chain: string[] = []
    let cur: Segment | undefined = s
    const seen = new Set<string>()
    while (cur && !seen.has(cur.id)) {
      seen.add(cur.id)
      chain.push(cur.name)
      cur = cur.parent_id ? byId[cur.parent_id] : undefined
    }
    return chain.reverse().join(' / ')
  }
  function depthOf(s: Segment): number {
    let d = 0
    let cur: Segment | undefined = s
    const seen = new Set<string>()
    while (cur?.parent_id && !seen.has(cur.id)) {
      seen.add(cur.id)
      d++
      cur = byId[cur.parent_id]
    }
    return d
  }
  for (const s of segments.value) {
    if (!s.active) continue
    if (isParent.has(s.id)) continue  // skip roots / non-leaf nodes
    out.push({ id: s.id, label: pathOf(s), depth: depthOf(s) })
  }
  out.sort((a, b) => a.label.localeCompare(b.label))
  return out
})

// Departamento de um segmento, pela raiz (Catálogo → Celular; raiz que não é
// departamento → 'sem') — o mesmo da API. O produto sem segmento de raiz
// conhecida a API ainda põe num departamento pelo reserva (categoria / família
// do SKU); aqui ele dá 'sem'.
function departamentoDoSegmento(segmentId: string | null): Departamento {
  if (!segmentId) return 'sem'
  const byId = segmentsById.value
  let cur: Segment | undefined = byId[segmentId]
  const seen = new Set<string>()
  while (cur?.parent_id && !seen.has(cur.id)) {
    seen.add(cur.id)
    cur = byId[cur.parent_id]
  }
  return (cur && RAIZ_PARA_DEPARTAMENTO[cur.slug]) || 'sem'
}

// Filter dropdown includes both roots and leaves (so user can filter by
// "Celular" and see all subtypes, or by a specific leaf). Desde 13/11/2026 só
// os segmentos do departamento escolhido (os outros dariam lista vazia).
const segmentFilterOptions = computed(() => {
  const byId = segmentsById.value
  function pathOf(s: Segment): string {
    const chain: string[] = []
    let cur: Segment | undefined = s
    const seen = new Set<string>()
    while (cur && !seen.has(cur.id)) {
      seen.add(cur.id)
      chain.push(cur.name)
      cur = cur.parent_id ? byId[cur.parent_id] : undefined
    }
    return chain.reverse().join(' / ')
  }
  return segments.value
    .filter((s) => s.active && departamentoDoSegmento(s.id) === departamento.value)
    .map((s) => ({ id: s.id, label: pathOf(s) }))
    .sort((a, b) => a.label.localeCompare(b.label))
})

const segPickerProductId = ref<string | null>(null)
function openSegPicker(p: Product) {
  segPickerProductId.value = segPickerProductId.value === p.id ? null : p.id
}
function closeSegPicker() {
  segPickerProductId.value = null
}

async function setProductSegment(p: Product, segmentId: string | null) {
  try {
    const updated = await api<Product>(`/api/products/${p.id}`, {
      method: 'PATCH',
      body: { segment_id: segmentId },
    })
    Object.assign(p, updated)
    closeSegPicker()
    // Mudou de departamento (ex.: classificou um de "Sem departamento"): sai
    // desta lista e os números das abas se atualizam. Sem segmento de raiz
    // conhecida quem decide é o reserva da API (categoria / família do SKU),
    // que a tela não sabe: recarrega.
    const novo = departamentoDoSegmento(updated.segment_id)
    if (novo === 'sem') {
      void refreshAll()
    } else if (novo !== departamento.value) {
      items.value = items.value.filter((x) => x.id !== p.id)
      total.value = Math.max(0, total.value - 1)
      void carregarDepartamentos()
    }
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  }
}

const showBulkSegment = ref(false)
const bulkSegmentId = ref<string>('')
async function runBulkSegment() {
  if (selected.value.size === 0) return
  try {
    await api('/api/products/bulk-segment', {
      method: 'POST',
      body: {
        product_ids: [...selected.value],
        segment_id: bulkSegmentId.value || null,
      },
    })
    showBulkSegment.value = false
    bulkSegmentId.value = ''
    selected.value = new Set()
    await refreshAll()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  }
}

function toggleExpand(id: string) {
  if (expanded.value.has(id)) expanded.value.delete(id)
  else expanded.value.add(id)
  expanded.value = new Set(expanded.value)
}

function toggleSelect(id: string) {
  if (selected.value.has(id)) selected.value.delete(id)
  else selected.value.add(id)
  selected.value = new Set(selected.value)
}

function brl(v: string | number | null | undefined) {
  if (v === null || v === undefined || v === '') return '—'
  const n = typeof v === 'number' ? v : Number(v)
  if (Number.isNaN(n)) return '—'
  return n.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
}

async function bulkDelete() {
  if (selected.value.size === 0) return
  const count = selected.value.size
  if (!confirm(`Excluir ${count} produto(s)?`)) return
  try {
    const ids = new Set(selected.value)
    await api('/api/products/bulk-delete', {
      method: 'POST',
      body: { ids: [...ids] },
    })
    // Drop deleted products locally instead of refetching the whole grid.
    items.value = items.value.filter((p) => !ids.has(p.id))
    total.value = Math.max(0, total.value - ids.size)
    selected.value = new Set()
  } catch (e: any) {
    const msg = e?.data?.detail?.code || e?.message || 'erro desconhecido'
    pushToast({ kind: 'error', title: `Falha ao excluir ${count} produto(s)`, lines: [msg] }, 15000)
  }
}

async function deleteOne(id: string) {
  if (!confirm('Excluir produto?')) return
  try {
    await api(`/api/products/${id}`, { method: 'DELETE' })
    items.value = items.value.filter((p) => p.id !== id)
    total.value = Math.max(0, total.value - 1)
  } catch (e: any) {
    const msg = e?.data?.detail?.code || e?.message || 'erro desconhecido'
    pushToast({ kind: 'error', title: 'Falha ao excluir produto', lines: [msg] }, 15000)
  }
}

// Drop links from the in-memory grid without refetching all ~4000 products
// (Item 5: the old `await refreshAll()` after every delete made the button
// feel slow/stuck). Rebuild only the touched products so Vue re-renders them.
function removeLinksLocal(ids: Set<string> | string[]) {
  const idSet = ids instanceof Set ? ids : new Set(ids)
  if (idSet.size === 0) return
  items.value = items.value.map((p) =>
    p.links.some((l) => idSet.has(l.id))
      ? { ...p, links: p.links.filter((l) => !idSet.has(l.id)) }
      : p,
  )
  for (const id of idSet) selectedLinks.value.delete(id)
  selectedLinks.value = new Set(selectedLinks.value)
}

// ── Saúde do vínculo ────────────────────────────────────────────────────────
function normSku(v?: string | null): string {
  return (v || '').replace(/\s+/g, '').toLowerCase()
}
// O anúncio está com outro SKU e ficou onde estava (SKU sem produto ou em 2+
// produtos). Os que tinham para onde ir já foram movidos sozinhos.
function skuDiferente(p: Product, l: ProductLink): boolean {
  return !!l.external_sku && !l.morto_desde && normSku(l.external_sku) !== normSku(p.sku)
}
function fmtQuando(v?: string | null): string {
  if (!v) return '—'
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? v : d.toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })
}
function dicaVinculo(p: Product, l: ProductLink): string {
  const linhas = [integrationById.value[l.integration_id]?.name?.trim() || l.platform]
  if (l.morto_desde) linhas.push(`MORTO desde ${fmtQuando(l.morto_desde)}: ${l.morto_motivo || 'anúncio acabou'}`)
  if (l.external_sku) linhas.push(`SKU no anúncio: ${l.external_sku}${skuDiferente(p, l) ? ` (produto é ${p.sku})` : ''}`)
  linhas.push(`último envio: ${fmtQuando(l.last_sync_at)} · ${l.last_sync_status}`)
  if (l.last_error) linhas.push(l.last_error)
  return linhas.join('\n')
}
// A saúde dos vínculos (faixa "Vínculos: N mortos…") é do departamento cuja
// lista está na tela. O "Remover os N mortos" saiu da tela (decisão do dono,
// 17/11/2026): para tirar os mortos da frente, o ADM usa o Organizar, que só
// esconde e dá para desfazer.

async function deleteLink(id: string) {
  if (!confirm('Remover link?')) return
  try {
    await api(`/api/product-links/${id}`, { method: 'DELETE' })
    removeLinksLocal([id])
  } catch (e: any) {
    const msg = e?.data?.detail?.code || e?.message || 'erro desconhecido'
    pushToast({ kind: 'error', title: 'Falha ao remover link', lines: [msg] }, 15000)
  }
}

function toggleSelectLink(id: string) {
  if (selectedLinks.value.has(id)) selectedLinks.value.delete(id)
  else selectedLinks.value.add(id)
  selectedLinks.value = new Set(selectedLinks.value)
}

function selectedLinkCountFor(p: Product): number {
  return p.links.reduce((n, l) => (selectedLinks.value.has(l.id) ? n + 1 : n), 0)
}

function allLinksSelected(p: Product): boolean {
  return p.links.length > 0 && p.links.every((l) => selectedLinks.value.has(l.id))
}

function toggleSelectAllLinks(p: Product) {
  const all = allLinksSelected(p)
  for (const l of p.links) {
    if (all) selectedLinks.value.delete(l.id)
    else selectedLinks.value.add(l.id)
  }
  selectedLinks.value = new Set(selectedLinks.value)
}

async function bulkDeleteLinks(p: Product) {
  const ids = p.links.filter((l) => selectedLinks.value.has(l.id)).map((l) => l.id)
  if (ids.length === 0) return
  if (!confirm(`Remover ${ids.length} vínculo(s)?`)) return
  try {
    const r = await api<{ deleted: number; deleted_ids: string[] }>(
      '/api/product-links/bulk-delete',
      { method: 'POST', body: { link_ids: ids } },
    )
    removeLinksLocal(r.deleted_ids?.length ? r.deleted_ids : ids)
  } catch (e: any) {
    const msg = e?.data?.detail?.code || e?.message || 'erro desconhecido'
    pushToast({ kind: 'error', title: `Falha ao remover ${ids.length} vínculo(s)`, lines: [msg] }, 15000)
  }
}

// ---------------------------- CSV import (Feature 1) ------------------------

function handleCsvUpload(e: Event) {
  const files = (e.target as HTMLInputElement).files
  if (!files || files.length === 0) return
  csvFile.value = files[0]
}

async function submitCsvImport() {
  if (!csvFile.value) return
  csvImporting.value = true
  csvImportError.value = null
  try {
    const formData = new FormData()
    formData.append('file', csvFile.value)
    const result = await api<{ imported: number; updated: number; errors: string[] }>(
      '/api/products/import/csv',
      { method: 'POST', body: formData },
    )
    alert(`Importados: ${result.imported}, Atualizados: ${result.updated}`)
    showImportCsv.value = false
    csvFile.value = null
    await refreshAll()
  } catch (e: any) {
    csvImportError.value = e?.data?.detail?.code || e?.message || 'erro ao importar'
  } finally {
    csvImporting.value = false
  }
}

// ---------------------------- New product (Feature 2) ----------------------

function openNewProduct() {
  newProduct.sku = ''
  newProduct.name = ''
  newProduct.cost_price = ''
  newProduct.stock = 0
  newProduct.min_stock = 0
  showNewProduct.value = true
}

async function submitNewProduct() {
  if (!newProduct.sku.trim() || !newProduct.name.trim()) {
    error.value = 'SKU e Nome são obrigatórios'
    return
  }
  creatingProduct.value = true
  try {
    await api('/api/products', {
      method: 'POST',
      body: {
        sku: newProduct.sku.trim(),
        name: newProduct.name.trim(),
        cost_price: newProduct.cost_price ? Number(newProduct.cost_price) : null,
        stock: Number(newProduct.stock || 0),
        min_stock: Number(newProduct.min_stock || 0),
      },
    })
    showNewProduct.value = false
    await refreshAll()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro ao criar produto'
  } finally {
    creatingProduct.value = false
  }
}

// ---------------------------- Sync ----------------------------------------

const syncingProduct = ref<Set<string>>(new Set())

async function syncProduct(id: string, integrationIds?: string[]) {
  syncingProduct.value.add(id)
  syncingProduct.value = new Set(syncingProduct.value)
  try {
    const job = await api<Job>(`/api/sync/product/${id}`, {
      method: 'POST',
      body: integrationIds && integrationIds.length > 0
        ? { integration_ids: integrationIds }
        : {},
    })
    // Re-fetch products so the table reflects new marketplace stock + last_sync_at.
    await refreshAll()
    // Emit toasts: one per link (sku · platform · account · qty_before -> qty_after).
    // We resolve integration name via the local map populated by loadIntegrations().
    const product = items.value.find((p) => p.id === id)
    const sku = product?.sku || id.slice(0, 8)
    const details = (job?.details || []) as Array<Record<string, any>>
    if (details.length === 0) {
      pushToast({
        kind: 'warning',
        title: `⚠ ${sku}`,
        lines: ['nenhum link sincronizado'],
      })
    } else {
      const okLines: string[] = []
      const errLines: string[] = []
      // Reconcile entries (kind=reconcile_*) come from the on-demand SKU
      // reconcile that runs before the push: they carry `kind`, not `status`,
      // so pull them out first and surface where each link moved.
      const moveLines: string[] = []
      for (const d of details) {
        const acc = d.integration_id
          ? integrationById.value[d.integration_id]?.name || d.platform
          : d.platform
        const plat = String(d.platform || '').toUpperCase()
        if (d.kind === 'reconcile_move') {
          moveLines.push(`🔀 ${plat} ${acc}: ${d.from_sku} → ${d.to_sku}`)
          continue
        }
        if (d.kind === 'reconcile_warning') {
          if (d.code === 'produto_novo_ausente') {
            moveLines.push(`⚠ SKU novo sem produto cadastrado: ${d.sku}`)
          } else if (d.code === 'sku_ambiguo') {
            moveLines.push(`⚠ SKU novo ambíguo (2+ produtos): ${d.sku}`)
          } else {
            moveLines.push(`⚠ ${d.code}: ${d.sku || ''}`)
          }
          continue
        }
        if (d.kind === 'reconcile_excedent_deleted') {
          continue // internal cleanup, not worth a toast line
        }
        if (d.status === 'ok') {
          okLines.push(
            `${plat} ${acc}: ${d.qty_before ?? '—'} → ${d.qty_after ?? '—'} ✓`,
          )
        } else if (d.status === 'skipped') {
          // Skip can mean many things — translate the backend error_code so
          // operators understand which guard fired instead of seeing a
          // misleading "estoque igual" for every skip.
          const code = String(d.error_code || '')
          let msg: string
          if (code === 'b1_guard_zero_block') {
            msg = 'bloqueado (proteção anti-zerar)'
          } else if (code.startsWith('ml_listing_')) {
            msg = `pulado (${code.replace('ml_listing_', '')})`
          } else if (code === 'bling_refresh_failed_no_push') {
            msg = 'pulado (Bling falhou)'
          } else if (code === 'platform_not_implemented') {
            msg = 'pulado (não implementado)'
          } else if (code === 'verified_skip') {
            // legacy code from the (now-removed) verify-before-send shortcut
            msg = 'pulado (estoque igual)'
          } else if (d.error_detail) {
            msg = `pulado (${String(d.error_detail).slice(0, 60)})`
          } else if (code) {
            msg = `pulado (${code})`
          } else {
            msg = 'pulado'
          }
          okLines.push(`${plat} ${acc}: ${msg}`)
        } else {
          const err = d.error_code
            ? `${d.error_code}${d.error_detail ? ': ' + d.error_detail : ''}`
            : 'erro'
          errLines.push(`${plat} ${acc}: ${err}`)
        }
      }
      const movedNote = moveLines.length ? ` · ${moveLines.length} link(s) movido(s)` : ''
      if (errLines.length === 0) {
        pushToast({
          kind: 'success',
          title: `✓ ${sku} sincronizado${movedNote}`,
          lines: [...moveLines, ...okLines],
        })
      } else if (okLines.length === 0) {
        pushToast({ kind: 'error', title: `✗ ${sku}`, lines: [...moveLines, ...errLines] })
      } else {
        pushToast({
          kind: 'warning',
          title: `${sku} sincronizado parcialmente${movedNote}`,
          lines: [...moveLines, ...okLines, ...errLines],
        })
      }
    }
  } catch (e: any) {
    const msg = e?.data?.detail?.code || e?.message || 'erro'
    error.value = msg
    pushToast({ kind: 'error', title: '✗ Erro ao sincronizar', lines: [msg] })
  } finally {
    syncingProduct.value.delete(id)
    syncingProduct.value = new Set(syncingProduct.value)
  }
}

// -------------------- Anúncio por ID (não varre a conta toda) --------------
// Colar o ID do anúncio no campo de busca e apertar Enter (ou clicar em
// "Buscar anúncio") traz SÓ aquele anúncio, com todas as variações/SKUs
// agrupados embaixo. Antes, um anúncio novo — ex.: 52 variações na Shopee —
// obrigava a rodar "Vincular Automático"/"Sincronizar Todos" da conta inteira.
type AnuncioEstado =
  | 'vinculado'
  | 'pronto'
  | 'trocado'
  | 'sem_sku'
  | 'sku_ambiguo'
  | 'sem_produto'
type AnuncioVariacao = {
  variation_id: string | null
  variacao: string | null
  sku: string | null
  estado: AnuncioEstado
  listing_type: string | null
  product_id: string | null
  produto_nome: string | null
  estoque_bling: number | null
  estoque_anuncio: number | null
  link_id: string | null
  // Só no estado "trocado": o SKU antigo que o vínculo ainda carrega.
  sku_anterior?: string | null
}
type AnuncioLookup = {
  encontrado: boolean
  motivo?: string | null
  external_id: string
  plataforma: string | null
  integration_id: string | null
  conta: string | null
  titulo: string | null
  origem: string | null
  resumo: {
    total: number
    prontos: number
    ja_vinculados: number
    sem_sku: number
    sku_ambiguo: number
    sem_produto: number
    trocados?: number
  }
  variacoes: AnuncioVariacao[]
}

const ESTADO_ANUNCIO: Record<AnuncioEstado, { label: string; cls: string }> = {
  vinculado: { label: 'já vinculado', cls: 'bg-blue-100 text-blue-700' },
  pronto: { label: 'pronto p/ vincular', cls: 'bg-green-100 text-green-700' },
  trocado: { label: 'SKU trocado — mover vínculo', cls: 'bg-purple-100 text-purple-700' },
  sem_sku: { label: 'sem SKU no anúncio', cls: 'bg-gray-100 text-gray-600' },
  sku_ambiguo: { label: 'SKU em 2+ produtos', cls: 'bg-amber-100 text-amber-700' },
  sem_produto: { label: 'SKU não existe no DaVinci', cls: 'bg-red-100 text-red-700' },
}

async function buscarAnuncio() {
  const id = search.value.trim()
  if (!id) return
  anuncioLoading.value = true
  anuncioErro.value = null
  try {
    anuncio.value = await api<AnuncioLookup>(`/api/anuncio/lookup?id=${encodeURIComponent(id)}`)
    if (!anuncio.value?.encontrado) {
      anuncioErro.value = `Nenhum anúncio ${id} nas suas contas conectadas.`
    }
  } catch (e: any) {
    anuncio.value = null
    anuncioErro.value = e?.data?.detail?.code || e?.message || 'erro ao buscar o anúncio'
  } finally {
    anuncioLoading.value = false
  }
}

function fecharAnuncio() {
  anuncio.value = null
  anuncioErro.value = null
}

// Enter no campo de busca: ID → busca o anúncio (a tabela de produtos fica
// como está); qualquer outro texto → busca normal por nome/SKU.
async function onBuscaEnter() {
  if (buscaEhIdAnuncio.value) {
    await buscarAnuncio()
    return
  }
  fecharAnuncio()
  await refreshAll()
}

async function sincronizarAnuncio() {
  const a = anuncio.value
  if (!a?.encontrado || !a.integration_id || anuncioSyncing.value) return
  anuncioSyncing.value = true
  try {
    const job = await api<Job>('/api/anuncio/sync', {
      method: 'POST',
      body: { external_id: a.external_id, integration_id: a.integration_id, vincular: true },
    })
    const details = (job?.details || []) as Array<Record<string, any>>
    // Com o retry do Bling (429) o mesmo link pode aparecer 2× nos detalhes
    // (a falha e a tentativa que deu certo) — vale a ÚLTIMA de cada um.
    const porLink = new Map<string, Record<string, any>>()
    for (const d of details) {
      if (d.kind) continue
      porLink.set(`${d.platform || ''}|${d.sku || ''}|${d.external_id || ''}`, d)
    }
    const okLines: string[] = []
    const errLines: string[] = []
    for (const d of porLink.values()) {
      const plat = String(d.platform || '').toUpperCase()
      const alvo = d.sku || d.external_id || ''
      if (d.status === 'ok') {
        okLines.push(`${plat} ${alvo}: ${d.qty_before ?? '—'} → ${d.qty_after ?? '—'} ✓`)
      } else if (d.status === 'skipped') {
        okLines.push(`${plat} ${alvo}: pulado${d.error_code ? ` (${d.error_code})` : ''}`)
      } else {
        errLines.push(`${plat} ${alvo}: ${d.error_code || 'erro'}${d.error_detail ? ': ' + d.error_detail : ''}`)
      }
    }
    const anuncioRes = (job?.result as any)?.anuncio || {}
    const criados = Number(anuncioRes.vinculos_criados || 0)
    const movidos = Number(anuncioRes.vinculos_movidos || 0)
    const removidos = Number(anuncioRes.vinculos_removidos || 0)
    const partes: string[] = []
    if (criados) partes.push(`${criados} vínculo(s) novo(s)`)
    if (movidos) partes.push(`${movidos} vínculo(s) movido(s) p/ o SKU novo`)
    if (removidos) partes.push(`${removidos} vínculo(s) de variação extinta limpo(s)`)
    const cabecalho = partes.length ? partes.join(' · ') : 'sem vínculo novo'
    const moveLines = ((anuncioRes.movimentos || []) as Array<Record<string, any>>).map(
      (m) => `🔀 ${m.variacao || m.variation_id || ''}: ${m.de_sku || '?'} → ${m.para_sku || '?'}`,
    )
    // Variações apagadas do anúncio no marketplace: o vínculo velho saiu.
    moveLines.push(
      ...((anuncioRes.remocoes || []) as Array<Record<string, any>>).map(
        (m) => `🧹 ${m.sku || m.variation_id || '?'}: variação não existe mais no anúncio — vínculo removido`,
      ),
    )
    if (errLines.length === 0) {
      pushToast({
        kind: 'success',
        title: `✓ ${a.external_id} sincronizado · ${cabecalho}`,
        lines: [...moveLines.slice(0, 10), ...okLines.slice(0, 20)],
      })
    } else {
      pushToast({
        kind: okLines.length ? 'warning' : 'error',
        title: `${a.external_id}: ${okLines.length} ok · ${errLines.length} erro(s)`,
        lines: [...moveLines.slice(0, 5), ...okLines.slice(0, 10), ...errLines.slice(0, 10)],
      })
    }
    // Tabela e painel voltam a refletir o que ficou vinculado/empurrado.
    await refreshAll()
    await buscarAnuncio()
  } catch (e: any) {
    const code = e?.data?.detail?.code || e?.message || 'erro'
    const msg = code === 'anuncio_sem_vinculos'
      ? 'Nenhuma variação deste anúncio casou com um produto do DaVinci (confira os SKUs).'
      : code
    anuncioErro.value = msg
    pushToast({ kind: 'error', title: '✗ Erro ao sincronizar o anúncio', lines: [msg] })
  } finally {
    anuncioSyncing.value = false
  }
}

// "Recarregar Vínculo": relê o SKU atual de todos os anúncios do produto em
// todas as plataformas e re-aponta os links cujo SKU mudou pra outro produto
// (repoint on-demand). Diferente do "Sincronizar" (que empurra estoque por
// conta escolhida), este corre em todas as contas do produto e foca em MOVER
// vínculos que ficaram presos no produto errado.
const reloadingLinks = ref<Set<string>>(new Set())

type ReloadMove = {
  platform: string
  external_id: string
  from_sku: string
  to_sku: string
  to_product_sku?: string | null
  to_product_name?: string | null
}
type ReloadLinksResult = {
  moved: number
  checked: number
  unreadable: number
  warnings: number
  moves: ReloadMove[]
  warnings_detail: Array<Record<string, any>>
}

async function reloadLinks(id: string) {
  reloadingLinks.value.add(id)
  reloadingLinks.value = new Set(reloadingLinks.value)
  try {
    const res = await api<ReloadLinksResult>(
      `/api/sync/product/${id}/reload-links`,
      { method: 'POST', body: {} },
    )
    // Re-fetch pra tabela refletir os links movidos + estoque novo.
    await refreshAll()
    const product = items.value.find((p) => p.id === id)
    const sku = product?.sku || id.slice(0, 8)

    const warnLines = (res.warnings_detail || []).map((w) => {
      if (w.code === 'produto_novo_ausente') return `⚠ SKU novo sem produto cadastrado: ${w.sku}`
      if (w.code === 'sku_ambiguo') return `⚠ SKU novo ambíguo (2+ produtos): ${w.sku}`
      return `⚠ ${w.code}: ${w.sku || ''}`
    })

    if (res.moved === 0) {
      pushToast({
        kind: warnLines.length ? 'warning' : 'success',
        title: `${sku}: nenhum vínculo movido`,
        lines: warnLines.length
          ? warnLines
          : [`${res.checked} anúncio(s) conferido(s) — todos já no produto certo`],
      })
    } else {
      const moveLines = res.moves.map((m) => {
        const dest = m.to_product_sku
          ? ` (${m.to_product_name || m.to_product_sku})`
          : ''
        return `🔀 ${String(m.platform).toUpperCase()} ${m.external_id}: ${m.from_sku} → ${m.to_sku}${dest}`
      })
      pushToast({
        kind: warnLines.length ? 'warning' : 'success',
        title: `✓ ${sku}: ${res.moved} vínculo(s) movido(s)`,
        lines: [...moveLines, ...warnLines],
      }, 12000)
    }
  } catch (e: any) {
    const msg = e?.data?.detail?.code || e?.message || 'erro'
    pushToast({ kind: 'error', title: '✗ Erro ao recarregar vínculo', lines: [msg] })
  } finally {
    reloadingLinks.value.delete(id)
    reloadingLinks.value = new Set(reloadingLinks.value)
  }
}

function openSyncPopover(p: Product, ev: MouseEvent) {
  // Pre-selecionar todas as integrações que têm link com este produto.
  syncPopoverSelectedIds.value = new Set(
    p.links.map((l) => l.integration_id).filter(Boolean) as string[],
  )
  syncPopoverProductId.value = p.id
  // Ancora no rect do botão clicado pra posicionar o popover teleportado.
  syncPopoverAnchor.value = (
    ev.currentTarget as HTMLElement
  ).getBoundingClientRect()
  // `capture: true` pega scroll em qualquer container (ex: o overflow-auto
  // do `.table-card` quando voltar a ser scrollável). Adiamos o
  // click-outside um tick pra não capturar o próprio clique que abriu.
  window.addEventListener('scroll', onSyncPopoverScroll, true)
  setTimeout(() => document.addEventListener('mousedown', onSyncPopoverDocClick), 0)
}

function closeSyncPopover() {
  syncPopoverProductId.value = null
  syncPopoverSelectedIds.value = new Set()
  syncPopoverAnchor.value = null
  window.removeEventListener('scroll', onSyncPopoverScroll, true)
  document.removeEventListener('mousedown', onSyncPopoverDocClick)
}

// Fecha ao rolar a PÁGINA (âncora invalida), mas NÃO quando o scroll é da
// própria lista de contas do popover (max-h-48 overflow-y-auto) — senão
// arrastar pra ver mais contas fechava o popover.
function onSyncPopoverScroll(ev: Event) {
  const el = document.getElementById('sync-popover-floating')
  if (el && el.contains(ev.target as Node)) return
  closeSyncPopover()
}

function onSyncPopoverDocClick(ev: MouseEvent) {
  const el = document.getElementById('sync-popover-floating')
  if (el && !el.contains(ev.target as Node)) closeSyncPopover()
}

onUnmounted(() => {
  window.removeEventListener('scroll', onSyncPopoverScroll, true)
  document.removeEventListener('mousedown', onSyncPopoverDocClick)
})

function toggleSyncPopoverIntegration(id: string) {
  if (syncPopoverSelectedIds.value.has(id)) syncPopoverSelectedIds.value.delete(id)
  else syncPopoverSelectedIds.value.add(id)
  syncPopoverSelectedIds.value = new Set(syncPopoverSelectedIds.value)
}

async function runSyncFromPopover(productId: string) {
  if (syncPopoverSelectedIds.value.size === 0) return
  const ids = Array.from(syncPopoverSelectedIds.value)
  closeSyncPopover()
  await syncProduct(productId, ids)
}

function linkedIntegrationsFor(p: Product): Integration[] {
  const linkedIds = new Set(p.links.map((l) => l.integration_id))
  return marketplaceIntegrations.value.filter((i) => linkedIds.has(i.id))
}

async function openSyncAllDialog() {
  if (!integrations.value.length) await carregarContas()
  // O botão Sincronizar é sempre para TODOS os produtos: zera a seleção que um
  // "Sincronizar Selecionados" cancelado tenha deixado (senão mandava só ela).
  syncAllProductIds.value = null
  // Pre-select all marketplace integrations (matches SSH default behavior).
  selectedSyncAllIds.value = new Set(marketplaceIntegrations.value.map((i) => i.id))
  forceSyncAll.value = false
  activeJob.value = null
  avisoMassa.value = null
  showSyncAll.value = true
}

function toggleSelectedSyncAll(id: string) {
  if (selectedSyncAllIds.value.has(id)) selectedSyncAllIds.value.delete(id)
  else selectedSyncAllIds.value.add(id)
  selectedSyncAllIds.value = new Set(selectedSyncAllIds.value)
}

// When the floating multi-select bar fires "Sincronizar Selecionados", this
// holds the product UUIDs for the next sync_all call so we don't conflate
// global sync with selection-scoped sync.
const syncAllProductIds = ref<string[] | null>(null)

async function runSyncAll() {
  if (selectedSyncAllIds.value.size === 0) return
  activeJob.value = null
  avisoMassa.value = null
  try {
    const r = await api<{ job_id: string }>('/api/jobs/sync-all', {
      method: 'POST',
      body: {
        integration_ids: Array.from(selectedSyncAllIds.value),
        product_ids: syncAllProductIds.value,
        include_all_stock: true,
        force: forceSyncAll.value,
      },
    })
    dialogoDoJob.value = 'sincronizar'
    startPolling(r.job_id)
    syncAllProductIds.value = null
  } catch (e: any) {
    falhaDeMassa(e)
  }
}

function openSyncSelected() {
  if (selected.value.size === 0) return
  if (avisarSeMassaRodando()) return
  syncAllProductIds.value = [...selected.value]
  selectedSyncAllIds.value = new Set(marketplaceIntegrations.value.map((i) => i.id))
  forceSyncAll.value = false
  activeJob.value = null
  avisoMassa.value = null
  showSyncAll.value = true
}

async function startRefreshBlingStock() {
  showRefreshStock.value = true
  activeJob.value = null
  try {
    const r = await api<{ job_id: string }>('/api/jobs/refresh-bling-stock', {
      method: 'POST',
    })
    startPolling(r.job_id)
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
    showRefreshStock.value = false
  }
}

function formatDetail(d: Record<string, any>): string {
  const at = typeof d.at === 'string' ? d.at.slice(11, 19) : ''
  if (d.action === 'refresh_bling' || d.action === 'update_stock') {
    const qty = d.qty_before != null || d.qty_after != null
      ? ` ${d.qty_before ?? '—'}→${d.qty_after ?? '—'}`
      : ''
    const err = d.error_code ? ` · ${d.error_code}${d.error_detail ? ': ' + d.error_detail : ''}` : ''
    return `${at} [${d.platform}] ${d.sku || d.product_id || ''} ${d.action}${qty} → ${d.status}${err}`
  }
  if (d.page != null) {
    return `${at} [bling page ${d.page}] fetched ${d.fetched ?? 0}, updated ${d.updated ?? 0}, sem-link ${d.missing_local ?? 0}`
  }
  if (d.phase === 'start') {
    return `${at} [${d.platform || 'bling'}] iniciando integração ${d.integration_id?.slice(0, 8) || ''}…`
  }
  return `${at} ${JSON.stringify(d)}`
}

// Per-line color/icon for sync log entries. Mirrors SSH's traffic-light
// scheme (green ok / amber skipped or retryable / red fatal/review).
function detailIcon(d: Record<string, any>): string {
  const s = d.status
  if (s === 'ok') return '✓'
  if (s === 'skipped') return '⊘'
  if (s === 'retryable') return '↻'
  if (s === 'fatal' || s === 'requires_review') return '✗'
  if (d.error_code) return '✗'
  return '·'
}
function detailColorClass(d: Record<string, any>): string {
  const s = d.status
  if (s === 'ok') return 'text-emerald-600'
  if (s === 'skipped') return 'text-amber-600'
  if (s === 'retryable') return 'text-amber-600'
  if (s === 'fatal' || s === 'requires_review') return 'text-red-600'
  if (d.error_code) return 'text-red-600'
  return 'text-muted-foreground'
}
// Latest SKU we observed sliding through the orchestrator log — for the
// "Sincronizando: X" line. Iterates from the tail because details is
// append-only and the most recent product is always last.
function lastProcessedSku(details: Array<Record<string, any>> | undefined): string {
  if (!details?.length) return ''
  for (let i = details.length - 1; i >= 0; i--) {
    const d = details[i]
    if (d?.sku) return d.sku
  }
  return ''
}
// Pull job.result counters with safe defaults so the badges always render.
function syncCounters(job: any): {
  ok: number
  skipped: number
  retryable: number
  fatal: number
  requires_review: number
  total_links: number
} {
  const r = (job?.result || {}) as Record<string, any>
  return {
    ok: r.ok ?? r.synced ?? 0,
    skipped: r.skipped ?? r.skippedVerified ?? 0,
    retryable: r.retryable ?? 0,
    fatal: r.fatal ?? r.errors ?? r.failed ?? 0,
    requires_review: r.requires_review ?? 0,
    total_links: r.total_links ?? job?.total ?? 0,
  }
}
// Produtos que deram erro inteiro no Sincronizar (antes sumiam calados).
function falhasProduto(job: any): number {
  return Number((job?.result as any)?.falhas_produto ?? 0) || 0
}
// Quais (os primeiros SKUs, que o backend guarda no resultado).
function falhasAmostra(job: any): string[] {
  const a = (job?.result as any)?.falhas_amostra
  return Array.isArray(a) ? a : []
}
// % da barra: o total do job conta vínculos mortos que não são processados, e o
// processado pode passar dele — limita em 100.
function pct(feito: number | null | undefined, total: number | null | undefined): number {
  const t = Number(total) || 0
  if (t <= 0) return 0
  return Math.min(100, Math.max(0, Math.round(((Number(feito) || 0) / t) * 100)))
}
// Final summary box color. Green if no failures, amber if any
// retryable/skipped without fatal, red if any fatal/review.
function syncSummaryClass(job: any): string {
  if (job?.status === 'failed') return 'border-red-300 bg-red-50 text-red-800'
  const c = syncCounters(job)
  if (c.fatal > 0 || c.requires_review > 0 || falhasProduto(job) > 0)
    return 'border-red-300 bg-red-50 text-red-800'
  if (c.skipped > 0 || c.retryable > 0)
    return 'border-amber-300 bg-amber-50 text-amber-800'
  return 'border-emerald-300 bg-emerald-50 text-emerald-800'
}
// Tail slice — render the last N entries to keep DOM small even on long runs.
function tailDetails(details: Array<Record<string, any>> | undefined, n: number) {
  if (!details?.length) return []
  return details.slice(Math.max(0, details.length - n))
}

// Auto-link helpers: surface the marketplace currently being scanned and a
// running tally aggregated per platform from the per-integration entries.
function autoLinkCurrent(details: Array<Record<string, any>> | undefined): string {
  const last = details?.at?.(-1)
  if (!last) return ''
  const plat = last.platform ? String(last.platform).toUpperCase() : ''
  const name = last.integration_name || ''
  return name ? `${plat}: ${name}` : plat
}
type MudancaVinculo = {
  acao: 'movido' | 'criado'
  conta?: string | null
  anuncio?: string | null
  variacao?: string | null
  sku_anuncio?: string | null
  de?: string | null
  para?: string | null
}
// Resumo do Vincular (o job dele e o bloco `vincular` do Atualizar têm o
// mesmo formato).
function vincularTotais(res: any): {
  created: number
  movidos: number
  sku_atualizados: number
  mortos: number
  revividos: number
  mortos_nao_criados: number
  already: number
  not_found: number
  sku_vazio: number
  sku_ambiguo: number
  pending: number
} {
  const r = (res || {}) as Record<string, any>
  return {
    created: r.created ?? 0,
    // anúncio que trocou de SKU e teve o vínculo movido para o produto novo
    movidos: r.repointed ?? 0,
    // vínculo que ficou onde estava mas guardava o SKU velho do anúncio
    sku_atualizados: r.sku_atualizados ?? 0,
    mortos: r.mortos ?? 0,
    revividos: r.revividos ?? 0,
    // anúncio já encerrado/excluído no marketplace: não vira vínculo novo
    mortos_nao_criados: r.mortos_nao_criados ?? 0,
    already: r.already_present ?? 0,
    not_found: r.not_found ?? 0,
    sku_vazio: r.sku_vazio ?? 0,
    sku_ambiguo: r.sku_ambiguo ?? 0,
    // Contas que esgotaram os retries (ITEM 1). `failed_integrations` é mantido
    // no backend como sinônimo; preferimos `pending_integrations` quando existe.
    pending: r.pending_integrations ?? r.failed_integrations ?? 0,
  }
}
function autoLinkTotals(job: any) {
  return vincularTotais(job?.result)
}
function fmtN(n: number | null | undefined): string {
  return (Number(n) || 0).toLocaleString('pt-BR')
}
// Linhas do resumo do Vincular, em português simples (só o que não é zero,
// menos os criados, que aparecem sempre).
function vincularLinhaPrincipal(res: any): string {
  const t = vincularTotais(res)
  const partes = [`${fmtN(t.created)} vínculo(s) criado(s)`]
  if (t.movidos) partes.push(`${fmtN(t.movidos)} movido(s) para o SKU novo do anúncio`)
  if (t.sku_atualizados) partes.push(`${fmtN(t.sku_atualizados)} SKU(s) atualizado(s) no DaVinci`)
  if (t.mortos) partes.push(`${fmtN(t.mortos)} marcado(s) como morto(s)`)
  if (t.revividos) partes.push(`${fmtN(t.revividos)} voltaram a viver`)
  return partes.join(' · ')
}
function vincularLinhaDetalhe(res: any): string {
  const t = vincularTotais(res)
  const partes: string[] = []
  if (t.mortos_nao_criados) partes.push(`${fmtN(t.mortos_nao_criados)} não criado(s) porque o anúncio já está encerrado ou excluído`)
  partes.push(`${fmtN(t.already)} já existentes`)
  partes.push(`${fmtN(t.not_found)} sem produto no DaVinci`)
  if (t.sku_vazio) partes.push(`${fmtN(t.sku_vazio)} sem SKU no anúncio`)
  if (t.sku_ambiguo) partes.push(`${fmtN(t.sku_ambiguo)} com SKU duplicado no cadastro`)
  return partes.join(' · ')
}
// O que o Vincular acertou na aba Anúncios (o produto mostrado em cada linha).
function vincularLinhaLista(res: any): string | null {
  const r = (res || {}) as Record<string, any>
  if (r.lista_anuncios_erro) return `Aba Anúncios não foi acertada desta vez: ${r.lista_anuncios_erro}`
  const l = r.lista_anuncios
  if (!l) return null
  return `Aba Anúncios: ${fmtN(l.passou_a_ter_produto)} passaram a ter produto · ${fmtN(l.trocou_produto)} trocaram de produto`
}
function vincularMudancas(res: any): MudancaVinculo[] {
  const m = (res as any)?.mudancas
  return Array.isArray(m) ? m : []
}
function vincularSkusSemProduto(res: any): string[] {
  const s = (res as any)?.skus_sem_produto
  return Array.isArray(s) ? s : []
}
function textoMudanca(m: MudancaVinculo): string {
  const onde = [m.anuncio, m.variacao].filter(Boolean).join('/')
  const sufixo = onde ? ` (${onde})` : ''
  if (m.acao === 'movido') return `movido ${m.de || '?'} → ${m.para || m.sku_anuncio || '?'}${sufixo}`
  return `criado ${m.para || m.sku_anuncio || '?'}${sufixo}`
}
function autoLinkBreakdown(details: Array<Record<string, any>> | undefined, res?: any): Array<{
  integration: string
  platform: string
  created: number
  already: number
  not_found: number
  sku_vazio: number
  sku_ambiguo: number
  sku_ambiguo_sample: string[]
  result: string
  bad: boolean
  error: string | null
  mudancas: MudancaVinculo[]
}> {
  const doResumo = vincularMudancas(res)
  return (details || []).map((d: any) => {
    const integration = d.integration_name || (d.integration_id || '').slice(0, 8)
    return {
      integration,
      platform: d.platform || '',
      created: d.created ?? 0,
      already: d.already_present ?? 0,
      not_found: d.not_found ?? 0,
      sku_vazio: d.sku_vazio ?? 0,
      sku_ambiguo: d.sku_ambiguo ?? 0,
      sku_ambiguo_sample: d.sku_ambiguo_sample ?? [],
      result: d.result || 'ok',
      // `pending` (retries esgotados) e o legado `failed` renderizam como aviso.
      bad: d.result === 'pending' || d.result === 'failed',
      error: d.error || null,
      // Movidos/criados desta conta (até 50): os do detalhe da conta ou, se não
      // vierem nele, os do resumo com o nome da conta.
      mudancas: Array.isArray(d.mudancas)
        ? d.mudancas
        : doResumo.filter((m) => (m.conta || '').trim() === String(integration).trim()),
    }
  })
}
function autoLinkSummaryClass(job: any): string {
  if (job?.status === 'failed') return 'border-red-300 bg-red-50 text-red-800'
  const t = autoLinkTotals(job)
  if (t.pending > 0) return 'border-amber-300 bg-amber-50 text-amber-800'
  return 'border-emerald-300 bg-emerald-50 text-emerald-800'
}

// ---------------------------- Bling import ----------------------------------

const importIntegration = ref<string>('')
const importPreview = ref<BlingPreviewItem[]>([])
const importSelected = ref<Set<number>>(new Set())
const importLoading = ref(false)
const importResult = ref<{ imported: number; updated: number; skipped_no_sku: number[] } | null>(null)

async function openImport() {
  importIntegration.value = blingIntegrations.value[0]?.id || ''
  importPreview.value = []
  importSelected.value = new Set()
  importResult.value = null
  showImport.value = true
  if (importIntegration.value) await loadPreview()
}

async function loadPreview() {
  if (!importIntegration.value) return
  importLoading.value = true
  // Backend now paginates internally and returns the full catalog in one
  // call (SSH parity) — UI no longer drives page-by-page navigation.
  try {
    const r = await api<{ items: BlingPreviewItem[] }>(
      `/api/products/preview/bling?integration_id=${importIntegration.value}`,
    )
    importPreview.value = r.items
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    importLoading.value = false
  }
}

async function runImport() {
  if (!importIntegration.value || importSelected.value.size === 0) return
  importLoading.value = true
  try {
    importResult.value = await api('/api/products/import/bling', {
      method: 'POST',
      body: {
        integration_id: importIntegration.value,
        bling_product_ids: [...importSelected.value],
      },
    })
    await refreshAll()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    importLoading.value = false
  }
}

const importSearch = ref('')
const filteredPreview = computed(() => {
  const q = importSearch.value.trim().toLowerCase()
  if (!q) return importPreview.value
  return importPreview.value.filter(
    (r) =>
      (r.sku || '').toLowerCase().includes(q) ||
      (r.name || '').toLowerCase().includes(q),
  )
})
// Selectable = has SKU AND not yet imported. Already-imported rows render
// disabled in the UI, so they cannot be added to the selection.
const selectablePreview = computed(() =>
  filteredPreview.value.filter((r) => !!r.sku && !r.already_imported),
)
const allPageSelected = computed(
  () =>
    selectablePreview.value.length > 0 &&
    selectablePreview.value.every((r) => importSelected.value.has(r.bling_product_id)),
)
const somePageSelected = computed(
  () =>
    selectablePreview.value.some((r) => importSelected.value.has(r.bling_product_id)) &&
    !allPageSelected.value,
)
function toggleAllOnPage() {
  const next = new Set(importSelected.value)
  if (allPageSelected.value) {
    for (const r of selectablePreview.value) next.delete(r.bling_product_id)
  } else {
    for (const r of selectablePreview.value) next.add(r.bling_product_id)
  }
  importSelected.value = next
}
const importStats = computed(() => {
  const total = importPreview.value.length
  const imported = importPreview.value.filter((r) => r.already_imported).length
  const novos = importPreview.value.filter((r) => !r.already_imported && !!r.sku).length
  return { total, imported, novos, selecionados: importSelected.value.size }
})

// ---------------------------- Auto-link ------------------------------------

const activeJob = ref<Job | null>(null)
// Detail log lives outside `activeJob` in a shallowRef: we reassign the array
// to trigger a (shallow) update and never deep-track its entries — that deep
// reactivity over a growing array is what used to freeze the page. Fed
// incrementally by fetchDetailDelta via the `after` cursor.
const jobDetails = shallowRef<Array<Record<string, any>>>([])
let lastDetailId = 0
let pollHandle: number | null = null

// Cross-tab/reload: além do `activeJob` local (job iniciado NESTA aba), pergunta
// ao backend se HÁ um massa em andamento (outra aba/cliente ou o daily_sync).
// Assim os botões de massa ficam desabilitados p/ todos enquanto um massa roda,
// não só na aba que o disparou. O gate real (409) mora no backend; isto é UX.
const massSyncActiveRemote = ref(false)
let massActiveHandle: number | null = null

// O que está rodando (vem do /api/sync/active e do 409). Desde 17/11/2026 a
// trava é GLOBAL: um Atualizar, Vincular ou Sincronizar de qualquer pessoa, a
// varredura das 10h ou a sincronização da noite seguram os botões de todos.
type MassaInfo = {
  job_id?: string | null
  tipo?: string | null
  type?: string | null
  status?: string | null
  gatilho?: string | null
  quem?: string | null
  desde?: string | null
  // nome pronto que o servidor manda ("a varredura automática de vínculos")
  rotulo?: string | null
  // a massa é de quem está olhando (o /api/sync/active diz): ao abrir a página
  // de novo, volta a acompanhar o andamento dela
  meu?: boolean
}
const massaAtiva = ref<MassaInfo | null>(null)
// Aviso dentro do diálogo aberto (409 / sem contas), para a pessoa ver ali
// mesmo por que nada começou.
const avisoMassa = ref<string | null>(null)
const NOME_DO_GATILHO: Record<string, string> = {
  botao_atualizar: 'um Atualizar',
  botao_vincular: 'um Vincular',
  varredura_diaria: 'a varredura automática de vínculos',
  daily_sync: 'a sincronização automática da noite',
}
const NOME_DO_TIPO: Record<string, string> = {
  sync_all: 'um Sincronizar',
  auto_link: 'um Vincular',
  import_listings: 'um Atualizar',
}
// Os automáticos rodam em nome do dono: "disparado por" confundiria.
const GATILHOS_AUTOMATICOS = new Set(['varredura_diaria', 'daily_sync'])
function fmtHoraMassa(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const hora = d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
  const hoje = new Date().toDateString() === d.toDateString()
  return hoje ? hora : `${d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })} ${hora}`
}
function descreverMassa(info: MassaInfo | null | undefined, curto = false): string {
  const i = info || {}
  const tipo = i.tipo || i.type || ''
  const oQue = i.rotulo || (i.gatilho && NOME_DO_GATILHO[i.gatilho]) || NOME_DO_TIPO[tipo] || 'outra tarefa em massa'
  // Sem tipo = a trava curta de dois cliques ao mesmo tempo ("outro disparo em
  // andamento"): o rótulo já diz tudo.
  const soRotulo = !tipo && !!i.rotulo
  let texto = curto ? `Rodando agora: ${oQue}` : soRotulo ? `Já tem ${oQue}` : `Já tem ${oQue} rodando`
  if (i.quem && !(i.gatilho && GATILHOS_AUTOMATICOS.has(i.gatilho))) texto += `, disparado por ${i.quem}`
  const desde = fmtHoraMassa(i.desde)
  if (desde) texto += `, desde ${desde}`
  return curto ? texto : `${texto}. Espere terminar e tente de novo.`
}

async function refreshMassActive() {
  try {
    const r = await api<{ active: boolean } & MassaInfo>('/api/sync/active')
    massSyncActiveRemote.value = !!r.active
    massaAtiva.value = r.active ? r : null
  } catch {
    /* silencioso — quem garante a exclusividade é o 409 do backend */
  }
}

// While a sync-all / auto-link job is running, disable the buttons that would
// launch another one (the dialogs already hide their run button once a job is
// active; this covers the header buttons). Individual per-SKU sync NÃO é
// bloqueado (exclusividade "só outro massa").
const jobActive = computed(
  () =>
    massSyncActiveRemote.value ||
    (activeJob.value != null &&
      (activeJob.value.status === 'running' || activeJob.value.status === 'pending')),
)

// Trata o 409 "sync_already_running": marca massa ativo (desabilita botões) e
// mostra o que está rodando, quem disparou e desde quando — no aviso da página
// (vale nas duas abas) e dentro do diálogo. Devolve true se era 409 (o caller
// não sobrescreve).
function handleMassBusy(e: any): boolean {
  const d = e?.data?.detail
  if (d?.code === 'sync_already_running' || e?.status === 409 || e?.statusCode === 409) {
    massSyncActiveRemote.value = true
    const info = d && typeof d === 'object' ? (d as MassaInfo) : null
    if (info) massaAtiva.value = info
    const msg = descreverMassa(info)
    error.value = msg
    avisoMassa.value = msg
    return true
  }
  return false
}
// Erro ao disparar Atualizar / Vincular / Sincronizar.
function falhaDeMassa(e: any) {
  if (handleMassBusy(e)) return
  const d = e?.data?.detail
  const code = typeof d === 'string' ? d : d?.code
  const msg = code === 'sem_contas'
    ? 'Nenhuma conta de marketplace para isso (as suas estão arquivadas ou não têm leitor).'
    : code === 'forbidden' || code === 'admin_only' || e?.status === 403
      ? 'Você não tem permissão para isso.'
      : code || e?.message || 'erro'
  error.value = msg
  avisoMassa.value = msg
}

// Qual diálogo disparou o job que está rodando (para "ver andamento" reabrir
// o certo depois de Minimizar: o Atualizar leva ~15–25 min).
const dialogoDoJob = ref<'atualizar' | 'vincular' | 'sincronizar' | null>(null)
// O job terminou com o diálogo fechado (a pessoa minimizou — o Atualizar diz
// "pode fechar esta janela"): fica um aviso na página com o resumo e
// "ver resultado" até ela fechar. Antes o resultado sumia sem aviso.
const fimDeMassa = ref<{ texto: string; tom: 'ok' | 'aviso' | 'erro' } | null>(null)
const jobRodando = computed(() =>
  activeJob.value != null && (activeJob.value.status === 'running' || activeJob.value.status === 'pending'))
const andamentoAberto = computed(() =>
  dialogoDoJob.value != null &&
  activeJob.value != null &&
  (jobRodando.value || fimDeMassa.value != null) &&
  !showAtualizar.value && !showAutoLink.value && !showSyncAll.value)
function dialogoAberto(): boolean {
  if (dialogoDoJob.value === 'atualizar') return showAtualizar.value
  if (dialogoDoJob.value === 'vincular') return showAutoLink.value
  if (dialogoDoJob.value === 'sincronizar') return showSyncAll.value
  return false
}
function verAndamento() {
  fimDeMassa.value = null
  if (dialogoDoJob.value === 'atualizar') showAtualizar.value = true
  else if (dialogoDoJob.value === 'vincular') showAutoLink.value = true
  else if (dialogoDoJob.value === 'sincronizar') showSyncAll.value = true
}
// Abrir outro diálogo de massa (Sincronizar Selecionados, "+ vincular" da
// célula) com uma massa rodando: avisa o que está rodando e não abre.
function avisarSeMassaRodando(): boolean {
  if (!jobActive.value) return false
  const msg = massaAtiva.value
    ? descreverMassa(massaAtiva.value)
    : 'Já tem uma tarefa em massa rodando. Espere terminar e tente de novo.'
  error.value = msg
  return true
}
// Uma linha para o aviso da página quando o job termina com o diálogo fechado.
function resumoDoFim(job: Job): { texto: string; tom: 'ok' | 'aviso' | 'erro' } {
  const qual = dialogoDoJob.value === 'atualizar' ? 'Atualizar' : dialogoDoJob.value === 'vincular' ? 'Vincular' : 'Sincronizar'
  if (job.status !== 'succeeded') {
    const motivo = jobInterrompido(job) ? 'interrompido no meio (o que já foi gravado ficou)' : (job.error || 'falhou')
    return { texto: `${qual} parou: ${motivo}. Clique em ${qual} de novo.`, tom: 'erro' }
  }
  if (dialogoDoJob.value === 'atualizar') {
    const a = atualizarResultado(job).anuncios || {}
    const pelaFlex = atualizarContasLidas.value.filter((d) => d.resultado === 'pulada' && /robô do Flex/.test(d.erro || '')).length
    const partes = [`${fmtN(a.ok)} de ${fmtN(a.contas)} contas lidas`]
    if (pelaFlex) partes.push(`${fmtN(pelaFlex)} pulada(s) pelo robô do Flex — tente de novo em alguns minutos`)
    const outrasPuladas = (a.puladas || 0) - pelaFlex
    if (outrasPuladas > 0) partes.push(`${fmtN(outrasPuladas)} pulada(s)`)
    if (a.falhas) partes.push(`${fmtN(a.falhas)} com falha`)
    if (vincularIncompleto(job)) partes.push('o Vincular do fim não terminou — rode o Vincular')
    const tom = atualizarSummaryClass(job).includes('amber') ? 'aviso' : 'ok'
    return { texto: `Atualizar terminou: ${partes.join(' · ')}.`, tom }
  }
  if (dialogoDoJob.value === 'vincular') {
    const t = vincularTotais(job.result)
    return {
      texto: `Vincular terminou: ${vincularLinhaPrincipal(job.result)}${t.pending ? ` · ${fmtN(t.pending)} conta(s) pendente(s)` : ''}.`,
      tom: t.pending ? 'aviso' : 'ok',
    }
  }
  const c = syncCounters(job)
  const falhas = falhasProduto(job)
  return {
    texto: `Sincronizar terminou: ${fmtN(c.ok)} sincronizado(s)${c.fatal ? ` · ${fmtN(c.fatal)} com erro` : ''}${falhas ? ` · ${fmtN(falhas)} produto(s) com erro` : ''}.`,
    tom: syncSummaryClass(job).includes('red') ? 'erro' : syncSummaryClass(job).includes('amber') ? 'aviso' : 'ok',
  }
}
// Job cortado por publicação/reinício (o backend grava esse texto).
function jobInterrompido(job: any): boolean {
  return job?.status === 'failed' && /^interrompido/.test(String(job?.error || ''))
}

async function openAutoLinkDialog() {
  if (!integrations.value.length) await carregarContas()
  selectedAutoLinkIds.value = new Set(contasVincular.value.map((i) => i.id))
  activeJob.value = null
  avisoMassa.value = null
  showAutoLink.value = true
}

function toggleSelectedAutoLink(id: string) {
  if (selectedAutoLinkIds.value.has(id)) selectedAutoLinkIds.value.delete(id)
  else selectedAutoLinkIds.value.add(id)
  selectedAutoLinkIds.value = new Set(selectedAutoLinkIds.value)
}

async function runAutoLink() {
  if (selectedAutoLinkIds.value.size === 0) return
  activeJob.value = null
  avisoMassa.value = null
  try {
    const r = await api<{ job_id: string }>('/api/jobs/auto-link', {
      method: 'POST',
      body: {
        integration_ids: Array.from(selectedAutoLinkIds.value),
      },
    })
    dialogoDoJob.value = 'vincular'
    startPolling(r.job_id)
  } catch (e: any) {
    falhaDeMassa(e)
  }
}

// ---------------------------- Atualizar ------------------------------------
// Lê os produtos novos do Bling e as listas de anúncios dos marketplaces e,
// no fim, roda o Vincular sozinho (segurando o robô do Flex até terminar).
// Só grava no DaVinci. As contas quem monta é o servidor (corpo vazio).
async function abrirAtualizar() {
  if (!integrations.value.length) await carregarContas()
  activeJob.value = null
  avisoMassa.value = null
  showAtualizar.value = true
}
async function runAtualizar() {
  activeJob.value = null
  avisoMassa.value = null
  try {
    const r = await api<{ job_id: string }>('/api/produtos/atualizar', { method: 'POST', body: {} })
    dialogoDoJob.value = 'atualizar'
    startPolling(r.job_id)
  } catch (e: any) {
    falhaDeMassa(e)
  }
}
// Uma linha por conta (a mais nova, se a rodada foi refeita pelo robô).
const atualizarContasLidas = computed(() => {
  const porConta = new Map<string, Record<string, any>>()
  for (const d of jobDetails.value) {
    if (d.etapa === 'conta') porConta.set(String(d.integration_id ?? d.id), d)
  }
  return [...porConta.values()]
})
// Fases que o backend marca (etapa 'fase'): a última diz o que está fazendo.
const atualizarFases = computed(() => jobDetails.value.filter((d) => d.etapa === 'fase'))
const atualizarVincularProgresso = computed(() => {
  const total = atualizarFases.value
    .filter((d) => d.fase === 'vincular')
    .reduce((n, d) => n + (Number(d.contas) || 0), 0)
  const feitas = jobDetails.value.filter((d) => d.etapa === 'vincular').length
  return { feitas: Math.min(feitas, total), total }
})
const atualizarTotalContas = computed(() => {
  const ids = (activeJob.value?.payload as any)?.integration_ids
  return Array.isArray(ids) ? ids.length : Math.max(0, (activeJob.value?.total ?? 1) - 1)
})
const atualizarBlingLido = computed(() =>
  jobDetails.value.some((d) => d.etapa === 'bling') || !!(activeJob.value?.result as any)?.bling)
// O que está fazendo agora (as etapas não têm ordem fixa entre Bling e contas).
const atualizarEtapa = computed(() => {
  const j = activeJob.value
  if (!j) return ''
  if (j.status === 'pending') return 'Na fila, esperando o robô começar…'
  if (!atualizarBlingLido.value) return 'Lendo os produtos novos do Bling…'
  const fase = atualizarFases.value.at(-1)?.fase
  if (fase === 'esperando_flex') return 'Esperando o robô do Flex terminar a rodada dele (até 10 min)…'
  if (fase === 'vincular') {
    const v = atualizarVincularProgresso.value
    return `Ligando os anúncios aos produtos (Vincular): ${fmtN(v.feitas)} de ${fmtN(v.total)} contas…`
  }
  if (fase === 'listas_flex') return 'Lendo as listas do Mercado Livre e da Shopee…'
  return 'Lendo as listas de anúncios…'
})
function atualizarResultado(job: any): Record<string, any> {
  return (job?.result || {}) as Record<string, any>
}
const MOTIVO_BLING_PULADO: Record<string, string> = {
  rodada_automatica_em_andamento: 'a rodada automática de 15 min estava rodando (ela cria os mesmos produtos)',
}
function atualizarBlingTexto(job: any): { pulado: string | null; erro: string | null; linha: string; skus: string[] } {
  const r = atualizarResultado(job)
  const det = jobDetails.value.find((d) => d.etapa === 'bling') || {}
  const b = (r.bling || det || {}) as Record<string, any>
  const pulado = b.pulado
    ? (typeof b.pulado === 'string' ? (MOTIVO_BLING_PULADO[b.pulado] || b.pulado) : 'pulado')
    : null
  return {
    pulado,
    erro: b.erro || null,
    linha: `${fmtN(b.lidos)} lidos · ${fmtN(b.faltando)} faltando no DaVinci · ${fmtN(b.criados)} criados` +
      (b.falhas ? ` · ${fmtN(b.falhas)} com falha` : ''),
    skus: Array.isArray(b.skus_criados) ? b.skus_criados : [],
  }
}
function atualizarContaIcone(d: Record<string, any>): string {
  if (d.resultado === 'ok') return '✓'
  if (d.resultado === 'pulada') return '⊘'
  return '⚠'
}
function atualizarContaTexto(d: Record<string, any>): string {
  if (d.resultado === 'pulada') return `pulada${d.erro ? ` — ${d.erro}` : ''}${d.dica ? ` (${d.dica})` : ''}`
  if (d.resultado !== 'ok') return `falhou${d.erro ? ` — ${d.erro}` : ''}${d.dica ? ` · ${d.dica}` : ''}`
  let t = `${fmtN(d.novos)} novos · ${fmtN(d.atualizados)} atualizados`
  if (d.ignorados) t += ` · ${fmtN(d.ignorados)} ignorados (encerrados que o DaVinci não conhecia)`
  if (d.leitura_completa === false) t += ' · leitura incompleta (o marketplace não mandou tudo; rode de novo depois)'
  return t
}
function atualizarSemLeitura(job: any): string[] {
  const s = atualizarResultado(job).sem_leitura
  return Array.isArray(s) ? s.map((p: string) => nomePlataforma(p)) : []
}
// "Sem leitura da lista": só a Amazon tem os vínculos lidos no Vincular; Temu
// e outras não têm leitor nenhum.
function atualizarSemLeituraTexto(job: any): string | null {
  const s = atualizarResultado(job).sem_leitura
  const lista: string[] = Array.isArray(s) ? s : []
  if (!lista.length) return null
  const outras = lista.filter((pl) => pl !== 'amazon')
  const partes: string[] = []
  if (lista.includes('amazon')) partes.push('os vínculos da Amazon são lidos no Vincular')
  if (outras.length) partes.push(`${outras.map((pl) => nomePlataforma(pl)).join('/')}: sem leitor`)
  return `Sem leitura da lista: ${lista.map((pl) => nomePlataforma(pl)).join(', ')} (${partes.join('; ')}).`
}
// O Vincular do fim (a proteção do Flex) quebrou ou deixou conta pendente.
function vincularIncompleto(job: any): boolean {
  const r = atualizarResultado(job)
  return !!r.vincular_erro || vincularTotais(r.vincular).pending > 0
}
const atualizarVincularPendentes = computed(() =>
  jobDetails.value.filter((d) => d.etapa === 'vincular' && d.result === 'pending'))
// O erro do job em texto para a pessoa (o 'Vincular incompleto' já aparece no
// bloco do Vincular; o interrompido tem aviso próprio).
function atualizarErroTexto(job: any): string | null {
  const e = job?.error
  if (!e || jobInterrompido(job) || /^Vincular incompleto/.test(e)) return null
  return e
}
function atualizarSummaryClass(job: any): string {
  if (job?.status === 'failed') return 'border-red-300 bg-red-50 text-red-800'
  const a = atualizarResultado(job).anuncios || {}
  const flex = atualizarResultado(job).flex || {}
  if (a.falhas || a.puladas || flex.conseguiu === false || vincularIncompleto(job)) {
    return 'border-amber-300 bg-amber-50 text-amber-800'
  }
  return 'border-emerald-300 bg-emerald-50 text-emerald-800'
}

// ---------------------------- Organizar (só ADM) ---------------------------
// Esconde da TELA (Produtos e Anúncios) o que não vale mais. Nada é apagado,
// nada muda no Bling nem nos marketplaces, o estoque continua sendo enviado.
// A prévia fica guardada no servidor (token de 30 min): o "Ocultar" grava só o
// que foi mostrado e ainda vale.
type OrgChave = {
  chave: string
  tabela: string
  rotulo: string
  explicacao: string
  quantidade: number
  marcado_por_padrao: boolean
}
type OrgLote = {
  lote_id: string
  oculto_em: string
  oculto_por_nome: string | null
  quantidade: number
  por_chave: Record<string, number>
}
type OrgPrevia = {
  token: string
  expira_em: string
  chaves: OrgChave[]
  ocultos: { total: number; por_chave: Record<string, number> }
  lotes: OrgLote[]
}
type OrgItem = {
  marcador_id?: string | null
  registro_id: string
  sku: string | null
  nome: string | null
  conta: string | null
  anuncio: string | null
  variacao: string | null
  sku_anuncio: string | null
  motivo: string | null
  data: string | null
}
type OrgItens = { itens: OrgItem[]; total: number; pagina: number; por_pagina: number }
type OrgAplicado = {
  lote_id: string | null // null quando nada foi escondido
  ocultados: number
  por_chave: Record<string, number>
  sairam_desde_previa: number
}
const organizarAba = ref<'organizar' | 'ocultos'>('organizar')
const orgPrevia = ref<OrgPrevia | null>(null)
const orgCarregando = ref(false)
const orgErro = ref<string | null>(null)
const orgMarcadas = ref<Set<string>>(new Set())
const orgLista = ref<{ chave: string; ocultos: boolean; pagina: number; dados: OrgItens | null; carregando: boolean } | null>(null)
let seqOrgLista = 0
const orgAplicando = ref(false)
const orgResultado = ref<OrgAplicado | null>(null)
const orgReexibindo = ref(false)

function orgRotulo(chave: string): string {
  return orgPrevia.value?.chaves.find((c) => c.chave === chave)?.rotulo || chave
}
// Total exato do botão "Ocultar N itens": só as caixas marcadas que têm item.
const orgChavesParaOcultar = computed(() =>
  (orgPrevia.value?.chaves || []).filter((c) => orgMarcadas.value.has(c.chave) && c.quantidade > 0))
const orgTotalMarcado = computed(() => orgChavesParaOcultar.value.reduce((n, c) => n + c.quantidade, 0))
const orgLoteDoResultado = computed(() =>
  orgPrevia.value?.lotes.find((l) => l.lote_id === orgResultado.value?.lote_id) || null)
function orgPorChaveTexto(porChave: Record<string, number> | null | undefined): string {
  return Object.entries(porChave || {})
    .filter(([, n]) => n)
    .map(([k, n]) => `${orgRotulo(k)}: ${fmtN(n)}`)
    .join(' · ')
}
function orgErroTexto(e: any): string {
  const d = e?.data?.detail
  const code = typeof d === 'string' ? d : d?.code
  if (code === 'admin_only' || e?.status === 403) return 'Só o administrador pode organizar.'
  return code || e?.message || 'erro'
}

async function abrirOrganizar() {
  showOrganizar.value = true
  organizarAba.value = 'organizar'
  orgResultado.value = null
  orgLista.value = null
  orgErro.value = null
  await carregarPreviaOrganizar(true)
}
// padrao=true: as caixas voltam a vir marcadas como o servidor sugere; senão
// fica o que a pessoa marcou (das chaves que ainda existem).
async function carregarPreviaOrganizar(padrao = false) {
  orgCarregando.value = true
  try {
    const p = await api<OrgPrevia>('/api/produtos/organizar/previa')
    const antes = orgMarcadas.value
    orgMarcadas.value = new Set(
      p.chaves.filter((c) => (padrao ? c.marcado_por_padrao : antes.has(c.chave))).map((c) => c.chave))
    orgPrevia.value = p
  } catch (e: any) {
    orgErro.value = orgErroTexto(e)
  } finally {
    orgCarregando.value = false
  }
}
function alternarChaveOrganizar(chave: string) {
  const next = new Set(orgMarcadas.value)
  if (next.has(chave)) next.delete(chave)
  else next.add(chave)
  orgMarcadas.value = next
}
async function verListaOrganizar(chave: string, ocultos: boolean, pagina = 1) {
  const minha = ++seqOrgLista
  const mesma = orgLista.value?.chave === chave && orgLista.value?.ocultos === ocultos
  orgLista.value = { chave, ocultos, pagina, dados: mesma ? orgLista.value!.dados : null, carregando: true }
  try {
    const qs = new URLSearchParams({ chave, ocultos: String(ocultos), pagina: String(pagina) })
    // "Ver lista" da prévia: exatamente o que ela guardou (o total bate com o
    // botão "Ocultar N"); o que entrou depois fica para a próxima prévia.
    if (!ocultos && orgPrevia.value?.token) qs.set('token', orgPrevia.value.token)
    const dados = await api<OrgItens>(`/api/produtos/organizar/itens?${qs.toString()}`)
    if (minha !== seqOrgLista) return
    orgLista.value = { chave, ocultos, pagina, dados, carregando: false }
  } catch (e: any) {
    if (minha !== seqOrgLista) return
    if (e?.data?.detail?.code === 'previa_expirada') {
      orgErro.value = 'A prévia passou de 30 minutos e venceu. Os números foram lidos de novo: abra a lista outra vez.'
      orgLista.value = null
      await carregarPreviaOrganizar()
      return
    }
    orgErro.value = orgErroTexto(e)
    if (orgLista.value) orgLista.value = { ...orgLista.value, carregando: false }
  }
}
function orgPaginas(d: OrgItens | null | undefined): number {
  if (!d) return 1
  return Math.max(1, Math.ceil((d.total || 0) / (d.por_pagina || 100)))
}
async function aplicarOrganizar() {
  const p = orgPrevia.value
  const chaves = orgChavesParaOcultar.value.map((c) => c.chave)
  if (!p || !chaves.length || orgAplicando.value) return
  orgAplicando.value = true
  orgErro.value = null
  try {
    const r = await api<OrgAplicado>('/api/produtos/organizar/aplicar', {
      method: 'POST',
      body: { token: p.token, chaves },
    })
    orgResultado.value = r
    orgLista.value = null
    // A lista, a faixa "Vínculos" e os números das abas já sem os ocultos.
    await Promise.all([carregarPreviaOrganizar(), recarregarAbaAberta()])
  } catch (e: any) {
    const code = e?.data?.detail?.code
    if (code === 'previa_expirada') {
      orgErro.value = 'A prévia passou de 30 minutos e venceu. Os números foram lidos de novo: confira e clique outra vez.'
      orgLista.value = null
      await carregarPreviaOrganizar()
    } else if (code === 'organizar_em_andamento') {
      orgErro.value = 'Outra pessoa está organizando agora. Espere um pouco e tente de novo.'
    } else {
      orgErro.value = orgErroTexto(e)
    }
  } finally {
    orgAplicando.value = false
  }
}
async function reexibirOrganizar(
  filtro: { lote_id?: string; marcador_ids?: string[]; chave?: string },
  descricao: string,
) {
  if (orgReexibindo.value) return
  orgReexibindo.value = true
  orgErro.value = null
  try {
    const r = await api<{ reexibidos: number }>('/api/produtos/organizar/reexibir', {
      method: 'POST',
      body: filtro,
    })
    pushToast({
      kind: 'success',
      title: `${fmtN(r.reexibidos)} item(ns) voltaram para a tela`,
      lines: [descricao],
    }, 8000)
    if (filtro.lote_id && orgResultado.value?.lote_id === filtro.lote_id) orgResultado.value = null
    const lista = orgLista.value
    await Promise.all([carregarPreviaOrganizar(), recarregarAbaAberta()])
    if (lista?.ocultos) {
      if (filtro.chave === lista.chave) orgLista.value = null
      else await verListaOrganizar(lista.chave, true, lista.pagina)
    }
  } catch (e: any) {
    orgErro.value = orgErroTexto(e)
  } finally {
    orgReexibindo.value = false
  }
}

// Fim de um job (ou do Organizar): recarrega a aba que está aberta e os
// números das abas. Na aba Anúncios quem relê é o painel; a lista de produtos
// fica para quando a pessoa voltar para a aba Produtos.
async function recarregarAbaAberta() {
  if (aba.value === 'anuncios') {
    produtosCarregadosDe.value = null
    await Promise.all([painelAnuncios.value?.refreshAll(), carregarDepartamentos()])
  } else {
    await refreshAll() // já relê os números das abas
  }
}

function stopPolling() {
  if (pollHandle) {
    clearTimeout(pollHandle)
    pollHandle = null
  }
}

// Pull new detail entries since our cursor. Pages forward until caught up
// (usually one request). shallowRef → reassign to trigger the update.
async function fetchDetailDelta(jobId: string) {
  for (let guard = 0; guard < 50; guard++) {
    const r = await api<{ items: Array<Record<string, any>>; max_id: number }>(
      `/api/jobs/${jobId}/details?after=${lastDetailId}&limit=500`,
    ).catch(() => null)
    if (!r || !r.items?.length) break
    jobDetails.value = jobDetails.value.concat(r.items)
    lastDetailId = r.max_id
    if (r.items.length < 500) break
  }
}

// Poll the lightweight /status endpoint and fetch only the detail delta when
// the count grows. setTimeout (not setInterval) so we can back off from 1.5s
// up to 5s while nothing changes, and stop cleanly on a terminal status.
// PENDING por mais que isto = o robô não vai pegar (ex.: API publicada antes
// do worker): a tela desiste — é a mesma regra da trava global no servidor.
const PENDENTE_MAX_MS = 20 * 60 * 1000
function startPolling(jobId: string) {
  stopPolling()
  jobDetails.value = []
  lastDetailId = 0
  activeJob.value = null
  fimDeMassa.value = null
  let delay = 1500
  let lastCount = -1
  let pendenteDesde: number | null = null
  const tick = async () => {
    try {
      const s = await api<Job>(`/api/jobs/${jobId}/status`)
      // Rodada refeita pelo robô (worker reiniciado no meio): o backend apaga
      // os detalhes e recomeça — a tela também, senão congelava e duplicava.
      const anterior = activeJob.value
      if (
        (s.details_count ?? 0) < jobDetails.value.length ||
        (anterior?.id === s.id && anterior?.started_at && s.started_at && anterior.started_at !== s.started_at)
      ) {
        jobDetails.value = []
        lastDetailId = 0
      }
      activeJob.value = s
      const count = s.details_count ?? 0
      if (count > jobDetails.value.length) await fetchDetailDelta(jobId)
      if (s.status === 'succeeded' || s.status === 'failed' || s.status === 'cancelled') {
        await fetchDetailDelta(jobId) // catch the tail before we stop
        stopPolling()
        if (!dialogoAberto() && dialogoDoJob.value) fimDeMassa.value = resumoDoFim(s)
        await recarregarAbaAberta()
        await refreshMassActive() // re-enable os botões de massa assim que fecha
        return
      }
      if (s.status === 'pending') {
        const criado = s.created_at ? Date.parse(s.created_at) : NaN
        pendenteDesde ??= Number.isNaN(criado) ? Date.now() : Math.min(criado, Date.now())
        if (Date.now() - pendenteDesde > PENDENTE_MAX_MS) {
          stopPolling()
          activeJob.value = {
            ...s,
            status: 'failed',
            error: 'O robô não pegou o pedido em 20 min. Tente de novo (se repetir, avise o administrador).',
          }
          if (!dialogoAberto() && dialogoDoJob.value) fimDeMassa.value = resumoDoFim(activeJob.value)
          await refreshMassActive()
          return
        }
      } else {
        pendenteDesde = null
      }
      // Snap back to fast polling on activity; back off when idle.
      delay = count === lastCount ? Math.min(5000, Math.round(delay * 1.4)) : 1500
      lastCount = count
      pollHandle = window.setTimeout(tick, delay)
    } catch {
      pollHandle = window.setTimeout(tick, delay)
    }
  }
  void tick()
}

// Qual diálogo mostra a massa (para retomar o andamento ao abrir a página de
// novo). Os automáticos (varredura das 10h, noturna) não têm diálogo.
function dialogoDaMassa(m: MassaInfo): 'atualizar' | 'vincular' | 'sincronizar' | null {
  const tipo = m.tipo || m.type
  if (m.gatilho === 'botao_atualizar') return 'atualizar'
  if (m.gatilho && GATILHOS_AUTOMATICOS.has(m.gatilho)) return null
  if (tipo === 'auto_link') return 'vincular'
  if (tipo === 'sync_all') return 'sincronizar'
  return null
}
async function retomarMassaMinha() {
  await refreshMassActive()
  const m = massaAtiva.value
  if (!m?.meu || !m.job_id || activeJob.value) return
  const qual = dialogoDaMassa(m)
  if (!qual) return
  dialogoDoJob.value = qual
  startPolling(m.job_id)
}

onMounted(() => {
  void retomarMassaMinha()
  // Poll leve (10s) p/ refletir massa iniciado em outra aba/cliente ou pelo
  // daily_sync — mantém os botões de massa desabilitados p/ todos.
  massActiveHandle = window.setInterval(() => void refreshMassActive(), 10000)
})
onUnmounted(() => {
  stopPolling()
  if (massActiveHandle) window.clearInterval(massActiveHandle)
})

</script>

<template>
  <div class="products-page min-w-0 space-y-5">
    <PageHeader title="Produtos" description="SKUs do Bling, custos e links por canal.">
      <template #actions>
        <!-- Aba Anúncios: "recarregar" a lista (fica fora do "Gerenciar
             produtos", que no celular começa fechado). O "importar" de uma
             conta por vez saiu: quem lê as listas agora é o Atualizar. -->
        <Button v-if="aba === 'anuncios'" size="sm" variant="outline" @click="painelAnuncios?.refreshAll()">
          <RefreshCw class="size-4 mr-1.5" /> recarregar
        </Button>
        <Button v-if="canEdit" size="sm" variant="outline" class="lg:hidden" :aria-expanded="mobileProductTools" aria-controls="product-tools" @click="mobileProductTools = !mobileProductTools">
          Gerenciar produtos <ChevronDown class="ml-1.5 size-4" :class="mobileProductTools ? 'rotate-180' : ''" />
        </Button>
        <div v-if="canEdit" id="product-tools" class="flex-wrap items-center gap-2 min-w-0" :class="mobileProductTools ? 'flex' : 'hidden lg:flex'">
        <!-- Barra comum às duas abas (17/11/2026), na ordem de uso:
             Atualizar → Vincular → Sincronizar. Organizar só para o ADM. -->
        <div class="flex flex-wrap items-center gap-2" aria-label="Botões de Produtos e Anúncios">
          <Button
            size="sm"
            variant="outline"
            :disabled="jobActive"
            class="border-sky-300 text-sky-700 hover:bg-sky-50"
            :title="jobActive ? descreverMassa(massaAtiva, true) : 'Lê os produtos novos do Bling e as listas de anúncios dos marketplaces e liga os anúncios aos produtos. Só grava no DaVinci.'"
            @click="abrirAtualizar"
          >
            <CloudDownload class="size-4 mr-1.5" /> Atualizar
          </Button>
          <Button
            size="sm"
            variant="outline"
            :disabled="jobActive"
            class="border-emerald-300 text-emerald-700 hover:bg-emerald-50"
            :title="jobActive ? descreverMassa(massaAtiva, true) : 'Lê os anúncios ao vivo e acerta o SKU e o vínculo só no DaVinci. Não muda nada no marketplace.'"
            @click="openAutoLinkDialog"
          >
            <Zap class="size-4 mr-1.5" /> Vincular
          </Button>
          <Button
            size="sm"
            variant="outline"
            :disabled="jobActive"
            :title="jobActive ? descreverMassa(massaAtiva, true) : 'Envia o estoque do Bling para os anúncios vinculados.'"
            @click="openSyncAllDialog"
          >
            <RefreshCw class="size-4 mr-1.5" /> Sincronizar
          </Button>
          <Button
            v-if="isAdmin"
            size="sm"
            variant="outline"
            class="border-violet-300 text-violet-700 hover:bg-violet-50"
            title="Esconde da tela o que não vale mais (só administrador). Nada é apagado e dá para desfazer."
            @click="abrirOrganizar"
          >
            <EyeOff class="size-4 mr-1.5" /> Organizar
          </Button>
          <Button v-if="andamentoAberto" size="sm" variant="ghost" @click="verAndamento">
            <template v-if="jobRodando"><Loader2 class="size-4 mr-1.5 animate-spin" /> ver andamento</template>
            <template v-else>ver resultado</template>
          </Button>
        </div>
        <template v-if="aba === 'produtos'">
        <label
          v-if="canEdit"
          class="flex items-center gap-2 text-xs px-3 py-1.5 rounded-md border bg-background cursor-pointer select-none"
          :title="autoSyncEnabled ? 'Desligar sync automático diário' : 'Ligar sync automático diário'"
        >
          <input
            type="checkbox"
            :checked="autoSyncEnabled"
            :disabled="togglingAutoSync"
            class="accent-primary"
            @change="toggleAutoSync"
          />
          <span :class="autoSyncEnabled ? 'text-foreground' : 'text-muted-foreground'">
            sync automático {{ autoSyncEnabled ? 'on' : 'off' }}
          </span>
        </label>
        <Button
          v-if="canEdit"
          size="sm"
          variant="outline"
          class="border-orange-300 text-orange-700 hover:bg-orange-50"
          @click="openImport"
        >
          <Download class="size-4 mr-1.5" /> Importar do Bling
        </Button>
        <Button v-if="canEdit" size="sm" variant="outline" @click="showImportCsv = true">
          <Upload class="size-4 mr-1.5" /> Importar CSV
        </Button>
        <Button v-if="canEdit" size="sm" @click="showNewProduct = true">
          <Plus class="size-4 mr-1.5" /> Novo Produto
        </Button>
        </template>
        </div>
      </template>
    </PageHeader>

    <!-- ===================== Departamentos (como a Tabela de Preços) ===== -->
    <!-- Sem número nos botões (decidido em 12/11); "Sem departamento" só
         quando houver produto ou anúncio para classificar. -->
    <div class="flex flex-wrap gap-2" aria-label="Departamentos">
      <button
        v-for="d in departamentosVisiveis"
        :key="d.value"
        type="button"
        class="inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm transition-colors"
        :class="departamento === d.value
          ? 'bg-primary text-primary-foreground border-primary'
          : 'border-border hover:bg-muted'"
        :aria-pressed="departamento === d.value"
        @click="departamento = d.value"
      >
        <component :is="d.icon" class="h-4 w-4" />
        {{ d.label }}
      </button>
    </div>

    <!-- ===================== Abas do departamento ======================== -->
    <div class="flex flex-wrap gap-1 rounded-md bg-muted/40 p-1 w-fit" aria-label="Abas de Produtos">
      <button
        v-for="a in ABAS"
        :key="a.key"
        type="button"
        class="inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-sm transition-colors"
        :class="aba === a.key
          ? 'bg-background shadow-sm'
          : 'text-muted-foreground hover:text-foreground'"
        :aria-pressed="aba === a.key"
        @click="setAba(a.key)"
      >
        <component :is="a.icon" class="h-4 w-4" />
        {{ a.label }}
        <span v-if="a.qtd != null" class="text-xs text-muted-foreground">({{ a.qtd.toLocaleString('pt-BR') }})</span>
      </button>
    </div>

    <!-- Avisos da página (erro, "já tem … rodando"): fora das abas, para
         aparecerem também na aba Anúncios. -->
    <div v-if="error" class="flex items-start gap-2 rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">
      <span class="flex-1">{{ error }}</span>
      <button class="text-red-500 hover:text-red-700" title="fechar" @click="error = null">
        <X class="size-4" />
      </button>
    </div>
    <div
      v-else-if="canEdit && massSyncActiveRemote && massaAtiva"
      class="rounded-md border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs text-amber-800"
    >
      {{ descreverMassa(massaAtiva, true) }}. Atualizar, Vincular e Sincronizar voltam quando terminar.
    </div>
    <!-- Terminou com o diálogo fechado (minimizado): o resumo fica aqui até a
         pessoa ver ou fechar. -->
    <div
      v-if="fimDeMassa"
      class="flex items-start gap-2 rounded-md border px-3 py-2 text-sm"
      :class="fimDeMassa.tom === 'erro' ? 'border-red-300 bg-red-50 text-red-800' : fimDeMassa.tom === 'aviso' ? 'border-amber-300 bg-amber-50 text-amber-800' : 'border-emerald-300 bg-emerald-50 text-emerald-800'"
    >
      <span class="flex-1">{{ fimDeMassa.texto }}</span>
      <button class="underline font-medium" @click="verAndamento">ver resultado</button>
      <button title="fechar" @click="fimDeMassa = null"><X class="size-4" /></button>
    </div>

    <!-- ===================== Aba Anúncios ================================ -->
    <ProdutosAnunciosPainel
      v-if="aba === 'anuncios'"
      ref="painelAnuncios"
      :departamento="departamento"
      @mudou="carregarDepartamentos"
    />

    <!-- ===================== Aba Produtos ================================ -->
    <template v-else>

    <div class="product-filters flex flex-wrap gap-3 items-center" :class="{ 'product-filters-open': mobileProductFilters }">
      <div class="relative w-full min-w-0 sm:flex-1 sm:min-w-[260px]">
        <Search class="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
        <Input v-model="search" placeholder="Buscar por nome, SKU ou ID do anúncio..." class="pl-9" @keyup.enter="onBuscaEnter" />
      </div>
      <Button
        v-if="buscaEhIdAnuncio"
        size="sm"
        variant="outline"
        :disabled="anuncioLoading"
        @click="buscarAnuncio"
      >
        <Search class="size-4 mr-1.5" />
        {{ anuncioLoading ? 'buscando anúncio…' : 'Buscar anúncio' }}
      </Button>
      <select v-model="stockFilter" class="h-9 w-[180px] rounded-md border bg-background px-2 text-sm" @change="refreshAll">
        <option value="">Todo estoque</option>
        <option value="low">Estoque baixo</option>
        <option value="ok">Estoque OK</option>
        <option value="zero">Sem estoque</option>
      </select>
      <Button class="lg:hidden" size="sm" variant="outline" :aria-expanded="mobileProductFilters" @click="mobileProductFilters = !mobileProductFilters">
        Filtros <span v-if="filtroVinculos || filtroIntegration || filtroSegment" class="ml-1.5 size-2 rounded-full bg-primary" aria-label="Filtros ativos" />
      </Button>
      <select
        v-model="filtroVinculos"
        class="product-filter-extra h-9 w-[240px] rounded-md border bg-background px-2 text-sm"
        title="Vínculos com problema: anúncio encerrado/excluído (morto), anúncio com SKU diferente do produto, ou erro no envio de estoque"
        @change="page = 1; refreshAll()"
      >
        <option value="">Vínculos: todos</option>
        <option value="mortos">Vínculos: mortos ({{ saude.mortos }})</option>
        <option value="sku">Vínculos: SKU do anúncio diferente ({{ saude.sku_divergente }})</option>
        <option value="erro">Vínculos: com erro no envio ({{ saude.erro }})</option>
      </select>
      <select v-model="filtroIntegration" class="product-filter-extra h-9 w-[220px] rounded-md border bg-background px-2 text-sm" @change="refreshAll">
        <option value="">Todas as contas</option>
        <optgroup v-for="g in integrationGroups" :key="g.platform" :label="g.platform">
          <option v-for="i in g.items" :key="i.id" :value="i.id">[{{ i.platform }}] {{ i.name }}</option>
        </optgroup>
      </select>
      <select v-model="filtroSegment" class="product-filter-extra h-9 w-[200px] rounded-md border bg-background px-2 text-sm">
        <option value="">Todos segmentos</option>
        <option value="__none__">— sem segmento</option>
        <option v-for="s in segmentFilterOptions" :key="s.id" :value="s.id">{{ s.label }}</option>
      </select>
      <span class="ml-auto text-xs text-muted-foreground">
        {{ filteredItems.length }} de {{ total }} produtos · pág {{ page }}/{{ totalPages }}
      </span>
      <Button v-if="canEdit && selected.size > 0" size="sm" variant="outline" @click="showBulkSegment = true">
        <Tags class="size-4 mr-1.5" /> segmento ({{ selected.size }})
      </Button>
      <Button v-if="canDelete && selected.size > 0" size="sm" variant="destructive" @click="bulkDelete">
        <Trash2 class="size-4 mr-1.5" /> excluir {{ selected.size }}
      </Button>
    </div>

    <div
      v-if="saude.mortos || saude.sku_divergente || saude.erro"
      class="flex flex-wrap items-center gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900"
    >
      <span class="font-medium">Vínculos:</span>
      <button v-if="saude.mortos" class="underline" @click="filtroVinculos = 'mortos'; page = 1; refreshAll()">
        {{ saude.mortos.toLocaleString('pt-BR') }} mortos (anúncio encerrado, excluído ou bloqueado — não recebem mais estoque)
      </button>
      <span v-if="saude.mortos && (saude.sku_divergente || saude.erro)">·</span>
      <button v-if="saude.sku_divergente" class="underline" @click="filtroVinculos = 'sku'; page = 1; refreshAll()">
        {{ saude.sku_divergente.toLocaleString('pt-BR') }} com SKU do anúncio diferente do produto
      </button>
      <span v-if="saude.sku_divergente && saude.erro">·</span>
      <button v-if="saude.erro" class="underline" @click="filtroVinculos = 'erro'; page = 1; refreshAll()">
        {{ saude.erro.toLocaleString('pt-BR') }} com erro no envio
      </button>
    </div>

    <!-- Anúncio buscado por ID: todas as variações/SKUs agrupados embaixo,
         com um botão que vincula + sincroniza só este anúncio. -->
    <div
      v-if="anuncioErro && !anuncio?.encontrado"
      class="flex items-center gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800"
    >
      <span class="flex-1">{{ anuncioErro }}</span>
      <button class="text-amber-700 hover:text-amber-900" title="fechar" @click="fecharAnuncio">
        <X class="size-4" />
      </button>
    </div>

    <div v-if="anuncio?.encontrado" class="table-card p-0">
      <div class="flex flex-wrap items-start gap-3 border-b px-4 py-3">
        <div class="min-w-[240px] flex-1">
          <div class="flex items-center gap-2">
            <span :class="platformBadgeClass(anuncio.plataforma || '')">{{ anuncio.plataforma }}</span>
            <span class="font-medium">{{ anuncio.titulo || anuncio.external_id }}</span>
          </div>
          <div class="mt-1 text-xs text-muted-foreground">
            ID {{ anuncio.external_id }} · conta {{ anuncio.conta }} ·
            {{ anuncio.resumo.total }} variação(ões)
            <span v-if="anuncio.origem === 'local'" class="text-amber-700">
              · o marketplace não respondeu, mostrando os vínculos já salvos
            </span>
          </div>
        </div>
        <div class="flex flex-wrap items-center gap-1.5 text-xs">
          <span v-if="anuncio.resumo.prontos" class="rounded px-2 py-0.5 bg-green-100 text-green-700">
            {{ anuncio.resumo.prontos }} pronto(s) p/ vincular
          </span>
          <span v-if="anuncio.resumo.trocados" class="rounded px-2 py-0.5 bg-purple-100 text-purple-700">
            {{ anuncio.resumo.trocados }} SKU(s) trocado(s) — o botão move o vínculo
          </span>
          <span v-if="anuncio.resumo.ja_vinculados" class="rounded px-2 py-0.5 bg-blue-100 text-blue-700">
            {{ anuncio.resumo.ja_vinculados }} já vinculado(s)
          </span>
          <span v-if="anuncio.resumo.sem_produto" class="rounded px-2 py-0.5 bg-red-100 text-red-700">
            {{ anuncio.resumo.sem_produto }} sem produto
          </span>
          <span v-if="anuncio.resumo.sku_ambiguo" class="rounded px-2 py-0.5 bg-amber-100 text-amber-700">
            {{ anuncio.resumo.sku_ambiguo }} SKU ambíguo
          </span>
          <span v-if="anuncio.resumo.sem_sku" class="rounded px-2 py-0.5 bg-gray-100 text-gray-600">
            {{ anuncio.resumo.sem_sku }} sem SKU
          </span>
        </div>
        <div class="flex items-center gap-2">
          <Button
            v-if="canEdit"
            size="sm"
            :disabled="anuncioSyncing"
            @click="sincronizarAnuncio"
          >
            <Loader2 v-if="anuncioSyncing" class="size-4 mr-1.5 animate-spin" />
            <Zap v-else class="size-4 mr-1.5" />
            {{ anuncioSyncing ? 'sincronizando…' : 'Vincular e sincronizar estoque deste anúncio' }}
          </Button>
          <Button size="sm" variant="outline" :disabled="anuncioLoading" @click="buscarAnuncio">
            <RefreshCw class="size-4" :class="anuncioLoading ? 'animate-spin' : ''" />
          </Button>
          <button class="text-muted-foreground hover:text-foreground" title="fechar" @click="fecharAnuncio">
            <X class="size-4" />
          </button>
        </div>
      </div>
      <div v-if="anuncioErro" class="border-b bg-red-50 px-4 py-2 text-sm text-red-700">
        {{ anuncioErro }}
      </div>
      <table class="w-full">
        <thead>
          <tr>
            <th>Variação</th>
            <th>SKU do anúncio</th>
            <th>Produto no DaVinci</th>
            <th class="text-center">Estoque Bling</th>
            <th class="text-center">Estoque no anúncio</th>
            <th>Situação</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(v, i) in anuncio.variacoes" :key="v.variation_id || `v${i}`">
            <td>{{ v.variacao || '—' }}</td>
            <td class="font-mono text-xs">
              {{ v.sku || '—' }}
              <span v-if="v.sku_anterior" class="block text-[10px] text-purple-600">
                vínculo ainda no {{ v.sku_anterior }}
              </span>
            </td>
            <td>{{ v.produto_nome || '—' }}</td>
            <td class="text-center">{{ v.estoque_bling ?? '—' }}</td>
            <td class="text-center">{{ v.estoque_anuncio ?? '—' }}</td>
            <td>
              <span
                class="inline-block rounded px-2 py-0.5 text-xs font-medium"
                :class="ESTADO_ANUNCIO[v.estado].cls"
              >
                {{ ESTADO_ANUNCIO[v.estado].label }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
      <div class="border-t px-4 py-2 text-xs text-muted-foreground">
        Sincroniza só as variações deste anúncio — a conta inteira não é varrida.
        Variação sem SKU casado com um produto continua de fora (mesma regra do Vincular).
        Se você trocou o SKU dentro do anúncio, o botão move o vínculo para o produto do SKU novo
        antes de sincronizar — o estoque passa a sair do produto certo.
      </div>
    </div>

    <div class="flex items-center justify-between gap-3 lg:hidden">
      <p class="text-xs text-muted-foreground">{{ filteredItems.length }} produtos nesta página</p>
      <div class="flex rounded-lg border bg-muted/30 p-0.5" role="group" aria-label="Visualização dos produtos">
        <button type="button" class="rounded-md px-3 py-1.5 text-xs font-medium" :class="mobileProductView === 'cards' ? 'bg-background shadow-sm' : 'text-muted-foreground'" :aria-pressed="mobileProductView === 'cards'" @click="mobileProductView = 'cards'; closeSyncPopover(); closeSegPicker()">Resumo</button>
        <button type="button" class="rounded-md px-3 py-1.5 text-xs font-medium" :class="mobileProductView === 'table' ? 'bg-background shadow-sm' : 'text-muted-foreground'" :aria-pressed="mobileProductView === 'table'" @click="mobileProductView = 'table'">Tabela</button>
      </div>
    </div>

    <section v-if="mobileProductView === 'cards'" class="product-mobile-cards grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-2 lg:hidden" aria-label="Resumo dos produtos" :aria-busy="loading">
      <article v-for="p in filteredItems" :key="p.id" class="product-mobile-card min-w-0 self-start rounded-xl border bg-card p-3">
        <div class="flex items-start gap-2.5">
          <img v-if="p.image_url" :src="p.image_url" alt="" loading="lazy" class="size-11 shrink-0 rounded-md border bg-white object-contain" />
          <div class="min-w-0 flex-1">
            <div class="font-mono text-[11px] text-muted-foreground">{{ p.sku }}</div>
            <h2 class="mt-0.5 text-sm font-semibold leading-snug">{{ p.name }}</h2>
          </div>
          <label v-if="canEdit || canDelete" class="flex size-10 shrink-0 cursor-pointer items-center justify-center rounded-md hover:bg-muted">
            <input type="checkbox" :checked="selected.has(p.id)" :aria-label="`Selecionar produto ${p.sku}`" @change="toggleSelect(p.id)" />
          </label>
        </div>
        <dl class="mt-3 grid grid-cols-3 gap-2 rounded-lg bg-muted/40 px-2.5 py-2">
          <div>
            <dt class="text-[10px] text-muted-foreground">Estoque Bling</dt>
            <dd class="text-lg font-semibold tabular-nums" :class="p.stock === 0 ? 'text-red-600' : p.stock < p.min_stock ? 'text-amber-600' : ''">{{ p.stock.toLocaleString('pt-BR') }}</dd>
          </div>
          <div>
            <dt class="text-[10px] text-muted-foreground">Mínimo</dt>
            <dd class="text-lg font-semibold tabular-nums">{{ p.min_stock.toLocaleString('pt-BR') }}</dd>
          </div>
          <div>
            <dt class="text-[10px] text-muted-foreground">Vínculos</dt>
            <dd class="text-lg font-semibold tabular-nums">{{ mobileLinksFor(p).length }}</dd>
          </div>
        </dl>
        <div class="mt-2 flex flex-wrap items-center gap-1.5">
          <span class="pill text-[10px]" :class="p.stock === 0 ? 'pill-danger' : p.stock < p.min_stock ? 'pill-warning' : 'pill-success'">{{ p.stock === 0 ? 'Sem estoque' : p.stock < p.min_stock ? 'Estoque baixo' : 'Estoque OK' }}</span>
          <span v-if="p.segment_path" class="text-[11px] text-muted-foreground">{{ p.segment_path }}</span>
        </div>
        <details class="mt-2 border-t pt-1" :open="mobileProductExpanded.has(p.id)" @toggle="setMobileProductDetails(p.id, $event)">
          <summary class="flex min-h-10 cursor-pointer list-none items-center justify-between gap-2 text-xs font-medium text-primary">
            Detalhes e vínculos <ChevronDown class="size-4 shrink-0" />
          </summary>
          <template v-if="mobileProductExpanded.has(p.id)">
          <dl class="grid grid-cols-2 gap-x-3 gap-y-2 pb-3 text-xs">
            <div><dt class="text-muted-foreground">Bling ID</dt><dd>{{ p.bling_product_id || '—' }}</dd></div>
            <div><dt class="text-muted-foreground">Atualizado</dt><dd>{{ fmtQuando(p.updated_at) }}</dd></div>
            <div v-if="p.observation" class="col-span-2"><dt class="text-muted-foreground">Observação</dt><dd>{{ p.observation }}</dd></div>
            <div v-if="p.observation2" class="col-span-2"><dt class="text-muted-foreground">Observação 2</dt><dd>{{ p.observation2 }}</dd></div>
            <div v-if="p.observation3" class="col-span-2"><dt class="text-muted-foreground">Observação 3</dt><dd>{{ p.observation3 }}</dd></div>
          </dl>
          <p v-if="mobileLinksFor(p).length === 0" class="pb-2 text-xs text-muted-foreground">Nenhum vínculo{{ filtroIntegration ? ' nesta conta' : '' }}.</p>
          <div v-else class="space-y-2">
            <div v-for="link in mobileLinksFor(p)" :key="link.id" class="rounded-lg border p-2.5 text-xs">
              <div class="flex flex-wrap items-start justify-between gap-2">
                <div class="min-w-0">
                  <span :class="platformBadgeClass(link.platform)">{{ mobilePlatformLabels[link.platform] || link.platform }}</span>
                  <div class="mt-1 font-medium">{{ integrationById[link.integration_id]?.name || link.platform }}</div>
                </div>
                <div class="text-right"><div class="text-[10px] text-muted-foreground">Estoque no canal</div><strong class="tabular-nums" :class="link.morto_desde ? 'line-through text-muted-foreground' : ''">{{ link.stock ?? '—' }}</strong></div>
              </div>
              <div v-if="link.listing_title" class="mt-2">{{ link.listing_title }}</div>
              <dl class="mt-2 space-y-1 text-[11px]">
                <div><dt class="inline text-muted-foreground">ID: </dt><dd class="inline font-mono">{{ link.external_id }}</dd></div>
                <div v-if="link.external_sku"><dt class="inline text-muted-foreground">SKU: </dt><dd class="inline font-mono" :class="skuDiferente(p, link) ? 'text-purple-700 font-semibold' : ''">{{ link.external_sku }}</dd></div>
                <div v-if="link.variation_id"><dt class="inline text-muted-foreground">Variação: </dt><dd class="inline">{{ link.variation_id }}</dd></div>
                <div><dt class="inline text-muted-foreground">Último envio: </dt><dd class="inline">{{ fmtQuando(link.last_sync_at) }}</dd></div>
              </dl>
              <div class="mt-2 flex flex-wrap gap-1">
                <span v-if="link.morto_desde" class="pill pill-danger">Anúncio encerrado</span>
                <span v-else class="pill" :class="link.last_sync_status === 'ok' ? 'pill-success' : link.last_sync_status === 'fatal' ? 'pill-danger' : 'pill-muted'">{{ link.last_sync_status === 'ok' ? 'Sincronizado' : link.last_sync_status === 'fatal' ? 'Falha no envio' : link.last_sync_status || 'Aguardando' }}</span>
                <span v-if="skuDiferente(p, link)" class="pill pill-warning">SKU diferente</span>
              </div>
              <p v-if="link.morto_desde && link.morto_motivo" class="mt-2 text-red-700">{{ link.morto_motivo }}</p>
              <p v-else-if="link.last_error" class="mt-2 text-amber-700">{{ link.last_error }}</p>
            </div>
          </div>
          </template>
        </details>
      </article>
      <p v-if="!filteredItems.length" class="rounded-xl border bg-card px-4 py-8 text-center text-sm text-muted-foreground sm:col-span-2">{{ loading ? 'Carregando produtos…' : 'Nenhum produto encontrado com estes filtros.' }}</p>
    </section>

    <div class="table-card" :class="mobileProductView === 'cards' ? 'hidden lg:block' : 'block'">
      <table class="w-full">
        <thead>
          <tr>
            <th class="w-8"></th>
            <th class="w-8"></th>
            <th>SKU</th>
            <th>Produto</th>
            <th>Segmento</th>
            <th class="text-center">Bling</th>
            <th class="text-center">Shopee</th>
            <th class="text-center">Amazon</th>
            <th class="text-center">ML Clássico</th>
            <th class="text-center">ML Premium</th>
            <th class="text-center">TikTok</th>
            <th class="text-center">Magalu</th>
            <th class="text-center">Status</th>
            <th class="text-right w-24">Ações</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="p in filteredItems" :key="p.id">
            <tr>
              <td>
                <input type="checkbox" :checked="selected.has(p.id)" @change="toggleSelect(p.id)" />
              </td>
              <td>
                <button class="size-6 grid place-items-center rounded hover:bg-muted" @click="toggleExpand(p.id)">
                  <component :is="expanded.has(p.id) ? ChevronDown : ChevronRight" class="size-4" />
                </button>
              </td>
              <td class="font-mono text-xs">{{ p.sku }}</td>
              <td class="font-medium">{{ p.name }}</td>
              <td class="text-xs relative">
                <button
                  v-if="canEdit"
                  class="text-left hover:underline"
                  :class="p.segment_id ? 'text-foreground' : 'text-muted-foreground italic'"
                  :title="p.segment_path || 'Clique para definir segmento'"
                  @click="openSegPicker(p)"
                >
                  <span v-if="p.segment_path">{{ p.segment_path }}</span>
                  <span v-else>+ segmento</span>
                </button>
                <span v-else :class="p.segment_id ? '' : 'text-muted-foreground'">
                  {{ p.segment_path || '—' }}
                </span>
                <div
                  v-if="segPickerProductId === p.id"
                  class="absolute left-0 top-full mt-1 z-30 w-72 rounded-md border bg-background shadow-lg p-2 text-left"
                  @click.stop
                >
                  <div class="space-y-1 max-h-72 overflow-y-auto">
                    <button
                      class="w-full text-left text-xs px-2 py-1 rounded hover:bg-muted text-muted-foreground italic"
                      @click="setProductSegment(p, null)"
                    >
                      — limpar segmento —
                    </button>
                    <button
                      v-for="opt in segmentOptions"
                      :key="opt.id"
                      class="w-full text-left text-xs px-2 py-1 rounded hover:bg-muted"
                      :class="opt.id === p.segment_id ? 'bg-emerald-50 dark:bg-emerald-900/30 font-semibold' : ''"
                      @click="setProductSegment(p, opt.id)"
                    >
                      {{ opt.label }}
                    </button>
                    <p v-if="segmentOptions.length === 0" class="text-xs text-muted-foreground italic px-2 py-1">
                      Nenhum segmento. Cadastre em Admin → Segmentos.
                    </p>
                  </div>
                  <div class="flex justify-end pt-1 border-t mt-1">
                    <Button size="sm" variant="ghost" class="h-6 px-2 text-xs" @click="closeSegPicker">fechar</Button>
                  </div>
                </div>
              </td>
              <td class="text-center tabular-nums font-semibold">
                <span :class="p.stock === 0 ? 'text-red-600' : p.stock < p.min_stock ? 'text-amber-600' : ''">
                  {{ p.stock }}
                </span>
              </td>
              <td v-for="col in (['shopee','amazon','ml_classico','ml_premium','tiktok','magalu'] as const)" :key="col" class="text-center">
                <template v-if="linksFor(p, col).length === 0">
                  <button
                    v-if="(col === 'amazon' || col === 'tiktok') && hasIntegrationsForCol(col)"
                    class="text-[10px] text-blue-500 hover:text-blue-700 hover:underline cursor-pointer"
                    :title="`Vincular manualmente à ${col === 'amazon' ? 'Amazon' : 'TikTok'}`"
                    @click="manualLink(p, col)"
                  >+ vincular</button>
                  <span v-else class="text-xs text-muted-foreground">—</span>
                </template>
                <div v-else class="flex flex-col items-center gap-0.5">
                  <div
                    v-for="(l, idx) in linksFor(p, col)"
                    :key="l.id"
                    class="group/link relative flex flex-col items-center leading-tight px-3"
                    :class="idx > 0 ? 'border-t border-border/30 pt-0.5 mt-0.5' : ''"
                    :title="dicaVinculo(p, l)"
                  >
                    <span
                      class="text-xs font-bold tabular-nums"
                      :class="l.morto_desde ? 'line-through text-muted-foreground font-normal' : l.last_sync_status === 'fatal' ? 'text-red-600' : ''"
                    >{{ l.stock ?? 0 }}</span>
                    <span class="text-[9px] text-muted-foreground leading-none">
                      {{ integrationById[l.integration_id]?.name?.trim() || l.platform }}
                    </span>
                    <span v-if="l.morto_desde" class="text-[8px] uppercase tracking-wide text-red-700 leading-none">morto</span>
                    <span v-else-if="skuDiferente(p, l)" class="text-[8px] uppercase tracking-wide text-purple-700 leading-none">sku ≠</span>
                    <button
                      v-if="canDelete"
                      class="absolute -right-2 -top-0.5 hidden group-hover/link:flex items-center justify-center size-3.5 rounded-full bg-red-500 hover:bg-red-600 text-white text-[9px]"
                      :title="`Remover vínculo: ${integrationById[l.integration_id]?.name || l.platform}`"
                      @click.stop="deleteLink(l.id)"
                    >×</button>
                  </div>
                </div>
              </td>
              <td class="text-center">
                <span
                  class="pill text-[10px]"
                  :class="p.stock < p.min_stock ? 'pill-danger' : 'pill-success'"
                >
                  {{ p.stock < p.min_stock ? 'BAIXO' : 'OK' }}
                </span>
              </td>
              <td class="text-right">
                <div class="flex items-center justify-end gap-1 relative">
                  <Button
                    v-if="canEdit"
                    size="icon"
                    variant="ghost"
                    class="text-cyan-600 hover:text-cyan-700 hover:bg-cyan-50"
                    :disabled="syncingProduct.has(p.id)"
                    :title="syncingProduct.has(p.id) ? 'sincronizando…' : 'Sincronizar este SKU'"
                    @click="(ev: MouseEvent) => syncPopoverProductId === p.id ? closeSyncPopover() : openSyncPopover(p, ev)"
                  >
                    <RefreshCw class="size-4" :class="syncingProduct.has(p.id) ? 'animate-spin' : ''" />
                  </Button>
                  <Button
                    v-if="canEdit"
                    size="icon"
                    variant="ghost"
                    class="text-violet-600 hover:text-violet-700 hover:bg-violet-50"
                    :disabled="reloadingLinks.has(p.id)"
                    :title="reloadingLinks.has(p.id) ? 'recarregando vínculo…' : 'Recarregar Vínculo — relê o SKU dos anúncios e re-aponta os que mudaram'"
                    @click="reloadLinks(p.id)"
                  >
                    <Link2 class="size-4" :class="reloadingLinks.has(p.id) ? 'animate-pulse' : ''" />
                  </Button>
                  <Button
                    v-if="canDelete"
                    size="icon"
                    variant="ghost"
                    class="text-red-600 hover:text-red-700 hover:bg-red-50"
                    title="Excluir produto"
                    @click="deleteOne(p.id)"
                  >
                    <Trash2 class="size-4" />
                  </Button>

                  <!-- Per-product sync popover. Teleportado pra <body>
                       porque o .table-card tem overflow-hidden (pro
                       rounded-xl) e clipa qualquer absolute dentro de uma
                       <td>. position: fixed via syncPopoverStyle ancora
                       no rect do botão. -->
                  <Teleport to="body">
                  <div
                    v-if="syncPopoverProductId === p.id"
                    id="sync-popover-floating"
                    class="z-50 rounded-md border bg-background shadow-lg p-3 text-left"
                    :style="syncPopoverStyle"
                    @click.stop
                  >
                    <div class="space-y-3">
                      <div>
                        <p class="text-sm font-semibold">Sincronizar: {{ p.sku }}</p>
                        <p class="text-xs text-muted-foreground">Selecione as contas para sincronizar</p>
                      </div>
                      <div class="space-y-1 max-h-48 overflow-y-auto">
                        <p v-if="linkedIntegrationsFor(p).length === 0" class="text-xs text-muted-foreground italic py-2">
                          Sem links neste produto. Use "Vincular".
                        </p>
                        <label
                          v-for="integ in linkedIntegrationsFor(p)"
                          :key="integ.id"
                          class="flex items-center gap-2 text-sm cursor-pointer hover:bg-muted/50 rounded px-1 py-0.5"
                        >
                          <input
                            type="checkbox"
                            :checked="syncPopoverSelectedIds.has(integ.id)"
                            @change="toggleSyncPopoverIntegration(integ.id)"
                          />
                          <span :class="platformBadgeClass(integ.platform)">{{ integ.platform }}</span>
                          <span class="truncate">{{ integ.name }}</span>
                        </label>
                      </div>
                      <div class="flex items-center justify-between gap-2 pt-1 border-t">
                        <span class="text-xs text-muted-foreground">{{ syncPopoverSelectedIds.size }} selecionada(s)</span>
                        <div class="flex gap-1">
                          <Button size="sm" variant="ghost" @click="closeSyncPopover">Cancelar</Button>
                          <Button
                            size="sm"
                            :disabled="syncPopoverSelectedIds.size === 0 || syncingProduct.has(p.id)"
                            @click="runSyncFromPopover(p.id)"
                          >
                            <Loader2 v-if="syncingProduct.has(p.id)" class="size-3.5 mr-1 animate-spin" />
                            <RefreshCw v-else class="size-3.5 mr-1" />
                            Sincronizar
                          </Button>
                        </div>
                      </div>
                    </div>
                  </div>
                  </Teleport>
                </div>
              </td>
            </tr>
            <tr v-if="expanded.has(p.id)" class="bg-muted/30">
              <td colspan="14" class="p-3">
                <div v-if="p.links.length === 0" class="text-xs text-muted-foreground">
                  Sem links. Rode o auto-link para vincular este SKU aos canais.
                </div>
                <div v-else>
                  <div v-if="canDelete" class="flex items-center gap-2 mb-2">
                    <button
                      class="text-xs text-muted-foreground hover:text-foreground hover:underline"
                      @click="toggleSelectAllLinks(p)"
                    >
                      {{ allLinksSelected(p) ? 'Desmarcar todos' : 'Marcar todos' }}
                    </button>
                    <Button
                      v-if="selectedLinkCountFor(p) > 0"
                      size="sm"
                      variant="ghost"
                      class="h-7 text-red-600 hover:text-red-700 hover:bg-red-50"
                      @click="bulkDeleteLinks(p)"
                    >
                      <Trash2 class="size-3.5 mr-1" />
                      Excluir selecionados ({{ selectedLinkCountFor(p) }})
                    </Button>
                  </div>
                  <table class="w-full text-xs">
                    <thead>
                      <tr class="text-left text-muted-foreground">
                        <th v-if="canDelete" class="w-6"></th>
                        <th>Plataforma</th>
                        <th>Tipo</th>
                        <th>Integração</th>
                        <th>External ID</th>
                        <th>Variação</th>
                        <th>Título</th>
                        <th>SKU no anúncio</th>
                        <th class="text-right">Estoque</th>
                        <th>Status</th>
                        <th>Último envio</th>
                        <th class="w-8"></th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="l in p.links" :key="l.id" :class="selectedLinks.has(l.id) ? 'bg-red-50' : ''">
                        <td v-if="canDelete" class="w-6">
                          <input
                            type="checkbox"
                            :checked="selectedLinks.has(l.id)"
                            :aria-label="`Selecionar vínculo ${l.external_id}`"
                            @change="toggleSelectLink(l.id)"
                          />
                        </td>
                        <td>{{ l.platform }}</td>
                        <td>{{ l.listing_type || '—' }}</td>
                        <td>{{ integrationById[l.integration_id]?.name || l.integration_id }}</td>
                        <td class="font-mono">{{ l.external_id }}</td>
                        <td>{{ l.variation_id || '—' }}</td>
                        <td>{{ l.listing_title || '—' }}</td>
                        <td class="font-mono" :class="skuDiferente(p, l) ? 'text-purple-700 font-semibold' : ''" :title="skuDiferente(p, l) ? `O anúncio está com outro SKU; o produto é ${p.sku}` : ''">
                          {{ l.external_sku || '—' }}
                        </td>
                        <td class="text-right tabular-nums" :class="l.morto_desde ? 'line-through text-muted-foreground' : ''">{{ l.stock ?? '—' }}</td>
                        <td>
                          <span v-if="l.morto_desde" class="pill pill-danger" :title="l.morto_motivo || ''">morto</span>
                          <span v-else class="pill" :class="l.last_sync_status === 'ok' ? 'pill-success' : l.last_sync_status === 'fatal' ? 'pill-danger' : 'pill-muted'">
                            {{ l.last_sync_status }}
                          </span>
                        </td>
                        <td class="max-w-[280px]">
                          <div class="text-muted-foreground">{{ fmtQuando(l.last_sync_at) }}</div>
                          <div v-if="l.morto_desde" class="text-red-700 truncate" :title="l.morto_motivo || ''">{{ l.morto_motivo }}</div>
                          <div v-else-if="l.last_error" class="truncate" :class="l.last_error.startsWith('sku_movido') ? 'text-emerald-700' : 'text-amber-700'" :title="l.last_error">{{ l.last_error }}</div>
                        </td>
                        <td>
                          <Button v-if="canDelete" size="icon" variant="ghost" @click="deleteLink(l.id)">
                            <X class="size-3" />
                          </Button>
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </td>
            </tr>
          </template>
          <tr v-if="items.length === 0">
            <td colspan="14" class="py-8 text-center text-sm text-muted-foreground">
              Nenhum produto. Use "importar Bling" para começar.
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="flex flex-wrap sm:flex-nowrap justify-between items-center gap-y-2 text-sm">
      <div class="flex flex-wrap sm:flex-nowrap gap-1 items-center">
        <Button size="sm" variant="outline" :disabled="page <= 1" title="Primeira página" @click="page = 1; refreshAll()">«</Button>
        <Button size="sm" variant="outline" :disabled="page <= 1" title="Página anterior" @click="page--; refreshAll()">‹</Button>
        <span class="px-2 py-1">página {{ page }} de {{ totalPages }}</span>
        <Button size="sm" variant="outline" :disabled="page >= totalPages" title="Próxima página" @click="page++; refreshAll()">›</Button>
        <Button size="sm" variant="outline" :disabled="page >= totalPages" title="Última página" @click="page = totalPages; refreshAll()">»</Button>
      </div>
      <span class="text-xs text-muted-foreground">
        mostrando {{ total === 0 ? 0 : (page - 1) * pageSize + 1 }}-{{ Math.min(page * pageSize, total) }} de {{ total }} produtos
      </span>
    </div>
    </template>

    <!-- Toast stack — top-right, auto-dismisses after 5s. Sits above modals
         (z-[60]) so per-link sync results stay visible even mid-dialog. -->
    <div class="fixed top-4 right-4 z-[60] flex flex-col gap-2 w-[min(420px,calc(100vw-2rem))]">
      <TransitionGroup
        enter-active-class="transition duration-150 ease-out"
        enter-from-class="opacity-0 translate-x-4"
        enter-to-class="opacity-100 translate-x-0"
        leave-active-class="transition duration-100 ease-in"
        leave-from-class="opacity-100"
        leave-to-class="opacity-0"
      >
        <div
          v-for="t in toasts"
          :key="t.id"
          class="rounded-lg border-2 shadow-lg px-3 py-2 text-sm bg-background"
          :class="
            t.kind === 'success'
              ? 'border-emerald-400 bg-emerald-50'
              : t.kind === 'error'
              ? 'border-red-400 bg-red-50'
              : 'border-amber-400 bg-amber-50'
          "
        >
          <div class="flex items-start justify-between gap-2">
            <div class="flex-1 min-w-0">
              <div
                class="font-semibold"
                :class="
                  t.kind === 'success'
                    ? 'text-emerald-800'
                    : t.kind === 'error'
                    ? 'text-red-800'
                    : 'text-amber-800'
                "
              >{{ t.title }}</div>
              <ul class="mt-0.5 space-y-0.5 font-mono text-xs">
                <li
                  v-for="(ln, i) in t.lines"
                  :key="i"
                  :class="
                    t.kind === 'success'
                      ? 'text-emerald-700'
                      : t.kind === 'error'
                      ? 'text-red-700'
                      : 'text-amber-700'
                  "
                >{{ ln }}</li>
              </ul>
            </div>
            <button class="text-muted-foreground hover:text-foreground" @click="dismissToast(t.id)">
              <X class="size-3.5" />
            </button>
          </div>
        </div>
      </TransitionGroup>
    </div>

    <!-- Floating multi-select action bar (visible whenever there's at least
         one selected product). Sits above the modals via z-40 so the modals
         (z-50) still render on top. -->
    <Transition
      enter-active-class="transition duration-150 ease-out"
      enter-from-class="opacity-0 translate-y-4"
      enter-to-class="opacity-100 translate-y-0"
      leave-active-class="transition duration-100 ease-in"
      leave-from-class="opacity-100 translate-y-0"
      leave-to-class="opacity-0 translate-y-4"
    >
      <div
        v-if="aba === 'produtos' && selected.size > 0"
        class="product-selection-actions fixed bottom-4 left-1/2 -translate-x-1/2 z-40 rounded-lg border bg-background shadow-xl px-4 py-3 flex items-center gap-3"
      >
        <span class="text-sm font-medium">
          ☑ {{ selected.size }} produto(s) selecionado(s)
        </span>
        <div class="h-5 w-px bg-border" />
        <Button
          v-if="canEdit"
          size="sm"
          variant="outline"
          class="border-cyan-300 text-cyan-700 hover:bg-cyan-50"
          @click="openSyncSelected"
        >
          <RefreshCw class="size-4 mr-1.5" /> Sincronizar Selecionados
        </Button>
        <Button
          v-if="canDelete"
          size="sm"
          variant="outline"
          class="border-red-300 text-red-700 hover:bg-red-50"
          @click="bulkDelete"
        >
          <Trash2 class="size-4 mr-1.5" /> Excluir {{ selected.size }}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          @click="selected = new Set()"
        >
          Limpar
        </Button>
      </div>
    </Transition>

    <!-- Import modal -->
    <div v-if="showImport" class="fixed inset-0 z-50 grid place-items-center bg-black/40" @click.self="showImport = false">
      <div class="product-dialog bg-background rounded-lg shadow-lg w-[min(900px,95vw)] max-h-[90vh] flex flex-col">
        <div class="flex items-center justify-between border-b p-3">
          <h3 class="font-semibold">Importar produtos do Bling</h3>
          <button @click="showImport = false"><X class="size-4" /></button>
        </div>
        <div class="p-3 space-y-3 overflow-auto">
          <div class="flex gap-2 items-center">
            <select v-model="importIntegration" class="h-9 rounded-md border bg-background px-2 text-sm" @change="loadPreview()">
              <option v-for="i in blingIntegrations" :key="i.id" :value="i.id">{{ i.name }}</option>
              <option v-if="blingIntegrations.length === 0" disabled value="">sem integração Bling</option>
            </select>
            <Button size="sm" variant="outline" :disabled="importLoading || !importIntegration" @click="loadPreview()">
              <RefreshCw class="size-3.5" :class="importLoading ? 'animate-spin' : ''" />
              <span class="ml-1.5">{{ importLoading ? 'buscando…' : 'recarregar' }}</span>
            </Button>
          </div>
          <div
            v-if="importLoading"
            class="rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-700"
          >
            Buscando todos os produtos do Bling… isso pode levar alguns segundos para catálogos grandes (2k+ SKUs).
          </div>

          <!-- Counters -->
          <div class="grid grid-cols-4 gap-2 text-center text-xs">
            <div class="rounded-md border bg-muted/30 px-2 py-1.5">
              <div class="text-base font-semibold tabular-nums">{{ importStats.total }}</div>
              <div class="text-muted-foreground">no Bling</div>
            </div>
            <div class="rounded-md border bg-emerald-50 px-2 py-1.5">
              <div class="text-base font-semibold tabular-nums text-emerald-700">{{ importStats.imported }}</div>
              <div class="text-emerald-700">importados</div>
            </div>
            <div class="rounded-md border bg-amber-50 px-2 py-1.5">
              <div class="text-base font-semibold tabular-nums text-amber-700">{{ importStats.novos }}</div>
              <div class="text-amber-700">novos</div>
            </div>
            <div class="rounded-md border bg-blue-50 px-2 py-1.5">
              <div class="text-base font-semibold tabular-nums text-blue-700">{{ importStats.selecionados }}</div>
              <div class="text-blue-700">selecionados</div>
            </div>
          </div>

          <!-- Search + Selecionar Novos -->
          <div class="flex gap-2 items-center">
            <input
              v-model="importSearch"
              placeholder="🔍 Buscar por nome ou SKU..."
              class="h-9 flex-1 rounded-md border bg-background px-3 text-sm"
            />
            <Button
              size="sm"
              variant="outline"
              :disabled="selectablePreview.length === 0 || importLoading"
              class="whitespace-nowrap"
              @click="toggleAllOnPage"
            >
              {{ allPageSelected ? '☐ Desmarcar' : '☑ Selecionar Novos' }}
            </Button>
          </div>

          <div v-if="importResult" class="rounded-md border bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
            Importados: {{ importResult.imported }} · atualizados: {{ importResult.updated }} ·
            ignorados (sem SKU): {{ importResult.skipped_no_sku.length }}
          </div>

          <table class="w-full text-sm">
            <thead>
              <tr>
                <th class="w-8">
                  <input
                    type="checkbox"
                    :disabled="selectablePreview.length === 0 || importLoading"
                    :checked="allPageSelected"
                    :indeterminate.prop="somePageSelected"
                    title="Selecionar todos os novos visíveis"
                    @change="toggleAllOnPage"
                  />
                </th>
                <th>Bling ID</th>
                <th>SKU</th>
                <th>Nome</th>
                <th class="text-right">Preço</th>
                <th class="text-right">Estoque</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="row in filteredPreview"
                :key="row.bling_product_id"
                :class="row.already_imported ? 'opacity-60' : ''"
              >
                <td>
                  <input
                    type="checkbox"
                    :disabled="!row.sku || row.already_imported"
                    :checked="importSelected.has(row.bling_product_id)"
                    @change="
                      importSelected.has(row.bling_product_id)
                        ? importSelected.delete(row.bling_product_id)
                        : importSelected.add(row.bling_product_id);
                      importSelected = new Set(importSelected)
                    "
                  />
                </td>
                <td class="font-mono text-xs">{{ row.bling_product_id }}</td>
                <td class="font-mono text-xs">
                  <span :class="!row.sku ? 'text-red-500' : ''">{{ row.sku || '— sem SKU —' }}</span>
                </td>
                <td>{{ row.name }}</td>
                <td class="text-right tabular-nums">{{ brl(row.price) }}</td>
                <td class="text-right tabular-nums">{{ row.stock ?? '—' }}</td>
                <td>
                  <span
                    v-if="row.already_imported"
                    class="inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium bg-emerald-100 text-emerald-700"
                  >Importado</span>
                  <span
                    v-else-if="!row.sku"
                    class="inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium bg-red-100 text-red-700"
                  >sem SKU</span>
                  <span
                    v-else
                    class="inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium bg-amber-100 text-amber-700"
                  >Novo</span>
                </td>
              </tr>
              <tr v-if="filteredPreview.length === 0">
                <td colspan="7" class="py-4 text-center text-muted-foreground">
                  {{ importLoading ? 'carregando…' : importSearch ? 'nenhum resultado para a busca' : 'nenhum produto nesta página' }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="border-t p-3 flex gap-2 justify-end">
          <Button variant="outline" @click="showImport = false">fechar</Button>
          <Button :disabled="importSelected.size === 0 || importLoading" @click="runImport">
            <Plus class="size-4 mr-1.5" />
            importar {{ importSelected.size }}
          </Button>
        </div>
      </div>
    </div>

    <!-- Atualizar (17/11/2026): produtos novos do Bling + listas de anúncios
         dos marketplaces + Vincular sozinho no fim. Só grava no DaVinci. -->
    <div v-if="showAtualizar" class="fixed inset-0 z-50 grid place-items-center bg-black/40" @click.self="showAtualizar = false">
      <div class="product-dialog bg-background rounded-lg shadow-lg w-[min(620px,95vw)] max-h-[85vh] flex flex-col">
        <div class="flex items-center justify-between border-b p-4">
          <div>
            <h3 class="font-semibold flex items-center gap-2">
              <CloudDownload class="size-5 text-sky-600" />
              Atualizar
            </h3>
            <p class="text-xs text-muted-foreground mt-1">
              {{ activeJob && (activeJob.status === 'running' || activeJob.status === 'pending')
                ? 'Atualizando. Pode fechar esta janela: continua em segundo plano.'
                : 'Traz para o DaVinci o que é novo no Bling e nas listas de anúncios dos marketplaces.' }}
            </p>
          </div>
          <button @click="showAtualizar = false"><X class="size-4" /></button>
        </div>

        <!-- Prévia: nada é gravado antes do "Atualizar agora" -->
        <div v-if="!activeJob" class="p-4 space-y-3 flex-1 overflow-y-auto text-sm">
          <div>
            <div class="font-medium">Listas de anúncios que serão lidas ({{ qtdContasAtualizar }} contas)</div>
            <ul class="mt-1 space-y-1 text-xs">
              <li v-for="g in contasAtualizarPorPlataforma" :key="g.platform">
                <span :class="platformBadgeClass(g.platform)">{{ g.label }} ({{ g.nomes.length }})</span>
                <span class="ml-1 text-muted-foreground">{{ g.nomes.join(', ') }}</span>
              </li>
              <li v-if="contasAtualizarPorPlataforma.length === 0" class="text-muted-foreground">
                Nenhuma conta de marketplace com lista para ler.
              </li>
              <li v-for="g in plataformasForaDoAtualizar" :key="`fora-${g.platform}`" class="text-muted-foreground">
                <template v-if="g.platform === 'amazon'">
                  Amazon: fora da lista (a lista da Amazon na tela é antiga; os vínculos da Amazon são lidos no Vincular).
                </template>
                <template v-else>{{ g.label }}: fora da lista (ainda não tem leitor).</template>
              </li>
            </ul>
          </div>
          <div class="rounded-md border p-3 text-xs">
            <div class="font-medium text-sm">Produtos novos do Bling</div>
            Lê a lista inteira do Bling e cria só os produtos que ainda não existem no DaVinci.
            Não altera, não reativa e não desativa nenhum produto.
          </div>
          <ul class="rounded-md border border-sky-200 bg-sky-50 p-3 text-xs text-sky-800 space-y-1 list-disc pl-6">
            <li>Depois de ler, liga os anúncios aos produtos sozinho (o mesmo Vincular) e segura o robô do Flex até terminar, para ele não desligar o Flex de anúncio que ainda não foi ligado.</li>
            <li>Não grava nada no Bling nem nos marketplaces e não envia estoque (isso é o Sincronizar).</li>
            <li>Leva ~25–45 min (até 1 h se o robô do Flex estiver rodando). Pode fechar esta janela: continua em segundo plano e avisa aqui quando terminar.</li>
          </ul>
          <div v-if="avisoMassa || jobActive" class="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800">
            {{ avisoMassa || descreverMassa(massaAtiva) }}
          </div>
        </div>

        <!-- Andamento e resultado -->
        <div v-else class="p-4 space-y-3 flex-1 overflow-y-auto text-sm">
          <div v-if="activeJob.status === 'running' || activeJob.status === 'pending'" class="flex items-center gap-2">
            <Loader2 class="size-4 animate-spin text-sky-600" />
            <span class="text-muted-foreground">{{ atualizarEtapa }}</span>
          </div>
          <div>
            <div class="flex justify-between text-xs mb-1">
              <span class="text-muted-foreground tabular-nums">
                {{ atualizarContasLidas.length }} de {{ atualizarTotalContas }} contas lidas
              </span>
              <span class="tabular-nums font-semibold text-sky-600">
                {{ pct(atualizarContasLidas.length, atualizarTotalContas) }}%
              </span>
            </div>
            <div class="h-2 bg-muted rounded overflow-hidden">
              <div class="h-full bg-sky-500 transition-all" :style="`width: ${pct(atualizarContasLidas.length, atualizarTotalContas)}%`" />
            </div>
            <div v-if="atualizarVincularProgresso.total" class="text-xs text-muted-foreground tabular-nums mt-1">
              Vincular: {{ fmtN(atualizarVincularProgresso.feitas) }} de {{ fmtN(atualizarVincularProgresso.total) }} contas
            </div>
          </div>

          <!-- Uma linha por conta -->
          <ul v-if="atualizarContasLidas.length" class="max-h-56 overflow-auto text-xs space-y-0.5 bg-muted/30 p-2 rounded">
            <li
              v-for="(d, idx) in atualizarContasLidas"
              :key="idx"
              :class="d.resultado === 'ok' ? 'text-emerald-700' : 'text-amber-700'"
            >
              <span class="font-bold">{{ atualizarContaIcone(d) }}</span>
              <span class="uppercase font-semibold ml-1">{{ nomePlataforma(d.plataforma) }}</span>
              {{ d.nome }}: {{ atualizarContaTexto(d) }}
            </li>
          </ul>

          <!-- Resultado final -->
          <div
            v-if="activeJob.status === 'succeeded' || activeJob.status === 'failed' || activeJob.status === 'cancelled'"
            class="rounded-md border-2 px-3 py-2 text-sm space-y-2"
            :class="atualizarSummaryClass(activeJob)"
          >
            <div v-if="jobInterrompido(activeJob)" class="text-xs font-medium">
              Interrompido no meio (publicação ou reinício do robô): o que já foi gravado está nas linhas acima.
              Clique em Atualizar de novo.
            </div>
            <div v-else-if="atualizarErroTexto(activeJob)" class="text-xs">{{ atualizarErroTexto(activeJob) }}</div>
            <template v-if="!jobInterrompido(activeJob)">
            <div v-if="atualizarResultado(activeJob).anuncios">
              <div class="font-semibold">Listas de anúncios</div>
              <div class="text-xs">
                {{ fmtN(atualizarResultado(activeJob).anuncios.ok) }} de {{ fmtN(atualizarResultado(activeJob).anuncios.contas) }} contas lidas ·
                {{ fmtN(atualizarResultado(activeJob).anuncios.novos) }} anúncios novos ·
                {{ fmtN(atualizarResultado(activeJob).anuncios.atualizados) }} atualizados<template v-if="atualizarResultado(activeJob).anuncios.falhas"> ·
                {{ fmtN(atualizarResultado(activeJob).anuncios.falhas) }} conta(s) com falha</template><template v-if="atualizarResultado(activeJob).anuncios.puladas"> ·
                {{ fmtN(atualizarResultado(activeJob).anuncios.puladas) }} pulada(s)</template>
              </div>
              <div v-if="atualizarSemLeituraTexto(activeJob)" class="text-xs opacity-80">
                {{ atualizarSemLeituraTexto(activeJob) }}
              </div>
            </div>
            <div v-if="atualizarResultado(activeJob).flex?.conseguiu === false" class="text-xs font-medium">
              O robô do Flex estava rodando e não terminou em 10 min: as listas do Mercado Livre e da Shopee não foram gravadas.
              Tente de novo em alguns minutos.
            </div>
            <div v-else-if="(atualizarResultado(activeJob).flex?.esperou_s ?? 0) > 0" class="text-xs opacity-80">
              Esperou {{ fmtN(atualizarResultado(activeJob).flex.esperou_s) }} s o robô do Flex terminar a rodada dele.
            </div>
            <div>
              <div class="font-semibold">Produtos novos do Bling</div>
              <div v-if="atualizarBlingTexto(activeJob).pulado" class="text-xs">
                Pulado: {{ atualizarBlingTexto(activeJob).pulado }}.
              </div>
              <div v-else-if="atualizarBlingTexto(activeJob).erro" class="text-xs">
                Não deu para ler o Bling: {{ atualizarBlingTexto(activeJob).erro }}
              </div>
              <template v-else>
                <div class="text-xs">{{ atualizarBlingTexto(activeJob).linha }}</div>
                <div v-if="atualizarBlingTexto(activeJob).skus.length" class="text-[11px] font-mono opacity-80 break-words">
                  Criados: {{ atualizarBlingTexto(activeJob).skus.join(', ') }}
                </div>
              </template>
            </div>
            <div v-if="atualizarResultado(activeJob).vincular">
              <div class="font-semibold">Vincular (rodou sozinho)</div>
              <div v-if="vincularIncompleto(activeJob)" class="text-xs font-medium">
                O Vincular não terminou<template v-if="atualizarResultado(activeJob).vincular_erro">: {{ atualizarResultado(activeJob).vincular_erro }}</template>.
                Rode o Vincular antes da próxima rodada do robô do Flex.
                <ul v-if="atualizarVincularPendentes.length" class="mt-0.5 font-normal">
                  <li v-for="(d, i) in atualizarVincularPendentes" :key="i">
                    ⚠ {{ nomePlataforma(d.platform) }} {{ d.integration_name }}{{ d.error ? ` — ${d.error}` : '' }}
                  </li>
                </ul>
              </div>
              <div class="text-xs">{{ vincularLinhaPrincipal(atualizarResultado(activeJob).vincular) }}</div>
              <div class="text-xs opacity-80">{{ vincularLinhaDetalhe(atualizarResultado(activeJob).vincular) }}</div>
              <div v-if="vincularLinhaLista(atualizarResultado(activeJob).vincular)" class="text-xs opacity-80">
                {{ vincularLinhaLista(atualizarResultado(activeJob).vincular) }}
              </div>
              <details v-if="vincularMudancas(atualizarResultado(activeJob).vincular).length" class="text-xs mt-1">
                <summary class="cursor-pointer underline">Ver {{ vincularMudancas(atualizarResultado(activeJob).vincular).length }} mudança(s)</summary>
                <ul class="mt-1 font-mono text-[11px] space-y-0.5 max-h-40 overflow-auto">
                  <li v-for="(m, i) in vincularMudancas(atualizarResultado(activeJob).vincular)" :key="i">
                    {{ m.conta ? `${m.conta}: ` : '' }}{{ textoMudanca(m) }}
                  </li>
                </ul>
              </details>
            </div>
            <div class="text-xs font-medium">
              Para enviar o estoque aos anúncios novos ou movidos, clique em Sincronizar.
            </div>
            </template>
          </div>
        </div>

        <div class="border-t p-3 flex gap-2 justify-end">
          <Button v-if="!activeJob" variant="outline" @click="showAtualizar = false">Cancelar</Button>
          <Button
            v-if="!activeJob"
            class="bg-sky-600 hover:bg-sky-700 text-white"
            :disabled="jobActive || qtdContasAtualizar === 0"
            @click="runAtualizar"
          >
            <CloudDownload class="size-4 mr-1.5" /> Atualizar agora
          </Button>
          <Button
            v-if="activeJob && (activeJob.status === 'succeeded' || activeJob.status === 'failed' || activeJob.status === 'cancelled')"
            variant="outline"
            @click="showAtualizar = false"
          >
            Fechar
          </Button>
          <Button v-else-if="activeJob" variant="outline" @click="showAtualizar = false">Minimizar</Button>
        </div>
      </div>
    </div>

    <!-- Vincular (era o "Vincular Automático"): lê os anúncios ao vivo e acerta
         o SKU e o vínculo SÓ no DaVinci. -->
    <div v-if="showAutoLink" class="fixed inset-0 z-50 grid place-items-center bg-black/40" @click.self="showAutoLink = false">
      <div class="product-dialog bg-background rounded-lg shadow-lg w-[min(600px,95vw)] max-h-[85vh] flex flex-col">
        <div class="flex items-center justify-between border-b p-4">
          <div>
            <h3 class="font-semibold flex items-center gap-2">
              <Zap class="size-5 text-emerald-600" />
              Vincular
            </h3>
            <p class="text-xs text-muted-foreground mt-1">
              Lê os anúncios ao vivo e atualiza o SKU e o vínculo SÓ no DaVinci.
              Não muda nada no marketplace e não envia estoque.
            </p>
          </div>
          <button @click="showAutoLink = false"><X class="size-4" /></button>
        </div>

        <!-- Selection phase -->
        <div v-if="!activeJob" class="p-4 space-y-4 flex-1 overflow-y-auto">
          <div class="space-y-2">
            <div class="flex items-center justify-between">
              <label class="text-sm font-medium">Contas que serão lidas:</label>
              <div class="flex gap-2">
                <Button variant="ghost" size="sm" class="text-xs h-7 px-2" @click="selectedAutoLinkIds = new Set(contasVincular.map(i => i.id))">Todas</Button>
                <Button variant="ghost" size="sm" class="text-xs h-7 px-2" @click="selectedAutoLinkIds = new Set()">Nenhuma</Button>
              </div>
            </div>
            <div class="rounded-lg border max-h-[240px] overflow-y-auto">
              <p v-if="contasVincular.length === 0" class="text-sm text-muted-foreground text-center py-4">
                Nenhuma conta de marketplace conectada.
              </p>
              <label
                v-for="(integ, idx) in contasVincular"
                :key="integ.id"
                class="flex items-center gap-3 px-3 py-2.5 hover:bg-muted/40 cursor-pointer transition-colors"
                :class="idx !== contasVincular.length - 1 ? 'border-b' : ''"
              >
                <input
                  type="checkbox"
                  :checked="selectedAutoLinkIds.has(integ.id)"
                  @change="toggleSelectedAutoLink(integ.id)"
                />
                <span :class="platformBadgeClass(integ.platform)">{{ integ.platform }}</span>
                <span class="text-sm truncate">{{ integ.name }}</span>
              </label>
            </div>
            <p v-if="contasVincular.length > 0" class="text-xs text-muted-foreground">
              {{ selectedAutoLinkIds.size }} de {{ contasVincular.length }} conta(s) selecionada(s)
            </p>
            <p v-if="plataformasForaDoVincular.length" class="text-xs text-muted-foreground">
              {{ plataformasForaDoVincular.map(g => g.label).join('/') }}: sem leitor de vínculo (ficam de fora).
            </p>
          </div>

          <p
            v-if="aba === 'produtos' && produtosCarregadosDe && (saude.sku_divergente || saude.mortos)"
            class="text-xs text-muted-foreground"
          >
            Agora em {{ DEPARTAMENTOS.find(d => d.value === produtosCarregadosDe)?.label }}:
            {{ fmtN(saude.sku_divergente) }} vínculo(s) com SKU do anúncio diferente do produto ·
            {{ fmtN(saude.mortos) }} vínculo(s) morto(s).
          </p>

          <div class="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-800 space-y-1">
            <p>
              <strong>O que pode mudar (só no DaVinci):</strong> cria o vínculo que falta, move o vínculo cujo SKU
              mudou no anúncio, atualiza o SKU guardado, marca ou revive vínculo morto e acerta o produto mostrado na
              aba Anúncios.
            </p>
            <p><strong>O que não muda:</strong> nada no marketplace, nada no Bling, nenhum estoque enviado.</p>
            <p>O SKU tem de ser igual no Bling e no anúncio (o sufixo de lote, como .sp ou .ci, conta).</p>
          </div>
          <div v-if="avisoMassa || jobActive" class="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800">
            {{ avisoMassa || descreverMassa(massaAtiva) }}
          </div>
        </div>

        <!-- Progress phase -->
        <div v-else class="p-4 space-y-3 flex-1 overflow-y-auto">
          <!-- Current marketplace -->
          <div v-if="activeJob.status === 'running' || activeJob.status === 'pending'" class="text-sm flex items-center gap-2">
            <Loader2 class="size-4 animate-spin text-emerald-600" />
            <span class="text-muted-foreground">Processando:</span>
            <span class="font-medium">{{ autoLinkCurrent(jobDetails) || '…' }}</span>
          </div>

          <!-- Progress bar (integrations done / total) -->
          <div>
            <div class="flex justify-between text-xs mb-1">
              <span class="text-muted-foreground tabular-nums">
                {{ activeJob.details_count ?? 0 }} / {{ activeJob.total }} conta(s)
              </span>
              <span class="tabular-nums font-semibold text-emerald-600">
                {{ pct(activeJob.details_count ?? 0, activeJob.total) }}%
              </span>
            </div>
            <div class="h-2 bg-muted rounded overflow-hidden">
              <div
                class="h-full bg-emerald-500 transition-all"
                :style="`width: ${pct(activeJob.details_count ?? 0, activeJob.total)}%`"
              />
            </div>
          </div>

          <!-- Running counter -->
          <div class="text-sm">
            <span class="font-semibold tabular-nums text-emerald-700">{{ autoLinkTotals(activeJob).created }}</span>
            <span class="text-muted-foreground"> vinculados até agora</span>
            <span v-if="autoLinkTotals(activeJob).pending > 0" class="ml-2 text-amber-700">
              · {{ autoLinkTotals(activeJob).pending }} conta(s) pendente(s)
            </span>
          </div>

          <!-- Final summary box -->
          <div
            v-if="activeJob.status === 'succeeded' || activeJob.status === 'failed' || activeJob.status === 'cancelled'"
            class="rounded-md border-2 px-3 py-2 text-sm"
            :class="autoLinkSummaryClass(activeJob)"
          >
            <div class="text-center">
              <!-- Cortado no meio (result vazio): os números estariam zerados e
                   contradiriam as contas abaixo. -->
              <div v-if="!activeJob.result" class="text-sm font-medium">
                {{ jobInterrompido(activeJob)
                  ? 'Interrompido no meio (publicação ou reinício do robô): o que já foi vinculado está nas contas abaixo. Clique em Vincular de novo.'
                  : (activeJob.error || 'Parou sem resumo.') }}
              </div>
              <template v-else>
              <div class="text-base font-bold tabular-nums">{{ vincularLinhaPrincipal(activeJob.result) }}</div>
              <div class="text-xs mt-0.5 opacity-90">{{ vincularLinhaDetalhe(activeJob.result) }}</div>
              <div v-if="vincularLinhaLista(activeJob.result)" class="text-xs mt-0.5 opacity-90">
                {{ vincularLinhaLista(activeJob.result) }}
              </div>
              <div v-if="vincularSkusSemProduto(activeJob.result).length" class="text-[11px] mt-0.5 font-mono opacity-80 break-words">
                SKUs sem produto no DaVinci: {{ vincularSkusSemProduto(activeJob.result).join(', ') }}
              </div>
              <div v-if="activeJob.error" class="text-xs mt-1 font-mono opacity-90">{{ activeJob.error }}</div>
              </template>
            </div>
            <!-- Per-integration breakdown -->
            <ul class="mt-2 space-y-0.5 text-xs">
              <li v-for="(b, idx) in autoLinkBreakdown(jobDetails, activeJob.result)" :key="idx">
                <div class="flex items-center gap-1">
                  <span class="font-bold">{{ b.bad ? '⚠' : '✓' }}</span>
                  <span class="uppercase font-semibold">{{ b.platform }}</span>
                  <span>({{ b.integration }}):</span>
                  <template v-if="b.bad">
                    <span class="text-amber-700 font-mono text-[11px]">pendente: não deu para ler<template v-if="b.error"> · {{ b.error }}</template></span>
                  </template>
                  <template v-else>
                    <span>{{ b.created }} vinculados, {{ b.already }} já existentes, {{ b.not_found }} sem produto<template v-if="b.sku_vazio">, {{ b.sku_vazio }} sem SKU</template><template v-if="b.sku_ambiguo">, {{ b.sku_ambiguo }} SKU duplicado ({{ b.sku_ambiguo_sample.join(', ') }})</template></span>
                  </template>
                </div>
                <details v-if="b.mudancas.length" class="ml-4">
                  <summary class="cursor-pointer underline">ver {{ b.mudancas.length }} movido(s)/criado(s)</summary>
                  <ul class="font-mono text-[11px] space-y-0.5">
                    <li v-for="(m, i) in b.mudancas" :key="i">{{ textoMudanca(m) }}</li>
                  </ul>
                </details>
              </li>
            </ul>
            <div class="mt-2 text-xs font-medium">
              Para enviar o estoque aos anúncios movidos ou criados, clique em Sincronizar.
            </div>
            <div class="text-[11px] opacity-70">Variação do Mercado Livre sem SKU no anúncio é pulada sem contar.</div>
          </div>

          <!-- Live log -->
          <ul class="max-h-48 overflow-auto text-xs space-y-0.5 font-mono bg-muted/30 p-2 rounded">
            <li
              v-for="(d, idx) in tailDetails(jobDetails, 100)"
              :key="idx"
              class="whitespace-pre-wrap"
              :class="(d.result === 'failed' || d.result === 'pending') ? 'text-amber-600' : 'text-emerald-600'"
            >
              <span class="font-bold">{{ (d.result === 'failed' || d.result === 'pending') ? '⚠' : '✓' }}</span>
              [{{ (d.platform || '').toUpperCase() }}] {{ d.integration_name || (d.integration_id || '').slice(0, 8) }}:
              <template v-if="d.result === 'failed' || d.result === 'pending'">pendente: não deu para ler<template v-if="d.error"> · {{ d.error }}</template></template>
              <template v-else>{{ d.created ?? 0 }} vinculados · {{ d.already_present ?? 0 }} já existentes · {{ d.not_found ?? 0 }} sem produto<template v-if="d.sku_vazio"> · {{ d.sku_vazio }} sem SKU</template><template v-if="d.sku_ambiguo"> · {{ d.sku_ambiguo }} SKU duplicado</template></template>
            </li>
            <li v-if="!jobDetails.length" class="text-muted-foreground italic">
              processando…
            </li>
          </ul>
        </div>

        <!-- Footer -->
        <div class="border-t p-3 flex gap-2 justify-end">
          <Button v-if="!activeJob" variant="outline" @click="showAutoLink = false">Cancelar</Button>
          <Button
            v-if="!activeJob"
            class="bg-emerald-600 hover:bg-emerald-700 text-white"
            :disabled="selectedAutoLinkIds.size === 0 || jobActive"
            @click="runAutoLink"
          >
            <Zap class="size-4 mr-1.5" />
            Vincular agora ({{ selectedAutoLinkIds.size }} contas)
          </Button>
          <Button
            v-if="activeJob && (activeJob.status === 'succeeded' || activeJob.status === 'failed' || activeJob.status === 'cancelled')"
            variant="outline"
            @click="showAutoLink = false"
          >
            Fechar
          </Button>
          <Button
            v-else-if="activeJob"
            variant="outline"
            @click="showAutoLink = false"
          >
            Minimizar
          </Button>
        </div>
      </div>
    </div>

    <!-- Sincronizar (era o "Sincronizar Todos"; o "Sincronizar Selecionados"
         usa este mesmo diálogo, só com os produtos marcados). -->
    <div v-if="showSyncAll" class="fixed inset-0 z-50 grid place-items-center bg-black/40" @click.self="showSyncAll = false">
      <div class="product-dialog bg-background rounded-lg shadow-lg w-[min(560px,95vw)] max-h-[85vh] flex flex-col">
        <div class="flex items-center justify-between border-b p-4">
          <div>
            <h3 class="font-semibold flex items-center gap-2">
              <RefreshCw class="size-5 text-cyan-600" :class="activeJob?.status === 'running' || activeJob?.status === 'pending' ? 'animate-spin' : ''" />
              {{ syncAllProductIds?.length ? `Sincronizar ${syncAllProductIds.length} produto(s) selecionado(s)` : 'Sincronizar' }}
            </h3>
            <p class="text-xs text-muted-foreground mt-1">
              {{ activeJob && (activeJob.status === 'running' || activeJob.status === 'pending')
                ? 'Sincronização em andamento. Pode fechar esta janela: continua em segundo plano.'
                : 'Envia o estoque do Bling, relido agora, para os anúncios vinculados das contas marcadas. Não altera nada no Bling.' }}
            </p>
          </div>
          <button @click="showSyncAll = false"><X class="size-4" /></button>
        </div>

        <!-- Selection phase -->
        <div v-if="!activeJob" class="p-4 space-y-4 flex-1 overflow-y-auto">
          <div class="space-y-2">
            <div class="flex items-center justify-between">
              <label class="text-sm font-medium">Selecione as contas:</label>
              <div class="flex gap-2">
                <Button variant="ghost" size="sm" class="text-xs h-7 px-2" @click="selectedSyncAllIds = new Set(marketplaceIntegrations.map(i => i.id))">Todas</Button>
                <Button variant="ghost" size="sm" class="text-xs h-7 px-2" @click="selectedSyncAllIds = new Set()">Nenhuma</Button>
              </div>
            </div>
            <div class="rounded-lg border max-h-[280px] overflow-y-auto">
              <p v-if="marketplaceIntegrations.length === 0" class="text-sm text-muted-foreground text-center py-4">
                Nenhuma integração de marketplace conectada.
              </p>
              <label
                v-for="(integ, idx) in marketplaceIntegrationsSorted"
                :key="integ.id"
                class="flex items-center gap-3 px-3 py-2.5 hover:bg-muted/40 cursor-pointer transition-colors"
                :class="idx !== marketplaceIntegrationsSorted.length - 1 ? 'border-b' : ''"
              >
                <input
                  type="checkbox"
                  :checked="selectedSyncAllIds.has(integ.id)"
                  @change="toggleSelectedSyncAll(integ.id)"
                />
                <span :class="platformBadgeClass(integ.platform)">{{ integ.platform }}</span>
                <span class="text-sm flex-1 truncate">{{ integ.name }}</span>
              </label>
            </div>
            <p v-if="marketplaceIntegrations.length > 0" class="text-xs text-muted-foreground">
              {{ selectedSyncAllIds.size }} de {{ marketplaceIntegrations.length }} conta(s) selecionada(s)
            </p>
          </div>

          <div class="rounded-lg border border-cyan-200 bg-cyan-50 p-3">
            <p class="text-xs text-cyan-700">
              Com todas as contas leva ~1 h e divide a fila do Bling com os pedidos.
              <strong>Não dá para cancelar depois de começar.</strong>
              Roda em segundo plano: pode fechar esta janela.
            </p>
          </div>
          <div v-if="avisoMassa || jobActive" class="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800">
            {{ avisoMassa || descreverMassa(massaAtiva) }}
          </div>
        </div>

        <!-- Progress phase -->
        <div v-else class="p-4 space-y-3 flex-1 overflow-y-auto">
          <!-- Current SKU -->
          <div v-if="activeJob.status === 'running' || activeJob.status === 'pending'" class="text-sm">
            <span class="text-muted-foreground">Sincronizando:</span>
            <span class="ml-1 font-mono font-medium">{{ lastProcessedSku(jobDetails) || '…' }}</span>
          </div>

          <!-- Progress bar + % -->
          <div>
            <div class="flex justify-between text-xs mb-1">
              <span class="text-muted-foreground tabular-nums">{{ activeJob.processed }} / {{ activeJob.total }} links</span>
              <span class="tabular-nums font-semibold text-cyan-600">
                {{ pct(activeJob.processed, activeJob.total) }}%
              </span>
            </div>
            <div class="h-2 bg-muted rounded overflow-hidden">
              <div
                class="h-full bg-cyan-500 transition-all"
                :style="`width: ${pct(activeJob.processed, activeJob.total)}%`"
              />
            </div>
          </div>

          <!-- Colored counters -->
          <div class="flex flex-wrap gap-2 text-xs">
            <span class="inline-flex items-center gap-1 rounded px-2 py-1 bg-emerald-50 text-emerald-700 border border-emerald-200">
              <span class="font-semibold">✓ {{ syncCounters(activeJob).ok }}</span>
              <span class="text-emerald-600">sincronizados</span>
            </span>
            <span v-if="syncCounters(activeJob).skipped > 0" class="inline-flex items-center gap-1 rounded px-2 py-1 bg-amber-50 text-amber-700 border border-amber-200">
              <span class="font-semibold">⊘ {{ syncCounters(activeJob).skipped }}</span>
              <span class="text-amber-600">pulados</span>
            </span>
            <span v-if="syncCounters(activeJob).retryable > 0" class="inline-flex items-center gap-1 rounded px-2 py-1 bg-amber-50 text-amber-700 border border-amber-200">
              <span class="font-semibold">↻ {{ syncCounters(activeJob).retryable }}</span>
              <span class="text-amber-600">retry</span>
            </span>
            <span v-if="syncCounters(activeJob).fatal > 0" class="inline-flex items-center gap-1 rounded px-2 py-1 bg-red-50 text-red-700 border border-red-200">
              <span class="font-semibold">✗ {{ syncCounters(activeJob).fatal }}</span>
              <span class="text-red-600">erros</span>
            </span>
            <span v-if="syncCounters(activeJob).requires_review > 0" class="inline-flex items-center gap-1 rounded px-2 py-1 bg-red-50 text-red-700 border border-red-200">
              <span class="font-semibold">⚠ {{ syncCounters(activeJob).requires_review }}</span>
              <span class="text-red-600">revisar</span>
            </span>
          </div>

          <!-- Final summary (only when done) -->
          <div
            v-if="activeJob.status === 'succeeded' || activeJob.status === 'failed' || activeJob.status === 'cancelled'"
            class="rounded-md border-2 px-3 py-2 text-sm text-center"
            :class="syncSummaryClass(activeJob)"
          >
            <div class="text-lg font-bold tabular-nums">
              {{ syncCounters(activeJob).ok }} sincronizado(s)
            </div>
            <div class="text-xs mt-0.5 opacity-90">
              {{ activeJob.processed }} processado(s) ·
              {{ syncCounters(activeJob).skipped }} pulado(s) ·
              {{ syncCounters(activeJob).fatal + syncCounters(activeJob).requires_review }} erro(s)
            </div>
            <div v-if="falhasProduto(activeJob)" class="text-xs mt-1 font-medium">
              {{ fmtN(falhasProduto(activeJob)) }} produto(s) deram erro e não foram enviados (rode o
              Sincronizar de novo; se repetir, abra o produto e use "Sincronizar"):
              <span v-if="falhasAmostra(activeJob).length" class="font-mono font-normal break-words">
                {{ falhasAmostra(activeJob).join(', ') }}{{ falhasProduto(activeJob) > falhasAmostra(activeJob).length ? '…' : '' }}
              </span>
            </div>
            <div v-if="activeJob.error" class="text-xs mt-1 font-mono opacity-90">{{ activeJob.error }}</div>
          </div>

          <!-- Log: last 100 entries with colored status -->
          <ul class="max-h-60 overflow-auto text-xs space-y-0.5 font-mono bg-muted/30 p-2 rounded">
            <li
              v-for="(d, idx) in tailDetails(jobDetails, 100)"
              :key="idx"
              class="whitespace-pre-wrap"
              :class="detailColorClass(d)"
            >
              <span class="font-bold">{{ detailIcon(d) }}</span> {{ formatDetail(d) }}
            </li>
            <li v-if="!jobDetails.length" class="text-muted-foreground italic">
              processando…
            </li>
          </ul>
        </div>

        <!-- Forçar: fura as travas do marketplace e reativa anúncios pausados
             pelo ML por falta de estoque (mesma trava do sync individual). -->
        <label
          v-if="!activeJob"
          class="border-t px-3 pt-3 flex items-start gap-2 text-xs cursor-pointer select-none"
        >
          <input type="checkbox" v-model="forceSyncAll" class="mt-0.5" />
          <span>
            <span class="font-medium">Forçar (reativar anúncios pausados)</span>
            <span class="block text-muted-foreground">
              Empurra o estoque mesmo em anúncios que o ML pausou por estarem
              zerados, religando-os. Use ao repor estoque depois de zerar.
            </span>
          </span>
        </label>

        <!-- Footer -->
        <div class="border-t p-3 flex gap-2 justify-end">
          <Button v-if="!activeJob" variant="outline" @click="showSyncAll = false">Cancelar</Button>
          <Button
            v-if="!activeJob"
            :disabled="selectedSyncAllIds.size === 0 || jobActive"
            @click="runSyncAll"
          >
            <RefreshCw class="size-4 mr-1.5" />
            Sincronizar agora ({{ selectedSyncAllIds.size }} contas)
          </Button>
          <Button
            v-if="activeJob && (activeJob.status === 'succeeded' || activeJob.status === 'failed' || activeJob.status === 'cancelled')"
            variant="outline"
            @click="showSyncAll = false"
          >
            Fechar
          </Button>
          <Button
            v-else-if="activeJob"
            variant="outline"
            @click="showSyncAll = false"
          >
            Minimizar
          </Button>
        </div>
      </div>
    </div>

    <!-- Organizar (só ADM, 17/11/2026): esconde da TELA o que não vale mais.
         Nada é apagado; dá para desfazer por lote, por motivo ou por item. -->
    <div v-if="showOrganizar" class="fixed inset-0 z-50 grid place-items-center bg-black/40" @click.self="showOrganizar = false">
      <div class="product-dialog bg-background rounded-lg shadow-lg w-[min(860px,95vw)] max-h-[90vh] flex flex-col">
        <div class="flex items-center justify-between border-b p-4">
          <div>
            <h3 class="font-semibold flex items-center gap-2">
              <EyeOff class="size-5 text-violet-600" />
              Organizar
            </h3>
            <p class="text-xs text-muted-foreground mt-1">
              Só esconde da tela do DaVinci. Nada é apagado, nada muda no Bling nem nos marketplaces, o estoque
              continua sendo enviado (inclusive o 0 dos excluídos). Dá para desfazer.
            </p>
          </div>
          <button @click="showOrganizar = false"><X class="size-4" /></button>
        </div>

        <div class="flex gap-1 border-b px-4 pt-2" aria-label="Abas do Organizar">
          <button
            type="button"
            class="rounded-t px-3 py-1.5 text-sm"
            :class="organizarAba === 'organizar' ? 'bg-muted font-medium' : 'text-muted-foreground hover:text-foreground'"
            :aria-pressed="organizarAba === 'organizar'"
            @click="organizarAba = 'organizar'; orgLista = null"
          >
            Organizar
          </button>
          <button
            type="button"
            class="rounded-t px-3 py-1.5 text-sm"
            :class="organizarAba === 'ocultos' ? 'bg-muted font-medium' : 'text-muted-foreground hover:text-foreground'"
            :aria-pressed="organizarAba === 'ocultos'"
            @click="organizarAba = 'ocultos'; orgLista = null"
          >
            Ocultos ({{ fmtN(orgPrevia?.ocultos.total) }})
          </button>
        </div>

        <div class="p-4 space-y-3 flex-1 overflow-y-auto text-sm">
          <div v-if="orgErro" class="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800">
            {{ orgErro }}
          </div>
          <div v-if="orgCarregando && !orgPrevia" class="flex items-center gap-2 text-muted-foreground">
            <Loader2 class="size-4 animate-spin" /> contando…
          </div>

          <!-- Resultado do último "Ocultar" -->
          <div v-if="orgResultado" class="rounded-md border-2 border-emerald-300 bg-emerald-50 px-3 py-2 text-emerald-800">
            <div class="font-semibold">
              Ocultados: {{ fmtN(orgResultado.ocultados) }}<template v-if="orgLoteDoResultado">
              (lote de {{ fmtQuando(orgLoteDoResultado.oculto_em) }}<template v-if="orgLoteDoResultado.oculto_por_nome"> por {{ orgLoteDoResultado.oculto_por_nome }}</template>)</template>
            </div>
            <div class="text-xs">{{ orgPorChaveTexto(orgResultado.por_chave) }}</div>
            <div v-if="orgResultado.sairam_desde_previa" class="text-xs">
              {{ fmtN(orgResultado.sairam_desde_previa) }} saíram da lista desde a prévia (voltaram a valer) e ficaram na tela.
              Os que entraram depois ficam para a próxima.
            </div>
            <div v-if="!orgResultado.ocultados" class="text-xs">
              Nada foi escondido: tudo o que estava na prévia já tinha saído da lista.
            </div>
            <Button
              v-if="orgResultado.lote_id"
              size="sm"
              variant="outline"
              class="mt-2"
              :disabled="orgReexibindo"
              @click="reexibirOrganizar({ lote_id: orgResultado.lote_id }, 'Lote desfeito')"
            >
              <Eye class="size-4 mr-1.5" /> Desfazer este lote
            </Button>
          </div>

          <!-- Aba Organizar: a prévia, uma caixa por motivo -->
          <template v-if="orgPrevia && organizarAba === 'organizar'">
            <div
              v-for="c in orgPrevia.chaves"
              :key="c.chave"
              class="rounded-md border p-3"
              :class="orgMarcadas.has(c.chave) && c.quantidade > 0 ? 'border-violet-300 bg-violet-50/40' : ''"
            >
              <label class="flex items-start gap-2" :class="c.quantidade > 0 ? 'cursor-pointer' : 'opacity-60'">
                <input
                  type="checkbox"
                  class="mt-1"
                  :checked="orgMarcadas.has(c.chave)"
                  :disabled="c.quantidade === 0"
                  @change="alternarChaveOrganizar(c.chave)"
                />
                <span class="flex-1">
                  <span class="font-medium">{{ c.rotulo }}</span>
                  <span class="ml-1 tabular-nums">— {{ fmtN(c.quantidade) }}</span>
                  <span class="block text-xs text-muted-foreground">{{ c.explicacao }}</span>
                </span>
              </label>
              <button
                v-if="c.quantidade > 0"
                type="button"
                class="ml-6 mt-1 text-xs underline text-violet-700"
                @click="verListaOrganizar(c.chave, false)"
              >
                Ver lista
              </button>
            </div>
            <p class="text-xs text-muted-foreground">
              Prévia válida até {{ fmtQuando(orgPrevia.expira_em) }}. Nada é gravado antes do botão "Ocultar".
            </p>
          </template>

          <!-- Aba Ocultos: lotes e motivos, com "Mostrar de novo" -->
          <template v-if="orgPrevia && organizarAba === 'ocultos'">
            <p v-if="!orgPrevia.ocultos.total" class="text-muted-foreground">Nada escondido agora.</p>
            <div v-if="orgPrevia.lotes.length" class="space-y-1">
              <div class="font-medium">Lotes</div>
              <div
                v-for="l in orgPrevia.lotes"
                :key="l.lote_id"
                class="flex flex-wrap items-center gap-2 rounded-md border px-3 py-2 text-xs"
              >
                <span class="flex-1">
                  {{ fmtQuando(l.oculto_em) }}<template v-if="l.oculto_por_nome"> por {{ l.oculto_por_nome }}</template>
                  — {{ fmtN(l.quantidade) }} item(ns)
                  <span class="block text-muted-foreground">{{ orgPorChaveTexto(l.por_chave) }}</span>
                </span>
                <Button
                  size="sm"
                  variant="outline"
                  :disabled="orgReexibindo"
                  @click="reexibirOrganizar({ lote_id: l.lote_id }, `Lote de ${fmtQuando(l.oculto_em)}`)"
                >
                  <Eye class="size-4 mr-1.5" /> Mostrar de novo
                </Button>
              </div>
            </div>
            <div v-if="orgPrevia.ocultos.total" class="space-y-1">
              <div class="font-medium">Por motivo</div>
              <template v-for="(n, chave) in orgPrevia.ocultos.por_chave" :key="chave">
                <div v-if="n" class="flex flex-wrap items-center gap-2 rounded-md border px-3 py-2 text-xs">
                  <span class="flex-1">{{ orgRotulo(String(chave)) }} — {{ fmtN(n) }}</span>
                  <button type="button" class="underline text-violet-700" @click="verListaOrganizar(String(chave), true)">Ver lista</button>
                  <Button
                    size="sm"
                    variant="outline"
                    :disabled="orgReexibindo"
                    @click="reexibirOrganizar({ chave: String(chave) }, orgRotulo(String(chave)))"
                  >
                    <Eye class="size-4 mr-1.5" /> Mostrar todos de novo
                  </Button>
                </div>
              </template>
            </div>
          </template>

          <!-- Lista paginada (100 por página) de um motivo -->
          <div v-if="orgLista" class="rounded-md border">
            <div class="flex flex-wrap items-center gap-2 border-b bg-muted/40 px-3 py-2 text-xs">
              <span class="flex-1 font-medium">
                {{ orgLista.ocultos ? 'Escondidos' : 'Vão ser escondidos' }}: {{ orgRotulo(orgLista.chave) }}
                <template v-if="orgLista.dados"> — {{ fmtN(orgLista.dados.total) }}</template>
              </span>
              <Loader2 v-if="orgLista.carregando" class="size-4 animate-spin" />
              <template v-if="orgLista.dados && orgPaginas(orgLista.dados) > 1">
                <Button size="sm" variant="outline" :disabled="orgLista.pagina <= 1 || orgLista.carregando" @click="verListaOrganizar(orgLista.chave, orgLista.ocultos, orgLista.pagina - 1)">‹</Button>
                <span>página {{ orgLista.pagina }} de {{ orgPaginas(orgLista.dados) }}</span>
                <Button size="sm" variant="outline" :disabled="orgLista.pagina >= orgPaginas(orgLista.dados) || orgLista.carregando" @click="verListaOrganizar(orgLista.chave, orgLista.ocultos, orgLista.pagina + 1)">›</Button>
              </template>
              <button class="text-muted-foreground hover:text-foreground" title="fechar a lista" @click="orgLista = null">
                <X class="size-4" />
              </button>
            </div>
            <div class="max-h-72 overflow-auto">
              <table class="w-full text-xs">
                <thead>
                  <tr>
                    <th>SKU</th>
                    <th>Nome</th>
                    <th>Conta</th>
                    <th>Anúncio</th>
                    <th>Variação</th>
                    <th>SKU do anúncio</th>
                    <th>Motivo</th>
                    <th>Data</th>
                    <th v-if="orgLista.ocultos"></th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="it in orgLista.dados?.itens || []" :key="it.marcador_id || it.registro_id">
                    <td class="font-mono">{{ it.sku || '—' }}</td>
                    <td class="max-w-[16rem] truncate" :title="it.nome || ''">{{ it.nome || '—' }}</td>
                    <td>{{ it.conta || '—' }}</td>
                    <td class="font-mono">{{ it.anuncio || '—' }}</td>
                    <td class="font-mono">{{ it.variacao || '—' }}</td>
                    <td class="font-mono">{{ it.sku_anuncio || '—' }}</td>
                    <td>{{ it.motivo || '—' }}</td>
                    <td class="whitespace-nowrap">{{ fmtQuando(it.data) }}</td>
                    <td v-if="orgLista.ocultos" class="text-right">
                      <button
                        v-if="it.marcador_id"
                        type="button"
                        class="underline text-violet-700 whitespace-nowrap"
                        :disabled="orgReexibindo"
                        @click="reexibirOrganizar({ marcador_ids: [it.marcador_id] }, it.sku || it.anuncio || 'item')"
                      >
                        Mostrar de novo
                      </button>
                    </td>
                  </tr>
                  <tr v-if="orgLista.dados && !orgLista.dados.itens.length">
                    <td :colspan="orgLista.ocultos ? 9 : 8" class="py-3 text-center text-muted-foreground">nada nesta lista</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <div class="border-t p-3 flex flex-wrap gap-2 justify-end">
          <Button variant="outline" @click="showOrganizar = false">Fechar</Button>
          <Button
            v-if="organizarAba === 'organizar'"
            class="bg-violet-600 hover:bg-violet-700 text-white"
            :disabled="!orgPrevia || orgTotalMarcado === 0 || orgAplicando || orgCarregando"
            @click="aplicarOrganizar"
          >
            <Loader2 v-if="orgAplicando" class="size-4 mr-1.5 animate-spin" />
            <EyeOff v-else class="size-4 mr-1.5" />
            Ocultar {{ fmtN(orgTotalMarcado) }} itens
          </Button>
        </div>
      </div>
    </div>

    <!-- Refresh Bling stock modal -->
    <div v-if="showRefreshStock" class="fixed inset-0 z-50 grid place-items-center bg-black/40" @click.self="showRefreshStock = false">
      <div class="product-dialog bg-background rounded-lg shadow-lg w-[min(720px,95vw)]">
        <div class="flex items-center justify-between border-b p-3">
          <h3 class="font-semibold">Atualizar estoque do Bling</h3>
          <button @click="showRefreshStock = false"><X class="size-4" /></button>
        </div>
        <div class="p-3 space-y-3">
          <div v-if="!activeJob" class="text-sm text-muted-foreground">iniciando… (paginação 100 produtos por página)</div>
          <div v-else class="space-y-2">
            <div class="flex justify-between text-sm">
              <span>Status: <strong>{{ activeJob.status }}</strong></span>
              <span class="tabular-nums">{{ activeJob.processed }} produtos</span>
            </div>
            <div v-if="activeJob.result && Object.keys(activeJob.result).length" class="text-xs text-muted-foreground">
              integrações: {{ (activeJob.result as any).integrations ?? 0 }} ·
              páginas: {{ (activeJob.result as any).pages ?? 0 }} ·
              atualizados: {{ (activeJob.result as any).updated ?? 0 }} ·
              sem-link local: {{ (activeJob.result as any).missing_local ?? 0 }}
            </div>
            <div v-if="activeJob.error" class="rounded-md border border-red-300 bg-red-50 p-2 text-sm text-red-700">
              {{ activeJob.error }}
            </div>
            <ul class="max-h-72 overflow-auto text-xs space-y-0.5 font-mono bg-muted/30 p-2 rounded">
              <li v-for="(d, idx) in jobDetails" :key="idx" class="text-muted-foreground whitespace-pre-wrap">
                {{ formatDetail(d) }}
              </li>
              <li v-if="!jobDetails.length" class="text-muted-foreground italic">
                buscando primeira página…
              </li>
            </ul>
            <Button v-if="activeJob.status === 'succeeded' || activeJob.status === 'failed'" class="w-full" variant="outline" @click="showRefreshStock = false">
              fechar
            </Button>
          </div>
        </div>
      </div>
    </div>

    <!-- CSV Import modal -->
    <div v-if="showImportCsv" class="fixed inset-0 z-50 grid place-items-center bg-black/40" @click.self="showImportCsv = false">
      <div class="product-dialog bg-background rounded-lg shadow-lg w-[min(600px,95vw)] flex flex-col">
        <div class="flex items-center justify-between border-b p-3">
          <h3 class="font-semibold">Importar Produtos via CSV</h3>
          <button @click="showImportCsv = false"><X class="size-4" /></button>
        </div>
        <div class="p-4 space-y-3">
          <p class="text-sm text-muted-foreground">
            Formato esperado: SKU, Nome, Custo, Estoque, Estoque Mínimo
          </p>
          <div v-if="csvImportError" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">
            {{ csvImportError }}
          </div>
          <input
            type="file"
            accept=".csv"
            class="block w-full text-sm border rounded px-3 py-2"
            @change="handleCsvUpload"
          />
          <div class="flex gap-2 justify-end">
            <Button variant="outline" @click="showImportCsv = false">Cancelar</Button>
            <Button :disabled="!csvFile || csvImporting" @click="submitCsvImport">
              <Loader2 v-if="csvImporting" class="size-4 mr-1 animate-spin" />
              Importar
            </Button>
          </div>
        </div>
      </div>
    </div>

    <!-- Bulk segment assign modal -->
    <div v-if="showBulkSegment" class="fixed inset-0 z-50 grid place-items-center bg-black/40" @click.self="showBulkSegment = false">
      <div class="product-dialog bg-background rounded-lg shadow-lg w-[min(520px,95vw)]">
        <div class="flex items-center justify-between border-b p-3">
          <h3 class="font-semibold flex items-center gap-2">
            <Tags class="size-5 text-emerald-600" />
            Atribuir segmento ({{ selected.size }} produto(s))
          </h3>
          <button @click="showBulkSegment = false"><X class="size-4" /></button>
        </div>
        <div class="p-4 space-y-3">
          <label class="text-sm font-medium">Segmento</label>
          <select v-model="bulkSegmentId" class="w-full h-9 rounded-md border bg-background px-2 text-sm">
            <option value="">— remover segmento —</option>
            <option v-for="s in segmentOptions" :key="s.id" :value="s.id">{{ s.label }}</option>
          </select>
          <p v-if="segmentOptions.length === 0" class="text-xs text-muted-foreground italic">
            Nenhum segmento cadastrado. Vá em Admin → Segmentos.
          </p>
          <div class="flex gap-2 justify-end pt-2">
            <Button variant="outline" @click="showBulkSegment = false">Cancelar</Button>
            <Button @click="runBulkSegment">
              Aplicar
            </Button>
          </div>
        </div>
      </div>
    </div>

    <!-- New product modal (Feature 2) -->
    <div v-if="showNewProduct" class="fixed inset-0 z-50 grid place-items-center bg-black/40" @click.self="showNewProduct = false">
      <div class="product-dialog bg-background rounded-lg shadow-lg w-[min(600px,95vw)] flex flex-col">
        <div class="flex items-center justify-between border-b p-3">
          <h3 class="font-semibold">Novo Produto</h3>
          <button @click="showNewProduct = false"><X class="size-4" /></button>
        </div>
        <div class="p-4 space-y-3">
          <div>
            <label class="text-sm font-medium">SKU *</label>
            <input
              v-model="newProduct.sku"
              type="text"
              placeholder="Ex: SKU001"
              class="w-full border rounded px-2 py-1 text-sm bg-background"
            />
          </div>
          <div>
            <label class="text-sm font-medium">Nome *</label>
            <input
              v-model="newProduct.name"
              type="text"
              placeholder="Ex: iPhone 13 Pro"
              class="w-full border rounded px-2 py-1 text-sm bg-background"
            />
          </div>
          <div>
            <label class="text-sm font-medium">Custo</label>
            <input
              v-model="newProduct.cost_price"
              type="number"
              step="0.01"
              placeholder="0.00"
              class="w-full border rounded px-2 py-1 text-sm bg-background"
            />
          </div>
          <div class="grid grid-cols-2 gap-2">
            <div>
              <label class="text-sm font-medium">Estoque</label>
              <input
                v-model.number="newProduct.stock"
                type="number"
                min="0"
                class="w-full border rounded px-2 py-1 text-sm bg-background"
              />
            </div>
            <div>
              <label class="text-sm font-medium">Estoque mínimo</label>
              <input
                v-model.number="newProduct.min_stock"
                type="number"
                min="0"
                class="w-full border rounded px-2 py-1 text-sm bg-background"
              />
            </div>
          </div>
          <div class="flex gap-2 justify-end">
            <Button variant="outline" @click="showNewProduct = false">Cancelar</Button>
            <Button :disabled="creatingProduct" @click="submitNewProduct">
              <Loader2 v-if="creatingProduct" class="size-4 mr-1 animate-spin" />
              Criar
            </Button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.product-mobile-card { overflow-wrap: anywhere; }
.product-mobile-card summary::-webkit-details-marker { display: none; }
.product-mobile-card details[open] > summary :deep(svg) { transform: rotate(180deg); }
@media (max-width: 1023px) {
  .product-filter-extra { display: none; }
  .product-filters-open .product-filter-extra {
    display: block;
    flex: 1 1 calc(50% - 0.75rem);
    width: 100%;
    min-width: 0;
  }
  #product-tools > * { flex: 1 1 auto; }
  #product-tools > [aria-label="Botões de Produtos e Anúncios"] > * { flex: 1 1 auto; }
}
@media (max-width: 639px) {
  .product-selection-actions {
    width: calc(100vw - 1.5rem);
    max-height: calc(100dvh - 2rem);
    overflow-y: auto;
    flex-wrap: wrap;
    justify-content: center;
    gap: .5rem;
    padding: .75rem;
    bottom: max(.75rem, env(safe-area-inset-bottom));
  }
  .product-selection-actions > span { flex-basis: 100%; text-align: center; }
  .product-selection-actions > .w-px { display: none; }
  .product-selection-actions :deep(button) { white-space: normal; }
  .product-dialog { max-height: calc(100dvh - 2rem); overflow-y: auto; }
  .product-dialog > .border-t { flex-wrap: wrap; }
}
</style>
