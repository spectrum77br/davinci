<script setup lang="ts">
import { TrendingUp, TrendingDown } from 'lucide-vue-next'

const props = defineProps<{
  label: string
  value: string | number
  delta?: number
  hint?: string
  icon?: any
  tone?: 'default' | 'success' | 'warning' | 'danger'
  // 18/09 (Vinicius: "tô achando muito grande"): versão enxuta pros resumos que
  // ficam em cima de uma tabela (Chamados, Devoluções) — menos altura, número menor.
  compact?: boolean
}>()

// 18/09: `tone` passou a colorir o número (Chamados e Devoluções já passavam o
// tom, mas ele não fazia nada). Só quando há algo a mostrar — um 0 vermelho
// assusta à toa.
const TONS: Record<string, string> = {
  success: 'text-emerald-600 dark:text-emerald-400',
  warning: 'text-amber-600 dark:text-amber-400',
  danger: 'text-red-600 dark:text-red-400',
}
const corValor = computed(() => {
  if (!props.tone || props.tone === 'default') return ''
  const n = Number(props.value)
  if (!Number.isNaN(n) && n <= 0) return ''
  return TONS[props.tone] || ''
})
</script>

<template>
  <div class="border bg-card flex flex-col" :class="compact ? 'rounded-lg px-3 py-2 gap-0.5' : 'rounded-xl p-4 gap-2'">
    <div class="flex items-center gap-1.5 min-w-0">
      <component :is="icon" v-if="icon" class="shrink-0 text-muted-foreground" :class="compact ? 'size-3.5' : 'size-[18px]'" />
      <span class="uppercase tracking-wider font-medium text-muted-foreground truncate" :class="compact ? 'text-[10px]' : 'text-xs'" :title="label">{{ label }}</span>
    </div>
    <div class="flex items-end gap-2">
      <div class="font-semibold tracking-tight tabular-nums" :class="[compact ? 'text-lg leading-6' : 'text-2xl', corValor]">{{ value }}</div>
      <span
        v-if="delta !== undefined"
        class="inline-flex items-center gap-0.5 text-[11px] font-medium pb-1"
        :class="delta >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'"
      >
        <TrendingUp v-if="delta >= 0" class="size-3" />
        <TrendingDown v-else class="size-3" />
        {{ Math.abs(delta).toFixed(1) }}%
      </span>
    </div>
    <div v-if="hint" class="text-muted-foreground" :class="compact ? 'text-[11px] leading-4 truncate' : 'text-xs'" :title="hint">{{ hint }}</div>
  </div>
</template>
