<script setup lang="ts">
import { PopoverContent, PopoverPortal, PopoverRoot, PopoverTrigger } from 'reka-ui'

// "Status Atual" da aba Status com VÁRIOS estados do Bling (Vinicius 24/09: a
// mesma chave do ML com as mesmas ações em "Em aberto" e em "Em andamento"
// virava duas linhas iguais). Clicou, abre a lista de situações pra marcar;
// nenhuma marcada = a regra vale de qualquer estado. Clicar fora ou Esc fecha;
// se a seleção mudou, emite `save` (a célula da tabela grava nesse momento). O
// v-model acompanha cada clique — é o que o modal "Novo status" usa.
const props = withDefaults(defineProps<{
  modelValue: string[]
  opcoes: string[]
  disabled?: boolean
  vazio?: string
  bloco?: boolean
}>(), { disabled: false, vazio: '— qualquer status —', bloco: false })

const emit = defineEmits<{
  (e: 'update:modelValue', value: string[]): void
  (e: 'save', value: string[]): void
}>()

const open = ref(false)
const sel = ref<string[]>([])
let aoAbrir = ''

// Marcada que saiu do catálogo do Bling continua na lista (não some da regra
// sem ninguém ver); a ordem é a do catálogo.
const lista = computed(() => {
  const base = props.opcoes
  const extra = sel.value.filter((s) => !base.includes(s))
  return [...base, ...extra]
})

function ordenar(v: string[]): string[] {
  return lista.value.filter((o) => v.includes(o))
}

function setOpen(v: boolean) {
  if (props.disabled && v) return
  if (v) {
    sel.value = [...props.modelValue]
    aoAbrir = ordenar(sel.value).join('\n')
  } else if (ordenar(sel.value).join('\n') !== aoAbrir) {
    emit('save', ordenar(sel.value))
  }
  open.value = v
}

function alternar(o: string) {
  sel.value = sel.value.includes(o) ? sel.value.filter((x) => x !== o) : [...sel.value, o]
  emit('update:modelValue', ordenar(sel.value))
}

function limpar() {
  sel.value = []
  emit('update:modelValue', [])
}
</script>

<template>
  <PopoverRoot :open="open" @update:open="setOpen">
    <PopoverTrigger as-child>
      <button
        type="button"
        :disabled="disabled"
        class="flex flex-wrap items-center gap-1 text-left disabled:opacity-60"
        :class="bloco ? 'w-full min-h-9 rounded-md border bg-background px-2 py-1.5' : 'min-h-6'"
        :title="disabled ? '' : 'clique pra escolher um ou mais status'"
      >
        <template v-if="modelValue.length">
          <span
            v-for="s in modelValue"
            :key="s"
            class="text-xs px-2 py-0.5 rounded border border-border whitespace-nowrap"
          >{{ s }}</span>
        </template>
        <span v-else class="text-sm text-muted-foreground">{{ bloco ? vazio : '—' }}</span>
      </button>
    </PopoverTrigger>
    <PopoverPortal>
      <PopoverContent
        side="bottom"
        align="start"
        :side-offset="4"
        :collision-padding="8"
        class="z-[70] w-64 max-w-[calc(100vw-16px)] rounded-md border bg-background p-2 shadow-lg"
      >
        <div class="mb-1 flex items-center px-1 text-[11px] font-medium text-muted-foreground">
          Status Atual — marque um ou mais
          <button
            v-if="sel.length"
            type="button"
            class="ml-auto font-normal underline hover:text-foreground"
            @click="limpar"
          >limpar</button>
        </div>
        <div class="max-h-72 overflow-y-auto">
          <label
            v-for="o in lista"
            :key="o"
            class="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm hover:bg-muted/50"
          >
            <input type="checkbox" class="size-4" :checked="sel.includes(o)" @change="alternar(o)" />
            {{ o }}
          </label>
        </div>
        <div class="mt-1 border-t px-1 pt-1 text-[11px] text-muted-foreground">
          Nenhum marcado = vale pra qualquer status. Esc ou clicar fora fecha.
        </div>
      </PopoverContent>
    </PopoverPortal>
  </PopoverRoot>
</template>
