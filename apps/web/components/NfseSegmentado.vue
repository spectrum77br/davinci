<script setup lang="ts">
// Pílulas: as abas da Emissão de Serviço e os filtros pequenos (Teste ×
// Produção, Em uso × Todas). Mesmo visual das abas de Devoluções. Setas ←/→
// trocam de opção. Contador neutro (cinza) ou de atenção (âmbar; some no 0).
// Nunca quebra em duas linhas: sem espaço, a faixa rola para o lado (e a
// opção escolhida fica à vista). O tamanho "sm" tem a altura dos campos (h-9)
// para alinhar nas barras de filtro.
import { nextTick, onMounted, ref, watch, type Component } from 'vue'

type Opcao = {
  id: string
  rotulo: string
  icone?: Component
  contador?: number | null
  tomContador?: 'neutro' | 'atencao'
  dica?: string
}

const props = withDefaults(
  defineProps<{
    modelValue: string
    opcoes: Opcao[]
    tamanho?: 'md' | 'sm'
  }>(),
  { tamanho: 'md' },
)

const emit = defineEmits<{ (e: 'update:modelValue', v: string): void }>()

function mostraContador(o: Opcao): boolean {
  if (o.contador == null) return false
  return o.tomContador === 'atencao' ? o.contador > 0 : true
}

function escolher(id: string) {
  if (id !== props.modelValue) emit('update:modelValue', id)
}

const faixa = ref<HTMLElement | null>(null)

// Rola só a faixa (nunca a página) até a opção escolhida ficar à vista.
function mostrarEscolhida() {
  const c = faixa.value
  if (!c || c.scrollWidth <= c.clientWidth) return
  const i = props.opcoes.findIndex((o) => o.id === props.modelValue)
  const b = c.querySelectorAll<HTMLElement>('[role="tab"]')[i]
  if (!b) return
  const rc = c.getBoundingClientRect()
  const rb = b.getBoundingClientRect()
  if (rb.left < rc.left) c.scrollLeft += rb.left - rc.left - 4
  else if (rb.right > rc.right) c.scrollLeft += rb.right - rc.right + 4
}

onMounted(mostrarEscolhida)
watch(
  () => props.modelValue,
  () => nextTick(mostrarEscolhida),
)

function teclas(ev: KeyboardEvent, i: number) {
  const n = props.opcoes.length
  if (!n) return
  let alvo = -1
  if (ev.key === 'ArrowRight') alvo = (i + 1) % n
  else if (ev.key === 'ArrowLeft') alvo = (i - 1 + n) % n
  else if (ev.key === 'Home') alvo = 0
  else if (ev.key === 'End') alvo = n - 1
  if (alvo < 0) return
  ev.preventDefault()
  const o = props.opcoes[alvo]
  if (!o) return
  escolher(o.id)
  faixa.value?.querySelectorAll<HTMLElement>('[role="tab"]')[alvo]?.focus()
}
</script>

<template>
  <div
    ref="faixa"
    role="tablist"
    class="flex w-fit max-w-full gap-1 overflow-x-auto rounded-md bg-muted/40 p-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
  >
    <button
      v-for="(o, i) in opcoes"
      :key="o.id"
      type="button"
      role="tab"
      :aria-selected="modelValue === o.id"
      :tabindex="modelValue === o.id ? 0 : -1"
      class="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
      :class="[
        tamanho === 'sm' ? 'px-2.5 py-1.5 text-xs' : 'px-3 py-1.5 text-sm',
        modelValue === o.id
          ? 'bg-background font-medium shadow-sm'
          : 'text-muted-foreground hover:bg-background/60 hover:text-foreground',
      ]"
      @click="escolher(o.id)"
      @keydown="teclas($event, i)"
    >
      <component
        :is="o.icone"
        v-if="o.icone"
        class="shrink-0"
        :class="tamanho === 'sm' ? 'size-3.5' : 'hidden size-4 lg:inline'"
        aria-hidden="true"
      />
      {{ o.rotulo }}
      <NfseDica v-if="mostraContador(o)" :texto="o.dica">
        <span
          class="rounded px-1.5 text-[11px] font-medium tabular-nums"
          :class="o.tomContador === 'atencao'
            ? 'bg-amber-500/15 text-amber-700 dark:text-amber-400'
            : 'bg-muted text-foreground/80'"
        >{{ o.contador }}</span>
      </NfseDica>
    </button>
  </div>
</template>
