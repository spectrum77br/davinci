<script setup lang="ts">
import {
  AlertCircle,
  ChevronLeft,
  ChevronRight,
  Loader2,
  Plus,
  RotateCcw,
  Search,
  X,
} from 'lucide-vue-next'

definePageMeta({ middleware: ['permission'], permission: { resource: 'reembolso', action: 'view' } })

type RefundTipo = 'Logistica' | 'Cliente' | 'Manutenção' | 'Extraviado'
const TIPO_OPTIONS: RefundTipo[] = ['Logistica', 'Cliente', 'Manutenção', 'Extraviado']

type RefundRow = {
  id: string
  data: string | null
  pedido_bling: string | null
  pedido_marketplace: string | null
  plataforma: string | null
  conta: string
  tipo: RefundTipo | null
  prejuizo: number | null
  reembolso: number | null
  chamado: string | null
  operacao: string | null
  conferido: boolean
  observacao: string | null
  created_at: string
  updated_at: string
}

type RefundPage = {
  items: RefundRow[]
  total: number
  limit: number
  offset: number
  platforms: string[]
}

type LookupRow = {
  data: string | null
  pedido_bling: string | null
  pedido_marketplace: string | null
  plataforma: string | null
  conta: string
  custo_produto: number | null
  custo_manutencao: number | null
}

type LookupPage = {
  items: LookupRow[]
  historico_disponivel: boolean
}

type RefundDraft = LookupRow & {
  tipo: RefundTipo | ''
  prejuizo: number | null
  reembolso: number | null
  chamado: string
  operacao: string
  observacao: string
}

type ConferidoFilter = 'all' | 'true' | 'false'

const PAGE_SIZE = 100

const { api } = useApi()
const canEdit = useCan('reembolso', 'edit')

const items = ref<RefundRow[]>([])
const total = ref(0)
const platforms = ref<string[]>([])
const page = ref(1)
const loading = ref(false)
const error = ref<string | null>(null)

const search = ref('')
const platform = ref<'all' | string>('all')
const tipoFilter = ref<'all' | RefundTipo>('all')
const conferidoFilter = ref<ConferidoFilter>('false')

const addOpen = ref(false)
const lookupPedido = ref('')
const lookupLoading = ref(false)
const lookupResults = ref<LookupRow[]>([])
const lookupError = ref<string | null>(null)
const historicoDisponivel = ref(false)
const historicoLoading = ref(false)
const historicoElapsedMs = ref(0)
const creating = ref(false)
const draft = ref<RefundDraft | null>(null)

const rowSaveQueue = new Map<string, Promise<void>>()

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))
const rangeStart = computed(() => total.value === 0 ? 0 : (page.value - 1) * PAGE_SIZE + 1)
const rangeEnd = computed(() => Math.min(page.value * PAGE_SIZE, total.value))
const totalPrejuizo = computed(() => items.value.reduce((acc, row) => acc + (row.prejuizo ?? 0), 0))
const totalReembolso = computed(() => items.value.reduce((acc, row) => acc + (row.reembolso ?? 0), 0))
const totalAConferir = computed(() => items.value.filter((row) => !row.conferido).length)

const sheetInputClass = 'h-7 w-full rounded-none border-0 bg-transparent px-1 text-xs focus:bg-background focus:outline-none focus:ring-1 focus:ring-primary disabled:cursor-default disabled:opacity-70'
const sheetSelectClass = `${sheetInputClass} cursor-pointer`
const sheetMoneyInputClass = `${sheetInputClass} text-right tabular-nums [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:m-0 [&::-webkit-inner-spin-button]:appearance-none`

function apiError(e: any) {
  const detail = e?.data?.detail
  if (detail && typeof detail === 'object') return detail.message || detail.code || e?.message || 'erro'
  return detail || e?.message || 'erro'
}

function brl(v: number | null | undefined) {
  if (v == null) return '—'
  return v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
}

function fmtDateTime(v: string | null) {
  if (!v) return '—'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return v
  return d.toLocaleString('pt-BR', {
    day: '2-digit',
    month: '2-digit',
    year: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function numberOrNull(value: string) {
  if (value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function clampReembolsoForCliente(row: RefundRow) {
  if (row.tipo === 'Cliente' && row.reembolso != null && row.reembolso > 0) {
    row.reembolso = -row.reembolso
  }
}

function setRowReembolso(row: RefundRow, value: string) {
  const parsed = numberOrNull(value)
  if (parsed != null && row.tipo === 'Cliente' && parsed > 0) {
    row.reembolso = -parsed
  } else {
    row.reembolso = parsed
  }
}

function setRowText(row: RefundRow, field: 'chamado' | 'operacao' | 'observacao', value: string) {
  row[field] = value || null
}

async function fetchOrderCost(pedido_bling: string | null, conta: string | null): Promise<number | null> {
  if (!pedido_bling || !conta) return null
  try {
    const params = new URLSearchParams({ pedido_bling, conta })
    const res = await api<{ custo_produto: number | null }>(`/api/refunds/order-cost?${params.toString()}`)
    return res.custo_produto ?? null
  } catch (e: any) {
    error.value = apiError(e)
    return null
  }
}

async function setRowTipo(row: RefundRow, value: string) {
  const next = (value || null) as RefundTipo | null
  row.tipo = next
  if (next === 'Cliente') clampReembolsoForCliente(row)
  if (next === 'Extraviado') {
    const cost = await fetchOrderCost(row.pedido_bling, row.conta)
    if (cost != null && row.tipo === 'Extraviado') {
      row.prejuizo = cost
    }
  }
  await saveRow(row)
}

function onDraftTipoChange() {
  if (!draft.value) return
  if (draft.value.tipo === 'Extraviado' && draft.value.custo_produto != null) {
    draft.value.prejuizo = draft.value.custo_produto
  }
  if (draft.value.tipo === 'Manutenção' && draft.value.custo_manutencao != null) {
    draft.value.prejuizo = draft.value.custo_manutencao
  }
  if (draft.value.tipo === 'Cliente' && draft.value.reembolso != null && draft.value.reembolso > 0) {
    draft.value.reembolso = -draft.value.reembolso
  }
}

function setDraftReembolso(value: string) {
  if (!draft.value) return
  const parsed = numberOrNull(value)
  if (parsed != null && draft.value.tipo === 'Cliente' && parsed > 0) {
    draft.value.reembolso = -parsed
  } else {
    draft.value.reembolso = parsed
  }
}

async function setRowConferido(row: RefundRow, value: boolean) {
  row.conferido = value
  await saveRow(row)
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const params = new URLSearchParams()
    params.set('limit', String(PAGE_SIZE))
    params.set('offset', String((page.value - 1) * PAGE_SIZE))
    if (search.value.trim()) params.set('search', search.value.trim())
    if (platform.value !== 'all') params.set('platform', platform.value)
    if (tipoFilter.value !== 'all') params.set('tipo', tipoFilter.value)
    if (conferidoFilter.value !== 'all') params.set('conferido', conferidoFilter.value)
    const res = await api<RefundPage>(`/api/refunds?${params.toString()}`)
    items.value = res.items
    total.value = res.total
    platforms.value = res.platforms
  } catch (e: any) {
    error.value = apiError(e)
  } finally {
    loading.value = false
  }
}

await load()

let searchTimer: ReturnType<typeof setTimeout> | null = null
watch(search, () => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    page.value = 1
    load()
  }, 300)
})
watch([platform, tipoFilter, conferidoFilter], () => {
  page.value = 1
  load()
})
watch(page, () => load())

function openAdd() {
  addOpen.value = true
  lookupPedido.value = ''
  lookupResults.value = []
  lookupError.value = null
  historicoDisponivel.value = false
  historicoLoading.value = false
  historicoElapsedMs.value = 0
  draft.value = null
}

function closeAdd() {
  addOpen.value = false
  lookupPedido.value = ''
  lookupResults.value = []
  lookupError.value = null
  historicoDisponivel.value = false
  historicoLoading.value = false
  historicoElapsedMs.value = 0
  draft.value = null
}

function selectLookup(row: LookupRow) {
  draft.value = {
    ...row,
    tipo: '',
    prejuizo: null,
    reembolso: null,
    chamado: '',
    operacao: '',
    observacao: '',
  }
}

function fmtElapsed(ms: number) {
  const s = Math.floor(ms / 1000)
  const m = Math.floor(s / 60)
  const rem = s % 60
  return m > 0 ? `${m}m ${String(rem).padStart(2, '0')}s` : `${s}s`
}

async function lookupOrder(forceRefresh = false) {
  const pedido = lookupPedido.value.trim()
  if (!pedido) return
  lookupError.value = null
  lookupResults.value = []
  historicoDisponivel.value = false
  draft.value = null
  let timerHandle: ReturnType<typeof setInterval> | null = null
  if (forceRefresh) {
    historicoLoading.value = true
    historicoElapsedMs.value = 0
    const started = Date.now()
    timerHandle = setInterval(() => {
      historicoElapsedMs.value = Date.now() - started
    }, 250)
  } else {
    lookupLoading.value = true
  }
  try {
    const params = new URLSearchParams({ pedido })
    if (forceRefresh) params.set('force_refresh', 'true')
    const res = await api<LookupPage>(`/api/refunds/order-lookup?${params.toString()}`)
    lookupResults.value = res.items
    historicoDisponivel.value = !!res.historico_disponivel
    if (res.items.length === 1) selectLookup(res.items[0])
    if (!res.items.length && !historicoDisponivel.value) {
      lookupError.value = forceRefresh ? 'pedido não encontrado no histórico' : 'pedido não encontrado'
    }
  } catch (e: any) {
    lookupError.value = apiError(e)
  } finally {
    if (timerHandle) clearInterval(timerHandle)
    lookupLoading.value = false
    historicoLoading.value = false
  }
}

function lookupHistorico() {
  lookupOrder(true)
}

function draftPayload() {
  if (!draft.value) return null
  return {
    data: draft.value.data,
    pedido_bling: draft.value.pedido_bling,
    pedido_marketplace: draft.value.pedido_marketplace,
    plataforma: draft.value.plataforma,
    conta: draft.value.conta,
    tipo: draft.value.tipo || null,
    prejuizo: draft.value.prejuizo,
    reembolso: draft.value.reembolso,
    chamado: draft.value.chamado || null,
    operacao: draft.value.operacao || null,
    observacao: draft.value.observacao || null,
  }
}

async function createRefund() {
  const body = draftPayload()
  if (!body || !canEdit.value) return
  if (!body.tipo) {
    lookupError.value = 'Selecione o tipo antes de adicionar o pedido.'
    return
  }
  creating.value = true
  lookupError.value = null
  try {
    const created = await api<RefundRow>('/api/refunds', { method: 'POST', body })
    if (page.value === 1) items.value = [created, ...items.value].slice(0, PAGE_SIZE)
    total.value += 1
    closeAdd()
  } catch (e: any) {
    lookupError.value = apiError(e)
  } finally {
    creating.value = false
  }
}

function rowPatchPayload(row: RefundRow) {
  return {
    tipo: row.tipo || null,
    prejuizo: row.prejuizo,
    reembolso: row.reembolso,
    chamado: row.chamado || null,
    operacao: row.operacao || null,
    conferido: row.conferido,
    observacao: row.observacao || null,
  }
}

async function saveRow(row: RefundRow): Promise<void> {
  if (!canEdit.value) return
  const id = row.id
  const prev = rowSaveQueue.get(id) ?? Promise.resolve()
  const next = prev
    .catch(() => undefined)
    .then(async () => {
      try {
        await api(`/api/refunds/${encodeURIComponent(id)}`, {
          method: 'PATCH',
          body: rowPatchPayload(row),
        })
        error.value = null
      } catch (e: any) {
        error.value = apiError(e)
      }
    })
    .finally(() => {
      if (rowSaveQueue.get(id) === next) rowSaveQueue.delete(id)
    })
  rowSaveQueue.set(id, next)
  await next
}
</script>

<template>
  <div class="space-y-5">
    <PageHeader title="Reembolso" description="Controle de reembolsos por pedido da conciliação marketplace.">
      <template #actions>
        <Button size="sm" variant="outline" :disabled="loading" @click="load">
          <RotateCcw class="size-4 mr-1.5" :class="{ 'animate-spin': loading }" />
          atualizar
        </Button>
        <Button size="sm" :disabled="!canEdit" @click="openAdd">
          <Plus class="size-4 mr-1.5" />
          adicionar pedido
        </Button>
      </template>
    </PageHeader>

    <div v-if="error" class="flex items-center gap-2 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-400">
      <AlertCircle class="size-4" />
      {{ error }}
    </div>

    <div v-if="addOpen" class="rounded-md border bg-background">
      <div class="flex flex-wrap items-end gap-3 border-b px-3 py-3">
        <label class="space-y-1">
          <span class="text-[11px] font-medium text-muted-foreground">Pedido</span>
          <div class="relative">
            <Search class="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
            <input
              v-model="lookupPedido"
              class="h-9 w-72 rounded-md border bg-background pl-8 pr-3 text-sm"
              placeholder="Bling ou marketplace"
              :disabled="historicoLoading"
              @keydown.enter.prevent="lookupOrder(false)"
            />
          </div>
        </label>
        <Button size="sm" :disabled="lookupLoading || historicoLoading || !lookupPedido.trim()" @click="lookupOrder(false)">
          <Loader2 v-if="lookupLoading" class="size-4 mr-1.5 animate-spin" />
          <Search v-else class="size-4 mr-1.5" />
          buscar
        </Button>
        <Button size="sm" variant="ghost" :disabled="historicoLoading" @click="closeAdd">
          <X class="size-4 mr-1.5" />
          fechar
        </Button>
        <span v-if="lookupError" class="text-sm text-red-400">{{ lookupError }}</span>
      </div>

      <div
        v-if="historicoLoading"
        class="border-b border-amber-500/30 bg-amber-500/5 px-4 py-4"
      >
        <div class="flex items-start gap-3">
          <Loader2 class="mt-0.5 size-5 shrink-0 animate-spin text-amber-500" />
          <div class="flex-1">
            <div class="text-sm font-medium text-amber-700 dark:text-amber-300">
              Buscando pedido <span class="font-mono">{{ lookupPedido.trim() }}</span> no historico…
            </div>
            <p class="mt-1 text-xs text-muted-foreground">
              A view de conciliacao precisa materializar todos os pedidos antes
              de filtrar, entao essa busca pode levar varios minutos. Pode deixar
              essa aba aberta; o resultado aparece assim que terminar.
            </p>
            <div class="mt-2 text-xs font-mono tabular-nums text-amber-700 dark:text-amber-300">
              {{ fmtElapsed(historicoElapsedMs) }} decorrido
            </div>
          </div>
        </div>
      </div>

      <div
        v-else-if="historicoDisponivel && !lookupResults.length && !draft"
        class="border-b border-blue-500/30 bg-blue-500/5 px-4 py-4"
      >
        <div class="flex items-start gap-3">
          <AlertCircle class="mt-0.5 size-5 shrink-0 text-blue-500" />
          <div class="flex-1">
            <div class="text-sm font-medium">
              Pedido <span class="font-mono">{{ lookupPedido.trim() }}</span> nao esta no historico recente
            </div>
            <p class="mt-1 text-xs text-muted-foreground">
              Ele existe no Bling, mas esta fora da janela de 20 dias da
              conciliacao rapida. A busca no historico le a view completa e
              pode levar varios minutos.
            </p>
            <Button size="sm" class="mt-3" @click="lookupHistorico">
              <Search class="size-4 mr-1.5" />
              buscar no historico
            </Button>
          </div>
        </div>
      </div>

      <div v-if="lookupResults.length > 1 && !draft" class="overflow-auto border-b">
        <table class="w-full text-xs border-collapse">
          <thead class="bg-background">
            <tr>
              <th class="px-2 py-1 text-left text-[11px] font-semibold border-b" colspan="5">Identificação</th>
              <th class="px-2 py-1 text-right text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600" colspan="1">Ação</th>
            </tr>
            <tr>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px]">Data</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px]">Pedido Bling</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[155px]">Pedido Marketplace</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[105px]">Plataforma</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[155px]">Conta</th>
              <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[90px] border-l-[3px] border-gray-400 dark:border-gray-600"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in lookupResults" :key="`${row.pedido_bling}-${row.pedido_marketplace}-${row.conta}`" class="border-t hover:brightness-95 dark:hover:brightness-110">
              <td class="px-2 py-1 whitespace-nowrap text-muted-foreground">{{ fmtDateTime(row.data) }}</td>
              <td class="px-2 py-1 font-mono">{{ row.pedido_bling || '—' }}</td>
              <td class="px-2 py-1 font-mono text-muted-foreground">{{ row.pedido_marketplace || '—' }}</td>
              <td class="px-2 py-1 uppercase">{{ row.plataforma || '—' }}</td>
              <td class="px-2 py-1">{{ row.conta }}</td>
              <td class="px-2 py-1 text-right border-l-[3px] border-gray-400 dark:border-gray-600">
                <Button size="sm" variant="outline" @click="selectLookup(row)">usar</Button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="draft" class="overflow-auto">
        <table class="min-w-[1380px] w-full text-xs border-collapse">
          <thead class="bg-background">
            <tr>
              <th class="px-2 py-1 text-left text-[11px] font-semibold border-b" colspan="5">Identificação</th>
              <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-amber-50 dark:bg-amber-900/20" colspan="5">Reembolso</th>
              <th class="px-2 py-1 text-left text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-emerald-50 dark:bg-emerald-900/20" colspan="1">Observação</th>
              <th class="px-2 py-1 text-right text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600" colspan="1">Ação</th>
            </tr>
            <tr>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px]">Data</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px]">Pedido Bling</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[155px]">Pedido Marketplace</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[105px]">Plataforma</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[155px]">Conta</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[145px] bg-amber-50 dark:bg-amber-900/20 border-l-[3px] border-gray-400 dark:border-gray-600">Tipo</th>
              <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px] bg-amber-50 dark:bg-amber-900/20">Prejuízo</th>
              <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px] bg-amber-50 dark:bg-amber-900/20">Reembolso</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[145px] bg-amber-50 dark:bg-amber-900/20">Chamado</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[155px] bg-amber-50 dark:bg-amber-900/20">Operação</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[240px] bg-emerald-50 dark:bg-emerald-900/20 border-l-[3px] border-gray-400 dark:border-gray-600">Observação</th>
              <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px] border-l-[3px] border-gray-400 dark:border-gray-600"></th>
            </tr>
          </thead>
          <tbody>
            <tr class="border-t">
              <td class="px-2 py-1 whitespace-nowrap text-muted-foreground">{{ fmtDateTime(draft.data) }}</td>
              <td class="px-2 py-1 font-mono">{{ draft.pedido_bling || '—' }}</td>
              <td class="px-2 py-1 font-mono text-muted-foreground">{{ draft.pedido_marketplace || '—' }}</td>
              <td class="px-2 py-1 uppercase">{{ draft.plataforma || '—' }}</td>
              <td class="px-2 py-1">{{ draft.conta }}</td>
              <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10 border-l-[3px] border-gray-400 dark:border-gray-600">
                <select v-model="draft.tipo" :class="[sheetSelectClass, !draft.tipo ? 'ring-1 ring-red-500/60' : '']" @change="onDraftTipoChange">
                  <option value="">— obrigatório</option>
                  <option v-for="tipo in TIPO_OPTIONS" :key="tipo" :value="tipo">{{ tipo }}</option>
                </select>
              </td>
              <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
                <input v-model.number="draft.prejuizo" type="number" step="0.01" :class="sheetMoneyInputClass" />
              </td>
              <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
                <input
                  :value="draft.reembolso ?? ''"
                  type="number"
                  step="0.01"
                  :max="draft.tipo === 'Cliente' ? 0 : undefined"
                  :class="sheetMoneyInputClass"
                  @input="(e) => setDraftReembolso((e.target as HTMLInputElement).value)"
                />
              </td>
              <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
                <input v-model="draft.chamado" :class="sheetInputClass" />
              </td>
              <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
                <input v-model="draft.operacao" :class="sheetInputClass" />
              </td>
              <td class="px-1 py-0.5 bg-emerald-50/40 dark:bg-emerald-900/10 border-l-[3px] border-gray-400 dark:border-gray-600">
                <input v-model="draft.observacao" :class="sheetInputClass" />
              </td>
              <td class="px-2 py-1 text-right border-l-[3px] border-gray-400 dark:border-gray-600">
                <Button size="sm" :disabled="creating || !canEdit || !draft.tipo" @click="createRefund">
                  <Loader2 v-if="creating" class="size-4 mr-1.5 animate-spin" />
                  <Plus v-else class="size-4 mr-1.5" />
                  adicionar
                </Button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="flex flex-wrap items-center gap-2">
      <div class="relative">
        <Search class="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
        <input
          v-model="search"
          class="h-9 w-72 rounded-md border bg-background pl-8 pr-3 text-sm"
          placeholder="buscar pedido, conta, chamado…"
        />
      </div>
      <select v-model="platform" class="h-9 rounded-md border bg-background px-2 text-sm">
        <option value="all">todas plataformas</option>
        <option v-for="p in platforms" :key="p" :value="p">{{ p }}</option>
      </select>
      <select v-model="tipoFilter" class="h-9 rounded-md border bg-background px-2 text-sm">
        <option value="all">todos tipos</option>
        <option v-for="tipo in TIPO_OPTIONS" :key="tipo" :value="tipo">{{ tipo }}</option>
      </select>
      <select v-model="conferidoFilter" class="h-9 rounded-md border bg-background px-2 text-sm">
        <option value="false">a conferir</option>
        <option value="true">conferidos</option>
        <option value="all">todos</option>
      </select>
      <span class="ml-auto text-xs text-muted-foreground">
        {{ rangeStart }}–{{ rangeEnd }} de {{ total }} · a conferir {{ totalAConferir }} · prejuízo {{ brl(totalPrejuizo) }} · reembolso {{ brl(totalReembolso) }}
      </span>
    </div>

    <div class="overflow-auto rounded border max-h-[75vh] focus:outline-none" tabindex="0">
      <table class="min-w-[1440px] text-xs border-collapse">
        <thead class="sticky top-0 z-20 bg-background">
          <tr>
            <th class="px-2 py-1 text-left text-[11px] font-semibold border-b" colspan="5">Identificação</th>
            <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-amber-50 dark:bg-amber-900/20" colspan="5">Reembolso</th>
            <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-emerald-50 dark:bg-emerald-900/20" colspan="2">Conferência</th>
          </tr>
          <tr class="border-b">
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[115px]">Data</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px]">Pedido Bling</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[155px]">Pedido Marketplace</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[105px]">Plataforma</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[155px]">Conta</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[145px] bg-amber-50 dark:bg-amber-900/20 border-l-[3px] border-gray-400 dark:border-gray-600">Tipo</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px] bg-amber-50 dark:bg-amber-900/20">Prejuízo</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px] bg-amber-50 dark:bg-amber-900/20">Reembolso</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[150px] bg-amber-50 dark:bg-amber-900/20">Chamado</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[155px] bg-amber-50 dark:bg-amber-900/20">Operação</th>
            <th class="px-2 py-1 text-center font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[90px] bg-emerald-50 dark:bg-emerald-900/20 border-l-[3px] border-gray-400 dark:border-gray-600">Conferido</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[260px] bg-emerald-50 dark:bg-emerald-900/20">Observação</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading && !items.length">
            <td colspan="12" class="py-8 text-center text-muted-foreground">
              <Loader2 class="size-4 inline animate-spin mr-1.5" />
              carregando…
            </td>
          </tr>
          <tr v-else-if="!items.length">
            <td colspan="12" class="py-8 text-center text-muted-foreground">sem registros</td>
          </tr>
          <tr v-for="row in items" :key="row.id" class="border-t hover:brightness-95 dark:hover:brightness-110">
            <td class="px-2 py-1 whitespace-nowrap text-muted-foreground">{{ fmtDateTime(row.data) }}</td>
            <td class="px-2 py-1 font-mono whitespace-nowrap">{{ row.pedido_bling || '—' }}</td>
            <td class="px-2 py-1 font-mono text-muted-foreground whitespace-nowrap">{{ row.pedido_marketplace || '—' }}</td>
            <td class="px-2 py-1 uppercase whitespace-nowrap">{{ row.plataforma || '—' }}</td>
            <td class="px-2 py-1 whitespace-nowrap">{{ row.conta }}</td>
            <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10 border-l-[3px] border-gray-400 dark:border-gray-600">
              <select
                :value="row.tipo || ''"
                :disabled="!canEdit"
                :class="sheetSelectClass"
                @change="(e) => setRowTipo(row, (e.target as HTMLSelectElement).value)"
              >
                <option value="">—</option>
                <option v-for="tipo in TIPO_OPTIONS" :key="tipo" :value="tipo">{{ tipo }}</option>
              </select>
            </td>
            <td class="px-2 py-1 text-right tabular-nums bg-amber-50/40 dark:bg-amber-900/10 text-muted-foreground">
              {{ row.prejuizo ?? '—' }}
            </td>
            <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
              <input
                :value="row.reembolso ?? ''"
                :disabled="!canEdit"
                type="number"
                step="0.01"
                :max="row.tipo === 'Cliente' ? 0 : undefined"
                :class="sheetMoneyInputClass"
                @input="(e) => setRowReembolso(row, (e.target as HTMLInputElement).value)"
                @change="saveRow(row)"
              />
            </td>
            <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
              <input
                :value="row.chamado || ''"
                :disabled="!canEdit"
                :class="sheetInputClass"
                @input="(e) => setRowText(row, 'chamado', (e.target as HTMLInputElement).value)"
                @change="saveRow(row)"
              />
            </td>
            <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
              <input
                :value="row.operacao || ''"
                :disabled="!canEdit"
                :class="sheetInputClass"
                @input="(e) => setRowText(row, 'operacao', (e.target as HTMLInputElement).value)"
                @change="saveRow(row)"
              />
            </td>
            <td class="px-2 py-1 text-center bg-emerald-50/40 dark:bg-emerald-900/10 border-l-[3px] border-gray-400 dark:border-gray-600">
              <input
                :checked="row.conferido"
                :disabled="!canEdit"
                type="checkbox"
                class="size-4 rounded border accent-primary disabled:cursor-default disabled:opacity-70"
                @change="(e) => setRowConferido(row, (e.target as HTMLInputElement).checked)"
              />
            </td>
            <td class="px-1 py-0.5 bg-emerald-50/40 dark:bg-emerald-900/10">
              <input
                :value="row.observacao || ''"
                :disabled="!canEdit"
                :class="sheetInputClass"
                @input="(e) => setRowText(row, 'observacao', (e.target as HTMLInputElement).value)"
                @change="saveRow(row)"
              />
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="total > PAGE_SIZE" class="flex items-center justify-between gap-2">
      <span class="text-xs text-muted-foreground">
        página {{ page }} de {{ totalPages }} · {{ PAGE_SIZE }}/página
      </span>
      <div class="flex items-center gap-1">
        <Button size="sm" variant="outline" :disabled="page <= 1 || loading" @click="page = 1">«</Button>
        <Button size="sm" variant="outline" :disabled="page <= 1 || loading" @click="page = page - 1">
          <ChevronLeft class="size-4" />
        </Button>
        <input
          v-model.number="page"
          type="number"
          :min="1"
          :max="totalPages"
          class="w-16 rounded-md border bg-background px-2 py-1 text-center text-sm"
          @change="page = Math.min(Math.max(1, page), totalPages)"
        />
        <Button size="sm" variant="outline" :disabled="page >= totalPages || loading" @click="page = page + 1">
          <ChevronRight class="size-4" />
        </Button>
        <Button size="sm" variant="outline" :disabled="page >= totalPages || loading" @click="page = totalPages">»</Button>
      </div>
    </div>
  </div>
</template>
