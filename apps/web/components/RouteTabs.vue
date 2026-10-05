<script setup lang="ts">
// Barra de abas que NAVEGA entre rotas de um grupo unificado (lib/navGroups).
// Mesmo visual das sub-abas da Tabela de Preços. Abas são filtradas pela
// permissão de view do usuário; com 0 ou 1 aba visível a barra some inteira
// (ex.: operador vendo /tarefas não ganha barra "Usuários | Permissões").
import { computed } from 'vue'
import { allowedTabs, type RouteTab } from '~/lib/navGroups'

const props = defineProps<{ tabs: RouteTab[] }>()
const auth = useAuthStore()
const route = useRoute()

const visible = computed(() => allowedTabs(props.tabs, auth.user as any))

function isActive(to: string) {
  return route.path === to || route.path.startsWith(to + '/')
}
</script>

<template>
  <nav v-if="visible.length > 1" aria-label="Seções desta área" class="flex max-w-full flex-nowrap overflow-x-auto gap-1 rounded-md bg-muted/40 p-1 sm:w-fit sm:flex-wrap">
    <NuxtLink
      v-for="t in visible"
      :key="t.to"
      :to="t.to"
      class="inline-flex shrink-0 items-center gap-1.5 rounded px-3 py-2.5 sm:py-1.5 text-sm whitespace-nowrap transition-colors"
      :aria-current="isActive(t.to) ? 'page' : undefined"
      :class="isActive(t.to)
        ? 'bg-background shadow-sm'
        : 'text-muted-foreground hover:text-foreground'"
    >
      {{ t.label }}
    </NuxtLink>
  </nav>
</template>
