<script setup lang="ts">
// CNPJ/CPF com máscara ao digitar e conferência do dígito verificador.
// v-model = só dígitos. Emite `valido` (true/false; null = ainda incompleto).
import { computed, ref, watch } from 'vue'
import { docTipo, docValido, mascaraDoc, soDigitos, TOM_TEXTO } from '~/lib/nfse'

const props = withDefaults(
  defineProps<{
    modelValue: string | null
    disabled?: boolean
    id?: string
    invalido?: boolean
  }>(),
  { disabled: false, invalido: false },
)

const emit = defineEmits<{
  (e: 'update:modelValue', v: string): void
  (e: 'valido', v: boolean | null): void
}>()

const texto = ref(mascaraDoc(props.modelValue ?? ''))

watch(
  () => props.modelValue,
  (v) => {
    if (soDigitos(v) !== soDigitos(texto.value)) texto.value = mascaraDoc(v ?? '')
  },
)

const digitos = computed(() => soDigitos(texto.value))
const tipo = computed(() => docTipo(digitos.value))
// null = incompleto (ou vazio); true/false = número completo, válido ou não.
const estado = computed<boolean | null>(() => {
  const n = digitos.value.length
  if (n === 11 || n === 14) {
    // 11 dígitos podem ser o começo de um CNPJ: só acusa erro de CPF se não for válido como CPF.
    return docValido(digitos.value)
  }
  return null
})

// 11 dígitos que não fecham como CPF podem ser um CNPJ pela metade: sem vermelho.
const erroVisivel = computed(() => props.invalido || (estado.value === false && digitos.value.length === 14))

watch(estado, (v) => emit('valido', v), { immediate: true })

function aoDigitar(ev: Event) {
  const el = ev.target as HTMLInputElement
  const d = soDigitos(el.value).slice(0, 14)
  texto.value = mascaraDoc(d)
  el.value = texto.value
  emit('update:modelValue', d)
}
</script>

<template>
  <div class="space-y-1">
    <input
      :id="id"
      :value="texto"
      type="text"
      inputmode="numeric"
      autocomplete="off"
      maxlength="18"
      placeholder="só os números do CNPJ ou CPF"
      :disabled="disabled"
      :aria-invalid="erroVisivel ? 'true' : undefined"
      class="h-9 w-full rounded-md border bg-background px-3 text-sm tabular-nums focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
      :class="erroVisivel && 'border-red-500 dark:border-red-400'"
      @input="aoDigitar"
    />
    <p v-if="estado === true" class="text-xs" :class="TOM_TEXTO.sucesso">{{ tipo }} válido</p>
    <p v-else-if="estado === false && digitos.length === 14" class="text-xs" :class="TOM_TEXTO.perigo">
      Número inválido: confira os dígitos.
    </p>
    <p v-else-if="estado === false" class="text-xs text-muted-foreground">
      CPF inválido — se for CNPJ, continue digitando.
    </p>
    <p v-else-if="digitos.length" class="text-xs text-muted-foreground">faltam dígitos</p>
  </div>
</template>
