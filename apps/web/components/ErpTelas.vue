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

// Fotos e Embalagem do cadastro do produto (M-72, 26/11/2026): abrem aqui, na moldura, a MESMA
// janela da Tabela de Preços › Produtos (MidiasProdutoDrawer). O quadro do Produtos 2 manda só o id
// da linha da Tabela de Preços e a aba. A moldura aceita a mensagem só do próprio site E do próprio
// quadro do Produtos 2 (evento.source), confere o id e a aba, lê a linha com o login da pessoa (a
// API confere as permissões de sempre: ver para abrir, editar para enviar) e devolve ao quadro o
// que a janela mudou, para o selo trocar na hora. Nenhum dado do quadro é usado para gravar.
const PRODUTOS = '/erp-produtos'
const ID_DA_LINHA = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
type AbaMidias = 'fotos' | 'embalagens'
type LinhaMidias = {
  id: string
  sku: string
  name: string
  department: string
  fotos_url: string | null
  fotos_path: string | null
  embalagens_url: string | null
  embalagens_path: string | null
  fotos_count: number | null
  videos_count: number | null
  embalagens_count: number | null
  midias_contadas_em: string | null
  irmaos: { id: string; sku: string; name: string }[]
  pode_editar: boolean
}
const { api } = useApi()
const toast = useToasts()
const midias = ref<{ linha: LinhaMidias; aba: AbaMidias } | null>(null)
function avisarProdutos(mensagem: Record<string, unknown>) {
  const quadro = abertas.value.includes(PRODUTOS) ? quadros[PRODUTOS] : null
  quadro?.contentWindow?.postMessage(mensagem, window.location.origin)
}
async function abrirMidias(id: string, aba: AbaMidias) {
  try {
    const linha = await api<LinhaMidias>(`/api/pricing/mega/products/${id}`)
    if (atual.value === PRODUTOS) midias.value = { linha, aba }
  } catch (e: any) {
    toast.error('Fotos do produto', [
      e?.data?.detail?.message || 'Não foi possível abrir as fotos deste produto agora. Tente de novo.',
    ])
  }
}
function aoMensagemMidias(evento: MessageEvent) {
  if (evento.origin !== window.location.origin || evento.data?.tipo !== 'erp-abrir-midias') return
  const quadro = abertas.value.includes(PRODUTOS) ? quadros[PRODUTOS] : null
  if (!quadro?.contentWindow || evento.source !== quadro.contentWindow) return
  const id = String(evento.data.pricing_id || '')
  const aba = evento.data.aba
  if (!ID_DA_LINHA.test(id) || (aba !== 'fotos' && aba !== 'embalagens')) return
  abrirMidias(id, aba)
}
// O que a janela muda (envio, recontagem, trocar ou desligar a pasta) volta para o quadro.
function aplicarMidias(id: string, campos: Record<string, unknown>) {
  const presentes = JSON.parse(JSON.stringify(campos ?? {}))
  if (midias.value?.linha.id === id) Object.assign(midias.value.linha, presentes)
  avisarProdutos({ tipo: 'erp-midias', pricing_id: id, campos: presentes })
}
function aoEnviarMidias(ativo: boolean) {
  if (midias.value) avisarProdutos({ tipo: 'erp-midias-enviando', pricing_id: midias.value.linha.id, ativo })
}
function fecharMidias() {
  aoEnviarMidias(false)
  midias.value = null
}
watch(atual, (caminho) => {
  if (caminho !== PRODUTOS && midias.value) fecharMidias()
})
onMounted(() => window.addEventListener('message', aoMensagemMidias))
onBeforeUnmount(() => window.removeEventListener('message', aoMensagemMidias))
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
    <MidiasProdutoDrawer
      v-if="midias"
      :produto="midias.linha"
      :aba-inicial="midias.aba"
      :pode-editar="midias.linha.pode_editar"
      :irmaos="midias.linha.irmaos"
      :aplicar="aplicarMidias"
      @enviando="aoEnviarMidias"
      @fechar="fecharMidias"
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
