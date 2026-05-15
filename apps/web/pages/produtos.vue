<script setup lang="ts">
import { ref, reactive, computed, onUnmounted } from 'vue'
import { Plus, Trash2, Download, RefreshCw, ChevronDown, ChevronRight, X, Loader2, Zap, Upload, Search, Tags, Link2 } from 'lucide-vue-next'

definePageMeta({ middleware: ['permission'], permission: { resource: 'produtos', action: 'view' } })

type Integration = {
  id: string
  platform: 'bling' | 'ml' | 'shopee' | 'amazon'
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

type ProductPage = { items: Product[]; total: number; page: number; page_size: number }

type BlingPreviewItem = {
  bling_product_id: number
  sku: string | null
  name: string
  cost_price: string | null
  price: string | null
  stock: number | null
  image_url: string | null
}

type Job = {
  id: string
  type: string
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled'
  total: number
  processed: number
  payload: Record<string, unknown>
  result: Record<string, unknown>
  details: Array<Record<string, unknown>>
  error: string | null
  started_at: string | null
  finished_at: string | null
}

const { api } = useApi()
const canEdit = useCan('produtos', 'edit')
const canDelete = useCan('produtos', 'delete')

const integrations = ref<Integration[]>([])
const items = ref<Product[]>([])
const segments = ref<Segment[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 50
const search = ref('')
const filtroIntegration = ref<string>('')
const filtroSegment = ref<string>('')
const stockFilter = ref<'' | 'low' | 'ok' | 'zero'>('')
const expanded = ref<Set<string>>(new Set())
const selected = ref<Set<string>>(new Set())
const loading = ref(false)
const error = ref<string | null>(null)

const showImport = ref(false)
const showAutoLink = ref(false)
const showSyncAll = ref(false)
const showRefreshStock = ref(false)

// SSH-style selection dialogs
const selectedAutoLinkIds = ref<Set<string>>(new Set())
const selectedSyncAllIds = ref<Set<string>>(new Set())

// Per-product sync popover
const syncPopoverProductId = ref<string | null>(null)
const syncPopoverSelectedIds = ref<Set<string>>(new Set())

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
    case 'temu': return `${baseClass} bg-purple-100 text-purple-700`
    case 'aliexpress': return `${baseClass} bg-red-100 text-red-700`
    default: return `${baseClass} bg-gray-100 text-gray-700`
  }
}

type UserSettings = { daily_sync_enabled: boolean }
const autoSyncEnabled = ref<boolean>(false)
const togglingAutoSync = ref(false)

async function refreshAll() {
  loading.value = true
  error.value = null
  try {
    const [pg, integ, settings, segs] = await Promise.all([
      api<ProductPage>(`/api/products?page=${page.value}&page_size=${pageSize}` +
        (search.value ? `&search=${encodeURIComponent(search.value)}` : '') +
        (filtroIntegration.value ? `&integration_id=${filtroIntegration.value}` : '') +
        (stockFilter.value === 'low' ? `&low_stock=true` : '') +
        (stockFilter.value === 'zero' ? `&zero_stock=true` : '')),
        // 'ok' is handled client-side (filteredItems) to avoid backend changes
      api<Integration[]>('/api/integrations'),
      api<UserSettings>('/api/settings'),
      api<Segment[]>('/api/segments').catch(() => [] as Segment[]),
    ])
    items.value = pg.items
    total.value = pg.total
    integrations.value = integ
    autoSyncEnabled.value = settings.daily_sync_enabled
    segments.value = segs
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    loading.value = false
  }
}
await refreshAll()

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

type MarketCol = 'shopee' | 'amazon' | 'ml_classico' | 'ml_premium' | 'tiktok'

function linkCol(l: ProductLink): MarketCol | null {
  if (l.platform === 'shopee') return 'shopee'
  if (l.platform === 'amazon') return 'amazon'
  if (l.platform === 'tiktok') return 'tiktok'
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
  return p.links.filter(l => linkCol(l) === col)
}

function hasIntegrationsForCol(col: MarketCol): boolean {
  if (col === 'amazon') return integrations.value.some((i) => i.platform === 'amazon')
  if (col === 'tiktok') return integrations.value.some((i) => (i.platform as string) === 'tiktok')
  return false
}

function manualLink(_p: Product, col: MarketCol) {
  // Backend não expõe endpoint de criação manual de link.
  // Pré-selecionamos as integrações dessa plataforma no dialog de auto-link.
  const platform = col === 'amazon' ? 'amazon' : 'tiktok'
  selectedAutoLinkIds.value = new Set(
    marketplaceIntegrations.value.filter((i) => (i.platform as string) === platform).map((i) => i.id),
  )
  activeJob.value = null
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

// Filter dropdown includes both roots and leaves (so user can filter by
// "Celular" and see all subtypes, or by a specific leaf).
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
    .filter((s) => s.active)
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
  if (!confirm(`Excluir ${selected.value.size} produto(s)?`)) return
  await api('/api/products/bulk-delete', {
    method: 'POST',
    body: { ids: [...selected.value] },
  })
  selected.value = new Set()
  await refreshAll()
}

async function deleteOne(id: string) {
  if (!confirm('Excluir produto?')) return
  await api(`/api/products/${id}`, { method: 'DELETE' })
  await refreshAll()
}

async function deleteLink(id: string) {
  if (!confirm('Remover link?')) return
  await api(`/api/product-links/${id}`, { method: 'DELETE' })
  await refreshAll()
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
    await api(`/api/sync/product/${id}`, {
      method: 'POST',
      body: integrationIds && integrationIds.length > 0
        ? { integration_ids: integrationIds }
        : {},
    })
    await refreshAll()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    syncingProduct.value.delete(id)
    syncingProduct.value = new Set(syncingProduct.value)
  }
}

function openSyncPopover(p: Product) {
  // Pre-selecionar todas as integrações que têm link com este produto.
  syncPopoverSelectedIds.value = new Set(
    p.links.map((l) => l.integration_id).filter(Boolean) as string[],
  )
  syncPopoverProductId.value = p.id
}

function closeSyncPopover() {
  syncPopoverProductId.value = null
  syncPopoverSelectedIds.value = new Set()
}

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

function openSyncAllDialog() {
  // Pre-select all marketplace integrations (matches SSH default behavior).
  selectedSyncAllIds.value = new Set(marketplaceIntegrations.value.map((i) => i.id))
  activeJob.value = null
  showSyncAll.value = true
}

function toggleSelectedSyncAll(id: string) {
  if (selectedSyncAllIds.value.has(id)) selectedSyncAllIds.value.delete(id)
  else selectedSyncAllIds.value.add(id)
  selectedSyncAllIds.value = new Set(selectedSyncAllIds.value)
}

async function runSyncAll() {
  if (selectedSyncAllIds.value.size === 0) return
  activeJob.value = null
  try {
    const r = await api<{ job_id: string }>('/api/jobs/sync-all', {
      method: 'POST',
      body: {
        integration_ids: Array.from(selectedSyncAllIds.value),
        product_ids: null,
        include_all_stock: true,
      },
    })
    startPolling(r.job_id)
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  }
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

// ---------------------------- Bling import ----------------------------------

const importIntegration = ref<string>('')
const importPage = ref(1)
const importPreview = ref<BlingPreviewItem[]>([])
const importSelected = ref<Set<number>>(new Set())
const importLoading = ref(false)
const importResult = ref<{ imported: number; updated: number; skipped_no_sku: number[] } | null>(null)

async function openImport() {
  importIntegration.value = blingIntegrations.value[0]?.id || ''
  importPage.value = 1
  importPreview.value = []
  importSelected.value = new Set()
  importResult.value = null
  showImport.value = true
  if (importIntegration.value) await loadPreview()
}

async function loadPreview() {
  if (!importIntegration.value) return
  importLoading.value = true
  try {
    const r = await api<{ items: BlingPreviewItem[] }>(
      `/api/products/preview/bling?integration_id=${importIntegration.value}&page=${importPage.value}`,
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

// ---------------------------- Auto-link ------------------------------------

const activeJob = ref<Job | null>(null)
let pollHandle: number | null = null

function openAutoLinkDialog() {
  selectedAutoLinkIds.value = new Set(marketplaceIntegrations.value.map((i) => i.id))
  activeJob.value = null
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
  try {
    const r = await api<{ job_id: string }>('/api/jobs/auto-link', {
      method: 'POST',
      body: {
        integration_ids: Array.from(selectedAutoLinkIds.value),
      },
    })
    startPolling(r.job_id)
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  }
}

async function runAutoImportLink() {
  activeJob.value = null
  try {
    const r = await api<{ job_id: string }>('/api/jobs/auto-import-link', {
      method: 'POST',
    })
    startPolling(r.job_id)
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  }
}

function startPolling(jobId: string) {
  if (pollHandle) clearInterval(pollHandle)
  const tick = async () => {
    try {
      const j = await api<Job>(`/api/jobs/${jobId}`)
      activeJob.value = j
      if (j.status === 'succeeded' || j.status === 'failed' || j.status === 'cancelled') {
        if (pollHandle) { clearInterval(pollHandle); pollHandle = null }
        await refreshAll()
      }
    } catch {
      /* swallow */
    }
  }
  void tick()
  pollHandle = window.setInterval(tick, 1500)
}

onUnmounted(() => {
  if (pollHandle) clearInterval(pollHandle)
})

</script>

<template>
  <div class="space-y-5">
    <PageHeader title="Produtos" description="SKUs do Bling, custos e links por canal.">
      <template #actions>
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
          class="border-emerald-300 text-emerald-700 hover:bg-emerald-50"
          @click="openAutoLinkDialog"
        >
          <Zap class="size-4 mr-1.5" /> Vincular Automático
        </Button>
        <Button
          v-if="canEdit"
          size="sm"
          variant="outline"
          class="border-sky-300 text-sky-700 hover:bg-sky-50"
          title="Anexa anúncios já importados ao SKU do produto local (sem chamar marketplace)"
          @click="runAutoImportLink"
        >
          <Link2 class="size-4 mr-1.5" /> Vincular Anúncios
        </Button>
        <Button v-if="canEdit" size="sm" variant="outline" @click="openSyncAllDialog">
          <RefreshCw class="size-4 mr-1.5" /> Sincronizar Todos
        </Button>
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
    </PageHeader>

    <div v-if="error" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">
      {{ error }}
    </div>

    <div class="flex flex-wrap gap-3 items-center">
      <div class="relative flex-1 min-w-[260px]">
        <Search class="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
        <Input v-model="search" placeholder="Buscar por nome, SKU ou título do anúncio..." class="pl-9" @keyup.enter="refreshAll" />
      </div>
      <select v-model="stockFilter" class="h-9 w-[180px] rounded-md border bg-background px-2 text-sm" @change="refreshAll">
        <option value="">Todo estoque</option>
        <option value="low">Estoque baixo</option>
        <option value="ok">Estoque OK</option>
        <option value="zero">Sem estoque</option>
      </select>
      <select v-model="filtroIntegration" class="h-9 w-[220px] rounded-md border bg-background px-2 text-sm" @change="refreshAll">
        <option value="">Todas as contas</option>
        <option v-for="i in integrations" :key="i.id" :value="i.id">[{{ i.platform }}] {{ i.name }}</option>
      </select>
      <select v-model="filtroSegment" class="h-9 w-[200px] rounded-md border bg-background px-2 text-sm">
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

    <div class="table-card">
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
              <td v-for="col in (['shopee','amazon','ml_classico','ml_premium','tiktok'] as const)" :key="col" class="text-center">
                <template v-if="linksFor(p, col).length === 0">
                  <button
                    v-if="(col === 'amazon' || col === 'tiktok') && hasIntegrationsForCol(col)"
                    class="text-[10px] text-blue-500 hover:text-blue-700 hover:underline cursor-pointer"
                    :title="`Vincular manualmente à ${col === 'amazon' ? 'Amazon' : 'TikTok'}`"
                    @click="manualLink(p, col)"
                  >+ vincular</button>
                  <span v-else class="text-xs text-muted-foreground">—</span>
                </template>
                <div v-else class="flex flex-col gap-1 items-center">
                  <div v-for="l in linksFor(p, col)" :key="l.id" class="leading-tight">
                    <div
                      class="font-semibold tabular-nums"
                      :class="l.last_sync_status === 'fatal' ? 'text-red-600' : ''"
                    >{{ l.stock ?? 0 }}</div>
                    <div class="text-[10px] text-muted-foreground">
                      {{ integrationById[l.integration_id]?.name || l.platform }}
                    </div>
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
                    @click="syncPopoverProductId === p.id ? closeSyncPopover() : openSyncPopover(p)"
                  >
                    <RefreshCw class="size-4" :class="syncingProduct.has(p.id) ? 'animate-spin' : ''" />
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

                  <!-- Per-product sync popover -->
                  <div
                    v-if="syncPopoverProductId === p.id"
                    class="absolute right-0 top-full mt-1 z-30 w-72 rounded-md border bg-background shadow-lg p-3 text-left"
                    @click.stop
                  >
                    <div class="space-y-3">
                      <div>
                        <p class="text-sm font-semibold">Sincronizar: {{ p.sku }}</p>
                        <p class="text-xs text-muted-foreground">Selecione as contas para sincronizar</p>
                      </div>
                      <div class="space-y-1 max-h-48 overflow-y-auto">
                        <p v-if="linkedIntegrationsFor(p).length === 0" class="text-xs text-muted-foreground italic py-2">
                          Sem links neste produto. Use "Vincular Automático".
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
                </div>
              </td>
            </tr>
            <tr v-if="expanded.has(p.id)" class="bg-muted/30">
              <td colspan="13" class="p-3">
                <div v-if="p.links.length === 0" class="text-xs text-muted-foreground">
                  Sem links. Rode o auto-link para vincular este SKU aos canais.
                </div>
                <table v-else class="w-full text-xs">
                  <thead>
                    <tr class="text-left text-muted-foreground">
                      <th>Plataforma</th>
                      <th>Tipo</th>
                      <th>Integração</th>
                      <th>External ID</th>
                      <th>Variação</th>
                      <th>Título</th>
                      <th class="text-right">Estoque</th>
                      <th>Status</th>
                      <th class="w-8"></th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="l in p.links" :key="l.id">
                      <td>{{ l.platform }}</td>
                      <td>{{ l.listing_type || '—' }}</td>
                      <td>{{ integrationById[l.integration_id]?.name || l.integration_id }}</td>
                      <td class="font-mono">{{ l.external_id }}</td>
                      <td>{{ l.variation_id || '—' }}</td>
                      <td>{{ l.listing_title || '—' }}</td>
                      <td class="text-right tabular-nums">{{ l.stock ?? '—' }}</td>
                      <td>
                        <span class="pill" :class="l.last_sync_status === 'ok' ? 'pill-success' : l.last_sync_status === 'fatal' ? 'pill-danger' : 'pill-muted'">
                          {{ l.last_sync_status }}
                        </span>
                      </td>
                      <td>
                        <Button v-if="canDelete" size="icon" variant="ghost" @click="deleteLink(l.id)">
                          <X class="size-3" />
                        </Button>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </td>
            </tr>
          </template>
          <tr v-if="items.length === 0">
            <td colspan="13" class="py-8 text-center text-sm text-muted-foreground">
              Nenhum produto. Use "importar Bling" para começar.
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="flex justify-between items-center text-sm">
      <div class="flex gap-1 items-center">
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

    <!-- Import modal -->
    <div v-if="showImport" class="fixed inset-0 z-50 grid place-items-center bg-black/40" @click.self="showImport = false">
      <div class="bg-background rounded-lg shadow-lg w-[min(900px,95vw)] max-h-[90vh] flex flex-col">
        <div class="flex items-center justify-between border-b p-3">
          <h3 class="font-semibold">Importar produtos do Bling</h3>
          <button @click="showImport = false"><X class="size-4" /></button>
        </div>
        <div class="p-3 space-y-3 overflow-auto">
          <div class="flex gap-2 items-center">
            <select v-model="importIntegration" class="h-9 rounded-md border bg-background px-2 text-sm" @change="importPage = 1; loadPreview()">
              <option v-for="i in blingIntegrations" :key="i.id" :value="i.id">{{ i.name }}</option>
              <option v-if="blingIntegrations.length === 0" disabled value="">sem integração Bling</option>
            </select>
            <Button size="sm" :disabled="importPage <= 1 || importLoading" @click="importPage--; loadPreview()">←</Button>
            <span class="text-xs">página {{ importPage }}</span>
            <Button size="sm" :disabled="importLoading" @click="importPage++; loadPreview()">→</Button>
            <span class="ml-auto text-xs text-muted-foreground">{{ importSelected.size }} selecionados</span>
          </div>

          <div v-if="importResult" class="rounded-md border bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
            Importados: {{ importResult.imported }} · atualizados: {{ importResult.updated }} ·
            ignorados (sem SKU): {{ importResult.skipped_no_sku.length }}
          </div>

          <table class="w-full text-sm">
            <thead>
              <tr>
                <th class="w-8"></th>
                <th>Bling ID</th>
                <th>SKU</th>
                <th>Nome</th>
                <th class="text-right">Preço</th>
                <th class="text-right">Estoque</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in importPreview" :key="row.bling_product_id">
                <td>
                  <input
                    type="checkbox"
                    :disabled="!row.sku"
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
              </tr>
              <tr v-if="importPreview.length === 0">
                <td colspan="6" class="py-4 text-center text-muted-foreground">
                  {{ importLoading ? 'carregando…' : 'nenhum produto nesta página' }}
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

    <!-- Vincular Automático dialog (SSH-style) -->
    <div v-if="showAutoLink" class="fixed inset-0 z-50 grid place-items-center bg-black/40" @click.self="showAutoLink = false">
      <div class="bg-background rounded-lg shadow-lg w-[min(560px,95vw)] max-h-[85vh] flex flex-col">
        <div class="flex items-center justify-between border-b p-4">
          <div>
            <h3 class="font-semibold flex items-center gap-2">
              <Zap class="size-5 text-emerald-600" />
              Vincular Anúncios Automaticamente
            </h3>
            <p class="text-xs text-muted-foreground mt-1">
              Selecione as contas que deseja vincular e a plataforma vai buscar os anúncios e vincular automaticamente pelo SKU.
            </p>
          </div>
          <button @click="showAutoLink = false"><X class="size-4" /></button>
        </div>

        <!-- Selection phase -->
        <div v-if="!activeJob" class="p-4 space-y-4 flex-1 overflow-y-auto">
          <div class="space-y-2">
            <div class="flex items-center justify-between">
              <label class="text-sm font-medium">Selecione as contas para vincular:</label>
              <div class="flex gap-2">
                <Button variant="ghost" size="sm" class="text-xs h-7 px-2" @click="selectedAutoLinkIds = new Set(marketplaceIntegrations.map(i => i.id))">Todas</Button>
                <Button variant="ghost" size="sm" class="text-xs h-7 px-2" @click="selectedAutoLinkIds = new Set()">Nenhuma</Button>
              </div>
            </div>
            <div class="rounded-lg border max-h-[280px] overflow-y-auto">
              <p v-if="marketplaceIntegrations.length === 0" class="text-sm text-muted-foreground text-center py-4">
                Nenhuma integração de marketplace conectada.
              </p>
              <label
                v-for="(integ, idx) in marketplaceIntegrations"
                :key="integ.id"
                class="flex items-center gap-3 px-3 py-2.5 hover:bg-muted/40 cursor-pointer transition-colors"
                :class="idx !== marketplaceIntegrations.length - 1 ? 'border-b' : ''"
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
            <p v-if="marketplaceIntegrations.length > 0" class="text-xs text-muted-foreground">
              {{ selectedAutoLinkIds.size }} de {{ marketplaceIntegrations.length }} conta(s) selecionada(s)
            </p>
          </div>

          <div class="rounded-lg border border-amber-200 bg-amber-50 p-3">
            <p class="text-xs text-amber-700">
              <strong>Importante:</strong> Os SKUs precisam ser idênticos entre o Bling e os marketplaces para a vinculação funcionar.
            </p>
          </div>
        </div>

        <!-- Progress phase -->
        <div v-else class="p-4 space-y-3 flex-1 overflow-y-auto">
          <div class="flex justify-between text-sm">
            <span class="flex items-center gap-2">
              <Loader2 v-if="activeJob.status === 'running' || activeJob.status === 'pending'" class="size-4 animate-spin text-emerald-600" />
              <span>Status: <strong>{{ activeJob.status }}</strong></span>
            </span>
            <span class="tabular-nums">{{ activeJob.processed }} / {{ activeJob.total }}</span>
          </div>
          <div class="h-2 bg-muted rounded overflow-hidden">
            <div
              class="h-full bg-emerald-500 transition-all"
              :style="`width: ${activeJob.total > 0 ? (activeJob.processed / activeJob.total) * 100 : 0}%`"
            />
          </div>
          <div v-if="activeJob.result && Object.keys(activeJob.result).length" class="text-xs text-muted-foreground">
            vinculados: {{ (activeJob.result as any).linked ?? (activeJob.result as any).synced ?? 0 }}
            <span v-if="(activeJob.result as any).errors !== undefined"> · erros: {{ (activeJob.result as any).errors }}</span>
          </div>
          <div v-if="activeJob.error" class="rounded-md border border-red-300 bg-red-50 p-2 text-sm text-red-700">
            {{ activeJob.error }}
          </div>
          <ul class="max-h-48 overflow-auto text-xs space-y-0.5 font-mono bg-muted/30 p-2 rounded">
            <li v-for="(d, idx) in activeJob.details" :key="idx" class="text-muted-foreground whitespace-pre-wrap">
              {{ formatDetail(d) }}
            </li>
            <li v-if="!activeJob.details?.length" class="text-muted-foreground italic">
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
            :disabled="selectedAutoLinkIds.size === 0"
            @click="runAutoLink"
          >
            <Zap class="size-4 mr-1.5" />
            Iniciar Vinculação ({{ selectedAutoLinkIds.size }})
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

    <!-- Sincronizar Todos dialog (SSH-style) -->
    <div v-if="showSyncAll" class="fixed inset-0 z-50 grid place-items-center bg-black/40" @click.self="showSyncAll = false">
      <div class="bg-background rounded-lg shadow-lg w-[min(560px,95vw)] max-h-[85vh] flex flex-col">
        <div class="flex items-center justify-between border-b p-4">
          <div>
            <h3 class="font-semibold flex items-center gap-2">
              <RefreshCw class="size-5 text-cyan-600" :class="activeJob?.status === 'running' || activeJob?.status === 'pending' ? 'animate-spin' : ''" />
              Sincronizar Todos os Produtos
            </h3>
            <p class="text-xs text-muted-foreground mt-1">
              {{ activeJob && (activeJob.status === 'running' || activeJob.status === 'pending')
                ? 'Sincronização em andamento. Você pode fechar este dialog — o processo continua em segundo plano.'
                : 'Selecione as contas/plataformas que deseja sincronizar. O estoque do Bling será enviado para as plataformas selecionadas.' }}
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
                v-for="(integ, idx) in marketplaceIntegrations"
                :key="integ.id"
                class="flex items-center gap-3 px-3 py-2.5 hover:bg-muted/40 cursor-pointer transition-colors"
                :class="idx !== marketplaceIntegrations.length - 1 ? 'border-b' : ''"
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
              <strong>Info:</strong> A sincronização é executada em segundo plano. Você pode fechar esta janela a qualquer momento.
            </p>
          </div>
        </div>

        <!-- Progress phase -->
        <div v-else class="p-4 space-y-3 flex-1 overflow-y-auto">
          <div class="flex justify-between text-sm">
            <span class="flex items-center gap-2">
              <Loader2 v-if="activeJob.status === 'running' || activeJob.status === 'pending'" class="size-4 animate-spin text-cyan-600" />
              <span>Status: <strong>{{ activeJob.status }}</strong></span>
            </span>
            <span class="tabular-nums font-semibold text-cyan-600">
              {{ activeJob.total > 0 ? Math.round((activeJob.processed / activeJob.total) * 100) : 0 }}%
            </span>
          </div>
          <div class="h-2 bg-muted rounded overflow-hidden">
            <div
              class="h-full bg-cyan-500 transition-all"
              :style="`width: ${activeJob.total > 0 ? (activeJob.processed / activeJob.total) * 100 : 0}%`"
            />
          </div>
          <div class="flex justify-between text-xs text-muted-foreground">
            <span>{{ activeJob.processed }} / {{ activeJob.total }} links</span>
            <span v-if="activeJob.result && Object.keys(activeJob.result).length" class="flex gap-3">
              <span class="text-emerald-600">✓ {{ (activeJob.result as any).ok ?? (activeJob.result as any).synced ?? 0 }}</span>
              <span v-if="(activeJob.result as any).failed" class="text-red-600">✗ {{ (activeJob.result as any).failed }}</span>
            </span>
          </div>
          <div v-if="activeJob.error" class="rounded-md border border-red-300 bg-red-50 p-2 text-sm text-red-700">
            {{ activeJob.error }}
          </div>
          <ul class="max-h-60 overflow-auto text-xs space-y-0.5 font-mono bg-muted/30 p-2 rounded">
            <li v-for="(d, idx) in activeJob.details" :key="idx" class="text-muted-foreground whitespace-pre-wrap">
              {{ formatDetail(d) }}
            </li>
            <li v-if="!activeJob.details?.length" class="text-muted-foreground italic">
              processando…
            </li>
          </ul>
        </div>

        <!-- Footer -->
        <div class="border-t p-3 flex gap-2 justify-end">
          <Button v-if="!activeJob" variant="outline" @click="showSyncAll = false">Cancelar</Button>
          <Button
            v-if="!activeJob"
            :disabled="selectedSyncAllIds.size === 0"
            @click="runSyncAll"
          >
            <RefreshCw class="size-4 mr-1.5" />
            Sincronizar ({{ selectedSyncAllIds.size }} contas)
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

    <!-- Refresh Bling stock modal -->
    <div v-if="showRefreshStock" class="fixed inset-0 z-50 grid place-items-center bg-black/40" @click.self="showRefreshStock = false">
      <div class="bg-background rounded-lg shadow-lg w-[min(720px,95vw)]">
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
              <li v-for="(d, idx) in activeJob.details" :key="idx" class="text-muted-foreground whitespace-pre-wrap">
                {{ formatDetail(d) }}
              </li>
              <li v-if="!activeJob.details?.length" class="text-muted-foreground italic">
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
      <div class="bg-background rounded-lg shadow-lg w-[min(600px,95vw)] flex flex-col">
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
      <div class="bg-background rounded-lg shadow-lg w-[min(520px,95vw)]">
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
      <div class="bg-background rounded-lg shadow-lg w-[min(600px,95vw)] flex flex-col">
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
