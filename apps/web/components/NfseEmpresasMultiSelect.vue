<script setup lang="ts">
// Escolha de VÁRIAS empresas do grupo (01/10/2026, Eduardo: "nova nota fixa
// queria poder selecionar várias empresas em massa, para fazer de uma vez só").
// Mesmo jeito do NfseEmpresaSelect (busca por apelido, razão social ou dígitos
// do CNPJ; ↑/↓/Enter/Esc), mas cada opção tem caixinha e a lista não fecha ao
// marcar. Em cima da lista: "marcar as prontas", "marcar todas" (as da busca) e
// "limpar". Embaixo do campo, com 2 ou mais, a lista das escolhidas com a
// contagem e o X para tirar uma; o slot `extra` (por empresa) mostra o que a
// tela quiser ao lado do nome (ex.: a % da empresa).
import { computed, nextTick, ref, watch } from 'vue'
import { PopoverContent, PopoverPortal, PopoverRoot, PopoverTrigger } from 'reka-ui'
import { ChevronDown, Search, X } from 'lucide-vue-next'
import { fmtDoc, minusculo, pendenciaTexto, plural, soDigitos, useNfseTela, type Prestador } from '~/lib/nfse'

const props = withDefaults(
  defineProps<{
    modelValue: string[]
    // Mesmo filtro do NfseEmpresaSelect: 'uteis' = ligadas à NFE.io ou com
    // serviço prestado salvo.
    filtro?: 'uteis' | 'ligadas' | 'com_fiscal' | 'com_cnpj' | 'todas'
    mostrarProntidao?: boolean
    // Não aparecem para marcar (ex.: a empresa que é o tomador, as que já
    // ganharam a nota fixa nesta abertura). Se já estiver marcada, continua
    // aparecendo para dar para tirar.
    excluirIds?: string[]
    placeholder?: string
    disabled?: boolean
    invalido?: boolean
    id?: string
  }>(),
  {
    filtro: 'uteis',
    mostrarProntidao: false,
    excluirIds: () => [],
    placeholder: 'escolha uma ou várias empresas…',
    disabled: false,
    invalido: false,
  },
)

const emit = defineEmits<{ (e: 'update:modelValue', v: string[]): void }>()

const tela = useNfseTela()
const aberto = ref(false)
const busca = ref('')
const ativo = ref(0)
const campoBusca = ref<HTMLInputElement | null>(null)
const lista = ref<HTMLElement | null>(null)
const raiz = ref<HTMLElement | null>(null)

const marcadasSet = computed(() => new Set(props.modelValue))

// Na ordem em que foram marcadas (a 1ª é a que a tela usa de exemplo).
const escolhidas = computed(() =>
  props.modelValue
    .map((id) => tela.prestadores.value.find((p) => p.company_id === id))
    .filter((p): p is Prestador => !!p),
)

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
  const fora = new Set(props.excluirIds)
  return tela.prestadores.value
    .filter((p) => marcadasSet.value.has(p.company_id) || (!fora.has(p.company_id) && passaFiltro(p)))
    .filter((p) => {
      if (!q) return true
      return (
        p.apelido.toLowerCase().includes(q) ||
        (p.razao_social || '').toLowerCase().includes(q) ||
        (!!dq && soDigitos(p.cnpj).includes(dq))
      )
    })
})

// O que os botões de cima ainda podem marcar (só entre as da busca).
const prontasParaMarcar = computed(() => opcoes.value.filter((p) => p.pronto && !marcadasSet.value.has(p.company_id)))
const todasParaMarcar = computed(() => opcoes.value.filter((p) => !marcadasSet.value.has(p.company_id)))

watch(opcoes, () => {
  if (ativo.value >= opcoes.value.length) ativo.value = Math.max(0, opcoes.value.length - 1)
})

watch(aberto, (v) => {
  if (!v) return
  busca.value = ''
  ativo.value = 0
  nextTick(() => rolarAtivo())
})

function primeiraPendencia(p: Prestador): string | null {
  const x = p.pendencias?.[0]
  return x ? minusculo(pendenciaTexto(x).texto) : null
}

function alternar(p: Prestador) {
  if (marcadasSet.value.has(p.company_id)) emit('update:modelValue', props.modelValue.filter((id) => id !== p.company_id))
  else emit('update:modelValue', [...props.modelValue, p.company_id])
}

function marcarVarias(ps: Prestador[]) {
  if (!ps.length) return
  emit('update:modelValue', [...props.modelValue, ...ps.map((p) => p.company_id).filter((id) => !marcadasSet.value.has(id))])
}

function limpar() {
  emit('update:modelValue', [])
}

// 01/10/2026 (revisão): o botão com o foco some ao tirar; o foco vai para o X
// seguinte da lista (ou o anterior) e, sem lista, para o campo. Sem isso a
// gaveta jogava o foco para o topo e quem usa teclado se perdia.
function focarDepois(i: number) {
  nextTick(() => {
    const xs = raiz.value?.querySelectorAll<HTMLElement>('[data-tirar]')
    if (xs?.length) xs[Math.min(i, xs.length - 1)]!.focus()
    else raiz.value?.querySelector<HTMLElement>('button[role="combobox"]')?.focus()
  })
}

function tirar(id: string) {
  const i = props.modelValue.indexOf(id)
  emit('update:modelValue', props.modelValue.filter((x) => x !== id))
  focarDepois(Math.max(0, i))
}

function limparDaLista() {
  limpar()
  focarDepois(0)
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
    if (p) alternar(p)
  }
}

function focarBusca(ev: Event) {
  ev.preventDefault()
  campoBusca.value?.focus()
}

const textoBotao = computed(() => {
  const n = escolhidas.value.length
  if (!n) return null
  if (n === 1) return null
  return `${plural(n, 'empresa', 'empresas')}: ${escolhidas.value.map((p) => p.apelido).join(', ')}`
})
</script>

<template>
  <div ref="raiz" class="space-y-2">
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
          <span v-if="escolhidas.length === 1" class="min-w-0 truncate">
            {{ escolhidas[0]!.apelido }}
            <span class="ml-1 text-xs text-muted-foreground tabular-nums">{{ fmtDoc(escolhidas[0]!.cnpj) }}</span>
          </span>
          <span v-else-if="textoBotao" class="min-w-0 truncate">{{ textoBotao }}</span>
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
          class="z-[80] w-[var(--reka-popover-trigger-width)] min-w-[300px] max-w-[calc(100vw-16px)] rounded-md border bg-background p-0 shadow-lg duration-150 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=open]:fade-in-0 data-[state=closed]:fade-out-0 motion-reduce:animate-none"
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
          <div class="flex flex-wrap items-center gap-x-3 gap-y-1 border-b px-3 py-1.5 text-xs">
            <span class="tabular-nums text-muted-foreground" aria-live="polite">
              {{ modelValue.length ? plural(modelValue.length, 'marcada', 'marcadas') : 'nenhuma marcada' }}
            </span>
            <span class="ml-auto flex flex-wrap items-center gap-x-3 gap-y-1">
              <button
                type="button"
                class="text-primary hover:underline disabled:pointer-events-none disabled:opacity-40"
                :disabled="!prontasParaMarcar.length"
                @click="marcarVarias(prontasParaMarcar)"
              >
                marcar as prontas{{ prontasParaMarcar.length ? ` (${prontasParaMarcar.length})` : '' }}
              </button>
              <button
                type="button"
                class="text-primary hover:underline disabled:pointer-events-none disabled:opacity-40"
                :disabled="!todasParaMarcar.length"
                @click="marcarVarias(todasParaMarcar)"
              >
                marcar todas{{ todasParaMarcar.length ? ` (${todasParaMarcar.length})` : '' }}
              </button>
              <button
                type="button"
                class="text-muted-foreground hover:text-foreground hover:underline disabled:pointer-events-none disabled:opacity-40"
                :disabled="!modelValue.length"
                @click="limpar"
              >
                limpar
              </button>
            </span>
          </div>
          <ul ref="lista" role="listbox" aria-multiselectable="true" class="max-h-64 overflow-y-auto p-1">
            <li
              v-for="(p, i) in opcoes"
              :key="p.company_id"
              :data-i="i"
              role="option"
              :aria-selected="marcadasSet.has(p.company_id)"
              class="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm"
              :class="i === ativo ? 'bg-muted' : 'hover:bg-muted/60'"
              @mouseenter="ativo = i"
              @click="alternar(p)"
            >
              <input
                type="checkbox"
                tabindex="-1"
                class="pointer-events-none size-4 shrink-0 rounded accent-primary dark:[color-scheme:dark]"
                :checked="marcadasSet.has(p.company_id)"
                aria-hidden="true"
              />
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
            </li>
            <li v-if="!opcoes.length" class="px-2 py-6 text-center text-sm text-muted-foreground">
              Nenhuma empresa encontrada.
            </li>
          </ul>
          <div class="flex justify-end border-t px-2 py-1.5">
            <button
              type="button"
              class="rounded px-2 py-1 text-sm font-medium text-primary hover:bg-muted/60"
              @click="aberto = false"
            >
              pronto
            </button>
          </div>
        </PopoverContent>
      </PopoverPortal>
    </PopoverRoot>

    <!-- As escolhidas (com 2 ou mais): contagem + tirar uma -->
    <div v-if="escolhidas.length > 1" class="rounded-md border">
      <div class="flex items-center gap-2 border-b bg-muted/30 px-3 py-1.5 text-xs">
        <span class="font-medium tabular-nums">{{ plural(escolhidas.length, 'empresa escolhida', 'empresas escolhidas') }}</span>
        <span class="text-muted-foreground">· uma nota fixa para cada</span>
        <button
          v-if="!disabled"
          type="button"
          class="ml-auto text-muted-foreground hover:text-foreground hover:underline"
          @click="limparDaLista"
        >
          limpar
        </button>
      </div>
      <ul class="max-h-56 divide-y overflow-y-auto">
        <li v-for="p in escolhidas" :key="p.company_id" class="flex items-center gap-2 px-3 py-1.5 text-sm">
          <div class="min-w-0 flex-1">
            <span class="font-medium">{{ p.apelido }}</span>
            <span class="ml-1.5 text-xs tabular-nums text-muted-foreground">{{ p.cnpj ? fmtDoc(p.cnpj) : 'sem CNPJ' }}</span>
          </div>
          <slot name="extra" :empresa="p" />
          <template v-if="mostrarProntidao">
            <span v-if="p.nfeio?.teste" class="pill-warning shrink-0">TESTE</span>
            <span
              v-if="!p.pronto"
              class="pill-warning max-w-[140px] shrink-0 truncate"
              :title="p.pendencias.map((x) => pendenciaTexto(x).texto).join(', ')"
            >
              {{ primeiraPendencia(p) || 'falta ligar à NFE.io' }}
            </span>
          </template>
          <button
            v-if="!disabled"
            type="button"
            data-tirar
            class="inline-flex size-6 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
            :aria-label="`tirar a ${p.apelido}`"
            :title="`tirar a ${p.apelido}`"
            @click="tirar(p.company_id)"
          >
            <X class="size-3.5" aria-hidden="true" />
          </button>
        </li>
      </ul>
    </div>
  </div>
</template>
