<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  Activity, AlertCircle, BarChart3, Bell, Bot, Clock, Cpu, LayoutGrid,
  RefreshCw, Sparkles, TrendingUp, TrendingDown,
} from 'lucide-vue-next'

const { api } = useApi()

// ── Types ────────────────────────────────────────────────────────────
type Account = {
  id: string
  name: string
  platform: 'shopee' | 'ml' | 'amazon' | string
  department: 'celular' | 'mala' | 'eletro' | string
  acos_target: number
  daily_budget: number | null
  agent_enabled: boolean
  status: 'active' | 'reduced' | 'paused' | 'off' | string
  current_intensity: number
  credit_balance: number | null
  spend_today: number
  revenue_today: number
  impressions_today: number
}
type PeriodCell = {
  credit: number | null
  spend: number
  revenue: number
  impressions: number
  clicks: number
  orders: number
  acos: number | null
  status: string
}
type Summary = {
  accounts: Account[]
  period_1: { days: number; data: Record<string, PeriodCell> }
  period_2: { days: number; data: Record<string, PeriodCell> }
}
type Intensity = {
  account_id: string
  name: string
  platform: string
  department: string
  intensity: number
  assessment: string
  status: string
}
type Decision = {
  id: string
  account_id: string
  account_name: string
  platform: string
  department: string
  timestamp: string
  action: string
  reasoning: string
  market_intensity: number
  in_base_window: boolean
  acos_at_time: number | null
}
type Pattern = {
  id: string
  account_id: string
  pattern_type: string
  description: string
  confidence: number
  active: boolean
  discovered_at: string
}
type Schedule = {
  id: string
  account_id: string
  day_of_week: number
  start_hour: number
  end_hour: number
}
type HeatmapCell = {
  spend: number
  revenue: number
  impressions: number
  acos: number | null
}
type Heatmap = {
  acos_target: number
  cells: Record<string, HeatmapCell>
}
type TimeseriesPoint = {
  day: string
  spend: number
  revenue: number
  impressions: number
  clicks: number
  acos: number | null
}
type Timeseries = {
  days: number
  accounts: Account[]
  series: Record<string, TimeseriesPoint[]>
}
type AgentStatus = {
  platform: string
  running: boolean
  last_decision_at: string | null
  last_decision_minutes_ago: number | null
  decisions_today: number
}
type CreditAlert = {
  account_id: string
  name: string
  department: string
  credit_balance: number
  daily_spend_avg: number
  days_remaining: number
  severity: 'ok' | 'warning' | 'critical'
}
type Campaign = {
  id: string
  account_id: string
  account_name: string
  platform: string
  department: string
  name: string
  status: 'active' | 'reduced' | 'paused' | 'off' | string
  credit: number | null
  spend: number
  revenue: number
  impressions: number
  acos: number | null
  acos_target: number
}

// ── State ────────────────────────────────────────────────────────────
type Tab = 'metricas' | 'campanhas' | 'agentes'
type Dept = 'celular' | 'mala' | 'eletro'
const tab = ref<Tab>('metricas')
const department = ref<Dept>('celular')

const summary = ref<Summary | null>(null)
const intensity = ref<Intensity[]>([])
const decisions = ref<Decision[]>([])
const patterns = ref<Pattern[]>([])
const agentStatus = ref<AgentStatus[]>([])
const creditAlerts = ref<CreditAlert[]>([])
const campaigns = ref<Campaign[]>([])

const schedAccountId = ref<string | null>(null)
const schedules = ref<Schedule[]>([])
const heatmap = ref<Heatmap | null>(null)

const campaignAccountId = ref<string>('all')

const loading = ref(false)
const errorText = ref<string | null>(null)
const triggering = ref(false)

const period1Days = ref(7)
const period2Days = ref(30)

const decFilterPlatform = ref<string>('all')

const days = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']
const platformColor: Record<string, string> = {
  shopee: '#EE4D2D', ml: '#FFD400', amazon: '#FF9900',
}
const platformLabel: Record<string, string> = {
  shopee: 'Shopee', ml: 'Mercado Livre', amazon: 'Amazon',
}
const assessmentBg: Record<string, string> = {
  morto: 'bg-red-500', fraco: 'bg-amber-500', normal: 'bg-emerald-500',
  aquecido: 'bg-sky-500', pico: 'bg-violet-500',
}
const assessmentTxt: Record<string, string> = {
  morto: 'text-red-700', fraco: 'text-amber-700', normal: 'text-emerald-700',
  aquecido: 'text-sky-700', pico: 'text-violet-700',
}
const statusDot: Record<string, string> = {
  active: 'bg-emerald-500', reduced: 'bg-amber-500',
  paused: 'bg-red-500', off: 'bg-zinc-400',
}
const statusLabel: Record<string, string> = {
  active: 'Ativo', reduced: 'Reduzido', paused: 'Pausado', off: 'Desligado',
}
const actionLabel: Record<string, string> = {
  no_action: 'Sem ação', enable_all: 'LIGAR ADS', disable_all: 'DESLIGAR ADS',
  increase_budget: 'AUMENTAR BUDGET', decrease_budget: 'REDUZIR BUDGET',
  pause_worst: 'PAUSAR PIORES',
}

// Marketplace filter for the 7d/30d period tables. 'all' = no filter.
const tablePlatform = ref<'all' | 'shopee' | 'ml' | 'amazon'>('all')

// Chart state
type ChartMetric = 'spend' | 'revenue' | 'impressions' | 'clicks' | 'acos'
const chartMetric = ref<ChartMetric>('spend')
const chartDays = ref<7 | 30>(7)
const timeseries = ref<Timeseries | null>(null)
const chartHidden = ref<Set<string>>(new Set())  // account_ids hidden
const chartHover = ref<{ x: number; y: number; day: string; rows: { id: string; name: string; color: string; val: number | null }[] } | null>(null)

// ── Computeds ────────────────────────────────────────────────────────
const filteredAccounts = computed(() => {
  if (!summary.value) return []
  return summary.value.accounts.filter((a) =>
    a.department === department.value
    && (tablePlatform.value === 'all' || a.platform === tablePlatform.value),
  )
})

const filteredDecisions = computed(() => {
  if (decFilterPlatform.value === 'all') return decisions.value
  return decisions.value.filter((d) => d.platform === decFilterPlatform.value)
})

const selectedSchedAccount = computed(
  () => summary.value?.accounts.find((a) => a.id === schedAccountId.value) ?? null,
)
function scheduleCells(): Set<string> {
  if (!schedAccountId.value) return new Set()
  const s = new Set<string>()
  for (const sch of schedules.value) {
    if (sch.end_hour <= sch.start_hour) {
      for (let h = sch.start_hour; h < 24; h++) s.add(`${sch.day_of_week}-${h}`)
      for (let h = 0; h < sch.end_hour; h++) s.add(`${sch.day_of_week}-${h}`)
    } else {
      for (let h = sch.start_hour; h < sch.end_hour; h++) s.add(`${sch.day_of_week}-${h}`)
    }
  }
  return s
}
const scheduleSet = computed(() => scheduleCells())

const filteredCampaigns = computed(() => {
  let out = campaigns.value.filter((c) => c.department === department.value)
  if (campaignAccountId.value !== 'all') {
    out = out.filter((c) => c.account_id === campaignAccountId.value)
  }
  return out
})

const campaignAccountOptions = computed(() => {
  const accs = filteredAccounts.value
  const byId = new Set(
    campaigns.value
      .filter((c) => c.department === department.value)
      .map((c) => c.account_id),
  )
  return accs.filter((a) => byId.has(a.id))
})

// ── Helpers ──────────────────────────────────────────────────────────
function fmtMoney(v: number | null | undefined): string {
  if (v == null) return '—'
  return v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
}
function fmtPct(v: number | null | undefined): string {
  if (v == null) return '—'
  return `${v.toFixed(1)}%`
}
function fmtTime(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleString('pt-BR', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' })
}
function acosClass(acos: number | null | undefined, target: number): string {
  if (acos == null) return ''
  if (acos < target) return 'text-emerald-600 font-medium'
  if (acos < target * 1.5) return 'text-amber-600 font-medium'
  return 'text-red-600 font-medium'
}

// Deterministic per-account chart color. Groups by platform so the eye
// can read "this batch of bluish lines = ML"; within a platform each
// account picks its own hue via a stable hash.
const _PLATFORM_HUE_BASE: Record<string, number> = {
  amazon: 28,    // oranges
  ml: 50,        // yellows
  shopee: 8,     // reds
}
function accountColor(account: Account): string {
  const base = _PLATFORM_HUE_BASE[account.platform] ?? 200
  // Stable per-name hue offset within ±40°.
  let h = 0
  for (let i = 0; i < account.name.length; i++) h = (h * 31 + account.name.charCodeAt(i)) & 0xffff
  const hue = (base + (h % 80) - 40 + 360) % 360
  return `hsl(${hue} 65% 48%)`
}

// Chart geometry — derived from chartDays + timeseries data + chartMetric.
// Returns null when no series; the template hides the chart in that case.
type ChartPoint = { x: number; y: number; v: number; day: string }
type ChartLine = { id: string; name: string; platform: string; color: string; pts: ChartPoint[] }

function _metricValue(p: TimeseriesPoint, m: ChartMetric): number | null {
  if (m === 'acos') return p.acos
  return p[m] as number
}

const chartLines = computed<ChartLine[]>(() => {
  if (!timeseries.value) return []
  // Filter accounts the user can see (department + marketplace filter
  // mirror the period table) and excludes chips the user toggled off.
  const accs = (timeseries.value.accounts || []).filter(
    (a) => !chartHidden.value.has(a.id)
        && a.department === department.value
        && (tablePlatform.value === 'all' || a.platform === tablePlatform.value),
  )
  // x/y are placeholders here — chartGeom resolves them once it knows
  // the union-of-days and yMax. We drop null/NaN values per day so
  // lines just connect across gaps instead of dipping to zero.
  return accs.map((a) => {
    const raw = timeseries.value!.series[a.id] || []
    const pts: ChartPoint[] = raw
      .map((p) => ({ day: p.day, v: _metricValue(p, chartMetric.value) }))
      .filter((p): p is { day: string; v: number } => p.v !== null && !Number.isNaN(p.v))
      .map((p) => ({ x: 0, y: 0, v: p.v, day: p.day }))
    return {
      id: a.id, name: a.name, platform: a.platform,
      color: accountColor(a), pts,
    }
  }).filter((l) => l.pts.length > 0)
})

// Resolved chart geometry: viewBox 1000×320, padding 40 left / 10 right
// / 10 top / 30 bottom. Pre-computes x/y for each point off the maxes
// across all visible lines so axes are stable as user toggles chips.
const chartGeom = computed(() => {
  const W = 1000, H = 320
  const PAD = { t: 10, r: 10, b: 30, l: 56 }
  const lines = chartLines.value
  if (!lines.length) return { W, H, PAD, lines: [] as ChartLine[], days: [] as string[], yMax: 0 }
  // Union of days across all lines (sorted ASC).
  const dayset = new Set<string>()
  for (const l of lines) for (const p of l.pts) dayset.add(p.day)
  const days = Array.from(dayset).sort()
  const innerW = W - PAD.l - PAD.r
  const innerH = H - PAD.t - PAD.b
  const dx = days.length > 1 ? innerW / (days.length - 1) : 0
  // Max value across all lines for the current metric.
  let yMax = 0
  for (const l of lines) for (const p of l.pts) if (p.v > yMax) yMax = p.v
  if (yMax === 0) yMax = 1
  const sized: ChartLine[] = lines.map((l) => ({
    ...l,
    pts: l.pts.map((p) => {
      const i = days.indexOf(p.day)
      const x = PAD.l + (i >= 0 ? i * dx : 0)
      const y = PAD.t + innerH - (p.v / yMax) * innerH
      return { ...p, x, y }
    }),
  }))
  return { W, H, PAD, lines: sized, days, yMax }
})

function chartPath(line: ChartLine): string {
  if (!line.pts.length) return ''
  return line.pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')
}

function chartFmt(v: number | null): string {
  if (v == null) return '—'
  if (chartMetric.value === 'spend' || chartMetric.value === 'revenue') return fmtMoney(v)
  if (chartMetric.value === 'acos') return fmtPct(v)
  return v.toLocaleString('pt-BR')
}

// Hover: figure out which X column the mouse is over, build the stack
// of (account, value) at that day. SVG client coords → viewBox conversion.
function onChartHover(ev: MouseEvent) {
  const g = chartGeom.value
  if (!g.days.length || !g.lines.length) { chartHover.value = null; return }
  const svg = ev.currentTarget as SVGSVGElement
  const rect = svg.getBoundingClientRect()
  const xRel = ((ev.clientX - rect.left) / rect.width) * g.W
  const innerW = g.W - g.PAD.l - g.PAD.r
  const dx = g.days.length > 1 ? innerW / (g.days.length - 1) : 0
  let idx = Math.round((xRel - g.PAD.l) / Math.max(dx, 1))
  if (idx < 0) idx = 0
  if (idx >= g.days.length) idx = g.days.length - 1
  const day = g.days[idx]
  const rows = g.lines.map((l) => {
    const p = l.pts.find((pp) => pp.day === day)
    return { id: l.id, name: l.name, color: l.color, val: p ? p.v : null }
  })
  chartHover.value = {
    x: g.PAD.l + idx * dx,
    y: 0,
    day,
    rows,
  }
}
function onChartLeave() { chartHover.value = null }

function toggleChartLine(id: string) {
  const s = new Set(chartHidden.value)
  if (s.has(id)) s.delete(id); else s.add(id)
  chartHidden.value = s
}

function heatmapCellClass(dow: number, hour: number): string {
  const on = scheduleSet.value.has(`${dow}-${hour}`)
  const cell = heatmap.value?.cells[`${dow}-${hour}`]
  const target = heatmap.value?.acos_target ?? 7
  if (!on) return 'bg-muted hover:bg-muted-foreground/30'
  if (!cell || cell.acos == null) return 'bg-sky-400/60 hover:bg-sky-500/80'
  if (cell.acos < target) return 'bg-emerald-500/80 hover:bg-emerald-500'
  if (cell.acos < target * 1.5) return 'bg-amber-400/80 hover:bg-amber-500'
  return 'bg-red-500/80 hover:bg-red-600'
}

function heatmapTooltip(dow: number, hour: number): string {
  const dayName = days[dow]
  const cell = heatmap.value?.cells[`${dow}-${hour}`]
  const on = scheduleSet.value.has(`${dow}-${hour}`)
  const lines = [`${dayName} ${hour}:00${on ? ' — ligado' : ' — desligado'}`]
  if (cell) {
    lines.push(`ACOS: ${cell.acos != null ? cell.acos.toFixed(1) + '%' : '—'}`)
    lines.push(`Gasto: ${fmtMoney(cell.spend)}`)
    lines.push(`Faturamento: ${fmtMoney(cell.revenue)}`)
    lines.push(`Impressões: ${cell.impressions.toLocaleString('pt-BR')}`)
  } else {
    lines.push('Sem dados nesse horário')
  }
  return lines.join('\n')
}

// ── Fetchers ─────────────────────────────────────────────────────────
async function loadSummary() {
  const qs = `?period1_days=${period1Days.value}&period2_days=${period2Days.value}&department=${department.value}`
  summary.value = await api<Summary>(`/api/marketing/metrics/summary${qs}`)
  const accs = summary.value.accounts
  if (accs.length > 0 && (!schedAccountId.value || !accs.find((a) => a.id === schedAccountId.value))) {
    schedAccountId.value = accs[0].id
  }
}
async function loadIntensity() {
  intensity.value = await api<Intensity[]>(`/api/marketing/intensity?department=${department.value}`)
}
async function loadDecisions() {
  decisions.value = await api<Decision[]>(`/api/marketing/decisions?limit=20&department=${department.value}`)
}
async function loadPatterns() {
  patterns.value = await api<Pattern[]>('/api/marketing/patterns?active=true')
}
async function loadAgentStatus() {
  agentStatus.value = await api<AgentStatus[]>('/api/marketing/agents/status')
}
async function loadCreditAlerts() {
  creditAlerts.value = await api<CreditAlert[]>('/api/marketing/credit-alerts')
}
async function loadSchedules() {
  if (!schedAccountId.value) return
  schedules.value = await api<Schedule[]>(`/api/marketing/schedules/${schedAccountId.value}`)
}
async function loadHeatmap() {
  if (!schedAccountId.value) {
    heatmap.value = null
    return
  }
  heatmap.value = await api<Heatmap>(`/api/marketing/schedules/${schedAccountId.value}/heatmap`)
}
async function loadCampaigns() {
  campaigns.value = await api<Campaign[]>(`/api/marketing/campaigns?department=${department.value}`)
}
async function loadTimeseries() {
  const params = new URLSearchParams({
    days: String(chartDays.value),
    department: department.value,
  })
  if (tablePlatform.value !== 'all') params.set('platform', tablePlatform.value)
  timeseries.value = await api<Timeseries>(`/api/marketing/timeseries?${params}`)
}

async function refresh() {
  loading.value = true
  errorText.value = null
  try {
    await loadSummary()
    await Promise.all([
      loadIntensity(), loadDecisions(), loadPatterns(),
      loadAgentStatus(), loadCreditAlerts(), loadSchedules(),
      loadHeatmap(), loadCampaigns(), loadTimeseries(),
    ])
  } catch (e: any) {
    errorText.value = e?.data?.detail?.code ?? e?.message ?? 'load_failed'
  } finally {
    loading.value = false
  }
}

async function triggerAll() {
  triggering.value = true
  try {
    await api('/api/marketing/trigger-all', { method: 'POST' })
    await refresh()
  } catch (e: any) {
    errorText.value = e?.data?.detail?.code ?? 'trigger_failed'
  } finally {
    triggering.value = false
  }
}

async function toggleScheduleCell(dow: number, hour: number) {
  if (!schedAccountId.value) return
  const key = `${dow}-${hour}`
  const set = new Set(scheduleSet.value)
  if (set.has(key)) set.delete(key)
  else set.add(key)
  const blocks: { day_of_week: number; start_hour: number; end_hour: number }[] = []
  for (const cell of set) {
    const [d, h] = cell.split('-').map(Number)
    blocks.push({ day_of_week: d, start_hour: h, end_hour: (h + 1) % 24 || 24 })
  }
  schedules.value = await api<Schedule[]>(`/api/marketing/schedules/${schedAccountId.value}`, {
    method: 'PUT',
    body: blocks,
  })
}

let pollTimer: ReturnType<typeof setInterval> | null = null
onMounted(async () => {
  await refresh()
  pollTimer = setInterval(() => {
    Promise.all([loadIntensity(), loadDecisions(), loadAgentStatus()]).catch(() => {})
  }, 30000)
})
onBeforeUnmount(() => {
  if (pollTimer) clearInterval(pollTimer)
})

watch(department, async () => {
  campaignAccountId.value = 'all'
  await Promise.all([
    loadSummary(), loadIntensity(), loadDecisions(),
    loadCampaigns(), loadTimeseries(),
  ])
})
watch([chartDays, tablePlatform], () => {
  loadTimeseries().catch(() => {})
})

watch(schedAccountId, () => {
  loadSchedules().catch(() => {})
  loadHeatmap().catch(() => {})
})

definePageMeta({ middleware: [] })
</script>

<template>
  <div class="space-y-5">
    <!-- Header -->
    <div class="flex flex-wrap items-center gap-3">
      <div class="flex items-center gap-2">
        <BarChart3 class="h-6 w-6 text-primary" />
        <h1 class="text-2xl font-semibold">Marketing</h1>
      </div>
      <button class="rounded-md border px-2 py-1 text-sm hover:bg-muted disabled:opacity-50 inline-flex items-center gap-1"
        :disabled="loading" @click="refresh">
        <RefreshCw class="size-4" :class="{ 'animate-spin': loading }" /> recarregar
      </button>
      <div class="ml-auto flex items-center gap-2">
        <button v-if="(summary?.accounts.length ?? 0) > 0"
          class="rounded-md bg-primary text-primary-foreground px-3 py-1.5 text-sm inline-flex items-center gap-1 disabled:opacity-50"
          :disabled="triggering" @click="triggerAll">
          <Sparkles class="size-4" /> {{ triggering ? 'rodando…' : 'rodar ciclo agora' }}
        </button>
      </div>
    </div>

    <div v-if="errorText" class="rounded border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive flex items-center gap-2">
      <AlertCircle class="size-4" /> {{ errorText }}
    </div>

    <div v-if="(summary?.accounts.length ?? 0) === 0 && !loading"
      class="rounded-md border bg-muted/30 px-6 py-10 text-center text-sm text-muted-foreground">
      Nenhuma conta sincronizada ainda. As integrações Shopee/ML/Amazon com <code>ads_enabled</code> são populadas automaticamente pelo cron.
    </div>

    <!-- Tabs + department -->
    <div v-if="(summary?.accounts.length ?? 0) > 0" class="flex flex-wrap items-center gap-3">
      <div class="flex gap-1 rounded-md bg-muted/40 p-1 w-fit">
        <button v-for="t in (['metricas', 'campanhas', 'agentes'] as const)" :key="t"
          class="px-3 py-1.5 rounded text-sm transition-colors inline-flex items-center gap-1.5"
          :class="tab === t ? 'bg-background shadow-sm font-medium' : 'hover:bg-background/60 text-muted-foreground'"
          @click="tab = t">
          <BarChart3 v-if="t === 'metricas'" class="size-4" />
          <LayoutGrid v-else-if="t === 'campanhas'" class="size-4" />
          <Cpu v-else class="size-4" />
          {{ t === 'metricas' ? 'Métricas' : t === 'campanhas' ? 'Campanhas' : 'Agentes' }}
        </button>
      </div>
      <div class="flex gap-1 rounded-md bg-muted/40 p-1 w-fit">
        <button v-for="d in (['celular', 'mala', 'eletro'] as const)" :key="d"
          class="px-3 py-1 rounded text-sm transition-colors"
          :class="department === d ? 'bg-background shadow-sm font-medium' : 'hover:bg-background/60 text-muted-foreground'"
          @click="department = d">
          {{ d === 'celular' ? 'Celular' : d === 'mala' ? 'Mala' : 'Eletro' }}
        </button>
      </div>
    </div>

    <!-- ═══════════════════════════════ MÉTRICAS ═══════════════════════ -->
    <template v-if="tab === 'metricas' && summary">
      <div class="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <template v-for="(period, idx) in [{ key: 'period_1', days: period1Days }, { key: 'period_2', days: period2Days }] as const" :key="period.key">
          <div class="rounded-md border overflow-hidden">
            <div class="bg-muted/40 px-3 py-2 text-sm font-medium border-b flex items-center justify-between gap-2 flex-wrap">
              <span>Últimos {{ idx === 0 ? period1Days : period2Days }} dias</span>
              <div class="flex items-center gap-3">
                <label v-if="idx === 0" class="text-xs font-normal text-muted-foreground inline-flex items-center gap-1">
                  Plataforma:
                  <select v-model="tablePlatform" class="border rounded-md px-2 py-0.5 text-xs bg-background text-foreground">
                    <option value="all">Todas</option>
                    <option value="amazon">Amazon</option>
                    <option value="ml">Mercado Livre</option>
                    <option value="shopee">Shopee</option>
                  </select>
                </label>
                <label class="text-xs font-normal text-muted-foreground inline-flex items-center gap-1">
                  Dias:
                  <input
                    type="number" min="1" max="365"
                    :value="idx === 0 ? period1Days : period2Days"
                    class="w-16 border rounded px-2 py-0.5 bg-background text-foreground"
                    @change="(e: any) => { const v = parseInt(e.target.value || '7', 10); if (idx === 0) period1Days = v; else period2Days = v; loadSummary(); }"
                  />
                </label>
              </div>
            </div>
            <div class="overflow-x-auto">
              <table class="w-full text-sm">
                <thead class="bg-muted/20 text-left">
                  <tr>
                    <th class="px-2 py-2"></th>
                    <th v-for="a in filteredAccounts" :key="a.id" class="px-2 py-2 whitespace-nowrap min-w-[110px] align-top">
                      <div class="text-[11px] font-semibold" :style="{ color: platformColor[a.platform] }">{{ platformLabel[a.platform] ?? a.platform }}</div>
                      <div class="text-[11px] text-muted-foreground">{{ a.name }}</div>
                      <div class="text-[9px] uppercase tracking-wide text-muted-foreground">{{ a.department }}</div>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr class="border-t"><td class="px-2 py-1 text-muted-foreground">Crédito</td>
                    <td v-for="a in filteredAccounts" :key="a.id" class="px-2 py-1 whitespace-nowrap">
                      {{ a.platform === 'shopee' ? fmtMoney((summary as any)[period.key].data[a.id]?.credit) : '—' }}
                    </td>
                  </tr>
                  <tr class="border-t"><td class="px-2 py-1 text-muted-foreground">Gasto</td>
                    <td v-for="a in filteredAccounts" :key="a.id" class="px-2 py-1 whitespace-nowrap">
                      {{ fmtMoney((summary as any)[period.key].data[a.id]?.spend) }}
                    </td>
                  </tr>
                  <tr class="border-t"><td class="px-2 py-1 text-muted-foreground">Faturamento</td>
                    <td v-for="a in filteredAccounts" :key="a.id" class="px-2 py-1 whitespace-nowrap">
                      {{ fmtMoney((summary as any)[period.key].data[a.id]?.revenue) }}
                    </td>
                  </tr>
                  <tr class="border-t"><td class="px-2 py-1 text-muted-foreground">Impressões</td>
                    <td v-for="a in filteredAccounts" :key="a.id" class="px-2 py-1 whitespace-nowrap text-muted-foreground">
                      {{ ((summary as any)[period.key].data[a.id]?.impressions ?? 0).toLocaleString('pt-BR') }}
                    </td>
                  </tr>
                  <tr class="border-t"><td class="px-2 py-1 text-muted-foreground">ACOS</td>
                    <td v-for="a in filteredAccounts" :key="a.id" class="px-2 py-1 whitespace-nowrap"
                      :class="acosClass((summary as any)[period.key].data[a.id]?.acos, a.acos_target)">
                      {{ fmtPct((summary as any)[period.key].data[a.id]?.acos) }}
                    </td>
                  </tr>
                  <tr class="border-t"><td class="px-2 py-1 text-muted-foreground">Status</td>
                    <td v-for="a in filteredAccounts" :key="a.id" class="px-2 py-1 whitespace-nowrap">
                      <span class="inline-flex items-center gap-1">
                        <span class="size-2 rounded-full" :class="statusDot[a.status] ?? 'bg-zinc-400'" />
                        <span class="text-xs">{{ statusLabel[a.status] ?? a.status }}</span>
                      </span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </template>
      </div>

      <!-- Interactive chart -->
      <div class="rounded-md border p-4">
        <div class="flex items-center gap-3 mb-3 flex-wrap">
          <BarChart3 class="size-4 text-primary" />
          <h2 class="text-lg font-semibold">Evolução</h2>
          <label class="text-xs text-muted-foreground inline-flex items-center gap-1">
            Métrica:
            <select v-model="chartMetric" class="border rounded-md px-2 py-0.5 text-xs bg-background text-foreground">
              <option value="spend">Gasto</option>
              <option value="revenue">Faturamento</option>
              <option value="impressions">Impressões</option>
              <option value="clicks">Cliques</option>
              <option value="acos">ACOS</option>
            </select>
          </label>
          <label class="text-xs text-muted-foreground inline-flex items-center gap-1">
            Janela:
            <select v-model.number="chartDays" class="border rounded-md px-2 py-0.5 text-xs bg-background text-foreground">
              <option :value="7">7 dias</option>
              <option :value="30">30 dias</option>
            </select>
          </label>
          <span class="text-xs text-muted-foreground ml-auto">Clique nos chips abaixo pra mostrar/esconder lojas no gráfico.</span>
        </div>

        <div v-if="!timeseries || chartGeom.lines.length === 0"
             class="text-sm text-muted-foreground py-8 text-center">
          Sem dados pra essa combinação de departamento × plataforma × janela.
        </div>
        <div v-else class="relative">
          <svg :viewBox="`0 0 ${chartGeom.W} ${chartGeom.H}`" class="w-full h-[320px]"
               @mousemove="onChartHover" @mouseleave="onChartLeave">
            <!-- Y axis baseline + max -->
            <line :x1="chartGeom.PAD.l" :x2="chartGeom.W - chartGeom.PAD.r"
                  :y1="chartGeom.H - chartGeom.PAD.b" :y2="chartGeom.H - chartGeom.PAD.b"
                  stroke="currentColor" stroke-opacity="0.2" />
            <line :x1="chartGeom.PAD.l" :x2="chartGeom.W - chartGeom.PAD.r"
                  :y1="chartGeom.PAD.t" :y2="chartGeom.PAD.t"
                  stroke="currentColor" stroke-opacity="0.08" />
            <!-- Y labels -->
            <text :x="chartGeom.PAD.l - 6" :y="chartGeom.PAD.t + 4"
                  text-anchor="end" font-size="11" fill="currentColor" fill-opacity="0.5">
              {{ chartFmt(chartGeom.yMax) }}
            </text>
            <text :x="chartGeom.PAD.l - 6" :y="chartGeom.H - chartGeom.PAD.b + 4"
                  text-anchor="end" font-size="11" fill="currentColor" fill-opacity="0.5">
              {{ chartFmt(0) }}
            </text>
            <!-- X labels (first, mid, last day) -->
            <template v-for="(d, i) in chartGeom.days" :key="d">
              <text v-if="i === 0 || i === chartGeom.days.length - 1 || i === Math.floor((chartGeom.days.length - 1) / 2)"
                    :x="chartGeom.PAD.l + (chartGeom.days.length > 1 ? i * ((chartGeom.W - chartGeom.PAD.l - chartGeom.PAD.r) / (chartGeom.days.length - 1)) : 0)"
                    :y="chartGeom.H - chartGeom.PAD.b + 14"
                    text-anchor="middle" font-size="10" fill="currentColor" fill-opacity="0.5">
                {{ d.slice(5) }}
              </text>
            </template>
            <!-- Lines -->
            <g v-for="line in chartGeom.lines" :key="line.id">
              <path :d="chartPath(line)" :stroke="line.color" stroke-width="2" fill="none" stroke-linejoin="round" stroke-linecap="round" />
              <circle v-for="(p, i) in line.pts" :key="i" :cx="p.x" :cy="p.y" r="2" :fill="line.color" />
            </g>
            <!-- Hover vertical line + tooltip anchor -->
            <line v-if="chartHover"
                  :x1="chartHover.x" :x2="chartHover.x"
                  :y1="chartGeom.PAD.t" :y2="chartGeom.H - chartGeom.PAD.b"
                  stroke="currentColor" stroke-opacity="0.3" stroke-dasharray="3 3" />
          </svg>
          <!-- HTML tooltip overlay (easier formatting than SVG text) -->
          <div v-if="chartHover" class="absolute top-0 pointer-events-none bg-popover border rounded-md shadow-md px-3 py-2 text-xs"
               :style="{ left: `${(chartHover.x / chartGeom.W) * 100}%`, transform: 'translateX(-100%)', marginLeft: '-8px', maxWidth: '260px' }">
            <div class="font-semibold mb-1">{{ chartHover.day }}</div>
            <div v-for="r in chartHover.rows" :key="r.id" class="flex items-center gap-1.5">
              <span class="size-2 rounded-full" :style="{ backgroundColor: r.color }" />
              <span class="truncate">{{ r.name }}</span>
              <span class="ml-auto font-medium">{{ chartFmt(r.val) }}</span>
            </div>
          </div>
        </div>

        <!-- Chips: one per account, click to toggle. -->
        <div v-if="(timeseries?.accounts.length ?? 0) > 0" class="mt-3 flex flex-wrap gap-1.5">
          <button v-for="a in timeseries?.accounts.filter((a) => a.department === department && (tablePlatform === 'all' || a.platform === tablePlatform)) || []"
                  :key="a.id"
                  class="text-[11px] inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border transition-opacity"
                  :class="chartHidden.has(a.id) ? 'opacity-40' : ''"
                  :style="{ borderColor: accountColor(a), color: accountColor(a) }"
                  @click="toggleChartLine(a.id)">
            <span class="size-2 rounded-full" :style="{ backgroundColor: accountColor(a) }" />
            {{ a.name }}
            <span class="text-[9px] uppercase opacity-60">{{ a.platform }}</span>
          </button>
        </div>
      </div>

      <!-- Schedule heatmap (ACOS por hora × dia) -->
      <div class="rounded-md border p-4">
        <div class="flex items-center gap-2 mb-3 flex-wrap">
          <Clock class="size-4 text-primary" />
          <h2 class="text-lg font-semibold">Heatmap ACOS — Horários</h2>
          <span class="text-xs text-muted-foreground">— clique para ligar/desligar</span>
          <select v-if="filteredAccounts.length > 0" v-model="schedAccountId" class="ml-auto border rounded-md px-2 py-1 text-sm bg-background">
            <option v-for="a in filteredAccounts" :key="a.id" :value="a.id">
              {{ a.name }} ({{ platformLabel[a.platform] }} — {{ a.department }})
            </option>
          </select>
        </div>
        <div v-if="selectedSchedAccount" class="overflow-x-auto">
          <table class="text-[10px] border-separate border-spacing-0.5">
            <thead>
              <tr>
                <th class="w-10"></th>
                <th v-for="h in 24" :key="h" class="w-6 text-center text-muted-foreground">{{ h - 1 }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(d, dIdx) in days" :key="d">
                <td class="text-right pr-2 text-muted-foreground font-medium">{{ d }}</td>
                <td v-for="h in 24" :key="h"
                  class="w-6 h-6 rounded cursor-pointer transition-colors"
                  :class="heatmapCellClass(dIdx, h - 1)"
                  :title="heatmapTooltip(dIdx, h - 1)"
                  @click="toggleScheduleCell(dIdx, h - 1)" />
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="selectedSchedAccount" class="mt-3 flex flex-wrap items-center gap-3 text-[11px] text-muted-foreground">
          <span class="inline-flex items-center gap-1"><span class="size-3 rounded bg-emerald-500/80" /> ACOS &lt; {{ (heatmap?.acos_target ?? selectedSchedAccount.acos_target).toFixed(1) }}%</span>
          <span class="inline-flex items-center gap-1"><span class="size-3 rounded bg-amber-400/80" /> ACOS médio (até {{ ((heatmap?.acos_target ?? selectedSchedAccount.acos_target) * 1.5).toFixed(1) }}%)</span>
          <span class="inline-flex items-center gap-1"><span class="size-3 rounded bg-red-500/80" /> ACOS ruim</span>
          <span class="inline-flex items-center gap-1"><span class="size-3 rounded bg-sky-400/60" /> Ligado, sem dados</span>
          <span class="inline-flex items-center gap-1"><span class="size-3 rounded bg-muted border" /> Desligado</span>
        </div>
      </div>

      <!-- Credit alerts -->
      <div class="rounded-md border p-4">
        <div class="flex items-center gap-2 mb-3">
          <Bell class="size-4 text-primary" />
          <h2 class="text-lg font-semibold">Alertas de crédito (Shopee)</h2>
          <span class="text-xs text-muted-foreground">Telegram dispara quando ≤2 dias</span>
        </div>
        <div v-if="creditAlerts.length === 0" class="text-sm text-muted-foreground py-2">
          Nenhuma conta Shopee com saldo + gasto registrado ainda. O cron de Shopee roda a cada 5min em round-robin.
        </div>
        <div v-else class="space-y-1.5">
          <div v-for="a in creditAlerts" :key="a.account_id"
            class="border rounded-md px-3 py-2 text-sm flex items-center gap-3"
            :class="a.severity === 'critical' ? 'border-red-500/50 bg-red-500/5'
              : a.severity === 'warning' ? 'border-amber-500/50 bg-amber-500/5'
              : 'border-emerald-500/30 bg-emerald-500/5'">
            <span class="text-xl">{{ a.severity === 'critical' ? '🚨' : a.severity === 'warning' ? '⚠️' : '✅' }}</span>
            <div class="flex-1">
              <div class="font-medium">{{ a.name }} <span class="text-[10px] text-muted-foreground uppercase">{{ a.department }}</span></div>
              <div class="text-xs text-muted-foreground">
                Crédito {{ fmtMoney(a.credit_balance) }} · gasto médio/dia {{ fmtMoney(a.daily_spend_avg) }}
              </div>
            </div>
            <div class="text-sm font-medium text-right" :class="a.severity === 'critical' ? 'text-red-700' : a.severity === 'warning' ? 'text-amber-700' : 'text-emerald-700'">
              {{ a.days_remaining }} dias
            </div>
          </div>
        </div>
      </div>
    </template>

    <!-- ═══════════════════════════════ CAMPANHAS ══════════════════════ -->
    <template v-if="tab === 'campanhas' && summary">
      <div class="flex flex-wrap items-center gap-3">
        <span class="text-sm text-muted-foreground">Conta:</span>
        <select v-model="campaignAccountId" class="border rounded-md px-2 py-1 text-sm bg-background">
          <option value="all">Todas</option>
          <option v-for="a in campaignAccountOptions" :key="a.id" :value="a.id">
            {{ a.name }} ({{ platformLabel[a.platform] }} — {{ a.department }})
          </option>
        </select>
        <span class="text-xs text-muted-foreground">
          {{ filteredCampaigns.length }} campanha{{ filteredCampaigns.length === 1 ? '' : 's' }}
        </span>
      </div>

      <div v-if="filteredCampaigns.length === 0" class="rounded-md border bg-muted/20 px-4 py-8 text-center text-sm text-muted-foreground">
        Nenhuma campanha pra esse filtro.
      </div>
      <div v-else class="rounded-md border overflow-hidden">
        <div class="overflow-x-auto">
          <table class="text-sm border-collapse min-w-full">
            <thead>
              <tr class="bg-muted/40">
                <th class="sticky left-0 z-10 bg-muted/40 px-3 py-2 text-left text-xs uppercase tracking-wide text-muted-foreground border-r min-w-[140px] align-bottom">Métrica</th>
                <th v-for="c in filteredCampaigns" :key="c.id"
                  class="px-3 py-2 text-left whitespace-nowrap border-r last:border-r-0 min-w-[180px] align-top">
                  <div class="text-[11px] font-semibold" :style="{ color: platformColor[c.platform] }">
                    {{ platformLabel[c.platform] ?? c.platform }}
                  </div>
                  <div class="text-[11px] text-muted-foreground">{{ c.account_name }}</div>
                  <div class="text-[10px] uppercase tracking-wide text-muted-foreground">{{ c.department }}</div>
                  <div class="font-semibold mt-1">{{ c.name }}</div>
                </th>
              </tr>
            </thead>
            <tbody>
              <!-- Crédito (Shopee only; ML/Amazon = "—") -->
              <tr class="border-t hover:bg-muted/10">
                <td class="sticky left-0 z-10 bg-background px-3 py-1.5 text-muted-foreground border-r">Crédito</td>
                <td v-for="c in filteredCampaigns" :key="c.id" class="px-3 py-1.5 whitespace-nowrap border-r last:border-r-0">
                  {{ c.platform === 'shopee' ? fmtMoney(c.credit) : '—' }}
                </td>
              </tr>
              <!-- Gasto -->
              <tr class="border-t hover:bg-muted/10">
                <td class="sticky left-0 z-10 bg-background px-3 py-1.5 text-muted-foreground border-r">Gasto</td>
                <td v-for="c in filteredCampaigns" :key="c.id" class="px-3 py-1.5 whitespace-nowrap border-r last:border-r-0">
                  {{ fmtMoney(c.spend) }}
                </td>
              </tr>
              <!-- Faturamento -->
              <tr class="border-t hover:bg-muted/10">
                <td class="sticky left-0 z-10 bg-background px-3 py-1.5 text-muted-foreground border-r">Faturamento</td>
                <td v-for="c in filteredCampaigns" :key="c.id" class="px-3 py-1.5 whitespace-nowrap border-r last:border-r-0">
                  {{ fmtMoney(c.revenue) }}
                </td>
              </tr>
              <!-- Impressões -->
              <tr class="border-t hover:bg-muted/10">
                <td class="sticky left-0 z-10 bg-background px-3 py-1.5 text-muted-foreground border-r">Impressões</td>
                <td v-for="c in filteredCampaigns" :key="c.id" class="px-3 py-1.5 whitespace-nowrap text-muted-foreground border-r last:border-r-0">
                  {{ c.impressions.toLocaleString('pt-BR') }}
                </td>
              </tr>
              <!-- ACOS -->
              <tr class="border-t hover:bg-muted/10">
                <td class="sticky left-0 z-10 bg-background px-3 py-1.5 text-muted-foreground border-r">ACOS</td>
                <td v-for="c in filteredCampaigns" :key="c.id" class="px-3 py-1.5 whitespace-nowrap border-r last:border-r-0"
                  :class="acosClass(c.acos, c.acos_target)">
                  {{ fmtPct(c.acos) }}
                </td>
              </tr>
              <!-- Status -->
              <tr class="border-t hover:bg-muted/10">
                <td class="sticky left-0 z-10 bg-background px-3 py-1.5 text-muted-foreground border-r">Status</td>
                <td v-for="c in filteredCampaigns" :key="c.id" class="px-3 py-1.5 whitespace-nowrap border-r last:border-r-0">
                  <span class="inline-flex items-center gap-1.5">
                    <span class="size-2 rounded-full" :class="statusDot[c.status] ?? 'bg-zinc-400'" />
                    <span class="text-xs">{{ statusLabel[c.status] ?? c.status }}</span>
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>

    <!-- ═══════════════════════════════ AGENTES ════════════════════════ -->
    <template v-if="tab === 'agentes'">
      <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
        <div v-for="ag in agentStatus" :key="ag.platform"
          class="rounded-md border p-3 flex items-center gap-3">
          <div class="size-10 rounded-full grid place-items-center"
            :style="{ backgroundColor: platformColor[ag.platform] + '20' }">
            <Bot class="size-5" :style="{ color: platformColor[ag.platform] }" />
          </div>
          <div class="flex-1">
            <div class="font-medium">Agente {{ platformLabel[ag.platform] ?? ag.platform }}</div>
            <div class="text-xs text-muted-foreground">
              {{ ag.last_decision_minutes_ago != null ? `última decisão ${ag.last_decision_minutes_ago}min atrás` : 'sem decisões ainda' }}
            </div>
          </div>
          <div class="text-right">
            <div class="inline-flex items-center gap-1 text-xs">
              <span class="size-2 rounded-full" :class="ag.running ? 'bg-emerald-500' : 'bg-zinc-400'" />
              {{ ag.running ? 'Rodando' : 'Parado' }}
            </div>
            <div class="text-[10px] text-muted-foreground mt-0.5">{{ ag.decisions_today }} hoje</div>
          </div>
        </div>
      </div>

      <div v-if="intensity.length > 0" class="rounded-md border p-4">
        <div class="flex items-center gap-2 mb-3">
          <Activity class="size-4 text-primary" />
          <h2 class="text-lg font-semibold">Intensidade do mercado agora</h2>
          <span class="text-[10px] text-muted-foreground">atualiza a cada 30s</span>
        </div>
        <div class="space-y-1.5">
          <div v-for="i in intensity" :key="i.account_id" class="flex items-center gap-3">
            <div class="w-44 text-sm">
              <div class="font-medium">{{ i.name }}</div>
              <div class="text-[10px]" :style="{ color: platformColor[i.platform] }">{{ platformLabel[i.platform] }} — <span class="text-muted-foreground">{{ i.department }}</span></div>
            </div>
            <div class="flex-1 h-5 rounded-full bg-muted overflow-hidden relative">
              <div class="h-full transition-all duration-500" :class="assessmentBg[i.assessment]"
                :style="{ width: `${i.intensity}%` }" />
              <span class="absolute inset-0 flex items-center justify-center text-[11px] font-medium text-foreground/80">{{ i.intensity }}%</span>
            </div>
            <div class="w-24 text-sm font-medium uppercase tracking-wide" :class="assessmentTxt[i.assessment]">{{ i.assessment }}</div>
          </div>
        </div>
      </div>

      <div class="rounded-md border p-4">
        <div class="flex items-center gap-2 mb-3 flex-wrap">
          <Cpu class="size-4 text-primary" />
          <h2 class="text-lg font-semibold">Decisões recentes</h2>
          <select v-model="decFilterPlatform" class="ml-auto border rounded-md px-2 py-1 text-xs bg-background">
            <option value="all">Todas plataformas</option>
            <option value="shopee">Shopee</option>
            <option value="ml">Mercado Livre</option>
            <option value="amazon">Amazon</option>
          </select>
        </div>
        <div v-if="filteredDecisions.length === 0" class="text-sm text-muted-foreground py-2">
          Sem decisões. Clique "rodar ciclo agora" no topo.
        </div>
        <div v-else class="space-y-1.5 max-h-[480px] overflow-y-auto pr-2">
          <div v-for="d in filteredDecisions" :key="d.id" class="border rounded-md p-2.5 text-sm"
            :class="d.action === 'enable_all' || d.action === 'increase_budget' ? 'border-emerald-500/30 bg-emerald-500/5'
              : d.action === 'disable_all' || d.action === 'decrease_budget' || d.action === 'pause_worst' ? 'border-red-500/30 bg-red-500/5'
              : 'border-border'">
            <div class="flex items-center gap-2 flex-wrap text-xs">
              <span class="text-muted-foreground">{{ fmtTime(d.timestamp) }}</span>
              <span class="size-2 rounded-full" :style="{ backgroundColor: platformColor[d.platform] }" />
              <span class="font-medium">{{ d.account_name }}</span>
              <span class="text-muted-foreground uppercase">{{ d.department }}</span>
              <span v-if="d.in_base_window" class="px-1.5 py-0.5 rounded bg-sky-500/10 text-sky-700 border border-sky-500/30 text-[10px]">horário base</span>
              <span class="ml-auto font-medium inline-flex items-center gap-1">
                <TrendingUp v-if="d.action === 'enable_all' || d.action === 'increase_budget'" class="size-3 text-emerald-600" />
                <TrendingDown v-else-if="d.action === 'disable_all' || d.action === 'decrease_budget' || d.action === 'pause_worst'" class="size-3 text-red-600" />
                {{ actionLabel[d.action] ?? d.action }}
              </span>
            </div>
            <div class="mt-1">{{ d.reasoning }}</div>
          </div>
        </div>
      </div>

      <div class="rounded-md border p-4">
        <div class="flex items-center gap-2 mb-3">
          <Sparkles class="size-4 text-primary" />
          <h2 class="text-lg font-semibold">Padrões aprendidos</h2>
        </div>
        <div v-if="patterns.length === 0" class="text-sm text-muted-foreground py-2">
          Nenhum padrão detectado ainda.
        </div>
        <div v-else class="space-y-1.5">
          <div v-for="p in patterns" :key="p.id" class="border rounded-md p-2.5 text-sm">
            <div class="flex items-center justify-between gap-2">
              <span class="text-[10px] uppercase tracking-wide text-muted-foreground">{{ p.pattern_type }}</span>
              <span class="text-xs font-medium" :class="p.confidence > 0.8 ? 'text-emerald-600' : 'text-sky-600'">
                {{ Math.round(p.confidence * 100) }}% confiança
              </span>
            </div>
            <div class="mt-1">{{ p.description }}</div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
