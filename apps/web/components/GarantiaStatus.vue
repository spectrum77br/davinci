<script setup lang="ts">
// Selo do status da garantia com a cor do documento (§2) — sem emoji
// (29/10/2026: "igual aos nossos painéis do DaVinci"): Aguardando entrega
// (âmbar) · Ativa (verde) · Expirada (vermelho) · Prazo não cadastrado
// (laranja; desde 06/11/2026 o prazo é único, por produto — o "Somente
// software" saiu) · Sem garantia (cinza; o prazo 0, o produto vendido sem
// garantia).
// `entregueSemData`: o "Aguardando entrega" de pedido que o Bling já dá como
// entregue vira "Entregue — sem data no DaVinci".
import { statusInfo } from '~/lib/garantias'

const props = defineProps<{ status: string | null | undefined; entregueSemData?: boolean }>()
const info = computed(() => statusInfo(props.status, !!props.entregueSemData))
// As cores moram aqui (o Tailwind não lê lib/).
const CORES: Record<string, string> = {
  aguardando_entrega: 'border-amber-400/60 bg-amber-50 text-amber-800 dark:border-amber-500/40 dark:bg-amber-500/15 dark:text-amber-300',
  ativa: 'border-emerald-400/60 bg-emerald-50 text-emerald-800 dark:border-emerald-500/40 dark:bg-emerald-500/15 dark:text-emerald-300',
  sem_prazo: 'border-orange-400/60 bg-orange-50 text-orange-800 dark:border-orange-500/40 dark:bg-orange-500/15 dark:text-orange-300',
  expirada: 'border-red-400/60 bg-red-50 text-red-800 dark:border-red-500/40 dark:bg-red-500/15 dark:text-red-300',
  sem_garantia: 'border-slate-400/60 bg-slate-100 text-slate-700 dark:border-slate-500/40 dark:bg-slate-500/15 dark:text-slate-300',
}
const cor = computed(() => CORES[props.status || ''] || 'border-border bg-muted text-muted-foreground')
</script>

<template>
  <span
    class="inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium"
    :class="cor"
    :title="info.dica"
    :data-entregue-sem-data="entregueSemData && status === 'aguardando_entrega' ? '' : undefined"
    data-status-garantia
  >{{ info.rotulo }}</span>
</template>
