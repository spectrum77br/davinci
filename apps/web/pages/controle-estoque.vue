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
import { Boxes, Truck, ClipboardList, Loader2, RefreshCw } from 'lucide-vue-next'

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

// Single-day filter for Estoque + Pedidos. Envios uses a 7-day window
// that auto-resets on first activation (see watch below) — operators
// still want per-day counts but with enough rows on screen to compare.
const dia = ref(isoToday())
const enviosInicio = ref(isoDaysAgo(6))
const enviosFim = ref(isoToday())

// Admin-only tag override.
const isAdmin = computed(() => auth.user?.role === 'admin')
const tagOverride = ref<string>('')

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

const loading = ref(false)
const errorText = ref<string | null>(null)

// ── Fetchers ──────────────────────────────────────────────────────────
function singleDayDates(): string {
  // Estoque + Pedidos send the same value for both endpoints — backend
  // tolerates either treating the window as a single point or a range.
  const parts = [`data_inicio=${dia.value}`, `data_fim=${dia.value}`]
  if (isAdmin.value && tagOverride.value) parts.push(`tag=${tagOverride.value}`)
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
})
watch([dia, tagOverride, statusFilter], () => {
  if (tab.value !== 'envios') void loadCurrentTab()
})
watch([enviosInicio, enviosFim, conferidoFilter], () => {
  if (tab.value === 'envios') void loadCurrentTab()
})
watch(tagOverride, () => {
  if (tab.value === 'envios') void loadCurrentTab()
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
    await toggleCheck('estoque', row.sku, dia.value, next)
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
      <div class="flex gap-1 rounded-md bg-muted/40 p-1 w-fit">
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
            <td class="bg-amber-50/40 dark:bg-amber-900/5 align-top">
              <div v-if="row.entradas.length === 0" class="text-muted-foreground/60">—</div>
              <div v-else class="space-y-0.5">
                <input
                  v-for="(e, idx) in row.entradas" :key="e.movement_id"
                  :value="e.obs"
                  placeholder="—"
                  class="block w-full h-5 border rounded px-1 bg-background text-[11px] leading-5 placeholder:text-muted-foreground/60"
                  @blur="(ev) => patchMovementObs(e.movement_id, (ev.target as HTMLInputElement).value, row, idx)"
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
            <th class="text-center bg-gray-100/40">Conf.</th>
            <th class="text-left bg-emerald-50/40">Obs</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="pedidosFiltered.length === 0">
            <td colspan="10" class="py-6 text-center text-muted-foreground">
              Nenhum pedido para esse dia.
            </td>
          </tr>
          <tr
            v-for="row in pedidosFiltered" :key="row.id"
            class="hover:bg-muted/20"
            :class="row.conferido ? 'bg-emerald-50/40 dark:bg-emerald-900/10' : ''"
          >
            <td class="whitespace-nowrap">{{ row.data ? row.data.slice(0, 10) : '—' }}</td>
            <td class="font-mono text-[11px]">{{ row.pedido_bling || '—' }}</td>
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
            <td class="text-center bg-gray-100/30">
              <input type="checkbox" :checked="row.conferido" class="cursor-pointer" @change="togglePedido(row)" />
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
    <div v-if="tab === 'envios'" class="border rounded-md overflow-x-auto">
      <table class="grid-table w-full text-xs border-collapse">
        <thead>
          <tr class="bg-muted/30 text-[10px] uppercase tracking-wide">
            <th class="text-left">Data</th>
            <th class="text-right">Envios</th>
            <th class="text-center bg-gray-100/40">Conferido</th>
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
            class="hover:bg-muted/20"
            :class="row.conferido ? 'bg-emerald-50/40 dark:bg-emerald-900/10' : ''"
          >
            <td>{{ row.data }}</td>
            <td class="text-right font-semibold">{{ row.envios }}</td>
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
            <td class="text-center text-muted-foreground text-[10px]">
              geral: {{ envios.total_envios }}
            </td>
          </tr>
        </tfoot>
      </table>
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
</style>
