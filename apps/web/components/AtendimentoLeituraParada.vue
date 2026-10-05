<script setup lang="ts">
// Faixa "lojas sem ler" (05/10/2026). A checagem antes de sair do Duoke achou
// a Temu sem ler desde 01/10 sem ninguém saber; o Eduardo decidiu que o aviso
// fica AQUI, no próprio /atendimento ("já avisa ali no próprio atendimento"),
// e não na Ouvidoria. Discreta, como a faixa do que está desligado: uma linha
// com o resumo e as três primeiras lojas (loja, plataforma, há quanto tempo,
// motivo curto); o resto e o que fazer abrem em "e mais N" / "o que fazer?".
// O link leva à aba "Lojas e modo", onde estão o erro e o modo de cada caixa.
// É o retrato do /resumo (services/vigia_leitura_atendimento.py): some sozinha
// quando a loja volta a ler, na recarga seguinte.
import { CircleAlert } from 'lucide-vue-next'
import {
  dicaLeituraParada,
  linhaLeituraParada,
  tituloLeituraParada,
  type LeituraParada,
} from '~/components/AtendimentoPlataforma.vue'

const props = withDefaults(defineProps<{
  itens: LeituraParada[]
  // O relógio da tela (useRelogio): o "há quanto tempo" anda entre recargas.
  agora?: number
  // O link para a aba "Lojas e modo" (na própria aba, não).
  link?: boolean
}>(), { agora: undefined, link: true })
const emit = defineEmits<{ (e: 'abrir-lojas'): void }>()

// Na linha cabem três; o resto abre na lista.
const NA_LINHA = 3
const aberta = ref(false)
const titulo = computed(() => tituloLeituraParada(props.itens))
const naLinha = computed(() => props.itens.slice(0, NA_LINHA))
const escondidas = computed(() => Math.max(0, props.itens.length - NA_LINHA))
const textoBotao = computed(() => {
  if (aberta.value) return 'esconder'
  return escondidas.value ? `e mais ${escondidas.value}` : 'o que fazer?'
})
function linha(l: LeituraParada): string {
  return linhaLeituraParada(l, props.agora ?? Date.now())
}
</script>

<template>
  <div
    v-if="itens.length"
    role="status"
    data-leitura-parada
    class="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-1.5 text-xs"
  >
    <div class="flex items-start gap-2">
      <CircleAlert class="mt-px size-4 shrink-0 text-red-600 dark:text-red-400" />
      <div class="flex min-w-0 flex-1 flex-wrap items-center gap-x-1.5 gap-y-0.5">
        <span class="font-semibold text-red-700 dark:text-red-300">{{ titulo }}</span>
        <template v-if="!aberta">
          <template v-for="l in naLinha" :key="l.chave">
            <span class="text-muted-foreground" aria-hidden="true">·</span>
            <span class="text-red-900 dark:text-red-200" :title="dicaLeituraParada(l)" data-leitura-parada-item>{{ linha(l) }}</span>
          </template>
        </template>
        <button
          type="button"
          class="shrink-0 text-muted-foreground underline hover:text-foreground"
          :aria-expanded="aberta"
          @click="aberta = !aberta"
        >{{ textoBotao }}</button>
        <button
          v-if="link"
          type="button"
          class="ml-auto shrink-0 font-medium text-red-700 underline hover:text-red-900 dark:text-red-300 dark:hover:text-red-100"
          data-leitura-parada-link
          @click="emit('abrir-lojas')"
        >ver em Lojas e modo</button>
      </div>
    </div>
    <ul v-if="aberta" class="mt-1 space-y-0.5 pl-6">
      <li v-for="l in itens" :key="l.chave" class="text-red-900 dark:text-red-200" data-leitura-parada-item>
        {{ linha(l) }}<span v-if="l.acao" class="text-muted-foreground"> — {{ l.acao }}</span>
      </li>
    </ul>
  </div>
</template>
