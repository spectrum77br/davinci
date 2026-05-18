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
  margem_minima: number | null
  status: string | null
  pricing_account_name: string | null
  pricing_account_listing_type: string | null
  pricing_leaf_segment_name: string | null
  bling_listing_type: string | null
  observacao: string | null
}

type PageResponse = {
  items: MarketplaceRow[]
  total: number
  limit: number
  offset: number
  platforms: string[]
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

const items = ref<MarketplaceRow[]>([])
const total = ref(0)
const platforms = ref<string[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

const search = ref('')
const platform = ref<'all' | string>('all')
const statusFilter = ref<StatusFilter>('Pendente')
const attentionType = ref<AttentionType>('all')
const page = ref(1)

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

function isBlingSituationGuardError(e: any) {
  return ['bling_situacao_not_verificar_margem', 'bling_situacao_check_failed'].includes(apiErrorCode(e))
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const params = new URLSearchParams()
    params.set('limit', String(PAGE_SIZE))
    params.set('offset', String((page.value - 1) * PAGE_SIZE))
    if (platform.value !== 'all') params.set('platform', platform.value)
    if (statusFilter.value !== 'all') params.set('status', statusFilter.value)
    if (attentionType.value !== 'all') {
      params.set('attention_type', attentionType.value)
    }
    if (search.value.trim()) params.set('search', search.value.trim())
    const res = await api<PageResponse>(`/api/margens/marketplace?${params.toString()}`)
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
  await load()
}

// reload on filter change (debounced search)
let searchTimer: ReturnType<typeof setTimeout> | null = null
watch(search, () => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    page.value = 1
    load()
  }, 350)
})
watch(platform, () => {
  page.value = 1
  load()
})
watch(statusFilter, () => {
  page.value = 1
  load()
})
watch(attentionType, () => {
  page.value = 1
  load()
})
watch(page, () => load())

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
    return `SKU "${r.sku}" sem cadastro em pricing_products — cadastre em /pricing aba Produtos.`
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
  // optimistic update — propagar pra todas as linhas do mesmo pedido
  for (const r of items.value) if (r.pedido_bling === pedido) r.status = value

  async function call(local_only: boolean) {
    await api(`/api/margens/marketplace/status/${encodeURIComponent(pedido)}`, {
      method: 'PATCH',
      body: { status: value, sku: row.sku, local_only },
    })
  }

  try {
    await call(false)
    error.value = null
  } catch (e: any) {
    if (isBlingSituationGuardError(e)) {
      window.alert(apiError(e))
      for (const r of items.value) if (r.pedido_bling === pedido) r.status = prev
      error.value = null
      return
    }
    if (isBlingPatchError(e)) {
      const ok = window.confirm(
        `O pedido nao foi alterado no Bling.\n\nDeseja continuar e marcar como ${value} apenas no DaVinci?`,
      )
      if (ok) {
        try {
          await call(true)
          error.value = null
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
    await load()
  } catch (e: any) {
    error.value = apiError(e)
  } finally {
    const next = new Set(syncing.value)
    next.delete(id)
    syncing.value = next
  }
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

    <div class="flex flex-wrap items-center gap-2">
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

    <div class="overflow-auto rounded border max-h-[75vh] focus:outline-none" tabindex="0">
      <table class="text-xs border-collapse">
        <thead class="bg-background sticky top-0 z-20">
          <tr>
            <th class="px-2 py-1 text-left text-[11px] font-semibold border-b" colspan="3">Identificação</th>
            <th class="px-2 py-1 text-left text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600" colspan="5">Anúncio</th>
            <th class="px-2 py-1 text-right text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600" colspan="2">Item</th>
            <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-amber-50 dark:bg-amber-900/20" colspan="5">Frete</th>
            <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-emerald-50 dark:bg-emerald-900/20" colspan="3">Saldo</th>
            <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-blue-50 dark:bg-blue-900/20" colspan="2">Margem</th>
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
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px] bg-amber-50 dark:bg-amber-900/20">Frete Resultado</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px] bg-emerald-50 dark:bg-emerald-900/20 border-l-[3px] border-gray-400 dark:border-gray-600">Saldo Plataforma</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[100px] bg-emerald-50 dark:bg-emerald-900/20">Saldo Bling</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px] bg-emerald-50 dark:bg-emerald-900/20">Saldo Efetivo</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[80px] bg-blue-50 dark:bg-blue-900/20 border-l-[3px] border-gray-400 dark:border-gray-600">Margem</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[100px] bg-blue-50 dark:bg-blue-900/20">Margem Mínima</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px] border-l-[3px] border-gray-400 dark:border-gray-600">Status</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[180px]">Observação</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading && !items.length">
            <td colspan="22" class="text-center py-8 text-muted-foreground">
              <Loader2 class="size-4 inline animate-spin mr-1.5" /> carregando…
            </td>
          </tr>
          <tr v-else-if="!items.length">
            <td colspan="22" class="text-center py-8 text-muted-foreground">
              sem registros
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
              class="px-2 py-1 text-right tabular-nums whitespace-nowrap bg-amber-50/40 dark:bg-amber-900/10 font-medium"
              :class="r.resultado_frete != null ? (r.resultado_frete >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400') : ''"
            >
              {{ brl(r.resultado_frete) }}
            </td>
            <td class="px-2 py-1 text-right tabular-nums whitespace-nowrap bg-emerald-50/40 dark:bg-emerald-900/10 border-l-[3px] border-gray-400 dark:border-gray-600">{{ brl(r.saldo_plataforma) }}</td>
            <td class="px-2 py-1 text-right tabular-nums whitespace-nowrap bg-emerald-50/40 dark:bg-emerald-900/10 text-muted-foreground">{{ brl(r.saldo_bling) }}</td>
            <td class="px-2 py-1 whitespace-nowrap bg-emerald-50/40 dark:bg-emerald-900/10 font-medium">
              <div class="flex items-center justify-end gap-2">
                <button
                  v-if="canEdit && r.saldo_plataforma != null && r.saldo_bling != null && Math.abs(r.saldo_plataforma - r.saldo_bling) > 0.01"
                  type="button"
                  :disabled="isSyncing(r.bling_order_item_id)"
                  class="text-[10px] font-medium px-1.5 py-0.5 rounded border border-emerald-500/40 text-emerald-700 dark:text-emerald-300 hover:bg-emerald-500/10 disabled:opacity-50 disabled:cursor-default"
                  :title="`Copia bruto/taxa/frete do Marketplace para Bling neste item`"
                  @click="syncFromMarketplace(r)"
                >
                  <Loader2 v-if="isSyncing(r.bling_order_item_id)" class="h-3 w-3 animate-spin inline" />
                  <span v-else>Mkt →</span>
                </button>
                <span class="text-right tabular-nums">{{ brl(r.saldo_efetivo) }}</span>
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
            <td class="px-2 py-1 text-right tabular-nums whitespace-nowrap bg-blue-50/40 dark:bg-blue-900/10 text-muted-foreground">{{ pct(r.margem_minima) }}</td>
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
    <div v-if="total > PAGE_SIZE" class="flex items-center justify-between gap-2">
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
