<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { RefreshCw, Activity, AlertCircle, CheckCircle2, Clock } from 'lucide-vue-next'

definePageMeta({ middleware: ['permission'], permission: { resource: 'sincronizacoes', action: 'view' } })

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
  last_heartbeat_at: string | null
  created_at: string
  updated_at: string
}
type JobPage = { items: Job[]; total: number; limit: number; offset: number }
type JobStats = { pending: number; running: number; succeeded: number; failed: number; cancelled: number }

const TYPE_LABELS: Record<string, string> = {
  sync_all: 'sync all',
  sync_product: 'sync produto',
  auto_link: 'auto-link',
  audit: 'auditoria',
  sync_bling_costs: 'custos Bling',
  import_listings: 'import anúncios',
  import_bling_products: 'import produtos Bling',
  push_prices_batch: 'push preços',
  backfill_ml_stock: 'backfill estoque ML',
  refresh_bling_stock: 'estoque Bling',
}

const { api } = useApi()

const items = ref<Job[]>([])
const total = ref(0)
const stats = ref<JobStats>({ pending: 0, running: 0, succeeded: 0, failed: 0, cancelled: 0 })
const loading = ref(false)
const error = ref<string | null>(null)
const filterType = ref<string>('')
const filterStatus = ref<string>('')
const page = ref(1)
const pageSize = 50
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))

const TYPE_OPTIONS = Object.keys(TYPE_LABELS)
const STATUS_OPTIONS = ['pending', 'running', 'succeeded', 'failed', 'cancelled']

async function refresh() {
  loading.value = true
  error.value = null
  try {
    const offset = (page.value - 1) * pageSize
    const params = new URLSearchParams()
    params.set('limit', String(pageSize))
    params.set('offset', String(offset))
    if (filterType.value) params.set('type', filterType.value)
    if (filterStatus.value) params.set('status', filterStatus.value)
    const [pg, st] = await Promise.all([
      api<JobPage>(`/api/jobs?${params.toString()}`),
      api<JobStats>('/api/jobs/stats?window_hours=24'),
    ])
    items.value = pg.items
    total.value = pg.total
    stats.value = st
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    loading.value = false
  }
}

let pollHandle: number | null = null
onMounted(() => {
  refresh()
  pollHandle = window.setInterval(refresh, 5000)
})
onUnmounted(() => {
  if (pollHandle) clearInterval(pollHandle)
})

function fmtType(t: string): string {
  return TYPE_LABELS[t] ?? t
}

function pillClass(s: Job['status']): string {
  if (s === 'running') return 'pill pill-info'
  if (s === 'failed' || s === 'cancelled') return 'pill pill-danger'
  if (s === 'pending') return 'pill pill-warning'
  return 'pill pill-success'
}

function fmtDuration(j: Job): string {
  const a = j.started_at ? new Date(j.started_at).getTime() : null
  const b = j.finished_at ? new Date(j.finished_at).getTime() : null
  if (a == null) return '—'
  const end = b ?? Date.now()
  const ms = Math.max(0, end - a)
  if (ms < 1000) return `${ms}ms`
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  const rs = s % 60
  if (m < 60) return `${m}m ${rs}s`
  const h = Math.floor(m / 60)
  return `${h}h ${m % 60}m`
}

function fmtRelative(iso: string | null): string {
  if (!iso) return '—'
  const t = new Date(iso).getTime()
  const diff = Date.now() - t
  if (diff < 0) return '—'
  const s = Math.floor(diff / 1000)
  if (s < 60) return `${s}s atrás`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m} min atrás`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h atrás`
  return `${Math.floor(h / 24)}d atrás`
}

function fmtPayloadHint(j: Job): string {
  const p = j.payload || {}
  const r = j.result || {}
  if (j.type === 'import_listings') {
    return `integ ${(p.integration_id as string | undefined)?.slice(0, 8) ?? '?'}`
  }
  if (j.type === 'sync_all' && r.total_links != null) {
    return `${r.ok ?? 0} ok · ${r.fatal ?? 0} fatal · ${r.skipped ?? 0} skip`
  }
  if (j.type === 'refresh_bling_stock' && r.updated != null) {
    return `${r.updated} updated · ${r.pages ?? 0} pages`
  }
  if (j.type === 'sync_product' && p.product_id) {
    return `produto ${(p.product_id as string).slice(0, 8)}`
  }
  return ''
}
</script>

<template>
  <div class="space-y-5">
    <PageHeader title="Sincronizações" description="Jobs em execução e histórico recente. Atualiza a cada 5s.">
      <template #actions>
        <Button size="sm" variant="outline" @click="refresh">
          <RefreshCw class="size-4 mr-1.5" :class="loading ? 'animate-spin' : ''" /> recarregar
        </Button>
      </template>
    </PageHeader>

    <div v-if="error" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">
      {{ error }}
    </div>

    <div class="grid grid-cols-2 lg:grid-cols-5 gap-3">
      <StatCard label="OK (24h)" :value="stats.succeeded" :icon="CheckCircle2" tone="success" />
      <StatCard label="Em execução" :value="stats.running" :icon="Activity" />
      <StatCard label="Pendente" :value="stats.pending" :icon="Clock" />
      <StatCard label="Falhou (24h)" :value="stats.failed" :icon="AlertCircle" tone="danger" />
      <StatCard label="Cancelado (24h)" :value="stats.cancelled" :icon="AlertCircle" tone="warning" />
    </div>

    <div class="flex flex-wrap gap-2 items-center">
      <select v-model="filterType" class="h-9 rounded-md border bg-background px-2 text-sm" @change="page = 1; refresh()">
        <option value="">todos os tipos</option>
        <option v-for="t in TYPE_OPTIONS" :key="t" :value="t">{{ fmtType(t) }}</option>
      </select>
      <select v-model="filterStatus" class="h-9 rounded-md border bg-background px-2 text-sm" @change="page = 1; refresh()">
        <option value="">todos os status</option>
        <option v-for="s in STATUS_OPTIONS" :key="s" :value="s">{{ s }}</option>
      </select>
      <span class="ml-auto text-xs text-muted-foreground">
        {{ items.length }} de {{ total }} jobs · pág {{ page }}/{{ totalPages }}
      </span>
    </div>

    <div class="table-card">
      <table class="w-full">
        <thead>
          <tr>
            <th>Tipo</th>
            <th>Detalhe</th>
            <th>Iniciado</th>
            <th>Duração</th>
            <th class="text-right">Progresso</th>
            <th>Status</th>
            <th>Erro</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="j in items" :key="j.id">
            <td class="font-medium">{{ fmtType(j.type) }}</td>
            <td class="text-xs text-muted-foreground">{{ fmtPayloadHint(j) }}</td>
            <td class="text-muted-foreground text-xs">{{ fmtRelative(j.started_at || j.created_at) }}</td>
            <td class="text-muted-foreground text-xs tabular-nums">{{ fmtDuration(j) }}</td>
            <td class="text-right tabular-nums text-xs">
              <span v-if="j.total > 0">{{ j.processed }} / {{ j.total }}</span>
              <span v-else class="text-muted-foreground">—</span>
            </td>
            <td><span :class="pillClass(j.status)">{{ j.status }}</span></td>
            <td class="text-xs text-red-600 max-w-[260px] truncate" :title="j.error || ''">{{ j.error || '' }}</td>
          </tr>
          <tr v-if="items.length === 0 && !loading">
            <td colspan="7" class="py-8 text-center text-sm text-muted-foreground">
              Nenhum job no histórico. Rode "sync all" ou "estoque Bling" em /produtos.
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="flex justify-between items-center text-sm">
      <Button size="sm" variant="outline" :disabled="page <= 1" @click="page--; refresh()">anterior</Button>
      <span>página {{ page }} de {{ totalPages }}</span>
      <Button size="sm" variant="outline" :disabled="page >= totalPages" @click="page++; refresh()">próxima</Button>
    </div>
  </div>
</template>
