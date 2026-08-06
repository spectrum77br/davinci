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
  <div v-if="visible.length > 1" class="flex flex-wrap gap-1 rounded-md bg-muted/40 p-1 w-fit">
    <NuxtLink
      v-for="t in visible"
      :key="t.to"
      :to="t.to"
      class="inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-sm transition-colors"
      :class="isActive(t.to)
        ? 'bg-background shadow-sm'
        : 'text-muted-foreground hover:text-foreground'"
    >
      {{ t.label }}
    </NuxtLink>
  </div>
</template>
