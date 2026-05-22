<script setup lang="ts">
import { computed } from 'vue'
import {
  Package, Plug, AlertTriangle, RefreshCw,
  ArrowUpRight, Activity, Tag, Truck, CheckCircle2,
} from 'lucide-vue-next'

type Channel = { platform: string; listings: number; linked: number }
type RecentSync = {
  id: string
  type: string
  status: string
  total: number
  processed: number
  started_at: string | null
  finished_at: string | null
}
type DashboardOut = {
  kpis: {
    products_total: number
    products_active: number
    integrations_total: number
    integrations_connected: number
    listings_total: number
    listings_linked: number
    alerts_unread: number
  }
  channels: Channel[]
  recent_syncs: RecentSync[]
  onboarding: { key: string; done: boolean }[]
  needs_onboarding: boolean
}
type AlertOut = {
  id: string
  type: string
  severity: 'info' | 'warning' | 'critical' | string
  title: string
  message: string | null
  created_at: string
  read_at: string | null
}
type AlertList = { items: AlertOut[]; total: number; unread: number }

const { api } = useApi()

const { data: health } = await useFetch<{ status: string; postgres: string; redis: string }>(
  `/api/health`,
  { server: false, default: () => ({ status: 'unknown', postgres: '?', redis: '?' }) },
)

const { data, refresh, pending } = await useAsyncData('dashboard', () => api<DashboardOut>('/api/dashboard'))
const { data: alertData } = await useAsyncData('dashboard-alerts', () =>
  api<AlertList>('/api/alerts?limit=5&unread_only=true'),
)

const PLATFORM_LABELS: Record<string, string> = {
  bling: 'Bling',
  ml: 'Mercado Livre',
  shopee: 'Shopee',
  amazon: 'Amazon',
  magalu: 'Magalu',
  tiktok: 'TikTok Shop',
}
const PLATFORM_COLORS: Record<string, string> = {
  bling: 'bg-sky-500',
  ml: 'bg-amber-400',
  shopee: 'bg-orange-500',
  amazon: 'bg-yellow-600',
  magalu: 'bg-blue-500',
  tiktok: 'bg-pink-500',
}

const kpis = computed(() => {
  const k = data.value?.kpis
  if (!k) return []
  return [
    {
      label: 'Produtos ativos',
      value: k.products_active.toLocaleString('pt-BR'),
      hint: `${k.products_total.toLocaleString('pt-BR')} no catálogo`,
      icon: Package,
    },
    {
      label: 'Integrações',
      value: `${k.integrations_connected}/${k.integrations_total || 0}`,
      hint: 'conectadas',
      icon: Plug,
    },
    {
      label: 'Anúncios vinculados',
      value: k.listings_linked.toLocaleString('pt-BR'),
      hint: `${k.listings_total.toLocaleString('pt-BR')} importados`,
      icon: Tag,
    },
    {
      label: 'Alertas',
      value: k.alerts_unread.toLocaleString('pt-BR'),
      hint: 'não lidos',
      icon: Activity,
    },
  ]
})

const channels = computed(() => {
  const list = data.value?.channels ?? []
  const total = list.reduce((acc, c) => acc + c.listings, 0)
  return list.map((c) => ({
    platform: c.platform,
    label: PLATFORM_LABELS[c.platform] ?? c.platform,
    color: PLATFORM_COLORS[c.platform] ?? 'bg-muted-foreground',
    listings: c.listings,
    linked: c.linked,
    share: total > 0 ? Math.round((c.listings / total) * 100) : 0,
  }))
})

const recentSyncs = computed(() => data.value?.recent_syncs ?? [])
const alerts = computed(() => alertData.value?.items ?? [])

function alertClass(sev: string) {
  if (sev === 'critical') return 'pill-danger'
  if (sev === 'warning') return 'pill-warning'
  return 'pill-info'
}

function alertGlyph(sev: string) {
  if (sev === 'critical') return '!'
  if (sev === 'warning') return '⚠'
  return 'i'
}

function formatRelative(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso).getTime()
  const diff = Math.max(0, Date.now() - d)
  const m = Math.floor(diff / 60_000)
  if (m < 1) return 'agora'
  if (m < 60) return `${m} min`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} h`
  return `${Math.floor(h / 24)} d`
}

function formatDuration(s: RecentSync): string {
  if (!s.started_at) return '—'
  const start = new Date(s.started_at).getTime()
  const end = s.finished_at ? new Date(s.finished_at).getTime() : Date.now()
  const ms = Math.max(0, end - start)
  const sec = Math.floor(ms / 1000)
  if (sec < 60) return `${sec}s`
  const m = Math.floor(sec / 60)
  return `${m}m ${sec - m * 60}s`
}

function syncTypeLabel(t: string): string {
  return t.replace(/_/g, ' ')
}

const onboardingComplete = computed(() => {
  const o = data.value?.onboarding ?? []
  return o.length > 0 && o.every((s) => s.done)
})
</script>

<template>
  <div class="space-y-6">
    <PageHeader title="Dashboard" description="Visão geral do catálogo, integrações e sincronizações.">
      <template #actions>
        <Button size="sm" variant="outline" :disabled="pending" @click="refresh()">
          <RefreshCw class="size-4 mr-1.5" :class="pending && 'animate-spin'" />
          Atualizar
        </Button>
      </template>
    </PageHeader>

    <NuxtLink
      v-if="!onboardingComplete && data"
      to="/onboarding"
      class="block rounded-xl border bg-amber-500/5 border-amber-500/30 px-4 py-3 hover:bg-amber-500/10 transition"
    >
      <div class="flex items-center gap-3">
        <div class="size-9 rounded-lg bg-amber-500/15 text-amber-600 grid place-items-center">
          <CheckCircle2 class="size-5" />
        </div>
        <div class="min-w-0 flex-1">
          <div class="font-medium text-sm">Termine a configuração inicial</div>
          <div class="text-xs text-muted-foreground">
            {{ data.onboarding.filter((s) => s.done).length }} de {{ data.onboarding.length }} passos concluídos.
          </div>
        </div>
        <ArrowUpRight class="size-4 text-muted-foreground" />
      </div>
    </NuxtLink>

    <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
      <StatCard v-for="k in kpis" :key="k.label" v-bind="k" />
    </div>

    <div class="grid lg:grid-cols-3 gap-4">
      <div class="lg:col-span-2 rounded-xl border bg-card p-5">
        <div class="flex items-center mb-4">
          <h2 class="font-semibold">Anúncios por canal</h2>
          <NuxtLink to="/anuncios" class="ml-auto text-xs text-primary inline-flex items-center hover:underline">
            ver anúncios <ArrowUpRight class="size-3 ml-0.5" />
          </NuxtLink>
        </div>

        <div v-if="channels.length === 0" class="py-8 text-center text-sm text-muted-foreground">
          Nenhum anúncio importado ainda.
        </div>
        <div v-else class="space-y-4">
          <div v-for="c in channels" :key="c.platform" class="space-y-1.5">
            <div class="flex items-center text-sm">
              <span class="font-medium">{{ c.label }}</span>
              <span class="ml-auto tabular-nums text-muted-foreground">{{ c.linked }}/{{ c.listings }} vinculados</span>
              <span class="ml-3 tabular-nums font-semibold w-12 text-right">{{ c.share }}%</span>
            </div>
            <div class="h-2 rounded-full bg-muted overflow-hidden">
              <div class="h-full rounded-full" :class="c.color" :style="{ width: c.share + '%' }" />
            </div>
          </div>
        </div>
      </div>

      <div class="rounded-xl border bg-card p-5 space-y-3">
        <div class="flex items-center">
          <AlertTriangle class="size-4 text-amber-500 mr-1.5" />
          <h2 class="font-semibold">Alertas</h2>
          <span class="pill pill-muted ml-auto">{{ alertData?.unread ?? 0 }}</span>
        </div>
        <ul v-if="alerts.length > 0" class="space-y-3">
          <li v-for="a in alerts" :key="a.id" class="flex gap-3">
            <span :class="alertClass(a.severity)">{{ alertGlyph(a.severity) }}</span>
            <div class="min-w-0">
              <div class="text-sm font-medium leading-tight">{{ a.title }}</div>
              <div v-if="a.message" class="text-xs text-muted-foreground">{{ a.message }}</div>
            </div>
          </li>
        </ul>
        <div v-else class="py-2 text-sm text-muted-foreground">Sem alertas.</div>
        <div class="pt-2 border-t">
          <div class="flex items-center text-xs text-muted-foreground">
            <span>API</span>
            <span class="ml-auto" :class="health?.status === 'ok' ? 'text-emerald-600' : 'text-red-600'">
              {{ health?.status }}
            </span>
          </div>
          <div class="flex items-center text-xs text-muted-foreground mt-1">
            <span>Postgres / Redis</span>
            <span class="ml-auto font-mono">{{ health?.postgres }} / {{ health?.redis }}</span>
          </div>
        </div>
      </div>
    </div>

    <div class="rounded-xl border bg-card overflow-hidden">
      <div class="flex items-center px-5 py-3 border-b">
        <Truck class="size-4 text-muted-foreground mr-1.5" />
        <h2 class="font-semibold">Sincronizações recentes</h2>
        <NuxtLink to="/sincronizacoes" class="ml-auto text-xs text-primary inline-flex items-center hover:underline">
          ver todas <ArrowUpRight class="size-3 ml-0.5" />
        </NuxtLink>
      </div>
      <div v-if="recentSyncs.length === 0" class="p-6 text-center text-sm text-muted-foreground">
        Nenhuma sincronização ainda.
      </div>
      <table v-else class="w-full text-sm">
        <thead>
          <tr class="bg-muted/40 text-left text-xs text-muted-foreground">
            <th class="px-5 py-2 font-medium">Tipo</th>
            <th class="px-3 py-2 font-medium">Status</th>
            <th class="px-3 py-2 font-medium text-right">Processados</th>
            <th class="px-3 py-2 font-medium text-right">Total</th>
            <th class="px-3 py-2 font-medium">Duração</th>
            <th class="px-5 py-2 font-medium text-right">Quando</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in recentSyncs" :key="r.id" class="border-t hover:bg-muted/30">
            <td class="px-5 py-2.5 font-medium">{{ syncTypeLabel(r.type) }}</td>
            <td class="px-3 py-2.5">
              <span
                class="text-xs px-1.5 py-0.5 rounded"
                :class="{
                  'bg-emerald-500/10 text-emerald-600': r.status === 'success',
                  'bg-red-500/10 text-red-600': r.status === 'failed',
                  'bg-amber-500/10 text-amber-600': r.status === 'running',
                  'bg-muted text-muted-foreground': !['success', 'failed', 'running'].includes(r.status),
                }"
              >{{ r.status }}</span>
            </td>
            <td class="px-3 py-2.5 text-right tabular-nums">{{ r.processed }}</td>
            <td class="px-3 py-2.5 text-right tabular-nums text-muted-foreground">{{ r.total }}</td>
            <td class="px-3 py-2.5 text-muted-foreground">{{ formatDuration(r) }}</td>
            <td class="px-5 py-2.5 text-right text-muted-foreground">{{ formatRelative(r.started_at) }} atrás</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
