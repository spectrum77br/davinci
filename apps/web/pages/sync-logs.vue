<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import {
  RefreshCw,
  Filter,
  Activity,
  CheckCircle2,
  AlertCircle,
  Clock,
  X,
  ChevronLeft,
  ChevronRight,
} from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'sync_logs', action: 'view' },
})

type SyncLog = {
  id: string
  created_at: string
  job_id: string | null
  product_id: string | null
  product_link_id: string | null
  integration_id: string | null
  store_id: string | null
  platform: string | null
  action: string
  status: string
  qty_before: number | null
  qty_after: number | null
  error_code: string | null
  error_detail: string | null
  payload: Record<string, unknown>
}

type Page = { items: SyncLog[]; total: number; limit: number; offset: number }

type Stats = {
  window_hours: number
  ok: number
  skipped: number
  retryable: number
  fatal: number
  requires_review: number
  by_platform: Record<string, Record<string, number>>
}

type Job = {
  id: string
  type: string
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled'
  total: number
  processed: number
  result: Record<string, unknown>
  error: string | null
}

const { api } = useApi()
const canEdit = useCan('sincronizacoes', 'edit')

const items = ref<SyncLog[]>([])
const total = ref(0)
const limit = ref(50)
const offset = ref(0)
const loading = ref(false)
const error = ref<string | null>(null)

const filtroPlatform = ref('')
const filtroStatus = ref('')
const filtroSku = ref('')
const stats = ref<Stats | null>(null)
const drawerLog = ref<SyncLog | null>(null)
const activeJob = ref<Job | null>(null)
let pollHandle: number | null = null

async function load() {
  loading.value = true
  error.value = null
  try {
    const qs = new URLSearchParams()
    qs.set('limit', String(limit.value))
    qs.set('offset', String(offset.value))
    if (filtroPlatform.value) qs.set('platform', filtroPlatform.value)
    if (filtroStatus.value) qs.set('status', filtroStatus.value)
    if (filtroSku.value.trim()) qs.set('sku', filtroSku.value.trim())
    const r = await api<Page>(`/api/sync-logs?${qs}`)
    items.value = r.items
    total.value = r.total
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    loading.value = false
  }
}

async function loadStats() {
  try {
    stats.value = await api<Stats>('/api/sync-logs/stats?window_hours=24')
  } catch {
    /* ignore */
  }
}

async function refreshAll() {
  offset.value = 0
  await Promise.all([load(), loadStats()])
}

async function startSyncAll() {
  activeJob.value = null
  const r = await api<{ job_id: string }>('/api/jobs/sync-all', {
    method: 'POST',
    body: { integration_ids: null, product_ids: null, include_all_stock: true },
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

function statusPill(s: string) {
  if (s === 'ok') return 'pill-success'
  if (s === 'skipped') return 'pill-muted'
  if (s === 'retryable') return 'pill-warning'
  if (s === 'requires_review') return 'pill-warning'
  return 'pill-danger'
}

function fmtDate(iso: string) {
  const d = new Date(iso)
  return d.toLocaleString('pt-BR', { hour12: false })
}

function pageNext() {
  if (offset.value + limit.value < total.value) {
    offset.value += limit.value
    void load()
  }
}
function pagePrev() {
  if (offset.value > 0) {
    offset.value = Math.max(0, offset.value - limit.value)
    void load()
  }
}

const totalsTone = computed(() => {
  if (!stats.value) return { fatal: 0, ok: 0, retryable: 0, skipped: 0, requires_review: 0 }
  return stats.value
})

onMounted(() => {
  void refreshAll()
})
onUnmounted(() => {
  if (pollHandle) clearInterval(pollHandle)
})
</script>

<template>
  <div class="space-y-5">
    <PageHeader
      title="Sync logs"
      description="Histórico de sincronizações Bling ↔ marketplaces. Filtros por canal, status, SKU."
    >
      <template #actions>
        <Button v-if="canEdit" size="sm" @click="startSyncAll">
          <RefreshCw class="size-4 mr-1.5" /> sync all
        </Button>
        <Button size="sm" variant="outline" @click="refreshAll">
          <RefreshCw class="size-4 mr-1.5" /> recarregar
        </Button>
      </template>
    </PageHeader>

    <div v-if="error" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">
      {{ error }}
    </div>

    <div v-if="activeJob" class="rounded-md border bg-muted/40 px-3 py-2 text-sm">
      Job <code>{{ activeJob.id.slice(0, 8) }}</code> — {{ activeJob.status }}
      ({{ activeJob.processed }}/{{ activeJob.total || '?' }})
      <span v-if="activeJob.error" class="text-red-600 ml-2">{{ activeJob.error }}</span>
    </div>

    <div class="grid grid-cols-2 lg:grid-cols-5 gap-3">
      <StatCard label="OK (24h)" :value="totalsTone.ok" :icon="CheckCircle2" tone="success" />
      <StatCard label="Skipped" :value="totalsTone.skipped" :icon="Clock" />
      <StatCard label="Retryable" :value="totalsTone.retryable" :icon="Activity" tone="warning" />
      <StatCard label="Fatal" :value="totalsTone.fatal" :icon="AlertCircle" tone="danger" />
      <StatCard label="Review" :value="totalsTone.requires_review" :icon="AlertCircle" tone="warning" />
    </div>

    <div class="flex flex-wrap gap-2 items-center">
      <select v-model="filtroPlatform" class="h-9 rounded-md border bg-background px-2 text-sm" @change="refreshAll">
        <option value="">todas plataformas</option>
        <option value="bling">bling</option>
        <option value="ml">ml</option>
        <option value="shopee">shopee</option>
        <option value="amazon">amazon</option>
      </select>
      <select v-model="filtroStatus" class="h-9 rounded-md border bg-background px-2 text-sm" @change="refreshAll">
        <option value="">todos status</option>
        <option value="ok">ok</option>
        <option value="skipped">skipped</option>
        <option value="retryable">retryable</option>
        <option value="fatal">fatal</option>
        <option value="requires_review">requires_review</option>
      </select>
      <Input v-model="filtroSku" placeholder="SKU…" class="w-48" @keyup.enter="refreshAll" />
      <Button size="sm" variant="outline" @click="refreshAll">
        <Filter class="size-4 mr-1.5" /> aplicar
      </Button>
    </div>

    <div class="table-card">
      <table class="w-full">
        <thead>
          <tr>
            <th>Quando</th>
            <th>Plataforma</th>
            <th>Ação</th>
            <th>Status</th>
            <th class="text-right">Antes</th>
            <th class="text-right">Depois</th>
            <th>Erro</th>
            <th class="text-right">Detalhes</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading && items.length === 0">
            <td colspan="8" class="text-center text-muted-foreground py-6">carregando…</td>
          </tr>
          <tr v-else-if="items.length === 0">
            <td colspan="8" class="text-center text-muted-foreground py-6">sem registros</td>
          </tr>
          <tr v-for="row in items" :key="row.id" class="hover:bg-muted/40 cursor-pointer" @click="drawerLog = row">
            <td class="text-xs text-muted-foreground tabular-nums">{{ fmtDate(row.created_at) }}</td>
            <td><span class="pill pill-muted">{{ row.platform || '—' }}</span></td>
            <td class="text-xs">{{ row.action }}</td>
            <td><span :class="statusPill(row.status)">{{ row.status }}</span></td>
            <td class="text-right tabular-nums">{{ row.qty_before ?? '—' }}</td>
            <td class="text-right tabular-nums">{{ row.qty_after ?? '—' }}</td>
            <td class="text-xs text-red-600 truncate max-w-[20rem]">{{ row.error_code || '' }}</td>
            <td class="text-right">
              <ChevronRight class="inline size-4 text-muted-foreground" />
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="flex items-center justify-between text-sm text-muted-foreground">
      <div>{{ total }} registros</div>
      <div class="flex items-center gap-1">
        <Button size="sm" variant="ghost" :disabled="offset === 0" @click="pagePrev">
          <ChevronLeft class="size-4" />
        </Button>
        <span class="tabular-nums">
          {{ offset + 1 }}–{{ Math.min(offset + limit, total) }}
        </span>
        <Button
          size="sm"
          variant="ghost"
          :disabled="offset + limit >= total"
          @click="pageNext"
        >
          <ChevronRight class="size-4" />
        </Button>
      </div>
    </div>

    <!-- drawer ---------------------------------------------------------- -->
    <div
      v-if="drawerLog"
      class="fixed inset-0 z-50 flex justify-end bg-black/40"
      @click.self="drawerLog = null"
    >
      <div class="w-full max-w-md bg-background border-l shadow-xl overflow-y-auto p-4 space-y-3">
        <div class="flex items-center justify-between">
          <h3 class="font-semibold">Detalhe do log</h3>
          <Button size="sm" variant="ghost" class="h-7 w-7 p-0" @click="drawerLog = null">
            <X class="size-4" />
          </Button>
        </div>
        <dl class="text-sm space-y-2">
          <div class="flex justify-between"><dt class="text-muted-foreground">id</dt><dd class="font-mono text-xs">{{ drawerLog.id }}</dd></div>
          <div class="flex justify-between"><dt class="text-muted-foreground">quando</dt><dd>{{ fmtDate(drawerLog.created_at) }}</dd></div>
          <div class="flex justify-between"><dt class="text-muted-foreground">plataforma</dt><dd>{{ drawerLog.platform || '—' }}</dd></div>
          <div class="flex justify-between"><dt class="text-muted-foreground">ação</dt><dd>{{ drawerLog.action }}</dd></div>
          <div class="flex justify-between"><dt class="text-muted-foreground">status</dt><dd><span :class="statusPill(drawerLog.status)">{{ drawerLog.status }}</span></dd></div>
          <div class="flex justify-between"><dt class="text-muted-foreground">qty antes</dt><dd class="tabular-nums">{{ drawerLog.qty_before ?? '—' }}</dd></div>
          <div class="flex justify-between"><dt class="text-muted-foreground">qty depois</dt><dd class="tabular-nums">{{ drawerLog.qty_after ?? '—' }}</dd></div>
          <div v-if="drawerLog.error_code" class="space-y-1">
            <dt class="text-muted-foreground">erro</dt>
            <dd class="text-red-700 text-xs font-mono">{{ drawerLog.error_code }}</dd>
            <dd v-if="drawerLog.error_detail" class="text-xs whitespace-pre-wrap break-words bg-red-50 border border-red-200 rounded px-2 py-1">{{ drawerLog.error_detail }}</dd>
          </div>
          <div v-if="drawerLog.product_id" class="flex justify-between">
            <dt class="text-muted-foreground">product_id</dt>
            <dd class="font-mono text-xs">{{ drawerLog.product_id.slice(0, 8) }}</dd>
          </div>
          <div v-if="drawerLog.payload && Object.keys(drawerLog.payload).length" class="space-y-1">
            <dt class="text-muted-foreground">payload</dt>
            <dd>
              <pre class="text-xs bg-muted/50 rounded px-2 py-1 overflow-x-auto">{{ JSON.stringify(drawerLog.payload, null, 2) }}</pre>
            </dd>
          </div>
        </dl>
      </div>
    </div>
  </div>
</template>
