<script setup lang="ts">
// Selo da cobertura do atendimento (§5.2): ✅ Coberto · ⛔ Fora da garantia ·
// ⏳ Sem data de entrega. `bloco` = a caixa da prévia no "Vincular à
// garantia" (o texto vem no slot). As cores moram aqui (o Tailwind não lê lib/).
import { coberturaInfo } from '~/lib/garantias'

const props = defineProps<{ cobertura: string | null | undefined; rotulo?: string | null; bloco?: boolean; pequeno?: boolean }>()
const info = computed(() => coberturaInfo(props.cobertura))
const CORES: Record<string, string> = {
  coberto: 'border-emerald-400/60 bg-emerald-50 text-emerald-800 dark:border-emerald-500/40 dark:bg-emerald-500/15 dark:text-emerald-300',
  fora_da_garantia: 'border-red-400/60 bg-red-50 text-red-800 dark:border-red-500/40 dark:bg-red-500/15 dark:text-red-300',
  sem_data_de_entrega: 'border-amber-400/60 bg-amber-50 text-amber-800 dark:border-amber-500/40 dark:bg-amber-500/15 dark:text-amber-300',
}
const cor = computed(() => CORES[props.cobertura || ''] || 'border-border bg-muted text-muted-foreground')
</script>

<template>
  <div v-if="bloco" class="flex flex-wrap items-center gap-2 rounded-md border px-3 py-2" :class="cor" data-cobertura>
    <span class="font-semibold">{{ info.emoji }} {{ rotulo || info.rotulo }}</span>
    <slot />
  </div>
  <span
    v-else
    class="inline-flex items-center gap-1 whitespace-nowrap rounded-full border font-medium"
    :class="[cor, pequeno ? 'px-1.5 text-[11px]' : 'px-2 py-0.5 text-xs']"
    data-cobertura
  ><span v-if="!pequeno" aria-hidden="true">{{ info.emoji }}</span>{{ rotulo || info.rotulo }}</span>
</template>
