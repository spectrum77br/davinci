<script setup lang="ts">
// Controle de Estoque — operator-facing planilha.
//
// 3 tabs, each backed by a single GET on the backend. The default date
// window opens to "today" everywhere; the Envios tab opens to last 7
// days because per-day rollups need a wider window to be useful.
//
// Permission guard: definePageMeta middleware redirects users without
// controle_estoque.view to /403. The auth.global guard also bounces
// operadores (role != admin + stock_tag set) here whenever they try to
// navigate elsewhere, so this page is effectively their home screen.
import { computed, onMounted, ref, watch } from 'vue'
import { Boxes, Truck, ClipboardList, Loader2 } from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'controle_estoque', action: 'view' },
})

const { api } = useApi()
const auth = useAuthStore()

// ── Types ─────────────────────────────────────────────────────────────
type ProdutoRow = {
  sku: string
  nome: string
  entrada_qty: number
  entrada_obs: string
  entrada_movement_id: string | null
  saida_qty: number
  saida_origens: string
  saida_movement_id: string | null
  saldo: number
  reserva: number
  conferido: boolean
}
type PedidoRow = {
  id: string
  data: string | null
  pedido_bling: string | null
  pedido_marketplace: string | null
  loja: string | null
  sku: string | null
  produto: string | null
  quantidade: number
  status: 'enviado' | 'nao_enviado'
  conferido: boolean
  observacao: string | null
  bling_id: number | null
}
type EnvioRow = { data: string; envios: number; conferido: boolean }

// ── State ─────────────────────────────────────────────────────────────
type Tab = 'estoque' | 'pedidos' | 'envios'
const tab = ref<Tab>('estoque')

function isoToday(): string {
  return new Date().toISOString().slice(0, 10)
}
function isoDaysAgo(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() - days)
  return d.toISOString().slice(0, 10)
}

const dataInicio = ref(isoToday())
const dataFim = ref(isoToday())
// Envios tab uses a wider default window. Switching to that tab the
// first time auto-bumps the date inputs so the operator sees ~a week.
const enviosInitialized = ref(false)

// Admin override: pick a specific tag to view. Operadores ignore this
// (the backend uses their stock_tag).
const isAdmin = computed(() => auth.user?.role === 'admin')
const tagOverride = ref<string>('')

const statusFilter = ref<'all' | 'enviado' | 'nao_enviado'>('all')
const search = ref('')

// Data
const produtos = ref<ProdutoRow[]>([])
const pedidos = ref<PedidoRow[]>([])
const envios = ref<{ items: EnvioRow[]; total: number; total_conferido: number }>({
  items: [], total: 0, total_conferido: 0,
})

const loading = ref(false)
const errorText = ref<string | null>(null)

// ── Fetchers ──────────────────────────────────────────────────────────
function dateParams(): string {
  const parts = [`data_inicio=${dataInicio.value}`, `data_fim=${dataFim.value}`]
  if (isAdmin.value && tagOverride.value) parts.push(`tag=${tagOverride.value}`)
  return parts.join('&')
}

async function loadEstoque() {
  loading.value = true
  errorText.value = null
  try {
    const r = await api<{ data: ProdutoRow[] }>(`/api/estoque/produtos?${dateParams()}`)
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
    const qs = [dateParams()]
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
    const r = await api<{ data: EnvioRow[]; total_envios: number; total_conferido: number }>(
      `/api/estoque/envios?${dateParams()}`,
    )
    envios.value = {
      items: r.data || [],
      total: r.total_envios || 0,
      total_conferido: r.total_conferido || 0,
    }
  } catch (e: any) {
    errorText.value = e?.data?.detail?.code || e?.message || 'load_failed'
    envios.value = { items: [], total: 0, total_conferido: 0 }
  } finally {
    loading.value = false
  }
}

function loadCurrentTab() {
  if (tab.value === 'estoque') return loadEstoque()
  if (tab.value === 'pedidos') return loadPedidos()
  return loadEnvios()
}

watch(tab, (newTab) => {
  if (newTab === 'envios' && !enviosInitialized.value) {
    dataInicio.value = isoDaysAgo(6)
    dataFim.value = isoToday()
    enviosInitialized.value = true
  }
  void loadCurrentTab()
})
watch([dataInicio, dataFim, tagOverride, statusFilter], () => {
  void loadCurrentTab()
})
onMounted(() => {
  void loadCurrentTab()
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
    await toggleCheck('estoque', row.sku, dataInicio.value, next)
  } catch {
    row.conferido = !next  // revert on error
  }
}
async function togglePedido(row: PedidoRow) {
  const next = !row.conferido
  row.conferido = next
  try {
    await toggleCheck('pedido', row.id, (row.data || '').slice(0, 10), next, row.observacao)
  } catch {
    row.conferido = !next
  }
}
async function patchPedidoObs(row: PedidoRow, newObs: string) {
  row.observacao = newObs
  try {
    await toggleCheck('pedido', row.id, (row.data || '').slice(0, 10), row.conferido, newObs)
  } catch {
    // Silent — next reload will surface the truth.
  }
}
async function toggleEnvio(row: EnvioRow) {
  const next = !row.conferido
  row.conferido = next
  try {
    await toggleCheck('envio', row.data, row.data, next)
    // Recompute summary footer locally.
    if (next) envios.value.total_conferido += row.envios
    else envios.value.total_conferido = Math.max(0, envios.value.total_conferido - row.envios)
  } catch {
    row.conferido = !next
  }
}

async function patchMovementObs(movementId: string, newObs: string, row: ProdutoRow) {
  row.entrada_obs = newObs
  try {
    const params = new URLSearchParams()
    if (newObs) params.set('observacao', newObs)
    await api(`/api/estoque/movement/${movementId}/obs?${params.toString()}`, { method: 'PATCH' })
  } catch {
    // Reload to revert
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
</script>

<template>
  <div class="space-y-3 p-4">
    <!-- Header + tabs -->
    <div class="flex flex-wrap items-center gap-3">
      <div class="flex items-center gap-2">
        <Boxes class="h-5 w-5 text-primary" />
        <h1 class="text-xl font-semibold">Controle de Estoque</h1>
      </div>
      <div class="flex gap-1 rounded-md bg-muted/40 p-1 w-fit ml-auto">
        <button
          v-for="t in (['estoque', 'pedidos', 'envios'] as const)"
          :key="t"
          class="px-3 py-1.5 rounded text-sm transition-colors inline-flex items-center gap-1.5"
          :class="tab === t ? 'bg-background shadow-sm font-medium' : 'hover:bg-background/60 text-muted-foreground'"
          @click="tab = t"
        >
          <Boxes v-if="t === 'estoque'" class="size-4" />
          <ClipboardList v-else-if="t === 'pedidos'" class="size-4" />
          <Truck v-else class="size-4" />
          {{ t === 'estoque' ? 'Estoque' : t === 'pedidos' ? 'Pedidos' : 'Envios' }}
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
      <label class="inline-flex items-center gap-1">
        De:
        <input v-model="dataInicio" type="date" class="h-7 border rounded px-2 bg-background" />
      </label>
      <label class="inline-flex items-center gap-1">
        Até:
        <input v-model="dataFim" type="date" class="h-7 border rounded px-2 bg-background" />
      </label>
      <label v-if="isAdmin" class="inline-flex items-center gap-1">
        Tag:
        <select v-model="tagOverride" class="h-7 border rounded px-2 bg-background">
          <option value="">todas</option>
          <option value="ci">ci</option>
          <option value="pi">pi</option>
          <option value="ra">ra</option>
          <option value="sa">sa</option>
          <option value="sp">sp</option>
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
    <div v-if="tab === 'estoque'" class="border rounded-md overflow-x-auto">
      <table class="w-full text-xs border-collapse">
        <thead>
          <tr class="bg-muted/50">
            <th class="px-2 py-1 text-left text-[11px] font-semibold border-b" colspan="2">Identificação</th>
            <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-amber-50 dark:bg-amber-900/20" colspan="2">Entrada</th>
            <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-amber-50 dark:bg-amber-900/20" colspan="2">Saída</th>
            <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-emerald-50 dark:bg-emerald-900/20" colspan="2">Saldo</th>
            <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-gray-100 dark:bg-gray-800/40" colspan="1">Conf.</th>
          </tr>
          <tr class="bg-muted/30 text-[10px] uppercase tracking-wide">
            <th class="px-2 py-1 text-left border-b">SKU</th>
            <th class="px-2 py-1 text-left border-b">Produto</th>
            <th class="px-2 py-1 text-right border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-amber-50/60 dark:bg-amber-900/10">Qtd</th>
            <th class="px-2 py-1 text-left border-b bg-amber-50/60 dark:bg-amber-900/10">Resp./Obs</th>
            <th class="px-2 py-1 text-right border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-amber-50/60 dark:bg-amber-900/10">Qtd</th>
            <th class="px-2 py-1 text-left border-b bg-amber-50/60 dark:bg-amber-900/10">Nº Pedidos</th>
            <th class="px-2 py-1 text-right border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-emerald-50/60 dark:bg-emerald-900/10">Atual</th>
            <th class="px-2 py-1 text-right border-b bg-emerald-50/60 dark:bg-emerald-900/10">Reserva</th>
            <th class="px-2 py-1 text-center border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-gray-100/60 dark:bg-gray-800/30">✓</th>
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
            class="border-t hover:bg-muted/20"
            :class="row.conferido ? 'bg-emerald-50/40 dark:bg-emerald-900/10' : ''"
          >
            <td class="px-2 py-1 font-mono text-[11px]">{{ row.sku }}</td>
            <td class="px-2 py-1 truncate max-w-[280px]" :title="row.nome">{{ row.nome }}</td>
            <td
              class="px-2 py-1 text-right border-l-[3px] border-gray-400 dark:border-gray-600 bg-amber-50/40 dark:bg-amber-900/5"
              :class="row.entrada_qty > 0 ? 'font-semibold text-amber-700 dark:text-amber-300' : 'text-muted-foreground/60'"
            >
              {{ row.entrada_qty || '—' }}
            </td>
            <td class="px-2 py-1 bg-amber-50/40 dark:bg-amber-900/5">
              <input
                v-if="row.entrada_movement_id"
                :value="row.entrada_obs"
                placeholder="responsável / obs"
                class="w-full h-6 border rounded px-1 bg-background text-[11px]"
                @blur="(e) => patchMovementObs(row.entrada_movement_id!, (e.target as HTMLInputElement).value, row)"
              />
              <span v-else class="text-muted-foreground/60">—</span>
            </td>
            <td
              class="px-2 py-1 text-right border-l-[3px] border-gray-400 dark:border-gray-600 bg-amber-50/40 dark:bg-amber-900/5"
              :class="row.saida_qty > 0 ? 'font-semibold text-amber-700 dark:text-amber-300' : 'text-muted-foreground/60'"
            >
              {{ row.saida_qty || '—' }}
            </td>
            <td class="px-2 py-1 truncate max-w-[200px] bg-amber-50/40 dark:bg-amber-900/5" :title="row.saida_origens">
              {{ row.saida_origens || '—' }}
            </td>
            <td
              class="px-2 py-1 text-right border-l-[3px] border-gray-400 dark:border-gray-600 bg-emerald-50/40 dark:bg-emerald-900/5 font-semibold"
              :class="row.saldo === 0 ? 'text-red-600' : 'text-emerald-700'"
            >
              {{ row.saldo }}
            </td>
            <td class="px-2 py-1 text-right bg-emerald-50/40 dark:bg-emerald-900/5 text-muted-foreground">
              {{ row.reserva || '—' }}
            </td>
            <td class="px-2 py-1 text-center border-l-[3px] border-gray-400 dark:border-gray-600 bg-gray-100/40 dark:bg-gray-800/20">
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
    <div v-if="tab === 'pedidos'" class="border rounded-md overflow-x-auto">
      <table class="w-full text-xs border-collapse">
        <thead>
          <tr class="bg-muted/30 text-[10px] uppercase tracking-wide">
            <th class="px-2 py-1 text-left border-b">Data</th>
            <th class="px-2 py-1 text-left border-b">Pedido Bling</th>
            <th class="px-2 py-1 text-left border-b">Marketplace</th>
            <th class="px-2 py-1 text-left border-b">Loja</th>
            <th class="px-2 py-1 text-left border-b">SKU</th>
            <th class="px-2 py-1 text-left border-b">Produto</th>
            <th class="px-2 py-1 text-right border-b">Qtd</th>
            <th class="px-2 py-1 text-center border-b">Status</th>
            <th class="px-2 py-1 text-center border-b bg-gray-100/40">Conf.</th>
            <th class="px-2 py-1 text-left border-b bg-emerald-50/40">Obs</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="pedidosFiltered.length === 0">
            <td colspan="10" class="py-6 text-center text-muted-foreground">
              Nenhum pedido para esse filtro.
            </td>
          </tr>
          <tr
            v-for="row in pedidosFiltered" :key="row.id"
            class="border-t hover:bg-muted/20"
            :class="row.conferido ? 'bg-emerald-50/40 dark:bg-emerald-900/10' : ''"
          >
            <td class="px-2 py-1 whitespace-nowrap">{{ row.data ? row.data.slice(0, 10) : '—' }}</td>
            <td class="px-2 py-1 font-mono text-[11px]">{{ row.pedido_bling || '—' }}</td>
            <td class="px-2 py-1 font-mono text-[11px]">{{ row.pedido_marketplace || '—' }}</td>
            <td class="px-2 py-1">{{ row.loja || '—' }}</td>
            <td class="px-2 py-1 font-mono text-[11px]">{{ row.sku || '—' }}</td>
            <td class="px-2 py-1 truncate max-w-[280px]" :title="row.produto || ''">{{ row.produto || '—' }}</td>
            <td class="px-2 py-1 text-right">{{ row.quantidade }}</td>
            <td class="px-2 py-1 text-center">
              <span
                class="inline-block px-1.5 py-0.5 rounded text-[10px] font-medium"
                :class="row.status === 'enviado'
                  ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300'
                  : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300'"
              >
                {{ row.status === 'enviado' ? 'Enviado' : 'Não enviado' }}
              </span>
            </td>
            <td class="px-2 py-1 text-center bg-gray-100/30">
              <input type="checkbox" :checked="row.conferido" class="cursor-pointer" @change="togglePedido(row)" />
            </td>
            <td class="px-2 py-1 bg-emerald-50/30">
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
    <div v-if="tab === 'envios'" class="border rounded-md overflow-x-auto">
      <table class="w-full text-xs border-collapse">
        <thead>
          <tr class="bg-muted/30 text-[10px] uppercase tracking-wide">
            <th class="px-2 py-1 text-left border-b">Data</th>
            <th class="px-2 py-1 text-right border-b">Envios</th>
            <th class="px-2 py-1 text-center border-b bg-gray-100/40">Conferido</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="envios.items.length === 0">
            <td colspan="3" class="py-6 text-center text-muted-foreground">
              Nenhum envio no período.
            </td>
          </tr>
          <tr
            v-for="row in envios.items" :key="row.data"
            class="border-t hover:bg-muted/20"
            :class="row.conferido ? 'bg-emerald-50/40 dark:bg-emerald-900/10' : ''"
          >
            <td class="px-2 py-1">{{ row.data }}</td>
            <td class="px-2 py-1 text-right font-semibold">{{ row.envios }}</td>
            <td class="px-2 py-1 text-center bg-gray-100/30">
              <input type="checkbox" :checked="row.conferido" class="cursor-pointer" @change="toggleEnvio(row)" />
            </td>
          </tr>
        </tbody>
        <tfoot v-if="envios.items.length > 0" class="bg-muted/30 font-semibold">
          <tr>
            <td class="px-2 py-1 text-right">Total</td>
            <td class="px-2 py-1 text-right">{{ envios.total }}</td>
            <td class="px-2 py-1 text-center">
              {{ envios.total_conferido }} conferidos
            </td>
          </tr>
        </tfoot>
      </table>
    </div>
  </div>
</template>
