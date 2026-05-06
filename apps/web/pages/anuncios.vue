<script setup lang="ts">
import { Plus, Filter, Eye, Pause, Play, ExternalLink, Megaphone } from 'lucide-vue-next'

definePageMeta({ middleware: ['permission'], permission: { resource: 'anuncios', action: 'view' } })

type Anuncio = {
  id: string
  mlb: string
  titulo: string
  marketplace: 'ML' | 'Shopee' | 'Amazon' | 'Magalu'
  conta: string
  preco: number
  visitas: number
  vendas: number
  status: 'ativo' | 'pausado' | 'encerrado'
  reputacao?: 'verde' | 'amarela' | 'laranja' | 'vermelha'
}

const anuncios = ref<Anuncio[]>([
  { id: '1', mlb: 'MLB-3892371',  titulo: 'iPhone 15 128GB Preto Lacrado',     marketplace: 'ML',     conta: 'Aguiar',  preco: 5499, visitas: 12830, vendas: 142, status: 'ativo',    reputacao: 'verde' },
  { id: '2', mlb: 'SH-2918',      titulo: 'iPhone 15 128GB',                    marketplace: 'Shopee', conta: 'Luno',    preco: 5390, visitas: 7420,  vendas: 89,  status: 'ativo' },
  { id: '3', mlb: 'AMZ-B0CXK',    titulo: 'Apple iPhone 15 128GB Black',        marketplace: 'Amazon', conta: 'Eron',    preco: 5599, visitas: 4310,  vendas: 31,  status: 'pausado' },
  { id: '4', mlb: 'MLB-2918374',  titulo: 'AirPods Pro 2 Original Apple',       marketplace: 'ML',     conta: 'Jlas',    preco: 1899, visitas: 8930,  vendas: 213, status: 'ativo',    reputacao: 'verde' },
  { id: '5', mlb: 'SH-8830',      titulo: 'AirPods Pro 2',                      marketplace: 'Shopee', conta: 'Atv',     preco: 1849, visitas: 5210,  vendas: 96,  status: 'ativo' },
  { id: '6', mlb: 'MLB-7621093',  titulo: 'Galaxy S24 Ultra 512GB',             marketplace: 'ML',     conta: 'Barbosa', preco: 8290, visitas: 3120,  vendas: 18,  status: 'ativo',    reputacao: 'amarela' },
  { id: '7', mlb: 'MGL-12388',    titulo: 'Mala ABS 28" Preta',                 marketplace: 'Magalu', conta: 'Kit 4',   preco: 489,  visitas: 1240,  vendas: 22,  status: 'ativo' },
  { id: '8', mlb: 'MLB-2103984',  titulo: 'Apple Watch Series 9 GPS 45mm',      marketplace: 'ML',     conta: 'Mini',    preco: 3990, visitas: 2890,  vendas: 41,  status: 'encerrado' },
])

const search = ref('')
const mk = ref('')

const filtered = computed(() =>
  anuncios.value.filter((a) => {
    if (search.value && !`${a.titulo} ${a.mlb}`.toLowerCase().includes(search.value.toLowerCase())) return false
    if (mk.value && a.marketplace !== mk.value) return false
    return true
  }),
)

const totalVendas = computed(() => anuncios.value.reduce((a, x) => a + x.vendas, 0))
const totalVisitas = computed(() => anuncios.value.reduce((a, x) => a + x.visitas, 0))
const ctr = computed(() => ((totalVendas.value / totalVisitas.value) * 100).toFixed(2))

function brl(v: number) {
  return v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
}
function statusPill(s: Anuncio['status']) {
  if (s === 'ativo') return 'pill-success'
  if (s === 'pausado') return 'pill-warning'
  return 'pill-muted'
}
function repColor(r?: Anuncio['reputacao']) {
  if (r === 'verde') return 'bg-emerald-500'
  if (r === 'amarela') return 'bg-yellow-400'
  if (r === 'laranja') return 'bg-orange-500'
  if (r === 'vermelha') return 'bg-red-500'
  return 'bg-muted'
}
</script>

<template>
  <div class="space-y-5">
    <PageHeader title="Anúncios" description="Listagens publicadas em cada marketplace.">
      <template #actions>
        <Button size="sm" variant="outline">
          <ExternalLink class="size-4 mr-1.5" /> abrir no Bling
        </Button>
        <Button size="sm">
          <Plus class="size-4 mr-1.5" /> publicar
        </Button>
      </template>
    </PageHeader>

    <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
      <StatCard label="Anúncios ativos" :value="anuncios.filter(a => a.status === 'ativo').length" :icon="Megaphone" />
      <StatCard label="Visitas (30d)" :value="totalVisitas.toLocaleString('pt-BR')" />
      <StatCard label="Vendas (30d)" :value="totalVendas" />
      <StatCard label="Conversão" :value="`${ctr}%`" hint="vendas / visitas" />
    </div>

    <div class="flex flex-wrap gap-2 items-center">
      <Input v-model="search" placeholder="buscar título ou ID…" class="w-72" />
      <select v-model="mk" class="h-9 rounded-md border bg-background px-2 text-sm">
        <option value="">todos marketplaces</option>
        <option value="ML">Mercado Livre</option>
        <option value="Shopee">Shopee</option>
        <option value="Amazon">Amazon</option>
        <option value="Magalu">Magalu</option>
      </select>
      <Button size="sm" variant="ghost"><Filter class="size-4 mr-1.5" /> filtros</Button>
      <span class="ml-auto text-xs text-muted-foreground">{{ filtered.length }} anúncios</span>
    </div>

    <div class="table-card">
      <table class="w-full">
        <thead>
          <tr>
            <th>ID</th>
            <th>Título</th>
            <th>Canal</th>
            <th>Conta</th>
            <th class="text-right">Preço</th>
            <th class="text-right">Visitas</th>
            <th class="text-right">Vendas</th>
            <th class="text-center">Reputação</th>
            <th>Status</th>
            <th class="text-right">Ações</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="a in filtered" :key="a.id">
            <td class="font-mono text-xs text-muted-foreground">{{ a.mlb }}</td>
            <td class="font-medium max-w-xs truncate">{{ a.titulo }}</td>
            <td><span class="pill pill-muted">{{ a.marketplace }}</span></td>
            <td class="text-muted-foreground">{{ a.conta }}</td>
            <td class="text-right tabular-nums">{{ brl(a.preco) }}</td>
            <td class="text-right tabular-nums text-muted-foreground">{{ a.visitas.toLocaleString('pt-BR') }}</td>
            <td class="text-right tabular-nums font-medium">{{ a.vendas }}</td>
            <td class="text-center">
              <span class="inline-block size-3 rounded-full" :class="repColor(a.reputacao)" :title="a.reputacao || 'n/d'" />
            </td>
            <td><span :class="statusPill(a.status)">{{ a.status }}</span></td>
            <td class="text-right">
              <Button size="sm" variant="ghost" class="h-7 w-7 p-0"><Eye class="size-3.5" /></Button>
              <Button v-if="a.status === 'ativo'" size="sm" variant="ghost" class="h-7 w-7 p-0">
                <Pause class="size-3.5" />
              </Button>
              <Button v-else size="sm" variant="ghost" class="h-7 w-7 p-0"><Play class="size-3.5" /></Button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
