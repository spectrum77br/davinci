<script setup lang="ts">
import { computed, onScopeDispose, ref, watch } from 'vue'
import {
  LayoutDashboard, Rocket, Plug, Building2, ContactRound, Users,
  Package, Megaphone, DollarSign, RefreshCw, Undo2,
  Receipt, TrendingUp, ShieldCheck, Settings, Bell, BarChart3,
  Store, Tags, ClipboardList, ChevronLeft, ChevronRight, Warehouse,
  Coins, FileText, Calculator, FlaskConical, Ship,
} from 'lucide-vue-next'

const props = defineProps<{ collapsed: boolean }>()
const emit = defineEmits<{ (e: 'toggle'): void }>()

const auth = useAuthStore()
const route = useRoute()
const runtimeConfig = useRuntimeConfig()
const enableMarketing = computed(() => Boolean(runtimeConfig.public.enableMarketing))

// Pulsing red dot on the "Tarefas" row when the current user has open
// tarefas (data_conclusao IS NULL). Polls /api/tarefas/meu-pendente-count
// every 15s + on auth-state change. Independent from the modal so the
// dot stays even after the modal is dismissed.
const { api } = useApi()
const pendingTarefasCount = ref(0)

async function fetchPendingTarefas() {
  if (!auth.user) return
  try {
    const r = await api<{ count: number }>('/api/tarefas/meu-pendente-count')
    pendingTarefasCount.value = r.count ?? 0
  } catch {
    // Silent — the modal's polling will surface real outages.
  }
}

let pendingPoll: ReturnType<typeof setInterval> | null = null
watch(
  () => auth.user?.id ?? null,
  (uid) => {
    if (pendingPoll) {
      clearInterval(pendingPoll)
      pendingPoll = null
    }
    pendingTarefasCount.value = 0
    if (uid && import.meta.client) {
      void fetchPendingTarefas()
      pendingPoll = setInterval(() => {
        void fetchPendingTarefas()
      }, 15_000)
    }
  },
  { immediate: true },
)
onScopeDispose(() => {
  if (pendingPoll) {
    clearInterval(pendingPoll)
    pendingPoll = null
  }
})

type Item = {
  to: string
  label: string
  icon: any
  resource?: string
  adminOnly?: boolean
  featureFlag?: 'marketing'
}

type Section = { label?: string; items: Item[] }

const sections: Section[] = [
  {
    items: [
      { to: '/', label: 'Dashboard', icon: LayoutDashboard },
      { to: '/onboarding', label: 'Primeiros passos', icon: Rocket },
    ],
  },
  {
    label: 'Operação',
    items: [
      { to: '/produtos', label: 'Produtos', icon: Package, resource: 'produtos' },
      { to: '/anuncios', label: 'Anúncios', icon: Megaphone, resource: 'anuncios' },
      { to: '/marketing', label: 'Marketing', icon: BarChart3, featureFlag: 'marketing' },
      { to: '/pricing/tabela', label: 'Tabela de preços', icon: DollarSign, resource: 'tabela_precos' },
      { to: '/margem', label: 'Margem', icon: TrendingUp, resource: 'margem' },
      { to: '/controle-estoque', label: 'Controle de Estoque', icon: Warehouse, resource: 'controle_estoque' },
    ],
  },
  {
    label: 'Pós-venda',
    items: [
      { to: '/devolucoes', label: 'Devoluções', icon: Undo2, resource: 'devolucoes' },
      { to: '/reembolso', label: 'Reembolso', icon: Receipt, resource: 'reembolso' },
    ],
  },
  {
    label: 'Financeiro',
    items: [
      { to: '/financeiro/consorcio', label: 'Consórcio', icon: Coins, resource: 'financeiro_consorcio' },
      { to: '/financeiro/suprimentos', label: 'Suprimentos', icon: FileText, resource: 'financeiro_suprimentos' },
      { to: '/financeiro/simulacao', label: 'Simulação', icon: Calculator, resource: 'financeiro_simulacao' },
      { to: '/financeiro/dnp', label: 'DNP', icon: FlaskConical, resource: 'financeiro_dnp' },
    ],
  },
  {
    label: 'Importação',
    items: [
      { to: '/importacao', label: 'Importação', icon: Ship, resource: 'importacao' },
    ],
  },
  {
    label: 'Sistema',
    items: [
      { to: '/sincronizacoes', label: 'Sincronizações', icon: RefreshCw, resource: 'sincronizacoes' },
      { to: '/integrations', label: 'Integrações', icon: Plug, resource: 'integracoes' },
      { to: '/alertas', label: 'Alertas', icon: Bell, resource: 'alertas' },
    ],
  },
  {
    label: 'Cadastros',
    items: [
      { to: '/companies', label: 'Empresas', icon: Building2, resource: 'empresa' },
      { to: '/cadastros', label: 'Cadastros', icon: ContactRound, resource: 'cadastro' },
      { to: '/store-info', label: 'Lojas', icon: Store, resource: 'lojas_info' },
      { to: '/admin/segments', label: 'Segmentos', icon: Tags, resource: 'segmentos' },
    ],
  },
  {
    label: 'Admin',
    items: [
      { to: '/users', label: 'Usuários', icon: Users, adminOnly: true },
      { to: '/permissoes', label: 'Permissões', icon: ShieldCheck, adminOnly: true },
      { to: '/configuracoes', label: 'Configurações', icon: Settings, resource: 'configuracoes' },
      // Regular users see their own tarefas (router-level filter), so this
      // is NOT marked adminOnly — the page itself hides admin-only controls.
      { to: '/tarefas', label: 'Tarefas', icon: ClipboardList },
    ],
  },
]

// "Operador de estoque" = role !== 'admin' AND has at least one
// stock tag. These users only ever see /controle-estoque; the sidebar
// collapses to that single item even though the route guard already
// locks them there.
const isOperator = computed(
  () => !auth.isAdmin && (auth.user?.stock_tags?.length ?? 0) > 0,
)

const visibleSections = computed(() => {
  if (isOperator.value) {
    return [
      {
        items: [
          { to: '/controle-estoque', label: 'Controle de Estoque', icon: Warehouse },
        ],
      },
    ]
  }
  return sections
    .map((s) => ({
      ...s,
      items: s.items.filter((it) => {
        if (it.adminOnly && !auth.isAdmin) return false
        if (it.featureFlag === 'marketing' && !enableMarketing.value) return false
        return true
      }),
    }))
    .filter((s) => s.items.length > 0)
})

function isActive(to: string) {
  if (to === '/') return route.path === '/'
  return route.path === to || route.path.startsWith(to + '/')
}
</script>

<template>
  <aside
    class="shrink-0 border-r bg-[hsl(var(--sidebar))] text-[hsl(var(--sidebar-foreground))] transition-[width] duration-200 flex flex-col h-screen sticky top-0"
    :class="props.collapsed ? 'w-[68px]' : 'w-[248px]'"
  >
    <div class="h-14 flex items-center gap-2 px-4 border-b">
      <div class="size-7 rounded-md bg-primary text-primary-foreground grid place-items-center font-bold text-[13px]">
        D
      </div>
      <strong v-if="!props.collapsed" class="text-[15px] tracking-tight">DaVinci</strong>
      <button
        class="ml-auto rounded-md p-1 text-muted-foreground hover:text-foreground hover:bg-muted"
        @click="emit('toggle')"
      >
        <ChevronLeft v-if="!props.collapsed" class="size-4" />
        <ChevronRight v-else class="size-4" />
      </button>
    </div>

    <nav class="flex-1 overflow-y-auto py-3 space-y-4">
      <div v-for="(section, idx) in visibleSections" :key="idx">
        <div
          v-if="section.label && !props.collapsed"
          class="px-4 pb-1 text-[10px] uppercase tracking-[0.12em] font-semibold text-[hsl(var(--sidebar-muted))]"
        >
          {{ section.label }}
        </div>
        <ul class="px-2 space-y-0.5">
          <li v-for="it in section.items" :key="it.to">
            <NuxtLink
              :to="it.to"
              :title="props.collapsed ? it.label : undefined"
              class="group relative flex items-center gap-3 rounded-lg px-2.5 h-9 text-sm font-medium transition-colors"
              :class="isActive(it.to)
                ? 'bg-[hsl(var(--sidebar-active-bg))] text-[hsl(var(--sidebar-active-fg))]'
                : 'text-[hsl(var(--sidebar-foreground))] hover:bg-muted'"
            >
              <component :is="it.icon" class="size-[18px] shrink-0" />
              <span v-if="!props.collapsed" class="truncate">{{ it.label }}</span>
              <!-- Solid red dot when this user has pending tarefas.
                   Static (no animation) — the pulsing version was too
                   visually noisy. Expanded layout: right-aligned next
                   to the label. Collapsed: top-right of the icon. -->
              <span
                v-if="it.to === '/tarefas' && pendingTarefasCount > 0"
                :class="props.collapsed
                  ? 'absolute top-1 right-1 inline-block size-2.5 rounded-full bg-red-500'
                  : 'ml-auto inline-block size-2.5 rounded-full bg-red-500'"
                :title="`${pendingTarefasCount} tarefa${pendingTarefasCount === 1 ? '' : 's'} pendente${pendingTarefasCount === 1 ? '' : 's'}`"
              />
            </NuxtLink>
          </li>
        </ul>
      </div>
    </nav>

    <div v-if="!props.collapsed" class="border-t p-3 text-[11px] text-muted-foreground">
      <div class="font-medium text-foreground truncate">{{ auth.user?.name || auth.user?.email }}</div>
      <div class="truncate">{{ auth.user?.email }}</div>
    </div>
  </aside>
</template>
