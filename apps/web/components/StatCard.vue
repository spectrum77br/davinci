<script setup lang="ts">
import { TrendingUp, TrendingDown } from 'lucide-vue-next'

const props = defineProps<{
  label: string
  value: string | number
  delta?: number
  hint?: string
  icon?: any
  tone?: 'default' | 'success' | 'warning' | 'danger'
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
  <div class="rounded-xl border bg-card p-4 flex flex-col gap-2">
    <div class="flex items-center gap-2">
      <component :is="icon" v-if="icon" class="size-[18px] text-muted-foreground" />
      <span class="text-xs uppercase tracking-wider font-medium text-muted-foreground">{{ label }}</span>
    </div>
    <div class="flex items-end gap-2">
      <div class="text-2xl font-semibold tracking-tight tabular-nums" :class="corValor">{{ value }}</div>
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
    <div v-if="hint" class="text-xs text-muted-foreground">{{ hint }}</div>
  </div>
</template>
