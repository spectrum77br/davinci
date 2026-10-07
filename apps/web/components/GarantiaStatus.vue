<script setup lang="ts">
// Selo do status da garantia com a cor e o emoji do documento (§2):
// 🟡 Aguardando entrega · 🟢 Ativa · 🔵 Somente software · 🔴 Expirada.
// `soEmoji` = só o emoji (a coluna da tabela no celular), com o rótulo no
// title e para o leitor de tela. `entregueSemData`: o "Aguardando entrega" de
// pedido que o Bling já dá como entregue vira "Entregue — sem data no DaVinci".
import { statusInfo } from '~/lib/garantias'

const props = defineProps<{ status: string | null | undefined; soEmoji?: boolean; entregueSemData?: boolean }>()
const info = computed(() => statusInfo(props.status, !!props.entregueSemData))
// As cores moram aqui (o Tailwind não lê lib/).
const CORES: Record<string, string> = {
  aguardando_entrega: 'border-amber-400/60 bg-amber-50 text-amber-800 dark:border-amber-500/40 dark:bg-amber-500/15 dark:text-amber-300',
  ativa: 'border-emerald-400/60 bg-emerald-50 text-emerald-800 dark:border-emerald-500/40 dark:bg-emerald-500/15 dark:text-emerald-300',
  somente_software: 'border-sky-400/60 bg-sky-50 text-sky-800 dark:border-sky-500/40 dark:bg-sky-500/15 dark:text-sky-300',
  expirada: 'border-red-400/60 bg-red-50 text-red-800 dark:border-red-500/40 dark:bg-red-500/15 dark:text-red-300',
}
const cor = computed(() => CORES[props.status || ''] || 'border-border bg-muted text-muted-foreground')
</script>

<template>
  <span
    v-if="soEmoji"
    class="inline-flex items-center text-base leading-none"
    :title="`${info.rotulo} — ${info.dica}`"
    :aria-label="info.rotulo"
    role="img"
    data-status-garantia
  >{{ info.emoji }}</span>
  <span
    v-else
    class="inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium"
    :class="cor"
    :title="info.dica"
    :data-entregue-sem-data="entregueSemData && status === 'aguardando_entrega' ? '' : undefined"
    data-status-garantia
  ><span aria-hidden="true">{{ info.emoji }}</span>{{ info.rotulo }}</span>
</template>
