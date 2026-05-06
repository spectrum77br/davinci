<script setup lang="ts">
import {
  Smartphone, Briefcase, Zap, BarChart3, Store, Plus,
  Send, RefreshCw, Download, Undo2, Redo2, DollarSign, Search,
  ListTree, Upload, Filter,
} from 'lucide-vue-next'

definePageMeta({ middleware: ['permission'], permission: { resource: 'tabela_precos', action: 'view' } })

type Categoria = { key: string; label: string; icon: any; contas: number }
type Tab = 'precos' | 'contas' | 'produtos'

const categorias: Categoria[] = [
  { key: 'celular',  label: 'Celular',     icon: Smartphone, contas: 52 },
  { key: 'mala',     label: 'Mala',         icon: Briefcase,  contas: 14 },
  { key: 'eletro',   label: 'Eletro',       icon: Zap,        contas: 1 },
  { key: 'catalogo', label: 'Catálogo ML',  icon: BarChart3,  contas: 0 },
  { key: 'loja',     label: 'Loja',         icon: Store,      contas: 73 },
]

const activeCat = ref<Categoria>(categorias[0])
const activeTab = ref<Tab>('precos')

type ContaCol = { id: string; label: string; conta: string; obs: string[]; canal: string; tone: string }

const contas: ContaCol[] = [
  { id: 'shopee-luno',   label: 'Shopee kit 1', conta: 'luno GO', obs: ['obs1', 'obs2', 'obs3'], canal: 'shopee', tone: 'bg-amber-50 text-amber-900 border-amber-200 dark:bg-amber-500/10 dark:text-amber-300 dark:border-amber-500/20' },
  { id: 'shopee-mini',   label: 'Shopee kit 3', conta: 'mini',     obs: ['obs1', 'obs2', 'obs3'], canal: 'shopee', tone: 'bg-amber-50 text-amber-900 border-amber-200 dark:bg-amber-500/10 dark:text-amber-300 dark:border-amber-500/20' },
  { id: 'shopee-jlas',   label: 'Shopee kit 4', conta: 'jlas',     obs: ['obs1', 'obs2', 'obs3'], canal: 'shopee', tone: 'bg-amber-50 text-amber-900 border-amber-200 dark:bg-amber-500/10 dark:text-amber-300 dark:border-amber-500/20' },
  { id: 'temu-atv',      label: 'Temu kit 1',   conta: 'atv',      obs: ['obs1', 'obs2', 'obs3'], canal: 'temu',   tone: 'bg-violet-50 text-violet-900 border-violet-200 dark:bg-violet-500/10 dark:text-violet-300 dark:border-violet-500/20' },
  { id: 'temu-jlas',     label: 'Temu kit 2',   conta: 'jlas',     obs: ['obs1', 'obs2', 'obs3'], canal: 'temu',   tone: 'bg-violet-50 text-violet-900 border-violet-200 dark:bg-violet-500/10 dark:text-violet-300 dark:border-violet-500/20' },
  { id: 'tt-eron',       label: 'TikTok kit 1', conta: 'eron',     obs: ['obs1', 'obs2', 'obs3'], canal: 'tiktok', tone: 'bg-pink-50 text-pink-900 border-pink-200 dark:bg-pink-500/10 dark:text-pink-300 dark:border-pink-500/20' },
  { id: 'tt-atv',        label: 'TikTok kit 1', conta: 'atv',      obs: ['obs1', 'obs2', 'obs3'], canal: 'tiktok', tone: 'bg-pink-50 text-pink-900 border-pink-200 dark:bg-pink-500/10 dark:text-pink-300 dark:border-pink-500/20' },
  { id: 'tt-barbosa',    label: 'TikTok kit 1', conta: 'barbosa',  obs: ['obs1', 'obs2', 'obs3'], canal: 'tiktok', tone: 'bg-pink-50 text-pink-900 border-pink-200 dark:bg-pink-500/10 dark:text-pink-300 dark:border-pink-500/20' },
]

type Linha = {
  sku: string
  nome: string
  bling: number | null
  kits: (number | null)[]
  precos: Record<string, { v: number | null; sv?: boolean }>
}

const linhas: Linha[] = [
  {
    sku: 'IPH11-A16',  nome: 'apple ipad 11 A16 128GB…',
    bling: null, kits: [2000, 2000, 2000, 2000],
    precos: {
      'shopee-luno': { v: null }, 'shopee-mini': { v: null }, 'shopee-jlas': { v: 2584, sv: true },
      'temu-atv': { v: 2635 }, 'temu-jlas': { v: 2612 },
      'tt-eron': { v: null }, 'tt-atv': { v: null }, 'tt-barbosa': { v: null },
    },
  },
  {
    sku: 'IPH15-128', nome: 'apple iphone 15 128 — …',
    bling: null, kits: [3450, 3450, 3450, 3450],
    precos: {
      'shopee-luno': { v: null }, 'shopee-mini': { v: null }, 'shopee-jlas': { v: 4435, sv: true },
      'temu-atv': { v: 4523 }, 'temu-jlas': { v: 4483 },
      'tt-eron': { v: null }, 'tt-atv': { v: null }, 'tt-barbosa': { v: null },
    },
  },
  {
    sku: 'IPH17-PRO-256', nome: 'apple iphone 17 pro 25…',
    bling: null, kits: [6500, 6500, 6500, 6500],
    precos: {
      'shopee-luno': { v: null }, 'shopee-mini': { v: null }, 'shopee-jlas': { v: 8329 },
      'temu-atv': { v: 8495 }, 'temu-jlas': { v: 8420 },
      'tt-eron': { v: null }, 'tt-atv': { v: null }, 'tt-barbosa': { v: null },
    },
  },
  {
    sku: 'AIRPODS-PRO2', nome: 'AirPods Pro 2ª Geração',
    bling: 1280, kits: [1849, 1849, 1899, 1899],
    precos: {
      'shopee-luno': { v: 1899 }, 'shopee-mini': { v: 1879 }, 'shopee-jlas': { v: 1849, sv: true },
      'temu-atv': { v: 1945 }, 'temu-jlas': { v: 1915 },
      'tt-eron': { v: 1880 }, 'tt-atv': { v: 1875 }, 'tt-barbosa': { v: 1890 },
    },
  },
  {
    sku: 'GAL-S24-512', nome: 'Galaxy S24 Ultra 512GB',
    bling: 4900, kits: [7990, 7990, 8090, 8090],
    precos: {
      'shopee-luno': { v: 8290 }, 'shopee-mini': { v: 8190 }, 'shopee-jlas': { v: null },
      'temu-atv': { v: 8400 }, 'temu-jlas': { v: 8370 },
      'tt-eron': { v: null }, 'tt-atv': { v: null }, 'tt-barbosa': { v: 8210 },
    },
  },
]

const search = ref('')

const linhasFiltered = computed(() =>
  linhas.filter((l) => !search.value || `${l.sku} ${l.nome}`.toLowerCase().includes(search.value.toLowerCase())),
)

function fmt(v: number | null) {
  if (v == null) return '—'
  return v.toLocaleString('pt-BR')
}

const groupedHeaders = computed(() => {
  const groups: { canal: string; cols: ContaCol[]; tone: string }[] = []
  let cur: { canal: string; cols: ContaCol[]; tone: string } | null = null
  for (const c of contas) {
    if (!cur || cur.canal !== c.canal) {
      cur = { canal: c.canal, cols: [c], tone: c.tone }
      groups.push(cur)
    } else {
      cur.cols.push(c)
    }
  }
  return groups
})
</script>

<template>
  <div class="space-y-5">
    <PageHeader title="Tabela de Preços" description="Gerencie custos, margens e preços de venda por conta." />

    <!-- Pílulas de categoria -->
    <div class="flex flex-wrap gap-2">
      <button
        v-for="c in categorias"
        :key="c.key"
        class="inline-flex items-center gap-2 h-10 px-4 rounded-lg border text-sm font-medium transition-colors"
        :class="activeCat.key === c.key
          ? 'bg-primary text-primary-foreground border-primary'
          : 'bg-card hover:bg-muted'"
        @click="activeCat = c"
      >
        <component :is="c.icon" class="size-4" />
        {{ c.label }} <span class="opacity-70">({{ c.contas }} contas)</span>
      </button>
    </div>

    <!-- Sub-tabs -->
    <div class="flex items-center gap-1 border-b">
      <button
        class="inline-flex items-center gap-2 h-10 px-4 -mb-px text-sm font-medium border-b-2 transition-colors"
        :class="activeTab === 'precos' ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'"
        @click="activeTab = 'precos'"
      >
        <DollarSign class="size-4" /> Tabela de Preços
      </button>
      <button
        class="inline-flex items-center gap-2 h-10 px-4 -mb-px text-sm font-medium border-b-2 transition-colors"
        :class="activeTab === 'contas' ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'"
        @click="activeTab = 'contas'"
      >
        <ListTree class="size-4" /> Contas ({{ contas.length }})
      </button>
      <button
        class="inline-flex items-center gap-2 h-10 px-4 -mb-px text-sm font-medium border-b-2 transition-colors"
        :class="activeTab === 'produtos' ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'"
        @click="activeTab = 'produtos'"
      >
        <Upload class="size-4" /> Produtos ({{ linhas.length }})
      </button>
    </div>

    <!-- TAB: PREÇOS -->
    <div v-if="activeTab === 'precos'" class="space-y-3">
      <div class="flex flex-wrap items-center gap-2">
        <div class="relative flex-1 max-w-md">
          <Search class="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
          <input
            v-model="search"
            type="search"
            placeholder="Buscar por SKU ou nome…"
            class="w-full h-10 rounded-lg border bg-card pl-9 pr-3 text-sm"
          />
        </div>
        <Button size="sm" variant="outline" class="h-10 w-10 p-0"><Undo2 class="size-4" /></Button>
        <Button size="sm" variant="outline" class="h-10 w-10 p-0"><Redo2 class="size-4" /></Button>
        <Button size="sm" variant="outline" class="h-10">
          <Download class="size-4 mr-1.5" /> Excel
        </Button>
        <Button size="sm" variant="outline" class="h-10 border-emerald-300 text-emerald-700 hover:bg-emerald-50 dark:border-emerald-500/40 dark:text-emerald-400">
          <RefreshCw class="size-4 mr-1.5" /> Custo Bling
        </Button>
        <Button size="sm" class="h-10">
          <Send class="size-4 mr-1.5" /> Enviar
        </Button>
      </div>

      <div class="text-xs text-muted-foreground">
        70 produto(s) · 52 conta(s) · Fórmula: ((Custo × Margem) + Frete + Custo) / (1 - Comissão)
      </div>

      <div class="rounded-xl border bg-card overflow-x-auto">
        <table class="w-full text-sm border-collapse">
          <thead>
            <!-- Linha 1: Canais de marketplace -->
            <tr>
              <th class="bg-muted/50 sticky left-0 z-20" rowspan="4" />
              <th class="bg-muted/40 border-b px-3 py-1.5 text-center text-xs text-muted-foreground" colspan="4">
                custos
              </th>
              <th
                v-for="g in groupedHeaders"
                :key="g.canal"
                class="border-b border-l text-center text-xs font-medium px-3 py-1.5 capitalize"
                :class="g.tone"
                :colspan="g.cols.length"
              >
                <div class="flex items-center justify-center gap-2">
                  <span>{{ g.cols[0].label }}</span>
                </div>
              </th>
            </tr>
            <!-- Linha 2: nome conta -->
            <tr>
              <th class="bg-card border-b px-3 py-1 text-xs font-medium text-emerald-700" :colspan="4">{{ activeCat.label }}<br><span class="text-muted-foreground font-normal">produtos</span></th>
              <th
                v-for="c in contas" :key="c.id + '-name'"
                class="border-b border-l px-3 py-1 text-center text-xs font-semibold"
                :class="c.tone"
              >
                {{ c.conta }}
              </th>
            </tr>
            <!-- Linhas obs -->
            <tr v-for="i in [0, 1, 2]" :key="'obs' + i">
              <th
                v-for="c in contas" :key="c.id + 'obs' + i"
                class="border-b border-l px-3 py-0.5 text-center text-[10px] italic font-normal"
                :class="c.tone"
                :style="i === 0 && c === contas[0] ? '' : ''"
              >
                {{ c.obs[i] }}
              </th>
            </tr>
            <!-- Linha de cabeçalhos custo -->
            <tr>
              <th class="border-b px-2 py-1.5 text-center text-xs font-medium bg-emerald-50/60 text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-400">bling</th>
              <th class="border-b px-2 py-1.5 text-center text-xs font-medium bg-emerald-50/40 text-emerald-800 dark:bg-emerald-500/5 dark:text-emerald-400">kit 1</th>
              <th class="border-b px-2 py-1.5 text-center text-xs font-medium bg-emerald-50/40 text-emerald-800 dark:bg-emerald-500/5 dark:text-emerald-400">kit 2</th>
              <th class="border-b px-2 py-1.5 text-center text-xs font-medium bg-emerald-50/40 text-emerald-800 dark:bg-emerald-500/5 dark:text-emerald-400">kit 3</th>
              <th class="border-b px-2 py-1.5 text-center text-xs font-medium bg-emerald-50/40 text-emerald-800 dark:bg-emerald-500/5 dark:text-emerald-400">kit 4</th>
              <th
                v-for="c in contas" :key="c.id + '-canal'"
                class="border-b border-l px-2 py-1.5 text-center text-xs font-medium"
                :class="c.tone"
              >
                {{ c.canal }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="l in linhasFiltered" :key="l.sku" class="hover:bg-muted/20">
              <td class="sticky left-0 bg-card border-b border-r px-3 py-2 font-mono text-xs whitespace-nowrap">
                {{ l.nome }}
              </td>
              <td class="border-b text-center text-muted-foreground">—</td>
              <td v-for="(k, i) in l.kits" :key="i" class="border-b text-center tabular-nums text-primary font-medium">
                {{ fmt(k) }}
              </td>
              <td
                v-for="c in contas" :key="c.id + l.sku"
                class="border-b border-l text-center tabular-nums"
              >
                <template v-if="l.precos[c.id]?.v != null">
                  <span class="tabular-nums">{{ fmt(l.precos[c.id].v) }}</span>
                  <span v-if="l.precos[c.id].sv" class="ml-1 inline-block text-[9px] uppercase font-bold px-1 rounded bg-amber-200 text-amber-900 dark:bg-amber-500/30 dark:text-amber-300">
                    sv
                  </span>
                </template>
                <span v-else class="text-muted-foreground">—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- TAB: CONTAS -->
    <div v-if="activeTab === 'contas'" class="space-y-3">
      <div class="flex items-center gap-2">
        <Input placeholder="buscar conta…" class="w-72" />
        <Button size="sm" variant="ghost"><Filter class="size-4 mr-1.5" /> filtros</Button>
        <Button size="sm" class="ml-auto"><Plus class="size-4 mr-1.5" /> nova conta</Button>
      </div>
      <div class="table-card">
        <table class="w-full">
          <thead>
            <tr><th>Conta</th><th>Canal</th><th>Apelido</th><th class="text-right">Margem alvo</th><th class="text-right">Frete</th><th class="text-right">Comissão</th><th>Status</th></tr>
          </thead>
          <tbody>
            <tr v-for="c in contas" :key="c.id">
              <td class="font-medium">{{ c.conta }}</td>
              <td><span class="pill pill-muted capitalize">{{ c.canal }}</span></td>
              <td class="text-muted-foreground">{{ c.label }}</td>
              <td class="text-right tabular-nums">22,0%</td>
              <td class="text-right tabular-nums">R$ 28,00</td>
              <td class="text-right tabular-nums">14,0%</td>
              <td><span class="pill-success">ativa</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- TAB: PRODUTOS -->
    <div v-if="activeTab === 'produtos'" class="space-y-3">
      <div class="flex items-center gap-2">
        <Input placeholder="buscar SKU…" class="w-72" />
        <Button size="sm" variant="outline"><Upload class="size-4 mr-1.5" /> importar</Button>
        <Button size="sm" class="ml-auto"><Plus class="size-4 mr-1.5" /> adicionar SKU</Button>
      </div>
      <div class="table-card">
        <table class="w-full">
          <thead>
            <tr><th>SKU</th><th>Nome</th><th class="text-right">Custo Bling</th><th class="text-right">Kit 1</th><th class="text-right">Kit 2</th><th class="text-right">Kit 3</th><th class="text-right">Kit 4</th></tr>
          </thead>
          <tbody>
            <tr v-for="l in linhas" :key="l.sku">
              <td class="font-mono text-xs">{{ l.sku }}</td>
              <td>{{ l.nome }}</td>
              <td class="text-right tabular-nums">{{ fmt(l.bling) }}</td>
              <td v-for="(k, i) in l.kits" :key="i" class="text-right tabular-nums">{{ fmt(k) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
