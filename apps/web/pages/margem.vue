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
  return new Date(v).toLocaleDateString('pt-BR')
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
      <span class="ml-auto text-xs text-muted-foreground">
        {{ rangeStart }}–{{ rangeEnd }} de {{ total }}
      </span>
    </div>

    <div class="table-card overflow-x-auto">
      <table class="w-full text-xs min-w-[1800px]">
        <thead>
          <tr>
            <th>Data</th>
            <th>Pedido</th>
            <th>Plataforma</th>
            <th>Conta</th>
            <th>SKU</th>
            <th>Produto</th>
            <th class="text-right">Custo</th>
            <th class="text-right bg-amber-500/10">Frete plat.</th>
            <th class="text-right bg-amber-500/10">Frete anún.</th>
            <th class="text-right bg-amber-500/10">Frete proj.</th>
            <th class="text-right bg-amber-500/10">Reembolso</th>
            <th class="text-right bg-amber-500/10">Result. frete</th>
            <th class="text-right bg-amber-500/10">Saldo plat.</th>
            <th class="text-right">Saldo Bling</th>
            <th class="text-right bg-amber-500/10">Saldo efetivo</th>
            <th class="text-right">Margem</th>
            <th class="text-right">Marg. mín.</th>
            <th>Status</th>
            <th>Pricing acc.</th>
            <th>Obs</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading && !items.length">
            <td colspan="20" class="text-center py-8 text-muted-foreground">
              <Loader2 class="size-4 inline animate-spin mr-1.5" /> carregando…
            </td>
          </tr>
          <tr v-else-if="!items.length">
            <td colspan="20" class="text-center py-8 text-muted-foreground">
              sem registros
            </td>
          </tr>
          <tr v-for="r in items" :key="r.bling_order_item_id">
            <td class="whitespace-nowrap text-muted-foreground">{{ fmtDate(r.data) }}</td>
            <td class="tabular-nums">
              <div class="font-medium">{{ r.pedido_bling ?? '—' }}</div>
              <div v-if="r.pedido_marketplace" class="text-[10px] text-muted-foreground">{{ r.pedido_marketplace }}</div>
            </td>
            <td class="uppercase text-muted-foreground">
              {{ r.plataforma || '—' }}
              <div v-if="r.bling_listing_type" class="text-[10px] normal-case">{{ r.bling_listing_type }}</div>
            </td>
            <td>{{ r.conta || '—' }}</td>
            <td class="font-mono">{{ r.sku || '—' }}</td>
            <td class="max-w-[260px] truncate" :title="r.produto || ''">{{ r.produto || '—' }}</td>
            <td class="text-right tabular-nums text-muted-foreground">{{ brl(r.custo_produto) }}</td>
            <td class="text-right tabular-nums bg-amber-500/5">{{ brl(r.frete_plataforma) }}</td>
            <td class="text-right tabular-nums bg-amber-500/5">{{ brl(r.frete_anuncio) }}</td>
            <td
              class="text-right tabular-nums bg-amber-500/5 cursor-help"
              :class="r.frete_projetado == null
                ? (!r.pricing_leaf_segment_name ? 'text-red-400' : 'text-amber-400')
                : ''"
              :title="freteProjMissingReason(r) || ''"
            >
              {{ r.frete_projetado != null ? brl(r.frete_projetado) : (!r.pricing_leaf_segment_name ? '⚠️' : '—') }}
            </td>
            <td class="text-right tabular-nums bg-amber-500/5">{{ brl(r.reembolso) }}</td>
            <td
              class="text-right tabular-nums bg-amber-500/5 font-medium"
              :class="r.resultado_frete != null ? (r.resultado_frete >= 0 ? 'text-emerald-500' : 'text-red-500') : ''"
            >
              {{ brl(r.resultado_frete) }}
            </td>
            <td class="text-right tabular-nums bg-amber-500/5">{{ brl(r.saldo_plataforma) }}</td>
            <td class="text-right tabular-nums text-muted-foreground">{{ brl(r.saldo_bling) }}</td>
            <td class="text-right tabular-nums bg-amber-500/5 font-medium">{{ brl(r.saldo_efetivo) }}</td>
            <td
              class="text-right tabular-nums font-medium"
              :class="r.margem != null && r.margem_minima != null
                ? (r.margem >= r.margem_minima ? 'text-emerald-500' : 'text-red-500')
                : (r.margem != null && r.margem >= 0 ? 'text-emerald-500' : 'text-red-500')"
            >
              {{ pct(r.margem) }}
            </td>
            <td class="text-right tabular-nums text-muted-foreground">{{ pct(r.margem_minima) }}</td>
            <td>
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
            <td
              class="text-[10px] cursor-help"
              :class="!r.pricing_account_name && !r.pricing_leaf_segment_name ? 'text-red-400' : ''"
              :title="freteProjMissingReason(r) || ''"
            >
              <template v-if="r.pricing_account_name">
                <div>{{ r.pricing_account_name }}</div>
                <div v-if="r.pricing_leaf_segment_name" class="text-muted-foreground">{{ r.pricing_leaf_segment_name }}</div>
              </template>
              <template v-else-if="!r.pricing_leaf_segment_name">
                <div>⚠️ sem pricing_products</div>
                <div class="text-muted-foreground">cadastrar SKU</div>
              </template>
              <template v-else>
                <div class="text-amber-400">sem account p/ {{ r.pricing_leaf_segment_name }}</div>
              </template>
            </td>
            <td class="max-w-[200px]">
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
