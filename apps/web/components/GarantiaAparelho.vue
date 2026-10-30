<script setup lang="ts">
// O aparelho da garantia na tela Devoluções (30/10/2026, "o que realmente foi
// perdido e o que está na manutenção"): o selo da lista e do detalhe — "No
// técnico · faltam 5 dias", "Consertado (Novo)", "Perdido (Extraviado)" (com
// o gasto em reais para o admin; o texto vem pronto do servidor). O prazo vencido (o servidor diz, pelo dia em São Paulo) fica na
// cor do perdido. Numa coluna estreita o selo corta com "…" (o texto inteiro
// no mouse).
import type { DesfechoAparelho } from '~/lib/garantias'

const props = defineProps<{ situacao?: DesfechoAparelho | string | null; rotulo?: string | null; vencido?: boolean }>()
const CORES: Record<string, string> = {
  no_tecnico: 'border-orange-400/60 bg-orange-50 text-orange-800 dark:border-orange-500/40 dark:bg-orange-500/15 dark:text-orange-300',
  consertado: 'border-teal-400/60 bg-teal-50 text-teal-800 dark:border-teal-500/40 dark:bg-teal-500/15 dark:text-teal-300',
  perdido: 'border-rose-400/60 bg-rose-50 text-rose-800 dark:border-rose-500/40 dark:bg-rose-500/15 dark:text-rose-300',
  // Voltou sem passar pela manutenção (ou ainda sem a condição lançada).
  devolvido: 'border-slate-300 bg-slate-50 text-slate-700 dark:border-slate-600 dark:bg-slate-500/15 dark:text-slate-300',
  // Seguiu fora da tela Devoluções: a devolução aceita na plataforma (o
  // aparelho voltando) e o reembolso ao cliente.
  a_caminho: 'border-indigo-300 bg-indigo-50 text-indigo-800 dark:border-indigo-500/40 dark:bg-indigo-500/15 dark:text-indigo-300',
  reembolso: 'border-yellow-300 bg-yellow-50 text-yellow-800 dark:border-yellow-500/40 dark:bg-yellow-500/15 dark:text-yellow-300',
}
const cor = computed(() => CORES[props.vencido ? 'perdido' : props.situacao || ''] || 'border-border bg-muted text-muted-foreground')
// De onde veio (revisão 30/10): o reembolso e o "a caminho" não estão na tela
// Devoluções — vêm do Reembolso ou da plataforma.
const ORIGEM: Record<string, string> = {
  reembolso: 'reembolso ao cliente (tela Reembolso ou pago pela plataforma)',
  a_caminho: 'devolução aceita na plataforma, o aparelho está voltando (ainda não lançado na tela Devoluções)',
}
const dica = computed(() => `${props.rotulo} — ${ORIGEM[props.situacao || ''] || 'o aparelho desta garantia na tela Devoluções'}`)
</script>

<template>
  <span
    v-if="rotulo"
    class="inline-block max-w-full truncate rounded-full border px-2 py-0.5 text-xs font-medium"
    :class="cor"
    :title="dica"
    :data-aparelho="situacao || ''"
  >{{ rotulo }}</span>
</template>
