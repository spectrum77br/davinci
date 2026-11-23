<script setup lang="ts">
// Produtos 2 (23/11/2026, Marco): a tabela de produtos do DaVinci ERP aqui no DaVinci, só para
// quem entra no ERP. Lida do próprio ERP pelo mesmo portão do /erp (Caddy → /api/erp-local), só
// leitura; o ERP continua com a tela dele. Em modo consulta o cadastro e o estoque são os do Bling.
import { ChevronLeft, ChevronRight, Package, RefreshCw } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'

definePageMeta({ middleware: ['admin', 'erp'] })

type Produto = {
  sku: string
  name: string
  kind: string
  status: string
  unit: string
  gtin: string
  price: string | null
  cost_price: string | null
  physical: string | null
  reserved: string | null
  available: string | null
  components: { sku: string; quantity: string }[]
  image: string | null
}

const POR_PAGINA = 50
const TIPOS: Record<string, string> = { simple: 'Simples', kit: 'Com composição', service: 'Serviço' }
const { api } = useApi()
const itens = ref<Produto[]>([])
const total = ref(0)
const inicio = ref(0)
const busca = ref('')
const tipo = ref('')
const carregando = ref(false)
const erro = ref<string | null>(null)

let seq = 0
async function carregar() {
  const meu = ++seq
  carregando.value = true
  erro.value = null
  try {
    const r = await api<{ items: Produto[]; total: number }>('/api/erp-local/products/list', {
      query: { q: busca.value.trim(), kind: tipo.value, offset: inicio.value, limit: POR_PAGINA },
    })
    if (meu === seq) {
      itens.value = r.items
      total.value = r.total
    }
  } catch (e: any) {
    if (meu === seq) erro.value = e?.status === 403 ? 'Sem acesso ao DaVinci ERP.' : 'Não deu para ler os produtos do ERP.'
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
watch(tipo, () => {
  inicio.value = 0
  carregar()
})
function pagina(passo: number) {
  inicio.value = Math.max(0, inicio.value + passo)
  carregar()
}
const qtd = (v: string | null) => (v === null || v === undefined ? '—' : Number(v).toLocaleString('pt-BR', { maximumFractionDigits: 3 }))
const brl = (v: string | null) => (v === null || v === undefined ? '—' : Number(v).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' }))
const composicao = (p: Produto) => p.components.map((c) => `${Number(c.quantity).toLocaleString('pt-BR')}× ${c.sku}`).join(' + ')
</script>

<template>
  <div class="space-y-4">
    <PageHeader
      title="Produtos 2"
      description="Os produtos do DaVinci ERP: cadastro, preço, custo e estoque (físico, reservado e disponível), lidos do próprio ERP. Só consulta: enquanto o Bling manda, cadastro e estoque são os do Bling."
    >
      <template #actions>
        <Button size="sm" variant="ghost" :disabled="carregando" @click="carregar">
          <RefreshCw class="mr-1 size-4" :class="{ 'animate-spin': carregando }" /> atualizar
        </Button>
      </template>
    </PageHeader>

    <div class="flex flex-wrap items-center gap-2">
      <input v-model="busca" class="h-9 w-72 rounded-md border bg-background px-2 text-sm" placeholder="Código ou nome" aria-label="Pesquisar produtos" />
      <select v-model="tipo" class="h-9 rounded-md border bg-background px-2 text-sm" aria-label="Formato">
        <option value="">Formato: todos</option>
        <option value="simple">Simples</option>
        <option value="kit">Com composição</option>
        <option value="service">Serviço</option>
      </select>
      <span class="ml-auto text-sm text-muted-foreground">{{ total.toLocaleString('pt-BR') }} produtos</span>
    </div>

    <div v-if="erro" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">{{ erro }}</div>

    <div class="table-card overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr>
            <th>Código</th>
            <th>Nome</th>
            <th>Formato</th>
            <th class="text-right">Preço</th>
            <th class="text-right">Custo</th>
            <th class="text-right">Físico</th>
            <th class="text-right">Reservado</th>
            <th class="text-right">Disponível</th>
            <th>Situação</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="carregando && !itens.length">
            <td colspan="9" class="py-10 text-center text-muted-foreground">Carregando…</td>
          </tr>
          <tr v-else-if="!itens.length">
            <td colspan="9" class="py-10 text-center text-muted-foreground">
              <Package class="mx-auto mb-2 size-6 opacity-50" />
              Nenhum produto com essa busca.
            </td>
          </tr>
          <tr v-for="p in itens" :key="p.sku">
            <td class="whitespace-nowrap font-mono text-xs">{{ p.sku }}</td>
            <td>
              <div class="font-medium">{{ p.name }}</div>
              <div v-if="p.kind === 'kit' && p.components.length" class="text-xs text-muted-foreground">{{ composicao(p) }}</div>
            </td>
            <td class="whitespace-nowrap">{{ TIPOS[p.kind] ?? p.kind }}</td>
            <td class="whitespace-nowrap text-right">{{ brl(p.price) }}</td>
            <td class="whitespace-nowrap text-right">{{ brl(p.cost_price) }}</td>
            <td class="text-right">{{ qtd(p.physical) }}</td>
            <td class="text-right">{{ qtd(p.reserved) }}</td>
            <td class="text-right font-medium">{{ qtd(p.available) }}</td>
            <td><span :class="p.status === 'active' ? 'pill-success' : 'pill-muted'">{{ p.status === 'active' ? 'Ativo' : 'Inativo' }}</span></td>
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
