<script setup lang="ts">
// Controle de Estoque — operator-facing planilha.
//
// Filter model: SINGLE-DAY everywhere. The backend still accepts a
// date range (data_inicio / data_fim) so admin tooling can probe wider
// windows; the UI sends `data` for both to keep the contract one knob.
// Default = today.
//
// Tabs are isolated GETs:
//   * Estoque  → entradas + saídas + saldos for the chosen day.
//   * Pedidos  → "enviado etiqueta" orders shipped on the chosen day.
//   * Envios   → per-day shipment counts (the only tab that benefits
//                from a wider window, so it auto-widens to last 7 days
//                on first activation if the user hasn't picked a date).
import { computed, onMounted, ref, watch } from 'vue'
import {
  Boxes, Truck, ClipboardList, Loader2, RefreshCw,
  AlertTriangle, FileUp, Upload, Download, Trash2,
} from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'controle_estoque', action: 'view' },
})

const { api } = useApi()
const auth = useAuthStore()

// ── Types ─────────────────────────────────────────────────────────────
type EntradaMov = { movement_id: string; qty: number; obs: string }
type SaidaMov = { movement_id: string; qty: number; origem: string }
type ProdutoRow = {
  sku: string
  nome: string
  entradas: EntradaMov[]
  saidas: SaidaMov[]
  saida_qty_total: number
  saida_origens: string
  saldo_fisico: number
  saldo_virtual: number
  reserva: number
  conferido: boolean
  // Client-side state for the "no-entrada" obs row — survives until the
  // user types something, at which point saveSkuObs() upserts a real
  // placeholder movement and we cache the resulting id so blur/Enter
  // edits route back through patchMovementObs.
  _skuObsValue?: string
  _skuObsMovementId?: string
}
type PedidoRow = {
  id: string
  data: string | null         // ship date (em_andamento_data) — shown in column
  data_pedido: string | null
  data_envio: string | null
  pedido_bling: string | null
  pedido_marketplace: string | null
  loja: string | null         // already pretty-formatted by backend
  sku: string | null
  produto: string | null
  quantidade: number
  status: 'enviado' | 'nao_enviado'
  conferido: boolean
  observacao: string | null
  bling_id: number | null
}
type EnvioRow = {
  data: string
  envios: number
  conferido: boolean
  // Status da conferência da aba Estoque para aquele dia. Vem do
  // backend — comparação count(StockCheck conferido) vs count(produtos).
  conferencia_estoque: 'total' | 'parcial' | 'nenhuma'
}

// ── State ─────────────────────────────────────────────────────────────
type Tab = 'estoque' | 'pedidos' | 'envios' | 'estoque-negativo' | 'upload-nf'
const tab = ref<Tab>('estoque')

function isoToday(): string {
  return new Date().toISOString().slice(0, 10)
}
function isoDaysAgo(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() - days)
  return d.toISOString().slice(0, 10)
}

// Single-day filter for Estoque + Pedidos. Envios uses a 7-day window
// that auto-resets on first activation (see watch below) — operators
// still want per-day counts but with enough rows on screen to compare.
const dia = ref(isoToday())
const enviosInicio = ref(isoDaysAgo(6))
const enviosFim = ref(isoToday())

// Admin-only tag override.
const isAdmin = computed(() => auth.user?.role === 'admin')
const tagOverride = ref<string>('')

// Filter products by presence of stock. 'all' (default) = no filter,
// 'com' = Product.stock > 0, 'sem' = stock == 0 OR NULL. Applied to
// the /produtos list AND the conferência counter so denominators
// match what the operator sees on screen.
const estoqueFilter = ref<'all' | 'com' | 'sem'>('all')

// Single source of truth for tag labels — keep in sync with backend
// STOCK_TAGS list. The admin dropdown uses these; operadores never
// see this UI (they have a fixed set from user.stock_tags).
const TAG_OPTIONS: { slug: string; label: string }[] = [
  { slug: 'ci', label: 'CI' },
  { slug: 'pi', label: 'PI' },
  { slug: 'ra', label: 'RA' },
  { slug: 'sa', label: 'SA' },
  { slug: 'sp', label: 'SP' },
  { slug: 'us', label: 'Usados' },
  { slug: 'cd', label: 'Centro de Distribuição' },
  { slug: 'fake', label: 'Fake' },
  { slug: 'mala', label: 'Mala' },
  { slug: 'eletro', label: 'Eletro' },
  { slug: 'insumos', label: 'Insumos' },
]

// Manual reload — calls POST /api/estoque/sync-stocks which fans out
// GET /estoques/saldos on Bling for the visible product set. Used when
// the webhook missed a virtual-balance update (rare but happens for
// reservation-driven changes).
const syncing = ref(false)
const syncToast = ref<string | null>(null)
async function syncFromBling() {
  if (syncing.value) return
  syncing.value = true
  syncToast.value = null
  try {
    const params = new URLSearchParams()
    if (isAdmin.value && tagOverride.value) params.set('tag', tagOverride.value)
    const r = await api<{ updated: number; total_products: number; missing_bling_data: number }>(
      `/api/estoque/sync-stocks${params.toString() ? `?${params.toString()}` : ''}`,
      { method: 'POST' },
    )
    syncToast.value = `Sincronizado: ${r.updated}/${r.total_products} produtos`
    void loadCurrentTab()
  } catch (e: any) {
    syncToast.value = `Falha: ${e?.data?.detail?.code || e?.message || 'erro'}`
  } finally {
    syncing.value = false
    setTimeout(() => { syncToast.value = null }, 4000)
  }
}

const statusFilter = ref<'all' | 'enviado' | 'nao_enviado'>('all')
const conferidoFilter = ref<'all' | 'conferidos' | 'nao_conferidos'>('all')
const search = ref('')

// Data
const produtos = ref<ProdutoRow[]>([])
const pedidos = ref<PedidoRow[]>([])
const envios = ref<{
  items: EnvioRow[]
  total: number          // sum of conferido envios (footer "Total")
  total_envios: number   // sum across the window (footer "Total geral")
}>({ items: [], total: 0, total_envios: 0 })

// Foto da conferência do estoque HOJE — independente do filtro de dia.
// Alimenta o bloqueio da aba Envios pro operador (admin nunca bloqueia).
// Recarregado em onMounted, ao trocar a tab e após conferirTodos/toggleProduto
// quando o operador está vendo o dia de hoje.
const conferenciaHoje = ref<{ total: number; conferido: number; percent: number }>({
  total: 0, conferido: 0, percent: 0,
})
async function refreshConferenciaHoje() {
  try {
    const params = new URLSearchParams()
    if (isAdmin.value && tagOverride.value) params.set('tag', tagOverride.value)
    if (estoqueFilter.value !== 'all') params.set('estoque_filter', estoqueFilter.value)
    const r = await api<{ total: number; conferido: number; percent: number }>(
      `/api/estoque/conferencia-hoje${params.toString() ? `?${params.toString()}` : ''}`,
    )
    conferenciaHoje.value = { total: r.total, conferido: r.conferido, percent: r.percent }
  } catch {
    // não-fatal: mantém o valor anterior; o bloqueio cai pro lado seguro
    // (operador não acessa Envios sem conferência confirmada).
  }
}

const loading = ref(false)
const errorText = ref<string | null>(null)

// ── Fetchers ──────────────────────────────────────────────────────────
function singleDayDates(): string {
  // Estoque + Pedidos send the same value for both endpoints — backend
  // tolerates either treating the window as a single point or a range.
  const parts = [`data_inicio=${dia.value}`, `data_fim=${dia.value}`]
  if (isAdmin.value && tagOverride.value) parts.push(`tag=${tagOverride.value}`)
  // estoque_filter applies only to the Estoque tab (the /produtos call
  // below). Pedidos and Envios ignore the param.
  if (estoqueFilter.value !== 'all') parts.push(`estoque_filter=${estoqueFilter.value}`)
  return parts.join('&')
}
function rangeDates(): string {
  const parts = [
    `data_inicio=${enviosInicio.value}`,
    `data_fim=${enviosFim.value}`,
  ]
  if (isAdmin.value && tagOverride.value) parts.push(`tag=${tagOverride.value}`)
  return parts.join('&')
}

async function loadEstoque() {
  loading.value = true
  errorText.value = null
  try {
    const r = await api<{ data: ProdutoRow[] }>(`/api/estoque/produtos?${singleDayDates()}`)
    produtos.value = r.data || []
  } catch (e: any) {
    errorText.value = e?.data?.detail?.code || e?.message || 'load_failed'
    produtos.value = []
  } finally {
    loading.value = false
  }
}

async function loadPedidos() {
  loading.value = true
  errorText.value = null
  try {
    const qs = [singleDayDates()]
    if (statusFilter.value !== 'all') qs.push(`status=${statusFilter.value}`)
    const r = await api<{ data: PedidoRow[] }>(`/api/estoque/pedidos?${qs.join('&')}`)
    pedidos.value = r.data || []
  } catch (e: any) {
    errorText.value = e?.data?.detail?.code || e?.message || 'load_failed'
    pedidos.value = []
  } finally {
    loading.value = false
  }
}

async function loadEnvios() {
  loading.value = true
  errorText.value = null
  try {
    const qs = [rangeDates()]
    if (conferidoFilter.value !== 'all') qs.push(`conferido=${conferidoFilter.value}`)
    const r = await api<{
      data: EnvioRow[]
      total: number
      total_envios: number
      total_conferido: number
    }>(`/api/estoque/envios?${qs.join('&')}`)
    envios.value = {
      items: r.data || [],
      total: r.total ?? 0,
      total_envios: r.total_envios ?? 0,
    }
  } catch (e: any) {
    errorText.value = e?.data?.detail?.code || e?.message || 'load_failed'
    envios.value = { items: [], total: 0, total_envios: 0 }
  } finally {
    loading.value = false
  }
}

function loadCurrentTab() {
  if (tab.value === 'estoque') return loadEstoque()
  if (tab.value === 'pedidos') return loadPedidos()
  return loadEnvios()
}

watch(tab, () => {
  void loadCurrentTab()
  // O bloqueio da aba Envios depende da conferência de hoje — refetch
  // sempre que a tab muda pra refletir alterações feitas em outra aba.
  void refreshConferenciaHoje()
})
watch([dia, tagOverride, statusFilter], () => {
  if (tab.value !== 'envios') void loadCurrentTab()
})
watch(estoqueFilter, () => {
  // Refetch Estoque list (the only tab that uses the filter) AND the
  // conferência counter so the percentage matches the visible set.
  if (tab.value === 'estoque') void loadCurrentTab()
  void refreshConferenciaHoje()
})
watch([enviosInicio, enviosFim, conferidoFilter], () => {
  if (tab.value === 'envios') void loadCurrentTab()
})
watch(tagOverride, () => {
  if (tab.value === 'envios') void loadCurrentTab()
})

// ── Aba: Estoque Negativo (refresh-from-Bling + sufixos) ──────────────
type SaldoRow = { codigo: string; saldo_fisico: number; saldo_virtual_total: number }
const negativos = ref<SaldoRow[]>([])
const sufixos = ref<SaldoRow[]>([])
const negativosLoading = ref(false)
const refreshing = ref(false)
const refreshMsg = ref<string | null>(null)
const suffixChoice = ref<string>('.us,.sa')
const customSuffix = ref<string>('')
const SUFFIX_PRESETS: { value: string; label: string }[] = [
  { value: '.us,.sa', label: '.us + .sa (default)' },
  { value: '.ci', label: '.ci' },
  { value: '.ra', label: '.ra' },
  { value: '.sp', label: '.sp' },
  { value: '.cd', label: '.cd' },
  { value: '.pi', label: '.pi' },
  { value: '__custom__', label: 'personalizado…' },
]
const effectiveSuffixes = computed(() =>
  suffixChoice.value === '__custom__' ? customSuffix.value.trim() : suffixChoice.value,
)

async function loadNegativos() {
  negativosLoading.value = true
  try {
    const q = search.value.trim()
    const r = await api<{ items: SaldoRow[] }>(
      `/api/estoque/negativos${q ? `?search=${encodeURIComponent(q)}` : ''}`,
    )
    negativos.value = r.items || []
  } catch (e: any) {
    refreshMsg.value = `Falha negativos: ${e?.data?.detail?.code || e?.message || 'erro'}`
  } finally {
    negativosLoading.value = false
  }
}
async function loadSufixos() {
  const sufs = effectiveSuffixes.value
  if (!sufs) { sufixos.value = []; return }
  try {
    const r = await api<{ items: SaldoRow[] }>(
      `/api/estoque/sufixos?suffixes=${encodeURIComponent(sufs)}`,
    )
    sufixos.value = r.items || []
  } catch (e: any) {
    refreshMsg.value = `Falha sufixos: ${e?.data?.detail?.code || e?.message || 'erro'}`
  }
}
async function refreshFromBling() {
  refreshing.value = true
  refreshMsg.value = null
  try {
    const r = await api<{
      updated: number; total_products: number; missing_bling_data?: number
    }>('/api/estoque/atualizar-bling', { method: 'POST' })
    refreshMsg.value = `Atualizou ${r.updated}/${r.total_products} produtos` +
      (r.missing_bling_data ? ` (${r.missing_bling_data} sem dado Bling)` : '')
    await Promise.all([loadNegativos(), loadSufixos()])
  } catch (e: any) {
    const code = e?.data?.detail?.code
    if (code === 'refresh_already_running') {
      refreshMsg.value = `Refresh já em andamento (${e?.data?.detail?.started_at})`
    } else if (code === 'bling_not_connected') {
      refreshMsg.value = 'Bling não conectado'
    } else {
      refreshMsg.value = e?.message || 'erro'
    }
  } finally {
    refreshing.value = false
  }
}
function downloadCsv() {
  const sufs = effectiveSuffixes.value
  if (!sufs) return
  window.open(`/api/estoque/sufixos.csv?suffixes=${encodeURIComponent(sufs)}`, '_blank')
}

// ── Aba: Upload NF (XML → ML) ─────────────────────────────────────────
type NfAttempt = { store: string; success: boolean; error: string | null; shipping_id: string | null }
type NfResult = {
  filename: string
  success: boolean
  order_id?: string | null
  store_name?: string | null
  shipping_id?: string | null
  error?: string | null
  attempts_details?: NfAttempt[]
}
const nfFiles = ref<File[]>([])
const nfStores = ref<string[]>([])
const nfSelectedStores = ref<Set<string>>(new Set())
const nfProcessing = ref(false)
const nfCurrentFile = ref<string | null>(null)
const nfResults = ref<NfResult[]>([])
const nfFileInputRef = ref<HTMLInputElement | null>(null)

const nfSuccessCount = computed(() => nfResults.value.filter((r) => r.success).length)
const nfFailCount = computed(() => nfResults.value.filter((r) => !r.success).length)

async function loadNfStores() {
  try {
    const r = await api<{ stores: string[] }>('/api/nf/stores')
    nfStores.value = r.stores || []
    nfSelectedStores.value = new Set(nfStores.value)
  } catch (e: any) {
    console.error('Falha lojas NF:', e)
  }
}
function onNfFileChange(ev: Event) {
  const inp = ev.target as HTMLInputElement
  if (!inp.files) return
  const next = Array.from(inp.files)
  nfFiles.value = [
    ...nfFiles.value,
    ...next.filter((nf) =>
      !nfFiles.value.some((f) => f.name === nf.name && f.size === nf.size),
    ),
  ]
  inp.value = ''
}
function removeNfFile(idx: number) {
  nfFiles.value.splice(idx, 1)
}
function clearNfAll() {
  nfFiles.value = []
  nfResults.value = []
  nfCurrentFile.value = null
}
function toggleNfStore(name: string) {
  const s = new Set(nfSelectedStores.value)
  if (s.has(name)) s.delete(name); else s.add(name)
  nfSelectedStores.value = s
}
async function processNfFiles() {
  if (!nfFiles.value.length || !nfSelectedStores.value.size) return
  nfProcessing.value = true
  nfResults.value = []
  for (const file of nfFiles.value) {
    nfCurrentFile.value = file.name
    const fd = new FormData()
    fd.append('file', file)
    for (const s of nfSelectedStores.value) fd.append('selected_stores', s)
    try {
      const r = await api<NfResult>('/api/nf/upload', { method: 'POST', body: fd })
      nfResults.value.push({ filename: file.name, ...r })
    } catch (e: any) {
      nfResults.value.push({
        filename: file.name,
        success: false,
        error: e?.data?.detail?.code || e?.message || 'erro',
        attempts_details: [],
      })
    }
  }
  nfCurrentFile.value = null
  nfProcessing.value = false
}

// Lazy-load dos dados das novas abas + re-load no toggle de aba.
watch(tab, async (newTab) => {
  if (newTab === 'estoque-negativo') {
    await Promise.all([loadNegativos(), loadSufixos()])
  } else if (newTab === 'upload-nf' && !nfStores.value.length) {
    await loadNfStores()
  }
})
watch(effectiveSuffixes, () => {
  if (tab.value === 'estoque-negativo') void loadSufixos()
})
watch(search, () => {
  if (tab.value === 'estoque-negativo') void loadNegativos()
})

onMounted(() => {
  void loadCurrentTab()
  void refreshConferenciaHoje()
})

// ── Conferido toggle (per section) ────────────────────────────────────
async function toggleCheck(
  section: 'estoque' | 'pedido' | 'envio',
  referenceId: string,
  referenceDate: string,
  next: boolean,
  observacao?: string | null,
) {
  const params = new URLSearchParams({
    section,
    reference_id: referenceId,
    reference_date: referenceDate,
    conferido: String(next),
  })
  if (observacao != null) params.set('observacao', observacao)
  await api(`/api/estoque/check?${params.toString()}`, { method: 'POST' })
}

async function toggleProduto(row: ProdutoRow) {
  const next = !row.conferido
  row.conferido = next
  try {
    await toggleCheck('estoque', row.sku, dia.value, next)
    // Só conta pra liberar a aba Envios se o operador está marcando
    // o dia de hoje. Marcar dias passados não destrava nada.
    if (dia.value === isoToday()) void refreshConferenciaHoje()
  } catch {
    row.conferido = !next
  }
}
async function togglePedido(row: PedidoRow) {
  const next = !row.conferido
  row.conferido = next
  const refDate = (row.data || dia.value).slice(0, 10)
  try {
    await toggleCheck('pedido', row.id, refDate, next, row.observacao)
  } catch {
    row.conferido = !next
  }
}
async function patchPedidoObs(row: PedidoRow, newObs: string) {
  row.observacao = newObs
  const refDate = (row.data || dia.value).slice(0, 10)
  try {
    await toggleCheck('pedido', row.id, refDate, row.conferido, newObs)
  } catch { /* next reload reverts */ }
}
async function toggleEnvio(row: EnvioRow) {
  if (!isAdmin.value) return
  const next = !row.conferido
  row.conferido = next
  try {
    await toggleCheck('envio', row.data, row.data, next)
    if (next) envios.value.total += row.envios
    else envios.value.total = Math.max(0, envios.value.total - row.envios)
  } catch {
    row.conferido = !next
  }
}

async function patchMovementObs(movementId: string, newObs: string, row: ProdutoRow, idx: number) {
  row.entradas[idx].obs = newObs
  try {
    const params = new URLSearchParams()
    if (newObs) params.set('observacao', newObs)
    await api(`/api/estoque/movement/${movementId}/obs?${params.toString()}`, { method: 'PATCH' })
  } catch {
    void loadEstoque()
  }
}

// Obs input on a SKU with NO entrada today — upserts a placeholder
// `manual-note` movement via the dedicated endpoint. Once created, the
// next loadEstoque() surfaces it as a regular entrada (with qty=0) and
// subsequent edits go through patchMovementObs.
async function saveSkuObs(row: ProdutoRow, newObs: string) {
  // No-op on empty → don't create an empty placeholder movement.
  if (!newObs.trim() && (!row._skuObsMovementId)) return
  try {
    const params = new URLSearchParams({
      sku: row.sku,
      reference_date: dia.value,
    })
    if (newObs) params.set('observacao', newObs)
    const r = await api<{ movement_id: string }>(
      `/api/estoque/sku-obs?${params.toString()}`,
      { method: 'POST' },
    )
    row._skuObsMovementId = r.movement_id
    row._skuObsValue = newObs
  } catch {
    void loadEstoque()
  }
}

// ── Search filter (client-side) ───────────────────────────────────────
const produtosFiltered = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return produtos.value
  return produtos.value.filter(
    (p) =>
      (p.sku || '').toLowerCase().includes(q)
      || (p.nome || '').toLowerCase().includes(q),
  )
})
const pedidosFiltered = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return pedidos.value
  return pedidos.value.filter(
    (p) =>
      (p.sku || '').toLowerCase().includes(q)
      || (p.produto || '').toLowerCase().includes(q)
      || (p.pedido_bling || '').toLowerCase().includes(q)
      || (p.pedido_marketplace || '').toLowerCase().includes(q),
  )
})

// Pedidos distintos (não linhas) — `bling_orders` é multi-row por pedido
// (uma linha por item), então `pedidosFiltered.length` conta itens.
// O operador quer ver número de pedidos.
const totalPedidos = computed(() =>
  new Set(
    pedidosFiltered.value.map((p) => p.pedido_bling).filter(Boolean),
  ).size,
)

// Ordena por data envio (desc, primário) + pedido_bling (secundário) pra
// que itens do mesmo pedido fiquem consecutivos. Adiciona meta-flags
// usadas no render (`_isFirstOfGroup` controla render do número e
// separador visual entre grupos).
type PedidoRowWithGroup = PedidoRow & { _isFirstOfGroup: boolean; _groupSize: number }
const pedidosFilteredGrouped = computed<PedidoRowWithGroup[]>(() => {
  const sorted = [...pedidosFiltered.value].sort((a, b) => {
    const da = a.data || ''
    const db = b.data || ''
    if (da !== db) return db.localeCompare(da)
    return (a.pedido_bling || '').localeCompare(b.pedido_bling || '')
  })
  const groupSizes = new Map<string, number>()
  for (const r of sorted) {
    const key = r.pedido_bling || ''
    groupSizes.set(key, (groupSizes.get(key) || 0) + 1)
  }
  const out: PedidoRowWithGroup[] = []
  let lastPedido: string | null = null
  for (const r of sorted) {
    const isFirst = (r.pedido_bling || '') !== lastPedido
    out.push({
      ...r,
      _isFirstOfGroup: isFirst,
      _groupSize: groupSizes.get(r.pedido_bling || '') || 1,
    })
    lastPedido = r.pedido_bling || null
  }
  return out
})

// Tag extraction from SKU — mirrors the backend rule subset that's
// derivable from the SKU string alone:
//   * fake.* prefix      → 'fake'
//   * .ci/.pi/.ra/.sa/.sp/.us/.cd suffix → that tag
//   * .<numero> suffix   → 'mala' (number = tamanho, padrão das malas)
//   * outros             → 'outros' (kits sem suffix, insumos, eletro, etc)
// Composite SKUs (a+b+c) usam o primeiro pedaço — em produção quase
// sempre todos os pedaços têm a mesma tag. NÃO replica a lógica
// completa do backend (que considera SKUs sem dot etc); é uma
// aproximação suficiente pro contador visual.
const _TAG_SUFFIXES = new Set(['ci', 'pi', 'ra', 'sa', 'sp', 'us', 'cd'])
function extractPedidoTag(sku: string | null): string {
  if (!sku) return 'outros'
  const firstPiece = sku.split('+')[0].toLowerCase().trim()
  if (firstPiece.startsWith('fake.')) return 'fake'
  const parts = firstPiece.split('.')
  if (parts.length < 2) return 'outros'
  const suffix = parts[parts.length - 1]
  if (_TAG_SUFFIXES.has(suffix)) return suffix
  // Suffix numérico (12, 24, 18, etc) = tamanho de mala
  if (/^\d+$/.test(suffix)) return 'mala'
  return 'outros'
}

// Breakdown por tag (com base em pedidosFiltered, então respeita
// busca + filtros). Ordem fixa pra o display ficar estável.
const _TAG_DISPLAY_ORDER = ['sa', 'ci', 'pi', 'ra', 'sp', 'us', 'cd', 'fake', 'mala', 'outros']
const pedidosCountByTag = computed(() => {
  const counts: Record<string, number> = {}
  for (const p of pedidosFiltered.value) {
    const t = extractPedidoTag(p.sku)
    counts[t] = (counts[t] || 0) + 1
  }
  // Retorna só tags com contagem > 0, na ordem de display.
  return _TAG_DISPLAY_ORDER
    .map((t) => ({ tag: t, count: counts[t] || 0 }))
    .filter((x) => x.count > 0)
})

// Percentual conferido na aba Estoque DO DIA VISÍVEL — usado pelo
// header de progresso e pelo botão "Conferir todos". Difere de
// `conferenciaHoje` que é sempre HOJE (fonte da verdade pro bloqueio).
const conferidoPercent = computed(() => {
  const total = produtos.value.length
  if (total === 0) return 0
  const checked = produtos.value.filter((p) => p.conferido).length
  return Math.round((checked / total) * 100)
})

// Bloqueio da aba Envios: admin entra sempre; operador só com 100%
// da aba Estoque conferido HOJE (não do dia que ele estiver visualizando).
const canAccessEnvios = computed(() => {
  if (isAdmin.value) return true
  return conferenciaHoje.value.percent >= 100
})

async function conferirTodos() {
  const unchecked = produtos.value.filter((p) => !p.conferido)
  if (unchecked.length === 0) return
  if (!window.confirm(`Confirmar que você conferiu os ${unchecked.length} itens restantes?`)) return
  // Optimistic UI: marca local imediatamente; em caso de falha
  // individual reverte só aquele item (loadEstoque pegaria o resto).
  await Promise.all(
    unchecked.map(async (row) => {
      row.conferido = true
      try {
        await toggleCheck('estoque', row.sku, dia.value, true)
      } catch {
        row.conferido = false
      }
    }),
  )
  if (dia.value === isoToday()) void refreshConferenciaHoje()
}
</script>

<template>
  <div class="controle-estoque space-y-3 p-4">
    <!-- Header + tabs -->
    <div class="flex flex-wrap items-center gap-3">
      <div class="flex items-center gap-2">
        <Boxes class="h-5 w-5 text-primary" />
        <h1 class="text-xl font-semibold">Controle de Estoque</h1>
      </div>
      <button
        class="ml-auto inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
        :disabled="syncing"
        :title="'Busca saldos atualizados direto do Bling para os produtos visíveis'"
        @click="syncFromBling"
      >
        <RefreshCw class="size-3.5" :class="{ 'animate-spin': syncing }" />
        {{ syncing ? 'Sincronizando…' : 'Recarregar' }}
      </button>
      <span
        v-if="syncToast"
        class="text-xs text-muted-foreground bg-muted/40 border rounded px-2 py-1"
      >{{ syncToast }}</span>
      <div class="flex gap-1 rounded-md bg-muted/40 p-1 w-fit flex-wrap">
        <button
          v-for="t in (['estoque', 'pedidos', 'envios', 'estoque-negativo', 'upload-nf'] as const)"
          :key="t"
          class="px-3 py-1.5 rounded text-sm transition-colors inline-flex items-center gap-1.5"
          :class="tab === t ? 'bg-background shadow-sm font-medium' : 'hover:bg-background/60 text-muted-foreground'"
          @click="tab = t"
        >
          <Boxes v-if="t === 'estoque'" class="size-4" />
          <ClipboardList v-else-if="t === 'pedidos'" class="size-4" />
          <Truck v-else-if="t === 'envios'" class="size-4" />
          <AlertTriangle v-else-if="t === 'estoque-negativo'" class="size-4" />
          <FileUp v-else class="size-4" />
          {{
            t === 'estoque' ? 'Estoque' :
            t === 'pedidos' ? 'Pedidos' :
            t === 'envios' ? 'Envios' :
            t === 'estoque-negativo' ? 'Estoque Negativo' :
            'Upload NF'
          }}
        </button>
      </div>
    </div>

    <!-- Filters bar -->
    <div class="flex flex-wrap items-center gap-2 bg-muted/30 border rounded-md px-3 py-2 text-xs">
      <input
        v-model="search"
        type="search"
        placeholder="Buscar SKU, nome, pedido…"
        class="h-7 border rounded px-2 bg-background min-w-[200px]"
      />
      <template v-if="tab !== 'envios'">
        <label class="inline-flex items-center gap-1">
          Dia:
          <input v-model="dia" type="date" class="h-7 border rounded px-2 bg-background" />
        </label>
      </template>
      <template v-else>
        <label class="inline-flex items-center gap-1">
          De:
          <input v-model="enviosInicio" type="date" class="h-7 border rounded px-2 bg-background" />
        </label>
        <label class="inline-flex items-center gap-1">
          Até:
          <input v-model="enviosFim" type="date" class="h-7 border rounded px-2 bg-background" />
        </label>
        <label class="inline-flex items-center gap-1">
          Conferência:
          <select v-model="conferidoFilter" class="h-7 border rounded px-2 bg-background">
            <option value="all">todos</option>
            <option value="conferidos">conferidos</option>
            <option value="nao_conferidos">não conferidos</option>
          </select>
        </label>
      </template>
      <label v-if="isAdmin" class="inline-flex items-center gap-1">
        Tag:
        <select v-model="tagOverride" class="h-7 border rounded px-2 bg-background">
          <option value="">todas</option>
          <option v-for="opt in TAG_OPTIONS" :key="opt.slug" :value="opt.slug">
            {{ opt.label }}
          </option>
        </select>
      </label>
      <!-- Stock-presence filter — only meaningful on the Estoque tab.
           Backend ignores the param for Pedidos / Envios, so it's safe
           to leave the dropdown visible everywhere, but we keep it
           Estoque-only to avoid implying it affects the other tabs. -->
      <label v-if="tab === 'estoque'" class="inline-flex items-center gap-1">
        Estoque:
        <select v-model="estoqueFilter" class="h-7 border rounded px-2 bg-background">
          <option value="all">todos</option>
          <option value="com">com estoque</option>
          <option value="sem">sem estoque</option>
        </select>
      </label>
      <label v-if="tab === 'pedidos'" class="inline-flex items-center gap-1">
        Status:
        <select v-model="statusFilter" class="h-7 border rounded px-2 bg-background">
          <option value="all">todos</option>
          <option value="enviado">enviado</option>
          <option value="nao_enviado">não enviado</option>
        </select>
      </label>
      <div class="ml-auto inline-flex items-center gap-2 text-muted-foreground">
        <Loader2 v-if="loading" class="size-3 animate-spin" />
        <span v-if="errorText" class="text-destructive">{{ errorText }}</span>
      </div>
    </div>

    <!-- TAB: ESTOQUE ────────────────────────────────────────────────── -->
    <!-- Barra de progresso + "Conferir todos". Botão sempre visível
         (sem mínimo de %); ao 100% troca pro badge verde. -->
    <div v-if="tab === 'estoque' && produtos.length > 0" class="flex items-center gap-3 text-xs">
      <template v-if="conferidoPercent < 100">
        <button
          class="px-3 py-1.5 bg-emerald-600 text-white text-[11px] font-medium rounded hover:bg-emerald-700"
          @click="conferirTodos"
        >
          ✓ Conferir todos
        </button>
        <span class="text-muted-foreground">
          {{ conferidoPercent }}% conferido ({{ produtos.filter((p) => p.conferido).length }}/{{ produtos.length }})
        </span>
      </template>
      <span v-else class="text-emerald-600 font-medium">
        ✓ Estoque 100% conferido
      </span>
    </div>
    <div v-if="tab === 'estoque'" class="border rounded-md overflow-x-auto">
      <table class="grid-table w-full text-xs border-collapse">
        <colgroup>
          <col style="width: 80px" />   <!-- SKU -->
          <col style="width: 160px" />  <!-- Produto -->
          <col style="width: 50px" />   <!-- Entrada Qtd -->
          <col style="width: 110px" />  <!-- Entrada Obs -->
          <col style="width: 50px" />   <!-- Saída Qtd -->
          <col style="width: 100px" />  <!-- Saída Nº Pedidos -->
          <col style="width: 60px" />   <!-- Saldo Atual -->
          <col style="width: 55px" />   <!-- Saldo Reserva -->
          <col style="width: 40px" />   <!-- Conf -->
        </colgroup>
        <thead>
          <tr class="bg-muted/50">
            <th class="text-left text-[11px] font-semibold" colspan="2">Identificação</th>
            <th class="text-center text-[11px] font-semibold bg-amber-50 dark:bg-amber-900/20" colspan="2">Entrada</th>
            <th class="text-center text-[11px] font-semibold bg-amber-50 dark:bg-amber-900/20" colspan="2">Saída</th>
            <th class="text-center text-[11px] font-semibold bg-emerald-50 dark:bg-emerald-900/20" colspan="2">Saldo</th>
            <th class="text-center text-[11px] font-semibold bg-gray-100 dark:bg-gray-800/40">Conf.</th>
          </tr>
          <tr class="bg-muted/30 text-[10px] uppercase tracking-wide">
            <th class="text-left">SKU</th>
            <th class="text-left">Produto</th>
            <th class="text-right bg-amber-50/60 dark:bg-amber-900/10">Qtd</th>
            <th class="text-left bg-amber-50/60 dark:bg-amber-900/10">Obs</th>
            <th class="text-right bg-amber-50/60 dark:bg-amber-900/10">Qtd</th>
            <th class="text-left bg-amber-50/60 dark:bg-amber-900/10">Nº Pedidos</th>
            <th class="text-right bg-emerald-50/60 dark:bg-emerald-900/10">Atual</th>
            <th class="text-right bg-emerald-50/60 dark:bg-emerald-900/10">Reserva</th>
            <th class="text-center bg-gray-100/60 dark:bg-gray-800/30">✓</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="produtosFiltered.length === 0">
            <td colspan="9" class="py-6 text-center text-muted-foreground">
              Nenhum produto para esse filtro.
            </td>
          </tr>
          <tr
            v-for="row in produtosFiltered" :key="row.sku"
            class="hover:bg-muted/20"
            :class="row.conferido ? 'bg-emerald-50/40 dark:bg-emerald-900/10' : ''"
          >
            <td class="font-mono text-[11px] truncate" :title="row.sku">{{ row.sku }}</td>
            <td class="truncate" :title="row.nome">{{ row.nome }}</td>
            <!-- Entrada Qtd + Obs are split into 2 cells but visually
                 aligned: same vertical stack of N entradas in each. -->
            <td class="bg-amber-50/40 dark:bg-amber-900/5 align-top text-right">
              <div v-if="row.entradas.length === 0" class="text-muted-foreground/60">—</div>
              <div v-else class="space-y-0.5">
                <div
                  v-for="e in row.entradas" :key="e.movement_id"
                  class="font-semibold text-amber-700 dark:text-amber-300 leading-5 h-5"
                >
                  {{ e.qty }}
                </div>
              </div>
            </td>
            <td class="bg-amber-50/40 dark:bg-amber-900/5 align-top p-0">
              <div v-if="row.entradas.length === 0" class="p-0.5">
                <input
                  :value="row._skuObsValue ?? ''"
                  placeholder="—"
                  class="obs-input"
                  @blur="(ev) => saveSkuObs(row, (ev.target as HTMLInputElement).value)"
                  @keyup.enter="($event.target as HTMLInputElement).blur()"
                />
              </div>
              <div v-else class="space-y-0.5 p-0.5">
                <input
                  v-for="(e, idx) in row.entradas" :key="e.movement_id"
                  :value="e.obs"
                  placeholder="—"
                  class="obs-input"
                  @blur="(ev) => patchMovementObs(e.movement_id, (ev.target as HTMLInputElement).value, row, idx)"
                  @keyup.enter="($event.target as HTMLInputElement).blur()"
                />
              </div>
            </td>
            <td
              class="text-right bg-amber-50/40 dark:bg-amber-900/5"
              :class="row.saida_qty_total > 0 ? 'font-semibold text-amber-700 dark:text-amber-300' : 'text-muted-foreground/60'"
            >
              {{ row.saida_qty_total || '—' }}
            </td>
            <td class="truncate bg-amber-50/40 dark:bg-amber-900/5" :title="row.saida_origens">
              {{ row.saida_origens || '—' }}
            </td>
            <td
              class="text-right bg-emerald-50/40 dark:bg-emerald-900/5 font-semibold"
              :class="row.saldo_fisico === 0 ? 'text-red-600' : 'text-emerald-700'"
            >
              {{ row.saldo_fisico }}
            </td>
            <td class="text-right bg-emerald-50/40 dark:bg-emerald-900/5 text-muted-foreground">
              {{ row.reserva || '—' }}
            </td>
            <td class="text-center bg-gray-100/40 dark:bg-gray-800/20">
              <input
                type="checkbox"
                :checked="row.conferido"
                class="cursor-pointer"
                @change="toggleProduto(row)"
              />
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- TAB: PEDIDOS ────────────────────────────────────────────────── -->
    <!-- Stats bar: total + breakdown por tag. Reflete pedidosFiltered
         (search + filtros). Tag extraída do SKU no frontend — ver
         extractPedidoTag(). -->
    <div v-if="tab === 'pedidos'" class="flex flex-wrap items-center gap-2 text-xs">
      <span class="inline-flex items-center gap-1.5 rounded-md bg-primary text-primary-foreground px-2.5 py-1 font-semibold">
        Total: {{ totalPedidos }} pedidos
      </span>
      <span
        v-for="bucket in pedidosCountByTag" :key="bucket.tag"
        class="inline-flex items-center gap-1 rounded-md border bg-background px-2 py-0.5"
        :title="`Pedidos com tag ${bucket.tag.toUpperCase()}`"
      >
        <span class="uppercase font-semibold tracking-wide text-[10px]">{{ bucket.tag }}</span>
        <span class="text-foreground font-mono">{{ bucket.count }}</span>
      </span>
    </div>
    <div v-if="tab === 'pedidos'" class="border rounded-md overflow-x-auto">
      <table class="grid-table w-full text-xs border-collapse">
        <thead>
          <tr class="bg-muted/30 text-[10px] uppercase tracking-wide">
            <th class="text-left">Data Envio</th>
            <th class="text-left">Pedido Bling</th>
            <th class="text-left">Marketplace</th>
            <th class="text-left">Loja</th>
            <th class="text-left">SKU</th>
            <th class="text-left">Produto</th>
            <th class="text-right">Qtd</th>
            <th class="text-center">Status</th>
            <th class="text-left bg-emerald-50/40">Obs</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="pedidosFiltered.length === 0">
            <td colspan="9" class="py-6 text-center text-muted-foreground">
              Nenhum pedido para esse dia.
            </td>
          </tr>
          <tr
            v-for="(row, idx) in pedidosFilteredGrouped" :key="row.id"
            class="hover:bg-muted/20"
            :class="{ 'border-t-2 border-t-muted-foreground/30': row._isFirstOfGroup && idx > 0 }"
          >
            <td class="whitespace-nowrap">
              {{ row._isFirstOfGroup ? (row.data ? row.data.slice(0, 10) : '—') : '' }}
            </td>
            <td class="font-mono text-[11px]" :class="{ 'text-muted-foreground/40': !row._isFirstOfGroup }">
              {{ row._isFirstOfGroup ? (row.pedido_bling || '—') : '' }}
            </td>
            <td class="font-mono text-[11px]">{{ row.pedido_marketplace || '—' }}</td>
            <td>{{ row.loja || '—' }}</td>
            <td class="font-mono text-[11px]">{{ row.sku || '—' }}</td>
            <td class="truncate max-w-[280px]" :title="row.produto || ''">{{ row.produto || '—' }}</td>
            <td class="text-right">{{ row.quantidade }}</td>
            <td class="text-center">
              <span
                class="inline-block px-1.5 py-0.5 rounded text-[10px] font-medium"
                :class="row.status === 'enviado'
                  ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300'
                  : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300'"
              >
                {{ row.status === 'enviado' ? 'Enviado' : 'Não enviado' }}
              </span>
            </td>
            <td class="bg-emerald-50/30">
              <input
                :value="row.observacao || ''"
                placeholder="observação"
                class="w-full h-6 border rounded px-1 bg-background text-[11px]"
                @blur="(e) => patchPedidoObs(row, (e.target as HTMLInputElement).value)"
              />
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- TAB: ENVIOS ─────────────────────────────────────────────────── -->
    <!-- Bloqueio pro operador: precisa estar 100% conferido HOJE.
         Admin nunca cai aqui (canAccessEnvios returns true). -->
    <div v-if="tab === 'envios' && !canAccessEnvios" class="border rounded-md py-10 px-4 text-center space-y-3">
      <p class="text-sm text-muted-foreground">
        ⚠️ Confira o estoque do dia antes de acessar os envios.
      </p>
      <p class="text-[11px] text-muted-foreground">
        Hoje: {{ conferenciaHoje.conferido }}/{{ conferenciaHoje.total }} ({{ conferenciaHoje.percent }}%)
      </p>
      <button
        class="px-3 py-1.5 bg-primary text-primary-foreground text-xs rounded"
        @click="tab = 'estoque'"
      >
        Ir para Estoque
      </button>
    </div>
    <div v-else-if="tab === 'envios'" class="border rounded-md overflow-x-auto">
      <table class="grid-table w-full text-xs border-collapse">
        <thead>
          <tr class="bg-muted/30 text-[10px] uppercase tracking-wide">
            <th class="text-left">Data</th>
            <th class="text-right">Envios</th>
            <th class="text-center">Conf. Estoque</th>
            <th class="text-center bg-gray-100/40">Conferido</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="envios.items.length === 0">
            <td colspan="4" class="py-6 text-center text-muted-foreground">
              Nenhum envio no período.
            </td>
          </tr>
          <tr
            v-for="row in envios.items" :key="row.data"
            class="hover:bg-muted/20"
            :class="row.conferido ? 'bg-emerald-50/40 dark:bg-emerald-900/10' : ''"
          >
            <td>{{ row.data }}</td>
            <td class="text-right font-semibold">{{ row.envios }}</td>
            <td class="text-center">
              <span
                class="inline-block px-1.5 py-0.5 rounded text-[10px] font-medium"
                :class="{
                  'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300': row.conferencia_estoque === 'total',
                  'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300': row.conferencia_estoque === 'parcial',
                  'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300': row.conferencia_estoque === 'nenhuma',
                }"
              >
                {{
                  row.conferencia_estoque === 'total'
                    ? 'Total'
                    : row.conferencia_estoque === 'parcial'
                      ? 'Parcial'
                      : 'Não conferido'
                }}
              </span>
            </td>
            <td class="text-center bg-gray-100/30">
              <input
                v-if="isAdmin"
                type="checkbox"
                :checked="row.conferido"
                class="cursor-pointer"
                @change="toggleEnvio(row)"
              />
              <span
                v-else
                class="inline-block text-base"
                :class="row.conferido ? 'text-emerald-600' : 'text-muted-foreground/40'"
                :title="row.conferido ? 'Conferido' : 'Não conferido'"
              >{{ row.conferido ? '✓' : '✗' }}</span>
            </td>
          </tr>
        </tbody>
        <tfoot v-if="envios.items.length > 0" class="bg-muted/30 font-semibold">
          <tr>
            <td class="text-right">Total (conferidos)</td>
            <td class="text-right">{{ envios.total }}</td>
            <td></td>
            <td class="text-center text-muted-foreground text-[10px]">
              geral: {{ envios.total_envios }}
            </td>
          </tr>
        </tfoot>
      </table>
    </div>

    <!-- TAB: ESTOQUE NEGATIVO ───────────────────────────────────────── -->
    <div v-if="tab === 'estoque-negativo'" class="space-y-4">
      <div class="flex flex-wrap items-center gap-2 bg-muted/30 border rounded-md px-3 py-2 text-xs">
        <span class="text-muted-foreground">
          Lê de <code class="bg-background px-1 rounded">products.saldo_virtual_total</code> /
          <code class="bg-background px-1 rounded">saldo_fisico</code> — atualize pelo Bling antes de gerar etiquetas.
        </span>
        <button
          class="ml-auto inline-flex items-center gap-1 rounded-md bg-primary text-primary-foreground px-2.5 py-1 hover:opacity-90 disabled:opacity-50"
          :disabled="refreshing"
          @click="refreshFromBling"
        >
          <Loader2 v-if="refreshing" class="size-3 animate-spin" />
          <RefreshCw v-else class="size-3" />
          {{ refreshing ? 'Atualizando…' : 'Atualizar via Bling' }}
        </button>
        <span v-if="refreshMsg" class="text-xs text-muted-foreground bg-background border rounded px-2 py-1">
          {{ refreshMsg }}
        </span>
      </div>

      <!-- Saldo virtual negativo -->
      <div class="space-y-2">
        <div class="flex items-center gap-2">
          <h3 class="font-semibold text-sm flex items-center gap-2">
            <AlertTriangle class="size-4 text-amber-600" /> Saldo virtual &lt; 0
          </h3>
          <span class="text-xs text-muted-foreground">{{ negativos.length }} SKU(s)</span>
        </div>
        <div v-if="negativosLoading" class="text-center py-6 text-muted-foreground text-xs">
          <Loader2 class="size-4 animate-spin mx-auto mb-1" /> Carregando…
        </div>
        <div v-else-if="negativos.length === 0" class="text-center py-6 text-muted-foreground text-xs">
          Nenhum SKU com saldo virtual negativo.
        </div>
        <div v-else class="border rounded-md overflow-x-auto">
          <table class="grid-table w-full text-xs border-collapse">
            <thead>
              <tr class="bg-emerald-800 text-white text-[10px] uppercase tracking-wide">
                <th class="text-left">SKU</th>
                <th class="text-right">Saldo Físico</th>
                <th class="text-right">Saldo Virtual</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in negativos" :key="row.codigo" class="even:bg-muted/10 hover:bg-amber-50/40">
                <td class="font-mono">{{ row.codigo }}</td>
                <td class="text-right">{{ row.saldo_fisico }}</td>
                <td class="text-right font-semibold text-red-700">{{ row.saldo_virtual_total }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Por sufixo -->
      <div class="space-y-2">
        <div class="flex flex-wrap items-center gap-2">
          <h3 class="font-semibold text-sm">Por sufixo (saldo físico &gt; 0)</h3>
          <span class="text-xs text-muted-foreground">{{ sufixos.length }} SKU(s)</span>
          <label class="inline-flex items-center gap-1 text-xs">
            Sufixo:
            <select v-model="suffixChoice" class="h-7 border rounded px-2 bg-background">
              <option v-for="opt in SUFFIX_PRESETS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
            </select>
          </label>
          <input
            v-if="suffixChoice === '__custom__'"
            v-model="customSuffix"
            placeholder=".us,.sa,.ra"
            class="h-7 border rounded px-2 text-xs bg-background"
            @blur="loadSufixos"
          />
          <button
            class="ml-auto inline-flex items-center gap-1 rounded-md border px-2.5 py-1 hover:bg-muted disabled:opacity-50"
            :disabled="sufixos.length === 0"
            @click="downloadCsv"
          >
            <Download class="size-3" /> CSV
          </button>
        </div>
        <div v-if="sufixos.length === 0" class="text-center py-6 text-muted-foreground text-xs">
          Nenhum SKU encontrado para os sufixos selecionados.
        </div>
        <div v-else class="border rounded-md overflow-x-auto">
          <table class="grid-table w-full text-xs border-collapse">
            <thead>
              <tr class="bg-emerald-800 text-white text-[10px] uppercase tracking-wide">
                <th class="text-left">SKU</th>
                <th class="text-right">Saldo Físico</th>
                <th class="text-right">Saldo Virtual</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in sufixos" :key="row.codigo" class="even:bg-muted/10 hover:bg-amber-50/40">
                <td class="font-mono">{{ row.codigo }}</td>
                <td class="text-right font-semibold text-emerald-700">{{ row.saldo_fisico }}</td>
                <td class="text-right">{{ row.saldo_virtual_total }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- TAB: UPLOAD NF ─────────────────────────────────────────────── -->
    <div v-if="tab === 'upload-nf'" class="space-y-4">
      <!-- Stores picker -->
      <section class="border rounded-md p-3 space-y-2">
        <div class="flex items-center justify-between">
          <h3 class="font-semibold text-sm">Lojas ML alvo</h3>
          <span class="text-xs text-muted-foreground">
            {{ nfSelectedStores.size }} de {{ nfStores.length }} selecionada(s)
          </span>
        </div>
        <div v-if="!nfStores.length" class="text-xs text-muted-foreground">
          Nenhuma integração ML ativa encontrada.
        </div>
        <div v-else class="flex flex-wrap gap-1.5">
          <button
            v-for="s in nfStores"
            :key="s"
            type="button"
            class="rounded-full border px-2.5 py-1 text-xs transition-colors"
            :class="nfSelectedStores.has(s) ? 'bg-primary text-primary-foreground border-primary' : 'border-muted-foreground/40 hover:bg-muted'"
            @click="toggleNfStore(s)"
          >
            {{ s }}
          </button>
        </div>
      </section>

      <!-- File picker -->
      <section class="border rounded-md p-3 space-y-2">
        <div class="flex flex-wrap items-center gap-2">
          <label class="inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm cursor-pointer hover:bg-muted">
            <FileUp class="size-3.5" /> Selecionar XMLs
            <input ref="nfFileInputRef" type="file" accept=".xml" multiple class="hidden" @change="onNfFileChange" />
          </label>
          <span class="text-xs text-muted-foreground">{{ nfFiles.length }} arquivo(s)</span>
          <button
            v-if="nfFiles.length"
            class="ml-auto inline-flex items-center gap-1 rounded-md border border-destructive text-destructive px-2.5 py-1 text-xs hover:bg-destructive/10"
            @click="clearNfAll"
          >
            <Trash2 class="size-3" /> Limpar
          </button>
          <button
            class="inline-flex items-center gap-1.5 rounded-md bg-primary text-primary-foreground px-3 py-1.5 text-sm hover:opacity-90 disabled:opacity-50"
            :disabled="nfProcessing || !nfFiles.length || !nfSelectedStores.size"
            @click="processNfFiles"
          >
            <Loader2 v-if="nfProcessing" class="size-3.5 animate-spin" />
            <Upload v-else class="size-3.5" />
            {{ nfProcessing ? 'Enviando…' : `Enviar (${nfFiles.length})` }}
          </button>
        </div>
        <ul v-if="nfFiles.length" class="text-xs space-y-1 border-t pt-2">
          <li v-for="(f, idx) in nfFiles" :key="idx" class="flex items-center gap-2">
            <FileUp class="size-3 text-muted-foreground shrink-0" />
            <span class="truncate flex-1">{{ f.name }}</span>
            <span class="text-muted-foreground">{{ (f.size / 1024).toFixed(0) }} KiB</span>
            <button v-if="!nfProcessing" class="text-muted-foreground hover:text-destructive" @click="removeNfFile(idx)">
              <Trash2 class="size-3" />
            </button>
          </li>
        </ul>
      </section>

      <div
        v-if="nfProcessing && nfCurrentFile"
        class="rounded border bg-muted/20 px-3 py-2 text-sm flex items-center gap-2"
      >
        <Loader2 class="size-3 animate-spin" />
        Processando: <strong>{{ nfCurrentFile }}</strong>
        ({{ nfResults.length + 1 }} de {{ nfFiles.length }})
      </div>

      <!-- Results -->
      <section v-if="nfResults.length" class="space-y-2">
        <div class="flex items-center gap-3 text-sm">
          <h3 class="font-semibold">Resultados</h3>
          <span class="text-emerald-700">✓ {{ nfSuccessCount }}</span>
          <span class="text-red-700">✕ {{ nfFailCount }}</span>
        </div>
        <div class="border rounded-md divide-y">
          <div
            v-for="(r, idx) in nfResults"
            :key="idx"
            class="p-3 text-xs"
            :class="r.success ? 'bg-emerald-50/40' : 'bg-red-50/40'"
          >
            <div class="flex items-center gap-2 flex-wrap">
              <span v-if="r.success" class="text-emerald-700 font-bold">✓</span>
              <span v-else class="text-red-700 font-bold">✕</span>
              <strong class="truncate">{{ r.filename }}</strong>
              <span v-if="r.order_id" class="text-muted-foreground font-mono">pedido {{ r.order_id }}</span>
              <span v-if="r.success" class="ml-auto text-emerald-800">→ {{ r.store_name }}</span>
              <span v-else class="ml-auto text-red-700">{{ r.error || 'falha' }}</span>
            </div>
            <details v-if="r.attempts_details && r.attempts_details.length" class="mt-1.5">
              <summary class="cursor-pointer text-[10px] text-muted-foreground hover:text-foreground">
                {{ r.attempts_details.length }} tentativa(s)
              </summary>
              <ul class="mt-1 pl-4 space-y-0.5 text-[11px]">
                <li v-for="(a, i) in r.attempts_details" :key="i" class="flex items-center gap-2">
                  <span v-if="a.success" class="text-emerald-700">✓</span>
                  <span v-else class="text-red-700">✕</span>
                  <span class="font-medium">{{ a.store }}</span>
                  <span v-if="a.shipping_id" class="text-muted-foreground">ship={{ a.shipping_id }}</span>
                  <span v-if="!a.success" class="text-muted-foreground italic">{{ a.error }}</span>
                </li>
              </ul>
            </details>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
/* Full-grid borders on every cell — spreadsheet look. Padding kept tight
   so the row count visible on screen stays high. */
.grid-table th,
.grid-table td {
  border: 1px solid hsl(var(--border));
  padding: 4px 6px;
}
.grid-table thead th {
  background-clip: padding-box;
}

/* Inline-editable obs field on Entrada column. Sits flush in the cell
   with a dashed underline so operators can see it's editable without
   a heavy border that competes with the spreadsheet grid. Focused
   state turns into a clear bordered input on the muted background. */
.obs-input {
  display: block;
  width: 100%;
  height: 20px;
  padding: 0 4px;
  font-size: 11px;
  line-height: 20px;
  background: transparent;
  border: 0;
  border-bottom: 1px dashed hsl(var(--border));
  border-radius: 2px;
  color: inherit;
  cursor: text;
}
.obs-input::placeholder {
  color: hsl(var(--muted-foreground) / 0.55);
}
.obs-input:hover {
  background: hsl(var(--background) / 0.6);
  border-bottom-style: solid;
}
.obs-input:focus {
  background: hsl(var(--background));
  border: 1px solid hsl(var(--primary));
  outline: none;
}
</style>
