<script setup lang="ts">
// Escolha do tomador (quem recebe a nota), com busca e o atalho "+ cadastrar
// novo tomador": abre o formulário por cima e já escolhe o que foi salvo.
// O tomador do grupo que é a própria empresa que emite fica desabilitado.
import { computed, nextTick, ref, watch } from 'vue'
import { PopoverContent, PopoverPortal, PopoverRoot, PopoverTrigger } from 'reka-ui'
import { Check, ChevronDown, Plus, Search } from 'lucide-vue-next'
import { fmtDoc, soDigitos, useNfseTela, type Tomador } from '~/lib/nfse'

const props = withDefaults(
  defineProps<{
    modelValue: string | null
    excluirEmpresaId?: string | null
    placeholder?: string
    disabled?: boolean
    invalido?: boolean
    id?: string
  }>(),
  { excluirEmpresaId: null, placeholder: 'escolha quem recebe…', disabled: false, invalido: false },
)

const emit = defineEmits<{ (e: 'update:modelValue', v: string | null): void }>()

const tela = useNfseTela()
const aberto = ref(false)
const busca = ref('')
const ativo = ref(0)
const campoBusca = ref<HTMLInputElement | null>(null)
const lista = ref<HTMLElement | null>(null)

const escolhido = computed(() => tela.tomadores.value.find((t) => t.id === props.modelValue) ?? null)

function nome(t: Tomador) {
  return t.nome_nota || t.nome || '(sem nome)'
}

function bloqueado(t: Tomador): boolean {
  return t.tipo === 'grupo' && !!props.excluirEmpresaId && t.company_id === props.excluirEmpresaId
}

const opcoes = computed(() => {
  const q = busca.value.trim().toLowerCase()
  const dq = soDigitos(q)
  return tela.tomadores.value
    .filter((t) => t.ativo || t.id === props.modelValue)
    .filter((t) => {
      if (!q) return true
      return nome(t).toLowerCase().includes(q) || (!!dq && soDigitos(t.documento_nota || t.documento).includes(dq))
    })
    .sort((a, b) => nome(a).localeCompare(nome(b), 'pt-BR'))
})

function proximoLivre(de: number, passo: 1 | -1): number {
  const n = opcoes.value.length
  for (let k = 1; k <= n; k++) {
    const i = (de + passo * k + n * k) % n
    const t = opcoes.value[i]
    if (t && !bloqueado(t)) return i
  }
  return de
}

watch(aberto, (v) => {
  if (!v) return
  busca.value = ''
  const i = opcoes.value.findIndex((t) => t.id === props.modelValue)
  ativo.value = i >= 0 ? i : 0
  nextTick(rolarAtivo)
})

watch(opcoes, () => {
  if (ativo.value >= opcoes.value.length) ativo.value = Math.max(0, opcoes.value.length - 1)
})

function escolher(t: Tomador) {
  if (bloqueado(t)) return
  emit('update:modelValue', t.id)
  aberto.value = false
}

function rolarAtivo() {
  lista.value?.querySelector<HTMLElement>(`[data-i="${ativo.value}"]`)?.scrollIntoView({ block: 'nearest' })
}

function teclas(ev: KeyboardEvent) {
  if (!opcoes.value.length) return
  if (ev.key === 'ArrowDown') {
    ev.preventDefault()
    ativo.value = proximoLivre(ativo.value, 1)
    nextTick(rolarAtivo)
  } else if (ev.key === 'ArrowUp') {
    ev.preventDefault()
    ativo.value = proximoLivre(ativo.value, -1)
    nextTick(rolarAtivo)
  } else if (ev.key === 'Enter') {
    ev.preventDefault()
    const t = opcoes.value[ativo.value]
    if (t) escolher(t)
  }
}

function focarBusca(ev: Event) {
  ev.preventDefault()
  campoBusca.value?.focus()
}

async function cadastrarNovo() {
  aberto.value = false
  const t = await tela.abrirTomador()
  if (t) emit('update:modelValue', t.id)
}
</script>

<template>
  <PopoverRoot v-model:open="aberto">
    <PopoverTrigger as-child :disabled="disabled">
      <button
        :id="id"
        type="button"
        role="combobox"
        :aria-expanded="aberto"
        aria-haspopup="listbox"
        :aria-invalid="invalido ? 'true' : undefined"
        :disabled="disabled"
        class="inline-flex h-9 w-full items-center justify-between gap-2 rounded-md border bg-background px-3 text-left text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
        :class="invalido && 'border-red-500 dark:border-red-400'"
      >
        <span v-if="escolhido" class="min-w-0 truncate">
          {{ nome(escolhido) }}
          <span class="ml-1 text-xs text-muted-foreground tabular-nums">{{ fmtDoc(escolhido.documento_nota || escolhido.documento) }}</span>
        </span>
        <span v-else class="truncate text-muted-foreground">{{ placeholder }}</span>
        <ChevronDown class="size-4 shrink-0 opacity-50" aria-hidden="true" />
      </button>
    </PopoverTrigger>
    <PopoverPortal>
      <PopoverContent
        side="bottom"
        align="start"
        :side-offset="4"
        :collision-padding="8"
        class="z-[80] flex w-[var(--reka-popover-trigger-width)] min-w-[300px] max-w-[calc(100vw-16px)] flex-col rounded-md border bg-background p-0 shadow-lg duration-150 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=open]:fade-in-0 data-[state=closed]:fade-out-0 motion-reduce:animate-none"
        @open-auto-focus="focarBusca"
      >
        <div class="relative border-b">
          <Search class="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
          <input
            ref="campoBusca"
            v-model="busca"
            type="text"
            autocomplete="off"
            placeholder="buscar nome ou CNPJ/CPF…"
            aria-label="buscar tomador"
            class="h-9 w-full bg-transparent pl-9 pr-3 text-sm focus-visible:outline-none"
            @keydown="teclas"
          />
        </div>
        <ul ref="lista" role="listbox" class="max-h-64 overflow-y-auto p-1">
          <li
            v-for="(t, i) in opcoes"
            :key="t.id"
            :data-i="i"
            role="option"
            :aria-selected="t.id === modelValue"
            :aria-disabled="bloqueado(t) ? 'true' : undefined"
            class="flex items-center gap-2 rounded px-2 py-1.5 text-sm"
            :class="[
              bloqueado(t) ? 'cursor-not-allowed opacity-50' : 'cursor-pointer',
              i === ativo && !bloqueado(t) ? 'bg-muted' : !bloqueado(t) && 'hover:bg-muted/60',
            ]"
            @mouseenter="!bloqueado(t) && (ativo = i)"
            @click="escolher(t)"
          >
            <div class="min-w-0 flex-1">
              <div class="truncate font-medium">{{ nome(t) }}</div>
              <div class="truncate text-xs text-muted-foreground tabular-nums">
                <template v-if="bloqueado(t)">é a própria empresa que emite</template>
                <template v-else>{{ fmtDoc(t.documento_nota || t.documento) }}</template>
              </div>
            </div>
            <span :class="t.tipo === 'grupo' ? 'pill-info' : 'pill-muted'" class="shrink-0">
              {{ t.tipo === 'grupo' ? 'Do grupo' : 'De fora' }}
            </span>
            <Check v-if="t.id === modelValue" class="size-4 shrink-0 text-primary" aria-hidden="true" />
          </li>
          <li v-if="!opcoes.length" class="px-2 py-6 text-center text-sm text-muted-foreground">
            Nenhum tomador encontrado.
          </li>
        </ul>
        <button
          v-if="tela.canEdit.value"
          type="button"
          class="flex w-full items-center gap-1.5 border-t px-3 py-2 text-left text-sm font-medium text-primary hover:bg-muted/60 focus-visible:bg-muted/60 focus-visible:outline-none"
          @click="cadastrarNovo"
        >
          <Plus class="size-4" aria-hidden="true" /> cadastrar novo tomador
        </button>
      </PopoverContent>
    </PopoverPortal>
  </PopoverRoot>
</template>
