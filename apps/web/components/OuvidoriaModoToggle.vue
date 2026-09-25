<script lang="ts">
// Modo do robô (spec Ouvidoria 21/09): 'ligado' roda, registra e avisa no
// Threema; 'silencioso' roda e registra, mas não avisa; 'desligado' nem roda.
export type ModoRobo = 'ligado' | 'silencioso' | 'desligado'

export const MODO_LABEL: Record<ModoRobo, string> = {
  ligado: 'ligado',
  silencioso: 'silencioso',
  desligado: 'desligado',
}
</script>

<script setup lang="ts">
import { computed } from 'vue'

// Interruptor de 3 posições (desligado · silencioso · ligado), no desenho
// aprovado: bolinha à esquerda = desligado (cinza), no meio = silencioso
// (âmbar), à direita = ligado (verde). Cada terço da pista é um botão
// próprio — clicar escolhe a posição direto, sem ter que "passar" pelo modo
// do meio. Desligar pede confirmação, porque o robô para de registrar
// ocorrências e ninguém mais fica sabendo do que ele vigiava. Robô cujo modo
// tem efeito além disso (o da Margem mexe em pedido) manda a frase em
// `avisos` — e aí o modo que tiver frase pede confirmação com ela.
const props = withDefaults(defineProps<{
  modelValue: ModoRobo
  // Nome do robô — só pra frase do confirm().
  nome: string
  // modo → o que muda ao passar pra ele (vem do catálogo: `avisos_modo`).
  avisos?: Partial<Record<ModoRobo, string>>
  disabled?: boolean
  busy?: boolean
}>(), { disabled: false, busy: false, avisos: () => ({}) })

const emit = defineEmits<{
  (e: 'change', modo: ModoRobo): void
}>()

const POSICOES: { modo: ModoRobo; dica: string }[] = [
  { modo: 'desligado', dica: 'Desligar: não roda, não registra, não avisa' },
  { modo: 'silencioso', dica: 'Silencioso: roda e registra ocorrências, mas não avisa no Threema' },
  { modo: 'ligado', dica: 'Ligado: roda, registra e avisa' },
]

const trilha = computed(() => {
  if (props.modelValue === 'ligado') return 'bg-emerald-500'
  if (props.modelValue === 'silencioso') return 'bg-amber-500'
  return 'bg-gray-300 dark:bg-gray-600'
})

// 52px de pista, bolinha de 16px: 2px · 18px · 34px.
const bolinha = computed(() => {
  if (props.modelValue === 'ligado') return 'translate-x-[34px]'
  if (props.modelValue === 'silencioso') return 'translate-x-[18px]'
  return 'translate-x-0.5'
})

function escolher(modo: ModoRobo) {
  if (props.disabled || props.busy || modo === props.modelValue) return
  const aviso = props.avisos?.[modo]
  if (aviso) {
    const verbo = modo === 'desligado' ? 'Desligar' : modo === 'silencioso' ? 'Deixar em silencioso' : 'Ligar'
    if (!confirm(`${verbo} o robô "${props.nome}"?\n\n${aviso}`)) return
  } else if (
    modo === 'desligado'
    && !confirm(`Desligar o robô "${props.nome}"?\n\nEle para de rodar e de registrar ocorrências até alguém religar. As ocorrências abertas continuam na lista.`)
  ) return
  emit('change', modo)
}
</script>

<template>
  <div class="flex flex-col items-start gap-0.5">
    <div
      class="relative h-5 w-[52px] shrink-0 rounded-full transition-colors"
      :class="[trilha, disabled ? 'opacity-60' : '', busy ? 'animate-pulse' : '']"
      role="radiogroup"
      :aria-label="`Modo do robô ${nome}`"
    >
      <button
        v-for="(p, i) in POSICOES"
        :key="p.modo"
        type="button"
        role="radio"
        :aria-checked="modelValue === p.modo"
        :title="p.dica"
        :disabled="disabled || busy"
        class="absolute inset-y-0 w-1/3 rounded-full disabled:cursor-default"
        :class="disabled ? '' : 'cursor-pointer'"
        :style="{ left: `${i * 33.33}%` }"
        @click="escolher(p.modo)"
      />
      <span
        class="pointer-events-none absolute top-0.5 left-0 inline-block size-4 rounded-full bg-white shadow transition-transform"
        :class="bolinha"
      />
    </div>
    <span class="text-[11px] text-muted-foreground whitespace-nowrap">{{ MODO_LABEL[modelValue] }}</span>
  </div>
</template>
