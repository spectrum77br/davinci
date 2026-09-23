<script setup lang="ts">
import { PopoverContent, PopoverPortal, PopoverRoot, PopoverTrigger } from 'reka-ui'

// Balão da Observação (Vinicius 18/09: "fica muita coisa escrita e o campo é
// grande, fica ruim de ver"). A célula mostra só o começo do texto com "…";
// clicou, abre um balão por cima da tabela com o texto inteiro, editável.
// Esc, Ctrl+Enter ou clicar fora fecha; se o texto mudou, emite `save` — o
// mesmo papel do @change do input de uma linha que ficava na célula. Quem não
// pode editar também abre, só pra ler. 19/09: também nas colunas Última
// localização e Observação da tela Devoluções — `dica` é uma linha miúda
// abaixo do título (ex.: a entrega original, que ficava no title do input).
const props = withDefaults(defineProps<{
  modelValue: string | null
  disabled?: boolean
  placeholder?: string
  titulo?: string
  dica?: string
}>(), { disabled: false, placeholder: '', titulo: 'Observação', dica: '' })

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
  (e: 'save'): void
}>()

const open = ref(false)
const textoAoAbrir = ref('')
const caixa = ref<HTMLTextAreaElement | null>(null)

function setOpen(v: boolean) {
  if (v) textoAoAbrir.value = props.modelValue || ''
  else if (!props.disabled && (props.modelValue || '') !== textoAoAbrir.value) emit('save')
  open.value = v
}

// O foco vai pra caixa com o cursor no fim (o padrão deixava no começo).
function focarCaixa(e: Event) {
  e.preventDefault()
  const el = caixa.value
  if (!el) return
  el.focus()
  const fim = el.value.length
  el.setSelectionRange(fim, fim)
}
</script>

<template>
  <PopoverRoot :open="open" @update:open="setOpen">
    <PopoverTrigger as-child>
      <button
        type="button"
        class="block h-7 w-full truncate rounded-none px-1 text-left text-xs hover:bg-background focus:outline-none focus:ring-1 focus:ring-primary"
        :title="modelValue || (disabled ? '' : 'clique pra escrever')"
      >
        <span v-if="modelValue">{{ modelValue }}</span>
        <span v-else class="text-muted-foreground/60">{{ placeholder }}</span>
      </button>
    </PopoverTrigger>
    <PopoverPortal>
      <PopoverContent
        side="bottom"
        align="start"
        :side-offset="4"
        :collision-padding="8"
        class="z-[70] w-[380px] max-w-[calc(100vw-16px)] rounded-md border bg-background p-2 shadow-lg"
        @open-auto-focus="focarCaixa"
      >
        <div class="mb-1 text-[11px] font-medium text-muted-foreground">{{ titulo }}</div>
        <div v-if="dica" class="mb-1 text-[11px] text-muted-foreground">{{ dica }}</div>
        <textarea
          ref="caixa"
          :value="modelValue || ''"
          :readonly="disabled"
          rows="6"
          class="w-full resize-y rounded-md border bg-background px-2 py-1.5 text-sm read-only:opacity-70"
          :placeholder="placeholder"
          @input="emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)"
          @keydown.ctrl.enter.prevent="setOpen(false)"
          @keydown.meta.enter.prevent="setOpen(false)"
        />
        <div class="mt-1 text-[11px] text-muted-foreground">{{ disabled ? 'Esc ou clicar fora fecha' : 'Esc, Ctrl+Enter ou clicar fora fecha e salva' }}</div>
      </PopoverContent>
    </PopoverPortal>
  </PopoverRoot>
</template>
