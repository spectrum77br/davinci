<script setup lang="ts">
import { computed, onMounted, onScopeDispose, ref, watch } from 'vue'
import {
  LayoutDashboard, Rocket, Plug, ContactRound, Users,
  Package, Megaphone, DollarSign, Undo2,
  Receipt, TrendingUp, Settings, BarChart3,
  ClipboardList, ChevronDown, ChevronLeft, ChevronRight, Warehouse,
  Coins, FileText, Calculator, FlaskConical, Ship, Landmark, Headset,
  ReceiptText,
} from 'lucide-vue-next'
import { allowedTabs, TABS_CADASTROS, TABS_NF, TABS_SISTEMA } from '~/lib/navGroups'

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
// Red dot on "Faturas" (admin only) when there are faturas vencidas/vencendo
// (data_vencimento <= hoje+1). Polls /api/faturas/vencendo-count.
const pendingFaturasCount = ref(0)

async function fetchPendingTarefas() {
  if (!auth.user) return
  try {
    const r = await api<{ count: number }>('/api/tarefas/meu-pendente-count')
    pendingTarefasCount.value = r.count ?? 0
  } catch {
    // Silent — the modal's polling will surface real outages.
  }
}

async function fetchPendingFaturas() {
  if (!auth.user || !auth.isAdmin) return
  try {
    const r = await api<{ count: number }>('/api/faturas/vencendo-count')
    pendingFaturasCount.value = r.count ?? 0
  } catch {
    // Silent.
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
    pendingFaturasCount.value = 0
    if (uid && import.meta.client) {
      void fetchPendingTarefas()
      void fetchPendingFaturas()
      pendingPoll = setInterval(() => {
        void fetchPendingTarefas()
        void fetchPendingFaturas()
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
  ownerOnly?: boolean
  featureFlag?: 'marketing'
  // Grupo unificado (lib/navGroups): o item destaca quando QUALQUER rota
  // do grupo está ativa, e some quando o usuário não pode ver nenhuma aba.
  match?: string[]
  // Item que só aparece pra NÃO-admin (ex.: Tarefas — admin acessa via
  // aba dentro de Usuários).
  hideForAdmin?: boolean
}

type Section = { label?: string; items: Item[] }

// Itens de grupo unificado: `to` aponta pra primeira aba que o usuário pode
// ver (admin vê todas). Devolve null quando nenhuma — o item some do menu.
function groupItem(
  tabs: typeof TABS_CADASTROS,
  base: { label: string; icon: any },
): Item | null {
  const allowed = allowedTabs(tabs, auth.user as any)
  if (!allowed.length) return null
  return {
    ...base,
    // Com UMA aba visível não existe "grupo" pra essa pessoa: o item assume
    // o nome da própria página (ex.: só vê Lojas → item vira "Lojas").
    label: allowed.length === 1 ? allowed[0].label : base.label,
    to: allowed[0].to,
    match: tabs.map((t) => t.to),
  }
}

const sections = computed<Section[]>(() => [
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
      { to: '/faturamento', label: 'Faturamento', icon: Coins, resource: 'faturamento' },
      { to: '/controle-estoque', label: 'Controle de Estoque', icon: Warehouse, resource: 'controle_estoque' },
    ],
  },
  {
    label: 'Pós-venda',
    items: [
      { to: '/devolucoes', label: 'Devoluções', icon: Undo2, resource: 'devolucoes' },
      { to: '/reembolso', label: 'Reembolso', icon: Receipt, resource: 'reembolso' },
      { to: '/logistica', label: 'Logística', icon: Headset, resource: 'logistica' },
      { to: '/notas-fiscais', label: 'Notas fiscais', icon: FileText, resource: 'notas_fiscais' },
    ],
  },
  {
    label: 'Financeiro',
    items: [
      { to: '/financeiro/valuation', label: 'Valuation', icon: Landmark, adminOnly: true },
      { to: '/financeiro/consorcio', label: 'Consórcio', icon: Coins, resource: 'financeiro_consorcio' },
    ],
  },
  {
    label: 'Suprimentos',
    items: [
      { to: '/financeiro/suprimentos', label: 'Certificações', icon: FileText, resource: 'financeiro_suprimentos' },
      { to: '/importacao', label: 'Importação', icon: Ship, resource: 'importacao' },
      { to: '/financeiro/simulacao', label: 'Simulação', icon: Calculator, resource: 'financeiro_simulacao' },
      { to: '/financeiro/dnp', label: 'DNP', icon: FlaskConical, resource: 'financeiro_dnp' },
    ],
  },
  {
    label: 'Sistema',
    items: [
      // Sincronizações + Integrações + Alertas viraram abas de um item só.
      groupItem(TABS_SISTEMA, { label: 'Integrações', icon: Plug }),
      { to: '/margem-audit', label: 'Auditoria de pedidos', icon: ClipboardList, ownerOnly: true },
    ].filter((x): x is Item => x !== null),
  },
  {
    label: 'Cadastros',
    items: [
      // Empresas + Cadastros + Lojas + Segmentos viraram abas de um item só.
      groupItem(TABS_CADASTROS, { label: 'Cadastros', icon: ContactRound }),
      // NF (Faturador) + Faturamento NF idem.
      groupItem(TABS_NF, { label: 'NF Faturador', icon: ReceiptText }),
    ].filter((x): x is Item => x !== null),
  },
  {
    label: 'Admin',
    items: [
      // Permissões e Tarefas agora são abas dentro de Usuários.
      {
        to: '/users', label: 'Usuários', icon: Users, adminOnly: true,
        match: ['/users', '/permissoes', '/tarefas'],
      },
      { to: '/configuracoes', label: 'Configurações', icon: Settings, resource: 'configuracoes' },
      // Não-admin segue com o item próprio de Tarefas (vê só as dele;
      // não enxerga Usuários/Permissões). Admin acessa via aba.
      { to: '/tarefas', label: 'Tarefas', icon: ClipboardList, hideForAdmin: true },
      { to: '/faturas', label: 'Faturas', icon: Receipt, adminOnly: true },
    ],
  },
])

// "Operador de estoque" = role !== 'admin' AND has at least one stock tag
// AND has no view permission on any resource other than controle_estoque.
// Users who also hold other permissions (margem, devolucoes, etc.) see the
// full sidebar even if they happen to have stock tags assigned.
const isOperator = computed(() => {
  if (auth.isAdmin) return false
  if (!(auth.user?.stock_tags?.length ?? 0)) return false
  const perms = auth.user?.permissions ?? {}
  const hasOtherAccess = Object.entries(perms).some(
    ([key, val]) => key !== 'controle_estoque' && (val as any)?.view === true,
  )
  return !hasOtherAccess
})

const isOwner = computed(() => auth.user?.email === 'spectrum77@tuta.com')

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
  return sections.value
    .map((s) => ({
      ...s,
      items: s.items.filter((it) => {
        if (it.adminOnly && !auth.isAdmin) return false
        if (it.hideForAdmin && auth.isAdmin) return false
        if (it.ownerOnly && !isOwner.value) return false
        if (it.featureFlag === 'marketing' && !enableMarketing.value) return false
        return true
      }),
    }))
    .filter((s) => s.items.length > 0)
})

function isActive(it: Item) {
  if (it.to === '/') return route.path === '/'
  const targets = it.match ?? [it.to]
  return targets.some((to) => route.path === to || route.path.startsWith(to + '/'))
}

// ── Grupos recolhíveis ─────────────────────────────────────────────────
// Clicar no título da seção (Operação, Pós-venda…) abre/fecha o grupo.
// O que ficou fechado é lembrado no navegador (localStorage) e o grupo
// da página atual reabre sozinho ao navegar pra ela.
const CLOSED_KEY = 'davinci.sidebar.closed'
const closedGroups = ref<string[]>([])

function persistClosed() {
  try {
    localStorage.setItem(CLOSED_KEY, JSON.stringify(closedGroups.value))
  } catch {
    // navegador sem localStorage — só não lembra entre visitas
  }
}

function toggleGroup(label?: string) {
  if (!label) return
  closedGroups.value = closedGroups.value.includes(label)
    ? closedGroups.value.filter((l) => l !== label)
    : [...closedGroups.value, label]
  persistClosed()
}

function isGroupOpen(section: Section) {
  if (!section.label) return true
  return !closedGroups.value.includes(section.label)
}

// Reabre o grupo que contém a rota atual (se estiver fechado).
function openSectionOf(path: string) {
  const label = sections.value.find((s) =>
    s.label && s.items.some((it) =>
      it.to !== '/' && (it.match ?? [it.to]).some((to) => path === to || path.startsWith(to + '/')),
    ),
  )?.label
  if (label && closedGroups.value.includes(label)) {
    closedGroups.value = closedGroups.value.filter((l) => l !== label)
    persistClosed()
  }
}

onMounted(() => {
  try {
    const raw = localStorage.getItem(CLOSED_KEY)
    if (raw) {
      const arr = JSON.parse(raw)
      if (Array.isArray(arr)) closedGroups.value = arr.filter((x): x is string => typeof x === 'string')
    }
  } catch {
    // JSON inválido/localStorage bloqueado — começa tudo aberto
  }
  openSectionOf(route.path)
})

watch(() => route.path, (p) => openSectionOf(p))
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

    <nav class="flex-1 overflow-y-auto py-2 space-y-1.5">
      <div v-for="(section, idx) in visibleSections" :key="idx">
        <button
          v-if="section.label && !props.collapsed"
          type="button"
          class="w-full flex items-center px-4 py-1 text-[10px] uppercase tracking-[0.12em] font-semibold text-[hsl(var(--sidebar-muted))] hover:text-foreground transition-colors"
          @click="toggleGroup(section.label)"
        >
          <span>{{ section.label }}</span>
          <ChevronDown v-if="isGroupOpen(section)" class="size-3 ml-auto" />
          <ChevronRight v-else class="size-3 ml-auto" />
        </button>
        <ul v-show="props.collapsed || isGroupOpen(section)" class="px-2 space-y-px">
          <li v-for="it in section.items" :key="it.to">
            <NuxtLink
              :to="it.to"
              :title="props.collapsed ? it.label : undefined"
              class="group relative flex items-center gap-2.5 rounded-md px-2.5 h-8 text-[13px] font-medium transition-colors"
              :class="isActive(it)
                ? 'bg-[hsl(var(--sidebar-active-bg))] text-[hsl(var(--sidebar-active-fg))]'
                : 'text-[hsl(var(--sidebar-foreground))] hover:bg-muted'"
            >
              <component :is="it.icon" class="size-4 shrink-0" />
              <span v-if="!props.collapsed" class="truncate">{{ it.label }}</span>
              <!-- Solid red dot when this user has pending tarefas.
                   Static (no animation) — the pulsing version was too
                   visually noisy. Expanded layout: right-aligned next
                   to the label. Collapsed: top-right of the icon. -->
              <!-- Pro admin o item Tarefas não existe — a bolinha aparece no
                   item Usuários (que contém a aba Tarefas via `match`). -->
              <span
                v-if="(it.to === '/tarefas' || (it.match?.includes('/tarefas') ?? false)) && pendingTarefasCount > 0"
                :class="props.collapsed
                  ? 'absolute top-1 right-1 inline-block size-2.5 rounded-full bg-red-500'
                  : 'ml-auto inline-block size-2.5 rounded-full bg-red-500'"
                :title="`${pendingTarefasCount} tarefa${pendingTarefasCount === 1 ? '' : 's'} pendente${pendingTarefasCount === 1 ? '' : 's'}`"
              />
              <span
                v-if="it.to === '/faturas' && pendingFaturasCount > 0"
                :class="props.collapsed
                  ? 'absolute top-1 right-1 inline-block size-2.5 rounded-full bg-red-500'
                  : 'ml-auto inline-block size-2.5 rounded-full bg-red-500'"
                :title="`${pendingFaturasCount} fatura${pendingFaturasCount === 1 ? '' : 's'} vencendo`"
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
