<script setup lang="ts">
// Painel de ajuda da tela (M-18, 23/11/2026): abre pelo "?" da barra do topo
// (AppTopbar) e mostra a ajuda que a página registrou com useAjuda. Fica à
// direita, abaixo da barra do topo (não a cobre), até o fim da tela, com
// rolagem própria. Fecha clicando de novo no "?" ou com Esc (sem botão X).
// M-24 (24/11/2026): item com `cartao` mostra o cartãozinho vermelho da Tabela
// de Preços antes do termo (a "Pendências" da Ajuda · Faturamento).
import { onBeforeUnmount, ref, watch } from 'vue'

const ajuda = useAjudaAtual()
const aberta = useAjudaAberta()
// Onde a barra do topo termina (px). 56 = a altura dela no computador.
const topo = ref(56)

function medirTopo() {
  const barra = document.querySelector('.app-topbar')
  const fim = barra?.getBoundingClientRect().bottom
  if (fim && fim > 0) topo.value = Math.round(fim)
}

function aoTeclar(e: KeyboardEvent) {
  if (e.key !== 'Escape' || e.defaultPrevented) return
  aberta.value = false
}

function escutar(sim: boolean) {
  if (!import.meta.client) return
  if (sim) {
    medirTopo()
    window.addEventListener('keydown', aoTeclar)
    window.addEventListener('resize', medirTopo)
  } else {
    window.removeEventListener('keydown', aoTeclar)
    window.removeEventListener('resize', medirTopo)
  }
}

watch(() => !!(aberta.value && ajuda.value), escutar, { immediate: true })
onBeforeUnmount(() => escutar(false))
</script>

<template>
  <aside
    v-if="aberta && ajuda"
    id="davinci-ajuda"
    class="fixed right-0 bottom-0 z-[35] flex flex-col border-l bg-background shadow-xl"
    :style="{ top: `${topo}px`, width: 'min(440px, 100vw)' }"
    :aria-label="`Ajuda · ${ajuda.titulo}`"
    data-ajuda-painel
  >
    <h2 class="shrink-0 border-b px-5 py-3 text-sm font-semibold">Ajuda · {{ ajuda.titulo }}</h2>
    <div class="flex-1 overflow-y-auto px-5 py-4 space-y-5 text-[13px] leading-relaxed">
      <section v-for="secao in ajuda.conteudo" :key="secao.titulo">
        <h3 class="mb-1.5 font-semibold">{{ secao.titulo }}</h3>
        <component
          :is="secao.numerada ? 'ol' : 'ul'"
          class="space-y-1 pl-5 text-muted-foreground"
          :class="secao.numerada ? 'list-decimal' : 'list-disc'"
        >
          <li v-for="(item, i) in secao.itens" :key="i">
            <template v-if="typeof item === 'string'">{{ item }}</template>
            <template v-else><span v-if="item.cartao" class="mr-1.5 inline-block h-[10px] w-[7px] rotate-12 rounded-[1px] bg-red-600 align-middle shadow-sm" role="img" aria-label="cartão vermelho" data-cartao /><strong class="font-semibold text-foreground">{{ item.termo }}</strong> — {{ item.texto }}</template>
          </li>
        </component>
      </section>
    </div>
  </aside>
</template>
