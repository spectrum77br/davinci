<script setup lang="ts">
// Produtos › Anúncios (Organização do DaVinci, 13/11/2026, item 2): a aba
// "anúncios importados" que era a tela /anuncios, agora dentro de Produtos e só
// do departamento escolhido (Celular · Mala · Eletro · Sem departamento). Os
// cartões contam o departamento inteiro (vêm da API em `resumo` — antes eram
// contados só na página de 50). "Solicitações de anúncio" saiu e não veio
// (a tabela e a API continuam). Os botões importar / recarregar ficam no
// cabeçalho da página: ela chama openImport / refreshAll daqui (defineExpose).
import {
  Activity,
  ChevronDown,
  ChevronRight,
  Filter,
  Link2,
  Megaphone,
  Trash2,
  X,
} from 'lucide-vue-next'

const props = defineProps<{ departamento: string }>()
// `mudou`: um anúncio pode ter trocado de departamento (vincular/desvincular,
// excluir, importação terminou) — a página recarrega os números das abas.
const emit = defineEmits<{ (e: 'mudou'): void }>()

type Integration = {
  id: string
  platform: 'bling' | 'ml' | 'shopee' | 'amazon' | 'carrefour'
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

type ListingResumo = { total: number; sem_produto: number; ativos: number; em_revisao: number }
type ListingPage = {
  items: Listing[]; total: number; page: number; page_size: number; resumo?: ListingResumo | null
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

// ----------------------------------------------------------- listings state
const integrations = ref<Integration[]>([])
const items = ref<Listing[]>([])
const total = ref(0)
const resumo = ref<ListingResumo | null>(null)
const page = ref(1)
const pageSize = 50
const search = ref('')
const filtroIntegration = ref<string>('')
const filtroPlatform = ref<string>('')
const filtroStatus = ref<string>('')
const onlyUnlinked = ref(false)
const expanded = ref<Set<string>>(new Set())
// Começa "carregando": a primeira leitura é no onMounted (sem await no setup,
// para o painel trocar com a aba Produtos sem precisar de <Suspense>).
const loading = ref(true)
const error = ref<string | null>(null)

const PLATFORM_LABEL: Record<string, string> = {
  bling: 'Bling',
  ml: 'Mercado Livre',
  shopee: 'Shopee',
  amazon: 'Amazon',
  carrefour: 'Carrefour',
}

const STATUS_LABEL: Record<Listing['status'], string> = {
  active: 'ativo',
  paused: 'pausado',
  closed: 'encerrado',
  under_review: 'em revisão',
  inactive: 'inativo',
}

// Resposta de um departamento anterior que chega atrasada é descartada.
let seq = 0
async function refreshAll() {
  const minha = ++seq
  loading.value = true
  error.value = null
  try {
    const qs = new URLSearchParams({
      page: String(page.value),
      page_size: String(pageSize),
    })
    qs.set('departamento', props.departamento)
    if (search.value) qs.set('search', search.value)
    if (filtroIntegration.value) qs.set('integration_id', filtroIntegration.value)
    if (filtroPlatform.value) qs.set('platform', filtroPlatform.value)
    if (filtroStatus.value) qs.set('status', filtroStatus.value)
    if (onlyUnlinked.value) qs.set('unlinked', 'true')

    const [pg, integ] = await Promise.all([
      api<ListingPage>(`/api/listings?${qs.toString()}`),
      api<Integration[]>('/api/integrations'),
    ])
    if (minha !== seq) return
    items.value = pg.items
    total.value = pg.total
    resumo.value = pg.resumo ?? null
    integrations.value = integ
  } catch (e: any) {
    if (minha !== seq) return
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    if (minha === seq) loading.value = false
  }
}
onMounted(() => { void refreshAll() })

// Trocou o departamento: volta para a página 1 do departamento novo.
watch(() => props.departamento, () => {
  page.value = 1
  expanded.value = new Set()
  void refreshAll()
})

const integrationById = computed(() =>
  Object.fromEntries(integrations.value.map(i => [i.id, i])),
)
const importableIntegrations = computed(() =>
  integrations.value.filter(i => i.platform === 'ml' || i.platform === 'shopee' || i.platform === 'carrefour'),
)
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))

// Cartões do departamento: vêm do banco (resumo). API antiga sem `resumo`
// cai na conta de antes (só a página aberta).
const stats = computed(() => ({
  total: resumo.value?.total ?? total.value,
  unlinked: resumo.value?.sem_produto ?? items.value.filter(i => !i.product_id).length,
  active: resumo.value?.ativos ?? items.value.filter(i => i.status === 'active').length,
  review: resumo.value?.em_revisao ?? items.value.filter(i => i.status === 'under_review').length,
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
  emit('mudou')
}

async function patchListing(id: string, body: Record<string, unknown>) {
  await api(`/api/listings/${id}`, { method: 'PATCH', body })
  await refreshAll()
  emit('mudou')
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
        emit('mudou')
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

// Busca em TODOS os produtos (não só no departamento): é vinculando que o
// anúncio de "Sem departamento" vai para o departamento certo.
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

defineExpose({ openImport, refreshAll, importableIntegrations })
</script>

<template>
  <div class="space-y-5">
    <div v-if="error" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">
      {{ error }}
    </div>

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
  </div>
</template>
