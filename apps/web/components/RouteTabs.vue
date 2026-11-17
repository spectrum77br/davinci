<script setup lang="ts">
// Barra de abas que NAVEGA entre rotas de um grupo unificado (lib/navGroups).
// Mesmo visual das sub-abas da Tabela de Preços. Abas são filtradas pela
// permissão de view do usuário; com 0 ou 1 aba visível a barra some inteira
// (ex.: operador vendo /tarefas não ganha barra "Usuários | Permissões").
// `sub` (17/11/2026) = a segunda barra, a das abas de dentro de uma aba
// (Cadastros › Integrações › Histórico): mais discreta, sublinhada.
import { computed } from 'vue'
import { allowedTabs, tabPontos, tabRoutes, type RouteTab } from '~/lib/navGroups'

const props = defineProps<{ tabs: RouteTab[]; sub?: boolean }>()
const auth = useAuthStore()
const route = useRoute()
// As mesmas contagens da bolinha do menu lateral (AppSidebar busca a cada 15 s).
const pendentes = {
  tarefas: useState<number>('davinci.pendentes.tarefas', () => 0),
  faturas: useState<number>('davinci.pendentes.faturas', () => 0),
}

const visible = computed(() => allowedTabs(props.tabs, auth.user as any))

function isActive(t: RouteTab) {
  return tabRoutes(t).some((to) => route.path === to || route.path.startsWith(to + '/'))
}

function temPonto(t: RouteTab) {
  return tabPontos(t).some((p) => pendentes[p].value > 0)
}
</script>

<template>
  <nav
    v-if="visible.length > 1"
    :aria-label="props.sub ? 'Seções dentro desta aba' : 'Seções desta área'"
    class="flex max-w-full flex-nowrap overflow-x-auto"
    :class="props.sub ? 'gap-4 border-b' : 'gap-1 rounded-md bg-muted/40 p-1 sm:w-fit sm:flex-wrap'"
  >
    <NuxtLink
      v-for="t in visible"
      :key="t.to"
      :to="t.to"
      class="inline-flex shrink-0 items-center gap-1.5 text-sm whitespace-nowrap transition-colors"
      :aria-current="isActive(t) ? 'page' : undefined"
      :class="props.sub
        ? ['-mb-px border-b-2 px-1 py-2.5 sm:py-1.5', isActive(t)
          ? 'border-primary text-foreground font-medium'
          : 'border-transparent text-muted-foreground hover:text-foreground']
        : ['rounded px-3 py-2.5 sm:py-1.5', isActive(t)
          ? 'bg-background shadow-sm'
          : 'text-muted-foreground hover:text-foreground']"
    >
      {{ t.label }}
      <span v-if="temPonto(t)" class="inline-block size-2 rounded-full bg-red-500" aria-label="tem pendência" />
    </NuxtLink>
  </nav>
</template>
