<script setup lang="ts">
// Pedidos 2 (23/11/2026, Marco): os pedidos que o DaVinci ERP lê das lojas, aqui no DaVinci, só
// para quem entra no ERP. Cada venda com o número e a situação que tem no Bling; lida do próprio
// ERP pelo mesmo portão do /erp (Caddy → /api/erp-local), só leitura. O ERP continua com a tela.
import { ChevronLeft, ChevronRight, RefreshCw, ShoppingCart } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'

definePageMeta({ middleware: ['admin', 'erp'] })

type Venda = {
  number: string
  channel: string
  store: string
  created_at: string
  status: string
  total: string
  buyer: string
  skus: string[]
  bling_number: string
  bling_situation: number | null
}

const POR_PAGINA = 50
const CANAIS: Record<string, string> = {
  ml: 'Mercado Livre', shopee: 'Shopee', tiktok: 'TikTok Shop', amazon: 'Amazon',
  magalu: 'Magalu', carrefour: 'Carrefour', temu: 'Temu',
}
const { api } = useApi()
const itens = ref<Venda[]>([])
const situacoes = ref<Record<string, string>>({})
const total = ref(0)
const inicio = ref(0)
const busca = ref('')
const situacao = ref('')
const carregando = ref(false)
const erro = ref<string | null>(null)

let seq = 0
async function carregar() {
  const meu = ++seq
  carregando.value = true
  erro.value = null
  try {
    const query: Record<string, string | number> = { q: busca.value.trim(), offset: inicio.value, limit: POR_PAGINA }
    if (situacao.value) query.situation = Number(situacao.value)
    const r = await api<{ items: Venda[]; total: number; situations?: Record<string, string> }>('/api/erp-local/store-sales', { query })
    if (meu === seq) {
      itens.value = r.items
      total.value = r.total
      if (r.situations) situacoes.value = r.situations
    }
  } catch (e: any) {
    if (meu === seq) erro.value = e?.status === 403 ? 'Sem acesso ao DaVinci ERP.' : 'Não deu para ler os pedidos do ERP.'
  } finally {
    if (meu === seq) carregando.value = false
  }
}
onMounted(carregar)
let espera: ReturnType<typeof setTimeout> | undefined
watch(busca, () => {
  clearTimeout(espera)
  espera = setTimeout(() => {
    inicio.value = 0
    carregar()
  }, 300)
})
watch(situacao, () => {
  inicio.value = 0
  carregar()
})
function pagina(passo: number) {
  inicio.value = Math.max(0, inicio.value + passo)
  carregar()
}
const brl = (v: string) => Number(v || 0).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
const quando = (v: string) => (v ? new Date(v).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' }) : '—')
const nomeSituacao = (v: Venda) => (v.bling_situation !== null && v.bling_situation !== undefined ? situacoes.value[String(v.bling_situation)] ?? String(v.bling_situation) : v.status || '—')
const opcoes = computed(() => Object.entries(situacoes.value).sort((a, b) => a[1].localeCompare(b[1], 'pt-BR')))
</script>

<template>
  <div class="space-y-4">
    <PageHeader
      title="Pedidos 2"
      description="As vendas que o DaVinci ERP lê das lojas, com o número e a situação que têm no Bling. Só consulta: enquanto o Bling manda, os pedidos são do Bling."
    >
      <template #actions>
        <Button size="sm" variant="ghost" :disabled="carregando" @click="carregar">
          <RefreshCw class="mr-1 size-4" :class="{ 'animate-spin': carregando }" /> atualizar
        </Button>
      </template>
    </PageHeader>

    <div class="flex flex-wrap items-center gap-2">
      <input v-model="busca" class="h-9 w-72 rounded-md border bg-background px-2 text-sm" placeholder="Número, comprador, loja ou SKU" aria-label="Pesquisar pedidos" />
      <select v-model="situacao" class="h-9 rounded-md border bg-background px-2 text-sm" aria-label="Situação no Bling">
        <option value="">Situação: todas</option>
        <option v-for="[id, nome] in opcoes" :key="id" :value="id">{{ nome }}</option>
      </select>
      <span class="ml-auto text-sm text-muted-foreground">{{ total.toLocaleString('pt-BR') }} vendas</span>
    </div>

    <div v-if="erro" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">{{ erro }}</div>

    <div class="table-card overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr>
            <th>Número</th>
            <th>Nº na loja</th>
            <th>Data</th>
            <th>Loja</th>
            <th>Comprador</th>
            <th>Itens</th>
            <th class="text-right">Total</th>
            <th>Situação</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="carregando && !itens.length">
            <td colspan="8" class="py-10 text-center text-muted-foreground">Carregando…</td>
          </tr>
          <tr v-else-if="!itens.length">
            <td colspan="8" class="py-10 text-center text-muted-foreground">
              <ShoppingCart class="mx-auto mb-2 size-6 opacity-50" />
              Nenhuma venda com essa busca.
            </td>
          </tr>
          <tr v-for="v in itens" :key="`${v.channel}-${v.number}`">
            <td class="whitespace-nowrap font-medium">{{ v.bling_number || '—' }}</td>
            <td class="whitespace-nowrap font-mono text-xs">{{ v.number }}</td>
            <td class="whitespace-nowrap">{{ quando(v.created_at) }}</td>
            <td>
              <div>{{ v.store }}</div>
              <div class="text-xs text-muted-foreground">{{ CANAIS[v.channel] ?? v.channel }}</div>
            </td>
            <td>{{ v.buyer || '—' }}</td>
            <td class="max-w-[18rem] text-xs">{{ v.skus.join(', ') || '—' }}</td>
            <td class="whitespace-nowrap text-right">{{ brl(v.total) }}</td>
            <td class="whitespace-nowrap"><span class="pill-info">{{ nomeSituacao(v) }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="flex items-center justify-end gap-2 text-sm">
      <span class="text-muted-foreground">{{ total ? `${inicio + 1}–${Math.min(inicio + POR_PAGINA, total)} de ${total.toLocaleString('pt-BR')}` : '' }}</span>
      <Button size="sm" variant="outline" :disabled="!inicio || carregando" @click="pagina(-POR_PAGINA)"><ChevronLeft class="size-4" /></Button>
      <Button size="sm" variant="outline" :disabled="inicio + POR_PAGINA >= total || carregando" @click="pagina(POR_PAGINA)"><ChevronRight class="size-4" /></Button>
    </div>
  </div>
</template>
