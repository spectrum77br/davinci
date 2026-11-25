<script setup lang="ts">
// Produtos 2 e Pedidos 2 (23/11/2026, Marco): a própria tela do DaVinci ERP dentro do DaVinci.
// Cada tela carrega na primeira visita e fica carregada (só some da vista) enquanto a pessoa anda
// pelo DaVinci: voltar a ela é instantâneo, sem recarregar o ERP a cada clique no menu. Só monta
// para quem entra no ERP; o portão do /erp (Caddy) vale do mesmo jeito.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const TELAS: Record<string, { src: string; titulo: string }> = {
  '/erp-produtos': { src: '/erp?embed=1#products', titulo: 'Produtos do DaVinci ERP' },
  '/erp-pedidos': { src: '/erp?embed=1#orders/store', titulo: 'Pedidos do DaVinci ERP' },
}

const route = useRoute()
const auth = useAuthStore()
const atual = computed(() => (TELAS[route.path] ? route.path : ''))
const abertas = ref<string[]>([])
watch(
  atual,
  (caminho) => {
    if (caminho && !abertas.value.includes(caminho)) abertas.value.push(caminho)
  },
  { immediate: true },
)

// "V 310306" clicado num lançamento de estoque (Produtos 2): abre o pedido em Pedidos 2. Se a tela
// de pedidos já está carregada, ela recebe o número; senão nasce com ele no endereço.
const PEDIDOS = '/erp-pedidos'
const quadros: Record<string, HTMLIFrameElement | null> = {}
const pedidoInicial = ref('')
function origem(caminho: string) {
  return caminho === PEDIDOS && pedidoInicial.value
    ? `/erp?embed=1#orders/store/${pedidoInicial.value}`
    : TELAS[caminho].src
}
function aoMensagem(evento: MessageEvent) {
  if (evento.origin !== window.location.origin || evento.data?.tipo !== 'erp-abrir-pedido') return
  const numero = String(evento.data.numero || '')
  if (!/^\d{1,20}$/.test(numero)) return
  const pedidos = abertas.value.includes(PEDIDOS) ? quadros[PEDIDOS] : null
  if (pedidos?.contentWindow) pedidos.contentWindow.postMessage({ tipo: 'erp-abrir-pedido', numero }, window.location.origin)
  else pedidoInicial.value = numero
  navigateTo(PEDIDOS)
}
onMounted(() => window.addEventListener('message', aoMensagem))
onBeforeUnmount(() => window.removeEventListener('message', aoMensagem))
</script>

<template>
  <template v-if="auth.user?.erp === true">
    <iframe
      v-for="caminho in abertas"
      v-show="atual === caminho"
      :key="caminho"
      :ref="(quadro) => (quadros[caminho] = quadro as HTMLIFrameElement | null)"
      :src="origem(caminho)"
      :title="TELAS[caminho].titulo"
      class="erp-tela"
    />
  </template>
</template>

<style scoped>
.erp-tela {
  display: block;
  width: 100%;
  height: calc(100vh - 7rem);
  min-height: 600px;
  border: 0;
  border-radius: 0.5rem;
  background: #f7f9fb;
}
</style>
