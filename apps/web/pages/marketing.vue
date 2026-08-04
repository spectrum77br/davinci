<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  AlertCircle, BarChart3, Bell, Bot, Clapperboard, Clock,
  Pause, Play, RefreshCw, Sparkles,
} from 'lucide-vue-next'

const { api } = useApi()
const { success: toastOk, error: toastErr } = useToasts()

// Permissões: "marketing" = dashboards de Ads; "marketing_criativos" = aba
// Criativos. Quem só tem criativos não carrega (nem enxerga) nada de Ads.
const canAds = useCan('marketing', 'view')
const canCriativos = useCan('marketing_criativos', 'view')

// ── Types ────────────────────────────────────────────────────────────
type Account = {
  id: string
  name: string
  platform: 'shopee' | 'ml' | 'amazon' | string
  department: 'celular' | 'mala' | 'eletro' | string
  acos_target: number
  daily_budget: number | null
  agent_enabled: boolean
  flash_duplicate_enabled: boolean
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
  external_id: string | null
  status: 'active' | 'reduced' | 'paused' | 'off' | string
  credit: number | null
  spend: number
  revenue: number
  impressions: number
  acos: number | null
  acos_target: number
}
type Command = {
  id: string
  account_id: string
  campaign_external_id: string | null
  platform: string
  action: string
  payload: Record<string, unknown>
  status: 'pending' | 'claimed' | 'done' | 'failed' | string
  result: string | null
  attempts: number
  source: string
  executor: 'api' | 'browser' | string
  created_at: string
  completed_at: string | null
}
type ScheduleState = {
  state: 'on' | 'off'
  schedule_enabled: boolean
  override_action: 'pause' | 'resume' | null
  override_until: string | null
  override_active: boolean
  next_transition: string | null
  next_state: 'on' | 'off' | null
}
// Presence of the LOCAL browser executor (marionete) that drives Shopee via
// AdsPower. `online` = a heartbeat within the last ~120s (server-computed).
type AgentPresence = {
  online: boolean
  agent_name: string | null
  last_seen_at: string | null
  age_seconds?: number
  adspower_ok: boolean | null
  accounts_online: number | null
  info: Record<string, unknown>
}

// ── State ────────────────────────────────────────────────────────────
// The page is organised by marketplace — only Mercado Livre + Shopee are
// surfaced. The platform tab replaces the old mode + department tabs.
type Platform = 'ml' | 'shopee' | 'criativos'
const platform = ref<Platform>('ml')

const summary = ref<Summary | null>(null)
const agentPresence = ref<AgentPresence | null>(null)
const creditAlerts = ref<CreditAlert[]>([])
// Current desired-state of the schedule for the account selected in the
// heatmap/agenda panel (Shopee only).
const scheduleState = ref<ScheduleState | null>(null)
const scheduleBusy = ref(false)
// Oferta Relâmpago (Shopee flash-sale): botão "Duplicar agora".
const flashBusy = ref(false)

const schedAccountId = ref<string | null>(null)
const schedules = ref<Schedule[]>([])
const heatmap = ref<Heatmap | null>(null)

const loading = ref(false)
const errorText = ref<string | null>(null)
const triggering = ref(false)

const period1Days = ref(7)
const period2Days = ref(30)

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

// Command status → badge styling for the Campanhas action feedback.
const cmdBadge: Record<string, { label: string; cls: string }> = {
  pending: { label: 'Pendente', cls: 'bg-amber-500/15 text-amber-700 border-amber-500/30' },
  claimed: { label: 'Executando', cls: 'bg-sky-500/15 text-sky-700 border-sky-500/30' },
  done: { label: 'Feito', cls: 'bg-emerald-500/15 text-emerald-700 border-emerald-500/30' },
  failed: { label: 'Erro', cls: 'bg-red-500/15 text-red-700 border-red-500/30' },
}
const cmdActionLabel: Record<string, string> = {
  pause: 'Pausar', resume: 'Retomar',
  set_budget: 'Budget', adjust_budget_pct: 'Ajustar budget',
  flash_duplicate: 'Duplicar Oferta',
}

// Chart state
type ChartMetric = 'spend' | 'revenue' | 'impressions' | 'clicks' | 'acos'
const chartMetric = ref<ChartMetric>('spend')
const chartDays = ref<7 | 30>(7)
const timeseries = ref<Timeseries | null>(null)
// account_ids selected (=lines visible). Default empty → chart shows
// "select accounts" prompt until the user picks at least one.
const chartSelected = ref<Set<string>>(new Set())
const chartHover = ref<{ x: number; y: number; day: string; rows: { id: string; name: string; color: string; val: number | null }[] } | null>(null)

// ── Computeds ────────────────────────────────────────────────────────
const filteredAccounts = computed(() => {
  if (!summary.value) return []
  return summary.value.accounts.filter((a) => a.platform === platform.value)
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

// Deterministic per-account chart color. Hue base per platform keeps
// the eye reading "blues = ML, oranges = Amazon, reds = Shopee" without
// a separate legend; the per-name hash gives each account inside a
// platform a slightly different shade so they don't all look the same.
// Spread is kept tight (±25°) so platforms stay visually distinct.
// Distinct categorical color per account. Golden-angle hue rotation (137.5°)
// spaces consecutive accounts far apart on the color wheel; 3 alternating
// sat/lightness tiers break ties once the hue wraps — so even 20+ accounts on
// one chart stay visually distinct (the old scheme tied hue to the platform
// with only a ±25° spread, making every ML line a near-identical blue).
function _distinctColor(i: number): string {
  const hue = (i * 137.508) % 360
  const tier = i % 3
  const sat = [72, 85, 62][tier]
  const light = [50, 40, 56][tier]
  return `hsl(${hue.toFixed(1)} ${sat}% ${light}%)`
}
// account_id → color, assigned per platform in a stable name order so the same
// account always gets the same color in both the chart line and the legend.
const accountColorMap = computed<Map<string, string>>(() => {
  const map = new Map<string, string>()
  const byPlat = new Map<string, Account[]>()
  for (const a of summary.value?.accounts ?? []) {
    const list = byPlat.get(a.platform) ?? []
    list.push(a)
    byPlat.set(a.platform, list)
  }
  for (const list of byPlat.values()) {
    list
      .slice()
      .sort((x, y) => x.name.localeCompare(y.name, 'pt-BR'))
      .forEach((a, i) => map.set(a.id, _distinctColor(i)))
  }
  return map
})
function accountColor(account: Account): string {
  return accountColorMap.value.get(account.id) ?? _distinctColor(0)
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
  // Lines are the accounts the user explicitly checked. Department +
  // marketplace filter still apply so the chart respects the page's
  // current view scope.
  const accs = (timeseries.value.accounts || []).filter(
    (a) => chartSelected.value.has(a.id) && a.platform === platform.value,
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
  const s = new Set(chartSelected.value)
  if (s.has(id)) s.delete(id); else s.add(id)
  chartSelected.value = s
}

// Grouped account list for the sidebar — same scope filters as the
// chart itself, then bucketed by platform with a stable display order.
const chartSidebarGroups = computed(() => {
  const out: { platform: string; label: string; accounts: Account[] }[] = []
  const accs = (timeseries.value?.accounts || []).filter(
    (a) => a.platform === platform.value,
  )
  const groups: Record<string, Account[]> = { amazon: [], ml: [], shopee: [] }
  for (const a of accs) {
    if (!(a.platform in groups)) groups[a.platform] = []
    groups[a.platform].push(a)
  }
  for (const p of ['amazon', 'ml', 'shopee']) {
    if (groups[p]?.length) {
      out.push({
        platform: p,
        label: platformLabel[p] ?? p,
        accounts: groups[p].sort((x, y) => x.name.localeCompare(y.name)),
      })
    }
  }
  return out
})

const chartAllVisible = computed(() =>
  chartSidebarGroups.value.flatMap((g) => g.accounts.map((a) => a.id)),
)
const chartAllSelected = computed(() =>
  chartAllVisible.value.length > 0
    && chartAllVisible.value.every((id) => chartSelected.value.has(id)),
)

function toggleChartAll() {
  if (chartAllSelected.value) {
    // Deselect all currently-visible accounts (keep selections from
    // other depts/platforms intact so switching scope doesn't wipe them).
    const next = new Set(chartSelected.value)
    for (const id of chartAllVisible.value) next.delete(id)
    chartSelected.value = next
  } else {
    const next = new Set(chartSelected.value)
    for (const id of chartAllVisible.value) next.add(id)
    chartSelected.value = next
  }
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
  const qs = `?period1_days=${period1Days.value}&period2_days=${period2Days.value}&platform=${platform.value}`
  summary.value = await api<Summary>(`/api/marketing/metrics/summary${qs}`)
  const accs = summary.value.accounts
  if (accs.length > 0 && (!schedAccountId.value || !accs.find((a) => a.id === schedAccountId.value))) {
    schedAccountId.value = accs[0].id
  }
}
async function loadAgentPresence() {
  agentPresence.value = await api<AgentPresence>('/api/marketing/agent/status')
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
async function loadTimeseries() {
  const params = new URLSearchParams({
    days: String(chartDays.value),
    platform: platform.value,
  })
  timeseries.value = await api<Timeseries>(`/api/marketing/timeseries?${params}`)
}

// ── Time formatter (BRT) — used by the executor badge + agenda hint ──
function fmtHm(iso: string | null): string {
  if (!iso) return ''
  return new Date(iso).toLocaleTimeString('pt-BR', {
    timeZone: 'America/Sao_Paulo', hour: '2-digit', minute: '2-digit',
  })
}

// ── Schedule (automatic agenda) ──────────────────────────────────────
async function loadScheduleState() {
  if (!schedAccountId.value) {
    scheduleState.value = null
    return
  }
  scheduleState.value = await api<ScheduleState>(`/api/marketing/accounts/${schedAccountId.value}/schedule`)
}

async function patchSchedule(body: Record<string, unknown>) {
  if (!schedAccountId.value) return
  scheduleBusy.value = true
  try {
    scheduleState.value = await api<ScheduleState>(
      `/api/marketing/accounts/${schedAccountId.value}/schedule`,
      { method: 'PATCH', body },
    )
  } catch (e: any) {
    toastErr('Falha na agenda', e?.data?.detail?.code ?? e?.message ?? 'erro')
  } finally {
    scheduleBusy.value = false
  }
}

async function toggleScheduleAuto() {
  const next = !(scheduleState.value?.schedule_enabled)
  await patchSchedule({ schedule_enabled: next })
  toastOk(next ? 'Agenda automática ligada' : 'Agenda automática desligada')
}
async function overrideNow(action: 'pause' | 'resume') {
  await patchSchedule({ override_action: action })
  toastOk(action === 'pause' ? 'Pausado (override manual)' : 'Ligado (override manual)')
}
async function clearOverride() {
  await patchSchedule({ override_action: null })
  toastOk('Voltou ao automático')
}

// ── Oferta Relâmpago (Shopee flash-sale) ─────────────────────────────
// Liga/desliga a duplicação diária (01:00 BRT). O estado mora no account
// (flash_duplicate_enabled), então recarrego o summary depois do PATCH.
async function toggleFlashDuplicate() {
  const next = !(selectedSchedAccount.value?.flash_duplicate_enabled)
  await patchSchedule({ flash_duplicate_enabled: next })
  await loadSummary().catch(() => {})
  toastOk(next
    ? 'Duplicação da Oferta Relâmpago ligada (01:00)'
    : 'Duplicação da Oferta Relâmpago desligada')
}

// "Duplicar agora": enfileira um comando flash_duplicate de conta inteira
// (commit=true). O executor local só CRIA de verdade se SELECTORS_CALIBRATED
// =true no .env dele — senão recusa o Confirmar final (nada criado).
async function duplicateFlashNow() {
  const acc = selectedSchedAccount.value
  if (!acc) return
  flashBusy.value = true
  try {
    await api(`/api/marketing/accounts/${acc.id}/commands`, {
      method: 'POST',
      body: { action: 'flash_duplicate', payload: { commit: true } },
    })
    toastOk('Duplicação enfileirada', `Oferta Relâmpago — ${acc.name}`)
  } catch (e: any) {
    toastErr('Falha ao enfileirar', e?.data?.detail?.code ?? e?.message ?? 'erro')
  } finally {
    flashBusy.value = false
  }
}

// "Agora: LIGADO até 15:00" / "DESLIGADO — religa 19:00"
const scheduleHint = computed(() => {
  const s = scheduleState.value
  if (!s) return ''
  const on = s.state === 'on'
  const when = s.next_transition ? fmtHm(s.next_transition) : null
  if (on) return when ? `LIGADO até ${when}` : 'LIGADO'
  return when ? `DESLIGADO — religa ${when}` : 'DESLIGADO'
})

// Badge for the LOCAL browser executor (marionete). ONLINE means it sent a
// heartbeat in the last ~120s and is polling the outbox — i.e. scheduled
// Shopee pause/resume will actually run on the Mac.
const executorBadge = computed(() => {
  const p = agentPresence.value
  const seen = p?.last_seen_at ? fmtHm(p.last_seen_at) : null
  if (!p || !p.online) {
    return {
      online: false,
      label: 'Executor local: OFFLINE',
      title: seen ? `Última vez visto às ${seen}` : 'Nunca reportou',
    }
  }
  const bits: string[] = []
  if (p.adspower_ok != null) bits.push(`AdsPower ${p.adspower_ok ? 'ok' : 'falha'}`)
  if (p.accounts_online != null) bits.push(`${p.accounts_online} contas`)
  if (seen) bits.push(`visto ${seen}`)
  return {
    online: true,
    label: 'Executor local: ONLINE',
    title: bits.join(' · ') || 'Online',
  }
})

async function refresh() {
  if (!canAds.value) return
  loading.value = true
  errorText.value = null
  try {
    await loadSummary()
    await Promise.all([
      loadAgentPresence(), loadCreditAlerts(), loadSchedules(),
      loadHeatmap(), loadTimeseries(), loadScheduleState(),
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
  if (!canAds.value && !canCriativos.value) {
    await navigateTo('/403')
    return
  }
  if (!canAds.value) {
    // Usuário só de Criativos: pula todo o carregamento/polling de Ads.
    platform.value = 'criativos'
    return
  }
  await refresh()
  pollTimer = setInterval(() => {
    Promise.all([
      loadAgentPresence(), loadCreditAlerts(),
    ]).catch(() => {})
  }, 30000)
})
onBeforeUnmount(() => {
  if (pollTimer) clearInterval(pollTimer)
})

watch(platform, async () => {
  if (platform.value === 'criativos' || !canAds.value) return
  await Promise.all([
    loadSummary(), loadCreditAlerts(), loadTimeseries(),
  ])
})
watch(chartDays, () => {
  loadTimeseries().catch(() => {})
})

watch(schedAccountId, () => {
  loadSchedules().catch(() => {})
  loadHeatmap().catch(() => {})
  loadScheduleState().catch(() => {})
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
      <button v-if="canAds && platform !== 'criativos'"
        class="rounded-md border px-2 py-1 text-sm hover:bg-muted disabled:opacity-50 inline-flex items-center gap-1"
        :disabled="loading" @click="refresh">
        <RefreshCw class="size-4" :class="{ 'animate-spin': loading }" /> recarregar
      </button>
      <div v-if="canAds" class="ml-auto flex items-center gap-2">
        <span
          class="rounded-md border px-2 py-1 text-xs inline-flex items-center gap-1.5"
          :class="executorBadge.online
            ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
            : 'border-muted-foreground/30 bg-muted/40 text-muted-foreground'"
          :title="executorBadge.title">
          <Bot class="size-3.5" />
          <span class="inline-block size-1.5 rounded-full"
            :class="executorBadge.online ? 'bg-emerald-500' : 'bg-muted-foreground/50'" />
          {{ executorBadge.label }}
        </span>
        <button v-if="(summary?.accounts.length ?? 0) > 0"
          class="rounded-md bg-primary text-primary-foreground px-3 py-1.5 text-sm inline-flex items-center gap-1 disabled:opacity-50"
          :disabled="triggering" @click="triggerAll">
          <Sparkles class="size-4" /> {{ triggering ? 'rodando…' : 'rodar ciclo agora' }}
        </button>
      </div>
    </div>

    <div v-if="errorText && platform !== 'criativos'" class="rounded border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive flex items-center gap-2">
      <AlertCircle class="size-4" /> {{ errorText }}
    </div>

    <div v-if="canAds && platform !== 'criativos' && (summary?.accounts.length ?? 0) === 0 && !loading"
      class="rounded-md border bg-muted/30 px-6 py-10 text-center text-sm text-muted-foreground">
      Nenhuma conta sincronizada ainda. As integrações Shopee/ML/Amazon com <code>ads_enabled</code> são populadas automaticamente pelo cron.
    </div>

    <!-- Abas: Mercado Livre | Shopee (Ads) + Criativos. Cada uma aparece
         conforme a permissão do usuário (marketing / marketing_criativos). -->
    <div v-if="(canAds && (summary?.accounts.length ?? 0) > 0) || canCriativos" class="flex flex-wrap items-center gap-3">
      <div class="flex gap-1 rounded-md bg-muted/40 p-1 w-fit">
        <template v-if="canAds && (summary?.accounts.length ?? 0) > 0">
          <button v-for="p in (['ml', 'shopee'] as const)" :key="p"
            class="px-4 py-1.5 rounded text-sm transition-colors inline-flex items-center gap-1.5"
            :class="platform === p ? 'bg-background shadow-sm font-medium' : 'hover:bg-background/60 text-muted-foreground'"
            @click="platform = p">
            <span class="inline-block size-2 rounded-full" :style="{ background: platformColor[p] }" />
            {{ platformLabel[p] ?? p }}
          </button>
        </template>
        <button v-if="canCriativos"
          class="px-4 py-1.5 rounded text-sm transition-colors inline-flex items-center gap-1.5"
          :class="platform === 'criativos' ? 'bg-background shadow-sm font-medium' : 'hover:bg-background/60 text-muted-foreground'"
          @click="platform = 'criativos'">
          <Clapperboard class="size-3.5" />
          Criativos
        </button>
      </div>
    </div>

    <!-- ═══════════════════════════════ CRIATIVOS ══════════════════════ -->
    <MarketingCriativos v-if="platform === 'criativos' && canCriativos" />

    <!-- ═══════════════════════════════ MÉTRICAS ═══════════════════════ -->
    <template v-if="platform !== 'criativos' && summary">
      <div class="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <template v-for="(period, idx) in [{ key: 'period_1', days: period1Days }, { key: 'period_2', days: period2Days }] as const" :key="period.key">
          <div class="rounded-md border overflow-hidden">
            <div class="bg-muted/40 px-3 py-2 text-sm font-medium border-b flex items-center justify-between gap-2 flex-wrap">
              <span>Últimos {{ idx === 0 ? period1Days : period2Days }} dias</span>
              <div class="flex items-center gap-3">
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
                      <div class="text-[12px] font-semibold text-foreground">{{ a.name }}</div>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-if="platform === 'shopee'" class="border-t"><td class="px-2 py-1 text-muted-foreground">Crédito</td>
                    <td v-for="a in filteredAccounts" :key="a.id" class="px-2 py-1 whitespace-nowrap">
                      {{ fmtMoney((summary as any)[period.key].data[a.id]?.credit) }}
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
        </div>

        <!-- Chart + side panel of checkboxes. Stack vertically on small
             screens so mobile readers don't have to scroll horizontally. -->
        <div class="flex flex-col lg:flex-row gap-4">
          <!-- Chart -->
          <div class="flex-1 min-w-0">
            <div v-if="chartSelected.size === 0"
                 class="text-sm text-muted-foreground py-12 text-center border border-dashed rounded-md">
              Selecione uma ou mais contas na lista ao lado para ver a evolução.
            </div>
            <div v-else-if="!timeseries || chartGeom.lines.length === 0"
                 class="text-sm text-muted-foreground py-12 text-center">
              Sem dados pra essa combinação de contas × janela.
            </div>
            <div v-else class="relative">
              <svg :viewBox="`0 0 ${chartGeom.W} ${chartGeom.H}`" class="w-full h-[320px]"
                   @mousemove="onChartHover" @mouseleave="onChartLeave">
                <line :x1="chartGeom.PAD.l" :x2="chartGeom.W - chartGeom.PAD.r"
                      :y1="chartGeom.H - chartGeom.PAD.b" :y2="chartGeom.H - chartGeom.PAD.b"
                      stroke="currentColor" stroke-opacity="0.2" />
                <line :x1="chartGeom.PAD.l" :x2="chartGeom.W - chartGeom.PAD.r"
                      :y1="chartGeom.PAD.t" :y2="chartGeom.PAD.t"
                      stroke="currentColor" stroke-opacity="0.08" />
                <text :x="chartGeom.PAD.l - 6" :y="chartGeom.PAD.t + 4"
                      text-anchor="end" font-size="11" fill="currentColor" fill-opacity="0.5">
                  {{ chartFmt(chartGeom.yMax) }}
                </text>
                <text :x="chartGeom.PAD.l - 6" :y="chartGeom.H - chartGeom.PAD.b + 4"
                      text-anchor="end" font-size="11" fill="currentColor" fill-opacity="0.5">
                  {{ chartFmt(0) }}
                </text>
                <template v-for="(d, i) in chartGeom.days" :key="d">
                  <text v-if="i === 0 || i === chartGeom.days.length - 1 || i === Math.floor((chartGeom.days.length - 1) / 2)"
                        :x="chartGeom.PAD.l + (chartGeom.days.length > 1 ? i * ((chartGeom.W - chartGeom.PAD.l - chartGeom.PAD.r) / (chartGeom.days.length - 1)) : 0)"
                        :y="chartGeom.H - chartGeom.PAD.b + 14"
                        text-anchor="middle" font-size="10" fill="currentColor" fill-opacity="0.5">
                    {{ d.slice(5) }}
                  </text>
                </template>
                <g v-for="line in chartGeom.lines" :key="line.id">
                  <path :d="chartPath(line)" :stroke="line.color" stroke-width="2" fill="none" stroke-linejoin="round" stroke-linecap="round" />
                  <circle v-for="(p, i) in line.pts" :key="i" :cx="p.x" :cy="p.y" r="2" :fill="line.color" />
                </g>
                <line v-if="chartHover"
                      :x1="chartHover.x" :x2="chartHover.x"
                      :y1="chartGeom.PAD.t" :y2="chartGeom.H - chartGeom.PAD.b"
                      stroke="currentColor" stroke-opacity="0.3" stroke-dasharray="3 3" />
              </svg>
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
          </div>

          <!-- Sidebar: account picker, grouped by platform. Scrolls if
               the account list is taller than the chart on desktop. -->
          <aside class="w-full lg:w-64 lg:max-h-[340px] lg:overflow-y-auto border rounded-md p-2 text-sm shrink-0">
            <label v-if="chartSidebarGroups.length > 0"
                   class="flex items-center gap-2 px-2 py-1 cursor-pointer hover:bg-muted/40 rounded">
              <input type="checkbox" :checked="chartAllSelected" @change="toggleChartAll" />
              <span class="font-medium">{{ chartAllSelected ? 'Desmarcar todas' : 'Selecionar todas' }}</span>
            </label>
            <div v-if="chartSidebarGroups.length === 0" class="text-xs text-muted-foreground py-4 text-center">
              Sem contas pra esse filtro.
            </div>
            <template v-for="g in chartSidebarGroups" :key="g.platform">
              <div class="border-t my-1" />
              <div class="px-2 py-1 text-[10px] uppercase tracking-wide font-semibold"
                   :style="{ color: platformColor[g.platform] }">
                {{ g.label }}
              </div>
              <label v-for="a in g.accounts" :key="a.id"
                     class="flex items-center gap-2 px-2 py-1 cursor-pointer hover:bg-muted/40 rounded">
                <input type="checkbox"
                       :checked="chartSelected.has(a.id)"
                       @change="toggleChartLine(a.id)" />
                <span class="size-2.5 rounded-full shrink-0" :style="{ backgroundColor: accountColor(a) }" />
                <span class="truncate">{{ a.name }}</span>
              </label>
            </template>
          </aside>
        </div>
      </div>

      <!-- Schedule heatmap (ACOS por hora × dia) — ML + Shopee -->
      <div class="rounded-md border p-4">
        <div class="flex items-center gap-2 mb-3 flex-wrap">
          <Clock class="size-4 text-primary" />
          <h2 class="text-lg font-semibold">Heatmap ACOS — Horários</h2>
          <span class="text-xs text-muted-foreground">— clique para ligar/desligar</span>
          <select v-if="filteredAccounts.length > 0" v-model="schedAccountId" class="ml-auto border rounded-md px-2 py-1 text-sm bg-background">
            <option v-for="a in filteredAccounts" :key="a.id" :value="a.id">
              {{ a.name }}
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

      <!-- Agenda automática (BRT) — ML + Shopee -->
      <div v-if="selectedSchedAccount" class="rounded-md border p-4">
        <div class="flex items-center gap-2 mb-3 flex-wrap">
          <Clock class="size-4 text-primary" />
          <h2 class="text-lg font-semibold">Agenda automática</h2>
          <span class="text-xs text-muted-foreground">
            {{ selectedSchedAccount.name }} — horário de Brasília
          </span>
        </div>
        <div class="flex flex-wrap items-center gap-3">
          <button
            class="inline-flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm disabled:opacity-50 transition-colors"
            :class="scheduleState?.schedule_enabled
              ? 'border-emerald-500/50 bg-emerald-500/10 text-emerald-700'
              : 'hover:bg-muted'"
            :disabled="scheduleBusy" @click="toggleScheduleAuto">
            <span class="size-2 rounded-full" :class="scheduleState?.schedule_enabled ? 'bg-emerald-500' : 'bg-zinc-400'" />
            {{ scheduleState?.schedule_enabled ? 'Agenda automática LIGADA' : 'Agenda automática desligada' }}
          </button>

          <div v-if="scheduleState" class="text-sm inline-flex items-center gap-2">
            <span class="text-muted-foreground">Agora:</span>
            <span class="font-semibold" :class="scheduleState.state === 'on' ? 'text-emerald-600' : 'text-zinc-500'">
              {{ scheduleHint }}
            </span>
            <span v-if="scheduleState.override_active"
              class="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-violet-500/15 text-violet-700 border border-violet-500/30">
              override manual
            </span>
          </div>

          <div class="ml-auto flex items-center gap-2">
            <button class="rounded-md border px-2.5 py-1.5 text-sm inline-flex items-center gap-1 hover:bg-red-500/10 hover:border-red-500/40 disabled:opacity-50"
              :disabled="scheduleBusy" @click="overrideNow('pause')">
              <Pause class="size-3.5" /> Pausar agora
            </button>
            <button class="rounded-md border px-2.5 py-1.5 text-sm inline-flex items-center gap-1 hover:bg-emerald-500/10 hover:border-emerald-500/40 disabled:opacity-50"
              :disabled="scheduleBusy" @click="overrideNow('resume')">
              <Play class="size-3.5" /> Ligar agora
            </button>
            <button v-if="scheduleState?.override_active"
              class="rounded-md border px-2.5 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
              :disabled="scheduleBusy" @click="clearOverride">
              Voltar ao automático
            </button>
          </div>
        </div>
        <p class="mt-2 text-[11px] text-muted-foreground">
          As janelas ON/OFF são editadas no heatmap acima (clique pra ligar/desligar). Com a agenda ligada, a máquina
          dedicada pausa fora das janelas e religa dentro — sozinha, em horário de Brasília. O override manual vence a agenda até você voltar ao automático.
        </p>

        <!-- Oferta Relâmpago (Shopee flash-sale) — só Shopee -->
        <div v-if="selectedSchedAccount.platform === 'shopee'" class="mt-3 pt-3 border-t">
          <div class="flex items-center gap-2 mb-2 flex-wrap">
            <Sparkles class="size-4 text-primary" />
            <h3 class="text-sm font-semibold">Oferta Relâmpago</h3>
            <span class="text-xs text-muted-foreground">duplica a oferta 'Em andamento' pro próximo dia — 01:00 BRT</span>
          </div>
          <div class="flex flex-wrap items-center gap-3">
            <button
              class="inline-flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm disabled:opacity-50 transition-colors"
              :class="selectedSchedAccount.flash_duplicate_enabled
                ? 'border-emerald-500/50 bg-emerald-500/10 text-emerald-700'
                : 'hover:bg-muted'"
              :disabled="scheduleBusy" @click="toggleFlashDuplicate">
              <span class="size-2 rounded-full" :class="selectedSchedAccount.flash_duplicate_enabled ? 'bg-emerald-500' : 'bg-zinc-400'" />
              {{ selectedSchedAccount.flash_duplicate_enabled ? 'Duplicação diária LIGADA (01:00)' : 'Duplicação diária desligada' }}
            </button>
            <button
              class="ml-auto rounded-md border px-2.5 py-1.5 text-sm inline-flex items-center gap-1 hover:bg-primary/10 hover:border-primary/40 disabled:opacity-50"
              :disabled="flashBusy" @click="duplicateFlashNow">
              <Sparkles class="size-3.5" /> Duplicar agora
            </button>
          </div>
          <p class="mt-2 text-[11px] text-muted-foreground">
            A duplicação roda no executor local (AdsPower) e só CRIA de verdade quando ele está calibrado
            (SELECTORS_CALIBRATED=true); antes disso o comando percorre o fluxo mas para antes do Confirmar final (nada é criado).
          </p>
        </div>
      </div>

      <!-- Credit alerts — Shopee only -->
      <div v-if="platform === 'shopee'" class="rounded-md border p-4">
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
              <div class="font-medium">{{ a.name }}</div>
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
  </div>
</template>
