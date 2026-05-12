<script setup lang="ts">
import { ref, reactive, computed, onUnmounted } from 'vue'
import { Plus, Trash2, Download, RefreshCw, Package, ImageOff, ChevronDown, ChevronRight, Link2, X, Activity, Boxes, Loader2 } from 'lucide-vue-next'

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
const total = ref(0)
const page = ref(1)
const pageSize = 50
const search = ref('')
const filtroIntegration = ref<string>('')
const stockFilter = ref<'' | 'low' | 'zero'>('')
const expanded = ref<Set<string>>(new Set())
const selected = ref<Set<string>>(new Set())
const loading = ref(false)
const error = ref<string | null>(null)

const showImport = ref(false)
const showAutoLink = ref(false)
const showSyncAll = ref(false)
const showRefreshStock = ref(false)

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
    const [pg, integ, settings] = await Promise.all([
      api<ProductPage>(`/api/products?page=${page.value}&page_size=${pageSize}` +
        (search.value ? `&search=${encodeURIComponent(search.value)}` : '') +
        (filtroIntegration.value ? `&integration_id=${filtroIntegration.value}` : '') +
        (stockFilter.value === 'low' ? `&low_stock=true` : '') +
        (stockFilter.value === 'zero' ? `&zero_stock=true` : '')),
      api<Integration[]>('/api/integrations'),
      api<UserSettings>('/api/settings'),
    ])
    items.value = pg.items
    total.value = pg.total
    integrations.value = integ
    autoSyncEnabled.value = settings.daily_sync_enabled
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

type MarketCol = 'shopee' | 'amazon' | 'ml_classico' | 'ml_premium' | 'tiktok'

function linkCol(l: ProductLink): MarketCol | null {
  if (l.platform === 'shopee') return 'shopee'
  if (l.platform === 'amazon') return 'amazon'
  if (l.platform === 'tiktok') return 'tiktok'
  if (l.platform === 'ml') {
    const t = (l.listing_type || '').toLowerCase()
    if (t === 'ml premium') return 'ml_premium'
    return 'ml_classico'
  }
  return null
}

function linksFor(p: Product, col: MarketCol): ProductLink[] {
  return p.links.filter(l => linkCol(l) === col)
}

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))

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

async function syncProduct(id: string) {
  syncingProduct.value.add(id)
  syncingProduct.value = new Set(syncingProduct.value)
  try {
    await api(`/api/sync/product/${id}`, { method: 'POST' })
    await refreshAll()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    syncingProduct.value.delete(id)
    syncingProduct.value = new Set(syncingProduct.value)
  }
}

async function startSyncAll() {
  showSyncAll.value = true
  activeJob.value = null
  try {
    const r = await api<{ job_id: string }>('/api/jobs/sync-all', {
      method: 'POST',
      body: { integration_ids: null, product_ids: null },
    })
    startPolling(r.job_id)
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
    showSyncAll.value = false
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

const autoLinkIntegration = ref<string>('')
const activeJob = ref<Job | null>(null)
let pollHandle: number | null = null

async function startAutoLink() {
  showAutoLink.value = true
  activeJob.value = null
  const r = await api<{ job_id: string }>('/api/jobs/auto-link', {
    method: 'POST',
    body: {
      integration_ids: autoLinkIntegration.value ? [autoLinkIntegration.value] : null,
    },
  })
  startPolling(r.job_id)
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

const stats = computed(() => ({
  total: total.value,
  comStock: items.value.filter(p => p.stock > 0).length,
  baixo: items.value.filter(p => p.stock < p.min_stock).length,
  semSku: items.value.filter(p => !p.sku).length,
}))
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
        <Button v-if="canEdit" size="sm" variant="outline" @click="openImport">
          <Download class="size-4 mr-1.5" /> importar Bling
        </Button>
        <Button v-if="canEdit" size="sm" variant="outline" @click="showImportCsv = true">
          <Download class="size-4 mr-1.5" /> Importar CSV
        </Button>
        <Button v-if="canEdit" size="sm" variant="outline" @click="showNewProduct = true">
          <Plus class="size-4 mr-1.5" /> Novo Produto
        </Button>
        <Button v-if="canEdit" size="sm" variant="outline" @click="startAutoLink">
          <Link2 class="size-4 mr-1.5" /> auto-link
        </Button>
        <Button v-if="canEdit" size="sm" variant="outline" @click="startRefreshBlingStock">
          <Boxes class="size-4 mr-1.5" /> estoque Bling
        </Button>
        <Button v-if="canEdit" size="sm" @click="startSyncAll">
          <Activity class="size-4 mr-1.5" /> sync all
        </Button>
        <Button size="sm" variant="outline" @click="refreshAll">
          <RefreshCw class="size-4 mr-1.5" /> recarregar
        </Button>
      </template>
    </PageHeader>

    <div v-if="error" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">
      {{ error }}
    </div>

    <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
      <StatCard label="Total produtos" :value="stats.total" :icon="Package" />
      <StatCard label="Com estoque" :value="stats.comStock" />
      <StatCard label="Estoque baixo" :value="stats.baixo" tone="warning" />
      <StatCard label="Sem SKU" :value="stats.semSku" tone="danger" :icon="ImageOff" />
    </div>

    <div class="flex flex-wrap gap-2 items-center">
      <Input v-model="search" placeholder="buscar SKU ou nome…" class="w-72" @keyup.enter="refreshAll" />
      <select v-model="filtroIntegration" class="h-9 rounded-md border bg-background px-2 text-sm" @change="refreshAll">
        <option value="">
          <span class="inline-block px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-700">Todas</span>
        </option>
        <option v-for="i in integrations" :key="i.id" :value="i.id">
          <span :class="platformBadgeClass(i.platform)">{{ i.platform }}</span> — {{ i.name }}
        </option>
      </select>
      <select v-model="stockFilter" class="h-9 rounded-md border bg-background px-2 text-sm" @change="refreshAll">
        <option value="">Todo estoque</option>
        <option value="low">Estoque baixo</option>
        <option value="zero">Sem estoque</option>
      </select>
      <Button size="sm" @click="refreshAll">filtrar</Button>
      <span class="ml-auto text-xs text-muted-foreground">
        {{ items.length }} de {{ total }} produtos · pág {{ page }}/{{ totalPages }}
      </span>
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
            <th class="text-center">Bling</th>
            <th class="text-center">Shopee</th>
            <th class="text-center">Amazon</th>
            <th class="text-center">ML Clássico</th>
            <th class="text-center">ML Premium</th>
            <th class="text-center">TikTok</th>
            <th class="text-center">Status</th>
            <th class="w-20"></th>
          </tr>
        </thead>
        <tbody>
          <template v-for="p in items" :key="p.id">
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
              <td class="text-center tabular-nums font-semibold">
                <span :class="p.stock === 0 ? 'text-red-600' : p.stock < p.min_stock ? 'text-amber-600' : ''">
                  {{ p.stock }}
                </span>
              </td>
              <td v-for="col in (['shopee','amazon','ml_classico','ml_premium','tiktok'] as const)" :key="col" class="text-center">
                <div v-if="linksFor(p, col).length === 0" class="text-xs text-muted-foreground">—</div>
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
                <Button
                  v-if="canEdit"
                  size="icon"
                  variant="ghost"
                  :disabled="syncingProduct.has(p.id)"
                  :title="syncingProduct.has(p.id) ? 'sincronizando…' : 'sync produto'"
                  @click="syncProduct(p.id)"
                >
                  <RefreshCw class="size-4" :class="syncingProduct.has(p.id) ? 'animate-spin' : ''" />
                </Button>
                <Button v-if="canDelete" size="icon" variant="ghost" @click="deleteOne(p.id)">
                  <Trash2 class="size-4" />
                </Button>
              </td>
            </tr>
            <tr v-if="expanded.has(p.id)" class="bg-muted/30">
              <td colspan="12" class="p-3">
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
            <td colspan="12" class="py-8 text-center text-sm text-muted-foreground">
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

    <!-- Auto-link modal -->
    <div v-if="showAutoLink" class="fixed inset-0 z-50 grid place-items-center bg-black/40" @click.self="showAutoLink = false">
      <div class="bg-background rounded-lg shadow-lg w-[min(600px,95vw)]">
        <div class="flex items-center justify-between border-b p-3">
          <h3 class="font-semibold">Auto-link</h3>
          <button @click="showAutoLink = false"><X class="size-4" /></button>
        </div>
        <div class="p-3 space-y-3">
          <div v-if="!activeJob" class="space-y-2">
            <label class="block text-sm">Integração (opcional — vazio = todas)</label>
            <select v-model="autoLinkIntegration" class="h-9 w-full rounded-md border bg-background px-2 text-sm">
              <option value="">todas</option>
              <option v-for="i in integrations" :key="i.id" :value="i.id">{{ i.platform }} — {{ i.name }}</option>
            </select>
            <Button class="w-full" @click="startAutoLink">iniciar auto-link</Button>
          </div>
          <div v-else class="space-y-2">
            <div class="flex justify-between text-sm">
              <span>Status: <strong>{{ activeJob.status }}</strong></span>
              <span class="tabular-nums">{{ activeJob.processed }} / {{ activeJob.total }}</span>
            </div>
            <div class="h-2 bg-muted rounded overflow-hidden">
              <div
                class="h-full bg-emerald-500 transition-all"
                :style="`width: ${activeJob.total > 0 ? (activeJob.processed / activeJob.total) * 100 : 0}%`"
              />
            </div>
            <div v-if="activeJob.error" class="rounded-md border border-red-300 bg-red-50 p-2 text-sm text-red-700">
              {{ activeJob.error }}
            </div>
            <ul class="max-h-48 overflow-auto text-xs space-y-1">
              <li v-for="(d, idx) in activeJob.details" :key="idx" class="font-mono text-muted-foreground">
                {{ JSON.stringify(d) }}
              </li>
            </ul>
            <Button v-if="activeJob.status === 'succeeded' || activeJob.status === 'failed'" class="w-full" variant="outline" @click="showAutoLink = false">
              fechar
            </Button>
          </div>
        </div>
      </div>
    </div>

    <!-- Sync all modal -->
    <div v-if="showSyncAll" class="fixed inset-0 z-50 grid place-items-center bg-black/40" @click.self="showSyncAll = false">
      <div class="bg-background rounded-lg shadow-lg w-[min(720px,95vw)]">
        <div class="flex items-center justify-between border-b p-3">
          <h3 class="font-semibold">Sincronização</h3>
          <button @click="showSyncAll = false"><X class="size-4" /></button>
        </div>
        <div class="p-3 space-y-3">
          <div v-if="!activeJob" class="text-sm text-muted-foreground">iniciando…</div>
          <div v-else class="space-y-2">
            <div class="flex justify-between text-sm">
              <span>Status: <strong>{{ activeJob.status }}</strong></span>
              <span class="tabular-nums">
                {{ activeJob.processed }} / {{ activeJob.total }} links
                <span v-if="(activeJob.payload as any)?.total_products" class="text-muted-foreground">
                  · {{ (activeJob.payload as any).total_products }} produtos
                </span>
              </span>
            </div>
            <div class="h-2 bg-muted rounded overflow-hidden">
              <div
                class="h-full bg-emerald-500 transition-all"
                :style="`width: ${activeJob.total > 0 ? (activeJob.processed / activeJob.total) * 100 : 0}%`"
              />
            </div>
            <div v-if="activeJob.error" class="rounded-md border border-red-300 bg-red-50 p-2 text-sm text-red-700">
              {{ activeJob.error }}
            </div>
            <ul class="max-h-72 overflow-auto text-xs space-y-0.5 font-mono bg-muted/30 p-2 rounded">
              <li v-for="(d, idx) in activeJob.details" :key="idx" class="text-muted-foreground whitespace-pre-wrap">
                {{ formatDetail(d) }}
              </li>
              <li v-if="!activeJob.details?.length" class="text-muted-foreground italic">
                aguardando primeiro link…
              </li>
            </ul>
            <Button v-if="activeJob.status === 'succeeded' || activeJob.status === 'failed'" class="w-full" variant="outline" @click="showSyncAll = false">
              fechar
            </Button>
          </div>
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
