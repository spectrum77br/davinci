<script setup lang="ts">
// Prazo com a barra de dias restantes (§4.2): verde com folga, âmbar nos
// últimos 30 dias, vermelha (vazia) quando acabou; sem data de entrega,
// avisa que espera a entrega.
import { barraPrazo, dataBR, type Prazo } from '~/lib/garantias'

const props = defineProps<{ titulo: string; meses: number; prazo: Prazo | null | undefined }>()
const barra = computed(() => barraPrazo(props.prazo))
// As cores moram aqui (o Tailwind não lê lib/).
const COR = { folga: 'bg-emerald-500', perto: 'bg-amber-500', acabou: 'bg-red-500', aguardando: 'bg-amber-400' } as const
</script>

<template>
  <div class="space-y-1.5" data-prazo-garantia>
    <div class="flex flex-wrap items-baseline justify-between gap-x-2 text-sm">
      <span class="font-medium">{{ titulo }} <span class="text-xs font-normal text-muted-foreground">({{ meses }} {{ meses === 1 ? 'mês' : 'meses' }})</span></span>
      <span class="text-xs text-muted-foreground">até <strong class="font-semibold text-foreground">{{ prazo ? dataBR(prazo.fim) : '—' }}</strong></span>
    </div>
    <div
      class="h-2.5 w-full overflow-hidden rounded-full bg-muted"
      role="progressbar"
      :aria-label="`${titulo}: ${barra.texto}`"
      aria-valuemin="0"
      aria-valuemax="100"
      :aria-valuenow="barra.pct"
    >
      <div class="h-full rounded-full transition-[width]" :class="COR[barra.nivel]" :style="{ width: `${barra.pct}%` }" />
    </div>
    <div class="text-xs" :class="prazo && !prazo.coberto_hoje ? 'text-red-600 dark:text-red-400' : 'text-muted-foreground'">{{ barra.texto }}</div>
  </div>
</template>
