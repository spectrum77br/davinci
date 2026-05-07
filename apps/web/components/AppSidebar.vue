<script setup lang="ts">
import { computed } from 'vue'
import {
  LayoutDashboard, Rocket, Plug, Building2, ContactRound, Users,
  Package, Megaphone, DollarSign, Truck, RefreshCw, Undo2,
  Receipt, ListChecks, TrendingUp, ShieldCheck, Settings, Bell,
  FileSearch, ChevronLeft, ChevronRight,
} from 'lucide-vue-next'

const props = defineProps<{ collapsed: boolean }>()
const emit = defineEmits<{ (e: 'toggle'): void }>()

const auth = useAuthStore()
const route = useRoute()

type Item = {
  to: string
  label: string
  icon: any
  resource?: string
  adminOnly?: boolean
}

type Section = { label?: string; items: Item[] }

const sections: Section[] = [
  {
    items: [
      { to: '/', label: 'Dashboard', icon: LayoutDashboard },
      { to: '/primeiros-passos', label: 'Primeiros passos', icon: Rocket },
    ],
  },
  {
    label: 'Operação',
    items: [
      { to: '/produtos', label: 'Produtos', icon: Package, resource: 'produtos' },
      { to: '/anuncios', label: 'Anúncios', icon: Megaphone, resource: 'anuncios' },
      { to: '/pricing/contas', label: 'Tabela de preços', icon: DollarSign, resource: 'tabela_precos' },
      { to: '/audit', label: 'Auditoria', icon: FileSearch, resource: 'auditoria' },
      { to: '/margem', label: 'Margem', icon: TrendingUp, resource: 'margem' },
    ],
  },
  {
    label: 'Pós-venda',
    items: [
      { to: '/conciliacao-frete', label: 'Conciliação frete', icon: Truck, resource: 'conciliacao_frete' },
      { to: '/devolucoes', label: 'Devoluções', icon: Undo2, resource: 'devolucoes' },
      { to: '/reembolso', label: 'Reembolso', icon: Receipt, resource: 'reembolso' },
    ],
  },
  {
    label: 'Sistema',
    items: [
      { to: '/sincronizacoes', label: 'Sincronizações', icon: RefreshCw, resource: 'sincronizacoes' },
      { to: '/tarefas', label: 'Tarefas', icon: ListChecks, resource: 'tarefas' },
      { to: '/integrations', label: 'Integrações', icon: Plug, resource: 'empresa' },
      { to: '/alertas', label: 'Alertas', icon: Bell },
    ],
  },
  {
    label: 'Cadastros',
    items: [
      { to: '/companies', label: 'Empresas', icon: Building2, resource: 'empresa' },
      { to: '/cadastros', label: 'Cadastros', icon: ContactRound, resource: 'cadastro' },
    ],
  },
  {
    label: 'Admin',
    items: [
      { to: '/users', label: 'Usuários', icon: Users, adminOnly: true },
      { to: '/permissoes', label: 'Permissões', icon: ShieldCheck, adminOnly: true },
      { to: '/configuracoes', label: 'Configurações', icon: Settings },
    ],
  },
]

const visibleSections = computed(() =>
  sections
    .map((s) => ({
      ...s,
      items: s.items.filter((it) => {
        if (it.adminOnly && !auth.isAdmin) return false
        return true
      }),
    }))
    .filter((s) => s.items.length > 0),
)

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
              class="group flex items-center gap-3 rounded-lg px-2.5 h-9 text-sm font-medium transition-colors"
              :class="isActive(it.to)
                ? 'bg-[hsl(var(--sidebar-active-bg))] text-[hsl(var(--sidebar-active-fg))]'
                : 'text-[hsl(var(--sidebar-foreground))] hover:bg-muted'"
            >
              <component :is="it.icon" class="size-[18px] shrink-0" />
              <span v-if="!props.collapsed" class="truncate">{{ it.label }}</span>
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
