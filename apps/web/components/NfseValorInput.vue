<script setup lang="ts">
// Campo de dinheiro em pt-BR. v-model é o decimal da API ('1500.00'; '' =
// vazio ou inválido). Aceita "1.500,00", "1500,00", "1500.00", "1500" e
// "200.000" (ponto de milhar = 200 mil).
// Compacto (dentro da tabela): parece texto; clicou, vira campo. Enter ou sair
// confirma, Esc desfaz. Diferente do `padrao`: mostra "padrão R$ X · voltar"
// (`rotuloPadrao` troca o "padrão"; `rotulo` diz o que o campo é: "valor" ou
// "base"). Número que não dá pra ler não some calado: fica em vermelho com
// "Não entendi o número" (no compacto o valor anterior continua valendo).
import { computed, nextTick, ref, watch } from 'vue'
import { Pencil } from 'lucide-vue-next'
import { fmtBrl, paraDecimal, TOM_TEXTO } from '~/lib/nfse'

const props = withDefaults(
  defineProps<{
    modelValue: string
    padrao?: string | null
    compacto?: boolean
    invalido?: boolean
    disabled?: boolean
    placeholder?: string
    id?: string
    rotulo?: string // o que o campo é: 'valor' (padrão) ou 'base'
    rotuloPadrao?: string // "padrão R$ X · voltar"
  }>(),
  {
    padrao: null, compacto: false, invalido: false, disabled: false, placeholder: 'R$ 0,00',
    rotulo: 'valor', rotuloPadrao: 'padrão',
  },
)

const emit = defineEmits<{ (e: 'update:modelValue', v: string): void }>()

// '1500.00' → '1.500,00'
function paraTexto(v: string | null | undefined): string {
  const d = paraDecimal(v)
  if (!d) return v ? String(v) : ''
  return Number(d).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

const texto = ref(paraTexto(props.modelValue))
const editando = ref(false)
const campo = ref<HTMLInputElement | null>(null)
const naoEntendi = ref(false) // digitou algo que não é número
let cancelou = false

// Mexeu no texto (digitou, Esc, abriu de novo): o aviso sai.
watch(texto, () => {
  naoEntendi.value = false
})

const oQue = computed(() => (props.rotulo === 'valor' ? 'o valor' : `a ${props.rotulo}`))

watch(
  () => props.modelValue,
  (v) => {
    if (!editando.value || !props.compacto) {
      if (paraDecimal(texto.value) !== paraDecimal(v)) texto.value = paraTexto(v)
    }
  },
)

const mudouDoPadrao = computed(
  () => !!props.padrao && !!paraDecimal(props.modelValue) && paraDecimal(props.modelValue) !== paraDecimal(props.padrao),
)

function confirmar() {
  const bruto = texto.value.trim()
  const d = paraDecimal(bruto)
  if (bruto && !d) {
    // No compacto o valor anterior continua (o botão mostra ele); no normal o
    // campo fica vazio para o formulário não salvar um número que não é o digitado.
    if (!props.compacto && props.modelValue) emit('update:modelValue', '')
    naoEntendi.value = true
    return
  }
  if (d !== props.modelValue) emit('update:modelValue', d)
  texto.value = d ? paraTexto(d) : ''
}

function aoSair() {
  if (props.compacto) {
    if (cancelou) {
      cancelou = false
    } else {
      confirmar()
    }
    editando.value = false
    return
  }
  confirmar()
}

function abrir() {
  if (props.disabled) return
  texto.value = paraTexto(props.modelValue)
  editando.value = true
  nextTick(() => {
    campo.value?.focus()
    campo.value?.select()
  })
}

function teclas(ev: KeyboardEvent) {
  if (ev.key === 'Enter') {
    ev.preventDefault()
    if (props.compacto) campo.value?.blur()
    else confirmar()
  } else if (ev.key === 'Escape' && props.compacto) {
    ev.preventDefault()
    ev.stopPropagation()
    cancelou = true
    texto.value = paraTexto(props.modelValue)
    campo.value?.blur()
  }
}

function voltarAoPadrao() {
  if (!props.padrao) return
  const d = paraDecimal(props.padrao)
  texto.value = paraTexto(d)
  emit('update:modelValue', d)
}

const vermelho = computed(() => props.invalido || naoEntendi.value)
const borda = computed(() => (vermelho.value ? 'border-red-500 dark:border-red-400' : ''))
</script>

<template>
  <!-- Normal -->
  <div v-if="!props.compacto" class="inline-flex flex-col gap-1">
    <div class="relative inline-flex">
      <span class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-muted-foreground">R$</span>
      <input
        :id="id"
        v-model="texto"
        type="text"
        inputmode="decimal"
        autocomplete="off"
        :placeholder="placeholder.replace(/^R\$\s*/, '')"
        :disabled="disabled"
        :aria-invalid="vermelho ? 'true' : undefined"
        class="h-9 w-40 rounded-md border bg-background pl-9 pr-3 text-right text-sm tabular-nums focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
        :class="borda"
        @blur="aoSair"
        @keydown="teclas"
      />
    </div>
    <p v-if="naoEntendi" class="text-xs" :class="TOM_TEXTO.perigo" role="alert">
      Não entendi o número. Ex.: 200.000,00
    </p>
  </div>

  <!-- Compacto (tabela) -->
  <div v-else class="inline-flex flex-col items-end">
    <input
      v-if="editando"
      :id="id"
      ref="campo"
      v-model="texto"
      type="text"
      inputmode="decimal"
      autocomplete="off"
      :aria-invalid="vermelho ? 'true' : undefined"
      :aria-label="rotulo === 'valor' ? 'valor da nota' : rotulo"
      class="h-8 w-32 rounded-md border bg-background px-2 text-right text-sm tabular-nums focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      :class="borda"
      @click.stop
      @blur="aoSair"
      @keydown="teclas"
    />
    <button
      v-else
      :id="id"
      type="button"
      class="group inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-right text-sm tabular-nums hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-default disabled:hover:bg-transparent"
      :class="vermelho ? 'text-red-600 ring-1 ring-red-500/50 dark:text-red-400' : ''"
      :disabled="disabled"
      :title="disabled ? undefined : `clique para mudar ${oQue} deste mês`"
      :aria-label="`${rotulo}: ${modelValue ? fmtBrl(modelValue) : 'vazio'}. Clique para mudar.`"
      @click.stop="abrir"
    >
      <Pencil v-if="!disabled" class="size-3 opacity-0 transition-opacity group-hover:opacity-60 group-focus-visible:opacity-60" aria-hidden="true" />
      <span v-if="modelValue">{{ fmtBrl(modelValue) }}</span>
      <span v-else class="text-muted-foreground">{{ placeholder }}</span>
    </button>
    <span v-if="naoEntendi && !editando" class="mt-0.5 text-[11px]" :class="TOM_TEXTO.perigo" role="alert">
      Não entendi o número. Ex.: 200.000,00
    </span>
    <span v-if="mudouDoPadrao && !editando" class="mt-0.5 inline-flex items-center gap-1 text-[11px] text-muted-foreground">
      <span class="size-1.5 rounded-full bg-primary" aria-hidden="true" />
      {{ rotuloPadrao }} {{ fmtBrl(padrao) }} ·
      <button
        type="button"
        class="text-primary underline-offset-2 hover:underline disabled:opacity-50"
        :disabled="disabled"
        @click.stop="voltarAoPadrao"
      >voltar</button>
    </span>
  </div>
</template>
