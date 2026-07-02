<script setup lang="ts">
import {
  Activity,
  ChevronDown,
  ChevronRight,
  Download,
  Filter,
  Link2,
  Megaphone,
  Plus,
  RefreshCw,
  Trash2,
  X,
} from 'lucide-vue-next'
import { parseBRNumber } from '~/lib/number'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'anuncios', action: 'view' },
})

type Integration = {
  id: string
  platform: 'bling' | 'ml' | 'shopee' | 'amazon'
  name: string
  store_id: string | null
}

type Listing = {
  id: string
  user_id: string
  integration_id: string
  platform: string
  external_id: string
  sku: string | null
  title: string
  description: string | null
  price: number | null
  stock: number | null
  status: 'active' | 'paused' | 'closed' | 'under_review' | 'inactive'
  category: string | null
  thumbnail_url: string | null
  product_id: string | null
  raw_data: Record<string, unknown>
  imported_at: string
  created_at: string
  updated_at: string
}

type ListingPage = { items: Listing[]; total: number; page: number; page_size: number }

type ListingRequest = {
  id: string
  platform: string
  sku: string | null
  product_name: string
  description: string | null
  requested_price: number | null
  category: string | null
  notes: string | null
  status: 'pending' | 'in_progress' | 'completed' | 'rejected'
  created_at: string
  updated_at: string
}

type ProductLite = {
  id: string
  sku: string
  name: string
}

type ProductPage = { items: ProductLite[]; total: number; page: number; page_size: number }

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
}

const { api } = useApi()
const canEdit = useCan('anuncios', 'edit')
const canDelete = useCan('anuncios', 'delete')

const tab = ref<'listings' | 'requests'>('listings')

// ----------------------------------------------------------- listings state
const integrations = ref<Integration[]>([])
const items = ref<Listing[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 50
const search = ref('')
const filtroIntegration = ref<string>('')
const filtroPlatform = ref<string>('')
const filtroStatus = ref<string>('')
const onlyUnlinked = ref(false)
const expanded = ref<Set<string>>(new Set())
const loading = ref(false)
const error = ref<string | null>(null)

const PLATFORM_LABEL: Record<string, string> = {
  bling: 'Bling',
  ml: 'Mercado Livre',
  shopee: 'Shopee',
  amazon: 'Amazon',
}

const STATUS_LABEL: Record<Listing['status'], string> = {
  active: 'ativo',
  paused: 'pausado',
  closed: 'encerrado',
  under_review: 'em revisão',
  inactive: 'inativo',
}

async function refreshAll() {
  loading.value = true
  error.value = null
  try {
    const qs = new URLSearchParams({
      page: String(page.value),
      page_size: String(pageSize),
    })
    if (search.value) qs.set('search', search.value)
    if (filtroIntegration.value) qs.set('integration_id', filtroIntegration.value)
    if (filtroPlatform.value) qs.set('platform', filtroPlatform.value)
    if (filtroStatus.value) qs.set('status', filtroStatus.value)
    if (onlyUnlinked.value) qs.set('unlinked', 'true')

    const [pg, integ] = await Promise.all([
      api<ListingPage>(`/api/listings?${qs.toString()}`),
      api<Integration[]>('/api/integrations'),
    ])
    items.value = pg.items
    total.value = pg.total
    integrations.value = integ
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    loading.value = false
  }
}
await refreshAll()

const integrationById = computed(() =>
  Object.fromEntries(integrations.value.map(i => [i.id, i])),
)
const importableIntegrations = computed(() =>
  integrations.value.filter(i => i.platform === 'ml' || i.platform === 'shopee'),
)
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))

const stats = computed(() => ({
  total: total.value,
  unlinked: items.value.filter(i => !i.product_id).length,
  active: items.value.filter(i => i.status === 'active').length,
  review: items.value.filter(i => i.status === 'under_review').length,
}))

function toggleExpand(id: string) {
  if (expanded.value.has(id)) expanded.value.delete(id)
  else expanded.value.add(id)
  expanded.value = new Set(expanded.value)
}

function brl(cents: number | null | undefined) {
  if (cents === null || cents === undefined) return '—'
  return (cents / 100).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
}

function statusPill(s: Listing['status']) {
  if (s === 'active') return 'pill-success'
  if (s === 'paused') return 'pill-warning'
  if (s === 'under_review') return 'pill-warning'
  if (s === 'closed' || s === 'inactive') return 'pill-muted'
  return 'pill-muted'
}

// -------------------------------------------------------------- delete/patch
async function deleteListing(id: string) {
  if (!confirm('Excluir anúncio?')) return
  await api(`/api/listings/${id}`, { method: 'DELETE' })
  await refreshAll()
}

async function patchListing(id: string, body: Record<string, unknown>) {
  await api(`/api/listings/${id}`, { method: 'PATCH', body })
  await refreshAll()
}

// -------------------------------------------------------------- import flow
const showImport = ref(false)
const importIntegration = ref<string>('')
const importMaxPages = ref<number | null>(null)
const activeJob = ref<Job | null>(null)
let pollHandle: number | null = null

function openImport() {
  importIntegration.value = importableIntegrations.value[0]?.id || ''
  importMaxPages.value = null
  activeJob.value = null
  showImport.value = true
}

async function startImport() {
  if (!importIntegration.value) return
  activeJob.value = null
  const r = await api<{ job_id: string }>('/api/listings/import', {
    method: 'POST',
    body: {
      integration_id: importIntegration.value,
      max_pages: importMaxPages.value || undefined,
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
        if (pollHandle) {
          clearInterval(pollHandle)
          pollHandle = null
        }
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

// -------------------------------------------------------------- link picker
const showLinkPicker = ref(false)
const linkTarget = ref<Listing | null>(null)
const linkSearch = ref('')
const linkResults = ref<ProductLite[]>([])
const linkLoading = ref(false)

async function openLinkPicker(l: Listing) {
  linkTarget.value = l
  linkSearch.value = l.sku || ''
  linkResults.value = []
  showLinkPicker.value = true
  await searchProducts()
}

async function searchProducts() {
  linkLoading.value = true
  try {
    const qs = new URLSearchParams({ page: '1', page_size: '20' })
    if (linkSearch.value) qs.set('search', linkSearch.value)
    const r = await api<ProductPage>(`/api/products?${qs.toString()}`)
    linkResults.value = r.items
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    linkLoading.value = false
  }
}

async function attachProduct(p: ProductLite) {
  if (!linkTarget.value) return
  await patchListing(linkTarget.value.id, {
    product_id: p.id,
    sku: p.sku,
  })
  showLinkPicker.value = false
}

async function detachProduct(l: Listing) {
  if (!confirm('Remover vínculo com produto?')) return
  await patchListing(l.id, { product_id: null })
}

// -------------------------------------------------------------- requests tab
const requests = ref<ListingRequest[]>([])
const reqLoading = ref(false)
const showNewRequest = ref(false)
const newReq = ref<{
  platform: string
  product_name: string
  sku: string
  requested_price: string
  category: string
  notes: string
}>({
  platform: 'ml',
  product_name: '',
  sku: '',
  requested_price: '',
  category: '',
  notes: '',
})

async function loadRequests() {
  reqLoading.value = true
  try {
    requests.value = await api<ListingRequest[]>('/api/listing-requests')
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    reqLoading.value = false
  }
}

watch(tab, async v => {
  if (v === 'requests' && requests.value.length === 0) await loadRequests()
})

async function createRequest() {
  if (!newReq.value.product_name) return
  const priceNum = parseBRNumber(newReq.value.requested_price.replace(/[^0-9.,]/g, ''))
  const priceCents = priceNum == null ? null : Math.round(priceNum * 100)
  await api('/api/listing-requests', {
    method: 'POST',
    body: {
      platform: newReq.value.platform,
      product_name: newReq.value.product_name,
      sku: newReq.value.sku || null,
      requested_price: priceCents,
      category: newReq.value.category || null,
      notes: newReq.value.notes || null,
    },
  })
  newReq.value = {
    platform: 'ml',
    product_name: '',
    sku: '',
    requested_price: '',
    category: '',
    notes: '',
  }
  showNewRequest.value = false
  await loadRequests()
}

async function patchRequest(id: string, body: Record<string, unknown>) {
  await api(`/api/listing-requests/${id}`, { method: 'PATCH', body })
  await loadRequests()
}

async function deleteRequest(id: string) {
  if (!confirm('Excluir solicitação?')) return
  await api(`/api/listing-requests/${id}`, { method: 'DELETE' })
  await loadRequests()
}

function reqStatusPill(s: ListingRequest['status']) {
  if (s === 'completed') return 'pill-success'
  if (s === 'rejected') return 'pill-muted'
  if (s === 'in_progress') return 'pill-warning'
  return 'pill-muted'
}
</script>

<template>
  <div class="space-y-5">
    <PageHeader title="Anúncios" description="Listagens publicadas em cada marketplace.">
      <template #actions>
        <Button
          v-if="canEdit"
          size="sm"
          variant="outline"
          :disabled="importableIntegrations.length === 0"
          @click="openImport"
        >
          <Download class="size-4 mr-1.5" /> importar
        </Button>
        <Button size="sm" variant="outline" @click="refreshAll">
          <RefreshCw class="size-4 mr-1.5" /> recarregar
        </Button>
      </template>
    </PageHeader>

    <div v-if="error" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">
      {{ error }}
    </div>

    <div class="flex gap-2 border-b">
      <button
        class="px-3 py-2 text-sm border-b-2 -mb-px"
        :class="tab === 'listings' ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground'"
        @click="tab = 'listings'"
      >
        anúncios importados
      </button>
      <button
        class="px-3 py-2 text-sm border-b-2 -mb-px"
        :class="tab === 'requests' ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground'"
        @click="tab = 'requests'"
      >
        solicitações de anúncio
      </button>
    </div>

    <!-- =========================== LISTINGS =========================== -->
    <template v-if="tab === 'listings'">
      <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatCard label="Total anúncios" :value="stats.total" :icon="Megaphone" />
        <StatCard label="Sem produto" :value="stats.unlinked" tone="warning" />
        <StatCard label="Ativos" :value="stats.active" />
        <StatCard label="Em revisão" :value="stats.review" tone="warning" />
      </div>

      <div class="flex flex-wrap gap-2 items-center">
        <Input
          v-model="search"
          placeholder="buscar título, SKU ou ID…"
          class="w-72"
          @keyup.enter="page = 1; refreshAll()"
        />
        <select
          v-model="filtroIntegration"
          class="h-9 rounded-md border bg-background px-2 text-sm"
          @change="page = 1; refreshAll()"
        >
          <option value="">todas integrações</option>
          <option v-for="i in integrations" :key="i.id" :value="i.id">
            {{ PLATFORM_LABEL[i.platform] || i.platform }} — {{ i.name }}
          </option>
        </select>
        <select
          v-model="filtroPlatform"
          class="h-9 rounded-md border bg-background px-2 text-sm"
          @change="page = 1; refreshAll()"
        >
          <option value="">todos canais</option>
          <option value="ml">Mercado Livre</option>
          <option value="shopee">Shopee</option>
          <option value="amazon">Amazon</option>
          <option value="bling">Bling</option>
        </select>
        <select
          v-model="filtroStatus"
          class="h-9 rounded-md border bg-background px-2 text-sm"
          @change="page = 1; refreshAll()"
        >
          <option value="">todos status</option>
          <option value="active">ativo</option>
          <option value="paused">pausado</option>
          <option value="under_review">em revisão</option>
          <option value="closed">encerrado</option>
          <option value="inactive">inativo</option>
        </select>
        <label class="flex items-center gap-1 text-sm">
          <input v-model="onlyUnlinked" type="checkbox" @change="page = 1; refreshAll()" />
          sem produto vinculado
        </label>
        <Button size="sm" variant="ghost" @click="page = 1; refreshAll()">
          <Filter class="size-4 mr-1.5" /> filtrar
        </Button>
        <span class="ml-auto text-xs text-muted-foreground">
          {{ items.length }} de {{ total }} · pág {{ page }}/{{ totalPages }}
        </span>
      </div>

      <div class="table-card">
        <table class="w-full">
          <thead>
            <tr>
              <th class="w-8"></th>
              <th>External ID</th>
              <th>Título</th>
              <th>Canal</th>
              <th>SKU</th>
              <th>Produto</th>
              <th class="text-right">Preço</th>
              <th class="text-right">Estoque</th>
              <th>Status</th>
              <th class="text-right">Ações</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="l in items" :key="l.id">
              <tr>
                <td>
                  <button
                    class="size-6 grid place-items-center rounded hover:bg-muted"
                    @click="toggleExpand(l.id)"
                  >
                    <component :is="expanded.has(l.id) ? ChevronDown : ChevronRight" class="size-4" />
                  </button>
                </td>
                <td class="font-mono text-xs text-muted-foreground">{{ l.external_id }}</td>
                <td class="font-medium max-w-xs truncate" :title="l.title">{{ l.title }}</td>
                <td><span class="pill pill-muted">{{ PLATFORM_LABEL[l.platform] || l.platform }}</span></td>
                <td class="font-mono text-xs">
                  <span :class="!l.sku ? 'text-red-500' : ''">{{ l.sku || '—' }}</span>
                </td>
                <td>
                  <span v-if="l.product_id" class="pill pill-success">vinculado</span>
                  <span v-else class="text-xs text-muted-foreground">—</span>
                </td>
                <td class="text-right tabular-nums">{{ brl(l.price) }}</td>
                <td class="text-right tabular-nums">{{ l.stock ?? '—' }}</td>
                <td><span :class="statusPill(l.status)">{{ STATUS_LABEL[l.status] }}</span></td>
                <td class="text-right">
                  <Button
                    v-if="canEdit && !l.product_id"
                    size="icon"
                    variant="ghost"
                    title="vincular produto"
                    @click="openLinkPicker(l)"
                  >
                    <Link2 class="size-4" />
                  </Button>
                  <Button
                    v-if="canEdit && l.product_id"
                    size="icon"
                    variant="ghost"
                    title="desvincular"
                    @click="detachProduct(l)"
                  >
                    <X class="size-4" />
                  </Button>
                  <Button
                    v-if="canDelete"
                    size="icon"
                    variant="ghost"
                    title="excluir"
                    @click="deleteListing(l.id)"
                  >
                    <Trash2 class="size-4" />
                  </Button>
                </td>
              </tr>
              <tr v-if="expanded.has(l.id)" class="bg-muted/30">
                <td colspan="10" class="p-3">
                  <div class="grid grid-cols-2 gap-4 text-xs">
                    <div>
                      <div class="text-muted-foreground mb-1">Integração</div>
                      <div>{{ integrationById[l.integration_id]?.name || l.integration_id }}</div>
                    </div>
                    <div>
                      <div class="text-muted-foreground mb-1">Categoria</div>
                      <div>{{ l.category || '—' }}</div>
                    </div>
                    <div>
                      <div class="text-muted-foreground mb-1">Importado em</div>
                      <div>{{ new Date(l.imported_at).toLocaleString('pt-BR') }}</div>
                    </div>
                    <div>
                      <div class="text-muted-foreground mb-1">Atualizado em</div>
                      <div>{{ new Date(l.updated_at).toLocaleString('pt-BR') }}</div>
                    </div>
                    <div v-if="l.description" class="col-span-2">
                      <div class="text-muted-foreground mb-1">Descrição</div>
                      <div class="whitespace-pre-wrap">{{ l.description }}</div>
                    </div>
                    <div v-if="l.thumbnail_url" class="col-span-2">
                      <img :src="l.thumbnail_url" :alt="l.title" class="max-h-32 rounded border" />
                    </div>
                  </div>
                </td>
              </tr>
            </template>
            <tr v-if="items.length === 0">
              <td colspan="10" class="py-8 text-center text-sm text-muted-foreground">
                {{ loading ? 'carregando…' : 'Nenhum anúncio. Use "importar" para puxar do marketplace.' }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="flex justify-between items-center text-sm">
        <Button size="sm" variant="outline" :disabled="page <= 1" @click="page--; refreshAll()">
          anterior
        </Button>
        <span>página {{ page }} de {{ totalPages }}</span>
        <Button size="sm" variant="outline" :disabled="page >= totalPages" @click="page++; refreshAll()">
          próxima
        </Button>
      </div>
    </template>

    <!-- =========================== REQUESTS =========================== -->
    <template v-else>
      <div class="flex justify-end">
        <Button v-if="canEdit" size="sm" @click="showNewRequest = true">
          <Plus class="size-4 mr-1.5" /> nova solicitação
        </Button>
      </div>

      <div class="table-card">
        <table class="w-full">
          <thead>
            <tr>
              <th>Canal</th>
              <th>SKU</th>
              <th>Nome do produto</th>
              <th class="text-right">Preço</th>
              <th>Categoria</th>
              <th>Status</th>
              <th>Notas</th>
              <th class="text-right">Ações</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in requests" :key="r.id">
              <td>{{ PLATFORM_LABEL[r.platform] || r.platform }}</td>
              <td class="font-mono text-xs">{{ r.sku || '—' }}</td>
              <td class="font-medium">{{ r.product_name }}</td>
              <td class="text-right tabular-nums">{{ brl(r.requested_price) }}</td>
              <td>{{ r.category || '—' }}</td>
              <td>
                <select
                  v-if="canEdit"
                  :value="r.status"
                  class="h-8 rounded-md border bg-background px-2 text-xs"
                  @change="patchRequest(r.id, { status: ($event.target as HTMLSelectElement).value })"
                >
                  <option value="pending">pendente</option>
                  <option value="in_progress">em andamento</option>
                  <option value="completed">concluído</option>
                  <option value="rejected">rejeitado</option>
                </select>
                <span v-else :class="reqStatusPill(r.status)">{{ r.status }}</span>
              </td>
              <td class="max-w-xs truncate text-xs text-muted-foreground" :title="r.notes || ''">
                {{ r.notes || '—' }}
              </td>
              <td class="text-right">
                <Button v-if="canDelete" size="icon" variant="ghost" @click="deleteRequest(r.id)">
                  <Trash2 class="size-4" />
                </Button>
              </td>
            </tr>
            <tr v-if="requests.length === 0">
              <td colspan="8" class="py-8 text-center text-sm text-muted-foreground">
                {{ reqLoading ? 'carregando…' : 'Nenhuma solicitação.' }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <!-- ============================ Import modal ============================ -->
    <div
      v-if="showImport"
      class="fixed inset-0 z-50 grid place-items-center bg-black/40"
      @click.self="showImport = false"
    >
      <div class="bg-background rounded-lg shadow-lg w-[min(600px,95vw)]">
        <div class="flex items-center justify-between border-b p-3">
          <h3 class="font-semibold">Importar anúncios</h3>
          <button @click="showImport = false"><X class="size-4" /></button>
        </div>
        <div class="p-3 space-y-3">
          <div v-if="!activeJob" class="space-y-2">
            <label class="block text-sm">Integração (ML ou Shopee)</label>
            <select
              v-model="importIntegration"
              class="h-9 w-full rounded-md border bg-background px-2 text-sm"
            >
              <option v-for="i in importableIntegrations" :key="i.id" :value="i.id">
                {{ PLATFORM_LABEL[i.platform] }} — {{ i.name }}
              </option>
              <option v-if="importableIntegrations.length === 0" disabled value="">
                sem integração ML ou Shopee conectada
              </option>
            </select>

            <label class="block text-sm pt-2">Limite de páginas (opcional)</label>
            <input
              v-model.number="importMaxPages"
              type="number"
              min="1"
              max="100"
              placeholder="vazio = todas"
              class="h-9 w-full rounded-md border bg-background px-2 text-sm"
            />

            <Button class="w-full mt-2" :disabled="!importIntegration" @click="startImport">
              <Activity class="size-4 mr-1.5" /> iniciar importação
            </Button>
          </div>
          <div v-else class="space-y-2">
            <div class="flex justify-between text-sm">
              <span>Status: <strong>{{ activeJob.status }}</strong></span>
              <span class="tabular-nums">{{ activeJob.processed }} processados</span>
            </div>
            <div class="h-2 bg-muted rounded overflow-hidden">
              <div
                class="h-full bg-emerald-500 transition-all"
                :style="`width: ${activeJob.status === 'succeeded' ? 100 : activeJob.status === 'failed' ? 100 : Math.min(95, activeJob.processed)}%`"
              />
            </div>
            <div v-if="activeJob.error" class="rounded-md border border-red-300 bg-red-50 p-2 text-sm text-red-700">
              {{ activeJob.error }}
            </div>
            <div
              v-if="activeJob.status === 'succeeded'"
              class="rounded-md border bg-emerald-50 p-2 text-sm text-emerald-800"
            >
              Criados: {{ (activeJob.result as any).created ?? 0 }} ·
              atualizados: {{ (activeJob.result as any).updated ?? 0 }} ·
              ignorados: {{ (activeJob.result as any).skipped ?? 0 }} ·
              vinculados: {{ (activeJob.result as any).linked ?? 0 }}
            </div>
            <Button
              v-if="activeJob.status === 'succeeded' || activeJob.status === 'failed'"
              class="w-full"
              variant="outline"
              @click="showImport = false"
            >
              fechar
            </Button>
          </div>
        </div>
      </div>
    </div>

    <!-- ============================ Link product modal ============================ -->
    <div
      v-if="showLinkPicker"
      class="fixed inset-0 z-50 grid place-items-center bg-black/40"
      @click.self="showLinkPicker = false"
    >
      <div class="bg-background rounded-lg shadow-lg w-[min(700px,95vw)] max-h-[85vh] flex flex-col">
        <div class="flex items-center justify-between border-b p-3">
          <h3 class="font-semibold">Vincular produto</h3>
          <button @click="showLinkPicker = false"><X class="size-4" /></button>
        </div>
        <div class="p-3 space-y-3 overflow-auto">
          <div class="text-sm text-muted-foreground">
            Anúncio: <strong>{{ linkTarget?.title }}</strong>
            <span v-if="linkTarget?.sku" class="font-mono ml-2">SKU: {{ linkTarget.sku }}</span>
          </div>
          <div class="flex gap-2">
            <Input
              v-model="linkSearch"
              placeholder="buscar SKU ou nome do produto…"
              @keyup.enter="searchProducts"
            />
            <Button @click="searchProducts">buscar</Button>
          </div>
          <table class="w-full text-sm">
            <thead>
              <tr>
                <th>SKU</th>
                <th>Nome</th>
                <th class="w-24"></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="p in linkResults" :key="p.id">
                <td class="font-mono text-xs">{{ p.sku }}</td>
                <td>{{ p.name }}</td>
                <td class="text-right">
                  <Button size="sm" @click="attachProduct(p)">vincular</Button>
                </td>
              </tr>
              <tr v-if="linkResults.length === 0">
                <td colspan="3" class="py-4 text-center text-muted-foreground">
                  {{ linkLoading ? 'carregando…' : 'nenhum produto encontrado' }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- ============================ New request modal ============================ -->
    <div
      v-if="showNewRequest"
      class="fixed inset-0 z-50 grid place-items-center bg-black/40"
      @click.self="showNewRequest = false"
    >
      <div class="bg-background rounded-lg shadow-lg w-[min(560px,95vw)]">
        <div class="flex items-center justify-between border-b p-3">
          <h3 class="font-semibold">Nova solicitação de anúncio</h3>
          <button @click="showNewRequest = false"><X class="size-4" /></button>
        </div>
        <div class="p-3 space-y-3">
          <div>
            <label class="block text-sm mb-1">Canal</label>
            <select
              v-model="newReq.platform"
              class="h-9 w-full rounded-md border bg-background px-2 text-sm"
            >
              <option value="ml">Mercado Livre</option>
              <option value="shopee">Shopee</option>
              <option value="amazon">Amazon</option>
              <option value="bling">Bling</option>
            </select>
          </div>
          <div>
            <label class="block text-sm mb-1">Nome do produto *</label>
            <Input v-model="newReq.product_name" />
          </div>
          <div class="grid grid-cols-2 gap-2">
            <div>
              <label class="block text-sm mb-1">SKU</label>
              <Input v-model="newReq.sku" />
            </div>
            <div>
              <label class="block text-sm mb-1">Preço (R$)</label>
              <Input v-model="newReq.requested_price" placeholder="ex: 19,90" />
            </div>
          </div>
          <div>
            <label class="block text-sm mb-1">Categoria</label>
            <Input v-model="newReq.category" />
          </div>
          <div>
            <label class="block text-sm mb-1">Notas</label>
            <textarea
              v-model="newReq.notes"
              rows="3"
              class="w-full rounded-md border bg-background p-2 text-sm"
            />
          </div>
        </div>
        <div class="border-t p-3 flex gap-2 justify-end">
          <Button variant="outline" @click="showNewRequest = false">cancelar</Button>
          <Button :disabled="!newReq.product_name" @click="createRequest">
            <Plus class="size-4 mr-1.5" /> criar
          </Button>
        </div>
      </div>
    </div>
  </div>
</template>
