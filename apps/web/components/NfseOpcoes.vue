<script setup lang="ts">
// Escolha única em cartões (regime tributário, motivo do cancelamento, tipo
// de tomador). reka RadioGroup: setas do teclado trocam a opção.
import { computed, type Component } from 'vue'
import { RadioGroupItem, RadioGroupRoot } from 'reka-ui'

type Valor = string | number
type Opcao = { valor: Valor; titulo: string; descricao?: string; icone?: Component; disabled?: boolean }

const props = withDefaults(
  defineProps<{
    modelValue: Valor | null
    opcoes: Opcao[]
    colunas?: 1 | 2 | 3
    tom?: 'padrao' | 'perigo'
    disabled?: boolean
  }>(),
  { colunas: 1, tom: 'padrao', disabled: false },
)

const emit = defineEmits<{ (e: 'update:modelValue', v: Valor | null): void }>()

const grade = computed(() => ({ 1: '', 2: 'sm:grid-cols-2', 3: 'sm:grid-cols-3' })[props.colunas])
const marcado = computed(() =>
  props.tom === 'perigo'
    ? 'data-[state=checked]:border-red-500/60 data-[state=checked]:bg-red-500/5'
    : 'data-[state=checked]:border-primary data-[state=checked]:bg-primary/5',
)
const ponto = computed(() => (props.tom === 'perigo' ? 'bg-red-500' : 'bg-primary'))
const aro = computed(() => (props.tom === 'perigo' ? 'border-red-500' : 'border-primary'))

function mudar(v: unknown) {
  emit('update:modelValue', (v as Valor | null) ?? null)
}
</script>

<template>
  <RadioGroupRoot
    :model-value="modelValue ?? undefined"
    :disabled="disabled"
    class="grid grid-cols-1 gap-2"
    :class="grade"
    @update:model-value="mudar"
  >
    <RadioGroupItem
      v-for="o in opcoes"
      :key="String(o.valor)"
      v-slot="{ checked }"
      :value="o.valor"
      :disabled="o.disabled"
      class="flex cursor-pointer items-start gap-3 rounded-lg border p-3 text-left transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
      :class="marcado"
    >
      <span
        class="mt-0.5 grid size-4 shrink-0 place-items-center rounded-full border"
        :class="checked && aro"
        aria-hidden="true"
      >
        <span v-if="checked" class="size-2 rounded-full" :class="ponto" />
      </span>
      <component :is="o.icone" v-if="o.icone" class="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
      <span class="min-w-0">
        <span class="block text-sm font-medium">{{ o.titulo }}</span>
        <span v-if="o.descricao" class="block text-xs text-muted-foreground">{{ o.descricao }}</span>
      </span>
    </RadioGroupItem>
  </RadioGroupRoot>
</template>
