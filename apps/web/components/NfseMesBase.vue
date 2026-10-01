<script setup lang="ts">
// "Base do %: faturamento de [mês]" (01/10/2026, Eduardo: "como virou o mês, o
// faturamento de outubro está zerado ainda… precisa ter a opção de eu escolher o
// mês, por exemplo setembro"). Só a BASE das notas de percentual muda de mês: a
// nota continua com o mês de competência dela. Opções: o mesmo mês da nota
// (padrão, valor '') e os 3 anteriores. v-model 'AAAA-MM' ('' = o mês da nota).
// Fora do mês da nota, o seletor fica destacado e diz de que mês é a nota.
import { computed } from 'vue'
import { ChevronDown, Info } from 'lucide-vue-next'
import { fmtMes, opcoesMesBase } from '~/lib/nfse'

const props = withDefaults(
  defineProps<{
    modelValue: string
    competencia: string // 'AAAA-MM': o mês da nota
    disabled?: boolean
    id?: string
  }>(),
  { disabled: false, id: 'nfse-mes-base' },
)

const emit = defineEmits<{ (e: 'update:modelValue', v: string): void }>()

const opcoes = computed(() => opcoesMesBase(props.competencia))
// Mês que não está nas opções (ex.: o mês da nota acabou de mudar) vale o da nota.
const valor = computed(() => (opcoes.value.some((o) => o.valor === props.modelValue) ? props.modelValue : ''))
const outroMes = computed(() => !!valor.value)

function mudar(ev: Event) {
  emit('update:modelValue', (ev.target as HTMLSelectElement).value)
}
</script>

<template>
  <div class="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-sm">
    <label :for="id" class="whitespace-nowrap text-muted-foreground">Base do %: faturamento de</label>
    <div class="relative">
      <select
        :id="id"
        :value="valor"
        :disabled="disabled"
        class="h-9 appearance-none rounded-md border bg-background pl-3 pr-8 text-sm tabular-nums focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50 dark:[color-scheme:dark]"
        :class="outroMes && 'border-primary bg-primary/5 font-medium'"
        @change="mudar"
      >
        <option v-for="o in opcoes" :key="o.valor" :value="o.valor">{{ o.rotulo }}</option>
      </select>
      <ChevronDown
        class="pointer-events-none absolute right-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
        aria-hidden="true"
      />
    </div>
    <NfseDica
      texto="De que mês vem o faturamento usado como base das notas de percentual (ex.: no dia 1º o mês novo ainda está zerado). A nota continua sendo do mês dela. Notas de valor fixo não mudam."
    >
      <button
        type="button"
        class="grid size-8 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        aria-label="o que é o mês da base"
      >
        <Info class="size-4" aria-hidden="true" />
      </button>
    </NfseDica>
    <span v-if="outroMes" class="text-xs text-muted-foreground">
      (a nota continua sendo de {{ fmtMes(competencia) }})
    </span>
  </div>
</template>
