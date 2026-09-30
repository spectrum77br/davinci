<script setup lang="ts">
// Escolha da empresa do grupo (combobox com busca). Busca por apelido, razão
// social ou dígitos do CNPJ; ↑/↓/Enter/Esc. Com `mostrarProntidao`, cada
// opção diz se a empresa já pode emitir ou o que falta (e o selo TESTE da
// empresa em Teste na NFE.io).
import { computed, nextTick, ref, watch } from 'vue'
import { PopoverContent, PopoverPortal, PopoverRoot, PopoverTrigger } from 'reka-ui'
import { Check, ChevronDown, Search } from 'lucide-vue-next'
import { fmtDoc, minusculo, pendenciaTexto, soDigitos, useNfseTela, type Prestador } from '~/lib/nfse'

const props = withDefaults(
  defineProps<{
    modelValue: string | null
    // 'ligadas' = só as ligadas à NFE.io (as que podem emitir); 'uteis' = ligadas
    // ou com serviço prestado salvo.
    filtro?: 'uteis' | 'ligadas' | 'com_fiscal' | 'com_cnpj' | 'todas'
    mostrarProntidao?: boolean
    excluirId?: string | null
    placeholder?: string
    disabled?: boolean
    invalido?: boolean
    id?: string
  }>(),
  { filtro: 'uteis', mostrarProntidao: false, excluirId: null, placeholder: 'escolha a empresa…', disabled: false, invalido: false },
)

const emit = defineEmits<{ (e: 'update:modelValue', v: string | null): void }>()

const tela = useNfseTela()
const aberto = ref(false)
const busca = ref('')
const ativo = ref(0)
const campoBusca = ref<HTMLInputElement | null>(null)
const lista = ref<HTMLElement | null>(null)

const escolhida = computed(() => tela.prestadores.value.find((p) => p.company_id === props.modelValue) ?? null)

function passaFiltro(p: Prestador): boolean {
  if (props.filtro === 'ligadas') return p.nfeio != null
  if (props.filtro === 'com_fiscal') return p.fiscal != null
  if (props.filtro === 'com_cnpj') return !!p.cnpj
  if (props.filtro === 'uteis') return p.fiscal != null || p.nfeio != null
  return true
}

const opcoes = computed(() => {
  const q = busca.value.trim().toLowerCase()
  const dq = soDigitos(q)
  return tela.prestadores.value
    .filter((p) => p.company_id !== props.excluirId)
    .filter((p) => p.company_id === props.modelValue || passaFiltro(p))
    .filter((p) => {
      if (!q) return true
      return (
        p.apelido.toLowerCase().includes(q) ||
        (p.razao_social || '').toLowerCase().includes(q) ||
        (!!dq && soDigitos(p.cnpj).includes(dq))
      )
    })
})

watch(opcoes, () => {
  if (ativo.value >= opcoes.value.length) ativo.value = Math.max(0, opcoes.value.length - 1)
})

watch(aberto, (v) => {
  if (!v) return
  busca.value = ''
  const i = opcoes.value.findIndex((p) => p.company_id === props.modelValue)
  ativo.value = i >= 0 ? i : 0
  nextTick(() => rolarAtivo())
})

function primeiraPendencia(p: Prestador): string | null {
  const x = p.pendencias?.[0]
  return x ? minusculo(pendenciaTexto(x).texto) : null
}

function escolher(p: Prestador) {
  emit('update:modelValue', p.company_id)
  aberto.value = false
}

function rolarAtivo() {
  lista.value?.querySelector<HTMLElement>(`[data-i="${ativo.value}"]`)?.scrollIntoView({ block: 'nearest' })
}

function teclas(ev: KeyboardEvent) {
  const n = opcoes.value.length
  if (ev.key === 'ArrowDown') {
    ev.preventDefault()
    if (n) ativo.value = (ativo.value + 1) % n
    nextTick(rolarAtivo)
  } else if (ev.key === 'ArrowUp') {
    ev.preventDefault()
    if (n) ativo.value = (ativo.value - 1 + n) % n
    nextTick(rolarAtivo)
  } else if (ev.key === 'Enter') {
    ev.preventDefault()
    const p = opcoes.value[ativo.value]
    if (p) escolher(p)
  }
}

function focarBusca(ev: Event) {
  ev.preventDefault()
  campoBusca.value?.focus()
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
        <span v-if="escolhida" class="min-w-0 truncate">
          {{ escolhida.apelido }}
          <span class="ml-1 text-xs text-muted-foreground tabular-nums">{{ fmtDoc(escolhida.cnpj) }}</span>
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
        class="z-[80] w-[var(--reka-popover-trigger-width)] min-w-[280px] max-w-[calc(100vw-16px)] rounded-md border bg-background p-0 shadow-lg duration-150 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=open]:fade-in-0 data-[state=closed]:fade-out-0 motion-reduce:animate-none"
        @open-auto-focus="focarBusca"
      >
        <div class="relative border-b">
          <Search class="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
          <input
            ref="campoBusca"
            v-model="busca"
            type="text"
            autocomplete="off"
            placeholder="buscar nome ou CNPJ…"
            aria-label="buscar empresa"
            class="h-9 w-full bg-transparent pl-9 pr-3 text-sm focus-visible:outline-none"
            @keydown="teclas"
          />
        </div>
        <ul ref="lista" role="listbox" class="max-h-64 overflow-y-auto p-1">
          <li
            v-for="(p, i) in opcoes"
            :key="p.company_id"
            :data-i="i"
            role="option"
            :aria-selected="p.company_id === modelValue"
            class="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm"
            :class="i === ativo ? 'bg-muted' : 'hover:bg-muted/60'"
            @mouseenter="ativo = i"
            @click="escolher(p)"
          >
            <div class="min-w-0 flex-1">
              <div class="truncate font-medium">{{ p.apelido }}</div>
              <div class="truncate text-xs text-muted-foreground tabular-nums">
                {{ p.cnpj ? fmtDoc(p.cnpj) : 'sem CNPJ' }}<template v-if="p.razao_social && p.razao_social !== p.apelido"> · {{ p.razao_social }}</template>
              </div>
            </div>
            <template v-if="mostrarProntidao">
              <span v-if="p.nfeio?.teste" class="pill-warning shrink-0">TESTE</span>
              <span v-if="p.pronto" class="pill-success shrink-0">Pronta</span>
              <span v-else class="pill-warning max-w-[160px] shrink-0 truncate" :title="p.pendencias.map((x) => pendenciaTexto(x).texto).join(', ')">
                {{ primeiraPendencia(p) }}
              </span>
            </template>
            <Check v-if="p.company_id === modelValue" class="size-4 shrink-0 text-primary" aria-hidden="true" />
          </li>
          <li v-if="!opcoes.length" class="px-2 py-6 text-center text-sm text-muted-foreground">
            Nenhuma empresa encontrada.
          </li>
        </ul>
      </PopoverContent>
    </PopoverPortal>
  </PopoverRoot>
</template>
