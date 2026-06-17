<script setup lang="ts">
import { AlertCircle, Check, ChevronLeft, ChevronRight, Loader2, Pencil, RefreshCw, Search, X } from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'margem', action: 'view' },
})

type MarketplaceRow = {
  bling_order_item_id: string
  bling_id: number | null
  data: string | null
  pedido_bling: string | null
  pedido_marketplace: string | null
  plataforma: string | null
  conta: string | null
  sku: string | null
  produto: string | null
  quantidade: number | null
  custo_produto: number | null
  frete_plataforma: number | null
  frete_anuncio: number | null
  frete_projetado: number | null
  reembolso: number | null
  resultado_frete: number | null
  saldo_plataforma: number | null
  saldo_bling: number | null
  saldo_efetivo: number | null
  margem: number | null
  margem_bling: number | null
  margem_pos_reembolso: number | null
  margem_minima: number | null
  situacao_id: number | null
  situacao: string | null
  ajustes: number | null
  saldo_final: number | null
  status: string | null
  pricing_account_name: string | null
  pricing_account_listing_type: string | null
  pricing_leaf_segment_name: string | null
  bling_listing_type: string | null
  observacao: string | null
  attention_margem: boolean
  attention_frete: boolean
  attention_saldo: boolean
}

type PageResponse = {
  items: MarketplaceRow[]
  total: number
  limit: number
  offset: number
  platforms: string[]
  contas: string[]
  historico_disponivel?: boolean
}

type MargensStatus = 'Pendente' | 'Reprovado' | 'Aprovado'
const STATUS_OPTIONS: MargensStatus[] = ['Pendente', 'Reprovado', 'Aprovado']
const STATUS_CLS: Record<string, string> = {
  Pendente:  'bg-amber-500/15 text-amber-400 border-amber-500/40',
  Reprovado: 'bg-red-500/15 text-red-400 border-red-500/40',
  Aprovado:  'bg-emerald-500/15 text-emerald-400 border-emerald-500/40',
}
function statusCls(s: string | null | undefined): string {
  return STATUS_CLS[s ?? ''] ?? 'bg-muted text-muted-foreground border-border'
}

const PAGE_SIZE = 100
type StatusFilter = 'Pendente' | 'Aprovado' | 'Reprovado' | 'all'
const STATUS_FILTER_OPTIONS: StatusFilter[] = ['Pendente', 'Aprovado', 'Reprovado', 'all']

type AttentionType = 'all' | 'margem' | 'frete' | 'saldo'
const ATTENTION_LABEL: Record<AttentionType, string> = {
  all:    'todos motivos',
  margem: 'margem baixa',
  frete:  'frete negativo',
  saldo:  'saldo divergente',
}
const ATTENTION_OPTIONS: AttentionType[] = ['all', 'margem', 'frete', 'saldo']

const { api } = useApi()
const canEdit = useCan('margem', 'edit')

// Coluna "Frete Resultado" restrita a estes usuários.
const _auth = useAuthStore()
const FRETE_RESULTADO_USERS = [
  'spectrum77@tuta.com',
  'maconer06@tuta.com',
]
const canSeeFreteResultado = computed(() => {
  const email = _auth.user?.email?.toLowerCase()
  return !!email && FRETE_RESULTADO_USERS.includes(email)
})

const items = ref<MarketplaceRow[]>([])
const total = ref(0)
const platforms = ref<string[]>([])
const contas = ref<string[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

const search = ref('')
const platform = ref<'all' | string>('all')
const conta = ref<'all' | string>('all')
const statusFilter = ref<StatusFilter>('Pendente')
const attentionType = ref<AttentionType>('all')
const page = ref(1)

type Tab = 'list' | 'lookup'
const tab = ref<Tab>('list')
const lookupInput = ref('')
const lookupTerm = ref('')
const historicoDisponivel = ref(false)
const historicoLoading = ref(false)
const historicoElapsedMs = ref(0)

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))

function apiError(e: any) {
  const detail = e?.data?.detail
  if (detail && typeof detail === 'object') return detail.message || detail.code || e?.message || 'erro'
  return detail || e?.message || 'erro'
}

function apiErrorCode(e: any) {
  const detail = e?.data?.detail
  return detail && typeof detail === 'object' ? detail.code : null
}

function isBlingPatchError(e: any) {
  return ['bling_patch_failed', 'bling_integration_missing'].includes(apiErrorCode(e))
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const params = new URLSearchParams()
    params.set('limit', String(PAGE_SIZE))
    params.set('offset', String((page.value - 1) * PAGE_SIZE))
    if (platform.value !== 'all') params.set('platform', platform.value)
    if (conta.value !== 'all') params.set('conta', conta.value)
    if (statusFilter.value !== 'all') params.set('status', statusFilter.value)
    if (attentionType.value !== 'all') {
      params.set('attention_type', attentionType.value)
    }
    if (search.value.trim()) params.set('search', search.value.trim())
    const res = await api<PageResponse>(`/api/margens/marketplace?${params.toString()}`)
    items.value = res.items
    total.value = res.total
    platforms.value = res.platforms
    contas.value = res.contas
  } catch (e: any) {
    error.value = apiError(e)
  } finally {
    loading.value = false
  }
}

async function lookup(forceRefresh = false) {
  const term = lookupInput.value.trim()
  if (!term) {
    error.value = 'informe o numero do pedido'
    return
  }
  error.value = null
  lookupTerm.value = term
  historicoDisponivel.value = false

  // Slow-path (force_refresh) gets its own visual state with elapsed timer.
  // Fast-path (snapshot only) reuses the standard `loading` spinner.
  let timerHandle: ReturnType<typeof setInterval> | null = null
  if (forceRefresh) {
    historicoLoading.value = true
    historicoElapsedMs.value = 0
    const started = Date.now()
    timerHandle = setInterval(() => {
      historicoElapsedMs.value = Date.now() - started
    }, 250)
  } else {
    loading.value = true
  }

  try {
    const params = new URLSearchParams()
    params.set('pedido', term)
    if (forceRefresh) params.set('force_refresh', 'true')
    const res = await api<PageResponse>(`/api/margens/marketplace/lookup?${params.toString()}`)
    items.value = res.items
    total.value = res.total
    historicoDisponivel.value = !!res.historico_disponivel
  } catch (e: any) {
    error.value = apiError(e)
    items.value = []
    total.value = 0
  } finally {
    if (timerHandle) clearInterval(timerHandle)
    loading.value = false
    historicoLoading.value = false
  }
}

function lookupHistorico() {
  lookup(true)
}

function fmtElapsed(ms: number) {
  const s = Math.floor(ms / 1000)
  const m = Math.floor(s / 60)
  const rem = s % 60
  return m > 0 ? `${m}m ${String(rem).padStart(2, '0')}s` : `${s}s`
}

function switchTab(next: Tab) {
  if (tab.value === next) return
  tab.value = next
  error.value = null
  if (next === 'lookup') {
    // clear list to avoid confusing display; lookup only after submit
    items.value = []
    total.value = 0
    lookupTerm.value = ''
    historicoDisponivel.value = false
  } else {
    lookupInput.value = ''
    lookupTerm.value = ''
    historicoDisponivel.value = false
    page.value = 1
    load()
  }
}

await load()

async function reloadCurrent() {
  if (tab.value === 'lookup') {
    if (lookupTerm.value) await lookup()
  } else {
    await load()
  }
}

async function refreshAndLoad() {
  loading.value = true
  error.value = null
  try {
    await api('/api/margens/marketplace/refresh', { method: 'POST' })
  } catch (e: any) {
    error.value = apiError(e)
    loading.value = false
    return
  }
  await reloadCurrent()
}

// reload on filter change (debounced search) — only in list mode
let searchTimer: ReturnType<typeof setTimeout> | null = null
watch(search, () => {
  if (tab.value !== 'list') return
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    page.value = 1
    load()
  }, 350)
})
watch(platform, () => {
  if (tab.value !== 'list') return
  page.value = 1
  load()
})
watch(conta, () => {
  if (tab.value !== 'list') return
  page.value = 1
  load()
})
watch(statusFilter, () => {
  if (tab.value !== 'list') return
  page.value = 1
  load()
})
watch(attentionType, () => {
  if (tab.value !== 'list') return
  page.value = 1
  load()
})
watch(page, () => {
  if (tab.value !== 'list') return
  load()
})

function brl(v: number | null | undefined) {
  if (v == null) return '—'
  return v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
}

function pct(v: number | null | undefined) {
  if (v == null) return '—'
  return `${(v * 100).toFixed(1)}%`
}

function fmtDate(v: string | null) {
  if (!v) return '—'
  // Bling envia 'data' como YYYY-MM-DDT00:00:00Z — usar a parte YYYY-MM-DD
  // direto evita o off-by-one por causa do fuso BRT (UTC-3).
  const ymd = v.slice(0, 10)
  const [y, m, d] = ymd.split('-')
  if (!y || !m || !d) return v
  return `${d}/${m}/${y}`
}

function platformRowBg(platform: string | null): string {
  switch ((platform || '').toLowerCase()) {
    case 'shopee':       return 'bg-orange-50/50 dark:bg-orange-900/10'
    case 'mercadolivre':
    case 'mercado livre':
    case 'meli':         return 'bg-blue-50/50 dark:bg-blue-900/10'
    case 'amazon':       return 'bg-yellow-50/50 dark:bg-yellow-900/10'
    case 'magalu':       return 'bg-blue-50/30 dark:bg-blue-900/10'
    case 'tiktok':       return 'bg-pink-50/50 dark:bg-pink-900/10'
    default:             return ''
  }
}

function freteProjMissingReason(r: MarketplaceRow): string | null {
  if (r.frete_projetado != null) return null
  if (!r.pricing_leaf_segment_name) {
    return `Loja: ${r.conta ?? '—'} · SKU "${r.sku}" sem cadastro em pricing_products — cadastre em /pricing aba Produtos.`
  }
  if (!r.pricing_account_name) {
    return `Sem pricing_account p/ esta loja+segmento (${r.pricing_leaf_segment_name}). Cadastre em /pricing aba Contas.`
  }
  return `Pricing account "${r.pricing_account_name}" não tem shipping para o segmento "${r.pricing_leaf_segment_name}".`
}

// ---------- Status: aprovar/reprovar/pendente (atualiza Bling) ----------

async function setStatus(row: MarketplaceRow, value: MargensStatus) {
  if (!canEdit.value || value === row.status || !row.pedido_bling) return
  const prev = row.status
  const pedido = row.pedido_bling
  // Push to Bling apenas quando o problema é de margem. Frete/saldo divergente
  // só altera status local — sem situacao no Bling, sem dialog de fallback.
  const pushBling = row.attention_margem
  // optimistic update — propagar pra todas as linhas do mesmo pedido
  for (const r of items.value) if (r.pedido_bling === pedido) r.status = value

  async function call(local_only: boolean) {
    await api(`/api/margens/marketplace/status/${encodeURIComponent(pedido)}`, {
      method: 'PATCH',
      body: { status: value, sku: row.sku, local_only },
    })
  }

  function removeFromView() {
    if (statusFilter.value !== 'all' && value !== statusFilter.value) {
      const removed = items.value.filter(r => r.pedido_bling === pedido).length
      items.value = items.value.filter(r => r.pedido_bling !== pedido)
      total.value = Math.max(0, total.value - removed)
    }
  }

  try {
    await call(!pushBling)
    error.value = null
    removeFromView()
  } catch (e: any) {
    if (pushBling && isBlingPatchError(e)) {
      const ok = window.confirm(
        `O pedido nao foi alterado no Bling.\n\nDeseja continuar e marcar como ${value} apenas no DaVinci?`,
      )
      if (ok) {
        try {
          await call(true)
          error.value = null
          removeFromView()
          return
        } catch (fallback: any) {
          error.value = apiError(fallback)
        }
      }
    } else {
      error.value = apiError(e)
    }
    for (const r of items.value) if (r.pedido_bling === pedido) r.status = prev
  }
}

// ---------- Sync Bling ← Marketplace (one-shot) ----------

const syncing = ref<Set<string>>(new Set())
function isSyncing(id: string): boolean { return syncing.value.has(id) }

async function syncFromMarketplace(row: MarketplaceRow) {
  if (!canEdit.value) return
  const id = row.bling_order_item_id
  if (syncing.value.has(id)) return
  syncing.value.add(id)
  try {
    await api(`/api/margens/marketplace/${encodeURIComponent(id)}/sync-from-marketplace`, {
      method: 'POST',
    })
    error.value = null
    await reloadCurrent()
  } catch (e: any) {
    error.value = apiError(e)
  } finally {
    const next = new Set(syncing.value)
    next.delete(id)
    syncing.value = next
  }
}

const syncingSaldoFinal = ref<Set<string>>(new Set())
function isSyncingSaldoFinal(id: string): boolean { return syncingSaldoFinal.value.has(id) }

async function syncFromSaldoFinal(row: MarketplaceRow, valorBase?: number) {
  if (!canEdit.value) return
  const id = row.bling_order_item_id
  if (syncingSaldoFinal.value.has(id)) return
  syncingSaldoFinal.value.add(id)
  try {
    await api(`/api/margens/marketplace/${encodeURIComponent(id)}/sync-saldo-final`, {
      method: 'POST',
      body: valorBase != null ? { valor_base: valorBase } : undefined,
    })
    error.value = null
    await reloadCurrent()
  } catch (e: any) {
    error.value = apiError(e)
  } finally {
    const next = new Set(syncingSaldoFinal.value)
    next.delete(id)
    syncingSaldoFinal.value = next
  }
}

async function syncSaldoEfetivoAsFinal(row: MarketplaceRow) {
  if (row.saldo_efetivo == null) return
  await syncFromSaldoFinal(row, row.saldo_efetivo)
}

// ---------- Edição inline de Saldo Efetivo (grava como final no Bling) ----------

const editingSaldoEfetivo = ref<string | null>(null)
const saldoEfetivoDraft = ref('')

function startEditSaldoEfetivo(row: MarketplaceRow) {
  if (!canEdit.value || !row.pedido_bling) return
  editingSaldoEfetivo.value = row.bling_order_item_id
  saldoEfetivoDraft.value = row.saldo_efetivo != null ? String(row.saldo_efetivo) : ''
}

function cancelEditSaldoEfetivo() {
  editingSaldoEfetivo.value = null
  saldoEfetivoDraft.value = ''
}

async function saveSaldoEfetivo(row: MarketplaceRow) {
  const raw = saldoEfetivoDraft.value.trim().replace(',', '.')
  const next = Number(raw)
  if (raw === '' || Number.isNaN(next)) {
    cancelEditSaldoEfetivo()
    return
  }
  if (row.saldo_efetivo != null && Math.abs(next - row.saldo_efetivo) < 0.005) {
    cancelEditSaldoEfetivo()
    return
  }
  cancelEditSaldoEfetivo()
  await syncFromSaldoFinal(row, next)
}

// ---------- Edição inline de observação ----------

const editingObs = ref<string | null>(null)
const obsDraft = ref('')

function startEditObs(row: MarketplaceRow) {
  if (!canEdit.value || !row.pedido_bling) return
  editingObs.value = row.bling_order_item_id
  obsDraft.value = row.observacao ?? ''
}

function cancelEditObs() {
  editingObs.value = null
  obsDraft.value = ''
}

async function saveObs(row: MarketplaceRow) {
  if (!row.pedido_bling) {
    cancelEditObs()
    return
  }
  const next = obsDraft.value.trim() || null
  if (next === (row.observacao ?? null)) {
    cancelEditObs()
    return
  }
  try {
    await api(`/api/margens/marketplace/observacao/${encodeURIComponent(row.pedido_bling)}`, {
      method: 'PATCH',
      body: { observacao: next },
    })
    for (const r of items.value) {
      if (r.pedido_bling === row.pedido_bling) r.observacao = next
    }
  } catch (e: any) {
    error.value = apiError(e)
  } finally {
    cancelEditObs()
  }
}

const rangeStart = computed(() => total.value === 0 ? 0 : (page.value - 1) * PAGE_SIZE + 1)
const rangeEnd = computed(() => Math.min(page.value * PAGE_SIZE, total.value))
</script>

<template>
  <div class="space-y-5">
    <PageHeader title="Margem" description="Margem por pedido — conciliação marketplace (últimos 30 dias).">
      <template #actions>
        <Button size="sm" variant="outline" :disabled="loading" @click="refreshAndLoad">
          <RefreshCw class="size-4 mr-1.5" :class="{ 'animate-spin': loading }" />
          atualizar
        </Button>
      </template>
    </PageHeader>

    <div v-if="error" class="flex items-center gap-2 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-400">
      <AlertCircle class="size-4" />
      {{ error }}
    </div>

    <div class="flex items-center gap-1 border-b">
      <button
        type="button"
        class="px-3 py-1.5 text-sm border-b-2 -mb-px transition-colors"
        :class="tab === 'list'
          ? 'border-primary text-foreground font-medium'
          : 'border-transparent text-muted-foreground hover:text-foreground'"
        @click="switchTab('list')"
      >
        Pendentes (30d)
      </button>
      <button
        type="button"
        class="px-3 py-1.5 text-sm border-b-2 -mb-px transition-colors"
        :class="tab === 'lookup'
          ? 'border-primary text-foreground font-medium'
          : 'border-transparent text-muted-foreground hover:text-foreground'"
        @click="switchTab('lookup')"
      >
        Buscar pedido
      </button>
    </div>

    <div v-if="tab === 'list'" class="flex flex-wrap items-center gap-2">
      <div class="relative">
        <Search class="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
        <input
          v-model="search"
          class="pl-8 pr-3 py-1.5 text-sm rounded-md border bg-background w-72"
          placeholder="buscar pedido, sku, conta, pricing account…"
        />
      </div>
      <select
        v-model="platform"
        class="text-sm rounded-md border bg-background px-2 py-1.5"
      >
        <option value="all">todas plataformas</option>
        <option v-for="p in platforms" :key="p" :value="p">{{ p }}</option>
      </select>
      <select
        v-model="conta"
        class="text-sm rounded-md border bg-background px-2 py-1.5"
      >
        <option value="all">todas contas</option>
        <option v-for="c in contas" :key="c" :value="c">{{ c }}</option>
      </select>
      <select
        v-model="statusFilter"
        class="text-sm rounded-md border bg-background px-2 py-1.5"
      >
        <option v-for="s in STATUS_FILTER_OPTIONS" :key="s" :value="s">
          {{ s === 'all' ? 'todos status' : s }}
        </option>
      </select>
      <select
        v-model="attentionType"
        class="text-sm rounded-md border bg-background px-2 py-1.5"
      >
        <option v-for="a in ATTENTION_OPTIONS" :key="a" :value="a">
          {{ ATTENTION_LABEL[a] }}
        </option>
      </select>
      <span class="ml-auto text-xs text-muted-foreground">
        {{ rangeStart }}–{{ rangeEnd }} de {{ total }}
      </span>
    </div>

    <div v-else class="flex flex-wrap items-center gap-2">
      <div class="relative">
        <Search class="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
        <input
          v-model="lookupInput"
          class="pl-8 pr-3 py-1.5 text-sm rounded-md border bg-background w-72"
          placeholder="numero do pedido (Bling ou marketplace)"
          :disabled="historicoLoading"
          @keydown.enter="lookup(false)"
        />
      </div>
      <Button
        size="sm"
        :disabled="loading || historicoLoading || !lookupInput.trim()"
        @click="lookup(false)"
      >
        <Search class="size-4 mr-1.5" />
        buscar
      </Button>
      <span v-if="lookupTerm" class="text-xs text-muted-foreground">
        pedido <span class="font-mono">{{ lookupTerm }}</span> · {{ total }} {{ total === 1 ? 'item' : 'itens' }}
      </span>
      <span v-else class="text-xs text-muted-foreground">
        digite o numero do pedido e clique em buscar
      </span>
      <Button
        v-if="lookupTerm && items.length && historicoDisponivel"
        size="sm"
        variant="outline"
        :disabled="loading || historicoLoading"
        title="Recalcula da view completa (sem janela de 30 dias). Pode levar alguns minutos."
        @click="lookupHistorico"
      >
        <RefreshCw class="size-4 mr-1.5" :class="{ 'animate-spin': historicoLoading }" />
        atualizar este pedido
      </Button>
    </div>

    <!-- Slow-path overlay: only when user opted-in via "buscar no historico" -->
    <div
      v-if="historicoLoading"
      class="rounded-lg border border-amber-500/40 bg-amber-500/5 px-4 py-5"
    >
      <div class="flex items-start gap-4">
        <svg
          class="size-10 shrink-0 animate-spin text-amber-500"
          viewBox="0 0 24 24"
          fill="none"
          xmlns="http://www.w3.org/2000/svg"
          aria-hidden="true"
        >
          <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-opacity="0.25" stroke-width="3" />
          <path
            d="M22 12a10 10 0 0 1-10 10"
            stroke="currentColor"
            stroke-width="3"
            stroke-linecap="round"
          />
        </svg>
        <div class="flex-1">
          <div class="text-sm font-medium text-amber-700 dark:text-amber-300">
            Buscando pedido <span class="font-mono">{{ lookupTerm }}</span> no historico…
          </div>
          <p class="mt-1 text-xs text-muted-foreground">
            A view de conciliacao precisa materializar todos os pedidos antes
            de filtrar, entao essa busca pode levar varios minutos. Pode deixar
            essa aba aberta — o resultado aparece assim que terminar.
          </p>
          <div class="mt-2 text-xs font-mono tabular-nums text-amber-700 dark:text-amber-300">
            {{ fmtElapsed(historicoElapsedMs) }} decorrido
          </div>
        </div>
      </div>
    </div>

    <!-- Snapshot miss + CTA to opt-in to slow path -->
    <div
      v-else-if="tab === 'lookup' && historicoDisponivel && !items.length"
      class="rounded-lg border border-blue-500/40 bg-blue-500/5 px-4 py-4"
    >
      <div class="flex items-start gap-3">
        <AlertCircle class="size-5 shrink-0 text-blue-500 mt-0.5" />
        <div class="flex-1">
          <div class="text-sm font-medium">
            Pedido <span class="font-mono">{{ lookupTerm }}</span> nao esta no snapshot recente
          </div>
          <p class="mt-1 text-xs text-muted-foreground">
            O pedido existe no Bling mas esta fora da janela de 20 dias do
            cache. A busca no historico le a view completa de conciliacao e
            pode levar varios minutos.
          </p>
          <Button size="sm" class="mt-3" @click="lookupHistorico">
            <Search class="size-4 mr-1.5" />
            buscar no historico
          </Button>
        </div>
      </div>
    </div>

    <div class="overflow-auto rounded border max-h-[75vh] focus:outline-none" tabindex="0">
      <table class="text-xs border-collapse">
        <thead class="bg-background sticky top-0 z-20">
          <tr>
            <th class="px-2 py-1 text-left text-[11px] font-semibold border-b" colspan="3">Identificação</th>
            <th class="px-2 py-1 text-left text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600" colspan="5">Anúncio</th>
            <th class="px-2 py-1 text-right text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600" colspan="2">Item</th>
            <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-amber-50 dark:bg-amber-900/20" :colspan="canSeeFreteResultado ? 5 : 4">Frete</th>
            <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-emerald-50 dark:bg-emerald-900/20" colspan="3">Saldo</th>
            <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-blue-50 dark:bg-blue-900/20" colspan="4">Margem</th>
            <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600" colspan="1">Situação</th>
            <th class="px-2 py-1 text-left text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600" colspan="2">Aprovação</th>
          </tr>
          <tr class="border-b">
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[80px]">Data</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px]">Pedido Bling</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[140px]">Pedido Marketplace</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[90px] border-l-[3px] border-gray-400 dark:border-gray-600">Plataforma</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[130px]">Conta</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[140px]">Segmento</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px]">SKU</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground min-w-[240px]">Produto</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[60px] border-l-[3px] border-gray-400 dark:border-gray-600">Quantidade</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[90px]">Custo</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px] bg-amber-50 dark:bg-amber-900/20 border-l-[3px] border-gray-400 dark:border-gray-600">Frete Plataforma</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px] bg-amber-50 dark:bg-amber-900/20">Frete Anúncio</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px] bg-amber-50 dark:bg-amber-900/20">Frete Projetado</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[100px] bg-amber-50 dark:bg-amber-900/20">Reembolso</th>
            <th v-if="canSeeFreteResultado" class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px] bg-amber-50 dark:bg-amber-900/20">Frete Resultado</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px] bg-emerald-50 dark:bg-emerald-900/20 border-l-[3px] border-gray-400 dark:border-gray-600">Saldo Plataforma</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[100px] bg-emerald-50 dark:bg-emerald-900/20">Saldo Bling</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[140px] bg-emerald-50 dark:bg-emerald-900/20">Saldo Efetivo</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[80px] bg-blue-50 dark:bg-blue-900/20 border-l-[3px] border-gray-400 dark:border-gray-600">Margem</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[80px] bg-blue-50 dark:bg-blue-900/20">Margem Bling</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[130px] bg-blue-50 dark:bg-blue-900/20">Margem Pós Reembolso</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[100px] bg-blue-50 dark:bg-blue-900/20">Margem Mínima</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[140px] border-l-[3px] border-gray-400 dark:border-gray-600">Situação</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px] border-l-[3px] border-gray-400 dark:border-gray-600">Status</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[180px]">Observação</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading && !items.length">
            <td :colspan="canSeeFreteResultado ? 24 : 23" class="text-center py-8 text-muted-foreground">
              <Loader2 class="size-4 inline animate-spin mr-1.5" /> carregando…
            </td>
          </tr>
          <tr v-else-if="!items.length">
            <td :colspan="canSeeFreteResultado ? 24 : 23" class="text-center py-8 text-muted-foreground">
              <template v-if="tab === 'lookup' && !lookupTerm">
                digite o numero do pedido acima para buscar
              </template>
              <template v-else-if="tab === 'lookup' && historicoDisponivel">
                <!-- a CTA "buscar no historico" ja foi exibida acima -->
                snapshot vazio para este pedido
              </template>
              <template v-else-if="tab === 'lookup'">
                nenhum item encontrado para o pedido <span class="font-mono">{{ lookupTerm }}</span>
              </template>
              <template v-else>
                sem registros
              </template>
            </td>
          </tr>
          <tr
            v-for="r in items"
            :key="r.bling_order_item_id"
            class="border-t hover:brightness-95 dark:hover:brightness-110"
            :class="platformRowBg(r.plataforma)"
          >
            <td class="px-2 py-1 whitespace-nowrap text-muted-foreground">{{ fmtDate(r.data) }}</td>
            <td class="px-2 py-1 tabular-nums font-medium whitespace-nowrap">{{ r.pedido_bling ?? '—' }}</td>
            <td class="px-2 py-1 tabular-nums text-muted-foreground whitespace-nowrap">{{ r.pedido_marketplace ?? '—' }}</td>
            <td class="px-2 py-1 uppercase whitespace-nowrap border-l-[3px] border-gray-400 dark:border-gray-600">{{ r.plataforma || '—' }}</td>
            <td
              class="px-2 py-1 whitespace-nowrap cursor-help"
              :class="!r.pricing_account_name && !r.pricing_leaf_segment_name ? 'text-red-400' : ''"
              :title="freteProjMissingReason(r) || ''"
            >
              <template v-if="r.pricing_account_name">{{ r.pricing_account_name }}</template>
              <template v-else-if="!r.pricing_leaf_segment_name">⚠️ sem cadastro</template>
              <template v-else><span class="text-amber-500">sem account</span></template>
            </td>
            <td class="px-2 py-1 whitespace-nowrap text-muted-foreground">{{ r.pricing_leaf_segment_name || '—' }}</td>
            <td class="px-2 py-1 font-mono whitespace-nowrap">{{ r.sku || '—' }}</td>
            <td class="px-2 py-1 max-w-[260px] truncate" :title="r.produto || ''">{{ r.produto || '—' }}</td>
            <td class="px-2 py-1 text-right tabular-nums border-l-[3px] border-gray-400 dark:border-gray-600">{{ r.quantidade ?? '—' }}</td>
            <td class="px-2 py-1 text-right tabular-nums text-muted-foreground whitespace-nowrap">{{ brl(r.custo_produto) }}</td>
            <td class="px-2 py-1 text-right tabular-nums whitespace-nowrap bg-amber-50/40 dark:bg-amber-900/10 border-l-[3px] border-gray-400 dark:border-gray-600">{{ brl(r.frete_plataforma) }}</td>
            <td class="px-2 py-1 text-right tabular-nums whitespace-nowrap bg-amber-50/40 dark:bg-amber-900/10">{{ brl(r.frete_anuncio) }}</td>
            <td
              class="px-2 py-1 text-right tabular-nums bg-amber-50/40 dark:bg-amber-900/10 cursor-help whitespace-nowrap"
              :class="r.frete_projetado == null
                ? (!r.pricing_leaf_segment_name ? 'text-red-500' : 'text-amber-500')
                : ''"
              :title="freteProjMissingReason(r) || ''"
            >
              {{ r.frete_projetado != null ? brl(r.frete_projetado) : (!r.pricing_leaf_segment_name ? '⚠️' : '—') }}
            </td>
            <td class="px-2 py-1 text-right tabular-nums whitespace-nowrap bg-amber-50/40 dark:bg-amber-900/10">{{ brl(r.reembolso) }}</td>
            <td
              v-if="canSeeFreteResultado"
              class="px-2 py-1 text-right tabular-nums whitespace-nowrap bg-amber-50/40 dark:bg-amber-900/10 font-medium"
              :class="r.resultado_frete != null ? (r.resultado_frete > 0 ? 'text-red-600 dark:text-red-400' : 'text-emerald-600 dark:text-emerald-400') : ''"
            >
              {{ brl(r.resultado_frete) }}
            </td>
            <td class="px-2 py-1 text-right tabular-nums whitespace-nowrap bg-emerald-50/40 dark:bg-emerald-900/10 border-l-[3px] border-gray-400 dark:border-gray-600">{{ brl(r.saldo_plataforma) }}</td>
            <td class="px-2 py-1 text-right tabular-nums whitespace-nowrap bg-emerald-50/40 dark:bg-emerald-900/10 text-muted-foreground">{{ brl(r.saldo_bling) }}</td>
            <td class="px-2 py-1 whitespace-nowrap bg-emerald-50/40 dark:bg-emerald-900/10 font-medium">
              <div v-if="editingSaldoEfetivo === r.bling_order_item_id" class="flex items-center justify-end">
                <input
                  v-model="saldoEfetivoDraft"
                  type="text"
                  inputmode="decimal"
                  class="w-24 px-2 py-1 text-xs text-right tabular-nums rounded-md border bg-background"
                  autofocus
                  @keydown.enter="saveSaldoEfetivo(r)"
                  @keydown.esc="cancelEditSaldoEfetivo"
                  @blur="saveSaldoEfetivo(r)"
                />
              </div>
              <div v-else class="flex items-center justify-end gap-2">
                <button
                  v-if="canEdit && [6, 83965].includes(r.situacao_id) && r.saldo_plataforma != null && r.saldo_bling != null && Math.abs(r.saldo_plataforma - r.saldo_bling) > 0.01"
                  type="button"
                  :disabled="isSyncing(r.bling_order_item_id)"
                  class="text-[10px] font-medium px-1.5 py-0.5 rounded border border-emerald-500/40 text-emerald-700 dark:text-emerald-300 hover:bg-emerald-500/10 disabled:opacity-50 disabled:cursor-default"
                  :title="`Copia bruto/taxa/frete do Marketplace para Bling neste item`"
                  @click="syncFromMarketplace(r)"
                >
                  <Loader2 v-if="isSyncing(r.bling_order_item_id)" class="h-3 w-3 animate-spin inline" />
                  <span v-else>→</span>
                </button>
                <button
                  v-if="canEdit && r.saldo_efetivo != null && (r.saldo_bling == null || Math.abs(r.saldo_efetivo - r.saldo_bling) > 0.01)"
                  type="button"
                  :disabled="isSyncingSaldoFinal(r.bling_order_item_id)"
                  class="text-[10px] font-medium px-1.5 py-0.5 rounded border border-emerald-500/40 text-emerald-700 dark:text-emerald-300 hover:bg-emerald-500/10 disabled:opacity-50 disabled:cursor-default"
                  :title="`Grava o Saldo Efetivo como valor final no Bling (zera taxa e frete)`"
                  @click="syncSaldoEfetivoAsFinal(r)"
                >
                  <Loader2 v-if="isSyncingSaldoFinal(r.bling_order_item_id)" class="h-3 w-3 animate-spin inline" />
                  <Check v-else class="h-3 w-3" />
                </button>
                <button
                  type="button"
                  class="flex items-center justify-end gap-1 text-right tabular-nums hover:text-foreground disabled:cursor-default"
                  :disabled="!canEdit || !r.pedido_bling || isSyncingSaldoFinal(r.bling_order_item_id)"
                  :title="canEdit && r.pedido_bling ? `Editar Saldo Efetivo → grava o valor como final no Bling (zera taxa e frete)` : ''"
                  @click="startEditSaldoEfetivo(r)"
                >
                  <Loader2 v-if="isSyncingSaldoFinal(r.bling_order_item_id)" class="h-3 w-3 animate-spin inline" />
                  <span>{{ brl(r.saldo_efetivo) }}</span>
                  <Pencil v-if="canEdit" class="size-3 shrink-0 opacity-50" />
                </button>
              </div>
            </td>
            <td
              class="px-2 py-1 text-right tabular-nums whitespace-nowrap bg-blue-50/40 dark:bg-blue-900/10 font-medium border-l-[3px] border-gray-400 dark:border-gray-600"
              :class="r.margem != null && r.margem_minima != null
                ? (r.margem >= r.margem_minima ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400')
                : (r.margem != null && r.margem >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400')"
            >
              {{ pct(r.margem) }}
            </td>
            <td
              class="px-2 py-1 text-right tabular-nums whitespace-nowrap bg-blue-50/40 dark:bg-blue-900/10 font-medium"
              :class="r.margem_bling != null && r.margem_minima != null
                ? (r.margem_bling >= r.margem_minima ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400')
                : (r.margem_bling != null && r.margem_bling >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400')"
            >
              {{ pct(r.margem_bling) }}
            </td>
            <td
              class="px-2 py-1 text-right tabular-nums whitespace-nowrap bg-blue-50/40 dark:bg-blue-900/10 font-medium"
              :class="r.margem_pos_reembolso != null && r.margem_minima != null
                ? (r.margem_pos_reembolso >= r.margem_minima ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400')
                : (r.margem_pos_reembolso != null && r.margem_pos_reembolso >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400')"
            >
              {{ pct(r.margem_pos_reembolso) }}
            </td>
            <td class="px-2 py-1 text-right tabular-nums whitespace-nowrap bg-blue-50/40 dark:bg-blue-900/10 text-muted-foreground">{{ pct(r.margem_minima) }}</td>
            <td class="px-2 py-1 whitespace-nowrap text-muted-foreground border-l-[3px] border-gray-400 dark:border-gray-600">{{ r.situacao || '—' }}</td>
            <td class="px-2 py-1 border-l-[3px] border-gray-400 dark:border-gray-600">
              <select
                :value="r.status ?? 'Pendente'"
                :disabled="!canEdit || !r.pedido_bling"
                class="pill border text-[11px] font-medium px-2 py-1 rounded-md cursor-pointer disabled:cursor-default disabled:opacity-70"
                :class="statusCls(r.status ?? 'Pendente')"
                @change="(e) => setStatus(r, (e.target as HTMLSelectElement).value as MargensStatus)"
              >
                <option v-for="s in STATUS_OPTIONS" :key="s" :value="s">{{ s }}</option>
              </select>
            </td>
            <td class="px-2 py-1 max-w-[200px]">
              <div v-if="editingObs === r.bling_order_item_id" class="flex items-center gap-1">
                <input
                  v-model="obsDraft"
                  class="flex-1 px-2 py-1 text-xs rounded-md border bg-background"
                  autofocus
                  @keydown.enter="saveObs(r)"
                  @keydown.esc="cancelEditObs"
                />
                <button class="p-1 text-emerald-500 hover:opacity-80" @click="saveObs(r)">
                  <Check class="size-3.5" />
                </button>
                <button class="p-1 text-muted-foreground hover:opacity-80" @click="cancelEditObs">
                  <X class="size-3.5" />
                </button>
              </div>
              <button
                v-else
                class="flex items-center gap-1.5 text-left text-muted-foreground hover:text-foreground w-full truncate disabled:cursor-default"
                :disabled="!canEdit || !r.pedido_bling"
                :title="r.observacao || ''"
                @click="startEditObs(r)"
              >
                <span class="truncate">{{ r.observacao || (canEdit ? 'adicionar…' : '—') }}</span>
                <Pencil v-if="canEdit" class="size-3 shrink-0 opacity-50" />
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Pagination -->
    <div v-if="tab === 'list' && total > PAGE_SIZE" class="flex items-center justify-between gap-2">
      <span class="text-xs text-muted-foreground">
        página {{ page }} de {{ totalPages }} · {{ PAGE_SIZE }}/página
      </span>
      <div class="flex items-center gap-1">
        <Button
          size="sm"
          variant="outline"
          :disabled="page <= 1 || loading"
          @click="page = 1"
        >
          «
        </Button>
        <Button
          size="sm"
          variant="outline"
          :disabled="page <= 1 || loading"
          @click="page = page - 1"
        >
          <ChevronLeft class="size-4" />
        </Button>
        <input
          v-model.number="page"
          type="number"
          :min="1"
          :max="totalPages"
          class="w-16 text-sm text-center rounded-md border bg-background px-2 py-1"
          @change="page = Math.min(Math.max(1, page), totalPages)"
        />
        <Button
          size="sm"
          variant="outline"
          :disabled="page >= totalPages || loading"
          @click="page = page + 1"
        >
          <ChevronRight class="size-4" />
        </Button>
        <Button
          size="sm"
          variant="outline"
          :disabled="page >= totalPages || loading"
          @click="page = totalPages"
        >
          »
        </Button>
      </div>
    </div>
  </div>
</template>
