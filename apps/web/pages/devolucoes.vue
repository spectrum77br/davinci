<script setup lang="ts">
import {
  AlertCircle,
  ChevronLeft,
  ChevronRight,
  Clock,
  ExternalLink,
  Loader2,
  Package,
  Plus,
  RotateCcw,
  Save,
  Search,
  Trash2,
  Undo2,
  X,
} from 'lucide-vue-next'

definePageMeta({ middleware: ['permission'], permission: { resource: 'devolucoes', action: 'view' } })

type DevolutionRow = {
  id: string
  data: string | null
  pedido_bling: string | null
  pedido_marketplace: string | null
  conta: string
  sku: string | null
  produtos: string | null
  custo_produto: number | null
  condicao_produto: string | null
  link_abertura: string | null
  reembolso: boolean
  motivo_devolucao: string | null
  custo_manutencao: number | null
  tecnico: string | null
  devolver_estoque: string | null
  observacao: string | null
  created_at: string
  updated_at: string
}

type DevolutionPage = {
  items: DevolutionRow[]
  total: number
  limit: number
  offset: number
}

type LookupRow = {
  data: string | null
  pedido_bling: string | null
  pedido_marketplace: string | null
  conta: string
  sku: string | null
  produtos: string | null
  custo_produto: number | null
}

type DevolutionDraft = LookupRow & {
  condicao_produto: string
  link_abertura: string
  reembolso: boolean
  motivo_devolucao: string
  custo_manutencao: number | null
  tecnico: string
  devolver_estoque: string
  observacao: string
}

type ReembolsoFilter = 'all' | 'true' | 'false'

const PAGE_SIZE = 100

const MOTIVOS_DEVOLUCAO = [
  'Mudou de ideia',
  'Golpe',
  'Tamanho',
  'Item faltando',
  'Dano funcional / Não funciona',
  'Item Incorreto',
  'Não recebido',
  'Danificado (Outros)',
  'Pacote Suspeito',
  'Embalagem Externa Danificada',
] as const

const CONDICOES_PRODUTO = [
  'Novo',
  'Usado',
  'Manutenção',
  'Extraviado',
  'Trocado',
] as const

const { api } = useApi()
const canEdit = useCan('devolucoes', 'edit')
const canDelete = useCan('devolucoes', 'delete')

const items = ref<DevolutionRow[]>([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const error = ref<string | null>(null)

const search = ref('')
const reembolsoFilter = ref<ReembolsoFilter>('all')

const addOpen = ref(false)
const lookupPedido = ref('')
const lookupLoading = ref(false)
const lookupResults = ref<LookupRow[]>([])
const lookupError = ref<string | null>(null)
const creating = ref(false)
const draft = ref<DevolutionDraft | null>(null)

const dirtyRows = ref<Set<string>>(new Set())
const savingRows = ref<Set<string>>(new Set())
const deletingRows = ref<Set<string>>(new Set())

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))
const rangeStart = computed(() => total.value === 0 ? 0 : (page.value - 1) * PAGE_SIZE + 1)
const rangeEnd = computed(() => Math.min(page.value * PAGE_SIZE, total.value))
const totalCustoProduto = computed(() => items.value.reduce((a, r) => a + (r.custo_produto ?? 0), 0))
const totalCustoManutencao = computed(() => items.value.reduce((a, r) => a + (r.custo_manutencao ?? 0), 0))
const totalReembolsadas = computed(() => items.value.filter((r) => r.reembolso).length)

const sheetInputClass = 'h-7 w-full rounded-none border-0 bg-transparent px-1 text-xs focus:bg-background focus:outline-none focus:ring-1 focus:ring-primary disabled:cursor-default disabled:opacity-70'
const sheetSelectClass = `${sheetInputClass} cursor-pointer`
const sheetMoneyInputClass = `${sheetInputClass} text-right tabular-nums`

function apiError(e: any) {
  const detail = e?.data?.detail
  if (detail && typeof detail === 'object') return detail.message || detail.code || e?.message || 'erro'
  return detail || e?.message || 'erro'
}

function brl(v: number | null | undefined) {
  if (v == null) return '—'
  return v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
}

function normalizeUrl(v: string | null | undefined) {
  if (!v) return ''
  const trimmed = v.trim()
  if (!trimmed) return ''
  if (/^https?:\/\//i.test(trimmed)) return trimmed
  return `https://${trimmed}`
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

function markDirty(id: string) {
  const next = new Set(dirtyRows.value)
  next.add(id)
  dirtyRows.value = next
}
function clearDirty(id: string) {
  const next = new Set(dirtyRows.value)
  next.delete(id)
  dirtyRows.value = next
}
function hasDirty(id: string) { return dirtyRows.value.has(id) }
function setSaving(id: string, v: boolean) {
  const next = new Set(savingRows.value)
  if (v) next.add(id); else next.delete(id)
  savingRows.value = next
}
function isSaving(id: string) { return savingRows.value.has(id) }
function setDeleting(id: string, v: boolean) {
  const next = new Set(deletingRows.value)
  if (v) next.add(id); else next.delete(id)
  deletingRows.value = next
}
function isDeleting(id: string) { return deletingRows.value.has(id) }

function numberOrNull(value: string) {
  if (value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function setRowNumber(row: DevolutionRow, field: 'custo_produto' | 'custo_manutencao', value: string) {
  row[field] = numberOrNull(value)
  markDirty(row.id)
}

function setRowText(
  row: DevolutionRow,
  field: 'condicao_produto' | 'link_abertura' | 'motivo_devolucao' | 'tecnico' | 'devolver_estoque' | 'observacao',
  value: string,
) {
  row[field] = value || null
  markDirty(row.id)
}

function setRowReembolso(row: DevolutionRow, value: boolean) {
  row.reembolso = value
  markDirty(row.id)
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const params = new URLSearchParams()
    params.set('limit', String(PAGE_SIZE))
    params.set('offset', String((page.value - 1) * PAGE_SIZE))
    if (search.value.trim()) params.set('search', search.value.trim())
    if (reembolsoFilter.value !== 'all') params.set('reembolso', reembolsoFilter.value)
    const res = await api<DevolutionPage>(`/api/devolutions?${params.toString()}`)
    items.value = res.items
    total.value = res.total
    dirtyRows.value = new Set()
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
watch(reembolsoFilter, () => { page.value = 1; load() })
watch(page, () => load())

function openAdd() {
  addOpen.value = true
  lookupPedido.value = ''
  lookupResults.value = []
  lookupError.value = null
  draft.value = null
}
function closeAdd() {
  addOpen.value = false
  lookupPedido.value = ''
  lookupResults.value = []
  lookupError.value = null
  draft.value = null
}

function selectLookup(row: LookupRow) {
  draft.value = {
    ...row,
    condicao_produto: '',
    link_abertura: '',
    reembolso: false,
    motivo_devolucao: '',
    custo_manutencao: null,
    tecnico: '',
    devolver_estoque: '',
    observacao: '',
  }
}

async function lookupOrder() {
  const pedido = lookupPedido.value.trim()
  if (!pedido) return
  lookupLoading.value = true
  lookupError.value = null
  lookupResults.value = []
  draft.value = null
  try {
    const res = await api<LookupRow[]>(`/api/devolutions/order-lookup?pedido=${encodeURIComponent(pedido)}`)
    lookupResults.value = res
    if (res.length === 1) selectLookup(res[0])
    if (!res.length) lookupError.value = 'pedido não encontrado'
  } catch (e: any) {
    lookupError.value = apiError(e)
  } finally {
    lookupLoading.value = false
  }
}

function draftPayload() {
  if (!draft.value) return null
  return {
    data: draft.value.data,
    pedido_bling: draft.value.pedido_bling,
    pedido_marketplace: draft.value.pedido_marketplace,
    conta: draft.value.conta,
    sku: draft.value.sku,
    produtos: draft.value.produtos,
    custo_produto: draft.value.custo_produto,
    condicao_produto: draft.value.condicao_produto || null,
    link_abertura: draft.value.link_abertura || null,
    reembolso: draft.value.reembolso,
    motivo_devolucao: draft.value.motivo_devolucao || null,
    custo_manutencao: draft.value.custo_manutencao,
    tecnico: draft.value.tecnico || null,
    devolver_estoque: draft.value.devolver_estoque || null,
    observacao: draft.value.observacao || null,
  }
}

async function createDevolution() {
  const body = draftPayload()
  if (!body || !canEdit.value) return
  creating.value = true
  lookupError.value = null
  try {
    const created = await api<DevolutionRow>('/api/devolutions', { method: 'POST', body })
    if (page.value === 1) items.value = [created, ...items.value].slice(0, PAGE_SIZE)
    total.value += 1
    closeAdd()
  } catch (e: any) {
    lookupError.value = apiError(e)
  } finally {
    creating.value = false
  }
}

function rowPatchPayload(row: DevolutionRow) {
  return {
    custo_produto: row.custo_produto,
    condicao_produto: row.condicao_produto || null,
    link_abertura: row.link_abertura || null,
    reembolso: row.reembolso,
    motivo_devolucao: row.motivo_devolucao || null,
    custo_manutencao: row.custo_manutencao,
    tecnico: row.tecnico || null,
    devolver_estoque: row.devolver_estoque || null,
    observacao: row.observacao || null,
  }
}

async function saveRow(row: DevolutionRow) {
  if (!canEdit.value || !hasDirty(row.id) || isSaving(row.id)) return
  setSaving(row.id, true)
  error.value = null
  try {
    const updated = await api<DevolutionRow>(`/api/devolutions/${encodeURIComponent(row.id)}`, {
      method: 'PATCH',
      body: rowPatchPayload(row),
    })
    const idx = items.value.findIndex((i) => i.id === row.id)
    if (idx >= 0) items.value[idx] = updated
    clearDirty(row.id)
  } catch (e: any) {
    error.value = apiError(e)
  } finally {
    setSaving(row.id, false)
  }
}

async function deleteRow(row: DevolutionRow) {
  if (!canDelete.value || isDeleting(row.id)) return
  const ok = window.confirm(`Excluir devolução do pedido ${row.pedido_bling || row.pedido_marketplace || row.id}?`)
  if (!ok) return
  setDeleting(row.id, true)
  error.value = null
  try {
    await api(`/api/devolutions/${encodeURIComponent(row.id)}`, { method: 'DELETE' })
    items.value = items.value.filter((i) => i.id !== row.id)
    total.value = Math.max(0, total.value - 1)
  } catch (e: any) {
    error.value = apiError(e)
  } finally {
    setDeleting(row.id, false)
  }
}
</script>

<template>
  <div class="space-y-5">
    <PageHeader title="Devoluções" description="Controle manual de devoluções por pedido.">
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

    <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
      <StatCard label="Total devoluções" :value="total" :icon="Undo2" />
      <StatCard label="Reembolsadas" :value="totalReembolsadas" :icon="Clock" tone="warning" />
      <StatCard label="Custo produto (pág.)" :value="brl(totalCustoProduto)" :icon="Package" />
      <StatCard label="Custo manutenção (pág.)" :value="brl(totalCustoManutencao)" tone="danger" />
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
              @keydown.enter.prevent="lookupOrder"
            />
          </div>
        </label>
        <Button size="sm" :disabled="lookupLoading || !lookupPedido.trim()" @click="lookupOrder">
          <Loader2 v-if="lookupLoading" class="size-4 mr-1.5 animate-spin" />
          <Search v-else class="size-4 mr-1.5" />
          buscar
        </Button>
        <Button size="sm" variant="ghost" @click="closeAdd">
          <X class="size-4 mr-1.5" />
          fechar
        </Button>
        <span v-if="lookupError" class="text-sm text-red-400">{{ lookupError }}</span>
      </div>

      <div v-if="lookupResults.length > 1 && !draft" class="overflow-auto border-b">
        <table class="w-full text-xs border-collapse">
          <thead class="bg-background">
            <tr>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px]">Data</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px]">Pedido Bling</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[155px]">Pedido Marketplace</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[155px]">Conta</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px]">SKU</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[200px]">Produto</th>
              <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px]">Custo</th>
              <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[90px]"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in lookupResults" :key="`${row.pedido_bling}-${row.conta}`" class="border-t hover:brightness-95 dark:hover:brightness-110">
              <td class="px-2 py-1 whitespace-nowrap text-muted-foreground">{{ fmtDateTime(row.data) }}</td>
              <td class="px-2 py-1 font-mono">{{ row.pedido_bling || '—' }}</td>
              <td class="px-2 py-1 font-mono text-muted-foreground">{{ row.pedido_marketplace || '—' }}</td>
              <td class="px-2 py-1">{{ row.conta }}</td>
              <td class="px-2 py-1 font-mono text-xs">{{ row.sku || '—' }}</td>
              <td class="px-2 py-1 text-muted-foreground">{{ row.produtos || '—' }}</td>
              <td class="px-2 py-1 text-right tabular-nums">{{ brl(row.custo_produto) }}</td>
              <td class="px-2 py-1 text-right">
                <Button size="sm" variant="outline" @click="selectLookup(row)">usar</Button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="draft" class="overflow-auto">
        <table class="min-w-[1600px] w-full text-xs border-collapse">
          <thead class="bg-background">
            <tr>
              <th class="px-2 py-1 text-left text-[11px] font-semibold border-b" colspan="6">Identificação</th>
              <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-amber-50 dark:bg-amber-900/20" colspan="8">Devolução</th>
              <th class="px-2 py-1 text-right text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600" colspan="1">Ação</th>
            </tr>
            <tr>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px]">Data</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px]">Pedido Bling</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[155px]">Pedido Marketplace</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[150px]">Conta</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[130px]">SKU</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[200px]">Produtos</th>
              <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px] bg-amber-50 dark:bg-amber-900/20 border-l-[3px] border-gray-400 dark:border-gray-600">Custo produto</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[140px] bg-amber-50 dark:bg-amber-900/20">Condição</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[180px] bg-amber-50 dark:bg-amber-900/20">Link abertura</th>
              <th class="px-2 py-1 text-center font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[80px] bg-amber-50 dark:bg-amber-900/20">Reembolso</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[180px] bg-amber-50 dark:bg-amber-900/20">Motivo</th>
              <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px] bg-amber-50 dark:bg-amber-900/20">Custo manut.</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px] bg-amber-50 dark:bg-amber-900/20">Técnico</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px] bg-amber-50 dark:bg-amber-900/20">Dev. estoque</th>
              <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px] border-l-[3px] border-gray-400 dark:border-gray-600"></th>
            </tr>
          </thead>
          <tbody>
            <tr class="border-t">
              <td class="px-2 py-1 whitespace-nowrap text-muted-foreground">{{ fmtDateTime(draft.data) }}</td>
              <td class="px-2 py-1 font-mono">{{ draft.pedido_bling || '—' }}</td>
              <td class="px-2 py-1 font-mono text-muted-foreground">{{ draft.pedido_marketplace || '—' }}</td>
              <td class="px-2 py-1">{{ draft.conta }}</td>
              <td class="px-2 py-1 font-mono">{{ draft.sku || '—' }}</td>
              <td class="px-2 py-1 text-muted-foreground">{{ draft.produtos || '—' }}</td>
              <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10 border-l-[3px] border-gray-400 dark:border-gray-600">
                <input v-model.number="draft.custo_produto" type="number" step="0.01" :class="sheetMoneyInputClass" />
              </td>
              <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
                <select v-model="draft.condicao_produto" :class="sheetSelectClass">
                  <option value="">—</option>
                  <option v-for="c in CONDICOES_PRODUTO" :key="c" :value="c">{{ c }}</option>
                </select>
              </td>
              <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
                <input v-model="draft.link_abertura" :class="sheetInputClass" />
              </td>
              <td class="px-2 py-1 text-center bg-amber-50/40 dark:bg-amber-900/10">
                <input v-model="draft.reembolso" type="checkbox" class="size-4 rounded border accent-primary" />
              </td>
              <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
                <select v-model="draft.motivo_devolucao" :class="sheetSelectClass">
                  <option value="">—</option>
                  <option v-for="m in MOTIVOS_DEVOLUCAO" :key="m" :value="m">{{ m }}</option>
                </select>
              </td>
              <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
                <input v-model.number="draft.custo_manutencao" type="number" step="0.01" :class="sheetMoneyInputClass" />
              </td>
              <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
                <input v-model="draft.tecnico" :class="sheetInputClass" />
              </td>
              <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
                <input v-model="draft.devolver_estoque" :class="sheetInputClass" />
              </td>
              <td class="px-2 py-1 text-right border-l-[3px] border-gray-400 dark:border-gray-600">
                <Button size="sm" :disabled="creating || !canEdit" @click="createDevolution">
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
          placeholder="buscar pedido, conta, sku, motivo…"
        />
      </div>
      <select v-model="reembolsoFilter" class="h-9 rounded-md border bg-background px-2 text-sm">
        <option value="all">todas</option>
        <option value="true">reembolsadas</option>
        <option value="false">sem reembolso</option>
      </select>
      <span class="ml-auto text-xs text-muted-foreground">
        {{ rangeStart }}–{{ rangeEnd }} de {{ total }} · reembolsadas {{ totalReembolsadas }} · custo prod {{ brl(totalCustoProduto) }} · manut {{ brl(totalCustoManutencao) }}
      </span>
    </div>

    <div class="overflow-auto rounded border max-h-[75vh] focus:outline-none" tabindex="0">
      <table class="min-w-[1820px] text-xs border-collapse">
        <thead class="sticky top-0 z-20 bg-background">
          <tr>
            <th class="px-2 py-1 text-left text-[11px] font-semibold border-b" colspan="6">Identificação</th>
            <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-amber-50 dark:bg-amber-900/20" colspan="8">Devolução</th>
            <th class="px-2 py-1 text-left text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-emerald-50 dark:bg-emerald-900/20" colspan="1">Observação</th>
            <th class="px-2 py-1 text-right text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600" colspan="1">Ações</th>
          </tr>
          <tr class="border-b">
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[115px]">Data</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px]">Pedido Bling</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[155px]">Pedido Marketplace</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[150px]">Conta</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[130px]">SKU</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[200px]">Produtos</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px] bg-amber-50 dark:bg-amber-900/20 border-l-[3px] border-gray-400 dark:border-gray-600">Custo produto</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[140px] bg-amber-50 dark:bg-amber-900/20">Condição</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[180px] bg-amber-50 dark:bg-amber-900/20">Link abertura</th>
            <th class="px-2 py-1 text-center font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[80px] bg-amber-50 dark:bg-amber-900/20">Reembolso</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[180px] bg-amber-50 dark:bg-amber-900/20">Motivo</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px] bg-amber-50 dark:bg-amber-900/20">Custo manut.</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px] bg-amber-50 dark:bg-amber-900/20">Técnico</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px] bg-amber-50 dark:bg-amber-900/20">Dev. estoque</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[240px] bg-emerald-50 dark:bg-emerald-900/20 border-l-[3px] border-gray-400 dark:border-gray-600">Observação</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[95px] border-l-[3px] border-gray-400 dark:border-gray-600"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading && !items.length">
            <td colspan="16" class="py-8 text-center text-muted-foreground">
              <Loader2 class="size-4 inline animate-spin mr-1.5" />
              carregando…
            </td>
          </tr>
          <tr v-else-if="!items.length">
            <td colspan="16" class="py-8 text-center text-muted-foreground">sem registros</td>
          </tr>
          <tr v-for="row in items" :key="row.id" class="border-t hover:brightness-95 dark:hover:brightness-110">
            <td class="px-2 py-1 whitespace-nowrap text-muted-foreground">{{ fmtDateTime(row.data) }}</td>
            <td class="px-2 py-1 font-mono whitespace-nowrap">{{ row.pedido_bling || '—' }}</td>
            <td class="px-2 py-1 font-mono text-muted-foreground whitespace-nowrap">{{ row.pedido_marketplace || '—' }}</td>
            <td class="px-2 py-1 whitespace-nowrap">{{ row.conta }}</td>
            <td class="px-2 py-1 font-mono text-xs">{{ row.sku || '—' }}</td>
            <td class="px-2 py-1 text-muted-foreground">{{ row.produtos || '—' }}</td>
            <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10 border-l-[3px] border-gray-400 dark:border-gray-600">
              <input
                :value="row.custo_produto ?? ''"
                :disabled="!canEdit"
                type="number"
                step="0.01"
                :class="sheetMoneyInputClass"
                @input="(e) => setRowNumber(row, 'custo_produto', (e.target as HTMLInputElement).value)"
              />
            </td>
            <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
              <select
                :value="row.condicao_produto || ''"
                :disabled="!canEdit"
                :class="sheetSelectClass"
                @change="(e) => setRowText(row, 'condicao_produto', (e.target as HTMLSelectElement).value)"
              >
                <option value="">—</option>
                <option v-for="c in CONDICOES_PRODUTO" :key="c" :value="c">{{ c }}</option>
                <option
                  v-if="row.condicao_produto && !(CONDICOES_PRODUTO as readonly string[]).includes(row.condicao_produto)"
                  :value="row.condicao_produto"
                >{{ row.condicao_produto }}</option>
              </select>
            </td>
            <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
              <div class="flex items-center gap-1">
                <input
                  :value="row.link_abertura || ''"
                  :disabled="!canEdit"
                  :class="sheetInputClass"
                  @input="(e) => setRowText(row, 'link_abertura', (e.target as HTMLInputElement).value)"
                />
                <a
                  v-if="row.link_abertura"
                  :href="normalizeUrl(row.link_abertura)"
                  target="_blank"
                  rel="noopener noreferrer"
                  title="Abrir em nova guia"
                  class="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
                >
                  <ExternalLink class="size-3.5" />
                </a>
              </div>
            </td>
            <td class="px-2 py-1 text-center bg-amber-50/40 dark:bg-amber-900/10">
              <input
                :checked="row.reembolso"
                :disabled="!canEdit"
                type="checkbox"
                class="size-4 rounded border accent-primary disabled:cursor-default disabled:opacity-70"
                @change="(e) => setRowReembolso(row, (e.target as HTMLInputElement).checked)"
              />
            </td>
            <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
              <select
                :value="row.motivo_devolucao || ''"
                :disabled="!canEdit"
                :class="sheetSelectClass"
                @change="(e) => setRowText(row, 'motivo_devolucao', (e.target as HTMLSelectElement).value)"
              >
                <option value="">—</option>
                <option v-for="m in MOTIVOS_DEVOLUCAO" :key="m" :value="m">{{ m }}</option>
                <option
                  v-if="row.motivo_devolucao && !(MOTIVOS_DEVOLUCAO as readonly string[]).includes(row.motivo_devolucao)"
                  :value="row.motivo_devolucao"
                >{{ row.motivo_devolucao }}</option>
              </select>
            </td>
            <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
              <input
                :value="row.custo_manutencao ?? ''"
                :disabled="!canEdit"
                type="number"
                step="0.01"
                :class="sheetMoneyInputClass"
                @input="(e) => setRowNumber(row, 'custo_manutencao', (e.target as HTMLInputElement).value)"
              />
            </td>
            <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
              <input
                :value="row.tecnico || ''"
                :disabled="!canEdit"
                :class="sheetInputClass"
                @input="(e) => setRowText(row, 'tecnico', (e.target as HTMLInputElement).value)"
              />
            </td>
            <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
              <input
                :value="row.devolver_estoque || ''"
                :disabled="!canEdit"
                :class="sheetInputClass"
                @input="(e) => setRowText(row, 'devolver_estoque', (e.target as HTMLInputElement).value)"
              />
            </td>
            <td class="px-1 py-0.5 bg-emerald-50/40 dark:bg-emerald-900/10 border-l-[3px] border-gray-400 dark:border-gray-600">
              <input
                :value="row.observacao || ''"
                :disabled="!canEdit"
                :class="sheetInputClass"
                @input="(e) => setRowText(row, 'observacao', (e.target as HTMLInputElement).value)"
              />
            </td>
            <td class="px-2 py-1 border-l-[3px] border-gray-400 dark:border-gray-600">
              <div class="flex items-center justify-end gap-1">
                <button
                  type="button"
                  class="inline-flex h-7 w-7 items-center justify-center rounded border text-muted-foreground hover:bg-muted hover:text-foreground disabled:cursor-default disabled:opacity-40"
                  :disabled="!canEdit || !hasDirty(row.id) || isSaving(row.id)"
                  title="Salvar"
                  @click="saveRow(row)"
                >
                  <Loader2 v-if="isSaving(row.id)" class="size-4 animate-spin" />
                  <Save v-else class="size-4" />
                </button>
                <button
                  v-if="canDelete"
                  type="button"
                  class="inline-flex h-7 w-7 items-center justify-center rounded text-muted-foreground hover:bg-red-500/10 hover:text-red-500 disabled:cursor-default disabled:opacity-40"
                  :disabled="isDeleting(row.id)"
                  title="Excluir"
                  @click="deleteRow(row)"
                >
                  <Loader2 v-if="isDeleting(row.id)" class="size-4 animate-spin" />
                  <Trash2 v-else class="size-4" />
                </button>
              </div>
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
