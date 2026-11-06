<script setup lang="ts">
// "SAC sem pedido" (06/11/2026, "Painel de Garantia: alterações" v3): o
// formulário do SAC do site que o robô não ligou a nenhuma venda — o nº do
// pedido, a NF e o CPF + produto não acharam exatamente uma (compra em outro
// vendedor, loja física, nº digitado errado). Aqui a pessoa procura a venda à
// mão (a mesma busca do "Vincular à garantia": POST /api/garantias/busca, o
// termo no corpo) e liga (POST /api/garantias/sac/{id}/vincular). Depois de
// ligado o caso aparece na linha da venda, com o botão Confirmar.
// Só quem enxerga todas as lojas recebe a lista (o SAC não tem loja); sem
// CPF — o que aparece é o que o cliente escreveu no formulário.
import { Link2, Loader2, Search, X } from 'lucide-vue-next'
import {
  dataBR,
  dataCurta,
  dataHoraBR,
  errosDaApi,
  nomeDaLinha,
  nomePlataforma,
  type GarantiaLinha,
  type SacSemPedido,
  type SacSemPedidoLista,
} from '~/lib/garantias'

const emit = defineEmits<{ (e: 'fechar'): void; (e: 'vinculado', sacId: number): void }>()

const { api } = useApi()
const toasts = useToasts()
const acesso = useGarantiaAcesso()

const itens = ref<SacSemPedido[]>([])
const total = ref(0)
const carregando = ref(false)
const erro = ref('')
async function carregar() {
  carregando.value = true
  erro.value = ''
  try {
    const r = await api<SacSemPedidoLista>('/api/garantias/sac/sem-pedido')
    itens.value = r.itens || []
    total.value = r.total ?? itens.value.length
  } catch (e) {
    erro.value = errosDaApi(e).geral || 'Não consegui ler os SAC sem pedido.'
  } finally {
    carregando.value = false
  }
}
onMounted(() => { void carregar() })

// A busca de cada caso (um aberto por vez): já vem com o pedido citado (ou
// a NF) do formulário.
const aberto = ref<number | null>(null)
const termo = ref('')
const resultados = ref<GarantiaLinha[]>([])
const buscando = ref(false)
const buscou = ref(false)
const erroBusca = ref('')
let geracao = 0
function abrirBusca(s: SacSemPedido) {
  aberto.value = s.id
  termo.value = (s.pedido_citado || s.nota_fiscal || '').trim()
  resultados.value = []
  buscou.value = false
  erroBusca.value = ''
  if (termo.value) void buscar()
}
async function buscar() {
  const q = termo.value.trim()
  if (!q) return
  const g = ++geracao
  buscando.value = true
  erroBusca.value = ''
  try {
    const r = await api<GarantiaLinha[]>('/api/garantias/busca', { method: 'POST', body: { q } })
    if (g !== geracao) return
    resultados.value = r
    buscou.value = true
  } catch (e) {
    if (g !== geracao) return
    erroBusca.value = errosDaApi(e).geral
  } finally {
    if (g === geracao) buscando.value = false
  }
}

const ligando = ref<number | null>(null)
async function vincular(s: SacSemPedido, g: GarantiaLinha) {
  if (ligando.value !== null) return
  if (!window.confirm(`Ligar o SAC ${s.protocolo} à venda do pedido ${g.pedido_bling} (${nomeDaLinha(g)})?`)) return
  ligando.value = g.id
  try {
    await api(`/api/garantias/sac/${s.id}/vincular`, { method: 'POST', body: { garantia_id: g.id } })
    toasts.success(`SAC ${s.protocolo} ligado ao pedido ${g.pedido_bling}`, 'O caso aparece na linha da venda, com o botão Confirmar.')
    itens.value = itens.value.filter((x) => x.id !== s.id)
    total.value = Math.max(0, total.value - 1)
    aberto.value = null
    emit('vinculado', s.id)
  } catch (e) {
    toasts.error('Não deu para ligar o SAC', errosDaApi(e).geral)
  } finally {
    ligando.value = null
  }
}

const vazio = (v: string | null | undefined) => (v && v.trim() ? v : '—')
</script>

<template>
  <section class="space-y-3 rounded-lg border bg-card p-3 sm:p-4" data-sac-sem-pedido-painel>
    <div class="flex flex-wrap items-start gap-2">
      <div class="min-w-0 flex-1">
        <h2 class="text-sm font-semibold">SAC sem pedido<template v-if="!carregando"> ({{ total }})</template></h2>
        <p class="text-xs text-muted-foreground">Formulários do SAC do site que não acharam a venda pelo nº do pedido, pela nota fiscal nem pelo CPF + produto. Procure a venda e ligue à mão.</p>
      </div>
      <Button size="sm" variant="ghost" aria-label="Fechar" @click="emit('fechar')"><X class="size-4" /></Button>
    </div>
    <div v-if="carregando" class="flex items-center gap-2 py-4 text-sm text-muted-foreground"><Loader2 class="size-4 animate-spin" /> lendo…</div>
    <p v-else-if="erro" role="alert" class="text-sm text-destructive">{{ erro }}</p>
    <p v-else-if="!itens.length" class="text-sm text-muted-foreground" data-sac-sem-pedido-vazio>Nenhum SAC sem pedido agora.</p>
    <ul v-else class="divide-y rounded-md border" data-sac-sem-pedido-lista>
      <li v-for="s in itens" :key="s.id" class="space-y-2 px-3 py-2.5" :data-sac-id="s.id">
        <div class="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-sm">
          <span class="font-mono font-medium">{{ s.protocolo }}</span>
          <span class="text-xs text-muted-foreground" :title="dataHoraBR(s.recebido_em)">{{ dataBR(s.recebido_em) }}</span>
          <span class="break-words font-medium">{{ vazio(s.nome) }}</span>
          <Button v-if="acesso.registra && aberto !== s.id" size="sm" variant="outline" class="ml-auto h-7" data-procurar-venda @click="abrirBusca(s)">
            <Search class="mr-1 size-3.5" /> procurar a venda
          </Button>
        </div>
        <dl class="grid grid-cols-[auto,1fr] gap-x-3 gap-y-0.5 text-xs sm:grid-cols-[auto,1fr,auto,1fr]">
          <dt class="text-muted-foreground">Pedido citado</dt><dd class="break-all font-mono">{{ vazio(s.pedido_citado) }}</dd>
          <dt class="text-muted-foreground">Nota fiscal</dt><dd class="break-all font-mono">{{ vazio(s.nota_fiscal) }}</dd>
          <dt class="text-muted-foreground">Modelo</dt><dd class="break-words">{{ vazio(s.modelo) }}</dd>
          <dt class="text-muted-foreground">Local da compra</dt><dd class="break-words">{{ vazio(s.local_compra) }}<template v-if="s.loja_vendedor"> · {{ s.loja_vendedor }}</template></dd>
        </dl>
        <div v-if="aberto === s.id" class="space-y-2 rounded-md border bg-muted/30 p-2" data-busca-manual>
          <form class="flex gap-2" @submit.prevent="buscar">
            <Input v-model="termo" class="h-8" placeholder="Nome / CPF / NF / Pedido" autocomplete="off" aria-label="procurar a venda" />
            <Button size="sm" class="h-8" type="submit" :disabled="buscando || !termo.trim()">
              <Loader2 v-if="buscando" class="mr-1 size-3.5 animate-spin" /><Search v-else class="mr-1 size-3.5" /> buscar
            </Button>
            <Button size="sm" variant="ghost" class="h-8" type="button" @click="aberto = null">cancelar</Button>
          </form>
          <p v-if="erroBusca" class="text-xs text-destructive">{{ erroBusca }}</p>
          <p v-else-if="buscou && !resultados.length" class="text-xs text-muted-foreground">Nenhuma venda encontrada — a compra pode ter sido em outro vendedor.</p>
          <ul v-if="resultados.length" class="max-h-56 space-y-1 overflow-y-auto">
            <li v-for="r in resultados" :key="r.id" class="flex flex-wrap items-center gap-x-2 gap-y-0.5 rounded-md border bg-background px-2.5 py-1.5 text-xs">
              <span class="font-medium">{{ nomeDaLinha(r) }}</span>
              <span class="text-muted-foreground">pedido {{ r.pedido_bling }}<template v-if="r.plataforma"> · {{ nomePlataforma(r.plataforma) }}</template><template v-if="r.conta"> · {{ r.conta }}</template> · entrega {{ dataCurta(r.data_inicio) }}</span>
              <span v-if="r.produto_nome" class="text-muted-foreground">· {{ r.produto_nome }}</span>
              <Button size="sm" class="ml-auto h-7" :disabled="ligando !== null" :data-ligar-garantia="r.id" @click="vincular(s, r)">
                <Loader2 v-if="ligando === r.id" class="mr-1 size-3.5 animate-spin" /><Link2 v-else class="mr-1 size-3.5" /> ligar
              </Button>
            </li>
          </ul>
        </div>
      </li>
    </ul>
  </section>
</template>
