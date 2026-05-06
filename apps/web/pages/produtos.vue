<script setup lang="ts">
import { Plus, Filter, Download, RefreshCw, Package, ImageOff } from 'lucide-vue-next'

definePageMeta({ middleware: ['permission'], permission: { resource: 'produtos', action: 'view' } })

type Produto = {
  sku: string
  nome: string
  categoria: string
  custo: number
  estoque: number
  status: 'ativo' | 'pausado' | 'sem_estoque'
  marketplaces: string[]
  margem: number
}

const produtos = ref<Produto[]>([
  { sku: 'IPH15-128-PRT', nome: 'iPhone 15 128GB Preto',         categoria: 'celular',   custo: 3200, estoque: 24, status: 'ativo',       marketplaces: ['ML', 'Shopee', 'Amazon'], margem: 19.4 },
  { sku: 'IPH15-256-AZL', nome: 'iPhone 15 256GB Azul',          categoria: 'celular',   custo: 3650, estoque: 8,  status: 'ativo',       marketplaces: ['ML', 'Shopee'],          margem: 21.0 },
  { sku: 'GAL-S24-512',   nome: 'Galaxy S24 Ultra 512GB',         categoria: 'celular',   custo: 4900, estoque: 0,  status: 'sem_estoque', marketplaces: ['ML', 'Amazon'],          margem: 17.2 },
  { sku: 'AIRPODS-PRO2',  nome: 'AirPods Pro 2ª Geração',         categoria: 'eletro',    custo: 1280, estoque: 142,status: 'ativo',       marketplaces: ['ML', 'Shopee', 'Amazon', 'Magalu'], margem: 24.7 },
  { sku: 'MALA-ABS-28',   nome: 'Mala ABS 28" Preta',             categoria: 'mala',      custo: 280,  estoque: 67, status: 'ativo',       marketplaces: ['ML', 'Magalu'],          margem: 32.1 },
  { sku: 'TV-LG-55',      nome: 'TV LG 55" 4K OLED',              categoria: 'eletro',    custo: 4200, estoque: 5,  status: 'pausado',     marketplaces: ['ML'],                    margem: 11.8 },
  { sku: 'IPAD-AIR-256',  nome: 'iPad Air M2 256GB',              categoria: 'celular',   custo: 4100, estoque: 17, status: 'ativo',       marketplaces: ['ML', 'Amazon'],          margem: 18.3 },
  { sku: 'WATCH-S9-GPS',  nome: 'Apple Watch Series 9 GPS 45mm', categoria: 'eletro',    custo: 2400, estoque: 33, status: 'ativo',       marketplaces: ['ML', 'Shopee'],          margem: 22.6 },
])

const search = ref('')
const filtroCategoria = ref('')
const filtroStatus = ref('')

const filtered = computed(() =>
  produtos.value.filter((p) => {
    if (search.value && !`${p.sku} ${p.nome}`.toLowerCase().includes(search.value.toLowerCase())) return false
    if (filtroCategoria.value && p.categoria !== filtroCategoria.value) return false
    if (filtroStatus.value && p.status !== filtroStatus.value) return false
    return true
  }),
)

const totalEstoque = computed(() => produtos.value.reduce((a, p) => a + p.estoque, 0))
const valorEstoque = computed(() => produtos.value.reduce((a, p) => a + p.estoque * p.custo, 0))
const semEstoque = computed(() => produtos.value.filter((p) => p.status === 'sem_estoque').length)

function statusPill(s: Produto['status']) {
  if (s === 'ativo') return 'pill-success'
  if (s === 'sem_estoque') return 'pill-danger'
  return 'pill-warning'
}
function statusLabel(s: Produto['status']) {
  return s === 'ativo' ? 'ativo' : s === 'sem_estoque' ? 'sem estoque' : 'pausado'
}
function brl(v: number) {
  return v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
}
</script>

<template>
  <div class="space-y-5">
    <PageHeader title="Produtos" description="SKUs vindos do Bling, custos e situação por canal.">
      <template #actions>
        <Button size="sm" variant="outline">
          <Download class="size-4 mr-1.5" /> exportar
        </Button>
        <Button size="sm" variant="outline">
          <RefreshCw class="size-4 mr-1.5" /> sync Bling
        </Button>
        <Button size="sm">
          <Plus class="size-4 mr-1.5" /> novo SKU
        </Button>
      </template>
    </PageHeader>

    <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
      <StatCard label="SKUs ativos" :value="produtos.filter(p => p.status === 'ativo').length" :icon="Package" />
      <StatCard label="Total em estoque" :value="totalEstoque.toLocaleString('pt-BR')" hint="unidades" />
      <StatCard label="Valor em estoque" :value="brl(valorEstoque)" hint="ao custo" />
      <StatCard label="Sem estoque" :value="semEstoque" tone="danger" :icon="ImageOff" />
    </div>

    <div class="flex flex-wrap gap-2 items-center">
      <Input v-model="search" placeholder="buscar SKU ou nome…" class="w-72" />
      <select v-model="filtroCategoria" class="h-9 rounded-md border bg-background px-2 text-sm">
        <option value="">todas categorias</option>
        <option value="celular">celular</option>
        <option value="eletro">eletro</option>
        <option value="mala">mala</option>
      </select>
      <select v-model="filtroStatus" class="h-9 rounded-md border bg-background px-2 text-sm">
        <option value="">todos status</option>
        <option value="ativo">ativo</option>
        <option value="pausado">pausado</option>
        <option value="sem_estoque">sem estoque</option>
      </select>
      <Button size="sm" variant="ghost">
        <Filter class="size-4 mr-1.5" /> filtros
      </Button>
      <span class="ml-auto text-xs text-muted-foreground">
        {{ filtered.length }} de {{ produtos.length }} produtos
      </span>
    </div>

    <div class="table-card">
      <table class="w-full">
        <thead>
          <tr>
            <th>SKU</th>
            <th>Nome</th>
            <th>Categoria</th>
            <th class="text-right">Custo</th>
            <th class="text-right">Estoque</th>
            <th class="text-right">Margem</th>
            <th>Marketplaces</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in filtered" :key="p.sku">
            <td class="font-mono text-xs">{{ p.sku }}</td>
            <td class="font-medium">{{ p.nome }}</td>
            <td class="text-muted-foreground capitalize">{{ p.categoria }}</td>
            <td class="text-right tabular-nums">{{ brl(p.custo) }}</td>
            <td class="text-right tabular-nums">
              <span :class="p.estoque === 0 ? 'text-red-600 font-medium' : p.estoque < 10 ? 'text-amber-600' : ''">
                {{ p.estoque }}
              </span>
            </td>
            <td class="text-right tabular-nums">{{ p.margem.toFixed(1) }}%</td>
            <td>
              <div class="flex gap-1 flex-wrap">
                <span v-for="m in p.marketplaces" :key="m" class="pill pill-muted">{{ m }}</span>
              </div>
            </td>
            <td><span :class="statusPill(p.status)">{{ statusLabel(p.status) }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
